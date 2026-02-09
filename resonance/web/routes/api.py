"""
REST API Routes for Resonance.

Provides REST endpoints for the web UI and external integrations:
- /api/status: Server status
- /api/players: Player management
- /api/library/*: Library browsing and search
- /api/artwork/*: Album artwork
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request

from resonance.web.handlers.status import _lms_song_elapsed_seconds
from resonance.web.jsonrpc_helpers import build_player_item, to_dict

if TYPE_CHECKING:
    from resonance.core.library import MusicLibrary
    from resonance.core.playlist import PlaylistManager
    from resonance.player.registry import PlayerRegistry
    from resonance.streaming.server import StreamingServer

logger = logging.getLogger(__name__)

router = APIRouter(tags=["api"])

# References set during route registration
_music_library: MusicLibrary | None = None
_player_registry: PlayerRegistry | None = None
_playlist_manager: PlaylistManager | None = None
_streaming_server: StreamingServer | None = None


def register_api_routes(
    app,
    music_library: MusicLibrary,
    player_registry: PlayerRegistry,
    playlist_manager: PlaylistManager | None = None,
    streaming_server: StreamingServer | None = None,
) -> None:
    """
    Register API routes with the FastAPI app.

    Args:
        app: FastAPI application instance
        music_library: MusicLibrary for browsing/search
        player_registry: PlayerRegistry for player info
        playlist_manager: Optional PlaylistManager
        streaming_server: Optional StreamingServer for start_offset lookup
    """
    global _music_library, _player_registry, _playlist_manager, _streaming_server
    _music_library = music_library
    _player_registry = player_registry
    _playlist_manager = playlist_manager
    _streaming_server = streaming_server
    app.include_router(router)


# =============================================================================
# Server Status
# =============================================================================


@router.get("/api/status")
async def server_status() -> dict[str, Any]:
    """Get server status and basic info."""
    if _music_library is None or _player_registry is None:
        raise HTTPException(status_code=503, detail="Server not initialized")

    players = await _player_registry.get_all()

    return {
        "server": "resonance",
        "version": "0.1.0",
        "players_connected": len(players),
        "library_initialized": _music_library.initialized,
        "playlist_manager_available": _playlist_manager is not None,
    }


# =============================================================================
# Player Endpoints
# =============================================================================


@router.get("/api/players")
async def list_players() -> dict[str, Any]:
    """List all connected players."""
    if _player_registry is None:
        raise HTTPException(status_code=503, detail="Server not initialized")

    players = await _player_registry.get_all()
    players_list = [build_player_item(player) for player in players]

    return {
        "count": len(players_list),
        "players": players_list,
    }


@router.get("/api/players/{player_id}")
async def get_player(player_id: str) -> dict[str, Any]:
    """Get details for a specific player."""
    if _player_registry is None:
        raise HTTPException(status_code=503, detail="Server not initialized")

    player = await _player_registry.get_by_mac(player_id)
    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")

    return build_player_item(player)


@router.get("/api/players/{player_id}/status")
async def get_player_status(player_id: str) -> dict[str, Any]:
    """Get current playback status for a player."""
    if _player_registry is None:
        raise HTTPException(status_code=503, detail="Server not initialized")

    player = await _player_registry.get_by_mac(player_id)
    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")

    status = player.status

    # Map state to LMS mode format
    state_to_mode = {
        "PLAYING": "play",
        "PAUSED": "pause",
        "STOPPED": "stop",
        "DISCONNECTED": "stop",
        "BUFFERING": "play",
    }
    state_name = status.state.name if hasattr(status.state, "name") else "STOPPED"
    mode = state_to_mode.get(state_name, "stop")

    # -------------------------------------------------------------------------
    # LMS-style elapsed calculation (from StreamingController.pm):
    #   songtime = startOffset + songElapsedSeconds
    #
    # After a seek to position X, the player reports elapsed time relative to
    # the NEW stream start (0, 1, 2, 3...). The real track position is:
    #   actual_elapsed = start_offset + raw_elapsed
    #
    # Example: Seek to 30s → player reports 0,1,2,3... → we return 30,31,32,33...
    #
    # NO HEURISTICS needed - this is exactly how LMS does it.
    # The start_offset is set when queuing a seek and cleared when a new track starts.
    # -------------------------------------------------------------------------
    raw_elapsed_sec = _lms_song_elapsed_seconds(
        status=status,
        mode=mode,
    )

    # Get start offset from streaming server (LMS-style startOffset)
    start_offset: float = 0.0
    if _streaming_server is not None:
        try:
            start_offset = _streaming_server.get_start_offset(player_id)
        except Exception:
            start_offset = 0.0

    # Get duration for capping
    duration_sec = 0.0
    if _playlist_manager is not None:
        playlist = _playlist_manager.get(player_id)
        if playlist is not None:
            current = playlist.current_track
            if current is not None:
                duration_sec = (current.duration_ms or 0) / 1000.0

    # Calculate actual elapsed: start_offset + raw_elapsed (LMS formula)
    elapsed_sec = start_offset + raw_elapsed_sec

    # Cap elapsed to duration (never show more than 100% progress)
    if duration_sec > 0 and elapsed_sec > duration_sec:
        elapsed_sec = duration_sec

    result: dict[str, Any] = {
        "player_name": build_player_item(player)["name"],
        "player_connected": 1,
        "power": 1,
        "mode": mode,
        "time": elapsed_sec,
        "rate": 1 if mode == "play" else 0,
        "mixer volume": status.volume,
    }

    # Add playlist info if available
    if _playlist_manager is not None:
        playlist = _playlist_manager.get(player_id)
        if playlist is not None:
            result["playlist_tracks"] = len(playlist)
            result["playlist_cur_index"] = playlist.current_index
            result["playlist shuffle"] = playlist.shuffle_mode.value
            result["playlist repeat"] = playlist.repeat_mode.value

            current = playlist.current_track
            if current is not None:
                result["duration"] = (current.duration_ms or 0) / 1000.0

    return result


@router.get("/api/debug/playlist/{player_id}")
async def debug_playlist(player_id: str) -> dict[str, Any]:
    """Debug endpoint to check playlist state for a player."""
    if _playlist_manager is None:
        return {"error": "Playlist manager not available"}

    playlist = _playlist_manager.get(player_id)
    if playlist is None:
        return {"error": "No playlist for player", "player_id": player_id}

    tracks = []
    for i, track in enumerate(playlist.tracks):
        tracks.append(
            {
                "index": i,
                "id": track.id,
                "title": track.title,
                "path": track.path,
            }
        )

    return {
        "player_id": player_id,
        "current_index": playlist.current_index,
        "shuffle": playlist.shuffle_mode.value,
        "repeat": playlist.repeat_mode.value,
        "track_count": len(playlist),
        "tracks": tracks,
    }


# =============================================================================
# Library Endpoints
# =============================================================================


@router.get("/api/library/artists")
async def get_artists(offset: int = 0, limit: int = 100) -> dict[str, Any]:
    """Get list of artists from the library.

    Query params:
        offset: Starting position (default: 0)
        limit: Maximum items to return (default: 100)
    """
    if _music_library is None:
        raise HTTPException(status_code=503, detail="Library not initialized")

    db = _music_library._db
    total = await db.count_artists()
    rows = await db.list_all_artists(offset=offset, limit=limit)

    artists = []
    for row in rows:
        row_dict = to_dict(row)
        artists.append(
            {
                "id": row_dict.get("id"),
                "artist": row_dict.get("name", row_dict.get("artist", "")),
                "albums": row_dict.get("album_count", 0),
            }
        )

    return {
        "count": total,
        "artists": artists,
    }


@router.get("/api/library/albums")
async def get_albums(offset: int = 0, limit: int = 100) -> dict[str, Any]:
    """Get list of albums from the library.

    Query params:
        offset: Starting position (default: 0)
        limit: Maximum items to return (default: 100)
    """
    if _music_library is None:
        raise HTTPException(status_code=503, detail="Library not initialized")

    db = _music_library._db
    total = await db.count_albums()
    rows = await db.list_all_albums(offset=offset, limit=limit)

    albums = []
    for row in rows:
        row_dict = to_dict(row)
        albums.append(
            {
                "id": row_dict.get("id"),
                "album": row_dict.get("title", row_dict.get("album", "")),
                "artist": row_dict.get("artist_name", row_dict.get("artist", "")),
                "artist_id": row_dict.get("artist_id"),
                "year": row_dict.get("year"),
                "tracks": row_dict.get("track_count", 0),
            }
        )

    return {
        "count": total,
        "albums": albums,
    }


@router.get("/api/library/tracks")
async def get_tracks(offset: int = 0, limit: int = 200) -> dict[str, Any]:
    """Get list of tracks from the library.

    Query params:
        offset: Starting position (default: 0)
        limit: Maximum items to return (default: 200)
    """
    if _music_library is None:
        raise HTTPException(status_code=503, detail="Library not initialized")

    db = _music_library._db
    total = await db.count_tracks()
    rows = await db.list_tracks(offset=offset, limit=limit)

    tracks = []
    for row in rows:
        row_dict = to_dict(row)
        tracks.append(
            {
                "id": row_dict.get("id"),
                "title": row_dict.get("title", ""),
                "artist": row_dict.get("artist_name", row_dict.get("artist", "")),
                "album": row_dict.get("album_title", row_dict.get("album", "")),
                "duration": (row_dict.get("duration_ms") or 0) / 1000.0,
                "tracknum": row_dict.get("track_no"),
                "year": row_dict.get("year"),
            }
        )

    return {
        "count": total,
        "tracks": tracks,
    }


@router.get("/api/library/tracks/{track_id}")
async def get_track(track_id: int) -> dict[str, Any]:
    """Get a single track by ID."""
    if _music_library is None:
        raise HTTPException(status_code=503, detail="Library not initialized")

    db = _music_library._db
    row = await db.get_track_by_id(track_id)

    if row is None:
        raise HTTPException(status_code=404, detail="Track not found")

    row_dict = to_dict(row)
    return {
        "id": row_dict.get("id"),
        "title": row_dict.get("title", ""),
        "artist": row_dict.get("artist_name", row_dict.get("artist", "")),
        "album": row_dict.get("album_title", row_dict.get("album", "")),
        "path": row_dict.get("path", ""),
        "duration": (row_dict.get("duration_ms") or 0) / 1000.0,
        "tracknum": row_dict.get("track_no"),
        "disc": row_dict.get("disc_no"),
        "year": row_dict.get("year"),
        "sample_rate": row_dict.get("sample_rate"),
        "bit_depth": row_dict.get("bit_depth"),
        "bitrate": row_dict.get("bitrate"),
        "channels": row_dict.get("channels"),
    }


@router.delete("/api/library/albums/{album_id}")
async def delete_album(album_id: int) -> dict[str, Any]:
    """Delete an album and all its tracks from the library.

    This permanently removes:
    - All tracks belonging to the album
    - The album record itself
    - Any orphaned artists/genres with no remaining tracks

    Use this to clean up test data or remove unwanted albums before re-scanning.
    """
    if _music_library is None:
        raise HTTPException(status_code=503, detail="Library not initialized")

    db = _music_library._db

    # Check if album exists
    album = await db.get_album_by_id(album_id)
    if album is None:
        raise HTTPException(status_code=404, detail="Album not found")

    # Get album info for response before deletion
    # AlbumRow is a dataclass, access attributes directly
    album_title = (
        getattr(album, "title", "Unknown")
        if hasattr(album, "title")
        else (album.get("title", "Unknown") if isinstance(album, dict) else "Unknown")
    )

    # Delete album and tracks
    result = await db.delete_album(album_id, cleanup_orphans=True)

    return {
        "deleted": True,
        "album_id": album_id,
        "album_title": album_title,
        "tracks_deleted": result.get("tracks_deleted", 0),
        "orphan_albums_deleted": result.get("orphan_albums_deleted", 0),
        "orphan_artists_deleted": result.get("orphan_artists_deleted", 0),
        "orphan_genres_deleted": result.get("orphan_genres_deleted", 0),
    }


@router.delete("/api/library/tracks/{track_id}")
async def delete_track(track_id: int) -> dict[str, Any]:
    """Delete a single track from the library.

    This removes:
    - The track record
    - Any orphaned albums/artists/genres with no remaining tracks
    """
    if _music_library is None:
        raise HTTPException(status_code=503, detail="Library not initialized")

    db = _music_library._db

    # Get track info before deletion
    track = await db.get_track_by_id(track_id)
    if track is None:
        raise HTTPException(status_code=404, detail="Track not found")

    # TrackRow is a dataclass, access attributes directly
    track_path = (
        getattr(track, "path", "")
        if hasattr(track, "path")
        else (track.get("path", "") if isinstance(track, dict) else "")
    )
    track_title = (
        getattr(track, "title", "Unknown")
        if hasattr(track, "title")
        else (track.get("title", "Unknown") if isinstance(track, dict) else "Unknown")
    )

    # Delete by path
    deleted = await db.delete_track_by_path(track_path)
    if not deleted:
        raise HTTPException(status_code=404, detail="Track not found")

    # Cleanup orphans
    orphan_result = await db.cleanup_orphans()
    await db._require_conn().commit()

    return {
        "deleted": True,
        "track_id": track_id,
        "track_title": track_title,
        "orphan_albums_deleted": orphan_result.get("orphan_albums_deleted", 0),
        "orphan_artists_deleted": orphan_result.get("orphan_artists_deleted", 0),
        "orphan_genres_deleted": orphan_result.get("orphan_genres_deleted", 0),
    }


@router.get("/api/library/search")
async def search_library(q: str, limit: int = 50) -> dict[str, Any]:
    """Search the library.

    Query params:
        q: Search query
        limit: Maximum results per category (default: 50)
    """
    if _music_library is None:
        raise HTTPException(status_code=503, detail="Library not initialized")

    db = _music_library._db

    # Search all categories
    artists = await db.search_artists(term=q, offset=0, limit=limit)
    albums = await db.search_albums(term=q, offset=0, limit=limit)
    tracks = await db.search_tracks(term=q, offset=0, limit=limit)

    return {
        "query": q,
        "artists": [
            {
                "id": r.get("id") if isinstance(r, dict) else r["id"],
                "artist": r.get("name", r.get("artist", ""))
                if isinstance(r, dict)
                else r.get("name", ""),
            }
            for r in artists
        ],
        "albums": [
            {
                "id": r.get("id") if isinstance(r, dict) else r["id"],
                "album": r.get("title", r.get("album", ""))
                if isinstance(r, dict)
                else r.get("title", ""),
            }
            for r in albums
        ],
        "tracks": [
            {
                "id": r.get("id") if isinstance(r, dict) else r["id"],
                "title": r.get("title", "") if isinstance(r, dict) else r.get("title", ""),
            }
            for r in tracks
        ],
    }


# =============================================================================
# Library Management
# =============================================================================


@router.get("/api/library/folders")
async def get_music_folders() -> dict[str, Any]:
    """Get the list of configured music folders."""
    if _music_library is None:
        raise HTTPException(status_code=503, detail="Library not initialized")

    folders = await _music_library.get_music_folders()
    return {"folders": folders}


@router.post("/api/library/folders")
async def add_music_folder(request: Request) -> dict[str, Any]:
    """Add a music folder.

    Request body: {"path": "/path/to/music"}
    """
    if _music_library is None:
        raise HTTPException(status_code=503, detail="Library not initialized")

    body = await request.json()
    path = body.get("path")
    if not path:
        raise HTTPException(status_code=400, detail="Missing 'path' in request body")

    await _music_library.add_music_folder(path)
    folders = await _music_library.get_music_folders()
    return {"folders": folders, "added": path}


@router.put("/api/library/folders")
async def set_music_folders(request: Request) -> dict[str, Any]:
    """Replace all music folders.

    Request body: {"paths": ["/path1", "/path2"]}
    """
    if _music_library is None:
        raise HTTPException(status_code=503, detail="Library not initialized")

    body = await request.json()
    paths = body.get("paths", [])

    await _music_library.set_music_folders(paths)
    return {"folders": paths}


@router.delete("/api/library/folders")
async def remove_music_folder(request: Request) -> dict[str, Any]:
    """Remove a music folder.

    Request body: {"path": "/path/to/music"}
    """
    if _music_library is None:
        raise HTTPException(status_code=503, detail="Library not initialized")

    body = await request.json()
    path = body.get("path")
    if not path:
        raise HTTPException(status_code=400, detail="Missing 'path' in request body")

    await _music_library.remove_music_folder(path)
    folders = await _music_library.get_music_folders()
    return {"folders": folders, "removed": path}


@router.get("/api/library/scan")
async def get_scan_status() -> dict[str, Any]:
    """Get the current scan status."""
    if _music_library is None:
        raise HTTPException(status_code=503, detail="Library not initialized")

    status = _music_library.scan_status
    return {
        "is_running": status.is_running,
        "progress": status.progress,
        "current_folder": status.current_folder,
        "folders_total": status.folders_total,
        "folders_done": status.folders_done,
        "tracks_found": status.tracks_found,
        "errors": status.errors,
    }


@router.post("/api/library/scan")
async def trigger_scan() -> dict[str, Any]:
    """Trigger a background library scan.

    This scans all configured music folders and updates the database.
    The scan runs in the background - use GET /library/scan to check status.
    """
    if _music_library is None:
        raise HTTPException(status_code=503, detail="Library not initialized")

    await _music_library.start_scan()
    return {"status": "scan_started"}
