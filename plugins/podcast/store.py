"""
Persistence layer for the Podcast Plugin (v2).

Manages five data sets in a single JSON file:

1. **Subscriptions** — podcast feeds the user has subscribed to
2. **Resume positions** — per-episode playback positions (seconds)
3. **Recently played** — LRU list of recently played episodes
4. **Played episodes** — set of episode URLs fully listened to
5. **Episode progress** — per-episode progress metadata (position, duration, %)

All data is stored in ``data/plugins/podcast/podcasts.json`` with atomic
writes (write-to-tmp → rename) to prevent corruption on crash.

v2 enhancements over v1
~~~~~~~~~~~~~~~~~~~~~~~~

* **Episode progress tracking** — stores position + duration + percentage
  per episode, enabling progress bars in the UI and "continue listening"
  across sessions.
* **Played / unplayed state** — explicitly tracks which episodes have been
  fully consumed (auto-marked at configurable % threshold, or manual).
* **New episode tracking** — per-feed timestamp of last-browsed, so the
  "What's New" aggregator knows which episodes are genuinely new.
* **Configurable limits** — ``max_recent`` and ``auto_mark_played_percent``
  are constructor parameters rather than module-level constants.
* **Subscription ordering** — subscriptions can be reordered (move up/down).
* **Bulk operations** — ``import_subscriptions()`` for OPML import.

LMS Reference
~~~~~~~~~~~~~

LMS stores subscriptions in prefs (``plugin.podcast.feeds``), resume
positions in the global cache (``podcast-<url>``), and recently played
in prefs (``plugin.podcast.recent``).  We consolidate everything into
one JSON file for simplicity and portability, and add the played-state
and progress-tracking layers that LMS lacks entirely.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults (overridable via constructor)
# ---------------------------------------------------------------------------

DEFAULT_MAX_RECENT = 50
"""Default maximum number of recently played episodes to keep."""

DEFAULT_RESUME_THRESHOLD = 15
"""Minimum seconds from start/end to store a resume position.

If the user has played less than 15 seconds or is within 15 seconds of
the end, the resume position is cleared (episode considered unplayed or
finished).  Matches LMS ``ProtocolHandler.pm::onStop``."""

DEFAULT_AUTO_MARK_PLAYED_PERCENT = 90
"""Percentage of an episode that must be played to auto-mark as played."""

MAX_PLAYED_HISTORY = 5000
"""Upper bound on tracked played-episode URLs (memory guard)."""

MAX_PROGRESS_ENTRIES = 2000
"""Upper bound on tracked episode-progress entries (memory guard)."""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


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

    last_browsed_at: float = 0.0
    """Unix timestamp of the last time the user browsed this feed's episodes.
    Used by the "What's New" aggregator to determine which episodes are new."""

    new_episode_count: int = 0
    """Cached count of new episodes since last browse (UI badge)."""

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
        if self.last_browsed_at:
            d["last_browsed_at"] = self.last_browsed_at
        if self.new_episode_count:
            d["new_episode_count"] = self.new_episode_count
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
            last_browsed_at=float(data.get("last_browsed_at", 0.0)),
            new_episode_count=int(data.get("new_episode_count", 0)),
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


@dataclass
class EpisodeProgress:
    """Playback progress metadata for a single episode.

    Enables progress bars, "continue listening" recommendations, and
    auto-mark-as-played functionality.
    """

    position: int = 0
    """Current position in seconds."""

    duration: int = 0
    """Total duration in seconds (0 = unknown)."""

    percentage: float = 0.0
    """Completion percentage (0.0–100.0)."""

    updated_at: float = 0.0
    """Unix timestamp of the last progress update."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "position": self.position,
            "duration": self.duration,
            "percentage": round(self.percentage, 1),
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EpisodeProgress:
        return cls(
            position=int(data.get("position", 0)),
            duration=int(data.get("duration", 0)),
            percentage=float(data.get("percentage", 0.0)),
            updated_at=float(data.get("updated_at", 0.0)),
        )


# ---------------------------------------------------------------------------
# PodcastStore
# ---------------------------------------------------------------------------


class PodcastStore:
    """JSON-backed persistence for podcast data.

    Thread-safety: This store is designed for single-threaded async use
    (one writer at a time).  All mutations call :meth:`save` synchronously.

    File format (v2)::

        {
            "version": 2,
            "subscriptions": [ { "name": "...", "url": "...", ... }, ... ],
            "resume": { "<episode_url>": <seconds_int>, ... },
            "recent": [ { "url": "...", "title": "...", ... }, ... ],
            "played": [ "<episode_url>", ... ],
            "progress": { "<episode_url>": { "position": N, ... }, ... }
        }

    Parameters:
        data_dir: Directory for the JSON file (``podcasts.json``).
        max_recent: Maximum recently-played entries to retain.
        auto_mark_played_percent: Auto-mark an episode as played at this %.
        resume_threshold: Seconds threshold for resume logic.
    """

    def __init__(
        self,
        data_dir: Path,
        *,
        max_recent: int = DEFAULT_MAX_RECENT,
        auto_mark_played_percent: int = DEFAULT_AUTO_MARK_PLAYED_PERCENT,
        resume_threshold: int = DEFAULT_RESUME_THRESHOLD,
    ) -> None:
        self._data_dir = data_dir
        self._path = data_dir / "podcasts.json"
        self._max_recent = max(10, max_recent)
        self._auto_mark_played_percent = max(50, min(100, auto_mark_played_percent))
        self._resume_threshold = max(1, resume_threshold)

        self._subscriptions: list[Subscription] = []
        self._resume: dict[str, int] = {}
        self._recent: list[RecentEpisode] = []
        self._played: set[str] = set()
        self._progress: dict[str, EpisodeProgress] = {}
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

    @property
    def played_count(self) -> int:
        """Number of episodes marked as played."""
        return len(self._played)

    @property
    def progress_count(self) -> int:
        """Number of episodes with progress data."""
        return len(self._progress)

    @property
    def total_new_episodes(self) -> int:
        """Sum of new_episode_count across all subscriptions."""
        return sum(s.new_episode_count for s in self._subscriptions)

    # -- Configuration updates (for live settings changes) -------------------

    def update_max_recent(self, value: int) -> None:
        """Update the max-recent limit and trim if needed."""
        self._max_recent = max(10, value)
        if len(self._recent) > self._max_recent:
            self._recent = self._recent[: self._max_recent]
            self.save()

    def update_auto_mark_played_percent(self, value: int) -> None:
        """Update the auto-mark-played threshold."""
        self._auto_mark_played_percent = max(50, min(100, value))

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

        # Played episodes (v2)
        raw_played = data.get("played", [])
        if isinstance(raw_played, list):
            self._played = {
                str(url) for url in raw_played
                if isinstance(url, str) and url
            }

        # Episode progress (v2)
        raw_progress = data.get("progress", {})
        if isinstance(raw_progress, dict):
            self._progress = {}
            for k, v in raw_progress.items():
                if isinstance(v, dict):
                    self._progress[str(k)] = EpisodeProgress.from_dict(v)

        logger.info(
            "Loaded podcast store: %d subs, %d resume, %d recent, %d played, %d progress",
            len(self._subscriptions),
            len(self._resume),
            len(self._recent),
            len(self._played),
            len(self._progress),
        )

    def save(self) -> None:
        """Persist current state to disk with atomic write."""
        self._data_dir.mkdir(parents=True, exist_ok=True)

        data: dict[str, Any] = {
            "version": 2,
            "subscriptions": [s.to_dict() for s in self._subscriptions],
            "resume": self._resume,
            "recent": [r.to_dict() for r in self._recent],
            "played": sorted(self._played),
            "progress": {k: v.to_dict() for k, v in self._progress.items()},
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
            last_browsed_at=old.last_browsed_at,
            new_episode_count=old.new_episode_count,
        )
        self.save()
        return True

    def move_subscription(self, url: str, direction: int) -> bool:
        """Move a subscription up (direction=-1) or down (direction=1).

        Returns ``True`` if the move was performed.
        """
        idx = self._url_index.get(url)
        if idx is None:
            return False

        new_idx = idx + direction
        if new_idx < 0 or new_idx >= len(self._subscriptions):
            return False

        # Swap
        self._subscriptions[idx], self._subscriptions[new_idx] = (
            self._subscriptions[new_idx],
            self._subscriptions[idx],
        )
        self._rebuild_url_index()
        self.save()
        return True

    def import_subscriptions(
        self,
        feeds: list[dict[str, Any]],
    ) -> tuple[int, int]:
        """Bulk-import subscriptions (e.g. from OPML).

        Returns ``(added_count, skipped_count)``.
        """
        added = 0
        skipped = 0
        for feed in feeds:
            url = str(feed.get("url", ""))
            name = str(feed.get("name", "")) or url
            if not url:
                skipped += 1
                continue
            if url in self._url_index:
                skipped += 1
                continue

            sub = Subscription(
                name=name,
                url=url,
                image=str(feed.get("image", "") or feed.get("image_url", "")),
                author=str(feed.get("author", "")),
                description=str(feed.get("description", "")),
                added_at=time.time(),
            )
            self._subscriptions.append(sub)
            added += 1

        if added > 0:
            self._rebuild_url_index()
            self.save()
            logger.info("Imported %d subscriptions (%d skipped)", added, skipped)

        return added, skipped

    def mark_feed_browsed(self, feed_url: str) -> None:
        """Record that the user has browsed a feed's episodes.

        Resets the new-episode count and updates the last_browsed_at timestamp.
        """
        idx = self._url_index.get(feed_url)
        if idx is None or idx >= len(self._subscriptions):
            return

        old = self._subscriptions[idx]
        self._subscriptions[idx] = Subscription(
            name=old.name,
            url=old.url,
            image=old.image,
            author=old.author,
            description=old.description,
            added_at=old.added_at,
            last_browsed_at=time.time(),
            new_episode_count=0,
        )
        self.save()

    def set_new_episode_count(self, feed_url: str, count: int) -> None:
        """Update the cached new-episode count for a subscription."""
        idx = self._url_index.get(feed_url)
        if idx is None or idx >= len(self._subscriptions):
            return

        old = self._subscriptions[idx]
        if old.new_episode_count == count:
            return

        self._subscriptions[idx] = Subscription(
            name=old.name,
            url=old.url,
            image=old.image,
            author=old.author,
            description=old.description,
            added_at=old.added_at,
            last_browsed_at=old.last_browsed_at,
            new_episode_count=max(0, count),
        )
        self.save()

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
        - If ``seconds < threshold``: position is cleared (not started)
        - If ``duration > 0`` and ``seconds > duration - threshold``:
          position is cleared (finished)
        - Otherwise: position is stored

        Also auto-marks the episode as played when the configured % is reached.

        Args:
            episode_url: Episode audio URL.
            seconds: Current playback position in seconds.
            duration: Episode duration in seconds (0 = unknown).
        """
        if seconds < self._resume_threshold:
            self._resume.pop(episode_url, None)
        elif duration > 0 and seconds > duration - self._resume_threshold:
            self._resume.pop(episode_url, None)
            # Near the end → auto-mark as played
            self.mark_played(episode_url, _save=False)
        else:
            self._resume[episode_url] = int(seconds)

        # Update progress tracking
        if duration > 0:
            self._update_progress(episode_url, int(seconds), duration, _save=False)

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

    # -- Episode progress tracking -------------------------------------------

    def get_progress(self, episode_url: str) -> EpisodeProgress | None:
        """Get progress data for an episode, or ``None`` if not tracked."""
        return self._progress.get(episode_url)

    def get_progress_percentage(self, episode_url: str) -> float:
        """Get the completion percentage for an episode (0.0–100.0)."""
        prog = self._progress.get(episode_url)
        return prog.percentage if prog else 0.0

    def update_progress(
        self,
        episode_url: str,
        position: int,
        duration: int,
    ) -> None:
        """Update progress for an episode (public interface)."""
        self._update_progress(episode_url, position, duration, _save=True)

    def _update_progress(
        self,
        episode_url: str,
        position: int,
        duration: int,
        *,
        _save: bool = True,
    ) -> None:
        """Internal progress updater with optional save."""
        if duration <= 0:
            return

        percentage = min(100.0, (position / duration) * 100.0)

        self._progress[episode_url] = EpisodeProgress(
            position=position,
            duration=duration,
            percentage=round(percentage, 1),
            updated_at=time.time(),
        )

        # Auto-mark as played at configured threshold
        if percentage >= self._auto_mark_played_percent:
            self._played.add(episode_url)

        # Trim progress entries if over limit (remove oldest)
        if len(self._progress) > MAX_PROGRESS_ENTRIES:
            sorted_entries = sorted(
                self._progress.items(),
                key=lambda item: item[1].updated_at,
            )
            # Remove oldest 20%
            remove_count = len(sorted_entries) // 5
            for key, _ in sorted_entries[:remove_count]:
                del self._progress[key]

        if _save:
            self.save()

    def get_all_progress(self) -> dict[str, EpisodeProgress]:
        """Get all progress data (read-only copy)."""
        return dict(self._progress)

    def clear_progress(self, episode_url: str) -> None:
        """Remove progress data for an episode."""
        if episode_url in self._progress:
            del self._progress[episode_url]
            self.save()

    # -- Played / unplayed ---------------------------------------------------

    def is_played(self, episode_url: str) -> bool:
        """Check if an episode is marked as played."""
        return episode_url in self._played

    def mark_played(self, episode_url: str, *, _save: bool = True) -> None:
        """Mark an episode as fully played."""
        if episode_url not in self._played:
            self._played.add(episode_url)

            # Trim if over limit (remove arbitrary old entries)
            if len(self._played) > MAX_PLAYED_HISTORY:
                # Convert to list, remove oldest entries (roughly)
                excess = len(self._played) - MAX_PLAYED_HISTORY
                played_list = list(self._played)
                for url in played_list[:excess]:
                    self._played.discard(url)

            if _save:
                self.save()

    def mark_unplayed(self, episode_url: str) -> None:
        """Mark an episode as not played (reset)."""
        changed = False
        if episode_url in self._played:
            self._played.discard(episode_url)
            changed = True
        if episode_url in self._resume:
            del self._resume[episode_url]
            changed = True
        if episode_url in self._progress:
            del self._progress[episode_url]
            changed = True
        if changed:
            self.save()

    def mark_all_played(self, episode_urls: list[str]) -> int:
        """Mark multiple episodes as played. Returns count actually changed."""
        changed = 0
        for url in episode_urls:
            if url not in self._played:
                self._played.add(url)
                changed += 1
        if changed:
            self.save()
        return changed

    def mark_feed_played(self, episode_urls: list[str]) -> int:
        """Mark all episodes from a feed as played.

        Convenience for "mark all as played" on a subscription.
        Same as ``mark_all_played`` but named for clarity.
        """
        return self.mark_all_played(episode_urls)

    @property
    def played_episodes(self) -> set[str]:
        """All played episode URLs (read-only copy)."""
        return set(self._played)

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
        front (most recent).  The list is trimmed to ``max_recent``.
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
        if len(self._recent) > self._max_recent:
            self._recent = self._recent[: self._max_recent]

        self.save()

    def clear_recent(self) -> None:
        """Clear all recently played entries."""
        self._recent.clear()
        self.save()

    # -- Continue listening --------------------------------------------------

    def get_in_progress_episodes(self) -> list[dict[str, Any]]:
        """Get episodes that have been started but not finished.

        Returns a list of dicts sorted by most-recently-updated, containing:
        ``{url, position, duration, percentage, updated_at}``

        This powers the "Continue Listening" section in the UI.
        """
        in_progress: list[dict[str, Any]] = []

        for url, prog in self._progress.items():
            # Skip completed episodes
            if url in self._played:
                continue
            # Skip episodes with no meaningful progress
            if prog.position < self._resume_threshold:
                continue
            # Skip if nearly done (but not auto-marked for some reason)
            if prog.duration > 0 and prog.position >= prog.duration - self._resume_threshold:
                continue

            entry: dict[str, Any] = {
                "url": url,
                "position": prog.position,
                "duration": prog.duration,
                "percentage": prog.percentage,
                "updated_at": prog.updated_at,
            }

            # Enrich with recent-episode metadata if available
            for recent in self._recent:
                if recent.url == url:
                    entry["title"] = recent.title
                    entry["show"] = recent.show
                    entry["image"] = recent.image
                    entry["feed_url"] = recent.feed_url
                    break

            in_progress.append(entry)

        # Sort by most recently updated
        in_progress.sort(key=lambda e: e.get("updated_at", 0), reverse=True)

        return in_progress

    # -- Bulk operations -----------------------------------------------------

    def clear_all(self) -> None:
        """Reset all data (subscriptions, resume, recent, played, progress)."""
        self._subscriptions.clear()
        self._resume.clear()
        self._recent.clear()
        self._played.clear()
        self._progress.clear()
        self._url_index.clear()
        self.save()

    def get_stats(self) -> dict[str, Any]:
        """Get summary statistics for the podcast store."""
        in_progress = [
            url for url, prog in self._progress.items()
            if url not in self._played and prog.position >= self._resume_threshold
        ]
        return {
            "subscriptions": len(self._subscriptions),
            "resume_positions": len(self._resume),
            "recent_episodes": len(self._recent),
            "played_episodes": len(self._played),
            "in_progress_episodes": len(in_progress),
            "total_progress_entries": len(self._progress),
            "total_new_episodes": self.total_new_episodes,
        }

    def export_subscriptions(self) -> list[dict[str, Any]]:
        """Export subscriptions as plain dicts (for OPML export)."""
        return [s.to_dict() for s in self._subscriptions]

    # -- Internal helpers ----------------------------------------------------

    def _rebuild_url_index(self) -> None:
        """Rebuild the URL → index lookup for subscriptions."""
        self._url_index = {
            sub.url: idx for idx, sub in enumerate(self._subscriptions)
        }
