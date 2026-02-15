# Hardware Testing Runbook

This document describes **public, reproducible hardware tests** for Resonance, with a focus on
**bitmap/graphic displays** (Squeezebox2/SB3/Classic/Boom and similar). It is intended to be safe to
share and should not link to any local-only developer documentation.

---

## Scope

### Covered
- Bitmap display rendering (frame output, now-playing, overlays, screensaver)
- Metadata update pipeline on bitmap displays:
  - Track changes (playlist index changes)
  - ICY StreamTitle updates for live radio

### Not covered
- Touch/Radio/Jive/SqueezePlay UI (they render locally and primarily consume status via Cometd)
- Deep performance benchmarking
- RF/IR remote edge cases

---

## Prerequisites

### Hardware
- At least one of:
  - **Squeezebox2 / Squeezebox3 / Classic**
  - **Boom**
- Local network connectivity (player can discover/connect to the server)

### Software
- Resonance server running on a machine reachable by the player
- A browser for the Web UI (for easy playback control), optional but recommended

### Audio Content
- A small set of **local** library tracks (for deterministic track change testing)
- At least one **live radio** station that delivers ICY metadata (StreamTitle) at least occasionally

---

## Safety: Feature Flag for Bitmap Display Rendering

Bitmap display rendering is gated behind an environment variable and is **OFF by default** until
fully hardware-verified.

### Enable
Set:

- `RESONANCE_DISPLAY=1`

Then start Resonance normally.

### Disable
Unset the variable or set:

- `RESONANCE_DISPLAY=0`

and restart the server.

---

## Test Matrix (Recommended)

Run at minimum:

| Category | Local Track | Live Radio (ICY) |
|---|---:|---:|
| SB2/SB3/Classic | ✅ | ✅ |
| Boom | ✅ | ✅ |

---

## Quick Verification Checklist (10–15 minutes)

### A) Connection & Baseline
1. Start server with `RESONANCE_DISPLAY=1`.
2. Power on the player and connect to Resonance.
3. Confirm the display is not blank/garbled on idle (baseline render is stable).

Pass criteria:
- No flicker loop.
- No obviously corrupted frames (wrong orientation/garbled noise).

---

### B) Local Playback: Track Metadata & Progress
1. Start a **local** track.
2. Observe now-playing screen updates:
   - **Title**
   - **Artist**
   - **Album** (if shown by the renderer)
3. Let it play for ~5 seconds:
   - elapsed/progress should advance smoothly.

Pass criteria:
- Title/artist/album appear promptly after playback starts.
- Elapsed time advances and does not jump backwards.

---

### C) Track Change (Prev/Next) — Metadata Must Update Immediately
1. Press **Next** once.
2. Press **Previous** once.
3. Trigger a track change from the Web UI (skip/next).

Pass criteria:
- Display updates to the new track’s metadata without requiring a power cycle or menu navigation.
- No lingering metadata from the prior track.

---

### D) Pause / Unpause
1. Press pause.
2. Wait ~3 seconds.
3. Unpause.

Pass criteria:
- Pause indicator (or equivalent) appears.
- Elapsed/progress stops while paused and resumes after unpause.
- Metadata remains correct.

---

### E) Volume Overlay (showBriefly)
1. Change volume up/down several steps.
2. Observe the volume overlay.

Pass criteria:
- Overlay appears and disappears automatically.
- Overlay is **not empty** (no blank popup / empty frame).
- Overlay does not permanently “stick” over now-playing.

---

### F) Live Radio: ICY StreamTitle Update Push
1. Start a **live radio** station.
2. Wait for an ICY title change (may take a minute depending on station).
3. When the station updates title/artist, confirm the display updates.

Pass criteria:
- Display updates within a reasonable time after the ICY change.
- If ICY is in `"Artist - Title"` format (single separator), it displays split cleanly.
- If ICY has no separator or multiple separators, it is shown as a whole title (no broken split).

Notes:
- Not all stations send ICY frequently; choose one known to update often.

---

### G) Screensaver / Idle Behaviour
1. Stop playback or pause and leave the player idle.
2. Wait for the screensaver timeout.
3. Wake the player by changing volume or resuming playback.

Pass criteria:
- Screensaver activates after the configured idle timeout.
- Clock screensaver refreshes periodically (e.g. 1×/s).
- Wake triggers correctly and returns to the appropriate view.

---

## Deeper Diagnostics (If Something Fails)

When you observe a failure, capture:

1. Player model, firmware version (if known)
2. Whether issue reproduces on another bitmap device (SB2 vs Boom)
3. Repro steps:
   - local track vs radio
   - specific station URL/name if radio
4. Time of failure and any obvious pattern (e.g. “only after pause”, “only after next”, etc.)
5. Whether disabling the feature flag (`RESONANCE_DISPLAY=0`) makes the issue disappear

---

## Known Failure Modes to Watch For

- **Wrong frame size**: display shows scrambling/rolling corruption (often strict per model)
- **Wrong bitmap orientation**: mirrored/rotated text (column-major vs row-major issues)
- **Metadata not updating**:
  - track changes show old title/artist
  - ICY changes never appear
- **Empty overlay popup** on volume/showBriefly
- **Screensaver never wakes** or wakes but remains blank

---

## Exit Criteria (When to Turn It On by Default)

Bitmap display rendering can be considered hardware-verified when:

- SB2/SB3/Classic: all checklist sections A–G pass reliably
- Boom: all checklist sections A–G pass reliably
- No regressions observed across multiple sessions/restarts
- ICY updates are confirmed on at least one station with frequent StreamTitle changes

Until then, keep `RESONANCE_DISPLAY` default-off and rely on explicit enablement during testing.