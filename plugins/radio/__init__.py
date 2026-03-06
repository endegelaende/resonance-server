"""
Radio Plugin for Resonance.

Internet Radio via **radio-browser.info** — free, open-source community
database with ~40,000+ stations. SDUI dashboard with real browsing,
recently played stations, configurable settings, and full Jive menu
integration.

Architecture
~~~~~~~~~~~~

* **RadioBrowserClient** (``radiobrowser.py``) wraps the radio-browser.info API.
* **RadioProvider** implements
  :class:`~resonance.content_provider.ContentProvider` for the server's
  generic content infrastructure.
* **RadioStore** (``store.py``) persists recently played stations.
* JSON-RPC commands provide the Jive-compatible menu interface:

  - ``radio items <start> <count>`` — browse categories/stations
  - ``radio search <start> <count>`` — search stations
  - ``radio play`` — play/add/insert a station

* SDUI page provides a Web-UI dashboard with tabs:

  - **Recent** — recently played stations (click to replay)
  - **Browse** — live browsing of categories, countries, genres, languages,
    popular & trending stations with drill-down navigation
  - **Settings** — default country, cache TTL
  - **About** — plugin information

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

Data source
~~~~~~~~~~~

* https://www.radio-browser.info — open community project, free API,
  pre-resolved stream URLs.  No API key required.
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
_provider: Any | None = None  # RadioProvider (radio-browser) instance
_event_bus: Any | None = None  # EventBus reference
_store: Any | None = None  # RadioStore instance
_ctx: Any | None = None  # PluginContext reference
_http_client: Any | None = None  # httpx.AsyncClient for JSON-RPC self-calls

# Browse state — tracks what the user is currently browsing in the SDUI.
# When a browse action is triggered, _browse_path is set and the UI re-renders
# with data fetched from radio-browser.info.
_browse_path: str = ""  # e.g. "", "popular", "country", "country:DE", "tag", "tag:jazz"
_browse_data: list[dict[str, Any]] | None = None  # Cached browse results for current path
_browse_title: str = ""  # Human-readable title for current browse view


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
        resolution step needed.
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
    global _radio_browser, _provider
    global _event_bus, _store, _ctx, _http_client

    import httpx

    from .radiobrowser import RadioBrowserClient
    from .store import RadioStore

    _ctx = ctx
    _event_bus = ctx.event_bus
    _http_client = httpx.AsyncClient(follow_redirects=True, timeout=15.0)

    # ── Read settings ──────────────────────────────────────────
    cache_ttl = int(_setting("cache_ttl", 600))
    max_recent = int(_setting("max_recent_stations", 50))

    # ── Create store ───────────────────────────────────────────
    data_dir = ctx.ensure_data_dir()
    _store = RadioStore(data_dir, max_recent=max_recent)
    _store.load()

    # ── Create radio-browser client ────────────────────────────
    _radio_browser = RadioBrowserClient(cache_ttl=float(cache_ttl))
    await _radio_browser.start()
    _provider = RadioProvider(_radio_browser)

    # ── Register content provider ──────────────────────────────
    ctx.register_content_provider("radio", _provider)

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
        "Radio plugin v3.0 started (%d recent stations, radio-browser=active)",
        _store.recent_count,
    )


async def teardown(ctx: PluginContext) -> None:
    """Called by PluginManager during server shutdown."""
    global _radio_browser, _provider
    global _event_bus, _store, _ctx, _http_client

    # Save recently played stations.
    if _store is not None:
        _store.save()

    if _radio_browser is not None:
        await _radio_browser.close()

    if _http_client is not None:
        await _http_client.aclose()

    _radio_browser = None
    _provider = None
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
    if source != "radio":
        # Check if it's a radio stream by looking at the track metadata.
        track = getattr(event, "track", None)
        if track is None:
            return
        track_source = getattr(track, "source", "")
        if track_source != "radio":
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

    entry = _store.record_play(
        url=url,
        title=title,
        icon=icon,
        codec=codec,
        bitrate=bitrate,
        station_id=station_id,
        provider="radio-browser",
    )
    _store.save()

    logger.debug(
        "Recorded radio play: %s (count=%d)",
        entry.title or url[:60], entry.play_count,
    )

    # Notify the SDUI frontend to refresh.
    if _ctx is not None:
        _ctx.notify_ui_update()


# ---------------------------------------------------------------------------
# SDUI — Page builder
# ---------------------------------------------------------------------------


async def get_ui(ctx: PluginContext) -> Any:
    """Build the SDUI page for the Radio plugin."""
    from resonance.ui import (
        Page,
        Tabs,
    )

    recent_tab = _build_recent_tab()
    browse_tab = await _build_browse_tab()
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
        KeyValue,
        KVItem,
        Row,
        Tab,
        Table,
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


async def _build_browse_tab() -> Any:
    """Build the 'Browse' tab with live data from radio-browser.info.

    Uses the module-level _browse_path to determine what to show:
    - "" (empty) → category overview cards with quick-access buttons
    - "popular" → table of popular stations
    - "trending" → table of trending stations
    - "country" → table of countries (click to drill down)
    - "country:DE" → table of stations in Germany
    - "tag" → table of genres/tags
    - "tag:jazz" → table of jazz stations
    - "language" → table of languages
    - "language:german" → table of German-language stations
    - "search:<query>" → search results
    """
    from resonance.ui import (
        Alert,
        Button,
        Card,
        Column,
        Form,
        KeyValue,
        KVItem,
        Row,
        StatusBadge,
        Tab,
        Table,
        TableColumn,
        Text,
        TextInput,
    )

    global _browse_data, _browse_title

    rb_status = "active" if _radio_browser is not None else "inactive"

    # Navigation bar — always shown.  Back button + category shortcuts.
    nav_children: list[Any] = []

    if _browse_path:
        # Show back button and current location.
        back_target = ""
        if ":" in _browse_path and not _browse_path.startswith("search:"):
            # e.g. "country:DE" → go back to "country"
            back_target = _browse_path.split(":")[0]

        nav_children.append(
            Row(gap="sm", children=[
                Button(label="← Back", action="browse_back",
                       params={"target": back_target}, style="secondary", icon="arrow-left"),
                Button(label="Home", action="browse_home", style="secondary", icon="home"),
            ])
        )
        nav_children.append(
            Text(content=f"**Browsing:** {_browse_title or _browse_path}"),
        )
    else:
        # Show status and search.
        nav_children.append(
            Row(gap="md", children=[
                StatusBadge(
                    label="radio-browser.info",
                    status=rb_status,
                    color="green" if rb_status == "active" else "gray",
                ),
                StatusBadge(
                    label="Cache",
                    status=f"{_radio_browser.cache_size} entries" if _radio_browser else "N/A",
                    color="blue" if _radio_browser and _radio_browser.cache_size > 0 else "gray",
                ),
            ])
        )

    # Search form — always available.
    nav_children.append(
        Form(
            action="browse_search",
            submit_label="Search",
            children=[
                TextInput(
                    name="query",
                    label="Search Stations",
                    placeholder="Search by station name...",
                    help_text="Search across ~40,000+ stations on radio-browser.info",
                ),
            ],
        )
    )

    # Content area — depends on browse path.
    content_children: list[Any] = []

    if not _browse_path:
        # Category overview — quick-access cards.
        content_children.append(
            Card(title="Popular & Trending", collapsible=False, children=[
                Row(gap="sm", children=[
                    Button(label="Popular Stations", action="browse_navigate",
                           params={"path": "popular"}, style="primary", icon="trending-up"),
                    Button(label="Trending Now", action="browse_navigate",
                           params={"path": "trending"}, style="primary", icon="zap"),
                ]),
            ])
        )
        content_children.append(
            Card(title="Browse by Category", collapsible=False, children=[
                Row(gap="sm", children=[
                    Button(label="By Country", action="browse_navigate",
                           params={"path": "country"}, style="secondary", icon="globe"),
                    Button(label="By Genre", action="browse_navigate",
                           params={"path": "tag"}, style="secondary", icon="music"),
                    Button(label="By Language", action="browse_navigate",
                           params={"path": "language"}, style="secondary", icon="languages"),
                ]),
            ])
        )
        content_children.append(
            Row(gap="md", children=[
                Button(
                    label="Clear Cache",
                    action="clear_caches",
                    style="secondary",
                    icon="refresh-cw",
                ),
            ])
        )

    elif _browse_data is not None:
        # We have data to show — render it.
        if _browse_path in ("popular", "trending") or ":" in _browse_path or _browse_path.startswith("search:"):
            # Station list — show as table with play action.
            content_children.append(
                _build_station_table(_browse_data, _browse_title or "Stations"),
            )
        elif _browse_path in ("country", "tag", "language"):
            # Category list — show as table with drill-down action.
            content_children.append(
                _build_category_table(_browse_data, _browse_path, _browse_title or "Categories"),
            )
        else:
            content_children.append(
                Alert(message=f"Unknown browse path: {_browse_path}", severity="warning"),
            )
    else:
        # Browse path is set but no data loaded yet (shouldn't happen normally).
        content_children.append(
            Alert(message="Loading...", severity="info"),
        )

    return Tab(label="Browse", children=[
        Card(title="Radio Browser", children=nav_children),
        *content_children,
    ])


def _build_station_table(stations: list[dict[str, Any]], title: str) -> Any:
    """Build a Table widget showing radio stations with play action."""
    from resonance.ui import Alert, Card, Table, TableColumn, Text

    if not stations:
        return Card(title=title, children=[
            Alert(message="No stations found.", severity="info"),
        ])

    columns = [
        TableColumn(key="title", label="Station"),
        TableColumn(key="info", label="Info"),
        TableColumn(key="country", label="Country"),
        TableColumn(key="votes", label="Votes"),
    ]

    return Card(title=f"{title} ({len(stations)})", children=[
        Text(content=f"Click a station to play it on your default player."),
        Table(
            columns=columns,
            rows=stations,
            edit_action="play_station",
            row_key="_station_id",
        ),
    ])


def _build_category_table(
    categories: list[dict[str, Any]], browse_path: str, title: str
) -> Any:
    """Build a Table widget showing browse categories with drill-down."""
    from resonance.ui import Alert, Card, Table, TableColumn, Text

    if not categories:
        return Card(title=title, children=[
            Alert(message="No categories found.", severity="info"),
        ])

    columns = [
        TableColumn(key="name", label="Name"),
        TableColumn(key="stations", label="Stations"),
    ]

    return Card(title=f"{title} ({len(categories)})", children=[
        Text(content="Click a category to browse its stations."),
        Table(
            columns=columns,
            rows=categories,
            edit_action="browse_drilldown",
            row_key="_path",
        ),
    ])


def _build_settings_tab() -> Any:
    """Build the 'Settings' tab with a form for plugin configuration."""
    from resonance.ui import (
        Alert,
        Form,
        NumberInput,
        Tab,
        TextInput,
        Toggle,
    )

    default_country = str(_setting("default_country", ""))
    cache_ttl = int(_setting("cache_ttl", 600))
    max_recent = int(_setting("max_recent_stations", 50))
    show_metadata = bool(_setting("show_station_metadata", True))

    return Tab(label="Settings", children=[
        Form(
            action="save_settings",
            submit_label="Save Settings",
            children=[
                TextInput(
                    name="default_country",
                    label="Default Country Code",
                    value=default_country,
                    placeholder="e.g. DE, US, GB, FR",
                    help_text="ISO 3166-1 alpha-2 code. Used for sorting and highlighting your country in listings.",
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
            message="Changes to cache settings take effect after restarting the server.",
            severity="info",
        ),
    ])


def _build_about_tab() -> Any:
    """Build the 'About' tab with plugin information."""
    from resonance.ui import Markdown, Tab

    md = """## Radio Plugin v3.0

**Internet Radio** for Resonance — powered by radio-browser.info.

### Features

- **Browse** — popular, trending, by country, genre, language
- **Search** — full-text station search across ~40,000+ stations
- **Play from SDUI** — click any station in the Browse or Recent tab to start playback
- **Recently Played** — persistent history with play counts
- **Favorites** — add stations to your Favorites via the context menu on your player
- **Jive Menu** — full integration for Squeezebox Touch/Radio/Boom/Controller
- **SDUI Dashboard** — this page! Browse, play, manage, and configure from the Web UI

### Data Source

[radio-browser.info](https://www.radio-browser.info) — free, open-source,
community-maintained database. No API key required. Pre-resolved stream URLs.

### Credits

Built on the Resonance plugin framework. radio-browser.info is maintained
by its wonderful open-source community.
"""

    return Tab(label="About", children=[
        Markdown(content=md),
    ])


# ---------------------------------------------------------------------------
# SDUI — Action handlers
# ---------------------------------------------------------------------------


async def handle_action(action: str, params: dict[str, Any], ctx: PluginContext) -> dict[str, Any] | None:
    """Handle SDUI actions from the frontend."""
    match action:
        # Recent tab actions
        case "play_recent":
            return await _handle_play_station(params, ctx)
        case "remove_recent":
            return await _handle_remove_recent(params)
        case "clear_recent":
            return await _handle_clear_recent()

        # Browse tab actions — navigation
        case "browse_navigate":
            return await _handle_browse_navigate(params)
        case "browse_back":
            return await _handle_browse_back(params)
        case "browse_home":
            return await _handle_browse_home()
        case "browse_search":
            return await _handle_browse_search(params)
        case "browse_drilldown":
            return await _handle_browse_drilldown(params)

        # Browse tab actions — play
        case "play_station":
            return await _handle_play_station(params, ctx)

        # Settings
        case "clear_caches":
            return _handle_clear_caches()
        case "save_settings":
            return await _handle_save_settings(params, ctx)

        case _:
            return {"error": f"Unknown action: {action}"}


# ---------------------------------------------------------------------------
# Browse navigation handlers
# ---------------------------------------------------------------------------


async def _handle_browse_navigate(params: dict[str, Any]) -> dict[str, Any]:
    """Navigate to a browse path and load data."""
    path = params.get("path", "")
    if not path:
        return await _handle_browse_home()

    return await _load_browse_data(path)


async def _handle_browse_back(params: dict[str, Any]) -> dict[str, Any]:
    """Go back one level in browse navigation."""
    target = params.get("target", "")
    if target:
        return await _load_browse_data(target)
    return await _handle_browse_home()


async def _handle_browse_home() -> dict[str, Any]:
    """Return to browse home (category overview)."""
    global _browse_path, _browse_data, _browse_title
    _browse_path = ""
    _browse_data = None
    _browse_title = ""

    if _ctx is not None:
        _ctx.notify_ui_update()

    return {"message": "Browse home"}


async def _handle_browse_search(params: dict[str, Any]) -> dict[str, Any]:
    """Search for stations and show results."""
    query = params.get("query", "").strip()
    if not query:
        return {"error": "Please enter a search term"}

    if _radio_browser is None:
        return {"error": "Radio browser not available"}

    try:
        stations = await _radio_browser.search(query, limit=100)
        return await _set_browse_stations(f"search:{query}", f'Search: "{query}"', stations)
    except Exception as exc:
        logger.warning("Browse search failed: %s", exc)
        return {"error": f"Search failed: {exc}"}


async def _handle_browse_drilldown(params: dict[str, Any]) -> dict[str, Any]:
    """Drill down into a category (e.g. click a country to see its stations)."""
    row = params.get("row", params)
    path = row.get("_path", "")
    if not path:
        return {"error": "No category path found"}

    return await _load_browse_data(path)


async def _load_browse_data(path: str) -> dict[str, Any]:
    """Load browse data for a given path and update module state."""
    global _browse_path, _browse_data, _browse_title

    if _radio_browser is None:
        return {"error": "Radio browser not available"}

    try:
        if path == "popular":
            stations = await _radio_browser.get_popular_stations(limit=100)
            return await _set_browse_stations(path, "Popular Stations", stations)

        elif path == "trending":
            stations = await _radio_browser.get_trending_stations(limit=100)
            return await _set_browse_stations(path, "Trending Now", stations)

        elif path == "country":
            countries = await _radio_browser.get_countries(limit=200)
            return await _set_browse_categories(
                path, "Countries",
                [
                    {
                        "name": c.name,
                        "stations": str(c.stationcount),
                        "_path": f"country:{c.iso_3166_1}",
                    }
                    for c in countries
                    if c.iso_3166_1
                ],
            )

        elif path.startswith("country:"):
            code = path.split(":", 1)[1]
            stations = await _radio_browser.get_stations_by_country(code, limit=100)
            return await _set_browse_stations(path, f"Stations in {code}", stations)

        elif path == "tag":
            tags = await _radio_browser.get_tags(limit=200)
            return await _set_browse_categories(
                path, "Genres / Tags",
                [
                    {
                        "name": t.name,
                        "stations": str(t.stationcount),
                        "_path": f"tag:{t.name}",
                    }
                    for t in tags
                ],
            )

        elif path.startswith("tag:"):
            tag = path.split(":", 1)[1]
            stations = await _radio_browser.get_stations_by_tag(tag, limit=100)
            return await _set_browse_stations(path, f"Genre: {tag}", stations)

        elif path == "language":
            languages = await _radio_browser.get_languages(limit=200)
            return await _set_browse_categories(
                path, "Languages",
                [
                    {
                        "name": lang.name,
                        "stations": str(lang.stationcount),
                        "_path": f"language:{lang.name}",
                    }
                    for lang in languages
                ],
            )

        elif path.startswith("language:"):
            lang = path.split(":", 1)[1]
            stations = await _radio_browser.get_stations_by_language(lang, limit=100)
            return await _set_browse_stations(path, f"Language: {lang}", stations)

        else:
            return {"error": f"Unknown browse path: {path}"}

    except Exception as exc:
        logger.warning("Failed to load browse data for %s: %s", path, exc)
        return {"error": f"Failed to load data: {exc}"}


async def _set_browse_stations(
    path: str, title: str, stations: list[Any]
) -> dict[str, Any]:
    """Convert RadioStation objects to table rows and set browse state."""
    global _browse_path, _browse_data, _browse_title

    from .radiobrowser import format_station_subtitle

    rows: list[dict[str, Any]] = []
    for s in stations:
        info_parts: list[str] = []
        if s.codec:
            info_parts.append(s.codec)
        if s.bitrate:
            info_parts.append(f"{s.bitrate}kbps")

        rows.append({
            "title": s.name,
            "info": " · ".join(info_parts) if info_parts else "—",
            "country": s.country or "—",
            "votes": str(s.votes) if s.votes else "—",
            "_url": s.url_resolved or s.url,
            "_station_id": s.stationuuid,
            "_icon": s.favicon or "",
            "_codec": s.codec,
            "_bitrate": str(s.bitrate) if s.bitrate else "0",
            "_provider": "radio-browser",
        })

    _browse_path = path
    _browse_data = rows
    _browse_title = title

    if _ctx is not None:
        _ctx.notify_ui_update()

    return {"message": f"Loaded {len(rows)} station(s)"}


async def _set_browse_categories(
    path: str, title: str, categories: list[dict[str, Any]]
) -> dict[str, Any]:
    """Set browse state to show a category list."""
    global _browse_path, _browse_data, _browse_title

    _browse_path = path
    _browse_data = categories
    _browse_title = title

    if _ctx is not None:
        _ctx.notify_ui_update()

    return {"message": f"Loaded {len(categories)} categories"}


# ---------------------------------------------------------------------------
# Play handler (shared by Recent tab and Browse tab)
# ---------------------------------------------------------------------------


async def _handle_play_station(params: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """Play a station via JSON-RPC self-call.

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


# ---------------------------------------------------------------------------
# Other action handlers
# ---------------------------------------------------------------------------


async def _handle_remove_recent(params: dict[str, Any]) -> dict[str, Any]:
    """Remove a station from the recently played list."""
    if _store is None:
        return {"error": "Store not available"}

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
    """Clear browse caches."""
    global _browse_data, _browse_path, _browse_title

    if _radio_browser is not None:
        _radio_browser.clear_cache()

    # Also reset browse state.
    _browse_path = ""
    _browse_data = None
    _browse_title = ""

    if _ctx is not None:
        _ctx.notify_ui_update()

    return {"message": "Cleared browse cache"}


async def _handle_save_settings(params: dict[str, Any], ctx: PluginContext) -> dict[str, Any]:
    """Save settings from the SDUI settings form."""
    saved: list[str] = []

    setting_keys = [
        "default_country", "cache_ttl",
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
        return {"message": f"Saved settings: {', '.join(saved)}. Restart for cache changes."}
    else:
        return {"message": "No changes to save"}


# ---------------------------------------------------------------------------
# JSON-RPC command handler
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
# radio search
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

    # Add icons for categories.
    if key == "popular":
        entry["icon-id"] = "plugins/RadioBrowser/html/images/popular.png"
    elif key == "trending":
        entry["icon-id"] = "plugins/RadioBrowser/html/images/trending.png"
    elif key == "country":
        entry["icon-id"] = "plugins/RadioBrowser/html/images/world.png"
    elif key == "tag":
        entry["icon-id"] = "plugins/RadioBrowser/html/images/genre.png"
    elif key == "language":
        entry["icon-id"] = "plugins/RadioBrowser/html/images/language.png"

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
