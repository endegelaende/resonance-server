"""
Alarm runtime scheduler for Resonance (LMS compatibility subset).

This module provides a small background scheduler that watches the in-memory
alarm definitions from `resonance.web.handlers.alarm` and triggers alarms at
the correct *local* time.

Important notes / current scope:
- Alarms are currently stored in-memory in `resonance.web.handlers.alarm`.
  This runtime reads them under that module's lock.
- `AlarmEntry.time` is interpreted as "seconds since local midnight".
- `AlarmEntry.dow` uses LMS semantics: 0=Sunday, 1=Monday, ... 6=Saturday.
- When an alarm fires we:
    1) set the player's volume to the alarm volume (via JSON-RPC command path)
    2) invoke `play` (which starts from queue if STOPPED and playlist exists)
  URL/shuffle modes are not yet interpreted here (minimal viable alarm runtime).

Debounce/duplicate protection:
- We keep a per-player/per-alarm "last fired local date" cache to prevent
  double-firing if the scheduler loop wakes multiple times in the due window.
- For one-shot alarms (`repeat == False`), we disable them after firing.

This is designed to be integrated by `ResonanceServer`:
- create `AlarmRuntime(jsonrpc_execute=..., player_registry=...)`
- call `await alarm_runtime.start()` on server start
- call `await alarm_runtime.stop()` on server stop
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# How often we re-check alarms even if nothing is due soon.
# (Also serves as a safety net against clock changes.)
MAX_SLEEP_SECONDS: float = 30.0

# When computing "due", we allow a small grace window so we still fire if the
# loop wakes slightly late.
DUE_GRACE_SECONDS: float = 2.0


JsonRpcExecute = Callable[[str, list[Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class DueAlarm:
    player_id: str
    alarm_id: str
    scheduled_at: datetime
    alarm_time_s: int
    volume: int
    repeat: bool


def _local_now() -> datetime:
    # Uses system local timezone.
    return datetime.now().astimezone()


def _local_midnight(dt: datetime) -> datetime:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def _python_weekday_to_lms_dow(py_weekday: int) -> int:
    """
    Convert Python weekday (Mon=0..Sun=6) to LMS DOW (Sun=0..Sat=6).
    """
    # Mon(0)->1, Tue(1)->2, ... Sat(5)->6, Sun(6)->0
    return (py_weekday + 1) % 7


class AlarmRuntime:
    """
    Background alarm scheduler.

    Args:
        jsonrpc_execute:
            Callable that executes LMS-like commands via the same path as Web/CLI.
            Expected signature: (player_id, command_list) -> result dict.
            Typically: `web_server.jsonrpc_handler.execute_command`.
        poll_interval_seconds:
            Maximum time to sleep between checks (also clamps long sleeps).
        now_fn:
            Time provider for tests (returns aware local datetime).
    """

    def __init__(
        self,
        *,
        jsonrpc_execute: JsonRpcExecute,
        poll_interval_seconds: float = MAX_SLEEP_SECONDS,
        now_fn: Callable[[], datetime] = _local_now,
    ) -> None:
        self._jsonrpc_execute = jsonrpc_execute
        self._poll_interval_seconds = float(poll_interval_seconds)
        self._now_fn = now_fn

        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

        # (player_id, alarm_id) -> last fired local date
        self._last_fired: dict[tuple[str, str], date] = {}

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="alarm-runtime")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                now = self._now_fn()
                due_list, next_wake = await self._compute_due_and_next_wake(now)

                # Fire due alarms (sequentially; alarms are rare, keep simple)
                for due in due_list:
                    await self._fire_alarm(due)

                # Sleep until next wakeup (clamped)
                sleep_s = max(0.1, min(self._poll_interval_seconds, next_wake))
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=sleep_s)
                except asyncio.TimeoutError:
                    pass
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("AlarmRuntime loop error")
                # Avoid a tight loop on persistent errors
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass

    async def _compute_due_and_next_wake(self, now: datetime) -> tuple[list[DueAlarm], float]:
        """
        Returns:
            (due_alarms, next_wake_seconds)
        """
        # Import here to avoid import cycles at module load.
        from resonance.web.handlers import alarm as alarm_store

        due: list[DueAlarm] = []
        soonest_scheduled: datetime | None = None

        # Snapshot alarms under the alarm handler lock.
        async with alarm_store._ALARM_LOCK:  # noqa: SLF001 (internal lock is intentional)
            snapshot: dict[str, list[Any]] = {
                player_id: list(alarms)
                for player_id, alarms in alarm_store._PLAYER_ALARMS.items()  # noqa: SLF001
            }

        today_midnight = _local_midnight(now)
        today_lms_dow = _python_weekday_to_lms_dow(now.weekday())
        now_ts = now.timestamp()

        for player_id, alarms in snapshot.items():
            for alarm in alarms:
                try:
                    alarm_id = str(getattr(alarm, "id"))
                    enabled = bool(getattr(alarm, "enabled", False))
                    repeat = bool(getattr(alarm, "repeat", True))
                    alarm_time_s = int(getattr(alarm, "time", 0))
                    volume = int(getattr(alarm, "volume", 50))
                    dow = set(getattr(alarm, "dow", set()))
                except Exception:
                    logger.debug("Skipping malformed alarm entry for player %s", player_id, exc_info=True)
                    continue

                if not enabled:
                    continue

                # If dow is empty, treat as "no days selected" (never fires),
                # mirroring typical LMS UI semantics.
                if not dow:
                    continue

                # Determine whether the alarm is applicable today.
                if today_lms_dow not in dow:
                    # Not for today: compute next scheduled date for wake calculation.
                    scheduled = self._next_scheduled_datetime(now, alarm_time_s, dow)
                else:
                    scheduled = today_midnight + timedelta(seconds=alarm_time_s)

                    # If it's already passed significantly, schedule next occurrence.
                    # But still allow due firing within grace window.
                    if scheduled.timestamp() + DUE_GRACE_SECONDS < now_ts:
                        scheduled = self._next_scheduled_datetime(now, alarm_time_s, dow)

                if soonest_scheduled is None or scheduled < soonest_scheduled:
                    soonest_scheduled = scheduled

                # Due determination: if scheduled is within grace window of now.
                # We consider it due if scheduled <= now <= scheduled+grace, or if we're late but
                # within grace.
                if scheduled.timestamp() <= now_ts <= scheduled.timestamp() + DUE_GRACE_SECONDS:
                    last_date = self._last_fired.get((player_id, alarm_id))
                    local_day = now.date()
                    if last_date == local_day:
                        continue

                    due.append(
                        DueAlarm(
                            player_id=player_id,
                            alarm_id=alarm_id,
                            scheduled_at=scheduled,
                            alarm_time_s=alarm_time_s,
                            volume=max(0, min(100, volume)),
                            repeat=repeat,
                        )
                    )

        # Compute next wakeup: time until soonest_scheduled, but clamp to MAX_SLEEP_SECONDS.
        if soonest_scheduled is None:
            return due, self._poll_interval_seconds

        seconds_until = max(0.0, soonest_scheduled.timestamp() - now.timestamp())
        # Wake slightly before scheduled time so due window is hit reliably.
        seconds_until = max(0.0, seconds_until - 0.2)
        return due, min(seconds_until, self._poll_interval_seconds)

    def _next_scheduled_datetime(self, now: datetime, alarm_time_s: int, dow: set[int]) -> datetime:
        """
        Compute the next local datetime when this alarm should fire, starting *after now*.

        We search up to 8 days ahead.
        """
        base_midnight = _local_midnight(now)
        for day_offset in range(0, 8):
            candidate_date = base_midnight + timedelta(days=day_offset)
            candidate_py_weekday = (now.weekday() + day_offset) % 7
            candidate_lms_dow = _python_weekday_to_lms_dow(candidate_py_weekday)
            if candidate_lms_dow not in dow:
                continue
            candidate_dt = candidate_date + timedelta(seconds=alarm_time_s)
            if candidate_dt.timestamp() + DUE_GRACE_SECONDS < now.timestamp():
                continue
            return candidate_dt

        # Fallback: tomorrow at the alarm time (shouldn't happen with 8-day search)
        return base_midnight + timedelta(days=1, seconds=alarm_time_s)

    async def _fire_alarm(self, due: DueAlarm) -> None:
        from resonance.web.handlers import alarm as alarm_store

        logger.info(
            "[alarm_runtime] Firing alarm",
            extra={
                "player_id": due.player_id,
                "alarm_id": due.alarm_id,
                "scheduled_at": due.scheduled_at.isoformat(),
                "volume": due.volume,
                "repeat": due.repeat,
            },
        )

        # Mark fired early to prevent re-entrancy/double fire on slow execution.
        self._last_fired[(due.player_id, due.alarm_id)] = self._now_fn().date()

        # 1) Ensure volume is applied
        try:
            await self._jsonrpc_execute(due.player_id, ["mixer", "volume", str(due.volume)])
        except Exception:
            logger.exception("[alarm_runtime] Failed to set alarm volume for %s", due.player_id)

        # 2) Start playback (from current playlist if stopped)
        try:
            await self._jsonrpc_execute(due.player_id, ["play"])
        except Exception:
            logger.exception("[alarm_runtime] Failed to start playback for %s", due.player_id)

        # 3) One-shot alarms: disable after firing
        if not due.repeat:
            try:
                async with alarm_store._ALARM_LOCK:  # noqa: SLF001
                    alarms = alarm_store._PLAYER_ALARMS.get(due.player_id, [])  # noqa: SLF001
                    for alarm in alarms:
                        if str(getattr(alarm, "id", "")) == due.alarm_id:
                            setattr(alarm, "enabled", False)
                            break
            except Exception:
                logger.exception("[alarm_runtime] Failed to disable one-shot alarm %s", due.alarm_id)
