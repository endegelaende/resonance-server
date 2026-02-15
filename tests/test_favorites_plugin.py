"""
Tests for the Favorites Plugin.

Covers:
- FavoritesStore (CRUD, hierarchical indices, URL index, persistence, edge cases)
- Command handlers (favorites items/add/addlevel/delete/rename/move/exists/playlist)
- Jive menu integration (jivefavorites add/delete confirmation menus)
- Parameter parsing helpers
- Event notification on mutations
- Search filtering
- Pagination
- Error handling and boundary conditions
"""

from __future__ import annotations

import asyncio
import json
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Store tests
# ---------------------------------------------------------------------------


class TestFavoriteItem:
    """Tests for the FavoriteItem data model."""

    def test_audio_item_basic(self):
        from plugins.favorites.store import FavoriteItem

        item = FavoriteItem(title="My Song", url="file:///music/song.flac")
        assert item.title == "My Song"
        assert item.url == "file:///music/song.flac"
        assert item.type == "audio"
        assert item.icon is None
        assert item.items == []
        assert not item.is_folder
        assert item.is_playable

    def test_folder_item(self):
        from plugins.favorites.store import FavoriteItem

        child = FavoriteItem(title="Child", url="http://example.com/stream")
        folder = FavoriteItem(title="My Folder", type="folder", items=[child])
        assert folder.is_folder
        assert not folder.is_playable
        assert len(folder.items) == 1

    def test_folder_without_explicit_type(self):
        from plugins.favorites.store import FavoriteItem

        child = FavoriteItem(title="Child", url="http://example.com/stream")
        folder = FavoriteItem(title="Folder", items=[child])
        assert folder.is_folder

    def test_playlist_item(self):
        from plugins.favorites.store import FavoriteItem

        item = FavoriteItem(
            title="My Playlist", url="http://example.com/playlist.m3u", type="playlist"
        )
        assert item.type == "playlist"
        assert item.is_playable
        assert not item.is_folder

    def test_to_dict_audio(self):
        from plugins.favorites.store import FavoriteItem

        item = FavoriteItem(
            title="Song",
            url="file:///song.flac",
            type="audio",
            icon="http://example.com/icon.png",
        )
        d = item.to_dict()
        assert d["title"] == "Song"
        assert d["url"] == "file:///song.flac"
        assert d["type"] == "audio"
        assert d["icon"] == "http://example.com/icon.png"

    def test_to_dict_folder_with_children(self):
        from plugins.favorites.store import FavoriteItem

        child = FavoriteItem(title="Child", url="http://stream.url")
        folder = FavoriteItem(title="Folder", type="folder", items=[child])
        d = folder.to_dict()
        assert "items" in d
        assert len(d["items"]) == 1
        assert d["items"][0]["title"] == "Child"

    def test_to_dict_omits_none_fields(self):
        from plugins.favorites.store import FavoriteItem

        item = FavoriteItem(title="Minimal", url="http://x.com")
        d = item.to_dict()
        assert "icon" not in d
        assert "items" not in d

    def test_from_dict_audio(self):
        from plugins.favorites.store import FavoriteItem

        d = {"title": "Song", "url": "file:///song.flac", "type": "audio"}
        item = FavoriteItem.from_dict(d)
        assert item.title == "Song"
        assert item.url == "file:///song.flac"
        assert item.type == "audio"
        assert not item.is_folder

    def test_from_dict_folder(self):
        from plugins.favorites.store import FavoriteItem

        d = {
            "title": "Folder",
            "items": [
                {"title": "Child1", "url": "http://a.com"},
                {"title": "Child2", "url": "http://b.com"},
            ],
        }
        item = FavoriteItem.from_dict(d)
        assert item.is_folder
        assert item.type == "folder"
        assert len(item.items) == 2

    def test_from_dict_infers_folder_type_from_children(self):
        from plugins.favorites.store import FavoriteItem

        d = {"title": "Auto Folder", "items": [{"title": "X", "url": "http://x.com"}]}
        item = FavoriteItem.from_dict(d)
        assert item.type == "folder"

    def test_from_dict_missing_title(self):
        from plugins.favorites.store import FavoriteItem

        d = {"url": "http://x.com"}
        item = FavoriteItem.from_dict(d)
        assert item.title == ""

    def test_roundtrip(self):
        from plugins.favorites.store import FavoriteItem

        original = FavoriteItem(
            title="Test",
            url="http://example.com/stream",
            type="audio",
            icon="http://example.com/icon.png",
        )
        restored = FavoriteItem.from_dict(original.to_dict())
        assert restored.title == original.title
        assert restored.url == original.url
        assert restored.type == original.type
        assert restored.icon == original.icon

    def test_roundtrip_nested(self):
        from plugins.favorites.store import FavoriteItem

        child1 = FavoriteItem(title="C1", url="http://a.com")
        child2 = FavoriteItem(title="C2", url="http://b.com")
        grandchild = FavoriteItem(title="GC", url="http://c.com")
        subfolder = FavoriteItem(title="Sub", type="folder", items=[grandchild])
        folder = FavoriteItem(title="Root", type="folder", items=[child1, subfolder, child2])

        restored = FavoriteItem.from_dict(folder.to_dict())
        assert restored.is_folder
        assert len(restored.items) == 3
        assert restored.items[1].is_folder
        assert restored.items[1].items[0].title == "GC"

    def test_repr_audio(self):
        from plugins.favorites.store import FavoriteItem

        item = FavoriteItem(title="Song", url="http://x.com")
        r = repr(item)
        assert "Song" in r
        assert "http://x.com" in r

    def test_repr_folder(self):
        from plugins.favorites.store import FavoriteItem

        folder = FavoriteItem(
            title="Folder",
            type="folder",
            items=[FavoriteItem(title="X", url="http://x.com")],
        )
        r = repr(folder)
        assert "folder" in r
        assert "Folder" in r


class TestIndexHelpers:
    """Tests for _parse_index and _format_index."""

    def test_parse_single(self):
        from plugins.favorites.store import _parse_index

        assert _parse_index("0") == [0]
        assert _parse_index("5") == [5]

    def test_parse_dotted(self):
        from plugins.favorites.store import _parse_index

        assert _parse_index("1.2.0") == [1, 2, 0]

    def test_parse_invalid_raises(self):
        from plugins.favorites.store import _parse_index

        with pytest.raises(ValueError):
            _parse_index("")

    def test_parse_non_numeric_raises(self):
        from plugins.favorites.store import _parse_index

        with pytest.raises(ValueError):
            _parse_index("abc")

    def test_format_single(self):
        from plugins.favorites.store import _format_index

        assert _format_index([0]) == "0"

    def test_format_dotted(self):
        from plugins.favorites.store import _format_index

        assert _format_index([1, 2, 0]) == "1.2.0"

    def test_roundtrip(self):
        from plugins.favorites.store import _format_index, _parse_index

        assert _format_index(_parse_index("3.1.4")) == "3.1.4"


class TestFavoritesStore:
    """Tests for FavoritesStore CRUD and persistence."""

    @pytest.fixture()
    def store(self, tmp_path: Path):
        from plugins.favorites.store import FavoritesStore

        return FavoritesStore(tmp_path)

    def test_empty_store(self, store):
        assert store.count == 0
        assert store.version == 0

    def test_load_nonexistent_file(self, store):
        store.load()
        assert store.count == 0

    def test_add_simple(self, store):
        store.load()
        idx = store.add("http://example.com/stream", "My Stream")
        assert idx == "0"
        assert store.count == 1

    def test_add_multiple(self, store):
        store.load()
        idx0 = store.add("http://a.com", "A")
        idx1 = store.add("http://b.com", "B")
        idx2 = store.add("http://c.com", "C")
        assert idx0 == "0"
        assert idx1 == "1"
        assert idx2 == "2"
        assert store.count == 3

    def test_add_with_type_and_icon(self, store):
        store.load()
        store.add(
            "http://radio.com/stream",
            "Cool Radio",
            type="playlist",
            icon="http://radio.com/icon.png",
        )
        entry = store.get_entry("0")
        assert entry is not None
        assert entry.type == "playlist"
        assert entry.icon == "http://radio.com/icon.png"

    def test_add_deduplicates_by_url(self, store):
        store.load()
        idx1 = store.add("http://same.url", "First")
        idx2 = store.add("http://same.url", "Second")
        assert idx1 == idx2
        assert store.count == 1
        # Title stays as the original
        assert store.get_entry("0").title == "First"

    def test_add_at_index(self, store):
        store.load()
        store.add("http://a.com", "A")
        store.add("http://b.com", "B")
        store.add("http://inserted.com", "Inserted", index="1")
        # Now order: A, Inserted, B
        assert store.get_entry("0").title == "A"
        assert store.get_entry("1").title == "Inserted"
        assert store.get_entry("2").title == "B"

    def test_add_level(self, store):
        store.load()
        idx = store.add_level("My Folder")
        assert idx == "0"
        entry = store.get_entry("0")
        assert entry is not None
        assert entry.is_folder
        assert entry.title == "My Folder"

    def test_add_inside_folder(self, store):
        store.load()
        store.add_level("Folder")
        # Add at position 0.0 (inside the folder)
        idx = store.add("http://inside.com", "Inside", index="0.0")
        assert idx == "0.0"
        folder = store.get_entry("0")
        assert len(folder.items) == 1
        assert folder.items[0].title == "Inside"

    def test_delete_by_index(self, store):
        store.load()
        store.add("http://a.com", "A")
        store.add("http://b.com", "B")
        store.add("http://c.com", "C")

        removed = store.delete_by_index("1")
        assert removed is not None
        assert removed.title == "B"
        assert store.count == 2
        assert store.get_entry("0").title == "A"
        assert store.get_entry("1").title == "C"

    def test_delete_by_url(self, store):
        store.load()
        store.add("http://a.com", "A")
        store.add("http://b.com", "B")

        removed = store.delete_by_url("http://a.com")
        assert removed is not None
        assert removed.title == "A"
        assert store.count == 1
        assert not store.has_url("http://a.com")

    def test_delete_nonexistent_index(self, store):
        store.load()
        store.add("http://a.com", "A")
        removed = store.delete_by_index("99")
        assert removed is None
        assert store.count == 1

    def test_delete_nonexistent_url(self, store):
        store.load()
        removed = store.delete_by_url("http://nonexistent.com")
        assert removed is None

    def test_delete_from_folder(self, store):
        store.load()
        store.add_level("Folder")
        store.add("http://inside.com", "Inside", index="0.0")
        assert store.get_entry("0").items[0].title == "Inside"

        removed = store.delete_by_index("0.0")
        assert removed is not None
        assert removed.title == "Inside"
        assert len(store.get_entry("0").items) == 0

    def test_rename(self, store):
        store.load()
        store.add("http://a.com", "Old Name")
        assert store.rename("0", "New Name")
        assert store.get_entry("0").title == "New Name"

    def test_rename_nonexistent(self, store):
        store.load()
        assert not store.rename("99", "Name")

    def test_move_same_level(self, store):
        store.load()
        store.add("http://a.com", "A")
        store.add("http://b.com", "B")
        store.add("http://c.com", "C")

        assert store.move("2", "0")
        assert store.get_entry("0").title == "C"
        assert store.get_entry("1").title == "A"
        assert store.get_entry("2").title == "B"

    def test_move_invalid_index(self, store):
        store.load()
        store.add("http://a.com", "A")
        assert not store.move("99", "0")

    def test_find_url(self, store):
        store.load()
        store.add("http://a.com", "A")
        store.add("http://b.com", "B")

        assert store.find_url("http://a.com") == "0"
        assert store.find_url("http://b.com") == "1"
        assert store.find_url("http://c.com") is None

    def test_has_url(self, store):
        store.load()
        store.add("http://a.com", "A")

        assert store.has_url("http://a.com")
        assert not store.has_url("http://b.com")

    def test_find_url_in_subfolder(self, store):
        store.load()
        store.add_level("Folder")
        store.add("http://inside.com", "Inside", index="0.0")

        assert store.find_url("http://inside.com") == "0.0"

    def test_url_index_rebuilt_after_delete(self, store):
        store.load()
        store.add("http://a.com", "A")
        store.add("http://b.com", "B")

        store.delete_by_index("0")
        # After deleting index 0, "B" moves to index 0
        assert store.find_url("http://b.com") == "0"
        assert store.find_url("http://a.com") is None

    def test_get_entry_valid(self, store):
        store.load()
        store.add("http://a.com", "A")
        entry = store.get_entry("0")
        assert entry is not None
        assert entry.title == "A"

    def test_get_entry_invalid(self, store):
        store.load()
        assert store.get_entry("99") is None
        assert store.get_entry("abc") is None

    def test_get_items_at_top_level(self, store):
        store.load()
        store.add("http://a.com", "A")
        store.add("http://b.com", "B")

        items = store.get_items_at(None)
        assert len(items) == 2

    def test_get_items_at_folder(self, store):
        store.load()
        store.add_level("Folder")
        store.add("http://c1.com", "C1", index="0.0")
        store.add("http://c2.com", "C2", index="0.1")

        items = store.get_items_at("0")
        assert len(items) == 2

    def test_get_items_paginated(self, store):
        store.load()
        for i in range(10):
            store.add(f"http://{i}.com", f"Item {i}")

        items, total = store.get_items_paginated(start=2, count=3)
        assert total == 10
        assert len(items) == 3
        assert items[0][0] == "2"
        assert items[0][1].title == "Item 2"
        assert items[2][0] == "4"

    def test_get_items_paginated_beyond_end(self, store):
        store.load()
        store.add("http://a.com", "A")
        store.add("http://b.com", "B")

        items, total = store.get_items_paginated(start=5, count=10)
        assert total == 2
        assert len(items) == 0

    def test_get_items_paginated_in_folder(self, store):
        store.load()
        store.add_level("Folder")
        store.add("http://c1.com", "C1", index="0.0")
        store.add("http://c2.com", "C2", index="0.1")

        items, total = store.get_items_paginated(start=0, count=10, index="0")
        assert total == 2
        assert items[0][0] == "0.0"
        assert items[1][0] == "0.1"

    def test_all_playable(self, store):
        store.load()
        store.add("http://a.com", "A")
        store.add_level("Folder")
        store.add("http://b.com", "B", index="1.0")
        store.add("http://c.com", "C")

        playable = store.all_playable()
        assert len(playable) == 3
        urls = {p.url for p in playable}
        assert urls == {"http://a.com", "http://b.com", "http://c.com"}

    def test_all_items_flat(self, store):
        store.load()
        store.add("http://a.com", "A")
        store.add_level("Folder")
        store.add("http://b.com", "B", index="1.0")

        flat = store.all_items_flat()
        assert len(flat) == 3
        indices = [idx for idx, _ in flat]
        assert "0" in indices
        assert "1" in indices
        assert "1.0" in indices

    def test_clear(self, store):
        store.load()
        store.add("http://a.com", "A")
        store.add("http://b.com", "B")
        store.clear()
        assert store.count == 0
        assert not store.has_url("http://a.com")

    def test_version_increments(self, store):
        store.load()
        v0 = store.version
        store.add("http://a.com", "A")
        v1 = store.version
        assert v1 > v0
        store.add("http://b.com", "B")
        v2 = store.version
        assert v2 > v1

    # -- Persistence ---------------------------------------------------------

    def test_save_and_load(self, tmp_path):
        from plugins.favorites.store import FavoritesStore

        store1 = FavoritesStore(tmp_path)
        store1.load()
        store1.add("http://a.com", "Alpha")
        store1.add("http://b.com", "Beta", type="playlist", icon="http://icon.png")
        store1.add_level("Folder")
        store1.add("http://c.com", "Charlie", index="2.0")

        # Load into a fresh store
        store2 = FavoritesStore(tmp_path)
        store2.load()
        assert store2.count == 3
        assert store2.get_entry("0").title == "Alpha"
        assert store2.get_entry("1").type == "playlist"
        assert store2.get_entry("1").icon == "http://icon.png"
        assert store2.get_entry("2").is_folder
        assert len(store2.get_entry("2").items) == 1
        assert store2.get_entry("2.0").title == "Charlie"
        assert store2.has_url("http://c.com")

    def test_save_creates_directory(self, tmp_path):
        from plugins.favorites.store import FavoritesStore

        nested = tmp_path / "deep" / "nested"
        store = FavoritesStore(nested)
        store.load()
        store.add("http://a.com", "A")
        assert (nested / "favorites.json").is_file()

    def test_save_is_valid_json(self, tmp_path):
        from plugins.favorites.store import FavoritesStore

        store = FavoritesStore(tmp_path)
        store.load()
        store.add("http://a.com", "Alpha")

        content = (tmp_path / "favorites.json").read_text(encoding="utf-8")
        data = json.loads(content)
        assert "version" in data
        assert "updated" in data
        assert "items" in data
        assert len(data["items"]) == 1

    def test_load_corrupt_json(self, tmp_path):
        from plugins.favorites.store import FavoritesStore

        (tmp_path / "favorites.json").write_text("not valid json!!!", encoding="utf-8")
        store = FavoritesStore(tmp_path)
        store.load()
        assert store.count == 0  # graceful degradation

    def test_load_wrong_structure(self, tmp_path):
        from plugins.favorites.store import FavoritesStore

        (tmp_path / "favorites.json").write_text('"just a string"', encoding="utf-8")
        store = FavoritesStore(tmp_path)
        store.load()
        assert store.count == 0

    def test_atomic_save_no_partial_writes(self, tmp_path):
        from plugins.favorites.store import FavoritesStore

        store = FavoritesStore(tmp_path)
        store.load()
        store.add("http://a.com", "A")

        # Verify no .tmp file remains
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0

    def test_file_path_property(self, tmp_path):
        from plugins.favorites.store import FavoritesStore

        store = FavoritesStore(tmp_path)
        assert store.file_path == tmp_path / "favorites.json"

    def test_repr(self, store):
        store.load()
        store.add("http://a.com", "A")
        r = repr(store)
        assert "FavoritesStore" in r
        assert "items=1" in r

    # -- Deep hierarchy tests ------------------------------------------------

    def test_nested_folder_three_levels(self, store):
        store.load()
        store.add_level("L1")
        store.add_level("L2", index="0.0")  # inside L1
        store.add("http://deep.com", "Deep", index="0.0.0")  # inside L2

        assert store.get_entry("0").is_folder
        assert store.get_entry("0.0").is_folder
        entry = store.get_entry("0.0.0")
        assert entry is not None
        assert entry.title == "Deep"
        assert store.find_url("http://deep.com") == "0.0.0"

    def test_delete_in_nested_folder(self, store):
        store.load()
        store.add_level("L1")
        store.add_level("L2", index="0.0")
        store.add("http://deep.com", "Deep", index="0.0.0")

        removed = store.delete_by_index("0.0.0")
        assert removed is not None
        assert removed.title == "Deep"
        assert len(store.get_entry("0.0").items) == 0
        assert not store.has_url("http://deep.com")


# ---------------------------------------------------------------------------
# Command handler tests
# ---------------------------------------------------------------------------


class _FakeCommandContext:
    """Minimal stand-in for CommandContext used in handler tests."""

    def __init__(
        self,
        player_id: str = "-",
        music_library: Any = None,
        player_registry: Any = None,
        playlist_manager: Any = None,
    ):
        self.player_id = player_id
        self.music_library = music_library
        self.playlist_manager = playlist_manager
        self.streaming_server = None
        self.slimproto = None
        self.artwork_manager = None
        self.server_host = "127.0.0.1"
        self.server_port = 9000
        self.server_uuid = "test-uuid"

        if player_registry is not None:
            # Use the explicitly provided registry as-is
            self.player_registry = player_registry
        else:
            # Create a default mock that returns None for get_by_mac
            self.player_registry = AsyncMock()
            self.player_registry.get_by_mac = AsyncMock(return_value=None)


@pytest.fixture()
def favorites_env(tmp_path):
    """Set up the favorites plugin module-level state for testing."""
    import plugins.favorites as fav_mod
    from plugins.favorites.store import FavoritesStore

    store = FavoritesStore(tmp_path)
    store.load()
    fav_mod._store = store
    fav_mod._event_bus = MagicMock()
    fav_mod._event_bus.publish = AsyncMock()

    yield store, fav_mod

    # Cleanup
    fav_mod._store = None
    fav_mod._event_bus = None


class TestParseTagged:
    """Tests for _parse_tagged helper."""

    def test_basic(self):
        from plugins.favorites import _parse_tagged

        result = _parse_tagged(["favorites", "add", "url:http://a.com", "title:Song"], start=2)
        assert result["url"] == "http://a.com"
        assert result["title"] == "Song"

    def test_dict_params(self):
        from plugins.favorites import _parse_tagged

        result = _parse_tagged(
            ["favorites", "add", {"url": "http://a.com", "title": "Song"}], start=2
        )
        assert result["url"] == "http://a.com"
        assert result["title"] == "Song"

    def test_mixed_params(self):
        from plugins.favorites import _parse_tagged

        result = _parse_tagged(
            ["favorites", "add", "url:http://a.com", {"title": "Song"}], start=2
        )
        assert result["url"] == "http://a.com"
        assert result["title"] == "Song"

    def test_colon_in_value(self):
        from plugins.favorites import _parse_tagged

        result = _parse_tagged(["cmd", "sub", "url:http://host:8080/stream"], start=2)
        assert result["url"] == "http://host:8080/stream"

    def test_ignores_non_tagged(self):
        from plugins.favorites import _parse_tagged

        result = _parse_tagged(["cmd", "sub", "plain_text", "key:value"], start=2)
        assert "plain_text" not in result
        assert result["key"] == "value"

    def test_dict_with_none_value(self):
        from plugins.favorites import _parse_tagged

        result = _parse_tagged(["cmd", "sub", {"key": None, "other": "val"}], start=2)
        assert "key" not in result
        assert result["other"] == "val"


class TestParseStartCount:
    """Tests for _parse_start_count helper."""

    def test_default(self):
        from plugins.favorites import _parse_start_count

        start, count = _parse_start_count(["favorites", "items"])
        assert start == 0
        assert count == 200

    def test_explicit(self):
        from plugins.favorites import _parse_start_count

        start, count = _parse_start_count(["favorites", "items", 5, 50])
        assert start == 5
        assert count == 50

    def test_negative_start(self):
        from plugins.favorites import _parse_start_count

        start, count = _parse_start_count(["favorites", "items", -1, 50])
        assert start == 0

    def test_huge_count_clamped(self):
        from plugins.favorites import _parse_start_count

        _, count = _parse_start_count(["favorites", "items", 0, 999999])
        assert count == 10_000

    def test_non_numeric(self):
        from plugins.favorites import _parse_start_count

        start, count = _parse_start_count(["favorites", "items", "abc", "def"])
        assert start == 0
        assert count == 200


class TestCmdFavoritesItems:
    """Tests for the favorites items command."""

    @pytest.mark.asyncio
    async def test_items_empty(self, favorites_env):
        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()
        result = await cmd_favorites(ctx, ["favorites", "items", 0, 100])
        assert result["count"] == 0
        assert result["loop"] == []

    @pytest.mark.asyncio
    async def test_items_with_data(self, favorites_env):
        store, _ = favorites_env
        store.add("http://a.com", "Alpha")
        store.add("http://b.com", "Beta")

        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()
        result = await cmd_favorites(ctx, ["favorites", "items", 0, 100])
        assert result["count"] == 2
        assert len(result["loop"]) == 2
        assert result["loop"][0]["name"] == "Alpha"
        assert result["loop"][0]["url"] == "http://a.com"
        assert result["loop"][0]["id"] == "0"

    @pytest.mark.asyncio
    async def test_items_pagination(self, favorites_env):
        store, _ = favorites_env
        for i in range(10):
            store.add(f"http://{i}.com", f"Item {i}")

        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()
        result = await cmd_favorites(ctx, ["favorites", "items", 3, 2])
        assert result["count"] == 10
        assert result["offset"] == 3
        assert len(result["loop"]) == 2
        assert result["loop"][0]["name"] == "Item 3"

    @pytest.mark.asyncio
    async def test_items_menu_mode(self, favorites_env):
        store, _ = favorites_env
        store.add("http://a.com", "Alpha")

        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()
        result = await cmd_favorites(
            ctx, ["favorites", "items", 0, 100, "menu:1"]
        )
        assert "item_loop" in result
        assert "base" in result
        item = result["item_loop"][0]
        assert item["text"] == "Alpha"
        assert item["type"] == "audio"
        assert "play" in item["actions"]
        assert "add" in item["actions"]
        assert "presetParams" in item

    @pytest.mark.asyncio
    async def test_items_menu_mode_folder(self, favorites_env):
        store, _ = favorites_env
        store.add_level("My Folder")

        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()
        result = await cmd_favorites(
            ctx, ["favorites", "items", 0, 100, "menu:1"]
        )
        item = result["item_loop"][0]
        assert item["type"] == "folder"
        assert item["hasitems"] == 1
        assert "go" in item["actions"]

    @pytest.mark.asyncio
    async def test_items_in_folder(self, favorites_env):
        store, _ = favorites_env
        store.add_level("Folder")
        store.add("http://inside.com", "Inside", index="0.0")

        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()
        result = await cmd_favorites(
            ctx, ["favorites", "items", 0, 100, "item_id:0"]
        )
        assert result["count"] == 1
        assert result["loop"][0]["name"] == "Inside"
        assert result["loop"][0]["id"] == "0.0"

    @pytest.mark.asyncio
    async def test_items_search_filter(self, favorites_env):
        store, _ = favorites_env
        store.add("http://a.com", "Alpha Song")
        store.add("http://b.com", "Beta Track")
        store.add("http://c.com", "Alpha Mix")

        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()
        result = await cmd_favorites(
            ctx, ["favorites", "items", 0, 100, "search:alpha"]
        )
        assert result["count"] == 2
        names = [item["name"] for item in result["loop"]]
        assert "Alpha Song" in names
        assert "Alpha Mix" in names

    @pytest.mark.asyncio
    async def test_items_cli_folder_fields(self, favorites_env):
        store, _ = favorites_env
        store.add_level("Folder")

        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()
        result = await cmd_favorites(ctx, ["favorites", "items", 0, 100])
        item = result["loop"][0]
        assert item["type"] == "folder"
        assert item["hasitems"] == 1
        assert item["isaudio"] == 0

    @pytest.mark.asyncio
    async def test_items_default_subcommand(self, favorites_env):
        """favorites without sub-command defaults to items."""
        store, _ = favorites_env
        store.add("http://a.com", "A")

        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()
        result = await cmd_favorites(ctx, ["favorites"])
        assert result["count"] == 1


class TestCmdFavoritesAdd:
    """Tests for the favorites add command."""

    @pytest.mark.asyncio
    async def test_add_basic(self, favorites_env):
        store, _ = favorites_env

        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()
        result = await cmd_favorites(
            ctx, ["favorites", "add", "url:http://new.com", "title:New Song"]
        )
        assert result.get("count") == 1
        assert "item_id" in result
        assert store.count == 1
        assert store.has_url("http://new.com")

    @pytest.mark.asyncio
    async def test_add_with_type_and_icon(self, favorites_env):
        store, _ = favorites_env

        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()
        await cmd_favorites(
            ctx,
            [
                "favorites",
                "add",
                "url:http://radio.com",
                "title:Radio",
                "type:playlist",
                "icon:http://icon.png",
            ],
        )
        entry = store.get_entry("0")
        assert entry.type == "playlist"
        assert entry.icon == "http://icon.png"

    @pytest.mark.asyncio
    async def test_add_missing_url(self, favorites_env):
        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()
        result = await cmd_favorites(ctx, ["favorites", "add", "title:Song"])
        assert "error" in result

    @pytest.mark.asyncio
    async def test_add_missing_title(self, favorites_env):
        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()
        result = await cmd_favorites(ctx, ["favorites", "add", "url:http://a.com"])
        assert "error" in result

    @pytest.mark.asyncio
    async def test_add_with_item_id(self, favorites_env):
        store, _ = favorites_env
        store.add("http://a.com", "A")
        store.add("http://b.com", "B")

        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()
        await cmd_favorites(
            ctx,
            ["favorites", "add", "url:http://inserted.com", "title:Ins", "item_id:1"],
        )
        assert store.get_entry("1").title == "Ins"

    @pytest.mark.asyncio
    async def test_add_notifies_changed(self, favorites_env):
        _, fav_mod = favorites_env

        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()
        await cmd_favorites(
            ctx, ["favorites", "add", "url:http://a.com", "title:A"]
        )
        fav_mod._event_bus.publish.assert_called()

    @pytest.mark.asyncio
    async def test_add_dict_params(self, favorites_env):
        """Cometd sends params as a dict."""
        store, _ = favorites_env

        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()
        result = await cmd_favorites(
            ctx,
            ["favorites", "add", {"url": "http://dict.com", "title": "Dict Song"}],
        )
        assert result.get("count") == 1
        assert store.has_url("http://dict.com")


class TestCmdFavoritesAddLevel:
    """Tests for the favorites addlevel command."""

    @pytest.mark.asyncio
    async def test_addlevel_basic(self, favorites_env):
        store, _ = favorites_env

        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()
        result = await cmd_favorites(
            ctx, ["favorites", "addlevel", "title:My Folder"]
        )
        assert result["count"] == 1
        assert store.count == 1
        assert store.get_entry("0").is_folder

    @pytest.mark.asyncio
    async def test_addlevel_missing_title(self, favorites_env):
        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()
        result = await cmd_favorites(ctx, ["favorites", "addlevel"])
        assert "error" in result

    @pytest.mark.asyncio
    async def test_addlevel_notifies_changed(self, favorites_env):
        _, fav_mod = favorites_env

        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()
        await cmd_favorites(ctx, ["favorites", "addlevel", "title:F"])
        fav_mod._event_bus.publish.assert_called()


class TestCmdFavoritesDelete:
    """Tests for the favorites delete command."""

    @pytest.mark.asyncio
    async def test_delete_by_item_id(self, favorites_env):
        store, _ = favorites_env
        store.add("http://a.com", "A")
        store.add("http://b.com", "B")

        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()
        result = await cmd_favorites(ctx, ["favorites", "delete", "item_id:0"])
        assert "error" not in result
        assert store.count == 1
        assert not store.has_url("http://a.com")

    @pytest.mark.asyncio
    async def test_delete_by_url(self, favorites_env):
        store, _ = favorites_env
        store.add("http://a.com", "A")

        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()
        result = await cmd_favorites(
            ctx, ["favorites", "delete", "url:http://a.com"]
        )
        assert "error" not in result
        assert store.count == 0

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, favorites_env):
        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()
        result = await cmd_favorites(ctx, ["favorites", "delete", "item_id:99"])
        assert "error" in result

    @pytest.mark.asyncio
    async def test_delete_no_params(self, favorites_env):
        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()
        result = await cmd_favorites(ctx, ["favorites", "delete"])
        assert "error" in result

    @pytest.mark.asyncio
    async def test_delete_fallback_to_url_when_index_missing(self, favorites_env):
        """When item_id is given but entry not found, fall back to url param."""
        store, _ = favorites_env
        store.add("http://a.com", "A")

        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()
        result = await cmd_favorites(
            ctx, ["favorites", "delete", "item_id:99", "url:http://a.com"]
        )
        assert "error" not in result
        assert store.count == 0

    @pytest.mark.asyncio
    async def test_delete_notifies_changed(self, favorites_env):
        store, fav_mod = favorites_env
        store.add("http://a.com", "A")

        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()
        await cmd_favorites(ctx, ["favorites", "delete", "item_id:0"])
        fav_mod._event_bus.publish.assert_called()


class TestCmdFavoritesRename:
    """Tests for the favorites rename command."""

    @pytest.mark.asyncio
    async def test_rename(self, favorites_env):
        store, _ = favorites_env
        store.add("http://a.com", "Old")

        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()
        result = await cmd_favorites(
            ctx, ["favorites", "rename", "item_id:0", "title:New"]
        )
        assert "error" not in result
        assert store.get_entry("0").title == "New"

    @pytest.mark.asyncio
    async def test_rename_missing_params(self, favorites_env):
        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()
        result = await cmd_favorites(ctx, ["favorites", "rename"])
        assert "error" in result

    @pytest.mark.asyncio
    async def test_rename_nonexistent(self, favorites_env):
        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()
        result = await cmd_favorites(
            ctx, ["favorites", "rename", "item_id:99", "title:X"]
        )
        assert "error" in result


class TestCmdFavoritesMove:
    """Tests for the favorites move command."""

    @pytest.mark.asyncio
    async def test_move(self, favorites_env):
        store, _ = favorites_env
        store.add("http://a.com", "A")
        store.add("http://b.com", "B")
        store.add("http://c.com", "C")

        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()
        result = await cmd_favorites(
            ctx, ["favorites", "move", "from_id:2", "to_id:0"]
        )
        assert "error" not in result
        assert store.get_entry("0").title == "C"

    @pytest.mark.asyncio
    async def test_move_missing_params(self, favorites_env):
        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()
        result = await cmd_favorites(ctx, ["favorites", "move"])
        assert "error" in result

    @pytest.mark.asyncio
    async def test_move_invalid(self, favorites_env):
        store, _ = favorites_env
        store.add("http://a.com", "A")

        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()
        result = await cmd_favorites(
            ctx, ["favorites", "move", "from_id:99", "to_id:0"]
        )
        assert "error" in result


class TestCmdFavoritesExists:
    """Tests for the favorites exists command."""

    @pytest.mark.asyncio
    async def test_exists_by_url(self, favorites_env):
        store, _ = favorites_env
        store.add("http://a.com", "A")

        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()
        result = await cmd_favorites(
            ctx, ["favorites", "exists", "http://a.com"]
        )
        assert result["exists"] == 1
        assert result["index"] == "0"

    @pytest.mark.asyncio
    async def test_not_exists(self, favorites_env):
        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()
        result = await cmd_favorites(
            ctx, ["favorites", "exists", "http://nonexistent.com"]
        )
        assert result["exists"] == 0

    @pytest.mark.asyncio
    async def test_exists_no_id(self, favorites_env):
        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()
        result = await cmd_favorites(ctx, ["favorites", "exists"])
        assert result["exists"] == 0

    @pytest.mark.asyncio
    async def test_exists_by_track_id(self, favorites_env):
        """When a numeric ID is given, resolve via music library."""
        store, _ = favorites_env
        store.add("file:///music/track.flac", "Track")

        mock_db = AsyncMock()
        mock_db.get_track = AsyncMock(
            return_value={"url": "file:///music/track.flac", "title": "Track"}
        )
        mock_library = MagicMock()
        mock_library._db = mock_db

        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext(music_library=mock_library)
        result = await cmd_favorites(ctx, ["favorites", "exists", "42"])
        assert result["exists"] == 1

    @pytest.mark.asyncio
    async def test_exists_by_track_id_not_found(self, favorites_env):
        mock_db = AsyncMock()
        mock_db.get_track = AsyncMock(return_value=None)
        mock_library = MagicMock()
        mock_library._db = mock_db

        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext(music_library=mock_library)
        result = await cmd_favorites(ctx, ["favorites", "exists", "42"])
        assert result["exists"] == 0

    @pytest.mark.asyncio
    async def test_exists_tagged_url_param(self, favorites_env):
        store, _ = favorites_env
        store.add("http://a.com", "A")

        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()
        result = await cmd_favorites(
            ctx, ["favorites", "exists", "url:http://a.com"]
        )
        assert result["exists"] == 1


class TestCmdFavoritesPlaylist:
    """Tests for the favorites playlist play/add command."""

    @pytest.mark.asyncio
    async def test_playlist_no_player(self, favorites_env):
        store, _ = favorites_env
        store.add("http://a.com", "A")

        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext(player_id="-")
        result = await cmd_favorites(
            ctx, ["favorites", "playlist", "play"]
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_playlist_no_manager(self, favorites_env):
        store, _ = favorites_env
        store.add("http://a.com", "A")

        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext(player_id="aa:bb:cc:dd:ee:ff", playlist_manager=None)
        result = await cmd_favorites(
            ctx, ["favorites", "playlist", "play"]
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_playlist_no_playable(self, favorites_env):
        store, _ = favorites_env
        store.add_level("Empty Folder")

        mock_pm = MagicMock()
        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext(
            player_id="aa:bb:cc:dd:ee:ff", playlist_manager=mock_pm
        )
        result = await cmd_favorites(
            ctx, ["favorites", "playlist", "play"]
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_playlist_play_specific_item(self, favorites_env):
        store, _ = favorites_env
        store.add("http://a.com", "A")
        store.add("http://b.com", "B")

        mock_pm = MagicMock()
        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext(
            player_id="aa:bb:cc:dd:ee:ff", playlist_manager=mock_pm
        )

        with patch("resonance.web.handlers.playlist.cmd_playlist", new_callable=AsyncMock) as mock_pl:
            mock_pl.return_value = {}
            result = await cmd_favorites(
                ctx, ["favorites", "playlist", "play", "item_id:1"]
            )
            # Should play just item B
            assert result.get("count") == 1

    @pytest.mark.asyncio
    async def test_playlist_play_all(self, favorites_env):
        store, _ = favorites_env
        store.add("http://a.com", "A")
        store.add("http://b.com", "B")

        mock_pm = MagicMock()
        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext(
            player_id="aa:bb:cc:dd:ee:ff", playlist_manager=mock_pm
        )

        with patch("resonance.web.handlers.playlist.cmd_playlist", new_callable=AsyncMock) as mock_pl:
            mock_pl.return_value = {}
            result = await cmd_favorites(
                ctx, ["favorites", "playlist", "play"]
            )
            assert result.get("count") == 2

    @pytest.mark.asyncio
    async def test_playlist_add(self, favorites_env):
        store, _ = favorites_env
        store.add("http://a.com", "A")

        mock_pm = MagicMock()
        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext(
            player_id="aa:bb:cc:dd:ee:ff", playlist_manager=mock_pm
        )

        with patch("resonance.web.handlers.playlist.cmd_playlist", new_callable=AsyncMock) as mock_pl:
            mock_pl.return_value = {}
            result = await cmd_favorites(
                ctx, ["favorites", "playlist", "add"]
            )
            assert result.get("count") == 1

    @pytest.mark.asyncio
    async def test_playlist_nonexistent_item(self, favorites_env):
        store, _ = favorites_env

        mock_pm = MagicMock()
        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext(
            player_id="aa:bb:cc:dd:ee:ff", playlist_manager=mock_pm
        )
        result = await cmd_favorites(
            ctx, ["favorites", "playlist", "play", "item_id:99"]
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_playlist_folder_plays_children(self, favorites_env):
        store, _ = favorites_env
        store.add_level("Folder")
        store.add("http://c1.com", "C1", index="0.0")
        store.add("http://c2.com", "C2", index="0.1")

        mock_pm = MagicMock()
        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext(
            player_id="aa:bb:cc:dd:ee:ff", playlist_manager=mock_pm
        )

        with patch("resonance.web.handlers.playlist.cmd_playlist", new_callable=AsyncMock) as mock_pl:
            mock_pl.return_value = {}
            result = await cmd_favorites(
                ctx, ["favorites", "playlist", "play", "item_id:0"]
            )
            assert result.get("count") == 2


class TestCmdFavoritesUnknownSub:
    """Test unknown sub-command."""

    @pytest.mark.asyncio
    async def test_unknown_subcommand(self, favorites_env):
        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()
        result = await cmd_favorites(ctx, ["favorites", "nonexistent"])
        assert "error" in result


class TestCmdFavoritesNotInitialized:
    """Test error when store is not initialized."""

    @pytest.mark.asyncio
    async def test_store_not_initialized(self):
        import plugins.favorites as fav_mod

        fav_mod._store = None

        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()
        result = await cmd_favorites(ctx, ["favorites", "items"])
        assert "error" in result


# ---------------------------------------------------------------------------
# Jivefavorites command tests
# ---------------------------------------------------------------------------


class TestCmdJiveFavorites:
    """Tests for the jivefavorites command."""

    @pytest.mark.asyncio
    async def test_add_confirmation_menu(self, favorites_env):
        from plugins.favorites import cmd_jivefavorites

        ctx = _FakeCommandContext()
        result = await cmd_jivefavorites(
            ctx,
            [
                "jivefavorites",
                "add",
                "title:My Song",
                "url:http://song.com",
            ],
        )
        assert result["count"] == 2
        items = result["item_loop"]
        # First item: Cancel
        assert items[0]["text"] == "Cancel"
        assert items[0]["nextWindow"] == "parent"
        # Second item: Add action
        assert "Add" in items[1]["text"]
        assert items[1]["nextWindow"] == "grandparent"
        go = items[1]["actions"]["go"]
        assert go["cmd"] == ["favorites", "add"]
        assert go["params"]["title"] == "My Song"
        assert go["params"]["url"] == "http://song.com"

    @pytest.mark.asyncio
    async def test_delete_confirmation_menu(self, favorites_env):
        from plugins.favorites import cmd_jivefavorites

        ctx = _FakeCommandContext()
        result = await cmd_jivefavorites(
            ctx,
            [
                "jivefavorites",
                "delete",
                "title:My Song",
                "url:http://song.com",
                "item_id:0",
            ],
        )
        assert result["count"] == 2
        items = result["item_loop"]
        assert items[0]["text"] == "Cancel"
        assert "Delete" in items[1]["text"]
        go = items[1]["actions"]["go"]
        assert go["cmd"] == ["favorites", "delete"]
        assert go["params"]["item_id"] == "0"

    @pytest.mark.asyncio
    async def test_delete_includes_icon(self, favorites_env):
        from plugins.favorites import cmd_jivefavorites

        ctx = _FakeCommandContext()
        result = await cmd_jivefavorites(
            ctx,
            [
                "jivefavorites",
                "add",
                "title:Song",
                "url:http://s.com",
                "icon:http://i.com/icon.png",
            ],
        )
        go = result["item_loop"][1]["actions"]["go"]
        assert go["params"]["icon"] == "http://i.com/icon.png"

    @pytest.mark.asyncio
    async def test_unknown_subcommand(self, favorites_env):
        from plugins.favorites import cmd_jivefavorites

        ctx = _FakeCommandContext()
        result = await cmd_jivefavorites(ctx, ["jivefavorites", "unknown"])
        assert "error" in result

    @pytest.mark.asyncio
    async def test_not_initialized(self):
        import plugins.favorites as fav_mod

        fav_mod._store = None

        from plugins.favorites import cmd_jivefavorites

        ctx = _FakeCommandContext()
        result = await cmd_jivefavorites(ctx, ["jivefavorites", "add"])
        assert "error" in result


class TestJiveSetPreset:
    """Tests for jivefavorites set_preset."""

    @pytest.mark.asyncio
    async def test_set_preset_missing_params(self, favorites_env):
        from plugins.favorites import cmd_jivefavorites

        ctx = _FakeCommandContext()
        result = await cmd_jivefavorites(
            ctx, ["jivefavorites", "set_preset"]
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_set_preset_key_zero_maps_to_ten(self, favorites_env):
        """Preset key 0 should map to slot 10 (matching LMS)."""
        mock_player = MagicMock()
        mock_player.set_preset = MagicMock()
        mock_player.show_briefly = MagicMock()
        mock_registry = AsyncMock()
        mock_registry.get_by_mac = AsyncMock(return_value=mock_player)

        from plugins.favorites import cmd_jivefavorites

        ctx = _FakeCommandContext(
            player_id="aa:bb:cc:dd:ee:ff", player_registry=mock_registry
        )
        await cmd_jivefavorites(
            ctx,
            [
                "jivefavorites",
                "set_preset",
                "key:0",
                "favorites_title:Song",
                "favorites_url:http://s.com",
            ],
        )
        mock_player.set_preset.assert_called_once()
        call_args = mock_player.set_preset.call_args
        # set_preset is called with keyword args: slot=10, url=..., text=..., type=...
        kwargs = call_args.kwargs if call_args.kwargs else call_args[1] if len(call_args) > 1 else {}
        assert kwargs.get("slot") == 10, f"Expected slot=10, got call_args={call_args}"


# ---------------------------------------------------------------------------
# Plugin lifecycle tests
# ---------------------------------------------------------------------------


class TestPluginLifecycle:
    """Tests for setup() and teardown()."""

    @pytest.fixture()
    def mock_ctx(self, tmp_path):
        """Build a mock PluginContext for lifecycle tests."""
        ctx = MagicMock()
        ctx.plugin_id = "favorites"
        ctx.data_dir = tmp_path
        ctx.ensure_data_dir = MagicMock(return_value=tmp_path)
        ctx.event_bus = MagicMock()
        ctx.event_bus.publish = AsyncMock()
        ctx.register_command = MagicMock()
        ctx.register_menu_node = MagicMock()
        return ctx

    @pytest.mark.asyncio
    async def test_setup_registers_commands(self, mock_ctx):
        import plugins.favorites as fav_mod

        await fav_mod.setup(mock_ctx)

        # Should register 'favorites' and 'jivefavorites'
        calls = [c[0][0] for c in mock_ctx.register_command.call_args_list]
        assert "favorites" in calls
        assert "jivefavorites" in calls

        await fav_mod.teardown(mock_ctx)

    @pytest.mark.asyncio
    async def test_setup_registers_menu_node(self, mock_ctx):
        import plugins.favorites as fav_mod

        await fav_mod.setup(mock_ctx)

        mock_ctx.register_menu_node.assert_called_once()
        kwargs = mock_ctx.register_menu_node.call_args[1]
        assert kwargs["node_id"] == "favorites"
        assert kwargs["parent"] == "home"
        assert kwargs["weight"] == 55

        await fav_mod.teardown(mock_ctx)

    @pytest.mark.asyncio
    async def test_setup_loads_store(self, mock_ctx):
        import plugins.favorites as fav_mod

        await fav_mod.setup(mock_ctx)

        assert fav_mod._store is not None
        assert fav_mod._event_bus is not None

        await fav_mod.teardown(mock_ctx)

    @pytest.mark.asyncio
    async def test_teardown_clears_state(self, mock_ctx):
        import plugins.favorites as fav_mod

        await fav_mod.setup(mock_ctx)
        await fav_mod.teardown(mock_ctx)

        assert fav_mod._store is None
        assert fav_mod._event_bus is None

    @pytest.mark.asyncio
    async def test_setup_loads_existing_favorites(self, mock_ctx, tmp_path):
        """If favorites.json already exists, setup loads it."""
        data = {
            "version": 1,
            "updated": "2026-01-01T00:00:00Z",
            "items": [
                {"title": "Existing", "url": "http://existing.com", "type": "audio"}
            ],
        }
        (tmp_path / "favorites.json").write_text(
            json.dumps(data), encoding="utf-8"
        )

        import plugins.favorites as fav_mod

        await fav_mod.setup(mock_ctx)

        assert fav_mod._store.count == 1
        assert fav_mod._store.has_url("http://existing.com")

        await fav_mod.teardown(mock_ctx)


# ---------------------------------------------------------------------------
# Integration-style tests — full workflow
# ---------------------------------------------------------------------------


class TestIntegrationWorkflows:
    """End-to-end workflow tests combining multiple operations."""

    @pytest.mark.asyncio
    async def test_add_then_exists_then_delete(self, favorites_env):
        store, _ = favorites_env

        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()

        # Add
        result = await cmd_favorites(
            ctx, ["favorites", "add", "url:http://test.com", "title:Test"]
        )
        assert result["count"] == 1
        item_id = result["item_id"]

        # Exists
        result = await cmd_favorites(
            ctx, ["favorites", "exists", "http://test.com"]
        )
        assert result["exists"] == 1
        assert result["index"] == item_id

        # Delete
        result = await cmd_favorites(
            ctx, ["favorites", "delete", f"item_id:{item_id}"]
        )
        assert "error" not in result

        # Exists again → should not exist
        result = await cmd_favorites(
            ctx, ["favorites", "exists", "http://test.com"]
        )
        assert result["exists"] == 0

    @pytest.mark.asyncio
    async def test_add_rename_verify(self, favorites_env):
        store, _ = favorites_env

        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()

        await cmd_favorites(
            ctx, ["favorites", "add", "url:http://a.com", "title:Old Name"]
        )
        await cmd_favorites(
            ctx, ["favorites", "rename", "item_id:0", "title:New Name"]
        )
        result = await cmd_favorites(ctx, ["favorites", "items", 0, 100])
        assert result["loop"][0]["name"] == "New Name"

    @pytest.mark.asyncio
    async def test_add_folder_with_children_browse(self, favorites_env):
        store, _ = favorites_env

        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()

        await cmd_favorites(ctx, ["favorites", "addlevel", "title:My Radio"])
        # Now add items inside the folder
        store.add("http://r1.com", "Radio 1", index="0.0")
        store.add("http://r2.com", "Radio 2", index="0.1")

        # Browse top level → should show the folder
        result = await cmd_favorites(ctx, ["favorites", "items", 0, 100])
        assert result["count"] == 1
        assert result["loop"][0]["name"] == "My Radio"

        # Browse inside folder
        result = await cmd_favorites(
            ctx, ["favorites", "items", 0, 100, "item_id:0"]
        )
        assert result["count"] == 2
        assert result["loop"][0]["name"] == "Radio 1"
        assert result["loop"][1]["name"] == "Radio 2"

    @pytest.mark.asyncio
    async def test_move_and_verify_order(self, favorites_env):
        store, _ = favorites_env

        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()

        for label in ("A", "B", "C", "D"):
            await cmd_favorites(
                ctx,
                ["favorites", "add", f"url:http://{label.lower()}.com", f"title:{label}"],
            )

        # Move D (index 3) to position 1
        await cmd_favorites(ctx, ["favorites", "move", "from_id:3", "to_id:1"])

        result = await cmd_favorites(ctx, ["favorites", "items", 0, 100])
        names = [item["name"] for item in result["loop"]]
        assert names == ["A", "D", "B", "C"]

    @pytest.mark.asyncio
    async def test_persistence_across_store_instances(self, favorites_env, tmp_path):
        store, fav_mod = favorites_env

        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()

        await cmd_favorites(
            ctx, ["favorites", "add", "url:http://persist.com", "title:Persist"]
        )

        # Create a new store from the same dir
        from plugins.favorites.store import FavoritesStore

        store2 = FavoritesStore(tmp_path)
        store2.load()
        assert store2.count == 1
        assert store2.has_url("http://persist.com")
        assert store2.get_entry("0").title == "Persist"

    @pytest.mark.asyncio
    async def test_jive_menu_items_have_correct_structure(self, favorites_env):
        """Verify the Jive menu item structure matches what devices expect."""
        store, _ = favorites_env
        store.add("http://radio.com/stream", "Cool Radio", icon="http://radio.com/icon.png")
        store.add_level("Folder")

        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()
        result = await cmd_favorites(
            ctx, ["favorites", "items", 0, 100, "menu:1"]
        )

        # Audio item
        audio = result["item_loop"][0]
        assert audio["text"] == "Cool Radio"
        assert audio["type"] == "audio"
        assert audio["hasitems"] == 0
        assert audio["icon"] == "http://radio.com/icon.png"
        assert "presetParams" in audio
        assert audio["presetParams"]["favorites_url"] == "http://radio.com/stream"
        assert audio["presetParams"]["favorites_title"] == "Cool Radio"
        play_action = audio["actions"]["play"]
        assert play_action["cmd"] == ["favorites", "playlist", "play"]
        assert play_action["params"]["item_id"] == "0"

        # Folder item
        folder = result["item_loop"][1]
        assert folder["text"] == "Folder"
        assert folder["type"] == "folder"
        assert folder["hasitems"] == 1
        go_action = folder["actions"]["go"]
        assert go_action["cmd"] == ["favorites", "items"]
        assert go_action["params"]["item_id"] == "1"


# ---------------------------------------------------------------------------
# Edge case and stress tests
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_store_special_chars_in_title(self, tmp_path):
        from plugins.favorites.store import FavoritesStore

        store = FavoritesStore(tmp_path)
        store.load()
        store.add("http://a.com", 'Title with "quotes" & <special> chars')

        store2 = FavoritesStore(tmp_path)
        store2.load()
        assert store2.get_entry("0").title == 'Title with "quotes" & <special> chars'

    def test_store_unicode_title(self, tmp_path):
        from plugins.favorites.store import FavoritesStore

        store = FavoritesStore(tmp_path)
        store.load()
        store.add("http://a.com", "日本語タイトル — Ñoño 🎵")

        store2 = FavoritesStore(tmp_path)
        store2.load()
        assert store2.get_entry("0").title == "日本語タイトル — Ñoño 🎵"

    def test_store_very_long_url(self, tmp_path):
        from plugins.favorites.store import FavoritesStore

        store = FavoritesStore(tmp_path)
        store.load()
        long_url = "http://example.com/" + "x" * 10_000
        store.add(long_url, "Long URL")
        assert store.has_url(long_url)
        assert store.find_url(long_url) == "0"

    def test_store_empty_url(self, tmp_path):
        from plugins.favorites.store import FavoritesStore

        store = FavoritesStore(tmp_path)
        store.load()
        # Empty string URL should still work
        store.add("", "Empty URL")
        assert store.count == 1

    def test_delete_last_item(self, tmp_path):
        from plugins.favorites.store import FavoritesStore

        store = FavoritesStore(tmp_path)
        store.load()
        store.add("http://a.com", "A")
        store.delete_by_index("0")
        assert store.count == 0

    def test_add_many_items(self, tmp_path):
        from plugins.favorites.store import FavoritesStore

        store = FavoritesStore(tmp_path)
        store.load()
        for i in range(500):
            store.add(f"http://{i}.example.com", f"Item {i}")
        assert store.count == 500
        assert store.find_url("http://499.example.com") == "499"

    @pytest.mark.asyncio
    async def test_concurrent_style_mutations(self, favorites_env):
        """Simulate rapid sequential mutations (no actual concurrency)."""
        store, _ = favorites_env

        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()

        for i in range(20):
            await cmd_favorites(
                ctx,
                ["favorites", "add", f"url:http://{i}.com", f"title:Item{i}"],
            )

        for i in range(0, 20, 2):
            await cmd_favorites(ctx, ["favorites", "delete", f"url:http://{i}.com"])

        result = await cmd_favorites(ctx, ["favorites", "items", 0, 100])
        assert result["count"] == 10

    def test_resolve_level_out_of_range(self, tmp_path):
        from plugins.favorites.store import FavoritesStore

        store = FavoritesStore(tmp_path)
        store.load()
        store.add("http://a.com", "A")

        with pytest.raises(IndexError):
            store._resolve_level("5")

    def test_resolve_level_non_folder(self, tmp_path):
        from plugins.favorites.store import FavoritesStore

        store = FavoritesStore(tmp_path)
        store.load()
        store.add("http://a.com", "A")

        # Trying to descend into a non-folder
        with pytest.raises(IndexError):
            store._resolve_level("0.0", parent=True)

    @pytest.mark.asyncio
    async def test_items_with_icon_in_cli(self, favorites_env):
        store, _ = favorites_env
        store.add("http://a.com", "A", icon="http://icon.com/img.png")

        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()
        result = await cmd_favorites(ctx, ["favorites", "items", 0, 100])
        assert result["loop"][0]["icon"] == "http://icon.com/img.png"

    @pytest.mark.asyncio
    async def test_items_without_icon_in_cli(self, favorites_env):
        store, _ = favorites_env
        store.add("http://a.com", "A")

        from plugins.favorites import cmd_favorites

        ctx = _FakeCommandContext()
        result = await cmd_favorites(ctx, ["favorites", "items", 0, 100])
        assert "icon" not in result["loop"][0]
