"""
Track-related DB queries extracted from `resonance.core.library_db.LibraryDb`.

Design:
- Functions are *pure DB helpers*: they take an open `aiosqlite.Connection`
  and return rows/materialized dataclasses.
- Ordering is centralized via `resonance.core.db.ordering.tracks_order_clause`.
- These functions assume `conn.row_factory = aiosqlite.Row`.

Important:
- Do NOT interpolate user input into SQL. Any dynamic SQL here is limited to
  ORDER BY clauses selected from a small whitelist in `tracks_order_clause`.
"""

from __future__ import annotations

import aiosqlite

from resonance.core.db.models import TrackRow
from resonance.core.db.ordering import tracks_order_clause


def _row_to_track(row: aiosqlite.Row) -> TrackRow:
    """Convert an aiosqlite Row to a TrackRow dataclass."""
    # Defensive access for columns that may be missing in older databases
    try:
        has_artwork = row["has_artwork"]
    except (KeyError, IndexError):
        has_artwork = 0

    try:
        artist_id = row["artist_id"]
    except (KeyError, IndexError):
        artist_id = None

    try:
        album_id = row["album_id"]
    except (KeyError, IndexError):
        album_id = None

    try:
        compilation = row["compilation"]
    except (KeyError, IndexError):
        compilation = 0

    # Audio quality fields (added in schema v8)
    try:
        sample_rate = row["sample_rate"]
    except (KeyError, IndexError):
        sample_rate = None

    try:
        bit_depth = row["bit_depth"]
    except (KeyError, IndexError):
        bit_depth = None

    try:
        bitrate = row["bitrate"]
    except (KeyError, IndexError):
        bitrate = None

    try:
        channels = row["channels"]
    except (KeyError, IndexError):
        channels = None

    return TrackRow(
        id=int(row["id"]),
        path=str(row["path"]),
        title=row["title"],
        artist=row["artist"],
        album=row["album"],
        album_artist=row["album_artist"],
        track_no=row["track_no"],
        disc_no=row["disc_no"],
        year=row["year"],
        duration_ms=row["duration_ms"],
        file_size=row["file_size"],
        mtime_ns=row["mtime_ns"],
        has_artwork=has_artwork if has_artwork is not None else 0,
        compilation=int(compilation) if compilation is not None else 0,
        artist_id=artist_id,
        album_id=album_id,
        sample_rate=sample_rate,
        bit_depth=bit_depth,
        bitrate=bitrate,
        channels=channels,
    )


# ---------------------------------------------------------------------------
# Basic get/list/count
# ---------------------------------------------------------------------------


async def get_track_by_id(conn: aiosqlite.Connection, track_id: int) -> TrackRow | None:
    cursor = await conn.execute("SELECT * FROM tracks WHERE id = ?;", (track_id,))
    row = await cursor.fetchone()
    return _row_to_track(row) if row else None


async def get_track_by_path(conn: aiosqlite.Connection, path: str) -> TrackRow | None:
    cursor = await conn.execute("SELECT * FROM tracks WHERE path = ?;", (path,))
    row = await cursor.fetchone()
    return _row_to_track(row) if row else None


async def list_tracks(
    conn: aiosqlite.Connection,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[TrackRow]:
    order_clause = tracks_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT * FROM tracks t
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [_row_to_track(r) for r in rows]


async def count_tracks(conn: aiosqlite.Connection) -> int:
    cursor = await conn.execute("SELECT COUNT(*) AS c FROM tracks;")
    row = await cursor.fetchone()
    return int(row["c"]) if row else 0


async def _has_fts5(conn: aiosqlite.Connection) -> bool:
    """Return True if the ``tracks_fts`` FTS5 virtual table exists."""
    cursor = await conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tracks_fts' LIMIT 1;"
    )
    return await cursor.fetchone() is not None


async def search_tracks(
    conn: aiosqlite.Connection,
    query: str,
    *,
    limit: int,
    offset: int,
) -> list[TrackRow]:
    # Prefer FTS5 when the virtual table is available (schema v9+).
    # FTS5 provides relevance ranking via the built-in ``rank`` column and
    # unicode61 diacritics normalisation out of the box.
    if await _has_fts5(conn):
        # FTS5 match syntax: wrap each token in double-quotes so special
        # characters (colons, hyphens, …) are treated as literals, then
        # append ``*`` for prefix matching.
        tokens = query.split()
        match_expr = " ".join(f'"{t}"*' for t in tokens) if tokens else f'"{query}"*'
        try:
            cursor = await conn.execute(
                """
                SELECT t.* FROM tracks t
                JOIN tracks_fts fts ON t.id = fts.rowid
                WHERE tracks_fts MATCH ?
                ORDER BY fts.rank
                LIMIT ? OFFSET ?;
                """,
                (match_expr, int(limit), int(offset)),
            )
            rows = await cursor.fetchall()
            return [_row_to_track(r) for r in rows]
        except Exception:
            # Malformed MATCH expression or FTS index corruption — fall
            # through to the LIKE fallback so the user still gets results.
            pass

    # Fallback: simple LIKE search (no ranking, no diacritics normalisation).
    like_pattern = f"%{query}%"
    cursor = await conn.execute(
        """
        SELECT * FROM tracks t
        WHERE title LIKE ? OR artist LIKE ? OR album LIKE ?
        ORDER BY title COLLATE NOCASE
        LIMIT ? OFFSET ?;
        """,
        (like_pattern, like_pattern, like_pattern, int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [_row_to_track(r) for r in rows]


async def delete_track_by_path(conn: aiosqlite.Connection, path: str) -> bool:
    """Delete a track by path. Returns True if deleted, False if not found."""
    cursor = await conn.execute("DELETE FROM tracks WHERE path = ?;", (path,))
    return cursor.rowcount > 0


async def delete_tracks_by_album_id(conn: aiosqlite.Connection, album_id: int) -> int:
    """Delete all tracks belonging to an album. Returns count of deleted tracks."""
    cursor = await conn.execute("DELETE FROM tracks WHERE album_id = ?;", (album_id,))
    return cursor.rowcount


async def delete_tracks_by_artist_id(conn: aiosqlite.Connection, artist_id: int) -> int:
    """Delete all tracks belonging to an artist. Returns count of deleted tracks."""
    cursor = await conn.execute("DELETE FROM tracks WHERE artist_id = ?;", (artist_id,))
    return cursor.rowcount


# ---------------------------------------------------------------------------
# Filters: by album / by artist
# ---------------------------------------------------------------------------


async def list_tracks_by_album(
    conn: aiosqlite.Connection,
    album_id: int,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[TrackRow]:
    order_clause = tracks_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT * FROM tracks t
        WHERE album_id = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(album_id), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [_row_to_track(r) for r in rows]


async def list_tracks_by_artist(
    conn: aiosqlite.Connection,
    artist_id: int,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[TrackRow]:
    order_clause = tracks_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT * FROM tracks t
        WHERE artist_id = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(artist_id), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [_row_to_track(r) for r in rows]


async def count_tracks_by_album(conn: aiosqlite.Connection, album_id: int) -> int:
    cursor = await conn.execute(
        "SELECT COUNT(*) AS c FROM tracks WHERE album_id = ?;",
        (int(album_id),),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row else 0


async def count_tracks_by_artist(conn: aiosqlite.Connection, artist_id: int) -> int:
    cursor = await conn.execute(
        "SELECT COUNT(*) AS c FROM tracks WHERE artist_id = ?;",
        (int(artist_id),),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row else 0


# ---------------------------------------------------------------------------
# Filters: year
# ---------------------------------------------------------------------------


async def count_tracks_by_year(conn: aiosqlite.Connection, year: int) -> int:
    cursor = await conn.execute(
        "SELECT COUNT(*) AS c FROM tracks WHERE year = ?;",
        (int(year),),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row else 0


async def list_tracks_by_year(
    conn: aiosqlite.Connection,
    year: int,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[TrackRow]:
    order_clause = tracks_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT * FROM tracks t
        WHERE year = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(year), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [_row_to_track(r) for r in rows]


async def count_tracks_by_artist_and_year(
    conn: aiosqlite.Connection, artist_id: int, year: int
) -> int:
    cursor = await conn.execute(
        "SELECT COUNT(*) AS c FROM tracks WHERE artist_id = ? AND year = ?;",
        (int(artist_id), int(year)),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row else 0


async def list_tracks_by_artist_and_year(
    conn: aiosqlite.Connection,
    artist_id: int,
    year: int,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[TrackRow]:
    order_clause = tracks_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT * FROM tracks t
        WHERE artist_id = ? AND year = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(artist_id), int(year), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [_row_to_track(r) for r in rows]


async def count_tracks_by_album_and_year(
    conn: aiosqlite.Connection, album_id: int, year: int
) -> int:
    cursor = await conn.execute(
        "SELECT COUNT(*) AS c FROM tracks WHERE album_id = ? AND year = ?;",
        (int(album_id), int(year)),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row else 0


async def list_tracks_by_album_and_year(
    conn: aiosqlite.Connection,
    album_id: int,
    year: int,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[TrackRow]:
    order_clause = tracks_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT * FROM tracks t
        WHERE album_id = ? AND year = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(album_id), int(year), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [_row_to_track(r) for r in rows]


# ---------------------------------------------------------------------------
# Filters: compilation
# ---------------------------------------------------------------------------


async def count_tracks_by_compilation(conn: aiosqlite.Connection, compilation: int) -> int:
    cursor = await conn.execute(
        "SELECT COUNT(*) AS c FROM tracks WHERE compilation = ?;",
        (int(compilation),),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row else 0


async def list_tracks_by_compilation(
    conn: aiosqlite.Connection,
    compilation: int,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[TrackRow]:
    order_clause = tracks_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT * FROM tracks t
        WHERE compilation = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(compilation), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [_row_to_track(r) for r in rows]


async def count_tracks_by_compilation_and_year(
    conn: aiosqlite.Connection, compilation: int, year: int
) -> int:
    cursor = await conn.execute(
        "SELECT COUNT(*) AS c FROM tracks WHERE compilation = ? AND year = ?;",
        (int(compilation), int(year)),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row else 0


async def list_tracks_by_compilation_and_year(
    conn: aiosqlite.Connection,
    compilation: int,
    year: int,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[TrackRow]:
    order_clause = tracks_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT * FROM tracks t
        WHERE compilation = ? AND year = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(compilation), int(year), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [_row_to_track(r) for r in rows]


async def count_tracks_by_compilation_and_artist(
    conn: aiosqlite.Connection, compilation: int, artist_id: int
) -> int:
    cursor = await conn.execute(
        "SELECT COUNT(*) AS c FROM tracks WHERE compilation = ? AND artist_id = ?;",
        (int(compilation), int(artist_id)),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row else 0


async def list_tracks_by_compilation_and_artist(
    conn: aiosqlite.Connection,
    compilation: int,
    artist_id: int,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[TrackRow]:
    order_clause = tracks_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT * FROM tracks t
        WHERE compilation = ? AND artist_id = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(compilation), int(artist_id), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [_row_to_track(r) for r in rows]


async def count_tracks_by_compilation_artist_and_year(
    conn: aiosqlite.Connection, compilation: int, artist_id: int, year: int
) -> int:
    cursor = await conn.execute(
        "SELECT COUNT(*) AS c FROM tracks WHERE compilation = ? AND artist_id = ? AND year = ?;",
        (int(compilation), int(artist_id), int(year)),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row else 0


async def list_tracks_by_compilation_artist_and_year(
    conn: aiosqlite.Connection,
    compilation: int,
    artist_id: int,
    year: int,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[TrackRow]:
    order_clause = tracks_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT * FROM tracks t
        WHERE compilation = ? AND artist_id = ? AND year = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(compilation), int(artist_id), int(year), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [_row_to_track(r) for r in rows]


async def count_tracks_by_compilation_and_album(
    conn: aiosqlite.Connection, compilation: int, album_id: int
) -> int:
    cursor = await conn.execute(
        "SELECT COUNT(*) AS c FROM tracks WHERE compilation = ? AND album_id = ?;",
        (int(compilation), int(album_id)),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row else 0


async def list_tracks_by_compilation_and_album(
    conn: aiosqlite.Connection,
    compilation: int,
    album_id: int,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[TrackRow]:
    order_clause = tracks_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT * FROM tracks t
        WHERE compilation = ? AND album_id = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(compilation), int(album_id), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [_row_to_track(r) for r in rows]


async def count_tracks_by_compilation_album_and_year(
    conn: aiosqlite.Connection, compilation: int, album_id: int, year: int
) -> int:
    cursor = await conn.execute(
        "SELECT COUNT(*) AS c FROM tracks WHERE compilation = ? AND album_id = ? AND year = ?;",
        (int(compilation), int(album_id), int(year)),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row else 0


async def list_tracks_by_compilation_album_and_year(
    conn: aiosqlite.Connection,
    compilation: int,
    album_id: int,
    year: int,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[TrackRow]:
    order_clause = tracks_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT * FROM tracks t
        WHERE compilation = ? AND album_id = ? AND year = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(compilation), int(album_id), int(year), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [_row_to_track(r) for r in rows]


async def count_tracks_by_compilation_and_genre_id(
    conn: aiosqlite.Connection, compilation: int, genre_id: int
) -> int:
    cursor = await conn.execute(
        """
        SELECT COUNT(DISTINCT t.id) AS c
        FROM tracks t
        JOIN track_genres tg ON tg.track_id = t.id
        WHERE t.compilation = ? AND tg.genre_id = ?;
        """,
        (int(compilation), int(genre_id)),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row else 0


async def list_tracks_by_compilation_and_genre_id(
    conn: aiosqlite.Connection,
    compilation: int,
    genre_id: int,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[TrackRow]:
    order_clause = tracks_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT DISTINCT t.*
        FROM tracks t
        JOIN track_genres tg ON tg.track_id = t.id
        WHERE t.compilation = ? AND tg.genre_id = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(compilation), int(genre_id), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [_row_to_track(r) for r in rows]


# ---------------------------------------------------------------------------
# Filters: genre_id
# ---------------------------------------------------------------------------


async def count_tracks_by_genre_id(conn: aiosqlite.Connection, genre_id: int) -> int:
    cursor = await conn.execute(
        """
        SELECT COUNT(DISTINCT t.id) AS c
        FROM tracks t
        JOIN track_genres tg ON tg.track_id = t.id
        WHERE tg.genre_id = ?;
        """,
        (int(genre_id),),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row else 0


async def list_tracks_by_genre_id(
    conn: aiosqlite.Connection,
    genre_id: int,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[TrackRow]:
    order_clause = tracks_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT DISTINCT t.*
        FROM tracks t
        JOIN track_genres tg ON tg.track_id = t.id
        WHERE tg.genre_id = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(genre_id), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [_row_to_track(r) for r in rows]


async def count_tracks_by_genre_and_year(
    conn: aiosqlite.Connection, genre_id: int, year: int
) -> int:
    cursor = await conn.execute(
        """
        SELECT COUNT(DISTINCT t.id) AS c
        FROM tracks t
        JOIN track_genres tg ON tg.track_id = t.id
        WHERE tg.genre_id = ? AND t.year = ?;
        """,
        (int(genre_id), int(year)),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row else 0


async def list_tracks_by_genre_and_year(
    conn: aiosqlite.Connection,
    genre_id: int,
    year: int,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[TrackRow]:
    order_clause = tracks_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT DISTINCT t.*
        FROM tracks t
        JOIN track_genres tg ON tg.track_id = t.id
        WHERE tg.genre_id = ? AND t.year = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(genre_id), int(year), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [_row_to_track(r) for r in rows]


async def count_tracks_by_genre_and_artist(
    conn: aiosqlite.Connection, genre_id: int, artist_id: int
) -> int:
    cursor = await conn.execute(
        """
        SELECT COUNT(DISTINCT t.id) AS c
        FROM tracks t
        JOIN track_genres tg ON tg.track_id = t.id
        WHERE tg.genre_id = ? AND t.artist_id = ?;
        """,
        (int(genre_id), int(artist_id)),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row else 0


async def list_tracks_by_genre_and_artist(
    conn: aiosqlite.Connection,
    genre_id: int,
    artist_id: int,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[TrackRow]:
    order_clause = tracks_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT DISTINCT t.*
        FROM tracks t
        JOIN track_genres tg ON tg.track_id = t.id
        WHERE tg.genre_id = ? AND t.artist_id = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(genre_id), int(artist_id), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [_row_to_track(r) for r in rows]


async def count_tracks_by_genre_artist_and_year(
    conn: aiosqlite.Connection, genre_id: int, artist_id: int, year: int
) -> int:
    cursor = await conn.execute(
        """
        SELECT COUNT(DISTINCT t.id) AS c
        FROM tracks t
        JOIN track_genres tg ON tg.track_id = t.id
        WHERE tg.genre_id = ? AND t.artist_id = ? AND t.year = ?;
        """,
        (int(genre_id), int(artist_id), int(year)),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row else 0


async def list_tracks_by_genre_artist_and_year(
    conn: aiosqlite.Connection,
    genre_id: int,
    artist_id: int,
    year: int,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[TrackRow]:
    order_clause = tracks_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT DISTINCT t.*
        FROM tracks t
        JOIN track_genres tg ON tg.track_id = t.id
        WHERE tg.genre_id = ? AND t.artist_id = ? AND t.year = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(genre_id), int(artist_id), int(year), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [_row_to_track(r) for r in rows]


async def count_tracks_by_genre_and_album(
    conn: aiosqlite.Connection, genre_id: int, album_id: int
) -> int:
    cursor = await conn.execute(
        """
        SELECT COUNT(DISTINCT t.id) AS c
        FROM tracks t
        JOIN track_genres tg ON tg.track_id = t.id
        WHERE tg.genre_id = ? AND t.album_id = ?;
        """,
        (int(genre_id), int(album_id)),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row else 0


async def list_tracks_by_genre_and_album(
    conn: aiosqlite.Connection,
    genre_id: int,
    album_id: int,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[TrackRow]:
    order_clause = tracks_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT DISTINCT t.*
        FROM tracks t
        JOIN track_genres tg ON tg.track_id = t.id
        WHERE tg.genre_id = ? AND t.album_id = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(genre_id), int(album_id), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [_row_to_track(r) for r in rows]


async def count_tracks_by_genre_album_and_year(
    conn: aiosqlite.Connection, genre_id: int, album_id: int, year: int
) -> int:
    cursor = await conn.execute(
        """
        SELECT COUNT(DISTINCT t.id) AS c
        FROM tracks t
        JOIN track_genres tg ON tg.track_id = t.id
        WHERE tg.genre_id = ? AND t.album_id = ? AND t.year = ?;
        """,
        (int(genre_id), int(album_id), int(year)),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row else 0


async def list_tracks_by_genre_album_and_year(
    conn: aiosqlite.Connection,
    genre_id: int,
    album_id: int,
    year: int,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[TrackRow]:
    order_clause = tracks_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT DISTINCT t.*
        FROM tracks t
        JOIN track_genres tg ON tg.track_id = t.id
        WHERE tg.genre_id = ? AND t.album_id = ? AND t.year = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(genre_id), int(album_id), int(year), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [_row_to_track(r) for r in rows]


# ---------------------------------------------------------------------------
# Filters: role_id (contributors/roles)
# ---------------------------------------------------------------------------


async def count_tracks_by_role_id(conn: aiosqlite.Connection, role_id: int) -> int:
    cursor = await conn.execute(
        """
        SELECT COUNT(DISTINCT t.id) AS c
        FROM tracks t
        JOIN contributor_tracks ct ON ct.track_id = t.id
        WHERE ct.role_id = ?;
        """,
        (int(role_id),),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row else 0


async def list_tracks_by_role_id(
    conn: aiosqlite.Connection,
    role_id: int,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[TrackRow]:
    order_clause = tracks_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT DISTINCT t.*
        FROM tracks t
        JOIN contributor_tracks ct ON ct.track_id = t.id
        WHERE ct.role_id = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(role_id), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [_row_to_track(r) for r in rows]


async def count_tracks_by_role_and_genre_id(
    conn: aiosqlite.Connection, role_id: int, genre_id: int
) -> int:
    cursor = await conn.execute(
        """
        SELECT COUNT(DISTINCT t.id) AS c
        FROM tracks t
        JOIN contributor_tracks ct ON ct.track_id = t.id
        JOIN track_genres tg ON tg.track_id = t.id
        WHERE ct.role_id = ? AND tg.genre_id = ?;
        """,
        (int(role_id), int(genre_id)),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row else 0


async def list_tracks_by_role_and_genre_id(
    conn: aiosqlite.Connection,
    role_id: int,
    genre_id: int,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[TrackRow]:
    order_clause = tracks_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT DISTINCT t.*
        FROM tracks t
        JOIN contributor_tracks ct ON ct.track_id = t.id
        JOIN track_genres tg ON tg.track_id = t.id
        WHERE ct.role_id = ? AND tg.genre_id = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(role_id), int(genre_id), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [_row_to_track(r) for r in rows]


async def count_tracks_by_role_and_year(conn: aiosqlite.Connection, role_id: int, year: int) -> int:
    cursor = await conn.execute(
        """
        SELECT COUNT(DISTINCT t.id) AS c
        FROM tracks t
        JOIN contributor_tracks ct ON ct.track_id = t.id
        WHERE ct.role_id = ? AND t.year = ?;
        """,
        (int(role_id), int(year)),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row else 0


async def list_tracks_by_role_and_year(
    conn: aiosqlite.Connection,
    role_id: int,
    year: int,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[TrackRow]:
    order_clause = tracks_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT DISTINCT t.*
        FROM tracks t
        JOIN contributor_tracks ct ON ct.track_id = t.id
        WHERE ct.role_id = ? AND t.year = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(role_id), int(year), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [_row_to_track(r) for r in rows]


async def count_tracks_by_role_and_compilation(
    conn: aiosqlite.Connection, role_id: int, compilation: int
) -> int:
    cursor = await conn.execute(
        """
        SELECT COUNT(DISTINCT t.id) AS c
        FROM tracks t
        JOIN contributor_tracks ct ON ct.track_id = t.id
        WHERE ct.role_id = ? AND t.compilation = ?;
        """,
        (int(role_id), int(compilation)),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row else 0


async def list_tracks_by_role_and_compilation(
    conn: aiosqlite.Connection,
    role_id: int,
    compilation: int,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[TrackRow]:
    order_clause = tracks_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT DISTINCT t.*
        FROM tracks t
        JOIN contributor_tracks ct ON ct.track_id = t.id
        WHERE ct.role_id = ? AND t.compilation = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(role_id), int(compilation), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [_row_to_track(r) for r in rows]
