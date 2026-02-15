"""
LMS Bitmap Font Parser.

Parses the ``.font.bmp`` files shipped with Logitech Media Server into
an in-memory font table that can render strings to column-major bitmaps —
the exact format expected by ``grfe`` / ``grfd`` display commands.

This is a faithful Python re-implementation of
``Slim::Display::Lib::Fonts`` (Fonts.pm) from the LMS codebase.

Font BMP format
===============
Each ``.font.bmp`` is a **monochrome (1-bpp), uncompressed BMP** image.
The image contains all 256 characters of the cp1252 character set laid
out horizontally.  The **bottom row** of the image acts as a delimiter:
black (0) pixels separate individual character glyphs, while white (1)
pixels mark column boundaries within a glyph.

Character 0 is the **inter-character spacing** bitmap.  If it contains
any set pixels, inter-character spacing is suppressed (tight mode).

Character 0x1F (31) is the **extent mask**: a single-column glyph whose
set bits indicate which pixel rows the font actually occupies.

Character 0x0A (10) is the **cursor underline** pattern.

Column-major bitmap encoding
=============================
Each character glyph is stored as a byte string where every
``bytes_per_column`` bytes represent one pixel column.  Within a column
the bits are packed **MSB-first from top row to bottom row**::

    byte 0:  row 0 (MSB) … row 7 (LSB)
    byte 1:  row 8 (MSB) … row 15 (LSB)
    byte 2:  row 16 (MSB) … row 23 (LSB)   # only for 32-pixel displays
    byte 3:  row 24 (MSB) … row 31 (LSB)   # only for 32-pixel displays

This matches the Perl ``pack("B*", ...)`` used in LMS Fonts.pm.
"""

from __future__ import annotations

import logging
import os
import struct
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Font table type: list of 256 byte-strings (one per character ordinal).
# Index 0 = inter-character spacing glyph.
# ---------------------------------------------------------------------------
FontTable = list[bytes]


class FontCache:
    """Parsed font cache — holds all loaded font tables.

    Usage::

        cache = FontCache()
        cache.load_directory(Path("/path/to/Graphics"))
        bits = cache.render_string("standard.2", "Hello")
    """

    def __init__(self) -> None:
        self._fonts: dict[str, FontTable] = {}
        self._heights: dict[str, int] = {}  # font_name → pixel height
        self._extents: dict[str, int] = {}  # font_name → extent value
        # font_name → {"line": [name1, name2], "overlay": ..., "center": ...}
        self._font_hash: dict[str, dict[str, list[str | None]]] = {}

    # -- public API ----------------------------------------------------------

    @property
    def font_names(self) -> list[str]:
        """Return names of all loaded fonts."""
        return list(self._fonts.keys())

    def font_height(self, font_name: str) -> int | None:
        """Return the pixel height of a loaded font (excluding delimiter row)."""
        return self._heights.get(font_name)

    def font_extent(self, font_name: str) -> int:
        """Return the extent value for a font.

        Positive for bottom-line (``.2``) fonts, negative for top-line
        (``.1``) fonts.  Zero if unknown.
        """
        return self._extents.get(font_name, 0)

    def has_font(self, font_name: str) -> bool:
        return font_name in self._fonts

    def load_directory(self, directory: Path | str) -> int:
        """Load all ``.font.bmp`` files from *directory*.

        Returns the number of fonts successfully loaded.
        """
        directory = Path(directory)
        if not directory.is_dir():
            logger.warning("Font directory does not exist: %s", directory)
            return 0

        count = 0
        for bmp_file in sorted(directory.glob("*.font.bmp")):
            font_name = bmp_file.name.removesuffix(".font.bmp")
            try:
                self.load_font(font_name, bmp_file)
                count += 1
            except Exception:
                logger.warning("Failed to load font '%s' from %s", font_name, bmp_file, exc_info=True)
        return count

    def load_font(self, font_name: str, bmp_path: Path | str) -> None:
        """Load a single ``.font.bmp`` file."""
        bmp_path = Path(bmp_path)
        grid, height = _parse_bmp(bmp_path)
        if grid is None:
            raise ValueError(f"Failed to parse BMP: {bmp_path}")

        pixel_height = height - 1  # bottom row is delimiter
        self._heights[font_name] = pixel_height

        font_table = _parse_font(grid)
        self._fonts[font_name] = font_table

        # Compute extent from character 0x1F
        extent = self._compute_extent(font_name)
        self._extents[font_name] = extent

        # Register in font hash (e.g. "standard.2" → hash["standard"])
        self._register_font_hash(font_name)

        logger.debug(
            "Loaded font '%s': height=%d, extent=%d, chars=%d",
            font_name, pixel_height, extent, len(font_table),
        )

    def render_string(self, font_name: str, text: str) -> bytes:
        """Render *text* using *font_name* to a column-major bitmap.

        Returns raw bytes suitable for bitwise-OR composition with other
        lines into a ``grfe`` / ``grfd`` frame.

        This is the Python equivalent of ``Slim::Display::Lib::Fonts::string()``.
        """
        font = self._fonts.get(font_name)
        if font is None:
            logger.warning("Font '%s' not loaded, returning empty bitmap", font_name)
            return b""

        interspace = font[0] if len(font) > 0 else b""
        # If the interspace glyph has any set bits, suppress spacing
        if interspace and _has_set_bits(interspace):
            interspace = b""

        parts: list[bytes] = []
        chars = list(text.encode("cp1252", errors="replace"))
        remaining = len(chars)

        for ordinal in chars:
            remaining -= 1
            glyph = font[ordinal] if ordinal < len(font) else b""
            parts.append(glyph)
            # Add inter-character space except after the last character
            if remaining > 0 and interspace:
                parts.append(interspace)

        return b"".join(parts)

    def render_string_extended(self, font_name: str, text: str) -> tuple[bool, bytes]:
        """Render *text* with support for embedded font-change sequences.

        Returns ``(reverse, bitmap)`` matching LMS ``Fonts::string()``.
        Currently *reverse* is always ``False`` (BiDi not implemented).

        Supported control characters:
        - ``\\x1d`` (29) — tight mode ON  (suppress inter-char space)
        - ``\\x1c`` (28) — tight mode OFF (restore inter-char space)
        - ``\\x1b`` (27) — font change (``\\x1b<fontname>\\x1b``)
        - ``\\x0a`` (10) — cursor position marker
        """
        font = self._fonts.get(font_name)
        if font is None:
            return (False, b"")

        interspace = font[0] if font else b""
        if interspace and _has_set_bits(interspace):
            interspace = b""

        parts: list[bytes] = []
        tight = False
        font_change = False
        new_font_name_parts: list[str] = []
        current_font = font
        current_interspace = interspace
        cursor_pos = False

        encoded = text.encode("cp1252", errors="replace")
        remaining = len(encoded)

        for byte_val in encoded:
            remaining -= 1

            if font_change:
                if byte_val == 27:
                    # End of font name — switch font
                    new_name = "".join(new_font_name_parts)
                    if new_name and new_name in self._fonts:
                        current_font = self._fonts[new_name]
                    else:
                        current_font = font
                    if tight:
                        current_interspace = b""
                    else:
                        current_interspace = current_font[0] if current_font else b""
                        if current_interspace and _has_set_bits(current_interspace):
                            current_interspace = b""
                    font_change = False
                    new_font_name_parts.clear()
                else:
                    new_font_name_parts.append(chr(byte_val))
                continue

            if byte_val == 27:
                font_change = True
                continue

            if byte_val == 29:  # tight ON
                current_interspace = b""
                tight = True
                continue

            if byte_val == 28:  # tight OFF
                tight = False
                current_interspace = current_font[0] if current_font else b""
                if current_interspace and _has_set_bits(current_interspace):
                    current_interspace = b""
                continue

            if byte_val == 10:  # cursor position
                cursor_pos = True
                continue

            glyph = current_font[byte_val] if byte_val < len(current_font) else b""

            if cursor_pos and glyph:
                # OR cursor underline onto the glyph
                cursor_glyph = font[10] if 10 < len(font) else b""
                if cursor_glyph:
                    glyph_len = len(glyph)
                    # Tile cursor pattern to match glyph width
                    cursor_len = len(cursor_glyph)
                    if cursor_len > 0:
                        tiled = (cursor_glyph * ((glyph_len // cursor_len) + 1))[:glyph_len]
                        glyph = _bitmap_or(glyph, tiled)
                    # Pad narrow glyphs so cursor is visible
                    if glyph_len < 3 * len(current_interspace) and current_interspace:
                        glyph = current_interspace + glyph + current_interspace
                cursor_pos = False

            parts.append(glyph)
            if remaining > 0 and current_interspace:
                parts.append(current_interspace)

        return (False, b"".join(parts))

    def measure_text(self, font_name: str, text: str) -> int:
        """Measure the width of *text* in pixels using *font_name*.

        Returns 0 if the font is not loaded or has zero height.
        """
        height = self._heights.get(font_name)
        if not height:
            return 0
        bits = self.render_string(font_name, text)
        bytes_per_col = height // 8
        if height % 8:
            bytes_per_col += 1
        if bytes_per_col == 0:
            return 0
        return len(bits) // bytes_per_col

    def get_font_hash(self) -> dict[str, dict[str, list[str | None]]]:
        """Return the font hash mapping base names to line/overlay/center font names.

        Structure matches LMS ``$fonthash``::

            {
                "standard": {
                    "line":    ["standard.1", "standard.2"],
                    "overlay": ["standard.1", "standard.2"],
                    "center":  ["standard.1", "standard.2"],
                },
                ...
            }
        """
        return dict(self._font_hash)

    # -- internal ------------------------------------------------------------

    def _compute_extent(self, font_name: str) -> int:
        """Compute extent from character 0x1F (bitmask of valid rows).

        Positive for ``.2`` fonts, negative for ``.1`` fonts.
        """
        font = self._fonts.get(font_name)
        if font is None or len(font) < 0x20:
            return 0

        extent_bytes = font[0x1F]
        if not extent_bytes:
            return 0

        # Count set bits
        extent = sum(bin(b).count("1") for b in extent_bytes)

        if ".1" in font_name:
            extent = -extent

        return extent

    def _register_font_hash(self, font_name: str) -> None:
        """Register a font in the font hash under its base name."""
        # e.g. "standard.2" → base="standard", line_index=1
        if "." not in font_name:
            return

        parts = font_name.rsplit(".", 1)
        if len(parts) != 2:
            return

        base_name = parts[0]
        try:
            line_num = int(parts[1])
        except ValueError:
            return

        if line_num < 1 or line_num > 3:
            return

        idx = line_num - 1  # 0-based

        if base_name not in self._font_hash:
            self._font_hash[base_name] = {
                "line": [None, None, None],
                "overlay": [None, None, None],
                "center": [None, None, None],
            }

        entry = self._font_hash[base_name]
        for component in ("line", "overlay", "center"):
            while len(entry[component]) <= idx:
                entry[component].append(None)
            entry[component][idx] = font_name


# ---------------------------------------------------------------------------
# BMP parsing — matches Slim::Display::Lib::Fonts::parseBMP()
# ---------------------------------------------------------------------------


def _parse_bmp(path: Path) -> tuple[list[list[int]] | None, int]:
    """Parse a monochrome uncompressed BMP file into a 2D pixel grid.

    Returns ``(grid, height)`` where *grid* is a list of rows (top-to-bottom),
    each row a list of 0/1 ints.  Returns ``(None, 0)`` on error.
    """
    data = path.read_bytes()

    if len(data) < 62:
        logger.warning("BMP file too small: %s", path)
        return None, 0

    # BMP header
    bm_type = data[0:2]
    if bm_type != b"BM":
        logger.warning("Not a BMP file (no BM header): %s", path)
        return None, 0

    file_size = struct.unpack_from("<I", data, 2)[0]
    pixel_offset = struct.unpack_from("<I", data, 10)[0]

    # DIB header (BITMAPINFOHEADER)
    bi_width = struct.unpack_from("<i", data, 18)[0]
    bi_height = struct.unpack_from("<i", data, 22)[0]
    bi_planes = struct.unpack_from("<H", data, 26)[0]
    bi_bit_count = struct.unpack_from("<H", data, 28)[0]
    bi_compression = struct.unpack_from("<I", data, 30)[0]

    if bi_planes != 1:
        logger.warning("BMP planes must be 1, got %d: %s", bi_planes, path)
        return None, 0

    if bi_bit_count != 1:
        logger.warning("BMP must be 1-bpp, got %d: %s", bi_bit_count, path)
        return None, 0

    if bi_compression != 0:
        logger.warning("BMP must be uncompressed: %s", path)
        return None, 0

    # Determine palette order (first palette entry)
    # If first palette entry is 0xFFFFFF → normal palette
    # If first palette entry is 0x000000 → reversed palette
    bi_first_palette = struct.unpack_from("<I", data, 54)[0] & 0x00FFFFFF
    reversed_palette = (bi_first_palette != 0x00FFFFFF)

    # BMP height can be negative (top-down) but font BMPs are always bottom-up
    abs_height = abs(bi_height)
    top_down = bi_height < 0

    # Each scanline is padded to 4-byte boundary
    # For 1-bpp: bits_per_line rounded up to multiple of 32
    bits_per_line = bi_width
    if bits_per_line % 32:
        bits_per_line = bits_per_line + (32 - (bits_per_line % 32))
    bytes_per_line = bits_per_line // 8

    pixel_data = data[pixel_offset:]

    grid: list[list[int]] = []

    for i in range(abs_height):
        row_data = pixel_data[i * bytes_per_line: (i + 1) * bytes_per_line]
        row: list[int] = []

        for j in range(bi_width):
            byte_idx = j // 8
            bit_idx = 7 - (j % 8)

            if byte_idx < len(row_data):
                bit_val = (row_data[byte_idx] >> bit_idx) & 1
            else:
                bit_val = 0

            if reversed_palette:
                bit_val = 1 - bit_val

            row.append(bit_val)

        grid.append(row)

    # BMP stores rows bottom-to-top (unless top-down)
    if not top_down:
        grid.reverse()

    return grid, abs_height


# ---------------------------------------------------------------------------
# Font table parsing — matches Slim::Display::Lib::Fonts::parseFont()
# ---------------------------------------------------------------------------


def _parse_font(grid: list[list[int]]) -> FontTable:
    """Parse a pixel grid into a font table.

    The bottom row contains delimiters: columns with value 0 (black)
    separate characters.  Each character's bitmap is stored as a
    column-major byte string.

    Returns a list of up to 256 byte-strings indexed by character ordinal.
    """
    if not grid:
        return [b""] * 256

    bottom_index = len(grid) - 1
    bottom_row = grid[bottom_index]
    glyph_height = bottom_index  # Exclude delimiter row

    width = len(bottom_row)

    font_table: FontTable = []
    char_index = -1
    i = 0

    while i < width:
        # Skip delimiter columns (bottom_row[i] == 1, white)
        if bottom_row[i]:
            i += 1
            continue

        # We've found the start of a character (bottom_row[i] == 0, black)
        char_index += 1

        column_bits: list[str] = []

        while i < width and not bottom_row[i]:
            # Collect pixel column (top to bottom, excluding delimiter row)
            for j in range(glyph_height):
                column_bits.append("1" if grid[j][i] else "0")
            i += 1

        # Pack bits into bytes (MSB first, matching Perl pack("B*", ...))
        glyph_bytes = _bits_to_bytes(column_bits)

        if char_index == 0 and _has_set_bits(glyph_bytes):
            # Character 0 with set pixels → suppress interspace
            glyph_bytes = b""

        font_table.append(glyph_bytes)

        if char_index >= 255:
            break

    # Pad to 256 entries
    while len(font_table) < 256:
        font_table.append(b"")

    return font_table


# ---------------------------------------------------------------------------
# Bit/byte utilities
# ---------------------------------------------------------------------------


def _bits_to_bytes(bits: list[str]) -> bytes:
    """Pack a list of '0'/'1' strings into bytes, MSB first.

    Matches Perl ``pack("B*", join('', @bits))``.
    Pads the last byte with zeros if the bit count is not a multiple of 8.
    """
    if not bits:
        return b""

    bit_string = "".join(bits)
    # Pad to full bytes
    remainder = len(bit_string) % 8
    if remainder:
        bit_string += "0" * (8 - remainder)

    result = bytearray()
    for i in range(0, len(bit_string), 8):
        byte_val = int(bit_string[i:i + 8], 2)
        result.append(byte_val)
    return bytes(result)


def _has_set_bits(data: bytes) -> bool:
    """Check if any bits are set in the byte string."""
    for b in data:
        if b:
            return True
    return False


def _bitmap_or(a: bytes, b: bytes) -> bytes:
    """Bitwise-OR two byte strings.  Result length = max(len(a), len(b))."""
    la = len(a)
    lb = len(b)
    length = max(la, lb)
    result = bytearray(length)
    for i in range(la):
        result[i] |= a[i]
    for i in range(lb):
        result[i] |= b[i]
    return bytes(result)


# ---------------------------------------------------------------------------
# Convenience: singleton / default cache
# ---------------------------------------------------------------------------

_default_cache: FontCache | None = None


def get_font_cache() -> FontCache:
    """Return the global font cache singleton (created on first call)."""
    global _default_cache
    if _default_cache is None:
        _default_cache = FontCache()
    return _default_cache


def load_fonts_from_lms(lms_path: Path | str) -> FontCache:
    """Load fonts from an LMS installation's Graphics directory.

    Looks for ``Graphics/`` under the given LMS root path.
    Returns the populated font cache (also sets it as the default).
    """
    global _default_cache
    lms_path = Path(lms_path)
    graphics_dir = lms_path / "Graphics"

    if not graphics_dir.is_dir():
        raise FileNotFoundError(f"LMS Graphics directory not found: {graphics_dir}")

    cache = FontCache()
    count = cache.load_directory(graphics_dir)
    logger.info("Loaded %d fonts from %s", count, graphics_dir)

    _default_cache = cache
    return cache
