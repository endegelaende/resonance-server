"""
Tests for the Now Playing tutorial plugin.

Covers:
- PlayHistory store (record, trimming, persistence, corrupt JSON, clear, update_max_entries)
- Command handlers (nowplaying.stats, nowplaying.recent — empty, with data, menu/CLI mode)
- Event handler (_on_track_started — single, multiple, store=None)
- Plugin lifecycle (setup/teardown, registrations, existing data)
- _parse_tagged helper (string params, dict params, colon in value, non-tagged)
- SDUI (get_ui, handle_action, tab builders, settings form, format_timestamp)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# ═══════════════════════════════════════════════════════════════════
# Store Tests
# ═══════════════════════════════════════════════════════════════════


class TestPlayHistory:
    """Tests for the PlayHistory store."""

    @pytest.fixture()
    def store(self, tmp_path: Path):
        from plugins.nowplaying.store import PlayHistory

        s = PlayHistory(tmp_path)
        s.load()
        return s

    def test_empty_store(self, store):
        assert store.total == 0
        assert store.count == 0
        assert store.entries == []

    def test_record(self, store):
        entry = store.record("aa:bb:cc:dd:ee:ff")
        assert entry["player_id"] == "aa:bb:cc:dd:ee:ff"
        assert entry["play_number"] == 1
        assert "timestamp" in entry
        assert store.total == 1
        assert store.count == 1

    def test_record_multiple(self, store):
        store.record("aa:bb:cc:dd:ee:ff")
        store.record("11:22:33:44:55:66")
        store.record("aa:bb:cc:dd:ee:ff")
        assert store.total == 3
        assert store.count == 3
        assert store.entries[-1]["play_number"] == 3

    def test_record_trims_old_entries(self, tmp_path):
        from plugins.nowplaying.store import PlayHistory

        store = PlayHistory(tmp_path, max_entries=5)
        store.load()

        for i in range(10):
            store.record(f"player-{i}")

        assert store.total == 10  # Counter counts everything
        assert store.count == 5  # Only 5 stored
        assert store.entries[0]["play_number"] == 6  # Oldest kept: #6

    def test_clear(self, store):
        store.record("aa:bb:cc:dd:ee:ff")
        store.record("aa:bb:cc:dd:ee:ff")
        store.clear()
        assert store.total == 0
        assert store.count == 0

    # ── Persistence ────────────────────────────────────────────

    def test_save_and_load(self, tmp_path):
        from plugins.nowplaying.store import PlayHistory

        # Write data
        s1 = PlayHistory(tmp_path)
        s1.load()
        s1.record("aa:bb:cc:dd:ee:ff")
        s1.record("11:22:33:44:55:66")

        # Load into fresh store
        s2 = PlayHistory(tmp_path)
        s2.load()
        assert s2.total == 2
        assert s2.count == 2
        assert s2.entries[0]["player_id"] == "aa:bb:cc:dd:ee:ff"

    def test_save_creates_directory(self, tmp_path):
        from plugins.nowplaying.store import PlayHistory

        nested = tmp_path / "deep" / "nested"
        store = PlayHistory(nested)
        store.load()
        store.record("test")
        assert (nested / "history.json").is_file()

    def test_save_is_valid_json(self, tmp_path):
        from plugins.nowplaying.store import PlayHistory

        store = PlayHistory(tmp_path)
        store.load()
        store.record("test")

        content = (tmp_path / "history.json").read_text(encoding="utf-8")
        data = json.loads(content)
        assert "total" in data
        assert "entries" in data
        assert "updated" in data

    def test_load_corrupt_json(self, tmp_path):
        from plugins.nowplaying.store import PlayHistory

        (tmp_path / "history.json").write_text("BROKEN!!!", encoding="utf-8")
        store = PlayHistory(tmp_path)
        store.load()
        assert store.total == 0  # Graceful degradation

    def test_load_nonexistent(self, tmp_path):
        from plugins.nowplaying.store import PlayHistory

        store = PlayHistory(tmp_path)
        store.load()
        assert store.total == 0

    def test_load_empty_object(self, tmp_path):
        from plugins.nowplaying.store import PlayHistory

        (tmp_path / "history.json").write_text("{}", encoding="utf-8")
        store = PlayHistory(tmp_path)
        store.load()
        assert store.total == 0
        assert store.count == 0

    def test_persistence_preserves_total_across_trim(self, tmp_path):
        """Total count survives even when entries are trimmed."""
        from plugins.nowplaying.store import PlayHistory

        s1 = PlayHistory(tmp_path, max_entries=3)
        s1.load()
        for i in range(10):
            s1.record(f"player-{i}")
        assert s1.total == 10
        assert s1.count == 3

        s2 = PlayHistory(tmp_path, max_entries=3)
        s2.load()
        assert s2.total == 10
        assert s2.count == 3

    def test_no_tmp_file_remains(self, tmp_path):
        from plugins.nowplaying.store import PlayHistory

        store = PlayHistory(tmp_path)
        store.load()
        store.record("test")

        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0

    def test_record_returns_entry(self, store):
        entry = store.record("player-x")
        assert isinstance(entry, dict)
        assert entry["player_id"] == "player-x"
        assert entry["play_number"] == 1
        assert "timestamp" in entry

    def test_entries_order(self, store):
        store.record("first")
        store.record("second")
        store.record("third")
        assert store.entries[0]["player_id"] == "first"
        assert store.entries[1]["player_id"] == "second"
        assert store.entries[2]["player_id"] == "third"


# ═══════════════════════════════════════════════════════════════════
# Fake CommandContext
# ═══════════════════════════════════════════════════════════════════


class _FakeCtx:
    """Minimal stand-in for CommandContext used in handler tests."""

    def __init__(self, player_id: str = "-"):
        self.player_id = player_id
        self.music_library = None
        self.player_registry = AsyncMock()
        self.player_registry.get_by_mac = AsyncMock(return_value=None)
        self.playlist_manager = None
        self.streaming_server = None
        self.slimproto = None
        self.artwork_manager = None
        self.server_host = "127.0.0.1"
        self.server_port = 9000
        self.server_uuid = "test-uuid"


# ═══════════════════════════════════════════════════════════════════
# Plugin-Environment Fixture
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture()
def np_env(tmp_path):
    """Set up plugin module-level state for testing."""
    import plugins.nowplaying as mod
    from plugins.nowplaying.store import PlayHistory

    store = PlayHistory(tmp_path)
    store.load()
    mod._store = store

    yield store, mod

    mod._store = None


# ═══════════════════════════════════════════════════════════════════
# Command Handler Tests — nowplaying.stats
# ═══════════════════════════════════════════════════════════════════


class TestCmdStats:
    """Tests for nowplaying.stats command."""

    @pytest.mark.asyncio
    async def test_stats_empty(self, np_env):
        store, _ = np_env
        from plugins.nowplaying import cmd_stats

        result = await cmd_stats(_FakeCtx(), ["nowplaying.stats"])
        assert result["total_played"] == 0
        assert result["stored_entries"] == 0

    @pytest.mark.asyncio
    async def test_stats_after_records(self, np_env):
        store, _ = np_env
        store.record("player1")
        store.record("player2")

        from plugins.nowplaying import cmd_stats

        result = await cmd_stats(_FakeCtx(), ["nowplaying.stats"])
        assert result["total_played"] == 2
        assert result["stored_entries"] == 2

    @pytest.mark.asyncio
    async def test_stats_not_initialized(self):
        import plugins.nowplaying as mod

        mod._store = None

        from plugins.nowplaying import cmd_stats

        result = await cmd_stats(_FakeCtx(), ["nowplaying.stats"])
        assert "error" in result

    @pytest.mark.asyncio
    async def test_stats_after_many_records(self, np_env):
        store, _ = np_env
        for i in range(25):
            store.record(f"player-{i % 3}")

        from plugins.nowplaying import cmd_stats

        result = await cmd_stats(_FakeCtx(), ["nowplaying.stats"])
        assert result["total_played"] == 25
        assert result["stored_entries"] == 25


# ═══════════════════════════════════════════════════════════════════
# Command Handler Tests — nowplaying.recent
# ═══════════════════════════════════════════════════════════════════


class TestCmdRecent:
    """Tests for nowplaying.recent command."""

    @pytest.mark.asyncio
    async def test_recent_empty(self, np_env):
        from plugins.nowplaying import cmd_recent

        result = await cmd_recent(_FakeCtx(), ["nowplaying.recent"])
        assert result["count"] == 1
        assert "No tracks" in result["loop"][0]["text"]

    @pytest.mark.asyncio
    async def test_recent_with_data(self, np_env):
        store, _ = np_env
        store.record("player1")
        store.record("player2")

        from plugins.nowplaying import cmd_recent

        result = await cmd_recent(_FakeCtx(), ["nowplaying.recent"])
        assert result["count"] == 2
        # Newest first
        assert "#2" in result["loop"][0]["text"]
        assert "#1" in result["loop"][1]["text"]

    @pytest.mark.asyncio
    async def test_recent_menu_mode(self, np_env):
        store, _ = np_env
        store.record("player1")

        from plugins.nowplaying import cmd_recent

        result = await cmd_recent(_FakeCtx(), ["nowplaying.recent", "menu:1"])
        assert "item_loop" in result
        assert "loop" not in result

    @pytest.mark.asyncio
    async def test_recent_cli_mode(self, np_env):
        store, _ = np_env
        store.record("player1")

        from plugins.nowplaying import cmd_recent

        result = await cmd_recent(_FakeCtx(), ["nowplaying.recent"])
        assert "loop" in result
        assert "item_loop" not in result

    @pytest.mark.asyncio
    async def test_recent_not_initialized(self):
        import plugins.nowplaying as mod

        mod._store = None

        from plugins.nowplaying import cmd_recent

        result = await cmd_recent(_FakeCtx(), ["nowplaying.recent"])
        assert "error" in result

    @pytest.mark.asyncio
    async def test_recent_limits_to_20(self, np_env):
        store, _ = np_env
        for i in range(50):
            store.record(f"player-{i}")

        from plugins.nowplaying import cmd_recent

        result = await cmd_recent(_FakeCtx(), ["nowplaying.recent"])
        assert result["count"] == 20

    @pytest.mark.asyncio
    async def test_recent_includes_timestamp(self, np_env):
        store, _ = np_env
        store.record("player1")

        from plugins.nowplaying import cmd_recent

        result = await cmd_recent(_FakeCtx(), ["nowplaying.recent"])
        text = result["loop"][0]["text"]
        # Should contain time portion from timestamp (HH:MM:SS)
        assert "(" in text and ")" in text

    @pytest.mark.asyncio
    async def test_recent_includes_player_id(self, np_env):
        store, _ = np_env
        store.record("aa:bb:cc:dd:ee:ff")

        from plugins.nowplaying import cmd_recent

        result = await cmd_recent(_FakeCtx(), ["nowplaying.recent"])
        assert "aa:bb:cc:dd:ee:ff" in result["loop"][0]["text"]

    @pytest.mark.asyncio
    async def test_recent_dict_param(self, np_env):
        """Cometd-style dict params should work too."""
        store, _ = np_env
        store.record("player1")

        from plugins.nowplaying import cmd_recent

        result = await cmd_recent(
            _FakeCtx(), ["nowplaying.recent", {"menu": "1"}]
        )
        assert "item_loop" in result

    @pytest.mark.asyncio
    async def test_recent_offset_is_zero(self, np_env):
        store, _ = np_env
        store.record("player1")

        from plugins.nowplaying import cmd_recent

        result = await cmd_recent(_FakeCtx(), ["nowplaying.recent"])
        assert result["offset"] == 0

    @pytest.mark.asyncio
    async def test_recent_items_have_style(self, np_env):
        store, _ = np_env
        store.record("player1")

        from plugins.nowplaying import cmd_recent

        result = await cmd_recent(_FakeCtx(), ["nowplaying.recent"])
        assert result["loop"][0]["style"] == "itemNoAction"

    @pytest.mark.asyncio
    async def test_recent_empty_also_has_style(self, np_env):
        from plugins.nowplaying import cmd_recent

        result = await cmd_recent(_FakeCtx(), ["nowplaying.recent"])
        assert result["loop"][0]["style"] == "itemNoAction"


# ═══════════════════════════════════════════════════════════════════
# Event Handler Tests
# ═══════════════════════════════════════════════════════════════════


class TestEventHandler:
    """Tests for the player.track_started event handler."""

    @pytest.mark.asyncio
    async def test_on_track_started(self, np_env):
        store, _ = np_env
        from plugins.nowplaying import _on_track_started

        event = MagicMock()
        event.player_id = "aa:bb:cc:dd:ee:ff"

        await _on_track_started(event)
        assert store.total == 1
        assert store.entries[-1]["player_id"] == "aa:bb:cc:dd:ee:ff"

    @pytest.mark.asyncio
    async def test_on_track_started_multiple(self, np_env):
        store, _ = np_env
        from plugins.nowplaying import _on_track_started

        for i in range(5):
            event = MagicMock()
            event.player_id = f"player-{i}"
            await _on_track_started(event)

        assert store.total == 5
        assert store.count == 5

    @pytest.mark.asyncio
    async def test_on_track_started_store_none(self):
        """Handler should be a no-op when store is not initialized."""
        import plugins.nowplaying as mod

        mod._store = None

        from plugins.nowplaying import _on_track_started

        event = MagicMock()
        event.player_id = "test"
        await _on_track_started(event)  # Should not crash

    @pytest.mark.asyncio
    async def test_on_track_started_missing_player_id(self, np_env):
        """Handler should handle events without player_id gracefully."""
        store, _ = np_env
        from plugins.nowplaying import _on_track_started

        event = MagicMock(spec=[])  # No attributes at all

        await _on_track_started(event)
        assert store.total == 1
        assert store.entries[-1]["player_id"] == "unknown"

    @pytest.mark.asyncio
    async def test_on_track_started_persists(self, np_env, tmp_path):
        """Each record call should persist to disk."""
        store, _ = np_env
        from plugins.nowplaying import _on_track_started

        event = MagicMock()
        event.player_id = "persist-test"
        await _on_track_started(event)

        # Verify file was written
        from plugins.nowplaying.store import PlayHistory

        s2 = PlayHistory(tmp_path)
        s2.load()
        assert s2.total == 1
        assert s2.entries[0]["player_id"] == "persist-test"


# ═══════════════════════════════════════════════════════════════════
# Plugin Lifecycle Tests
# ═══════════════════════════════════════════════════════════════════


class TestLifecycle:
    """Tests for setup() and teardown()."""

    @pytest.fixture()
    def mock_ctx(self, tmp_path):
        ctx = MagicMock()
        ctx.plugin_id = "nowplaying"
        ctx.data_dir = tmp_path
        ctx.ensure_data_dir = MagicMock(return_value=tmp_path)
        ctx.event_bus = MagicMock()
        ctx.event_bus.publish = AsyncMock()
        ctx.register_command = MagicMock()
        ctx.register_menu_node = MagicMock()
        ctx.subscribe = AsyncMock()
        return ctx

    @pytest.mark.asyncio
    async def test_setup_registers_commands(self, mock_ctx):
        import plugins.nowplaying as mod

        await mod.setup(mock_ctx)

        names = [c[0][0] for c in mock_ctx.register_command.call_args_list]
        assert "nowplaying.stats" in names
        assert "nowplaying.recent" in names

        await mod.teardown(mock_ctx)

    @pytest.mark.asyncio
    async def test_setup_registers_two_commands(self, mock_ctx):
        import plugins.nowplaying as mod

        await mod.setup(mock_ctx)

        assert mock_ctx.register_command.call_count == 2

        await mod.teardown(mock_ctx)

    @pytest.mark.asyncio
    async def test_setup_registers_menu(self, mock_ctx):
        import plugins.nowplaying as mod

        await mod.setup(mock_ctx)

        mock_ctx.register_menu_node.assert_called_once()
        kwargs = mock_ctx.register_menu_node.call_args[1]
        assert kwargs["node_id"] == "nowPlaying"
        assert kwargs["parent"] == "home"
        assert kwargs["weight"] == 80
        assert kwargs["text"] == "Play Stats"

        await mod.teardown(mock_ctx)

    @pytest.mark.asyncio
    async def test_setup_subscribes_events(self, mock_ctx):
        import plugins.nowplaying as mod

        await mod.setup(mock_ctx)

        mock_ctx.subscribe.assert_called_once()
        args = mock_ctx.subscribe.call_args[0]
        assert args[0] == "player.track_started"

        await mod.teardown(mock_ctx)

    @pytest.mark.asyncio
    async def test_setup_initializes_store(self, mock_ctx):
        import plugins.nowplaying as mod

        await mod.setup(mock_ctx)
        assert mod._store is not None
        assert mod._store.total == 0

        await mod.teardown(mock_ctx)

    @pytest.mark.asyncio
    async def test_teardown_clears_store(self, mock_ctx):
        import plugins.nowplaying as mod

        await mod.setup(mock_ctx)
        assert mod._store is not None

        await mod.teardown(mock_ctx)
        assert mod._store is None

    @pytest.mark.asyncio
    async def test_setup_loads_existing_data(self, mock_ctx, tmp_path):
        """If history.json already exists, setup should load it."""
        data = {
            "total": 42,
            "entries": [
                {
                    "player_id": "test",
                    "timestamp": "2026-01-01T00:00:00Z",
                    "play_number": 42,
                }
            ],
        }
        (tmp_path / "history.json").write_text(
            json.dumps(data), encoding="utf-8"
        )

        import plugins.nowplaying as mod

        await mod.setup(mock_ctx)

        assert mod._store.total == 42
        assert mod._store.count == 1

        await mod.teardown(mock_ctx)

    @pytest.mark.asyncio
    async def test_setup_calls_ensure_data_dir(self, mock_ctx):
        import plugins.nowplaying as mod

        await mod.setup(mock_ctx)

        mock_ctx.ensure_data_dir.assert_called_once()

        await mod.teardown(mock_ctx)

    @pytest.mark.asyncio
    async def test_menu_actions_point_to_recent(self, mock_ctx):
        """The menu node should navigate to nowplaying.recent."""
        import plugins.nowplaying as mod

        await mod.setup(mock_ctx)

        kwargs = mock_ctx.register_menu_node.call_args[1]
        actions = kwargs["actions"]
        assert actions["go"]["cmd"] == ["nowplaying.recent"]
        assert actions["go"]["params"]["menu"] == 1

        await mod.teardown(mock_ctx)


# ═══════════════════════════════════════════════════════════════════
# Parse-Helper Tests
# ═══════════════════════════════════════════════════════════════════


class TestParseTagged:
    """Tests for the _parse_tagged helper."""

    def test_string_params(self):
        from plugins.nowplaying import _parse_tagged

        result = _parse_tagged(["cmd", "menu:1", "search:hello"], start=1)
        assert result["menu"] == "1"
        assert result["search"] == "hello"

    def test_dict_params(self):
        from plugins.nowplaying import _parse_tagged

        result = _parse_tagged(["cmd", {"menu": "1"}], start=1)
        assert result["menu"] == "1"

    def test_mixed_params(self):
        from plugins.nowplaying import _parse_tagged

        result = _parse_tagged(
            ["cmd", "key1:val1", {"key2": "val2"}], start=1
        )
        assert result["key1"] == "val1"
        assert result["key2"] == "val2"

    def test_colon_in_value(self):
        from plugins.nowplaying import _parse_tagged

        result = _parse_tagged(
            ["cmd", "url:http://host:8080/path"], start=1
        )
        assert result["url"] == "http://host:8080/path"

    def test_ignores_non_tagged(self):
        from plugins.nowplaying import _parse_tagged

        result = _parse_tagged(["cmd", "plain", "key:val"], start=1)
        assert "plain" not in result
        assert result["key"] == "val"

    def test_empty_command(self):
        from plugins.nowplaying import _parse_tagged

        result = _parse_tagged(["cmd"], start=1)
        assert result == {}

    def test_dict_with_none_value(self):
        from plugins.nowplaying import _parse_tagged

        result = _parse_tagged(
            ["cmd", {"good": "val", "bad": None}], start=1
        )
        assert result["good"] == "val"
        assert "bad" not in result

    def test_integer_params_ignored(self):
        from plugins.nowplaying import _parse_tagged

        result = _parse_tagged(["cmd", 42, "key:val"], start=1)
        assert result == {"key": "val"}

    def test_start_offset(self):
        from plugins.nowplaying import _parse_tagged

        result = _parse_tagged(
            ["cmd", "skip:this", "keep:that"], start=2
        )
        assert "skip" not in result
        assert result["keep"] == "that"


# ═══════════════════════════════════════════════════════════════════
# Integration Tests
# ═══════════════════════════════════════════════════════════════════


class TestIntegration:
    """End-to-end workflow tests combining multiple operations."""

    @pytest.mark.asyncio
    async def test_record_then_query(self, np_env):
        """Full workflow: record events, query stats and recent."""
        store, _ = np_env
        from plugins.nowplaying import _on_track_started, cmd_recent, cmd_stats

        # Simulate 3 track starts
        for pid in ["player-a", "player-b", "player-a"]:
            event = MagicMock()
            event.player_id = pid
            await _on_track_started(event)

        # Check stats
        stats = await cmd_stats(_FakeCtx(), ["nowplaying.stats"])
        assert stats["total_played"] == 3
        assert stats["stored_entries"] == 3

        # Check recent
        recent = await cmd_recent(_FakeCtx(), ["nowplaying.recent"])
        assert recent["count"] == 3
        # Newest first
        assert "player-a" in recent["loop"][0]["text"]
        assert "#3" in recent["loop"][0]["text"]

    @pytest.mark.asyncio
    async def test_persistence_workflow(self, np_env, tmp_path):
        """Record, reload from disk, verify data survives."""
        store, mod = np_env
        from plugins.nowplaying import _on_track_started

        event = MagicMock()
        event.player_id = "persist-test"
        await _on_track_started(event)

        # Create new store from same directory
        from plugins.nowplaying.store import PlayHistory

        s2 = PlayHistory(tmp_path)
        s2.load()
        assert s2.total == 1
        assert s2.entries[0]["player_id"] == "persist-test"

    @pytest.mark.asyncio
    async def test_recent_empty_then_filled(self, np_env):
        """Recent should transition from placeholder to real data."""
        from plugins.nowplaying import _on_track_started, cmd_recent

        # Empty
        r1 = await cmd_recent(_FakeCtx(), ["nowplaying.recent"])
        assert "No tracks" in r1["loop"][0]["text"]

        # Add a track
        event = MagicMock()
        event.player_id = "test-player"
        await _on_track_started(event)

        # Now has data
        r2 = await cmd_recent(_FakeCtx(), ["nowplaying.recent"])
        assert r2["count"] == 1
        assert "test-player" in r2["loop"][0]["text"]

    @pytest.mark.asyncio
    async def test_full_lifecycle(self, tmp_path):
        """Setup → record → query → teardown → verify cleanup."""
        import plugins.nowplaying as mod

        mock_ctx = MagicMock()
        mock_ctx.plugin_id = "nowplaying"
        mock_ctx.data_dir = tmp_path
        mock_ctx.ensure_data_dir = MagicMock(return_value=tmp_path)
        mock_ctx.event_bus = MagicMock()
        mock_ctx.event_bus.publish = AsyncMock()
        mock_ctx.register_command = MagicMock()
        mock_ctx.register_menu_node = MagicMock()
        mock_ctx.subscribe = AsyncMock()

        # Setup
        await mod.setup(mock_ctx)
        assert mod._store is not None

        # Record
        event = MagicMock()
        event.player_id = "lifecycle-test"
        await mod._on_track_started(event)
        assert mod._store.total == 1

        # Query
        result = await mod.cmd_stats(_FakeCtx(), ["nowplaying.stats"])
        assert result["total_played"] == 1

        # Teardown
        await mod.teardown(mock_ctx)
        assert mod._store is None


# =============================================================================
# Store — update_max_entries
# =============================================================================


class TestUpdateMaxEntries:
    """Tests for PlayHistory.update_max_entries()."""

    def test_update_max_entries_trims(self, tmp_path: Path):
        from plugins.nowplaying.store import PlayHistory

        store = PlayHistory(tmp_path, max_entries=50)
        store.load()
        for i in range(30):
            store.record(f"player-{i}")
        assert store.count == 30

        store.update_max_entries(20)
        assert store.count == 20
        # Should keep newest entries
        assert store.entries[-1]["play_number"] == 30

    def test_update_max_entries_no_trim_needed(self, tmp_path: Path):
        from plugins.nowplaying.store import PlayHistory

        store = PlayHistory(tmp_path, max_entries=50)
        store.load()
        for i in range(5):
            store.record(f"player-{i}")

        store.update_max_entries(100)
        assert store.count == 5

    def test_update_max_entries_minimum_clamped(self, tmp_path: Path):
        from plugins.nowplaying.store import PlayHistory

        store = PlayHistory(tmp_path, max_entries=50)
        store.load()
        for i in range(30):
            store.record(f"player-{i}")

        # Even if 5 is requested, minimum is 20
        store.update_max_entries(5)
        assert store.count == 20


# =============================================================================
# SDUI tests — get_ui, handle_action, tab builders
# =============================================================================


class TestSDUI:
    """Tests for the NowPlaying SDUI dashboard."""

    def _setup_module_state(self, tmp_path: Path) -> None:
        import plugins.nowplaying as mod
        from plugins.nowplaying.store import PlayHistory

        mod._store = PlayHistory(tmp_path)
        mod._store.load()
        mod._ctx = MagicMock()
        mod._ctx.get_setting = MagicMock(return_value=None)
        mod._ctx.set_setting = MagicMock()
        mod._ctx.notify_ui_update = MagicMock()

    def _teardown_module_state(self) -> None:
        import plugins.nowplaying as mod

        mod._store = None
        mod._ctx = None

    @pytest.mark.asyncio
    async def test_get_ui_returns_page(self, tmp_path: Path) -> None:
        import plugins.nowplaying as mod

        self._setup_module_state(tmp_path)
        try:
            page = await mod.get_ui(mod._ctx)
            assert page.title == "Play Stats"
            assert page.icon == "activity"
            assert page.refresh_interval == 10
            assert len(page.components) == 1
            tabs = page.components[0]
            assert tabs.type == "tabs"
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_get_ui_has_three_tabs(self, tmp_path: Path) -> None:
        import plugins.nowplaying as mod

        self._setup_module_state(tmp_path)
        try:
            page = await mod.get_ui(mod._ctx)
            page_dict = page.to_dict("nowplaying")
            tabs_data = page_dict["components"][0]["props"]["tabs"]
            tab_labels = [t["label"] for t in tabs_data]
            assert tab_labels == ["Stats", "Settings", "About"]
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_get_ui_stats_tab_empty(self, tmp_path: Path) -> None:
        import plugins.nowplaying as mod

        self._setup_module_state(tmp_path)
        try:
            page = await mod.get_ui(mod._ctx)
            page_dict = page.to_dict("nowplaying")
            stats_tab = page_dict["components"][0]["props"]["tabs"][0]
            assert stats_tab["label"] == "Stats"
            # Should have statistics card + empty recent card + button row
            assert len(stats_tab["children"]) >= 3
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_get_ui_stats_tab_with_plays(self, tmp_path: Path) -> None:
        import plugins.nowplaying as mod

        self._setup_module_state(tmp_path)
        try:
            mod._store.record("player-a")
            mod._store.record("player-b")
            mod._store.record("player-a")

            page = await mod.get_ui(mod._ctx)
            page_dict = page.to_dict("nowplaying")
            stats_tab = page_dict["components"][0]["props"]["tabs"][0]
            assert stats_tab["label"] == "Stats"
            # Should have statistics card + recent card (with table) + button row
            assert len(stats_tab["children"]) >= 3
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_get_ui_stats_tab_shows_player_summary(self, tmp_path: Path) -> None:
        import plugins.nowplaying as mod

        self._setup_module_state(tmp_path)
        try:
            for _ in range(5):
                mod._store.record("player-a")
            for _ in range(3):
                mod._store.record("player-b")

            page = await mod.get_ui(mod._ctx)
            page_dict = page.to_dict("nowplaying")
            stats_tab = page_dict["components"][0]["props"]["tabs"][0]
            # The statistics card should contain the "Most Active Players" collapsible
            stats_card = stats_tab["children"][0]
            assert stats_card["props"]["title"] == "Statistics"
            # Children include Row (badges), KeyValue (totals), Card (most active)
            assert len(stats_card["children"]) >= 3
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_get_ui_settings_tab(self, tmp_path: Path) -> None:
        import plugins.nowplaying as mod

        self._setup_module_state(tmp_path)
        try:
            page = await mod.get_ui(mod._ctx)
            page_dict = page.to_dict("nowplaying")
            settings_tab = page_dict["components"][0]["props"]["tabs"][1]
            assert settings_tab["label"] == "Settings"
            # Should have form + alert
            assert len(settings_tab["children"]) == 2
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_get_ui_about_tab(self, tmp_path: Path) -> None:
        import plugins.nowplaying as mod

        self._setup_module_state(tmp_path)
        try:
            page = await mod.get_ui(mod._ctx)
            page_dict = page.to_dict("nowplaying")
            about_tab = page_dict["components"][0]["props"]["tabs"][2]
            assert about_tab["label"] == "About"
            assert len(about_tab["children"]) >= 1
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_get_ui_serializes_cleanly(self, tmp_path: Path) -> None:
        import plugins.nowplaying as mod

        self._setup_module_state(tmp_path)
        try:
            mod._store.record("player-x")
            mod._store.record("player-y")

            page = await mod.get_ui(mod._ctx)
            d = page.to_dict("nowplaying")
            json_str = json.dumps(d)
            assert json_str
            parsed = json.loads(json_str)
            assert parsed["title"] == "Play Stats"
            assert parsed["plugin_id"] == "nowplaying"
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_get_ui_recent_table_newest_first(self, tmp_path: Path) -> None:
        import plugins.nowplaying as mod

        self._setup_module_state(tmp_path)
        try:
            mod._store.record("first-player")
            mod._store.record("second-player")
            mod._store.record("third-player")

            page = await mod.get_ui(mod._ctx)
            page_dict = page.to_dict("nowplaying")
            stats_tab = page_dict["components"][0]["props"]["tabs"][0]
            # Find the recent plays card (second child after stats card)
            recent_card = stats_tab["children"][1]
            assert "Recent Plays" in recent_card["props"]["title"]
            # Table rows should be newest first
            table = recent_card["children"][0]
            rows = table["props"]["rows"]
            assert rows[0]["player_id"] == "third-player"
            assert rows[2]["player_id"] == "first-player"
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_get_ui_store_none(self, tmp_path: Path) -> None:
        import plugins.nowplaying as mod

        self._setup_module_state(tmp_path)
        mod._store = None
        try:
            page = await mod.get_ui(mod._ctx)
            page_dict = page.to_dict("nowplaying")
            stats_tab = page_dict["components"][0]["props"]["tabs"][0]
            assert stats_tab["label"] == "Stats"
            # Should show warning alert
            assert len(stats_tab["children"]) >= 1
        finally:
            self._teardown_module_state()


class TestSDUIActions:
    """Tests for the NowPlaying SDUI action handlers."""

    def _setup_module_state(self, tmp_path: Path) -> None:
        import plugins.nowplaying as mod
        from plugins.nowplaying.store import PlayHistory

        mod._store = PlayHistory(tmp_path)
        mod._store.load()
        mod._ctx = MagicMock()
        mod._ctx.get_setting = MagicMock(return_value=None)
        mod._ctx.set_setting = MagicMock()
        mod._ctx.notify_ui_update = MagicMock()

    def _teardown_module_state(self) -> None:
        import plugins.nowplaying as mod

        mod._store = None
        mod._ctx = None

    @pytest.mark.asyncio
    async def test_handle_action_clear(self, tmp_path: Path) -> None:
        import plugins.nowplaying as mod

        self._setup_module_state(tmp_path)
        try:
            mod._store.record("player-a")
            mod._store.record("player-b")
            assert mod._store.count == 2
            assert mod._store.total == 2

            result = await mod.handle_action("clear", {}, mod._ctx)
            assert "message" in result
            assert "2" in result["message"]
            assert mod._store.count == 0
            assert mod._store.total == 0
            mod._ctx.notify_ui_update.assert_called()
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_handle_action_clear_empty(self, tmp_path: Path) -> None:
        import plugins.nowplaying as mod

        self._setup_module_state(tmp_path)
        try:
            result = await mod.handle_action("clear", {}, mod._ctx)
            assert "message" in result
            assert "0" in result["message"]
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_handle_action_clear_store_none(self, tmp_path: Path) -> None:
        import plugins.nowplaying as mod

        self._setup_module_state(tmp_path)
        mod._store = None
        try:
            result = await mod.handle_action("clear", {}, mod._ctx)
            assert "error" in result
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_handle_action_save_settings(self, tmp_path: Path) -> None:
        import plugins.nowplaying as mod

        self._setup_module_state(tmp_path)
        try:
            result = await mod.handle_action(
                "save_settings",
                {"max_entries": 100},
                mod._ctx,
            )
            assert "message" in result
            assert "max_entries" in result["message"]
            mod._ctx.set_setting.assert_called_once_with("max_entries", 100)
            mod._ctx.notify_ui_update.assert_called()
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_handle_action_save_settings_trims_store(self, tmp_path: Path) -> None:
        import plugins.nowplaying as mod

        self._setup_module_state(tmp_path)
        try:
            for i in range(50):
                mod._store.record(f"player-{i}")
            assert mod._store.count == 50

            result = await mod.handle_action(
                "save_settings",
                {"max_entries": 25},
                mod._ctx,
            )
            assert "message" in result
            assert mod._store.count == 25
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_handle_action_save_settings_invalid(self, tmp_path: Path) -> None:
        import plugins.nowplaying as mod

        self._setup_module_state(tmp_path)
        try:
            result = await mod.handle_action(
                "save_settings",
                {"max_entries": "not_a_number"},
                mod._ctx,
            )
            assert "error" in result
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_handle_action_save_settings_no_changes(self, tmp_path: Path) -> None:
        import plugins.nowplaying as mod

        self._setup_module_state(tmp_path)
        try:
            result = await mod.handle_action(
                "save_settings", {}, mod._ctx,
            )
            assert "message" in result
            assert "No changes" in result["message"]
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_handle_action_unknown(self, tmp_path: Path) -> None:
        import plugins.nowplaying as mod

        self._setup_module_state(tmp_path)
        try:
            result = await mod.handle_action(
                "nonexistent_action", {}, mod._ctx,
            )
            assert "error" in result
            assert "Unknown" in result["error"]
        finally:
            self._teardown_module_state()


class TestSDUISetupRegistration:
    """Test that setup registers SDUI handlers."""

    @pytest.mark.asyncio
    async def test_setup_registers_sdui_handlers(self, tmp_path: Path) -> None:
        import plugins.nowplaying as mod

        old_store = mod._store
        old_ctx = mod._ctx

        try:
            ctx = MagicMock()
            ctx.plugin_id = "nowplaying"
            ctx.data_dir = tmp_path
            ctx.ensure_data_dir = MagicMock(return_value=tmp_path)
            ctx.event_bus = MagicMock()
            ctx.register_command = MagicMock()
            ctx.register_menu_node = MagicMock()
            ctx.register_ui_handler = MagicMock()
            ctx.register_action_handler = MagicMock()
            ctx.subscribe = AsyncMock()
            ctx.get_setting = MagicMock(return_value=None)

            await mod.setup(ctx)

            ctx.register_ui_handler.assert_called_once_with(mod.get_ui)
            ctx.register_action_handler.assert_called_once_with(mod.handle_action)
        finally:
            await mod.teardown(MagicMock())
            mod._store = old_store
            mod._ctx = old_ctx

    @pytest.mark.asyncio
    async def test_teardown_clears_ctx(self, tmp_path: Path) -> None:
        import plugins.nowplaying as mod

        old_store = mod._store
        old_ctx = mod._ctx

        try:
            ctx = MagicMock()
            ctx.plugin_id = "nowplaying"
            ctx.ensure_data_dir = MagicMock(return_value=tmp_path)
            ctx.event_bus = MagicMock()
            ctx.register_command = MagicMock()
            ctx.register_menu_node = MagicMock()
            ctx.register_ui_handler = MagicMock()
            ctx.register_action_handler = MagicMock()
            ctx.subscribe = AsyncMock()
            ctx.get_setting = MagicMock(return_value=None)

            await mod.setup(ctx)
            assert mod._ctx is not None

            await mod.teardown(ctx)
            assert mod._ctx is None
            assert mod._store is None
        finally:
            mod._store = old_store
            mod._ctx = old_ctx


class TestFormatTimestamp:
    """Tests for the _format_timestamp helper."""

    def test_empty_string(self) -> None:
        import plugins.nowplaying as mod

        assert mod._format_timestamp("") == "—"

    def test_none_like(self) -> None:
        import plugins.nowplaying as mod

        assert mod._format_timestamp("") == "—"

    def test_short_string(self) -> None:
        import plugins.nowplaying as mod

        assert mod._format_timestamp("2026") == "—"

    def test_recent_timestamp(self) -> None:
        import time as _time
        from datetime import datetime, timezone

        import plugins.nowplaying as mod

        now = datetime.now(timezone.utc)
        iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        result = mod._format_timestamp(iso)
        assert result == "just now"

    def test_minutes_ago(self) -> None:
        from datetime import datetime, timedelta, timezone

        import plugins.nowplaying as mod

        ts = datetime.now(timezone.utc) - timedelta(minutes=5)
        iso = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
        result = mod._format_timestamp(iso)
        assert "m ago" in result

    def test_hours_ago(self) -> None:
        from datetime import datetime, timedelta, timezone

        import plugins.nowplaying as mod

        ts = datetime.now(timezone.utc) - timedelta(hours=3)
        iso = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
        result = mod._format_timestamp(iso)
        assert "h ago" in result

    def test_yesterday(self) -> None:
        from datetime import datetime, timedelta, timezone

        import plugins.nowplaying as mod

        ts = datetime.now(timezone.utc) - timedelta(days=1)
        iso = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
        result = mod._format_timestamp(iso)
        assert result == "yesterday"

    def test_days_ago(self) -> None:
        from datetime import datetime, timedelta, timezone

        import plugins.nowplaying as mod

        ts = datetime.now(timezone.utc) - timedelta(days=4)
        iso = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
        result = mod._format_timestamp(iso)
        assert "d ago" in result

    def test_old_timestamp_shows_date(self) -> None:
        import plugins.nowplaying as mod

        result = mod._format_timestamp("2025-01-15T14:30:00Z")
        assert "2025-01-15" in result
        assert "14:30:00" in result

    def test_non_z_suffix(self) -> None:
        import plugins.nowplaying as mod

        result = mod._format_timestamp("2025-01-15T14:30:00+00:00")
        assert "2025-01-15" in result

    def test_malformed_falls_back_to_time(self) -> None:
        import plugins.nowplaying as mod

        # Enough chars but not parseable as ISO — should fallback to time slice
        result = mod._format_timestamp("XXXX-XX-XXTXX:XX:XXZ")
        # Falls back to extracting characters 11:19 → "XX:XX:XX"
        assert len(result) > 0
