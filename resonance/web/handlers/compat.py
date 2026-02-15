"""
LMS compatibility handlers for legacy/optional top-level commands.

These handlers intentionally provide lightweight behavior so clients that
expect a broad LMS command surface do not fail with "Unknown command".

Persistence:
- ``configure_prefs_persistence()`` / ``save_player_prefs()`` /
  ``load_all_player_prefs()`` serialize per-player preferences to JSON files
  so they survive server restarts.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from resonance.protocol.commands import (
    DEFAULT_GRFD_BITMAP_BYTES,
    DEFAULT_GRFD_FRAMEBUFFER_OFFSET,
)
from resonance.web.handlers import CommandContext
from resonance.web.handlers.library import cmd_titles
from resonance.web.handlers.status import VERSION
from resonance.web.jsonrpc_helpers import parse_start_items

_logger = logging.getLogger(__name__)

_PLAYER_PREFS_LOCK = asyncio.Lock()
_PLAYER_PREFS: dict[str, dict[str, str]] = {}
_TRACK_RATINGS_LOCK = asyncio.Lock()
_TRACK_RATINGS: dict[str, int] = {}

# ---------------------------------------------------------------------------
# Player-prefs persistence
# ---------------------------------------------------------------------------

_PREFS_PERSISTENCE_DIR: Path | None = None


def _safe_prefs_filename(player_id: str) -> str:
    """Turn a player MAC/ID into a safe filename (replace colons)."""
    return player_id.replace(":", "-") + ".json"


def _player_id_from_prefs_filename(filename: str) -> str:
    """Reverse of ``_safe_prefs_filename``."""
    return filename.removesuffix(".json").replace("-", ":")


def configure_prefs_persistence(directory: Path | None) -> None:
    """Set the directory used for player-prefs persistence.

    Call once during server startup **before** ``load_all_player_prefs()``.
    Passing ``None`` disables persistence (pure in-memory).
    """
    global _PREFS_PERSISTENCE_DIR  # noqa: PLW0603
    _PREFS_PERSISTENCE_DIR = directory


def save_player_prefs(player_id: str) -> None:
    """Persist a single player's preferences to disk (synchronous).

    Safe to call from inside ``_PLAYER_PREFS_LOCK`` because file I/O
    does not touch the lock.
    """
    if _PREFS_PERSISTENCE_DIR is None:
        return

    prefs = _PLAYER_PREFS.get(player_id)
    if prefs is None:
        return

    _PREFS_PERSISTENCE_DIR.mkdir(parents=True, exist_ok=True)
    path = _PREFS_PERSISTENCE_DIR / _safe_prefs_filename(player_id)

    try:
        payload = {"version": 1, "player_id": player_id, "prefs": dict(prefs)}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        _logger.exception("Failed to save prefs for player %s", player_id)


def load_all_player_prefs() -> int:
    """Load all player-prefs JSON files from the persistence directory.

    Existing in-memory prefs are **not** overwritten — only players that
    don't already have prefs loaded get populated from disk.

    Returns:
        Number of players whose prefs were loaded.
    """
    if _PREFS_PERSISTENCE_DIR is None:
        return 0
    if not _PREFS_PERSISTENCE_DIR.is_dir():
        return 0

    loaded = 0
    for path in sorted(_PREFS_PERSISTENCE_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            player_id = data.get("player_id", _player_id_from_prefs_filename(path.name))
            prefs: dict[str, str] = {
                str(k): str(v) for k, v in data.get("prefs", {}).items()
            }
            if player_id and player_id not in _PLAYER_PREFS:
                _PLAYER_PREFS[player_id] = prefs
                loaded += 1
        except Exception:
            _logger.exception("Skipping corrupt player-prefs file: %s", path)

    if loaded:
        _logger.info(
            "Loaded prefs for %d player(s) from %s", loaded, _PREFS_PERSISTENCE_DIR
        )
    return loaded


async def _get_current_track(ctx: CommandContext) -> Any | None:
    if ctx.player_id == "-" or ctx.playlist_manager is None:
        return None

    playlist = ctx.playlist_manager.get(ctx.player_id)
    if playlist is None:
        return None

    return playlist.current_track


def _track_value(track: Any | None, key: str) -> Any:
    if track is None:
        if key == "duration":
            return 0
        return ""

    if key == "artist":
        return getattr(track, "artist_name", getattr(track, "artist", "")) or ""
    if key == "album":
        return getattr(track, "album_title", getattr(track, "album", "")) or ""
    if key in {"title", "current_title"}:
        return getattr(track, "title", "") or ""
    if key == "duration":
        duration_ms = getattr(track, "duration_ms", 0) or 0
        return duration_ms / 1000.0
    if key == "path":
        return getattr(track, "path", "") or ""
    if key == "remote":
        return getattr(track, "path", "") or ""
    if key == "genre":
        return getattr(track, "genre", "") or ""

    return ""


async def _cmd_cursonginfo(ctx: CommandContext, key: str) -> dict[str, Any]:
    track = await _get_current_track(ctx)
    value = _track_value(track, key)

    # Playlist tracks do not carry genres; resolve it from DB when possible.
    if key == "genre" and value == "" and track is not None:
        track_id = getattr(track, "id", getattr(track, "track_id", None))
        parsed_track_id = _parse_int(track_id)
        if parsed_track_id is not None:
            conn = ctx.music_library._db._require_conn()
            cursor = await conn.execute(
                """
                SELECT g.name
                FROM track_genres tg
                JOIN genres g ON g.id = tg.genre_id
                WHERE tg.track_id = ?
                ORDER BY g.name COLLATE NOCASE
                LIMIT 1
                """,
                (parsed_track_id,),
            )
            row = await cursor.fetchone()
            if row is not None and row[0] is not None:
                value = str(row[0])

    return {f"_{key}": value}


def _parse_int(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


async def cmd_version(ctx: CommandContext, params: list[Any]) -> dict[str, Any]:
    return {"_version": VERSION}


async def cmd_info(ctx: CommandContext, params: list[Any]) -> dict[str, Any]:
    if len(params) < 4:
        return {}

    if str(params[1]).lower() != "total":
        return {}

    query_key = str(params[2]).lower()
    db = ctx.music_library._db

    if query_key == "albums":
        return {"_albums": await db.count_albums()}
    if query_key == "artists":
        return {"_artists": await db.count_artists()}
    if query_key == "songs":
        return {"_songs": await db.count_tracks()}
    if query_key == "genres":
        return {"_genres": await db.count_genres()}
    if query_key == "duration":
        conn = db._require_conn()
        cursor = await conn.execute("SELECT COALESCE(SUM(duration_ms), 0) AS total_ms FROM tracks")
        row = await cursor.fetchone()
        total_ms = int(row["total_ms"]) if row else 0
        return {"_duration": total_ms // 1000}

    return {}


async def cmd_years(ctx: CommandContext, params: list[Any]) -> dict[str, Any]:
    start, items = parse_start_items(params)
    years = await ctx.music_library._db.get_distinct_years()

    if start < 0:
        start = 0
    if items < 0:
        items = 0

    page = years[start : start + items]
    years_loop = [{"year": year, "favorites_url": f"db:year.id={year}"} for year in page]

    return {
        "count": len(years),
        "years_loop": years_loop,
    }


async def cmd_works(ctx: CommandContext, params: list[Any]) -> dict[str, Any]:
    return {
        "count": 0,
        "works_loop": [],
    }


async def cmd_songs(ctx: CommandContext, params: list[Any]) -> dict[str, Any]:
    alias_params = list(params)
    alias_params[0] = "titles"
    return await cmd_titles(ctx, alias_params)


async def cmd_tracks(ctx: CommandContext, params: list[Any]) -> dict[str, Any]:
    alias_params = list(params)
    alias_params[0] = "titles"
    return await cmd_titles(ctx, alias_params)


async def cmd_songinfo(ctx: CommandContext, params: list[Any]) -> dict[str, Any]:
    alias_params = list(params)
    alias_params[0] = "titles"
    return await cmd_titles(ctx, alias_params)


async def cmd_artist(ctx: CommandContext, params: list[Any]) -> dict[str, Any]:
    return await _cmd_cursonginfo(ctx, "artist")


async def cmd_album(ctx: CommandContext, params: list[Any]) -> dict[str, Any]:
    return await _cmd_cursonginfo(ctx, "album")


async def cmd_title(ctx: CommandContext, params: list[Any]) -> dict[str, Any]:
    return await _cmd_cursonginfo(ctx, "title")


async def cmd_current_title(ctx: CommandContext, params: list[Any]) -> dict[str, Any]:
    return await _cmd_cursonginfo(ctx, "current_title")


async def cmd_duration(ctx: CommandContext, params: list[Any]) -> dict[str, Any]:
    return await _cmd_cursonginfo(ctx, "duration")


async def cmd_path(ctx: CommandContext, params: list[Any]) -> dict[str, Any]:
    return await _cmd_cursonginfo(ctx, "path")


async def cmd_genre(ctx: CommandContext, params: list[Any]) -> dict[str, Any]:
    return await _cmd_cursonginfo(ctx, "genre")


async def cmd_remote(ctx: CommandContext, params: list[Any]) -> dict[str, Any]:
    return await _cmd_cursonginfo(ctx, "remote")


async def cmd_name(ctx: CommandContext, params: list[Any]) -> dict[str, Any]:
    if ctx.player_id == "-":
        return {"_name": ""}

    player = await ctx.player_registry.get_by_mac(ctx.player_id)
    if player is None:
        return {"_name": ""}

    if len(params) >= 2 and str(params[1]) != "?":
        new_name = str(params[1])
        if hasattr(player, "info") and hasattr(player.info, "name"):
            player.info.name = new_name

    return {"_name": getattr(player, "name", "")}


async def cmd_connected(ctx: CommandContext, params: list[Any]) -> dict[str, Any]:
    if ctx.player_id == "-":
        return {"_connected": 0}

    player = await ctx.player_registry.get_by_mac(ctx.player_id)
    if player is None:
        return {"_connected": 0}

    connected = True
    if hasattr(player, "is_connected"):
        maybe_connected = getattr(player, "is_connected")
        connected = bool(maybe_connected() if callable(maybe_connected) else maybe_connected)

    return {"_connected": 1 if connected else 0}


async def cmd_signalstrength(ctx: CommandContext, params: list[Any]) -> dict[str, Any]:
    if ctx.player_id == "-":
        return {"_signalstrength": 0}

    player = await ctx.player_registry.get_by_mac(ctx.player_id)
    if player is None:
        return {"_signalstrength": 0}

    status = getattr(player, "status", None)
    value = getattr(status, "signal_strength", 0) if status is not None else 0
    return {"_signalstrength": int(value)}


def _rating_item_key(track: Any | None, params: list[Any]) -> str | None:
    if len(params) >= 2 and str(params[1]) != "?":
        return str(params[1])

    if track is None:
        return None

    track_id = getattr(track, "id", getattr(track, "track_id", None))
    if track_id is not None:
        return str(track_id)

    path = getattr(track, "path", "")
    if path:
        return str(path)

    return None


async def cmd_rating(ctx: CommandContext, params: list[Any]) -> dict[str, Any]:
    track = await _get_current_track(ctx)
    item_key = _rating_item_key(track, params)

    if item_key is None:
        return {"_rating": 0}

    is_query = len(params) >= 3 and str(params[2]) == "?"
    if is_query:
        async with _TRACK_RATINGS_LOCK:
            return {"_rating": _TRACK_RATINGS.get(item_key, 0)}

    if len(params) < 3:
        return {"_rating": 0}

    parsed = _parse_int(params[2])
    if parsed is None:
        return {"error": "Invalid rating"}

    rating = max(0, min(parsed, 100))
    async with _TRACK_RATINGS_LOCK:
        _TRACK_RATINGS[item_key] = rating

    return {"_rating": rating}


async def cmd_playlists(ctx: CommandContext, params: list[Any]) -> dict[str, Any]:
    from resonance.core.playlist_formats import parse_m3u
    from resonance.web.handlers.playlist import (
        _m3u_path_for_name,
        delete_saved_playlist,
        list_saved_playlists,
        load_saved_playlist_tracks,
        rename_saved_playlist,
    )

    subcommand = str(params[1]).lower() if len(params) > 1 else ""
    tagged = parse_tagged_params(params[2:]) if len(params) > 2 else {}

    # ---- delete ----
    if subcommand == "delete":
        playlist_id = tagged.get("playlist_id", "")
        if not playlist_id:
            # Try positional: playlists delete <name>
            playlist_id = str(params[2]) if len(params) > 2 else ""
        if playlist_id:
            delete_saved_playlist(playlist_id)
        return {}

    # ---- rename ----
    if subcommand == "rename":
        playlist_id = tagged.get("playlist_id", "")
        new_name = tagged.get("newname", "") or tagged.get("dry_run", "")
        if playlist_id and new_name:
            rename_saved_playlist(playlist_id, new_name)
        return {}

    # ---- edit / new ----
    if subcommand in {"edit", "new"}:
        return {}

    # ---- tracks ----
    if subcommand == "tracks":
        playlist_id = tagged.get("playlist_id", "")
        if not playlist_id:
            playlist_id = str(params[2]) if len(params) > 2 else ""

        if not playlist_id:
            return {"count": 0, "playlisttracks_loop": []}

        entries = load_saved_playlist_tracks(playlist_id)
        if entries is None:
            return {"count": 0, "playlisttracks_loop": []}

        start = int(tagged.get("_index", 0) or 0)
        quantity = int(tagged.get("_quantity", len(entries)) or len(entries))
        page = entries[start : start + quantity]

        tracks_loop: list[dict[str, Any]] = []
        for i, entry in enumerate(page):
            item: dict[str, Any] = {
                "playlist index": start + i,
                "title": entry.title or Path(entry.path).stem,
                "url": entry.path,
            }
            if entry.artist:
                item["artist"] = entry.artist
            if entry.album:
                item["album"] = entry.album
            if entry.duration_seconds > 0:
                item["duration"] = entry.duration_seconds
            tracks_loop.append(item)

        return {
            "count": len(entries),
            "playlisttracks_loop": tracks_loop,
        }

    # ---- list (default) ----
    search = tagged.get("search", "").lower()
    all_playlists = list_saved_playlists()

    if search:
        all_playlists = [p for p in all_playlists if search in p["playlist"].lower()]

    start = int(tagged.get("_index", 0) or 0)
    quantity = int(tagged.get("_quantity", len(all_playlists)) or len(all_playlists))
    page = all_playlists[start : start + quantity]

    return {
        "count": len(all_playlists),
        "playlists_loop": page,
    }


async def cmd_tags(ctx: CommandContext, params: list[Any]) -> dict[str, Any]:
    return {
        "count": 0,
        "tags_loop": [],
    }


async def cmd_libraries(ctx: CommandContext, params: list[Any]) -> dict[str, Any]:
    if len(params) > 1 and str(params[1]).lower() == "getid":
        return {"_id": "0"}

    return {
        "count": 1,
        "libraries_loop": [
            {
                "id": "0",
                "name": "Default",
                "enabled": 1,
            }
        ],
    }


async def cmd_mediafolder(ctx: CommandContext, params: list[Any]) -> dict[str, Any]:
    start, items = parse_start_items(params)
    folders = await ctx.music_library.get_music_folders()

    if start < 0:
        start = 0
    if items < 0:
        items = 0

    page = folders[start : start + items]
    folder_loop = [{"id": start + idx, "name": value} for idx, value in enumerate(page)]

    return {
        "count": len(folders),
        "folder_loop": folder_loop,
    }


async def cmd_musicfolder(ctx: CommandContext, params: list[Any]) -> dict[str, Any]:
    return await cmd_mediafolder(ctx, params)


async def cmd_rescanprogress(ctx: CommandContext, params: list[Any]) -> dict[str, Any]:
    status = ctx.music_library.get_scan_status()
    return {
        "rescan": 1 if status.is_running else 0,
        "progressname": status.current_folder,
        "progressdone": status.folders_done,
        "progresstotal": status.folders_total,
    }


async def cmd_getstring(ctx: CommandContext, params: list[Any]) -> dict[str, Any]:
    token = str(params[1]) if len(params) >= 2 else ""
    return {"_getstring": token}


async def cmd_linesperscreen(ctx: CommandContext, params: list[Any]) -> dict[str, Any]:
    return {"_linesperscreen": 2}


async def cmd_playerpref(ctx: CommandContext, params: list[Any]) -> dict[str, Any]:
    if ctx.player_id == "-":
        return {"error": "No player specified"}

    if len(params) < 2:
        return {"error": "Missing player preference name"}

    if str(params[1]).lower() == "validate":
        return {"valid": 1}

    pref_name = str(params[1])

    async with _PLAYER_PREFS_LOCK:
        player_prefs = _PLAYER_PREFS.setdefault(ctx.player_id, {})

        if len(params) >= 3 and str(params[2]) == "?":
            stored_value = player_prefs.get(pref_name)
            if stored_value is not None:
                return {"_p2": stored_value}

            if ctx.streaming_server is not None:
                default_value = ctx.streaming_server.get_player_pref_default(pref_name)
                if default_value is not None:
                    return {"_p2": default_value}

            return {"_p2": ""}

        if len(params) >= 3:
            value = str(params[2])
            player_prefs[pref_name] = value

            if ctx.streaming_server is not None:
                handled = ctx.streaming_server.set_player_pref(ctx.player_id, pref_name, value)
                if handled is not None:
                    canonical_name, normalized_value = handled
                    player_prefs[canonical_name] = normalized_value
                    player_prefs[pref_name] = normalized_value
                    save_player_prefs(ctx.player_id)
                    return {"_p2": normalized_value}

            save_player_prefs(ctx.player_id)
            return {"_p2": player_prefs[pref_name]}

        if pref_name in player_prefs:
            return {"_p2": player_prefs[pref_name]}

        if ctx.streaming_server is not None:
            default_value = ctx.streaming_server.get_player_pref_default(pref_name)
            if default_value is not None:
                return {"_p2": default_value}

        return {"_p2": ""}


async def cmd_abortscan(ctx: CommandContext, params: list[Any]) -> dict[str, Any]:
    task = getattr(ctx.music_library, "_scan_task", None)
    if task is not None and not task.done():
        task.cancel()
        return {"abortscan": 1}
    return {"abortscan": 0}


async def cmd_artwork(ctx: CommandContext, params: list[Any]) -> dict[str, Any]:
    if len(params) < 2:
        return {}

    artwork_id = str(params[1])
    return {
        "artwork_url": f"http://{ctx.server_host}:{ctx.server_port}/music/{artwork_id}/cover",
    }


async def cmd_artworkspec(ctx: CommandContext, params: list[Any]) -> dict[str, Any]:
    return {}


async def cmd_readdirectory(ctx: CommandContext, params: list[Any]) -> dict[str, Any]:
    return {
        "count": 0,
        "readdirectory_loop": [],
    }


async def cmd_display(ctx: CommandContext, params: list[Any]) -> dict[str, Any]:
    """
    Legacy display command with optional low-level grfb/grfe/grfd passthrough.

    Supported forms:
    - ["display", "grfb", <code>]
    - ["display", "grfe"]  # clear default framebuffer
    - ["display", "grfe", "clear", <bytes?>]
    - ["display", "grfe", <hex_bitmap>, <offset?>, <param?>, <transition?>]
    - ["display", "grfd"]  # clear legacy framebuffer with defaults
    - ["display", "grfd", "clear", <bytes?>, <offset?>]
    - ["display", "grfd", <hex_bitmap>, <offset?>]
    """
    if ctx.player_id == "-" or ctx.slimproto is None or len(params) < 2:
        return {}

    subcommand = str(params[1]).lower()

    if subcommand == "grfb":
        if len(params) < 3:
            return {"error": "Missing grfb brightness code"}

        code = _parse_int(params[2])
        if code is None:
            return {"error": "Invalid grfb brightness code"}

        try:
            sent = await ctx.slimproto.set_display_brightness(ctx.player_id, code)
        except ValueError as exc:
            return {"error": str(exc)}

        if not sent:
            return {"error": "Player not found"}

        return {"_grfb": code}

    if subcommand == "grfe":
        if len(params) == 2:
            sent = await ctx.slimproto.clear_display(ctx.player_id)
            if not sent:
                return {"error": "Player not found"}
            return {"_grfe": "clear"}

        if str(params[2]).lower() == "clear":
            bitmap_size = 1280
            if len(params) >= 4:
                parsed_size = _parse_int(params[3])
                if parsed_size is None or parsed_size < 0:
                    return {"error": "Invalid grfe clear bitmap size"}
                bitmap_size = parsed_size

            try:
                sent = await ctx.slimproto.clear_display(ctx.player_id, bitmap_size=bitmap_size)
            except ValueError as exc:
                return {"error": str(exc)}

            if not sent:
                return {"error": "Player not found"}

            return {"_grfe": "clear", "bytes": bitmap_size}

        hex_bitmap = str(params[2]).strip()
        try:
            bitmap = bytes.fromhex(hex_bitmap)
        except ValueError:
            return {"error": "Invalid grfe bitmap hex payload"}

        offset = 0
        if len(params) >= 4:
            parsed_offset = _parse_int(params[3])
            if parsed_offset is None:
                return {"error": "Invalid grfe offset"}
            offset = parsed_offset

        param = 0
        if len(params) >= 5:
            parsed_param = _parse_int(params[4])
            if parsed_param is None:
                return {"error": "Invalid grfe param"}
            param = parsed_param

        transition = "c"
        if len(params) >= 6:
            transition = str(params[5])

        try:
            sent = await ctx.slimproto.send_display_bitmap(
                ctx.player_id,
                bitmap,
                offset=offset,
                transition=transition,
                param=param,
            )
        except ValueError as exc:
            return {"error": str(exc)}

        if not sent:
            return {"error": "Player not found"}

        return {
            "_grfe": "bitmap",
            "bytes": len(bitmap),
            "offset": offset,
            "param": param,
            "transition": transition,
        }

    if subcommand == "grfd":
        if len(params) == 2:
            sent = await ctx.slimproto.clear_display_framebuffer(ctx.player_id)
            if not sent:
                return {"error": "Player not found"}
            return {
                "_grfd": "clear",
                "bytes": DEFAULT_GRFD_BITMAP_BYTES,
                "offset": DEFAULT_GRFD_FRAMEBUFFER_OFFSET,
            }

        if str(params[2]).lower() == "clear":
            bitmap_size = DEFAULT_GRFD_BITMAP_BYTES
            offset = DEFAULT_GRFD_FRAMEBUFFER_OFFSET

            if len(params) >= 4:
                parsed_size = _parse_int(params[3])
                if parsed_size is None or parsed_size < 0:
                    return {"error": "Invalid grfd clear bitmap size"}
                bitmap_size = parsed_size

            if len(params) >= 5:
                parsed_offset = _parse_int(params[4])
                if parsed_offset is None:
                    return {"error": "Invalid grfd offset"}
                offset = parsed_offset

            try:
                sent = await ctx.slimproto.clear_display_framebuffer(
                    ctx.player_id,
                    bitmap_size=bitmap_size,
                    offset=offset,
                )
            except ValueError as exc:
                return {"error": str(exc)}

            if not sent:
                return {"error": "Player not found"}

            return {
                "_grfd": "clear",
                "bytes": bitmap_size,
                "offset": offset,
            }

        hex_bitmap = str(params[2]).strip()
        try:
            bitmap = bytes.fromhex(hex_bitmap)
        except ValueError:
            return {"error": "Invalid grfd bitmap hex payload"}

        offset = DEFAULT_GRFD_FRAMEBUFFER_OFFSET
        if len(params) >= 4:
            parsed_offset = _parse_int(params[3])
            if parsed_offset is None:
                return {"error": "Invalid grfd offset"}
            offset = parsed_offset

        try:
            sent = await ctx.slimproto.send_display_framebuffer(
                ctx.player_id,
                bitmap,
                offset=offset,
            )
        except ValueError as exc:
            return {"error": str(exc)}

        if not sent:
            return {"error": "Player not found"}

        return {
            "_grfd": "bitmap",
            "bytes": len(bitmap),
            "offset": offset,
        }

    return {}


async def cmd_displaynow(ctx: CommandContext, params: list[Any]) -> dict[str, Any]:
    return await cmd_display(ctx, params)


async def cmd_irenable(ctx: CommandContext, params: list[Any]) -> dict[str, Any]:
    if len(params) >= 2 and str(params[1]) == "?":
        return {"_irenable": 1}
    return {}


async def cmd_debug(ctx: CommandContext, params: list[Any]) -> dict[str, Any]:
    if len(params) >= 3 and str(params[2]) == "?":
        flag = str(params[1]) if len(params) > 1 else "debug"
        return {f"_{flag}": 0}
    return {}


async def cmd_noop(ctx: CommandContext, params: list[Any]) -> dict[str, Any]:
    return {}
