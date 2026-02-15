"""
Binary header parser for DSD audio formats (DSF and DFF/DSDIFF).

mutagen does not support DSD formats, so we need a custom parser to extract
metadata from DSF and DFF files during library scanning.

References:
- DSF format: https://dsd-guide.com/sites/default/files/white-papers/DSFFileFormatSpec_E.pdf
- DSDIFF (DFF): https://dsd-guide.com/sites/default/files/white-papers/DSDIFF_1.5_Spec.pdf
- LMS modules: Slim::Formats::DSF, Slim::Formats::DFF, Slim::Formats::DSD

LMS-First: Structure and field semantics verified against LMS's Audio::Scan
output and Slim::Formats::DSD.pm tag mapping.
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

logger = logging.getLogger(__name__)


@dataclass
class DSDInfo:
    """Parsed DSD file information.

    Fields mirror what LMS extracts in Slim::Formats::DSD::getTag():
    - sample_rate, channels, duration_ms, bits_per_sample (always 1 for DSD)
    - audio_offset, audio_size (for streaming / seeking)
    - Tags: title, artist (and any ID3v2 tags for DSF)
    """

    format: str  # "dsf" or "dff"
    sample_rate: int = 0  # e.g. 2822400 (DSD64), 5644800 (DSD128)
    channels: int = 0
    bits_per_sample: int = 1  # Always 1 for DSD
    duration_ms: int = 0
    audio_offset: int = 0  # Byte offset to start of audio data
    audio_size: int = 0  # Size of audio data in bytes
    block_size_per_channel: int = 0  # DSF only: block interleave size
    lossless: bool = True

    # Metadata (tags)
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    album_artist: str | None = None
    track_number: int | None = None
    disc_number: int | None = None
    year: int | None = None
    genre: str | None = None
    has_artwork: bool = False

    # Raw ID3v2 tags dict (DSF only; DFF has limited DIAR/DITI tags)
    id3_tags: dict[str, object] = field(default_factory=dict)


class DSDParseError(Exception):
    """Raised when a DSD file cannot be parsed."""


# ---------------------------------------------------------------------------
# DSF Parser
# ---------------------------------------------------------------------------

def _parse_dsf(fh: BinaryIO) -> DSDInfo:
    """
    Parse a DSF (DSD Stream File) header.

    DSF layout (all multi-byte values are little-endian):

    DSD Chunk (28 bytes):
      bytes  0-3:   "DSD " magic
      bytes  4-11:  chunk_size (uint64 LE) = 28
      bytes 12-19:  total_file_size (uint64 LE)
      bytes 20-27:  metadata_offset (uint64 LE) → ID3v2 tag position (0 = none)

    fmt Chunk (52 bytes):
      bytes  0-3:   "fmt " magic
      bytes  4-11:  chunk_size (uint64 LE) = 52
      bytes 12-15:  format_version (uint32 LE) = 1
      bytes 16-19:  format_id (uint32 LE) = 0 (DSD raw)
      bytes 20-23:  channel_type (uint32 LE)
      bytes 24-27:  channel_num (uint32 LE)
      bytes 28-31:  sample_rate (uint32 LE)
      bytes 32-35:  bits_per_sample (uint32 LE) = 1
      bytes 36-43:  sample_count (uint64 LE)
      bytes 44-47:  block_size_per_channel (uint32 LE)
      bytes 48-51:  reserved (uint32 LE) = 0

    data Chunk:
      bytes  0-3:   "data" magic
      bytes  4-11:  chunk_size (uint64 LE) includes 12-byte header
      bytes 12+:    audio data

    [Optional ID3v2 tag block at metadata_offset]
    """
    info = DSDInfo(format="dsf")

    # --- DSD Chunk (28 bytes) ---
    dsd_chunk = fh.read(28)
    if len(dsd_chunk) < 28:
        raise DSDParseError("File too short for DSF DSD chunk")

    magic = dsd_chunk[0:4]
    if magic != b"DSD ":
        raise DSDParseError(f"Not a DSF file: bad magic {magic!r}")

    chunk_size, total_file_size, metadata_offset = struct.unpack_from("<QQQ", dsd_chunk, 4)

    if chunk_size != 28:
        logger.warning("DSF DSD chunk size is %d, expected 28", chunk_size)

    # --- fmt Chunk (52 bytes) ---
    fmt_chunk = fh.read(52)
    if len(fmt_chunk) < 52:
        raise DSDParseError("File too short for DSF fmt chunk")

    fmt_magic = fmt_chunk[0:4]
    if fmt_magic != b"fmt ":
        raise DSDParseError(f"Expected 'fmt ' chunk, got {fmt_magic!r}")

    (
        fmt_chunk_size,
        format_version,
        format_id,
        channel_type,
        channel_num,
        sample_rate,
        bits_per_sample,
        sample_count,
        block_size_per_channel,
        _reserved,
    ) = struct.unpack_from("<QIIIIIIQII", fmt_chunk, 4)

    info.sample_rate = sample_rate
    info.channels = channel_num
    info.bits_per_sample = bits_per_sample if bits_per_sample > 0 else 1
    info.block_size_per_channel = block_size_per_channel

    # Duration: sample_count / sample_rate
    if sample_rate > 0 and sample_count > 0:
        info.duration_ms = int(sample_count / sample_rate * 1000)

    # Skip past fmt chunk if it's larger than 52 bytes
    if fmt_chunk_size > 52:
        fh.read(fmt_chunk_size - 52)

    # --- data Chunk header (12 bytes minimum) ---
    data_header = fh.read(12)
    if len(data_header) < 12:
        raise DSDParseError("File too short for DSF data chunk header")

    data_magic = data_header[0:4]
    if data_magic != b"data":
        raise DSDParseError(f"Expected 'data' chunk, got {data_magic!r}")

    data_chunk_size = struct.unpack_from("<Q", data_header, 4)[0]

    # audio_offset = position right after data chunk header (12 bytes into data chunk)
    # Total offset from file start = DSD chunk (28) + fmt chunk + data header (12)
    info.audio_offset = 28 + fmt_chunk_size + 12
    # audio_size = data chunk size minus the 12-byte data chunk header
    info.audio_size = max(0, data_chunk_size - 12)

    # --- ID3v2 tags (optional, at end of file) ---
    if metadata_offset > 0:
        try:
            _parse_dsf_id3v2(fh, metadata_offset, info)
        except Exception as e:
            logger.debug("Failed to parse DSF ID3v2 tags: %s", e)

    return info


def _parse_dsf_id3v2(fh: BinaryIO, offset: int, info: DSDInfo) -> None:
    """
    Parse ID3v2 tags embedded at the end of a DSF file.

    DSF files store an ID3v2 tag block at the byte offset indicated by the
    metadata_offset field in the DSD chunk. We use mutagen's ID3 parser
    for the heavy lifting.

    LMS reference: Slim::Formats::DSD::getTag() calls Slim::Formats::MP3::doTagMapping()
    when id3_version is present.
    """
    try:
        from mutagen.id3 import ID3
    except ImportError:
        logger.debug("mutagen not available for ID3v2 parsing")
        return

    fh.seek(offset)
    header = fh.read(3)
    if header != b"ID3":
        return

    # Reset and let mutagen parse from the offset
    fh.seek(offset)

    try:
        # mutagen's ID3 can parse from a file-like object if we give it the
        # right position. We read the ID3 block and parse it.
        id3_data = fh.read()  # Read from offset to end of file
        if not id3_data:
            return

        import io
        id3_stream = io.BytesIO(id3_data)

        # Use mutagen's internal ID3 header parsing
        id3 = ID3()
        id3.load(id3_stream, translate=True)

        # Store raw tags for scanner compatibility
        info.id3_tags = dict(id3)

        # Extract common fields (matching LMS's doTagMapping)
        # TIT2 = Title
        tit2 = id3.get("TIT2")
        if tit2:
            info.title = str(tit2)

        # TPE1 = Artist
        tpe1 = id3.get("TPE1")
        if tpe1:
            info.artist = str(tpe1)

        # TALB = Album
        talb = id3.get("TALB")
        if talb:
            info.album = str(talb)

        # TPE2 = Album Artist
        tpe2 = id3.get("TPE2")
        if tpe2:
            info.album_artist = str(tpe2)

        # TRCK = Track Number
        trck = id3.get("TRCK")
        if trck:
            text = str(trck)
            try:
                info.track_number = int(text.split("/")[0])
            except (ValueError, IndexError):
                pass

        # TPOS = Disc Number
        tpos = id3.get("TPOS")
        if tpos:
            text = str(tpos)
            try:
                info.disc_number = int(text.split("/")[0])
            except (ValueError, IndexError):
                pass

        # TDRC / TYER = Year
        for year_key in ("TDRC", "TYER"):
            frame = id3.get(year_key)
            if frame:
                text = str(frame)
                try:
                    info.year = int(text[:4])
                    break
                except (ValueError, IndexError):
                    pass

        # TCON = Genre
        tcon = id3.get("TCON")
        if tcon:
            info.genre = str(tcon)

        # APIC = Artwork
        info.has_artwork = len(id3.getall("APIC")) > 0

    except Exception as e:
        logger.debug("Error parsing ID3v2 in DSF: %s", e)


# ---------------------------------------------------------------------------
# DFF (DSDIFF) Parser
# ---------------------------------------------------------------------------

def _parse_dff(fh: BinaryIO) -> DSDInfo:
    """
    Parse a DFF (DSDIFF) file header.

    DFF layout (all multi-byte values are big-endian):

    FRM8 container:
      bytes 0-3:    "FRM8" magic
      bytes 4-11:   chunk_size (uint64 BE) — size of everything after this field
      bytes 12-15:  form_type "DSD " (4 bytes)

    Inside FRM8, sequential chunks:

    PROP chunk (properties):
      "PROP" + size(8B) + "SND " + sub-chunks:
        FS:   "FS  " + size(8B) + sample_rate(4B BE)
        CHNL: "CHNL" + size(8B) + num_channels(2B BE) + channel IDs...
        CMPR: "CMPR" + size(8B) + compression_type(4B) "DSD " or "DST "

    DSD chunk (audio data):
      "DSD " + size(8B) + audio data

    Optional metadata chunks:
      DIAR: "DIAR" + size(8B) + count(4B BE) + artist text
      DITI: "DITI" + size(8B) + count(4B BE) + title text

    LMS reference: Slim::Formats::DFF inherits from Slim::Formats::DSD.
    """
    info = DSDInfo(format="dff")

    # --- FRM8 header (16 bytes) ---
    frm8_header = fh.read(16)
    if len(frm8_header) < 16:
        raise DSDParseError("File too short for DFF FRM8 header")

    magic = frm8_header[0:4]
    if magic != b"FRM8":
        raise DSDParseError(f"Not a DFF file: bad magic {magic!r}")

    frm8_size = struct.unpack_from(">Q", frm8_header, 4)[0]
    form_type = frm8_header[12:16]
    if form_type != b"DSD ":
        raise DSDParseError(f"FRM8 form type is {form_type!r}, expected 'DSD '")

    # Total data to parse inside FRM8 (after the 4-byte form_type)
    remaining = frm8_size - 4  # subtract the "DSD " form type already read
    pos = 16  # current file position

    while remaining >= 12:
        chunk_header = fh.read(12)
        if len(chunk_header) < 12:
            break

        chunk_id = chunk_header[0:4]
        chunk_size = struct.unpack_from(">Q", chunk_header, 4)[0]
        remaining -= 12
        pos += 12

        # Track whether the handler already consumed the chunk data.
        # Handlers that read data themselves set this to True so the
        # generic skip at the bottom is suppressed.
        consumed = False

        if chunk_id == b"PROP":
            _parse_dff_prop(fh, chunk_size, info)
            consumed = True
        elif chunk_id == b"DSD ":
            # Audio data chunk — record offset/size but don't read data
            info.audio_offset = pos
            info.audio_size = chunk_size
        elif chunk_id == b"DST ":
            # DST compressed audio frame chunk
            info.audio_offset = pos
            info.audio_size = chunk_size
        elif chunk_id == b"DIAR":
            # Artist metadata
            _parse_dff_text_chunk(fh, chunk_size, info, "artist")
            consumed = True
        elif chunk_id == b"DITI":
            # Title metadata
            _parse_dff_text_chunk(fh, chunk_size, info, "title")
            consumed = True

        # Skip chunk data (with even-byte alignment).
        # If a handler already consumed the data, only handle the pad byte.
        skip = chunk_size
        if skip % 2 != 0:
            skip += 1  # DFF chunks are padded to even boundaries

        if consumed:
            # Handler already read chunk_size bytes; only skip the pad byte
            pad = skip - chunk_size
            if pad > 0:
                fh.read(pad)
        else:
            if skip > 0:
                fh.seek(skip, 1)  # seek relative

        remaining -= skip
        pos += skip

    # Calculate duration
    # DFF duration = (audio_size * 8) / (sample_rate * channels)
    if info.sample_rate > 0 and info.channels > 0 and info.audio_size > 0:
        duration_seconds = (info.audio_size * 8) / (info.sample_rate * info.channels)
        info.duration_ms = int(duration_seconds * 1000)

    return info


def _parse_dff_prop(fh: BinaryIO, prop_size: int, info: DSDInfo) -> None:
    """
    Parse the PROP chunk and its sub-chunks (FS, CHNL, CMPR).

    The PROP chunk starts with "SND " (4 bytes) followed by sub-chunks.
    """
    if prop_size < 4:
        return

    prop_type = fh.read(4)
    if prop_type != b"SND ":
        logger.warning("PROP type is %r, expected 'SND '", prop_type)
        # Skip remaining
        if prop_size > 4:
            fh.seek(prop_size - 4, 1)
        return

    consumed = 4

    while consumed + 12 <= prop_size:
        sub_header = fh.read(12)
        if len(sub_header) < 12:
            break

        sub_id = sub_header[0:4]
        sub_size = struct.unpack_from(">Q", sub_header, 4)[0]
        consumed += 12

        if sub_id == b"FS  " and sub_size >= 4:
            sr_data = fh.read(4)
            if len(sr_data) == 4:
                info.sample_rate = struct.unpack(">I", sr_data)[0]
            # Skip any remaining data in this sub-chunk
            if sub_size > 4:
                fh.seek(sub_size - 4, 1)
            consumed += sub_size

        elif sub_id == b"CHNL" and sub_size >= 2:
            ch_data = fh.read(2)
            if len(ch_data) == 2:
                info.channels = struct.unpack(">H", ch_data)[0]
            # Skip channel descriptors
            if sub_size > 2:
                fh.seek(sub_size - 2, 1)
            consumed += sub_size

        elif sub_id == b"CMPR" and sub_size >= 4:
            cmpr_data = fh.read(4)
            # Compression type: "DSD " = uncompressed, "DST " = DST compressed
            if len(cmpr_data) == 4:
                compression = cmpr_data
                if compression not in (b"DSD ", b"DST "):
                    logger.warning("Unknown DFF compression: %r", compression)
            if sub_size > 4:
                fh.seek(sub_size - 4, 1)
            consumed += sub_size

        else:
            # Skip unknown sub-chunk
            if sub_size > 0:
                fh.seek(sub_size, 1)
            consumed += sub_size

    # Skip any remaining PROP data
    leftover = prop_size - consumed
    if leftover > 0:
        fh.seek(leftover, 1)


def _parse_dff_text_chunk(
    fh: BinaryIO,
    chunk_size: int,
    info: DSDInfo,
    field_name: str,
) -> None:
    """
    Parse a DIAR (artist) or DITI (title) text chunk.

    Layout:
      count (4 bytes BE) — number of text bytes
      text  (count bytes) — text content

    LMS reference: Slim::Formats::DSD::getTag() reads tag_diar_artist and
    tag_diti_title from Audio::Scan output.
    """
    if chunk_size < 4:
        fh.seek(chunk_size, 1)
        return

    count_data = fh.read(4)
    if len(count_data) < 4:
        return

    text_count = struct.unpack(">I", count_data)[0]
    read_remaining = chunk_size - 4

    if text_count <= 0 or text_count > read_remaining:
        if read_remaining > 0:
            fh.seek(read_remaining, 1)
        return

    text_data = fh.read(text_count)
    skip = read_remaining - text_count
    if skip > 0:
        fh.seek(skip, 1)

    if text_data:
        # Try UTF-8 first, fall back to latin-1
        try:
            text = text_data.decode("utf-8").strip()
        except UnicodeDecodeError:
            text = text_data.decode("latin-1").strip()

        if text:
            setattr(info, field_name, text)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_dsd_file(path: Path | str) -> DSDInfo:
    """
    Parse a DSD audio file (DSF or DFF) and return metadata.

    This is the main entry point for the scanner. It detects the format
    from the file's magic bytes and delegates to the appropriate parser.

    Args:
        path: Path to the DSD audio file.

    Returns:
        DSDInfo with extracted metadata.

    Raises:
        DSDParseError: If the file is not a valid DSF or DFF file.
        FileNotFoundError: If the file does not exist.
        OSError: If the file cannot be read.
    """
    path = Path(path)

    with open(path, "rb") as fh:
        magic = fh.read(4)
        if len(magic) < 4:
            raise DSDParseError(f"File too short: {path}")

        fh.seek(0)

        if magic == b"DSD ":
            return _parse_dsf(fh)
        elif magic == b"FRM8":
            return _parse_dff(fh)
        else:
            raise DSDParseError(
                f"Unknown DSD format (magic={magic!r}): {path}"
            )


def is_dsd_file(path: Path | str) -> bool:
    """
    Check if a file is a DSD audio file (DSF or DFF) by reading the magic bytes.

    This is a fast check that only reads the first 4 bytes.
    """
    try:
        with open(path, "rb") as fh:
            magic = fh.read(4)
            return magic in (b"DSD ", b"FRM8")
    except OSError:
        return False


# DSD sample rate constants (for reference / display)
DSD_RATES: dict[int, str] = {
    2822400: "DSD64",
    5644800: "DSD128",
    11289600: "DSD256",
    22579200: "DSD512",
}


def dsd_rate_name(sample_rate: int) -> str:
    """Return a human-readable DSD rate name (e.g. 'DSD64', 'DSD128')."""
    return DSD_RATES.get(sample_rate, f"DSD({sample_rate})")
