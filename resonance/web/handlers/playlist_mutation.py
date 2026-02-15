"""
Playlist Mutation Handlers.

Handles playlist sub-commands that modify the queue:

- ``playlist add`` / ``playlist append``  — append a track
- ``playlist insert``                      — insert a track at a position
- ``playlist insertlist``                  — insert via filter resolution
- ``playlist delete``                      — remove a track by index
- ``playlist deleteitem``                  — remove by index or path
- ``playlist clear``                       — clear the entire queue
- ``playlist move``                        — move a track to a new position
- ``playlist shuffle``                     — toggle or set shuffle mode
- ``playlist repeat``                      — set repeat mode
- ``playlist addtracks``                   — append all resolved tracks
- ``playlist inserttracks``                — insert resolved tracks after current
- ``playlist loadtracks`` / ``playtracks`` — stop+clear+load+play
- ``playlist loadalbum`` / ``playalbum``   — album alias for loadtracks
- ``playlist addalbum``                    — album alias for addtracks
- ``playlist insertalbum``                 — album alias for inserttracks
- ``playlist deletetracks``                — remove tracks matching filters
- ``playlist deletealbum``                 — album alias for deletetracks
- ``playlist zap``                         — remove current (or specified) track
"""

from __future__ import annotations

import logging
from typing import Any

from resonance.core.events import PlayerPlaylistEvent, event_bus
from resonance.web.handlers import CommandContext
from resonance.web.handlers.playlist_helpers import (
    _ZAPPED_TRACKS,
    _build_album_alias_command,
    _first_non_tag_param,
    _is_truthy_tag,
    _m3u_path_for_name,
    _normalize_saved_playlist_name,
    _parse_playlist_kv_params,
    _resolve_track,
    _resolve_track_rows_from_filters,
    _row_to_library_track,
    load_saved_playlist_tracks,
)
from resonance.web.handlers.playlist_playback import _start_track_stream
from resonance.web.jsonrpc_helpers import (
    get_filter_int,
    parse_start_items,
    parse_tagged_params,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Single-track mutation commands
# =============================================================================


async def _playlist_add(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """
    Handle 'playlist add' - add tracks to the playlist.
    """
    if ctx.player_id == "-":
        return {"error": "No player specified"}

    if ctx.playlist_manager is None:
        logger.warning("playlist_manager not available")
        return {"error": "Playlist manager not available"}

    playlist = ctx.playlist_manager.get(ctx.player_id)

    # Get track ID or path from params
    if len(params) < 3:
        return {"error": "Missing track to add"}

    track_ref = params[2]
    tagged_params = parse_tagged_params(params[3:])

    # Try to resolve track from database
    track = await _resolve_track(ctx, track_ref, tagged_params)
    if track is None:
        return {"error": f"Track not found: {track_ref}"}

    playlist.add(track)

    return {"count": len(playlist)}


async def _playlist_insert(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """
    Handle 'playlist insert' - insert tracks at a position.
    """
    if ctx.player_id == "-":
        return {"error": "No player specified"}

    if ctx.playlist_manager is None:
        return {"error": "Playlist manager not available"}

    playlist = ctx.playlist_manager.get(ctx.player_id)

    if len(params) < 3:
        return {"error": "Missing track to insert"}

    track_ref = params[2]
    tagged_params = parse_tagged_params(params[3:])

    # Get position (default to current index + 1)
    position = get_filter_int(tagged_params, "position")
    if position is None:
        position = playlist.current_index + 1

    track = await _resolve_track(ctx, track_ref, tagged_params)
    if track is None:
        return {"error": f"Track not found: {track_ref}"}

    playlist.insert(position, track)

    return {"count": len(playlist)}


async def _playlist_insertlist(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """Handle 'playlist insertlist' via inserttracks/insert fallback."""
    _, error = await _resolve_track_rows_from_filters(
        ctx,
        params,
        start_index=2,
        numeric_fallback_key="track_id",
    )
    if error is None:
        mapped = ["playlist", "inserttracks", *params[2:]]
        return await _playlist_inserttracks(ctx, mapped)

    mapped = ["playlist", "insert", *params[2:]]
    return await _playlist_insert(ctx, mapped)


async def _playlist_delete(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """
    Handle 'playlist delete' - remove tracks from the playlist.
    """
    if ctx.player_id == "-":
        return {"error": "No player specified"}

    if ctx.playlist_manager is None:
        return {"error": "Playlist manager not available"}

    playlist = ctx.playlist_manager.get(ctx.player_id)
    if playlist is None:
        return {"error": "No playlist for player"}

    if len(params) < 3:
        return {"error": "Missing index to delete"}

    try:
        index = int(params[2])
        playlist.remove(index)

        # Publish playlist event so Cometd/Web-UI see the change immediately.
        await event_bus.publish(
            PlayerPlaylistEvent(
                player_id=ctx.player_id,
                action="delete",
                count=len(playlist),
            )
        )

        return {"count": len(playlist)}
    except (ValueError, TypeError, IndexError) as e:
        return {"error": str(e)}


async def _playlist_deleteitem(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """Handle 'playlist deleteitem' - remove by queue index or item path."""
    if ctx.player_id == "-":
        return {"error": "No player specified"}

    if ctx.playlist_manager is None:
        return {"error": "Playlist manager not available"}

    if len(params) < 3:
        return {"error": "Missing item to delete"}

    playlist = ctx.playlist_manager.get(ctx.player_id)

    item = params[2]
    if isinstance(item, (int, float)):
        deleted = playlist.remove(int(item))
        if deleted is not None:
            await event_bus.publish(
                PlayerPlaylistEvent(
                    player_id=ctx.player_id,
                    action="delete",
                    count=len(playlist),
                )
            )
        return {"count": len(playlist)}

    item_str = str(item).strip()
    if not item_str:
        return {"error": "Missing item to delete"}

    try:
        index = int(item_str)
        deleted = playlist.remove(index)
        if deleted is not None:
            await event_bus.publish(
                PlayerPlaylistEvent(
                    player_id=ctx.player_id,
                    action="delete",
                    count=len(playlist),
                )
            )
        return {"count": len(playlist)}
    except ValueError:
        pass

    remove_indices = [
        idx
        for idx, track in enumerate(playlist.tracks)
        if str(getattr(track, "path", "")) == item_str
    ]

    for idx in reversed(remove_indices):
        playlist.remove(idx)

    if remove_indices:
        await event_bus.publish(
            PlayerPlaylistEvent(
                player_id=ctx.player_id,
                action="delete",
                count=len(playlist),
            )
        )

    return {"count": len(playlist)}


async def _playlist_clear(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """
    Handle 'playlist clear' - clear the playlist.
    """
    if ctx.player_id == "-":
        return {"error": "No player specified"}

    # Suppress track-finished for a short window to prevent race conditions
    if hasattr(ctx.slimproto, "_resonance_server") and hasattr(
        ctx.slimproto._resonance_server, "suppress_track_finished_for_player"
    ):
        ctx.slimproto._resonance_server.suppress_track_finished_for_player(ctx.player_id, seconds=6.0)

    if ctx.playlist_manager is None:
        return {"error": "Playlist manager not available"}

    playlist = ctx.playlist_manager.get(ctx.player_id)
    if playlist is not None:
        playlist.clear()

    # Clearing the playlist should stop playback (LMS-like) and cancel any active stream.
    # Otherwise the player may continue playing buffered audio / stale stream.
    if ctx.streaming_server is not None:
        ctx.streaming_server.cancel_stream(ctx.player_id)

    player = await ctx.player_registry.get_by_mac(ctx.player_id)
    if player is not None:
        await player.stop()
        if hasattr(player, "flush"):
            await player.flush()

    return {"count": 0}


async def _playlist_move(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """
    Handle 'playlist move' - move a track to a new position.
    """
    if ctx.player_id == "-":
        return {"error": "No player specified"}

    if ctx.playlist_manager is None:
        return {"error": "Playlist manager not available"}

    playlist = ctx.playlist_manager.get(ctx.player_id)
    if playlist is None:
        return {"error": "No playlist for player"}

    if len(params) < 4:
        return {"error": "Missing from and to indices"}

    try:
        from_idx = int(params[2])
        to_idx = int(params[3])
        playlist.move(from_idx, to_idx)
        return {"count": len(playlist)}
    except (ValueError, TypeError, IndexError) as e:
        return {"error": str(e)}


# =============================================================================
# Shuffle / repeat
# =============================================================================


async def _playlist_shuffle(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """
    Handle 'playlist shuffle' - toggle or set shuffle mode.
    """
    if ctx.player_id == "-":
        return {"error": "No player specified"}

    if ctx.playlist_manager is None:
        return {"error": "Playlist manager not available"}

    playlist = ctx.playlist_manager.get(ctx.player_id)
    if playlist is None:
        return {"_shuffle": 0}

    # Query mode
    if len(params) < 3 or params[2] == "?":
        return {"_shuffle": playlist.shuffle_mode.value}

    # Set mode
    try:
        value = int(params[2])
        playlist.set_shuffle(value)
        return {"_shuffle": playlist.shuffle_mode.value}
    except (ValueError, TypeError):
        # Toggle
        new_value = 0 if playlist.shuffle_mode.value else 1
        playlist.set_shuffle(new_value)
        return {"_shuffle": playlist.shuffle_mode.value}


async def _playlist_repeat(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """
    Handle 'playlist repeat' - set repeat mode.

    Values:
    - 0: Off
    - 1: Repeat one
    - 2: Repeat all
    """
    if ctx.player_id == "-":
        return {"error": "No player specified"}

    if ctx.playlist_manager is None:
        return {"error": "Playlist manager not available"}

    playlist = ctx.playlist_manager.get(ctx.player_id)
    if playlist is None:
        return {"_repeat": 0}

    # Query mode
    if len(params) < 3 or params[2] == "?":
        return {"_repeat": playlist.repeat_mode.value}

    # Set mode
    try:
        value = int(params[2])
        playlist.set_repeat(value)
        return {"_repeat": playlist.repeat_mode.value}
    except (ValueError, TypeError):
        return {"error": f"Invalid repeat value: {params[2]}"}


# =============================================================================
# Bulk-track operations
# =============================================================================


async def _playlist_addtracks(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """Handle 'playlist addtracks' - append all resolved tracks."""
    if ctx.player_id == "-":
        return {"error": "No player specified"}

    if ctx.playlist_manager is None:
        return {"error": "Playlist manager not available"}

    rows, error = await _resolve_track_rows_from_filters(
        ctx,
        params,
        start_index=2,
        numeric_fallback_key="track_id",
    )
    if error is not None:
        return {"error": error}

    playlist = ctx.playlist_manager.get(ctx.player_id)
    for row in rows or []:
        playlist.add(_row_to_library_track(row))

    return {"count": len(playlist)}


async def _playlist_inserttracks(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """Handle 'playlist inserttracks' - insert resolved tracks after current."""
    if ctx.player_id == "-":
        return {"error": "No player specified"}

    if ctx.playlist_manager is None:
        return {"error": "Playlist manager not available"}

    rows, error = await _resolve_track_rows_from_filters(
        ctx,
        params,
        start_index=2,
        numeric_fallback_key="track_id",
    )
    if error is not None:
        return {"error": error}

    tagged_params = _parse_playlist_kv_params(params, start_index=2)
    playlist = ctx.playlist_manager.get(ctx.player_id)

    position = get_filter_int(tagged_params, "position")
    if position is None:
        position = playlist.current_index + 1

    for row in rows or []:
        playlist.add(_row_to_library_track(row), position=position)
        position += 1

    return {"count": len(playlist)}


async def _playlist_loadtracks(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """
    Handle 'playlist loadtracks' - load tracks into playlist.

    LMS compatibility:
    - stop+clear current playlist/stream first
    - load tracks
    - then jump to index 0 to start playback (like LMS does via playlist jump)

    Supports ``playlist_name:<name>`` to load a saved M3U playlist.
    """
    if ctx.player_id == "-":
        return {"error": "No player specified"}

    # Suppress track-finished for a short window to prevent race conditions
    if hasattr(ctx.slimproto, "_resonance_server") and hasattr(
        ctx.slimproto._resonance_server, "suppress_track_finished_for_player"
    ):
        ctx.slimproto._resonance_server.suppress_track_finished_for_player(ctx.player_id, seconds=6.0)

    if ctx.playlist_manager is None:
        return {"error": "Playlist manager not available"}

    # Stop + clear (LMS-like): prevents buffered/stale audio and reduces races.
    playlist = ctx.playlist_manager.get(ctx.player_id)
    playlist.clear()

    if ctx.streaming_server is not None:
        ctx.streaming_server.cancel_stream(ctx.player_id)

    player = await ctx.player_registry.get_by_mac(ctx.player_id)
    if player is not None:
        await player.stop()
        if hasattr(player, "flush"):
            await player.flush()

    # Parse track criteria from params
    tagged_params = _parse_playlist_kv_params(params, start_index=2)

    # LMS: play_index tells the server to start playback at a specific track
    # within the loaded set (e.g. user clicked play on track 3 in an album view).
    play_index = get_filter_int(tagged_params, "play_index")

    # ---- Load from saved M3U playlist by name ----
    saved_name = tagged_params.get("playlist_name", "")
    if saved_name:
        from resonance.core.playlist import AlbumId, ArtistId, PlaylistTrack, TrackId

        m3u_entries = load_saved_playlist_tracks(saved_name)
        if m3u_entries is None:
            return {"error": f"Saved playlist not found: {saved_name}"}

        for entry in m3u_entries:
            playlist.add(PlaylistTrack(
                track_id=None,
                path=entry.path,
                title=entry.title,
                artist=entry.artist,
                album=entry.album,
                duration_ms=entry.duration_seconds * 1000 if entry.duration_seconds > 0 else 0,
            ))
    else:
        # ---- Standard DB-based track resolution ----
        rows, error = await _resolve_track_rows_from_filters(
            ctx,
            params,
            start_index=2,
            numeric_fallback_key="track_id",
        )
        if error is not None:
            return {"error": error}

        for row in rows or []:
            playlist.add(_row_to_library_track(row))

    # Start playback at the requested index (LMS does this via playlist jump).
    # play_index comes from the Jive "playallParams" when a user clicks play
    # on a specific track within an album view.  Default to 0 (first track).
    start_index = play_index if play_index is not None and 0 <= play_index < len(playlist) else 0
    if player is not None and len(playlist) > 0:
        start_track = playlist.play(start_index)
        if start_track is not None:
            await _start_track_stream(ctx, player, start_track)

    # Publish playlist event so Cometd re-executes slim subscriptions immediately.
    # Without this, the Radio only gets updated when the player sends STMs (track started),
    # which can be delayed by buffering.  The event triggers re-execution of the stored
    # "status - 10 menu:menu useContextMenu:1" command, delivering item_loop to the Radio.
    await event_bus.publish(
        PlayerPlaylistEvent(
            player_id=ctx.player_id,
            action="load",
            count=len(playlist),
        )
    )

    return {"count": len(playlist)}


# =============================================================================
# Album aliases
# =============================================================================


async def _playlist_loadalbum(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """Handle 'playlist loadalbum' via the loadtracks implementation."""
    mapped = _build_album_alias_command(params, target_subcommand="loadtracks")
    return await _playlist_loadtracks(ctx, mapped)


async def _playlist_playalbum(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """Handle 'playlist playalbum' as an alias of loadalbum/loadtracks."""
    mapped = _build_album_alias_command(params, target_subcommand="loadtracks")
    return await _playlist_loadtracks(ctx, mapped)


async def _playlist_addalbum(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """Handle 'playlist addalbum' via addtracks."""
    mapped = _build_album_alias_command(params, target_subcommand="addtracks")
    return await _playlist_addtracks(ctx, mapped)


async def _playlist_insertalbum(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """Handle 'playlist insertalbum' via inserttracks."""
    mapped = _build_album_alias_command(params, target_subcommand="inserttracks")
    return await _playlist_inserttracks(ctx, mapped)


# =============================================================================
# Filter-based delete
# =============================================================================


async def _playlist_deletetracks(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """Handle 'playlist deletetracks' - remove queued tracks matching filters."""
    if ctx.player_id == "-":
        return {"error": "No player specified"}

    if ctx.playlist_manager is None:
        return {"error": "Playlist manager not available"}

    rows, error = await _resolve_track_rows_from_filters(
        ctx,
        params,
        start_index=2,
        numeric_fallback_key="track_id",
    )
    if error is not None:
        return {"error": error}

    playlist = ctx.playlist_manager.get(ctx.player_id)
    target_ids: set[int] = set()
    target_paths: set[str] = set()

    for row in rows or []:
        if row.id is not None:
            try:
                target_ids.add(int(row.id))
            except (TypeError, ValueError):
                pass
        if row.path:
            target_paths.add(str(row.path))

    if not target_ids and not target_paths:
        return {"count": len(playlist)}

    remove_indices: list[int] = []
    for index, track in enumerate(playlist.tracks):
        track_id = getattr(track, "id", None)
        if track_id is None:
            track_id = getattr(track, "track_id", None)

        try:
            normalized_track_id = int(track_id) if track_id is not None else None
        except (TypeError, ValueError):
            normalized_track_id = None

        track_path = getattr(track, "path", None)
        if normalized_track_id in target_ids or (
            track_path is not None and str(track_path) in target_paths
        ):
            remove_indices.append(index)

    for index in reversed(remove_indices):
        playlist.remove(index)

    if remove_indices:
        await event_bus.publish(
            PlayerPlaylistEvent(
                player_id=ctx.player_id,
                action="delete",
                count=len(playlist),
            )
        )

    return {"count": len(playlist)}


async def _playlist_deletealbum(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """Handle 'playlist deletealbum' via deletetracks."""
    mapped = _build_album_alias_command(params, target_subcommand="deletetracks")
    return await _playlist_deletetracks(ctx, mapped)


# =============================================================================
# Zap
# =============================================================================


async def _playlist_zap(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """Handle 'playlist zap' - remove current (or specified) track from queue."""
    if ctx.player_id == "-":
        return {"error": "No player specified"}

    if ctx.playlist_manager is None:
        return {"error": "Playlist manager not available"}

    playlist = ctx.playlist_manager.get(ctx.player_id)
    if len(playlist) == 0:
        return {"count": 0}

    if len(params) >= 3:
        try:
            zap_index = int(params[2])
        except (ValueError, TypeError):
            return {"error": f"Invalid zap index: {params[2]}"}
    else:
        zap_index = playlist.current_index

    removed = playlist.remove(zap_index)
    if removed is None:
        return {"count": len(playlist)}

    _ZAPPED_TRACKS.setdefault(ctx.player_id, []).append(removed)

    await event_bus.publish(
        PlayerPlaylistEvent(
            player_id=ctx.player_id,
            action="delete",
            count=len(playlist),
        )
    )

    return {
        "count": len(playlist),
    }
