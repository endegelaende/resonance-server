# 🎵 Resonance — Architecture

A Python reimplementation of the Logitech Media Server (LMS/SlimServer).

---

## 📋 Overview

**Resonance** is a server that controls Squeezebox players and software players (Squeezelite).

```
┌─────────────┐      ┌─────────────┐      ┌─────────────┐
│  Web-UI /   │ ◄──► │  Resonance  │ ◄──► │ Squeezelite │ ──► 🔊
│  Mobile App │ HTTP │   Server    │Slim- │  (Player)   │
│             │      │  (Python)   │proto │             │
└─────────────┘      └─────────────┘      └─────────────┘
```

**Key principle:** The server gives commands, players are "dumb" and execute them.

---

## 🏗️ System Architecture

### The UI – Mediator – Server Model

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│       UI        │     │    MEDIATOR     │     │     SERVER      │
│ (Presentation)  │◀───▶│  (API/Adapter)  │◀───▶│ (Business Logic)│
└─────────────────┘     └─────────────────┘     └─────────────────┘
```

### Mediator Overview

| Mediator | UI | Protocol |
|----------|-----|----------|
| **Squeezelite** | Speaker | Slimproto + HTTP Audio |
| **Web Layer** | Browser | HTTP + JSON-RPC |
| **Mobile Apps** | Smartphone | JSON-RPC + Cometd |

---

## 📂 Project Structure

```
resonance-server/
├── resonance/                    # Main package (server code)
│   ├── __init__.py
│   ├── __main__.py               # Entry: python -m resonance
│   ├── server.py                 # Main server, starts all components
│   ├── content_provider.py       # ContentProvider ABC, StreamInfo,
│   │                             #   BrowseItem, ContentProviderRegistry
│   ├── plugin.py                 # PluginContext (DI for plugins)
│   ├── plugin_manager.py         # Plugin discovery, loading, lifecycle
│   │
│   ├── config/                   # Configuration
│   │   ├── devices.toml          # Device tiers (Modern/Legacy)
│   │   └── legacy.conf           # Transcoding rules (LMS-style)
│   │
│   ├── core/                     # Business logic
│   │   ├── library.py            # MusicLibrary facade
│   │   ├── library_db.py         # SQLite + aiosqlite
│   │   ├── scanner.py            # Audio file scanner (mutagen)
│   │   ├── playlist.py           # Playlist & PlaylistManager
│   │   ├── artwork.py            # Cover art + BlurHash
│   │   ├── events.py             # Event bus (pub/sub)
│   │   └── db/                   # DB schema & queries
│   │       ├── models.py         # Dataclasses (Track, Album, Artist)
│   │       ├── schema.py         # SQLite schema with migrations
│   │       ├── queries_*.py      # Query modules
│   │       └── ordering.py       # Sort logic
│   │
│   ├── display/                  # Bitmap display rendering (SB2/3/Classic/Boom)
│   │   ├── fonts.py              # BDF font loader
│   │   ├── renderer.py           # Frame renderer (column-major bitmaps)
│   │   └── manager.py            # DisplayManager (event-driven updates)
│   │
│   ├── player/                   # Player management
│   │   ├── client.py             # PlayerClient (status, commands)
│   │   ├── capabilities.py       # Device capabilities + volume curves
│   │   └── registry.py           # PlayerRegistry (all players)
│   │
│   ├── protocol/                 # Slimproto + CLI + Discovery
│   │   ├── slimproto.py          # SlimprotoServer (Port 3483)
│   │   ├── cli.py                # Telnet CLI Server (Port 9090)
│   │   ├── discovery.py          # UDP discovery
│   │   └── commands.py           # strm, audg, aude builders
│   │
│   ├── streaming/                # Audio streaming
│   │   ├── server.py             # StreamingServer, queue_file/url, resolve_stream
│   │   ├── transcoder.py         # Transcoding pipeline (faad, flac, lame)
│   │   ├── crossfade.py          # Server-side crossfade (SoX)
│   │   ├── seek_coordinator.py   # Latest-wins seek coordination
│   │   └── policy.py             # Transcoding decisions
│   │
│   └── web/                      # HTTP/API layer
│       ├── server.py             # FastAPI app (Port 9000)
│       ├── jsonrpc.py            # JSON-RPC handler (/jsonrpc.js)
│       ├── jsonrpc_helpers.py    # Parameter parsing
│       ├── cometd.py             # Bayeux long-polling
│       ├── security.py           # Auth + rate limiting middleware
│       ├── handlers/             # Command handlers
│       │   ├── status.py         # Player status
│       │   ├── seeking.py        # Seek commands (non-blocking)
│       │   ├── playback.py       # Play/Pause/Stop
│       │   ├── playlist.py       # Queue commands
│       │   ├── menu.py           # Jive menu system
│       │   └── library.py        # Library queries
│       └── routes/               # FastAPI routes
│           ├── api.py            # REST endpoints
│           ├── streaming.py      # /stream.mp3 (local + remote URL proxy)
│           ├── artwork.py        # Cover art endpoints
│           └── cometd.py         # /cometd
│
├── plugins/                      # Plugins (auto-discovered)
│   ├── example/                  # Template/demo plugin
│   ├── favorites/                # Favorites (LMS-compatible)
│   ├── nowplaying/               # Now Playing tutorial plugin
│   ├── radio/                    # Internet Radio — radio-browser.info
│   └── podcast/                  # Podcasts — RSS/PodcastIndex
│
├── tests/                        # pytest suite
├── web-ui/                       # Svelte 5 frontend
│   └── src/
│       ├── lib/
│       │   ├── api.ts            # TypeScript JSON-RPC client
│       │   ├── stores/           # Svelte 5 runes stores
│       │   └── components/       # UI components
│       └── routes/               # SvelteKit pages
└── docs/                         # Documentation
```

---

## 📡 Protocols & Ports

| Port | Protocol | Purpose |
|------|----------|---------|
| **3483** | Slimproto (TCP) | Binary player control |
| **9000** | HTTP | Streaming + JSON-RPC + Web UI |
| **9090** | Telnet CLI (TCP) | LMS CLI commands (text-based) |

### Slimproto (Port 3483)

Binary TCP protocol between server and player.

**Message format:**
```
┌──────────────┬──────────────┬─────────────────┐
│ Command      │ Length       │ Payload         │
│ (4 Bytes)    │ (4 Bytes)    │ (Length Bytes)  │
└──────────────┴──────────────┴─────────────────┘
```

**Key messages:**

| Tag | Direction | Description |
|-----|-----------|-------------|
| `HELO` | Client→Server | Handshake, device info |
| `STAT` | Client→Server | Heartbeat, status |
| `strm` | Server→Client | Stream control (start/pause/stop) |
| `audg` | Server→Client | Volume |

**STM event codes (in STAT):**

| Code | Meaning | Action |
|------|---------|--------|
| `STMs` | Track started | → PLAYING |
| `STMp` | Paused | → PAUSED |
| `STMr` | Resumed | → PLAYING |
| `STMf` | Flushed | → **No state change!** |
| `STMu` | Underrun | → STOPPED + track finished |

### HTTP (Port 9000)

| Endpoint | Purpose |
|----------|---------|
| `POST /jsonrpc.js` | JSON-RPC API (LMS-compatible) |
| `GET /stream.mp3` | Audio streaming |
| `POST /cometd` | Real-time updates (long-polling) |
| `GET /api/*` | REST API |
| `GET /api/artwork/*` | Cover art |

---

## 🎵 Audio Pipeline

### Streaming Flow (Local Files)

```
1. Client sends "playlist play /path/to/song.mp3"
2. Server queues track in StreamingServer (queue_file)
3. Server sends `strm s` (start) to player with HTTP URL
4. Player opens HTTP connection to /stream.mp3
5. StreamingServer delivers audio (direct or transcoded)
6. Player reports status via STAT
```

### Remote URL Proxy Streaming

Squeezebox hardware (SB2/3, Boom, Classic) cannot handle HTTPS and has limited
HTTP capabilities. For internet radio, podcasts, and external streaming services
the server acts as a transparent proxy:

```
1. Content provider plugin resolves item → StreamInfo(url="https://...")
2. Server queues URL in StreamingServer (queue_url)
3. Server sends `strm s` (start) to player with local HTTP URL
4. Player opens HTTP connection to /stream.mp3 (local server)
5. Streaming route fetches remote URL via httpx and relays chunks
6. ICY metadata (Shoutcast/Icecast) is automatically stripped
7. Player reports status via STAT
```

```
┌──────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────┐
│ Content  │────►│   Resonance  │────►│  Remote URL  │     │          │
│ Provider │     │   Server     │◄────│  (HTTPS)     │     │  Player  │
│ Plugin   │     │              │─────┼──────────────┼────►│  (HTTP)  │
└──────────┘     └──────────────┘     └──────────────┘     └──────────┘
  browse/search    queue_url()          httpx fetch          /stream.mp3
  get_stream_info  proxy relay          ICY strip            audio data
```

**Supported scenarios:**

| Source | Example | Proxy needed? |
|--------|---------|---------------|
| Internet Radio | Shoutcast/Icecast streams | Yes (HTTPS, ICY metadata) |
| Podcasts | RSS feed → episode URL | Yes (HTTPS) |
| Streaming services | API → stream URL | Yes (HTTPS + auth) |
| Local files | `/music/song.mp3` | No (direct/transcode) |

### Transcoding

```
┌─────────┐    ┌─────────┐    ┌──────────────┐
│ M4B/M4A │───►│  faad   │───►│ lame/flac    │───► Player
│  File   │    │ Decoder │    │ (rule-based) │
└─────────┘    └─────────┘    └──────────────┘
```

**Decision logic:** `streaming/policy.py`

| Format | Action |
|--------|--------|
| MP3, FLAC, OGG, WAV | Direct streaming |
| M4A, M4B, AAC | Transcode via faad→mp3 (rule-dependent, possibly flac) |
| Remote URL (HTTP/HTTPS) | Proxy streaming via httpx |

### Seek Coordination

Problem: Rapid seeks cause race conditions.

Solution: `SeekCoordinator` with latest-wins semantics.

```python
# Each seek increments a generation counter
# Only the latest seek is executed
# 50ms coalescing for rapid consecutive seeks
```

### Elapsed Calculation (LMS-compatible)

After a seek, the player reports `elapsed` relative to the stream start:

```python
# Formula (same as LMS):
elapsed = start_offset + raw_elapsed

# Example: seek to 30s
# Player reports: 0, 1, 2, 3...
# Server calculates: 30+0=30, 30+1=31, 30+2=32...
```

---

## 🔊 Device Capabilities

Squeezebox devices have different hardware and need different treatment.
LMS solves this with a Perl class hierarchy (Boom.pm, SqueezePlay.pm, etc.).
Resonance uses a data-driven approach instead: `player/capabilities.py`.

### Volume Curves

LMS uses a logarithmic dual-ramp curve. Parameters differ by device:

| Device | Range | Step Point | Step Fraction | Description |
|--------|-------|------------|---------------|-------------|
| Squeezebox2/3 | -50 dB | -1 | 1.0 | Single ramp |
| Transporter | -50 dB | -1 | 1.0 | Inherits from SB2 |
| Receiver | -50 dB | -1 | 1.0 | Inherits from SB2 |
| **Boom** | -74 dB | 25 | 0.5 | Dual ramp (built-in speaker) |
| **SqueezePlay** | -74 dB | 25 | 0.5 | Radio/Touch/Controller, same as Boom |

The Boom/SqueezePlay curve shifts 50% volume to the 25% position,
giving finer control at low volumes (quiet at night vs. loud during the day).

### Hardware Flags

Each device declares which features it supports:

| Flag | Boom | SB2/SB3 | Transporter | SqueezePlay |
|------|------|---------|-------------|-------------|
| `has_line_in` | Yes | No | No | No |
| `has_digital_in` | No | No | Yes | No |
| `has_balance` | No | Yes | Yes | Yes |
| `has_bass/treble` | Yes (±23) | No | No | No |
| `has_stereo_xl` | Yes (0-3) | No | No | No |
| `can_power_off` | No | Yes | Yes | Yes |

### Usage

```python
# Automatic via PlayerClient:
client.device_capabilities.volume_params      # VolumeParameters
client.device_capabilities.has_line_in        # bool
client.device_capabilities.has_bass           # True if max_bass != min_bass

# set_volume() uses the correct curve automatically:
await client.set_volume(50)  # → Boom gets -74dB curve, SB2 gets -50dB
```

---

## 🌐 Web Layer Architecture

### FastAPI + JSON-RPC

```
Browser/App Request
      │
      ▼
┌─────────────────────────────────────────────────────┐
│                  FastAPI (Port 9000)                 │
│                                                      │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐ │
│  │ Static/UI   │  │ JSON-RPC    │  │ Cometd       │ │
│  │ (SvelteKit) │  │ (/jsonrpc)  │  │ (Real-Time)  │ │
│  └─────────────┘  └─────────────┘  └──────────────┘ │
└───────────────────────────┬─────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────┐
│              Command Handlers                        │
│  status.py | playback.py | playlist.py | seeking.py │
└───────────────────────────┬─────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────┐
│              Core Services                           │
│  MusicLibrary | Playlist | PlayerRegistry            │
└─────────────────────────────────────────────────────┘
```

### JSON-RPC Format (LMS-compatible)

```json
{
  "id": 1,
  "method": "slim.request",
  "params": [
    "aa:bb:cc:dd:ee:ff",
    ["playlist", "play", "/path/to/song.mp3"]
  ]
}
```

### Cometd/Bayeux

Long-polling for real-time updates (iPeng, Squeezer, etc.):

- `/meta/handshake` — Create session
- `/meta/connect` — Fetch events (60s timeout)
- `/slim/subscribe` — Subscribe to player events

---

## 🗄️ Database

### SQLite with aiosqlite

**Schema (v8):**

```sql
-- Core tables
tracks (id, url, title, artist_id, album_id, duration_ms, ...)
artists (id, name)
albums (id, title, artist_id, year, artwork_url)
genres (id, name)
contributors (id, name, role)

-- Associations
track_genres (track_id, genre_id)
track_contributors (track_id, contributor_id, role)
```

### Library Facade

```python
library = MusicLibrary(db_path)
await library.scan_directory("/music")
artists = await library.list_artists()
tracks = await library.search("Beatles")
```

---

## 🎨 Frontend (Svelte Web UI)

The web UI is a **Svelte 5** single-page app (SPA) in `web-ui/`.
It is built as a static site and served by the backend (FastAPI) as `StaticFiles`
on `/`.

**URL:** `http://localhost:9000/`

### Tech Stack

- **Svelte 5** + **TypeScript**
- **SvelteKit** + `adapter-static` (SPA fallback `index.html`)
- **Tailwind CSS v4**
- Backend: **FastAPI** serves static assets (no separate web server needed)

### Dev / Build / Prod Mounting

- **Dev:** `scripts/dev.ps1` starts
  - Backend (FastAPI) on **:9000**
  - Vite dev server on **:5173** (with proxy to JSON-RPC/HTTP API)
- **Build:** `cd web-ui && npm run build` produces the static build output in `web-ui/build/`.
- **Prod:** The backend mounts `web-ui/build/` as `StaticFiles(..., html=True)` on `/`
  (SPA fallback to `index.html`).

### Architecture

```
Browser ──HTTP──▶ FastAPI (StaticFiles "/")
                    │
                    ├──JSON-RPC/Cometd──▶ Resonance Backend (Slimproto/Playlist/Streaming)
                    │
                    └──REST/SSE──▶ Plugin UI (SDUI)
                                    │
                                    ├─ GET  /api/plugins/{id}/ui      → JSON widget tree
                                    ├─ POST /api/plugins/{id}/actions → action dispatch
                                    └─ GET  /api/plugins/{id}/events  → SSE live updates
```

### Server-Driven UI (SDUI)

Plugins can provide full web UI pages without shipping any JavaScript to the browser.
A plugin defines its UI declaratively in Python (using widget classes from `resonance/ui/`),
and the frontend renders it generically via a recursive component renderer.

- **Backend:** `resonance/ui/__init__.py` — 20+ widget classes (`Heading`, `Table`, `Form`, `Modal`, …)
- **Frontend:** `web-ui/src/lib/plugin-ui/` — `PluginRenderer.svelte` (recursive), `registry.ts` (type → component map), 20 widget Svelte components
- **Live updates:** SSE endpoint with `EventSource` + automatic polling fallback
- **Security:** No plugin JavaScript runs in the browser — UI is declarative JSON only (security-by-design)
- **Reference:** [`PLUGIN_API.md` §19](./PLUGIN_API.md#19-server-driven-ui-sdui)

### Feature Flags

- `RESONANCE_DISPLAY=1`
  Enables bitmap display rendering for SB2/SB3/Classic/Boom (default: off, pending HW verification).
  When enabled, the server wires the `DisplayManager` to `SlimprotoServer`, `PlaylistManager`,
  and `StreamingServer` and manages its lifecycle with start/stop.

---

## 🔧 Technology Stack

| Component | Technology |
|-----------|------------|
| **Runtime** | Python 3.11+ (asyncio) |
| **Web Framework** | FastAPI |
| **Web UI** | Svelte 5 SPA (`web-ui/`) + Tailwind v4 |
| **Plugin UI** | Server-Driven UI (SDUI) — declarative Python → JSON → Svelte rendering |
| **Database** | SQLite + aiosqlite |
| **Audio Metadata** | mutagen |
| **Transcoding** | faad, flac, lame, sox |
| **HTTP Client** | httpx (remote URL proxy) |
| **Security** | CSP headers middleware, X-Frame-Options, X-Content-Type-Options |
| **Testing** | pytest (2853 tests) |

---

## 📚 Further Reading

→ [PLUGINS.md](./PLUGINS.md) — Plugin system overview (incl. Content Providers, SDUI)
→ [PLUGIN_API.md](./PLUGIN_API.md) — Plugin API reference (incl. §19 SDUI, ContentProvider ABC)
→ [PLUGIN_TUTORIAL.md](./PLUGIN_TUTORIAL.md) — Step-by-step plugin tutorial
→ [PLUGIN_REPOSITORY.md](./PLUGIN_REPOSITORY.md) — Community plugin publishing guide
→ [CHANGELOG.md](./CHANGELOG.md) — Change log
→ [HARDWARE_TESTING.md](./HARDWARE_TESTING.md) — Hardware testing runbook (bitmap displays, `RESONANCE_DISPLAY=1`)

---

*Last updated: June 2025 (SDUI architecture section added)*