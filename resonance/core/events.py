"""
Event Bus for Resonance.

This module provides a simple pub/sub event system for decoupled communication
between components. The primary use case is notifying Cometd subscribers
when player state changes.

Event types:
- server.started: All server components initialized, plugins started
- server.stopping: Server shutdown sequence beginning
- player.connected: A player connected to the server
- player.disconnected: A player disconnected
- player.status: Player status changed (play/pause/stop/volume/elapsed)
- player.track_started: Player reported STMs for a stream generation
- player.playlist: Playlist changed (add/remove/index)
- player.track_finished: Track finished playing (used for playlist advancement)
- player.live_stream_dropped: Live radio proxy stream ended unexpectedly (re-stream candidate)
- library.scan.started: Library scan started
- library.scan.progress: Library scan progress update
- library.scan.completed: Library scan completed

Usage:
    # Get the global event bus
    from resonance.core.events import event_bus

    # Subscribe to events
    async def on_player_status(event: PlayerStatusEvent) -> None:
        print(f"Player {event.player_id} is now {event.state}")

    await event_bus.subscribe("player.status", on_player_status)

    # Publish events
    await event_bus.publish(
        PlayerStatusEvent(
            player_id="aa:bb:cc:dd:ee:ff",
            state="playing",
            volume=80,
        )
    )
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Type alias for event handlers
EventHandler = Callable[["Event"], Coroutine[Any, Any, None]]


@dataclass
class Event:
    """Base class for all events."""

    event_type: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert event to dictionary for JSON serialization."""
        return {"type": self.event_type}


@dataclass
class PlayerConnectedEvent(Event):
    """Fired when a player connects to the server."""

    event_type: str = field(default="player.connected", init=False)
    player_id: str = ""
    name: str = ""
    model: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.event_type,
            "player_id": self.player_id,
            "name": self.name,
            "model": self.model,
        }


@dataclass
class PlayerDisconnectedEvent(Event):
    """Fired when a player disconnects from the server."""

    event_type: str = field(default="player.disconnected", init=False)
    player_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.event_type,
            "player_id": self.player_id,
        }


@dataclass
class PlayerStatusEvent(Event):
    """Fired when player status changes (play/pause/volume/elapsed/etc.)."""

    event_type: str = field(default="player.status", init=False)
    player_id: str = ""
    state: str = ""  # playing, paused, stopped
    volume: int = 0
    muted: bool = False
    elapsed_seconds: float = 0.0
    elapsed_milliseconds: int = 0
    duration: float = 0.0
    current_track: dict[str, Any] | None = None
    playlist_index: int = 0
    playlist_tracks: int = 0

    def to_dict(self) -> dict[str, Any]:
        result = {
            "type": self.event_type,
            "player_id": self.player_id,
            "state": self.state,
            "volume": self.volume,
            "muted": self.muted,
            "elapsed": self.elapsed_seconds,
            "elapsed_ms": self.elapsed_milliseconds,
            "duration": self.duration,
            "playlist_index": self.playlist_index,
            "playlist_tracks": self.playlist_tracks,
        }
        if self.current_track:
            result["current_track"] = self.current_track
        return result


@dataclass
class PlayerTrackFinishedEvent(Event):
    """Fired when a track finishes playing on a player.

    IMPORTANT:
    - Track-finished signals (e.g. Slimproto STAT "STMd") can arrive late relative to
      server-side stream switching (manual track changes, cancellation, stop/flush).
    - To prevent unintended auto-advance (playing track +1) after a manual switch,
      producers should attach a per-player stream generation, and consumers should
      only auto-advance when the generation matches the current stream.
    """

    event_type: str = field(default="player.track_finished", init=False)
    player_id: str = ""
    stream_generation: int | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "type": self.event_type,
            "player_id": self.player_id,
        }
        if self.stream_generation is not None:
            result["stream_generation"] = self.stream_generation
        return result


@dataclass
class PlayerDecodeReadyEvent(Event):
    """Fired when a player's decoder finishes reading input data (STMd).

    This signals that the player has consumed all data for the current track
    and is ready to receive the next one. Used by the crossfade/prefetch
    engine to prepare the next track before the current one finishes output.

    The stream_generation allows ignoring stale STMd events from previous
    streams (same pattern as PlayerTrackFinishedEvent).
    """

    event_type: str = field(default="player.decode_ready", init=False)
    player_id: str = ""
    stream_generation: int | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "type": self.event_type,
            "player_id": self.player_id,
        }
        if self.stream_generation is not None:
            result["stream_generation"] = self.stream_generation
        return result

@dataclass
class LiveStreamDroppedEvent(Event):
    """Fired when a live/radio proxy stream ends unexpectedly.

    This mirrors LMS's ``_RetryOrNext`` in ``StreamingController.pm`` (L910-930):
    when a live remote stream drops while the player is still playing, LMS
    attempts to re-connect to the same URL so audio resumes seamlessly.

    The streaming route fires this event from the proxy generator's ``finally``
    block when a live stream ends for a non-intentional reason (i.e. not
    cancelled by the server and not disconnected by the player).

    The handler in ``server.py`` checks LMS-equivalent conditions before
    re-queuing:
    - Stream generation still matches (not replaced by user action)
    - At least 10 seconds of playback elapsed (stream was working)
    - Retry limit not exceeded (prevent infinite reconnect loops)
    """

    event_type: str = field(default="player.live_stream_dropped", init=False)
    player_id: str = ""
    stream_generation: int | None = None
    remote_url: str = ""
    content_type: str = "audio/mpeg"
    title: str = ""

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "type": self.event_type,
            "player_id": self.player_id,
        }
        if self.stream_generation is not None:
            result["stream_generation"] = self.stream_generation
        if self.remote_url:
            result["remote_url"] = self.remote_url
        if self.title:
            result["title"] = self.title
        return result


@dataclass
class PlayerTrackStartedEvent(Event):
    """Fired when player confirms stream start (Slimproto STMs)."""

    event_type: str = field(default="player.track_started", init=False)
    player_id: str = ""
    stream_generation: int | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "type": self.event_type,
            "player_id": self.player_id,
        }
        if self.stream_generation is not None:
            result["stream_generation"] = self.stream_generation
        return result

@dataclass
class PlayerPlaylistEvent(Event):
    """Fired when playlist changes."""

    event_type: str = field(default="player.playlist", init=False)
    player_id: str = ""
    action: str = ""  # add, remove, clear, move, index
    index: int = 0
    count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.event_type,
            "player_id": self.player_id,
            "action": self.action,
            "index": self.index,
            "count": self.count,
        }


@dataclass
class ServerStartedEvent(Event):
    """Fired after all server components are initialized and plugins are started.

    Plugins can subscribe to this event to perform deferred initialization
    that depends on the full server being operational.
    """

    event_type: str = field(default="server.started", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.event_type}


@dataclass
class ServerStoppingEvent(Event):
    """Fired when the server begins its shutdown sequence.

    Plugins can subscribe to this event to persist state or release
    resources before the server components are torn down.
    """

    event_type: str = field(default="server.stopping", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.event_type}


@dataclass
class LibraryScanEvent(Event):
    """Fired during library scanning."""

    event_type: str = field(default="library.scan", init=False)
    status: str = ""  # started, progress, completed, failed
    scanned: int = 0
    total: int = 0
    current_path: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        result = {
            "type": self.event_type,
            "status": self.status,
            "scanned": self.scanned,
            "total": self.total,
        }
        if self.current_path:
            result["current_path"] = self.current_path
        if self.error:
            result["error"] = self.error
        return result


class EventBus:
    """
    Simple async pub/sub event bus.

    Supports:
    - Multiple handlers per event type
    - Wildcard subscriptions (e.g., "player.*")
    - Async handlers
    - Error isolation (one handler failing doesn't affect others)
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, event_type: str, handler: EventHandler) -> None:
        """
        Subscribe to events of a specific type.

        Args:
            event_type: Event type to subscribe to. Use "*" suffix for wildcards.
            handler: Async function to call when event is published.
        """
        async with self._lock:
            if event_type not in self._handlers:
                self._handlers[event_type] = []
            self._handlers[event_type].append(handler)
            logger.debug("Subscribed to %s: %s", event_type, handler)

    async def unsubscribe(self, event_type: str, handler: EventHandler) -> bool:
        """
        Unsubscribe a handler from an event type.

        Returns True if handler was found and removed.
        """
        async with self._lock:
            if event_type in self._handlers:
                try:
                    self._handlers[event_type].remove(handler)
                    logger.debug("Unsubscribed from %s: %s", event_type, handler)
                    return True
                except ValueError:
                    pass
            return False

    async def publish(self, event: Event) -> int:
        """
        Publish an event to all subscribed handlers.

        Args:
            event: The event to publish.

        Returns:
            Number of handlers that received the event.
        """
        event_type = event.event_type
        handlers_called = 0

        # Collect matching handlers
        async with self._lock:
            matching_handlers: list[EventHandler] = []

            # Exact match
            if event_type in self._handlers:
                matching_handlers.extend(self._handlers[event_type])

            # Wildcard matches (e.g., "player.*" matches "player.status")
            for pattern, handlers in self._handlers.items():
                if pattern.endswith(".*"):
                    prefix = pattern[:-2]
                    if event_type.startswith(prefix + "."):
                        matching_handlers.extend(handlers)
                elif pattern == "*":
                    # Global wildcard
                    matching_handlers.extend(handlers)

        # Call handlers outside of lock
        for handler in matching_handlers:
            try:
                await handler(event)
                handlers_called += 1
            except Exception as e:
                logger.exception("Error in event handler for %s: %s", event_type, e)

        if handlers_called > 0:
            logger.debug("Published %s to %d handlers", event_type, handlers_called)

        return handlers_called

    def publish_sync(self, event: Event) -> None:
        """
        Schedule event publication from synchronous code.

        This creates a task to publish the event asynchronously.
        Useful when you need to fire events from sync callbacks.
        """
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.publish(event))
        except RuntimeError:
            # No running event loop
            logger.warning("Cannot publish event %s: no running event loop", event.event_type)

    async def clear(self) -> None:
        """Remove all subscriptions."""
        async with self._lock:
            self._handlers.clear()
            logger.debug("Cleared all event subscriptions")


# Global event bus instance
event_bus = EventBus()
