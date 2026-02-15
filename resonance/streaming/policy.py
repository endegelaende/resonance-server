"""
Shared streaming policy for Resonance.

This module is the single source of truth for decisions that must be consistent
across:
- HTTP streaming route behavior (direct vs. transcoded streaming)
- Slimproto `strm` format signaling (what the player is told to expect)

Why this exists:
- Previously, the list of "always transcode" formats was duplicated in multiple
  modules. That creates drift and subtle playback bugs (player expects AAC while
  server actually streams FLAC, etc.).

Design goals:
- Keep policy decisions small, explicit, and easy to reason about.
- Prefer "safety first" behavior for unknown formats/devices.
- Avoid importing heavy modules at import time (FastAPI, etc.).
"""

from __future__ import annotations

from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Keep runtime imports minimal; accept both enum and string.
    from resonance.player.client import DeviceType


@dataclass(frozen=True, slots=True)
class StreamingPolicy:
    """
    Streaming policy constants.

    Notes:
    - `ALWAYS_TRANSCODE_FORMATS` are formats we transcode regardless of device tier,
      because they are unreliable to stream directly over HTTP in practice (most
      notably MP4 containers used by m4a/m4b/alac).
    - `NATIVE_STREAM_FORMATS` are formats that generally stream reliably over HTTP.
    - `TRANSCODE_TARGET_FORMAT` is what the server will output when transcoding.
      This must match what `PlayerClient.start_stream()` signals in the `strm` frame.
    """

    ALWAYS_TRANSCODE_FORMATS: AbstractSet[str] = frozenset(
        {
            # MP4 container family (HTTP streaming issues / moov atom / seeking)
            "m4a",
            "m4b",
            "mp4",
            "m4p",
            "m4r",
            # Apple Lossless in MP4 container
            "alac",
            # Treat AAC as problematic by default (many "aac" files are not ADTS-safe)
            "aac",
            # Opus: no Squeezebox hardware/software player can decode Opus natively
            # via HTTP streaming. legacy.conf has `opus mp3` / `opus flc` rules.
            "opus",
            # WavPack: lossless/lossy format, no native Squeezebox HTTP support.
            # Transcoded via ffmpeg (LMS uses wvunpack which we don't ship).
            "wv",
            # Monkey's Audio (APE): lossless format, no native Squeezebox HTTP support.
            # Transcoded via ffmpeg (LMS uses mac which we don't ship).
            "ape",
            # Musepack (MPC): lossy format, no native Squeezebox HTTP support.
            # Transcoded via ffmpeg (LMS uses mppdec which we don't ship).
            "mpc",
        }
    )

    NATIVE_STREAM_FORMATS: AbstractSet[str] = frozenset(
        {
            "mp3",
            "flac",
            "flc",
            "ogg",
            "wav",
            "aiff",
            "aif",
            # WMA: SB2+ hardware can decode WMA natively (legacy devices.toml
            # lists it in native_formats for the legacy tier). Device-specific
            # exceptions (e.g. SLIMP3) are handled by legacy.conf rules.
            "wma",
            # DSD: DSF and DFF are streamed as passthrough to DSD-capable players
            # (Squeezelite, patched SB firmware). LMS convert.conf has
            # `dsf dsf * * → -` and `dff dff * * → -` (passthrough).
            # LMS reference: Slim/Player/Squeezebox.pm stream_s() sends
            # format byte 'd' with sub-format in pcmsamplesize (0=DSF, 1=DFF).
            "dsf",
            "dff",
        }
    )

    # Current transcoding output strategy. Keep aligned with legacy.conf rules.
    #
    # For MP4 container family (m4a/m4b/mp4/alac) we typically transcode. LMS commonly
    # targets MP3 here for fast start and broad player compatibility.
    TRANSCODE_TARGET_FORMAT: str = "mp3"


DEFAULT_POLICY = StreamingPolicy()


def normalize_format(format_hint: str | None) -> str:
    """Normalize a file/format hint to a lowercase extension without dot."""
    if not format_hint:
        return ""
    return str(format_hint).strip().lower().lstrip(".")


def is_always_transcode_format(
    format_hint: str | None, *, policy: StreamingPolicy = DEFAULT_POLICY
) -> bool:
    """Return True if this format should always be transcoded regardless of device."""
    fmt = normalize_format(format_hint)
    return fmt in policy.ALWAYS_TRANSCODE_FORMATS


def is_native_stream_format(
    format_hint: str | None, *, policy: StreamingPolicy = DEFAULT_POLICY
) -> bool:
    """Return True if this format is typically safe to stream directly over HTTP."""
    fmt = normalize_format(format_hint)
    return fmt in policy.NATIVE_STREAM_FORMATS


def needs_transcoding(
    format_hint: str | None,
    device_type: DeviceType | str | None,
    *,
    policy: StreamingPolicy = DEFAULT_POLICY,
) -> bool:
    """
    Determine whether the server should transcode for streaming.

    Priority:
    1) Always-transcode list (policy-level, device-independent)
    2) Native-stream list (fast path)
    3) Device config fallback (tier-based), conservative behavior

    This function is intended to be used by the HTTP streaming route.
    """
    fmt = normalize_format(format_hint)

    if not fmt:
        # Unknown => be safe
        return True

    if fmt in policy.ALWAYS_TRANSCODE_FORMATS:
        return True

    if fmt in policy.NATIVE_STREAM_FORMATS:
        return False

    # Unknown format: delegate to device config (still conservative).
    # Kept as a local import to avoid import-time coupling.
    from resonance.config import get_device_config

    device_config = get_device_config()
    return device_config.needs_transcoding(device_type or "unknown", fmt)


def strm_expected_format_hint(
    source_format_hint: str | None,
    device_type: DeviceType | str | None,
    *,
    policy: StreamingPolicy = DEFAULT_POLICY,
) -> str:
    """
    Return the *format hint* that should be signaled in the Slimproto `strm` frame.

    If the HTTP route will transcode, the player must be told to expect the
    transcoded output format (currently MP3).
    """
    if needs_transcoding(source_format_hint, device_type, policy=policy):
        return policy.TRANSCODE_TARGET_FORMAT
    return normalize_format(source_format_hint) or "mp3"
