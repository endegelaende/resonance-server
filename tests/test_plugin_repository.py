from __future__ import annotations

import hashlib
import io
import zipfile
from typing import Any

import pytest

from resonance.plugin_repository import PluginRepository, RepositoryEntry


class _FakeResponse:
    def __init__(self, payload: dict[str, Any] | None = None, content: bytes = b"zip", *, status_code: int = 200) -> None:
        self._payload = payload or {}
        self.content = content
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeAsyncClient:
    def __init__(self, responses: dict[str, _FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    async def get(self, url: str) -> _FakeResponse:
        self.calls.append(url)
        if url not in self.responses:
            raise RuntimeError(f"missing fake response for {url}")
        return self.responses[url]


# =============================================================================
# TestRepositoryEntry
# =============================================================================


class TestRepositoryEntry:
    def test_from_dict_roundtrip(self) -> None:
        raw = {"name": "demo", "version": "1.0.0", "url": "https://x", "tags": ["a", "b"]}
        entry = RepositoryEntry.from_dict(raw)
        assert entry.name == "demo"
        assert entry.tags == ("a", "b")
        dumped = entry.to_dict()
        assert dumped["name"] == "demo"
        assert dumped["tags"] == ["a", "b"]

    def test_from_dict_defaults(self) -> None:
        entry = RepositoryEntry.from_dict({"name": "minimal", "version": "0.1"})
        assert entry.description == ""
        assert entry.author == ""
        assert entry.category == "misc"
        assert entry.icon == ""
        assert entry.min_resonance_version == ""
        assert entry.url == ""
        assert entry.sha256 == ""
        assert entry.homepage == ""
        assert entry.changelog == ""
        assert entry.tags == ()

    def test_from_dict_all_fields(self) -> None:
        raw = {
            "name": "full",
            "version": "2.0.0",
            "description": "A full plugin",
            "author": "Jane Doe",
            "category": "musicservices",
            "icon": "music",
            "min_resonance_version": "0.5.0",
            "url": "https://example.com/full.zip",
            "sha256": "abc123",
            "homepage": "https://example.com",
            "changelog": "Added stuff",
            "tags": ["music", "streaming"],
        }
        entry = RepositoryEntry.from_dict(raw)
        assert entry.name == "full"
        assert entry.version == "2.0.0"
        assert entry.description == "A full plugin"
        assert entry.author == "Jane Doe"
        assert entry.category == "musicservices"
        assert entry.icon == "music"
        assert entry.min_resonance_version == "0.5.0"
        assert entry.url == "https://example.com/full.zip"
        assert entry.sha256 == "abc123"
        assert entry.homepage == "https://example.com"
        assert entry.changelog == "Added stuff"
        assert entry.tags == ("music", "streaming")

    def test_to_dict_all_fields(self) -> None:
        entry = RepositoryEntry(
            name="test",
            version="1.0.0",
            description="desc",
            author="auth",
            category="tools",
            icon="wrench",
            min_resonance_version="0.1",
            url="https://x",
            sha256="abc",
            homepage="https://y",
            changelog="v1",
            tags=("a", "b", "c"),
        )
        d = entry.to_dict()
        assert d["name"] == "test"
        assert d["version"] == "1.0.0"
        assert d["description"] == "desc"
        assert d["author"] == "auth"
        assert d["category"] == "tools"
        assert d["icon"] == "wrench"
        assert d["min_resonance_version"] == "0.1"
        assert d["url"] == "https://x"
        assert d["sha256"] == "abc"
        assert d["homepage"] == "https://y"
        assert d["changelog"] == "v1"
        assert d["tags"] == ["a", "b", "c"]

    def test_frozen_dataclass(self) -> None:
        entry = RepositoryEntry(name="frozen", version="1.0")
        with pytest.raises(AttributeError):
            entry.name = "modified"  # type: ignore[misc]

    def test_empty_tags(self) -> None:
        entry = RepositoryEntry.from_dict({"name": "x", "version": "1"})
        assert entry.tags == ()
        assert entry.to_dict()["tags"] == []


# =============================================================================
# TestVersionComparison — comprehensive
# =============================================================================


class TestVersionComparison:
    def test_basic_greater(self) -> None:
        assert PluginRepository._version_gt("1.0.1", "1.0.0") is True

    def test_basic_less(self) -> None:
        assert PluginRepository._version_gt("1.0.0", "1.0.1") is False

    def test_equal_versions(self) -> None:
        assert PluginRepository._version_gt("1.0.0", "1.0.0") is False

    def test_major_version_difference(self) -> None:
        assert PluginRepository._version_gt("2.0.0", "1.9.9") is True
        assert PluginRepository._version_gt("1.9.9", "2.0.0") is False

    def test_minor_version_difference(self) -> None:
        assert PluginRepository._version_gt("1.2.0", "1.1.9") is True
        assert PluginRepository._version_gt("1.1.9", "1.2.0") is False

    def test_patch_version_difference(self) -> None:
        assert PluginRepository._version_gt("1.0.2", "1.0.1") is True

    def test_different_length_versions(self) -> None:
        assert PluginRepository._version_gt("2.0", "10.0") is False
        assert PluginRepository._version_gt("10.0", "2.0") is True

    def test_two_part_vs_three_part(self) -> None:
        assert PluginRepository._version_gt("1.1", "1.0.9") is True
        assert PluginRepository._version_gt("1.0.9", "1.1") is False

    def test_single_digit_versions(self) -> None:
        assert PluginRepository._version_gt("2", "1") is True
        assert PluginRepository._version_gt("1", "2") is False
        assert PluginRepository._version_gt("1", "1") is False

    def test_version_with_non_numeric(self) -> None:
        """Non-numeric parts should be handled gracefully."""
        assert PluginRepository._version_gt("1.0.1-beta", "1.0.0") is True
        assert PluginRepository._version_gt("1.0.0-alpha", "1.0.0") is False

    def test_empty_version_strings(self) -> None:
        assert PluginRepository._version_gt("1.0.0", "") is True
        assert PluginRepository._version_gt("", "1.0.0") is False
        assert PluginRepository._version_gt("", "") is False

    def test_prerelease_numeric_suffix(self) -> None:
        # The parser strips non-digits per part: "1rc9" → "19" → 19
        # So "1.0.1rc9" parses as (1, 0, 19), which is > (1, 0, 2)
        # This is a known quirk of the simple digit-extraction parser.
        assert PluginRepository._version_gt("1.0.1rc9", "1.0.2") is True
        assert PluginRepository._version_gt("1.0.2", "1.0.1rc9") is False

    def test_leading_zeros(self) -> None:
        assert PluginRepository._version_gt("1.02.0", "1.1.0") is True
        assert PluginRepository._version_gt("1.1.0", "1.02.0") is False

    def test_very_large_versions(self) -> None:
        assert PluginRepository._version_gt("100.200.300", "100.200.299") is True
        assert PluginRepository._version_gt("100.200.299", "100.200.300") is False


# =============================================================================
# TestCompareWithInstalled — comprehensive
# =============================================================================


class TestCompareWithInstalled:
    def _make_entries(self, *args: tuple[str, str]) -> list[RepositoryEntry]:
        return [
            RepositoryEntry(name=name, version=version, url=f"https://zip/{name}")
            for name, version in args
        ]

    def test_not_installed_can_install(self) -> None:
        repo = PluginRepository(repo_url="https://repo")
        available = self._make_entries(("new_plugin", "1.0.0"))
        compared = repo.compare_with_installed(
            available, installed={}, core_plugins=set()
        )
        assert len(compared) == 1
        item = compared[0]
        assert item["name"] == "new_plugin"
        assert item["installed_version"] is None
        assert item["can_install"] is True
        assert item["update_available"] is False
        assert item["is_core"] is False
        assert item["can_update"] is False

    def test_same_version_no_update(self) -> None:
        repo = PluginRepository(repo_url="https://repo")
        available = self._make_entries(("demo", "1.0.0"))
        compared = repo.compare_with_installed(
            available, installed={"demo": "1.0.0"}, core_plugins=set()
        )
        item = compared[0]
        assert item["update_available"] is False
        assert item["can_update"] is False
        assert item["can_install"] is False  # already installed
        assert item["installed_version"] == "1.0.0"

    def test_older_version_no_update(self) -> None:
        repo = PluginRepository(repo_url="https://repo")
        available = self._make_entries(("demo", "1.0.0"))
        compared = repo.compare_with_installed(
            available, installed={"demo": "2.0.0"}, core_plugins=set()
        )
        item = compared[0]
        assert item["update_available"] is False
        assert item["can_update"] is False

    def test_update_available(self) -> None:
        repo = PluginRepository(repo_url="https://repo")
        available = self._make_entries(("demo", "2.0.0"))
        compared = repo.compare_with_installed(
            available, installed={"demo": "1.0.0"}, core_plugins=set()
        )
        item = compared[0]
        assert item["update_available"] is True
        assert item["can_update"] is True

    def test_core_skipped_for_update(self) -> None:
        repo = PluginRepository(repo_url="https://repo")
        available = self._make_entries(("core_plugin", "9.9.9"))
        compared = repo.compare_with_installed(
            available,
            installed={"core_plugin": "1.0.0"},
            core_plugins={"core_plugin"},
        )
        item = compared[0]
        assert item["is_core"] is True
        assert item["can_install"] is False
        assert item["can_update"] is False
        assert item["update_available"] is False

    def test_core_not_installed_still_cant_install(self) -> None:
        """Even if a core plugin is listed in the repo, it can't be installed."""
        repo = PluginRepository(repo_url="https://repo")
        available = self._make_entries(("core_plugin", "1.0.0"))
        compared = repo.compare_with_installed(
            available, installed={}, core_plugins={"core_plugin"}
        )
        item = compared[0]
        assert item["is_core"] is True
        assert item["can_install"] is False

    def test_multiple_entries(self) -> None:
        repo = PluginRepository(repo_url="https://repo")
        available = self._make_entries(
            ("alpha", "1.0.0"),
            ("beta", "2.0.0"),
            ("gamma", "3.0.0"),
        )
        compared = repo.compare_with_installed(
            available,
            installed={"alpha": "1.0.0", "beta": "1.0.0"},
            core_plugins={"alpha"},
        )
        by_name = {item["name"]: item for item in compared}
        assert len(by_name) == 3

        # alpha: core, same version
        assert by_name["alpha"]["is_core"] is True
        assert by_name["alpha"]["update_available"] is False

        # beta: installed, has update
        assert by_name["beta"]["is_core"] is False
        assert by_name["beta"]["update_available"] is True
        assert by_name["beta"]["can_update"] is True

        # gamma: not installed
        assert by_name["gamma"]["can_install"] is True
        assert by_name["gamma"]["installed_version"] is None

    def test_empty_available(self) -> None:
        repo = PluginRepository(repo_url="https://repo")
        compared = repo.compare_with_installed(
            [], installed={"demo": "1.0.0"}, core_plugins=set()
        )
        assert compared == []

    def test_output_contains_all_entry_fields(self) -> None:
        repo = PluginRepository(repo_url="https://repo")
        available = [
            RepositoryEntry(
                name="full",
                version="1.0.0",
                description="A full plugin",
                author="Author",
                category="tools",
                icon="wrench",
                url="https://zip/full",
                sha256="abc",
                homepage="https://home",
                tags=("a",),
            )
        ]
        compared = repo.compare_with_installed(
            available, installed={}, core_plugins=set()
        )
        item = compared[0]
        assert item["description"] == "A full plugin"
        assert item["author"] == "Author"
        assert item["category"] == "tools"
        assert item["icon"] == "wrench"
        assert item["url"] == "https://zip/full"
        assert item["sha256"] == "abc"
        assert item["homepage"] == "https://home"
        assert item["tags"] == ["a"]


# =============================================================================
# TestPluginRepository — fetch, cache, dedup
# =============================================================================


class TestPluginRepository:
    @pytest.mark.asyncio
    async def test_fetch_available_dedup_and_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = PluginRepository(repo_url="https://repo/main.json", cache_ttl_seconds=3600)
        repo.add_repository("https://repo/custom.json")

        responses = {
            "https://repo/main.json": _FakeResponse(
                {
                    "plugins": [
                        {"name": "a", "version": "1.0.0", "url": "https://zip/a1"},
                        {"name": "b", "version": "1.0.0", "url": "https://zip/b1"},
                    ]
                }
            ),
            "https://repo/custom.json": _FakeResponse(
                {
                    "plugins": [
                        {"name": "a", "version": "1.1.0", "url": "https://zip/a2"},
                    ]
                }
            ),
        }
        fake_client = _FakeAsyncClient(responses)
        monkeypatch.setattr(
            "httpx.AsyncClient",
            lambda *args, **kwargs: fake_client,
            raising=True,
        )

        available = await repo.fetch_available()
        assert [entry.name for entry in available] == ["a", "b"]
        assert next(entry for entry in available if entry.name == "a").version == "1.1.0"

        # Cached call should not trigger new HTTP requests.
        before = len(fake_client.calls)
        cached = await repo.fetch_available()
        assert len(cached) == len(available)
        assert len(fake_client.calls) == before

    @pytest.mark.asyncio
    async def test_fetch_available_force_refresh(self, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = PluginRepository(repo_url="https://repo/main.json", cache_ttl_seconds=3600)

        responses = {
            "https://repo/main.json": _FakeResponse(
                {"plugins": [{"name": "demo", "version": "1.0.0", "url": "https://zip/demo"}]}
            ),
        }
        fake_client = _FakeAsyncClient(responses)
        monkeypatch.setattr(
            "httpx.AsyncClient",
            lambda *args, **kwargs: fake_client,
            raising=True,
        )

        # First fetch
        await repo.fetch_available()
        assert len(fake_client.calls) == 1

        # Force refresh should re-fetch despite cache
        await repo.fetch_available(force_refresh=True)
        assert len(fake_client.calls) == 2

    @pytest.mark.asyncio
    async def test_fetch_available_dedup_keeps_latest_version(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When the same plugin appears in multiple repos, the highest version wins."""
        repo = PluginRepository(repo_url="https://repo/first.json", cache_ttl_seconds=0)
        repo.add_repository("https://repo/second.json")

        responses = {
            "https://repo/first.json": _FakeResponse(
                {"plugins": [{"name": "dup", "version": "3.0.0", "url": "https://zip/dup3"}]}
            ),
            "https://repo/second.json": _FakeResponse(
                {"plugins": [{"name": "dup", "version": "2.0.0", "url": "https://zip/dup2"}]}
            ),
        }
        fake_client = _FakeAsyncClient(responses)
        monkeypatch.setattr(
            "httpx.AsyncClient",
            lambda *args, **kwargs: fake_client,
            raising=True,
        )

        available = await repo.fetch_available()
        assert len(available) == 1
        assert available[0].version == "3.0.0"
        assert available[0].url == "https://zip/dup3"

    @pytest.mark.asyncio
    async def test_fetch_available_handles_failed_repo(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A failing repository should not prevent other repos from being fetched."""
        repo = PluginRepository(repo_url="https://repo/good.json", cache_ttl_seconds=0)
        repo.add_repository("https://repo/bad.json")

        responses = {
            "https://repo/good.json": _FakeResponse(
                {"plugins": [{"name": "good_plug", "version": "1.0.0", "url": "https://zip/good"}]}
            ),
            "https://repo/bad.json": _FakeResponse(status_code=500),
        }
        fake_client = _FakeAsyncClient(responses)
        monkeypatch.setattr(
            "httpx.AsyncClient",
            lambda *args, **kwargs: fake_client,
            raising=True,
        )

        available = await repo.fetch_available()
        assert len(available) == 1
        assert available[0].name == "good_plug"

    @pytest.mark.asyncio
    async def test_fetch_available_empty_repo(self, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = PluginRepository(repo_url="https://repo/empty.json", cache_ttl_seconds=0)

        responses = {
            "https://repo/empty.json": _FakeResponse({"plugins": []}),
        }
        fake_client = _FakeAsyncClient(responses)
        monkeypatch.setattr(
            "httpx.AsyncClient",
            lambda *args, **kwargs: fake_client,
            raising=True,
        )

        available = await repo.fetch_available()
        assert available == []

    @pytest.mark.asyncio
    async def test_fetch_available_invalid_plugins_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If 'plugins' is not a list, the repo should be skipped."""
        repo = PluginRepository(repo_url="https://repo/bad_format.json", cache_ttl_seconds=0)

        responses = {
            "https://repo/bad_format.json": _FakeResponse({"plugins": "not a list"}),
        }
        fake_client = _FakeAsyncClient(responses)
        monkeypatch.setattr(
            "httpx.AsyncClient",
            lambda *args, **kwargs: fake_client,
            raising=True,
        )

        available = await repo.fetch_available()
        assert available == []

    @pytest.mark.asyncio
    async def test_fetch_available_skips_non_dict_entries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-dict entries in the plugins list should be skipped."""
        repo = PluginRepository(repo_url="https://repo/mixed.json", cache_ttl_seconds=0)

        responses = {
            "https://repo/mixed.json": _FakeResponse(
                {
                    "plugins": [
                        "not a dict",
                        42,
                        {"name": "valid", "version": "1.0.0", "url": "https://zip/valid"},
                    ]
                }
            ),
        }
        fake_client = _FakeAsyncClient(responses)
        monkeypatch.setattr(
            "httpx.AsyncClient",
            lambda *args, **kwargs: fake_client,
            raising=True,
        )

        available = await repo.fetch_available()
        assert len(available) == 1
        assert available[0].name == "valid"

    @pytest.mark.asyncio
    async def test_fetch_available_skips_incomplete_entries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Entries without name or url should be skipped."""
        repo = PluginRepository(repo_url="https://repo/incomplete.json", cache_ttl_seconds=0)

        responses = {
            "https://repo/incomplete.json": _FakeResponse(
                {
                    "plugins": [
                        {"name": "no_url", "version": "1.0.0"},  # missing url
                        {"version": "1.0.0", "url": "https://zip/x"},  # missing name → name=""
                        {"name": "complete", "version": "1.0.0", "url": "https://zip/complete"},
                    ]
                }
            ),
        }
        fake_client = _FakeAsyncClient(responses)
        monkeypatch.setattr(
            "httpx.AsyncClient",
            lambda *args, **kwargs: fake_client,
            raising=True,
        )

        available = await repo.fetch_available()
        assert len(available) == 1
        assert available[0].name == "complete"

    @pytest.mark.asyncio
    async def test_fetch_available_sorted_by_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = PluginRepository(repo_url="https://repo/main.json", cache_ttl_seconds=0)

        responses = {
            "https://repo/main.json": _FakeResponse(
                {
                    "plugins": [
                        {"name": "Zebra", "version": "1.0.0", "url": "https://zip/z"},
                        {"name": "alpha", "version": "1.0.0", "url": "https://zip/a"},
                        {"name": "Bravo", "version": "1.0.0", "url": "https://zip/b"},
                    ]
                }
            ),
        }
        fake_client = _FakeAsyncClient(responses)
        monkeypatch.setattr(
            "httpx.AsyncClient",
            lambda *args, **kwargs: fake_client,
            raising=True,
        )

        available = await repo.fetch_available()
        names = [e.name for e in available]
        assert names == ["alpha", "Bravo", "Zebra"]

    def test_compare_with_installed(self) -> None:
        repo = PluginRepository(repo_url="https://repo")
        available = [
            RepositoryEntry(name="core_plugin", version="9.9.9", url="https://zip/core"),
            RepositoryEntry(name="demo", version="2.0.0", url="https://zip/demo"),
        ]
        compared = repo.compare_with_installed(
            available,
            installed={"demo": "1.0.0", "core_plugin": "1.0.0"},
            core_plugins={"core_plugin"},
        )
        core = next(item for item in compared if item["name"] == "core_plugin")
        demo = next(item for item in compared if item["name"] == "demo")
        assert core["is_core"] is True
        assert core["can_install"] is False
        assert demo["update_available"] is True
        assert demo["can_update"] is True

    @pytest.mark.asyncio
    async def test_download_plugin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = PluginRepository(repo_url="https://repo/main.json")
        entry = RepositoryEntry(name="demo", version="1.0.0", url="https://zip/demo")
        fake_client = _FakeAsyncClient({"https://zip/demo": _FakeResponse(content=b"hello-zip")})
        monkeypatch.setattr(
            "httpx.AsyncClient",
            lambda *args, **kwargs: fake_client,
            raising=True,
        )
        content = await repo.download_plugin(entry)
        assert content == b"hello-zip"

    @pytest.mark.asyncio
    async def test_download_plugin_large_content(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Download should handle larger content correctly."""
        repo = PluginRepository(repo_url="https://repo/main.json")
        large_content = b"x" * 1024 * 1024  # 1 MB
        entry = RepositoryEntry(name="big", version="1.0.0", url="https://zip/big")
        fake_client = _FakeAsyncClient({"https://zip/big": _FakeResponse(content=large_content)})
        monkeypatch.setattr(
            "httpx.AsyncClient",
            lambda *args, **kwargs: fake_client,
            raising=True,
        )
        content = await repo.download_plugin(entry)
        assert len(content) == 1024 * 1024

    def test_version_compare(self) -> None:
        assert PluginRepository._version_gt("1.0.1", "1.0.0") is True
        assert PluginRepository._version_gt("1.0.0", "1.0.0") is False
        assert PluginRepository._version_gt("2.0", "10.0") is False


# =============================================================================
# TestPluginRepositoryManagement
# =============================================================================


class TestPluginRepositoryManagement:
    def test_add_repository(self) -> None:
        repo = PluginRepository(repo_url="https://main.json")
        repo.add_repository("https://custom.json")
        assert "https://custom.json" in repo.repositories
        assert repo.repositories[0] == "https://main.json"
        assert repo.repositories[1] == "https://custom.json"

    def test_add_repository_duplicate_ignored(self) -> None:
        repo = PluginRepository(repo_url="https://main.json")
        repo.add_repository("https://custom.json")
        repo.add_repository("https://custom.json")
        assert repo.repositories.count("https://custom.json") == 1

    def test_add_repository_empty_ignored(self) -> None:
        repo = PluginRepository(repo_url="https://main.json")
        repo.add_repository("")
        repo.add_repository("   ")
        assert len(repo.repositories) == 1

    def test_remove_repository(self) -> None:
        repo = PluginRepository(repo_url="https://main.json")
        repo.add_repository("https://custom.json")
        repo.remove_repository("https://custom.json")
        assert "https://custom.json" not in repo.repositories

    def test_remove_nonexistent_repository(self) -> None:
        repo = PluginRepository(repo_url="https://main.json")
        # Should not raise
        repo.remove_repository("https://nonexistent.json")
        assert len(repo.repositories) == 1

    def test_repositories_property(self) -> None:
        repo = PluginRepository(repo_url="https://primary.json")
        repo.add_repository("https://secondary.json")
        repo.add_repository("https://tertiary.json")
        assert repo.repositories == [
            "https://primary.json",
            "https://secondary.json",
            "https://tertiary.json",
        ]

    def test_add_repository_strips_whitespace(self) -> None:
        repo = PluginRepository(repo_url="https://main.json")
        repo.add_repository("  https://custom.json  ")
        assert "https://custom.json" in repo.repositories

    @pytest.mark.asyncio
    async def test_add_repository_invalidates_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Adding a repository should invalidate the cache so next fetch re-fetches."""
        repo = PluginRepository(repo_url="https://repo/main.json", cache_ttl_seconds=3600)

        responses = {
            "https://repo/main.json": _FakeResponse(
                {"plugins": [{"name": "a", "version": "1.0.0", "url": "https://zip/a"}]}
            ),
            "https://repo/extra.json": _FakeResponse(
                {"plugins": [{"name": "b", "version": "1.0.0", "url": "https://zip/b"}]}
            ),
        }
        fake_client = _FakeAsyncClient(responses)
        monkeypatch.setattr(
            "httpx.AsyncClient",
            lambda *args, **kwargs: fake_client,
            raising=True,
        )

        # First fetch — only main
        available1 = await repo.fetch_available()
        assert len(available1) == 1

        # Add extra repo — cache should be invalidated
        repo.add_repository("https://repo/extra.json")

        # Second fetch should include both
        available2 = await repo.fetch_available()
        assert len(available2) == 2

    @pytest.mark.asyncio
    async def test_remove_repository_invalidates_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Removing a repository should invalidate the cache."""
        repo = PluginRepository(repo_url="https://repo/main.json", cache_ttl_seconds=3600)
        repo.add_repository("https://repo/extra.json")

        responses = {
            "https://repo/main.json": _FakeResponse(
                {"plugins": [{"name": "a", "version": "1.0.0", "url": "https://zip/a"}]}
            ),
            "https://repo/extra.json": _FakeResponse(
                {"plugins": [{"name": "b", "version": "1.0.0", "url": "https://zip/b"}]}
            ),
        }
        fake_client = _FakeAsyncClient(responses)
        monkeypatch.setattr(
            "httpx.AsyncClient",
            lambda *args, **kwargs: fake_client,
            raising=True,
        )

        # First fetch — both repos
        available1 = await repo.fetch_available()
        assert len(available1) == 2

        # Remove extra repo
        repo.remove_repository("https://repo/extra.json")

        # Second fetch should only have main
        available2 = await repo.fetch_available()
        assert len(available2) == 1
        assert available2[0].name == "a"


# =============================================================================
# TestPluginRepositoryDefaults
# =============================================================================


class TestPluginRepositoryDefaults:
    def test_default_repo_url(self) -> None:
        repo = PluginRepository()
        assert "resonance-community-plugins" in repo.repo_url
        assert repo.repo_url == "https://endegelaende.github.io/resonance-community-plugins/repository/index.json"

    def test_default_cache_ttl(self) -> None:
        repo = PluginRepository()
        assert repo.cache_ttl_seconds == 600

    def test_custom_cache_ttl(self) -> None:
        repo = PluginRepository(cache_ttl_seconds=60)
        assert repo.cache_ttl_seconds == 60

    def test_custom_repo_url(self) -> None:
        repo = PluginRepository(repo_url="https://custom.example.com/index.json")
        assert repo.repo_url == "https://custom.example.com/index.json"

    def test_initial_cache_empty(self) -> None:
        repo = PluginRepository()
        assert repo._cache == []
        assert repo._cache_time == 0.0
