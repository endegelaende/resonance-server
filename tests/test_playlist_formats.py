"""
Tests for playlist file format parsers and writers.

Covers:
- M3U parsing: standard, extended, relative/absolute paths, encoding, BOM, #EXTURL
- M3U writing: extended format, CURTRACK, roundtrip
- PLS parsing: standard, titles, lengths
- PLS writing: roundtrip
- Format detection and auto-parse
- Edge cases: empty files, malformed data, missing files, UTF-8 with umlauts
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest


def _p(unix_path: str) -> str:
    """Return the path as-is — the parser preserves raw absolute paths."""
    return unix_path

from resonance.core.playlist_formats import (
    PLAYLIST_EXTENSIONS,
    PlaylistFileEntry,
    is_playlist_file,
    parse_m3u,
    parse_playlist_file,
    parse_pls,
    read_m3u_curtrack,
    write_m3u,
    write_pls,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FakeTrack:
    """Minimal track object compatible with write_m3u / write_pls."""

    path: str = "/music/test.mp3"
    title: str = "Test Track"
    artist: str = "Test Artist"
    album: str = "Test Album"
    duration_ms: int = 240_000


def _write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding=encoding)


def _write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


# ---------------------------------------------------------------------------
# M3U parsing — basic
# ---------------------------------------------------------------------------


class TestParseM3uBasic:
    def test_simple_m3u(self, tmp_path: Path) -> None:
        """Parse a simple M3U with absolute paths and no metadata."""
        m3u = tmp_path / "test.m3u"
        _write_text(
            m3u,
            "#EXTM3U\n"
            "/music/song1.mp3\n"
            "/music/song2.flac\n",
        )

        entries = parse_m3u(m3u)

        assert len(entries) == 2
        assert entries[0].path == _p("/music/song1.mp3")
        assert entries[1].path == _p("/music/song2.flac")
        assert entries[0].title == ""
        assert entries[0].duration_seconds == -1

    def test_empty_file(self, tmp_path: Path) -> None:
        """Empty M3U file produces zero entries."""
        m3u = tmp_path / "empty.m3u"
        _write_text(m3u, "")

        entries = parse_m3u(m3u)
        assert entries == []

    def test_only_header(self, tmp_path: Path) -> None:
        """M3U with only #EXTM3U header produces zero entries."""
        m3u = tmp_path / "header.m3u"
        _write_text(m3u, "#EXTM3U\n")

        entries = parse_m3u(m3u)
        assert entries == []

    def test_blank_lines_and_comments_skipped(self, tmp_path: Path) -> None:
        """Blank lines and arbitrary comments are ignored."""
        m3u = tmp_path / "comments.m3u"
        _write_text(
            m3u,
            "#EXTM3U\n"
            "\n"
            "# This is a comment\n"
            "#SOMECUSTOMTAG:value\n"
            "\n"
            "/music/song.mp3\n"
            "\n",
        )

        entries = parse_m3u(m3u)
        assert len(entries) == 1
        assert entries[0].path == _p("/music/song.mp3")

    def test_missing_file(self, tmp_path: Path) -> None:
        """Parsing a non-existent file returns empty list."""
        entries = parse_m3u(tmp_path / "does_not_exist.m3u")
        assert entries == []


# ---------------------------------------------------------------------------
# M3U parsing — Extended EXTINF
# ---------------------------------------------------------------------------


class TestParseM3uExtended:
    def test_standard_extinf(self, tmp_path: Path) -> None:
        """Parse standard #EXTINF with duration and title."""
        m3u = tmp_path / "ext.m3u"
        _write_text(
            m3u,
            "#EXTM3U\n"
            "#EXTINF:240,My Great Song\n"
            "/music/song.mp3\n",
        )

        entries = parse_m3u(m3u)
        assert len(entries) == 1
        assert entries[0].path == _p("/music/song.mp3")
        assert entries[0].title == "My Great Song"
        assert entries[0].duration_seconds == 240
        assert entries[0].artist == ""

    def test_extinf_with_artist_and_title(self, tmp_path: Path) -> None:
        """Parse #EXTINF with 'Artist - Title' display format."""
        m3u = tmp_path / "artist.m3u"
        _write_text(
            m3u,
            "#EXTM3U\n"
            "#EXTINF:300,Pink Floyd - Comfortably Numb\n"
            "/music/numb.mp3\n",
        )

        entries = parse_m3u(m3u)
        assert len(entries) == 1
        assert entries[0].path == _p("/music/numb.mp3")
        assert entries[0].title == "Pink Floyd - Comfortably Numb"
        assert entries[0].duration_seconds == 300

    def test_lms_extended_extinf(self, tmp_path: Path) -> None:
        """Parse LMS-style #EXTINF: secs,<artist> - <album> - <title>."""
        m3u = tmp_path / "lms.m3u"
        _write_text(
            m3u,
            "#EXTM3U\n"
            "#EXTINF:432,<Pink Floyd> - <The Wall> - <Comfortably Numb>\n"
            "/music/numb.flac\n",
        )

        entries = parse_m3u(m3u)
        assert len(entries) == 1
        assert entries[0].path == _p("/music/numb.flac")
        assert entries[0].artist == "Pink Floyd"
        assert entries[0].album == "The Wall"
        assert entries[0].title == "Comfortably Numb"
        assert entries[0].duration_seconds == 432

    def test_extinf_negative_duration(self, tmp_path: Path) -> None:
        """Duration of -1 means unknown."""
        m3u = tmp_path / "neg.m3u"
        _write_text(
            m3u,
            "#EXTM3U\n"
            "#EXTINF:-1,Unknown Length\n"
            "/music/stream.mp3\n",
        )

        entries = parse_m3u(m3u)
        assert entries[0].path == _p("/music/stream.mp3")
        assert entries[0].duration_seconds == -1
        assert entries[0].title == "Unknown Length"

    def test_multiple_tracks_with_extinf(self, tmp_path: Path) -> None:
        """Multiple tracks each with their own EXTINF."""
        m3u = tmp_path / "multi.m3u"
        _write_text(
            m3u,
            "#EXTM3U\n"
            "#EXTINF:180,Track One\n"
            "/music/one.mp3\n"
            "#EXTINF:200,Track Two\n"
            "/music/two.mp3\n"
            "#EXTINF:220,Track Three\n"
            "/music/three.mp3\n",
        )

        entries = parse_m3u(m3u)
        assert len(entries) == 3
        assert entries[0].title == "Track One"
        assert entries[0].duration_seconds == 180
        assert entries[1].title == "Track Two"
        assert entries[1].duration_seconds == 200
        assert entries[2].title == "Track Three"
        assert entries[2].duration_seconds == 220

    def test_extinf_without_matching_track_resets(self, tmp_path: Path) -> None:
        """EXTINF metadata only applies to the immediately following track line."""
        m3u = tmp_path / "reset.m3u"
        _write_text(
            m3u,
            "#EXTM3U\n"
            "#EXTINF:180,First Song\n"
            "/music/first.mp3\n"
            "/music/second.mp3\n",
        )

        entries = parse_m3u(m3u)
        assert len(entries) == 2
        assert entries[0].title == "First Song"
        assert entries[0].duration_seconds == 180
        # Second track has no EXTINF
        assert entries[1].title == ""
        assert entries[1].duration_seconds == -1


# ---------------------------------------------------------------------------
# M3U parsing — #EXTURL (LMS extension)
# ---------------------------------------------------------------------------


class TestParseM3uExturl:
    def test_exturl_preferred_over_path_line(self, tmp_path: Path) -> None:
        """When #EXTURL is present, its value is used as the track path."""
        m3u = tmp_path / "exturl.m3u"
        _write_text(
            m3u,
            "#EXTM3U\n"
            "#EXTINF:240,My Song\n"
            "#EXTURL:file:///music/actual.flac\n"
            "relative/display/path.mp3\n",
        )

        entries = parse_m3u(m3u)
        assert len(entries) == 1
        # On Windows we'd get a resolved windows path, on Unix /music/actual.flac
        # The key point is it didn't use the "relative/display/path.mp3" line
        assert "actual.flac" in entries[0].path or "actual" in entries[0].path


# ---------------------------------------------------------------------------
# M3U parsing — path resolution
# ---------------------------------------------------------------------------


class TestParseM3uPathResolution:
    def test_relative_paths_resolved_against_base_dir(self, tmp_path: Path) -> None:
        """Relative paths in M3U resolve against the M3U file's directory."""
        music_dir = tmp_path / "music"
        music_dir.mkdir()
        (music_dir / "song.mp3").touch()

        m3u = music_dir / "playlist.m3u"
        _write_text(m3u, "song.mp3\n")

        entries = parse_m3u(m3u)
        assert len(entries) == 1
        assert entries[0].path == str((music_dir / "song.mp3").resolve())

    def test_relative_paths_with_explicit_base_dir(self, tmp_path: Path) -> None:
        """Explicit base_dir overrides M3U file location."""
        music_dir = tmp_path / "music"
        music_dir.mkdir()
        (music_dir / "song.mp3").touch()

        m3u = tmp_path / "playlists" / "test.m3u"
        _write_text(m3u, "song.mp3\n")

        entries = parse_m3u(m3u, base_dir=music_dir)
        assert len(entries) == 1
        assert entries[0].path == str((music_dir / "song.mp3").resolve())

    def test_relative_path_fallback_to_music_dirs(self, tmp_path: Path) -> None:
        """Paths not found in base_dir are tried against music_dirs."""
        alt_dir = tmp_path / "alt_music"
        alt_dir.mkdir()
        (alt_dir / "rare.flac").touch()

        m3u = tmp_path / "test.m3u"
        _write_text(m3u, "rare.flac\n")

        entries = parse_m3u(m3u, music_dirs=[alt_dir])
        assert len(entries) == 1
        assert entries[0].path == str((alt_dir / "rare.flac").resolve())

    def test_unresolvable_relative_path_kept_as_is(self, tmp_path: Path) -> None:
        """If a relative path can't be resolved, keep it relative to base_dir."""
        m3u = tmp_path / "test.m3u"
        _write_text(m3u, "nonexistent/song.mp3\n")

        entries = parse_m3u(m3u)
        assert len(entries) == 1
        assert entries[0].path == str(tmp_path / "nonexistent" / "song.mp3")

    def test_absolute_paths_kept(self, tmp_path: Path) -> None:
        """Absolute paths are returned in platform-native form."""
        m3u = tmp_path / "test.m3u"
        _write_text(m3u, "/absolute/path/song.mp3\n")

        entries = parse_m3u(m3u)
        assert len(entries) == 1
        assert entries[0].path == _p("/absolute/path/song.mp3")

    def test_url_entries_pass_through(self, tmp_path: Path) -> None:
        """HTTP/HTTPS URLs are returned as-is."""
        m3u = tmp_path / "test.m3u"
        _write_text(
            m3u,
            "http://stream.example.com/live.mp3\n"
            "https://cdn.example.com/track.flac\n",
        )

        entries = parse_m3u(m3u)
        assert len(entries) == 2
        assert entries[0].path == "http://stream.example.com/live.mp3"
        assert entries[1].path == "https://cdn.example.com/track.flac"


# ---------------------------------------------------------------------------
# M3U parsing — encoding
# ---------------------------------------------------------------------------


class TestParseM3uEncoding:
    def test_utf8_with_umlauts(self, tmp_path: Path) -> None:
        """UTF-8 file with German umlauts and special characters."""
        m3u = tmp_path / "umlauts.m3u8"
        _write_text(
            m3u,
            "#EXTM3U\n"
            "#EXTINF:180,Ärzte - Schrei nach Liebe\n"
            "/musik/Ärzte/Schrei nach Liebe.mp3\n"
            "#EXTINF:240,Motörhead - Overkill\n"
            "/musik/Motörhead/Overkill.mp3\n",
        )

        entries = parse_m3u(m3u)
        assert len(entries) == 2
        assert entries[0].title == "Ärzte - Schrei nach Liebe"
        assert "Ärzte" in entries[0].path
        assert entries[1].title == "Motörhead - Overkill"

    def test_utf8_bom(self, tmp_path: Path) -> None:
        """UTF-8 file with BOM is handled correctly."""
        m3u = tmp_path / "bom.m3u"
        # utf-8-sig encoding adds the BOM automatically; do NOT put \ufeff in the string
        content = "#EXTM3U\n#EXTINF:120,BOM Test\n/music/bom.mp3\n"
        _write_text(m3u, content, encoding="utf-8-sig")

        entries = parse_m3u(m3u)
        assert len(entries) == 1
        assert entries[0].title == "BOM Test"

    def test_latin1_fallback(self, tmp_path: Path) -> None:
        """Latin-1 encoded files fall back gracefully."""
        m3u = tmp_path / "latin.m3u"
        content = "#EXTM3U\n#EXTINF:180,Ärzte\n/music/song.mp3\n"
        _write_bytes(m3u, content.encode("latin-1"))

        entries = parse_m3u(m3u)
        assert len(entries) == 1
        # The title should still be readable
        assert "rzte" in entries[0].title

    def test_crlf_line_endings(self, tmp_path: Path) -> None:
        """DOS-style CRLF line endings are handled."""
        m3u = tmp_path / "dos.m3u"
        content = "#EXTM3U\r\n#EXTINF:180,DOS Song\r\n/music/dos.mp3\r\n"
        _write_bytes(m3u, content.encode("utf-8"))

        entries = parse_m3u(m3u)
        assert len(entries) == 1
        assert entries[0].title == "DOS Song"


# ---------------------------------------------------------------------------
# M3U parsing — CURTRACK
# ---------------------------------------------------------------------------


class TestReadM3uCurtrack:
    def test_curtrack_present(self, tmp_path: Path) -> None:
        """Read CURTRACK marker from first line."""
        m3u = tmp_path / "resume.m3u"
        _write_text(
            m3u,
            "#CURTRACK 5\n"
            "#EXTM3U\n"
            "/music/song.mp3\n",
        )

        assert read_m3u_curtrack(m3u) == 5

    def test_curtrack_missing(self, tmp_path: Path) -> None:
        """Returns 0 when no CURTRACK marker."""
        m3u = tmp_path / "no_cur.m3u"
        _write_text(m3u, "#EXTM3U\n/music/song.mp3\n")

        assert read_m3u_curtrack(m3u) == 0

    def test_curtrack_nonexistent_file(self, tmp_path: Path) -> None:
        """Returns 0 for non-existent file."""
        assert read_m3u_curtrack(tmp_path / "missing.m3u") == 0

    def test_curtrack_zero(self, tmp_path: Path) -> None:
        """CURTRACK 0 is valid."""
        m3u = tmp_path / "zero.m3u"
        _write_text(m3u, "#CURTRACK 0\n#EXTM3U\n/music/song.mp3\n")

        assert read_m3u_curtrack(m3u) == 0


# ---------------------------------------------------------------------------
# M3U writing
# ---------------------------------------------------------------------------


class TestWriteM3u:
    def test_write_basic(self, tmp_path: Path) -> None:
        """Write M3U with track objects."""
        m3u = tmp_path / "out.m3u"
        tracks = [
            FakeTrack(path="/music/a.mp3", title="Song A", artist="Art A", album="Alb A", duration_ms=180_000),
            FakeTrack(path="/music/b.flac", title="Song B", artist="Art B", album="Alb B", duration_ms=240_000),
        ]

        write_m3u(m3u, tracks)

        content = m3u.read_text(encoding="utf-8")
        assert "#EXTM3U" in content
        assert "#EXTINF:180,<Art A> - <Alb A> - <Song A>" in content
        assert "/music/a.mp3" in content
        assert "#EXTINF:240,<Art B> - <Alb B> - <Song B>" in content
        assert "/music/b.flac" in content

    def test_write_with_curtrack(self, tmp_path: Path) -> None:
        """Write M3U with CURTRACK marker."""
        m3u = tmp_path / "resume.m3u"
        tracks = [FakeTrack(path="/music/a.mp3")]

        write_m3u(m3u, tracks, current_index=3)

        content = m3u.read_text(encoding="utf-8")
        assert content.startswith("#CURTRACK 3\n")

    def test_write_without_curtrack(self, tmp_path: Path) -> None:
        """No CURTRACK marker when current_index is None."""
        m3u = tmp_path / "no_cur.m3u"
        tracks = [FakeTrack(path="/music/a.mp3")]

        write_m3u(m3u, tracks)

        content = m3u.read_text(encoding="utf-8")
        assert "#CURTRACK" not in content

    def test_write_string_paths(self, tmp_path: Path) -> None:
        """Plain string paths are written as-is."""
        m3u = tmp_path / "strings.m3u"

        write_m3u(m3u, ["/music/a.mp3", "/music/b.flac"])

        content = m3u.read_text(encoding="utf-8")
        assert "#EXTM3U" in content
        assert "/music/a.mp3" in content
        assert "/music/b.flac" in content
        # No EXTINF for plain strings
        assert "#EXTINF" not in content

    def test_write_title_only_no_album(self, tmp_path: Path) -> None:
        """Track with title but no album uses simple EXTINF format."""
        m3u = tmp_path / "simple.m3u"
        track = FakeTrack(path="/music/a.mp3", title="Solo", artist="Art", album="", duration_ms=120_000)

        write_m3u(m3u, [track])

        content = m3u.read_text(encoding="utf-8")
        assert "#EXTINF:120,Art - Solo" in content

    def test_write_title_only_no_artist(self, tmp_path: Path) -> None:
        """Track with title but no artist uses title-only EXTINF."""
        m3u = tmp_path / "title_only.m3u"
        track = FakeTrack(path="/music/a.mp3", title="Just Title", artist="", album="", duration_ms=60_000)

        write_m3u(m3u, [track])

        content = m3u.read_text(encoding="utf-8")
        assert "#EXTINF:60,Just Title" in content

    def test_write_creates_parent_dirs(self, tmp_path: Path) -> None:
        """Parent directories are created if missing."""
        m3u = tmp_path / "sub" / "dir" / "playlist.m3u"

        write_m3u(m3u, [FakeTrack()])

        assert m3u.exists()

    def test_write_empty_playlist(self, tmp_path: Path) -> None:
        """Writing empty playlist produces valid M3U with just header."""
        m3u = tmp_path / "empty.m3u"
        write_m3u(m3u, [])

        content = m3u.read_text(encoding="utf-8")
        assert "#EXTM3U" in content
        lines = [l for l in content.strip().splitlines() if l and not l.startswith("#")]
        assert lines == []

    def test_write_utf8_umlauts(self, tmp_path: Path) -> None:
        """Umlauts and special characters are preserved in UTF-8."""
        m3u = tmp_path / "umlauts.m3u8"
        track = FakeTrack(
            path="/musik/Ärzte/Schrei.mp3",
            title="Schrei nach Liebe",
            artist="Die Ärzte",
            album="Die Bestie in Menschengestalt",
            duration_ms=210_000,
        )

        write_m3u(m3u, [track])

        content = m3u.read_text(encoding="utf-8")
        assert "Die Ärzte" in content
        assert "Ärzte" in content


# ---------------------------------------------------------------------------
# M3U roundtrip
# ---------------------------------------------------------------------------


class TestM3uRoundtrip:
    def test_write_then_parse(self, tmp_path: Path) -> None:
        """Write tracks and parse them back — metadata should roundtrip."""
        m3u = tmp_path / "roundtrip.m3u"
        original_tracks = [
            FakeTrack(path="/music/a.mp3", title="Alpha", artist="Art A", album="Alb A", duration_ms=180_000),
            FakeTrack(path="/music/b.flac", title="Beta", artist="Art B", album="Alb B", duration_ms=240_000),
            FakeTrack(path="/music/c.ogg", title="Gamma", artist="Art C", album="Alb C", duration_ms=300_000),
        ]

        write_m3u(m3u, original_tracks, current_index=1)

        entries = parse_m3u(m3u)
        assert len(entries) == 3

        assert entries[0].path == _p("/music/a.mp3")
        assert entries[0].title == "Alpha"
        assert entries[0].artist == "Art A"
        assert entries[0].album == "Alb A"
        assert entries[0].duration_seconds == 180

        assert entries[1].path == _p("/music/b.flac")
        assert entries[1].title == "Beta"
        assert entries[1].duration_seconds == 240

        assert entries[2].path == _p("/music/c.ogg")
        assert entries[2].title == "Gamma"
        assert entries[2].duration_seconds == 300

        # CURTRACK preserved
        assert read_m3u_curtrack(m3u) == 1

    def test_roundtrip_with_umlauts(self, tmp_path: Path) -> None:
        """Roundtrip preserves UTF-8 characters."""
        m3u = tmp_path / "umlaut_rt.m3u8"
        tracks = [
            FakeTrack(
                path="/musik/Motörhead/Overkill.flac",
                title="Overkill",
                artist="Motörhead",
                album="Overkill",
                duration_ms=322_000,
            ),
        ]

        write_m3u(m3u, tracks)
        entries = parse_m3u(m3u)

        assert len(entries) == 1
        assert entries[0].artist == "Motörhead"
        assert entries[0].title == "Overkill"
        assert "Motörhead" in entries[0].path


# ---------------------------------------------------------------------------
# PLS parsing
# ---------------------------------------------------------------------------


class TestParsePls:
    def test_basic_pls(self, tmp_path: Path) -> None:
        """Parse a standard PLS file."""
        pls = tmp_path / "test.pls"
        _write_text(
            pls,
            "[playlist]\n"
            "PlaylistName=Test\n"
            "File1=/music/song1.mp3\n"
            "Title1=Song One\n"
            "Length1=180\n"
            "File2=/music/song2.flac\n"
            "Title2=Song Two\n"
            "Length2=240\n"
            "NumberOfEntries=2\n"
            "Version=2\n",
        )

        entries = parse_pls(pls)
        assert len(entries) == 2

        assert entries[0].path == _p("/music/song1.mp3")
        assert entries[0].title == "Song One"
        assert entries[0].duration_seconds == 180

        assert entries[1].path == _p("/music/song2.flac")
        assert entries[1].title == "Song Two"
        assert entries[1].duration_seconds == 240

    def test_pls_without_titles(self, tmp_path: Path) -> None:
        """PLS file without titles still parses paths."""
        pls = tmp_path / "notitle.pls"
        _write_text(
            pls,
            "[playlist]\n"
            "File1=/music/a.mp3\n"
            "File2=/music/b.mp3\n"
            "NumberOfEntries=2\n"
            "Version=2\n",
        )

        entries = parse_pls(pls)
        assert len(entries) == 2
        assert entries[0].title == ""
        assert entries[1].title == ""

    def test_pls_without_lengths(self, tmp_path: Path) -> None:
        """PLS file without length fields defaults to -1."""
        pls = tmp_path / "nolen.pls"
        _write_text(
            pls,
            "[playlist]\n"
            "File1=/music/a.mp3\n"
            "Title1=Song A\n"
            "NumberOfEntries=1\n"
            "Version=2\n",
        )

        entries = parse_pls(pls)
        assert len(entries) == 1
        assert entries[0].duration_seconds == -1

    def test_pls_urls(self, tmp_path: Path) -> None:
        """PLS with URL entries."""
        pls = tmp_path / "stream.pls"
        _write_text(
            pls,
            "[playlist]\n"
            "File1=http://stream.example.com/live\n"
            "Title1=Live Stream\n"
            "Length1=-1\n"
            "NumberOfEntries=1\n"
            "Version=2\n",
        )

        entries = parse_pls(pls)
        assert len(entries) == 1
        assert entries[0].path == "http://stream.example.com/live"
        assert entries[0].title == "Live Stream"
        assert entries[0].duration_seconds == -1

    def test_pls_case_insensitive_keys(self, tmp_path: Path) -> None:
        """PLS keys are case-insensitive (File vs file vs FILE)."""
        pls = tmp_path / "case.pls"
        _write_text(
            pls,
            "[playlist]\n"
            "file1=/music/a.mp3\n"
            "TITLE1=Song A\n"
            "length1=120\n"
            "NumberOfEntries=1\n"
            "Version=2\n",
        )

        entries = parse_pls(pls)
        assert len(entries) == 1
        assert entries[0].path == _p("/music/a.mp3")
        assert entries[0].title == "Song A"
        assert entries[0].duration_seconds == 120

    def test_pls_sorted_by_index(self, tmp_path: Path) -> None:
        """PLS entries are returned sorted by their numeric index."""
        pls = tmp_path / "unordered.pls"
        _write_text(
            pls,
            "[playlist]\n"
            "File3=/music/c.mp3\n"
            "File1=/music/a.mp3\n"
            "File2=/music/b.mp3\n"
            "NumberOfEntries=3\n"
            "Version=2\n",
        )

        entries = parse_pls(pls)
        assert len(entries) == 3
        assert entries[0].path == _p("/music/a.mp3")
        assert entries[1].path == _p("/music/b.mp3")
        assert entries[2].path == _p("/music/c.mp3")

    def test_pls_empty_file(self, tmp_path: Path) -> None:
        """Empty PLS file returns empty list."""
        pls = tmp_path / "empty.pls"
        _write_text(pls, "")

        entries = parse_pls(pls)
        assert entries == []

    def test_pls_missing_file(self, tmp_path: Path) -> None:
        """Non-existent PLS file returns empty list."""
        entries = parse_pls(tmp_path / "missing.pls")
        assert entries == []


# ---------------------------------------------------------------------------
# PLS writing
# ---------------------------------------------------------------------------


class TestWritePls:
    def test_write_basic(self, tmp_path: Path) -> None:
        """Write a PLS file with track objects."""
        pls = tmp_path / "out.pls"
        tracks = [
            FakeTrack(path="/music/a.mp3", title="Song A", duration_ms=180_000),
            FakeTrack(path="/music/b.flac", title="Song B", duration_ms=240_000),
        ]

        write_pls(pls, tracks, playlist_name="My Playlist")

        content = pls.read_text(encoding="utf-8")
        assert "[playlist]" in content
        assert "PlaylistName=My Playlist" in content
        assert "File1=/music/a.mp3" in content
        assert "Title1=Song A" in content
        assert "Length1=180" in content
        assert "File2=/music/b.flac" in content
        assert "Title2=Song B" in content
        assert "Length2=240" in content
        assert "NumberOfEntries=2" in content
        assert "Version=2" in content

    def test_write_string_paths(self, tmp_path: Path) -> None:
        """Plain strings are written as file entries."""
        pls = tmp_path / "strings.pls"
        write_pls(pls, ["/music/a.mp3", "/music/b.flac"])

        content = pls.read_text(encoding="utf-8")
        assert "File1=/music/a.mp3" in content
        assert "File2=/music/b.flac" in content
        assert "Length1=-1" in content
        assert "NumberOfEntries=2" in content

    def test_write_creates_parent_dirs(self, tmp_path: Path) -> None:
        """Parent directories are created if missing."""
        pls = tmp_path / "sub" / "dir" / "out.pls"
        write_pls(pls, [FakeTrack()])
        assert pls.exists()

    def test_write_empty(self, tmp_path: Path) -> None:
        """Writing empty playlist produces valid PLS."""
        pls = tmp_path / "empty.pls"
        write_pls(pls, [])

        content = pls.read_text(encoding="utf-8")
        assert "[playlist]" in content
        assert "NumberOfEntries=0" in content


# ---------------------------------------------------------------------------
# PLS roundtrip
# ---------------------------------------------------------------------------


class TestPlsRoundtrip:
    def test_write_then_parse(self, tmp_path: Path) -> None:
        """Write and parse PLS — metadata roundtrips."""
        pls = tmp_path / "roundtrip.pls"
        tracks = [
            FakeTrack(path="/music/a.mp3", title="Alpha", duration_ms=180_000),
            FakeTrack(path="/music/b.flac", title="Beta", duration_ms=240_000),
        ]

        write_pls(pls, tracks)
        entries = parse_pls(pls)

        assert len(entries) == 2
        assert entries[0].path == _p("/music/a.mp3")
        assert entries[0].title == "Alpha"
        assert entries[0].duration_seconds == 180
        assert entries[1].path == _p("/music/b.flac")
        assert entries[1].title == "Beta"
        assert entries[1].duration_seconds == 240


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------


class TestFormatDetection:
    def test_playlist_extensions(self) -> None:
        """PLAYLIST_EXTENSIONS contains expected formats."""
        assert ".m3u" in PLAYLIST_EXTENSIONS
        assert ".m3u8" in PLAYLIST_EXTENSIONS
        assert ".pls" in PLAYLIST_EXTENSIONS

    def test_is_playlist_file(self) -> None:
        """is_playlist_file matches expected extensions."""
        assert is_playlist_file(Path("test.m3u")) is True
        assert is_playlist_file(Path("test.M3U")) is True
        assert is_playlist_file(Path("test.m3u8")) is True
        assert is_playlist_file(Path("test.pls")) is True
        assert is_playlist_file(Path("test.PLS")) is True
        assert is_playlist_file(Path("test.mp3")) is False
        assert is_playlist_file(Path("test.flac")) is False

    def test_parse_playlist_file_m3u(self, tmp_path: Path) -> None:
        """Auto-detect M3U format."""
        m3u = tmp_path / "test.m3u"
        _write_text(m3u, "/music/song.mp3\n")

        entries = parse_playlist_file(m3u)
        assert len(entries) == 1

    def test_parse_playlist_file_m3u8(self, tmp_path: Path) -> None:
        """Auto-detect M3U8 format."""
        m3u = tmp_path / "test.m3u8"
        _write_text(m3u, "/music/song.mp3\n")

        entries = parse_playlist_file(m3u)
        assert len(entries) == 1

    def test_parse_playlist_file_pls(self, tmp_path: Path) -> None:
        """Auto-detect PLS format."""
        pls = tmp_path / "test.pls"
        _write_text(
            pls,
            "[playlist]\nFile1=/music/song.mp3\nNumberOfEntries=1\nVersion=2\n",
        )

        entries = parse_playlist_file(pls)
        assert len(entries) == 1

    def test_parse_playlist_file_unsupported(self, tmp_path: Path) -> None:
        """Unsupported extension raises ValueError."""
        txt = tmp_path / "test.txt"
        _write_text(txt, "/music/song.mp3\n")

        with pytest.raises(ValueError, match="Unsupported"):
            parse_playlist_file(txt)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_m3u_with_only_urls(self, tmp_path: Path) -> None:
        """M3U with only streaming URLs."""
        m3u = tmp_path / "streams.m3u"
        _write_text(
            m3u,
            "#EXTM3U\n"
            "#EXTINF:-1,Radio Station 1\n"
            "http://radio.example.com/stream1\n"
            "#EXTINF:-1,Radio Station 2\n"
            "https://radio.example.com/stream2\n",
        )

        entries = parse_m3u(m3u)
        assert len(entries) == 2
        assert entries[0].path == "http://radio.example.com/stream1"
        assert entries[0].title == "Radio Station 1"
        assert entries[1].path == "https://radio.example.com/stream2"

    def test_m3u_mixed_absolute_relative_url(self, tmp_path: Path) -> None:
        """M3U with mixed path types."""
        music_dir = tmp_path / "music"
        music_dir.mkdir()
        (music_dir / "local.mp3").touch()

        m3u = music_dir / "mixed.m3u"
        _write_text(
            m3u,
            "/absolute/path.mp3\n"
            "local.mp3\n"
            "http://example.com/remote.mp3\n",
        )

        entries = parse_m3u(m3u)
        assert len(entries) == 3
        assert entries[0].path == _p("/absolute/path.mp3")
        assert entries[1].path == str((music_dir / "local.mp3").resolve())
        assert entries[2].path == "http://example.com/remote.mp3"

    def test_m3u_whitespace_handling(self, tmp_path: Path) -> None:
        """Lines with leading/trailing whitespace are trimmed."""
        m3u = tmp_path / "space.m3u"
        _write_text(
            m3u,
            "  #EXTM3U  \n"
            "  #EXTINF:120,Trimmed  \n"
            "  /music/song.mp3  \n",
        )

        entries = parse_m3u(m3u)
        assert len(entries) == 1
        assert entries[0].title == "Trimmed"
        assert entries[0].path == "/music/song.mp3"

    def test_pls_with_spaces_around_equals(self, tmp_path: Path) -> None:
        """PLS with spaces around '=' signs."""
        pls = tmp_path / "space.pls"
        _write_text(
            pls,
            "[playlist]\n"
            "File1 = /music/a.mp3\n"
            "Title1 = Song A\n"
            "Length1 = 180\n"
            "NumberOfEntries=1\n"
            "Version=2\n",
        )

        entries = parse_pls(pls)
        assert len(entries) == 1
        assert entries[0].path == "/music/a.mp3"
        assert entries[0].title == "Song A"
        assert entries[0].duration_seconds == 180

    def test_m3u_no_extinf_header(self, tmp_path: Path) -> None:
        """Simple M3U without #EXTM3U header works."""
        m3u = tmp_path / "simple.m3u"
        _write_text(
            m3u,
            "/music/a.mp3\n"
            "/music/b.mp3\n",
        )

        entries = parse_m3u(m3u)
        assert len(entries) == 2

    def test_frozen_entry(self) -> None:
        """PlaylistFileEntry is immutable."""
        entry = PlaylistFileEntry(path="/test.mp3", title="Test")
        with pytest.raises(AttributeError):
            entry.path = "/other.mp3"  # type: ignore[misc]

    def test_write_m3u_atomic_no_corrupt_on_existing(self, tmp_path: Path) -> None:
        """Writing M3U uses atomic rename so an existing file isn't corrupted mid-write."""
        m3u = tmp_path / "atomic.m3u"

        # Write initial content
        write_m3u(m3u, [FakeTrack(path="/music/first.mp3", title="First")])
        assert m3u.exists()

        # Overwrite
        write_m3u(m3u, [FakeTrack(path="/music/second.mp3", title="Second")])

        content = m3u.read_text(encoding="utf-8")
        assert "second.mp3" in content
        assert "first.mp3" not in content

        # No leftover temp file
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0

    def test_large_playlist(self, tmp_path: Path) -> None:
        """Writing and parsing a large playlist (1000 tracks)."""
        m3u = tmp_path / "large.m3u"
        tracks = [
            FakeTrack(
                path=f"/music/track_{i:04d}.mp3",
                title=f"Track {i}",
                artist="Artist",
                album="Big Album",
                duration_ms=(180 + i) * 1000,
            )
            for i in range(1000)
        ]

        write_m3u(m3u, tracks)
        entries = parse_m3u(m3u)

        assert len(entries) == 1000
        assert entries[0].title == "Track 0"
        assert entries[999].title == "Track 999"
        assert entries[500].duration_seconds == 680
