"""
Dynamic query builder for LibraryDb.

Replaces the combinatorial explosion of per-filter query functions with
composable Filter dataclasses and generic SQL builders.

Design principles:
- Filter dataclasses are frozen (immutable, hashable, safe to pass around).
- SQL is built from a small set of well-tested fragments — no raw user input
  is ever interpolated.  All dynamic values go through parameter binding (?).
- ORDER BY clauses are delegated to the existing whitelist-based helpers in
  ``resonance.core.db.ordering`` (no change to that module).
- JOINs are added only when a filter actually needs them (genre_id needs
  track_genres, role_id needs contributor_tracks).

Usage::

    from resonance.core.db.query_builder import TrackFilter, build_tracks_query

    f = TrackFilter(genre_id=5, year=2020)
    sql, params = build_tracks_query(f, order_by="title", limit=50, offset=0)
    cursor = await conn.execute(sql, params)

The old per-combination functions in ``queries_tracks.py``, ``queries_albums.py``,
and ``queries_artists.py`` are retained as thin wrappers that construct a filter
and call the builder.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from resonance.core.db.ordering import (
    albums_order_clause,
    artists_order_clause,
    tracks_order_clause,
)

# =============================================================================
# Filter dataclasses
# =============================================================================


@dataclass(frozen=True, slots=True)
class TrackFilter:
    """
    Composable filter for track queries.

    Set any combination of fields to narrow results.  ``None`` means
    "no constraint on this dimension".
    """

    genre_id: int | None = None
    artist_id: int | None = None
    album_id: int | None = None
    year: int | None = None
    compilation: int | None = None
    role_id: int | None = None


@dataclass(frozen=True, slots=True)
class AlbumFilter:
    """
    Composable filter for album queries.

    ``genre_id`` and ``role_id`` require a JOIN through the tracks table.
    ``compilation`` filters on ``tracks.compilation``.
    """

    artist_id: int | None = None
    genre_id: int | None = None
    year: int | None = None
    compilation: int | None = None
    role_id: int | None = None


@dataclass(frozen=True, slots=True)
class ArtistFilter:
    """
    Composable filter for artist queries.

    All filters except the trivial "all artists" case require a JOIN
    through the tracks table.
    """

    genre_id: int | None = None
    year: int | None = None
    compilation: int | None = None
    role_id: int | None = None


# =============================================================================
# Track query builder
# =============================================================================


def _track_joins_and_where(
    f: TrackFilter,
) -> tuple[list[str], list[str], list[Any]]:
    """
    Build JOIN and WHERE fragments for a TrackFilter.

    Returns:
        (joins, where_clauses, params)
    """
    joins: list[str] = []
    clauses: list[str] = []
    params: list[Any] = []

    if f.genre_id is not None:
        joins.append("JOIN track_genres tg ON tg.track_id = t.id")
        clauses.append("tg.genre_id = ?")
        params.append(int(f.genre_id))

    if f.role_id is not None:
        joins.append("JOIN contributor_tracks ct ON ct.track_id = t.id")
        clauses.append("ct.role_id = ?")
        params.append(int(f.role_id))

    if f.artist_id is not None:
        clauses.append("t.artist_id = ?")
        params.append(int(f.artist_id))

    if f.album_id is not None:
        clauses.append("t.album_id = ?")
        params.append(int(f.album_id))

    if f.year is not None:
        clauses.append("t.year = ?")
        params.append(int(f.year))

    if f.compilation is not None:
        clauses.append("t.compilation = ?")
        params.append(int(f.compilation))

    return joins, clauses, params


def build_tracks_query(
    f: TrackFilter,
    *,
    order_by: str = "title",
    limit: int = 100,
    offset: int = 0,
) -> tuple[str, list[Any]]:
    """
    Build a SELECT query for tracks with the given filter.

    Returns:
        ``(sql, params)`` ready for ``conn.execute(sql, params)``.
    """
    joins, clauses, params = _track_joins_and_where(f)

    join_sql = ("\n        " + "\n        ".join(joins)) if joins else ""
    where_sql = "WHERE " + " AND ".join(clauses) if clauses else ""
    order_sql = tracks_order_clause(order_by)

    sql = f"""
        SELECT * FROM tracks t{join_sql}
        {where_sql}
        {order_sql}
        LIMIT ? OFFSET ?;"""

    params.extend([int(limit), int(offset)])
    return sql, params


def build_tracks_count_query(
    f: TrackFilter,
) -> tuple[str, list[Any]]:
    """
    Build a COUNT query for tracks with the given filter.

    Returns:
        ``(sql, params)`` ready for ``conn.execute(sql, params)``.
    """
    joins, clauses, params = _track_joins_and_where(f)

    join_sql = ("\n        " + "\n        ".join(joins)) if joins else ""
    where_sql = "WHERE " + " AND ".join(clauses) if clauses else ""

    sql = f"""
        SELECT COUNT(*) AS c FROM tracks t{join_sql}
        {where_sql};"""

    return sql, params


# =============================================================================
# Album query builder
# =============================================================================

# Base SELECT used by all album list queries (with track_count subquery).
_ALBUM_SELECT = """\
SELECT
            a.id,
            a.title,
            a.artist_id,
            ar.name AS artist_name,
            a.year,
            (SELECT COUNT(*) FROM tracks t2 WHERE t2.album_id = a.id) AS track_count
        FROM albums a
        LEFT JOIN artists ar ON a.artist_id = ar.id"""


def _album_joins_and_where(
    f: AlbumFilter,
) -> tuple[list[str], list[str], list[Any], bool]:
    """
    Build JOIN and WHERE fragments for an AlbumFilter.

    Some filters (genre_id, compilation, role_id) require joining through
    the tracks table.  When any such join is present, we need DISTINCT.

    Returns:
        (joins, where_clauses, params, needs_distinct)
    """
    joins: list[str] = []
    clauses: list[str] = []
    params: list[Any] = []
    needs_distinct = False

    # Filters that can be applied directly on the albums table
    if f.artist_id is not None:
        clauses.append("a.artist_id = ?")
        params.append(int(f.artist_id))

    if f.year is not None:
        clauses.append("a.year = ?")
        params.append(int(f.year))

    # Filters that require joining through tracks
    if f.compilation is not None:
        joins.append("JOIN tracks t ON t.album_id = a.id")
        clauses.append("t.compilation = ?")
        params.append(int(f.compilation))
        needs_distinct = True

    if f.genre_id is not None:
        # If we already joined tracks for compilation, reuse that alias;
        # otherwise add the tracks join now.
        if not any("JOIN tracks t ON" in j for j in joins):
            joins.append("JOIN tracks t ON t.album_id = a.id")
        joins.append("JOIN track_genres tg ON tg.track_id = t.id")
        clauses.append("tg.genre_id = ?")
        params.append(int(f.genre_id))
        needs_distinct = True

    if f.role_id is not None:
        if not any("JOIN tracks t ON" in j for j in joins):
            joins.append("JOIN tracks t ON t.album_id = a.id")
        joins.append("JOIN contributor_tracks ct ON ct.track_id = t.id")
        clauses.append("ct.role_id = ?")
        params.append(int(f.role_id))
        needs_distinct = True

    return joins, clauses, params, needs_distinct


def build_albums_query(
    f: AlbumFilter,
    *,
    order_by: str = "album",
    limit: int = 100,
    offset: int = 0,
) -> tuple[str, list[Any]]:
    """
    Build a SELECT query for albums (with track_count) using the given filter.

    Returns:
        ``(sql, params)`` ready for ``conn.execute(sql, params)``.
    """
    joins, clauses, params, needs_distinct = _album_joins_and_where(f)

    join_sql = ("\n        " + "\n        ".join(joins)) if joins else ""
    where_sql = "WHERE " + " AND ".join(clauses) if clauses else ""
    order_sql = albums_order_clause(order_by)
    distinct = "DISTINCT\n            " if needs_distinct else ""

    sql = f"""
        SELECT {distinct}a.id,
            a.title,
            a.artist_id,
            ar.name AS artist_name,
            a.year,
            (SELECT COUNT(*) FROM tracks t2 WHERE t2.album_id = a.id) AS track_count
        FROM albums a
        LEFT JOIN artists ar ON a.artist_id = ar.id{join_sql}
        {where_sql}
        {order_sql}
        LIMIT ? OFFSET ?;"""

    params.extend([int(limit), int(offset)])
    return sql, params


def build_albums_count_query(
    f: AlbumFilter,
) -> tuple[str, list[Any]]:
    """
    Build a COUNT query for albums with the given filter.

    Returns:
        ``(sql, params)`` ready for ``conn.execute(sql, params)``.
    """
    joins, clauses, params, needs_distinct = _album_joins_and_where(f)

    join_sql = ("\n        " + "\n        ".join(joins)) if joins else ""
    where_sql = "WHERE " + " AND ".join(clauses) if clauses else ""
    count_expr = "COUNT(DISTINCT a.id)" if needs_distinct else "COUNT(*)"

    sql = f"""
        SELECT {count_expr} AS c
        FROM albums a{join_sql}
        {where_sql};"""

    return sql, params


# =============================================================================
# Artist query builder
# =============================================================================


def _artist_joins_and_where(
    f: ArtistFilter,
) -> tuple[list[str], list[str], list[Any], bool]:
    """
    Build JOIN and WHERE fragments for an ArtistFilter.

    Almost all artist filters require joining through the tracks table.

    Returns:
        (joins, where_clauses, params, needs_track_join)
    """
    joins: list[str] = []
    clauses: list[str] = []
    params: list[Any] = []
    needs_track_join = False

    if f.compilation is not None:
        needs_track_join = True
        clauses.append("t.compilation = ?")
        params.append(int(f.compilation))

    if f.year is not None:
        needs_track_join = True
        clauses.append("t.year = ?")
        params.append(int(f.year))

    if f.genre_id is not None:
        needs_track_join = True
        joins.append("JOIN track_genres tg ON tg.track_id = t.id")
        clauses.append("tg.genre_id = ?")
        params.append(int(f.genre_id))

    if f.role_id is not None:
        needs_track_join = True
        joins.append("JOIN contributor_tracks ct ON ct.track_id = t.id")
        clauses.append("ct.role_id = ?")
        params.append(int(f.role_id))

    # If any filter needs the tracks table, add the base tracks join first.
    if needs_track_join:
        joins.insert(0, "JOIN tracks t ON t.artist_id = ar.id")

    return joins, clauses, params, needs_track_join


def build_artists_query(
    f: ArtistFilter,
    *,
    order_by: str = "artist",
    limit: int = 100,
    offset: int = 0,
) -> tuple[str, list[Any]]:
    """
    Build a SELECT query for artists (with album_count) using the given filter.

    Returns:
        ``(sql, params)`` ready for ``conn.execute(sql, params)``.
    """
    joins, clauses, params, needs_track_join = _artist_joins_and_where(f)

    join_sql = ("\n        " + "\n        ".join(joins)) if joins else ""
    where_sql = "WHERE " + " AND ".join(clauses) if clauses else ""
    order_sql = artists_order_clause(order_by)
    distinct = "DISTINCT\n            " if needs_track_join else ""

    sql = f"""
        SELECT {distinct}ar.id,
            ar.name,
            (SELECT COUNT(DISTINCT al.id) FROM albums al WHERE al.artist_id = ar.id) AS album_count
        FROM artists ar{join_sql}
        {where_sql}
        {order_sql}
        LIMIT ? OFFSET ?;"""

    params.extend([int(limit), int(offset)])
    return sql, params


def build_artists_count_query(
    f: ArtistFilter,
) -> tuple[str, list[Any]]:
    """
    Build a COUNT query for artists with the given filter.

    Returns:
        ``(sql, params)`` ready for ``conn.execute(sql, params)``.
    """
    joins, clauses, params, needs_track_join = _artist_joins_and_where(f)

    join_sql = ("\n        " + "\n        ".join(joins)) if joins else ""
    where_sql = "WHERE " + " AND ".join(clauses) if clauses else ""

    # Always COUNT(DISTINCT ar.id) when joining through tracks to avoid
    # inflated counts from one-to-many joins.
    count_expr = "COUNT(DISTINCT ar.id)" if needs_track_join else "COUNT(*)"

    sql = f"""
        SELECT {count_expr} AS c
        FROM artists ar{join_sql}
        {where_sql};"""

    return sql, params


# =============================================================================
# Result row helpers
# =============================================================================

def album_row_to_dict(row: Any) -> dict[str, Any]:
    """
    Convert an aiosqlite Row from the album builder query to a dict.

    The builder SELECT always returns:
    ``id, title, artist_id, artist_name, year, track_count``

    This matches the dict format used by the existing handler code.
    """
    return {
        "id": int(row["id"]),
        "name": row["title"],
        "artist": row["artist_name"],
        "artist_id": row["artist_id"],
        "year": row["year"],
        "track_count": int(row["track_count"]),
    }


def artist_row_to_dict(row: Any) -> dict[str, Any]:
    """
    Convert an aiosqlite Row from the artist builder query to a dict.

    The builder SELECT always returns: ``id, name, album_count``
    """
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "album_count": int(row["album_count"]),
    }
