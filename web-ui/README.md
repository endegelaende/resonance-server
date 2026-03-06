# Resonance Web UI

Modern, reactive web interface for the [Resonance](../README.md) music server.
Built with **Svelte 5**, **SvelteKit**, **Tailwind CSS v4**, and **TypeScript**.

Serves as both the primary user-facing frontend and the SDUI (Server-Driven UI)
rendering engine for plugin pages.

---

## Table of Contents

- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Component Inventory](#component-inventory)
- [Store System](#store-system)
- [API Client](#api-client)
- [SDUI Renderer (Plugin UI)](#sdui-renderer-plugin-ui)
- [Theming](#theming)
- [Development](#development)
- [Production Build](#production-build)
- [Conventions](#conventions)

---

## Architecture

```text
┌───────────────────────────────────────────────────────────────┐
│                        Browser                                │
│                                                               │
│  ┌─────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐  │
│  │ Sidebar  │  │ Main     │  │ Queue    │  │ Now Playing  │  │
│  │ (nav +   │  │ Content  │  │ Panel    │  │ (transport + │  │
│  │ plugins) │  │ (views)  │  │          │  │  cover art)  │  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └──────┬───────┘  │
│       │              │             │               │          │
│  ┌────┴──────────────┴─────────────┴───────────────┴───────┐  │
│  │                    4 Svelte Stores                       │  │
│  │  playerStore · uiStore · colorStore · toastStore        │  │
│  └─────────────────────────┬───────────────────────────────┘  │
│                            │                                  │
│  ┌─────────────────────────┴───────────────────────────────┐  │
│  │               api.ts (ResonanceAPI)                     │  │
│  │   JSON-RPC (LMS compat) + REST + SSE                   │  │
│  └─────────────────────────┬───────────────────────────────┘  │
└────────────────────────────┼──────────────────────────────────┘
                             │ HTTP
┌────────────────────────────┼──────────────────────────────────┐
│              Resonance Server (Python)                        │
│   Port 9000 · FastAPI · JSON-RPC · Cometd · Slimproto        │
└───────────────────────────────────────────────────────────────┘
```

The UI is a **single-page application** (SPA) built with SvelteKit's static
adapter. In production, the Python backend serves the built files from
`web-ui/build/`. During development, Vite's dev server on port 5173 proxies
API requests to the backend on port 9000.

---

## Tech Stack

| Layer            | Technology                                                 | Version |
| ---------------- | ---------------------------------------------------------- | ------- |
| Framework        | [Svelte 5](https://svelte.dev/) + SvelteKit                | 5.x     |
| Styling          | [Tailwind CSS v4](https://tailwindcss.com/)                | 4.x     |
| Icons            | [Lucide Svelte](https://lucide.dev/)                       | 0.469+  |
| Build Tool       | [Vite](https://vitejs.dev/)                                | 6.x     |
| Type Safety      | TypeScript                                                 | 5.5+    |
| Color Extraction | [node-vibrant](https://github.com/nicokoenig/node-vibrant) | 4.x     |
| Placeholders     | [BlurHash](https://blurha.sh/)                             | 2.x     |
| Markdown         | [marked](https://marked.js.org/)                           | 17.x    |
| Adapter          | `@sveltejs/adapter-static` (SPA mode)                      | 3.x     |

---

## Project Structure

```text
web-ui/
├── src/
│   ├── app.css                           # Tailwind imports + warm vinyl-lounge theme + utilities
│   ├── app.html                          # HTML shell
│   ├── routes/
│   │   ├── +layout.ts                    # SPA prerender config
│   │   └── +page.svelte                  # Main SPA page (~1000 LOC)
│   └── lib/
│       ├── api.ts                        # ResonanceAPI client (~1900 LOC, ~60 methods)
│       ├── stores/
│       │   ├── player.svelte.ts          # Player state, transport, playlist, polling
│       │   ├── ui.svelte.ts              # Navigation, view state, modals
│       │   ├── color.svelte.ts           # Dynamic accent colors from album art
│       │   └── toast.svelte.ts           # Toast notifications
│       ├── components/                   # App-level Svelte components (20)
│       │   ├── NowPlaying.svelte         # Transport controls + cover art + progress
│       │   ├── Sidebar.svelte            # Navigation + plugin page entries
│       │   ├── Queue.svelte              # Playlist/queue panel
│       │   ├── TrackList.svelte          # Track listing with play/add actions
│       │   ├── SearchBar.svelte          # Global search
│       │   ├── PlayerSelector.svelte     # Player picker dropdown
│       │   ├── CoverArt.svelte           # Album art with BlurHash placeholder
│       │   ├── BlurHashPlaceholder.svelte# Canvas-based BlurHash decoder
│       │   ├── QualityBadge.svelte       # Audio format/quality indicator
│       │   ├── DynamicIcon.svelte        # Lucide icon resolver (46 icons)
│       │   ├── SettingsPanel.svelte      # Server + player settings
│       │   ├── AlarmSettings.svelte      # Player alarm configuration
│       │   ├── RadioView.svelte          # Internet radio browser
│       │   ├── PodcastView.svelte        # Podcast browser
│       │   ├── FavoritesView.svelte      # Favorites management
│       │   ├── PlaylistsView.svelte      # Saved playlists
│       │   ├── PluginsView.svelte        # Plugin manager orchestrator (tabs + shared state)
│       │   ├── PluginCard.svelte         # Reusable plugin card (badges, error banner, action slots)
│       │   ├── PluginsInstalled.svelte   # Installed tab (plugin grid, toggle, settings, uninstall)
│       │   ├── PluginsAvailable.svelte   # Available tab (search, filter, install/update)
│       │   ├── PluginSettings.svelte     # Settings tab (type-aware form, save, reset)
│       │   ├── AddFolderModal.svelte     # Music folder dialog
│       │   ├── ResizeHandle.svelte       # Panel resize handles
│       │   └── ToastContainer.svelte     # Toast notification display
│       └── plugin-ui/                    # SDUI renderer (Plugin UI system)
│           ├── PluginPageView.svelte     # Page loader + SSE/polling
│           ├── PluginRenderer.svelte     # Recursive component renderer
│           ├── registry.ts              # Widget type → component map
│           ├── actions.svelte.ts        # Action dispatcher
│           └── widgets/                 # 20 SDUI widget components
│               ├── ActionButton.svelte
│               ├── Alert.svelte
│               ├── Card.svelte
│               ├── Column.svelte
│               ├── DataTable.svelte
│               ├── Form.svelte
│               ├── Heading.svelte
│               ├── KeyValue.svelte
│               ├── MarkdownBlock.svelte
│               ├── Modal.svelte
│               ├── NumberInput.svelte
│               ├── ProgressBar.svelte
│               ├── Row.svelte
│               ├── Select.svelte
│               ├── StatusBadge.svelte
│               ├── Tabs.svelte
│               ├── TextBlock.svelte
│               ├── TextInput.svelte
│               ├── Textarea.svelte
│               └── Toggle.svelte
├── static/
│   └── fonts/                           # Self-hosted fonts
├── build/                               # Production output (git-ignored)
├── package.json
├── svelte.config.js
├── tsconfig.json
└── vite.config.ts
```

---

## Component Inventory

### App Components (`src/lib/components/` — 24 files)

| Component             | Responsibility                                                                                          |
| --------------------- | ------------------------------------------------------------------------------------------------------- |
| `NowPlaying`          | Full transport bar: cover art, track info, play/pause/seek, volume, progress bar, dynamic colors        |
| `Sidebar`             | Navigation: Menu, Collections, Sources, Plugin Pages (dynamic), Settings/Plugins footer                 |
| `Queue`               | Current playlist: reorder, remove, jump-to-index, track info                                            |
| `TrackList`           | Album/artist track listing with play, add-to-queue, delete actions                                      |
| `SearchBar`           | Global search across artists, albums, tracks                                                            |
| `PlayerSelector`      | Dropdown to select active Squeezebox/Squeezelite player                                                 |
| `CoverArt`            | Album artwork with BlurHash instant preview and dynamic color extraction                                |
| `BlurHashPlaceholder` | Canvas-rendered BlurHash placeholder (instant preview before image loads)                               |
| `QualityBadge`        | Audio quality indicator (Lossless/Hi-Res/Lossy with format details)                                     |
| `DynamicIcon`         | Lucide icon resolver — maps 46 string names to icon components                                          |
| `SettingsPanel`       | Server settings (music folders, scanning) + player prefs (crossfade, ReplayGain, gapless) + sync/alarms |
| `AlarmSettings`       | Per-player alarm configuration (time, days, volume, repeat)                                             |
| `RadioView`           | Internet radio browser (radio-browser.info): countries, genres, search, play                            |
| `PodcastView`         | Podcast browser: discovery, search, subscribe, play episodes                                            |
| `FavoritesView`       | Favorites management: folders, add/rename/delete, play                                                  |
| `PlaylistsView`       | Saved playlists: create, rename, delete, load, view tracks                                              |
| `PluginsView`         | Plugin manager orchestrator: shared state, API calls, tab switching — delegates to sub-components       |
| `PluginCard`          | Reusable plugin card: name, version, badges (type/state/error), error/warning banners, action snippets  |
| `PluginsInstalled`    | Installed tab: plugin grid with enable/disable, settings, uninstall actions                             |
| `PluginsAvailable`    | Available tab: repository browser with search, category filter, install/update buttons                  |
| `PluginSettings`      | Settings tab: type-aware form (string/int/float/bool/select), save, reset defaults                      |
| `AddFolderModal`      | Modal dialog for adding music folders                                                                   |
| `ResizeHandle`        | Draggable resize handles for sidebar and queue panels                                                   |
| `ToastContainer`      | Toast notification container with auto-dismiss and animations                                           |

### SDUI Widgets (`src/lib/plugin-ui/widgets/` — 20 files)

| Widget          | Type Key       | Category | Description                                                                |
| --------------- | -------------- | -------- | -------------------------------------------------------------------------- |
| `Heading`       | `heading`      | Display  | h1–h4 with themed styling                                                  |
| `TextBlock`     | `text`         | Display  | Paragraph text with optional color and size (sm/md/lg)                     |
| `StatusBadge`   | `status_badge` | Display  | Colored pill badge with dot indicator                                      |
| `KeyValue`      | `key_value`    | Display  | Key-value pairs list with optional color per value                         |
| `ProgressBar`   | `progress`     | Display  | Animated progress bar with label and percentage                            |
| `MarkdownBlock` | `markdown`     | Display  | Full GFM rendering via `marked` with themed custom renderer                |
| `Alert`         | `alert`        | Display  | Info/success/warning/error message box                                     |
| `Card`          | `card`         | Layout   | Container with title, optional collapsible                                 |
| `Row`           | `row`          | Layout   | Horizontal flex container with gap, justify, align                         |
| `Column`        | `column`       | Layout   | Vertical flex container with gap                                           |
| `Tabs`          | `tabs`         | Layout   | Client-side tab navigation with icons                                      |
| `Modal`         | `modal`        | Layout   | Dialog overlay with trigger button, focus trap, 4 sizes                    |
| `ActionButton`  | `button`       | Action   | Action-dispatching button with icon, spinner, confirm dialog               |
| `DataTable`     | `table`        | Action   | Table with row-click dispatch, badge cells, action buttons, inline editing |
| `Form`          | `form`         | Form     | Form wrapper: dirty tracking, validation, submit with spinner              |
| `TextInput`     | `text_input`   | Form     | Text field with label, validation, pattern, help_text                      |
| `Textarea`      | `textarea`     | Form     | Multi-line text with maxlength counter, help_text                          |
| `NumberInput`   | `number_input` | Form     | Number field with min/max/step, range hint, help_text                      |
| `Select`        | `select`       | Form     | Dropdown with options, custom chevron, help_text                           |
| `Toggle`        | `toggle`       | Form     | Switch with accessible role, help_text                                     |

---

## Store System

All stores use **Svelte 5 Runes** (`$state`, `$derived`, `$effect`) for
fine-grained reactivity. No legacy writable/readable stores.

### `playerStore` — Player State & Transport

The heart of the UI. Manages all player interaction:

- **State:** players list, selected player, playback status (mode/volume/muted/time/duration), current track, playlist
- **Transport:** play, pause, stop, togglePlayPause, next, previous, seek, volume, mute
- **Playlist:** jumpToIndex, addToPlaylist, removeTrack, clearPlaylist, playTrack, playAlbum
- **Polling:** Configurable polling with adaptive intervals (faster when playing)
- **Elapsed Time Smoothing:** `requestAnimationFrame` interpolation between server polls for smooth progress bars — no jitter even at 2-second poll intervals
- **Optimistic Updates:** Pending actions (play/pause/volume/seek) apply instantly in the UI, reconciled on next server poll

### `uiStore` — Navigation & Layout

Simple class-based store for UI state:

- **Views:** `artists`, `albums`, `tracks`, `search`, `playlists`, `radio`, `plugins`, `settings`, `plugin:<id>`
- **Drill-down:** `viewArtist()` → `viewAlbum()` → tracks, with `goBack()` breadcrumb support
- **Plugin pages:** `navigateToPlugin(pluginId)` sets `activePluginId` and switches view
- **Layout:** sidebar open/close (mobile), modal state

### `colorStore` — Dynamic Accent Colors

Extracts color palettes from album artwork using node-vibrant:

- **Extraction:** 6-color palette (vibrant, light/dark vibrant, muted, light/dark muted)
- **Quality checks:** `isTooDark()`, `isTooLight()`, `ensureVibrant()` adjustments
- **CSS variables:** Sets `--dynamic-accent`, `--dynamic-accent-rgb`, etc. on `:root`
- **Caching:** URL → palette cache avoids re-extraction
- **Default palette:** Warm amber (`#e09f5a`) when no artwork

### `toastStore` — Notifications

Toast notification system with auto-dismiss:

- **Types:** success (3.5s), info (4s), warning (5s), error (6s)
- **Features:** max 5 visible, exit animation, manual dismiss, detail text
- **Usage:** `toastStore.success("Album added")`, `toastStore.error("Failed", { detail: "..." })`

---

## API Client

`src/lib/api.ts` — **~1900 lines, ~60 methods**

A single `ResonanceAPI` class that wraps all server communication:

| Category       | Methods                                                                                                                          |
| -------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| **Server**     | `getServerStatus`                                                                                                                |
| **Players**    | `getPlayers`, `getPlayerStatus`, `getPlayerPref`, `setPlayerPref`, `getRuntimePrefs`, `setRuntimePrefs`                          |
| **Transport**  | `play`, `pause`, `stop`, `togglePlayPause`, `next`, `previous`, `seek`, `setVolume`, `adjustVolume`, `toggleMute`                |
| **Sync**       | `getSyncBuddies`, `syncPlayer`, `unsyncPlayer`, `getSyncGroups`                                                                  |
| **Alarms**     | `getAlarms`, `addAlarm`, `updateAlarm`, `deleteAlarm`, `enableAllAlarms`, `disableAllAlarms`, `setDefaultAlarmVolume`            |
| **Playlist**   | `playTrack`, `playAlbum`, `addTrack`, `insertTrack`, `clearPlaylist`, `removeFromPlaylist`, `getPlaylist`, `jumpToIndex`         |
| **Library**    | `getArtists`, `getAlbums`, `getTracks`, `search`, `deleteAlbum`, `deleteTrack`                                                   |
| **Scanning**   | `rescan`, `wipecache`, `getMusicFolders`, `addMusicFolder`, `removeMusicFolder`, `startScan`, `getScanStatus`                    |
| **BlurHash**   | `getTrackBlurHash`, `getAlbumBlurHash`, `isBlurHashAvailable`                                                                    |
| **Settings**   | `getSettings`, `updateSettings`, `resetSettings`                                                                                 |
| **Plugins**    | `getPlugins`, `enablePlugin`, `disablePlugin`, `uninstallPlugin`, `getPluginSettings`, `updatePluginSettings`                    |
| **Repository** | `getRepository`, `installFromRepository`                                                                                         |
| **Favorites**  | `getFavorites`, `addFavorite`, `deleteFavorite`, `renameFavorite`, `addFavoriteFolder`, `favoriteExists`, `playFavorites`        |
| **Radio**      | `getRadioItems`, `searchRadio`, `playRadio`                                                                                      |
| **Podcasts**   | `getPodcastItems`, `searchPodcasts`, `playPodcast`, `podcastSubscribe`, `podcastUnsubscribe`                                     |
| **Playlists**  | `getSavedPlaylists`, `getSavedPlaylistTracks`, `savePlaylist`, `loadSavedPlaylist`, `deleteSavedPlaylist`, `renameSavedPlaylist` |
| **Plugin UI**  | `getPluginUIRegistry`, `getPluginUI`, `dispatchPluginAction`                                                                     |

**Transport protocol:** Library/player/playlist commands go through **JSON-RPC**
(LMS-compatible wire format). Settings, plugins, scanning, and plugin UI use
**REST** endpoints. Plugin UI updates use **SSE** (Server-Sent Events) with
polling fallback.

---

## SDUI Renderer (Plugin UI)

The SDUI system allows plugins to define their UI in Python — the frontend
renders it automatically with no plugin-specific JavaScript.

### How It Works

```text
Plugin (Python)                    Frontend (Svelte)
─────────────────                  ──────────────────

get_ui(ctx) → Page                 GET /api/plugins/{id}/ui
  ├── Card(title="Status")    →      PluginPageView.svelte
  │   ├── StatusBadge(...)             │
  │   └── KeyValue(...)                ├── PluginRenderer.svelte (recursive)
  ├── Tabs(tabs=[...])                 │     registry.ts → Widget lookup
  └── Form(action="save")             │     visible_when evaluation
      ├── TextInput(...)               │
      └── Toggle(...)                  └── widgets/*.svelte (20 components)

handle_action(action, params)      POST /api/plugins/{id}/actions/{action}
  → {"message": "Saved"}               → toastStore.success("Saved")

notify_ui_update()                 SSE /api/plugins/{id}/events
  → {"event": "ui_refresh"}           → re-fetch UI (automatic)
```

### Key Files

| File                    | Role                                                                                                                                      |
| ----------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `PluginPageView.svelte` | Top-level page loader. SSE connection with exponential backoff, polling fallback.                                                         |
| `PluginRenderer.svelte` | Recursive renderer: looks up widget by `type` in registry, evaluates `visible_when` conditions, renders `fallback_text` for unknown types |
| `registry.ts`           | Maps type strings (`"heading"`, `"form"`, etc.) to Svelte components                                                                      |
| `actions.svelte.ts`     | `pluginActions.dispatch(pluginId, action, params)` — wraps REST call                                                                      |
| `widgets/*.svelte`      | 20 self-contained widget components (see inventory above)                                                                                 |

### SDUI Features

- **Conditional rendering:** `visible_when` with 8 operators (eq, ne, gt, lt, gte, lte, in, not_in)
- **Form system:** `formContext` via Svelte context — dirty tracking, disabled propagation, value collection
- **Focus trap:** Modal implements full Tab/Shift+Tab focus trap with focus restore
- **Row-click dispatch:** DataTable dispatches `edit_action` with full row data on row click (pointer cursor, loading state)
- **Inline editing:** DataTable supports click-to-edit cells with Enter/Escape/Blur commit
- **Secure Markdown:** Custom `marked` renderer with HTML disabled, URL scheme allowlist, attribute escaping
- **`help_text`:** Form widgets show contextual help below the field (hidden during validation errors)
- **`fallback_text`:** Unknown widget types display graceful fallback instead of crashing
- **SSE live updates:** Server can push `ui_refresh` events; frontend re-fetches automatically

### Security Model

SDUI is **secure by construction**:

- No `{@html}` anywhere except `MarkdownBlock` (which uses a custom renderer that never parses raw HTML)
- No `eval()`, no `Function()`, no dynamic script injection
- All plugin content is rendered through typed Svelte components
- URLs validated against safe scheme allowlist (`http:`, `https:`, `mailto:`)
- Event handlers are never passed from server to client

---

## Theming

### Warm Vinyl-Lounge Color System

The theme is defined in `src/app.css` using Tailwind v4's `@theme` directive.
A warm, dark palette inspired by vinyl lounges and espresso bars:

| Token           | Hex       | Usage                          |
| --------------- | --------- | ------------------------------ |
| `base`          | `#1c1917` | Main background                |
| `mantle`        | `#171412` | Sidebar, secondary backgrounds |
| `crust`         | `#110f0d` | Deepest background             |
| `surface-0`     | `#292524` | Cards, elevated surfaces       |
| `surface-1`     | `#3b3633` | Borders, dividers              |
| `surface-2`     | `#4d4642` | Hover states                   |
| `overlay-0/1/2` | `#6b–#9e` | Muted text, placeholders       |
| `text`          | `#ede0d4` | Primary text                   |
| `subtext-0/1`   | `#c4–#d5` | Secondary text, labels         |
| `accent`        | `#e09f5a` | Primary accent (Warm Amber)    |
| `success`       | `#8fbe7a` | Success states (Green)         |
| `warning`       | `#e8c468` | Warning states (Yellow)        |
| `error`         | `#d97070` | Error states (Red)             |
| `border`        | `#332e2b` | Border color                   |

### Dynamic Colors

When album artwork changes, `colorStore` extracts a palette via node-vibrant
and sets CSS custom properties:

- `--dynamic-accent` / `--dynamic-accent-rgb` — primary artwork color
- `--dynamic-accent-light` / `--dynamic-accent-dark` — lighter/darker variants
- `--dynamic-muted` / `--dynamic-muted-light` / `--dynamic-muted-dark` — muted variants

Use with utility classes: `dynamic-accent`, `dynamic-accent-bg`, `dynamic-glow`, etc.

---

## Development

### Prerequisites

- **Node.js 18+** and npm
- **Resonance Python backend** running on port 9000 (see [main README](../README.md))

### Quick Start

```bash
cd web-ui
npm install        # one-time dependency install
npm run dev        # Vite dev server → http://localhost:5173
```

Or use the combined dev script from the project root:

```powershell
.\scripts\dev.ps1  # starts backend (9000) + frontend (5173)
```

### Vite Proxy Configuration

During development, Vite proxies API requests to the Python backend
(`vite.config.ts`):

| Path          | Proxied To              |
| ------------- | ----------------------- |
| `/jsonrpc.js` | `http://localhost:9000` |
| `/jsonrpc`    | `http://localhost:9000` |
| `/cometd`     | `http://localhost:9000` |
| `/api`        | `http://localhost:9000` |
| `/stream.mp3` | `http://localhost:9000` |
| `/health`     | `http://localhost:9000` |

### Commands

| Command               | Description                                    |
| --------------------- | ---------------------------------------------- |
| `npm run dev`         | Development server with hot reload (port 5173) |
| `npm run build`       | Production build (output in `build/`)          |
| `npm run preview`     | Preview production build locally               |
| `npm run check`       | TypeScript type checking via svelte-check      |
| `npm run check:watch` | Type checking in watch mode                    |

---

## Production Build

```bash
cd web-ui
npm run build
```

Output goes to `web-ui/build/`. The Python backend serves these files
statically at `http://localhost:9000/`.

The build uses `@sveltejs/adapter-static` in SPA mode with `index.html`
as the fallback page (client-side routing).

---

## Conventions

### Svelte 5 Runes

All components use the Svelte 5 API exclusively:

- `$props()` for component inputs (no `export let`)
- `$state()` for local reactive state
- `$derived()` / `$derived.by()` for computed values
- `$effect()` for side effects
- `Snippet` type for slot content (`{@render children()}`)
- `setContext` / `getContext` for dependency injection (form context, plugin ID)

### Styling

- **Tailwind utility classes** for all styling — no component CSS
- **Theme tokens** (`bg-surface-0`, `text-overlay-1`, etc.) — never raw hex values
- **Transitions:** `transition-colors`, `transition-all` with appropriate durations
- **Responsive:** Mobile-first with `lg:` breakpoint for desktop layout

### API Communication

- **JSON-RPC** for LMS-compatible operations (player control, library, playlist)
- **REST** for Resonance-specific features (settings, plugins, scanning)
- **SSE** for real-time plugin UI updates (with polling fallback)
- All API calls go through the singleton `api` instance from `api.ts`
- UI calls `playerStore` methods, never `api` directly (store handles optimistic updates and state sync)

### SDUI Widget Development

When adding a new SDUI widget:

1. Add the Python class to `resonance/ui/__init__.py` (with validation)
2. Add the type to `ALLOWED_TYPES`
3. Create the Svelte component in `web-ui/src/lib/plugin-ui/widgets/`
4. Register it in `registry.ts`
5. Document it in `docs/PLUGIN_API.md` §19
6. Add tests in `tests/test_plugin_ui.py`

See [`docs/PLUGIN_API.md`](../docs/PLUGIN_API.md) §19 for the complete
widget reference and [`CLAUDE.md`](../CLAUDE.md) §12 for document
relations that must be kept in sync.

---

## Further Reading

| Document                                                | Content                                         |
| ------------------------------------------------------- | ----------------------------------------------- |
| [Main README](../README.md)                             | Installation, features, project overview        |
| [`docs/PLUGIN_API.md`](../docs/PLUGIN_API.md)           | Plugin API reference including §19 SDUI widgets |
| [`docs/PLUGIN_TUTORIAL.md`](../docs/PLUGIN_TUTORIAL.md) | Step-by-step plugin tutorial                    |
| [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md)       | System architecture                             |

---

## License

GPL-2.0 — same as Resonance.
