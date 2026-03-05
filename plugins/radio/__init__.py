"""
Radio Plugin for Resonance.

Internet Radio via **radio-browser.info** and **TuneIn** — dual-provider
support with a SDUI dashboard, recently played stations, configurable
settings, and full Jive menu integration.

Architecture
~~~~~~~~~~~~

* **RadioBrowserClient** (``radiobrowser.py``) wraps the radio-browser.info API.
* **TuneInClient** (``tunein.py``) wraps the TuneIn/RadioTime OPML API.
* **RadioProvider** / **TuneInProvider** implement
  :class:`~resonance.content_provider.ContentProvider` for the server's
  generic content infrastructure.
* **RadioStore** (``store.py``) persists recently played stations.
* JSON-RPC commands provide the Jive-compatible menu interface:

  - ``radio items <start> <count>`` — browse categories/stations
  - ``radio search <start> <count>`` — search stations
  - ``radio play`` — play/add/insert a station

* SDUI page provides a Web-UI dashboard with tabs:

  - **Recent** — recently played stations (click to replay)
  - **Browse** — quick-access category cards
  - **Settings** — provider choice, default country, cache TTL

Menu entry
~~~~~~~~~~

A top-level **"Radio"** node appears in the Jive home menu (weight 45,
matching LMS ``RADIO`` placement — between "My Music" at 11 and
"Favorites" at 55).

Browse structure (radio-browser.info)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

  - **Popular Stations** — sorted by community votes
  - **Trending Now** — sorted by recent clicks
  - **By Country** → country list → stations
  - **By Genre / Tag** → tag list → stations
  - **By Language** → language list → stations

Browse structure (TuneIn)
~~~~~~~~~~~~~~~~~~~~~~~~~

  - **Local Radio** — GeoIP-based local stations
  - **Music** / **News** / **Sports** / **Talk** — TuneIn categories
  - **By Location** / **By Language** — hierarchical browsing
  - **Podcasts** — TuneIn podcast directory (overlaps with podcast plugin)

Data sources
~~~~~~~~~~~~

* https://www.radio-browser.info — open community project, free API,
  pre-resolved stream URLs.  No API key required.
* https://opml.radiotime.com — TuneIn/RadioTime OPML API, same as LMS
  uses with Partner ID 16.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from resonance.web.jsonrpc_helpers import parse_start_count, parse_tagged_params

if TYPE_CHECKING:
    from resonance.core.events import Event
    from resonance.plugin import PluginContext
    from resonance.web.handlers import CommandContext

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level state (set during setup, cleared during teardown)
# ---------------------------------------------------------------------------

_radio_browser: Any | None = None  # RadioBrowserClient instance
_tunein_client: Any | None = None  # TuneInClient instance
_provider: Any | None = None  # RadioProvider (radio-browser) instance
_tunein_provider: Any | None = None  # TuneInProvider instance
_event_bus: Any | None = None  # EventBus reference
_store: Any | None = None  # RadioStore instance
_ctx: Any | None = None  # PluginContext reference
_http_client: Any | None = None  # httpx.AsyncClient for JSON-RPC self-calls


# ---------------------------------------------------------------------------
# Settings helper
# ---------------------------------------------------------------------------


def _setting(key: str, default: Any = "") -> Any:
    """Read a plugin setting, returning *default* if unavailable."""
    if _ctx is None:
        return default
    try:
        val = _ctx.get_setting(key)
        return val if val is not None else default
    except Exception:
        return default


def _preferred_provider() -> str:
    """Return the configured preferred provider."""
    return str(_setting("preferred_provider", "radio-browser"))


# ---------------------------------------------------------------------------
# ContentProvider implementation — radio-browser.info
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
# ContentProvider implementation — TuneIn
# ---------------------------------------------------------------------------


class TuneInProvider:
    """ContentProvider that wraps the TuneIn/RadioTime OPML API.

    Registered under ``"tunein"`` via ``PluginContext.register_content_provider()``.
    Uses the same Partner ID (16) as LMS.
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    @property
    def name(self) -> str:
        return "TuneIn Radio"

    @property
    def icon(self) -> str | None:
        return None

    async def browse(self, path: str = "") -> list[Any]:
        """Browse TuneIn categories and stations.

        *path* encoding:
        - ``""`` → TuneIn root menu (Local, Music, News, Sports, ...)
        - ``"<tunein_url>"`` → any TuneIn Browse.ashx URL
        """
        from resonance.content_provider import BrowseItem

        from .tunein import flatten_items, is_tune_url

        if not path:
            items = await self._client.fetch_root()
        else:
            items = await self._client.browse(path)

        items = flatten_items(items)
        result: list[BrowseItem] = []

        for item in items:
            if item.type == "audio":
                result.append(BrowseItem(
                    id=item.guide_id or item.url,
                    title=item.text,
                    type="audio",
                    url=item.url,
                    icon=item.image or None,
                    subtitle=item.subtext or None,
                    extra={
                        k: v for k, v in {
                            "guide_id": item.guide_id,
                            "bitrate": item.bitrate,
                            "formats": item.formats,
                            "reliability": item.reliability,
                            "playing": item.playing,
                        }.items() if v
                    },
                ))
            elif item.type == "link" and item.url:
                result.append(BrowseItem(
                    id=item.guide_id or item.url,
                    title=item.text,
                    type="folder",
                    url=item.url,
                    icon=item.image or None,
                    subtitle=item.subtext or None,
                ))
            elif item.type == "search":
                result.append(BrowseItem(
                    id="tunein-search",
                    title=item.text or "Search",
                    type="search",
                    url=item.url,
                ))

        return result

    async def search(self, query: str) -> list[Any]:
        """Search TuneIn for stations/shows."""
        from resonance.content_provider import BrowseItem

        from .tunein import flatten_items

        items = await self._client.search(query)
        items = flatten_items(items)

        result: list[BrowseItem] = []
        for item in items:
            if item.type == "audio":
                result.append(BrowseItem(
                    id=item.guide_id or item.url,
                    title=item.text,
                    type="audio",
                    url=item.url,
                    icon=item.image or None,
                    subtitle=item.subtext or None,
                    extra={
                        k: v for k, v in {
                            "guide_id": item.guide_id,
                            "bitrate": item.bitrate,
                            "formats": item.formats,
                        }.items() if v
                    },
                ))
            elif item.type == "link" and item.url:
                result.append(BrowseItem(
                    id=item.guide_id or item.url,
                    title=item.text,
                    type="folder",
                    url=item.url,
                    icon=item.image or None,
                    subtitle=item.subtext or None,
                ))

        return result

    async def get_stream_info(self, item_id: str) -> Any | None:
        """Resolve a TuneIn station to stream info.

        Unlike radio-browser, TuneIn requires a ``Tune.ashx`` call to
        resolve the final stream URL (which may be behind M3U/PLS playlists).
        """
        from resonance.content_provider import StreamInfo

        from .tunein import content_type_for_media, extract_station_id

        # item_id may be a guide_id (s12345) or a Tune.ashx URL.
        station_id = extract_station_id(item_id) if "radiotime.com" in item_id else item_id

        if not station_id:
            logger.warning("TuneIn: cannot extract station ID from %s", item_id)
            return None

        stream = await self._client.tune(station_id)
        if stream is None:
            logger.warning("TuneIn: failed to resolve stream for %s", station_id)
            return None

        content_type = content_type_for_media(stream.media_type)

        return StreamInfo(
            url=stream.url,
            content_type=content_type,
            bitrate=stream.bitrate,
            is_live=True,
        )

    async def on_stream_started(self, item_id: str, player_mac: str) -> None:
        logger.info(
            "TuneIn stream started: station=%s player=%s", item_id, player_mac
        )

    async def on_stream_stopped(self, item_id: str, player_mac: str) -> None:
        logger.debug(
            "TuneIn stream stopped: station=%s player=%s", item_id, player_mac
        )


# ---------------------------------------------------------------------------
# Helpers — parameter parsing
# ---------------------------------------------------------------------------


def _parse_tagged(command: list[Any], start: int = 1) -> dict[str, str]:
    """Parse ``key:value`` tagged params from *command* at *start*."""
    return parse_tagged_params(command[start:])


def _parse_start_count(command: list[Any]) -> tuple[int, int]:
    """Parse ``<start> <count>`` from *command*."""
    return parse_start_count(command)


# ---------------------------------------------------------------------------
# Plugin lifecycle
# ---------------------------------------------------------------------------


async def setup(ctx: PluginContext) -> None:
    """Called by PluginManager during server startup."""
    global _radio_browser, _tunein_client, _provider, _tunein_provider
    global _event_bus, _store, _ctx, _http_client

    import httpx

    from .radiobrowser import RadioBrowserClient
    from .store import RadioStore
    from .tunein import TuneInClient

    _ctx = ctx
    _event_bus = ctx.event_bus
    _http_client = httpx.AsyncClient(follow_redirects=True, timeout=15.0)

    # ── Read settings ──────────────────────────────────────────
    cache_ttl = int(_setting("cache_ttl", 600))
    max_recent = int(_setting("max_recent_stations", 50))
    provider_pref = _preferred_provider()

    # ── Create store ───────────────────────────────────────────
    data_dir = ctx.ensure_data_dir()
    _store = RadioStore(data_dir, max_recent=max_recent)
    _store.load()

    # ── Create radio-browser client (always, it's free) ────────
    _radio_browser = RadioBrowserClient(cache_ttl=float(cache_ttl))
    await _radio_browser.start()
    _provider = RadioProvider(_radio_browser)

    # ── Register radio-browser content provider ────────────────
    ctx.register_content_provider("radio", _provider)

    # ── Create TuneIn client (always available, even if not preferred) ──
    _tunein_client = TuneInClient(cache_ttl=float(cache_ttl))
    await _tunein_client.start()
    _tunein_provider = TuneInProvider(_tunein_client)

    # Register TuneIn as a separate content provider.
    ctx.register_content_provider("tunein", _tunein_provider)

    # ── Commands ───────────────────────────────────────────────
    ctx.register_command("radio", cmd_radio)

    # ── Subscribe to track events for recently-played tracking ─
    await ctx.subscribe("player.track_started", _on_track_started)

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

    # ── SDUI ───────────────────────────────────────────────────
    ctx.register_ui_handler(get_ui)
    ctx.register_action_handler(handle_action)

    logger.info(
        "Radio plugin v2.2 started (provider=%s, %d recent stations, "
        "radio-browser=active, tunein=active)",
        provider_pref, _store.recent_count,
    )


async def teardown(ctx: PluginContext) -> None:
    """Called by PluginManager during server shutdown."""
    global _radio_browser, _tunein_client, _provider, _tunein_provider
    global _event_bus, _store, _ctx, _http_client

    # Save recently played stations.
    if _store is not None:
        _store.save()

    if _radio_browser is not None:
        await _radio_browser.close()

    if _tunein_client is not None:
        await _tunein_client.close()

    if _http_client is not None:
        await _http_client.aclose()

    _radio_browser = None
    _tunein_client = None
    _provider = None
    _tunein_provider = None
    _event_bus = None
    _store = None
    _http_client = None
    _ctx = None
    logger.info("Radio plugin stopped")


# ---------------------------------------------------------------------------
# Event handler — track recently played stations
# ---------------------------------------------------------------------------


async def _on_track_started(event: Event) -> None:
    """Record radio station plays into the persistent store."""
    if _store is None:
        return

    # Only track radio sources.
    source = getattr(event, "source", "")
    if source not in ("radio", "tunein"):
        # Check if it's a radio stream by looking at the track metadata.
        track = getattr(event, "track", None)
        if track is None:
            return
        track_source = getattr(track, "source", "")
        if track_source not in ("radio", "tunein"):
            return
        source = track_source
        # Extract station metadata from the track.
        url = getattr(track, "effective_stream_url", "") or getattr(track, "url", "")
        title = getattr(track, "title", "")
        icon = getattr(track, "artwork_url", "") or ""
        codec = getattr(track, "content_type", "")
        bitrate = getattr(track, "bitrate", 0) or 0
        station_id = getattr(track, "external_id", "") or ""
    else:
        track = getattr(event, "track", None)
        url = getattr(track, "effective_stream_url", "") or getattr(track, "url", "") if track else ""
        title = getattr(track, "title", "") if track else ""
        icon = getattr(track, "artwork_url", "") or "" if track else ""
        codec = getattr(track, "content_type", "") if track else ""
        bitrate = getattr(track, "bitrate", 0) or 0 if track else 0
        station_id = getattr(track, "external_id", "") or "" if track else ""

    if not url:
        return

    provider = "tunein" if source == "tunein" else "radio-browser"

    entry = _store.record_play(
        url=url,
        title=title,
        icon=icon,
        codec=codec,
        bitrate=bitrate,
        station_id=station_id,
        provider=provider,
    )
    _store.save()

    logger.debug(
        "Recorded radio play: %s (count=%d, provider=%s)",
        entry.title or url[:60], entry.play_count, provider,
    )

    # Notify the SDUI frontend to refresh.
    if _ctx is not None:
        _ctx.notify_ui_update()


# ---------------------------------------------------------------------------
# SDUI — get_ui / handle_action
# ---------------------------------------------------------------------------


async def get_ui(ctx: PluginContext) -> Any:
    """Build the SDUI page for the Radio plugin."""
    from resonance.ui import (
        Alert,
        Button,
        Card,
        Column,
        Form,
        Heading,
        KeyValue,
        KVItem,
        Markdown,
        Page,
        Row,
        Select,
        SelectOption,
        StatusBadge,
        Tab,
        Table,
        TableAction,
        TableColumn,
        Tabs,
        Text,
        TextInput,
        Toggle,
    )

    recent_tab = _build_recent_tab()
    browse_tab = _build_browse_tab()
    settings_tab = _build_settings_tab()
    about_tab = _build_about_tab()

    return Page(
        title="Radio",
        icon="radio",
        refresh_interval=30,
        components=[
            Tabs(tabs=[
                recent_tab,
                browse_tab,
                settings_tab,
                about_tab,
            ]),
        ],
    )


def _build_recent_tab() -> Any:
    """Build the 'Recent' tab showing recently played stations."""
    from resonance.ui import (
        Alert,
        Button,
        Card,
        Heading,
        KeyValue,
        KVItem,
        Row,
        StatusBadge,
        Tab,
        Table,
        TableAction,
        TableColumn,
        Text,
    )

    if _store is None or _store.recent_count == 0:
        return Tab(label="Recent", children=[
            Card(title="Recently Played", children=[
                Alert(
                    message="No stations played yet. Browse or search for radio stations to get started!",
                    severity="info",
                ),
            ]),
        ])

    recent = _store.recent
    most_played = _store.get_most_played(limit=5)

    # Build table columns.
    columns = [
        TableColumn(key="title", label="Station"),
        TableColumn(key="info", label="Info"),
        TableColumn(key="provider", label="Source"),
        TableColumn(key="plays", label="Plays"),
        TableColumn(key="actions", label="Actions", variant="actions"),
    ]

    # Build table rows from recent stations.
    rows: list[dict[str, Any]] = []
    for station in recent:
        info_parts: list[str] = []
        if station.codec:
            info_parts.append(station.codec)
        if station.bitrate:
            info_parts.append(f"{station.bitrate}kbps")
        if station.country:
            info_parts.append(station.country)

        rows.append({
            "title": station.title or station.url[:40],
            "info": " · ".join(info_parts) if info_parts else "—",
            "provider": "TuneIn" if station.provider == "tunein" else "radio-browser",
            "plays": str(station.play_count),
            "_url": station.url,
            "_station_id": station.station_id,
            "_icon": station.icon,
            "_codec": station.codec,
            "_bitrate": str(station.bitrate) if station.bitrate else "0",
            "_provider": station.provider,
            "actions": [
                {
                    "label": "Remove",
                    "action": "remove_recent",
                    "params": {"_url": station.url, "title": station.title},
                    "style": "danger",
                    "confirm": True,
                },
            ],
        })

    children: list[Any] = [
        Table(
            columns=columns,
            rows=rows,
            edit_action="play_recent",
            row_key="_url",
        ),
    ]

    # Show top played stations as a quick summary.
    if most_played:
        top_items = []
        for i, s in enumerate(most_played, 1):
            top_items.append(KVItem(
                key=f"#{i}",
                value=f"{s.title or s.url[:30]} ({s.play_count}×)",
            ))
        children.insert(0, Card(title="Most Played", collapsible=True, children=[
            KeyValue(items=top_items),
        ]))

    return Tab(label="Recent", children=[
        Card(title="Recently Played Stations", children=children),
        Row(gap="md", children=[
            Button(
                label="Clear All History",
                action="clear_recent",
                style="danger",
                confirm=True,
                icon="trash-2",
            ),
        ]),
    ])


def _build_browse_tab() -> Any:
    """Build the 'Browse' tab with quick-access category cards."""
    from resonance.ui import (
        Alert,
        Button,
        Card,
        Column,
        Heading,
        KeyValue,
        KVItem,
        Row,
        StatusBadge,
        Tab,
        Text,
    )

    provider_pref = _preferred_provider()

    # Provider status badges.
    rb_status = "active" if _radio_browser is not None else "inactive"
    ti_status = "active" if _tunein_client is not None else "inactive"

    status_children: list[Any] = [
        Row(gap="md", children=[
            StatusBadge(
                label="radio-browser.info",
                status=rb_status,
                color="green" if rb_status == "active" else "gray",
            ),
            StatusBadge(
                label="TuneIn",
                status=ti_status,
                color="green" if ti_status == "active" else "gray",
            ),
        ]),
        KeyValue(items=[
            KVItem(key="Preferred Provider", value=_format_provider_name(provider_pref)),
            KVItem(
                key="radio-browser Cache",
                value=f"{_radio_browser.cache_size} entries" if _radio_browser else "N/A",
            ),
            KVItem(
                key="TuneIn Cache",
                value=f"{_tunein_client.cache_size} entries" if _tunein_client else "N/A",
            ),
        ]),
    ]

    # Browse quick-access cards.
    browse_children: list[Any] = []

    if provider_pref in ("radio-browser", "both"):
        browse_children.append(
            Card(title="radio-browser.info", collapsible=True, children=[
                Text(content="Free, open-source community database with ~40,000+ stations. No API key required."),
                Row(gap="sm", children=[
                    Button(label="Popular Stations", action="browse_rb", style="secondary", icon="trending-up"),
                    Button(label="Trending Now", action="browse_rb_trending", style="secondary", icon="zap"),
                ]),
                Row(gap="sm", children=[
                    Button(label="By Country", action="browse_rb_country", style="secondary", icon="globe"),
                    Button(label="By Genre", action="browse_rb_tag", style="secondary", icon="music"),
                    Button(label="By Language", action="browse_rb_language", style="secondary", icon="languages"),
                ]),
            ])
        )

    if provider_pref in ("tunein", "both"):
        browse_children.append(
            Card(title="TuneIn Radio", collapsible=True, children=[
                Text(content="TuneIn/RadioTime — same provider as LMS. Local radio, premium stations, and curated categories."),
                Row(gap="sm", children=[
                    Button(label="Browse TuneIn", action="browse_tunein", style="secondary", icon="radio"),
                ]),
            ])
        )

    # Cache management.
    browse_children.append(
        Row(gap="md", children=[
            Button(
                label="Clear All Caches",
                action="clear_caches",
                style="secondary",
                icon="refresh-cw",
            ),
        ])
    )

    return Tab(label="Browse", children=[
        Card(title="Provider Status", children=status_children),
        *browse_children,
    ])


def _build_settings_tab() -> Any:
    """Build the 'Settings' tab with a form for plugin configuration."""
    from resonance.ui import (
        Alert,
        Form,
        NumberInput,
        Select,
        SelectOption,
        Tab,
        Text,
        TextInput,
        Toggle,
    )

    provider_pref = _preferred_provider()
    default_country = str(_setting("default_country", ""))
    cache_ttl = int(_setting("cache_ttl", 600))
    max_recent = int(_setting("max_recent_stations", 50))
    show_metadata = bool(_setting("show_station_metadata", True))

    return Tab(label="Settings", children=[
        Form(
            action="save_settings",
            submit_label="Save Settings",
            children=[
                Select(
                    name="preferred_provider",
                    label="Preferred Provider",
                    value=provider_pref,
                    help_text="Controls which provider is used for browsing and search. 'both' shows categories from both providers.",
                    options=[
                        SelectOption(value="radio-browser", label="radio-browser.info (free, open-source)"),
                        SelectOption(value="tunein", label="TuneIn (LMS-compatible)"),
                        SelectOption(value="both", label="Both Providers"),
                    ],
                ),
                TextInput(
                    name="default_country",
                    label="Default Country Code",
                    value=default_country,
                    placeholder="e.g. DE, US, GB, FR",
                    help_text="ISO 3166-1 alpha-2 code. Used for local radio sorting and TuneIn's 'Local Radio' feature.",
                ),
                NumberInput(
                    name="cache_ttl",
                    label="Browse Cache TTL (seconds)",
                    value=cache_ttl,
                    min=60,
                    max=7200,
                    help_text="How long browse results are cached. Lower = fresher data, higher = fewer API calls.",
                ),
                NumberInput(
                    name="max_recent_stations",
                    label="Max Recently Played Stations",
                    value=max_recent,
                    min=5,
                    max=200,
                    help_text="Maximum number of recently played stations to remember.",
                ),
                Toggle(
                    name="show_station_metadata",
                    label="Show Station Metadata",
                    value=show_metadata,
                    help_text="Display codec, bitrate, country, and genre tags in station listings.",
                ),
            ],
        ),
        Alert(
            message="Changes to provider preferences and cache settings take effect after restarting the server.",
            severity="info",
        ),
    ])


def _build_about_tab() -> Any:
    """Build the 'About' tab with plugin information."""
    from resonance.ui import Markdown, Tab

    md = """## Radio Plugin v2.2

**Dual-Provider Internet Radio** for Resonance.

### Providers

| Provider | Stations | API Key | Source |
|----------|----------|---------|--------|
| radio-browser.info | ~40,000+ | Not required | Open-source community database |
| TuneIn / RadioTime | 100,000+ | Partner ID 16 | Same as LMS uses |

### Features

- **Browse** — categories, countries, genres, languages, local radio
- **Search** — full-text station search across providers
- **Play** — stream live radio to any connected player
- **Play from SDUI** — click a recently played station to start playback directly from this dashboard
- **Recently Played** — persistent history of played stations
- **Favorites** — add stations to your Favorites via the context menu
- **Jive Menu** — full integration for Squeezebox Touch/Radio/Boom/Controller
- **SDUI Dashboard** — this page! Browse, manage, and configure from the Web UI

### Data Sources

- [radio-browser.info](https://www.radio-browser.info) — free, open-source, community-maintained
- [TuneIn](https://tunein.com) — premium radio directory (Partner ID 16, same as LMS)

### Credits

Built on top of the Resonance plugin framework. radio-browser.info
is maintained by its wonderful community. TuneIn integration uses
the same RadioTime OPML API as the original Logitech Media Server.
"""

    return Tab(label="About", children=[
        Markdown(content=md),
    ])


def _format_provider_name(pref: str) -> str:
    """Format a provider preference value for display."""
    return {
        "radio-browser": "radio-browser.info",
        "tunein": "TuneIn",
        "both": "Both Providers",
    }.get(pref, pref)


# ---------------------------------------------------------------------------
# SDUI — action handler
# ---------------------------------------------------------------------------


async def handle_action(action: str, params: dict[str, Any], ctx: PluginContext) -> dict[str, Any] | None:
    """Handle SDUI actions from the frontend."""
    match action:
        case "play_recent":
            return await _handle_play_recent(params, ctx)
        case "remove_recent":
            return await _handle_remove_recent(params)
        case "clear_recent":
            return await _handle_clear_recent()
        case "clear_caches":
            return _handle_clear_caches()
        case "save_settings":
            return await _handle_save_settings(params, ctx)
        case "browse_rb" | "browse_rb_trending" | "browse_rb_country" \
                | "browse_rb_tag" | "browse_rb_language" | "browse_tunein":
            # These are informational browse buttons — the actual browsing
            # happens via the Jive menu / JSON-RPC commands. The SDUI buttons
            # serve as shortcuts that show a message.
            return {"message": _browse_action_message(action)}
        case _:
            return {"error": f"Unknown action: {action}"}


async def _handle_play_recent(params: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """Replay a recently played station via JSON-RPC self-call.

    Uses the server's own JSON-RPC endpoint (``radio play``) to start
    playback.  This reuses the full playback pipeline — playlist, streaming,
    Slimproto — without needing to reconstruct a ``CommandContext`` inside
    the SDUI action handler.
    """
    # Table actions pass params directly (from row action or edit_action).
    # The row data is either nested under "row" (table row-click) or flat.
    row = params.get("row", params)
    url = row.get("_url", "")
    title = row.get("title", "")
    station_id = row.get("_station_id", "")
    icon = row.get("_icon", "")
    codec = row.get("_codec", "")
    bitrate = row.get("_bitrate", "0")
    provider = row.get("_provider", "radio-browser")

    if not url:
        return {"error": "No station URL found"}

    # Find a player to play on
    player_registry = ctx.player_registry
    if player_registry is None:
        return {"error": "Player registry not available"}

    players = await player_registry.get_all()
    if not players:
        return {
            "error": "No players connected. Connect a Squeezebox or "
                     "Squeezelite player first."
        }

    # Use the first connected player
    player = players[0]
    player_id = player.mac_address

    # Determine server address from PluginContext.server_info
    host = ctx.server_info.get("host", "127.0.0.1") if ctx.server_info else "127.0.0.1"
    port = ctx.server_info.get("port", 9000) if ctx.server_info else 9000
    # Always use 127.0.0.1 for self-calls (avoid 0.0.0.0)
    if host == "0.0.0.0":
        host = "127.0.0.1"

    # Build the radio play command array
    cmd: list[Any] = [
        "radio", "play",
        f"url:{url}",
        f"cmd:play",
    ]
    if title:
        cmd.append(f"title:{title}")
    if icon:
        cmd.append(f"icon:{icon}")
    if station_id:
        cmd.append(f"id:{station_id}")
    if codec:
        cmd.append(f"codec:{codec}")
    if bitrate and bitrate != "0":
        cmd.append(f"bitrate:{bitrate}")

    rpc_body = {
        "id": 1,
        "method": "slim.request",
        "params": [player_id, cmd],
    }

    try:
        if _http_client is None:
            return {"error": "HTTP client not available"}

        rpc_url = f"http://{host}:{port}/jsonrpc.js"
        resp = await _http_client.post(rpc_url, json=rpc_body)
        resp.raise_for_status()
        result = resp.json()

        # Check for errors in the JSON-RPC response
        if "error" in result:
            error_detail = result["error"]
            if isinstance(error_detail, dict):
                error_detail = error_detail.get("message", str(error_detail))
            return {"error": f"Playback failed: {error_detail}"}

        rpc_result = result.get("result", {})
        if "error" in rpc_result:
            return {"error": f"Playback failed: {rpc_result['error']}"}

        logger.info(
            "SDUI play: %s → %s on player %s (via JSON-RPC)",
            title, url[:80], player.name or player_id,
        )

        if _ctx is not None:
            _ctx.notify_ui_update()

        return {"message": f"Playing '{title or 'station'}' on {player.name or player_id}"}

    except Exception as exc:
        logger.warning("SDUI play failed for %s: %s", url, exc)
        return {
            "error": f"Could not start playback: {exc}. "
                     f"Try playing via the Radio menu on your player instead."
        }


async def _handle_remove_recent(params: dict[str, Any]) -> dict[str, Any]:
    """Remove a station from the recently played list."""
    if _store is None:
        return {"error": "Store not available"}

    # Table row-actions pass params directly (from the action's params dict).
    # Also accept nested "row" for compatibility with table row-click.
    row = params.get("row", params)
    url = row.get("_url", "")

    if not url:
        return {"error": "No station URL found"}

    removed = _store.remove(url)
    if removed:
        _store.save()
        if _ctx is not None:
            _ctx.notify_ui_update()
        return {"message": f"Removed '{row.get('title', 'station')}' from history"}
    else:
        return {"error": "Station not found in history"}


async def _handle_clear_recent() -> dict[str, Any]:
    """Clear all recently played stations."""
    if _store is None:
        return {"error": "Store not available"}

    count = _store.recent_count
    _store.clear()
    _store.save()

    if _ctx is not None:
        _ctx.notify_ui_update()

    return {"message": f"Cleared {count} station(s) from history"}


def _handle_clear_caches() -> dict[str, Any]:
    """Clear browse caches for all providers."""
    cleared: list[str] = []

    if _radio_browser is not None:
        _radio_browser.clear_cache()
        cleared.append("radio-browser")

    if _tunein_client is not None:
        _tunein_client.clear_cache()
        cleared.append("tunein")

    if _ctx is not None:
        _ctx.notify_ui_update()

    if cleared:
        return {"message": f"Cleared caches for: {', '.join(cleared)}"}
    else:
        return {"error": "No providers available"}


async def _handle_save_settings(params: dict[str, Any], ctx: PluginContext) -> dict[str, Any]:
    """Save settings from the SDUI settings form."""
    saved: list[str] = []

    setting_keys = [
        "preferred_provider", "default_country", "cache_ttl",
        "max_recent_stations", "show_station_metadata",
    ]

    for key in setting_keys:
        if key in params:
            value = params[key]
            # Type coercion.
            if key in ("cache_ttl", "max_recent_stations"):
                try:
                    value = int(value)
                except (ValueError, TypeError):
                    continue
            elif key == "show_station_metadata":
                if isinstance(value, str):
                    value = value.lower() in ("true", "1", "yes")
            ctx.set_setting(key, value)
            saved.append(key)

    # Update store max_recent if changed.
    if "max_recent_stations" in params and _store is not None:
        try:
            _store.max_recent = int(params["max_recent_stations"])
            _store.save()
        except (ValueError, TypeError):
            pass

    if _ctx is not None:
        _ctx.notify_ui_update()

    if saved:
        return {"message": f"Saved settings: {', '.join(saved)}. Restart for provider/cache changes."}
    else:
        return {"message": "No changes to save"}


def _browse_action_message(action: str) -> str:
    """Generate a helpful message for browse shortcut buttons."""
    messages = {
        "browse_rb": (
            "Browse popular stations via your player's Radio menu, "
            "or use: radio items 0 50 category:popular"
        ),
        "browse_rb_trending": (
            "Browse trending stations via your player's Radio menu, "
            "or use: radio items 0 50 category:trending"
        ),
        "browse_rb_country": (
            "Browse by country via your player's Radio menu, "
            "or use: radio items 0 50 category:country"
        ),
        "browse_rb_tag": (
            "Browse by genre/tag via your player's Radio menu, "
            "or use: radio items 0 50 category:tag"
        ),
        "browse_rb_language": (
            "Browse by language via your player's Radio menu, "
            "or use: radio items 0 50 category:language"
        ),
        "browse_tunein": (
            "Browse TuneIn via your player's Radio menu. TuneIn provides "
            "local radio, curated categories, and premium stations."
        ),
    }
    return messages.get(action, "Use your player's Radio menu to browse.")


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

    # Record in recently-played store.
    if _store is not None:
        _store.record_play(
            url=stream_url,
            title=title,
            icon=icon,
            codec=codec,
            bitrate=bitrate,
            station_id=station_uuid,
            provider="radio-browser",
        )
        _store.save()
        if _ctx is not None:
            _ctx.notify_ui_update()

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
