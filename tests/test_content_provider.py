"""
Tests for Content Provider Phase 2.

Tests cover:
- PlaylistTrack remote fields and from_url() constructor
- PlaylistTrack effective_stream_url property
- Playlist serialization/deserialization of remote tracks
- StreamingServer queue_url / resolve_stream / is_remote_stream
- RemoteStreamInfo dataclass
- ContentProvider ABC and concrete implementations
- ContentProviderRegistry register/unregister/browse/search/get_stream_info
- ContentProviderRegistry search_all across multiple providers
- PluginContext register_content_provider / unregister_content_provider / cleanup
- Backward compatibility: local-only playlists unchanged
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from resonance.content_provider import (
    BrowseItem,
    ContentProvider,
    ContentProviderRegistry,
    StreamInfo,
)
from resonance.core.playlist import (
    AlbumId,
    ArtistId,
    Playlist,
    PlaylistTrack,
    RepeatMode,
    ShuffleMode,
    TrackId,
    _deserialize_playlist,
    _serialize_playlist,
)
from resonance.streaming.server import (
    RemoteStreamInfo,
    ResolvedStream,
    StreamingServer,
)

# =============================================================================
# PlaylistTrack remote fields
# =============================================================================


class TestPlaylistTrackRemoteFields:
    """Tests for PlaylistTrack remote/content-provider fields."""

    def test_default_values_are_local(self) -> None:
        """A plain PlaylistTrack defaults to local source."""
        track = PlaylistTrack(track_id=TrackId(1), path="/music/song.mp3")
        assert track.source == "local"
        assert track.stream_url is None
        assert track.external_id is None
        assert track.artwork_url is None
        assert track.is_remote is False
        assert track.content_type is None
        assert track.bitrate == 0
        assert track.is_live is False

    def test_from_path_is_local(self) -> None:
        """from_path() creates a local track."""
        track = PlaylistTrack.from_path("/music/song.flac")
        assert track.source == "local"
        assert track.is_remote is False
        assert track.stream_url is None

    def test_from_url_basic(self) -> None:
        """from_url() creates a remote track with expected defaults."""
        track = PlaylistTrack.from_url("https://stream.example.com/live.mp3")
        assert track.is_remote is True
        assert track.source == "external"
        assert track.path == "https://stream.example.com/live.mp3"
        assert track.stream_url is None
        assert track.title == "live.mp3"  # fallback from URL

    def test_from_url_with_all_fields(self) -> None:
        """from_url() with all keyword arguments."""
        track = PlaylistTrack.from_url(
            "https://radio.example.com/station/123",
            title="Jazz FM",
            artist="Jazz FM Network",
            album="Internet Radio",
            duration_ms=0,
            source="radio",
            stream_url="https://cdn.example.com/stream.aac",
            external_id="tunein:s123456",
            artwork_url="https://img.example.com/logo.png",
            content_type="audio/aac",
            bitrate=128,
            is_live=True,
        )
        assert track.is_remote is True
        assert track.source == "radio"
        assert track.path == "https://radio.example.com/station/123"
        assert track.stream_url == "https://cdn.example.com/stream.aac"
        assert track.title == "Jazz FM"
        assert track.artist == "Jazz FM Network"
        assert track.album == "Internet Radio"
        assert track.duration_ms == 0
        assert track.external_id == "tunein:s123456"
        assert track.artwork_url == "https://img.example.com/logo.png"
        assert track.content_type == "audio/aac"
        assert track.bitrate == 128
        assert track.is_live is True
        assert track.track_id is None
        assert track.album_id is None
        assert track.artist_id is None

    def test_from_url_podcast_source(self) -> None:
        """from_url() with podcast source type."""
        track = PlaylistTrack.from_url(
            "https://podcast.example.com/episode/42.mp3",
            title="Episode 42: Testing",
            artist="Test Podcast",
            source="podcast",
            duration_ms=3600000,
            content_type="audio/mpeg",
            bitrate=192,
        )
        assert track.source == "podcast"
        assert track.is_remote is True
        assert track.is_live is False
        assert track.duration_ms == 3600000

    def test_from_url_title_fallback(self) -> None:
        """from_url() uses last URL segment as title fallback."""
        track = PlaylistTrack.from_url("https://example.com/path/to/stream.mp3")
        assert track.title == "stream.mp3"

    def test_from_url_title_fallback_no_slash(self) -> None:
        """from_url() with URL without slashes."""
        track = PlaylistTrack.from_url("http://example.com")
        assert track.title == "example.com"


class TestPlaylistTrackEffectiveStreamUrl:
    """Tests for PlaylistTrack.effective_stream_url property."""

    def test_local_track_returns_path(self) -> None:
        """For local tracks, effective_stream_url returns path."""
        track = PlaylistTrack.from_path("/music/song.mp3")
        assert track.effective_stream_url == str(Path("/music/song.mp3"))

    def test_remote_without_stream_url_returns_path(self) -> None:
        """For remote tracks without stream_url, returns path (the canonical URL)."""
        track = PlaylistTrack.from_url("https://stream.example.com/live.mp3")
        assert track.effective_stream_url == "https://stream.example.com/live.mp3"

    def test_remote_with_stream_url_returns_stream_url(self) -> None:
        """For remote tracks with a resolved stream_url, returns stream_url."""
        track = PlaylistTrack.from_url(
            "https://radio.example.com/station/123",
            stream_url="https://cdn.example.com/actual-stream.aac",
        )
        assert track.effective_stream_url == "https://cdn.example.com/actual-stream.aac"

    def test_local_track_with_stream_url_still_returns_path(self) -> None:
        """A local track with stream_url set still returns path (is_remote=False)."""
        track = PlaylistTrack(
            track_id=None,
            path="/music/song.mp3",
            stream_url="https://should-be-ignored.com",
            is_remote=False,
        )
        assert track.effective_stream_url == "/music/song.mp3"


# =============================================================================
# Playlist serialization/deserialization of remote tracks
# =============================================================================


class TestPlaylistSerializationRemote:
    """Tests for serialize/deserialize with remote track fields."""

    def _make_playlist_with_remote_track(self) -> Playlist:
        """Helper: create a playlist with one local and one remote track."""
        local = PlaylistTrack.from_path("/music/song.mp3")
        remote = PlaylistTrack.from_url(
            "https://stream.example.com/live.mp3",
            title="Live Radio",
            artist="RadioStation",
            source="radio",
            stream_url="https://cdn.example.com/stream.aac",
            external_id="tunein:s123",
            artwork_url="https://img.example.com/logo.png",
            content_type="audio/aac",
            bitrate=128,
            is_live=True,
        )
        playlist = Playlist(player_id="aa:bb:cc:dd:ee:ff", tracks=[local, remote])
        return playlist

    def test_serialize_local_track_no_remote_fields(self) -> None:
        """Serialized local tracks should NOT contain remote-specific keys."""
        playlist = Playlist(
            player_id="aa:bb:cc:dd:ee:ff",
            tracks=[PlaylistTrack.from_path("/music/song.mp3")],
        )
        data = _serialize_playlist(playlist)
        td = data["tracks"][0]
        assert "is_remote" not in td
        assert "source" not in td or td.get("source") == "local"
        assert "stream_url" not in td
        assert "external_id" not in td
        assert "artwork_url" not in td
        assert "content_type" not in td
        assert "bitrate" not in td
        assert "is_live" not in td

    def test_serialize_remote_track_includes_fields(self) -> None:
        """Serialized remote tracks MUST contain remote-specific keys."""
        playlist = self._make_playlist_with_remote_track()
        data = _serialize_playlist(playlist)
        td = data["tracks"][1]  # second track is remote
        assert td["is_remote"] is True
        assert td["source"] == "radio"
        assert td["stream_url"] == "https://cdn.example.com/stream.aac"
        assert td["external_id"] == "tunein:s123"
        assert td["artwork_url"] == "https://img.example.com/logo.png"
        assert td["content_type"] == "audio/aac"
        assert td["bitrate"] == 128
        assert td["is_live"] is True

    def test_roundtrip_preserves_remote_fields(self) -> None:
        """Serialize → deserialize roundtrip preserves all remote fields."""
        original = self._make_playlist_with_remote_track()
        data = _serialize_playlist(original)
        restored = _deserialize_playlist(data)

        assert len(restored.tracks) == 2

        local = restored.tracks[0]
        assert local.is_remote is False
        assert local.source == "local"

        remote = restored.tracks[1]
        assert remote.is_remote is True
        assert remote.source == "radio"
        assert remote.stream_url == "https://cdn.example.com/stream.aac"
        assert remote.external_id == "tunein:s123"
        assert remote.artwork_url == "https://img.example.com/logo.png"
        assert remote.content_type == "audio/aac"
        assert remote.bitrate == 128
        assert remote.is_live is True
        assert remote.title == "Live Radio"
        assert remote.artist == "RadioStation"

    def test_deserialize_legacy_format_without_remote_fields(self) -> None:
        """Playlists saved by older Resonance versions (no remote fields) load fine."""
        data = {
            "player_id": "aa:bb:cc:dd:ee:ff",
            "version": 1,
            "current_index": 0,
            "repeat_mode": 0,
            "shuffle_mode": 0,
            "updated_at": 1000.0,
            "tracks": [
                {
                    "track_id": 1,
                    "path": "/music/old-song.mp3",
                    "title": "Old Song",
                    "artist": "Old Artist",
                    "album": "Old Album",
                    "album_id": None,
                    "artist_id": None,
                    "duration_ms": 180000,
                }
            ],
        }
        playlist = _deserialize_playlist(data)
        track = playlist.tracks[0]
        assert track.is_remote is False
        assert track.source == "local"
        assert track.stream_url is None
        assert track.content_type is None
        assert track.bitrate == 0
        assert track.is_live is False

    def test_serialize_compact_no_false_booleans(self) -> None:
        """Remote fields with default/falsy values are omitted for compactness."""
        track = PlaylistTrack.from_url(
            "https://example.com/stream.mp3",
            title="Simple Remote",
            # No stream_url, external_id, artwork_url, content_type
            # bitrate=0 (default), is_live=False (default)
        )
        playlist = Playlist(player_id="test", tracks=[track])
        data = _serialize_playlist(playlist)
        td = data["tracks"][0]

        assert td["is_remote"] is True
        # Falsy fields should be absent
        assert "stream_url" not in td
        assert "external_id" not in td
        assert "artwork_url" not in td
        assert "content_type" not in td
        assert "bitrate" not in td
        assert "is_live" not in td


# =============================================================================
# StreamingServer — remote URL queuing
# =============================================================================


class TestStreamingServerRemoteUrl:
    """Tests for StreamingServer.queue_url / resolve_stream / is_remote_stream."""

    def _make_server(self) -> StreamingServer:
        return StreamingServer(host="0.0.0.0", port=9000)

    def test_queue_url_stores_remote_info(self) -> None:
        """queue_url() stores a RemoteStreamInfo."""
        server = self._make_server()
        mac = "aa:bb:cc:dd:ee:ff"
        server.queue_url(
            mac,
            "https://stream.example.com/live.mp3",
            content_type="audio/mpeg",
            is_live=True,
            title="Test Radio",
        )
        assert server.is_remote_stream(mac) is True

    def test_queue_url_clears_local_file(self) -> None:
        """queue_url() replaces any pending local file queue."""
        server = self._make_server()
        mac = "aa:bb:cc:dd:ee:ff"
        server.queue_file(mac, Path("/music/song.mp3"))
        assert server.get_queued_file(mac) is not None

        server.queue_url(mac, "https://stream.example.com/live.mp3")
        assert server.get_queued_file(mac) is None
        assert server.is_remote_stream(mac) is True

    def test_queue_file_clears_remote_url(self) -> None:
        """queue_file() replaces any pending remote URL."""
        server = self._make_server()
        mac = "aa:bb:cc:dd:ee:ff"
        server.queue_url(mac, "https://stream.example.com/live.mp3")
        assert server.is_remote_stream(mac) is True

        server.queue_file(mac, Path("/music/song.mp3"))
        assert server.is_remote_stream(mac) is False
        assert server.get_queued_file(mac) is not None

    def test_queue_url_increments_generation(self) -> None:
        """queue_url() increments the stream generation like queue_file."""
        server = self._make_server()
        mac = "aa:bb:cc:dd:ee:ff"

        server.queue_url(mac, "https://example.com/a.mp3")
        gen1 = server.get_stream_generation(mac)

        server.queue_url(mac, "https://example.com/b.mp3")
        gen2 = server.get_stream_generation(mac)

        assert gen2 > gen1

    def test_queue_url_cancels_previous_stream(self) -> None:
        """queue_url() cancels any active stream via CancellationToken."""
        server = self._make_server()
        mac = "aa:bb:cc:dd:ee:ff"

        server.queue_file(mac, Path("/music/song.mp3"))
        old_token = server.get_cancellation_token(mac)

        server.queue_url(mac, "https://example.com/live.mp3")
        assert old_token.cancelled is True

    def test_resolve_stream_remote(self) -> None:
        """resolve_stream() returns RemoteStreamInfo when URL is queued."""
        server = self._make_server()
        mac = "aa:bb:cc:dd:ee:ff"
        server.queue_url(
            mac,
            "https://stream.example.com/live.mp3",
            content_type="audio/aac",
            is_live=True,
            title="Jazz FM",
        )
        resolved = server.resolve_stream(mac)
        assert resolved.file_path is None
        assert resolved.remote is not None
        assert resolved.remote.url == "https://stream.example.com/live.mp3"
        assert resolved.remote.content_type == "audio/aac"
        assert resolved.remote.is_live is True
        assert resolved.remote.title == "Jazz FM"

    def test_resolve_stream_local_file(self) -> None:
        """resolve_stream() returns file_path when local file is queued."""
        server = self._make_server()
        mac = "aa:bb:cc:dd:ee:ff"
        server.queue_file(mac, Path("/music/song.mp3"))

        resolved = server.resolve_stream(mac)
        assert resolved.file_path == Path("/music/song.mp3")
        assert resolved.remote is None

    def test_resolve_stream_nothing_queued(self) -> None:
        """resolve_stream() returns empty ResolvedStream when nothing is queued."""
        server = self._make_server()
        resolved = server.resolve_stream("aa:bb:cc:dd:ee:ff")
        assert resolved.file_path is None
        assert resolved.remote is None

    def test_resolve_stream_none_player(self) -> None:
        """resolve_stream(None) returns empty ResolvedStream."""
        server = self._make_server()
        resolved = server.resolve_stream(None)
        assert resolved.file_path is None
        assert resolved.remote is None

    def test_is_remote_stream_false_for_local(self) -> None:
        """is_remote_stream() returns False for local files."""
        server = self._make_server()
        mac = "aa:bb:cc:dd:ee:ff"
        server.queue_file(mac, Path("/music/song.mp3"))
        assert server.is_remote_stream(mac) is False

    def test_is_remote_stream_false_for_unknown(self) -> None:
        """is_remote_stream() returns False for unknown player."""
        server = self._make_server()
        assert server.is_remote_stream("unknown:mac") is False

    def test_queue_url_clears_seek_and_offset(self) -> None:
        """queue_url() clears seek positions, byte offsets, start offsets."""
        server = self._make_server()
        mac = "aa:bb:cc:dd:ee:ff"
        # Set up some state via a file with seek
        server.queue_file_with_seek(mac, Path("/music/book.m4b"), start_seconds=120.0)
        assert server.get_seek_position(mac) is not None
        assert server.get_start_offset(mac) == 120.0

        # queue_url should clear all of it
        server.queue_url(mac, "https://example.com/stream.mp3")
        assert server.get_seek_position(mac) is None
        # get_start_offset returns 0.0 (not None) when no offset is active
        assert server.get_start_offset(mac) == 0.0

    def test_stop_clears_remote_urls(self) -> None:
        """StreamingServer.stop() clears remote URL queue."""
        server = self._make_server()
        mac = "aa:bb:cc:dd:ee:ff"
        server._running = True  # simulate started
        server.queue_url(mac, "https://example.com/stream.mp3")

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(server.stop())
        finally:
            loop.close()

        assert server.is_remote_stream(mac) is False


class TestRemoteStreamInfo:
    """Tests for RemoteStreamInfo dataclass."""

    def test_defaults(self) -> None:
        """RemoteStreamInfo has sensible defaults."""
        info = RemoteStreamInfo(url="https://example.com/stream.mp3")
        assert info.url == "https://example.com/stream.mp3"
        assert info.content_type == "audio/mpeg"
        assert info.is_live is False
        assert info.title == ""

    def test_all_fields(self) -> None:
        """RemoteStreamInfo stores all fields."""
        info = RemoteStreamInfo(
            url="https://cdn.example.com/stream.aac",
            content_type="audio/aac",
            is_live=True,
            title="Jazz FM",
        )
        assert info.url == "https://cdn.example.com/stream.aac"
        assert info.content_type == "audio/aac"
        assert info.is_live is True
        assert info.title == "Jazz FM"

    def test_frozen(self) -> None:
        """RemoteStreamInfo is immutable."""
        info = RemoteStreamInfo(url="https://example.com/stream.mp3")
        with pytest.raises(AttributeError):
            info.url = "changed"  # type: ignore[misc]


class TestResolvedStream:
    """Tests for ResolvedStream named tuple."""

    def test_local_file(self) -> None:
        """ResolvedStream with file_path only."""
        r = ResolvedStream(file_path=Path("/music/song.mp3"), remote=None)
        assert r.file_path == Path("/music/song.mp3")
        assert r.remote is None

    def test_remote_url(self) -> None:
        """ResolvedStream with remote only."""
        info = RemoteStreamInfo(url="https://example.com/stream.mp3")
        r = ResolvedStream(file_path=None, remote=info)
        assert r.file_path is None
        assert r.remote is info

    def test_empty(self) -> None:
        """ResolvedStream with neither."""
        r = ResolvedStream(file_path=None, remote=None)
        assert r.file_path is None
        assert r.remote is None


# =============================================================================
# ContentProvider ABC
# =============================================================================


class DummyProvider(ContentProvider):
    """A minimal ContentProvider for testing."""

    def __init__(self, provider_name: str = "DummyProvider") -> None:
        self._name = provider_name
        self._items: list[BrowseItem] = [
            BrowseItem(id="item1", title="Item 1", type="audio"),
            BrowseItem(id="folder1", title="Folder 1", type="folder"),
        ]

    @property
    def name(self) -> str:
        return self._name

    @property
    def icon(self) -> str | None:
        return "https://example.com/icon.png"

    async def browse(self, path: str = "") -> list[BrowseItem]:
        if path == "":
            return self._items
        if path == "folder1":
            return [BrowseItem(id="item2", title="Item 2")]
        return []

    async def search(self, query: str) -> list[BrowseItem]:
        return [i for i in self._items if query.lower() in i.title.lower()]

    async def get_stream_info(self, item_id: str) -> StreamInfo | None:
        if item_id == "item1":
            return StreamInfo(
                url="https://cdn.example.com/item1.mp3",
                content_type="audio/mpeg",
                title="Item 1",
                bitrate=128,
            )
        return None


class FailingProvider(ContentProvider):
    """A ContentProvider that raises on every method."""

    @property
    def name(self) -> str:
        return "FailingProvider"

    async def browse(self, path: str = "") -> list[BrowseItem]:
        raise RuntimeError("browse failed!")

    async def search(self, query: str) -> list[BrowseItem]:
        raise RuntimeError("search failed!")

    async def get_stream_info(self, item_id: str) -> StreamInfo | None:
        raise RuntimeError("get_stream_info failed!")


class TestContentProviderABC:
    """Tests for the ContentProvider abstract base class."""

    def test_cannot_instantiate_abc(self) -> None:
        """ContentProvider cannot be instantiated directly."""
        with pytest.raises(TypeError):
            ContentProvider()  # type: ignore[abstract]

    def test_dummy_provider_properties(self) -> None:
        """DummyProvider has name and icon."""
        p = DummyProvider("TestRadio")
        assert p.name == "TestRadio"
        assert p.icon == "https://example.com/icon.png"

    @pytest.mark.asyncio
    async def test_dummy_browse_root(self) -> None:
        """DummyProvider.browse() returns root items."""
        p = DummyProvider()
        items = await p.browse("")
        assert len(items) == 2
        assert items[0].id == "item1"
        assert items[1].type == "folder"

    @pytest.mark.asyncio
    async def test_dummy_browse_subfolder(self) -> None:
        """DummyProvider.browse() returns subfolder items."""
        p = DummyProvider()
        items = await p.browse("folder1")
        assert len(items) == 1
        assert items[0].id == "item2"

    @pytest.mark.asyncio
    async def test_dummy_search(self) -> None:
        """DummyProvider.search() filters by query."""
        p = DummyProvider()
        results = await p.search("item 1")
        assert len(results) == 1
        assert results[0].id == "item1"

    @pytest.mark.asyncio
    async def test_dummy_search_no_match(self) -> None:
        """DummyProvider.search() returns empty for no match."""
        p = DummyProvider()
        results = await p.search("nonexistent")
        assert results == []

    @pytest.mark.asyncio
    async def test_dummy_get_stream_info(self) -> None:
        """DummyProvider.get_stream_info() resolves a known item."""
        p = DummyProvider()
        info = await p.get_stream_info("item1")
        assert info is not None
        assert info.url == "https://cdn.example.com/item1.mp3"
        assert info.content_type == "audio/mpeg"
        assert info.title == "Item 1"
        assert info.bitrate == 128

    @pytest.mark.asyncio
    async def test_dummy_get_stream_info_unknown(self) -> None:
        """DummyProvider.get_stream_info() returns None for unknown item."""
        p = DummyProvider()
        info = await p.get_stream_info("nonexistent")
        assert info is None

    @pytest.mark.asyncio
    async def test_on_stream_started_default(self) -> None:
        """Default on_stream_started does not raise."""
        p = DummyProvider()
        await p.on_stream_started("item1", "aa:bb:cc:dd:ee:ff")

    @pytest.mark.asyncio
    async def test_on_stream_stopped_default(self) -> None:
        """Default on_stream_stopped does not raise."""
        p = DummyProvider()
        await p.on_stream_stopped("item1", "aa:bb:cc:dd:ee:ff")


# =============================================================================
# StreamInfo and BrowseItem dataclasses
# =============================================================================


class TestStreamInfo:
    """Tests for StreamInfo dataclass."""

    def test_defaults(self) -> None:
        """StreamInfo has sensible defaults."""
        info = StreamInfo(url="https://example.com/stream.mp3")
        assert info.url == "https://example.com/stream.mp3"
        assert info.content_type == "audio/mpeg"
        assert info.title == ""
        assert info.artist == ""
        assert info.album == ""
        assert info.artwork_url is None
        assert info.duration_ms == 0
        assert info.bitrate == 0
        assert info.is_live is False
        assert info.extra == {}

    def test_all_fields(self) -> None:
        """StreamInfo stores all fields."""
        info = StreamInfo(
            url="https://cdn.example.com/stream.aac",
            content_type="audio/aac",
            title="Jazz FM",
            artist="Jazz Network",
            album="Internet Radio",
            artwork_url="https://img.example.com/logo.png",
            duration_ms=0,
            bitrate=256,
            is_live=True,
            extra={"station_id": "s123"},
        )
        assert info.title == "Jazz FM"
        assert info.artist == "Jazz Network"
        assert info.album == "Internet Radio"
        assert info.artwork_url == "https://img.example.com/logo.png"
        assert info.bitrate == 256
        assert info.is_live is True
        assert info.extra == {"station_id": "s123"}

    def test_frozen(self) -> None:
        """StreamInfo is immutable."""
        info = StreamInfo(url="https://example.com/stream.mp3")
        with pytest.raises(AttributeError):
            info.url = "changed"  # type: ignore[misc]


class TestBrowseItem:
    """Tests for BrowseItem dataclass."""

    def test_defaults(self) -> None:
        """BrowseItem has sensible defaults."""
        item = BrowseItem(id="test", title="Test Item")
        assert item.id == "test"
        assert item.title == "Test Item"
        assert item.type == "audio"
        assert item.url is None
        assert item.icon is None
        assert item.subtitle is None
        assert item.items is None
        assert item.extra == {}

    def test_folder_type(self) -> None:
        """BrowseItem with folder type."""
        item = BrowseItem(id="genres", title="Genres", type="folder")
        assert item.type == "folder"

    def test_search_type(self) -> None:
        """BrowseItem with search type."""
        item = BrowseItem(id="search", title="Search Stations", type="search")
        assert item.type == "search"

    def test_nested_items(self) -> None:
        """BrowseItem with pre-loaded children."""
        children = [
            BrowseItem(id="sub1", title="Sub 1"),
            BrowseItem(id="sub2", title="Sub 2"),
        ]
        item = BrowseItem(id="folder", title="Folder", type="folder", items=children)
        assert item.items is not None
        assert len(item.items) == 2
        assert item.items[0].id == "sub1"

    def test_frozen(self) -> None:
        """BrowseItem is immutable."""
        item = BrowseItem(id="test", title="Test")
        with pytest.raises(AttributeError):
            item.id = "changed"  # type: ignore[misc]


# =============================================================================
# ContentProviderRegistry
# =============================================================================


class TestContentProviderRegistry:
    """Tests for ContentProviderRegistry."""

    def test_register_and_get(self) -> None:
        """Register a provider and retrieve it."""
        registry = ContentProviderRegistry()
        provider = DummyProvider("Radio")
        registry.register("radio", provider)

        assert registry.get("radio") is provider
        assert "radio" in registry
        assert len(registry) == 1

    def test_register_duplicate_raises(self) -> None:
        """Registering the same provider_id twice raises ValueError."""
        registry = ContentProviderRegistry()
        registry.register("radio", DummyProvider("Radio 1"))
        with pytest.raises(ValueError, match="already registered"):
            registry.register("radio", DummyProvider("Radio 2"))

    def test_unregister(self) -> None:
        """Unregister removes the provider."""
        registry = ContentProviderRegistry()
        registry.register("radio", DummyProvider())
        assert "radio" in registry

        registry.unregister("radio")
        assert "radio" not in registry
        assert registry.get("radio") is None

    def test_unregister_unknown_silent(self) -> None:
        """Unregistering an unknown provider_id does not raise."""
        registry = ContentProviderRegistry()
        registry.unregister("nonexistent")  # should not raise

    def test_list_providers(self) -> None:
        """list_providers() returns all registered providers."""
        registry = ContentProviderRegistry()
        p1 = DummyProvider("Radio")
        p2 = DummyProvider("Podcast")
        registry.register("radio", p1)
        registry.register("podcast", p2)

        providers = registry.list_providers()
        assert len(providers) == 2
        ids = [pid for pid, _ in providers]
        assert "radio" in ids
        assert "podcast" in ids

    def test_provider_ids(self) -> None:
        """provider_ids returns list of registered IDs."""
        registry = ContentProviderRegistry()
        registry.register("radio", DummyProvider())
        registry.register("podcast", DummyProvider())
        assert set(registry.provider_ids) == {"radio", "podcast"}

    def test_empty_registry(self) -> None:
        """Empty registry has length 0 and no providers."""
        registry = ContentProviderRegistry()
        assert len(registry) == 0
        assert registry.provider_ids == []
        assert registry.list_providers() == []

    @pytest.mark.asyncio
    async def test_browse(self) -> None:
        """browse() delegates to the correct provider."""
        registry = ContentProviderRegistry()
        registry.register("radio", DummyProvider())

        items = await registry.browse("radio", "")
        assert len(items) == 2
        assert items[0].id == "item1"

    @pytest.mark.asyncio
    async def test_browse_subfolder(self) -> None:
        """browse() with a sub-path."""
        registry = ContentProviderRegistry()
        registry.register("radio", DummyProvider())

        items = await registry.browse("radio", "folder1")
        assert len(items) == 1
        assert items[0].id == "item2"

    @pytest.mark.asyncio
    async def test_browse_unknown_provider(self) -> None:
        """browse() returns empty list for unknown provider."""
        registry = ContentProviderRegistry()
        items = await registry.browse("nonexistent", "")
        assert items == []

    @pytest.mark.asyncio
    async def test_browse_error_returns_empty(self) -> None:
        """browse() returns empty list when provider raises."""
        registry = ContentProviderRegistry()
        registry.register("failing", FailingProvider())
        items = await registry.browse("failing", "")
        assert items == []

    @pytest.mark.asyncio
    async def test_search(self) -> None:
        """search() delegates to the correct provider."""
        registry = ContentProviderRegistry()
        registry.register("radio", DummyProvider())

        results = await registry.search("radio", "Item 1")
        assert len(results) == 1
        assert results[0].id == "item1"

    @pytest.mark.asyncio
    async def test_search_unknown_provider(self) -> None:
        """search() returns empty list for unknown provider."""
        registry = ContentProviderRegistry()
        results = await registry.search("nonexistent", "test")
        assert results == []

    @pytest.mark.asyncio
    async def test_search_error_returns_empty(self) -> None:
        """search() returns empty list when provider raises."""
        registry = ContentProviderRegistry()
        registry.register("failing", FailingProvider())
        results = await registry.search("failing", "test")
        assert results == []

    @pytest.mark.asyncio
    async def test_get_stream_info(self) -> None:
        """get_stream_info() delegates to the correct provider."""
        registry = ContentProviderRegistry()
        registry.register("radio", DummyProvider())

        info = await registry.get_stream_info("radio", "item1")
        assert info is not None
        assert info.url == "https://cdn.example.com/item1.mp3"

    @pytest.mark.asyncio
    async def test_get_stream_info_not_found(self) -> None:
        """get_stream_info() returns None for unknown item."""
        registry = ContentProviderRegistry()
        registry.register("radio", DummyProvider())

        info = await registry.get_stream_info("radio", "nonexistent")
        assert info is None

    @pytest.mark.asyncio
    async def test_get_stream_info_unknown_provider(self) -> None:
        """get_stream_info() returns None for unknown provider."""
        registry = ContentProviderRegistry()
        info = await registry.get_stream_info("nonexistent", "item1")
        assert info is None

    @pytest.mark.asyncio
    async def test_get_stream_info_error_returns_none(self) -> None:
        """get_stream_info() returns None when provider raises."""
        registry = ContentProviderRegistry()
        registry.register("failing", FailingProvider())
        info = await registry.get_stream_info("failing", "item1")
        assert info is None

    @pytest.mark.asyncio
    async def test_search_all(self) -> None:
        """search_all() queries all providers."""
        registry = ContentProviderRegistry()
        registry.register("radio", DummyProvider("Radio"))
        registry.register("podcast", DummyProvider("Podcast"))

        results = await registry.search_all("Item 1")
        assert "radio" in results
        assert "podcast" in results
        assert len(results["radio"]) == 1
        assert len(results["podcast"]) == 1

    @pytest.mark.asyncio
    async def test_search_all_with_failing_provider(self) -> None:
        """search_all() skips failing providers without crashing."""
        registry = ContentProviderRegistry()
        registry.register("good", DummyProvider("Good"))
        registry.register("bad", FailingProvider())

        results = await registry.search_all("Item 1")
        assert "good" in results
        assert "bad" not in results

    @pytest.mark.asyncio
    async def test_search_all_empty_results_omitted(self) -> None:
        """search_all() omits providers with no results."""
        registry = ContentProviderRegistry()
        registry.register("radio", DummyProvider())

        results = await registry.search_all("nonexistent_query_xyz")
        assert results == {}


# =============================================================================
# PluginContext — content provider registration
# =============================================================================


class TestPluginContextContentProvider:
    """Tests for PluginContext.register_content_provider."""

    def _make_context(
        self, registry: ContentProviderRegistry | None = None
    ) -> Any:
        """Create a PluginContext with mocked dependencies."""
        from resonance.plugin import PluginContext

        # Minimal mocks for required fields
        event_bus = MagicMock()
        event_bus.unsubscribe = AsyncMock()
        music_library = MagicMock()
        player_registry = MagicMock()

        ctx = PluginContext(
            plugin_id="test_plugin",
            event_bus=event_bus,
            music_library=music_library,
            player_registry=player_registry,
            _content_registry=registry,
        )
        return ctx

    def test_register_content_provider(self) -> None:
        """register_content_provider() adds provider to registry."""
        registry = ContentProviderRegistry()
        ctx = self._make_context(registry)
        provider = DummyProvider("TestRadio")

        ctx.register_content_provider("radio", provider)

        assert "radio" in registry
        assert registry.get("radio") is provider
        assert "radio" in ctx._registered_content_providers

    def test_register_content_provider_no_registry_raises(self) -> None:
        """register_content_provider() raises when no registry available."""
        ctx = self._make_context(registry=None)
        with pytest.raises(RuntimeError, match="not available"):
            ctx.register_content_provider("radio", DummyProvider())

    def test_unregister_content_provider(self) -> None:
        """unregister_content_provider() removes provider from registry."""
        registry = ContentProviderRegistry()
        ctx = self._make_context(registry)
        ctx.register_content_provider("radio", DummyProvider())
        assert "radio" in registry

        ctx.unregister_content_provider("radio")
        assert "radio" not in registry
        assert "radio" not in ctx._registered_content_providers

    def test_unregister_content_provider_no_registry_silent(self) -> None:
        """unregister_content_provider() is silent when no registry."""
        ctx = self._make_context(registry=None)
        ctx.unregister_content_provider("radio")  # should not raise

    @pytest.mark.asyncio
    async def test_cleanup_removes_content_providers(self) -> None:
        """_cleanup() automatically unregisters all content providers."""
        registry = ContentProviderRegistry()
        ctx = self._make_context(registry)
        ctx.register_content_provider("radio", DummyProvider("Radio"))
        ctx.register_content_provider("podcast", DummyProvider("Podcast"))
        assert len(registry) == 2

        await ctx._cleanup()

        assert len(registry) == 0
        assert ctx._registered_content_providers == []

    @pytest.mark.asyncio
    async def test_cleanup_handles_already_removed(self) -> None:
        """_cleanup() handles providers that were already unregistered."""
        registry = ContentProviderRegistry()
        ctx = self._make_context(registry)
        ctx.register_content_provider("radio", DummyProvider())

        # Manually remove before cleanup
        registry.unregister("radio")

        # Cleanup should not crash
        await ctx._cleanup()
        assert ctx._registered_content_providers == []

    def test_repr_includes_content_providers(self) -> None:
        """PluginContext repr includes content_providers count."""
        registry = ContentProviderRegistry()
        ctx = self._make_context(registry)
        ctx.register_content_provider("radio", DummyProvider())

        r = repr(ctx)
        assert "content_providers=1" in r

    def test_multiple_plugins_different_providers(self) -> None:
        """Multiple PluginContexts can register different providers."""
        registry = ContentProviderRegistry()
        ctx1 = self._make_context(registry)
        ctx1.plugin_id = "plugin_a"
        ctx2 = self._make_context(registry)
        ctx2.plugin_id = "plugin_b"

        ctx1.register_content_provider("radio", DummyProvider("Radio"))
        ctx2.register_content_provider("podcast", DummyProvider("Podcast"))

        assert len(registry) == 2
        assert "radio" in registry
        assert "podcast" in registry


# =============================================================================
# Backward compatibility
# =============================================================================


class TestBackwardCompatibility:
    """Ensure existing local-only workflows are unaffected."""

    def test_playlist_track_from_path_unchanged(self) -> None:
        """from_path() still works exactly as before."""
        track = PlaylistTrack.from_path("/music/song.mp3")
        assert track.path == str(Path("/music/song.mp3"))
        assert track.title == "song"
        assert track.track_id is None
        assert track.is_remote is False

    def test_streaming_server_queue_file_unchanged(self) -> None:
        """queue_file() / resolve_file() still work for local files."""
        server = StreamingServer()
        mac = "aa:bb:cc:dd:ee:ff"
        server.queue_file(mac, Path("/music/song.mp3"))

        # resolve_file still works
        resolved = server.resolve_file(mac)
        assert resolved == Path("/music/song.mp3")

        # resolve_stream also works
        stream = server.resolve_stream(mac)
        assert stream.file_path == Path("/music/song.mp3")
        assert stream.remote is None

    def test_serialize_local_only_playlist_compact(self) -> None:
        """A playlist with only local tracks serializes without remote fields."""
        tracks = [
            PlaylistTrack(
                track_id=TrackId(1),
                path="/music/song1.mp3",
                title="Song 1",
                artist="Artist 1",
                album="Album 1",
                duration_ms=180000,
            ),
            PlaylistTrack(
                track_id=TrackId(2),
                path="/music/song2.flac",
                title="Song 2",
                artist="Artist 2",
                album="Album 2",
                duration_ms=240000,
            ),
        ]
        playlist = Playlist(player_id="aa:bb:cc:dd:ee:ff", tracks=tracks)
        data = _serialize_playlist(playlist)

        for td in data["tracks"]:
            # None of the remote-specific keys should be present
            assert "is_remote" not in td
            assert "stream_url" not in td
            assert "external_id" not in td
            assert "artwork_url" not in td
            assert "content_type" not in td
            assert "is_live" not in td
            # source might be "local" or absent, either is fine
            if "source" in td:
                assert td["source"] == "local"
            # bitrate might be 0 or absent, either is fine
            if "bitrate" in td:
                assert td["bitrate"] == 0

    def test_playlist_add_path_still_works(self) -> None:
        """Playlist.add_path() convenience method still works."""
        playlist = Playlist(player_id="test")
        idx = playlist.add_path("/music/song.mp3")
        assert idx == 0
        assert playlist.tracks[0].is_remote is False
        assert playlist.tracks[0].source == "local"

    def test_frozen_track_immutable(self) -> None:
        """PlaylistTrack is still frozen/immutable."""
        track = PlaylistTrack.from_path("/music/song.mp3")
        with pytest.raises(AttributeError):
            track.path = "/other.mp3"  # type: ignore[misc]

    def test_streaming_server_generation_consistent(self) -> None:
        """Stream generation increments consistently across local and remote."""
        server = StreamingServer()
        mac = "aa:bb:cc:dd:ee:ff"

        server.queue_file(mac, Path("/music/song.mp3"))
        gen1 = server.get_stream_generation(mac)

        server.queue_url(mac, "https://example.com/stream.mp3")
        gen2 = server.get_stream_generation(mac)

        server.queue_file(mac, Path("/music/song2.mp3"))
        gen3 = server.get_stream_generation(mac)

        assert gen1 < gen2 < gen3
