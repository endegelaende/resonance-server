"""Plugin management JSON-RPC handlers."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx

from resonance.plugin import PluginManifest, SettingDefinition
from resonance.web.handlers import CommandContext

logger = logging.getLogger(__name__)


def _mask_secret(value: Any) -> Any:
    if not isinstance(value, str):
        return "****" if value is not None else value
    if not value:
        return value
    if len(value) <= 4:
        return "*" * len(value)
    return "*" * max(4, len(value) - 4) + value[-4:]


def _settings_path_for(plugin_name: str) -> Path:
    return Path("data/plugins") / plugin_name / "settings.json"


def _load_settings_from_disk(manifest: PluginManifest) -> dict[str, Any]:
    values = {definition.key: definition.default for definition in manifest.settings_defs}
    path = _settings_path_for(manifest.name)
    if not path.is_file():
        return values

    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        for definition in manifest.settings_defs:
            if definition.key not in payload:
                continue
            value = payload[definition.key]
            ok, _ = definition.validate(value)
            if ok:
                values[definition.key] = value
    except Exception as exc:
        logger.warning("Failed to load plugin settings for %s: %s", manifest.name, exc)
    return values


def _save_settings_to_disk(
    manifest: PluginManifest,
    values: dict[str, Any],
) -> None:
    path = _settings_path_for(manifest.name)
    path.parent.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {"_version": 1, "_plugin_version": manifest.version}
    for definition in manifest.settings_defs:
        payload[definition.key] = values.get(definition.key, definition.default)

    tmp = path.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        tmp.replace(path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _mask_settings(definitions: tuple[SettingDefinition, ...], values: dict[str, Any]) -> dict[str, Any]:
    result = dict(values)
    for definition in definitions:
        if definition.secret:
            result[definition.key] = _mask_secret(result.get(definition.key))
    return result


def _parse_typed_value(definition: SettingDefinition, raw: Any) -> Any:
    if definition.type == "int":
        if isinstance(raw, int) and not isinstance(raw, bool):
            return raw
        return int(str(raw))
    if definition.type == "float":
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            return float(raw)
        return float(str(raw))
    if definition.type == "bool":
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"Invalid boolean value for '{definition.key}': {raw}")
    if definition.type in {"string", "select"}:
        return str(raw)
    return raw


def _require_plugin_manager(ctx: CommandContext):
    manager = ctx.plugin_manager
    if manager is None:
        raise RuntimeError("Plugin manager not available")
    return manager


async def cmd_pluginsettings(ctx: CommandContext, command: list[Any]) -> dict[str, Any]:
    """Handle pluginsettings get/set/getdef commands."""
    try:
        manager = _require_plugin_manager(ctx)
    except Exception as exc:
        return {"error": str(exc)}

    if len(command) < 3:
        return {"error": "Usage: pluginsettings <get|set|getdef> <plugin_name> [...]"}

    action = str(command[1]).lower()
    plugin_name = str(command[2])
    manifest = manager.get_manifest(plugin_name)
    if manifest is None:
        return {"error": f"Unknown plugin: {plugin_name}"}

    definitions = list(manifest.settings_defs)
    if action == "getdef":
        return {
            "plugin_name": plugin_name,
            "definitions": [definition.to_dict() for definition in definitions],
        }

    if action == "get":
        values = _load_settings_from_disk(manifest)
        return {
            "plugin_name": plugin_name,
            "definitions": [definition.to_dict() for definition in definitions],
            "values": _mask_settings(manifest.settings_defs, values),
        }

    if action == "set":
        if len(command) < 4:
            return {"error": "Usage: pluginsettings set <plugin_name> key:value [key:value ...]"}

        current_values = _load_settings_from_disk(manifest)
        changed_keys: list[str] = []
        restart_required = False
        defs_by_key = {definition.key: definition for definition in manifest.settings_defs}

        try:
            for raw_arg in command[3:]:
                if ":" not in str(raw_arg):
                    return {"error": f"Invalid setting argument (expected key:value): {raw_arg}"}
                key, raw_value = str(raw_arg).split(":", 1)
                definition = defs_by_key.get(key)
                if definition is None:
                    return {"error": f"Unknown setting '{key}' for plugin '{plugin_name}'"}
                typed_value = _parse_typed_value(definition, raw_value)
                ok, error = definition.validate(typed_value)
                if not ok:
                    return {"error": error}
                if current_values.get(key, definition.default) != typed_value:
                    changed_keys.append(key)
                    if definition.restart_required:
                        restart_required = True
                current_values[key] = typed_value
        except ValueError as exc:
            return {"error": str(exc)}

        _save_settings_to_disk(manifest, current_values)
        plugin_ctx = manager.get_context(plugin_name)
        if plugin_ctx is not None:
            try:
                plugin_ctx.set_settings(
                    {key: current_values[key] for key in changed_keys}
                )
            except Exception:
                # Disk write already succeeded; runtime update is optional.
                pass

        if restart_required:
            manager.mark_restart_required()

        return {
            "plugin_name": plugin_name,
            "updated": changed_keys,
            "restart_required": restart_required,
            "values": _mask_settings(manifest.settings_defs, current_values),
        }

    return {"error": f"Unknown pluginsettings action: {action}"}


async def cmd_pluginmanager(ctx: CommandContext, command: list[Any]) -> dict[str, Any]:
    """Handle pluginmanager list/enable/disable/info/install/repository commands."""
    try:
        manager = _require_plugin_manager(ctx)
    except Exception as exc:
        return {"error": str(exc)}

    if len(command) < 2:
        return {"error": "Usage: pluginmanager <list|enable|disable|info|install|uninstall|repository|installrepo> [...]"}

    action = str(command[1]).lower()

    if action == "list":
        plugins = manager.list_plugin_info()
        return {
            "pluginmanager": "list",
            "count": len(plugins),
            "plugins": plugins,
            "restart_required": manager.restart_required,
        }

    if action in {"enable", "disable"}:
        if len(command) < 3:
            return {"error": f"Usage: pluginmanager {action} <plugin_name>"}
        plugin_name = str(command[2])
        if manager.get_manifest(plugin_name) is None:
            return {"error": f"Unknown plugin: {plugin_name}"}
        manager.set_plugin_enabled(plugin_name, action == "enable")
        return {
            "pluginmanager": action,
            "name": plugin_name,
            "state": manager.get_plugin_state(plugin_name),
            "restart_required": manager.restart_required,
        }

    if action == "info":
        if len(command) < 3:
            return {"error": "Usage: pluginmanager info <plugin_name>"}
        plugin_name = str(command[2])
        info = next((item for item in manager.list_plugin_info() if item["name"] == plugin_name), None)
        if info is None:
            return {"error": f"Unknown plugin: {plugin_name}"}
        return {
            "pluginmanager": "info",
            "plugin": info,
            "definitions": (
                [definition.to_dict() for definition in manager.get_manifest(plugin_name).settings_defs]
                if manager.get_manifest(plugin_name) is not None
                else []
            ),
        }

    if action == "install":
        if len(command) < 4:
            return {"error": "Usage: pluginmanager install <url> <sha256>"}
        installer = ctx.plugin_installer
        if installer is None:
            return {"error": "Plugin installer not available"}

        url = str(command[2])
        sha256 = str(command[3])
        try:
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
                zip_data = response.content
            plugin_name = installer.install_from_zip(zip_data, sha256)
            manager.state_manager.set_enabled(plugin_name, True)
            manager.mark_restart_required()
            return {
                "pluginmanager": "install",
                "name": plugin_name,
                "installed": True,
                "restart_required": True,
            }
        except Exception as exc:
            return {"error": f"Install failed: {exc}"}

    if action == "uninstall":
        if len(command) < 3:
            return {"error": "Usage: pluginmanager uninstall <plugin_name>"}
        installer = ctx.plugin_installer
        if installer is None:
            return {"error": "Plugin installer not available"}

        plugin_name = str(command[2])
        if manager.is_core_plugin(plugin_name):
            return {"error": f"Core plugin cannot be uninstalled: {plugin_name}"}
        if not installer.uninstall(plugin_name):
            return {"error": f"Plugin not installed: {plugin_name}"}
        manager.mark_restart_required()
        return {
            "pluginmanager": "uninstall",
            "name": plugin_name,
            "uninstalled": True,
            "restart_required": True,
        }

    if action == "repository":
        repository = ctx.plugin_repository
        if repository is None:
            return {"error": "Plugin repository not available"}
        try:
            available = await repository.fetch_available()
            installed = {m.name: m.version for m in manager.manifests}
            core = {m.name for m in manager.manifests if m.plugin_type == "core"}
            plugins = repository.compare_with_installed(available, installed, core)
            return {"pluginmanager": "repository", "count": len(plugins), "plugins": plugins}
        except Exception as exc:
            return {"error": f"Repository fetch failed: {exc}"}

    if action == "installrepo":
        if len(command) < 3:
            return {"error": "Usage: pluginmanager installrepo <plugin_name>"}
        repository = ctx.plugin_repository
        installer = ctx.plugin_installer
        if repository is None or installer is None:
            return {"error": "Plugin repository or installer not available"}

        plugin_name = str(command[2])
        if manager.is_core_plugin(plugin_name):
            return {"error": f"Core plugin cannot be installed from repository: {plugin_name}"}

        try:
            available = await repository.fetch_available()
            entry = next((item for item in available if item.name == plugin_name), None)
            if entry is None:
                return {"error": f"Plugin not found in repository: {plugin_name}"}

            compatible, reason = repository.check_compatible(entry)
            if not compatible:
                return {"error": reason}

            zip_data = await repository.download_plugin(entry)
            installed_name = installer.install_from_zip(zip_data, entry.sha256)
            manager.state_manager.set_enabled(installed_name, True)
            manager.mark_restart_required()
            return {
                "pluginmanager": "installrepo",
                "name": installed_name,
                "version": entry.version,
                "installed": True,
                "restart_required": True,
            }
        except Exception as exc:
            return {"error": f"Install from repository failed: {exc}"}

    return {"error": f"Unknown pluginmanager action: {action}"}
