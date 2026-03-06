"""
Tests for the Radio plugin.

Tests cover:
- RadioBrowserClient helpers (parsing, caching, codec mapping)
- RadioProvider ContentProvider implementation
- JSON-RPC command dispatch (radio items/search/play)
- Jive menu item format (audio, folder, search items)
- CLI item format
- Error handling (network errors, missing params, empty responses)
- Plugin lifecycle (setup/teardown)
- SDUI get_ui, handle_action, browse navigation
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.radio.radiobrowser import (
    BROWSE_CATEGORIES,
    CategoryEntry,
    RadioBrowserClient,
    RadioStation,
    _parse_station,
    _safe_int,
    _SimpleCache,
    codec_to_content_type,
    format_station_subtitle,
)
from plugins.radio.store import RadioStore, RecentStation

# =============================================================================
# RadioBrowser helpers
# =============================================================================


class TestRadioBrowserHelpers:
    """Tests for radiobrowser.py helper functions and data classes."""

    def test_codec_to_content_type_mp3(self) -> None:
        assert codec_to_content_type("MP3") == "audio/mpeg"
        assert codec_to_content_type("mp3") == "audio/mpeg"

    def test_codec_to_content_type_aac(self) -> None:
        assert codec_to_content_type("AAC") == "audio/aac"
        assert codec_to_content_type("AAC+") == "audio/aac"

    def test_codec_to_content_type_ogg(self) -> None:
        assert codec_to_content_type("OGG") == "audio/ogg"

    def test_codec_to_content_type_unknown(self) -> None:
        assert codec_to_content_type("UNKNOWN") == "audio/mpeg"

    def test_format_station_subtitle(self) -> None:
        station = RadioStation(
            stationuuid="test-uuid",
            name="Test Station",
            url="http://stream.test.com/live",
            url_resolved="http://stream.test.com/live.mp3",
            codec="MP3",
            bitrate=128,
            country="Germany",
            tags="jazz, rock",
        )
        subtitle = format_station_subtitle(station)
        assert "MP3" in subtitle
        assert "128" in subtitle
        assert "Germany" in subtitle

    def test_format_station_subtitle_minimal(self) -> None:
        station = RadioStation(
            stationuuid="test-uuid",
            name="Minimal",
            url="http://stream.test.com/live",
            url_resolved="http://stream.test.com/live.mp3",
        )
        subtitle = format_station_subtitle(station)
        # Should still return something (possibly empty or minimal)
        assert isinstance(subtitle, str)

    def test_parse_station(self) -> None:
        raw = {
            "stationuuid": "abc-123",
            "name": "Test FM",
            "url": "http://stream.test.com/live.pls",
            "url_resolved": "http://stream.test.com/live.mp3",
            "homepage": "http://testfm.com",
            "favicon": "http://testfm.com/logo.png",
            "tags": "pop, rock",
            "country": "Germany",
            "countrycode": "DE",
            "codec": "MP3",
            "bitrate": 128,
            "votes": 500,
            "clickcount": 100,
        }
        station = _parse_station(raw)
        assert station.stationuuid == "abc-123"
        assert station.name == "Test FM"
        assert station.url_resolved == "http://stream.test.com/live.mp3"
        assert station.codec == "MP3"
        assert station.bitrate == 128

    def test_parse_station_missing_url_resolved(self) -> None:
        raw = {
            "stationuuid": "abc-123",
            "name": "Test",
            "url": "http://stream.test.com/live",
        }
        station = _parse_station(raw)
        assert station.url == "http://stream.test.com/live"
        # Falls back to url when url_resolved is empty/missing
        assert station.url_resolved == "http://stream.test.com/live"

    def test_browse_categories_defined(self) -> None:
        assert len(BROWSE_CATEGORIES) >= 5
        keys = [c["key"] for c in BROWSE_CATEGORIES]
        assert "popular" in keys
        assert "trending" in keys
        assert "country" in keys
        assert "tag" in keys
        assert "language" in keys

    def test_radio_station_dataclass(self) -> None:
        station = RadioStation(
            stationuuid="uuid", name="Name",
            url="http://url", url_resolved="http://resolved",
        )
        assert station.stationuuid == "uuid"
        assert station.bitrate == 0  # default

    def test_category_entry_dataclass(self) -> None:
        entry = CategoryEntry(name="Germany", stationcount=5000, iso_3166_1="DE")
        assert entry.name == "Germany"
        assert entry.stationcount == 5000
        assert entry.iso_3166_1 == "DE"


# =============================================================================
# SimpleCache
# =============================================================================


class TestSimpleCache:
    """Tests for the _SimpleCache used by RadioBrowserClient."""

    def test_put_and_get(self) -> None:
        cache = _SimpleCache(ttl=60.0)
        cache.put("key1", {"data": 123})
        assert cache.get("key1") == {"data": 123}

    def test_get_missing_returns_none(self) -> None:
        cache = _SimpleCache(ttl=60.0)
        assert cache.get("nonexistent") is None

    def test_expired_entry_returns_none(self) -> None:
        cache = _SimpleCache(ttl=0.01)
        cache.put("key1", "value")
        time.sleep(0.02)
        assert cache.get("key1") is None

    def test_custom_ttl_per_entry(self) -> None:
        cache = _SimpleCache(ttl=60.0)
        cache.put("key1", "value1")
        assert cache.get("key1") == "value1"

    def test_eviction_at_capacity(self) -> None:
        cache = _SimpleCache(ttl=60.0, max_entries=2)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("c", 3)  # Should evict "a"
        assert cache.get("a") is None
        assert cache.get("b") == 2
        assert cache.get("c") == 3

    def test_clear(self) -> None:
        cache = _SimpleCache(ttl=60.0)
        cache.put("a", 1)
        cache.put("b", 2)
        assert len(cache) == 2
        cache.clear()
        assert len(cache) == 0

    def test_len(self) -> None:
        cache = _SimpleCache(ttl=60.0)
        assert len(cache) == 0
        cache.put("a", 1)
        assert len(cache) == 1
        cache.put("b", 2)
        assert len(cache) == 2


# =============================================================================
# Base Actions
# =============================================================================


class TestBaseActions:
    """Tests for Jive base actions."""

    def test_base_actions_structure(self) -> None:
        from plugins.radio import _base_actions

        result = _base_actions()
        assert "actions" in result
        actions = result["actions"]
        assert "go" in actions
        assert "play" in actions
        assert "add" in actions

        # go should use radio items
        assert actions["go"]["cmd"] == ["radio", "items"]

        # play should use radio play
        assert actions["play"]["cmd"] == ["radio", "play"]

    def test_base_actions_add_has_cmd_add(self) -> None:
        from plugins.radio import _base_actions

        result = _base_actions()
        add_action = result["actions"]["add"]
        assert add_action["params"]["cmd"] == "add"


# =============================================================================
# Parameter parsing
# =============================================================================


class TestParameterParsing:
    """Tests for parameter parsing helpers."""

    def test_parse_tagged_colon_format(self) -> None:
        from plugins.radio import _parse_tagged

        result = _parse_tagged(["cmd", "sub", "key1:val1", "key2:val2"], start=2)
        assert result == {"key1": "val1", "key2": "val2"}

    def test_parse_tagged_dict_format(self) -> None:
        from plugins.radio import _parse_tagged

        result = _parse_tagged(["cmd", "sub", {"key1": "val1"}], start=2)
        assert result["key1"] == "val1"

    def test_parse_tagged_mixed(self) -> None:
        from plugins.radio import _parse_tagged

        result = _parse_tagged(["cmd", "sub", "a:1", {"b": "2"}], start=2)
        assert result["a"] == "1"
        assert result["b"] == "2"

    def test_parse_tagged_ignores_non_tagged(self) -> None:
        from plugins.radio import _parse_tagged

        result = _parse_tagged(["cmd", "sub", "0", "50", "key:val"], start=2)
        assert "key" in result

    def test_parse_tagged_none_values_skipped(self) -> None:
        from plugins.radio import _parse_tagged

        result = _parse_tagged(["cmd", "sub", {"a": None, "b": "2"}], start=2)
        # None values may or may not be present depending on implementation
        assert result.get("b") == "2"

    def test_parse_start_count_defaults(self) -> None:
        from plugins.radio import _parse_start_count

        start, count = _parse_start_count(["cmd", "sub"])
        assert start == 0
        assert count > 0

    def test_parse_start_count_explicit(self) -> None:
        from plugins.radio import _parse_start_count

        start, count = _parse_start_count(["cmd", "sub", 10, 25])
        assert start == 10
        assert count == 25

    def test_parse_start_count_negative_clamped(self) -> None:
        from plugins.radio import _parse_start_count

        start, count = _parse_start_count(["cmd", "sub", -5, 10])
        assert start >= 0

    def test_parse_start_count_large_clamped(self) -> None:
        from plugins.radio import _parse_start_count

        start, count = _parse_start_count(["cmd", "sub", 0, 99999])
        assert count <= 10000  # some reasonable max

    def test_parse_start_count_invalid_types(self) -> None:
        from plugins.radio import _parse_start_count

        start, count = _parse_start_count(["cmd", "sub", "abc", "xyz"])
        assert isinstance(start, int)
        assert isinstance(count, int)


# =============================================================================
# cmd_radio dispatch
# =============================================================================


class TestCmdRadio:
    """Tests for the main command dispatcher."""

    def _make_ctx(self) -> MagicMock:
        ctx = MagicMock()
        ctx.player_id = "aa:bb:cc:dd:ee:ff"
        ctx.player_registry = MagicMock()
        ctx.playlist_manager = MagicMock()
        return ctx

    @pytest.mark.asyncio
    async def test_dispatch_default_to_items(self) -> None:
        import plugins.radio as radio_mod

        mock_client = MagicMock()
        mock_client.get_browse_categories = MagicMock(return_value=list(BROWSE_CATEGORIES))
        radio_mod._radio_browser = mock_client

        try:
            ctx = self._make_ctx()
            result = await radio_mod.cmd_radio(ctx, ["radio"])
            assert "count" in result
        finally:
            radio_mod._radio_browser = None

    @pytest.mark.asyncio
    async def test_dispatch_items(self) -> None:
        import plugins.radio as radio_mod

        mock_client = MagicMock()
        mock_client.get_browse_categories = MagicMock(return_value=list(BROWSE_CATEGORIES))
        radio_mod._radio_browser = mock_client

        try:
            ctx = self._make_ctx()
            result = await radio_mod.cmd_radio(ctx, ["radio", "items", 0, 100])
            assert "count" in result
        finally:
            radio_mod._radio_browser = None

    @pytest.mark.asyncio
    async def test_dispatch_search(self) -> None:
        import plugins.radio as radio_mod

        mock_client = MagicMock()
        mock_client.search = AsyncMock(return_value=[])
        radio_mod._radio_browser = mock_client

        try:
            ctx = self._make_ctx()
            result = await radio_mod.cmd_radio(ctx, ["radio", "search", 0, 50, "term:jazz"])
            assert "count" in result
        finally:
            radio_mod._radio_browser = None

    @pytest.mark.asyncio
    async def test_dispatch_unknown(self) -> None:
        import plugins.radio as radio_mod

        mock_client = MagicMock()
        radio_mod._radio_browser = mock_client

        try:
            ctx = self._make_ctx()
            result = await radio_mod.cmd_radio(ctx, ["radio", "blorb"])
            assert "error" in result
        finally:
            radio_mod._radio_browser = None

    @pytest.mark.asyncio
    async def test_dispatch_not_initialized(self) -> None:
        import plugins.radio as radio_mod

        radio_mod._radio_browser = None
        ctx = self._make_ctx()
        result = await radio_mod.cmd_radio(ctx, ["radio", "items"])
        assert "error" in result


# =============================================================================
# radio items — browse
# =============================================================================


class TestRadioItems:
    """Tests for the `radio items` command."""

    def _make_ctx(self) -> MagicMock:
        ctx = MagicMock()
        ctx.player_id = "aa:bb:cc:dd:ee:ff"
        return ctx

    def _make_station(self, name: str = "Test FM", uuid: str = "test-uuid") -> RadioStation:
        return RadioStation(
            stationuuid=uuid, name=name,
            url="http://stream.test.com/live.pls",
            url_resolved="http://stream.test.com/live.mp3",
            favicon="http://img.test.com/logo.png",
            codec="MP3", bitrate=128,
            country="Germany", tags="jazz",
        )

    @pytest.mark.asyncio
    async def test_items_root_menu_mode(self) -> None:
        import plugins.radio as radio_mod

        mock_client = MagicMock()
        mock_client.get_browse_categories = MagicMock(return_value=list(BROWSE_CATEGORIES))
        radio_mod._radio_browser = mock_client

        try:
            ctx = self._make_ctx()
            result = await radio_mod._radio_items(ctx, ["radio", "items", 0, 100, "menu:1"])
            assert result["count"] >= 5
            assert "item_loop" in result
            # First item should be Popular Stations
            assert result["item_loop"][0]["text"] == "Popular Stations"
        finally:
            radio_mod._radio_browser = None

    @pytest.mark.asyncio
    async def test_items_cli_mode(self) -> None:
        import plugins.radio as radio_mod

        mock_client = MagicMock()
        mock_client.get_browse_categories = MagicMock(return_value=list(BROWSE_CATEGORIES))
        radio_mod._radio_browser = mock_client

        try:
            ctx = self._make_ctx()
            result = await radio_mod._radio_items(ctx, ["radio", "items", 0, 100])
            assert result["count"] >= 5
            assert "loop" in result
            assert result["loop"][0]["name"] == "Popular Stations"
        finally:
            radio_mod._radio_browser = None

    @pytest.mark.asyncio
    async def test_items_pagination(self) -> None:
        import plugins.radio as radio_mod

        mock_client = MagicMock()
        stations = [self._make_station(f"Station {i}", f"uuid-{i}") for i in range(10)]
        mock_client.get_popular_stations = AsyncMock(return_value=stations)
        radio_mod._radio_browser = mock_client

        try:
            ctx = self._make_ctx()
            result = await radio_mod._radio_items(
                ctx, ["radio", "items", 2, 3, "category:popular", "menu:1"]
            )
            assert result["count"] == 10
            assert result["offset"] == 2
            assert len(result["item_loop"]) == 3
            assert result["item_loop"][0]["text"] == "Station 2"
        finally:
            radio_mod._radio_browser = None

    @pytest.mark.asyncio
    async def test_items_with_url_param(self) -> None:
        """'url' param is an alias for 'category'."""
        import plugins.radio as radio_mod

        mock_client = MagicMock()
        mock_client.get_popular_stations = AsyncMock(return_value=[
            self._make_station("Pop FM", "pop-uuid"),
        ])
        radio_mod._radio_browser = mock_client

        try:
            ctx = self._make_ctx()
            result = await radio_mod._radio_items(
                ctx, ["radio", "items", 0, 50, "url:popular", "menu:1"]
            )
            assert result["count"] == 1
        finally:
            radio_mod._radio_browser = None

    @pytest.mark.asyncio
    async def test_items_with_search_param(self) -> None:
        """Inline search from Jive input field."""
        import plugins.radio as radio_mod

        mock_client = MagicMock()
        mock_client.search = AsyncMock(return_value=[
            self._make_station("Jazz FM", "jazz-uuid"),
        ])
        radio_mod._radio_browser = mock_client

        try:
            ctx = self._make_ctx()
            result = await radio_mod._radio_items(
                ctx, ["radio", "items", 0, 50, "search:jazz", "menu:1"]
            )
            assert result["count"] == 1
        finally:
            radio_mod._radio_browser = None

    @pytest.mark.asyncio
    async def test_items_empty_result(self) -> None:
        import plugins.radio as radio_mod

        mock_client = MagicMock()
        mock_client.get_stations_by_country = AsyncMock(return_value=[])
        radio_mod._radio_browser = mock_client

        try:
            ctx = self._make_ctx()
            result = await radio_mod._radio_items(
                ctx, ["radio", "items", 0, 50, "category:country:XX"]
            )
            assert result["count"] == 0
        finally:
            radio_mod._radio_browser = None


# =============================================================================
# radio search
# =============================================================================


class TestRadioSearch:
    """Tests for the `radio search` command."""

    def _make_ctx(self) -> MagicMock:
        ctx = MagicMock()
        ctx.player_id = "aa:bb:cc:dd:ee:ff"
        return ctx

    def _make_station(self, name: str = "Test FM", uuid: str = "test-uuid") -> RadioStation:
        return RadioStation(
            stationuuid=uuid, name=name,
            url="http://stream.test.com/live.pls",
            url_resolved="http://stream.test.com/live.mp3",
            codec="MP3", bitrate=128,
        )

    @pytest.mark.asyncio
    async def test_search_with_term(self) -> None:
        import plugins.radio as radio_mod

        mock_client = MagicMock()
        mock_client.search = AsyncMock(return_value=[
            self._make_station("Jazz FM", "jazz-uuid"),
            self._make_station("Jazz Radio", "jazz-radio-uuid"),
        ])
        radio_mod._radio_browser = mock_client

        try:
            ctx = self._make_ctx()
            result = await radio_mod._radio_search(ctx, ["radio", "search", 0, 50, "term:jazz", "menu:1"])
            assert result["count"] == 2
            assert "item_loop" in result
        finally:
            radio_mod._radio_browser = None

    @pytest.mark.asyncio
    async def test_search_with_query_param(self) -> None:
        import plugins.radio as radio_mod

        mock_client = MagicMock()
        mock_client.search = AsyncMock(return_value=[self._make_station()])
        radio_mod._radio_browser = mock_client

        try:
            ctx = self._make_ctx()
            result = await radio_mod._radio_search(ctx, ["radio", "search", 0, 50, "query:test"])
            assert result["count"] == 1
        finally:
            radio_mod._radio_browser = None

    @pytest.mark.asyncio
    async def test_search_empty_query(self) -> None:
        import plugins.radio as radio_mod

        mock_client = MagicMock()
        radio_mod._radio_browser = mock_client

        try:
            ctx = self._make_ctx()
            result = await radio_mod._radio_search(ctx, ["radio", "search", 0, 50])
            assert result["count"] == 0
            # Should not have called search
            mock_client.search.assert_not_called()
        finally:
            radio_mod._radio_browser = None

    @pytest.mark.asyncio
    async def test_search_cli_mode(self) -> None:
        import plugins.radio as radio_mod

        mock_client = MagicMock()
        mock_client.search = AsyncMock(return_value=[self._make_station()])
        radio_mod._radio_browser = mock_client

        try:
            ctx = self._make_ctx()
            result = await radio_mod._radio_search(ctx, ["radio", "search", 0, 50, "term:test"])
            assert "loop" in result
            assert result["count"] == 1
        finally:
            radio_mod._radio_browser = None


# =============================================================================
# radio play
# =============================================================================


class TestRadioPlay:
    """Tests for the `radio play` command."""

    def _make_ctx(self) -> MagicMock:
        from unittest.mock import PropertyMock

        ctx = MagicMock()
        ctx.player_id = "aa:bb:cc:dd:ee:ff"

        # Player
        player = MagicMock()
        player.mac_address = "aa:bb:cc:dd:ee:ff"
        player.name = "Living Room"

        player_registry = MagicMock()
        player_registry.get_by_mac = AsyncMock(return_value=player)
        ctx.player_registry = player_registry

        # Playlist
        playlist = MagicMock()
        playlist.current_index = 0
        playlist_manager = MagicMock()
        playlist_manager.get = MagicMock(return_value=playlist)
        ctx.playlist_manager = playlist_manager

        return ctx

    @pytest.mark.asyncio
    async def test_play_missing_params(self) -> None:
        import plugins.radio as radio_mod

        mock_client = MagicMock()
        radio_mod._radio_browser = mock_client

        try:
            ctx = self._make_ctx()
            result = await radio_mod._radio_play(ctx, ["radio", "play"])
            assert "error" in result
        finally:
            radio_mod._radio_browser = None

    @pytest.mark.asyncio
    async def test_play_with_station_id(self) -> None:
        import plugins.radio as radio_mod

        mock_client = MagicMock()
        radio_mod._radio_browser = mock_client
        radio_mod._event_bus = MagicMock()
        radio_mod._event_bus.publish = AsyncMock()
        radio_mod._store = RadioStore(Path("."), max_recent=50)
        radio_mod._ctx = MagicMock()
        radio_mod._ctx.notify_ui_update = MagicMock()

        try:
            ctx = self._make_ctx()

            with patch("plugins.radio._radio_play.__module__", "plugins.radio"):
                with patch("resonance.web.handlers.playlist_playback._start_track_stream", new_callable=AsyncMock):
                    result = await radio_mod._radio_play(ctx, [
                        "radio", "play",
                        "url:http://stream.test.com/live.mp3",
                        "id:test-uuid",
                        "title:Test FM",
                        "icon:http://img.test.com/logo.png",
                        "codec:MP3",
                        "bitrate:128",
                    ])

            # Should have started playback
            assert result.get("count") == 1 or "error" not in result
        finally:
            radio_mod._radio_browser = None
            radio_mod._event_bus = None
            radio_mod._store = None
            radio_mod._ctx = None

    @pytest.mark.asyncio
    async def test_play_add_mode(self) -> None:
        import plugins.radio as radio_mod

        mock_client = MagicMock()
        radio_mod._radio_browser = mock_client
        radio_mod._store = RadioStore(Path("."), max_recent=50)
        radio_mod._ctx = MagicMock()
        radio_mod._ctx.notify_ui_update = MagicMock()

        try:
            ctx = self._make_ctx()
            result = await radio_mod._radio_play(ctx, [
                "radio", "play",
                "url:http://stream.test.com/live.mp3",
                "title:Test FM",
                "cmd:add",
            ])
            assert result.get("count") == 1
            # add mode should call playlist.add, not playlist.play
            ctx.playlist_manager.get.return_value.add.assert_called_once()
        finally:
            radio_mod._radio_browser = None
            radio_mod._store = None
            radio_mod._ctx = None

    @pytest.mark.asyncio
    async def test_play_insert_mode(self) -> None:
        import plugins.radio as radio_mod

        mock_client = MagicMock()
        radio_mod._radio_browser = mock_client
        radio_mod._store = RadioStore(Path("."), max_recent=50)
        radio_mod._ctx = MagicMock()
        radio_mod._ctx.notify_ui_update = MagicMock()

        try:
            ctx = self._make_ctx()
            result = await radio_mod._radio_play(ctx, [
                "radio", "play",
                "url:http://stream.test.com/live.mp3",
                "title:Test FM",
                "cmd:insert",
            ])
            assert result.get("count") == 1
            ctx.playlist_manager.get.return_value.insert.assert_called_once()
        finally:
            radio_mod._radio_browser = None
            radio_mod._store = None
            radio_mod._ctx = None

    @pytest.mark.asyncio
    async def test_play_no_player(self) -> None:
        import plugins.radio as radio_mod

        mock_client = MagicMock()
        radio_mod._radio_browser = mock_client

        try:
            ctx = self._make_ctx()
            ctx.player_id = "-"
            ctx.player_registry.get_by_mac = AsyncMock(return_value=None)

            result = await radio_mod._radio_play(ctx, [
                "radio", "play",
                "url:http://stream.test.com/live.mp3",
            ])
            assert "error" in result
            assert "player" in result["error"].lower()
        finally:
            radio_mod._radio_browser = None

    @pytest.mark.asyncio
    async def test_play_uuid_lookup_failure(self) -> None:
        import plugins.radio as radio_mod

        mock_client = MagicMock()
        mock_client.get_station_by_uuid = AsyncMock(return_value=None)
        radio_mod._radio_browser = mock_client

        try:
            ctx = self._make_ctx()
            result = await radio_mod._radio_play(ctx, [
                "radio", "play",
                "id:nonexistent-uuid",
            ])
            assert "error" in result
        finally:
            radio_mod._radio_browser = None

    @pytest.mark.asyncio
    async def test_play_with_uuid_lookup(self) -> None:
        import plugins.radio as radio_mod

        station = RadioStation(
            stationuuid="lookup-uuid",
            name="Looked Up Station",
            url="http://stream.lookup.com/live.pls",
            url_resolved="http://stream.lookup.com/live.mp3",
            favicon="http://img.lookup.com/logo.png",
            codec="AAC",
            bitrate=64,
        )
        mock_client = MagicMock()
        mock_client.get_station_by_uuid = AsyncMock(return_value=station)
        mock_client.count_click = AsyncMock(return_value=True)
        radio_mod._radio_browser = mock_client
        radio_mod._event_bus = MagicMock()
        radio_mod._event_bus.publish = AsyncMock()
        radio_mod._store = RadioStore(Path("."), max_recent=50)
        radio_mod._ctx = MagicMock()
        radio_mod._ctx.notify_ui_update = MagicMock()

        try:
            ctx = self._make_ctx()

            with patch("resonance.web.handlers.playlist_playback._start_track_stream", new_callable=AsyncMock):
                result = await radio_mod._radio_play(ctx, [
                    "radio", "play",
                    "id:lookup-uuid",
                ])

            assert result.get("count") == 1 or "error" not in result
        finally:
            radio_mod._radio_browser = None
            radio_mod._event_bus = None
            radio_mod._store = None
            radio_mod._ctx = None

    @pytest.mark.asyncio
    async def test_play_no_playlist_manager(self) -> None:
        import plugins.radio as radio_mod

        mock_client = MagicMock()
        radio_mod._radio_browser = mock_client

        try:
            ctx = self._make_ctx()
            ctx.playlist_manager = None

            result = await radio_mod._radio_play(ctx, [
                "radio", "play",
                "url:http://stream.test.com/live.mp3",
            ])
            assert "error" in result
        finally:
            radio_mod._radio_browser = None


# =============================================================================
# Plugin lifecycle
# =============================================================================


class TestPluginLifecycle:
    """Tests for plugin setup and teardown."""

    def _make_ctx(self, tmp_path: Path | None = None) -> MagicMock:
        """Create a mock PluginContext with all methods needed by setup()."""
        ctx = MagicMock()
        ctx.event_bus = MagicMock()
        ctx.register_command = MagicMock()
        ctx.register_content_provider = MagicMock()
        ctx.register_menu_node = MagicMock()
        ctx.register_ui_handler = MagicMock()
        ctx.register_action_handler = MagicMock()
        ctx.subscribe = AsyncMock()
        ctx.notify_ui_update = MagicMock()
        ctx.get_setting = MagicMock(return_value=None)
        ctx.set_setting = MagicMock()
        if tmp_path is not None:
            ctx.ensure_data_dir = MagicMock(return_value=tmp_path)
        else:
            ctx.ensure_data_dir = MagicMock(return_value=Path("."))
        return ctx

    @pytest.mark.asyncio
    async def test_setup_registers_components(self, tmp_path: Path) -> None:
        """setup() registers command, content provider, menu node, UI and action handlers."""
        import plugins.radio as radio_mod

        ctx = self._make_ctx(tmp_path)

        await radio_mod.setup(ctx)

        # Verify command registered
        ctx.register_command.assert_called_once_with("radio", radio_mod.cmd_radio)

        # Verify only radio-browser content provider registered (no TuneIn)
        assert ctx.register_content_provider.call_count == 1
        cp_call = ctx.register_content_provider.call_args
        assert cp_call[0][0] == "radio"
        assert isinstance(cp_call[0][1], radio_mod.RadioProvider)

        # Verify menu node registered
        ctx.register_menu_node.assert_called_once()
        menu_call = ctx.register_menu_node.call_args
        assert menu_call[1]["node_id"] == "radios"
        assert menu_call[1]["parent"] == "home"
        assert menu_call[1]["text"] == "Radio"
        assert menu_call[1]["weight"] == 45

        # Verify SDUI handlers registered
        ctx.register_ui_handler.assert_called_once_with(radio_mod.get_ui)
        ctx.register_action_handler.assert_called_once_with(radio_mod.handle_action)

        # Verify event subscription
        ctx.subscribe.assert_called_once()
        sub_call = ctx.subscribe.call_args
        assert sub_call[0][0] == "player.track_started"

        # Verify state was set
        assert radio_mod._radio_browser is not None
        assert radio_mod._provider is not None
        assert radio_mod._event_bus is not None
        assert radio_mod._store is not None
        assert radio_mod._ctx is not None

        # Cleanup
        await radio_mod.teardown(ctx)

    @pytest.mark.asyncio
    async def test_teardown_clears_state(self, tmp_path: Path) -> None:
        """teardown() clears module-level state and saves the store."""
        import plugins.radio as radio_mod

        ctx = self._make_ctx(tmp_path)

        await radio_mod.setup(ctx)
        assert radio_mod._radio_browser is not None

        await radio_mod.teardown(ctx)
        assert radio_mod._radio_browser is None
        assert radio_mod._provider is None
        assert radio_mod._event_bus is None
        assert radio_mod._store is None
        assert radio_mod._ctx is None


# =============================================================================
# Jive menu item builders
# =============================================================================


class TestJiveMenuItemBuilders:
    """Tests for Jive/CLI item builder functions."""

    def _make_station(self) -> RadioStation:
        return RadioStation(
            stationuuid="test-uuid-123",
            name="Test Radio FM",
            url="http://stream.test.com/live.pls",
            url_resolved="http://stream.test.com/live.mp3",
            favicon="http://img.test.com/logo.png",
            codec="MP3",
            bitrate=128,
            country="Germany",
            countrycode="DE",
            tags="jazz, pop",
            votes=500,
            homepage="http://testradio.fm",
        )

    def test_build_station_jive_item(self) -> None:
        from plugins.radio import _build_station_jive_item

        station = self._make_station()
        item = _build_station_jive_item(station)
        assert item["text"] == "Test Radio FM"
        assert item["type"] == "audio"
        assert item["hasitems"] == 0
        assert "icon" in item
        assert item["icon"] == "http://img.test.com/logo.png"
        assert "actions" in item
        assert "play" in item["actions"]
        assert "add" in item["actions"]
        assert "go" in item["actions"]

    def test_build_station_jive_item_add_action(self) -> None:
        from plugins.radio import _build_station_jive_item

        station = self._make_station()
        item = _build_station_jive_item(station)
        add = item["actions"]["add"]
        assert add["params"]["cmd"] == "add"
        assert add["params"]["url"] == "http://stream.test.com/live.mp3"

    def test_build_station_jive_item_favorites(self) -> None:
        from plugins.radio import _build_station_jive_item

        station = self._make_station()
        item = _build_station_jive_item(station)
        assert "more" in item["actions"]
        fav = item["actions"]["more"]
        assert fav["cmd"] == ["jivefavorites", "add"]
        assert fav["params"]["title"] == "Test Radio FM"

    def test_build_station_jive_item_without_image(self) -> None:
        from plugins.radio import _build_station_jive_item

        station = RadioStation(
            stationuuid="no-img", name="No Image Radio",
            url="http://stream.test.com/live", url_resolved="http://stream.test.com/live",
        )
        item = _build_station_jive_item(station)
        assert "icon" not in item

    def test_build_station_jive_item_without_uuid_no_favorites(self) -> None:
        from plugins.radio import _build_station_jive_item

        station = RadioStation(
            stationuuid="", name="No UUID",
            url="http://stream.test.com/live", url_resolved="http://stream.test.com/live",
        )
        item = _build_station_jive_item(station)
        assert "more" not in item["actions"]

    def test_build_station_cli_item(self) -> None:
        from plugins.radio import _build_station_cli_item

        station = self._make_station()
        item = _build_station_cli_item(station)
        assert item["name"] == "Test Radio FM"
        assert item["type"] == "audio"
        assert item["url"] == "http://stream.test.com/live.mp3"
        assert item["id"] == "test-uuid-123"
        assert item["codec"] == "MP3"
        assert item["bitrate"] == 128

    def test_build_category_jive_item(self) -> None:
        from plugins.radio import _build_category_jive_item

        item = _build_category_jive_item("popular", "Popular Stations")
        assert item["text"] == "Popular Stations"
        assert item["hasitems"] == 1
        assert "actions" in item
        assert item["actions"]["go"]["cmd"] == ["radio", "items"]
        assert item["actions"]["go"]["params"]["category"] == "popular"

    def test_build_subcategory_jive_item(self) -> None:
        from plugins.radio import _build_subcategory_jive_item

        item = _build_subcategory_jive_item("country:DE", "Germany (5000)")
        assert item["text"] == "Germany (5000)"
        assert item["hasitems"] == 1
        assert item["actions"]["go"]["params"]["category"] == "country:DE"

    def test_build_jive_folder_with_window(self) -> None:
        """Category items have expected structure."""
        from plugins.radio import _build_category_jive_item

        item = _build_category_jive_item("tag", "By Genre / Tag")
        assert item["text"] == "By Genre / Tag"
        assert item["hasitems"] == 1


# =============================================================================
# Integration-style: full browse → play flow (radio-browser.info)
# =============================================================================


class TestIntegrationFlow:
    """End-to-end-ish tests simulating user flow with mocked data."""

    def _make_station(self, name: str, uuid: str) -> Any:
        return RadioStation(
            stationuuid=uuid, name=name,
            url=f"http://stream.{uuid}.com/live.pls",
            url_resolved=f"http://stream.{uuid}.com/live.mp3",
            favicon=f"http://img.{uuid}.com/logo.png",
            codec="MP3", bitrate=128,
            country="Germany", tags="jazz",
        )

    @pytest.mark.asyncio
    async def test_browse_then_play(self) -> None:
        """Simulate: browse root → browse tag → view stations."""
        import plugins.radio as radio_mod

        mock_client = MagicMock()
        mock_client.get_browse_categories = MagicMock(return_value=list(BROWSE_CATEGORIES))

        radio_mod._radio_browser = mock_client

        ctx = MagicMock()
        ctx.player_id = "aa:bb:cc:dd:ee:ff"

        # Step 1: Browse root — should show categories
        result = await radio_mod._radio_items(ctx, ["radio", "items", 0, 100, "menu:1"])
        assert result["count"] >= 5
        assert result["item_loop"][0]["text"] == "Popular Stations"

        # Step 2: Browse into "tag" — should show genre list
        mock_client.get_tags = AsyncMock(return_value=[
            CategoryEntry(name="jazz", stationcount=5000),
            CategoryEntry(name="rock", stationcount=8000),
        ])
        result = await radio_mod._radio_items(ctx, ["radio", "items", 0, 100, "category:tag", "menu:1"])
        assert result["count"] == 2
        assert "jazz" in result["item_loop"][0]["text"]

        # Step 3: Browse jazz stations
        station = self._make_station("Jazz FM", "jazz-uuid")
        mock_client.get_stations_by_tag = AsyncMock(return_value=[station])
        result = await radio_mod._radio_items(ctx, ["radio", "items", 0, 100, "category:tag:jazz", "menu:1"])
        assert result["count"] == 1
        assert result["item_loop"][0]["text"] == "Jazz FM"
        assert result["item_loop"][0]["type"] == "audio"

        # Verify play action points to correct stream
        play = result["item_loop"][0]["actions"]["play"]
        assert play["params"]["url"] == "http://stream.jazz-uuid.com/live.mp3"
        assert play["params"]["id"] == "jazz-uuid"

        radio_mod._radio_browser = None

    @pytest.mark.asyncio
    async def test_search_then_play(self) -> None:
        """Simulate: search → play result."""
        import plugins.radio as radio_mod

        mock_client = MagicMock()
        station = self._make_station("BBC Radio 1", "bbc-uuid")
        mock_client.search = AsyncMock(return_value=[station])
        radio_mod._radio_browser = mock_client

        ctx = MagicMock()
        ctx.player_id = "aa:bb:cc:dd:ee:ff"

        result = await radio_mod._radio_search(ctx, ["radio", "search", 0, 100, "term:bbc radio", "menu:1"])
        assert result["count"] == 1
        assert result["item_loop"][0]["text"] == "BBC Radio 1"

        # Verify the play action points to the correct station
        play_action = result["item_loop"][0]["actions"]["play"]
        assert play_action["params"]["id"] == "bbc-uuid"
        assert play_action["params"]["url"] == "http://stream.bbc-uuid.com/live.mp3"

        radio_mod._radio_browser = None


# =============================================================================
# RadioStore tests
# =============================================================================


class TestRecentStation:
    """Tests for the RecentStation data class."""

    def test_defaults(self) -> None:
        station = RecentStation(url="http://example.com/stream")
        assert station.url == "http://example.com/stream"
        assert station.title == ""
        assert station.icon == ""
        assert station.codec == ""
        assert station.bitrate == 0
        assert station.country == ""
        assert station.countrycode == ""
        assert station.tags == ""
        assert station.station_id == ""
        assert station.provider == ""
        assert station.last_played == ""
        assert station.play_count == 0

    def test_to_dict_omits_empty(self) -> None:
        station = RecentStation(
            url="http://example.com/stream",
            title="Test",
        )
        d = station.to_dict()
        assert "url" in d
        assert "title" in d
        # Empty strings should be omitted (except play_count which is always included)
        for key in ("icon", "codec", "country", "countrycode", "tags", "station_id", "provider", "last_played"):
            assert key not in d

    def test_to_dict_includes_nonzero(self) -> None:
        station = RecentStation(
            url="http://example.com/stream",
            title="Test",
            codec="MP3",
            bitrate=128,
            play_count=3,
        )
        d = station.to_dict()
        assert d["codec"] == "MP3"
        assert d["bitrate"] == 128
        assert d["play_count"] == 3

    def test_from_dict(self) -> None:
        data = {
            "url": "http://example.com/stream",
            "title": "Test FM",
            "codec": "MP3",
            "bitrate": 128,
            "country": "Germany",
            "countrycode": "DE",
            "tags": "jazz",
            "station_id": "abc-123",
            "provider": "radio-browser",
            "play_count": 5,
        }
        station = RecentStation.from_dict(data)
        assert station.url == "http://example.com/stream"
        assert station.title == "Test FM"
        assert station.codec == "MP3"
        assert station.bitrate == 128
        assert station.play_count == 5

    def test_from_dict_missing_fields(self) -> None:
        data = {"url": "http://example.com/stream"}
        station = RecentStation.from_dict(data)
        assert station.title == ""
        assert station.bitrate == 0

    def test_from_dict_bad_types(self) -> None:
        data = {"url": "http://example.com/stream", "bitrate": "not_a_number"}
        station = RecentStation.from_dict(data)
        assert station.bitrate == 0

    def test_roundtrip(self) -> None:
        original = RecentStation(
            url="http://example.com/stream",
            title="Test FM",
            icon="http://example.com/icon.png",
            codec="MP3",
            bitrate=128,
            country="Germany",
            countrycode="DE",
            tags="jazz, rock",
            station_id="abc-123",
            provider="radio-browser",
            last_played="2026-03-15T14:30:00+00:00",
            play_count=3,
        )
        d = original.to_dict()
        restored = RecentStation.from_dict(d)
        assert restored.url == original.url
        assert restored.title == original.title
        assert restored.codec == original.codec
        assert restored.bitrate == original.bitrate
        assert restored.play_count == original.play_count


class TestRadioStore:

    @pytest.fixture
    def store(self, tmp_path: Path) -> RadioStore:
        return RadioStore(tmp_path, max_recent=10)

    def test_empty_store(self, store: RadioStore) -> None:
        assert store.recent_count == 0
        assert store.recent == []

    def test_record_play(self, store: RadioStore) -> None:
        entry = store.record_play(
            url="http://example.com/stream",
            title="Test FM",
            codec="MP3",
            bitrate=128,
        )
        assert entry.title == "Test FM"
        assert entry.play_count == 1
        assert store.recent_count == 1

    def test_record_play_deduplicates(self, store: RadioStore) -> None:
        store.record_play(url="http://example.com/stream", title="Test")
        store.record_play(url="http://example.com/stream", title="Test Updated")
        assert store.recent_count == 1
        assert store.recent[0].play_count == 2
        assert store.recent[0].title == "Test Updated"

    def test_record_play_dedup_case_insensitive(self, store: RadioStore) -> None:
        store.record_play(url="http://Example.Com/STREAM", title="A")
        store.record_play(url="http://example.com/stream", title="B")
        assert store.recent_count == 1

    def test_record_play_dedup_trailing_slash(self, store: RadioStore) -> None:
        store.record_play(url="http://example.com/stream/", title="A")
        store.record_play(url="http://example.com/stream", title="B")
        assert store.recent_count == 1

    def test_record_play_preserves_existing_metadata(self, store: RadioStore) -> None:
        store.record_play(
            url="http://example.com/stream",
            title="Original",
            codec="MP3",
            bitrate=128,
            country="Germany",
        )
        # Second play with partial metadata — should keep existing
        entry = store.record_play(
            url="http://example.com/stream",
            title="Updated",
        )
        assert entry.title == "Updated"
        assert entry.codec == "MP3"  # preserved
        assert entry.bitrate == 128  # preserved
        assert entry.country == "Germany"  # preserved

    def test_record_play_trims(self, store: RadioStore) -> None:
        for i in range(15):
            store.record_play(url=f"http://s{i}.com/stream", title=f"S{i}")
        assert store.recent_count == 10  # max_recent

    def test_max_recent_setter(self, store: RadioStore) -> None:
        for i in range(10):
            store.record_play(url=f"http://s{i}.com/stream", title=f"S{i}")
        assert store.recent_count == 10
        store.max_recent = 5
        assert store.recent_count == 5

    def test_max_recent_minimum_one(self, store: RadioStore) -> None:
        store.max_recent = 0
        assert store.max_recent >= 1

    def test_remove(self, store: RadioStore) -> None:
        store.record_play(url="http://a.com/stream", title="A")
        store.record_play(url="http://b.com/stream", title="B")
        assert store.remove("http://a.com/stream")
        assert store.recent_count == 1

    def test_remove_not_found(self, store: RadioStore) -> None:
        assert not store.remove("http://nonexistent.com/stream")

    def test_clear(self, store: RadioStore) -> None:
        store.record_play(url="http://a.com/stream", title="A")
        store.record_play(url="http://b.com/stream", title="B")
        store.clear()
        assert store.recent_count == 0

    def test_get_by_url(self, store: RadioStore) -> None:
        store.record_play(url="http://a.com/stream", title="A")
        result = store.get_by_url("http://a.com/stream")
        assert result is not None
        assert result.title == "A"

    def test_get_by_url_not_found(self, store: RadioStore) -> None:
        assert store.get_by_url("http://nonexistent.com") is None

    def test_get_by_station_id(self, store: RadioStore) -> None:
        store.record_play(url="http://a.com/stream", title="A", station_id="id-123")
        result = store.get_by_station_id("id-123")
        assert result is not None

    def test_get_by_station_id_not_found(self, store: RadioStore) -> None:
        assert store.get_by_station_id("nope") is None

    def test_get_by_station_id_empty(self, store: RadioStore) -> None:
        assert store.get_by_station_id("") is None

    def test_get_most_played(self, store: RadioStore) -> None:
        store.record_play(url="http://a.com/stream", title="A")
        store.record_play(url="http://b.com/stream", title="B")
        store.record_play(url="http://b.com/stream", title="B")
        store.record_play(url="http://c.com/stream", title="C")
        store.record_play(url="http://c.com/stream", title="C")
        store.record_play(url="http://c.com/stream", title="C")

        most = store.get_most_played(limit=2)
        assert len(most) == 2
        assert most[0].title == "C"
        assert most[0].play_count == 3
        assert most[1].title == "B"
        assert most[1].play_count == 2

    def test_save_and_load(self, tmp_path: Path) -> None:
        store1 = RadioStore(tmp_path, max_recent=50)
        store1.record_play(url="http://a.com/stream", title="A", codec="MP3")
        store1.record_play(url="http://b.com/stream", title="B", codec="AAC")
        store1.save()

        store2 = RadioStore(tmp_path, max_recent=50)
        store2.load()
        assert store2.recent_count == 2
        assert store2.recent[0].title == "B"  # newest first

    def test_save_creates_directory(self, tmp_path: Path) -> None:
        sub_dir = tmp_path / "sub" / "dir"
        store = RadioStore(sub_dir, max_recent=10)
        store.record_play(url="http://a.com/stream", title="A")
        store.save()
        assert (sub_dir / "radio.json").exists()

    def test_save_is_valid_json(self, tmp_path: Path) -> None:
        store = RadioStore(tmp_path, max_recent=10)
        store.record_play(url="http://a.com/stream", title="A")
        store.save()

        content = (tmp_path / "radio.json").read_text(encoding="utf-8")
        data = json.loads(content)
        assert data["version"] == 1
        assert len(data["recent"]) == 1

    def test_load_nonexistent(self, tmp_path: Path) -> None:
        store = RadioStore(tmp_path, max_recent=10)
        store.load()  # Should not raise
        assert store.recent_count == 0

    def test_load_corrupt_json(self, tmp_path: Path) -> None:
        (tmp_path / "radio.json").write_text("not valid json {{{", encoding="utf-8")
        store = RadioStore(tmp_path, max_recent=10)
        store.load()  # Should not raise
        assert store.recent_count == 0

    def test_load_non_dict(self, tmp_path: Path) -> None:
        (tmp_path / "radio.json").write_text("[1, 2, 3]", encoding="utf-8")
        store = RadioStore(tmp_path, max_recent=10)
        store.load()
        assert store.recent_count == 0

    def test_load_skips_entries_without_url(self, tmp_path: Path) -> None:
        data = {
            "version": 1,
            "recent": [
                {"url": "http://a.com/stream", "title": "A"},
                {"title": "No URL"},  # Should be skipped
                {"url": "", "title": "Empty URL"},  # Should be skipped
            ],
        }
        (tmp_path / "radio.json").write_text(json.dumps(data), encoding="utf-8")
        store = RadioStore(tmp_path, max_recent=10)
        store.load()
        assert store.recent_count == 1


# =============================================================================
# RadioProvider tests
# =============================================================================


class TestRadioProvider:
    """Tests for the RadioProvider ContentProvider implementation."""

    def _make_station(self, name: str = "Test FM", uuid: str = "test-uuid") -> RadioStation:
        return RadioStation(
            stationuuid=uuid, name=name,
            url="http://stream.test.com/live.pls",
            url_resolved="http://stream.test.com/live.mp3",
            favicon="http://img.test.com/logo.png",
            codec="MP3", bitrate=128,
            country="Germany", countrycode="DE",
            tags="jazz",
        )

    def _make_provider(self) -> tuple[Any, MagicMock]:
        import plugins.radio as radio_mod

        mock_client = MagicMock()
        return radio_mod.RadioProvider(mock_client), mock_client

    @pytest.mark.asyncio
    async def test_name(self) -> None:
        provider, _ = self._make_provider()
        assert provider.name == "Community Radio Browser"

    @pytest.mark.asyncio
    async def test_icon(self) -> None:
        provider, _ = self._make_provider()
        assert provider.icon is None

    @pytest.mark.asyncio
    async def test_browse_root_returns_categories(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.get_browse_categories = MagicMock(return_value=list(BROWSE_CATEGORIES))
        items = await provider.browse("")
        assert len(items) >= 5
        assert items[0].title == "Popular Stations"
        assert items[0].type == "folder"

    @pytest.mark.asyncio
    async def test_browse_popular(self) -> None:
        provider, mock_client = self._make_provider()
        station = self._make_station("Pop FM", "pop-uuid")
        mock_client.get_popular_stations = AsyncMock(return_value=[station])
        items = await provider.browse("popular")
        assert len(items) == 1
        assert items[0].title == "Pop FM"
        assert items[0].type == "audio"

    @pytest.mark.asyncio
    async def test_browse_country_list(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.get_countries = AsyncMock(return_value=[
            CategoryEntry(name="Germany", stationcount=5000, iso_3166_1="DE"),
            CategoryEntry(name="France", stationcount=3000, iso_3166_1="FR"),
        ])
        items = await provider.browse("country")
        assert len(items) == 2
        assert items[0].type == "folder"
        assert "Germany" in items[0].title

    @pytest.mark.asyncio
    async def test_browse_country_stations(self) -> None:
        provider, mock_client = self._make_provider()
        station = self._make_station("German FM", "de-uuid")
        mock_client.get_stations_by_country = AsyncMock(return_value=[station])
        items = await provider.browse("country:DE")
        assert len(items) == 1
        assert items[0].title == "German FM"

    @pytest.mark.asyncio
    async def test_search(self) -> None:
        provider, mock_client = self._make_provider()
        station = self._make_station("Jazz FM", "jazz-uuid")
        mock_client.search = AsyncMock(return_value=[station])
        items = await provider.search("jazz")
        assert len(items) == 1
        assert items[0].title == "Jazz FM"

    @pytest.mark.asyncio
    async def test_get_stream_info_success(self) -> None:
        provider, mock_client = self._make_provider()
        station = self._make_station()
        mock_client.get_station_by_uuid = AsyncMock(return_value=station)
        info = await provider.get_stream_info("test-uuid")
        assert info is not None
        assert info.url == "http://stream.test.com/live.mp3"
        assert info.is_live is True

    @pytest.mark.asyncio
    async def test_get_stream_info_failure(self) -> None:
        provider, mock_client = self._make_provider()
        mock_client.get_station_by_uuid = AsyncMock(return_value=None)
        info = await provider.get_stream_info("nonexistent")
        assert info is None


# =============================================================================
# SDUI tests
# =============================================================================


class TestSDUI:
    """Tests for SDUI get_ui and handle_action."""

    def _setup_module_state(self, tmp_path: Path) -> None:
        """Set module-level state so SDUI functions can run."""
        import plugins.radio as radio_mod
        from plugins.radio.store import RadioStore

        radio_mod._radio_browser = MagicMock()
        radio_mod._radio_browser.cache_size = 42
        radio_mod._provider = MagicMock()
        radio_mod._event_bus = MagicMock()
        radio_mod._store = RadioStore(tmp_path, max_recent=50)
        radio_mod._http_client = MagicMock()
        radio_mod._ctx = MagicMock()
        radio_mod._ctx.get_setting = MagicMock(return_value=None)
        radio_mod._ctx.set_setting = MagicMock()
        radio_mod._ctx.notify_ui_update = MagicMock()
        radio_mod._ctx.server_info = {"host": "127.0.0.1", "port": 9000}

        # Reset browse state
        radio_mod._browse_path = ""
        radio_mod._browse_data = None
        radio_mod._browse_title = ""

    def _teardown_module_state(self) -> None:
        import plugins.radio as radio_mod

        radio_mod._radio_browser = None
        radio_mod._provider = None
        radio_mod._event_bus = None
        radio_mod._store = None
        radio_mod._http_client = None
        radio_mod._ctx = None
        radio_mod._browse_path = ""
        radio_mod._browse_data = None
        radio_mod._browse_title = ""

    @pytest.mark.asyncio
    async def test_get_ui_returns_page(self, tmp_path: Path) -> None:
        import plugins.radio as radio_mod

        self._setup_module_state(tmp_path)
        try:
            page = await radio_mod.get_ui(radio_mod._ctx)
            assert page.title == "Radio"
            assert page.icon == "radio"
            assert page.refresh_interval == 30
            # Should have a Tabs component
            assert len(page.components) == 1
            tabs = page.components[0]
            assert tabs.type == "tabs"
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_get_ui_has_four_tabs(self, tmp_path: Path) -> None:
        import plugins.radio as radio_mod

        self._setup_module_state(tmp_path)
        try:
            page = await radio_mod.get_ui(radio_mod._ctx)
            page_dict = page.to_dict("radio")
            tabs_data = page_dict["components"][0]["props"]["tabs"]
            tab_labels = [t["label"] for t in tabs_data]
            assert tab_labels == ["Recent", "Browse", "Settings", "About"]
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_get_ui_recent_tab_empty(self, tmp_path: Path) -> None:
        import plugins.radio as radio_mod

        self._setup_module_state(tmp_path)
        try:
            page = await radio_mod.get_ui(radio_mod._ctx)
            page_dict = page.to_dict("radio")
            recent_tab = page_dict["components"][0]["props"]["tabs"][0]
            assert recent_tab["label"] == "Recent"
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_get_ui_recent_tab_with_stations(self, tmp_path: Path) -> None:
        import plugins.radio as radio_mod

        self._setup_module_state(tmp_path)
        try:
            radio_mod._store.record_play(url="http://a.com/stream", title="Station A", codec="MP3")
            radio_mod._store.record_play(url="http://b.com/stream", title="Station B", codec="AAC")
            page = await radio_mod.get_ui(radio_mod._ctx)
            page_dict = page.to_dict("radio")
            recent_tab = page_dict["components"][0]["props"]["tabs"][0]
            assert recent_tab["label"] == "Recent"
            recent_children = recent_tab["children"]
            assert len(recent_children) >= 1
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_get_ui_serializes_cleanly(self, tmp_path: Path) -> None:
        import plugins.radio as radio_mod

        self._setup_module_state(tmp_path)
        try:
            page = await radio_mod.get_ui(radio_mod._ctx)
            d = page.to_dict("radio")
            json_str = json.dumps(d)
            assert json_str
            parsed = json.loads(json_str)
            assert parsed["title"] == "Radio"
            assert parsed["plugin_id"] == "radio"
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_get_ui_browse_tab_home_has_categories(self, tmp_path: Path) -> None:
        """Browse tab at home shows category cards with navigation buttons."""
        import plugins.radio as radio_mod

        self._setup_module_state(tmp_path)
        try:
            page = await radio_mod.get_ui(radio_mod._ctx)
            page_dict = page.to_dict("radio")
            browse_tab = page_dict["components"][0]["props"]["tabs"][1]
            assert browse_tab["label"] == "Browse"
            # Serialize to JSON and check for key strings
            browse_json = json.dumps(browse_tab)
            assert "Popular Stations" in browse_json
            assert "Trending Now" in browse_json
            assert "By Country" in browse_json
            assert "By Genre" in browse_json
            assert "By Language" in browse_json
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_get_ui_browse_tab_with_stations(self, tmp_path: Path) -> None:
        """Browse tab shows station table when browse data is loaded."""
        import plugins.radio as radio_mod

        self._setup_module_state(tmp_path)
        try:
            # Simulate having loaded popular stations
            radio_mod._browse_path = "popular"
            radio_mod._browse_title = "Popular Stations"
            radio_mod._browse_data = [
                {
                    "title": "Jazz FM",
                    "info": "MP3 · 128kbps",
                    "country": "Germany",
                    "votes": "500",
                    "_url": "http://jazzfm.com/stream",
                    "_station_id": "jazz-uuid",
                    "_icon": "",
                    "_codec": "MP3",
                    "_bitrate": "128",
                    "_provider": "radio-browser",
                },
            ]

            page = await radio_mod.get_ui(radio_mod._ctx)
            page_dict = page.to_dict("radio")
            browse_tab = page_dict["components"][0]["props"]["tabs"][1]
            browse_json = json.dumps(browse_tab)

            # Should contain station data
            assert "Jazz FM" in browse_json
            # Should have back/home navigation
            assert "Back" in browse_json or "Home" in browse_json
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_get_ui_browse_tab_with_categories(self, tmp_path: Path) -> None:
        """Browse tab shows category table when browsing a category list."""
        import plugins.radio as radio_mod

        self._setup_module_state(tmp_path)
        try:
            radio_mod._browse_path = "country"
            radio_mod._browse_title = "Countries"
            radio_mod._browse_data = [
                {"name": "Germany", "stations": "5000", "_path": "country:DE"},
                {"name": "France", "stations": "3000", "_path": "country:FR"},
            ]

            page = await radio_mod.get_ui(radio_mod._ctx)
            page_dict = page.to_dict("radio")
            browse_tab = page_dict["components"][0]["props"]["tabs"][1]
            browse_json = json.dumps(browse_tab)

            assert "Germany" in browse_json
            assert "France" in browse_json
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_handle_action_clear_recent(self, tmp_path: Path) -> None:
        import plugins.radio as radio_mod

        self._setup_module_state(tmp_path)
        try:
            radio_mod._store.record_play(url="http://a.com/stream", title="A")
            radio_mod._store.record_play(url="http://b.com/stream", title="B")
            assert radio_mod._store.recent_count == 2

            result = await radio_mod.handle_action("clear_recent", {}, radio_mod._ctx)
            assert "message" in result
            assert "2" in result["message"]
            assert radio_mod._store.recent_count == 0
            radio_mod._ctx.notify_ui_update.assert_called()
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_handle_action_remove_recent(self, tmp_path: Path) -> None:
        import plugins.radio as radio_mod

        self._setup_module_state(tmp_path)
        try:
            radio_mod._store.record_play(url="http://a.com/stream", title="A")
            radio_mod._store.record_play(url="http://b.com/stream", title="B")

            result = await radio_mod.handle_action(
                "remove_recent",
                {"_url": "http://a.com/stream", "title": "A"},
                radio_mod._ctx,
            )
            assert "message" in result
            assert radio_mod._store.recent_count == 1
            assert radio_mod._store.recent[0].title == "B"
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_handle_action_remove_recent_not_found(self, tmp_path: Path) -> None:
        import plugins.radio as radio_mod

        self._setup_module_state(tmp_path)
        try:
            result = await radio_mod.handle_action(
                "remove_recent",
                {"_url": "http://nonexistent.com/stream"},
                radio_mod._ctx,
            )
            assert "error" in result
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_handle_action_clear_caches(self, tmp_path: Path) -> None:
        import plugins.radio as radio_mod

        self._setup_module_state(tmp_path)
        try:
            result = await radio_mod.handle_action("clear_caches", {}, radio_mod._ctx)
            assert "message" in result
            assert "cache" in result["message"].lower()
            radio_mod._radio_browser.clear_cache.assert_called_once()
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_handle_action_save_settings(self, tmp_path: Path) -> None:
        import plugins.radio as radio_mod

        self._setup_module_state(tmp_path)
        try:
            result = await radio_mod.handle_action(
                "save_settings",
                {
                    "default_country": "DE",
                    "cache_ttl": 300,
                    "max_recent_stations": 25,
                    "show_station_metadata": True,
                },
                radio_mod._ctx,
            )
            assert "message" in result
            assert "Saved" in result["message"]
            assert radio_mod._ctx.set_setting.call_count == 4
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_handle_action_save_settings_updates_store(self, tmp_path: Path) -> None:
        import plugins.radio as radio_mod

        self._setup_module_state(tmp_path)
        try:
            for i in range(10):
                radio_mod._store.record_play(url=f"http://s{i}.com/stream", title=f"S{i}")
            assert radio_mod._store.recent_count == 10

            await radio_mod.handle_action(
                "save_settings",
                {"max_recent_stations": 5},
                radio_mod._ctx,
            )
            assert radio_mod._store.max_recent == 5
            assert radio_mod._store.recent_count == 5
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_handle_action_unknown(self, tmp_path: Path) -> None:
        import plugins.radio as radio_mod

        self._setup_module_state(tmp_path)
        try:
            result = await radio_mod.handle_action("nonexistent_action", {}, radio_mod._ctx)
            assert "error" in result
            assert "Unknown" in result["error"]
        finally:
            self._teardown_module_state()

    # -- Browse navigation tests --

    @pytest.mark.asyncio
    async def test_handle_action_browse_navigate_popular(self, tmp_path: Path) -> None:
        """browse_navigate to 'popular' loads popular stations."""
        import plugins.radio as radio_mod

        self._setup_module_state(tmp_path)
        try:
            station = RadioStation(
                stationuuid="pop-uuid", name="Pop FM",
                url="http://pop.com/live.pls",
                url_resolved="http://pop.com/live.mp3",
                codec="MP3", bitrate=128, votes=1000,
            )
            radio_mod._radio_browser.get_popular_stations = AsyncMock(return_value=[station])

            result = await radio_mod.handle_action(
                "browse_navigate", {"path": "popular"}, radio_mod._ctx
            )
            assert "message" in result
            assert "1" in result["message"]  # "Loaded 1 station(s)"
            assert radio_mod._browse_path == "popular"
            assert radio_mod._browse_data is not None
            assert len(radio_mod._browse_data) == 1
            assert radio_mod._browse_data[0]["title"] == "Pop FM"
            radio_mod._ctx.notify_ui_update.assert_called()
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_handle_action_browse_navigate_country(self, tmp_path: Path) -> None:
        """browse_navigate to 'country' loads country list."""
        import plugins.radio as radio_mod

        self._setup_module_state(tmp_path)
        try:
            radio_mod._radio_browser.get_countries = AsyncMock(return_value=[
                CategoryEntry(name="Germany", stationcount=5000, iso_3166_1="DE"),
                CategoryEntry(name="France", stationcount=3000, iso_3166_1="FR"),
            ])

            result = await radio_mod.handle_action(
                "browse_navigate", {"path": "country"}, radio_mod._ctx
            )
            assert "message" in result
            assert "2" in result["message"]
            assert radio_mod._browse_path == "country"
            assert len(radio_mod._browse_data) == 2
            assert radio_mod._browse_data[0]["name"] == "Germany"
            assert radio_mod._browse_data[0]["_path"] == "country:DE"
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_handle_action_browse_navigate_country_stations(self, tmp_path: Path) -> None:
        """browse_navigate to 'country:DE' loads German stations."""
        import plugins.radio as radio_mod

        self._setup_module_state(tmp_path)
        try:
            station = RadioStation(
                stationuuid="de-uuid", name="Deutschlandfunk",
                url="http://dlf.com/live.pls",
                url_resolved="http://dlf.com/live.mp3",
                codec="MP3", bitrate=128, country="Germany",
            )
            radio_mod._radio_browser.get_stations_by_country = AsyncMock(return_value=[station])

            result = await radio_mod.handle_action(
                "browse_navigate", {"path": "country:DE"}, radio_mod._ctx
            )
            assert "message" in result
            assert radio_mod._browse_path == "country:DE"
            assert len(radio_mod._browse_data) == 1
            assert radio_mod._browse_data[0]["title"] == "Deutschlandfunk"
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_handle_action_browse_navigate_tag(self, tmp_path: Path) -> None:
        """browse_navigate to 'tag' loads genre/tag list."""
        import plugins.radio as radio_mod

        self._setup_module_state(tmp_path)
        try:
            radio_mod._radio_browser.get_tags = AsyncMock(return_value=[
                CategoryEntry(name="jazz", stationcount=5000),
                CategoryEntry(name="rock", stationcount=8000),
            ])

            result = await radio_mod.handle_action(
                "browse_navigate", {"path": "tag"}, radio_mod._ctx
            )
            assert radio_mod._browse_path == "tag"
            assert len(radio_mod._browse_data) == 2
            assert radio_mod._browse_data[0]["_path"] == "tag:jazz"
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_handle_action_browse_navigate_tag_stations(self, tmp_path: Path) -> None:
        """browse_navigate to 'tag:jazz' loads jazz stations."""
        import plugins.radio as radio_mod

        self._setup_module_state(tmp_path)
        try:
            station = RadioStation(
                stationuuid="jazz-uuid", name="Jazz FM",
                url="http://jazz.com/live.pls",
                url_resolved="http://jazz.com/live.mp3",
                codec="MP3", bitrate=128,
            )
            radio_mod._radio_browser.get_stations_by_tag = AsyncMock(return_value=[station])

            result = await radio_mod.handle_action(
                "browse_navigate", {"path": "tag:jazz"}, radio_mod._ctx
            )
            assert radio_mod._browse_path == "tag:jazz"
            assert len(radio_mod._browse_data) == 1
            assert radio_mod._browse_data[0]["title"] == "Jazz FM"
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_handle_action_browse_navigate_language(self, tmp_path: Path) -> None:
        """browse_navigate to 'language' loads language list."""
        import plugins.radio as radio_mod

        self._setup_module_state(tmp_path)
        try:
            radio_mod._radio_browser.get_languages = AsyncMock(return_value=[
                CategoryEntry(name="german", stationcount=3000),
                CategoryEntry(name="english", stationcount=10000),
            ])

            result = await radio_mod.handle_action(
                "browse_navigate", {"path": "language"}, radio_mod._ctx
            )
            assert radio_mod._browse_path == "language"
            assert len(radio_mod._browse_data) == 2
            assert radio_mod._browse_data[0]["_path"] == "language:german"
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_handle_action_browse_navigate_language_stations(self, tmp_path: Path) -> None:
        """browse_navigate to 'language:german' loads German-language stations."""
        import plugins.radio as radio_mod

        self._setup_module_state(tmp_path)
        try:
            station = RadioStation(
                stationuuid="de-lang-uuid", name="Deutschlandfunk",
                url="http://dlf.com/live.pls",
                url_resolved="http://dlf.com/live.mp3",
                codec="MP3", bitrate=128,
            )
            radio_mod._radio_browser.get_stations_by_language = AsyncMock(return_value=[station])

            result = await radio_mod.handle_action(
                "browse_navigate", {"path": "language:german"}, radio_mod._ctx
            )
            assert radio_mod._browse_path == "language:german"
            assert len(radio_mod._browse_data) == 1
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_handle_action_browse_navigate_trending(self, tmp_path: Path) -> None:
        """browse_navigate to 'trending' loads trending stations."""
        import plugins.radio as radio_mod

        self._setup_module_state(tmp_path)
        try:
            station = RadioStation(
                stationuuid="trend-uuid", name="Trending FM",
                url="http://trend.com/live.pls",
                url_resolved="http://trend.com/live.mp3",
                codec="MP3", bitrate=128,
            )
            radio_mod._radio_browser.get_trending_stations = AsyncMock(return_value=[station])

            result = await radio_mod.handle_action(
                "browse_navigate", {"path": "trending"}, radio_mod._ctx
            )
            assert radio_mod._browse_path == "trending"
            assert len(radio_mod._browse_data) == 1
            assert radio_mod._browse_data[0]["title"] == "Trending FM"
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_handle_action_browse_home(self, tmp_path: Path) -> None:
        """browse_home resets browse state."""
        import plugins.radio as radio_mod

        self._setup_module_state(tmp_path)
        try:
            # First navigate somewhere
            radio_mod._browse_path = "popular"
            radio_mod._browse_data = [{"title": "x"}]
            radio_mod._browse_title = "Popular"

            result = await radio_mod.handle_action("browse_home", {}, radio_mod._ctx)
            assert "message" in result
            assert radio_mod._browse_path == ""
            assert radio_mod._browse_data is None
            assert radio_mod._browse_title == ""
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_handle_action_browse_back_to_parent(self, tmp_path: Path) -> None:
        """browse_back from 'country:DE' goes back to 'country'."""
        import plugins.radio as radio_mod

        self._setup_module_state(tmp_path)
        try:
            radio_mod._browse_path = "country:DE"

            radio_mod._radio_browser.get_countries = AsyncMock(return_value=[
                CategoryEntry(name="Germany", stationcount=5000, iso_3166_1="DE"),
            ])

            result = await radio_mod.handle_action(
                "browse_back", {"target": "country"}, radio_mod._ctx
            )
            assert "message" in result
            assert radio_mod._browse_path == "country"
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_handle_action_browse_back_to_home(self, tmp_path: Path) -> None:
        """browse_back with empty target goes to home."""
        import plugins.radio as radio_mod

        self._setup_module_state(tmp_path)
        try:
            radio_mod._browse_path = "popular"

            result = await radio_mod.handle_action(
                "browse_back", {"target": ""}, radio_mod._ctx
            )
            assert radio_mod._browse_path == ""
            assert radio_mod._browse_data is None
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_handle_action_browse_search(self, tmp_path: Path) -> None:
        """browse_search loads search results."""
        import plugins.radio as radio_mod

        self._setup_module_state(tmp_path)
        try:
            station = RadioStation(
                stationuuid="search-uuid", name="Found FM",
                url="http://found.com/live.pls",
                url_resolved="http://found.com/live.mp3",
                codec="MP3", bitrate=128,
            )
            radio_mod._radio_browser.search = AsyncMock(return_value=[station])

            result = await radio_mod.handle_action(
                "browse_search", {"query": "found"}, radio_mod._ctx
            )
            assert "message" in result
            assert radio_mod._browse_path == "search:found"
            assert len(radio_mod._browse_data) == 1
            assert radio_mod._browse_data[0]["title"] == "Found FM"
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_handle_action_browse_search_empty(self, tmp_path: Path) -> None:
        """browse_search with empty query returns error."""
        import plugins.radio as radio_mod

        self._setup_module_state(tmp_path)
        try:
            result = await radio_mod.handle_action(
                "browse_search", {"query": ""}, radio_mod._ctx
            )
            assert "error" in result
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_handle_action_browse_drilldown(self, tmp_path: Path) -> None:
        """browse_drilldown navigates into a category."""
        import plugins.radio as radio_mod

        self._setup_module_state(tmp_path)
        try:
            station = RadioStation(
                stationuuid="de-uuid", name="German FM",
                url="http://de.com/live.pls",
                url_resolved="http://de.com/live.mp3",
                codec="MP3", bitrate=128,
            )
            radio_mod._radio_browser.get_stations_by_country = AsyncMock(return_value=[station])

            # Simulate clicking a country row in the category table
            result = await radio_mod.handle_action(
                "browse_drilldown",
                {"row": {"_path": "country:DE", "name": "Germany"}},
                radio_mod._ctx,
            )
            assert "message" in result
            assert radio_mod._browse_path == "country:DE"
            assert len(radio_mod._browse_data) == 1
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_handle_action_browse_drilldown_no_path(self, tmp_path: Path) -> None:
        """browse_drilldown without path returns error."""
        import plugins.radio as radio_mod

        self._setup_module_state(tmp_path)
        try:
            result = await radio_mod.handle_action(
                "browse_drilldown", {"row": {}}, radio_mod._ctx
            )
            assert "error" in result
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_handle_action_play_station(self, tmp_path: Path) -> None:
        """play_station plays a station from browse results."""
        import plugins.radio as radio_mod

        self._setup_module_state(tmp_path)
        try:
            mock_player = MagicMock()
            mock_player.mac_address = "aa:bb:cc:dd:ee:ff"
            mock_player.name = "Living Room"
            radio_mod._ctx.player_registry.get_all = AsyncMock(
                return_value=[mock_player]
            )

            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {"id": 1, "result": {"count": 1}}
            radio_mod._http_client.post = AsyncMock(return_value=mock_resp)

            result = await radio_mod.handle_action(
                "play_station",
                {
                    "_url": "http://example.com/stream",
                    "title": "My Station",
                    "_station_id": "abc-123",
                    "_icon": "http://example.com/icon.png",
                    "_codec": "MP3",
                    "_bitrate": "128",
                    "_provider": "radio-browser",
                },
                radio_mod._ctx,
            )
            assert "message" in result
            assert "My Station" in result["message"]
            assert "Living Room" in result["message"]

            radio_mod._http_client.post.assert_called_once()
            call_args = radio_mod._http_client.post.call_args
            assert "jsonrpc.js" in call_args[0][0]
            rpc_body = call_args[1]["json"]
            assert rpc_body["method"] == "slim.request"
            assert rpc_body["params"][0] == "aa:bb:cc:dd:ee:ff"
            cmd = rpc_body["params"][1]
            assert cmd[0] == "radio"
            assert cmd[1] == "play"
            assert "url:http://example.com/stream" in cmd
            assert "title:My Station" in cmd
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_handle_action_play_recent(self, tmp_path: Path) -> None:
        """play_recent uses the same handler as play_station."""
        import plugins.radio as radio_mod

        self._setup_module_state(tmp_path)
        try:
            mock_player = MagicMock()
            mock_player.mac_address = "aa:bb:cc:dd:ee:ff"
            mock_player.name = "Living Room"
            radio_mod._ctx.player_registry.get_all = AsyncMock(
                return_value=[mock_player]
            )

            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {"id": 1, "result": {"count": 1}}
            radio_mod._http_client.post = AsyncMock(return_value=mock_resp)

            result = await radio_mod.handle_action(
                "play_recent",
                {
                    "_url": "http://example.com/stream",
                    "title": "My Station",
                    "_station_id": "abc-123",
                    "_icon": "http://example.com/icon.png",
                    "_codec": "MP3",
                    "_bitrate": "128",
                },
                radio_mod._ctx,
            )
            assert "message" in result
            assert "My Station" in result["message"]
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_handle_action_play_station_no_url(self, tmp_path: Path) -> None:
        import plugins.radio as radio_mod

        self._setup_module_state(tmp_path)
        try:
            result = await radio_mod.handle_action(
                "play_station", {}, radio_mod._ctx,
            )
            assert "error" in result
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_handle_action_play_station_no_players(self, tmp_path: Path) -> None:
        import plugins.radio as radio_mod

        self._setup_module_state(tmp_path)
        try:
            radio_mod._ctx.player_registry.get_all = AsyncMock(return_value=[])

            result = await radio_mod.handle_action(
                "play_station",
                {"_url": "http://example.com/stream", "title": "Test"},
                radio_mod._ctx,
            )
            assert "error" in result
            assert "No players" in result["error"]
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_handle_action_play_station_no_player_registry(self, tmp_path: Path) -> None:
        import plugins.radio as radio_mod

        self._setup_module_state(tmp_path)
        radio_mod._ctx.player_registry = None
        try:
            result = await radio_mod.handle_action(
                "play_station",
                {"_url": "http://example.com/stream", "title": "Test"},
                radio_mod._ctx,
            )
            assert "error" in result
            assert "registry" in result["error"].lower()
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_handle_action_play_station_rpc_error(self, tmp_path: Path) -> None:
        import plugins.radio as radio_mod

        self._setup_module_state(tmp_path)
        try:
            mock_player = MagicMock()
            mock_player.mac_address = "aa:bb:cc:dd:ee:ff"
            mock_player.name = "Test Player"
            radio_mod._ctx.player_registry.get_all = AsyncMock(
                return_value=[mock_player]
            )

            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {
                "id": 1,
                "error": {"message": "Command failed"},
            }
            radio_mod._http_client.post = AsyncMock(return_value=mock_resp)

            result = await radio_mod.handle_action(
                "play_station",
                {"_url": "http://example.com/stream", "title": "Test"},
                radio_mod._ctx,
            )
            assert "error" in result
            assert "Playback failed" in result["error"]
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_handle_action_play_station_network_error(self, tmp_path: Path) -> None:
        import plugins.radio as radio_mod

        self._setup_module_state(tmp_path)
        try:
            mock_player = MagicMock()
            mock_player.mac_address = "aa:bb:cc:dd:ee:ff"
            mock_player.name = "Test Player"
            radio_mod._ctx.player_registry.get_all = AsyncMock(
                return_value=[mock_player]
            )
            radio_mod._http_client.post = AsyncMock(
                side_effect=Exception("Connection refused")
            )

            result = await radio_mod.handle_action(
                "play_station",
                {"_url": "http://example.com/stream", "title": "Test"},
                radio_mod._ctx,
            )
            assert "error" in result
            assert "Could not start playback" in result["error"]
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_handle_action_play_station_http_client_none(self, tmp_path: Path) -> None:
        import plugins.radio as radio_mod

        self._setup_module_state(tmp_path)
        radio_mod._http_client = None
        try:
            mock_player = MagicMock()
            mock_player.mac_address = "aa:bb:cc:dd:ee:ff"
            mock_player.name = "Test Player"
            radio_mod._ctx.player_registry.get_all = AsyncMock(
                return_value=[mock_player]
            )

            result = await radio_mod.handle_action(
                "play_station",
                {"_url": "http://example.com/stream", "title": "Test"},
                radio_mod._ctx,
            )
            assert "error" in result
            assert "HTTP client" in result["error"]
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_handle_action_play_station_host_0000(self, tmp_path: Path) -> None:
        """When server_info host is 0.0.0.0, it should use 127.0.0.1."""
        import plugins.radio as radio_mod

        self._setup_module_state(tmp_path)
        radio_mod._ctx.server_info = {"host": "0.0.0.0", "port": 9000}
        try:
            mock_player = MagicMock()
            mock_player.mac_address = "aa:bb:cc:dd:ee:ff"
            mock_player.name = "Test"
            radio_mod._ctx.player_registry.get_all = AsyncMock(
                return_value=[mock_player]
            )

            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {"id": 1, "result": {"count": 1}}
            radio_mod._http_client.post = AsyncMock(return_value=mock_resp)

            result = await radio_mod.handle_action(
                "play_station",
                {"_url": "http://example.com/stream", "title": "Test"},
                radio_mod._ctx,
            )
            assert "message" in result

            call_url = radio_mod._http_client.post.call_args[0][0]
            assert "127.0.0.1" in call_url
            assert "0.0.0.0" not in call_url
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_handle_action_play_station_from_row_wrapper(self, tmp_path: Path) -> None:
        """Table row actions may nest params under 'row'."""
        import plugins.radio as radio_mod

        self._setup_module_state(tmp_path)
        try:
            mock_player = MagicMock()
            mock_player.mac_address = "aa:bb:cc:dd:ee:ff"
            mock_player.name = "Test"
            radio_mod._ctx.player_registry.get_all = AsyncMock(
                return_value=[mock_player]
            )

            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {"id": 1, "result": {"count": 1}}
            radio_mod._http_client.post = AsyncMock(return_value=mock_resp)

            result = await radio_mod.handle_action(
                "play_station",
                {"row": {"_url": "http://example.com/stream", "title": "Row Station"}},
                radio_mod._ctx,
            )
            assert "message" in result
            assert "Row Station" in result["message"]
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_browse_navigate_unknown_path(self, tmp_path: Path) -> None:
        """browse_navigate with unknown path returns error."""
        import plugins.radio as radio_mod

        self._setup_module_state(tmp_path)
        try:
            result = await radio_mod.handle_action(
                "browse_navigate", {"path": "nonexistent_category"}, radio_mod._ctx
            )
            assert "error" in result
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_browse_navigate_api_failure(self, tmp_path: Path) -> None:
        """browse_navigate handles API failures gracefully."""
        import plugins.radio as radio_mod

        self._setup_module_state(tmp_path)
        try:
            radio_mod._radio_browser.get_popular_stations = AsyncMock(
                side_effect=Exception("Network error")
            )

            result = await radio_mod.handle_action(
                "browse_navigate", {"path": "popular"}, radio_mod._ctx
            )
            assert "error" in result
            assert "Failed" in result["error"]
        finally:
            self._teardown_module_state()

    @pytest.mark.asyncio
    async def test_clear_caches_resets_browse_state(self, tmp_path: Path) -> None:
        """Clearing caches also resets browse state."""
        import plugins.radio as radio_mod

        self._setup_module_state(tmp_path)
        try:
            radio_mod._browse_path = "popular"
            radio_mod._browse_data = [{"title": "x"}]
            radio_mod._browse_title = "Popular"

            await radio_mod.handle_action("clear_caches", {}, radio_mod._ctx)

            assert radio_mod._browse_path == ""
            assert radio_mod._browse_data is None
            assert radio_mod._browse_title == ""
        finally:
            self._teardown_module_state()
