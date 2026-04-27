from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NewType

from resonance.core.library_db import LibraryDb, UpsertTrack
from resonance.core.scanner import ScanConfig, scan_music_folder

logger = logging.getLogger(__name__)

ArtistId = NewType("ArtistId", int)
AlbumId = NewType("AlbumId", int)
TrackId = NewType("TrackId", int)


@dataclass(frozen=True, slots=True)
class Artist:
    id: ArtistId
    name: str
    album_count: int | None = None
    track_count: int | None = None


@dataclass(frozen=True, slots=True)
class Album:
    id: AlbumId
    title: str
    artist_id: ArtistId | None = None
    artist_name: str | None = None
    year: int | None = None
    track_count: int | None = None


@dataclass(frozen=True, slots=True)
class Track:
    id: TrackId
    path: str
    title: str
    artist_id: ArtistId | None = None
    album_id: AlbumId | None = None
    artist_name: str | None = None
    album_title: str | None = None
    year: int | None = None
    duration_ms: int | None = None
    disc_no: int | None = None
    track_no: int | None = None
    compilation: int = 0
    # Audio quality metadata
    sample_rate: int | None = None
    bit_depth: int | None = None
    bitrate: int | None = None
    channels: int | None = None


@dataclass(frozen=True, slots=True)
class ScanResult:
    scanned_files: int
    added_tracks: int
    updated_tracks: int
    skipped_files: int
    errors: int
    deleted_tracks: int = 0


@dataclass
class ScanStatus:
    """Status of a running or completed scan."""

    is_running: bool = False
    progress: float = 0.0  # 0.0 to 1.0
    current_folder: str = ""
    folders_total: int = 0
    folders_done: int = 0
    tracks_found: int = 0
    errors: int = 0
    last_result: ScanResult | None = None


@dataclass(frozen=True, slots=True)
class SearchResult:
    artists: tuple[Artist, ...]
    albums: tuple[Album, ...]
    tracks: tuple[Track, ...]


class MusicLibraryError(RuntimeError):
    """Base error for MusicLibrary operations."""


class MusicLibraryNotReadyError(MusicLibraryError):
    """Raised when read operations are attempted before the library is initialized."""


class MusicLibrary:
    """
    High-level facade for the Resonance music library.

    This is intentionally NOT an LMS port. We keep:
    - a small schema (tracks as canonical source)
    - simple browse queries (DISTINCT artist/album strings)
    - a clear async interface for UI layers (web/jsonrpc/cli)

    Dependencies:
    - `LibraryDb` for persistence
    - `scanner` for tag extraction
    """

    def __init__(self, *, db: LibraryDb, music_root: Path | None = None) -> None:
        self._db = db
        self._music_root = music_root
        self._initialized = False
        self._scan_status = ScanStatus()
        self._scan_task: asyncio.Task | None = None

    @property
    def initialized(self) -> bool:
        return self._initialized

    @property
    def music_root(self) -> Path | None:
        return self._music_root

    @property
    def scan_status(self) -> ScanStatus:
        """Get the current scan status."""
        return self._scan_status

    @property
    def is_scanning(self) -> bool:
        """Check if a scan is currently running."""
        return self._scan_status.is_running

    async def initialize(self) -> None:
        """
        Initialize underlying storage and prepare the library.

        Contract:
        - `LibraryDb` must already be open.
        - schema/migrations are ensured here for convenience.
        """
        if not self._db.is_open:
            raise MusicLibraryError(
                "LibraryDb is not open. Open it before initializing MusicLibrary."
            )

        await self._db.ensure_schema()
        self._initialized = True

    async def scan(
        self,
        *,
        roots: Sequence[Path] | None = None,
        force: bool = False,
    ) -> ScanResult:
        """
        Scan music folders and update the library DB.

        We keep scanning & persistence separate internally:
        - scanner returns normalized metadata
        - db upserts rows (idempotent)

        Args:
            roots: Directories to scan.  Falls back to ``self._music_root``.
            force: If ``True``, skip the mtime/size optimisation and re-read
                every file's tags unconditionally (full rescan).
        """
        self._require_initialized()

        scan_roots = (
            list(roots) if roots is not None else ([self._music_root] if self._music_root else [])
        )
        if not scan_roots:
            raise MusicLibraryError("No scan roots provided and no music_root configured.")

        # Pre-load the mtime index so we can skip unchanged files.
        mtime_index: dict[str, tuple[int, int]] = {}
        if not force:
            mtime_index = await self._db.get_track_mtime_index()

        scanned_files = 0
        skipped_files = 0
        errors = 0
        upserted = 0
        total_deleted = 0

        for root in scan_roots:
            scan_cfg = ScanConfig(root=root)
            result = await scan_music_folder(scan_cfg)

            scanned_files += len(result.tracks) + len(result.issues)
            errors += len(result.issues)

            # Collect every path the scanner found under this root so we can
            # detect orphans (files deleted from disk since the last scan).
            scanned_paths: set[str] = set()

            to_upsert: list[UpsertTrack] = []
            for tm in result.tracks:
                path_str = str(tm.path)
                scanned_paths.add(path_str)

                stat = tm.path.stat()

                # mtime-skip: if the file has not changed, skip tag extraction / upsert.
                if not force:
                    existing = mtime_index.get(path_str)
                    if existing and existing[0] == stat.st_mtime_ns and existing[1] == stat.st_size:
                        skipped_files += 1
                        continue

                to_upsert.append(
                    UpsertTrack(
                        path=path_str,
                        title=tm.title,
                        artist=tm.artist,
                        album=tm.album,
                        album_artist=tm.album_artist,
                        track_no=tm.track_number,
                        disc_no=tm.disc_number,
                        year=tm.year,
                        duration_ms=tm.duration_ms,
                        file_size=stat.st_size,
                        mtime_ns=stat.st_mtime_ns,
                        has_artwork=tm.has_artwork,
                        genres=tm.genres,
                        compilation=tm.compilation,
                        contributors=tm.contributors,
                        sample_rate=tm.sample_rate,
                        bit_depth=tm.bit_depth,
                        bitrate=tm.bitrate,
                        channels=tm.channels,
                    )
                )

            upserted += await self._db.upsert_tracks(to_upsert)

            # Orphan detection: remove DB entries for files that no longer
            # exist on disk under this scan root.
            deleted = await self._db.delete_tracks_not_in_paths(scanned_paths, str(root))
            if deleted:
                logger.info("Removed %d orphaned track(s) under %s", deleted, root)
                await self._db.cleanup_orphans()
            total_deleted += deleted

        # Rebuild FTS5 index when a full rescan was requested or orphaned
        # tracks were removed.  The triggers keep the index in sync for
        # normal upserts, but bulk deletes and force-rescans benefit from
        # a full rebuild to guarantee consistency.
        if force or total_deleted > 0:
            await self._db.rebuild_fts()

        return ScanResult(
            scanned_files=scanned_files,
            added_tracks=0,
            updated_tracks=upserted,
            skipped_files=skipped_files,
            errors=errors,
            deleted_tracks=total_deleted,
        )

    # ---- Browse APIs (build the web UI / JSON-RPC on top of these) ----

    async def get_artists(self, *, offset: int = 0, limit: int = 100) -> tuple[Artist, ...]:
        """
        Return artists with stable IDs (artists table, schema v4+).
        """
        self._require_initialized()
        self._validate_paging(offset=offset, limit=limit)

        rows = await self._db.list_all_artists(limit=limit, offset=offset)
        return tuple(Artist(id=ArtistId(r["id"]), name=r["name"]) for r in rows)

    async def get_albums(
        self,
        *,
        artist_id: ArtistId | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[Album, ...]:
        """
        Return albums with stable IDs (albums table, schema v4+).

        If artist_id is provided, returns albums for that artist.
        """
        self._require_initialized()
        self._validate_paging(offset=offset, limit=limit)

        if artist_id is None:
            rows = await self._db.list_all_albums(limit=limit, offset=offset)
        else:
            rows = await self._db.list_albums_by_artist(int(artist_id), limit=limit, offset=offset)

        # Album model currently expects (id, title). We keep that shape and let
        # higher layers decide whether to also show artist/year from other endpoints.
        return tuple(Album(id=AlbumId(r.id), title=r.title) for r in rows)

    async def get_years(self) -> list[int]:
        """
        Return a list of unique years from the library.

        Returns:
            List of years (integers), excluding None/0 values.
        """
        self._require_initialized()
        rows = await self._db.get_distinct_years()
        return [r for r in rows if r and r > 0]

    async def get_tracks(
        self,
        *,
        album_id: AlbumId | None = None,
        offset: int = 0,
        limit: int = 200,
    ) -> tuple[Track, ...]:
        """
        Return tracks.

        If album_id is provided, returns tracks for that album.
        """
        self._require_initialized()
        self._validate_paging(offset=offset, limit=limit)

        if album_id is None:
            rows = await self._db.list_tracks(limit=limit, offset=offset, order_by="artist")
        else:
            rows = await self._db.list_tracks_by_album(int(album_id), limit=limit, offset=offset)

        return tuple(
            Track(
                id=TrackId(r.id),
                path=r.path,
                title=r.title or Path(r.path).stem,
                artist_id=ArtistId(r.artist_id) if r.artist_id else None,
                album_id=AlbumId(r.album_id) if r.album_id else None,
                artist_name=r.artist,
                album_title=r.album,
                year=r.year,
                duration_ms=r.duration_ms,
                disc_no=r.disc_no,
                track_no=r.track_no,
            )
            for r in rows
        )

    async def get_track_by_id(self, track_id: TrackId) -> Track | None:
        self._require_initialized()
        row = await self._db.get_track_by_id(int(track_id))
        if row is None:
            return None
        return Track(
            id=TrackId(row.id),
            path=row.path,
            title=row.title or Path(row.path).stem,
            artist_id=ArtistId(row.artist_id) if row.artist_id else None,
            album_id=AlbumId(row.album_id) if row.album_id else None,
            artist_name=row.artist,
            album_title=row.album,
            year=row.year,
            duration_ms=row.duration_ms,
            disc_no=row.disc_no,
            track_no=row.track_no,
        )

    async def get_track_by_path(self, path: str | Path) -> Track | None:
        """
        Lookup by file path. `path` may be absolute or relative.
        The DB stores whatever the scanner wrote; for MVP we do exact matching.
        """
        self._require_initialized()
        row = await self._db.get_track_by_path(str(path))
        if row is None:
            return None
        return Track(
            id=TrackId(row.id),
            path=row.path,
            title=row.title or Path(row.path).stem,
            artist_id=ArtistId(row.artist_id) if row.artist_id else None,
            album_id=AlbumId(row.album_id) if row.album_id else None,
            artist_name=row.artist,
            album_title=row.album,
            year=row.year,
            duration_ms=row.duration_ms,
            disc_no=row.disc_no,
            track_no=row.track_no,
        )

    async def search(self, query: str, *, limit: int = 50) -> SearchResult:
        self._require_initialized()
        if not query.strip():
            return SearchResult(artists=tuple(), albums=tuple(), tracks=tuple())
        if limit <= 0:
            raise ValueError("limit must be > 0")

        rows = await self._db.search_tracks(query, limit=limit, offset=0)
        tracks = tuple(
            Track(
                id=TrackId(r.id),
                path=r.path,
                title=r.title or Path(r.path).stem,
                artist_id=ArtistId(r.artist_id) if r.artist_id else None,
                album_id=AlbumId(r.album_id) if r.album_id else None,
                artist_name=r.artist,
                album_title=r.album,
                year=r.year,
                duration_ms=r.duration_ms,
                disc_no=r.disc_no,
                track_no=r.track_no,
            )
            for r in rows
        )

        # MVP: we don't compute separate artist/album results yet; tracks cover the UX.
        return SearchResult(artists=tuple(), albums=tuple(), tracks=tracks)

    # ---- Utilities ----

    def iter_supported_extensions(self) -> Iterable[str]:
        """
        Central place to define which file extensions we consider for scanning.

        MVP-first: keep it small and proven end-to-end.
        Scanner currently supports a larger set; this is the policy knob.
        """
        return (".mp3", ".flac", ".ogg", ".opus", ".m4a", ".m4b", ".aac")

    # ---- Music Folder Management ----

    async def get_music_folders(self) -> list[str]:
        """Get the list of configured music folders."""
        self._require_initialized()
        return await self._db.list_music_folders()

    async def add_music_folder(self, path: str | Path) -> int:
        """
        Add a music folder to the library.

        Args:
            path: Path to the folder to add.

        Returns:
            The folder id.
        """
        self._require_initialized()
        folder_path = Path(path).resolve()
        if not folder_path.exists():
            raise MusicLibraryError(f"Folder does not exist: {folder_path}")
        if not folder_path.is_dir():
            raise MusicLibraryError(f"Path is not a directory: {folder_path}")

        folder_id = await self._db.add_music_folder(str(folder_path))
        logger.info("Added music folder: %s", folder_path)
        return folder_id

    async def remove_music_folder(self, path: str | Path) -> bool:
        """
        Remove a music folder from the library.

        Deletes the folder entry, removes all tracks whose path starts with
        the folder prefix, and cleans up orphaned albums/artists/genres.

        If the exact path is not registered, falls back to a prefix match:
        all registered folders that lie under the given path are removed.

        Args:
            path: Path to the folder to remove.

        Returns:
            True if at least one folder was removed.
        """
        self._require_initialized()
        folder_path = str(Path(path).resolve())
        removed = await self._db.remove_music_folder(folder_path)

        if removed:
            # Delete all tracks that belong to this folder (empty valid_paths
            # means every track under the prefix is orphaned).
            deleted = await self._db.delete_tracks_not_in_paths(set(), folder_path)
            if deleted:
                logger.info(
                    "Removed %d track(s) from removed folder %s",
                    deleted,
                    folder_path,
                )
                await self._db.cleanup_orphans()
                await self._db.rebuild_fts()
            logger.info("Removed music folder: %s", folder_path)
            return True

        # Exact path didn't match — try prefix match: maybe the user is
        # removing a parent folder and the DB has subfolders registered.
        remaining_folders = await self._db.list_music_folders()
        prefix = folder_path.rstrip("/")
        matched_folders = [
            f for f in remaining_folders if f.startswith(prefix + "/") or f == prefix
        ]
        if matched_folders:
            total_deleted = 0
            for mf in matched_folders:
                await self._db.remove_music_folder(mf)
                deleted = await self._db.delete_tracks_not_in_paths(set(), mf)
                total_deleted += deleted
                logger.info("Removed subfolder %s (%d tracks)", mf, deleted)
            if total_deleted:
                await self._db.cleanup_orphans()
                await self._db.rebuild_fts()
            logger.info(
                "Removed %d subfolder(s) under %s (%d tracks total)",
                len(matched_folders),
                folder_path,
                total_deleted,
            )
            return True

        logger.warning("Music folder not found in library: %s", folder_path)
        return False

    async def set_music_folders(self, paths: list[str | Path]) -> int:
        """
        Replace all music folders with a new list.

        Args:
            paths: List of folder paths to set.

        Returns:
            Number of folders set.
        """
        self._require_initialized()
        # Validate all paths first
        resolved = []
        for p in paths:
            folder_path = Path(p).resolve()
            if not folder_path.exists():
                raise MusicLibraryError(f"Folder does not exist: {folder_path}")
            if not folder_path.is_dir():
                raise MusicLibraryError(f"Path is not a directory: {folder_path}")
            resolved.append(str(folder_path))

        count = await self._db.set_music_folders(resolved)
        logger.info("Set %d music folders", count)
        return count

    # ---- Background Scan ----

    async def start_scan(self) -> bool:
        """
        Start a background scan of all configured music folders.

        Returns:
            True if scan started, False if already running.
        """
        self._require_initialized()

        if self._scan_status.is_running:
            logger.warning("Scan already in progress")
            return False

        # Get folders to scan
        folders = await self._db.list_music_folders()
        if not folders:
            logger.warning("No music folders configured")
            return False

        # Start background task
        self._scan_task = asyncio.create_task(self._run_scan(folders))
        return True

    async def _run_scan(self, folders: list[str]) -> None:
        """Run the scan in the background."""
        self._scan_status = ScanStatus(
            is_running=True,
            folders_total=len(folders),
        )

        total_scanned = 0
        total_upserted = 0
        total_errors = 0

        try:
            for i, folder in enumerate(folders):
                self._scan_status.current_folder = folder
                self._scan_status.folders_done = i
                self._scan_status.progress = i / len(folders) if folders else 0

                logger.info("Scanning folder %d/%d: %s", i + 1, len(folders), folder)

                try:
                    result = await self.scan(roots=[Path(folder)])
                    total_scanned += result.scanned_files
                    total_upserted += result.updated_tracks
                    total_errors += result.errors
                    self._scan_status.tracks_found = total_upserted
                    self._scan_status.errors = total_errors
                except Exception as e:
                    logger.error("Error scanning folder %s: %s", folder, e)
                    total_errors += 1
                    self._scan_status.errors = total_errors

            self._scan_status.folders_done = len(folders)
            self._scan_status.progress = 1.0
            self._scan_status.last_result = ScanResult(
                scanned_files=total_scanned,
                added_tracks=0,
                updated_tracks=total_upserted,
                skipped_files=0,
                errors=total_errors,
                deleted_tracks=0,
            )
            logger.info(
                "Scan complete: %d files, %d tracks, %d errors",
                total_scanned,
                total_upserted,
                total_errors,
            )

        finally:
            self._scan_status.is_running = False
            self._scan_status.current_folder = ""

    async def rescan(self) -> bool:
        """
        Convenience method to trigger a rescan.

        Same as start_scan(), but clearer intent.
        """
        return await self.start_scan()

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise MusicLibraryNotReadyError(
                "MusicLibrary is not initialized. Call await MusicLibrary.initialize() first."
            )

    @staticmethod
    def _validate_paging(*, offset: int, limit: int) -> None:
        if offset < 0:
            raise ValueError("offset must be >= 0")
        if limit <= 0:
            raise ValueError("limit must be > 0")
        if limit > 10_000:
            raise ValueError("limit is unreasonably large")
