"""
Alarm command handlers (LMS compatibility subset).

Implements:
- alarm add/delete/update/enableall/disableall/defaultvolume
- alarms <start> <items> [filter:<enabled|all|defined>] [dow:<0-6>]
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any

from resonance.web.handlers import CommandContext
from resonance.web.jsonrpc_helpers import parse_start_items, parse_tagged_params


@dataclass
class AlarmEntry:
    id: str
    time: int
    dow: set[int]
    enabled: bool = True
    repeat: bool = True
    volume: int = 50
    shufflemode: int = 0
    url: str | None = None


_ALARM_LOCK = asyncio.Lock()
_PLAYER_ALARMS: dict[str, list[AlarmEntry]] = {}
_PLAYER_DEFAULT_VOLUME: dict[str, int] = {}


def _parse_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _parse_bool(value: Any) -> bool | None:
    parsed = _parse_int(value)
    if parsed is None:
        return None
    return parsed != 0


def _parse_dow_csv(value: Any) -> set[int] | None:
    if value is None:
        return None

    raw = str(value).strip()
    if raw == "":
        return set()

    days: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part == "":
            continue
        try:
            day = int(part)
        except ValueError:
            return None
        if day < 0 or day > 6:
            return None
        days.add(day)

    return days


def _serialize_alarm(alarm: AlarmEntry) -> dict[str, Any]:
    return {
        "id": alarm.id,
        "dow": ",".join(str(day) for day in sorted(alarm.dow)),
        "enabled": int(alarm.enabled),
        "repeat": int(alarm.repeat),
        "shufflemode": alarm.shufflemode,
        "time": alarm.time,
        "volume": alarm.volume,
        "url": alarm.url or "CURRENT_PLAYLIST",
    }


def _extract_alarm_params(command: list[Any]) -> dict[str, Any]:
    params: dict[str, Any] = {}

    if len(command) >= 2:
        first = command[1]
        if isinstance(first, str) and ":" in first:
            key, value = first.split(":", 1)
            params[key] = value
        else:
            params["cmd"] = first

    for arg in command[2:]:
        if isinstance(arg, dict):
            params.update(arg)
        elif isinstance(arg, str) and ":" in arg:
            key, value = arg.split(":", 1)
            params[key] = value

    if "url" not in params and "playlisturl" in params:
        params["url"] = params["playlisturl"]

    return params


def _find_alarm(alarms: list[AlarmEntry], alarm_id: str) -> AlarmEntry | None:
    for alarm in alarms:
        if alarm.id == alarm_id:
            return alarm
    return None


def _remove_alarm(alarms: list[AlarmEntry], alarm_id: str) -> bool:
    idx_to_remove: int | None = None
    for idx, alarm in enumerate(alarms):
        if alarm.id == alarm_id:
            idx_to_remove = idx
            break
    if idx_to_remove is None:
        return False
    del alarms[idx_to_remove]
    return True


def _apply_alarm_updates(alarm: AlarmEntry, params: dict[str, Any]) -> bool:
    if "time" in params:
        parsed_time = _parse_int(params.get("time"))
        if parsed_time is None:
            return False
        alarm.time = parsed_time

    if "url" in params:
        raw_url = str(params.get("url"))
        alarm.url = None if raw_url == "0" else raw_url

    if "volume" in params:
        parsed_volume = _parse_int(params.get("volume"))
        if parsed_volume is None:
            return False
        alarm.volume = parsed_volume

    if "shufflemode" in params:
        parsed_shuffle = _parse_int(params.get("shufflemode"))
        if parsed_shuffle is None:
            return False
        alarm.shufflemode = parsed_shuffle

    if "enabled" in params:
        parsed_enabled = _parse_bool(params.get("enabled"))
        if parsed_enabled is None:
            return False
        alarm.enabled = parsed_enabled

    if "repeat" in params:
        parsed_repeat = _parse_bool(params.get("repeat"))
        if parsed_repeat is None:
            return False
        alarm.repeat = parsed_repeat

    if "dow" in params:
        parsed_dow = _parse_dow_csv(params.get("dow"))
        if parsed_dow is None:
            return False
        alarm.dow = parsed_dow

    if "dowAdd" in params:
        parsed_add = _parse_int(params.get("dowAdd"))
        if parsed_add is None or parsed_add < 0 or parsed_add > 6:
            return False
        alarm.dow.add(parsed_add)

    if "dowDel" in params:
        parsed_del = _parse_int(params.get("dowDel"))
        if parsed_del is None or parsed_del < 0 or parsed_del > 6:
            return False
        alarm.dow.discard(parsed_del)

    return True


async def cmd_alarm(ctx: CommandContext, command: list[Any]) -> dict[str, Any]:
    """Handle LMS-compatible `alarm` command."""
    if ctx.player_id == "-":
        return {"error": "No player specified"}

    params = _extract_alarm_params(command)
    cmd = str(params.get("cmd", "")).lower()

    if cmd not in {"add", "delete", "update", "enableall", "disableall", "defaultvolume"}:
        return {"error": "Invalid alarm command"}

    async with _ALARM_LOCK:
        alarms = _PLAYER_ALARMS.setdefault(ctx.player_id, [])

        if cmd == "add":
            parsed_time = _parse_int(params.get("time"))
            if parsed_time is None:
                return {"error": "Missing or invalid alarm time"}

            default_volume = _PLAYER_DEFAULT_VOLUME.get(ctx.player_id, 50)
            alarm = AlarmEntry(
                id=uuid.uuid4().hex,
                time=parsed_time,
                dow={0, 1, 2, 3, 4, 5, 6},
                volume=default_volume,
            )
            if not _apply_alarm_updates(alarm, params):
                return {"error": "Invalid alarm parameters"}

            alarms.append(alarm)
            return {"id": alarm.id}

        if cmd == "delete":
            alarm_id = str(params.get("id", ""))
            if alarm_id == "":
                return {"error": "Missing alarm id"}

            removed = _remove_alarm(alarms, alarm_id)
            return {"id": alarm_id} if removed else {}

        if cmd == "update":
            alarm_id = str(params.get("id", ""))
            if alarm_id == "":
                return {"error": "Missing alarm id"}

            alarm = _find_alarm(alarms, alarm_id)
            if alarm is None:
                return {}

            if not _apply_alarm_updates(alarm, params):
                return {"error": "Invalid alarm parameters"}
            return {"id": alarm.id}

        if cmd == "enableall":
            for alarm in alarms:
                alarm.enabled = True
            return {}

        if cmd == "disableall":
            for alarm in alarms:
                alarm.enabled = False
            return {}

        # defaultvolume
        parsed_volume = _parse_int(params.get("volume"))
        if parsed_volume is None:
            return {"error": "Missing or invalid alarm volume"}

        _PLAYER_DEFAULT_VOLUME[ctx.player_id] = parsed_volume
        return {"volume": parsed_volume}


async def cmd_alarms(ctx: CommandContext, command: list[Any]) -> dict[str, Any]:
    """Handle LMS-compatible `alarms` query."""
    if ctx.player_id == "-":
        return {"error": "No player specified"}

    tagged = parse_tagged_params(command)
    filter_mode = tagged.get("filter", "enabled").lower()
    if filter_mode == "defined":
        filter_mode = "all"
    if filter_mode not in {"all", "enabled"}:
        return {"error": "Invalid alarm filter"}

    alarm_dow = tagged.get("dow")
    parsed_dow = _parse_int(alarm_dow) if alarm_dow is not None else None
    if alarm_dow is not None and (parsed_dow is None or parsed_dow < 0 or parsed_dow > 6):
        return {"error": "Invalid alarm dow"}

    start, items = parse_start_items(command)

    async with _ALARM_LOCK:
        alarms = list(_PLAYER_ALARMS.get(ctx.player_id, []))

    if parsed_dow is not None:
        alarms = [alarm for alarm in alarms if parsed_dow in alarm.dow]
    elif filter_mode == "enabled":
        alarms = [alarm for alarm in alarms if alarm.enabled]

    count = len(alarms)
    if start < 0:
        start = 0
    if items < 0:
        items = 0

    page = alarms[start : start + items]

    return {
        "fade": 0,
        "count": count,
        "offset": start,
        "alarms_loop": [_serialize_alarm(alarm) for alarm in page],
    }
