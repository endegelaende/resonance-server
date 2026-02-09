"""
Playback Command Handlers.

Handles player control commands:
- play: Start/resume playback
- pause: Pause playback (toggle or explicit)
- stop: Stop playback
- mode: Query or set playback mode
- power: Query or set player power state
- mixer: Volume and audio controls
- button: Simulate remote control buttons
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from typing import Any

from resonance.core.events import PlayerStatusEvent, event_bus
from resonance.web.handlers import CommandContext

logger = logging.getLogger(__name__)

_SLEEP_TIMER_TASKS: dict[str, asyncio.Task[None]] = {}
_SLEEP_DEADLINES: dict[str, float] = {}
_SLEEP_GENERATIONS: dict[str, int] = {}
_MIXER_PREFS_LOCK = asyncio.Lock()
_MIXER_PREFS: dict[str, dict[str, int]] = {}


def _remaining_sleep_seconds(player_id: str) -> float:
    """Return remaining sleep duration in seconds for a player."""
    deadline = _SLEEP_DEADLINES.get(player_id)
    if deadline is None:
        return 0.0
    remaining = deadline - time.time()
    return remaining if remaining > 0 else 0.0


async def _cancel_sleep_timer(player_id: str) -> None:
    """Cancel and clear an active sleep timer for a player."""
    _SLEEP_DEADLINES.pop(player_id, None)
    task = _SLEEP_TIMER_TASKS.pop(player_id, None)
    if task is not None and not task.done():
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def _sleep_timer_worker(player_id: str, generation: int, duration_s: float, ctx: CommandContext) -> None:
    """Wait for timer expiry, then stop playback and power off audio."""
    try:
        await asyncio.sleep(duration_s)
    except asyncio.CancelledError:
        return

    if _SLEEP_GENERATIONS.get(player_id) != generation:
        return

    _SLEEP_DEADLINES.pop(player_id, None)
    _SLEEP_TIMER_TASKS.pop(player_id, None)
    player = await ctx.player_registry.get_by_mac(player_id)
    if player is None:
        return

    try:
        await player.stop()
        if hasattr(player, "set_audio_enable"):
            await player.set_audio_enable(False)
        await event_bus.publish(
            PlayerStatusEvent(
                player_id=player_id,
                state=(player.status.state.value if hasattr(player.status.state, "value") else str(player.status.state)),
                volume=player.status.volume,
                muted=getattr(player.status, "muted", False),
            )
        )
    except Exception:
        logger.exception("Sleep timer expiry handling failed for %s", player_id)



def _player_state_name(player: Any) -> str:
    """Return the uppercase player state name with a safe STOPPED fallback."""
    status = getattr(player, "status", None)
    state = getattr(status, "state", None)
    name = getattr(state, "name", None)
    if isinstance(name, str):
        return name
    return "STOPPED"


async def _start_playlist_track_if_stopped(ctx: CommandContext, player: Any) -> bool:
    """
    LMS-like start-from-queue helper.

    Returns True when playback was started by streaming the current playlist track.
    """
    playlist = None
    if ctx.playlist_manager is not None:
        playlist = ctx.playlist_manager.get(ctx.player_id)

    if playlist is None or len(playlist) == 0:
        return False

    if _player_state_name(player) not in ("STOPPED", "DISCONNECTED"):
        return False

    # Avoid top-level import to prevent circular imports.
    from resonance.web.handlers.playlist import _start_track_stream

    track = playlist.play(playlist.current_index)
    if track is None:
        return False

    logger.info(
        "[playback] STOPPED -> starting stream from playlist",
        extra={
            "player_id": ctx.player_id,
            "index": playlist.current_index,
            "track_id": getattr(track, "id", None),
        },
    )
    await _start_track_stream(ctx, player, track)
    return True


async def cmd_play(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """
    Handle 'play' command.

    LMS-like behavior:
    - If the player is STOPPED and there is a non-empty playlist, start streaming
      the current playlist item (queue playback).
    - Otherwise, resume/unpause via player.play().
    """
    if ctx.player_id == "-":
        return {"error": "No player specified"}

    player = await ctx.player_registry.get_by_mac(ctx.player_id)
    if player is None:
        return {"error": "Player not found"}

    try:
        if await _start_playlist_track_if_stopped(ctx, player):
            return {}
    except Exception:
        logger.exception("[cmd_play] Failed to start from playlist, falling back to resume")

    await player.play()
    return {}


async def cmd_pause(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """
    Handle 'pause' command.

    Pauses playback. Optional parameter:
    - 0: Resume (unpause)
    - 1: Pause
    - (none): Toggle
    """
    if ctx.player_id == "-":
        return {"error": "No player specified"}

    player = await ctx.player_registry.get_by_mac(ctx.player_id)
    if player is None:
        return {"error": "Player not found"}

    # Check for explicit pause/unpause value
    if len(params) >= 2:
        try:
            pause_val = int(params[1])
            if pause_val == 0:
                try:
                    if await _start_playlist_track_if_stopped(ctx, player):
                        return {}
                except Exception:
                    logger.exception("[cmd_pause] Failed to start from playlist, falling back to resume")

                # Keep STOPPED state stable when there is nothing to resume.
                if _player_state_name(player) in ("STOPPED", "DISCONNECTED", "CONNECTED"):
                    logger.debug(
                        "[cmd_pause] Ignoring pause 0 for stopped/disconnected player",
                        extra={"player_id": ctx.player_id},
                    )
                    return {}

                await player.play()  # Unpause
            else:
                # Pausing while stopped should remain a no-op.
                if _player_state_name(player) in ("STOPPED", "DISCONNECTED", "CONNECTED"):
                    return {}
                await player.pause()
        except (ValueError, TypeError):
            # Invalid value, just toggle
            await player.pause()
    else:
        # Toggle pause
        await player.pause()

    return {}


async def cmd_stop(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """
    Handle 'stop' command.

    Stops playback on the player.
    """
    if ctx.player_id == "-":
        return {"error": "No player specified"}

    player = await ctx.player_registry.get_by_mac(ctx.player_id)
    if player is None:
        return {"error": "Player not found"}

    await player.stop()

    return {}


async def cmd_mode(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """
    Handle 'mode' command.

    Query or set the playback mode.
    - mode ? : Returns current mode
    - mode play : Start playing
    - mode pause : Pause
    - mode stop : Stop
    """
    if ctx.player_id == "-":
        return {"_mode": "stop"}

    player = await ctx.player_registry.get_by_mac(ctx.player_id)
    if player is None:
        return {"_mode": "stop"}

    # Query mode
    if len(params) < 2 or params[1] == "?":
        status = player.status
        state_to_mode = {
            "PLAYING": "play",
            "PAUSED": "pause",
            "STOPPED": "stop",
            "DISCONNECTED": "stop",
            "BUFFERING": "play",
        }
        state_name = status.state.name if hasattr(status.state, "name") else "STOPPED"
        return {"_mode": state_to_mode.get(state_name, "stop")}

    # Set mode
    new_mode = params[1].lower()
    if new_mode == "play":
        await player.play()
    elif new_mode == "pause":
        await player.pause()
    elif new_mode == "stop":
        await player.stop()

    return {"_mode": new_mode}


async def cmd_power(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """
    Handle 'power' command.

    Query or set the player power state.
    - power ? : Returns current power state
    - power 0 : Power off
    - power 1 : Power on
    """
    if ctx.player_id == "-":
        return {"_power": 0}

    player = await ctx.player_registry.get_by_mac(ctx.player_id)
    if player is None:
        return {"_power": 0}

    # Query power
    if len(params) < 2 or params[1] == "?":
        # Players are always "on" when connected
        return {"_power": 1}

    # Set power
    try:
        power_val = int(params[1])
    except (ValueError, TypeError):
        return {"_power": 1}

    caps = getattr(player, "device_capabilities", None)
    can_power_off = bool(getattr(caps, "can_power_off", True))

    if power_val == 0:
        if not can_power_off:
            return {"error": "Player does not support power off", "_power": 1}

        # Power off - stop playback and disable audio outputs
        await player.stop()
        await player.set_audio_enable(False)
    else:
        # Power on - enable audio outputs
        await player.set_audio_enable(True)

    return {"_power": power_val}


async def cmd_sleep(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """
    Handle 'sleep' command.

    LMS-compatible behavior:
    - sleep ?     -> return remaining seconds
    - sleep <sec> -> set/cancel sleep timer
    """
    if ctx.player_id == "-":
        return {"error": "No player specified"}

    player = await ctx.player_registry.get_by_mac(ctx.player_id)
    if player is None:
        return {"error": "Player not found"}

    # Query current sleep timer.
    if len(params) < 2 or params[1] == "?":
        return {"_sleep": _remaining_sleep_seconds(ctx.player_id)}

    raw_value = params[1]
    try:
        requested_seconds = float(raw_value)
    except (TypeError, ValueError):
        return {"error": f"Invalid sleep value: {raw_value}"}

    # Bump generation so stale timer tasks cannot fire after a re-schedule.
    generation = _SLEEP_GENERATIONS.get(ctx.player_id, 0) + 1
    _SLEEP_GENERATIONS[ctx.player_id] = generation

    await _cancel_sleep_timer(ctx.player_id)

    if requested_seconds <= 0:
        return {"_sleep": 0}

    _SLEEP_DEADLINES[ctx.player_id] = time.time() + requested_seconds
    timer_task = asyncio.create_task(
        _sleep_timer_worker(ctx.player_id, generation, requested_seconds, ctx)
    )
    _SLEEP_TIMER_TASKS[ctx.player_id] = timer_task

    return {"_sleep": _remaining_sleep_seconds(ctx.player_id)}

async def cmd_mixer(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """
    Handle 'mixer' command.

    Controls volume and other audio settings.
    - mixer volume ? : Query volume
    - mixer volume <n> : Set absolute volume (0-100)
    - mixer volume +<n> : Increase volume
    - mixer volume -<n> : Decrease volume
    - mixer volume <n> seq_no:<n> : Set volume with sequence number (LMS compat)
    - mixer muting ? : Query mute state
    - mixer muting 0/1 : Set mute state
    - mixer muting toggle : Toggle mute

    The seq_no parameter is used by SqueezePlay/Jive devices (Radio, Touch, etc.)
    to synchronize volume changes. We echo it back in the audg frame so the
    player can discard stale volume updates.
    """
    if ctx.player_id == "-":
        return {"error": "No player specified"}

    player = await ctx.player_registry.get_by_mac(ctx.player_id)
    if player is None:
        return {"error": "Player not found"}

    if len(params) < 2:
        return {"error": "Missing mixer subcommand"}

    # Parse seq_no from params (can be "seq_no:123" anywhere in params)
    seq_no: int | None = None
    for param in params:
        if isinstance(param, str) and param.startswith("seq_no:"):
            try:
                seq_no = int(param.split(":", 1)[1])
            except (ValueError, IndexError):
                pass
            break

    subcommand = params[1].lower()

    if subcommand == "volume":
        current_volume = player.status.volume

        # Query volume
        if len(params) < 3 or params[2] == "?":
            return {"_volume": current_volume}

        # Set volume
        volume_str = str(params[2])

        try:
            if volume_str.startswith("+"):
                # Relative increase
                delta = int(volume_str[1:])
                new_volume = min(100, current_volume + delta)
            elif volume_str.startswith("-"):
                # Relative decrease
                delta = int(volume_str[1:])
                new_volume = max(0, current_volume - delta)
            else:
                # Absolute value
                new_volume = int(volume_str)
                new_volume = max(0, min(100, new_volume))

            # Pass seq_no to set_volume for LMS compatibility
            await player.set_volume(new_volume, seq_no=seq_no)

            # Publish status event so cometd re-executes slim subscriptions.
            # Without this, other clients (Radio display, web-ui) don't see
            # the volume change until the next heartbeat.
            await event_bus.publish(
                PlayerStatusEvent(
                    player_id=ctx.player_id,
                    state=player.status.state.value if hasattr(player.status.state, "value") else str(player.status.state),
                    volume=new_volume,
                    muted=player.status.muted,
                )
            )

            return {"_volume": new_volume}
        except (ValueError, TypeError):
            return {"error": f"Invalid volume value: {volume_str}"}

    elif subcommand == "muting":
        # Query mute state
        if len(params) < 3 or params[2] == "?":
            muted = getattr(player.status, "muted", False)
            return {"_muting": 1 if muted else 0}

        # Set mute state
        mute_val = params[2]
        if mute_val == "toggle":
            current_muted = getattr(player.status, "muted", False)
            # Toggle mute (if supported)
            if hasattr(player, "set_mute"):
                await player.set_mute(not current_muted)
            return {"_muting": 0 if current_muted else 1}
        else:
            try:
                mute_int = int(mute_val)
                if hasattr(player, "set_mute"):
                    await player.set_mute(mute_int == 1)
                return {"_muting": mute_int}
            except (ValueError, TypeError):
                return {"error": f"Invalid muting value: {mute_val}"}

    elif subcommand in {"bass", "treble", "pitch", "stereoxl"}:
        from resonance.player.capabilities import get_device_capabilities
        from resonance.player.client import DeviceType

        caps = getattr(player, "device_capabilities", get_device_capabilities(DeviceType.UNKNOWN))

        ranges: dict[str, tuple[int, int]] = {
            "bass": (caps.min_bass, caps.max_bass),
            "treble": (caps.min_treble, caps.max_treble),
            # LMS Client.pm defaults: minPitch=maxPitch=100.
            "pitch": (100, 100),
            "stereoxl": (caps.min_xl, caps.max_xl),
        }
        supports: dict[str, bool] = {
            "bass": caps.has_bass,
            "treble": caps.has_treble,
            "pitch": True,
            "stereoxl": caps.has_stereo_xl,
        }

        min_value, max_value = ranges[subcommand]
        default_value = (min_value + max_value) // 2

        raw_value: str | None = None
        if len(params) >= 3:
            candidate = str(params[2])
            if candidate != "?":
                raw_value = candidate
        if raw_value is None:
            for param in params[2:]:
                if not isinstance(param, str):
                    continue
                if param.startswith("value:"):
                    raw_value = param.split(":", 1)[1]
                    break

        async with _MIXER_PREFS_LOCK:
            player_prefs = _MIXER_PREFS.setdefault(ctx.player_id, {})
            current_value = player_prefs.get(subcommand, default_value)

            # Keep query compatibility (returns fixed midpoint), but reject
            # mutating unsupported controls for this device.
            if raw_value is None:
                return {f"_{subcommand}": current_value}

            if not supports[subcommand]:
                return {"error": f"Mixer subcommand '{subcommand}' not supported for this device"}

            parse_value = raw_value
            if parse_value.startswith("value:"):
                parse_value = parse_value.split(":", 1)[1]

            try:
                if parse_value.startswith("+") or parse_value.startswith("-"):
                    new_value = current_value + int(parse_value)
                else:
                    new_value = int(parse_value)
            except (TypeError, ValueError):
                return {"error": f"Invalid {subcommand} value: {raw_value}"}

            if new_value < min_value:
                new_value = min_value
            if new_value > max_value:
                new_value = max_value

            player_prefs[subcommand] = new_value
            return {f"_{subcommand}": new_value}
    return {"error": f"Unknown mixer subcommand: {subcommand}"}


async def cmd_button(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """
    Handle 'button' command.

    Simulates remote control button presses.
    Common buttons: play, pause, stop, fwd, rew, volup, voldown, mute
    """
    if ctx.player_id == "-":
        return {"error": "No player specified"}

    player = await ctx.player_registry.get_by_mac(ctx.player_id)
    if player is None:
        return {"error": "Player not found"}

    if len(params) < 2:
        return {"error": "Missing button name"}

    button = params[1].lower()

    # Map button names to actions
    if button == "play":
        await player.play()
    elif button == "pause":
        await player.pause()
    elif button == "stop":
        await player.stop()
    elif button in ("fwd", "jump_fwd", "fwd.single"):
        # Skip forward - use _start_track_stream for proper transcoding
        if ctx.playlist_manager is not None:
            playlist = ctx.playlist_manager.get(ctx.player_id)
            if playlist is not None:
                next_track = playlist.next()
                if next_track is not None:
                    from resonance.web.handlers.playlist import _start_track_stream
                    await _start_track_stream(ctx, player, next_track)
    elif button in ("rew", "jump_rew", "rew.single"):
        # Skip backward - use _start_track_stream for proper transcoding
        if ctx.playlist_manager is not None:
            playlist = ctx.playlist_manager.get(ctx.player_id)
            if playlist is not None:
                prev_track = playlist.previous()
                if prev_track is not None:
                    from resonance.web.handlers.playlist import _start_track_stream
                    await _start_track_stream(ctx, player, prev_track)
    elif button == "volup":
        current = player.status.volume
        await player.set_volume(min(100, current + 5))
    elif button == "voldown":
        current = player.status.volume
        await player.set_volume(max(0, current - 5))
    elif button == "mute":
        if hasattr(player, "set_mute"):
            current_muted = getattr(player.status, "muted", False)
            await player.set_mute(not current_muted)

    return {}





