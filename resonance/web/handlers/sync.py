"""
Sync command handlers (LMS compatibility subset).

Implements:
- sync ?
- sync <player_id|player_index|->
- syncgroups ?
"""

from __future__ import annotations

import logging
from typing import Any

from resonance.web.handlers import CommandContext

logger = logging.getLogger(__name__)


async def _resolve_sync_target_player_id(ctx: CommandContext, raw_target: str) -> str | None:
    """Resolve sync target from player id or numeric player index."""
    if raw_target == "-":
        return "-"

    # LMS also accepts a player index for sync targets.
    try:
        index = int(raw_target)
    except (TypeError, ValueError):
        index = None

    if index is not None:
        players = await ctx.player_registry.get_all()
        if 0 <= index < len(players):
            return players[index].mac_address
        return None

    player = await ctx.player_registry.get_by_mac(raw_target)
    if player is None:
        return None
    return player.mac_address


async def cmd_sync(ctx: CommandContext, command: list[Any]) -> dict[str, Any]:
    """Handle LMS `sync` query/command."""
    if ctx.player_id == "-":
        return {"error": "No player specified"}

    target_raw = str(command[1]) if len(command) > 1 else None
    if target_raw == "?":
        buddies = await ctx.player_registry.get_sync_buddies(ctx.player_id)
        if not buddies:
            return {"_sync": "-"}
        return {"_sync": ",".join(player.mac_address for player in buddies)}

    if target_raw is None:
        return {"error": "Missing sync target"}

    if target_raw == "-":
        await ctx.player_registry.unsync(ctx.player_id)
        return {}

    target_player_id = await _resolve_sync_target_player_id(ctx, target_raw)
    if target_player_id is None:
        logger.debug("sync: unresolved target %s", target_raw)
        return {}

    if target_player_id == ctx.player_id:
        return {}

    await ctx.player_registry.sync(ctx.player_id, target_player_id)
    logger.warning(
        "Sync group created (logical only — clock/buffer synchronization not yet implemented)"
    )
    return {}


async def cmd_syncgroups(ctx: CommandContext, command: list[Any]) -> dict[str, Any]:
    """Handle LMS `syncgroups ?` query."""
    groups = await ctx.player_registry.get_sync_groups()

    loop: list[dict[str, Any]] = []
    for group in groups:
        member_ids = [player.mac_address for player in group]
        member_names = [player.name for player in group]
        loop.append(
            {
                "sync_members": ",".join(member_ids),
                "sync_member_names": ",".join(member_names),
            }
        )

    return {
        "count": len(loop),
        "offset": 0,
        "syncgroups_loop": loop,
        "_note": "logical_only",
    }
