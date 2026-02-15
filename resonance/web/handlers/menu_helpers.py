"""
Jive Menu Builder Helpers — Template Pattern.

Reusable helper functions for constructing Jive menu item dicts.
These eliminate repetitive dict-literal construction across menu.py
and provide a consistent vocabulary for menu assembly.

All helpers return plain dicts — no classes, no magic.
"""

from __future__ import annotations

from typing import Any

# ─── Constants ────────────────────────────────────────────────────────
BROWSELIBRARY = "browselibrary"


# =====================================================================
# Generic menu-item builders
# =====================================================================


def menu_node(
    text: str,
    *,
    id: str,
    node: str,
    weight: int,
) -> dict[str, Any]:
    """Build a node item (``isANode: 1``) for the Jive menu tree."""
    return {
        "text": text,
        "id": id,
        "node": node,
        "weight": weight,
        "isANode": 1,
    }


def menu_item(
    text: str,
    *,
    id: str | None = None,
    node: str | None = None,
    weight: int | None = None,
    actions: dict[str, Any] | None = None,
    style: str | None = None,
    item_type: str | None = None,
    icon: str | None = None,
    icon_id: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build a generic Jive menu item dict.

    Only keys with non-``None`` values are included, keeping the output
    identical to hand-written dict literals.
    """
    item: dict[str, Any] = {"text": text}
    if id is not None:
        item["id"] = id
    if node is not None:
        item["node"] = node
    if weight is not None:
        item["weight"] = weight
    if style is not None:
        item["style"] = style
    if item_type is not None:
        item["type"] = item_type
    if icon is not None:
        item["icon"] = icon
    if icon_id is not None:
        item["icon-id"] = icon_id
    if actions is not None:
        item["actions"] = actions
    if extra:
        item.update(extra)
    return item


# =====================================================================
# Action builders
# =====================================================================


def go_action(
    cmd: list[Any],
    params: dict[str, Any] | None = None,
    *,
    player: int | None = None,
    next_window: str | None = None,
    items_params: str | None = None,
) -> dict[str, Any]:
    """Build a ``{"go": {…}}`` action dict."""
    action: dict[str, Any] = {}
    if player is not None:
        action["player"] = player
    action["cmd"] = cmd
    if params:
        action["params"] = params
    if next_window:
        action["nextWindow"] = next_window
    if items_params:
        action["itemsParams"] = items_params
    return {"go": action}


def do_action(
    cmd: list[Any],
    params: dict[str, Any] | None = None,
    *,
    player: int = 0,
) -> dict[str, Any]:
    """Build a ``{"do": {…}}`` action dict."""
    action: dict[str, Any] = {"player": player, "cmd": cmd}
    if params:
        action["params"] = params
    return {"do": action}


# =====================================================================
# Browselibrary shorthand
# =====================================================================


def browse_go(
    mode: str,
    extra_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a ``go`` action pointing to ``browselibrary items``."""
    params: dict[str, Any] = {"menu": 1, "mode": mode}
    if extra_params:
        params.update(extra_params)
    return {"go": {"cmd": [BROWSELIBRARY, "items"], "params": params}}


def browse_menu_item(
    text: str,
    *,
    id: str,
    node: str,
    weight: int,
    mode: str,
    extra_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a My-Music child item that navigates to a browselibrary mode."""
    return {
        "text": text,
        "id": id,
        "node": node,
        "weight": weight,
        "actions": browse_go(mode, extra_params),
    }


# =====================================================================
# Playlistcontrol action helpers
# =====================================================================


def playlist_play(
    filter_key: str,
    filter_value: Any,
    *,
    player: int = 0,
) -> dict[str, Any]:
    """Build a playlistcontrol ``load`` (play) action."""
    return {
        "play": {
            "player": player,
            "cmd": ["playlistcontrol"],
            "params": {"cmd": "load", filter_key: filter_value},
        },
    }


def playlist_add(
    filter_key: str,
    filter_value: Any,
    *,
    player: int = 0,
) -> dict[str, Any]:
    """Build a playlistcontrol ``add`` action."""
    return {
        "add": {
            "player": player,
            "cmd": ["playlistcontrol"],
            "params": {"cmd": "add", filter_key: filter_value},
        },
    }


def browse_actions(
    filter_key: str,
    filter_value: Any,
    *,
    go_mode: str,
    go_params: dict[str, Any] | None = None,
    include_add: bool = True,
) -> dict[str, Any]:
    """Build combined go / play / add actions for a browse list item.

    This is the most common action-set used by artist, album, genre and
    year browse items.
    """
    full_go_params: dict[str, Any] = {"menu": 1, "mode": go_mode}
    if go_params:
        full_go_params.update(go_params)

    actions: dict[str, Any] = {
        "go": {
            "cmd": [BROWSELIBRARY, "items"],
            "params": full_go_params,
        },
        "play": {
            "player": 0,
            "cmd": ["playlistcontrol"],
            "params": {"cmd": "load", filter_key: filter_value},
        },
    }

    if include_add:
        actions["add"] = {
            "player": 0,
            "cmd": ["playlistcontrol"],
            "params": {"cmd": "add", filter_key: filter_value},
        }

    return actions


# =====================================================================
# Settings helpers (sliders / choices)
# =====================================================================


def slider_item(
    text: str,
    *,
    id: str,
    node: str,
    weight: int,
    cmd: list[Any],
    min_val: int,
    max_val: int,
    player: int = 0,
) -> dict[str, Any]:
    """Build a slider settings item (Bass, Treble, Balance, …)."""
    return {
        "text": text,
        "id": id,
        "node": node,
        "weight": weight,
        "slider": 1,
        "min": min_val,
        "max": max_val,
        "adjust": 1,
        "actions": {
            "do": {
                "player": player,
                "cmd": cmd,
                "params": {"valtag": "value"},
            },
        },
    }


def choice_item(
    text: str,
    *,
    id: str,
    node: str,
    weight: int,
    cmd: list[Any],
    choices: list[str],
    player: int = 0,
) -> dict[str, Any]:
    """Build a choice settings item (Repeat, Shuffle, Fixed Volume, …)."""
    return {
        "text": text,
        "id": id,
        "node": node,
        "weight": weight,
        "choiceStrings": choices,
        "actions": {
            "do": {
                "player": player,
                "cmd": cmd,
                "params": {"valtag": "value"},
            },
        },
    }


# =====================================================================
# Context-menu helpers
# =====================================================================


def context_menu_item(
    text: str,
    *,
    style: str,
    cmd: list[Any],
    params: dict[str, Any] | None = None,
    next_window: str | None = None,
    player: int = 0,
) -> dict[str, Any]:
    """Build a single context-menu item (Add to end / Play next / …).

    Used by ``_trackinfo_library_menu``, ``_trackinfo_playlist_menu``,
    and ``_play_control_context_menu``.
    """
    go: dict[str, Any] = {"player": player, "cmd": cmd}
    if params:
        go["params"] = params
    if next_window:
        go["nextWindow"] = next_window
    return {
        "text": text,
        "style": style,
        "actions": {"go": go},
    }


# =====================================================================
# Response envelope
# =====================================================================


def paginated(
    items: list[dict[str, Any]],
    *,
    count: int | None = None,
    offset: int = 0,
    **extra: Any,
) -> dict[str, Any]:
    """Wrap items in the standard Jive pagination envelope.

    If *count* is not given it defaults to ``len(items)``.
    Extra kwargs (e.g. ``base``, ``window``) are merged into the response.
    """
    result: dict[str, Any] = {
        "count": count if count is not None else len(items),
        "offset": offset,
        "item_loop": items,
    }
    if extra:
        result.update(extra)
    return result
