"""
Plugin manager lifecycle and state handling.

This module discovers plugins, imports them, starts/stops them, and persists
enable/disable state across restarts.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

from resonance.plugin import PluginContext, PluginManifest, SettingDefinition

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi import APIRouter

    from resonance.content_provider import ContentProviderRegistry
    from resonance.core.events import EventBus
    from resonance.core.library import MusicLibrary
    from resonance.core.playlist import PlaylistManager
    from resonance.player.registry import PlayerRegistry
    from resonance.web.jsonrpc import CommandHandler

logger = logging.getLogger(__name__)


@dataclass
class _LoadedPlugin:
    """Internal record for a successfully loaded plugin module."""

    manifest: PluginManifest
    module: ModuleType
    context: PluginContext | None = None
    started: bool = False


class PluginStateManager:
    """Persists and queries plugin enable/disable states."""

    def __init__(self, state_file: Path | None = None) -> None:
        self._state_file = state_file or Path("data/plugin_states.json")
        self._states: dict[str, str] = {}
        self._load()

    def is_enabled(self, plugin_name: str) -> bool:
        """Unknown plugins default to enabled for backwards compatibility."""
        return self._states.get(plugin_name, "enabled") == "enabled"

    def set_enabled(self, plugin_name: str, enabled: bool) -> None:
        self._states[plugin_name] = "enabled" if enabled else "disabled"
        self._save()

    def get_state(self, plugin_name: str) -> str:
        return self._states.get(plugin_name, "enabled")

    def get_all_states(self) -> dict[str, str]:
        return dict(self._states)

    def _load(self) -> None:
        if not self._state_file.is_file():
            return

        try:
            with open(self._state_file, "r", encoding="utf-8") as f:
                payload = json.load(f)
            states = payload.get("states", {}) if isinstance(payload, dict) else {}
            normalized: dict[str, str] = {}
            if isinstance(states, dict):
                for key, value in states.items():
                    key_str = str(key)
                    value_str = str(value).lower()
                    normalized[key_str] = "disabled" if value_str == "disabled" else "enabled"
            self._states = normalized
        except Exception as exc:
            logger.warning("Failed to load plugin states: %s", exc)
            self._states = {}

    def _save(self) -> None:
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {"_version": 1, "states": self._states}
        tmp = self._state_file.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            tmp.replace(self._state_file)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


class PluginManager:
    """Manages plugin discovery, loading, startup, and shutdown."""

    def __init__(
        self,
        plugins_dir: Path | None = None,
        core_plugins_dir: Path | None = None,
        community_plugins_dir: Path | None = None,
        state_file: Path | None = None,
    ) -> None:
        # Backward compatibility with older constructor signature.
        if plugins_dir is not None and core_plugins_dir is None:
            core_plugins_dir = plugins_dir
            # Legacy mode historically scanned one directory only.
            if community_plugins_dir is None:
                community_plugins_dir = Path("data/_disabled_community_plugins")

        self.core_plugins_dir: Path = core_plugins_dir or Path("plugins")
        self.community_plugins_dir: Path = (
            community_plugins_dir or Path("data/installed_plugins")
        )
        self.plugins_dir: Path = self.core_plugins_dir

        self.manifests: list[PluginManifest] = []
        self.plugins: dict[str, _LoadedPlugin] = {}
        self.state_manager = PluginStateManager(state_file=state_file)

        self._restart_required = False

    @property
    def restart_required(self) -> bool:
        return self._restart_required

    def mark_restart_required(self) -> None:
        self._restart_required = True

    def clear_restart_required(self) -> None:
        self._restart_required = False

    async def discover(self) -> list[PluginManifest]:
        """Scan both core and community plugin directories."""
        self.manifests.clear()

        await self._scan_directory(self.core_plugins_dir, plugin_type="core")
        await self._scan_directory(self.community_plugins_dir, plugin_type="community")

        core_count = sum(1 for m in self.manifests if m.plugin_type == "core")
        community_count = sum(1 for m in self.manifests if m.plugin_type == "community")
        logger.info(
            "Discovered %d plugin(s) (%d core, %d community)",
            len(self.manifests),
            core_count,
            community_count,
        )
        return self.manifests

    async def _scan_directory(self, directory: Path, plugin_type: str) -> None:
        if not directory.is_dir():
            return

        for child in sorted(directory.iterdir()):
            if not child.is_dir():
                continue

            toml_path = child / "plugin.toml"
            if not toml_path.is_file():
                continue

            try:
                manifest = self._parse_manifest(toml_path, child, plugin_type=plugin_type)
                existing = next((m for m in self.manifests if m.name == manifest.name), None)
                if existing is not None:
                    logger.warning(
                        "Plugin '%s' exists in both '%s' and '%s' — using first (%s wins)",
                        manifest.name,
                        existing.plugin_dir,
                        child,
                        existing.plugin_type,
                    )
                    continue
                self.manifests.append(manifest)
                logger.info(
                    "Discovered plugin: %s v%s (%s, %s)",
                    manifest.name,
                    manifest.version,
                    manifest.plugin_dir,
                    manifest.plugin_type,
                )
            except Exception as exc:
                logger.warning("Failed to parse %s: %s", toml_path, exc)

    async def load_all(self) -> int:
        """Import every discovered plugin module."""
        loaded_count = 0

        for manifest in self.manifests:
            if manifest.name in self.plugins:
                logger.warning("Plugin '%s' already loaded — skipping duplicate", manifest.name)
                continue

            try:
                module = self._import_plugin(manifest)
                self._validate_module(module, manifest)
                self.plugins[manifest.name] = _LoadedPlugin(
                    manifest=manifest,
                    module=module,
                )
                loaded_count += 1
                logger.info("Loaded plugin: %s v%s", manifest.name, manifest.version)
            except Exception as exc:
                logger.error("Failed to load plugin '%s': %s", manifest.name, exc)

        logger.info("Loaded %d / %d plugin(s)", loaded_count, len(self.manifests))
        return loaded_count

    async def start_all(
        self,
        event_bus: EventBus,
        music_library: MusicLibrary,
        player_registry: PlayerRegistry,
        playlist_manager: PlaylistManager | None = None,
        *,
        command_register: Callable[[str, CommandHandler], None] | None = None,
        command_unregister: Callable[[str], None] | None = None,
        route_register: Callable[[APIRouter], None] | None = None,
        content_registry: ContentProviderRegistry | None = None,
        server_info: dict[str, Any] | None = None,
    ) -> int:
        """Call setup() on every loaded and enabled plugin."""
        started = 0

        for name, loaded in self.plugins.items():
            if loaded.started:
                logger.debug("Plugin '%s' already started — skipping", name)
                continue
            if not self.state_manager.is_enabled(name):
                logger.info("Plugin '%s' is disabled — skipping start", name)
                continue

            ctx = PluginContext(
                plugin_id=name,
                event_bus=event_bus,
                music_library=music_library,
                player_registry=player_registry,
                playlist_manager=playlist_manager,
                _command_register=command_register,
                _command_unregister=command_unregister,
                _route_register=route_register,
                _content_registry=content_registry,
                settings_defs=loaded.manifest.settings_defs,
                plugin_version=loaded.manifest.version,
                data_dir=Path(f"data/plugins/{name}"),
                server_info=server_info,
            )
            loaded.context = ctx

            try:
                setup_fn = getattr(loaded.module, "setup")
                await setup_fn(ctx)
                loaded.started = True
                started += 1
                logger.info("Started plugin: %s v%s — %s", name, loaded.manifest.version, ctx)
            except Exception as exc:
                logger.error("Failed to start plugin '%s': %s", name, exc)
                try:
                    await ctx._cleanup()
                except Exception as cleanup_exc:
                    logger.warning("Cleanup after failed start of '%s': %s", name, cleanup_exc)
                loaded.context = None

        logger.info("Started %d / %d plugin(s)", started, len(self.plugins))
        return started

    async def stop_all(self) -> None:
        """Call teardown() on started plugins in reverse order and clean up."""
        started_names = [name for name, loaded in self.plugins.items() if loaded.started]

        for name in reversed(started_names):
            loaded = self.plugins[name]
            ctx = loaded.context

            teardown_fn = getattr(loaded.module, "teardown", None)
            if teardown_fn is not None and ctx is not None:
                try:
                    await teardown_fn(ctx)
                except Exception as exc:
                    logger.error("Error in teardown of plugin '%s': %s", name, exc)

            if ctx is not None:
                try:
                    await ctx._cleanup()
                except Exception as exc:
                    logger.warning("Cleanup error for plugin '%s': %s", name, exc)

            loaded.started = False
            loaded.context = None
            logger.info("Stopped plugin: %s", name)

        logger.info("All plugins stopped")

    @property
    def started_plugins(self) -> list[str]:
        return [name for name, loaded in self.plugins.items() if loaded.started]

    @property
    def loaded_plugin_count(self) -> int:
        return len(self.plugins)

    def get_manifest(self, name: str) -> PluginManifest | None:
        loaded = self.plugins.get(name)
        if loaded is not None:
            return loaded.manifest
        return next((manifest for manifest in self.manifests if manifest.name == name), None)

    def get_context(self, name: str) -> PluginContext | None:
        loaded = self.plugins.get(name)
        return loaded.context if loaded is not None else None

    def is_core_plugin(self, name: str) -> bool:
        manifest = self.get_manifest(name)
        return bool(manifest and manifest.plugin_type == "core")

    def set_plugin_enabled(self, plugin_name: str, enabled: bool) -> None:
        current = self.state_manager.is_enabled(plugin_name)
        self.state_manager.set_enabled(plugin_name, enabled)
        if current != enabled:
            self._restart_required = True

    def get_plugin_state(self, plugin_name: str) -> str:
        return self.state_manager.get_state(plugin_name)

    def get_all_plugin_states(self) -> dict[str, str]:
        return self.state_manager.get_all_states()

    def list_plugin_info(self) -> list[dict[str, Any]]:
        """Return manifest + runtime info for API/JSON-RPC responses."""
        result: list[dict[str, Any]] = []
        manifests_by_name = {manifest.name: manifest for manifest in self.manifests}

        for name in sorted(manifests_by_name):
            manifest = manifests_by_name[name]
            loaded = self.plugins.get(name)
            started = bool(loaded and loaded.started)
            result.append(
                {
                    "name": manifest.name,
                    "version": manifest.version,
                    "description": manifest.description,
                    "author": manifest.author,
                    "category": manifest.category,
                    "icon": manifest.icon,
                    "state": self.state_manager.get_state(manifest.name),
                    "started": started,
                    "type": manifest.plugin_type,
                    "has_settings": bool(manifest.settings_defs),
                    "can_uninstall": manifest.plugin_type == "community",
                }
            )

        return result

    @staticmethod
    def _parse_manifest(
        toml_path: Path,
        plugin_dir: Path,
        *,
        plugin_type: str = "core",
    ) -> PluginManifest:
        """Read and parse a plugin.toml including [settings.*] definitions."""
        with open(toml_path, "rb") as f:
            full_data = tomllib.load(f)

        plugin_table = full_data.get("plugin")
        if not isinstance(plugin_table, dict):
            raise ValueError(f"{toml_path} is missing the [plugin] table")

        settings_defs: list[SettingDefinition] = []
        settings_table = full_data.get("settings", {})
        if isinstance(settings_table, dict):
            for key, value in settings_table.items():
                if not isinstance(value, dict):
                    continue
                try:
                    settings_defs.append(SettingDefinition.from_toml(str(key), value))
                except Exception as exc:
                    logger.warning("Invalid setting '%s' in %s: %s", key, toml_path, exc)
        settings_defs.sort(key=lambda definition: (definition.order, definition.key))

        return PluginManifest.from_toml(
            plugin_table,
            plugin_dir,
            settings_defs=tuple(settings_defs),
            plugin_type=plugin_type,
        )

    @staticmethod
    def _import_plugin(manifest: PluginManifest) -> ModuleType:
        """Import a plugin's __init__.py as a Python module."""
        init_path = manifest.plugin_dir / "__init__.py"
        if not init_path.is_file():
            raise FileNotFoundError(
                f"Plugin '{manifest.name}' is missing __init__.py in {manifest.plugin_dir}"
            )

        module_name = f"resonance_plugins.{manifest.name}"

        if module_name in sys.modules:
            del sys.modules[module_name]

        spec = importlib.util.spec_from_file_location(module_name, init_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot create module spec for {init_path}")

        module = importlib.util.module_from_spec(spec)

        parent_name = "resonance_plugins"
        if parent_name not in sys.modules:
            import types

            parent_module = types.ModuleType(parent_name)
            parent_module.__path__ = []  # type: ignore[attr-defined]
            sys.modules[parent_name] = parent_module

        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        return module

    @staticmethod
    def _validate_module(module: ModuleType, manifest: PluginManifest) -> None:
        """Ensure setup() exists and teardown() is callable when present."""
        setup_fn = getattr(module, "setup", None)
        if setup_fn is None:
            raise TypeError(f"Plugin '{manifest.name}' module is missing a 'setup' function")
        if not callable(setup_fn):
            raise TypeError(f"Plugin '{manifest.name}' 'setup' attribute is not callable")

        teardown_fn = getattr(module, "teardown", None)
        if teardown_fn is not None and not callable(teardown_fn):
            raise TypeError(f"Plugin '{manifest.name}' 'teardown' attribute is not callable")
