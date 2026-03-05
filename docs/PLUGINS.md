# Plugin System — Overview

A guide to what the Resonance plugin system is, why it exists,
and how it works at a high level — no code required.
Resonance currently ships with five plugins, including Internet Radio
via radio-browser.info and Podcasts.

For the developer perspective (API, code examples, tutorial):
→ [`PLUGIN_API.md`](PLUGIN_API.md)
→ [`PLUGIN_TUTORIAL.md`](PLUGIN_TUTORIAL.md)
→ [Community Plugins Repository](https://github.com/endegelaende/resonance-community-plugins)

---

## 1) What Is the Plugin System?

Resonance is a modular music server. The plugin system allows you to
**add features without modifying the server core**.

A plugin is a small Python package living in its own folder under
`plugins/`. On server startup it is automatically discovered, loaded,
and started. On shutdown it is cleanly stopped and all registrations
are reverted.

```
plugins/
├── example/          ← Example plugin (template)
│   ├── plugin.toml   ← Manifest: name, version, description
│   └── __init__.py   ← Code: what the plugin does
├── favorites/        ← Favorites management (LMS-compatible)
│   ├── plugin.toml
│   ├── __init__.py
│   └── store.py
├── nowplaying/       ← Now Playing tutorial plugin
│   ├── plugin.toml
│   └── __init__.py
├── radio/            ← Internet Radio (radio-browser.info) — first ContentProvider
│   ├── plugin.toml
│   ├── __init__.py   ← Commands, Jive menu, RadioProvider
│   └── radiobrowser.py ← radio-browser.info API client
├── podcast/          ← Podcasts — second ContentProvider
│   ├── plugin.toml
│   ├── __init__.py   ← Commands, Jive menu, PodcastProvider
│   ├── feed_parser.py ← RSS 2.0 / iTunes namespace parser
│   └── store.py      ← Subscriptions, resume positions, recently played
└── my-plugin/        ← Your own plugins are just another folder
    ├── plugin.toml
    └── __init__.py
```

**No server code needs to be touched.** Create a folder, write a manifest,
write some code — done. The plugin is active on the next server start.

---

## 2) Why a Plugin System?

| Problem                                                                  | Solution via Plugins                                                          |
| ------------------------------------------------------------------------ | ----------------------------------------------------------------------------- |
| Everyone wants different features (Internet Radio, Podcasts, Spotify, …) | Everyone builds only the plugin they need                                     |
| New features require changes to the server core                          | Plugins run alongside the core without modifying it                           |
| A bug in one feature can bring down the entire server                    | Plugins are **fault-isolated** — a crash in Plugin A does not affect Plugin B |
| Hard to test when everything is intertwined                              | Plugins have their own tests, data directories, and lifecycle                 |

### LMS Background

The Logitech Media Server (LMS), which Resonance replaces, has a
powerful plugin system — written in Perl and deeply coupled with the
LMS runtime. Resonance plugins are **not compatible** with LMS plugins
but offer a modern Python API covering the same use cases.

---

## 3) What Can a Plugin Do?

A plugin can register seven things:

### 3.1) JSON-RPC Commands

New commands that clients (Material Skin, iPeng, Squeezer, …) can call
via the JSON-RPC interface — just like built-in commands
(`play`, `pause`, `status`, …).

> _Example:_ The Favorites plugin registers `favorites` and `jivefavorites`.
> A client can send `["favorites", "items", 0, 100]` and receives
> the favorites list in return.

### 3.2) Jive Menu Entries

Entries in the touchscreen menu of Squeezebox Touch, Radio, Boom, and Controller.
Plugins define where the entry appears (e.g. in the home menu) and what
happens when it is tapped.

> _Example:_ The Favorites plugin shows "Favorites" in the home menu.
> Tapping it navigates the device into the favorites list.

### 3.3) HTTP Routes (REST API)

Custom web endpoints for plugin-specific APIs or web interfaces.
Built on FastAPI — you can register routers with arbitrary endpoints.

### 3.4) Event Subscriptions

React to server events (e.g. "player connected", "track started",
"library scan completed"). Subscriptions are automatically cleaned up
when the plugin is stopped.

> _Example:_ A scrobbling plugin could subscribe to `player.track_started`
> to report every played track to Last.fm.

### 3.5) Content Providers

Supply external audio sources — Internet Radio stations, podcast episodes,
or streaming service tracks. The server proxies the remote audio stream
to the player (required because Squeezebox hardware cannot handle HTTPS).

> _Example:_ The Radio plugin registers a `ContentProvider` that browses
> radio-browser.info categories, searches for stations, and resolves station UUIDs
> to direct stream URLs. The user sees "Radio" in the Jive home menu.

A content provider implements three methods:

| Method                     | Purpose                                      |
| -------------------------- | -------------------------------------------- |
| `browse(path)`             | Return a hierarchical menu of playable items |
| `search(query)`            | Find items by text query                     |
| `get_stream_info(item_id)` | Resolve an item to a concrete stream URL     |

### 3.6) Data Directory

Each plugin gets its own directory under `data/plugins/<name>/`
where it can store files (configuration, cache, databases, …).

> _Example:_ The Favorites plugin stores `favorites.json` in
> `data/plugins/favorites/`.

### 3.7) Web UI Pages (Server-Driven UI)

Plugins can provide full web UI pages in the Resonance frontend —
without writing any JavaScript. The plugin describes its UI declaratively
in Python using widget classes (`Heading`, `Table`, `Form`, `Button`, …),
and the frontend renders it automatically via a generic recursive renderer.

> _Example:_ The raopbridge community plugin provides a 5-tab UI page
> (Status, Devices, Settings, Advanced, About) with device management,
> inline editing, settings forms, and per-device configuration modals —
> all defined in Python, zero Svelte code.

A SDUI plugin implements two functions:

| Function                             | Purpose                                                           |
| ------------------------------------ | ----------------------------------------------------------------- |
| `get_ui(ctx)`                        | Return a `Page` object describing the current UI state            |
| `handle_action(ctx, action, params)` | Process user interactions (button clicks, form submissions, etc.) |

20+ widget types are available: display widgets (headings, tables, badges, cards, markdown),
layout widgets (tabs, modals, rows, columns), and form widgets (text inputs, selects, toggles,
number inputs, textareas). Widgets support conditional rendering via `visible_when` with
8 comparison operators.

For details, see [`PLUGIN_API.md` §19](PLUGIN_API.md#19-server-driven-ui-sdui).

---

## 4) How Does It Work? (Lifecycle)

```
                         ┌──────────────────────────────────┐
                         │         Server starts            │
                         └──────────┬───────────────────────┘
                                    │
                         ┌──────────▼───────────────────────┐
                    1.   │  Discover                        │
                         │  Scan plugins/ directory          │
                         │  Read plugin.toml manifests       │
                         └──────────┬───────────────────────┘
                                    │
                         ┌──────────▼───────────────────────┐
                    2.   │  Load                            │
                         │  Import Python module             │
                         │  Verify setup() function          │
                         └──────────┬───────────────────────┘
                                    │
                         ┌──────────▼───────────────────────┐
                    3.   │  Start                           │
                         │  Call setup(ctx)                  │
                         │  → Register commands              │
                         │  → Register menus                 │
                         │  → Subscribe to events            │
                         │  → Load data                      │
                         └──────────┬───────────────────────┘
                                    │
                         ┌──────────▼───────────────────────┐
                         │       Server is running          │
                         │  Plugins process requests         │
                         └──────────┬───────────────────────┘
                                    │
                         ┌──────────▼───────────────────────┐
                    4.   │  Stop                            │
                         │  Call teardown(ctx)               │
                         │  → Save state                     │
                         │  → Automatic cleanup of all       │
                         │    registrations                  │
                         └──────────────────────────────────┘
```

### Key Properties

| Property            | Description                                                                                                             |
| ------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| **Auto-Discovery**  | Any folder with a `plugin.toml` is detected — no manual registration needed                                             |
| **Auto-Cleanup**    | Everything registered in `setup()` is automatically removed after `teardown()`                                          |
| **Fault Isolation** | If a plugin crashes during startup, its partial registrations are rolled back. Other plugins continue to start normally |
| **Order Guarantee** | Plugins are started in alphabetical order and stopped in reverse order (LIFO)                                           |

---

## 5) Included & Community Plugins

### 5.1) Example Plugin

|             |                                                           |
| ----------- | --------------------------------------------------------- |
| **Purpose** | Template and API demo — showcases all plugin capabilities |
| **Folder**  | `plugins/example/`                                        |
| **Command** | `example.hello` — returns a greeting                      |
| **Menu**    | "Example Plugin" in the Jive home menu                    |
| **Events**  | Counts started tracks, logs server start                  |

The Example plugin is intentionally simple. It serves as a copy-paste
template for your own plugins.

### 5.2) Favorites Plugin

|              |                                                                            |
| ------------ | -------------------------------------------------------------------------- |
| **Purpose**  | Favorites management — LMS-compatible                                      |
| **Folder**   | `plugins/favorites/`                                                       |
| **Commands** | `favorites` (items, add, addlevel, delete, rename, move, exists, playlist) |
|              | `jivefavorites` (add/delete confirmation, preset buttons)                  |
| **Menu**     | "Favorites" in the Jive home menu (weight 55, matching LMS)                |
| **Storage**  | `data/plugins/favorites/favorites.json`                                    |
| **Features** | Hierarchical folders, URL deduplication, search filter, pagination         |
| **Tests**    | 152 tests                                                                  |

The Favorites plugin is a **reference implementation** — a complete,
production-ready plugin that serves as a model for complex plugins.

### 5.3) Radio Plugin (radio-browser.info)

|                      |                                                                                                                                                                                                                    |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Purpose**          | Internet Radio — browse, search, and stream live radio via radio-browser.info                                                                                                                                      |
| **Folder**           | `plugins/radio/`                                                                                                                                                                                                   |
| **Commands**         | `radio` (items, search, play)                                                                                                                                                                                      |
| **Menu**             | "Radio" in the Jive home menu (weight 45 — between My Music and Favorites)                                                                                                                                         |
| **Content Provider** | Registered as `"radio"` — first ContentProvider plugin                                                                                                                                                             |
| **Features**         | radio-browser.info API (~40,000+ stations), browse by country/genre/language, full-text search, pre-resolved stream URLs, play/add/insert, add-to-favorites context menu. Free community API, no API key required. |
| **Caching**          | 10-minute TTL browse cache (256 entries)                                                                                                                                                                           |
| **Tests**            | 114 tests                                                                                                                                                                                                          |

The Radio plugin is the **first ContentProvider plugin** — it demonstrates
the full content-provider lifecycle: browsing a remote API, resolving
stream URLs, and starting playback through the server's URL proxy.

### 5.4) Now Playing Tutorial Plugin

|              |                                           |
| ------------ | ----------------------------------------- |
| **Purpose**  | Companion code for the Plugin Tutorial    |
| **Folder**   | `plugins/nowplaying/`                     |
| **Commands** | `nowplaying.stats`, `nowplaying.recent`   |
| **Menu**     | "Now Playing Stats" in the Jive home menu |
| **Features** | Play history store, event subscription    |
| **Tests**    | 58 tests                                  |

The Now Playing plugin is a **tutorial companion** — it walks through
building a plugin step by step in [`PLUGIN_TUTORIAL.md`](PLUGIN_TUTORIAL.md).

### 5.5) Podcast Plugin

|                      |                                                                                                                                                                                                                                                                          |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Purpose**          | Podcasts — browse, search, subscribe, and stream episodes with resume support                                                                                                                                                                                            |
| **Folder**           | `plugins/podcast/`                                                                                                                                                                                                                                                       |
| **Commands**         | `podcast` (items, search, play, addshow, delshow)                                                                                                                                                                                                                        |
| **Menu**             | "Podcasts" in the Jive home menu (weight 50 — between Radio and Favorites)                                                                                                                                                                                               |
| **Content Provider** | Registered as `"podcast"` — second ContentProvider plugin                                                                                                                                                                                                                |
| **Features**         | RSS 2.0 feed parsing (iTunes namespace), PodcastIndex search API, subscription management (subscribe/unsubscribe), resume position tracking (LMS-compatible threshold logic), recently played episodes (LRU, 50 entries), play/add/insert, add-to-favorites context menu |
| **Storage**          | `data/plugins/podcast/podcasts.json` (subscriptions, resume positions, recently played)                                                                                                                                                                                  |
| **Caching**          | 10-minute TTL feed cache                                                                                                                                                                                                                                                 |
| **Tests**            | 178 tests                                                                                                                                                                                                                                                                |

The Podcast plugin is the **second ContentProvider plugin** — it
demonstrates RSS feed parsing, subscription persistence, resume
position tracking, and PodcastIndex API integration alongside the
full content-provider lifecycle.

---

## 6) Current Extensions

### Phase 2 — Content Providers ✅ Complete

Plugins can now provide external audio sources (Internet Radio,
Podcasts, streaming services). Both infrastructure and the first
plugin are implemented:

- ✅ `ContentProvider` abstract base class (`browse`, `search`, `get_stream_info`)
- ✅ `ContentProviderRegistry` — central registry with error-isolated delegation
- ✅ `PluginContext.register_content_provider()` — with automatic cleanup
- ✅ URL proxy in the streaming server (httpx-based, handles HTTPS for hardware players)
- ✅ ICY/Shoutcast metadata stripping in the proxy stream
- ✅ `PlaylistTrack` extended for remote sources (`is_remote`, `stream_url`, `source`, …)
- ✅ Playback handlers branch on local/remote tracks automatically
- ✅ 88 tests covering all infrastructure components
- ✅ **Radio Plugin** (radio-browser.info) — first production ContentProvider (114 tests)
- ✅ **Podcast Plugin** — second ContentProvider (RSS feeds, PodcastIndex search, subscriptions, resume positions, 178 tests)

### Phase 3 — Plugin Management ✅ Complete

- ✅ Declarative plugin settings in `plugin.toml` (`[settings.<key>]`) with validation
- ✅ Settings persistence in `data/plugins/<plugin>/settings.json`
- ✅ Plugin state persistence in `data/plugin_states.json` (enable/disable)
- ✅ Community plugin install/uninstall via ZIP + SHA256 verification
- ✅ Plugin repository index integration (available/install/update metadata)
- ✅ REST endpoints for plugin management (`/api/plugins*`)
- ✅ JSON-RPC management commands (`pluginsettings`, `pluginmanager`)
- ✅ Svelte PluginsView with Installed / Available / Settings tabs

### Phase 4 — Server-Driven UI (SDUI) ✅ Complete

- ✅ 20+ widget types (display, layout, form) in `resonance/ui/__init__.py`
- ✅ Recursive frontend renderer with `visible_when` conditional rendering (8 operators)
- ✅ SSE real-time updates (`EventSource` + polling fallback)
- ✅ Inline-editable table columns, collapsible cards, modal dialogs
- ✅ Form widgets with dirty tracking and disabled state
- ✅ Security headers middleware (CSP, X-Frame-Options, etc.)
- ✅ raopbridge as first SDUI consumer (5-tab UI, device management, settings)
- ✅ 184 SDUI tests + 166 raopbridge tests + 98 security tests

### Phase 5 — Ecosystem Growth (ongoing)

- SDUI adoption for more plugins (favorites, radio, podcast)
- Native reimplementation of popular LMS plugin functionality (Spotify, YouTube, …)
- External plugin publishing workflow and CI/CD for repository releases

---

## 7) Frequently Asked Questions

### Are LMS plugins compatible?

**No.** LMS plugins are written in Perl and use internal LMS APIs
(`Slim::Plugin::Base`, `Slim::Control::Request`, …). Resonance plugins
are written in Python and use a separate, modern API.

Popular LMS plugin functionality is instead reimplemented natively
as Resonance plugins.

### Can plugins stream Internet Radio?

**Yes.** The included Radio plugin streams Internet Radio via radio-browser.info —
browse by country, genre, or language, search stations, and play live streams.
The server proxies the remote audio (HTTP/HTTPS) to the player hardware. ICY
metadata (station/title changes) is stripped and logged. You can also
build your own streaming plugins using the `ContentProvider` API —
see [`PLUGIN_API.md`](PLUGIN_API.md) §16 for details.

### Can plugins stream Podcasts?

**Yes.** The included Podcast plugin lets you search for podcasts via
PodcastIndex, subscribe to RSS feeds, browse episodes, and stream them.
Resume positions are tracked automatically — if you stop mid-episode,
the next time you see "Play from last position" or "Play from the
beginning" (matching LMS behaviour). The server proxies the audio
stream to the player, just like Internet Radio.

### Can a plugin crash the server?

**Unlikely.** Plugin code runs inside `try/except` blocks.
An error in a plugin is logged, but the server and other plugins
continue to run. However, plugins run in the same process as the
server — a `sys.exit()` or memory corruption would terminate the
entire process.

### Do I need Python knowledge?

**Yes.** Plugins are written in Python. Basic knowledge of
asyncio (async/await) is helpful since all handlers are asynchronous.

### Can a plugin have a Web UI page?

**Yes.** Using the Server-Driven UI (SDUI) system, a plugin can provide
a full web UI page without writing any JavaScript. The plugin describes
its UI in Python and the Resonance frontend renders it automatically.
See [`PLUGIN_API.md` §19](PLUGIN_API.md#19-server-driven-ui-sdui) for details
and the raopbridge community plugin as a reference implementation.

### Where can I find help for developing my own plugins?

→ [`PLUGIN_TUTORIAL.md`](PLUGIN_TUTORIAL.md) — Step-by-step guide
→ [`PLUGIN_API.md`](PLUGIN_API.md) — Complete API reference (including §19 SDUI)
→ [Community Plugins Repository](https://github.com/endegelaende/resonance-community-plugins) — How to publish community plugins
→ `plugins/example/` — Minimal template
→ `plugins/favorites/` — Reference plugin (commands, menus, persistence)
→ `plugins/radio/` — Reference ContentProvider plugin (radio-browser.info, remote streaming)
→ `plugins/podcast/` — Reference ContentProvider plugin (RSS feeds, subscriptions, resume)
→ [`raopbridge`](https://github.com/endegelaende/resonance-community-plugins/tree/main/plugins/raopbridge) — Reference SDUI plugin (AirPlay bridge with full UI)

---

## 8) Plugin Management (for operators)

The plugin system now supports full lifecycle operations without editing code.

### Installed Plugins

- `GET /api/plugins` returns plugin metadata, state (`enabled`/`disabled`), type (`core`/`community`), and `restart_required`.
- `POST /api/plugins/{name}/enable` and `POST /api/plugins/{name}/disable` toggle runtime state persistence.
- Core plugins cannot be uninstalled.

### Plugin Settings

- Settings are declared in `plugin.toml` via `[settings.<key>]` tables.
- Current values and definitions are available via `GET /api/plugins/{name}/settings`.
- Updates are applied via `PUT /api/plugins/{name}/settings` with type validation and secret masking in responses.

### Repository and Installation

- Repository index: `GET /api/plugins/repository` (supports `force_refresh=true`).
- Install from repository: `POST /api/plugins/install-from-repo` with JSON body `{ "name": "plugin_name" }`.
- Direct install from ZIP URL: `POST /api/plugins/install` with `{ "url": "...", "sha256": "..." }`.
- Uninstall community plugins: `POST /api/plugins/{name}/uninstall`.

For implementation details and manifest schema, see [`PLUGIN_API.md`](PLUGIN_API.md)
and the [Community Plugins Repository](https://github.com/endegelaende/resonance-community-plugins).

---

_Last updated: March 2026_
