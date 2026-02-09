"""
Artist-related DB queries extracted from `resonance.core.library_db.LibraryDb`.

Design:
- Functions are *pure DB helpers*: they take an open `aiosqlite.Connection`
  and return rows/materialized dicts.
- Ordering is centralized via `resonance.core.db.ordering.artists_order_clause`.
- These functions assume `conn.row_factory = aiosqlite.Row`.

Important:
- Do NOT interpolate user input into SQL. Any dynamic SQL here is limited to
  ORDER BY clauses selected from a small whitelist in `artists_order_clause`.
"""

from __future__ import annotations

from typing import Any

import aiosqlite

from resonance.core.db.ordering import artists_order_clause

# ---------------------------------------------------------------------------
# Basic get/list/count
# ---------------------------------------------------------------------------


async def get_artist_by_id(conn: aiosqlite.Connection, artist_id: int) -> dict[str, Any] | None:
    """
    Return an artist row as dict: {id, name, name_sort} or None.
    """
    cursor = await conn.execute(
        """
        SELECT id, name, name_sort
        FROM artists
        WHERE id = ?
        """,
        (int(artist_id),),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return {"id": int(row["id"]), "name": row["name"], "name_sort": row["name_sort"]}


async def get_artist_by_name(conn: aiosqlite.Connection, name: str) -> dict[str, Any] | None:
    """
    Return an artist row as dict: {id, name, name_sort} or None.
    """
    cursor = await conn.execute(
        """
        SELECT id, name, name_sort
        FROM artists
        WHERE name = ?
        """,
        (name,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return {"id": int(row["id"]), "name": row["name"], "name_sort": row["name_sort"]}


async def list_all_artists(
    conn: aiosqlite.Connection,
    *,
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    """
    List all artists as dicts: {id, name, name_sort}.
    """
    cursor = await conn.execute(
        """
        SELECT id, name, name_sort
        FROM artists
        ORDER BY name COLLATE NOCASE ASC, id ASC
        LIMIT ? OFFSET ?;
        """,
        (int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [{"id": int(r["id"]), "name": r["name"], "name_sort": r["name_sort"]} for r in rows]


async def count_artists(conn: aiosqlite.Connection) -> int:
    cursor = await conn.execute("SELECT COUNT(*) AS c FROM artists;")
    row = await cursor.fetchone()
    return int(row["c"]) if row is not None else 0


async def get_artist_album_count(conn: aiosqlite.Connection, artist_id: int) -> int:
    cursor = await conn.execute(
        "SELECT COUNT(*) AS c FROM albums WHERE artist_id = ?;",
        (int(artist_id),),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row is not None else 0


async def get_artist_with_album_count(
    conn: aiosqlite.Connection, artist_id: int
) -> dict[str, Any] | None:
    """
    Return one artist as dict: {id, name, album_count}.
    """
    cursor = await conn.execute(
        """
        SELECT
            ar.id,
            ar.name,
            (SELECT COUNT(DISTINCT al.id) FROM albums al WHERE al.artist_id = ar.id) AS album_count
        FROM artists ar
        WHERE ar.id = ?
        """,
        (int(artist_id),),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return {"id": int(row["id"]), "name": row["name"], "album_count": int(row["album_count"])}


async def list_artists_with_album_counts(
    conn: aiosqlite.Connection,
    *,
    offset: int,
    limit: int,
    order_by: str,
) -> list[dict[str, Any]]:
    """
    List artists with album counts.

    Returns list of dicts with: id, name, album_count
    """
    order_clause = artists_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT
            ar.id,
            ar.name,
            (SELECT COUNT(DISTINCT al.id) FROM albums al WHERE al.artist_id = ar.id) AS album_count
        FROM artists ar
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [
        {"id": int(r["id"]), "name": r["name"], "album_count": int(r["album_count"])} for r in rows
    ]


# ---------------------------------------------------------------------------
# Filters: compilation / year / genre
# ---------------------------------------------------------------------------


async def count_artists_by_compilation(conn: aiosqlite.Connection, compilation: int) -> int:
    cursor = await conn.execute(
        """
        SELECT COUNT(DISTINCT ar.id) AS c
        FROM artists ar
        JOIN tracks t ON t.artist_id = ar.id
        WHERE t.compilation = ?
        """,
        (int(compilation),),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row is not None else 0


async def list_artists_with_album_counts_by_compilation(
    conn: aiosqlite.Connection,
    compilation: int,
    *,
    offset: int,
    limit: int,
    order_by: str,
) -> list[dict[str, Any]]:
    order_clause = artists_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT DISTINCT
            ar.id,
            ar.name,
            (SELECT COUNT(DISTINCT al.id) FROM albums al WHERE al.artist_id = ar.id) AS album_count
        FROM artists ar
        JOIN tracks t ON t.artist_id = ar.id
        WHERE t.compilation = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(compilation), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [
        {"id": int(r["id"]), "name": r["name"], "album_count": int(r["album_count"])} for r in rows
    ]


async def count_artists_by_year(conn: aiosqlite.Connection, year: int) -> int:
    cursor = await conn.execute(
        """
        SELECT COUNT(DISTINCT ar.id) AS c
        FROM artists ar
        JOIN tracks t ON t.artist_id = ar.id
        WHERE t.year = ?
        """,
        (int(year),),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row is not None else 0


async def list_artists_with_album_counts_by_year(
    conn: aiosqlite.Connection,
    year: int,
    *,
    offset: int,
    limit: int,
    order_by: str,
) -> list[dict[str, Any]]:
    order_clause = artists_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT DISTINCT
            ar.id,
            ar.name,
            (SELECT COUNT(DISTINCT al.id) FROM albums al WHERE al.artist_id = ar.id) AS album_count
        FROM artists ar
        JOIN tracks t ON t.artist_id = ar.id
        WHERE t.year = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(year), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [
        {"id": int(r["id"]), "name": r["name"], "album_count": int(r["album_count"])} for r in rows
    ]


async def count_artists_by_genre_id(conn: aiosqlite.Connection, genre_id: int) -> int:
    cursor = await conn.execute(
        """
        SELECT COUNT(DISTINCT ar.id) AS c
        FROM artists ar
        JOIN tracks t ON t.artist_id = ar.id
        JOIN track_genres tg ON tg.track_id = t.id
        WHERE tg.genre_id = ?
        """,
        (int(genre_id),),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row is not None else 0


async def list_artists_by_genre_id(
    conn: aiosqlite.Connection,
    genre_id: int,
    *,
    offset: int,
    limit: int,
    order_by: str,
) -> list[dict[str, Any]]:
    """
    List artists having at least one track in the given genre.

    Returns list of dicts with: id, name, album_count
    """
    order_clause = artists_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT DISTINCT
            ar.id,
            ar.name,
            (SELECT COUNT(DISTINCT al.id) FROM albums al WHERE al.artist_id = ar.id) AS album_count
        FROM artists ar
        JOIN tracks t ON t.artist_id = ar.id
        JOIN track_genres tg ON tg.track_id = t.id
        WHERE tg.genre_id = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(genre_id), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [
        {"id": int(r["id"]), "name": r["name"], "album_count": int(r["album_count"])} for r in rows
    ]


async def count_artists_by_genre_and_year(
    conn: aiosqlite.Connection, genre_id: int, year: int
) -> int:
    cursor = await conn.execute(
        """
        SELECT COUNT(DISTINCT ar.id) AS c
        FROM artists ar
        JOIN tracks t ON t.artist_id = ar.id
        JOIN track_genres tg ON tg.track_id = t.id
        WHERE tg.genre_id = ? AND t.year = ?
        """,
        (int(genre_id), int(year)),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row is not None else 0


async def list_artists_by_genre_and_year(
    conn: aiosqlite.Connection,
    genre_id: int,
    year: int,
    *,
    offset: int,
    limit: int,
    order_by: str,
) -> list[dict[str, Any]]:
    """
    List artists in a genre restricted to a specific year.
    """
    order_clause = artists_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT DISTINCT
            ar.id,
            ar.name,
            (SELECT COUNT(DISTINCT al.id) FROM albums al WHERE al.artist_id = ar.id) AS album_count
        FROM artists ar
        JOIN tracks t ON t.artist_id = ar.id
        JOIN track_genres tg ON tg.track_id = t.id
        WHERE tg.genre_id = ? AND t.year = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(genre_id), int(year), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [
        {"id": int(r["id"]), "name": r["name"], "album_count": int(r["album_count"])} for r in rows
    ]


# ---------------------------------------------------------------------------
# Filters: role_id (contributors/roles) and AND-combinations
# ---------------------------------------------------------------------------


async def list_artists_with_album_counts_by_role_id(
    conn: aiosqlite.Connection,
    role_id: int,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[dict[str, Any]]:
    order_clause = artists_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT DISTINCT
            ar.id,
            ar.name,
            (SELECT COUNT(DISTINCT al.id) FROM albums al WHERE al.artist_id = ar.id) AS album_count
        FROM artists ar
        JOIN tracks t ON t.artist_id = ar.id
        JOIN contributor_tracks ct ON ct.track_id = t.id
        WHERE ct.role_id = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(role_id), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [
        {"id": int(r["id"]), "name": r["name"], "album_count": int(r["album_count"])} for r in rows
    ]


async def count_artists_by_role_id(conn: aiosqlite.Connection, role_id: int) -> int:
    cursor = await conn.execute(
        """
        SELECT COUNT(DISTINCT ar.id) AS c
        FROM artists ar
        JOIN tracks t ON t.artist_id = ar.id
        JOIN contributor_tracks ct ON ct.track_id = t.id
        WHERE ct.role_id = ?
        """,
        (int(role_id),),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row is not None else 0


async def list_artists_with_album_counts_by_role_and_genre_id(
    conn: aiosqlite.Connection,
    role_id: int,
    genre_id: int,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[dict[str, Any]]:
    order_clause = artists_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT DISTINCT
            ar.id,
            ar.name,
            (SELECT COUNT(DISTINCT al.id) FROM albums al WHERE al.artist_id = ar.id) AS album_count
        FROM artists ar
        JOIN tracks t ON t.artist_id = ar.id
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
        {"id": int(r["id"]), "name": r["name"], "album_count": int(r["album_count"])} for r in rows
    ]


async def count_artists_by_role_and_genre_id(
    conn: aiosqlite.Connection, role_id: int, genre_id: int
) -> int:
    cursor = await conn.execute(
        """
        SELECT COUNT(DISTINCT ar.id) AS c
        FROM artists ar
        JOIN tracks t ON t.artist_id = ar.id
        JOIN contributor_tracks ct ON ct.track_id = t.id
        JOIN track_genres tg ON tg.track_id = t.id
        WHERE ct.role_id = ? AND tg.genre_id = ?
        """,
        (int(role_id), int(genre_id)),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row is not None else 0


async def list_artists_with_album_counts_by_role_and_year(
    conn: aiosqlite.Connection,
    role_id: int,
    year: int,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[dict[str, Any]]:
    order_clause = artists_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT DISTINCT
            ar.id,
            ar.name,
            (SELECT COUNT(DISTINCT al.id) FROM albums al WHERE al.artist_id = ar.id) AS album_count
        FROM artists ar
        JOIN tracks t ON t.artist_id = ar.id
        JOIN contributor_tracks ct ON ct.track_id = t.id
        WHERE ct.role_id = ? AND t.year = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(role_id), int(year), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [
        {"id": int(r["id"]), "name": r["name"], "album_count": int(r["album_count"])} for r in rows
    ]


async def count_artists_by_role_and_year(
    conn: aiosqlite.Connection, role_id: int, year: int
) -> int:
    cursor = await conn.execute(
        """
        SELECT COUNT(DISTINCT ar.id) AS c
        FROM artists ar
        JOIN tracks t ON t.artist_id = ar.id
        JOIN contributor_tracks ct ON ct.track_id = t.id
        WHERE ct.role_id = ? AND t.year = ?
        """,
        (int(role_id), int(year)),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row is not None else 0


async def list_artists_with_album_counts_by_role_and_compilation(
    conn: aiosqlite.Connection,
    role_id: int,
    compilation: int,
    *,
    limit: int,
    offset: int,
    order_by: str,
) -> list[dict[str, Any]]:
    order_clause = artists_order_clause(order_by)
    cursor = await conn.execute(
        f"""
        SELECT DISTINCT
            ar.id,
            ar.name,
            (SELECT COUNT(DISTINCT al.id) FROM albums al WHERE al.artist_id = ar.id) AS album_count
        FROM artists ar
        JOIN tracks t ON t.artist_id = ar.id
        JOIN contributor_tracks ct ON ct.track_id = t.id
        WHERE ct.role_id = ? AND t.compilation = ?
        {order_clause}
        LIMIT ? OFFSET ?;
        """,
        (int(role_id), int(compilation), int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [
        {"id": int(r["id"]), "name": r["name"], "album_count": int(r["album_count"])} for r in rows
    ]


async def count_artists_by_role_and_compilation(
    conn: aiosqlite.Connection, role_id: int, compilation: int
) -> int:
    cursor = await conn.execute(
        """
        SELECT COUNT(DISTINCT ar.id) AS c
        FROM artists ar
        JOIN tracks t ON t.artist_id = ar.id
        JOIN contributor_tracks ct ON ct.track_id = t.id
        WHERE ct.role_id = ? AND t.compilation = ?
        """,
        (int(role_id), int(compilation)),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row is not None else 0
