from __future__ import annotations

import hashlib
import io
import shutil
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from resonance.core.events import EventBus
from resonance.plugin_installer import PluginInstaller
from resonance.plugin_manager import PluginManager


def _build_zip(plugin_name: str, *, with_prefix: bool = True, version: str = "1.0.0") -> bytes:
    buf = io.BytesIO()
    prefix = f"{plugin_name}/" if with_prefix else ""
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            f"{prefix}plugin.toml",
            f'[plugin]\nname = "{plugin_name}"\nversion = "{version}"\n',
        )
        zf.writestr(
            f"{prefix}__init__.py",
            "async def setup(ctx):\n    pass\n",
        )
    return buf.getvalue()


def _build_zip_with_subdir(plugin_name: str) -> bytes:
    """Build a zip with nested subdirectories to test prefix stripping."""
    buf = io.BytesIO()
    prefix = f"{plugin_name}/"
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            f"{prefix}plugin.toml",
            f'[plugin]\nname = "{plugin_name}"\nversion = "1.0.0"\n',
        )
        zf.writestr(
            f"{prefix}__init__.py",
            "async def setup(ctx):\n    pass\n",
        )
        zf.writestr(
            f"{prefix}lib/helper.py",
            "def helper():\n    return 42\n",
        )
        zf.writestr(
            f"{prefix}templates/index.html",
            "<html></html>",
        )
    return buf.getvalue()


def _build_zip_no_toml() -> bytes:
    """Build a zip without plugin.toml."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("demo/__init__.py", "async def setup(ctx):\n    pass\n")
    return buf.getvalue()


def _build_zip_missing_name() -> bytes:
    """Build a zip with plugin.toml but missing [plugin].name."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "demo/plugin.toml",
            '[plugin]\nversion = "1.0.0"\n',
        )
        zf.writestr(
            "demo/__init__.py",
            "async def setup(ctx):\n    pass\n",
        )
    return buf.getvalue()


def _build_zip_empty_name() -> bytes:
    """Build a zip with plugin.toml with empty name."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "demo/plugin.toml",
            '[plugin]\nname = ""\nversion = "1.0.0"\n',
        )
        zf.writestr(
            "demo/__init__.py",
            "async def setup(ctx):\n    pass\n",
        )
    return buf.getvalue()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _create_plugin_dir(root: Path, name: str, *, version: str = "1.0.0", plugin_type: str = "core") -> None:
    plugin_dir = root / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "__init__.py").write_text(
        "async def setup(ctx):\n    pass\n", encoding="utf-8"
    )
    (plugin_dir / "plugin.toml").write_text(
        f'[plugin]\nname = "{name}"\nversion = "{version}"\n',
        encoding="utf-8",
    )


# =============================================================================
# TestPluginInstaller — comprehensive
# =============================================================================


class TestPluginInstaller:
    def test_install_valid_zip(self, tmp_path: Path) -> None:
        installer = PluginInstaller(install_dir=tmp_path / "installed")
        zip_data = _build_zip("demo")
        sha = _sha256(zip_data)

        plugin_name = installer.install_from_zip(zip_data, sha)
        assert plugin_name == "demo"
        assert (tmp_path / "installed" / "demo" / "plugin.toml").is_file()
        assert installer.is_installed("demo") is True

    def test_sha256_mismatch(self, tmp_path: Path) -> None:
        installer = PluginInstaller(install_dir=tmp_path / "installed")
        zip_data = _build_zip("demo")
        with pytest.raises(ValueError, match="SHA256 mismatch"):
            installer.install_from_zip(zip_data, "deadbeef")

    def test_sha256_case_insensitive(self, tmp_path: Path) -> None:
        """SHA256 comparison should be case-insensitive."""
        installer = PluginInstaller(install_dir=tmp_path / "installed")
        zip_data = _build_zip("demo")
        sha_lower = _sha256(zip_data)
        sha_upper = sha_lower.upper()

        # Should not raise
        plugin_name = installer.install_from_zip(zip_data, sha_upper)
        assert plugin_name == "demo"

    def test_missing_plugin_toml(self, tmp_path: Path) -> None:
        installer = PluginInstaller(install_dir=tmp_path / "installed")
        zip_data = _build_zip_no_toml()
        sha = _sha256(zip_data)
        with pytest.raises(ValueError, match="plugin.toml"):
            installer.install_from_zip(zip_data, sha)

    def test_missing_name_in_toml(self, tmp_path: Path) -> None:
        installer = PluginInstaller(install_dir=tmp_path / "installed")
        zip_data = _build_zip_missing_name()
        sha = _sha256(zip_data)
        with pytest.raises(ValueError, match="name"):
            installer.install_from_zip(zip_data, sha)

    def test_empty_name_in_toml(self, tmp_path: Path) -> None:
        installer = PluginInstaller(install_dir=tmp_path / "installed")
        zip_data = _build_zip_empty_name()
        sha = _sha256(zip_data)
        with pytest.raises(ValueError, match="name"):
            installer.install_from_zip(zip_data, sha)

    def test_bad_zip_file(self, tmp_path: Path) -> None:
        installer = PluginInstaller(install_dir=tmp_path / "installed")
        bad_data = b"this is not a zip file at all"
        sha = _sha256(bad_data)
        with pytest.raises(ValueError, match="[Ii]nvalid zip"):
            installer.install_from_zip(bad_data, sha)

    def test_uninstall_existing_and_missing(self, tmp_path: Path) -> None:
        installer = PluginInstaller(install_dir=tmp_path / "installed")
        zip_data = _build_zip("demo")
        sha = _sha256(zip_data)
        installer.install_from_zip(zip_data, sha)
        assert installer.uninstall("demo") is True
        assert installer.uninstall("demo") is False

    def test_uninstall_nonexistent(self, tmp_path: Path) -> None:
        installer = PluginInstaller(install_dir=tmp_path / "installed")
        assert installer.uninstall("never_existed") is False

    def test_list_installed(self, tmp_path: Path) -> None:
        installer = PluginInstaller(install_dir=tmp_path / "installed")
        for name in ("a", "b"):
            zip_data = _build_zip(name, with_prefix=False)
            sha = _sha256(zip_data)
            installer.install_from_zip(zip_data, sha)
        assert installer.list_installed() == ["a", "b"]

    def test_list_installed_empty(self, tmp_path: Path) -> None:
        installer = PluginInstaller(install_dir=tmp_path / "installed")
        assert installer.list_installed() == []

    def test_list_installed_nonexistent_dir(self, tmp_path: Path) -> None:
        installer = PluginInstaller(install_dir=tmp_path / "does_not_exist")
        assert installer.list_installed() == []

    def test_prefix_stripping(self, tmp_path: Path) -> None:
        """Files inside a zip prefix directory should be stripped to the plugin root."""
        installer = PluginInstaller(install_dir=tmp_path / "installed")
        zip_data = _build_zip_with_subdir("demo")
        sha = _sha256(zip_data)

        plugin_name = installer.install_from_zip(zip_data, sha)
        assert plugin_name == "demo"
        plugin_dir = tmp_path / "installed" / "demo"
        assert (plugin_dir / "plugin.toml").is_file()
        assert (plugin_dir / "__init__.py").is_file()
        assert (plugin_dir / "lib" / "helper.py").is_file()
        assert (plugin_dir / "templates" / "index.html").is_file()

    def test_install_without_prefix(self, tmp_path: Path) -> None:
        """Flat zip (no prefix directory) should still work."""
        installer = PluginInstaller(install_dir=tmp_path / "installed")
        zip_data = _build_zip("flat_demo", with_prefix=False)
        sha = _sha256(zip_data)

        plugin_name = installer.install_from_zip(zip_data, sha)
        assert plugin_name == "flat_demo"
        assert (tmp_path / "installed" / "flat_demo" / "plugin.toml").is_file()
        assert (tmp_path / "installed" / "flat_demo" / "__init__.py").is_file()

    def test_overwrite_existing(self, tmp_path: Path) -> None:
        """Installing over an existing plugin should replace it."""
        installer = PluginInstaller(install_dir=tmp_path / "installed")

        # Install v1
        zip_v1 = _build_zip("overwrite_demo", version="1.0.0")
        sha_v1 = _sha256(zip_v1)
        installer.install_from_zip(zip_v1, sha_v1)
        assert installer.is_installed("overwrite_demo")

        # Install v2 over it
        zip_v2 = _build_zip("overwrite_demo", version="2.0.0")
        sha_v2 = _sha256(zip_v2)
        plugin_name = installer.install_from_zip(zip_v2, sha_v2)
        assert plugin_name == "overwrite_demo"
        assert installer.is_installed("overwrite_demo")

        # Verify it's the new version by reading toml
        toml_content = (tmp_path / "installed" / "overwrite_demo" / "plugin.toml").read_text(encoding="utf-8")
        assert "2.0.0" in toml_content

    def test_is_installed_true(self, tmp_path: Path) -> None:
        installer = PluginInstaller(install_dir=tmp_path / "installed")
        zip_data = _build_zip("check_me")
        sha = _sha256(zip_data)
        installer.install_from_zip(zip_data, sha)
        assert installer.is_installed("check_me") is True

    def test_is_installed_false(self, tmp_path: Path) -> None:
        installer = PluginInstaller(install_dir=tmp_path / "installed")
        assert installer.is_installed("nonexistent") is False

    def test_is_installed_dir_without_toml(self, tmp_path: Path) -> None:
        """A directory without plugin.toml should not count as installed."""
        installer = PluginInstaller(install_dir=tmp_path / "installed")
        (tmp_path / "installed" / "fake").mkdir(parents=True)
        (tmp_path / "installed" / "fake" / "__init__.py").write_text("pass", encoding="utf-8")
        assert installer.is_installed("fake") is False

    def test_install_creates_install_dir(self, tmp_path: Path) -> None:
        """install_dir should be created automatically if it doesn't exist."""
        install_dir = tmp_path / "new" / "deep" / "dir"
        installer = PluginInstaller(install_dir=install_dir)
        zip_data = _build_zip("auto_dir")
        sha = _sha256(zip_data)

        plugin_name = installer.install_from_zip(zip_data, sha)
        assert plugin_name == "auto_dir"
        assert (install_dir / "auto_dir" / "plugin.toml").is_file()

    def test_list_installed_ignores_non_plugin_dirs(self, tmp_path: Path) -> None:
        """Directories without plugin.toml should not appear in list_installed."""
        install_dir = tmp_path / "installed"
        install_dir.mkdir()

        # Create a real plugin
        zip_data = _build_zip("real_plugin")
        sha = _sha256(zip_data)
        installer = PluginInstaller(install_dir=install_dir)
        installer.install_from_zip(zip_data, sha)

        # Create a fake directory without plugin.toml
        (install_dir / "not_a_plugin").mkdir()
        (install_dir / "not_a_plugin" / "random.txt").write_text("hi", encoding="utf-8")

        # Create a file (not a directory)
        (install_dir / "some_file.txt").write_text("hi", encoding="utf-8")

        result = installer.list_installed()
        assert result == ["real_plugin"]

    def test_list_installed_sorted(self, tmp_path: Path) -> None:
        """Installed plugins should be returned in sorted order."""
        installer = PluginInstaller(install_dir=tmp_path / "installed")
        for name in ("charlie", "alpha", "bravo"):
            zip_data = _build_zip(name, with_prefix=False)
            sha = _sha256(zip_data)
            installer.install_from_zip(zip_data, sha)
        assert installer.list_installed() == ["alpha", "bravo", "charlie"]

    def test_zip_slip_detected(self, tmp_path: Path) -> None:
        """Zip entries with path traversal should be rejected."""
        installer = PluginInstaller(install_dir=tmp_path / "installed")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "demo/plugin.toml",
                '[plugin]\nname = "demo"\nversion = "1.0.0"\n',
            )
            zf.writestr(
                "demo/__init__.py",
                "async def setup(ctx):\n    pass\n",
            )
            zf.writestr(
                "demo/../../etc/malicious.txt",
                "pwned",
            )
        zip_data = buf.getvalue()
        sha = _sha256(zip_data)

        with pytest.raises(ValueError, match="[Uu]nsafe zip"):
            installer.install_from_zip(zip_data, sha)

    def test_default_install_dir(self) -> None:
        """Default install_dir should be data/installed_plugins."""
        installer = PluginInstaller()
        assert installer.install_dir == Path("data/installed_plugins")


# =============================================================================
# TestDualDirectoryDiscovery — comprehensive
# =============================================================================


class TestDualDirectoryDiscovery:
    @pytest.mark.asyncio
    async def test_core_wins_on_name_collision(self, tmp_path: Path) -> None:
        core_dir = tmp_path / "core"
        community_dir = tmp_path / "community"

        for root, version in ((core_dir, "1.0.0"), (community_dir, "9.9.9")):
            _create_plugin_dir(root, "dup", version=version)

        manager = PluginManager(
            core_plugins_dir=core_dir,
            community_plugins_dir=community_dir,
            state_file=tmp_path / "states.json",
        )
        manifests = await manager.discover()
        assert len(manifests) == 1
        assert manifests[0].plugin_type == "core"
        assert manifests[0].version == "1.0.0"

    @pytest.mark.asyncio
    async def test_community_only(self, tmp_path: Path) -> None:
        """When core dir is empty, only community plugins should be discovered."""
        core_dir = tmp_path / "core"
        core_dir.mkdir(parents=True)
        community_dir = tmp_path / "community"
        _create_plugin_dir(community_dir, "community_only")

        manager = PluginManager(
            core_plugins_dir=core_dir,
            community_plugins_dir=community_dir,
            state_file=tmp_path / "states.json",
        )
        manifests = await manager.discover()
        assert len(manifests) == 1
        assert manifests[0].name == "community_only"
        assert manifests[0].plugin_type == "community"

    @pytest.mark.asyncio
    async def test_core_only(self, tmp_path: Path) -> None:
        """When community dir doesn't exist, only core plugins should be found."""
        core_dir = tmp_path / "core"
        _create_plugin_dir(core_dir, "core_only")
        community_dir = tmp_path / "community_nonexistent"

        manager = PluginManager(
            core_plugins_dir=core_dir,
            community_plugins_dir=community_dir,
            state_file=tmp_path / "states.json",
        )
        manifests = await manager.discover()
        assert len(manifests) == 1
        assert manifests[0].name == "core_only"
        assert manifests[0].plugin_type == "core"

    @pytest.mark.asyncio
    async def test_both_dirs(self, tmp_path: Path) -> None:
        """Plugins from both directories should be discovered."""
        core_dir = tmp_path / "core"
        community_dir = tmp_path / "community"
        _create_plugin_dir(core_dir, "core_plug")
        _create_plugin_dir(community_dir, "community_plug")

        manager = PluginManager(
            core_plugins_dir=core_dir,
            community_plugins_dir=community_dir,
            state_file=tmp_path / "states.json",
        )
        manifests = await manager.discover()
        assert len(manifests) == 2
        names = {m.name for m in manifests}
        assert names == {"core_plug", "community_plug"}

        by_name = {m.name: m for m in manifests}
        assert by_name["core_plug"].plugin_type == "core"
        assert by_name["community_plug"].plugin_type == "community"

    @pytest.mark.asyncio
    async def test_empty_community_dir(self, tmp_path: Path) -> None:
        """An empty community dir should not cause errors."""
        core_dir = tmp_path / "core"
        community_dir = tmp_path / "community"
        _create_plugin_dir(core_dir, "the_core")
        community_dir.mkdir(parents=True)

        manager = PluginManager(
            core_plugins_dir=core_dir,
            community_plugins_dir=community_dir,
            state_file=tmp_path / "states.json",
        )
        manifests = await manager.discover()
        assert len(manifests) == 1
        assert manifests[0].name == "the_core"

    @pytest.mark.asyncio
    async def test_nonexistent_community_dir(self, tmp_path: Path) -> None:
        """A non-existent community dir should be handled gracefully."""
        core_dir = tmp_path / "core"
        _create_plugin_dir(core_dir, "solo")

        manager = PluginManager(
            core_plugins_dir=core_dir,
            community_plugins_dir=tmp_path / "ghost_dir",
            state_file=tmp_path / "states.json",
        )
        manifests = await manager.discover()
        assert len(manifests) == 1

    @pytest.mark.asyncio
    async def test_nonexistent_both_dirs(self, tmp_path: Path) -> None:
        """Both dirs nonexistent should yield zero plugins."""
        manager = PluginManager(
            core_plugins_dir=tmp_path / "nope_core",
            community_plugins_dir=tmp_path / "nope_community",
            state_file=tmp_path / "states.json",
        )
        manifests = await manager.discover()
        assert len(manifests) == 0

    @pytest.mark.asyncio
    async def test_mixed_enabled_disabled(self, tmp_path: Path) -> None:
        """Disabled plugins should still be discovered but not started."""
        core_dir = tmp_path / "core"
        community_dir = tmp_path / "community"
        _create_plugin_dir(core_dir, "active_core")
        _create_plugin_dir(community_dir, "disabled_community")

        for name in ["active_core", "disabled_community"]:
            shutil.rmtree(Path("data/plugins") / name, ignore_errors=True)

        manager = PluginManager(
            core_plugins_dir=core_dir,
            community_plugins_dir=community_dir,
            state_file=tmp_path / "states.json",
        )
        manifests = await manager.discover()
        assert len(manifests) == 2

        await manager.load_all()
        manager.state_manager.set_enabled("disabled_community", False)

        started = await manager.start_all(
            event_bus=EventBus(),
            music_library=MagicMock(),
            player_registry=MagicMock(),
        )
        assert started == 1
        assert "active_core" in manager.started_plugins
        assert "disabled_community" not in manager.started_plugins

    @pytest.mark.asyncio
    async def test_directory_without_toml_skipped(self, tmp_path: Path) -> None:
        """Subdirectories without plugin.toml should be silently skipped."""
        core_dir = tmp_path / "core"
        _create_plugin_dir(core_dir, "real_plugin")

        # Create a directory without plugin.toml
        fake_dir = core_dir / "not_a_plugin"
        fake_dir.mkdir(parents=True)
        (fake_dir / "random.py").write_text("x = 1", encoding="utf-8")

        manager = PluginManager(
            core_plugins_dir=core_dir,
            community_plugins_dir=tmp_path / "__none__",
            state_file=tmp_path / "states.json",
        )
        manifests = await manager.discover()
        assert len(manifests) == 1
        assert manifests[0].name == "real_plugin"

    @pytest.mark.asyncio
    async def test_files_in_plugins_dir_ignored(self, tmp_path: Path) -> None:
        """Files (not directories) in the plugins dir should be ignored."""
        core_dir = tmp_path / "core"
        _create_plugin_dir(core_dir, "legit")

        # Create a stray file in core_dir
        (core_dir / "README.md").write_text("# Plugins", encoding="utf-8")

        manager = PluginManager(
            core_plugins_dir=core_dir,
            community_plugins_dir=tmp_path / "__none__",
            state_file=tmp_path / "states.json",
        )
        manifests = await manager.discover()
        assert len(manifests) == 1

    @pytest.mark.asyncio
    async def test_multiple_community_plugins(self, tmp_path: Path) -> None:
        """Multiple community plugins should all be discovered."""
        core_dir = tmp_path / "core"
        core_dir.mkdir(parents=True)
        community_dir = tmp_path / "community"

        for name in ["comm_a", "comm_b", "comm_c"]:
            _create_plugin_dir(community_dir, name)

        manager = PluginManager(
            core_plugins_dir=core_dir,
            community_plugins_dir=community_dir,
            state_file=tmp_path / "states.json",
        )
        manifests = await manager.discover()
        assert len(manifests) == 3
        assert all(m.plugin_type == "community" for m in manifests)


# =============================================================================
# TestPluginManagerPluginType
# =============================================================================


class TestPluginManagerPluginType:
    @pytest.mark.asyncio
    async def test_plugin_type_core(self, tmp_path: Path) -> None:
        core_dir = tmp_path / "core"
        _create_plugin_dir(core_dir, "my_core")
        shutil.rmtree(Path("data/plugins") / "my_core", ignore_errors=True)

        manager = PluginManager(
            core_plugins_dir=core_dir,
            community_plugins_dir=tmp_path / "__none__",
            state_file=tmp_path / "states.json",
        )
        manifests = await manager.discover()
        assert manifests[0].plugin_type == "core"

    @pytest.mark.asyncio
    async def test_plugin_type_community(self, tmp_path: Path) -> None:
        core_dir = tmp_path / "core"
        core_dir.mkdir(parents=True)
        community_dir = tmp_path / "community"
        _create_plugin_dir(community_dir, "my_community")
        shutil.rmtree(Path("data/plugins") / "my_community", ignore_errors=True)

        manager = PluginManager(
            core_plugins_dir=core_dir,
            community_plugins_dir=community_dir,
            state_file=tmp_path / "states.json",
        )
        manifests = await manager.discover()
        assert manifests[0].plugin_type == "community"

    @pytest.mark.asyncio
    async def test_type_in_list_plugin_info(self, tmp_path: Path) -> None:
        core_dir = tmp_path / "core"
        community_dir = tmp_path / "community"
        _create_plugin_dir(core_dir, "info_core")
        _create_plugin_dir(community_dir, "info_community")

        for name in ["info_core", "info_community"]:
            shutil.rmtree(Path("data/plugins") / name, ignore_errors=True)

        manager = PluginManager(
            core_plugins_dir=core_dir,
            community_plugins_dir=community_dir,
            state_file=tmp_path / "states.json",
        )
        await manager.discover()
        await manager.load_all()
        await manager.start_all(
            event_bus=EventBus(),
            music_library=MagicMock(),
            player_registry=MagicMock(),
        )

        info_list = manager.list_plugin_info()
        by_name = {item["name"]: item for item in info_list}

        assert by_name["info_core"]["type"] == "core"
        assert by_name["info_core"]["can_uninstall"] is False
        assert by_name["info_community"]["type"] == "community"
        assert by_name["info_community"]["can_uninstall"] is True

    @pytest.mark.asyncio
    async def test_is_core_plugin(self, tmp_path: Path) -> None:
        core_dir = tmp_path / "core"
        community_dir = tmp_path / "community"
        _create_plugin_dir(core_dir, "c_core")
        _create_plugin_dir(community_dir, "c_community")

        for name in ["c_core", "c_community"]:
            shutil.rmtree(Path("data/plugins") / name, ignore_errors=True)

        manager = PluginManager(
            core_plugins_dir=core_dir,
            community_plugins_dir=community_dir,
            state_file=tmp_path / "states.json",
        )
        await manager.discover()
        await manager.load_all()

        assert manager.is_core_plugin("c_core") is True
        assert manager.is_core_plugin("c_community") is False
        assert manager.is_core_plugin("nonexistent") is False

    @pytest.mark.asyncio
    async def test_can_uninstall_flag(self, tmp_path: Path) -> None:
        core_dir = tmp_path / "core"
        community_dir = tmp_path / "community"
        _create_plugin_dir(core_dir, "uninstall_core")
        _create_plugin_dir(community_dir, "uninstall_community")

        for name in ["uninstall_core", "uninstall_community"]:
            shutil.rmtree(Path("data/plugins") / name, ignore_errors=True)

        manager = PluginManager(
            core_plugins_dir=core_dir,
            community_plugins_dir=community_dir,
            state_file=tmp_path / "states.json",
        )
        await manager.discover()
        await manager.load_all()
        await manager.start_all(
            event_bus=EventBus(),
            music_library=MagicMock(),
            player_registry=MagicMock(),
        )

        info_list = manager.list_plugin_info()
        by_name = {item["name"]: item for item in info_list}
        assert by_name["uninstall_core"]["can_uninstall"] is False
        assert by_name["uninstall_community"]["can_uninstall"] is True

    @pytest.mark.asyncio
    async def test_discover_clears_previous(self, tmp_path: Path) -> None:
        """Calling discover() again should clear previous manifests."""
        core_dir = tmp_path / "core"
        _create_plugin_dir(core_dir, "first_pass")
        shutil.rmtree(Path("data/plugins") / "first_pass", ignore_errors=True)

        manager = PluginManager(
            core_plugins_dir=core_dir,
            community_plugins_dir=tmp_path / "__none__",
            state_file=tmp_path / "states.json",
        )
        manifests1 = await manager.discover()
        assert len(manifests1) == 1

        # Remove the plugin directory
        shutil.rmtree(core_dir / "first_pass")

        manifests2 = await manager.discover()
        assert len(manifests2) == 0


# =============================================================================
# TestPluginManagerBackwardCompat
# =============================================================================


class TestPluginManagerBackwardCompat:
    @pytest.mark.asyncio
    async def test_plugins_dir_compat(self, tmp_path: Path) -> None:
        """Legacy plugins_dir argument should work as core_plugins_dir."""
        core_dir = tmp_path / "legacy"
        _create_plugin_dir(core_dir, "legacy_plug")
        shutil.rmtree(Path("data/plugins") / "legacy_plug", ignore_errors=True)

        manager = PluginManager(
            plugins_dir=core_dir,
            state_file=tmp_path / "states.json",
        )
        assert manager.core_plugins_dir == core_dir
        manifests = await manager.discover()
        assert len(manifests) == 1
