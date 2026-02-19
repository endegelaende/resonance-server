"""Plugin repository client."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import resonance

logger = logging.getLogger(__name__)

DEFAULT_REPOSITORY_URL = "https://endegelaende.github.io/resonance-community-plugins/repository/index.json"
CACHE_TTL_SECONDS = 600


@dataclass(frozen=True)
class RepositoryEntry:
    """One plugin entry from the repository index."""

    name: str
    version: str
    description: str = ""
    author: str = ""
    category: str = "misc"
    icon: str = ""
    min_resonance_version: str = ""
    url: str = ""
    sha256: str = ""
    homepage: str = ""
    changelog: str = ""
    tags: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RepositoryEntry:
        return cls(
            name=str(data.get("name", "")),
            version=str(data.get("version", "")),
            description=str(data.get("description", "")),
            author=str(data.get("author", "")),
            category=str(data.get("category", "misc")),
            icon=str(data.get("icon", "")),
            min_resonance_version=str(data.get("min_resonance_version", "")),
            url=str(data.get("url", "")),
            sha256=str(data.get("sha256", "")),
            homepage=str(data.get("homepage", "")),
            changelog=str(data.get("changelog", "")),
            tags=tuple(str(tag) for tag in data.get("tags", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "category": self.category,
            "icon": self.icon,
            "min_resonance_version": self.min_resonance_version,
            "url": self.url,
            "sha256": self.sha256,
            "homepage": self.homepage,
            "changelog": self.changelog,
            "tags": list(self.tags),
        }


class PluginRepository:
    """Fetches and caches plugin repository entries."""

    def __init__(
        self,
        repo_url: str = DEFAULT_REPOSITORY_URL,
        cache_ttl_seconds: int = CACHE_TTL_SECONDS,
    ) -> None:
        self.repo_url = repo_url
        self.cache_ttl_seconds = cache_ttl_seconds
        self._custom_repos: list[str] = []
        self._cache: list[RepositoryEntry] = []
        self._cache_time: float = 0.0

    @property
    def repositories(self) -> list[str]:
        """Return configured repository URLs (primary first)."""
        return [self.repo_url, *self._custom_repos]

    def add_repository(self, url: str) -> None:
        cleaned = url.strip()
        if cleaned and cleaned not in self._custom_repos:
            self._custom_repos.append(cleaned)
            self._cache_time = 0.0

    def remove_repository(self, url: str) -> None:
        cleaned = url.strip()
        if cleaned in self._custom_repos:
            self._custom_repos.remove(cleaned)
            self._cache_time = 0.0

    async def fetch_available(self, force_refresh: bool = False) -> list[RepositoryEntry]:
        """Fetch and merge repository indexes, with in-memory caching."""
        now = time.monotonic()
        if (
            not force_refresh
            and self._cache
            and now - self._cache_time < self.cache_ttl_seconds
        ):
            return list(self._cache)

        import httpx

        merged: dict[str, RepositoryEntry] = {}
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            for repo in self.repositories:
                try:
                    response = await client.get(repo)
                    response.raise_for_status()
                    payload = response.json()
                    plugins = payload.get("plugins", [])
                    if not isinstance(plugins, list):
                        logger.warning("Repository %s returned invalid plugins list", repo)
                        continue

                    for raw in plugins:
                        if not isinstance(raw, dict):
                            continue
                        try:
                            entry = RepositoryEntry.from_dict(raw)
                        except Exception as exc:
                            logger.warning("Invalid repository entry from %s: %s", repo, exc)
                            continue

                        if not entry.name or not entry.url:
                            logger.warning(
                                "Skipping incomplete repository entry from %s: %s",
                                repo,
                                entry.name or "<missing-name>",
                            )
                            continue

                        current = merged.get(entry.name)
                        if current is None or self._version_gt(entry.version, current.version):
                            merged[entry.name] = entry
                except Exception as exc:
                    logger.warning("Failed to fetch repository %s: %s", repo, exc)

        deduped = sorted(merged.values(), key=lambda entry: entry.name.lower())
        self._cache = deduped
        self._cache_time = time.monotonic()
        logger.info(
            "Fetched %d repository plugins from %d repositories",
            len(deduped),
            len(self.repositories),
        )
        return list(self._cache)

    def compare_with_installed(
        self,
        available: list[RepositoryEntry],
        installed: dict[str, str],
        core_plugins: set[str],
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for entry in available:
            is_core = entry.name in core_plugins
            installed_version = installed.get(entry.name)
            update_available = (
                installed_version is not None
                and not is_core
                and self._version_gt(entry.version, installed_version)
            )
            compatible, incompatible_reason = self.check_compatible(entry)
            info = entry.to_dict()
            info["installed_version"] = installed_version
            info["update_available"] = update_available
            info["is_core"] = is_core
            info["compatible"] = compatible
            info["incompatible_reason"] = incompatible_reason
            info["can_install"] = compatible and not is_core and installed_version is None
            info["can_update"] = compatible and update_available
            result.append(info)
        return result

    async def download_plugin(self, entry: RepositoryEntry) -> bytes:
        """Download plugin zip bytes for the given repository entry."""
        import httpx

        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            response = await client.get(entry.url)
            response.raise_for_status()
            return response.content

    @staticmethod
    def check_compatible(entry: RepositoryEntry) -> tuple[bool, str]:
        """Check whether a repository plugin is compatible with this server.

        Returns:
            A ``(compatible, reason)`` tuple.  *compatible* is ``True`` when
            the plugin can be installed; *reason* contains a human-readable
            explanation when it cannot.
        """
        required = entry.min_resonance_version.strip()
        if not required:
            return True, ""

        server_version = resonance.__version__
        if PluginRepository._version_gt(required, server_version):
            return False, (
                f"Plugin requires Resonance >= {required}, "
                f"but this server is {server_version}"
            )
        return True, ""

    @staticmethod
    def _version_gt(a: str, b: str) -> bool:
        def parse(text: str) -> tuple[int, ...]:
            parts = []
            for part in str(text).split("."):
                digits = "".join(ch for ch in part if ch.isdigit())
                parts.append(int(digits) if digits else 0)
            return tuple(parts) if parts else (0,)

        return parse(a) > parse(b)
