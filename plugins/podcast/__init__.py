"""
Podcast Plugin for Resonance.

Browse, search, subscribe to, and stream podcast episodes with resume
support and full Jive menu integration.  Uses the Content Provider
Phase 2 infrastructure for URL-proxied streaming to Squeezebox hardware.

Architecture
~~~~~~~~~~~~

* **feed_parser.py** parses RSS 2.0 feeds (with iTunes namespace) into
  typed dataclasses.
* **store.py** provides JSON-backed persistence for subscriptions,
  resume positions, and recently played episodes.
* **PodcastProvider** implements :class:`~resonance.content_provider.ContentProvider`
  so the server's generic content infrastructure can resolve streams.
* JSON-RPC commands provide the Jive-compatible menu interface:

  - ``podcast items <start> <count>`` — browse subscribed feeds / episodes
  - ``podcast search <start> <count>`` — search PodcastIndex
  - ``podcast play`` — play/add/insert an episode
  - ``podcast addshow`` — subscribe to a feed
  - ``podcast delshow`` — unsubscribe from a feed

Menu entry
~~~~~~~~~~

A top-level **"Podcasts"** node appears in the Jive home menu (weight 50,
between "Radio" at 45 and "Favorites" at 55).

LMS Reference
~~~~~~~~~~~~~~

``Slim::Plugin::Podcast::Plugin`` + ``Parser.pm`` + ``ProtocolHandler.pm``
+ ``PodcastIndex.pm`` + ``GPodder.pm``
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from resonance.content_provider import ContentProvider as ContentProviderBase
    from resonance.core.events import Event
    from resonance.plugin import PluginContext
    from resonance.web.handlers import CommandContext

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level state (set during setup, cleared during teardown)
# ---------------------------------------------------------------------------

_store: Any | None = None  # PodcastStore instance
_http_client: Any | None = None  # httpx.AsyncClient (shared)
_event_bus: Any | None = None  # EventBus reference
_provider: Any | None = None  # PodcastProvider instance

# Simple in-memory cache for parsed feeds {feed_url: (PodcastFeed, expire_ts)}
_feed_cache: dict[str, tuple[Any, float]] = {}
_FEED_CACHE_TTL = 600  # 10 minutes, matches Radio plugin

# PodcastIndex API configuration
_PODCASTINDEX_BASE = "https://api.podcastindex.org/api/1.0"
_PODCASTINDEX_KEY = "YPBFNRQ3GAFCFKWN2WXB"
_PODCASTINDEX_SECRET = "FN$5xQbVcPjvEU#gBsK4S$wrtHzrG7HFc3Nex#Eq"
_PODCASTINDEX_USER_AGENT = "Resonance/1.0 (Podcast Plugin)"


# ---------------------------------------------------------------------------
# Feed cache helpers
# ---------------------------------------------------------------------------


async def _get_feed(feed_url: str) -> Any:
    """Fetch and parse a podcast feed with caching."""
    now = time.time()

    # Check cache
    if feed_url in _feed_cache:
        feed, expires = _feed_cache[feed_url]
        if now < expires:
            return feed

    from .feed_parser import fetch_feed

    feed = await fetch_feed(feed_url, client=_http_client)
    _feed_cache[feed_url] = (feed, now + _FEED_CACHE_TTL)

    # Update subscription metadata if we have a store
    if _store is not None and _store.is_subscribed(feed_url):
        _store.update_subscription(
            feed_url,
            name=feed.title or None,
            image=feed.image_url or None,
            author=feed.author or None,
            description=feed.description[:200] if feed.description else None,
        )

    return feed


def _clear_feed_cache() -> None:
    """Clear the in-memory feed cache."""
    _feed_cache.clear()


# ---------------------------------------------------------------------------
# PodcastIndex search
# ---------------------------------------------------------------------------


def _podcastindex_headers() -> dict[str, str]:
    """Build authentication headers for PodcastIndex API.

    PodcastIndex uses a simple auth scheme: SHA-1 hash of
    (api_key + api_secret + unix_timestamp).
    """
    auth_time = str(int(time.time()))
    auth_hash = hashlib.sha1(
        (_PODCASTINDEX_KEY + _PODCASTINDEX_SECRET + auth_time).encode()
    ).hexdigest()

    return {
        "User-Agent": _PODCASTINDEX_USER_AGENT,
        "X-Auth-Key": _PODCASTINDEX_KEY,
        "X-Auth-Date": auth_time,
        "Authorization": auth_hash,
    }


async def _search_podcastindex(query: str) -> list[dict[str, Any]]:
    """Search PodcastIndex for podcasts matching *query*.

    Returns a list of dicts with keys: name, url, image, description, author.
    """
    if _http_client is None:
        return []

    import urllib.parse

    url = f"{_PODCASTINDEX_BASE}/search/byterm?q={urllib.parse.quote(query)}"
    headers = _podcastindex_headers()

    try:
        response = await _http_client.get(url, headers=headers, timeout=10.0)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        logger.warning("PodcastIndex search failed: %s", exc)
        return []

    feeds = data.get("feeds", [])
    if not isinstance(feeds, list):
        return []

    results: list[dict[str, Any]] = []
    for feed in feeds:
        if not isinstance(feed, dict):
            continue

        feed_url = feed.get("url", "")
        if not feed_url:
            continue

        # Prefer artwork over image
        image = ""
        for key in ("artwork", "image"):
            if feed.get(key):
                image = feed[key]
                break

        results.append({
            "name": feed.get("title", ""),
            "url": feed_url,
            "image": image,
            "description": feed.get("description", ""),
            "author": feed.get("author", ""),
            "language": feed.get("language", ""),
        })

    return results


# ---------------------------------------------------------------------------
# ContentProvider implementation
# ---------------------------------------------------------------------------


class PodcastProvider:
    """ContentProvider that wraps podcast RSS feeds.

    Registered under ``"podcast"`` via ``PluginContext.register_content_provider()``.
    """

    @property
    def name(self) -> str:
        return "Podcasts"

    @property
    def icon(self) -> str | None:
        return None

    async def browse(self, path: str = "") -> list[Any]:
        """Browse subscribed podcasts or episodes within a feed.

        *path* is either empty (list subscriptions) or a feed URL
        (list episodes).
        """
        from resonance.content_provider import BrowseItem

        if _store is None:
            return []

        if not path:
            # Root level: list subscriptions + search + recently played
            items: list[BrowseItem] = []

            # Search entry
            items.append(BrowseItem(
                id="search",
                title="Search Podcasts",
                type="search",
            ))

            # Recently played
            if _store.recent_count > 0:
                items.append(BrowseItem(
                    id="__recent__",
                    title="Recently Played",
                    type="folder",
                    subtitle=f"{_store.recent_count} episodes",
                ))

            # Subscribed feeds
            for sub in _store.subscriptions:
                items.append(BrowseItem(
                    id=sub.url,
                    title=sub.name,
                    type="folder",
                    url=sub.url,
                    icon=sub.image or None,
                    subtitle=sub.author or None,
                ))

            return items

        if path == "__recent__":
            # Recently played episodes
            items = []
            for ep in _store.recent:
                subtitle_parts = []
                if ep.show:
                    subtitle_parts.append(ep.show)

                items.append(BrowseItem(
                    id=ep.url,
                    title=ep.title or ep.url,
                    type="audio",
                    url=ep.url,
                    icon=ep.image or None,
                    subtitle=" — ".join(subtitle_parts) if subtitle_parts else None,
                    extra={"feed_url": ep.feed_url, "duration": ep.duration},
                ))
            return items

        # Feed URL: list episodes
        try:
            feed = await _get_feed(path)
        except Exception as exc:
            logger.warning("Failed to fetch feed %s: %s", path, exc)
            return []

        items = []
        for ep in feed.episodes:
            from .feed_parser import format_duration

            subtitle_parts = []
            if ep.published:
                # Show just the date part
                date_str = ep.published[:10] if len(ep.published) >= 10 else ep.published
                subtitle_parts.append(date_str)
            if ep.duration_seconds:
                subtitle_parts.append(format_duration(ep.duration_seconds))

            # Check for resume position
            resume_pos = _store.get_resume_position(ep.url) if _store else 0
            if resume_pos > 0:
                from .feed_parser import format_duration as fmt_dur
                subtitle_parts.append(f"from {fmt_dur(resume_pos)}")

            items.append(BrowseItem(
                id=ep.guid or ep.url,
                title=ep.title,
                type="audio",
                url=ep.url,
                icon=ep.image_url or feed.image_url or None,
                subtitle=" · ".join(subtitle_parts) if subtitle_parts else None,
                extra={
                    "duration": ep.duration_seconds,
                    "content_type": ep.content_type,
                    "feed_url": path,
                    "feed_title": feed.title,
                    "feed_image": feed.image_url,
                },
            ))

        return items

    async def search(self, query: str) -> list[Any]:
        """Search PodcastIndex for podcasts matching *query*."""
        from resonance.content_provider import BrowseItem

        results = await _search_podcastindex(query)

        items: list[BrowseItem] = []
        for r in results:
            items.append(BrowseItem(
                id=r["url"],
                title=r["name"],
                type="folder",
                url=r["url"],
                icon=r.get("image") or None,
                subtitle=r.get("author") or r.get("description", "")[:100] or None,
            ))

        return items

    async def get_stream_info(self, item_id: str) -> Any | None:
        """Resolve an episode URL/GUID to stream info.

        For podcasts, the episode URL *is* the stream URL — no additional
        resolution step needed (unlike radio stations which need Tune.ashx).
        """
        from resonance.content_provider import StreamInfo

        # item_id could be a GUID or URL; we treat it as URL
        if not item_id.startswith("http"):
            return None

        return StreamInfo(
            url=item_id,
            content_type="audio/mpeg",
            is_live=False,
        )

    async def on_stream_started(self, item_id: str, player_mac: str) -> None:
        logger.info(
            "Podcast stream started: episode=%s player=%s", item_id, player_mac
        )

    async def on_stream_stopped(self, item_id: str, player_mac: str) -> None:
        logger.debug(
            "Podcast stream stopped: episode=%s player=%s", item_id, player_mac
        )


# ---------------------------------------------------------------------------
# Helpers — parameter parsing
# ---------------------------------------------------------------------------


def _parse_tagged(command: list[Any], start: int = 1) -> dict[str, str]:
    """Parse ``key:value`` tagged params from *command* starting at *start*.

    Also handles ``dict`` elements that some clients (Cometd) send as
    inline objects.
    """
    result: dict[str, str] = {}
    for arg in command[start:]:
        if isinstance(arg, dict):
            for k, v in arg.items():
                if v is not None:
                    result[str(k)] = str(v)
        elif isinstance(arg, str) and ":" in arg:
            key, value = arg.split(":", 1)
            result[key] = value
    return result


def _parse_start_count(command: list[Any], sub_offset: int = 2) -> tuple[int, int]:
    """Extract ``(start, count)`` from positional args after the sub-command."""
    start = 0
    count = 200

    if len(command) > sub_offset:
        try:
            start = max(0, int(command[sub_offset]))
        except (ValueError, TypeError):
            pass

    if len(command) > sub_offset + 1:
        try:
            count = max(0, min(int(command[sub_offset + 1]), 10_000))
        except (ValueError, TypeError):
            pass

    return start, count


# ---------------------------------------------------------------------------
# Plugin lifecycle
# ---------------------------------------------------------------------------


async def setup(ctx: PluginContext) -> None:
    """Called by PluginManager during server startup."""
    global _store, _http_client, _event_bus, _provider

    import httpx

    from .store import PodcastStore

    _http_client = httpx.AsyncClient(follow_redirects=True, timeout=15.0)

    _store = PodcastStore(ctx.ensure_data_dir())
    _store.load()

    _event_bus = ctx.event_bus
    _provider = PodcastProvider()

    # ── Register content provider ──────────────────────────────
    ctx.register_content_provider("podcast", _provider)

    # ── Commands ───────────────────────────────────────────────
    ctx.register_command("podcast", cmd_podcast)

    # ── Jive main-menu: "Podcasts" node ────────────────────────
    # Weight 50 places it between "Radio" (45) and "Favorites" (55).
    ctx.register_menu_node(
        node_id="podcasts",
        parent="home",
        text="Podcasts",
        weight=50,
        actions={
            "go": {
                "cmd": ["podcast", "items"],
                "params": {"menu": 1},
            },
        },
        window={"titleStyle": "album"},
    )

    logger.info(
        "Podcast plugin started (%d subscriptions, %d resume positions)",
        _store.subscription_count,
        len(_store.resume_positions),
    )


async def teardown(ctx: PluginContext) -> None:
    """Called by PluginManager during server shutdown."""
    global _store, _http_client, _event_bus, _provider

    if _store is not None:
        _store.save()

    if _http_client is not None:
        await _http_client.aclose()

    _clear_feed_cache()
    _store = None
    _http_client = None
    _event_bus = None
    _provider = None

    logger.info("Podcast plugin stopped")


# ---------------------------------------------------------------------------
# JSON-RPC command dispatch
# ---------------------------------------------------------------------------


async def cmd_podcast(
    ctx: CommandContext, command: list[Any]
) -> dict[str, Any]:
    """Dispatch ``podcast <sub-command> …`` to the appropriate handler.

    Sub-commands:
    - ``items``    — browse subscribed feeds / episodes / recently played
    - ``search``   — search PodcastIndex
    - ``play``     — play/add/insert an episode
    - ``addshow``  — subscribe to a feed
    - ``delshow``  — unsubscribe from a feed
    """
    if _store is None:
        return {"error": "Podcast plugin not initialized"}

    sub = str(command[1]).lower() if len(command) > 1 else "items"

    match sub:
        case "items":
            return await _podcast_items(ctx, command)
        case "search":
            return await _podcast_search(ctx, command)
        case "play":
            return await _podcast_play(ctx, command)
        case "addshow":
            return await _podcast_addshow(ctx, command)
        case "delshow":
            return await _podcast_delshow(ctx, command)
        case _:
            return {"error": f"Unknown podcast sub-command: {sub}"}


# ---------------------------------------------------------------------------
# podcast items — browse
# ---------------------------------------------------------------------------


async def _podcast_items(
    ctx: CommandContext, command: list[Any]
) -> dict[str, Any]:
    """Handle ``podcast items <start> <count> [url:…] [menu:1] [search:…]``.

    Without ``url``, returns the top-level menu (search, recently played,
    subscriptions).  With ``url``, returns episodes for that feed.
    """
    assert _store is not None

    tagged = _parse_tagged(command, start=2)
    start, count = _parse_start_count(command)
    browse_url = tagged.get("url", "")
    is_menu = tagged.get("menu") == "1"
    search_query = tagged.get("search", "")

    if search_query:
        # Inline search — delegate to PodcastIndex
        results = await _search_podcastindex(search_query)
        all_items: list[dict[str, Any]] = []
        for r in results:
            if is_menu:
                all_items.append(_build_jive_feed_item(r))
            else:
                all_items.append(_build_cli_item_from_search(r))

        total = len(all_items)
        page = all_items[start: start + count]

        result: dict[str, Any] = {"count": total, "offset": start}
        result["item_loop" if is_menu else "loop"] = page
        if is_menu:
            result["base"] = _base_actions()
        return result

    if browse_url == "__recent__":
        # Recently played episodes
        recent_items: list[dict[str, Any]] = []
        for ep in _store.recent:
            if is_menu:
                recent_items.append(_build_jive_recent_item(ep))
            else:
                recent_items.append({
                    "name": ep.title or ep.url,
                    "url": ep.url,
                    "type": "audio",
                    "show": ep.show,
                    "image": ep.image,
                })

        total = len(recent_items)
        page = recent_items[start: start + count]

        result = {"count": total, "offset": start}
        result["item_loop" if is_menu else "loop"] = page
        if is_menu:
            result["base"] = _base_actions()
        return result

    if browse_url:
        # Browse a specific feed — list episodes
        try:
            feed = await _get_feed(browse_url)
        except Exception as exc:
            logger.warning("Failed to fetch feed %s: %s", browse_url, exc)
            error_items = [{"text": f"Failed to load feed: {exc}"}] if is_menu else []
            result = {"count": 0, "offset": 0}
            result["item_loop" if is_menu else "loop"] = error_items
            return result

        all_items = []
        for ep in feed.episodes:
            if is_menu:
                all_items.append(_build_jive_episode_item(
                    ep, feed_url=browse_url, feed_title=feed.title,
                    feed_image=feed.image_url,
                ))
            else:
                all_items.append(_build_cli_episode_item(ep, feed_url=browse_url))

        total = len(all_items)
        page = all_items[start: start + count]

        result = {"count": total, "offset": start}
        result["item_loop" if is_menu else "loop"] = page
        if is_menu:
            result["base"] = _base_actions()
        return result

    # Root menu: search + recently played + subscriptions
    all_items = []

    if is_menu:
        # Search entry
        all_items.append({
            "text": "Search Podcasts",
            "hasitems": 1,
            "actions": {
                "go": {
                    "cmd": ["podcast", "items"],
                    "params": {"menu": 1},
                    "itemsParams": "params",
                },
            },
            "input": {
                "len": 1,
                "processingPopup": {"text": "Searching..."},
                "help": {"text": "Enter search text"},
            },
        })
        all_items[-1]["actions"]["go"]["params"]["search"] = "__TAGGEDINPUT__"

        # Recently played
        if _store.recent_count > 0:
            all_items.append({
                "text": "Recently Played",
                "hasitems": 1,
                "actions": {
                    "go": {
                        "cmd": ["podcast", "items"],
                        "params": {"url": "__recent__", "menu": 1},
                    },
                },
            })

        # Subscribed feeds
        for sub in _store.subscriptions:
            item: dict[str, Any] = {
                "text": sub.name,
                "hasitems": 1,
                "actions": {
                    "go": {
                        "cmd": ["podcast", "items"],
                        "params": {"url": sub.url, "menu": 1},
                    },
                },
            }
            if sub.image:
                item["icon"] = sub.image
                item["window"] = {"icon-id": sub.image}
            if sub.author:
                item["textkey"] = sub.author

            # Context menu: unsubscribe
            item["actions"]["more"] = {
                "cmd": ["podcast", "delshow"],
                "params": {"url": sub.url, "name": sub.name, "menu": 1},
            }

            all_items.append(item)
    else:
        # CLI mode
        for sub in _store.subscriptions:
            all_items.append({
                "name": sub.name,
                "url": sub.url,
                "type": "link",
                "image": sub.image,
                "author": sub.author,
            })

    total = len(all_items)
    page = all_items[start: start + count]

    result = {"count": total, "offset": start}
    result["item_loop" if is_menu else "loop"] = page
    if is_menu:
        result["base"] = _base_actions()
    return result


# ---------------------------------------------------------------------------
# podcast search — search PodcastIndex
# ---------------------------------------------------------------------------


async def _podcast_search(
    ctx: CommandContext, command: list[Any]
) -> dict[str, Any]:
    """Handle ``podcast search <start> <count> [term:…] [menu:1]``.

    Searches PodcastIndex and returns matching feeds.
    """
    tagged = _parse_tagged(command, start=2)
    start, count = _parse_start_count(command)
    query = tagged.get("term", tagged.get("search", tagged.get("query", "")))
    is_menu = tagged.get("menu") == "1"

    if not query:
        result: dict[str, Any] = {"count": 0, "offset": 0}
        result["item_loop" if is_menu else "loop"] = []
        return result

    results = await _search_podcastindex(query)

    all_items: list[dict[str, Any]] = []
    for r in results:
        if is_menu:
            all_items.append(_build_jive_feed_item(r))
        else:
            all_items.append(_build_cli_item_from_search(r))

    total = len(all_items)
    page = all_items[start: start + count]

    result = {"count": total, "offset": start}
    result["item_loop" if is_menu else "loop"] = page
    if is_menu:
        result["base"] = _base_actions()
    return result


# ---------------------------------------------------------------------------
# podcast play — play an episode
# ---------------------------------------------------------------------------


async def _podcast_play(
    ctx: CommandContext, command: list[Any]
) -> dict[str, Any]:
    """Handle ``podcast play [url:…] [title:…] [icon:…] [feed_url:…] [cmd:…] [from:…]``.

    Plays a podcast episode.

    Params:
        url: Episode audio URL (required)
        title: Display title
        icon: Artwork URL
        feed_url: RSS feed URL (for metadata)
        feed_title: Podcast/show name
        cmd: ``"play"`` (default), ``"add"``, ``"insert"``
        from: Resume position in seconds (0 = from beginning)
        duration: Episode duration in seconds
        content_type: MIME type
    """
    assert _store is not None

    tagged = _parse_tagged(command, start=2)
    episode_url = tagged.get("url", "")
    title = tagged.get("title", "")
    icon = tagged.get("icon", "")
    feed_url = tagged.get("feed_url", "")
    feed_title = tagged.get("feed_title", "")
    play_cmd = tagged.get("cmd", "play")
    content_type = tagged.get("content_type", "audio/mpeg")
    resume_from = tagged.get("from", "")

    duration_str = tagged.get("duration", "0")
    try:
        duration_seconds = int(duration_str)
    except (ValueError, TypeError):
        duration_seconds = 0

    if not episode_url:
        return {"error": "Missing 'url' parameter"}

    # Build a PlaylistTrack
    from resonance.core.playlist import PlaylistTrack

    track = PlaylistTrack.from_url(
        url=episode_url,
        title=title or "Podcast Episode",
        artist=feed_title,
        album=feed_title,
        duration_ms=duration_seconds * 1000 if duration_seconds else 0,
        source="podcast",
        stream_url=episode_url,
        external_id=episode_url,
        artwork_url=icon or None,
        content_type=content_type,
        bitrate=0,
        is_live=False,
    )

    # Get player
    player = None
    if ctx.player_id and ctx.player_id != "-":
        player = await ctx.player_registry.get_by_mac(ctx.player_id)

    if player is None:
        return {"error": "No player selected"}

    if ctx.playlist_manager is None:
        return {"error": "Playlist manager not available"}

    playlist = ctx.playlist_manager.get(ctx.player_id)

    # Record as recently played
    _store.record_played(
        url=episode_url,
        title=title,
        show=feed_title,
        image=icon,
        duration=duration_seconds,
        feed_url=feed_url,
    )

    # Execute play/add/insert
    if play_cmd == "add":
        playlist.add(track)
        logger.info("Added podcast episode to playlist: %s", title)
    elif play_cmd == "insert":
        insert_idx = playlist.current_index + 1
        playlist.insert(insert_idx, track)
        logger.info("Inserted podcast episode into playlist: %s", title)
    else:
        # play — replace playlist and start playback
        playlist.clear()
        playlist.add(track)
        playlist.play(0)

        # Start streaming
        from resonance.web.handlers.playlist_playback import _start_track_stream

        await _start_track_stream(ctx, player, track)

        # Publish playlist event
        from resonance.core.events import PlayerPlaylistEvent

        if _event_bus is not None:
            await _event_bus.publish(
                PlayerPlaylistEvent(
                    player_id=ctx.player_id,
                    action="loadtracks",
                    index=0,
                    count=1,
                )
            )

        logger.info("Playing podcast episode: %s → %s", title, episode_url)

    return {"count": 1}


# ---------------------------------------------------------------------------
# podcast addshow — subscribe to a feed
# ---------------------------------------------------------------------------


async def _podcast_addshow(
    ctx: CommandContext, command: list[Any]
) -> dict[str, Any]:
    """Handle ``podcast addshow [url:…] [name:…] [image:…] [menu:1]``.

    Subscribe to a podcast feed.
    """
    assert _store is not None

    tagged = _parse_tagged(command, start=2)
    feed_url = tagged.get("url", "")
    name = tagged.get("name", "")
    image = tagged.get("image", "")
    is_menu = tagged.get("menu") == "1"

    if not feed_url:
        return {"error": "Missing 'url' parameter"}

    # If name is not given, try to fetch the feed to get metadata
    if not name:
        try:
            feed = await _get_feed(feed_url)
            name = feed.title
            if not image:
                image = feed.image_url
        except Exception:
            name = feed_url

    added = _store.add_subscription(
        url=feed_url,
        name=name,
        image=image,
    )

    if is_menu:
        msg = f"Subscribed to '{name}'" if added else f"Already subscribed to '{name}'"
        return {
            "count": 1,
            "item_loop": [{
                "text": msg,
                "showBriefly": 1,
                "nextWindow": "parent",
            }],
        }

    return {
        "count": 1,
        "subscribed": added,
        "name": name,
        "url": feed_url,
    }


# ---------------------------------------------------------------------------
# podcast delshow — unsubscribe from a feed
# ---------------------------------------------------------------------------


async def _podcast_delshow(
    ctx: CommandContext, command: list[Any]
) -> dict[str, Any]:
    """Handle ``podcast delshow [url:…] [name:…] [menu:1]``.

    Unsubscribe from a podcast feed.
    """
    assert _store is not None

    tagged = _parse_tagged(command, start=2)
    feed_url = tagged.get("url", "")
    name = tagged.get("name", feed_url)
    is_menu = tagged.get("menu") == "1"

    if not feed_url:
        return {"error": "Missing 'url' parameter"}

    removed = _store.remove_subscription(feed_url)

    if is_menu:
        msg = f"Unsubscribed from '{name}'" if removed else f"Not subscribed to '{name}'"
        return {
            "count": 1,
            "item_loop": [{
                "text": msg,
                "showBriefly": 1,
                "nextWindow": "grandparent",
            }],
        }

    return {
        "count": 1,
        "unsubscribed": removed,
        "name": name,
        "url": feed_url,
    }


# ---------------------------------------------------------------------------
# Jive menu item builders
# ---------------------------------------------------------------------------


def _build_jive_episode_item(
    episode: Any,
    *,
    feed_url: str = "",
    feed_title: str = "",
    feed_image: str = "",
) -> dict[str, Any]:
    """Build a Jive-compatible menu item for a podcast episode."""
    from .feed_parser import format_duration

    entry: dict[str, Any] = {
        "text": episode.title,
        "type": "audio",
        "hasitems": 0,
        "playHoldAction": "go",
    }

    # Image: per-episode or feed-level
    image = episode.image_url or feed_image
    if image:
        entry["icon"] = image

    # Subtitle: date + duration + resume info
    subtitle_parts: list[str] = []
    if episode.published:
        date_str = episode.published[:10] if len(episode.published) >= 10 else episode.published
        subtitle_parts.append(date_str)
    if episode.duration_seconds:
        subtitle_parts.append(format_duration(episode.duration_seconds))

    # Check for resume position
    resume_pos = 0
    if _store is not None:
        resume_pos = _store.get_resume_position(episode.url)
        if resume_pos > 0:
            subtitle_parts.append(f"from {format_duration(resume_pos)}")

    if subtitle_parts:
        entry["textkey"] = " · ".join(subtitle_parts)

    # Play params common to all actions
    play_params: dict[str, Any] = {
        "url": episode.url,
        "title": episode.title,
        "icon": image,
        "feed_url": feed_url,
        "feed_title": feed_title,
        "duration": str(episode.duration_seconds),
        "content_type": episode.content_type,
    }

    # If there's a resume position, show a sub-menu
    if resume_pos > 0 and episode.duration_seconds > 0:
        resume_str = format_duration(resume_pos)
        duration_str = format_duration(episode.duration_seconds)

        entry["type"] = "redirect"
        entry["hasitems"] = 1

        entry["actions"] = {
            "go": {
                "cmd": ["podcast", "items"],
                "params": {
                    "menu": 1,
                    "url": f"__resume__{episode.url}",
                    "resume_pos": str(resume_pos),
                    "ep_url": episode.url,
                    "ep_title": episode.title,
                    "ep_icon": image,
                    "feed_url": feed_url,
                    "feed_title": feed_title,
                    "duration": str(episode.duration_seconds),
                    "content_type": episode.content_type,
                },
            },
            "play": {
                "player": 0,
                "cmd": ["podcast", "play"],
                "params": {**play_params, "cmd": "play"},
            },
        }
    else:
        entry["actions"] = {
            "play": {
                "player": 0,
                "cmd": ["podcast", "play"],
                "params": {**play_params, "cmd": "play"},
            },
            "add": {
                "player": 0,
                "cmd": ["podcast", "play"],
                "params": {**play_params, "cmd": "add"},
            },
            "go": {
                "player": 0,
                "cmd": ["podcast", "play"],
                "params": {**play_params, "cmd": "play"},
            },
        }

    # Add-to-favorites context action
    entry.setdefault("actions", {})["more"] = {
        "player": 0,
        "cmd": ["jivefavorites", "add"],
        "params": {
            "title": episode.title,
            "url": episode.url,
            "type": "audio",
            "icon": image,
        },
    }

    return entry


def _build_jive_recent_item(ep: Any) -> dict[str, Any]:
    """Build a Jive-compatible menu item for a recently played episode."""
    entry: dict[str, Any] = {
        "text": ep.title or ep.url,
        "type": "audio",
        "hasitems": 0,
        "playHoldAction": "go",
    }

    if ep.image:
        entry["icon"] = ep.image

    if ep.show:
        entry["textkey"] = ep.show

    play_params: dict[str, Any] = {
        "url": ep.url,
        "title": ep.title,
        "icon": ep.image,
        "feed_url": ep.feed_url,
        "feed_title": ep.show,
        "duration": str(ep.duration),
    }

    entry["actions"] = {
        "play": {
            "player": 0,
            "cmd": ["podcast", "play"],
            "params": {**play_params, "cmd": "play"},
        },
        "add": {
            "player": 0,
            "cmd": ["podcast", "play"],
            "params": {**play_params, "cmd": "add"},
        },
        "go": {
            "player": 0,
            "cmd": ["podcast", "play"],
            "params": {**play_params, "cmd": "play"},
        },
    }

    return entry


def _build_jive_feed_item(feed_data: dict[str, Any]) -> dict[str, Any]:
    """Build a Jive-compatible menu item for a search result (feed)."""
    entry: dict[str, Any] = {
        "text": feed_data.get("name", ""),
        "hasitems": 1,
    }

    image = feed_data.get("image", "")
    if image:
        entry["icon"] = image
        entry["window"] = {"icon-id": image}

    author = feed_data.get("author", "")
    if author:
        entry["textkey"] = author

    feed_url = feed_data.get("url", "")

    entry["actions"] = {
        "go": {
            "cmd": ["podcast", "items"],
            "params": {"url": feed_url, "menu": 1},
        },
        # Context menu: subscribe
        "more": {
            "cmd": ["podcast", "addshow"],
            "params": {
                "url": feed_url,
                "name": feed_data.get("name", ""),
                "image": image,
                "menu": 1,
            },
        },
    }

    return entry


def _build_cli_episode_item(episode: Any, feed_url: str = "") -> dict[str, Any]:
    """Build a plain CLI response item for an episode."""
    from .feed_parser import format_duration

    entry: dict[str, Any] = {
        "name": episode.title,
        "url": episode.url,
        "type": "audio",
    }

    if episode.content_type:
        entry["content_type"] = episode.content_type
    if episode.duration_seconds:
        entry["duration"] = episode.duration_seconds
        entry["duration_text"] = format_duration(episode.duration_seconds)
    if episode.published:
        entry["published"] = episode.published
    if episode.image_url:
        entry["image"] = episode.image_url
    if feed_url:
        entry["feed_url"] = feed_url

    # Resume position
    if _store is not None:
        resume_pos = _store.get_resume_position(episode.url)
        if resume_pos > 0:
            entry["resume_position"] = resume_pos

    return entry


def _build_cli_item_from_search(data: dict[str, Any]) -> dict[str, Any]:
    """Build a plain CLI item from a PodcastIndex search result."""
    entry: dict[str, Any] = {
        "name": data.get("name", ""),
        "url": data.get("url", ""),
        "type": "link",
    }
    if data.get("image"):
        entry["image"] = data["image"]
    if data.get("author"):
        entry["author"] = data["author"]
    if data.get("description"):
        entry["description"] = data["description"][:200]
    return entry


def _base_actions() -> dict[str, Any]:
    """Base actions for Jive menu responses.

    These are merged with per-item actions by the Jive device.
    """
    return {
        "actions": {
            "go": {
                "cmd": ["podcast", "items"],
                "params": {"menu": 1},
                "itemsParams": "params",
            },
            "play": {
                "player": 0,
                "cmd": ["podcast", "play"],
                "itemsParams": "params",
            },
            "add": {
                "player": 0,
                "cmd": ["podcast", "play"],
                "params": {"cmd": "add"},
                "itemsParams": "params",
            },
        },
    }
