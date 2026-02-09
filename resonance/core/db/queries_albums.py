"""
Album-related DB queries extracted from `resonance.core.library_db.LibraryDb`.

Design:
- Functions are *pure DB helpers*: they take an open `aiosqlite.Connection`
  and return rows/materialized dicts.
- Ordering is centralized via `resonance.core.db.ordering.albums_order_clause`.
- These functions assume `conn.row_factory = aiosqlite.Row`.

Important:
- Do NOT interpolate user input into SQL. Any dynamic SQL here is limited to
  ORDER BY clauses selected from a small whitelist in `albums_order_clause`.
"""

from __future__ import annotations

from typing import Any

import aiosqlite

from resonance.core.db.models import AlbumRow
from resonance.core.db.ordering import albums_order_clause


def _row_to_album(row: aiosqlite.Row) -> AlbumRow:
    """Convert an aiosqlite Row to an AlbumRow dataclass."""
    return AlbumRow(
        id=int(row["id"]),
        title=str(row["title"]),
        title_sort=row["title_sort"],
        artist_id=row["artist_id"],
        artist_name=row["artist_name"],
        year=row["year"],
    )


# ---------------------------------------------------------------------------
# Basic get/list/count
# ---------------------------------------------------------------------------


async def get_album_by_id(conn: aiosqlite.Connection, album_id: int) -> AlbumRow | None:
    cursor = await conn.execute(
        """
        SELECT
            a.id, a.title, a.title_sort, a.artist_id, ar.name AS artist_name, a.year
        FROM albums a
        LEFT JOIN artists ar ON a.artist_id = ar.id
        WHERE a.id = ?;
        """,
        (int(album_id),),
    )
    row = await cursor.fetchone()
    return _row_to_album(row) if row else None


async def list_all_albums(
    conn: aiosqlite.Connection,
    *,
    limit: int,
    offset: int,
) -> list[AlbumRow]:
    cursor = await conn.execute(
        """
        SELECT
            a.id, a.title, a.title_sort, a.artist_id, ar.name AS artist_name, a.year
        FROM albums a
        LEFT JOIN artists ar ON a.artist_id = ar.id
        ORDER BY a.title COLLATE NOCASE
        LIMIT ? OFFSET ?;
        """,
        (int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [_row_to_album(r) for r in rows]


async def list_albums_by_artist(
    conn: aiosqlite.Connection,
    artist_id: int,
    *,
    limit: int,
    offset: int,
) -> list[AlbumRow]:
    cursor = await conn.execute(
        """
        SELECT
            a.id, a.title, a.title_sort, a.artist_id, ar.name AS artist_name, a.year
        FROM albums a
        LEFT JOIN artists ar ON a.artist_id = ar.id
        WHERE a.artist_id = ?
        ORDER BY a.title COLLATE NOCASE
        LIMIT ? OFFSET ?;
        """,
        (int(artist_id), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [_row_to_album(r) for r in rows]


async def count_albums(conn: aiosqlite.Connection) -> int:
    cursor = await conn.execute("SELECT COUNT(*) AS c FROM albums;")
    row = await cursor.fetchone()
    return int(row["c"]) if row else 0


async def count_albums_by_artist(conn: aiosqlite.Connection, artist_id: int) -> int:
    cursor = await conn.execute(
        "SELECT COUNT(*) AS c FROM albums WHERE artist_id = ?;",
        (int(artist_id),),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row else 0


async def get_album_track_count(conn: aiosqlite.Connection, album_id: int) -> int:
    cursor = await conn.execute(
        "SELECT COUNT(*) AS c FROM tracks WHERE album_id = ?;",
        (int(album_id),),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row else 0


async def get_album_with_track_count(
    conn: aiosqlite.Connection, album_id: int
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        SELECT
            a.id,
            a.title,
            a.artist_id,
            ar.name AS artist_name,
            a.year,
            (SELECT COUNT(*) FROM tracks t WHERE t.album_id = a.id) AS track_count
        FROM albums a
        LEFT JOIN artists ar ON a.artist_id = ar.id
        WHERE a.id = ?;
        """,
        (int(album_id),),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return {
        "id": int(row["id"]),
        "name": row["title"],
        "artist": row["artist_name"],
        "artist_id": row["artist_id"],
        "year": row["year"],
        "track_count": int(row["track_count"]),
    }


async def list_albums_with_track_counts(
    conn: aiosqlite.Connection,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[dict[str, Any]]:
    order_clause = albums_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT
            a.id,
            a.title,
            a.artist_id,
            ar.name AS artist_name,
            a.year,
            (SELECT COUNT(*) FROM tracks t WHERE t.album_id = a.id) AS track_count
        FROM albums a
        LEFT JOIN artists ar ON a.artist_id = ar.id
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [
        {
            "id": int(r["id"]),
            "name": r["title"],
            "artist": r["artist_name"],
            "artist_id": r["artist_id"],
            "year": r["year"],
            "track_count": int(r["track_count"]),
        }
        for r in rows
    ]


async def list_albums_with_track_counts_by_artist(
    conn: aiosqlite.Connection,
    artist_id: int,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[dict[str, Any]]:
    order_clause = albums_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT
            a.id,
            a.title,
            a.artist_id,
            ar.name AS artist_name,
            a.year,
            (SELECT COUNT(*) FROM tracks t WHERE t.album_id = a.id) AS track_count
        FROM albums a
        LEFT JOIN artists ar ON a.artist_id = ar.id
        WHERE a.artist_id = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(artist_id), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [
        {
            "id": int(r["id"]),
            "name": r["title"],
            "artist": r["artist_name"],
            "artist_id": r["artist_id"],
            "year": r["year"],
            "track_count": int(r["track_count"]),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Filters: year
# ---------------------------------------------------------------------------


async def count_albums_by_year(conn: aiosqlite.Connection, year: int) -> int:
    cursor = await conn.execute(
        "SELECT COUNT(*) AS c FROM albums WHERE year = ?;",
        (int(year),),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row else 0


async def list_albums_with_track_counts_by_year(
    conn: aiosqlite.Connection,
    year: int,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[dict[str, Any]]:
    order_clause = albums_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT
            a.id,
            a.title,
            a.artist_id,
            ar.name AS artist_name,
            a.year,
            (SELECT COUNT(*) FROM tracks t WHERE t.album_id = a.id) AS track_count
        FROM albums a
        LEFT JOIN artists ar ON a.artist_id = ar.id
        WHERE a.year = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(year), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [
        {
            "id": int(r["id"]),
            "name": r["title"],
            "artist": r["artist_name"],
            "artist_id": r["artist_id"],
            "year": r["year"],
            "track_count": int(r["track_count"]),
        }
        for r in rows
    ]


async def count_albums_by_artist_and_year(
    conn: aiosqlite.Connection, artist_id: int, year: int
) -> int:
    cursor = await conn.execute(
        "SELECT COUNT(*) AS c FROM albums WHERE artist_id = ? AND year = ?;",
        (int(artist_id), int(year)),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row else 0


async def list_albums_with_track_counts_by_artist_and_year(
    conn: aiosqlite.Connection,
    artist_id: int,
    year: int,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[dict[str, Any]]:
    order_clause = albums_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT
            a.id,
            a.title,
            a.artist_id,
            ar.name AS artist_name,
            a.year,
            (SELECT COUNT(*) FROM tracks t WHERE t.album_id = a.id) AS track_count
        FROM albums a
        LEFT JOIN artists ar ON a.artist_id = ar.id
        WHERE a.artist_id = ? AND a.year = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(artist_id), int(year), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [
        {
            "id": int(r["id"]),
            "name": r["title"],
            "artist": r["artist_name"],
            "artist_id": r["artist_id"],
            "year": r["year"],
            "track_count": int(r["track_count"]),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Filters: compilation
# ---------------------------------------------------------------------------


async def count_albums_by_compilation(conn: aiosqlite.Connection, compilation: int) -> int:
    cursor = await conn.execute(
        "SELECT COUNT(*) AS c FROM albums WHERE compilation = ?;",
        (int(compilation),),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row else 0


async def list_albums_with_track_counts_by_compilation(
    conn: aiosqlite.Connection,
    compilation: int,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[dict[str, Any]]:
    order_clause = albums_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT
            a.id,
            a.title,
            a.artist_id,
            ar.name AS artist_name,
            a.year,
            (SELECT COUNT(*) FROM tracks t WHERE t.album_id = a.id) AS track_count
        FROM albums a
        LEFT JOIN artists ar ON a.artist_id = ar.id
        WHERE a.compilation = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(compilation), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [
        {
            "id": int(r["id"]),
            "name": r["title"],
            "artist": r["artist_name"],
            "artist_id": r["artist_id"],
            "year": r["year"],
            "track_count": int(r["track_count"]),
        }
        for r in rows
    ]


async def count_albums_by_compilation_and_year(
    conn: aiosqlite.Connection, compilation: int, year: int
) -> int:
    cursor = await conn.execute(
        "SELECT COUNT(*) AS c FROM albums WHERE compilation = ? AND year = ?;",
        (int(compilation), int(year)),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row else 0


async def list_albums_with_track_counts_by_compilation_and_year(
    conn: aiosqlite.Connection,
    compilation: int,
    year: int,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[dict[str, Any]]:
    order_clause = albums_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT
            a.id,
            a.title,
            a.artist_id,
            ar.name AS artist_name,
            a.year,
            (SELECT COUNT(*) FROM tracks t WHERE t.album_id = a.id) AS track_count
        FROM albums a
        LEFT JOIN artists ar ON a.artist_id = ar.id
        WHERE a.compilation = ? AND a.year = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(compilation), int(year), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [
        {
            "id": int(r["id"]),
            "name": r["title"],
            "artist": r["artist_name"],
            "artist_id": r["artist_id"],
            "year": r["year"],
            "track_count": int(r["track_count"]),
        }
        for r in rows
    ]


async def count_albums_by_compilation_and_artist(
    conn: aiosqlite.Connection, compilation: int, artist_id: int
) -> int:
    cursor = await conn.execute(
        "SELECT COUNT(*) AS c FROM albums WHERE compilation = ? AND artist_id = ?;",
        (int(compilation), int(artist_id)),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row else 0


async def list_albums_with_track_counts_by_compilation_and_artist(
    conn: aiosqlite.Connection,
    compilation: int,
    artist_id: int,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[dict[str, Any]]:
    order_clause = albums_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT
            a.id,
            a.title,
            a.artist_id,
            ar.name AS artist_name,
            a.year,
            (SELECT COUNT(*) FROM tracks t WHERE t.album_id = a.id) AS track_count
        FROM albums a
        LEFT JOIN artists ar ON a.artist_id = ar.id
        WHERE a.compilation = ? AND a.artist_id = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(compilation), int(artist_id), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [
        {
            "id": int(r["id"]),
            "name": r["title"],
            "artist": r["artist_name"],
            "artist_id": r["artist_id"],
            "year": r["year"],
            "track_count": int(r["track_count"]),
        }
        for r in rows
    ]


async def count_albums_by_compilation_artist_and_year(
    conn: aiosqlite.Connection, compilation: int, artist_id: int, year: int
) -> int:
    cursor = await conn.execute(
        "SELECT COUNT(*) AS c FROM albums WHERE compilation = ? AND artist_id = ? AND year = ?;",
        (int(compilation), int(artist_id), int(year)),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row else 0


async def list_albums_with_track_counts_by_compilation_artist_and_year(
    conn: aiosqlite.Connection,
    compilation: int,
    artist_id: int,
    year: int,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[dict[str, Any]]:
    order_clause = albums_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT
            a.id,
            a.title,
            a.artist_id,
            ar.name AS artist_name,
            a.year,
            (SELECT COUNT(*) FROM tracks t WHERE t.album_id = a.id) AS track_count
        FROM albums a
        LEFT JOIN artists ar ON a.artist_id = ar.id
        WHERE a.compilation = ? AND a.artist_id = ? AND a.year = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(compilation), int(artist_id), int(year), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [
        {
            "id": int(r["id"]),
            "name": r["title"],
            "artist": r["artist_name"],
            "artist_id": r["artist_id"],
            "year": r["year"],
            "track_count": int(r["track_count"]),
        }
        for r in rows
    ]


async def count_albums_by_compilation_and_genre_id(
    conn: aiosqlite.Connection, compilation: int, genre_id: int
) -> int:
    cursor = await conn.execute(
        """
        SELECT COUNT(DISTINCT a.id) AS c
        FROM albums a
        JOIN tracks t ON t.album_id = a.id
        JOIN track_genres tg ON tg.track_id = t.id
        WHERE a.compilation = ? AND tg.genre_id = ?;
        """,
        (int(compilation), int(genre_id)),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row else 0


async def list_albums_with_track_counts_by_compilation_and_genre_id(
    conn: aiosqlite.Connection,
    compilation: int,
    genre_id: int,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[dict[str, Any]]:
    order_clause = albums_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT DISTINCT
            a.id,
            a.title,
            a.artist_id,
            ar.name AS artist_name,
            a.year,
            (SELECT COUNT(*) FROM tracks t2 WHERE t2.album_id = a.id) AS track_count
        FROM albums a
        LEFT JOIN artists ar ON a.artist_id = ar.id
        JOIN tracks t ON t.album_id = a.id
        JOIN track_genres tg ON tg.track_id = t.id
        WHERE a.compilation = ? AND tg.genre_id = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(compilation), int(genre_id), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [
        {
            "id": int(r["id"]),
            "name": r["title"],
            "artist": r["artist_name"],
            "artist_id": r["artist_id"],
            "year": r["year"],
            "track_count": int(r["track_count"]),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Filters: genre_id
# ---------------------------------------------------------------------------


async def count_albums_by_genre_id(conn: aiosqlite.Connection, genre_id: int) -> int:
    cursor = await conn.execute(
        """
        SELECT COUNT(DISTINCT a.id) AS c
        FROM albums a
        JOIN tracks t ON t.album_id = a.id
        JOIN track_genres tg ON tg.track_id = t.id
        WHERE tg.genre_id = ?;
        """,
        (int(genre_id),),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row else 0


async def list_albums_by_genre_id(
    conn: aiosqlite.Connection,
    genre_id: int,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[dict[str, Any]]:
    order_clause = albums_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT DISTINCT
            a.id,
            a.title,
            a.artist_id,
            ar.name AS artist_name,
            a.year,
            (SELECT COUNT(*) FROM tracks t2 WHERE t2.album_id = a.id) AS track_count
        FROM albums a
        LEFT JOIN artists ar ON a.artist_id = ar.id
        JOIN tracks t ON t.album_id = a.id
        JOIN track_genres tg ON tg.track_id = t.id
        WHERE tg.genre_id = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(genre_id), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [
        {
            "id": int(r["id"]),
            "name": r["title"],
            "artist": r["artist_name"],
            "artist_id": r["artist_id"],
            "year": r["year"],
            "track_count": int(r["track_count"]),
        }
        for r in rows
    ]


async def count_albums_by_genre_and_year(
    conn: aiosqlite.Connection, genre_id: int, year: int
) -> int:
    cursor = await conn.execute(
        """
        SELECT COUNT(DISTINCT a.id) AS c
        FROM albums a
        JOIN tracks t ON t.album_id = a.id
        JOIN track_genres tg ON tg.track_id = t.id
        WHERE tg.genre_id = ? AND a.year = ?;
        """,
        (int(genre_id), int(year)),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row else 0


async def list_albums_by_genre_and_year(
    conn: aiosqlite.Connection,
    genre_id: int,
    year: int,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[dict[str, Any]]:
    order_clause = albums_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT DISTINCT
            a.id,
            a.title,
            a.artist_id,
            ar.name AS artist_name,
            a.year,
            (SELECT COUNT(*) FROM tracks t2 WHERE t2.album_id = a.id) AS track_count
        FROM albums a
        LEFT JOIN artists ar ON a.artist_id = ar.id
        JOIN tracks t ON t.album_id = a.id
        JOIN track_genres tg ON tg.track_id = t.id
        WHERE tg.genre_id = ? AND a.year = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(genre_id), int(year), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [
        {
            "id": int(r["id"]),
            "name": r["title"],
            "artist": r["artist_name"],
            "artist_id": r["artist_id"],
            "year": r["year"],
            "track_count": int(r["track_count"]),
        }
        for r in rows
    ]


async def count_albums_by_genre_and_artist(
    conn: aiosqlite.Connection, genre_id: int, artist_id: int
) -> int:
    cursor = await conn.execute(
        """
        SELECT COUNT(DISTINCT a.id) AS c
        FROM albums a
        JOIN tracks t ON t.album_id = a.id
        JOIN track_genres tg ON tg.track_id = t.id
        WHERE tg.genre_id = ? AND a.artist_id = ?;
        """,
        (int(genre_id), int(artist_id)),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row else 0


async def list_albums_by_genre_and_artist(
    conn: aiosqlite.Connection,
    genre_id: int,
    artist_id: int,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[dict[str, Any]]:
    order_clause = albums_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT DISTINCT
            a.id,
            a.title,
            a.artist_id,
            ar.name AS artist_name,
            a.year,
            (SELECT COUNT(*) FROM tracks t2 WHERE t2.album_id = a.id) AS track_count
        FROM albums a
        LEFT JOIN artists ar ON a.artist_id = ar.id
        JOIN tracks t ON t.album_id = a.id
        JOIN track_genres tg ON tg.track_id = t.id
        WHERE tg.genre_id = ? AND a.artist_id = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(genre_id), int(artist_id), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [
        {
            "id": int(r["id"]),
            "name": r["title"],
            "artist": r["artist_name"],
            "artist_id": r["artist_id"],
            "year": r["year"],
            "track_count": int(r["track_count"]),
        }
        for r in rows
    ]


async def count_albums_by_genre_artist_and_year(
    conn: aiosqlite.Connection, genre_id: int, artist_id: int, year: int
) -> int:
    cursor = await conn.execute(
        """
        SELECT COUNT(DISTINCT a.id) AS c
        FROM albums a
        JOIN tracks t ON t.album_id = a.id
        JOIN track_genres tg ON tg.track_id = t.id
        WHERE tg.genre_id = ? AND a.artist_id = ? AND a.year = ?;
        """,
        (int(genre_id), int(artist_id), int(year)),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row else 0


async def list_albums_by_genre_artist_and_year(
    conn: aiosqlite.Connection,
    genre_id: int,
    artist_id: int,
    year: int,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[dict[str, Any]]:
    order_clause = albums_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT DISTINCT
            a.id,
            a.title,
            a.artist_id,
            ar.name AS artist_name,
            a.year,
            (SELECT COUNT(*) FROM tracks t2 WHERE t2.album_id = a.id) AS track_count
        FROM albums a
        LEFT JOIN artists ar ON a.artist_id = ar.id
        JOIN tracks t ON t.album_id = a.id
        JOIN track_genres tg ON tg.track_id = t.id
        WHERE tg.genre_id = ? AND a.artist_id = ? AND a.year = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(genre_id), int(artist_id), int(year), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [
        {
            "id": int(r["id"]),
            "name": r["title"],
            "artist": r["artist_name"],
            "artist_id": r["artist_id"],
            "year": r["year"],
            "track_count": int(r["track_count"]),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Filters: role_id (contributors/roles)
# ---------------------------------------------------------------------------


async def count_albums_by_role_id(conn: aiosqlite.Connection, role_id: int) -> int:
    cursor = await conn.execute(
        """
        SELECT COUNT(DISTINCT a.id) AS c
        FROM albums a
        JOIN tracks t ON t.album_id = a.id
        JOIN contributor_tracks ct ON ct.track_id = t.id
        WHERE ct.role_id = ?;
        """,
        (int(role_id),),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row else 0


async def list_albums_with_track_counts_by_role_id(
    conn: aiosqlite.Connection,
    role_id: int,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[dict[str, Any]]:
    order_clause = albums_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT DISTINCT
            a.id,
            a.title,
            a.artist_id,
            ar.name AS artist_name,
            a.year,
            (SELECT COUNT(*) FROM tracks t2 WHERE t2.album_id = a.id) AS track_count
        FROM albums a
        LEFT JOIN artists ar ON a.artist_id = ar.id
        JOIN tracks t ON t.album_id = a.id
        JOIN contributor_tracks ct ON ct.track_id = t.id
        WHERE ct.role_id = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(role_id), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [
        {
            "id": int(r["id"]),
            "name": r["title"],
            "artist": r["artist_name"],
            "artist_id": r["artist_id"],
            "year": r["year"],
            "track_count": int(r["track_count"]),
        }
        for r in rows
    ]


async def count_albums_by_role_and_genre_id(
    conn: aiosqlite.Connection, role_id: int, genre_id: int
) -> int:
    cursor = await conn.execute(
        """
        SELECT COUNT(DISTINCT a.id) AS c
        FROM albums a
        JOIN tracks t ON t.album_id = a.id
        JOIN contributor_tracks ct ON ct.track_id = t.id
        JOIN track_genres tg ON tg.track_id = t.id
        WHERE ct.role_id = ? AND tg.genre_id = ?;
        """,
        (int(role_id), int(genre_id)),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row else 0


async def list_albums_with_track_counts_by_role_and_genre_id(
    conn: aiosqlite.Connection,
    role_id: int,
    genre_id: int,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[dict[str, Any]]:
    order_clause = albums_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT DISTINCT
            a.id,
            a.title,
            a.artist_id,
            ar.name AS artist_name,
            a.year,
            (SELECT COUNT(*) FROM tracks t2 WHERE t2.album_id = a.id) AS track_count
        FROM albums a
        LEFT JOIN artists ar ON a.artist_id = ar.id
        JOIN tracks t ON t.album_id = a.id
        JOIN contributor_tracks ct ON ct.track_id = t.id
        JOIN track_genres tg ON tg.track_id = t.id
        WHERE ct.role_id = ? AND tg.genre_id = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(role_id), int(genre_id), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [
        {
            "id": int(r["id"]),
            "name": r["title"],
            "artist": r["artist_name"],
            "artist_id": r["artist_id"],
            "year": r["year"],
            "track_count": int(r["track_count"]),
        }
        for r in rows
    ]


async def count_albums_by_role_and_year(conn: aiosqlite.Connection, role_id: int, year: int) -> int:
    cursor = await conn.execute(
        """
        SELECT COUNT(DISTINCT a.id) AS c
        FROM albums a
        JOIN tracks t ON t.album_id = a.id
        JOIN contributor_tracks ct ON ct.track_id = t.id
        WHERE ct.role_id = ? AND a.year = ?;
        """,
        (int(role_id), int(year)),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row else 0


async def list_albums_with_track_counts_by_role_and_year(
    conn: aiosqlite.Connection,
    role_id: int,
    year: int,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[dict[str, Any]]:
    order_clause = albums_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT DISTINCT
            a.id,
            a.title,
            a.artist_id,
            ar.name AS artist_name,
            a.year,
            (SELECT COUNT(*) FROM tracks t2 WHERE t2.album_id = a.id) AS track_count
        FROM albums a
        LEFT JOIN artists ar ON a.artist_id = ar.id
        JOIN tracks t ON t.album_id = a.id
        JOIN contributor_tracks ct ON ct.track_id = t.id
        WHERE ct.role_id = ? AND a.year = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(role_id), int(year), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [
        {
            "id": int(r["id"]),
            "name": r["title"],
            "artist": r["artist_name"],
            "artist_id": r["artist_id"],
            "year": r["year"],
            "track_count": int(r["track_count"]),
        }
        for r in rows
    ]


async def count_albums_by_role_and_compilation(
    conn: aiosqlite.Connection, role_id: int, compilation: int
) -> int:
    cursor = await conn.execute(
        """
        SELECT COUNT(DISTINCT a.id) AS c
        FROM albums a
        JOIN tracks t ON t.album_id = a.id
        JOIN contributor_tracks ct ON ct.track_id = t.id
        WHERE ct.role_id = ? AND a.compilation = ?;
        """,
        (int(role_id), int(compilation)),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row else 0


async def list_albums_with_track_counts_by_role_and_compilation(
    conn: aiosqlite.Connection,
    role_id: int,
    compilation: int,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[dict[str, Any]]:
    order_clause = albums_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT DISTINCT
            a.id,
            a.title,
            a.artist_id,
            ar.name AS artist_name,
            a.year,
            (SELECT COUNT(*) FROM tracks t2 WHERE t2.album_id = a.id) AS track_count
        FROM albums a
        LEFT JOIN artists ar ON a.artist_id = ar.id
        JOIN tracks t ON t.album_id = a.id
        JOIN contributor_tracks ct ON ct.track_id = t.id
        WHERE ct.role_id = ? AND a.compilation = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(role_id), int(compilation), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [
        {
            "id": int(r["id"]),
            "name": r["title"],
            "artist": r["artist_name"],
            "artist_id": r["artist_id"],
            "year": r["year"],
            "track_count": int(r["track_count"]),
        }
        for r in rows
    ]
