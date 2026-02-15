"""
Tests for Radio/Remote stream metadata in status responses.

Verifies that cmd_status returns LMS-compatible fields for radio streams:
- ``remote: 1`` for remote tracks
- ``current_title`` for remote streams (ICY StreamTitle → player.icy_title → static title)
- ``remote_title`` for radio stations
- ``live_edge: 0`` for live streams
- ``trackType`` reflecting the source (radio/podcast/local)
- artwork_url fallback when no album_id is present
- Correct icon/coverArt in currentTrack and playlist_loop/item_loop
- ICY metadata wiring: StreamingServer.get_icy_title() → current_title

Also tests that plugin result-level errors ({"error": "..."} inside result)
are distinguishable from successful responses.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from resonance.core.playlist import PlaylistTrack
from resonance.web.handlers import CommandContext
from resonance.web.handlers.status import cmd_status

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_radio_track(
    *,
    title: str = "Jazz FM",
    artist: str = "",
    album: str = "",
    stream_url: str = "http://stream.jazzfm.com/live.mp3",
    artwork_url: str = "http://img.jazzfm.com/logo.png",
    station_uuid: str = "jazz-uuid-1234",
    codec: str = "MP3",
    bitrate: int = 128,
) -> PlaylistTrack:
    """Create a PlaylistTrack that mimics a radio station."""
    return PlaylistTrack.from_url(
        url=stream_url,
        title=title,
        artist=artist,
        album=album,
        source="radio",
        stream_url=stream_url,
        external_id=station_uuid,
        artwork_url=artwork_url,
        content_type="audio/mpeg",
        bitrate=bitrate,
        is_live=True,
    )


def _make_podcast_track(
    *,
    title: str = "Episode 42: Testing",
    artist: str = "Tech Podcast",
    album: str = "Tech Podcast Show",
    stream_url: str = "http://feeds.example.com/ep42.mp3",
    artwork_url: str = "http://img.example.com/show.png",
    duration_ms: int = 3600_000,
) -> PlaylistTrack:
    """Create a PlaylistTrack that mimics a podcast episode."""
    return PlaylistTrack.from_url(
        url=stream_url,
        title=title,
        artist=artist,
        album=album,
        duration_ms=duration_ms,
        source="podcast",
        stream_url=stream_url,
        external_id="ep-42-guid",
        artwork_url=artwork_url,
        content_type="audio/mpeg",
        bitrate=192,
        is_live=False,
    )


def _make_local_track() -> PlaylistTrack:
    """Create a PlaylistTrack that mimics a local library track."""
    return PlaylistTrack(
        track_id=42,
        path="/music/song.flac",
        album_id=10,
        artist_id=5,
        title="Local Song",
        artist="Local Artist",
        album="Local Album",
        duration_ms=240_000,
        source="local",
        is_remote=False,
        is_live=False,
    )


def _make_player(*, state: str = "PLAYING", volume: int = 80) -> Any:
    """Create a mock player object."""
    player = SimpleNamespace(
        name="Test Player",
        mac_address="aa:bb:cc:dd:ee:ff",
        _seq_no=1,
        status=SimpleNamespace(
            state=SimpleNamespace(name=state),
            volume=volume,
            muted=False,
            elapsed_seconds=5.0,
            elapsed_milliseconds=5000,
            elapsed_report_monotonic=0.0,
            duration_seconds=0.0,
        ),
        info=SimpleNamespace(
            device_type=SimpleNamespace(name="squeezebox"),
            model="squeezebox3",
            uuid=None,
        ),
    )
    return player


def _make_playlist(track: PlaylistTrack, *, index: int = 0) -> Any:
    """Create a mock playlist containing a single track."""
    playlist = MagicMock()
    playlist.current_index = index
    playlist.current_track = track
    playlist.tracks = [track]
    playlist.shuffle_mode = SimpleNamespace(value=0)
    playlist.repeat_mode = SimpleNamespace(value=0)
    playlist.updated_at = 1700000000.0
    playlist.__len__ = MagicMock(return_value=1)
    return playlist


def _make_ctx(
    track: PlaylistTrack,
    *,
    player: Any | None = None,
    player_id: str = "aa:bb:cc:dd:ee:ff",
    icy_title: str | None = None,
) -> CommandContext:
    """Build a CommandContext with the given track in the playlist.

    Args:
        icy_title: If set, ``streaming_server.get_icy_title()`` will
            return this value — simulates ICY StreamTitle parsed from
            an upstream radio stream.
    """
    if player is None:
        player = _make_player()

    playlist = _make_playlist(track)

    player_registry = AsyncMock()
    player_registry.get_by_mac = AsyncMock(return_value=player)

    playlist_manager = MagicMock()
    playlist_manager.get = MagicMock(return_value=playlist)

    streaming_server = MagicMock()
    streaming_server.get_stream_generation = MagicMock(return_value=1)
    streaming_server.get_start_offset = MagicMock(return_value=0.0)
    streaming_server.get_icy_title = MagicMock(return_value=icy_title)

    ctx = CommandContext(
        player_id=player_id,
        music_library=MagicMock(),
        player_registry=player_registry,
        playlist_manager=playlist_manager,
        streaming_server=streaming_server,
        artwork_manager=None,
        server_host="192.168.1.100",
        server_port=9000,
    )
    return ctx


# =============================================================================
# Remote flag and current_title
# =============================================================================


class TestRadioStatusMetadata:
    """Verify LMS-compatible metadata fields for radio streams in status."""

    @pytest.mark.asyncio
    async def test_remote_flag_set_for_radio(self) -> None:
        """Status must include ``remote: 1`` when current track is remote."""
        track = _make_radio_track()
        ctx = _make_ctx(track)

        result = await cmd_status(ctx, ["status", "-", 1])

        assert result.get("remote") == 1

    @pytest.mark.asyncio
    async def test_remote_flag_absent_for_local(self) -> None:
        """Status must NOT include ``remote`` for local tracks."""
        track = _make_local_track()
        ctx = _make_ctx(track)

        result = await cmd_status(ctx, ["status", "-", 1])

        assert "remote" not in result

    @pytest.mark.asyncio
    async def test_current_title_for_radio(self) -> None:
        """Status must include ``current_title`` for remote streams."""
        track = _make_radio_track(title="Jazz FM")
        ctx = _make_ctx(track)

        result = await cmd_status(ctx, ["status", "-", 1])

        assert result.get("current_title") == "Jazz FM"

    @pytest.mark.asyncio
    async def test_current_title_absent_for_local(self) -> None:
        """Local tracks should not have ``current_title`` in status."""
        track = _make_local_track()
        ctx = _make_ctx(track)

        result = await cmd_status(ctx, ["status", "-", 1])

        assert "current_title" not in result

    @pytest.mark.asyncio
    async def test_remote_title_for_radio(self) -> None:
        """Radio streams must expose ``remote_title`` (station name)."""
        track = _make_radio_track(title="BBC Radio 1")
        ctx = _make_ctx(track)

        result = await cmd_status(ctx, ["status", "-", 1])

        assert result.get("remote_title") == "BBC Radio 1"

    @pytest.mark.asyncio
    async def test_remote_title_absent_for_podcast(self) -> None:
        """Podcast streams are remote but should NOT have ``remote_title``."""
        track = _make_podcast_track()
        ctx = _make_ctx(track)

        result = await cmd_status(ctx, ["status", "-", 1])

        # Podcast is remote, so remote:1 should be set...
        assert result.get("remote") == 1
        # ...but remote_title is radio-specific
        assert "remote_title" not in result


# =============================================================================
# live_edge
# =============================================================================


class TestLiveEdge:
    """Verify ``live_edge`` field for live vs non-live streams."""

    @pytest.mark.asyncio
    async def test_live_edge_zero_for_live_stream(self) -> None:
        """Live radio streams must report ``live_edge: 0`` (at live edge)."""
        track = _make_radio_track()
        assert track.is_live is True
        ctx = _make_ctx(track)

        result = await cmd_status(ctx, ["status", "-", 1])

        assert result.get("live_edge") == 0

    @pytest.mark.asyncio
    async def test_live_edge_absent_for_non_live(self) -> None:
        """Podcast episodes (not live) should not report ``live_edge``."""
        track = _make_podcast_track()
        assert track.is_live is False
        ctx = _make_ctx(track)

        result = await cmd_status(ctx, ["status", "-", 1])

        assert "live_edge" not in result

    @pytest.mark.asyncio
    async def test_live_edge_absent_for_local(self) -> None:
        """Local library tracks should not report ``live_edge``."""
        track = _make_local_track()
        ctx = _make_ctx(track)

        result = await cmd_status(ctx, ["status", "-", 1])

        assert "live_edge" not in result


# =============================================================================
# currentTrack fields for remote streams
# =============================================================================


class TestCurrentTrackRemoteFields:
    """Verify ``currentTrack`` carries remote-specific fields."""

    @pytest.mark.asyncio
    async def test_current_track_has_remote_flag(self) -> None:
        track = _make_radio_track()
        ctx = _make_ctx(track)

        result = await cmd_status(ctx, ["status", "-", 1])
        ct = result.get("currentTrack", {})

        assert ct.get("remote") == 1

    @pytest.mark.asyncio
    async def test_current_track_source_radio(self) -> None:
        track = _make_radio_track()
        ctx = _make_ctx(track)

        result = await cmd_status(ctx, ["status", "-", 1])
        ct = result.get("currentTrack", {})

        assert ct.get("source") == "radio"

    @pytest.mark.asyncio
    async def test_current_track_source_podcast(self) -> None:
        track = _make_podcast_track()
        ctx = _make_ctx(track)

        result = await cmd_status(ctx, ["status", "-", 1])
        ct = result.get("currentTrack", {})

        assert ct.get("source") == "podcast"

    @pytest.mark.asyncio
    async def test_current_track_no_remote_for_local(self) -> None:
        track = _make_local_track()
        ctx = _make_ctx(track)

        result = await cmd_status(ctx, ["status", "-", 1])
        ct = result.get("currentTrack", {})

        assert "remote" not in ct
        assert "source" not in ct

    @pytest.mark.asyncio
    async def test_current_track_is_live(self) -> None:
        track = _make_radio_track()
        ctx = _make_ctx(track)

        result = await cmd_status(ctx, ["status", "-", 1])
        ct = result.get("currentTrack", {})

        assert ct.get("is_live") is True

    @pytest.mark.asyncio
    async def test_current_track_content_type(self) -> None:
        track = _make_radio_track()
        ctx = _make_ctx(track)

        result = await cmd_status(ctx, ["status", "-", 1])
        ct = result.get("currentTrack", {})

        assert ct.get("content_type") == "audio/mpeg"

    @pytest.mark.asyncio
    async def test_current_track_bitrate(self) -> None:
        track = _make_radio_track(bitrate=320)
        ctx = _make_ctx(track)

        result = await cmd_status(ctx, ["status", "-", 1])
        ct = result.get("currentTrack", {})

        assert ct.get("bitrate") == 320


# =============================================================================
# Artwork fallback for remote tracks
# =============================================================================


class TestArtworkFallback:
    """Verify cover art falls back to artwork_url when no album_id."""

    @pytest.mark.asyncio
    async def test_radio_artwork_url_in_cover_art(self) -> None:
        """Radio tracks without album_id should use artwork_url."""
        track = _make_radio_track(artwork_url="http://img.radio.com/logo.png")
        assert track.album_id is None
        ctx = _make_ctx(track)

        result = await cmd_status(ctx, ["status", "-", 1])
        ct = result.get("currentTrack", {})

        assert ct["coverArt"] == "http://img.radio.com/logo.png"

    @pytest.mark.asyncio
    async def test_radio_icon_in_current_track(self) -> None:
        """Radio tracks should expose artwork_url as ``icon``."""
        track = _make_radio_track(artwork_url="http://img.radio.com/logo.png")
        ctx = _make_ctx(track)

        result = await cmd_status(ctx, ["status", "-", 1])
        ct = result.get("currentTrack", {})

        assert ct.get("icon") == "http://img.radio.com/logo.png"
        # icon-id requires album_id, so it should NOT be set
        assert "icon-id" not in ct

    @pytest.mark.asyncio
    async def test_local_track_uses_album_id_artwork(self) -> None:
        """Local tracks with album_id should use server-generated artwork URL."""
        track = _make_local_track()
        assert track.album_id == 10
        ctx = _make_ctx(track)

        result = await cmd_status(ctx, ["status", "-", 1])
        ct = result.get("currentTrack", {})

        assert "/artwork/10" in ct["coverArt"]
        assert ct.get("icon-id") == "/music/10/cover"

    @pytest.mark.asyncio
    async def test_radio_no_artwork_url_empty_cover(self) -> None:
        """Radio track with no artwork_url gets empty coverArt."""
        track = _make_radio_track(artwork_url="")
        # from_url sets artwork_url to None when empty string isn't provided;
        # force it for this test
        track = PlaylistTrack.from_url(
            url="http://stream.example.com/live.mp3",
            title="No Logo Station",
            source="radio",
            is_live=True,
        )
        assert track.artwork_url is None
        ctx = _make_ctx(track)

        result = await cmd_status(ctx, ["status", "-", 1])
        ct = result.get("currentTrack", {})

        assert ct["coverArt"] == ""
        assert "icon" not in ct
        assert "icon-id" not in ct


# =============================================================================
# trackType in Jive menu mode (item_loop)
# =============================================================================


class TestTrackTypeInMenuMode:
    """Verify ``trackType`` is correctly set based on source in menu mode."""

    @pytest.mark.asyncio
    async def test_radio_track_type_in_menu_mode(self) -> None:
        """Radio tracks should have ``trackType: "radio"`` in item_loop."""
        track = _make_radio_track()
        ctx = _make_ctx(track)

        result = await cmd_status(ctx, ["status", "-", 1, "menu:menu"])

        items = result.get("item_loop", [])
        assert len(items) >= 1
        # The first item is the track (last may be "Clear Playlist")
        track_item = items[0]
        assert track_item.get("trackType") == "radio"

    @pytest.mark.asyncio
    async def test_podcast_track_type_in_menu_mode(self) -> None:
        """Podcast tracks should have ``trackType: "podcast"`` in item_loop."""
        track = _make_podcast_track()
        ctx = _make_ctx(track)

        result = await cmd_status(ctx, ["status", "-", 1, "menu:menu"])

        items = result.get("item_loop", [])
        assert len(items) >= 1
        track_item = items[0]
        assert track_item.get("trackType") == "podcast"

    @pytest.mark.asyncio
    async def test_local_track_type_in_menu_mode(self) -> None:
        """Local tracks should have ``trackType: "local"`` in item_loop."""
        track = _make_local_track()
        ctx = _make_ctx(track)

        result = await cmd_status(ctx, ["status", "-", 1, "menu:menu"])

        items = result.get("item_loop", [])
        assert len(items) >= 1
        track_item = items[0]
        assert track_item.get("trackType") == "local"


# =============================================================================
# playlist_loop remote flags
# =============================================================================


class TestPlaylistLoopRemoteFields:
    """Verify remote flags in standard playlist_loop (non-menu mode)."""

    @pytest.mark.asyncio
    async def test_radio_track_in_playlist_loop(self) -> None:
        """Radio tracks in playlist_loop must have remote:1 and trackType."""
        track = _make_radio_track(artwork_url="http://img.radio.com/logo.png")
        ctx = _make_ctx(track)

        result = await cmd_status(ctx, ["status", "-", 1])

        loop = result.get("playlist_loop", [])
        assert len(loop) >= 1
        item = loop[0]
        assert item.get("remote") == 1
        assert item.get("trackType") == "radio"

    @pytest.mark.asyncio
    async def test_radio_artwork_in_playlist_loop(self) -> None:
        """Radio tracks in playlist_loop use artwork_url for coverArt/icon."""
        track = _make_radio_track(artwork_url="http://img.radio.com/logo.png")
        ctx = _make_ctx(track)

        result = await cmd_status(ctx, ["status", "-", 1])

        loop = result.get("playlist_loop", [])
        assert len(loop) >= 1
        item = loop[0]
        assert item["coverArt"] == "http://img.radio.com/logo.png"
        assert item.get("icon") == "http://img.radio.com/logo.png"

    @pytest.mark.asyncio
    async def test_local_track_no_remote_in_playlist_loop(self) -> None:
        """Local tracks should not have remote flag in playlist_loop."""
        track = _make_local_track()
        ctx = _make_ctx(track)

        result = await cmd_status(ctx, ["status", "-", 1])

        loop = result.get("playlist_loop", [])
        assert len(loop) >= 1
        item = loop[0]
        assert "remote" not in item
        assert "trackType" not in item  # local tracks omit trackType

    @pytest.mark.asyncio
    async def test_podcast_track_in_playlist_loop(self) -> None:
        """Podcast tracks in playlist_loop should have trackType podcast."""
        track = _make_podcast_track()
        ctx = _make_ctx(track)

        result = await cmd_status(ctx, ["status", "-", 1])

        loop = result.get("playlist_loop", [])
        assert len(loop) >= 1
        item = loop[0]
        assert item.get("remote") == 1
        assert item.get("trackType") == "podcast"


# =============================================================================
# Jive menu mode artwork for radio
# =============================================================================


class TestMenuModeArtwork:
    """Verify icon fields in item_loop (Jive menu mode) for radio."""

    @pytest.mark.asyncio
    async def test_radio_icon_in_menu_mode(self) -> None:
        """Radio station favicon should appear as ``icon`` in item_loop."""
        track = _make_radio_track(artwork_url="http://img.station.com/favicon.png")
        ctx = _make_ctx(track)

        result = await cmd_status(ctx, ["status", "-", 1, "menu:menu"])

        items = result.get("item_loop", [])
        assert len(items) >= 1
        track_item = items[0]
        assert track_item.get("icon") == "http://img.station.com/favicon.png"
        # icon-id requires album_id which radio tracks don't have
        assert "icon-id" not in track_item

    @pytest.mark.asyncio
    async def test_local_icon_id_in_menu_mode(self) -> None:
        """Local tracks with album_id should use icon-id in item_loop."""
        track = _make_local_track()
        ctx = _make_ctx(track)

        result = await cmd_status(ctx, ["status", "-", 1, "menu:menu"])

        items = result.get("item_loop", [])
        assert len(items) >= 1
        track_item = items[0]
        assert track_item.get("icon-id") == 10  # album_id


# =============================================================================
# Plugin result-level error detection
# =============================================================================


class TestPluginResultError:
    """Test that plugin commands returning {"error": "..."} are detectable.

    This validates the pattern used by radio/podcast/favorites plugins
    where error conditions are returned inside the result body rather
    than as JSON-RPC transport errors.
    """

    @pytest.mark.asyncio
    async def test_radio_play_missing_params_returns_error(self) -> None:
        """``radio play`` without url/id must return result with ``error`` key."""
        import plugins.radio as radio_mod

        radio_mod._radio_browser = AsyncMock()

        ctx = MagicMock()
        ctx.player_id = "aa:bb:cc:dd:ee:ff"
        ctx.player_registry = AsyncMock()

        result = await radio_mod._radio_play(ctx, ["radio", "play"])

        assert isinstance(result, dict)
        assert "error" in result
        assert isinstance(result["error"], str)
        assert len(result["error"]) > 0

        radio_mod._radio_browser = None

    @pytest.mark.asyncio
    async def test_radio_play_station_not_found_returns_error(self) -> None:
        """``radio play`` with unknown UUID must return result error."""
        import plugins.radio as radio_mod

        mock_client = AsyncMock()
        mock_client.get_station_by_uuid = AsyncMock(return_value=None)
        radio_mod._radio_browser = mock_client

        ctx = MagicMock()
        ctx.player_id = "aa:bb:cc:dd:ee:ff"
        ctx.player_registry = AsyncMock()

        result = await radio_mod._radio_play(
            ctx, ["radio", "play", "id:nonexistent-uuid"]
        )

        assert "error" in result
        assert "not found" in result["error"].lower()

        radio_mod._radio_browser = None

    @pytest.mark.asyncio
    async def test_radio_dispatch_not_initialized_returns_error(self) -> None:
        """If radio plugin is not initialized, dispatch returns error."""
        import plugins.radio as radio_mod

        saved = radio_mod._radio_browser
        radio_mod._radio_browser = None

        ctx = MagicMock()
        result = await radio_mod.cmd_radio(ctx, ["radio", "items"])

        assert "error" in result

        radio_mod._radio_browser = saved

    @pytest.mark.asyncio
    async def test_radio_play_no_player_returns_error(self) -> None:
        """``radio play`` with no player selected returns error."""
        import plugins.radio as radio_mod

        radio_mod._radio_browser = AsyncMock()

        ctx = MagicMock()
        ctx.player_id = "-"
        ctx.player_registry = AsyncMock()
        ctx.player_registry.get_by_mac = AsyncMock(return_value=None)
        ctx.playlist_manager = MagicMock()
        ctx.playlist_manager.get = MagicMock(return_value=MagicMock())

        result = await radio_mod._radio_play(
            ctx,
            ["radio", "play", "url:http://stream.example.com/live.mp3", "title:Test"],
        )

        assert "error" in result

        radio_mod._radio_browser = None


# =============================================================================
# PlaylistTrack invariants for radio
# =============================================================================


class TestPlaylistTrackRadioFields:
    """Verify that PlaylistTrack.from_url() sets remote fields correctly."""

    def test_from_url_radio_fields(self) -> None:
        track = _make_radio_track(
            title="Test FM",
            stream_url="http://stream.test.fm/live.mp3",
            artwork_url="http://img.test.fm/logo.png",
            station_uuid="test-uuid",
            bitrate=256,
        )

        assert track.is_remote is True
        assert track.is_live is True
        assert track.source == "radio"
        assert track.title == "Test FM"
        assert track.stream_url == "http://stream.test.fm/live.mp3"
        assert track.artwork_url == "http://img.test.fm/logo.png"
        assert track.external_id == "test-uuid"
        assert track.content_type == "audio/mpeg"
        assert track.bitrate == 256
        assert track.duration_ms == 0
        assert track.album_id is None
        assert track.track_id is None

    def test_from_url_podcast_fields(self) -> None:
        track = _make_podcast_track(duration_ms=1800_000)

        assert track.is_remote is True
        assert track.is_live is False
        assert track.source == "podcast"
        assert track.duration_ms == 1800_000

    def test_effective_stream_url_radio(self) -> None:
        track = _make_radio_track(stream_url="http://resolved.stream.com/live.mp3")

        assert track.effective_stream_url == "http://resolved.stream.com/live.mp3"

    def test_effective_stream_url_local(self) -> None:
        track = _make_local_track()

        assert track.effective_stream_url == "/music/song.flac"


# =============================================================================
# Duration behaviour for live vs finite remote streams
# =============================================================================


class TestDurationForRemoteStreams:
    """Verify duration handling in status for live vs finite remote streams."""

    @pytest.mark.asyncio
    async def test_live_stream_duration_zero(self) -> None:
        """Live radio should report duration 0."""
        track = _make_radio_track()
        ctx = _make_ctx(track)

        result = await cmd_status(ctx, ["status", "-", 1])

        assert result["duration"] == 0.0

    @pytest.mark.asyncio
    async def test_podcast_has_duration(self) -> None:
        """Podcast episode should report its actual duration."""
        track = _make_podcast_track(duration_ms=3600_000)
        player = _make_player()
        player.status.duration_seconds = 3600.0
        ctx = _make_ctx(track, player=player)

        result = await cmd_status(ctx, ["status", "-", 1])

        # Duration comes from the current track's duration_ms
        assert result["duration"] == 3600.0


# =============================================================================
# Combined integration-style test
# =============================================================================


class TestRadioStatusIntegration:
    """Full status response check for a radio station."""

    @pytest.mark.asyncio
    async def test_full_radio_status_response(self) -> None:
        """Verify all radio-specific fields are present and consistent."""
        track = _make_radio_track(
            title="BBC Radio 1",
            artwork_url="http://bbc.co.uk/radio1/logo.png",
            bitrate=128,
        )
        ctx = _make_ctx(track)

        result = await cmd_status(ctx, ["status", "-", 1])

        # Top-level remote fields
        assert result["remote"] == 1
        assert result["current_title"] == "BBC Radio 1"
        assert result["remote_title"] == "BBC Radio 1"
        assert result["live_edge"] == 0
        assert result["duration"] == 0.0

        # currentTrack
        ct = result["currentTrack"]
        assert ct["title"] == "BBC Radio 1"
        assert ct["coverArt"] == "http://bbc.co.uk/radio1/logo.png"
        assert ct["icon"] == "http://bbc.co.uk/radio1/logo.png"
        assert ct["remote"] == 1
        assert ct["source"] == "radio"
        assert ct["is_live"] is True
        assert ct["content_type"] == "audio/mpeg"
        assert ct["bitrate"] == 128

        # playlist_loop
        loop = result.get("playlist_loop", [])
        assert len(loop) == 1
        item = loop[0]
        assert item["remote"] == 1
        assert item["trackType"] == "radio"
        assert item["coverArt"] == "http://bbc.co.uk/radio1/logo.png"
        assert item["icon"] == "http://bbc.co.uk/radio1/logo.png"
        assert item["title"] == "BBC Radio 1"

    @pytest.mark.asyncio
    async def test_full_local_status_no_remote_fields(self) -> None:
        """Local track status must NOT contain any remote-specific fields."""
        track = _make_local_track()
        ctx = _make_ctx(track)

        result = await cmd_status(ctx, ["status", "-", 1])

        assert "remote" not in result
        assert "current_title" not in result
        assert "remote_title" not in result
        assert "live_edge" not in result

        ct = result.get("currentTrack", {})
        assert "remote" not in ct
        assert "source" not in ct
        assert "is_live" not in ct


# =============================================================================
# ICY metadata wiring (StreamTitle → current_title)
# =============================================================================


class TestIcyMetadataInStatus:
    """Verify that ICY StreamTitle is wired into ``current_title``.

    Priority order (matching LMS ``getCurrentTitle()``):
    1. ``StreamingServer.get_icy_title()`` — parsed from proxied upstream
    2. ``player.icy_title`` — from Slimproto META messages
    3. Static track title (station name for radio)
    """

    @pytest.mark.asyncio
    async def test_icy_title_from_streaming_server(self) -> None:
        """ICY title from streaming proxy takes highest priority."""
        track = _make_radio_track(title="Jazz FM")
        ctx = _make_ctx(track, icy_title="Miles Davis - So What")

        result = await cmd_status(ctx, ["status", "-", 1])

        assert result["current_title"] == "Miles Davis - So What"
        # remote_title is always the station name
        assert result["remote_title"] == "Jazz FM"

    @pytest.mark.asyncio
    async def test_icy_title_in_current_track(self) -> None:
        """ICY title should also appear in ``currentTrack.current_title``."""
        track = _make_radio_track(title="Jazz FM")
        ctx = _make_ctx(track, icy_title="Coltrane - A Love Supreme")

        result = await cmd_status(ctx, ["status", "-", 1])
        ct = result.get("currentTrack", {})

        assert ct.get("current_title") == "Coltrane - A Love Supreme"
        # Static station name remains in "title"
        assert ct["title"] == "Jazz FM"

    @pytest.mark.asyncio
    async def test_player_icy_title_fallback(self) -> None:
        """If streaming server has no ICY, fall back to player.icy_title."""
        track = _make_radio_track(title="Rock FM")
        player = _make_player()
        player.icy_title = "AC/DC - Thunderstruck"

        ctx = _make_ctx(track, player=player, icy_title=None)

        result = await cmd_status(ctx, ["status", "-", 1])

        assert result["current_title"] == "AC/DC - Thunderstruck"

    @pytest.mark.asyncio
    async def test_static_title_fallback(self) -> None:
        """If no ICY metadata at all, fall back to static track title."""
        track = _make_radio_track(title="Classical FM")
        player = _make_player()
        player.icy_title = None

        ctx = _make_ctx(track, player=player, icy_title=None)

        result = await cmd_status(ctx, ["status", "-", 1])

        assert result["current_title"] == "Classical FM"

    @pytest.mark.asyncio
    async def test_empty_icy_title_falls_back(self) -> None:
        """Empty string ICY title should fall through to next priority."""
        track = _make_radio_track(title="Pop FM")
        player = _make_player()
        player.icy_title = ""

        ctx = _make_ctx(track, player=player, icy_title="")

        result = await cmd_status(ctx, ["status", "-", 1])

        # Empty strings are falsy → falls back to static title
        assert result["current_title"] == "Pop FM"

    @pytest.mark.asyncio
    async def test_icy_title_not_set_for_local_tracks(self) -> None:
        """Local tracks should never have current_title, even if ICY is set."""
        track = _make_local_track()
        # Even if the streaming server somehow has a leftover ICY title,
        # local tracks are not remote → no current_title.
        ctx = _make_ctx(track, icy_title="Stale Radio Title")

        result = await cmd_status(ctx, ["status", "-", 1])

        assert "current_title" not in result

    @pytest.mark.asyncio
    async def test_icy_title_for_podcast_uses_static(self) -> None:
        """Podcasts are remote but unlikely to have ICY; should use title."""
        track = _make_podcast_track(title="Episode 42: Testing")
        ctx = _make_ctx(track, icy_title=None)

        result = await cmd_status(ctx, ["status", "-", 1])

        assert result["current_title"] == "Episode 42: Testing"
        # No remote_title for podcasts
        assert "remote_title" not in result

    @pytest.mark.asyncio
    async def test_icy_title_overrides_for_podcast_too(self) -> None:
        """If a podcast stream somehow sends ICY, it should still be used."""
        track = _make_podcast_track(title="Episode 42: Testing")
        ctx = _make_ctx(track, icy_title="Ad: Buy Our Product")

        result = await cmd_status(ctx, ["status", "-", 1])

        assert result["current_title"] == "Ad: Buy Our Product"

    @pytest.mark.asyncio
    async def test_streaming_server_icy_beats_player_icy(self) -> None:
        """Streaming proxy ICY takes priority over player META ICY."""
        track = _make_radio_track(title="Jazz FM")
        player = _make_player()
        player.icy_title = "Old Title From META"

        ctx = _make_ctx(track, player=player, icy_title="New Title From Proxy")

        result = await cmd_status(ctx, ["status", "-", 1])

        assert result["current_title"] == "New Title From Proxy"


# =============================================================================
# ICY metadata storage on StreamingServer
# =============================================================================


class TestStreamingServerIcyStorage:
    """Verify StreamingServer ICY title get/set/clear methods."""

    def test_set_and_get_icy_title(self) -> None:
        from resonance.streaming.server import StreamingServer

        ss = StreamingServer()
        ss.set_icy_title("aa:bb:cc:dd:ee:ff", "Artist - Song")

        assert ss.get_icy_title("aa:bb:cc:dd:ee:ff") == "Artist - Song"

    def test_get_icy_title_missing_returns_none(self) -> None:
        from resonance.streaming.server import StreamingServer

        ss = StreamingServer()

        assert ss.get_icy_title("aa:bb:cc:dd:ee:ff") is None

    def test_clear_icy_title(self) -> None:
        from resonance.streaming.server import StreamingServer

        ss = StreamingServer()
        ss.set_icy_title("aa:bb:cc:dd:ee:ff", "Artist - Song")
        ss.clear_icy_title("aa:bb:cc:dd:ee:ff")

        assert ss.get_icy_title("aa:bb:cc:dd:ee:ff") is None

    def test_clear_icy_title_missing_is_noop(self) -> None:
        from resonance.streaming.server import StreamingServer

        ss = StreamingServer()
        # Should not raise
        ss.clear_icy_title("nonexistent:mac")

    def test_icy_title_per_player_isolation(self) -> None:
        from resonance.streaming.server import StreamingServer

        ss = StreamingServer()
        ss.set_icy_title("aa:bb:cc:dd:ee:01", "Station A - Song A")
        ss.set_icy_title("aa:bb:cc:dd:ee:02", "Station B - Song B")

        assert ss.get_icy_title("aa:bb:cc:dd:ee:01") == "Station A - Song A"
        assert ss.get_icy_title("aa:bb:cc:dd:ee:02") == "Station B - Song B"

    def test_set_icy_title_overwrites(self) -> None:
        from resonance.streaming.server import StreamingServer

        ss = StreamingServer()
        ss.set_icy_title("aa:bb:cc:dd:ee:ff", "First Song")
        ss.set_icy_title("aa:bb:cc:dd:ee:ff", "Second Song")

        assert ss.get_icy_title("aa:bb:cc:dd:ee:ff") == "Second Song"


# =============================================================================
# ICY metadata parsing in streaming route
# =============================================================================


class TestLogIcyMetadata:
    """Verify _log_icy_metadata() parses StreamTitle and stores it."""

    def test_parses_stream_title(self) -> None:
        from resonance.web.routes import streaming as streaming_mod

        mock_server = MagicMock()
        saved = streaming_mod._streaming_server
        streaming_mod._streaming_server = mock_server

        try:
            meta = b"StreamTitle='Artist - Song Title';\x00\x00\x00"
            streaming_mod._log_icy_metadata(meta, "aa:bb:cc:dd:ee:ff")

            mock_server.set_icy_title.assert_called_once_with(
                "aa:bb:cc:dd:ee:ff", "Artist - Song Title"
            )
        finally:
            streaming_mod._streaming_server = saved

    def test_empty_stream_title_not_stored(self) -> None:
        from resonance.web.routes import streaming as streaming_mod

        mock_server = MagicMock()
        saved = streaming_mod._streaming_server
        streaming_mod._streaming_server = mock_server

        try:
            meta = b"StreamTitle='';\x00\x00\x00"
            streaming_mod._log_icy_metadata(meta, "aa:bb:cc:dd:ee:ff")

            mock_server.set_icy_title.assert_not_called()
        finally:
            streaming_mod._streaming_server = saved

    def test_no_stream_title_in_metadata(self) -> None:
        from resonance.web.routes import streaming as streaming_mod

        mock_server = MagicMock()
        saved = streaming_mod._streaming_server
        streaming_mod._streaming_server = mock_server

        try:
            meta = b"StreamUrl='http://example.com';\x00"
            streaming_mod._log_icy_metadata(meta, "aa:bb:cc:dd:ee:ff")

            mock_server.set_icy_title.assert_not_called()
        finally:
            streaming_mod._streaming_server = saved

    def test_all_null_bytes_ignored(self) -> None:
        from resonance.web.routes import streaming as streaming_mod

        mock_server = MagicMock()
        saved = streaming_mod._streaming_server
        streaming_mod._streaming_server = mock_server

        try:
            meta = b"\x00\x00\x00\x00\x00\x00\x00\x00"
            streaming_mod._log_icy_metadata(meta, "aa:bb:cc:dd:ee:ff")

            mock_server.set_icy_title.assert_not_called()
        finally:
            streaming_mod._streaming_server = saved

    def test_no_streaming_server_does_not_crash(self) -> None:
        from resonance.web.routes import streaming as streaming_mod

        saved = streaming_mod._streaming_server
        streaming_mod._streaming_server = None

        try:
            meta = b"StreamTitle='Artist - Song';\x00"
            # Should not raise
            streaming_mod._log_icy_metadata(meta, "aa:bb:cc:dd:ee:ff")
        finally:
            streaming_mod._streaming_server = saved

    def test_unicode_stream_title(self) -> None:
        from resonance.web.routes import streaming as streaming_mod

        mock_server = MagicMock()
        saved = streaming_mod._streaming_server
        streaming_mod._streaming_server = mock_server

        try:
            meta = "StreamTitle='Ärzte - Schrei nach Liebe';".encode("utf-8") + b"\x00"
            streaming_mod._log_icy_metadata(meta, "aa:bb:cc:dd:ee:ff")

            mock_server.set_icy_title.assert_called_once_with(
                "aa:bb:cc:dd:ee:ff", "Ärzte - Schrei nach Liebe"
            )
        finally:
            streaming_mod._streaming_server = saved
