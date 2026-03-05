"""
Favorites Plugin for Resonance.

LMS-compatible favorites management with hierarchical folder support,
JSON-backed persistence, and full Jive menu integration.

Registered commands:

- ``favorites items <start> <count>``  — Browse favorites (paginated)
- ``favorites add``                    — Add a favorite (url, title, type, icon)
- ``favorites addlevel``               — Add a folder
- ``favorites delete``                 — Delete by item_id or url
- ``favorites rename``                 — Rename by item_id
- ``favorites move``                   — Reorder (from_id → to_id)
- ``favorites exists <url_or_id>``     — Check if a URL is favorited
- ``favorites playlist <method>``      — Play/add favorites to playlist
- ``jivefavorites <cmd>``              — Jive context-menu add/delete confirmation

Menu entry:

- Top-level "Favorites" node in Jive home menu (weight 55, matching LMS)

Events:

- Publishes ``favorites.changed`` on every mutation so that Cometd
  subscribers (Material Skin, iPeng, …) can refresh.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from resonance.web.jsonrpc_helpers import parse_start_count, parse_tagged_params

if TYPE_CHECKING:
    from resonance.core.events import Event
    from resonance.plugin import PluginContext
    from resonance.web.handlers import CommandContext

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level state (set during setup, cleared during teardown)
# ---------------------------------------------------------------------------

_store: Any | None = None  # FavoritesStore instance
_event_bus: Any | None = None  # EventBus reference for publishing changes


# ---------------------------------------------------------------------------
# Helpers — parameter parsing
# ---------------------------------------------------------------------------


def _parse_tagged(command: list[Any], start: int = 1) -> dict[str, str]:
    """Parse ``key:value`` tagged params from *command* starting at *start*.

    Delegates to :func:`resonance.web.jsonrpc_helpers.parse_tagged_params`.
    """
    return parse_tagged_params(command[start:])


def _parse_start_count(command: list[Any], sub_offset: int = 2) -> tuple[int, int]:
    """Extract ``(start, count)`` from positional args after the sub-command.

    Delegates to :func:`resonance.web.jsonrpc_helpers.parse_start_count`.
    """
    return parse_start_count(command, sub_offset)


# ---------------------------------------------------------------------------
# Event helper
# ---------------------------------------------------------------------------


async def _notify_changed() -> None:
    """Publish a ``favorites.changed`` event on the global event bus."""
    if _event_bus is None:
        return

    from resonance.core.events import Event as _Evt

    @dataclass
    class _FavoritesChangedEvent(_Evt):
        event_type: str = field(default="favorites.changed", init=False)

    await _event_bus.publish(_FavoritesChangedEvent())


# ---------------------------------------------------------------------------
# Plugin lifecycle
# ---------------------------------------------------------------------------


async def setup(ctx: PluginContext) -> None:
    """Called by PluginManager during server startup."""
    global _store, _event_bus

    from .store import FavoritesStore

    data_dir = ctx.ensure_data_dir()
    _store = FavoritesStore(data_dir)
    _store.load()
    _event_bus = ctx.event_bus

    # ── Commands ────────────────────────────────────────────────
    ctx.register_command("favorites", cmd_favorites)
    ctx.register_command("jivefavorites", cmd_jivefavorites)

    # ── Jive main-menu node ─────────────────────────────────────
    # Weight 55 matches LMS (Slim::Plugin::Favorites MENU_WEIGHT).
    ctx.register_menu_node(
        node_id="favorites",
        parent="home",
        text="Favorites",
        weight=55,
        actions={
            "go": {
                "cmd": ["favorites", "items"],
                "params": {"menu": 1},
            },
        },
    )

    logger.info(
        "Favorites plugin started — %d favorite(s) loaded", _store.count
    )


async def teardown(ctx: PluginContext) -> None:
    """Called by PluginManager during server shutdown."""
    global _store, _event_bus

    if _store is not None:
        logger.info(
            "Favorites plugin stopping — %d favorite(s) in store",
            _store.count,
        )
    _store = None
    _event_bus = None


# ╔═══════════════════════════════════════════════════════════════════╗
# ║  Command: favorites                                              ║
# ╚═══════════════════════════════════════════════════════════════════╝


async def cmd_favorites(
    ctx: CommandContext, command: list[Any]
) -> dict[str, Any]:
    """Dispatch ``favorites <sub-command> …`` to the appropriate handler."""
    if _store is None:
        return {"error": "Favorites plugin not initialized"}

    sub = str(command[1]).lower() if len(command) > 1 else "items"

    match sub:
        case "items":
            return await _favorites_items(ctx, command)
        case "add":
            return await _favorites_add(ctx, command)
        case "addlevel":
            return await _favorites_addlevel(ctx, command)
        case "delete":
            return await _favorites_delete(ctx, command)
        case "rename":
            return await _favorites_rename(ctx, command)
        case "move":
            return await _favorites_move(ctx, command)
        case "exists":
            return await _favorites_exists(ctx, command)
        case "playlist":
            return await _favorites_playlist(ctx, command)
        case _:
            return {"error": f"Unknown favorites sub-command: {sub}"}


# ── items ──────────────────────────────────────────────────────────


async def _favorites_items(
    ctx: CommandContext, command: list[Any]
) -> dict[str, Any]:
    """Handle ``favorites items <start> <count> [item_id:…] [menu:1] [search:…]``.

    Returns a paginated list of favorites.  When ``menu:1`` is present the
    response uses Jive ``item_loop`` format with full action sets.
    """
    assert _store is not None

    tagged = _parse_tagged(command, start=2)
    start, count = _parse_start_count(command)
    parent_id = tagged.get("item_id")
    is_menu = tagged.get("menu") == "1"
    search = tagged.get("search")

    items_with_idx, total = _store.get_items_paginated(
        start=start, count=count, index=parent_id
    )

    # Optional search filter
    if search:
        needle = search.lower()
        items_with_idx = [
            (idx, fav) for idx, fav in items_with_idx
            if needle in fav.title.lower()
        ]
        total = len(items_with_idx)

    loop: list[dict[str, Any]] = []
    for idx, fav in items_with_idx:
        if is_menu:
            loop.append(_build_jive_item(ctx, idx, fav))
        else:
            loop.append(_build_cli_item(idx, fav))

    result: dict[str, Any] = {
        "count": total,
        "offset": start,
    }

    # LMS uses ``item_loop`` for menu queries, ``loop`` for plain CLI
    loop_key = "item_loop" if is_menu else "loop"
    result[loop_key] = loop

    if is_menu:
        result["base"] = {"actions": _base_actions()}

    return result


def _build_jive_item(
    ctx: CommandContext, index: str, fav: Any
) -> dict[str, Any]:
    """Build a Jive-compatible menu item for a single favorite."""
    item: dict[str, Any] = {
        "text": fav.title,
        "id": index,
    }

    if fav.icon:
        item["icon"] = fav.icon

    if fav.is_folder:
        item["type"] = "folder"
        item["hasitems"] = 1
        item["actions"] = {
            "go": {
                "cmd": ["favorites", "items"],
                "params": {"item_id": index, "menu": 1},
            },
        }
    else:
        item["type"] = "audio"
        item["hasitems"] = 0
        # Preset params (used by Jive preset buttons)
        item["presetParams"] = {
            "favorites_url": fav.url,
            "favorites_title": fav.title,
            "favorites_type": fav.type or "audio",
        }
        item["commonParams"] = {"track_id": fav.url, "favorites_url": fav.url}
        item["actions"] = {
            "play": {
                "player": 0,
                "cmd": ["favorites", "playlist", "play"],
                "params": {"item_id": index},
            },
            "add": {
                "player": 0,
                "cmd": ["favorites", "playlist", "add"],
                "params": {"item_id": index},
            },
        }

        # Context menu — "Remove from Favorites"
        item["actions"]["more"] = {
            "player": 0,
            "cmd": ["jivefavorites", "delete"],
            "params": {
                "title": fav.title,
                "url": fav.url,
                "item_id": index,
            },
        }

    return item


def _build_cli_item(index: str, fav: Any) -> dict[str, Any]:
    """Build a plain CLI item dict (used by Material Skin, web UIs, etc.)."""
    item: dict[str, Any] = {
        "id": index,
        "name": fav.title,
    }

    if fav.url:
        item["url"] = fav.url

    if fav.is_folder:
        item["hasitems"] = 1
        item["type"] = "folder"
        item["isaudio"] = 0
    else:
        item["hasitems"] = 0
        item["type"] = fav.type or "audio"
        item["isaudio"] = 1

    if fav.icon:
        item["icon"] = fav.icon

    return item


def _base_actions() -> dict[str, Any]:
    """Return the Jive ``base.actions`` block for the favorites list."""
    return {
        "go": {
            "cmd": ["favorites", "items"],
            "params": {"menu": 1},
            "itemsParams": "commonParams",
        },
    }


# ── add ────────────────────────────────────────────────────────────


async def _favorites_add(
    ctx: CommandContext, command: list[Any]
) -> dict[str, Any]:
    """Handle ``favorites add url:<url> title:<title> [type:…] [icon:…] [item_id:…]``."""
    assert _store is not None

    tagged = _parse_tagged(command, start=2)
    url = tagged.get("url")
    title = tagged.get("title")
    icon = tagged.get("icon")
    item_id = tagged.get("item_id")
    fav_type = tagged.get("type", "audio")

    if not url or not title:
        return {"error": "Missing required parameters: url, title"}

    new_index = _store.add(url, title, type=fav_type, icon=icon, index=item_id)

    # Show brief feedback on Jive devices
    player = await _get_player(ctx)
    if player is not None:
        try:
            player.show_briefly(
                {
                    "jive": {
                        "type": "mixed",
                        "style": "favorite",
                        "text": ["Adding to Favorites", title],
                        "icon": icon or "",
                    }
                }
            )
        except Exception:
            pass  # show_briefly is best-effort

    await _notify_changed()
    return {"count": 1, "item_id": new_index}


# ── addlevel ───────────────────────────────────────────────────────


async def _favorites_addlevel(
    ctx: CommandContext, command: list[Any]
) -> dict[str, Any]:
    """Handle ``favorites addlevel title:<title> [item_id:…]``."""
    assert _store is not None

    tagged = _parse_tagged(command, start=2)
    title = tagged.get("title")
    item_id = tagged.get("item_id")

    if not title:
        return {"error": "Missing required parameter: title"}

    new_index = _store.add_level(title, index=item_id)

    await _notify_changed()
    return {"count": 1, "item_id": new_index}


# ── delete ─────────────────────────────────────────────────────────


async def _favorites_delete(
    ctx: CommandContext, command: list[Any]
) -> dict[str, Any]:
    """Handle ``favorites delete item_id:<index>`` or ``favorites delete url:<url>``."""
    assert _store is not None

    tagged = _parse_tagged(command, start=2)
    item_id = tagged.get("item_id")
    url = tagged.get("url")

    removed = None
    if item_id is not None:
        entry = _store.get_entry(item_id)
        if entry is not None:
            removed = _store.delete_by_index(item_id)
        elif url:
            removed = _store.delete_by_url(url)
        else:
            return {"error": f"Index {item_id} not found"}
    elif url:
        removed = _store.delete_by_url(url)
    else:
        return {"error": "Missing parameter: item_id or url"}

    if removed is None:
        return {"error": "Favorite not found"}

    # Show brief feedback on Jive devices
    player = await _get_player(ctx)
    if player is not None:
        try:
            player.show_briefly(
                {
                    "jive": {
                        "type": "mixed",
                        "text": ["Removing from Favorites", removed.title],
                    }
                }
            )
        except Exception:
            pass

    await _notify_changed()
    return {}


# ── rename ─────────────────────────────────────────────────────────


async def _favorites_rename(
    ctx: CommandContext, command: list[Any]
) -> dict[str, Any]:
    """Handle ``favorites rename item_id:<index> title:<title>``."""
    assert _store is not None

    tagged = _parse_tagged(command, start=2)
    item_id = tagged.get("item_id")
    title = tagged.get("title")

    if item_id is None or title is None:
        return {"error": "Missing required parameters: item_id, title"}

    if _store.rename(item_id, title):
        await _notify_changed()
        return {}

    return {"error": f"Index {item_id} not found"}


# ── move ───────────────────────────────────────────────────────────


async def _favorites_move(
    ctx: CommandContext, command: list[Any]
) -> dict[str, Any]:
    """Handle ``favorites move from_id:<from> to_id:<to>``."""
    assert _store is not None

    tagged = _parse_tagged(command, start=2)
    from_id = tagged.get("from_id")
    to_id = tagged.get("to_id")

    if from_id is None or to_id is None:
        return {"error": "Missing required parameters: from_id, to_id"}

    if _store.move(from_id, to_id):
        await _notify_changed()
        return {}

    return {"error": f"Move failed: {from_id} → {to_id}"}


# ── exists ─────────────────────────────────────────────────────────


async def _favorites_exists(
    ctx: CommandContext, command: list[Any]
) -> dict[str, Any]:
    """Handle ``favorites exists <url_or_track_id>``.

    Checks whether a URL (or track-id resolved to a URL) exists in the
    favorites list.  Returns ``{exists: 1, index: "…"}`` or ``{exists: 0}``.
    """
    assert _store is not None

    # The ID can come as a positional param or as a tagged param
    tagged = _parse_tagged(command, start=2)
    raw_id = tagged.get("_id") or tagged.get("url")

    # Positional: ["favorites", "exists", "<id>"]
    if raw_id is None and len(command) > 2:
        raw_id = str(command[2])

    if raw_id is None:
        return {"exists": 0}

    url = raw_id

    # If the ID looks like a numeric track ID, try to resolve it to a URL
    # via the music library.
    if raw_id.isdigit() and ctx.music_library is not None:
        try:
            db = ctx.music_library._db
            row = await db.get_track(int(raw_id))
            if row:
                url = row.get("url", row.get("path", raw_id))
        except Exception:
            pass  # fall back to using raw_id as URL

    index = _store.find_url(url)
    if index is not None:
        return {"exists": 1, "index": index}

    return {"exists": 0}


# ── playlist ───────────────────────────────────────────────────────


async def _favorites_playlist(
    ctx: CommandContext, command: list[Any]
) -> dict[str, Any]:
    """Handle ``favorites playlist play|add|insert [item_id:…]``.

    Plays or enqueues favorites via the playlist manager.
    """
    assert _store is not None

    method = str(command[2]).lower() if len(command) > 2 else "play"
    tagged = _parse_tagged(command, start=3)
    item_id = tagged.get("item_id")

    if method not in ("play", "add", "insert"):
        # If method looks like a tagged param, re-parse
        tagged2 = _parse_tagged(command, start=2)
        item_id = item_id or tagged2.get("item_id")
        # Default to items query
        return await _favorites_items(ctx, command)

    if ctx.playlist_manager is None:
        return {"error": "Playlist manager not available"}

    if ctx.player_id == "-":
        return {"error": "No player specified"}

    # Resolve which items to play
    if item_id is not None:
        entry = _store.get_entry(item_id)
        if entry is None:
            return {"error": f"Favorite {item_id} not found"}

        if entry.is_folder:
            playable = _store.all_playable(entry.items)
        elif entry.is_playable:
            playable = [entry]
        else:
            return {"error": "Favorite is not playable"}
    else:
        playable = _store.all_playable()

    if not playable:
        return {"error": "No playable favorites found"}

    # Resolve URLs to track IDs where possible, fall back to URL play
    from resonance.web.handlers.playlist import cmd_playlist

    if method == "play":
        # Clear and load
        await cmd_playlist(ctx, ["playlist", "clear"])

        for fav in playable:
            await cmd_playlist(ctx, ["playlist", "add", fav.url])

        await cmd_playlist(ctx, ["playlist", "index", "0"])
        return {"count": len(playable)}

    elif method == "add":
        for fav in playable:
            await cmd_playlist(ctx, ["playlist", "add", fav.url])
        return {"count": len(playable)}

    elif method == "insert":
        # Insert after current track
        for i, fav in enumerate(playable):
            await cmd_playlist(ctx, ["playlist", "insert", fav.url])
        return {"count": len(playable)}

    return {}


# ╔═══════════════════════════════════════════════════════════════════╗
# ║  Command: jivefavorites                                          ║
# ╚═══════════════════════════════════════════════════════════════════╝


async def cmd_jivefavorites(
    ctx: CommandContext, command: list[Any]
) -> dict[str, Any]:
    """Handle ``jivefavorites <add|delete|set_preset>`` for Jive context menus.

    This command returns a confirmation menu that the Jive device displays
    before actually performing the add/delete.  This matches LMS behavior
    in ``Slim::Control::Jive::jiveFavoritesCommand``.
    """
    if _store is None:
        return {"error": "Favorites plugin not initialized"}

    sub = str(command[1]).lower() if len(command) > 1 else ""
    tagged = _parse_tagged(command, start=2)

    title = tagged.get("title", "")
    url = tagged.get("url", "")
    icon = tagged.get("icon")
    fav_type = tagged.get("type", "audio")
    parser = tagged.get("parser")
    item_id = tagged.get("item_id")
    is_context = tagged.get("isContextMenu") == "1"

    if sub == "set_preset":
        return await _jive_set_preset(ctx, tagged)

    if sub == "add":
        token = "Add"
        action_text = f"Add {title}"
    elif sub == "delete":
        token = "Delete"
        action_text = f"Delete {title}"
    else:
        return {"error": f"Unknown jivefavorites command: {sub}"}

    # Build confirmation menu (Cancel + Action)
    menu: list[dict[str, Any]] = [
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
        _build_confirm_action(
            text=action_text,
            sub=sub,
            title=title,
            url=url,
            fav_type=fav_type,
            icon=icon,
            item_id=item_id,
        ),
    ]

    return {
        "offset": 0,
        "count": len(menu),
        "item_loop": menu,
    }


def _build_confirm_action(
    *,
    text: str,
    sub: str,
    title: str,
    url: str,
    fav_type: str,
    icon: str | None,
    item_id: str | None,
) -> dict[str, Any]:
    """Build the confirmation action item for the jivefavorites menu."""
    params: dict[str, Any] = {
        "title": title,
        "url": url,
        "type": fav_type,
    }
    if icon:
        params["icon"] = icon
    if item_id is not None:
        params["item_id"] = item_id

    item: dict[str, Any] = {
        "text": text,
        "actions": {
            "go": {
                "player": 0,
                "cmd": ["favorites", sub],
                "params": params,
            },
        },
        "nextWindow": "grandparent",
    }
    return item


async def _jive_set_preset(
    ctx: CommandContext, tagged: dict[str, str]
) -> dict[str, Any]:
    """Handle ``jivefavorites set_preset key:<slot> …``.

    Sets a preset button on the player (1–10).
    """
    preset_key = tagged.get("key")
    title = tagged.get("favorites_title") or tagged.get("title", "")
    url = tagged.get("favorites_url") or tagged.get("url", "")
    fav_type = tagged.get("favorites_type") or tagged.get("type", "audio")

    # Resolve from playlist_index if provided
    playlist_index = tagged.get("playlist_index")
    if playlist_index is not None and ctx.playlist_manager is not None:
        try:
            playlist = ctx.playlist_manager.get(ctx.player_id)
            idx = int(playlist_index)
            track = playlist.get_track(idx)
            if track:
                url = track.get("url", url)
                title = track.get("title", title)
                fav_type = "audio"
        except (ValueError, TypeError, IndexError):
            pass

    if not preset_key or not title or not url:
        return {"error": "Missing preset parameters"}

    # Convert preset key — 0 maps to 10 (matching LMS)
    try:
        slot = int(preset_key)
        if slot == 0:
            slot = 10
    except ValueError:
        return {"error": f"Invalid preset key: {preset_key}"}

    # Set preset on the player
    player = await _get_player(ctx)
    if player is not None:
        try:
            player.set_preset(
                slot=slot,
                url=url,
                text=title,
                type=fav_type,
            )
            player.show_briefly(
                {
                    "jive": {
                        "type": "popupplay",
                        "text": [f"Setting Preset {slot}", title],
                    }
                }
            )
        except Exception as exc:
            logger.warning("Failed to set preset %d: %s", slot, exc)

    return {}


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


async def _get_player(ctx: CommandContext) -> Any | None:
    """Resolve the current player from context, or ``None``."""
    if ctx.player_id and ctx.player_id != "-" and ctx.player_registry:
        try:
            return await ctx.player_registry.get_by_mac(ctx.player_id)
        except Exception:
            pass
    return None
