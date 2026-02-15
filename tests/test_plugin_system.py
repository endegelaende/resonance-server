"""
Tests for the Resonance Plugin System.

Covers:
- PluginManifest parsing (valid, missing fields, from_toml)
- PluginContext (command registration, menu registration, event tracking, cleanup)
- PluginManager lifecycle (discover, load, start, stop)
- Dynamic command registration / unregistration in jsonrpc
- Plugin menu entries appearing in _build_main_menu()
- Error handling (bad manifests, missing setup, failing plugins)
"""

from __future__ import annotations

import asyncio
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from resonance.core.events import Event, EventBus, event_bus
from resonance.plugin import (
    PluginContext,
    PluginManifest,
    _clear_plugin_menus,
    _plugin_menu_items,
    _plugin_menu_nodes,
    get_plugin_menu_items,
    get_plugin_menu_nodes,
)
from resonance.plugin_manager import PluginManager, _LoadedPlugin
from resonance.web.jsonrpc import (
    COMMAND_HANDLERS,
    register_command,
    unregister_command,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_plugin_menus():
    """Ensure plugin menu registries are empty before and after each test."""
    _clear_plugin_menus()
    yield
    _clear_plugin_menus()


@pytest.fixture()
def fresh_event_bus() -> EventBus:
    """Return a fresh EventBus instance (not the global singleton)."""
    return EventBus()


@pytest.fixture()
def mock_library() -> MagicMock:
    return MagicMock()


@pytest.fixture()
def mock_registry() -> MagicMock:
    return MagicMock()


@pytest.fixture()
def mock_playlist_manager() -> MagicMock:
    return MagicMock()


@pytest.fixture()
def plugin_context(
    fresh_event_bus: EventBus,
    mock_library: MagicMock,
    mock_registry: MagicMock,
    mock_playlist_manager: MagicMock,
    tmp_path: Path,
) -> PluginContext:
    """Create a PluginContext wired to mocks for isolated testing."""
    registered: dict[str, Any] = {}

    def _register(name: str, handler: Any) -> None:
        if name in registered:
            raise RuntimeError(f"Command '{name}' is already registered")
        registered[name] = handler

    def _unregister(name: str) -> None:
        registered.pop(name, None)

    return PluginContext(
        plugin_id="test_plugin",
        event_bus=fresh_event_bus,
        music_library=mock_library,
        player_registry=mock_registry,
        playlist_manager=mock_playlist_manager,
        _command_register=_register,
        _command_unregister=_unregister,
        _route_register=MagicMock(),
        data_dir=tmp_path / "plugin_data",
    )


# ═══════════════════════════════════════════════════════════════════════════
# PluginManifest
# ═══════════════════════════════════════════════════════════════════════════


class TestPluginManifest:
    """Tests for PluginManifest parsing."""

    def test_from_toml_minimal(self, tmp_path: Path) -> None:
        data = {"name": "hello", "version": "1.0.0"}
        m = PluginManifest.from_toml(data, tmp_path)
        assert m.name == "hello"
        assert m.version == "1.0.0"
        assert m.description == ""
        assert m.author == ""
        assert m.min_resonance_version == ""
        assert m.plugin_dir == tmp_path

    def test_from_toml_full(self, tmp_path: Path) -> None:
        data = {
            "name": "fancy",
            "version": "2.3.1",
            "description": "A fancy plugin",
            "author": "Alice",
            "min_resonance_version": "0.1.0",
        }
        m = PluginManifest.from_toml(data, tmp_path)
        assert m.name == "fancy"
        assert m.version == "2.3.1"
        assert m.description == "A fancy plugin"
        assert m.author == "Alice"
        assert m.min_resonance_version == "0.1.0"

    def test_from_toml_missing_name(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="missing 'name'"):
            PluginManifest.from_toml({"version": "1.0.0"}, tmp_path)

    def test_from_toml_missing_version(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="missing 'version'"):
            PluginManifest.from_toml({"name": "oops"}, tmp_path)

    def test_from_toml_empty_name(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="missing 'name'"):
            PluginManifest.from_toml({"name": "", "version": "1.0.0"}, tmp_path)

    def test_frozen(self, tmp_path: Path) -> None:
        m = PluginManifest.from_toml({"name": "x", "version": "1"}, tmp_path)
        with pytest.raises(AttributeError):
            m.name = "changed"  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════════════
# PluginContext — Command Registration
# ═══════════════════════════════════════════════════════════════════════════


class TestPluginContextCommands:
    """Tests for PluginContext command registration and cleanup."""

    def test_register_command(self, plugin_context: PluginContext) -> None:
        handler = AsyncMock()
        plugin_context.register_command("test.cmd", handler)
        assert "test.cmd" in plugin_context._registered_commands

    def test_register_duplicate_raises(self, plugin_context: PluginContext) -> None:
        handler = AsyncMock()
        plugin_context.register_command("dup.cmd", handler)
        with pytest.raises(RuntimeError, match="already registered"):
            plugin_context.register_command("dup.cmd", handler)

    def test_unregister_command(self, plugin_context: PluginContext) -> None:
        handler = AsyncMock()
        plugin_context.register_command("rm.cmd", handler)
        plugin_context.unregister_command("rm.cmd")
        assert "rm.cmd" not in plugin_context._registered_commands

    def test_unregister_unknown_is_silent(self, plugin_context: PluginContext) -> None:
        # Should not raise
        plugin_context.unregister_command("nonexistent")

    @pytest.mark.asyncio
    async def test_cleanup_removes_commands(self, plugin_context: PluginContext) -> None:
        handler = AsyncMock()
        plugin_context.register_command("cleanup.cmd", handler)
        assert len(plugin_context._registered_commands) == 1
        await plugin_context._cleanup()
        assert len(plugin_context._registered_commands) == 0

    def test_register_without_callback_raises(
        self, fresh_event_bus: EventBus, mock_library: MagicMock, mock_registry: MagicMock
    ) -> None:
        ctx = PluginContext(
            plugin_id="no_cb",
            event_bus=fresh_event_bus,
            music_library=mock_library,
            player_registry=mock_registry,
        )
        with pytest.raises(RuntimeError, match="not available"):
            ctx.register_command("x", AsyncMock())


# ═══════════════════════════════════════════════════════════════════════════
# PluginContext — Menu Registration
# ═══════════════════════════════════════════════════════════════════════════


class TestPluginContextMenus:
    """Tests for PluginContext menu node and item registration."""

    def test_register_menu_node(self, plugin_context: PluginContext) -> None:
        plugin_context.register_menu_node(
            node_id="myNode", parent="home", text="My Node", weight=50
        )
        nodes = get_plugin_menu_nodes()
        assert len(nodes) == 1
        assert nodes[0]["id"] == "myNode"
        assert nodes[0]["text"] == "My Node"
        assert nodes[0]["node"] == "home"
        assert nodes[0]["weight"] == 50
        assert nodes[0]["isANode"] == 1
        assert nodes[0]["_plugin_id"] == "test_plugin"

    def test_register_menu_item(self, plugin_context: PluginContext) -> None:
        item = {"text": "Sub Item", "actions": {"go": {"cmd": ["test"]}}}
        plugin_context.register_menu_item("myNode", item)
        items = get_plugin_menu_items()
        assert len(items) == 1
        assert items[0]["text"] == "Sub Item"
        assert items[0]["node"] == "myNode"
        assert items[0]["_plugin_id"] == "test_plugin"

    def test_register_menu_item_preserves_existing_node(self, plugin_context: PluginContext) -> None:
        item = {"text": "Override", "node": "customParent"}
        plugin_context.register_menu_item("ignored", item)
        items = get_plugin_menu_items()
        # node was already set to "customParent", register_menu_item uses setdefault
        assert items[0]["node"] == "customParent"

    def test_register_menu_node_extra_kwargs(self, plugin_context: PluginContext) -> None:
        plugin_context.register_menu_node(
            node_id="fancy", parent="home", text="Fancy", weight=10,
            icon="plugins/fancy/icon.png", windowStyle="icon_list",
        )
        nodes = get_plugin_menu_nodes()
        assert nodes[0]["icon"] == "plugins/fancy/icon.png"
        assert nodes[0]["windowStyle"] == "icon_list"

    @pytest.mark.asyncio
    async def test_cleanup_removes_menu_entries(self, plugin_context: PluginContext) -> None:
        plugin_context.register_menu_node("n1", "home", "Node 1", 10)
        plugin_context.register_menu_item("n1", {"text": "Item 1"})
        assert len(get_plugin_menu_nodes()) == 1
        assert len(get_plugin_menu_items()) == 1

        await plugin_context._cleanup()

        assert len(get_plugin_menu_nodes()) == 0
        assert len(get_plugin_menu_items()) == 0

    @pytest.mark.asyncio
    async def test_cleanup_only_removes_own_entries(
        self,
        fresh_event_bus: EventBus,
        mock_library: MagicMock,
        mock_registry: MagicMock,
    ) -> None:
        """Two plugins register menus; cleaning up one should not affect the other."""
        ctx_a = PluginContext(
            plugin_id="plugin_a",
            event_bus=fresh_event_bus,
            music_library=mock_library,
            player_registry=mock_registry,
        )
        ctx_b = PluginContext(
            plugin_id="plugin_b",
            event_bus=fresh_event_bus,
            music_library=mock_library,
            player_registry=mock_registry,
        )

        ctx_a.register_menu_node("a_node", "home", "A", 10)
        ctx_b.register_menu_node("b_node", "home", "B", 20)
        assert len(get_plugin_menu_nodes()) == 2

        await ctx_a._cleanup()
        remaining = get_plugin_menu_nodes()
        assert len(remaining) == 1
        assert remaining[0]["id"] == "b_node"

        await ctx_b._cleanup()
        assert len(get_plugin_menu_nodes()) == 0


# ═══════════════════════════════════════════════════════════════════════════
# PluginContext — Event Tracking
# ═══════════════════════════════════════════════════════════════════════════


class TestPluginContextEvents:
    """Tests for PluginContext tracked event subscriptions."""

    @pytest.mark.asyncio
    async def test_subscribe_tracks_handler(self, plugin_context: PluginContext) -> None:
        handler = AsyncMock()
        await plugin_context.subscribe("player.status", handler)
        assert len(plugin_context._registered_event_handlers) == 1
        assert plugin_context._registered_event_handlers[0] == ("player.status", handler)

    @pytest.mark.asyncio
    async def test_subscribe_handler_receives_events(self, plugin_context: PluginContext) -> None:
        handler = AsyncMock()
        await plugin_context.subscribe("test.event", handler)

        event = Event(event_type="test.event")
        await plugin_context.event_bus.publish(event)
        handler.assert_awaited_once_with(event)

    @pytest.mark.asyncio
    async def test_cleanup_unsubscribes_events(self, plugin_context: PluginContext) -> None:
        handler = AsyncMock()
        await plugin_context.subscribe("test.event", handler)

        await plugin_context._cleanup()
        assert len(plugin_context._registered_event_handlers) == 0

        # Handler should not be called after cleanup
        event = Event(event_type="test.event")
        await plugin_context.event_bus.publish(event)
        handler.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_multiple_subscriptions_all_cleaned(self, plugin_context: PluginContext) -> None:
        h1 = AsyncMock()
        h2 = AsyncMock()
        h3 = AsyncMock()
        await plugin_context.subscribe("e1", h1)
        await plugin_context.subscribe("e2", h2)
        await plugin_context.subscribe("e3", h3)
        assert len(plugin_context._registered_event_handlers) == 3

        await plugin_context._cleanup()
        assert len(plugin_context._registered_event_handlers) == 0


# ═══════════════════════════════════════════════════════════════════════════
# PluginContext — Data Directory
# ═══════════════════════════════════════════════════════════════════════════


class TestPluginContextDataDir:
    """Tests for per-plugin data directory."""

    def test_data_dir_default(
        self, fresh_event_bus: EventBus, mock_library: MagicMock, mock_registry: MagicMock
    ) -> None:
        ctx = PluginContext(
            plugin_id="myplugin",
            event_bus=fresh_event_bus,
            music_library=mock_library,
            player_registry=mock_registry,
        )
        assert ctx.data_dir == Path("data/plugins/myplugin")

    def test_data_dir_custom(self, plugin_context: PluginContext, tmp_path: Path) -> None:
        assert plugin_context.data_dir == tmp_path / "plugin_data"

    def test_ensure_data_dir_creates(self, plugin_context: PluginContext) -> None:
        result = plugin_context.ensure_data_dir()
        assert result.is_dir()
        assert result == plugin_context.data_dir

    def test_ensure_data_dir_idempotent(self, plugin_context: PluginContext) -> None:
        plugin_context.ensure_data_dir()
        plugin_context.ensure_data_dir()  # Should not raise
        assert plugin_context.data_dir.is_dir()


# ═══════════════════════════════════════════════════════════════════════════
# PluginContext — repr
# ═══════════════════════════════════════════════════════════════════════════


class TestPluginContextRepr:
    def test_repr(self, plugin_context: PluginContext) -> None:
        r = repr(plugin_context)
        assert "test_plugin" in r
        assert "commands=0" in r
        assert "menu_nodes=0" in r
        assert "menu_items=0" in r

    def test_repr_after_registrations(self, plugin_context: PluginContext) -> None:
        plugin_context.register_command("a", AsyncMock())
        plugin_context.register_menu_node("n", "home", "N", 10)
        plugin_context.register_menu_item("n", {"text": "I"})
        r = repr(plugin_context)
        assert "commands=1" in r
        assert "menu_nodes=1" in r
        assert "menu_items=1" in r


# ═══════════════════════════════════════════════════════════════════════════
# PluginContext — Full Cleanup Cycle
# ═══════════════════════════════════════════════════════════════════════════


class TestPluginContextFullCleanup:
    """Test that cleanup handles all registration types simultaneously."""

    @pytest.mark.asyncio
    async def test_full_cleanup(self, plugin_context: PluginContext) -> None:
        # Register everything
        plugin_context.register_command("full.cmd", AsyncMock())
        plugin_context.register_menu_node("full_node", "home", "Full", 10)
        plugin_context.register_menu_item("full_node", {"text": "Full Item"})
        await plugin_context.subscribe("full.event", AsyncMock())

        assert len(plugin_context._registered_commands) == 1
        assert len(plugin_context._registered_menu_node_ids) == 1
        assert len(plugin_context._registered_menu_item_ids) == 1
        assert len(plugin_context._registered_event_handlers) == 1
        assert len(get_plugin_menu_nodes()) == 1
        assert len(get_plugin_menu_items()) == 1

        await plugin_context._cleanup()

        assert len(plugin_context._registered_commands) == 0
        assert len(plugin_context._registered_menu_node_ids) == 0
        assert len(plugin_context._registered_menu_item_ids) == 0
        assert len(plugin_context._registered_event_handlers) == 0
        assert len(get_plugin_menu_nodes()) == 0
        assert len(get_plugin_menu_items()) == 0


# ═══════════════════════════════════════════════════════════════════════════
# Dynamic Command Registration (jsonrpc module-level)
# ═══════════════════════════════════════════════════════════════════════════


class TestDynamicCommandRegistration:
    """Tests for register_command / unregister_command in jsonrpc.py."""

    def test_register_new_command(self) -> None:
        handler = AsyncMock()
        name = "_test_dynamic_cmd_1"
        try:
            register_command(name, handler)
            assert name in COMMAND_HANDLERS
            assert COMMAND_HANDLERS[name] is handler
        finally:
            COMMAND_HANDLERS.pop(name, None)

    def test_register_duplicate_raises(self) -> None:
        handler = AsyncMock()
        name = "_test_dynamic_cmd_2"
        try:
            register_command(name, handler)
            with pytest.raises(RuntimeError, match="already registered"):
                register_command(name, handler)
        finally:
            COMMAND_HANDLERS.pop(name, None)

    def test_unregister_existing(self) -> None:
        handler = AsyncMock()
        name = "_test_dynamic_cmd_3"
        register_command(name, handler)
        unregister_command(name)
        assert name not in COMMAND_HANDLERS

    def test_unregister_nonexistent_is_silent(self) -> None:
        # Should not raise
        unregister_command("_test_nonexistent_cmd_xyz")

    def test_cannot_overwrite_builtin(self) -> None:
        """Built-in commands like 'play' must not be overwritten by plugins."""
        with pytest.raises(RuntimeError, match="already registered"):
            register_command("play", AsyncMock())


# ═══════════════════════════════════════════════════════════════════════════
# PluginManager — Discover
# ═══════════════════════════════════════════════════════════════════════════


class TestPluginManagerDiscover:
    """Tests for PluginManager.discover()."""

    @pytest.mark.asyncio
    async def test_discover_empty_dir(self, tmp_path: Path) -> None:
        pm = PluginManager(plugins_dir=tmp_path)
        manifests = await pm.discover()
        assert manifests == []

    @pytest.mark.asyncio
    async def test_discover_nonexistent_dir(self, tmp_path: Path) -> None:
        pm = PluginManager(plugins_dir=tmp_path / "does_not_exist")
        manifests = await pm.discover()
        assert manifests == []

    @pytest.mark.asyncio
    async def test_discover_valid_plugin(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "myplugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.toml").write_text(
            '[plugin]\nname = "myplugin"\nversion = "1.0.0"\n'
        )
        (plugin_dir / "__init__.py").write_text("async def setup(ctx): pass\n")

        pm = PluginManager(plugins_dir=tmp_path)
        manifests = await pm.discover()
        assert len(manifests) == 1
        assert manifests[0].name == "myplugin"
        assert manifests[0].version == "1.0.0"

    @pytest.mark.asyncio
    async def test_discover_skips_files(self, tmp_path: Path) -> None:
        (tmp_path / "not_a_dir.txt").write_text("hello")
        pm = PluginManager(plugins_dir=tmp_path)
        manifests = await pm.discover()
        assert manifests == []

    @pytest.mark.asyncio
    async def test_discover_skips_dir_without_toml(self, tmp_path: Path) -> None:
        (tmp_path / "no_manifest").mkdir()
        pm = PluginManager(plugins_dir=tmp_path)
        manifests = await pm.discover()
        assert manifests == []

    @pytest.mark.asyncio
    async def test_discover_skips_bad_toml(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "bad"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.toml").write_text("this is not valid TOML [[[")

        pm = PluginManager(plugins_dir=tmp_path)
        manifests = await pm.discover()
        assert manifests == []

    @pytest.mark.asyncio
    async def test_discover_skips_missing_name(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "noname"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.toml").write_text('[plugin]\nversion = "1.0.0"\n')

        pm = PluginManager(plugins_dir=tmp_path)
        manifests = await pm.discover()
        assert manifests == []

    @pytest.mark.asyncio
    async def test_discover_multiple_sorted(self, tmp_path: Path) -> None:
        for name in ["zebra", "alpha", "middle"]:
            d = tmp_path / name
            d.mkdir()
            (d / "plugin.toml").write_text(f'[plugin]\nname = "{name}"\nversion = "1.0"\n')
            (d / "__init__.py").write_text("async def setup(ctx): pass\n")

        pm = PluginManager(plugins_dir=tmp_path)
        manifests = await pm.discover()
        assert len(manifests) == 3
        # Sorted alphabetically by directory name
        assert [m.name for m in manifests] == ["alpha", "middle", "zebra"]


# ═══════════════════════════════════════════════════════════════════════════
# PluginManager — Load
# ═══════════════════════════════════════════════════════════════════════════


class TestPluginManagerLoad:
    """Tests for PluginManager.load_all()."""

    @pytest.mark.asyncio
    async def test_load_valid_plugin(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "good"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.toml").write_text(
            '[plugin]\nname = "good"\nversion = "1.0.0"\n'
        )
        (plugin_dir / "__init__.py").write_text(
            "async def setup(ctx): pass\nasync def teardown(ctx): pass\n"
        )

        pm = PluginManager(plugins_dir=tmp_path)
        await pm.discover()
        loaded = await pm.load_all()
        assert loaded == 1
        assert "good" in pm.plugins

    @pytest.mark.asyncio
    async def test_load_missing_init(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "noinit"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.toml").write_text(
            '[plugin]\nname = "noinit"\nversion = "1.0.0"\n'
        )
        # No __init__.py!

        pm = PluginManager(plugins_dir=tmp_path)
        await pm.discover()
        loaded = await pm.load_all()
        assert loaded == 0
        assert "noinit" not in pm.plugins

    @pytest.mark.asyncio
    async def test_load_missing_setup(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "nosetup"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.toml").write_text(
            '[plugin]\nname = "nosetup"\nversion = "1.0.0"\n'
        )
        (plugin_dir / "__init__.py").write_text(
            "# No setup function\nVALUE = 42\n"
        )

        pm = PluginManager(plugins_dir=tmp_path)
        await pm.discover()
        loaded = await pm.load_all()
        assert loaded == 0

    @pytest.mark.asyncio
    async def test_load_setup_not_callable(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "badsetup"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.toml").write_text(
            '[plugin]\nname = "badsetup"\nversion = "1.0.0"\n'
        )
        (plugin_dir / "__init__.py").write_text("setup = 'not a function'\n")

        pm = PluginManager(plugins_dir=tmp_path)
        await pm.discover()
        loaded = await pm.load_all()
        assert loaded == 0

    @pytest.mark.asyncio
    async def test_load_syntax_error(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "syntaxerr"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.toml").write_text(
            '[plugin]\nname = "syntaxerr"\nversion = "1.0.0"\n'
        )
        (plugin_dir / "__init__.py").write_text("def setup(ctx)\n")  # Missing colon

        pm = PluginManager(plugins_dir=tmp_path)
        await pm.discover()
        loaded = await pm.load_all()
        assert loaded == 0

    @pytest.mark.asyncio
    async def test_load_idempotent(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "idem"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.toml").write_text(
            '[plugin]\nname = "idem"\nversion = "1.0.0"\n'
        )
        (plugin_dir / "__init__.py").write_text("async def setup(ctx): pass\n")

        pm = PluginManager(plugins_dir=tmp_path)
        await pm.discover()
        await pm.load_all()
        # Loading again should skip duplicates
        loaded = await pm.load_all()
        assert loaded == 0
        assert pm.loaded_plugin_count == 1


# ═══════════════════════════════════════════════════════════════════════════
# PluginManager — Start / Stop
# ═══════════════════════════════════════════════════════════════════════════


def _create_plugin(
    tmp_path: Path,
    name: str,
    code: str,
    version: str = "1.0.0",
) -> Path:
    """Helper to create a plugin directory with manifest and code."""
    plugin_dir = tmp_path / name
    plugin_dir.mkdir(exist_ok=True)
    (plugin_dir / "plugin.toml").write_text(
        f'[plugin]\nname = "{name}"\nversion = "{version}"\n'
    )
    (plugin_dir / "__init__.py").write_text(textwrap.dedent(code))
    return plugin_dir


class TestPluginManagerStartStop:
    """Tests for PluginManager start_all / stop_all lifecycle."""

    @pytest.mark.asyncio
    async def test_start_calls_setup(self, tmp_path: Path, fresh_event_bus: EventBus) -> None:
        _create_plugin(tmp_path, "starter", """
            _started = False

            async def setup(ctx):
                global _started
                _started = True

            async def teardown(ctx):
                global _started
                _started = False
        """)

        pm = PluginManager(plugins_dir=tmp_path)
        await pm.discover()
        await pm.load_all()
        started = await pm.start_all(
            event_bus=fresh_event_bus,
            music_library=MagicMock(),
            player_registry=MagicMock(),
        )
        assert started == 1
        assert pm.started_plugins == ["starter"]

        # Verify setup was called
        import sys
        mod = sys.modules["resonance_plugins.starter"]
        assert mod._started is True  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_stop_calls_teardown(self, tmp_path: Path, fresh_event_bus: EventBus) -> None:
        _create_plugin(tmp_path, "stopper", """
            _torn_down = False

            async def setup(ctx):
                pass

            async def teardown(ctx):
                global _torn_down
                _torn_down = True
        """)

        pm = PluginManager(plugins_dir=tmp_path)
        await pm.discover()
        await pm.load_all()
        await pm.start_all(
            event_bus=fresh_event_bus,
            music_library=MagicMock(),
            player_registry=MagicMock(),
        )
        await pm.stop_all()
        assert pm.started_plugins == []

        import sys
        mod = sys.modules["resonance_plugins.stopper"]
        assert mod._torn_down is True  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_start_with_command_registration(
        self, tmp_path: Path, fresh_event_bus: EventBus
    ) -> None:
        _create_plugin(tmp_path, "cmdreg", """
            async def setup(ctx):
                ctx.register_command("cmdreg.hello", _cmd_hello)

            async def _cmd_hello(ctx, command):
                return {"hello": True}
        """)

        commands_registered: dict[str, Any] = {}

        pm = PluginManager(plugins_dir=tmp_path)
        await pm.discover()
        await pm.load_all()
        await pm.start_all(
            event_bus=fresh_event_bus,
            music_library=MagicMock(),
            player_registry=MagicMock(),
            command_register=lambda n, h: commands_registered.__setitem__(n, h),
            command_unregister=lambda n: commands_registered.pop(n, None),
        )
        assert "cmdreg.hello" in commands_registered

        # Stop should clean up
        await pm.stop_all()
        assert "cmdreg.hello" not in commands_registered

    @pytest.mark.asyncio
    async def test_start_with_menu_registration(
        self, tmp_path: Path, fresh_event_bus: EventBus
    ) -> None:
        _create_plugin(tmp_path, "menureg", """
            async def setup(ctx):
                ctx.register_menu_node(
                    node_id="menureg_node",
                    parent="home",
                    text="Menu Reg Plugin",
                    weight=999,
                )
        """)

        pm = PluginManager(plugins_dir=tmp_path)
        await pm.discover()
        await pm.load_all()
        await pm.start_all(
            event_bus=fresh_event_bus,
            music_library=MagicMock(),
            player_registry=MagicMock(),
        )
        nodes = get_plugin_menu_nodes()
        assert any(n["id"] == "menureg_node" for n in nodes)

        await pm.stop_all()
        assert len(get_plugin_menu_nodes()) == 0

    @pytest.mark.asyncio
    async def test_start_with_event_subscription(
        self, tmp_path: Path, fresh_event_bus: EventBus
    ) -> None:
        _create_plugin(tmp_path, "evsub", """
            _count = 0

            async def setup(ctx):
                await ctx.subscribe("test.ping", _on_ping)

            async def _on_ping(event):
                global _count
                _count += 1
        """)

        pm = PluginManager(plugins_dir=tmp_path)
        await pm.discover()
        await pm.load_all()
        await pm.start_all(
            event_bus=fresh_event_bus,
            music_library=MagicMock(),
            player_registry=MagicMock(),
        )

        # Publish an event — plugin handler should fire
        await fresh_event_bus.publish(Event(event_type="test.ping"))
        import sys
        mod = sys.modules["resonance_plugins.evsub"]
        assert mod._count == 1  # type: ignore[attr-defined]

        # After stop, events should no longer be received
        await pm.stop_all()
        await fresh_event_bus.publish(Event(event_type="test.ping"))
        assert mod._count == 1  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_failing_setup_does_not_block_others(
        self, tmp_path: Path, fresh_event_bus: EventBus
    ) -> None:
        _create_plugin(tmp_path, "alpha_good", """
            async def setup(ctx):
                ctx.register_command("alpha.ok", _cmd)
            async def _cmd(ctx, cmd):
                return {}
        """)
        _create_plugin(tmp_path, "beta_bad", """
            async def setup(ctx):
                raise RuntimeError("I broke!")
        """)
        _create_plugin(tmp_path, "gamma_good", """
            async def setup(ctx):
                ctx.register_command("gamma.ok", _cmd)
            async def _cmd(ctx, cmd):
                return {}
        """)

        cmds: dict[str, Any] = {}
        pm = PluginManager(plugins_dir=tmp_path)
        await pm.discover()
        await pm.load_all()
        started = await pm.start_all(
            event_bus=fresh_event_bus,
            music_library=MagicMock(),
            player_registry=MagicMock(),
            command_register=lambda n, h: cmds.__setitem__(n, h),
            command_unregister=lambda n: cmds.pop(n, None),
        )
        # 2 out of 3 should have started
        assert started == 2
        assert "alpha.ok" in cmds
        assert "gamma.ok" in cmds
        assert "beta_bad" not in pm.started_plugins

    @pytest.mark.asyncio
    async def test_failing_teardown_does_not_block_others(
        self, tmp_path: Path, fresh_event_bus: EventBus
    ) -> None:
        _create_plugin(tmp_path, "td_bad", """
            async def setup(ctx):
                pass
            async def teardown(ctx):
                raise RuntimeError("Teardown failed!")
        """)
        _create_plugin(tmp_path, "td_good", """
            _torn = False
            async def setup(ctx):
                pass
            async def teardown(ctx):
                global _torn
                _torn = True
        """)

        pm = PluginManager(plugins_dir=tmp_path)
        await pm.discover()
        await pm.load_all()
        await pm.start_all(
            event_bus=fresh_event_bus,
            music_library=MagicMock(),
            player_registry=MagicMock(),
        )
        # Should not raise despite td_bad failing
        await pm.stop_all()
        assert pm.started_plugins == []

    @pytest.mark.asyncio
    async def test_stop_reverse_order(
        self, tmp_path: Path, fresh_event_bus: EventBus
    ) -> None:
        """Plugins should be stopped in reverse start order."""
        order: list[str] = []

        _create_plugin(tmp_path, "first", f"""
            async def setup(ctx):
                pass
            async def teardown(ctx):
                import sys
                sys.modules['{__name__}'].__dict__.setdefault('_stop_order', []).append('first')
        """)
        _create_plugin(tmp_path, "second", f"""
            async def setup(ctx):
                pass
            async def teardown(ctx):
                import sys
                sys.modules['{__name__}'].__dict__.setdefault('_stop_order', []).append('second')
        """)

        # Inject a tracking list into this test module
        import sys
        this_module = sys.modules[__name__]
        this_module._stop_order = []  # type: ignore[attr-defined]

        pm = PluginManager(plugins_dir=tmp_path)
        await pm.discover()
        await pm.load_all()
        await pm.start_all(
            event_bus=fresh_event_bus,
            music_library=MagicMock(),
            player_registry=MagicMock(),
        )
        await pm.stop_all()

        stop_order = this_module._stop_order  # type: ignore[attr-defined]
        assert stop_order == ["second", "first"]

        # Cleanup
        del this_module._stop_order  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_teardown_optional(
        self, tmp_path: Path, fresh_event_bus: EventBus
    ) -> None:
        """Plugin without teardown() should still work fine."""
        _create_plugin(tmp_path, "no_td", """
            async def setup(ctx):
                pass
            # No teardown function
        """)

        pm = PluginManager(plugins_dir=tmp_path)
        await pm.discover()
        await pm.load_all()
        await pm.start_all(
            event_bus=fresh_event_bus,
            music_library=MagicMock(),
            player_registry=MagicMock(),
        )
        # Should not raise
        await pm.stop_all()
        assert pm.started_plugins == []


# ═══════════════════════════════════════════════════════════════════════════
# PluginManager — Convenience Properties
# ═══════════════════════════════════════════════════════════════════════════


class TestPluginManagerProperties:

    @pytest.mark.asyncio
    async def test_get_manifest(self, tmp_path: Path, fresh_event_bus: EventBus) -> None:
        _create_plugin(tmp_path, "props", "async def setup(ctx): pass\n", version="3.2.1")

        pm = PluginManager(plugins_dir=tmp_path)
        await pm.discover()
        await pm.load_all()

        m = pm.get_manifest("props")
        assert m is not None
        assert m.version == "3.2.1"

        assert pm.get_manifest("nonexistent") is None

    @pytest.mark.asyncio
    async def test_get_context_before_start(self, tmp_path: Path) -> None:
        _create_plugin(tmp_path, "ctx_test", "async def setup(ctx): pass\n")

        pm = PluginManager(plugins_dir=tmp_path)
        await pm.discover()
        await pm.load_all()

        # Not started yet — context should be None
        assert pm.get_context("ctx_test") is None

    @pytest.mark.asyncio
    async def test_get_context_after_start(
        self, tmp_path: Path, fresh_event_bus: EventBus
    ) -> None:
        _create_plugin(tmp_path, "ctx_test2", "async def setup(ctx): pass\n")

        pm = PluginManager(plugins_dir=tmp_path)
        await pm.discover()
        await pm.load_all()
        await pm.start_all(
            event_bus=fresh_event_bus,
            music_library=MagicMock(),
            player_registry=MagicMock(),
        )

        ctx = pm.get_context("ctx_test2")
        assert ctx is not None
        assert ctx.plugin_id == "ctx_test2"

    @pytest.mark.asyncio
    async def test_loaded_plugin_count(self, tmp_path: Path) -> None:
        _create_plugin(tmp_path, "c1", "async def setup(ctx): pass\n")
        _create_plugin(tmp_path, "c2", "async def setup(ctx): pass\n")

        pm = PluginManager(plugins_dir=tmp_path)
        assert pm.loaded_plugin_count == 0
        await pm.discover()
        await pm.load_all()
        assert pm.loaded_plugin_count == 2


# ═══════════════════════════════════════════════════════════════════════════
# PluginManager — Validate Module
# ═══════════════════════════════════════════════════════════════════════════


class TestPluginManagerValidation:

    def test_validate_module_ok(self, tmp_path: Path) -> None:
        import types
        mod = types.ModuleType("test_mod")
        mod.setup = AsyncMock()  # type: ignore[attr-defined]
        manifest = PluginManifest(name="t", version="1", plugin_dir=tmp_path)
        # Should not raise
        PluginManager._validate_module(mod, manifest)

    def test_validate_module_missing_setup(self, tmp_path: Path) -> None:
        import types
        mod = types.ModuleType("test_mod")
        manifest = PluginManifest(name="t", version="1", plugin_dir=tmp_path)
        with pytest.raises(TypeError, match="missing a 'setup'"):
            PluginManager._validate_module(mod, manifest)

    def test_validate_module_setup_not_callable(self, tmp_path: Path) -> None:
        import types
        mod = types.ModuleType("test_mod")
        mod.setup = "not callable"  # type: ignore[attr-defined]
        manifest = PluginManifest(name="t", version="1", plugin_dir=tmp_path)
        with pytest.raises(TypeError, match="not callable"):
            PluginManager._validate_module(mod, manifest)

    def test_validate_module_teardown_not_callable(self, tmp_path: Path) -> None:
        import types
        mod = types.ModuleType("test_mod")
        mod.setup = AsyncMock()  # type: ignore[attr-defined]
        mod.teardown = "not callable"  # type: ignore[attr-defined]
        manifest = PluginManifest(name="t", version="1", plugin_dir=tmp_path)
        with pytest.raises(TypeError, match="teardown.*not callable"):
            PluginManager._validate_module(mod, manifest)


# ═══════════════════════════════════════════════════════════════════════════
# Integration: Example Plugin
# ═══════════════════════════════════════════════════════════════════════════


class TestExamplePluginIntegration:
    """Smoke test loading the actual example plugin from the repo."""

    @pytest.mark.asyncio
    async def test_example_plugin_lifecycle(self, fresh_event_bus: EventBus) -> None:
        """Load, start, exercise, and stop the example plugin."""
        example_dir = Path("plugins")
        if not (example_dir / "example" / "plugin.toml").is_file():
            pytest.skip("Example plugin not present in plugins/")

        cmds: dict[str, Any] = {}

        pm = PluginManager(plugins_dir=example_dir)
        await pm.discover()

        # Should find the example plugin
        names = [m.name for m in pm.manifests]
        assert "example" in names

        await pm.load_all()
        assert "example" in pm.plugins

        await pm.start_all(
            event_bus=fresh_event_bus,
            music_library=MagicMock(),
            player_registry=MagicMock(),
            command_register=lambda n, h: cmds.__setitem__(n, h),
            command_unregister=lambda n: cmds.pop(n, None),
        )
        assert "example" in pm.started_plugins
        assert "example.hello" in cmds

        # Menu node should exist
        nodes = get_plugin_menu_nodes()
        assert any(n["id"] == "examplePlugin" for n in nodes)

        # Exercise the command handler
        mock_ctx = MagicMock()
        mock_ctx.player_id = "aa:bb:cc:dd:ee:ff"
        result = await cmds["example.hello"](mock_ctx, ["example.hello", "Claude"])
        assert "Hello" in result["message"]
        assert "Claude" in result["message"]

        # Stop — everything should be cleaned up
        await pm.stop_all()
        assert "example.hello" not in cmds
        assert len(get_plugin_menu_nodes()) == 0
        assert pm.started_plugins == []


# ═══════════════════════════════════════════════════════════════════════════
# Edge Cases
# ═══════════════════════════════════════════════════════════════════════════


class TestEdgeCases:

    @pytest.mark.asyncio
    async def test_stop_without_start(self, tmp_path: Path) -> None:
        """Stopping when nothing was started should be a no-op."""
        pm = PluginManager(plugins_dir=tmp_path)
        await pm.stop_all()  # Should not raise

    @pytest.mark.asyncio
    async def test_start_without_load(self, tmp_path: Path, fresh_event_bus: EventBus) -> None:
        """Starting when nothing was loaded should return 0."""
        pm = PluginManager(plugins_dir=tmp_path)
        started = await pm.start_all(
            event_bus=fresh_event_bus,
            music_library=MagicMock(),
            player_registry=MagicMock(),
        )
        assert started == 0

    @pytest.mark.asyncio
    async def test_double_start(self, tmp_path: Path, fresh_event_bus: EventBus) -> None:
        """Starting an already-started plugin should be skipped."""
        _create_plugin(tmp_path, "double", "async def setup(ctx): pass\n")

        pm = PluginManager(plugins_dir=tmp_path)
        await pm.discover()
        await pm.load_all()

        kwargs = dict(
            event_bus=fresh_event_bus,
            music_library=MagicMock(),
            player_registry=MagicMock(),
        )
        started1 = await pm.start_all(**kwargs)
        started2 = await pm.start_all(**kwargs)
        assert started1 == 1
        assert started2 == 0  # Already running

    @pytest.mark.asyncio
    async def test_partial_setup_failure_cleanup(
        self, tmp_path: Path, fresh_event_bus: EventBus
    ) -> None:
        """If setup registers some things then fails, partial registrations should be cleaned up."""
        _create_plugin(tmp_path, "partial", """
            async def setup(ctx):
                ctx.register_menu_node("partial_node", "home", "Partial", 10)
                raise RuntimeError("Oops, failed after menu reg!")
        """)

        pm = PluginManager(plugins_dir=tmp_path)
        await pm.discover()
        await pm.load_all()
        started = await pm.start_all(
            event_bus=fresh_event_bus,
            music_library=MagicMock(),
            player_registry=MagicMock(),
        )
        assert started == 0
        # The menu node registered before the crash should have been cleaned up
        assert len(get_plugin_menu_nodes()) == 0

    @pytest.mark.asyncio
    async def test_discover_clears_previous(self, tmp_path: Path) -> None:
        """Re-discovering should replace the old manifest list."""
        _create_plugin(tmp_path, "p1", "async def setup(ctx): pass\n")

        pm = PluginManager(plugins_dir=tmp_path)
        await pm.discover()
        assert len(pm.manifests) == 1

        # Add another plugin and re-discover
        _create_plugin(tmp_path, "p2", "async def setup(ctx): pass\n")
        await pm.discover()
        assert len(pm.manifests) == 2

    @pytest.mark.asyncio
    async def test_plugin_with_missing_toml_table(self, tmp_path: Path) -> None:
        """A plugin.toml without [plugin] table should be skipped."""
        plugin_dir = tmp_path / "notable"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.toml").write_text('[other]\nkey = "value"\n')

        pm = PluginManager(plugins_dir=tmp_path)
        manifests = await pm.discover()
        assert manifests == []
