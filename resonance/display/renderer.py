"""
Display Renderer — Screen Composition for Squeezebox Graphics Displays.

This module provides the core rendering pipeline that converts high-level
screen descriptions (text lines, overlays, progress bars) into raw
column-major bitmaps ready for ``grfe`` / ``grfd`` transmission.

Architecture mirrors ``Slim::Display::Graphics::render()`` from LMS:

1. Render individual text lines → column-major bitmaps via ``FontCache``
2. Render overlays (right-aligned text, e.g. elapsed time)
3. Compose lines + overlays into a single screen bitmap using bitwise-OR
4. Optionally add progress bar, centered text
5. Output: raw ``bytes`` of exactly ``DisplaySpec.frame_bytes`` length

Screen model
============
A screen is described by a ``ScreenParts`` dict with up to 3 text lines,
overlays, and centered text — matching the LMS display hash::

    {
        "line":    ["Top line text", "Bottom line text"],
        "overlay": [None, "3:42"],
        "center":  [None, None],
    }

For ``full`` fonts (single-line mode), only ``line[1]`` is used and
rendered at full display height.

Progress bar
============
The progress bar is rendered as a horizontal bar at the bottom 3 pixel
rows of the display.  ``render_progress_bar()`` generates the bitmap
independently; the caller ORs it into the final frame.

Now-Playing screen
==================
``render_now_playing()`` is a convenience function that assembles the
standard playback screen: track title (line 1), artist — album (line 2),
optional elapsed/remaining overlay, optional progress bar.

Phase 3 — Menu rendering
=========================
``render_menu_advanced()`` provides multi-page menu support with a
position indicator overlay (``"2/10"``), optional right-arrow markers
for items that have sub-menus, and configurable visible-window tracking.

``render_slider_bar()`` / ``render_volume_overlay()`` implement the
LMS ``sliderBar`` / ``simpleSliderBar`` equivalent for volume and
settings displays.

Phase 4 — Screensaver
=====================
``render_clock()`` renders a digital clock screensaver (date + time,
centered) matching the LMS DateTime plugin output.

``render_now_playing_mini()`` renders a minimal now-playing screensaver
(title only, centered, no progress bar).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from resonance.display import DisplaySpec, FontConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Screen description types
# ---------------------------------------------------------------------------


@dataclass
class ScreenParts:
    """High-level description of what to render on a display screen.

    Matches the LMS ``$parts`` hash passed to ``Graphics::render()``.

    Each list can have up to 3 entries (for 3-line font modes), but
    typically only indices 0 and 1 are used (2-line mode).
    """

    line: list[str | None] = field(default_factory=lambda: [None, None])
    overlay: list[str | None] = field(default_factory=lambda: [None, None])
    center: list[str | None] = field(default_factory=lambda: [None, None])


@dataclass
class RenderedScreen:
    """Result of rendering a screen.

    Attributes:
        bitmap: Raw column-major bitmap bytes (exactly ``frame_bytes``).
        scroll_line: Index of the line that needs scrolling, or -1.
        scroll_bitmap: Full (un-truncated) bitmap for the scrolling line.
        scroll_width: Width of scroll_bitmap in pixels.
    """

    bitmap: bytes
    scroll_line: int = -1
    scroll_bitmap: bytes = b""
    scroll_width: int = 0


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


class DisplayRenderer:
    """Stateless renderer that converts ``ScreenParts`` into bitmaps.

    Requires a ``FontCache`` for text rendering and a ``DisplaySpec``
    for frame geometry.  One renderer instance is shared across all
    screens for a given display model.
    """

    def __init__(
        self,
        font_cache: Any,  # resonance.display.fonts.FontCache
        spec: DisplaySpec,
        font_config: FontConfig | None = None,
    ) -> None:
        from resonance.display import default_font_config

        self._fonts = font_cache
        self._spec = spec
        self._font_config = font_config or default_font_config(spec)

    @property
    def spec(self) -> DisplaySpec:
        return self._spec

    @property
    def font_config(self) -> FontConfig:
        return self._font_config

    @font_config.setter
    def font_config(self, value: FontConfig) -> None:
        self._font_config = value

    # -- Main render entry point ---------------------------------------------

    def render(self, parts: ScreenParts, *, allow_scroll: bool = True) -> RenderedScreen:
        """Render a ``ScreenParts`` into a ``RenderedScreen``.

        This is the Python equivalent of ``Slim::Display::Graphics::render()``.

        Steps:
        1. Render each ``line[i]`` to a column-major bitmap.
        2. Render each ``overlay[i]`` to a column-major bitmap (right-aligned).
        3. For each line, check if line + overlay fit; if not, mark for scroll.
        4. Compose all bitmaps via bitwise-OR into a single frame.
        5. Render centered text and OR it in.
        6. Pad/truncate to exactly ``frame_bytes``.
        """
        screen_size = self._spec.frame_bytes
        if screen_size <= 0:
            return RenderedScreen(bitmap=b"")

        bpc = self._spec.bytes_per_column

        # Render individual components
        max_line = self._max_lines - 1
        line_bits: list[bytes] = []
        line_widths: list[int] = []
        overlay_bits: list[bytes] = []
        overlay_widths: list[int] = []

        for i in range(max_line + 1):
            font_name = self._font_for_line(i)
            # Line text
            text = parts.line[i] if i < len(parts.line) else None
            if text:
                rendered = self._fonts.render_string(font_name, text)
                line_bits.append(rendered)
                line_widths.append(len(rendered))
            else:
                line_bits.append(b"")
                line_widths.append(0)

            # Overlay text (prepend a null byte for leading space, like LMS)
            overlay_text = parts.overlay[i] if i < len(parts.overlay) else None
            if overlay_text:
                rendered = self._fonts.render_string(font_name, "\x00" + overlay_text)
                # Truncate overlay to screen width
                if len(rendered) > screen_size:
                    rendered = rendered[:screen_size]
                overlay_bits.append(rendered)
                overlay_widths.append(len(rendered))
            else:
                overlay_bits.append(b"")
                overlay_widths.append(0)

        # In single-line mode (e.g. "full" font), if line[1] has text but
        # line[0] is empty, copy line[1] to line[0] (matching LMS behavior).
        if max_line >= 1:
            font_line0 = self._font_for_line(0)
            if not self._fonts.has_font(font_line0):
                # Single-line font — line[0] font doesn't exist
                if not line_bits[1] and line_bits[0]:
                    line_bits[1] = line_bits[0]
                    line_widths[1] = line_widths[0]

        # Compose: for each line, determine if it scrolls or gets truncated
        scroll_line = -1
        scroll_bitmap = b""
        scroll_width = 0

        frame = bytearray(screen_size)

        for i in range(max_line, -1, -1):  # Prefer scrolling lower lines
            lb = line_bits[i]
            ob = overlay_bits[i]
            lb_len = line_widths[i]
            ob_len = overlay_widths[i]
            overlay_start = screen_size - ob_len

            if not lb and not ob and i > 0:
                # Blank line (except line 0 which gives blank screen)
                continue

            if lb_len <= overlay_start:
                # Line + overlay fit — assemble: line + padding + overlay
                padded = lb + b"\x00" * (overlay_start - lb_len) + ob
                _or_into(frame, padded)

            elif allow_scroll and i > 0 and scroll_line < 0:
                # Line too long — mark for scrolling, only show overlay
                scroll_line = i
                scroll_bitmap = lb
                scroll_width = lb_len // bpc if bpc else 0

                # Static frame: just the overlay (right-aligned)
                if ob:
                    padded = b"\x00" * overlay_start + ob
                    _or_into(frame, padded)
            else:
                # Truncate the line to fit before the overlay
                if overlay_start > 0:
                    truncated = lb[:overlay_start]
                else:
                    truncated = b""
                padded = truncated + b"\x00" * (overlay_start - len(truncated)) + ob
                _or_into(frame, padded)

        # Centered text
        for i in range(max_line + 1):
            center_text = parts.center[i] if i < len(parts.center) else None
            if center_text:
                font_name = self._font_for_line(i)
                rendered = self._fonts.render_string(font_name, center_text)
                if rendered:
                    # Center horizontally
                    center_pad = (screen_size - len(rendered)) // (bpc * 2) * bpc
                    if center_pad < 0:
                        center_pad = 0
                    centered = b"\x00" * center_pad + rendered
                    # Truncate to screen
                    centered = centered[:screen_size]
                    _or_into(frame, centered)

        # Ensure exact size
        result = bytes(frame[:screen_size])
        if len(result) < screen_size:
            result = result + b"\x00" * (screen_size - len(result))

        return RenderedScreen(
            bitmap=result,
            scroll_line=scroll_line,
            scroll_bitmap=scroll_bitmap,
            scroll_width=scroll_width,
        )

    # -- Convenience renderers -----------------------------------------------

    def render_now_playing(
        self,
        *,
        title: str = "",
        artist: str = "",
        album: str = "",
        elapsed_s: float = 0.0,
        duration_s: float = 0.0,
        show_elapsed: bool = True,
        show_progress: bool = True,
        is_paused: bool = False,
    ) -> RenderedScreen:
        """Render a standard now-playing screen.

        Line 1 (top): track title
        Line 2 (bottom): artist — album
        Overlay 2: elapsed time (or remaining)
        Progress bar: at bottom of frame (if enabled)
        """
        # Build line 2: "Artist — Album" or just one
        line2_parts = []
        if artist:
            line2_parts.append(artist)
        if album:
            line2_parts.append(album)
        line2 = " \u2014 ".join(line2_parts) if line2_parts else ""

        # Build overlay: elapsed time
        overlay2 = None
        if show_elapsed and duration_s > 0:
            overlay2 = _format_time(elapsed_s)

        parts = ScreenParts(
            line=[title, line2],
            overlay=[None, overlay2],
        )

        result = self.render(parts)

        # Add progress bar if requested
        if show_progress and duration_s > 0:
            fraction = min(max(elapsed_s / duration_s, 0.0), 1.0)
            bar_bitmap = self.render_progress_bar(fraction)
            result = RenderedScreen(
                bitmap=_bitmap_or_full(result.bitmap, bar_bitmap),
                scroll_line=result.scroll_line,
                scroll_bitmap=result.scroll_bitmap,
                scroll_width=result.scroll_width,
            )

        return result

    def render_idle(
        self,
        *,
        text: str = "",
        center: bool = True,
    ) -> RenderedScreen:
        """Render an idle screen (e.g. player name or clock)."""
        if center:
            parts = ScreenParts(center=[None, text or ""])
        else:
            parts = ScreenParts(line=[None, text or ""])
        return self.render(parts, allow_scroll=False)

    def render_menu(
        self,
        items: list[str],
        *,
        selected_index: int = 0,
        offset: int = 0,
    ) -> RenderedScreen:
        """Render a simple 2-line menu view.

        Shows the selected item on line 2 and (if available) the previous
        item on line 1.  A cursor marker is prepended to the selected item.
        """
        visible_above = ""
        visible_selected = ""

        if items:
            idx = max(0, min(selected_index, len(items) - 1))
            visible_selected = "\x0a" + items[idx]  # cursor marker
            if idx > 0:
                visible_above = items[idx - 1]

        parts = ScreenParts(
            line=[visible_above, visible_selected],
        )
        return self.render(parts, allow_scroll=True)

    def render_menu_advanced(
        self,
        items: list[str],
        *,
        selected_index: int = 0,
        has_submenu: list[bool] | None = None,
        show_position: bool = True,
    ) -> RenderedScreen:
        """Render a 2-line menu with position indicator and sub-menu arrows.

        This extends ``render_menu()`` with LMS-style enhancements:

        - **Position indicator** (overlay on line 1): ``"2/10"`` showing
          the current item number and total count, matching the LMS
          ``headerAddCount`` behaviour in ``Input::List``.
        - **Right-arrow overlay** (overlay on line 2): ``"\\x02"``
          (rightarrow symbol) appended when *has_submenu[selected_index]*
          is ``True``, matching LMS ``overlayRef`` convention.
        - **Cursor marker**: ``"\\x0a"`` prepended to the selected item
          (same as ``render_menu``).

        Parameters
        ----------
        items:
            List of menu item labels.
        selected_index:
            Currently highlighted item (0-based).
        has_submenu:
            Per-item flag indicating whether the item opens a sub-menu.
            If ``None``, no right-arrow is shown for any item.
        show_position:
            Whether to show ``"X/N"`` position indicator on line 1.
        """
        if not items:
            return self.render(ScreenParts(), allow_scroll=False)

        total = len(items)
        idx = max(0, min(selected_index, total - 1))

        # Line 1: previous item (context)
        visible_above = items[idx - 1] if idx > 0 else ""

        # Line 2: selected item with cursor marker
        visible_selected = "\x0a" + items[idx]

        # Overlay 1: position indicator  "X/N"
        overlay1: str | None = None
        if show_position and total > 1:
            overlay1 = f"{idx + 1}/{total}"

        # Overlay 2: right-arrow for sub-menu items
        overlay2: str | None = None
        if has_submenu is not None and idx < len(has_submenu) and has_submenu[idx]:
            overlay2 = "\x02"  # rightarrow symbol

        parts = ScreenParts(
            line=[visible_above, visible_selected],
            overlay=[overlay1, overlay2],
        )
        return self.render(parts, allow_scroll=True)

    def render_slider_bar(
        self,
        value: float,
        *,
        width_chars: int | None = None,
        midpoint: float = 0.0,
    ) -> RenderedScreen:
        """Render a horizontal slider/progress bar using font symbols.

        This is the Python equivalent of LMS ``Graphics::sliderBar()``.
        The bar is rendered on line 2 using the progress-bar font symbols
        (chars ``\\x03``–``\\x09``).

        Parameters
        ----------
        value:
            Current value in range [0, 100].
        width_chars:
            Width of the bar in characters.  Defaults to display width
            divided by the average character width (roughly 40 for SB2).
        midpoint:
            Position of the midpoint divider (0–100).  Use 0 for a
            simple progress bar (no midpoint).
        """
        value = max(0.0, min(100.0, value))

        if width_chars is None:
            # Estimate: ~8 pixels per progress char on a 320px display
            width_chars = max(10, self._spec.width // 8)

        # Build the bar string using LMS progress symbols:
        # \x03 = progressEnd (cap)
        # \x04 = progress1e (filled end-lobe)
        # \x05 = progress2e (filled near-end)
        # \x06 = progress3e (filled middle)
        # \x07 = progress1 (empty end-lobe)
        # \x08 = progress2 (empty near-end)
        # \x09 = progress3 (empty middle)
        prog_end = "\x03"
        prog1_filled = "\x04"
        prog2_filled = "\x05"
        prog3_filled = "\x06"
        prog1_empty = "\x07"
        prog2_empty = "\x08"
        prog3_empty = "\x09"

        spaces = width_chars - 1  # space for progressEnd cap
        if midpoint > 0:
            spaces -= 1  # extra cap for midpoint divider

        dots = int(value / 100.0 * spaces)
        dots = max(0, dots)

        # Build the bar: tight-mode wrapper + progressEnd + fill/empty + progressEnd
        chart = "\x1d" + prog_end  # tight mode ON + left cap

        for i in range(spaces):
            is_filled = i < dots

            # End-lobe shaping: first and last positions get rounded caps
            if i == 0 or i == spaces - 1:
                chart += prog1_filled if is_filled else prog1_empty
            elif i == 1 or i == spaces - 2:
                chart += prog2_filled if is_filled else prog2_empty
            else:
                chart += prog3_filled if is_filled else prog3_empty

        chart += prog_end + "\x1c"  # right cap + tight mode OFF

        parts = ScreenParts(
            line=[None, chart],
        )
        return self.render(parts, allow_scroll=False)

    def render_volume_overlay(
        self,
        volume: int,
        *,
        label: str = "Volume",
    ) -> RenderedScreen:
        """Render a volume display with label and slider bar.

        Line 1: ``"Volume"`` (or custom label)
        Line 2: slider bar at *volume* percent

        This is intended for ``showBriefly`` overlays, matching the
        LMS volume display behaviour.
        """
        volume = max(0, min(100, volume))

        # Use the pixel-based progress bar for clean rendering
        bar_fraction = volume / 100.0
        bar_bitmap = self.render_progress_bar(bar_fraction, bar_height=5)

        # Render the label on line 1 with volume value overlay
        parts = ScreenParts(
            center=[None, label],
            overlay=[None, f"{volume}"],
        )
        result = self.render(parts, allow_scroll=False)

        # OR in the bar
        return RenderedScreen(
            bitmap=_bitmap_or_full(result.bitmap, bar_bitmap),
            scroll_line=-1,
        )

    def render_blank(self) -> bytes:
        """Return an all-zeros frame (display off / blank screensaver)."""
        return b"\x00" * self._spec.frame_bytes

    # -- Screensaver renderers (Phase 4) ------------------------------------

    def render_clock(
        self,
        *,
        now: datetime | None = None,
        date_format: str = "%A, %d %B",
        time_format: str = "%H:%M",
        alarm_symbol: str | None = None,
    ) -> RenderedScreen:
        """Render a digital clock screensaver.

        Mirrors the LMS DateTime plugin (``Slim/Plugin/DateTime/Plugin.pm``):
        - Line 1 (centered): formatted date (e.g. ``"Monday, 14 February"``)
        - Line 2 (centered): formatted time (e.g. ``"20:35"``)
        - Overlay 1: optional alarm symbol (``\\x10`` = bell, ``\\x11`` = sleep)

        Parameters
        ----------
        now:
            Timestamp to display.  Defaults to ``datetime.now()``.
        date_format:
            ``strftime`` format for the date line.
        time_format:
            ``strftime`` format for the time line.
        alarm_symbol:
            Optional symbol to show in the top-right overlay.
            Use ``"\\x10"`` for alarm bell, ``"\\x11"`` for snooze/sleep.
        """
        if now is None:
            now = datetime.now()

        date_str = now.strftime(date_format)
        time_str = now.strftime(time_format)

        parts = ScreenParts(
            center=[date_str, time_str],
            overlay=[alarm_symbol, None],
        )
        return self.render(parts, allow_scroll=False)

    def render_now_playing_mini(
        self,
        *,
        title: str = "",
        artist: str = "",
    ) -> RenderedScreen:
        """Render a minimal now-playing screensaver.

        Shows only the track title (centered, line 1) and artist
        (centered, line 2) — no elapsed time, no progress bar.
        Intended for idle/screensaver mode where a simplified display
        is appropriate.

        Matches the LMS ``screensaver`` mode that shows the playlist's
        current track info via ``Slim::Buttons::Playlist::lines``.
        """
        parts = ScreenParts(
            center=[title or "", artist or ""],
        )
        return self.render(parts, allow_scroll=False)

    # -- Progress bar --------------------------------------------------------

    def render_progress_bar(
        self,
        fraction: float,
        *,
        bar_height: int = 3,
    ) -> bytes:
        """Render a progress bar at the bottom of the display.

        The bar occupies the bottom *bar_height* pixel rows.
        *fraction* should be in [0.0, 1.0].

        Returns a bitmap of exactly ``frame_bytes`` length.
        """
        width = self._spec.width
        height = self._spec.height
        bpc = self._spec.bytes_per_column
        screen_size = self._spec.frame_bytes

        if screen_size <= 0 or bpc <= 0:
            return b""

        fraction = min(max(fraction, 0.0), 1.0)
        fill_width = int(width * fraction)

        # Build the bar column pattern.
        # The bar occupies the bottom `bar_height` rows of the display.
        # In column-major format, we need to set the appropriate bits.
        bar_col_filled = _make_bar_column(height, bpc, bar_height, filled=True)
        bar_col_empty = _make_bar_column(height, bpc, bar_height, filled=False)

        columns: list[bytes] = []
        for x in range(width):
            if x < fill_width:
                columns.append(bar_col_filled)
            else:
                columns.append(bar_col_empty)

        result = b"".join(columns)
        return result[:screen_size]

    # -- Scroll frame generation ---------------------------------------------

    def build_scroll_frame(
        self,
        static_bitmap: bytes,
        scroll_bitmap: bytes,
        scroll_offset: int,
        overlay_start: int,
    ) -> bytes:
        """Build a single animation frame for scrolling text.

        Composites the scrolling line at *scroll_offset* into the static
        background, truncating at *overlay_start* to preserve the overlay.
        """
        screen_size = self._spec.frame_bytes
        bpc = self._spec.bytes_per_column

        if screen_size <= 0 or bpc <= 0:
            return static_bitmap

        scroll_len = len(scroll_bitmap)

        # Extract the visible portion of the scroll bitmap
        start_byte = scroll_offset * bpc
        end_byte = start_byte + overlay_start

        if start_byte >= scroll_len:
            visible = b""
        elif end_byte > scroll_len:
            visible = scroll_bitmap[start_byte:scroll_len]
        else:
            visible = scroll_bitmap[start_byte:end_byte]

        # Build frame: static background OR visible scroll portion
        frame = bytearray(static_bitmap[:screen_size])
        _or_into(frame, visible)
        return bytes(frame)

    # -- Internal helpers ----------------------------------------------------

    @property
    def _max_lines(self) -> int:
        """Maximum number of text lines this display supports."""
        fc = self._font_config
        active = fc.active_font

        # Check if this is a single-line font (no .1 variant)
        if not self._fonts.has_font(f"{active}.1"):
            return 1

        # Check for 3-line font
        if self._fonts.has_font(f"{active}.3"):
            return 3

        return 2

    def _font_for_line(self, line_index: int) -> str:
        """Return the font name for a given line index (0-based).

        Line 0 → ``<font>.1``, Line 1 → ``<font>.2``, Line 2 → ``<font>.3``.
        """
        active = self._font_config.active_font
        suffix = line_index + 1
        return f"{active}.{suffix}"


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _or_into(target: bytearray, source: bytes) -> None:
    """Bitwise-OR *source* into *target* in-place."""
    for i in range(min(len(target), len(source))):
        target[i] |= source[i]


def _bitmap_or_full(a: bytes, b: bytes) -> bytes:
    """Bitwise-OR two byte strings, returning max-length result."""
    la = len(a)
    lb = len(b)
    length = max(la, lb)
    result = bytearray(length)
    for i in range(la):
        result[i] |= a[i]
    for i in range(lb):
        result[i] |= b[i]
    return bytes(result)


def _make_bar_column(
    display_height: int,
    bytes_per_column: int,
    bar_height: int,
    *,
    filled: bool,
) -> bytes:
    """Create a single column for a progress bar.

    For the filled portion, the bottom *bar_height* rows are set.
    For the empty portion, only the top and bottom border rows are set.
    """
    bits = ["0"] * display_height

    if filled:
        # Fill the bottom bar_height rows
        for row in range(display_height - bar_height, display_height):
            bits[row] = "1"
    else:
        # Empty: just top and bottom border of the bar area
        top_row = display_height - bar_height
        bottom_row = display_height - 1
        if 0 <= top_row < display_height:
            bits[top_row] = "1"
        if 0 <= bottom_row < display_height:
            bits[bottom_row] = "1"

    # Pack to bytes (MSB first, matching column-major format)
    bit_string = "".join(bits)
    # Pad to bytes_per_column * 8 bits
    total_bits = bytes_per_column * 8
    if len(bit_string) < total_bits:
        bit_string += "0" * (total_bits - len(bit_string))
    elif len(bit_string) > total_bits:
        bit_string = bit_string[:total_bits]

    result = bytearray()
    for i in range(0, len(bit_string), 8):
        result.append(int(bit_string[i:i + 8], 2))
    return bytes(result)


def _format_time(seconds: float) -> str:
    """Format seconds as ``M:SS`` or ``H:MM:SS``."""
    total = int(max(seconds, 0))
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"
