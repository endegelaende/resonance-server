"""
Example Plugin for Resonance.

This plugin demonstrates the full plugin API surface:

- Registering a JSON-RPC command (``example.hello``)
- Adding a menu node visible on Jive devices (Touch/Radio/Boom/Controller)
- Subscribing to server events (tracked, auto-unsubscribed on teardown)
- Using the per-plugin data directory for persistence
- Clean teardown

To enable this plugin, ensure the ``plugins/example/`` directory exists
with this file and a ``plugin.toml`` manifest alongside it.

Usage via JSON-RPC::

    {"method": "slim.request", "params": ["-", ["example.hello"]]}

Response::

    {"result": {"message": "Hello from example plugin!", "version": "0.1.0"}}
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from resonance.core.events import Event
    from resonance.plugin import PluginContext
    from resonance.web.handlers import CommandContext

logger = logging.getLogger(__name__)

# Module-level state (reset on teardown)
_track_count: int = 0


# ---------------------------------------------------------------------------
# Plugin lifecycle
# ---------------------------------------------------------------------------


async def setup(ctx: PluginContext) -> None:
    """Called by PluginManager during server startup.

    Register commands, menu entries, and event subscriptions here.
    """
    global _track_count
    _track_count = 0

    # 1) Register a JSON-RPC command
    ctx.register_command("example.hello", cmd_hello)

    # 2) Register a menu node on Jive devices
    #    This creates a top-level entry under "home" visible on
    #    Touch/Radio/Boom/Controller.
    ctx.register_menu_node(
        node_id="examplePlugin",
        parent="home",
        text="Example Plugin",
        weight=1000,  # High weight = appears near the bottom
    )

    # 3) Subscribe to events (tracked — auto-unsubscribed on teardown)
    await ctx.subscribe("player.track_started", _on_track_started)
    await ctx.subscribe("server.started", _on_server_started)

    logger.info("Example plugin setup complete")


async def teardown(ctx: PluginContext) -> None:
    """Called by PluginManager during server shutdown.

    Persist state or release resources here.
    All command/menu/event registrations are cleaned up automatically
    after this function returns — no need to unregister manually.
    """
    global _track_count
    logger.info("Example plugin teardown — %d tracks were started during this session", _track_count)
    _track_count = 0


# ---------------------------------------------------------------------------
# JSON-RPC command handler
# ---------------------------------------------------------------------------


async def cmd_hello(ctx: CommandContext, command: list[Any]) -> dict[str, Any]:
    """Handle the ``example.hello`` JSON-RPC command.

    Args:
        ctx: The command context (player_id, library, registry, …).
        command: The raw command array, e.g. ``["example.hello"]``.

    Returns:
        A result dict that is sent back to the client.
    """
    # Demonstrate reading an optional parameter
    name = "World"
    if len(command) > 1:
        name = str(command[1])

    return {
        "message": f"Hello from example plugin, {name}!",
        "version": "0.1.0",
        "tracks_started": _track_count,
        "player_id": ctx.player_id,
    }


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------


async def _on_track_started(event: Event) -> None:
    """Count how many tracks have been started while the plugin is loaded."""
    global _track_count
    _track_count += 1
    logger.debug("Example plugin: track started (total: %d)", _track_count)


async def _on_server_started(event: Event) -> None:
    """Log when the server is fully operational."""
    logger.info("Example plugin: server is fully started — ready to go!")
