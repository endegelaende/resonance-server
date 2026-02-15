"""
RSS Feed Parser for the Podcast Plugin.

Parses standard RSS 2.0 podcast feeds (with iTunes namespace extensions)
into typed dataclasses.  Also provides an async ``fetch_feed()`` helper
that downloads and parses a feed URL in one call.

Supported elements
~~~~~~~~~~~~~~~~~~

* ``<channel>`` — title, link, description, author, language, image
* ``<item>`` — title, enclosure (url/type/length), guid, pubDate,
  duration (``<itunes:duration>``), description/summary, image
* iTunes namespace (``http://www.itunes.com/dtds/podcast-1.0.dtd``)

LMS Reference
~~~~~~~~~~~~~

``Slim::Plugin::Podcast::Parser`` — RSS parsing with duration conversion,
resume-position injection, and ``podcast://`` URL wrapping.  We handle
resume separately in ``store.py`` and keep parsing pure.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# iTunes namespace — used by virtually all podcast feeds
# ---------------------------------------------------------------------------

_NS_ITUNES = "http://www.itunes.com/dtds/podcast-1.0.dtd"
_NS_CONTENT = "http://purl.org/rss/1.0/modules/content/"
_NS_ATOM = "http://www.w3.org/2005/Atom"

# Pre-register common namespaces so ET doesn't invent ns0/ns1 prefixes
# (only matters if we ever serialise, but good practice).
for _prefix, _uri in [
    ("itunes", _NS_ITUNES),
    ("content", _NS_CONTENT),
    ("atom", _NS_ATOM),
]:
    ET.register_namespace(_prefix, _uri)


# ============================================================================
# Data classes
# ============================================================================


@dataclass(frozen=True, slots=True)
class PodcastEpisode:
    """A single episode parsed from an RSS ``<item>``."""

    title: str
    """Episode title."""

    url: str
    """Direct audio URL (from ``<enclosure url="…">``).  Empty string if
    the item has no enclosure (non-audio items are filtered out by
    :func:`parse_feed`)."""

    guid: str = ""
    """Globally unique identifier.  Falls back to *url* when ``<guid>``
    is missing."""

    description: str = ""
    """Episode description / show notes (plain text, HTML stripped)."""

    published: str = ""
    """Publication date as ISO-8601 string (UTC).  Empty when unparseable."""

    published_epoch: float = 0.0
    """Publication date as Unix timestamp (for sorting).  ``0`` when
    unparseable."""

    duration_seconds: int = 0
    """Duration in whole seconds.  ``0`` when unknown."""

    content_type: str = "audio/mpeg"
    """MIME type from the ``<enclosure type="…">`` attribute."""

    file_size: int = 0
    """File size in bytes from ``<enclosure length="…">``."""

    image_url: str = ""
    """Per-episode artwork (``<itunes:image href="…">``).  Falls back to
    the feed-level image when empty."""

    explicit: bool = False
    """Whether the episode is marked explicit."""

    episode_number: int = 0
    """Episode number (``<itunes:episode>``), ``0`` if absent."""

    season_number: int = 0
    """Season number (``<itunes:season>``), ``0`` if absent."""


@dataclass(frozen=True, slots=True)
class PodcastFeed:
    """A parsed podcast feed (channel-level metadata + episodes)."""

    title: str
    """Podcast / show title."""

    url: str
    """Feed URL (the URL that was fetched)."""

    description: str = ""
    """Show description."""

    author: str = ""
    """Author or creator name."""

    language: str = ""
    """Language code (e.g. ``"en"``, ``"de"``)."""

    image_url: str = ""
    """Show artwork URL."""

    link: str = ""
    """Website link."""

    categories: list[str] = field(default_factory=list)
    """iTunes categories."""

    explicit: bool = False
    """Whether the feed is marked explicit."""

    episodes: list[PodcastEpisode] = field(default_factory=list)
    """Parsed episodes, sorted newest-first by publication date."""


# ============================================================================
# Duration parsing
# ============================================================================


def parse_duration(raw: str) -> int:
    """Parse a podcast duration string into seconds.

    Accepted formats (matching LMS ``Parser.pm`` behaviour):

    * ``"3661"`` — plain seconds
    * ``"54:23"`` — ``MM:SS``
    * ``"1:02:03"`` — ``H:MM:SS``
    * ``"00:54:23"`` — ``HH:MM:SS`` (leading zero)
    * ``"1h 2m 3s"`` — human-readable (bonus, some feeds use this)

    Returns 0 for unparseable or empty input.
    """
    if not raw or not raw.strip():
        return 0

    raw = raw.strip()

    # Plain integer seconds
    if raw.isdigit():
        return int(raw)

    # HH:MM:SS or MM:SS
    parts = raw.split(":")
    if len(parts) >= 2:
        try:
            parts_int = [int(p) for p in parts]
            if len(parts) == 3:
                return parts_int[0] * 3600 + parts_int[1] * 60 + parts_int[2]
            elif len(parts) == 2:
                return parts_int[0] * 60 + parts_int[1]
        except (ValueError, IndexError):
            pass

    # Human-readable: "1h 2m 3s", "45m", "1h30m", etc.
    total = 0
    m = re.findall(r"(\d+)\s*h", raw, re.IGNORECASE)
    if m:
        total += int(m[0]) * 3600
    m = re.findall(r"(\d+)\s*m", raw, re.IGNORECASE)
    if m:
        total += int(m[0]) * 60
    m = re.findall(r"(\d+)\s*s", raw, re.IGNORECASE)
    if m:
        total += int(m[0])
    if total > 0:
        return total

    # Last resort: try float seconds
    try:
        return int(float(raw))
    except (ValueError, TypeError):
        return 0


# ============================================================================
# Date parsing
# ============================================================================


def parse_pub_date(raw: str) -> tuple[str, float]:
    """Parse an RSS ``<pubDate>`` into (ISO-8601 string, epoch float).

    Returns ``("", 0.0)`` on failure.
    """
    if not raw or not raw.strip():
        return ("", 0.0)

    raw = raw.strip()

    try:
        dt = parsedate_to_datetime(raw)
        # Normalise to UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return (dt.isoformat(), dt.timestamp())
    except Exception:
        pass

    # Fallback: try ISO-8601 directly
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (dt.isoformat(), dt.timestamp())
        except ValueError:
            continue

    logger.debug("Unparseable pubDate: %r", raw)
    return ("", 0.0)


# ============================================================================
# HTML stripping
# ============================================================================


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def strip_html(text: str) -> str:
    """Remove HTML tags and collapse whitespace."""
    if not text:
        return ""
    text = _HTML_TAG_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text)
    return text.strip()


# ============================================================================
# XML parsing
# ============================================================================


def _find_text(element: ET.Element, tag: str, default: str = "") -> str:
    """Find text content of a child element, with namespace fallback."""
    # Try plain tag first
    child = element.find(tag)
    if child is not None and child.text:
        return child.text.strip()
    return default


def _find_itunes(element: ET.Element, tag: str, default: str = "") -> str:
    """Find text in an iTunes-namespaced child element."""
    child = element.find(f"{{{_NS_ITUNES}}}{tag}")
    if child is not None and child.text:
        return child.text.strip()
    return default


def _find_itunes_attr(element: ET.Element, tag: str, attr: str, default: str = "") -> str:
    """Find an attribute on an iTunes-namespaced child element."""
    child = element.find(f"{{{_NS_ITUNES}}}{tag}")
    if child is not None:
        return child.get(attr, default)
    return default


def _parse_image_url(channel: ET.Element) -> str:
    """Extract the best image URL from a channel element.

    Priority:
    1. ``<itunes:image href="…">``
    2. ``<image><url>…</url></image>``
    3. ``<media:thumbnail url="…">`` (less common)
    """
    # iTunes image (most common for podcasts)
    href = _find_itunes_attr(channel, "image", "href")
    if href:
        return href

    # Standard RSS image
    image_el = channel.find("image")
    if image_el is not None:
        url_el = image_el.find("url")
        if url_el is not None and url_el.text:
            return url_el.text.strip()

    return ""


def _is_explicit(element: ET.Element) -> bool:
    """Check if ``<itunes:explicit>`` is set to a truthy value."""
    val = _find_itunes(element, "explicit").lower()
    return val in ("yes", "true", "1", "explicit")


def _parse_int(raw: str, default: int = 0) -> int:
    """Safely parse an integer string."""
    if not raw:
        return default
    try:
        return int(raw)
    except (ValueError, TypeError):
        return default


def _parse_episode(item: ET.Element, feed_image: str = "") -> PodcastEpisode | None:
    """Parse a single ``<item>`` element into a :class:`PodcastEpisode`.

    Returns ``None`` if the item has no ``<enclosure>`` (non-audio items
    like text-only show notes).
    """
    # Enclosure is required — skip items without one
    enclosure = item.find("enclosure")
    if enclosure is None:
        return None

    enc_url = enclosure.get("url", "")
    if not enc_url:
        return None

    enc_type = enclosure.get("type", "audio/mpeg")
    enc_length = _parse_int(enclosure.get("length", "0"))

    title = _find_text(item, "title") or _find_itunes(item, "title")

    # GUID: prefer <guid>, fall back to enclosure URL
    guid = _find_text(item, "guid") or enc_url

    # Description: prefer <itunes:summary>, then <description>, then <content:encoded>
    description = _find_itunes(item, "summary")
    if not description:
        description = _find_text(item, "description")
    if not description:
        content_el = item.find(f"{{{_NS_CONTENT}}}encoded")
        if content_el is not None and content_el.text:
            description = content_el.text.strip()
    description = strip_html(description)

    # Truncate very long descriptions
    if len(description) > 2000:
        description = description[:1997] + "..."

    # Publication date
    pub_iso, pub_epoch = parse_pub_date(_find_text(item, "pubDate"))

    # Duration
    duration_raw = _find_itunes(item, "duration")
    duration_seconds = parse_duration(duration_raw)

    # Per-episode image
    image_url = _find_itunes_attr(item, "image", "href") or feed_image

    # Episode/season numbers
    episode_number = _parse_int(_find_itunes(item, "episode"))
    season_number = _parse_int(_find_itunes(item, "season"))

    return PodcastEpisode(
        title=title or "Untitled Episode",
        url=enc_url,
        guid=guid,
        description=description,
        published=pub_iso,
        published_epoch=pub_epoch,
        duration_seconds=duration_seconds,
        content_type=enc_type,
        file_size=enc_length,
        image_url=image_url,
        explicit=_is_explicit(item),
        episode_number=episode_number,
        season_number=season_number,
    )


def parse_feed(xml_content: str, feed_url: str = "") -> PodcastFeed:
    """Parse an RSS 2.0 XML string into a :class:`PodcastFeed`.

    Args:
        xml_content: Raw XML content of the feed.
        feed_url: The URL the feed was fetched from (stored in the result
            for reference; not extracted from the XML itself).

    Returns:
        A :class:`PodcastFeed` with channel metadata and episodes sorted
        newest-first.

    Raises:
        ET.ParseError: If the XML is malformed.
        ValueError: If the XML has no ``<channel>`` element.
    """
    root = ET.fromstring(xml_content)

    # RSS 2.0: <rss><channel>…</channel></rss>
    channel = root.find("channel")
    if channel is None:
        # Some feeds omit the <rss> wrapper or use Atom
        channel = root.find(f"{{{_NS_ATOM}}}feed")
    if channel is None:
        # Try the root itself (malformed but seen in the wild)
        if root.tag == "channel":
            channel = root
        else:
            raise ValueError("No <channel> element found in RSS feed")

    title = _find_text(channel, "title")
    description = strip_html(
        _find_itunes(channel, "summary")
        or _find_text(channel, "description")
    )
    author = (
        _find_itunes(channel, "author")
        or _find_itunes(channel, "owner")
        or _find_text(channel, "managingEditor")
    )
    language = _find_text(channel, "language")
    link = _find_text(channel, "link")
    image_url = _parse_image_url(channel)

    # Categories
    categories: list[str] = []
    for cat_el in channel.findall(f"{{{_NS_ITUNES}}}category"):
        cat_text = cat_el.get("text", "")
        if cat_text:
            categories.append(cat_text)
            # Sub-categories
            for sub_el in cat_el.findall(f"{{{_NS_ITUNES}}}category"):
                sub_text = sub_el.get("text", "")
                if sub_text:
                    categories.append(f"{cat_text} > {sub_text}")

    # Episodes
    episodes: list[PodcastEpisode] = []
    for item in channel.findall("item"):
        episode = _parse_episode(item, feed_image=image_url)
        if episode is not None:
            episodes.append(episode)

    # Sort episodes newest-first (by publication date)
    episodes.sort(key=lambda ep: ep.published_epoch, reverse=True)

    return PodcastFeed(
        title=title or "Untitled Podcast",
        url=feed_url,
        description=description,
        author=author,
        language=language,
        image_url=image_url,
        link=link,
        categories=categories,
        explicit=_is_explicit(channel),
        episodes=episodes,
    )


# ============================================================================
# Async feed fetching
# ============================================================================


async def fetch_feed(
    url: str,
    *,
    timeout: float = 15.0,
    client: Any | None = None,
) -> PodcastFeed:
    """Download and parse a podcast RSS feed.

    Args:
        url: Feed URL (HTTP or HTTPS).
        timeout: Request timeout in seconds.
        client: Optional ``httpx.AsyncClient`` to reuse.  When ``None``,
            a temporary client is created and closed after the request.

    Returns:
        Parsed :class:`PodcastFeed`.

    Raises:
        httpx.HTTPStatusError: On HTTP error responses.
        ET.ParseError: On malformed XML.
        ValueError: On missing ``<channel>`` element.
    """
    import httpx

    headers = {
        "User-Agent": "Resonance/1.0 (Podcast Plugin)",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    }

    close_after = False
    if client is None:
        client = httpx.AsyncClient(follow_redirects=True, timeout=timeout)
        close_after = True

    try:
        response = await client.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        xml_content = response.text
    finally:
        if close_after:
            await client.aclose()

    return parse_feed(xml_content, feed_url=url)


def format_duration(seconds: int) -> str:
    """Format seconds into a human-readable duration string.

    Examples::

        >>> format_duration(0)
        ''
        >>> format_duration(65)
        '1:05'
        >>> format_duration(3661)
        '1:01:01'

    Used for display in Jive menus and CLI output.
    """
    if seconds <= 0:
        return ""

    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60

    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    else:
        return f"{m}:{s:02d}"
