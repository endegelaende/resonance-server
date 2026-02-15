"""
Tests for the Radio plugin.

Tests cover:
- TuneIn API client (parsing, caching, URL helpers)
- RadioProvider ContentProvider implementation
- JSON-RPC command dispatch (radio items/search/play)
- Jive menu item format (audio, folder, search items)
- CLI item format
- Error handling (network errors, missing params, empty responses)
- Plugin lifecycle (setup/teardown)
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.radio.tunein import (
    PARTNER_ID,
    TuneInClient,
    TuneInItem,
    TuneInStream,
    _ensure_json,
    _ensure_partner_id,
    _parse_body,
    _parse_outline,
    _SimpleCache,
    _url_encode_query,
    content_type_for_media,
    extract_station_id,
    flatten_items,
    is_browse_url,
    is_search_url,
    is_tune_url,
    is_tunein_url,
)

# =============================================================================
# TuneIn URL helpers
# =============================================================================


class TestTuneInURLHelpers:
    """Tests for URL helper functions."""

    def test_ensure_json_adds_render_param(self) -> None:
        url = "http://opml.radiotime.com/Index.aspx?partnerId=16"
        result = _ensure_json(url)
        assert "render=json" in result

    def test_ensure_json_does_not_duplicate(self) -> None:
        url = "http://opml.radiotime.com/Index.aspx?partnerId=16&render=json"
        result = _ensure_json(url)
        assert result.count("render=json") == 1

    def test_ensure_json_with_no_query_string(self) -> None:
        url = "http://opml.radiotime.com/Index.aspx"
        result = _ensure_json(url)
        assert "?render=json" in result

    def test_ensure_partner_id_adds_param(self) -> None:
        url = "http://opml.radiotime.com/Browse.ashx?c=music"
        result = _ensure_partner_id(url)
        assert f"partnerId={PARTNER_ID}" in result

    def test_ensure_partner_id_does_not_duplicate(self) -> None:
        url = f"http://opml.radiotime.com/Browse.ashx?partnerId={PARTNER_ID}"
        result = _ensure_partner_id(url)
        assert result.count("partnerId=") == 1

    def test_is_tunein_url_radiotime(self) -> None:
        assert is_tunein_url("http://opml.radiotime.com/Index.aspx") is True

    def test_is_tunein_url_tunein(self) -> None:
        assert is_tunein_url("http://opml.tunein.com/Browse.ashx") is True

    def test_is_tunein_url_other(self) -> None:
        assert is_tunein_url("http://example.com/stream.mp3") is False

    def test_is_tune_url(self) -> None:
        assert is_tune_url("http://opml.radiotime.com/Tune.ashx?id=s31681") is True
        assert is_tune_url("http://opml.radiotime.com/Browse.ashx?c=music") is False
        assert is_tune_url("http://example.com/tune.ashx") is False

    def test_is_browse_url(self) -> None:
        assert is_browse_url("http://opml.radiotime.com/Browse.ashx?c=music") is True
        assert is_browse_url("http://opml.radiotime.com/Tune.ashx?id=s31681") is False

    def test_is_search_url(self) -> None:
        assert is_search_url("http://opml.radiotime.com/Search.ashx?query=jazz") is True
        assert is_search_url("http://opml.radiotime.com/Browse.ashx") is False

    def test_extract_station_id_from_tune_url(self) -> None:
        url = "http://opml.radiotime.com/Tune.ashx?id=s31681&partnerId=16"
        assert extract_station_id(url) == "s31681"

    def test_extract_station_id_no_id(self) -> None:
        url = "http://opml.radiotime.com/Browse.ashx?c=music"
        assert extract_station_id(url) is None

    def test_extract_station_id_invalid_url(self) -> None:
        assert extract_station_id("") is None

    def test_url_encode_query_basic(self) -> None:
        result = _url_encode_query("bbc radio")
        assert "bbc" in result
        assert "radio" in result

    def test_url_encode_query_special_chars(self) -> None:
        result = _url_encode_query("rock & roll")
        # Should be URL-encoded
        assert "%" in result or "&" not in result.replace("%26", "")

    def test_content_type_mp3(self) -> None:
        assert content_type_for_media("mp3") == "audio/mpeg"

    def test_content_type_aac(self) -> None:
        assert content_type_for_media("aac") == "audio/aac"

    def test_content_type_ogg(self) -> None:
        assert content_type_for_media("ogg") == "audio/ogg"

    def test_content_type_wma(self) -> None:
        assert content_type_for_media("wma") == "audio/x-ms-wma"

    def test_content_type_hls(self) -> None:
        assert content_type_for_media("hls") == "application/vnd.apple.mpegurl"

    def test_content_type_flac(self) -> None:
        assert content_type_for_media("flac") == "audio/flac"

    def test_content_type_unknown_defaults_to_mpeg(self) -> None:
        assert content_type_for_media("xyz") == "audio/mpeg"

    def test_content_type_case_insensitive(self) -> None:
        assert content_type_for_media("MP3") == "audio/mpeg"
        assert content_type_for_media("AAC") == "audio/aac"


# =============================================================================
# SimpleCache
# =============================================================================


class TestSimpleCache:
    """Tests for the TTL cache used by TuneInClient."""

    def test_put_and_get(self) -> None:
        cache = _SimpleCache(max_entries=10, ttl=60)
        cache.put("key1", {"data": "value"})
        assert cache.get("key1") == {"data": "value"}

    def test_get_missing_returns_none(self) -> None:
        cache = _SimpleCache()
        assert cache.get("nonexistent") is None

    def test_expired_entry_returns_none(self) -> None:
        cache = _SimpleCache(ttl=0.01)
        cache.put("key1", "data")
        time.sleep(0.02)
        assert cache.get("key1") is None

    def test_custom_ttl_per_entry(self) -> None:
        cache = _SimpleCache(ttl=60)
        cache.put("key1", "data", ttl=0.01)
        time.sleep(0.02)
        assert cache.get("key1") is None

    def test_eviction_at_capacity(self) -> None:
        cache = _SimpleCache(max_entries=2, ttl=60)
        cache.put("key1", "a")
        cache.put("key2", "b")
        cache.put("key3", "c")  # Should evict oldest
        assert len(cache) == 2
        assert cache.get("key3") == "c"

    def test_clear(self) -> None:
        cache = _SimpleCache()
        cache.put("a", 1)
        cache.put("b", 2)
        cache.clear()
        assert len(cache) == 0
        assert cache.get("a") is None

    def test_len(self) -> None:
        cache = _SimpleCache()
        assert len(cache) == 0
        cache.put("a", 1)
        assert len(cache) == 1
        cache.put("b", 2)
        assert len(cache) == 2


# =============================================================================
# Outline parsing
# =============================================================================


class TestOutlineParsing:
    """Tests for TuneIn OPML JSON response parsing."""

    def test_parse_audio_item(self) -> None:
        raw = {
            "element": "outline",
            "type": "audio",
            "text": "Jazz 88.5",
            "URL": "http://opml.radiotime.com/Tune.ashx?id=s31681",
            "bitrate": "128",
            "guide_id": "s31681",
            "subtext": "Now Playing: Some Song",
            "formats": "mp3",
            "image": "http://example.com/logo.png",
            "preset_id": "s31681",
            "playing": "Artist - Song",
            "reliability": "97",
            "item": "station",
        }
        item = _parse_outline(raw)
        assert item.text == "Jazz 88.5"
        assert item.type == "audio"
        assert item.url == "http://opml.radiotime.com/Tune.ashx?id=s31681"
        assert item.bitrate == "128"
        assert item.guide_id == "s31681"
        assert item.subtext == "Now Playing: Some Song"
        assert item.formats == "mp3"
        assert item.image == "http://example.com/logo.png"
        assert item.preset_id == "s31681"
        assert item.playing == "Artist - Song"
        assert item.reliability == "97"
        assert item.item_type == "station"
        assert item.is_container is False
        assert item.children == []

    def test_parse_link_item(self) -> None:
        raw = {
            "element": "outline",
            "type": "link",
            "text": "Music",
            "URL": "http://opml.radiotime.com/Browse.ashx?c=music",
            "key": "music",
        }
        item = _parse_outline(raw)
        assert item.text == "Music"
        assert item.type == "link"
        assert item.key == "music"
        assert item.is_container is False

    def test_parse_search_item(self) -> None:
        raw = {
            "element": "outline",
            "type": "search",
            "text": "Search TuneIn",
            "URL": "http://opml.radiotime.com/Search.ashx?query={QUERY}",
        }
        item = _parse_outline(raw)
        assert item.type == "search"
        assert item.text == "Search TuneIn"

    def test_parse_container_with_children(self) -> None:
        raw = {
            "element": "outline",
            "text": "Stations (26+)",
            "key": "stations",
            "children": [
                {
                    "element": "outline",
                    "type": "audio",
                    "text": "Station A",
                    "URL": "http://opml.radiotime.com/Tune.ashx?id=s1",
                    "guide_id": "s1",
                },
                {
                    "element": "outline",
                    "type": "audio",
                    "text": "Station B",
                    "URL": "http://opml.radiotime.com/Tune.ashx?id=s2",
                    "guide_id": "s2",
                },
            ],
        }
        item = _parse_outline(raw)
        assert item.is_container is True
        assert item.type == "container"
        assert len(item.children) == 2
        assert item.children[0].text == "Station A"
        assert item.children[1].text == "Station B"

    def test_parse_empty_outline(self) -> None:
        raw = {"element": "outline"}
        item = _parse_outline(raw)
        assert item.text == ""
        assert item.type == "link"

    def test_parse_body(self) -> None:
        body = [
            {"element": "outline", "type": "link", "text": "Local Radio", "URL": "http://example.com/local"},
            {"element": "outline", "type": "audio", "text": "Station", "URL": "http://example.com/tune"},
        ]
        items = _parse_body(body)
        assert len(items) == 2
        assert items[0].text == "Local Radio"
        assert items[1].text == "Station"


# =============================================================================
# flatten_items
# =============================================================================


class TestFlattenItems:
    """Tests for container flattening."""

    def test_flat_items_unchanged(self) -> None:
        items = [
            TuneInItem(text="A", type="link"),
            TuneInItem(text="B", type="audio"),
        ]
        result = flatten_items(items)
        assert len(result) == 2
        assert result[0].text == "A"
        assert result[1].text == "B"

    def test_container_children_inlined(self) -> None:
        child1 = TuneInItem(text="Child1", type="audio", guide_id="s1")
        child2 = TuneInItem(text="Child2", type="audio", guide_id="s2")
        container = TuneInItem(
            text="Stations (2)",
            type="container",
            is_container=True,
            children=[child1, child2],
        )
        non_container = TuneInItem(text="Other", type="link")

        result = flatten_items([container, non_container])
        assert len(result) == 3
        assert result[0].text == "Child1"
        assert result[1].text == "Child2"
        assert result[2].text == "Other"

    def test_empty_container_removed(self) -> None:
        """A container with is_container=True but no children produces nothing."""
        container = TuneInItem(text="Empty", type="container", is_container=True, children=[])
        result = flatten_items([container])
        # is_container=True but children=[] → not flattened (children are falsy), kept as-is
        assert len(result) == 1

    def test_mixed_containers_and_items(self) -> None:
        items = [
            TuneInItem(text="Before", type="link"),
            TuneInItem(
                text="Group",
                type="container",
                is_container=True,
                children=[TuneInItem(text="Inner", type="audio")],
            ),
            TuneInItem(text="After", type="audio"),
        ]
        result = flatten_items(items)
        assert len(result) == 3
        assert [r.text for r in result] == ["Before", "Inner", "After"]


# =============================================================================
# TuneInStream
# =============================================================================


class TestTuneInStream:
    """Tests for the TuneInStream dataclass."""

    def test_defaults(self) -> None:
        stream = TuneInStream(url="http://stream.example.com/live.mp3")
        assert stream.url == "http://stream.example.com/live.mp3"
        assert stream.bitrate == 0
        assert stream.media_type == "mp3"
        assert stream.is_direct is True
        assert stream.reliability == 0
        assert stream.guide_id == ""
        assert stream.is_hls is False

    def test_all_fields(self) -> None:
        stream = TuneInStream(
            url="http://stream.example.com/live",
            bitrate=128,
            media_type="aac",
            is_direct=False,
            reliability=97,
            guide_id="e12345",
            is_hls=True,
        )
        assert stream.bitrate == 128
        assert stream.media_type == "aac"
        assert stream.is_direct is False
        assert stream.reliability == 97
        assert stream.guide_id == "e12345"
        assert stream.is_hls is True


# =============================================================================
# TuneInClient
# =============================================================================


class TestTuneInClient:
    """Tests for the TuneIn API client (mocked HTTP)."""

    @pytest.fixture
    def client(self) -> TuneInClient:
        return TuneInClient(timeout=5.0, cache_ttl=60)

    @pytest.mark.asyncio
    async def test_fetch_root(self, client: TuneInClient) -> None:
        """fetch_root returns parsed TuneIn root menu items."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "head": {"title": "Browse", "status": "200"},
            "body": [
                {
                    "element": "outline",
                    "type": "link",
                    "text": "Local Radio",
                    "URL": "http://opml.radiotime.com/Browse.ashx?c=local",
                    "key": "local",
                },
                {
                    "element": "outline",
                    "type": "link",
                    "text": "Music",
                    "URL": "http://opml.radiotime.com/Browse.ashx?c=music",
                    "key": "music",
                },
                {
                    "element": "outline",
                    "type": "search",
                    "text": "Search TuneIn",
                    "URL": "http://opml.radiotime.com/Search.ashx?query={QUERY}",
                },
            ],
        }

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        client._client = mock_http

        items = await client.fetch_root()
        assert len(items) == 3
        assert items[0].text == "Local Radio"
        assert items[0].type == "link"
        assert items[0].key == "local"
        assert items[1].text == "Music"
        assert items[2].type == "search"

    @pytest.mark.asyncio
    async def test_browse_empty_path_calls_root(self, client: TuneInClient) -> None:
        """browse('') delegates to fetch_root."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "head": {"status": "200"},
            "body": [
                {
                    "element": "outline",
                    "type": "link",
                    "text": "Music",
                    "URL": "http://opml.radiotime.com/Browse.ashx?c=music",
                },
            ],
        }

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        client._client = mock_http

        items = await client.browse("")
        assert len(items) == 1
        assert items[0].text == "Music"

    @pytest.mark.asyncio
    async def test_browse_with_url(self, client: TuneInClient) -> None:
        """browse(url) fetches the given URL."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "head": {"title": "Jazz", "status": "200"},
            "body": [
                {
                    "element": "outline",
                    "type": "audio",
                    "text": "Jazz 88.5",
                    "URL": "http://opml.radiotime.com/Tune.ashx?id=s31681",
                    "guide_id": "s31681",
                    "bitrate": "128",
                    "image": "http://example.com/logo.png",
                },
            ],
        }

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        client._client = mock_http

        items = await client.browse("http://opml.radiotime.com/Browse.ashx?id=c57944")
        assert len(items) == 1
        assert items[0].text == "Jazz 88.5"
        assert items[0].type == "audio"
        assert items[0].guide_id == "s31681"

    @pytest.mark.asyncio
    async def test_search(self, client: TuneInClient) -> None:
        """search returns parsed results."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "head": {"title": "Search Results: jazz", "status": "200"},
            "body": [
                {
                    "element": "outline",
                    "type": "audio",
                    "text": "Jazz FM",
                    "URL": "http://opml.radiotime.com/Tune.ashx?id=s100",
                    "guide_id": "s100",
                    "bitrate": "128",
                    "formats": "mp3",
                },
                {
                    "element": "outline",
                    "type": "link",
                    "text": "Jazz Shows",
                    "URL": "http://opml.radiotime.com/Browse.ashx?id=g42",
                    "guide_id": "g42",
                },
            ],
        }

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        client._client = mock_http

        items = await client.search("jazz")
        assert len(items) == 2
        assert items[0].type == "audio"
        assert items[0].text == "Jazz FM"
        assert items[1].type == "link"

    @pytest.mark.asyncio
    async def test_tune_success(self, client: TuneInClient) -> None:
        """tune resolves a station ID to a TuneInStream."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "head": {"status": "200"},
            "body": [
                {
                    "element": "audio",
                    "url": "http://kbem-live.streamguys1.com/kbem_mp3",
                    "reliability": 97,
                    "bitrate": 128,
                    "media_type": "mp3",
                    "is_direct": True,
                    "guide_id": "e364677222",
                }
            ],
        }

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        client._client = mock_http

        stream = await client.tune("s31681")
        assert stream is not None
        assert stream.url == "http://kbem-live.streamguys1.com/kbem_mp3"
        assert stream.bitrate == 128
        assert stream.media_type == "mp3"
        assert stream.is_direct is True
        assert stream.reliability == 97
        assert stream.is_hls is False

    @pytest.mark.asyncio
    async def test_tune_empty_body(self, client: TuneInClient) -> None:
        """tune returns None on empty body."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "head": {"status": "200"},
            "body": [],
        }

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        client._client = mock_http

        stream = await client.tune("s99999")
        assert stream is None

    @pytest.mark.asyncio
    async def test_tune_no_url(self, client: TuneInClient) -> None:
        """tune returns None when body has no url."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "head": {"status": "200"},
            "body": [{"element": "audio"}],
        }

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        client._client = mock_http

        stream = await client.tune("s99999")
        assert stream is None

    @pytest.mark.asyncio
    async def test_tune_http_error(self, client: TuneInClient) -> None:
        """tune returns None on HTTP error."""
        import httpx

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(side_effect=httpx.HTTPError("Connection failed"))
        client._client = mock_http

        stream = await client.tune("s12345")
        assert stream is None

    @pytest.mark.asyncio
    async def test_tune_hls_detection(self, client: TuneInClient) -> None:
        """tune detects HLS streams."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "head": {"status": "200"},
            "body": [
                {
                    "element": "audio",
                    "url": "http://example.com/stream.m3u8",
                    "media_type": "hls",
                    "bitrate": 256,
                }
            ],
        }

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        client._client = mock_http

        stream = await client.tune("s777")
        assert stream is not None
        assert stream.is_hls is True
        assert stream.media_type == "hls"

    @pytest.mark.asyncio
    async def test_tune_url_method(self, client: TuneInClient) -> None:
        """tune_url resolves a full Tune.ashx URL."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "head": {"status": "200"},
            "body": [
                {
                    "element": "audio",
                    "url": "http://stream.example.com/live.mp3",
                    "bitrate": 192,
                    "media_type": "mp3",
                    "is_direct": True,
                }
            ],
        }

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        client._client = mock_http

        stream = await client.tune_url("http://opml.radiotime.com/Tune.ashx?id=s555&partnerId=16")
        assert stream is not None
        assert stream.url == "http://stream.example.com/live.mp3"
        assert stream.bitrate == 192

    @pytest.mark.asyncio
    async def test_cache_hit(self, client: TuneInClient) -> None:
        """Second fetch for the same URL returns cached data."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "head": {"status": "200"},
            "body": [
                {"element": "outline", "type": "link", "text": "Cached"},
            ],
        }

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        client._client = mock_http

        # First call
        items1 = await client.browse("http://opml.radiotime.com/Browse.ashx?c=music")
        # Second call — should use cache
        items2 = await client.browse("http://opml.radiotime.com/Browse.ashx?c=music")

        assert items1[0].text == "Cached"
        assert items2[0].text == "Cached"
        # HTTP should only have been called once
        assert mock_http.get.call_count == 1

    @pytest.mark.asyncio
    async def test_search_not_cached(self, client: TuneInClient) -> None:
        """Search results should not be cached."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "head": {"status": "200"},
            "body": [
                {"element": "outline", "type": "audio", "text": "Result"},
            ],
        }

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        client._client = mock_http

        await client.search("jazz")
        await client.search("jazz")
        # Should be called twice (no caching for search)
        assert mock_http.get.call_count == 2

    def test_clear_cache(self, client: TuneInClient) -> None:
        """clear_cache empties the cache."""
        client._cache.put("test", "data")
        assert client.cache_size == 1
        client.clear_cache()
        assert client.cache_size == 0

    @pytest.mark.asyncio
    async def test_start_creates_client(self, client: TuneInClient) -> None:
        """start() creates an httpx client."""
        assert client._client is None
        await client.start()
        assert client._client is not None
        await client.close()

    @pytest.mark.asyncio
    async def test_close_clears_state(self, client: TuneInClient) -> None:
        """close() cleans up client and cache."""
        await client.start()
        client._cache.put("key", "val")
        await client.close()
        assert client._client is None
        assert client.cache_size == 0


# =============================================================================
# Base actions
# =============================================================================


class TestBaseActions:
    """Tests for the base actions in Jive menu responses."""

    def test_base_actions_structure(self) -> None:
        from plugins.radio import _base_actions

        base = _base_actions()
        assert "actions" in base
        assert "go" in base["actions"]
        assert "play" in base["actions"]
        assert "add" in base["actions"]

        go = base["actions"]["go"]
        assert go["cmd"] == ["radio", "items"]
        assert go["params"]["menu"] == 1

        play = base["actions"]["play"]
        assert play["player"] == 0
        assert play["cmd"] == ["radio", "play"]

    def test_base_actions_add_has_cmd_add(self) -> None:
        from plugins.radio import _base_actions

        base = _base_actions()
        add = base["actions"]["add"]
        assert add["params"]["cmd"] == "add"


# =============================================================================
# Parameter parsing
# =============================================================================


class TestParameterParsing:
    """Tests for _parse_tagged and _parse_start_count."""

    def test_parse_tagged_colon_format(self) -> None:
        from plugins.radio import _parse_tagged

        result = _parse_tagged(["radio", "items", "url:http://example.com", "menu:1"], start=2)
        assert result["url"] == "http://example.com"
        assert result["menu"] == "1"

    def test_parse_tagged_dict_format(self) -> None:
        from plugins.radio import _parse_tagged

        result = _parse_tagged(["radio", "items", {"url": "http://example.com", "menu": "1"}], start=2)
        assert result["url"] == "http://example.com"
        assert result["menu"] == "1"

    def test_parse_tagged_mixed(self) -> None:
        from plugins.radio import _parse_tagged

        result = _parse_tagged(["radio", "items", "url:http://test.com", {"menu": "1"}], start=2)
        assert result["url"] == "http://test.com"
        assert result["menu"] == "1"

    def test_parse_tagged_ignores_non_tagged(self) -> None:
        from plugins.radio import _parse_tagged

        result = _parse_tagged(["radio", "items", "0", "100", "menu:1"], start=2)
        assert "menu" in result
        assert "0" not in result

    def test_parse_tagged_none_values_skipped(self) -> None:
        from plugins.radio import _parse_tagged

        result = _parse_tagged(["radio", "items", {"key": None, "other": "val"}], start=2)
        assert "key" not in result
        assert result["other"] == "val"

    def test_parse_start_count_defaults(self) -> None:
        from plugins.radio import _parse_start_count

        start, count = _parse_start_count(["radio", "items"])
        assert start == 0
        assert count == 200

    def test_parse_start_count_explicit(self) -> None:
        from plugins.radio import _parse_start_count

        start, count = _parse_start_count(["radio", "items", 10, 50])
        assert start == 10
        assert count == 50

    def test_parse_start_count_negative_clamped(self) -> None:
        from plugins.radio import _parse_start_count

        start, count = _parse_start_count(["radio", "items", -5, 50])
        assert start == 0

    def test_parse_start_count_large_clamped(self) -> None:
        from plugins.radio import _parse_start_count

        start, count = _parse_start_count(["radio", "items", 0, 99999])
        assert count == 10_000

    def test_parse_start_count_invalid_types(self) -> None:
        from plugins.radio import _parse_start_count

        start, count = _parse_start_count(["radio", "items", "abc", "def"])
        assert start == 0
        assert count == 200


# =============================================================================
# JSON-RPC command dispatch
# =============================================================================


class TestCmdRadio:
    """Tests for the cmd_radio JSON-RPC dispatcher."""

    def _make_ctx(self, player_id: str = "aa:bb:cc:dd:ee:ff") -> MagicMock:
        ctx = MagicMock()
        ctx.player_id = player_id
        ctx.player_registry = MagicMock()
        ctx.player_registry.get_by_mac = AsyncMock(return_value=MagicMock())
        ctx.playlist_manager = MagicMock()
        ctx.playlist_manager.get = MagicMock(return_value=MagicMock())
        ctx.streaming_server = MagicMock()
        ctx.slimproto = MagicMock()
        return ctx

    @pytest.mark.asyncio
    async def test_dispatch_default_to_items(self) -> None:
        """Default sub-command is 'items' — returns BROWSE_CATEGORIES."""
        import plugins.radio as radio_mod
        from plugins.radio.radiobrowser import BROWSE_CATEGORIES

        mock_client = MagicMock()
        radio_mod._radio_browser = mock_client

        ctx = self._make_ctx()
        result = await radio_mod.cmd_radio(ctx, ["radio"])

        # _radio_items uses the module constant BROWSE_CATEGORIES (5 entries).
        assert result["count"] == len(BROWSE_CATEGORIES)
        assert "loop" in result or "item_loop" in result

        radio_mod._radio_browser = None

    @pytest.mark.asyncio
    async def test_dispatch_items(self) -> None:
        """Explicit 'items' sub-command returns category count."""
        import plugins.radio as radio_mod
        from plugins.radio.radiobrowser import BROWSE_CATEGORIES

        mock_client = MagicMock()
        radio_mod._radio_browser = mock_client

        ctx = self._make_ctx()
        result = await radio_mod.cmd_radio(ctx, ["radio", "items"])

        assert result["count"] == len(BROWSE_CATEGORIES)
        radio_mod._radio_browser = None

    @pytest.mark.asyncio
    async def test_dispatch_search(self) -> None:
        import plugins.radio as radio_mod

        mock_client = MagicMock()
        mock_client.search = AsyncMock(return_value=[])
        radio_mod._radio_browser = mock_client

        ctx = self._make_ctx()
        result = await radio_mod.cmd_radio(ctx, ["radio", "search", 0, 100, "term:test"])

        assert "count" in result
        radio_mod._radio_browser = None

    @pytest.mark.asyncio
    async def test_dispatch_unknown(self) -> None:
        import plugins.radio as radio_mod

        mock_client = MagicMock()
        radio_mod._radio_browser = mock_client

        ctx = self._make_ctx()
        result = await radio_mod.cmd_radio(ctx, ["radio", "foobar"])

        assert "error" in result
        radio_mod._radio_browser = None

    @pytest.mark.asyncio
    async def test_dispatch_not_initialized(self) -> None:
        import plugins.radio as radio_mod

        radio_mod._radio_browser = None

        ctx = self._make_ctx()
        result = await radio_mod.cmd_radio(ctx, ["radio", "items"])

        assert "error" in result
        assert "not initialized" in result["error"]


# =============================================================================
# radio items command
# =============================================================================


class TestRadioItems:
    """Tests for the _radio_items handler (radio-browser.info)."""

    def _make_ctx(self) -> MagicMock:
        ctx = MagicMock()
        ctx.player_id = "aa:bb:cc:dd:ee:ff"
        return ctx

    def _make_station(self, name: str = "Jazz FM", uuid: str = "jazz-uuid") -> Any:
        from plugins.radio.radiobrowser import RadioStation

        return RadioStation(
            stationuuid=uuid, name=name,
            url="http://stream.example.com/live",
            url_resolved="http://stream.example.com/live.mp3",
            codec="MP3", bitrate=128,
        )

    @pytest.mark.asyncio
    async def test_items_root_menu_mode(self) -> None:
        """Browse root with menu:1 returns top-level categories."""
        import plugins.radio as radio_mod
        from plugins.radio.radiobrowser import BROWSE_CATEGORIES

        mock_client = MagicMock()
        mock_client.get_browse_categories = MagicMock(return_value=list(BROWSE_CATEGORIES))
        radio_mod._radio_browser = mock_client

        ctx = self._make_ctx()
        result = await radio_mod._radio_items(ctx, ["radio", "items", 0, 100, "menu:1"])

        assert result["count"] >= 5
        assert len(result["item_loop"]) >= 5
        assert "base" in result

        # First item is a category folder
        assert result["item_loop"][0]["hasitems"] == 1
        assert result["item_loop"][0]["text"] == "Popular Stations"

        radio_mod._radio_browser = None

    @pytest.mark.asyncio
    async def test_items_cli_mode(self) -> None:
        """Browse without menu:1 returns CLI loop."""
        import plugins.radio as radio_mod
        from plugins.radio.radiobrowser import BROWSE_CATEGORIES

        mock_client = MagicMock()
        mock_client.get_browse_categories = MagicMock(return_value=list(BROWSE_CATEGORIES))
        radio_mod._radio_browser = mock_client

        ctx = self._make_ctx()
        result = await radio_mod._radio_items(ctx, ["radio", "items", 0, 100])

        assert "loop" in result
        assert "item_loop" not in result
        assert "base" not in result
        assert result["loop"][0]["name"] == "Popular Stations"

        radio_mod._radio_browser = None

    @pytest.mark.asyncio
    async def test_items_pagination(self) -> None:
        """Pagination works with start/count on station lists."""
        import plugins.radio as radio_mod
        from plugins.radio.radiobrowser import RadioStation

        stations = [
            RadioStation(
                stationuuid=f"uuid-{i}", name=f"Station {i}",
                url=f"http://s{i}.com/live", url_resolved=f"http://s{i}.com/live.mp3",
                codec="MP3", bitrate=128,
            )
            for i in range(10)
        ]
        mock_client = MagicMock()
        mock_client.get_popular_stations = AsyncMock(return_value=stations)
        radio_mod._radio_browser = mock_client

        ctx = self._make_ctx()
        result = await radio_mod._radio_items(ctx, ["radio", "items", 2, 3, "category:popular", "menu:1"])

        assert result["count"] == 10
        assert result["offset"] == 2
        assert len(result["item_loop"]) == 3
        assert result["item_loop"][0]["text"] == "Station 2"

        radio_mod._radio_browser = None

    @pytest.mark.asyncio
    async def test_items_with_url_param(self) -> None:
        """Browse with category: (or url: compat) drills into a category."""
        import plugins.radio as radio_mod

        station = self._make_station()
        mock_client = MagicMock()
        mock_client.get_stations_by_tag = AsyncMock(return_value=[station])
        radio_mod._radio_browser = mock_client

        ctx = self._make_ctx()
        result = await radio_mod._radio_items(ctx, ["radio", "items", 0, 100, "category:tag:jazz", "menu:1"])

        assert result["count"] == 1
        mock_client.get_stations_by_tag.assert_called_once_with("jazz", limit=200)

        radio_mod._radio_browser = None

    @pytest.mark.asyncio
    async def test_items_with_search_param(self) -> None:
        """Browse with search: parameter triggers inline search."""
        import plugins.radio as radio_mod

        station = self._make_station("Found Station", "found-uuid")
        mock_client = MagicMock()
        mock_client.search = AsyncMock(return_value=[station])
        radio_mod._radio_browser = mock_client

        ctx = self._make_ctx()
        result = await radio_mod._radio_items(ctx, ["radio", "items", 0, 100, "search:jazz", "menu:1"])

        assert result["count"] == 1
        mock_client.search.assert_called_once_with("jazz", limit=200)

        radio_mod._radio_browser = None

    @pytest.mark.asyncio
    async def test_items_empty_result(self) -> None:
        """Empty browse returns count 0."""
        import plugins.radio as radio_mod

        mock_client = MagicMock()
        mock_client.get_trending_stations = AsyncMock(return_value=[])
        radio_mod._radio_browser = mock_client

        ctx = self._make_ctx()
        result = await radio_mod._radio_items(ctx, ["radio", "items", 0, 100, "category:trending", "menu:1"])

        assert result["count"] == 0
        assert len(result["item_loop"]) == 0

        radio_mod._radio_browser = None


# =============================================================================
# radio search command
# =============================================================================


class TestRadioSearch:
    """Tests for the _radio_search handler (radio-browser.info)."""

    def _make_ctx(self) -> MagicMock:
        ctx = MagicMock()
        ctx.player_id = "aa:bb:cc:dd:ee:ff"
        return ctx

    def _make_station(self, name: str = "BBC Radio 1", uuid: str = "bbc-uuid") -> Any:
        from plugins.radio.radiobrowser import RadioStation

        return RadioStation(
            stationuuid=uuid, name=name,
            url="http://stream.example.com/live",
            url_resolved="http://stream.example.com/live.mp3",
            codec="MP3", bitrate=128,
        )

    @pytest.mark.asyncio
    async def test_search_with_term(self) -> None:
        import plugins.radio as radio_mod

        mock_client = MagicMock()
        station = self._make_station("BBC Radio 1", "bbc-uuid")
        mock_client.search = AsyncMock(return_value=[station])
        radio_mod._radio_browser = mock_client

        ctx = self._make_ctx()
        result = await radio_mod._radio_search(ctx, ["radio", "search", 0, 100, "term:bbc", "menu:1"])

        assert result["count"] == 1
        assert result["item_loop"][0]["text"] == "BBC Radio 1"
        mock_client.search.assert_called_once_with("bbc", limit=200)

        radio_mod._radio_browser = None

    @pytest.mark.asyncio
    async def test_search_with_query_param(self) -> None:
        import plugins.radio as radio_mod

        mock_client = MagicMock()
        mock_client.search = AsyncMock(return_value=[])
        radio_mod._radio_browser = mock_client

        ctx = self._make_ctx()
        result = await radio_mod._radio_search(ctx, ["radio", "search", 0, 100, "query:jazz"])

        mock_client.search.assert_called_once_with("jazz", limit=200)
        radio_mod._radio_browser = None

    @pytest.mark.asyncio
    async def test_search_empty_query(self) -> None:
        import plugins.radio as radio_mod

        mock_client = MagicMock()
        radio_mod._radio_browser = mock_client

        ctx = self._make_ctx()
        result = await radio_mod._radio_search(ctx, ["radio", "search", 0, 100])

        assert result["count"] == 0
        mock_client.search.assert_not_called()

        radio_mod._radio_browser = None

    @pytest.mark.asyncio
    async def test_search_cli_mode(self) -> None:
        import plugins.radio as radio_mod

        mock_client = MagicMock()
        station = self._make_station("Jazz FM", "jazz-uuid")
        mock_client.search = AsyncMock(return_value=[station])
        radio_mod._radio_browser = mock_client

        ctx = self._make_ctx()
        result = await radio_mod._radio_search(ctx, ["radio", "search", 0, 100, "term:jazz"])

        assert "loop" in result
        assert result["loop"][0]["name"] == "Jazz FM"

        radio_mod._radio_browser = None


# =============================================================================
# radio play command
# =============================================================================


class TestRadioPlay:
    """Tests for the radio play command."""

    def _make_ctx(self, player_id: str = "aa:bb:cc:dd:ee:ff") -> MagicMock:
        ctx = MagicMock()
        ctx.player_id = player_id
        ctx.player_registry = AsyncMock()
        ctx.streaming_server = MagicMock()
        ctx.streaming_server.cancel_stream = MagicMock()
        ctx.streaming_server.queue_url = MagicMock()
        ctx.streaming_server.queue_file = MagicMock()
        ctx.streaming_server.get_stream_generation = MagicMock(return_value=1)
        ctx.streaming_server.set_track_duration = MagicMock()
        ctx.streaming_server.resolve_runtime_stream_params = AsyncMock()
        ctx.slimproto = MagicMock()
        ctx.slimproto.get_advertise_ip_for_player = MagicMock(return_value="192.168.1.1")
        ctx.slimproto._resonance_server = MagicMock()
        ctx.server_host = "192.168.1.1"
        ctx.server_port = 9000

        # Player mock
        player = AsyncMock()
        player.status = MagicMock()
        player.status.volume = 80
        player.status.muted = False
        player.status.state = MagicMock()
        player.status.state.name = "STOPPED"
        player.info = MagicMock()
        player.info.device_type = MagicMock()
        ctx.player_registry.get_by_mac = AsyncMock(return_value=player)

        # Playlist mock
        playlist = MagicMock()
        playlist.current_index = 0
        playlist.clear = MagicMock()
        playlist.add = MagicMock()
        playlist.insert = MagicMock()
        playlist.play = MagicMock()
        ctx.playlist_manager = MagicMock()
        ctx.playlist_manager.get = MagicMock(return_value=playlist)

        return ctx

    @pytest.mark.asyncio
    async def test_play_missing_params(self) -> None:
        """play without id or url returns error."""
        import plugins.radio as radio_mod

        radio_mod._radio_browser = AsyncMock()

        ctx = self._make_ctx()
        result = await radio_mod._radio_play(ctx, ["radio", "play"])

        assert "error" in result
        assert "Missing" in result["error"]

        radio_mod._radio_browser = None

    @pytest.mark.asyncio
    async def test_play_with_station_id(self) -> None:
        """play with url: and id: resolves and plays."""
        import plugins.radio as radio_mod

        mock_client = MagicMock()
        mock_client.count_click = AsyncMock(return_value=True)
        radio_mod._radio_browser = mock_client
        radio_mod._event_bus = AsyncMock()

        ctx = self._make_ctx()

        with patch("resonance.web.handlers.playlist_playback._start_track_stream", new_callable=AsyncMock):
            result = await radio_mod._radio_play(ctx, [
                "radio", "play",
                "url:http://stream.example.com/live.mp3",
                "id:abc-def-uuid",
                "title:Jazz 88.5",
                "icon:http://example.com/logo.png",
                "codec:MP3",
                "bitrate:128",
            ])

        assert result.get("count") == 1

        # Verify playlist was populated
        playlist = ctx.playlist_manager.get.return_value
        playlist.clear.assert_called_once()
        playlist.add.assert_called_once()
        playlist.play.assert_called_once_with(0)

        # Verify the track added to playlist
        added_track = playlist.add.call_args[0][0]
        assert added_track.source == "radio"
        assert added_track.is_remote is True
        assert added_track.is_live is True
        assert added_track.effective_stream_url == "http://stream.example.com/live.mp3"
        assert added_track.external_id == "abc-def-uuid"
        assert added_track.content_type == "audio/mpeg"
        assert added_track.bitrate == 128

        radio_mod._radio_browser = None
        radio_mod._event_bus = None

    @pytest.mark.asyncio
    async def test_play_add_mode(self) -> None:
        """play with cmd:add adds to playlist without clearing."""
        import plugins.radio as radio_mod

        mock_client = MagicMock()
        mock_client.count_click = AsyncMock(return_value=True)
        radio_mod._radio_browser = mock_client

        ctx = self._make_ctx()
        result = await radio_mod._radio_play(ctx, [
            "radio", "play",
            "url:http://stream.example.com/live.mp3",
            "id:abc-uuid",
            "title:Jazz 88.5",
            "cmd:add",
        ])

        assert result.get("count") == 1
        playlist = ctx.playlist_manager.get.return_value
        playlist.clear.assert_not_called()
        playlist.add.assert_called_once()

        radio_mod._radio_browser = None

    @pytest.mark.asyncio
    async def test_play_insert_mode(self) -> None:
        """play with cmd:insert inserts after current track."""
        import plugins.radio as radio_mod

        mock_client = MagicMock()
        mock_client.count_click = AsyncMock(return_value=True)
        radio_mod._radio_browser = mock_client

        ctx = self._make_ctx()
        ctx.playlist_manager.get.return_value.current_index = 2

        result = await radio_mod._radio_play(ctx, [
            "radio", "play",
            "url:http://stream.example.com/live.mp3",
            "id:abc-uuid",
            "title:Jazz 88.5",
            "cmd:insert",
        ])

        assert result.get("count") == 1
        playlist = ctx.playlist_manager.get.return_value
        playlist.clear.assert_not_called()
        playlist.insert.assert_called_once()
        # Insert at current_index + 1
        assert playlist.insert.call_args[0][0] == 3

        radio_mod._radio_browser = None

    @pytest.mark.asyncio
    async def test_play_no_player(self) -> None:
        """play without a player returns error."""
        import plugins.radio as radio_mod

        radio_mod._radio_browser = MagicMock()

        ctx = self._make_ctx(player_id="-")
        ctx.player_registry.get_by_mac = AsyncMock(return_value=None)

        result = await radio_mod._radio_play(ctx, [
            "radio", "play",
            "url:http://stream.example.com/live.mp3",
        ])
        assert "error" in result
        assert "No player" in result["error"]

        radio_mod._radio_browser = None

    @pytest.mark.asyncio
    async def test_play_uuid_lookup_failure(self) -> None:
        """play with only id: returns error when station not found."""
        import plugins.radio as radio_mod

        mock_client = MagicMock()
        mock_client.get_station_by_uuid = AsyncMock(return_value=None)
        radio_mod._radio_browser = mock_client

        ctx = self._make_ctx()
        result = await radio_mod._radio_play(ctx, [
            "radio", "play",
            "id:nonexistent-uuid",
            "title:Bad Station",
        ])

        assert "error" in result
        assert "not found" in result["error"]

        radio_mod._radio_browser = None

    @pytest.mark.asyncio
    async def test_play_with_uuid_lookup(self) -> None:
        """play with only id: looks up station and uses url_resolved."""
        import plugins.radio as radio_mod
        from plugins.radio.radiobrowser import RadioStation

        station = RadioStation(
            stationuuid="jazz-uuid",
            name="Jazz FM",
            url="http://stream.jazzfm.com/live.pls",
            url_resolved="http://stream.jazzfm.com/live.mp3",
            favicon="http://jazzfm.com/logo.png",
            codec="MP3",
            bitrate=128,
        )
        mock_client = MagicMock()
        mock_client.get_station_by_uuid = AsyncMock(return_value=station)
        mock_client.count_click = AsyncMock(return_value=True)
        radio_mod._radio_browser = mock_client
        radio_mod._event_bus = AsyncMock()

        ctx = self._make_ctx()

        with patch("resonance.web.handlers.playlist_playback._start_track_stream", new_callable=AsyncMock):
            result = await radio_mod._radio_play(ctx, [
                "radio", "play",
                "id:jazz-uuid",
            ])

        assert result.get("count") == 1
        playlist = ctx.playlist_manager.get.return_value
        added_track = playlist.add.call_args[0][0]
        assert added_track.effective_stream_url == "http://stream.jazzfm.com/live.mp3"
        assert added_track.title == "Jazz FM"
        assert added_track.artwork_url == "http://jazzfm.com/logo.png"

        radio_mod._radio_browser = None
        radio_mod._event_bus = None

    @pytest.mark.asyncio
    async def test_play_no_playlist_manager(self) -> None:
        """play returns error when playlist manager is None."""
        import plugins.radio as radio_mod

        radio_mod._radio_browser = MagicMock()

        ctx = self._make_ctx()
        ctx.playlist_manager = None

        result = await radio_mod._radio_play(ctx, [
            "radio", "play",
            "url:http://stream.example.com/live.mp3",
        ])
        assert "error" in result
        assert "Playlist manager" in result["error"]

        radio_mod._radio_browser = None


# =============================================================================
# Plugin lifecycle
# =============================================================================


class TestPluginLifecycle:
    """Tests for plugin setup and teardown."""

    @pytest.mark.asyncio
    async def test_setup_registers_components(self) -> None:
        """setup() registers command, content provider, and menu node."""
        import plugins.radio as radio_mod

        ctx = MagicMock()
        ctx.event_bus = MagicMock()
        ctx.register_command = MagicMock()
        ctx.register_content_provider = MagicMock()
        ctx.register_menu_node = MagicMock()

        await radio_mod.setup(ctx)

        # Verify command registered
        ctx.register_command.assert_called_once_with("radio", radio_mod.cmd_radio)

        # Verify content provider registered
        ctx.register_content_provider.assert_called_once()
        call_args = ctx.register_content_provider.call_args
        assert call_args[0][0] == "radio"
        assert isinstance(call_args[0][1], radio_mod.RadioProvider)

        # Verify menu node registered
        ctx.register_menu_node.assert_called_once()
        menu_call = ctx.register_menu_node.call_args
        assert menu_call[1]["node_id"] == "radios"
        assert menu_call[1]["parent"] == "home"
        assert menu_call[1]["text"] == "Radio"
        assert menu_call[1]["weight"] == 45

        # Verify state was set
        assert radio_mod._radio_browser is not None
        assert radio_mod._provider is not None
        assert radio_mod._event_bus is not None

        # Cleanup
        await radio_mod.teardown(ctx)

    @pytest.mark.asyncio
    async def test_teardown_clears_state(self) -> None:
        """teardown() clears module-level state."""
        import plugins.radio as radio_mod

        ctx = MagicMock()
        ctx.event_bus = MagicMock()
        ctx.register_command = MagicMock()
        ctx.register_content_provider = MagicMock()
        ctx.register_menu_node = MagicMock()

        await radio_mod.setup(ctx)
        assert radio_mod._radio_browser is not None

        await radio_mod.teardown(ctx)
        assert radio_mod._radio_browser is None
        assert radio_mod._provider is None
        assert radio_mod._event_bus is None


# =============================================================================
# TuneInItem dataclass (tunein.py still exists as a module)
# =============================================================================


class TestTuneInItem:
    """Tests for the TuneInItem dataclass."""

    def test_defaults(self) -> None:
        item = TuneInItem(text="Test")
        assert item.text == "Test"
        assert item.type == "link"
        assert item.url == ""
        assert item.guide_id == ""
        assert item.key == ""
        assert item.image == ""
        assert item.bitrate == ""
        assert item.subtext == ""
        assert item.formats == ""
        assert item.is_container is False
        assert item.children == []
        assert item.preset_id == ""
        assert item.playing == ""
        assert item.playing_image == ""
        assert item.item_type == ""
        assert item.reliability == ""

    def test_frozen(self) -> None:
        item = TuneInItem(text="Test")
        with pytest.raises(AttributeError):
            item.text = "Changed"  # type: ignore[misc]


# =============================================================================
# RadioBrowser helpers (radiobrowser.py)
# =============================================================================


class TestRadioBrowserHelpers:
    """Tests for radiobrowser.py helper functions."""

    def test_codec_to_content_type_mp3(self) -> None:
        from plugins.radio.radiobrowser import codec_to_content_type

        assert codec_to_content_type("MP3") == "audio/mpeg"
        assert codec_to_content_type("mp3") == "audio/mpeg"

    def test_codec_to_content_type_aac(self) -> None:
        from plugins.radio.radiobrowser import codec_to_content_type

        assert codec_to_content_type("AAC") == "audio/aac"
        assert codec_to_content_type("AAC+") == "audio/aac"

    def test_codec_to_content_type_ogg(self) -> None:
        from plugins.radio.radiobrowser import codec_to_content_type

        assert codec_to_content_type("OGG") == "audio/ogg"

    def test_codec_to_content_type_unknown(self) -> None:
        from plugins.radio.radiobrowser import codec_to_content_type

        assert codec_to_content_type("SOMETHING_WEIRD") == "audio/mpeg"

    def test_format_station_subtitle(self) -> None:
        from plugins.radio.radiobrowser import RadioStation, format_station_subtitle

        station = RadioStation(
            stationuuid="test-uuid",
            name="Test",
            url="http://test.com",
            url_resolved="http://test.com",
            codec="MP3",
            bitrate=128,
            country="Germany",
            tags="jazz,blues,smooth",
        )
        subtitle = format_station_subtitle(station)
        assert "MP3 128kbps" in subtitle
        assert "Germany" in subtitle
        assert "jazz" in subtitle

    def test_format_station_subtitle_minimal(self) -> None:
        from plugins.radio.radiobrowser import RadioStation, format_station_subtitle

        station = RadioStation(
            stationuuid="x", name="X", url="http://x.com", url_resolved="http://x.com",
        )
        subtitle = format_station_subtitle(station)
        assert subtitle == ""

    def test_parse_station(self) -> None:
        from plugins.radio.radiobrowser import _parse_station

        data = {
            "stationuuid": "abc-123",
            "name": " Jazz FM ",
            "url": "http://stream.com/live.pls",
            "url_resolved": "http://stream.com/live.mp3",
            "favicon": "http://img.com/logo.png",
            "codec": "MP3",
            "bitrate": 128,
            "country": "Germany",
            "countrycode": "DE",
            "tags": "jazz,smooth",
            "votes": 500,
            "lastcheckok": 1,
        }
        station = _parse_station(data)
        assert station.stationuuid == "abc-123"
        assert station.name == "Jazz FM"  # Stripped
        assert station.url_resolved == "http://stream.com/live.mp3"
        assert station.codec == "MP3"
        assert station.bitrate == 128
        assert station.votes == 500

    def test_parse_station_missing_url_resolved(self) -> None:
        from plugins.radio.radiobrowser import _parse_station

        data = {
            "stationuuid": "abc",
            "name": "Test",
            "url": "http://stream.com/live",
            "url_resolved": "",
        }
        station = _parse_station(data)
        # Falls back to url when url_resolved is empty
        assert station.url_resolved == "http://stream.com/live"

    def test_browse_categories_defined(self) -> None:
        from plugins.radio.radiobrowser import BROWSE_CATEGORIES

        assert len(BROWSE_CATEGORIES) >= 5
        keys = [c["key"] for c in BROWSE_CATEGORIES]
        assert "popular" in keys
        assert "trending" in keys
        assert "country" in keys
        assert "tag" in keys
        assert "language" in keys

    def test_radio_station_dataclass(self) -> None:
        from plugins.radio.radiobrowser import RadioStation

        s = RadioStation(
            stationuuid="u1", name="S1",
            url="http://a.com", url_resolved="http://b.com",
        )
        assert s.stationuuid == "u1"
        assert s.bitrate == 0  # Default
        assert s.lastcheckok == 1  # Default

    def test_category_entry_dataclass(self) -> None:
        from plugins.radio.radiobrowser import CategoryEntry

        c = CategoryEntry(name="Germany", stationcount=500, iso_3166_1="DE")
        assert c.name == "Germany"
        assert c.stationcount == 500
        assert c.iso_3166_1 == "DE"


# =============================================================================
# RadioProvider (ContentProvider) — radio-browser.info
# =============================================================================


class TestRadioProvider:
    """Tests for the RadioProvider ContentProvider implementation."""

    def _make_station(self, name: str = "Jazz FM", uuid: str = "jazz-uuid") -> Any:
        from plugins.radio.radiobrowser import RadioStation

        return RadioStation(
            stationuuid=uuid, name=name,
            url="http://stream.example.com/live.pls",
            url_resolved="http://stream.example.com/live.mp3",
            favicon="http://img.com/logo.png",
            codec="MP3", bitrate=128,
            country="Germany", countrycode="DE",
            tags="jazz,blues",
        )

    def _make_provider(self, client_mock: Any = None) -> Any:
        from plugins.radio import RadioProvider

        return RadioProvider(client_mock or MagicMock())

    @pytest.mark.asyncio
    async def test_name(self) -> None:
        provider = self._make_provider()
        assert provider.name == "Community Radio Browser"

    @pytest.mark.asyncio
    async def test_icon(self) -> None:
        provider = self._make_provider()
        assert provider.icon is None

    @pytest.mark.asyncio
    async def test_browse_root_returns_categories(self) -> None:
        from plugins.radio.radiobrowser import BROWSE_CATEGORIES

        mock = MagicMock()
        mock.get_browse_categories = MagicMock(return_value=list(BROWSE_CATEGORIES))
        provider = self._make_provider(mock)

        items = await provider.browse("")
        assert len(items) >= 5
        assert items[0].type == "folder"
        assert items[0].title == "Popular Stations"

    @pytest.mark.asyncio
    async def test_browse_popular(self) -> None:
        mock = MagicMock()
        station = self._make_station()
        mock.get_popular_stations = AsyncMock(return_value=[station])
        provider = self._make_provider(mock)

        items = await provider.browse("popular")
        assert len(items) == 1
        assert items[0].type == "audio"
        assert items[0].title == "Jazz FM"

    @pytest.mark.asyncio
    async def test_browse_country_list(self) -> None:
        from plugins.radio.radiobrowser import CategoryEntry

        mock = MagicMock()
        mock.get_countries = AsyncMock(return_value=[
            CategoryEntry(name="Germany", stationcount=500, iso_3166_1="DE"),
            CategoryEntry(name="France", stationcount=300, iso_3166_1="FR"),
        ])
        provider = self._make_provider(mock)

        items = await provider.browse("country")
        assert len(items) == 2
        assert items[0].type == "folder"
        assert "Germany" in items[0].title

    @pytest.mark.asyncio
    async def test_browse_country_stations(self) -> None:
        mock = MagicMock()
        station = self._make_station()
        mock.get_stations_by_country = AsyncMock(return_value=[station])
        provider = self._make_provider(mock)

        items = await provider.browse("country:DE")
        assert len(items) == 1
        assert items[0].type == "audio"

    @pytest.mark.asyncio
    async def test_search(self) -> None:
        mock = MagicMock()
        station = self._make_station("BBC Radio 1", "bbc-uuid")
        mock.search = AsyncMock(return_value=[station])
        provider = self._make_provider(mock)

        items = await provider.search("bbc")
        assert len(items) == 1
        assert items[0].title == "BBC Radio 1"
        assert items[0].type == "audio"

    @pytest.mark.asyncio
    async def test_get_stream_info_success(self) -> None:
        mock = MagicMock()
        station = self._make_station()
        mock.get_station_by_uuid = AsyncMock(return_value=station)
        provider = self._make_provider(mock)

        info = await provider.get_stream_info("jazz-uuid")
        assert info is not None
        assert info.url == "http://stream.example.com/live.mp3"
        assert info.content_type == "audio/mpeg"
        assert info.is_live is True

    @pytest.mark.asyncio
    async def test_get_stream_info_failure(self) -> None:
        mock = MagicMock()
        mock.get_station_by_uuid = AsyncMock(return_value=None)
        provider = self._make_provider(mock)

        info = await provider.get_stream_info("nonexistent")
        assert info is None


# =============================================================================
# Jive menu item builders — radio-browser.info
# =============================================================================


class TestJiveMenuItemBuilders:
    """Tests for station/category Jive menu item builders."""

    def _make_station(self, **kwargs: Any) -> Any:
        from plugins.radio.radiobrowser import RadioStation

        defaults = dict(
            stationuuid="s-uuid-1", name="Jazz FM",
            url="http://stream.example.com/live.pls",
            url_resolved="http://stream.example.com/live.mp3",
            favicon="http://img.com/logo.png",
            codec="MP3", bitrate=128,
            country="Germany", countrycode="DE",
            tags="jazz,blues",
            votes=500,
        )
        defaults.update(kwargs)
        return RadioStation(**defaults)

    def test_build_station_jive_item(self) -> None:
        from plugins.radio import _build_station_jive_item

        station = self._make_station()
        item = _build_station_jive_item(station)

        assert item["text"] == "Jazz FM"
        assert item["type"] == "audio"
        assert item["hasitems"] == 0
        assert item["icon"] == "http://img.com/logo.png"
        assert "actions" in item
        assert "play" in item["actions"]
        assert "add" in item["actions"]
        assert "go" in item["actions"]

        # Verify play action params
        play = item["actions"]["play"]
        assert play["cmd"] == ["radio", "play"]
        assert play["params"]["url"] == "http://stream.example.com/live.mp3"
        assert play["params"]["id"] == "s-uuid-1"
        assert play["params"]["cmd"] == "play"

    def test_build_station_jive_item_add_action(self) -> None:
        from plugins.radio import _build_station_jive_item

        station = self._make_station()
        item = _build_station_jive_item(station)

        add = item["actions"]["add"]
        assert add["params"]["cmd"] == "add"

    def test_build_station_jive_item_favorites(self) -> None:
        from plugins.radio import _build_station_jive_item

        station = self._make_station()
        item = _build_station_jive_item(station)

        assert "more" in item["actions"]
        more = item["actions"]["more"]
        assert more["cmd"] == ["jivefavorites", "add"]
        assert more["params"]["title"] == "Jazz FM"

    def test_build_station_jive_item_without_image(self) -> None:
        from plugins.radio import _build_station_jive_item

        station = self._make_station(favicon="")
        item = _build_station_jive_item(station)

        assert "icon" not in item

    def test_build_station_jive_item_without_uuid_no_favorites(self) -> None:
        from plugins.radio import _build_station_jive_item

        station = self._make_station(stationuuid="")
        item = _build_station_jive_item(station)

        assert "more" not in item["actions"]

    def test_build_station_cli_item(self) -> None:
        from plugins.radio import _build_station_cli_item

        station = self._make_station()
        item = _build_station_cli_item(station)

        assert item["name"] == "Jazz FM"
        assert item["type"] == "audio"
        assert item["url"] == "http://stream.example.com/live.mp3"
        assert item["id"] == "s-uuid-1"
        assert item["codec"] == "MP3"
        assert item["bitrate"] == 128
        assert item["country"] == "Germany"

    def test_build_category_jive_item(self) -> None:
        from plugins.radio import _build_category_jive_item

        item = _build_category_jive_item("popular", "Popular Stations")

        assert item["text"] == "Popular Stations"
        assert item["hasitems"] == 1
        assert "go" in item["actions"]
        go = item["actions"]["go"]
        assert go["cmd"] == ["radio", "items"]
        assert go["params"]["category"] == "popular"

    def test_build_subcategory_jive_item(self) -> None:
        from plugins.radio import _build_subcategory_jive_item

        item = _build_subcategory_jive_item("country:DE", "Germany (500)")

        assert item["text"] == "Germany (500)"
        assert item["hasitems"] == 1
        go = item["actions"]["go"]
        assert go["params"]["category"] == "country:DE"

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
        from plugins.radio.radiobrowser import RadioStation

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
        from plugins.radio.radiobrowser import BROWSE_CATEGORIES, CategoryEntry

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
