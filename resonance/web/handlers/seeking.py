"""
Seeking Command Handlers.

Handles time/position control commands:
- time: Query or set playback position
- perform_seek: Execute a seek operation
- calculate_byte_offset: Calculate byte offset for direct-stream seeking

Direct-stream seeking follows LMS File.pm time->offset mapping for MP3/FLAC/OGG.
Transcoded seeking uses faad's -j/-e parameters for M4B/M4A.

This module integrates with SeekCoordinator for:
- Latest-wins semantics (only the most recent seek executes)
- Coalescing of rapid seek requests during user scrubbing
- Safe subprocess termination to prevent asyncio race conditions

IMPORTANT (LMS-compat / Race protection):
Seeking is a user-initiated manual action. Any pending/deferred "track finished"
timers (e.g. from early STMd deferral) must be cancelled/ignored, otherwise a
late deferred track-finished can incorrectly auto-advance the playlist to the
next track right after a seek.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from resonance.protocol.commands import FLAG_NO_RESTART_DECODER
from resonance.streaming.seek_coordinator import get_seek_coordinator
from resonance.web.handlers import CommandContext

logger = logging.getLogger(__name__)

# Formats that require transcoding for seeking (use time-based seeking)
TRANSCODE_SEEK_FORMATS = {".m4a", ".m4b", ".mp4", ".aac", ".alac"}


def _stream_flags_for_explicit_restart(flags: int) -> int:
    """
    Normalize STRM flags for manual STOP+FLUSH+START cycles.

    For seeks we explicitly restart the stream pipeline; keeping
    FLAG_NO_RESTART_DECODER here can leave decoder state stale on some players.
    """
    try:
        normalized = int(flags)
    except (TypeError, ValueError):
        normalized = 0
    return normalized & ~FLAG_NO_RESTART_DECODER


async def cmd_time(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """
    Handle 'time' command.

    Query or set the playback position.
    - time ? : Returns current position in seconds
    - time <seconds> : Seek to absolute position
    - time +<seconds> : Seek forward
    - time -<seconds> : Seek backward

    Seeks are coordinated through SeekCoordinator for latest-wins semantics.

    NOTE (LMS-style semantics):
    This handler must be fast. Seeking can involve cancelling streams, stopping/flushing,
    restarting transcode pipelines, etc. Waiting for the full seek execution here can
    cause JSON-RPC timeouts on clients.

    Therefore, we schedule the coordinated seek asynchronously ("fire-and-forget") and
    immediately acknowledge the target time. Clients will observe the seek via polling
    (`status`) and/or subsequent STAT updates.
    """
    if ctx.player_id == "-":
        return {"_time": 0}

    player = await ctx.player_registry.get_by_mac(ctx.player_id)
    if player is None:
        return {"_time": 0}

    # Query mode - return corrected elapsed (start_offset + raw)
    if len(params) < 2 or params[1] == "?":
        raw_elapsed = player.status.elapsed_seconds

        # Apply LMS-style start_offset correction (same as status.py/api.py)
        # After seek, player reports elapsed relative to stream start (0,1,2...)
        # Real position = start_offset + raw_elapsed
        start_offset: float = 0.0
        if ctx.streaming_server is not None:
            try:
                start_offset = ctx.streaming_server.get_start_offset(ctx.player_id)
            except Exception:
                start_offset = 0.0

        elapsed = start_offset + raw_elapsed
        return {"_time": elapsed}

    # Parse seek target
    time_str = str(params[1])
    current_time = player.status.elapsed_seconds

    try:
        if time_str.startswith("+"):
            # Relative forward
            delta = float(time_str[1:])
            target_time = current_time + delta
        elif time_str.startswith("-"):
            # Relative backward
            delta = float(time_str[1:])
            target_time = current_time - delta
        else:
            # Absolute position
            target_time = float(time_str)
    except (ValueError, TypeError):
        return {"error": f"Invalid time value: {time_str}"}

    # Clamp to valid range
    target_time = max(0.0, target_time)

    # Get current track duration for clamping
    duration = 0.0
    if ctx.playlist_manager is not None:
        playlist = ctx.playlist_manager.get(ctx.player_id)
        if playlist is not None:
            current_track = playlist.current_track
            if current_track is not None and current_track.duration_ms:
                duration = current_track.duration_ms / 1000.0
                # Clamp to duration minus 1 second to avoid EOF issues
                target_time = min(target_time, max(0, duration - 1.0))

    # Use SeekCoordinator for latest-wins semantics
    coordinator = get_seek_coordinator()

    async def seek_executor(seek_target: float) -> None:
        await _execute_seek_internal(ctx, player, seek_target)

    async def run_seek() -> None:
        try:
            await coordinator.seek(ctx.player_id, target_time, seek_executor)
        except Exception:
            logger.exception(
                "Background seek failed for player %s (target=%.3fs)",
                ctx.player_id,
                target_time,
            )

    asyncio.create_task(run_seek())

    return {"_time": target_time}


async def perform_seek(
    ctx: CommandContext,
    player: Any,
    target_seconds: float,
) -> None:
    """
    Execute a seek operation through the SeekCoordinator.

    This is the public API for seek operations. It uses the SeekCoordinator
    for latest-wins semantics, ensuring that rapid seeks don't overwhelm
    the server with transcode pipeline restarts.

    For transcoded formats (M4B/M4A), uses time-based seeking via faad -j/-e.
    For direct-stream formats (MP3/FLAC/OGG), uses byte offset seeking.
    """
    coordinator = get_seek_coordinator()

    async def seek_executor(seek_target: float) -> None:
        await _execute_seek_internal(ctx, player, seek_target)

    await coordinator.seek(ctx.player_id, target_seconds, seek_executor)


async def _execute_seek_internal(
    ctx: CommandContext,
    player: Any,
    target_seconds: float,
) -> None:
    """
    Internal seek execution logic.

    This is called by the SeekCoordinator after coalescing and
    generation checks. It performs the actual seek work:
    1. Cancel current stream
    2. Stop and flush player
    3. Queue new stream with seek position
    4. Start playback from new position

    This function should NOT be called directly - use perform_seek() instead.
    """
    if ctx.playlist_manager is None or ctx.streaming_server is None:
        return

    # ---------------------------------------------------------------------
    # Race protection: seeking is a manual user action.
    #
    # If the protocol layer scheduled a deferred "track finished" (e.g. STMd
    # deferral because output buffer is still playing), that task MUST NOT be
    # allowed to fire after a seek, otherwise it can incorrectly advance the
    # playlist to track +1.
    #
    # We therefore:
    # 1) cancel any pending deferred track-finished task for this player (best-effort)
    # 2) suppress track-finished handling briefly (best-effort)
    # ---------------------------------------------------------------------
    try:
        slimproto = getattr(ctx, "slimproto", None)
        if slimproto is not None:
            cancel_fn = getattr(slimproto, "cancel_deferred_track_finished", None)
            if callable(cancel_fn):
                cancel_fn(ctx.player_id)

            # Suppress track-finished handling for a short window
            server = getattr(slimproto, "_resonance_server", None)
            if server is not None:
                suppress_fn = getattr(server, "suppress_track_finished_for_player", None)
                if callable(suppress_fn):
                    suppress_fn(ctx.player_id, seconds=2.0)
    except Exception:
        # Defensive: seek must still work even if suppression hooks are unavailable
        pass

    playlist = ctx.playlist_manager.get(ctx.player_id)
    if playlist is None:
        return

    current_track = playlist.current_track
    if current_track is None:
        return

    file_path = Path(current_track.path)
    suffix = file_path.suffix.lower()

    state = getattr(getattr(player, "status", None), "state", None)
    state_name = str(getattr(state, "name", state)).upper() if state is not None else ""
    is_currently_playing = state_name == "PLAYING"

    runtime_params = await ctx.streaming_server.resolve_runtime_stream_params(
        ctx.player_id,
        track=current_track,
        playlist=playlist,
        allow_transition=False,
        is_currently_playing=is_currently_playing,
    )

    # Stop and flush player
    await player.stop()
    if hasattr(player, "flush"):
        await player.flush()

    if suffix in TRANSCODE_SEEK_FORMATS:
        # Time-based seeking for transcoded formats
        #
        # IMPORTANT: Do NOT pass end_seconds for regular seeks!
        # LMS only substitutes $END$ for cuesheets (where the end position
        # comes from the track path "#start-end").  For normal seeks, $END$
        # is left unsubstituted and cleaned up.  The LMS-patched faad
        # interprets -e as a duration (not absolute position), so passing
        # the full track duration here produces garbage output that the
        # player cannot decode.
        ctx.streaming_server.queue_file_with_seek(
            ctx.player_id,
            file_path,
            start_seconds=target_seconds,
            end_seconds=None,
        )
    else:
        # Byte offset seeking for direct-stream formats
        duration_ms = current_track.duration_ms
        byte_offset = calculate_byte_offset(
            file_path=file_path,
            target_seconds=target_seconds,
            duration_ms=duration_ms,
        )

        # Byte-offset seeking: pass start_seconds for LMS-style elapsed calculation.
        # After seek, elapsed = start_seconds + raw_elapsed (same as time-based seeks).
        ctx.streaming_server.queue_file_with_byte_offset(
            ctx.player_id,
            file_path,
            byte_offset=byte_offset,
            start_seconds=target_seconds,
        )

    # Get server IP for player
    server_ip = ctx.server_host
    if ctx.slimproto is not None and hasattr(ctx.slimproto, "get_advertise_ip_for_player"):
        server_ip = ctx.slimproto.get_advertise_ip_for_player(player)

    # Ensure audio outputs are enabled before setting gain/starting stream.
    if hasattr(player, "set_audio_enable"):
        await player.set_audio_enable(True)

    # CRITICAL: Set volume BEFORE stream start (audg must precede strm).
    # Without this, Squeezebox Radio (and other hardware) stays SILENT after
    # a seek because the player needs an explicit audg frame before every
    # strm/s.  This matches _start_track_stream() and Fallstrick #11.
    current_volume = getattr(player.status, "volume", 100)
    current_muted = getattr(player.status, "muted", False)
    await player.set_volume(current_volume, current_muted)

    # Start streaming from new position
    await player.start_track(
        current_track,
        server_port=ctx.server_port,
        server_ip=server_ip,
        transition_duration=runtime_params.transition_duration,
        transition_type=runtime_params.transition_type,
        stream_flags=_stream_flags_for_explicit_restart(runtime_params.flags),
        replay_gain=runtime_params.replay_gain,
    )


def calculate_byte_offset(
    file_path: Path,
    target_seconds: float,
    duration_ms: int | None = None,
) -> int:
    """
    Calculate byte offset for direct-stream seek (LMS File.pm style).

    Mirrors Slim::Player::Protocols::File::_timeToOffset:
    - clamp to song boundaries
    - derive byte rate from stream_size / duration
    - align by block size when frame-boundary helpers are unavailable
    """
    try:
        file_size = file_path.stat().st_size
    except OSError:
        return 0

    if file_size <= 0:
        return 0

    suffix = file_path.suffix.lower().lstrip(".")

    # Approximate LMS song->offset for MP3 by skipping leading ID3v2 metadata.
    audio_start = _get_mp3_audio_start(file_path) if suffix == "mp3" else 0

    if target_seconds <= 0:
        return max(0, min(audio_start, file_size))

    # LMS _timeToOffset relies on known duration; no guessed bytes/sec fallback.
    if not duration_ms or duration_ms <= 0:
        return max(0, min(audio_start, file_size))

    duration_seconds = duration_ms / 1000.0
    if duration_seconds <= 0:
        return max(0, min(audio_start, file_size))

    if target_seconds >= duration_seconds:
        return file_size

    stream_size = max(0, file_size - audio_start)
    byterate = stream_size / duration_seconds
    seek_in_stream = int(byterate * target_seconds)

    # LMS falls back to block alignment when no frame-boundary helper is used.
    block_align = _get_block_align(suffix)
    if block_align > 1:
        seek_in_stream -= seek_in_stream % block_align

    byte_offset = audio_start + seek_in_stream
    return max(audio_start, min(byte_offset, file_size))


def _get_block_align(suffix: str) -> int:
    """Return conservative byte alignment for direct-stream formats."""
    if suffix in {"wav", "aiff", "aif"}:
        # 16-bit stereo PCM common case; safe alignment fallback.
        return 4
    return 1


def _get_mp3_audio_start(file_path: Path) -> int:
    """
    Find the start of audio data in an MP3 file by skipping ID3v2 tags.

    ID3v2 tags are at the start of the file and have a specific header:
    - 3 bytes: "ID3"
    - 2 bytes: version
    - 1 byte: flags
    - 4 bytes: synchsafe size

    Returns:
        Byte offset where audio data starts
    """
    try:
        with open(file_path, "rb") as f:
            header = f.read(10)

            if len(header) < 10:
                return 0

            # Check for ID3v2 header
            if header[:3] != b"ID3":
                return 0

            # Parse synchsafe size (4 bytes, 7 bits each)
            size_bytes = header[6:10]
            tag_size = (
                ((size_bytes[0] & 0x7F) << 21)
                | ((size_bytes[1] & 0x7F) << 14)
                | ((size_bytes[2] & 0x7F) << 7)
                | (size_bytes[3] & 0x7F)
            )

            # ID3v2 header is 10 bytes + tag data
            return 10 + tag_size

    except OSError:
        return 0
