"""
Playlist Playback Handlers.

Handles playback-related playlist sub-commands:

- ``playlist play``   — play a track by index or start playback
- ``playlist pause``  — alias for top-level ``pause``
- ``playlist stop``   — alias for top-level ``stop``
- ``playlist index``  — jump to a track by absolute/relative index
- ``playlist jump``   — relative navigation (+1 / -1)

Also contains the shared ``_start_track_stream`` helper used by mutation
and persistence sub-modules, and the prefetch fast-path logic.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from resonance.core.events import PlayerPlaylistEvent, event_bus
from resonance.web.handlers import CommandContext
from resonance.web.handlers.playback import cmd_pause, cmd_stop
from resonance.web.handlers.playlist_helpers import (
    _stream_flags_for_explicit_restart,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Playback sub-commands
# =============================================================================


async def _playlist_play(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """
    Handle 'playlist play' - play a track by index or start playback.
    """
    if ctx.player_id == "-":
        return {"error": "No player specified"}

    player = await ctx.player_registry.get_by_mac(ctx.player_id)
    if player is None:
        return {"error": "Player not found"}

    if ctx.playlist_manager is None:
        logger.warning("playlist_manager not available")
        return {"error": "Playlist manager not available"}

    playlist = ctx.playlist_manager.get(ctx.player_id)
    if playlist is None:
        return {"error": "No playlist for player"}

    # Get track index if provided
    if len(params) >= 3:
        try:
            index = int(params[2])
            track = playlist.play(index)
        except (ValueError, TypeError):
            # Not an index, might be a track ID or path
            track = playlist.current_track
    else:
        track = playlist.current_track

    if track is None:
        return {"error": "No track to play"}

    # Start track stream
    await _start_track_stream(ctx, player, track)

    return {}


async def _playlist_pause(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """Handle 'playlist pause' as an alias of top-level pause."""
    mapped: list[Any] = ["pause"]
    if len(params) >= 3:
        mapped.append(params[2])
    return await cmd_pause(ctx, mapped)


async def _playlist_stop(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """Handle 'playlist stop' as an alias of top-level stop."""
    return await cmd_stop(ctx, ["stop"])


async def _playlist_index(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """
    Handle 'playlist index' - jump to a track by index.

    Supports:
    - playlist index ? : Query current index
    - playlist index <n> : Jump to absolute index
    - playlist index +1 : Next track
    - playlist index -1 : Previous track
    """
    if ctx.player_id == "-":
        return {"error": "No player specified"}

    if ctx.playlist_manager is None:
        return {"error": "Playlist manager not available"}

    playlist = ctx.playlist_manager.get(ctx.player_id)
    if playlist is None:
        return {"_index": 0}

    # Query mode
    if len(params) < 3 or params[2] == "?":
        return {"_index": playlist.current_index}

    player = await ctx.player_registry.get_by_mac(ctx.player_id)

    # Handle relative indices (+1, -1)
    index_str = str(params[2])
    if index_str == "+1":
        used_prefetch = await _try_use_prefetch_fast_path(
            ctx,
            playlist,
            caller="playlist index",
        )
        if not used_prefetch:
            track = playlist.next()
            if track is not None and player is not None:
                await _start_track_stream(ctx, player, track)
        return {"_index": playlist.current_index}
    elif index_str == "-1":
        track = playlist.previous()
        if track is not None and player is not None:
            await _start_track_stream(ctx, player, track)
        return {"_index": playlist.current_index}

    # Absolute index
    try:
        index = int(index_str)
        track = playlist.play(index)
        if track is not None and player is not None:
            await _start_track_stream(ctx, player, track)
        return {"_index": playlist.current_index}
    except (ValueError, TypeError):
        return {"error": f"Invalid index: {index_str}"}


async def _playlist_jump(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """
    Handle 'playlist jump' - relative navigation.

    - playlist jump +1 : Next track
    - playlist jump -1 : Previous track
    """
    if ctx.player_id == "-":
        return {"error": "No player specified"}

    if ctx.playlist_manager is None:
        return {"error": "Playlist manager not available"}

    playlist = ctx.playlist_manager.get(ctx.player_id)
    if playlist is None:
        return {"error": "No playlist for player"}

    player = await ctx.player_registry.get_by_mac(ctx.player_id)
    if player is None:
        return {"error": "Player not found"}

    if len(params) < 3:
        return {"error": "Missing jump direction"}

    direction = str(params[2])
    track = None

    if direction in ("+1", "1"):
        used_prefetch = await _try_use_prefetch_fast_path(
            ctx,
            playlist,
            caller="playlist jump",
        )
        if not used_prefetch:
            track = playlist.next()
    elif direction == "-1":
        track = playlist.previous()
    else:
        return {"error": f"Invalid jump direction: {direction}"}

    if track is not None:
        await _start_track_stream(ctx, player, track)

    return {"_index": playlist.current_index}


# =============================================================================
# Shared helpers
# =============================================================================


async def _try_use_prefetch_fast_path(
    ctx: CommandContext,
    playlist: Any,
    *,
    caller: str,
) -> bool:
    """Advance via prefetch without STOP+FLUSH+restart when the next stream is ready."""
    if ctx.streaming_server is None or ctx.slimproto is None:
        return False

    server = getattr(ctx.slimproto, "_resonance_server", None)
    if server is None:
        return False

    prefetch_generation = getattr(server, "_prefetched_generation", None)
    suppress = getattr(server, "suppress_track_finished_for_player", None)
    if not isinstance(prefetch_generation, dict) or not callable(suppress):
        return False

    current_gen = ctx.streaming_server.get_stream_generation(ctx.player_id)
    prefetched_gen = prefetch_generation.get(ctx.player_id)
    if current_gen is None or prefetched_gen is None or prefetched_gen != current_gen:
        return False

    track = playlist.next()
    if track is None:
        return False

    prefetch_generation.pop(ctx.player_id, None)
    suppress(ctx.player_id, seconds=4.0)
    # Keep generation marker so the later STMu still takes the fast path.
    prefetch_generation[ctx.player_id] = current_gen

    logger.info(
        "%s +1: using prefetch fast path for player %s (gen=%s, track=%s)",
        caller,
        ctx.player_id,
        current_gen,
        track.title,
    )

    await event_bus.publish(
        PlayerPlaylistEvent(
            player_id=ctx.player_id,
            action="index",
            index=playlist.current_index,
            count=len(playlist),
        )
    )
    return True


async def _start_track_stream(
    ctx: CommandContext,
    player: Any,
    track: Any,
) -> None:
    """
    Start streaming a track to a player.

    Handles:
    1. Stream cancellation (stop old stream)
    2. Player stop/flush
    3. Set volume (audg must be sent before strm!)
    4. Queue new file
    5. Start new stream
    """
    if ctx.streaming_server is None or ctx.slimproto is None:
        return

    # Suppress track-finished for a short window to prevent race conditions
    if hasattr(ctx.slimproto, "_resonance_server") and hasattr(
        ctx.slimproto._resonance_server, "suppress_track_finished_for_player"
    ):
        ctx.slimproto._resonance_server.suppress_track_finished_for_player(ctx.player_id, seconds=6.0)

    playlist = ctx.playlist_manager.get(ctx.player_id) if ctx.playlist_manager is not None else None

    state = getattr(getattr(player, "status", None), "state", None)
    state_name = str(getattr(state, "name", state)).upper() if state is not None else ""
    is_currently_playing = state_name == "PLAYING"

    runtime_params = await ctx.streaming_server.resolve_runtime_stream_params(
        ctx.player_id,
        track=track,
        playlist=playlist,
        allow_transition=True,
        is_currently_playing=is_currently_playing,
    )

    # Cancel any existing stream
    ctx.streaming_server.cancel_stream(ctx.player_id)

    # Stop and flush player buffer
    await player.stop()
    if hasattr(player, "flush"):
        await player.flush()

    # Ensure audio outputs are enabled before setting gain/starting stream.
    if hasattr(player, "set_audio_enable"):
        await player.set_audio_enable(True)

    # CRITICAL: Set volume before stream start!
    # The player needs an audg command before strm, otherwise audio may be silent.
    # Use current volume from player status, default to 100 if not set.
    current_volume = getattr(player.status, "volume", 100)
    current_muted = getattr(player.status, "muted", False)
    await player.set_volume(current_volume, current_muted)

    # Queue the track — remote URL or local file
    if getattr(track, "is_remote", False):
        ctx.streaming_server.queue_url(
            ctx.player_id,
            getattr(track, "effective_stream_url", track.path),
            content_type=getattr(track, "content_type", None) or "audio/mpeg",
            is_live=getattr(track, "is_live", False),
            title=track.title or track.path,
        )
    else:
        ctx.streaming_server.queue_file(ctx.player_id, Path(track.path))

    # Store track duration for server-side track-end detection.
    # Controller-class players with transitionType=0 never send STMd/STMu,
    # so the server must detect track end from stream age vs duration.
    _duration_ms = getattr(track, "duration_ms", None) or 0
    if _duration_ms > 0:
        ctx.streaming_server.set_track_duration(ctx.player_id, float(_duration_ms) / 1000.0)

    # Get server IP for player
    server_ip = ctx.server_host
    if hasattr(ctx.slimproto, "get_advertise_ip_for_player"):
        server_ip = ctx.slimproto.get_advertise_ip_for_player(player)

    # Start streaming
    await player.start_track(
        track,
        server_port=ctx.server_port,
        server_ip=server_ip,
        transition_duration=runtime_params.transition_duration,
        transition_type=runtime_params.transition_type,
        stream_flags=_stream_flags_for_explicit_restart(runtime_params.flags),
        replay_gain=runtime_params.replay_gain,
    )
