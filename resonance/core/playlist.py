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
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, NewType

if TYPE_CHECKING:
    from pathlib import Path

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
    """

    track_id: TrackId | None
    path: str
    album_id: AlbumId | None = None
    artist_id: ArtistId | None = None
    title: str = ""
    artist: str = ""
    album: str = ""
    duration_ms: int = 0

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

    def __len__(self) -> int:
        """Return number of tracks in playlist."""
        return len(self.tracks)

    def _touch(self) -> None:
        """Update the mutation timestamp."""
        self.updated_at = time.time()

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


class PlaylistManager:
    """
    Manages playlists for all connected players.

    This is a central registry that creates and retrieves playlists
    by player ID (MAC address).
    """

    def __init__(self) -> None:
        """Initialize the playlist manager."""
        self._playlists: dict[str, Playlist] = {}

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
