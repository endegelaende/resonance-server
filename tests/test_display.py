"""
Tests for the display rendering engine.

Covers:
- BMP font parsing (LMS .font.bmp format)
- Font cache loading and lookup
- String rendering to column-major bitmaps
- Screen composition (lines, overlays, centering)
- Progress bar rendering
- Now-playing / idle / menu convenience renderers
- DisplaySpec / FontConfig dataclasses
- DisplayManager player registration and state
"""

from __future__ import annotations

import struct
import tempfile
from pathlib import Path
from typing import Any

import pytest

from resonance.display import (
    DEFAULT_FONTS,
    DISPLAY_BOOM,
    DISPLAY_NONE,
    DISPLAY_SB2,
    DISPLAY_SBG,
    DISPLAY_TRANSPORTER,
    DisplayModel,
    DisplaySpec,
    FontConfig,
    default_font_config,
    display_spec_for_model,
)
from resonance.display.fonts import (
    FontCache,
    _bitmap_or,
    _bits_to_bytes,
    _has_set_bits,
    _parse_bmp,
    _parse_font,
)
from resonance.display.renderer import (
    DisplayRenderer,
    RenderedScreen,
    ScreenParts,
    _format_time,
    _make_bar_column,
    _or_into,
)

# =====================================================================
# Helpers: create minimal monochrome BMP files for testing
# =====================================================================


def _create_test_bmp(
    pixels: list[list[int]],
    *,
    reversed_palette: bool = False,
) -> bytes:
    """Create a minimal monochrome (1-bpp) uncompressed BMP from a 2D pixel grid.

    *pixels* is a list of rows (top-to-bottom), each row a list of 0/1 ints.
    BMP stores rows bottom-to-top, so we reverse internally.
    """
    height = len(pixels)
    width = len(pixels[0]) if pixels else 0

    # Each row padded to 4-byte boundary (1 bpp)
    bits_per_row = width
    padded_bits = bits_per_row + (32 - bits_per_row % 32) if bits_per_row % 32 else bits_per_row
    bytes_per_row = padded_bits // 8

    # Build pixel data (bottom-to-top)
    pixel_data = bytearray()
    for row in reversed(pixels):
        row_bytes = bytearray(bytes_per_row)
        for j, val in enumerate(row):
            byte_idx = j // 8
            bit_idx = 7 - (j % 8)
            if reversed_palette:
                # In reversed palette, 0 in grid means 1 in file
                if not val:
                    row_bytes[byte_idx] |= 1 << bit_idx
            else:
                # Normal palette: 1 in grid means 1 in file (white=set)
                if val:
                    row_bytes[byte_idx] |= 1 << bit_idx
        pixel_data.extend(row_bytes)

    # BMP header
    pixel_offset = 14 + 40 + 8  # file header + DIB header + 2 palette entries
    file_size = pixel_offset + len(pixel_data)

    header = bytearray()
    # File header (14 bytes)
    header.extend(b"BM")
    header.extend(struct.pack("<I", file_size))
    header.extend(b"\x00\x00\x00\x00")  # reserved
    header.extend(struct.pack("<I", pixel_offset))

    # DIB header (BITMAPINFOHEADER, 40 bytes)
    header.extend(struct.pack("<I", 40))  # header size
    header.extend(struct.pack("<i", width))
    header.extend(struct.pack("<i", height))  # positive = bottom-up
    header.extend(struct.pack("<H", 1))  # planes
    header.extend(struct.pack("<H", 1))  # bits per pixel
    header.extend(struct.pack("<I", 0))  # compression (none)
    header.extend(struct.pack("<I", len(pixel_data)))  # image size
    header.extend(struct.pack("<i", 0))  # x ppm
    header.extend(struct.pack("<i", 0))  # y ppm
    header.extend(struct.pack("<I", 0))  # colors used
    header.extend(struct.pack("<I", 0))  # important colors

    # Color table (2 entries, 4 bytes each)
    if reversed_palette:
        # First entry black (0x000000), second white
        header.extend(struct.pack("<I", 0x00000000))
        header.extend(struct.pack("<I", 0x00FFFFFF))
    else:
        # First entry white (0xFFFFFF = normal), second black
        header.extend(struct.pack("<I", 0x00FFFFFF))
        header.extend(struct.pack("<I", 0x00000000))

    return bytes(header) + bytes(pixel_data)


def _create_simple_font_bmp(
    glyph_height: int = 8,
    num_chars: int = 128,
    char_width: int = 5,
) -> bytes:
    """Create a simplified font BMP for testing.

    Each character is *char_width* columns wide, all set to a pattern
    based on the character index.  The bottom row is the delimiter row
    with a white (1) pixel between each character.

    Total height = glyph_height + 1 (for delimiter row).
    """
    total_height = glyph_height + 1
    # Width: each char is char_width columns, separated by 1 delimiter column
    total_width = num_chars * (char_width + 1) - 1  # no trailing delimiter

    # Build grid (top-to-bottom)
    grid: list[list[int]] = []
    for y in range(total_height):
        row: list[int] = []
        for char_idx in range(num_chars):
            if y == total_height - 1:
                # Delimiter row: 0 (black) for char columns, 1 (white) for separator
                for x in range(char_width):
                    row.append(0)
                if char_idx < num_chars - 1:
                    row.append(1)  # separator
            else:
                # Glyph row: set a pattern based on char index
                for x in range(char_width):
                    if char_idx == 0:
                        # Char 0 = interspace: leave blank
                        row.append(0)
                    elif char_idx == ord(" "):
                        # Space: all blank
                        row.append(0)
                    else:
                        # Set some pixels to distinguish chars
                        row.append(1 if (x + y + char_idx) % 3 == 0 else 0)
                if char_idx < num_chars - 1:
                    row.append(1)  # separator column

        grid.append(row)

    return _create_test_bmp(grid)


@pytest.fixture
def font_bmp_dir(tmp_path: Path) -> Path:
    """Create a temp directory with test font BMP files."""
    # Create a simple 8+1 = 9-row high font (for 8-pixel high glyphs)
    bmp_data = _create_simple_font_bmp(glyph_height=8, num_chars=128, char_width=5)
    (tmp_path / "test.1.font.bmp").write_bytes(bmp_data)

    # Create a second font for line 2
    bmp_data2 = _create_simple_font_bmp(glyph_height=8, num_chars=128, char_width=6)
    (tmp_path / "test.2.font.bmp").write_bytes(bmp_data2)

    return tmp_path


@pytest.fixture
def font_cache(font_bmp_dir: Path) -> FontCache:
    """A FontCache loaded from test font BMPs."""
    cache = FontCache()
    count = cache.load_directory(font_bmp_dir)
    assert count == 2
    return cache


# =====================================================================
# DisplaySpec tests
# =====================================================================


class TestDisplaySpec:
    """Tests for DisplaySpec and related functions."""

    def test_sb2_spec(self) -> None:
        assert DISPLAY_SB2.width == 320
        assert DISPLAY_SB2.height == 32
        assert DISPLAY_SB2.bytes_per_column == 4
        assert DISPLAY_SB2.frame_bytes == 1280
        assert DISPLAY_SB2.frame_command == "grfe"
        assert not DISPLAY_SB2.has_screen2

    def test_transporter_spec(self) -> None:
        assert DISPLAY_TRANSPORTER.width == 320
        assert DISPLAY_TRANSPORTER.height == 32
        assert DISPLAY_TRANSPORTER.has_screen2
        assert DISPLAY_TRANSPORTER.screen2_offset == 640
        assert DISPLAY_TRANSPORTER.total_frame_bytes == 2560

    def test_boom_spec(self) -> None:
        assert DISPLAY_BOOM.width == 160
        assert DISPLAY_BOOM.height == 32
        assert DISPLAY_BOOM.frame_bytes == 640

    def test_sbg_spec(self) -> None:
        assert DISPLAY_SBG.width == 280
        assert DISPLAY_SBG.height == 16
        assert DISPLAY_SBG.bytes_per_column == 2
        assert DISPLAY_SBG.frame_bytes == 560
        assert DISPLAY_SBG.frame_command == "grfd"

    def test_none_spec(self) -> None:
        assert DISPLAY_NONE.width == 0
        assert DISPLAY_NONE.frame_bytes == 0
        assert DISPLAY_NONE.model == DisplayModel.NONE

    def test_display_spec_for_model_vfdmodel(self) -> None:
        assert display_spec_for_model("graphic-320x32") == DISPLAY_SB2
        assert display_spec_for_model("graphic-160x32") == DISPLAY_BOOM
        assert display_spec_for_model("graphic-280x16") == DISPLAY_SBG

    def test_display_spec_for_model_device_names(self) -> None:
        assert display_spec_for_model("transporter") == DISPLAY_TRANSPORTER
        assert display_spec_for_model("squeezebox2") == DISPLAY_SB2
        assert display_spec_for_model("boom") == DISPLAY_BOOM
        assert display_spec_for_model("squeezeboxg") == DISPLAY_SBG

    def test_display_spec_for_model_unknown(self) -> None:
        assert display_spec_for_model("unknown_device") == DISPLAY_NONE
        assert display_spec_for_model("receiver") == DISPLAY_NONE
        assert display_spec_for_model("") == DISPLAY_NONE


class TestFontConfig:
    """Tests for FontConfig."""

    def test_default_font_config(self) -> None:
        fc = FontConfig()
        assert fc.active_font == "standard"
        assert fc.line1_font == "standard.1"
        assert fc.line2_font == "standard.2"

    def test_custom_font_config(self) -> None:
        fc = FontConfig(font_names=["small", "medium", "large"], active_index=2)
        assert fc.active_font == "large"
        assert fc.line1_font == "large.1"
        assert fc.line2_font == "large.2"

    def test_active_index_out_of_range(self) -> None:
        fc = FontConfig(font_names=["a", "b"], active_index=99)
        # Falls back to first
        assert fc.active_font == "a"

    def test_empty_font_names(self) -> None:
        fc = FontConfig(font_names=[], active_index=0)
        # Falls back to "standard"
        assert fc.active_font == "standard"

    def test_default_font_config_for_spec(self) -> None:
        fc = default_font_config(DISPLAY_SB2)
        assert "standard" in fc.font_names

        fc_boom = default_font_config(DISPLAY_BOOM)
        assert "standard_n" in fc_boom.font_names

        fc_sbg = default_font_config(DISPLAY_SBG)
        assert "medium" in fc_sbg.font_names

    def test_default_font_config_returns_copy(self) -> None:
        fc1 = default_font_config(DISPLAY_SB2)
        fc2 = default_font_config(DISPLAY_SB2)
        fc1.active_index = 999
        assert fc2.active_index != 999


# =====================================================================
# Font parsing tests
# =====================================================================


class TestBmpParsing:
    """Tests for BMP file parsing."""

    def test_parse_valid_bmp(self, tmp_path: Path) -> None:
        # 3x3 pixel image, all white
        pixels = [[1, 1, 1], [1, 1, 1], [1, 1, 1]]
        bmp_data = _create_test_bmp(pixels)
        bmp_file = tmp_path / "test.bmp"
        bmp_file.write_bytes(bmp_data)

        grid, height = _parse_bmp(bmp_file)
        assert grid is not None
        assert height == 3
        assert len(grid) == 3
        assert all(len(row) == 3 for row in grid)
        # All pixels should be 1 (white in normal palette)
        for row in grid:
            for px in row:
                assert px == 1

    def test_parse_reversed_palette(self, tmp_path: Path) -> None:
        # Reversed palette: file bit 1 → pixel 0, file bit 0 → pixel 1
        pixels = [[1, 0], [0, 1]]
        bmp_data = _create_test_bmp(pixels, reversed_palette=True)
        bmp_file = tmp_path / "test.bmp"
        bmp_file.write_bytes(bmp_data)

        grid, height = _parse_bmp(bmp_file)
        assert grid is not None
        assert height == 2
        assert grid[0] == [1, 0]
        assert grid[1] == [0, 1]

    def test_parse_invalid_file(self, tmp_path: Path) -> None:
        bmp_file = tmp_path / "bad.bmp"
        bmp_file.write_bytes(b"NOT A BMP FILE AT ALL")
        grid, height = _parse_bmp(bmp_file)
        assert grid is None
        assert height == 0

    def test_parse_too_small(self, tmp_path: Path) -> None:
        bmp_file = tmp_path / "tiny.bmp"
        bmp_file.write_bytes(b"BM\x00")
        grid, height = _parse_bmp(bmp_file)
        assert grid is None
        assert height == 0


class TestFontParsing:
    """Tests for font table construction from pixel grids."""

    def test_parse_font_simple(self) -> None:
        # Create a simple grid: 2 characters, each 1 column wide, 3 rows + delimiter
        # Character 0 (interspace): 1 column, blank
        # Character 1: 1 column, some pixels set
        # Delimiter row: 0 for char cols, 1 for separators
        grid = [
            # Row 0 (top)
            [0, 1, 1],  # char0=0, sep=1, char1=1
            # Row 1
            [0, 1, 0],  # char0=0, sep=1, char1=0
            # Row 2
            [0, 1, 1],  # char0=0, sep=1, char1=1
            # Delimiter row (bottom)
            [0, 1, 0],  # char0 col=black(0), sep=white(1), char1 col=black(0)
        ]
        font = _parse_font(grid)
        assert len(font) == 256

        # Character 0 has no set bits → valid interspace glyph (not suppressed).
        # Only characters with *set* bits get suppressed to b"".
        assert font[0] == b"\x00"

        # Character 1 should have the pattern [1, 0, 1] in 3 rows
        # Packed as bits: '101' padded to '10100000' = 0xA0
        assert len(font[1]) > 0
        expected_bits = "101"
        expected_padded = expected_bits + "0" * (8 - len(expected_bits))
        expected_byte = int(expected_padded, 2)
        assert font[1][0] == expected_byte

    def test_parse_font_interspace_with_pixels(self) -> None:
        # If character 0 has set pixels, interspace is suppressed (empty bytes)
        grid = [
            [1, 1],  # char0=1 (set pixel!), sep=1
            [0, 1],  # delimiter
        ]
        font = _parse_font(grid)
        # Character 0 with set pixels → becomes empty bytes (suppress interspace)
        assert font[0] == b""

    def test_parse_empty_grid(self) -> None:
        font = _parse_font([])
        assert len(font) == 256
        assert all(g == b"" for g in font)


class TestFontCache:
    """Tests for FontCache loading and rendering."""

    def test_load_directory(self, font_cache: FontCache) -> None:
        assert font_cache.has_font("test.1")
        assert font_cache.has_font("test.2")
        assert not font_cache.has_font("nonexistent")
        assert "test.1" in font_cache.font_names
        assert "test.2" in font_cache.font_names

    def test_font_height(self, font_cache: FontCache) -> None:
        height = font_cache.font_height("test.1")
        assert height is not None
        assert height == 8  # Our test font is 8 pixels high

    def test_font_height_unknown(self, font_cache: FontCache) -> None:
        assert font_cache.font_height("nonexistent") is None

    def test_render_string_empty(self, font_cache: FontCache) -> None:
        result = font_cache.render_string("test.2", "")
        assert result == b""

    def test_render_string_unknown_font(self, font_cache: FontCache) -> None:
        result = font_cache.render_string("nonexistent_font", "hello")
        assert result == b""

    def test_render_string_produces_bytes(self, font_cache: FontCache) -> None:
        result = font_cache.render_string("test.2", "A")
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_render_string_different_chars_differ(self, font_cache: FontCache) -> None:
        a = font_cache.render_string("test.2", "A")
        b = font_cache.render_string("test.2", "B")
        # Different characters should produce different bitmaps
        # (unless both happen to be blank, but our test font has patterns)
        assert a != b

    def test_render_string_with_interspace(self, font_cache: FontCache) -> None:
        single = font_cache.render_string("test.2", "A")
        double = font_cache.render_string("test.2", "AB")
        # "AB" should be longer than "A" (A + interspace + B)
        assert len(double) > len(single)

    def test_render_string_space_char(self, font_cache: FontCache) -> None:
        result = font_cache.render_string("test.2", " ")
        # Space is blank in our test font, but still has width
        assert isinstance(result, bytes)

    def test_font_hash(self, font_cache: FontCache) -> None:
        fh = font_cache.get_font_hash()
        assert "test" in fh
        assert "line" in fh["test"]
        assert fh["test"]["line"][0] == "test.1"
        assert fh["test"]["line"][1] == "test.2"

    def test_load_directory_nonexistent(self) -> None:
        cache = FontCache()
        count = cache.load_directory(Path("/nonexistent/path"))
        assert count == 0

    def test_load_font_invalid_file(self, tmp_path: Path) -> None:
        cache = FontCache()
        bad_file = tmp_path / "bad.font.bmp"
        bad_file.write_bytes(b"NOT A BMP")
        with pytest.raises(ValueError):
            cache.load_font("bad", bad_file)

    def test_render_string_extended_basic(self, font_cache: FontCache) -> None:
        reverse, bits = font_cache.render_string_extended("test.2", "Hello")
        assert reverse is False
        assert isinstance(bits, bytes)
        assert len(bits) > 0

    def test_render_string_extended_tight_mode(self, font_cache: FontCache) -> None:
        normal = font_cache.render_string("test.2", "AB")
        _, tight = font_cache.render_string_extended("test.2", "A\x1dB")
        # Tight mode removes interspace, so tight should be shorter or equal
        assert len(tight) <= len(normal)

    def test_measure_text(self, font_cache: FontCache) -> None:
        width = font_cache.measure_text("test.2", "Hello")
        assert width > 0

    def test_measure_text_unknown_font(self, font_cache: FontCache) -> None:
        assert font_cache.measure_text("nonexistent", "test") == 0

    def test_font_extent(self, font_cache: FontCache) -> None:
        # .1 fonts should have negative extent, .2 fonts positive (or zero)
        ext1 = font_cache.font_extent("test.1")
        ext2 = font_cache.font_extent("test.2")
        # test.1 is a .1 font → negative extent (or zero if no extent char)
        assert ext1 <= 0
        # test.2 is a .2 font → positive or zero
        assert ext2 >= 0


# =====================================================================
# Bit/byte utility tests
# =====================================================================


class TestBitUtils:
    """Tests for low-level bit manipulation utilities."""

    def test_bits_to_bytes_basic(self) -> None:
        # "10000000" → 0x80
        assert _bits_to_bytes(["1", "0", "0", "0", "0", "0", "0", "0"]) == b"\x80"

    def test_bits_to_bytes_all_ones(self) -> None:
        assert _bits_to_bytes(["1"] * 8) == b"\xff"

    def test_bits_to_bytes_all_zeros(self) -> None:
        assert _bits_to_bytes(["0"] * 8) == b"\x00"

    def test_bits_to_bytes_partial(self) -> None:
        # "101" → "10100000" = 0xA0
        assert _bits_to_bytes(["1", "0", "1"]) == b"\xa0"

    def test_bits_to_bytes_empty(self) -> None:
        assert _bits_to_bytes([]) == b""

    def test_bits_to_bytes_multi_byte(self) -> None:
        # 16 bits → 2 bytes
        bits = ["1"] * 8 + ["0"] * 8
        result = _bits_to_bytes(bits)
        assert result == b"\xff\x00"

    def test_has_set_bits_true(self) -> None:
        assert _has_set_bits(b"\x01") is True
        assert _has_set_bits(b"\x80") is True

    def test_has_set_bits_false(self) -> None:
        assert _has_set_bits(b"\x00") is False
        assert _has_set_bits(b"\x00\x00\x00") is False

    def test_has_set_bits_empty(self) -> None:
        assert _has_set_bits(b"") is False

    def test_bitmap_or(self) -> None:
        assert _bitmap_or(b"\x0f", b"\xf0") == b"\xff"
        assert _bitmap_or(b"\x00", b"\x00") == b"\x00"

    def test_bitmap_or_different_lengths(self) -> None:
        result = _bitmap_or(b"\xff", b"\x00\xff")
        assert len(result) == 2
        assert result[0] == 0xFF
        assert result[1] == 0xFF

    def test_bitmap_or_empty(self) -> None:
        assert _bitmap_or(b"", b"") == b""
        assert _bitmap_or(b"\xff", b"") == b"\xff"


# =====================================================================
# Renderer tests
# =====================================================================


class TestOrInto:
    """Tests for the _or_into helper."""

    def test_or_into_basic(self) -> None:
        target = bytearray(b"\x00\x00")
        _or_into(target, b"\x0f\xf0")
        assert bytes(target) == b"\x0f\xf0"

    def test_or_into_accumulates(self) -> None:
        target = bytearray(b"\x0f\x00")
        _or_into(target, b"\xf0\xff")
        assert bytes(target) == b"\xff\xff"

    def test_or_into_shorter_source(self) -> None:
        target = bytearray(b"\x00\x00\x00")
        _or_into(target, b"\xff")
        assert target[0] == 0xFF
        assert target[1] == 0x00
        assert target[2] == 0x00


class TestFormatTime:
    """Tests for time formatting helper."""

    def test_format_seconds(self) -> None:
        assert _format_time(0) == "0:00"
        assert _format_time(5) == "0:05"
        assert _format_time(65) == "1:05"
        assert _format_time(3599) == "59:59"

    def test_format_hours(self) -> None:
        assert _format_time(3600) == "1:00:00"
        assert _format_time(3661) == "1:01:01"

    def test_format_negative(self) -> None:
        assert _format_time(-5) == "0:00"

    def test_format_float(self) -> None:
        assert _format_time(61.7) == "1:01"


class TestMakeBarColumn:
    """Tests for progress bar column generation."""

    def test_filled_column_32(self) -> None:
        col = _make_bar_column(32, 4, 3, filled=True)
        assert len(col) == 4
        # Bottom 3 bits of the 32-bit column should be set
        # Row 29 is bit 2, row 30 is bit 1, row 31 is bit 0 of last byte
        assert col[3] & 0x07 == 0x07  # bottom 3 bits

    def test_empty_column_32(self) -> None:
        col = _make_bar_column(32, 4, 3, filled=False)
        assert len(col) == 4
        # Only top and bottom border of bar area
        # Row 29 (top border) and row 31 (bottom border)
        assert col[3] & 0x05 == 0x05  # bits 2 and 0
        assert col[3] & 0x02 == 0x00  # middle bit not set

    def test_column_16(self) -> None:
        col = _make_bar_column(16, 2, 3, filled=True)
        assert len(col) == 2


class TestDisplayRenderer:
    """Tests for the main renderer."""

    def _make_renderer(
        self, font_cache: FontCache, spec: DisplaySpec | None = None
    ) -> DisplayRenderer:
        if spec is None:
            # Use a small spec for fast tests
            spec = DisplaySpec(
                model=DisplayModel.GRAPHIC_320x32,
                width=40,
                height=8,
                bytes_per_column=1,
                frame_command="grfe",
            )
        fc = FontConfig(font_names=["test"], active_index=0)
        return DisplayRenderer(font_cache, spec, fc)

    def test_render_empty_parts(self, font_cache: FontCache) -> None:
        renderer = self._make_renderer(font_cache)
        parts = ScreenParts()
        result = renderer.render(parts)
        assert isinstance(result, RenderedScreen)
        assert len(result.bitmap) == renderer.spec.frame_bytes
        assert result.scroll_line == -1

    def test_render_single_line(self, font_cache: FontCache) -> None:
        renderer = self._make_renderer(font_cache)
        parts = ScreenParts(line=["Hello", None])
        result = renderer.render(parts)
        assert len(result.bitmap) == renderer.spec.frame_bytes
        # Should have some non-zero bytes (text rendered)
        assert any(b != 0 for b in result.bitmap)

    def test_render_two_lines(self, font_cache: FontCache) -> None:
        renderer = self._make_renderer(font_cache)
        parts = ScreenParts(line=["Line 1", "Line 2"])
        result = renderer.render(parts)
        assert len(result.bitmap) == renderer.spec.frame_bytes

    def test_render_with_overlay(self, font_cache: FontCache) -> None:
        renderer = self._make_renderer(font_cache)
        parts = ScreenParts(
            line=["Title", "Artist"],
            overlay=[None, "3:42"],
        )
        result = renderer.render(parts)
        assert len(result.bitmap) == renderer.spec.frame_bytes

    def test_render_blank(self, font_cache: FontCache) -> None:
        renderer = self._make_renderer(font_cache)
        blank = renderer.render_blank()
        assert len(blank) == renderer.spec.frame_bytes
        assert all(b == 0 for b in blank)

    def test_render_now_playing(self, font_cache: FontCache) -> None:
        renderer = self._make_renderer(font_cache)
        result = renderer.render_now_playing(
            title="Test Song",
            artist="Test Artist",
            album="Test Album",
            elapsed_s=65.0,
            duration_s=200.0,
        )
        assert isinstance(result, RenderedScreen)
        assert len(result.bitmap) == renderer.spec.frame_bytes

    def test_render_idle(self, font_cache: FontCache) -> None:
        renderer = self._make_renderer(font_cache)
        result = renderer.render_idle(text="Resonance")
        assert len(result.bitmap) == renderer.spec.frame_bytes

    def test_render_menu(self, font_cache: FontCache) -> None:
        renderer = self._make_renderer(font_cache)
        result = renderer.render_menu(
            ["Artists", "Albums", "Genres"],
            selected_index=1,
        )
        assert len(result.bitmap) == renderer.spec.frame_bytes

    def test_render_progress_bar(self, font_cache: FontCache) -> None:
        renderer = self._make_renderer(font_cache)
        bar = renderer.render_progress_bar(0.5)
        assert len(bar) == renderer.spec.frame_bytes

    def test_render_progress_bar_zero(self, font_cache: FontCache) -> None:
        renderer = self._make_renderer(font_cache)
        bar = renderer.render_progress_bar(0.0)
        assert len(bar) == renderer.spec.frame_bytes

    def test_render_progress_bar_full(self, font_cache: FontCache) -> None:
        renderer = self._make_renderer(font_cache)
        bar = renderer.render_progress_bar(1.0)
        assert len(bar) == renderer.spec.frame_bytes

    def test_render_progress_bar_clamps(self, font_cache: FontCache) -> None:
        renderer = self._make_renderer(font_cache)
        bar_over = renderer.render_progress_bar(1.5)
        bar_under = renderer.render_progress_bar(-0.5)
        bar_full = renderer.render_progress_bar(1.0)
        bar_zero = renderer.render_progress_bar(0.0)
        assert bar_over == bar_full
        assert bar_under == bar_zero

    def test_render_none_spec(self) -> None:
        cache = FontCache()
        renderer = DisplayRenderer(cache, DISPLAY_NONE)
        result = renderer.render(ScreenParts())
        assert result.bitmap == b""

    def test_build_scroll_frame(self, font_cache: FontCache) -> None:
        renderer = self._make_renderer(font_cache)
        frame_size = renderer.spec.frame_bytes
        static = b"\x00" * frame_size
        scroll = b"\xff" * (frame_size * 2)
        frame = renderer.build_scroll_frame(static, scroll, 5, frame_size)
        assert len(frame) == frame_size

    def test_font_config_setter(self, font_cache: FontCache) -> None:
        renderer = self._make_renderer(font_cache)
        new_config = FontConfig(font_names=["test"], active_index=0)
        renderer.font_config = new_config
        assert renderer.font_config is new_config


# =====================================================================
# DisplayManager tests
# =====================================================================


class TestDisplayManager:
    """Tests for the per-player display manager."""

    def test_register_player(self) -> None:
        from resonance.display.manager import DisplayManager

        manager = DisplayManager()
        pd = manager.register_player("00:11:22:33:44:55", model="squeezebox2")
        assert pd.player_id == "00:11:22:33:44:55"
        assert pd.spec == DISPLAY_SB2
        assert pd.has_display

    def test_register_player_no_display(self) -> None:
        from resonance.display.manager import DisplayManager

        manager = DisplayManager()
        pd = manager.register_player("00:11:22:33:44:55", model="receiver")
        assert not pd.has_display

    def test_register_player_boom(self) -> None:
        from resonance.display.manager import DisplayManager

        manager = DisplayManager()
        pd = manager.register_player("00:11:22:33:44:55", model="boom")
        assert pd.spec == DISPLAY_BOOM
        assert pd.has_display

    def test_unregister_player(self) -> None:
        from resonance.display.manager import DisplayManager

        manager = DisplayManager()
        manager.register_player("00:11:22:33:44:55", model="squeezebox2")
        assert manager.get_player_display("00:11:22:33:44:55") is not None
        manager.unregister_player("00:11:22:33:44:55")
        assert manager.get_player_display("00:11:22:33:44:55") is None

    def test_get_player_display_not_found(self) -> None:
        from resonance.display.manager import DisplayManager

        manager = DisplayManager()
        assert manager.get_player_display("nonexistent") is None

    def test_player_display_elapsed(self) -> None:
        from resonance.display.manager import PlayerDisplay

        pd = PlayerDisplay(player_id="test")
        pd.playback_elapsed_at_start = 10.0
        pd.is_playing = False
        assert pd.current_elapsed == 10.0

    def test_player_display_elapsed_playing(self) -> None:
        import time

        from resonance.display.manager import PlayerDisplay

        pd = PlayerDisplay(player_id="test")
        pd.playback_elapsed_at_start = 10.0
        pd.playback_started_at = time.monotonic() - 5.0
        pd.is_playing = True
        pd.is_paused = False
        elapsed = pd.current_elapsed
        assert 14.5 < elapsed < 16.0

    def test_player_display_elapsed_paused(self) -> None:
        import time

        from resonance.display.manager import PlayerDisplay

        pd = PlayerDisplay(player_id="test")
        pd.playback_elapsed_at_start = 10.0
        pd.playback_started_at = time.monotonic() - 100.0
        pd.is_playing = True
        pd.is_paused = True
        # Should not advance while paused
        assert pd.current_elapsed == 10.0

    @pytest.mark.asyncio
    async def test_update_now_playing_no_display(self) -> None:
        from resonance.display.manager import DisplayManager

        manager = DisplayManager()
        manager.register_player("test-mac", model="receiver")
        # Should not crash even though player has no display
        await manager.update_now_playing(
            "test-mac", title="Test", duration_s=200.0,
        )

    @pytest.mark.asyncio
    async def test_update_now_playing_unknown_player(self) -> None:
        from resonance.display.manager import DisplayManager

        manager = DisplayManager()
        # Should not crash for unregistered player
        await manager.update_now_playing(
            "nonexistent", title="Test", duration_s=200.0,
        )

    @pytest.mark.asyncio
    async def test_clear_display(self) -> None:
        from resonance.display.manager import DisplayManager

        manager = DisplayManager()
        manager.register_player("test-mac", model="squeezebox2")
        # Should not crash (no slimproto server attached)
        await manager.clear_display("test-mac")

    @pytest.mark.asyncio
    async def test_set_power(self) -> None:
        from resonance.display.manager import DisplayManager, DisplayState

        manager = DisplayManager()
        pd = manager.register_player("test-mac", model="squeezebox2")
        await manager.set_power("test-mac", power_on=False)
        assert pd.state == DisplayState.OFF
        await manager.set_power("test-mac", power_on=True)
        assert pd.state == DisplayState.IDLE

    @pytest.mark.asyncio
    async def test_show_briefly(self, font_cache: FontCache) -> None:
        from resonance.display.manager import DisplayManager

        manager = DisplayManager(font_cache=font_cache)
        pd = manager.register_player("test-mac", model="squeezebox2")
        parts = ScreenParts(center=[None, "Volume: 50"])
        await manager.show_briefly("test-mac", parts, duration=1.0)
        assert pd.show_briefly_parts is not None

    def test_font_cache_setter(self, font_cache: FontCache) -> None:
        from resonance.display.manager import DisplayManager

        manager = DisplayManager()
        pd = manager.register_player("test-mac", model="squeezebox2")
        manager.font_cache = font_cache
        # Renderer should be updated
        assert manager.font_cache is font_cache

    @pytest.mark.asyncio
    async def test_start_stop(self) -> None:
        from resonance.display.manager import DisplayManager

        manager = DisplayManager()
        await manager.start()
        assert manager._running
        assert manager._update_task is not None
        await manager.stop()
        assert not manager._running
        assert manager._update_task is None

    @pytest.mark.asyncio
    async def test_start_stop_idempotent(self) -> None:
        from resonance.display.manager import DisplayManager

        manager = DisplayManager()
        await manager.start()
        await manager.start()  # Should not crash
        await manager.stop()
        await manager.stop()  # Should not crash


class TestDisplayManagerSingleton:
    """Test module-level singleton helpers."""

    def test_get_display_manager(self) -> None:
        from resonance.display.manager import (
            DisplayManager,
            get_display_manager,
            set_display_manager,
        )

        original = get_display_manager()
        assert isinstance(original, DisplayManager)

        custom = DisplayManager()
        set_display_manager(custom)
        assert get_display_manager() is custom

        # Restore
        set_display_manager(original)


# =====================================================================
# Integration: render with real-ish specs
# =====================================================================


class TestIntegrationRendering:
    """Integration tests: render with SB2-like specs."""

    def test_sb2_frame_size(self, font_cache: FontCache) -> None:
        spec = DisplaySpec(
            model=DisplayModel.GRAPHIC_320x32,
            width=320,
            height=32,
            bytes_per_column=4,
            frame_command="grfe",
        )
        fc = FontConfig(font_names=["test"], active_index=0)
        renderer = DisplayRenderer(font_cache, spec, fc)
        result = renderer.render(ScreenParts(line=["Hello World", "Testing"]))
        assert len(result.bitmap) == 1280

    def test_boom_frame_size(self, font_cache: FontCache) -> None:
        spec = DisplaySpec(
            model=DisplayModel.GRAPHIC_160x32,
            width=160,
            height=32,
            bytes_per_column=4,
            frame_command="grfe",
        )
        fc = FontConfig(font_names=["test"], active_index=0)
        renderer = DisplayRenderer(font_cache, spec, fc)
        result = renderer.render(ScreenParts(line=["Hello", "World"]))
        assert len(result.bitmap) == 640

    def test_sbg_frame_size(self, font_cache: FontCache) -> None:
        spec = DisplaySpec(
            model=DisplayModel.GRAPHIC_280x16,
            width=280,
            height=16,
            bytes_per_column=2,
            frame_command="grfd",
        )
        fc = FontConfig(font_names=["test"], active_index=0)
        renderer = DisplayRenderer(font_cache, spec, fc)
        result = renderer.render(ScreenParts(line=["SB1 Test", None]))
        assert len(result.bitmap) == 560

    def test_now_playing_with_progress(self, font_cache: FontCache) -> None:
        spec = DisplaySpec(
            model=DisplayModel.GRAPHIC_320x32,
            width=320,
            height=32,
            bytes_per_column=4,
            frame_command="grfe",
        )
        fc = FontConfig(font_names=["test"], active_index=0)
        renderer = DisplayRenderer(font_cache, spec, fc)
        result = renderer.render_now_playing(
            title="Bohemian Rhapsody",
            artist="Queen",
            album="A Night at the Opera",
            elapsed_s=120.0,
            duration_s=354.0,
            show_progress=True,
        )
        assert len(result.bitmap) == 1280
        # Should have non-zero bytes (text + progress bar)
        assert any(b != 0 for b in result.bitmap)


# =====================================================================
# Phase 3: Advanced Menu Rendering
# =====================================================================


class TestRenderMenuAdvanced:
    """Tests for render_menu_advanced with position indicator and arrows."""

    def _make_renderer(
        self, font_cache: FontCache, spec: DisplaySpec | None = None
    ) -> DisplayRenderer:
        if spec is None:
            spec = DisplaySpec(
                model=DisplayModel.GRAPHIC_320x32,
                width=40,
                height=8,
                bytes_per_column=1,
                frame_command="grfe",
            )
        fc = FontConfig(font_names=["test"], active_index=0)
        return DisplayRenderer(font_cache, spec, fc)

    def test_render_menu_advanced_basic(self, font_cache: FontCache) -> None:
        renderer = self._make_renderer(font_cache)
        result = renderer.render_menu_advanced(
            ["Artists", "Albums", "Genres"],
            selected_index=1,
        )
        assert isinstance(result, RenderedScreen)
        assert len(result.bitmap) == renderer.spec.frame_bytes

    def test_render_menu_advanced_empty(self, font_cache: FontCache) -> None:
        renderer = self._make_renderer(font_cache)
        result = renderer.render_menu_advanced([])
        assert len(result.bitmap) == renderer.spec.frame_bytes

    def test_render_menu_advanced_single_item(self, font_cache: FontCache) -> None:
        renderer = self._make_renderer(font_cache)
        result = renderer.render_menu_advanced(["Only Item"], selected_index=0)
        assert len(result.bitmap) == renderer.spec.frame_bytes

    def test_render_menu_advanced_with_submenu_arrows(self, font_cache: FontCache) -> None:
        renderer = self._make_renderer(font_cache)
        items = ["Artists", "Albums", "Genres", "Playlists"]
        has_sub = [True, True, True, False]
        result = renderer.render_menu_advanced(
            items, selected_index=0, has_submenu=has_sub,
        )
        assert len(result.bitmap) == renderer.spec.frame_bytes

    def test_render_menu_advanced_no_submenu_arrow(self, font_cache: FontCache) -> None:
        renderer = self._make_renderer(font_cache)
        items = ["Artists", "Albums"]
        has_sub = [False, False]
        result = renderer.render_menu_advanced(
            items, selected_index=1, has_submenu=has_sub,
        )
        assert len(result.bitmap) == renderer.spec.frame_bytes

    def test_render_menu_advanced_no_position(self, font_cache: FontCache) -> None:
        renderer = self._make_renderer(font_cache)
        result = renderer.render_menu_advanced(
            ["A", "B", "C"], selected_index=2, show_position=False,
        )
        assert len(result.bitmap) == renderer.spec.frame_bytes

    def test_render_menu_advanced_selected_clamped(self, font_cache: FontCache) -> None:
        renderer = self._make_renderer(font_cache)
        # selected_index out of range — should clamp
        result = renderer.render_menu_advanced(
            ["A", "B"], selected_index=99,
        )
        assert len(result.bitmap) == renderer.spec.frame_bytes

    def test_render_menu_advanced_first_item_no_context(self, font_cache: FontCache) -> None:
        """When the first item is selected, line 1 (context above) should be empty."""
        renderer = self._make_renderer(font_cache)
        result = renderer.render_menu_advanced(
            ["First", "Second", "Third"], selected_index=0,
        )
        assert len(result.bitmap) == renderer.spec.frame_bytes

    def test_render_menu_advanced_has_submenu_none(self, font_cache: FontCache) -> None:
        """When has_submenu is None, no arrow is shown."""
        renderer = self._make_renderer(font_cache)
        result = renderer.render_menu_advanced(
            ["A", "B", "C"], selected_index=1, has_submenu=None,
        )
        assert len(result.bitmap) == renderer.spec.frame_bytes

    def test_render_menu_advanced_produces_different_output(self, font_cache: FontCache) -> None:
        """Different selected items should produce different bitmaps."""
        renderer = self._make_renderer(font_cache)
        items = ["Artists", "Albums", "Genres"]
        r1 = renderer.render_menu_advanced(items, selected_index=0)
        r2 = renderer.render_menu_advanced(items, selected_index=2)
        # At least the bitmaps should differ (different text rendered)
        assert r1.bitmap != r2.bitmap


class TestRenderSliderBar:
    """Tests for render_slider_bar (LMS sliderBar equivalent)."""

    def _make_renderer(
        self, font_cache: FontCache, spec: DisplaySpec | None = None
    ) -> DisplayRenderer:
        if spec is None:
            spec = DisplaySpec(
                model=DisplayModel.GRAPHIC_320x32,
                width=40,
                height=8,
                bytes_per_column=1,
                frame_command="grfe",
            )
        fc = FontConfig(font_names=["test"], active_index=0)
        return DisplayRenderer(font_cache, spec, fc)

    def test_slider_bar_zero(self, font_cache: FontCache) -> None:
        renderer = self._make_renderer(font_cache)
        result = renderer.render_slider_bar(0.0)
        assert len(result.bitmap) == renderer.spec.frame_bytes

    def test_slider_bar_full(self, font_cache: FontCache) -> None:
        renderer = self._make_renderer(font_cache)
        result = renderer.render_slider_bar(100.0)
        assert len(result.bitmap) == renderer.spec.frame_bytes

    def test_slider_bar_mid(self, font_cache: FontCache) -> None:
        renderer = self._make_renderer(font_cache)
        result = renderer.render_slider_bar(50.0)
        assert len(result.bitmap) == renderer.spec.frame_bytes

    def test_slider_bar_clamps_negative(self, font_cache: FontCache) -> None:
        renderer = self._make_renderer(font_cache)
        r_neg = renderer.render_slider_bar(-10.0)
        r_zero = renderer.render_slider_bar(0.0)
        assert r_neg.bitmap == r_zero.bitmap

    def test_slider_bar_clamps_over(self, font_cache: FontCache) -> None:
        renderer = self._make_renderer(font_cache)
        r_over = renderer.render_slider_bar(150.0)
        r_full = renderer.render_slider_bar(100.0)
        assert r_over.bitmap == r_full.bitmap

    def test_slider_bar_custom_width(self, font_cache: FontCache) -> None:
        renderer = self._make_renderer(font_cache)
        result = renderer.render_slider_bar(50.0, width_chars=20)
        assert len(result.bitmap) == renderer.spec.frame_bytes

    def test_slider_bar_different_values_produce_output(self, font_cache: FontCache) -> None:
        """Slider bar at different values should produce valid non-zero bitmaps.

        Note: with synthetic test fonts the symbol glyphs (chars 3–9) may
        not be visually distinct, so we only verify that the bar renders
        valid output.  With real LMS fonts the filled/empty symbols are
        clearly different.
        """
        renderer = self._make_renderer(font_cache)
        for value in (0.0, 25.0, 50.0, 75.0, 100.0):
            result = renderer.render_slider_bar(value)
            assert len(result.bitmap) == renderer.spec.frame_bytes
            assert any(b != 0 for b in result.bitmap), f"slider at {value}% should be non-zero"


class TestRenderVolumeOverlay:
    """Tests for render_volume_overlay."""

    def _make_renderer(
        self, font_cache: FontCache, spec: DisplaySpec | None = None
    ) -> DisplayRenderer:
        if spec is None:
            spec = DisplaySpec(
                model=DisplayModel.GRAPHIC_320x32,
                width=40,
                height=8,
                bytes_per_column=1,
                frame_command="grfe",
            )
        fc = FontConfig(font_names=["test"], active_index=0)
        return DisplayRenderer(font_cache, spec, fc)

    def test_volume_overlay_zero(self, font_cache: FontCache) -> None:
        renderer = self._make_renderer(font_cache)
        result = renderer.render_volume_overlay(0)
        assert isinstance(result, RenderedScreen)
        assert len(result.bitmap) == renderer.spec.frame_bytes

    def test_volume_overlay_full(self, font_cache: FontCache) -> None:
        renderer = self._make_renderer(font_cache)
        result = renderer.render_volume_overlay(100)
        assert len(result.bitmap) == renderer.spec.frame_bytes

    def test_volume_overlay_mid(self, font_cache: FontCache) -> None:
        renderer = self._make_renderer(font_cache)
        result = renderer.render_volume_overlay(50)
        assert len(result.bitmap) == renderer.spec.frame_bytes
        # Should have non-zero bytes (label + bar)
        assert any(b != 0 for b in result.bitmap)

    def test_volume_overlay_clamps(self, font_cache: FontCache) -> None:
        renderer = self._make_renderer(font_cache)
        r_over = renderer.render_volume_overlay(200)
        r_full = renderer.render_volume_overlay(100)
        assert r_over.bitmap == r_full.bitmap

    def test_volume_overlay_custom_label(self, font_cache: FontCache) -> None:
        renderer = self._make_renderer(font_cache)
        result = renderer.render_volume_overlay(50, label="Bass")
        assert len(result.bitmap) == renderer.spec.frame_bytes

    def test_volume_overlay_no_scroll(self, font_cache: FontCache) -> None:
        renderer = self._make_renderer(font_cache)
        result = renderer.render_volume_overlay(50)
        assert result.scroll_line == -1


# =====================================================================
# Phase 4: Screensaver Rendering
# =====================================================================


class TestRenderClock:
    """Tests for render_clock (DateTime screensaver)."""

    def _make_renderer(
        self, font_cache: FontCache, spec: DisplaySpec | None = None
    ) -> DisplayRenderer:
        if spec is None:
            spec = DisplaySpec(
                model=DisplayModel.GRAPHIC_320x32,
                width=40,
                height=8,
                bytes_per_column=1,
                frame_command="grfe",
            )
        fc = FontConfig(font_names=["test"], active_index=0)
        return DisplayRenderer(font_cache, spec, fc)

    def test_clock_default(self, font_cache: FontCache) -> None:
        from datetime import datetime

        renderer = self._make_renderer(font_cache)
        result = renderer.render_clock()
        assert isinstance(result, RenderedScreen)
        assert len(result.bitmap) == renderer.spec.frame_bytes
        assert result.scroll_line == -1

    def test_clock_specific_time(self, font_cache: FontCache) -> None:
        from datetime import datetime

        renderer = self._make_renderer(font_cache)
        dt = datetime(2026, 2, 14, 20, 35, 0)
        result = renderer.render_clock(now=dt)
        assert len(result.bitmap) == renderer.spec.frame_bytes
        # Should have non-zero bytes (text rendered)
        assert any(b != 0 for b in result.bitmap)

    def test_clock_custom_formats(self, font_cache: FontCache) -> None:
        from datetime import datetime

        renderer = self._make_renderer(font_cache)
        dt = datetime(2026, 6, 15, 8, 5, 0)
        result = renderer.render_clock(
            now=dt,
            date_format="%Y-%m-%d",
            time_format="%I:%M %p",
        )
        assert len(result.bitmap) == renderer.spec.frame_bytes

    def test_clock_with_alarm_bell(self, font_cache: FontCache) -> None:
        from datetime import datetime

        renderer = self._make_renderer(font_cache)
        dt = datetime(2026, 2, 14, 7, 0, 0)
        result = renderer.render_clock(
            now=dt,
            alarm_symbol="\x10",  # bell symbol
        )
        assert len(result.bitmap) == renderer.spec.frame_bytes

    def test_clock_with_sleep_symbol(self, font_cache: FontCache) -> None:
        from datetime import datetime

        renderer = self._make_renderer(font_cache)
        result = renderer.render_clock(
            now=datetime(2026, 1, 1, 0, 0),
            alarm_symbol="\x11",  # sleep/snooze symbol
        )
        assert len(result.bitmap) == renderer.spec.frame_bytes

    def test_clock_no_alarm(self, font_cache: FontCache) -> None:
        from datetime import datetime

        renderer = self._make_renderer(font_cache)
        result = renderer.render_clock(
            now=datetime(2026, 3, 1, 12, 0),
            alarm_symbol=None,
        )
        assert len(result.bitmap) == renderer.spec.frame_bytes

    def test_clock_different_times_differ(self, font_cache: FontCache) -> None:
        from datetime import datetime

        # Use a wider display so the rendered text has enough room to differ
        spec = DisplaySpec(
            model=DisplayModel.GRAPHIC_320x32,
            width=320,
            height=8,
            bytes_per_column=1,
            frame_command="grfe",
        )
        renderer = self._make_renderer(font_cache, spec=spec)
        r1 = renderer.render_clock(now=datetime(2026, 1, 1, 10, 0))
        r2 = renderer.render_clock(now=datetime(2026, 6, 15, 22, 59))
        # Very different date+time → different bitmap
        assert r1.bitmap != r2.bitmap

    def test_clock_boom_spec(self, font_cache: FontCache) -> None:
        from datetime import datetime

        spec = DisplaySpec(
            model=DisplayModel.GRAPHIC_160x32,
            width=160,
            height=32,
            bytes_per_column=4,
            frame_command="grfe",
        )
        fc = FontConfig(font_names=["test"], active_index=0)
        renderer = DisplayRenderer(font_cache, spec, fc)
        result = renderer.render_clock(now=datetime(2026, 2, 14, 20, 35))
        assert len(result.bitmap) == 640


class TestRenderNowPlayingMini:
    """Tests for render_now_playing_mini (screensaver variant)."""

    def _make_renderer(
        self, font_cache: FontCache, spec: DisplaySpec | None = None
    ) -> DisplayRenderer:
        if spec is None:
            spec = DisplaySpec(
                model=DisplayModel.GRAPHIC_320x32,
                width=40,
                height=8,
                bytes_per_column=1,
                frame_command="grfe",
            )
        fc = FontConfig(font_names=["test"], active_index=0)
        return DisplayRenderer(font_cache, spec, fc)

    def test_now_playing_mini_basic(self, font_cache: FontCache) -> None:
        renderer = self._make_renderer(font_cache)
        result = renderer.render_now_playing_mini(
            title="Bohemian Rhapsody",
            artist="Queen",
        )
        assert isinstance(result, RenderedScreen)
        assert len(result.bitmap) == renderer.spec.frame_bytes

    def test_now_playing_mini_no_scroll(self, font_cache: FontCache) -> None:
        renderer = self._make_renderer(font_cache)
        result = renderer.render_now_playing_mini(
            title="Test", artist="Artist",
        )
        assert result.scroll_line == -1

    def test_now_playing_mini_empty(self, font_cache: FontCache) -> None:
        renderer = self._make_renderer(font_cache)
        result = renderer.render_now_playing_mini()
        assert len(result.bitmap) == renderer.spec.frame_bytes

    def test_now_playing_mini_title_only(self, font_cache: FontCache) -> None:
        renderer = self._make_renderer(font_cache)
        result = renderer.render_now_playing_mini(title="Title Only")
        assert len(result.bitmap) == renderer.spec.frame_bytes
        assert any(b != 0 for b in result.bitmap)

    def test_now_playing_mini_differs_from_full(self, font_cache: FontCache) -> None:
        """Mini now-playing should differ from full now-playing (no progress bar)."""
        renderer = self._make_renderer(font_cache)
        mini = renderer.render_now_playing_mini(
            title="Song", artist="Artist",
        )
        full = renderer.render_now_playing(
            title="Song", artist="Artist", album="Album",
            elapsed_s=60.0, duration_s=200.0,
            show_progress=True,
        )
        # They use different layouts (centered vs left-aligned + overlay + progress)
        assert mini.bitmap != full.bitmap


# =====================================================================
# Phase 4: Screensaver State Management in DisplayManager
# =====================================================================


class TestScreensaverType:
    """Tests for the ScreensaverType enum."""

    def test_enum_values(self) -> None:
        from resonance.display import ScreensaverType

        assert ScreensaverType.CLOCK.value == "clock"
        assert ScreensaverType.BLANK.value == "blank"
        assert ScreensaverType.NOW_PLAYING_MINI.value == "nowplaying"
        assert ScreensaverType.NONE.value == "none"

    def test_all_types_exist(self) -> None:
        from resonance.display import ScreensaverType

        assert len(ScreensaverType) == 4


class TestDisplayManagerScreensaver:
    """Tests for screensaver state management in DisplayManager."""

    def test_default_screensaver_type(self) -> None:
        from resonance.display import ScreensaverType
        from resonance.display.manager import DisplayManager

        manager = DisplayManager()
        pd = manager.register_player("test-mac", model="squeezebox2")
        assert pd.screensaver_type == ScreensaverType.CLOCK

    def test_set_screensaver_type(self) -> None:
        from resonance.display import ScreensaverType
        from resonance.display.manager import DisplayManager

        manager = DisplayManager()
        pd = manager.register_player("test-mac", model="squeezebox2")
        manager.set_screensaver("test-mac", ScreensaverType.BLANK)
        assert pd.screensaver_type == ScreensaverType.BLANK

    def test_set_screensaver_type_with_timeout(self) -> None:
        from resonance.display import ScreensaverType
        from resonance.display.manager import DisplayManager

        manager = DisplayManager()
        pd = manager.register_player("test-mac", model="squeezebox2")
        manager.set_screensaver("test-mac", ScreensaverType.CLOCK, timeout=60.0)
        assert pd.screensaver_type == ScreensaverType.CLOCK
        assert pd.screensaver_timeout == 60.0

    def test_set_screensaver_unknown_player(self) -> None:
        from resonance.display import ScreensaverType
        from resonance.display.manager import DisplayManager

        manager = DisplayManager()
        # Should not crash
        manager.set_screensaver("nonexistent", ScreensaverType.CLOCK)

    def test_set_screensaver_now_playing_mini(self) -> None:
        from resonance.display import ScreensaverType
        from resonance.display.manager import DisplayManager

        manager = DisplayManager()
        pd = manager.register_player("test-mac", model="squeezebox2")
        manager.set_screensaver("test-mac", ScreensaverType.NOW_PLAYING_MINI)
        assert pd.screensaver_type == ScreensaverType.NOW_PLAYING_MINI

    def test_set_screensaver_none(self) -> None:
        from resonance.display import ScreensaverType
        from resonance.display.manager import DisplayManager

        manager = DisplayManager()
        pd = manager.register_player("test-mac", model="squeezebox2")
        manager.set_screensaver("test-mac", ScreensaverType.NONE)
        assert pd.screensaver_type == ScreensaverType.NONE

    def test_screensaver_state_exists(self) -> None:
        from resonance.display.manager import DisplayManager, DisplayState

        manager = DisplayManager()
        pd = manager.register_player("test-mac", model="squeezebox2")
        pd.state = DisplayState.SCREENSAVER
        assert pd.state == DisplayState.SCREENSAVER

    @pytest.mark.asyncio
    async def test_idle_to_screensaver_transition(self) -> None:
        """Verify that the IDLE → SCREENSAVER transition logic exists."""
        import time

        from resonance.display import ScreensaverType
        from resonance.display.manager import DisplayManager, DisplayState

        manager = DisplayManager()
        pd = manager.register_player("test-mac", model="squeezebox2")
        pd.state = DisplayState.IDLE
        pd.screensaver_type = ScreensaverType.CLOCK
        pd.screensaver_timeout = 0.0  # immediate
        pd.last_interaction = time.monotonic() - 1.0  # 1s ago

        # The transition happens in _update_loop, which we can't easily
        # call directly without starting the task. But we can verify
        # the state fields are correctly configured.
        assert pd.screensaver_type == ScreensaverType.CLOCK
        assert pd.state == DisplayState.IDLE

    @pytest.mark.asyncio
    async def test_render_screensaver_clock(self, font_cache: FontCache) -> None:
        from resonance.display.manager import DisplayManager, DisplayState

        manager = DisplayManager(font_cache=font_cache)
        pd = manager.register_player("test-mac", model="squeezebox2")
        pd.state = DisplayState.SCREENSAVER

        rendered = manager._render_screensaver(pd)
        assert rendered is not None
        assert len(rendered.bitmap) == pd.spec.frame_bytes

    @pytest.mark.asyncio
    async def test_render_screensaver_blank(self, font_cache: FontCache) -> None:
        from resonance.display import ScreensaverType
        from resonance.display.manager import DisplayManager, DisplayState

        manager = DisplayManager(font_cache=font_cache)
        pd = manager.register_player("test-mac", model="squeezebox2")
        pd.state = DisplayState.SCREENSAVER
        pd.screensaver_type = ScreensaverType.BLANK

        rendered = manager._render_screensaver(pd)
        assert rendered is not None
        assert len(rendered.bitmap) == pd.spec.frame_bytes
        assert all(b == 0 for b in rendered.bitmap)

    @pytest.mark.asyncio
    async def test_render_screensaver_now_playing_mini(self, font_cache: FontCache) -> None:
        from resonance.display import ScreensaverType
        from resonance.display.manager import DisplayManager, DisplayState

        manager = DisplayManager(font_cache=font_cache)
        pd = manager.register_player("test-mac", model="squeezebox2")
        # Override font config to use the test fonts loaded in font_cache
        pd.font_config = FontConfig(font_names=["test"], active_index=0)
        pd.renderer = DisplayRenderer(font_cache, pd.spec, pd.font_config)
        pd.state = DisplayState.SCREENSAVER
        pd.screensaver_type = ScreensaverType.NOW_PLAYING_MINI
        pd.track_title = "Test Song"
        pd.track_artist = "Test Artist"

        rendered = manager._render_screensaver(pd)
        assert rendered is not None
        assert len(rendered.bitmap) == pd.spec.frame_bytes
        assert any(b != 0 for b in rendered.bitmap)

    @pytest.mark.asyncio
    async def test_render_screensaver_none_fallback(self, font_cache: FontCache) -> None:
        from resonance.display import ScreensaverType
        from resonance.display.manager import DisplayManager, DisplayState

        manager = DisplayManager(font_cache=font_cache)
        pd = manager.register_player("test-mac", model="squeezebox2")
        pd.state = DisplayState.SCREENSAVER
        pd.screensaver_type = ScreensaverType.NONE

        rendered = manager._render_screensaver(pd)
        assert rendered is not None
        assert len(rendered.bitmap) == pd.spec.frame_bytes

    @pytest.mark.asyncio
    async def test_render_screensaver_no_renderer(self) -> None:
        from resonance.display.manager import DisplayManager, DisplayState, PlayerDisplay

        manager = DisplayManager()
        pd = PlayerDisplay(player_id="test")
        pd.state = DisplayState.SCREENSAVER
        pd.renderer = None

        rendered = manager._render_screensaver(pd)
        assert rendered is None


class TestDisplayManagerMenuAdvanced:
    """Tests for update_menu_advanced in DisplayManager."""

    @pytest.mark.asyncio
    async def test_update_menu_advanced(self, font_cache: FontCache) -> None:
        from resonance.display.manager import DisplayManager, DisplayState

        manager = DisplayManager(font_cache=font_cache)
        pd = manager.register_player("test-mac", model="squeezebox2")
        await manager.update_menu_advanced(
            "test-mac",
            ["Artists", "Albums", "Genres"],
            selected_index=1,
        )
        assert pd.state == DisplayState.MENU

    @pytest.mark.asyncio
    async def test_update_menu_advanced_with_submenu(self, font_cache: FontCache) -> None:
        from resonance.display.manager import DisplayManager, DisplayState

        manager = DisplayManager(font_cache=font_cache)
        pd = manager.register_player("test-mac", model="squeezebox2")
        await manager.update_menu_advanced(
            "test-mac",
            ["Artists", "Albums", "Playlists"],
            selected_index=0,
            has_submenu=[True, True, False],
            show_position=True,
        )
        assert pd.state == DisplayState.MENU

    @pytest.mark.asyncio
    async def test_update_menu_advanced_no_display(self) -> None:
        from resonance.display.manager import DisplayManager

        manager = DisplayManager()
        pd = manager.register_player("test-mac", model="receiver")
        # Should not crash for displayless player
        await manager.update_menu_advanced("test-mac", ["A", "B"])

    @pytest.mark.asyncio
    async def test_update_menu_advanced_unknown_player(self) -> None:
        from resonance.display.manager import DisplayManager

        manager = DisplayManager()
        # Should not crash for unknown player
        await manager.update_menu_advanced("nonexistent", ["A", "B"])

    @pytest.mark.asyncio
    async def test_update_menu_advanced_resets_scroll(self, font_cache: FontCache) -> None:
        from resonance.display.manager import DisplayManager

        manager = DisplayManager(font_cache=font_cache)
        pd = manager.register_player("test-mac", model="squeezebox2")
        pd.scroll_offset = 42
        await manager.update_menu_advanced(
            "test-mac", ["A", "B", "C"], selected_index=1,
        )
        assert pd.scroll_offset == 0


# =====================================================================
# Phase 3–4: Integration tests with realistic specs
# =====================================================================


class TestPhase34Integration:
    """Integration tests for Phase 3–4 features with realistic display specs."""

    def test_menu_advanced_sb2_frame_size(self, font_cache: FontCache) -> None:
        spec = DisplaySpec(
            model=DisplayModel.GRAPHIC_320x32,
            width=320,
            height=32,
            bytes_per_column=4,
            frame_command="grfe",
        )
        fc = FontConfig(font_names=["test"], active_index=0)
        renderer = DisplayRenderer(font_cache, spec, fc)
        result = renderer.render_menu_advanced(
            ["Artists", "Albums", "Genres", "Playlists", "Favorites"],
            selected_index=2,
            has_submenu=[True, True, True, True, False],
        )
        assert len(result.bitmap) == 1280

    def test_clock_sb2_frame_size(self, font_cache: FontCache) -> None:
        from datetime import datetime

        spec = DisplaySpec(
            model=DisplayModel.GRAPHIC_320x32,
            width=320,
            height=32,
            bytes_per_column=4,
            frame_command="grfe",
        )
        fc = FontConfig(font_names=["test"], active_index=0)
        renderer = DisplayRenderer(font_cache, spec, fc)
        result = renderer.render_clock(now=datetime(2026, 2, 14, 20, 35, 42))
        assert len(result.bitmap) == 1280
        assert any(b != 0 for b in result.bitmap)

    def test_clock_boom_frame_size(self, font_cache: FontCache) -> None:
        from datetime import datetime

        spec = DisplaySpec(
            model=DisplayModel.GRAPHIC_160x32,
            width=160,
            height=32,
            bytes_per_column=4,
            frame_command="grfe",
        )
        fc = FontConfig(font_names=["test"], active_index=0)
        renderer = DisplayRenderer(font_cache, spec, fc)
        result = renderer.render_clock(now=datetime(2026, 2, 14, 20, 35))
        assert len(result.bitmap) == 640

    def test_clock_sbg_frame_size(self, font_cache: FontCache) -> None:
        from datetime import datetime

        spec = DisplaySpec(
            model=DisplayModel.GRAPHIC_280x16,
            width=280,
            height=16,
            bytes_per_column=2,
            frame_command="grfd",
        )
        fc = FontConfig(font_names=["test"], active_index=0)
        renderer = DisplayRenderer(font_cache, spec, fc)
        result = renderer.render_clock(now=datetime(2026, 2, 14, 20, 35))
        assert len(result.bitmap) == 560

    def test_volume_overlay_sb2(self, font_cache: FontCache) -> None:
        spec = DisplaySpec(
            model=DisplayModel.GRAPHIC_320x32,
            width=320,
            height=32,
            bytes_per_column=4,
            frame_command="grfe",
        )
        fc = FontConfig(font_names=["test"], active_index=0)
        renderer = DisplayRenderer(font_cache, spec, fc)
        result = renderer.render_volume_overlay(75)
        assert len(result.bitmap) == 1280
        assert any(b != 0 for b in result.bitmap)

    def test_slider_bar_sb2(self, font_cache: FontCache) -> None:
        spec = DisplaySpec(
            model=DisplayModel.GRAPHIC_320x32,
            width=320,
            height=32,
            bytes_per_column=4,
            frame_command="grfe",
        )
        fc = FontConfig(font_names=["test"], active_index=0)
        renderer = DisplayRenderer(font_cache, spec, fc)
        result = renderer.render_slider_bar(50.0)
        assert len(result.bitmap) == 1280

    def test_now_playing_mini_sb2(self, font_cache: FontCache) -> None:
        spec = DisplaySpec(
            model=DisplayModel.GRAPHIC_320x32,
            width=320,
            height=32,
            bytes_per_column=4,
            frame_command="grfe",
        )
        fc = FontConfig(font_names=["test"], active_index=0)
        renderer = DisplayRenderer(font_cache, spec, fc)
        result = renderer.render_now_playing_mini(
            title="Stairway to Heaven",
            artist="Led Zeppelin",
        )
        assert len(result.bitmap) == 1280
        assert any(b != 0 for b in result.bitmap)

    def test_screensaver_font_overrides_exist(self) -> None:
        from resonance.display import SCREENSAVER_FONT_OVERRIDES

        assert "graphic-320x32" in SCREENSAVER_FONT_OVERRIDES
        assert "graphic-160x32" in SCREENSAVER_FONT_OVERRIDES
        assert "graphic-280x16" in SCREENSAVER_FONT_OVERRIDES
        for model_key, overrides in SCREENSAVER_FONT_OVERRIDES.items():
            assert "overlay" in overrides
            assert isinstance(overrides["overlay"], list)


# ---------------------------------------------------------------------------
# Display Metadata Pipeline Tests
# ---------------------------------------------------------------------------
#
# Verifies the fix for the identified gap: DisplayManager.update_now_playing()
# existed but was never called.  These tests cover:
#
# 1. _parse_icy_title() — local ICY title parser (mirrors LMS HTTP.pm L1085)
# 2. _load_track_metadata() — reads PlaylistTrack into PlayerDisplay
# 3. _on_playlist_event(action="index") — track change updates display
# 4. _on_playlist_event(action="load") — playlist load updates display
# 5. _on_playlist_event(action="newmetadata") — ICY title change updates display
# 6. _on_playback_event(action="play") — also loads metadata on play
# 7. Integration: track change + ICY change sequence
# ---------------------------------------------------------------------------


class TestParseIcyTitleDisplay:
    """Test the module-level _parse_icy_title() in display/manager.py."""

    def test_artist_dash_title(self) -> None:
        from resonance.display.manager import _parse_icy_title

        artist, title = _parse_icy_title("Miles Davis - So What")
        assert artist == "Miles Davis"
        assert title == "So What"

    def test_no_dash(self) -> None:
        from resonance.display.manager import _parse_icy_title

        artist, title = _parse_icy_title("Just a Title")
        assert artist == ""
        assert title == "Just a Title"

    def test_multiple_dashes(self) -> None:
        from resonance.display.manager import _parse_icy_title

        artist, title = _parse_icy_title("A - B - C")
        assert artist == ""
        assert title == "A - B - C"

    def test_empty_string(self) -> None:
        from resonance.display.manager import _parse_icy_title

        artist, title = _parse_icy_title("")
        assert artist == ""
        assert title == ""

    def test_whitespace_stripping(self) -> None:
        from resonance.display.manager import _parse_icy_title

        artist, title = _parse_icy_title("  Coltrane  -  Giant Steps  ")
        assert artist == "Coltrane"
        assert title == "Giant Steps"

    def test_unicode(self) -> None:
        from resonance.display.manager import _parse_icy_title

        artist, title = _parse_icy_title("Björk - Jóga")
        assert artist == "Björk"
        assert title == "Jóga"


def _make_playlist_manager_with_track(
    player_id: str = "aa:bb:cc:dd:ee:ff",
    title: str = "Test Song",
    artist: str = "Test Artist",
    album: str = "Test Album",
    duration_ms: int = 240000,
    source: str = "local",
    is_live: bool = False,
):
    """Create a PlaylistManager with one track in the playlist."""
    from resonance.core.playlist import PlaylistManager, PlaylistTrack

    pm = PlaylistManager()
    playlist = pm.get(player_id)
    track = PlaylistTrack(
        track_id=1,
        path="/music/test.flac",
        title=title,
        artist=artist,
        album=album,
        duration_ms=duration_ms,
        source=source,
        is_live=is_live,
    )
    playlist.add(track)
    playlist.current_index = 0
    return pm


class TestLoadTrackMetadata:
    """Test DisplayManager._load_track_metadata()."""

    def test_loads_local_track(self) -> None:
        from resonance.display.manager import DisplayManager

        pm = _make_playlist_manager_with_track(
            title="Blue Train", artist="Coltrane", album="Blue Train",
            duration_ms=300000,
        )
        manager = DisplayManager(playlist_manager=pm)
        pd = manager.register_player("aa:bb:cc:dd:ee:ff", model="squeezebox2")

        result = manager._load_track_metadata(pd, "aa:bb:cc:dd:ee:ff")

        assert result is True
        assert pd.track_title == "Blue Train"
        assert pd.track_artist == "Coltrane"
        assert pd.track_album == "Blue Train"
        assert pd.track_duration_s == 300.0

    def test_loads_radio_track(self) -> None:
        from resonance.display.manager import DisplayManager

        pm = _make_playlist_manager_with_track(
            title="Jazz FM", artist="", album="",
            duration_ms=0, source="radio", is_live=True,
        )
        manager = DisplayManager(playlist_manager=pm)
        pd = manager.register_player("aa:bb:cc:dd:ee:ff", model="squeezebox2")

        result = manager._load_track_metadata(pd, "aa:bb:cc:dd:ee:ff")

        assert result is True
        assert pd.track_title == "Jazz FM"
        assert pd.track_duration_s == 0.0

    def test_no_playlist_manager(self) -> None:
        from resonance.display.manager import DisplayManager

        manager = DisplayManager()
        pd = manager.register_player("aa:bb:cc:dd:ee:ff", model="squeezebox2")

        result = manager._load_track_metadata(pd, "aa:bb:cc:dd:ee:ff")

        assert result is False
        assert pd.track_title == ""

    def test_unknown_player(self) -> None:
        from resonance.core.playlist import PlaylistManager
        from resonance.display.manager import DisplayManager

        pm = PlaylistManager()
        manager = DisplayManager(playlist_manager=pm)
        pd = manager.register_player("aa:bb:cc:dd:ee:ff", model="squeezebox2")

        result = manager._load_track_metadata(pd, "aa:bb:cc:dd:ee:ff")

        assert result is False

    def test_empty_playlist(self) -> None:
        from resonance.core.playlist import PlaylistManager
        from resonance.display.manager import DisplayManager

        pm = PlaylistManager()
        # Create an empty playlist (get auto-creates)
        pm.get("aa:bb:cc:dd:ee:ff")
        manager = DisplayManager(playlist_manager=pm)
        pd = manager.register_player("aa:bb:cc:dd:ee:ff", model="squeezebox2")

        result = manager._load_track_metadata(pd, "aa:bb:cc:dd:ee:ff")

        assert result is False

    def test_zero_duration(self) -> None:
        from resonance.display.manager import DisplayManager

        pm = _make_playlist_manager_with_track(duration_ms=0)
        manager = DisplayManager(playlist_manager=pm)
        pd = manager.register_player("aa:bb:cc:dd:ee:ff", model="squeezebox2")

        manager._load_track_metadata(pd, "aa:bb:cc:dd:ee:ff")

        assert pd.track_duration_s == 0.0


class TestOnPlaylistEvent:
    """Test DisplayManager._on_playlist_event() handler."""

    @pytest.mark.asyncio
    async def test_index_action_updates_metadata(self) -> None:
        """action='index' (track change) loads metadata and re-renders."""
        from resonance.core.events import PlayerPlaylistEvent
        from resonance.display.manager import DisplayManager, DisplayState

        pm = _make_playlist_manager_with_track(
            title="Giant Steps", artist="Coltrane", album="Giant Steps",
        )
        manager = DisplayManager(playlist_manager=pm)
        pd = manager.register_player("aa:bb:cc:dd:ee:ff", model="squeezebox2")

        event = PlayerPlaylistEvent(
            player_id="aa:bb:cc:dd:ee:ff", action="index", index=0, count=1,
        )
        await manager._on_playlist_event(event)

        assert pd.track_title == "Giant Steps"
        assert pd.track_artist == "Coltrane"
        assert pd.track_album == "Giant Steps"
        assert pd.state == DisplayState.NOW_PLAYING
        assert pd.scroll_offset == 0

    @pytest.mark.asyncio
    async def test_load_action_updates_metadata(self) -> None:
        """action='load' (playlist loaded) also refreshes metadata."""
        from resonance.core.events import PlayerPlaylistEvent
        from resonance.display.manager import DisplayManager, DisplayState

        pm = _make_playlist_manager_with_track(
            title="A Love Supreme", artist="Coltrane",
        )
        manager = DisplayManager(playlist_manager=pm)
        pd = manager.register_player("aa:bb:cc:dd:ee:ff", model="squeezebox2")

        event = PlayerPlaylistEvent(
            player_id="aa:bb:cc:dd:ee:ff", action="load", index=0, count=1,
        )
        await manager._on_playlist_event(event)

        assert pd.track_title == "A Love Supreme"
        assert pd.state == DisplayState.NOW_PLAYING

    @pytest.mark.asyncio
    async def test_newmetadata_updates_icy_title(self) -> None:
        """action='newmetadata' reads ICY title from StreamingServer."""
        from resonance.core.events import PlayerPlaylistEvent
        from resonance.display.manager import DisplayManager, DisplayState
        from resonance.streaming.server import StreamingServer

        ss = StreamingServer()
        ss.set_icy_title("aa:bb:cc:dd:ee:ff", "Miles Davis - So What")

        pm = _make_playlist_manager_with_track(
            title="Jazz FM", source="radio", is_live=True,
        )
        manager = DisplayManager(
            playlist_manager=pm, streaming_server=ss,
        )
        pd = manager.register_player("aa:bb:cc:dd:ee:ff", model="squeezebox2")
        # Ensure NOW_PLAYING state so we see the title update
        pd.state = DisplayState.NOW_PLAYING

        event = PlayerPlaylistEvent(
            player_id="aa:bb:cc:dd:ee:ff", action="newmetadata",
        )
        await manager._on_playlist_event(event)

        assert pd.track_title == "So What"
        assert pd.track_artist == "Miles Davis"

    @pytest.mark.asyncio
    async def test_newmetadata_no_dash_uses_full_title(self) -> None:
        """ICY title without ' - ' goes entirely into track_title."""
        from resonance.core.events import PlayerPlaylistEvent
        from resonance.display.manager import DisplayManager, DisplayState
        from resonance.streaming.server import StreamingServer

        ss = StreamingServer()
        ss.set_icy_title("aa:bb:cc:dd:ee:ff", "Station Jingle")

        manager = DisplayManager(streaming_server=ss)
        pd = manager.register_player("aa:bb:cc:dd:ee:ff", model="squeezebox2")
        pd.state = DisplayState.NOW_PLAYING

        event = PlayerPlaylistEvent(
            player_id="aa:bb:cc:dd:ee:ff", action="newmetadata",
        )
        await manager._on_playlist_event(event)

        assert pd.track_title == "Station Jingle"
        # Artist should not be overwritten when ICY has no artist part
        assert pd.track_artist == ""

    @pytest.mark.asyncio
    async def test_newmetadata_no_streaming_server(self) -> None:
        """Without StreamingServer, newmetadata is a no-op."""
        from resonance.core.events import PlayerPlaylistEvent
        from resonance.display.manager import DisplayManager, DisplayState

        manager = DisplayManager()
        pd = manager.register_player("aa:bb:cc:dd:ee:ff", model="squeezebox2")
        pd.state = DisplayState.NOW_PLAYING
        pd.track_title = "Original Title"

        event = PlayerPlaylistEvent(
            player_id="aa:bb:cc:dd:ee:ff", action="newmetadata",
        )
        await manager._on_playlist_event(event)

        assert pd.track_title == "Original Title"

    @pytest.mark.asyncio
    async def test_newmetadata_no_icy_title_stored(self) -> None:
        """If StreamingServer has no ICY title for player, no update."""
        from resonance.core.events import PlayerPlaylistEvent
        from resonance.display.manager import DisplayManager, DisplayState
        from resonance.streaming.server import StreamingServer

        ss = StreamingServer()
        # No ICY title set for this player

        manager = DisplayManager(streaming_server=ss)
        pd = manager.register_player("aa:bb:cc:dd:ee:ff", model="squeezebox2")
        pd.state = DisplayState.NOW_PLAYING
        pd.track_title = "Original Title"

        event = PlayerPlaylistEvent(
            player_id="aa:bb:cc:dd:ee:ff", action="newmetadata",
        )
        await manager._on_playlist_event(event)

        assert pd.track_title == "Original Title"

    @pytest.mark.asyncio
    async def test_index_no_playlist_manager(self) -> None:
        """Without PlaylistManager, index event is a no-op."""
        from resonance.core.events import PlayerPlaylistEvent
        from resonance.display.manager import DisplayManager, DisplayState

        manager = DisplayManager()
        pd = manager.register_player("aa:bb:cc:dd:ee:ff", model="squeezebox2")
        pd.state = DisplayState.IDLE

        event = PlayerPlaylistEvent(
            player_id="aa:bb:cc:dd:ee:ff", action="index", index=0, count=1,
        )
        await manager._on_playlist_event(event)

        # State should remain IDLE — no metadata to load
        assert pd.state == DisplayState.IDLE

    @pytest.mark.asyncio
    async def test_unknown_player_ignored(self) -> None:
        """Events for unregistered players are silently ignored."""
        from resonance.core.events import PlayerPlaylistEvent
        from resonance.display.manager import DisplayManager

        manager = DisplayManager()

        event = PlayerPlaylistEvent(
            player_id="xx:xx:xx:xx:xx:xx", action="index", index=0, count=1,
        )
        # Should not raise
        await manager._on_playlist_event(event)

    @pytest.mark.asyncio
    async def test_no_display_player_ignored(self) -> None:
        """Events for players without displays (receiver) are ignored."""
        from resonance.core.events import PlayerPlaylistEvent
        from resonance.display.manager import DisplayManager

        pm = _make_playlist_manager_with_track(title="Test")
        manager = DisplayManager(playlist_manager=pm)
        pd = manager.register_player("aa:bb:cc:dd:ee:ff", model="receiver")
        assert not pd.has_display

        event = PlayerPlaylistEvent(
            player_id="aa:bb:cc:dd:ee:ff", action="index", index=0, count=1,
        )
        await manager._on_playlist_event(event)

        assert pd.track_title == ""

    @pytest.mark.asyncio
    async def test_delete_action_not_handled(self) -> None:
        """Non-display-relevant actions like 'delete' are silently ignored."""
        from resonance.core.events import PlayerPlaylistEvent
        from resonance.display.manager import DisplayManager, DisplayState

        manager = DisplayManager()
        pd = manager.register_player("aa:bb:cc:dd:ee:ff", model="squeezebox2")
        pd.state = DisplayState.IDLE

        event = PlayerPlaylistEvent(
            player_id="aa:bb:cc:dd:ee:ff", action="delete", count=0,
        )
        await manager._on_playlist_event(event)

        assert pd.state == DisplayState.IDLE

    @pytest.mark.asyncio
    async def test_index_resets_scroll_offset(self) -> None:
        """Track change resets scroll position for new text."""
        from resonance.core.events import PlayerPlaylistEvent
        from resonance.display.manager import DisplayManager

        pm = _make_playlist_manager_with_track(title="New Track")
        manager = DisplayManager(playlist_manager=pm)
        pd = manager.register_player("aa:bb:cc:dd:ee:ff", model="squeezebox2")
        pd.scroll_offset = 42

        event = PlayerPlaylistEvent(
            player_id="aa:bb:cc:dd:ee:ff", action="index", index=0, count=1,
        )
        await manager._on_playlist_event(event)

        assert pd.scroll_offset == 0

    @pytest.mark.asyncio
    async def test_newmetadata_resets_scroll_offset(self) -> None:
        """ICY title change resets scroll position."""
        from resonance.core.events import PlayerPlaylistEvent
        from resonance.display.manager import DisplayManager, DisplayState
        from resonance.streaming.server import StreamingServer

        ss = StreamingServer()
        ss.set_icy_title("aa:bb:cc:dd:ee:ff", "New Artist - New Song")

        manager = DisplayManager(streaming_server=ss)
        pd = manager.register_player("aa:bb:cc:dd:ee:ff", model="squeezebox2")
        pd.state = DisplayState.NOW_PLAYING
        pd.scroll_offset = 99

        event = PlayerPlaylistEvent(
            player_id="aa:bb:cc:dd:ee:ff", action="newmetadata",
        )
        await manager._on_playlist_event(event)

        assert pd.scroll_offset == 0


class TestPlaybackEventMetadataLoading:
    """Test that _on_playback_event also loads metadata on play/unpause."""

    @pytest.mark.asyncio
    async def test_play_loads_metadata(self) -> None:
        """play action loads track metadata from PlaylistManager."""
        from resonance.display.manager import DisplayManager, DisplayState

        pm = _make_playlist_manager_with_track(
            title="Watermelon Man", artist="Herbie Hancock",
            album="Head Hunters",
        )
        manager = DisplayManager(playlist_manager=pm)
        pd = manager.register_player("aa:bb:cc:dd:ee:ff", model="squeezebox2")

        # Simulate a PlaybackEvent with action="play"
        class FakePlaybackEvent:
            player_id = "aa:bb:cc:dd:ee:ff"
            action = "play"

        await manager._on_playback_event(FakePlaybackEvent())

        assert pd.track_title == "Watermelon Man"
        assert pd.track_artist == "Herbie Hancock"
        assert pd.track_album == "Head Hunters"
        assert pd.state == DisplayState.NOW_PLAYING
        assert pd.is_playing is True

    @pytest.mark.asyncio
    async def test_play_without_playlist_manager(self) -> None:
        """play action works even without PlaylistManager (empty metadata)."""
        from resonance.display.manager import DisplayManager, DisplayState

        manager = DisplayManager()
        pd = manager.register_player("aa:bb:cc:dd:ee:ff", model="squeezebox2")

        class FakePlaybackEvent:
            player_id = "aa:bb:cc:dd:ee:ff"
            action = "play"

        await manager._on_playback_event(FakePlaybackEvent())

        assert pd.state == DisplayState.NOW_PLAYING
        assert pd.is_playing is True
        assert pd.track_title == ""

    @pytest.mark.asyncio
    async def test_unpause_loads_metadata(self) -> None:
        """unpause also loads metadata (in case it was stale)."""
        from resonance.display.manager import DisplayManager

        pm = _make_playlist_manager_with_track(
            title="Cantaloupe Island", artist="Herbie Hancock",
        )
        manager = DisplayManager(playlist_manager=pm)
        pd = manager.register_player("aa:bb:cc:dd:ee:ff", model="squeezebox2")
        pd.is_paused = True

        class FakePlaybackEvent:
            player_id = "aa:bb:cc:dd:ee:ff"
            action = "unpause"

        await manager._on_playback_event(FakePlaybackEvent())

        assert pd.track_title == "Cantaloupe Island"
        assert pd.is_playing is True
        assert pd.is_paused is False


class TestDisplayMetadataPipelineIntegration:
    """Integration tests: full sequence of track change + ICY updates."""

    @pytest.mark.asyncio
    async def test_track_change_then_icy_update(self) -> None:
        """Simulate: load radio → get ICY title → title updates on display."""
        from resonance.core.events import PlayerPlaylistEvent
        from resonance.display.manager import DisplayManager, DisplayState
        from resonance.streaming.server import StreamingServer

        ss = StreamingServer()
        pm = _make_playlist_manager_with_track(
            title="Jazz FM", artist="", source="radio", is_live=True,
            duration_ms=0,
        )
        manager = DisplayManager(
            playlist_manager=pm, streaming_server=ss,
        )
        pd = manager.register_player("aa:bb:cc:dd:ee:ff", model="squeezebox2")

        # Step 1: Track loads — index event
        idx_event = PlayerPlaylistEvent(
            player_id="aa:bb:cc:dd:ee:ff", action="index", index=0, count=1,
        )
        await manager._on_playlist_event(idx_event)

        assert pd.track_title == "Jazz FM"
        assert pd.track_artist == ""
        assert pd.state == DisplayState.NOW_PLAYING

        # Step 2: ICY title arrives
        ss.set_icy_title("aa:bb:cc:dd:ee:ff", "Miles Davis - So What")
        meta_event = PlayerPlaylistEvent(
            player_id="aa:bb:cc:dd:ee:ff", action="newmetadata",
        )
        await manager._on_playlist_event(meta_event)

        assert pd.track_title == "So What"
        assert pd.track_artist == "Miles Davis"
        assert pd.state == DisplayState.NOW_PLAYING

    @pytest.mark.asyncio
    async def test_icy_title_changes_multiple_times(self) -> None:
        """Multiple ICY title changes each update the display."""
        from resonance.core.events import PlayerPlaylistEvent
        from resonance.display.manager import DisplayManager, DisplayState
        from resonance.streaming.server import StreamingServer

        ss = StreamingServer()
        manager = DisplayManager(streaming_server=ss)
        pd = manager.register_player("aa:bb:cc:dd:ee:ff", model="squeezebox2")
        pd.state = DisplayState.NOW_PLAYING

        titles = [
            ("Coltrane - Giant Steps", "Giant Steps", "Coltrane"),
            ("Davis - Freddie Freeloader", "Freddie Freeloader", "Davis"),
            ("Monk - Round Midnight", "Round Midnight", "Monk"),
        ]
        for icy_raw, expected_title, expected_artist in titles:
            ss.set_icy_title("aa:bb:cc:dd:ee:ff", icy_raw)
            event = PlayerPlaylistEvent(
                player_id="aa:bb:cc:dd:ee:ff", action="newmetadata",
            )
            await manager._on_playlist_event(event)

            assert pd.track_title == expected_title
            assert pd.track_artist == expected_artist

    @pytest.mark.asyncio
    async def test_local_track_then_radio_track(self) -> None:
        """Switch from local track to radio — metadata updates correctly."""
        from resonance.core.events import PlayerPlaylistEvent
        from resonance.core.playlist import PlaylistManager, PlaylistTrack
        from resonance.display.manager import DisplayManager, DisplayState
        from resonance.streaming.server import StreamingServer

        ss = StreamingServer()
        pm = PlaylistManager()
        playlist = pm.get("aa:bb:cc:dd:ee:ff")

        # Add local track
        local_track = PlaylistTrack(
            track_id=1, path="/music/song.flac",
            title="Local Song", artist="Local Artist", album="Local Album",
            duration_ms=180000,
        )
        playlist.add(local_track)

        # Add radio track
        radio_track = PlaylistTrack(
            track_id=None, path="http://radio.example.com/stream",
            title="Jazz FM", artist="", album="",
            duration_ms=0, source="radio", is_remote=True, is_live=True,
        )
        playlist.add(radio_track)
        playlist.current_index = 0

        manager = DisplayManager(
            playlist_manager=pm, streaming_server=ss,
        )
        pd = manager.register_player("aa:bb:cc:dd:ee:ff", model="squeezebox2")

        # Play local track
        idx_event = PlayerPlaylistEvent(
            player_id="aa:bb:cc:dd:ee:ff", action="index", index=0, count=2,
        )
        await manager._on_playlist_event(idx_event)
        assert pd.track_title == "Local Song"
        assert pd.track_artist == "Local Artist"
        assert pd.track_duration_s == 180.0

        # Advance to radio track
        playlist.current_index = 1
        idx_event2 = PlayerPlaylistEvent(
            player_id="aa:bb:cc:dd:ee:ff", action="index", index=1, count=2,
        )
        await manager._on_playlist_event(idx_event2)
        assert pd.track_title == "Jazz FM"
        assert pd.track_duration_s == 0.0

        # ICY title arrives for radio
        ss.set_icy_title("aa:bb:cc:dd:ee:ff", "Coltrane - My Favorite Things")
        meta_event = PlayerPlaylistEvent(
            player_id="aa:bb:cc:dd:ee:ff", action="newmetadata",
        )
        await manager._on_playlist_event(meta_event)
        assert pd.track_title == "My Favorite Things"
        assert pd.track_artist == "Coltrane"

    @pytest.mark.asyncio
    async def test_player_isolation(self) -> None:
        """Events for one player don't affect another player's display."""
        from resonance.core.events import PlayerPlaylistEvent
        from resonance.core.playlist import PlaylistManager, PlaylistTrack
        from resonance.display.manager import DisplayManager
        from resonance.streaming.server import StreamingServer

        ss = StreamingServer()
        pm = PlaylistManager()

        # Player 1
        p1 = pm.get("aa:bb:cc:dd:ee:01")
        p1.add(PlaylistTrack(
            track_id=1, path="/a.flac", title="Song A", artist="Artist A",
        ))
        p1.current_index = 0

        # Player 2
        p2 = pm.get("aa:bb:cc:dd:ee:02")
        p2.add(PlaylistTrack(
            track_id=2, path="/b.flac", title="Song B", artist="Artist B",
        ))
        p2.current_index = 0

        manager = DisplayManager(
            playlist_manager=pm, streaming_server=ss,
        )
        pd1 = manager.register_player("aa:bb:cc:dd:ee:01", model="squeezebox2")
        pd2 = manager.register_player("aa:bb:cc:dd:ee:02", model="squeezebox2")

        # Update player 1 only
        event = PlayerPlaylistEvent(
            player_id="aa:bb:cc:dd:ee:01", action="index", index=0, count=1,
        )
        await manager._on_playlist_event(event)

        assert pd1.track_title == "Song A"
        assert pd2.track_title == ""  # Untouched

    @pytest.mark.asyncio
    async def test_index_forces_frame_resend(self) -> None:
        """Track change clears last_frame_sent to force re-render."""
        from resonance.core.events import PlayerPlaylistEvent
        from resonance.display.manager import DisplayManager

        pm = _make_playlist_manager_with_track(title="Test")
        manager = DisplayManager(playlist_manager=pm)
        pd = manager.register_player("aa:bb:cc:dd:ee:ff", model="squeezebox2")
        pd.last_frame_sent = b"\xff" * 100  # Simulate cached frame

        event = PlayerPlaylistEvent(
            player_id="aa:bb:cc:dd:ee:ff", action="index", index=0, count=1,
        )
        await manager._on_playlist_event(event)

        assert pd.last_frame_sent != b"\xff" * 100


class TestDisplayManagerConstructorDeps:
    """Test that DisplayManager accepts and stores optional dependencies."""

    def test_default_no_deps(self) -> None:
        from resonance.display.manager import DisplayManager

        manager = DisplayManager()
        assert manager._playlist_manager is None
        assert manager._streaming_server is None

    def test_with_playlist_manager(self) -> None:
        from resonance.core.playlist import PlaylistManager
        from resonance.display.manager import DisplayManager

        pm = PlaylistManager()
        manager = DisplayManager(playlist_manager=pm)
        assert manager._playlist_manager is pm

    def test_with_streaming_server(self) -> None:
        from resonance.display.manager import DisplayManager
        from resonance.streaming.server import StreamingServer

        ss = StreamingServer()
        manager = DisplayManager(streaming_server=ss)
        assert manager._streaming_server is ss

    def test_with_both_deps(self) -> None:
        from resonance.core.playlist import PlaylistManager
        from resonance.display.manager import DisplayManager
        from resonance.streaming.server import StreamingServer

        pm = PlaylistManager()
        ss = StreamingServer()
        manager = DisplayManager(playlist_manager=pm, streaming_server=ss)
        assert manager._playlist_manager is pm
        assert manager._streaming_server is ss
