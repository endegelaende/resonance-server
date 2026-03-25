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
`status` is polled frequently by clients (~1 Hz). It MUST remain fast.

In particular, generating BlurHash placeholders on-demand can be expensive (artwork extraction,
image decoding, hashing) and must NOT block the status response, otherwise clients will hit
HTTP timeouts during seeks/track transitions.

Therefore, we only include a BlurHash if it is already cached or cheaply accessible; otherwise
we skip it (and optionally let other background mechanisms populate caches).
"""

from __future__ import annotations

import re as _re_mod
import time
from typing import Any
from urllib.parse import quote as _url_quote

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

# Match LMS version to avoid "update required" messages on hardware players.
# Must be 7.x — SqueezePlay firmware <=7.7.3 rejects version 8.0.0+.
VERSION = "7.999.999"


def _parse_icy_title(icy_title: str) -> tuple[str, str]:
    """Parse an ICY StreamTitle into ``(artist, title)``.

    Mirrors LMS ``HTTP.pm`` ``getMetadataFor()`` L1085-1092::

        my @dashes = $currentTitle =~ /( - )/g;
        if ( scalar @dashes == 1 ) {
            ($artist, $title) = split /\\s+-\\s+/, $currentTitle;
        } else {
            $title = $currentTitle;
        }

    When the ICY string contains exactly one ``" - "`` separator, it is
    split into *artist* (before) and *title* (after).  Otherwise the
    whole string is returned as *title* with an empty *artist*.

    Returns:
        A ``(artist, title)`` tuple.  Both strings are stripped of
        leading/trailing whitespace.
    """
    if not icy_title:
        return ("", "")

    parts = icy_title.split(" - ")
    if len(parts) == 2:
        return (parts[0].strip(), parts[1].strip())
    # More than one " - " or none at all → entire string is the title
    return ("", icy_title.strip())


def _proxied_image_url(url: str | None) -> str | None:
    """Convert an external artwork URL to an /imageproxy/ server-local path.

    Mirrors LMS ``Slim::Web::ImageProxy::proxiedImage()`` (ImageProxy.pm L437-457).
    SqueezePlay/JiveLite devices cannot reliably fetch external URLs directly,
    so LMS routes all remote artwork through a server-side proxy.

    Returns *None* when *url* is falsy or already server-relative.
    """
    if not url or not url.startswith(("http://", "https://")):
        return None

    # Detect file extension (LMS defaults to .png).
    ext = ".png"
    m = _re_mod.search(r"\.(jpe?g|png|gif|webp)", url, _re_mod.IGNORECASE)
    if m:
        ext = "." + m.group(1).lower()
        if ext == ".jpeg":
            ext = ".jpg"

    # URI-encode the URL so it's safe to embed in a path segment.
    encoded = _url_quote(url, safe="")
    return f"/imageproxy/{encoded}/image{ext}"


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

    # ── Player info (LMS Queries.pm L4050-4058) ─────────────────
    # JiveLite/SqueezePlay reads player_name from the status response
    # to display in the UI header.  Without this field the box shows
    # whatever it has stored locally (often "nil" for unconfigured
    # SqueezePlay instances).
    if player is not None:
        _raw_name = getattr(player, "name", "") or ""
        # Normalize nil-like names the same way jsonrpc_helpers does.
        _NIL_NAMES = {"nil", "null", "none", "undefined", "(null)"}
        if _raw_name and _raw_name.strip().lower() not in _NIL_NAMES:
            result["player_name"] = _raw_name
        else:
            # Fall back to capability ModelName → model label → MAC
            _cap = getattr(player, "capabilities", None) or {}
            _model_name_cap = _cap.get("ModelName", "")
            if _model_name_cap and _model_name_cap.lower() not in _NIL_NAMES:
                result["player_name"] = _model_name_cap
            else:
                _model = getattr(player, "model", "")
                from resonance.web.jsonrpc_helpers import PLAYER_MODEL_LABELS

                _label = PLAYER_MODEL_LABELS.get(str(_model).lower(), "")
                result["player_name"] = _label or getattr(player, "mac_address", "Player")
        # player_ip (LMS Queries.pm L4056)
        _ip = getattr(player, "ip", None) or getattr(player, "address", None)
        if _ip:
            result["player_ip"] = str(_ip)

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

                # ── Remote/Radio stream metadata (LMS-compatible) ────
                # LMS Queries.pm L4088-4091: adds `remote: 1` and
                # `current_title` for remote streams.
                # Tag 'N' (remote_title) carries the station name.
                # `live_edge`: 0 = at live edge, -1 = not live
                #   (Queries.pm L5909-5912).
                _is_remote = bool(getattr(current, "is_remote", False))
                _is_live = bool(getattr(current, "is_live", False))
                _source = getattr(current, "source", "local") or "local"

                if _is_remote:
                    result["remote"] = 1

                    # current_title: LMS uses getCurrentTitle() which
                    # returns ICY metadata when available, else the
                    # track title.  Priority order:
                    #   1. StreamingServer._icy_titles (parsed from proxied
                    #      upstream ICY metadata in _icy_strip_relay)
                    #   2. PlayerClient.icy_title (from Slimproto META
                    #      messages sent by the player)
                    #   3. Static track title (station name for radio)
                    _icy_title: str | None = None
                    if ctx.streaming_server is not None:
                        _icy_title = ctx.streaming_server.get_icy_title(ctx.player_id)
                    if not _icy_title and player is not None:
                        _icy_title = getattr(player, "icy_title", None)
                    _static_title = getattr(current, "title", "") or ""
                    result["current_title"] = _icy_title or _static_title

                    # ── ICY "Artist - Title" parsing (LMS HTTP.pm L1085-1092) ─
                    # LMS splits the ICY StreamTitle on exactly one " - "
                    # separator to extract artist and title.  When no ICY
                    # data is available the static track fields are used.
                    _icy_artist: str = ""
                    _icy_parsed_title: str = ""
                    if _icy_title:
                        _icy_artist, _icy_parsed_title = _parse_icy_title(_icy_title)

                if _is_live:
                    # LMS: live_edge 0 means "at live edge"
                    result["live_edge"] = 0

                # For radio streams, expose station name as remote_title.
                # LMS Queries.pm L5572-5578: when remote_title exists and
                # differs from the track title, the station name is shown
                # as the "album" line in Now Playing.
                if _source == "radio":
                    # The track title IS the station name for radio.
                    result["remote_title"] = getattr(current, "title", "") or ""

                # Expose current track in a stable shape so the UI can highlight correctly.
                # Note: keep keys aligned with the Track shape used by the web-ui.
                track_id = getattr(current, "id", getattr(current, "track_id", None))
                album_id = getattr(current, "album_id", None)
                artist = getattr(current, "artist_name", getattr(current, "artist", ""))
                album = getattr(current, "album_title", getattr(current, "album", ""))
                duration_ms = getattr(current, "duration_ms", 0)
                path = getattr(current, "path", "")
                _artwork_url = getattr(current, "artwork_url", None)

                # ── Artwork debug logging ────────────────────────────
                import logging as _logging

                _status_logger = _logging.getLogger("resonance.web.handlers.status")
                _status_logger.debug(
                    "[STATUS-ART] player=%s track=%s album_id=%s artwork_url=%s is_remote=%s source=%s",
                    ctx.player_id,
                    getattr(current, "title", "?")[:60],
                    album_id,
                    (_artwork_url[:120] if _artwork_url else "<NONE>"),
                    _is_remote,
                    _source,
                )

                # Also expose a top-level track id so polling clients can detect
                # track changes even if `currentTrack` is missing/partial temporarily.
                result["track_id"] = track_id

                # Determine cover art URL — prefer local album art, fall
                # back to remote artwork_url (e.g. station favicon).
                if album_id:
                    _cover_art = f"{current_server_url}/artwork/{album_id}"
                elif _artwork_url:
                    _cover_art = _artwork_url
                else:
                    _cover_art = ""

                result["currentTrack"] = {
                    "id": track_id,
                    "title": getattr(current, "title", ""),
                    "artist": artist or "",
                    "album": album or "",
                    "duration": (duration_ms or 0) / 1000.0,
                    "path": path,
                    "coverArt": _cover_art,
                }

                # ── LMS-compatible top-level song tags ───────────
                # JiveLite/SqueezePlay NowPlaying reads these as
                # top-level fields in the status response, NOT from
                # the currentTrack object (which is a Resonance
                # Web-UI extension).  LMS Queries.pm L4193-4250
                # calls _songData() which adds these based on the
                # requested tags string.
                #
                # Without these, the Squeezebox Radio/Touch/Boom
                # NowPlaying screen shows no artwork and incomplete
                # track info.
                _cur_title = getattr(current, "title", "")
                if _cur_title:
                    result["title"] = _cur_title
                if artist:
                    result["artist"] = artist
                if album:
                    result["album"] = album
                if track_id is not None:
                    result["id"] = track_id
                if album_id:
                    # artwork_track_id (Tag J) — primary artwork
                    # reference for JiveLite.  NowPlaying.lua uses
                    # this to build /music/{id}/cover_{spec} URLs
                    # via the persistent artworkPool connection.
                    result["artwork_track_id"] = album_id
                    result["coverart"] = 1
                    result["artwork_url"] = f"{current_server_url}/artwork/{album_id}"
                elif _artwork_url:
                    result["artwork_url"] = _artwork_url
                    result["coverart"] = 1

                # Expose remote-specific fields in currentTrack for the
                # Web-UI (source, is_live, content_type, bitrate,
                # current_title, icy_artist, icy_title).
                if _is_remote:
                    result["currentTrack"]["remote"] = 1
                    result["currentTrack"]["source"] = _source
                    # Expose current_title in currentTrack so the Web-UI
                    # can display the currently-playing song on a radio
                    # station (from ICY StreamTitle metadata) separately
                    # from the static station name in "title".
                    _ct_title = result.get("current_title", "")
                    if _ct_title:
                        result["currentTrack"]["current_title"] = _ct_title
                    # Parsed ICY artist/title (LMS HTTP.pm "Artist - Title"
                    # splitting).  Only set when the split was successful
                    # (both artist AND title non-empty).  When only one
                    # part is available the raw ``current_title`` already
                    # carries the full ICY string for the UI's fallback.
                    if _icy_artist and _icy_parsed_title:
                        result["currentTrack"]["icy_artist"] = _icy_artist
                        result["currentTrack"]["icy_title"] = _icy_parsed_title
                if _is_live:
                    result["currentTrack"]["is_live"] = True
                _ct = getattr(current, "content_type", None)
                if _ct:
                    result["currentTrack"]["content_type"] = _ct
                _br = getattr(current, "bitrate", 0)
                if _br:
                    result["currentTrack"]["bitrate"] = _br

                # ── remoteMeta (LMS Queries.pm L4357-4361) ───────────
                # LMS adds a top-level ``remoteMeta`` dict for remote
                # tracks, built by ``_songData()``.  It contains the
                # same tag-based fields as playlist_loop entries but
                # enriched with live metadata from
                # ``getMetadataFor()`` (artist/title parsed from ICY,
                # cover art, bitrate, etc.).
                #
                # Jive/SqueezePlay hardware players read this to display
                # Now Playing info for radio streams.
                if _is_remote:
                    _remote_meta: dict[str, Any] = {
                        "id": track_id or 0,
                        "title": (_icy_parsed_title or _icy_title or getattr(current, "title", "")),
                    }

                    # Artist: prefer parsed ICY, fall back to static
                    _rm_artist = _icy_artist or getattr(
                        current, "artist_name", getattr(current, "artist", "")
                    )
                    if _rm_artist:
                        _remote_meta["artist"] = _rm_artist

                    # Album: static field (usually empty for radio)
                    _rm_album = getattr(current, "album_title", getattr(current, "album", "")) or ""
                    if _rm_album:
                        _remote_meta["album"] = _rm_album

                    # remote_title (tag N) — station name
                    if _source == "radio":
                        _remote_meta["remote_title"] = _static_title

                    # Duration (tag d) — 0 for live streams
                    _remote_meta["duration"] = (duration_ms or 0) / 1000.0

                    # Bitrate (tag r)
                    if _br:
                        _remote_meta["bitrate"] = _br

                    # Content type (tag o)
                    if _ct:
                        _remote_meta["type"] = _ct

                    # Artwork (tag K) — proxy external URLs through
                    # /imageproxy/ like LMS does (Queries.pm _songData
                    # uses proxiedImage for tag K).
                    if _cover_art:
                        _proxied_rm = _proxied_image_url(_cover_art)
                        if _proxied_rm:
                            # Server-relative path like LMS proxiedImage()
                            # so JiveLite fetches via artworkPool.
                            _remote_meta["artwork_url"] = _proxied_rm
                        else:
                            _remote_meta["artwork_url"] = _cover_art

                    # Remote flag (tag x)
                    _remote_meta["remote"] = 1

                    # live_edge (tag V) — 0 at live edge, -1 not live
                    _remote_meta["live_edge"] = 0 if _is_live else -1

                    result["remoteMeta"] = _remote_meta

                # JiveLite/SqueezePlay compatibility (Squeezebox Radio, Touch, etc.)
                if album_id:
                    # Server-relative paths — JiveLite fetches these via
                    # its persistent artworkPool (SlimServer.lua L987-989)
                    # instead of opening ad-hoc SocketHttp connections
                    # which are unreliable (L975-983 "XXXX" comment).
                    result["currentTrack"]["icon-id"] = f"/music/{album_id}/cover"
                    result["currentTrack"]["icon"] = f"/music/{album_id}/cover"
                    result["currentTrack"]["artwork_track_id"] = album_id
                elif _artwork_url:
                    # Remote artwork (e.g. station favicon) — proxy through
                    # /imageproxy/ so SqueezePlay fetches via artworkPool.
                    # LMS proxiedImage() returns server-relative paths
                    # (ImageProxy.pm L457): /imageproxy/<encoded>/image.ext
                    # JiveLite adds resize suffix → our endpoint strips it.
                    _proxied_ct = _proxied_image_url(_artwork_url)
                    if _proxied_ct:
                        result["currentTrack"]["icon"] = _proxied_ct
                        _status_logger.info(
                            "[STATUS-ART] player=%s currentTrack.icon=%s (proxied from %s)",
                            ctx.player_id,
                            _proxied_ct[:120],
                            _artwork_url[:120],
                        )
                    else:
                        result["currentTrack"]["icon"] = _artwork_url
                        _status_logger.info(
                            "[STATUS-ART] player=%s currentTrack.icon=%s (raw, not proxied)",
                            ctx.player_id,
                            _artwork_url[:120],
                        )
                elif _is_remote:
                    # Radio placeholder (LMS Player.pm L622).
                    # Server-relative path — JiveLite adds resize suffix
                    # (e.g. radio_300x300_m.png), our /html/images/ route
                    # strips it and serves the original.
                    result["currentTrack"]["icon-id"] = "/html/images/radio.png"
                    _status_logger.info(
                        "[STATUS-ART] player=%s currentTrack.icon-id=/html/images/radio.png (fallback, no artwork_url)",
                        ctx.player_id,
                    )

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
                    # Matches LMS _addJiveSong() (Queries.pm L5522-5615):
                    #   text (multiline), track, album, artist, icon/icon-id,
                    #   params (track_id + playlist_index), style, trackType
                    #
                    # For the CURRENT track of a remote/radio stream, LMS
                    # enriches these fields from remoteMeta (getMetadataFor):
                    #   track  = ICY parsed title (or static title)
                    #   artist = ICY parsed artist
                    #   album  = remote_title (station name) for radio
                    # This is how SqueezePlay shows "Artist - Title" on
                    # the NowPlaying screen instead of just the station name.

                    _jive_title = title
                    _jive_artist = artist
                    _jive_album = album

                    # Determine trackType from PlaylistTrack.source
                    # (LMS uses "radio" for radio streams, "local" for
                    # library tracks, etc.)
                    _trk_source = getattr(track, "source", "local") or "local"
                    _trk_type = _trk_source if _trk_source != "external" else "local"
                    _trk_is_remote = bool(getattr(track, "is_remote", False))

                    # ── Enrich current track with ICY metadata ───────
                    # LMS _addJiveSong calls _songData which merges
                    # remoteMeta from getMetadataFor().  We replicate
                    # this by injecting ICY artist/title for the current
                    # track (the only one with live ICY data).
                    if _trk_is_remote and playlist_idx == playlist.current_index:
                        # Re-use the ICY data already parsed above for
                        # currentTrack / remoteMeta (avoids duplicate lookups).
                        _jive_icy_title: str | None = None
                        if ctx.streaming_server is not None:
                            _jive_icy_title = ctx.streaming_server.get_icy_title(ctx.player_id)
                        if not _jive_icy_title and player is not None:
                            _jive_icy_title = getattr(player, "icy_title", None)

                        if _jive_icy_title:
                            _jive_icy_artist, _jive_icy_parsed = _parse_icy_title(_jive_icy_title)
                            if _jive_icy_artist and _jive_icy_parsed:
                                _jive_title = _jive_icy_parsed
                                _jive_artist = _jive_icy_artist
                            else:
                                # No " - " separator — show raw ICY as title
                                _jive_title = _jive_icy_title

                        # LMS Queries.pm L5579-5583: for remote streams with
                        # no album and a remote_title (station name), use
                        # station name as album line.
                        if _trk_source == "radio" and not _jive_album:
                            _jive_album = title  # static title IS the station name

                    second_line_parts: list[str] = []
                    if _jive_artist:
                        second_line_parts.append(_jive_artist)
                    if _jive_album:
                        second_line_parts.append(_jive_album)
                    second_line = " - ".join(second_line_parts)
                    text = f"{_jive_title}\n{second_line}" if second_line else _jive_title

                    track_dict: dict[str, Any] = {
                        "text": text,
                        "track": _jive_title,
                        "album": _jive_album or "",
                        "artist": _jive_artist or "",
                        "params": {
                            "track_id": track_id,
                            "playlist_index": playlist_idx,
                        },
                        "style": "itemplay",
                        "trackType": _trk_type,
                    }

                    # ── Artwork (LMS Player.pm L601-625) ─────────────
                    # LMS uses proxiedImage() for external URLs so
                    # SqueezePlay fetches via the server.  Fallback:
                    # /html/images/radio.png for remote tracks with no art.
                    _trk_artwork = getattr(track, "artwork_url", None)
                    if album_id:
                        track_dict["icon-id"] = f"/music/{album_id}/cover"
                        track_dict["icon"] = f"/music/{album_id}/cover"
                    elif _trk_artwork:
                        # Server-relative proxied path like LMS proxiedImage().
                        _proxied = _proxied_image_url(_trk_artwork)
                        if _proxied:
                            track_dict["icon"] = _proxied
                            _status_logger.info(
                                "[STATUS-ART] player=%s item_loop[%d].icon=%s (proxied from %s)",
                                ctx.player_id,
                                playlist_idx,
                                _proxied[:120],
                                _trk_artwork[:120],
                            )
                        else:
                            track_dict["icon"] = _trk_artwork
                            _status_logger.info(
                                "[STATUS-ART] player=%s item_loop[%d].icon=%s (raw, not proxied)",
                                ctx.player_id,
                                playlist_idx,
                                _trk_artwork[:120],
                            )
                    elif _trk_is_remote:
                        # Radio placeholder (LMS Player.pm L622).
                        # Server-relative path — resize suffix handled by
                        # our /html/images/ route.
                        track_dict["icon-id"] = "/html/images/radio.png"

                else:
                    # ── Standard playlist_loop format ─────────────────
                    _loop_artwork_url = getattr(track, "artwork_url", None)
                    _loop_is_remote = bool(getattr(track, "is_remote", False))
                    _loop_source = getattr(track, "source", "local") or "local"

                    # Determine cover art + stream URL for this track.
                    if album_id:
                        _loop_cover = f"{server_url}/artwork/{album_id}"
                    elif _loop_artwork_url:
                        _loop_cover = _loop_artwork_url
                    else:
                        _loop_cover = ""

                    # For remote tracks (podcast, radio, external), use the
                    # proxied stream URL so the Web UI can display/play them.
                    # Local library tracks use track_id-based URLs.
                    if _loop_is_remote or track_id is None:
                        _stream_url = getattr(track, "stream_url", None) or path
                        _loop_url = f"{server_url}/stream.mp3?url={_stream_url}"
                    else:
                        _loop_url = f"{server_url}/stream.mp3?track_id={track_id}"

                    track_dict = {
                        "id": track_id if track_id is not None else -playlist_idx - 1,
                        "title": title,
                        "artist": artist or "",
                        "album": album or "",
                        "duration": (duration_ms or 0) / 1000.0,
                        "url": _loop_url,
                        "coverArt": _loop_cover,
                        "playlist index": playlist_idx,
                    }

                    # Remote stream flags (LMS Queries.pm: `remote`,
                    # `remote_title`, trackType).
                    if _loop_is_remote:
                        track_dict["remote"] = 1
                    if _loop_source != "local":
                        track_dict["trackType"] = _loop_source

                    # JiveLite/SqueezePlay compatibility (Squeezebox Radio, Touch, etc.)
                    # These players expect icon-id or icon for cover art display
                    if album_id:
                        track_dict["icon-id"] = f"/music/{album_id}/cover"
                        track_dict["icon"] = f"/music/{album_id}/cover"
                        track_dict["artwork_track_id"] = album_id
                    elif _loop_artwork_url:
                        # Server-relative proxied path like LMS proxiedImage().
                        _proxied_loop = _proxied_image_url(_loop_artwork_url)
                        if _proxied_loop:
                            track_dict["icon"] = _proxied_loop
                        else:
                            track_dict["icon"] = _loop_artwork_url

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

                    # Add optional fields based on tags.
                    # Use getattr() because PlaylistTrack (remote streams like
                    # radio/podcast) does not carry library.Track fields such
                    # as track_no, disc_no, year.
                    if tags is None or "n" in tags:
                        _track_no = getattr(track, "track_no", None)
                        if _track_no:
                            track_dict["tracknum"] = _track_no
                    if tags is None or "i" in tags:
                        _disc_no = getattr(track, "disc_no", None)
                        if _disc_no:
                            track_dict["disc"] = _disc_no
                    if tags is None or "y" in tags:
                        _year = getattr(track, "year", None)
                        if _year:
                            track_dict["year"] = _year

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
