# CLAUDE.md — Resonance Server Development Notes

This file documents critical issues, architecture decisions, and debugging
knowledge for the Resonance server. It serves as institutional memory so
that known problems are not re-investigated from scratch.

---

## Build & Run

```bash
# Docker (production)
cd /etc/komodo/stacks/resonance-server
docker compose build
docker compose down && docker compose up -d
docker logs -f resonance-server

# Update from git + rebuild
/root/scripts/update-resonance-server.sh          # normal (skip if no changes)
/root/scripts/update-resonance-server.sh --force   # rebuild even if up to date

# Server endpoints
# Web UI:      http://192.168.1.35:9000
# Slimproto:   port 3483 (player control)
# CLI:         port 9090
```

---

## Critical Known Issue: Squeezebox Radio UUID Blacklisting

### Problem

The Squeezebox Radio's JiveLite firmware **blacklists server UUIDs** of
servers it previously failed to connect to via CometD/HTTP. Once blacklisted,
JiveLite will:

- ✅ Connect via Slimproto (audio works)
- ✅ Respond to TLV discovery (finds the server)
- ❌ **Never attempt HTTP/CometD connection** (no NowPlaying, no cover, no menu)

This manifests as: audio plays on the Radio when triggered from the Web UI,
but the Radio's display shows nothing — no track info, no cover art, no
playlist. The Radio may show a "connecting" screen or sit idle in a discovery
loop (TLV requests every 10 seconds without ever making an HTTP request).

### Root Cause

JiveLite stores a persistent blacklist of server UUIDs on the Radio's flash
storage. If a CometD session fails (e.g., streaming connection timeout,
server crash, malformed response), JiveLite adds the server's UUID to this
blacklist. On subsequent boots, JiveLite skips HTTP/CometD for blacklisted
UUIDs entirely — even though Slimproto (a separate protocol) still works.

### Diagnosis

Check the Docker logs for HTTP requests from the Radio's IP:

```bash
docker logs resonance-server 2>&1 | grep 'HTTP.*192.168.1.69'
```

If you see **zero HTTP requests** from the Radio but TLV discovery is
happening, the UUID is blacklisted:

```bash
docker logs resonance-server 2>&1 | grep '192.168.1.69' | grep -v TLV
# Expected: only slimproto connections, no HTTP
```

### Fix

1. **Generate a new UUID:**
   ```bash
   python3 -c "import uuid; print(uuid.uuid4())"
   ```

2. **Write it to the server_uuid file:**
   ```bash
   echo '<new-uuid>' > /etc/komodo/stacks/resonance-server/cache/server_uuid
   ```

3. **Restart the container:**
   ```bash
   cd /etc/komodo/stacks/resonance-server
   docker compose down && docker compose up -d
   ```

4. **Power-cycle the Radio** (unplug for 10 seconds, not just standby).
   JiveLite reloads its blacklist on cold boot.

### Prevention

- The `server_uuid` file is mounted into the container read-only
  (`docker-compose.yml`) to ensure consistent identity across rebuilds.
- **Never let the CometD streaming connection fail silently.** The streaming
  generator in `resonance/web/routes/cometd.py` sends a reconnect-advice
  frame before closing to tell JiveLite to retry instead of blacklisting.
- Keep `STREAMING_TIMEOUT` generous (currently 3600s = 1 hour).

---

## Critical Known Issue: CometD Race Condition on Boot (players_loop empty)

### Problem

When the Squeezebox Radio boots, two independent connections race:

1. **Slimproto** (port 3483) — registers the player in `PlayerRegistry`
2. **CometD/HTTP** (port 9000) — JiveLite subscribes to `serverstatus`

If CometD connects **before** Slimproto registers the player, the initial
`serverstatus` response contains `"players_loop": []` (empty). JiveLite
sees no players → never subscribes to `playerstatus` → no NowPlaying, no
cover, no track info on the display.

### Root Cause (fixed 2026-03-25)

`PlayerConnectedEvent` and `PlayerDisconnectedEvent` were only delivered
on the `/players` channel (for the web UI). They did **not** trigger
re-execution of global slim subscriptions like `serverstatus`.

So when the player registered via Slimproto 0.5–2 seconds after CometD
connected, JiveLite's `serverstatus` subscription was never re-executed
with the updated `players_loop`.

### Fix

In `resonance/web/cometd.py` → `handle_event()`:

```python
elif isinstance(event, PlayerConnectedEvent):
    channel = "/players"
    await self.deliver_event(channel, {"event": "connected", ...})

    # Re-execute global subscriptions (serverstatus) so JiveLite
    # sees the updated players_loop.
    self._schedule_debounced_reexec("", 0.5)
```

Calling `_schedule_debounced_reexec("")` with an empty player_id matches
all global subscriptions (`sub.player_id == ""`), which includes
`serverstatus`. JiveLite then receives the updated `players_loop`,
identifies its player, and proceeds to subscribe to `playerstatus`.

The same fix applies to `PlayerDisconnectedEvent`.

### How to verify it works

After a Radio reboot, the logs should show this sequence:

```
Player connected: 00:04:20:26:84:ae — scheduling global subscription reexec in 0.5s
Reexec: firing 2 slim subscription(s) for player
Reexec: pushed result to client XXXX on /XXXX/slim/serverstatus
...
CometD IN: channel=/slim/subscribe ... ['status', '-', 10, 'menu:menu', ...]
   → response: /XXXX/slim/playerstatus/00:04:20:26:84:ae
Client XXXX stored slim subscription: player=00:04:20:26:84:ae cmd=['status', ...]
```

The critical line is the `playerstatus` subscription. If it's missing,
JiveLite never gets NowPlaying updates.

---

## CometD / JiveLite Connection Flow

This is the complete message sequence when a Squeezebox Radio connects.
Understanding this flow is essential for debugging display issues.

```
Radio                              Resonance Server
  │                                      │
  ├──── Slimproto TCP connect ──────────►│  (port 3483, audio control)
  │                                      │  → PlayerConnectedEvent
  │                                      │  → triggers serverstatus reexec
  │                                      │
  ├──── TLV Discovery (UDP) ───────────►│  → responds with IPAD, JSON port
  │◄─── TLV Response ──────────────────│
  │                                      │
  ├──── POST /cometd ──────────────────►│  /meta/handshake
  │◄─── {clientId: "abc123"} ──────────│
  │                                      │
  ├──── POST /cometd ──────────────────►│  /meta/connect (streaming)
  │     (connection stays open)          │  /meta/subscribe
  │◄─── StreamingResponse begins ──────│
  │                                      │
  ├──── POST /cometd ──────────────────►│  /slim/subscribe serverstatus
  │     (separate POST)                  │  /slim/subscribe firmwareupgrade
  │◄─── serverstatus data (streamed) ──│  → players_loop with Radio
  │                                      │
  ├──── POST /cometd ──────────────────►│  /slim/subscribe menustatus
  │◄─── menu items (streamed) ─────────│
  │                                      │
  ├──── POST /cometd ──────────────────►│  /slim/subscribe date
  │◄─── date/time (streamed) ──────────│
  │                                      │
  ├──── POST /cometd ──────────────────►│  /slim/subscribe playerstatus ← KEY!
  │     (status, -, 10, menu:menu,       │  /slim/subscribe displaystatus
  │      useContextMenu:1, subscribe:600)│
  │◄─── full player status (streamed) ─│  → track, playlist, artwork URLs
  │                                      │
  │   ... streaming connection open ...  │
  │◄─── re-executed status on events ──│  → every play/pause/skip/etc.
  │                                      │
```

**Key subscriptions stored by JiveLite:**

| Subscription        | Channel pattern                              | Purpose                     |
|---------------------|----------------------------------------------|-----------------------------|
| `serverstatus`      | `/<clientId>/slim/serverstatus`               | Player list, server info    |
| `firmwareupgrade`   | `/<clientId>/slim/firmwarestatus`             | Firmware check              |
| `menustatus`        | `/<clientId>/slim/menustatus/<mac>`           | Menu changes                |
| **`playerstatus`**  | `/<clientId>/slim/playerstatus/<mac>`         | **NowPlaying, cover, playlist** |
| `displaystatus`     | `/<clientId>/slim/displaystatus/<mac>`        | Brief display messages      |
| `date`              | `/<clientId>/slim/datestatus/<mac>`           | Clock/timezone              |

---

## Debugging CometD Issues

### Essential log commands

```bash
# All CometD messages from the Radio
docker logs resonance-server 2>&1 | grep 'CometD IN:'

# Check if playerstatus subscription exists
docker logs resonance-server 2>&1 | grep 'stored slim.*playerstatus'

# Check re-execution results
docker logs resonance-server 2>&1 | grep 'Reexec:'

# All HTTP requests from the Radio (192.168.1.69)
docker logs resonance-server 2>&1 | grep 'HTTP.*192.168.1.69'

# Full flow for a specific CometD client
docker logs resonance-server 2>&1 | grep '<clientId>'

# Watch live
docker logs -f resonance-server 2>&1 | grep --line-buffered \
  'CometD IN:\|Streaming.*event\|stored slim\|Reexec\|Player connected'
```

### What to look for

| Symptom | Cause | Fix |
|---------|-------|-----|
| No HTTP requests from Radio IP | UUID blacklisted | New UUID + power-cycle Radio |
| `players_loop: []` in serverstatus | Race condition (CometD before slimproto) | Fixed: PlayerConnectedEvent triggers serverstatus reexec |
| `Reexec: no matching slim subscriptions (clients: 0)` | No CometD client connected | Check UUID blacklist or network |
| `Reexec: no matching slim subscriptions (clients: 1, total_subs: 2)` | Only serverstatus/firmware subscribed, no playerstatus | JiveLite didn't see player in serverstatus |
| `Streaming connection ended` after 1 hour | Normal: `STREAMING_TIMEOUT` reached | Radio should auto-reconnect |

---

## Docker Setup

```yaml
# docker-compose.yml key points:
network_mode: host          # Required for UDP broadcast discovery
volumes:
  - ./cache/server_uuid:/app/cache/server_uuid:ro  # Consistent UUID!
```

- `network_mode: host` is mandatory — Docker bridge mode blocks UDP
  broadcasts on port 3483, so Squeezebox players can't discover the server.
- The `server_uuid` file is bind-mounted read-only from the host to ensure
  the UUID survives container rebuilds. **Changing this UUID will cause all
  Squeezebox Radios to treat it as a new server** (which can be useful to
  escape blacklisting, but means JiveLite loses its connection state).

### Server paths inside container

| Path | Content |
|------|---------|
| `/app/cache/server_uuid` | Server UUID (mounted from host) |
| `/app/data/` | Playlists, alarms, player prefs, plugin data |
| `/app/cache/` | SQLite database, artwork cache |
| `/music/` | Music library (mounted read-only) |

---

## Key Source Files

| File | Purpose |
|------|---------|
| `resonance/web/cometd.py` | CometD session management, slim subscriptions, re-execution engine |
| `resonance/web/routes/cometd.py` | HTTP route handler for `/cometd`, streaming generator |
| `resonance/web/handlers/status.py` | `serverstatus`, `status` command handlers |
| `resonance/web/jsonrpc_helpers.py` | `build_player_item()` — builds players_loop entries |
| `resonance/protocol/slimproto.py` | Slimproto protocol, player connection handling |
| `resonance/web/server.py` | FastAPI app setup, middleware, uvicorn config |
| `resonance/core/events.py` | Event types (PlayerStatusEvent, PlayerConnectedEvent, etc.) |

---

## LMS Compatibility Notes

Resonance emulates Logitech Media Server (LMS) for Squeezebox hardware.
The reference implementation is in `slimserver-public-9.2/`.

- **Version string:** `7.999.999` — signals "modern LMS" to JiveLite
- **CometD re-execution model:** When a player's state changes, all slim
  subscriptions targeting that player are re-executed and the full result
  is pushed on the streaming connection. This is how JiveLite gets updates.
- **Debounce timings** mirror LMS's `statusQuery_filter()`:
  - Default: 0.3s (command bursts)
  - Stop: 1.0s (stop often followed by play)
  - Jump/Load: 1.5s (newsong follows)