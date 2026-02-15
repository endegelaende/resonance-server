"""
Persistence layer for the Podcast Plugin.

Manages three data sets in a single JSON file:

1. **Subscriptions** — podcast feeds the user has subscribed to
2. **Resume positions** — per-episode playback positions (seconds)
3. **Recently played** — LRU list of recently played episodes

All data is stored in ``data/plugins/podcast/podcasts.json`` with atomic
writes (write-to-tmp → rename) to prevent corruption on crash.

LMS Reference
~~~~~~~~~~~~~

LMS stores subscriptions in prefs (``plugin.podcast.feeds``), resume
positions in the global cache (``podcast-<url>``), and recently played
in prefs (``plugin.podcast.recent``).  We consolidate everything into
one JSON file for simplicity and portability.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

MAX_RECENT = 50
"""Maximum number of recently played episodes to keep (matches LMS)."""

RESUME_THRESHOLD = 15
"""Minimum seconds from start/end to store a resume position.

If the user has played less than 15 seconds or is within 15 seconds of
the end, the resume position is cleared (episode considered unplayed or
finished).  Matches LMS ``ProtocolHandler.pm::onStop``."""


@dataclass
class Subscription:
    """A subscribed podcast feed."""

    name: str
    """Display name of the podcast."""

    url: str
    """RSS feed URL."""

    image: str = ""
    """Cover art URL (cached from feed)."""

    author: str = ""
    """Author / creator."""

    description: str = ""
    """Short description."""

    added_at: float = 0.0
    """Unix timestamp when the subscription was added."""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "url": self.url,
        }
        if self.image:
            d["image"] = self.image
        if self.author:
            d["author"] = self.author
        if self.description:
            d["description"] = self.description
        if self.added_at:
            d["added_at"] = self.added_at
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Subscription:
        return cls(
            name=str(data.get("name", "")),
            url=str(data.get("url", "")),
            image=str(data.get("image", "")),
            author=str(data.get("author", "")),
            description=str(data.get("description", "")),
            added_at=float(data.get("added_at", 0.0)),
        )


@dataclass
class RecentEpisode:
    """A recently played episode."""

    url: str
    """Episode audio URL."""

    title: str = ""
    """Episode title."""

    show: str = ""
    """Podcast / show name."""

    image: str = ""
    """Episode or show artwork URL."""

    duration: int = 0
    """Duration in seconds (0 = unknown)."""

    feed_url: str = ""
    """RSS feed URL of the parent podcast."""

    played_at: float = 0.0
    """Unix timestamp of last playback."""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"url": self.url}
        if self.title:
            d["title"] = self.title
        if self.show:
            d["show"] = self.show
        if self.image:
            d["image"] = self.image
        if self.duration:
            d["duration"] = self.duration
        if self.feed_url:
            d["feed_url"] = self.feed_url
        if self.played_at:
            d["played_at"] = self.played_at
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RecentEpisode:
        return cls(
            url=str(data.get("url", "")),
            title=str(data.get("title", "")),
            show=str(data.get("show", "")),
            image=str(data.get("image", "")),
            duration=int(data.get("duration", 0)),
            feed_url=str(data.get("feed_url", "")),
            played_at=float(data.get("played_at", 0.0)),
        )


# ---------------------------------------------------------------------------
# PodcastStore
# ---------------------------------------------------------------------------


class PodcastStore:
    """JSON-backed persistence for podcast data.

    Thread-safety: This store is designed for single-threaded async use
    (one writer at a time).  All mutations call :meth:`save` synchronously.

    File format::

        {
            "subscriptions": [ { "name": "...", "url": "..." }, ... ],
            "resume": { "<episode_url>": <seconds_int>, ... },
            "recent": [ { "url": "...", "title": "...", ... }, ... ]
        }
    """

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._path = data_dir / "podcasts.json"

        self._subscriptions: list[Subscription] = []
        self._resume: dict[str, int] = {}
        self._recent: list[RecentEpisode] = []
        self._url_index: dict[str, int] = {}  # url → index in _subscriptions

    # -- Properties ----------------------------------------------------------

    @property
    def subscriptions(self) -> list[Subscription]:
        """All subscribed feeds (read-only copy)."""
        return list(self._subscriptions)

    @property
    def subscription_count(self) -> int:
        return len(self._subscriptions)

    @property
    def recent(self) -> list[RecentEpisode]:
        """Recently played episodes, newest first (read-only copy)."""
        return list(self._recent)

    @property
    def recent_count(self) -> int:
        return len(self._recent)

    # -- Load / Save ---------------------------------------------------------

    def load(self) -> None:
        """Load data from disk.  Silently starts empty if file is missing
        or corrupt."""
        if not self._path.is_file():
            logger.debug("No podcast store file at %s — starting empty", self._path)
            return

        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load podcast store %s: %s — starting empty", self._path, exc)
            return

        if not isinstance(data, dict):
            logger.warning("Podcast store root is not a dict — starting empty")
            return

        # Subscriptions
        raw_subs = data.get("subscriptions", [])
        if isinstance(raw_subs, list):
            self._subscriptions = [
                Subscription.from_dict(s) for s in raw_subs
                if isinstance(s, dict) and s.get("url")
            ]
        self._rebuild_url_index()

        # Resume positions
        raw_resume = data.get("resume", {})
        if isinstance(raw_resume, dict):
            self._resume = {
                str(k): int(v)
                for k, v in raw_resume.items()
                if isinstance(v, (int, float)) and v > 0
            }

        # Recently played
        raw_recent = data.get("recent", [])
        if isinstance(raw_recent, list):
            self._recent = [
                RecentEpisode.from_dict(r) for r in raw_recent
                if isinstance(r, dict) and r.get("url")
            ]

        logger.info(
            "Loaded podcast store: %d subscriptions, %d resume positions, %d recent",
            len(self._subscriptions),
            len(self._resume),
            len(self._recent),
        )

    def save(self) -> None:
        """Persist current state to disk with atomic write."""
        self._data_dir.mkdir(parents=True, exist_ok=True)

        data = {
            "subscriptions": [s.to_dict() for s in self._subscriptions],
            "resume": self._resume,
            "recent": [r.to_dict() for r in self._recent],
        }

        # Atomic write: write to temp file, then rename
        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=str(self._data_dir), suffix=".tmp", prefix="podcasts_"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
            except BaseException:
                os.unlink(tmp_path)
                raise

            # On Windows, rename fails if target exists — remove first
            if self._path.exists():
                self._path.unlink()
            os.rename(tmp_path, self._path)

        except OSError as exc:
            logger.error("Failed to save podcast store: %s", exc)

    # -- Subscriptions -------------------------------------------------------

    def is_subscribed(self, feed_url: str) -> bool:
        """Check if a feed URL is subscribed."""
        return feed_url in self._url_index

    def get_subscription(self, feed_url: str) -> Subscription | None:
        """Get a subscription by feed URL."""
        idx = self._url_index.get(feed_url)
        if idx is not None and idx < len(self._subscriptions):
            return self._subscriptions[idx]
        return None

    def add_subscription(
        self,
        url: str,
        name: str,
        *,
        image: str = "",
        author: str = "",
        description: str = "",
    ) -> bool:
        """Subscribe to a podcast feed.

        Returns ``True`` if the subscription was added, ``False`` if
        already subscribed (no duplicate).
        """
        if url in self._url_index:
            logger.debug("Already subscribed to %s", url)
            return False

        sub = Subscription(
            name=name,
            url=url,
            image=image,
            author=author,
            description=description,
            added_at=time.time(),
        )
        self._subscriptions.append(sub)
        self._url_index[url] = len(self._subscriptions) - 1
        self.save()

        logger.info("Subscribed to podcast: %s (%s)", name, url)
        return True

    def remove_subscription(self, url: str) -> bool:
        """Unsubscribe from a podcast feed.

        Returns ``True`` if the subscription was removed.
        """
        if url not in self._url_index:
            return False

        self._subscriptions = [s for s in self._subscriptions if s.url != url]
        self._rebuild_url_index()
        self.save()

        logger.info("Unsubscribed from podcast: %s", url)
        return True

    def update_subscription(
        self,
        url: str,
        *,
        name: str | None = None,
        image: str | None = None,
        author: str | None = None,
        description: str | None = None,
    ) -> bool:
        """Update metadata for an existing subscription.

        Only provided (non-None) fields are updated.
        Returns ``True`` if the subscription was found and updated.
        """
        idx = self._url_index.get(url)
        if idx is None or idx >= len(self._subscriptions):
            return False

        old = self._subscriptions[idx]
        self._subscriptions[idx] = Subscription(
            name=name if name is not None else old.name,
            url=old.url,
            image=image if image is not None else old.image,
            author=author if author is not None else old.author,
            description=description if description is not None else old.description,
            added_at=old.added_at,
        )
        self.save()
        return True

    # -- Resume positions ----------------------------------------------------

    def get_resume_position(self, episode_url: str) -> int:
        """Get the resume position for an episode in seconds.

        Returns ``0`` if no position is stored.
        """
        return self._resume.get(episode_url, 0)

    def set_resume_position(
        self,
        episode_url: str,
        seconds: int,
        duration: int = 0,
    ) -> None:
        """Store the resume position for an episode.

        Applies LMS-style threshold logic:
        - If ``seconds < RESUME_THRESHOLD``: position is cleared (not started)
        - If ``duration > 0`` and ``seconds > duration - RESUME_THRESHOLD``:
          position is cleared (finished)
        - Otherwise: position is stored

        Args:
            episode_url: Episode audio URL.
            seconds: Current playback position in seconds.
            duration: Episode duration in seconds (0 = unknown).
        """
        if seconds < RESUME_THRESHOLD:
            self._resume.pop(episode_url, None)
        elif duration > 0 and seconds > duration - RESUME_THRESHOLD:
            self._resume.pop(episode_url, None)
        else:
            self._resume[episode_url] = int(seconds)

        self.save()

    def clear_resume_position(self, episode_url: str) -> None:
        """Remove the resume position for an episode."""
        if episode_url in self._resume:
            del self._resume[episode_url]
            self.save()

    def has_resume_position(self, episode_url: str) -> bool:
        """Check if a resume position exists for an episode."""
        return episode_url in self._resume

    @property
    def resume_positions(self) -> dict[str, int]:
        """All resume positions (read-only copy)."""
        return dict(self._resume)

    # -- Recently played -----------------------------------------------------

    def record_played(
        self,
        url: str,
        *,
        title: str = "",
        show: str = "",
        image: str = "",
        duration: int = 0,
        feed_url: str = "",
    ) -> None:
        """Record an episode as recently played.

        If the episode is already in the recent list, it is moved to the
        front (most recent).  The list is trimmed to :data:`MAX_RECENT`.
        """
        # Remove existing entry for the same URL (dedup)
        self._recent = [r for r in self._recent if r.url != url]

        entry = RecentEpisode(
            url=url,
            title=title,
            show=show,
            image=image,
            duration=duration,
            feed_url=feed_url,
            played_at=time.time(),
        )

        # Insert at front (newest first)
        self._recent.insert(0, entry)

        # Trim to max size
        if len(self._recent) > MAX_RECENT:
            self._recent = self._recent[:MAX_RECENT]

        self.save()

    def clear_recent(self) -> None:
        """Clear all recently played entries."""
        self._recent.clear()
        self.save()

    # -- Bulk operations -----------------------------------------------------

    def clear_all(self) -> None:
        """Reset all data (subscriptions, resume, recent)."""
        self._subscriptions.clear()
        self._resume.clear()
        self._recent.clear()
        self._url_index.clear()
        self.save()

    # -- Internal helpers ----------------------------------------------------

    def _rebuild_url_index(self) -> None:
        """Rebuild the URL → index lookup for subscriptions."""
        self._url_index = {
            sub.url: idx for idx, sub in enumerate(self._subscriptions)
        }
