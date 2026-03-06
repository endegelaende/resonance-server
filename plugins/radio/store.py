"""
Persistent store for the Radio plugin.

Manages recently played stations with JSON-backed persistence and atomic
writes.  The store keeps a bounded list of station entries (FIFO) and
deduplicates by stream URL — replaying a station moves it to the top.

Storage format (``radio.json``)::

    {
        "version": 1,
        "recent": [
            {
                "url": "https://stream.example.com/live",
                "title": "Example FM",
                "icon": "https://example.com/logo.png",
                "codec": "MP3",
                "bitrate": 128,
                "country": "Germany",
                "countrycode": "DE",
                "tags": "pop, rock",
                "station_id": "abc-123-uuid",
                "provider": "radio-browser",
                "last_played": "2026-03-15T14:30:00+00:00",
                "play_count": 3
            },
            ...
        ]
    }

Thread safety: the store is designed for single-writer (the plugin's
async event handlers) and is not thread-safe.  All access happens on
the asyncio event loop.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Current storage format version.
_STORE_VERSION = 1


def _safe_int(value: Any) -> int:
    """Safely convert a value to int, defaulting to 0."""
    try:
        return int(value or 0)
    except (ValueError, TypeError):
        return 0

# Default maximum number of recent stations to keep.
_DEFAULT_MAX_RECENT = 50


@dataclass
class RecentStation:
    """A recently played radio station entry."""

    url: str
    """Direct stream URL."""

    title: str = ""
    """Station display name."""

    icon: str = ""
    """Station logo / favicon URL."""

    codec: str = ""
    """Audio codec (MP3, AAC, OGG, ...)."""

    bitrate: int = 0
    """Bitrate in kbps."""

    country: str = ""
    """Full country name."""

    countrycode: str = ""
    """ISO 3166-1 alpha-2 country code."""

    tags: str = ""
    """Comma-separated genre tags."""

    station_id: str = ""
    """Provider-specific station identifier (UUID for radio-browser)."""

    provider: str = ""
    """Which provider this station came from (e.g. 'radio-browser')."""

    last_played: str = ""
    """ISO 8601 timestamp of the last time this station was played."""

    play_count: int = 0
    """Number of times this station has been played."""

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict for JSON storage."""
        return {k: v for k, v in asdict(self).items() if v or k == "play_count"}

    @staticmethod
    def from_dict(data: dict[str, Any]) -> RecentStation:
        """Deserialise from a JSON dict."""
        return RecentStation(
            url=str(data.get("url", "")),
            title=str(data.get("title", "")),
            icon=str(data.get("icon", "")),
            codec=str(data.get("codec", "")),
            bitrate=_safe_int(data.get("bitrate", 0)),
            country=str(data.get("country", "")),
            countrycode=str(data.get("countrycode", "")),
            tags=str(data.get("tags", "")),
            station_id=str(data.get("station_id", "")),
            provider=str(data.get("provider", "")),
            last_played=str(data.get("last_played", "")),
            play_count=_safe_int(data.get("play_count", 0)),
        )


class RadioStore:
    """Persistent store for recently played radio stations.

    Args:
        data_dir: Directory where ``radio.json`` is stored.
        max_recent: Maximum number of recent stations to keep.
    """

    FILENAME = "radio.json"

    def __init__(
        self,
        data_dir: str | Path,
        max_recent: int = _DEFAULT_MAX_RECENT,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._file_path = self._data_dir / self.FILENAME
        self._max_recent = max(1, max_recent)
        self._recent: list[RecentStation] = []

    # -- Properties ----------------------------------------------------------

    @property
    def recent(self) -> list[RecentStation]:
        """Recently played stations, newest first."""
        return list(self._recent)

    @property
    def recent_count(self) -> int:
        """Number of recently played stations."""
        return len(self._recent)

    @property
    def max_recent(self) -> int:
        """Maximum number of recent stations kept."""
        return self._max_recent

    @max_recent.setter
    def max_recent(self, value: int) -> None:
        self._max_recent = max(1, value)
        self._trim()

    # -- Load / Save ---------------------------------------------------------

    def load(self) -> None:
        """Load store from disk.  Tolerates missing or corrupt files."""
        if not self._file_path.is_file():
            logger.debug("No radio store file at %s — starting fresh", self._file_path)
            return

        try:
            raw = self._file_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load radio store: %s — starting fresh", exc)
            self._recent = []
            return

        if not isinstance(data, dict):
            logger.warning("Radio store is not a JSON object — starting fresh")
            self._recent = []
            return

        version = data.get("version", 0)
        if version != _STORE_VERSION:
            logger.info(
                "Radio store version %s != %s — migrating",
                version, _STORE_VERSION,
            )
            # Future: migration logic goes here.

        recent_raw = data.get("recent", [])
        if isinstance(recent_raw, list):
            self._recent = [
                RecentStation.from_dict(entry)
                for entry in recent_raw
                if isinstance(entry, dict) and entry.get("url")
            ]
        else:
            self._recent = []

        self._trim()

        logger.info(
            "Loaded radio store: %d recent station(s) from %s",
            len(self._recent), self._file_path,
        )

    def save(self) -> None:
        """Save store to disk using atomic write (write-to-temp → rename).

        Creates the data directory if it doesn't exist.
        """
        self._data_dir.mkdir(parents=True, exist_ok=True)

        data = {
            "version": _STORE_VERSION,
            "recent": [s.to_dict() for s in self._recent],
        }

        json_str = json.dumps(data, indent=2, ensure_ascii=False)

        # Atomic write: write to temp file, then rename.
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self._data_dir),
            suffix=".tmp",
            prefix="radio_",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json_str)
                f.write("\n")
        except BaseException:
            os.unlink(tmp_path)
            raise

        # On Windows, rename fails if target exists — remove first.
        if self._file_path.exists():
            self._file_path.unlink()
        os.rename(tmp_path, self._file_path)

        logger.debug(
            "Saved radio store: %d recent station(s) to %s",
            len(self._recent), self._file_path,
        )

    # -- Recent stations API -------------------------------------------------

    def record_play(
        self,
        url: str,
        title: str = "",
        icon: str = "",
        codec: str = "",
        bitrate: int = 0,
        country: str = "",
        countrycode: str = "",
        tags: str = "",
        station_id: str = "",
        provider: str = "",
    ) -> RecentStation:
        """Record that a station was played.

        If the station (identified by URL) already exists in the recent
        list, it is moved to the front and its play count is incremented.
        Otherwise, a new entry is created at the front.

        Returns the (new or updated) :class:`RecentStation`.
        """
        now = datetime.now(timezone.utc).isoformat()

        # Look for existing entry by URL (case-insensitive).
        url_lower = url.lower().rstrip("/")
        existing_idx = None
        for i, entry in enumerate(self._recent):
            if entry.url.lower().rstrip("/") == url_lower:
                existing_idx = i
                break

        if existing_idx is not None:
            # Move to front and update metadata.
            existing = self._recent.pop(existing_idx)
            station = RecentStation(
                url=url or existing.url,
                title=title or existing.title,
                icon=icon or existing.icon,
                codec=codec or existing.codec,
                bitrate=bitrate or existing.bitrate,
                country=country or existing.country,
                countrycode=countrycode or existing.countrycode,
                tags=tags or existing.tags,
                station_id=station_id or existing.station_id,
                provider=provider or existing.provider,
                last_played=now,
                play_count=existing.play_count + 1,
            )
        else:
            station = RecentStation(
                url=url,
                title=title,
                icon=icon,
                codec=codec,
                bitrate=bitrate,
                country=country,
                countrycode=countrycode,
                tags=tags,
                station_id=station_id,
                provider=provider,
                last_played=now,
                play_count=1,
            )

        # Insert at front (newest first).
        self._recent.insert(0, station)
        self._trim()

        return station

    def remove(self, url: str) -> bool:
        """Remove a station from the recent list by URL.

        Returns ``True`` if a station was removed, ``False`` if not found.
        """
        url_lower = url.lower().rstrip("/")
        before = len(self._recent)
        self._recent = [
            s for s in self._recent
            if s.url.lower().rstrip("/") != url_lower
        ]
        removed = len(self._recent) < before
        if removed:
            logger.debug("Removed station from recent: %s", url)
        return removed

    def clear(self) -> None:
        """Remove all recently played stations."""
        self._recent.clear()
        logger.debug("Cleared all recent stations")

    def get_by_url(self, url: str) -> RecentStation | None:
        """Look up a recent station by URL."""
        url_lower = url.lower().rstrip("/")
        for station in self._recent:
            if station.url.lower().rstrip("/") == url_lower:
                return station
        return None

    def get_by_station_id(self, station_id: str) -> RecentStation | None:
        """Look up a recent station by its provider-specific ID."""
        if not station_id:
            return None
        for station in self._recent:
            if station.station_id == station_id:
                return station
        return None

    def get_most_played(self, limit: int = 10) -> list[RecentStation]:
        """Return the most frequently played stations, sorted by play count."""
        sorted_stations = sorted(
            self._recent,
            key=lambda s: s.play_count,
            reverse=True,
        )
        return sorted_stations[:limit]

    # -- Internal ------------------------------------------------------------

    def _trim(self) -> None:
        """Trim the recent list to the maximum size."""
        if len(self._recent) > self._max_recent:
            self._recent = self._recent[: self._max_recent]
