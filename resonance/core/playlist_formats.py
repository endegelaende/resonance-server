"""
Playlist file format parsers and writers.

Supports:
- M3U / M3U8 (Extended M3U with ``#EXTINF`` metadata)
- PLS (INI-style playlist format)

Reference: ``Slim/Formats/Playlists/M3U.pm``, ``Slim/Formats/Playlists/PLS.pm``

Usage::

    from resonance.core.playlist_formats import parse_m3u, write_m3u, parse_pls

    # Parse
    entries = parse_m3u(Path("playlist.m3u"))

    # Write
    write_m3u(Path("output.m3u"), tracks, current_index=3)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlaylistFileEntry:
    """A single entry parsed from a playlist file.

    Attributes:
        path: Resolved absolute path or URL string.
        title: Display title (from ``#EXTINF`` or ``TitleN``), may be empty.
        artist: Artist name (from extended ``#EXTINF`` format), may be empty.
        album: Album name (from extended ``#EXTINF`` format), may be empty.
        duration_seconds: Duration in seconds (``-1`` means unknown).
    """

    path: str
    title: str = ""
    artist: str = ""
    album: str = ""
    duration_seconds: int = -1


# ---------------------------------------------------------------------------
# M3U / M3U8
# ---------------------------------------------------------------------------

# LMS extended EXTINF:  #EXTINF:secs,<artist> - <album> - <title>
_EXTINF_EXTENDED_RE = re.compile(
    r"^#EXTINF:\s*(-?\d+)\s*,\s*<(.+?)>\s*-\s*<(.+?)>\s*-\s*<(.+?)>\s*$"
)

# Standard EXTINF:  #EXTINF:secs,display title
_EXTINF_STANDARD_RE = re.compile(r"^#EXTINF:\s*(-?\d+)\s*,\s*(.+)$")

# Minimal EXTINF:  #EXTINF:display title  (no duration)
_EXTINF_MINIMAL_RE = re.compile(r"^#EXTINF:\s*(.+)$")

# BOM character
_BOM = "\ufeff"


def parse_m3u(
    path: Path,
    base_dir: Path | None = None,
    music_dirs: list[Path] | None = None,
) -> list[PlaylistFileEntry]:
    """Parse an M3U or M3U8 playlist file.

    Args:
        path: Path to the ``.m3u`` / ``.m3u8`` file.
        base_dir: Base directory for resolving relative paths.
                  Defaults to the parent directory of *path*.
        music_dirs: Additional directories to search when a relative
                    path cannot be resolved against *base_dir*.

    Returns:
        List of parsed playlist entries with resolved paths.
    """
    if base_dir is None:
        base_dir = path.parent

    entries: list[PlaylistFileEntry] = []

    # Pending metadata from #EXTINF lines
    pending_secs: int = -1
    pending_title: str = ""
    pending_artist: str = ""
    pending_album: str = ""
    pending_url: str = ""

    try:
        raw_bytes = path.read_bytes()
    except OSError:
        logger.warning("Cannot read playlist file: %s", path)
        return entries

    # Detect encoding: try UTF-8 first, fall back to latin-1
    text = _decode_playlist_bytes(raw_bytes)

    for line in text.splitlines():
        line = line.strip()

        if not line:
            continue

        # ---- Comment / metadata lines ----
        if line.startswith("#"):
            # Extended EXTINF with <artist> - <album> - <title>
            m = _EXTINF_EXTENDED_RE.match(line)
            if m:
                pending_secs = _safe_int(m.group(1), -1)
                pending_artist = m.group(2).strip()
                pending_album = m.group(3).strip()
                pending_title = m.group(4).strip()
                continue

            # Standard EXTINF with secs,title
            m = _EXTINF_STANDARD_RE.match(line)
            if m:
                pending_secs = _safe_int(m.group(1), -1)
                pending_title = m.group(2).strip()
                continue

            # Minimal EXTINF (title only)
            m = _EXTINF_MINIMAL_RE.match(line)
            if m:
                pending_title = m.group(1).strip()
                continue

            # #EXTURL:<url>  (LMS extension — prefer this as the track path)
            if line.startswith("#EXTURL:"):
                pending_url = line[len("#EXTURL:") :].strip()
                continue

            # Skip #EXTM3U header, #CURTRACK, and any other comments
            continue

        # ---- Track line ----
        # If an #EXTURL was seen, use that; otherwise use this line
        track_ref = pending_url if pending_url else line

        resolved = _resolve_path(track_ref, base_dir, music_dirs)

        entries.append(
            PlaylistFileEntry(
                path=resolved,
                title=pending_title,
                artist=pending_artist,
                album=pending_album,
                duration_seconds=pending_secs,
            )
        )

        # Reset pending metadata
        pending_secs = -1
        pending_title = ""
        pending_artist = ""
        pending_album = ""
        pending_url = ""

    logger.debug("Parsed %d entries from M3U: %s", len(entries), path)
    return entries


def write_m3u(
    path: Path,
    tracks: list[Any],
    *,
    current_index: int | None = None,
) -> None:
    """Write an Extended M3U playlist file.

    Each element of *tracks* should be a ``PlaylistTrack`` (or any object
    with ``path``, ``title``, ``artist``, ``album``, and ``duration_ms``
    attributes).  Plain ``str`` paths are also accepted.

    Args:
        path: Destination ``.m3u`` / ``.m3u8`` file.
        tracks: Sequence of track objects or path strings.
        current_index: If given, a ``#CURTRACK <n>`` comment is written so
                       that playback can resume at the correct position.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []

    if current_index is not None:
        lines.append(f"#CURTRACK {current_index}")

    lines.append("#EXTM3U")

    for track in tracks:
        if isinstance(track, str):
            lines.append(track)
            continue

        # PlaylistTrack-compatible object
        duration_s = _track_duration_seconds(track)
        title = getattr(track, "title", "") or ""
        artist = getattr(track, "artist", "") or ""
        album = getattr(track, "album", "") or ""
        track_path: str = getattr(track, "path", "") or ""

        # Write #EXTINF
        if artist and album and title:
            # Extended format: #EXTINF:secs,<artist> - <album> - <title>
            lines.append(f"#EXTINF:{duration_s},<{artist}> - <{album}> - <{title}>")
        elif title:
            display = f"{artist} - {title}" if artist else title
            lines.append(f"#EXTINF:{duration_s},{display}")

        lines.append(track_path)

    # Write atomically via temp file
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        tmp_path.replace(path)
    except OSError:
        logger.exception("Failed to write M3U: %s", path)
        # Clean up temp file on failure
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    logger.debug("Wrote %d tracks to M3U: %s", len(tracks), path)


def read_m3u_curtrack(path: Path) -> int:
    """Read the ``#CURTRACK`` index from an M3U file.

    Returns 0 if the marker is missing or the file cannot be read.

    Reference: ``Slim::Formats::Playlists::M3U::readCurTrackForM3U``
    """
    try:
        first_line = path.read_text(encoding="utf-8").split("\n", 1)[0].strip()
    except OSError:
        return 0

    m = re.match(r"^#CURTRACK\s+(\d+)$", first_line)
    return int(m.group(1)) if m else 0


# ---------------------------------------------------------------------------
# PLS
# ---------------------------------------------------------------------------

_PLS_FILE_RE = re.compile(r"^File(\d+)\s*=\s*(.+)$", re.IGNORECASE)
_PLS_TITLE_RE = re.compile(r"^Title(\d+)\s*=\s*(.+)$", re.IGNORECASE)
_PLS_LENGTH_RE = re.compile(r"^Length(\d+)\s*=\s*(-?\d+)$", re.IGNORECASE)


def parse_pls(
    path: Path,
    base_dir: Path | None = None,
    music_dirs: list[Path] | None = None,
) -> list[PlaylistFileEntry]:
    """Parse a PLS playlist file.

    Args:
        path: Path to the ``.pls`` file.
        base_dir: Base directory for resolving relative paths.
        music_dirs: Additional directories to try.

    Returns:
        List of parsed playlist entries.
    """
    if base_dir is None:
        base_dir = path.parent

    try:
        raw_bytes = path.read_bytes()
    except OSError:
        logger.warning("Cannot read playlist file: %s", path)
        return []

    text = _decode_playlist_bytes(raw_bytes)

    urls: dict[int, str] = {}
    titles: dict[int, str] = {}
    lengths: dict[int, int] = {}

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        m = _PLS_FILE_RE.match(line)
        if m:
            urls[int(m.group(1))] = m.group(2).strip()
            continue

        m = _PLS_TITLE_RE.match(line)
        if m:
            titles[int(m.group(1))] = m.group(2).strip()
            continue

        m = _PLS_LENGTH_RE.match(line)
        if m:
            lengths[int(m.group(1))] = _safe_int(m.group(2), -1)
            continue

    entries: list[PlaylistFileEntry] = []
    for idx in sorted(urls.keys()):
        raw_path = urls[idx]
        resolved = _resolve_path(raw_path, base_dir, music_dirs)
        entries.append(
            PlaylistFileEntry(
                path=resolved,
                title=titles.get(idx, ""),
                duration_seconds=lengths.get(idx, -1),
            )
        )

    logger.debug("Parsed %d entries from PLS: %s", len(entries), path)
    return entries


def write_pls(
    path: Path,
    tracks: list[Any],
    *,
    playlist_name: str = "Resonance Playlist",
) -> None:
    """Write a PLS playlist file.

    Args:
        path: Destination ``.pls`` file.
        tracks: Sequence of track objects or path strings.
        playlist_name: Name stored in the PLS header.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = [
        "[playlist]",
        f"PlaylistName={playlist_name}",
    ]

    item_count = 0
    for track in tracks:
        item_count += 1

        if isinstance(track, str):
            lines.append(f"File{item_count}={track}")
            lines.append(f"Length{item_count}=-1")
            continue

        track_path = getattr(track, "path", "") or ""
        title = getattr(track, "title", "") or ""
        duration_s = _track_duration_seconds(track)

        lines.append(f"File{item_count}={track_path}")
        if title:
            lines.append(f"Title{item_count}={title}")
        lines.append(f"Length{item_count}={duration_s}")

    lines.append(f"NumberOfEntries={item_count}")
    lines.append("Version=2")

    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        tmp_path.replace(path)
    except OSError:
        logger.exception("Failed to write PLS: %s", path)
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    logger.debug("Wrote %d tracks to PLS: %s", item_count, path)


# ---------------------------------------------------------------------------
# Format detection helper
# ---------------------------------------------------------------------------

PLAYLIST_EXTENSIONS: frozenset[str] = frozenset({
    ".m3u", ".m3u8", ".pls",
})


def is_playlist_file(path: Path) -> bool:
    """Return True if *path* has a recognised playlist file extension."""
    return path.suffix.lower() in PLAYLIST_EXTENSIONS


def parse_playlist_file(
    path: Path,
    base_dir: Path | None = None,
    music_dirs: list[Path] | None = None,
) -> list[PlaylistFileEntry]:
    """Auto-detect format and parse a playlist file.

    Raises ``ValueError`` for unsupported extensions.
    """
    ext = path.suffix.lower()
    if ext in {".m3u", ".m3u8"}:
        return parse_m3u(path, base_dir=base_dir, music_dirs=music_dirs)
    if ext == ".pls":
        return parse_pls(path, base_dir=base_dir, music_dirs=music_dirs)
    raise ValueError(f"Unsupported playlist format: {ext}")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _decode_playlist_bytes(raw: bytes) -> str:
    """Decode raw playlist bytes, handling BOM and encoding detection.

    Tries UTF-8-sig first (handles BOM automatically), then falls back
    to latin-1 which never fails.
    """
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _resolve_path(
    entry: str,
    base_dir: Path,
    music_dirs: list[Path] | None,
) -> str:
    """Resolve a playlist entry to an absolute path or return as-is for URLs.

    Resolution order (matching LMS behaviour):
    1. If it's a URL (http://, file://, etc.) — return as-is.
    2. If it's already absolute and exists — return as-is.
    3. Resolve relative to *base_dir* (the M3U file's directory).
    4. Try each directory in *music_dirs*.
    5. Fall back to the raw string.
    """
    # URLs — pass through
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+\-.]*://", entry):
        # Handle file:// URLs by converting to path
        if entry.startswith("file:///"):
            try:
                from urllib.parse import unquote
                file_path = unquote(entry[len("file:///"):])
                # On Windows, file:///C:/... → C:/...
                # On Unix, file:///path → /path
                if not file_path.startswith("/"):
                    # Windows path like C:/...
                    return str(Path(file_path))
                return file_path
            except Exception:
                pass
        return entry

    # Absolute path — return the raw string to avoid platform conversion
    # (e.g. "/music/song.mp3" → "C:\music\song.mp3" on Windows).
    # Check for Unix-style absolute (starts with /) or Windows-style (X:\).
    if entry.startswith("/") or (len(entry) >= 3 and entry[1] == ":" and entry[2] in ("/", "\\")):
        return entry

    entry_path = Path(entry)
    if entry_path.is_absolute():
        return entry

    # Relative to base_dir
    resolved = base_dir / entry_path
    if resolved.exists():
        return str(resolved.resolve())

    # Try music directories
    if music_dirs:
        for mdir in music_dirs:
            candidate = mdir / entry_path
            if candidate.exists():
                return str(candidate.resolve())

    # Fall back: return relative-to-base even if file doesn't exist
    return str(resolved)


def _safe_int(value: str, default: int = 0) -> int:
    """Parse an integer string, returning *default* on failure."""
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _track_duration_seconds(track: Any) -> int:
    """Extract duration in whole seconds from a track object.

    Looks for ``duration_ms`` (milliseconds) first, then ``duration``
    (seconds).  Returns ``-1`` if unavailable.
    """
    duration_ms = getattr(track, "duration_ms", None)
    if duration_ms is not None and duration_ms > 0:
        return duration_ms // 1000

    duration = getattr(track, "duration", None)
    if duration is not None and duration > 0:
        return int(duration)

    return -1
