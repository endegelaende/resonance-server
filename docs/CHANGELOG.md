# 📋 Resonance Changelog

All notable changes to the project are documented here.

---

## [Unreleased] — SDUI + Plugin Modernization Completed ✅

**Status:** 2853 passed, 2 skipped | SDUI Phase 1–3 feature-complete, Plugin-System Phasen A-E umgesetzt

### 🎨 Server-Driven UI (SDUI) — Phase 1–3 + UX Polish (June 2025)

Plugins can now build full web UI pages declaratively in Python — no JavaScript required.
The Resonance frontend renders plugin UIs generically via a recursive widget renderer.

- **20+ widget types** in `resonance/ui/__init__.py`:
  - Display: `Heading`, `Text`, `StatusBadge`, `KeyValue`, `Table`, `Button`, `Card`, `Row`, `Column`, `Alert`, `Progress`, `Markdown`
  - Layout: `Tabs` (with `Tab`), `Modal` (sizes: sm/md/lg/xl)
  - Form: `Form`, `TextInput`, `Textarea`, `NumberInput`, `Select`, `Toggle`

- **Conditional rendering** (`visible_when`):
  - `.when(field, value, operator)` on any widget
  - 8 operators: `eq`, `ne`, `gt`, `lt`, `gte`, `lte`, `in`, `not_in`
  - Evaluated in frontend against `formContext.getValues()`

- **SSE real-time updates**:
  - `GET /api/plugins/{id}/events` — Server-Sent Events endpoint
  - `PluginContext.notify_ui_update()` / `wait_for_ui_update()` / `ui_revision`
  - `dispatch_plugin_action()` auto-calls `notify_ui_update()`
  - Frontend: `EventSource` with automatic polling fallback after retries

- **Inline-editable table columns**:
  - `TableColumn(variant="editable")` + `Table(edit_action=..., row_key=...)`
  - Click-to-edit, Enter/blur commit, Escape cancel

- **Collapsible cards**: `Card(collapsible=True, collapsed=True)`

- **Form with dirty tracking**: `Form` exposes `getValues()`, `isDisabled`, dirty state

- **Modal dialogs**: 4 sizes (sm/md/lg/xl), close-on-escape, backdrop click

- **Frontend implementation** (`web-ui/src/lib/plugin-ui/`):
  - `registry.ts` — widget type → Svelte component mapping (20 widgets)
  - `PluginRenderer.svelte` — recursive renderer with `visible_when` evaluation
  - `PluginPageView.svelte` — page container with SSE + polling
  - `actions.svelte.ts` — generic action dispatcher
  - `widgets/` — 20 Svelte widget components

- **Test coverage**:
  - `test_plugin_ui.py`: **184 tests** (all widgets, operators, collapsible, inline-edit, SSE, modal)

### 🔒 Security Headers Middleware (June 2025)

- New `SecurityHeadersMiddleware` in `resonance/web/security.py`
- Sets `Content-Security-Policy`, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`
- Pragmatic CSP uses `'unsafe-inline'` due to SvelteKit inline bootstrap (future: SHA-256 hashes)
- **Test coverage**: `test_security.py`: **98 tests**

### 🔊 raopbridge — AirPlay Bridge Plugin on SDUI (June 2025)

First community plugin fully using the SDUI system. Located in
`resonance-community-plugins/plugins/raopbridge/`.

- **5-tab UI**: Status, Devices, Settings, Advanced, About
- **Device management**: Table with inline rename, enable/disable toggle, delete with confirmation
- **Per-device settings modal**: 3 sub-tabs (General, Audio, Behaviour) with Select/Toggle/NumberInput
- **Settings form**: Binary path, interface, server, auto_start, auto_save, logging, debug (conditional `visible_when`)
- **Advanced tab**: Collapsible card with all `common_options` as read-only KeyValue
- **About tab**: Markdown in collapsible card
- **Per-device advanced overrides**: ints, booleans, codecs list applied in `_handle_update_device()`
- **Issue #11 (Pinoatrome)**: All 10 hardcoded Svelte files from Pinoatrome's branch are fully replaced by generic SDUI rendering
- **Test coverage**: `test_raopbridge_ui.py`: **166 tests**

- Full suite after SDUI + Security + raopbridge: **2853 passed, 2 skipped**
- `svelte-check`: 0 errors | `npm run build`: ✅

### 🔌 Plugin System Modernization — Phases A-E (2026-02-19)

- **Plugin settings (declarative)**:
  - `plugin.toml` now supports `[settings.<key>]` definitions (`string/int/float/bool/select`)
  - Validation rules: `required`, `min/max`, `min_length/max_length`, `pattern`, `options`
  - Runtime API in `PluginContext`: `get_setting()`, `set_setting()`, `set_settings()`, `get_all_settings()`
  - Persistence: `data/plugins/<plugin>/settings.json` with `_version` and `_plugin_version`

- **Plugin states (enable/disable)**:
  - New persisted state file: `data/plugin_states.json`
  - State-aware startup: disabled plugins are discovered/loaded but not started
  - `restart_required` flag for state changes and install/uninstall operations

- **Dual-directory discovery + installer**:
  - `PluginManager` scans core (`plugins/`) and community (`data/installed_plugins/`) directories
  - New `PluginInstaller` supports ZIP install/uninstall with SHA256 verification
  - ZIP extraction hardening: zip-slip guard + manifest-based plugin name resolution

- **Repository client + install flow**:
  - New `PluginRepository` fetches repository index, caches results, merges multiple repositories
  - Compares repository versions against installed plugins (core/community aware)
  - Supports direct repository download + install flow

- **New JSON-RPC commands**:
  - `pluginsettings`: `get`, `set`, `getdef`
  - `pluginmanager`: `list`, `info`, `enable`, `disable`, `install`, `uninstall`, `repository`, `installrepo`

- **New REST endpoints** (`/api/plugins*`):
  - `GET /api/plugins`
  - `GET/PUT /api/plugins/{plugin}/settings`
  - `POST /api/plugins/{plugin}/enable`
  - `POST /api/plugins/{plugin}/disable`
  - `POST /api/plugins/install`
  - `POST /api/plugins/{plugin}/uninstall`
  - `GET /api/plugins/repository`
  - `POST /api/plugins/install-from-repo`

- **Web-UI plugin management**:
  - New `PluginsView.svelte` with tabs: Installed / Available / Settings
  - Sidebar navigation and UI store extended with `plugins` view
  - API client extended with plugin management and repository methods

- **Test coverage added**:
  - `test_plugin_settings.py`, `test_plugin_states.py`, `test_plugin_installer.py`
  - `test_plugin_repository.py`, `test_plugin_handlers.py`, `test_plugin_api.py`
  - Full suite at time of completion: **2071 passed, 2 skipped** (now 2853 with SDUI + Security)

### 🌐 Svelte Web-UI Major Update — Favorites, Radio, Podcasts, Playlists (2026-02-15)

- **FavoritesView** (`web-ui/src/lib/components/FavoritesView.svelte`, NEW — ~480 LOC):
  Full favorites management integrated via JSON-RPC `favorites` commands
  - Hierarchical folder navigation with breadcrumb trail and navigation stack
  - Play / Add to Queue for audio favorites
  - Inline rename with keyboard support (Enter/Escape)
  - Delete with confirmation overlay
  - Create new folders within the current location
  - Artwork/icon display for items with remote icons

- **RadioView** (`web-ui/src/lib/components/RadioView.svelte`, NEW — ~411 LOC):
  Internet radio browsing via JSON-RPC `radio` commands
  - Category drill-down navigation (Country → Genre → Stations)
  - Station play/add-to-queue with in-flight guards
  - Inline search with debounce (400ms) searching radio-browser.info
  - Station logos, LIVE badge, bitrate display
  - Breadcrumb navigation with back button

- **PodcastView** (`web-ui/src/lib/components/PodcastView.svelte`, NEW — ~611 LOC):
  Podcast browsing and management via JSON-RPC `podcast` commands
  - Browse subscribed feeds, recently played episodes
  - Episode play/add-to-queue with artwork
  - PodcastIndex search with debounce
  - Subscribe to new podcasts from search results
  - Unsubscribe with confirmation overlay
  - Feed drill-down showing episode list with subtitles (date, duration, resume position)

- **PlaylistsView** (`web-ui/src/lib/components/PlaylistsView.svelte`, NEW — ~673 LOC):
  Saved playlist management via JSON-RPC `playlists` and `playlist` commands
  - List all saved M3U playlists on disk
  - Track detail view with duration, artist, album
  - Load & Play: loads saved playlist into player queue and starts playback
  - Save Current Queue: saves current player queue as named M3U playlist
  - Inline rename with keyboard support
  - Delete with confirmation overlay
  - Stats bar showing track count and total duration

- **API Client Extended** (`web-ui/src/lib/api.ts`, +350 LOC):
  - 10 new TypeScript interfaces: `FavoriteItem`, `FavoritesResult`, `RadioItem`, `RadioResult`,
    `PodcastItem`, `PodcastResult`, `SavedPlaylist`, `SavedPlaylistTrack`
  - ~20 new API methods, all via JSON-RPC (no new REST endpoints needed):
    - Favorites: `getFavorites()`, `addFavorite()`, `deleteFavorite()`, `renameFavorite()`,
      `addFavoriteFolder()`, `favoriteExists()`, `playFavorites()`
    - Radio: `getRadioItems()`, `searchRadio()`, `playRadio()`
    - Podcasts: `getPodcastItems()`, `searchPodcasts()`, `playPodcast()`,
      `podcastSubscribe()`, `podcastUnsubscribe()`
    - Playlists: `getSavedPlaylists()`, `getSavedPlaylistTracks()`, `savePlaylist()`,
      `loadSavedPlaylist()`, `deleteSavedPlaylist()`, `renameSavedPlaylist()`

- **Sidebar Updated** (`web-ui/src/lib/components/Sidebar.svelte`):
  New "Sources" section with Favorites (Star icon), Radio (Radio icon), Podcasts (Podcast icon)

- **UI Store Extended** (`web-ui/src/lib/stores/ui.svelte.ts`):
  3 new view types: `favorites`, `radio`, `podcasts` (total: 10 views)

- **Main Page Wiring** (`web-ui/src/routes/+page.svelte`):
  View routing for all new components; "Playlists coming soon" placeholder replaced with functional PlaylistsView

- **Build:** `svelte-check` 0 errors, `npm run build` successful, backend tests passed (see Status at top)

### 🖥️ NiceGUI Web-UI — Python-Native Dashboard (2026-02-15, removed)

> This experiment was removed in favor of the Svelte 5 SPA Web-UI due to compatibility and maintenance overhead.

- **NiceGUI Web-UI** (`resonance/web/ui/`):
  Removed. The project uses the Svelte 5 SPA (`web-ui/`) as the supported web interface.

- **Integration:** Removed (`/ui/` route no longer exists).

- **Backend Bridge:** Removed.
  - `rpc()` shortcut: in-process JSON-RPC execution without HTTP round-trips

- **Theme** (`resonance/web/ui/theme.py`, NEW):
  - Catppuccin Mocha dark palette matching the Svelte UI look-and-feel
  - Custom CSS: Quasar dark overrides, scrollbar styling, track row hover/highlight, album card animations, playing-bar keyframes, cover art glow effect
  - Quasar brand colour mapping (primary/accent/positive/negative)
  - `format_duration()` utility (m:ss / h:mm:ss)

- **Dashboard Page** (`resonance/web/ui/pages/dashboard.py`, NEW):
  Single-page application at `/ui/` with four views, navigation via left drawer sidebar:

  - **Now Playing View**:
    - Cover art with glow effect (280×280)
    - Track title, artist, album
    - Progress slider with seek-on-drag (throttled 500ms)
    - Transport controls: Previous / Play-Pause / Next
    - Volume slider with mute toggle and numeric display
    - Play/pause icon updates dynamically based on playback mode

  - **Queue View**:
    - Track list with current-track highlighting (accent colour + equalizer icon)
    - Click track to jump (`playlist index`)
    - Remove button per track (`playlist delete`)
    - Clear queue button
    - Header shows track count + total duration

  - **Library Browse View**:
    - Three-level drill-down: Artists → Albums → Tracks
    - Breadcrumb with back-button navigation
    - Artist rows: name + album count + chevron
    - Album rows: cover art thumbnail, artist, year, track count, play button + chevron
    - Track rows: track number, title, artist, duration, add-to-queue button
    - "Play Album" button (uses `playlist loadtracks album_id:… sort:tracknum`)

  - **Search View**:
    - Debounced text input (400ms) with clearable field
    - Results grouped into Artists, Albums, Tracks sections
    - Click artist/album → drill into Library Browse
    - Click track → play immediately, plus-button → add to queue

  - **Real-time Updates**:
    - Status polling via `ui.timer(1.0)` — elapsed time, playback mode, volume, current track
    - Player list refresh via `ui.timer(5.0)` — auto-discovers new/disconnected players
    - Player selector dropdown with auto-select first player

- **Server Startup Changes** (`resonance/web/server.py`, CHANGED):
  - Health-check probe timeout increased from 5s to 20s (NiceGUI lifespan startup needs time)
  - Probe connection timeout 0.2s → 0.5s, read timeout 0.3s → 1.0s, interval 50ms → 250ms
  - Progress logging during startup wait (every 3s) + final timing report
  - Debug logging on probe failures for easier diagnosis

- **Dependency**: `nicegui>=2.0.0` added to `pyproject.toml` (installed: NiceGUI 3.7.1)

- **Coexistence**: Svelte UI remains at `/`, NiceGUI UI at `/ui/` — both served from the same port

**Changed Files:** `resonance/web/ui/__init__.py` (NEW), `resonance/web/ui/state.py` (NEW),
`resonance/web/ui/theme.py` (NEW), `resonance/web/ui/pages/__init__.py` (NEW),
`resonance/web/ui/pages/dashboard.py` (NEW), `resonance/web/server.py` (CHANGED),
`pyproject.toml` (CHANGED)

### 🎙️ Podcast Plugin (2026-02-15)

- **Podcast Plugin** (`plugins/podcast/`, NEW — 4 files, 178 tests):
  Second ContentProvider plugin — browse, search, subscribe, and stream podcast episodes with resume support

- **RSS Feed Parser** (`plugins/podcast/feed_parser.py`, NEW):
  - `PodcastFeed`: Frozen dataclass for parsed feed metadata (title, author, description, image, episodes)
  - `PodcastEpisode`: Frozen dataclass for episode data (title, url, duration, pub_date, description, image)
  - RSS 2.0 parsing with iTunes namespace support (`itunes:author`, `itunes:image`, `itunes:duration`, `itunes:summary`)
  - Duration parsing: HH:MM:SS, plain seconds, human-readable formats
  - pubDate parsing: RFC 2822, ISO 8601 formats
  - HTML stripping for descriptions
  - `fetch_feed(url)`: Async feed fetching via httpx

- **PodcastStore** (`plugins/podcast/store.py`, NEW):
  - JSON persistence with atomic writes
  - Subscription management: add/remove/update, URL-based index
  - Resume position tracking: LMS-compatible threshold logic (<15s = not started, >duration-15s = finished)
  - Recently played: LRU list (50 entries, deduplication)

- **PodcastProvider ContentProvider** (`plugins/podcast/__init__.py`, NEW):
  - Implements `ContentProvider` ABC — `browse()`, `search()`, `get_stream_info()`
  - Registered as `"podcast"` via `PluginContext.register_content_provider()`
  - `browse()` lists subscriptions, episodes, recently played
  - `search()` delegates to PodcastIndex API (`/search/byterm`)
  - `get_stream_info()` returns episode URL as `StreamInfo(is_live=False)`

- **PodcastIndex Search Integration**:
  - API integration with SHA-1 auth headers (API key + secret)
  - Feed search via `/search/byterm` endpoint

- **JSON-RPC Commands**:
  - `podcast items <start> <count>` — browse subscriptions/episodes (supports `url:`, `search:`, `menu:1` params)
  - `podcast search <start> <count>` — search PodcastIndex (supports `term:`, `query:` params)
  - `podcast play` — start episode playback (supports `url:`, `title:`, `cmd:play/add/insert`)
  - `podcast addshow` — subscribe to a podcast feed
  - `podcast delshow` — unsubscribe from a podcast feed
  - Full Jive `item_loop` format with play/add/go/more actions

- **Jive Menu Integration**:
  - Top-level "Podcasts" node under home menu (weight 50 — between Radio at 45 and Favorites at 55)
  - Search entry: `__TAGGEDINPUT__` with processing popup
  - Recently Played section
  - Subscriptions with unsubscribe context menu
  - Episode items with resume sub-menu ("Play from last position" / "Play from the beginning")
  - Context menu: "Add to Favorites" via `jivefavorites add`

- **Playback Integration**:
  - Creates `PlaylistTrack.from_url()` with `source="podcast"`, `is_live=False`
  - Play/add/insert modes
  - Recently-played tracking on stream start

- **Tests** (`tests/test_podcast_plugin.py`, NEW — 178 tests):
  - Feed parser: RSS parsing, iTunes namespace, duration/pubDate formats, HTML stripping, edge cases
  - PodcastStore: subscriptions CRUD, resume positions (threshold logic), recently played (LRU, dedup), persistence, atomic writes
  - PodcastProvider: browse root/subscriptions/episodes/recently-played, search, get_stream_info
  - Jive menu builders: episode items, subscription items, search items, resume sub-menu
  - Commands: items/search/play/addshow/delshow with all parameter variants
  - Plugin lifecycle: setup registers components, teardown clears state

**Changed Files:** `plugins/podcast/__init__.py` (NEW), `plugins/podcast/feed_parser.py` (NEW),
`plugins/podcast/store.py` (NEW), `plugins/podcast/plugin.toml` (NEW),
`tests/test_podcast_plugin.py` (NEW)

### 📻 Radio Plugin — Internet Radio via radio-browser.info (2026-02-14)

### 📻 Radio Quality Phase — Status Metadata + ICY Title Wiring (2026-02-15)

- LMS-first verified status metadata parity for remote/radio streams:
  - `remote: 1`, `remote_title` (station name), `live_edge: 0` for live streams
  - `trackType` derived from playlist track source (`radio`/`podcast`/`local`)
  - `current_title` priority: StreamingServer ICY → Slimproto META ICY → static track title
- ICY metadata end-to-end:
  - ICY StreamTitle parsing + storage in StreamingServer
  - Status responses expose `current_title` accordingly
- Frontend RPC error handling:
  - Treat `result.error` (plugin result-level error) as an error (not success)

### 🔁 Live Radio Self-Healing — Auto Re-Stream on Drop (2026-02-15)

- LMS `_RetryOrNext` equivalent for live remote streams:
  - Detect unexpected proxy EOF for live radio
  - Guarded retries with budget + reset window
  - Generation checks to avoid stale STMu/STMd races

### 🛰️ remoteMeta + ICY Push Notifications (2026-02-15)

- `remoteMeta` dict included in `status` for remote tracks (LMS `Queries.pm` `_songData()` parity):
  - Remote metadata fields: title/artist/album, duration/bitrate, artwork_url, remote/live flags
- LMS-style ICY parsing:
  - When ICY contains exactly one `" - "`, split into `icy_artist` + `icy_title` (mirrors LMS `HTTP.pm` `getMetadataFor()`)
- ICY push notifications:
  - Change-detected ICY updates fire `PlayerPlaylistEvent(action="newmetadata")`
  - Cometd subscribers re-execute status and receive updated metadata immediately
  - CLI mapping mirrors LMS `playlist newsong`

### 🎨 Radio Cover-Art Fixes for JiveLite/SqueezePlay (2026-02-15)

- **ICO→PNG auto-conversion** (`resonance/web/routes/artwork.py`):
  Many radio stations serve `.ico` favicons (Windows Icon format). JiveLite uses SDL_image
  which cannot render `.ico` files (`Surface:loadImageData()` returns `w=0, h=0` → image
  silently discarded). The imageproxy endpoint now detects non-standard formats (ICO, BMP,
  TIFF — anything not JPEG/PNG/GIF/WebP) and converts them to PNG via PIL before serving.
- **Server-relative icon paths** (`resonance/web/handlers/status.py`):
  All `icon` / `icon-id` fields for remote tracks now use server-relative `/imageproxy/…`
  paths via `_proxied_image_url()` (mirrors LMS `Slim::Web::ImageProxy::proxiedImage()`,
  ImageProxy.pm L437-457). Previously absolute URLs caused JiveLite to use ad-hoc
  `SocketHttp` fetches which were unreliable on embedded hardware.
- **Resize-suffix stripping route** (`resonance/web/routes/artwork.py`, NEW):
  JiveLite's `fetchArtwork()` (SlimServer.lua L1170-1172) appends `_300x300_m` to icon-id
  paths, e.g. `/html/images/radio_300x300_m.png`. New `/html/images/{filename}` route
  strips the suffix via `_RESIZE_SUFFIX_RE` and serves the original file.
- **Imageproxy upstream fallback** (`resonance/web/routes/artwork.py`):
  When the upstream image fetch fails (HTTP error, timeout, network error) the imageproxy
  now returns the radio placeholder image (`/html/images/radio.png`) instead of a 502,
  so SqueezePlay always gets *some* artwork.
- **Debug logging** for artwork pipeline:
  `[RADIO-PLAY]` log in radio plugin shows `icon` parameter at play time;
  `[STATUS-ART]` log in status handler traces `artwork_url` → `icon` path resolution.

| Problem | Root Cause | Fix |
|---|---|---|
| Cover sometimes yes, sometimes no | `.ico` favicons → SDL_image can't load ICO | Imageproxy converts non-standard formats to PNG via PIL |
| JiveLite unreliable artwork loading | Absolute URLs → ad-hoc `SocketHttp` | All `icon` paths server-relative (→ `artworkPool`) |
| Radio fallback icon 404 | JiveLite appends resize suffix to `/html/images/radio.png` | New route strips `_300x300_m` suffix |
| Imageproxy upstream errors | No fallback handling | Returns radio placeholder on 404/timeout |

### 🖥️ Bitmap Display Metadata Pipeline + Feature Flag (2026-02-15)

- Fix: Bitmap displays (SB2/SB3/Classic/Boom) now receive metadata updates:
  - `DisplayManager` subscribes to playlist events
  - Track changes (`action="index"/"load"`) load metadata from PlaylistManager and re-render
  - ICY changes (`action="newmetadata"`) read StreamTitle from StreamingServer and re-render
- Server wiring:
  - Display rendering is gated behind `RESONANCE_DISPLAY=1` (default off until hardware verified)

- **Radio Plugin** (`plugins/radio/`, NEW — 3 files, 114 tests):
  First ContentProvider plugin — browse, search, and stream live Internet Radio via radio-browser.info

- **radio-browser.info API Client** (`plugins/radio/radiobrowser.py`, NEW):
  - `RadioBrowserClient`: Async httpx-based client wrapping the radio-browser.info JSON API
  - `RadioStation`: Frozen dataclass for station data (name, url, url_resolved, favicon, country, tags, codec, bitrate, votes)
  - `_SimpleCache`: Bounded TTL cache for browse responses (256 entries, 10min TTL)
  - Browse endpoints: countries, tags, languages, codecs, top/trending stations
  - Search: full-text by station name, tag, country, language
  - Click counting: `count_click()` registers plays with the community API
  - Pre-resolved stream URLs (`url_resolved`) — no M3U/PLS resolution needed
  - Free community API, no API key or partner ID required

- **RadioProvider ContentProvider** (`plugins/radio/__init__.py`, NEW):
  - Implements `ContentProvider` ABC — `browse()`, `search()`, `get_stream_info()`, `on_stream_started/stopped()`
  - Registered as `"radio"` via `PluginContext.register_content_provider()`
  - `browse()` maps radio-browser.info categories/stations to `BrowseItem` (folder/audio/search types)
  - `search()` delegates to radio-browser.info search API, returns station results
  - `get_stream_info()` resolves station UUID → `StreamInfo` with `is_live=True`

- **JSON-RPC Commands**:
  - `radio items <start> <count>` — browse categories/stations (supports `url:`, `search:`, `menu:1` params)
  - `radio search <start> <count>` — search radio-browser.info (supports `term:`, `query:`, `search:` params)
  - `radio play` — resolve station and start playback (supports `id:`, `url:`, `title:`, `icon:`, `cmd:play/add/insert`)
  - Full Jive `item_loop` format with `play`/`add`/`go`/`more` (add-to-favorites) actions
  - CLI `loop` format for non-menu queries

- **Jive Menu Integration**:
  - Top-level "Radio" node under home menu (weight 45 — between "My Music" at 11 and "Favorites" at 55)
  - Browse navigation: folder items trigger `radio items` with category URL
  - Search entry: `__TAGGEDINPUT__` with processing popup
  - Audio items: play/add/go actions with station metadata
  - Context menu: "Add to Favorites" via `jivefavorites add`

- **Playback Integration**:
  - Creates `PlaylistTrack.from_url()` with `source="radio"`, `is_live=True`, resolved `stream_url`
  - Play mode: clears playlist, adds track, starts streaming via `_start_track_stream()`
  - Add mode: appends to current playlist
  - Insert mode: inserts after current track
  - Publishes `PlayerPlaylistEvent` for Cometd status push

- **Tests** (`tests/test_radio_plugin.py`, NEW — 114 tests):
  - API client (12 tests): browse countries/tags/top, search, get_station_by_uuid, count_click, caching, lifecycle
  - RadioStation dataclass (2 tests): defaults, all fields
  - SimpleCache (7 tests): put/get, TTL expiry, eviction, clear
  - RadioProvider (8 tests): name, icon, browse root, categories, search, get_stream_info (mp3/aac/failure)
  - Jive menu builders (8 tests): audio/folder/search items, missing image, missing station UUID, CLI format, window icon-id
  - Base actions (1 test): structure validation
  - Parameter parsing (10 tests): tagged colon/dict/mixed format, start/count defaults/explicit/clamped
  - Command dispatch (5 tests): default, items, search, unknown, not initialized
  - Radio items (6 tests): root menu, CLI mode, pagination, category param, search param, empty
  - Radio search (4 tests): term, query param, empty query, CLI mode
  - Radio play (8 tests): missing params, station UUID, add/insert mode, no player, resolve failure, direct URL, no playlist manager
  - Plugin lifecycle (2 tests): setup registers components, teardown clears state
  - Integration flow (2 tests): browse→play, search→play

### 🌐 Content Provider Phase 2 — Infrastructure (2026-02-14)

- **Content Provider Abstraction** (`resonance/content_provider.py`, NEW):
  - `StreamInfo`: Frozen dataclass — `url`, `content_type`, `title`, `artist`, `album`, `artwork_url`, `duration_ms`, `bitrate`, `is_live`, `extra`
  - `BrowseItem`: Frozen dataclass — `id`, `title`, `type` (audio/folder/search), `url`, `icon`, `subtitle`, `items` (nested children), `extra`
  - `ContentProvider`: Abstract base class with `name` (property), `icon`, `browse(path)`, `search(query)`, `get_stream_info(item_id)`, `on_stream_started()`, `on_stream_stopped()` lifecycle hooks
  - `ContentProviderRegistry`: Central registry — `register()`/`unregister()`, `get()`, `list_providers()`, `provider_ids`, delegating `browse()`/`search()`/`get_stream_info()` wrappers with error handling, `search_all()` cross-provider search
  - All methods are `async` — providers are expected to make network requests

- **PlaylistTrack Extended for Remote Streams** (`resonance/core/playlist.py`):
  - 9 new fields: `source` (local/radio/podcast/external), `stream_url`, `external_id`, `artwork_url`, `is_remote`, `content_type`, `bitrate`, `is_live`
  - `from_url()` class method — create a track from a remote URL with full metadata
  - `effective_stream_url` property — returns `stream_url` (if set) or `path`
  - Serialization: remote fields only persisted when non-default (backward-compatible compactness)
  - Deserialization: gracefully handles playlists from older Resonance versions (no remote fields)

- **StreamingServer URL Proxy** (`resonance/streaming/server.py`):
  - `RemoteStreamInfo`: Frozen dataclass — `url`, `content_type`, `is_live`, `title`
  - `ResolvedStream`: NamedTuple — `file_path` | `remote` (exactly one populated)
  - `queue_url(mac, url, content_type, is_live, title)` — queue a remote URL for proxy streaming
  - `resolve_stream(mac)` — unified resolution returning `ResolvedStream` (local or remote)
  - `is_remote_stream(mac)` — quick check if pending stream is a remote URL
  - Mutual exclusion: `queue_url()` clears local file queue and vice versa
  - Generation, cancellation token, seek/offset clearing — all consistent with `queue_file()`

- **Remote Proxy Streaming Route** (`resonance/web/routes/streaming.py`):
  - `_stream_remote_proxy()` — httpx-based async streaming proxy
  - `_icy_strip_relay()` — ICY/Shoutcast metadata stripping from interleaved byte streams
  - `_log_icy_metadata()` — parse and log ICY `StreamTitle` changes
  - Shared `httpx.AsyncClient` with configurable timeouts, follow-redirects, `Icy-MetaData: 1` header
  - Cancellation-token and disconnect checks every 4 chunks
  - Full error handling: HTTP status errors, request errors, timeouts
  - `stream_audio()` now uses `resolve_stream()` and branches to remote proxy when URL is queued

- **PluginContext Extended** (`resonance/plugin.py`):
  - `register_content_provider(provider_id, provider)` — register with auto-tracking
  - `unregister_content_provider(provider_id)` — manual removal
  - `_cleanup()` automatically unregisters all content providers on teardown
  - `__repr__` includes `content_providers=N` count

- **PluginManager Extended** (`resonance/plugin_manager.py`):
  - `start_all()` accepts optional `content_registry` parameter
  - Passes `_content_registry` to each `PluginContext`

- **Server Integration** (`resonance/server.py`):
  - `ContentProviderRegistry` created in `__init__()`, injected into plugin manager
  - `_on_decode_ready()`: branches on `is_remote` → `queue_url()`, skips server-side crossfade for remote tracks
  - `_on_track_finished()`: branches on `is_remote` → `queue_url()` for next track

- **Playback Handler** (`resonance/web/handlers/playlist_playback.py`):
  - `_start_track_stream()`: branches on `track.is_remote` → `queue_url()` with `effective_stream_url`, `content_type`, `is_live`, `title`

- **Dependency** (`pyproject.toml`):
  - `httpx>=0.27.0` promoted from dev to runtime dependency (required for URL proxy streaming)

- **Tests:** 88 new tests in `test_content_provider.py`:
  - `TestPlaylistTrackRemoteFields`: default values, `from_url()`, all fields, podcast source, title fallback (7 tests)
  - `TestPlaylistTrackEffectiveStreamUrl`: local path, remote without/with stream_url, local with stream_url (4 tests)
  - `TestPlaylistSerializationRemote`: local no remote fields, remote includes fields, roundtrip, legacy compat, compact serialization (5 tests)
  - `TestStreamingServerRemoteUrl`: queue/resolve/cancel/generation/clear/stop for remote URLs (13 tests)
  - `TestRemoteStreamInfo`: defaults, all fields, frozen immutability (3 tests)
  - `TestResolvedStream`: local, remote, empty (3 tests)
  - `TestContentProviderABC`: cannot instantiate, dummy provider properties, browse/search/get_stream_info, lifecycle hooks (10 tests)
  - `TestStreamInfo`: defaults, all fields, frozen (3 tests)
  - `TestBrowseItem`: defaults, folder/search types, nested items, frozen (5 tests)
  - `TestContentProviderRegistry`: register/unregister/list/browse/search/get_stream_info/search_all, error handling, unknown providers (17 tests)
  - `TestPluginContextContentProvider`: register/unregister, no registry raises, cleanup, repr, multi-plugin (8 tests)
  - `TestBackwardCompatibility`: from_path unchanged, queue_file unchanged, serialize compact, add_path, frozen, generation consistency (6 tests)

**Changed Files:** `resonance/content_provider.py` (NEW), `resonance/core/playlist.py`,
`resonance/streaming/server.py`, `resonance/web/routes/streaming.py`,
`resonance/plugin.py`, `resonance/plugin_manager.py`, `resonance/server.py`,
`resonance/web/handlers/playlist_playback.py`, `pyproject.toml`,
`tests/test_content_provider.py` (NEW)

### 🔌 Plugin System Phase 1 — MVP (2026-02-14)

- **Plugin API** (`resonance/plugin.py`, NEW):
  - `PluginManifest`: Parsed from `plugin.toml` (name, version, description, author)
  - `PluginContext`: DI container for plugins with controlled API surface
  - `register_command()`: Dynamically register JSON-RPC commands
  - `register_menu_node()` / `register_menu_item()`: Extend Jive menus
  - `register_route()`: Integrate FastAPI routers
  - `subscribe()`: Event bus with automatic cleanup
  - `ensure_data_dir()`: Per-plugin data directory
  - Full cleanup on teardown (commands, menus, events)

- **Plugin Manager** (`resonance/plugin_manager.py`, NEW):
  - 4-phase lifecycle: Discover → Load → Start → Stop
  - `discover()`: Scans `plugins/` for `plugin.toml` manifests
  - `load_all()`: Imports plugin modules, validates `setup()`/`teardown()`
  - `start_all()`: Calls `setup(ctx)`, creates PluginContext per plugin
  - `stop_all()`: Reverse-order teardown with automatic cleanup
  - Error isolation: A failing plugin does not block others
  - Partial registrations on setup failure are rolled back

- **Dynamic Command Registration** (`resonance/web/jsonrpc.py`):
  - `register_command(name, handler)` — adds commands at runtime
  - `unregister_command(name)` — removes them on plugin teardown
  - Protection against overwriting existing built-in commands

- **Dynamic Menu Registration** (`resonance/web/handlers/menu.py`):
  - `_build_main_menu()` automatically integrates plugin menus
  - Plugin nodes and items appear on Jive devices (Touch/Radio/Boom)

- **New Event Types** (`resonance/core/events.py`):
  - `ServerStartedEvent` (`server.started`) — Server fully initialized
  - `ServerStoppingEvent` (`server.stopping`) — Shutdown begins

- **Server Integration** (`resonance/server.py`):
  - PluginManager integrated into start/stop lifecycle
  - Plugins are started after all core components
  - Plugins are stopped before core components
  - `server.started` / `server.stopping` events at the correct points

- **Example Plugin** (`plugins/example/`):
  - `plugin.toml` manifest as template
  - `__init__.py` with `setup()`/`teardown()`, command, menu node, event handler
  - Demonstrates the complete plugin API

- **Tests:** 72 new tests in `test_plugin_system.py`:
  - PluginManifest parsing (valid, missing fields, frozen)
  - PluginContext commands (register, duplicate, unregister, cleanup)
  - PluginContext menus (nodes, items, multi-plugin isolation, cleanup)
  - PluginContext events (subscribe, receive, auto-unsubscribe)
  - PluginContext data directory (default, custom, ensure)
  - Dynamic command registration (jsonrpc register/unregister, builtin protection)
  - PluginManager discover (empty, nonexistent, valid, skips, sorted)
  - PluginManager load (valid, missing init, missing setup, syntax error, idempotent)
  - PluginManager start/stop (setup/teardown calls, command/menu/event wiring, 
    failing setup isolation, failing teardown isolation, reverse stop order, optional teardown)
  - PluginManager properties (manifest, context, count)
  - Example plugin integration (full lifecycle smoke test)
  - Edge cases (stop without start, double start, partial failure cleanup)

**Changed Files:** `resonance/plugin.py` (NEW), `resonance/plugin_manager.py` (NEW),
`resonance/server.py`, `resonance/web/jsonrpc.py`, `resonance/web/handlers/menu.py`,
`resonance/core/events.py`, `plugins/example/plugin.toml` (NEW),
`plugins/example/__init__.py` (NEW), `tests/test_plugin_system.py` (NEW)

### ⭐ Favorites Plugin — LMS-Compatible Favorites Management (2026-02-14)

- **First real plugin** built on the Plugin System Phase 1
- **JSON-RPC Commands** (`plugins/favorites/__init__.py`):
  - `favorites items` — Favorites list with pagination, search filter, sorting
  - `favorites add` — Add URL/title with duplicate detection
  - `favorites addlevel` — Create hierarchical folders
  - `favorites delete` / `favorites rename` / `favorites move` — CRUD operations
  - `favorites exists` — URL existence check (LMS-compatible)
  - `favorites playlist` — Play all favorites as playlist (play/load/add/insert)
  - `jivefavorites` — Confirmation menus for Jive devices (add/delete with feedback)
- **Persistence** (`plugins/favorites/store.py`):
  - `FavoritesStore` — JSON-backed with atomic write
  - Hierarchical folder structure, ID-based navigation
  - URL deduplication, search filter, configurable sorting
  - Storage location: `data/plugins/favorites/favorites.json`
- **Jive Menu Integration**:
  - "Favorites" in the main menu (weight 55, like LMS)
  - Browse navigation into subfolders
  - Preset buttons (1–6) for quick access
- **Tests:** 152 tests in `test_favorites_plugin.py`:
  - FavoritesStore (CRUD, folders, search, pagination, persistence, edge cases)
  - Command handlers (items, add, addlevel, delete, rename, move, exists, playlist)
  - Jive confirmation menus (jivefavorites add/delete)
  - Plugin lifecycle (setup/teardown, registrations, existing data)

**Changed Files:** `plugins/favorites/plugin.toml` (NEW),
`plugins/favorites/__init__.py` (NEW), `plugins/favorites/store.py` (NEW),
`tests/test_favorites_plugin.py` (NEW)

### 📖 Now Playing Tutorial Plugin — Companion Code for Plugin Tutorial (2026-02-14)

- **Tutorial plugin** as runnable companion code for `docs/PLUGINS_TUTORIAL.md`
- **JSON-RPC Commands** (`plugins/nowplaying/__init__.py`):
  - `nowplaying.stats` — Overall statistics (total_played, stored_entries)
  - `nowplaying.recent` — Recent tracks with CLI and Jive menu mode
- **Event Subscription**: Counts `player.track_started` events
- **Persistence** (`plugins/nowplaying/store.py`):
  - `PlayHistory` — JSON-backed with atomic write, configurable trimming
  - Total counter survives trimming and server restarts
  - Storage location: `data/plugins/nowplaying/history.json`
- **Jive Menu Integration**: "Play Stats" in the main menu (weight 80)
- **Tests:** 58 tests in `test_nowplaying_plugin.py`:
  - PlayHistory store (CRUD, trimming, persistence, corrupt JSON, ordering)
  - Command handlers (stats, recent — empty, with data, menu/CLI mode, limits)
  - Event handler (single, multiple, store=None, missing player_id, persistence)
  - Plugin lifecycle (setup/teardown, registrations, existing data)
  - Parse helper (string/dict/mixed params, special cases)
  - Integration (record→query, persistence workflow, full lifecycle)
- **Documentation**: `PLUGINS_TUTORIAL.md` updated to 58 tests / ~810 lines

**Changed Files:** `plugins/nowplaying/plugin.toml` (NEW),
`plugins/nowplaying/__init__.py` (NEW), `plugins/nowplaying/store.py` (NEW),
`tests/test_nowplaying_plugin.py` (NEW), `docs/PLUGINS_TUTORIAL.md`

### 🎵 DSD/DoP Support — DSF & DFF Format Support (2026-02-14)

- **Custom Binary Header Parser** (`resonance/core/dsd_parser.py`, NEW):
  - DSF parser: DSD chunk + fmt chunk + data chunk + optional ID3v2 at end of file
  - DFF/DSDIFF parser: FRM8 container, PROP/FS/CHNL sub-chunks, DIAR/DITI metadata
  - Extraction: duration, sample rate, channels, artwork detection, tags
  - Fallback for mutagen (does not natively support DSD)

- **Scanner Integration** (`resonance/core/scanner.py`):
  - `.dsf`, `.dff` added to `DEFAULT_AUDIO_EXTENSIONS`
  - `_extract_dsd_metadata()` fallback when mutagen returns `None`
  - Contributors, genres, compilation flag extracted from ID3v2 (DSF)

- **Streaming Policy** (`resonance/streaming/policy.py`):
  - DSF/DFF in `NATIVE_STREAM_FORMATS` (passthrough, LMS-conformant)
  - LMS reference: `convert.conf` has `dsf dsf * * → -` and `dff dff * * → -`

- **Transcode Rules** (`resonance/config/legacy.conf`):
  - Passthrough rules for DSD-capable players (Squeezelite)
  - ffmpeg-based fallback rules: DSF/DFF → FLAC, MP3, PCM

- **Wire Protocol** (`resonance/protocol/commands.py`):
  - `PCMSampleSize.DSD_DSF = 0` and `PCMSampleSize.DSD_DFF = 1` (raw bytes)
  - LMS reference: `$pcmsamplesize = $format eq 'dsf' ? 0 : 1`

- **Player Client** (`resonance/player/client.py`):
  - `dsf`/`dff` → `AudioFormat.DSD` mapping with correct DSD_DSF/DSD_DFF pcmsamplesize

- **Tests:** 51 new tests in `test_dsd_parser.py` + 18 new tests in `test_transcoder.py`
  (DSF/DFF parsing, tags, edge cases, policy, legacy.conf rule matching)

### 🖥️ Display Rendering SB2/3/Classic — Phases 3–4: Menu & Screensaver (2026-02-14)

- **Phase 3: Advanced Menu Rendering**
  - `render_menu_advanced()`: Multi-page menu with position indicator ("X/N" overlay on line 1),
    right arrow (`\x02`) for items with submenus, cursor marker — like LMS `Input::List` + `overlayRef`
  - `render_slider_bar()`: LMS `Graphics::sliderBar()` equivalent with progress font symbols (`\x03`–`\x09`),
    tight mode, end-lobe shaping, configurable width
  - `render_volume_overlay()`: Volume label (centered) + pixel bar for `showBriefly` overlays
  - `update_menu_advanced()` in DisplayManager: State management + scroll reset

- **Phase 4: Screensaver System**
  - `ScreensaverType` enum: `CLOCK`, `BLANK`, `NOW_PLAYING_MINI`, `NONE`
  - `render_clock()`: Digital clock (date line 1, time line 2, each centered) with optional
    alarm symbol overlay (`\x10` bell, `\x11` sleep) — identical to LMS `Slim/Plugin/DateTime/Plugin.pm`
  - `render_now_playing_mini()`: Simplified now-playing (title + artist centered, no progress/overlay)
  - Screensaver state in DisplayManager: `IDLE → SCREENSAVER` after configurable timeout
  - `set_screensaver()` API per player (type + timeout)
  - Clock screensaver refreshes 1×/s in the update loop
  - Wake from screensaver on user interaction (volume event, playback event)
  - `SCREENSAVER_FONT_OVERRIDES` lookup table per display model (like LMS `$fontDef`)

- **Changed Files:**
  - `resonance/display/__init__.py` — `ScreensaverType` enum, `SCREENSAVER_FONT_OVERRIDES`
  - `resonance/display/renderer.py` — 5 new methods (+230 LOC)
  - `resonance/display/manager.py` — Screensaver state, `_render_screensaver()`, `set_screensaver()`,
    `update_menu_advanced()`, IDLE→SCREENSAVER transition in update loop (+100 LOC)
  - `tests/test_display.py` — 64 new tests (96 → 160), 8 new test classes (+740 LOC)

### 🖥️ Display Rendering SB2/3/Classic — Phase 1 (2026-02-14)

- **New Package: `resonance/display/`** — Server-side bitmap rendering for Squeezebox graphics displays

- `resonance/display/__init__.py` (231 LOC)
  - `DisplayModel` enum: `GRAPHIC_320x32` (SB2/3/Transporter), `GRAPHIC_160x32` (Boom), `GRAPHIC_280x16` (SBG/SB1)
  - `DisplaySpec` dataclass: width, height, bytes_per_column, frame_command, screen2 support
  - Predefined specs: `DISPLAY_SB2`, `DISPLAY_TRANSPORTER`, `DISPLAY_BOOM`, `DISPLAY_SBG`, `DISPLAY_NONE`
  - `display_spec_for_model()`: Lookup by vfdmodel string or device name
  - `FontConfig` dataclass: Font selection per display model (standard/light/full etc.)

- `resonance/display/fonts.py` (599 LOC) — LMS BMP Font Parser
  - `FontCache`: Loads and caches all `.font.bmp` files from the LMS Graphics directory
  - `_parse_bmp()`: Monochrome 1-bpp BMP parser (normal + reversed palette)
  - `_parse_font()`: Pixel grid → column-major font table (256 characters, cp1252)
  - `render_string()` / `render_string_extended()`: Text → column-major bitmap (identical to `Fonts.pm::string()`)
  - Supports tight mode (`\x1d`/`\x1c`), font switching (`\x1b`), cursor (`\x0a`)
  - `measure_text()`: Text width in pixels
  - All 24 LMS fonts successfully validated (standard, light, full, narrow, threeline, etc.)

- `resonance/display/renderer.py` (540 LOC) — Screen Composition
  - `DisplayRenderer`: Stateless renderer for a display model
  - `render()`: `ScreenParts` (lines + overlays + center) → `RenderedScreen` with scroll detection
  - `render_now_playing()`: Track title, artist/album, elapsed time overlay, progress bar
  - `render_idle()`: Idle/screensaver screen (centered text)
  - `render_menu()`: 2-line menu view with cursor
  - `render_progress_bar()`: Horizontal progress bar (bottom 3 pixel rows)
  - `build_scroll_frame()`: Scroll animation frame generation

- `resonance/display/manager.py` (581 LOC) — Per-Player Display State
  - `DisplayManager`: Singleton, manages all player displays
  - `PlayerDisplay`: State per player (playback tracking, scroll state, showBriefly)
  - `register_player()` / `unregister_player()`: Automatic spec/font detection
  - `update_now_playing()` / `update_menu()` / `show_briefly()` / `set_power()`
  - Periodic update loop: 1s during playback (elapsed time), 10s idle
  - Event bus integration: reacts to playback/power/volume events
  - Frame duplicate suppression (saves bandwidth)

- `tests/test_display.py` (1744 LOC) — 160 Tests
  - BMP parsing (normal + reversed palette, error handling)
  - Font cache loading, rendering, extended rendering (tight/font-change)
  - Screen composition (lines, overlays, center, progress bar)
  - DisplaySpec/FontConfig dataclasses
  - DisplayManager lifecycle (register/unregister, start/stop, power, showBriefly)
  - Integration: Correct frame sizes for SB2 (1280B), Boom (640B), SBG (560B)

### 🏗️ Code Quality & Refactoring (2026-02-14)

- **Playlist Handler Split** (TODO 15.2 ✅)
  - `resonance/web/handlers/playlist.py` split from ~2,035 LOC monolith into 6 focused modules:
    - `playlist.py` (180 LOC) — Facade: dispatch table + re-exports
    - `playlist_helpers.py` (620 LOC) — Shared state, filesystem utils, parser, resolver
    - `playlist_playback.py` (344 LOC) — Play, pause, stop, index, jump, stream start
    - `playlist_mutation.py` (719 LOC) — Add, insert, delete, clear, move, shuffle, repeat
    - `playlist_query.py` (367 LOC) — Metadata queries, tracks list, event no-ops
    - `playlist_persistence.py` (352 LOC) — Save, load, preview, resume
  - Re-exports in facade preserve existing imports and test patches

- **Menu Handler Template Pattern** (TODO 15.3 ✅)
  - `resonance/web/handlers/menu_helpers.py` (new, 350 LOC) — Reusable Jive menu builders:
    - `menu_node()`, `menu_item()` — Generic item construction
    - `browse_go()`, `browse_menu_item()`, `browse_actions()` — Browselibrary navigation actions
    - `playlist_play()`, `playlist_add()` — Playlistcontrol action shortcuts
    - `slider_item()`, `choice_item()` — Settings items (bass/treble/repeat/shuffle)
    - `context_menu_item()` — Context menu items (add/play next/play this/play all)
    - `go_action()`, `do_action()`, `paginated()` — Low-level building blocks
  - `menu.py` reduced from 1,925 to 1,389 LOC (−28%) by using helpers
  - Simplifies future extensions (favorites, plugins)

### 🔒 Security & Hardening (2026-02-14)

- `resonance/web/security.py` (new, ~520 LOC)
  - **HTTP Basic Auth Middleware** (`AuthMiddleware`)
    - Optional (default: off) — no breaking change
    - Protects all HTTP endpoints except `/stream*` (players need unauthenticated access) and `/health`
    - Slimproto (port 3483) and Discovery (UDP) remain open (player compatibility)
    - Credentials via `Authorization: Basic …` header
  - **Rate Limiting Middleware** (`RateLimitMiddleware`)
    - Token bucket per client IP
    - Default: 100 requests/s (generous for normal usage)
    - Cometd long-poll `/cometd` and streaming `/stream*` exempted
    - HTTP 429 with `Retry-After` header on excess
    - Automatic cleanup of stale buckets (5 min), max 10,000 tracked IPs
  - **Password Hashing** (stdlib-only, no bcrypt)
    - `hash_password()` / `verify_password()` with `hashlib.pbkdf2_hmac` (SHA-256, 600k iterations)
    - Format: `pbkdf2:sha256:<iterations>$<hex-salt>$<hex-hash>` — self-describing
    - Plaintext fallback for development (with log warning)
  - **Input Validation Helpers**
    - `clamp_paging()`: Start ≥ 0, items ≤ 10,000
    - `is_valid_mac()`: MAC format validation (colon/dash-separated)
    - `is_safe_path()`: Path traversal detection (`..`, absolute paths, Windows drives)
    - `sanitise_player_id()`: Normalization to lowercase colons
    - `clamp_volume()`, `clamp_seek()`, `clamp_playlist_index()`
- `resonance/config/settings.py`
  - New fields: `auth_enabled`, `auth_username`, `auth_password_hash`,
    `rate_limit_enabled`, `rate_limit_per_second`
  - TOML section `[security]` for all security settings
  - Validation: auth_enabled requires username + password_hash; rate_limit 1–10,000
  - All 5 fields in `RESTART_REQUIRED` (change requires restart)
- `resonance/web/server.py`
  - `AuthMiddleware` and `RateLimitMiddleware` are automatically hooked in at `settings_loaded()`
  - Middleware order: Auth checks before rate limit (rejected auth requests don't count)
- `resonance/web/jsonrpc.py`
  - `execute_command()`: Player ID is validated against `is_valid_mac()`
  - Invalid player IDs (SQL injection, garbage) are rejected with a clear error message
- `resonance/web/jsonrpc_helpers.py`
  - `parse_start_items()`: Paging parameters are clamped to safe ranges
    (start: 0–1,000,000, items: 0–10,000)
- `resonance/web/handlers/playlist.py`
  - `_resolve_track()`: Path traversal protection — paths with `..` components are rejected
- `resonance/protocol/cli.py`
  - `CliServer`: New parameters `auth_enabled`, `auth_username`, `auth_password_hash`
  - `login <user> <pass>` command — when auth is enabled, clients must authenticate before
    other commands
  - Unauthenticated commands receive `error:not_authenticated login required`
  - Auth state is tracked per connection and cleaned up on disconnect
- `resonance/__main__.py`
  - `--hash-password` CLI flag: Interactive password hasher for `resonance.toml`
- `tests/test_security.py` (new, 85 tests)
  - Password hashing: Roundtrip, wrong password, plaintext fallback, malformed hash
  - Token bucket: Burst, refill, max cap
  - Auth middleware: Enabled/disabled, valid/invalid credentials, exempt paths, malformed header
  - Rate limiting: Disabled, 429 on excess, Retry-After header, exempt paths
  - Input validation: Paging clamp, MAC validation, path safety, volume/seek/index clamp
  - JSON-RPC: Invalid player ID rejection, SQL injection rejection, valid MAC accepted
  - Settings validation: Auth fields, rate limit range
  - CLI auth: Disabled allows all, enabled rejects without login, login+command, wrong password
  - Path traversal: `..` rejected in `_resolve_track`, normal paths accepted
- All 927 tests passed (842 existing + 85 new, no regressions)

### 🏗️ LibraryDb Query Builder Refactoring (2026-02-14)

- `resonance/core/db/query_builder.py` (new, ~463 LOC)
  - `TrackFilter`, `AlbumFilter`, `ArtistFilter` — Frozen dataclasses for composable filters
  - `build_tracks_query()` / `build_tracks_count_query()` — Dynamic SQL with WHERE + JOINs
  - `build_albums_query()` / `build_albums_count_query()` — Including DISTINCT for track JOINs
  - `build_artists_query()` / `build_artists_count_query()` — With album_count subquery
  - `album_row_to_dict()`, `artist_row_to_dict()` — Row converters for builder results
  - JOINs are only added when needed (genre_id → track_genres, role_id → contributor_tracks)
  - All dynamic values via parameter binding (?), no user input in SQL
  - ORDER BY still via whitelist in `ordering.py` (unchanged)
- `resonance/core/library_db.py`
  - New generic methods: `list_tracks_filtered()`, `count_tracks_filtered()`,
    `list_albums_filtered()`, `count_albums_filtered()`,
    `list_artists_filtered()`, `count_artists_filtered()`
  - All existing per-combination methods remain as compatibility wrappers
- `resonance/web/handlers/library.py`
  - `cmd_artists`: if/elif chain (5 branches) → one `ArtistFilter` + 2 calls
  - `cmd_albums`: if/elif chain (13 branches) → one `AlbumFilter` + 2 calls
  - `cmd_titles`: if/elif chain (15 branches) → one `TrackFilter` + 2 calls
  - Search path remains separate (FTS5/LIKE, not filter-based)
  - Handlers reduced from ~440 LOC to ~280 LOC
- All 842 tests pass (no regressions, all filter combination tests green)

### ⚙️ Settings System: TOML Config, REST API, Web UI (2026-02-14)

- `resonance/config/settings.py` (new, ~635 LOC)
  - `ServerSettings` dataclass with all configurable values
    - Network: `host`, `slimproto_port`, `web_port`, `cli_port`, `cors_origins`
    - Library: `music_folders`, `scan_on_startup`, `auto_rescan`
    - Playback defaults: `default_volume`, `default_repeat`, `default_transition_type`,
      `default_transition_duration`, `default_replay_gain_mode`
    - Paths: `data_dir`, `cache_dir`
    - Logging: `log_level`, `log_file`
  - TOML loading via `tomllib` (Python 3.11 built-in, no extra dependency)
  - Priority chain: CLI arguments > TOML file > built-in defaults
  - Config file discovery: `--config <path>` → `./resonance.toml` → `~/.resonance/config.toml`
  - TOML saving with atomic write (tmp + rename)
  - Clear separation: `RUNTIME_CHANGEABLE` vs `RESTART_REQUIRED` settings
  - Validation: Port ranges, volume 0–100, repeat modes, log levels, port uniqueness
  - Unknown TOML keys → warning (no crash)
  - Global singleton with `init_settings()` / `get_settings()`
  - `update_settings()` for partial runtime updates
  - `reset_settings()` resets to defaults (config path is preserved)
- `resonance/__main__.py`
  - New `--config <path>` CLI argument
  - CLI argument defaults changed to `None` (so TOML values are not overwritten)
  - `setup_logging()` now reads from `ServerSettings` (`log_level`, `log_file`)
  - `_build_cli_overrides()` builds override dict only from explicitly set args
- `resonance/web/routes/api.py`
  - `GET /api/settings` → all settings + TOML sections + changeability metadata
  - `PUT /api/settings` → partial update with validation, restart warnings, TOML persistence
  - `POST /api/settings/reset` → reset to defaults with automatic save
- `web-ui/src/lib/api.ts`
  - New interfaces: `ServerSettingsData`, `SettingsFieldMeta`, `SettingsResponse`, `SettingsUpdateResponse`
  - New methods: `getSettings()`, `updateSettings()`, `resetSettings()`
- `web-ui/src/lib/components/SettingsPanel.svelte` (completely new, ~668 LOC)
  - **Server Info** (read-only): Host, ports, CORS, paths, config file
  - **Music Folders**: Inline add/remove, scan button, scan status with progress bar,
    checkboxes for `scan_on_startup` and `auto_rescan`
  - **Playback Defaults**: Volume slider, repeat/transition/ReplayGain dropdowns,
    transition duration slider (only visible when transition > None)
  - **Logging**: Log level dropdown
  - **Actions**: Save button with dirty tracking, reset-to-defaults button
  - Toast feedback on save/reset/error, restart warnings on network changes
  - About section
- `tests/test_config.py` (new, 108 tests)
  - `TestServerSettingsDefaults` — all default values
  - `TestServerSettingsValidation` — port ranges, volume, repeat, transitions, log level
  - `TestServerSettingsSerialisation` — `to_dict()`, `to_toml_dict()`, None handling
  - `TestChangeability` — runtime vs restart-required classification
  - `TestTomlParsing` — known/unknown keys, sections, flattening
  - `TestCliOverrides` — port/host/verbose/CORS/None handling
  - `TestConfigFileDiscovery` — explicit/CWD/home path, fallback
  - `TestLoadSettings` — full priority chain, partial TOML, CLI override, corrupt TOML
  - `TestSaveSettings` — file creation, round-trip, atomic write, format
  - `TestGlobalSingleton` — init/get/replace lifecycle
  - `TestUpdateSettings` — runtime/restart/unknown/invalid updates
  - `TestResetSettings` — defaults, config path preservation
  - `TestGetSettingsApi` — GET endpoint, values, meta, sections
  - `TestPutSettingsApi` — PUT runtime/restart/invalid/persist/reflect
  - `TestResetSettingsApi` — POST reset, warnings, GET-after-reset
  - `TestEdgeCases` — CORS round-trip, TOML mapping coverage, port edge cases

### 🎮 IR Remote Control: Hold/Long-Press Detection + Extended Codes (2026-02-14)

- `resonance/protocol/slimproto.py`
  - `_dispatch_ir()` completely rewritten with per-player IR state tracking
  - Timing constants from LMS (`IR_REPEAT_WINDOW_MS = 300`, `IR_HOLD_THRESHOLD_MS = 900`)
  - 3-tier dispatch: First press → Repeat (volume ramping +2/-2) → Hold
  - Hold actions (LMS-verified via `Default.map`):
    - `volume_up/down` → `mixer volume +2/-2` (finer steps)
    - `playlist_next` → `time +10` (seek forward, `fwd.hold = song_scanner`)
    - `playlist_prev` → `time -10` (seek backward, `rew.hold = song_scanner`)
    - `pause` → `stop` (`pause.hold = stop`)
  - 32-bit timer wraparound correctly handled (`& 0xFFFFFFFF`)
  - `_IR_CODE_MAP` expanded from ~48 to ~100 codes (all 3 LMS profiles):
    - Numbers 0–9 → `playlist index <n>` (track jump)
    - Shuffle/repeat toggle → `playlist shuffle` / `playlist repeat`
    - Arrow keys, home, favorites, browse, presets 1–6 → log-only
      (`_IR_LOG_ONLY_ACTIONS`, no dispatch without menu/display system)
  - LMS reference: `Slim/Hardware/IR.pm`, `IR/Slim_Devices_Remote.ir`,
    `IR/jvc_dvd.ir`, `IR/Front_Panel.ir`, `IR/Default.map`
- `tests/test_slimproto.py`
  - +25 new tests: Parametrized code mapping (33 codes incl. numbers/shuffle/repeat),
    volume repeat (+2 steps), hold scenarios (fwd→seek, prev→seek, pause→stop,
    volume hold), log-only actions, timer wraparound, multi-player isolation,
    gap-after-repeat reset

### 🎵 Streaming & Formats: Opus Bugfix, WavPack/APE/MPC Support (2026-02-14)

- `resonance/streaming/policy.py`
  - `wv`, `ape`, `mpc` added to `ALWAYS_TRANSCODE_FORMATS`
- `resonance/config/legacy.conf`
  - **Bugfix Opus:** `opus mp3` rule added as first Opus rule (sox→wav→lame).
    Previously `opus flc` was the first rule → `strm` frame signaled MP3
    (`TRANSCODE_TARGET_FORMAT`), but the stream delivered FLAC. Mismatch fixed.
  - "UNTESTED" comment removed from Opus section
  - New transcode rules for WavPack (`.wv`), Monkey's Audio (`.ape`),
    Musepack (`.mpc`) — each with `mp3` (primary, ffmpeg→wav→lame) and
    `flc` (lossless fallback, ffmpeg→flac)
  - ffmpeg as universal decoder (LMS uses wvunpack/mac/mppdec, which we don't ship)
  - Seeking via `$START$` → `-ss` (build_command recognizes ffmpeg context)
- `resonance/streaming/server.py`
  - Content types for `.wv` (`audio/x-wavpack`), `.ape` (`audio/x-monkeys-audio`),
    `.mpc` (`audio/x-musepack`) added to `get_content_type()`
- `tests/test_transcoder.py`
  - +22 new tests: Policy tests for wv/ape/mpc (needs_transcoding, strm_hint,
    not-native), Opus rule matching (mp3/flc/pcm) + command building,
    WavPack/APE/MPC rule matching + command building (with/without seek),
    cross-format guard `test_all_transcode_formats_first_rule_matches_target`
  - LMS reference verified: `convert.conf` (ops/wvp/ape/mpc), `types.conf`,
    `Slim/Formats/{WavPack,APE,Musepack}.pm`

### 📚 Library & Scanner: Incremental Rescan, Orphan Detection, FTS5 (2026-02-14)

- `resonance/core/library.py`
  - `scan(force=False)`: New `force` parameter — without `force`, files with
    unchanged `mtime_ns` + `file_size` are skipped (mtime skip)
  - Orphan detection: After each scan root, DB tracks whose files no longer
    exist on disk are deleted; orphaned albums/artists/genres are then
    cleaned up via `cleanup_orphans()`
  - `ScanResult` extended with `deleted_tracks: int`
  - `rebuild_fts()` is called after force rescan or orphan deletion
- `resonance/core/library_db.py`
  - New method `get_track_mtime_index()` → `dict[str, (mtime_ns, file_size)]`
  - New method `delete_tracks_not_in_paths(valid_paths, scan_root)` — only deletes
    tracks under the respective scan root, other roots remain untouched
  - New method `rebuild_fts()` — Idempotent FTS5 index rebuild (no-op for schema < v9)
- `resonance/core/db/schema.py`
  - Schema version 8 → 9: FTS5 virtual table `tracks_fts` with `unicode61 remove_diacritics 2`
  - Automatic sync triggers (`tracks_fts_insert`, `tracks_fts_delete`, `tracks_fts_update`)
  - Initial `rebuild` during migration to populate the index from existing tracks
- `resonance/core/db/queries_tracks.py`
  - `search_tracks()` now uses FTS5 with relevance ranking (`ORDER BY fts.rank`)
    and prefix matching (`"token"*`); LIKE fallback for older schemas
  - `_has_fts5()` helper function for runtime detection of the virtual table
- `tests/test_core_library.py`
  - +18 new tests: `TestMtimeIndex` (3), `TestIncrementalRescan` (2),
    `TestOrphanDetection` (4), `TestFTS5Search` (9 — title/artist/album search,
    diacritic normalization, prefix matching, upsert/delete sync, rebuild idempotency)

### 🗄️ Persistence: Playlists, Alarms, and Player Prefs Survive Restart (2026-02-14)

- `resonance/core/playlist.py`
  - `PlaylistManager` gets `persistence_dir: Path | None` parameter
  - New methods `save_all()` / `load_all()` for JSON serialization per player
  - Dirty flag tracking in `Playlist._touch()` — only changed playlists are written
  - Background autosave task (`start_autosave()` / `stop_autosave()`, interval 30s)
  - `_serialize_playlist()` / `_deserialize_playlist()` with graceful handling of missing fields
  - Data format: `data/playlists/{mac}.json` (version 1)
- `resonance/web/handlers/alarm.py`
  - New functions `save_alarms()` / `load_alarms()` / `configure_persistence()`
  - Auto-save after every mutation in `cmd_alarm()` (add/delete/update/enableall/disableall/defaultvolume)
  - Data format: `data/alarms.json` (version 1, all players in one file)
- `resonance/web/handlers/compat.py`
  - New functions `save_player_prefs()` / `load_all_player_prefs()` / `configure_prefs_persistence()`
  - Auto-save after every `cmd_playerpref()` set call
  - Data format: `data/player_prefs/{mac}.json` (version 1)
- `resonance/server.py`
  - `ResonanceServer.__init__()`: Playlist persistence under `data/playlists/`
  - `ResonanceServer.start()`: `load_all()`, `start_autosave()`, `load_alarms()`, `load_all_player_prefs()`
  - `ResonanceServer.stop()`: `stop_autosave()` (flushes dirty playlists)
- `tests/test_playlist.py`
  - +22 new tests: Serialization roundtrip, dirty flag, manager persistence,
    alarm persistence, player prefs persistence, corrupt file handling

### ⚡ Quick Wins: WMA/Opus Policy, CORS, Sync API, ALAC Tests (2026-02-14)

- `resonance/streaming/policy.py`
  - `opus` explicitly in `ALWAYS_TRANSCODE_FORMATS` (no player can play Opus natively via HTTP)
  - `wma` explicitly in `NATIVE_STREAM_FORMATS` (SB2+ hardware decoder, legacy.conf handles exceptions)
  - Comments added explaining why each format is where it is
- `resonance/web/server.py`
  - CORS `allow_origins` configurable instead of hardcoded `["*"]`
  - `WebServer.__init__()` takes `cors_origins: str | list[str]` parameter
  - Comma-separated strings or lists are supported
- `resonance/__main__.py`
  - New CLI argument `--cors-origins` (default: `"*"`)
- `resonance/server.py`
  - `ResonanceServer.__init__()` takes `cors_origins` and passes it to `WebServer`
- `resonance/web/handlers/sync.py`
  - `cmd_sync()`: `logger.warning()` on sync creation (logical only, no clock/buffer sync)
  - `cmd_syncgroups()`: `"_note": "logical_only"` in response (debug-visible, LMS clients ignore it)
- `tests/test_transcoder.py`
  - +4 new tests: `test_alac_always_needs_transcoding`, `test_opus_always_needs_transcoding`,
    `test_wma_is_native_stream_format`, `test_wma_and_opus_deterministic`

### 🎨 Web UI Branding: Logo #160 ("R + Arcs Warm") in Sidebar (2026-02-09)

- `web-ui/src/lib/components/Sidebar.svelte`
  - Sidebar logo switched to variant `#160 — R + Arcs Warm (letter)`.
  - "Resonance" text removed from header (icon-only branding).

### 🧹 Release Cleanup: Logo Assets Reduced to Production Set (2026-02-09)

- `assets/logos/`
  - Draft/gallery files removed:
    - `logo-gallery.html`
    - `logo-gallery - Kopie.html`
  - Logo set consolidated to **one** file:
    - kept: `resonance-waves-light.svg`
    - removed: `resonance-waves.svg`, `resonance-wordmark.svg`, `resonance-vinyl.svg`, `resonance-vinyl-icon.svg`, `resonance-vinyl-wordmark.svg`
  - `resonance-waves-light.svg` updated to same style as Web UI sidebar logo (`R + Arcs Warm`).
- `.gitignore`
  - `assets/logos/logo-gallery*.html` excluded as scratch/draft pattern.

### 📄 README Completely Rewritten for GitHub (2026-02-09)

- Logo + centered header with badges (License, Python, Svelte, Tailwind, Status)
- Table of contents for quick navigation
- Quick Start section at the top (3 steps: clone, install, run)
- New "First Steps" section after server start (Add Folder, Connect Player, Play)
- Web UI description upgraded: "basic web interface" → full feature list
  incl. dynamic theming, BlurHash, infinite scroll, toast notifications, resizable panels
- Web UI project structure updated: all 14 components and 4 stores listed
- Feature tables with checkmarks instead of bullet points
- Transcoding details moved into `<details>` collapsible (less scroll noise)
- Three disclaimer blocks consolidated into one
- Contributing section with dev setup, code style hints, and PR guide
- Screenshot placeholders prepared (`docs/screenshots/`)
- Redundancies removed, overall length reduced with more content

**Changed Files:**
- `README.md` (completely rewritten)
- `docs/screenshots/` (directory created)

### 🚀 Web UI: Pagination, Extended Search & Toast System (2026-02-09)

**Pagination + Infinite Scroll:**
- Artists, albums, and tracks are now loaded in batches of 50 instead of hardcoded 100.
- IntersectionObserver-based infinite scroll automatically loads more when scrolling.
- Fallback "Load more" button with counter (`123 / 456`) if observer doesn't trigger.
- Separate loading states per category (`artistsLoading`, `albumsLoading`, `tracksLoading`).

**Extended Search (Artists + Albums + Tracks):**
- Search results now show three sections: Artists (horizontal scroll bar), Albums (with cover art) and Tracks.
- Click on artist/album from search results navigates directly to detail view.
- Previously only tracks were displayed, even though the API already returned all three categories.

**Toast Notification System:**
- New `toastStore` (`toast.svelte.ts`) with `success()`, `error()`, `info()`, `warning()` methods.
- New `ToastContainer.svelte` component: animated slide-in/out toasts, max 5 simultaneous, auto-dismiss.
- Type-specific icons (CheckCircle, XCircle, Info, AlertTriangle) and colors.
- `alert()` calls replaced by toasts (e.g. album deletion).
- Error feedback for all API calls (loading artists/albums/tracks, rescan, search).

**Changed Files:**
- `web-ui/src/lib/stores/toast.svelte.ts` (new)
- `web-ui/src/lib/components/ToastContainer.svelte` (new)
- `web-ui/src/routes/+layout.svelte` (ToastContainer integrated)
- `web-ui/src/routes/+page.svelte` (pagination, search UI, toast integration)

### 🐛 Fix: Album Art Restored on SqueezePlay (2026-02-09)

**Problem:** During playback, Radio/Touch/Boom displayed an empty image window instead of cover art.

**Fixes:**
- `resonance/web/routes/artwork.py`
  - `/music/{id}/cover` now uses proper fallback: first `album_id`, then `track_id`.
  - Resize fallback without Pillow (`PIL_AVAILABLE=False`) preserves the original `Content-Type`.
- New regression tests:
  - `tests/test_web_api.py::test_artwork_music_cover_no_ext_falls_back_to_track_lookup`
  - `tests/test_web_api.py::test_artwork_cover_with_spec_preserves_content_type_without_pillow`

### 🐛 Fix: Empty Popup on `displaystatus subscribe:showbriefly` Removed (2026-02-09)

**Problem:** Radio/Touch repeatedly showed an empty popup window; cover art was obscured or perceived as "missing".

**LMS Reference:** `displaystatus` does not deliver a synthetic display payload on subscription setup; real display data only comes from `displaynotify` events.

**Fixes:**
- `resonance/web/handlers/status.py`
  - `cmd_displaystatus()` now returns an empty result (`{}`) for subscription/polling instead of `display.text=["",""]`.
  - Prevents unsolicited, empty showbriefly popups on Jive/SqueezePlay.
- Tests:
  - `tests/test_displaystatus.py::test_displaystatus_showbriefly_is_silent_even_with_current_track`
  - `tests/test_displaystatus.py::test_displaystatus_without_subscribe_is_empty`

### 🧹 COMPARISON_LMS.md Updated After Code Review (2026-02-09)

**Goal:** Align feature comparison document with the actual implementation status.

**Changes:**
- **Gapless Playback**: ⚠️ → ✅ — Server-side feature-complete (STMd→Prefetch, generation-aware, repeat modes)
- **Crossfade**: ⚠️ → ✅ — Full engine (server-side SoX mixing + player-side strm signaling); HW-E2E Radio PASS, Touch/Boom open
- **ReplayGain**: ⚠️ → ✅ — Feature-complete (tag reading mode 0–3, clipping prevention, 16.16 fixed-point, LRU cache); HW-E2E Radio PASS, Touch/Boom open
- **ALAC**: Description clarified — pipeline functional for .m4a containers, dedicated E2E test open
- **WMA**: Risk specified — neither in ALWAYS_TRANSCODE nor NATIVE_STREAM, falls back to device_config fallback
- **Sync Groups**: Clarified — API complete (sync/unsync/syncgroups/syncsettings), clock/buffer sync missing
- **Display**: 🟡 → ✅ — SqueezePlay devices (Touch/Radio/Controller) render locally via JSON data that Resonance already delivers (menus, status, artwork URLs via Cometd/JSON-RPC); LMS uses `EmulatedSqueezebox2` with NO-OP drawFrameBuf for these devices; grfe/grfb/grfd primitives + display/displaystatus/displaynow commands complete; only text→bitmap for SB2/3/Classic/Transporter open (niche)
- **Artwork Resizing**: ❌ → ✅ — Was already implemented: `_resize_image()` with Pillow/LANCZOS, modes m/o/p, LMS-compatible endpoint `/music/{id}/cover_{WxH}_{mode}`
- **IR**: Clarified — 48 codes, 3 profiles, transport+volume+power; hold/long-press missing
- **Incremental Rescan**: More honest — DB stores mtime_ns, but no skip of unchanged files, no cleanup of deleted tracks
- **AAC/M4A/M4B**: Transcoding target corrected (faad→mp3, not faad→flac)
- **Server-side Crossfade Engine** added as Resonance-exclusive feature
- Test count updated to 534, trailing whitespace cleaned up
- Next priorities expanded to include incremental rescan
- Display render engine removed from priority list (only relevant for old SB2/3/Classic)

**Changed Files:** `docs/COMPARISON_LMS.md`, `docs/CHANGELOG.md`

### ✅ THIRD_PARTY_NOTICES.md Created (2026-02-09)

**Goal:** License documentation for all dependencies and shipped binaries.

**Implementation:**
- `THIRD_PARTY_NOTICES.md` (new): Documents licenses of all shipped binaries (faad, flac, lame, sox, squeezelite), pip runtime/optional/dev dependencies, npm production dependencies incl. license summary and GPL-2.0 compatibility statement
- `README.md`: Link to THIRD_PARTY_NOTICES.md added in license section

### ✅ README Merged and Installation Guide Extended (2026-02-09)

**Goal:** A single comprehensive README for GitHub with newbie-friendly installation guide.

**Implementation:**
- `README.md`: Completely rewritten — both READMEs (root + web-ui) merged; detailed installation guide for Windows (PowerShell, cmd) and Linux/macOS; git clone / ZIP download; venv explanation for beginners; server start in all shells; transcoding tools section with explanation of the LMS-patched faad binary (ralph-irving/faad2); troubleshooting section; complete project structure
- `web-ui/README.md`: Reduced to short version, references main README
- Test count updated to 534

### ✅ Distribution Switched to venv + pip (2026-02-09)

**Goal:** Distribution without Docker/Conda/Micromamba — only standard Python tooling.

**Implementation:**
- `pyproject.toml`: Runtime dependencies consolidated (mutagen, aiosqlite, fastapi, uvicorn); unused `aiofiles` dependency removed; `[web]`/`[library]` groups dissolved; blurhash/pillow as optional `[blurhash]` group; `httpx` added to `[dev]` (required for FastAPI TestClient)
- `scripts/setup.ps1` (new): Creates `.venv`, installs via `pip install .`, checks Python version and transcoding tools
- `scripts/dev.ps1`: Uses `.venv` instead of Micromamba
- `environment.yml`: Removed (replaced by `pyproject.toml` + venv)
- Docs updated: `README.md`, `web-ui/README.md` — all micromamba references replaced with venv commands

**Changed Files:** `pyproject.toml`, `scripts/setup.ps1`, `scripts/dev.ps1`, `README.md`, `web-ui/README.md`, `docs/CHANGELOG.md`, `environment.yml` (removed)

### ✅ Web UI Settings in Test Mode (2026-02-09)

**Goal:** Keep settings lean for now, rather than mirroring the Lyrion UI.

**Implementation:**
- `web-ui/src/lib/components/SettingsPanel.svelte`
  - Sections for playback runtime, sync/multiroom, and alarms removed from the UI.
  - Settings show only basic info for the active player in test mode.
- `docs/TODO.md`
  - New list for possible later reactivation:
    1. Playback Runtime
    2. Sync / Multiroom
    3. Alarms
- `README.md`
  - Doc link to `docs/TODO.md` added.

### 🧹 Documentation Simplification + Session Context (2026-02-09)

**Goal:** Significantly slim down document structure and create a single session context path.

**Implementation:**
- Redundant and obsolete documentation files removed and consolidated

### 🧹 Documentation Consolidation (`docs` + `docs/special`) (2026-02-09)

**Goal:** Merge thematically related documents and remove outdated analysis artifacts.

**Implementation:**
- `docs/special/` reduced to one consolidated core document:
  - kept/newly consolidated: `docs/special/CODE_OVERVIEW.md`
  - removed: `CODEMAP.md`, `FLOWS.md`, `FEATURES.md`, `DIFF_TO_LMS.md`, `GLOSSARY.md`, `KILLERFEATURES.md`
- Redundant gap analysis removed:
  - `docs/slim_vs_cadence.md`
- Obsolete session/raw analysis artifacts removed:
  - `docs/memory.txt`
  - `docs/slim_raw.txt`
  - `docs/slim_raw101.txt`
  - `docs/ws100.pcapng`
  - `docs/ws101.pcapng`
  - `docs/ws102.pcapng`
  - `docs/ws103.pcapng`
  - `docs/ws104_slim_raw.txt`
- References updated:
  - `docs/special/README.md`
  - `docs/KILLERFEATURES.md`
  - `README.md` (doc index extended with `docs/special/CODE_OVERVIEW.md`)

### 🧹 Documentation Cleaned Up (2026-02-08)

**Goal:** Reduce documentation overhead and remove outdated session/work files.

**Implementation:**
- `README.md` brought to current state (tests `464/464`, updated doc overview).
- Obsolete one-off documents removed:
  - `docs/CONTEXT_NULL_HANDOFF_2026-02-07.md`
  - `docs/fingdings.md`
  - `docs/tech_doofe.md`
- Navigation consolidated:
  - new: `docs/NAVIGATION_FOR_SESSION.md`
  - removed: `docs/LMS_NAVIGATION_FOR_SESSION.md`, `docs/RESONANCE_NAVIGATION_FOR_SESSION.md`
- Hardware smoke run after streaming registry fix added to matrix.


### ✅ Cleanup: HIGHLIGHT Debug and Capability Validation (2026-02-08)

**Goal:** Close pre-release cleanup items for `status` logging and capability-based `power/mixer` validation.

**Implementation:**
- `resonance/web/handlers/status.py`
  - Removed debug trace logging (`status elapsed trace`) incl. unused `logging`/`logger` boilerplate.
- `tests/test_web_api.py`
  - New regressions for capability-based command validation:
    - `test_jsonrpc_power_off_supported_device_executes_powerdown`
    - `test_jsonrpc_mixer_rejects_unsupported_tone_set` (parametrized for `bass` + `treble`)
    - `test_jsonrpc_mixer_allows_supported_tone_set_on_boom`
**Verification:**
- Focused: `tests/test_web_api.py -k "power_off or mixer_rejects_unsupported_stereoxl_set or mixer_rejects_unsupported_tone_set or mixer_allows_supported_tone_set_on_boom"` → 6 passed
- Full suite: `464/464` passed ✅

### ✅ IR Code Table Extended (2026-02-08)

**Goal:** Upgrade the existing MVP IR mapping to real LMS codes and secure it for real hardware events.

**Implementation:**
- `resonance/protocol/slimproto.py`
  - IR mapping extended to LMS reference:
    - `IR/Slim_Devices_Remote.ir`
    - `IR/jvc_dvd.ir`
    - `IR/Front_Panel.ir` (`.down` events)
  - Existing Boom `BUTN` codes kept and integrated into the same table.
  - Incorrect assignments from the old MVP mapping corrected (e.g. `power`, `volume`, `play/pause`, `fwd/rew`).
  - Central action→command table introduced (`_IR_ACTION_COMMANDS`) for consistent dispatch logic.
  - New actions: `play`, `power_on`, `power_off`, `mute_toggle`.

- `tests/test_slimproto.py`
  - New helpers: `build_ir_payload()`, `build_butn_payload()`.
  - New test block `TestIrDispatch`:
    - Parametrized code→command regressions (Slim Remote, JVC, Front Panel, Boom)
    - Repeat gate behavior (<300ms)
    - Parsing/dispatch for `IR` and `BUTN`

**Verification:**
- Focused: `tests/test_slimproto.py -k "IrDispatch or handle_ir or handle_butn"` → 23 passed
- Full suite: `487/487` passed ✅
### ✅ grfe/grfb Display Graphics Path (Legacy MVP) (2026-02-08)

**Goal:** Upgrade the previously unused display graphics path (`grfe`/`grfb`) from stub to real runtime path.

**Implementation:**
- `resonance/protocol/commands.py`
  - New builders: `build_display_brightness()`, `build_display_bitmap()`, extended `build_display_clear()` (default 1280 bytes).
- `resonance/protocol/slimproto.py`
  - New server methods: `set_display_brightness()`, `send_display_bitmap()`, `clear_display()`.
- `resonance/web/handlers/compat.py`
  - `display` command can now pass through low-level `grfb`/`grfe`:
    - `display grfb <code>`
    - `display grfe`
    - `display grfe clear <bytes?>`
    - `display grfe <hex_bitmap> <offset?> <param?> <transition?>`
  - `displaynow` delegates to the same path.
- Tests extended:
  - `tests/test_commands.py` (frame builders)
  - `tests/test_slimproto.py` (Slimproto send path)
  - `tests/test_web_api.py` (JSON-RPC passthrough)

**Verification:**
- Focused: `python -m pytest tests/test_commands.py tests/test_slimproto.py tests/test_web_api.py -q` → 203 passed
- Full suite: `python -m pytest -q` → 499/499 passed ✅
### ✅ grfd Legacy Framebuffer Path (2026-02-08)

**Goal:** Complete the still-open `grfd` path (SqueezeboxG framebuffer) analogous to LMS as MVP.

**Implementation:**
- `resonance/protocol/commands.py`
  - New builders: `build_display_framebuffer()`, `build_display_framebuffer_clear()`
  - LMS-like defaults: Offset `560` (`GRAPHICS_FRAMEBUF_LIVE`), clear size `560` bytes.
- `resonance/protocol/slimproto.py`
  - New server methods: `send_display_framebuffer()`, `clear_display_framebuffer()`.
- `resonance/web/handlers/compat.py`
  - `display` now also supports `grfd`:
    - `display grfd`
    - `display grfd clear <bytes?> <offset?>`
    - `display grfd <hex_bitmap> <offset?>`
- Tests extended:
  - `tests/test_commands.py` (`grfd` builders)
  - `tests/test_slimproto.py` (`grfd` send/clear)
  - `tests/test_web_api.py` (JSON-RPC passthrough `display grfd`)

**Verification:**
- Focused: `python -m pytest tests/test_commands.py tests/test_slimproto.py tests/test_web_api.py -k "display or grfd"` → 20 passed
- Full suite: `python -m pytest` → 507/507 passed ✅
### ✅ Playlist Subcommands Extended (2026-02-08)

**Goal:** Fully close LMS playlist parity with `Slim/Control/Request.pm`.

**Implementation:**
- `playlist` dispatch extended with aliases/additions: `append`, `load`, `insertlist`, `playlistsinfo`, `preview`, `zap`, `playtracks`, `addtracks`, `inserttracks`, `deletetracks`, `loadalbum`, `playalbum`, `addalbum`, `insertalbum`, `deletealbum`, `deleteitem`, `pause`, `stop`, `save`, `resume`.
- Final query/event subcommands added: `album`, `artist`, `duration`, `genre`, `modified`, `name`, `path`, `remote`, `title`, `url`, `load_done`, `newsong`, `open`, `sync`, `cant_open`.
- Parity verified against LMS dispatch: `47/47` playlist subcommands covered.
- Unified filter resolution for track selection (incl. `year`, combined ID filter, and legacy text fallbacks for `album`/`artist`/`title`).
- `playlist save/resume` as MVP with in-memory snapshots per player (`noplay` + `wipePlaylist` supported).
- `playlist loadtracks` switched to the new shared filter logic.
- `playlist deletetracks` implemented: Queue items are resolved per filter and removed from the current playlist; `deletealbum` maps to the same path.
- `playlist deleteitem` implemented (index or path in the current queue).
- `playlist pause`/`playlist stop` as LMS-compatible aliases to the existing playback commands.
- New handlers: `playlist load` (single item), `playlist insertlist` (filter/list fallback), `playlist playlistsinfo` (queue metadata), `playlist preview` (save/load + restore on `cmd:stop`), and `playlist zap` (remove current queue track).

**Tests:**
- New JSON-RPC regressions in `tests/test_web_api.py`:
  - `test_jsonrpc_playlist_loadalbum_alias_loads_album_tracks`
  - `test_jsonrpc_playlist_addalbum_and_insertalbum_aliases`
  - `test_jsonrpc_playlist_save_resume_snapshot_and_wipe`
  - `test_jsonrpc_playlist_deletetracks_removes_filtered_tracks`
  - `test_jsonrpc_playlist_deletealbum_alias_with_numeric_id`
  - `test_jsonrpc_playlist_deleteitem_removes_track_by_path`
  - `test_jsonrpc_playlist_pause_alias_uses_pause_semantics`
  - `test_jsonrpc_playlist_stop_alias_stops_player`
  - `test_jsonrpc_playlist_load_loads_single_track_by_path`
  - `test_jsonrpc_playlist_insertlist_inserts_after_current`
  - `test_jsonrpc_playlist_playlistsinfo_returns_current_queue_metadata`
  - `test_jsonrpc_playlist_preview_save_and_restore`
  - `test_jsonrpc_playlist_zap_removes_current_track`
  - `test_jsonrpc_playlist_query_subcommands_return_track_fields`
  - `test_jsonrpc_playlist_remote_query_detects_stream_url`
  - `test_jsonrpc_playlist_event_style_subcommands_are_noop_compatible`
- Full suite still green: `460/460`.

### ✅ Alarm Runtime (Alarm Scheduler)

**Goal:** Not just configure alarms (alarm/alarms), but actually trigger them at the correct local time.

**Problem:** There were already LMS-compatible alarm CRUD/query handlers (`alarm`, `alarms`), but no runtime that fires alarms at the appropriate time.

**Solution (Minimal Implementation, LMS Subset):**
- Background scheduler reads alarm definitions from `resonance.web.handlers.alarm` (in-memory) and calculates the next due time (local time).
- Interpretation: `AlarmEntry.time` = seconds since local midnight; `dow` = LMS semantics (0=Sun..6=Sat).
- When due:
  1. Sets volume via `["mixer","volume",X]`
  2. Starts playback via `["play"]` (queue/playlist start when STOPPED)
- Duplicate guard: per (player_id, alarm_id) the "last fired" date is cached → no double-fire on the same day.
- One-shot (`repeat=0`) is disabled after firing.

**Note:** `url`/`shufflemode` are not yet evaluated (CURRENT_PLAYLIST behavior is the default).

**Changed Files:**
- `resonance/core/alarm_runtime.py` — New AlarmRuntime (scheduler, DOW mapping, due calculation, fire/disable logic)
- `resonance/server.py` — Start/stop wiring of AlarmRuntime (uses JSON-RPC command path)
- `tests/test_alarm_runtime.py` — 6 unit tests (due, fire order volume→play, one-shot disable, no double fire, grace window, empty dow/disabled)

### ✅ Elapsed Time Push (STMt Events)

**Goal:** Running time display on Squeezebox Radio/Touch/Boom — periodic elapsed time updates via Cometd push.

**Problem:** STMt heartbeats (every ~1s from the player) previously did NOT publish a `PlayerStatusEvent`. Only STMp/STMr/STMs triggered pushes. During normal playback there were therefore no periodic status updates to JiveLite/Web UI/Cadence.

**Solution:**
- **Throttled STMt Push** — When the player is in PLAYING state and STMt is received, a `PlayerStatusEvent` is published every `ELAPSED_PUSH_INTERVAL_SECONDS` (5s)
- **Throttle Reset on State Changes** — STMp/STMr/STMs reset the throttle timer so the next STMt doesn't fire too soon after a state change
- **Cleanup on Disconnect** — `_last_elapsed_push` is cleaned up on BYE! and connection close, so reconnecting players immediately get a fresh push

**Flow:** STMt → throttle check → `PlayerStatusEvent` → `CometdManager.handle_event()` → `_reexecute_slim_subscriptions()` → JiveLite gets fresh `time`/`rate`/`duration` value → `getTrackElapsed()` interpolation is recalibrated.

**Changed Files:**
- `resonance/protocol/slimproto.py` — `ELAPSED_PUSH_INTERVAL_SECONDS` constant, `_last_elapsed_push` dict, throttled STMt logic in `_handle_stat`, cleanup in `_handle_bye` and `_handle_connection`
- `tests/test_slimproto.py` — 6 new tests in `TestElapsedTimePush` (publishes when playing, throttled within interval, fires after interval expires, no publish when not playing, state change resets throttle, disconnect clears throttle)
### ✅ LMS-Style Debouncing for Subscription Re-Execution

**Goal:** Prevent fast event bursts (e.g. Stop→Play, Load→STMs, multiple STMt) from flooding expensive subscription re-executions.

**Problem:** Every `PlayerStatusEvent` and `PlayerPlaylistEvent` immediately called `_reexecute_slim_subscriptions()` — the full status query for all subscribers. During fast bursts (e.g. loading an album = clear + load + STMs) there were 3 full re-executions in milliseconds, even though only the last one is relevant.

**LMS Reference:** `Request.pm` `notify()` uses `killOneTimer` + `setTimer` (classic debounce). The delay values come from `statusQuery_filter()` in `Queries.pm`:
- `return 1.3` → 0.3s delay (default — burst absorption)
- `return 2.0` → 1.0s delay (playlist stop — often followed by play)
- `return 2.5` → 1.5s delay (playlist jump/open — newsong follows)

**Solution:**
- **3 delay constants** — `REEXEC_DEBOUNCE_DEFAULT=0.3`, `REEXEC_DEBOUNCE_STOP=1.0`, `REEXEC_DEBOUNCE_JUMP=1.5`
- **`_get_reexec_delay(event)`** — Classifies events: stopped→1.0s, index/load→1.5s, everything else→0.3s
- **`_schedule_debounced_reexec(player_id, delay)`** — Classic debounce with `asyncio.Task`: cancel pending task + schedule new one. Shorter delay "wins" on follow-up events
- **Legacy channel immediate** — Web UI gets raw events immediately (cheap dict push), only the expensive Slim subscription re-execution is debounced
- **Cleanup in `stop()`** — All pending debounce tasks are cancelled

**Flow:** Event → `_get_reexec_delay()` → cancel pending task → `asyncio.sleep(delay)` → `_reexecute_slim_subscriptions()`. New event before expiry → timer reset (debounce).

**Changed Files:**
- `resonance/web/cometd.py` — 3 delay constants, `_reexec_debounce_tasks` dict, `_get_reexec_delay()`, `_schedule_debounced_reexec()`, `handle_event()` refactored, cleanup in `stop()`
- `tests/test_cometd.py` — 14 new tests in `TestReexecDebounce` (delay classification ×7, scheduling ×3, coalescing, independent players, shorter-delay-wins, stop cleanup, handle_event integration, legacy channel immediate)
### ✅ Quick Win Session: Slimproto Handlers, IR Dispatch, Transcoding Consistency (2026-02-08)

**Goal:** Process 9 low-hanging fruits from the gap analysis in one pass.

**Implementation (9 Fixes):**

1. **Genre count in `serverstatus`** — `count_genres()` instead of hardcoded `0` (`resonance/web/handlers/status.py`)
2. **Format map completed** — `aac`, `wma`, `alc`/`alac`, `dsd` correctly signaled in the `strm` frame (`resonance/player/client.py`)
3. **ANIC handler** — TODO removed, no-op is correct (no display engine) (`resonance/protocol/slimproto.py`)
4. **RESP handler** — HTTP response headers logged + stored on `client.last_resp_headers` (`resonance/protocol/slimproto.py`)
5. **META handler** — ICY `StreamTitle` extracted + stored on `client.icy_title` (`resonance/protocol/slimproto.py`)
6. **SETD handler** — Player name (ID=0) and disabled flag (ID=4) processed (`resonance/protocol/slimproto.py`, `resonance/player/client.py`)
7. **BUTN handler** — Hardware buttons → same dispatch path as IR (`resonance/protocol/slimproto.py`)
8. **IR dispatch** — MVP mapping (play/pause/vol±5/skip/power) with 300ms repeat gate + `jsonrpc_handler` wired on SlimprotoServer (`resonance/protocol/slimproto.py`, `resonance/server.py`)
9. **Transcoding consistency** — `devices.toml` + `config/__init__.py` default aligned to `"mp3"`; streaming route now resolves `device_type` via `player_registry` (was previously `None`) (`resonance/config/devices.toml`, `resonance/config/__init__.py`, `resonance/web/routes/streaming.py`, `resonance/web/server.py`)

**Changed Files:**
- `resonance/web/handlers/status.py` (genre count)
- `resonance/player/client.py` (format map, `name` setter, `last_resp_headers`/`icy_title` attributes)
- `resonance/protocol/slimproto.py` (ANIC, RESP, META, BUTN, SETD, IR dispatch, `_IR_CODE_MAP`, `_dispatch_ir()`)
- `resonance/server.py` (`jsonrpc_handler` wiring)
- `resonance/config/devices.toml` (transcode_target: `"flac"` → `"mp3"`)
- `resonance/config/__init__.py` (default aligned)
- `resonance/web/routes/streaming.py` (`player_registry` + `device_type` resolution)
- `resonance/web/server.py` (`player_registry` passed to streaming route)

**Verification:**
- Full run: **440/440 tests passed** ✅ (not a single break)

### ✅ T0-TIME-STUCK Completed (2026-02-07/08)

**Problem:** With `transitionType=0`, `status.time` stayed at `0.0`, and on controller-class players
no track change was detected (`index_transitions=0`).

**Implementation:**
- `status` fallback to stream age when `raw_elapsed=0` persists
- DSCO handling (`reason=0`) implemented
- Server-side track-end detection in STMt handler (generation + duration + margin)
- Track duration wired in streaming server

**Verification:**
- 8 new tests in `tests/test_slimproto.py`
- Full run: **440/440 tests passed** ✅
- Hardware long-window (`transitionType=0`): PASS (`index_transitions=1`)
  - `artifacts/hardware-e2e/20260207-235837-Radio_t0_long.md`

### ✅ Telnet CLI (Port 9090) Implemented (2026-02-07)

**Problem:** LMS CLI on port 9090 was marked as an open compatibility gap.

**Implementation:**
- New module: `resonance/protocol/cli.py`
  - TCP line-based CLI server (Telnet)
  - Parser for LMS-typical inputs (`<playerid> <command...>` and implicit `-`)
  - Forwarding to the existing JSON-RPC command dispatcher (`execute_command`)
- Server lifecycle wired:
  - `resonance/server.py` starts/stops CLI with the main server
  - `cli_port` configurable (0 = disabled)
- CLI argument extended:
  - `resonance/__main__.py` with `--cli-port` (default `9090`)
- Protocol exports updated:
  - `resonance/protocol/__init__.py` exports `CliServer`

**Tests:**
- New test module `tests/test_cli.py` (7 tests)
- Full run: **431/431 tests passed** ✅ (`python -m pytest -v` via micromamba)
### ✅ Hardware E2E Matrix Tooling (2026-02-07)

**Goal:** Reproducible hardware validation for Radio/Touch/Boom instead of ad-hoc individual tests.

**New:**
- `scripts/hardware_e2e_matrix.ps1`
  - Sets runtime prefs (`transitionType`, `transitionDuration`, `transitionSmart`, `replayGainMode`, `noRestartDecoder`)
  - Optional queue setup via `track_id`
  - Polls `status` and detects `time` backsteps within the same track
  - Writes artifacts to `artifacts/hardware-e2e/*.md` and `artifacts/hardware-e2e/*.json`
- `docs/HARDWARE_E2E_MATRIX.md`
  - Central matrix + run history + pass/fail criteria
- `docs/E2E_TEST_GUIDE.md`
  - Test 10 switched to PowerShell-first flow

**First Run (Baseline):**
- `controller_quick` with `TrackIds 154,155,156`
- Result: `backsteps_same_track=0`, `mode_not_play_samples=0`, `index_transitions=1`
- Artifact: `artifacts/hardware-e2e/20260207-215349-controller_quick.md`
### ✅ Status Time Monotonic Guard Against Backsteps (2026-02-07)

**Problem:** In hardware E2E, `status.time` occasionally showed a brief backstep within
the same track during crossfade, even though audio flow and playlist index were stable.
Root cause was a stale elapsed sample (`elapsed_milliseconds`) from an old stream generation.

**Implementation:**
- **Streaming Generation Age** (`resonance/streaming/server.py`)
  - `_stream_generation_started_at` per player
  - `get_stream_generation_age(player_mac)` for runtime plausibility checks
- **Elapsed Origin at Stream Start** (`resonance/player/client.py`)
  - `elapsed_origin_monotonic` in `PlayerStatus`
  - Elapsed/sticky elapsed is cleanly re-initialized at `start_stream()`
- **Status Guarding** (`resonance/web/handlers/status.py`)
  - Plausibility check: `elapsed_milliseconds` only used when consistent with `elapsed_seconds`
  - Hard clamping via stream origin and stream generation age
  - Monotonic cache per player/track/generation: no backstep when `mode=play`

**Tests:**
- `tests/test_streaming.py`
  - `test_get_stream_generation_age_tracks_queue_time`
  - `test_get_stream_generation_age_returns_none_for_unknown_player`
- `tests/test_web_api.py`
  - `test_jsonrpc_status_clamps_stale_elapsed_to_stream_age`
  - `test_jsonrpc_status_uses_elapsed_seconds_when_ms_is_implausible`
  - `test_jsonrpc_status_monotonic_for_same_track_and_generation`
- Full run: **422/422 tests passed** ✅ (`python -m pytest -v` via micromamba)

### ✅ Real Crossfade Engine in Streaming Server (2026-02-07)

**Problem:** Previously crossfade was only active as runtime/prefetch logic (`strm` transition + STMd prefetch), but without actual server-side overlap mixing.

**Implementation:**
- **New Module:** `resonance/streaming/crossfade.py`
  - Pre-computation of a concrete crossfade plan (duration probe, clamp, splice/trim parameters)
  - SoX command building for server-side mix output
- **StreamingServer Extended** (`resonance/streaming/server.py`)
  - Pending crossfade plans per player/generation
  - `queue_file_with_crossfade_plan()`, `get_crossfade_plan()`, `pop_crossfade_plan()`
  - Invalidation on manual queue/seek/offset paths
- **Runtime Integration** (`resonance/server.py`)
  - `_on_decode_ready()` plans a real server mix for crossfade types (1/5)
  - When server mix is active, player-side transition fields are set to `0/0` (no double-fade)
  - `start_track(... format_hint_override=...)` for correct `strm` format signaling
- **Streaming Route Integration** (`resonance/web/routes/streaming.py`)
  - `/stream.mp3` prioritizes pending crossfade plan and streams SoX mix directly
  - Disconnect/cancel handling analogous to existing streaming generators

**Tests:**
- New tests in `tests/test_crossfade.py` and `tests/test_streaming.py`
- Full run: **414/414 tests passed** ✅
### ✅ Crossfade Engine: Prefetch-Based Track Transitions (2026-02-07)

**Problem:** Resonance loaded the next track only at STMu (output buffer empty), causing
1-3 seconds of silence between tracks. Crossfade and gapless were impossible.

**LMS Analysis:** The server does not mix audio. The player firmware handles crossfade/mixing.
The server only needs to prepare the next track in time (prefetch at STMd) and send the
correct transition parameters in the `strm` frame.

**Implementation — Two-Phase Track Transition Model:**
- **Phase 1 (STMd → Prefetch):** Player decoder has consumed input → server prepares
  next track (peek_next, resolve_runtime_stream_params, queue_file, strm s)
- **Phase 2 (STMu → Advance):** Output buffer empty → server only advances playlist index
  (no second strm, as stream is already running)

**Changes:**
- `resonance/core/playlist.py`: `peek_next()` — reads next track without index change
- `resonance/core/events.py`: `PlayerDecodeReadyEvent` for STMd
- `resonance/protocol/slimproto.py`: STMd publishes event instead of `return`
- `resonance/streaming/server.py`: Short-track clamping (LMS: `dur < transition*2 → dur/3`)
- `resonance/server.py`: `_on_decode_ready()` prefetch logic, `_on_track_finished()` fast path,
  prefetch invalidation on manual actions (skip/seek/play)

**Safety Mechanisms:**
- Generation guard: Stale STMd from previous streams is ignored
- Double-prefetch guard: Only one prefetch per stream generation
- Manual-action guard: `suppress_track_finished_for_player()` clears prefetch state

**Tests:** 14 new tests in `tests/test_crossfade.py` (409 total) ✅

### ✅ Runtime Logic: Gapless/Crossfade/ReplayGain Active (2026-02-07)

**Problem:** The `strm` header fields for transition/gapless/ReplayGain existed,
but were not consistently wired in the runtime path (`playerpref` → track start/seek/auto-advance).

**Implementation:**
- **Runtime Resolver** (`resonance/streaming/runtime.py`, `resonance/streaming/server.py`)
  - Player pref mapping/normalization (`transitionType`, `transitionDuration`, `transitionSmart`, `replayGainMode`, `remoteReplayGain`, `gapless` + alias `noRestartDecoder`)
  - Smart transition decision (album adjacency), gapless flag (`FLAG_NO_RESTART_DECODER`)
  - ReplayGain tag evaluation (ID3/MP4/Vorbis/FLAC keys) + 16.16 fixed-point calculation
- **Start Paths Wired**
  - `resonance/web/handlers/playlist.py`: Manual track start uses runtime params
  - `resonance/server.py`: Auto-advance (`_on_track_finished`) uses runtime params
  - `resonance/web/handlers/seeking.py`: Seek starts without transition, but with gapless/ReplayGain
  - `resonance/player/client.py`: `start_stream`/`start_track` accept and send `transition_duration`, `transition_type`, `flags`, `replay_gain`
- **playerpref Runtime Effect** (`resonance/web/handlers/compat.py`)
  - Runtime-relevant keys write immediately to the streaming runtime
  - Query uses defaults for runtime keys when no value has been set yet

**Tests:**
- New/extended tests in `tests/test_commands.py`, `tests/test_streaming.py`, `tests/test_web_api.py`
- Full run: **395/395 tests passed** ✅ (`python -m pytest -v` via micromamba)
### ✅ Device Capability System + Device-Specific Volume Curves (2026-02-07)

**Problem:** Resonance treated all Squeezebox devices identically — same volume curve
(-50 dB, Squeezebox2), same menus. Boom and SqueezePlay devices (Radio, Touch) need
a different volume curve (-74 dB, two-ramp), and device menus should only show features
that the respective device actually supports.

**LMS Analysis:** LMS has separate Perl modules per device type (Boom.pm, SqueezePlay.pm, etc.)
with overridden `getVolumeParameters()` and capability methods (`hasLineIn()`,
`hasBalance()`, `maxBass()` etc.). The menus are dynamically built in `Jive.pm` based
on these capabilities.

**Solution (Data-Driven Instead of Class Hierarchy):**

1. **`resonance/player/capabilities.py`** (NEW)
   - `VolumeParameters` dataclass: `total_volume_range_db`, `step_point`, `step_fraction`
   - `DeviceCapabilities` dataclass: Volume curve + hardware flags (has_line_in, has_balance, etc.)
   - Predefined curves: `SB2_VOLUME` (-50 dB) and `BOOM_VOLUME` (-74 dB, two-ramp)
   - Dict mapping for all 12 device types

2. **Volume Curve Parameterized** (`protocol/commands.py`)
   - `_volume_to_db()` now takes optional curve parameters
   - `build_volume_frame()` has new parameter `volume_params`
   - Default remains SB2 curve (no breaking changes)

3. **PlayerClient Extended** (`player/client.py`)
   - Property `device_capabilities` → automatic lookup by device type
   - `set_volume()` automatically uses the correct curve

4. **Capability-Based Menus** (`web/handlers/menu.py`)
   - Audio settings (bass, treble, StereoXL, balance, fixed volume, line out) only if device supports it
   - "Turn Player Off" only if `can_power_off=True` (Boom has no power off)

5. **Cleanup:** `slave_streams` → `sync_streams` renamed

**Volume Curves Comparison:**

| Device | Range | Step Point | Effect at Volume 10 |
|--------|-------|------------|---------------------|
| SB2 | -50 dB | -1 | -44.6 dB (Gain 388) |
| Boom/Radio | -74 dB | 25 | -59.2 dB (Gain 72) |

**Tests:** 8 new tests (366 total), all passed ✅

### ✅ VERS Version Fix for Touch UI Devices (2026-02-07) 🎉

**Problem:** Squeezebox Touch UI devices (Boom, Radio, Touch) made NO HTTP/Cometd
connection to port 9000, even though discovery and Slimproto were working.

**Root Cause:** SqueezePlay firmware 7.7.3 and older has a **version comparison bug**
that erroneously rejects servers with version >= 8.0.0. Resonance was sending "9.0.0".

**Deep Research Findings (`Research_gold.md`):**
- HTTP/Cometd is triggered by **Discovery TLV parsing**, independently of Slimproto
- Critical TLVs: NAME, JSON (port as ASCII!), UUID (36 characters), VERS (must be 7.x!)
- LMS works around the bug with `getFakeVersion()` → "7.9.1"

**Fix:**
- `resonance/server.py`: Discovery VERS TLV → "7.9.1"
- `resonance/protocol/slimproto.py`: Slimproto vers → "7.9.1"
- `resonance/protocol/discovery.py`: Default version → "7.9.1"
- `resonance/web/handlers/status.py`: serverstatus version → "7.9.1"

**Status:** All 356 tests passed ✅ — **Live test with hardware still pending!**

### ✅ Branding Polish & Cleanup (2026-02-06)

**Typography:**
- **Orbitron Font** for brand name (Sci-Fi/Synthwave style)
- Web UI: Self-hosted in `/static/fonts/` (GDPR-compliant, no Google requests)
- Cadence: Via `google_fonts` package (cached locally)

**Web UI:**
- Favicon added (vinyl logo as SVG)
- "Resonance" text in sidebar smaller (text-base instead of text-lg)

**Cadence:**
- Logo smaller (44px → 32px), spacing to text reduced
- Windows title bar: "cadence" → "Cadence"
- ~160 unused JiveLite assets removed (hdskin, toolbar, nowplaying PNGs)

### ✅ Play-from-STOP Fix + Web UI UX (2026-02-06)

**Problem:** When the player was stopped and tracks were in the queue, the `play`
command did not reliably start — track was briefly played, then aborted.

**Root Cause:** `cmd_play()` in the server only did `await player.play()` (resume),
but did **not start a stream** from the playlist in STOP state.

**Fix (LMS-like):**
- `play` when STOP + non-empty queue → `playlist.play(current_index)` + `_start_track_stream()`
- Fallback when PLAYING/PAUSED → `player.play()` (resume as before)

**Additional Changes:**
- Regression test added (356 tests total)
- Web UI: Album action bar with **Play / Shuffle / Add to Queue** buttons
- Web UI: **+** button on tracks adds individual track to queue
- Web UI: Workaround in `playerStore.play()` removed (server is now correct)

### ✅ Web UI Improvements: Cadence-Style Smoothing (2026-02-06)

**Problem:** Progress bar in the Web UI was less smooth than in Cadence (Flutter)

**Solution:** Cadence-style elapsed time interpolation ported:

1. **Slew-Rate Limiting**
   - Forward: max 0.025s per frame (1.5x speed)
   - Backward: max 0.012s per frame (only on server correction)
   - Prevents jitter and abrupt jumps

2. **Monotonic Clamp**
   - Prevents small backward movements (<0.1s)
   - Ensures smooth forward motion

3. **Track Change Detection**
   - Detects track changes and large jumps (>1.5s)
   - Hard reset of smoothing state on detection

4. **pendingSeek Flag**
   - Prevents polling during seek operations
   - No "jumping back" after seek

**Additional Fixes:**
- TypeScript types for `playlist_loop` extended (`coverArt`, `artwork_url`)
- `svelte.config.js`: `handleHttpError` for missing favicon
- Build successful, all 355 tests passed

### ✅ Fixed: Rapid Seeking Blocking (2026-02-05)

**Problem:** Rapid seeking led to app hangs (timeouts after multiple fast seeks)

**Root Causes & Fixes:**

1. **Stream Lock Removed (LMS Style)**
   - `streaming.py` used an `asyncio.Lock` per player
   - LMS does it differently: Closes old stream immediately, opens new one — NO lock!
   - Fix: Lock removed, streams run briefly in parallel, old one aborts via `cancel_token`

2. **Pipeline Cleanup Made Synchronous**
   - `_cleanup_popen_pipeline_sync()` instead of async version
   - No `await`, no `create_task` in finally block
   - Direct `close()` and `kill()` — does not block on CancelledError

3. **SeekCoordinator Deadlock Fixed**
   - Lock acquisition with 500ms timeout
   - Old tasks are no longer awaited when cancelling
   - Coalesce delay reduced from 50ms to 20ms

4. **Slider: Seek Only on Release** (Cadence)
   - New `_SeekSlider` widget with `onChangeEnd` instead of `onChanged`
   - During dragging: only local display update
   - On release: single seek request (instead of 100+ during drag)

5. **stderr Reading on Cancellation Removed**
   - `_log_popen_stderr` is no longer called on CancelledError
   - Prevents blocking on still-running processes

**Additional Fixes This Session:**
- `playlist index` → `playlist jump` for Next/Previous (LMS-conformant)
- `playAlbum`: Redundant `index 0` + `play` commands removed (loadtracks does auto-start)
- "Playing:" SnackBar messages removed
- Play icon overlay on album cards removed

### 🎵 Cadence Desktop App (Flutter)

Full desktop app as controller for Resonance:

- Server connection with auto-connect
- Player selection dropdown
- Library browser (Artists → Albums → Tracks) with breadcrumb navigation
- Now Playing bar with seek slider
- Queue view with drag & drop
- Playback controls (Play/Pause/Next/Previous/Volume)
- LMS-conformant pause/resume semantics (`pause 1` / `pause 0`)
- Catppuccin Mocha theme
- Debug logging for seek operations (`[SEEK]`, `[API-SEEK]`)

### 🔧 Server Core

- **Slimproto Server** — Full implementation (port 3483)
- **HTTP Streaming** — Range requests, transcoding (port 9000)
- **JSON-RPC API** — LMS-compatible for iPeng, Squeezer, Orange Squeeze
- **Cometd/Bayeux** — Long-polling for real-time updates
- **Music Library** — Scanner, SQLite, search, genres, contributors
- **Playlist/Queue** — Add, remove, shuffle, repeat (off/one/all)

### 🔊 Streaming & Transcoding

- **Formats:** MP3, FLAC, OGG, WAV (direct) + M4A, M4B, AAC (via faad→flac/mp3)
- **SeekCoordinator** — Latest-wins semantics, 50ms coalescing
- **Policy System** — Centralized transcoding decisions
- **Range Requests** — Full seeking
- **Debug Logging** — `[STREAM-LOCK]`, `[TRANSCODE]` tags for diagnostics

### 🎨 Web UI (Svelte 5)

- Svelte 5 with Runes ($state, $derived)
- Tailwind CSS v4
- Cover art with BlurHash placeholders
- Adaptive accent colors (node-vibrant)
- Resizable sidebar & queue panels
- Now Playing with progress bar, volume slider

### 🐛 Important Fixes

- **LMS-Conformant STM Event Handling** — STMu = track end, STMf = no state change
- **Elapsed Calculation** — `elapsed = start_offset + raw_elapsed` (like LMS)
- **Non-Blocking Seek** — JSON-RPC responds immediately, seek runs in background
- **BlurHash Cache-Only** — Status endpoint no longer blocks

---

## [0.1.0] — First Working Version

### Milestones

1. **Slimproto Connection** — Squeezelite connects and stays stable
2. **Audio Streaming** — First playback via HTTP
3. **Transcoding** — M4B/M4A works via faad
4. **Web UI** — Modern Svelte 5 frontend
5. **Cometd** — Real-time updates for apps
6. **Cadence** — Flutter desktop app started

---

## Version Scheme

We follow [Semantic Versioning](https://semver.org/):

- **MAJOR:** Incompatible API changes
- **MINOR:** New features, backward-compatible
- **PATCH:** Bugfixes

---

## 📚 Further Documentation

→ [INDEX.md](./INDEX.md) — Complete overview of all documents with target audiences

---

*Last updated: February 2026*
