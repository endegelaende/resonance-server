# Scripts

Hilfs- und Testskripte für Entwicklung und Validierung.

---

## Übersicht

| Skript | Sprache | Zweck |
|---|---|---|
| [`dev.ps1`](#devps1) | PowerShell | Dev-Umgebung starten (Backend + Frontend) |
| [`status_handoff_smoke.py`](#status_handoff_smokepy) | Python | Automatischer Live-Regressionstest |
| [`hardware_e2e_matrix.ps1`](#hardware_e2e_matrixps1) | PowerShell | Hardware-Validierung mit Report |
| [`rpc_test_console.py`](#rpc_test_consolepy) | Python | Interaktive JSON-RPC-Konsole |
| [`parse_slim.py`](#parse_slimpy) | Python | Slimproto-Traffic aus Wireshark dekodieren |

---

## `dev.ps1`

Startet Backend (Python, Port 9000) und Frontend (Svelte, Port 5173) in separaten
PowerShell-Fenstern und öffnet den Browser.

### Wann nutzen

Täglicher Entwicklungsstart. Erspart zwei Terminals manuell aufzumachen.

### Aufruf

```powershell
.\scripts\dev.ps1
```

### Voraussetzungen

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
cd web-ui && npm install
```

---

## `status_handoff_smoke.py`

Automatischer Live-Smoketest gegen die JSON-RPC-API. Pollt den Player-Status
über eine konfigurierbare Dauer und prüft:

- **Backstep-Detection:** `time` darf auf demselben Track/Generation nicht rückwärts springen
- **Stall-Detection:** Progress darf nicht länger als 6s stehen bleiben (bei `mode=play`)
- **Duration-Overflow:** `time` darf `duration` nicht überschreiten
- **Index-Increment:** Playlist-Index muss bei Track-Wechsel um +1 steigen (optional)
- **Mode-Check:** `mode` muss mindestens einmal `play` erreichen (optional)

Exitcode 0 = PASS, 1 = FAIL.

### Wann nutzen

Nach Änderungen an: Playlist, Seek, Streaming, Status, Cometd, Prefetch/Gapless.
Entspricht **Teststufe C** aus [`docs/OPERATIONS.md`](../docs/OPERATIONS.md).

### Aufruf

```powershell
# Einfach: Album 3 abspielen, 35s beobachten
python scripts/status_handoff_smoke.py --album-id 3 --seconds 35 --require-play

# Streng: 2 Track-Transitions und Index-Check verlangen
python scripts/status_handoff_smoke.py --album-id 3 --seconds 90 --require-play --require-transitions 2 --strict-index-increment

# Automatisch erstes Album nehmen
python scripts/status_handoff_smoke.py --autoplay-first-album --seconds 60

# Bestimmten Player ansprechen
python scripts/status_handoff_smoke.py --player-id 00:04:20:26:84:ae --album-id 3 --seconds 45
```

### Wichtige Parameter

| Parameter | Default | Beschreibung |
|---|---|---|
| `--base-url` | `http://127.0.0.1:9000` | Server-URL |
| `--player-id` | Auto-Detect | Player-MAC oder Name |
| `--album-id` | — | Album laden vor dem Polling |
| `--seconds` | 90 | Polling-Dauer |
| `--poll-interval` | 1.0 | Sekunden zwischen Polls |
| `--require-play` | aus | FAIL wenn nie `mode=play` |
| `--require-transitions` | 0 | Mindestanzahl Track-Wechsel |
| `--strict-index-increment` | aus | Index muss bei Wechsel +1 sein |
| `--backstep-tolerance` | 0.35 | Erlaubter Rücksprung in Sekunden |
| `--stall-seconds` | 6.0 | Max. erlaubte Stillstandszeit |

---

## `hardware_e2e_matrix.ps1`

Reproduzierbare Hardware-E2E-Validierung. Setzt Playback-Preferences
(Crossfade, ReplayGain, etc.) auf einem echten Gerät, queued Tracks,
pollt den Status und erzeugt einen strukturierten Report.

**Ausgabe:** JSON + Markdown nach `artifacts/hardware-e2e/`.

### Wann nutzen

Zum Validieren von Gapless, Crossfade und ReplayGain auf echter Hardware
(Squeezebox Radio, Touch, Boom). Entspricht **Teststufe E** (Hardware-E2E).

### Aufruf

```powershell
# Squeezebox Radio: Crossfade + ReplayGain, 3 Tracks, 120s Polling
.\scripts\hardware_e2e_matrix.ps1 `
    -DeviceLabel "Radio" `
    -PlayerId "00:04:20:26:84:ae" `
    -TrackIds 154,155,156 `
    -PollSeconds 120 `
    -PollIntervalSeconds 0.5 `
    -TransitionType 1 `
    -TransitionDuration 7 `
    -ReplayGainMode 1

# Nur Status beobachten, Queue nicht ändern
.\scripts\hardware_e2e_matrix.ps1 `
    -PlayerId "00:04:20:26:84:ae" `
    -SkipQueueSetup `
    -PollSeconds 60

# Dry-Run (zeigt Requests ohne sie abzusetzen)
.\scripts\hardware_e2e_matrix.ps1 -PlayerId "..." -TrackIds 1,2,3 -DryRun
```

### Wichtige Parameter

| Parameter | Default | Beschreibung |
|---|---|---|
| `-ServerUrl` | `http://localhost:9000/jsonrpc.js` | JSON-RPC Endpoint |
| `-PlayerId` | Auto-Detect | Player-MAC (Pflicht bei mehreren Playern) |
| `-DeviceLabel` | `"device"` | Label für Report-Dateinamen |
| `-TrackIds` | — | Komma-getrennte Track-IDs für Queue |
| `-PollSeconds` | 90 | Polling-Dauer |
| `-PollIntervalSeconds` | 0.5 | Polling-Intervall |
| `-TransitionType` | 1 | 0=keine, 1=Crossfade, 2=Fade In, 3=Fade Out, 4=Fade In+Out |
| `-TransitionDuration` | 7 | Crossfade-Dauer in Sekunden |
| `-ReplayGainMode` | 1 | 0=aus, 1=Track, 2=Album, 3=Smart |
| `-Volume` | — | Volume setzen (0–100) |
| `-SkipPrefSetup` | — | Preferences nicht ändern |
| `-SkipQueueSetup` | — | Queue nicht ändern |
| `-DryRun` | — | Nur anzeigen, nichts senden |

### Pass/Fail-Kriterien

- **PASS:** `backsteps_same_track = 0`, keine reproduzierbaren Audio-Lücken
- **FAIL:** Rücksprünge auf demselben Track, instabile Übergänge

---

## `rpc_test_console.py`

Interaktive JSON-RPC-Konsole mit drei Modi. Zeigt für jeden Request
das gesendete und empfangene JSON an.

### Modi

#### `cli` — Interaktive Kommandozeile

REPL mit Kurzkommandos für schnelles manuelles Debugging.

Verfügbare Befehle:
`players`, `use <id>`, `status`, `serverstatus`,
`play`, `pause`, `stop`, `next`, `prev`,
`seek <s>`, `volume <0-100>`, `mute`,
`loadalbum <id>`, `raw <json-array>`,
`scenario handoff [album_id] [seconds]`

```powershell
python scripts/rpc_test_console.py cli
python scripts/rpc_test_console.py cli --player-id 00:04:20:26:84:ae
```

#### `gui` — Tkinter-Oberfläche

Grafische Konsole mit Buttons für Playback-Steuerung und JSON-Trace-Log.

```powershell
python scripts/rpc_test_console.py gui
```

#### `scenario-handoff` — Einmal-Szenario

Pollt Status und prüft Track-Transitions. Funktional ähnlich zu
`status_handoff_smoke.py`, aber mit weniger Prüfungen (kein Backstep-,
kein Stall-Check). Für schnelle Einmal-Checks.

> **Hinweis:** Für umfassende Regressionstests besser
> `status_handoff_smoke.py` verwenden.

```powershell
python scripts/rpc_test_console.py scenario-handoff --album-id 3 --seconds 35 --require-transitions 2
```

### Wann nutzen

- **`cli`:** Manuelles API-Debugging, Befehle ausprobieren, JSON inspizieren
- **`gui`:** Schnelle visuelle Kontrolle mit Buttons
- **`scenario-handoff`:** Schneller Einmal-Check (für gründliche Tests → `status_handoff_smoke.py`)

---

## `parse_slim.py`

Dekodiert Slimproto-Netzwerkverkehr aus tshark-TSV-Exporten. Gibt alle
Messages (HELO, STAT, strm, audg, aude, etc.) menschenlesbar aus, mit
Track-Nummerierung und Event-Markierungen.

### Wann nutzen

Für Protokoll-Debugging, wenn man einen Wireshark-Mitschnitt analysieren will.
Nützlich bei Problemen mit Playback-Übergängen, Seek, Volume oder Player-Handshake.

### Aufruf

```powershell
# 1. Wireshark-Export erzeugen (einmalig):
tshark -r capture.pcapng -Y "tcp.port == 3483 && tcp.len > 0" `
    -T fields -e frame.number -e frame.time_relative -e ip.src -e tcp.payload `
    > slim_capture.txt

# 2. Dekodieren:
python scripts/parse_slim.py slim_capture.txt
```

### Ausgabe-Beispiel

```
  >>> NEW STREAM COMMAND >>>
    0.3s  [S->P] >>>  strm/s (START)  fmt=mp3  auto=1  [GET /stream.mp3?...] port=9000

================================================================================
  *** TRACK 1 STARTED ***
================================================================================
    1.2s  [P->S] <<<  STAT/STMs (STARTED)  buf=78%  out=12%  recv=245KB  elapsed=0s/0ms
    2.2s  [P->S] <<<  STAT/STMt (heartbeat)  buf=95%  out=34%  recv=512KB  elapsed=1s/1024ms
```

### Konfiguration

`SERVER_IP` am Anfang der Datei muss auf die eigene Server-IP angepasst werden
(bestimmt die Richtungserkennung Server→Player vs. Player→Server).