"""
Playlist management for Resonance.

This module provides playlist/queue functionality for each player.
Each player has its own playlist (queue) of tracks that can be
played sequentially.

Design decisions:
- Simple list-based queue (not a database table for MVP)
- Each PlayerClient gets its own Playlist instance
- Supports basic operations: add, play, clear, next, previous
- Track references are by ID (TrackId) or path string
- Playlists are persisted as JSON files in a configurable directory
  so they survive server restarts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, NewType

logger = logging.getLogger(__name__)

# Use NewType for type safety, but it's just an int at runtime
ArtistId = NewType("ArtistId", int)
AlbumId = NewType("AlbumId", int)
TrackId = NewType("TrackId", int)


class RepeatMode(Enum):
    """Repeat mode for playlist."""

    OFF = 0  # No repeat
    ONE = 1  # Repeat current track
    ALL = 2  # Repeat entire playlist


class ShuffleMode(Enum):
    """Shuffle mode for playlist."""

    OFF = 0
    ON = 1


@dataclass(frozen=True, slots=True)
class PlaylistTrack:
    """
    A track in the playlist.

    We store both track_id (for DB lookups) and path (for streaming).
    This allows the playlist to work even if the DB is not available.

    For remote streams (Internet Radio, Podcasts, external URLs), ``path``
    holds the canonical URL and ``is_remote`` is ``True``.  The optional
    ``stream_url`` field carries the *resolved* streaming URL when it
    differs from ``path`` (e.g. after a playlist-URL has been resolved to
    a direct audio stream).
    """

    track_id: TrackId | None
    path: str
    album_id: AlbumId | None = None
    artist_id: ArtistId | None = None
    title: str = ""
    artist: str = ""
    album: str = ""
    duration_ms: int = 0

    # --- Remote / Content-Provider fields ---
    source: str = "local"
    """Origin of this track: ``"local"`` (default), ``"radio"``, ``"podcast"``, ``"external"``."""

    stream_url: str | None = None
    """Resolved stream URL.  For local tracks this is ``None``; for remote
    tracks it may differ from *path* (e.g. a redirect-resolved direct URL)."""

    external_id: str | None = None
    """Provider-specific identifier (e.g. TuneIn station ID, podcast GUID)."""

    artwork_url: str | None = None
    """Remote artwork URL for tracks that have no local cover art."""

    is_remote: bool = False
    """``True`` when the track references a remote URL rather than a local file."""

    content_type: str | None = None
    """MIME type hint from the content provider (e.g. ``"audio/mpeg"``)."""

    bitrate: int = 0
    """Bitrate in kbps as reported by the content provider (0 = unknown)."""

    is_live: bool = False
    """``True`` for live/infinite streams (Internet Radio) with no defined duration."""

    @classmethod
    def from_path(cls, path: str | Path) -> PlaylistTrack:
        """Create a playlist track from just a file path."""
        from pathlib import Path as PathLib

        p = PathLib(path) if isinstance(path, str) else path
        return cls(
            track_id=None,
            path=str(p),
            album_id=None,
            artist_id=None,
            title=p.stem,
        )

    @classmethod
    def from_url(
        cls,
        url: str,
        *,
        title: str = "",
        artist: str = "",
        album: str = "",
        duration_ms: int = 0,
        source: str = "external",
        stream_url: str | None = None,
        external_id: str | None = None,
        artwork_url: str | None = None,
        content_type: str | None = None,
        bitrate: int = 0,
        is_live: bool = False,
    ) -> PlaylistTrack:
        """Create a playlist track from a remote URL.

        Args:
            url: Canonical URL for this track (stored in ``path``).
            title: Display title (falls back to the URL if empty).
            artist: Artist / station name.
            album: Album / show name.
            duration_ms: Duration in ms (0 for live streams).
            source: Origin tag — ``"radio"``, ``"podcast"``, ``"external"``.
            stream_url: Resolved direct stream URL if different from *url*.
            external_id: Provider-specific ID.
            artwork_url: Remote cover art URL.
            content_type: MIME type hint.
            bitrate: Bitrate in kbps.
            is_live: Whether this is an infinite live stream.
        """
        return cls(
            track_id=None,
            path=url,
            album_id=None,
            artist_id=None,
            title=title or url.rsplit("/", 1)[-1],
            artist=artist,
            album=album,
            duration_ms=duration_ms,
            source=source,
            stream_url=stream_url,
            external_id=external_id,
            artwork_url=artwork_url,
            is_remote=True,
            content_type=content_type,
            bitrate=bitrate,
            is_live=is_live,
        )

    @property
    def effective_stream_url(self) -> str:
        """Return the URL to actually stream from.

        For remote tracks this is ``stream_url`` (if set) or ``path``.
        For local tracks this returns ``path`` unchanged.
        """
        if self.is_remote and self.stream_url:
            return self.stream_url
        return self.path


@dataclass
class Playlist:
    """
    Playlist (queue) for a single player.

    This class manages an ordered list of tracks that will be played
    sequentially. It supports:
    - Adding tracks (at end or at specific position)
    - Removing tracks
    - Navigation (next, previous, jump to index)
    - Repeat and shuffle modes

    The playlist is in-memory only (not persisted to DB in MVP).
    """

    player_id: str
    tracks: list[PlaylistTrack] = field(default_factory=list)
    current_index: int = 0
    repeat_mode: RepeatMode = RepeatMode.OFF
    shuffle_mode: ShuffleMode = ShuffleMode.OFF

    # Epoch timestamp of last playlist mutation (load/add/clear/next/prev/shuffle).
    # JiveLite requires "playlist_timestamp" in the status response to highlight
    # the current track — DB.lua stores it as self.ts and playlistIndex()
    # returns nil when ts is falsy.
    updated_at: float = field(default_factory=time.time)

    # Original order (for unshuffle)
    _original_order: list[PlaylistTrack] = field(default_factory=list)

    # Dirty flag — set by _touch(), cleared after successful save.
    _dirty: bool = field(default=False, repr=False)

    def __len__(self) -> int:
        """Return number of tracks in playlist."""
        return len(self.tracks)

    def _touch(self) -> None:
        """Update the mutation timestamp and mark dirty for persistence."""
        self.updated_at = time.time()
        self._dirty = True

    @property
    def is_empty(self) -> bool:
        """Check if playlist is empty."""
        return len(self.tracks) == 0

    @property
    def current_track(self) -> PlaylistTrack | None:
        """Get the current track, or None if playlist is empty."""
        if self.is_empty or self.current_index >= len(self.tracks):
            return None
        return self.tracks[self.current_index]

    @property
    def has_next(self) -> bool:
        """Check if there's a next track available."""
        if self.is_empty:
            return False
        if self.repeat_mode in (RepeatMode.ONE, RepeatMode.ALL):
            return True
        return self.current_index < len(self.tracks) - 1

    @property
    def has_previous(self) -> bool:
        """Check if there's a previous track available."""
        if self.is_empty:
            return False
        if self.repeat_mode in (RepeatMode.ONE, RepeatMode.ALL):
            return True
        return self.current_index > 0

    def add(self, track: PlaylistTrack, *, position: int | None = None) -> int:
        """
        Add a track to the playlist.

        Args:
            track: The track to add.
            position: Optional position to insert at. None = append at end.

        Returns:
            The index where the track was inserted.
        """
        old_current_index = self.current_index
        was_empty = self.is_empty

        if position is None:
            self.tracks.append(track)
            idx = len(self.tracks) - 1
        else:
            position = max(0, min(position, len(self.tracks)))
            self.tracks.insert(position, track)
            idx = position
            # Adjust current_index if we inserted before it.
            #
            # IMPORTANT:
            # - When the playlist is empty, current_index is 0 and must remain 0.
            #   Shifting it to 1 would make the newly inserted first track "not current",
            #   which can manifest as immediately playing track +1 after a manual start.
            if not was_empty and position <= self.current_index:
                self.current_index += 1

        self._touch()
        logger.info(
            "playlist.add: track=%s, position=%s, idx=%d, current_index: %d -> %d, len=%d",
            track.title or track.path,
            position,
            idx,
            old_current_index,
            self.current_index,
            len(self.tracks),
        )
        return idx

    def add_path(self, path: str | Path, *, position: int | None = None) -> int:
        """
        Convenience method to add a track by path only.

        Args:
            path: Path to the audio file.
            position: Optional position to insert at.

        Returns:
            The index where the track was inserted.
        """
        track = PlaylistTrack.from_path(path)
        return self.add(track, position=position)

    def remove(self, index: int) -> PlaylistTrack | None:
        """
        Remove a track at the given index.

        Args:
            index: Index of track to remove.

        Returns:
            The removed track, or None if index was invalid.
        """
        if index < 0 or index >= len(self.tracks):
            return None

        track = self.tracks.pop(index)

        # Adjust current_index
        if index < self.current_index:
            self.current_index -= 1
        elif index == self.current_index and self.current_index >= len(self.tracks):
            self.current_index = max(0, len(self.tracks) - 1)

        self._touch()
        logger.debug("Removed track at index %d from playlist %s", index, self.player_id)
        return track

    def clear(self) -> int:
        """
        Clear all tracks from the playlist.

        Returns:
            Number of tracks that were cleared.
        """
        count = len(self.tracks)
        self.tracks.clear()
        self._original_order.clear()
        self.current_index = 0
        self._touch()
        logger.info("playlist.clear: cleared %d tracks, current_index reset to 0", count)
        return count

    def play(self, index: int = 0) -> PlaylistTrack | None:
        """
        Start playing from a specific index.

        Args:
            index: The index to start playing from.

        Returns:
            The track at the specified index, or None if invalid.
        """
        if self.is_empty:
            logger.info("playlist.play: playlist is empty, returning None")
            return None

        old_index = self.current_index
        index = max(0, min(index, len(self.tracks) - 1))
        self.current_index = index
        # NOTE: play() is navigation, NOT a content mutation.
        # LMS does NOT update currentPlaylistUpdateTime for playlist jump/index.
        # Updating playlist_timestamp here would cause JiveLite's DB.lua to
        # reset self.store on every track change → brief empty-playlist flash.
        track = self.current_track
        logger.info(
            "playlist.play: index=%d (requested), current_index: %d -> %d, track=%s",
            index,
            old_index,
            self.current_index,
            track.title if track else None,
        )
        return track

    def peek_next(self) -> PlaylistTrack | None:
        """
        Peek at the next track without advancing the index.

        Used by the crossfade/prefetch engine to prepare the next track
        before the current one finishes (STMd → prefetch).

        Respects repeat mode:
        - OFF: Returns None if at end
        - ONE: Returns current track (will replay)
        - ALL: Wraps to beginning

        Returns:
            The next track, or None if no next track available.
        """
        if self.is_empty:
            return None
        if self.repeat_mode == RepeatMode.ONE:
            return self.current_track
        if self.current_index < len(self.tracks) - 1:
            return self.tracks[self.current_index + 1]
        if self.repeat_mode == RepeatMode.ALL:
            return self.tracks[0]
        return None

    def next(self) -> PlaylistTrack | None:
        """
        Move to the next track.

        Respects repeat mode:
        - OFF: Returns None if at end
        - ONE: Returns same track
        - ALL: Wraps to beginning

        Returns:
            The next track, or None if no next track.
        """
        if self.is_empty:
            return None

        if self.repeat_mode == RepeatMode.ONE:
            return self.current_track

        if self.current_index < len(self.tracks) - 1:
            self.current_index += 1
        elif self.repeat_mode == RepeatMode.ALL:
            self.current_index = 0
        else:
            return None

        # NOTE: next() is navigation, NOT a content mutation.
        # LMS does NOT update currentPlaylistUpdateTime for auto-advance.
        # Keeping playlist_timestamp unchanged lets JiveLite update the
        # highlighted index without resetting its item cache (no flash).
        return self.current_track

    def previous(self) -> PlaylistTrack | None:
        """
        Move to the previous track.

        Respects repeat mode:
        - OFF: Returns None if at beginning
        - ONE: Returns same track
        - ALL: Wraps to end

        Returns:
            The previous track, or None if no previous track.
        """
        if self.is_empty:
            return None

        if self.repeat_mode == RepeatMode.ONE:
            return self.current_track

        if self.current_index > 0:
            self.current_index -= 1
        elif self.repeat_mode == RepeatMode.ALL:
            self.current_index = len(self.tracks) - 1
        else:
            return None

        # NOTE: previous() is navigation, NOT a content mutation.
        # Same rationale as next() — see comment there.
        return self.current_track

    def set_repeat(self, mode: RepeatMode | int) -> None:
        """Set the repeat mode."""
        if isinstance(mode, int):
            mode = RepeatMode(mode)
        self.repeat_mode = mode
        # NOTE: LMS playlistRepeatCommand does NOT call
        # currentPlaylistUpdateTime — repeat is a playback preference,
        # not a playlist content change.
        logger.debug("Set repeat mode to %s for playlist %s", mode.name, self.player_id)

    def set_shuffle(self, mode: ShuffleMode | int) -> None:
        """Set the shuffle mode (basic implementation)."""
        if isinstance(mode, int):
            mode = ShuffleMode(mode)

        if mode == ShuffleMode.ON and self.shuffle_mode == ShuffleMode.OFF:
            # Enable shuffle: save original order and randomize
            import random

            self._original_order = list(self.tracks)
            current = self.current_track

            # Shuffle but keep current track at current position
            other_tracks = [t for t in self.tracks if t != current]
            random.shuffle(other_tracks)

            if current:
                self.tracks = [current, *other_tracks]
                self.current_index = 0
            else:
                self.tracks = other_tracks

        elif mode == ShuffleMode.OFF and self.shuffle_mode == ShuffleMode.ON:
            # Disable shuffle: restore original order
            if self._original_order:
                current = self.current_track
                self.tracks = list(self._original_order)
                # Find current track in restored order
                if current:
                    try:
                        self.current_index = self.tracks.index(current)
                    except ValueError:
                        self.current_index = 0
                self._original_order.clear()

        self.shuffle_mode = mode
        self._touch()
        logger.debug("Set shuffle mode to %s for playlist %s", mode.name, self.player_id)

    def get_tracks_info(self) -> list[dict[str, Any]]:
        """
        Get track info for JSON-RPC responses.

        Returns:
            List of track dictionaries suitable for JSON serialization.
        """
        result: list[dict[str, Any]] = []
        for i, track in enumerate(self.tracks):
            result.append(
                {
                    "playlist index": i,
                    "id": track.track_id,
                    "title": track.title,
                    "artist": track.artist,
                    "album": track.album,
                    "album_id": track.album_id,
                    "artist_id": track.artist_id,
                    "duration": track.duration_ms // 1000 if track.duration_ms else 0,
                    "url": track.path,
                }
            )
        return result


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

_PERSISTENCE_VERSION = 1


def _serialize_playlist(playlist: Playlist) -> dict[str, Any]:
    """Serialize a Playlist to a JSON-safe dict."""
    tracks: list[dict[str, Any]] = []
    for t in playlist.tracks:
        td: dict[str, Any] = {
            "track_id": t.track_id,
            "path": t.path,
            "title": t.title,
            "artist": t.artist,
            "album": t.album,
            "album_id": t.album_id,
            "artist_id": t.artist_id,
            "duration_ms": t.duration_ms,
        }
        # Persist remote-stream fields only when they carry non-default values
        # so that playlists with only local tracks stay compact.
        if t.is_remote:
            td["is_remote"] = True
            td["source"] = t.source
        if t.stream_url is not None:
            td["stream_url"] = t.stream_url
        if t.external_id is not None:
            td["external_id"] = t.external_id
        if t.artwork_url is not None:
            td["artwork_url"] = t.artwork_url
        if t.content_type is not None:
            td["content_type"] = t.content_type
        if t.bitrate:
            td["bitrate"] = t.bitrate
        if t.is_live:
            td["is_live"] = True
        tracks.append(td)

    return {
        "player_id": playlist.player_id,
        "version": _PERSISTENCE_VERSION,
        "current_index": playlist.current_index,
        "repeat_mode": playlist.repeat_mode.value,
        "shuffle_mode": playlist.shuffle_mode.value,
        "updated_at": playlist.updated_at,
        "tracks": tracks,
    }


def _deserialize_playlist(data: dict[str, Any]) -> Playlist:
    """Deserialize a dict (from JSON) back into a Playlist.

    Unknown / missing fields are handled gracefully so that files written
    by older or newer versions of Resonance don't crash the loader.
    """
    player_id: str = data.get("player_id", "")
    tracks: list[PlaylistTrack] = []
    for td in data.get("tracks", []):
        tracks.append(PlaylistTrack(
            track_id=TrackId(td["track_id"]) if td.get("track_id") is not None else None,
            path=td.get("path", ""),
            title=td.get("title", ""),
            artist=td.get("artist", ""),
            album=td.get("album", ""),
            album_id=AlbumId(td["album_id"]) if td.get("album_id") is not None else None,
            artist_id=ArtistId(td["artist_id"]) if td.get("artist_id") is not None else None,
            duration_ms=td.get("duration_ms", 0),
            # Remote-stream fields (backward-compat: default to local track)
            source=td.get("source", "local"),
            stream_url=td.get("stream_url"),
            external_id=td.get("external_id"),
            artwork_url=td.get("artwork_url"),
            is_remote=td.get("is_remote", False),
            content_type=td.get("content_type"),
            bitrate=td.get("bitrate", 0),
            is_live=td.get("is_live", False),
        ))

    playlist = Playlist(
        player_id=player_id,
        tracks=tracks,
        current_index=data.get("current_index", 0),
        repeat_mode=RepeatMode(data.get("repeat_mode", 0)),
        shuffle_mode=ShuffleMode(data.get("shuffle_mode", 0)),
        updated_at=data.get("updated_at", 0.0),
    )
    # Freshly loaded — not dirty.
    playlist._dirty = False
    return playlist


def _safe_filename(player_id: str) -> str:
    """Turn a player MAC/ID into a safe filename (replace colons)."""
    return player_id.replace(":", "-") + ".json"


def _player_id_from_filename(filename: str) -> str:
    """Reverse of ``_safe_filename``."""
    return filename.removesuffix(".json").replace("-", ":")


class PlaylistManager:
    """
    Manages playlists for all connected players.

    This is a central registry that creates and retrieves playlists
    by player ID (MAC address).

    Optionally persists playlists as JSON files so queues survive
    server restarts.
    """

    def __init__(self, persistence_dir: Path | None = None) -> None:
        """Initialize the playlist manager.

        Args:
            persistence_dir: Directory for JSON playlist files.
                ``None`` disables persistence (pure in-memory).
        """
        self._playlists: dict[str, Playlist] = {}
        self._persistence_dir: Path | None = persistence_dir
        self._autosave_task: asyncio.Task[None] | None = None
        self._autosave_interval: float = 30.0  # seconds

    def get(self, player_id: str) -> Playlist:
        """
        Get or create a playlist for a player.

        Args:
            player_id: Player's MAC address or unique ID.

        Returns:
            The player's playlist (created if it didn't exist).
        """
        if player_id not in self._playlists:
            self._playlists[player_id] = Playlist(player_id=player_id)
            logger.debug("Created new playlist for player %s", player_id)
        return self._playlists[player_id]

    def remove(self, player_id: str) -> Playlist | None:
        """
        Remove a player's playlist.

        Args:
            player_id: Player's MAC address.

        Returns:
            The removed playlist, or None if it didn't exist.
        """
        return self._playlists.pop(player_id, None)

    def clear_all(self) -> int:
        """
        Clear all playlists.

        Returns:
            Number of playlists cleared.
        """
        count = len(self._playlists)
        self._playlists.clear()
        return count

    def __len__(self) -> int:
        """Return number of playlists."""
        return len(self._playlists)

    def __contains__(self, player_id: str) -> bool:
        """Check if a playlist exists for a player."""
        return player_id in self._playlists

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_all(self) -> int:
        """Persist every dirty playlist to disk (synchronous).

        Returns:
            Number of playlists written.
        """
        if self._persistence_dir is None:
            return 0

        self._persistence_dir.mkdir(parents=True, exist_ok=True)
        written = 0
        for playlist in self._playlists.values():
            if not playlist._dirty:
                continue
            path = self._persistence_dir / _safe_filename(playlist.player_id)
            try:
                data = _serialize_playlist(playlist)
                tmp_path = path.with_suffix(".tmp")
                tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
                tmp_path.replace(path)
                playlist._dirty = False
                written += 1
            except Exception:
                logger.exception(
                    "Failed to save playlist for player %s", playlist.player_id
                )
        if written:
            logger.debug("Saved %d playlist(s) to %s", written, self._persistence_dir)
        return written

    def load_all(self) -> int:
        """Load all playlist JSON files from the persistence directory.

        Existing in-memory playlists are **not** overwritten — only players
        that don't already have a playlist get one loaded from disk.

        Returns:
            Number of playlists loaded.
        """
        if self._persistence_dir is None:
            return 0
        if not self._persistence_dir.is_dir():
            return 0

        loaded = 0
        for path in sorted(self._persistence_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                playlist = _deserialize_playlist(data)
                if not playlist.player_id:
                    logger.warning("Skipping playlist file without player_id: %s", path)
                    continue
                if playlist.player_id not in self._playlists:
                    self._playlists[playlist.player_id] = playlist
                    loaded += 1
            except Exception:
                logger.exception("Skipping corrupt playlist file: %s", path)

        if loaded:
            logger.info(
                "Loaded %d persisted playlist(s) from %s", loaded, self._persistence_dir
            )
        return loaded

    async def start_autosave(self) -> None:
        """Start the background auto-save task (call once after event loop is running)."""
        if self._persistence_dir is None:
            return
        if self._autosave_task is not None:
            return
        self._autosave_task = asyncio.create_task(self._autosave_loop())
        logger.debug("Playlist autosave started (interval=%.0fs)", self._autosave_interval)

    async def stop_autosave(self) -> None:
        """Stop the background auto-save task and flush dirty playlists."""
        if self._autosave_task is not None:
            self._autosave_task.cancel()
            try:
                await self._autosave_task
            except asyncio.CancelledError:
                pass
            self._autosave_task = None
        # Final flush
        self.save_all()

    async def _autosave_loop(self) -> None:
        """Periodically save dirty playlists."""
        try:
            while True:
                await asyncio.sleep(self._autosave_interval)
                self.save_all()
        except asyncio.CancelledError:
            pass
