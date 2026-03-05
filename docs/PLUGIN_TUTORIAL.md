# Plugin Tutorial — Build Your Own Plugin from Scratch

## Welcome!

You can extend Resonance with plugins — and we mean _really_ extend it.
Plugins can add new audio sources, build full-featured management UIs,
control external hardware, or simply track what you are listening to.
All in Python, all with a clean API, and all without touching the
server core.

Here are some things plugins already do today:

- **Internet Radio** — browse and search thousands of stations
  (`plugins/radio/`)
- **Podcasts** — subscribe, browse episodes, resume playback
  (`plugins/podcast/`)
- **Favorites** — hierarchical folders, LMS-compatible
  (`plugins/favorites/`)
- **AirPlay Bridge** — turn AirPlay speakers into Squeezebox players,
  with a full tabbed UI, device management, and live status updates
  ([`raopbridge`](https://github.com/endegelaende/resonance-community-plugins/tree/main/plugins/raopbridge)
  — contributed by community member [Pinoatrome](https://github.com/pinoatrome))

The raopbridge plugin is a great example of what a community
contribution looks like. Pinoatrome ported an entire LMS plugin to
Python, and his backend code still powers the plugin today. If you
want to see a complex, real-world plugin in detail, read
[Build a Real Plugin — The raopbridge Story](PLUGIN_CASESTUDY.md)
after finishing this tutorial.

**Your plugin idea is welcome too.** Whether it is a small utility or
an ambitious integration — the plugin system is designed to support it.
Contributions to Resonance are valued and credited. Every plugin that
ships today started with someone thinking "I wonder if I could…"

This tutorial will take you from zero to a fully working plugin in
about 30 minutes. Let's go.

---

## What You Need

- **Python 3.11+** and basic `asyncio` knowledge
- **Resonance server** running locally (`python -m resonance` or
  `scripts/dev.ps1`)
- A text editor

You can always look things up in the
[API Reference](PLUGIN_API.md) or the
[Plugin System Overview](PLUGINS.md), but this tutorial is
self-contained — you will not need them to follow along.

---

## What We Are Building: "Now Playing Info"

We will build a plugin called **nowplaying** that:

1. **Listens** to what is playing (event subscription)
2. **Counts** tracks and keeps a history (business logic)
3. **Answers questions** via JSON-RPC ("how many tracks played?")
4. **Shows up** on Squeezebox hardware menus (Jive menu entry)
5. **Remembers** data across server restarts (persistence)
6. **Has a web UI page** in the browser (SDUI — optional, shown at the end)
7. **Is fully tested** (58 pytest tests)

The final file structure looks like this:

```
plugins/nowplaying/
├── plugin.toml          ← Manifest
├── __init__.py          ← Logic
├── store.py             ← Persistence
```

```
tests/
└── test_nowplaying_plugin.py   ← Tests
```

---

## Step 1: Directory and Manifest

Create the plugin folder and the manifest:

```
mkdir plugins/nowplaying
```

**`plugins/nowplaying/plugin.toml`:**

```toml
[plugin]
name = "nowplaying"
version = "0.1.0"
description = "Tracks recently played songs and provides play statistics"
author = "Tutorial"
min_resonance_version = "0.1.0"
```

Rules for the manifest:

- `name` **must** match the directory name
- `name` and `version` are required fields
- Everything else is optional

> **Tip:** Start the server now. The log should show:
>
> ```
> INFO  Discovered plugin: nowplaying v0.1.0 (plugins/nowplaying)
> ```
>
> It will fail to load because `__init__.py` does not exist yet —
> that is expected behavior.

---

## Step 2: Minimal Entry Point

Create `plugins/nowplaying/__init__.py` with the smallest possible plugin:

```python
"""Now Playing Info — tracks recently played songs."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from resonance.plugin import PluginContext
    from resonance.web.handlers import CommandContext

logger = logging.getLogger(__name__)


async def setup(ctx: PluginContext) -> None:
    """Called on server startup."""
    ctx.register_command("nowplaying.stats", cmd_stats)
    logger.info("Now Playing plugin started")


async def cmd_stats(ctx: CommandContext, command: list[Any]) -> dict[str, Any]:
    """Return a simple response."""
    return {"message": "Now Playing plugin is running!"}
```

### What Is Happening Here?

1. **`from __future__ import annotations`** — defers type-hint evaluation
   to analysis time. Prevents circular imports.

2. **`TYPE_CHECKING` guard** — `PluginContext` and `CommandContext` are
   only imported for type checking, not at runtime. This keeps the import
   fast and avoids dependency issues.

3. **`setup(ctx)`** — the only required function. Here we register a
   JSON-RPC command `nowplaying.stats`.

4. **`cmd_stats(ctx, command)`** — the command handler. Receives a
   `CommandContext` (with player ID, library, etc.) and the raw command
   array. Returns a dict that is sent as the JSON-RPC result.

### Try It Out

Start the server and test:

```bash
curl -X POST http://localhost:9000/jsonrpc.js \
  -H "Content-Type: application/json" \
  -d '{"id":1,"method":"slim.request","params":["-",["nowplaying.stats"]]}'
```

Expected response:

```json
{ "id": 1, "result": { "message": "Now Playing plugin is running!" } }
```

---

## Step 3: Subscribe to Events — Count Tracks

Now let's make the plugin useful: we count which tracks are played.

Update `__init__.py`:

```python
"""Now Playing Info — tracks recently played songs."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from resonance.core.events import Event
    from resonance.plugin import PluginContext
    from resonance.web.handlers import CommandContext

logger = logging.getLogger(__name__)

# ── Module-Level State ─────────────────────────────────────────────
# Initialized in setup() and reset in teardown().

_play_count: int = 0
_last_tracks: list[dict[str, Any]] = []
_max_history: int = 50


# ── Lifecycle ──────────────────────────────────────────────────────


async def setup(ctx: PluginContext) -> None:
    """Called on server startup."""
    global _play_count, _last_tracks

    _play_count = 0
    _last_tracks = []

    # 1) Register command
    ctx.register_command("nowplaying.stats", cmd_stats)

    # 2) Subscribe to event — use ctx.subscribe() instead of
    #    event_bus.subscribe() so the handler is automatically
    #    unsubscribed on teardown.
    await ctx.subscribe("player.track_started", _on_track_started)

    logger.info("Now Playing plugin started")


async def teardown(ctx: PluginContext) -> None:
    """Called on server shutdown."""
    global _play_count, _last_tracks
    logger.info(
        "Now Playing plugin stopping — %d tracks played this session",
        _play_count,
    )
    _play_count = 0
    _last_tracks = []


# ── Event Handler ──────────────────────────────────────────────────


async def _on_track_started(event: Event) -> None:
    """Called on every track start."""
    global _play_count
    _play_count += 1

    entry = {
        "player_id": getattr(event, "player_id", "unknown"),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "play_number": _play_count,
    }
    _last_tracks.append(entry)

    # Trim list to _max_history
    if len(_last_tracks) > _max_history:
        del _last_tracks[: len(_last_tracks) - _max_history]

    logger.debug("Track #%d started on %s", _play_count, entry["player_id"])


# ── Command Handler ────────────────────────────────────────────────


async def cmd_stats(ctx: CommandContext, command: list[Any]) -> dict[str, Any]:
    """Handle 'nowplaying.stats' — return play statistics."""
    return {
        "total_played": _play_count,
        "recent_count": len(_last_tracks),
        "recent": _last_tracks[-10:],  # Last 10
    }
```

### What Is New?

- **`ctx.subscribe("player.track_started", ...)`** subscribes to the event
  _with auto-cleanup_. When the plugin is stopped the handler is
  automatically unsubscribed. No manual cleanup needed.

- **`_on_track_started(event)`** is an async event handler. The `event`
  object has fields like `player_id` and `stream_generation` (depending
  on the event type).

- **`teardown()`** resets the state. Without this the data would survive
  a server restart (without process restart).

- **Module-level state** (`_play_count`, `_last_tracks`): simple and
  effective for in-memory data. Persistence comes in Step 5.

### Try It Out

Play a track and then query:

```json
{ "method": "slim.request", "params": ["-", ["nowplaying.stats"]] }
```

Response:

```json
{
    "result": {
        "total_played": 3,
        "recent_count": 3,
        "recent": [
            {
                "player_id": "aa:bb:cc:dd:ee:ff",
                "timestamp": "2026-02-14T18:30:00Z",
                "play_number": 1
            },
            {
                "player_id": "aa:bb:cc:dd:ee:ff",
                "timestamp": "2026-02-14T18:33:12Z",
                "play_number": 2
            },
            {
                "player_id": "aa:bb:cc:dd:ee:ff",
                "timestamp": "2026-02-14T18:36:45Z",
                "play_number": 3
            }
        ]
    }
}
```

---

## Step 4: Jive Menu Entry

Now let's make the plugin visible on Squeezebox Touch/Radio/Boom.

> **No Squeezebox hardware?** This step still works — the code runs fine
> and the menu data is served correctly, you just won't see it on a
> physical device. The command handler pattern you learn here is the
> same one used everywhere else in the server. If you prefer, you can
> skip ahead to [Step 5: Persistence](#step-5-persistence--saving-data)
> and come back later.

Add the following to `setup()` after the command registration:

```python
async def setup(ctx: PluginContext) -> None:
    """Called on server startup."""
    global _play_count, _last_tracks

    _play_count = 0
    _last_tracks = []

    # 1) Register commands
    ctx.register_command("nowplaying.stats", cmd_stats)
    ctx.register_command("nowplaying.recent", cmd_recent)

    # 2) Subscribe to events
    await ctx.subscribe("player.track_started", _on_track_started)

    # 3) Jive menu: node in the home menu
    ctx.register_menu_node(
        node_id="nowPlaying",
        parent="home",
        text="Play Stats",
        weight=80,                      # Between Favorites (55) and Power (100)
        actions={
            "go": {
                "cmd": ["nowplaying.recent"],
                "params": {"menu": 1},
            },
        },
    )

    logger.info("Now Playing plugin started")
```

And the new command handler for the Jive display:

```python
async def cmd_recent(ctx: CommandContext, command: list[Any]) -> dict[str, Any]:
    """Handle 'nowplaying.recent' — Jive-compatible list of recent tracks."""
    tagged = _parse_tagged(command, start=1)
    is_menu = tagged.get("menu") == "1"

    items = list(reversed(_last_tracks[-20:]))  # Newest first

    if not items:
        loop = [{"text": "No tracks played yet", "style": "itemNoAction"}]
    else:
        loop = []
        for entry in items:
            loop.append({
                "text": f"#{entry['play_number']} — {entry['player_id']}",
                "style": "itemNoAction",
            })

    loop_key = "item_loop" if is_menu else "loop"
    return {
        "count": len(loop),
        "offset": 0,
        loop_key: loop,
    }


def _parse_tagged(command: list[Any], start: int = 1) -> dict[str, str]:
    """Parse key:value parameters from the command array."""
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
```

### What Happens on the Device?

1. User navigates to the home menu → sees "Play Stats" (weight 80)
2. Taps it → device sends `["nowplaying.recent", "menu:1"]`
3. Server responds with `item_loop` → device displays the list

### Weight Placement

```
11  My Music
55  Favorites
80  Play Stats          ← our plugin
100 Turn Player Off
```

---

## Step 5: Persistence — Saving Data

Currently all data is lost on server restart. Let's build a JSON store
that writes the history to disk.

**`plugins/nowplaying/store.py`:**

```python
"""Persistence layer for the Now Playing plugin."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class PlayHistory:
    """JSON-backed play history with atomic write."""

    def __init__(self, data_dir: Path, *, max_entries: int = 500) -> None:
        self._file = data_dir / "history.json"
        self._max = max_entries
        self._entries: list[dict[str, Any]] = []
        self._total: int = 0

    # ── Properties ─────────────────────────────────────────────

    @property
    def total(self) -> int:
        """Total number of tracks ever counted."""
        return self._total

    @property
    def entries(self) -> list[dict[str, Any]]:
        """Stored history entries (oldest first)."""
        return self._entries

    @property
    def count(self) -> int:
        """Number of stored entries."""
        return len(self._entries)

    # ── Load / Save ────────────────────────────────────────────

    def load(self) -> None:
        """Load history from JSON file. Starts empty if the file is missing."""
        if not self._file.is_file():
            logger.info("No history file at %s — starting fresh", self._file)
            return

        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
            self._entries = data.get("entries", [])
            self._total = data.get("total", len(self._entries))
            logger.info("Loaded %d history entries (total: %d)", len(self._entries), self._total)
        except Exception as exc:
            logger.error("Failed to load history: %s", exc)
            self._entries = []
            self._total = 0

    def save(self) -> None:
        """Save history atomically (write-to-tmp → rename)."""
        self._file.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "total": self._total,
            "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "entries": self._entries,
        }

        tmp = self._file.with_suffix(".tmp")
        try:
            tmp.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp.replace(self._file)
        except Exception as exc:
            logger.error("Failed to save history: %s", exc)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    # ── Mutations ──────────────────────────────────────────────

    def record(self, player_id: str) -> dict[str, Any]:
        """Record a new track play. Returns the entry."""
        self._total += 1

        entry = {
            "player_id": player_id,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "play_number": self._total,
        }
        self._entries.append(entry)

        # Remove old entries
        if len(self._entries) > self._max:
            self._entries = self._entries[-self._max :]

        self.save()
        return entry

    def clear(self) -> None:
        """Clear the history."""
        self._entries.clear()
        self._total = 0
        self.save()
```

### Update **init**.py

Now use the store in the plugin. Here is the **complete, final version**
of `plugins/nowplaying/__init__.py`:

```python
"""Now Playing Info — tracks recently played songs and provides play statistics."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from resonance.core.events import Event
    from resonance.plugin import PluginContext
    from resonance.web.handlers import CommandContext

logger = logging.getLogger(__name__)

# ── Module-Level State ─────────────────────────────────────────────

_store: Any | None = None  # PlayHistory instance


# ── Lifecycle ──────────────────────────────────────────────────────


async def setup(ctx: PluginContext) -> None:
    """Called on server startup."""
    global _store

    from .store import PlayHistory

    data_dir = ctx.ensure_data_dir()
    _store = PlayHistory(data_dir)
    _store.load()

    # Commands
    ctx.register_command("nowplaying.stats", cmd_stats)
    ctx.register_command("nowplaying.recent", cmd_recent)

    # Events
    await ctx.subscribe("player.track_started", _on_track_started)

    # Jive menu
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
    """Called on server shutdown."""
    global _store

    if _store is not None:
        logger.info("Now Playing plugin stopping — %d total plays", _store.total)
    _store = None


# ── Event Handler ──────────────────────────────────────────────────


async def _on_track_started(event: Event) -> None:
    """Called on every track start."""
    if _store is None:
        return

    player_id = getattr(event, "player_id", "unknown")
    entry = _store.record(player_id)
    logger.debug("Track #%d on %s", entry["play_number"], player_id)


# ── Command Handler ────────────────────────────────────────────────


async def cmd_stats(ctx: CommandContext, command: list[Any]) -> dict[str, Any]:
    """Handle 'nowplaying.stats' — overall statistics."""
    if _store is None:
        return {"error": "Now Playing plugin not initialized"}

    return {
        "total_played": _store.total,
        "stored_entries": _store.count,
    }


async def cmd_recent(ctx: CommandContext, command: list[Any]) -> dict[str, Any]:
    """Handle 'nowplaying.recent' — recent tracks (CLI and Jive)."""
    if _store is None:
        return {"error": "Now Playing plugin not initialized"}

    tagged = _parse_tagged(command, start=1)
    is_menu = tagged.get("menu") == "1"

    recent = list(reversed(_store.entries[-20:]))  # Newest first

    if not recent:
        loop = [{"text": "No tracks played yet", "style": "itemNoAction"}]
    else:
        loop = []
        for entry in recent:
            text = f"#{entry['play_number']} — {entry['player_id']}"
            if entry.get("timestamp"):
                text += f" ({entry['timestamp'][11:19]})"
            loop.append({"text": text, "style": "itemNoAction"})

    loop_key = "item_loop" if is_menu else "loop"
    return {
        "count": len(loop),
        "offset": 0,
        loop_key: loop,
    }


# ── Utility Functions ──────────────────────────────────────────────


def _parse_tagged(command: list[Any], start: int = 1) -> dict[str, str]:
    """Parse key:value parameters and dict elements from the command array."""
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
```

### What Changed?

- **`_store`** replaces the individual variables `_play_count` and `_last_tracks`.
- **`from .store import PlayHistory`** — relative import within the plugin.
  This works because the PluginManager loads the plugin as a Python package.
- **`setup()`** loads existing data; `teardown()` only resets the reference
  (the store already saved on the last `record()` call).
- **`_on_track_started()`** calls `_store.record()`, which saves automatically.

### Test It

1. Start the server, play a few tracks
2. Stop and restart the server
3. Query `nowplaying.stats` — `total_played` should retain its value

---

## Step 6: Adding Settings to Your Plugin

Now make the plugin configurable without changing Python code.

### 1) Add settings to `plugin.toml`

```toml
[plugin]
name = "nowplaying"
version = "0.1.0"
description = "Now Playing stats plugin"

[settings.max_entries]
type = "int"
label = "Maximum history entries"
default = 200
min = 20
max = 2000
restart_required = true

[settings.show_timestamps]
type = "bool"
label = "Show timestamps in recent list"
default = true

[settings.display_format]
type = "string"
label = "Display format for recent tracks"
default = "compact"

[settings.max_title_length]
type = "int"
label = "Maximum title length (detailed mode)"
default = 40
min = 10
max = 200
```

### 2) Read settings in your plugin code

Use `ctx.get_setting()` in `setup()` and/or handlers:

```python
async def setup(ctx: PluginContext) -> None:
    global _store
    data_dir = ctx.ensure_data_dir()
    max_entries = int(ctx.get_setting("max_entries"))
    _store = PlayHistory(data_dir, max_entries=max_entries)
    _store.load()
```

Store frequently used settings in module-level state and use them in handlers:

```python
_show_timestamps = bool(ctx.get_setting("show_timestamps"))
...
if _show_timestamps and entry.get("timestamp"):
    text += f" ({entry['timestamp'][11:19]})"
```

### 3) Update settings via API

- REST:
    - `GET /api/plugins/nowplaying/settings`
    - `PUT /api/plugins/nowplaying/settings` with JSON body, e.g. `{ "max_entries": 500 }`
- JSON-RPC:
    - `["pluginsettings", "get", "nowplaying"]`
    - `["pluginsettings", "set", "nowplaying", "max_entries:500"]`

Settings are validated against the schema and persisted in
`data/plugins/nowplaying/settings.json`. Secret settings are masked in responses.

### Try It Out

Start the server and query the settings:

```bash
curl http://localhost:9000/api/plugins/nowplaying/settings
```

Expected response:

```json
{
    "plugin_id": "nowplaying",
    "settings": {
        "max_entries": 200,
        "show_timestamps": true
    },
    "definitions": [
        {
            "key": "max_entries",
            "type": "int",
            "label": "Maximum history entries",
            "default": 200,
            "min": 20,
            "max": 2000,
            "restart_required": true
        },
        {
            "key": "show_timestamps",
            "type": "bool",
            "label": "Show timestamps in recent list",
            "default": true
        }
    ]
}
```

Now change a setting:

```bash
curl -X PUT http://localhost:9000/api/plugins/nowplaying/settings \
  -H "Content-Type: application/json" \
  -d '{"show_timestamps": false}'
```

Query again — `show_timestamps` is now `false`. The value is persisted in
`data/plugins/nowplaying/settings.json`, so it survives server restarts.

> **What about validation?** Try setting `max_entries` to `5` (below the
> minimum of 20). The server rejects it with a `400` error and an
> explanation. Schema validation comes for free from the `plugin.toml`
> definition — no manual checking needed in your Python code.

> **Note:** We do not add separate tests for settings in Step 7 because
> settings validation and persistence are handled by the server core,
> not by our plugin code. Your plugin just calls `ctx.get_setting()` —
> the framework does the rest.

---

## Step 7: Write Tests

Now let's secure everything with tests. Create `tests/test_nowplaying_plugin.py`:

```python
"""Tests for the Now Playing plugin."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


# ═══════════════════════════════════════════════════════════════════
# Store Tests
# ═══════════════════════════════════════════════════════════════════


class TestPlayHistory:
    """Tests for the PlayHistory store."""

    @pytest.fixture()
    def store(self, tmp_path: Path):
        from plugins.nowplaying.store import PlayHistory
        s = PlayHistory(tmp_path)
        s.load()
        return s

    def test_empty_store(self, store):
        assert store.total == 0
        assert store.count == 0
        assert store.entries == []

    def test_record(self, store):
        entry = store.record("aa:bb:cc:dd:ee:ff")
        assert entry["player_id"] == "aa:bb:cc:dd:ee:ff"
        assert entry["play_number"] == 1
        assert "timestamp" in entry
        assert store.total == 1
        assert store.count == 1

    def test_record_multiple(self, store):
        store.record("aa:bb:cc:dd:ee:ff")
        store.record("11:22:33:44:55:66")
        store.record("aa:bb:cc:dd:ee:ff")
        assert store.total == 3
        assert store.count == 3
        assert store.entries[-1]["play_number"] == 3

    def test_record_trims_old_entries(self, tmp_path):
        from plugins.nowplaying.store import PlayHistory
        store = PlayHistory(tmp_path, max_entries=5)
        store.load()

        for i in range(10):
            store.record(f"player-{i}")

        assert store.total == 10          # Counter counts everything
        assert store.count == 5           # Only 5 stored
        assert store.entries[0]["play_number"] == 6  # Oldest: #6

    def test_clear(self, store):
        store.record("aa:bb:cc:dd:ee:ff")
        store.record("aa:bb:cc:dd:ee:ff")
        store.clear()
        assert store.total == 0
        assert store.count == 0

    # ── Persistence ────────────────────────────────────────────

    def test_save_and_load(self, tmp_path):
        from plugins.nowplaying.store import PlayHistory

        # Write data
        s1 = PlayHistory(tmp_path)
        s1.load()
        s1.record("aa:bb:cc:dd:ee:ff")
        s1.record("11:22:33:44:55:66")

        # Load in a new store
        s2 = PlayHistory(tmp_path)
        s2.load()
        assert s2.total == 2
        assert s2.count == 2
        assert s2.entries[0]["player_id"] == "aa:bb:cc:dd:ee:ff"

    def test_save_creates_directory(self, tmp_path):
        from plugins.nowplaying.store import PlayHistory
        nested = tmp_path / "deep" / "nested"
        store = PlayHistory(nested)
        store.load()
        store.record("test")
        assert (nested / "history.json").is_file()

    def test_save_is_valid_json(self, tmp_path):
        from plugins.nowplaying.store import PlayHistory
        store = PlayHistory(tmp_path)
        store.load()
        store.record("test")

        content = (tmp_path / "history.json").read_text(encoding="utf-8")
        data = json.loads(content)
        assert "total" in data
        assert "entries" in data
        assert "updated" in data

    def test_load_corrupt_json(self, tmp_path):
        from plugins.nowplaying.store import PlayHistory
        (tmp_path / "history.json").write_text("BROKEN!!!", encoding="utf-8")
        store = PlayHistory(tmp_path)
        store.load()
        assert store.total == 0  # Graceful degradation

    def test_load_nonexistent(self, tmp_path):
        from plugins.nowplaying.store import PlayHistory
        store = PlayHistory(tmp_path)
        store.load()
        assert store.total == 0


# ═══════════════════════════════════════════════════════════════════
# Fake CommandContext
# ═══════════════════════════════════════════════════════════════════


class _FakeCtx:
    """Minimal stand-in for CommandContext."""

    def __init__(self, player_id: str = "-"):
        self.player_id = player_id
        self.music_library = None
        self.player_registry = AsyncMock()
        self.player_registry.get_by_mac = AsyncMock(return_value=None)
        self.playlist_manager = None
        self.streaming_server = None
        self.slimproto = None
        self.artwork_manager = None
        self.server_host = "127.0.0.1"
        self.server_port = 9000
        self.server_uuid = "test-uuid"


# ═══════════════════════════════════════════════════════════════════
# Plugin-Environment Fixture
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture()
def np_env(tmp_path):
    """Set up plugin state for tests."""
    import plugins.nowplaying as mod
    from plugins.nowplaying.store import PlayHistory

    store = PlayHistory(tmp_path)
    store.load()
    mod._store = store

    yield store, mod

    mod._store = None


# ═══════════════════════════════════════════════════════════════════
# Command Handler Tests
# ═══════════════════════════════════════════════════════════════════


class TestCmdStats:
    """Tests for nowplaying.stats."""

    @pytest.mark.asyncio
    async def test_stats_empty(self, np_env):
        store, _ = np_env
        from plugins.nowplaying import cmd_stats

        result = await cmd_stats(_FakeCtx(), ["nowplaying.stats"])
        assert result["total_played"] == 0
        assert result["stored_entries"] == 0

    @pytest.mark.asyncio
    async def test_stats_after_records(self, np_env):
        store, _ = np_env
        store.record("player1")
        store.record("player2")

        from plugins.nowplaying import cmd_stats

        result = await cmd_stats(_FakeCtx(), ["nowplaying.stats"])
        assert result["total_played"] == 2
        assert result["stored_entries"] == 2

    @pytest.mark.asyncio
    async def test_stats_not_initialized(self):
        import plugins.nowplaying as mod
        mod._store = None

        from plugins.nowplaying import cmd_stats

        result = await cmd_stats(_FakeCtx(), ["nowplaying.stats"])
        assert "error" in result


class TestCmdRecent:
    """Tests for nowplaying.recent."""

    @pytest.mark.asyncio
    async def test_recent_empty(self, np_env):
        from plugins.nowplaying import cmd_recent

        result = await cmd_recent(_FakeCtx(), ["nowplaying.recent"])
        assert result["count"] == 1
        assert "No tracks" in result["loop"][0]["text"]

    @pytest.mark.asyncio
    async def test_recent_with_data(self, np_env):
        store, _ = np_env
        store.record("player1")
        store.record("player2")

        from plugins.nowplaying import cmd_recent

        result = await cmd_recent(_FakeCtx(), ["nowplaying.recent"])
        assert result["count"] == 2
        # Newest first
        assert "#2" in result["loop"][0]["text"]
        assert "#1" in result["loop"][1]["text"]

    @pytest.mark.asyncio
    async def test_recent_menu_mode(self, np_env):
        store, _ = np_env
        store.record("player1")

        from plugins.nowplaying import cmd_recent

        result = await cmd_recent(_FakeCtx(), ["nowplaying.recent", "menu:1"])
        assert "item_loop" in result
        assert "loop" not in result

    @pytest.mark.asyncio
    async def test_recent_cli_mode(self, np_env):
        store, _ = np_env
        store.record("player1")

        from plugins.nowplaying import cmd_recent

        result = await cmd_recent(_FakeCtx(), ["nowplaying.recent"])
        assert "loop" in result
        assert "item_loop" not in result


# ═══════════════════════════════════════════════════════════════════
# Event Handler Tests
# ═══════════════════════════════════════════════════════════════════


class TestEventHandler:
    """Tests for the track-started event handler."""

    @pytest.mark.asyncio
    async def test_on_track_started(self, np_env):
        store, _ = np_env
        from plugins.nowplaying import _on_track_started

        # Simulate an event
        event = MagicMock()
        event.player_id = "aa:bb:cc:dd:ee:ff"

        await _on_track_started(event)
        assert store.total == 1
        assert store.entries[-1]["player_id"] == "aa:bb:cc:dd:ee:ff"

    @pytest.mark.asyncio
    async def test_on_track_started_multiple(self, np_env):
        store, _ = np_env
        from plugins.nowplaying import _on_track_started

        for i in range(5):
            event = MagicMock()
            event.player_id = f"player-{i}"
            await _on_track_started(event)

        assert store.total == 5

    @pytest.mark.asyncio
    async def test_on_track_started_store_none(self):
        """Handler should do nothing when the store is not initialized."""
        import plugins.nowplaying as mod
        mod._store = None

        from plugins.nowplaying import _on_track_started

        event = MagicMock()
        event.player_id = "test"
        await _on_track_started(event)  # Should not crash


# ═══════════════════════════════════════════════════════════════════
# Plugin Lifecycle Tests
# ═══════════════════════════════════════════════════════════════════


class TestLifecycle:
    """Tests for setup() and teardown()."""

    @pytest.fixture()
    def mock_ctx(self, tmp_path):
        ctx = MagicMock()
        ctx.plugin_id = "nowplaying"
        ctx.data_dir = tmp_path
        ctx.ensure_data_dir = MagicMock(return_value=tmp_path)
        ctx.event_bus = MagicMock()
        ctx.event_bus.publish = AsyncMock()
        ctx.register_command = MagicMock()
        ctx.register_menu_node = MagicMock()
        ctx.subscribe = AsyncMock()
        return ctx

    @pytest.mark.asyncio
    async def test_setup_registers_commands(self, mock_ctx):
        import plugins.nowplaying as mod
        await mod.setup(mock_ctx)

        names = [c[0][0] for c in mock_ctx.register_command.call_args_list]
        assert "nowplaying.stats" in names
        assert "nowplaying.recent" in names

        await mod.teardown(mock_ctx)

    @pytest.mark.asyncio
    async def test_setup_registers_menu(self, mock_ctx):
        import plugins.nowplaying as mod
        await mod.setup(mock_ctx)

        mock_ctx.register_menu_node.assert_called_once()
        kwargs = mock_ctx.register_menu_node.call_args[1]
        assert kwargs["node_id"] == "nowPlaying"
        assert kwargs["parent"] == "home"

        await mod.teardown(mock_ctx)

    @pytest.mark.asyncio
    async def test_setup_subscribes_events(self, mock_ctx):
        import plugins.nowplaying as mod
        await mod.setup(mock_ctx)

        mock_ctx.subscribe.assert_called_once()
        args = mock_ctx.subscribe.call_args[0]
        assert args[0] == "player.track_started"

        await mod.teardown(mock_ctx)

    @pytest.mark.asyncio
    async def test_teardown_clears_store(self, mock_ctx):
        import plugins.nowplaying as mod
        await mod.setup(mock_ctx)
        assert mod._store is not None

        await mod.teardown(mock_ctx)
        assert mod._store is None

    @pytest.mark.asyncio
    async def test_setup_loads_existing_data(self, mock_ctx, tmp_path):
        """Existing history.json is loaded on startup."""
        data = {
            "total": 42,
            "entries": [
                {"player_id": "test", "timestamp": "2026-01-01T00:00:00Z", "play_number": 42}
            ],
        }
        (tmp_path / "history.json").write_text(json.dumps(data), encoding="utf-8")

        import plugins.nowplaying as mod
        await mod.setup(mock_ctx)

        assert mod._store.total == 42
        assert mod._store.count == 1

        await mod.teardown(mock_ctx)


# ═══════════════════════════════════════════════════════════════════
# Parse-Helper Tests
# ═══════════════════════════════════════════════════════════════════


class TestParseTagged:
    """Tests for the _parse_tagged helper."""

    def test_string_params(self):
        from plugins.nowplaying import _parse_tagged
        result = _parse_tagged(["cmd", "menu:1", "search:hello"], start=1)
        assert result["menu"] == "1"
        assert result["search"] == "hello"

    def test_dict_params(self):
        from plugins.nowplaying import _parse_tagged
        result = _parse_tagged(["cmd", {"menu": "1"}], start=1)
        assert result["menu"] == "1"

    def test_colon_in_value(self):
        from plugins.nowplaying import _parse_tagged
        result = _parse_tagged(["cmd", "url:http://host:8080/path"], start=1)
        assert result["url"] == "http://host:8080/path"

    def test_ignores_non_tagged(self):
        from plugins.nowplaying import _parse_tagged
        result = _parse_tagged(["cmd", "plain", "key:val"], start=1)
        assert "plain" not in result
        assert result["key"] == "val"
```

### Run Tests

```bash
python -m pytest tests/test_nowplaying_plugin.py -v
```

Expected result:

```
tests/test_nowplaying_plugin.py::TestPlayHistory::test_empty_store PASSED
tests/test_nowplaying_plugin.py::TestPlayHistory::test_record PASSED
tests/test_nowplaying_plugin.py::TestPlayHistory::test_record_multiple PASSED
...
============================= 58 passed in 1.07s ==============================
```

### What the Tests Cover

| Area                 | Tests                                                                           |
| -------------------- | ------------------------------------------------------------------------------- |
| PlayHistory Store    | CRUD, trimming, persistence, corrupt JSON, ordering, tmp cleanup                |
| `cmd_stats` handler  | Empty, with data, not initialized, many entries                                 |
| `cmd_recent` handler | Empty, with data, menu/CLI mode, limit, timestamp, dict params, style           |
| Event handler        | Single, multiple, store=None, missing player_id, persistence                    |
| Lifecycle            | setup/teardown, command/menu/event registration, existing data, ensure_data_dir |
| Parse helper         | String params, dict params, mixed, edge cases, offset                           |
| Integration          | Record→Query, persistence workflow, empty→filled, full lifecycle                |

---

## Step 8: What the Plugin Can Do Now

Congratulations! The tutorial plugin is complete. Here is the summary:

```
plugins/nowplaying/
├── plugin.toml          6 lines      Manifest
├── __init__.py        ~140 lines     Commands, events, lifecycle
└── store.py           ~100 lines     JSON persistence
```

```
tests/
└── test_nowplaying_plugin.py   ~810 lines     58 tests
```

| Feature               | How                                                           |
| --------------------- | ------------------------------------------------------------- |
| ✅ Count tracks       | Event subscription on `player.track_started`                  |
| ✅ Query statistics   | Command `nowplaying.stats`                                    |
| ✅ Show recent tracks | Command `nowplaying.recent` (CLI + Jive)                      |
| ✅ Visible on device  | Menu node "Play Stats" in the home menu                       |
| ✅ Persist data       | JSON store with atomic write                                  |
| ✅ Tested             | 58 tests: store + commands + events + lifecycle + integration |

---

## Going Further: Extension Ideas

You now have all the fundamentals. Here are ideas to extend the plugin
and learn advanced features:

### Idea 1: Show Track Titles

Currently we only show the player ID. Request track information via
the `MusicLibrary`:

```python
async def _on_track_started(event: Event) -> None:
    if _store is None:
        return

    player_id = getattr(event, "player_id", "unknown")
    title = "Unknown"

    # Get track info from the playlist
    if _ctx and _ctx.playlist_manager:
        playlist = _ctx.playlist_manager.get(player_id)
        track = playlist.current_track
        if track:
            title = track.get("title", "Unknown")

    _store.record(player_id, title=title)
```

For this, `record()` would need to accept an optional `title` parameter.

### Idea 2: Add a REST API

```python
from fastapi import APIRouter

router = APIRouter(prefix="/api/nowplaying", tags=["nowplaying"])

@router.get("/stats")
async def api_stats():
    if _store is None:
        return {"error": "not initialized"}
    return {"total": _store.total, "entries": _store.entries[-20:]}


async def setup(ctx: PluginContext) -> None:
    # ... existing code ...
    ctx.register_route(router)
```

Then: `GET http://localhost:9000/api/nowplaying/stats`

### Idea 3: Publish Custom Events

Publish a custom event when a milestone is reached:

```python
from dataclasses import dataclass, field
from resonance.core.events import Event as BaseEvent

@dataclass
class MilestoneEvent(BaseEvent):
    event_type: str = field(default="nowplaying.milestone", init=False)
    total: int = 0

async def _on_track_started(event):
    # ... existing logic ...
    if _store.total % 100 == 0:
        await _ctx.event_bus.publish(MilestoneEvent(total=_store.total))
```

Here `_ctx` is the `PluginContext` reference saved in `setup()` (see
the pattern from [Idea 6, Part B](#part-b-settings-form-with-tabs)).
The `event_bus` attribute gives you direct access to the server's event
bus for publishing — subscribing should still go through `ctx.subscribe()`
for automatic cleanup.

Other plugins could then subscribe to `"nowplaying.milestone"`.

### Idea 4: Per-Player Statistics

Extend the store to count per player:

```python
_player_counts: dict[str, int] = {}

def record(self, player_id: str) -> dict:
    # ... existing logic ...
    self._player_counts[player_id] = self._player_counts.get(player_id, 0) + 1
```

Then `nowplaying.stats` can return a player leaderboard.

### Idea 5: Wildcard Events

Instead of only `player.track_started` you could subscribe to all player events:

```python
await ctx.subscribe("player.*", _on_any_player_event)
```

This is useful for monitoring or debug plugins.

### Idea 6: UI Page in the Web UI (SDUI)

Your plugin can have its own page in the web UI sidebar — no JavaScript
required. You describe the UI as a Python data structure and the frontend
renders it automatically. This is one of the most powerful features of
the plugin system, so we will cover it in two parts:

1. **A read-only status page** — display data, buttons, tables
2. **A settings form** — text inputs, dropdowns, toggles, conditional fields

#### Part A: Status Page

**1. Add `[ui]` to `plugin.toml`:**

```toml
[ui]
enabled = true
sidebar_label = "Now Playing Stats"
sidebar_icon = "activity"
```

This tells the server your plugin has a UI page. The `sidebar_label` is
what appears in the web UI navigation, and `sidebar_icon` is a
[Lucide icon](https://lucide.dev/) name.

**2. Write a `get_ui()` function:**

The server calls `get_ui(ctx)` with the plugin's `PluginContext` — the
same `ctx` you receive in `setup()`. This means you can call
`ctx.get_setting()`, access `ctx.data_dir`, etc.

```python
from resonance.ui import (
    Page, Card, KeyValue, KVItem, StatusBadge, Button, Row, Table, TableColumn,
)

async def get_ui(ctx: PluginContext):
    total = _store.total if _store else 0
    recent = _store.entries[:5] if _store else []

    components = [
        Card(title="Statistics", children=[
            StatusBadge(
                label=f"{total} tracks played",
                color="green" if total > 0 else "gray",
            ),
            KeyValue(items=[
                KVItem("Total Played", str(total)),
                KVItem("Recent Count", str(len(recent))),
            ]),
        ]),
        Row(children=[
            Button("Clear History", action="clear", style="danger", confirm=True),
        ]),
    ]

    if recent:
        components.append(Table(
            title="Recent Tracks",
            columns=[
                TableColumn(key="player_id", label="Player"),
                TableColumn(key="timestamp", label="Time"),
                TableColumn(key="play_number", label="#"),
            ],
            rows=recent,
        ))

    return Page(
        title="Now Playing Stats",
        icon="activity",
        refresh_interval=10,
        components=components,
    )
```

Here is what each widget does:

| Widget        | What It Renders                                                                  |
| ------------- | -------------------------------------------------------------------------------- |
| `Page`        | Top-level container. `title` is shown as the page heading.                       |
| `Card`        | A bordered box with an optional title — groups related content together.         |
| `StatusBadge` | A small colored pill (green/red/yellow/blue/gray) — good for status at a glance. |
| `KeyValue`    | A list of label→value pairs, nicely aligned.                                     |
| `KVItem`      | One row in a `KeyValue` list. Optional `color` for the value.                    |
| `Row`         | Horizontal layout — places children side by side.                                |
| `Button`      | Triggers an action on click. `confirm=True` shows a "Are you sure?" dialog.      |
| `Table`       | A data table with column headers and rows.                                       |
| `TableColumn` | Defines one column: `key` matches a field in the row dicts.                      |

**3. Write a `handle_action()` function:**

Actions are triggered when the user clicks a `Button` or submits a `Form`.
The `action` string matches what you set on the widget, and `params`
contains any data (empty for buttons, form values for forms).

```python
async def handle_action(action: str, params: dict) -> dict:
    if action == "clear" and _store:
        _store.clear()
        return {"message": "History cleared"}
    return {"error": f"Unknown action: {action}"}
```

Return `{"message": "..."}` for a success toast, or `{"error": "..."}`
for an error toast. The frontend shows these automatically.

**4. Register both in `setup()`:**

```python
async def setup(ctx):
    # ... existing registrations ...
    ctx.register_ui_handler(get_ui)
    ctx.register_action_handler(handle_action)
```

#### Try It Out

Start the server and open `http://localhost:9000` in your browser.
"Now Playing Stats" should appear in the sidebar. Click it — you see
your statistics card, recent tracks table, and a "Clear History" button.
Play a few tracks, then refresh the page — the data updates.

You can also fetch the UI as raw JSON to see what the frontend receives:

```bash
curl http://localhost:9000/api/plugins/nowplaying/ui
```

The response is a JSON tree of `{"type": "...", "props": {...}, "children": [...]}`
objects — the frontend maps each `type` to a Svelte component and renders
it recursively.

#### Part B: Settings Form with Tabs

Now let's add a second tab with a settings form. This shows the real
power of SDUI — interactive forms with validation, conditional fields,
and help text, all defined in Python:

```python
from resonance.ui import (
    Page, Tabs, Tab, Card, KeyValue, KVItem, StatusBadge, Button, Row,
    Table, TableColumn, Form, TextInput, NumberInput, Select, SelectOption,
    Toggle, Alert,
)

async def get_ui(ctx: PluginContext):
    total = _store.total if _store else 0
    recent = _store.entries[:5] if _store else []

    # ── Tab 1: Status (same as before) ────────────────────────
    status_tab = Tab(label="Status", icon="activity", children=[
        Card(title="Statistics", children=[
            StatusBadge(
                label=f"{total} tracks played",
                color="green" if total > 0 else "gray",
            ),
            KeyValue(items=[
                KVItem("Total Played", str(total)),
                KVItem("Recent Count", str(len(recent))),
            ]),
        ]),
        Row(children=[
            Button("Clear History", action="clear", style="danger", confirm=True),
        ]),
    ])

    if recent:
        status_tab.children.append(Table(
            title="Recent Tracks",
            columns=[
                TableColumn(key="player_id", label="Player"),
                TableColumn(key="timestamp", label="Time"),
                TableColumn(key="play_number", label="#"),
            ],
            rows=recent,
        ))

    # ── Tab 2: Settings Form ──────────────────────────────────
    max_entries = int(ctx.get_setting("max_entries"))
    show_timestamps = ctx.get_setting("show_timestamps")
    display_format = ctx.get_setting("display_format")

    settings_tab = Tab(label="Settings", icon="settings", children=[
        Form(action="save_settings", submit_label="Save Settings", children=[
            NumberInput(
                name="max_entries",
                label="Maximum History Entries",
                value=max_entries,
                min=20,
                max=2000,
                step=10,
                help_text="How many track plays to keep in the history file.",
            ),
            Toggle(
                name="show_timestamps",
                label="Show Timestamps",
                value=bool(show_timestamps),
                help_text="Display the time each track was played.",
            ),
            Select(
                name="display_format",
                label="Display Format",
                value=display_format,
                options=[
                    SelectOption(value="compact", label="Compact (player + number)"),
                    SelectOption(value="detailed", label="Detailed (player + title + time)"),
                    SelectOption(value="minimal", label="Minimal (number only)"),
                ],
                help_text="How recent tracks are shown in the Jive menu.",
            ),
            # This field only appears when "detailed" format is selected:
            NumberInput(
                name="max_title_length",
                label="Max Title Length",
                value=40,
                min=10,
                max=200,
                help_text="Truncate long titles to this many characters.",
            ).when("display_format", "detailed"),

            Alert(
                message="Changing the maximum entries will take effect on the next server restart.",
                severity="info",
            ),
        ]),
    ])

    return Page(
        title="Now Playing Stats",
        icon="activity",
        refresh_interval=10,
        components=[
            Tabs(tabs=[status_tab, settings_tab]),
        ],
    )
```

There is a lot going on here. Let's break it down:

| Concept                               | What It Does                                                                                                                                                                |
| ------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Tabs` + `Tab`                        | Creates a tab bar — clicking a tab switches content. Purely client-side, no server roundtrip.                                                                               |
| `Form(action="save_settings")`        | Wraps input fields. On submit, collects all field values into a dict and sends them to `handle_action("save_settings", {...})`.                                             |
| `NumberInput`                         | A number field with min/max/step validation. The frontend shows a range hint automatically.                                                                                 |
| `Toggle`                              | A boolean on/off switch.                                                                                                                                                    |
| `Select` + `SelectOption`             | A dropdown. `value` sets the default, `options` defines the choices.                                                                                                        |
| `help_text`                           | A gray hint shown below the field — disappears when a validation error is shown instead.                                                                                    |
| `.when("display_format", "detailed")` | **Conditional visibility.** This field only appears when the "Display Format" dropdown is set to "detailed". The frontend evaluates this live — no server roundtrip needed. |
| `Alert`                               | An info/warning/error/success message box.                                                                                                                                  |

Now update `handle_action()` to process the form data:

```python
async def handle_action(action: str, params: dict) -> dict:
    if action == "clear" and _store:
        _store.clear()
        return {"message": "History cleared"}

    if action == "save_settings":
        # params contains: {"max_entries": 100, "show_timestamps": True,
        #                    "display_format": "detailed", "max_title_length": 40}
        _ctx.set_settings(params)
        return {"message": "Settings saved"}

    return {"error": f"Unknown action: {action}"}
```

When the user clicks "Save Settings", the frontend collects the current
value of every input field inside the `Form`, puts them in a dict, and
sends them to your `handle_action()`. The field `name` prop becomes the
dict key, and the current value becomes the dict value. You then persist
them with `ctx.set_settings(params)` — the batch method validates all
values against the `plugin.toml` definitions and persists them
atomically. It returns the list of changed keys (which you can ignore
here). Note that `set_settings()` is a regular (synchronous) method,
not `async`, so call it **without** `await`.

> **Tip:** If you only need to update a single setting, use
> `ctx.set_setting(key, value)` instead. Both methods validate against
> the schema defined in `plugin.toml`.

> **Important:** You need to store the `ctx` reference in a module-level
> variable so `handle_action()` can access it. Add `_ctx` alongside
> `_store` and assign it in `setup()`:
>
> ```python
> _store: Any | None = None
> _ctx: Any | None = None   # PluginContext — set in setup()
>
> async def setup(ctx: PluginContext) -> None:
>     global _store, _ctx
>     _ctx = ctx
>     # ... rest of setup ...
> ```
>
> This is the same pattern the raopbridge plugin uses.

#### Try It Out

1. Start the server and open the web UI
2. Click "Now Playing Stats" in the sidebar
3. You see two tabs: **Status** and **Settings**
4. Click the **Settings** tab
5. Change "Display Format" to "Detailed" — watch the "Max Title Length"
   field appear (that is `visible_when` in action)
6. Change "Maximum History Entries" to `500` and click "Save Settings"
7. A green toast says "Settings saved"
8. Refresh the page — the value is still `500` (persisted)

#### What You Just Learned

You now know how to build a complete plugin UI page:

| Skill                    | What You Used                                                      |
| ------------------------ | ------------------------------------------------------------------ |
| Display read-only data   | `StatusBadge`, `KeyValue`, `Table`                                 |
| Group content            | `Card`, `Row`, `Tabs`                                              |
| Trigger server actions   | `Button` with `action` + `handle_action()`                         |
| Build input forms        | `Form` + `TextInput` / `NumberInput` / `Select` / `Toggle`         |
| Show contextual help     | `help_text` on form widgets                                        |
| Conditional fields       | `.when(field, value)` — live in the browser                        |
| Process form submissions | `handle_action(action, params)` — params is a dict of field values |
| Persist settings         | `ctx.set_setting(key, value)` inside the action handler            |
| Show feedback            | Return `{"message": "..."}` for success toast                      |
| Auto-refresh             | `Page(refresh_interval=10)` — re-fetches every 10s                 |

There are 20 widget types in total — including `Markdown` (renders
full GitHub-Flavored Markdown), `Modal` (dialog overlay with focus trap),
`Progress`, `Alert`, `Table` (with inline editing via `variant="editable"`
columns), and more.

For the full SDUI widget reference, see
[`PLUGIN_API.md` §19](PLUGIN_API.md#19-server-driven-ui-sdui).

For a complete real-world example — including a detailed before/after
comparison of migrating a hardcoded Svelte plugin to SDUI — see
[`PLUGIN_CASESTUDY.md`](PLUGIN_CASESTUDY.md).

---

## What Next? Build Your Own!

Congratulations — you have built a real plugin. You know how to register
commands, subscribe to events, persist data, show menus on hardware,
render a UI page in the browser, and write tests. That covers everything
you need to start building your own plugin.

**Need inspiration?** Here are some ideas the community has discussed:

- **RandomPlay / Don't Stop The Music** — auto-queue tracks when the
  playlist runs out
- **Equalizer / DSP UI** — per-player audio settings
- **Listening Statistics** — weekly/monthly play reports
- **Home Automation Bridge** — expose player state to Home Assistant
- **Lyrics Display** — fetch and show lyrics for the current track
- **Additional Streaming Services** — build a ContentProvider for your
  favourite source

If you build something, consider sharing it in the
[community plugins repo](https://github.com/endegelaende/resonance-community-plugins).
Your plugin gets its own entry in the Plugin Manager, installable by
anyone with one click. Contributions are always welcome and credited.

For a deep dive into a full-featured community plugin, read
[Build a Real Plugin — The raopbridge Story](PLUGIN_CASESTUDY.md).
It walks through every SDUI widget, action handler, and architectural
pattern using a real production plugin.

---

## Checklist: Starting Your Own Plugin

When you are ready to build your own plugin (not the tutorial plugin):

- [ ] `plugins/<name>/plugin.toml` with `name` and `version`
- [ ] `plugins/<name>/__init__.py` with `async def setup(ctx)`
- [ ] `from __future__ import annotations` in every `.py` file
- [ ] `TYPE_CHECKING` guard for all Resonance imports
- [ ] `ctx.subscribe()` instead of `event_bus.subscribe()` (auto-cleanup!)
- [ ] `ctx.ensure_data_dir()` before file I/O
- [ ] Atomic save (write-to-tmp → rename)
- [ ] Reset module-level state in `teardown()`
- [ ] Check `playlist_manager` for `None`
- [ ] Optional: `[ui]` section + `get_ui()` / `handle_action()` for a web UI page
- [ ] Tests in `tests/test_<name>_plugin.py`
- [ ] Start the server and test manually
- [ ] `python -m pytest` — all tests must pass

---

## Further Reading

| Document                                                                                                                  | What You Will Find                                                                 |
| ------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| [`PLUGIN_API.md`](PLUGIN_API.md)                                                                                          | Complete API reference — every method, every widget, every option (including SDUI) |
| [`PLUGIN_CASESTUDY.md`](PLUGIN_CASESTUDY.md)                                                                              | Deep dive: build a full plugin with tabs, tables, modals, forms — real-world code  |
| [`PLUGINS.md`](PLUGINS.md)                                                                                                | General overview for all audiences                                                 |
| [Community Plugins Repository](https://github.com/endegelaende/resonance-community-plugins)                               | Publish your plugin here — CI, releases, Plugin Manager integration                |
| `plugins/example/`                                                                                                        | Minimal "Hello World" template — start here for a quick skeleton                   |
| `plugins/favorites/`                                                                                                      | Full reference: commands, menus, persistence, LMS compatibility                    |
| `plugins/radio/`                                                                                                          | ContentProvider example: radio-browser.info, remote streaming, proxy               |
| `plugins/podcast/`                                                                                                        | ContentProvider example: RSS feeds, subscriptions, resume, artwork                 |
| [`raopbridge` community plugin](https://github.com/endegelaende/resonance-community-plugins/tree/main/plugins/raopbridge) | Complete SDUI reference: 5 tabs, device table, modals, forms, SSE live updates     |

---

_Happy building! If you get stuck, open an issue — the community is here to help._

_Last updated: March 2026_
