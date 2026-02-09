"""
Shared ORDER BY clause helpers for LibraryDb queries.

These helpers centralize the translation from higher-level sort keys into
SQL snippets (including reasonable COLLATE/NULLS handling) so the logic
doesn't get duplicated across query modules.

Important:
- The returned strings are intended to be *static SQL fragments* selected
  from a small whitelist. Do NOT concatenate user input into ORDER BY.
- Callers should validate/whitelist the `order_by` argument before passing it.
"""

from __future__ import annotations

from typing import Literal

TracksOrderBy = Literal[
    "title",
    "tracknum",
    "album",
    "artist",
    "year",
    "id",
]

AlbumsOrderBy = Literal[
    "album",
    "title",
    "artist",
    "year",
    "id",
]

ArtistsOrderBy = Literal[
    "artist",
    "name",
    "albums",
    "id",
]


def tracks_order_clause(order_by: str) -> str:
    """
    Return an ORDER BY clause for track list queries.

    Expected order_by values are a small whitelist (see TracksOrderBy),
    but we accept `str` to keep call sites ergonomic. Unknown values
    fall back to a sensible default.
    """
    # NOTE: Keep these as static fragments (no user input interpolation).
    if order_by == "tracknum":
        # Disc then track then title as tiebreaker.
        return (
            "ORDER BY "
            "COALESCE(t.disc_no, 0) ASC, "
            "COALESCE(t.track_no, 0) ASC, "
            "t.title COLLATE NOCASE ASC, "
            "t.id ASC"
        )
    if order_by == "album":
        return (
            "ORDER BY "
            "t.album COLLATE NOCASE ASC, "
            "COALESCE(t.disc_no, 0) ASC, "
            "COALESCE(t.track_no, 0) ASC, "
            "t.title COLLATE NOCASE ASC, "
            "t.id ASC"
        )
    if order_by == "artist":
        return (
            "ORDER BY "
            "t.artist COLLATE NOCASE ASC, "
            "t.album COLLATE NOCASE ASC, "
            "COALESCE(t.disc_no, 0) ASC, "
            "COALESCE(t.track_no, 0) ASC, "
            "t.title COLLATE NOCASE ASC, "
            "t.id ASC"
        )
    if order_by == "year":
        return (
            "ORDER BY "
            "t.year ASC, "
            "t.album COLLATE NOCASE ASC, "
            "COALESCE(t.disc_no, 0) ASC, "
            "COALESCE(t.track_no, 0) ASC, "
            "t.title COLLATE NOCASE ASC, "
            "t.id ASC"
        )
    if order_by == "id":
        return "ORDER BY t.id ASC"

    # Default: title
    return "ORDER BY t.title COLLATE NOCASE ASC, t.id ASC"


def albums_order_clause(order_by: str) -> str:
    """
    Return an ORDER BY clause for album list queries.

    Uses common, stable tie-breakers to avoid flickering pagination.
    """
    if order_by in ("album", "title"):
        return "ORDER BY a.title COLLATE NOCASE ASC, a.id ASC"
    if order_by == "artist":
        # Prefer artist name (join-provided alias) then album title.
        return "ORDER BY artist_name COLLATE NOCASE ASC, a.title COLLATE NOCASE ASC, a.id ASC"
    if order_by == "year":
        return "ORDER BY a.year ASC, a.title COLLATE NOCASE ASC, a.id ASC"
    if order_by == "id":
        return "ORDER BY a.id ASC"

    # Default: album title
    return "ORDER BY a.title COLLATE NOCASE ASC, a.id ASC"


def artists_order_clause(order_by: str) -> str:
    """
    Return an ORDER BY clause for artist list queries.

    Notes:
    - Some artist queries compute album counts; `albums` ordering assumes an
      `album_count` column/alias exists in the SELECT.
    """
    if order_by in ("artist", "name"):
        return "ORDER BY ar.name COLLATE NOCASE ASC, ar.id ASC"
    if order_by == "albums":
        # Descending album_count, then name for stable paging.
        return "ORDER BY album_count DESC, ar.name COLLATE NOCASE ASC, ar.id ASC"
    if order_by == "id":
        return "ORDER BY ar.id ASC"

    # Default: name
    return "ORDER BY ar.name COLLATE NOCASE ASC, ar.id ASC"
