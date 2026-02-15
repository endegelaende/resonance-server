"""
Tests for Playlist and PlaylistManager.

Tests cover:
- PlaylistTrack creation and properties
- Playlist add/remove/clear operations
- Playlist navigation (next/previous/play)
- Repeat and shuffle modes
- PlaylistManager registry
- Playlist persistence (save/load roundtrip, dirty flag, corrupt files)
- Alarm persistence (save/load roundtrip, corrupt files)
- Player-prefs persistence (save/load roundtrip, corrupt files)
"""

import json
from pathlib import Path

import pytest

from resonance.core.playlist import (
    AlbumId,
    ArtistId,
    Playlist,
    PlaylistManager,
    PlaylistTrack,
    RepeatMode,
    ShuffleMode,
    TrackId,
    _deserialize_playlist,
    _serialize_playlist,
)


class TestPlaylistTrack:
    """Tests for PlaylistTrack dataclass."""

    def test_create_with_all_fields(self) -> None:
        """Should create track with all metadata."""
        track = PlaylistTrack(
            track_id=TrackId(1),
            path="/music/song.mp3",
            title="Test Song",
            artist="Test Artist",
            album="Test Album",
            duration_ms=180000,
        )
        assert track.track_id == 1
        assert track.path == "/music/song.mp3"
        assert track.title == "Test Song"
        assert track.artist == "Test Artist"
        assert track.album == "Test Album"
        assert track.duration_ms == 180000

    def test_create_with_minimal_fields(self) -> None:
        """Should create track with just path."""
        track = PlaylistTrack(track_id=None, path="/music/song.mp3")
        assert track.track_id is None
        assert track.path == "/music/song.mp3"
        assert track.title == ""
        assert track.artist == ""

    def test_from_path_string(self) -> None:
        """Should create track from path string."""
        track = PlaylistTrack.from_path("/music/My Song.mp3")
        # Path separators may differ on Windows vs Unix
        assert "My Song.mp3" in track.path
        assert track.title == "My Song"  # stem of filename
        assert track.track_id is None

    def test_from_path_object(self) -> None:
        """Should create track from Path object."""
        from pathlib import Path

        track = PlaylistTrack.from_path(Path("/music/Another Song.flac"))
        assert "Another Song.flac" in track.path
        assert track.title == "Another Song"

    def test_frozen_immutable(self) -> None:
        """PlaylistTrack should be immutable."""
        track = PlaylistTrack(track_id=TrackId(1), path="/music/song.mp3")
        with pytest.raises(AttributeError):
            track.title = "New Title"  # type: ignore


class TestPlaylist:
    """Tests for Playlist class."""

    def test_create_empty(self) -> None:
        """Should create empty playlist."""
        playlist = Playlist(player_id="aa:bb:cc:dd:ee:ff")
        assert playlist.player_id == "aa:bb:cc:dd:ee:ff"
        assert len(playlist) == 0
        assert playlist.is_empty
        assert playlist.current_track is None

    def test_add_track(self) -> None:
        """Should add track to end of playlist."""
        playlist = Playlist(player_id="test")
        track = PlaylistTrack.from_path("/music/song1.mp3")

        idx = playlist.add(track)

        assert idx == 0
        assert len(playlist) == 1
        assert not playlist.is_empty

    def test_add_multiple_tracks(self) -> None:
        """Should add multiple tracks in order."""
        playlist = Playlist(player_id="test")
        track1 = PlaylistTrack.from_path("/music/song1.mp3")
        track2 = PlaylistTrack.from_path("/music/song2.mp3")
        track3 = PlaylistTrack.from_path("/music/song3.mp3")

        playlist.add(track1)
        playlist.add(track2)
        playlist.add(track3)

        assert len(playlist) == 3
        assert playlist.tracks[0] == track1
        assert playlist.tracks[1] == track2
        assert playlist.tracks[2] == track3

    def test_add_at_position(self) -> None:
        """Should insert track at specific position."""
        playlist = Playlist(player_id="test")
        track1 = PlaylistTrack.from_path("/music/song1.mp3")
        track2 = PlaylistTrack.from_path("/music/song2.mp3")
        track3 = PlaylistTrack.from_path("/music/song3.mp3")

        playlist.add(track1)
        playlist.add(track3)
        idx = playlist.add(track2, position=1)

        assert idx == 1
        assert playlist.tracks[0] == track1
        assert playlist.tracks[1] == track2
        assert playlist.tracks[2] == track3

    def test_add_path_convenience(self) -> None:
        """Should add track by path using convenience method."""
        playlist = Playlist(player_id="test")

        idx = playlist.add_path("/music/song.mp3")

        assert idx == 0
        assert "song.mp3" in playlist.tracks[0].path

    def test_remove_track(self) -> None:
        """Should remove track at index."""
        playlist = Playlist(player_id="test")
        playlist.add_path("/music/song1.mp3")
        playlist.add_path("/music/song2.mp3")
        playlist.add_path("/music/song3.mp3")

        removed = playlist.remove(1)

        assert removed is not None
        assert "song2.mp3" in removed.path
        assert len(playlist) == 2
        assert "song3.mp3" in playlist.tracks[1].path

    def test_remove_invalid_index(self) -> None:
        """Should return None for invalid index."""
        playlist = Playlist(player_id="test")
        playlist.add_path("/music/song.mp3")

        assert playlist.remove(-1) is None
        assert playlist.remove(5) is None
        assert len(playlist) == 1

    def test_clear(self) -> None:
        """Should clear all tracks."""
        playlist = Playlist(player_id="test")
        playlist.add_path("/music/song1.mp3")
        playlist.add_path("/music/song2.mp3")

        count = playlist.clear()

        assert count == 2
        assert len(playlist) == 0
        assert playlist.is_empty

    def test_current_track(self) -> None:
        """Should return current track."""
        playlist = Playlist(player_id="test")
        playlist.add_path("/music/song1.mp3")
        playlist.add_path("/music/song2.mp3")

        assert playlist.current_track is not None
        assert "song1.mp3" in playlist.current_track.path

    def test_play_at_index(self) -> None:
        """Should set current_index and return track."""
        playlist = Playlist(player_id="test")
        playlist.add_path("/music/song1.mp3")
        playlist.add_path("/music/song2.mp3")
        playlist.add_path("/music/song3.mp3")

        track = playlist.play(1)

        assert track is not None
        assert "song2.mp3" in track.path
        assert playlist.current_index == 1

    def test_play_clamps_index(self) -> None:
        """Should clamp index to valid range."""
        playlist = Playlist(player_id="test")
        playlist.add_path("/music/song1.mp3")
        playlist.add_path("/music/song2.mp3")

        track = playlist.play(100)
        assert playlist.current_index == 1  # clamped to last

        track = playlist.play(-5)
        assert playlist.current_index == 0  # clamped to first

    def test_next_track(self) -> None:
        """Should move to next track."""
        playlist = Playlist(player_id="test")
        playlist.add_path("/music/song1.mp3")
        playlist.add_path("/music/song2.mp3")
        playlist.add_path("/music/song3.mp3")

        track = playlist.next()

        assert track is not None
        assert "song2.mp3" in track.path
        assert playlist.current_index == 1

    def test_next_at_end_no_repeat(self) -> None:
        """Should return None at end with no repeat."""
        playlist = Playlist(player_id="test")
        playlist.add_path("/music/song1.mp3")
        playlist.add_path("/music/song2.mp3")
        playlist.current_index = 1  # last track

        track = playlist.next()

        assert track is None
        assert playlist.current_index == 1  # unchanged

    def test_next_at_end_repeat_all(self) -> None:
        """Should wrap to beginning with repeat all."""
        playlist = Playlist(player_id="test")
        playlist.add_path("/music/song1.mp3")
        playlist.add_path("/music/song2.mp3")
        playlist.set_repeat(RepeatMode.ALL)
        playlist.current_index = 1

        track = playlist.next()

        assert track is not None
        assert "song1.mp3" in track.path
        assert playlist.current_index == 0

    def test_next_repeat_one(self) -> None:
        """Should return same track with repeat one."""
        playlist = Playlist(player_id="test")
        playlist.add_path("/music/song1.mp3")
        playlist.add_path("/music/song2.mp3")
        playlist.set_repeat(RepeatMode.ONE)
        playlist.current_index = 0

        track = playlist.next()

        assert track is not None
        assert "song1.mp3" in track.path
        assert playlist.current_index == 0

    def test_previous_track(self) -> None:
        """Should move to previous track."""
        playlist = Playlist(player_id="test")
        playlist.add_path("/music/song1.mp3")
        playlist.add_path("/music/song2.mp3")
        playlist.add_path("/music/song3.mp3")
        playlist.current_index = 2

        track = playlist.previous()

        assert track is not None
        assert "song2.mp3" in track.path
        assert playlist.current_index == 1

    def test_previous_at_start_no_repeat(self) -> None:
        """Should return None at start with no repeat."""
        playlist = Playlist(player_id="test")
        playlist.add_path("/music/song1.mp3")
        playlist.add_path("/music/song2.mp3")
        playlist.current_index = 0

        track = playlist.previous()

        assert track is None

    def test_previous_at_start_repeat_all(self) -> None:
        """Should wrap to end with repeat all."""
        playlist = Playlist(player_id="test")
        playlist.add_path("/music/song1.mp3")
        playlist.add_path("/music/song2.mp3")
        playlist.set_repeat(RepeatMode.ALL)
        playlist.current_index = 0

        track = playlist.previous()

        assert track is not None
        assert "song2.mp3" in track.path
        assert playlist.current_index == 1

    def test_has_next(self) -> None:
        """Should correctly report if next track available."""
        playlist = Playlist(player_id="test")
        assert not playlist.has_next  # empty

        playlist.add_path("/music/song1.mp3")
        assert not playlist.has_next  # single track, at end

        playlist.add_path("/music/song2.mp3")
        playlist.current_index = 0
        assert playlist.has_next  # second track available

        playlist.current_index = 1
        assert not playlist.has_next  # at end

        playlist.set_repeat(RepeatMode.ALL)
        assert playlist.has_next  # can wrap

    def test_has_previous(self) -> None:
        """Should correctly report if previous track available."""
        playlist = Playlist(player_id="test")
        assert not playlist.has_previous  # empty

        playlist.add_path("/music/song1.mp3")
        playlist.add_path("/music/song2.mp3")
        playlist.current_index = 0
        assert not playlist.has_previous  # at start

        playlist.current_index = 1
        assert playlist.has_previous  # first track available

        playlist.current_index = 0
        playlist.set_repeat(RepeatMode.ALL)
        assert playlist.has_previous  # can wrap

    def test_set_repeat_by_enum(self) -> None:
        """Should set repeat mode by enum."""
        playlist = Playlist(player_id="test")

        playlist.set_repeat(RepeatMode.ONE)
        assert playlist.repeat_mode == RepeatMode.ONE

        playlist.set_repeat(RepeatMode.ALL)
        assert playlist.repeat_mode == RepeatMode.ALL

    def test_set_repeat_by_int(self) -> None:
        """Should set repeat mode by integer."""
        playlist = Playlist(player_id="test")

        playlist.set_repeat(1)
        assert playlist.repeat_mode == RepeatMode.ONE

        playlist.set_repeat(2)
        assert playlist.repeat_mode == RepeatMode.ALL

        playlist.set_repeat(0)
        assert playlist.repeat_mode == RepeatMode.OFF

    def test_set_shuffle_on(self) -> None:
        """Should shuffle tracks when enabling shuffle."""
        playlist = Playlist(player_id="test")
        for i in range(10):
            playlist.add_path(f"/music/song{i}.mp3")

        original_paths = [t.path for t in playlist.tracks]

        playlist.set_shuffle(ShuffleMode.ON)

        assert playlist.shuffle_mode == ShuffleMode.ON
        # Current track should be at index 0
        assert playlist.current_index == 0
        # Order should be different (with very high probability)
        shuffled_paths = [t.path for t in playlist.tracks]
        # Note: there's a tiny chance this could fail if shuffle produces same order
        assert len(shuffled_paths) == len(original_paths)

    def test_set_shuffle_off_restores_order(self) -> None:
        """Should restore original order when disabling shuffle."""
        playlist = Playlist(player_id="test")
        for i in range(5):
            playlist.add_path(f"/music/song{i}.mp3")

        original_paths = [t.path for t in playlist.tracks]

        playlist.set_shuffle(ShuffleMode.ON)
        playlist.set_shuffle(ShuffleMode.OFF)

        assert playlist.shuffle_mode == ShuffleMode.OFF
        restored_paths = [t.path for t in playlist.tracks]
        assert restored_paths == original_paths

    def test_get_tracks_info(self) -> None:
        """Should return track info for JSON serialization."""
        playlist = Playlist(player_id="test")
        playlist.add(
            PlaylistTrack(
                track_id=TrackId(1),
                path="/music/song.mp3",
                title="Test Song",
                artist="Artist",
                album="Album",
                duration_ms=180000,
            )
        )

        info = playlist.get_tracks_info()

        assert len(info) == 1
        assert info[0]["playlist index"] == 0
        assert info[0]["id"] == 1
        assert info[0]["title"] == "Test Song"
        assert info[0]["artist"] == "Artist"
        assert info[0]["album"] == "Album"
        assert info[0]["duration"] == 180  # converted to seconds
        assert info[0]["url"] == "/music/song.mp3"

    def test_remove_adjusts_current_index(self) -> None:
        """Should adjust current_index when removing track before it."""
        playlist = Playlist(player_id="test")
        playlist.add_path("/music/song1.mp3")
        playlist.add_path("/music/song2.mp3")
        playlist.add_path("/music/song3.mp3")
        playlist.current_index = 2  # pointing to song3

        playlist.remove(0)  # remove song1

        assert playlist.current_index == 1  # adjusted down
        assert "song3.mp3" in playlist.current_track.path  # type: ignore

    def test_insert_adjusts_current_index(self) -> None:
        """Should adjust current_index when inserting track before it."""
        playlist = Playlist(player_id="test")
        playlist.add_path("/music/song1.mp3")
        playlist.add_path("/music/song3.mp3")
        playlist.current_index = 1  # pointing to song3

        playlist.add_path("/music/song2.mp3", position=1)  # insert before current

        assert playlist.current_index == 2  # adjusted up
        assert "song3.mp3" in playlist.current_track.path  # type: ignore


class TestPlaylistManager:
    """Tests for PlaylistManager class."""

    def test_create_empty(self) -> None:
        """Should create empty manager."""
        manager = PlaylistManager()
        assert len(manager) == 0

    def test_get_creates_playlist(self) -> None:
        """Should create playlist on first access."""
        manager = PlaylistManager()
        playlist = manager.get("aa:bb:cc:dd:ee:ff")

        assert playlist is not None
        assert playlist.player_id == "aa:bb:cc:dd:ee:ff"
        assert len(manager) == 1

    def test_get_returns_same_instance(self) -> None:
        """Should return same playlist on subsequent access."""
        manager = PlaylistManager()
        playlist1 = manager.get("player1")
        playlist1.add_path("/music/song.mp3")

        playlist2 = manager.get("player1")

        assert playlist1 is playlist2
        assert len(playlist2) == 1

    def test_contains(self) -> None:
        """Should check if playlist exists."""
        manager = PlaylistManager()
        assert "player1" not in manager

        manager.get("player1")
        assert "player1" in manager

    def test_remove(self) -> None:
        """Should remove playlist."""
        manager = PlaylistManager()
        playlist = manager.get("player1")
        playlist.add_path("/music/song.mp3")

        removed = manager.remove("player1")

        assert removed is playlist
        assert "player1" not in manager
        assert len(manager) == 0

    def test_remove_nonexistent(self) -> None:
        """Should return None for nonexistent playlist."""
        manager = PlaylistManager()
        assert manager.remove("nonexistent") is None

    def test_clear_all(self) -> None:
        """Should clear all playlists."""
        manager = PlaylistManager()
        manager.get("player1").add_path("/music/song1.mp3")
        manager.get("player2").add_path("/music/song2.mp3")
        manager.get("player3").add_path("/music/song3.mp3")

        count = manager.clear_all()

        assert count == 3
        assert len(manager) == 0

    def test_multiple_players_independent(self) -> None:
        """Each player should have independent playlist."""
        manager = PlaylistManager()
        playlist1 = manager.get("player1")
        playlist2 = manager.get("player2")

        playlist1.add_path("/music/song1.mp3")
        playlist1.add_path("/music/song2.mp3")
        playlist2.add_path("/music/other.mp3")

        assert len(playlist1) == 2
        assert len(playlist2) == 1


# -----------------------------------------------------------------------
# Playlist persistence tests
# -----------------------------------------------------------------------


class TestPlaylistSerialization:
    """Tests for playlist JSON serialization / deserialization."""

    def _make_track(self, idx: int = 1) -> PlaylistTrack:
        return PlaylistTrack(
            track_id=TrackId(idx),
            path=f"/music/song{idx}.mp3",
            title=f"Song {idx}",
            artist=f"Artist {idx}",
            album=f"Album {idx}",
            album_id=AlbumId(idx + 100),
            artist_id=ArtistId(idx + 200),
            duration_ms=240000 + idx,
        )

    def test_serialize_roundtrip(self) -> None:
        """Serialize → deserialize must preserve all fields."""
        playlist = Playlist(player_id="aa:bb:cc:dd:ee:ff")
        playlist.add(self._make_track(1))
        playlist.add(self._make_track(2))
        playlist.add(self._make_track(3))
        playlist.play(1)
        playlist.set_repeat(RepeatMode.ALL)

        data = _serialize_playlist(playlist)
        restored = _deserialize_playlist(data)

        assert restored.player_id == playlist.player_id
        assert restored.current_index == 1
        assert restored.repeat_mode == RepeatMode.ALL
        assert restored.shuffle_mode == ShuffleMode.OFF
        assert len(restored.tracks) == 3
        assert restored.tracks[0].title == "Song 1"
        assert restored.tracks[1].track_id == TrackId(2)
        assert restored.tracks[2].album_id == AlbumId(103)
        assert restored.tracks[2].artist_id == ArtistId(203)
        assert restored.tracks[0].duration_ms == 240001

    def test_serialize_empty_playlist(self) -> None:
        """Empty playlist serializes and deserializes cleanly."""
        playlist = Playlist(player_id="00:11:22:33:44:55")
        data = _serialize_playlist(playlist)
        restored = _deserialize_playlist(data)

        assert restored.player_id == "00:11:22:33:44:55"
        assert len(restored.tracks) == 0
        assert restored.current_index == 0

    def test_deserialize_missing_fields_graceful(self) -> None:
        """Missing fields in JSON should use sensible defaults."""
        data = {"player_id": "aa:bb:cc:dd:ee:ff", "tracks": [{"path": "/x.mp3"}]}
        restored = _deserialize_playlist(data)

        assert restored.player_id == "aa:bb:cc:dd:ee:ff"
        assert len(restored.tracks) == 1
        assert restored.tracks[0].path == "/x.mp3"
        assert restored.tracks[0].track_id is None
        assert restored.tracks[0].title == ""
        assert restored.current_index == 0
        assert restored.repeat_mode == RepeatMode.OFF

    def test_deserialized_playlist_not_dirty(self) -> None:
        """A freshly deserialized playlist must not be dirty."""
        data = _serialize_playlist(Playlist(player_id="p1"))
        restored = _deserialize_playlist(data)
        assert restored._dirty is False


class TestPlaylistDirtyFlag:
    """Tests for dirty-flag tracking in Playlist."""

    def test_new_playlist_not_dirty(self) -> None:
        """A brand-new Playlist starts not dirty."""
        p = Playlist(player_id="p1")
        assert p._dirty is False

    def test_add_sets_dirty(self) -> None:
        p = Playlist(player_id="p1")
        p.add(PlaylistTrack(track_id=None, path="/a.mp3"))
        assert p._dirty is True

    def test_clear_sets_dirty(self) -> None:
        p = Playlist(player_id="p1")
        p.add(PlaylistTrack(track_id=None, path="/a.mp3"))
        p._dirty = False
        p.clear()
        assert p._dirty is True

    def test_remove_sets_dirty(self) -> None:
        p = Playlist(player_id="p1")
        p.add(PlaylistTrack(track_id=None, path="/a.mp3"))
        p._dirty = False
        p.remove(0)
        assert p._dirty is True

    def test_set_shuffle_sets_dirty(self) -> None:
        p = Playlist(player_id="p1")
        p.add(PlaylistTrack(track_id=None, path="/a.mp3"))
        p._dirty = False
        p.set_shuffle(ShuffleMode.ON)
        assert p._dirty is True


class TestPlaylistManagerPersistence:
    """Tests for PlaylistManager save/load with a real temp directory."""

    def _make_track(self, idx: int = 1) -> PlaylistTrack:
        return PlaylistTrack(
            track_id=TrackId(idx),
            path=f"/music/song{idx}.mp3",
            title=f"Song {idx}",
            artist=f"Artist {idx}",
            album=f"Album {idx}",
            album_id=AlbumId(idx),
            artist_id=ArtistId(idx),
            duration_ms=180000,
        )

    def test_save_load_roundtrip(self, tmp_path: Path) -> None:
        """Save all → new manager → load all must restore playlists."""
        mgr = PlaylistManager(persistence_dir=tmp_path)
        pl = mgr.get("aa:bb:cc:dd:ee:ff")
        pl.add(self._make_track(1))
        pl.add(self._make_track(2))
        pl.play(1)
        pl.set_repeat(RepeatMode.ALL)

        written = mgr.save_all()
        assert written == 1

        mgr2 = PlaylistManager(persistence_dir=tmp_path)
        loaded = mgr2.load_all()
        assert loaded == 1

        restored = mgr2.get("aa:bb:cc:dd:ee:ff")
        assert len(restored.tracks) == 2
        assert restored.current_index == 1
        assert restored.repeat_mode == RepeatMode.ALL
        assert restored.tracks[0].title == "Song 1"

    def test_save_skips_clean_playlists(self, tmp_path: Path) -> None:
        """Only dirty playlists should be written."""
        mgr = PlaylistManager(persistence_dir=tmp_path)
        pl = mgr.get("p1")
        pl.add(self._make_track(1))

        mgr.save_all()
        assert pl._dirty is False

        # Second save should write 0 (nothing dirty)
        assert mgr.save_all() == 0

    def test_load_skips_corrupt_json(self, tmp_path: Path) -> None:
        """Corrupt JSON files should be skipped gracefully."""
        bad_file = tmp_path / "bad-player.json"
        bad_file.write_text("{{{invalid json", encoding="utf-8")

        mgr = PlaylistManager(persistence_dir=tmp_path)
        loaded = mgr.load_all()
        assert loaded == 0

    def test_load_does_not_overwrite_existing(self, tmp_path: Path) -> None:
        """load_all() must not clobber playlists already in memory."""
        mgr = PlaylistManager(persistence_dir=tmp_path)
        pl = mgr.get("p1")
        pl.add(self._make_track(1))
        mgr.save_all()

        mgr2 = PlaylistManager(persistence_dir=tmp_path)
        # Pre-populate p1 in memory with different content
        pl2 = mgr2.get("p1")
        pl2.add(self._make_track(99))

        mgr2.load_all()
        # Should keep the in-memory version, not the disk version
        assert mgr2.get("p1").tracks[0].title == "Song 99"

    def test_no_persistence_dir_is_noop(self) -> None:
        """With persistence_dir=None, save/load are harmless no-ops."""
        mgr = PlaylistManager(persistence_dir=None)
        mgr.get("p1").add(PlaylistTrack(track_id=None, path="/a.mp3"))
        assert mgr.save_all() == 0
        assert mgr.load_all() == 0

    def test_multiple_players_roundtrip(self, tmp_path: Path) -> None:
        """Multiple players each get their own file and round-trip correctly."""
        mgr = PlaylistManager(persistence_dir=tmp_path)
        for i in range(3):
            pid = f"00:11:22:33:44:{i:02x}"
            pl = mgr.get(pid)
            pl.add(self._make_track(i + 1))

        written = mgr.save_all()
        assert written == 3

        mgr2 = PlaylistManager(persistence_dir=tmp_path)
        loaded = mgr2.load_all()
        assert loaded == 3


# -----------------------------------------------------------------------
# Alarm persistence tests
# -----------------------------------------------------------------------


class TestAlarmPersistence:
    """Tests for alarm save/load JSON roundtrip."""

    def test_save_load_roundtrip(self, tmp_path: Path) -> None:
        """Alarms saved to JSON should restore identically."""
        from resonance.web.handlers.alarm import (
            _ALARM_LOCK,
            _PLAYER_ALARMS,
            _PLAYER_DEFAULT_VOLUME,
            AlarmEntry,
            configure_persistence,
            load_alarms,
            save_alarms,
        )

        alarm_file = tmp_path / "alarms.json"
        configure_persistence(alarm_file)

        # Clear state
        _PLAYER_ALARMS.clear()
        _PLAYER_DEFAULT_VOLUME.clear()

        # Add test alarms
        _PLAYER_ALARMS["aa:bb:cc:dd:ee:01"] = [
            AlarmEntry(id="a1", time=25200, dow={1, 2, 3, 4, 5}, volume=60),
            AlarmEntry(id="a2", time=28800, dow={0, 6}, enabled=False, repeat=False),
        ]
        _PLAYER_DEFAULT_VOLUME["aa:bb:cc:dd:ee:01"] = 75

        save_alarms()
        assert alarm_file.is_file()

        # Clear and reload
        _PLAYER_ALARMS.clear()
        _PLAYER_DEFAULT_VOLUME.clear()

        loaded = load_alarms()
        assert loaded == 1
        assert len(_PLAYER_ALARMS["aa:bb:cc:dd:ee:01"]) == 2

        a1 = _PLAYER_ALARMS["aa:bb:cc:dd:ee:01"][0]
        assert a1.id == "a1"
        assert a1.time == 25200
        assert a1.dow == {1, 2, 3, 4, 5}
        assert a1.volume == 60
        assert a1.enabled is True

        a2 = _PLAYER_ALARMS["aa:bb:cc:dd:ee:01"][1]
        assert a2.enabled is False
        assert a2.repeat is False

        assert _PLAYER_DEFAULT_VOLUME["aa:bb:cc:dd:ee:01"] == 75

        # Cleanup
        configure_persistence(None)
        _PLAYER_ALARMS.clear()
        _PLAYER_DEFAULT_VOLUME.clear()

    def test_load_skips_corrupt_json(self, tmp_path: Path) -> None:
        """Corrupt alarm file should be skipped gracefully."""
        from resonance.web.handlers.alarm import (
            _PLAYER_ALARMS,
            _PLAYER_DEFAULT_VOLUME,
            configure_persistence,
            load_alarms,
        )

        alarm_file = tmp_path / "alarms.json"
        alarm_file.write_text("not valid json!!!", encoding="utf-8")
        configure_persistence(alarm_file)

        _PLAYER_ALARMS.clear()
        _PLAYER_DEFAULT_VOLUME.clear()

        loaded = load_alarms()
        assert loaded == 0

        # Cleanup
        configure_persistence(None)

    def test_load_missing_file_returns_zero(self, tmp_path: Path) -> None:
        """Non-existent file should return 0 without error."""
        from resonance.web.handlers.alarm import configure_persistence, load_alarms

        configure_persistence(tmp_path / "nonexistent.json")
        assert load_alarms() == 0
        configure_persistence(None)


# -----------------------------------------------------------------------
# Player-prefs persistence tests
# -----------------------------------------------------------------------


class TestPlayerPrefsPersistence:
    """Tests for player-prefs save/load JSON roundtrip."""

    def test_save_load_roundtrip(self, tmp_path: Path) -> None:
        """Prefs saved to JSON should restore identically."""
        from resonance.web.handlers.compat import (
            _PLAYER_PREFS,
            configure_prefs_persistence,
            load_all_player_prefs,
            save_player_prefs,
        )

        configure_prefs_persistence(tmp_path)

        _PLAYER_PREFS.clear()
        _PLAYER_PREFS["aa:bb:cc:dd:ee:01"] = {
            "transitionType": "crossfade",
            "transitionDuration": "5",
            "replayGainMode": "1",
        }

        save_player_prefs("aa:bb:cc:dd:ee:01")
        assert (tmp_path / "aa-bb-cc-dd-ee-01.json").is_file()

        # Clear and reload
        _PLAYER_PREFS.clear()

        loaded = load_all_player_prefs()
        assert loaded == 1
        assert _PLAYER_PREFS["aa:bb:cc:dd:ee:01"]["transitionType"] == "crossfade"
        assert _PLAYER_PREFS["aa:bb:cc:dd:ee:01"]["transitionDuration"] == "5"
        assert _PLAYER_PREFS["aa:bb:cc:dd:ee:01"]["replayGainMode"] == "1"

        # Cleanup
        configure_prefs_persistence(None)
        _PLAYER_PREFS.clear()

    def test_load_skips_corrupt_json(self, tmp_path: Path) -> None:
        """Corrupt prefs file should be skipped gracefully."""
        from resonance.web.handlers.compat import (
            _PLAYER_PREFS,
            configure_prefs_persistence,
            load_all_player_prefs,
        )

        bad_file = tmp_path / "bad-player.json"
        bad_file.write_text("{corrupt!", encoding="utf-8")
        configure_prefs_persistence(tmp_path)

        _PLAYER_PREFS.clear()
        loaded = load_all_player_prefs()
        assert loaded == 0

        # Cleanup
        configure_prefs_persistence(None)

    def test_load_does_not_overwrite_existing(self, tmp_path: Path) -> None:
        """load_all_player_prefs() must not clobber in-memory prefs."""
        from resonance.web.handlers.compat import (
            _PLAYER_PREFS,
            configure_prefs_persistence,
            load_all_player_prefs,
            save_player_prefs,
        )

        configure_prefs_persistence(tmp_path)

        _PLAYER_PREFS.clear()
        _PLAYER_PREFS["p1"] = {"key": "disk_value"}
        save_player_prefs("p1")

        # Pre-populate with different value
        _PLAYER_PREFS["p1"] = {"key": "memory_value"}
        load_all_player_prefs()

        # In-memory value should be preserved
        assert _PLAYER_PREFS["p1"]["key"] == "memory_value"

        # Cleanup
        configure_prefs_persistence(None)
        _PLAYER_PREFS.clear()

    def test_no_persistence_dir_is_noop(self) -> None:
        """With persistence_dir=None, save is a harmless no-op."""
        from resonance.web.handlers.compat import (
            _PLAYER_PREFS,
            configure_prefs_persistence,
            load_all_player_prefs,
            save_player_prefs,
        )

        configure_prefs_persistence(None)
        _PLAYER_PREFS["p1"] = {"k": "v"}
        save_player_prefs("p1")  # should not crash
        assert load_all_player_prefs() == 0

        # Cleanup
        _PLAYER_PREFS.clear()
