"""
TuneIn OPML API client for the Radio plugin.

This module wraps the TuneIn/RadioTime OPML API used by LMS
(``Slim::Plugin::InternetRadio::TuneIn``) to provide:

* **Browse** — hierarchical category navigation (Local Radio, Music,
  News, Sports, Talk, By Location, By Language, Podcasts)
* **Search** — full-text station search
* **Tune** — resolve a station/show ID to a direct audio stream URL

The API is accessed via JSON (``&render=json``) rather than XML/OPML
to simplify parsing.  All HTTP calls use ``httpx`` (async).

Reference
~~~~~~~~~

LMS uses Partner ID 16 for TuneIn.  The OPML endpoints are:

* ``http://opml.radiotime.com/Index.aspx``  — root menu
* ``http://opml.radiotime.com/Browse.ashx`` — browse categories
* ``http://opml.radiotime.com/Search.ashx`` — search
* ``http://opml.radiotime.com/Tune.ashx``   — resolve stream URL

All responses share the same JSON envelope::

    {
      "head": {"title": "…", "status": "200"},
      "body": [ {outline}, … ]
    }

Each *outline* has ``element``, ``type`` (``"link"`` | ``"audio"`` |
``"search"``), ``text``, ``URL``, and optional metadata fields like
``image``, ``bitrate``, ``subtext``, ``guide_id``, ``formats``, etc.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (matching LMS Slim::Plugin::InternetRadio::TuneIn)
# ---------------------------------------------------------------------------

PARTNER_ID = 16

_BASE = "http://opml.radiotime.com"
INDEX_URL = f"{_BASE}/Index.aspx"
BROWSE_URL = f"{_BASE}/Browse.ashx"
SEARCH_URL = f"{_BASE}/Search.ashx"
TUNE_URL = f"{_BASE}/Tune.ashx"

# Default cache TTL for browse results (seconds).
_CACHE_TTL = 600  # 10 minutes — categories change rarely

# Maximum number of cached browse pages.
_CACHE_MAX_ENTRIES = 256

# HTTP request timeout (seconds).
_HTTP_TIMEOUT = 15.0


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TuneInItem:
    """A single item parsed from a TuneIn OPML JSON response.

    This is the "raw" representation before conversion to
    :class:`~resonance.content_provider.BrowseItem`.
    """

    text: str
    """Display text (station name, category, etc.)."""

    type: str = "link"
    """Item type: ``"link"`` (folder), ``"audio"`` (station), ``"search"``."""

    url: str = ""
    """URL for further navigation (Browse/Tune) or the search template."""

    guide_id: str = ""
    """TuneIn-internal identifier (e.g. ``"s31681"``, ``"c57944"``)."""

    key: str = ""
    """Semantic key (``"local"``, ``"music"``, ``"search"``, …)."""

    image: str = ""
    """Station logo or category icon URL."""

    bitrate: str = ""
    """Bitrate as string (e.g. ``"128"``)."""

    subtext: str = ""
    """Secondary text (e.g. now-playing info, description)."""

    formats: str = ""
    """Comma-separated format list (e.g. ``"mp3"``, ``"aac,mp3"``)."""

    is_container: bool = False
    """``True`` if this item groups children (section header)."""

    children: list[TuneInItem] = field(default_factory=list)
    """Inline children (for grouped sections like "Stations", "Shows")."""

    preset_id: str = ""
    """Preset/favorite ID (``s12345``, ``p12345``)."""

    playing: str = ""
    """Currently playing track info."""

    playing_image: str = ""
    """Album art for the currently playing track."""

    item_type: str = ""
    """TuneIn item kind: ``"station"``, ``"show"``, ``""``."""

    reliability: str = ""
    """Stream reliability score (0–100) as string."""


@dataclass(frozen=True, slots=True)
class TuneInStream:
    """Resolved stream information from a ``Tune.ashx`` call."""

    url: str
    """Direct audio stream URL."""

    bitrate: int = 0
    """Bitrate in kbps."""

    media_type: str = "mp3"
    """Audio format (``"mp3"``, ``"aac"``, ``"ogg"``, …)."""

    is_direct: bool = True
    """Whether *url* points directly at the audio (vs. a playlist)."""

    reliability: int = 0
    """Stream reliability (0–100)."""

    guide_id: str = ""
    """TuneIn internal ID for this stream variant."""

    is_hls: bool = False
    """``True`` if this is an HLS (m3u8) stream."""


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


@dataclass
class _CacheEntry:
    """Simple TTL cache entry."""

    data: Any
    expires_at: float


class _SimpleCache:
    """Bounded TTL cache for TuneIn browse responses."""

    def __init__(self, max_entries: int = _CACHE_MAX_ENTRIES, ttl: float = _CACHE_TTL) -> None:
        self._store: dict[str, _CacheEntry] = {}
        self._max_entries = max_entries
        self._ttl = ttl

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        if time.monotonic() > entry.expires_at:
            del self._store[key]
            return None
        return entry.data

    def put(self, key: str, data: Any, ttl: float | None = None) -> None:
        # Evict oldest if at capacity
        if len(self._store) >= self._max_entries:
            oldest_key = min(self._store, key=lambda k: self._store[k].expires_at)
            del self._store[oldest_key]
        self._store[key] = _CacheEntry(
            data=data,
            expires_at=time.monotonic() + (ttl if ttl is not None else self._ttl),
        )

    def clear(self) -> None:
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)


# ---------------------------------------------------------------------------
# TuneIn API Client
# ---------------------------------------------------------------------------


def _ensure_json(url: str) -> str:
    """Append ``render=json`` to a TuneIn URL if not already present."""
    if "render=json" in url:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}render=json"


def _ensure_partner_id(url: str) -> str:
    """Ensure ``partnerId`` is present in the URL."""
    if "partnerId=" in url:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}partnerId={PARTNER_ID}"


def _parse_outline(raw: dict[str, Any]) -> TuneInItem:
    """Parse a single TuneIn OPML outline dict into a :class:`TuneInItem`."""
    children_raw = raw.get("children", [])
    children = [_parse_outline(c) for c in children_raw] if children_raw else []

    # Section headers have children but no type
    item_type = raw.get("type", "")
    is_container = bool(children) and not item_type

    return TuneInItem(
        text=raw.get("text", ""),
        type=item_type or ("container" if is_container else "link"),
        url=raw.get("URL", ""),
        guide_id=raw.get("guide_id", ""),
        key=raw.get("key", ""),
        image=raw.get("image", ""),
        bitrate=str(raw.get("bitrate", "")),
        subtext=raw.get("subtext", ""),
        formats=raw.get("formats", ""),
        is_container=is_container,
        children=children,
        preset_id=raw.get("preset_id", ""),
        playing=raw.get("playing", ""),
        playing_image=raw.get("playing_image", ""),
        item_type=raw.get("item", ""),
        reliability=str(raw.get("reliability", "")),
    )


def _parse_body(body: list[dict[str, Any]]) -> list[TuneInItem]:
    """Parse the ``body`` array of a TuneIn JSON response."""
    return [_parse_outline(item) for item in body]


class TuneInClient:
    """Async HTTP client for the TuneIn OPML API.

    Args:
        partner_id: TuneIn partner ID (default: 16, same as LMS).
        timeout: HTTP request timeout in seconds.
        cache_ttl: Browse-cache TTL in seconds.
    """

    def __init__(
        self,
        partner_id: int = PARTNER_ID,
        timeout: float = _HTTP_TIMEOUT,
        cache_ttl: float = _CACHE_TTL,
    ) -> None:
        self.partner_id = partner_id
        self._timeout = timeout
        self._cache = _SimpleCache(ttl=cache_ttl)
        self._client: httpx.AsyncClient | None = None

    # -- Lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        """Create the underlying httpx client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
                headers={"User-Agent": "Resonance/1.0"},
            )

    async def close(self) -> None:
        """Close the underlying httpx client and clear caches."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._cache.clear()

    # -- Internal HTTP -------------------------------------------------------

    async def _fetch_json(self, url: str, *, use_cache: bool = True) -> dict[str, Any]:
        """Fetch a TuneIn URL and return the parsed JSON dict.

        Args:
            url: The TuneIn API URL (``render=json`` is added automatically).
            use_cache: If ``True``, cache GET results by URL.

        Returns:
            Parsed JSON response dict.

        Raises:
            httpx.HTTPError: On network/HTTP errors.
            ValueError: If the response is not valid JSON.
        """
        final_url = _ensure_json(_ensure_partner_id(url))

        # Check cache first
        if use_cache:
            cached = self._cache.get(final_url)
            if cached is not None:
                logger.debug("Cache hit for %s", final_url)
                return cached

        if self._client is None:
            await self.start()
        assert self._client is not None

        logger.debug("Fetching TuneIn URL: %s", final_url)
        response = await self._client.get(final_url)
        response.raise_for_status()

        data = response.json()

        if use_cache:
            self._cache.put(final_url, data)

        return data

    # -- Public API ----------------------------------------------------------

    async def fetch_root(self) -> list[TuneInItem]:
        """Fetch the TuneIn root menu (Index.aspx).

        Returns the top-level categories: Local Radio, Music, News,
        Sports, Talk, By Location, By Language, Podcasts, Search.
        """
        data = await self._fetch_json(INDEX_URL)
        body = data.get("body", [])
        return _parse_body(body)

    async def browse(self, url: str) -> list[TuneInItem]:
        """Browse a TuneIn category/sub-category by URL.

        Args:
            url: A TuneIn ``Browse.ashx`` (or similar) URL obtained from
                a previous browse result's :attr:`TuneInItem.url`.

        Returns:
            List of items at that level.
        """
        if not url:
            return await self.fetch_root()

        data = await self._fetch_json(url)
        body = data.get("body", [])
        return _parse_body(body)

    async def search(self, query: str) -> list[TuneInItem]:
        """Search TuneIn for stations/shows matching *query*.

        Args:
            query: Free-text search string.

        Returns:
            List of matching items.
        """
        url = f"{SEARCH_URL}?query={_url_encode_query(query)}"
        data = await self._fetch_json(url, use_cache=False)
        body = data.get("body", [])
        return _parse_body(body)

    async def tune(self, station_id: str) -> TuneInStream | None:
        """Resolve a station/show ID to a direct stream URL.

        Args:
            station_id: TuneIn guide ID (e.g. ``"s31681"``).

        Returns:
            A :class:`TuneInStream` with the direct URL, or ``None``
            if resolution fails.
        """
        url = f"{TUNE_URL}?id={station_id}"
        try:
            data = await self._fetch_json(url, use_cache=False)
        except Exception:
            logger.exception("Failed to tune station %s", station_id)
            return None

        body = data.get("body", [])
        if not body:
            logger.warning("Empty tune response for station %s", station_id)
            return None

        # The first element in the body is the primary stream.
        stream_data = body[0]
        stream_url = stream_data.get("url", "")
        if not stream_url:
            logger.warning("No stream URL in tune response for %s", station_id)
            return None

        media_type = stream_data.get("media_type", "mp3")
        is_hls = media_type in ("hls",) or stream_url.endswith(".m3u8")

        bitrate = 0
        try:
            bitrate = int(stream_data.get("bitrate", 0))
        except (ValueError, TypeError):
            pass

        reliability = 0
        try:
            reliability = int(stream_data.get("reliability", 0))
        except (ValueError, TypeError):
            pass

        stream = TuneInStream(
            url=stream_url,
            bitrate=bitrate,
            media_type=media_type,
            is_direct=bool(stream_data.get("is_direct", True)),
            reliability=reliability,
            guide_id=stream_data.get("guide_id", ""),
            is_hls=is_hls,
        )

        # Resolve playlist URLs (.m3u/.pls) to direct stream URLs.
        if not stream.is_direct or _is_playlist_url(stream.url):
            resolved = await self._resolve_playlist_url(stream.url)
            if resolved:
                logger.info(
                    "Resolved playlist URL to direct stream: %s -> %s",
                    stream.url, resolved,
                )
                stream = TuneInStream(
                    url=resolved,
                    bitrate=stream.bitrate,
                    media_type=stream.media_type,
                    is_direct=True,
                    reliability=stream.reliability,
                    guide_id=stream.guide_id,
                    is_hls=stream.is_hls,
                )
            else:
                logger.warning(
                    "Failed to resolve playlist URL %s — using as-is",
                    stream.url,
                )

        return stream

    async def tune_url(self, tune_url: str) -> TuneInStream | None:
        """Resolve a station by its full ``Tune.ashx`` URL.

        This is used when the browse result already contains the full
        tune URL (``http://opml.radiotime.com/Tune.ashx?id=…``).

        Args:
            tune_url: Full Tune.ashx URL.

        Returns:
            A :class:`TuneInStream` or ``None``.
        """
        try:
            data = await self._fetch_json(tune_url, use_cache=False)
        except Exception:
            logger.exception("Failed to tune URL %s", tune_url)
            return None

        body = data.get("body", [])
        if not body:
            return None

        stream_data = body[0]
        stream_url = stream_data.get("url", "")
        if not stream_url:
            return None

        media_type = stream_data.get("media_type", "mp3")
        is_hls = media_type in ("hls",) or stream_url.endswith(".m3u8")

        bitrate = 0
        try:
            bitrate = int(stream_data.get("bitrate", 0))
        except (ValueError, TypeError):
            pass

        reliability = 0
        try:
            reliability = int(stream_data.get("reliability", 0))
        except (ValueError, TypeError):
            pass

        stream = TuneInStream(
            url=stream_url,
            bitrate=bitrate,
            media_type=media_type,
            is_direct=bool(stream_data.get("is_direct", True)),
            reliability=reliability,
            guide_id=stream_data.get("guide_id", ""),
            is_hls=is_hls,
        )

        # Resolve playlist URLs (.m3u/.pls) to direct stream URLs.
        if not stream.is_direct or _is_playlist_url(stream.url):
            resolved = await self._resolve_playlist_url(stream.url)
            if resolved:
                logger.info(
                    "Resolved playlist URL to direct stream: %s -> %s",
                    stream.url, resolved,
                )
                stream = TuneInStream(
                    url=resolved,
                    bitrate=stream.bitrate,
                    media_type=stream.media_type,
                    is_direct=True,
                    reliability=stream.reliability,
                    guide_id=stream.guide_id,
                    is_hls=stream.is_hls,
                )
            else:
                logger.warning(
                    "Failed to resolve playlist URL %s — using as-is",
                    stream.url,
                )

        return stream

    # -- Playlist URL resolution ---------------------------------------------

    async def _resolve_playlist_url(self, url: str) -> str | None:
        """Resolve a playlist URL (.m3u/.pls) to a direct audio stream URL.

        TuneIn sometimes returns playlist files instead of direct stream URLs.
        LMS handles this in ``Slim::Plugin::RadioTime`` by following playlist
        redirects.  We do the same here: fetch the URL, detect if it's a
        playlist, and extract the first stream URL.

        Args:
            url: Potentially a playlist URL.

        Returns:
            The first stream URL found in the playlist, or ``None`` if
            resolution fails or the URL is already a direct stream.
        """
        if self._client is None:
            await self.start()
        assert self._client is not None

        try:
            resp = await self._client.get(url)
            resp.raise_for_status()
        except Exception:
            logger.debug("Failed to fetch playlist URL: %s", url)
            return None

        content_type = resp.headers.get("content-type", "").lower()
        body_text = resp.text.strip()

        # Detect by Content-Type or by content inspection
        if "audio/x-scpls" in content_type or "audio/scpls" in content_type or body_text.startswith("[playlist]"):
            return _parse_pls(body_text)
        elif (
            "audio/x-mpegurl" in content_type
            or "application/x-mpegurl" in content_type
            or "application/vnd.apple.mpegurl" in content_type
            or body_text.startswith("#EXTM3U")
            or body_text.startswith("#EXT")
        ):
            return _parse_m3u(body_text)
        elif body_text.startswith("http://") or body_text.startswith("https://"):
            # Plain text with a single URL (some TuneIn responses)
            first_line = body_text.split("\n")[0].strip()
            if first_line.startswith("http"):
                return first_line

        # If the content looks like audio (binary), the URL is already direct
        if "audio/" in content_type and "mpegurl" not in content_type and "scpls" not in content_type:
            return url

        logger.debug(
            "Could not detect playlist format for %s (content-type: %s, first 100 chars: %s)",
            url, content_type, body_text[:100],
        )
        return None

    def clear_cache(self) -> None:
        """Clear the browse cache."""
        self._cache.clear()

    @property
    def cache_size(self) -> int:
        """Number of cached browse responses."""
        return len(self._cache)


# ---------------------------------------------------------------------------
# Playlist URL detection and parsing
# ---------------------------------------------------------------------------

# File extensions and URL patterns that indicate a playlist (not a direct stream).
_PLAYLIST_EXTENSIONS = (".m3u", ".m3u8", ".pls", ".asx", ".xspf")


def _is_playlist_url(url: str) -> bool:
    """Heuristic: does this URL look like a playlist rather than a direct stream?"""
    lower = url.lower().split("?")[0]  # ignore query params
    return any(lower.endswith(ext) for ext in _PLAYLIST_EXTENSIONS)


def _parse_m3u(text: str) -> str | None:
    """Extract the first stream URL from M3U/M3U8 content.

    Skips comment lines (``#…``) and blank lines.

    Args:
        text: Raw M3U content.

    Returns:
        First HTTP(S) URL found, or ``None``.
    """
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("http://") or line.startswith("https://"):
            return line
    return None


def _parse_pls(text: str) -> str | None:
    """Extract the first stream URL from a PLS playlist file.

    PLS format::

        [playlist]
        File1=http://stream.example.com:8000/live
        Title1=Station Name
        Length1=-1
        NumberOfEntries=1
        Version=2

    Args:
        text: Raw PLS content.

    Returns:
        URL from ``File1=``, or the first ``FileN=`` URL found.
    """
    for line in text.splitlines():
        line = line.strip()
        # Match FileN= entries (case-insensitive)
        lower = line.lower()
        if lower.startswith("file") and "=" in line:
            _, _, url = line.partition("=")
            url = url.strip()
            if url.startswith("http://") or url.startswith("https://"):
                return url
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _url_encode_query(query: str) -> str:
    """URL-encode a search query for TuneIn.

    Uses ``+`` for spaces (form encoding), which is what TuneIn expects.
    """
    # urlencode with doseq produces key=value; we just want the value part.
    return urlencode({"q": query})[2:]  # strip "q="


def extract_station_id(url: str) -> str | None:
    """Extract a TuneIn station/show ID from a Tune.ashx URL.

    Examples::

        >>> extract_station_id("http://opml.radiotime.com/Tune.ashx?id=s31681&partnerId=16")
        's31681'
        >>> extract_station_id("http://opml.radiotime.com/Browse.ashx?c=music")
        None

    Args:
        url: A TuneIn URL.

    Returns:
        The ``id`` query parameter if present, else ``None``.
    """
    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        ids = params.get("id", [])
        return ids[0] if ids else None
    except Exception:
        return None


def is_tunein_url(url: str) -> bool:
    """Check if a URL points to the TuneIn/RadioTime API."""
    lower = url.lower()
    return "radiotime.com" in lower or "tunein.com" in lower


def is_tune_url(url: str) -> bool:
    """Check if a URL is a TuneIn ``Tune.ashx`` URL (needs stream resolution)."""
    lower = url.lower()
    return is_tunein_url(url) and "tune.ashx" in lower


def is_browse_url(url: str) -> bool:
    """Check if a URL is a TuneIn ``Browse.ashx`` URL."""
    lower = url.lower()
    return is_tunein_url(url) and "browse.ashx" in lower


def is_search_url(url: str) -> bool:
    """Check if a URL is a TuneIn ``Search.ashx`` URL."""
    lower = url.lower()
    return is_tunein_url(url) and "search.ashx" in lower


def content_type_for_media(media_type: str) -> str:
    """Map a TuneIn ``media_type`` to an HTTP Content-Type.

    Args:
        media_type: TuneIn media type string (``"mp3"``, ``"aac"``, ``"ogg"``, …).

    Returns:
        MIME type string.
    """
    _map = {
        "mp3": "audio/mpeg",
        "aac": "audio/aac",
        "ogg": "audio/ogg",
        "wma": "audio/x-ms-wma",
        "wmap": "audio/x-ms-wma",
        "wmvoice": "audio/x-ms-wma",
        "hls": "application/vnd.apple.mpegurl",
        "flac": "audio/flac",
    }
    return _map.get(media_type.lower(), "audio/mpeg")


def flatten_items(items: list[TuneInItem]) -> list[TuneInItem]:
    """Flatten container items by inlining their children.

    TuneIn sometimes returns grouped sections like::

        { "text": "Stations (26+)", "children": [...] }
        { "text": "Shows (6+)", "children": [...] }

    This function inlines those children so the caller gets a flat list.

    Args:
        items: Parsed TuneIn items (may contain containers).

    Returns:
        Flat list with containers replaced by their children.
    """
    result: list[TuneInItem] = []
    for item in items:
        if item.is_container and item.children:
            result.extend(item.children)
        else:
            result.append(item)
    return result
