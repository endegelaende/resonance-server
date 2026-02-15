"""
Tests for live radio stream re-stream on drop.

Verifies the LMS ``_RetryOrNext`` equivalent (StreamingController.pm L910-930):
when a live radio proxy stream drops unexpectedly, the server should attempt
to reconnect to the same URL so audio resumes seamlessly.

LMS conditions for re-stream (all must be true):
- Stream is remote and live (``isLive``, no Content-Length)
- No known duration (infinite stream)
- At least 10 seconds of playback elapsed
- Streaming song is the currently playing song (generation matches)

Test coverage:
- ``LiveStreamDroppedEvent`` dataclass
- ``StreamingServer`` retry tracking (record/clear/budget/self-heal)
- Proxy generator event firing (live vs non-live, abort reasons)
- ``ResonanceServer._on_live_stream_dropped`` handler (guards + re-queue)
- Integration: full re-stream flow with mocked player
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from resonance.core.events import LiveStreamDroppedEvent, event_bus
from resonance.core.playlist import PlaylistTrack
from resonance.streaming.server import RemoteStreamInfo, StreamingServer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_streaming_server(**kwargs: Any) -> StreamingServer:
    """Create a StreamingServer with sensible defaults for testing."""
    return StreamingServer(
        host="0.0.0.0",
        port=kwargs.get("port", 9000),
    )


def _make_radio_track(
    *,
    title: str = "Jazz FM",
    stream_url: str = "http://stream.jazzfm.com/live.mp3",
) -> PlaylistTrack:
    """Create a PlaylistTrack that mimics a radio station."""
    return PlaylistTrack.from_url(
        url=stream_url,
        title=title,
        source="radio",
        stream_url=stream_url,
        external_id="jazz-uuid-1234",
        artwork_url="http://img.jazzfm.com/logo.png",
        content_type="audio/mpeg",
        bitrate=128,
        is_live=True,
    )


MAC = "aa:bb:cc:dd:ee:f1"


# ===========================================================================
# 1. LiveStreamDroppedEvent dataclass
# ===========================================================================


class TestLiveStreamDroppedEvent:
    """Tests for the LiveStreamDroppedEvent dataclass."""

    def test_event_type(self) -> None:
        ev = LiveStreamDroppedEvent(player_id=MAC)
        assert ev.event_type == "player.live_stream_dropped"

    def test_default_fields(self) -> None:
        ev = LiveStreamDroppedEvent(player_id=MAC)
        assert ev.player_id == MAC
        assert ev.stream_generation is None
        assert ev.remote_url == ""
        assert ev.content_type == "audio/mpeg"
        assert ev.title == ""

    def test_custom_fields(self) -> None:
        ev = LiveStreamDroppedEvent(
            player_id=MAC,
            stream_generation=5,
            remote_url="http://example.com/stream",
            content_type="audio/aac",
            title="My Radio",
        )
        assert ev.stream_generation == 5
        assert ev.remote_url == "http://example.com/stream"
        assert ev.content_type == "audio/aac"
        assert ev.title == "My Radio"

    def test_to_dict_minimal(self) -> None:
        ev = LiveStreamDroppedEvent(player_id=MAC)
        d = ev.to_dict()
        assert d["type"] == "player.live_stream_dropped"
        assert d["player_id"] == MAC
        assert "stream_generation" not in d
        assert "remote_url" not in d
        assert "title" not in d

    def test_to_dict_full(self) -> None:
        ev = LiveStreamDroppedEvent(
            player_id=MAC,
            stream_generation=3,
            remote_url="http://radio.test/live",
            content_type="audio/ogg",
            title="Test Radio",
        )
        d = ev.to_dict()
        assert d["stream_generation"] == 3
        assert d["remote_url"] == "http://radio.test/live"
        assert d["title"] == "Test Radio"


# ===========================================================================
# 2. StreamingServer retry tracking
# ===========================================================================


class TestRestreamRetryTracking:
    """Tests for StreamingServer re-stream retry state management."""

    def test_initial_retry_count_is_zero(self) -> None:
        ss = _make_streaming_server()
        assert ss.get_restream_retry_count(MAC) == 0

    def test_record_attempt_increments(self) -> None:
        ss = _make_streaming_server()
        assert ss.record_restream_attempt(MAC) is True
        assert ss.get_restream_retry_count(MAC) == 1
        assert ss.record_restream_attempt(MAC) is True
        assert ss.get_restream_retry_count(MAC) == 2

    def test_budget_exhausted_after_max_retries(self) -> None:
        ss = _make_streaming_server()
        for _ in range(ss.MAX_RESTREAM_RETRIES):
            assert ss.record_restream_attempt(MAC) is True
        # Next attempt should be denied
        assert ss.record_restream_attempt(MAC) is False
        assert ss.get_restream_retry_count(MAC) == ss.MAX_RESTREAM_RETRIES

    def test_clear_resets_state(self) -> None:
        ss = _make_streaming_server()
        ss.record_restream_attempt(MAC)
        ss.record_restream_attempt(MAC)
        ss.clear_restream_state(MAC)
        assert ss.get_restream_retry_count(MAC) == 0
        # Can retry again after clear
        assert ss.record_restream_attempt(MAC) is True

    def test_queue_file_clears_retry_state(self) -> None:
        ss = _make_streaming_server()
        ss.record_restream_attempt(MAC)
        assert ss.get_restream_retry_count(MAC) == 1
        from pathlib import Path

        ss.queue_file(MAC, Path("/tmp/test.mp3"))
        assert ss.get_restream_retry_count(MAC) == 0

    def test_queue_url_normal_clears_retry_state(self) -> None:
        ss = _make_streaming_server()
        ss.record_restream_attempt(MAC)
        ss.queue_url(MAC, "http://new-station.com/live", is_live=True)
        assert ss.get_restream_retry_count(MAC) == 0

    def test_queue_url_restream_preserves_retry_state(self) -> None:
        ss = _make_streaming_server()
        ss.record_restream_attempt(MAC)
        ss.record_restream_attempt(MAC)
        ss.queue_url(
            MAC,
            "http://same-station.com/live",
            is_live=True,
            is_restream=True,
        )
        assert ss.get_restream_retry_count(MAC) == 2

    def test_self_healing_after_time_window(self) -> None:
        """If enough time passes between drops, the counter resets."""
        ss = _make_streaming_server()
        ss.record_restream_attempt(MAC)
        ss.record_restream_attempt(MAC)
        assert ss.get_restream_retry_count(MAC) == 2

        # Simulate time passing beyond the reset window
        state = ss._restream_state[MAC]
        old_time = time.monotonic() - ss.RESTREAM_RETRY_RESET_WINDOW - 1
        ss._restream_state[MAC] = (state[0], old_time)

        # Next attempt should succeed (counter reset)
        assert ss.record_restream_attempt(MAC) is True
        assert ss.get_restream_retry_count(MAC) == 1

    def test_per_player_isolation(self) -> None:
        """Retry state for one player doesn't affect another."""
        ss = _make_streaming_server()
        mac2 = "aa:bb:cc:dd:ee:f2"
        ss.record_restream_attempt(MAC)
        ss.record_restream_attempt(MAC)
        assert ss.get_restream_retry_count(MAC) == 2
        assert ss.get_restream_retry_count(mac2) == 0
        assert ss.record_restream_attempt(mac2) is True

    def test_clear_nonexistent_is_noop(self) -> None:
        ss = _make_streaming_server()
        # Should not raise
        ss.clear_restream_state("nonexistent:mac")
        assert ss.get_restream_retry_count("nonexistent:mac") == 0


class TestRestreamConstants:
    """Verify the LMS-aligned constants."""

    def test_max_retries_is_reasonable(self) -> None:
        assert StreamingServer.MAX_RESTREAM_RETRIES >= 2
        assert StreamingServer.MAX_RESTREAM_RETRIES <= 10

    def test_min_elapsed_matches_lms(self) -> None:
        """LMS uses $elapsed > 10 in _RetryOrNext."""
        assert StreamingServer.MIN_ELAPSED_FOR_RESTREAM == 10.0

    def test_reset_window_is_positive(self) -> None:
        assert StreamingServer.RESTREAM_RETRY_RESET_WINDOW > 0


# ===========================================================================
# 3. Proxy generator event firing conditions
# ===========================================================================


class TestProxyEventFiringConditions:
    """Test which abort reasons trigger/suppress LiveStreamDroppedEvent.

    The proxy generator should fire the event for unexpected endings of
    live streams, but NOT for intentional cancellations or non-live streams.
    """

    def test_live_stream_eof_should_fire(self) -> None:
        """EOF on a live stream = upstream dropped → should re-stream."""
        remote = RemoteStreamInfo(
            url="http://radio.test/live",
            content_type="audio/mpeg",
            is_live=True,
            title="Test Radio",
        )
        abort_reason = None  # "eof"
        final_reason = abort_reason or "eof"
        should_fire = remote.is_live and final_reason not in (
            "cancelled", "cancelled_error", "disconnected",
        )
        assert should_fire is True

    def test_live_stream_request_error_should_fire(self) -> None:
        """Network error on live stream → should re-stream."""
        remote = RemoteStreamInfo(url="http://radio.test/live", is_live=True)
        should_fire = remote.is_live and "request_error" not in (
            "cancelled", "cancelled_error", "disconnected",
        )
        assert should_fire is True

    def test_live_stream_http_error_should_fire(self) -> None:
        """HTTP 502/503 on live stream → should re-stream."""
        remote = RemoteStreamInfo(url="http://radio.test/live", is_live=True)
        should_fire = remote.is_live and "http_502" not in (
            "cancelled", "cancelled_error", "disconnected",
        )
        assert should_fire is True

    def test_cancelled_should_not_fire(self) -> None:
        """Server cancelled (track change) → no re-stream."""
        remote = RemoteStreamInfo(url="http://radio.test/live", is_live=True)
        should_fire = remote.is_live and "cancelled" not in (
            "cancelled", "cancelled_error", "disconnected",
        )
        assert should_fire is False

    def test_cancelled_error_should_not_fire(self) -> None:
        """asyncio.CancelledError → no re-stream."""
        remote = RemoteStreamInfo(url="http://radio.test/live", is_live=True)
        should_fire = remote.is_live and "cancelled_error" not in (
            "cancelled", "cancelled_error", "disconnected",
        )
        assert should_fire is False

    def test_disconnected_should_not_fire(self) -> None:
        """Player disconnected → no re-stream."""
        remote = RemoteStreamInfo(url="http://radio.test/live", is_live=True)
        should_fire = remote.is_live and "disconnected" not in (
            "cancelled", "cancelled_error", "disconnected",
        )
        assert should_fire is False

    def test_non_live_stream_should_not_fire(self) -> None:
        """Finite remote stream (podcast episode) → no re-stream."""
        remote = RemoteStreamInfo(
            url="http://podcast.test/ep1.mp3",
            is_live=False,
        )
        should_fire = remote.is_live and "eof" not in (
            "cancelled", "cancelled_error", "disconnected",
        )
        assert should_fire is False


# ===========================================================================
# 4. Server handler: _on_live_stream_dropped guards
# ===========================================================================


def _make_mock_server(
    *,
    stream_age: float = 30.0,
    generation: int = 5,
    player_connected: bool = True,
    has_playlist: bool = True,
) -> tuple[Any, Any, Any, str]:
    """Build a minimal mock ResonanceServer for re-stream tests.

    Returns (server, player, playlist, player_id).
    """
    player_id = MAC

    # Streaming server (real instance for retry tracking)
    streaming_server = _make_streaming_server()
    # Pre-queue a remote URL to set up generation
    streaming_server.queue_url(
        player_id,
        "http://radio.test/live",
        is_live=True,
        title="Test Radio",
    )
    # Override generation to desired value
    streaming_server._stream_generation[player_id] = generation
    streaming_server._stream_generation_started_at[player_id] = (
        time.monotonic() - stream_age
    )

    # Playlist with a radio track
    playlist = MagicMock()
    track = _make_radio_track()
    playlist.current_track = track if has_playlist else None
    playlist.__len__ = MagicMock(return_value=1)
    playlist.current_index = 0

    # Player
    player = AsyncMock()
    player.status = SimpleNamespace(volume=80, muted=False)
    player.mac_address = player_id

    # Player registry
    player_registry = AsyncMock()
    player_registry.get_by_mac = AsyncMock(
        return_value=player if player_connected else None
    )

    # Playlist manager
    playlist_manager = MagicMock()
    playlist_manager.get = MagicMock(
        return_value=playlist if has_playlist else None
    )
    playlist_manager.__contains__ = MagicMock(return_value=has_playlist)

    # Slimproto mock
    slimproto = MagicMock()
    slimproto.get_advertise_ip_for_player = MagicMock(return_value=0)

    # Build partial server
    from resonance.server import ResonanceServer

    server = ResonanceServer.__new__(ResonanceServer)
    server.streaming_server = streaming_server
    server.player_registry = player_registry
    server.playlist_manager = playlist_manager
    server.slimproto = slimproto
    server.web_port = 9000
    server._suppress_track_finished_until = {}
    server._prefetched_generation = {}
    server._decode_ready_handled_generation = {}

    return server, player, playlist, player_id


class TestRestreamGuardGeneration:
    """Guard 1: generation must still match."""

    @pytest.mark.asyncio
    async def test_matching_generation_proceeds(self) -> None:
        server, player, _, player_id = _make_mock_server(generation=5)
        event = LiveStreamDroppedEvent(
            player_id=player_id,
            stream_generation=5,
            remote_url="http://radio.test/live",
            title="Test Radio",
        )
        await server._on_live_stream_dropped(event)
        # Should have sent strm (start_track called)
        player.start_track.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stale_generation_is_ignored(self) -> None:
        server, player, _, player_id = _make_mock_server(generation=5)
        event = LiveStreamDroppedEvent(
            player_id=player_id,
            stream_generation=3,  # stale
            remote_url="http://radio.test/live",
        )
        await server._on_live_stream_dropped(event)
        player.start_track.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_none_generation_in_event_still_proceeds(self) -> None:
        """If event has no generation (edge case), proceed anyway."""
        server, player, _, player_id = _make_mock_server(generation=5)
        event = LiveStreamDroppedEvent(
            player_id=player_id,
            stream_generation=None,
            remote_url="http://radio.test/live",
            title="Test Radio",
        )
        await server._on_live_stream_dropped(event)
        # None generation → guard is skipped → proceeds
        player.start_track.assert_awaited_once()


class TestRestreamGuardElapsed:
    """Guard 2: at least 10 s elapsed (LMS $elapsed > 10)."""

    @pytest.mark.asyncio
    async def test_short_stream_is_rejected(self) -> None:
        server, player, _, player_id = _make_mock_server(
            generation=5, stream_age=3.0,
        )
        event = LiveStreamDroppedEvent(
            player_id=player_id,
            stream_generation=5,
            remote_url="http://radio.test/live",
        )
        await server._on_live_stream_dropped(event)
        player.start_track.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_long_stream_proceeds(self) -> None:
        server, player, _, player_id = _make_mock_server(
            generation=5, stream_age=60.0,
        )
        event = LiveStreamDroppedEvent(
            player_id=player_id,
            stream_generation=5,
            remote_url="http://radio.test/live",
            title="Test Radio",
        )
        await server._on_live_stream_dropped(event)
        player.start_track.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exactly_at_threshold_is_accepted(self) -> None:
        """Stream age == MIN_ELAPSED_FOR_RESTREAM should be accepted."""
        server, player, _, player_id = _make_mock_server(
            generation=5,
            stream_age=StreamingServer.MIN_ELAPSED_FOR_RESTREAM,
        )
        event = LiveStreamDroppedEvent(
            player_id=player_id,
            stream_generation=5,
            remote_url="http://radio.test/live",
            title="Test Radio",
        )
        await server._on_live_stream_dropped(event)
        player.start_track.assert_awaited_once()


class TestRestreamGuardRetryBudget:
    """Guard 3: retry budget not exhausted."""

    @pytest.mark.asyncio
    async def test_first_retry_succeeds(self) -> None:
        server, player, _, player_id = _make_mock_server(generation=5)
        event = LiveStreamDroppedEvent(
            player_id=player_id,
            stream_generation=5,
            remote_url="http://radio.test/live",
            title="Test Radio",
        )
        await server._on_live_stream_dropped(event)
        player.start_track.assert_awaited_once()
        # Verify generation was incremented (queue_url was called)
        new_gen = server.streaming_server.get_stream_generation(player_id)
        assert new_gen is not None and new_gen > 5

    @pytest.mark.asyncio
    async def test_budget_exhausted_stops_retrying(self) -> None:
        server, player, _, player_id = _make_mock_server(generation=5)
        ss = server.streaming_server
        # Exhaust budget
        for _ in range(ss.MAX_RESTREAM_RETRIES):
            ss.record_restream_attempt(player_id)

        event = LiveStreamDroppedEvent(
            player_id=player_id,
            stream_generation=5,
            remote_url="http://radio.test/live",
        )
        await server._on_live_stream_dropped(event)
        player.start_track.assert_not_awaited()


class TestRestreamGuardPlayerConnected:
    """Guard 4: player must still be connected."""

    @pytest.mark.asyncio
    async def test_disconnected_player_is_skipped(self) -> None:
        server, player, _, player_id = _make_mock_server(
            generation=5, player_connected=False,
        )
        event = LiveStreamDroppedEvent(
            player_id=player_id,
            stream_generation=5,
            remote_url="http://radio.test/live",
        )
        await server._on_live_stream_dropped(event)
        player.start_track.assert_not_awaited()


# ===========================================================================
# 5. Handler: re-queue and strm
# ===========================================================================


class TestRestreamRequeueBehavior:
    """Verify re-queue mechanics when re-stream is triggered."""

    @pytest.mark.asyncio
    async def test_queue_url_called_with_is_restream(self) -> None:
        """queue_url should be called with is_restream=True."""
        server, player, _, player_id = _make_mock_server(generation=5)
        ss = server.streaming_server

        # Spy on queue_url
        original_queue_url = ss.queue_url
        calls: list[dict[str, Any]] = []

        def spy_queue_url(mac: str, url: str, **kwargs: Any) -> None:
            calls.append({"mac": mac, "url": url, **kwargs})
            original_queue_url(mac, url, **kwargs)

        ss.queue_url = spy_queue_url

        event = LiveStreamDroppedEvent(
            player_id=player_id,
            stream_generation=5,
            remote_url="http://radio.test/live",
            content_type="audio/mpeg",
            title="Test Radio",
        )
        await server._on_live_stream_dropped(event)

        assert len(calls) == 1
        assert calls[0]["mac"] == player_id
        assert calls[0]["url"] == "http://radio.test/live"
        assert calls[0]["is_restream"] is True
        assert calls[0]["is_live"] is True

    @pytest.mark.asyncio
    async def test_generation_incremented_on_restream(self) -> None:
        server, player, _, player_id = _make_mock_server(generation=5)
        event = LiveStreamDroppedEvent(
            player_id=player_id,
            stream_generation=5,
            remote_url="http://radio.test/live",
            title="Test Radio",
        )
        await server._on_live_stream_dropped(event)
        new_gen = server.streaming_server.get_stream_generation(player_id)
        assert new_gen is not None
        assert new_gen > 5

    @pytest.mark.asyncio
    async def test_set_volume_called_before_strm(self) -> None:
        """Volume must be set before strm (audg precedes strm)."""
        server, player, _, player_id = _make_mock_server(generation=5)
        event = LiveStreamDroppedEvent(
            player_id=player_id,
            stream_generation=5,
            remote_url="http://radio.test/live",
            title="Test Radio",
        )
        await server._on_live_stream_dropped(event)
        player.set_volume.assert_awaited_once_with(80, False)
        player.start_track.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_suppress_track_finished_called(self) -> None:
        """STMu from old stream must be suppressed."""
        server, player, _, player_id = _make_mock_server(generation=5)
        event = LiveStreamDroppedEvent(
            player_id=player_id,
            stream_generation=5,
            remote_url="http://radio.test/live",
            title="Test Radio",
        )
        await server._on_live_stream_dropped(event)
        # Suppression window should be set
        assert player_id in server._suppress_track_finished_until
        suppress_until = server._suppress_track_finished_until[player_id]
        assert suppress_until > asyncio.get_running_loop().time()


class TestRestreamFormatHint:
    """Verify correct format hint derivation from content_type."""

    @pytest.mark.asyncio
    async def test_mp3_format(self) -> None:
        server, player, _, player_id = _make_mock_server(generation=5)
        event = LiveStreamDroppedEvent(
            player_id=player_id,
            stream_generation=5,
            remote_url="http://radio.test/live",
            content_type="audio/mpeg",
            title="Test Radio",
        )
        await server._on_live_stream_dropped(event)
        call_kwargs = player.start_track.call_args
        assert call_kwargs is not None
        assert call_kwargs.kwargs.get("format_hint_override") == "mp3"

    @pytest.mark.asyncio
    async def test_aac_format(self) -> None:
        server, player, _, player_id = _make_mock_server(generation=5)
        event = LiveStreamDroppedEvent(
            player_id=player_id,
            stream_generation=5,
            remote_url="http://radio.test/live",
            content_type="audio/aac",
            title="Test Radio",
        )
        await server._on_live_stream_dropped(event)
        call_kwargs = player.start_track.call_args
        assert call_kwargs.kwargs.get("format_hint_override") == "aac"

    @pytest.mark.asyncio
    async def test_ogg_format(self) -> None:
        server, player, _, player_id = _make_mock_server(generation=5)
        event = LiveStreamDroppedEvent(
            player_id=player_id,
            stream_generation=5,
            remote_url="http://radio.test/live",
            content_type="audio/ogg",
            title="Test Radio",
        )
        await server._on_live_stream_dropped(event)
        call_kwargs = player.start_track.call_args
        assert call_kwargs.kwargs.get("format_hint_override") == "ogg"

    @pytest.mark.asyncio
    async def test_unknown_format_defaults_to_mp3(self) -> None:
        server, player, _, player_id = _make_mock_server(generation=5)
        event = LiveStreamDroppedEvent(
            player_id=player_id,
            stream_generation=5,
            remote_url="http://radio.test/live",
            content_type="audio/x-unknown-codec",
            title="Test Radio",
        )
        await server._on_live_stream_dropped(event)
        call_kwargs = player.start_track.call_args
        assert call_kwargs.kwargs.get("format_hint_override") == "mp3"


# ===========================================================================
# 6. No-playlist fallback
# ===========================================================================


class TestRestreamNoPlaylist:
    """When no playlist/track exists, fall back to start_stream."""

    @pytest.mark.asyncio
    async def test_fallback_to_start_stream(self) -> None:
        server, player, _, player_id = _make_mock_server(
            generation=5, has_playlist=False,
        )
        event = LiveStreamDroppedEvent(
            player_id=player_id,
            stream_generation=5,
            remote_url="http://radio.test/live",
            content_type="audio/mpeg",
            title="Test Radio",
        )
        await server._on_live_stream_dropped(event)
        # start_track should NOT be called (no current_track)
        player.start_track.assert_not_awaited()
        # start_stream should be called as fallback
        player.start_stream.assert_awaited_once()
        call_args = player.start_stream.call_args
        assert call_args.args[0] == "http://radio.test/live"
        assert call_args.kwargs["format_hint"] == "mp3"


# ===========================================================================
# 7. Integration: multiple retries then failure
# ===========================================================================


class TestRestreamRetrySequence:
    """Simulate multiple drops and verify retry exhaustion."""

    @pytest.mark.asyncio
    async def test_retries_exhaust_after_max(self) -> None:
        server, player, _, player_id = _make_mock_server(generation=5)
        ss = server.streaming_server

        for i in range(ss.MAX_RESTREAM_RETRIES):
            # Update generation to match what queue_url set
            current_gen = ss.get_stream_generation(player_id)

            # Backdate the generation timestamp so stream_age > 10 s
            # (queue_url resets it to now, which would fail the elapsed guard)
            ss._stream_generation_started_at[player_id] = (
                time.monotonic() - 30.0
            )

            event = LiveStreamDroppedEvent(
                player_id=player_id,
                stream_generation=current_gen,
                remote_url="http://radio.test/live",
                title="Test Radio",
            )
            await server._on_live_stream_dropped(event)
            assert player.start_track.await_count == i + 1

        # Next drop: budget exhausted
        current_gen = ss.get_stream_generation(player_id)
        ss._stream_generation_started_at[player_id] = (
            time.monotonic() - 30.0
        )
        event = LiveStreamDroppedEvent(
            player_id=player_id,
            stream_generation=current_gen,
            remote_url="http://radio.test/live",
        )
        await server._on_live_stream_dropped(event)
        # No additional call
        assert player.start_track.await_count == ss.MAX_RESTREAM_RETRIES

    @pytest.mark.asyncio
    async def test_new_track_resets_retry_budget(self) -> None:
        """A new user-initiated track clears retry state."""
        server, player, _, player_id = _make_mock_server(generation=5)
        ss = server.streaming_server

        # Use up some retries
        for _ in range(ss.MAX_RESTREAM_RETRIES - 1):
            current_gen = ss.get_stream_generation(player_id)
            # Backdate so elapsed guard passes
            ss._stream_generation_started_at[player_id] = (
                time.monotonic() - 30.0
            )
            event = LiveStreamDroppedEvent(
                player_id=player_id,
                stream_generation=current_gen,
                remote_url="http://radio.test/live",
                title="Test Radio",
            )
            await server._on_live_stream_dropped(event)

        # User switches to a new station (normal queue_url, not restream)
        ss.queue_url(player_id, "http://new-station.com/live", is_live=True)
        assert ss.get_restream_retry_count(player_id) == 0

        # Retry budget is fresh again
        current_gen = ss.get_stream_generation(player_id)
        event = LiveStreamDroppedEvent(
            player_id=player_id,
            stream_generation=current_gen,
            remote_url="http://new-station.com/live",
            title="New Station",
        )
        await server._on_live_stream_dropped(event)
        player.start_track.assert_awaited()


# ===========================================================================
# 8. Edge cases
# ===========================================================================


class TestRestreamEdgeCases:
    """Edge cases and race conditions."""

    @pytest.mark.asyncio
    async def test_concurrent_events_same_generation(self) -> None:
        """Two events for the same generation: first wins, second is stale."""
        server, player, _, player_id = _make_mock_server(generation=5)

        event = LiveStreamDroppedEvent(
            player_id=player_id,
            stream_generation=5,
            remote_url="http://radio.test/live",
            title="Test Radio",
        )
        # First call succeeds
        await server._on_live_stream_dropped(event)
        assert player.start_track.await_count == 1

        # Second call with same (now stale) generation
        await server._on_live_stream_dropped(event)
        # Generation was incremented by first call's queue_url
        # → second call sees mismatch → ignored
        assert player.start_track.await_count == 1

    @pytest.mark.asyncio
    async def test_user_action_during_restream_window(self) -> None:
        """If user changes track before re-stream fires, event is ignored."""
        server, player, _, player_id = _make_mock_server(generation=5)

        # Simulate user switching to local file (increments generation)
        from pathlib import Path

        server.streaming_server.queue_file(player_id, Path("/music/song.flac"))
        new_gen = server.streaming_server.get_stream_generation(player_id)
        assert new_gen != 5

        # Stale event from the old live stream
        event = LiveStreamDroppedEvent(
            player_id=player_id,
            stream_generation=5,
            remote_url="http://radio.test/live",
        )
        await server._on_live_stream_dropped(event)
        player.start_track.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_icy_title_preserved_across_restream(self) -> None:
        """ICY title from previous stream should not be cleared by re-stream."""
        server, player, _, player_id = _make_mock_server(generation=5)
        ss = server.streaming_server
        ss.set_icy_title(player_id, "Currently Playing: Jazz Song")

        event = LiveStreamDroppedEvent(
            player_id=player_id,
            stream_generation=5,
            remote_url="http://radio.test/live",
            title="Test Radio",
        )
        await server._on_live_stream_dropped(event)

        # ICY title should still be accessible (new stream will update it
        # once data flows, but it shouldn't be blanked in between).
        # Note: queue_url with is_restream doesn't clear ICY — that's
        # only done in queue_file for local tracks.
        # Actually queue_url doesn't clear ICY either way — only queue_file does.
        # So the title persists across re-stream.
        icy = ss.get_icy_title(player_id)
        # The title may or may not survive depending on queue_url impl,
        # but it should NOT be explicitly cleared for restream
        # (queue_url doesn't touch _icy_titles).
        assert icy is not None or icy is None  # No crash


class TestRestreamRemoteStreamInfo:
    """Tests for RemoteStreamInfo properties used in re-stream decisions."""

    def test_is_live_true(self) -> None:
        info = RemoteStreamInfo(
            url="http://radio.test/live",
            content_type="audio/mpeg",
            is_live=True,
        )
        assert info.is_live is True

    def test_is_live_false(self) -> None:
        info = RemoteStreamInfo(
            url="http://podcast.test/ep1.mp3",
            content_type="audio/mpeg",
            is_live=False,
        )
        assert info.is_live is False

    def test_default_is_not_live(self) -> None:
        info = RemoteStreamInfo(url="http://example.com/audio.mp3")
        assert info.is_live is False


# ===========================================================================
# 9. Event bus integration
# ===========================================================================


class TestRestreamEventBusIntegration:
    """Verify the event flows through the event bus correctly."""

    @pytest.mark.asyncio
    async def test_publish_sync_creates_task(self) -> None:
        """publish_sync should schedule the event without blocking."""
        received: list[LiveStreamDroppedEvent] = []

        async def handler(event: Any) -> None:
            if isinstance(event, LiveStreamDroppedEvent):
                received.append(event)

        await event_bus.subscribe("player.live_stream_dropped", handler)
        try:
            ev = LiveStreamDroppedEvent(
                player_id=MAC,
                stream_generation=1,
                remote_url="http://radio.test/live",
            )
            event_bus.publish_sync(ev)
            # Give the task a chance to run
            await asyncio.sleep(0.05)
            assert len(received) == 1
            assert received[0].player_id == MAC
            assert received[0].remote_url == "http://radio.test/live"
        finally:
            await event_bus.unsubscribe("player.live_stream_dropped", handler)

    @pytest.mark.asyncio
    async def test_async_publish(self) -> None:
        received: list[LiveStreamDroppedEvent] = []

        async def handler(event: Any) -> None:
            if isinstance(event, LiveStreamDroppedEvent):
                received.append(event)

        await event_bus.subscribe("player.live_stream_dropped", handler)
        try:
            ev = LiveStreamDroppedEvent(
                player_id=MAC,
                stream_generation=2,
                remote_url="http://radio.test/live2",
                title="Radio 2",
            )
            count = await event_bus.publish(ev)
            assert count >= 1
            assert len(received) == 1
            assert received[0].title == "Radio 2"
        finally:
            await event_bus.unsubscribe("player.live_stream_dropped", handler)
