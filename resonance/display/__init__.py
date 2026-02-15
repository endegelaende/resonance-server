"""
Display Rendering for Squeezebox Graphics Displays.

.. versionchanged:: Phase 3–4
   Added ``ScreensaverType`` enum for screensaver mode selection.

This package provides server-side bitmap rendering for Squeezebox devices
with graphic displays (SB2/3, Transporter, Boom, SB1/SqueezeboxG).

Architecture mirrors the LMS display stack:
- ``fonts``    — BMP font parser (LMS ``.font.bmp`` format)
- ``renderer`` — text→bitmap rendering + screen composition
- ``manager``  — per-player display state, update loop, event integration

Display Specifications (from LMS source):

===================  ==========  ================  ===========  =========  =======
Device               Resolution  bytesPerColumn    Frame bytes  Command    Fonts
===================  ==========  ================  ===========  =========  =======
Squeezebox2/3        320 × 32    4                 1280         ``grfe``   standard, light, full
Transporter          320 × 32    4 (×2 screens)    2 × 1280     ``grfe``   standard, light, full
Boom                 160 × 32    4                 640          ``grfe``   standard_n, light_n, full_n
SqueezeboxG (SB1)    280 × 16    2                 560          ``grfd``   small, medium, large, huge
===================  ==========  ================  ===========  =========  =======

Bitmap format (column-major):
  Data is organized by columns (x positions), left-to-right.
  Each column is ``bytes_per_column`` bytes.  Within a column, bits are
  packed MSB-first from top row (row 0) to bottom row.  Multiple text
  lines are combined by bitwise-OR of their rendered bitmaps (each font
  already encodes its vertical position within the column height).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ScreensaverType(Enum):
    """Available screensaver modes for graphic displays.

    Mirrors LMS screensaver plugin registration
    (``Slim::Buttons::Common::addSaver``).
    """

    CLOCK = "clock"                 # Digital clock — date + time (DateTime plugin)
    BLANK = "blank"                 # Display off / all-black
    NOW_PLAYING_MINI = "nowplaying" # Simplified now-playing (title only, no progress)
    NONE = "none"                   # No screensaver — stay on last screen


class DisplayModel(Enum):
    """Known graphic display models, matching LMS ``vfdmodel``."""

    GRAPHIC_320x32 = "graphic-320x32"   # SB2, SB3, Transporter
    GRAPHIC_160x32 = "graphic-160x32"   # Boom
    GRAPHIC_280x16 = "graphic-280x16"   # SqueezeboxG / SB1
    NONE = "none"                        # No display (Receiver, etc.)


@dataclass(frozen=True)
class DisplaySpec:
    """Hardware display specification for a device model.

    All values are derived from the corresponding LMS ``Display/*.pm``
    classes (``displayWidth``, ``displayHeight``, ``bytesPerColumn``,
    ``graphicCommand``).
    """

    model: DisplayModel
    width: int
    height: int
    bytes_per_column: int
    frame_command: str  # "grfe" or "grfd"
    has_screen2: bool = False
    screen2_offset: int = 0

    @property
    def frame_bytes(self) -> int:
        """Total bytes for one screen frame."""
        return self.width * self.bytes_per_column

    @property
    def total_frame_bytes(self) -> int:
        """Total bytes including both screens (if dual-screen)."""
        return self.frame_bytes * (2 if self.has_screen2 else 1)


# ── Pre-defined specs ────────────────────────────────────────────────

DISPLAY_SB2 = DisplaySpec(
    model=DisplayModel.GRAPHIC_320x32,
    width=320,
    height=32,
    bytes_per_column=4,
    frame_command="grfe",
)

DISPLAY_TRANSPORTER = DisplaySpec(
    model=DisplayModel.GRAPHIC_320x32,
    width=320,
    height=32,
    bytes_per_column=4,
    frame_command="grfe",
    has_screen2=True,
    screen2_offset=640,
)

DISPLAY_BOOM = DisplaySpec(
    model=DisplayModel.GRAPHIC_160x32,
    width=160,
    height=32,
    bytes_per_column=4,
    frame_command="grfe",
)

DISPLAY_SBG = DisplaySpec(
    model=DisplayModel.GRAPHIC_280x16,
    width=280,
    height=16,
    bytes_per_column=2,
    frame_command="grfd",
)

DISPLAY_NONE = DisplaySpec(
    model=DisplayModel.NONE,
    width=0,
    height=0,
    bytes_per_column=0,
    frame_command="",
)

# Map from vfdmodel string → DisplaySpec
DISPLAY_SPECS: dict[str, DisplaySpec] = {
    "graphic-320x32": DISPLAY_SB2,
    "graphic-160x32": DISPLAY_BOOM,
    "graphic-280x16": DISPLAY_SBG,
}


def display_spec_for_model(model_name: str) -> DisplaySpec:
    """Look up the display spec for a device model string.

    Accepts LMS ``vfdmodel`` strings (``graphic-320x32``, etc.) as well
    as device type names (``squeezebox2``, ``transporter``, ``boom``, etc.).
    Returns ``DISPLAY_NONE`` for unknown / display-less devices.
    """
    # Direct vfdmodel match
    if model_name in DISPLAY_SPECS:
        return DISPLAY_SPECS[model_name]

    # Device-type name mapping
    name = model_name.lower()
    if name in ("transporter",):
        return DISPLAY_TRANSPORTER
    if name in ("squeezebox2", "squeezebox3", "sb2", "sb3"):
        return DISPLAY_SB2
    if name in ("boom", "softboom"):
        return DISPLAY_BOOM
    if name in ("squeezebox", "squeezeboxg", "sb1", "slimp3"):
        return DISPLAY_SBG

    return DISPLAY_NONE


@dataclass
class FontConfig:
    """Font selection for a display, matching LMS ``activeFont`` prefs.

    For SB2/3/Transporter the default font set is:
      ``["light", "standard", "full"]`` (index 1 = standard = default).

    For Boom (narrow display):
      ``["light_n", "standard_n", "full_n"]``.

    For SqueezeboxG (16-pixel display):
      ``["small", "medium", "large", "huge"]`` (index 1 = medium = default).

    Each font name has a ``.1`` (top line) and ``.2`` (bottom line) variant.
    """

    font_names: list[str] = field(default_factory=lambda: ["light", "standard", "full"])
    active_index: int = 1

    @property
    def active_font(self) -> str:
        """Currently selected font base name."""
        if 0 <= self.active_index < len(self.font_names):
            return self.font_names[self.active_index]
        return self.font_names[0] if self.font_names else "standard"

    @property
    def line1_font(self) -> str:
        """Font name for line 1 (top)."""
        return f"{self.active_font}.1"

    @property
    def line2_font(self) -> str:
        """Font name for line 2 (bottom)."""
        return f"{self.active_font}.2"


# Default font configs per display model
DEFAULT_FONTS: dict[DisplayModel, FontConfig] = {
    DisplayModel.GRAPHIC_320x32: FontConfig(
        font_names=["light", "standard", "full"],
        active_index=1,
    ),
    DisplayModel.GRAPHIC_160x32: FontConfig(
        font_names=["light_n", "standard_n", "full_n"],
        active_index=1,
    ),
    DisplayModel.GRAPHIC_280x16: FontConfig(
        font_names=["small", "medium", "large", "huge"],
        active_index=1,
    ),
}


def default_font_config(spec: DisplaySpec) -> FontConfig:
    """Return the default font config for a display spec."""
    cfg = DEFAULT_FONTS.get(spec.model)
    if cfg is not None:
        # Return a copy so callers can mutate
        return FontConfig(
            font_names=list(cfg.font_names),
            active_index=cfg.active_index,
        )
    return FontConfig()


# ── Screensaver font overrides ───────────────────────────────────────
#
# LMS DateTime plugin uses specific overlay fonts per display model
# (see ``Slim/Plugin/DateTime/Plugin.pm`` ``$fontDef``).
# These are used when rendering the clock screensaver so that the
# overlay (alarm bell, etc.) uses the correct font size.

SCREENSAVER_FONT_OVERRIDES: dict[str, dict[str, list[str | None]]] = {
    "graphic-320x32": {"overlay": ["standard.1"]},
    "graphic-160x32": {"overlay": ["standard.1"]},
    "graphic-280x16": {"overlay": ["small.1"]},
}


__all__ = [
    "DisplayModel",
    "DisplaySpec",
    "FontConfig",
    "ScreensaverType",
    "DISPLAY_SB2",
    "DISPLAY_TRANSPORTER",
    "DISPLAY_BOOM",
    "DISPLAY_SBG",
    "DISPLAY_NONE",
    "DISPLAY_SPECS",
    "SCREENSAVER_FONT_OVERRIDES",
    "display_spec_for_model",
    "default_font_config",
    "DEFAULT_FONTS",
]
