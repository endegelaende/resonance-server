"""Now Playing Info — tracks recently played songs and provides play statistics.

This plugin demonstrates the Resonance plugin API by:

- Subscribing to ``player.track_started`` events to count plays
- Providing JSON-RPC commands to query statistics (``nowplaying.stats``,
  ``nowplaying.recent``)
- Adding a "Play Stats" node to the Jive main menu
- Providing an SDUI dashboard with statistics, recent history, and settings
- Persisting play history across server restarts via JSON store

This plugin is also the companion code for ``docs/PLUGINS_TUTORIAL.md``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from resonance.web.jsonrpc_helpers import parse_tagged_params

if TYPE_CHECKING:
    from resonance.core.events import Event
    from resonance.plugin import PluginContext
    from resonance.web.handlers import CommandContext

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level state (set during setup, cleared during teardown)
# ---------------------------------------------------------------------------

_store: Any | None = None  # PlayHistory instance
_ctx: Any | None = None    # PluginContext — set in setup()


# ---------------------------------------------------------------------------
# Settings helper
# ---------------------------------------------------------------------------


def _setting(key: str, default: Any = None) -> Any:
    """Read a plugin setting, falling back to *default*."""
    if _ctx is None:
        return default
    try:
        val = _ctx.get_setting(key)
        if val is None:
            return default
        return val
    except (KeyError, Exception):
        return default


# ---------------------------------------------------------------------------
# Plugin lifecycle
# ---------------------------------------------------------------------------


async def setup(ctx: PluginContext) -> None:
    """Called by PluginManager during server startup."""
    global _store, _ctx

    from .store import PlayHistory

    _ctx = ctx
    data_dir = ctx.ensure_data_dir()

    max_entries = int(_setting("max_entries", 500) or 500)
    _store = PlayHistory(data_dir, max_entries=max_entries)
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

    # ── SDUI ───────────────────────────────────────────────────
    ctx.register_ui_handler(get_ui)
    ctx.register_action_handler(handle_action)

    logger.info("Now Playing plugin v1.0 started — %d plays on record", _store.total)


async def teardown(ctx: PluginContext) -> None:
    """Called by PluginManager during server shutdown."""
    global _store, _ctx

    if _store is not None:
        logger.info(
            "Now Playing plugin stopping — %d total plays", _store.total
        )
    _store = None
    _ctx = None


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
# SDUI — Server-Driven UI
# ---------------------------------------------------------------------------


async def get_ui(ctx: PluginContext) -> Any:
    """Build the SDUI page for the Now Playing plugin."""
    from resonance.ui import Page, Tabs

    stats_tab = _build_stats_tab()
    settings_tab = _build_settings_tab()
    about_tab = _build_about_tab()

    return Page(
        title="Play Stats",
        icon="activity",
        refresh_interval=10,
        components=[
            Tabs(tabs=[
                stats_tab,
                settings_tab,
                about_tab,
            ]),
        ],
    )


def _build_stats_tab() -> Any:
    """Build the 'Stats' tab with play statistics and recent history."""
    from resonance.ui import (
        Alert,
        Button,
        Card,
        KeyValue,
        KVItem,
        Row,
        StatusBadge,
        Tab,
        Table,
        TableColumn,
    )

    if _store is None:
        return Tab(label="Stats", children=[
            Alert(message="Now Playing plugin not initialized.", severity="warning"),
        ])

    total = _store.total
    count = _store.count
    recent = list(reversed(_store.entries[-20:]))  # Newest first

    # Stats summary card
    stats_children: list[Any] = [
        Row(gap="md", children=[
            StatusBadge(
                label="Total Played",
                status=str(total),
                color="green" if total > 0 else "gray",
            ),
            StatusBadge(
                label="Stored Entries",
                status=str(count),
                color="blue" if count > 0 else "gray",
            ),
        ]),
        KeyValue(items=[
            KVItem(key="Total Plays (all time)", value=str(total)),
            KVItem(key="Stored History Entries", value=str(count)),
            KVItem(key="Max Entries", value=str(_setting("max_entries", 500) or 500)),
        ]),
    ]

    # Add most-active players summary if there's data
    if recent:
        player_counts: dict[str, int] = {}
        for entry in _store.entries:
            pid = entry.get("player_id", "unknown")
            player_counts[pid] = player_counts.get(pid, 0) + 1

        if player_counts:
            top_players = sorted(player_counts.items(), key=lambda x: x[1], reverse=True)[:5]
            player_items = [
                KVItem(key=pid, value=f"{c} play{'s' if c != 1 else ''}")
                for pid, c in top_players
            ]
            stats_children.append(
                Card(title="Most Active Players", collapsible=True, children=[
                    KeyValue(items=player_items),
                ])
            )

    children: list[Any] = [
        Card(title="Statistics", children=stats_children),
    ]

    # Recent tracks table
    if recent:
        columns = [
            TableColumn(key="play_number", label="#"),
            TableColumn(key="player_id", label="Player"),
            TableColumn(key="time", label="Time"),
        ]

        rows: list[dict[str, Any]] = []
        for entry in recent:
            ts = entry.get("timestamp", "")
            time_str = _format_timestamp(ts) if ts else "—"

            rows.append({
                "play_number": str(entry.get("play_number", "?")),
                "player_id": entry.get("player_id", "unknown"),
                "time": time_str,
            })

        children.append(
            Card(title=f"Recent Plays ({len(recent)})", children=[
                Table(
                    columns=columns,
                    rows=rows,
                    row_key="play_number",
                ),
            ])
        )
    else:
        children.append(
            Card(title="Recent Plays", children=[
                Alert(
                    message="No tracks played yet. Start playing music on any connected player and plays will appear here.",
                    severity="info",
                ),
            ])
        )

    # Action buttons
    children.append(
        Row(gap="md", children=[
            Button(
                label="Clear History",
                action="clear",
                style="danger",
                confirm=True,
                icon="trash-2",
            ),
        ]),
    )

    return Tab(label="Stats", children=children)


def _build_settings_tab() -> Any:
    """Build the 'Settings' tab with a form for plugin configuration."""
    from resonance.ui import (
        Alert,
        Form,
        NumberInput,
        Tab,
    )

    max_entries = int(_setting("max_entries", 500) or 500)

    return Tab(label="Settings", children=[
        Form(
            action="save_settings",
            submit_label="Save Settings",
            children=[
                NumberInput(
                    name="max_entries",
                    label="Maximum History Entries",
                    value=max_entries,
                    min=20,
                    max=5000,
                    help_text="How many track plays to keep in the history file. The total play count is always preserved even when older entries are trimmed.",
                ),
            ],
        ),
        Alert(
            message="Changes to the maximum entries setting take effect immediately. If the new limit is lower, excess entries are trimmed on the next track play.",
            severity="info",
        ),
    ])


def _build_about_tab() -> Any:
    """Build the 'About' tab with plugin information."""
    from resonance.ui import Markdown, Tab

    md = """## Now Playing Stats v1.0

**Play Statistics Tracker** for Resonance.

### How It Works

This plugin listens for `player.track_started` events from the server's
event bus. Every time a track starts playing on any connected player
(Squeezebox, Squeezelite, etc.), a record is created with the player ID
and timestamp.

### Features

| Feature | Description |
|---------|-------------|
| **Play Counting** | Counts every track start across all players |
| **History** | Persistent JSON-backed play history with configurable limit |
| **Per-Player Stats** | Shows which players are most active |
| **Jive Menu** | "Play Stats" menu entry on Squeezebox Touch/Radio/Boom |
| **JSON-RPC** | `nowplaying.stats` and `nowplaying.recent` commands |
| **SDUI Dashboard** | This page — view stats, manage history, configure |

### JSON-RPC Commands

```
# Get overall stats
{"method": "nowplaying.stats"}
→ {"total_played": 42, "stored_entries": 20}

# Get recent plays (Jive menu mode)
{"method": "nowplaying.recent", "params": ["menu:1"]}
→ {"count": 20, "item_loop": [...]}
```

### Data Storage

Play history is stored in `data/plugins/nowplaying/history.json`.
The file uses atomic writes (write-to-tmp → rename) to prevent
corruption. The total play count is preserved even when older
entries are trimmed to stay within the configured limit.

### Credits

Tutorial companion plugin for the Resonance Plugin System.
See `docs/PLUGIN_TUTORIAL.md` for a step-by-step guide to building
this plugin from scratch.
"""

    return Tab(label="About", children=[
        Markdown(content=md),
    ])


def _format_timestamp(iso_ts: str) -> str:
    """Format an ISO timestamp as a short human-readable string.

    Input: ``2026-02-14T18:30:00Z``
    Output: ``18:30:00`` or the relative time if today.
    """
    if not iso_ts or len(iso_ts) < 19:
        return "—"

    try:
        # Parse ISO timestamp
        from datetime import datetime, timezone

        # Handle both 'Z' suffix and no-suffix formats
        clean = iso_ts.replace("Z", "+00:00") if iso_ts.endswith("Z") else iso_ts
        try:
            dt = datetime.fromisoformat(clean)
        except ValueError:
            # Fallback: extract time portion directly
            return iso_ts[11:19] if len(iso_ts) >= 19 else iso_ts

        now = datetime.now(timezone.utc)
        delta = (now - dt).total_seconds()

        if delta < 60:
            return "just now"
        if delta < 3600:
            return f"{int(delta / 60)}m ago"
        if delta < 86400:
            return f"{int(delta / 3600)}h ago"

        days = int(delta / 86400)
        if days == 1:
            return "yesterday"
        if days < 7:
            return f"{days}d ago"

        # For older entries, show the date + time
        return iso_ts[0:10] + " " + iso_ts[11:19]
    except Exception:
        # Absolute fallback: just show the time portion
        return iso_ts[11:19] if len(iso_ts) >= 19 else iso_ts


# ---------------------------------------------------------------------------
# SDUI action handlers
# ---------------------------------------------------------------------------


async def handle_action(
    action: str, params: dict[str, Any], ctx: PluginContext
) -> dict[str, Any] | None:
    """Handle SDUI actions from the frontend."""
    match action:
        case "clear":
            return _handle_clear()
        case "save_settings":
            return _handle_save_settings(params, ctx)
        case _:
            return {"error": f"Unknown action: {action}"}


def _handle_clear() -> dict[str, Any]:
    """Clear all play history."""
    if _store is None:
        return {"error": "Store not available"}

    count = _store.count
    total = _store.total
    _store.clear()

    if _ctx is not None:
        _ctx.notify_ui_update()

    return {"message": f"Cleared {count} history entries ({total} total plays reset)"}


def _handle_save_settings(
    params: dict[str, Any], ctx: PluginContext
) -> dict[str, Any]:
    """Save settings from the SDUI settings form."""
    saved: list[str] = []

    if "max_entries" in params:
        try:
            value = int(params["max_entries"])
            ctx.set_setting("max_entries", value)
            saved.append("max_entries")

            # Apply live to the store
            if _store is not None:
                _store.update_max_entries(value)
        except (ValueError, TypeError):
            return {"error": "Invalid value for max_entries"}

    if _ctx is not None:
        _ctx.notify_ui_update()

    if saved:
        return {"message": f"Saved settings: {', '.join(saved)}"}
    else:
        return {"message": "No changes to save"}


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
    """Parse ``key:value`` tagged params from *command* starting at *start*.

    Delegates to :func:`resonance.web.jsonrpc_helpers.parse_tagged_params`.
    """
    return parse_tagged_params(command[start:])
