"""
Content Provider abstraction for Resonance.

This module defines the interfaces that plugins implement to provide
external audio sources (Internet Radio, Podcasts, streaming services).

Architecture
~~~~~~~~~~~~

A **ContentProvider** is a plugin-supplied object that can:

* **browse** — return a hierarchical menu of playable items
* **search** — find items by text query
* **get_stream_info** — resolve an item ID to a concrete stream URL

Providers are registered via :meth:`ContentProviderRegistry.register`
(usually called from ``PluginContext.register_content_provider``).  The
registry is a singleton managed by the server and injected into the
web/handler layer so that playlist and streaming code can resolve remote
tracks.

Data flow::

    Plugin                    Registry                   StreamingServer
    ──────                    ────────                   ───────────────
    setup():
      ctx.register_content_provider("radio", provider)
                          ─►  providers["radio"] = provider

    User browses "Radio":
      registry.browse("radio", "/")
                          ─►  provider.browse("/")
                          ◄─  [BrowseItem, ...]

    User plays item:
      stream_info = registry.get_stream_info("radio", item_id)
                          ─►  provider.get_stream_info(item_id)
                          ◄─  StreamInfo(url=..., content_type=..., ...)

      streaming_server.queue_url(mac, stream_info.url, ...)
                                                    ─►  proxy stream to player

Design notes
~~~~~~~~~~~~

* **LMS-First**: LMS uses ``ProtocolHandlers`` (per-scheme) and ``XMLBrowser``
  (OPML-based menus).  Our ContentProvider merges both concerns into one
  interface — simpler, but equally capable.
* All methods are ``async`` because providers typically make HTTP calls.
* ``BrowseItem`` can nest (``type="folder"`` with children) or be flat
  (the provider resolves children on demand via ``browse(path)``).
* ``StreamInfo`` carries everything the streaming layer needs to proxy a
  remote URL to the player.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# =============================================================================
# Data classes
# =============================================================================


@dataclass(frozen=True, slots=True)
class StreamInfo:
    """Resolved stream information returned by a content provider.

    This is everything the streaming layer needs to proxy a remote audio
    URL to a Squeezebox player.
    """

    url: str
    """Direct audio stream URL (HTTP or HTTPS)."""

    content_type: str = "audio/mpeg"
    """MIME type of the audio stream."""

    title: str = ""
    """Display title (station name, episode title, …)."""

    artist: str = ""
    """Artist or show name."""

    album: str = ""
    """Album, podcast series, or station category."""

    artwork_url: str | None = None
    """URL for cover art / station logo."""

    duration_ms: int = 0
    """Duration in milliseconds.  ``0`` for live/infinite streams."""

    bitrate: int = 0
    """Bitrate in kbps as reported by the provider (0 = unknown)."""

    is_live: bool = False
    """``True`` for infinite live streams (Internet Radio)."""

    extra: dict[str, Any] = field(default_factory=dict)
    """Provider-specific extra metadata (forwarded but not interpreted)."""


@dataclass(frozen=True, slots=True)
class BrowseItem:
    """A single item in a content-provider browse tree.

    Depending on *type*, an item is either:

    * ``"audio"`` — a playable audio item (can be resolved via
      :meth:`ContentProvider.get_stream_info`)
    * ``"folder"`` — a container whose children are fetched with
      :meth:`ContentProvider.browse`
    * ``"search"`` — a search entry point (UI renders a text input)
    """

    id: str
    """Provider-scoped unique identifier for this item."""

    title: str
    """Display text."""

    type: str = "audio"
    """Item type: ``"audio"``, ``"folder"``, or ``"search"``."""

    url: str | None = None
    """Optional direct URL (used as hint; the authoritative URL comes
    from :meth:`ContentProvider.get_stream_info`)."""

    icon: str | None = None
    """Optional icon / artwork URL."""

    subtitle: str | None = None
    """Secondary display text (e.g. genre, description)."""

    items: list[BrowseItem] | None = None
    """Pre-loaded children (for small static sub-menus).  When ``None``,
    the UI should call :meth:`ContentProvider.browse` with this item's
    *id* to load children on demand."""

    extra: dict[str, Any] = field(default_factory=dict)
    """Provider-specific extra data."""


# =============================================================================
# Abstract base class
# =============================================================================


class ContentProvider(ABC):
    """Abstract base class for content providers.

    Plugins subclass this and register an instance via
    ``PluginContext.register_content_provider()``.

    All methods are *async* — providers are expected to make network
    requests (HTTP APIs, RSS feeds, etc.).
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name (e.g. ``"TuneIn Radio"``)."""
        ...

    @property
    def icon(self) -> str | None:
        """Optional icon URL for the provider's top-level menu entry."""
        return None

    @abstractmethod
    async def browse(self, path: str = "") -> list[BrowseItem]:
        """Browse the provider's content tree.

        Args:
            path: Hierarchical path into the browse tree.  An empty
                string means the root level.  Sub-paths are
                provider-defined (e.g. ``"genres/jazz"``).

        Returns:
            A list of :class:`BrowseItem` instances at the requested
            level.
        """
        ...

    @abstractmethod
    async def search(self, query: str) -> list[BrowseItem]:
        """Search for items matching *query*.

        Args:
            query: Free-text search string.

        Returns:
            A list of matching :class:`BrowseItem` instances.
        """
        ...

    @abstractmethod
    async def get_stream_info(self, item_id: str) -> StreamInfo | None:
        """Resolve an item to a concrete stream URL.

        Args:
            item_id: The :attr:`BrowseItem.id` of the item to resolve.

        Returns:
            A :class:`StreamInfo` with the direct stream URL and
            metadata, or ``None`` if the item cannot be resolved.
        """
        ...

    async def on_stream_started(self, item_id: str, player_mac: str) -> None:
        """Called when the player actually starts playing this item.

        Providers can override this to update play counts, scrobble, etc.
        The default implementation does nothing.
        """

    async def on_stream_stopped(self, item_id: str, player_mac: str) -> None:
        """Called when the player stops playing this item.

        The default implementation does nothing.
        """


# =============================================================================
# Registry
# =============================================================================


class ContentProviderRegistry:
    """Central registry for all loaded content providers.

    Singleton managed by the server and shared with the web/handler layer.
    Plugins register providers at setup time; handlers query the registry
    when users browse or play remote content.
    """

    def __init__(self) -> None:
        self._providers: dict[str, ContentProvider] = {}

    # -- Registration --------------------------------------------------------

    def register(self, provider_id: str, provider: ContentProvider) -> None:
        """Register a content provider.

        Args:
            provider_id: Short unique identifier (e.g. ``"radio"``,
                ``"podcast"``).  Used as a namespace in browse paths
                and item IDs.
            provider: The :class:`ContentProvider` instance.

        Raises:
            ValueError: If *provider_id* is already registered.
        """
        if provider_id in self._providers:
            raise ValueError(
                f"Content provider '{provider_id}' is already registered"
            )
        self._providers[provider_id] = provider
        logger.info(
            "Registered content provider: %s (%s)",
            provider_id,
            provider.name,
        )

    def unregister(self, provider_id: str) -> None:
        """Remove a content provider.

        This is called during plugin teardown.  Silently ignores unknown
        IDs.
        """
        removed = self._providers.pop(provider_id, None)
        if removed is not None:
            logger.info("Unregistered content provider: %s", provider_id)

    # -- Queries -------------------------------------------------------------

    def get(self, provider_id: str) -> ContentProvider | None:
        """Look up a provider by ID."""
        return self._providers.get(provider_id)

    def list_providers(self) -> list[tuple[str, ContentProvider]]:
        """Return all registered providers as ``(id, provider)`` pairs."""
        return list(self._providers.items())

    @property
    def provider_ids(self) -> list[str]:
        """List of registered provider IDs."""
        return list(self._providers.keys())

    def __contains__(self, provider_id: str) -> bool:
        return provider_id in self._providers

    def __len__(self) -> int:
        return len(self._providers)

    # -- Convenience wrappers ------------------------------------------------

    async def browse(self, provider_id: str, path: str = "") -> list[BrowseItem]:
        """Browse a specific provider.

        Args:
            provider_id: Which provider to query.
            path: Browse path (empty = root).

        Returns:
            List of :class:`BrowseItem` or empty list if provider unknown.
        """
        provider = self._providers.get(provider_id)
        if provider is None:
            logger.warning("browse: unknown provider '%s'", provider_id)
            return []
        try:
            return await provider.browse(path)
        except Exception:
            logger.exception(
                "browse failed for provider '%s' path='%s'",
                provider_id,
                path,
            )
            return []

    async def search(self, provider_id: str, query: str) -> list[BrowseItem]:
        """Search within a specific provider.

        Args:
            provider_id: Which provider to query.
            query: Search text.

        Returns:
            List of matching :class:`BrowseItem` or empty list.
        """
        provider = self._providers.get(provider_id)
        if provider is None:
            logger.warning("search: unknown provider '%s'", provider_id)
            return []
        try:
            return await provider.search(query)
        except Exception:
            logger.exception(
                "search failed for provider '%s' query='%s'",
                provider_id,
                query,
            )
            return []

    async def get_stream_info(
        self,
        provider_id: str,
        item_id: str,
    ) -> StreamInfo | None:
        """Resolve an item to a stream URL via its provider.

        Args:
            provider_id: Which provider owns this item.
            item_id: The item's provider-scoped ID.

        Returns:
            :class:`StreamInfo` or ``None``.
        """
        provider = self._providers.get(provider_id)
        if provider is None:
            logger.warning(
                "get_stream_info: unknown provider '%s'", provider_id
            )
            return None
        try:
            return await provider.get_stream_info(item_id)
        except Exception:
            logger.exception(
                "get_stream_info failed for provider '%s' item='%s'",
                provider_id,
                item_id,
            )
            return None

    async def search_all(self, query: str) -> dict[str, list[BrowseItem]]:
        """Search across **all** registered providers.

        Returns:
            Dict mapping provider_id → list of matching items.
            Providers that return no results or fail are omitted.
        """
        results: dict[str, list[BrowseItem]] = {}
        for pid, provider in self._providers.items():
            try:
                items = await provider.search(query)
                if items:
                    results[pid] = items
            except Exception:
                logger.exception(
                    "search_all: provider '%s' failed for query='%s'",
                    pid,
                    query,
                )
        return results
