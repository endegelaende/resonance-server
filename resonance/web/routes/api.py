"""
REST API Routes for Resonance.

Provides REST endpoints for the web UI and external integrations:
- /api/status: Server status
- /api/players: Player management
- /api/library/*: Library browsing and search
- /api/settings: Server settings management
- /api/artwork/*: Album artwork
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from resonance.plugin import PluginManifest, SettingDefinition
from resonance.web.handlers.status import _lms_song_elapsed_seconds
from resonance.web.jsonrpc_helpers import build_player_item, to_dict

if TYPE_CHECKING:
    from resonance.core.library import MusicLibrary
    from resonance.core.playlist import PlaylistManager
    from resonance.player.registry import PlayerRegistry
    from resonance.plugin_installer import PluginInstaller
    from resonance.plugin_manager import PluginManager
    from resonance.plugin_repository import PluginRepository
    from resonance.streaming.server import StreamingServer

logger = logging.getLogger(__name__)

router = APIRouter(tags=["api"])

# References set during route registration
_music_library: MusicLibrary | None = None
_player_registry: PlayerRegistry | None = None
_playlist_manager: PlaylistManager | None = None
_streaming_server: StreamingServer | None = None
_plugin_manager: PluginManager | None = None
_plugin_installer: PluginInstaller | None = None
_plugin_repository: PluginRepository | None = None


def register_api_routes(
    app,
    music_library: MusicLibrary,
    player_registry: PlayerRegistry,
    playlist_manager: PlaylistManager | None = None,
    streaming_server: StreamingServer | None = None,
    plugin_manager: PluginManager | None = None,
    plugin_installer: PluginInstaller | None = None,
    plugin_repository: PluginRepository | None = None,
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
    global _plugin_manager, _plugin_installer, _plugin_repository
    _music_library = music_library
    _player_registry = player_registry
    _playlist_manager = playlist_manager
    _streaming_server = streaming_server
    _plugin_manager = plugin_manager
    _plugin_installer = plugin_installer
    _plugin_repository = plugin_repository
    app.include_router(router)


def _require_plugin_manager() -> PluginManager:
    if _plugin_manager is None:
        raise HTTPException(status_code=503, detail="Plugin manager not initialized")
    return _plugin_manager


def _require_plugin_installer() -> PluginInstaller:
    if _plugin_installer is None:
        raise HTTPException(status_code=503, detail="Plugin installer not initialized")
    return _plugin_installer


def _require_plugin_repository() -> PluginRepository:
    if _plugin_repository is None:
        raise HTTPException(status_code=503, detail="Plugin repository not initialized")
    return _plugin_repository


def _settings_path_for(plugin_name: str) -> Path:
    return Path("data/plugins") / plugin_name / "settings.json"


def _load_settings_from_disk(manifest: PluginManifest) -> dict[str, Any]:
    values = {definition.key: definition.default for definition in manifest.settings_defs}
    path = _settings_path_for(manifest.name)
    if not path.is_file():
        return values
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        for definition in manifest.settings_defs:
            if definition.key not in payload:
                continue
            value = payload[definition.key]
            ok, _ = definition.validate(value)
            if ok:
                values[definition.key] = value
    except Exception as exc:
        logger.warning("Failed to load plugin settings for %s: %s", manifest.name, exc)
    return values


def _save_settings_to_disk(manifest: PluginManifest, values: dict[str, Any]) -> None:
    path = _settings_path_for(manifest.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"_version": 1, "_plugin_version": manifest.version}
    for definition in manifest.settings_defs:
        payload[definition.key] = values.get(definition.key, definition.default)
    tmp = path.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        tmp.replace(path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _parse_typed_value(definition: SettingDefinition, raw: Any) -> Any:
    if definition.type == "int":
        if isinstance(raw, int) and not isinstance(raw, bool):
            return raw
        return int(str(raw))
    if definition.type == "float":
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            return float(raw)
        return float(str(raw))
    if definition.type == "bool":
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"Invalid boolean value for '{definition.key}': {raw}")
    if definition.type in {"string", "select"}:
        return str(raw)
    return raw


def _mask_secret(value: Any) -> Any:
    if not isinstance(value, str):
        return "****" if value is not None else value
    if not value:
        return value
    if len(value) <= 4:
        return "*" * len(value)
    return "*" * max(4, len(value) - 4) + value[-4:]


def _mask_values(
    definitions: tuple[SettingDefinition, ...], values: dict[str, Any]
) -> dict[str, Any]:
    masked = dict(values)
    for definition in definitions:
        if definition.secret:
            masked[definition.key] = _mask_secret(masked.get(definition.key))
    return masked


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
# Plugins
# =============================================================================


@router.get("/api/plugins")
async def list_plugins() -> dict[str, Any]:
    manager = _require_plugin_manager()
    plugins = manager.list_plugin_info()
    return {
        "count": len(plugins),
        "plugins": plugins,
        "restart_required": manager.restart_required,
    }


@router.get("/api/plugins/{plugin_name}/settings")
async def get_plugin_settings(plugin_name: str) -> dict[str, Any]:
    manager = _require_plugin_manager()
    manifest = manager.get_manifest(plugin_name)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"Unknown plugin: {plugin_name}")

    values = _load_settings_from_disk(manifest)
    return {
        "plugin_name": plugin_name,
        "definitions": [definition.to_dict() for definition in manifest.settings_defs],
        "values": _mask_values(manifest.settings_defs, values),
    }


@router.put("/api/plugins/{plugin_name}/settings")
async def update_plugin_settings(plugin_name: str, request: Request) -> dict[str, Any]:
    manager = _require_plugin_manager()
    manifest = manager.get_manifest(plugin_name)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"Unknown plugin: {plugin_name}")

    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object")

    current_values = _load_settings_from_disk(manifest)
    defs_by_key = {definition.key: definition for definition in manifest.settings_defs}
    changed_keys: list[str] = []
    restart_required = False

    try:
        for key, raw_value in body.items():
            definition = defs_by_key.get(key)
            if definition is None:
                raise KeyError(key)
            typed_value = _parse_typed_value(definition, raw_value)
            ok, error = definition.validate(typed_value)
            if not ok:
                raise ValueError(error)
            if current_values.get(key, definition.default) != typed_value:
                changed_keys.append(key)
                if definition.restart_required:
                    restart_required = True
            current_values[key] = typed_value
    except KeyError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown setting for plugin '{plugin_name}': {exc}",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    _save_settings_to_disk(manifest, current_values)
    plugin_ctx = manager.get_context(plugin_name)
    if plugin_ctx is not None and changed_keys:
        try:
            plugin_ctx.set_settings({key: current_values[key] for key in changed_keys})
        except Exception:
            pass

    if restart_required:
        manager.mark_restart_required()

    return {
        "plugin_name": plugin_name,
        "updated": sorted(changed_keys),
        "restart_required": restart_required,
        "definitions": [definition.to_dict() for definition in manifest.settings_defs],
        "values": _mask_values(manifest.settings_defs, current_values),
    }


@router.post("/api/plugins/{plugin_name}/enable")
async def enable_plugin(plugin_name: str) -> dict[str, Any]:
    manager = _require_plugin_manager()
    if manager.get_manifest(plugin_name) is None:
        raise HTTPException(status_code=404, detail=f"Unknown plugin: {plugin_name}")
    manager.set_plugin_enabled(plugin_name, True)
    return {
        "name": plugin_name,
        "state": manager.get_plugin_state(plugin_name),
        "restart_required": manager.restart_required,
    }


@router.post("/api/plugins/{plugin_name}/disable")
async def disable_plugin(plugin_name: str) -> dict[str, Any]:
    manager = _require_plugin_manager()
    if manager.get_manifest(plugin_name) is None:
        raise HTTPException(status_code=404, detail=f"Unknown plugin: {plugin_name}")
    manager.set_plugin_enabled(plugin_name, False)
    return {
        "name": plugin_name,
        "state": manager.get_plugin_state(plugin_name),
        "restart_required": manager.restart_required,
    }


@router.post("/api/plugins/install")
async def install_plugin(request: Request) -> dict[str, Any]:
    manager = _require_plugin_manager()
    installer = _require_plugin_installer()

    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object")
    url = body.get("url")
    sha256 = body.get("sha256")
    if not isinstance(url, str) or not isinstance(sha256, str):
        raise HTTPException(status_code=400, detail="Request body must include 'url' and 'sha256'")

    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            zip_data = response.content
        plugin_name = installer.install_from_zip(zip_data, sha256)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Install failed: {exc}")

    manager.state_manager.set_enabled(plugin_name, True)
    manager.mark_restart_required()
    await manager.discover()

    return {
        "name": plugin_name,
        "installed": True,
        "restart_required": True,
    }


@router.post("/api/plugins/{plugin_name}/uninstall")
async def uninstall_plugin(plugin_name: str) -> dict[str, Any]:
    manager = _require_plugin_manager()
    installer = _require_plugin_installer()

    if manager.is_core_plugin(plugin_name):
        raise HTTPException(
            status_code=400, detail=f"Core plugin cannot be uninstalled: {plugin_name}"
        )
    if not installer.uninstall(plugin_name):
        raise HTTPException(status_code=404, detail=f"Plugin not installed: {plugin_name}")

    manager.mark_restart_required()
    await manager.discover()
    return {
        "name": plugin_name,
        "uninstalled": True,
        "restart_required": True,
    }


@router.get("/api/plugins/repository")
async def get_repository(force_refresh: bool = False) -> dict[str, Any]:
    manager = _require_plugin_manager()
    repository = _require_plugin_repository()

    available = await repository.fetch_available(force_refresh=force_refresh)
    installed = {manifest.name: manifest.version for manifest in manager.manifests}
    core_plugins = {
        manifest.name for manifest in manager.manifests if manifest.plugin_type == "core"
    }
    compared = repository.compare_with_installed(available, installed, core_plugins)
    return {"count": len(compared), "plugins": compared}


@router.post("/api/plugins/install-from-repo")
async def install_from_repository(request: Request) -> dict[str, Any]:
    manager = _require_plugin_manager()
    installer = _require_plugin_installer()
    repository = _require_plugin_repository()

    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object")
    plugin_name = body.get("name")
    if not isinstance(plugin_name, str) or not plugin_name.strip():
        raise HTTPException(status_code=400, detail="Request body must include 'name'")

    if manager.is_core_plugin(plugin_name):
        raise HTTPException(
            status_code=400,
            detail=f"Core plugin cannot be installed from repository: {plugin_name}",
        )

    try:
        available = await repository.fetch_available()
        entry = next((item for item in available if item.name == plugin_name), None)
        if entry is None:
            raise HTTPException(
                status_code=404, detail=f"Plugin not found in repository: {plugin_name}"
            )

        compatible, reason = repository.check_compatible(entry)
        if not compatible:
            raise HTTPException(status_code=400, detail=reason)

        zip_data = await repository.download_plugin(entry)
        installed_name = installer.install_from_zip(zip_data, entry.sha256)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Install from repository failed: {exc}")

    manager.state_manager.set_enabled(installed_name, True)
    manager.mark_restart_required()
    await manager.discover()

    return {
        "name": installed_name,
        "version": entry.version,
        "installed": True,
        "restart_required": True,
    }


# =============================================================================
# Plugin UI (Server-Driven UI)
# =============================================================================


@router.get("/api/plugins/ui-registry")
async def get_ui_registry() -> list[dict[str, Any]]:
    """Return plugins that have UI pages enabled (for sidebar rendering)."""
    manager = _require_plugin_manager()
    result: list[dict[str, Any]] = []

    for name, loaded in manager.plugins.items():
        if not loaded.started or not loaded.manifest.ui_enabled:
            continue
        ctx = loaded.context
        if ctx is None or ctx._ui_handler is None:
            continue
        result.append(
            {
                "id": name,
                "label": loaded.manifest.ui_sidebar_label or name,
                "icon": loaded.manifest.ui_sidebar_icon or loaded.manifest.icon or "plug",
                "path": f"/plugins/{name}",
            }
        )

    return result


@router.get("/api/plugins/{plugin_id}/ui")
async def get_plugin_ui(plugin_id: str) -> dict[str, Any]:
    """Return the full UI schema for a plugin page."""
    manager = _require_plugin_manager()
    loaded = manager.plugins.get(plugin_id)

    if loaded is None:
        raise HTTPException(status_code=404, detail=f"Plugin not found: {plugin_id}")
    if not loaded.started:
        raise HTTPException(status_code=503, detail=f"Plugin not started: {plugin_id}")

    ctx = loaded.context
    if ctx is None or ctx._ui_handler is None:
        raise HTTPException(status_code=404, detail=f"Plugin has no UI handler: {plugin_id}")

    try:
        page = await ctx._ui_handler(ctx)
        return page.to_dict(plugin_id=plugin_id)
    except Exception as exc:
        logger.error("Plugin UI handler error for '%s': %s", plugin_id, exc)
        raise HTTPException(status_code=500, detail="Plugin UI handler error")


@router.post("/api/plugins/{plugin_id}/actions/{action}")
async def dispatch_plugin_action(plugin_id: str, action: str, request: Request) -> dict[str, Any]:
    """Dispatch a UI button action to a plugin's action handler."""
    manager = _require_plugin_manager()
    loaded = manager.plugins.get(plugin_id)

    if loaded is None:
        raise HTTPException(status_code=404, detail=f"Plugin not found: {plugin_id}")
    if not loaded.started:
        raise HTTPException(status_code=503, detail=f"Plugin not started: {plugin_id}")

    ctx = loaded.context
    if ctx is None or ctx._action_handler is None:
        raise HTTPException(status_code=404, detail=f"Plugin has no action handler: {plugin_id}")

    try:
        body = await request.json()
    except Exception:
        body = {}

    try:
        result = await ctx._action_handler(action, body, ctx)
        if not isinstance(result, dict):
            result = {"success": True}
        # Auto-notify SSE clients so the UI refreshes immediately
        ctx.notify_ui_update()
        return result
    except Exception as exc:
        logger.error("Plugin action '%s' error for '%s': %s", action, plugin_id, exc)
        raise HTTPException(status_code=500, detail=f"Action failed: {exc}")


@router.get("/api/plugins/{plugin_id}/events")
async def plugin_ui_events(plugin_id: str, request: Request) -> StreamingResponse:
    """SSE stream that emits ``ui_refresh`` events when plugin state changes.

    The frontend connects to this endpoint instead of polling.  Each time
    the plugin calls ``ctx.notify_ui_update()`` (or an action is dispatched),
    a ``data: {"event": "ui_refresh", "revision": N}`` line is sent.

    If no update occurs within 30 seconds a keep-alive comment is sent to
    prevent proxy/browser timeouts.  The client should reconnect on error.
    """
    manager = _require_plugin_manager()
    loaded = manager.plugins.get(plugin_id)

    if loaded is None:
        raise HTTPException(status_code=404, detail=f"Plugin not found: {plugin_id}")
    if not loaded.started:
        raise HTTPException(status_code=503, detail=f"Plugin not started: {plugin_id}")

    ctx = loaded.context
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"Plugin context not available: {plugin_id}")

    async def event_generator():
        """Yield SSE frames: data lines on update, comments as keep-alive."""
        last_rev = ctx.ui_revision
        try:
            while True:
                # Check if client disconnected
                if await request.is_disconnected():
                    break
                new_rev = await ctx.wait_for_ui_update(last_revision=last_rev, timeout=25.0)
                if new_rev > last_rev:
                    last_rev = new_rev
                    yield f'data: {{"event": "ui_refresh", "revision": {new_rev}}}\n\n'
                else:
                    # Keep-alive comment to prevent proxy timeouts
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


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
    artists = await db.search_artists(query=q, offset=0, limit=limit)
    albums = await db.search_albums(query=q, offset=0, limit=limit)
    tracks = await db.search_tracks(query=q, offset=0, limit=limit)

    return {
        "query": q,
        "artists": [
            {
                "id": r["id"] if isinstance(r, dict) else r.id,
                "artist": r.get("name", r.get("artist", ""))
                if isinstance(r, dict)
                else getattr(r, "name", getattr(r, "artist", "")),
            }
            for r in artists
        ],
        "albums": [
            {
                "id": r["id"] if isinstance(r, dict) else r.id,
                "album": r.get("title", r.get("album", ""))
                if isinstance(r, dict)
                else getattr(r, "title", getattr(r, "album", "")),
                "artist": r.get("artist_name", "")
                if isinstance(r, dict)
                else getattr(r, "artist_name", ""),
                "year": r.get("year") if isinstance(r, dict) else getattr(r, "year", None),
            }
            for r in albums
        ],
        "tracks": [
            {
                "id": r["id"] if isinstance(r, dict) else r.id,
                "title": r.get("title", "") if isinstance(r, dict) else getattr(r, "title", ""),
                "artist": r.get("artist", "") if isinstance(r, dict) else getattr(r, "artist", ""),
                "album": r.get("album", "") if isinstance(r, dict) else getattr(r, "album", ""),
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


# =============================================================================
# Settings
# =============================================================================


@router.get("/api/settings")
async def get_settings() -> dict[str, Any]:
    """Get the current server settings.

    Returns all settings grouped by category, plus metadata about
    which settings are runtime-changeable vs restart-required.
    """
    from resonance.config.settings import (
        RESTART_REQUIRED,
        RUNTIME_CHANGEABLE,
        settings_loaded,
    )
    from resonance.config.settings import (
        get_settings as _get_settings,
    )

    if not settings_loaded():
        raise HTTPException(status_code=503, detail="Settings not loaded")

    settings = _get_settings()
    settings_dict = settings.to_dict()

    # Annotate each field with changeability info
    meta: dict[str, str] = {}
    for key in settings_dict:
        if key in RUNTIME_CHANGEABLE:
            meta[key] = "runtime"
        elif key in RESTART_REQUIRED:
            meta[key] = "restart_required"
        else:
            meta[key] = "runtime"

    return {
        "settings": settings_dict,
        "sections": settings.to_toml_dict(),
        "meta": meta,
        "config_file": str(settings._config_path) if settings._config_path else None,
    }


@router.put("/api/settings")
async def put_settings(request: Request) -> dict[str, Any]:
    """Update server settings (partial update).

    Request body: ``{"settings": {"default_volume": 60, "log_level": "DEBUG"}}``

    Only the provided fields are updated. Unknown fields are ignored
    with a warning. Settings that require a restart are accepted but
    flagged in the response.

    Returns the full updated settings plus any warnings.
    """
    from resonance.config.settings import (
        get_settings as _get_settings,
    )
    from resonance.config.settings import (
        save_settings as _save_settings,
    )
    from resonance.config.settings import (
        settings_loaded,
    )
    from resonance.config.settings import (
        update_settings as _update_settings,
    )

    if not settings_loaded():
        raise HTTPException(status_code=503, detail="Settings not loaded")

    body = await request.json()
    updates = body.get("settings")
    if not updates or not isinstance(updates, dict):
        raise HTTPException(
            status_code=400,
            detail="Request body must contain a 'settings' object with fields to update",
        )

    try:
        settings, warnings = _update_settings(updates)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Persist to TOML file
    try:
        config_path = _save_settings(settings)
    except Exception as exc:
        logger.error("Failed to save settings: %s", exc)
        warnings.append(f"Settings applied in memory but could not be saved to disk: {exc}")
        config_path = None

    # Sync music_folders to the library DB so the scanner picks them up.
    # Without this, changing music_folders via Settings only writes to the
    # TOML file but the scanner reads folders from the DB — see issue #7.
    if "music_folders" in updates and _music_library is not None:
        try:
            await _music_library.set_music_folders(settings.music_folders)
        except Exception as exc:
            logger.error("Failed to sync music_folders to library DB: %s", exc)
            warnings.append(f"Music folders saved to config but failed to sync to library: {exc}")

    return {
        "settings": settings.to_dict(),
        "warnings": warnings,
        "config_file": str(config_path) if config_path else None,
    }


@router.post("/api/settings/reset")
async def reset_settings_endpoint() -> dict[str, Any]:
    """Reset all settings to their default values.

    The config file path is preserved so that ``save`` still works.
    The reset settings are automatically saved to disk.
    """
    from resonance.config.settings import (
        reset_settings as _reset_settings,
    )
    from resonance.config.settings import (
        save_settings as _save_settings,
    )

    settings = _reset_settings()

    warnings: list[str] = []
    try:
        config_path = _save_settings(settings)
    except Exception as exc:
        logger.error("Failed to save reset settings: %s", exc)
        warnings.append(f"Settings reset in memory but could not be saved to disk: {exc}")
        config_path = None

    warnings.append("All settings reset to defaults. A server restart is recommended.")

    return {
        "settings": settings.to_dict(),
        "warnings": warnings,
        "config_file": str(config_path) if config_path else None,
    }


# =============================================================================
# Server restart
# =============================================================================


@router.post("/api/server/restart")
async def restart_server() -> dict[str, Any]:
    """Gracefully shut down the server process.

    In a Docker environment with ``restart: unless-stopped``, the container
    will be automatically restarted by the Docker daemon.  Outside Docker
    the process simply exits with code 0.
    """
    logger.info("Server restart requested via REST API")

    async def _delayed_shutdown() -> None:
        """Give the HTTP response time to be sent before killing the process."""
        await asyncio.sleep(0.5)
        logger.info("Shutting down for restart...")
        # SIGTERM triggers the graceful shutdown path in __main__.py
        os.kill(os.getpid(), signal.SIGTERM)

    asyncio.create_task(_delayed_shutdown())

    return {
        "restarting": True,
        "message": "Server is restarting. Please wait a few seconds and reload.",
    }
