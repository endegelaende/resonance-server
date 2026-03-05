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
4. [Plugin Lifecycle](#4-plugin-lifecycle)
5. [PluginContext — Complete API](#5-plugincontext--complete-api)
6. [JSON-RPC Commands](#6-json-rpc-commands)
7. [Jive Menu System](#7-jive-menu-system)
8. [Event System](#8-event-system)
9. [HTTP Routes (FastAPI)](#9-http-routes-fastapi)
10. [Data Persistence](#10-data-persistence)
11. [Server Access (Read-Only)](#11-server-access-read-only)
12. [Testing](#12-testing)
13. [Best Practices](#13-best-practices)
14. [Debugging & Logging](#14-debugging--logging)
15. [Error Handling & Isolation](#15-error-handling--isolation)
16. [Known Limitations](#16-known-limitations)
17. [Content Providers](#17-content-providers)
18. [Reference Plugins](#18-reference-plugins)
19. [Server-Driven UI (SDUI)](#19-server-driven-ui-sdui)
    - [Overview](#overview)
    - [Quick Start](#quick-start)
    - [Widget Reference](#widget-reference)
    - [Wire Format Specification](#wire-format-specification)
    - [Colors, Styles & Constants](#colors-styles--constants)
    - [Layout & Composition](#layout--composition)
    - [Form System](#form-system)
    - [Conditional Rendering (`visible_when`)](#conditional-rendering-visible_when)
    - [Modal Dialogs](#modal-dialogs)
    - [Action Handler Protocol](#action-handler-protocol)
    - [REST Endpoints & SSE](#rest-endpoints--sse)
    - [Error Responses](#error-responses)
    - [Schema Versioning](#schema-versioning)
    - [Security Model](#security-model)
    - [Reference Implementation](#reference-implementation)
20. [Plugin Settings & Management API](#20-plugin-settings--management-api)

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
{ "method": "slim.request", "params": ["-", ["myplugin.hello"]] }
```

→ `{"result": {"message": "Hello from myplugin!"}}`

---

## 2) Manifest (`plugin.toml`)

Every plugin needs a `plugin.toml` in its plugin directory. The parser
reads the `[plugin]` table and creates a `PluginManifest` object.

### Fields

| Field                   | Required | Type   | Description                                                              |
| ----------------------- | -------- | ------ | ------------------------------------------------------------------------ |
| `name`                  | ✅       | string | Unique identifier. Must match the directory name. Only `[a-z0-9_-]`.     |
| `version`               | ✅       | string | Semver version, e.g. `"1.0.0"`.                                          |
| `description`           | ❌       | string | One-line description.                                                    |
| `author`                | ❌       | string | Author or maintainer.                                                    |
| `min_resonance_version` | ❌       | string | Minimum server version (informational, not enforced).                    |
| `category`              | ❌       | string | Optional category for UIs/repository (`radio`, `podcast`, `tools`, ...). |
| `icon`                  | ❌       | string | Optional icon key/URL hint for UIs.                                      |

### UI Page (`[ui]`)

If your plugin wants to expose a page in the web UI sidebar, add a `[ui]`
section to your manifest. See [§19 Server-Driven UI](#19-server-driven-ui-sdui) for the full guide.

| Field           | Required | Type   | Description                                                                                                                           |
| --------------- | -------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------- |
| `enabled`       | ✅       | bool   | Set `true` to enable the plugin UI page.                                                                                              |
| `sidebar_label` | ❌       | string | Label shown in the sidebar (defaults to plugin name).                                                                                 |
| `sidebar_icon`  | ❌       | string | Lucide icon name for the sidebar entry (e.g. `"cast"`, `"radio"`, `"radar"`). Falls back to the plugin's `icon` field, then `"plug"`. |

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

| Field                       | Type           | Default    | Meaning                          |
| --------------------------- | -------------- | ---------- | -------------------------------- |
| `type`                      | string         | `"string"` | Value type                       |
| `label`                     | string         | key        | Display label                    |
| `description`               | string         | `""`       | Help text                        |
| `default`                   | type-dependent | varies     | Default value                    |
| `required`                  | bool           | `false`    | Reject empty value               |
| `secret`                    | bool           | `false`    | Mask value in external responses |
| `restart_required`          | bool           | `false`    | Marks update as restart-relevant |
| `order`                     | int            | `0`        | UI sort order                    |
| `min` / `max`               | number         | `null`     | Numeric range                    |
| `min_length` / `max_length` | int            | `null`     | String length bounds             |
| `pattern`                   | string         | `null`     | Regex for strings                |
| `options`                   | list[string]   | `[]`       | Allowed values for `select`      |

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

## 4) Plugin Lifecycle

### Startup Sequence

```
Server starts
│
├─► Core initialization (DB, Library, PlayerRegistry, EventBus)
│
├─► PluginManager.discover()
│     For each plugins/<name>/plugin.toml:
│       ├─► Parse manifest → PluginManifest
│       ├─► Check plugin_states.json → enabled?
│       └─► Log: "Discovered plugin: <name> v<version>"
│
├─► PluginManager.start_all()
│     For each discovered & enabled plugin (alphabetical):
│       ├─► Create PluginContext(manifest, server_refs)
│       ├─► Load module (importlib)
│       ├─► Call setup(ctx)
│       │     ├─► Plugin registers commands, menus, events, routes
│       │     ├─► Plugin loads data, starts background tasks
│       │     └─► ✅ Success → mark as started
│       │         ❌ Exception → rollback all registrations, log error, skip
│       └─► Log: "Started plugin: <name> — PluginContext(...)"
│
└─► Server ready (HTTP on :9000, Slimproto on :3483)
```

### Shutdown Sequence

```
Server shutdown signal (Ctrl+C, SIGTERM)
│
├─► PluginManager.stop_all()
│     For each started plugin (REVERSE order — LIFO):
│       ├─► Call teardown(ctx) if defined
│       │     └─► Plugin saves state, releases resources
│       ├─► ctx._cleanup()
│       │     ├─► Unregister all commands
│       │     ├─► Unregister all menu nodes/items
│       │     ├─► Unsubscribe all event handlers
│       │     ├─► Unregister content providers
│       │     └─► Clear UI handlers
│       └─► Log: "Stopped plugin: <name>"
│
├─► Core shutdown (close DB, disconnect players)
│
└─► Process exit
```

### State Transitions

```
  ┌────────────┐    discover()    ┌────────────┐
  │            │ ───────────────► │            │
  │  Unknown   │                  │ Discovered │
  │            │                  │ (enabled)  │
  └────────────┘                  └─────┬──────┘
                                        │
                                   setup(ctx)
                                        │
                              ┌─────────┴─────────┐
                              │                    │
                        ✅ Success            ❌ Exception
                              │                    │
                    ┌─────────▼──────┐    ┌────────▼───────┐
                    │                │    │                 │
                    │    Started     │    │ Failed (logged, │
                    │   (running)    │    │  skipped)       │
                    │                │    │                 │
                    └─────────┬──────┘    └─────────────────┘
                              │
                         teardown(ctx)
                         + _cleanup()
                              │
                    ┌─────────▼──────┐
                    │                │
                    │    Stopped     │
                    │                │
                    └────────────────┘
```

---

## 5) PluginContext — Complete API

The `PluginContext` is created per plugin by the `PluginManager` and
passed to `setup()` / `teardown()`. It is the **only** gateway to
server functionality.

### Identity

| Attribute     | Type             | Description                                       |
| ------------- | ---------------- | ------------------------------------------------- |
| `plugin_id`   | `str`            | Name from manifest (e.g. `"favorites"`)           |
| `data_dir`    | `Path`           | `data/plugins/<plugin_id>/`                       |
| `server_info` | `dict[str, Any]` | Server networking info (`{"host": …, "port": …}`) |

### Server Access (read-only)

| Attribute          | Type                      | Description             |
| ------------------ | ------------------------- | ----------------------- |
| `event_bus`        | `EventBus`                | Pub/sub event system    |
| `music_library`    | `MusicLibrary`            | Query the music library |
| `player_registry`  | `PlayerRegistry`          | Connected players       |
| `playlist_manager` | `PlaylistManager \| None` | Playlist access         |

### Registration Methods

| Method                        | Signature                                               | Description                                               |
| ----------------------------- | ------------------------------------------------------- | --------------------------------------------------------- |
| `register_command`            | `(name: str, handler: CommandHandler) -> None`          | JSON-RPC command                                          |
| `unregister_command`          | `(name: str) -> None`                                   | Remove a command                                          |
| `register_menu_node`          | `(node_id, parent, text, weight, **kwargs) -> None`     | Jive menu node                                            |
| `register_menu_item`          | `(node_id: str, item: dict) -> None`                    | Jive menu entry                                           |
| `register_route`              | `(router: APIRouter) -> None`                           | FastAPI router                                            |
| `register_content_provider`   | `(provider_id: str, provider: ContentProvider) -> None` | External audio source (Radio, Podcast, …)                 |
| `unregister_content_provider` | `(provider_id: str) -> None`                            | Remove a content provider                                 |
| `subscribe`                   | `async (event_type: str, handler) -> None`              | Event with auto-cleanup                                   |
| `register_ui_handler`         | `(handler: Callable) -> None`                           | SDUI page builder ([§19](#19-server-driven-ui-sdui))      |
| `register_action_handler`     | `(handler: Callable) -> None`                           | SDUI action dispatcher ([§19](#19-server-driven-ui-sdui)) |

### Utility Functions

| Method                     | Signature                               | Description                                        |
| -------------------------- | --------------------------------------- | -------------------------------------------------- |
| `ensure_data_dir`          | `() -> Path`                            | Create/return data directory                       |
| `get_setting`              | `(key: str) -> Any`                     | Read one setting (falls back to default)           |
| `set_setting`              | `(key: str, value: Any) -> None`        | Validate and persist one setting                   |
| `set_settings`             | `(values: dict[str, Any]) -> list[str]` | Validate and persist multiple settings atomically  |
| `get_all_settings`         | `() -> dict[str, Any]`                  | All settings with defaults                         |
| `get_all_settings_masked`  | `() -> dict[str, Any]`                  | Same values, but secrets masked                    |
| `get_settings_definitions` | `() -> list[dict[str, Any]]`            | Serialized setting definitions for APIs            |
| `has_settings`             | `@property -> bool`                     | Whether the plugin declared settings               |
| `notify_ui_update`         | `() -> None`                            | Push SSE refresh to all connected UI clients (§19) |
| `ui_revision`              | `@property -> int`                      | Current UI revision counter (monotonic)            |
| `wait_for_ui_update`       | `async (last_revision, timeout) -> int` | Block until revision changes or timeout            |

### Cleanup Guarantee

**Everything** registered via `PluginContext` is automatically removed
after `teardown()`:

- Commands → `unregister_command()` for each registered command
- Menus → Entries with `_plugin_id` are removed from the global list
- Events → `event_bus.unsubscribe()` for each subscribed handler
- Content providers → `unregister_content_provider()` for each registered provider
- UI handlers → `_ui_handler` and `_action_handler` are set to `None`
- Routes → _Note: FastAPI routes cannot currently be cleanly removed (framework limitation)_

**Manual cleanup in `teardown()` is not needed** — only for your own
resources (open files, network connections, subprocesses, etc.).

---

## 6) JSON-RPC Commands

### Handler Signature

```python
async def my_handler(
    ctx: CommandContext,
    command: list[Any],
) -> dict[str, Any]:
    ...
```

| Parameter  | Type             | Description                                                   |
| ---------- | ---------------- | ------------------------------------------------------------- |
| `ctx`      | `CommandContext` | Server context (player ID, library, registry, …)              |
| `command`  | `list[Any]`      | Raw command array, e.g. `["myplugin", "action", "key:value"]` |
| **Return** | `dict[str, Any]` | Result dict, sent as `result` in the JSON-RPC response        |

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

## 7) Jive Menu System

Squeezebox Touch/Radio/Boom/Controller use a tree-based menu system.
Plugins can attach nodes and entries to this tree.

### Concepts

| Concept     | Description                                                        |
| ----------- | ------------------------------------------------------------------ |
| **Node**    | Menu folder, contains children. Has `isANode: 1`.                  |
| **Item**    | Menu entry with actions (go, play, add, do).                       |
| **Weight**  | Sort weight — lower = higher in the list.                          |
| **Parent**  | ID of the parent node. `"home"` = home menu.                       |
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

| Weight | Entry          |
| ------ | -------------- |
| 11     | My Music       |
| 35     | Audio Settings |
| 55     | Favorites      |
| 100    | Player Power   |
| 1000   | Example Plugin |
| 1005   | Settings       |

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

## 8) Event System

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

| Event String                 | Class                      | Fields                                                                  | When                                 |
| ---------------------------- | -------------------------- | ----------------------------------------------------------------------- | ------------------------------------ |
| `server.started`             | `ServerStartedEvent`       | —                                                                       | Server fully initialized             |
| `server.stopping`            | `ServerStoppingEvent`      | —                                                                       | Shutdown begins                      |
| `player.connected`           | `PlayerConnectedEvent`     | `player_id`, `name`, `model`                                            | Player connects                      |
| `player.disconnected`        | `PlayerDisconnectedEvent`  | `player_id`                                                             | Player disconnects                   |
| `player.status`              | `PlayerStatusEvent`        | `player_id`, `state`, `volume`, `muted`, `elapsed_*`, `duration`, …     | Status change                        |
| `player.track_started`       | `PlayerTrackStartedEvent`  | `player_id`, `stream_generation`                                        | Track playback started (STMs)        |
| `player.track_finished`      | `PlayerTrackFinishedEvent` | `player_id`, `stream_generation`                                        | Track playback finished              |
| `player.decode_ready`        | `PlayerDecodeReadyEvent`   | `player_id`, `stream_generation`                                        | Decoder ready for next track (STMd)  |
| `player.live_stream_dropped` | `LiveStreamDroppedEvent`   | `player_id`, `stream_generation`, `remote_url`, `content_type`, `title` | Live/radio stream ended unexpectedly |
| `player.playlist`            | `PlayerPlaylistEvent`      | `player_id`, `action`, `index`, `count`                                 | Playlist changed                     |
| `library.scan`               | `LibraryScanEvent`         | `status`, `scanned`, `total`, `current_path`, `error`                   | Library scan                         |

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

## 9) HTTP Routes (FastAPI)

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

## 10) Data Persistence

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

## 11) Server Access (Read-Only)

Plugins access the server through the `PluginContext` attributes listed
in [§5](#5-plugincontext--complete-api). This section documents the
available methods on each server object.

### MusicLibrary

`ctx.music_library` — query the indexed music database.

| Method              | Signature                                                             | Returns              | Description                                |
| ------------------- | --------------------------------------------------------------------- | -------------------- | ------------------------------------------ |
| `get_artists`       | `async (*, offset=0, limit=100) -> tuple[Artist, ...]`                | Tuple of `Artist`    | List artists with stable IDs (paginated)   |
| `get_albums`        | `async (*, artist_id=None, offset=0, limit=100) -> tuple[Album, ...]` | Tuple of `Album`     | List albums, optionally filtered by artist |
| `get_tracks`        | `async (*, album_id=None, offset=0, limit=200) -> tuple[Track, ...]`  | Tuple of `Track`     | List tracks, optionally filtered by album  |
| `get_track_by_id`   | `async (track_id: TrackId) -> Track \| None`                          | `Track` or `None`    | Look up a single track                     |
| `get_track_by_path` | `async (path: str \| Path) -> Track \| None`                          | `Track` or `None`    | Look up a track by file path               |
| `get_years`         | `async () -> list[int]`                                               | List of integers     | Unique years in the library                |
| `search`            | `async (query: str, *, limit=50) -> SearchResult`                     | `SearchResult`       | Full-text search across all tracks         |
| `get_music_folders` | `async () -> list[str]`                                               | List of path strings | Configured music root directories          |
| `start_scan`        | `async () -> bool`                                                    | `True` if started    | Trigger background library scan            |
| `is_scanning`       | `@property -> bool`                                                   | Boolean              | Whether a scan is currently running        |
| `scan_status`       | `@property -> ScanStatus`                                             | `ScanStatus`         | Detailed scan progress                     |

**Key types:**

```python
@dataclass
class Track:
    id: TrackId
    path: str
    title: str
    artist_id: ArtistId | None
    album_id: AlbumId | None
    artist_name: str
    album_title: str
    year: int | None
    duration_ms: int
    disc_no: int | None
    track_no: int | None

@dataclass
class Artist:
    id: ArtistId
    name: str

@dataclass
class Album:
    id: AlbumId
    title: str

@dataclass
class SearchResult:
    artists: tuple[Artist, ...]
    albums: tuple[Album, ...]
    tracks: tuple[Track, ...]
```

**Example — search for tracks:**

```python
result = await ctx.music_library.search("Beethoven", limit=50)
for track in result.tracks:
    logger.info("%s — %s (%s)", track.artist_name, track.title, track.album_title)
```

### PlayerRegistry

`ctx.player_registry` — access connected Squeezebox players.

| Method             | Signature                                      | Returns          | Description                      |
| ------------------ | ---------------------------------------------- | ---------------- | -------------------------------- |
| `get_all`          | `async () -> list[PlayerClient]`               | List of players  | All connected players            |
| `get_by_mac`       | `async (mac: str) -> PlayerClient \| None`     | Player or `None` | Look up by MAC address           |
| `get_by_ip`        | `async (ip: str) -> PlayerClient \| None`      | Player or `None` | Look up by IP (first match)      |
| `get_by_name`      | `async (name: str) -> PlayerClient \| None`    | Player or `None` | Look up by display name          |
| `get_sync_buddies` | `async (player_id: str) -> list[PlayerClient]` | List of players  | Players synced with given player |
| `get_sync_groups`  | `async () -> list[list[PlayerClient]]`         | List of groups   | All sync groups (master first)   |
| `__len__`          | `() -> int`                                    | Integer          | Number of connected players      |
| `__contains__`     | `(mac: str) -> bool`                           | Boolean          | Check if MAC is registered       |

**Key attributes on `PlayerClient`:**

| Attribute             | Type                 | Description                        |
| --------------------- | -------------------- | ---------------------------------- |
| `mac_address`         | `str`                | Player MAC (e.g. `"00:04:20:..."`) |
| `name`                | `str \| None`        | Display name                       |
| `model`               | `str`                | Model identifier                   |
| `ip_address`          | `str`                | Player IP                          |
| `device_capabilities` | `DeviceCapabilities` | Hardware capabilities              |

**Example — list all players:**

```python
players = await ctx.player_registry.get_all()
for p in players:
    logger.info("Player: %s (%s) at %s", p.name, p.model, p.ip_address)
```

### PlaylistManager

`ctx.playlist_manager` — access player playlists.

| Method    | Signature                              | Returns           | Description                   |
| --------- | -------------------------------------- | ----------------- | ----------------------------- |
| `get`     | `(player_id: str) -> Playlist`         | `Playlist`        | Get or create player playlist |
| `remove`  | `(player_id: str) -> Playlist \| None` | Removed or `None` | Remove a player's playlist    |
| `__len__` | `() -> int`                            | Integer           | Number of active playlists    |

**Key attributes on `Playlist`:**

| Attribute       | Type                    | Description              |
| --------------- | ----------------------- | ------------------------ |
| `player_id`     | `str`                   | Owner player MAC         |
| `current_track` | `PlaylistTrack \| None` | Currently playing track  |
| `current_index` | `int`                   | Index in the track list  |
| `__len__`       | `int`                   | Total tracks in playlist |

**⚠️ Caution:** `playlist_manager` can be `None` (e.g. in tests).
Always check before use:

```python
if ctx.playlist_manager:
    playlist = ctx.playlist_manager.get("aa:bb:cc:dd:ee:ff")
    logger.info("Queue has %d tracks", len(playlist))
```

---

## 12) Testing

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

| Convention | Description                                                 |
| ---------- | ----------------------------------------------------------- |
| File name  | `tests/test_<plugin>_plugin.py`                             |
| Classes    | `TestClassName` — group by feature/handler                  |
| Fixture    | `<plugin>_env` — sets up module state, cleans up after test |
| Assertions | Always check `"error" not in result` or `"error" in result` |

---

## 13) Best Practices

### Do

| Rule                                                    | Reason                                    |
| ------------------------------------------------------- | ----------------------------------------- |
| ✅ Always use `from __future__ import annotations`      | Avoids circular imports with type hints   |
| ✅ `TYPE_CHECKING` guard for imports                    | Faster import, no runtime dependencies    |
| ✅ `ctx.subscribe()` instead of `event_bus.subscribe()` | Auto-cleanup on teardown                  |
| ✅ `ensure_data_dir()` before file I/O                  | Directory is guaranteed to exist          |
| ✅ Atomic write (tmp + rename)                          | No data loss on crash                     |
| ✅ Logging with `logger = logging.getLogger(__name__)`  | Logger name = module name → filterable    |
| ✅ Check `playlist_manager` for `None`                  | Not always available (tests!)             |
| ✅ Parameter parsing for both `str` and `dict`          | Cometd sends dicts, CLI sends `key:value` |
| ✅ Write your own tests (target: 90%+ coverage)         | Regression protection                     |

### Don't

| Anti-Pattern                                        | Problem                                           |
| --------------------------------------------------- | ------------------------------------------------- |
| ❌ Override built-in commands                       | `RuntimeError` — protection is intentional        |
| ❌ Use `event_bus.subscribe()` directly             | No auto-cleanup → memory leak                     |
| ❌ Synchronous I/O in handlers                      | Blocks the event loop → server stalls             |
| ❌ Global variables without reset in `teardown()`   | State leaks between server restarts               |
| ❌ Not catching exceptions in `teardown()`          | Can block cleanup of other plugins                |
| ❌ Directly importing and mutating server internals | Breaks on refactors; only use `PluginContext` API |

### Naming Conventions

| Element                   | Convention                | Example                     |
| ------------------------- | ------------------------- | --------------------------- |
| Plugin directory          | `lowercase`, `[a-z0-9_-]` | `plugins/my_radio/`         |
| Command (plugin-specific) | `<plugin>.<action>`       | `"myradio.search"`          |
| Command (LMS-compatible)  | Single name               | `"favorites"`               |
| Menu node ID              | camelCase                 | `"myRadio"`                 |
| Event type                | `<namespace>.<event>`     | `"myradio.station_changed"` |

---

## 14) Debugging & Logging

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
python scripts/rpc-console.py
> myplugin.hello
```

---

## 15) Error Handling & Isolation

### Setup Errors

If `setup()` raises an exception:

1. All **already-made** registrations (commands, menus, events)
   are automatically rolled back (`ctx._cleanup()`).
2. The error message is stored in the plugin's runtime state.
3. The error is logged.
4. **Other plugins continue to start normally.**

```
ERROR Failed to start plugin 'broken_plugin': ValueError: something went wrong
WARNING Cleanup after failed start of 'broken_plugin': ...
INFO  Started plugin: good_plugin v1.0.0 — ...
```

#### Error Visibility in the API

The `list_plugin_info()` response (used by both the JSON-RPC `pluginmanager list`
command and the REST `GET /api/plugins` endpoint) includes an `error` field for
each plugin:

| Field     | Type           | Description                                                        |
| --------- | -------------- | ------------------------------------------------------------------ |
| `error`   | `string\|null` | `null` when healthy; error message when `setup()` or import failed |
| `started` | `bool`         | `true` only when `setup()` completed without exception             |

This covers two failure modes:

- **Import errors** — the plugin module could not be imported (syntax error,
  missing dependency). The error message starts with `"Failed to load: …"`.
- **Setup errors** — the module imported but `setup()` raised an exception.
  The error message starts with `"Failed to start: …"`.

The Web UI (`PluginsView`) uses these fields to show:

- A **red "error" badge** and the error message when `error` is non-null.
- A **yellow warning** ("Plugin is enabled but not running") when
  `started` is `false` but `state` is `"enabled"` and there is no error
  (e.g. after install, before server restart).

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

## 16) Known Limitations

| Limitation                                 | Details                                                                                 | Planned Solution                    |
| ------------------------------------------ | --------------------------------------------------------------------------------------- | ----------------------------------- |
| No hot-reload                              | Plugins cannot be loaded/unloaded at runtime                                            | Server restart required             |
| No sandbox/security                        | Plugins run in the same process, full Python access                                     | Accepted (same as LMS)              |
| FastAPI routes not removable               | `register_route()` is permanent                                                         | Framework limitation                |
| Enable/disable/install is restart-oriented | State changes set `restart_required`; running plugin instances are not hot-swapped      | Explicit restart workflow in UI/API |
| `playlist_manager` optional                | Can be `None` in tests                                                                  | Always check                        |
| Content provider commands are per-plugin   | No generic `content.browse` — each plugin defines its own commands (e.g. `radio items`) | By design                           |

---

## 17) Content Providers

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

| Method / Property   | Signature                                       | Description                                   |
| ------------------- | ----------------------------------------------- | --------------------------------------------- |
| `name`              | `@property -> str`                              | Human-readable name (e.g. `"Internet Radio"`) |
| `icon`              | `@property -> str \| None`                      | Optional icon URL for top-level menu          |
| `browse`            | `async (path: str = "") -> list[BrowseItem]`    | Browse content tree (empty = root)            |
| `search`            | `async (query: str) -> list[BrowseItem]`        | Search for items by text                      |
| `get_stream_info`   | `async (item_id: str) -> StreamInfo \| None`    | Resolve item to stream URL                    |
| `on_stream_started` | `async (item_id: str, player_mac: str) -> None` | Called when playback starts (optional)        |
| `on_stream_stopped` | `async (item_id: str, player_mac: str) -> None` | Called when playback stops (optional)         |

All methods are `async` — providers are expected to make HTTP calls
to external APIs.

### StreamInfo

Import: `from resonance.content_provider import StreamInfo`

Frozen dataclass returned by `get_stream_info()`:

| Field          | Type          | Default        | Description                             |
| -------------- | ------------- | -------------- | --------------------------------------- |
| `url`          | `str`         | _(required)_   | Direct audio stream URL (HTTP or HTTPS) |
| `content_type` | `str`         | `"audio/mpeg"` | MIME type of the stream                 |
| `title`        | `str`         | `""`           | Display title                           |
| `artist`       | `str`         | `""`           | Artist or show name                     |
| `album`        | `str`         | `""`           | Album or category                       |
| `artwork_url`  | `str \| None` | `None`         | Cover art URL                           |
| `duration_ms`  | `int`         | `0`            | Duration (0 = live/unknown)             |
| `bitrate`      | `int`         | `0`            | Bitrate in kbps                         |
| `is_live`      | `bool`        | `False`        | `True` for infinite live streams        |
| `extra`        | `dict`        | `{}`           | Provider-specific metadata              |

### BrowseItem

Import: `from resonance.content_provider import BrowseItem`

Frozen dataclass representing one entry in a browse tree:

| Field      | Type                       | Default      | Description                                           |
| ---------- | -------------------------- | ------------ | ----------------------------------------------------- |
| `id`       | `str`                      | _(required)_ | Provider-scoped unique ID                             |
| `title`    | `str`                      | _(required)_ | Display text                                          |
| `type`     | `str`                      | `"audio"`    | `"audio"`, `"folder"`, or `"search"`                  |
| `url`      | `str \| None`              | `None`       | Hint URL (authoritative URL from `get_stream_info()`) |
| `icon`     | `str \| None`              | `None`       | Icon / artwork URL                                    |
| `subtitle` | `str \| None`              | `None`       | Secondary text                                        |
| `items`    | `list[BrowseItem] \| None` | `None`       | Pre-loaded children (for small static sub-menus)      |
| `extra`    | `dict`                     | `{}`         | Provider-specific data                                |

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

| Method                                  | Description                                                                |
| --------------------------------------- | -------------------------------------------------------------------------- |
| `browse(provider_id, path)`             | Delegate to provider with error handling                                   |
| `search(provider_id, query)`            | Delegate to provider with error handling                                   |
| `get_stream_info(provider_id, item_id)` | Delegate to provider with error handling                                   |
| `search_all(query)`                     | Search across all providers, returns `dict[provider_id, list[BrowseItem]]` |
| `list_providers()`                      | All `(id, provider)` pairs                                                 |
| `provider_ids`                          | List of registered IDs                                                     |

All wrapper methods catch exceptions from providers and return empty
results / `None` — a failing provider does not crash the server.

---

## 18) Reference Plugins

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

Most feature-rich plugin — podcast browsing, search, streaming, and full SDUI dashboard:

- 1 command (`podcast`) with 13 sub-commands (`items`, `search`, `play`, `addshow`, `delshow`, `markplayed`, `markunplayed`, `opmlimport`, `opmlexport`, `trending`, `info`, `skip`, `stats`)
- 1 menu node ("Podcasts" at weight 50)
- ContentProvider registered as `"podcast"` (`browse`, `search`, `get_stream_info`, `on_stream_started`, `on_stream_stopped`)
- RSS 2.0 feed parser with iTunes namespace support (`feed_parser.py`)
- Multi-provider search: PodcastIndex, gPodder, iTunes (`providers.py`)
- Subscription management with ordering (move up/down via `store.move_subscription()`)
- Resume position tracking with progress percentages
- Recently played episodes (configurable LRU)
- What's New aggregation, Continue Listening, background feed refresh
- OPML import (URL or file path) and export (`opml.py`)
- Auto mark played at configurable threshold
- **SDUI dashboard** — 5 tabs (Subscriptions, Recent, Continue, Settings, About) with 15 actions including play-from-SDUI via JSON-RPC self-call, OPML import from URL, subscription reordering
- JSON persistence with atomic writes (`store.py`)
- "Add to Favorites" context menu via `jivefavorites add`
- 334 tests

**Ideal as a reference for ContentProvider plugins with persistence,
subscription management, SDUI dashboards with play functionality,
OPML interchange, and multi-provider API integration.**

### Now Playing Tutorial Plugin (`plugins/nowplaying/`)

Companion code for the [Plugin Tutorial](PLUGIN_TUTORIAL.md):

- 2 commands (`nowplaying.stats`, `nowplaying.recent`)
- 1 menu node ("Now Playing Stats")
- Event subscription (`player.track_started`)
- JSON persistence store
- **SDUI dashboard** — 3 tabs (Stats, Settings, About) with clear and save_settings actions
- ~300 lines of plugin code
- 92 tests

**Ideal as a learning companion — built step by step in the tutorial.**

---

## 19) Server-Driven UI (SDUI)

Plugins can expose pages in the web UI without writing any JavaScript.
The plugin describes its UI as a Python data structure (a tree of typed
components). The frontend has a generic renderer that maps each component
`type` to a Svelte widget.

**Plugins supply data. The frontend supplies presentation.**

---

### Overview

```
Plugin (Python)           Server              Frontend (Svelte)
─────────────────         ──────              ──────────────────
get_ui(ctx) → Page   →   GET /api/plugins/{id}/ui   →   PluginRenderer
                              ↓ JSON                       ↓
                         { components: [...] }        Recursive render
                                                      (Card, Table, Button, …)

handle_action(action, params)  ←  POST /api/plugins/{id}/actions/{action}

ctx.notify_ui_update()   →   SSE: {"event": "ui_refresh"}  →  Re-fetch UI
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
async def handle_action(action: str, params: dict, ctx: PluginContext) -> dict:
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

---

### Widget Reference

Every widget is documented with its full constructor signature, all
props, types, defaults, validation rules, and what it renders as.

Import all widgets from `resonance.ui`:

```python
from resonance.ui import (
    # Display
    Heading, Text, StatusBadge, KeyValue, KVItem, Markdown, Progress,
    # Data
    Table, TableColumn, TableAction,
    # Actions
    Button,
    # Layout
    Card, Row, Column, Tabs, Tab,
    # Modal
    Modal,
    # Forms
    Form, TextInput, Textarea, NumberInput, Select, SelectOption, Toggle,
    # Page envelope
    Page,
)
```

---

#### `Heading`

Section heading. Renders as `<h1>` through `<h4>` depending on level.

```python
Heading(text: str, level: int = 2)
```

| Prop    | Type  | Default | Description                                     |
| ------- | ----- | ------- | ----------------------------------------------- |
| `text`  | `str` | —       | Heading text (required)                         |
| `level` | `int` | `2`     | Heading level: 1 (largest) through 4 (smallest) |

**Rendering:**

| Level | HTML   | Style                                   |
| ----- | ------ | --------------------------------------- |
| 1     | `<h1>` | 2xl, bold                               |
| 2     | `<h2>` | xl, semibold                            |
| 3     | `<h3>` | sm, semibold, uppercase, tracking-wider |
| 4     | `<h4>` | sm, medium                              |

**Example:**

```python
Heading("Device Configuration", level=2)
```

---

#### `Text`

Paragraph text with optional color and size.

```python
Text(content: str, color: str | None = None, size: str | None = None)
```

| Prop      | Type          | Default | Validation                                         | Description  |
| --------- | ------------- | ------- | -------------------------------------------------- | ------------ |
| `content` | `str`         | —       | Required                                           | Text content |
| `color`   | `str \| None` | `None`  | Must be a [valid color](#colors-styles--constants) | Text color   |
| `size`    | `str \| None` | `None`  | `"sm"`, `"md"`, or `"lg"`                          | Font size    |

**Size mapping:**

| Value  | CSS class   | Description |
| ------ | ----------- | ----------- |
| `"sm"` | `text-sm`   | Small       |
| `"md"` | `text-base` | Medium      |
| `"lg"` | `text-lg`   | Large       |

**Example:**

```python
Text("Bridge is currently inactive.", color="yellow", size="sm")
```

---

#### `StatusBadge`

Colored pill-shaped status indicator with a dot.

```python
StatusBadge(label: str, status: str | None = None, color: str = "gray")
```

| Prop     | Type          | Default  | Validation                                         | Description                         |
| -------- | ------------- | -------- | -------------------------------------------------- | ----------------------------------- |
| `label`  | `str`         | —        | Required                                           | Display text                        |
| `status` | `str \| None` | `None`   | —                                                  | Status string (defaults to `label`) |
| `color`  | `str`         | `"gray"` | Must be a [valid color](#colors-styles--constants) | Badge color                         |

**Renders as:** A rounded pill with a small colored dot and the label text.

**Example:**

```python
StatusBadge(label="Active", color="green")
StatusBadge(label="Stopped", color="red")
StatusBadge(label="3 devices", status="ok", color="blue")
```

---

#### `KeyValue`

Vertically stacked list of key-value pairs. Each row shows a label on
the left and a value on the right.

```python
KeyValue(items: list[KVItem])
```

| Prop    | Type           | Default | Description             |
| ------- | -------------- | ------- | ----------------------- |
| `items` | `list[KVItem]` | —       | List of key-value pairs |

**`KVItem` (frozen dataclass):**

```python
KVItem(key: str, value: str, color: str | None = None)
```

| Field   | Type          | Default | Description                          |
| ------- | ------------- | ------- | ------------------------------------ |
| `key`   | `str`         | —       | Label (left side)                    |
| `value` | `str`         | —       | Value (right side)                   |
| `color` | `str \| None` | `None`  | Value text color (valid color value) |

**Example:**

```python
KeyValue(items=[
    KVItem("Status", "Active", color="green"),
    KVItem("Binary", "/usr/bin/squeeze2raop"),
    KVItem("Server", "192.168.1.1:9000"),
    KVItem("Uptime", "3h 42m", color="blue"),
])
```

---

#### `Table`

Data table with column definitions, row data, and optional inline
editing and action buttons.

```python
Table(
    columns: list[TableColumn],
    rows: list[dict[str, Any]],
    title: str | None = None,
    edit_action: str | None = None,
    row_key: str = "udn",
)
```

| Prop          | Type                   | Default | Description                                    |
| ------------- | ---------------------- | ------- | ---------------------------------------------- |
| `columns`     | `list[TableColumn]`    | —       | Column definitions (required)                  |
| `rows`        | `list[dict[str, Any]]` | —       | Row data where keys match column `key` values  |
| `title`       | `str \| None`          | `None`  | Table heading above the table                  |
| `edit_action` | `str \| None`          | `None`  | Plugin action dispatched on inline edit commit |
| `row_key`     | `str`                  | `"udn"` | Column key that uniquely identifies rows       |

`edit_action` and `row_key` are only serialized when `edit_action` is set.

**`TableColumn` (frozen dataclass):**

```python
TableColumn(key: str, label: str, variant: str = "text")
```

| Field     | Type  | Default  | Description                          |
| --------- | ----- | -------- | ------------------------------------ |
| `key`     | `str` | —        | Column identifier (matches row keys) |
| `label`   | `str` | —        | Column header text                   |
| `variant` | `str` | `"text"` | Rendering variant (see table below)  |

**Column variants:**

| Variant      | Description                     | Cell Value Format                                       |
| ------------ | ------------------------------- | ------------------------------------------------------- |
| `"text"`     | Plain text (default)            | `str`                                                   |
| `"badge"`    | Colored badge pill              | `{"text": "Yes", "color": "green"}`                     |
| `"actions"`  | Action buttons per row          | `[TableAction(...).to_dict(), ...]`                     |
| `"editable"` | Click-to-edit inline text field | `str` (current value; editable if `edit_action` is set) |

**`TableAction` (frozen dataclass):**

```python
TableAction(
    label: str,
    action: str,
    params: dict[str, Any] | None = None,
    style: str = "secondary",
    confirm: bool = False,
)
```

| Field     | Type           | Default       | Description                        |
| --------- | -------------- | ------------- | ---------------------------------- |
| `label`   | `str`          | —             | Button text                        |
| `action`  | `str`          | —             | Plugin action name                 |
| `params`  | `dict \| None` | `None`        | Params merged into action dispatch |
| `style`   | `str`          | `"secondary"` | Button style (valid button style)  |
| `confirm` | `bool`         | `False`       | Show browser confirmation dialog   |

**Inline editing behaviour:**

When a column has `variant="editable"`, clicking the cell opens an inline
text input. On Enter or blur, the frontend dispatches:

```python
# Sent to handle_action(edit_action, params):
params = {
    "<row_key>": "<row[row_key] value>",  # e.g. "udn": "uuid-123"
    "<col.key>": "<new_value>",           # e.g. "name": "Kitchen"
}
```

Pressing Escape cancels. Only changed values trigger a dispatch.

**Example:**

```python
Table(
    title="Detected Devices",
    columns=[
        TableColumn(key="name", label="Name", variant="editable"),
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
    edit_action="update_device",
    row_key="udn",
)
```

---

#### `Button`

Action button that triggers a POST to the plugin's action endpoint.

```python
Button(
    label: str,
    action: str,
    params: dict[str, Any] | None = None,
    style: str = "secondary",
    confirm: bool = False,
    icon: str | None = None,
    disabled: bool = False,
)
```

| Prop       | Type           | Default       | Validation            | Description                                 |
| ---------- | -------------- | ------------- | --------------------- | ------------------------------------------- |
| `label`    | `str`          | —             | Required              | Button text                                 |
| `action`   | `str`          | —             | Required              | Action name sent to `handle_action()`       |
| `params`   | `dict \| None` | `None`        | —                     | Params dict sent with the action            |
| `style`    | `str`          | `"secondary"` | Must be a valid style | Visual style (see [Styles](#button-styles)) |
| `confirm`  | `bool`         | `False`       | —                     | Show browser `confirm()` dialog first       |
| `icon`     | `str \| None`  | `None`        | —                     | Lucide icon name rendered before the label  |
| `disabled` | `bool`         | `False`       | —                     | Disable the button                          |

**Renders as:** A rounded button with loading state during dispatch.
While the action is in flight, a spinning `Loader2` icon replaces the
button icon and the button is disabled. The label text remains visible.

**Example:**

```python
Button("Start Bridge", action="activate", style="primary")
Button("Delete All", action="delete_all", style="danger", confirm=True)
Button("Refresh", action="refresh", params={"force": True})
```

---

#### `Card`

Grouped content container with an optional title and optional
expand/collapse behavior.

```python
Card(
    title: str = "",
    children: list[UIComponent] | None = None,
    collapsible: bool = False,
    collapsed: bool = False,
)
```

| Prop          | Type                        | Default | Description                                     |
| ------------- | --------------------------- | ------- | ----------------------------------------------- |
| `title`       | `str`                       | `""`    | Card header text                                |
| `children`    | `list[UIComponent] \| None` | `None`  | Child widgets rendered inside the card          |
| `collapsible` | `bool`                      | `False` | Show chevron toggle in header                   |
| `collapsed`   | `bool`                      | `False` | Initial collapsed state (only when collapsible) |

**Serialization:** `collapsible` and `collapsed` are only emitted when
`collapsible=True`, keeping payloads compact. Setting `collapsed=True`
without `collapsible=True` is silently ignored.

**Renders as:** A rounded bordered container with subtle background.
When collapsible, the header becomes a clickable toggle with an
animated chevron (▼ expanded, ► collapsed).

**Example:**

```python
# Always expanded
Card(title="Status", children=[
    StatusBadge(label="Running", color="green"),
])

# Collapsible, starts expanded
Card(title="Details", collapsible=True, children=[...])

# Collapsible, starts collapsed
Card(title="Advanced Options", collapsible=True, collapsed=True, children=[...])
```

---

#### `Row`

Horizontal flex layout container.

```python
Row(
    children: list[UIComponent] | None = None,
    gap: str = "4",
    justify: str | None = None,
    align: str | None = None,
)
```

| Prop       | Type                        | Default | Allowed Values                              | Description             |
| ---------- | --------------------------- | ------- | ------------------------------------------- | ----------------------- |
| `children` | `list[UIComponent] \| None` | `None`  | —                                           | Child widgets           |
| `gap`      | `str`                       | `"4"`   | Tailwind spacing values (`"2"`, `"4"`, etc) | Gap between children    |
| `justify`  | `str \| None`               | `None`  | `"start"`, `"center"`, `"end"`, `"between"` | Horizontal distribution |
| `align`    | `str \| None`               | `None`  | `"start"`, `"center"`, `"end"`, `"stretch"` | Vertical alignment      |

**Renders as:** A `flex flex-wrap` container. Children wrap on small screens.

**Example:**

```python
Row(children=[
    Button("Save", action="save", style="primary"),
    Button("Cancel", action="cancel"),
], gap="3", justify="end")
```

---

#### `Column`

Vertical flex layout container.

```python
Column(children: list[UIComponent] | None = None, gap: str = "4")
```

| Prop       | Type                        | Default | Description                |
| ---------- | --------------------------- | ------- | -------------------------- |
| `children` | `list[UIComponent] \| None` | `None`  | Child widgets              |
| `gap`      | `str`                       | `"4"`   | Vertical gap between items |

**Renders as:** A `flex flex-col` container.

---

#### `Alert`

Colored banner for informational messages, warnings, errors, or success.

```python
Alert(message: str, severity: str = "info", title: str | None = None)
```

| Prop       | Type          | Default  | Validation                                    | Description       |
| ---------- | ------------- | -------- | --------------------------------------------- | ----------------- |
| `message`  | `str`         | —        | Required                                      | Alert body text   |
| `severity` | `str`         | `"info"` | `"info"`, `"warning"`, `"error"`, `"success"` | Visual variant    |
| `title`    | `str \| None` | `None`   | —                                             | Bold heading line |

**Renders as:** A rounded bordered box with colored background and
border matching the severity. Title (if present) is rendered bold
above the message.

**Example:**

```python
Alert(message="Bridge will restart. Active streams will be interrupted.",
      severity="warning", title="Warning")
Alert(message="Settings saved successfully.", severity="success")
```

---

#### `Progress`

Horizontal progress bar (0–100%).

```python
Progress(value: int | float, label: str | None = None, color: str | None = None)
```

| Prop    | Type           | Default | Validation            | Description                 |
| ------- | -------------- | ------- | --------------------- | --------------------------- |
| `value` | `int \| float` | —       | Clamped to 0–100      | Progress percentage         |
| `label` | `str \| None`  | `None`  | —                     | Text label above the bar    |
| `color` | `str \| None`  | `None`  | Must be a valid color | Bar color (default: accent) |

**Renders as:** A thin rounded bar. When `label` is set, the label and
percentage are shown side-by-side above the bar.

**Example:**

```python
Progress(value=65, label="Download Progress", color="blue")
```

---

#### `Markdown`

Rendered markdown text block with full [GFM](https://github.github.com/gfm/)
support.

```python
Markdown(content: str)
```

| Prop      | Type  | Default | Description          |
| --------- | ----- | ------- | -------------------- |
| `content` | `str` | —       | Markdown source text |

**Supported syntax:** Headings, bold, italic, strikethrough, links,
images, inline code, fenced code blocks (with language label),
blockquotes, ordered and unordered lists, tables, horizontal rules, and
line breaks. All elements are styled to match the Catppuccin theme.

**Renders as:** Themed HTML produced by a custom `marked` renderer.
Each Markdown element maps to a specific set of Tailwind classes (e.g.
headings use the same hierarchy as the `Heading` widget, code blocks get
`bg-crust` with monospace font, links are accent-colored with
`target="_blank"`).

**Security model:** The renderer uses `marked` with **raw HTML disabled**
(the default — `options.html` is never set to `true`). Any HTML tags in
the Markdown source are escaped to `&lt;` / `&gt;` by the parser before
they reach the renderer. The custom `Renderer` only emits known-safe
HTML elements with explicitly constructed attributes. All attribute
values pass through `escapeAttr()`, and URLs are validated against an
allowlist of safe schemes (`http:`, `https:`, `mailto:`) plus relative
paths. This is equivalent to a strict allowlist sanitizer but without
the overhead of parsing untrusted HTML.

**Example:**

```python
Markdown(content="""
## About This Plugin

AirPlay Bridge for **Resonance** — uses philippe44's
[squeeze2raop](https://github.com/philippe44/AirConnect) binary.

### Features

- Automatic device discovery
- Per-device volume control
- `enabled` / `disabled` state per device

> Note: Requires network access to AirPlay receivers.

| Setting   | Default |
|-----------|---------|
| Port      | 49152   |
| Interface | 0.0.0.0 |
""")
```

---

#### `Tabs`

Tab-based navigation — purely client-side, no backend roundtrip on
tab switch. The frontend renders tab buttons and shows the active tab's
content.

```python
Tabs(tabs: list[Tab])
```

| Prop   | Type        | Default | Validation                | Description     |
| ------ | ----------- | ------- | ------------------------- | --------------- |
| `tabs` | `list[Tab]` | —       | At least one tab required | Tab definitions |

**`Tab` (frozen dataclass):**

```python
Tab(label: str, children: list[UIComponent] = [], icon: str | None = None)
```

| Field      | Type                | Default | Description                              |
| ---------- | ------------------- | ------- | ---------------------------------------- |
| `label`    | `str`               | —       | Tab button text                          |
| `children` | `list[UIComponent]` | `[]`    | Widgets rendered when this tab is active |
| `icon`     | `str \| None`       | `None`  | Lucide icon name next to the label       |

**Renders as:** Horizontal tab bar with an accent-colored underline on the
active tab. Icons render at 16px via `DynamicIcon`.

**Example:**

```python
Tabs(tabs=[
    Tab(label="Status", icon="activity", children=[
        Card(title="Bridge Status", children=[...]),
    ]),
    Tab(label="Devices", icon="speaker", children=[
        Table(columns=[...], rows=[...]),
    ]),
    Tab(label="Settings", icon="settings", children=[
        Form(action="save_settings", children=[...]),
    ]),
])
```

---

#### `Modal`

Modal dialog overlay triggered by a button. The modal appears as a
centered overlay with backdrop blur.

```python
Modal(
    title: str,
    trigger_label: str,
    children: list[UIComponent] | None = None,
    trigger_style: str = "secondary",
    trigger_icon: str | None = None,
    size: str = "md",
)
```

| Prop            | Type                        | Default       | Validation                     | Description                                         |
| --------------- | --------------------------- | ------------- | ------------------------------ | --------------------------------------------------- |
| `title`         | `str`                       | —             | Non-empty required             | Modal header text                                   |
| `trigger_label` | `str`                       | —             | Non-empty required             | Text on the button that opens the modal             |
| `children`      | `list[UIComponent] \| None` | `None`        | —                              | Content rendered inside the modal body              |
| `trigger_style` | `str`                       | `"secondary"` | Valid button style             | Trigger button appearance                           |
| `trigger_icon`  | `str \| None`               | `None`        | —                              | Icon on the trigger button (not rendered currently) |
| `size`          | `str`                       | `"md"`        | `"sm"`, `"md"`, `"lg"`, `"xl"` | Modal width                                         |

**Size mapping:**

| Value  | CSS class   | Width |
| ------ | ----------- | ----- |
| `"sm"` | `max-w-sm`  | 384px |
| `"md"` | `max-w-lg`  | 512px |
| `"lg"` | `max-w-2xl` | 672px |
| `"xl"` | `max-w-4xl` | 896px |

**Behaviour:**

- Clicking the trigger button opens the modal.
- Clicking the backdrop (dark area) closes the modal.
- Pressing Escape closes the modal.
- **Focus trap:** Tab and Shift+Tab cycle through focusable elements
  inside the modal without escaping to the page behind it.
- **Autofocus:** On open, the first focusable element inside the modal
  receives focus (or the modal container itself if there are none).
- **Focus restore:** On close, focus returns to the element that opened
  the modal (the trigger button).
- The modal body scrolls independently (max 85vh).
- Modals can contain any widget: `Form`, `Table`, `KeyValue`, nested `Tabs`, etc.

**Example:**

```python
Modal(
    title="Device Settings — Living Room",
    trigger_label="Configure",
    trigger_style="secondary",
    size="lg",
    children=[
        Form(
            action="update_device",
            submit_label="Save",
            children=[
                TextInput(name="name", label="Display Name", value="Living Room"),
                Select(name="volume_mode", label="Volume", value="2", options=[
                    SelectOption(value="2", label="Hardware"),
                    SelectOption(value="1", label="Software"),
                ]),
            ],
        ),
    ],
)
```

---

#### `Form`

Container that groups input widgets and submits their collected values
as a single action. The submit button tracks dirty state and disables
until a value changes.

```python
Form(
    action: str,
    children: list[UIComponent] | None = None,
    submit_label: str = "Save",
    submit_style: str = "primary",
    disabled: bool = False,
)
```

| Prop           | Type                        | Default     | Validation         | Description                               |
| -------------- | --------------------------- | ----------- | ------------------ | ----------------------------------------- |
| `action`       | `str`                       | —           | Non-empty required | Action name dispatched on submit          |
| `children`     | `list[UIComponent] \| None` | `None`      | —                  | Form input widgets + any display widgets  |
| `submit_label` | `str`                       | `"Save"`    | —                  | Text on the submit button                 |
| `submit_style` | `str`                       | `"primary"` | Valid button style | Submit button appearance                  |
| `disabled`     | `bool`                      | `False`     | —                  | Disable entire form (all inputs + submit) |

**Behaviour:**

- On submit, all child input values (identified by `name`) are collected
  into a `params` dict and sent to `POST /api/plugins/{id}/actions/{action}`.
- The submit button is disabled until the user changes a value (dirty tracking).
- After successful submission, dirty state resets.
- Shows a spinner ("Saving…") during dispatch.
- Shows "Unsaved changes" indicator when dirty.
- Individual inputs can also be independently disabled via their own `disabled` prop.

**Submission payload example:**

```python
# If the form contains:
#   TextInput(name="interface", value="192.168.1.1")
#   NumberInput(name="port", value=9000)
#   Toggle(name="autostart", value=True)

# Then handle_action receives:
params = {
    "interface": "192.168.1.1",
    "port": 9000,
    "autostart": True,
}
```

---

#### `TextInput`

Single-line text input field for use inside a `Form`.

```python
TextInput(
    name: str,
    label: str,
    value: str = "",
    placeholder: str = "",
    required: bool = False,
    pattern: str | None = None,
    disabled: bool = False,
    help_text: str | None = None,
)
```

| Prop          | Type          | Default | Validation         | Description                                   |
| ------------- | ------------- | ------- | ------------------ | --------------------------------------------- |
| `name`        | `str`         | —       | Non-empty required | Field identifier in form submission           |
| `label`       | `str`         | —       | —                  | Label text above the input                    |
| `value`       | `str`         | `""`    | —                  | Initial value                                 |
| `placeholder` | `str`         | `""`    | —                  | Placeholder text                              |
| `required`    | `bool`        | `False` | —                  | Show `*` marker; validate non-empty on blur   |
| `pattern`     | `str \| None` | `None`  | Valid regex        | Regex pattern validated on the frontend       |
| `disabled`    | `bool`        | `False` | —                  | Prevent editing                               |
| `help_text`   | `str \| None` | `None`  | —                  | Hint text shown below the input (gray, small) |

**Renders as:** Label + single-line `<input type="text">` + validation
error or help text below. Required fields show a red asterisk. Validation
errors appear on blur (touched state).

**Example:**

```python
TextInput(
    name="interface",
    label="Network Interface",
    value="127.0.0.1",
    placeholder="e.g. 192.168.1.100",
    help_text="The IP address the bridge will bind to.",
)
```

---

#### `Textarea`

Multi-line text input with optional character limit.

```python
Textarea(
    name: str,
    label: str,
    value: str = "",
    placeholder: str = "",
    rows: int = 4,
    maxlength: int | None = None,
    required: bool = False,
    disabled: bool = False,
    help_text: str | None = None,
)
```

| Prop          | Type          | Default | Validation         | Description                              |
| ------------- | ------------- | ------- | ------------------ | ---------------------------------------- |
| `name`        | `str`         | —       | Non-empty required | Field identifier in form submission      |
| `label`       | `str`         | —       | —                  | Label text above the textarea            |
| `value`       | `str`         | `""`    | —                  | Initial value                            |
| `placeholder` | `str`         | `""`    | —                  | Placeholder text                         |
| `rows`        | `int`         | `4`     | Must be ≥ 1        | Visible height in text rows              |
| `maxlength`   | `int \| None` | `None`  | Must be ≥ 1 if set | Max character count (shows live counter) |
| `required`    | `bool`        | `False` | —                  | Validate non-empty on blur               |
| `disabled`    | `bool`        | `False` | —                  | Prevent editing                          |
| `help_text`   | `str \| None` | `None`  | —                  | Hint text shown below the textarea       |

**Renders as:** Label + vertically resizable `<textarea>` + character
counter (when maxlength is set) + validation error or help text.

**Example:**

```python
Textarea(
    name="notes",
    label="Notes",
    value="",
    rows=6,
    maxlength=500,
    placeholder="Optional notes...",
    help_text="Free-form notes about this configuration.",
)
```

---

#### `NumberInput`

Numeric input with optional min/max/step constraints.

```python
NumberInput(
    name: str,
    label: str,
    value: int | float = 0,
    min: int | float | None = None,
    max: int | float | None = None,
    step: int | float = 1,
    required: bool = False,
    disabled: bool = False,
    help_text: str | None = None,
)
```

| Prop        | Type                   | Default | Validation         | Description                                |
| ----------- | ---------------------- | ------- | ------------------ | ------------------------------------------ |
| `name`      | `str`                  | —       | Non-empty required | Field identifier in form submission        |
| `label`     | `str`                  | —       | —                  | Label text above the input                 |
| `value`     | `int \| float`         | `0`     | —                  | Initial value                              |
| `min`       | `int \| float \| None` | `None`  | —                  | Minimum allowed value                      |
| `max`       | `int \| float \| None` | `None`  | —                  | Maximum allowed value                      |
| `step`      | `int \| float`         | `1`     | —                  | Increment step                             |
| `required`  | `bool`                 | `False` | —                  | Validate non-empty on blur                 |
| `disabled`  | `bool`                 | `False` | —                  | Prevent editing                            |
| `help_text` | `str \| None`          | `None`  | —                  | Hint text (overrides auto-generated range) |

**Renders as:** Label + `<input type="number">` (browser spinners hidden) +
range hint or help text below. If `help_text` is not set and min/max are
provided, an automatic "Range: min – max" hint is shown.

**Example:**

```python
NumberInput(
    name="port",
    label="Port",
    value=9000,
    min=1,
    max=65535,
    help_text="TCP port for the bridge listener.",
)
```

---

#### `Select`

Dropdown selection field.

```python
Select(
    name: str,
    label: str,
    value: str = "",
    options: list[SelectOption] | None = None,
    required: bool = False,
    disabled: bool = False,
    help_text: str | None = None,
)
```

| Prop        | Type                         | Default | Validation         | Description                         |
| ----------- | ---------------------------- | ------- | ------------------ | ----------------------------------- |
| `name`      | `str`                        | —       | Non-empty required | Field identifier in form submission |
| `label`     | `str`                        | —       | —                  | Label text above the select         |
| `value`     | `str`                        | `""`    | —                  | Initially selected value            |
| `options`   | `list[SelectOption] \| None` | `None`  | —                  | Available choices                   |
| `required`  | `bool`                       | `False` | —                  | Validate non-empty on blur          |
| `disabled`  | `bool`                       | `False` | —                  | Prevent selection                   |
| `help_text` | `str \| None`                | `None`  | —                  | Hint text below the dropdown        |

**`SelectOption` (frozen dataclass):**

```python
SelectOption(value: str, label: str)
```

| Field   | Type  | Description                   |
| ------- | ----- | ----------------------------- |
| `value` | `str` | Value sent in form submission |
| `label` | `str` | Display text in the dropdown  |

**Renders as:** Label + custom-styled `<select>` with chevron indicator +
validation error or help text.

**Example:**

```python
Select(
    name="codec",
    label="Output Codec",
    value="flac",
    options=[
        SelectOption(value="flac", label="FLAC (lossless)"),
        SelectOption(value="pcm", label="PCM (raw)"),
        SelectOption(value="mp3", label="MP3"),
    ],
    help_text="Audio codec used for player output.",
)
```

---

#### `Toggle`

Boolean on/off switch.

```python
Toggle(
    name: str,
    label: str,
    value: bool = False,
    disabled: bool = False,
    help_text: str | None = None,
)
```

| Prop        | Type          | Default | Validation         | Description                         |
| ----------- | ------------- | ------- | ------------------ | ----------------------------------- |
| `name`      | `str`         | —       | Non-empty required | Field identifier in form submission |
| `label`     | `str`         | —       | —                  | Label text (left side)              |
| `value`     | `bool`        | `False` | —                  | Initial state                       |
| `disabled`  | `bool`        | `False` | —                  | Prevent toggling                    |
| `help_text` | `str \| None` | `None`  | —                  | Hint text below the toggle          |

**Renders as:** Label on the left + toggle switch on the right. The
switch uses accent color when on, overlay color when off. Has proper
ARIA `role="switch"` and `aria-checked`.

**Example:**

```python
Toggle(
    name="autostart",
    label="Auto-start at server startup",
    value=True,
    help_text="When enabled, the bridge starts automatically with the server.",
)
```

---

#### `Page`

Top-level envelope returned by `get_ui()`. Not a widget itself — it
wraps the component tree and provides metadata.

```python
Page(
    title: str,
    components: list[UIComponent] = [],
    icon: str | None = None,
    refresh_interval: int = 0,
)
```

| Prop               | Type                | Default | Description                                    |
| ------------------ | ------------------- | ------- | ---------------------------------------------- |
| `title`            | `str`               | —       | Page heading (required)                        |
| `components`       | `list[UIComponent]` | `[]`    | Top-level widgets                              |
| `icon`             | `str \| None`       | `None`  | Lucide icon name                               |
| `refresh_interval` | `int`               | `0`     | Polling fallback interval in seconds (0 = off) |

---

#### `UIComponent` (Base Class)

All widgets inherit from `UIComponent`. You normally don't use this
directly — use the convenience constructors above.

| Attribute       | Type                        | Default | Description                                                               |
| --------------- | --------------------------- | ------- | ------------------------------------------------------------------------- |
| `type`          | `str`                       | —       | Widget type identifier (from `ALLOWED_TYPES`)                             |
| `props`         | `dict[str, Any]`            | `{}`    | Widget-specific properties                                                |
| `children`      | `list[UIComponent] \| None` | `None`  | Child components (for container widgets)                                  |
| `fallback_text` | `str \| None`               | `None`  | Text shown if the widget type is unknown to frontend                      |
| `visible_when`  | `dict[str, Any] \| None`    | `None`  | Conditional visibility (see [below](#conditional-rendering-visible_when)) |

**`fallback_text`** — When the server sends a widget type that the frontend
does not recognize (e.g. a new widget added in a newer server version), the
frontend renders a gray box with `"Unknown widget: <type>"` and the
`fallback_text` if provided. This enables graceful degradation.

```python
# Hypothetical future widget with fallback for older frontends
UIComponent(
    type="chart",
    props={"data": [...]},
    fallback_text="Chart widget requires frontend update.",
)
```

---

### Wire Format Specification

This section documents the exact JSON structure returned by
`GET /api/plugins/{plugin_id}/ui`. This is the contract between
server and frontend.

#### Page Envelope

```json
{
    "schema_version": "1.0",
    "plugin_id": "myplugin",
    "title": "My Plugin",
    "icon": "star",
    "refresh_interval": 10,
    "components": [
        { "type": "...", "props": {...}, "children": [...] },
        ...
    ]
}
```

| Field              | Type     | Description                             |
| ------------------ | -------- | --------------------------------------- |
| `schema_version`   | `string` | Schema version (currently `"1.0"`)      |
| `plugin_id`        | `string` | Plugin identifier                       |
| `title`            | `string` | Page title                              |
| `icon`             | `string` | Lucide icon name (empty string if none) |
| `refresh_interval` | `int`    | Polling interval in seconds (0 = off)   |
| `components`       | `array`  | Array of component objects              |

#### Component Object

Every component serializes to:

```json
{
    "type": "button",
    "props": {
        "label": "Restart",
        "action": "restart",
        "style": "danger",
        "confirm": true
    }
}
```

With children (containers like `Card`, `Row`, `Form`, etc.):

```json
{
    "type": "card",
    "props": { "title": "Status" },
    "children": [
        {
            "type": "status_badge",
            "props": { "label": "Active", "status": "Active", "color": "green" }
        },
        {
            "type": "key_value",
            "props": { "items": [{ "key": "Version", "value": "1.0" }] }
        }
    ]
}
```

With `visible_when` (inside a Form):

```json
{
    "type": "text_input",
    "props": { "name": "debug_path", "label": "Debug Path", "value": "" },
    "visible_when": { "field": "debug_enabled", "value": true }
}
```

With `fallback_text`:

```json
{
    "type": "future_widget",
    "props": { ... },
    "fallback_text": "Upgrade your frontend to see this widget."
}
```

#### Compact Serialization Rules

The Python `to_dict()` methods follow these rules to minimize payload size:

| Field           | Omitted when                             |
| --------------- | ---------------------------------------- |
| `children`      | `None` or empty list                     |
| `fallback_text` | `None`                                   |
| `visible_when`  | `None`                                   |
| `collapsible`   | `False` (Card)                           |
| `collapsed`     | `False` or `collapsible=False` (Card)    |
| `variant`       | `"text"` (TableColumn)                   |
| `style`         | `"secondary"` (TableAction)              |
| `confirm`       | `False` (TableAction, Button)            |
| `params`        | `None` or empty (Button, TableAction)    |
| `icon`          | `None` (Button, Tab)                     |
| `disabled`      | `False` (Button, Form, inputs)           |
| `help_text`     | `None` (all form inputs)                 |
| `operator`      | `"eq"` (visible_when — default operator) |

#### Complete Allowed Types

```
heading, text, status_badge, key_value, table, button,
card, row, column, alert, progress, markdown,
tabs, form, text_input, textarea, number_input, select, toggle,
modal
```

Total: **20 widget types**.

---

### Colors, Styles & Constants

#### Color Values

All color props (`StatusBadge.color`, `KVItem.color`, `Text.color`,
`Progress.color`, badge cells in `Table`) accept:

| Value      | Semantic | CSS Effect                  |
| ---------- | -------- | --------------------------- |
| `"green"`  | Success  | Green badge/text/bar        |
| `"red"`    | Error    | Red badge/text/bar          |
| `"yellow"` | Warning  | Yellow/amber badge/text/bar |
| `"blue"`   | Accent   | Blue/accent badge/text/bar  |
| `"gray"`   | Neutral  | Gray/muted badge/text/bar   |

Invalid colors raise `ValueError` on the Python side.

#### Button Styles

Used by `Button.style`, `Form.submit_style`, `Modal.trigger_style`,
and `TableAction.style`:

| Value         | Appearance                                |
| ------------- | ----------------------------------------- |
| `"primary"`   | Accent-colored background, crust text     |
| `"secondary"` | Surface background, normal text (default) |
| `"danger"`    | Red/error background, crust text          |

Invalid styles raise `ValueError`.

#### Alert Severities

| Value       | Appearance                 |
| ----------- | -------------------------- |
| `"info"`    | Accent/blue tint (default) |
| `"warning"` | Yellow/amber tint          |
| `"error"`   | Red tint                   |
| `"success"` | Green tint                 |

#### Modal Sizes

| Value  | CSS Class   | Max Width |
| ------ | ----------- | --------- |
| `"sm"` | `max-w-sm`  | 384px     |
| `"md"` | `max-w-lg`  | 512px     |
| `"lg"` | `max-w-2xl` | 672px     |
| `"xl"` | `max-w-4xl` | 896px     |

---

### Layout & Composition

Widgets can be arbitrarily nested to build complex UIs:

```python
Page(title="My Plugin", components=[
    Tabs(tabs=[
        Tab(label="Overview", children=[
            Card(title="Status", children=[
                Row(children=[
                    StatusBadge(label="Running", color="green"),
                    Button("Restart", action="restart", style="danger"),
                ]),
                KeyValue(items=[...]),
            ]),
            Alert(message="All systems operational.", severity="success"),
        ]),
        Tab(label="Devices", children=[
            Table(columns=[...], rows=[
                {
                    ...,
                    "actions": [
                        # Each row can open a Modal
                    ],
                },
            ]),
            # Device modals (one per device)
            Modal(title="Device Settings", trigger_label="Configure", children=[
                Tabs(tabs=[                  # Nested tabs inside a modal
                    Tab(label="General", children=[
                        Form(action="save_device", children=[...]),
                    ]),
                    Tab(label="Audio", children=[
                        Form(action="save_device", children=[...]),
                    ]),
                ]),
            ]),
        ]),
    ]),
])
```

---

### Form System

#### How Forms Collect Values

1. `Form` creates a Svelte context (`formContext`).
2. Each child input widget (`TextInput`, `Select`, etc.) registers
   its current value via `formContext.setValue(name, value)`.
3. On submit, the form collects all registered values into a `params` dict.
4. The dict is POSTed to `POST /api/plugins/{id}/actions/{action}`.

#### Dirty Tracking

The form snapshots initial values after all children register. Any
change marks the form as "dirty". The submit button is disabled until
dirty. After successful submission, the snapshot is updated and dirty
state resets.

#### Form-Level vs Input-Level Disabled

- `Form(disabled=True)` → disables **all** child inputs and the submit button.
- `TextInput(disabled=True)` → disables only that specific input.
- Both can be combined: form-level disabled overrides individual settings.

#### Validation

Validation runs on the **frontend** (on blur / touched state):

- `required` fields must be non-empty.
- `pattern` (TextInput) is checked via `new RegExp(pattern)`.
- `min`/`max` (NumberInput) are checked against the current value.
- `maxlength` (Textarea) prevents exceeding the limit.

Validation errors are shown as red text below the field. They do not
prevent form submission — backend validation is the authoritative check.

---

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

#### The `.when()` Method

```python
.when(field: str, value: Any, operator: str = "eq") -> self
```

Returns `self` for chaining. Calling `.when()` multiple times on the same
component **replaces** the previous condition (last call wins).

#### Operators

| Operator | Meaning               | Example                                             |
| -------- | --------------------- | --------------------------------------------------- |
| `eq`     | Equal (default)       | `.when("mode", "hardware")`                         |
| `ne`     | Not equal             | `.when("mode", "disabled", operator="ne")`          |
| `gt`     | Greater than          | `.when("port", 1024, operator="gt")`                |
| `lt`     | Less than             | `.when("volume", 10, operator="lt")`                |
| `gte`    | Greater than or equal | `.when("level", 5, operator="gte")`                 |
| `lte`    | Less than or equal    | `.when("level", 100, operator="lte")`               |
| `in`     | Value is in list      | `.when("codec", ["aac", "alac"], operator="in")`    |
| `not_in` | Value is not in list  | `.when("codec", ["pcm", "wav"], operator="not_in")` |

```python
# Show a warning when port is below 1024
Alert(message="Privileged port!", severity="warning").when("port", 1024, operator="lt"),

# Show options only for specific codecs
Text("Lossy codec selected").when("codec", ["aac", "mp3"], operator="in"),
```

#### Wire Format

```json
{
    "type": "select",
    "props": { ... },
    "visible_when": {
        "field": "debug_enabled",
        "value": true
    }
}
```

When `operator` is `"eq"` (the default), the `operator` key is omitted to
keep payloads compact:

```json
// operator="ne" — explicitly included
"visible_when": { "field": "mode", "value": "disabled", "operator": "ne" }

// operator="eq" — omitted (default)
"visible_when": { "field": "mode", "value": "hardware" }
```

#### Behaviour Notes

- Outside a `Form`, `visible_when` is silently ignored — the component is
  always visible.
- The frontend evaluates conditions reactively: changing a form field
  immediately shows/hides dependent components (no roundtrip).
- Unknown operators cause the frontend to show the component (fail-open).

---

### Modal Dialogs

See the [Modal widget reference](#modal) above for constructor details.

#### Usage Patterns

**Pattern 1: Per-row modal (device configuration)**

Generate one modal per table row by building them in a loop:

```python
device_modals = []
for device in devices:
    device_modals.append(
        Modal(
            title=f"Settings — {device.name}",
            trigger_label=f"Settings: {device.name}",
            size="lg",
            children=[
                Form(action="save_device_settings", children=[
                    TextInput(name="name", label="Name", value=device.name),
                    # Hidden identifier
                    TextInput(name="udn", label="", value=device.udn,
                              disabled=True),
                ]),
            ],
        )
    )
```

**Pattern 2: Global modal (add new item)**

```python
Modal(
    title="Add New Source",
    trigger_label="Add Source",
    trigger_style="primary",
    size="md",
    children=[
        Form(action="add_source", submit_label="Add", children=[
            TextInput(name="url", label="Stream URL", required=True),
            TextInput(name="name", label="Display Name", required=True),
        ]),
    ],
)
```

---

### Action Handler Protocol

#### Handler Signature

```python
async def handle_action(action: str, params: dict, ctx: PluginContext) -> dict:
```

| Parameter | Type            | Source                                                               |
| --------- | --------------- | -------------------------------------------------------------------- |
| `action`  | `str`           | From `Button(action="...")`, `Form(action="...")`, or `TableAction`  |
| `params`  | `dict`          | Merged from button params, table row params, or form input values    |
| `ctx`     | `PluginContext` | The plugin's context — access settings, player registry, server info |

The `ctx` parameter gives the action handler full access to the plugin's
`PluginContext`, enabling operations like reading/writing settings
(`ctx.get_setting()`, `ctx.set_setting()`), accessing connected players
(`ctx.player_registry`), or querying server networking info (`ctx.server_info`).

#### Return Value Protocol

The handler must return a `dict`. Special keys control frontend behaviour:

| Return Value            | Effect                                          |
| ----------------------- | ----------------------------------------------- |
| `{"message": "Saved!"}` | Success toast notification in the UI            |
| `{"error": "Failed!"}`  | (In dict) Plugin-level error, still HTTP 200    |
| `{"success": True}`     | Silent success — no toast, UI refreshes via SSE |
| Raised `Exception`      | HTTP 500, error toast with exception message    |

**Important:** After every action dispatch, the server automatically calls
`ctx.notify_ui_update()`. This means the frontend will re-fetch the UI
via SSE — you do not need to call `notify_ui_update()` manually from
within `handle_action()` (but it's harmless to do so).

#### Handler Pattern

```python
async def handle_action(action: str, params: dict, ctx: PluginContext) -> dict:
    match action:
        case "activate":
            await bridge.start()
            return {"message": "Bridge activated"}

        case "save_settings":
            ctx.set_settings(params)
            return {"message": "Settings saved"}

        case "delete_device":
            udn = params.get("udn")
            if not udn:
                return {"error": "Missing device ID"}
            await bridge.delete_device(udn)
            return {"message": f"Device deleted"}

        case "update_device":
            # From inline table edit or modal form
            udn = params.get("udn")
            name = params.get("name")
            await bridge.rename_device(udn, name)
            return {"message": f"Device renamed to {name}"}

        case _:
            return {"error": f"Unknown action: {action}"}
```

---

### REST Endpoints & SSE

These endpoints are automatically available for any plugin with
`[ui] enabled = true` in its manifest.

| Endpoint                                    | Method | Content-Type        | Description                              |
| ------------------------------------------- | ------ | ------------------- | ---------------------------------------- |
| `/api/plugins/ui-registry`                  | `GET`  | `application/json`  | Sidebar entries (all UI-enabled plugins) |
| `/api/plugins/{plugin_id}/ui`               | `GET`  | `application/json`  | Full UI JSON schema for one plugin       |
| `/api/plugins/{plugin_id}/actions/{action}` | `POST` | `application/json`  | Dispatch a button/form action            |
| `/api/plugins/{plugin_id}/events`           | `GET`  | `text/event-stream` | SSE stream for live UI updates           |

#### UI Registry Response

```json
[
    {
        "id": "raopbridge",
        "label": "AirPlay",
        "icon": "cast",
        "path": "/plugins/raopbridge"
    }
]
```

#### UI Schema Response

See [Wire Format Specification](#wire-format-specification) above.

#### Action Request / Response

**Request:**

```
POST /api/plugins/raopbridge/actions/activate
Content-Type: application/json

{"force": true}
```

**Success response (HTTP 200):**

```json
{ "message": "Bridge activated" }
```

**Plugin error (HTTP 200 — error is in the dict, not the status code):**

```json
{ "error": "Binary not found" }
```

**Server error (HTTP 500):**

```json
{ "detail": "Action failed: FileNotFoundError(...)" }
```

#### SSE Protocol

The `/events` endpoint sends Server-Sent Events. Each event is a JSON line:

```
data: {"event": "ui_refresh", "revision": 42}

```

- **Keep-alive:** If no update occurs within ~25 seconds, a comment is sent:

    ```
    : keepalive

    ```

- **Reconnection:** The frontend automatically reconnects with exponential
  backoff (up to 3 retries), then falls back to polling.
- **Trigger:** Call `ctx.notify_ui_update()` from your plugin to send an
  event. This is also called automatically after every action dispatch.

---

### Error Responses

| Scenario                     | HTTP Status | Response Body                                      |
| ---------------------------- | ----------- | -------------------------------------------------- |
| Plugin not found             | 404         | `{"detail": "Plugin not found: xyz"}`              |
| Plugin not started           | 503         | `{"detail": "Plugin not started: xyz"}`            |
| Plugin has no UI handler     | 404         | `{"detail": "Plugin has no UI handler: xyz"}`      |
| Plugin has no action handler | 404         | `{"detail": "Plugin has no action handler: xyz"}`  |
| `get_ui()` raises exception  | 500         | `{"detail": "Plugin UI handler error"}`            |
| `handle_action()` raises     | 500         | `{"detail": "Action failed: <exception message>"}` |
| Invalid/missing request body | —           | Body defaults to `{}` (no error)                   |
| Non-dict return from handler | —           | Silently converted to `{"success": true}`          |

---

### Schema Versioning

The wire format includes a `schema_version` field (currently `"1.0"`).

**Current guarantees:**

- `schema_version` is set from `SCHEMA_VERSION` in `resonance/ui/__init__.py`.
- The frontend does not currently enforce schema version checks.
- Backward compatibility is maintained through compact serialization rules:
  new optional fields are omitted when not set, so older frontends ignore them.

**Forward compatibility (planned):**

- New widget types are handled gracefully: the frontend renders an
  "Unknown widget" box with `fallback_text` if provided.
- New props on existing widgets are ignored by older frontends (Svelte
  ignores unknown props).
- Breaking changes to existing widget serialization would require a
  `schema_version` bump and frontend migration.

**Migration policy:**

| Change Type          | Schema Version Impact | Example                                |
| -------------------- | --------------------- | -------------------------------------- |
| New widget type      | None (additive)       | Adding `chart` widget                  |
| New optional prop    | None (additive)       | Adding `help_text` to form inputs      |
| Rename existing prop | Major bump            | Renaming `label` to `text` on Button   |
| Remove existing prop | Major bump            | Removing `fallback_text`               |
| Change prop type     | Major bump            | Changing `value` from string to object |

---

### Security Model

The SDUI system uses **security by construction** — the architecture
makes entire categories of vulnerabilities impossible:

1. **No plugin JavaScript runs in the browser.** Plugins only provide
   declarative JSON data. The frontend has a fixed set of Svelte
   components that render this data.

2. **Event handler props are rejected.** Any prop starting with `on`
   (e.g. `onclick`, `onerror`) raises `ValueError` during component
   construction:

    ```python
    UIComponent(type="text", props={"onclick": "alert(1)"})
    # → ValueError: Event handler props are not allowed: 'onclick'
    ```

3. **URL schemes are restricted.** Props named `href`, `src`, or `url`
   must use `http:`, `https:`, or `mailto:` schemes. `javascript:` and
   `data:` URLs are blocked:

    ```python
    UIComponent(type="text", props={"href": "javascript:alert(1)"})
    # → ValueError: Invalid URL scheme in 'javascript:alert(1)'
    ```

4. **No `{@html}` on raw plugin strings.** The frontend never uses Svelte's
   `{@html}` directive on plugin-provided content directly. All text is
   rendered as text nodes, preventing XSS even if a plugin provides HTML
   strings. The one exception is the `Markdown` widget, which uses `{@html}`
   on the **output of the custom `marked` renderer** — not on the raw plugin
   string. The renderer has raw HTML disabled (`marked` escapes all HTML tags
   in the source to `&lt;`/`&gt;`), only emits known-safe elements, escapes
   all attribute values, and validates URLs against an allowlist of safe
   schemes. See the [Markdown widget reference](#markdown) for details.

5. **CSP headers.** The server sets Content-Security-Policy headers to
   further restrict script execution.

---

### Sidebar Icon

The sidebar icon for a plugin is resolved in this order:

1. `[ui].sidebar_icon` from `plugin.toml`
2. `[plugin].icon` from `plugin.toml`
3. Fallback: `"plug"`

Supported icon names are any [Lucide](https://lucide.dev/icons/) icon name
in kebab-case (e.g. `"cast"`, `"radio"`, `"hard-drive"`, `"refresh-cw"`).

---

### Reference Implementation

The `raopbridge` community plugin is the first SDUI consumer. Study its
implementation for a complete real-world example:

- [`plugin.toml`](https://github.com/endegelaende/resonance-community-plugins/tree/main/plugins/raopbridge/plugin.toml) — `[ui]` section
- [`__init__.py`](https://github.com/endegelaende/resonance-community-plugins/tree/main/plugins/raopbridge/__init__.py) — `get_ui()` and `handle_action()`

It renders a tabbed interface with **five tabs**:

| Tab          | Widgets Used                                                  | Description                              |
| ------------ | ------------------------------------------------------------- | ---------------------------------------- |
| **Status**   | `Card`, `StatusBadge`, `KeyValue`, `Button`, `Row`            | Bridge status + control buttons          |
| **Devices**  | `Table` (badge + editable + actions), `Modal`, `Tabs`, `Form` | Device list + per-device config modal    |
| **Settings** | `Form`, `Select`, `TextInput`, `Toggle`, `Card`               | Bridge settings (disabled when active)   |
| **Advanced** | `Card` (collapsible), `KeyValue`                              | Read-only common options from XML config |
| **About**    | `Markdown`                                                    | Plugin description + external links      |

Actions dispatch to bridge management functions (`save_settings`,
`delete_device`, `activate`, `deactivate`, `restart`, `save_device_settings`).

For a guided walkthrough, see
[`PLUGIN_CASESTUDY.md`](PLUGIN_CASESTUDY.md).

---

## 20) Plugin Settings & Management API

This section documents the built-in management surface introduced with the
plugin modernization (phases A-E).

### JSON-RPC Commands

#### `pluginsettings`

| Command                                                   | Purpose                                       |
| --------------------------------------------------------- | --------------------------------------------- |
| `["pluginsettings", "getdef", "<plugin>"]`                | Returns only setting definitions              |
| `["pluginsettings", "get", "<plugin>"]`                   | Returns definitions + current (masked) values |
| `["pluginsettings", "set", "<plugin>", "key:value", ...]` | Validates, persists, and updates values       |

Notes:

- `set` performs type parsing (`int/float/bool/string/select`) before validation.
- Secret values are masked in responses.
- If a changed setting has `restart_required = true`, response includes `restart_required: true`.

#### `pluginmanager`

| Command                                             | Purpose                                         |
| --------------------------------------------------- | ----------------------------------------------- |
| `["pluginmanager", "list"]`                         | List installed plugins with state/type metadata |
| `["pluginmanager", "info", "<plugin>"]`             | One plugin + setting definitions                |
| `["pluginmanager", "enable", "<plugin>"]`           | Persist state as enabled                        |
| `["pluginmanager", "disable", "<plugin>"]`          | Persist state as disabled                       |
| `["pluginmanager", "install", "<url>", "<sha256>"]` | Download and install plugin ZIP                 |
| `["pluginmanager", "uninstall", "<plugin>"]`        | Uninstall community plugin                      |
| `["pluginmanager", "repository"]`                   | Fetch repository index + compare with installed |
| `["pluginmanager", "installrepo", "<plugin>"]`      | Install plugin directly from repository         |

### REST Endpoints

| Endpoint                         | Method | Purpose                                            |
| -------------------------------- | ------ | -------------------------------------------------- |
| `/api/plugins`                   | `GET`  | List plugins + `restart_required`                  |
| `/api/plugins/{name}/settings`   | `GET`  | Get definitions + masked values                    |
| `/api/plugins/{name}/settings`   | `PUT`  | Update settings with validation                    |
| `/api/plugins/{name}/enable`     | `POST` | Enable plugin                                      |
| `/api/plugins/{name}/disable`    | `POST` | Disable plugin                                     |
| `/api/plugins/install`           | `POST` | Install from ZIP URL + SHA256                      |
| `/api/plugins/{name}/uninstall`  | `POST` | Uninstall community plugin                         |
| `/api/plugins/repository`        | `GET`  | List repository plugins (`force_refresh` optional) |
| `/api/plugins/install-from-repo` | `POST` | Install by repository plugin name                  |

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

## Further Reading

| Document                                                                                                                | Content                                                                             |
| ----------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| [`PLUGINS.md`](PLUGINS.md)                                                                                              | General overview for all audiences                                                  |
| [`PLUGIN_TUTORIAL.md`](PLUGIN_TUTORIAL.md)                                                                              | Step-by-step: Build your own plugin                                                 |
| [`PLUGIN_CASESTUDY.md`](PLUGIN_CASESTUDY.md)                                                                            | Case study: raopbridge migration from Svelte to SDUI (before/after, mapping tables) |
| [Community Plugins Repository](https://github.com/endegelaende/resonance-community-plugins)                             | Publishing plugins and repository index format                                      |
| [`ARCHITECTURE.md`](ARCHITECTURE.md)                                                                                    | Resonance system architecture                                                       |
| `plugins/radio/`                                                                                                        | Reference ContentProvider plugin (radio-browser.info, remote streaming)             |
| `plugins/podcast/`                                                                                                      | Reference ContentProvider plugin (RSS feeds, subscriptions, resume)                 |
| [raopbridge community plugin](https://github.com/endegelaende/resonance-community-plugins/tree/main/plugins/raopbridge) | Reference SDUI consumer (AirPlay bridge with UI page)                               |

---

_Last updated: March 2026_
