"""
Server-side crossfade helpers.

This module prepares a concrete crossfade plan and builds the SoX command used
to render an overlapped transition from two files.
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from mutagen import File as mutagen_file

from resonance.streaming.transcoder import (
    build_command,
    get_transcode_config,
    resolve_binary,
)

logger = logging.getLogger(__name__)

_MIN_OVERLAP_SECONDS = 0.1
_MIN_REMAINING_SECONDS = 0.05

_DEFAULT_MEDIA_TYPE = "audio/mpeg"
_MEDIA_TYPES_BY_FORMAT: dict[str, str] = {
    "mp3": "audio/mpeg",
    "flac": "audio/flac",
    "ogg": "audio/ogg",
    "wav": "audio/wav",
    "aiff": "audio/aiff",
}

_SOX_FORMAT_ALIASES: dict[str, str] = {
    "flc": "flac",
    "aif": "aiff",
}


@dataclass(frozen=True, slots=True)
class PreparedCrossfadePlan:
    """Precomputed values for a server-side crossfade transition."""

    previous_path: Path
    next_path: Path
    output_format_hint: str
    overlap_seconds: float
    splice_position_seconds: float
    splice_excess_seconds: float
    trim_start_seconds: float
    # Optional explicit SoX input specs (e.g. |faad ... | lame ... for m4b).
    previous_input_spec: str | None = None
    next_input_spec: str | None = None
    previous_input_format_hint: str | None = None
    next_input_format_hint: str | None = None


def normalize_output_format_hint(format_hint: str | None) -> str:
    """Normalize an output format hint to a SoX-compatible token."""
    normalized = str(format_hint or "").strip().lower().lstrip(".")
    if not normalized:
        return "mp3"
    return _SOX_FORMAT_ALIASES.get(normalized, normalized)


def media_type_for_output_format(format_hint: str | None) -> str:
    """Return response media type for a crossfade output format."""
    fmt = normalize_output_format_hint(format_hint)
    return _MEDIA_TYPES_BY_FORMAT.get(fmt, _DEFAULT_MEDIA_TYPE)


def prepare_crossfade_plan(
    *,
    previous_path: Path,
    next_path: Path,
    requested_overlap_seconds: float,
    output_format_hint: str | None,
) -> PreparedCrossfadePlan | None:
    """
    Prepare a concrete crossfade plan.

    Returns None when crossfade is not feasible (missing files, no durations,
    overlap too short, etc.).
    """
    if requested_overlap_seconds <= 0:
        return None

    if previous_path == next_path:
        return None

    if not previous_path.exists() or not next_path.exists():
        return None

    previous_input_spec, previous_input_fmt = _resolve_sox_input_spec(previous_path)
    next_input_spec, next_input_fmt = _resolve_sox_input_spec(next_path)
    if previous_input_spec is None or next_input_spec is None:
        logger.debug(
            "Skipping server-side crossfade; no SoX-readable input for %s | %s",
            previous_path,
            next_path,
        )
        return None

    previous_duration = _read_duration_seconds(previous_path)
    next_duration = _read_duration_seconds(next_path)
    if previous_duration is None or next_duration is None:
        return None

    overlap = min(
        float(requested_overlap_seconds),
        previous_duration - _MIN_REMAINING_SECONDS,
        next_duration - _MIN_REMAINING_SECONDS,
    )
    if overlap < _MIN_OVERLAP_SECONDS:
        return None

    splice_position = previous_duration
    splice_excess = overlap / 2.0
    trim_start = max(0.0, previous_duration - overlap)

    return PreparedCrossfadePlan(
        previous_path=previous_path,
        next_path=next_path,
        output_format_hint=normalize_output_format_hint(output_format_hint),
        overlap_seconds=overlap,
        splice_position_seconds=splice_position,
        splice_excess_seconds=splice_excess,
        trim_start_seconds=trim_start,
        previous_input_spec=previous_input_spec,
        next_input_spec=next_input_spec,
        previous_input_format_hint=previous_input_fmt,
        next_input_format_hint=next_input_fmt,
    )


def build_crossfade_command(plan: PreparedCrossfadePlan) -> list[str]:
    """
    Build the SoX command that renders the crossfaded transition.

    Output is written to stdout and starts at the transition window
    (tail of previous track + full next track after overlap).
    """
    sox_bin = resolve_binary("sox")
    if sox_bin is None:
        raise ValueError("Binary not found: sox")

    splice_arg = (
        f"{plan.splice_position_seconds:.3f},"
        f"{plan.splice_excess_seconds:.3f},0"
    )
    trim_arg = f"{plan.trim_start_seconds:.3f}"

    previous_input = plan.previous_input_spec or str(plan.previous_path)
    next_input = plan.next_input_spec or str(plan.next_path)

    command = [str(sox_bin)]

    # For pipe inputs, SoX cannot infer type from extension, so declare it.
    if plan.previous_input_format_hint:
        command.extend(["-t", plan.previous_input_format_hint])
    command.append(previous_input)

    if plan.next_input_format_hint:
        command.extend(["-t", plan.next_input_format_hint])
    command.append(next_input)

    command.extend(
        [
            "-t",
            plan.output_format_hint,
            "-",
            "splice",
            "-q",
            splice_arg,
            "trim",
            trim_arg,
        ]
    )

    return command


def _read_duration_seconds(path: Path) -> float | None:
    """Read audio duration in seconds (mutagen first, SoX fallback)."""
    try:
        audio = mutagen_file(path, easy=False)
        info = getattr(audio, "info", None)
        length = getattr(info, "length", None)
        if length is not None:
            duration = float(length)
            if duration > 0:
                return duration
    except Exception:
        logger.debug("Duration probe via mutagen failed for %s", path, exc_info=True)

    sox_bin = resolve_binary("sox")
    if sox_bin is None:
        return None

    try:
        output = subprocess.check_output(
            [str(sox_bin), "--i", "-D", str(path)],
            text=True,
            stderr=subprocess.STDOUT,
            timeout=5.0,
        )
        duration = float(output.strip())
        if duration > 0:
            return duration
    except Exception:
        logger.debug("Duration probe via sox failed for %s", path, exc_info=True)

    return None


def _resolve_sox_input_spec(path: Path) -> tuple[str | None, str | None]:
    """Return (SoX input arg, input format hint)."""
    if _can_decode_with_sox(path):
        return str(path), None

    return _build_legacy_decode_pipe_for_sox(path)


def _build_legacy_decode_pipe_for_sox(path: Path) -> tuple[str | None, str | None]:
    """
    Build a SoX input pipe command via legacy.conf transcoding rules.

    This allows server-side crossfade for formats SoX cannot decode directly
    (notably m4a/m4b), by decoding them through the existing LMS-style pipeline.
    """
    source_format = path.suffix.lower().lstrip(".")
    if not source_format:
        return None, None

    try:
        config = get_transcode_config()
        rule = config.find_rule(source_format, dest_format="mp3")
        if rule is None or rule.is_passthrough():
            return None, None

        commands = build_command(rule, path)
        if not commands:
            return None, None

        rendered_parts: list[str] = []
        for cmd in commands:
            normalized = [_normalize_arg_for_sox_pipe(arg) for arg in cmd]
            rendered_parts.append(subprocess.list2cmdline(normalized))

        return "|" + " | ".join(rendered_parts), normalize_output_format_hint(rule.dest_format)
    except Exception:
        logger.debug("Failed to build legacy decode pipe for %s", path, exc_info=True)
        return None, None


def _normalize_arg_for_sox_pipe(arg: str) -> str:
    """Normalize command args for SoX pipe execution on Windows."""
    if os.name != "nt":
        return arg

    if arg.startswith("-"):
        return arg

    if not any(ch in arg for ch in ("\\", "/", ":")):
        return arg

    candidate = Path(arg)
    if not candidate.exists():
        return arg

    short = _windows_short_path(candidate)
    return short or arg


def _windows_short_path(path: Path) -> str | None:
    """Best-effort DOS 8.3 short path resolution (Windows only)."""
    if os.name != "nt":
        return str(path)

    try:
        import ctypes

        full = str(path)
        buffer = ctypes.create_unicode_buffer(32768)
        result = ctypes.windll.kernel32.GetShortPathNameW(full, buffer, len(buffer))
        if result == 0:
            return full
        return buffer.value
    except Exception:
        return str(path)


def _can_decode_with_sox(path: Path) -> bool:
    """Return True when SoX can inspect/decode this input file."""
    sox_bin = resolve_binary("sox")
    if sox_bin is None:
        return False

    try:
        result = subprocess.run(
            [str(sox_bin), "--i", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5.0,
        )
        return result.returncode == 0
    except Exception:
        logger.debug("SoX decode probe failed for %s", path, exc_info=True)
        return False
