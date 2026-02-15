"""Now Playing Info — tracks recently played songs and provides play statistics.

This plugin demonstrates the Resonance plugin API by:

- Subscribing to ``player.track_started`` events to count plays
- Providing JSON-RPC commands to query statistics (``nowplaying.stats``,
  ``nowplaying.recent``)
- Adding a "Play Stats" node to the Jive main menu
- Persisting play history across server restarts via JSON store

This plugin is also the companion code for ``docs/PLUGINS_TUTORIAL.md``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from resonance.core.events import Event
    from resonance.plugin import PluginContext
    from resonance.web.handlers import CommandContext

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level state (set during setup, cleared during teardown)
# ---------------------------------------------------------------------------

_store: Any | None = None  # PlayHistory instance


# ---------------------------------------------------------------------------
# Plugin lifecycle
# ---------------------------------------------------------------------------


async def setup(ctx: PluginContext) -> None:
    """Called by PluginManager during server startup."""
    global _store

    from .store import PlayHistory

    data_dir = ctx.ensure_data_dir()
    _store = PlayHistory(data_dir)
    _store.load()

    # ── Commands ────────────────────────────────────────────────
    ctx.register_command("nowplaying.stats", cmd_stats)
    ctx.register_command("nowplaying.recent", cmd_recent)

    # ── Events ──────────────────────────────────────────────────
    await ctx.subscribe("player.track_started", _on_track_started)

    # ── Jive menu ───────────────────────────────────────────────
    ctx.register_menu_node(
        node_id="nowPlaying",
        parent="home",
        text="Play Stats",
        weight=80,
        actions={
            "go": {
                "cmd": ["nowplaying.recent"],
                "params": {"menu": 1},
            },
        },
    )

    logger.info("Now Playing plugin started — %d plays on record", _store.total)


async def teardown(ctx: PluginContext) -> None:
    """Called by PluginManager during server shutdown."""
    global _store

    if _store is not None:
        logger.info(
            "Now Playing plugin stopping — %d total plays", _store.total
        )
    _store = None


# ---------------------------------------------------------------------------
# Event handler
# ---------------------------------------------------------------------------


async def _on_track_started(event: Event) -> None:
    """Record every track start into the persistent history."""
    if _store is None:
        return

    player_id = getattr(event, "player_id", "unknown")
    entry = _store.record(player_id)
    logger.debug("Track #%d on %s", entry["play_number"], player_id)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


async def cmd_stats(ctx: CommandContext, command: list[Any]) -> dict[str, Any]:
    """Handle ``nowplaying.stats`` — return overall play statistics.

    Returns::

        {
            "total_played": 42,
            "stored_entries": 20
        }
    """
    if _store is None:
        return {"error": "Now Playing plugin not initialized"}

    return {
        "total_played": _store.total,
        "stored_entries": _store.count,
    }


async def cmd_recent(ctx: CommandContext, command: list[Any]) -> dict[str, Any]:
    """Handle ``nowplaying.recent`` — return recently played tracks.

    Supports both CLI mode (``loop`` key) and Jive menu mode
    (``item_loop`` key when ``menu:1`` is present).

    Returns the 20 most recent entries, newest first.
    """
    if _store is None:
        return {"error": "Now Playing plugin not initialized"}

    tagged = _parse_tagged(command, start=1)
    is_menu = tagged.get("menu") == "1"

    recent = list(reversed(_store.entries[-20:]))  # Newest first

    if not recent:
        loop: list[dict[str, Any]] = [
            {"text": "No tracks played yet", "style": "itemNoAction"}
        ]
    else:
        loop = []
        for entry in recent:
            text = f"#{entry['play_number']} — {entry['player_id']}"
            ts = entry.get("timestamp", "")
            if ts and len(ts) >= 19:
                text += f" ({ts[11:19]})"
            loop.append({"text": text, "style": "itemNoAction"})

    loop_key = "item_loop" if is_menu else "loop"
    return {
        "count": len(loop),
        "offset": 0,
        loop_key: loop,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_tagged(command: list[Any], start: int = 1) -> dict[str, str]:
    """Parse ``key:value`` tagged params and dict elements from *command*.

    Handles both LMS-style colon-separated strings and dict objects
    that some clients (Cometd) send as inline params.
    """
    result: dict[str, str] = {}
    for arg in command[start:]:
        if isinstance(arg, dict):
            for k, v in arg.items():
                if v is not None:
                    result[str(k)] = str(v)
        elif isinstance(arg, str) and ":" in arg:
            key, value = arg.split(":", 1)
            result[key] = value
    return result
