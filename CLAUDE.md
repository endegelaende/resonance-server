# CLAUDE.md — Der eine Einstiegspunkt

Stand: Maerz 2026

> **KI-Assistenten: Lies diese Datei zuerst. Dann bist du bereit.**
> Fuer Deep-Dives gibt es genau zwei weitere Dateien:
>
> - `docs/dev/CODE_INDEX.md` — Modul-Details, Abhaengigkeiten, Datenfluesse, Debugging
> - `docs/dev/PROTOCOL_REFERENCE.md` — Slimproto, JSON-RPC, Cometd, Jive Wire-Protokoll

---

## 1) Was ist Resonance?

Ein moderner Musikserver (Python 3.11+, asyncio) — eine vollstaendige Neuimplementierung
des Logitech Media Server (LMS/SlimServer). Steuert Squeezebox-Hardware und Software-Player
(Squeezelite) ueber das Slimproto-Protokoll.

```text
┌─────────────┐      ┌─────────────┐      ┌─────────────┐
│  Web-UI /   │      │             │      │ Squeezebox  │
│  iPeng /    │◄────►│  Resonance  │◄────►│ Radio/Touch │──► ))
│  Squeezer   │ HTTP │   Server    │Slim- │ Squeezelite │
│             │      │  (Python)   │proto │             │
└─────────────┘      └──────┬──────┘      └─────────────┘
                            │
                     ┌──────┴──────┐
                     │   SQLite    │
                     │  Music DB   │
                     └─────────────┘
```

**Ports:** 3483 (Slimproto TCP), 9000 (HTTP/JSON-RPC/Streaming/Web-UI), 9090 (Telnet CLI)

---

## 2) Repos und Pfade

| Repo                            | Lokaler Pfad                                                | GitHub                                                                                                  | Inhalt                                                 |
| ------------------------------- | ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- | ------------------------------------------------------ |
| **resonance-server**            | `C:\Users\stephan\Desktop\resonance-server`                 | [endegelaende/resonance-server](https://github.com/endegelaende/resonance-server)                       | Server, Plugin-Framework, SDUI, Web-UI, Tests, Docs    |
| **resonance-community-plugins** | `C:\Users\stephan\Desktop\resonance-community-plugins-main` | [endegelaende/resonance-community-plugins](https://github.com/endegelaende/resonance-community-plugins) | Community-Plugins, CI/CD Workflows, GitHub Pages Index |
| **slimserver-public**           | `C:\Users\stephan\Desktop\slimserver-public-9.2`            | —                                                                                                       | LMS 9.2 Quelltexte (Perl, read-only Referenz)          |

---

## 3) Aktueller Stand

### Server

- **Tests:** 2853 passed, 2 skipped
- **Protokolle:** Slimproto, JSON-RPC, Cometd/Bayeux, Telnet CLI, UDP Discovery — alles implementiert
- **Audio:** MP3/FLAC/OGG/WAV direkt, M4A/M4B/AAC/ALAC via Transcoding, DSD/DoP, Gapless, Crossfade, ReplayGain
- **Plugin-System:** Phasen A-E complete (Settings, States, ZIP-Installer, Repository-Client)
- **SDUI:** Phase 1–3 + UX Polish feature-complete (20+ Widget-Typen, SSE, visible_when, Modal, Form, Inline-Edit, help_text, Markdown-Rendering, Focus-Trap)
- **Security:** CSP Headers Middleware
- **Core-Plugins:** Example, Favorites, NowPlaying, Radio, Podcast
- **Web-UI:** Svelte 5 + Tailwind v4, 20+ Komponenten, 4 Stores
- **Frontends:** Web-UI, iPeng (iOS), Squeezer (Android) verifiziert

### Community-Plugins

- GitHub Actions laufen (build-release + update-index), alle gruen
- **raopbridge v0.1.0** released (v0.2.0 lokal, noch nicht getaggt)
- GitHub Pages Index live: `https://endegelaende.github.io/resonance-community-plugins/index.json`
- End-to-End Pipeline funktioniert: Tag → CI Build → Release → Index → Server Install

### Was ist erledigt (nicht nochmal anfassen)

| Feature                                                                                                        | Status                  |
| -------------------------------------------------------------------------------------------------------------- | ----------------------- |
| Plugin-System Phasen A-E                                                                                       | ✅                      |
| SDUI Framework Phase 1–3 + UX Polish                                                                           | ✅                      |
| raopbridge SDUI-Migration (5 Tabs, Device Modal, Settings Form)                                                | ✅                      |
| Plugin-Repository Betrieb Phase F (CI/CD, GitHub Pages, Index)                                                 | ✅                      |
| CSP Security Headers                                                                                           | ✅                      |
| Issue #11 (Pinoatrome AirPlay PR)                                                                              | ✅ funktional abgedeckt |
| `help_text` Prop fuer SDUI Form-Widgets (5 Widgets, Python+Svelte)                                             | ✅                      |
| PLUGIN_API.md Major Upgrade (2026 → 3370 Zeilen, Widget-Referenz)                                              | ✅                      |
| SDUI Widget Polish (MarkdownBlock, Row/Column gap, ActionButton icon+spinner, Toggle layout, Modal focus-trap) | ✅                      |
| `web-ui/README.md` Rewrite (27 → 500 Zeilen, Architektur, Stores, Widget-Inventar, SDUI-Renderer, Theming)     | ✅                      |
| Dokument-Relationen in beiden `CLAUDE.md` (Dok→Dok, Code→Dok, Repo-uebergreifende Sync-Pflichten)              | ✅                      |

---

## 4) LMS-First Grundsatz (bindend)

> **"Der LMS (SlimServer)-Code hat recht."**
> Bei Unklarheit zuerst im LMS-Code (`slimserver-public-9.2`) nachsehen.
> Nur Verhalten umsetzen, das im LMS-Code belegbar ist.
> Wenn nicht verifiziert: nicht raten. Keine Heuristiken bauen.

---

## 5) Implementierungsregeln

1. `status.time` darf innerhalb derselben Track/Generation nicht rueckwaerts laufen.
2. `menu:menu` liefert `item_loop`, sonst `playlist_loop`.
3. `playlist_timestamp` fuer Highlighting konsistent pflegen.
4. Cometd-Subscriptions als Re-Execution behandeln, nicht nur Event-Push.
5. Seek normal: kein `end_seconds` fuer faad-Pfad setzen.
6. PlayerRegistry im Web-Layer konsistent async abfragen.
7. Vor groesseren Aenderungen immer erst zielgerichtete Tests, dann Vollsuite.
8. `displaystatus subscribe:showbriefly` darf kein synthetisches leeres Display-Payload erzeugen.
9. Display-Bitmaps: column-major (MSB-first, top-to-bottom). Frame-Groessen: SB2/3 1280B, Boom 640B, SBG 560B.
10. Track-Typen nicht verwechseln: `library.Track` (DB-Modell) vs. `PlaylistTrack` (Playlist-Modell).
11. Web-UI API-Aufrufe via JSON-RPC: `api.rpc()`, nicht eigene REST-Endpoints.
12. SDUI: Kein Plugin-JavaScript im Browser. UI ist deklaratives JSON — Security by Construction.

---

## 6) Projektstruktur (Server)

```text
resonance-server/
├── resonance/                    # Hauptpaket
│   ├── __main__.py               # Entry Point
│   ├── server.py                 # Startet alle Komponenten
│   ├── plugin.py                 # PluginContext, SettingDefinition, Manifest
│   ├── plugin_manager.py         # Discovery, Loading, Lifecycle
│   ├── plugin_installer.py       # ZIP-Installer mit SHA256
│   ├── plugin_repository.py      # Repository-Client (Index-Fetch, Cache)
│   ├── content_provider.py       # ContentProvider ABC + Registry
│   ├── ui/__init__.py            # SDUI Widget-Klassen (20+)
│   ├── core/                     # Library, Scanner, Playlist, Events, Artwork
│   ├── player/                   # PlayerClient, Capabilities, Registry
│   ├── protocol/                 # Slimproto, CLI, Discovery, Commands
│   ├── streaming/                # StreamingServer, Transcoder, Crossfade, Seek
│   └── web/                      # FastAPI, JSON-RPC, Cometd, Security
│       ├── handlers/             # Command-Handler (status, playback, playlist, seeking, menu, library)
│       └── routes/               # REST-API, Streaming, Artwork, Cometd
├── plugins/                      # Core-Plugins (example, favorites, nowplaying, radio, podcast)
├── web-ui/                       # Svelte 5 SPA
│   └── src/lib/
│       ├── api.ts                # ~1500 LOC, ~50 Methoden
│       ├── stores/               # player, ui, color, toast
│       ├── components/           # 20+ Svelte-Komponenten
│       └── plugin-ui/            # SDUI Rendering (registry, renderer, 20 widgets)
├── tests/                        # 2853 Tests
├── scripts/                      # Dev-Skripte (dev, smoketest, hardware-test, rpc-console, decode-slimproto)
└── docs/                         # Oeffentliche Docs (Englisch)
```

### Projektstruktur (Community-Plugins)

```text
resonance-community-plugins/
├── .github/workflows/
│   ├── build-release.yml         # Tag → ZIP + SHA256 → GitHub Release
│   └── update-index.yml          # Scan → index.json → GitHub Pages
├── plugins/
│   └── raopbridge/               # AirPlay Bridge (v0.2.0 lokal)
│       ├── plugin.toml           # Manifest
│       ├── __init__.py           # get_ui(), handle_action(), setup(), teardown()
│       ├── bridge.py             # RaopBridge Subprocess-Management
│       ├── config.py             # Device/Config XML-Parsing
│       ├── serializers.py        # Serialisierung
│       └── tests/
└── README.md
```

---

## 7) Teststufen

### Stufe A: Schnelltest nach Aenderung

```powershell
.venv\Scripts\python.exe -m pytest -q
```

### Stufe B: Vollsuite

```powershell
.venv\Scripts\python.exe -m pytest -v
```

### Stufe C: Live-Handover-Smoketest

```powershell
python scripts/smoketest.py --album-id 3 --seconds 35 --require-play --require-transitions 2 --strict-index-increment
```

PASS = Exitcode 0, FAIL = Exitcode 1.
Wann: nach Aenderungen an Playlist, Status, Seek, Streaming, Prefetch/Gapless.

### Stufe D: Displaystatus Guard

```powershell
python -m pytest tests/test_displaystatus.py -q
```

Wann: nach Aenderungen an `status.py`, `cometd.py`, Jive-Statuspfaden.

### Stufe E: Hardware-E2E Matrix

```powershell
.\scripts\hardware-test.ps1 -DeviceLabel "Radio" -PlayerId "00:04:20:26:84:ae" -TrackIds 154,155,156 -PollSeconds 120
```

Wann: Gapless/Crossfade/ReplayGain auf echter Hardware validieren.

---

## 8) Skripte

| Skript                        | Zweck                                                 |
| ----------------------------- | ----------------------------------------------------- |
| `scripts/dev.ps1`             | Backend (Port 9000) + Frontend (Port 5173) starten    |
| `scripts/smoketest.py`        | Live-Smoketest: Track-Handover, Progress, Transitions |
| `scripts/hardware-test.ps1`   | Hardware-E2E mit Report (JSON + Markdown)             |
| `scripts/rpc-console.py`      | Interaktive JSON-RPC-Konsole (CLI/GUI)                |
| `scripts/decode-slimproto.py` | Slimproto-Traffic aus Wireshark dekodieren            |

---

## 9) Plugin-Entwicklung

Plugins liegen in `plugins/<name>/` mit `plugin.toml` + `__init__.py`.

**PluginContext API:**

| Methode                                      | Was                                |
| -------------------------------------------- | ---------------------------------- |
| `register_command(name, handler)`            | JSON-RPC Command                   |
| `register_menu_node(...)`                    | Jive Menu Node                     |
| `register_menu_item(...)`                    | Jive Menu Entry                    |
| `register_route(router)`                     | FastAPI Router                     |
| `register_content_provider(id, provider)`    | Audio-Quelle (Radio, Podcast, ...) |
| `register_ui_handler(handler)`               | SDUI Page Builder                  |
| `register_action_handler(handler)`           | SDUI Action Dispatcher             |
| `subscribe(event_type, handler)`             | Event-Subscription (auto-cleanup)  |
| `get_setting(key)` / `set_setting(key, val)` | Plugin-Settings                    |
| `ensure_data_dir()`                          | Data-Verzeichnis anlegen           |
| `notify_ui_update()`                         | SSE-Push an Frontend               |

**Docs (oeffentlich, Englisch):**

- `docs/PLUGIN_API.md` — Vollstaendige API-Referenz inkl. §19 SDUI
- `docs/PLUGIN_TUTORIAL.md` — Schritt-fuer-Schritt Tutorial
- `docs/PLUGINS.md` — Plugin-System Ueberblick

### Community-Plugin Release-Flow

1. Code in `resonance-community-plugins/plugins/<name>/` entwickeln
2. Version in `plugin.toml` bumpen
3. Committen + pushen auf `main`
4. Tag: `git tag <name>-v<version>` → `git push origin <name>-v<version>`
5. CI baut ZIP + SHA256 → GitHub Release → Index → GitHub Pages
6. Server sieht neues Plugin unter "Available"

---

## 10) Web-UI

Svelte 5 SPA in `web-ui/`, Build-Output in `web-ui/build/`.

| URL                      | Was                                                |
| ------------------------ | -------------------------------------------------- |
| `http://localhost:9000/` | Produktion (FastAPI served statisch)               |
| `http://localhost:5173/` | Vite Dev-Server (nur lokal, via `scripts/dev.ps1`) |

```powershell
# Entwicklung
.\scripts\dev.ps1

# Oder manuell
cd web-ui && npm run dev

# Produktion
cd web-ui && npm run build
```

---

## 11) Offene Aufgaben

### Kurzfristig

- [ ] raopbridge v0.2.0 taggen und releasen
- [x] `help_text` Prop fuer SDUI Form-Widgets (5 Python-Widgets + 5 Svelte-Widgets)
- [x] SDUI Widget Polish: MarkdownBlock (echtes GFM via `marked`), Row/Column gap (Map statt dynamische Tailwind-Klassen), ActionButton (Icon-Rendering + Spinner), Toggle (konsistentes help_text Layout), Modal (Focus-Trap + Focus-Restore)
- [ ] Duplicate-Name-Validation fuer raopbridge Device-Namen
- [ ] Weitere Plugins mit SDUI ausstatten (favorites, radio, podcast)

### Mittelfristig

- [ ] CSP Hardening: SHA-256 Hashes statt `'unsafe-inline'`
- [ ] Display-Rendering HW-Verifikation (`RESONANCE_DISPLAY=1` default-on)

### Langfristig

- [ ] Sync/Multi-Room Implementierung
- [ ] RandomPlay/DontStopTheMusic Plugin
- [ ] Equalizer/DSP UI
- [ ] Mobile/Responsive Layout
- [ ] Drag & Drop (Queue, Favorites)

---

## 12) Dokumentations-Karte

> **Fuer Menschen:** `docs/dev/CHEATSHEET.md` — "Was will ich tun? → Lies das."

### Dateien die der KI-Assistent kennen muss

| Datei                            | Wann lesen                                   |
| -------------------------------- | -------------------------------------------- |
| `CLAUDE.md` (dieses File)        | Immer zuerst                                 |
| `docs/dev/CODE_INDEX.md`         | Bei Modul-Fragen, Abhaengigkeiten, Debugging |
| `docs/dev/PROTOCOL_REFERENCE.md` | Bei Slimproto/Cometd/Jive Wire-Level Fragen  |
| `docs/PLUGIN_API.md`             | Bei Plugin-API oder SDUI Fragen (§19 = SDUI) |
| `docs/PLUGIN_TUTORIAL.md`        | Bei Plugin-Entwicklung                       |
| `docs/CHANGELOG.md`              | Bei Fragen zur Aenderungshistorie            |
| `docs/dev/CODE_INDEX.md` §10     | Bei Display-Rendering / Hardware-Testing     |

### Dokument-Relationen (Was muss mit-aktualisiert werden?)

> **Regel:** Wenn du ein Dokument oder eine Quelldatei aenderst, pruefe die Spalte
> "Muss auch aktualisiert werden" und passe die abhaengigen Dateien an.

#### Dokument → Dokument

| Wenn du aenderst...                  | Muss auch aktualisiert werden                                                                                                               | Grund                                                                         |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| **`CLAUDE.md`** (Server)             | `community-plugins/CLAUDE.md` (§2 Repos, §3 Stand, §9 Aufgaben)                                                                             | Beide teilen Status, Repo-Tabelle und offene Aufgaben                         |
| **`community-plugins/CLAUDE.md`**    | `CLAUDE.md` (Server, §3 Stand, §11 Aufgaben)                                                                                                | Gegenstueck — gleiche Felder synchron halten                                  |
| **`docs/PLUGIN_API.md`**             | `docs/PLUGIN_TUTORIAL.md` (verweist auf §19 SDUI), `docs/PLUGIN_CASESTUDY.md` (verweist auf §19), `docs/PLUGINS.md` (verweist auf §16, §19) | Alle drei verlinken API-Sektionen per Anker — Anker-Aenderungen brechen Links |
| **`docs/PLUGIN_TUTORIAL.md`**        | `docs/PLUGIN_API.md` (Further Reading), `docs/PLUGIN_CASESTUDY.md` (Prerequisites-Link)                                                     | Gegenseitige "Further Reading"-Verweise                                       |
| **`docs/PLUGIN_CASESTUDY.md`**       | `docs/PLUGIN_TUTORIAL.md` (3× verlinkt), `docs/PLUGIN_API.md` (Further Reading)                                                             | Wird aus Tutorial und API-Referenz verlinkt                                   |
| **`docs/PLUGINS.md`**                | `docs/ARCHITECTURE.md` (Further Reading), `README.md` (Projektstruktur)                                                                     | Ueberblick-Dokument — wird von Einstiegspunkten referenziert                  |
| **`docs/ARCHITECTURE.md`**           | `README.md` (Architektur-Verweis), `docs/CHANGELOG.md` (Further Documentation)                                                              | Architektur-Diagramme werden extern verlinkt                                  |
| **`docs/CHANGELOG.md`**              | Keine zwingenden Abhaengigkeiten                                                                                                            | Append-only Log — andere Docs verweisen nur pauschal                          |
| **`docs/dev/CHEATSHEET.md`**         | Keine — aber MUSS aktualisiert werden wenn **irgendein** Doc hinzugefuegt/entfernt/umbenannt wird                                           | Ist die kanonische Dateiliste, wird von `CLAUDE.md` §12 referenziert          |
| **`docs/dev/CODE_INDEX.md`**         | Keine zwingenden Abhaengigkeiten                                                                                                            | Interne Referenz — keine externen Links darauf (ausser CLAUDE.md)             |
| **`docs/dev/PROTOCOL_REFERENCE.md`** | Keine zwingenden Abhaengigkeiten                                                                                                            | Interne Referenz — keine externen Links darauf (ausser CLAUDE.md)             |
| **`README.md`**                      | Keine zwingenden Abhaengigkeiten                                                                                                            | Wird nirgends per Dateilink verlinkt (ist GitHub-Einstiegsseite)              |
| **`web-ui/README.md`**               | `docs/PLUGIN_API.md` §19 (Widget-Inventar muss uebereinstimmen), `docs/dev/CHEATSHEET.md`                                                   | Enthaelt Widget-Inventar, Store-Doku, SDUI-Architektur — muss synchron sein   |
| **`community-plugins/README.md`**    | Keine zwingenden Abhaengigkeiten                                                                                                            | Eigenstaendig — nur von CHEATSHEET erwaehnt                                   |

#### Code → Dokument

| Wenn du aenderst...                                             | Muss auch aktualisiert werden                                                                                               | Grund                                                                   |
| --------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| **`resonance/ui/__init__.py`** (Widgets, Props, SCHEMA_VERSION) | `docs/PLUGIN_API.md` §19 (Widget Reference), `docs/PLUGIN_TUTORIAL.md` §Idea 6, `docs/PLUGIN_CASESTUDY.md` (SDUI-Beispiele) | API-Doku muss Props/Typen/Defaults/Validierung exakt widerspiegeln      |
| **`resonance/plugin.py`** (PluginContext, SettingDefinition)    | `docs/PLUGIN_API.md` §3-§5 (Lifecycle, Context, Settings), `docs/PLUGIN_TUTORIAL.md` (Setup-Beispiele)                      | Context-API und Settings-Schema sind in beiden Docs dokumentiert        |
| **`resonance/plugin_manager.py`**                               | `docs/PLUGIN_API.md` §4 (Lifecycle), `docs/PLUGINS.md` §2 (Loading), `docs/dev/CODE_INDEX.md` §3                            | Lifecycle-Aenderungen betreffen Doku und Modul-Index                    |
| **`resonance/plugin_installer.py`**                             | `docs/PLUGIN_API.md` §17 (Installation), `docs/PLUGINS.md` §8 (Management)                                                  | Installer-Verhalten ist in beiden Docs beschrieben                      |
| **`resonance/plugin_repository.py`**                            | `docs/PLUGIN_API.md` §17, `community-plugins/CLAUDE.md` §5 (Release-Flow)                                                   | Repository-Client-Verhalten und Index-Format                            |
| **`resonance/content_provider.py`**                             | `docs/PLUGIN_API.md` §16 (ContentProvider), `docs/PLUGINS.md` §3.4                                                          | ABC-Interface ist in beiden Docs dokumentiert                           |
| **`web-ui/src/lib/plugin-ui/widgets/*.svelte`**                 | `docs/PLUGIN_API.md` §19 (Rendering-Beschreibung pro Widget), `web-ui/README.md` (Widget-Inventar)                          | "Renders as"-Abschnitte und Widget-Tabelle muessen uebereinstimmen      |
| **`web-ui/src/lib/plugin-ui/registry.ts`**                      | `docs/PLUGIN_API.md` §19 (Widget-Typ-Liste, ALLOWED_TYPES), `web-ui/README.md` (Widget-Inventar)                            | Registry definiert welche Typen existieren                              |
| **`web-ui/src/lib/plugin-ui/PluginRenderer.svelte`**            | `docs/PLUGIN_API.md` §19 (visible_when Operatoren, Rendering-Pipeline), `web-ui/README.md` (SDUI Features)                  | Renderer-Logik (visible_when, fallback_text) ist in API-Doku und README |
| **`web-ui/src/lib/plugin-ui/PluginPageView.svelte`**            | `docs/PLUGIN_API.md` §19 (SSE, Polling, REST Endpoints), `web-ui/README.md` (SDUI Renderer)                                 | SSE/Polling-Verhalten und Endpoint-URLs sind dokumentiert               |
| **`web-ui/src/lib/stores/*.svelte.ts`**                         | `web-ui/README.md` (Store System)                                                                                           | Store-API und -Verhalten sind in der README dokumentiert                |
| **`web-ui/src/lib/components/*.svelte`**                        | `web-ui/README.md` (Component Inventory)                                                                                    | Komponentenliste muss bei Hinzufuegen/Entfernen aktualisiert werden     |
| **`web-ui/src/lib/api.ts`**                                     | `web-ui/README.md` (API Client Methoden-Tabelle)                                                                            | Methoden-Inventar muss bei neuen API-Methoden aktualisiert werden       |
| **`resonance/web/routes/plugin_*.py`**                          | `docs/PLUGIN_API.md` §19 (REST + SSE Endpoints)                                                                             | Endpoint-Pfade und Response-Formate                                     |
| **`.github/workflows/*.yml`** (Community-Plugins)               | `community-plugins/CLAUDE.md` §5 (Release-Flow), `community-plugins/README.md`                                              | CI/CD-Pipeline-Beschreibung                                             |
| **`plugins/*/plugin.toml`** (Community-Plugins)                 | `community-plugins/CLAUDE.md` §6 (Version), `docs/PLUGIN_API.md` §2 (Manifest)                                              | Version und Manifest-Schema                                             |

#### Repo-uebergreifende Sync-Pflichten

| Aenderung                          | Server-Repo                                                                                                 | Community-Plugins-Repo                |
| ---------------------------------- | ----------------------------------------------------------------------------------------------------------- | ------------------------------------- |
| Neues SDUI Widget hinzugefuegt     | `ui/__init__.py`, `registry.ts`, neues `.svelte`, `PLUGIN_API.md` §19, `web-ui/README.md` (Widget-Inventar) | Plugin-Code der es nutzt, `CLAUDE.md` |
| Widget-Prop geaendert/hinzugefuegt | `ui/__init__.py`, `.svelte`, `PLUGIN_API.md` §19, `web-ui/README.md` (bei neuen Features)                   | Plugin-Code der es nutzt              |
| PluginContext-Methode hinzugefuegt | `plugin.py`, `PLUGIN_API.md` §3, `CLAUDE.md` §9                                                             | `CLAUDE.md` §7 (Kurzreferenz)         |
| Neues Plugin released              | `CLAUDE.md` §3 (Stand)                                                                                      | `CLAUDE.md` §3-§6, `README.md`        |
| Offene Aufgabe erledigt            | `CLAUDE.md` §3 (erledigt) + §11 (Aufgaben)                                                                  | `CLAUDE.md` §9 (Aufgaben)             |
| Doku-Datei hinzugefuegt/umbenannt  | `CLAUDE.md` §12, `docs/dev/CHEATSHEET.md`                                                                   | —                                     |

### Soll-Zustand (nicht aendern ohne guten Grund)

- **Server-Repo:** 9 Haupt-Docs (`CLAUDE.md`, `README.md`, `THIRD_PARTY_NOTICES.md` + 6 in `docs/`) plus Sub-READMEs in `scripts/`, `tests/`, `web-ui/` (davon `web-ui/README.md` ~500 Zeilen mit Architektur, Store-Doku, Widget-Inventar, SDUI-Renderer)
- **Server-Repo `docs/dev/`:** 3 Dateien (`CHEATSHEET.md`, `CODE_INDEX.md`, `PROTOCOL_REFERENCE.md`) — gitignored, deutsch, nur lokal
- **Plugins-Repo:** 2 Dateien (`CLAUDE.md`, `README.md`)
- `docs/` (ohne `dev/`) ist oeffentlich auf GitHub — englisch

---

## 13) Session-Ablauf

1. **Dieses File lesen** — fertig, du bist orientiert
2. Scope festlegen — was soll gemacht werden?
3. Bei Bedarf: `CODE_INDEX.md` oder `PROTOCOL_REFERENCE.md` fuer Details
4. Fix/Feature umsetzen
5. Tests fahren (Stufe A mindestens, Stufe B bei groesseren Aenderungen)
6. Bei Bedarf dieses File aktualisieren

### Token-Check

Bei laengeren Sessions: ab ~80% Verbrauch Stand zusammenfassen
und dieses File aktualisieren. So kann die naechste Session nahtlos weitermachen.
