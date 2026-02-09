"""
Playlist Command Handlers.

Handles playlist management commands:
- playlist play: Play a track by index or load a new playlist
- playlist resume: Resume current playback or a saved playlist snapshot
- playlist add: Add tracks to the playlist
- playlist addtracks/addalbum: Add multiple tracks to the playlist
- playlist insert: Insert tracks at a position
- playlist inserttracks/insertalbum: Insert multiple tracks after current item
- playlist delete: Remove tracks from the playlist
- playlist clear: Clear the playlist
- playlist move: Move a track to a new position
- playlist index: Jump to a track by index
- playlist shuffle: Toggle shuffle mode
- playlist repeat: Set repeat mode
- playlist tracks: Get playlist track info
- playlist loadtracks/playtracks/loadalbum/playalbum: Load tracks into playlist
- playlist save: Save the current queue as an in-memory snapshot
- playlist jump: Relative navigation (+1/-1 for next/previous)

NOTE (LMS compatibility):
LMS `playlist loadtracks` does more than just populate the playlist:
- stop+clear the current playlist
- add tracks
- reshuffle (if needed)
- then `playlist jump` to the requested index (defaults to 0), which starts playback

Resonance mirrors the "stop+clear then jump" behavior to avoid races where the client
loads tracks and then issues separate commands (index/play) while late track-finished
events or old streams are still in flight.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from resonance.core.db.models import TrackRow
from resonance.core.events import PlayerPlaylistEvent, event_bus
from resonance.protocol.commands import FLAG_NO_RESTART_DECODER
from resonance.web.handlers import CommandContext
from resonance.web.handlers.playback import cmd_pause, cmd_stop
from resonance.web.handlers.sync import cmd_sync
from resonance.web.jsonrpc_helpers import (
    get_filter_int,
    parse_start_items,
    parse_tagged_params,
)

logger = logging.getLogger(__name__)

# MVP snapshot store for playlist save/resume compatibility.
# Structure: {player_id: {normalized_name: snapshot_dict}}
_SAVED_PLAYLISTS: dict[str, dict[str, dict[str, Any]]] = {}

# Best-effort in-memory history of zapped items per player.
_ZAPPED_TRACKS: dict[str, list[Any]] = {}


def _stream_flags_for_explicit_restart(flags: int) -> int:
    """Clear no-restart-decoder bit for explicit STOP+FLUSH+START restarts."""
    try:
        normalized = int(flags)
    except (TypeError, ValueError):
        normalized = 0
    return normalized & ~FLAG_NO_RESTART_DECODER


def _preview_snapshot_name(player_id: str) -> str:
    """Build the temporary snapshot key used by playlist preview."""
    return f"tempplaylist_{player_id.replace(':', '')}"


async def cmd_playlist(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """
    Handle 'playlist' command.

    Dispatches to sub-handlers based on the subcommand.
    """
    if len(params) < 2:
        return {"error": "Missing playlist subcommand"}

    subcommand = str(params[1]).lower()

    handlers = {
        "play": _playlist_play,
        "resume": _playlist_resume,
        "add": _playlist_add,
        "append": _playlist_add,
        "addtracks": _playlist_addtracks,
        "addalbum": _playlist_addalbum,
        "album": _playlist_album,
        "artist": _playlist_artist,
        "insert": _playlist_insert,
        "inserttracks": _playlist_inserttracks,
        "insertalbum": _playlist_insertalbum,
        "insertlist": _playlist_insertlist,
        "deletetracks": _playlist_deletetracks,
        "deletealbum": _playlist_deletealbum,
        "deleteitem": _playlist_deleteitem,
        "delete": _playlist_delete,
        "cant_open": _playlist_cant_open,
        "pause": _playlist_pause,
        "stop": _playlist_stop,
        "clear": _playlist_clear,
        "move": _playlist_move,
        "index": _playlist_index,
        "shuffle": _playlist_shuffle,
        "repeat": _playlist_repeat,
        "duration": _playlist_duration,
        "genre": _playlist_genre,
        "tracks": _playlist_tracks,
        "load": _playlist_load,
        "load_done": _playlist_load_done,
        "loadtracks": _playlist_loadtracks,
        "playtracks": _playlist_loadtracks,
        "loadalbum": _playlist_loadalbum,
        "playalbum": _playlist_playalbum,
        "playlistsinfo": _playlist_playlistsinfo,
        "preview": _playlist_preview,
        "zap": _playlist_zap,
        "save": _playlist_save,
        "modified": _playlist_modified,
        "name": _playlist_name,
        "newsong": _playlist_newsong,
        "open": _playlist_open,
        "path": _playlist_path,
        "remote": _playlist_remote,
        "sync": _playlist_sync,
        "title": _playlist_title,
        "url": _playlist_url,
        "jump": _playlist_jump,
    }

    handler = handlers.get(subcommand)
    if handler is None:
        return {"error": f"Unknown playlist subcommand: {subcommand}"}

    return await handler(ctx, params)


def _parse_playlist_kv_params(params: list[Any], *, start_index: int = 0) -> dict[str, str]:
    """Parse LMS tagged params plus optional dict-style params."""
    parsed = parse_tagged_params(params[start_index:])

    for raw in params[start_index:]:
        if not isinstance(raw, dict):
            continue
        for key, value in raw.items():
            if value is None:
                continue
            parsed[str(key)] = str(value)

    return parsed


def _first_non_tag_param(params: list[Any], *, start_index: int = 0) -> str | None:
    """Return the first positional value that is not a tag:value token."""
    for raw in params[start_index:]:
        if isinstance(raw, str):
            value = raw.strip()
            if not value or ":" in value:
                continue
            return value
        if isinstance(raw, (int, float)):
            return str(raw)
    return None


def _is_truthy_tag(value: str | None) -> bool:
    """Interpret LMS-style tagged booleans (1/0, true/false, yes/no)."""
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_optional_playlist_index(params: list[Any], *, start_index: int = 2) -> int | None:
    """Parse optional playlist index arguments used by LMS-style query subcommands."""
    if len(params) <= start_index:
        return None

    raw = params[start_index]
    if isinstance(raw, str):
        value = raw.strip()
        if not value or value == "?":
            return None
        raw = value

    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _playlist_track_for_query(playlist: Any, *, index: int | None = None) -> Any | None:
    """Return the requested track (or current track) from a playlist object."""
    if playlist is None or len(playlist) == 0:
        return None

    resolved_index = playlist.current_index if index is None else index
    if resolved_index < 0 or resolved_index >= len(playlist):
        return None

    return playlist.tracks[resolved_index]


def _is_remote_track_url(path: str | None) -> bool:
    """Best-effort remote URL detection for playlist remote query."""
    if not path:
        return False

    value = path.strip().lower()
    return value.startswith(("http://", "https://", "mms://", "rtsp://", "icy://", "ftp://"))


def _normalize_query_string(value: Any) -> str:
    """Normalize possibly-missing string fields in query responses."""
    if value is None:
        return ""
    return str(value)


def _normalize_duration_seconds(duration_ms: Any) -> float:
    """Convert track duration in ms to LMS-style seconds."""
    try:
        duration = float(duration_ms)
    except (TypeError, ValueError):
        return 0.0

    if duration <= 0:
        return 0.0
    return duration / 1000.0


def _normalize_saved_playlist_name(name: str) -> str:
    return name.strip().lower()


def _build_album_alias_command(params: list[Any], *, target_subcommand: str) -> list[Any]:
    """Map legacy playlist *album arguments to playlist *tracks tagged args."""
    mapped: list[Any] = ["playlist", target_subcommand]
    tagged = _parse_playlist_kv_params(params, start_index=2)

    if tagged:
        for key, value in tagged.items():
            mapped.append(f"{key}:{value}")
        return mapped

    positional: list[str] = []
    for raw in params[2:]:
        if isinstance(raw, str):
            value = raw.strip()
            if value:
                positional.append(value)
        elif isinstance(raw, (int, float)):
            positional.append(str(raw))

    if len(positional) == 1:
        try:
            int(positional[0])
            mapped.append(f"album_id:{positional[0]}")
        except ValueError:
            mapped.append(f"album:{positional[0]}")
        return mapped

    legacy_keys = ("genre", "artist", "album", "title")
    for key, value in zip(legacy_keys, positional):
        mapped.append(f"{key}:{value}")

    return mapped


def _matches_search_term(value: str | None, term: str | None) -> bool:
    """Simple LMS-like matcher with optional '*' wildcard support."""
    if not term:
        return True
    if value is None:
        return False

    needle = term.strip().casefold()
    haystack = value.casefold()

    if not needle or needle == "*":
        return True

    if "*" in needle:
        needle = needle.replace("*", "")
        if not needle:
            return True
        return needle in haystack

    return needle in haystack


def _row_to_library_track(row: TrackRow) -> Any:
    """Convert DB row to the Track model used by playlist/streaming handlers."""
    from resonance.core.library import Track

    return Track(
        id=row.id,
        path=row.path,
        title=row.title or "",
        artist_id=row.artist_id,
        album_id=row.album_id,
        artist_name=row.artist,
        album_title=row.album,
        year=row.year,
        duration_ms=row.duration_ms,
        disc_no=row.disc_no,
        track_no=row.track_no,
        compilation=row.compilation,
    )


async def _resolve_track_rows_from_filters(
    ctx: CommandContext,
    params: list[Any],
    *,
    start_index: int = 2,
    numeric_fallback_key: str | None = None,
) -> tuple[list[TrackRow] | None, str | None]:
    """
    Resolve playlist filter params to DB track rows.

    Returns (rows, error). Exactly one of these is non-None.
    """
    tagged_params = _parse_playlist_kv_params(params, start_index=start_index)

    track_id = get_filter_int(tagged_params, "track_id") or get_filter_int(tagged_params, "track.id")
    album_id = get_filter_int(tagged_params, "album_id")
    artist_id = get_filter_int(tagged_params, "artist_id")
    genre_id = get_filter_int(tagged_params, "genre_id")
    year = get_filter_int(tagged_params, "year")

    # Accept numeric aliases used by some clients (album:<id>, artist:<id>, genre:<id>).
    if album_id is None:
        album_id = get_filter_int(tagged_params, "album")
    if artist_id is None:
        artist_id = get_filter_int(tagged_params, "artist")
    if genre_id is None:
        genre_id = get_filter_int(tagged_params, "genre")

    # Optional fallback for legacy positional forms where the first arg is numeric.
    if (
        track_id is None
        and album_id is None
        and artist_id is None
        and genre_id is None
        and year is None
        and numeric_fallback_key is not None
    ):
        first = _first_non_tag_param(params, start_index=start_index)
        try:
            parsed = int(first) if first is not None else None
        except (TypeError, ValueError):
            parsed = None

        if parsed is not None:
            if numeric_fallback_key == "track_id":
                track_id = parsed
            elif numeric_fallback_key == "album_id":
                album_id = parsed
            elif numeric_fallback_key == "artist_id":
                artist_id = parsed
            elif numeric_fallback_key == "genre_id":
                genre_id = parsed
            elif numeric_fallback_key == "year":
                year = parsed

    db = ctx.music_library._db

    # If we got a textual genre name, map it once to a genre_id.
    if genre_id is None and tagged_params.get("genre"):
        genre_term = tagged_params["genre"]
        genres = await db.list_genres(limit=500, offset=0)
        exact_match = next(
            (
                g
                for g in genres
                if str(g.get("name", g.get("genre", ""))).strip().casefold()
                == genre_term.strip().casefold()
            ),
            None,
        )
        if exact_match is not None:
            try:
                genre_id = int(exact_match.get("id"))
            except (TypeError, ValueError):
                genre_id = None

    rows: list[TrackRow] = []

    if track_id is not None:
        row = await db.get_track_by_id(track_id)
        rows = [row] if row else []
    elif genre_id is not None and album_id is not None and year is not None:
        rows = await db.list_tracks_by_genre_album_and_year(
            genre_id=genre_id,
            album_id=album_id,
            year=year,
            offset=0,
            limit=1000,
            order_by="album",
        )
    elif genre_id is not None and artist_id is not None and year is not None:
        rows = await db.list_tracks_by_genre_artist_and_year(
            genre_id=genre_id,
            artist_id=artist_id,
            year=year,
            offset=0,
            limit=1000,
            order_by="album",
        )
    elif genre_id is not None and album_id is not None:
        rows = await db.list_tracks_by_genre_and_album(
            genre_id=genre_id,
            album_id=album_id,
            offset=0,
            limit=1000,
            order_by="album",
        )
    elif genre_id is not None and artist_id is not None:
        rows = await db.list_tracks_by_genre_and_artist(
            genre_id=genre_id,
            artist_id=artist_id,
            offset=0,
            limit=1000,
            order_by="album",
        )
    elif album_id is not None and year is not None:
        rows = await db.list_tracks_by_album_and_year(
            album_id=album_id,
            year=year,
            offset=0,
            limit=1000,
            order_by="album",
        )
    elif artist_id is not None and year is not None:
        rows = await db.list_tracks_by_artist_and_year(
            artist_id=artist_id,
            year=year,
            offset=0,
            limit=1000,
            order_by="album",
        )
    elif genre_id is not None and year is not None:
        rows = await db.list_tracks_by_genre_and_year(
            genre_id=genre_id,
            year=year,
            offset=0,
            limit=1000,
            order_by="album",
        )
    elif album_id is not None:
        rows = await db.list_tracks_by_album(album_id=album_id, offset=0, limit=1000, order_by="album")
    elif artist_id is not None:
        rows = await db.list_tracks_by_artist(
            artist_id=artist_id,
            offset=0,
            limit=1000,
            order_by="album",
        )
    elif genre_id is not None:
        rows = await db.list_tracks_by_genre_id(
            genre_id=genre_id,
            offset=0,
            limit=1000,
            order_by="title",
        )
    elif year is not None:
        rows = await db.list_tracks_by_year(year=year, offset=0, limit=1000, order_by="album")
    else:
        # Fallback for legacy textual filters (album/artist/title).
        title_term = tagged_params.get("title")
        album_term = tagged_params.get("album")
        artist_term = tagged_params.get("artist")
        search_query = title_term or album_term or artist_term

        if not search_query:
            return None, "No track criteria specified"

        candidate_rows = await db.search_tracks(search_query, limit=2000, offset=0)
        rows = [
            row
            for row in candidate_rows
            if _matches_search_term(row.title, title_term)
            and _matches_search_term(row.album, album_term)
            and _matches_search_term(row.artist, artist_term)
        ]

    return rows, None



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
    provided, delegate to the top-level `sync` command semantics.
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
        if snapshot is None:
            return {"error": f"Saved playlist not found: {resume_name}"}

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


async def _playlist_save(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """Handle 'playlist save' using an in-memory snapshot store (MVP)."""
    if ctx.player_id == "-":
        return {"error": "No player specified"}

    if ctx.playlist_manager is None:
        return {"error": "Playlist manager not available"}

    playlist_name = _first_non_tag_param(params, start_index=2)
    if not playlist_name:
        return {"error": "Missing playlist name"}

    playlist = ctx.playlist_manager.get(ctx.player_id)
    by_player = _SAVED_PLAYLISTS.setdefault(ctx.player_id, {})
    by_player[_normalize_saved_playlist_name(playlist_name)] = {
        "name": playlist_name,
        "tracks": list(playlist.tracks),
        "current_index": playlist.current_index,
        "saved_at": time.time(),
    }

    return {
        "__playlist_id": playlist_name,
        "count": len(playlist),
    }


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
# Helper Functions
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

    # Queue the new file
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

async def _resolve_track(
    ctx: CommandContext,
    track_ref: Any,
    tagged_params: dict[str, str],
) -> Any:
    """
    Resolve a track reference to a Track object.

    track_ref can be:
    - Integer track ID
    - String track ID
    - File path
    """
    from resonance.core.library import Track

    db = ctx.music_library._db

    # Try as track ID
    def _row_to_track(row: TrackRow) -> Track:
        """Convert a TrackRow dataclass to a Track object."""
        return Track(
            id=row.id,
            path=row.path,
            title=row.title or "",
            artist_id=row.artist_id,
            album_id=row.album_id,
            artist_name=row.artist,
            album_title=row.album,
            year=row.year,
            duration_ms=row.duration_ms,
            disc_no=row.disc_no,
            track_no=row.track_no,
            compilation=row.compilation,
        )

    # Try as track ID
    try:
        track_id = int(track_ref)
        row = await db.get_track_by_id(track_id)
        if row is not None:
            return _row_to_track(row)
    except (ValueError, TypeError):
        pass

    # Try as file path
    track_ref_str = str(track_ref)
    if track_ref_str.startswith("file://"):
        track_ref_str = track_ref_str[7:]

    row = await db.get_track_by_path(track_ref_str)
    if row is not None:
        return _row_to_track(row)

    return None
