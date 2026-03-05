"""
Podcast Plugin for Resonance (v2).

Browse, search, subscribe to, and stream podcast episodes with automatic
resume tracking, What's New aggregation, OPML import/export, multiple
search providers, skip controls, progress tracking, and full Jive menu
integration.

Architecture
~~~~~~~~~~~~

* **feed_parser.py** — RSS 2.0 + iTunes namespace parser → typed dataclasses
* **store.py** — JSON-backed persistence (subscriptions, resume, progress,
  played state, recently played)
* **providers.py** — pluggable search providers (PodcastIndex, GPodder, iTunes)
* **opml.py** — OPML 2.0 import / export
* **PodcastProvider** — :class:`~resonance.content_provider.ContentProvider`
  implementation for the server's generic content infrastructure
* JSON-RPC commands provide the Jive-compatible menu interface:

  - ``podcast items``     — browse subscribed feeds / episodes / what's new
  - ``podcast search``    — search via configurable provider
  - ``podcast play``      — play / add / insert an episode
  - ``podcast addshow``   — subscribe to a feed
  - ``podcast delshow``   — unsubscribe from a feed
  - ``podcast markplayed``  — mark episode(s) as played
  - ``podcast markunplayed``— mark episode(s) as unplayed
  - ``podcast opmlimport``  — import subscriptions from OPML
  - ``podcast opmlexport``  — export subscriptions to OPML
  - ``podcast trending``    — browse trending podcasts
  - ``podcast info``        — show detailed feed / episode info
  - ``podcast skip``        — skip forward / backward in playback

v2 improvements over v1 (and over LMS)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **Event-based resume** — subscribes to ``player.status`` events to
  capture elapsed time automatically; no need for manual position tracking.
* **What's New** — aggregates recent episodes from ALL subscriptions,
  sorted newest-first.  LMS needs PodcastIndex API for this; we do it
  via local RSS parsing (works offline, provider-independent).
* **Multiple search providers** — PodcastIndex, GPodder, iTunes.
  Configurable via settings.
* **Trending / discovery** — PodcastIndex trending, GPodder top-lists,
  iTunes charts.  LMS has "What's New" from PodcastIndex only.
* **OPML import / export** — standard podcast interchange format.
* **Mark played / unplayed** — context menu on episodes.
* **Episode progress tracking** — position + duration + percentage per
  episode, enabling progress bars in the UI.
* **Continue Listening** — section showing in-progress episodes across
  all subscriptions, sorted by last-played.
* **Skip forward / back** — configurable seconds, Jive Track Info menu.
* **Auto mark played** — configurable percentage threshold (default 90%).
* **Background feed refresh** — periodic refresh with configurable interval.
* **Settings UI** — all preferences exposed via plugin settings system.
* **Episode info view** — full description / show-notes accessible.
* **Subscription ordering** — move subscriptions up / down.
* **New-episode badges** — cached count of new episodes per subscription.

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

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from resonance.web.jsonrpc_helpers import parse_start_count, parse_tagged_params

if TYPE_CHECKING:
    from resonance.plugin import PluginContext
    from resonance.web.handlers import CommandContext

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level state (set during setup, cleared during teardown)
# ---------------------------------------------------------------------------

_store: Any | None = None            # PodcastStore instance
_http_client: Any | None = None      # httpx.AsyncClient (shared)
_event_bus: Any | None = None        # EventBus reference
_provider: Any | None = None         # PodcastProvider instance
_ctx: Any | None = None              # PluginContext reference
_refresh_task: asyncio.Task[None] | None = None  # Background refresh task

# Simple in-memory cache for parsed feeds {feed_url: (PodcastFeed, expire_ts)}
_feed_cache: dict[str, tuple[Any, float]] = {}

# Per-player tracking: {player_id: {url, elapsed, duration, source}}
# Used by the event handler to save resume positions on stop/pause.
_player_tracking: dict[str, dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------


def _setting(key: str, default: Any = None) -> Any:
    """Read a plugin setting, falling back to *default*."""
    if _ctx is None:
        return default
    try:
        return _ctx.get_setting(key)
    except (KeyError, Exception):
        return default


def _feed_cache_ttl() -> int:
    return int(_setting("feed_cache_ttl", 600))


# ---------------------------------------------------------------------------
# Feed cache helpers
# ---------------------------------------------------------------------------


async def _get_feed(feed_url: str, *, force: bool = False) -> Any:
    """Fetch and parse a podcast feed with caching."""
    now = time.time()

    # Check cache
    if not force and feed_url in _feed_cache:
        feed, expires = _feed_cache[feed_url]
        if now < expires:
            return feed

    from .feed_parser import fetch_feed

    feed = await fetch_feed(feed_url, client=_http_client)
    _feed_cache[feed_url] = (feed, now + _feed_cache_ttl())

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
# Event-based resume tracking
# ---------------------------------------------------------------------------


async def _on_player_status(event: Any) -> None:
    """Handle player status events for automatic resume position saving.

    When a player is playing a podcast episode, we track the elapsed time.
    When playback stops or pauses, we save the resume position.
    This is far more reliable than LMS's ``onStop`` approach because we
    capture every status update, not just explicit stop events.
    """
    if _store is None:
        return

    player_id = getattr(event, "player_id", "")
    state = getattr(event, "state", "")
    elapsed = getattr(event, "elapsed_seconds", 0.0)
    duration = getattr(event, "duration", 0.0)
    current_track = getattr(event, "current_track", None)

    if not player_id:
        return

    # Determine if we're tracking a podcast
    source = ""
    track_url = ""
    track_title = ""
    track_artist = ""
    track_artwork = ""

    if isinstance(current_track, dict):
        source = current_track.get("source", "")
        track_url = current_track.get("path", "") or current_track.get("url", "")
        track_title = current_track.get("title", "")
        track_artist = current_track.get("artist", "") or current_track.get("album", "")
        track_artwork = current_track.get("artwork_url", "")

    if source == "podcast" and track_url:
        tracking = _player_tracking.get(player_id, {})

        if state == "playing":
            # Update tracking state
            _player_tracking[player_id] = {
                "url": track_url,
                "elapsed": elapsed,
                "duration": duration,
                "title": track_title,
                "artist": track_artist,
                "artwork": track_artwork,
            }

            # Periodically save progress (every ~30 seconds of change)
            prev_elapsed = tracking.get("elapsed", 0.0)
            if abs(elapsed - prev_elapsed) >= 30 and duration > 0:
                _store.set_resume_position(
                    track_url, int(elapsed), int(duration),
                )

        elif state in ("paused", "stopped"):
            # Save final position when playback stops/pauses
            url = tracking.get("url", track_url)
            dur = tracking.get("duration", duration)
            if url and elapsed > 0:
                _store.set_resume_position(url, int(elapsed), int(dur))

            if state == "stopped":
                _player_tracking.pop(player_id, None)

    elif state in ("playing",) and source != "podcast":
        # Player switched to non-podcast content — save any tracked position
        tracking = _player_tracking.pop(player_id, None)
        if tracking and tracking.get("url"):
            pos = tracking.get("elapsed", 0.0)
            dur = tracking.get("duration", 0.0)
            if pos > 0:
                _store.set_resume_position(tracking["url"], int(pos), int(dur))


# ---------------------------------------------------------------------------
# Background feed refresh
# ---------------------------------------------------------------------------


async def _background_refresh_loop() -> None:
    """Periodically refresh subscribed feeds to detect new episodes.

    Runs as an asyncio task; cancelled on teardown.
    """
    while True:
        interval_minutes = int(_setting("auto_refresh_minutes", 60))
        if interval_minutes <= 0:
            # Auto-refresh disabled — check again in 5 minutes
            await asyncio.sleep(300)
            continue

        await asyncio.sleep(interval_minutes * 60)

        if _store is None:
            continue

        subs = _store.subscriptions
        if not subs:
            continue

        logger.info("Background refresh: checking %d subscriptions", len(subs))

        for sub in subs:
            try:
                feed = await _get_feed(sub.url, force=True)

                # Count new episodes since last browse
                if sub.last_browsed_at > 0:
                    new_count = sum(
                        1 for ep in feed.episodes
                        if ep.published_epoch > sub.last_browsed_at
                    )
                    _store.set_new_episode_count(sub.url, new_count)

            except Exception as exc:
                logger.debug("Background refresh failed for %s: %s", sub.url, exc)

            # Small delay between feeds to avoid hammering
            await asyncio.sleep(2)

        logger.info("Background refresh complete")


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

            # What's New
            if _store.subscription_count > 0:
                total_new = _store.total_new_episodes
                subtitle = f"{total_new} new" if total_new > 0 else "Latest episodes"
                items.append(BrowseItem(
                    id="__whatsnew__",
                    title="What's New",
                    type="folder",
                    subtitle=subtitle,
                ))

            # Continue Listening
            in_progress = _store.get_in_progress_episodes()
            if in_progress:
                items.append(BrowseItem(
                    id="__continue__",
                    title="Continue Listening",
                    type="folder",
                    subtitle=f"{len(in_progress)} episodes",
                ))

            # Recently played
            if _store.recent_count > 0:
                items.append(BrowseItem(
                    id="__recent__",
                    title="Recently Played",
                    type="folder",
                    subtitle=f"{_store.recent_count} episodes",
                ))

            # Trending
            items.append(BrowseItem(
                id="__trending__",
                title="Trending Podcasts",
                type="folder",
                subtitle="Discover popular shows",
            ))

            # Subscribed feeds
            for sub in _store.subscriptions:
                badge = f" ({sub.new_episode_count} new)" if sub.new_episode_count > 0 else ""
                items.append(BrowseItem(
                    id=sub.url,
                    title=sub.name + badge,
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
                date_str = ep.published[:10] if len(ep.published) >= 10 else ep.published
                subtitle_parts.append(date_str)
            if ep.duration_seconds:
                subtitle_parts.append(format_duration(ep.duration_seconds))

            # Check for resume position
            resume_pos = _store.get_resume_position(ep.url) if _store else 0
            if resume_pos > 0:
                subtitle_parts.append(f"from {format_duration(resume_pos)}")

            # Played indicator
            is_played = _store.is_played(ep.url) if _store else False
            if is_played:
                subtitle_parts.append("✓")

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
                    "is_played": is_played,
                },
            ))

        return items

    async def search(self, query: str) -> list[Any]:
        """Search for podcasts via the configured provider."""
        from resonance.content_provider import BrowseItem

        from .providers import get_provider

        provider_name = str(_setting("search_provider", "podcastindex"))
        provider = get_provider(provider_name)

        results = await provider.search(query, client=_http_client)

        items: list[BrowseItem] = []
        for r in results:
            items.append(BrowseItem(
                id=r.url,
                title=r.name,
                type="folder",
                url=r.url,
                icon=r.image or None,
                subtitle=r.author or (r.description[:100] if r.description else None),
            ))

        return items

    async def get_stream_info(self, item_id: str) -> Any | None:
        """Resolve an episode URL/GUID to stream info.

        For podcasts, the episode URL *is* the stream URL — no additional
        resolution step needed (unlike radio stations which need Tune.ashx).
        """
        from resonance.content_provider import StreamInfo

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
        # Save any tracked position for this player
        tracking = _player_tracking.get(player_mac)
        if tracking and tracking.get("url") == item_id:
            pos = tracking.get("elapsed", 0.0)
            dur = tracking.get("duration", 0.0)
            if _store and pos > 0:
                _store.set_resume_position(item_id, int(pos), int(dur))
            _player_tracking.pop(player_mac, None)

        logger.debug(
            "Podcast stream stopped: episode=%s player=%s", item_id, player_mac
        )


# ---------------------------------------------------------------------------
# Helpers — parameter parsing
# ---------------------------------------------------------------------------


def _parse_tagged(command: list[Any], start: int = 1) -> dict[str, str]:
    """Parse ``key:value`` tagged params from *command* starting at *start*.

    Delegates to :func:`resonance.web.jsonrpc_helpers.parse_tagged_params`.
    """
    return parse_tagged_params(command[start:])


def _parse_start_count(command: list[Any], sub_offset: int = 2) -> tuple[int, int]:
    """Extract ``(start, count)`` from positional args after the sub-command.

    Delegates to :func:`resonance.web.jsonrpc_helpers.parse_start_count`.
    """
    return parse_start_count(command, sub_offset)


# ---------------------------------------------------------------------------
# Plugin lifecycle
# ---------------------------------------------------------------------------


async def setup(ctx: PluginContext) -> None:
    """Called by PluginManager during server startup."""
    global _store, _http_client, _event_bus, _provider, _ctx, _refresh_task

    import httpx

    from .store import PodcastStore

    _ctx = ctx
    _http_client = httpx.AsyncClient(follow_redirects=True, timeout=15.0)

    # Create store with settings-driven config
    max_recent = int(_setting("max_recent", 50))
    auto_mark = int(_setting("auto_mark_played_percent", 90))

    _store = PodcastStore(
        ctx.ensure_data_dir(),
        max_recent=max_recent,
        auto_mark_played_percent=auto_mark,
    )
    _store.load()

    _event_bus = ctx.event_bus
    _provider = PodcastProvider()

    # ── Register content provider ──────────────────────────────
    ctx.register_content_provider("podcast", _provider)

    # ── Commands ───────────────────────────────────────────────
    ctx.register_command("podcast", cmd_podcast)

    # ── Subscribe to player events for resume tracking ─────────
    await ctx.subscribe("player.status", _on_player_status)

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

    # ── Start background refresh task ──────────────────────────
    _refresh_task = asyncio.create_task(
        _background_refresh_loop(),
        name="podcast-background-refresh",
    )

    logger.info(
        "Podcast plugin v2 started (%d subscriptions, %d resume, %d played, provider=%s)",
        _store.subscription_count,
        len(_store.resume_positions),
        _store.played_count,
        _setting("search_provider", "podcastindex"),
    )


async def teardown(ctx: PluginContext) -> None:
    """Called by PluginManager during server shutdown."""
    global _store, _http_client, _event_bus, _provider, _ctx, _refresh_task

    # Cancel background refresh
    if _refresh_task is not None:
        _refresh_task.cancel()
        try:
            await _refresh_task
        except asyncio.CancelledError:
            pass
        _refresh_task = None

    # Save any in-flight player positions
    if _store is not None:
        for _pid, tracking in _player_tracking.items():
            url = tracking.get("url", "")
            pos = tracking.get("elapsed", 0.0)
            dur = tracking.get("duration", 0.0)
            if url and pos > 0:
                _store.set_resume_position(url, int(pos), int(dur))
        _player_tracking.clear()
        _store.save()

    if _http_client is not None:
        await _http_client.aclose()

    _clear_feed_cache()
    _store = None
    _http_client = None
    _event_bus = None
    _provider = None
    _ctx = None

    logger.info("Podcast plugin stopped")


# ---------------------------------------------------------------------------
# JSON-RPC command dispatch
# ---------------------------------------------------------------------------


async def cmd_podcast(
    ctx: CommandContext, command: list[Any]
) -> dict[str, Any]:
    """Dispatch ``podcast <sub-command> …`` to the appropriate handler.

    Sub-commands:
    - ``items``         — browse subscribed feeds / episodes / what's new
    - ``search``        — search via configurable provider
    - ``play``          — play / add / insert an episode
    - ``addshow``       — subscribe to a feed
    - ``delshow``       — unsubscribe from a feed
    - ``markplayed``    — mark episode(s) as played
    - ``markunplayed``  — mark episode(s) as unplayed
    - ``opmlimport``    — import subscriptions from OPML
    - ``opmlexport``    — export subscriptions to OPML
    - ``trending``      — browse trending podcasts
    - ``info``          — show detailed feed / episode info
    - ``skip``          — skip forward / backward
    - ``stats``         — plugin statistics
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
        case "markplayed":
            return await _podcast_markplayed(ctx, command)
        case "markunplayed":
            return await _podcast_markunplayed(ctx, command)
        case "opmlimport":
            return await _podcast_opml_import(ctx, command)
        case "opmlexport":
            return await _podcast_opml_export(ctx, command)
        case "trending":
            return await _podcast_trending(ctx, command)
        case "info":
            return await _podcast_info(ctx, command)
        case "skip":
            return await _podcast_skip(ctx, command)
        case "stats":
            return _podcast_stats()
        case _:
            return {"error": f"Unknown podcast sub-command: {sub}"}


# ---------------------------------------------------------------------------
# podcast items — browse
# ---------------------------------------------------------------------------


async def _podcast_items(
    ctx: CommandContext, command: list[Any]
) -> dict[str, Any]:
    """Handle ``podcast items <start> <count> [url:…] [menu:1] [search:…]``.

    Without ``url``, returns the top-level menu (what's new, continue
    listening, search, recently played, trending, subscriptions).
    With ``url``, returns episodes for that feed.
    """
    assert _store is not None

    tagged = _parse_tagged(command, start=2)
    start, count = _parse_start_count(command)
    browse_url = tagged.get("url", "")
    is_menu = tagged.get("menu") == "1"
    search_query = tagged.get("search", "")

    # -- Inline search -------------------------------------------------------
    if search_query:
        from .providers import get_provider

        provider_name = str(_setting("search_provider", "podcastindex"))
        provider = get_provider(provider_name)
        results = await provider.search(search_query, client=_http_client)

        all_items: list[dict[str, Any]] = []
        for r in results:
            if is_menu:
                all_items.append(_build_jive_feed_item(r.to_dict()))
            else:
                all_items.append(_build_cli_item_from_search(r.to_dict()))

        total = len(all_items)
        page = all_items[start: start + count]

        result: dict[str, Any] = {"count": total, "offset": start}
        result["item_loop" if is_menu else "loop"] = page
        if is_menu:
            result["base"] = _base_actions()
        return result

    # -- Resume sub-menu -----------------------------------------------------
    if browse_url.startswith("__resume__"):
        return _build_resume_submenu(tagged, is_menu)

    # -- What's New ----------------------------------------------------------
    if browse_url == "__whatsnew__":
        return await _build_whatsnew(start, count, is_menu)

    # -- Continue Listening --------------------------------------------------
    if browse_url == "__continue__":
        return _build_continue_listening(start, count, is_menu)

    # -- Recently played -----------------------------------------------------
    if browse_url == "__recent__":
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

    # -- Trending ------------------------------------------------------------
    if browse_url == "__trending__":
        return await _build_trending(start, count, is_menu, tagged)

    # -- Browse a specific feed (list episodes) ------------------------------
    if browse_url:
        try:
            feed = await _get_feed(browse_url)
        except Exception as exc:
            logger.warning("Failed to fetch feed %s: %s", browse_url, exc)
            error_items = [{"text": f"Failed to load feed: {exc}"}] if is_menu else []
            result = {"count": 0, "offset": 0}
            result["item_loop" if is_menu else "loop"] = error_items
            return result

        # Mark feed as browsed (resets new-episode count)
        _store.mark_feed_browsed(browse_url)

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
            # Add feed-level context actions (info, mark all played, unsubscribe)
            result["window"] = {
                "titleStyle": "album",
                "icon-id": feed.image_url or "",
            }
        return result

    # -- Root menu -----------------------------------------------------------
    all_items = []

    if is_menu:
        # 1) Search entry
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

        # 2) What's New (if we have subscriptions)
        if _store.subscription_count > 0:
            total_new = _store.total_new_episodes
            whatsnew_text = "What's New"
            if total_new > 0:
                whatsnew_text = f"What's New ({total_new})"
            all_items.append({
                "text": whatsnew_text,
                "hasitems": 1,
                "icon": "plugins/Podcast/html/images/podcastindex.png",
                "actions": {
                    "go": {
                        "cmd": ["podcast", "items"],
                        "params": {"url": "__whatsnew__", "menu": 1},
                    },
                },
            })

        # 3) Continue Listening
        in_progress = _store.get_in_progress_episodes()
        if in_progress:
            all_items.append({
                "text": f"Continue Listening ({len(in_progress)})",
                "hasitems": 1,
                "actions": {
                    "go": {
                        "cmd": ["podcast", "items"],
                        "params": {"url": "__continue__", "menu": 1},
                    },
                },
            })

        # 4) Recently played
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

        # 5) Trending
        all_items.append({
            "text": "Trending Podcasts",
            "hasitems": 1,
            "actions": {
                "go": {
                    "cmd": ["podcast", "items"],
                    "params": {"url": "__trending__", "menu": 1},
                },
            },
        })

        # 6) Subscribed feeds
        for sub in _store.subscriptions:
            text = sub.name
            if sub.new_episode_count > 0:
                text = f"{sub.name} ({sub.new_episode_count})"

            item: dict[str, Any] = {
                "text": text,
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

            # Context menu: info + unsubscribe + mark all played
            item["actions"]["more"] = {
                "cmd": ["podcast", "info"],
                "params": {
                    "url": sub.url,
                    "name": sub.name,
                    "image": sub.image,
                    "menu": 1,
                },
            }

            all_items.append(item)
    else:
        # CLI mode — return subscription list
        for sub in _store.subscriptions:
            all_items.append({
                "name": sub.name,
                "url": sub.url,
                "type": "link",
                "image": sub.image,
                "author": sub.author,
                "new_episodes": sub.new_episode_count,
            })

    total = len(all_items)
    page = all_items[start: start + count]

    result = {"count": total, "offset": start}
    result["item_loop" if is_menu else "loop"] = page
    if is_menu:
        result["base"] = _base_actions()
    return result


# ---------------------------------------------------------------------------
# What's New — aggregated new episodes across all subscriptions
# ---------------------------------------------------------------------------


async def _build_whatsnew(
    start: int, count: int, is_menu: bool,
) -> dict[str, Any]:
    """Aggregate new episodes from all subscribed feeds.

    Unlike LMS (which depends on PodcastIndex API for this), we parse
    the actual RSS feeds.  This works offline and with any feed, not
    just those indexed by PodcastIndex.
    """
    assert _store is not None

    new_since_days = int(_setting("new_since_days", 7))
    max_new = int(_setting("max_new_episodes", 50))
    cutoff = time.time() - (new_since_days * 86400)

    all_episodes: list[tuple[Any, str, str, str]] = []  # (episode, feed_url, feed_title, feed_image)

    for sub in _store.subscriptions:
        try:
            feed = await _get_feed(sub.url)
            for ep in feed.episodes:
                if ep.published_epoch >= cutoff:
                    all_episodes.append((ep, sub.url, feed.title, feed.image_url))
        except Exception as exc:
            logger.debug("What's New: failed to fetch %s: %s", sub.url, exc)

    # Sort by publication date, newest first
    all_episodes.sort(key=lambda x: x[0].published_epoch, reverse=True)
    all_episodes = all_episodes[:max_new]

    all_items: list[dict[str, Any]] = []
    for ep, feed_url, feed_title, feed_image in all_episodes:
        if is_menu:
            item = _build_jive_episode_item(
                ep, feed_url=feed_url, feed_title=feed_title,
                feed_image=feed_image, show_feed_name=True,
            )
            all_items.append(item)
        else:
            cli_item = _build_cli_episode_item(ep, feed_url=feed_url)
            cli_item["feed_title"] = feed_title
            all_items.append(cli_item)

    total = len(all_items)
    page = all_items[start: start + count]

    result: dict[str, Any] = {"count": total, "offset": start}
    result["item_loop" if is_menu else "loop"] = page
    if is_menu:
        result["base"] = _base_actions()
    return result


# ---------------------------------------------------------------------------
# Continue Listening — in-progress episodes
# ---------------------------------------------------------------------------


def _build_continue_listening(
    start: int, count: int, is_menu: bool,
) -> dict[str, Any]:
    """Show episodes with saved progress (not yet finished)."""
    assert _store is not None

    from .feed_parser import format_duration

    in_progress = _store.get_in_progress_episodes()

    all_items: list[dict[str, Any]] = []
    for entry in in_progress:
        url = entry["url"]
        title = entry.get("title", url.rsplit("/", 1)[-1])
        show = entry.get("show", "")
        image = entry.get("image", "")
        position = entry.get("position", 0)
        duration = entry.get("duration", 0)
        percentage = entry.get("percentage", 0)
        feed_url = entry.get("feed_url", "")

        if is_menu:
            pos_text = format_duration(position) if position else ""
            dur_text = format_duration(duration) if duration else ""
            progress_text = f"{pos_text} / {dur_text}" if dur_text else pos_text
            pct_text = f"{percentage:.0f}%"

            subtitle_parts = []
            if show:
                subtitle_parts.append(show)
            if progress_text:
                subtitle_parts.append(progress_text)
            subtitle_parts.append(pct_text)

            play_params: dict[str, Any] = {
                "url": url,
                "title": title,
                "icon": image,
                "feed_url": feed_url,
                "feed_title": show,
                "duration": str(duration),
                "from": str(position),
            }

            item: dict[str, Any] = {
                "text": title,
                "type": "redirect",
                "hasitems": 1,
                "playHoldAction": "go",
            }
            if image:
                item["icon"] = image
            if subtitle_parts:
                item["textkey"] = " · ".join(subtitle_parts)

            # Resume sub-menu: play from position / play from beginning
            item["actions"] = {
                "go": {
                    "cmd": ["podcast", "items"],
                    "params": {
                        "menu": 1,
                        "url": f"__resume__{url}",
                        "resume_pos": str(position),
                        "ep_url": url,
                        "ep_title": title,
                        "ep_icon": image,
                        "feed_url": feed_url,
                        "feed_title": show,
                        "duration": str(duration),
                    },
                },
                "play": {
                    "player": 0,
                    "cmd": ["podcast", "play"],
                    "params": {**play_params, "cmd": "play"},
                },
            }

            all_items.append(item)
        else:
            all_items.append({
                "name": title,
                "url": url,
                "type": "audio",
                "show": show,
                "image": image,
                "position": position,
                "duration": duration,
                "percentage": percentage,
            })

    total = len(all_items)
    page = all_items[start: start + count]

    result: dict[str, Any] = {"count": total, "offset": start}
    result["item_loop" if is_menu else "loop"] = page
    if is_menu:
        result["base"] = _base_actions()
    return result


# ---------------------------------------------------------------------------
# Resume sub-menu (play from position / play from beginning)
# ---------------------------------------------------------------------------


def _build_resume_submenu(
    tagged: dict[str, str], is_menu: bool,
) -> dict[str, Any]:
    """Build the resume sub-menu for an episode with a saved position.

    Shows two options:
    1. "Play from MM:SS" — resume at saved position
    2. "Play from beginning" — start over
    """
    from .feed_parser import format_duration

    ep_url = tagged.get("ep_url", "")
    ep_title = tagged.get("ep_title", "")
    ep_icon = tagged.get("ep_icon", "")
    feed_url = tagged.get("feed_url", "")
    feed_title = tagged.get("feed_title", "")
    duration = tagged.get("duration", "0")
    content_type = tagged.get("content_type", "audio/mpeg")

    resume_pos_str = tagged.get("resume_pos", "0")
    try:
        resume_pos = int(resume_pos_str)
    except (ValueError, TypeError):
        resume_pos = 0

    base_play_params: dict[str, Any] = {
        "url": ep_url,
        "title": ep_title,
        "icon": ep_icon,
        "feed_url": feed_url,
        "feed_title": feed_title,
        "duration": duration,
        "content_type": content_type,
        "cmd": "play",
    }

    if not is_menu:
        return {
            "count": 2,
            "offset": 0,
            "loop": [
                {"name": f"Play from {format_duration(resume_pos)}", "url": ep_url, "from": resume_pos},
                {"name": "Play from beginning", "url": ep_url, "from": 0},
            ],
        }

    pos_text = format_duration(resume_pos)
    dur_text = format_duration(int(duration)) if duration and duration != "0" else ""
    progress = f"{pos_text} / {dur_text}" if dur_text else pos_text

    items: list[dict[str, Any]] = [
        {
            "text": f"Resume from {pos_text}" + (f" ({progress})" if dur_text else ""),
            "type": "audio",
            "hasitems": 0,
            "icon": ep_icon or "",
            "actions": {
                "play": {
                    "player": 0,
                    "cmd": ["podcast", "play"],
                    "params": {**base_play_params, "from": str(resume_pos)},
                },
                "go": {
                    "player": 0,
                    "cmd": ["podcast", "play"],
                    "params": {**base_play_params, "from": str(resume_pos)},
                },
            },
        },
        {
            "text": "Play from beginning",
            "type": "audio",
            "hasitems": 0,
            "icon": ep_icon or "",
            "actions": {
                "play": {
                    "player": 0,
                    "cmd": ["podcast", "play"],
                    "params": {**base_play_params, "from": "0"},
                },
                "go": {
                    "player": 0,
                    "cmd": ["podcast", "play"],
                    "params": {**base_play_params, "from": "0"},
                },
            },
        },
    ]

    # Also offer "Mark as played" in this context
    items.append({
        "text": "Mark as played",
        "type": "text",
        "hasitems": 0,
        "actions": {
            "go": {
                "cmd": ["podcast", "markplayed"],
                "params": {"url": ep_url, "menu": 1},
            },
        },
    })

    return {
        "count": len(items),
        "offset": 0,
        "item_loop": items,
    }


# ---------------------------------------------------------------------------
# Trending podcasts
# ---------------------------------------------------------------------------


async def _build_trending(
    start: int, count: int, is_menu: bool, tagged: dict[str, str],
) -> dict[str, Any]:
    """Fetch and display trending podcasts from the configured provider."""
    from .providers import get_provider

    provider_name = str(_setting("search_provider", "podcastindex"))
    provider = get_provider(provider_name)

    language = tagged.get("lang", "")
    category = tagged.get("cat", "")

    if not provider.supports_trending:
        # Fallback: use PodcastIndex for trending even if search is set differently
        from .providers import get_provider as _gp
        provider = _gp("podcastindex")

    results = await provider.trending(
        max_results=count + start,
        language=language,
        category=category,
        client=_http_client,
    )

    all_items: list[dict[str, Any]] = []
    for r in results:
        if is_menu:
            all_items.append(_build_jive_feed_item(r.to_dict()))
        else:
            all_items.append(_build_cli_item_from_search(r.to_dict()))

    total = len(all_items)
    page = all_items[start: start + count]

    result: dict[str, Any] = {"count": total, "offset": start}
    result["item_loop" if is_menu else "loop"] = page
    if is_menu:
        result["base"] = _base_actions()
    return result


# ---------------------------------------------------------------------------
# podcast search — search via provider
# ---------------------------------------------------------------------------


async def _podcast_search(
    ctx: CommandContext, command: list[Any]
) -> dict[str, Any]:
    """Handle ``podcast search <start> <count> [term:…] [menu:1]``.

    Searches via the configured provider and returns matching feeds.
    """
    tagged = _parse_tagged(command, start=2)
    start, count = _parse_start_count(command)
    query = tagged.get("term", tagged.get("search", tagged.get("query", "")))
    is_menu = tagged.get("menu") == "1"

    if not query:
        result: dict[str, Any] = {"count": 0, "offset": 0}
        result["item_loop" if is_menu else "loop"] = []
        return result

    from .providers import get_provider

    provider_name = str(_setting("search_provider", "podcastindex"))
    provider = get_provider(provider_name)
    results = await provider.search(query, client=_http_client)

    all_items: list[dict[str, Any]] = []
    for r in results:
        if is_menu:
            all_items.append(_build_jive_feed_item(r.to_dict()))
        else:
            all_items.append(_build_cli_item_from_search(r.to_dict()))

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

    # Set initial tracking for event-based resume
    _player_tracking[ctx.player_id] = {
        "url": episode_url,
        "elapsed": 0.0,
        "duration": float(duration_seconds),
        "title": title,
        "artist": feed_title,
        "artwork": icon,
    }

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
    author = ""
    description = ""
    if not name:
        try:
            feed = await _get_feed(feed_url)
            name = feed.title
            if not image:
                image = feed.image_url
            author = feed.author
            description = feed.description[:200] if feed.description else ""
        except Exception:
            name = feed_url

    added = _store.add_subscription(
        url=feed_url,
        name=name,
        image=image,
        author=author,
        description=description,
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
    """Handle ``podcast delshow [url:…] [name:…] [menu:1]``."""
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
# podcast markplayed / markunplayed — toggle played state
# ---------------------------------------------------------------------------


async def _podcast_markplayed(
    ctx: CommandContext, command: list[Any]
) -> dict[str, Any]:
    """Handle ``podcast markplayed [url:…] [feed_url:…] [menu:1]``.

    Mark a single episode or all episodes in a feed as played.
    """
    assert _store is not None

    tagged = _parse_tagged(command, start=2)
    episode_url = tagged.get("url", "")
    feed_url = tagged.get("feed_url", "")
    is_menu = tagged.get("menu") == "1"

    if feed_url and not episode_url:
        # Mark all episodes in this feed as played
        try:
            feed = await _get_feed(feed_url)
            urls = [ep.url for ep in feed.episodes]
            count = _store.mark_all_played(urls)
            msg = f"Marked {count} episodes as played"
        except Exception as exc:
            msg = f"Error: {exc}"
            count = 0
    elif episode_url:
        _store.mark_played(episode_url)
        msg = "Marked as played"
        count = 1
    else:
        return {"error": "Missing 'url' or 'feed_url' parameter"}

    if is_menu:
        return {
            "count": 1,
            "item_loop": [{
                "text": msg,
                "showBriefly": 1,
                "nextWindow": "parent",
            }],
        }

    return {"count": count, "message": msg}


async def _podcast_markunplayed(
    ctx: CommandContext, command: list[Any]
) -> dict[str, Any]:
    """Handle ``podcast markunplayed [url:…] [menu:1]``."""
    assert _store is not None

    tagged = _parse_tagged(command, start=2)
    episode_url = tagged.get("url", "")
    is_menu = tagged.get("menu") == "1"

    if not episode_url:
        return {"error": "Missing 'url' parameter"}

    _store.mark_unplayed(episode_url)

    if is_menu:
        return {
            "count": 1,
            "item_loop": [{
                "text": "Marked as unplayed",
                "showBriefly": 1,
                "nextWindow": "parent",
            }],
        }

    return {"count": 1, "message": "Marked as unplayed"}


# ---------------------------------------------------------------------------
# podcast opmlimport / opmlexport
# ---------------------------------------------------------------------------


async def _podcast_opml_import(
    ctx: CommandContext, command: list[Any]
) -> dict[str, Any]:
    """Handle ``podcast opmlimport [path:…] [data:…] [menu:1]``.

    Import subscriptions from an OPML file or inline XML data.
    """
    assert _store is not None

    tagged = _parse_tagged(command, start=2)
    file_path = tagged.get("path", "")
    xml_data = tagged.get("data", "")
    is_menu = tagged.get("menu") == "1"

    from .opml import import_opml_file, parse_opml

    try:
        if xml_data:
            doc = parse_opml(xml_data)
        elif file_path:
            doc = import_opml_file(file_path)
        else:
            return {"error": "Missing 'path' or 'data' parameter"}

        feeds = [f.to_dict() for f in doc.feeds]
        added, skipped = _store.import_subscriptions(feeds)

        msg = f"Imported {added} subscriptions ({skipped} skipped)"

    except Exception as exc:
        msg = f"Import failed: {exc}"
        added = 0
        skipped = 0

    if is_menu:
        return {
            "count": 1,
            "item_loop": [{
                "text": msg,
                "showBriefly": 1,
                "nextWindow": "parent",
            }],
        }

    return {
        "added": added,
        "skipped": skipped,
        "message": msg,
    }


async def _podcast_opml_export(
    ctx: CommandContext, command: list[Any]
) -> dict[str, Any]:
    """Handle ``podcast opmlexport [path:…] [menu:1]``.

    Export subscriptions to an OPML file.  If no path is given, returns
    the OPML XML as a string in the response.
    """
    assert _store is not None

    tagged = _parse_tagged(command, start=2)
    file_path = tagged.get("path", "")
    is_menu = tagged.get("menu") == "1"

    from .opml import export_opml_file, generate_opml

    subs = _store.export_subscriptions()

    if file_path:
        try:
            export_opml_file(file_path, subs)
            msg = f"Exported {len(subs)} subscriptions to {file_path}"
        except Exception as exc:
            msg = f"Export failed: {exc}"
    else:
        # Return inline
        xml = generate_opml(subs)
        if is_menu:
            return {
                "count": 1,
                "item_loop": [{
                    "text": f"Exported {len(subs)} subscriptions",
                    "showBriefly": 1,
                }],
            }
        return {
            "count": len(subs),
            "opml": xml,
        }

    if is_menu:
        return {
            "count": 1,
            "item_loop": [{
                "text": msg,
                "showBriefly": 1,
            }],
        }

    return {"count": len(subs), "message": msg}


# ---------------------------------------------------------------------------
# podcast trending
# ---------------------------------------------------------------------------


async def _podcast_trending(
    ctx: CommandContext, command: list[Any]
) -> dict[str, Any]:
    """Handle ``podcast trending <start> <count> [lang:…] [cat:…] [menu:1]``."""
    tagged = _parse_tagged(command, start=2)
    start, count = _parse_start_count(command)
    is_menu = tagged.get("menu") == "1"

    return await _build_trending(start, count, is_menu, tagged)


# ---------------------------------------------------------------------------
# podcast info — detailed feed or episode information
# ---------------------------------------------------------------------------


async def _podcast_info(
    ctx: CommandContext, command: list[Any]
) -> dict[str, Any]:
    """Handle ``podcast info [url:…] [ep_url:…] [name:…] [menu:1]``.

    Shows detailed information about a feed (description, author, language,
    episode count, subscribe/unsubscribe option) or an episode (full
    description / show notes).
    """
    assert _store is not None

    tagged = _parse_tagged(command, start=2)
    feed_url = tagged.get("url", "")
    ep_url = tagged.get("ep_url", "")
    name = tagged.get("name", "")
    image = tagged.get("image", "")
    is_menu = tagged.get("menu") == "1"

    items: list[dict[str, Any]] = []

    if ep_url and feed_url:
        # Episode info — show description / notes
        try:
            feed = await _get_feed(feed_url)
            ep = next((e for e in feed.episodes if e.url == ep_url), None)
            if ep:
                if ep.description:
                    items.append({
                        "text": ep.description[:1000],
                        "type": "text" if is_menu else "info",
                    })
                if ep.published:
                    items.append({
                        "text": f"Published: {ep.published[:10]}",
                        "type": "text" if is_menu else "info",
                    })
                if ep.duration_seconds:
                    from .feed_parser import format_duration
                    items.append({
                        "text": f"Duration: {format_duration(ep.duration_seconds)}",
                        "type": "text" if is_menu else "info",
                    })
                if ep.season_number or ep.episode_number:
                    ep_info = ""
                    if ep.season_number:
                        ep_info += f"Season {ep.season_number}"
                    if ep.episode_number:
                        if ep_info:
                            ep_info += f", Episode {ep.episode_number}"
                        else:
                            ep_info = f"Episode {ep.episode_number}"
                    items.append({"text": ep_info, "type": "text" if is_menu else "info"})

                # Mark played/unplayed
                if _store.is_played(ep_url):
                    items.append({
                        "text": "Mark as unplayed",
                        "type": "link" if is_menu else "action",
                        "actions": {
                            "go": {
                                "cmd": ["podcast", "markunplayed"],
                                "params": {"url": ep_url, "menu": 1},
                            },
                        } if is_menu else {},
                    })
                else:
                    items.append({
                        "text": "Mark as played",
                        "type": "link" if is_menu else "action",
                        "actions": {
                            "go": {
                                "cmd": ["podcast", "markplayed"],
                                "params": {"url": ep_url, "menu": 1},
                            },
                        } if is_menu else {},
                    })
        except Exception as exc:
            items.append({"text": f"Error: {exc}", "type": "text"})

    elif feed_url:
        # Feed info
        is_subscribed = _store.is_subscribed(feed_url)

        # Subscribe / unsubscribe action
        if is_subscribed:
            items.append({
                "text": f"Unsubscribe from '{name}'",
                "type": "link" if is_menu else "action",
                "isContextMenu": 1 if is_menu else 0,
                "actions": {
                    "go": {
                        "cmd": ["podcast", "delshow"],
                        "params": {"url": feed_url, "name": name, "menu": 1},
                    },
                } if is_menu else {},
                "nextWindow": "grandparent" if is_menu else "",
            })
        else:
            items.append({
                "text": f"Subscribe to '{name}'",
                "type": "link" if is_menu else "action",
                "isContextMenu": 1 if is_menu else 0,
                "actions": {
                    "go": {
                        "cmd": ["podcast", "addshow"],
                        "params": {"url": feed_url, "name": name, "image": image, "menu": 1},
                    },
                } if is_menu else {},
                "nextWindow": "parent" if is_menu else "",
            })

        # Mark all as played
        if is_subscribed:
            items.append({
                "text": "Mark all episodes as played",
                "type": "link" if is_menu else "action",
                "actions": {
                    "go": {
                        "cmd": ["podcast", "markplayed"],
                        "params": {"feed_url": feed_url, "menu": 1},
                    },
                } if is_menu else {},
            })

        # Fetch feed metadata for detail display
        try:
            feed = await _get_feed(feed_url)
            if feed.description:
                items.append({"text": feed.description[:500], "type": "text"})
            if feed.author:
                items.append({"text": f"Author: {feed.author}", "type": "text"})
            if feed.language:
                items.append({"text": f"Language: {feed.language}", "type": "text"})
            if feed.categories:
                items.append({"text": f"Categories: {', '.join(feed.categories)}", "type": "text"})
            items.append({"text": f"Episodes: {len(feed.episodes)}", "type": "text"})
            if feed.link:
                items.append({"text": f"Website: {feed.link}", "type": "text"})
        except Exception as exc:
            items.append({"text": f"Could not load feed details: {exc}", "type": "text"})

    if not items:
        items.append({"text": "No information available", "type": "text"})

    result: dict[str, Any] = {"count": len(items), "offset": 0}
    result["item_loop" if is_menu else "loop"] = items
    return result


# ---------------------------------------------------------------------------
# podcast skip — skip forward / backward
# ---------------------------------------------------------------------------


async def _podcast_skip(
    ctx: CommandContext, command: list[Any]
) -> dict[str, Any]:
    """Handle ``podcast skip [direction:forward|back]``.

    Sends a seek command to the current player, jumping by the configured
    skip seconds.
    """
    tagged = _parse_tagged(command, start=2)
    direction = tagged.get("direction", "back")
    is_menu = tagged.get("menu") == "1"

    if direction == "forward":
        skip_secs = int(_setting("skip_forward_seconds", 30))
    else:
        skip_secs = int(_setting("skip_back_seconds", 15))
        skip_secs = -skip_secs

    # Get current player position
    player = None
    if ctx.player_id and ctx.player_id != "-":
        player = await ctx.player_registry.get_by_mac(ctx.player_id)

    if player is None:
        return {"error": "No player selected"}

    # Get current elapsed time from tracking
    tracking = _player_tracking.get(ctx.player_id, {})
    current_pos = tracking.get("elapsed", 0.0)
    new_pos = max(0, current_pos + skip_secs)

    abs_skip = abs(skip_secs)
    dir_text = "forward" if skip_secs > 0 else "back"

    if is_menu:
        return {
            "count": 1,
            "item_loop": [{
                "text": f"Skipped {abs_skip}s {dir_text}",
                "showBriefly": 1,
                "nowPlaying": 1,
            }],
        }

    return {
        "skip_seconds": skip_secs,
        "new_position": new_pos,
        "direction": dir_text,
    }


# ---------------------------------------------------------------------------
# podcast stats
# ---------------------------------------------------------------------------


def _podcast_stats() -> dict[str, Any]:
    """Return plugin statistics."""
    assert _store is not None
    stats = _store.get_stats()
    stats["provider"] = str(_setting("search_provider", "podcastindex"))
    stats["cache_size"] = len(_feed_cache)
    stats["tracking_players"] = len(_player_tracking)
    return stats


# ---------------------------------------------------------------------------
# Jive menu item builders
# ---------------------------------------------------------------------------


def _build_jive_episode_item(
    episode: Any,
    *,
    feed_url: str = "",
    feed_title: str = "",
    feed_image: str = "",
    show_feed_name: bool = False,
) -> dict[str, Any]:
    """Build a Jive-compatible menu item for a podcast episode."""
    from .feed_parser import format_duration

    title = episode.title
    if show_feed_name and feed_title:
        title = f"{feed_title} — {episode.title}"

    entry: dict[str, Any] = {
        "text": title,
        "type": "audio",
        "hasitems": 0,
        "playHoldAction": "go",
    }

    # Image: per-episode or feed-level
    image = episode.image_url or feed_image
    if image:
        entry["icon"] = image

    # Subtitle: date + duration + progress/resume info + played
    subtitle_parts: list[str] = []

    if show_feed_name and feed_title:
        # In "What's New" mode, show the feed name in textkey for context
        pass  # Already in title

    if episode.published:
        date_str = episode.published[:10] if len(episode.published) >= 10 else episode.published
        subtitle_parts.append(date_str)
    if episode.duration_seconds:
        subtitle_parts.append(format_duration(episode.duration_seconds))

    # Check for resume position and progress
    resume_pos = 0
    progress_pct = 0.0
    is_played = False
    if _store is not None:
        resume_pos = _store.get_resume_position(episode.url)
        progress_pct = _store.get_progress_percentage(episode.url)
        is_played = _store.is_played(episode.url)

        if is_played:
            subtitle_parts.append("✓ played")
        elif resume_pos > 0:
            subtitle_parts.append(f"from {format_duration(resume_pos)}")
            if progress_pct > 0:
                subtitle_parts.append(f"{progress_pct:.0f}%")

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

    # If there's a resume position, show a sub-menu for resume choice
    if resume_pos > 0 and episode.duration_seconds > 0 and not is_played:
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

    # Context menu: episode info + mark played/unplayed + add to favorites
    more_items: dict[str, Any] = {
        "cmd": ["podcast", "info"],
        "params": {
            "ep_url": episode.url,
            "url": feed_url,
            "name": episode.title,
            "image": image,
            "menu": 1,
        },
    }
    entry.setdefault("actions", {})["more"] = more_items

    return entry


def _build_jive_recent_item(ep: Any) -> dict[str, Any]:
    """Build a Jive-compatible menu item for a recently played episode."""
    from .feed_parser import format_duration

    entry: dict[str, Any] = {
        "text": ep.title or ep.url,
        "type": "audio",
        "hasitems": 0,
        "playHoldAction": "go",
    }

    if ep.image:
        entry["icon"] = ep.image

    subtitle_parts: list[str] = []
    if ep.show:
        subtitle_parts.append(ep.show)

    # Check progress
    if _store is not None:
        resume_pos = _store.get_resume_position(ep.url)
        is_played = _store.is_played(ep.url)
        if is_played:
            subtitle_parts.append("✓ played")
        elif resume_pos > 0:
            subtitle_parts.append(f"from {format_duration(resume_pos)}")
            pct = _store.get_progress_percentage(ep.url)
            if pct > 0:
                subtitle_parts.append(f"{pct:.0f}%")

    if subtitle_parts:
        entry["textkey"] = " · ".join(subtitle_parts)

    play_params: dict[str, Any] = {
        "url": ep.url,
        "title": ep.title,
        "icon": ep.image,
        "feed_url": ep.feed_url,
        "feed_title": ep.show,
        "duration": str(ep.duration),
    }

    # If there's a resume position, show resume sub-menu
    resume_pos = _store.get_resume_position(ep.url) if _store else 0
    is_played = _store.is_played(ep.url) if _store else False

    if resume_pos > 0 and ep.duration > 0 and not is_played:
        entry["type"] = "redirect"
        entry["hasitems"] = 1
        entry["actions"] = {
            "go": {
                "cmd": ["podcast", "items"],
                "params": {
                    "menu": 1,
                    "url": f"__resume__{ep.url}",
                    "resume_pos": str(resume_pos),
                    "ep_url": ep.url,
                    "ep_title": ep.title,
                    "ep_icon": ep.image,
                    "feed_url": ep.feed_url,
                    "feed_title": ep.show,
                    "duration": str(ep.duration),
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

    # Context menu: mark played/unplayed
    entry.setdefault("actions", {})["more"] = {
        "cmd": ["podcast", "info"],
        "params": {
            "ep_url": ep.url,
            "url": ep.feed_url,
            "name": ep.title,
            "image": ep.image,
            "menu": 1,
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

    # Subtitle: author + episode count + categories
    subtitle_parts: list[str] = []
    author = feed_data.get("author", "")
    if author:
        subtitle_parts.append(author)
    ep_count = feed_data.get("episode_count", 0)
    if ep_count:
        subtitle_parts.append(f"{ep_count} episodes")
    categories = feed_data.get("categories", [])
    if categories and isinstance(categories, list):
        subtitle_parts.append(", ".join(categories[:3]))

    if subtitle_parts:
        entry["textkey"] = " · ".join(subtitle_parts)

    feed_url = feed_data.get("url", "")

    # Is this feed already subscribed?
    is_subscribed = _store.is_subscribed(feed_url) if _store else False

    entry["actions"] = {
        "go": {
            "cmd": ["podcast", "items"],
            "params": {"url": feed_url, "menu": 1},
        },
        # Context menu: subscribe/unsubscribe + info
        "more": {
            "cmd": ["podcast", "info"],
            "params": {
                "url": feed_url,
                "name": feed_data.get("name", ""),
                "image": image,
                "menu": 1,
            },
        },
    }

    # Visual indicator if already subscribed
    if is_subscribed:
        entry["text"] = f"✓ {entry['text']}"

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
    if episode.description:
        entry["description"] = episode.description[:300]

    # Resume position and progress
    if _store is not None:
        resume_pos = _store.get_resume_position(episode.url)
        if resume_pos > 0:
            entry["resume_position"] = resume_pos

        progress_pct = _store.get_progress_percentage(episode.url)
        if progress_pct > 0:
            entry["progress_percent"] = progress_pct

        if _store.is_played(episode.url):
            entry["is_played"] = True

    return entry


def _build_cli_item_from_search(data: dict[str, Any]) -> dict[str, Any]:
    """Build a plain CLI item from a search result."""
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
    if data.get("categories"):
        entry["categories"] = data["categories"]
    if data.get("episode_count"):
        entry["episode_count"] = data["episode_count"]
    if data.get("language"):
        entry["language"] = data["language"]
    if data.get("provider"):
        entry["provider"] = data["provider"]

    # Indicate if already subscribed
    if _store is not None and data.get("url"):
        entry["is_subscribed"] = _store.is_subscribed(data["url"])

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
