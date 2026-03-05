# Plugin Development — API Reference

Complete technical reference for plugin developers.
Requirements: Python 3.11+, basic asyncio knowledge.

General overview (no code): → [`PLUGINS.md`](PLUGINS.md)
Step-by-step tutorial: → [`PLUGIN_TUTORIAL.md`](PLUGIN_TUTORIAL.md)

---

## Table of Contents

1. [Quick Start](#1-quick-start)
2. [Manifest (`plugin.toml`)](#2-manifest-plugintoml)
3. [Entry Point (`__init__.py`)](#3-entry-point-initpy)
4. [PluginContext — Complete API](#4-plugincontext--complete-api)
5. [JSON-RPC Commands](#5-json-rpc-commands)
6. [Jive Menu System](#6-jive-menu-system)
7. [Event System](#7-event-system)
8. [HTTP Routes (FastAPI)](#8-http-routes-fastapi)
9. [Data Persistence](#9-data-persistence)
10. [Server Access (Read-Only)](#10-server-access-read-only)
11. [Testing](#11-testing)
12. [Best Practices](#12-best-practices)
13. [Debugging & Logging](#13-debugging--logging)
14. [Error Handling & Isolation](#14-error-handling--isolation)
15. [Known Limitations](#15-known-limitations)
16. [Content Providers](#16-content-providers)
17. [Reference Plugins](#17-reference-plugins)
18. [Plugin Settings & Management API](#18-plugin-settings--management-api)
19. [Server-Driven UI (SDUI)](#19-server-driven-ui-sdui)

---

## 1) Quick Start

Minimal file structure for a working plugin:

```
plugins/myplugin/
├── plugin.toml
└── __init__.py
```

**`plugin.toml`:**

```toml
[plugin]
name = "myplugin"
version = "0.1.0"
description = "My first plugin"
```

**`__init__.py`:**

```python
from __future__ import annotations
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from resonance.plugin import PluginContext
    from resonance.web.handlers import CommandContext


async def setup(ctx: PluginContext) -> None:
    ctx.register_command("myplugin.hello", cmd_hello)


async def cmd_hello(ctx: CommandContext, command: list[Any]) -> dict[str, Any]:
    return {"message": "Hello from myplugin!"}
```

Start the server — the plugin is active. Test it:

```json
{"method": "slim.request", "params": ["-", ["myplugin.hello"]]}
```

→ `{"result": {"message": "Hello from myplugin!"}}`

---

## 2) Manifest (`plugin.toml`)

Every plugin needs a `plugin.toml` in its plugin directory. The parser
reads the `[plugin]` table and creates a `PluginManifest` object.

### Fields

| Field | Required | Type | Description |
|---|---|---|---|
| `name` | ✅ | string | Unique identifier. Must match the directory name. Only `[a-z0-9_-]`. |
| `version` | ✅ | string | Semver version, e.g. `"1.0.0"`. |
| `description` | ❌ | string | One-line description. |
| `author` | ❌ | string | Author or maintainer. |
| `min_resonance_version` | ❌ | string | Minimum server version (informational, not enforced). |
| `category` | ❌ | string | Optional category for UIs/repository (`radio`, `podcast`, `tools`, ...). |
| `icon` | ❌ | string | Optional icon key/URL hint for UIs. |

### UI Page (`[ui]`)

If your plugin wants to expose a page in the web UI sidebar, add a `[ui]`
section to your manifest. See [§19 Server-Driven UI](#19-server-driven-ui-sdui) for the full guide.

| Field | Required | Type | Description |
|---|---|---|---|
| `enabled` | ✅ | bool | Set `true` to enable the plugin UI page. |
| `sidebar_label` | ❌ | string | Label shown in the sidebar (defaults to plugin name). |
| `sidebar_icon` | ❌ | string | Lucide icon name for the sidebar entry (e.g. `"cast"`, `"radio"`, `"radar"`). Falls back to the plugin's `icon` field, then `"plug"`. |

```toml
[ui]
enabled = true
sidebar_label = "AirPlay"
sidebar_icon = "cast"
```

### Declarative Settings (`[settings.<key>]`)

Plugins can declare settings directly in `plugin.toml`. Each setting is a
table under `[settings.<key>]` and is parsed into `SettingDefinition`.

Supported setting types:
- `string`
- `int`
- `float`
- `bool`
- `select` (requires `options = [...]`)

Common setting fields:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `type` | string | `"string"` | Value type |
| `label` | string | key | Display label |
| `description` | string | `""` | Help text |
| `default` | type-dependent | varies | Default value |
| `required` | bool | `false` | Reject empty value |
| `secret` | bool | `false` | Mask value in external responses |
| `restart_required` | bool | `false` | Marks update as restart-relevant |
| `order` | int | `0` | UI sort order |
| `min` / `max` | number | `null` | Numeric range |
| `min_length` / `max_length` | int | `null` | String length bounds |
| `pattern` | string | `null` | Regex for strings |
| `options` | list[string] | `[]` | Allowed values for `select` |

### Full Example

```toml
[plugin]
name = "favorites"
version = "1.0.0"
description = "Favorites management — LMS-compatible favorites with hierarchical folders"
author = "Resonance"
min_resonance_version = "0.1.0"
category = "library"
icon = "star"

[settings.sort_mode]
type = "select"
label = "Sort mode"
default = "name"
options = ["name", "recent"]

[settings.api_key]
type = "string"
label = "API key"
secret = true
required = true
```

### Internal Data Model

```python
@dataclass(frozen=True)
class PluginManifest:
    name: str
    version: str
    description: str = ""
    author: str = ""
    min_resonance_version: str = ""
    category: str = ""
    icon: str = ""
    plugin_type: str = "core"     # set by PluginManager (core/community)
    plugin_dir: Path = ...          # Set by loader, not from TOML
    settings_defs: tuple[SettingDefinition, ...] = ()
    ui_enabled: bool = False        # from [ui].enabled
    ui_sidebar_label: str = ""      # from [ui].sidebar_label
    ui_sidebar_icon: str = ""       # from [ui].sidebar_icon
```

**Error handling:** If `name` or `version` is missing, the plugin is
skipped and a warning is logged. Invalid settings definitions are logged
and skipped individually. Other plugins continue to load normally.

---

## 3) Entry Point (`__init__.py`)

The plugin module **must** export a `setup()` function.
`teardown()` is **optional**.

### `setup(ctx: PluginContext) -> None`

- Called on server startup (after core initialization).
- Use this to: register commands, menus, events, routes; load data.
- Must be `async`.
- May raise exceptions — the plugin will not be started and any
  registrations already made are automatically rolled back.

### `teardown(ctx: PluginContext) -> None`

- Called on server shutdown (before core teardown).
- Use this to: persist state, release resources.
- **Not needed** for deregistration — all registrations are
  automatically removed after `teardown()`.
- Order: Plugins are stopped in **reverse start order** (LIFO).

### Complete Skeleton

```python
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from resonance.core.events import Event
    from resonance.plugin import PluginContext
    from resonance.web.handlers import CommandContext

logger = logging.getLogger(__name__)


async def setup(ctx: PluginContext) -> None:
    """Called on server startup."""

    # 1) Commands
    ctx.register_command("myplugin.action", cmd_action)

    # 2) Menus
    ctx.register_menu_node(
        node_id="myPlugin",
        parent="home",
        text="My Plugin",
        weight=50,
    )

    # 3) Events
    await ctx.subscribe("player.track_started", on_track_started)

    # 4) Load data
    data_dir = ctx.ensure_data_dir()
    # ... load files from data_dir ...

    logger.info("My plugin started")


async def teardown(ctx: PluginContext) -> None:
    """Called on server shutdown. Optional."""
    # Save state if needed
    logger.info("My plugin stopped")


# --- Command Handler ---

async def cmd_action(ctx: CommandContext, command: list[Any]) -> dict[str, Any]:
    """Handler for the 'myplugin.action' command."""
    return {"status": "ok"}


# --- Event Handler ---

async def on_track_started(event: Event) -> None:
    """React to started tracks."""
    logger.info("Track started: %s", event.to_dict())
```

### Submodules

Plugins can use multiple `.py` files. Relative imports work:

```python
# In __init__.py:
from .store import MyStore
from .helpers import parse_something
```

Structure:

```
plugins/myplugin/
├── plugin.toml
├── __init__.py      # setup() / teardown()
├── store.py         # Data persistence
└── helpers.py       # Utility functions
```

---

## 4) PluginContext — Complete API

The `PluginContext` is created per plugin by the `PluginManager` and
passed to `setup()` / `teardown()`. It is the **only** gateway to
server functionality.

### Identity

| Attribute | Type | Description |
|---|---|---|
| `plugin_id` | `str` | Name from manifest (e.g. `"favorites"`) |
| `data_dir` | `Path` | `data/plugins/<plugin_id>/` |

### Server Access (read-only)

| Attribute | Type | Description |
|---|---|---|
| `event_bus` | `EventBus` | Pub/sub event system |
| `music_library` | `MusicLibrary` | Query the music library |
| `player_registry` | `PlayerRegistry` | Connected players |
| `playlist_manager` | `PlaylistManager \| None` | Playlist access |

### Registration Methods

| Method | Signature | Description |
|---|---|---|
| `register_command` | `(name: str, handler: CommandHandler) -> None` | JSON-RPC command |
| `unregister_command` | `(name: str) -> None` | Remove a command |
| `register_menu_node` | `(node_id, parent, text, weight, **kwargs) -> None` | Jive menu node |
| `register_menu_item` | `(node_id: str, item: dict) -> None` | Jive menu entry |
| `register_route` | `(router: APIRouter) -> None` | FastAPI router |
| `register_content_provider` | `(provider_id: str, provider: ContentProvider) -> None` | External audio source (Radio, Podcast, …) |
| `unregister_content_provider` | `(provider_id: str) -> None` | Remove a content provider |
| `subscribe` | `async (event_type: str, handler) -> None` | Event with auto-cleanup |
| `register_ui_handler` | `(handler: Callable) -> None` | SDUI page builder ([§19](#19-server-driven-ui-sdui)) |
| `register_action_handler` | `(handler: Callable) -> None` | SDUI action dispatcher ([§19](#19-server-driven-ui-sdui)) |

### Utility Functions

| Method | Signature | Description |
|---|---|---|
| `ensure_data_dir` | `() -> Path` | Create/return data directory |
| `get_setting` | `(key: str) -> Any` | Read one setting (falls back to default) |
| `set_setting` | `(key: str, value: Any) -> None` | Validate and persist one setting |
| `set_settings` | `(values: dict[str, Any]) -> list[str]` | Validate and persist multiple settings atomically |
| `get_all_settings` | `() -> dict[str, Any]` | All settings with defaults |
| `get_all_settings_masked` | `() -> dict[str, Any]` | Same values, but secrets masked |
| `get_settings_definitions` | `() -> list[dict[str, Any]]` | Serialized setting definitions for APIs |
| `has_settings` | `@property -> bool` | Whether the plugin declared settings |

### Cleanup Guarantee

**Everything** registered via `PluginContext` is automatically removed
after `teardown()`:

- Commands → `unregister_command()` for each registered command
- Menus → Entries with `_plugin_id` are removed from the global list
- Events → `event_bus.unsubscribe()` for each subscribed handler
- Content providers → `unregister_content_provider()` for each registered provider
- UI handlers → `_ui_handler` and `_action_handler` are set to `None`
- Routes → *Note: FastAPI routes cannot currently be cleanly removed (framework limitation)*

**Manual cleanup in `teardown()` is not needed** — only for your own
resources (open files, network connections, etc.).

---

## 5) JSON-RPC Commands

### Handler Signature

```python
async def my_handler(
    ctx: CommandContext,
    command: list[Any],
) -> dict[str, Any]:
    ...
```

| Parameter | Type | Description |
|---|---|---|
| `ctx` | `CommandContext` | Server context (player ID, library, registry, …) |
| `command` | `list[Any]` | Raw command array, e.g. `["myplugin", "action", "key:value"]` |
| **Return** | `dict[str, Any]` | Result dict, sent as `result` in the JSON-RPC response |

### CommandContext — Available Fields

```python
@dataclass
class CommandContext:
    player_id: str                              # MAC or "-" for server commands
    music_library: MusicLibrary                 # Library
    player_registry: PlayerRegistry             # Players
    playlist_manager: PlaylistManager | None    # Playlists
    streaming_server: StreamingServer | None    # Streaming
    slimproto: SlimprotoServer | None           # Slimproto
    artwork_manager: ArtworkManager | None      # Cover art
    plugin_manager: PluginManager | None        # Plugin registry/state
    plugin_installer: PluginInstaller | None    # ZIP installer
    plugin_repository: PluginRepository | None  # Repository client
    server_host: str                            # Server IP
    server_port: int                            # Server port (default: 9000)
    server_uuid: str                            # Server UUID
```

### Registration

```python
ctx.register_command("myplugin.hello", cmd_hello)
```

**Rules:**
- The command name must be **unique**.
- Built-in commands (`play`, `pause`, `status`, …) **cannot** be overridden.
- `register_command()` raises `RuntimeError` on duplicates.
- Naming convention: `<plugin_name>.<action>` for plugin-specific commands,
  or a single name for LMS-compatible commands (e.g. `"favorites"`).

### Sub-Command Dispatch

For commands with sub-commands (like `favorites items`, `favorites add`)
register **one** handler and dispatch internally:

```python
ctx.register_command("mycommand", cmd_mycommand)

async def cmd_mycommand(ctx: CommandContext, command: list[Any]) -> dict[str, Any]:
    sub = str(command[1]).lower() if len(command) > 1 else "default"

    match sub:
        case "list":   return await _handle_list(ctx, command)
        case "add":    return await _handle_add(ctx, command)
        case "delete": return await _handle_delete(ctx, command)
        case _:        return {"error": f"Unknown sub-command: {sub}"}
```

### Parameter Parsing

LMS clients send parameters as `key:value` strings in the command array.
Some clients (Cometd) send a `dict` instead.

Standard parsing pattern:

```python
def _parse_tagged(command: list[Any], start: int = 1) -> dict[str, str]:
    """Parse key:value params and dict elements from position 'start'."""
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

You can also use the server helper functions:

```python
from resonance.web.jsonrpc_helpers import parse_tagged_params, parse_start_items

tagged = parse_tagged_params(command[2:])    # key:value params
start, count = parse_start_items(command)    # Positional start/count params
```

### Paginated Responses

For list commands (with `<start>` and `<count>`):

```python
async def cmd_list(ctx: CommandContext, command: list[Any]) -> dict[str, Any]:
    start, count = _parse_start_count(command)
    tagged = _parse_tagged(command, start=2)
    is_menu = tagged.get("menu") == "1"

    all_items = get_all_items()
    page = all_items[start : start + count]

    loop_key = "item_loop" if is_menu else "loop"

    return {
        "count": len(all_items),
        "offset": start,
        loop_key: [build_item(item) for item in page],
    }
```

**Convention:** `item_loop` for Jive menu responses, `loop` for CLI responses.

### Returning Errors

```python
return {"error": "Something went wrong"}
```

There is no special exception handling — simply return a dict with `"error"`.
Uncaught exceptions are caught by the JsonRpcHandler and logged as internal errors.

---

## 6) Jive Menu System

Squeezebox Touch/Radio/Boom/Controller use a tree-based menu system.
Plugins can attach nodes and entries to this tree.

### Concepts

| Concept | Description |
|---|---|
| **Node** | Menu folder, contains children. Has `isANode: 1`. |
| **Item** | Menu entry with actions (go, play, add, do). |
| **Weight** | Sort weight — lower = higher in the list. |
| **Parent** | ID of the parent node. `"home"` = home menu. |
| **Actions** | Dict with `go`/`play`/`add`/`do`/`more` — determines what happens. |

### Registering Menu Nodes

```python
ctx.register_menu_node(
    node_id="myPlugin",              # Unique ID
    parent="home",                   # Under which node?
    text="My Plugin",                # Display text
    weight=50,                       # Sort order
)
```

Nodes with actions (e.g. "navigate to a list when tapped"):

```python
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
```

### Registering Menu Items

```python
ctx.register_menu_item("myPlugin", {
    "text": "Do Something",
    "id": "myPlugin_doSomething",
    "actions": {
        "do": {
            "player": 0,
            "cmd": ["myplugin", "dosomething"],
        },
    },
})
```

### Standard Jive Weights (LMS Reference)

| Weight | Entry |
|---|---|
| 11 | My Music |
| 35 | Audio Settings |
| 55 | Favorites |
| 100 | Player Power |
| 1000 | Example Plugin |
| 1005 | Settings |

### Action Types

```python
# go — Navigation (opens new menu)
"go": {"cmd": ["myplugin", "items"], "params": {"menu": 1}}

# play — Play immediately
"play": {"player": 0, "cmd": ["favorites", "playlist", "play"], "params": {"item_id": "0"}}

# add — Add to playlist
"add": {"player": 0, "cmd": ["favorites", "playlist", "add"], "params": {"item_id": "0"}}

# do — Execute immediately without navigation
"do": {"player": 0, "cmd": ["power", "0"]}
```

### Response Format for Menu Commands

When a client sends `menu:1`, it expects a Jive-compatible response:

```python
return {
    "count": len(items),
    "offset": 0,
    "item_loop": [
        {
            "text": "Song Title",
            "type": "audio",
            "hasitems": 0,
            "icon": "http://server/art/123",
            "presetParams": {
                "favorites_url": "file:///music/song.flac",
                "favorites_title": "Song Title",
                "favorites_type": "audio",
            },
            "actions": {
                "play": {"player": 0, "cmd": [...], "params": {...}},
                "add":  {"player": 0, "cmd": [...], "params": {...}},
            },
        },
        {
            "text": "Folder Name",
            "type": "folder",
            "hasitems": 1,
            "actions": {
                "go": {"cmd": [...], "params": {...}},
            },
        },
    ],
    "base": {
        "actions": {
            "go": {"cmd": [...], "itemsParams": "commonParams"},
        },
    },
}
```

### Confirmation Menus (jivefavorites Pattern)

For destructive actions (delete, add) you can return a
confirmation menu:

```python
return {
    "count": 2,
    "offset": 0,
    "item_loop": [
        {
            "text": "Cancel",
            "actions": {"go": {"player": 0, "cmd": ["jiveblankcommand"]}},
            "nextWindow": "parent",
        },
        {
            "text": "Delete Song",
            "actions": {"go": {"player": 0, "cmd": ["myplugin", "delete"], "params": {...}}},
            "nextWindow": "grandparent",
        },
    ],
}
```

`nextWindow` values: `"parent"`, `"grandparent"`, `"home"`, `"nowPlaying"`.

---

## 7) Event System

### Subscription

```python
await ctx.subscribe("player.track_started", on_track_started)
```

`ctx.subscribe()` tracks the handler automatically. On plugin teardown
`event_bus.unsubscribe()` is called — **no manual cleanup needed**.

Alternative (without auto-cleanup, not recommended):

```python
await ctx.event_bus.subscribe("player.track_started", on_track_started)
```

### Handler Signature

```python
async def on_track_started(event: Event) -> None:
    player_id = event.player_id  # Field depends on event type
    logger.info("Track started on %s", player_id)
```

Handlers **must** be `async`. Exceptions in handlers are logged
but **do not** break event processing for other handlers.

### Available Event Types

| Event String | Class | Fields | When |
|---|---|---|---|
| `server.started` | `ServerStartedEvent` | — | Server fully initialized |
| `server.stopping` | `ServerStoppingEvent` | — | Shutdown begins |
| `player.connected` | `PlayerConnectedEvent` | `player_id`, `name`, `model` | Player connects |
| `player.disconnected` | `PlayerDisconnectedEvent` | `player_id` | Player disconnects |
| `player.status` | `PlayerStatusEvent` | `player_id`, `state`, `volume`, `muted`, `elapsed_*`, `duration`, … | Status change |
| `player.track_started` | `PlayerTrackStartedEvent` | `player_id`, `stream_generation` | Track playback started (STMs) |
| `player.track_finished` | `PlayerTrackFinishedEvent` | `player_id`, `stream_generation` | Track playback finished |
| `player.decode_ready` | `PlayerDecodeReadyEvent` | `player_id`, `stream_generation` | Decoder ready for next track (STMd) |
| `player.playlist` | `PlayerPlaylistEvent` | `player_id`, `action`, `index`, `count` | Playlist changed |
| `library.scan` | `LibraryScanEvent` | `status`, `scanned`, `total`, `current_path`, `error` | Library scan |

### Wildcard Subscriptions

```python
await ctx.subscribe("player.*", on_any_player_event)    # All player.* events
await ctx.subscribe("library.*", on_library_event)       # All library.* events
await ctx.subscribe("*", on_any_event)                   # All events
```

### Publishing Custom Events

```python
from dataclasses import dataclass, field
from resonance.core.events import Event

@dataclass
class MyPluginEvent(Event):
    event_type: str = field(default="myplugin.something_happened", init=False)
    detail: str = ""

# Publish:
await ctx.event_bus.publish(MyPluginEvent(detail="Hello"))
```

Other plugins (or your own) can then subscribe to
`"myplugin.something_happened"`.

---

## 8) HTTP Routes (FastAPI)

Plugins can register custom REST endpoints:

```python
from fastapi import APIRouter

router = APIRouter(prefix="/api/myplugin", tags=["myplugin"])

@router.get("/status")
async def get_status():
    return {"status": "running"}

@router.post("/action")
async def do_action(body: dict):
    return {"result": "done"}


async def setup(ctx: PluginContext) -> None:
    ctx.register_route(router)
```

The routes are then available at `http://<server>:9000/api/myplugin/status`.

**Note:** FastAPI routers currently **cannot** be cleanly removed on
plugin teardown (framework limitation). For most use cases this is not
a problem since plugins are rarely loaded/unloaded at runtime.

---

## 9) Data Persistence

### Data Directory

```python
data_dir = ctx.ensure_data_dir()
# → data/plugins/<plugin_id>/
```

`ensure_data_dir()` creates the directory if it does not exist
and returns the `Path`.

### Recommended: JSON File with Atomic Write

```python
import json
from pathlib import Path

class MyStore:
    def __init__(self, data_dir: Path) -> None:
        self._file = data_dir / "state.json"
        self._data: dict = {}

    def load(self) -> None:
        if self._file.is_file():
            self._data = json.loads(self._file.read_text(encoding="utf-8"))

    def save(self) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._file.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(self._file)    # Atomic rename
```

**Why atomic write?** A power failure during `write_text()` can leave
a half-written file. `write → rename` is atomic on most file systems —
either the old or the new version is fully present.

### Reference: FavoritesStore

The Favorites plugin (`plugins/favorites/store.py`) is a complete
reference implementation with:

- Hierarchical data structure
- URL index for O(1) lookups
- Atomic save
- Version counter
- Fault tolerance for corrupt files

---

## 10) Server Access (Read-Only)

### MusicLibrary

```python
db = ctx.music_library._db

# Search tracks
tracks = await db.search_tracks("Beethoven", limit=50, offset=0)

# Get album
album = await db.get_album(album_id=42)

# All genres
genres = await db.list_genres(limit=500, offset=0)
```

### PlayerRegistry

```python
# All connected players
players = await ctx.player_registry.get_all()

# Player by MAC
player = await ctx.player_registry.get_by_mac("aa:bb:cc:dd:ee:ff")
if player:
    print(player.name, player.model, player.device_capabilities)
```

### PlaylistManager

```python
if ctx.playlist_manager:
    playlist = ctx.playlist_manager.get("aa:bb:cc:dd:ee:ff")
    current_track = playlist.current_track
    total_tracks = len(playlist)
```

**Caution:** `playlist_manager` can be `None` (e.g. in tests).
Always check!

---

## 11) Testing

### Test Setup

Plugin tests live in `tests/test_<plugin_name>_plugin.py` and use
`pytest` + `pytest-asyncio`.

Basic structure:

```python
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture()
def my_env(tmp_path):
    """Set up plugin state for tests."""
    import plugins.myplugin as mod
    from plugins.myplugin.store import MyStore

    store = MyStore(tmp_path)
    store.load()
    mod._store = store
    mod._event_bus = MagicMock()
    mod._event_bus.publish = AsyncMock()

    yield store, mod

    mod._store = None
    mod._event_bus = None
```

### Fake CommandContext

```python
class _FakeCommandContext:
    def __init__(self, player_id="-", music_library=None, **kwargs):
        self.player_id = player_id
        self.music_library = music_library
        self.player_registry = kwargs.get("player_registry") or AsyncMock()
        self.playlist_manager = kwargs.get("playlist_manager")
        self.streaming_server = None
        self.slimproto = None
        self.artwork_manager = None
        self.server_host = "127.0.0.1"
        self.server_port = 9000
        self.server_uuid = "test-uuid"

        if kwargs.get("player_registry") is None:
            self.player_registry.get_by_mac = AsyncMock(return_value=None)
```

### Testing Command Handlers

```python
@pytest.mark.asyncio
async def test_add(self, my_env):
    store, _ = my_env
    from plugins.myplugin import cmd_mycommand

    ctx = _FakeCommandContext()
    result = await cmd_mycommand(ctx, ["mycommand", "add", "key:value"])

    assert "error" not in result
    assert store.count == 1
```

### Patching Late Imports

When a handler uses a server module import (`from resonance.web.handlers.playlist import cmd_playlist`),
you must patch the import at the **source location**:

```python
# ✅ Correct — patch at the source location
with patch("resonance.web.handlers.playlist.cmd_playlist", new_callable=AsyncMock):
    ...

# ❌ Wrong — the attribute does not exist on the plugin module
with patch("plugins.myplugin.cmd_playlist", new_callable=AsyncMock):
    ...
```

### Testing Lifecycle

```python
@pytest.mark.asyncio
async def test_setup_teardown(self, tmp_path):
    import plugins.myplugin as mod

    ctx = MagicMock()
    ctx.plugin_id = "myplugin"
    ctx.ensure_data_dir = MagicMock(return_value=tmp_path)
    ctx.event_bus = MagicMock()
    ctx.register_command = MagicMock()
    ctx.register_menu_node = MagicMock()

    await mod.setup(ctx)
    assert mod._store is not None

    await mod.teardown(ctx)
    assert mod._store is None
```

### Testing Event Notifications

```python
@pytest.mark.asyncio
async def test_mutation_publishes_event(self, my_env):
    _, mod = my_env
    from plugins.myplugin import cmd_mycommand

    ctx = _FakeCommandContext()
    await cmd_mycommand(ctx, ["mycommand", "add", "key:value"])

    mod._event_bus.publish.assert_called()
```

### Test Conventions

| Convention | Description |
|---|---|
| File name | `tests/test_<plugin>_plugin.py` |
| Classes | `TestClassName` — group by feature/handler |
| Fixture | `<plugin>_env` — sets up module state, cleans up after test |
| Assertions | Always check `"error" not in result` or `"error" in result` |

---

## 12) Best Practices

### Do

| Rule | Reason |
|---|---|
| ✅ Always use `from __future__ import annotations` | Avoids circular imports with type hints |
| ✅ `TYPE_CHECKING` guard for imports | Faster import, no runtime dependencies |
| ✅ `ctx.subscribe()` instead of `event_bus.subscribe()` | Auto-cleanup on teardown |
| ✅ `ensure_data_dir()` before file I/O | Directory is guaranteed to exist |
| ✅ Atomic write (tmp + rename) | No data loss on crash |
| ✅ Logging with `logger = logging.getLogger(__name__)` | Logger name = module name → filterable |
| ✅ Check `playlist_manager` for `None` | Not always available (tests!) |
| ✅ Parameter parsing for both `str` and `dict` | Cometd sends dicts, CLI sends `key:value` |
| ✅ Write your own tests (target: 90%+ coverage) | Regression protection |

### Don't

| Anti-Pattern | Problem |
|---|---|
| ❌ Override built-in commands | `RuntimeError` — protection is intentional |
| ❌ Use `event_bus.subscribe()` directly | No auto-cleanup → memory leak |
| ❌ Synchronous I/O in handlers | Blocks the event loop → server stalls |
| ❌ Global variables without reset in `teardown()` | State leaks between server restarts |
| ❌ Not catching exceptions in `teardown()` | Can block cleanup of other plugins |
| ❌ Directly importing and mutating server internals | Breaks on refactors; only use `PluginContext` API |

### Naming Conventions

| Element | Convention | Example |
|---|---|---|
| Plugin directory | `lowercase`, `[a-z0-9_-]` | `plugins/my_radio/` |
| Command (plugin-specific) | `<plugin>.<action>` | `"myradio.search"` |
| Command (LMS-compatible) | Single name | `"favorites"` |
| Menu node ID | camelCase | `"myRadio"` |
| Event type | `<namespace>.<event>` | `"myradio.station_changed"` |

---

## 13) Debugging & Logging

### Setting Up a Logger

```python
import logging
logger = logging.getLogger(__name__)
```

The logger name is automatically `resonance_plugins.<plugin_name>`.

### Log Levels

```python
logger.debug("Detailed info: %s", data)             # Only at DEBUG
logger.info("Plugin started — %d items", count)      # Normal operational info
logger.warning("Unexpected state: %s", msg)           # Not an error yet
logger.error("Operation failed: %s", exc)             # Error, plugin continues
```

### Checking Plugin Start Logs

On server startup the PluginManager logs:

```
INFO  Discovered plugin: favorites v1.0.0 (plugins/favorites)
INFO  Loaded plugin: favorites v1.0.0
INFO  Started plugin: favorites v1.0.0 — PluginContext(plugin_id='favorites', commands=2, menu_nodes=1, menu_items=0)
```

If a plugin fails to start:

```
ERROR Failed to start plugin 'myplugin': <Exception details>
```

### Testing via JSON-RPC

```bash
curl -X POST http://localhost:9000/jsonrpc.js \
  -H "Content-Type: application/json" \
  -d '{"id":1,"method":"slim.request","params":["-",["myplugin.hello"]]}'
```

Or with the RPC test script:

```bash
python scripts/rpc_test_console.py
> myplugin.hello
```

---

## 14) Error Handling & Isolation

### Setup Errors

If `setup()` raises an exception:

1. All **already-made** registrations (commands, menus, events)
   are automatically rolled back (`ctx._cleanup()`).
2. The error is logged.
3. **Other plugins continue to start normally.**

```
ERROR Failed to start plugin 'broken_plugin': ValueError: something went wrong
WARNING Cleanup after failed start of 'broken_plugin': ...
INFO  Started plugin: good_plugin v1.0.0 — ...
```

### Handler Errors

If a command handler raises an exception:

1. The exception is logged.
2. The client receives a JSON-RPC error response.
3. **Other handlers and plugins are not affected.**

### Event Handler Errors

If an event handler raises an exception:

1. The exception is logged.
2. **Other handlers for the same event are still called.**

### Teardown Errors

If `teardown()` raises an exception:

1. The error is logged.
2. Cleanup (`ctx._cleanup()`) is **still** executed.
3. **Other plugins continue to be stopped.**

---

## 15) Known Limitations

| Limitation | Details | Planned Solution |
|---|---|---|
| No hot-reload | Plugins cannot be loaded/unloaded at runtime | Server restart required |
| No sandbox/security | Plugins run in the same process, full Python access | Accepted (same as LMS) |
| FastAPI routes not removable | `register_route()` is permanent | Framework limitation |
| Enable/disable/install is restart-oriented | State changes set `restart_required`; running plugin instances are not hot-swapped | Explicit restart workflow in UI/API |
| `playlist_manager` optional | Can be `None` in tests | Always check |
| Content provider commands are per-plugin | No generic `content.browse` — each plugin defines its own commands (e.g. `radio items`) | By design |

---

## 16) Content Providers

Plugins can supply external audio sources (Internet Radio, Podcasts,
streaming services) by implementing the `ContentProvider` abstract base
class and registering it via `PluginContext.register_content_provider()`.

### Overview

```
Plugin                    Registry                   StreamingServer
──────                    ────────                   ───────────────
setup():
  ctx.register_content_provider("radio", provider)
                      ─►  providers["radio"] = provider

User browses "Radio":
  registry.browse("radio", "/")
                      ─►  provider.browse("/")
                      ◄─  [BrowseItem, ...]

User plays item:
  info = registry.get_stream_info("radio", item_id)
                      ─►  provider.get_stream_info(item_id)
                      ◄─  StreamInfo(url=..., ...)

  streaming_server.queue_url(mac, info.url, ...)
                                                ─►  proxy stream to player
```

### ContentProvider ABC

Import: `from resonance.content_provider import ContentProvider`

| Method / Property | Signature | Description |
|---|---|---|
| `name` | `@property -> str` | Human-readable name (e.g. `"Internet Radio"`) |
| `icon` | `@property -> str \| None` | Optional icon URL for top-level menu |
| `browse` | `async (path: str = "") -> list[BrowseItem]` | Browse content tree (empty = root) |
| `search` | `async (query: str) -> list[BrowseItem]` | Search for items by text |
| `get_stream_info` | `async (item_id: str) -> StreamInfo \| None` | Resolve item to stream URL |
| `on_stream_started` | `async (item_id: str, player_mac: str) -> None` | Called when playback starts (optional) |
| `on_stream_stopped` | `async (item_id: str, player_mac: str) -> None` | Called when playback stops (optional) |

All methods are `async` — providers are expected to make HTTP calls
to external APIs.

### StreamInfo

Import: `from resonance.content_provider import StreamInfo`

Frozen dataclass returned by `get_stream_info()`:

| Field | Type | Default | Description |
|---|---|---|---|
| `url` | `str` | *(required)* | Direct audio stream URL (HTTP or HTTPS) |
| `content_type` | `str` | `"audio/mpeg"` | MIME type of the stream |
| `title` | `str` | `""` | Display title |
| `artist` | `str` | `""` | Artist or show name |
| `album` | `str` | `""` | Album or category |
| `artwork_url` | `str \| None` | `None` | Cover art URL |
| `duration_ms` | `int` | `0` | Duration (0 = live/unknown) |
| `bitrate` | `int` | `0` | Bitrate in kbps |
| `is_live` | `bool` | `False` | `True` for infinite live streams |
| `extra` | `dict` | `{}` | Provider-specific metadata |

### BrowseItem

Import: `from resonance.content_provider import BrowseItem`

Frozen dataclass representing one entry in a browse tree:

| Field | Type | Default | Description |
|---|---|---|---|
| `id` | `str` | *(required)* | Provider-scoped unique ID |
| `title` | `str` | *(required)* | Display text |
| `type` | `str` | `"audio"` | `"audio"`, `"folder"`, or `"search"` |
| `url` | `str \| None` | `None` | Hint URL (authoritative URL from `get_stream_info()`) |
| `icon` | `str \| None` | `None` | Icon / artwork URL |
| `subtitle` | `str \| None` | `None` | Secondary text |
| `items` | `list[BrowseItem] \| None` | `None` | Pre-loaded children (for small static sub-menus) |
| `extra` | `dict` | `{}` | Provider-specific data |

### Registration

```python
from resonance.content_provider import ContentProvider, BrowseItem, StreamInfo
from resonance.plugin import PluginContext

class MyRadioProvider(ContentProvider):
    @property
    def name(self) -> str:
        return "My Radio"

    async def browse(self, path: str = "") -> list[BrowseItem]:
        if path == "":
            return [
                BrowseItem(id="jazz", title="Jazz", type="folder"),
                BrowseItem(id="rock", title="Rock", type="folder"),
                BrowseItem(id="search", title="Search", type="search"),
            ]
        # ... fetch stations for category
        return []

    async def search(self, query: str) -> list[BrowseItem]:
        # ... search external API
        return []

    async def get_stream_info(self, item_id: str) -> StreamInfo | None:
        # ... resolve station ID to stream URL
        return StreamInfo(
            url="https://stream.example.com/live.mp3",
            content_type="audio/mpeg",
            title="Jazz FM",
            is_live=True,
            bitrate=128,
        )

async def setup(ctx: PluginContext) -> None:
    ctx.register_content_provider("myradio", MyRadioProvider())
```

### How URL Proxy Streaming Works

Squeezebox hardware (SB2, SB3, Boom, Classic) cannot handle HTTPS and
has limited HTTP capabilities. When a content provider returns a stream
URL, the server acts as a transparent proxy:

1. Plugin resolves item → `StreamInfo(url="https://...")`
2. Handler calls `streaming_server.queue_url(mac, url, ...)`
3. Player connects to `/stream.mp3?player=MAC` (local HTTP)
4. Streaming route fetches the remote URL via `httpx` and relays chunks
5. ICY metadata (Shoutcast/Icecast title changes) is automatically
   stripped from the byte stream and logged

### PlaylistTrack for Remote Sources

When a content provider's item is played, a `PlaylistTrack` is created
with `from_url()`:

```python
from resonance.core.playlist import PlaylistTrack

track = PlaylistTrack.from_url(
    "https://radio.example.com/station/123",
    title="Jazz FM",
    artist="Jazz FM Network",
    source="radio",
    stream_url="https://cdn.example.com/stream.aac",
    external_id="radio:s123456",
    artwork_url="https://img.example.com/logo.png",
    content_type="audio/aac",
    bitrate=128,
    is_live=True,
)

# track.is_remote == True
# track.effective_stream_url == "https://cdn.example.com/stream.aac"
```

Remote tracks are persisted in playlist JSON files and survive server
restarts. Backward compatibility with playlists from older Resonance
versions is maintained (remote fields default to local-track values).

### ContentProviderRegistry

The registry is a server-level singleton. Plugins do not interact with
it directly — use `PluginContext.register_content_provider()` instead.

| Method | Description |
|---|---|
| `browse(provider_id, path)` | Delegate to provider with error handling |
| `search(provider_id, query)` | Delegate to provider with error handling |
| `get_stream_info(provider_id, item_id)` | Delegate to provider with error handling |
| `search_all(query)` | Search across all providers, returns `dict[provider_id, list[BrowseItem]]` |
| `list_providers()` | All `(id, provider)` pairs |
| `provider_ids` | List of registered IDs |

All wrapper methods catch exceptions from providers and return empty
results / `None` — a failing provider does not crash the server.

---

## 17) Reference Plugins

### Example Plugin (`plugins/example/`)

Minimal demo of all API features:

- 1 command (`example.hello`)
- 1 menu node
- 2 event subscriptions
- ~130 lines

**Ideal as a copy-paste template.**

### Favorites Plugin (`plugins/favorites/`)

Complete, production-ready plugin:

- 2 commands (`favorites`, `jivefavorites`) with 8+ sub-commands
- 1 menu node with Jive actions
- Event notification (`favorites.changed`)
- JSON store with hierarchical folders
- Atomic write, URL index, search filter
- ~1400 lines of code
- 152 tests

**Ideal as a reference for complex plugins.**

### Radio Plugin (`plugins/radio/`)

First ContentProvider plugin — Internet Radio via radio-browser.info:

- 1 command (`radio`) with 3 sub-commands (`items`, `search`, `play`)
- 1 menu node ("Radio" at weight 45)
- ContentProvider registered as `"radio"` (`browse`, `search`, `get_stream_info`)
- radio-browser.info API client with async caching (256 entries, 10min TTL)
- Pre-resolved stream URLs, play/add/insert modes
- "Add to Favorites" context menu via `jivefavorites add`
- ~730 lines of code (plugin + radio-browser.info client)
- 114 tests

**Ideal as a reference for ContentProvider plugins (remote streaming,
Jive browse/search menus, URL proxy integration).**

### Podcast Plugin (`plugins/podcast/`)

Second ContentProvider plugin — podcast browsing, search, and streaming:

- 1 command (`podcast`) with 5 sub-commands (`items`, `search`, `play`, `addshow`, `delshow`)
- 1 menu node ("Podcasts" at weight 50)
- ContentProvider registered as `"podcast"` (`browse`, `search`, `get_stream_info`)
- RSS 2.0 feed parser with iTunes namespace support (`feed_parser.py`)
- PodcastIndex API integration for podcast search
- Subscription management (subscribe/unsubscribe to feeds)
- Resume position tracking (LMS-compatible threshold logic)
- Recently played episodes (LRU, 50 entries)
- JSON persistence with atomic writes (`store.py`)
- "Add to Favorites" context menu via `jivefavorites add`
- ~1200 lines of plugin code + ~550 lines feed parser + ~490 lines store
- 178 tests

**Ideal as a reference for ContentProvider plugins with persistence,
subscription management, and RSS feed integration.**

### Now Playing Tutorial Plugin (`plugins/nowplaying/`)

Companion code for the [Plugin Tutorial](PLUGIN_TUTORIAL.md):

- 2 commands (`nowplaying.stats`, `nowplaying.recent`)
- 1 menu node ("Now Playing Stats")
- Event subscription (`player.track_started`)
- JSON persistence store
- ~200 lines of code
- 58 tests

**Ideal as a learning companion — built step by step in the tutorial.**

---

## 18) Plugin Settings & Management API

This section documents the built-in management surface introduced with the
plugin modernization (phases A-E).

### JSON-RPC Commands

#### `pluginsettings`

| Command | Purpose |
|---|---|
| `["pluginsettings", "getdef", "<plugin>"]` | Returns only setting definitions |
| `["pluginsettings", "get", "<plugin>"]` | Returns definitions + current (masked) values |
| `["pluginsettings", "set", "<plugin>", "key:value", ...]` | Validates, persists, and updates values |

Notes:
- `set` performs type parsing (`int/float/bool/string/select`) before validation.
- Secret values are masked in responses.
- If a changed setting has `restart_required = true`, response includes `restart_required: true`.

#### `pluginmanager`

| Command | Purpose |
|---|---|
| `["pluginmanager", "list"]` | List installed plugins with state/type metadata |
| `["pluginmanager", "info", "<plugin>"]` | One plugin + setting definitions |
| `["pluginmanager", "enable", "<plugin>"]` | Persist state as enabled |
| `["pluginmanager", "disable", "<plugin>"]` | Persist state as disabled |
| `["pluginmanager", "install", "<url>", "<sha256>"]` | Download and install plugin ZIP |
| `["pluginmanager", "uninstall", "<plugin>"]` | Uninstall community plugin |
| `["pluginmanager", "repository"]` | Fetch repository index + compare with installed |
| `["pluginmanager", "installrepo", "<plugin>"]` | Install plugin directly from repository |

### REST Endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/plugins` | `GET` | List plugins + `restart_required` |
| `/api/plugins/{name}/settings` | `GET` | Get definitions + masked values |
| `/api/plugins/{name}/settings` | `PUT` | Update settings with validation |
| `/api/plugins/{name}/enable` | `POST` | Enable plugin |
| `/api/plugins/{name}/disable` | `POST` | Disable plugin |
| `/api/plugins/install` | `POST` | Install from ZIP URL + SHA256 |
| `/api/plugins/{name}/uninstall` | `POST` | Uninstall community plugin |
| `/api/plugins/repository` | `GET` | List repository plugins (`force_refresh` optional) |
| `/api/plugins/install-from-repo` | `POST` | Install by repository plugin name |

### Settings Storage Format

Per plugin, settings are stored at:

```
data/plugins/<plugin_name>/settings.json
```

Payload structure:

```json
{
  "_version": 1,
  "_plugin_version": "1.0.0",
  "my_setting": "value"
}
```

### State Storage Format

Plugin enable/disable states are stored globally at:

```
data/plugin_states.json
```

Unknown plugins default to `enabled` for backwards compatibility.

---

## 19) Server-Driven UI (SDUI)

Plugins can expose pages in the web UI without writing any JavaScript.
The plugin describes its UI as a Python data structure (a tree of typed
components). The frontend has a generic renderer that maps each component
`type` to a Svelte widget.

**Plugins supply data. The frontend supplies presentation.**

### Overview

```
Plugin (Python)           Server              Frontend (Svelte)
─────────────────         ──────              ──────────────────
get_ui(ctx) → Page   →   GET /api/plugins/{id}/ui   →   PluginRenderer
                              ↓ JSON                       ↓
                         { components: [...] }        Recursive render
                                                      (Card, Table, Button, …)

handle_action(action, params)  ←  POST /api/plugins/{id}/actions/{action}
```

### Quick Start

**1. Add `[ui]` to `plugin.toml`:**

```toml
[plugin]
name = "myplugin"
version = "1.0.0"

[ui]
enabled = true
sidebar_label = "My Plugin"
sidebar_icon = "star"
```

**2. Write `get_ui()` — returns a `Page` describing your UI:**

```python
from resonance.ui import (
    Page, Card, KeyValue, KVItem, StatusBadge, Button, Row, Alert, Table,
    TableColumn, Text, Heading, Column, Progress, Markdown,
)

async def get_ui(ctx):
    return Page(
        title="My Plugin",
        icon="star",
        refresh_interval=10,  # auto-refresh every 10 seconds (0 = no polling)
        components=[
            Card(title="Status", children=[
                StatusBadge(label="Running", color="green"),
                KeyValue(items=[
                    KVItem("Version", "1.0.0"),
                    KVItem("Uptime", "3h 42m", color="blue"),
                ]),
            ]),
            Row(children=[
                Button("Restart", action="restart", style="danger", confirm=True),
                Button("Refresh", action="refresh", style="secondary"),
            ]),
        ],
    )
```

**3. Write `handle_action()` — dispatches button clicks:**

```python
async def handle_action(action: str, params: dict) -> dict:
    match action:
        case "restart":
            await do_restart()
            return {"message": "Restarted successfully"}
        case "refresh":
            return {"success": True}
        case _:
            return {"error": f"Unknown action: {action}"}
```

**4. Register both in `setup()`:**

```python
async def setup(ctx):
    ctx.register_ui_handler(get_ui)
    ctx.register_action_handler(handle_action)
```

That's it. The plugin now has a page in the web UI sidebar.

### Available Widgets

#### Display Widgets

| Widget | Constructor | Key Props | Description |
|---|---|---|---|
| `heading` | `Heading(text, level=2)` | `text`, `level` (1–4) | Section heading |
| `text` | `Text(content, color?, size?)` | `content`, `color`, `size` | Paragraph text |
| `status_badge` | `StatusBadge(label, status?, color)` | `label`, `status`, `color` | Colored status indicator |
| `key_value` | `KeyValue(items)` | `items: list[KVItem]` | Key-value pair list |
| `table` | `Table(columns, rows, title?, edit_action?, row_key?)` | `columns`, `rows`, `title`, `edit_action`, `row_key` | Data table (supports row actions and inline editing) |
| `button` | `Button(label, action, params?, style?, confirm?, icon?, disabled?)` | `label`, `action`, `style`, `confirm`, `disabled` | Action trigger |
| `card` | `Card(title?, children?, collapsible?, collapsed?)` | `title`, `children`, `collapsible`, `collapsed` | Grouped content box with optional expand/collapse |
| `row` | `Row(children?, gap?, justify?, align?)` | `children`, `gap` | Horizontal flex layout |
| `column` | `Column(children?, gap?)` | `children`, `gap` | Vertical flex layout |
| `alert` | `Alert(message, severity?, title?)` | `message`, `severity` | Info/warning/error/success banner |
| `progress` | `Progress(value, label?, color?)` | `value` (0–100), `label` | Progress bar |
| `markdown` | `Markdown(content)` | `content` | Rendered markdown text |

#### Layout Widgets (Phase 2)

| Widget | Constructor | Key Props | Description |
|---|---|---|---|
| `tabs` | `Tabs(tabs)` | `tabs: list[Tab]` | Tab-based navigation (client-side, no backend roundtrip) |

Each `Tab` has: `label` (str), `children` (list of components), `icon` (optional str).

#### Modal / Dialog Widget (Phase 2.5)

| Widget | Constructor | Key Props | Description |
|---|---|---|---|
| `modal` | `Modal(title, trigger_label, children?, trigger_style?, trigger_icon?, size?)` | `title`, `trigger_label`, `size` | Modal dialog overlay triggered by a button |

**Props:**

- `title` (str, required) — shown in the modal header.
- `trigger_label` (str, required) — text on the button that opens the modal.
- `trigger_style` (str, default `"secondary"`) — button style: `"primary"`, `"secondary"`, `"danger"`.
- `trigger_icon` (str, optional) — icon name for the trigger button.
- `size` (str, default `"md"`) — modal width: `"sm"` (384px), `"md"` (512px), `"lg"` (672px), `"xl"` (896px).
- `children` (list, optional) — components rendered inside the modal body.

**Usage:** The modal renders a trigger button inline. When clicked, a dialog overlay
appears with the header, close button, and the children components. Clicking the
backdrop or pressing Escape closes the modal. Modals can contain any widget,
including `Form`, `KeyValue`, `Table`, etc.

#### Form Widgets (Phase 2)

| Widget | Constructor | Key Props | Description |
|---|---|---|---|
| `form` | `Form(action, children?, submit_label?, submit_style?, disabled?)` | `action`, `submit_label`, `disabled` | Container that collects input values and submits as action params |
| `text_input` | `TextInput(name, label, value?, placeholder?, required?, pattern?, disabled?)` | `name`, `label`, `value` | Single-line text field |
| `textarea` | `Textarea(name, label, value?, placeholder?, rows?, maxlength?, required?, disabled?)` | `name`, `label`, `value`, `rows` | Multi-line text field with optional character limit |
| `number_input` | `NumberInput(name, label, value?, min?, max?, step?, required?, disabled?)` | `name`, `label`, `value`, `min`, `max` | Numeric input with range constraints |
| `select` | `Select(name, label, value?, options?, required?, disabled?)` | `name`, `label`, `options: list[SelectOption]` | Dropdown selection |
| `toggle` | `Toggle(name, label, value?, disabled?)` | `name`, `label`, `value` (bool) | Boolean on/off switch |

Each `SelectOption` has: `value` (str), `label` (str).

**Textarea props:** `rows` (int, default 4) sets the visible height. `maxlength` (int, optional)
limits the character count and shows a live counter in the UI. The textarea is vertically resizable.

**Form behaviour:** On submit, the `Form` widget collects all child input values
(identified by their `name` prop) into a `params` dict and sends it to
`POST /api/plugins/{id}/actions/{action}`. The submit button is disabled until
the user changes a value (dirty tracking). After a successful submission, the
dirty state resets.

### Color Values

All color props accept: `"green"`, `"red"`, `"yellow"`, `"blue"`, `"gray"`.

### Button Styles

- `"primary"` — Accent-colored, for primary actions
- `"secondary"` — Neutral, for secondary actions (default)
- `"danger"` — Red, for destructive actions

### Alert Severities

`"info"` (default), `"warning"`, `"error"`, `"success"`.

### Card: Collapsible Mode

Cards can be made collapsible so the user can expand/collapse the body:

```python
# Always expanded (default)
Card(title="Status", children=[...])

# Collapsible, starts expanded
Card(title="Details", collapsible=True, children=[...])

# Collapsible, starts collapsed
Card(title="Common Options (read-only)", collapsible=True, collapsed=True, children=[...])
```

- `collapsible` (bool, default `False`) — renders a toggle chevron in the header.
- `collapsed` (bool, default `False`) — initial state; only used when `collapsible=True`.
- When `collapsed=True` is set without `collapsible=True`, it is silently ignored.
- Both flags are only serialised when `collapsible` is `True` to keep payloads compact.

### Table Usage

```python
Table(
    title="Detected Devices",
    columns=[
        TableColumn(key="name", label="Name"),
        TableColumn(key="mac", label="MAC Address"),
        TableColumn(key="enabled", label="Enabled", variant="badge"),
        TableColumn(key="actions", label="", variant="actions"),
    ],
    rows=[
        {
            "name": "Kitchen Speaker",
            "mac": "aa:bb:cc:dd:ee:ff",
            "enabled": {"text": "Yes", "color": "green"},
            "actions": [
                TableAction(label="Delete", action="delete_device",
                            params={"udn": "..."}, style="danger", confirm=True).to_dict(),
            ],
        },
    ],
)
```

Each row is a `dict` whose keys match the column `key` values.

**Column variants:**
- `"text"` (default) — plain text display
- `"badge"` — renders cell as a colored badge; cell value must be `{"text": "...", "color": "..."}`
- `"actions"` — renders `TableAction` buttons per row; cell value is a list of action dicts

### Table: Inline Editing

Columns with `variant="editable"` render as click-to-edit text fields. When the
user commits an edit (Enter or blur), the table dispatches `edit_action` with the
row identifier and the new value:

```python
Table(
    columns=[
        TableColumn(key="name", label="Name", variant="editable"),
        TableColumn(key="mac", label="MAC Address"),
        TableColumn(key="enabled", label="Enabled", variant="badge"),
        TableColumn(key="actions", label="", variant="actions"),
    ],
    rows=device_rows,
    title="Detected AirPlay Devices",
    edit_action="update_device",
    row_key="udn",
)
```

- `edit_action` (str, optional) — plugin action to dispatch on edit commit.
- `row_key` (str, default `"udn"`) — which column uniquely identifies a row.
- On commit, the frontend dispatches: `{<row_key>: <row[row_key]>, <col.key>: <new_value>}`.
- Pressing Escape cancels the edit. Only changed values trigger a dispatch.
- The column variants are: `"text"` (default), `"badge"`, `"actions"`, `"editable"`.

### Tabs Usage

```python
from resonance.ui import Tabs, Tab

Tabs(tabs=[
    Tab(label="Status", icon="activity", children=[
        Card(title="Bridge Status", children=[...]),
    ]),
    Tab(label="Settings", icon="settings", children=[
        Form(action="save_settings", children=[...]),
    ]),
])
```

Tab switching is purely client-side — no backend roundtrip on tab change.
The `icon` parameter is optional and accepts any Lucide icon name.

### Form Usage

```python
from resonance.ui import Form, TextInput, NumberInput, Select, SelectOption, Toggle

Form(
    action="save_settings",
    submit_label="Save Settings",
    disabled=is_active,  # disable when bridge is running
    children=[
        Select(
            name="mode",
            label="Volume Mode",
            value="hardware",
            options=[
                SelectOption(value="hardware", label="Hardware"),
                SelectOption(value="software", label="Software"),
                SelectOption(value="disabled", label="Disabled"),
            ],
        ),
        TextInput(
            name="interface",
            label="Network Interface",
            value="127.0.0.1",
            placeholder="e.g. 192.168.1.100",
        ),
        NumberInput(
            name="port",
            label="Port",
            value=9000,
            min=1,
            max=65535,
        ),
        Toggle(
            name="autostart",
            label="Auto-start at server startup",
            value=True,
        ),
    ],
)
```

When submitted, `handle_action("save_settings", params)` receives:
```python
params = {
    "mode": "hardware",
    "interface": "127.0.0.1",
    "port": 9000,
    "autostart": True,
}
```

**Disabled state:** Setting `disabled=True` on a `Form` disables all child
inputs and the submit button. Individual inputs can also be disabled
independently via their own `disabled` prop.

### Conditional Rendering (`visible_when`)

Any component inside a `Form` can be conditionally shown or hidden based on the
current value of a sibling form field. Use the `.when()` method to attach a
condition:

```python
Toggle(name="debug_enabled", label="Enable Debug", value=False),
Select(
    name="debug_category",
    label="Debug Category",
    value="all",
    options=[SelectOption(value="all", label="All"), SelectOption(value="network", label="Network")],
).when("debug_enabled", True),
```

The `Select` above is only rendered when the `debug_enabled` toggle is `True`.

#### Operators

By default `.when()` uses equality (`eq`). You can pass an `operator` argument
to use a different comparison:

| Operator | Meaning | Example |
|----------|---------|---------|
| `eq` | Equal (default) | `.when("mode", "hardware")` |
| `ne` | Not equal | `.when("mode", "disabled", operator="ne")` |
| `gt` | Greater than | `.when("port", 1024, operator="gt")` |
| `lt` | Less than | `.when("volume", 10, operator="lt")` |
| `gte` | Greater than or equal | `.when("level", 5, operator="gte")` |
| `lte` | Less than or equal | `.when("level", 100, operator="lte")` |
| `in` | Value is in list | `.when("codec", ["aac", "alac"], operator="in")` |
| `not_in` | Value is not in list | `.when("codec", ["pcm", "wav"], operator="not_in")` |

```python
# Show a warning when port is below 1024
Alert(message="Privileged port!", severity="warning").when("port", 1024, operator="lt"),

# Show options only for specific codecs
Text("Lossy codec selected").when("codec", ["aac", "mp3"], operator="in"),
```

#### Behaviour Notes

- `.when()` returns `self`, so it is chainable.
- Calling `.when()` multiple times on the same component replaces the previous
  condition (last call wins).
- Outside a `Form`, `visible_when` is silently ignored — the component is
  always visible.
- When `operator` is `"eq"` (the default), the `operator` key is omitted from
  the serialised JSON to keep payloads compact.
- The frontend evaluates conditions reactively: changing a form field
  immediately shows/hides dependent components.

### KeyValue Usage

```python
KeyValue(items=[
    KVItem("Status", "Active", color="green"),
    KVItem("Binary", "/usr/bin/squeeze2raop"),
    KVItem("Server", "192.168.1.1:9000"),
])
```

### Modal Usage

```python
from resonance.ui import Modal, Form, TextInput, Select, SelectOption, KeyValue, KVItem

Modal(
    title="Device Settings — Living Room",
    trigger_label="Settings: Living Room",
    trigger_style="secondary",
    trigger_icon="settings",
    size="md",
    children=[
        Form(
            action="update_device",
            submit_label="Save Device Settings",
            children=[
                TextInput(
                    name="name",
                    label="Display Name",
                    value="Living Room",
                    required=True,
                ),
                Select(
                    name="volume_mode",
                    label="Volume Mode",
                    value="2",
                    options=[
                        SelectOption(value="2", label="Hardware"),
                        SelectOption(value="1", label="Software"),
                        SelectOption(value="0", label="Ignored"),
                    ],
                ),
                KeyValue(items=[
                    KVItem("MAC Address", "aa:bb:cc:dd:ee:ff"),
                    KVItem("Enabled", "Yes", color="green"),
                ]),
            ],
        ),
    ],
)
```

The modal renders a button labelled "Settings: Living Room". Clicking it opens
a dialog overlay containing the form. The form collects `name` and
`volume_mode` values and submits them to `handle_action("update_device", params)`.
Clicking the backdrop or pressing Escape closes the modal.

### Action Handler Details

The `handle_action()` function receives:
- `action` — the string from `Button(action="...")` or `Form(action="...")`
- `params` — the dict from `Button(params={...})`, the merged row params from
  `TableAction`, or the collected form input values from a `Form` submission.
  Defaults to `{}` if none.

It must return a `dict`. Special keys in the return value:
- `{"message": "..."}` — shown as a success toast notification in the UI
- `{"error": "..."}` — indicates failure (HTTP 500 if raised as exception)
- `{"success": True}` — silent success (no toast)

### Page Envelope

The `Page` object wraps the component tree:

```python
Page(
    title="My Plugin",          # Page heading (required)
    icon="star",                # Lucide icon name (optional)
    refresh_interval=10,        # Auto-poll interval in seconds (0 = off)
    components=[...],           # List of UIComponent widgets
)
```

### REST Endpoints

These are automatically available for any plugin with `[ui] enabled = true`:

| Endpoint | Method | Description |
|---|---|---|
| `/api/plugins/ui-registry` | GET | Sidebar entries (all UI-enabled plugins) |
| `/api/plugins/{plugin_id}/ui` | GET | Full UI JSON schema for one plugin |
| `/api/plugins/{plugin_id}/actions/{action}` | POST | Dispatch a button action |

### Security

- **No plugin JavaScript runs in the browser.** Plugins only provide data.
- Event handler props (e.g. `onclick`) are rejected during validation.
- URL props (`href`, `src`, `url`) must use `http:`, `https:`, or `mailto:` schemes.
  `javascript:` and `data:` URLs are blocked.
- The frontend never uses `{@html}` on plugin-provided content.

### Sidebar Icon

The sidebar icon is resolved in this order:
1. `[ui].sidebar_icon` from `plugin.toml`
2. `[plugin].icon` from `plugin.toml`
3. Fallback: `"plug"`

Supported icon names are any [Lucide](https://lucide.dev/icons/) icon name
in kebab-case (e.g. `"cast"`, `"radio"`, `"hard-drive"`, `"refresh-cw"`).

### Reference Implementation

The `raopbridge` community plugin is the first SDUI consumer. Study its
implementation for a complete real-world example:

- `resonance-community-plugins-main/plugins/raopbridge/plugin.toml` — `[ui]` section
- `resonance-community-plugins-main/plugins/raopbridge/__init__.py` — `get_ui()` and `handle_action()`

It renders a tabbed interface with three tabs:

- **Status tab:** Bridge status card with `StatusBadge` + `KeyValue`, control
  buttons (`Activate` / `Deactivate` / `Restart`).
- **Devices tab:** A device `Table` with badge columns (enabled status) and
  row-level `TableAction` buttons (delete device with confirmation).
- **Settings tab:** An editable `Form` with `Select` (binary selection),
  `TextInput` (interface, server address), and multiple `Toggle` switches
  (auto-start, auto-save, logging, debug). The form is disabled while the
  bridge is active. A read-only `Card` shows configuration file info.

Actions dispatch to existing bridge management functions (`save_settings`,
`delete_device`, `activate`, `deactivate`, `restart`).

### Architecture Details

For the original architecture document including security model, schema
specification, and design rationale, see the archived planning document
[`dev/SDUI_ARCHITECTURE.md`](dev/SDUI_ARCHITECTURE.md).

---

## Further Reading

| Document | Content |
|---|---|
| [`PLUGINS.md`](PLUGINS.md) | General overview for all audiences |
| [`PLUGIN_TUTORIAL.md`](PLUGIN_TUTORIAL.md) | Step-by-step: Build your own plugin |
| [`PLUGIN_REPOSITORY.md`](PLUGIN_REPOSITORY.md) | Publishing plugins and repository index format |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Resonance system architecture |
| [`dev/SDUI_ARCHITECTURE.md`](dev/SDUI_ARCHITECTURE.md) | SDUI architecture deep-dive (archived planning document) |
| `plugins/radio/` | Reference ContentProvider plugin (radio-browser.info, remote streaming) |
| `plugins/podcast/` | Reference ContentProvider plugin (RSS feeds, subscriptions, resume) |
| `community-repo/plugins/raopbridge/` | Reference SDUI consumer (AirPlay bridge with UI page) |

---

*Last updated: June 2025 (SDUI documentation added in §19)*
