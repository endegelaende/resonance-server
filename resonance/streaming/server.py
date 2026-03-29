"""
HTTP Streaming Server for Resonance.

This module provides audio streaming functionality for Squeezebox players.
When a player receives a 'strm' command, it connects back to the HTTP server
to receive the audio data.

IMPORTANT: This class does NOT bind its own port. The streaming endpoint
is exposed via FastAPI routes (see web/routes/streaming.py). This avoids
port conflicts with the main web server.

STREAM CANCELLATION (LMS-Style):
================================
When a player changes tracks or seeks, we need to cancel the current HTTP stream
immediately so the new track can start without delay. This is done via
CancellationTokens - each stream checks its token and aborts if cancelled.
This mimics LMS's StreamingController._Stream() behavior:
  1. songStreamController->close()  -- old stream is cancelled
  2. song->open(seekdata)           -- new stream starts immediately

NO LOCKS: Unlike earlier implementations, we do NOT use per-player locks
to serialize streams. LMS doesn't use locks either - it simply closes the
old stream and opens a new one. Locks caused blocking during rapid seeks
because the new stream had to wait for the old transcoder to finish.
"""

import asyncio
import logging
import mimetypes
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from resonance.protocol.commands import FLAG_NO_RESTART_DECODER
from resonance.streaming.crossfade import PreparedCrossfadePlan
from resonance.streaming.runtime import (
    PlayerRuntimeConfig,
    RuntimeStreamParams,
    apply_player_pref,
    compute_replay_gain_fixed,
    default_player_pref_value,
    normalize_player_pref_value,
)

logger = logging.getLogger(__name__)


class CancellationToken:
    """
    Token to signal stream cancellation.

    When a player changes tracks, we set cancelled=True on their token.
    The streaming generator checks this and aborts, allowing the new
    stream to start immediately without waiting for buffer drain.
    """

    __slots__ = ("_cancelled", "_generation")

    def __init__(self, generation: int = 0) -> None:
        self._cancelled = False
        self._generation = generation

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    @property
    def generation(self) -> int:
        return self._generation

    def cancel(self) -> None:
        self._cancelled = True


@dataclass(frozen=True, slots=True)
class PendingCrossfade:
    """A queued server-side crossfade plan bound to a stream generation."""

    generation: int
    plan: PreparedCrossfadePlan


@dataclass(frozen=True, slots=True)
class RemoteStreamInfo:
    """Metadata for a queued remote URL stream.

    Stored by :meth:`StreamingServer.queue_url` and consumed by the
    streaming route to proxy the remote audio to the player.
    """

    url: str
    """The URL to fetch audio data from (may be HTTP or HTTPS)."""

    content_type: str = "audio/mpeg"
    """Expected MIME type of the remote stream."""

    is_live: bool = False
    """``True`` for infinite live streams (Internet Radio)."""

    title: str = ""
    """Display title (for logging / ICY metadata)."""

    start_byte: int = 0
    """Byte offset for seeking in remote streams (HTTP Range header).
    0 means start from the beginning (no Range header sent)."""


class ResolvedStream(NamedTuple):
    """Result of :meth:`StreamingServer.resolve_stream`.

    Exactly one of *file_path* or *remote* is set.
    """

    file_path: Path | None
    """Local file path (for direct / transcoded streaming)."""

    remote: RemoteStreamInfo | None
    """Remote URL info (for proxy streaming)."""


# Buffer size for streaming
STREAM_BUFFER_SIZE = 65536  # 64KB chunks


class StreamingServer:
    """
    Audio streaming service for Squeezebox players.

    This class manages the queue of files to stream and provides
    methods to resolve which file to serve for a given player.

    NOTE: This class no longer binds its own socket. Instead, streaming
    is handled via FastAPI routes that call into this class.

    Attributes:
        port: The HTTP port where streaming is available (for strm command).
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 9000,
        audio_provider: Callable[[str], Path | None] | None = None,
    ) -> None:
        """
        Initialize the streaming server.

        Args:
            host: Host address (kept for compatibility, not used for binding).
            port: HTTP port (used in strm command URL generation).
            audio_provider: Optional callback to resolve player MAC to audio file.
                           Takes player MAC, returns Path to audio file or None.
        """
        self.host = host
        self.port = port
        self._audio_provider = audio_provider
        self._running = False

        # Queue of files to stream, keyed by player MAC
        self._stream_queue: dict[str, Path] = {}

        # Queue of remote URLs to proxy-stream, keyed by player MAC.
        # When a remote URL is queued, ``_stream_queue`` is cleared for that
        # player and vice-versa — only one source type is active at a time.
        self._remote_urls: dict[str, RemoteStreamInfo] = {}

        # Seek positions for transcoded streams, keyed by player MAC
        # Values are (start_seconds, end_seconds or None)
        self._seek_positions: dict[str, tuple[float, float | None]] = {}

        # Start offset for seek operations (LMS-style startOffset).
        #
        # After a seek to position X, the player reports elapsed time relative to
        # the NEW stream start (0, 1, 2, 3...). The real track position is:
        #   actual_elapsed = start_offset + raw_elapsed
        #
        # This mirrors LMS's song.startOffset() which is set during seek and
        # added to songElapsedSeconds() in playingSongElapsed().
        #
        # The offset is cleared when:
        # - A new track starts (queue_file without seek)
        # - A new seek happens (overwrites the old offset)
        #
        # It is NOT cleared based on time - it's needed for the entire track duration.
        self._start_offset: dict[str, float] = {}

        # Byte offsets for direct-stream seeking, keyed by player MAC
        # Used for MP3/FLAC/OGG where we can seek by byte position
        self._byte_offsets: dict[str, int] = {}

        # Active stream cancellation tokens, keyed by player MAC
        # When a player changes tracks, we cancel the old token so the
        # streaming generator aborts immediately (LMS-style closeStream)
        self._stream_tokens: dict[str, CancellationToken] = {}

        # Generation counter per player to detect stale streams
        self._stream_generation: dict[str, int] = {}

        # Monotonic timestamp when the current generation was queued.
        self._stream_generation_started_at: dict[str, float] = {}

        # Duration (in seconds) of the currently-playing track per player.
        # Used for server-side track-end detection: when stream_age exceeds
        # this value, the server can synthesize a track-finished event for
        # players that never send STMd/STMu (e.g. controller-class devices
        # with transitionType=0).
        self._track_duration: dict[str, float] = {}

        # Runtime playback prefs/features (gapless/crossfade/replaygain), per player.
        self._runtime_config: dict[str, PlayerRuntimeConfig] = {}

        # Pending server-side crossfade plan keyed by player MAC.
        # A plan is consumed by the next /stream.mp3 request for that generation.
        self._crossfade_plans: dict[str, PendingCrossfade] = {}

        # Per-player ICY metadata (StreamTitle) parsed from proxied radio
        # streams.  Updated by the streaming route's ``_log_icy_metadata()``
        # callback and read by ``cmd_status`` to populate ``current_title``.
        self._icy_titles: dict[str, str] = {}

        # ── Re-stream retry tracking (LMS _RetryOrNext equivalent) ──
        #
        # When a live radio stream drops unexpectedly, we attempt to
        # reconnect (like LMS StreamingController.pm L920-927).  To
        # prevent infinite reconnect loops on dead URLs, we track retry
        # count and timing per player.
        #
        # Format: {player_mac: (retry_count, last_retry_monotonic)}
        # Reset when: a genuinely new track is queued (queue_file / normal
        #   queue_url), or when enough time passes between drops (the
        #   stream was working fine in between → self-healing).
        self._restream_state: dict[str, tuple[int, float]] = {}

        # NOTE: We previously had per-player locks (_stream_locks) to serialize
        # transcoded streams. This was REMOVED because it caused blocking during
        # rapid seeks - the new stream had to wait for the old transcoder to finish.
        #
        # LMS-style approach: No locks! Old stream aborts via cancel_token,
        # new stream starts immediately. See StreamingController._Stream() in LMS.

    def get_runtime_config(self, player_mac: str) -> PlayerRuntimeConfig:
        """Get or create runtime playback config for a player."""
        return self._runtime_config.setdefault(player_mac, PlayerRuntimeConfig())

    def set_player_pref(
        self,
        player_mac: str,
        pref_name: str,
        pref_value: str,
    ) -> tuple[str, str] | None:
        """
        Apply a runtime-relevant playerpref.

        Returns:
            (canonical_pref_name, normalized_value) if handled, otherwise None.
        """
        normalized = normalize_player_pref_value(pref_name, pref_value)
        if normalized is None:
            return None

        canonical_name, value = normalized
        config = self.get_runtime_config(player_mac)
        apply_player_pref(config, canonical_name, value)
        return canonical_name, value

    @staticmethod
    def get_player_pref_default(pref_name: str) -> str | None:
        """Return default for runtime-relevant playerpref keys."""
        return default_player_pref_value(pref_name)

    async def resolve_runtime_stream_params(
        self,
        player_mac: str,
        *,
        track: object | None,
        playlist: object | None = None,
        allow_transition: bool = True,
        is_currently_playing: bool = False,
        track_duration_s: float | None = None,
    ) -> RuntimeStreamParams:
        """
        Resolve transition/gapless/replaygain parameters for strm start.

        Args:
            track_duration_s: Track duration in seconds.  Used to shorten
                transitions on very short tracks (LMS: Squeezebox.pm stream_s).
        """
        config = self.get_runtime_config(player_mac)
        previous_track, next_track = self._playlist_neighbors(playlist)

        same_album_transition = self._tracks_album_adjacent(
            previous_track, track
        ) or self._tracks_album_adjacent(track, next_track)

        transition_type = 0
        transition_duration = 0

        if allow_transition and config.transition_type > 0:
            transition_type = config.transition_type
            transition_duration = config.transition_duration

            # Smart transition: disable fades/crossfades on adjacent album tracks.
            if config.transition_smart and same_album_transition:
                transition_type = 0
                transition_duration = 0

            # Fade-in only makes sense when already playing.
            if not is_currently_playing:
                if transition_type == 2:  # fade in
                    transition_type = 0
                elif transition_type == 4:  # fade in/out -> fade out
                    transition_type = 3

            # Short-track clamping (LMS: Squeezebox.pm stream_s).
            # Don't do long transitions on very short tracks.
            if (
                transition_duration > 0
                and track_duration_s is not None
                and track_duration_s > 0
                and track_duration_s < transition_duration * 2
            ):
                transition_duration = max(1, int(track_duration_s / 3))

        flags = 0
        if config.gapless and transition_type == 0:
            flags |= FLAG_NO_RESTART_DECODER

        replay_gain = 0
        track_path = str(getattr(track, "path", "") or "")
        if config.replay_gain_mode > 0 and track_path:
            try:
                replay_gain = await asyncio.to_thread(
                    compute_replay_gain_fixed,
                    path=track_path,
                    replay_gain_mode=config.replay_gain_mode,
                    remote_replay_gain_db=config.remote_replay_gain_db,
                    prefer_album_gain=(config.replay_gain_mode == 2)
                    or (config.replay_gain_mode == 3 and same_album_transition),
                )
            except Exception:
                logger.debug(
                    "Failed to resolve replay gain for %s (%s)",
                    player_mac,
                    track_path,
                    exc_info=True,
                )

        return RuntimeStreamParams(
            transition_type=transition_type,
            transition_duration=transition_duration,
            flags=flags,
            replay_gain=replay_gain,
        )

    @staticmethod
    def _playlist_neighbors(playlist: object | None) -> tuple[object | None, object | None]:
        """Return previous/next track around playlist.current_index."""
        if playlist is None:
            return None, None

        tracks_attr = getattr(playlist, "tracks", None)
        if tracks_attr is None:
            return None, None

        try:
            tracks = list(tracks_attr)
            current_index = int(getattr(playlist, "current_index", 0))
        except Exception:
            return None, None

        if current_index < 0 or current_index >= len(tracks):
            return None, None

        previous_track = tracks[current_index - 1] if current_index > 0 else None
        next_track = tracks[current_index + 1] if current_index + 1 < len(tracks) else None
        return previous_track, next_track

    @staticmethod
    def _tracks_album_adjacent(left: object | None, right: object | None) -> bool:
        """Best-effort check whether two tracks are adjacent on the same album."""
        if left is None or right is None:
            return False

        left_album = getattr(left, "album_id", None)
        right_album = getattr(right, "album_id", None)

        if left_album is None or right_album is None:
            left_album = getattr(left, "album_title", getattr(left, "album", None))
            right_album = getattr(right, "album_title", getattr(right, "album", None))

        if left_album is None or right_album is None or left_album != right_album:
            return False

        left_disc = getattr(left, "disc_no", None)
        right_disc = getattr(right, "disc_no", None)
        left_track_no = getattr(left, "track_no", None)
        right_track_no = getattr(right, "track_no", None)

        try:
            if left_track_no is not None and right_track_no is not None:
                if (
                    left_disc is not None
                    and right_disc is not None
                    and int(left_disc) != int(right_disc)
                ):
                    return False
                return abs(int(left_track_no) - int(right_track_no)) == 1
        except Exception:
            return True

        # If track numbers are missing, album match is still good enough for smart mode.
        return True

    def get_stream_generation(self, player_mac: str) -> int | None:
        """
        Get the current stream generation for a player.

        The generation counter is incremented each time a new file is queued,
        allowing detection of stale events (e.g., late STMd from a previous stream).

        Args:
            player_mac: MAC address of the player.

        Returns:
            The current generation counter, or None if the player has no stream history.
        """
        return self._stream_generation.get(player_mac)

    def get_stream_generation_age(self, player_mac: str) -> float | None:
        """
        Return age in seconds of the current stream generation.

        Used as an upper bound for raw elapsed samples right after stream
        switches so stale values from an old stream generation can be ignored.
        """
        started_at = self._stream_generation_started_at.get(player_mac)
        if started_at is None:
            return None

        age = time.monotonic() - started_at
        if age < 0:
            return 0.0
        return age

    def set_track_duration(self, player_mac: str, duration_seconds: float) -> None:
        """
        Store the duration of the currently-playing track for a player.

        This is used by the server-side track-end detection logic: when
        ``get_stream_generation_age()`` exceeds this value, the server
        can infer that the track has finished even if the player never
        sent STMd/STMu.

        Args:
            player_mac: MAC address of the player.
            duration_seconds: Track duration in seconds (>0).
        """
        if duration_seconds > 0:
            self._track_duration[player_mac] = duration_seconds

    def get_track_duration(self, player_mac: str) -> float | None:
        """
        Return the stored track duration for a player, or *None*.
        """
        return self._track_duration.get(player_mac)

    def cancel_stream(self, player_mac: str) -> None:
        """
        Cancel any active stream for a player.

        This should be called before starting a new track to ensure
        the old HTTP stream aborts immediately (LMS-style closeStream).

        Args:
            player_mac: MAC address of the player.
        """
        if player_mac in self._stream_tokens:
            old_token = self._stream_tokens[player_mac]
            old_token.cancel()
            logger.debug(
                "Cancelled stream for player %s (generation %d)", player_mac, old_token.generation
            )
        self._crossfade_plans.pop(player_mac, None)

    def get_cancellation_token(self, player_mac: str) -> CancellationToken:
        """
        Get the current cancellation token for a player's stream.

        The streaming route should check this token periodically and
        abort if cancelled. A new token is created each time a file
        is queued.

        Args:
            player_mac: MAC address of the player.

        Returns:
            The current CancellationToken for this player.
        """
        if player_mac not in self._stream_tokens:
            gen = self._stream_generation.get(player_mac, 0)
            self._stream_tokens[player_mac] = CancellationToken(gen)
        return self._stream_tokens[player_mac]

    def queue_file(self, player_mac: str, file_path: Path) -> None:
        """
        Queue an audio file to be streamed to a player.

        When the player connects with a GET request, this file will be served.
        This also cancels any existing stream and creates a new cancellation token.

        Args:
            player_mac: MAC address of the player.
            file_path: Path to the audio file to stream.
        """
        # Cancel any existing stream first (LMS-style closeStream)
        self.cancel_stream(player_mac)

        # Increment generation and create new token
        gen = self._stream_generation.get(player_mac, 0) + 1
        self._stream_generation[player_mac] = gen
        self._stream_generation_started_at[player_mac] = time.monotonic()
        self._stream_tokens[player_mac] = CancellationToken(gen)

        self._stream_queue[player_mac] = file_path
        # Clear stale ICY title — local file playback should not show
        # a leftover radio station's StreamTitle.
        self._icy_titles.pop(player_mac, None)
        # A local file replaces any pending remote URL.
        self._remote_urls.pop(player_mac, None)
        # Clear any previous seek position
        self._seek_positions.pop(player_mac, None)
        self._byte_offsets.pop(player_mac, None)
        self._crossfade_plans.pop(player_mac, None)

        # Clear start offset for non-seek queueing (track starts from beginning).
        self._start_offset.pop(player_mac, None)

        # Clear stale track duration — caller should set the new value via
        # set_track_duration() once the track metadata is known.
        self._track_duration.pop(player_mac, None)

        # New local track → reset re-stream retry state.
        self._restream_state.pop(player_mac, None)

        logger.info("Queued %s for player %s (generation %d)", file_path.name, player_mac, gen)

    def queue_url(
        self,
        player_mac: str,
        url: str,
        *,
        content_type: str = "audio/mpeg",
        is_live: bool = False,
        title: str = "",
        is_restream: bool = False,
    ) -> None:
        """Queue a remote URL to be proxy-streamed to a player.

        The server will fetch the URL on behalf of the player and relay
        audio data via ``/stream.mp3``.  This is required because
        Squeezebox hardware cannot handle HTTPS and has limited HTTP
        capabilities.

        Args:
            player_mac: MAC address of the player.
            url: Remote audio URL (HTTP or HTTPS).
            content_type: MIME type of the remote stream.
            is_live: ``True`` for infinite live streams (Internet Radio).
            title: Human-readable title for logging.
            is_restream: ``True`` when re-queuing after a live stream drop.
                Preserves the retry counter so the limit is enforced.
        """
        # Cancel any existing stream first (LMS-style closeStream)
        self.cancel_stream(player_mac)

        # Increment generation and create new token
        gen = self._stream_generation.get(player_mac, 0) + 1
        self._stream_generation[player_mac] = gen
        self._stream_generation_started_at[player_mac] = time.monotonic()
        self._stream_tokens[player_mac] = CancellationToken(gen)

        self._remote_urls[player_mac] = RemoteStreamInfo(
            url=url,
            content_type=content_type,
            is_live=is_live,
            title=title,
        )
        # A remote URL replaces any pending local file.
        self._stream_queue.pop(player_mac, None)
        # Clear seek/offset state — not applicable for remote streams.
        self._seek_positions.pop(player_mac, None)
        self._byte_offsets.pop(player_mac, None)
        self._crossfade_plans.pop(player_mac, None)
        self._start_offset.pop(player_mac, None)
        self._track_duration.pop(player_mac, None)

        # New user-initiated URL → reset retry state.
        # Re-stream attempts preserve the counter (is_restream=True).
        if not is_restream:
            self._restream_state.pop(player_mac, None)

        logger.info(
            "Queued remote URL for player %s (generation %d%s): %s",
            player_mac,
            gen,
            " [restream]" if is_restream else "",
            title or url,
        )

    def queue_url_with_seek(
        self,
        player_mac: str,
        url: str,
        start_byte: int,
        start_seconds: float,
        *,
        content_type: str = "audio/mpeg",
        title: str = "",
    ) -> None:
        """Queue a remote URL with byte-offset seeking via HTTP Range.

        Like :meth:`queue_url` but sets ``start_byte`` on the
        :class:`RemoteStreamInfo` so the streaming proxy sends an HTTP
        ``Range: bytes=<start_byte>-`` header.  Also records
        ``start_offset`` so elapsed-time calculation stays correct after
        the seek (same LMS-style formula as local-file seeks).

        Args:
            player_mac: MAC address of the player.
            url: Remote audio URL (HTTP or HTTPS).
            start_byte: Byte offset to start streaming from.
            start_seconds: Seek target in seconds (for elapsed calculation).
            content_type: MIME type of the remote stream.
            title: Human-readable title for logging.
        """
        self.cancel_stream(player_mac)

        gen = self._stream_generation.get(player_mac, 0) + 1
        self._stream_generation[player_mac] = gen
        self._stream_generation_started_at[player_mac] = time.monotonic()
        self._stream_tokens[player_mac] = CancellationToken(gen)

        self._remote_urls[player_mac] = RemoteStreamInfo(
            url=url,
            content_type=content_type,
            is_live=False,
            title=title,
            start_byte=start_byte,
        )
        self._stream_queue.pop(player_mac, None)
        self._seek_positions.pop(player_mac, None)
        self._byte_offsets.pop(player_mac, None)
        self._crossfade_plans.pop(player_mac, None)

        # LMS-style start offset so status reports correct position.
        self._start_offset[player_mac] = float(start_seconds)

        # Seeking is a manual action — reset retry state.
        self._restream_state.pop(player_mac, None)

        logger.info(
            "Queued remote URL for player %s with seek: byte=%d, start=%.1fs (generation %d): %s",
            player_mac,
            start_byte,
            start_seconds,
            gen,
            title or url,
        )

    # ------------------------------------------------------------------
    # ICY metadata (StreamTitle) per player
    # ------------------------------------------------------------------

    def set_icy_title(self, player_mac: str, title: str) -> bool:
        """Store the latest ICY StreamTitle parsed from a proxied radio stream.

        Called by the streaming route when ICY metadata is stripped from
        the upstream byte stream.  The value is read by ``cmd_status``
        to populate ``current_title`` for remote/radio tracks.

        Mirrors LMS ``setCurrentTitle()`` (Info.pm L516) change detection:
        only updates (and returns ``True``) when the title actually differs
        from the previously stored value.  This prevents firing spurious
        ``playlist newmetadata`` events when the same ICY block is repeated
        (which happens every ~16 KB of audio data).

        Returns:
            ``True`` if the title changed, ``False`` if it was identical
            to the previously stored value.
        """
        previous = self._icy_titles.get(player_mac)
        if previous == title:
            return False
        self._icy_titles[player_mac] = title
        return True

    def get_icy_title(self, player_mac: str) -> str | None:
        """Return the last ICY StreamTitle for *player_mac*, or ``None``."""
        return self._icy_titles.get(player_mac)

    def clear_icy_title(self, player_mac: str) -> None:
        """Remove any stored ICY title for *player_mac*.

        Called when a new (non-radio) stream is queued so stale radio
        metadata does not bleed into a local-library track's status.
        """
        self._icy_titles.pop(player_mac, None)

    # ------------------------------------------------------------------
    # Live-stream re-stream retry tracking (LMS _RetryOrNext equivalent)
    # ------------------------------------------------------------------

    #: Maximum consecutive re-stream attempts before giving up.
    #: LMS uses retryData with count but has no hard-coded max —
    #: we add a safety limit to prevent infinite loops on dead URLs.
    MAX_RESTREAM_RETRIES: int = 3

    #: If more than this many seconds pass between drops, reset the
    #: retry counter (the stream was working fine in between).
    RESTREAM_RETRY_RESET_WINDOW: float = 120.0

    #: Minimum elapsed playback (seconds) before re-stream is attempted.
    #: Matches LMS ``$elapsed > 10`` check in ``_RetryOrNext``.
    MIN_ELAPSED_FOR_RESTREAM: float = 10.0

    def record_restream_attempt(self, player_mac: str) -> bool:
        """Record a re-stream attempt and return whether it is allowed.

        Returns ``True`` if the attempt is within limits, ``False`` if
        the retry budget is exhausted (caller should let normal
        track-finished / playlist-advance logic proceed).

        The counter self-heals: if the last drop was more than
        :attr:`RESTREAM_RETRY_RESET_WINDOW` seconds ago, the counter
        resets — the stream was working fine in the meantime.
        """
        now = time.monotonic()
        prev = self._restream_state.get(player_mac)

        if prev is not None:
            count, last_time = prev
            if now - last_time > self.RESTREAM_RETRY_RESET_WINDOW:
                # Long gap since last drop — stream was healthy, reset.
                count = 0
        else:
            count = 0

        if count >= self.MAX_RESTREAM_RETRIES:
            logger.warning(
                "Re-stream retry limit reached for player %s (%d/%d) — giving up",
                player_mac,
                count,
                self.MAX_RESTREAM_RETRIES,
            )
            return False

        count += 1
        self._restream_state[player_mac] = (count, now)
        logger.info(
            "Re-stream attempt %d/%d for player %s",
            count,
            self.MAX_RESTREAM_RETRIES,
            player_mac,
        )
        return True

    def clear_restream_state(self, player_mac: str) -> None:
        """Reset re-stream retry state for a player.

        Called when a genuinely new track starts (not a re-stream).
        """
        self._restream_state.pop(player_mac, None)

    def get_restream_retry_count(self, player_mac: str) -> int:
        """Return current retry count (0 if no retries recorded)."""
        state = self._restream_state.get(player_mac)
        return state[0] if state is not None else 0

    def is_remote_stream(self, player_mac: str) -> bool:
        """Return ``True`` if the pending stream for *player_mac* is a remote URL."""
        return player_mac in self._remote_urls

    def resolve_stream(self, player_mac: str | None) -> ResolvedStream:
        """Resolve what to stream for a player — local file **or** remote URL.

        This is the unified successor to :meth:`resolve_file`.  The streaming
        route should call this instead and branch on the result.

        Returns:
            A :class:`ResolvedStream` tuple.  Exactly one of its fields is
            populated; both may be ``None`` if nothing is queued.
        """
        if not player_mac:
            return ResolvedStream(file_path=None, remote=None)

        # Remote URL takes priority (set by queue_url).
        if player_mac in self._remote_urls:
            info = self._remote_urls[player_mac]
            logger.info(
                "resolve_stream: player=%s -> REMOTE URL: %s",
                player_mac,
                info.title or info.url,
            )
            return ResolvedStream(file_path=None, remote=info)

        # Fall back to local file resolution (existing logic).
        local = self.resolve_file(player_mac)
        return ResolvedStream(file_path=local, remote=None)

    def queue_file_with_crossfade_plan(
        self,
        player_mac: str,
        file_path: Path,
        plan: PreparedCrossfadePlan,
    ) -> None:
        """
        Queue a file and bind a server-side crossfade plan to the new generation.

        The plan is used by /stream.mp3 to render overlap audio for the first
        request of this queued generation.
        """
        self.cancel_stream(player_mac)

        gen = self._stream_generation.get(player_mac, 0) + 1
        self._stream_generation[player_mac] = gen
        self._stream_generation_started_at[player_mac] = time.monotonic()
        self._stream_tokens[player_mac] = CancellationToken(gen)

        self._stream_queue[player_mac] = file_path
        self._seek_positions.pop(player_mac, None)
        self._byte_offsets.pop(player_mac, None)
        self._start_offset.pop(player_mac, None)
        self._crossfade_plans[player_mac] = PendingCrossfade(generation=gen, plan=plan)

        logger.info(
            "Queued %s for player %s with server-side crossfade %.2fs (generation %d)",
            file_path.name,
            player_mac,
            plan.overlap_seconds,
            gen,
        )

    def get_crossfade_plan(
        self,
        player_mac: str,
        file_path: Path | None = None,
    ) -> PreparedCrossfadePlan | None:
        """
        Return a pending crossfade plan if it matches current stream generation.
        """
        pending = self._crossfade_plans.get(player_mac)
        if pending is None:
            return None

        current_gen = self._stream_generation.get(player_mac)
        if current_gen is None or current_gen != pending.generation:
            self._crossfade_plans.pop(player_mac, None)
            return None

        if file_path is not None and Path(pending.plan.next_path) != Path(file_path):
            return None

        return pending.plan

    def pop_crossfade_plan(
        self,
        player_mac: str,
        file_path: Path | None = None,
    ) -> PreparedCrossfadePlan | None:
        """Return and remove the pending crossfade plan for a player."""
        plan = self.get_crossfade_plan(player_mac, file_path=file_path)
        if plan is None:
            return None
        self._crossfade_plans.pop(player_mac, None)
        return plan

    def clear_crossfade_plan(self, player_mac: str) -> None:
        """Clear pending server-side crossfade plan for a player."""
        self._crossfade_plans.pop(player_mac, None)

    def queue_file_with_seek(
        self,
        player_mac: str,
        file_path: Path,
        start_seconds: float,
        end_seconds: float | None = None,
    ) -> None:
        """
        Queue an audio file with a seek position for transcoded streaming.

        This is used for M4B/M4A/MP4 files where we need to tell faad
        to start at a specific time position using -j and optionally -e.

        Args:
            player_mac: MAC address of the player.
            file_path: Path to the audio file to stream.
            start_seconds: Start position in seconds.
            end_seconds: Optional end position in seconds.
        """
        # Cancel any existing stream first (LMS-style closeStream)
        self.cancel_stream(player_mac)

        # Increment generation and create new token
        gen = self._stream_generation.get(player_mac, 0) + 1
        self._stream_generation[player_mac] = gen
        self._stream_generation_started_at[player_mac] = time.monotonic()
        self._stream_tokens[player_mac] = CancellationToken(gen)

        self._stream_queue[player_mac] = file_path
        self._seek_positions[player_mac] = (start_seconds, end_seconds)
        self._byte_offsets.pop(player_mac, None)  # Clear byte offset when using time-based seek
        self._crossfade_plans.pop(player_mac, None)

        # Record start offset (LMS-style) so status can calculate correct position.
        # After seek, player reports elapsed relative to stream start (0, 1, 2...).
        # Real position = start_offset + raw_elapsed (e.g., 30 + 0 = 30, 30 + 1 = 31...).
        self._start_offset[player_mac] = float(start_seconds)

        logger.info(
            "Queued %s for player %s with seek: start=%.1fs, end=%s (generation %d)",
            file_path.name,
            player_mac,
            start_seconds,
            f"{end_seconds:.1f}s" if end_seconds else "None",
            gen,
        )

    def get_seek_position(self, player_mac: str) -> tuple[float, float | None] | None:
        """
        Get the seek position for a player.

        Args:
            player_mac: MAC address of the player.

        Returns:
            Tuple of (start_seconds, end_seconds) or None if no seek position set.
        """
        return self._seek_positions.get(player_mac)

    def clear_seek_position(self, player_mac: str) -> None:
        """
        Clear the seek position for a player after streaming starts.

        Args:
            player_mac: MAC address of the player.
        """
        self._seek_positions.pop(player_mac, None)

    def get_start_offset(self, player_mac: str) -> float:
        """
        Get the start offset for a player (LMS-style startOffset).

        After a seek to position X, the player reports elapsed time relative to
        the stream start. The real track position is: start_offset + raw_elapsed.

        This mirrors LMS's song.startOffset() from StreamingController.pm:
            songtime = startStream + songtime

        Returns:
            Start offset in seconds, or 0.0 if no seek offset is active.
        """
        return self._start_offset.get(player_mac, 0.0)

    def clear_start_offset(self, player_mac: str) -> None:
        """Clear the start offset for a player (e.g., when track changes)."""
        self._start_offset.pop(player_mac, None)

    def queue_file_with_byte_offset(
        self,
        player_mac: str,
        file_path: Path,
        byte_offset: int,
        start_seconds: float = 0.0,
    ) -> None:
        """
        Queue an audio file with a byte offset for direct streaming.

        This is used for MP3/FLAC/OGG files where we can seek by byte position.
        The byte offset is calculated from the seek time and file properties.

        Args:
            player_mac: MAC address of the player.
            file_path: Path to the audio file to stream.
            byte_offset: Starting byte offset in the file.
            start_seconds: The seek target time in seconds (for LMS-style elapsed calculation).
                          After seek, elapsed = start_seconds + raw_elapsed from player.
        """
        # Cancel any existing stream first (LMS-style closeStream)
        self.cancel_stream(player_mac)

        # Increment generation and create new token
        gen = self._stream_generation.get(player_mac, 0) + 1
        self._stream_generation[player_mac] = gen
        self._stream_generation_started_at[player_mac] = time.monotonic()
        self._stream_tokens[player_mac] = CancellationToken(gen)

        self._stream_queue[player_mac] = file_path
        self._byte_offsets[player_mac] = byte_offset
        self._seek_positions.pop(player_mac, None)  # Clear time-based seek
        self._crossfade_plans.pop(player_mac, None)

        # Record start offset (LMS-style) so status can calculate correct position.
        # After seek, player reports elapsed relative to stream start (0, 1, 2...).
        # Real position = start_offset + raw_elapsed (same as time-based seeks).
        if start_seconds > 0:
            self._start_offset[player_mac] = float(start_seconds)
        else:
            self._start_offset.pop(player_mac, None)

        logger.info(
            "Queued %s for player %s with byte offset: %d, start_offset=%.1fs (generation %d)",
            file_path.name,
            player_mac,
            byte_offset,
            start_seconds,
            gen,
        )

    def get_byte_offset(self, player_mac: str) -> int | None:
        """
        Get the byte offset for a player.

        Args:
            player_mac: MAC address of the player.

        Returns:
            Byte offset or None if not set.
        """
        return self._byte_offsets.get(player_mac)

    def clear_byte_offset(self, player_mac: str) -> None:
        """
        Clear the byte offset for a player after streaming starts.

        Args:
            player_mac: MAC address of the player.
        """
        self._byte_offsets.pop(player_mac, None)

    def dequeue_file(self, player_mac: str) -> Path | None:
        """
        Remove and return the queued file for a player.

        Args:
            player_mac: MAC address of the player.

        Returns:
            The queued file path, or None if nothing was queued.
        """
        return self._stream_queue.pop(player_mac, None)

    def get_queued_file(self, player_mac: str) -> Path | None:
        """
        Get the queued file for a player without removing it.

        Args:
            player_mac: MAC address of the player.

        Returns:
            The queued file path, or None if nothing was queued.
        """
        return self._stream_queue.get(player_mac)

    async def start(self) -> None:
        """
        Mark the streaming server as running.

        NOTE: This no longer binds a socket. The actual HTTP endpoint
        is provided by FastAPI routes.
        """
        if self._running:
            logger.warning("Streaming server already running")
            return

        self._running = True
        logger.info("Streaming server ready (via FastAPI on port %d)", self.port)

    async def stop(self) -> None:
        """Stop the streaming server."""
        if not self._running:
            return

        self._running = False

        # Cancel all active streams
        for player_mac in list(self._stream_tokens.keys()):
            self.cancel_stream(player_mac)

        self._stream_queue.clear()
        self._remote_urls.clear()
        self._seek_positions.clear()
        self._byte_offsets.clear()
        self._stream_tokens.clear()
        self._stream_generation.clear()
        self._stream_generation_started_at.clear()
        self._runtime_config.clear()
        self._crossfade_plans.clear()
        logger.info("Streaming server stopped")

    def resolve_file(self, player_mac: str | None) -> Path | None:
        """
        Resolve the file to stream for a player.

        This checks:
        1. The direct queue (files queued via queue_file)
        2. The audio_provider callback (e.g., PlaylistManager)

        Args:
            player_mac: MAC address of the player.

        Returns:
            Path to the audio file, or None if not found.
        """
        if not player_mac:
            logger.debug("resolve_file: no player_mac provided")
            return None

        # First check the queue
        if player_mac in self._stream_queue:
            queued = self._stream_queue.get(player_mac)
            logger.info(
                "resolve_file: player=%s -> FROM QUEUE: %s",
                player_mac,
                queued.name if queued else None,
            )
            return queued

        # Then try the audio provider callback
        if self._audio_provider:
            from_provider = self._audio_provider(player_mac)
            logger.info(
                "resolve_file: player=%s -> FROM PROVIDER (playlist.current_track): %s",
                player_mac,
                from_provider.name if from_provider else None,
            )
            return from_provider

        logger.warning("resolve_file: player=%s -> NO FILE FOUND", player_mac)
        return None

    @staticmethod
    def get_content_type(file_path: Path) -> str:
        """Get MIME type for a file."""
        suffix = file_path.suffix.lower()

        # Common audio types
        audio_types = {
            ".mp3": "audio/mpeg",
            ".flac": "audio/flac",
            ".ogg": "audio/ogg",
            ".wav": "audio/wav",
            ".aac": "audio/aac",
            ".m4a": "audio/mp4",
            ".m4b": "audio/m4a",  # Audiobook format (AAC in MP4 container)
            ".wma": "audio/x-ms-wma",
            ".aif": "audio/aiff",
            ".aiff": "audio/aiff",
            ".opus": "audio/opus",
            ".wv": "audio/x-wavpack",
            ".ape": "audio/x-monkeys-audio",
            ".mpc": "audio/x-musepack",
        }

        if suffix in audio_types:
            return audio_types[suffix]

        # Fall back to mimetypes
        content_type, _ = mimetypes.guess_type(str(file_path))
        return content_type or "application/octet-stream"

    @staticmethod
    def parse_range_header(range_header: str | None, file_size: int) -> tuple[int, int]:
        """
        Parse HTTP Range header.

        Args:
            range_header: The Range header value (e.g., "bytes=0-1024").
            file_size: Total size of the file.

        Returns:
            Tuple of (start_byte, end_byte).
        """
        start_byte = 0
        end_byte = file_size - 1

        if range_header and range_header.startswith("bytes="):
            try:
                range_spec = range_header[6:]  # Remove "bytes="
                if "-" in range_spec:
                    start_str, end_str = range_spec.split("-", 1)
                    if start_str:
                        start_byte = int(start_str)
                    if end_str:
                        end_byte = int(end_str)
            except ValueError:
                pass

        # Clamp range
        start_byte = max(0, min(start_byte, file_size - 1))
        end_byte = max(start_byte, min(end_byte, file_size - 1))

        return start_byte, end_byte

    @property
    def is_running(self) -> bool:
        """Check if the server is running."""
        return self._running

    @property
    def buffer_size(self) -> int:
        """Get the buffer size for streaming."""
        return STREAM_BUFFER_SIZE
