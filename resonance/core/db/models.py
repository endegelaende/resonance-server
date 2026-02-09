"""
DB models (DTOs) and small normalization helpers extracted from `library_db.py`.

This module is intentionally lightweight:
- No DB connection knowledge
- No SQL
- Pure dataclasses + helper functions
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ArtistRow:
    """Artist record as stored in SQLite."""

    id: int
    name: str
    name_sort: str | None


@dataclass(frozen=True, slots=True)
class AlbumRow:
    """Album record as stored in SQLite."""

    id: int
    title: str
    title_sort: str | None
    artist_id: int | None
    artist_name: str | None  # Denormalized for convenience in some list queries
    year: int | None


@dataclass(frozen=True, slots=True)
class TrackRow:
    """
    Canonical track record as stored in SQLite.

    Notes:
    - `path` is the stable unique identifier for a local file.
    - `artist_id` and `album_id` are FKs to the artists/albums tables (schema v4+).
    """

    id: int
    path: str
    title: str | None
    artist: str | None
    album: str | None
    album_artist: str | None
    track_no: int | None
    disc_no: int | None
    year: int | None
    duration_ms: int | None
    file_size: int | None
    mtime_ns: int | None
    has_artwork: int
    compilation: int = 0
    artist_id: int | None = None
    album_id: int | None = None
    # Audio quality metadata
    sample_rate: int | None = None
    bit_depth: int | None = None
    bitrate: int | None = None
    channels: int | None = None


@dataclass(frozen=True, slots=True)
class UpsertTrack:
    """
    Input record used by scanners/importers.

    `path` is required and must identify the same file across scans.
    `mtime_ns` and `file_size` are used to detect changes.

    `genres` and `contributors` are denormalized inputs that will be persisted into
    their respective normalized tables when upserting.
    """

    path: str
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    album_artist: str | None = None
    track_no: int | None = None
    disc_no: int | None = None
    year: int | None = None
    duration_ms: int | None = None
    file_size: int | None = None
    mtime_ns: int | None = None
    has_artwork: bool = False
    compilation: bool = False
    genres: tuple[str, ...] = ()
    contributors: tuple[tuple[str, str], ...] = ()
    # Audio quality metadata
    sample_rate: int | None = None
    bit_depth: int | None = None
    bitrate: int | None = None
    channels: int | None = None


def normalize_text(value: str | None) -> str | None:
    """
    Normalize optional text fields:
    - strip whitespace
    - coerce empty strings to None
    """
    if value is None:
        return None
    v = value.strip()
    return v if v else None


def normalize_int(value: int | None) -> int | None:
    """Normalize optional integer fields (coerce to int, keep None)."""
    if value is None:
        return None
    return int(value)
