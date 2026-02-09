<p align="center">
  <img src="assets/logos/resonance-waves-light.svg" alt="Resonance" width="160" height="160" />
</p>

<h1 align="center">Resonance</h1>

<p align="center">
  <strong>A modern, LMS-compatible music server written in Python</strong>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-GPL--2.0-blue.svg" alt="License: GPL-2.0" /></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-3776ab.svg?logo=python&logoColor=white" alt="Python 3.11+" />
  <img src="https://img.shields.io/badge/svelte-5-ff3e00.svg?logo=svelte&logoColor=white" alt="Svelte 5" />
  <img src="https://img.shields.io/badge/tailwind-4-06b6d4.svg?logo=tailwindcss&logoColor=white" alt="Tailwind v4" />
  <img src="https://img.shields.io/badge/status-alpha-orange.svg" alt="Status: Alpha" />
</p>

<p align="center">
  An independent reimplementation of the
  <a href="https://lyrion.org/">Lyrion Music Server</a>
  (formerly Logitech Media Server / SlimServer), built from scratch in Python with asyncio.
  <br />
  Controls Squeezebox hardware, Squeezelite, and works with iPeng, Squeezer, and other LMS-compatible apps.
</p>

---

> **Disclaimer** — Resonance is a hobby project, **not affiliated with or endorsed by** the
> Lyrion / LMS project. It is under active development, **not finished**, and will contain bugs.
> When protocol behavior is unclear, the LMS source code is the reference.
> The developer only owns a single Squeezebox Radio — other hardware
> (Touch, Boom, Transporter, Classic, Controller) has **not been tested**.
> Feedback and bug reports are very welcome!

---

<!-- Screenshots — drop your images into docs/screenshots/ and uncomment:
<p align="center">
  <img src="docs/screenshots/now-playing.png" alt="Now Playing" width="800" />
</p>
-->

## Table of Contents

- [Quick Start](#quick-start)
- [Architecture](#architecture)
- [Features](#features)
- [Installation Details](#installation-details)
- [Transcoding Tools (optional)](#transcoding-tools-optional)
- [Web UI](#web-ui)
- [First Steps](#first-steps)
- [Project Structure](#project-structure)
- [Running the Tests](#running-the-tests)
- [Contributing](#contributing)
- [License](#license)
- [Acknowledgments](#acknowledgments)

---

## Quick Start

**Linux / macOS:**

```bash
git clone https://github.com/endegelaende/resonance-server.git
cd resonance-server
python3 -m venv .venv
.venv/bin/python -m pip install mutagen aiosqlite fastapi uvicorn
.venv/bin/python -m resonance
```

**Windows (PowerShell or cmd.exe):**

```powershell
git clone https://github.com/endegelaende/resonance-server.git
cd resonance-server
python -m venv .venv
.venv\Scripts\python.exe -m pip install mutagen aiosqlite fastapi uvicorn
.venv\Scripts\python.exe -m resonance
```

The server starts on ports **3483** (Slimproto), **9000** (HTTP/API), and **9090** (CLI).
Players on the same subnet may discover the server automatically via UDP broadcast.
If not, point your player to the server IP manually (e.g. `squeezelite -s <server-ip>`).

→ See [First Steps](#first-steps) for what to do next.

<details>
<summary><strong>Command-line options</strong></summary>

```
Options:
  -v, --verbose     Enable debug logging
  -p, --port PORT   Slimproto port (default: 3483)
  --host HOST       Bind address (default: 0.0.0.0)
  --web-port PORT   HTTP port (default: 9000)
  --cli-port PORT   Telnet CLI port (default: 9090, 0 to disable)
  --version         Show version
```

</details>

---

## Architecture

```
┌─────────────┐      ┌─────────────┐      ┌─────────────┐
│  Web-UI /   │      │             │      │ Squeezebox  │
│  iPeng /    │◄────►│  Resonance  │◄────►│ Radio/Touch │──► ))
│  Squeezer   │ HTTP │   Server    │Slim- │ Squeezelite │
│  Cadence    │      │  (Python)   │proto │             │
└─────────────┘      └──────┬──────┘      └─────────────┘
                            │
                     ┌──────┴──────┐
                     │   SQLite    │
                     │  Music DB   │
                     └─────────────┘
```

Resonance speaks the same protocols as LMS. The server gives commands, players execute.

| Port     | Protocol        | Purpose                       |
| -------- | --------------- | ----------------------------- |
| **3483** | Slimproto (TCP) | Binary player control         |
| **9000** | HTTP            | Streaming + JSON-RPC + Web UI |
| **9090** | Telnet CLI      | Text-based command interface  |

---

## Features

### Protocols & Compatibility

| Feature                                           | Status |
| ------------------------------------------------- | ------ |
| Slimproto (binary player control)                 | Yes    |
| JSON-RPC API (LMS-compatible)                     | Yes    |
| Cometd/Bayeux (real-time push)                    | Yes    |
| Telnet CLI (Port 9090)                            | Yes    |
| UDP Discovery (auto-detect)                       | Yes    |
| Jive Menu System (Radio, Touch, Boom, Controller) | Yes    |

### Audio

| Feature                                      | Status |
| -------------------------------------------- | ------ |
| HTTP Streaming (MP3, FLAC, OGG, WAV)         | Yes    |
| On-the-fly Transcoding (M4A, M4B, AAC, ALAC) | Yes    |
| Gapless Playback                             | Yes    |
| Crossfade (configurable overlap)             | Yes    |
| ReplayGain (track & album mode)              | Yes    |
| Seeking (byte-accurate & time-based)         | Yes    |

### Library & Playback

| Feature                                                | Status |
| ------------------------------------------------------ | ------ |
| Music Library (scanner, SQLite, full-text search)      | Yes    |
| Cover Art (extraction, caching, BlurHash placeholders) | Yes    |
| Playlist / Queue (shuffle, repeat, insert, move)       | Yes    |
| Alarm Scheduling (per-player)                          | Yes    |
| Device Capabilities (volume curves, hardware flags)    | Yes    |

### Frontends

| Frontend                                                   | Status         |
| ---------------------------------------------------------- | -------------- |
| **Web UI** — Svelte 5 + Tailwind v4 (see [below](#web-ui)) | Yes            |
| **iPeng** (iOS)                                            | Not verified   |
| **Squeezer** (Android)                                     | Not verified   |
| **Cadence** — Flutter desktop app                          | In development |

---

## Installation Details

The [Quick Start](#quick-start) above covers cloning and installing.
Below are additional details for reference.

**Alternative — Download ZIP** instead of `git clone`:
Go to https://github.com/endegelaende/resonance-server → green **Code** button → **Download ZIP**.

> **Tip:** If `python3` is not found or too old, install Python 3.11+ via your package manager:
> 
> - Debian/Ubuntu: `sudo apt install python3 python3-venv python3-pip`
> - Fedora: `sudo dnf install python3`
> - macOS: `brew install python@3`

### Python Dependencies

| Package     | Purpose                                              |
| ----------- | ---------------------------------------------------- |
| `mutagen`   | Read audio file metadata (tags, duration, cover art) |
| `aiosqlite` | Async SQLite for the music library database          |
| `fastapi`   | Web framework for JSON-RPC, REST API, streaming      |
| `uvicorn`   | ASGI server that runs FastAPI                        |

### Optional Dependencies

```bash
pip install blurhash-python pillow   # BlurHash cover art placeholders
```

---

## Transcoding Tools (optional)

Whether a format is streamed directly (passthrough) or transcoded depends on
both the **audio format** and the **player/device type**. The rules are defined
in `resonance/config/devices.toml` (device tiers) and
`resonance/config/legacy.conf` (transcoding pipelines).

**Common cases that need no extra tools:**
MP3, FLAC, OGG, and WAV are passthrough for most players (Squeezelite, SB2+, Radio, Touch, Boom).

**Cases that require external tools:**

| Tool     | When needed                                                           |
| -------- | --------------------------------------------------------------------- |
| **faad** | M4A, M4B, ALAC, AAC-in-MP4 — decodes audio from MP4 containers       |
| **lame** | Used together with faad — encodes the decoded stream to MP3           |
| **flac** | FLAC → PCM conversion (devices requesting raw PCM), server-side crossfade |
| **sox**  | Opus support, OGG → PCM fallback, server-side crossfade              |

> **Example:** MP4-container formats (M4A, M4B, ALAC) always need transcoding
> because no Squeezebox hardware or Squeezelite can reliably stream MP4 over HTTP.
> WMA works on SB2+ but needs transcoding on SLIMP3. Opus always needs `sox`.

### Windows

Binaries are **included** in `third_party/bin/` — no extra installation needed.

### Linux

```bash
# Standard tools (Debian/Ubuntu):
sudo apt install -y flac lame sox
```

For `faad` you need the **LMS-patched version** from
[ralph-irving/faad2](https://github.com/ralph-irving/faad2), which adds
seeking support (`-j`/`-e` flags) and ALAC decoding.

<details>
<summary><strong>How to get the LMS-patched faad binary</strong></summary>

**Option A — Extract from LMS package (easiest):**

Download the LMS package from [lms-community.github.io](https://lms-community.github.io/getting-started/),
extract it, and copy the `faad` binary for your architecture:

```bash
# Example for x86_64:
cp /path/to/lms/Bin/x86_64-linux/faad third_party/bin/faad
chmod +x third_party/bin/faad
```

Available architectures: `x86_64-linux`, `aarch64-linux`, `arm-linux`,
`armhf-linux`, `i386-linux`, `powerpc-linux`, `sparc-linux`, `darwin`,
`i386-freebsd-64int`, `i86pc-solaris-thread-multi-64int`.

**Option B — Build from source:**

```bash
git clone https://github.com/ralph-irving/faad2.git
cd ~/faad2
autoreconf -i
./configure
make
sudo make install
sudo ldconfig
```

**Verify:**

```bash
ldconfig -p | grep libfaad
which faad
faad -h
```

</details>

> **Note:** If you place the binaries in `third_party/bin/`, Resonance finds them
> automatically. Otherwise, make sure they are on your system `PATH`.

---

## Web UI

Resonance ships with a modern web interface built with **Svelte 5**, **SvelteKit**, and
**Tailwind CSS v4**. It covers basic library browsing, playback control, and queue
management.

<!-- Uncomment when you add screenshots:
<p align="center">
  <img src="docs/screenshots/web-ui-overview.png" alt="Web UI" width="800" />
</p>
-->

### Running the Web UI

```bash
cd web-ui
npm install       # one-time
npm run dev       # dev server → http://localhost:5173
npm run build     # production build → web-ui/build/
```

The dev server proxies API requests to the Python backend on port 9000.
Make sure the backend is running first.

### API Integration

The frontend communicates with Resonance via:

| Protocol | Endpoint      | Purpose                                              |
| -------- | ------------- | ---------------------------------------------------- |
| JSON-RPC | `/jsonrpc.js` | LMS-compatible API (player control, library queries) |
| REST     | `/api/*`      | Modern endpoints (folders, scan, artwork, delete)    |
| Cometd   | `/cometd`     | Real-time updates (currently uses polling)           |

### Project Structure

```
web-ui/
├── src/
│   ├── app.css                       # Global styles + Tailwind v4 theme
│   ├── app.html                      # HTML shell
│   ├── lib/
│   │   ├── api.ts                    # API client (JSON-RPC + REST)
│   │   ├── components/
│   │   │   ├── AddFolderModal.svelte     # Add music folder dialog
│   │   │   ├── AlarmSettings.svelte      # Per-player alarm management
│   │   │   ├── BlurHashPlaceholder.svelte# Blurred artwork placeholder
│   │   │   ├── CoverArt.svelte           # Album art with BlurHash + glow
│   │   │   ├── NowPlaying.svelte         # Playback controls + progress
│   │   │   ├── PlayerSelector.svelte     # Multi-player dropdown
│   │   │   ├── QualityBadge.svelte       # Lossless / Hi-Res indicators
│   │   │   ├── Queue.svelte              # Playlist sidebar
│   │   │   ├── ResizeHandle.svelte       # Drag-to-resize panels
│   │   │   ├── SearchBar.svelte          # Search with debounce + Ctrl+K
│   │   │   ├── SettingsPanel.svelte      # Player settings
│   │   │   ├── Sidebar.svelte            # Navigation sidebar
│   │   │   ├── ToastContainer.svelte     # Toast notification renderer
│   │   │   └── TrackList.svelte          # Track list with play/add actions
│   │   └── stores/
│   │       ├── color.svelte.ts           # Dynamic accent colors (Vibrant)
│   │       ├── player.svelte.ts          # Player state + polling
│   │       ├── toast.svelte.ts           # Toast notification store
│   │       └── ui.svelte.ts              # Navigation + layout state
│   └── routes/
│       ├── +layout.svelte
│       └── +page.svelte
├── static/                           # Fonts, favicon, brand assets
├── package.json
├── svelte.config.js
├── tsconfig.json
└── vite.config.ts
```

---

## First Steps

Once the server is running:

1. **Add your music** — Open `http://localhost:9000` in your browser (or `http://localhost:5173`
   if running the dev server). Click **Add Folder**, enter the path to your music directory,
   and the library scan starts automatically.

2. **Connect a player** — Start [Squeezelite](https://github.com/ralph-irving/squeezelite)
   or power on your Squeezebox hardware. Players on the same subnet may discover the server
   automatically via UDP broadcast. If discovery doesn't work, specify the server IP explicitly
   (e.g. `squeezelite -s <server-ip>` or enter the IP in your hardware player's network settings).

3. **Play music** — Browse your library in the Web UI, select a track or album, and hit play.
   LMS-compatible apps like iPeng (iOS) or Squeezer (Android) should work as well,
   but this has not been fully verified yet.

---

## Project Structure

```
resonance-server/
├── resonance/                    # Main Python package
│   ├── __main__.py               # Entry point (python -m resonance)
│   ├── server.py                 # Main server, starts all components
│   ├── config/                   # Configuration
│   │   ├── devices.toml          #   Device tiers (Modern/Legacy)
│   │   └── legacy.conf           #   Transcoding rules (LMS-style)
│   ├── core/                     # Business logic
│   │   ├── library.py            #   MusicLibrary facade
│   │   ├── library_db.py         #   SQLite database layer
│   │   ├── scanner.py            #   Audio file scanner (mutagen)
│   │   ├── playlist.py           #   Playlist management
│   │   ├── artwork.py            #   Cover art extraction + BlurHash
│   │   └── events.py             #   Event bus (pub/sub)
│   ├── player/                   # Player management
│   │   ├── client.py             #   PlayerClient (status, commands)
│   │   ├── capabilities.py       #   Device capabilities + volume curves
│   │   └── registry.py           #   PlayerRegistry (all players)
│   ├── protocol/                 # Network protocols
│   │   ├── slimproto.py          #   Slimproto server (Port 3483)
│   │   ├── cli.py                #   Telnet CLI (Port 9090)
│   │   ├── discovery.py          #   UDP discovery
│   │   └── commands.py           #   Binary command builder
│   ├── streaming/                # Audio streaming
│   │   ├── server.py             #   Streaming server
│   │   ├── transcoder.py         #   Transcoding pipeline
│   │   ├── crossfade.py          #   Server-side crossfade (SoX)
│   │   ├── seek_coordinator.py   #   Latest-wins seek coordination
│   │   └── policy.py             #   Format decision logic
│   └── web/                      # HTTP layer
│       ├── server.py             #   FastAPI app (Port 9000)
│       ├── jsonrpc.py            #   JSON-RPC handler
│       ├── cometd.py             #   Bayeux long-polling
│       ├── handlers/             #   Command handlers
│       │   ├── status.py         #     Player status
│       │   ├── playback.py       #     Play/Pause/Stop
│       │   ├── playlist.py       #     Queue commands
│       │   ├── seeking.py        #     Seek (non-blocking)
│       │   └── library.py        #     Library queries
│       └── routes/               #   FastAPI routes
│           ├── api.py            #     REST API
│           ├── streaming.py      #     /stream.mp3
│           ├── artwork.py        #     Cover art endpoints
│           └── cometd.py         #     /cometd
├── web-ui/                       # Svelte 5 frontend
├── tests/                        # pytest suite
├── scripts/                      # Dev & test scripts
├── third_party/                  # External binaries
│   ├── bin/                      #   faad, flac, lame, sox (Windows)
│   └── squeezelite/              #   Squeezelite binary
├── docs/                         # Documentation
│   ├── ARCHITECTURE.md           #   System architecture
│   ├── PROTOCOL_REFERENCE.md     #   Protocol deep-dive
│   ├── OPERATIONS.md             #   Test & operations guide
│   └── CHANGELOG.md              #   Change log
├── pyproject.toml                # Python project config
└── LICENSE                       # GPL-2.0
```

---

## Running the Tests

```bash
# Install dev dependencies (one-time)
pip install -e ".[dev]"

# Run the full test suite
pytest

# Run with coverage report
pytest --cov
```

---

## Contributing

Resonance is a hobby project and contributions are welcome! Here's how you can help:

- **Bug reports** — Open an [issue](https://github.com/endegelaende/resonance-server/issues)
  with steps to reproduce.
- **Hardware testing** — If you own Squeezebox hardware other than Radio, your test results are especially valuable.
- **Pull requests** — Fork the repo, create a branch, make your changes, and open a PR.

---

## License

[GPL-2.0](LICENSE) — same as the original Lyrion Music Server.

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for licenses of all dependencies
and shipped binaries.

---

## Acknowledgments

A huge thank-you to the Squeezebox community — you keep this wonderful platform alive.

- [Lyrion Music Server](https://lyrion.org/) ([GitHub](https://github.com/LMS-Community/slimserver)) — the original that inspired this project
- [LMS Community Forums](https://forums.slimdevices.com/) — for keeping Squeezebox alive
- [Squeezelite](https://github.com/ralph-irving/squeezelite) by Ralph Irving — excellent software player
- [ralph-irving/faad2](https://github.com/ralph-irving/faad2) — patched faad binary with seeking and ALAC support

If you have feedback, ideas, or run into bugs — please
[open an issue](https://github.com/endegelaende/resonance-server/issues) or start a discussion.
Community input is what makes this project better.
