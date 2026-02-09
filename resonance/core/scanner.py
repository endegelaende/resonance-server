from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mutagen import File as mutagen_file
from mutagen.flac import FLAC
from mutagen.id3 import ID3
from mutagen.mp4 import MP4

logger = logging.getLogger(__name__)


# Keep this conservative & format-focused for MVP.
# Add more as you validate formats end-to-end with Squeezelite/streaming.
DEFAULT_AUDIO_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".mp3",
        ".flac",
        ".ogg",
        ".opus",
        ".m4a",
        ".m4b",
        ".aac",
        ".wav",
        ".aiff",
        ".aif",
        ".wma",
        ".wv",
        ".ape",
        ".mpc",
    }
)


@dataclass(frozen=True, slots=True)
class ScanConfig:
    """
    Configuration for scanning a music folder.

    We intentionally keep this "modern & simple" (no LMS-style role/relational explosion).
    """

    root: Path
    extensions: frozenset[str] = DEFAULT_AUDIO_EXTENSIONS
    follow_symlinks: bool = False
    max_concurrency: int = 8


@dataclass(frozen=True, slots=True)
class TrackMetadata:
    """
    Normalized metadata extracted from an audio file.

    This is an MVP structure optimized for:
    - browsing (artist/album/title)
    - playback (path/duration)
    - stable identity (path)

    Phase 3 (Contributors/Roles):
    - Contributors are extracted as (role, name) tuples (LMS-like contributor_tracks),
      but kept lightweight at the scanner level.
    """

    path: Path
    title: str
    artist: str | None
    album: str | None
    album_artist: str | None
    genres: tuple[str, ...] = ()
    contributors: tuple[tuple[str, str], ...] = ()
    compilation: bool = False
    track_number: int | None = None
    disc_number: int | None = None
    year: int | None = None
    duration_ms: int | None = None
    has_artwork: bool = False
    # Audio quality metadata
    sample_rate: int | None = None
    bit_depth: int | None = None
    bitrate: int | None = None
    channels: int | None = None


@dataclass(frozen=True, slots=True)
class ScanIssue:
    path: Path
    message: str


@dataclass(frozen=True, slots=True)
class ScanResult:
    tracks: list[TrackMetadata]
    issues: list[ScanIssue]


def _clean_str(value: str | None) -> str | None:
    if value is None:
        return None
    s = value.strip()
    return s if s else None


def _as_text_list(value: Any) -> list[str]:
    """
    Normalize mutagen tag values into a UTF-8-ish list of strings.

    Shapes seen in the wild:
    - list[str]
    - str
    - ID3 frames / objects with `.text`
    - bytes
    """
    if value is None:
        return []
    if isinstance(value, list | tuple):
        out: list[str] = []
        for v in value:
            out.extend(_as_text_list(v))
        return out
    if isinstance(value, bytes):
        try:
            return [value.decode("utf-8", errors="replace")]
        except Exception:
            return [str(value)]
    if isinstance(value, str):
        return [value]
    text = getattr(value, "text", None)
    if text is not None:
        return _as_text_list(text)
    return [str(value)]


def _parse_people_tag(value: Any) -> tuple[str, ...]:
    """
    Parse a tag that potentially contains multiple people.

    We split on common separators. We intentionally do NOT split on '&'
    (many tags use '&' inside a name, and Phase 2 already learned that lesson for genres).
    """
    raw = _as_text_list(value)
    if not raw:
        return ()
    out: list[str] = []
    for item in raw:
        s = (item or "").strip()
        if not s:
            continue
        # Common separators from taggers: ';' '/' ','
        parts = [p.strip() for p in s.replace("/", ";").replace(",", ";").split(";")]
        for p in parts:
            if p:
                out.append(p)
    # Preserve order, de-dupe
    seen: set[str] = set()
    uniq: list[str] = []
    for n in out:
        k = n.casefold()
        if k in seen:
            continue
        seen.add(k)
        uniq.append(n)
    return tuple(uniq)


def _first_text(value: Any) -> str | None:
    """
    Mutagen returns different shapes depending on container/tag type:
    - ID3 frames
    - lists of strings
    - plain strings
    - objects with `.text`
    We normalize to a single string (first item if multiple).
    """
    if value is None:
        return None

    # Common case: list/tuple of values
    if isinstance(value, (list, tuple)):
        if not value:
            return None
        return _first_text(value[0])

    # Mutagen ID3 frames often have `.text` list
    text = getattr(value, "text", None)
    if text is not None:
        return _first_text(text)

    # Some frames provide `.value`
    val = getattr(value, "value", None)
    if val is not None:
        return _first_text(val)

    # Fall back to string conversion
    try:
        s = str(value)
    except Exception:
        return None

    return _clean_str(s)


def _parse_int_maybe(value: Any) -> int | None:
    """
    Parse things like:
    - "3"
    - "3/12"
    - ["3/12"]
    - mutagen frame objects
    """
    s = _first_text(value)
    if not s:
        return None

    # handle "3/12"
    if "/" in s:
        s = s.split("/", 1)[0].strip()

    try:
        return int(s)
    except ValueError:
        return None


def _parse_genres(value: Any) -> tuple[str, ...]:
    """
    Parse genre tag value and split into multiple genres.

    Common separators in genre tags: ; / , (but not & which is often intentional like "Drum & Bass")
    Examples:
      "Rock; Pop" -> ("Rock", "Pop")
      "Electronic/Dance" -> ("Electronic", "Dance")
      "Jazz, Blues" -> ("Jazz", "Blues")
      "Drum & Bass" -> ("Drum & Bass",)  # kept as single genre
    """
    s = _first_text(value)
    if not s:
        return ()

    # Split on common separators (semicolon, slash, comma) but not ampersand
    import re

    # Split on ; or / or , (with optional whitespace around)
    parts = re.split(r"\s*[;/,]\s*", s)

    # Clean and deduplicate while preserving order
    seen: set[str] = set()
    result: list[str] = []
    for part in parts:
        cleaned = part.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)

    return tuple(result)


def _parse_year_maybe(value: Any) -> int | None:
    """
    Accept "1999" or "1999-01-01" or "1999/.." formats.
    """
    s = _first_text(value)
    if not s:
        return None

    # Extract first 4-digit year if present
    for i in range(0, max(0, len(s) - 3)):
        chunk = s[i : i + 4]
        if chunk.isdigit():
            year = int(chunk)
            # sanity range
            if 1000 <= year <= 3000:
                return year
    return None


def _parse_compilation_flag(value: Any) -> bool:
    """
    Parse common "compilation" flags across tag formats.

    - ID3: TCMP is commonly "1" (sometimes "0")
    - Vorbis/FLAC: "compilation" may be "1", "true", "yes"
    - MP4: "cpil" is commonly 1/0 (mutagen may surface it as int-like)
    """
    s = _first_text(value)
    if not s:
        return False

    v = s.strip().lower()
    if not v:
        return False

    # Common truthy values
    if v in {"1", "true", "yes", "y", "on"}:
        return True
    if v in {"0", "false", "no", "n", "off"}:
        return False

    # Fallback: try int parsing
    try:
        return int(v) != 0
    except ValueError:
        return False


def _tags_get(tags: dict[str, Any] | None, keys: Iterable[str]) -> Any:
    if not tags:
        return None
    for k in keys:
        if k in tags:
            return tags.get(k)
    return None


def _extract_metadata(path: Path) -> TrackMetadata:
    """
    Extract metadata using mutagen.

    Important: This function is intentionally synchronous; scanning can run it in a thread
    to keep the asyncio event loop responsive.
    """
    audio = mutagen_file(path)
    if audio is None:
        raise ValueError("unsupported or unreadable audio file")

    tags: dict[str, Any] | None = None
    if hasattr(audio, "tags") and audio.tags is not None:
        # mutagen tags often behave like dict
        try:
            tags = dict(audio.tags)
        except Exception:
            # Some tag containers may not be directly castable
            tags = audio.tags  # type: ignore[assignment]

    # Duration and audio quality info
    duration_ms: int | None = None
    sample_rate: int | None = None
    bit_depth: int | None = None
    bitrate: int | None = None
    channels: int | None = None

    info = getattr(audio, "info", None)
    if info is not None:
        # Duration
        length = getattr(info, "length", None)
        if isinstance(length, (int, float)) and length > 0:
            duration_ms = int(length * 1000)

        # Sample rate (Hz)
        sr = getattr(info, "sample_rate", None)
        if isinstance(sr, int) and sr > 0:
            sample_rate = sr

        # Bit depth (bits per sample) - available for lossless formats
        bps = getattr(info, "bits_per_sample", None)
        if isinstance(bps, int) and bps > 0:
            bit_depth = bps

        # Bitrate (bps) - useful for lossy formats
        br = getattr(info, "bitrate", None)
        if isinstance(br, int) and br > 0:
            bitrate = br

        # Channels
        ch = getattr(info, "channels", None)
        if isinstance(ch, int) and ch > 0:
            channels = ch

    # Title
    # Keys: ID3=TIT2, Vorbis=title, MP4=©nam
    title = _first_text(_tags_get(tags, ("TIT2", "title", "TITLE", "©nam"))) or path.stem

    # Artist
    # Keys: ID3=TPE1, Vorbis=artist, MP4=©ART
    artist = _first_text(_tags_get(tags, ("TPE1", "artist", "ARTIST", "©ART")))

    # Album
    # Keys: ID3=TALB, Vorbis=album, MP4=©alb
    album = _first_text(_tags_get(tags, ("TALB", "album", "ALBUM", "©alb")))

    # Album artist (optional but useful; keep separate to avoid LMS role matrix)
    # Keys: ID3=TPE2, Vorbis=albumartist, MP4=aART
    album_artist = _first_text(
        _tags_get(tags, ("TPE2", "albumartist", "ALBUMARTIST", "aART", "ALBUM ARTIST"))
    )

    # Contributors / Roles (LMS-like contributor_tracks, Phase 3)
    #
    # Common tags:
    # - Composer:   ID3=TCOM, Vorbis/FLAC=composer
    # - Conductor: ID3=TPE3, Vorbis/FLAC=conductor
    # - Band:      ID3=TPE2 (often "Band/Orchestra"), Vorbis/FLAC=band/orchestra
    #
    # Note: We also include artist/album_artist as contributors for convenience and to match
    # LMS-ish browsing semantics later (role filters can include these roles too).
    contributors_pairs: list[tuple[str, str]] = []

    for name in _parse_people_tag(_tags_get(tags, ("TCOM", "composer", "COMPOSER"))):
        contributors_pairs.append(("composer", name))

    for name in _parse_people_tag(_tags_get(tags, ("TPE3", "conductor", "CONDUCTOR"))):
        contributors_pairs.append(("conductor", name))

    # Band/Orchestra: prefer explicit Vorbis-ish tags if present, fall back to TPE2 only if
    # it doesn't just duplicate album_artist.
    for name in _parse_people_tag(_tags_get(tags, ("band", "BAND", "orchestra", "ORCHESTRA"))):
        contributors_pairs.append(("band", name))

    if album_artist:
        # Many files set TPE2=Album Artist; treat it as role "albumartist".
        contributors_pairs.append(("albumartist", album_artist))

    if artist:
        contributors_pairs.append(("artist", artist))

    # Dedupe (role, name), preserve order
    seen_pairs: set[tuple[str, str]] = set()
    contributors: list[tuple[str, str]] = []
    for role, name in contributors_pairs:
        r = (role or "").strip().lower()
        n = (name or "").strip()
        if not r or not n:
            continue
        key = (r, n)
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        contributors.append(key)

    # Genres (split multi-value genre tags)
    # Keys:
    # - ID3: TCON
    # - Vorbis/FLAC: genre
    # - MP4: ©gen (not always present, but used by some taggers)
    genres = _parse_genres(_tags_get(tags, ("TCON", "genre", "GENRE", "©gen")))

    # Numbers
    # Keys: ID3=TRCK, Vorbis=tracknumber, MP4=trkn
    track_number = _parse_int_maybe(_tags_get(tags, ("TRCK", "tracknumber", "TRACKNUMBER", "trkn")))
    # Keys: ID3=TPOS, Vorbis=discnumber, MP4=disk
    disc_number = _parse_int_maybe(_tags_get(tags, ("TPOS", "discnumber", "DISCNUMBER", "disk")))

    # Year/Date
    # Keys: ID3=TDRC/TYER, Vorbis=date, MP4=©day
    year = _parse_year_maybe(_tags_get(tags, ("TDRC", "TYER", "date", "DATE", "YEAR", "©day")))

    # Compilation flag (LMS/iTunes-ish)
    # Keys:
    # - ID3: TCMP
    # - Vorbis/FLAC: compilation
    # - MP4: cpil
    compilation = _parse_compilation_flag(
        _tags_get(tags, ("TCMP", "compilation", "COMPILATION", "cpil"))
    )

    # Artwork detection
    has_artwork = False
    if isinstance(audio, MP4):
        has_artwork = bool(tags and "covr" in tags)
    elif isinstance(audio, FLAC):
        has_artwork = len(audio.pictures) > 0
    elif isinstance(audio, ID3):
        has_artwork = len(audio.getall("APIC")) > 0
    elif hasattr(audio, "tags") and isinstance(audio.tags, ID3):
        has_artwork = len(audio.tags.getall("APIC")) > 0
    elif tags and ("metadata_block_picture" in tags or "METADATA_BLOCK_PICTURE" in tags):
        has_artwork = True

    return TrackMetadata(
        path=path,
        title=title,
        artist=_clean_str(artist),
        album=_clean_str(album),
        album_artist=_clean_str(album_artist),
        genres=genres,
        contributors=tuple(contributors),
        compilation=compilation,
        track_number=track_number,
        disc_number=disc_number,
        year=year,
        duration_ms=duration_ms,
        has_artwork=has_artwork,
        sample_rate=sample_rate,
        bit_depth=bit_depth,
        bitrate=bitrate,
        channels=channels,
    )


async def iter_audio_files(config: ScanConfig) -> AsyncIterator[Path]:
    """
    Asynchronously yields audio file paths under `config.root`.

    Implementation notes:
    - We collect file paths in a thread to avoid blocking the event loop on large trees.
    - We keep it simple and predictable: extension-based filtering only.
    """
    root = config.root
    if not root.exists():
        raise FileNotFoundError(root)
    if not root.is_dir():
        raise NotADirectoryError(root)

    def _walk() -> list[Path]:
        # Using rglob keeps it readable; performance is fine for MVP.
        # If needed, we can optimize with os.scandir later.
        paths: list[Path] = []
        for p in root.rglob("*"):
            try:
                if not config.follow_symlinks and p.is_symlink():
                    continue
                if not p.is_file():
                    continue
                if p.suffix.lower() not in config.extensions:
                    continue
                paths.append(p)
            except OSError:
                # Ignore broken permissions/paths during walk; report at decode stage if needed.
                continue
        return paths

    paths = await asyncio.to_thread(_walk)
    for p in paths:
        yield p


async def scan_music_folder(config: ScanConfig) -> ScanResult:
    """
    Scan a folder for audio files and extract metadata.

    This returns a pure in-memory result. Persisting to a DB is a separate responsibility
    (keeps layers clean, testable, and avoids LMS-style tangles).

    Concurrency:
    - filesystem walk: runs in a thread
    - metadata extraction: bounded concurrency using threads via asyncio.to_thread
    """
    semaphore = asyncio.Semaphore(max(1, config.max_concurrency))

    tracks: list[TrackMetadata] = []
    issues: list[ScanIssue] = []

    async def _process(path: Path) -> None:
        async with semaphore:
            try:
                meta = await asyncio.to_thread(_extract_metadata, path)
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"
                issues.append(ScanIssue(path=path, message=msg))
                logger.debug("Scan issue for %s: %s", path, msg)
                return
            tracks.append(meta)

    tasks: list[asyncio.Task[None]] = []
    async for path in iter_audio_files(config):
        tasks.append(asyncio.create_task(_process(path)))

    if tasks:
        # gather will preserve exceptions—handled inside _process, so this shouldn't raise
        await asyncio.gather(*tasks)

    # Deterministic ordering is useful for tests and predictable UI.
    tracks.sort(key=lambda t: str(t.path).lower())

    return ScanResult(tracks=tracks, issues=issues)
