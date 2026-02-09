"""
Status Command Handlers.

Handles server and player status commands:
- serverstatus: Server information and statistics
- players: List of connected players
- player: Player information by index
- status: Current player status (track, position, mode)
- pref: Server preferences
- rescan: Trigger library rescan
- wipecache: Clear library cache

PERF / RELIABILITY NOTE:
`status` is polled frequently by clients (e.g. Cadence ~1 Hz). It MUST remain fast.

In particular, generating BlurHash placeholders on-demand can be expensive (artwork extraction,
image decoding, hashing) and must NOT block the status response, otherwise clients will hit
HTTP timeouts during seeks/track transitions.

Therefore, we only include a BlurHash if it is already cached or cheaply accessible; otherwise
we skip it (and optionally let other background mechanisms populate caches).
"""

from __future__ import annotations

import time
from typing import Any

from resonance.web.handlers import CommandContext
from resonance.web.jsonrpc_helpers import (
    build_list_response,
    build_player_item,
    get_filter_int,
    is_audio_player,
    parse_start_items,
    parse_tagged_params,
    parse_tags_string,
)

# Match LMS version to avoid "update required" messages on hardware players
# NOTE: Must be 7.x for firmware compatibility - see Research_gold.md
# SqueezePlay firmware 7.7.3 and earlier has a version comparison bug
# that rejects servers reporting version 8.0.0 or higher.
VERSION = "7.999.999"

def _lms_song_elapsed_seconds(*, status: Any, mode: str) -> float:
    """Compute song elapsed like LMS Squeezebox2::songElapsedSeconds().

    - Prefer elapsed_milliseconds when available.
    - Apply LMS truncation correction when ms/1000 < elapsed_seconds.
    - While playing, extrapolate using local monotonic delta since last STAT.
    """
    elapsed_seconds = float(getattr(status, "elapsed_seconds", 0.0) or 0.0)
    elapsed_ms_raw = int(getattr(status, "elapsed_milliseconds", 0) or 0)

    if elapsed_ms_raw > 0:
        song_elapsed = elapsed_ms_raw / 1000.0
        if song_elapsed < elapsed_seconds:
            song_elapsed = elapsed_seconds + (elapsed_ms_raw % 1000) / 1000.0
    else:
        song_elapsed = elapsed_seconds

    if mode == "play":
        sample_monotonic = float(getattr(status, "elapsed_report_monotonic", 0.0) or 0.0)
        if sample_monotonic > 0:
            time_diff = time.monotonic() - sample_monotonic
            if time_diff > 0:
                song_elapsed += time_diff

    return max(0.0, song_elapsed)


async def cmd_serverstatus(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """
    Handle 'serverstatus' command.

    Returns server version and basic statistics, including players_loop.
    UI uses players_loop to auto-select a player when one connects.
    """
    # Get players
    players = await ctx.player_registry.get_all()
    player_count = len(players)

    # Build players_loop for UI compatibility
    players_loop = [build_player_item(p) for p in players]

    # Get library stats
    db = ctx.music_library._db
    artist_count = await db.count_artists()
    album_count = await db.count_albums()
    track_count = await db.count_tracks()
    genre_count = await db.count_genres()

    return {
        "version": VERSION,
        "uuid": ctx.server_uuid,
        "mac": "00:00:00:00:00:00",
        "ip": ctx.server_host,
        "httpport": str(ctx.server_port),
        "info total albums": album_count,
        "info total artists": artist_count,
        "info total songs": track_count,
        "info total genres": genre_count,
        "player count": player_count,
        "players_loop": players_loop,
        "other player count": 0,
        "sn player count": 0,
    }


async def cmd_players(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """
    Handle 'players' command.

    Returns list of connected players.
    """
    start, items = parse_start_items(params)

    all_players = await ctx.player_registry.get_all()
    total_count = len(all_players)

    # Apply pagination
    paginated = all_players[start : start + items]

    players_loop = [build_player_item(p) for p in paginated]

    return build_list_response(players_loop, total_count, "players_loop")


async def cmd_player(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """
    Handle 'player' command.

    Returns information about a specific player by index or MAC.
    """
    if len(params) < 2:
        return {"error": "Missing player index or parameter"}

    subcommand = str(params[1]).lower() if len(params) > 1 else ""

    if subcommand == "count":
        players = await ctx.player_registry.get_all()
        return {"_count": len(players)}

    supported = {
        "id",
        "address",
        "uuid",
        "name",
        "ip",
        "model",
        "displaytype",
        "isplayer",
        "canpoweroff",
    }
    if subcommand not in supported:
        return {"error": f"Unknown player subcommand: {subcommand}"}

    player_ref = str(params[2]) if len(params) > 2 else "0"
    player = await ctx.player_registry.get_by_mac(player_ref)

    if player is None:
        player_idx = get_filter_int({"idx": player_ref}, "idx")
        if player_idx is None:
            return {"error": "Player not found"}

        players = await ctx.player_registry.get_all()
        if player_idx < 0 or player_idx >= len(players):
            return {"error": "Player not found"}

        player = players[player_idx]

    if subcommand in {"id", "address"}:
        return {f"_{subcommand}": getattr(player, "mac_address", "")}
    if subcommand == "uuid":
        uuid_value = getattr(getattr(player, "info", None), "uuid", None) or getattr(
            player,
            "mac_address",
            "",
        )
        return {"_uuid": uuid_value}
    if subcommand == "name":
        return {"_name": getattr(player, "name", "")}
    if subcommand == "ip":
        return {"_ip": getattr(player, "ip_address", "0.0.0.0")}
    if subcommand == "model":
        model = "squeezebox"
        player_info = getattr(player, "info", None)
        if player_info is not None:
            model_hint = getattr(player_info, "model", None)
            if isinstance(model_hint, str) and model_hint:
                model = model_hint.lower()
            else:
                device_type = getattr(player_info, "device_type", None)
                if device_type is not None and hasattr(device_type, "name"):
                    model = device_type.name.lower()
        return {"_model": model}
    if subcommand == "displaytype":
        return {"_displaytype": "none"}
    if subcommand == "isplayer":
        return {"_isplayer": 1 if is_audio_player(player) else 0}
    if subcommand == "canpoweroff":
        can_power_off = True
        if hasattr(player, "device_capabilities"):
            can_power_off = bool(getattr(player.device_capabilities, "can_power_off", True))
        elif hasattr(player, "can_power_off"):
            can_power_off = bool(getattr(player, "can_power_off"))
        return {"_canpoweroff": 1 if can_power_off else 0}

    return {"error": f"Unknown player subcommand: {subcommand}"}


async def cmd_displaystatus(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """
    Handle 'displaystatus' command.

    LMS behavior:
    - ``displaystatus subscribe:*`` registers an auto-execute subscription.
    - The initial query response does not contain display payload.
    - Display payload is only delivered for real display notifications.

    Returning synthetic ``display`` content here causes unsolicited empty popups
    on Jive/SqueezePlay clients (eg. Squeezebox Radio/Touch).
    """
    # Keep signature for command-dispatch compatibility.
    _ = ctx, params

    # LMS does not synthesize a display payload for subscription setup or
    # ordinary polling calls; the side effect is the subscription itself.
    return {}


async def cmd_status(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """
    Handle 'status' command.

    Returns current player status including:
    - Current track info
    - Playback position
    - Mode (play/pause/stop)
    - Volume
    - Playlist info
    """
    tagged_params = parse_tagged_params(params)
    tags_str = tagged_params.get("tags", "")
    tags = parse_tags_string(tags_str) if tags_str else None

    # Jive/SqueezePlay menu mode — when "menu:menu" is specified, the Radio
    # expects item_loop (Jive-formatted) instead of playlist_loop.
    menu_mode = tagged_params.get("menu") == "menu"
    use_context_menu = tagged_params.get("useContextMenu") == "1"

    # Get player
    player = None
    if ctx.player_id != "-":
        player = await ctx.player_registry.get_by_mac(ctx.player_id)

    # Base status
    result: dict[str, Any] = {
        "player_connected": 1 if player else 0,
        "power": 1 if player else 0,
    }

    # Attach stream generation for discontinuity detection on polling clients.
    # This increments whenever a new stream is queued and allows clients to
    # ignore stale/foreign status samples.
    stream_generation: int | None = None
    if ctx.streaming_server is not None:
        try:
            stream_generation = ctx.streaming_server.get_stream_generation(ctx.player_id)
        except Exception:
            stream_generation = None
        if stream_generation is not None:
            result["stream_generation"] = stream_generation
    if player is None:
        result["mode"] = "stop"
        result["time"] = 0
        result["duration"] = 0
        result["playlist_tracks"] = 0
        result["playlist_loop"] = []
        return result

    # Get player status
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
    result["mode"] = state_to_mode.get(state_name, "stop")

    # Playback position and volume
    #
    # LMS-style elapsed calculation (from StreamingController.pm):
    #   songtime = startOffset + songElapsedSeconds
    #
    # After a seek to position X, the player reports elapsed time relative to
    # the NEW stream start (0, 1, 2, 3...). The real track position is:
    #   actual_elapsed = start_offset + raw_elapsed
    #
    # Example: Seek to 30s → player reports 0,1,2,3... → we return 30,31,32,33...
    #
    # start_offset is set when queuing a seek and cleared when a new track starts.
    raw_elapsed_sec = _lms_song_elapsed_seconds(
        status=status,
        mode=result.get("mode", "stop"),
    )
    # Get start offset from streaming server (LMS-style startOffset)
    start_offset: float = 0.0
    if ctx.streaming_server is not None:
        try:
            start_offset = ctx.streaming_server.get_start_offset(ctx.player_id)
        except Exception:
            start_offset = 0.0

    # Get duration for capping
    duration_sec = float(status.duration_seconds) if hasattr(status, "duration_seconds") else 0.0

    # Calculate actual elapsed: start_offset + raw_elapsed (LMS formula)
    elapsed_sec = start_offset + raw_elapsed_sec

    # Cap elapsed to duration (never show more than 100% progress)
    if duration_sec > 0 and elapsed_sec > duration_sec:
        elapsed_sec = duration_sec

    result["time"] = elapsed_sec
    result["duration"] = duration_sec
    result["mixer volume"] = status.volume
    result["rate"] = 1 if result["mode"] == "play" else 0

    # Include seq_no for volume sync (LMS/SqueezePlay compatibility)
    # This allows the player to track which volume updates are current
    if hasattr(player, "_seq_no") and player._seq_no is not None:
        result["seq_no"] = player._seq_no

    # Playlist info
    loop_items: list[dict[str, Any]] = []
    # In menuMode (Jive/SqueezePlay), use "item_loop"; otherwise "playlist_loop".
    # See LMS Slim::Control::Queries::statusQuery():
    #   my $loop = $menuMode ? 'item_loop' : 'playlist_loop';
    loop_name = "item_loop" if menu_mode else "playlist_loop"

    if ctx.playlist_manager is not None:
        playlist = ctx.playlist_manager.get(ctx.player_id)
        if playlist is not None:
            song_count = len(playlist)
            result["playlist_tracks"] = song_count
            result["playlist shuffle"] = 1 if playlist.shuffle_mode.value else 0
            result["playlist repeat"] = playlist.repeat_mode.value

            # LMS only sends playlist_cur_index and playlist_timestamp when
            # songCount > 0 (Queries.pm L4173-4179).  Sending them for an
            # empty playlist would set DB.lua self.ts to a truthy value while
            # there are no items, confusing playlistIndex().
            if song_count > 0:
                result["playlist_cur_index"] = playlist.current_index
                result["playlist index"] = playlist.current_index
                # playlist_timestamp is REQUIRED by JiveLite to highlight the
                # current track.  DB.lua stores it as self.ts and
                # playlistIndex() returns nil when ts is falsy → no selection
                # highlighting.  LMS uses
                # $client->currentPlaylistUpdateTime() which is
                # Time::HiRes::time() (epoch seconds), updated on every
                # playlist mutation.  Our Playlist.updated_at mirrors this.
                result["playlist_timestamp"] = playlist.updated_at

            # Get current track info
            current = playlist.current_track
            if current is not None:
                result["duration"] = (current.duration_ms or 0) / 1000.0
                if result["duration"] > 0 and result["time"] > result["duration"]:
                    result["time"] = result["duration"]
                current_server_url = f"http://{ctx.server_host}:{ctx.server_port}"

                # Expose current track in a stable shape so the UI can highlight correctly.
                # Note: keep keys aligned with the Track shape used by the web-ui.
                track_id = getattr(current, "id", getattr(current, "track_id", None))
                album_id = getattr(current, "album_id", None)
                artist = getattr(current, "artist_name", getattr(current, "artist", ""))
                album = getattr(current, "album_title", getattr(current, "album", ""))
                duration_ms = getattr(current, "duration_ms", 0)
                path = getattr(current, "path", "")

                # Also expose a top-level track id so polling clients can detect
                # track changes even if `currentTrack` is missing/partial temporarily.
                result["track_id"] = track_id

                result["currentTrack"] = {
                    "id": track_id,
                    "title": getattr(current, "title", ""),
                    "artist": artist or "",
                    "album": album or "",
                    "duration": (duration_ms or 0) / 1000.0,
                    "path": path,
                    "coverArt": f"{current_server_url}/artwork/{album_id}" if album_id else "",
                }

                # JiveLite/SqueezePlay compatibility (Squeezebox Radio, Touch, etc.)
                if album_id:
                    result["currentTrack"]["icon-id"] = f"/music/{album_id}/cover"
                    result["currentTrack"]["icon"] = f"{current_server_url}/artwork/{album_id}"
                    result["currentTrack"]["artwork_track_id"] = album_id

                # Add BlurHash if available — MUST NOT BLOCK status polling.
                #
                # Important: get_blurhash() may extract artwork + decode images + compute hash.
                # That can exceed client HTTP timeouts (especially around seeks/stream restarts).
                #
                # We therefore only include blurhash if it can be obtained without expensive
                # on-demand generation. If the ArtworkManager doesn't provide a cheap path,
                # we skip blurhash here.
                if ctx.artwork_manager and path:
                    try:
                        # Prefer a non-blocking / cache-only method if available.
                        # Use the fast cached-only method when available.
                        # Call it directly so type-checkers see an awaitable coroutine.
                        if hasattr(ctx.artwork_manager, "get_blurhash_if_cached"):
                            blurhash = await ctx.artwork_manager.get_blurhash_if_cached(path)
                            if blurhash:
                                result["currentTrack"]["blurhash"] = blurhash
                        else:
                            # Fallback: do NOT call get_blurhash() here (can be slow).
                            pass
                    except Exception:
                        pass

            # Build track info for the loop
            # This is OUTSIDE the "if current is not None" block so that
            # the loop is populated even if no track is currently playing
            # (e.g., right after adding tracks but before playback starts)
            server_url = f"http://{ctx.server_host}:{ctx.server_port}"

            # ── menuMode: Jive base actions ──────────────────────────
            # LMS adds a "base" with context menu actions and adjusts
            # "count" to include +1 for the Clear Playlist control.
            if menu_mode:
                if use_context_menu:
                    base = {
                        "actions": {
                            "more": {
                                "player": 0,
                                "cmd": ["contextmenu"],
                                "params": {
                                    "menu": "track",
                                    "context": "playlist",
                                },
                                "itemsParams": "params",
                                "window": {"isContextMenu": 1},
                            }
                        }
                    }
                else:
                    base = {
                        "actions": {
                            "go": {
                                "cmd": ["trackinfo", "items"],
                                "params": {
                                    "menu": "nowhere",
                                    "useContextMenu": 1,
                                    "context": "playlist",
                                },
                                "itemsParams": "params",
                            }
                        }
                    }
                result["base"] = base
                # count includes +1 for Clear Playlist (if non-empty)
                menu_count = song_count + 1 if song_count > 0 else 0
                result["count"] = menu_count

            # Get the first N tracks based on params
            start = 0
            items = 1  # Default to just current track

            # Check for "-" which means current track only
            if len(params) >= 2:
                if params[1] == "-":
                    # "-" means start from current track (LMS: modecurrent)
                    # normalize(playlist_cur_index, quantity, songCount)
                    start = playlist.current_index
                    # The second positional param is the quantity (e.g. "10")
                    if len(params) >= 3:
                        try:
                            items = int(params[2])
                        except (ValueError, TypeError):
                            items = 1
                else:
                    start, items = parse_start_items(["status", params[1]] + list(params[2:]))

            if menu_mode:
                result["offset"] = start

            # Get tracks for the loop
            tracks = list(playlist.tracks)[start : start + items]

            for i, track in enumerate(tracks):
                track_id = getattr(track, "id", getattr(track, "track_id", None))
                album_id = getattr(track, "album_id", None)
                artist = getattr(track, "artist_name", getattr(track, "artist", ""))
                album = getattr(track, "album_title", getattr(track, "album", ""))
                duration_ms = getattr(track, "duration_ms", 0)
                path = getattr(track, "path", "")
                title = getattr(track, "title", "")
                playlist_idx = start + i

                if menu_mode:
                    # ── Jive item_loop format ────────────────────────
                    # Matches LMS _addJiveSong():
                    #   text (multiline), track, album, artist, icon-id,
                    #   params (track_id + playlist_index), style, trackType
                    second_line_parts: list[str] = []
                    if artist:
                        second_line_parts.append(artist)
                    if album:
                        second_line_parts.append(album)
                    second_line = " - ".join(second_line_parts)
                    text = f"{title}\n{second_line}" if second_line else title

                    track_dict: dict[str, Any] = {
                        "text": text,
                        "track": title,
                        "album": album or "",
                        "artist": artist or "",
                        "params": {
                            "track_id": track_id,
                            "playlist_index": playlist_idx,
                        },
                        "style": "itemplay",
                        "trackType": "local",
                    }
                    if album_id:
                        track_dict["icon-id"] = album_id
                        track_dict["icon"] = f"{server_url}/artwork/{album_id}"

                else:
                    # ── Standard playlist_loop format ─────────────────
                    track_dict = {
                        "id": track_id,
                        "title": title,
                        "artist": artist or "",
                        "album": album or "",
                        "duration": (duration_ms or 0) / 1000.0,
                        "url": f"{server_url}/stream.mp3?track_id={track_id}",
                        "coverArt": f"{server_url}/artwork/{album_id}" if album_id else "",
                        "playlist index": playlist_idx,
                    }

                    # JiveLite/SqueezePlay compatibility (Squeezebox Radio, Touch, etc.)
                    # These players expect icon-id or icon for cover art display
                    if album_id:
                        track_dict["icon-id"] = f"/music/{album_id}/cover"
                        track_dict["icon"] = f"{server_url}/artwork/{album_id}"
                        track_dict["artwork_track_id"] = album_id

                    # JiveLite expects track/album/artist as separate fields + text
                    track_dict["track"] = title
                    text_parts = [title]
                    if artist:
                        text_parts.append(artist)
                    if album:
                        text_parts.append(album)
                    track_dict["text"] = "\n".join(text_parts)

                    # Add BlurHash for tracks in the loop
                    if ctx.artwork_manager and path:
                        try:
                            track_dict["blurhash"] = await ctx.artwork_manager.get_blurhash(path)
                        except Exception:
                            pass

                    # Add optional fields based on tags
                    if tags is None or "n" in tags:
                        if track.track_no:
                            track_dict["tracknum"] = track.track_no
                    if tags is None or "i" in tags:
                        if track.disc_no:
                            track_dict["disc"] = track.disc_no
                    if tags is None or "y" in tags:
                        if track.year:
                            track_dict["year"] = track.year

                loop_items.append(track_dict)

            # ── Jive playlist controls at the bottom ─────────────
            # LMS adds a "Clear Playlist" item after the
            # last track when menuMode is active and the playlist is non-empty.
            if menu_mode and song_count > 0 and (start + items) >= song_count:
                # Clear Playlist
                clear_item: dict[str, Any] = {
                    "text": "Clear Playlist",
                    "icon-id": "/html/images/playlistclear.png",
                    "offset": 0,
                    "count": 2,
                    "item_loop": [
                        {
                            "text": "Cancel",
                            "actions": {
                                "go": {
                                    "player": 0,
                                    "cmd": ["jiveblankcommand"],
                                },
                            },
                            "nextWindow": "parent",
                        },
                        {
                            "text": "Clear Playlist",
                            "actions": {
                                "do": {
                                    "player": 0,
                                    "cmd": ["playlist", "clear"],
                                },
                            },
                            "nextWindow": "home",
                        },
                    ],
                }
                loop_items.append(clear_item)

        else:
            result["playlist_tracks"] = 0
            result["playlist shuffle"] = 0
            result["playlist repeat"] = 0
            if menu_mode:
                result["count"] = 0
    else:
        result["playlist_tracks"] = 0
        if menu_mode:
            result["count"] = 0

    # Only include the loop key when there are actual items.
    # LMS omits item_loop/playlist_loop entirely for empty results (the loop
    # is created lazily by addResultLoop in Request.pm).  Sending an empty
    # array ("item_loop": []) causes a crash in JiveLite's _whatsPlaying()
    # (Player.lua): it checks `if obj.item_loop then` (empty table is truthy
    # in Lua), then indexes obj.item_loop[1] which is nil → "attempt to
    # index nil value" error on nil.params.
    if loop_items:
        result[loop_name] = loop_items

    return result

async def cmd_pref(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """
    Handle 'pref' command.

    Returns server preferences.
    """
    if len(params) < 2:
        return {"error": "Missing preference name"}

    pref_name = params[1]

    # Handle mediadirs preference (music folders)
    if pref_name == "mediadirs":
        if len(params) >= 3 and params[2] == "?":
            # Query music folders
            folders = await ctx.music_library.get_music_folders()
            return {"_p2": ";".join(folders)}

        elif len(params) >= 3:
            # Set music folders
            folders_str = params[2]
            if folders_str:
                folders = folders_str.split(";")
                await ctx.music_library.set_music_folders(folders)
            return {"_p2": folders_str}

    # Handle other preferences
    pref_defaults: dict[str, Any] = {
        "language": "en",
        "audiodir": "",
        "playlistdir": "",
        "httpport": ctx.server_port,
    }

    if len(params) >= 3 and params[2] == "?":
        return {"_p2": pref_defaults.get(pref_name, "")}

    return {}


async def cmd_rescan(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """
    Handle 'rescan' command.

    Triggers a library rescan.
    """
    # Check if this is a progress query
    if "?" in params:
        status = ctx.music_library.get_scan_status()
        return {
            "rescan": 1 if status.is_running else 0,
            "progressname": status.current_folder,
            "progressdone": status.folders_done,
            "progresstotal": status.folders_total,
        }

    # Start rescan
    await ctx.music_library.start_scan()

    return {"rescan": 1}


async def cmd_wipecache(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """
    Handle 'wipecache' command.

    Clears the library cache and rescans.
    """
    # Clear database
    await ctx.music_library._db.clear_all()

    # Start fresh scan
    await ctx.music_library.start_scan()

    return {"wipecache": 1}
