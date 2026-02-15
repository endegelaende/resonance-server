# Tests

pytest-Suite für Resonance. Stand: 541 Tests (2 skipped).

---

## Übersicht

| Datei | Tests | Bereich |
|---|---|---|
| `test_web_api.py` | 84 | FastAPI, JSON-RPC, REST Endpoints, Tag-Gating |
| `test_slimproto.py` | 76 | Slimproto Server, HELO-Parsing, Message-Dispatch, State-Machine |
| `test_commands.py` | 62 | Slimproto-Command-Builder (strm, audg, aude, setd) |
| `test_streaming.py` | 49 | Streaming-Endpoint, Range Requests, Content-Type |
| `test_cometd.py` | 43 | Cometd/Bayeux Protokoll (iPeng, Squeezer Kompatibilität) |
| `test_playlist.py` | 40 | Playlist-Operationen, Navigation, Repeat, Shuffle |
| `test_core_library.py` | 36 | LibraryDb, Scanner, MusicLibrary Facade |
| `test_transcoder.py` | 34 | Transcoding-Regeln, Policy, Command-Building |
| `test_discovery.py` | 31 | UDP Discovery Protokoll |
| `test_seek_coordinator.py` | 23 | SeekCoordinator, Latest-Wins, Generationen |
| `test_crossfade.py` | 20 | Crossfade/Gapless Prefetch Engine |
| `test_role_filters.py` | 12 | Contributor/Role-Filtering in DB |
| `test_cli.py` | 7 | Telnet CLI (Port 9090) Parsing |
| `test_jsonrpc_helpers.py` | 7 | JSON-RPC Helper-Utilities |
| `test_alarm_runtime.py` | 6 | Alarm-Scheduler (Due, Fire, One-Shot, Dedup) |
| `test_seeking_offsets.py` | 4 | Byte-Offset-Berechnung für MP3-Seeking |
| `test_playlist_prefetch_fast_path.py` | 3 | Prefetch-aware Playlist +1 Navigation |
| `test_displaystatus.py` | 2 | Displaystatus showbriefly Popup-Guard |
| `test_restart_stream_flags.py` | 2 | noRestartDecoder Flag-Handling |

---

## Tests ausführen

Alle Befehle aus dem Repo-Root.

```powershell
# Schnelltest (nach Änderungen)
.venv\Scripts\python.exe -m pytest tests/ -q

# Vollsuite mit Details
.venv\Scripts\python.exe -m pytest tests/ -v

# Einzelne Datei
.venv\Scripts\python.exe -m pytest tests/test_slimproto.py -v

# Einzelner Test
.venv\Scripts\python.exe -m pytest tests/test_slimproto.py::test_helo_parsing -v

# Displaystatus-Guard (nach Änderungen an status.py / cometd.py)
.venv\Scripts\python.exe -m pytest tests/test_displaystatus.py -q
```

Teststufen und wann welche Tests zu fahren sind → [`docs/OPERATIONS.md`](../docs/OPERATIONS.md)

---

## Kategorien

### Protokoll & Netzwerk
Tests für die drei Kommunikationskanäle (Slimproto, HTTP, CLI):

- `test_slimproto.py` — Binäres TCP-Protokoll, HELO, STAT, State-Machine
- `test_commands.py` — Server→Player Command-Builder (strm, audg, aude)
- `test_discovery.py` — UDP Discovery (Port 3483)
- `test_cli.py` — Telnet CLI Parsing (Port 9090)
- `test_cometd.py` — Cometd/Bayeux Long-Polling für LMS-Apps

### Web-API & JSON-RPC
- `test_web_api.py` — Größte Testdatei. FastAPI-Endpoints, JSON-RPC Commands, REST API, Tag-Gating, Year-Filtering
- `test_jsonrpc_helpers.py` — Parameter-Parsing, Player-Item-Building

### Audio & Streaming
- `test_streaming.py` — Streaming-Route, Range Requests, Content-Type Headers
- `test_transcoder.py` — legacy.conf Regeln, Format-Policy, Command-Building
- `test_crossfade.py` — Prefetch-Engine, STMd/STMu-Handling, Repeat-Modi
- `test_seek_coordinator.py` — Latest-Wins Semantik, Generation Counter
- `test_seeking_offsets.py` — MP3 Byte-Offset mit ID3v2 Synchsafe
- `test_restart_stream_flags.py` — noRestartDecoder Flag

### Bibliothek & Datenbank
- `test_core_library.py` — SQLite Schema, CRUD, Scanner, MusicLibrary Facade
- `test_role_filters.py` — Contributor-Rollen, Genre-Filtering
- `test_playlist.py` — Queue-Operationen, Navigation, Shuffle, Repeat
- `test_playlist_prefetch_fast_path.py` — Prefetch bei Playlist +1

### Player-Features
- `test_alarm_runtime.py` — Wecker-Scheduler
- `test_displaystatus.py` — Popup-Regression Guard (Radio/Touch)

---

## Konventionen

- Alle Tests sind reine Unit-Tests (kein laufender Server nötig)
- Async-Tests nutzen `pytest-asyncio` (auto-mode via `pyproject.toml`)
- Mocking über `unittest.mock` (AsyncMock, MagicMock, patch)
- DB-Tests verwenden `tmp_path` für temporäre SQLite-Dateien
- Web-Tests nutzen `httpx.AsyncClient` mit `ASGITransport` (kein echter Server)

### Abhängigkeiten

- `pytest` + `pytest-asyncio` — Test-Runner
- `httpx` — Für FastAPI/Starlette TestClient (test_web_api, test_cometd, test_streaming)

### Namenskonvention

- Dateien: `test_<modul>.py`
- Funktionen: `test_<was_getestet_wird>()`
- Fixtures mit `autouse=True` für Isolation (z.B. Alarm-Store Cleanup)