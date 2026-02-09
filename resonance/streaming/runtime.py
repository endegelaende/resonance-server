"""
Runtime playback helpers for gapless/crossfade/replaygain behavior.

This module keeps the logic isolated so command handlers and the streaming
service can share one implementation.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from mutagen import File as mutagen_file

# Player prefs we use to control runtime playback behavior.
PLAYER_PREF_DEFAULTS: dict[str, str] = {
    "transitionType": "0",
    "transitionDuration": "10",
    "transitionSmart": "1",
    "replayGainMode": "0",
    "remoteReplayGain": "-5",
    "gapless": "1",
}

# Alias names that appear in different clients/plugins.
PLAYER_PREF_ALIASES: dict[str, str] = {
    "transitiontype": "transitionType",
    "transitionduration": "transitionDuration",
    "transitionsmart": "transitionSmart",
    "replaygainmode": "replayGainMode",
    "remotereplaygain": "remoteReplayGain",
    "gapless": "gapless",
    "norestartdecoder": "gapless",
}

_GAIN_RE = re.compile(r"(-?\d+(?:\.\d+)?)")
_PEAK_RE = re.compile(r"(\d+(?:\.\d+)?)")


@dataclass(slots=True)
class PlayerRuntimeConfig:
    """Runtime playback config derived from player preferences."""

    transition_type: int = 0
    transition_duration: int = 10
    transition_smart: bool = True
    replay_gain_mode: int = 0
    remote_replay_gain_db: float = -5.0
    gapless: bool = True


@dataclass(slots=True)
class RuntimeStreamParams:
    """Resolved strm parameters for a concrete track start."""

    transition_type: int = 0
    transition_duration: int = 0
    flags: int = 0
    replay_gain: int = 0


@dataclass(frozen=True, slots=True)
class ReplayGainMetadata:
    """ReplayGain values read from tags."""

    track_gain_db: float | None = None
    track_peak: float | None = None
    album_gain_db: float | None = None
    album_peak: float | None = None


def canonical_player_pref_name(pref_name: str) -> str | None:
    """Normalize a player pref key to its canonical runtime name."""
    if not pref_name:
        return None
    return PLAYER_PREF_ALIASES.get(pref_name.strip().lower())


def default_player_pref_value(pref_name: str) -> str | None:
    """Return default value for a runtime player pref."""
    canonical = canonical_player_pref_name(pref_name)
    if canonical is None:
        return None
    return PLAYER_PREF_DEFAULTS.get(canonical)


def normalize_player_pref_value(pref_name: str, raw_value: Any) -> tuple[str, str] | None:
    """
    Parse and normalize runtime player prefs.

    Returns:
        (canonical_name, normalized_string) or None if pref is unsupported.
    """
    canonical = canonical_player_pref_name(pref_name)
    if canonical is None:
        return None

    text = str(raw_value).strip()

    if canonical == "transitionType":
        value = _clamp_int(text, 0, 5, default=0)
        return canonical, str(value)

    if canonical == "transitionDuration":
        value = _clamp_int(text, 0, 255, default=10)
        return canonical, str(value)

    if canonical == "transitionSmart":
        return canonical, "1" if _parse_bool(text, default=True) else "0"

    if canonical == "replayGainMode":
        value = _clamp_int(text, 0, 3, default=0)
        return canonical, str(value)

    if canonical == "remoteReplayGain":
        value = _clamp_float(text, -20.0, 20.0, default=-5.0)
        # Keep a compact string format for round-tripping via playerpref.
        return canonical, f"{value:.3f}".rstrip("0").rstrip(".")

    if canonical == "gapless":
        return canonical, "1" if _parse_bool(text, default=True) else "0"

    return None


def apply_player_pref(config: PlayerRuntimeConfig, canonical_name: str, value_text: str) -> None:
    """Apply a normalized runtime pref value to a config object."""
    if canonical_name == "transitionType":
        config.transition_type = _clamp_int(value_text, 0, 5, default=0)
        return
    if canonical_name == "transitionDuration":
        config.transition_duration = _clamp_int(value_text, 0, 255, default=10)
        return
    if canonical_name == "transitionSmart":
        config.transition_smart = _parse_bool(value_text, default=True)
        return
    if canonical_name == "replayGainMode":
        config.replay_gain_mode = _clamp_int(value_text, 0, 3, default=0)
        return
    if canonical_name == "remoteReplayGain":
        config.remote_replay_gain_db = _clamp_float(value_text, -20.0, 20.0, default=-5.0)
        return
    if canonical_name == "gapless":
        config.gapless = _parse_bool(value_text, default=True)


def prevent_clipping(gain_db: float, peak: float | None) -> float:
    """Clamp gain if peak indicates clipping risk (LMS-style)."""
    if peak is not None and peak > 0:
        no_clip = -20.0 * math.log10(peak)
        if no_clip < gain_db:
            return no_clip
    return gain_db


def db_to_fixed(db_value: float) -> int:
    """Convert dB gain value to 16.16 fixed point (LMS-compatible)."""
    float_mult = 10 ** (db_value / 20.0)
    if -30.0 <= db_value <= 0.0:
        value = int(float_mult * (1 << 8) + 0.5) * (1 << 8)
    else:
        value = int(float_mult * (1 << 16) + 0.5)
    return max(0, min(value, 0xFFFFFFFF))


def compute_replay_gain_fixed(
    *,
    path: str,
    replay_gain_mode: int,
    remote_replay_gain_db: float,
    prefer_album_gain: bool,
) -> int:
    """
    Compute replay_gain field value for strm (16.16 fixed point).

    replay_gain_mode:
        0 = off, 1 = track, 2 = album, 3 = smart (album/track)
    """
    mode = max(0, min(int(replay_gain_mode), 3))
    if mode == 0:
        return 0

    # LMS applies remoteReplayGain when we do not have local tags.
    if path.startswith("http://") or path.startswith("https://"):
        return db_to_fixed(remote_replay_gain_db)

    metadata = read_replay_gain(Path(path))

    gain_db: float | None = None
    peak: float | None = None

    if mode == 1:
        gain_db = metadata.track_gain_db
        peak = metadata.track_peak
    elif mode == 2:
        gain_db = metadata.album_gain_db
        peak = metadata.album_peak
    else:
        if prefer_album_gain and metadata.album_gain_db is not None:
            gain_db = metadata.album_gain_db
            peak = metadata.album_peak
        else:
            gain_db = metadata.track_gain_db
            peak = metadata.track_peak

    if gain_db is None:
        return 0

    gain_db = prevent_clipping(gain_db, peak)
    return db_to_fixed(gain_db)


def read_replay_gain(path: Path) -> ReplayGainMetadata:
    """Read ReplayGain tags with stat-based caching."""
    try:
        stat = path.stat()
    except OSError:
        return ReplayGainMetadata()

    return _read_replay_gain_cached(str(path), int(stat.st_mtime_ns), int(stat.st_size))


@lru_cache(maxsize=4096)
def _read_replay_gain_cached(path_text: str, mtime_ns: int, file_size: int) -> ReplayGainMetadata:
    del mtime_ns, file_size  # only part of cache key
    try:
        audio = mutagen_file(path_text, easy=False)
    except Exception:
        return ReplayGainMetadata()

    if audio is None:
        return ReplayGainMetadata()

    tags = getattr(audio, "tags", None)
    if tags is None:
        return ReplayGainMetadata()

    track_gain: float | None = None
    track_peak: float | None = None
    album_gain: float | None = None
    album_peak: float | None = None

    try:
        iterator = tags.items() if hasattr(tags, "items") else []
    except Exception:
        iterator = []

    for raw_key, raw_value in iterator:
        key = _normalize_replaygain_key(raw_key)
        values = _extract_text_values(raw_value)
        if not values:
            continue

        if key == "replaygain_track_gain" and track_gain is None:
            track_gain = _parse_gain(values[0])
        elif key == "replaygain_track_peak" and track_peak is None:
            track_peak = _parse_peak(values[0])
        elif key == "replaygain_album_gain" and album_gain is None:
            album_gain = _parse_gain(values[0])
        elif key == "replaygain_album_peak" and album_peak is None:
            album_peak = _parse_peak(values[0])

    return ReplayGainMetadata(
        track_gain_db=track_gain,
        track_peak=track_peak,
        album_gain_db=album_gain,
        album_peak=album_peak,
    )


def _normalize_replaygain_key(raw_key: Any) -> str:
    key = str(raw_key).strip().lower()
    if key.startswith("txxx:"):
        key = key[5:]
    if key.startswith("----:com.apple.itunes:"):
        key = key[20:]
    key = key.replace(" ", "_").replace("-", "_")
    return key


def _extract_text_values(raw_value: Any) -> list[str]:
    if raw_value is None:
        return []
    if isinstance(raw_value, str):
        return [raw_value]
    if isinstance(raw_value, bytes):
        return [raw_value.decode("utf-8", errors="replace")]
    if isinstance(raw_value, list | tuple):
        out: list[str] = []
        for entry in raw_value:
            out.extend(_extract_text_values(entry))
        return out
    text_attr = getattr(raw_value, "text", None)
    if text_attr is not None:
        return _extract_text_values(text_attr)
    return [str(raw_value)]


def _parse_gain(value: str) -> float | None:
    match = _GAIN_RE.search(value)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _parse_peak(value: str) -> float | None:
    match = _PEAK_RE.search(value)
    if not match:
        return None
    try:
        peak = float(match.group(1))
    except ValueError:
        return None
    if peak <= 0:
        return None
    return peak


def _parse_bool(value: str, *, default: bool) -> bool:
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return default


def _clamp_int(value: str, low: int, high: int, *, default: int) -> int:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(low, min(parsed, high))


def _clamp_float(value: str, low: float, high: float, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(parsed, high))
