"""
Jive Menu Handler for Squeezebox Controller/Touch/Boom/Radio.

These devices use a special JSON-RPC "menu" query to build their
touch-screen UI. This module provides an implementation that allows
these devices to connect and display menus for browsing music.

Plugins can register additional menu nodes and items via
:meth:`PluginContext.register_menu_node` / :meth:`PluginContext.register_menu_item`.
Those entries are appended automatically by :func:`_build_main_menu`.

Reference: Slim::Control::Jive and Slim::Menu::BrowseLibrary in the LMS codebase.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from resonance.plugin import get_plugin_menu_items, get_plugin_menu_nodes
from resonance.web.handlers.menu_helpers import (
    BROWSELIBRARY,
    browse_actions,
    browse_go,
    browse_menu_item,
    choice_item,
    context_menu_item,
    do_action,
    go_action,
    menu_item,
    menu_node,
    paginated,
    playlist_add,
    playlist_play,
    slider_item,
)
from resonance.web.jsonrpc_helpers import parse_start_items

if TYPE_CHECKING:
    from resonance.web.handlers import CommandContext

logger = logging.getLogger(__name__)

_ALARM_DAY_NAMES = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")


async def cmd_menustatus(ctx: CommandContext, command: list[Any]) -> dict[str, Any]:
    """
    Handle the 'menustatus' query for Jive devices.

    This is a notification mechanism for dynamic menu updates.
    Jive devices subscribe to this to receive menu changes.

    For now, we return an empty response since we don't have
    dynamic menu plugins that need to push updates.

    LMS command: [player_id] menustatus

    Returns:
        Empty dict (no pending menu updates)
    """
    logger.debug("menustatus query: %s", command)
    if len(command) >= 3:
        return {"menu": command[1], "action": command[2]}
    return {}


async def cmd_menu(ctx: CommandContext, command: list[Any]) -> dict[str, Any]:
    """
    Handle the 'menu' query for Jive devices.

    This returns the main menu structure that Squeezebox Controller,
    Touch, Boom, and Radio devices need to build their UI.

    LMS command: [player_id] menu <start> <itemsPerResponse>

    Args:
        ctx: Command context
        command: ['menu', start_index, items_per_response, ...]

    Returns:
        Menu structure with item_loop
    """
    # Parse pagination parameters
    start = 0
    items_per_page = 100

    if len(command) > 1:
        try:
            start = int(command[1])
        except (ValueError, TypeError):
            pass

    if len(command) > 2:
        try:
            items_per_page = int(command[2])
        except (ValueError, TypeError):
            pass

    # Check for 'direct' parameter (used by disconnected players)
    direct = False
    for arg in command[3:]:
        if isinstance(arg, str) and arg == "direct:1":
            direct = True
            break

    logger.debug("menu query: start=%d, items=%d, direct=%s", start, items_per_page, direct)

    # Build the main menu structure (flat, as LMS does)
    menu_items = await _build_main_menu(ctx)

    # Apply pagination
    total_count = len(menu_items)
    paginated_items = menu_items[start : start + items_per_page]

    return {
        "count": total_count,
        "offset": start,
        "item_loop": paginated_items,
    }


async def _build_main_menu(ctx: CommandContext) -> list[dict[str, Any]]:
    """
    Build the main menu structure for Jive devices.

    This returns a flat list of menu items, including nodes and their children.
    The device uses 'node' to build the hierarchy and 'weight' for ordering.

    Reference: Slim::Control::Jive::mainMenu()
    """
    menu: list[dict[str, Any]] = []

    # ── My Music node ────────────────────────────────────────────
    menu.append(menu_node("My Music", id="myMusic", node="home", weight=11))

    # ── My Music children (from BrowseLibrary) ───────────────────
    menu.extend([
        browse_menu_item("Artists",   id="myMusicArtists",  node="myMusic", weight=10, mode="artists"),
        browse_menu_item("Albums",    id="myMusicAlbums",   node="myMusic", weight=20, mode="albums"),
        browse_menu_item("Genres",    id="myMusicGenres",   node="myMusic", weight=30, mode="genres"),
        browse_menu_item("Years",     id="myMusicYears",    node="myMusic", weight=40, mode="years"),
        browse_menu_item("New Music", id="myMusicNewMusic", node="myMusic", weight=50, mode="albums", extra_params={"sort": "new"}),
    ])

    # Search (has input + special go action — not a simple browse_menu_item)
    menu.append(
        menu_item(
            "Search",
            id="myMusicSearch",
            node="myMusic",
            weight=90,
            actions={
                "go": {
                    "cmd": [BROWSELIBRARY, "items"],
                    "params": {"menu": 1, "mode": "search"},
                    "itemsParams": "params",
                },
            },
            input={
                "len": 1,
                "processingPopup": {"text": "Searching..."},
                "help": {"text": "Enter search text"},
            },
            window={"isContextMenu": 1},
        )
    )

    # ── Player Power ─────────────────────────────────────────────
    # Only show if the device supports power off (Boom does not).
    # Reference: Slim::Control::Jive::playerPower()
    player = (
        await ctx.player_registry.get_by_mac(ctx.player_id)
        if ctx.player_id and ctx.player_id != "-"
        else None
    )
    if player is None or player.device_capabilities.can_power_off:
        menu.append(
            menu_item(
                "Turn Player Off",
                id="playerpower",
                node="home",
                weight=100,
                actions=do_action(["power", "0"]),
            )
        )

    # ── Settings node ────────────────────────────────────────────
    menu.append(menu_node("Settings", id="settings", node="home", weight=1005))

    # ── Player Settings children ─────────────────────────────────
    menu.extend([
        choice_item("Repeat",  id="settingsRepeat",  node="settings", weight=10, cmd=["playlist", "repeat"],  choices=["Off", "Song", "Playlist"]),
        choice_item("Shuffle", id="settingsShuffle", node="settings", weight=20, cmd=["playlist", "shuffle"], choices=["Off", "Songs", "Albums"]),
    ])

    # Sleep setting
    menu.append(
        menu_item(
            "Sleep",
            id="settingsSleep",
            node="settings",
            weight=65,
            actions=go_action(["sleepsettings"], player=0),
        )
    )

    # Audio Settings node
    menu.append(menu_node("Audio Settings", id="settingsAudio", node="settings", weight=35))

    # Capability-based Audio Settings
    # Only show settings the device actually supports.
    # Reference: Slim::Control::Jive::playerSettingsMenu()
    await _add_audio_settings(menu, ctx)

    # Advanced Settings node
    menu.append(menu_node("Advanced Settings", id="advancedSettings", node="settings", weight=105))

    # Player Information
    menu.append(
        menu_item(
            "Player Information",
            id="settingsInformation",
            node="advancedSettings",
            weight=100,
            actions=go_action(["playerinfo"], player=0),
        )
    )

    # ── Plugin-registered menu entries ────────────────────────────
    # Plugins add nodes/items via PluginContext.register_menu_node/item.
    # We append them here so they appear alongside the built-in entries
    # and respect the weight-based ordering on the device.
    menu.extend(get_plugin_menu_nodes())
    menu.extend(get_plugin_menu_items())

    return menu


async def _add_audio_settings(menu: list[dict[str, Any]], ctx: CommandContext) -> None:
    """Add audio settings based on device capabilities.

    Only adds menu items for features the connected device actually supports.
    Reference: Slim::Control::Jive::playerSettingsMenu() lines 1395-1591.
    """
    from resonance.player.capabilities import get_device_capabilities
    from resonance.player.client import DeviceType

    # Look up the player to get its capabilities
    player = (
        await ctx.player_registry.get_by_mac(ctx.player_id)
        if ctx.player_id and ctx.player_id != "-"
        else None
    )
    if player:
        caps = player.device_capabilities
    else:
        caps = get_device_capabilities(DeviceType.UNKNOWN)

    _NODE = "settingsAudio"

    # Bass (only if device supports adjustment)
    if caps.has_bass:
        menu.append(slider_item("Bass",      id="settingsBass",      node=_NODE, weight=10, cmd=["mixer", "bass"],     min_val=caps.min_bass,   max_val=caps.max_bass))

    # Treble (only if device supports adjustment)
    if caps.has_treble:
        menu.append(slider_item("Treble",    id="settingsTreble",    node=_NODE, weight=20, cmd=["mixer", "treble"],   min_val=caps.min_treble, max_val=caps.max_treble))

    # StereoXL (only Boom)
    if caps.has_stereo_xl:
        menu.append(slider_item("Stereo XL", id="settingsStereoXL",  node=_NODE, weight=25, cmd=["mixer", "stereoxl"], min_val=caps.min_xl,     max_val=caps.max_xl))

    # Balance (SB2, Transporter, SqueezePlay — NOT Boom)
    if caps.has_balance:
        menu.append(slider_item("Balance",   id="settingsBalance",   node=_NODE, weight=30, cmd=["mixer", "balance"],  min_val=-100,            max_val=100))

    # Fixed Volume / Digital Output (only if device has digital out)
    if caps.has_digital_out:
        menu.append(choice_item("Fixed Volume", id="settingsFixedVolume", node=_NODE, weight=40, cmd=["mixer", "fixedvolume"], choices=["Off", "On"]))

    # Line Out Mode (only if device has headphone/sub out)
    if caps.has_head_sub_out:
        menu.append(choice_item("Line Out",     id="settingsLineOut",     node=_NODE, weight=50, cmd=["mixer", "lineout"],     choices=["Headphone", "Sub Out"]))


async def cmd_browselibrary(ctx: CommandContext, command: list[Any]) -> dict[str, Any]:
    """
    Handle the 'browselibrary' command for Jive menu navigation.

    This is the entry point for browsing the music library from Jive menus.

    LMS command: [player_id] browselibrary items <start> <itemsPerResponse> <params...>

    Args:
        ctx: Command context
        command: ['browselibrary', 'items', start, count, ...]

    Returns:
        Library items in Jive menu format
    """
    # Parse subcommand
    if len(command) < 2:
        return {"count": 0, "item_loop": []}

    subcmd = str(command[1]).lower()

    if subcmd != "items":
        logger.warning("Unknown browselibrary subcommand: %s", subcmd)
        return {"count": 0, "item_loop": []}

    # Parse pagination
    start = 0
    items_per_page = 100

    if len(command) > 2:
        try:
            start = int(command[2])
        except (ValueError, TypeError):
            pass

    if len(command) > 3:
        try:
            items_per_page = int(command[3])
        except (ValueError, TypeError):
            pass

    # Parse parameters
    params: dict[str, Any] = {}
    for arg in command[4:]:
        if isinstance(arg, str) and ":" in arg:
            key, value = arg.split(":", 1)
            params[key] = value
        elif isinstance(arg, dict):
            params.update(arg)

    mode = params.get("mode", "")
    logger.debug("browselibrary: mode=%s, start=%d, items=%d", mode, start, items_per_page)

    # Route to appropriate handler based on mode
    if mode == "artists":
        return await _browse_artists(ctx, start, items_per_page, params)
    elif mode == "albums":
        return await _browse_albums(ctx, start, items_per_page, params)
    elif mode == "genres":
        return await _browse_genres(ctx, start, items_per_page, params)
    elif mode == "years":
        return await _browse_years(ctx, start, items_per_page, params)
    elif mode == "tracks":
        return await _browse_tracks(ctx, start, items_per_page, params)
    elif mode == "search":
        return await _browse_search(ctx, start, items_per_page, params)
    else:
        logger.warning("Unknown browselibrary mode: %s", mode)
        return {"count": 0, "item_loop": []}


async def _browse_artists(
    ctx: CommandContext, start: int, count: int, params: dict[str, Any]
) -> dict[str, Any]:
    """Browse artists in Jive menu format."""
    from resonance.web.handlers.library import cmd_artists

    cmd = ["artists", start, count]
    result = await cmd_artists(ctx, cmd)

    items = []
    for artist in result.get("artists_loop", []):
        artist_id = artist.get("id", "")
        artist_name = artist.get("artist", "Unknown Artist")

        items.append(
            menu_item(
                artist_name,
                id=f"artist_{artist_id}",
                actions=browse_actions(
                    "artist_id", artist_id,
                    go_mode="albums",
                    go_params={"artist_id": artist_id},
                ),
            )
        )

    return paginated(items, count=result.get("count", len(items)), offset=start)


async def _browse_albums(
    ctx: CommandContext, start: int, count: int, params: dict[str, Any]
) -> dict[str, Any]:
    """Browse albums in Jive menu format."""
    from resonance.web.handlers.library import cmd_albums

    cmd: list[Any] = ["albums", start, count]

    # Add filters
    for key in ("artist_id", "genre_id", "year", "sort"):
        if params.get(key):
            cmd.append(f"{key}:{params[key]}")

    # Always request artwork
    cmd.append("tags:aljJ")

    result = await cmd_albums(ctx, cmd)

    items = []
    for album in result.get("albums_loop", []):
        album_id = album.get("id", "")
        album_title = album.get("album", "Unknown Album")
        artist_name = album.get("artist", "")

        item = menu_item(
            album_title,
            id=f"album_{album_id}",
            actions=browse_actions(
                "album_id", album_id,
                go_mode="tracks",
                go_params={"album_id": album_id},
            ),
        )

        # Add artist as second line
        if artist_name:
            item["textkey"] = album_title[0].upper() if album_title else "?"
            item["icon-id"] = album.get("artwork_track_id") or album_id

        # Add artwork URL if available
        artwork_url = album.get("artwork_url")
        if artwork_url:
            item["icon"] = artwork_url
        elif album_id:
            item["icon-id"] = f"/music/{album_id}/cover"

        items.append(item)

    return paginated(items, count=result.get("count", len(items)), offset=start)


async def _browse_genres(
    ctx: CommandContext, start: int, count: int, params: dict[str, Any]
) -> dict[str, Any]:
    """Browse genres in Jive menu format."""
    from resonance.web.handlers.library import cmd_genres

    cmd = ["genres", start, count]
    result = await cmd_genres(ctx, cmd)

    items = []
    for genre in result.get("genres_loop", []):
        genre_id = genre.get("id", "")
        genre_name = genre.get("genre", "Unknown Genre")

        items.append(
            menu_item(
                genre_name,
                id=f"genre_{genre_id}",
                actions=browse_actions(
                    "genre_id", genre_id,
                    go_mode="albums",
                    go_params={"genre_id": genre_id},
                    include_add=False,
                ),
            )
        )

    return paginated(items, count=result.get("count", len(items)), offset=start)


async def _browse_years(
    ctx: CommandContext, start: int, count: int, params: dict[str, Any]
) -> dict[str, Any]:
    """Browse years in Jive menu format."""
    years = await ctx.music_library.get_years()
    years = sorted(years, reverse=True)

    total = len(years)
    page = years[start : start + count]

    items = []
    for year in page:
        year_str = str(year) if year else "Unknown"
        items.append(
            menu_item(
                year_str,
                id=f"year_{year}",
                actions=browse_actions(
                    "year", year,
                    go_mode="albums",
                    go_params={"year": year},
                    include_add=False,
                ),
            )
        )

    return paginated(items, count=total, offset=start)


async def _play_control_context_menu(
    ctx: CommandContext,
    params: dict[str, Any],
    *,
    album_id: str | None,
    play_index: int,
) -> dict[str, Any]:
    """Return a play-control context menu for a single track position.

    This is the response to ``browselibrary items … xmlbrowserPlayControl:N``.
    LMS returns 4 items (verified against real LMS 9.0.4):

    1. Add to end          — ``playlistcontrol cmd:add track_id:X``
    2. Play next           — ``playlistcontrol cmd:insert track_id:X``
    3. Play this song      — ``playlistcontrol cmd:load track_id:X``
    4. Play all songs      — ``playlistcontrol cmd:load album_id:A play_index:N``
    """
    from resonance.web.handlers.library import cmd_titles

    # Look up the track at the given index to get its track_id.
    lookup_cmd: list[Any] = ["titles", play_index, 1]
    if album_id:
        lookup_cmd.append(f"album_id:{album_id}")
    if params.get("artist_id"):
        lookup_cmd.append(f"artist_id:{params['artist_id']}")
    if params.get("genre_id"):
        lookup_cmd.append(f"genre_id:{params['genre_id']}")
    lookup_cmd.append("tags:aAdtl")

    result = await cmd_titles(ctx, lookup_cmd)
    tracks = result.get("titles_loop", [])
    track_id = tracks[0].get("id") if tracks else None

    items: list[dict[str, Any]] = []

    if track_id is not None:
        items.append(context_menu_item(
            "Add to end", style="item_add",
            cmd=["playlistcontrol"],
            params={"cmd": "add", "track_id": track_id, "menu": 1},
            next_window="parentNoRefresh",
        ))
        items.append(context_menu_item(
            "Play next", style="itemNoAction",
            cmd=["playlistcontrol"],
            params={"cmd": "insert", "track_id": track_id, "menu": 1},
            next_window="parentNoRefresh",
        ))
        items.append(context_menu_item(
            "Play this song", style="item_play",
            cmd=["playlistcontrol"],
            params={"cmd": "load", "track_id": track_id, "menu": 1},
            next_window="nowPlaying",
        ))

    # Play all songs (always available when album_id is known)
    if album_id:
        items.append(context_menu_item(
            "Play all songs", style="itemNoAction",
            cmd=["playlistcontrol"],
            params={
                "cmd": "load",
                "album_id": str(album_id),
                "sort": "albumtrack",
                "play_index": play_index,
                "menu": 1,
            },
            next_window="nowPlaying",
        ))

    return paginated(items, window={"windowStyle": "text_list"})


async def _browse_tracks(
    ctx: CommandContext, start: int, count: int, params: dict[str, Any]
) -> dict[str, Any]:
    """Browse tracks in Jive menu format.

    LMS model (verified against real LMS 9.0.4):
    - ``base.actions.play`` loads the ENTIRE album (``album_id``) and jumps to
      the selected track via ``play_index`` (from per-item ``playallParams``).
    - ``base.actions.add`` adds the entire album.
    - ``base.actions.add-hold`` inserts a single track (via ``commonParams``).
    - ``base.actions.more`` opens a context menu for the track.
    - Each item carries ``commonParams`` (track_id), ``playallParams``
      (play_index), and ``playControlParams`` (xmlbrowserPlayControl).

    When ``xmlbrowserPlayControl`` is present in *params*, return a play-control
    context menu (Add / Play next / Play this / Play all) instead of the normal
    track list.  This is triggered by the ``goAction: "playControl"`` on each
    track item — JiveLite merges ``playControlParams`` into the action and
    sends the request back here.  Without this handler the same track list
    would be returned, creating an infinite navigation loop.

    Verified against real LMS 9.0.4:
    ``browselibrary items mode:tracks album_id:1 menu:menu useContextMenu:1 xmlbrowserPlayControl:0``
    returns a 4-item context menu.
    """
    from resonance.web.handlers.library import cmd_titles

    album_id = params.get("album_id")
    artist_id = params.get("artist_id")
    genre_id = params.get("genre_id")

    # ── Play-control context menu (xmlbrowserPlayControl) ────────
    # When present, return the 4-option context menu instead of the track list.
    # Only shown when the playlist already has tracks; otherwise the play
    # action fires directly (see goAction logic below).
    play_control = params.get("xmlbrowserPlayControl")
    if play_control is not None:
        return await _play_control_context_menu(
            ctx, params, album_id=album_id, play_index=int(play_control),
        )

    # Check whether the player's playlist is empty.  This determines
    # whether Enter on a track plays immediately or opens a context menu.
    playlist_empty = True
    if ctx.playlist_manager and ctx.player_id and ctx.player_id != "-":
        playlist = ctx.playlist_manager.get(ctx.player_id)
        playlist_empty = playlist.is_empty

    cmd: list[Any] = ["titles", start, count]

    # Add filters
    if album_id:
        cmd.append(f"album_id:{album_id}")
    if artist_id:
        cmd.append(f"artist_id:{artist_id}")
    if genre_id:
        cmd.append(f"genre_id:{genre_id}")

    # Request useful tags
    cmd.append("tags:aAdtl")

    result = await cmd_titles(ctx, cmd)

    # ── base.actions (LMS-style) ─────────────────────────────────
    # When an album_id is available, play/add operate on the whole album.
    # ``itemsParams`` tells JiveLite which per-item dict to merge into params.
    base: dict[str, Any] = {"actions": {}}

    if album_id:
        base["actions"]["play"] = {
            "player": 0,
            "cmd": ["playlistcontrol"],
            "params": {
                "cmd": "load",
                "album_id": str(album_id),
                "sort": "albumtrack",
                "menu": 1,
            },
            "itemsParams": "playallParams",
            "nextWindow": "nowPlaying",
        }
        base["actions"]["add"] = {
            "player": 0,
            "cmd": ["playlistcontrol"],
            "params": {
                "cmd": "add",
                "album_id": str(album_id),
                "sort": "albumtrack",
                "menu": 1,
            },
        }
        base["actions"]["add-hold"] = {
            "player": 0,
            "cmd": ["playlistcontrol"],
            "params": {
                "cmd": "insert",
                "menu": 1,
            },
            "itemsParams": "commonParams",
        }
        base["actions"]["more"] = {
            "player": 0,
            "cmd": ["trackinfo", "items"],
            "params": {
                "album_id": str(album_id),
                "menu": 1,
            },
            "itemsParams": "commonParams",
            "window": {"isContextMenu": 1},
        }
        base["actions"]["playControl"] = {
            "player": 0,
            "cmd": ["browselibrary", "items"],
            "params": {
                "mode": "tracks",
                "album_id": str(album_id),
                "menu": "menu",
                "useContextMenu": "1",
            },
            "itemsParams": "playControlParams",
            "window": {"isContextMenu": 1},
        }
        # Playlist empty → Enter plays directly (go = play action).
        # Playlist not empty → Enter opens context menu (go = playControl).
        if playlist_empty:
            base["actions"]["go"] = base["actions"]["play"]
        else:
            base["actions"]["go"] = base["actions"]["playControl"]
    else:
        # No album context — fall back to per-track actions via base
        base["actions"]["play"] = {
            "player": 0,
            "cmd": ["playlistcontrol"],
            "params": {"cmd": "load"},
            "itemsParams": "commonParams",
            "nextWindow": "nowPlaying",
        }
        base["actions"]["add"] = {
            "player": 0,
            "cmd": ["playlistcontrol"],
            "params": {"cmd": "add"},
            "itemsParams": "commonParams",
        }
        base["actions"]["add-hold"] = {
            "player": 0,
            "cmd": ["playlistcontrol"],
            "params": {"cmd": "insert"},
            "itemsParams": "commonParams",
        }

    base["window"] = {"windowStyle": "text_list"}

    # ── item_loop ────────────────────────────────────────────────
    items = []
    for idx, track in enumerate(result.get("titles_loop", [])):
        track_id = track.get("id", "")
        track_title = track.get("title", "Unknown Track")
        artist_name = track.get("artist", "")

        item: dict[str, Any] = {
            "text": track_title,
            "type": "audio",
            "commonParams": {
                "track_id": track_id,
            },
            "playallParams": {
                "play_index": start + idx,
            },
            "playControlParams": {
                "xmlbrowserPlayControl": str(start + idx),
            },
        }

        # Playlist empty → Enter triggers "play" (loads album, starts at
        # this track).  Playlist not empty → Enter triggers "playControl"
        # (opens context menu with Add/Insert/Play/Play-all).
        if not playlist_empty:
            item["goAction"] = "playControl"

        # Add artist as second line if available
        if artist_name:
            item["textkey"] = track_title[0].upper() if track_title else "?"

        items.append(item)

    return {
        "count": result.get("count", len(items)),
        "offset": start,
        "item_loop": items,
        "base": base,
    }


async def _browse_search(
    ctx: CommandContext, start: int, count: int, params: dict[str, Any]
) -> dict[str, Any]:
    """Handle search in Jive menu format."""
    search_term = params.get("search", "")

    if not search_term:
        # Return search input prompt
        return {
            "count": 1,
            "offset": 0,
            "item_loop": [
                {
                    "text": "Enter search term",
                    "style": "itemNoAction",
                    "type": "text",
                }
            ],
        }

    from resonance.web.handlers.library import cmd_search

    cmd = ["search", 0, count, f"term:{search_term}"]
    result = await cmd_search(ctx, cmd)

    items = []

    # Add artists found
    for artist in result.get("artists_loop", [])[:5]:
        artist_id = artist.get("id", "")
        artist_name = artist.get("artist", "")
        items.append(menu_item(
            artist_name,
            id=f"search_artist_{artist_id}",
            textkey="A",
            actions=browse_go("albums", {"artist_id": artist_id}),
        ))

    # Add albums found
    for album in result.get("albums_loop", [])[:5]:
        album_id = album.get("id", "")
        album_title = album.get("album", "")
        actions: dict[str, Any] = {
            **browse_go("tracks", {"album_id": album_id}),
            **playlist_play("album_id", album_id),
        }
        items.append(menu_item(
            album_title,
            id=f"search_album_{album_id}",
            icon_id=f"/music/{album_id}/cover",
            textkey="B",
            actions=actions,
        ))

    # Add tracks found
    for track in result.get("titles_loop", [])[:10]:
        track_id = track.get("id", "")
        track_title = track.get("title", "")
        items.append(menu_item(
            track_title,
            id=f"search_track_{track_id}",
            item_type="audio",
            textkey="C",
            actions={
                **playlist_play("track_id", track_id),
                **playlist_add("track_id", track_id),
            },
        ))

    return paginated(items)


def _trackinfo_library_menu(
    track_id: str | int | None,
    album_id: str | int | None,
    play_index: int | None,
) -> dict[str, Any]:
    """Build a simple track context menu for library browsing."""
    items: list[dict[str, Any]] = []

    if track_id is not None:
        track_id_str = str(track_id)

        items.append(context_menu_item(
            "Add to end", style="item_add",
            cmd=["playlist", "add", track_id_str],
            next_window="parentNoRefresh",
        ))
        items.append(context_menu_item(
            "Play next", style="itemNoAction",
            cmd=["playlist", "insert", track_id_str],
            next_window="parentNoRefresh",
        ))
        items.append(context_menu_item(
            "Play this song", style="item_play",
            cmd=["playlist", "loadtracks", f"track_id:{track_id_str}"],
            next_window="nowPlaying",
        ))

    if album_id is not None:
        album_id_str = str(album_id)
        if play_index is None or play_index < 0:
            play_index = 0

        items.append(context_menu_item(
            "Play all songs", style="itemNoAction",
            cmd=["playlist", "loadtracks", f"album_id:{album_id_str}", f"play_index:{play_index}"],
            next_window="nowPlaying",
        ))

    return paginated(items, window={"windowStyle": "text_list"})



def _trackinfo_playlist_menu(
    *,
    playlist_index: int,
    current_index: int,
) -> dict[str, Any]:
    """Build playlist-context actions (jump/move/delete) for trackinfo."""
    items: list[dict[str, Any]] = []

    items.append(context_menu_item(
        "Play this song", style="item_play",
        cmd=["playlist", "jump", str(playlist_index)],
        next_window="parent",
    ))

    # Mimic LMS playlist context behavior: move selected item to "next" slot.
    if playlist_index not in (current_index, current_index + 1):
        move_to = current_index + 1
        if playlist_index <= current_index:
            move_to = current_index

        items.append(context_menu_item(
            "Play next", style="itemNoAction",
            cmd=["playlist", "move", str(playlist_index), str(move_to)],
            next_window="parent",
        ))

    items.append(context_menu_item(
        "Remove from playlist", style="itemNoAction",
        cmd=["playlist", "delete", str(playlist_index)],
        next_window="parent",
    ))

    return paginated(items, window={"windowStyle": "text_list"})


async def cmd_trackinfo(ctx: CommandContext, command: list[Any]) -> dict[str, Any]:
    """Handle the Jive `trackinfo items` context menu command."""
    if len(command) < 2 or str(command[1]).lower() != "items":
        return {"count": 0, "offset": 0, "item_loop": []}

    params: dict[str, Any] = {}
    for arg in command[2:]:
        if isinstance(arg, dict):
            params.update(arg)
        elif isinstance(arg, str) and ":" in arg:
            key, value = arg.split(":", 1)
            params[key] = value

    context = str(params.get("context", "normal")).lower()
    track_id = params.get("track_id")
    album_id = params.get("album_id")

    play_index: int | None = None
    if params.get("play_index") is not None:
        try:
            play_index = int(str(params.get("play_index")))
        except (TypeError, ValueError):
            play_index = None

    playlist_index: int | None = None
    if params.get("playlist_index") is not None:
        try:
            playlist_index = int(str(params.get("playlist_index")))
        except (TypeError, ValueError):
            playlist_index = None

    # If playlist_index is provided, try to hydrate missing track/album metadata.
    current_index = 0
    if ctx.playlist_manager is not None and ctx.player_id and ctx.player_id != "-":
        playlist = ctx.playlist_manager.get(ctx.player_id)
        if playlist is not None:
            current_index = playlist.current_index
            if playlist_index is not None and 0 <= playlist_index < len(playlist):
                track = list(playlist.tracks)[playlist_index]
                if track_id is None:
                    track_id = getattr(track, "id", getattr(track, "track_id", None))
                if album_id is None:
                    album_id = getattr(track, "album_id", None)

    if context == "playlist" and playlist_index is not None:
        return _trackinfo_playlist_menu(
            playlist_index=playlist_index,
            current_index=current_index,
        )

    return _trackinfo_library_menu(track_id=track_id, album_id=album_id, play_index=play_index)

async def cmd_contextmenu(ctx: CommandContext, command: list[Any]) -> dict[str, Any]:
    """Handle `contextmenu` by proxying to the corresponding *info handler."""
    params: dict[str, Any] = {}
    for arg in command[1:]:
        if isinstance(arg, dict):
            params.update(arg)
        elif isinstance(arg, str) and ":" in arg:
            key, value = arg.split(":", 1)
            params[key] = value

    menu = str(params.get("menu", "")).lower()
    if not menu:
        return {"count": 0, "offset": 0, "item_loop": []}

    if menu == "track":
        proxied_command: list[Any] = ["trackinfo", "items"]
        for key, value in params.items():
            if key == "menu":
                continue
            proxied_command.append(f"{key}:{value}")
        return await cmd_trackinfo(ctx, proxied_command)

    return {"count": 0, "offset": 0, "item_loop": []}

async def cmd_jiveblankcommand(ctx: CommandContext, command: list[Any]) -> dict[str, Any]:
    """No-op command used by Jive context menus to close/return."""
    return {}

async def cmd_date(ctx: CommandContext, command: list[Any]) -> dict[str, Any]:
    """
    Handle the 'date' query for Jive devices.

    Returns the current date/time for clock display.

    Args:
        ctx: Command context
        command: ['date', ...]

    Returns:
        Date/time information
    """
    now = time.time()
    local_time = time.localtime(now)

    return {
        "date_epoch": int(now),
        "date": time.strftime("%Y-%m-%d", local_time),
        "time": int(now),
        "timezone": time.strftime("%Z", local_time),
        "timezone_offset": -time.timezone if time.daylight == 0 else -time.altzone,
    }


async def cmd_alarm_settings(
    ctx: CommandContext, command: list[Any]
) -> dict[str, Any]:
    """
    Handle the 'alarmsettings' query for Jive devices.

    Returns alarm configuration with LMS-style menu items.
    """
    if ctx.player_id == "-":
        return {"count": 0, "offset": 0, "item_loop": []}

    from resonance.web.handlers.alarm import cmd_alarms

    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(str(value))
        except (TypeError, ValueError):
            return default

    def _format_alarm_text(alarm: dict[str, Any], index: int) -> str:
        seconds = _safe_int(alarm.get("time"), 0)
        if seconds < 0:
            seconds = 0
        hour = (seconds // 3600) % 24
        minute = (seconds % 3600) // 60

        repeat = _safe_int(alarm.get("repeat"), 1) != 0
        dow: list[int] = []
        raw_dow = str(alarm.get("dow", "")).strip()
        if raw_dow:
            for part in raw_dow.split(","):
                part = part.strip()
                if part == "":
                    continue
                day = _safe_int(part, -1)
                if 0 <= day <= 6:
                    dow.append(day)

        if repeat:
            if len(set(dow)) >= 7:
                day_text = "Daily"
            elif dow:
                day_text = ",".join(_ALARM_DAY_NAMES[day] for day in sorted(set(dow)))
            else:
                day_text = "Daily"
        else:
            day_text = "Once"

        return f"Alarm {index}: {hour:02d}:{minute:02d} ({day_text})"

    alarms_response = await cmd_alarms(ctx, ["alarms", "0", "1000", "filter:all"])
    alarms_loop = alarms_response.get("alarms_loop", [])
    if not isinstance(alarms_loop, list):
        alarms_loop = []

    menu_items: list[dict[str, Any]] = []
    any_enabled = any(_safe_int(alarm.get("enabled"), 0) != 0 for alarm in alarms_loop)
    menu_items.append(
        {
            "text": "All alarms",
            "choiceStrings": ["Off", "On"],
            "selectedIndex": 2 if any_enabled else 1,
            "actions": {
                "do": {
                    "choices": [
                        {"player": 0, "cmd": ["alarm", "disableall"]},
                        {"player": 0, "cmd": ["alarm", "enableall"]},
                    ]
                }
            },
        }
    )

    for index, alarm in enumerate(alarms_loop, start=1):
        alarm_id = str(alarm.get("id", "")).strip()
        if alarm_id == "":
            continue

        enabled = _safe_int(alarm.get("enabled"), 0) != 0
        menu_items.append(
            {
                "text": _format_alarm_text(alarm, index),
                "checkbox": int(enabled),
                "actions": {
                    "on": {"player": 0, "cmd": ["alarm", "update", f"id:{alarm_id}", "enabled:1"]},
                    "off": {"player": 0, "cmd": ["alarm", "update", f"id:{alarm_id}", "enabled:0"]},
                },
            }
        )

    menu_items.append(
        {
            "text": "Add alarm (07:00)",
            "actions": {"go": {"player": 0, "cmd": ["alarm", "add", "time:25200", "enabled:1"]}},
            "nextWindow": "refresh",
        }
    )

    start, items = parse_start_items(command)
    if start < 0:
        start = 0
    if items < 0:
        items = 0

    return {"count": len(menu_items), "offset": start, "item_loop": menu_items[start : start + items]}


# (label, seconds, is_current_default)
_SLEEP_OPTIONS: list[tuple[str, str]] = [
    ("Off", "0"),
    ("15 minutes", "900"),
    ("30 minutes", "1800"),
    ("45 minutes", "2700"),
    ("1 hour", "3600"),
    ("2 hours", "7200"),
]


async def cmd_sleep_settings(
    ctx: CommandContext, command: list[Any]
) -> dict[str, Any]:
    """
    Handle the 'sleepsettings' query for Jive devices.

    Returns sleep timer options.
    """
    sleep_options = [
        {
            "text": label,
            "radio": 1 if idx == 0 else 0,
            "nextWindow": "parent",
            **do_action(["sleep", seconds]),
        }
        for idx, (label, seconds) in enumerate(_SLEEP_OPTIONS)
    ]

    return paginated(sleep_options)


async def cmd_sync_settings(
    ctx: CommandContext, command: list[Any]
) -> dict[str, Any]:
    """
    Handle the 'syncsettings' query for Jive devices.

    Returns sync group settings with available target players.
    """
    if ctx.player_id == "-":
        return {"count": 0, "offset": 0, "item_loop": []}

    current_player = await ctx.player_registry.get_by_mac(ctx.player_id)
    if current_player is None:
        return {"count": 0, "offset": 0, "item_loop": []}

    players = await ctx.player_registry.get_all()
    candidates = [player for player in players if player.mac_address != ctx.player_id]
    if not candidates:
        return {
            "window": {"textarea": "No other players available for sync."},
            "count": 0,
            "offset": 0,
            "item_loop": [],
        }

    buddies = await ctx.player_registry.get_sync_buddies(ctx.player_id)
    synced_ids = {buddy.mac_address for buddy in buddies}

    menu_items: list[dict[str, Any]] = []
    for candidate in sorted(
        candidates,
        key=lambda player: str(getattr(player, "name", "") or player.mac_address).lower(),
    ):
        label = str(getattr(candidate, "name", "") or candidate.mac_address)
        menu_items.append(
            {
                "text": label,
                "radio": 1 if candidate.mac_address in synced_ids else 0,
                "actions": {
                    "do": {
                        "player": 0,
                        "cmd": ["sync", candidate.mac_address],
                    }
                },
                "nextWindow": "refresh",
            }
        )

    if synced_ids:
        menu_items.append(
            {
                "text": "Do not sync",
                "radio": 0,
                "actions": {"do": {"player": 0, "cmd": ["sync", "-"]}},
                "nextWindow": "refresh",
            }
        )

    start, items = parse_start_items(command)
    if start < 0:
        start = 0
    if items < 0:
        items = 0

    return {"count": len(menu_items), "offset": start, "item_loop": menu_items[start : start + items]}


async def cmd_firmwareupgrade(
    ctx: CommandContext, command: list[Any]
) -> dict[str, Any]:
    """
    Handle the 'firmwareupgrade' query for Jive devices.

    Tells the device there's no firmware upgrade available.
    Also uses the optional machine tag to refine device model classification.

    LMS returns:
    - firmwareUpgrade: 0 (no upgrade needed) or 1 (upgrade required)
    - relativeFirmwareUrl or firmwareUrl: URL to firmware file (if upgrade available)
    """
    machine: str | None = None
    for part in command[1:]:
        if isinstance(part, str) and part.startswith("machine:"):
            machine = part.split(":", 1)[1].strip().lower() or None

    if machine:
        player = None
        if ctx.player_id and ctx.player_id != "-":
            player = await ctx.player_registry.get_by_mac(ctx.player_id)
        if player is None:
            players = await ctx.player_registry.get_all()
            if len(players) == 1:
                player = players[0]
        if player is not None and hasattr(player, "info"):
            player.info.model = machine

    return {
        "firmwareUpgrade": 0,
    }
async def _playlistcontrol_query_tracks(ctx: CommandContext, params: dict[str, Any]) -> list[Any]:
    """Resolve playlistcontrol filters to a list of PlaylistTrack objects."""
    from resonance.core.playlist import PlaylistTrack

    db = ctx.music_library._db

    def _to_track(row: Any) -> PlaylistTrack:
        return PlaylistTrack(
            track_id=getattr(row, "id", None),
            path=getattr(row, "path", ""),
            title=getattr(row, "title", "") or "",
            artist=getattr(row, "artist", "") or "",
            album=getattr(row, "album", "") or "",
            artist_id=getattr(row, "artist_id", None),
            album_id=getattr(row, "album_id", None),
            duration_ms=getattr(row, "duration_ms", 0) or 0,
        )

    track_id = int(params["track_id"]) if params.get("track_id") else None
    album_id = int(params["album_id"]) if params.get("album_id") else None
    artist_id = int(params["artist_id"]) if params.get("artist_id") else None
    genre_id = int(params["genre_id"]) if params.get("genre_id") else None
    year = int(params["year"]) if params.get("year") else None

    rows: list[Any] = []
    if track_id is not None:
        row = await db.get_track_by_id(track_id)
        rows = [row] if row else []
    elif album_id is not None:
        rows = await db.list_tracks_by_album(album_id=album_id, offset=0, limit=1000, order_by="album")
    elif artist_id is not None:
        rows = await db.list_tracks_by_artist(artist_id=artist_id, offset=0, limit=1000, order_by="album")
    elif genre_id is not None:
        rows = await db.list_tracks_by_genre_id(genre_id=genre_id, offset=0, limit=1000, order_by="title")
    elif year is not None:
        rows = await db.list_tracks_by_year(year=year, offset=0, limit=1000, order_by="album")

    return [_to_track(row) for row in rows]


async def cmd_playlistcontrol(
    ctx: CommandContext, command: list[Any]
) -> dict[str, Any]:
    """
    Handle the 'playlistcontrol' command for Jive menu actions.

    This maps Jive actions (load/add/insert) to existing playlist behavior.
    """
    params: dict[str, Any] = {}
    for arg in command[1:]:
        if isinstance(arg, dict):
            params.update(arg)
        elif isinstance(arg, str) and ":" in arg:
            key, value = arg.split(":", 1)
            params[key] = value

    cmd_action = str(params.get("cmd", "load")).lower()
    logger.debug("playlistcontrol: cmd=%s, params=%s", cmd_action, params)

    from resonance.web.handlers.playlist import cmd_playlist

    # LOAD keeps LMS behavior through playlist loadtracks/start logic.
    if cmd_action == "load":
        if params.get("track_id"):
            playlist_cmd = ["playlist", "loadtracks", f"track_id:{params['track_id']}"]
        elif params.get("album_id"):
            playlist_cmd = ["playlist", "loadtracks", f"album_id:{params['album_id']}"]
            if params.get("play_index"):
                playlist_cmd.append(f"play_index:{params['play_index']}")
        elif params.get("artist_id"):
            playlist_cmd = ["playlist", "loadtracks", f"artist_id:{params['artist_id']}"]
        elif params.get("genre_id"):
            playlist_cmd = ["playlist", "loadtracks", f"genre_id:{params['genre_id']}"]
        elif params.get("year"):
            playlist_cmd = ["playlist", "loadtracks", f"year:{params['year']}"]
        else:
            logger.warning("playlistcontrol: no recognized parameters")
            return {}

        return await cmd_playlist(ctx, playlist_cmd)

    if cmd_action not in ("add", "insert"):
        return {}

    if ctx.player_id == "-":
        return {"error": "No player specified"}
    if ctx.playlist_manager is None:
        return {"error": "Playlist manager not available"}

    try:
        tracks = await _playlistcontrol_query_tracks(ctx, params)
    except (TypeError, ValueError):
        return {"error": "Invalid playlistcontrol parameters"}

    if not tracks:
        return {}

    playlist = ctx.playlist_manager.get(ctx.player_id)

    if cmd_action == "add":
        for track in tracks:
            playlist.add(track)
    else:
        position = playlist.current_index + 1
        for track in tracks:
            playlist.add(track, position=position)
            position += 1

    # Trigger immediate Jive status refresh after queue mutation.
    from resonance.core.events import PlayerPlaylistEvent, event_bus

    await event_bus.publish(
        PlayerPlaylistEvent(
            player_id=ctx.player_id,
            action=cmd_action,
            count=len(playlist),
        )
    )

    return {"count": len(playlist)}


async def cmd_playerinfo(ctx: CommandContext, command: list[Any]) -> dict[str, Any]:
    """
    Handle the 'playerinfo' command for Jive devices.

    Returns player information for the settings menu.
    """
    player = await ctx.player_registry.get_by_mac(ctx.player_id)

    if not player:
        return paginated([menu_item("Player not found", style="item_no_arrow")])

    items = [
        menu_item(f"Name: {player.name}", style="item_no_arrow"),
        menu_item(f"Model: {player.info.model}", style="item_no_arrow"),
        menu_item(f"MAC: {player.info.mac_address}", style="item_no_arrow"),
        menu_item(f"Firmware: {player.info.firmware_version}", style="item_no_arrow"),
        menu_item("Server: Resonance", style="item_no_arrow"),
    ]

    return paginated(items)
