"""
Meta-related DB queries extracted from `resonance.core.library_db.LibraryDb`.

This module contains queries for:
- Genres
- Roles
- Music folders

Design:
- Functions are *pure DB helpers*: they take an open `aiosqlite.Connection`
  and return rows/materialized dicts.
- These functions assume `conn.row_factory = aiosqlite.Row`.

Important:
- Do NOT interpolate user input into SQL.
"""

from __future__ import annotations

from typing import Any

import aiosqlite

# ---------------------------------------------------------------------------
# Genres
# ---------------------------------------------------------------------------


async def list_genres(
    conn: aiosqlite.Connection,
    *,
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        SELECT
            g.id,
            g.name,
            (SELECT COUNT(*) FROM track_genres tg WHERE tg.genre_id = g.id) AS track_count
        FROM genres g
        ORDER BY g.name COLLATE NOCASE
        LIMIT ? OFFSET ?;
        """,
        (int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [
        {"id": int(r["id"]), "name": r["name"], "track_count": int(r["track_count"])} for r in rows
    ]


async def count_genres(conn: aiosqlite.Connection) -> int:
    cursor = await conn.execute("SELECT COUNT(*) AS c FROM genres;")
    row = await cursor.fetchone()
    return int(row["c"]) if row else 0


async def get_genre_by_id(conn: aiosqlite.Connection, genre_id: int) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        SELECT
            g.id,
            g.name,
            (SELECT COUNT(*) FROM track_genres tg WHERE tg.genre_id = g.id) AS track_count
        FROM genres g
        WHERE g.id = ?
        """,
        (int(genre_id),),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return {"id": int(row["id"]), "name": row["name"], "track_count": int(row["track_count"])}


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------


async def list_roles(
    conn: aiosqlite.Connection,
    *,
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        SELECT id, name
        FROM roles
        ORDER BY name COLLATE NOCASE
        LIMIT ? OFFSET ?;
        """,
        (int(limit), int(offset)),
    )
    rows = await cursor.fetchall()
    return [{"id": int(r["id"]), "name": r["name"]} for r in rows]


async def count_roles(conn: aiosqlite.Connection) -> int:
    cursor = await conn.execute("SELECT COUNT(*) AS c FROM roles;")
    row = await cursor.fetchone()
    return int(row["c"]) if row else 0


async def get_role_by_id(conn: aiosqlite.Connection, role_id: int) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        SELECT id, name
        FROM roles
        WHERE id = ?
        """,
        (int(role_id),),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return {"id": int(row["id"]), "name": row["name"]}


async def get_role_by_name(conn: aiosqlite.Connection, name: str) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        SELECT id, name
        FROM roles
        WHERE name = ?
        """,
        (name,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return {"id": int(row["id"]), "name": row["name"]}


# ---------------------------------------------------------------------------
# Music folders
# ---------------------------------------------------------------------------


async def add_music_folder(conn: aiosqlite.Connection, path: str) -> int:
    """Add a music folder. Returns the folder ID."""
    await conn.execute(
        """
        INSERT OR IGNORE INTO music_folders (path) VALUES (?);
        """,
        (path,),
    )
    cursor = await conn.execute(
        "SELECT id FROM music_folders WHERE path = ?;",
        (path,),
    )
    row = await cursor.fetchone()
    return int(row["id"])


async def remove_music_folder(conn: aiosqlite.Connection, path: str) -> None:
    """Remove a music folder by path."""
    await conn.execute("DELETE FROM music_folders WHERE path = ?;", (path,))


async def list_music_folders(conn: aiosqlite.Connection) -> list[str]:
    """List all enabled music folders."""
    cursor = await conn.execute(
        """
        SELECT path
        FROM music_folders
        WHERE enabled = 1
        ORDER BY path COLLATE NOCASE;
        """
    )
    rows = await cursor.fetchall()
    return [str(r["path"]) for r in rows]


async def set_music_folders(conn: aiosqlite.Connection, paths: list[str]) -> None:
    """Replace all music folders with the given list."""
    await conn.execute("DELETE FROM music_folders;")
    for p in paths:
        await conn.execute(
            "INSERT INTO music_folders (path) VALUES (?);",
            (p,),
        )
    await conn.commit()


async def clear_music_folders(conn: aiosqlite.Connection) -> None:
    """Remove all music folders."""
    await conn.execute("DELETE FROM music_folders;")
    await conn.commit()
