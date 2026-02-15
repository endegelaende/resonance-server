"""
Plugin Manager for Resonance.

Discovers, loads, starts, and stops plugins following a well-defined lifecycle:

1. **Discover** — Scan the ``plugins/`` directory for subdirectories containing
   a ``plugin.toml`` manifest.
2. **Load** — Parse each manifest into a :class:`PluginManifest`, import the
   plugin's ``__init__`` module, and verify it exposes ``setup()`` /
   ``teardown()`` callables.
3. **Start** — For each loaded plugin, create a :class:`PluginContext` and call
   ``await plugin.setup(ctx)``.  Plugins can register commands, menu entries,
   routes, and event subscriptions during setup.
4. **Stop** — Call ``await plugin.teardown(ctx)`` for each started plugin (in
   reverse order), then run ``ctx._cleanup()`` to undo all registrations.

Usage (inside ``server.py``)::

    from resonance.plugin_manager import PluginManager

    pm = PluginManager(plugins_dir=Path("plugins"))
    await pm.discover()
    await pm.load_all()
    await pm.start_all(
        event_bus=event_bus,
        music_library=self.music_library,
        player_registry=self.player_registry,
        playlist_manager=self.playlist_manager,
        command_register=register_command,
        command_unregister=unregister_command,
        route_register=lambda r: self.web_server.app.include_router(r),
    )
    # ... server runs ...
    await pm.stop_all()
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

from resonance.plugin import PluginContext, PluginManifest, _clear_plugin_menus

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


# ---------------------------------------------------------------------------
# Internal bookkeeping
# ---------------------------------------------------------------------------


@dataclass
class _LoadedPlugin:
    """Internal record for a successfully loaded (but not yet started) plugin."""

    manifest: PluginManifest
    module: ModuleType
    context: PluginContext | None = None
    started: bool = False


# ---------------------------------------------------------------------------
# PluginManager
# ---------------------------------------------------------------------------


class PluginManager:
    """Manages the full plugin lifecycle: discover → load → start → stop.

    Attributes:
        plugins_dir: Root directory containing plugin subdirectories.
        manifests: Discovered manifests (populated after :meth:`discover`).
        plugins: Loaded plugins keyed by plugin name.
    """

    def __init__(self, plugins_dir: Path | None = None) -> None:
        self.plugins_dir: Path = plugins_dir or Path("plugins")
        self.manifests: list[PluginManifest] = []
        self.plugins: dict[str, _LoadedPlugin] = {}

    # -- Phase 1: Discover ---------------------------------------------------

    async def discover(self) -> list[PluginManifest]:
        """Scan *plugins_dir* for valid plugin manifests.

        Each subdirectory must contain a ``plugin.toml`` with at least::

            [plugin]
            name = "..."
            version = "..."

        Returns:
            List of discovered :class:`PluginManifest` instances.

        Directories without a ``plugin.toml`` or with invalid manifests are
        skipped with a warning (they do not prevent other plugins from loading).
        """
        self.manifests.clear()

        if not self.plugins_dir.is_dir():
            logger.info("Plugins directory does not exist: %s — no plugins to load", self.plugins_dir)
            return self.manifests

        for child in sorted(self.plugins_dir.iterdir()):
            if not child.is_dir():
                continue

            toml_path = child / "plugin.toml"
            if not toml_path.is_file():
                logger.debug("Skipping %s — no plugin.toml", child.name)
                continue

            try:
                manifest = self._parse_manifest(toml_path, child)
                self.manifests.append(manifest)
                logger.info("Discovered plugin: %s v%s (%s)", manifest.name, manifest.version, child)
            except Exception as exc:
                logger.warning("Failed to parse %s: %s", toml_path, exc)

        logger.info("Discovered %d plugin(s)", len(self.manifests))
        return self.manifests

    # -- Phase 2: Load ------------------------------------------------------

    async def load_all(self) -> int:
        """Import every discovered plugin module.

        A plugin module is the ``__init__.py`` inside its directory.  It must
        expose at least an ``async def setup(ctx: PluginContext) -> None``.
        ``teardown`` is optional.

        Returns:
            Number of successfully loaded plugins.
        """
        loaded = 0

        for manifest in self.manifests:
            if manifest.name in self.plugins:
                logger.warning("Plugin '%s' already loaded — skipping duplicate", manifest.name)
                continue

            try:
                module = self._import_plugin(manifest)
                self._validate_module(module, manifest)
                self.plugins[manifest.name] = _LoadedPlugin(manifest=manifest, module=module)
                loaded += 1
                logger.info("Loaded plugin: %s v%s", manifest.name, manifest.version)
            except Exception as exc:
                logger.error("Failed to load plugin '%s': %s", manifest.name, exc)

        logger.info("Loaded %d / %d plugin(s)", loaded, len(self.manifests))
        return loaded

    # -- Phase 3: Start ------------------------------------------------------

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
    ) -> int:
        """Call ``setup()`` on every loaded plugin.

        A :class:`PluginContext` is created per plugin with the provided
        server components wired in.

        Args:
            event_bus: The global event bus.
            music_library: The music library instance.
            player_registry: The player registry instance.
            playlist_manager: Optional playlist manager.
            command_register: Callback to register a JSON-RPC command.
            command_unregister: Callback to unregister a JSON-RPC command.
            route_register: Callback to mount a FastAPI router.
            content_registry: Optional content-provider registry for plugins
                that supply external audio sources (Radio, Podcasts, etc.).

        Returns:
            Number of successfully started plugins.
        """
        started = 0

        for name, loaded in self.plugins.items():
            if loaded.started:
                logger.debug("Plugin '%s' already started — skipping", name)
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
                data_dir=Path(f"data/plugins/{name}"),
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
                # Clean up any partial registrations
                try:
                    await ctx._cleanup()
                except Exception as cleanup_exc:
                    logger.warning("Cleanup after failed start of '%s': %s", name, cleanup_exc)
                loaded.context = None

        logger.info("Started %d / %d plugin(s)", started, len(self.plugins))
        return started

    # -- Phase 4: Stop -------------------------------------------------------

    async def stop_all(self) -> None:
        """Call ``teardown()`` on every started plugin (reverse order) and clean up.

        After teardown, all commands, menu entries, event subscriptions, and
        routes registered by the plugin are automatically removed.
        """
        # Reverse order: last started = first stopped
        started_names = [
            name for name, lp in self.plugins.items() if lp.started
        ]

        for name in reversed(started_names):
            loaded = self.plugins[name]
            ctx = loaded.context

            # Call teardown if it exists
            teardown_fn = getattr(loaded.module, "teardown", None)
            if teardown_fn is not None and ctx is not None:
                try:
                    await teardown_fn(ctx)
                except Exception as exc:
                    logger.error("Error in teardown of plugin '%s': %s", name, exc)

            # Automatic cleanup of all registrations
            if ctx is not None:
                try:
                    await ctx._cleanup()
                except Exception as exc:
                    logger.warning("Cleanup error for plugin '%s': %s", name, exc)

            loaded.started = False
            loaded.context = None
            logger.info("Stopped plugin: %s", name)

        logger.info("All plugins stopped")

    # -- Convenience ---------------------------------------------------------

    @property
    def started_plugins(self) -> list[str]:
        """Return names of all currently started plugins."""
        return [name for name, lp in self.plugins.items() if lp.started]

    @property
    def loaded_plugin_count(self) -> int:
        """Return the number of loaded plugins."""
        return len(self.plugins)

    def get_manifest(self, name: str) -> PluginManifest | None:
        """Return the manifest for a loaded plugin, or ``None``."""
        loaded = self.plugins.get(name)
        return loaded.manifest if loaded else None

    def get_context(self, name: str) -> PluginContext | None:
        """Return the context for a started plugin, or ``None``."""
        loaded = self.plugins.get(name)
        return loaded.context if loaded else None

    # -- Internal helpers ----------------------------------------------------

    @staticmethod
    def _parse_manifest(toml_path: Path, plugin_dir: Path) -> PluginManifest:
        """Read and parse a ``plugin.toml`` file.

        Raises:
            ValueError: If required fields are missing.
            tomllib.TOMLDecodeError: If the TOML is malformed.
        """
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)

        plugin_table = data.get("plugin")
        if not isinstance(plugin_table, dict):
            raise ValueError(f"{toml_path} is missing the [plugin] table")

        return PluginManifest.from_toml(plugin_table, plugin_dir)

    @staticmethod
    def _import_plugin(manifest: PluginManifest) -> ModuleType:
        """Import a plugin's ``__init__.py`` as a Python module.

        The module is registered under ``resonance_plugins.<name>`` in
        ``sys.modules`` to avoid collisions with other packages.
        """
        init_path = manifest.plugin_dir / "__init__.py"
        if not init_path.is_file():
            raise FileNotFoundError(
                f"Plugin '{manifest.name}' is missing __init__.py in {manifest.plugin_dir}"
            )

        module_name = f"resonance_plugins.{manifest.name}"

        # Remove stale module entry if reloading
        if module_name in sys.modules:
            del sys.modules[module_name]

        spec = importlib.util.spec_from_file_location(module_name, init_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot create module spec for {init_path}")

        module = importlib.util.module_from_spec(spec)

        # Ensure the parent package exists in sys.modules so relative-ish
        # lookups don't break.
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
        """Ensure the plugin module has the required ``setup`` function.

        Raises:
            TypeError: If ``setup`` is missing or not callable.
        """
        setup_fn = getattr(module, "setup", None)
        if setup_fn is None:
            raise TypeError(
                f"Plugin '{manifest.name}' module is missing a 'setup' function"
            )
        if not callable(setup_fn):
            raise TypeError(
                f"Plugin '{manifest.name}' 'setup' attribute is not callable"
            )

        # teardown is optional but must be callable if present
        teardown_fn = getattr(module, "teardown", None)
        if teardown_fn is not None and not callable(teardown_fn):
            raise TypeError(
                f"Plugin '{manifest.name}' 'teardown' attribute is not callable"
            )
