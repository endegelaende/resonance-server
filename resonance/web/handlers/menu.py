"""
Jive Menu Handler for Squeezebox Controller/Touch/Boom/Radio.

These devices use a special JSON-RPC "menu" query to build their
touch-screen UI. This module provides an implementation that allows
these devices to connect and display menus for browsing music.

Reference: Slim::Control::Jive and Slim::Menu::BrowseLibrary in the LMS codebase.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from resonance.web.jsonrpc_helpers import parse_start_items

if TYPE_CHECKING:
    from resonance.web.handlers import CommandContext

logger = logging.getLogger(__name__)

# Constants matching LMS
BROWSELIBRARY = "browselibrary"
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

    # ========================================
    # My Music node - main music browsing entry
    # ========================================
    menu.append(
        {
            "text": "My Music",
            "id": "myMusic",
            "node": "home",
            "weight": 11,
            "isANode": 1,
        }
    )

    # ========================================
    # My Music children (from BrowseLibrary)
    # ========================================

    # Artists
    menu.append(
        {
            "text": "Artists",
            "id": "myMusicArtists",
            "node": "myMusic",
            "weight": 10,
            "actions": {
                "go": {
                    "cmd": [BROWSELIBRARY, "items"],
                    "params": {
                        "menu": 1,
                        "mode": "artists",
                    },
                },
            },
        }
    )

    # Albums
    menu.append(
        {
            "text": "Albums",
            "id": "myMusicAlbums",
            "node": "myMusic",
            "weight": 20,
            "actions": {
                "go": {
                    "cmd": [BROWSELIBRARY, "items"],
                    "params": {
                        "menu": 1,
                        "mode": "albums",
                    },
                },
            },
        }
    )

    # Genres
    menu.append(
        {
            "text": "Genres",
            "id": "myMusicGenres",
            "node": "myMusic",
            "weight": 30,
            "actions": {
                "go": {
                    "cmd": [BROWSELIBRARY, "items"],
                    "params": {
                        "menu": 1,
                        "mode": "genres",
                    },
                },
            },
        }
    )

    # Years
    menu.append(
        {
            "text": "Years",
            "id": "myMusicYears",
            "node": "myMusic",
            "weight": 40,
            "actions": {
                "go": {
                    "cmd": [BROWSELIBRARY, "items"],
                    "params": {
                        "menu": 1,
                        "mode": "years",
                    },
                },
            },
        }
    )

    # New Music
    menu.append(
        {
            "text": "New Music",
            "id": "myMusicNewMusic",
            "node": "myMusic",
            "weight": 50,
            "actions": {
                "go": {
                    "cmd": [BROWSELIBRARY, "items"],
                    "params": {
                        "menu": 1,
                        "mode": "albums",
                        "sort": "new",
                    },
                },
            },
        }
    )

    # Search
    menu.append(
        {
            "text": "Search",
            "id": "myMusicSearch",
            "node": "myMusic",
            "weight": 90,
            "input": {
                "len": 1,
                "processingPopup": {
                    "text": "Searching...",
                },
                "help": {
                    "text": "Enter search text",
                },
            },
            "actions": {
                "go": {
                    "cmd": [BROWSELIBRARY, "items"],
                    "params": {
                        "menu": 1,
                        "mode": "search",
                    },
                    "itemsParams": "params",
                },
            },
            "window": {
                "isContextMenu": 1,
            },
        }
    )

    # ========================================
    # Player Power (like LMS playerPower)
    # ========================================
    # Only show if the device supports power off (Boom does not).
    # Reference: Slim::Control::Jive::playerPower()
    player = (
        await ctx.player_registry.get_by_mac(ctx.player_id)
        if ctx.player_id and ctx.player_id != "-"
        else None
    )
    if player is None or player.device_capabilities.can_power_off:
        menu.append(
            {
                "text": "Turn Player Off",
                "id": "playerpower",
                "node": "home",
                "weight": 100,
                "actions": {
                    "do": {
                        "player": 0,
                        "cmd": ["power", "0"],
                    },
                },
            }
        )

    # ========================================
    # Settings node
    # ========================================
    menu.append(
        {
            "text": "Settings",
            "id": "settings",
            "node": "home",
            "weight": 1005,
            "isANode": 1,
        }
    )

    # ========================================
    # Player Settings children
    # ========================================

    # Repeat setting
    menu.append(
        {
            "text": "Repeat",
            "id": "settingsRepeat",
            "node": "settings",
            "weight": 10,
            "choiceStrings": ["Off", "Song", "Playlist"],
            "actions": {
                "do": {
                    "player": 0,
                    "cmd": ["playlist", "repeat"],
                    "params": {"valtag": "value"},
                },
            },
        }
    )

    # Shuffle setting
    menu.append(
        {
            "text": "Shuffle",
            "id": "settingsShuffle",
            "node": "settings",
            "weight": 20,
            "choiceStrings": ["Off", "Songs", "Albums"],
            "actions": {
                "do": {
                    "player": 0,
                    "cmd": ["playlist", "shuffle"],
                    "params": {"valtag": "value"},
                },
            },
        }
    )

    # Sleep setting
    menu.append(
        {
            "text": "Sleep",
            "id": "settingsSleep",
            "node": "settings",
            "weight": 65,
            "actions": {
                "go": {
                    "cmd": ["sleepsettings"],
                    "player": 0,
                },
            },
        }
    )

    # Audio Settings node
    menu.append(
        {
            "text": "Audio Settings",
            "id": "settingsAudio",
            "node": "settings",
            "weight": 35,
            "isANode": 1,
        }
    )

    # ========================================
    # Capability-based Audio Settings
    # ========================================
    # Only show settings the device actually supports.
    # Reference: Slim::Control::Jive::playerSettingsMenu()
    await _add_audio_settings(menu, ctx)

    # Advanced Settings node
    menu.append(
        {
            "text": "Advanced Settings",
            "id": "advancedSettings",
            "node": "settings",
            "weight": 105,
            "isANode": 1,
        }
    )

    # Player Information
    menu.append(
        {
            "text": "Player Information",
            "id": "settingsInformation",
            "node": "advancedSettings",
            "weight": 100,
            "actions": {
                "go": {
                    "cmd": ["playerinfo"],
                    "player": 0,
                },
            },
        }
    )

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

    # Bass (only if device supports adjustment)
    if caps.has_bass:
        menu.append(
            {
                "text": "Bass",
                "id": "settingsBass",
                "node": "settingsAudio",
                "weight": 10,
                "slider": 1,
                "min": caps.min_bass,
                "max": caps.max_bass,
                "adjust": 1,
                "actions": {
                    "do": {
                        "player": 0,
                        "cmd": ["mixer", "bass"],
                        "params": {"valtag": "value"},
                    },
                },
            }
        )

    # Treble (only if device supports adjustment)
    if caps.has_treble:
        menu.append(
            {
                "text": "Treble",
                "id": "settingsTreble",
                "node": "settingsAudio",
                "weight": 20,
                "slider": 1,
                "min": caps.min_treble,
                "max": caps.max_treble,
                "adjust": 1,
                "actions": {
                    "do": {
                        "player": 0,
                        "cmd": ["mixer", "treble"],
                        "params": {"valtag": "value"},
                    },
                },
            }
        )

    # StereoXL (only Boom)
    if caps.has_stereo_xl:
        menu.append(
            {
                "text": "Stereo XL",
                "id": "settingsStereoXL",
                "node": "settingsAudio",
                "weight": 25,
                "slider": 1,
                "min": caps.min_xl,
                "max": caps.max_xl,
                "adjust": 1,
                "actions": {
                    "do": {
                        "player": 0,
                        "cmd": ["mixer", "stereoxl"],
                        "params": {"valtag": "value"},
                    },
                },
            }
        )

    # Balance (SB2, Transporter, SqueezePlay — NOT Boom)
    if caps.has_balance:
        menu.append(
            {
                "text": "Balance",
                "id": "settingsBalance",
                "node": "settingsAudio",
                "weight": 30,
                "slider": 1,
                "min": -100,
                "max": 100,
                "adjust": 1,
                "actions": {
                    "do": {
                        "player": 0,
                        "cmd": ["mixer", "balance"],
                        "params": {"valtag": "value"},
                    },
                },
            }
        )

    # Fixed Volume / Digital Output (only if device has digital out)
    if caps.has_digital_out:
        menu.append(
            {
                "text": "Fixed Volume",
                "id": "settingsFixedVolume",
                "node": "settingsAudio",
                "weight": 40,
                "choiceStrings": ["Off", "On"],
                "actions": {
                    "do": {
                        "player": 0,
                        "cmd": ["mixer", "fixedvolume"],
                        "params": {"valtag": "value"},
                    },
                },
            }
        )

    # Line Out Mode (only if device has headphone/sub out)
    if caps.has_head_sub_out:
        menu.append(
            {
                "text": "Line Out",
                "id": "settingsLineOut",
                "node": "settingsAudio",
                "weight": 50,
                "choiceStrings": ["Headphone", "Sub Out"],
                "actions": {
                    "do": {
                        "player": 0,
                        "cmd": ["mixer", "lineout"],
                        "params": {"valtag": "value"},
                    },
                },
            }
        )


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
    # Use the existing artists handler but format for Jive
    from resonance.web.handlers.library import cmd_artists

    # Build command for existing handler
    cmd = ["artists", start, count]
    result = await cmd_artists(ctx, cmd)

    # Convert to Jive menu format
    items = []
    for artist in result.get("artists_loop", []):
        artist_id = artist.get("id", "")
        artist_name = artist.get("artist", "Unknown Artist")

        items.append(
            {
                "text": artist_name,
                "id": f"artist_{artist_id}",
                "actions": {
                    "go": {
                        "cmd": [BROWSELIBRARY, "items"],
                        "params": {
                            "menu": 1,
                            "mode": "albums",
                            "artist_id": artist_id,
                        },
                    },
                    "play": {
                        "player": 0,
                        "cmd": ["playlistcontrol"],
                        "params": {
                            "cmd": "load",
                            "artist_id": artist_id,
                        },
                    },
                    "add": {
                        "player": 0,
                        "cmd": ["playlistcontrol"],
                        "params": {
                            "cmd": "add",
                            "artist_id": artist_id,
                        },
                    },
                },
            }
        )

    return {
        "count": result.get("count", len(items)),
        "offset": start,
        "item_loop": items,
    }


async def _browse_albums(
    ctx: CommandContext, start: int, count: int, params: dict[str, Any]
) -> dict[str, Any]:
    """Browse albums in Jive menu format."""
    from resonance.web.handlers.library import cmd_albums

    # Build command for existing handler
    cmd: list[Any] = ["albums", start, count]

    # Add filters
    if params.get("artist_id"):
        cmd.append(f"artist_id:{params['artist_id']}")
    if params.get("genre_id"):
        cmd.append(f"genre_id:{params['genre_id']}")
    if params.get("year"):
        cmd.append(f"year:{params['year']}")
    if params.get("sort"):
        cmd.append(f"sort:{params['sort']}")

    # Always request artwork
    cmd.append("tags:aljJ")

    result = await cmd_albums(ctx, cmd)

    # Convert to Jive menu format
    items = []
    for album in result.get("albums_loop", []):
        album_id = album.get("id", "")
        album_title = album.get("album", "Unknown Album")
        artist_name = album.get("artist", "")

        item: dict[str, Any] = {
            "text": album_title,
            "id": f"album_{album_id}",
            "actions": {
                "go": {
                    "cmd": [BROWSELIBRARY, "items"],
                    "params": {
                        "menu": 1,
                        "mode": "tracks",
                        "album_id": album_id,
                    },
                },
                "play": {
                    "player": 0,
                    "cmd": ["playlistcontrol"],
                    "params": {
                        "cmd": "load",
                        "album_id": album_id,
                    },
                },
                "add": {
                    "player": 0,
                    "cmd": ["playlistcontrol"],
                    "params": {
                        "cmd": "add",
                        "album_id": album_id,
                    },
                },
            },
        }

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

    return {
        "count": result.get("count", len(items)),
        "offset": start,
        "item_loop": items,
    }


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
            {
                "text": genre_name,
                "id": f"genre_{genre_id}",
                "actions": {
                    "go": {
                        "cmd": [BROWSELIBRARY, "items"],
                        "params": {
                            "menu": 1,
                            "mode": "albums",
                            "genre_id": genre_id,
                        },
                    },
                    "play": {
                        "player": 0,
                        "cmd": ["playlistcontrol"],
                        "params": {
                            "cmd": "load",
                            "genre_id": genre_id,
                        },
                    },
                },
            }
        )

    return {
        "count": result.get("count", len(items)),
        "offset": start,
        "item_loop": items,
    }


async def _browse_years(
    ctx: CommandContext, start: int, count: int, params: dict[str, Any]
) -> dict[str, Any]:
    """Browse years in Jive menu format."""
    # Get unique years from the library
    years = await ctx.music_library.get_years()

    # Sort descending (newest first)
    years = sorted(years, reverse=True)

    total = len(years)
    paginated = years[start : start + count]

    items = []
    for year in paginated:
        year_str = str(year) if year else "Unknown"
        items.append(
            {
                "text": year_str,
                "id": f"year_{year}",
                "actions": {
                    "go": {
                        "cmd": [BROWSELIBRARY, "items"],
                        "params": {
                            "menu": 1,
                            "mode": "albums",
                            "year": year,
                        },
                    },
                    "play": {
                        "player": 0,
                        "cmd": ["playlistcontrol"],
                        "params": {
                            "cmd": "load",
                            "year": year,
                        },
                    },
                },
            }
        )

    return {
        "count": total,
        "offset": start,
        "item_loop": items,
    }


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
        # 1. Add to end
        items.append({
            "text": "Add to end",
            "style": "item_add",
            "actions": {
                "go": {
                    "player": 0,
                    "cmd": ["playlistcontrol"],
                    "params": {"cmd": "add", "track_id": track_id, "menu": 1},
                    "nextWindow": "parentNoRefresh",
                },
            },
        })

        # 2. Play next
        items.append({
            "text": "Play next",
            "style": "itemNoAction",
            "actions": {
                "go": {
                    "player": 0,
                    "cmd": ["playlistcontrol"],
                    "params": {"cmd": "insert", "track_id": track_id, "menu": 1},
                    "nextWindow": "parentNoRefresh",
                },
            },
        })

        # 3. Play this song
        items.append({
            "text": "Play this song",
            "style": "item_play",
            "actions": {
                "go": {
                    "player": 0,
                    "cmd": ["playlistcontrol"],
                    "params": {"cmd": "load", "track_id": track_id, "menu": 1},
                    "nextWindow": "nowPlaying",
                },
            },
        })

    # 4. Play all songs (always available when album_id is known)
    if album_id:
        items.append({
            "text": "Play all songs",
            "style": "itemNoAction",
            "actions": {
                "go": {
                    "player": 0,
                    "cmd": ["playlistcontrol"],
                    "params": {
                        "cmd": "load",
                        "album_id": str(album_id),
                        "sort": "albumtrack",
                        "play_index": play_index,
                        "menu": 1,
                    },
                    "nextWindow": "nowPlaying",
                },
            },
        })

    return {
        "count": len(items),
        "offset": 0,
        "item_loop": items,
        "window": {"windowStyle": "text_list"},
    }


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
        items.append(
            {
                "text": artist_name,
                "id": f"search_artist_{artist_id}",
                "textkey": "A",
                "actions": {
                    "go": {
                        "cmd": [BROWSELIBRARY, "items"],
                        "params": {"menu": 1, "mode": "albums", "artist_id": artist_id},
                    },
                },
            }
        )

    # Add albums found
    for album in result.get("albums_loop", [])[:5]:
        album_id = album.get("id", "")
        album_title = album.get("album", "")
        items.append(
            {
                "text": album_title,
                "id": f"search_album_{album_id}",
                "textkey": "B",
                "icon-id": f"/music/{album_id}/cover",
                "actions": {
                    "go": {
                        "cmd": [BROWSELIBRARY, "items"],
                        "params": {"menu": 1, "mode": "tracks", "album_id": album_id},
                    },
                    "play": {
                        "player": 0,
                        "cmd": ["playlistcontrol"],
                        "params": {"cmd": "load", "album_id": album_id},
                    },
                },
            }
        )

    # Add tracks found
    for track in result.get("titles_loop", [])[:10]:
        track_id = track.get("id", "")
        track_title = track.get("title", "")
        items.append(
            {
                "text": track_title,
                "id": f"search_track_{track_id}",
                "textkey": "C",
                "type": "audio",
                "actions": {
                    "play": {
                        "player": 0,
                        "cmd": ["playlistcontrol"],
                        "params": {"cmd": "load", "track_id": track_id},
                    },
                    "add": {
                        "player": 0,
                        "cmd": ["playlistcontrol"],
                        "params": {"cmd": "add", "track_id": track_id},
                    },
                },
            }
        )

    return {
        "count": len(items),
        "offset": 0,
        "item_loop": items,
    }


def _trackinfo_library_menu(
    track_id: str | int | None,
    album_id: str | int | None,
    play_index: int | None,
) -> dict[str, Any]:
    """Build a simple track context menu for library browsing."""
    items: list[dict[str, Any]] = []

    if track_id is not None:
        track_id_str = str(track_id)

        items.append(
            {
                "text": "Add to end",
                "style": "item_add",
                "actions": {
                    "go": {
                        "player": 0,
                        "cmd": ["playlist", "add", track_id_str],
                        "nextWindow": "parentNoRefresh",
                    },
                },
            }
        )

        items.append(
            {
                "text": "Play next",
                "style": "itemNoAction",
                "actions": {
                    "go": {
                        "player": 0,
                        "cmd": ["playlist", "insert", track_id_str],
                        "nextWindow": "parentNoRefresh",
                    },
                },
            }
        )

        items.append(
            {
                "text": "Play this song",
                "style": "item_play",
                "actions": {
                    "go": {
                        "player": 0,
                        "cmd": ["playlist", "loadtracks", f"track_id:{track_id_str}"],
                        "nextWindow": "nowPlaying",
                    },
                },
            }
        )

    if album_id is not None:
        album_id_str = str(album_id)
        if play_index is None or play_index < 0:
            play_index = 0

        items.append(
            {
                "text": "Play all songs",
                "style": "itemNoAction",
                "actions": {
                    "go": {
                        "player": 0,
                        "cmd": [
                            "playlist",
                            "loadtracks",
                            f"album_id:{album_id_str}",
                            f"play_index:{play_index}",
                        ],
                        "nextWindow": "nowPlaying",
                    },
                },
            }
        )

    return {
        "count": len(items),
        "offset": 0,
        "item_loop": items,
        "window": {"windowStyle": "text_list"},
    }



def _trackinfo_playlist_menu(
    *,
    playlist_index: int,
    current_index: int,
) -> dict[str, Any]:
    """Build playlist-context actions (jump/move/delete) for trackinfo."""
    items: list[dict[str, Any]] = []

    items.append(
        {
            "text": "Play this song",
            "style": "item_play",
            "actions": {
                "go": {
                    "player": 0,
                    "cmd": ["playlist", "jump", str(playlist_index)],
                    "nextWindow": "parent",
                },
            },
        }
    )

    # Mimic LMS playlist context behavior: move selected item to "next" slot.
    if playlist_index not in (current_index, current_index + 1):
        move_to = current_index + 1
        if playlist_index <= current_index:
            move_to = current_index

        items.append(
            {
                "text": "Play next",
                "style": "itemNoAction",
                "actions": {
                    "go": {
                        "player": 0,
                        "cmd": ["playlist", "move", str(playlist_index), str(move_to)],
                        "nextWindow": "parent",
                    },
                },
            }
        )

    items.append(
        {
            "text": "Remove from playlist",
            "style": "itemNoAction",
            "actions": {
                "go": {
                    "player": 0,
                    "cmd": ["playlist", "delete", str(playlist_index)],
                    "nextWindow": "parent",
                },
            },
        }
    )

    return {
        "count": len(items),
        "offset": 0,
        "item_loop": items,
        "window": {"windowStyle": "text_list"},
    }


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


async def cmd_sleep_settings(
    ctx: CommandContext, command: list[Any]
) -> dict[str, Any]:
    """
    Handle the 'sleepsettings' query for Jive devices.

    Returns sleep timer options.
    """
    sleep_options = [
        {
            "text": "Off",
            "radio": 1,
            "actions": {"do": {"player": 0, "cmd": ["sleep", "0"]}},
            "nextWindow": "parent",
        },
        {
            "text": "15 minutes",
            "radio": 0,
            "actions": {"do": {"player": 0, "cmd": ["sleep", "900"]}},
            "nextWindow": "parent",
        },
        {
            "text": "30 minutes",
            "radio": 0,
            "actions": {"do": {"player": 0, "cmd": ["sleep", "1800"]}},
            "nextWindow": "parent",
        },
        {
            "text": "45 minutes",
            "radio": 0,
            "actions": {"do": {"player": 0, "cmd": ["sleep", "2700"]}},
            "nextWindow": "parent",
        },
        {
            "text": "1 hour",
            "radio": 0,
            "actions": {"do": {"player": 0, "cmd": ["sleep", "3600"]}},
            "nextWindow": "parent",
        },
        {
            "text": "2 hours",
            "radio": 0,
            "actions": {"do": {"player": 0, "cmd": ["sleep", "7200"]}},
            "nextWindow": "parent",
        },
    ]

    return {
        "count": len(sleep_options),
        "offset": 0,
        "item_loop": sleep_options,
    }


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
    """Resolve playlistcontrol filters to a list of Track objects."""
    from resonance.core.library import Track

    db = ctx.music_library._db

    def _to_track(row: Any) -> Track:
        return Track(
            id=getattr(row, "id", None),
            path=getattr(row, "path", ""),
            title=getattr(row, "title", "") or "",
            artist_id=getattr(row, "artist_id", None),
            album_id=getattr(row, "album_id", None),
            artist_name=getattr(row, "artist", None),
            album_title=getattr(row, "album", None),
            year=getattr(row, "year", None),
            duration_ms=getattr(row, "duration_ms", None),
            disc_no=getattr(row, "disc_no", None),
            track_no=getattr(row, "track_no", None),
            compilation=getattr(row, "compilation", 0) or 0,
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
        return {
            "count": 1,
            "offset": 0,
            "item_loop": [{"text": "Player not found", "style": "item_no_arrow"}],
        }

    items = [
        {"text": f"Name: {player.name}", "style": "item_no_arrow"},
        {"text": f"Model: {player.info.model}", "style": "item_no_arrow"},
        {"text": f"MAC: {player.info.mac_address}", "style": "item_no_arrow"},
        {"text": f"Firmware: {player.info.firmware_version}", "style": "item_no_arrow"},
        {"text": f"Server: Resonance", "style": "item_no_arrow"},
    ]

    return {
        "count": len(items),
        "offset": 0,
        "item_loop": items,
    }
