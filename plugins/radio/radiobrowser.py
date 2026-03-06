"""
Radio Browser API client for the Radio plugin.

This module wraps the free community **radio-browser.info** API — an open,
community-maintained database of ~40 000+ Internet radio stations with:

* **Pre-resolved stream URLs** (``url_resolved``) — M3U/PLS/redirects
  already followed by the server's check infrastructure.
* **Rich metadata** — tags, country, codec, bitrate, favicon, geo, votes.
* **Advanced search** — by name, tag, country, language, codec, bitrate.
* **Browse endpoints** — countries, tags, languages, codecs, top/trending.
* **Click counting** — ``GET /json/url/{stationuuid}`` to register plays.
* **No API key / partner ID required** — completely free for any use.

Reference
~~~~~~~~~

* Homepage: https://www.radio-browser.info
* API docs: https://de1.api.radio-browser.info (self-documenting)
* Source:   https://gitlab.com/radiobrowser/radiobrowser-api-rust

The API requests a descriptive ``User-Agent`` header.  We send
``Resonance/<version>`` so the maintainer can identify our traffic.

Server selection
~~~~~~~~~~~~~~~~

``all.api.radio-browser.info`` resolves to multiple mirror IPs.  We use
a single hardcoded base URL (``de1.api.radio-browser.info``) for
simplicity; httpx follows redirects if needed.  A future enhancement
could do DNS round-robin like the API docs suggest.

All HTTP calls use ``httpx`` (async) with JSON responses.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# We use a specific mirror rather than all.api.radio-browser.info to avoid
# DNS round-robin issues with connection pooling.  de1 is in Germany and
# has good availability.
API_BASE = "https://de1.api.radio-browser.info"

# Resonance User-Agent (requested by radio-browser.info maintainer).
USER_AGENT = "Resonance/1.0"

# HTTP timeouts (seconds).
_CONNECT_TIMEOUT = 10.0
_READ_TIMEOUT = 15.0

# Default limits.
_DEFAULT_LIMIT = 200
_MAX_LIMIT = 1000

# Browse cache TTL (seconds) — category lists change rarely.
_CACHE_TTL = 600  # 10 minutes

# Maximum cached entries.
_CACHE_MAX_ENTRIES = 128

# Top-level browse categories shown to the user.
# Each has a key (used internally), a display name, and an icon hint.
BROWSE_CATEGORIES = [
    {"key": "popular", "name": "Popular Stations", "type": "category"},
    {"key": "trending", "name": "Trending Now", "type": "category"},
    {"key": "country", "name": "By Country", "type": "category"},
    {"key": "tag", "name": "By Genre / Tag", "type": "category"},
    {"key": "language", "name": "By Language", "type": "category"},
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RadioStation:
    """A single radio station from radio-browser.info."""

    stationuuid: str
    """Globally unique station ID."""

    name: str
    """Station name."""

    url: str
    """Original stream URL (may be playlist/redirect)."""

    url_resolved: str
    """Pre-resolved direct stream URL (M3U/PLS/redirects followed)."""

    homepage: str = ""
    """Station homepage URL."""

    favicon: str = ""
    """Station logo/icon URL."""

    tags: str = ""
    """Comma-separated tags (genres)."""

    country: str = ""
    """Full country name."""

    countrycode: str = ""
    """ISO 3166-1 alpha-2 country code."""

    state: str = ""
    """State/region within the country."""

    language: str = ""
    """Languages spoken."""

    codec: str = ""
    """Audio codec (MP3, AAC, OGG, …)."""

    bitrate: int = 0
    """Bitrate in kbps."""

    votes: int = 0
    """Community vote count."""

    clickcount: int = 0
    """Clicks in last 24h."""

    lastcheckok: int = 1
    """1 if the station was online at last check."""

    hls: int = 0
    """1 if HLS stream."""

    has_extended_info: bool = False
    """True if station provides extended ICY metadata."""


@dataclass(frozen=True, slots=True)
class CategoryEntry:
    """An entry in a category list (country, tag, language)."""

    name: str
    """Display name (e.g. 'Germany', 'jazz', 'english')."""

    stationcount: int = 0
    """Number of stations in this category."""

    # For countries only:
    iso_3166_1: str = ""
    """ISO country code (countries endpoint only)."""


# ---------------------------------------------------------------------------
# Simple in-memory cache
# ---------------------------------------------------------------------------


@dataclass
class _CacheEntry:
    data: Any
    timestamp: float


class _SimpleCache:
    """TTL-based in-memory cache with max-entry eviction."""

    def __init__(self, ttl: float = _CACHE_TTL, max_entries: int = _CACHE_MAX_ENTRIES) -> None:
        self._ttl = ttl
        self._max = max_entries
        self._store: dict[str, _CacheEntry] = {}

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        if time.monotonic() - entry.timestamp > self._ttl:
            del self._store[key]
            return None
        return entry.data

    def put(self, key: str, data: Any) -> None:
        # Evict oldest if at capacity.
        if len(self._store) >= self._max and key not in self._store:
            oldest_key = min(self._store, key=lambda k: self._store[k].timestamp)
            del self._store[oldest_key]
        self._store[key] = _CacheEntry(data=data, timestamp=time.monotonic())

    def clear(self) -> None:
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)


# ---------------------------------------------------------------------------
# RadioBrowserClient
# ---------------------------------------------------------------------------


class RadioBrowserClient:
    """Async HTTP client for the radio-browser.info community API.

    Args:
        base_url: API base URL (default: de1 mirror).
        timeout: HTTP request timeout in seconds.
        cache_ttl: Browse-cache TTL in seconds.
    """

    def __init__(
        self,
        base_url: str = API_BASE,
        timeout: float = _READ_TIMEOUT,
        cache_ttl: float = _CACHE_TTL,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._cache = _SimpleCache(ttl=cache_ttl)
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        """Create the shared httpx client."""
        if self._client is not None and not self._client.is_closed:
            return
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(
                connect=_CONNECT_TIMEOUT,
                read=self._timeout,
                write=None,
                pool=None,
            ),
            follow_redirects=True,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
        )
        logger.info("RadioBrowserClient started (base=%s)", self._base_url)

    async def close(self) -> None:
        """Close the httpx client and clear cache."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._cache.clear()
        logger.debug("RadioBrowserClient closed")

    # -- Internal HTTP -------------------------------------------------------

    async def _get_json(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        use_cache: bool = True,
    ) -> Any:
        """Fetch a JSON endpoint.

        Args:
            path: URL path relative to base (e.g. ``/json/stations/search``).
            params: Query parameters or POST form data.
            use_cache: Cache GET results by (path, sorted params).

        Returns:
            Parsed JSON (list or dict).
        """
        cache_key = f"{path}|{sorted((params or {}).items())}"

        if use_cache:
            cached = self._cache.get(cache_key)
            if cached is not None:
                logger.debug("Cache hit: %s", path)
                return cached

        if self._client is None:
            await self.start()
        assert self._client is not None

        logger.debug("Fetching %s params=%s", path, params)

        # Use POST for search/complex queries (radio-browser recommends it),
        # GET for simple list endpoints.
        if params and any(k in params for k in ("name", "tag", "tagList", "country", "language")):
            resp = await self._client.post(path, data=params)
        else:
            resp = await self._client.get(path, params=params)

        resp.raise_for_status()
        data = resp.json()

        if use_cache:
            self._cache.put(cache_key, data)

        return data

    # -- Public API: Browse categories ---------------------------------------

    def get_browse_categories(self) -> list[dict[str, str]]:
        """Return the static top-level browse menu."""
        return list(BROWSE_CATEGORIES)

    async def get_countries(
        self,
        limit: int = _DEFAULT_LIMIT,
        offset: int = 0,
    ) -> list[CategoryEntry]:
        """Fetch countries with station counts.

        Returns countries sorted by station count (most stations first).
        """
        data = await self._get_json(
            "/json/countries",
            params={
                "order": "stationcount",
                "reverse": "true",
                "hidebroken": "true",
                "limit": str(min(limit, _MAX_LIMIT)),
                "offset": str(offset),
            },
        )
        return [
            CategoryEntry(
                name=item.get("name", ""),
                stationcount=int(item.get("stationcount", 0)),
                iso_3166_1=item.get("iso_3166_1", ""),
            )
            for item in data
            if int(item.get("stationcount", 0)) > 0
        ]

    async def get_tags(
        self,
        limit: int = _DEFAULT_LIMIT,
        offset: int = 0,
    ) -> list[CategoryEntry]:
        """Fetch tags (genres) with station counts.

        Returns tags sorted by station count (most popular first).
        """
        data = await self._get_json(
            "/json/tags",
            params={
                "order": "stationcount",
                "reverse": "true",
                "hidebroken": "true",
                "limit": str(min(limit, _MAX_LIMIT)),
                "offset": str(offset),
            },
        )
        return [
            CategoryEntry(
                name=item.get("name", ""),
                stationcount=int(item.get("stationcount", 0)),
            )
            for item in data
            if int(item.get("stationcount", 0)) > 0 and item.get("name", "").strip()
        ]

    async def get_languages(
        self,
        limit: int = _DEFAULT_LIMIT,
        offset: int = 0,
    ) -> list[CategoryEntry]:
        """Fetch languages with station counts.

        Returns languages sorted by station count (most popular first).
        """
        data = await self._get_json(
            "/json/languages",
            params={
                "order": "stationcount",
                "reverse": "true",
                "hidebroken": "true",
                "limit": str(min(limit, _MAX_LIMIT)),
                "offset": str(offset),
            },
        )
        return [
            CategoryEntry(
                name=item.get("name", ""),
                stationcount=int(item.get("stationcount", 0)),
            )
            for item in data
            if int(item.get("stationcount", 0)) > 0 and item.get("name", "").strip()
        ]

    # -- Public API: Station lists -------------------------------------------

    async def get_popular_stations(
        self,
        limit: int = _DEFAULT_LIMIT,
        offset: int = 0,
    ) -> list[RadioStation]:
        """Fetch stations sorted by vote count (most popular first)."""
        data = await self._get_json(
            "/json/stations/topvote",
            params={
                "hidebroken": "true",
                "limit": str(min(limit, _MAX_LIMIT)),
                "offset": str(offset),
            },
        )
        return [_parse_station(s) for s in data]

    async def get_trending_stations(
        self,
        limit: int = _DEFAULT_LIMIT,
        offset: int = 0,
    ) -> list[RadioStation]:
        """Fetch stations sorted by recent click count (trending now)."""
        data = await self._get_json(
            "/json/stations/topclick",
            params={
                "hidebroken": "true",
                "limit": str(min(limit, _MAX_LIMIT)),
                "offset": str(offset),
            },
            use_cache=False,  # Trending changes frequently.
        )
        return [_parse_station(s) for s in data]

    async def get_stations_by_country(
        self,
        countrycode: str,
        limit: int = _DEFAULT_LIMIT,
        offset: int = 0,
    ) -> list[RadioStation]:
        """Fetch stations for a given ISO 3166-1 alpha-2 country code."""
        data = await self._get_json(
            f"/json/stations/bycountrycodeexact/{countrycode.upper()}",
            params={
                "hidebroken": "true",
                "order": "votes",
                "reverse": "true",
                "limit": str(min(limit, _MAX_LIMIT)),
                "offset": str(offset),
            },
        )
        return [_parse_station(s) for s in data]

    async def get_stations_by_tag(
        self,
        tag: str,
        limit: int = _DEFAULT_LIMIT,
        offset: int = 0,
    ) -> list[RadioStation]:
        """Fetch stations matching an exact tag."""
        data = await self._get_json(
            f"/json/stations/bytagexact/{tag}",
            params={
                "hidebroken": "true",
                "order": "votes",
                "reverse": "true",
                "limit": str(min(limit, _MAX_LIMIT)),
                "offset": str(offset),
            },
        )
        return [_parse_station(s) for s in data]

    async def get_stations_by_language(
        self,
        language: str,
        limit: int = _DEFAULT_LIMIT,
        offset: int = 0,
    ) -> list[RadioStation]:
        """Fetch stations for a given language (exact match)."""
        data = await self._get_json(
            f"/json/stations/bylanguageexact/{language}",
            params={
                "hidebroken": "true",
                "order": "votes",
                "reverse": "true",
                "limit": str(min(limit, _MAX_LIMIT)),
                "offset": str(offset),
            },
        )
        return [_parse_station(s) for s in data]

    # -- Public API: Search --------------------------------------------------

    async def search(
        self,
        query: str,
        limit: int = _DEFAULT_LIMIT,
        offset: int = 0,
    ) -> list[RadioStation]:
        """Search stations by name.

        Args:
            query: Free-text search string (matched against station name).
            limit: Max results.
            offset: Pagination offset.

        Returns:
            List of matching stations sorted by votes (best first).
        """
        data = await self._get_json(
            "/json/stations/search",
            params={
                "name": query,
                "hidebroken": "true",
                "order": "votes",
                "reverse": "true",
                "limit": str(min(limit, _MAX_LIMIT)),
                "offset": str(offset),
            },
            use_cache=False,
        )
        return [_parse_station(s) for s in data]

    # -- Public API: Click counting ------------------------------------------

    async def count_click(self, stationuuid: str) -> bool:
        """Register a click/play for a station.

        Should be called when a user starts playing a station.  The API
        rate-limits to one click per IP per station per day.

        Args:
            stationuuid: The station's UUID.

        Returns:
            ``True`` if the click was counted, ``False`` otherwise.
        """
        if self._client is None:
            await self.start()
        assert self._client is not None

        try:
            resp = await self._client.get(f"/json/url/{stationuuid}")
            resp.raise_for_status()
            result = resp.json()
            ok = result.get("ok", "false")
            if ok == "true" or ok is True:
                logger.debug("Click counted for station %s", stationuuid)
                return True
            logger.debug("Click not counted for station %s: %s", stationuuid, result.get("message", ""))
            return False
        except Exception:
            logger.debug("Failed to count click for station %s", stationuuid, exc_info=True)
            return False

    # -- Public API: Station by UUID -----------------------------------------

    async def get_station_by_uuid(self, stationuuid: str) -> RadioStation | None:
        """Fetch a single station by its UUID."""
        if self._client is None:
            await self.start()
        assert self._client is not None

        try:
            resp = await self._client.post(
                "/json/stations/byuuid",
                data={"uuids": stationuuid},
            )
            resp.raise_for_status()
            data = resp.json()
            if data and len(data) > 0:
                return _parse_station(data[0])
            return None
        except Exception:
            logger.debug("Failed to fetch station %s", stationuuid, exc_info=True)
            return None

    # -- Cache management ----------------------------------------------------

    def clear_cache(self) -> None:
        """Clear the browse/category cache."""
        self._cache.clear()

    @property
    def cache_size(self) -> int:
        """Number of cached responses."""
        return len(self._cache)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_station(data: dict[str, Any]) -> RadioStation:
    """Parse a station JSON object into a RadioStation dataclass."""
    return RadioStation(
        stationuuid=str(data.get("stationuuid", "")),
        name=str(data.get("name", "")).strip(),
        url=str(data.get("url", "")),
        url_resolved=str(data.get("url_resolved", "") or data.get("url", "")),
        homepage=str(data.get("homepage", "")),
        favicon=str(data.get("favicon", "")),
        tags=str(data.get("tags", "")),
        country=str(data.get("country", "")),
        countrycode=str(data.get("countrycode", "")),
        state=str(data.get("state", "")),
        language=str(data.get("language", "")),
        codec=str(data.get("codec", "")),
        bitrate=_safe_int(data.get("bitrate", 0)),
        votes=_safe_int(data.get("votes", 0)),
        clickcount=_safe_int(data.get("clickcount", 0)),
        lastcheckok=_safe_int(data.get("lastcheckok", 1)),
        hls=_safe_int(data.get("hls", 0)),
        has_extended_info=bool(data.get("has_extended_info", False)),
    )


def _safe_int(value: Any) -> int:
    """Safely convert a value to int, defaulting to 0."""
    try:
        return int(value)
    except (ValueError, TypeError):
        return 0


def codec_to_content_type(codec: str) -> str:
    """Map a radio-browser codec string to an HTTP Content-Type.

    Args:
        codec: Codec string from radio-browser (e.g. ``"MP3"``, ``"AAC"``).

    Returns:
        MIME type string.
    """
    _map = {
        "mp3": "audio/mpeg",
        "aac": "audio/aac",
        "aac+": "audio/aac",
        "ogg": "audio/ogg",
        "wma": "audio/x-ms-wma",
        "flac": "audio/flac",
        "opus": "audio/opus",
        "unknown": "audio/mpeg",
    }
    return _map.get(codec.lower().strip(), "audio/mpeg")


def format_station_subtitle(station: RadioStation) -> str:
    """Build a human-readable subtitle for a station.

    Combines codec, bitrate, country, and tags into a concise string.

    Examples:
        ``"MP3 128kbps · Germany · jazz, blues"``
        ``"AAC 64kbps · France"``
    """
    parts: list[str] = []

    # Codec + bitrate
    if station.codec and station.bitrate:
        parts.append(f"{station.codec} {station.bitrate}kbps")
    elif station.codec:
        parts.append(station.codec)
    elif station.bitrate:
        parts.append(f"{station.bitrate}kbps")

    # Country
    if station.country:
        parts.append(station.country)

    # Tags (first 3)
    if station.tags:
        tag_list = [t.strip() for t in station.tags.split(",") if t.strip()]
        if tag_list:
            parts.append(", ".join(tag_list[:3]))

    return " · ".join(parts)
