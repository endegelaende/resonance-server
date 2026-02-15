"""
Playlist Helpers — shared state, filesystem utilities, and parsers.

This module contains all shared state, helper functions, and track-resolution
logic used by the playlist sub-modules:

- ``playlist_playback``  — play, pause, stop, index, jump, stream start
- ``playlist_mutation``  — add, insert, delete, clear, move, shuffle, repeat, bulk ops
- ``playlist_query``     — metadata queries, tracks list, event-style noops
- ``playlist_persistence`` — save, load, preview, resume

By centralising these here the sub-modules form a clean DAG with no circular
imports::

    playlist_helpers  (standalone)
         ↑
    playlist_playback  (depends on helpers)
         ↑
    playlist_mutation  (depends on helpers + playback)
         ↑
    playlist_persistence  (depends on helpers + playback + mutation)

    playlist_query  (depends on helpers only)

    playlist.py  (facade — imports from all sub-modules)
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from resonance.core.db.models import TrackRow
from resonance.core.events import PlayerPlaylistEvent, event_bus
from resonance.core.playlist_formats import PlaylistFileEntry, parse_m3u, write_m3u
from resonance.protocol.commands import FLAG_NO_RESTART_DECODER
from resonance.web.handlers import CommandContext
from resonance.web.jsonrpc_helpers import (
    get_filter_int,
    parse_start_items,
    parse_tagged_params,
)

logger = logging.getLogger(__name__)

# =============================================================================
# Module-level state
# =============================================================================

# MVP snapshot store for playlist save/resume compatibility.
# Structure: {player_id: {normalized_name: snapshot_dict}}
_SAVED_PLAYLISTS: dict[str, dict[str, dict[str, Any]]] = {}

# Best-effort in-memory history of zapped items per player.
_ZAPPED_TRACKS: dict[str, list[Any]] = {}

# ---------------------------------------------------------------------------
# Saved-playlists directory (M3U persistence)
# ---------------------------------------------------------------------------

_SAVED_PLAYLISTS_DIR: Path | None = None


# =============================================================================
# Saved-playlist filesystem functions
# =============================================================================


def configure_saved_playlists_dir(directory: Path | None) -> None:
    """Set the directory used for M3U saved-playlist persistence.

    Call once during server startup.  Passing ``None`` disables
    disk persistence (pure in-memory snapshots only).
    """
    global _SAVED_PLAYLISTS_DIR  # noqa: PLW0603
    _SAVED_PLAYLISTS_DIR = directory
    if directory is not None:
        directory.mkdir(parents=True, exist_ok=True)
        logger.info("Saved-playlists directory: %s", directory)


def get_saved_playlists_dir() -> Path | None:
    """Return the configured saved-playlists directory (or ``None``)."""
    return _SAVED_PLAYLISTS_DIR


def _safe_playlist_filename(name: str) -> str:
    """Sanitise a playlist name into a safe filename (without extension)."""
    # Replace problematic characters with underscores
    safe = name.strip()
    for ch in r'\/:*?"<>|':
        safe = safe.replace(ch, "_")
    # Collapse multiple underscores / strip leading dots
    while "__" in safe:
        safe = safe.replace("__", "_")
    safe = safe.strip("_. ")
    return safe or "unnamed"


def _m3u_path_for_name(name: str) -> Path | None:
    """Return the M3U file path for a playlist name, or None if no dir configured."""
    if _SAVED_PLAYLISTS_DIR is None:
        return None
    return _SAVED_PLAYLISTS_DIR / (_safe_playlist_filename(name) + ".m3u")


def list_saved_playlists() -> list[dict[str, Any]]:
    """List all saved M3U playlists on disk.

    Returns a list of dicts with ``id`` (filename stem), ``playlist`` (display
    name derived from filename), and ``url`` (absolute path) keys — matching
    the LMS ``playlists_loop`` schema.
    """
    if _SAVED_PLAYLISTS_DIR is None or not _SAVED_PLAYLISTS_DIR.is_dir():
        return []

    results: list[dict[str, Any]] = []
    for p in sorted(_SAVED_PLAYLISTS_DIR.glob("*.m3u")):
        results.append({
            "id": p.stem,
            "playlist": p.stem,
            "url": str(p),
        })
    for p in sorted(_SAVED_PLAYLISTS_DIR.glob("*.m3u8")):
        results.append({
            "id": p.stem,
            "playlist": p.stem,
            "url": str(p),
        })
    return results


def delete_saved_playlist(name: str) -> bool:
    """Delete a saved playlist M3U file by name.  Returns True if deleted."""
    m3u = _m3u_path_for_name(name)
    if m3u is not None and m3u.exists():
        m3u.unlink()
        logger.info("Deleted saved playlist: %s", m3u)
        return True
    return False


def rename_saved_playlist(old_name: str, new_name: str) -> bool:
    """Rename a saved playlist M3U file.  Returns True if renamed."""
    old_path = _m3u_path_for_name(old_name)
    new_path = _m3u_path_for_name(new_name)
    if old_path is None or new_path is None:
        return False
    if old_path.exists():
        old_path.rename(new_path)
        logger.info("Renamed saved playlist: %s -> %s", old_path, new_path)
        return True
    return False


def load_saved_playlist_tracks(
    name: str,
) -> list[PlaylistFileEntry] | None:
    """Load tracks from a saved M3U playlist by name.

    Returns ``None`` if the playlist doesn't exist on disk.
    """
    m3u = _m3u_path_for_name(name)
    if m3u is None or not m3u.exists():
        return None
    return parse_m3u(m3u)


# =============================================================================
# Stream / protocol helpers
# =============================================================================


def _stream_flags_for_explicit_restart(flags: int) -> int:
    """Clear no-restart-decoder bit for explicit STOP+FLUSH+START restarts."""
    try:
        normalized = int(flags)
    except (TypeError, ValueError):
        normalized = 0
    return normalized & ~FLAG_NO_RESTART_DECODER


def _preview_snapshot_name(player_id: str) -> str:
    """Build the temporary snapshot key used by playlist preview."""
    return f"tempplaylist_{player_id.replace(':', '')}"


# =============================================================================
# Parameter parsing helpers
# =============================================================================


def _parse_playlist_kv_params(params: list[Any], *, start_index: int = 0) -> dict[str, str]:
    """Parse LMS tagged params plus optional dict-style params."""
    parsed = parse_tagged_params(params[start_index:])

    for raw in params[start_index:]:
        if not isinstance(raw, dict):
            continue
        for key, value in raw.items():
            if value is None:
                continue
            parsed[str(key)] = str(value)

    return parsed


def _first_non_tag_param(params: list[Any], *, start_index: int = 0) -> str | None:
    """Return the first positional value that is not a tag:value token."""
    for raw in params[start_index:]:
        if isinstance(raw, str):
            value = raw.strip()
            if not value or ":" in value:
                continue
            return value
        if isinstance(raw, (int, float)):
            return str(raw)
    return None


def _is_truthy_tag(value: str | None) -> bool:
    """Interpret LMS-style tagged booleans (1/0, true/false, yes/no)."""
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_optional_playlist_index(params: list[Any], *, start_index: int = 2) -> int | None:
    """Parse optional playlist index arguments used by LMS-style query subcommands."""
    if len(params) <= start_index:
        return None

    raw = params[start_index]
    if isinstance(raw, str):
        value = raw.strip()
        if not value or value == "?":
            return None
        raw = value

    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


# =============================================================================
# Track / playlist utilities
# =============================================================================


def _playlist_track_for_query(playlist: Any, *, index: int | None = None) -> Any | None:
    """Return the requested track (or current track) from a playlist object."""
    if playlist is None or len(playlist) == 0:
        return None

    resolved_index = playlist.current_index if index is None else index
    if resolved_index < 0 or resolved_index >= len(playlist):
        return None

    return playlist.tracks[resolved_index]


def _is_remote_track_url(path: str | None) -> bool:
    """Best-effort remote URL detection for playlist remote query."""
    if not path:
        return False

    value = path.strip().lower()
    return value.startswith(("http://", "https://", "mms://", "rtsp://", "icy://", "ftp://"))


def _normalize_query_string(value: Any) -> str:
    """Normalize possibly-missing string fields in query responses."""
    if value is None:
        return ""
    return str(value)


def _normalize_duration_seconds(duration_ms: Any) -> float:
    """Convert track duration in ms to LMS-style seconds."""
    try:
        duration = float(duration_ms)
    except (TypeError, ValueError):
        return 0.0

    if duration <= 0:
        return 0.0
    return duration / 1000.0


def _normalize_saved_playlist_name(name: str) -> str:
    return name.strip().lower()


def _build_album_alias_command(params: list[Any], *, target_subcommand: str) -> list[Any]:
    """Map legacy playlist *album arguments to playlist *tracks tagged args."""
    mapped: list[Any] = ["playlist", target_subcommand]
    tagged = _parse_playlist_kv_params(params, start_index=2)

    if tagged:
        for key, value in tagged.items():
            mapped.append(f"{key}:{value}")
        return mapped

    positional: list[str] = []
    for raw in params[2:]:
        if isinstance(raw, str):
            value = raw.strip()
            if value:
                positional.append(value)
        elif isinstance(raw, (int, float)):
            positional.append(str(raw))

    if len(positional) == 1:
        try:
            int(positional[0])
            mapped.append(f"album_id:{positional[0]}")
        except ValueError:
            mapped.append(f"album:{positional[0]}")
        return mapped

    legacy_keys = ("genre", "artist", "album", "title")
    for key, value in zip(legacy_keys, positional):
        mapped.append(f"{key}:{value}")

    return mapped


def _matches_search_term(value: str | None, term: str | None) -> bool:
    """Simple LMS-like matcher with optional '*' wildcard support."""
    if not term:
        return True
    if value is None:
        return False

    needle = term.strip().casefold()
    haystack = value.casefold()

    if not needle or needle == "*":
        return True

    if "*" in needle:
        needle = needle.replace("*", "")
        if not needle:
            return True
        return needle in haystack

    return needle in haystack


# =============================================================================
# Track resolution (DB → Track model)
# =============================================================================


def _row_to_library_track(row: TrackRow) -> Any:
    """Convert DB row to a PlaylistTrack for use in playlist operations."""
    from resonance.core.playlist import PlaylistTrack

    return PlaylistTrack(
        track_id=row.id,
        path=row.path,
        title=row.title or "",
        artist=row.artist or "",
        album=row.album or "",
        artist_id=row.artist_id,
        album_id=row.album_id,
        duration_ms=row.duration_ms or 0,
    )


async def _resolve_track_rows_from_filters(
    ctx: CommandContext,
    params: list[Any],
    *,
    start_index: int = 2,
    numeric_fallback_key: str | None = None,
) -> tuple[list[TrackRow] | None, str | None]:
    """
    Resolve playlist filter params to DB track rows.

    Returns (rows, error). Exactly one of these is non-None.
    """
    tagged_params = _parse_playlist_kv_params(params, start_index=start_index)

    track_id = get_filter_int(tagged_params, "track_id") or get_filter_int(tagged_params, "track.id")
    album_id = get_filter_int(tagged_params, "album_id")
    artist_id = get_filter_int(tagged_params, "artist_id")
    genre_id = get_filter_int(tagged_params, "genre_id")
    year = get_filter_int(tagged_params, "year")

    # Accept numeric aliases used by some clients (album:<id>, artist:<id>, genre:<id>).
    if album_id is None:
        album_id = get_filter_int(tagged_params, "album")
    if artist_id is None:
        artist_id = get_filter_int(tagged_params, "artist")
    if genre_id is None:
        genre_id = get_filter_int(tagged_params, "genre")

    # Optional fallback for legacy positional forms where the first arg is numeric.
    if (
        track_id is None
        and album_id is None
        and artist_id is None
        and genre_id is None
        and year is None
        and numeric_fallback_key is not None
    ):
        first = _first_non_tag_param(params, start_index=start_index)
        try:
            parsed = int(first) if first is not None else None
        except (TypeError, ValueError):
            parsed = None

        if parsed is not None:
            if numeric_fallback_key == "track_id":
                track_id = parsed
            elif numeric_fallback_key == "album_id":
                album_id = parsed
            elif numeric_fallback_key == "artist_id":
                artist_id = parsed
            elif numeric_fallback_key == "genre_id":
                genre_id = parsed
            elif numeric_fallback_key == "year":
                year = parsed

    db = ctx.music_library._db

    # If we got a textual genre name, map it once to a genre_id.
    if genre_id is None and tagged_params.get("genre"):
        genre_term = tagged_params["genre"]
        genres = await db.list_genres(limit=500, offset=0)
        exact_match = next(
            (
                g
                for g in genres
                if str(g.get("name", g.get("genre", ""))).strip().casefold()
                == genre_term.strip().casefold()
            ),
            None,
        )
        if exact_match is not None:
            try:
                genre_id = int(exact_match.get("id"))
            except (TypeError, ValueError):
                genre_id = None

    rows: list[TrackRow] = []

    if track_id is not None:
        row = await db.get_track_by_id(track_id)
        rows = [row] if row else []
    elif genre_id is not None and album_id is not None and year is not None:
        rows = await db.list_tracks_by_genre_album_and_year(
            genre_id=genre_id,
            album_id=album_id,
            year=year,
            offset=0,
            limit=1000,
            order_by="album",
        )
    elif genre_id is not None and artist_id is not None and year is not None:
        rows = await db.list_tracks_by_genre_artist_and_year(
            genre_id=genre_id,
            artist_id=artist_id,
            year=year,
            offset=0,
            limit=1000,
            order_by="album",
        )
    elif genre_id is not None and album_id is not None:
        rows = await db.list_tracks_by_genre_and_album(
            genre_id=genre_id,
            album_id=album_id,
            offset=0,
            limit=1000,
            order_by="album",
        )
    elif genre_id is not None and artist_id is not None:
        rows = await db.list_tracks_by_genre_and_artist(
            genre_id=genre_id,
            artist_id=artist_id,
            offset=0,
            limit=1000,
            order_by="album",
        )
    elif album_id is not None and year is not None:
        rows = await db.list_tracks_by_album_and_year(
            album_id=album_id,
            year=year,
            offset=0,
            limit=1000,
            order_by="album",
        )
    elif artist_id is not None and year is not None:
        rows = await db.list_tracks_by_artist_and_year(
            artist_id=artist_id,
            year=year,
            offset=0,
            limit=1000,
            order_by="album",
        )
    elif genre_id is not None and year is not None:
        rows = await db.list_tracks_by_genre_and_year(
            genre_id=genre_id,
            year=year,
            offset=0,
            limit=1000,
            order_by="album",
        )
    elif album_id is not None:
        rows = await db.list_tracks_by_album(album_id=album_id, offset=0, limit=1000, order_by="album")
    elif artist_id is not None:
        rows = await db.list_tracks_by_artist(
            artist_id=artist_id,
            offset=0,
            limit=1000,
            order_by="album",
        )
    elif genre_id is not None:
        rows = await db.list_tracks_by_genre_id(
            genre_id=genre_id,
            offset=0,
            limit=1000,
            order_by="title",
        )
    elif year is not None:
        rows = await db.list_tracks_by_year(year=year, offset=0, limit=1000, order_by="album")
    else:
        # Fallback for legacy textual filters (album/artist/title).
        title_term = tagged_params.get("title")
        album_term = tagged_params.get("album")
        artist_term = tagged_params.get("artist")
        search_query = title_term or album_term or artist_term

        if not search_query:
            return None, "No track criteria specified"

        candidate_rows = await db.search_tracks(search_query, limit=2000, offset=0)
        rows = [
            row
            for row in candidate_rows
            if _matches_search_term(row.title, title_term)
            and _matches_search_term(row.album, album_term)
            and _matches_search_term(row.artist, artist_term)
        ]

    return rows, None


async def _resolve_track(
    ctx: CommandContext,
    track_ref: Any,
    tagged_params: dict[str, str],
) -> Any:
    """
    Resolve a track reference to a PlaylistTrack object.

    track_ref can be:
    - Integer track ID
    - String track ID
    - File path

    Path-traversal protection (§14.3): file paths containing ``..``
    components are rejected to prevent directory-traversal attacks.
    """
    from resonance.core.playlist import PlaylistTrack

    db = ctx.music_library._db

    def _row_to_playlist_track(row: TrackRow) -> PlaylistTrack:
        """Convert a TrackRow dataclass to a PlaylistTrack object."""
        return PlaylistTrack(
            track_id=row.id,
            path=row.path,
            title=row.title or "",
            artist=row.artist or "",
            album=row.album or "",
            artist_id=row.artist_id,
            album_id=row.album_id,
            duration_ms=row.duration_ms or 0,
        )

    # Try as track ID
    try:
        track_id = int(track_ref)
        row = await db.get_track_by_id(track_id)
        if row is not None:
            return _row_to_playlist_track(row)
    except (ValueError, TypeError):
        pass

    # Try as file path
    track_ref_str = str(track_ref)
    if track_ref_str.startswith("file://"):
        track_ref_str = track_ref_str[7:]

    # Path-traversal protection (§14.3): reject paths with ".." components
    normalised = track_ref_str.replace("\\", "/")
    for component in normalised.split("/"):
        if component == "..":
            logger.warning("Path traversal attempt rejected: %r", track_ref_str)
            return None

    row = await db.get_track_by_path(track_ref_str)
    if row is not None:
        return _row_to_playlist_track(row)

    return None
