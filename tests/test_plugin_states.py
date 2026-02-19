from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from resonance.core.events import EventBus
from resonance.plugin_manager import PluginManager, PluginStateManager
from resonance.web.handlers.plugins import cmd_pluginmanager


def _create_plugin(root: Path, name: str, setup_body: str = "pass") -> None:
    plugin_dir = root / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.toml").write_text(
        f'[plugin]\nname = "{name}"\nversion = "1.0.0"\n',
        encoding="utf-8",
    )
    (plugin_dir / "__init__.py").write_text(
        f"async def setup(ctx):\n    {setup_body}\n",
        encoding="utf-8",
    )


def _create_plugin_with_settings(root: Path, name: str) -> None:
    plugin_dir = root / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.toml").write_text(
        f"""
[plugin]
name = "{name}"
version = "1.0.0"

[settings.enabled]
type = "bool"
default = true
""",
        encoding="utf-8",
    )
    (plugin_dir / "__init__.py").write_text(
        "async def setup(ctx):\n    pass\n",
        encoding="utf-8",
    )


class _FakeCommandCtx:
    def __init__(self, manager: PluginManager) -> None:
        self.plugin_manager = manager
        self.plugin_installer = None
        self.plugin_repository = None


async def _make_manager(
    tmp_path: Path,
    plugin_names: list[str] | None = None,
    *,
    with_settings: bool = False,
    start: bool = True,
) -> PluginManager:
    """Helper to create a PluginManager with plugins ready for testing."""
    core_dir = tmp_path / "core_plugins"
    if plugin_names:
        for name in plugin_names:
            shutil.rmtree(Path("data/plugins") / name, ignore_errors=True)
            if with_settings:
                _create_plugin_with_settings(core_dir, name)
            else:
                _create_plugin(core_dir, name)

    manager = PluginManager(
        core_plugins_dir=core_dir,
        community_plugins_dir=tmp_path / "__none__",
        state_file=tmp_path / "plugin_states.json",
    )
    if plugin_names:
        await manager.discover()
        await manager.load_all()
        if start:
            await manager.start_all(
                event_bus=EventBus(),
                music_library=MagicMock(),
                player_registry=MagicMock(),
            )
    return manager


# =============================================================================
# Phase B — TestPluginStateManager (comprehensive)
# =============================================================================


class TestPluginStateManager:
    def test_default_unknown_enabled(self, tmp_path: Path) -> None:
        state = PluginStateManager(state_file=tmp_path / "states.json")
        assert state.is_enabled("anything") is True
        assert state.get_state("anything") == "enabled"

    def test_save_and_reload(self, tmp_path: Path) -> None:
        path = tmp_path / "states.json"
        state = PluginStateManager(state_file=path)
        state.set_enabled("demo", False)
        assert path.is_file()

        loaded = PluginStateManager(state_file=path)
        assert loaded.is_enabled("demo") is False
        assert loaded.get_all_states()["demo"] == "disabled"

    def test_corrupt_file_graceful(self, tmp_path: Path) -> None:
        """Corrupt JSON should be handled gracefully — empty state, all enabled."""
        path = tmp_path / "states.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not valid json!!!", encoding="utf-8")

        state = PluginStateManager(state_file=path)
        assert state.is_enabled("anything") is True
        assert state.get_all_states() == {}

    def test_missing_file_empty_states(self, tmp_path: Path) -> None:
        path = tmp_path / "nonexistent" / "states.json"
        state = PluginStateManager(state_file=path)
        assert state.get_all_states() == {}
        assert state.is_enabled("anything") is True

    def test_atomic_write_file_exists(self, tmp_path: Path) -> None:
        path = tmp_path / "states.json"
        state = PluginStateManager(state_file=path)
        state.set_enabled("demo", False)
        assert path.is_file()
        # .tmp file should not linger
        assert not path.with_suffix(".tmp").exists()

    def test_get_all_states_returns_copy(self, tmp_path: Path) -> None:
        """Modifying returned dict should not affect internal state."""
        state = PluginStateManager(state_file=tmp_path / "states.json")
        state.set_enabled("a", False)
        all_states = state.get_all_states()
        all_states["a"] = "enabled"
        all_states["b"] = "disabled"
        # Internal state should remain unchanged
        assert state.is_enabled("a") is False
        assert state.is_enabled("b") is True  # unknown → enabled

    def test_set_enabled_creates_parent_dirs(self, tmp_path: Path) -> None:
        path = tmp_path / "deep" / "nested" / "states.json"
        state = PluginStateManager(state_file=path)
        state.set_enabled("demo", False)
        assert path.is_file()

    def test_multiple_plugins(self, tmp_path: Path) -> None:
        path = tmp_path / "states.json"
        state = PluginStateManager(state_file=path)
        state.set_enabled("alpha", False)
        state.set_enabled("beta", True)
        state.set_enabled("gamma", False)

        assert state.is_enabled("alpha") is False
        assert state.is_enabled("beta") is True
        assert state.is_enabled("gamma") is False

        # Reload and verify
        loaded = PluginStateManager(state_file=path)
        assert loaded.is_enabled("alpha") is False
        assert loaded.is_enabled("beta") is True
        assert loaded.is_enabled("gamma") is False

    def test_toggle_back_and_forth(self, tmp_path: Path) -> None:
        state = PluginStateManager(state_file=tmp_path / "states.json")
        state.set_enabled("demo", False)
        assert state.is_enabled("demo") is False
        state.set_enabled("demo", True)
        assert state.is_enabled("demo") is True
        state.set_enabled("demo", False)
        assert state.is_enabled("demo") is False

    def test_normalized_state_values(self, tmp_path: Path) -> None:
        """Non-standard state values should be normalized to 'enabled'."""
        path = tmp_path / "states.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"_version": 1, "states": {"a": "DISABLED", "b": "Enabled", "c": "whatever"}}
        path.write_text(json.dumps(payload), encoding="utf-8")

        state = PluginStateManager(state_file=path)
        assert state.is_enabled("a") is False  # DISABLED → disabled
        assert state.is_enabled("b") is True  # Enabled → enabled
        assert state.is_enabled("c") is True  # anything else → enabled

    def test_state_file_version_field(self, tmp_path: Path) -> None:
        path = tmp_path / "states.json"
        state = PluginStateManager(state_file=path)
        state.set_enabled("demo", False)

        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        assert payload["_version"] == 1
        assert "states" in payload
        assert payload["states"]["demo"] == "disabled"

    def test_disabled_state_string(self, tmp_path: Path) -> None:
        state = PluginStateManager(state_file=tmp_path / "states.json")
        state.set_enabled("demo", False)
        assert state.get_state("demo") == "disabled"
        state.set_enabled("demo", True)
        assert state.get_state("demo") == "enabled"

    def test_empty_json_object_handled(self, tmp_path: Path) -> None:
        """An empty JSON object (no 'states' key) should be handled gracefully."""
        path = tmp_path / "states.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")

        state = PluginStateManager(state_file=path)
        assert state.get_all_states() == {}
        assert state.is_enabled("anything") is True

    def test_json_array_handled(self, tmp_path: Path) -> None:
        """A JSON array instead of object should be handled gracefully."""
        path = tmp_path / "states.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[]", encoding="utf-8")

        state = PluginStateManager(state_file=path)
        assert state.get_all_states() == {}


# =============================================================================
# Phase B — TestPluginManagerWithStates (comprehensive)
# =============================================================================


class TestPluginManagerStates:
    @pytest.mark.asyncio
    async def test_start_respects_disabled_state(self, tmp_path: Path) -> None:
        _create_plugin(tmp_path, "enabled_one")
        _create_plugin(tmp_path, "disabled_one")

        manager = PluginManager(
            core_plugins_dir=tmp_path,
            community_plugins_dir=tmp_path / "__none__",
            state_file=tmp_path / "plugin_states.json",
        )
        await manager.discover()
        await manager.load_all()
        manager.state_manager.set_enabled("disabled_one", False)

        started = await manager.start_all(
            event_bus=EventBus(),
            music_library=MagicMock(),
            player_registry=MagicMock(),
        )
        assert started == 1
        assert "enabled_one" in manager.started_plugins
        assert "disabled_one" not in manager.started_plugins

    @pytest.mark.asyncio
    async def test_enable_disable_marks_restart_required(self, tmp_path: Path) -> None:
        _create_plugin(tmp_path, "demo")
        manager = PluginManager(
            core_plugins_dir=tmp_path,
            community_plugins_dir=tmp_path / "__none__",
            state_file=tmp_path / "plugin_states.json",
        )
        await manager.discover()
        assert manager.restart_required is False
        manager.set_plugin_enabled("demo", False)
        assert manager.get_plugin_state("demo") == "disabled"
        assert manager.restart_required is True

    @pytest.mark.asyncio
    async def test_default_enabled_for_unknown(self, tmp_path: Path) -> None:
        """Plugins not in the state file should default to enabled."""
        manager = await _make_manager(tmp_path, ["plugin_a", "plugin_b"])
        assert manager.get_plugin_state("plugin_a") == "enabled"
        assert manager.get_plugin_state("plugin_b") == "enabled"
        # Both should be started
        assert "plugin_a" in manager.started_plugins
        assert "plugin_b" in manager.started_plugins

    @pytest.mark.asyncio
    async def test_multiple_disabled(self, tmp_path: Path) -> None:
        """Multiple plugins can be disabled simultaneously."""
        core_dir = tmp_path / "core_plugins"
        for name in ["plug_a", "plug_b", "plug_c"]:
            shutil.rmtree(Path("data/plugins") / name, ignore_errors=True)
            _create_plugin(core_dir, name)

        manager = PluginManager(
            core_plugins_dir=core_dir,
            community_plugins_dir=tmp_path / "__none__",
            state_file=tmp_path / "plugin_states.json",
        )
        await manager.discover()
        await manager.load_all()

        manager.state_manager.set_enabled("plug_a", False)
        manager.state_manager.set_enabled("plug_c", False)

        started = await manager.start_all(
            event_bus=EventBus(),
            music_library=MagicMock(),
            player_registry=MagicMock(),
        )
        assert started == 1
        assert "plug_b" in manager.started_plugins
        assert "plug_a" not in manager.started_plugins
        assert "plug_c" not in manager.started_plugins

    @pytest.mark.asyncio
    async def test_list_plugin_info_shows_state(self, tmp_path: Path) -> None:
        core_dir = tmp_path / "core_plugins"
        for name in ["info_enabled", "info_disabled"]:
            shutil.rmtree(Path("data/plugins") / name, ignore_errors=True)
            _create_plugin(core_dir, name)

        manager = PluginManager(
            core_plugins_dir=core_dir,
            community_plugins_dir=tmp_path / "__none__",
            state_file=tmp_path / "plugin_states.json",
        )
        await manager.discover()
        await manager.load_all()
        manager.state_manager.set_enabled("info_disabled", False)

        await manager.start_all(
            event_bus=EventBus(),
            music_library=MagicMock(),
            player_registry=MagicMock(),
        )

        info_list = manager.list_plugin_info()
        by_name = {item["name"]: item for item in info_list}

        assert by_name["info_enabled"]["state"] == "enabled"
        assert by_name["info_enabled"]["started"] is True
        assert by_name["info_disabled"]["state"] == "disabled"
        assert by_name["info_disabled"]["started"] is False

    @pytest.mark.asyncio
    async def test_list_plugin_info_shows_started(self, tmp_path: Path) -> None:
        manager = await _make_manager(tmp_path, ["running_plug"])
        info_list = manager.list_plugin_info()
        assert len(info_list) == 1
        assert info_list[0]["started"] is True

    @pytest.mark.asyncio
    async def test_restart_required_cleared(self, tmp_path: Path) -> None:
        manager = await _make_manager(tmp_path, ["demo_clear"])
        manager.set_plugin_enabled("demo_clear", False)
        assert manager.restart_required is True
        manager.clear_restart_required()
        assert manager.restart_required is False

    @pytest.mark.asyncio
    async def test_enable_already_enabled_no_restart(self, tmp_path: Path) -> None:
        """Re-enabling an already enabled plugin should NOT mark restart_required."""
        manager = await _make_manager(tmp_path, ["already_on"])
        assert manager.restart_required is False
        manager.set_plugin_enabled("already_on", True)
        assert manager.restart_required is False

    @pytest.mark.asyncio
    async def test_has_settings_in_info(self, tmp_path: Path) -> None:
        manager = await _make_manager(
            tmp_path, ["settings_plug"], with_settings=True
        )
        info_list = manager.list_plugin_info()
        assert info_list[0]["has_settings"] is True

    @pytest.mark.asyncio
    async def test_has_settings_false_in_info(self, tmp_path: Path) -> None:
        manager = await _make_manager(
            tmp_path, ["no_settings_plug"], with_settings=False
        )
        info_list = manager.list_plugin_info()
        assert info_list[0]["has_settings"] is False

    @pytest.mark.asyncio
    async def test_can_uninstall_core_is_false(self, tmp_path: Path) -> None:
        manager = await _make_manager(tmp_path, ["core_plug"])
        info_list = manager.list_plugin_info()
        assert info_list[0]["can_uninstall"] is False

    @pytest.mark.asyncio
    async def test_plugin_type_in_info(self, tmp_path: Path) -> None:
        manager = await _make_manager(tmp_path, ["typed_plug"])
        info_list = manager.list_plugin_info()
        assert info_list[0]["type"] == "core"

    @pytest.mark.asyncio
    async def test_is_core_plugin(self, tmp_path: Path) -> None:
        manager = await _make_manager(tmp_path, ["core_check"])
        assert manager.is_core_plugin("core_check") is True
        assert manager.is_core_plugin("nonexistent") is False

    @pytest.mark.asyncio
    async def test_get_all_plugin_states(self, tmp_path: Path) -> None:
        manager = await _make_manager(tmp_path, ["state_a", "state_b"])
        manager.set_plugin_enabled("state_a", False)
        states = manager.get_all_plugin_states()
        assert states["state_a"] == "disabled"


# =============================================================================
# Phase B — TestCmdPluginmanager (comprehensive)
# =============================================================================


class TestCmdPluginmanager:
    @pytest.mark.asyncio
    async def test_list_subcommand(self, tmp_path: Path) -> None:
        manager = await _make_manager(tmp_path, ["list_plug"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginmanager(ctx, ["pluginmanager", "list"])
        assert result["pluginmanager"] == "list"
        assert result["count"] == 1
        assert len(result["plugins"]) == 1
        assert result["plugins"][0]["name"] == "list_plug"
        assert result["plugins"][0]["state"] == "enabled"

    @pytest.mark.asyncio
    async def test_enable_disable(self, tmp_path: Path) -> None:
        manager = await _make_manager(tmp_path, ["toggle_plug"])
        ctx = _FakeCommandCtx(manager)

        disabled = await cmd_pluginmanager(
            ctx, ["pluginmanager", "disable", "toggle_plug"]
        )
        assert disabled["pluginmanager"] == "disable"
        assert disabled["state"] == "disabled"
        assert disabled["restart_required"] is True

        enabled = await cmd_pluginmanager(
            ctx, ["pluginmanager", "enable", "toggle_plug"]
        )
        assert enabled["pluginmanager"] == "enable"
        assert enabled["state"] == "enabled"

    @pytest.mark.asyncio
    async def test_info_subcommand(self, tmp_path: Path) -> None:
        manager = await _make_manager(
            tmp_path, ["info_plug"], with_settings=True
        )
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginmanager(
            ctx, ["pluginmanager", "info", "info_plug"]
        )
        assert result["pluginmanager"] == "info"
        assert result["plugin"]["name"] == "info_plug"
        assert result["plugin"]["version"] == "1.0.0"
        assert result["plugin"]["state"] == "enabled"
        assert result["plugin"]["started"] is True
        assert result["plugin"]["has_settings"] is True
        assert "definitions" in result

    @pytest.mark.asyncio
    async def test_info_unknown_plugin_error(self, tmp_path: Path) -> None:
        manager = await _make_manager(tmp_path, ["existing"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginmanager(
            ctx, ["pluginmanager", "info", "nonexistent"]
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_enable_unknown_plugin_error(self, tmp_path: Path) -> None:
        manager = await _make_manager(tmp_path, ["known_plug"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginmanager(
            ctx, ["pluginmanager", "enable", "ghost"]
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_disable_unknown_plugin_error(self, tmp_path: Path) -> None:
        manager = await _make_manager(tmp_path, ["known_plug2"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginmanager(
            ctx, ["pluginmanager", "disable", "ghost"]
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_already_enabled_still_works(self, tmp_path: Path) -> None:
        """Enabling an already enabled plugin should succeed without error."""
        manager = await _make_manager(tmp_path, ["already_enabled"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginmanager(
            ctx, ["pluginmanager", "enable", "already_enabled"]
        )
        assert "error" not in result
        assert result["state"] == "enabled"

    @pytest.mark.asyncio
    async def test_already_disabled_returns_state(self, tmp_path: Path) -> None:
        """Disabling an already disabled plugin should succeed."""
        manager = await _make_manager(tmp_path, ["double_dis"])
        ctx = _FakeCommandCtx(manager)

        await cmd_pluginmanager(ctx, ["pluginmanager", "disable", "double_dis"])
        result = await cmd_pluginmanager(
            ctx, ["pluginmanager", "disable", "double_dis"]
        )
        assert "error" not in result
        assert result["state"] == "disabled"

    @pytest.mark.asyncio
    async def test_missing_plugin_name_error(self, tmp_path: Path) -> None:
        manager = await _make_manager(tmp_path, ["some_plug"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginmanager(ctx, ["pluginmanager", "enable"])
        assert "error" in result

        result2 = await cmd_pluginmanager(ctx, ["pluginmanager", "disable"])
        assert "error" in result2

        result3 = await cmd_pluginmanager(ctx, ["pluginmanager", "info"])
        assert "error" in result3

    @pytest.mark.asyncio
    async def test_list_restart_required(self, tmp_path: Path) -> None:
        manager = await _make_manager(tmp_path, ["restart_plug"])
        ctx = _FakeCommandCtx(manager)

        # Initially no restart needed
        result = await cmd_pluginmanager(ctx, ["pluginmanager", "list"])
        assert result["restart_required"] is False

        # Disable a plugin → restart needed
        await cmd_pluginmanager(
            ctx, ["pluginmanager", "disable", "restart_plug"]
        )
        result2 = await cmd_pluginmanager(ctx, ["pluginmanager", "list"])
        assert result2["restart_required"] is True

    @pytest.mark.asyncio
    async def test_response_format_list(self, tmp_path: Path) -> None:
        manager = await _make_manager(
            tmp_path, ["fmt_plug"], with_settings=True
        )
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginmanager(ctx, ["pluginmanager", "list"])
        plugin = result["plugins"][0]
        # Verify all expected fields are present
        expected_keys = {
            "name",
            "version",
            "description",
            "author",
            "category",
            "icon",
            "state",
            "started",
            "type",
            "has_settings",
            "can_uninstall",
        }
        assert expected_keys <= set(plugin.keys())

    @pytest.mark.asyncio
    async def test_unknown_action(self, tmp_path: Path) -> None:
        manager = await _make_manager(tmp_path, ["action_plug"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginmanager(
            ctx, ["pluginmanager", "frobnicate"]
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_missing_args_returns_usage(self, tmp_path: Path) -> None:
        manager = await _make_manager(tmp_path, ["args_plug"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginmanager(ctx, ["pluginmanager"])
        assert "error" in result

    @pytest.mark.asyncio
    async def test_uninstall_core_rejected(self, tmp_path: Path) -> None:
        from resonance.plugin_installer import PluginInstaller

        manager = await _make_manager(tmp_path, ["core_no_uninstall"])
        ctx = _FakeCommandCtx(manager)
        ctx.plugin_installer = PluginInstaller(install_dir=tmp_path / "installed")

        result = await cmd_pluginmanager(
            ctx, ["pluginmanager", "uninstall", "core_no_uninstall"]
        )
        assert "error" in result
        assert "Core plugin" in result["error"]

    @pytest.mark.asyncio
    async def test_uninstall_no_installer(self, tmp_path: Path) -> None:
        manager = await _make_manager(tmp_path, ["no_inst_plug"])
        ctx = _FakeCommandCtx(manager)
        ctx.plugin_installer = None

        result = await cmd_pluginmanager(
            ctx, ["pluginmanager", "uninstall", "no_inst_plug"]
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_install_no_installer(self, tmp_path: Path) -> None:
        manager = await _make_manager(tmp_path, ["no_inst_plug2"])
        ctx = _FakeCommandCtx(manager)
        ctx.plugin_installer = None

        result = await cmd_pluginmanager(
            ctx, ["pluginmanager", "install", "https://example.com/plugin.zip", "abc123"]
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_install_missing_args(self, tmp_path: Path) -> None:
        manager = await _make_manager(tmp_path, ["inst_args_plug"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginmanager(
            ctx, ["pluginmanager", "install", "https://example.com/plugin.zip"]
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_repository_no_repository(self, tmp_path: Path) -> None:
        manager = await _make_manager(tmp_path, ["repo_plug"])
        ctx = _FakeCommandCtx(manager)
        ctx.plugin_repository = None

        result = await cmd_pluginmanager(ctx, ["pluginmanager", "repository"])
        assert "error" in result

    @pytest.mark.asyncio
    async def test_installrepo_no_repository(self, tmp_path: Path) -> None:
        manager = await _make_manager(tmp_path, ["installrepo_plug"])
        ctx = _FakeCommandCtx(manager)
        ctx.plugin_repository = None
        ctx.plugin_installer = None

        result = await cmd_pluginmanager(
            ctx, ["pluginmanager", "installrepo", "some_plugin"]
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_installrepo_core_rejected(self, tmp_path: Path) -> None:
        manager = await _make_manager(tmp_path, ["core_installrepo"])
        ctx = _FakeCommandCtx(manager)
        ctx.plugin_repository = MagicMock()
        ctx.plugin_installer = MagicMock()

        result = await cmd_pluginmanager(
            ctx, ["pluginmanager", "installrepo", "core_installrepo"]
        )
        assert "error" in result
        assert "Core plugin" in result["error"]

    @pytest.mark.asyncio
    async def test_installrepo_missing_plugin_name(self, tmp_path: Path) -> None:
        manager = await _make_manager(tmp_path, ["installrepo_noarg"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginmanager(
            ctx, ["pluginmanager", "installrepo"]
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_uninstall_missing_plugin_name(self, tmp_path: Path) -> None:
        manager = await _make_manager(tmp_path, ["uninstall_noarg"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginmanager(
            ctx, ["pluginmanager", "uninstall"]
        )
        assert "error" in result
