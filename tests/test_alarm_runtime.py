"""
Unit tests for AlarmRuntime.

These tests focus on:
- due computation around a scheduled time
- firing behavior (volume + play)
- one-shot alarms (repeat=False) disable after fire
- duplicate protection (same day -> no double fire)

IMPORTANT:
- Alarm definitions are stored in a module-level in-memory dict in
  `resonance.web.handlers.alarm`. These tests must clear that shared store
  between cases to avoid cross-test leakage.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from resonance.core.alarm_runtime import AlarmRuntime
from resonance.web.handlers import alarm as alarm_store


@pytest.fixture(autouse=True)
async def _clear_alarm_store_between_tests() -> None:
    """Isolate tests by clearing the shared in-memory alarm store."""
    async with alarm_store._ALARM_LOCK:
        alarm_store._PLAYER_ALARMS.clear()
        alarm_store._PLAYER_DEFAULT_VOLUME.clear()


@dataclass
class _RecordedCall:
    player_id: str
    command: list[object]


class _FakeJsonRpc:
    def __init__(self) -> None:
        self.calls: list[_RecordedCall] = []

    async def execute(self, player_id: str, command: list[object]) -> dict[str, object]:
        self.calls.append(_RecordedCall(player_id=player_id, command=list(command)))
        return {}


def _local_aware(dt: datetime) -> datetime:
    """Ensure dt is timezone-aware in local tz.

    AlarmRuntime uses datetime.now().astimezone() (aware). For tests we supply
    our own aware datetime; keeping tzinfo consistent avoids subtle bugs.
    """
    if dt.tzinfo is None:
        # Use a fixed offset timezone to keep tests deterministic.
        return dt.replace(tzinfo=timezone(timedelta(hours=1)))
    return dt


def _seconds_since_local_midnight(dt: datetime) -> int:
    midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return int((dt - midnight).total_seconds())


@pytest.mark.asyncio
async def test_due_alarm_fires_volume_then_play_and_marks_last_fired() -> None:
    fake = _FakeJsonRpc()

    # Use an aware "local" time (fixed TZ).
    now = _local_aware(datetime(2026, 2, 8, 7, 0, 1))

    player_id = "00:11:22:33:44:aa"
    alarm_id = "alarm1"
    alarm_time_s = _seconds_since_local_midnight(now)  # due "now"
    entry = alarm_store.AlarmEntry(
        id=alarm_id,
        time=alarm_time_s,
        dow={0, 1, 2, 3, 4, 5, 6},
        enabled=True,
        repeat=True,
        volume=33,
        shufflemode=0,
        url=None,
    )

    async with alarm_store._ALARM_LOCK:
        alarm_store._PLAYER_ALARMS[player_id] = [entry]

    rt = AlarmRuntime(jsonrpc_execute=fake.execute, now_fn=lambda: now, poll_interval_seconds=30.0)

    due, _ = await rt._compute_due_and_next_wake(now)  # type: ignore[attr-defined]
    assert len(due) == 1
    assert due[0].player_id == player_id
    assert due[0].alarm_id == alarm_id
    assert due[0].volume == 33

    await rt._fire_alarm(due[0])  # type: ignore[attr-defined]

    # Verify JSON-RPC commands: set volume then play
    assert [c.command for c in fake.calls] == [
        ["mixer", "volume", "33"],
        ["play"],
    ]

    # Duplicate protection: last fired should be today
    assert rt._last_fired[(player_id, alarm_id)] == now.date()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_one_shot_alarm_is_disabled_after_firing() -> None:
    fake = _FakeJsonRpc()
    now = _local_aware(datetime(2026, 2, 8, 7, 0, 1))

    player_id = "00:11:22:33:44:bb"
    alarm_id = "alarm2"
    alarm_time_s = _seconds_since_local_midnight(now)

    entry = alarm_store.AlarmEntry(
        id=alarm_id,
        time=alarm_time_s,
        dow={0, 1, 2, 3, 4, 5, 6},
        enabled=True,
        repeat=False,  # one-shot
        volume=41,
        shufflemode=0,
        url=None,
    )

    async with alarm_store._ALARM_LOCK:
        alarm_store._PLAYER_ALARMS[player_id] = [entry]

    rt = AlarmRuntime(jsonrpc_execute=fake.execute, now_fn=lambda: now)

    due, _ = await rt._compute_due_and_next_wake(now)  # type: ignore[attr-defined]
    assert len(due) == 1

    await rt._fire_alarm(due[0])  # type: ignore[attr-defined]

    # Alarm should be disabled in store
    async with alarm_store._ALARM_LOCK:
        stored = alarm_store._PLAYER_ALARMS[player_id][0]
        assert stored.enabled is False

    # Commands were called
    assert [c.command for c in fake.calls] == [
        ["mixer", "volume", "41"],
        ["play"],
    ]


@pytest.mark.asyncio
async def test_alarm_does_not_fire_twice_same_day() -> None:
    fake = _FakeJsonRpc()
    now = _local_aware(datetime(2026, 2, 8, 7, 0, 1))

    player_id = "00:11:22:33:44:cc"
    alarm_id = "alarm3"
    alarm_time_s = _seconds_since_local_midnight(now)

    entry = alarm_store.AlarmEntry(
        id=alarm_id,
        time=alarm_time_s,
        dow={0, 1, 2, 3, 4, 5, 6},
        enabled=True,
        repeat=True,
        volume=20,
    )

    async with alarm_store._ALARM_LOCK:
        alarm_store._PLAYER_ALARMS[player_id] = [entry]

    rt = AlarmRuntime(jsonrpc_execute=fake.execute, now_fn=lambda: now)

    # First compute: due
    due1, _ = await rt._compute_due_and_next_wake(now)  # type: ignore[attr-defined]
    assert len(due1) == 1
    await rt._fire_alarm(due1[0])  # type: ignore[attr-defined]

    # Second compute at same timestamp/day: should not be due again
    due2, _ = await rt._compute_due_and_next_wake(now)  # type: ignore[attr-defined]
    assert due2 == []

    # Only one fire (2 calls: volume + play)
    assert len(fake.calls) == 2


@pytest.mark.asyncio
async def test_alarm_not_due_outside_grace_window() -> None:
    fake = _FakeJsonRpc()
    base = _local_aware(datetime(2026, 2, 8, 7, 0, 0))

    player_id = "00:11:22:33:44:dd"
    alarm_id = "alarm4"
    alarm_time_s = _seconds_since_local_midnight(base)

    entry = alarm_store.AlarmEntry(
        id=alarm_id,
        time=alarm_time_s,
        dow={0, 1, 2, 3, 4, 5, 6},
        enabled=True,
        repeat=True,
        volume=55,
    )

    async with alarm_store._ALARM_LOCK:
        alarm_store._PLAYER_ALARMS[player_id] = [entry]

    # Move "now" far beyond grace
    now = base + timedelta(seconds=10)
    rt = AlarmRuntime(jsonrpc_execute=fake.execute, now_fn=lambda: now)

    due, _ = await rt._compute_due_and_next_wake(now)  # type: ignore[attr-defined]
    assert due == []
    assert fake.calls == []


@pytest.mark.asyncio
async def test_alarm_with_empty_dow_never_fires() -> None:
    fake = _FakeJsonRpc()
    now = _local_aware(datetime(2026, 2, 8, 7, 0, 1))

    player_id = "00:11:22:33:44:ee"
    alarm_id = "alarm5"
    alarm_time_s = _seconds_since_local_midnight(now)

    entry = alarm_store.AlarmEntry(
        id=alarm_id,
        time=alarm_time_s,
        dow=set(),  # no days selected
        enabled=True,
        repeat=True,
        volume=10,
    )

    async with alarm_store._ALARM_LOCK:
        alarm_store._PLAYER_ALARMS[player_id] = [entry]

    rt = AlarmRuntime(jsonrpc_execute=fake.execute, now_fn=lambda: now)

    due, _ = await rt._compute_due_and_next_wake(now)  # type: ignore[attr-defined]
    assert due == []
    assert fake.calls == []


@pytest.mark.asyncio
async def test_alarm_disabled_never_fires() -> None:
    fake = _FakeJsonRpc()
    now = _local_aware(datetime(2026, 2, 8, 7, 0, 1))

    player_id = "00:11:22:33:44:ff"
    alarm_id = "alarm6"
    alarm_time_s = _seconds_since_local_midnight(now)

    entry = alarm_store.AlarmEntry(
        id=alarm_id,
        time=alarm_time_s,
        dow={0, 1, 2, 3, 4, 5, 6},
        enabled=False,
        repeat=True,
        volume=10,
    )

    async with alarm_store._ALARM_LOCK:
        alarm_store._PLAYER_ALARMS[player_id] = [entry]

    rt = AlarmRuntime(jsonrpc_execute=fake.execute, now_fn=lambda: now)

    due, _ = await rt._compute_due_and_next_wake(now)  # type: ignore[attr-defined]
    assert due == []
    assert fake.calls == []
