"""
Music library database schema + access layer.

Goals:
- Modern, minimal, and testable (no LMS complexity).
- SQLite + aiosqlite, async/await friendly.
- Keep schema small, but leave room to evolve (via user_version migrations).

This module is intentionally independent of the web layer.

Note:
- Models/DTOs and normalization helpers live in `resonance.core.db.models`
- Schema/migrations live in `resonance.core.db.schema`
- Query functions live in `resonance.core.db.queries_*` modules
- `LibraryDb` remains the public facade used by the rest of the codebase
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import aiosqlite

# Import query modules for delegation
from resonance.core.db import queries_albums, queries_artists, queries_meta, queries_tracks
from resonance.core.db.models import (
    AlbumRow,
    TrackRow,
    UpsertTrack,
    normalize_int,
    normalize_text,
)
from resonance.core.db.query_builder import (
    AlbumFilter,
    ArtistFilter,
    TrackFilter,
    album_row_to_dict,
    artist_row_to_dict,
    build_albums_count_query,
    build_albums_query,
    build_artists_count_query,
    build_artists_query,
    build_tracks_count_query,
    build_tracks_query,
)
from resonance.core.db.schema import ensure_schema as ensure_schema_sql


class LibraryDb:
    """
    Async access layer for the music library DB.

    Usage:
        db = LibraryDb("resonance.db")
        await db.open()
        await db.ensure_schema()
        ... queries ...
        await db.close()

    Notes:
    - This class is designed to be injected into other components.
    - Connections are not pooled; for now we keep a single connection.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._conn: aiosqlite.Connection | None = None

    @property
    def is_open(self) -> bool:
        return self._conn is not None

    async def open(self) -> None:
        if self._conn is not None:
            return
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row

        # Pragmas: modern defaults without being clever.
        await self._conn.execute("PRAGMA foreign_keys = ON;")
        await self._conn.execute("PRAGMA journal_mode = WAL;")
        await self._conn.execute("PRAGMA synchronous = NORMAL;")
        await self._conn.execute("PRAGMA temp_store = MEMORY;")

    async def close(self) -> None:
        if self._conn is None:
            return
        await self._conn.close()
        self._conn = None

    def _require_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("LibraryDb is not open. Call await db.open() first.")
        return self._conn

    async def ensure_schema(self) -> None:
        """Create or migrate schema to current version."""
        conn = self._require_conn()
        await ensure_schema_sql(conn)

    async def execute(self, sql: str, params: Sequence[Any] | Mapping[str, Any] = ()) -> None:
        conn = self._require_conn()
        await conn.execute(sql, params)

    async def commit(self) -> None:
        conn = self._require_conn()
        await conn.commit()

    # ===========================================================================
    # Tracks: upsert (core business logic - stays here)
    # ===========================================================================

    async def upsert_track(self, track: UpsertTrack) -> int:
        """
        Insert or update a track by its path.
        Also creates/links artist and album records as needed.
        Returns the track id.
        """
        conn = self._require_conn()

        path = str(track.path)
        title = normalize_text(track.title)
        artist = normalize_text(track.artist)
        album = normalize_text(track.album)
        album_artist = normalize_text(track.album_artist)
        track_no = normalize_int(track.track_no)
        disc_no = normalize_int(track.disc_no)
        year = normalize_int(track.year)
        duration_ms = normalize_int(track.duration_ms)
        file_size = normalize_int(track.file_size)
        mtime_ns = normalize_int(track.mtime_ns)
        has_artwork = 1 if track.has_artwork else 0

        genres: tuple[str, ...] = tuple(
            g for g in ((normalize_text(x) for x in track.genres) if track.genres else ()) if g
        )

        artist_id: int | None = None
        if artist:
            artist_id = await self._ensure_artist(artist)

        album_id: int | None = None
        if album:
            album_artist_name = album_artist or artist
            album_artist_id: int | None = None
            if album_artist_name:
                album_artist_id = await self._ensure_artist(album_artist_name)
            album_id = await self._ensure_album(album, album_artist_id, year)

        compilation = 1 if track.compilation else 0

        # Audio quality metadata
        sample_rate = normalize_int(track.sample_rate)
        bit_depth = normalize_int(track.bit_depth)
        bitrate = normalize_int(track.bitrate)
        channels = normalize_int(track.channels)

        # SQLite UPSERT
        await conn.execute(
            """
            INSERT INTO tracks(
                path, title, artist, album, album_artist,
                track_no, disc_no, year,
                duration_ms, file_size, mtime_ns, has_artwork, compilation,
                artist_id, album_id,
                sample_rate, bit_depth, bitrate, channels
            ) VALUES (
                :path, :title, :artist, :album, :album_artist,
                :track_no, :disc_no, :year,
                :duration_ms, :file_size, :mtime_ns, :has_artwork, :compilation,
                :artist_id, :album_id,
                :sample_rate, :bit_depth, :bitrate, :channels
            )
            ON CONFLICT(path) DO UPDATE SET
                title        = excluded.title,
                artist       = excluded.artist,
                album        = excluded.album,
                album_artist = excluded.album_artist,
                track_no     = excluded.track_no,
                disc_no      = excluded.disc_no,
                year         = excluded.year,
                duration_ms  = excluded.duration_ms,
                file_size    = excluded.file_size,
                mtime_ns     = excluded.mtime_ns,
                has_artwork  = excluded.has_artwork,
                compilation  = excluded.compilation,
                artist_id    = excluded.artist_id,
                album_id     = excluded.album_id,
                sample_rate  = excluded.sample_rate,
                bit_depth    = excluded.bit_depth,
                bitrate      = excluded.bitrate,
                channels     = excluded.channels
            """,
            {
                "path": path,
                "title": title,
                "artist": artist,
                "album": album,
                "album_artist": album_artist,
                "track_no": track_no,
                "disc_no": disc_no,
                "year": year,
                "duration_ms": duration_ms,
                "file_size": file_size,
                "mtime_ns": mtime_ns,
                "has_artwork": has_artwork,
                "compilation": compilation,
                "artist_id": artist_id,
                "album_id": album_id,
                "sample_rate": sample_rate,
                "bit_depth": bit_depth,
                "bitrate": bitrate,
                "channels": channels,
            },
        )

        # Fetch id deterministically
        cursor = await conn.execute("SELECT id FROM tracks WHERE path = ?;", (path,))
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("Upsert failed: track row not found after insert/update.")
        track_id = int(row["id"])

        # Aggregate compilation flag to album level
        if album_id is not None and compilation:
            await conn.execute(
                "UPDATE albums SET compilation = 1 WHERE id = ?;",
                (int(album_id),),
            )

        # Persist genres if provided
        if genres:
            for g in genres:
                genre_id = await self._ensure_genre(g)
                await conn.execute(
                    """
                    INSERT OR IGNORE INTO track_genres (track_id, genre_id)
                    VALUES (?, ?)
                    """,
                    (int(track_id), int(genre_id)),
                )

        # Persist contributors if provided
        if track.contributors:
            # Clear old contributor links for this track
            await conn.execute(
                "DELETE FROM contributor_tracks WHERE track_id = ?;",
                (int(track_id),),
            )

            for role_name_raw, contributor_name_raw in track.contributors:
                role_name = normalize_text(role_name_raw)
                contributor_name = normalize_text(contributor_name_raw)
                if not role_name or not contributor_name:
                    continue

                role_id = await self._ensure_role(role_name)
                contributor_id = await self._ensure_contributor(contributor_name)

                await conn.execute(
                    """
                    INSERT OR IGNORE INTO contributor_tracks (track_id, contributor_id, role_id)
                    VALUES (?, ?, ?)
                    """,
                    (int(track_id), int(contributor_id), int(role_id)),
                )

        return int(track_id)

    async def _ensure_artist(self, name: str) -> int:
        """Get or create an artist by name, return ID."""
        conn = self._require_conn()
        cursor = await conn.execute("SELECT id FROM artists WHERE name = ?;", (name,))
        row = await cursor.fetchone()
        if row is not None:
            return int(row["id"])

        await conn.execute(
            "INSERT INTO artists (name, name_sort) VALUES (?, ?);",
            (name, name),
        )
        cursor = await conn.execute("SELECT id FROM artists WHERE name = ?;", (name,))
        row = await cursor.fetchone()
        return int(row["id"])

    async def _ensure_contributor(self, name: str) -> int:
        """Get or create a contributor by name, return ID."""
        conn = self._require_conn()
        cursor = await conn.execute("SELECT id FROM contributors WHERE name = ?;", (name,))
        row = await cursor.fetchone()
        if row is not None:
            return int(row["id"])

        await conn.execute(
            "INSERT INTO contributors (name, name_sort) VALUES (?, ?);",
            (name, name),
        )
        cursor = await conn.execute("SELECT id FROM contributors WHERE name = ?;", (name,))
        row = await cursor.fetchone()
        return int(row["id"])

    async def _ensure_role(self, name: str) -> int:
        """Get or create a role by name, return ID."""
        conn = self._require_conn()
        cursor = await conn.execute("SELECT id FROM roles WHERE name = ?;", (name,))
        row = await cursor.fetchone()
        if row is not None:
            return int(row["id"])

        await conn.execute("INSERT INTO roles (name) VALUES (?);", (name,))
        cursor = await conn.execute("SELECT id FROM roles WHERE name = ?;", (name,))
        row = await cursor.fetchone()
        return int(row["id"])

    async def _ensure_album(self, title: str, artist_id: int | None, year: int | None) -> int:
        """Get or create an album by title + artist_id, return ID."""
        conn = self._require_conn()
        if artist_id is not None:
            cursor = await conn.execute(
                "SELECT id FROM albums WHERE title = ? AND artist_id = ?;",
                (title, artist_id),
            )
        else:
            cursor = await conn.execute(
                "SELECT id FROM albums WHERE title = ? AND artist_id IS NULL;",
                (title,),
            )
        row = await cursor.fetchone()
        if row is not None:
            return int(row["id"])

        await conn.execute(
            "INSERT INTO albums (title, title_sort, artist_id, year) VALUES (?, ?, ?, ?);",
            (title, title, artist_id, year),
        )
        if artist_id is not None:
            cursor = await conn.execute(
                "SELECT id FROM albums WHERE title = ? AND artist_id = ?;",
                (title, artist_id),
            )
        else:
            cursor = await conn.execute(
                "SELECT id FROM albums WHERE title = ? AND artist_id IS NULL;",
                (title,),
            )
        row = await cursor.fetchone()
        return int(row["id"])

    async def _ensure_genre(self, name: str) -> int:
        """Get or create a genre by name, return ID."""
        conn = self._require_conn()
        cursor = await conn.execute("SELECT id FROM genres WHERE name = ?;", (name,))
        row = await cursor.fetchone()
        if row is not None:
            return int(row["id"])

        await conn.execute(
            "INSERT INTO genres (name, name_sort) VALUES (?, ?);",
            (name, name),
        )
        cursor = await conn.execute("SELECT id FROM genres WHERE name = ?;", (name,))
        row = await cursor.fetchone()
        return int(row["id"])

    async def upsert_tracks(self, tracks: Iterable[UpsertTrack]) -> int:
        """Bulk upsert tracks. Returns count of upserted tracks."""
        conn = self._require_conn()
        # Use savepoint for transaction safety
        await conn.execute("SAVEPOINT upsert_tracks_sp;")
        try:
            count = 0
            for track in tracks:
                await self.upsert_track(track)
                count += 1
            await conn.execute("RELEASE SAVEPOINT upsert_tracks_sp;")
            # Persist scan writes immediately so they survive process restarts.
            await conn.commit()
            return count
        except Exception:
            await conn.execute("ROLLBACK TO SAVEPOINT upsert_tracks_sp;")
            raise

    # ===========================================================================
    # Track queries (delegated to queries_tracks module)
    # ===========================================================================

    async def get_track_by_id(self, track_id: int) -> TrackRow | None:
        return await queries_tracks.get_track_by_id(self._require_conn(), track_id)

    async def get_track_by_path(self, path: str) -> TrackRow | None:
        return await queries_tracks.get_track_by_path(self._require_conn(), path)

    async def list_tracks(
        self, *, limit: int = 500, offset: int = 0, order_by: str = "title"
    ) -> list[TrackRow]:
        return await queries_tracks.list_tracks(
            self._require_conn(), limit=limit, offset=offset, order_by=order_by
        )

    async def count_tracks(self) -> int:
        return await queries_tracks.count_tracks(self._require_conn())

    async def list_tracks_by_album(
        self, album_id: int, *, limit: int = 500, offset: int = 0, order_by: str = "tracknum"
    ) -> list[TrackRow]:
        return await queries_tracks.list_tracks_by_album(
            self._require_conn(), album_id, limit=limit, offset=offset, order_by=order_by
        )

    async def list_tracks_by_artist(
        self, artist_id: int, *, limit: int = 500, offset: int = 0, order_by: str = "album"
    ) -> list[TrackRow]:
        return await queries_tracks.list_tracks_by_artist(
            self._require_conn(), artist_id, limit=limit, offset=offset, order_by=order_by
        )

    async def count_tracks_by_album(self, album_id: int) -> int:
        return await queries_tracks.count_tracks_by_album(self._require_conn(), album_id)

    async def count_tracks_by_artist(self, artist_id: int) -> int:
        return await queries_tracks.count_tracks_by_artist(self._require_conn(), artist_id)

    async def search_tracks(
        self, query: str, *, limit: int = 100, offset: int = 0
    ) -> list[TrackRow]:
        return await queries_tracks.search_tracks(
            self._require_conn(), query, limit=limit, offset=offset
        )

    async def delete_track_by_path(self, path: str) -> bool:
        return await queries_tracks.delete_track_by_path(self._require_conn(), path)

    async def delete_tracks_by_album_id(self, album_id: int) -> int:
        """Delete all tracks belonging to an album. Returns count of deleted tracks."""
        return await queries_tracks.delete_tracks_by_album_id(self._require_conn(), album_id)

    async def delete_tracks_by_artist_id(self, artist_id: int) -> int:
        """Delete all tracks belonging to an artist. Returns count of deleted tracks."""
        return await queries_tracks.delete_tracks_by_artist_id(self._require_conn(), artist_id)

    async def delete_album(self, album_id: int, cleanup_orphans: bool = True) -> dict[str, int]:
        """
        Delete an album and all its tracks.

        Args:
            album_id: ID of the album to delete
            cleanup_orphans: If True, also remove orphaned artists/albums/genres

        Returns:
            Dict with counts: {"tracks_deleted": N, "album_deleted": 0|1, ...}
        """
        conn = self._require_conn()
        result: dict[str, int] = {}

        # First delete all tracks belonging to this album
        tracks_deleted = await queries_tracks.delete_tracks_by_album_id(conn, album_id)
        result["tracks_deleted"] = tracks_deleted

        cursor = await conn.execute("DELETE FROM albums WHERE id = ?;", (album_id,))
        result["album_deleted"] = cursor.rowcount

        # Optionally clean up orphaned data
        if cleanup_orphans:
            orphan_result = await self.cleanup_orphans()
            result.update(orphan_result)

        await conn.commit()
        return result

    async def cleanup_orphans(self) -> dict[str, int]:
        """
        Remove orphaned albums, artists, and genres that have no tracks.

        Returns:
            Dict with counts of deleted orphans.
        """
        conn = self._require_conn()
        result: dict[str, int] = {}

        # Delete albums with no tracks
        cursor = await conn.execute(
            """
            DELETE FROM albums
            WHERE id NOT IN (SELECT DISTINCT album_id FROM tracks WHERE album_id IS NOT NULL)
            """
        )
        result["orphan_albums_deleted"] = cursor.rowcount

        # Delete artists with no tracks and no albums
        cursor = await conn.execute(
            """
            DELETE FROM artists
            WHERE id NOT IN (SELECT DISTINCT artist_id FROM tracks WHERE artist_id IS NOT NULL)
              AND id NOT IN (SELECT DISTINCT artist_id FROM albums WHERE artist_id IS NOT NULL)
            """
        )
        result["orphan_artists_deleted"] = cursor.rowcount

        # Delete genres with no track associations
        cursor = await conn.execute(
            """
            DELETE FROM genres
            WHERE id NOT IN (SELECT DISTINCT genre_id FROM track_genres)
            """
        )
        result["orphan_genres_deleted"] = cursor.rowcount

        # Delete contributor_tracks entries for deleted tracks (should cascade, but be safe)
        cursor = await conn.execute(
            """
            DELETE FROM contributor_tracks
            WHERE track_id NOT IN (SELECT id FROM tracks)
            """
        )
        result["orphan_contributors_deleted"] = cursor.rowcount

        return result

    # Track filters: year
    async def count_tracks_by_year(self, year: int) -> int:
        return await queries_tracks.count_tracks_by_year(self._require_conn(), year)

    async def list_tracks_by_year(
        self, year: int, *, limit: int = 500, offset: int = 0, order_by: str = "title"
    ) -> list[TrackRow]:
        return await queries_tracks.list_tracks_by_year(
            self._require_conn(), year, limit=limit, offset=offset, order_by=order_by
        )

    async def count_tracks_by_artist_and_year(self, artist_id: int, year: int) -> int:
        return await queries_tracks.count_tracks_by_artist_and_year(
            self._require_conn(), artist_id, year
        )

    async def list_tracks_by_artist_and_year(
        self,
        artist_id: int,
        year: int,
        *,
        limit: int = 500,
        offset: int = 0,
        order_by: str = "album",
    ) -> list[TrackRow]:
        return await queries_tracks.list_tracks_by_artist_and_year(
            self._require_conn(), artist_id, year, limit=limit, offset=offset, order_by=order_by
        )

    async def count_tracks_by_album_and_year(self, album_id: int, year: int) -> int:
        return await queries_tracks.count_tracks_by_album_and_year(
            self._require_conn(), album_id, year
        )

    async def list_tracks_by_album_and_year(
        self,
        album_id: int,
        year: int,
        *,
        limit: int = 500,
        offset: int = 0,
        order_by: str = "tracknum",
    ) -> list[TrackRow]:
        return await queries_tracks.list_tracks_by_album_and_year(
            self._require_conn(), album_id, year, limit=limit, offset=offset, order_by=order_by
        )

    # Track filters: compilation
    async def count_tracks_by_compilation(self, compilation: int) -> int:
        return await queries_tracks.count_tracks_by_compilation(self._require_conn(), compilation)

    async def list_tracks_by_compilation(
        self, compilation: int, *, limit: int = 500, offset: int = 0, order_by: str = "title"
    ) -> list[TrackRow]:
        return await queries_tracks.list_tracks_by_compilation(
            self._require_conn(), compilation, limit=limit, offset=offset, order_by=order_by
        )

    async def count_tracks_by_compilation_and_year(self, compilation: int, year: int) -> int:
        return await queries_tracks.count_tracks_by_compilation_and_year(
            self._require_conn(), compilation, year
        )

    async def list_tracks_by_compilation_and_year(
        self,
        compilation: int,
        year: int,
        *,
        limit: int = 500,
        offset: int = 0,
        order_by: str = "title",
    ) -> list[TrackRow]:
        return await queries_tracks.list_tracks_by_compilation_and_year(
            self._require_conn(), compilation, year, limit=limit, offset=offset, order_by=order_by
        )

    async def count_tracks_by_compilation_and_artist(self, compilation: int, artist_id: int) -> int:
        return await queries_tracks.count_tracks_by_compilation_and_artist(
            self._require_conn(), compilation, artist_id
        )

    async def list_tracks_by_compilation_and_artist(
        self,
        compilation: int,
        artist_id: int,
        *,
        limit: int = 500,
        offset: int = 0,
        order_by: str = "album",
    ) -> list[TrackRow]:
        return await queries_tracks.list_tracks_by_compilation_and_artist(
            self._require_conn(),
            compilation,
            artist_id,
            limit=limit,
            offset=offset,
            order_by=order_by,
        )

    async def count_tracks_by_compilation_artist_and_year(
        self, compilation: int, artist_id: int, year: int
    ) -> int:
        return await queries_tracks.count_tracks_by_compilation_artist_and_year(
            self._require_conn(), compilation, artist_id, year
        )

    async def list_tracks_by_compilation_artist_and_year(
        self,
        compilation: int,
        artist_id: int,
        year: int,
        *,
        limit: int = 500,
        offset: int = 0,
        order_by: str = "album",
    ) -> list[TrackRow]:
        return await queries_tracks.list_tracks_by_compilation_artist_and_year(
            self._require_conn(),
            compilation,
            artist_id,
            year,
            limit=limit,
            offset=offset,
            order_by=order_by,
        )

    async def count_tracks_by_compilation_and_album(self, compilation: int, album_id: int) -> int:
        return await queries_tracks.count_tracks_by_compilation_and_album(
            self._require_conn(), compilation, album_id
        )

    async def list_tracks_by_compilation_and_album(
        self,
        compilation: int,
        album_id: int,
        *,
        limit: int = 500,
        offset: int = 0,
        order_by: str = "tracknum",
    ) -> list[TrackRow]:
        return await queries_tracks.list_tracks_by_compilation_and_album(
            self._require_conn(),
            compilation,
            album_id,
            limit=limit,
            offset=offset,
            order_by=order_by,
        )

    async def count_tracks_by_compilation_album_and_year(
        self, compilation: int, album_id: int, year: int
    ) -> int:
        return await queries_tracks.count_tracks_by_compilation_album_and_year(
            self._require_conn(), compilation, album_id, year
        )

    async def list_tracks_by_compilation_album_and_year(
        self,
        compilation: int,
        album_id: int,
        year: int,
        *,
        limit: int = 500,
        offset: int = 0,
        order_by: str = "tracknum",
    ) -> list[TrackRow]:
        return await queries_tracks.list_tracks_by_compilation_album_and_year(
            self._require_conn(),
            compilation,
            album_id,
            year,
            limit=limit,
            offset=offset,
            order_by=order_by,
        )

    async def count_tracks_by_compilation_and_genre_id(
        self, compilation: int, genre_id: int
    ) -> int:
        return await queries_tracks.count_tracks_by_compilation_and_genre_id(
            self._require_conn(), compilation, genre_id
        )

    async def list_tracks_by_compilation_and_genre_id(
        self,
        compilation: int,
        genre_id: int,
        *,
        limit: int = 500,
        offset: int = 0,
        order_by: str = "title",
    ) -> list[TrackRow]:
        return await queries_tracks.list_tracks_by_compilation_and_genre_id(
            self._require_conn(),
            compilation,
            genre_id,
            limit=limit,
            offset=offset,
            order_by=order_by,
        )

    # Track filters: genre_id
    async def count_tracks_by_genre_id(self, genre_id: int) -> int:
        return await queries_tracks.count_tracks_by_genre_id(self._require_conn(), genre_id)

    async def list_tracks_by_genre_id(
        self, genre_id: int, *, limit: int = 500, offset: int = 0, order_by: str = "title"
    ) -> list[TrackRow]:
        return await queries_tracks.list_tracks_by_genre_id(
            self._require_conn(), genre_id, limit=limit, offset=offset, order_by=order_by
        )

    async def count_tracks_by_genre_and_year(self, genre_id: int, year: int) -> int:
        return await queries_tracks.count_tracks_by_genre_and_year(
            self._require_conn(), genre_id, year
        )

    async def list_tracks_by_genre_and_year(
        self,
        genre_id: int,
        year: int,
        *,
        limit: int = 500,
        offset: int = 0,
        order_by: str = "title",
    ) -> list[TrackRow]:
        return await queries_tracks.list_tracks_by_genre_and_year(
            self._require_conn(), genre_id, year, limit=limit, offset=offset, order_by=order_by
        )

    async def count_tracks_by_genre_and_artist(self, genre_id: int, artist_id: int) -> int:
        return await queries_tracks.count_tracks_by_genre_and_artist(
            self._require_conn(), genre_id, artist_id
        )

    async def list_tracks_by_genre_and_artist(
        self,
        genre_id: int,
        artist_id: int,
        *,
        limit: int = 500,
        offset: int = 0,
        order_by: str = "album",
    ) -> list[TrackRow]:
        return await queries_tracks.list_tracks_by_genre_and_artist(
            self._require_conn(), genre_id, artist_id, limit=limit, offset=offset, order_by=order_by
        )

    async def count_tracks_by_genre_artist_and_year(
        self, genre_id: int, artist_id: int, year: int
    ) -> int:
        return await queries_tracks.count_tracks_by_genre_artist_and_year(
            self._require_conn(), genre_id, artist_id, year
        )

    async def list_tracks_by_genre_artist_and_year(
        self,
        genre_id: int,
        artist_id: int,
        year: int,
        *,
        limit: int = 500,
        offset: int = 0,
        order_by: str = "album",
    ) -> list[TrackRow]:
        return await queries_tracks.list_tracks_by_genre_artist_and_year(
            self._require_conn(),
            genre_id,
            artist_id,
            year,
            limit=limit,
            offset=offset,
            order_by=order_by,
        )

    async def count_tracks_by_genre_and_album(self, genre_id: int, album_id: int) -> int:
        return await queries_tracks.count_tracks_by_genre_and_album(
            self._require_conn(), genre_id, album_id
        )

    async def list_tracks_by_genre_and_album(
        self,
        genre_id: int,
        album_id: int,
        *,
        limit: int = 500,
        offset: int = 0,
        order_by: str = "tracknum",
    ) -> list[TrackRow]:
        return await queries_tracks.list_tracks_by_genre_and_album(
            self._require_conn(), genre_id, album_id, limit=limit, offset=offset, order_by=order_by
        )

    async def count_tracks_by_genre_album_and_year(
        self, genre_id: int, album_id: int, year: int
    ) -> int:
        return await queries_tracks.count_tracks_by_genre_album_and_year(
            self._require_conn(), genre_id, album_id, year
        )

    async def list_tracks_by_genre_album_and_year(
        self,
        genre_id: int,
        album_id: int,
        year: int,
        *,
        limit: int = 500,
        offset: int = 0,
        order_by: str = "tracknum",
    ) -> list[TrackRow]:
        return await queries_tracks.list_tracks_by_genre_album_and_year(
            self._require_conn(),
            genre_id,
            album_id,
            year,
            limit=limit,
            offset=offset,
            order_by=order_by,
        )

    # Track filters: role_id
    async def count_tracks_by_role_id(self, role_id: int) -> int:
        return await queries_tracks.count_tracks_by_role_id(self._require_conn(), role_id)

    async def list_tracks_by_role_id(
        self, role_id: int, *, limit: int = 500, offset: int = 0, order_by: str = "title"
    ) -> list[TrackRow]:
        return await queries_tracks.list_tracks_by_role_id(
            self._require_conn(), role_id, limit=limit, offset=offset, order_by=order_by
        )

    async def count_tracks_by_role_and_genre_id(self, role_id: int, genre_id: int) -> int:
        return await queries_tracks.count_tracks_by_role_and_genre_id(
            self._require_conn(), role_id, genre_id
        )

    async def list_tracks_by_role_and_genre_id(
        self,
        role_id: int,
        genre_id: int,
        *,
        limit: int = 500,
        offset: int = 0,
        order_by: str = "title",
    ) -> list[TrackRow]:
        return await queries_tracks.list_tracks_by_role_and_genre_id(
            self._require_conn(), role_id, genre_id, limit=limit, offset=offset, order_by=order_by
        )

    async def count_tracks_by_role_and_year(self, role_id: int, year: int) -> int:
        return await queries_tracks.count_tracks_by_role_and_year(
            self._require_conn(), role_id, year
        )

    async def list_tracks_by_role_and_year(
        self,
        role_id: int,
        year: int,
        *,
        limit: int = 500,
        offset: int = 0,
        order_by: str = "title",
    ) -> list[TrackRow]:
        return await queries_tracks.list_tracks_by_role_and_year(
            self._require_conn(), role_id, year, limit=limit, offset=offset, order_by=order_by
        )

    async def count_tracks_by_role_and_compilation(self, role_id: int, compilation: int) -> int:
        return await queries_tracks.count_tracks_by_role_and_compilation(
            self._require_conn(), role_id, compilation
        )

    async def list_tracks_by_role_and_compilation(
        self,
        role_id: int,
        compilation: int,
        *,
        limit: int = 500,
        offset: int = 0,
        order_by: str = "title",
    ) -> list[TrackRow]:
        return await queries_tracks.list_tracks_by_role_and_compilation(
            self._require_conn(),
            role_id,
            compilation,
            limit=limit,
            offset=offset,
            order_by=order_by,
        )

    # ===========================================================================
    # Generic filtered queries (query_builder-based)
    # ===========================================================================

    async def list_tracks_filtered(
        self,
        f: TrackFilter,
        *,
        limit: int = 500,
        offset: int = 0,
        order_by: str = "title",
    ) -> list[TrackRow]:
        """List tracks matching the given filter using the dynamic query builder."""
        conn = self._require_conn()
        sql, params = build_tracks_query(f, order_by=order_by, limit=limit, offset=offset)
        cursor = await conn.execute(sql, params)
        rows = await cursor.fetchall()
        return [queries_tracks._row_to_track(r) for r in rows]

    async def count_tracks_filtered(self, f: TrackFilter) -> int:
        """Count tracks matching the given filter using the dynamic query builder."""
        conn = self._require_conn()
        sql, params = build_tracks_count_query(f)
        cursor = await conn.execute(sql, params)
        row = await cursor.fetchone()
        return int(row["c"]) if row else 0

    async def list_albums_filtered(
        self,
        f: AlbumFilter,
        *,
        limit: int = 500,
        offset: int = 0,
        order_by: str = "album",
    ) -> list[dict[str, Any]]:
        """List albums (with track_count) matching the given filter."""
        conn = self._require_conn()
        sql, params = build_albums_query(f, order_by=order_by, limit=limit, offset=offset)
        cursor = await conn.execute(sql, params)
        rows = await cursor.fetchall()
        return [album_row_to_dict(r) for r in rows]

    async def count_albums_filtered(self, f: AlbumFilter) -> int:
        """Count albums matching the given filter."""
        conn = self._require_conn()
        sql, params = build_albums_count_query(f)
        cursor = await conn.execute(sql, params)
        row = await cursor.fetchone()
        return int(row["c"]) if row else 0

    async def list_artists_filtered(
        self,
        f: ArtistFilter,
        *,
        limit: int = 500,
        offset: int = 0,
        order_by: str = "artist",
    ) -> list[dict[str, Any]]:
        """List artists (with album_count) matching the given filter."""
        conn = self._require_conn()
        sql, params = build_artists_query(f, order_by=order_by, limit=limit, offset=offset)
        cursor = await conn.execute(sql, params)
        rows = await cursor.fetchall()
        return [artist_row_to_dict(r) for r in rows]

    async def count_artists_filtered(self, f: ArtistFilter) -> int:
        """Count artists matching the given filter."""
        conn = self._require_conn()
        sql, params = build_artists_count_query(f)
        cursor = await conn.execute(sql, params)
        row = await cursor.fetchone()
        return int(row["c"]) if row else 0

    # ===========================================================================
    # Artist queries (delegated to queries_artists module)
    # ===========================================================================

    async def get_artist_by_id(self, artist_id: int) -> dict[str, Any] | None:
        return await queries_artists.get_artist_by_id(self._require_conn(), artist_id)

    async def get_artist_by_name(self, name: str) -> dict[str, Any] | None:
        return await queries_artists.get_artist_by_name(self._require_conn(), name)

    async def list_all_artists(self, *, limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
        return await queries_artists.list_all_artists(
            self._require_conn(), limit=limit, offset=offset
        )

    async def count_artists(self) -> int:
        return await queries_artists.count_artists(self._require_conn())

    async def get_artist_album_count(self, artist_id: int) -> int:
        return await queries_artists.get_artist_album_count(self._require_conn(), artist_id)

    async def get_artist_with_album_count(self, artist_id: int) -> dict[str, Any] | None:
        return await queries_artists.get_artist_with_album_count(self._require_conn(), artist_id)

    async def list_artists_with_album_counts(
        self, *, offset: int = 0, limit: int = 500, order_by: str = "artist"
    ) -> list[dict[str, Any]]:
        return await queries_artists.list_artists_with_album_counts(
            self._require_conn(), offset=offset, limit=limit, order_by=order_by
        )

    # Artist filters: compilation
    async def count_artists_by_compilation(self, compilation: int) -> int:
        return await queries_artists.count_artists_by_compilation(self._require_conn(), compilation)

    async def list_artists_with_album_counts_by_compilation(
        self, compilation: int, *, offset: int = 0, limit: int = 500, order_by: str = "artist"
    ) -> list[dict[str, Any]]:
        return await queries_artists.list_artists_with_album_counts_by_compilation(
            self._require_conn(), compilation, offset=offset, limit=limit, order_by=order_by
        )

    # Artist filters: year
    async def count_artists_by_year(self, year: int) -> int:
        return await queries_artists.count_artists_by_year(self._require_conn(), year)

    async def list_artists_with_album_counts_by_year(
        self, year: int, *, offset: int = 0, limit: int = 500, order_by: str = "artist"
    ) -> list[dict[str, Any]]:
        return await queries_artists.list_artists_with_album_counts_by_year(
            self._require_conn(), year, offset=offset, limit=limit, order_by=order_by
        )

    # Artist filters: genre_id
    async def count_artists_by_genre_id(self, genre_id: int) -> int:
        return await queries_artists.count_artists_by_genre_id(self._require_conn(), genre_id)

    async def list_artists_by_genre_id(
        self, genre_id: int, *, offset: int = 0, limit: int = 500, order_by: str = "artist"
    ) -> list[dict[str, Any]]:
        return await queries_artists.list_artists_by_genre_id(
            self._require_conn(), genre_id, offset=offset, limit=limit, order_by=order_by
        )

    async def count_artists_by_genre_and_year(self, genre_id: int, year: int) -> int:
        return await queries_artists.count_artists_by_genre_and_year(
            self._require_conn(), genre_id, year
        )

    async def list_artists_by_genre_and_year(
        self,
        genre_id: int,
        year: int,
        *,
        offset: int = 0,
        limit: int = 500,
        order_by: str = "artist",
    ) -> list[dict[str, Any]]:
        return await queries_artists.list_artists_by_genre_and_year(
            self._require_conn(), genre_id, year, offset=offset, limit=limit, order_by=order_by
        )

    # Artist filters: role_id
    async def count_artists_by_role_id(self, role_id: int) -> int:
        return await queries_artists.count_artists_by_role_id(self._require_conn(), role_id)

    async def list_artists_with_album_counts_by_role_id(
        self,
        role_id: int,
        *,
        limit: int = 500,
        offset: int = 0,
        order_by: str = "artist",
    ) -> list[dict[str, Any]]:
        conn = self._require_conn()
        # order_by for contributors: use name or name_sort
        if order_by == "artist":
            order_clause = "c.name_sort COLLATE NOCASE"
        else:
            order_clause = "c.name COLLATE NOCASE"
        cursor = await conn.execute(
            f"""
            SELECT
                c.id,
                c.name,
                COUNT(DISTINCT ct.track_id) AS track_count,
                COUNT(DISTINCT t.album_id) AS album_count
            FROM contributors c
            JOIN contributor_tracks ct ON ct.contributor_id = c.id
            JOIN tracks t ON t.id = ct.track_id
            WHERE ct.role_id = ?
            GROUP BY c.id, c.name
            ORDER BY {order_clause}
            LIMIT ? OFFSET ?;
            """,
            (int(role_id), int(limit), int(offset)),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": int(r["id"]),
                "name": r["name"],
                "track_count": int(r["track_count"]),
                "album_count": int(r["album_count"]),
            }
            for r in rows
        ]

    async def count_artists_by_role_and_genre_id(self, role_id: int, genre_id: int) -> int:
        conn = self._require_conn()
        cursor = await conn.execute(
            """
            SELECT COUNT(DISTINCT c.id) AS c
            FROM contributors c
            JOIN contributor_tracks ct ON ct.contributor_id = c.id
            JOIN tracks t ON t.id = ct.track_id
            JOIN track_genres tg ON tg.track_id = t.id
            WHERE ct.role_id = ? AND tg.genre_id = ?
            """,
            (int(role_id), int(genre_id)),
        )
        row = await cursor.fetchone()
        return int(row["c"]) if row else 0

    async def list_artists_with_album_counts_by_role_and_genre_id(
        self,
        role_id: int,
        genre_id: int,
        *,
        limit: int = 500,
        offset: int = 0,
        order_by: str = "artist",
    ) -> list[dict[str, Any]]:
        conn = self._require_conn()
        if order_by == "artist":
            order_clause = "c.name_sort COLLATE NOCASE"
        else:
            order_clause = "c.name COLLATE NOCASE"
        cursor = await conn.execute(
            f"""
            SELECT
                c.id,
                c.name,
                COUNT(DISTINCT ct.track_id) AS track_count,
                COUNT(DISTINCT t.album_id) AS album_count
            FROM contributors c
            JOIN contributor_tracks ct ON ct.contributor_id = c.id
            JOIN tracks t ON t.id = ct.track_id
            JOIN track_genres tg ON tg.track_id = t.id
            WHERE ct.role_id = ? AND tg.genre_id = ?
            GROUP BY c.id, c.name
            ORDER BY {order_clause}
            LIMIT ? OFFSET ?;
            """,
            (int(role_id), int(genre_id), int(limit), int(offset)),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": int(r["id"]),
                "name": r["name"],
                "track_count": int(r["track_count"]),
                "album_count": int(r["album_count"]),
            }
            for r in rows
        ]

    async def count_artists_by_role_and_year(self, role_id: int, year: int) -> int:
        conn = self._require_conn()
        cursor = await conn.execute(
            """
            SELECT COUNT(DISTINCT c.id) AS c
            FROM contributors c
            JOIN contributor_tracks ct ON ct.contributor_id = c.id
            JOIN tracks t ON t.id = ct.track_id
            WHERE ct.role_id = ? AND t.year = ?
            """,
            (int(role_id), int(year)),
        )
        row = await cursor.fetchone()
        return int(row["c"]) if row else 0

    async def list_artists_with_album_counts_by_role_and_year(
        self,
        role_id: int,
        year: int,
        *,
        limit: int = 500,
        offset: int = 0,
        order_by: str = "artist",
    ) -> list[dict[str, Any]]:
        conn = self._require_conn()
        if order_by == "artist":
            order_clause = "c.name_sort COLLATE NOCASE"
        else:
            order_clause = "c.name COLLATE NOCASE"
        cursor = await conn.execute(
            f"""
            SELECT
                c.id,
                c.name,
                COUNT(DISTINCT ct.track_id) AS track_count,
                COUNT(DISTINCT t.album_id) AS album_count
            FROM contributors c
            JOIN contributor_tracks ct ON ct.contributor_id = c.id
            JOIN tracks t ON t.id = ct.track_id
            WHERE ct.role_id = ? AND t.year = ?
            GROUP BY c.id, c.name
            ORDER BY {order_clause}
            LIMIT ? OFFSET ?;
            """,
            (int(role_id), int(year), int(limit), int(offset)),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": int(r["id"]),
                "name": r["name"],
                "track_count": int(r["track_count"]),
                "album_count": int(r["album_count"]),
            }
            for r in rows
        ]

    async def count_artists_by_role_and_compilation(self, role_id: int, compilation: int) -> int:
        conn = self._require_conn()
        cursor = await conn.execute(
            """
            SELECT COUNT(DISTINCT c.id) AS c
            FROM contributors c
            JOIN contributor_tracks ct ON ct.contributor_id = c.id
            JOIN tracks t ON t.id = ct.track_id
            WHERE ct.role_id = ? AND t.compilation = ?
            """,
            (int(role_id), int(compilation)),
        )
        row = await cursor.fetchone()
        return int(row["c"]) if row else 0

    async def list_artists_with_album_counts_by_role_and_compilation(
        self,
        role_id: int,
        compilation: int,
        *,
        limit: int = 500,
        offset: int = 0,
        order_by: str = "artist",
    ) -> list[dict[str, Any]]:
        conn = self._require_conn()
        if order_by == "artist":
            order_clause = "c.name_sort COLLATE NOCASE"
        else:
            order_clause = "c.name COLLATE NOCASE"
        cursor = await conn.execute(
            f"""
            SELECT
                c.id,
                c.name,
                COUNT(DISTINCT ct.track_id) AS track_count,
                COUNT(DISTINCT t.album_id) AS album_count
            FROM contributors c
            JOIN contributor_tracks ct ON ct.contributor_id = c.id
            JOIN tracks t ON t.id = ct.track_id
            WHERE ct.role_id = ? AND t.compilation = ?
            GROUP BY c.id, c.name
            ORDER BY {order_clause}
            LIMIT ? OFFSET ?;
            """,
            (int(role_id), int(compilation), int(limit), int(offset)),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": int(r["id"]),
                "name": r["name"],
                "track_count": int(r["track_count"]),
                "album_count": int(r["album_count"]),
            }
            for r in rows
        ]

    # Legacy browse: just artist/album name lists
    async def list_artists(self, *, limit: int = 500, offset: int = 0) -> list[str]:
        conn = self._require_conn()
        cursor = await conn.execute(
            """
            SELECT DISTINCT name
            FROM artists
            ORDER BY name COLLATE NOCASE
            LIMIT ? OFFSET ?;
            """,
            (int(limit), int(offset)),
        )
        rows = await cursor.fetchall()
        return [str(r["name"]) for r in rows]

    async def list_albums(
        self, artist: str | None = None, *, limit: int = 500, offset: int = 0
    ) -> list[str]:
        conn = self._require_conn()
        if artist is None:
            cursor = await conn.execute(
                """
                SELECT DISTINCT title AS name
                FROM albums
                ORDER BY title COLLATE NOCASE
                LIMIT ? OFFSET ?;
                """,
                (int(limit), int(offset)),
            )
        else:
            cursor = await conn.execute(
                """
                SELECT DISTINCT a.title AS name
                FROM albums a
                LEFT JOIN artists ar ON a.artist_id = ar.id
                WHERE ar.name = ?
                ORDER BY a.title COLLATE NOCASE
                LIMIT ? OFFSET ?;
                """,
                (artist, int(limit), int(offset)),
            )
        rows = await cursor.fetchall()
        return [str(r["name"]) for r in rows]

    # ===========================================================================
    # Album queries (delegated to queries_albums module)
    # ===========================================================================

    async def get_album_by_id(self, album_id: int) -> AlbumRow | None:
        return await queries_albums.get_album_by_id(self._require_conn(), album_id)

    async def list_all_albums(self, *, limit: int = 500, offset: int = 0) -> list[AlbumRow]:
        return await queries_albums.list_all_albums(
            self._require_conn(), limit=limit, offset=offset
        )

    async def list_albums_by_artist(
        self, artist_id: int, *, limit: int = 500, offset: int = 0
    ) -> list[AlbumRow]:
        return await queries_albums.list_albums_by_artist(
            self._require_conn(), artist_id, limit=limit, offset=offset
        )

    async def count_albums(self) -> int:
        return await queries_albums.count_albums(self._require_conn())

    async def count_albums_by_artist(self, artist_id: int) -> int:
        return await queries_albums.count_albums_by_artist(self._require_conn(), artist_id)

    async def get_album_track_count(self, album_id: int) -> int:
        return await queries_albums.get_album_track_count(self._require_conn(), album_id)

    async def list_albums_with_track_counts(
        self, *, limit: int = 500, offset: int = 0, order_by: str = "album"
    ) -> list[dict[str, Any]]:
        return await queries_albums.list_albums_with_track_counts(
            self._require_conn(), limit=limit, offset=offset, order_by=order_by
        )

    async def get_album_with_track_count(self, album_id: int) -> dict[str, Any] | None:
        return await queries_albums.get_album_with_track_count(self._require_conn(), album_id)

    async def list_albums_with_track_counts_by_artist(
        self, artist_id: int, *, limit: int = 500, offset: int = 0, order_by: str = "album"
    ) -> list[dict[str, Any]]:
        return await queries_albums.list_albums_with_track_counts_by_artist(
            self._require_conn(), artist_id, limit=limit, offset=offset, order_by=order_by
        )

    # Album filters: year
    async def count_albums_by_year(self, year: int) -> int:
        return await queries_albums.count_albums_by_year(self._require_conn(), year)

    async def list_albums_with_track_counts_by_year(
        self, year: int, *, limit: int = 500, offset: int = 0, order_by: str = "album"
    ) -> list[dict[str, Any]]:
        return await queries_albums.list_albums_with_track_counts_by_year(
            self._require_conn(), year, limit=limit, offset=offset, order_by=order_by
        )

    async def count_albums_by_artist_and_year(self, artist_id: int, year: int) -> int:
        return await queries_albums.count_albums_by_artist_and_year(
            self._require_conn(), artist_id, year
        )

    async def list_albums_with_track_counts_by_artist_and_year(
        self,
        artist_id: int,
        year: int,
        *,
        limit: int = 500,
        offset: int = 0,
        order_by: str = "album",
    ) -> list[dict[str, Any]]:
        return await queries_albums.list_albums_with_track_counts_by_artist_and_year(
            self._require_conn(), artist_id, year, limit=limit, offset=offset, order_by=order_by
        )

    # Album filters: compilation
    async def count_albums_by_compilation(self, compilation: int) -> int:
        return await queries_albums.count_albums_by_compilation(self._require_conn(), compilation)

    async def list_albums_with_track_counts_by_compilation(
        self, compilation: int, *, limit: int = 500, offset: int = 0, order_by: str = "album"
    ) -> list[dict[str, Any]]:
        return await queries_albums.list_albums_with_track_counts_by_compilation(
            self._require_conn(), compilation, limit=limit, offset=offset, order_by=order_by
        )

    async def count_albums_by_compilation_and_year(self, compilation: int, year: int) -> int:
        return await queries_albums.count_albums_by_compilation_and_year(
            self._require_conn(), compilation, year
        )

    async def list_albums_with_track_counts_by_compilation_and_year(
        self,
        compilation: int,
        year: int,
        *,
        limit: int = 500,
        offset: int = 0,
        order_by: str = "album",
    ) -> list[dict[str, Any]]:
        return await queries_albums.list_albums_with_track_counts_by_compilation_and_year(
            self._require_conn(), compilation, year, limit=limit, offset=offset, order_by=order_by
        )

    async def count_albums_by_compilation_and_artist(self, compilation: int, artist_id: int) -> int:
        return await queries_albums.count_albums_by_compilation_and_artist(
            self._require_conn(), compilation, artist_id
        )

    async def list_albums_with_track_counts_by_compilation_and_artist(
        self,
        compilation: int,
        artist_id: int,
        *,
        limit: int = 500,
        offset: int = 0,
        order_by: str = "album",
    ) -> list[dict[str, Any]]:
        return await queries_albums.list_albums_with_track_counts_by_compilation_and_artist(
            self._require_conn(),
            compilation,
            artist_id,
            limit=limit,
            offset=offset,
            order_by=order_by,
        )

    async def count_albums_by_compilation_artist_and_year(
        self, compilation: int, artist_id: int, year: int
    ) -> int:
        return await queries_albums.count_albums_by_compilation_artist_and_year(
            self._require_conn(), compilation, artist_id, year
        )

    async def list_albums_with_track_counts_by_compilation_artist_and_year(
        self,
        compilation: int,
        artist_id: int,
        year: int,
        *,
        limit: int = 500,
        offset: int = 0,
        order_by: str = "album",
    ) -> list[dict[str, Any]]:
        return await queries_albums.list_albums_with_track_counts_by_compilation_artist_and_year(
            self._require_conn(),
            compilation,
            artist_id,
            year,
            limit=limit,
            offset=offset,
            order_by=order_by,
        )

    async def count_albums_by_compilation_and_genre_id(
        self, compilation: int, genre_id: int
    ) -> int:
        return await queries_albums.count_albums_by_compilation_and_genre_id(
            self._require_conn(), compilation, genre_id
        )

    async def list_albums_with_track_counts_by_compilation_and_genre_id(
        self,
        compilation: int,
        genre_id: int,
        *,
        limit: int = 500,
        offset: int = 0,
        order_by: str = "album",
    ) -> list[dict[str, Any]]:
        return await queries_albums.list_albums_with_track_counts_by_compilation_and_genre_id(
            self._require_conn(),
            compilation,
            genre_id,
            limit=limit,
            offset=offset,
            order_by=order_by,
        )

    # Album filters: genre_id
    async def count_albums_by_genre_id(self, genre_id: int) -> int:
        return await queries_albums.count_albums_by_genre_id(self._require_conn(), genre_id)

    async def list_albums_by_genre_id(
        self, genre_id: int, *, limit: int = 500, offset: int = 0, order_by: str = "album"
    ) -> list[dict[str, Any]]:
        return await queries_albums.list_albums_by_genre_id(
            self._require_conn(), genre_id, limit=limit, offset=offset, order_by=order_by
        )

    async def count_albums_by_genre_and_year(self, genre_id: int, year: int) -> int:
        return await queries_albums.count_albums_by_genre_and_year(
            self._require_conn(), genre_id, year
        )

    async def list_albums_by_genre_and_year(
        self,
        genre_id: int,
        year: int,
        *,
        limit: int = 500,
        offset: int = 0,
        order_by: str = "album",
    ) -> list[dict[str, Any]]:
        return await queries_albums.list_albums_by_genre_and_year(
            self._require_conn(), genre_id, year, limit=limit, offset=offset, order_by=order_by
        )

    async def count_albums_by_genre_and_artist(self, genre_id: int, artist_id: int) -> int:
        return await queries_albums.count_albums_by_genre_and_artist(
            self._require_conn(), genre_id, artist_id
        )

    async def list_albums_by_genre_and_artist(
        self,
        genre_id: int,
        artist_id: int,
        *,
        limit: int = 500,
        offset: int = 0,
        order_by: str = "album",
    ) -> list[dict[str, Any]]:
        return await queries_albums.list_albums_by_genre_and_artist(
            self._require_conn(), genre_id, artist_id, limit=limit, offset=offset, order_by=order_by
        )

    async def count_albums_by_genre_artist_and_year(
        self, genre_id: int, artist_id: int, year: int
    ) -> int:
        return await queries_albums.count_albums_by_genre_artist_and_year(
            self._require_conn(), genre_id, artist_id, year
        )

    async def list_albums_by_genre_artist_and_year(
        self,
        genre_id: int,
        artist_id: int,
        year: int,
        *,
        limit: int = 500,
        offset: int = 0,
        order_by: str = "album",
    ) -> list[dict[str, Any]]:
        return await queries_albums.list_albums_by_genre_artist_and_year(
            self._require_conn(),
            genre_id,
            artist_id,
            year,
            limit=limit,
            offset=offset,
            order_by=order_by,
        )

    # Album filters: role_id
    async def count_albums_by_role_id(self, role_id: int) -> int:
        return await queries_albums.count_albums_by_role_id(self._require_conn(), role_id)

    async def list_albums_with_track_counts_by_role_id(
        self, role_id: int, *, limit: int = 500, offset: int = 0, order_by: str = "album"
    ) -> list[dict[str, Any]]:
        return await queries_albums.list_albums_with_track_counts_by_role_id(
            self._require_conn(), role_id, limit=limit, offset=offset, order_by=order_by
        )

    async def count_albums_by_role_and_genre_id(self, role_id: int, genre_id: int) -> int:
        return await queries_albums.count_albums_by_role_and_genre_id(
            self._require_conn(), role_id, genre_id
        )

    async def list_albums_with_track_counts_by_role_and_genre_id(
        self,
        role_id: int,
        genre_id: int,
        *,
        limit: int = 500,
        offset: int = 0,
        order_by: str = "album",
    ) -> list[dict[str, Any]]:
        return await queries_albums.list_albums_with_track_counts_by_role_and_genre_id(
            self._require_conn(), role_id, genre_id, limit=limit, offset=offset, order_by=order_by
        )

    async def count_albums_by_role_and_year(self, role_id: int, year: int) -> int:
        return await queries_albums.count_albums_by_role_and_year(
            self._require_conn(), role_id, year
        )

    async def list_albums_with_track_counts_by_role_and_year(
        self, role_id: int, year: int, *, limit: int = 500, offset: int = 0, order_by: str = "album"
    ) -> list[dict[str, Any]]:
        return await queries_albums.list_albums_with_track_counts_by_role_and_year(
            self._require_conn(), role_id, year, limit=limit, offset=offset, order_by=order_by
        )

    async def count_albums_by_role_and_compilation(self, role_id: int, compilation: int) -> int:
        return await queries_albums.count_albums_by_role_and_compilation(
            self._require_conn(), role_id, compilation
        )

    async def list_albums_with_track_counts_by_role_and_compilation(
        self,
        role_id: int,
        compilation: int,
        *,
        limit: int = 500,
        offset: int = 0,
        order_by: str = "album",
    ) -> list[dict[str, Any]]:
        return await queries_albums.list_albums_with_track_counts_by_role_and_compilation(
            self._require_conn(),
            role_id,
            compilation,
            limit=limit,
            offset=offset,
            order_by=order_by,
        )

    # ===========================================================================
    # Meta queries: Genres, Roles, Music Folders (delegated to queries_meta)
    # ===========================================================================

    # Genres
    async def list_genres(self, *, limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
        return await queries_meta.list_genres(self._require_conn(), limit=limit, offset=offset)

    async def count_genres(self) -> int:
        return await queries_meta.count_genres(self._require_conn())

    # Years
    async def get_distinct_years(self) -> list[int]:
        """Return a list of distinct years from tracks, sorted descending."""
        conn = self._require_conn()
        cursor = await conn.execute(
            """
            SELECT DISTINCT year FROM tracks
            WHERE year IS NOT NULL AND year > 0
            ORDER BY year DESC
            """
        )
        rows = await cursor.fetchall()
        return [row[0] for row in rows]

    async def get_genre_by_id(self, genre_id: int) -> dict[str, Any] | None:
        return await queries_meta.get_genre_by_id(self._require_conn(), genre_id)

    # Roles
    async def list_roles(self, *, limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
        return await queries_meta.list_roles(self._require_conn(), limit=limit, offset=offset)

    async def count_roles(self) -> int:
        return await queries_meta.count_roles(self._require_conn())

    # Music folders
    async def add_music_folder(self, path: str) -> int:
        return await queries_meta.add_music_folder(self._require_conn(), path)

    async def remove_music_folder(self, path: str) -> None:
        return await queries_meta.remove_music_folder(self._require_conn(), path)

    async def list_music_folders(self) -> list[str]:
        return await queries_meta.list_music_folders(self._require_conn())

    async def set_music_folders(self, paths: list[str]) -> None:
        return await queries_meta.set_music_folders(self._require_conn(), paths)

    async def clear_music_folders(self) -> None:
        return await queries_meta.clear_music_folders(self._require_conn())

    # Track count helper for artist (moved from queries_artists for convenience)
    async def get_artist_track_count(self, artist_id: int) -> int:
        conn = self._require_conn()
        cursor = await conn.execute(
            "SELECT COUNT(*) AS c FROM tracks WHERE artist_id = ?;",
            (int(artist_id),),
        )
        row = await cursor.fetchone()
        return int(row["c"]) if row else 0

    # ===========================================================================
    # FTS5 full-text search helpers
    # ===========================================================================

    async def rebuild_fts(self) -> None:
        """Rebuild the FTS5 index from the tracks table.

        Should be called after a full rescan (``force=True``) or after bulk
        deletions to ensure the FTS index is consistent.  This is a no-op if
        the ``tracks_fts`` virtual table does not exist (schema < v9).
        """
        conn = self._require_conn()
        # Check whether the FTS table exists before issuing the rebuild command.
        cursor = await conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tracks_fts' LIMIT 1;"
        )
        if await cursor.fetchone() is None:
            return
        await conn.execute("INSERT INTO tracks_fts(tracks_fts) VALUES('rebuild');")
        await conn.commit()

    # ===========================================================================
    # Incremental rescan helpers
    # ===========================================================================

    async def get_track_mtime_index(self) -> dict[str, tuple[int, int]]:
        """Return a mapping of ``path → (mtime_ns, file_size)`` for all tracks.

        Used by the scanner to skip files whose mtime and size have not changed
        since the last scan (incremental rescan).
        """
        conn = self._require_conn()
        cursor = await conn.execute(
            "SELECT path, mtime_ns, file_size FROM tracks WHERE mtime_ns IS NOT NULL AND file_size IS NOT NULL;"
        )
        rows = await cursor.fetchall()
        return {
            str(row["path"]): (int(row["mtime_ns"]), int(row["file_size"]))
            for row in rows
        }

    async def delete_tracks_not_in_paths(
        self, valid_paths: set[str], scan_root: str
    ) -> int:
        """Delete tracks whose path starts with *scan_root* but is not in *valid_paths*.

        This removes "orphaned" DB entries for files that have been deleted from
        disk since the last scan.

        Args:
            valid_paths: Set of absolute path strings that were found during the
                current scan.
            scan_root: Only tracks whose ``path`` starts with this prefix are
                considered for deletion (so tracks from other scan roots are
                left untouched).

        Returns:
            Number of deleted tracks.
        """
        conn = self._require_conn()

        # Fetch all DB paths under this scan root.
        prefix = scan_root.rstrip("/").rstrip("\\")
        cursor = await conn.execute(
            "SELECT path FROM tracks WHERE path LIKE ? || '%';",
            (prefix,),
        )
        rows = await cursor.fetchall()

        to_delete = [str(row["path"]) for row in rows if str(row["path"]) not in valid_paths]

        if not to_delete:
            return 0

        # Delete in batches to stay within SQLite's variable limit.
        deleted = 0
        batch_size = 500
        for i in range(0, len(to_delete), batch_size):
            batch = to_delete[i : i + batch_size]
            placeholders = ",".join("?" for _ in batch)
            cursor = await conn.execute(
                f"DELETE FROM tracks WHERE path IN ({placeholders});",
                batch,
            )
            deleted += cursor.rowcount

        await conn.commit()
        return deleted
