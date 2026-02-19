from __future__ import annotations

import hashlib
import io
import shutil
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from resonance.core.events import EventBus
from resonance.plugin_installer import PluginInstaller
from resonance.plugin_manager import PluginManager
from resonance.plugin_repository import PluginRepository, RepositoryEntry
from resonance.web.handlers.plugins import cmd_pluginmanager, cmd_pluginsettings


def _create_plugin(
    root: Path,
    name: str,
    *,
    with_settings: bool = True,
    version: str = "1.0.0",
    category: str = "",
    icon: str = "",
) -> None:
    plugin_dir = root / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    settings = ""
    if with_settings:
        settings = """
[settings.enabled]
type = "bool"
default = true

[settings.volume]
type = "int"
default = 50
min = 0
max = 100

[settings.secret_key]
type = "string"
default = ""
secret = true

[settings.service]
type = "select"
options = ["spotify", "tidal", "qobuz"]
default = "spotify"
"""
    category_line = f'category = "{category}"' if category else ""
    icon_line = f'icon = "{icon}"' if icon else ""
    (plugin_dir / "plugin.toml").write_text(
        f"""
[plugin]
name = "{name}"
version = "{version}"
{category_line}
{icon_line}

{settings}
""",
        encoding="utf-8",
    )
    (plugin_dir / "__init__.py").write_text(
        "async def setup(ctx):\n    pass\n", encoding="utf-8"
    )


def _build_zip(plugin_name: str, *, version: str = "1.0.0") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            f"{plugin_name}/plugin.toml",
            f'[plugin]\nname = "{plugin_name}"\nversion = "{version}"\n',
        )
        zf.writestr(
            f"{plugin_name}/__init__.py",
            "async def setup(ctx):\n    pass\n",
        )
    return buf.getvalue()


class _FakeCommandCtx:
    def __init__(
        self,
        manager: PluginManager,
        installer: PluginInstaller | None = None,
        repository: PluginRepository | None = None,
    ) -> None:
        self.plugin_manager = manager
        self.plugin_installer = installer
        self.plugin_repository = repository


async def _setup_manager(
    tmp_path: Path,
    plugin_names: list[str] | None = None,
    *,
    with_settings: bool = True,
    community_names: list[str] | None = None,
    start: bool = True,
) -> PluginManager:
    core_dir = tmp_path / "core_plugins"
    community_dir = tmp_path / "community_plugins"
    if plugin_names:
        for name in plugin_names:
            shutil.rmtree(Path("data/plugins") / name, ignore_errors=True)
            _create_plugin(core_dir, name, with_settings=with_settings)
    if community_names:
        for name in community_names:
            shutil.rmtree(Path("data/plugins") / name, ignore_errors=True)
            _create_plugin(community_dir, name, with_settings=with_settings)

    manager = PluginManager(
        core_plugins_dir=core_dir,
        community_plugins_dir=community_dir,
        state_file=tmp_path / "states.json",
    )
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
# cmd_pluginsettings — comprehensive
# =============================================================================


class TestCmdPluginsettingsGet:
    @pytest.mark.asyncio
    async def test_get_returns_values_and_definitions(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["ps_get_1"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginsettings(ctx, ["pluginsettings", "get", "ps_get_1"])
        assert result["plugin_name"] == "ps_get_1"
        assert "values" in result
        assert "definitions" in result
        assert result["values"]["enabled"] is True
        assert result["values"]["volume"] == 50
        assert len(result["definitions"]) == 4

    @pytest.mark.asyncio
    async def test_get_unknown_plugin_returns_error(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["ps_get_2"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginsettings(ctx, ["pluginsettings", "get", "nonexistent"])
        assert "error" in result
        assert "Unknown plugin" in result["error"]

    @pytest.mark.asyncio
    async def test_get_masks_secrets(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["ps_get_mask"])
        ctx = _FakeCommandCtx(manager)

        # Set a secret value first
        await cmd_pluginsettings(
            ctx, ["pluginsettings", "set", "ps_get_mask", "secret_key:my_super_secret"]
        )

        result = await cmd_pluginsettings(ctx, ["pluginsettings", "get", "ps_get_mask"])
        assert result["values"]["secret_key"] != "my_super_secret"
        assert "secret_key" in result["values"]


class TestCmdPluginsettingsGetdef:
    @pytest.mark.asyncio
    async def test_getdef_returns_definitions(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["ps_getdef_1"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginsettings(
            ctx, ["pluginsettings", "getdef", "ps_getdef_1"]
        )
        assert result["plugin_name"] == "ps_getdef_1"
        assert "definitions" in result
        assert "values" not in result
        keys = [d["key"] for d in result["definitions"]]
        assert "enabled" in keys
        assert "volume" in keys
        assert "secret_key" in keys
        assert "service" in keys

    @pytest.mark.asyncio
    async def test_getdef_unknown_plugin(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["ps_getdef_2"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginsettings(
            ctx, ["pluginsettings", "getdef", "nonexistent"]
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_getdef_includes_type_and_constraints(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["ps_getdef_3"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginsettings(
            ctx, ["pluginsettings", "getdef", "ps_getdef_3"]
        )
        defs_by_key = {d["key"]: d for d in result["definitions"]}
        assert defs_by_key["volume"]["type"] == "int"
        assert defs_by_key["volume"]["min"] == 0
        assert defs_by_key["volume"]["max"] == 100
        assert defs_by_key["enabled"]["type"] == "bool"
        assert defs_by_key["service"]["type"] == "select"
        assert defs_by_key["service"]["options"] == ["spotify", "tidal", "qobuz"]
        assert defs_by_key["secret_key"].get("secret") is True


class TestCmdPluginsettingsSet:
    @pytest.mark.asyncio
    async def test_set_single_value(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["ps_set_1"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginsettings(
            ctx, ["pluginsettings", "set", "ps_set_1", "volume:75"]
        )
        assert "error" not in result
        assert result["values"]["volume"] == 75
        assert "volume" in result["updated"]

    @pytest.mark.asyncio
    async def test_set_multiple_values(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["ps_set_multi"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginsettings(
            ctx,
            [
                "pluginsettings",
                "set",
                "ps_set_multi",
                "volume:80",
                "enabled:false",
                "service:tidal",
            ],
        )
        assert "error" not in result
        assert sorted(result["updated"]) == ["enabled", "service", "volume"]
        assert result["values"]["volume"] == 80
        assert result["values"]["enabled"] is False
        assert result["values"]["service"] == "tidal"

    @pytest.mark.asyncio
    async def test_set_bool_string_conversion(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["ps_set_bool"])
        ctx = _FakeCommandCtx(manager)

        for truthy in ("true", "1", "yes", "on"):
            result = await cmd_pluginsettings(
                ctx, ["pluginsettings", "set", "ps_set_bool", f"enabled:{truthy}"]
            )
            assert "error" not in result
            assert result["values"]["enabled"] is True

        for falsy in ("false", "0", "no", "off"):
            result = await cmd_pluginsettings(
                ctx, ["pluginsettings", "set", "ps_set_bool", f"enabled:{falsy}"]
            )
            assert "error" not in result
            assert result["values"]["enabled"] is False

    @pytest.mark.asyncio
    async def test_set_invalid_bool_string(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["ps_set_bool_bad"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginsettings(
            ctx, ["pluginsettings", "set", "ps_set_bool_bad", "enabled:maybe"]
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_set_int_string_conversion(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["ps_set_int"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginsettings(
            ctx, ["pluginsettings", "set", "ps_set_int", "volume:42"]
        )
        assert "error" not in result
        assert result["values"]["volume"] == 42

    @pytest.mark.asyncio
    async def test_set_int_out_of_range(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["ps_set_range"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginsettings(
            ctx, ["pluginsettings", "set", "ps_set_range", "volume:999"]
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_set_int_below_min(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["ps_set_min"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginsettings(
            ctx, ["pluginsettings", "set", "ps_set_min", "volume:-1"]
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_set_select_valid_option(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["ps_set_select"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginsettings(
            ctx, ["pluginsettings", "set", "ps_set_select", "service:qobuz"]
        )
        assert "error" not in result
        assert result["values"]["service"] == "qobuz"

    @pytest.mark.asyncio
    async def test_set_select_invalid_option(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["ps_set_select_bad"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginsettings(
            ctx, ["pluginsettings", "set", "ps_set_select_bad", "service:deezer"]
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_set_unknown_key(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["ps_set_unknown_key"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginsettings(
            ctx, ["pluginsettings", "set", "ps_set_unknown_key", "nonexistent:value"]
        )
        assert "error" in result
        assert "Unknown setting" in result["error"]

    @pytest.mark.asyncio
    async def test_set_unknown_plugin(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["ps_set_unkn_plug"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginsettings(
            ctx, ["pluginsettings", "set", "ghost_plugin", "volume:50"]
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_set_missing_colon(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["ps_set_no_colon"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginsettings(
            ctx, ["pluginsettings", "set", "ps_set_no_colon", "volume_no_value"]
        )
        assert "error" in result
        assert "key:value" in result["error"]

    @pytest.mark.asyncio
    async def test_set_missing_key_value_args(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["ps_set_no_args"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginsettings(
            ctx, ["pluginsettings", "set", "ps_set_no_args"]
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_set_no_change_returns_empty_updated(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["ps_set_nochange"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginsettings(
            ctx, ["pluginsettings", "set", "ps_set_nochange", "volume:50"]
        )
        assert "error" not in result
        assert result["updated"] == []

    @pytest.mark.asyncio
    async def test_set_secret_masked_in_response(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["ps_set_mask_resp"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginsettings(
            ctx,
            ["pluginsettings", "set", "ps_set_mask_resp", "secret_key:supersecretvalue"],
        )
        assert "error" not in result
        assert result["values"]["secret_key"] != "supersecretvalue"
        assert "secret_key" in result["updated"]

    @pytest.mark.asyncio
    async def test_set_persists_across_get(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["ps_set_persist"])
        ctx = _FakeCommandCtx(manager)

        await cmd_pluginsettings(
            ctx, ["pluginsettings", "set", "ps_set_persist", "volume:99"]
        )
        result = await cmd_pluginsettings(
            ctx, ["pluginsettings", "get", "ps_set_persist"]
        )
        assert result["values"]["volume"] == 99


class TestCmdPluginsettingsEdgeCases:
    @pytest.mark.asyncio
    async def test_missing_args_returns_usage(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["ps_edge_1"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginsettings(ctx, ["pluginsettings"])
        assert "error" in result
        assert "Usage" in result["error"]

    @pytest.mark.asyncio
    async def test_only_two_args_returns_usage(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["ps_edge_2"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginsettings(ctx, ["pluginsettings", "get"])
        assert "error" in result

    @pytest.mark.asyncio
    async def test_unknown_action_returns_error(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["ps_edge_3"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginsettings(
            ctx, ["pluginsettings", "delete", "ps_edge_3"]
        )
        assert "error" in result
        assert "Unknown" in result["error"]

    @pytest.mark.asyncio
    async def test_no_plugin_manager_returns_error(self, tmp_path: Path) -> None:
        ctx = _FakeCommandCtx(None)  # type: ignore[arg-type]

        result = await cmd_pluginsettings(ctx, ["pluginsettings", "get", "something"])
        assert "error" in result

    @pytest.mark.asyncio
    async def test_plugin_without_settings_get(self, tmp_path: Path) -> None:
        manager = await _setup_manager(
            tmp_path, ["ps_nosettings"], with_settings=False
        )
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginsettings(
            ctx, ["pluginsettings", "get", "ps_nosettings"]
        )
        assert "error" not in result
        assert result["values"] == {}
        assert result["definitions"] == []


# =============================================================================
# cmd_pluginmanager — comprehensive
# =============================================================================


class TestCmdPluginmanagerList:
    @pytest.mark.asyncio
    async def test_list_returns_all_plugins(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["pm_list_a", "pm_list_b"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginmanager(ctx, ["pluginmanager", "list"])
        assert result["pluginmanager"] == "list"
        assert result["count"] == 2
        names = {p["name"] for p in result["plugins"]}
        assert names == {"pm_list_a", "pm_list_b"}

    @pytest.mark.asyncio
    async def test_list_includes_restart_required(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["pm_list_restart"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginmanager(ctx, ["pluginmanager", "list"])
        assert result["restart_required"] is False

        await cmd_pluginmanager(ctx, ["pluginmanager", "disable", "pm_list_restart"])
        result2 = await cmd_pluginmanager(ctx, ["pluginmanager", "list"])
        assert result2["restart_required"] is True

    @pytest.mark.asyncio
    async def test_list_response_format(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["pm_list_fmt"], with_settings=True)
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginmanager(ctx, ["pluginmanager", "list"])
        plugin = result["plugins"][0]
        expected_keys = {
            "name", "version", "description", "author", "category", "icon",
            "state", "started", "type", "has_settings", "can_uninstall",
        }
        assert expected_keys <= set(plugin.keys())

    @pytest.mark.asyncio
    async def test_list_empty(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, [])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginmanager(ctx, ["pluginmanager", "list"])
        assert result["count"] == 0
        assert result["plugins"] == []

    @pytest.mark.asyncio
    async def test_list_shows_state_for_each_plugin(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["pm_ls_en", "pm_ls_dis"])
        ctx = _FakeCommandCtx(manager)

        await cmd_pluginmanager(ctx, ["pluginmanager", "disable", "pm_ls_dis"])
        result = await cmd_pluginmanager(ctx, ["pluginmanager", "list"])

        by_name = {p["name"]: p for p in result["plugins"]}
        assert by_name["pm_ls_en"]["state"] == "enabled"
        assert by_name["pm_ls_dis"]["state"] == "disabled"


class TestCmdPluginmanagerEnableDisable:
    @pytest.mark.asyncio
    async def test_enable_plugin(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["pm_en"])
        ctx = _FakeCommandCtx(manager)

        # First disable
        await cmd_pluginmanager(ctx, ["pluginmanager", "disable", "pm_en"])
        # Then re-enable
        result = await cmd_pluginmanager(ctx, ["pluginmanager", "enable", "pm_en"])
        assert result["pluginmanager"] == "enable"
        assert result["name"] == "pm_en"
        assert result["state"] == "enabled"

    @pytest.mark.asyncio
    async def test_disable_plugin(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["pm_dis"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginmanager(ctx, ["pluginmanager", "disable", "pm_dis"])
        assert result["pluginmanager"] == "disable"
        assert result["name"] == "pm_dis"
        assert result["state"] == "disabled"
        assert result["restart_required"] is True

    @pytest.mark.asyncio
    async def test_enable_unknown_plugin(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["pm_en_unkn"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginmanager(ctx, ["pluginmanager", "enable", "ghost"])
        assert "error" in result

    @pytest.mark.asyncio
    async def test_disable_unknown_plugin(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["pm_dis_unkn"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginmanager(ctx, ["pluginmanager", "disable", "ghost"])
        assert "error" in result

    @pytest.mark.asyncio
    async def test_enable_already_enabled(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["pm_en_dup"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginmanager(ctx, ["pluginmanager", "enable", "pm_en_dup"])
        assert "error" not in result
        assert result["state"] == "enabled"

    @pytest.mark.asyncio
    async def test_disable_already_disabled(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["pm_dis_dup"])
        ctx = _FakeCommandCtx(manager)

        await cmd_pluginmanager(ctx, ["pluginmanager", "disable", "pm_dis_dup"])
        result = await cmd_pluginmanager(
            ctx, ["pluginmanager", "disable", "pm_dis_dup"]
        )
        assert "error" not in result
        assert result["state"] == "disabled"

    @pytest.mark.asyncio
    async def test_enable_missing_name_arg(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["pm_en_noarg"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginmanager(ctx, ["pluginmanager", "enable"])
        assert "error" in result

    @pytest.mark.asyncio
    async def test_disable_missing_name_arg(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["pm_dis_noarg"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginmanager(ctx, ["pluginmanager", "disable"])
        assert "error" in result


class TestCmdPluginmanagerInfo:
    @pytest.mark.asyncio
    async def test_info_returns_plugin_details(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["pm_info_1"], with_settings=True)
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginmanager(ctx, ["pluginmanager", "info", "pm_info_1"])
        assert result["pluginmanager"] == "info"
        assert result["plugin"]["name"] == "pm_info_1"
        assert result["plugin"]["version"] == "1.0.0"
        assert result["plugin"]["state"] == "enabled"
        assert result["plugin"]["started"] is True
        assert result["plugin"]["has_settings"] is True
        assert "definitions" in result
        assert len(result["definitions"]) == 4

    @pytest.mark.asyncio
    async def test_info_unknown_plugin(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["pm_info_2"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginmanager(
            ctx, ["pluginmanager", "info", "nonexistent"]
        )
        assert "error" in result
        assert "Unknown plugin" in result["error"]

    @pytest.mark.asyncio
    async def test_info_missing_name_arg(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["pm_info_3"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginmanager(ctx, ["pluginmanager", "info"])
        assert "error" in result

    @pytest.mark.asyncio
    async def test_info_plugin_without_settings(self, tmp_path: Path) -> None:
        manager = await _setup_manager(
            tmp_path, ["pm_info_nosettings"], with_settings=False
        )
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginmanager(
            ctx, ["pluginmanager", "info", "pm_info_nosettings"]
        )
        assert result["plugin"]["has_settings"] is False
        assert result["definitions"] == []

    @pytest.mark.asyncio
    async def test_info_disabled_plugin(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["pm_info_dis"])
        ctx = _FakeCommandCtx(manager)

        await cmd_pluginmanager(ctx, ["pluginmanager", "disable", "pm_info_dis"])
        result = await cmd_pluginmanager(
            ctx, ["pluginmanager", "info", "pm_info_dis"]
        )
        assert result["plugin"]["state"] == "disabled"


class TestCmdPluginmanagerInstall:
    @pytest.mark.asyncio
    async def test_install_missing_args(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["pm_inst_1"])
        installer = PluginInstaller(install_dir=tmp_path / "installed")
        ctx = _FakeCommandCtx(manager, installer=installer)

        result = await cmd_pluginmanager(
            ctx, ["pluginmanager", "install", "https://example.com/plugin.zip"]
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_install_no_installer(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["pm_inst_noinst"])
        ctx = _FakeCommandCtx(manager, installer=None)

        result = await cmd_pluginmanager(
            ctx,
            ["pluginmanager", "install", "https://example.com/plugin.zip", "abc123"],
        )
        assert "error" in result
        assert "installer" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_install_bad_sha(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["pm_inst_badsha"])
        installer = PluginInstaller(install_dir=tmp_path / "installed")
        ctx = _FakeCommandCtx(manager, installer=installer)

        zip_data = _build_zip("test_plugin")
        wrong_sha = "0" * 64

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.content = zip_data
            mock_resp.raise_for_status = MagicMock()

            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_resp)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_instance

            result = await cmd_pluginmanager(
                ctx,
                [
                    "pluginmanager",
                    "install",
                    "https://example.com/test.zip",
                    wrong_sha,
                ],
            )
            assert "error" in result
            assert "SHA256" in result["error"]

    @pytest.mark.asyncio
    async def test_install_success(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["pm_inst_ok"])
        installer = PluginInstaller(install_dir=tmp_path / "installed")
        ctx = _FakeCommandCtx(manager, installer=installer)

        zip_data = _build_zip("new_plugin")
        sha = hashlib.sha256(zip_data).hexdigest()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.content = zip_data
            mock_resp.raise_for_status = MagicMock()

            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_resp)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_instance

            result = await cmd_pluginmanager(
                ctx,
                [
                    "pluginmanager",
                    "install",
                    "https://example.com/new_plugin.zip",
                    sha,
                ],
            )
            assert "error" not in result
            assert result["installed"] is True
            assert result["name"] == "new_plugin"
            assert result["restart_required"] is True


class TestCmdPluginmanagerUninstall:
    @pytest.mark.asyncio
    async def test_uninstall_core_rejected(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["pm_uninst_core"])
        installer = PluginInstaller(install_dir=tmp_path / "installed")
        ctx = _FakeCommandCtx(manager, installer=installer)

        result = await cmd_pluginmanager(
            ctx, ["pluginmanager", "uninstall", "pm_uninst_core"]
        )
        assert "error" in result
        assert "Core plugin" in result["error"]

    @pytest.mark.asyncio
    async def test_uninstall_no_installer(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, community_names=["pm_uninst_noinst"])
        ctx = _FakeCommandCtx(manager, installer=None)

        result = await cmd_pluginmanager(
            ctx, ["pluginmanager", "uninstall", "pm_uninst_noinst"]
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_uninstall_not_installed(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, community_names=["pm_uninst_missing"])
        installer = PluginInstaller(install_dir=tmp_path / "installed")
        ctx = _FakeCommandCtx(manager, installer=installer)

        result = await cmd_pluginmanager(
            ctx, ["pluginmanager", "uninstall", "pm_uninst_missing"]
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_uninstall_community_success(self, tmp_path: Path) -> None:
        community_dir = tmp_path / "community_plugins"
        manager = await _setup_manager(tmp_path, community_names=["pm_uninst_ok"])
        installer = PluginInstaller(install_dir=community_dir)
        ctx = _FakeCommandCtx(manager, installer=installer)

        result = await cmd_pluginmanager(
            ctx, ["pluginmanager", "uninstall", "pm_uninst_ok"]
        )
        assert "error" not in result
        assert result["uninstalled"] is True
        assert result["restart_required"] is True

    @pytest.mark.asyncio
    async def test_uninstall_missing_name_arg(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["pm_uninst_noarg"])
        installer = PluginInstaller(install_dir=tmp_path / "installed")
        ctx = _FakeCommandCtx(manager, installer=installer)

        result = await cmd_pluginmanager(ctx, ["pluginmanager", "uninstall"])
        assert "error" in result


class TestCmdPluginmanagerRepository:
    @pytest.mark.asyncio
    async def test_repository_no_repository(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["pm_repo_1"])
        ctx = _FakeCommandCtx(manager, repository=None)

        result = await cmd_pluginmanager(ctx, ["pluginmanager", "repository"])
        assert "error" in result
        assert "repository" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_repository_success(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["pm_repo_ok"])
        repo = PluginRepository(repo_url="https://repo/main.json", cache_ttl_seconds=0)

        class _FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "plugins": [
                        {
                            "name": "remote_plug",
                            "version": "2.0.0",
                            "url": "https://zip/remote",
                        }
                    ]
                }

        class _FakeAsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, url):
                return _FakeResponse()

        ctx = _FakeCommandCtx(manager, repository=repo)

        with patch("httpx.AsyncClient", return_value=_FakeAsyncClient()):
            result = await cmd_pluginmanager(ctx, ["pluginmanager", "repository"])

        assert "error" not in result
        assert result["pluginmanager"] == "repository"
        assert result["count"] >= 1


class TestCmdPluginmanagerInstallrepo:
    @pytest.mark.asyncio
    async def test_installrepo_no_repository(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["pm_ir_norepo"])
        ctx = _FakeCommandCtx(manager, installer=MagicMock(), repository=None)

        result = await cmd_pluginmanager(
            ctx, ["pluginmanager", "installrepo", "some_plugin"]
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_installrepo_no_installer(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["pm_ir_noinst"])
        ctx = _FakeCommandCtx(manager, installer=None, repository=MagicMock())

        result = await cmd_pluginmanager(
            ctx, ["pluginmanager", "installrepo", "some_plugin"]
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_installrepo_core_rejected(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["pm_ir_core"])
        ctx = _FakeCommandCtx(manager, installer=MagicMock(), repository=MagicMock())

        result = await cmd_pluginmanager(
            ctx, ["pluginmanager", "installrepo", "pm_ir_core"]
        )
        assert "error" in result
        assert "Core plugin" in result["error"]

    @pytest.mark.asyncio
    async def test_installrepo_missing_name_arg(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["pm_ir_noarg"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginmanager(ctx, ["pluginmanager", "installrepo"])
        assert "error" in result

    @pytest.mark.asyncio
    async def test_installrepo_plugin_not_found(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["pm_ir_notfound"])
        repo = PluginRepository(repo_url="https://repo/main.json", cache_ttl_seconds=0)
        installer = PluginInstaller(install_dir=tmp_path / "installed")
        ctx = _FakeCommandCtx(manager, installer=installer, repository=repo)

        class _FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"plugins": []}

        class _FakeAsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, url):
                return _FakeResponse()

        with patch("httpx.AsyncClient", return_value=_FakeAsyncClient()):
            result = await cmd_pluginmanager(
                ctx, ["pluginmanager", "installrepo", "nonexistent_plugin"]
            )

        assert "error" in result
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_installrepo_success(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["pm_ir_success"])
        installer = PluginInstaller(install_dir=tmp_path / "installed")
        repo = PluginRepository(repo_url="https://repo/main.json", cache_ttl_seconds=0)
        ctx = _FakeCommandCtx(manager, installer=installer, repository=repo)

        zip_data = _build_zip("remote_plugin")
        sha = hashlib.sha256(zip_data).hexdigest()

        class _FakeResponse:
            def __init__(self, payload=None, content=b""):
                self._payload = payload or {}
                self.content = content

            def raise_for_status(self):
                pass

            def json(self):
                return self._payload

        index_resp = _FakeResponse(
            payload={
                "plugins": [
                    {
                        "name": "remote_plugin",
                        "version": "1.0.0",
                        "url": "https://zip/remote_plugin.zip",
                        "sha256": sha,
                    }
                ]
            }
        )
        zip_resp = _FakeResponse(content=zip_data)

        class _FakeAsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, url):
                if "index.json" in url or "main.json" in url:
                    return index_resp
                return zip_resp

        with patch("httpx.AsyncClient", return_value=_FakeAsyncClient()):
            result = await cmd_pluginmanager(
                ctx, ["pluginmanager", "installrepo", "remote_plugin"]
            )

        assert "error" not in result
        assert result["installed"] is True
        assert result["name"] == "remote_plugin"
        assert result["restart_required"] is True


class TestCmdPluginmanagerEdgeCases:
    @pytest.mark.asyncio
    async def test_missing_args_returns_usage(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["pm_edge_1"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginmanager(ctx, ["pluginmanager"])
        assert "error" in result

    @pytest.mark.asyncio
    async def test_unknown_action(self, tmp_path: Path) -> None:
        manager = await _setup_manager(tmp_path, ["pm_edge_2"])
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginmanager(ctx, ["pluginmanager", "frobnicate"])
        assert "error" in result

    @pytest.mark.asyncio
    async def test_no_plugin_manager_returns_error(self, tmp_path: Path) -> None:
        ctx = _FakeCommandCtx(None)  # type: ignore[arg-type]

        result = await cmd_pluginmanager(ctx, ["pluginmanager", "list"])
        assert "error" in result
