"""
Playlist Persistence Handlers.

Handles playlist sub-commands related to saving and restoring queue state:

- ``playlist save``    — persist current queue to M3U on disk + in-memory snapshot
- ``playlist load``    — load and play one item or a filtered set
- ``playlist preview`` — save current queue, load a preview item, restore on stop
- ``playlist resume``  — resume current playback or restore a saved snapshot
"""

from __future__ import annotations

import logging
import time
from typing import Any

from resonance.core.events import PlayerPlaylistEvent, event_bus
from resonance.core.playlist_formats import write_m3u
from resonance.web.handlers import CommandContext
from resonance.web.handlers.playlist_helpers import (
    _SAVED_PLAYLISTS,
    _first_non_tag_param,
    _is_truthy_tag,
    _m3u_path_for_name,
    _normalize_saved_playlist_name,
    _parse_playlist_kv_params,
    _preview_snapshot_name,
    _resolve_track,
    _resolve_track_rows_from_filters,
    load_saved_playlist_tracks,
)
from resonance.web.handlers.playlist_mutation import _playlist_loadtracks
from resonance.web.handlers.playlist_playback import _start_track_stream

logger = logging.getLogger(__name__)


# =============================================================================
# Save
# =============================================================================


async def _playlist_save(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """Handle 'playlist save' — persist to M3U on disk and in-memory snapshot."""
    if ctx.player_id == "-":
        return {"error": "No player specified"}

    if ctx.playlist_manager is None:
        return {"error": "Playlist manager not available"}

    playlist_name = _first_non_tag_param(params, start_index=2)
    if not playlist_name:
        return {"error": "Missing playlist name"}

    playlist = ctx.playlist_manager.get(ctx.player_id)

    # In-memory snapshot (for preview/restore flow)
    by_player = _SAVED_PLAYLISTS.setdefault(ctx.player_id, {})
    by_player[_normalize_saved_playlist_name(playlist_name)] = {
        "name": playlist_name,
        "tracks": list(playlist.tracks),
        "current_index": playlist.current_index,
        "saved_at": time.time(),
    }

    # Persist to M3U on disk
    m3u_path = _m3u_path_for_name(playlist_name)
    if m3u_path is not None:
        try:
            write_m3u(
                m3u_path,
                playlist.tracks,
                current_index=playlist.current_index,
            )
            logger.info(
                "Saved playlist '%s' (%d tracks) to %s",
                playlist_name,
                len(playlist),
                m3u_path,
            )
        except Exception:
            logger.exception("Failed to save playlist '%s' to disk", playlist_name)

    return {
        "__playlist_id": playlist_name,
        "count": len(playlist),
    }


# =============================================================================
# Load
# =============================================================================


async def _playlist_load(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """Handle 'playlist load' - load and play one item or a filtered set."""
    if ctx.player_id == "-":
        return {"error": "No player specified"}

    if ctx.playlist_manager is None:
        return {"error": "Playlist manager not available"}

    if len(params) < 3:
        return {"error": "Missing item to load"}

    filter_keys = {
        "track_id",
        "track.id",
        "album_id",
        "artist_id",
        "genre_id",
        "year",
        "album",
        "artist",
        "title",
    }

    has_filter_tokens = False
    for raw in params[2:]:
        if isinstance(raw, dict):
            has_filter_tokens = True
            break
        if isinstance(raw, str) and ":" in raw:
            key = raw.split(":", 1)[0].strip().lower()
            if key in filter_keys:
                has_filter_tokens = True
                break

    rows, error = await _resolve_track_rows_from_filters(
        ctx,
        params,
        start_index=2,
        numeric_fallback_key="track_id",
    )
    if has_filter_tokens and error is None:
        mapped = ["playlist", "loadtracks", *params[2:]]
        return await _playlist_loadtracks(ctx, mapped)

    track_ref = params[2]
    tagged_params = _parse_playlist_kv_params(params, start_index=3)
    track = await _resolve_track(ctx, track_ref, tagged_params)

    # Numeric load forms (e.g. playlist load 123) resolve through loadtracks.
    if track is None and error is None and rows:
        mapped = ["playlist", "loadtracks", *params[2:]]
        return await _playlist_loadtracks(ctx, mapped)

    if track is None:
        return {"error": f"Track not found: {track_ref}"}

    # Suppress track-finished for a short window to prevent race conditions.
    if hasattr(ctx.slimproto, "_resonance_server") and hasattr(
        ctx.slimproto._resonance_server, "suppress_track_finished_for_player"
    ):
        ctx.slimproto._resonance_server.suppress_track_finished_for_player(ctx.player_id, seconds=6.0)

    playlist = ctx.playlist_manager.get(ctx.player_id)
    playlist.clear()

    if ctx.streaming_server is not None:
        ctx.streaming_server.cancel_stream(ctx.player_id)

    player = await ctx.player_registry.get_by_mac(ctx.player_id)
    if player is not None:
        await player.stop()
        if hasattr(player, "flush"):
            await player.flush()

    playlist.add(track)

    load_tagged = _parse_playlist_kv_params(params, start_index=2)
    if player is not None and len(playlist) > 0 and not _is_truthy_tag(load_tagged.get("noplay")):
        start_track = playlist.play(0)
        if start_track is not None:
            await _start_track_stream(ctx, player, start_track)

    await event_bus.publish(
        PlayerPlaylistEvent(
            player_id=ctx.player_id,
            action="load",
            count=len(playlist),
        )
    )

    return {
        "count": len(playlist),
    }


# =============================================================================
# Preview
# =============================================================================


async def _playlist_preview(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """Handle 'playlist preview' by save+load and restore-on-stop semantics."""
    if ctx.player_id == "-":
        return {"error": "No player specified"}

    tagged_params = _parse_playlist_kv_params(params, start_index=2)
    cmd = str(tagged_params.get("cmd", "")).strip().lower()
    snapshot_name = _preview_snapshot_name(ctx.player_id)

    if cmd == "stop":
        return await _playlist_resume(
            ctx,
            ["playlist", "resume", snapshot_name, "noplay:1", "wipePlaylist:1"],
        )

    preview_url = tagged_params.get("url")
    if not preview_url and len(params) >= 3 and isinstance(params[2], str):
        first_param = params[2].strip()
        lowered = first_param.casefold()
        if lowered.startswith("url:"):
            preview_url = first_param.split(":", 1)[1]
        elif not lowered.startswith("cmd:") and not lowered.startswith("title:") and not lowered.startswith("fadein:"):
            preview_url = first_param

    if not preview_url:
        return {"error": "Missing preview url"}

    save_result = await _playlist_save(ctx, ["playlist", "save", snapshot_name])
    if "error" in save_result:
        return save_result

    return await _playlist_load(ctx, ["playlist", "load", preview_url])


# =============================================================================
# Resume
# =============================================================================


async def _playlist_resume(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """Handle 'playlist resume' for current queue or saved snapshots."""
    if ctx.player_id == "-":
        return {"error": "No player specified"}

    if ctx.playlist_manager is None:
        return {"error": "Playlist manager not available"}

    playlist = ctx.playlist_manager.get(ctx.player_id)
    tagged_params = _parse_playlist_kv_params(params, start_index=2)

    resume_name = _first_non_tag_param(params, start_index=2)
    resumed_from_snapshot = False

    if resume_name:
        saved_for_player = _SAVED_PLAYLISTS.get(ctx.player_id, {})
        snapshot_key = _normalize_saved_playlist_name(resume_name)
        snapshot = saved_for_player.get(snapshot_key)

        if snapshot is not None:
            # Found in-memory snapshot — use it directly
            playlist.clear()
            for saved_track in snapshot.get("tracks", []):
                playlist.add(saved_track)

            if len(playlist) > 0:
                snapshot_index = int(snapshot.get("current_index", 0))
                playlist.play(snapshot_index)

            resumed_from_snapshot = True

            if _is_truthy_tag(tagged_params.get("wipePlaylist")):
                saved_for_player.pop(snapshot_key, None)
                if not saved_for_player:
                    _SAVED_PLAYLISTS.pop(ctx.player_id, None)
        else:
            # Try loading from M3U on disk
            from resonance.core.playlist import AlbumId, ArtistId, PlaylistTrack, TrackId

            m3u_entries = load_saved_playlist_tracks(resume_name)
            if m3u_entries is None:
                return {"error": f"Saved playlist not found: {resume_name}"}

            playlist.clear()
            for entry in m3u_entries:
                playlist.add(PlaylistTrack(
                    track_id=None,
                    path=entry.path,
                    title=entry.title,
                    artist=entry.artist,
                    album=entry.album,
                    duration_ms=entry.duration_seconds * 1000 if entry.duration_seconds > 0 else 0,
                ))

            if len(playlist) > 0:
                from resonance.core.playlist_formats import read_m3u_curtrack
                m3u_path = _m3u_path_for_name(resume_name)
                cur_idx = read_m3u_curtrack(m3u_path) if m3u_path else 0
                playlist.play(cur_idx)

            resumed_from_snapshot = True

        await event_bus.publish(
            PlayerPlaylistEvent(
                player_id=ctx.player_id,
                action="load",
                count=len(playlist),
            )
        )

    if len(playlist) == 0:
        return {"error": "No track to resume"}

    if _is_truthy_tag(tagged_params.get("noplay")):
        return {
            "count": len(playlist),
            "_index": playlist.current_index,
        }

    player = await ctx.player_registry.get_by_mac(ctx.player_id)
    if player is None:
        return {
            "count": len(playlist),
            "_index": playlist.current_index,
        }

    if not resumed_from_snapshot:
        state = getattr(getattr(player, "status", None), "state", None)
        state_name = str(getattr(state, "name", state)).upper() if state is not None else ""
        if state_name == "PAUSED":
            await player.play()
            return {
                "count": len(playlist),
                "_index": playlist.current_index,
            }

    track = playlist.play(playlist.current_index)
    if track is None:
        return {"error": "No track to resume"}

    await _start_track_stream(ctx, player, track)

    return {
        "count": len(playlist),
        "_index": playlist.current_index,
    }
