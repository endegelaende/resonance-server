"""
Playlist Command Handlers — Facade.

This module is the public entry point for all ``playlist`` sub-commands.
It contains only the dispatch table (``cmd_playlist``) and re-exports of
symbols that external modules import from ``resonance.web.handlers.playlist``.

All implementation logic has been split into focused sub-modules:

- ``playlist_helpers``      — shared state, filesystem utils, parsers, resolvers
- ``playlist_playback``     — play, pause, stop, index, jump, stream start
- ``playlist_mutation``     — add, insert, delete, clear, move, shuffle, repeat, bulk ops
- ``playlist_query``        — metadata queries, tracks list, event-style noops
- ``playlist_persistence``  — save, load, preview, resume
"""

from __future__ import annotations

import logging
from typing import Any

from resonance.web.handlers import CommandContext

# ── Re-exports: playlist_helpers (public API + internal symbols used externally) ──
from resonance.web.handlers.playlist_helpers import (  # noqa: F401
    _m3u_path_for_name,
    _resolve_track,
    _resolve_track_rows_from_filters,
    _row_to_library_track,
    _stream_flags_for_explicit_restart,
    configure_saved_playlists_dir,
    delete_saved_playlist,
    get_saved_playlists_dir,
    list_saved_playlists,
    load_saved_playlist_tracks,
    rename_saved_playlist,
)

# ── Sub-module imports for dispatch table ──
from resonance.web.handlers.playlist_mutation import (
    _playlist_add,
    _playlist_addalbum,
    _playlist_addtracks,
    _playlist_clear,
    _playlist_delete,
    _playlist_deletealbum,
    _playlist_deleteitem,
    _playlist_deletetracks,
    _playlist_insert,
    _playlist_insertalbum,
    _playlist_insertlist,
    _playlist_inserttracks,
    _playlist_loadalbum,
    _playlist_loadtracks,
    _playlist_move,
    _playlist_playalbum,
    _playlist_repeat,
    _playlist_shuffle,
    _playlist_zap,
)
from resonance.web.handlers.playlist_persistence import (
    _playlist_load,
    _playlist_preview,
    _playlist_resume,
    _playlist_save,
)

# ── Re-exports: playlist_playback (used by playback.py, tests) ──
from resonance.web.handlers.playlist_playback import (  # noqa: F401
    _playlist_index,
    _playlist_jump,
    _playlist_pause,
    _playlist_play,
    _playlist_stop,
    _start_track_stream,
    _try_use_prefetch_fast_path,
)
from resonance.web.handlers.playlist_query import (
    _playlist_album,
    _playlist_artist,
    _playlist_cant_open,
    _playlist_duration,
    _playlist_genre,
    _playlist_load_done,
    _playlist_modified,
    _playlist_name,
    _playlist_newsong,
    _playlist_open,
    _playlist_path,
    _playlist_playlistsinfo,
    _playlist_remote,
    _playlist_sync,
    _playlist_title,
    _playlist_tracks,
    _playlist_url,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Dispatch facade
# =============================================================================


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
        # Playback
        "play": _playlist_play,
        "pause": _playlist_pause,
        "stop": _playlist_stop,
        "index": _playlist_index,
        "jump": _playlist_jump,
        # Mutation — single track
        "add": _playlist_add,
        "append": _playlist_add,
        "insert": _playlist_insert,
        "insertlist": _playlist_insertlist,
        "delete": _playlist_delete,
        "deleteitem": _playlist_deleteitem,
        "clear": _playlist_clear,
        "move": _playlist_move,
        "shuffle": _playlist_shuffle,
        "repeat": _playlist_repeat,
        # Mutation — bulk / album aliases
        "addtracks": _playlist_addtracks,
        "addalbum": _playlist_addalbum,
        "inserttracks": _playlist_inserttracks,
        "insertalbum": _playlist_insertalbum,
        "loadtracks": _playlist_loadtracks,
        "playtracks": _playlist_loadtracks,
        "loadalbum": _playlist_loadalbum,
        "playalbum": _playlist_playalbum,
        "deletetracks": _playlist_deletetracks,
        "deletealbum": _playlist_deletealbum,
        "zap": _playlist_zap,
        # Persistence
        "load": _playlist_load,
        "save": _playlist_save,
        "preview": _playlist_preview,
        "resume": _playlist_resume,
        # Query — metadata
        "album": _playlist_album,
        "artist": _playlist_artist,
        "duration": _playlist_duration,
        "genre": _playlist_genre,
        "modified": _playlist_modified,
        "name": _playlist_name,
        "path": _playlist_path,
        "remote": _playlist_remote,
        "title": _playlist_title,
        "url": _playlist_url,
        "tracks": _playlist_tracks,
        "playlistsinfo": _playlist_playlistsinfo,
        # Event-style no-ops
        "load_done": _playlist_load_done,
        "newsong": _playlist_newsong,
        "open": _playlist_open,
        "sync": _playlist_sync,
        "cant_open": _playlist_cant_open,
    }

    handler = handlers.get(subcommand)
    if handler is None:
        return {"error": f"Unknown playlist subcommand: {subcommand}"}

    return await handler(ctx, params)
