"""
Database schema + migrations for Resonance.

This module is extracted from `resonance.core.library_db` to keep responsibilities
separated:

- Connection management and the public `LibraryDb` facade stay in `library_db.py`
- Schema creation, schema versioning, and forward-only migrations live here

Design notes:
- We use SQLite `PRAGMA user_version` as the schema version.
- Migrations are forward-only (no downgrade support).
- Keep migrations small and explicit; for huge refactors prefer a new DB.

The schema version is intentionally duplicated here (was previously a private
constant in `library_db.py`). `LibraryDb.ensure_schema()` should call into
`ensure_schema(conn)` from this module.
"""

from __future__ import annotations

from typing import Final

import aiosqlite

# Bump when you change the schema and add a migration in `migrate()`.
SCHEMA_VERSION: Final[int] = 8


async def ensure_schema(conn: aiosqlite.Connection) -> None:
    """
    Create or migrate schema to current version.

    This function assumes:
    - `conn` is an open aiosqlite connection
    - `conn.row_factory` is configured by the caller if desired
    - foreign_keys pragma is enabled by the caller
    """
    # meta: reserved for key-value config/flags (and future use)
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    await conn.commit()

    # Use PRAGMA user_version for schema versioning.
    cursor = await conn.execute("PRAGMA user_version;")
    row = await cursor.fetchone()
    current = int(row[0]) if row is not None else 0

    if current > SCHEMA_VERSION:
        raise RuntimeError(
            f"Database schema version {current} is newer than supported {SCHEMA_VERSION}."
        )

    if current == SCHEMA_VERSION:
        return

    await migrate(conn, from_version=current, to_version=SCHEMA_VERSION)
    await conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION};")
    await conn.commit()


async def migrate(conn: aiosqlite.Connection, *, from_version: int, to_version: int) -> None:
    """
    Perform forward-only migrations.

    Keep migrations small. If you need a big refactor, create a new DB.
    """
    # v0 -> v1
    if from_version == 0 and to_version >= 1:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL UNIQUE,

                title TEXT,
                artist TEXT,
                album TEXT,
                album_artist TEXT,

                track_no INTEGER,
                disc_no INTEGER,
                year INTEGER,

                duration_ms INTEGER,

                file_size INTEGER,
                mtime_ns INTEGER
            )
            """
        )

        # Indexes: tuned for browsing and exact lookups.
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_artist ON tracks(artist);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_album ON tracks(album);")
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tracks_album_artist ON tracks(album_artist);"
        )
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_title ON tracks(title);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_path ON tracks(path);")

        # A light-weight full text search can be added later (FTS5) if needed.
        await conn.commit()
        from_version = 1

    # v1 -> v2
    if from_version == 1 and to_version >= 2:
        # Add music_folders table for persistent scan roots
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS music_folders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL UNIQUE,
                enabled INTEGER NOT NULL DEFAULT 1,
                added_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_music_folders_path ON music_folders(path);"
        )
        await conn.commit()
        from_version = 2

    # v2 -> v3
    if from_version == 2 and to_version >= 3:
        # Add has_artwork column to tracks table
        await conn.execute("ALTER TABLE tracks ADD COLUMN has_artwork INTEGER NOT NULL DEFAULT 0;")
        await conn.commit()
        from_version = 3

    # v3 -> v4
    if from_version == 3 and to_version >= 4:
        # Add artists table
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS artists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                name_sort TEXT
            )
            """
        )
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_artists_name ON artists(name);")
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_artists_name_sort ON artists(name_sort);"
        )

        # Add albums table
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS albums (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                title_sort TEXT,
                artist_id INTEGER REFERENCES artists(id) ON DELETE SET NULL,
                year INTEGER,
                UNIQUE(title, artist_id)
            )
            """
        )
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_albums_title ON albums(title);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_albums_artist_id ON albums(artist_id);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_albums_year ON albums(year);")

        # Add FK columns to tracks
        await conn.execute(
            "ALTER TABLE tracks ADD COLUMN artist_id INTEGER REFERENCES artists(id);"
        )
        await conn.execute("ALTER TABLE tracks ADD COLUMN album_id INTEGER REFERENCES albums(id);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_artist_id ON tracks(artist_id);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_album_id ON tracks(album_id);")

        # Populate artists from existing track data
        await conn.execute(
            """
            INSERT OR IGNORE INTO artists (name, name_sort)
            SELECT DISTINCT artist, artist
            FROM tracks
            WHERE artist IS NOT NULL AND artist != ''
            """
        )

        # Populate albums from existing track data
        # First, ensure artists exist for album_artist values
        await conn.execute(
            """
            INSERT OR IGNORE INTO artists (name, name_sort)
            SELECT DISTINCT album_artist, album_artist
            FROM tracks
            WHERE album_artist IS NOT NULL AND album_artist != ''
              AND album_artist NOT IN (SELECT name FROM artists)
            """
        )

        # Insert albums with artist linkage (prefer album_artist, fall back to artist)
        await conn.execute(
            """
            INSERT OR IGNORE INTO albums (title, title_sort, artist_id, year)
            SELECT DISTINCT
                t.album,
                t.album,
                a.id,
                t.year
            FROM tracks t
            LEFT JOIN artists a ON a.name = COALESCE(t.album_artist, t.artist)
            WHERE t.album IS NOT NULL AND t.album != ''
            """
        )

        # Update tracks with artist_id
        await conn.execute(
            """
            UPDATE tracks
            SET artist_id = (SELECT id FROM artists WHERE name = tracks.artist)
            WHERE artist IS NOT NULL AND artist != ''
            """
        )

        # Update tracks with album_id
        await conn.execute(
            """
            UPDATE tracks
            SET album_id = (
                SELECT al.id FROM albums al
                LEFT JOIN artists ar ON al.artist_id = ar.id
                WHERE al.title = tracks.album
                  AND (ar.name = COALESCE(tracks.album_artist, tracks.artist) OR al.artist_id IS NULL)
                LIMIT 1
            )
            WHERE album IS NOT NULL AND album != ''
            """
        )

        await conn.commit()
        from_version = 4

    # v4 -> v5
    if from_version == 4 and to_version >= 5:
        # Genres: normalized table + m2m join to tracks (LMS-ish)
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS genres (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                name_sort TEXT
            )
            """
        )
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_genres_name ON genres(name);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_genres_name_sort ON genres(name_sort);")

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS track_genres (
                track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
                genre_id INTEGER NOT NULL REFERENCES genres(id) ON DELETE CASCADE,
                PRIMARY KEY (track_id, genre_id)
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_track_genres_genre ON track_genres(genre_id);"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_track_genres_track ON track_genres(track_id);"
        )

        await conn.commit()
        from_version = 5

    # v5 -> v6
    if from_version == 5 and to_version >= 6:
        # Compilations: LMS-ish album flag derived from track tags.
        # We store it both on tracks (raw tag) and albums (aggregated, for filtering).
        await conn.execute("ALTER TABLE tracks ADD COLUMN compilation INTEGER NOT NULL DEFAULT 0;")
        await conn.execute("ALTER TABLE albums ADD COLUMN compilation INTEGER NOT NULL DEFAULT 0;")
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_albums_compilation ON albums(compilation);"
        )

        await conn.commit()
        from_version = 6

    # v6 -> v7
    if from_version == 6 and to_version >= 7:
        # Contributors/Roles (LMS-like "contributor_tracks"):
        # - contributors: canonical person/group (normalized)
        # - roles: canonical contributor role (composer/conductor/...)
        # - contributor_tracks: m2m between tracks and contributors with a role
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS contributors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                name_sort TEXT
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_contributors_name ON contributors(name);"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_contributors_name_sort ON contributors(name_sort);"
        )

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS roles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE
            )
            """
        )
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_roles_name ON roles(name);")

        # Seed a small, stable role set. IDs are not assumed stable; clients should use role_id filters.
        await conn.execute("INSERT OR IGNORE INTO roles (name) VALUES ('artist');")
        await conn.execute("INSERT OR IGNORE INTO roles (name) VALUES ('albumartist');")
        await conn.execute("INSERT OR IGNORE INTO roles (name) VALUES ('composer');")
        await conn.execute("INSERT OR IGNORE INTO roles (name) VALUES ('conductor');")
        await conn.execute("INSERT OR IGNORE INTO roles (name) VALUES ('band');")

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS contributor_tracks (
                track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
                contributor_id INTEGER NOT NULL REFERENCES contributors(id) ON DELETE CASCADE,
                role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
                PRIMARY KEY (track_id, contributor_id, role_id)
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_contributor_tracks_track ON contributor_tracks(track_id);"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_contributor_tracks_contributor ON contributor_tracks(contributor_id);"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_contributor_tracks_role ON contributor_tracks(role_id);"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_contributor_tracks_role_contributor ON contributor_tracks(role_id, contributor_id);"
        )

        await conn.commit()
        from_version = 7

    # v7 -> v8
    if from_version == 7 and to_version >= 8:
        # Audio quality metadata columns for tracks
        await conn.execute("ALTER TABLE tracks ADD COLUMN sample_rate INTEGER;")
        await conn.execute("ALTER TABLE tracks ADD COLUMN bit_depth INTEGER;")
        await conn.execute("ALTER TABLE tracks ADD COLUMN bitrate INTEGER;")
        await conn.execute("ALTER TABLE tracks ADD COLUMN channels INTEGER;")

        await conn.commit()
        from_version = 8

    if from_version != to_version:
        raise RuntimeError(f"No migration path from {from_version} to {to_version}.")
