"""
Tests for the DSD binary header parser (DSF and DFF/DSDIFF formats).

Tests cover:
- DSF header parsing (magic, fmt chunk, data chunk, ID3v2 tags)
- DFF header parsing (FRM8 container, PROP/FS/CHNL chunks, DIAR/DITI tags)
- Duration calculation for both formats
- Edge cases: corrupted headers, truncated files, missing tags
- Public API: parse_dsd_file(), is_dsd_file(), dsd_rate_name()

LMS reference: Slim::Formats::DSF, Slim::Formats::DFF, Slim::Formats::DSD
"""

from __future__ import annotations

import struct
from pathlib import Path
from tempfile import NamedTemporaryFile

import pytest

from resonance.core.dsd_parser import (
    DSDInfo,
    DSDParseError,
    dsd_rate_name,
    is_dsd_file,
    parse_dsd_file,
)

# ---------------------------------------------------------------------------
# DSF test file builders
# ---------------------------------------------------------------------------

def _build_dsf_file(
    *,
    sample_rate: int = 2822400,
    channels: int = 2,
    bits_per_sample: int = 1,
    sample_count: int = 2822400,  # 1 second at DSD64
    block_size_per_channel: int = 4096,
    audio_data: bytes | None = None,
    metadata_offset: int = 0,
    id3v2_block: bytes = b"",
    bad_dsd_magic: bytes | None = None,
    bad_fmt_magic: bytes | None = None,
    bad_data_magic: bytes | None = None,
    fmt_chunk_size: int = 52,
) -> bytes:
    """Build a minimal valid DSF file for testing."""

    # DSD chunk (28 bytes)
    dsd_magic = bad_dsd_magic or b"DSD "
    # fmt chunk (52 bytes)
    fmt_magic = bad_fmt_magic or b"fmt "
    format_version = 1
    format_id = 0  # DSD raw

    fmt_body = struct.pack(
        "<QIIIIIIQII",
        fmt_chunk_size,         # chunk size
        format_version,
        format_id,
        2,                      # channel_type (stereo)
        channels,
        sample_rate,
        bits_per_sample,
        sample_count,
        block_size_per_channel,
        0,                      # reserved
    )
    fmt_chunk = fmt_magic + fmt_body

    # data chunk
    data_magic = bad_data_magic or b"data"
    if audio_data is None:
        # Generate minimal fake audio data (one block per channel)
        audio_data = b"\x00" * (block_size_per_channel * channels)
    data_chunk_size = len(audio_data) + 12  # 12 = data header size
    data_chunk = data_magic + struct.pack("<Q", data_chunk_size) + audio_data

    # Total file size
    total_content = fmt_chunk + data_chunk + id3v2_block
    total_file_size = 28 + len(total_content)

    # Metadata offset: point to ID3v2 block if present
    if id3v2_block:
        metadata_offset = 28 + len(fmt_chunk) + len(data_chunk)

    dsd_chunk = dsd_magic + struct.pack("<QQQ", 28, total_file_size, metadata_offset)

    return dsd_chunk + total_content


def _build_minimal_id3v2() -> bytes:
    """Build a minimal ID3v2.3 header with a TIT2 (title) frame."""
    # ID3v2.3 header: "ID3" + version(2B) + flags(1B) + size(4B syncsafe)
    # TIT2 frame: frame_id(4B) + size(4B) + flags(2B) + encoding(1B) + text

    title_text = b"Test DSD Title"
    frame_data = b"\x03" + title_text  # 0x03 = UTF-8 encoding
    frame_size = len(frame_data)

    tit2_frame = b"TIT2" + struct.pack(">I", frame_size) + b"\x00\x00" + frame_data

    # Artist frame (TPE1)
    artist_text = b"Test DSD Artist"
    artist_frame_data = b"\x03" + artist_text
    tpe1_frame = b"TPE1" + struct.pack(">I", len(artist_frame_data)) + b"\x00\x00" + artist_frame_data

    # Album frame (TALB)
    album_text = b"Test DSD Album"
    album_frame_data = b"\x03" + album_text
    talb_frame = b"TALB" + struct.pack(">I", len(album_frame_data)) + b"\x00\x00" + album_frame_data

    all_frames = tit2_frame + tpe1_frame + talb_frame

    # Syncsafe size encoding (4 bytes, 7 bits per byte)
    total_size = len(all_frames)
    syncsafe = (
        ((total_size >> 21) & 0x7F,)
        + ((total_size >> 14) & 0x7F,)
        + ((total_size >> 7) & 0x7F,)
        + (total_size & 0x7F,)
    )

    id3_header = b"ID3" + bytes([3, 0, 0]) + bytes(syncsafe)
    return id3_header + all_frames


# ---------------------------------------------------------------------------
# DFF test file builders
# ---------------------------------------------------------------------------

def _build_dff_file(
    *,
    sample_rate: int = 2822400,
    channels: int = 2,
    audio_data_size: int = 705600,  # ~1 second at DSD64 stereo
    artist: str | None = None,
    title: str | None = None,
    bad_frm8_magic: bytes | None = None,
    bad_form_type: bytes | None = None,
) -> bytes:
    """Build a minimal valid DFF (DSDIFF) file for testing."""

    # Build sub-chunks inside PROP
    # FS chunk
    fs_chunk = b"FS  " + struct.pack(">Q", 4) + struct.pack(">I", sample_rate)

    # CHNL chunk
    chnl_data = struct.pack(">H", channels)
    chnl_chunk = b"CHNL" + struct.pack(">Q", len(chnl_data)) + chnl_data

    # CMPR chunk
    cmpr_data = b"DSD "
    # CMPR includes a count byte after the 4-byte type in some specs,
    # but we keep it simple: just the compression type
    cmpr_chunk = b"CMPR" + struct.pack(">Q", len(cmpr_data)) + cmpr_data

    prop_body = b"SND " + fs_chunk + chnl_chunk + cmpr_chunk
    prop_chunk = b"PROP" + struct.pack(">Q", len(prop_body)) + prop_body

    # DSD audio data chunk
    audio_data = b"\x00" * audio_data_size
    dsd_chunk = b"DSD " + struct.pack(">Q", len(audio_data)) + audio_data

    # Optional metadata chunks
    meta_chunks = b""
    if artist:
        artist_bytes = artist.encode("utf-8")
        diar_body = struct.pack(">I", len(artist_bytes)) + artist_bytes
        meta_chunks += b"DIAR" + struct.pack(">Q", len(diar_body)) + diar_body
        # Pad to even boundary
        if len(diar_body) % 2 != 0:
            meta_chunks += b"\x00"

    if title:
        title_bytes = title.encode("utf-8")
        diti_body = struct.pack(">I", len(title_bytes)) + title_bytes
        meta_chunks += b"DITI" + struct.pack(">Q", len(diti_body)) + diti_body
        if len(diti_body) % 2 != 0:
            meta_chunks += b"\x00"

    # FRM8 container
    form_type = bad_form_type or b"DSD "
    frm8_body = form_type + prop_chunk + dsd_chunk + meta_chunks
    frm8_magic = bad_frm8_magic or b"FRM8"
    frm8 = frm8_magic + struct.pack(">Q", len(frm8_body)) + frm8_body

    return frm8


# ---------------------------------------------------------------------------
# DSF Tests
# ---------------------------------------------------------------------------

class TestDSFParser:
    """Tests for DSF format parsing."""

    def test_parse_basic_dsf(self, tmp_path: Path) -> None:
        """Parse a minimal valid DSF file."""
        dsf_data = _build_dsf_file(sample_rate=2822400, channels=2, sample_count=2822400)
        path = tmp_path / "test.dsf"
        path.write_bytes(dsf_data)

        info = parse_dsd_file(path)

        assert info.format == "dsf"
        assert info.sample_rate == 2822400
        assert info.channels == 2
        assert info.bits_per_sample == 1
        assert info.lossless is True

    def test_dsf_duration_one_second(self, tmp_path: Path) -> None:
        """Duration should be ~1000ms for 2822400 samples at DSD64."""
        dsf_data = _build_dsf_file(sample_rate=2822400, sample_count=2822400)
        path = tmp_path / "test.dsf"
        path.write_bytes(dsf_data)

        info = parse_dsd_file(path)
        assert info.duration_ms == 1000

    def test_dsf_duration_five_seconds(self, tmp_path: Path) -> None:
        """Duration for 5 seconds at DSD64."""
        dsf_data = _build_dsf_file(sample_rate=2822400, sample_count=2822400 * 5)
        path = tmp_path / "test.dsf"
        path.write_bytes(dsf_data)

        info = parse_dsd_file(path)
        assert info.duration_ms == 5000

    def test_dsf_dsd128_sample_rate(self, tmp_path: Path) -> None:
        """DSD128 sample rate is 5644800."""
        dsf_data = _build_dsf_file(sample_rate=5644800, sample_count=5644800 * 3)
        path = tmp_path / "test.dsf"
        path.write_bytes(dsf_data)

        info = parse_dsd_file(path)
        assert info.sample_rate == 5644800
        assert info.duration_ms == 3000

    def test_dsf_mono(self, tmp_path: Path) -> None:
        """Single-channel DSF."""
        dsf_data = _build_dsf_file(channels=1, sample_count=2822400)
        path = tmp_path / "test.dsf"
        path.write_bytes(dsf_data)

        info = parse_dsd_file(path)
        assert info.channels == 1

    def test_dsf_audio_offset_and_size(self, tmp_path: Path) -> None:
        """audio_offset and audio_size are set correctly."""
        audio = b"\xAA" * 8192
        dsf_data = _build_dsf_file(audio_data=audio)
        path = tmp_path / "test.dsf"
        path.write_bytes(dsf_data)

        info = parse_dsd_file(path)
        # audio_offset = DSD chunk (28) + fmt chunk (52) + data header (12) = 92
        assert info.audio_offset == 92
        assert info.audio_size == len(audio)

    def test_dsf_block_size(self, tmp_path: Path) -> None:
        """block_size_per_channel is stored."""
        dsf_data = _build_dsf_file(block_size_per_channel=4096)
        path = tmp_path / "test.dsf"
        path.write_bytes(dsf_data)

        info = parse_dsd_file(path)
        assert info.block_size_per_channel == 4096

    def test_dsf_bad_magic_raises(self, tmp_path: Path) -> None:
        """Non-DSF magic bytes should raise DSDParseError."""
        dsf_data = _build_dsf_file(bad_dsd_magic=b"RIFF")
        path = tmp_path / "test.dsf"
        path.write_bytes(dsf_data)

        with pytest.raises(DSDParseError, match="Unknown DSD format"):
            parse_dsd_file(path)

    def test_dsf_bad_fmt_magic_raises(self, tmp_path: Path) -> None:
        """Bad fmt chunk magic should raise DSDParseError."""
        dsf_data = _build_dsf_file(bad_fmt_magic=b"xxxx")
        path = tmp_path / "test.dsf"
        path.write_bytes(dsf_data)

        with pytest.raises(DSDParseError, match="Expected 'fmt ' chunk"):
            parse_dsd_file(path)

    def test_dsf_bad_data_magic_raises(self, tmp_path: Path) -> None:
        """Bad data chunk magic should raise DSDParseError."""
        dsf_data = _build_dsf_file(bad_data_magic=b"xxxx")
        path = tmp_path / "test.dsf"
        path.write_bytes(dsf_data)

        with pytest.raises(DSDParseError, match="Expected 'data' chunk"):
            parse_dsd_file(path)

    def test_dsf_truncated_file_raises(self, tmp_path: Path) -> None:
        """A truncated file should raise DSDParseError."""
        path = tmp_path / "test.dsf"
        path.write_bytes(b"DSD \x00\x00")

        with pytest.raises(DSDParseError, match="too short"):
            parse_dsd_file(path)

    def test_dsf_empty_file_raises(self, tmp_path: Path) -> None:
        """An empty file should raise DSDParseError."""
        path = tmp_path / "test.dsf"
        path.write_bytes(b"")

        with pytest.raises(DSDParseError, match="too short"):
            parse_dsd_file(path)

    def test_dsf_zero_sample_rate(self, tmp_path: Path) -> None:
        """Zero sample rate should not cause division by zero."""
        dsf_data = _build_dsf_file(sample_rate=0, sample_count=1000)
        path = tmp_path / "test.dsf"
        path.write_bytes(dsf_data)

        info = parse_dsd_file(path)
        assert info.sample_rate == 0
        assert info.duration_ms == 0

    def test_dsf_zero_sample_count(self, tmp_path: Path) -> None:
        """Zero sample count → zero duration."""
        dsf_data = _build_dsf_file(sample_count=0)
        path = tmp_path / "test.dsf"
        path.write_bytes(dsf_data)

        info = parse_dsd_file(path)
        assert info.duration_ms == 0

    def test_dsf_with_id3v2_tags(self, tmp_path: Path) -> None:
        """DSF with embedded ID3v2 tags should extract title/artist/album."""
        id3_block = _build_minimal_id3v2()
        dsf_data = _build_dsf_file(id3v2_block=id3_block)
        path = tmp_path / "test.dsf"
        path.write_bytes(dsf_data)

        info = parse_dsd_file(path)
        assert info.title == "Test DSD Title"
        assert info.artist == "Test DSD Artist"
        assert info.album == "Test DSD Album"

    def test_dsf_without_id3v2(self, tmp_path: Path) -> None:
        """DSF without ID3v2 → tags should be None."""
        dsf_data = _build_dsf_file()
        path = tmp_path / "test.dsf"
        path.write_bytes(dsf_data)

        info = parse_dsd_file(path)
        assert info.title is None
        assert info.artist is None
        assert info.album is None

    def test_dsf_with_corrupt_id3v2_no_crash(self, tmp_path: Path) -> None:
        """Corrupt ID3v2 block should not crash — tags just remain None."""
        corrupt_id3 = b"ID3\x03\x00\x00\x00\x00\x00\x05XXXXX"
        dsf_data = _build_dsf_file(id3v2_block=corrupt_id3)
        path = tmp_path / "test.dsf"
        path.write_bytes(dsf_data)

        # Should not raise — parser catches ID3 errors gracefully
        info = parse_dsd_file(path)
        assert info.format == "dsf"


# ---------------------------------------------------------------------------
# DFF Tests
# ---------------------------------------------------------------------------

class TestDFFParser:
    """Tests for DFF (DSDIFF) format parsing."""

    def test_parse_basic_dff(self, tmp_path: Path) -> None:
        """Parse a minimal valid DFF file."""
        dff_data = _build_dff_file(sample_rate=2822400, channels=2)
        path = tmp_path / "test.dff"
        path.write_bytes(dff_data)

        info = parse_dsd_file(path)

        assert info.format == "dff"
        assert info.sample_rate == 2822400
        assert info.channels == 2
        assert info.bits_per_sample == 1
        assert info.lossless is True

    def test_dff_duration(self, tmp_path: Path) -> None:
        """Duration calculation: (audio_size * 8) / (sample_rate * channels)."""
        # 1 second at DSD64 stereo = 2822400 * 2 / 8 = 705600 bytes
        dff_data = _build_dff_file(
            sample_rate=2822400, channels=2, audio_data_size=705600
        )
        path = tmp_path / "test.dff"
        path.write_bytes(dff_data)

        info = parse_dsd_file(path)
        assert info.duration_ms == 1000

    def test_dff_duration_three_seconds(self, tmp_path: Path) -> None:
        """3 seconds at DSD64 stereo."""
        audio_bytes = 705600 * 3
        dff_data = _build_dff_file(
            sample_rate=2822400, channels=2, audio_data_size=audio_bytes
        )
        path = tmp_path / "test.dff"
        path.write_bytes(dff_data)

        info = parse_dsd_file(path)
        assert info.duration_ms == 3000

    def test_dff_dsd128(self, tmp_path: Path) -> None:
        """DSD128 sample rate (5644800)."""
        # 1 second DSD128 stereo = 5644800 * 2 / 8 = 1411200 bytes
        dff_data = _build_dff_file(
            sample_rate=5644800, channels=2, audio_data_size=1411200
        )
        path = tmp_path / "test.dff"
        path.write_bytes(dff_data)

        info = parse_dsd_file(path)
        assert info.sample_rate == 5644800
        assert info.duration_ms == 1000

    def test_dff_mono(self, tmp_path: Path) -> None:
        """Single-channel DFF."""
        # 1 second DSD64 mono = 2822400 / 8 = 352800 bytes
        dff_data = _build_dff_file(
            sample_rate=2822400, channels=1, audio_data_size=352800
        )
        path = tmp_path / "test.dff"
        path.write_bytes(dff_data)

        info = parse_dsd_file(path)
        assert info.channels == 1
        assert info.duration_ms == 1000

    def test_dff_with_artist_tag(self, tmp_path: Path) -> None:
        """DIAR chunk should be parsed as artist."""
        dff_data = _build_dff_file(artist="Miles Davis")
        path = tmp_path / "test.dff"
        path.write_bytes(dff_data)

        info = parse_dsd_file(path)
        assert info.artist == "Miles Davis"

    def test_dff_with_title_tag(self, tmp_path: Path) -> None:
        """DITI chunk should be parsed as title."""
        dff_data = _build_dff_file(title="So What")
        path = tmp_path / "test.dff"
        path.write_bytes(dff_data)

        info = parse_dsd_file(path)
        assert info.title == "So What"

    def test_dff_with_artist_and_title(self, tmp_path: Path) -> None:
        """Both DIAR and DITI chunks present."""
        dff_data = _build_dff_file(artist="Miles Davis", title="So What")
        path = tmp_path / "test.dff"
        path.write_bytes(dff_data)

        info = parse_dsd_file(path)
        assert info.artist == "Miles Davis"
        assert info.title == "So What"

    def test_dff_without_metadata(self, tmp_path: Path) -> None:
        """No DIAR/DITI chunks → tags should be None."""
        dff_data = _build_dff_file()
        path = tmp_path / "test.dff"
        path.write_bytes(dff_data)

        info = parse_dsd_file(path)
        assert info.title is None
        assert info.artist is None

    def test_dff_bad_magic_raises(self, tmp_path: Path) -> None:
        """Non-FRM8 magic should raise DSDParseError."""
        dff_data = _build_dff_file(bad_frm8_magic=b"RIFF")
        path = tmp_path / "test.dff"
        path.write_bytes(dff_data)

        with pytest.raises(DSDParseError, match="Unknown DSD format"):
            parse_dsd_file(path)

    def test_dff_bad_form_type_raises(self, tmp_path: Path) -> None:
        """FRM8 with wrong form type should raise DSDParseError."""
        dff_data = _build_dff_file(bad_form_type=b"WAVE")
        path = tmp_path / "test.dff"
        path.write_bytes(dff_data)

        with pytest.raises(DSDParseError, match="form type"):
            parse_dsd_file(path)

    def test_dff_truncated_raises(self, tmp_path: Path) -> None:
        """Truncated DFF file should raise DSDParseError."""
        path = tmp_path / "test.dff"
        path.write_bytes(b"FRM8\x00\x00")

        with pytest.raises(DSDParseError, match="too short"):
            parse_dsd_file(path)

    def test_dff_zero_sample_rate(self, tmp_path: Path) -> None:
        """Zero sample rate should not crash."""
        dff_data = _build_dff_file(sample_rate=0)
        path = tmp_path / "test.dff"
        path.write_bytes(dff_data)

        info = parse_dsd_file(path)
        assert info.sample_rate == 0
        assert info.duration_ms == 0

    def test_dff_utf8_artist(self, tmp_path: Path) -> None:
        """UTF-8 encoded artist text in DIAR chunk."""
        dff_data = _build_dff_file(artist="Ünïcödé Àrtïst")
        path = tmp_path / "test.dff"
        path.write_bytes(dff_data)

        info = parse_dsd_file(path)
        assert info.artist == "Ünïcödé Àrtïst"


# ---------------------------------------------------------------------------
# Public API Tests
# ---------------------------------------------------------------------------

class TestPublicAPI:
    """Tests for the public API functions."""

    def test_is_dsd_file_dsf(self, tmp_path: Path) -> None:
        """is_dsd_file returns True for DSF files."""
        path = tmp_path / "test.dsf"
        path.write_bytes(_build_dsf_file())

        assert is_dsd_file(path) is True

    def test_is_dsd_file_dff(self, tmp_path: Path) -> None:
        """is_dsd_file returns True for DFF files."""
        path = tmp_path / "test.dff"
        path.write_bytes(_build_dff_file())

        assert is_dsd_file(path) is True

    def test_is_dsd_file_mp3(self, tmp_path: Path) -> None:
        """is_dsd_file returns False for non-DSD files."""
        path = tmp_path / "test.mp3"
        path.write_bytes(b"ID3\x03\x00\x00\x00\x00\x00\x00")

        assert is_dsd_file(path) is False

    def test_is_dsd_file_nonexistent(self, tmp_path: Path) -> None:
        """is_dsd_file returns False for non-existent files."""
        path = tmp_path / "nonexistent.dsf"
        assert is_dsd_file(path) is False

    def test_is_dsd_file_empty(self, tmp_path: Path) -> None:
        """is_dsd_file returns False for empty files."""
        path = tmp_path / "empty.dsf"
        path.write_bytes(b"")

        assert is_dsd_file(path) is False

    def test_parse_dsd_file_nonexistent_raises(self) -> None:
        """parse_dsd_file raises FileNotFoundError for missing files."""
        with pytest.raises(FileNotFoundError):
            parse_dsd_file(Path("/nonexistent/path/test.dsf"))

    def test_parse_dsd_file_unknown_format_raises(self, tmp_path: Path) -> None:
        """parse_dsd_file raises DSDParseError for unknown formats."""
        path = tmp_path / "test.wav"
        path.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")

        with pytest.raises(DSDParseError, match="Unknown DSD format"):
            parse_dsd_file(path)

    def test_dsd_rate_name_dsd64(self) -> None:
        assert dsd_rate_name(2822400) == "DSD64"

    def test_dsd_rate_name_dsd128(self) -> None:
        assert dsd_rate_name(5644800) == "DSD128"

    def test_dsd_rate_name_dsd256(self) -> None:
        assert dsd_rate_name(11289600) == "DSD256"

    def test_dsd_rate_name_dsd512(self) -> None:
        assert dsd_rate_name(22579200) == "DSD512"

    def test_dsd_rate_name_unknown(self) -> None:
        assert dsd_rate_name(44100) == "DSD(44100)"

    def test_parse_dsd_file_auto_detects_dsf(self, tmp_path: Path) -> None:
        """parse_dsd_file auto-detects DSF from magic bytes, regardless of extension."""
        dsf_data = _build_dsf_file()
        path = tmp_path / "audio.bin"
        path.write_bytes(dsf_data)

        info = parse_dsd_file(path)
        assert info.format == "dsf"

    def test_parse_dsd_file_auto_detects_dff(self, tmp_path: Path) -> None:
        """parse_dsd_file auto-detects DFF from magic bytes, regardless of extension."""
        dff_data = _build_dff_file()
        path = tmp_path / "audio.bin"
        path.write_bytes(dff_data)

        info = parse_dsd_file(path)
        assert info.format == "dff"


# ---------------------------------------------------------------------------
# DSDInfo dataclass tests
# ---------------------------------------------------------------------------

class TestDSDInfo:
    """Tests for the DSDInfo dataclass defaults."""

    def test_default_values(self) -> None:
        info = DSDInfo(format="dsf")
        assert info.format == "dsf"
        assert info.sample_rate == 0
        assert info.channels == 0
        assert info.bits_per_sample == 1
        assert info.duration_ms == 0
        assert info.audio_offset == 0
        assert info.audio_size == 0
        assert info.lossless is True
        assert info.title is None
        assert info.artist is None
        assert info.album is None
        assert info.album_artist is None
        assert info.track_number is None
        assert info.disc_number is None
        assert info.year is None
        assert info.genre is None
        assert info.has_artwork is False
        assert info.id3_tags == {}

    def test_dff_defaults(self) -> None:
        info = DSDInfo(format="dff")
        assert info.format == "dff"
        assert info.block_size_per_channel == 0


# ---------------------------------------------------------------------------
# Integration: DSF and DFF in same test for comparison
# ---------------------------------------------------------------------------

class TestFormatComparison:
    """Tests comparing DSF and DFF parsing results for equivalent content."""

    def test_same_sample_rate_both_formats(self, tmp_path: Path) -> None:
        """Both formats should report the same sample rate."""
        dsf_path = tmp_path / "test.dsf"
        dsf_path.write_bytes(_build_dsf_file(sample_rate=5644800))

        dff_path = tmp_path / "test.dff"
        dff_path.write_bytes(_build_dff_file(sample_rate=5644800))

        dsf_info = parse_dsd_file(dsf_path)
        dff_info = parse_dsd_file(dff_path)

        assert dsf_info.sample_rate == dff_info.sample_rate == 5644800

    def test_same_channels_both_formats(self, tmp_path: Path) -> None:
        """Both formats should report the same channel count."""
        dsf_path = tmp_path / "test.dsf"
        dsf_path.write_bytes(_build_dsf_file(channels=2))

        dff_path = tmp_path / "test.dff"
        dff_path.write_bytes(_build_dff_file(channels=2))

        dsf_info = parse_dsd_file(dsf_path)
        dff_info = parse_dsd_file(dff_path)

        assert dsf_info.channels == dff_info.channels == 2

    def test_both_formats_are_lossless(self, tmp_path: Path) -> None:
        """Both formats should be marked as lossless."""
        dsf_path = tmp_path / "test.dsf"
        dsf_path.write_bytes(_build_dsf_file())

        dff_path = tmp_path / "test.dff"
        dff_path.write_bytes(_build_dff_file())

        dsf_info = parse_dsd_file(dsf_path)
        dff_info = parse_dsd_file(dff_path)

        assert dsf_info.lossless is True
        assert dff_info.lossless is True

    def test_format_field_distinguishes(self, tmp_path: Path) -> None:
        """The format field should correctly distinguish DSF from DFF."""
        dsf_path = tmp_path / "test.dsf"
        dsf_path.write_bytes(_build_dsf_file())

        dff_path = tmp_path / "test.dff"
        dff_path.write_bytes(_build_dff_file())

        dsf_info = parse_dsd_file(dsf_path)
        dff_info = parse_dsd_file(dff_path)

        assert dsf_info.format == "dsf"
        assert dff_info.format == "dff"
