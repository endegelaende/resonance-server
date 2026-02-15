"""
Plugin API for Resonance.

This module defines the core API surface that plugins interact with:

- **PluginManifest**: Parsed metadata from a plugin's ``plugin.toml``.
- **PluginContext**: The dependency-injection container passed to every plugin's
  ``setup()`` / ``teardown()`` functions.  It exposes registration helpers for
  commands, menu entries, FastAPI routes, and the global event bus — without
  giving plugins direct access to server internals.

Usage (inside a plugin's ``__init__.py``):

    from resonance.plugin import PluginContext

    async def setup(ctx: PluginContext) -> None:
        ctx.register_command("myplugin.hello", cmd_hello)
        ctx.register_menu_node(
            node_id="myplugin",
            parent="home",
            text="My Plugin",
            weight=50,
        )
        await ctx.event_bus.subscribe("player.track_started", on_track)

    async def teardown(ctx: PluginContext) -> None:
        pass

    async def cmd_hello(ctx, command):
        return {"message": "Hello from plugin!"}

    async def on_track(event):
        pass
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import APIRouter

    from resonance.content_provider import ContentProvider, ContentProviderRegistry
    from resonance.core.events import EventBus
    from resonance.core.library import MusicLibrary
    from resonance.core.playlist import PlaylistManager
    from resonance.player.registry import PlayerRegistry
    from resonance.web.jsonrpc import CommandHandler

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Plugin Manifest
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PluginManifest:
    """Parsed representation of a plugin's ``plugin.toml``.

    The manifest lives at ``plugins/<name>/plugin.toml`` and contains:

    .. code-block:: toml

        [plugin]
        name = "example"
        version = "0.1.0"
        description = "An example plugin"
        author = "Someone"
        min_resonance_version = "0.1.0"

    All fields except *name* and *version* are optional.
    """

    name: str
    """Unique identifier for the plugin (must match the directory name)."""

    version: str
    """Semver-ish version string."""

    description: str = ""
    """Human-readable one-liner."""

    author: str = ""
    """Author or maintainer."""

    min_resonance_version: str = ""
    """Minimum Resonance server version required (informational for now)."""

    plugin_dir: Path = field(default_factory=lambda: Path("."))
    """Absolute path to the plugin directory (set by the loader, not by TOML)."""

    @classmethod
    def from_toml(cls, data: dict[str, Any], plugin_dir: Path) -> PluginManifest:
        """Create a manifest from a parsed TOML ``[plugin]`` table.

        Args:
            data: The ``[plugin]`` table from the TOML file.
            plugin_dir: Path to the directory containing ``plugin.toml``.

        Raises:
            ValueError: If required fields are missing.
        """
        name = data.get("name")
        version = data.get("version")
        if not name:
            raise ValueError(f"plugin.toml in {plugin_dir} is missing 'name'")
        if not version:
            raise ValueError(f"plugin.toml in {plugin_dir} is missing 'version'")

        return cls(
            name=str(name),
            version=str(version),
            description=str(data.get("description", "")),
            author=str(data.get("author", "")),
            min_resonance_version=str(data.get("min_resonance_version", "")),
            plugin_dir=plugin_dir,
        )


# ---------------------------------------------------------------------------
# Menu registration helpers (module-level registry)
# ---------------------------------------------------------------------------

# These lists are populated by PluginContext.register_menu_node / register_menu_item
# and consumed by _build_main_menu() in web/handlers/menu.py.
_plugin_menu_nodes: list[dict[str, Any]] = []
_plugin_menu_items: list[dict[str, Any]] = []


def get_plugin_menu_nodes() -> list[dict[str, Any]]:
    """Return all menu nodes registered by plugins (read-only copy)."""
    return list(_plugin_menu_nodes)


def get_plugin_menu_items() -> list[dict[str, Any]]:
    """Return all menu items registered by plugins (read-only copy)."""
    return list(_plugin_menu_items)


def _clear_plugin_menus() -> None:
    """Remove all plugin-registered menu entries.  Used during teardown / tests."""
    _plugin_menu_nodes.clear()
    _plugin_menu_items.clear()


# ---------------------------------------------------------------------------
# PluginContext
# ---------------------------------------------------------------------------


class PluginContext:
    """Dependency-injection container passed to plugin ``setup()`` / ``teardown()``.

    A *PluginContext* is created per plugin by the :class:`PluginManager`.  It
    exposes a controlled subset of server functionality so that plugins can:

    * Register / unregister JSON-RPC commands
    * Add menu nodes and items visible on Jive devices
    * Mount additional FastAPI routers (REST endpoints)
    * Subscribe to the global :class:`EventBus`
    * Read (but not mutate) the music library and player registry

    All registrations are tracked so they can be cleanly undone when the
    plugin is unloaded.
    """

    def __init__(
        self,
        plugin_id: str,
        event_bus: EventBus,
        music_library: MusicLibrary,
        player_registry: PlayerRegistry,
        playlist_manager: PlaylistManager | None = None,
        *,
        _command_register: Any = None,
        _command_unregister: Any = None,
        _route_register: Any = None,
        _content_registry: ContentProviderRegistry | None = None,
        data_dir: Path | None = None,
    ) -> None:
        self.plugin_id = plugin_id
        """Unique identifier for this plugin (matches manifest *name*)."""

        self.event_bus: EventBus = event_bus
        """Global event bus — subscribe, unsubscribe, or publish events."""

        self.music_library: MusicLibrary = music_library
        """Read-only access to the music library (query artists, albums, …)."""

        self.player_registry: PlayerRegistry = player_registry
        """Read-only access to connected players."""

        self.playlist_manager: PlaylistManager | None = playlist_manager
        """Access to the playlist manager (may be ``None`` in tests)."""

        self.data_dir: Path = data_dir or Path(f"data/plugins/{plugin_id}")
        """Per-plugin data directory (created automatically if needed)."""

        # Internal callbacks wired by PluginManager ---------------------------
        self._command_register = _command_register
        self._command_unregister = _command_unregister
        self._route_register = _route_register
        self._content_registry = _content_registry

        # Track what this plugin registered so teardown can clean up.
        self._registered_commands: list[str] = []
        self._registered_menu_node_ids: list[str] = []
        self._registered_menu_item_ids: list[str] = []
        self._registered_event_handlers: list[tuple[str, Any]] = []
        self._registered_content_providers: list[str] = []

    # -- Command registration ------------------------------------------------

    def register_command(self, name: str, handler: CommandHandler) -> None:
        """Register a JSON-RPC command handler.

        Args:
            name: Command name (e.g. ``"favorites.items"``).  Must be unique.
            handler: Async handler with signature
                ``(ctx: CommandContext, command: list[Any]) -> dict[str, Any]``.

        Raises:
            RuntimeError: If the command name is already taken.
        """
        if self._command_register is None:
            raise RuntimeError("Command registration not available (test mode?)")
        self._command_register(name, handler)
        self._registered_commands.append(name)
        logger.debug("[%s] Registered command: %s", self.plugin_id, name)

    def unregister_command(self, name: str) -> None:
        """Unregister a previously registered JSON-RPC command."""
        if self._command_unregister is None:
            return
        self._command_unregister(name)
        if name in self._registered_commands:
            self._registered_commands.remove(name)
        logger.debug("[%s] Unregistered command: %s", self.plugin_id, name)

    # -- Menu registration ---------------------------------------------------

    def register_menu_node(
        self,
        node_id: str,
        parent: str,
        text: str,
        weight: int,
        **kwargs: Any,
    ) -> None:
        """Register a top-level menu node for Jive devices.

        Args:
            node_id: Unique node identifier (e.g. ``"favorites"``).
            parent: Parent node (usually ``"home"``).
            text: Display text.
            weight: Sort weight (lower = higher in list).
            **kwargs: Extra fields forwarded to the menu item dict.
        """
        entry: dict[str, Any] = {
            "text": text,
            "id": node_id,
            "node": parent,
            "weight": weight,
            "isANode": 1,
            "_plugin_id": self.plugin_id,
            **kwargs,
        }
        _plugin_menu_nodes.append(entry)
        self._registered_menu_node_ids.append(node_id)
        logger.debug("[%s] Registered menu node: %s", self.plugin_id, node_id)

    def register_menu_item(
        self,
        node_id: str,
        item: dict[str, Any],
    ) -> None:
        """Register a menu item under an existing node.

        Args:
            node_id: The parent node this item belongs to.
            item: Full menu-item dict (must contain at least ``"text"``).
        """
        item.setdefault("node", node_id)
        item["_plugin_id"] = self.plugin_id
        item_id = item.get("id", f"{self.plugin_id}_{len(self._registered_menu_item_ids)}")
        item.setdefault("id", item_id)
        _plugin_menu_items.append(item)
        self._registered_menu_item_ids.append(item_id)
        logger.debug("[%s] Registered menu item: %s", self.plugin_id, item_id)

    # -- Content provider registration ---------------------------------------

    def register_content_provider(
        self,
        provider_id: str,
        provider: ContentProvider,
    ) -> None:
        """Register a content provider (Radio, Podcast, etc.).

        Content providers supply external audio sources that are browseable,
        searchable, and playable.  The provider is automatically unregistered
        when the plugin is unloaded.

        Args:
            provider_id: Short unique identifier (e.g. ``"radio"``).
            provider: A :class:`~resonance.content_provider.ContentProvider`
                instance.

        Raises:
            RuntimeError: If no content-provider registry is available.
            ValueError: If *provider_id* is already taken.
        """
        if self._content_registry is None:
            raise RuntimeError(
                "Content provider registration not available (test mode?)"
            )
        self._content_registry.register(provider_id, provider)
        self._registered_content_providers.append(provider_id)
        logger.debug(
            "[%s] Registered content provider: %s", self.plugin_id, provider_id
        )

    def unregister_content_provider(self, provider_id: str) -> None:
        """Unregister a previously registered content provider."""
        if self._content_registry is None:
            return
        self._content_registry.unregister(provider_id)
        if provider_id in self._registered_content_providers:
            self._registered_content_providers.remove(provider_id)
        logger.debug(
            "[%s] Unregistered content provider: %s",
            self.plugin_id,
            provider_id,
        )

    # -- Route registration --------------------------------------------------

    def register_route(self, router: APIRouter) -> None:
        """Mount an additional FastAPI router.

        Args:
            router: A :class:`fastapi.APIRouter` with plugin-specific endpoints.
        """
        if self._route_register is None:
            raise RuntimeError("Route registration not available (test mode?)")
        self._route_register(router)
        logger.debug("[%s] Registered FastAPI router", self.plugin_id)

    # -- Data directory ------------------------------------------------------

    def ensure_data_dir(self) -> Path:
        """Create and return the per-plugin data directory."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self.data_dir

    # -- Cleanup (called by PluginManager) -----------------------------------

    async def _cleanup(self) -> None:
        """Undo all registrations made by this plugin.

        Called automatically by :class:`PluginManager` after ``teardown()``.
        """
        # Unregister commands
        for cmd_name in list(self._registered_commands):
            try:
                self.unregister_command(cmd_name)
            except Exception as exc:
                logger.warning("[%s] Failed to unregister command %s: %s", self.plugin_id, cmd_name, exc)
        self._registered_commands.clear()

        # Remove menu nodes owned by this plugin
        _plugin_menu_nodes[:] = [
            n for n in _plugin_menu_nodes if n.get("_plugin_id") != self.plugin_id
        ]
        self._registered_menu_node_ids.clear()

        # Remove menu items owned by this plugin
        _plugin_menu_items[:] = [
            i for i in _plugin_menu_items if i.get("_plugin_id") != self.plugin_id
        ]
        self._registered_menu_item_ids.clear()

        # Unsubscribe event handlers
        for event_type, handler in self._registered_event_handlers:
            try:
                await self.event_bus.unsubscribe(event_type, handler)
            except Exception as exc:
                logger.warning("[%s] Failed to unsubscribe %s: %s", self.plugin_id, event_type, exc)
        self._registered_event_handlers.clear()

        # Unregister content providers
        for provider_id in list(self._registered_content_providers):
            try:
                self.unregister_content_provider(provider_id)
            except Exception as exc:
                logger.warning(
                    "[%s] Failed to unregister content provider %s: %s",
                    self.plugin_id,
                    provider_id,
                    exc,
                )
        self._registered_content_providers.clear()

        logger.debug("[%s] Cleanup complete", self.plugin_id)

    # -- Convenience: tracked event subscription -----------------------------

    async def subscribe(self, event_type: str, handler: Any) -> None:
        """Subscribe to an event **and** track it for automatic cleanup.

        Prefer this over ``self.event_bus.subscribe()`` directly so that the
        handler is automatically unsubscribed when the plugin is unloaded.
        """
        await self.event_bus.subscribe(event_type, handler)
        self._registered_event_handlers.append((event_type, handler))

    # -- repr ----------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"PluginContext(plugin_id={self.plugin_id!r}, "
            f"commands={len(self._registered_commands)}, "
            f"menu_nodes={len(self._registered_menu_node_ids)}, "
            f"menu_items={len(self._registered_menu_item_ids)}, "
            f"content_providers={len(self._registered_content_providers)})"
        )
