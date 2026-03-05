"""
Radio Plugin for Resonance.

Internet Radio via **radio-browser.info** — a free, open, community-maintained
database of ~40 000+ Internet radio stations.  No API key or partner ID required.

Architecture
~~~~~~~~~~~~

* **RadioBrowserClient** (``radiobrowser.py``) wraps the radio-browser.info API.
* **RadioProvider** implements :class:`~resonance.content_provider.ContentProvider`
  so the server's generic content infrastructure can resolve streams.
* JSON-RPC commands provide the Jive-compatible menu interface:

  - ``radio items <start> <count>`` — browse categories/stations
  - ``radio search <start> <count>`` — search stations
  - ``radio play`` — play/add/insert a station

Menu entry
~~~~~~~~~~

A top-level **"Radio"** node appears in the Jive home menu (weight 45,
matching LMS ``RADIO`` placement — between "My Music" at 11 and
"Favorites" at 55).

Browse structure
~~~~~~~~~~~~~~~~

Top-level categories:
  - **Popular Stations** — sorted by community votes
  - **Trending Now** — sorted by recent clicks
  - **By Country** → country list → stations
  - **By Genre / Tag** → tag list → stations
  - **By Language** → language list → stations

Data source
~~~~~~~~~~~

https://www.radio-browser.info — open community project, free API,
pre-resolved stream URLs (no M3U/PLS resolution needed).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from resonance.web.jsonrpc_helpers import parse_start_count, parse_tagged_params

if TYPE_CHECKING:
    from resonance.content_provider import ContentProvider as ContentProviderBase
    from resonance.core.events import Event
    from resonance.plugin import PluginContext
    from resonance.web.handlers import CommandContext

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level state (set during setup, cleared during teardown)
# ---------------------------------------------------------------------------

_radio_browser: Any | None = None  # RadioBrowserClient instance
_provider: Any | None = None  # RadioProvider instance
_event_bus: Any | None = None  # EventBus reference


# ---------------------------------------------------------------------------
# ContentProvider implementation
# ---------------------------------------------------------------------------


class RadioProvider:
    """ContentProvider that wraps the radio-browser.info API.

    Registered under ``"radio"`` via ``PluginContext.register_content_provider()``.
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    @property
    def name(self) -> str:
        return "Community Radio Browser"

    @property
    def icon(self) -> str | None:
        return None

    async def browse(self, path: str = "") -> list[Any]:
        """Browse radio categories and stations.

        *path* encoding:
        - ``""`` → top-level category menu
        - ``"popular"`` → popular stations
        - ``"trending"`` → trending stations
        - ``"country"`` → country list
        - ``"country:DE"`` → stations in Germany
        - ``"tag"`` → tag/genre list
        - ``"tag:jazz"`` → stations tagged 'jazz'
        - ``"language"`` → language list
        - ``"language:german"`` → stations in German
        """
        from resonance.content_provider import BrowseItem

        from .radiobrowser import format_station_subtitle

        if not path:
            # Top-level categories
            categories = self._client.get_browse_categories()
            return [
                BrowseItem(
                    id=cat["key"],
                    title=cat["name"],
                    type="folder",
                    url=cat["key"],
                )
                for cat in categories
            ]

        if path == "popular":
            stations = await self._client.get_popular_stations(limit=200)
            return [self._station_to_browse_item(s) for s in stations]

        if path == "trending":
            stations = await self._client.get_trending_stations(limit=200)
            return [self._station_to_browse_item(s) for s in stations]

        if path == "country":
            countries = await self._client.get_countries(limit=200)
            return [
                BrowseItem(
                    id=f"country:{c.iso_3166_1}",
                    title=f"{c.name} ({c.stationcount})",
                    type="folder",
                    url=f"country:{c.iso_3166_1}",
                )
                for c in countries
                if c.iso_3166_1
            ]

        if path.startswith("country:"):
            code = path.split(":", 1)[1]
            stations = await self._client.get_stations_by_country(code, limit=200)
            return [self._station_to_browse_item(s) for s in stations]

        if path == "tag":
            tags = await self._client.get_tags(limit=200)
            return [
                BrowseItem(
                    id=f"tag:{t.name}",
                    title=f"{t.name} ({t.stationcount})",
                    type="folder",
                    url=f"tag:{t.name}",
                )
                for t in tags
            ]

        if path.startswith("tag:"):
            tag = path.split(":", 1)[1]
            stations = await self._client.get_stations_by_tag(tag, limit=200)
            return [self._station_to_browse_item(s) for s in stations]

        if path == "language":
            languages = await self._client.get_languages(limit=200)
            return [
                BrowseItem(
                    id=f"language:{l.name}",
                    title=f"{l.name} ({l.stationcount})",
                    type="folder",
                    url=f"language:{l.name}",
                )
                for l in languages
            ]

        if path.startswith("language:"):
            lang = path.split(":", 1)[1]
            stations = await self._client.get_stations_by_language(lang, limit=200)
            return [self._station_to_browse_item(s) for s in stations]

        return []

    def _station_to_browse_item(self, station: Any) -> Any:
        """Convert a RadioStation to a BrowseItem."""
        from resonance.content_provider import BrowseItem

        from .radiobrowser import format_station_subtitle

        return BrowseItem(
            id=station.stationuuid,
            title=station.name,
            type="audio",
            url=station.url_resolved or station.url,
            icon=station.favicon or None,
            subtitle=format_station_subtitle(station),
            extra={
                k: v for k, v in {
                    "bitrate": str(station.bitrate) if station.bitrate else "",
                    "codec": station.codec,
                    "country": station.country,
                    "countrycode": station.countrycode,
                    "tags": station.tags,
                    "votes": str(station.votes) if station.votes else "",
                    "stationuuid": station.stationuuid,
                    "homepage": station.homepage,
                }.items() if v
            },
        )

    async def search(self, query: str) -> list[Any]:
        """Search radio-browser.info for stations."""
        from .radiobrowser import format_station_subtitle

        stations = await self._client.search(query, limit=200)
        return [self._station_to_browse_item(s) for s in stations]

    async def get_stream_info(self, item_id: str) -> Any | None:
        """Resolve a station UUID to stream info.

        radio-browser.info already provides ``url_resolved`` — no additional
        resolution step needed (unlike TuneIn).
        """
        from resonance.content_provider import StreamInfo

        from .radiobrowser import codec_to_content_type

        station = await self._client.get_station_by_uuid(item_id)
        if station is None:
            return None

        stream_url = station.url_resolved or station.url
        content_type = codec_to_content_type(station.codec)

        return StreamInfo(
            url=stream_url,
            content_type=content_type,
            bitrate=station.bitrate,
            is_live=True,
        )

    async def on_stream_started(self, item_id: str, player_mac: str) -> None:
        """Count the click when a station starts playing."""
        logger.info(
            "Radio stream started: station=%s player=%s", item_id, player_mac
        )
        # Register the play with radio-browser.info (best-effort).
        if _radio_browser is not None:
            await _radio_browser.count_click(item_id)

    async def on_stream_stopped(self, item_id: str, player_mac: str) -> None:
        logger.debug(
            "Radio stream stopped: station=%s player=%s", item_id, player_mac
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
    global _radio_browser, _provider, _event_bus

    from .radiobrowser import RadioBrowserClient

    _radio_browser = RadioBrowserClient()
    await _radio_browser.start()

    _provider = RadioProvider(_radio_browser)
    _event_bus = ctx.event_bus

    # ── Register content provider ──────────────────────────────
    ctx.register_content_provider("radio", _provider)

    # ── Commands ───────────────────────────────────────────────
    ctx.register_command("radio", cmd_radio)

    # ── Jive main-menu: "Radio" node ───────────────────────────
    # Weight 45 places it between "My Music" (11) and "Favorites" (55),
    # matching LMS's RADIO menu placement.
    ctx.register_menu_node(
        node_id="radios",
        parent="home",
        text="Radio",
        weight=45,
        actions={
            "go": {
                "cmd": ["radio", "items"],
                "params": {"menu": 1},
            },
        },
        window={"titleStyle": "album"},
    )

    logger.info("Radio plugin started (radio-browser.info)")


async def teardown(ctx: PluginContext) -> None:
    """Called by PluginManager during server shutdown."""
    global _radio_browser, _provider, _event_bus

    if _radio_browser is not None:
        await _radio_browser.close()
    _radio_browser = None
    _provider = None
    _event_bus = None
    logger.info("Radio plugin stopped")


# ---------------------------------------------------------------------------
# JSON-RPC command dispatcher
# ---------------------------------------------------------------------------


async def cmd_radio(
    ctx: CommandContext, command: list[Any]
) -> dict[str, Any]:
    """Dispatch ``radio <sub-command> …`` to the appropriate handler.

    Sub-commands:
    - ``items``  — browse radio categories/stations
    - ``search`` — search stations
    - ``play``   — play/add/insert a station
    """
    if _radio_browser is None:
        return {"error": "Radio plugin not initialized"}

    sub = str(command[1]).lower() if len(command) > 1 else "items"

    match sub:
        case "items":
            return await _radio_items(ctx, command)
        case "search":
            return await _radio_search(ctx, command)
        case "play":
            return await _radio_play(ctx, command)
        case _:
            return {"error": f"Unknown radio sub-command: {sub}"}


# ---------------------------------------------------------------------------
# radio items — browse
# ---------------------------------------------------------------------------

# Category keys that map to station lists (not sub-category lists).
_STATION_CATEGORIES = {"popular", "trending"}


async def _radio_items(
    ctx: CommandContext, command: list[Any]
) -> dict[str, Any]:
    """Handle ``radio items <start> <count> [category:…] [menu:1] [search:…]``.

    Browse structure:
    - No ``category`` → top-level menu (Popular, Trending, By Country, …)
    - ``category:popular`` → popular stations
    - ``category:trending`` → trending stations
    - ``category:country`` → country list
    - ``category:country:DE`` → stations in Germany
    - ``category:tag`` → genre/tag list
    - ``category:tag:jazz`` → stations tagged 'jazz'
    - ``category:language`` → language list
    - ``category:language:german`` → German-language stations
    """
    assert _radio_browser is not None

    tagged = _parse_tagged(command, start=2)
    start, count = _parse_start_count(command)
    category = tagged.get("category", tagged.get("url", ""))
    is_menu = tagged.get("menu") == "1"
    search_query = tagged.get("search", "")

    from .radiobrowser import (
        BROWSE_CATEGORIES,
        RadioStation,
        format_station_subtitle,
    )

    all_items: list[dict[str, Any]] = []

    if search_query:
        # Inline search from Jive input field.
        stations = await _radio_browser.search(search_query, limit=200)
        for s in stations:
            if is_menu:
                all_items.append(_build_station_jive_item(s))
            else:
                all_items.append(_build_station_cli_item(s))

    elif not category:
        # Top-level browse menu.
        for cat in BROWSE_CATEGORIES:
            if is_menu:
                all_items.append(_build_category_jive_item(cat["key"], cat["name"]))
            else:
                all_items.append({
                    "name": cat["name"],
                    "type": "link",
                    "hasitems": 1,
                    "category": cat["key"],
                })

    elif category == "popular":
        stations = await _radio_browser.get_popular_stations(limit=200)
        for s in stations:
            all_items.append(
                _build_station_jive_item(s) if is_menu else _build_station_cli_item(s)
            )

    elif category == "trending":
        stations = await _radio_browser.get_trending_stations(limit=200)
        for s in stations:
            all_items.append(
                _build_station_jive_item(s) if is_menu else _build_station_cli_item(s)
            )

    elif category == "country":
        countries = await _radio_browser.get_countries(limit=200)
        for c in countries:
            if not c.iso_3166_1:
                continue
            key = f"country:{c.iso_3166_1}"
            name = f"{c.name} ({c.stationcount})"
            if is_menu:
                all_items.append(_build_subcategory_jive_item(key, name))
            else:
                all_items.append({
                    "name": name,
                    "type": "link",
                    "hasitems": 1,
                    "category": key,
                })

    elif category.startswith("country:"):
        code = category.split(":", 1)[1]
        stations = await _radio_browser.get_stations_by_country(code, limit=200)
        for s in stations:
            all_items.append(
                _build_station_jive_item(s) if is_menu else _build_station_cli_item(s)
            )

    elif category == "tag":
        tags = await _radio_browser.get_tags(limit=200)
        for t in tags:
            key = f"tag:{t.name}"
            name = f"{t.name} ({t.stationcount})"
            if is_menu:
                all_items.append(_build_subcategory_jive_item(key, name))
            else:
                all_items.append({
                    "name": name,
                    "type": "link",
                    "hasitems": 1,
                    "category": key,
                })

    elif category.startswith("tag:"):
        tag = category.split(":", 1)[1]
        stations = await _radio_browser.get_stations_by_tag(tag, limit=200)
        for s in stations:
            all_items.append(
                _build_station_jive_item(s) if is_menu else _build_station_cli_item(s)
            )

    elif category == "language":
        languages = await _radio_browser.get_languages(limit=200)
        for lang_entry in languages:
            key = f"language:{lang_entry.name}"
            name = f"{lang_entry.name} ({lang_entry.stationcount})"
            if is_menu:
                all_items.append(_build_subcategory_jive_item(key, name))
            else:
                all_items.append({
                    "name": name,
                    "type": "link",
                    "hasitems": 1,
                    "category": key,
                })

    elif category.startswith("language:"):
        lang = category.split(":", 1)[1]
        stations = await _radio_browser.get_stations_by_language(lang, limit=200)
        for s in stations:
            all_items.append(
                _build_station_jive_item(s) if is_menu else _build_station_cli_item(s)
            )

    # Paginate.
    total = len(all_items)
    page = all_items[start : start + count]

    result: dict[str, Any] = {
        "count": total,
        "offset": start,
    }

    loop_key = "item_loop" if is_menu else "loop"
    result[loop_key] = page

    if is_menu:
        result["base"] = _base_actions()

    return result


# ---------------------------------------------------------------------------
# radio search — search stations
# ---------------------------------------------------------------------------


async def _radio_search(
    ctx: CommandContext, command: list[Any]
) -> dict[str, Any]:
    """Handle ``radio search <start> <count> [term:…] [menu:1]``.

    Searches radio-browser.info by station name and returns results.
    """
    assert _radio_browser is not None

    tagged = _parse_tagged(command, start=2)
    start, count = _parse_start_count(command)
    query = tagged.get("term", tagged.get("search", tagged.get("query", "")))
    is_menu = tagged.get("menu") == "1"

    if not query:
        result: dict[str, Any] = {"count": 0, "offset": 0}
        result["item_loop" if is_menu else "loop"] = []
        return result

    stations = await _radio_browser.search(query, limit=200)

    all_items: list[dict[str, Any]] = []
    for s in stations:
        if is_menu:
            all_items.append(_build_station_jive_item(s))
        else:
            all_items.append(_build_station_cli_item(s))

    total = len(all_items)
    page = all_items[start : start + count]

    result = {
        "count": total,
        "offset": start,
    }
    loop_key = "item_loop" if is_menu else "loop"
    result[loop_key] = page

    if is_menu:
        result["base"] = _base_actions()

    return result


# ---------------------------------------------------------------------------
# radio play — play a station
# ---------------------------------------------------------------------------


async def _radio_play(
    ctx: CommandContext, command: list[Any]
) -> dict[str, Any]:
    """Handle ``radio play [url:…] [id:…] [title:…] [icon:…] [cmd:…]``.

    Starts playback of a radio station.  The stream URL is used directly
    (radio-browser.info provides pre-resolved URLs via ``url_resolved``).

    Params:
        url: Direct stream URL (from ``url_resolved``)
        id: Station UUID (for click counting and metadata lookup)
        title: Display title
        icon: Artwork/favicon URL
        cmd: ``"play"`` (default), ``"add"``, ``"insert"``
        codec: Audio codec hint (e.g. ``"MP3"``, ``"AAC"``)
        bitrate: Bitrate in kbps
    """
    assert _radio_browser is not None

    tagged = _parse_tagged(command, start=2)
    stream_url = tagged.get("url", "")
    station_uuid = tagged.get("id", "")
    title = tagged.get("title", "")
    icon = tagged.get("icon", "")
    play_cmd = tagged.get("cmd", "play")
    codec = tagged.get("codec", "")
    bitrate_str = tagged.get("bitrate", "0")

    logger.info(
        "[RADIO-PLAY] params: url=%s id=%s title=%s icon=%s cmd=%s",
        stream_url[:80] if stream_url else "<empty>",
        station_uuid or "<empty>",
        title or "<empty>",
        icon[:120] if icon else "<NONE>",
        play_cmd,
    )

    if not stream_url and not station_uuid:
        return {"error": "Missing 'url' or 'id' parameter"}

    # If we have a UUID but no URL, look up the station.
    if not stream_url and station_uuid:
        station = await _radio_browser.get_station_by_uuid(station_uuid)
        if station is None:
            return {"error": f"Station not found: {station_uuid}"}
        stream_url = station.url_resolved or station.url
        if not title:
            title = station.name
        if not icon:
            icon = station.favicon
        if not codec:
            codec = station.codec
        if not bitrate_str or bitrate_str == "0":
            bitrate_str = str(station.bitrate)

    if not stream_url:
        return {"error": "Could not determine stream URL"}

    # Determine content type from codec.
    from .radiobrowser import codec_to_content_type

    content_type = codec_to_content_type(codec) if codec else "audio/mpeg"

    bitrate = 0
    try:
        bitrate = int(bitrate_str)
    except (ValueError, TypeError):
        pass

    logger.info(
        "Resolved radio stream: station=%s codec=%s bitrate=%s url=%s",
        station_uuid or title,
        codec,
        bitrate,
        stream_url,
    )

    # Build a PlaylistTrack for the radio stream.
    from resonance.core.playlist import PlaylistTrack

    _artwork = icon or None
    logger.info(
        "[RADIO-PLAY] creating PlaylistTrack: title=%s artwork_url=%s",
        title or "Radio Station",
        _artwork[:120] if _artwork else "<NONE>",
    )

    track = PlaylistTrack.from_url(
        url=stream_url,
        title=title or "Radio Station",
        source="radio",
        stream_url=stream_url,
        external_id=station_uuid,
        artwork_url=_artwork,
        content_type=content_type,
        bitrate=bitrate,
        is_live=True,
    )

    # Get player.
    player = None
    if ctx.player_id and ctx.player_id != "-":
        player = await ctx.player_registry.get_by_mac(ctx.player_id)

    if player is None:
        return {"error": "No player selected"}

    if ctx.playlist_manager is None:
        return {"error": "Playlist manager not available"}

    playlist = ctx.playlist_manager.get(ctx.player_id)

    # Execute play/add/insert.
    if play_cmd == "add":
        playlist.add(track)
        logger.info("Added radio station to playlist: %s (%s)", title, station_uuid)
    elif play_cmd == "insert":
        insert_idx = playlist.current_index + 1
        playlist.insert(insert_idx, track)
        logger.info("Inserted radio station into playlist: %s (%s)", title, station_uuid)
    else:
        # play — replace playlist and start playback.
        logger.info(
            "Radio play: stopping current playback and replacing queue for player %s",
            ctx.player_id,
        )
        playlist.clear()
        playlist.add(track)
        playlist.play(0)

        # Start streaming.
        from resonance.web.handlers.playlist_playback import _start_track_stream

        logger.info(
            "Radio play: starting stream for player %s — url=%s content_type=%s is_live=%s",
            ctx.player_id, track.effective_stream_url, content_type, track.is_live,
        )

        # Debug: check prerequisites
        logger.info(
            "Radio play: ctx.streaming_server=%s ctx.slimproto=%s player=%s",
            ctx.streaming_server is not None,
            ctx.slimproto is not None,
            player,
        )
        _state = getattr(getattr(player, "status", None), "state", None)
        _state_name = str(getattr(_state, "name", _state)).upper() if _state else "UNKNOWN"
        logger.info(
            "Radio play: player state before _start_track_stream: %s",
            _state_name,
        )

        try:
            await _start_track_stream(ctx, player, track)
            logger.info(
                "Radio play: _start_track_stream completed successfully for player %s",
                ctx.player_id,
            )
        except Exception:
            logger.exception(
                "Radio play: _start_track_stream FAILED for player %s",
                ctx.player_id,
            )
            raise

        # Check state after stream start
        _state2 = getattr(getattr(player, "status", None), "state", None)
        _state2_name = str(getattr(_state2, "name", _state2)).upper() if _state2 else "UNKNOWN"
        logger.info(
            "Radio play: player state after _start_track_stream: %s",
            _state2_name,
        )

        # Publish playlist event so Cometd/Web-UI see the change immediately.
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

        logger.info(
            "Playing radio station: %s (%s) → %s",
            title, station_uuid, stream_url,
        )

    # Count click (best-effort, non-blocking).
    if station_uuid:
        try:
            await _radio_browser.count_click(station_uuid)
        except Exception:
            pass  # Click counting is optional.

    return {"count": 1}


# ---------------------------------------------------------------------------
# Jive menu item builders
# ---------------------------------------------------------------------------


def _build_category_jive_item(key: str, name: str) -> dict[str, Any]:
    """Build a Jive menu item for a top-level browse category."""
    entry: dict[str, Any] = {
        "text": name,
        "hasitems": 1,
        "actions": {
            "go": {
                "cmd": ["radio", "items"],
                "params": {"category": key, "menu": 1},
            },
        },
    }

    # Add search input for the top-level menu.
    if key == "popular":
        entry["icon-id"] = "plugins/TuneIn/html/images/radiomusic.png"
    elif key == "trending":
        entry["icon-id"] = "plugins/TuneIn/html/images/radionews.png"
    elif key == "country":
        entry["icon-id"] = "plugins/TuneIn/html/images/radioworld.png"
    elif key == "tag":
        entry["icon-id"] = "plugins/TuneIn/html/images/radiomusic.png"
    elif key == "language":
        entry["icon-id"] = "plugins/TuneIn/html/images/radiotalk.png"

    return entry


def _build_subcategory_jive_item(key: str, name: str) -> dict[str, Any]:
    """Build a Jive menu item for a sub-category (country, tag, language)."""
    return {
        "text": name,
        "hasitems": 1,
        "actions": {
            "go": {
                "cmd": ["radio", "items"],
                "params": {"category": key, "menu": 1},
            },
        },
    }


def _build_station_jive_item(station: Any) -> dict[str, Any]:
    """Build a Jive-compatible menu item for a RadioStation."""
    from .radiobrowser import format_station_subtitle

    entry: dict[str, Any] = {
        "text": station.name,
        "type": "audio",
        "hasitems": 0,
        "playHoldAction": "go",
    }

    if station.favicon:
        entry["icon"] = station.favicon

    subtitle = format_station_subtitle(station)
    if subtitle:
        entry["textkey"] = subtitle

    # Station actions: play, add, go (= play).
    station_params = {
        "url": station.url_resolved or station.url,
        "id": station.stationuuid,
        "title": station.name,
        "icon": station.favicon,
        "codec": station.codec,
        "bitrate": str(station.bitrate) if station.bitrate else "0",
    }

    entry["actions"] = {
        "play": {
            "player": 0,
            "cmd": ["radio", "play"],
            "params": {**station_params, "cmd": "play"},
        },
        "add": {
            "player": 0,
            "cmd": ["radio", "play"],
            "params": {**station_params, "cmd": "add"},
        },
        "go": {
            "player": 0,
            "cmd": ["radio", "play"],
            "params": {**station_params, "cmd": "play"},
        },
    }

    # Add-to-favorites context action.
    if station.stationuuid:
        entry["actions"]["more"] = {
            "player": 0,
            "cmd": ["jivefavorites", "add"],
            "params": {
                "title": station.name,
                "url": station.url_resolved or station.url,
                "type": "audio",
                "icon": station.favicon,
            },
        }

    return entry


def _build_station_cli_item(station: Any) -> dict[str, Any]:
    """Build a plain CLI response item for a RadioStation."""
    from .radiobrowser import format_station_subtitle

    entry: dict[str, Any] = {
        "name": station.name,
        "type": "audio",
        "url": station.url_resolved or station.url,
        "id": station.stationuuid,
    }

    if station.favicon:
        entry["icon"] = station.favicon
    if station.codec:
        entry["codec"] = station.codec
    if station.bitrate:
        entry["bitrate"] = station.bitrate
    if station.country:
        entry["country"] = station.country
    if station.countrycode:
        entry["countrycode"] = station.countrycode
    if station.tags:
        entry["tags"] = station.tags
    if station.votes:
        entry["votes"] = station.votes
    if station.homepage:
        entry["homepage"] = station.homepage

    subtitle = format_station_subtitle(station)
    if subtitle:
        entry["subtext"] = subtitle

    return entry


def _base_actions() -> dict[str, Any]:
    """Base actions for Jive menu responses.

    These are merged with per-item actions by the Jive device.
    """
    return {
        "actions": {
            "go": {
                "cmd": ["radio", "items"],
                "params": {"menu": 1},
                "itemsParams": "params",
            },
            "play": {
                "player": 0,
                "cmd": ["radio", "play"],
                "itemsParams": "params",
            },
            "add": {
                "player": 0,
                "cmd": ["radio", "play"],
                "params": {"cmd": "add"},
                "itemsParams": "params",
            },
        },
    }
