"""
Playlist Query Handlers.

Handles read-only playlist sub-commands that return metadata or track
information without modifying the queue:

- ``playlist album``          — album title of current/specified track
- ``playlist artist``         — artist name of current/specified track
- ``playlist duration``       — duration in seconds
- ``playlist genre``          — first genre name (best-effort)
- ``playlist modified``       — whether the queue has been modified
- ``playlist name``           — queue display name
- ``playlist path``           — file path of current/specified track
- ``playlist remote``         — whether the track is a remote URL
- ``playlist title``          — title of current/specified track
- ``playlist url``            — URL/path of the current track
- ``playlist tracks``         — paginated track list
- ``playlist playlistsinfo``  — current queue metadata summary

Event-style no-ops (LMS internal notifications that Resonance acknowledges):

- ``playlist load_done``
- ``playlist newsong``
- ``playlist open``
- ``playlist sync``          — delegates to top-level ``sync`` if args present
- ``playlist cant_open``
"""

from __future__ import annotations

import logging
from typing import Any

from resonance.web.handlers import CommandContext
from resonance.web.handlers.playlist_helpers import (
    _is_remote_track_url,
    _normalize_duration_seconds,
    _normalize_query_string,
    _parse_optional_playlist_index,
    _playlist_track_for_query,
)
from resonance.web.handlers.sync import cmd_sync
from resonance.web.jsonrpc_helpers import parse_start_items

logger = logging.getLogger(__name__)


# =============================================================================
# Metadata queries
# =============================================================================


async def _playlist_playlistsinfo(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """Handle 'playlist playlistsinfo' - return current queue metadata."""
    if ctx.player_id == "-" or ctx.playlist_manager is None:
        return {
            "id": 0,
            "name": "Current Playlist",
            "modified": 0,
            "url": "",
        }

    playlist = ctx.playlist_manager.get(ctx.player_id)
    current_track = playlist.current_track
    return {
        "id": 0,
        "name": "Current Playlist",
        "modified": 1 if len(playlist) > 0 else 0,
        "url": str(getattr(current_track, "path", "")) if current_track is not None else "",
    }


async def _playlist_album(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """Handle 'playlist album' query."""
    if ctx.player_id == "-" or ctx.playlist_manager is None:
        return {"_album": ""}

    playlist = ctx.playlist_manager.get(ctx.player_id)
    index = _parse_optional_playlist_index(params, start_index=2)
    track = _playlist_track_for_query(playlist, index=index)
    value = getattr(track, "album_title", None) if track is not None else None
    if value in (None, "") and track is not None:
        value = getattr(track, "album", None)
    return {"_album": _normalize_query_string(value)}


async def _playlist_artist(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """Handle 'playlist artist' query."""
    if ctx.player_id == "-" or ctx.playlist_manager is None:
        return {"_artist": ""}

    playlist = ctx.playlist_manager.get(ctx.player_id)
    index = _parse_optional_playlist_index(params, start_index=2)
    track = _playlist_track_for_query(playlist, index=index)
    value = getattr(track, "artist_name", None) if track is not None else None
    if value in (None, "") and track is not None:
        value = getattr(track, "artist", None)
    return {"_artist": _normalize_query_string(value)}


async def _playlist_duration(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """Handle 'playlist duration' query (seconds)."""
    if ctx.player_id == "-" or ctx.playlist_manager is None:
        return {"_duration": 0.0}

    playlist = ctx.playlist_manager.get(ctx.player_id)
    index = _parse_optional_playlist_index(params, start_index=2)
    track = _playlist_track_for_query(playlist, index=index)
    duration_ms = getattr(track, "duration_ms", None) if track is not None else None
    return {"_duration": _normalize_duration_seconds(duration_ms)}


async def _playlist_genre(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """Handle 'playlist genre' query (best-effort first genre name)."""
    if ctx.player_id == "-" or ctx.playlist_manager is None:
        return {"_genre": ""}

    playlist = ctx.playlist_manager.get(ctx.player_id)
    index = _parse_optional_playlist_index(params, start_index=2)
    track = _playlist_track_for_query(playlist, index=index)
    if track is None:
        return {"_genre": ""}

    direct_value = getattr(track, "genre", None)
    if direct_value:
        return {"_genre": _normalize_query_string(direct_value)}

    track_id = getattr(track, "id", None)
    if track_id is None:
        track_id = getattr(track, "track_id", None)

    try:
        resolved_track_id = int(track_id) if track_id is not None else None
    except (TypeError, ValueError):
        resolved_track_id = None

    if resolved_track_id is None:
        return {"_genre": ""}

    try:
        conn = ctx.music_library._db._require_conn()
        cursor = await conn.execute(
            """
            SELECT g.name
            FROM genres g
            JOIN track_genres tg ON tg.genre_id = g.id
            WHERE tg.track_id = ?
            ORDER BY g.name
            LIMIT 1
            """,
            (resolved_track_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
    except Exception:
        logger.debug("playlist genre query failed for track_id=%s", resolved_track_id, exc_info=True)
        row = None

    if row is None:
        return {"_genre": ""}
    return {"_genre": _normalize_query_string(row[0])}


async def _playlist_modified(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """Handle 'playlist modified' query."""
    if ctx.player_id == "-" or ctx.playlist_manager is None:
        return {"_modified": 0}

    playlist = ctx.playlist_manager.get(ctx.player_id)
    return {"_modified": 1 if len(playlist) > 0 else 0}


async def _playlist_name(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """Handle 'playlist name' query."""
    if ctx.player_id == "-" or ctx.playlist_manager is None:
        return {"_name": ""}

    playlist = ctx.playlist_manager.get(ctx.player_id)
    if len(playlist) == 0:
        return {"_name": ""}

    return {"_name": "Current Playlist"}


async def _playlist_path(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """Handle 'playlist path' query."""
    if ctx.player_id == "-" or ctx.playlist_manager is None:
        return {"_path": ""}

    playlist = ctx.playlist_manager.get(ctx.player_id)
    index = _parse_optional_playlist_index(params, start_index=2)
    track = _playlist_track_for_query(playlist, index=index)
    path = getattr(track, "path", "") if track is not None else ""
    return {"_path": _normalize_query_string(path)}


async def _playlist_remote(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """Handle 'playlist remote' query."""
    if ctx.player_id == "-" or ctx.playlist_manager is None:
        return {"_remote": 0}

    playlist = ctx.playlist_manager.get(ctx.player_id)
    index = _parse_optional_playlist_index(params, start_index=2)
    track = _playlist_track_for_query(playlist, index=index)
    path = getattr(track, "path", None) if track is not None else None
    return {"_remote": 1 if _is_remote_track_url(_normalize_query_string(path)) else 0}


async def _playlist_title(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """Handle 'playlist title' query."""
    if ctx.player_id == "-" or ctx.playlist_manager is None:
        return {"_title": ""}

    playlist = ctx.playlist_manager.get(ctx.player_id)
    index = _parse_optional_playlist_index(params, start_index=2)
    track = _playlist_track_for_query(playlist, index=index)
    value = getattr(track, "title", None) if track is not None else None
    return {"_title": _normalize_query_string(value)}


async def _playlist_url(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """Handle 'playlist url' query."""
    if ctx.player_id == "-" or ctx.playlist_manager is None:
        return {"_url": ""}

    playlist = ctx.playlist_manager.get(ctx.player_id)
    track = _playlist_track_for_query(playlist, index=None)
    value = getattr(track, "path", None) if track is not None else None
    return {"_url": _normalize_query_string(value)}


async def _playlist_tracks(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """
    Handle 'playlist tracks' - get playlist track info.
    """
    if ctx.player_id == "-":
        return {"count": 0, "tracks_loop": []}

    if ctx.playlist_manager is None:
        return {"count": 0, "tracks_loop": []}

    playlist = ctx.playlist_manager.get(ctx.player_id)
    if playlist is None:
        return {"count": 0, "tracks_loop": []}

    start, items = parse_start_items(params)
    server_url = f"http://{ctx.server_host}:{ctx.server_port}"

    tracks_loop = []
    all_tracks = list(playlist.tracks)
    paginated = all_tracks[start : start + items]

    for i, track in enumerate(paginated):
        tracks_loop.append(
            {
                "id": track.id,
                "title": track.title,
                "artist": track.artist_name or "",
                "album": track.album_title or "",
                "duration": (track.duration_ms or 0) / 1000.0,
                "url": f"{server_url}/stream.mp3?track_id={track.id}",
                "playlist index": start + i,
            }
        )

    return {
        "count": len(all_tracks),
        "tracks_loop": tracks_loop,
    }


# =============================================================================
# Event-style no-ops (LMS internal notifications)
# =============================================================================


async def _playlist_load_done(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """Handle LMS internal 'playlist load_done' notification as no-op."""
    logger.debug("playlist load_done received", extra={"player_id": ctx.player_id})
    return {}


async def _playlist_newsong(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """Handle LMS internal 'playlist newsong' notification as no-op."""
    logger.debug("playlist newsong received", extra={"player_id": ctx.player_id})
    return {}


async def _playlist_open(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """Handle LMS internal 'playlist open' notification as no-op."""
    logger.debug(
        "playlist open received",
        extra={"player_id": ctx.player_id, "path": (params[2] if len(params) >= 3 else None)},
    )
    return {}


async def _playlist_sync(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """Handle 'playlist sync' notification/alias.

    LMS often emits this without params as a notification. If a target/query is
    provided, delegate to the top-level ``sync`` command semantics.
    """
    if len(params) <= 2:
        return {}

    delegated = ["sync", *params[2:]]
    return await cmd_sync(ctx, delegated)


async def _playlist_cant_open(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """Handle LMS internal 'playlist cant_open' notification as no-op."""
    url = params[2] if len(params) >= 3 else None
    error = params[3] if len(params) >= 4 else None
    logger.debug("playlist cant_open received", extra={"player_id": ctx.player_id, "url": url, "error": error})
    return {}
