"""
Tests for remoteMeta in status responses and ICY title parsing.

Verifies that cmd_status returns an LMS-compatible ``remoteMeta`` dict
for remote tracks (LMS Queries.pm L4357-4361) and that the
``_parse_icy_title()`` helper correctly splits ICY StreamTitle strings
into ``(artist, title)`` tuples following LMS HTTP.pm L1085-1092.

Also tests the new ``icy_artist`` / ``icy_title`` fields exposed in
``currentTrack`` for the Web-UI.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from resonance.core.playlist import PlaylistTrack
from resonance.web.handlers import CommandContext
from resonance.web.handlers.status import _parse_icy_title, cmd_status

# ---------------------------------------------------------------------------
# Helpers (mirrors test_radio_status_metadata.py)
# ---------------------------------------------------------------------------


def _make_radio_track(
    *,
    title: str = "Jazz FM",
    artist: str = "",
    album: str = "",
    stream_url: str = "http://stream.jazzfm.com/live.mp3",
    artwork_url: str = "http://img.jazzfm.com/logo.png",
    station_uuid: str = "jazz-uuid-1234",
    bitrate: int = 128,
    content_type: str = "audio/mpeg",
    is_live: bool = True,
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
        content_type=content_type,
        bitrate=bitrate,
        is_live=is_live,
    )


def _make_podcast_track(
    *,
    title: str = "Episode 42: Testing",
    artist: str = "Tech Podcast",
    album: str = "Tech Podcast Show",
    stream_url: str = "http://feeds.example.com/ep42.mp3",
    artwork_url: str = "http://img.example.com/show.png",
    duration_ms: int = 3600_000,
    bitrate: int = 192,
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
        bitrate=bitrate,
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
    return SimpleNamespace(
        name="Test Player",
        mac_address="aa:bb:cc:dd:ee:ff",
        _seq_no=1,
        icy_title=None,
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
    """Build a CommandContext wired for the given track."""
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

    return CommandContext(
        player_id=player_id,
        music_library=MagicMock(),
        player_registry=player_registry,
        playlist_manager=playlist_manager,
        streaming_server=streaming_server,
        artwork_manager=None,
        server_host="192.168.1.100",
        server_port=9000,
    )


# =============================================================================
# _parse_icy_title (LMS HTTP.pm L1085-1092)
# =============================================================================


class TestParseIcyTitle:
    """Unit tests for ``_parse_icy_title()`` — LMS Artist-Title splitting."""

    def test_single_dash_splits_artist_title(self) -> None:
        """Exactly one ' - ' → (artist, title)."""
        artist, title = _parse_icy_title("Miles Davis - So What")
        assert artist == "Miles Davis"
        assert title == "So What"

    def test_no_dash_returns_empty_artist(self) -> None:
        """No ' - ' separator → entire string is title, artist is empty."""
        artist, title = _parse_icy_title("Jazz FM News Update")
        assert artist == ""
        assert title == "Jazz FM News Update"

    def test_multiple_dashes_returns_empty_artist(self) -> None:
        """Multiple ' - ' → cannot split unambiguously, title = full string."""
        artist, title = _parse_icy_title("A - B - C")
        assert artist == ""
        assert title == "A - B - C"

    def test_empty_string(self) -> None:
        """Empty ICY title → both empty."""
        artist, title = _parse_icy_title("")
        assert artist == ""
        assert title == ""

    def test_none_coerced(self) -> None:
        """None is not a valid input (caller checks), but empty behaves."""
        artist, title = _parse_icy_title("")
        assert artist == ""
        assert title == ""

    def test_whitespace_stripped(self) -> None:
        """Whitespace around artist and title is trimmed."""
        artist, title = _parse_icy_title("  Coltrane  -  My Favorite Things  ")
        assert artist == "Coltrane"
        assert title == "My Favorite Things"

    def test_unicode_characters(self) -> None:
        """Unicode in station/artist names."""
        artist, title = _parse_icy_title("Ärzte - Schrei nach Liebe")
        assert artist == "Ärzte"
        assert title == "Schrei nach Liebe"

    def test_dash_without_spaces_not_split(self) -> None:
        """Hyphens without surrounding spaces are NOT separators (LMS uses ' - ')."""
        # "AC-DC" has a dash but no surrounding spaces → split(" - ") yields 1 part
        artist, title = _parse_icy_title("AC-DC Greatest Hits")
        assert artist == ""
        assert title == "AC-DC Greatest Hits"

    def test_only_dash(self) -> None:
        """Edge case: just the separator itself."""
        artist, title = _parse_icy_title(" - ")
        assert artist == ""
        assert title == ""


# =============================================================================
# remoteMeta dict in status response
# =============================================================================


class TestRemoteMetaPresence:
    """Verify that ``remoteMeta`` is present/absent as expected."""

    @pytest.mark.asyncio
    async def test_remote_meta_present_for_radio(self) -> None:
        """Radio track → status must contain a ``remoteMeta`` dict."""
        track = _make_radio_track()
        ctx = _make_ctx(track)
        result = await cmd_status(ctx, ["status", "-", 1])
        assert "remoteMeta" in result
        assert isinstance(result["remoteMeta"], dict)

    @pytest.mark.asyncio
    async def test_remote_meta_present_for_podcast(self) -> None:
        """Podcast track is remote → remoteMeta must be present."""
        track = _make_podcast_track()
        ctx = _make_ctx(track)
        result = await cmd_status(ctx, ["status", "-", 1])
        assert "remoteMeta" in result

    @pytest.mark.asyncio
    async def test_remote_meta_absent_for_local(self) -> None:
        """Local track → no ``remoteMeta`` in status."""
        track = _make_local_track()
        ctx = _make_ctx(track)
        result = await cmd_status(ctx, ["status", "-", 1])
        assert "remoteMeta" not in result


class TestRemoteMetaFieldsRadio:
    """Verify individual fields inside ``remoteMeta`` for radio streams."""

    @pytest.mark.asyncio
    async def test_remote_flag(self) -> None:
        track = _make_radio_track()
        ctx = _make_ctx(track)
        result = await cmd_status(ctx, ["status", "-", 1])
        assert result["remoteMeta"]["remote"] == 1

    @pytest.mark.asyncio
    async def test_title_from_static(self) -> None:
        """Without ICY data, title falls back to static track title."""
        track = _make_radio_track(title="Smooth Jazz FM")
        ctx = _make_ctx(track)
        result = await cmd_status(ctx, ["status", "-", 1])
        assert result["remoteMeta"]["title"] == "Smooth Jazz FM"

    @pytest.mark.asyncio
    async def test_title_from_icy_parsed(self) -> None:
        """With ICY 'Artist - Title', remoteMeta.title = parsed title part."""
        track = _make_radio_track(title="Jazz FM")
        ctx = _make_ctx(track, icy_title="Coltrane - Giant Steps")
        result = await cmd_status(ctx, ["status", "-", 1])
        # _icy_parsed_title takes precedence
        assert result["remoteMeta"]["title"] == "Giant Steps"

    @pytest.mark.asyncio
    async def test_artist_from_icy_parsed(self) -> None:
        """With ICY 'Artist - Title', remoteMeta.artist = parsed artist."""
        track = _make_radio_track(title="Jazz FM")
        ctx = _make_ctx(track, icy_title="Coltrane - Giant Steps")
        result = await cmd_status(ctx, ["status", "-", 1])
        assert result["remoteMeta"]["artist"] == "Coltrane"

    @pytest.mark.asyncio
    async def test_artist_empty_when_no_icy(self) -> None:
        """Without ICY data and no static artist, artist may be absent."""
        track = _make_radio_track(title="Jazz FM", artist="")
        ctx = _make_ctx(track)
        result = await cmd_status(ctx, ["status", "-", 1])
        # No artist key when both ICY and static are empty
        assert "artist" not in result["remoteMeta"]

    @pytest.mark.asyncio
    async def test_remote_title_is_station_name(self) -> None:
        """For radio, remote_title must be the station name (= track.title)."""
        track = _make_radio_track(title="BBC Radio 3")
        ctx = _make_ctx(track)
        result = await cmd_status(ctx, ["status", "-", 1])
        assert result["remoteMeta"]["remote_title"] == "BBC Radio 3"

    @pytest.mark.asyncio
    async def test_duration_zero_for_live(self) -> None:
        """Live radio → duration 0."""
        track = _make_radio_track()
        ctx = _make_ctx(track)
        result = await cmd_status(ctx, ["status", "-", 1])
        assert result["remoteMeta"]["duration"] == 0.0

    @pytest.mark.asyncio
    async def test_bitrate(self) -> None:
        track = _make_radio_track(bitrate=320)
        ctx = _make_ctx(track)
        result = await cmd_status(ctx, ["status", "-", 1])
        assert result["remoteMeta"]["bitrate"] == 320

    @pytest.mark.asyncio
    async def test_content_type(self) -> None:
        track = _make_radio_track(content_type="audio/aac")
        ctx = _make_ctx(track)
        result = await cmd_status(ctx, ["status", "-", 1])
        assert result["remoteMeta"]["type"] == "audio/aac"

    @pytest.mark.asyncio
    async def test_artwork_url(self) -> None:
        track = _make_radio_track(artwork_url="http://img.test/logo.png")
        ctx = _make_ctx(track)
        result = await cmd_status(ctx, ["status", "-", 1])
        assert result["remoteMeta"]["artwork_url"] == "http://img.test/logo.png"

    @pytest.mark.asyncio
    async def test_live_edge_for_live_stream(self) -> None:
        """Live stream → live_edge = 0 (at live edge)."""
        track = _make_radio_track(is_live=True)
        ctx = _make_ctx(track)
        result = await cmd_status(ctx, ["status", "-", 1])
        assert result["remoteMeta"]["live_edge"] == 0

    @pytest.mark.asyncio
    async def test_live_edge_for_non_live_remote(self) -> None:
        """Non-live remote (podcast) → live_edge = -1."""
        track = _make_podcast_track()
        ctx = _make_ctx(track)
        result = await cmd_status(ctx, ["status", "-", 1])
        assert result["remoteMeta"]["live_edge"] == -1


class TestRemoteMetaFieldsPodcast:
    """Verify remoteMeta for podcast tracks (remote but not live)."""

    @pytest.mark.asyncio
    async def test_duration_nonzero(self) -> None:
        """Podcast has a known duration."""
        track = _make_podcast_track(duration_ms=1800_000)
        ctx = _make_ctx(track)
        result = await cmd_status(ctx, ["status", "-", 1])
        assert result["remoteMeta"]["duration"] == 1800.0

    @pytest.mark.asyncio
    async def test_no_remote_title_for_podcast(self) -> None:
        """remote_title (station name) is only for radio, not podcasts."""
        track = _make_podcast_track()
        ctx = _make_ctx(track)
        result = await cmd_status(ctx, ["status", "-", 1])
        assert "remote_title" not in result["remoteMeta"]

    @pytest.mark.asyncio
    async def test_artist_from_static(self) -> None:
        """Podcast artist comes from the static PlaylistTrack field."""
        track = _make_podcast_track(artist="Science Weekly")
        ctx = _make_ctx(track)
        result = await cmd_status(ctx, ["status", "-", 1])
        assert result["remoteMeta"]["artist"] == "Science Weekly"

    @pytest.mark.asyncio
    async def test_album_from_static(self) -> None:
        """Podcast album (show name) is present in remoteMeta."""
        track = _make_podcast_track(album="Science Show")
        ctx = _make_ctx(track)
        result = await cmd_status(ctx, ["status", "-", 1])
        assert result["remoteMeta"]["album"] == "Science Show"


# =============================================================================
# ICY parsed fields in currentTrack (for Web-UI)
# =============================================================================


class TestCurrentTrackIcyFields:
    """Verify icy_artist / icy_title in currentTrack for the Web-UI."""

    @pytest.mark.asyncio
    async def test_icy_artist_and_title_set(self) -> None:
        """When ICY has 'Artist - Title', both fields are in currentTrack."""
        track = _make_radio_track(title="Jazz FM")
        ctx = _make_ctx(track, icy_title="Miles Davis - Blue in Green")
        result = await cmd_status(ctx, ["status", "-", 1])
        ct = result["currentTrack"]
        assert ct["icy_artist"] == "Miles Davis"
        assert ct["icy_title"] == "Blue in Green"

    @pytest.mark.asyncio
    async def test_icy_fields_absent_when_no_icy(self) -> None:
        """Without ICY metadata, icy_artist/icy_title are absent."""
        track = _make_radio_track(title="Jazz FM")
        ctx = _make_ctx(track)
        result = await cmd_status(ctx, ["status", "-", 1])
        ct = result["currentTrack"]
        assert "icy_artist" not in ct
        assert "icy_title" not in ct

    @pytest.mark.asyncio
    async def test_icy_no_dash_no_split(self) -> None:
        """ICY without ' - ' → no icy_artist, no icy_title (can't split)."""
        track = _make_radio_track(title="Jazz FM")
        ctx = _make_ctx(track, icy_title="Jazz FM News Update")
        result = await cmd_status(ctx, ["status", "-", 1])
        ct = result["currentTrack"]
        # No split possible → both absent
        assert "icy_artist" not in ct
        assert "icy_title" not in ct

    @pytest.mark.asyncio
    async def test_current_title_always_set(self) -> None:
        """current_title is the raw ICY string (not parsed)."""
        track = _make_radio_track(title="Jazz FM")
        ctx = _make_ctx(track, icy_title="Miles Davis - So What")
        result = await cmd_status(ctx, ["status", "-", 1])
        assert result["currentTrack"]["current_title"] == "Miles Davis - So What"

    @pytest.mark.asyncio
    async def test_local_track_no_icy_fields(self) -> None:
        """Local tracks never have icy_artist / icy_title."""
        track = _make_local_track()
        ctx = _make_ctx(track)
        result = await cmd_status(ctx, ["status", "-", 1])
        ct = result.get("currentTrack", {})
        assert "icy_artist" not in ct
        assert "icy_title" not in ct


# =============================================================================
# ICY interaction with remoteMeta
# =============================================================================


class TestRemoteMetaIcyInteraction:
    """Verify that ICY data flows correctly into remoteMeta."""

    @pytest.mark.asyncio
    async def test_icy_overrides_static_title_in_remote_meta(self) -> None:
        """When ICY is 'Artist - Title', remoteMeta.title = parsed title."""
        track = _make_radio_track(title="Jazz FM")
        ctx = _make_ctx(track, icy_title="Coltrane - A Love Supreme")
        result = await cmd_status(ctx, ["status", "-", 1])
        rm = result["remoteMeta"]
        assert rm["title"] == "A Love Supreme"
        assert rm["artist"] == "Coltrane"
        # remote_title stays as the station name
        assert rm["remote_title"] == "Jazz FM"

    @pytest.mark.asyncio
    async def test_icy_no_dash_uses_full_string(self) -> None:
        """ICY without dash → remoteMeta.title = full ICY string."""
        track = _make_radio_track(title="Jazz FM")
        ctx = _make_ctx(track, icy_title="Jazz FM Station Ident")
        result = await cmd_status(ctx, ["status", "-", 1])
        rm = result["remoteMeta"]
        assert rm["title"] == "Jazz FM Station Ident"

    @pytest.mark.asyncio
    async def test_icy_multiple_dashes_uses_full_string(self) -> None:
        """ICY with multiple ' - ' → cannot split, use full string."""
        track = _make_radio_track(title="Jazz FM")
        ctx = _make_ctx(track, icy_title="A - B - C")
        result = await cmd_status(ctx, ["status", "-", 1])
        rm = result["remoteMeta"]
        assert rm["title"] == "A - B - C"
        assert "artist" not in rm  # no static artist either

    @pytest.mark.asyncio
    async def test_static_artist_used_when_icy_has_no_artist(self) -> None:
        """When ICY doesn't parse to artist, static artist is used."""
        track = _make_radio_track(title="Jazz FM", artist="Jazz FM Network")
        ctx = _make_ctx(track, icy_title="Station Ident Jingle")
        result = await cmd_status(ctx, ["status", "-", 1])
        rm = result["remoteMeta"]
        # ICY parse yields artist="" → falls back to static artist
        assert rm["artist"] == "Jazz FM Network"

    @pytest.mark.asyncio
    async def test_no_icy_uses_static_fields(self) -> None:
        """Without ICY, remoteMeta uses static track fields."""
        track = _make_radio_track(title="BBC World Service", artist="BBC")
        ctx = _make_ctx(track)
        result = await cmd_status(ctx, ["status", "-", 1])
        rm = result["remoteMeta"]
        assert rm["title"] == "BBC World Service"
        assert rm["artist"] == "BBC"
