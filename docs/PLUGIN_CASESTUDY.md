# Build a Real Plugin — Learn from the raopbridge Story

So you want to build a plugin for Resonance? Awesome — this is the right
place. We will walk you through a **real plugin** that a community member
built, show you how it works, and teach you the patterns you need for
your own plugin along the way.

This is not a toy example. This is a production plugin with device
management, a settings form, a status dashboard, confirmation dialogs,
and live updates — all built in Python, no JavaScript required.

**Prerequisites:** If you have not read the
[Plugin Tutorial](PLUGIN_TUTORIAL.md) yet, start there. It covers the
basics (manifest, setup, commands, events, persistence, tests). This
document builds on those foundations and shows you what a full-featured
plugin looks like in practice.

---

## Table of Contents

- [The Story: How raopbridge Came to Be](#the-story-how-raopbridge-came-to-be)
- [What Pinoatrome Built](#what-pinoatrome-built)
- [How the Plugin Architecture Works](#how-the-plugin-architecture-works)
- [Building the UI in Python with SDUI](#building-the-ui-in-python-with-sdui)
    - [The Page Structure](#the-page-structure)
    - [Status Tab — Badges, Key-Value, Buttons](#status-tab--badges-key-value-buttons)
    - [Devices Tab — Tables, Inline Edit, Row Actions](#devices-tab--tables-inline-edit-row-actions)
    - [Device Modal — Nested Tabs Inside a Form Inside a Modal](#device-modal--nested-tabs-inside-a-form-inside-a-modal)
    - [Settings Tab — Forms with Conditional Fields](#settings-tab--forms-with-conditional-fields)
    - [Advanced Tab — Read-Only Collapsible Cards](#advanced-tab--read-only-collapsible-cards)
    - [About Tab — Markdown](#about-tab--markdown)
- [Handling Actions — The Backend for Your UI](#handling-actions--the-backend-for-your-ui)
- [Live Updates with SSE](#live-updates-with-sse)
- [The Full Picture: How Frontend and Backend Connect](#the-full-picture-how-frontend-and-backend-connect)
- [Adding REST Routes (Optional but Useful)](#adding-rest-routes-optional-but-useful)
- [What You Can Learn from Pinoatrome's Journey](#what-you-can-learn-from-pinoatromes-journey)
- [Your Plugin Checklist](#your-plugin-checklist)
- [Further Reading](#further-reading)

---

## The Story: How raopbridge Came to Be

In February 2026, a community member named
[Pinoatrome](https://github.com/pinoatrome) opened
[Issue #11](https://github.com/endegelaende/resonance-server/issues/11)
with a simple message:

> _"Hi, I've completed the porting in python of the LMS plugin LMS-Raop
> by philippe44. I still have a couple of points about integration not
> resolved but once fixed I'd be happy to create a PR."_

What Pinoatrome delivered was far more than a "couple of points." He had
ported philippe44's entire
[LMS-Raop](https://github.com/philippe44/LMS-Raop) AirPlay bridge from
Perl to Python, written a full REST API, built tests, and created a
polished web UI — complete with tabs, device cards, settings forms, and
confirmation dialogs.

This was an outstanding contribution. The backend code — bridge process
management, binary download logic, XML configuration parsing, device
discovery — was so well done that it forms the core of the raopbridge
plugin to this day. **Pinoatrome's code is still running.**

The one thing we adapted was the UI delivery method. When Pinoatrome
built his version, Resonance's SDUI framework did not exist yet. He did
the natural thing and wrote Svelte components. His work actually helped
us understand what a plugin UI system _needed_ to support — tabs, tables,
modals, forms, inline editing, conditional fields. In a real sense, his
Svelte components served as the specification for the SDUI widget library
we built afterwards.

We migrated the UI layer from custom Svelte to declarative Python — but
the foundation is his.

**This is what community contributions look like.** If you build
something for Resonance, your work matters. Even when the architecture
evolves, the core of good contributions lives on.

---

## What Pinoatrome Built

Here is the full scope of what Pinoatrome delivered in his
[`feature/raop` branch](https://github.com/pinoatrome/resonance-server/tree/feature/raop):

### Backend — The Foundation We Kept

| File             | What It Does                                     |
| ---------------- | ------------------------------------------------ |
| `__init__.py`    | Plugin lifecycle, JSON-RPC commands, REST routes |
| `bridge.py`      | Subprocess management for squeeze2raop binary    |
| `config.py`      | XML config parsing (devices, common options)     |
| `serializers.py` | Data serialisation helpers                       |
| `tests/`         | Unit tests                                       |

`bridge.py` handles everything about the external squeeze2raop process:
finding the right binary for your OS, downloading it from philippe44's
repo on first use, starting and stopping the subprocess, generating the
XML configuration, and monitoring its state. This is non-trivial, well-
tested code that "just works."

`config.py` parses the bridge's XML configuration file into clean Python
dataclasses (`RaopDevice`, `RaopCommonOptions`) that are easy to work
with.

### Frontend — The UI We Migrated to SDUI

Pinoatrome also built **10 Svelte components** and a **TypeScript API
client** (~1,015 lines total) that provided a complete web UI:

| Component                   | What It Did                                    |
| --------------------------- | ---------------------------------------------- |
| `PluginPage.svelte`         | Main container with 4 tabs                     |
| `StatusView.svelte`         | Plugin name and version display                |
| `ToggleBridgeStatus.svelte` | Activate/deactivate switch with confirmation   |
| `DeviceList.svelte`         | Device table with expand/collapse and 3 modals |
| `DeviceCard.svelte`         | Single device row with inline rename           |
| `DeviceSettings.svelte`     | Per-device volume mode selector                |
| `SettingsView.svelte`       | Full settings form with validation             |
| `AdvancedView.svelte`       | Read-only key-value display (22 fields)        |
| `AboutView.svelte`          | About text with links                          |
| `raopbridgeApi.ts`          | TypeScript REST client (11 endpoints)          |

This was polished, functional work. The reason we migrated it to SDUI
was not quality — it was architecture:

- **Security.** Resonance uses Content Security Policy headers. With
  SDUI, no plugin-supplied JavaScript runs in the browser. Plugins
  produce JSON data, the frontend renders it with its own trusted
  components.

- **Installation.** Svelte components need to be compiled into the
  frontend bundle. SDUI plugins are Python-only ZIP files — install
  through the Plugin Manager, restart the server, done.

- **Shared widgets.** Pinoatrome built his own modal, switch, and tabs
  components (great ones!). With SDUI, every plugin uses the same widget
  library — `Modal`, `Toggle`, `Tabs`, `Form`, `Table`, and 15+ more.

The beauty of SDUI is: **you describe your UI in Python, and the
frontend renders it automatically.** You never touch JavaScript, Svelte,
or CSS. Let's see how.

---

## How the Plugin Architecture Works

Before we dive into the UI, here is how a plugin with SDUI connects to
the rest of the system:

```
Your Plugin (Python)             Resonance Server           Web Frontend (Svelte)
────────────────────             ────────────────           ─────────────────────
get_ui(ctx) → Page          →   GET /api/plugins/{id}/ui   →   PluginPageView
     ↓                               ↓ JSON                       ↓
  widget tree                   { components: [...] }       PluginRenderer
  (Python objects)                                          (renders recursively)

handle_action(action, params) ← POST /api/plugins/{id}/actions/{action}

ctx.notify_ui_update()       → SSE /api/plugins/{id}/events → auto re-fetch UI
```

Your plugin provides two functions:

- **`get_ui(ctx)`** — returns a `Page` object describing your UI as a
  tree of widgets
- **`handle_action(action, params)`** — handles button clicks and form
  submissions

And in `setup()`, you register them:

```python
async def setup(ctx):
    ctx.register_ui_handler(get_ui)
    ctx.register_action_handler(handle_action)
```

That's it. The sidebar entry, page rendering, action dispatch, SSE live
updates — all handled automatically.

---

## Building the UI in Python with SDUI

Let's walk through each part of the raopbridge UI and learn the patterns
you will use in your own plugin.

### The Page Structure

The raopbridge plugin organises its UI into 5 tabs. Here is the
top-level `get_ui()` function:

```python
from resonance.ui import (
    Alert, Button, Card, Column, Form, Heading, KeyValue, KVItem,
    Markdown, Modal, NumberInput, Page, Row, Select, SelectOption,
    StatusBadge, Tab, Table, TableColumn, Tabs, Text, TextInput, Toggle,
)

async def get_ui(ctx) -> Page:
    if _raop_bridge is None:
        return Page(
            title="AirPlay Bridge",
            icon="cast",
            refresh_interval=5,
            components=[
                Alert(message="The raopbridge plugin is not initialised.",
                      severity="error"),
            ],
        )

    is_active = _raop_bridge.is_active
    settings = _raop_bridge.settings

    return Page(
        title="AirPlay Bridge",
        icon="cast",
        refresh_interval=5,
        components=[
            Tabs(tabs=[
                _build_status_tab(is_active, settings),
                await _build_devices_tab(is_active),
                _build_settings_tab(is_active, settings),
                await _build_advanced_tab(is_active),
                _build_about_tab(),
            ]),
        ],
    )
```

**What to learn here:**

- Always handle the "not ready" case gracefully — show an `Alert`
  instead of crashing.
- `refresh_interval=5` means the frontend will poll for updates every
  5 seconds if SSE is not available. You can also set it to `0` to
  disable polling entirely (if you rely on SSE only).
- Break your UI into builder functions. One function per tab keeps
  things readable.

### Status Tab — Badges, Key-Value, Buttons

The Status tab shows whether the bridge is running and provides
control buttons:

```python
def _build_status_tab(is_active: bool, settings: dict) -> Tab:
    badge = StatusBadge("Active", color="green") if is_active \
        else StatusBadge("Inactive", color="red")

    status_card = Card(
        title="Bridge Status",
        children=[
            Column(children=[badge]),
            KeyValue(items=[
                KVItem("Binary", str(settings.get("bin", "unknown"))),
                KVItem("Interface", str(settings.get("interface", "?"))),
                KVItem("Server", str(settings.get("server", "?"))),
                KVItem("Auto-start", "Yes" if settings.get("active_at_startup") else "No"),
            ]),
        ],
    )

    if is_active:
        controls = Row(children=[
            Button("Deactivate", action="deactivate", style="danger", confirm=True),
            Button("Restart", action="restart", style="secondary", confirm=True),
        ])
    else:
        controls = Row(children=[
            Button("Activate", action="activate", style="primary"),
        ])

    return Tab(label="Status", icon="activity", children=[status_card, controls])
```

**What to learn here:**

- `StatusBadge` is perfect for showing an at-a-glance status with a
  colour indicator (green, red, yellow, blue, gray).
- `KeyValue` + `KVItem` is the easiest way to show labelled data.
- `confirm=True` on a `Button` automatically shows a confirmation
  dialog before dispatching the action — no custom modal needed.
- Use `style="danger"` for destructive actions and `style="primary"`
  for the main action.
- Show different buttons based on state. The UI is rebuilt every time
  `get_ui()` is called, so this is just an `if` statement.

### Devices Tab — Tables, Inline Edit, Row Actions

The Devices tab shows a table of detected AirPlay devices with inline
editing, badge columns, and per-row action buttons:

```python
async def _build_devices_tab(is_active: bool) -> Tab:
    if not is_active:
        return Tab(label="Devices", icon="speaker", children=[
            Alert(message="Activate the bridge to discover AirPlay devices.",
                  severity="info"),
        ])

    devices = await _raop_bridge.parse_devices()

    if not devices:
        return Tab(label="Devices", icon="speaker", children=[
            Alert(message="No AirPlay devices detected.", severity="info"),
        ])

    columns = [
        TableColumn(key="name", label="Name", variant="editable"),
        TableColumn(key="friendly_name", label="Friendly Name"),
        TableColumn(key="mac", label="MAC Address"),
        TableColumn(key="enabled", label="Enabled", variant="badge"),
        TableColumn(key="actions", label="Actions", variant="actions"),
    ]

    rows = []
    for device in devices:
        rows.append({
            "name": device.name,
            "friendly_name": device.friendly_name,
            "mac": device.mac,
            "udn": device.udn,
            "enabled": {
                "text": "Yes" if device.enabled else "No",
                "color": "green" if device.enabled else "red",
            },
            "actions": [
                {"label": "Disable" if device.enabled else "Enable",
                 "action": "toggle_device",
                 "params": {"udn": device.udn, "enabled": not device.enabled}},
                {"label": "Delete", "action": "delete_device",
                 "params": {"udn": device.udn}, "style": "danger", "confirm": True},
            ],
        })

    table = Table(
        columns=columns,
        rows=rows,
        title="Detected AirPlay Devices",
        edit_action="update_device",
        row_key="udn",
    )

    # Per-device settings modals
    modals = [_build_device_modal(device) for device in devices]

    return Tab(label="Devices", icon="speaker", children=[table] + modals)
```

**What to learn here:**

- **`variant="editable"`** on a column makes it click-to-edit. When the
  user commits an edit, the frontend dispatches your `edit_action` with
  the row key and new value. You get
  `handle_action("update_device", {"udn": "...", "name": "new_name"})`.

- **`variant="badge"`** renders a coloured pill. The cell value must be
  `{"text": "...", "color": "..."}`.

- **`variant="actions"`** renders action buttons per row. Each action
  dict has `label`, `action`, `params`, and optionally `style` and
  `confirm`.

- **`row_key="udn"`** tells the table which field uniquely identifies
  a row. This is included in action params automatically.

- Handle empty/error states with `Alert` widgets — don't let the user
  see a blank page.

### Device Modal — Nested Tabs Inside a Form Inside a Modal

This is where it gets fun. Each device has a settings modal with three
sub-tabs (General, Audio, Behaviour), all inside a form, all inside a
modal — and it is still just a Python function:

```python
def _build_device_modal(device) -> Modal:
    common = device.common

    inner_tabs = Tabs(tabs=[
        Tab(label="General", children=[
            TextInput(name="name", label="Display Name",
                      value=device.name, required=True),
            Select(name="volume_mode", label="Volume Mode",
                   value=str(common.volume_mode),
                   options=[
                       SelectOption(value="0", label="Ignored"),
                       SelectOption(value="1", label="Software"),
                       SelectOption(value="2", label="Hardware"),
                   ]),
            KeyValue(items=[
                KVItem("Friendly Name", device.friendly_name),
                KVItem("MAC Address", device.mac),
            ]),
        ]),
        Tab(label="Audio", children=[
            Toggle(name="alac_encode", label="ALAC Encode",
                   value=common.alac_encode),
            Toggle(name="encryption", label="Encryption",
                   value=common.encryption),
        ]),
        Tab(label="Behaviour", children=[
            Toggle(name="send_metadata", label="Send Metadata",
                   value=common.send_metadata),
            Toggle(name="auto_play", label="Auto Play",
                   value=common.auto_play),
            NumberInput(name="idle_timeout", label="Idle Timeout (seconds)",
                        value=common.idle_timeout, min=0, max=3600),
        ]),
    ])

    return Modal(
        title=f"Settings — {device.name}",
        trigger_label=f"Edit {device.name}",
        size="lg",
        children=[
            Form(
                action="update_device",
                submit_label="Save Device",
                children=[
                    TextInput(name="udn", label="UDN",
                              value=device.udn, disabled=True),
                    inner_tabs,
                ],
            ),
        ],
    )
```

**What to learn here:**

- **`Modal`** renders a trigger button inline. Clicking it opens a
  dialog overlay. Backdrop click or Escape closes it. No state
  management code needed.

- **Widgets compose freely.** `Modal` > `Form` > `Tabs` > `Tab` >
  `Toggle` — nest as deep as you need.

- **`Form` collects all child input values** (identified by `name`)
  and submits them as a single `params` dict to your `handle_action()`.
  Dirty tracking (the "Save" button only activates when something
  changes) is built in.

- **`disabled=True`** on `TextInput` shows a read-only field. Use this
  for identifiers the user should see but not edit.

### Settings Tab — Forms with Conditional Fields

The Settings tab shows an editable form that is disabled while the
bridge is running. Debug options only appear when debug mode is on:

```python
def _build_settings_tab(is_active: bool, settings: dict) -> Tab:
    children = []

    if is_active:
        children.append(
            Alert(message="Deactivate the bridge before changing settings.",
                  severity="info")
        )

    form = Form(
        action="save_settings",
        submit_label="Save Settings",
        disabled=is_active,
        children=[
            Select(name="bin", label="Binary",
                   value=settings.get("bin", ""),
                   options=bin_select_options, disabled=is_active),
            TextInput(name="interface", label="Network Interface",
                      value=settings.get("interface", ""), disabled=is_active),
            Toggle(name="active_at_startup", label="Auto-start",
                   value=settings.get("active_at_startup", False)),
            Toggle(name="debug_enabled", label="Debug Mode",
                   value=settings.get("debug_enabled", False)),

            # These only appear when debug_enabled is True:
            Select(name="debug_category", label="Debug Category",
                   value=settings.get("debug_category", "all"),
                   options=_DEBUG_CATEGORIES,
            ).when("debug_enabled", True),
            Select(name="debug_level", label="Debug Level",
                   value=settings.get("debug_level", "info"),
                   options=_DEBUG_LEVELS,
            ).when("debug_enabled", True),
        ],
    )
    children.append(form)

    return Tab(label="Settings", icon="settings", children=children)
```

**What to learn here:**

- **`Form(disabled=is_active)`** disables all child inputs and the
  submit button in one shot. No need to pass `readonly` through every
  component.

- **`.when("debug_enabled", True)`** is conditional rendering. The
  debug selects only appear when the toggle is on. The frontend
  evaluates this reactively — flip the toggle and the fields appear
  instantly, no server roundtrip.

- `.when()` supports operators: `eq` (default), `ne`, `gt`, `lt`,
  `gte`, `lte`, `in`, `not_in`. See
  [`PLUGIN_API.md` §19](PLUGIN_API.md#19-server-driven-ui-sdui) for
  the full reference.

### Advanced Tab — Read-Only Collapsible Cards

For read-only configuration display, use `KeyValue` inside a
collapsible `Card`:

```python
async def _build_advanced_tab(is_active: bool) -> Tab:
    if not is_active:
        return Tab(label="Advanced", icon="sliders-horizontal", children=[
            Alert(message="Activate the bridge to view advanced options.",
                  severity="info"),
        ])

    common = await _raop_bridge.parse_common_options()

    card = Card(
        title="Common Options",
        collapsible=True,
        collapsed=True,    # starts collapsed — click to expand
        children=[
            Text("Read-only view of global options from the bridge XML config."),
            KeyValue(items=[
                KVItem("Stream Buffer Size", str(common.streambuf_size)),
                KVItem("Sample Rate", str(common.sample_rate)),
                KVItem("ALAC Encode", "Yes" if common.alac_encode else "No"),
                # ... more items ...
            ]),
        ],
    )

    return Tab(label="Advanced", icon="sliders-horizontal", children=[card])
```

**What to learn here:**

- `collapsible=True` + `collapsed=True` means the card starts collapsed.
  The user clicks to expand. Great for advanced/diagnostic information
  that most users don't need to see.

### About Tab — Markdown

Every plugin should have an About section. `Markdown` renders full
Markdown including headers, links, lists, and code:

```python
def _build_about_tab() -> Tab:
    md_content = (
        "## AirPlay Bridge\n\n"
        "This plugin uses **squeeze2raop** by "
        "[philippe44](https://github.com/philippe44) to make AirPlay "
        "devices available as Squeezebox players in Resonance.\n\n"
        "### Links\n\n"
        "- [squeeze2raop on GitHub](https://github.com/philippe44/LMS-Raop)\n"
        "- [Resonance Documentation](https://github.com/endegelaende/resonance-server)\n"
    )

    return Tab(label="About", icon="info", children=[
        Card(title="About", collapsible=True, children=[Markdown(md_content)]),
    ])
```

**Tip:** Use the About tab to credit the people whose work your plugin
builds on. Pinoatrome credited philippe44. We credit Pinoatrome. Open
source thrives on this.

---

## Handling Actions — The Backend for Your UI

Every button click and form submission in your UI dispatches an
**action**. Your `handle_action()` function receives the action name and
a params dict, and returns a result:

```python
async def handle_action(action: str, params: dict) -> dict:
    if _raop_bridge is None:
        return {"error": "raopbridge plugin not initialised"}

    match action:
        case "activate":
            return await _activate()
        case "deactivate":
            return await _deactivate()
        case "restart":
            return await _restart()
        case "save_settings":
            return await _handle_save_settings(params)
        case "delete_device":
            return await _handle_delete_device(params)
        case "toggle_device":
            return await _handle_toggle_device(params)
        case "update_device":
            return await _handle_update_device(params)
        case _:
            return {"error": f"Unknown action: {action}"}
```

**Return value conventions:**

| Return                          | What the frontend does    |
| ------------------------------- | ------------------------- |
| `{"message": "Settings saved"}` | Shows a success toast     |
| `{"error": "Invalid device"}`   | Shows an error toast      |
| `{"success": True}`             | Silent success (no toast) |

**Where do params come from?**

- `Button(action="activate")` → params is `{}`
- `Button(action="delete", params={"udn": "abc"})` → params is
  `{"udn": "abc"}`
- `Form(action="save_settings")` with child inputs → params is
  `{"bin": "...", "interface": "...", "debug_enabled": True, ...}`
  (all input values collected by name)
- Table inline edit → params is `{"udn": "abc", "name": "new_name"}`
  (row key + changed column)
- Table row action → params is whatever you put in the action's
  `"params"` dict

Here is a concrete action handler — note how it wraps Pinoatrome's
original backend function:

```python
async def _handle_save_settings(params: dict) -> dict:
    if not params:
        return {"error": "No settings provided"}

    settings_list = list(params.items())
    result = _do_save_settings(settings_list)  # ← Pinoatrome's original function!

    if "errors" in result:
        return {"error": result["errors"]}
    return {"success": True, "message": "Settings saved successfully"}
```

This is a key pattern: **SDUI action handlers often wrap existing
backend functions.** If the contributor already wrote the business
logic (and Pinoatrome did), your action handler is just a thin adapter.

---

## Live Updates with SSE

Every SDUI plugin gets Server-Sent Events for free:

```
GET /api/plugins/{plugin_id}/events
→ SSE stream, emits {"event": "ui_refresh"} on state changes
```

When state changes in your plugin, call:

```python
ctx.notify_ui_update()
```

The frontend re-fetches `get_ui()` and re-renders. The user sees
changes instantly — no page reload, no polling delay.

If SSE is unavailable, the frontend falls back to polling using your
`Page(refresh_interval=...)` value.

When Pinoatrome built his version, this infrastructure did not exist
yet. His Svelte components loaded data on mount, which was the standard
approach at the time. SSE support came later as part of the SDUI
framework — now every plugin benefits from it automatically.

---

## The Full Picture: How Frontend and Backend Connect

Here is what happens when a user interacts with your plugin UI — no
frontend code needed on your part:

**1. User opens the plugin page:**

- Frontend fetches `GET /api/plugins/raopbridge/ui`
- Server calls your `get_ui(ctx)` → returns a `Page` with widgets
- `PluginPageView.svelte` renders the widget tree recursively
- SSE connection opens to `/api/plugins/raopbridge/events`

**2. User clicks "Activate":**

- Frontend calls `POST /api/plugins/raopbridge/actions/activate`
- Server calls your `handle_action("activate", {})`
- You return `{"message": "Bridge activated"}`
- Frontend shows a success toast
- You call `ctx.notify_ui_update()` → SSE fires → UI re-renders with
  the new state (badge turns green, buttons change)

**3. User submits the Settings form:**

- Frontend collects all input values by `name`
- Calls `POST /api/plugins/raopbridge/actions/save_settings` with
  `{"bin": "squeeze2raop-linux-x86_64", "interface": "192.168.1.10", ...}`
- Your `_handle_save_settings(params)` processes it
- Returns `{"message": "Settings saved"}`

**4. User edits a device name inline in the table:**

- Frontend dispatches `POST /api/plugins/raopbridge/actions/update_device`
  with `{"udn": "abc-123", "name": "Kitchen Speaker"}`
- Your `_handle_update_device(params)` updates the device
- Returns `{"message": "Device 'Kitchen Speaker' updated"}`

All of this works through the same generic frontend code — the same
`PluginPageView`, `PluginRenderer`, and `pluginActions.dispatch()` that
serve every plugin. Your plugin only provides `get_ui()` and
`handle_action()`.

---

## Adding REST Routes (Optional but Useful)

SDUI handles all UI interactions through `handle_action()`. But you can
_also_ register REST routes — Pinoatrome did, and we kept them:

```python
def define_api_router() -> APIRouter:
    router = APIRouter(prefix="/api/raopbridge", tags=["raopbridge"])

    @router.get("/status")
    async def get_status():
        return {
            "plugin": "enabled" if _raop_bridge else "disabled",
            "bridge": "active" if _raop_bridge and _raop_bridge.is_active else "inactive",
        }

    @router.post("/activate")
    async def do_activate():
        return await _activate()

    # ... more endpoints ...
    return router

async def setup(ctx):
    ctx.register_route(define_api_router())
    ctx.register_ui_handler(get_ui)
    ctx.register_action_handler(handle_action)
```

**When are REST routes useful?**

- **Scripting and automation.** Users and tools can call
  `/api/raopbridge/status` directly.
- **JSON-RPC integration.** The same backend functions serve both
  the REST API and the JSON-RPC command dispatcher.
- **Third-party integrations.** Other systems can interact with
  your plugin without going through the UI.

**REST routes are optional for SDUI.** A simple plugin can do everything
through `get_ui()` + `handle_action()` alone. But if you already have
REST routes (like Pinoatrome did), keep them — they are a nice bonus.

---

## What You Can Learn from Pinoatrome's Journey

Pinoatrome's raopbridge is the most complete community plugin for
Resonance. Here are the patterns worth studying:

### 1. Solid Backend First

The bridge management, config parsing, and device handling are all in
separate modules (`bridge.py`, `config.py`, `serializers.py`). The UI
layer (`get_ui()` / `handle_action()`) is a thin wrapper. This
separation made the SDUI migration straightforward — the business
logic did not change at all.

**Takeaway:** Build your backend logic in its own module(s). Keep the
UI layer thin. If the UI framework changes, your core code survives.

### 2. REST + JSON-RPC + SDUI — All Calling the Same Functions

The plugin has three interface layers — REST routes, a JSON-RPC command
dispatcher, and SDUI action handlers — and they all call the same
underlying functions (`_activate()`, `_deactivate()`, `_devices()`,
`_do_save_settings()`).

**Takeaway:** Write your business logic once. Let multiple interfaces
wrap it.

### 3. Graceful Error Handling

Every builder function handles the "not ready" case:

```python
if not is_active:
    return Tab(label="Devices", icon="speaker", children=[
        Alert(message="Activate the bridge to discover devices.", severity="info"),
    ])
```

**Takeaway:** Never assume your plugin state is valid. Show helpful
messages when things are not ready yet.

### 4. Pinoatrome's Original Svelte Approach vs. SDUI

Here is a quick reference for how Pinoatrome's Svelte patterns
translate to SDUI widgets:

| Pinoatrome Built (Svelte)         | SDUI Widget(s)                                       |
| --------------------------------- | ---------------------------------------------------- |
| `PluginPage.svelte` (4 tabs)      | `Tabs(tabs=[Tab(...), ...])`                         |
| `StatusView.svelte`               | `StatusBadge` + `KeyValue`                           |
| `ToggleBridgeStatus.svelte`       | `Button(action="...", confirm=True)`                 |
| `DeviceList.svelte` + 3 modals    | `Table` with `variant="actions"` + `variant="badge"` |
| `DeviceCard.svelte` (inline edit) | `TableColumn(variant="editable")`                    |
| `DeviceSettings.svelte` (modal)   | `Modal` > `Form` > `Tabs`                            |
| `SettingsView.svelte`             | `Form` with `Select`, `TextInput`, `Toggle`          |
| `AdvancedView.svelte`             | `Card(collapsible=True)` + `KeyValue`                |
| `AboutView.svelte`                | `Markdown` inside a `Card`                           |
| `raopbridgeApi.ts` (API client)   | Not needed — generic `pluginActions.dispatch()`      |

This is not a criticism of Pinoatrome's Svelte code — it was well built.
The SDUI widgets exist _because_ his work showed us what plugin UIs need.
If you are looking at his
[original branch](https://github.com/pinoatrome/resonance-server/tree/feature/raop)
for reference, this table helps you map concepts.

---

## Your Plugin Checklist

Ready to build your own plugin? Here is everything you need:

### Minimum (Plugin with UI)

- [ ] `plugins/<name>/plugin.toml` — manifest with `[ui] enabled = true`
- [ ] `plugins/<name>/__init__.py` — `setup(ctx)`, `teardown(ctx)`
- [ ] `get_ui(ctx)` — returns a `Page` with your widget tree
- [ ] `handle_action(action, params)` — handles user interactions
- [ ] Register both in `setup()`:
    - `ctx.register_ui_handler(get_ui)`
    - `ctx.register_action_handler(handle_action)`

### Recommended

- [ ] `from __future__ import annotations` in every `.py` file
- [ ] `TYPE_CHECKING` guard for all Resonance imports
- [ ] Use `ctx.subscribe()` for events (auto-cleanup on teardown)
- [ ] Use `ctx.ensure_data_dir()` before file I/O
- [ ] Call `ctx.notify_ui_update()` after state changes
- [ ] Handle "not ready" states with `Alert` widgets
- [ ] Use `confirm=True` on destructive buttons
- [ ] Use `.when()` for conditional fields in forms

### If You Have Backend Logic

- [ ] Put business logic in separate modules (like `bridge.py`)
- [ ] Keep the UI layer thin — `get_ui()` and `handle_action()` should
      be wrappers, not business logic
- [ ] Reset module-level state in `teardown()`
- [ ] Tests in `tests/`

### Optional Extras

- [ ] REST routes via `ctx.register_route(router)` for scripting/API
- [ ] JSON-RPC commands via `ctx.register_command(name, handler)`
- [ ] Jive menu entries for Squeezebox hardware UIs
- [ ] Credit contributors in your About tab

---

## Further Reading

| Document                                                                                                        | Content                                                 |
| --------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------- |
| [`PLUGIN_TUTORIAL.md`](PLUGIN_TUTORIAL.md)                                                                      | Step-by-step basics: build a plugin from scratch        |
| [`PLUGIN_API.md`](PLUGIN_API.md)                                                                                | Complete API reference (including §19 SDUI widgets)     |
| [`PLUGINS.md`](PLUGINS.md)                                                                                      | General plugin system overview                          |
| [Community Plugins Repo](https://github.com/endegelaende/resonance-community-plugins)                           | Publishing plugins and repository index                 |
| [`raopbridge` source](https://github.com/endegelaende/resonance-community-plugins/tree/main/plugins/raopbridge) | The complete working plugin discussed in this document  |
| [Issue #11](https://github.com/endegelaende/resonance-server/issues/11)                                         | Pinoatrome's original contribution                      |
| [Pinoatrome's branch](https://github.com/pinoatrome/resonance-server/tree/feature/raop)                         | Original implementation with Svelte UI (for comparison) |

---

_Thank you, Pinoatrome, for the outstanding contribution that made this
plugin — and this document — possible._

_Last updated: March 2026_
