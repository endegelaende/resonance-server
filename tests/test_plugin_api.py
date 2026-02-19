from __future__ import annotations

import hashlib
import io
import shutil
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from resonance.core.events import EventBus
from resonance.core.library import MusicLibrary
from resonance.core.library_db import LibraryDb
from resonance.player.registry import PlayerRegistry
from resonance.plugin_installer import PluginInstaller
from resonance.plugin_manager import PluginManager
from resonance.plugin_repository import PluginRepository, RepositoryEntry
from resonance.web.server import WebServer


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
        f'[plugin]\nname = "{name}"\nversion = "{version}"\n{category_line}\n{icon_line}\n{settings}\n',
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


@pytest.fixture
async def plugin_client(tmp_path: Path):
    core_dir = tmp_path / "core_plugins"
    community_dir = tmp_path / "community_plugins"

    for name in ["api_core", "api_core_settings", "api_community"]:
        shutil.rmtree(Path("data/plugins") / name, ignore_errors=True)

    _create_plugin(core_dir, "api_core", with_settings=False)
    _create_plugin(core_dir, "api_core_settings", with_settings=True, category="tools", icon="wrench")
    _create_plugin(community_dir, "api_community", with_settings=True)

    manager = PluginManager(
        core_plugins_dir=core_dir,
        community_plugins_dir=community_dir,
        state_file=tmp_path / "plugin_states.json",
    )
    await manager.discover()
    await manager.load_all()
    await manager.start_all(
        event_bus=EventBus(),
        music_library=MagicMock(),
        player_registry=MagicMock(),
    )

    installer = PluginInstaller(install_dir=community_dir)
    repository = PluginRepository(repo_url="https://example.invalid/index.json")

    db = LibraryDb(":memory:")
    await db.open()
    await db.ensure_schema()
    library = MusicLibrary(db=db, music_root=None)
    await library.initialize()

    server = WebServer(
        player_registry=PlayerRegistry(),
        music_library=library,
        plugin_manager=manager,
        plugin_installer=installer,
        plugin_repository=repository,
    )
    transport = ASGITransport(app=server.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, manager, community_dir, installer, repository
    await db.close()


# =============================================================================
# GET /api/plugins — List plugins
# =============================================================================


class TestListPlugins:
    @pytest.mark.asyncio
    async def test_list_plugins_returns_all(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        response = await client.get("/api/plugins")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 3
        names = {item["name"] for item in data["plugins"]}
        assert {"api_core", "api_core_settings", "api_community"} <= names

    @pytest.mark.asyncio
    async def test_list_plugins_has_settings_flag(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        response = await client.get("/api/plugins")
        data = response.json()
        by_name = {p["name"]: p for p in data["plugins"]}
        assert by_name["api_core"]["has_settings"] is False
        assert by_name["api_core_settings"]["has_settings"] is True
        assert by_name["api_community"]["has_settings"] is True

    @pytest.mark.asyncio
    async def test_list_plugins_has_type_field(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        response = await client.get("/api/plugins")
        data = response.json()
        by_name = {p["name"]: p for p in data["plugins"]}
        assert by_name["api_core"]["type"] == "core"
        assert by_name["api_core_settings"]["type"] == "core"
        assert by_name["api_community"]["type"] == "community"

    @pytest.mark.asyncio
    async def test_list_plugins_can_uninstall_flag(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        response = await client.get("/api/plugins")
        data = response.json()
        by_name = {p["name"]: p for p in data["plugins"]}
        assert by_name["api_core"]["can_uninstall"] is False
        assert by_name["api_community"]["can_uninstall"] is True

    @pytest.mark.asyncio
    async def test_list_plugins_has_state_field(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        response = await client.get("/api/plugins")
        data = response.json()
        for plugin in data["plugins"]:
            assert "state" in plugin
            assert plugin["state"] in ("enabled", "disabled")

    @pytest.mark.asyncio
    async def test_list_plugins_has_started_field(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        response = await client.get("/api/plugins")
        data = response.json()
        for plugin in data["plugins"]:
            assert "started" in plugin
            assert isinstance(plugin["started"], bool)

    @pytest.mark.asyncio
    async def test_list_plugins_has_restart_required(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        response = await client.get("/api/plugins")
        data = response.json()
        assert "restart_required" in data
        assert data["restart_required"] is False

    @pytest.mark.asyncio
    async def test_list_plugins_restart_required_after_disable(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        await client.post("/api/plugins/api_core/disable")
        response = await client.get("/api/plugins")
        data = response.json()
        assert data["restart_required"] is True

    @pytest.mark.asyncio
    async def test_list_plugins_response_format(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        response = await client.get("/api/plugins")
        data = response.json()
        expected_keys = {
            "name", "version", "description", "author", "category", "icon",
            "state", "started", "type", "has_settings", "can_uninstall",
        }
        for plugin in data["plugins"]:
            assert expected_keys <= set(plugin.keys())

    @pytest.mark.asyncio
    async def test_list_plugins_state_reflects_disable(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        await client.post("/api/plugins/api_community/disable")
        response = await client.get("/api/plugins")
        data = response.json()
        by_name = {p["name"]: p for p in data["plugins"]}
        assert by_name["api_community"]["state"] == "disabled"
        assert by_name["api_core"]["state"] == "enabled"


# =============================================================================
# GET /api/plugins/{name}/settings — Get plugin settings
# =============================================================================


class TestGetPluginSettings:
    @pytest.mark.asyncio
    async def test_get_settings_success(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        response = await client.get("/api/plugins/api_core_settings/settings")
        assert response.status_code == 200
        data = response.json()
        assert data["plugin_name"] == "api_core_settings"
        assert "values" in data
        assert "definitions" in data

    @pytest.mark.asyncio
    async def test_get_settings_default_values(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        response = await client.get("/api/plugins/api_core_settings/settings")
        data = response.json()
        assert data["values"]["enabled"] is True
        assert data["values"]["volume"] == 50
        assert data["values"]["service"] == "spotify"

    @pytest.mark.asyncio
    async def test_get_settings_has_definitions(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        response = await client.get("/api/plugins/api_core_settings/settings")
        data = response.json()
        assert len(data["definitions"]) == 4
        keys = [d["key"] for d in data["definitions"]]
        assert "enabled" in keys
        assert "volume" in keys
        assert "secret_key" in keys
        assert "service" in keys

    @pytest.mark.asyncio
    async def test_get_settings_definition_types(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        response = await client.get("/api/plugins/api_core_settings/settings")
        data = response.json()
        defs_by_key = {d["key"]: d for d in data["definitions"]}
        assert defs_by_key["enabled"]["type"] == "bool"
        assert defs_by_key["volume"]["type"] == "int"
        assert defs_by_key["volume"]["min"] == 0
        assert defs_by_key["volume"]["max"] == 100
        assert defs_by_key["secret_key"]["type"] == "string"
        assert defs_by_key["secret_key"].get("secret") is True
        assert defs_by_key["service"]["type"] == "select"
        assert defs_by_key["service"]["options"] == ["spotify", "tidal", "qobuz"]

    @pytest.mark.asyncio
    async def test_get_settings_404_unknown_plugin(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        response = await client.get("/api/plugins/nonexistent_plugin/settings")
        assert response.status_code == 404
        data = response.json()
        assert "detail" in data
        assert "Unknown plugin" in data["detail"]

    @pytest.mark.asyncio
    async def test_get_settings_plugin_without_settings(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        response = await client.get("/api/plugins/api_core/settings")
        assert response.status_code == 200
        data = response.json()
        assert data["values"] == {}
        assert data["definitions"] == []

    @pytest.mark.asyncio
    async def test_get_settings_masks_secrets(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        # First set a secret
        await client.put(
            "/api/plugins/api_core_settings/settings",
            json={"secret_key": "mysupersecretvalue"},
        )
        # Then get — secret should be masked
        response = await client.get("/api/plugins/api_core_settings/settings")
        data = response.json()
        assert data["values"]["secret_key"] != "mysupersecretvalue"
        assert "****" in data["values"]["secret_key"] or len(data["values"]["secret_key"]) > 0


# =============================================================================
# PUT /api/plugins/{name}/settings — Update plugin settings
# =============================================================================


class TestUpdatePluginSettings:
    @pytest.mark.asyncio
    async def test_update_single_setting(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        response = await client.put(
            "/api/plugins/api_core_settings/settings",
            json={"volume": 75},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["values"]["volume"] == 75
        assert "volume" in data["updated"]

    @pytest.mark.asyncio
    async def test_update_multiple_settings(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        response = await client.put(
            "/api/plugins/api_core_settings/settings",
            json={"volume": 80, "enabled": False, "service": "tidal"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["values"]["volume"] == 80
        assert data["values"]["enabled"] is False
        assert data["values"]["service"] == "tidal"
        assert sorted(data["updated"]) == ["enabled", "service", "volume"]

    @pytest.mark.asyncio
    async def test_update_persists(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        await client.put(
            "/api/plugins/api_core_settings/settings",
            json={"volume": 99},
        )
        response = await client.get("/api/plugins/api_core_settings/settings")
        data = response.json()
        assert data["values"]["volume"] == 99

    @pytest.mark.asyncio
    async def test_update_no_change_returns_empty_updated(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        response = await client.put(
            "/api/plugins/api_core_settings/settings",
            json={"volume": 50},  # default value
        )
        assert response.status_code == 200
        data = response.json()
        assert data["updated"] == []

    @pytest.mark.asyncio
    async def test_update_validation_error_int_out_of_range(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        response = await client.put(
            "/api/plugins/api_core_settings/settings",
            json={"volume": 999},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_update_validation_error_int_below_min(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        response = await client.put(
            "/api/plugins/api_core_settings/settings",
            json={"volume": -1},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_update_validation_error_select_invalid(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        response = await client.put(
            "/api/plugins/api_core_settings/settings",
            json={"service": "deezer"},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_update_unknown_key(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        response = await client.put(
            "/api/plugins/api_core_settings/settings",
            json={"nonexistent_setting": "value"},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_update_404_unknown_plugin(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        response = await client.put(
            "/api/plugins/nonexistent_plugin/settings",
            json={"volume": 50},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_update_bad_body(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        response = await client.put(
            "/api/plugins/api_core_settings/settings",
            content=b'"not an object"',
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_update_masks_secrets_in_response(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        response = await client.put(
            "/api/plugins/api_core_settings/settings",
            json={"secret_key": "my_super_secret_value"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["values"]["secret_key"] != "my_super_secret_value"

    @pytest.mark.asyncio
    async def test_update_response_includes_definitions(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        response = await client.put(
            "/api/plugins/api_core_settings/settings",
            json={"volume": 60},
        )
        assert response.status_code == 200
        data = response.json()
        assert "definitions" in data
        assert len(data["definitions"]) == 4

    @pytest.mark.asyncio
    async def test_update_bool_from_json(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        response = await client.put(
            "/api/plugins/api_core_settings/settings",
            json={"enabled": False},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["values"]["enabled"] is False

        response2 = await client.put(
            "/api/plugins/api_core_settings/settings",
            json={"enabled": True},
        )
        assert response2.status_code == 200
        assert response2.json()["values"]["enabled"] is True


# =============================================================================
# POST /api/plugins/{name}/enable — Enable plugin
# =============================================================================


class TestEnablePlugin:
    @pytest.mark.asyncio
    async def test_enable_success(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        # First disable
        await client.post("/api/plugins/api_core_settings/disable")
        # Then enable
        response = await client.post("/api/plugins/api_core_settings/enable")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "api_core_settings"
        assert data["state"] == "enabled"
        assert "restart_required" in data

    @pytest.mark.asyncio
    async def test_enable_already_enabled(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        response = await client.post("/api/plugins/api_core_settings/enable")
        assert response.status_code == 200
        data = response.json()
        assert data["state"] == "enabled"

    @pytest.mark.asyncio
    async def test_enable_unknown_plugin_404(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        response = await client.post("/api/plugins/nonexistent_plugin/enable")
        assert response.status_code == 404


# =============================================================================
# POST /api/plugins/{name}/disable — Disable plugin
# =============================================================================


class TestDisablePlugin:
    @pytest.mark.asyncio
    async def test_disable_success(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        response = await client.post("/api/plugins/api_core_settings/disable")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "api_core_settings"
        assert data["state"] == "disabled"
        assert data["restart_required"] is True

    @pytest.mark.asyncio
    async def test_disable_already_disabled(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        await client.post("/api/plugins/api_core_settings/disable")
        response = await client.post("/api/plugins/api_core_settings/disable")
        assert response.status_code == 200
        data = response.json()
        assert data["state"] == "disabled"

    @pytest.mark.asyncio
    async def test_disable_unknown_plugin_404(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        response = await client.post("/api/plugins/nonexistent_plugin/disable")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_disable_reflects_in_listing(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        await client.post("/api/plugins/api_community/disable")
        response = await client.get("/api/plugins")
        data = response.json()
        by_name = {p["name"]: p for p in data["plugins"]}
        assert by_name["api_community"]["state"] == "disabled"


# =============================================================================
# POST /api/plugins/{name}/uninstall — Uninstall plugin
# =============================================================================


class TestUninstallPlugin:
    @pytest.mark.asyncio
    async def test_uninstall_core_rejected(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        response = await client.post("/api/plugins/api_core/uninstall")
        assert response.status_code == 400
        data = response.json()
        assert "Core plugin cannot be uninstalled" in data["detail"]

    @pytest.mark.asyncio
    async def test_uninstall_core_settings_rejected(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        response = await client.post("/api/plugins/api_core_settings/uninstall")
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_uninstall_community_success(self, plugin_client) -> None:
        client, _manager, community_dir, _, _ = plugin_client
        response = await client.post("/api/plugins/api_community/uninstall")
        assert response.status_code == 200
        data = response.json()
        assert data["uninstalled"] is True
        assert data["name"] == "api_community"
        assert data["restart_required"] is True

    @pytest.mark.asyncio
    async def test_uninstall_nonexistent_plugin(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        response = await client.post("/api/plugins/nonexistent_plugin/uninstall")
        # Should be 404 because installer.uninstall returns False
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_uninstall_already_uninstalled(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        # First uninstall
        await client.post("/api/plugins/api_community/uninstall")
        # Second attempt — already gone
        response = await client.post("/api/plugins/api_community/uninstall")
        assert response.status_code in (400, 404)


# =============================================================================
# POST /api/plugins/install — Install plugin from URL
# =============================================================================


class TestInstallPlugin:
    @pytest.mark.asyncio
    async def test_install_missing_fields(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        response = await client.post(
            "/api/plugins/install",
            json={"url": "https://example.com/plugin.zip"},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_install_missing_url(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        response = await client.post(
            "/api/plugins/install",
            json={"sha256": "abc123"},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_install_bad_body(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        response = await client.post(
            "/api/plugins/install",
            content=b'"not an object"',
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_install_bad_sha256(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        zip_data = _build_zip("install_test")
        wrong_sha = "0" * 64

        with patch("resonance.web.routes.api.httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.content = zip_data
            mock_resp.raise_for_status = MagicMock()

            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_resp)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_instance

            response = await client.post(
                "/api/plugins/install",
                json={"url": "https://example.com/test.zip", "sha256": wrong_sha},
            )
            assert response.status_code == 400
            data = response.json()
            assert "SHA256" in data["detail"]

    @pytest.mark.asyncio
    async def test_install_success(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        zip_data = _build_zip("new_test_plugin")
        sha = hashlib.sha256(zip_data).hexdigest()

        with patch("resonance.web.routes.api.httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.content = zip_data
            mock_resp.raise_for_status = MagicMock()

            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_resp)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_instance

            response = await client.post(
                "/api/plugins/install",
                json={"url": "https://example.com/new_test_plugin.zip", "sha256": sha},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["installed"] is True
            assert data["name"] == "new_test_plugin"
            assert data["restart_required"] is True


# =============================================================================
# GET /api/plugins/repository — Get repository plugins
# =============================================================================


class TestGetRepository:
    @pytest.mark.asyncio
    async def test_repository_success(self, plugin_client) -> None:
        client, _manager, _, _, repository = plugin_client

        class _FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "plugins": [
                        {
                            "name": "repo_plugin",
                            "version": "2.0.0",
                            "url": "https://zip/repo_plugin.zip",
                            "description": "A repo plugin",
                            "author": "Test",
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

        with patch("httpx.AsyncClient", return_value=_FakeAsyncClient()):
            response = await client.get("/api/plugins/repository")

        assert response.status_code == 200
        data = response.json()
        assert "count" in data
        assert "plugins" in data
        assert data["count"] >= 1
        names = [p["name"] for p in data["plugins"]]
        assert "repo_plugin" in names

    @pytest.mark.asyncio
    async def test_repository_includes_installed_info(self, plugin_client) -> None:
        client, _manager, _, _, repository = plugin_client

        class _FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "plugins": [
                        {
                            "name": "api_core",
                            "version": "9.0.0",
                            "url": "https://zip/core.zip",
                        },
                        {
                            "name": "brand_new",
                            "version": "1.0.0",
                            "url": "https://zip/new.zip",
                        },
                    ]
                }

        class _FakeAsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, url):
                return _FakeResponse()

        with patch("httpx.AsyncClient", return_value=_FakeAsyncClient()):
            response = await client.get("/api/plugins/repository")

        assert response.status_code == 200
        data = response.json()
        by_name = {p["name"]: p for p in data["plugins"]}

        # api_core is a core plugin — should not be installable or updatable
        if "api_core" in by_name:
            assert by_name["api_core"]["is_core"] is True
            assert by_name["api_core"]["can_install"] is False

        # brand_new is not installed — should be installable
        if "brand_new" in by_name:
            assert by_name["brand_new"]["can_install"] is True
            assert by_name["brand_new"]["installed_version"] is None

    @pytest.mark.asyncio
    async def test_repository_empty(self, plugin_client) -> None:
        client, _manager, _, _, repository = plugin_client

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
            response = await client.get("/api/plugins/repository")

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 0
        assert data["plugins"] == []

    @pytest.mark.asyncio
    async def test_repository_force_refresh(self, plugin_client) -> None:
        client, _manager, _, _, repository = plugin_client

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
            response = await client.get("/api/plugins/repository?force_refresh=true")

        assert response.status_code == 200


# =============================================================================
# POST /api/plugins/install-from-repo — Install from repository
# =============================================================================


class TestInstallFromRepository:
    @pytest.mark.asyncio
    async def test_install_from_repo_missing_name(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        response = await client.post(
            "/api/plugins/install-from-repo",
            json={},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_install_from_repo_empty_name(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        response = await client.post(
            "/api/plugins/install-from-repo",
            json={"name": ""},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_install_from_repo_bad_body(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        response = await client.post(
            "/api/plugins/install-from-repo",
            content=b'"not an object"',
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_install_from_repo_core_rejected(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client
        response = await client.post(
            "/api/plugins/install-from-repo",
            json={"name": "api_core"},
        )
        assert response.status_code == 400
        data = response.json()
        assert "Core plugin" in data["detail"]

    @pytest.mark.asyncio
    async def test_install_from_repo_not_found(self, plugin_client) -> None:
        client, _manager, _, _, repository = plugin_client

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
            response = await client.post(
                "/api/plugins/install-from-repo",
                json={"name": "nonexistent_plugin"},
            )

        assert response.status_code == 404
        data = response.json()
        assert "not found" in data["detail"].lower()

    @pytest.mark.asyncio
    async def test_install_from_repo_success(self, plugin_client) -> None:
        client, _manager, _, _, repository = plugin_client

        zip_data = _build_zip("repo_install_test")
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
                        "name": "repo_install_test",
                        "version": "1.0.0",
                        "url": "https://zip/repo_install_test.zip",
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
                if "index.json" in url:
                    return index_resp
                return zip_resp

        with patch("httpx.AsyncClient", return_value=_FakeAsyncClient()):
            response = await client.post(
                "/api/plugins/install-from-repo",
                json={"name": "repo_install_test"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["installed"] is True
        assert data["name"] == "repo_install_test"
        assert data["version"] == "1.0.0"
        assert data["restart_required"] is True

    @pytest.mark.asyncio
    async def test_install_from_repo_bad_sha(self, plugin_client) -> None:
        client, _manager, _, _, repository = plugin_client

        zip_data = _build_zip("repo_bad_sha")
        wrong_sha = "0" * 64  # intentionally wrong

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
                        "name": "repo_bad_sha",
                        "version": "1.0.0",
                        "url": "https://zip/repo_bad_sha.zip",
                        "sha256": wrong_sha,
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
                if "index.json" in url:
                    return index_resp
                return zip_resp

        with patch("httpx.AsyncClient", return_value=_FakeAsyncClient()):
            response = await client.post(
                "/api/plugins/install-from-repo",
                json={"name": "repo_bad_sha"},
            )

        assert response.status_code == 400
        data = response.json()
        assert "SHA256" in data["detail"]


# =============================================================================
# Integration / cross-endpoint tests
# =============================================================================


class TestPluginApiIntegration:
    @pytest.mark.asyncio
    async def test_enable_disable_roundtrip(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client

        # Disable
        resp1 = await client.post("/api/plugins/api_core_settings/disable")
        assert resp1.status_code == 200
        assert resp1.json()["state"] == "disabled"

        # Verify in listing
        resp2 = await client.get("/api/plugins")
        by_name = {p["name"]: p for p in resp2.json()["plugins"]}
        assert by_name["api_core_settings"]["state"] == "disabled"

        # Re-enable
        resp3 = await client.post("/api/plugins/api_core_settings/enable")
        assert resp3.status_code == 200
        assert resp3.json()["state"] == "enabled"

        # Verify in listing
        resp4 = await client.get("/api/plugins")
        by_name2 = {p["name"]: p for p in resp4.json()["plugins"]}
        assert by_name2["api_core_settings"]["state"] == "enabled"

    @pytest.mark.asyncio
    async def test_settings_update_roundtrip(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client

        # Update
        resp1 = await client.put(
            "/api/plugins/api_core_settings/settings",
            json={"volume": 42, "service": "qobuz"},
        )
        assert resp1.status_code == 200

        # Verify
        resp2 = await client.get("/api/plugins/api_core_settings/settings")
        data = resp2.json()
        assert data["values"]["volume"] == 42
        assert data["values"]["service"] == "qobuz"
        assert data["values"]["enabled"] is True  # unchanged

    @pytest.mark.asyncio
    async def test_community_uninstall_and_reinstall(self, plugin_client) -> None:
        client, _manager, community_dir, _, _ = plugin_client

        # Uninstall community plugin
        resp1 = await client.post("/api/plugins/api_community/uninstall")
        assert resp1.status_code == 200
        assert resp1.json()["uninstalled"] is True

        # Now reinstall via zip
        zip_data = _build_zip("api_community")
        sha = hashlib.sha256(zip_data).hexdigest()

        with patch("resonance.web.routes.api.httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.content = zip_data
            mock_resp.raise_for_status = MagicMock()

            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_resp)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_instance

            resp2 = await client.post(
                "/api/plugins/install",
                json={"url": "https://example.com/api_community.zip", "sha256": sha},
            )
            assert resp2.status_code == 200
            assert resp2.json()["installed"] is True

    @pytest.mark.asyncio
    async def test_multiple_settings_operations(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client

        # Set multiple values
        await client.put(
            "/api/plugins/api_core_settings/settings",
            json={"volume": 10, "enabled": False},
        )

        # Change one
        resp = await client.put(
            "/api/plugins/api_core_settings/settings",
            json={"volume": 90},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["values"]["volume"] == 90
        # enabled should still be False (from previous update)
        assert data["values"]["enabled"] is False

    @pytest.mark.asyncio
    async def test_restart_required_persists_across_operations(self, plugin_client) -> None:
        client, _manager, _, _, _ = plugin_client

        # Disable → restart required
        await client.post("/api/plugins/api_community/disable")

        # List should show restart_required
        resp = await client.get("/api/plugins")
        assert resp.json()["restart_required"] is True

        # Enable doesn't clear restart_required (still needs restart)
        await client.post("/api/plugins/api_community/enable")
        resp2 = await client.get("/api/plugins")
        # restart_required stays True until server restart
        assert resp2.json()["restart_required"] is True
