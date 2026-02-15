"""
Favorites Store — JSON-backed persistence with hierarchical dot-notation indices.

This module provides the data model and storage layer for the Favorites plugin.
Favorites are stored as a JSON file with support for nested folders, mirroring
the LMS OPML-based favorites structure but using a modern, simpler format.

Index notation (LMS-compatible):
- ``"0"``     → first top-level item
- ``"2"``     → third top-level item
- ``"1.0"``   → first child of the second top-level folder
- ``"1.2.0"`` → first child of a nested subfolder

The store maintains an in-memory URL→index mapping for fast lookups
(e.g. ``favorites exists``).  All mutations automatically rebuild
this index and persist to disk.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class FavoriteItem:
    """A single favorites entry — either an audio/playlist item or a folder.

    Attributes:
        title: Display title.
        url: Stream/file URL (``None`` for folders).
        type: ``"audio"``, ``"playlist"``, or ``"folder"``.
        icon: Optional icon URL for Jive devices.
        items: Nested children (non-empty only for folders).
    """

    title: str
    url: str | None = None
    type: str = "audio"
    icon: str | None = None
    items: list[FavoriteItem] = field(default_factory=list)

    # -- Predicates ----------------------------------------------------------

    @property
    def is_folder(self) -> bool:
        """Return ``True`` if this entry is a folder (has children or type is folder)."""
        return self.type == "folder" or bool(self.items)

    @property
    def is_playable(self) -> bool:
        """Return ``True`` if this entry can be played directly."""
        return self.url is not None and not self.is_folder

    # -- Serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-friendly dict (omitting empty/None fields)."""
        d: dict[str, Any] = {"title": self.title}
        if self.url is not None:
            d["url"] = self.url
        if self.type and self.type != "audio":
            d["type"] = self.type
        elif self.url is not None:
            d["type"] = self.type  # always include for playable items
        if self.icon is not None:
            d["icon"] = self.icon
        if self.items:
            d["items"] = [child.to_dict() for child in self.items]
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FavoriteItem:
        """Deserialise from a JSON dict."""
        children = [cls.from_dict(c) for c in data.get("items", [])]
        item_type = data.get("type", "folder" if children else "audio")
        return cls(
            title=data.get("title", ""),
            url=data.get("url"),
            type=item_type,
            icon=data.get("icon"),
            items=children,
        )

    # -- repr ----------------------------------------------------------------

    def __repr__(self) -> str:
        if self.is_folder:
            return f"FavoriteItem(folder={self.title!r}, children={len(self.items)})"
        return f"FavoriteItem(title={self.title!r}, url={self.url!r})"


# ---------------------------------------------------------------------------
# Index helpers
# ---------------------------------------------------------------------------


def _parse_index(index: str) -> list[int]:
    """Parse a dot-notation index string into a list of integer positions.

    Examples::

        >>> _parse_index("0")
        [0]
        >>> _parse_index("1.2.0")
        [1, 2, 0]

    Raises:
        ValueError: If *index* contains non-integer components.
    """
    if not index and index != "0":
        raise ValueError(f"Invalid index: {index!r}")
    return [int(part) for part in index.split(".")]


def _format_index(parts: list[int]) -> str:
    """Format a list of integer positions back to dot-notation."""
    return ".".join(str(p) for p in parts)


# ---------------------------------------------------------------------------
# FavoritesStore
# ---------------------------------------------------------------------------


class FavoritesStore:
    """JSON-backed hierarchical favorites store.

    The store is designed for moderate-size collections (hundreds to low
    thousands of entries).  All data is kept in memory with a single
    JSON file as the persistence backend.  Writes are atomic (write to
    temp file, then rename) to prevent corruption.

    Usage::

        store = FavoritesStore(Path("data/plugins/favorites"))
        store.load()
        idx = store.add("file:///music/song.flac", "My Song")
        store.save()

    Thread / task safety: This store is **not** thread-safe.  In Resonance
    all JSON-RPC handlers run on the main asyncio thread, so no locking
    is required.
    """

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._file_path = data_dir / "favorites.json"
        self._items: list[FavoriteItem] = []
        self._url_index: dict[str, str] = {}  # url → dot-notation index
        self._version: int = 0  # bumped on every mutation

    # -- Properties ----------------------------------------------------------

    @property
    def file_path(self) -> Path:
        """Path to the backing JSON file."""
        return self._file_path

    @property
    def version(self) -> int:
        """Monotonically increasing mutation counter (useful for caching)."""
        return self._version

    @property
    def count(self) -> int:
        """Number of top-level entries."""
        return len(self._items)

    # -- Load / Save ---------------------------------------------------------

    def load(self) -> None:
        """Load favorites from the JSON file.

        If the file does not exist or is unreadable, the store starts empty.
        """
        if not self._file_path.is_file():
            logger.info("No favorites file at %s — starting empty", self._file_path)
            self._items = []
            self._rebuild_url_index()
            return

        try:
            raw = self._file_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("Root element must be a JSON object")

            self._items = [
                FavoriteItem.from_dict(entry) for entry in data.get("items", [])
            ]
            self._version = data.get("version", 0)
            self._rebuild_url_index()
            logger.info(
                "Loaded %d favorite(s) from %s", len(self._items), self._file_path
            )
        except Exception as exc:
            logger.error("Failed to load favorites from %s: %s", self._file_path, exc)
            self._items = []
            self._rebuild_url_index()

    def save(self) -> None:
        """Persist the current state to disk (atomic write).

        Creates the data directory if it doesn't exist.
        """
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._version += 1

        payload: dict[str, Any] = {
            "version": self._version,
            "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "items": [item.to_dict() for item in self._items],
        }

        tmp_path = self._file_path.with_suffix(".tmp")
        try:
            tmp_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp_path.replace(self._file_path)
            logger.debug("Saved %d favorite(s) to %s", len(self._items), self._file_path)
        except Exception as exc:
            logger.error("Failed to save favorites to %s: %s", self._file_path, exc)
            # Clean up temp file on failure
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    # -- URL index -----------------------------------------------------------

    def _rebuild_url_index(self) -> None:
        """Rebuild the URL → index lookup table by walking the full tree."""
        self._url_index.clear()
        self._walk_url_index(self._items, [])

    def _walk_url_index(
        self, items: list[FavoriteItem], prefix: list[int]
    ) -> None:
        for i, item in enumerate(items):
            current = prefix + [i]
            if item.url:
                self._url_index[item.url] = _format_index(current)
            if item.items:
                self._walk_url_index(item.items, current)

    # -- Navigation ----------------------------------------------------------

    def _resolve_level(
        self,
        index: str | None,
        *,
        parent: bool = False,
    ) -> tuple[list[FavoriteItem], int | None]:
        """Resolve a dot-notation index to the containing list and position.

        Args:
            index: Dot-notation index string, or ``None`` for top level.
            parent: If ``True``, return the list that *contains* the indexed
                    item (and the item's position within that list).

        Returns:
            ``(level, position)`` where *level* is the ``list[FavoriteItem]``
            and *position* is the integer offset within that list (or ``None``
            when *index* is ``None`` and ``parent=False``).

        Raises:
            IndexError: If the index is out of range.
            ValueError: If the index is malformed.
        """
        if index is None:
            return self._items, None

        parts = _parse_index(index)

        if parent:
            # Walk to the parent level
            level = self._items
            for depth, pos in enumerate(parts[:-1]):
                if pos < 0 or pos >= len(level):
                    raise IndexError(
                        f"Index {index!r} out of range at depth {depth} "
                        f"(level has {len(level)} items)"
                    )
                entry = level[pos]
                if not entry.is_folder:
                    raise IndexError(
                        f"Index {index!r} at depth {depth} is not a folder"
                    )
                level = entry.items
            final_pos = parts[-1]
            return level, final_pos

        # Walk to the exact level
        level = self._items
        for depth, pos in enumerate(parts[:-1]):
            if pos < 0 or pos >= len(level):
                raise IndexError(
                    f"Index {index!r} out of range at depth {depth}"
                )
            entry = level[pos]
            level = entry.items

        final_pos = parts[-1]
        if final_pos < 0 or final_pos >= len(level):
            raise IndexError(
                f"Index {index!r}: position {final_pos} out of range "
                f"(level has {len(level)} items)"
            )
        return level, final_pos

    def get_entry(self, index: str) -> FavoriteItem | None:
        """Return the entry at *index*, or ``None`` if invalid."""
        try:
            level, pos = self._resolve_level(index)
            if pos is not None and 0 <= pos < len(level):
                return level[pos]
        except (IndexError, ValueError):
            pass
        return None

    def get_items_at(
        self,
        index: str | None = None,
    ) -> list[FavoriteItem]:
        """Return the child items at *index* (top-level if ``None``).

        For a folder index, returns that folder's children.
        For ``None``, returns all top-level items.
        """
        if index is None:
            return self._items

        entry = self.get_entry(index)
        if entry is not None and entry.is_folder:
            return entry.items

        return []

    def get_items_paginated(
        self,
        start: int = 0,
        count: int = 100,
        index: str | None = None,
    ) -> tuple[list[tuple[str, FavoriteItem]], int]:
        """Return a paginated slice of items at a given level.

        Args:
            start: Offset into the item list.
            count: Maximum number of items to return.
            index: Parent folder index, or ``None`` for top-level.

        Returns:
            ``(items, total)`` where *items* is a list of
            ``(dot_index, FavoriteItem)`` tuples and *total* is the
            full count of items at that level.
        """
        items = self.get_items_at(index)
        total = len(items)

        prefix = f"{index}." if index else ""
        result: list[tuple[str, FavoriteItem]] = []
        for i in range(start, min(start + count, total)):
            result.append((f"{prefix}{i}", items[i]))

        return result, total

    # -- Mutations -----------------------------------------------------------

    def add(
        self,
        url: str,
        title: str,
        type: str = "audio",
        icon: str | None = None,
        *,
        index: str | None = None,
    ) -> str:
        """Add an audio/playlist favorite.

        Args:
            url: Stream or file URL.
            title: Display title.
            type: ``"audio"`` or ``"playlist"``.
            icon: Optional icon URL.
            index: Insert position (dot-notation). If ``None``, append to
                   top-level.  If the index points inside a folder, the item
                   is inserted at that position within the folder.

        Returns:
            The dot-notation index of the newly inserted item.
        """
        # De-duplicate: if URL already exists, return existing index
        existing = self.find_url(url)
        if existing is not None:
            logger.debug("URL already in favorites at index %s: %s", existing, url)
            return existing

        item = FavoriteItem(title=title, url=url, type=type, icon=icon)
        return self._insert_item(item, index)

    def add_level(
        self,
        title: str,
        *,
        index: str | None = None,
    ) -> str:
        """Add a folder.

        Args:
            title: Folder display title.
            index: Insert position. If ``None``, append to top-level.

        Returns:
            The dot-notation index of the newly created folder.
        """
        item = FavoriteItem(title=title, url=None, type="folder")
        return self._insert_item(item, index)

    def _insert_item(self, item: FavoriteItem, index: str | None) -> str:
        """Insert *item* at *index* and return the resulting index string."""
        if index is None:
            # Append to top level
            self._items.append(item)
            new_index = str(len(self._items) - 1)
        else:
            try:
                level, pos = self._resolve_level(index, parent=True)
                if pos is not None and 0 <= pos <= len(level):
                    level.insert(pos, item)
                    new_index = index
                else:
                    # Position beyond end → append
                    level.append(item)
                    parts = _parse_index(index)
                    parts[-1] = len(level) - 1
                    new_index = _format_index(parts)
            except (IndexError, ValueError):
                # Invalid index → append to top level
                self._items.append(item)
                new_index = str(len(self._items) - 1)

        self._rebuild_url_index()
        self.save()
        logger.info("Added favorite at %s: %s", new_index, item.title)
        return new_index

    def delete_by_index(self, index: str) -> FavoriteItem | None:
        """Delete the entry at *index*.

        Returns:
            The removed item, or ``None`` if *index* was invalid.
        """
        try:
            level, pos = self._resolve_level(index, parent=True)
            if pos is not None and 0 <= pos < len(level):
                removed = level.pop(pos)
                self._rebuild_url_index()
                self.save()
                logger.info("Deleted favorite at %s: %s", index, removed.title)
                return removed
        except (IndexError, ValueError) as exc:
            logger.warning("Cannot delete index %s: %s", index, exc)
        return None

    def delete_by_url(self, url: str) -> FavoriteItem | None:
        """Delete the first entry matching *url*.

        Returns:
            The removed item, or ``None`` if not found.
        """
        index = self.find_url(url)
        if index is not None:
            return self.delete_by_index(index)
        logger.debug("URL not found in favorites: %s", url)
        return None

    def rename(self, index: str, title: str) -> bool:
        """Rename the entry at *index*.

        Returns:
            ``True`` if the entry was found and renamed.
        """
        entry = self.get_entry(index)
        if entry is None:
            logger.warning("Cannot rename: index %s not found", index)
            return False

        entry.title = title
        self.save()
        logger.info("Renamed favorite at %s to %r", index, title)
        return True

    def move(self, from_index: str, to_index: str) -> bool:
        """Move an entry from one position to another.

        Both indices must be at the same nesting depth in the current
        implementation (matching LMS behavior for the ``favorites move``
        command).

        Returns:
            ``True`` if the move succeeded.
        """
        try:
            from_level, from_pos = self._resolve_level(from_index, parent=True)
            to_level, to_pos = self._resolve_level(to_index, parent=True)
        except (IndexError, ValueError) as exc:
            logger.warning("Cannot move %s → %s: %s", from_index, to_index, exc)
            return False

        if from_pos is None or to_pos is None:
            return False

        if from_level is not to_level:
            # Cross-level moves: remove from source, insert into target
            if from_pos < 0 or from_pos >= len(from_level):
                return False
            entry = from_level.pop(from_pos)
            to_pos_clamped = min(to_pos, len(to_level))
            to_level.insert(to_pos_clamped, entry)
        else:
            # Same-level reorder
            if from_pos < 0 or from_pos >= len(from_level):
                return False
            entry = from_level.pop(from_pos)
            to_pos_clamped = min(to_pos, len(from_level))
            from_level.insert(to_pos_clamped, entry)

        self._rebuild_url_index()
        self.save()
        logger.info("Moved favorite %s → %s", from_index, to_index)
        return True

    # -- Queries -------------------------------------------------------------

    def find_url(self, url: str) -> str | None:
        """Return the dot-notation index for *url*, or ``None`` if not found."""
        return self._url_index.get(url)

    def has_url(self, url: str) -> bool:
        """Return ``True`` if *url* exists in the favorites."""
        return url in self._url_index

    def all_playable(
        self,
        level: list[FavoriteItem] | None = None,
    ) -> list[FavoriteItem]:
        """Return a flat list of all playable (non-folder) entries, recursively."""
        result: list[FavoriteItem] = []
        for item in level or self._items:
            if item.is_playable:
                result.append(item)
            if item.items:
                result.extend(self.all_playable(item.items))
        return result

    def all_items_flat(self) -> list[tuple[str, FavoriteItem]]:
        """Return all items (recursively) with their dot-notation indices."""
        result: list[tuple[str, FavoriteItem]] = []
        self._walk_flat(self._items, [], result)
        return result

    def _walk_flat(
        self,
        items: list[FavoriteItem],
        prefix: list[int],
        out: list[tuple[str, FavoriteItem]],
    ) -> None:
        for i, item in enumerate(items):
            current = prefix + [i]
            out.append((_format_index(current), item))
            if item.items:
                self._walk_flat(item.items, current, out)

    # -- Bulk ----------------------------------------------------------------

    def clear(self) -> None:
        """Remove all favorites."""
        self._items.clear()
        self._rebuild_url_index()
        self.save()
        logger.info("Cleared all favorites")

    # -- repr ----------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"FavoritesStore(items={len(self._items)}, "
            f"urls={len(self._url_index)}, "
            f"version={self._version})"
        )
