"""
Pluggable podcast search & discovery providers.

Each provider implements :class:`PodcastSearchProvider` and can:

* **Search** for podcasts by keyword
* **Fetch trending** podcasts (where the API supports it)
* **Fetch feed details** by URL (where the API supports it)

Supported providers
~~~~~~~~~~~~~~~~~~~

* **PodcastIndex** — open podcast directory with authentication
  (`podcastindex.org <https://podcastindex.org/>`_).  Supports search,
  trending, new-episode lookup per feed, and category browsing.
* **GPodder** — community-driven directory, no auth required
  (`gpodder.net <https://gpodder.net/>`_).  Supports search and
  top-lists by category/tag.
* **iTunes** — Apple's free search API
  (`affiliate.itunes.apple.com <https://affiliate.itunes.apple.com/>`_).
  Supports search and top-charts.  No API key needed.

LMS Reference
~~~~~~~~~~~~~~

LMS hard-codes PodcastIndex + GPodder as ``Provider.pm`` subclasses with
obfuscated API keys.  We go further: three providers, clean class
hierarchy, typed results, and a trending/discovery surface that LMS
lacks entirely.
"""

from __future__ import annotations

import hashlib
import logging
import time
import urllib.parse
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import httpx  # noqa: F401 — used for type hints

logger = logging.getLogger(__name__)


# ============================================================================
# Result data classes
# ============================================================================


@dataclass(frozen=True, slots=True)
class PodcastSearchResult:
    """A single podcast discovered via search or trending."""

    name: str
    """Podcast / show title."""

    url: str
    """RSS feed URL."""

    image: str = ""
    """Cover art URL."""

    description: str = ""
    """Short description (first ~300 chars)."""

    author: str = ""
    """Author or creator."""

    language: str = ""
    """Language code (e.g. ``"en"``, ``"de"``)."""

    categories: list[str] = field(default_factory=list)
    """Category tags."""

    episode_count: int = 0
    """Total episode count (when available from the API)."""

    last_update: float = 0.0
    """Unix timestamp of the most recent episode (0 = unknown)."""

    provider: str = ""
    """Which provider returned this result."""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "url": self.url,
        }
        if self.image:
            d["image"] = self.image
        if self.description:
            d["description"] = self.description
        if self.author:
            d["author"] = self.author
        if self.language:
            d["language"] = self.language
        if self.categories:
            d["categories"] = self.categories
        if self.episode_count:
            d["episode_count"] = self.episode_count
        if self.last_update:
            d["last_update"] = self.last_update
        if self.provider:
            d["provider"] = self.provider
        return d


@dataclass(frozen=True, slots=True)
class NewEpisodeResult:
    """A new episode discovered via provider API (not full RSS parse)."""

    title: str
    url: str
    feed_url: str = ""
    feed_title: str = ""
    image: str = ""
    published_epoch: float = 0.0
    duration_seconds: int = 0
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "title": self.title,
            "url": self.url,
        }
        if self.feed_url:
            d["feed_url"] = self.feed_url
        if self.feed_title:
            d["feed_title"] = self.feed_title
        if self.image:
            d["image"] = self.image
        if self.published_epoch:
            d["published_epoch"] = self.published_epoch
        if self.duration_seconds:
            d["duration_seconds"] = self.duration_seconds
        if self.description:
            d["description"] = self.description[:300]
        return d


# ============================================================================
# Abstract base
# ============================================================================


class PodcastSearchProvider(ABC):
    """Abstract base for podcast search / discovery providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Machine-readable provider name (e.g. ``"podcastindex"``)."""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable provider name for UI display."""

    @abstractmethod
    async def search(
        self,
        query: str,
        *,
        max_results: int = 20,
        client: Any | None = None,
    ) -> list[PodcastSearchResult]:
        """Search for podcasts matching *query*.

        Args:
            query: Free-text search string.
            max_results: Maximum number of results to return.
            client: Optional ``httpx.AsyncClient`` to reuse.

        Returns:
            List of :class:`PodcastSearchResult`.
        """

    async def trending(
        self,
        *,
        max_results: int = 20,
        language: str = "",
        category: str = "",
        client: Any | None = None,
    ) -> list[PodcastSearchResult]:
        """Fetch currently trending podcasts.

        Not all providers support this — the default returns an empty list.
        """
        return []

    async def new_episodes_for_feed(
        self,
        feed_url: str,
        *,
        since_epoch: float = 0.0,
        max_episodes: int = 10,
        client: Any | None = None,
    ) -> list[NewEpisodeResult]:
        """Fetch new episodes for a specific feed via the provider API.

        This is faster than full RSS parsing when the provider supports it.
        Falls back to empty list.
        """
        return []

    @property
    def supports_trending(self) -> bool:
        """Whether this provider supports trending/discovery."""
        return False

    @property
    def supports_new_episodes(self) -> bool:
        """Whether this provider can fetch new episodes per feed via API."""
        return False


# ============================================================================
# PodcastIndex provider
# ============================================================================

# Public API credentials — PodcastIndex encourages open access
_PI_BASE = "https://api.podcastindex.org/api/1.0"
_PI_KEY = "YPBFNRQ3GAFCFKWN2WXB"
_PI_SECRET = "FN$5xQbVcPjvEU#gBsK4S$wrtHzrG7HFc3Nex#Eq"
_PI_USER_AGENT = "Resonance/2.0 (Podcast Plugin)"


def _podcastindex_headers() -> dict[str, str]:
    """Build PodcastIndex authentication headers.

    Auth scheme: SHA-1 of ``api_key + api_secret + unix_timestamp``.
    """
    auth_time = str(int(time.time()))
    auth_hash = hashlib.sha1(
        (_PI_KEY + _PI_SECRET + auth_time).encode()
    ).hexdigest()

    return {
        "User-Agent": _PI_USER_AGENT,
        "X-Auth-Key": _PI_KEY,
        "X-Auth-Date": auth_time,
        "Authorization": auth_hash,
    }


async def _pi_get(
    path: str,
    params: dict[str, str] | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    """Make an authenticated GET request to PodcastIndex.

    Returns parsed JSON dict, or empty dict on error.
    """
    import httpx

    url = f"{_PI_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    headers = _podcastindex_headers()
    close_after = False

    if client is None:
        client = httpx.AsyncClient(follow_redirects=True, timeout=15.0)
        close_after = True

    try:
        response = await client.get(url, headers=headers, timeout=10.0)
        response.raise_for_status()
        return response.json()  # type: ignore[no-any-return]
    except Exception as exc:
        logger.warning("PodcastIndex request failed (%s): %s", path, exc)
        return {}
    finally:
        if close_after:
            await client.aclose()


class PodcastIndexProvider(PodcastSearchProvider):
    """PodcastIndex.org — open, community-driven podcast directory.

    Features:
    * Full-text search
    * Trending podcasts (global + per-language + per-category)
    * New episode lookup per feed URL (avoids full RSS parse)
    * Category browsing
    """

    @property
    def name(self) -> str:
        return "podcastindex"

    @property
    def display_name(self) -> str:
        return "PodcastIndex"

    @property
    def supports_trending(self) -> bool:
        return True

    @property
    def supports_new_episodes(self) -> bool:
        return True

    async def search(
        self,
        query: str,
        *,
        max_results: int = 20,
        client: Any | None = None,
    ) -> list[PodcastSearchResult]:
        if not query.strip():
            return []

        data = await _pi_get(
            "/search/byterm",
            {"q": query, "max": str(max_results)},
            client=client,
        )

        feeds = data.get("feeds", [])
        if not isinstance(feeds, list):
            return []

        results: list[PodcastSearchResult] = []
        for feed in feeds:
            if not isinstance(feed, dict):
                continue
            feed_url = feed.get("url", "")
            if not feed_url:
                continue

            # Prefer high-res artwork
            image = ""
            for key in ("artwork", "image"):
                if feed.get(key):
                    image = str(feed[key])
                    break

            # Categories from PodcastIndex
            categories: list[str] = []
            cat_dict = feed.get("categories", {})
            if isinstance(cat_dict, dict):
                categories = [str(v) for v in cat_dict.values() if v]

            description = str(feed.get("description", ""))
            if len(description) > 300:
                description = description[:297] + "..."

            results.append(PodcastSearchResult(
                name=str(feed.get("title", "")),
                url=feed_url,
                image=image,
                description=description,
                author=str(feed.get("author", "")),
                language=str(feed.get("language", "")),
                categories=categories,
                episode_count=int(feed.get("episodeCount", 0)),
                last_update=float(feed.get("newestItemPubdate", 0)),
                provider="podcastindex",
            ))

        return results[:max_results]

    async def trending(
        self,
        *,
        max_results: int = 20,
        language: str = "",
        category: str = "",
        client: Any | None = None,
    ) -> list[PodcastSearchResult]:
        params: dict[str, str] = {"max": str(max_results)}
        if language:
            params["lang"] = language
        if category:
            params["cat"] = category

        data = await _pi_get("/podcasts/trending", params, client=client)

        feeds = data.get("feeds", [])
        if not isinstance(feeds, list):
            return []

        results: list[PodcastSearchResult] = []
        for feed in feeds:
            if not isinstance(feed, dict):
                continue
            feed_url = feed.get("url", "")
            if not feed_url:
                continue

            image = ""
            for key in ("artwork", "image"):
                if feed.get(key):
                    image = str(feed[key])
                    break

            categories: list[str] = []
            cat_dict = feed.get("categories", {})
            if isinstance(cat_dict, dict):
                categories = [str(v) for v in cat_dict.values() if v]

            results.append(PodcastSearchResult(
                name=str(feed.get("title", "")),
                url=feed_url,
                image=image,
                description=str(feed.get("description", ""))[:300],
                author=str(feed.get("author", "")),
                language=str(feed.get("language", "")),
                categories=categories,
                episode_count=int(feed.get("episodeCount", 0)),
                last_update=float(feed.get("newestItemPubdate", 0)),
                provider="podcastindex",
            ))

        return results[:max_results]

    async def new_episodes_for_feed(
        self,
        feed_url: str,
        *,
        since_epoch: float = 0.0,
        max_episodes: int = 10,
        client: Any | None = None,
    ) -> list[NewEpisodeResult]:
        """Fetch recent episodes for a feed via PodcastIndex ``episodes/byfeedurl``.

        This is the same technique LMS uses in ``PodcastIndex.pm::newsHandler``
        but done properly with typed results.
        """
        params: dict[str, str] = {
            "url": feed_url,
            "max": str(max_episodes),
        }
        if since_epoch > 0:
            params["since"] = str(int(since_epoch))

        data = await _pi_get("/episodes/byfeedurl", params, client=client)

        items = data.get("items", [])
        if not isinstance(items, list):
            return []

        results: list[NewEpisodeResult] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            enc_url = item.get("enclosureUrl", "")
            if not enc_url:
                continue

            image = str(item.get("image", "") or item.get("feedImage", ""))
            description = str(item.get("description", ""))
            if len(description) > 300:
                description = description[:297] + "..."

            results.append(NewEpisodeResult(
                title=str(item.get("title", "")),
                url=enc_url,
                feed_url=feed_url,
                feed_title=str(item.get("feedTitle", "")),
                image=image,
                published_epoch=float(item.get("datePublished", 0)),
                duration_seconds=int(item.get("duration", 0)),
                description=description,
            ))

        return results[:max_episodes]


# ============================================================================
# GPodder provider
# ============================================================================

_GPODDER_BASE = "https://gpodder.net"


class GPodderProvider(PodcastSearchProvider):
    """GPodder.net — community-driven, no-auth-required directory.

    Features:
    * Full-text search
    * Top podcasts by tag (used as trending fallback)
    """

    @property
    def name(self) -> str:
        return "gpodder"

    @property
    def display_name(self) -> str:
        return "GPodder"

    @property
    def supports_trending(self) -> bool:
        return True

    async def search(
        self,
        query: str,
        *,
        max_results: int = 20,
        client: Any | None = None,
    ) -> list[PodcastSearchResult]:
        if not query.strip():
            return []

        import httpx

        url = f"{_GPODDER_BASE}/search.json?scale_logo=256&q={urllib.parse.quote(query)}"
        close_after = False

        if client is None:
            client = httpx.AsyncClient(follow_redirects=True, timeout=15.0)
            close_after = True

        try:
            response = await client.get(
                url,
                headers={"User-Agent": _PI_USER_AGENT},
                timeout=10.0,
            )
            response.raise_for_status()
            feeds = response.json()
        except Exception as exc:
            logger.warning("GPodder search failed: %s", exc)
            return []
        finally:
            if close_after:
                await client.aclose()

        if not isinstance(feeds, list):
            return []

        results: list[PodcastSearchResult] = []
        for feed in feeds:
            if not isinstance(feed, dict):
                continue
            feed_url = feed.get("url", "")
            if not feed_url:
                continue

            # GPodder image keys: scaled_logo_url > logo_url
            image = ""
            for key in ("scaled_logo_url", "logo_url"):
                if feed.get(key):
                    image = str(feed[key])
                    break

            description = str(feed.get("description", ""))
            if len(description) > 300:
                description = description[:297] + "..."

            results.append(PodcastSearchResult(
                name=str(feed.get("title", "")),
                url=feed_url,
                image=image,
                description=description,
                author=str(feed.get("author", "")),
                language="",  # GPodder doesn't return language
                episode_count=0,
                provider="gpodder",
            ))

        return results[:max_results]

    async def trending(
        self,
        *,
        max_results: int = 20,
        language: str = "",
        category: str = "",
        client: Any | None = None,
    ) -> list[PodcastSearchResult]:
        """Fetch top podcasts from GPodder (top-lists endpoint)."""
        import httpx

        # GPodder top list — optionally by tag
        if category:
            url = f"{_GPODDER_BASE}/api/2/podcasts/tag/{urllib.parse.quote(category)}/{max_results}.json"
        else:
            url = f"{_GPODDER_BASE}/toplist/{max_results}.json?scale_logo=256"

        close_after = False
        if client is None:
            client = httpx.AsyncClient(follow_redirects=True, timeout=15.0)
            close_after = True

        try:
            response = await client.get(
                url,
                headers={"User-Agent": _PI_USER_AGENT},
                timeout=10.0,
            )
            response.raise_for_status()
            feeds = response.json()
        except Exception as exc:
            logger.warning("GPodder trending failed: %s", exc)
            return []
        finally:
            if close_after:
                await client.aclose()

        if not isinstance(feeds, list):
            return []

        results: list[PodcastSearchResult] = []
        for feed in feeds:
            if not isinstance(feed, dict):
                continue
            feed_url = feed.get("url", "")
            if not feed_url:
                continue

            image = ""
            for key in ("scaled_logo_url", "logo_url"):
                if feed.get(key):
                    image = str(feed[key])
                    break

            results.append(PodcastSearchResult(
                name=str(feed.get("title", "")),
                url=feed_url,
                image=image,
                description=str(feed.get("description", ""))[:300],
                author=str(feed.get("author", "")),
                provider="gpodder",
            ))

        return results[:max_results]


# ============================================================================
# iTunes Search provider
# ============================================================================

_ITUNES_SEARCH_BASE = "https://itunes.apple.com"


class ITunesSearchProvider(PodcastSearchProvider):
    """Apple iTunes Search API — free, no auth required.

    Features:
    * Full-text search (the most comprehensive podcast index)
    * Top charts (via lookup API)

    The iTunes API returns ``feedUrl`` which is the actual RSS feed URL,
    so results are directly usable.
    """

    @property
    def name(self) -> str:
        return "itunes"

    @property
    def display_name(self) -> str:
        return "iTunes / Apple Podcasts"

    @property
    def supports_trending(self) -> bool:
        return True

    async def search(
        self,
        query: str,
        *,
        max_results: int = 20,
        client: Any | None = None,
    ) -> list[PodcastSearchResult]:
        if not query.strip():
            return []

        import httpx

        params = {
            "term": query,
            "media": "podcast",
            "limit": str(min(max_results, 50)),
            "entity": "podcast",
        }
        url = f"{_ITUNES_SEARCH_BASE}/search?{urllib.parse.urlencode(params)}"

        close_after = False
        if client is None:
            client = httpx.AsyncClient(follow_redirects=True, timeout=15.0)
            close_after = True

        try:
            response = await client.get(
                url,
                headers={"User-Agent": _PI_USER_AGENT},
                timeout=10.0,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            logger.warning("iTunes search failed: %s", exc)
            return []
        finally:
            if close_after:
                await client.aclose()

        items = data.get("results", [])
        if not isinstance(items, list):
            return []

        results: list[PodcastSearchResult] = []
        for item in items:
            if not isinstance(item, dict):
                continue

            # iTunes returns feedUrl — the actual RSS URL
            feed_url = item.get("feedUrl", "")
            if not feed_url:
                continue

            # Prefer high-res artwork
            image = str(
                item.get("artworkUrl600", "")
                or item.get("artworkUrl100", "")
                or item.get("artworkUrl60", "")
            )

            # Extract genres as categories
            genres = item.get("genres", [])
            categories = [str(g) for g in genres] if isinstance(genres, list) else []

            results.append(PodcastSearchResult(
                name=str(item.get("collectionName", item.get("trackName", ""))),
                url=feed_url,
                image=image,
                description="",  # iTunes search doesn't return descriptions
                author=str(item.get("artistName", "")),
                language="",  # Not in search results
                categories=categories,
                episode_count=int(item.get("trackCount", 0)),
                last_update=0.0,
                provider="itunes",
            ))

        return results[:max_results]

    async def trending(
        self,
        *,
        max_results: int = 20,
        language: str = "",
        category: str = "",
        client: Any | None = None,
    ) -> list[PodcastSearchResult]:
        """Fetch top podcasts via iTunes lookup.

        Uses the RSS generator endpoint for top podcast charts.
        """
        import httpx

        # Apple's RSS feed generator for top podcasts
        country = language[:2].upper() if language and len(language) >= 2 else "US"
        genre_suffix = ""
        if category:
            # Map common categories to iTunes genre IDs
            genre_map = {
                "arts": "1301",
                "business": "1321",
                "comedy": "1303",
                "education": "1304",
                "health": "1307",
                "kids": "1305",
                "music": "1310",
                "news": "1311",
                "science": "1315",
                "society": "1324",
                "sports": "1316",
                "technology": "1318",
                "true crime": "1488",
                "tv": "1309",
            }
            genre_id = genre_map.get(category.lower(), "")
            if genre_id:
                genre_suffix = f"/genre={genre_id}"

        url = (
            f"https://rss.applemarketingtools.com/api/v2/{country.lower()}"
            f"/podcasts/top/{max_results}/podcasts{genre_suffix}.json"
        )

        close_after = False
        if client is None:
            client = httpx.AsyncClient(follow_redirects=True, timeout=15.0)
            close_after = True

        try:
            response = await client.get(
                url,
                headers={"User-Agent": _PI_USER_AGENT},
                timeout=10.0,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            logger.warning("iTunes trending failed: %s", exc)
            return []
        finally:
            if close_after:
                await client.aclose()

        # Apple Marketing Tools returns {feed: {results: [...]}}
        feed_data = data.get("feed", {})
        items = feed_data.get("results", [])
        if not isinstance(items, list):
            return []

        results: list[PodcastSearchResult] = []
        for item in items:
            if not isinstance(item, dict):
                continue

            # This endpoint returns Apple Podcast IDs, not feed URLs directly.
            # We need to look up the feedUrl via the iTunes Lookup API.
            # For efficiency, we store the iTunes URL and resolve lazily.
            podcast_id = item.get("id", "")
            name = str(item.get("name", ""))
            artist = str(item.get("artistName", ""))
            image = str(item.get("artworkUrl100", ""))

            # Genre info
            genres = item.get("genres", [])
            categories = []
            if isinstance(genres, list):
                for g in genres:
                    if isinstance(g, dict):
                        categories.append(str(g.get("name", "")))
                    elif isinstance(g, str):
                        categories.append(g)

            if not podcast_id:
                continue

            results.append(PodcastSearchResult(
                name=name,
                url="",  # Will be resolved via lookup
                image=image,
                author=artist,
                categories=categories,
                provider="itunes",
            ))

        # Batch-resolve feed URLs via iTunes Lookup API
        if results:
            ids = [item.get("id", "") for item in items if isinstance(item, dict) and item.get("id")]
            if ids:
                resolved = await self._batch_lookup_feed_urls(ids[:max_results], client=client)
                final_results: list[PodcastSearchResult] = []
                for i, result in enumerate(results):
                    if i < len(ids):
                        feed_url = resolved.get(str(ids[i]), "")
                        if feed_url:
                            final_results.append(PodcastSearchResult(
                                name=result.name,
                                url=feed_url,
                                image=result.image,
                                description=result.description,
                                author=result.author,
                                language=result.language,
                                categories=result.categories,
                                provider="itunes",
                            ))
                return final_results[:max_results]

        return results[:max_results]

    async def _batch_lookup_feed_urls(
        self,
        podcast_ids: list[str],
        client: Any | None = None,
    ) -> dict[str, str]:
        """Resolve iTunes podcast IDs to RSS feed URLs via the Lookup API."""
        import httpx

        if not podcast_ids:
            return {}

        # iTunes lookup supports comma-separated IDs
        id_str = ",".join(str(pid) for pid in podcast_ids[:200])
        url = f"{_ITUNES_SEARCH_BASE}/lookup?id={id_str}&entity=podcast"

        close_after = False
        if client is None:
            client = httpx.AsyncClient(follow_redirects=True, timeout=15.0)
            close_after = True

        try:
            response = await client.get(
                url,
                headers={"User-Agent": _PI_USER_AGENT},
                timeout=10.0,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            logger.warning("iTunes batch lookup failed: %s", exc)
            return {}
        finally:
            if close_after:
                await client.aclose()

        result_map: dict[str, str] = {}
        for item in data.get("results", []):
            if not isinstance(item, dict):
                continue
            cid = str(item.get("collectionId", ""))
            feed_url = item.get("feedUrl", "")
            if cid and feed_url:
                result_map[cid] = feed_url

        return result_map


# ============================================================================
# Provider registry
# ============================================================================


# Singleton instances
_PROVIDERS: dict[str, PodcastSearchProvider] = {}


def _ensure_providers() -> None:
    """Lazily initialise the provider registry."""
    if _PROVIDERS:
        return
    _PROVIDERS["podcastindex"] = PodcastIndexProvider()
    _PROVIDERS["gpodder"] = GPodderProvider()
    _PROVIDERS["itunes"] = ITunesSearchProvider()


def get_provider(name: str = "podcastindex") -> PodcastSearchProvider:
    """Get a search provider by name.

    Falls back to PodcastIndex if the requested provider is unknown.
    """
    _ensure_providers()
    return _PROVIDERS.get(name, _PROVIDERS["podcastindex"])


def get_all_providers() -> dict[str, PodcastSearchProvider]:
    """Get all registered providers."""
    _ensure_providers()
    return dict(_PROVIDERS)


def list_provider_names() -> list[str]:
    """List available provider names."""
    _ensure_providers()
    return list(_PROVIDERS.keys())
