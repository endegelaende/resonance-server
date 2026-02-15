"""
Per-Player Display State Manager.

Manages display state for each connected Squeezebox graphics player,
drives periodic screen updates (e.g. elapsed time ticking), and
reacts to player events (track change, pause, power off, etc.).

Architecture
============
- One ``DisplayManager`` instance per server.
- Each connected graphics player gets a ``PlayerDisplay`` state object.
- The manager subscribes to player events via the event bus.
- A background asyncio task drives periodic refreshes (every ~1 s for
  elapsed-time updates during playback).

Integration points
==================
- ``SlimprotoServer.send_display_bitmap()`` / ``clear_display()`` — frame TX
- ``PlayerRegistry`` — player lookup and state
- ``PlaylistManager`` — current track metadata
- ``event_bus`` — subscribe to playback / playlist events

Phase 3–4 additions
===================
- ``ScreensaverType`` per player (CLOCK, BLANK, NOW_PLAYING_MINI, NONE).
- Screensaver state: after ``_SCREENSAVER_TIMEOUT`` seconds of idle the
  display transitions to the configured screensaver.
- Clock screensaver refreshes every second; blank just sends zeros.
- ``update_menu_advanced()`` for menus with position indicator & arrows.

Reference: ``Slim::Display::Display::update()`` and the per-player
display timer logic in LMS.  Screensaver logic follows
``Slim::Buttons::ScreenSaver::screenSaver()`` and
``Slim::Plugin::DateTime::Plugin``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any

from resonance.display import (
    DISPLAY_NONE,
    DisplaySpec,
    FontConfig,
    ScreensaverType,
    default_font_config,
    display_spec_for_model,
)
from resonance.display.fonts import FontCache, get_font_cache
from resonance.display.renderer import DisplayRenderer, RenderedScreen, ScreenParts

if TYPE_CHECKING:
    from resonance.core.playlist import PlaylistManager
    from resonance.protocol.slimproto import SlimprotoServer
    from resonance.streaming.server import StreamingServer

logger = logging.getLogger(__name__)

# How often to refresh the display during playback (seconds).
_PLAYBACK_REFRESH_INTERVAL = 1.0

# How often to refresh the display when idle (seconds).
_IDLE_REFRESH_INTERVAL = 10.0

# Timeout before switching to idle/screensaver (seconds).
_IDLE_TIMEOUT = 30.0

# Timeout before switching from IDLE to SCREENSAVER (seconds).
# Matches LMS default ``screensavertimeout`` pref (30 s).
_SCREENSAVER_TIMEOUT = 30.0

# How often to refresh the screensaver display (seconds).
# Clock needs 1 s; others can be slower.
_SCREENSAVER_REFRESH_INTERVAL = 1.0


def _parse_icy_title(icy_title: str) -> tuple[str, str]:
    """Parse an ICY StreamTitle into ``(artist, title)``.

    Mirrors LMS ``HTTP.pm`` ``getMetadataFor()`` L1085-1092: when the
    ICY string contains exactly one ``" - "`` separator it is split
    into *artist* and *title*; otherwise the whole string is the title.

    This is a local copy of ``resonance.web.handlers.status._parse_icy_title``
    to avoid a cross-layer import (display → web).
    """
    if not icy_title:
        return ("", "")
    parts = icy_title.split(" - ")
    if len(parts) == 2:
        return (parts[0].strip(), parts[1].strip())
    return ("", icy_title.strip())


class DisplayState(Enum):
    """Current display mode for a player."""

    OFF = auto()           # Player powered off — display dark
    NOW_PLAYING = auto()   # Showing playback info
    MENU = auto()          # Showing a menu / browse screen
    IDLE = auto()          # Idle (pre-screensaver)
    SCREENSAVER = auto()   # Active screensaver (clock, blank, mini-NP)
    SHOW_BRIEFLY = auto()  # Transient overlay (volume, etc.)


@dataclass
class PlayerDisplay:
    """Per-player display state.

    Tracks the current screen mode, cached render output, scroll state,
    and the timestamp of the last user interaction (for idle timeout).
    """

    player_id: str
    spec: DisplaySpec = field(default_factory=lambda: DISPLAY_NONE)
    font_config: FontConfig = field(default_factory=FontConfig)
    state: DisplayState = DisplayState.IDLE
    renderer: DisplayRenderer | None = None

    # Current screen content
    current_parts: ScreenParts = field(default_factory=ScreenParts)
    last_rendered: RenderedScreen | None = None
    last_frame_sent: bytes = b""

    # Scroll state
    scroll_offset: int = 0
    scroll_direction: int = 1  # +1 = left-to-right, -1 = reverse
    scroll_pixels_per_tick: int = 2

    # Playback tracking (for elapsed time updates)
    playback_started_at: float = 0.0  # monotonic time when playback started
    playback_elapsed_at_start: float = 0.0  # elapsed seconds at start
    is_playing: bool = False
    is_paused: bool = False

    # Track metadata cache
    track_title: str = ""
    track_artist: str = ""
    track_album: str = ""
    track_duration_s: float = 0.0

    # Timing
    last_interaction: float = 0.0  # monotonic time of last user action
    last_update: float = 0.0  # monotonic time of last display update

    # showBriefly state
    show_briefly_parts: ScreenParts | None = None
    show_briefly_until: float = 0.0

    # Screensaver config (Phase 4)
    screensaver_type: ScreensaverType = ScreensaverType.CLOCK
    screensaver_timeout: float = _SCREENSAVER_TIMEOUT

    @property
    def has_display(self) -> bool:
        """Whether this player has a graphic display."""
        return self.spec.frame_bytes > 0

    @property
    def current_elapsed(self) -> float:
        """Compute current elapsed time based on play state."""
        if not self.is_playing or self.is_paused:
            return self.playback_elapsed_at_start
        now = time.monotonic()
        return self.playback_elapsed_at_start + (now - self.playback_started_at)


class DisplayManager:
    """Manages display rendering and updates for all connected players.

    Usage::

        manager = DisplayManager(slimproto_server)
        await manager.start()
        # ... server runs ...
        await manager.stop()
    """

    def __init__(
        self,
        slimproto: SlimprotoServer | None = None,
        font_cache: FontCache | None = None,
        *,
        playlist_manager: PlaylistManager | None = None,
        streaming_server: StreamingServer | None = None,
    ) -> None:
        self._slimproto = slimproto
        self._font_cache = font_cache or get_font_cache()
        self._playlist_manager = playlist_manager
        self._streaming_server = streaming_server
        self._players: dict[str, PlayerDisplay] = {}
        self._update_task: asyncio.Task[None] | None = None
        self._running = False
        self._event_subscriptions: list[Any] = []

    @property
    def font_cache(self) -> FontCache:
        return self._font_cache

    @font_cache.setter
    def font_cache(self, cache: FontCache) -> None:
        self._font_cache = cache
        # Update all existing renderers
        for pd in self._players.values():
            if pd.renderer is not None:
                pd.renderer = DisplayRenderer(cache, pd.spec, pd.font_config)

    # -- Lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        """Start the periodic display update loop and subscribe to events."""
        if self._running:
            return

        self._running = True
        self._update_task = asyncio.create_task(
            self._update_loop(), name="display-manager-update"
        )
        self._subscribe_events()
        logger.info("DisplayManager started")

    async def stop(self) -> None:
        """Stop the update loop and unsubscribe from events."""
        self._running = False
        self._unsubscribe_events()

        if self._update_task is not None:
            self._update_task.cancel()
            try:
                await self._update_task
            except asyncio.CancelledError:
                pass
            self._update_task = None

        logger.info("DisplayManager stopped")

    # -- Player registration -------------------------------------------------

    def register_player(
        self,
        player_id: str,
        model: str = "",
    ) -> PlayerDisplay:
        """Register a player and create its display state.

        Called when a player connects via Slimproto.
        """
        spec = display_spec_for_model(model)
        font_config = default_font_config(spec)

        renderer: DisplayRenderer | None = None
        if spec.frame_bytes > 0 and self._font_cache.font_names:
            renderer = DisplayRenderer(self._font_cache, spec, font_config)

        pd = PlayerDisplay(
            player_id=player_id,
            spec=spec,
            font_config=font_config,
            renderer=renderer,
            last_interaction=time.monotonic(),
            last_update=0.0,
        )
        self._players[player_id] = pd

        logger.debug(
            "Registered display for player %s: model=%s spec=%s",
            player_id, model, spec.model.value,
        )
        return pd

    def unregister_player(self, player_id: str) -> None:
        """Remove a player's display state (on disconnect)."""
        self._players.pop(player_id, None)

    def get_player_display(self, player_id: str) -> PlayerDisplay | None:
        """Look up the display state for a player."""
        return self._players.get(player_id)

    # -- Display updates (called by playback / menu handlers) ----------------

    async def update_now_playing(
        self,
        player_id: str,
        *,
        title: str = "",
        artist: str = "",
        album: str = "",
        elapsed_s: float = 0.0,
        duration_s: float = 0.0,
        is_playing: bool = True,
        is_paused: bool = False,
    ) -> None:
        """Update the now-playing screen for a player."""
        pd = self._players.get(player_id)
        if pd is None or not pd.has_display:
            return

        pd.track_title = title
        pd.track_artist = artist
        pd.track_album = album
        pd.track_duration_s = duration_s
        pd.is_playing = is_playing
        pd.is_paused = is_paused
        pd.playback_elapsed_at_start = elapsed_s
        pd.playback_started_at = time.monotonic()
        pd.state = DisplayState.NOW_PLAYING
        pd.scroll_offset = 0

        await self._render_and_send(pd)

    async def update_menu(
        self,
        player_id: str,
        items: list[str],
        *,
        selected_index: int = 0,
    ) -> None:
        """Update the menu display for a player."""
        pd = self._players.get(player_id)
        if pd is None or not pd.has_display or pd.renderer is None:
            return

        pd.state = DisplayState.MENU
        pd.last_interaction = time.monotonic()
        pd.scroll_offset = 0

        rendered = pd.renderer.render_menu(items, selected_index=selected_index)
        await self._send_frame(pd, rendered.bitmap)

    async def update_menu_advanced(
        self,
        player_id: str,
        items: list[str],
        *,
        selected_index: int = 0,
        has_submenu: list[bool] | None = None,
        show_position: bool = True,
    ) -> None:
        """Update the menu display with position indicator and sub-menu arrows.

        Delegates to ``DisplayRenderer.render_menu_advanced()``.
        """
        pd = self._players.get(player_id)
        if pd is None or not pd.has_display or pd.renderer is None:
            return

        pd.state = DisplayState.MENU
        pd.last_interaction = time.monotonic()
        pd.scroll_offset = 0

        rendered = pd.renderer.render_menu_advanced(
            items,
            selected_index=selected_index,
            has_submenu=has_submenu,
            show_position=show_position,
        )
        await self._send_frame(pd, rendered.bitmap)

    async def show_briefly(
        self,
        player_id: str,
        parts: ScreenParts,
        *,
        duration: float = 3.0,
    ) -> None:
        """Show a transient screen (e.g. volume change) for a few seconds.

        After *duration* seconds, the previous screen is restored.
        """
        pd = self._players.get(player_id)
        if pd is None or not pd.has_display or pd.renderer is None:
            return

        pd.show_briefly_parts = parts
        pd.show_briefly_until = time.monotonic() + duration

        rendered = pd.renderer.render(parts, allow_scroll=False)
        await self._send_frame(pd, rendered.bitmap)

    async def set_power(self, player_id: str, *, power_on: bool) -> None:
        """Handle power on/off — blank display or restore."""
        pd = self._players.get(player_id)
        if pd is None or not pd.has_display:
            return

        if power_on:
            pd.state = DisplayState.IDLE
            pd.last_interaction = time.monotonic()
            await self._render_and_send(pd)
        else:
            pd.state = DisplayState.OFF
            await self._send_frame(pd, pd.renderer.render_blank() if pd.renderer else b"")

    def set_screensaver(
        self,
        player_id: str,
        screensaver_type: ScreensaverType,
        *,
        timeout: float | None = None,
    ) -> None:
        """Configure the screensaver type (and optional timeout) for a player."""
        pd = self._players.get(player_id)
        if pd is None:
            return

        pd.screensaver_type = screensaver_type
        if timeout is not None:
            pd.screensaver_timeout = timeout

    async def clear_display(self, player_id: str) -> None:
        """Send a blank frame to the player's display."""
        pd = self._players.get(player_id)
        if pd is None or not pd.has_display:
            return

        blank = b"\x00" * pd.spec.frame_bytes
        await self._send_frame(pd, blank)

    # -- Internal: rendering and sending -------------------------------------

    async def _render_and_send(self, pd: PlayerDisplay) -> None:
        """Render the current screen and send the frame to the player."""
        if pd.renderer is None:
            return

        now = time.monotonic()

        # Check if showBriefly is active
        if pd.show_briefly_parts is not None:
            if now < pd.show_briefly_until:
                return  # Don't overwrite the brief display
            pd.show_briefly_parts = None

        if pd.state == DisplayState.NOW_PLAYING:
            elapsed = pd.current_elapsed
            rendered = pd.renderer.render_now_playing(
                title=pd.track_title,
                artist=pd.track_artist,
                album=pd.track_album,
                elapsed_s=elapsed,
                duration_s=pd.track_duration_s,
                show_elapsed=True,
                show_progress=True,
                is_paused=pd.is_paused,
            )
        elif pd.state == DisplayState.IDLE:
            rendered = pd.renderer.render_idle(text="Resonance", center=True)
        elif pd.state == DisplayState.SCREENSAVER:
            rendered = self._render_screensaver(pd)
            if rendered is None:
                return  # BLANK already handled, or no screensaver
        elif pd.state == DisplayState.OFF:
            await self._send_frame(pd, pd.renderer.render_blank())
            return
        else:
            return  # MENU and SHOW_BRIEFLY are handled by their update methods

        # Handle scroll animation
        if rendered.scroll_line >= 0 and pd.scroll_offset > 0:
            # Use scroll frame builder for animated frames
            frame = pd.renderer.build_scroll_frame(
                static_bitmap=rendered.bitmap,
                scroll_bitmap=rendered.scroll_bitmap,
                scroll_offset=pd.scroll_offset,
                overlay_start=pd.spec.frame_bytes
                    - len(b""),  # Full width for now
            )
        else:
            frame = rendered.bitmap

        pd.last_rendered = rendered
        await self._send_frame(pd, frame)

    def _render_screensaver(self, pd: PlayerDisplay) -> RenderedScreen | None:
        """Render the appropriate screensaver for a player.

        Returns ``None`` when the screensaver has been fully handled
        internally (e.g. BLANK sends the frame directly).
        """
        if pd.renderer is None:
            return None

        stype = pd.screensaver_type

        if stype == ScreensaverType.BLANK:
            # Blank is just zeros — send directly and return None
            # (the update loop will suppress duplicates automatically)
            return RenderedScreen(bitmap=pd.renderer.render_blank())

        if stype == ScreensaverType.CLOCK:
            return pd.renderer.render_clock()

        if stype == ScreensaverType.NOW_PLAYING_MINI:
            return pd.renderer.render_now_playing_mini(
                title=pd.track_title,
                artist=pd.track_artist,
            )

        # ScreensaverType.NONE — fall back to idle text
        return pd.renderer.render_idle(text="Resonance", center=True)

    async def _send_frame(self, pd: PlayerDisplay, frame: bytes) -> None:
        """Send a rendered frame to the physical player display.

        Suppresses sending if the frame is identical to the last one sent.
        """
        if not frame:
            return

        # Suppress duplicate frames
        if frame == pd.last_frame_sent:
            return

        pd.last_frame_sent = frame
        pd.last_update = time.monotonic()

        if self._slimproto is None:
            return

        try:
            if pd.spec.frame_command == "grfe":
                await self._slimproto.send_display_bitmap(
                    pd.player_id,
                    frame,
                    offset=0,
                    transition="c",
                    param=0,
                )
            elif pd.spec.frame_command == "grfd":
                await self._slimproto.send_display_framebuffer(
                    pd.player_id,
                    frame,
                    offset=pd.spec.width * pd.spec.bytes_per_column,
                )
        except Exception:
            logger.debug(
                "Failed to send display frame to %s", pd.player_id, exc_info=True,
            )

    # -- Periodic update loop ------------------------------------------------

    async def _update_loop(self) -> None:
        """Background task that periodically refreshes displays."""
        while self._running:
            try:
                now = time.monotonic()

                for pd in list(self._players.values()):
                    if not pd.has_display or pd.renderer is None:
                        continue

                    if pd.state == DisplayState.OFF:
                        continue

                    # Check showBriefly expiry
                    if pd.show_briefly_parts is not None and now >= pd.show_briefly_until:
                        pd.show_briefly_parts = None
                        # Fall through to normal render

                    if pd.state == DisplayState.NOW_PLAYING and pd.is_playing and not pd.is_paused:
                        # Active playback — refresh every second for elapsed time
                        if now - pd.last_update >= _PLAYBACK_REFRESH_INTERVAL:
                            # Advance scroll
                            if (
                                pd.last_rendered is not None
                                and pd.last_rendered.scroll_line >= 0
                            ):
                                pd.scroll_offset += pd.scroll_pixels_per_tick
                                if pd.scroll_offset > pd.last_rendered.scroll_width:
                                    pd.scroll_offset = 0

                            await self._render_and_send(pd)

                    elif pd.state == DisplayState.IDLE:
                        # Idle — refresh less frequently
                        if now - pd.last_update >= _IDLE_REFRESH_INTERVAL:
                            await self._render_and_send(pd)

                    elif pd.state == DisplayState.SCREENSAVER:
                        # Screensaver — refresh at screensaver interval
                        if now - pd.last_update >= _SCREENSAVER_REFRESH_INTERVAL:
                            await self._render_and_send(pd)

                    # Idle timeout: switch from NOW_PLAYING (paused) to IDLE
                    if (
                        pd.state == DisplayState.NOW_PLAYING
                        and pd.is_paused
                        and now - pd.last_interaction > _IDLE_TIMEOUT
                    ):
                        pd.state = DisplayState.IDLE
                        pd.last_update = 0.0  # force immediate render
                        await self._render_and_send(pd)

                    # Screensaver timeout: switch from IDLE to SCREENSAVER
                    if (
                        pd.state == DisplayState.IDLE
                        and pd.screensaver_type != ScreensaverType.NONE
                        and now - pd.last_interaction > pd.screensaver_timeout
                    ):
                        pd.state = DisplayState.SCREENSAVER
                        pd.last_frame_sent = b""  # force frame send
                        pd.last_update = 0.0
                        await self._render_and_send(pd)

            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Display update loop error", exc_info=True)

            await asyncio.sleep(0.25)  # 4 Hz tick for smooth scrolling

    # -- Event bus integration -----------------------------------------------

    def _subscribe_events(self) -> None:
        """Subscribe to player events for display updates."""
        try:
            from resonance.core.events import (
                PlayerPlaybackEvent,
                PlayerPlaylistEvent,
                PlayerPowerEvent,
                PlayerVolumeEvent,
                event_bus,
            )

            self._event_subscriptions = [
                event_bus.subscribe(PlayerPlaybackEvent, self._on_playback_event),
                event_bus.subscribe(PlayerPlaylistEvent, self._on_playlist_event),
                event_bus.subscribe(PlayerPowerEvent, self._on_power_event),
                event_bus.subscribe(PlayerVolumeEvent, self._on_volume_event),
            ]
        except ImportError:
            logger.debug("Event bus not available — display updates will be manual only")

    def _unsubscribe_events(self) -> None:
        """Unsubscribe from all events."""
        try:
            from resonance.core.events import event_bus

            for sub in self._event_subscriptions:
                event_bus.unsubscribe(sub)
        except (ImportError, Exception):
            pass
        self._event_subscriptions.clear()

    async def _on_playback_event(self, event: Any) -> None:
        """Handle playback state changes (play/pause/stop/track change)."""
        player_id = getattr(event, "player_id", None)
        if not player_id:
            return

        pd = self._players.get(player_id)
        if pd is None or not pd.has_display:
            return

        action = getattr(event, "action", "")
        pd.last_interaction = time.monotonic()

        if action in ("play", "unpause"):
            pd.is_playing = True
            pd.is_paused = False
            pd.playback_started_at = time.monotonic()
            pd.state = DisplayState.NOW_PLAYING
            pd.last_frame_sent = b""  # force re-render after screensaver
            # Load track metadata so the first render has correct data.
            # _on_playlist_event may also set this, but play can arrive first.
            self._load_track_metadata(pd, player_id)
            await self._render_and_send(pd)

        elif action == "pause":
            pd.is_paused = True
            pd.playback_elapsed_at_start = pd.current_elapsed
            await self._render_and_send(pd)

        elif action == "stop":
            pd.is_playing = False
            pd.is_paused = False
            pd.state = DisplayState.IDLE
            await self._render_and_send(pd)

    async def _on_playlist_event(self, event: Any) -> None:
        """Handle playlist changes — update display metadata on track/ICY change.

        Mirrors LMS ``Slim::Buttons::Playlist::newTitle`` callback
        (registered via ``setCurrentTitleChangeCallback`` in Info.pm L54)
        and the ``setCurrentTitle()`` flow (Info.pm L516-555) which
        fires ``['playlist', 'newsong', $title]`` on ICY title changes.

        Actions handled:

        - ``index`` / ``load``: Track changed or playlist loaded —
          read current track metadata from ``PlaylistManager`` and
          re-render the now-playing screen.
        - ``newmetadata``: ICY StreamTitle changed — read the new title
          from ``StreamingServer.get_icy_title()`` and update the display.
        """
        player_id = getattr(event, "player_id", None)
        if not player_id:
            return

        pd = self._players.get(player_id)
        if pd is None or not pd.has_display:
            return

        action = getattr(event, "action", "")

        if action in ("index", "load"):
            # Track change or playlist load — refresh metadata from PlaylistManager
            pd.last_interaction = time.monotonic()
            if self._load_track_metadata(pd, player_id):
                pd.state = DisplayState.NOW_PLAYING
                pd.scroll_offset = 0
                pd.last_frame_sent = b""  # force re-render
                await self._render_and_send(pd)
                logger.debug(
                    "Display metadata updated for %s: %r - %r",
                    player_id, pd.track_artist, pd.track_title,
                )

        elif action == "newmetadata":
            # ICY title change — mirrors LMS setCurrentTitle() → newTitle() callback
            icy_title: str | None = None
            if self._streaming_server is not None:
                icy_title = self._streaming_server.get_icy_title(player_id)

            if icy_title:
                artist, title = _parse_icy_title(icy_title)
                pd.track_title = title or icy_title
                if artist:
                    pd.track_artist = artist
                pd.scroll_offset = 0
                pd.last_frame_sent = b""  # force re-render
                await self._render_and_send(pd)
                logger.debug(
                    "Display ICY title updated for %s: %r",
                    player_id, icy_title,
                )

    def _load_track_metadata(self, pd: PlayerDisplay, player_id: str) -> bool:
        """Load current track metadata from PlaylistManager into *pd*.

        Returns ``True`` if metadata was successfully loaded, ``False``
        if the PlaylistManager is unavailable or the playlist is empty.
        """
        if self._playlist_manager is None:
            return False
        if player_id not in self._playlist_manager:
            return False

        playlist = self._playlist_manager.get(player_id)
        track = playlist.current_track
        if track is None:
            return False

        pd.track_title = track.title or ""
        pd.track_artist = track.artist or ""
        pd.track_album = track.album or ""
        pd.track_duration_s = track.duration_ms / 1000.0 if track.duration_ms else 0.0
        return True

    async def _on_power_event(self, event: Any) -> None:
        """Handle power on/off events."""
        player_id = getattr(event, "player_id", None)
        power = getattr(event, "power", None)
        if player_id is None or power is None:
            return
        await self.set_power(player_id, power_on=bool(power))

    async def _on_volume_event(self, event: Any) -> None:
        """Handle volume change — show brief volume overlay."""
        player_id = getattr(event, "player_id", None)
        volume = getattr(event, "volume", None)
        if player_id is None or volume is None:
            return

        pd = self._players.get(player_id)
        if pd is None or not pd.has_display:
            return

        pd.last_interaction = time.monotonic()

        # Wake from screensaver on user interaction
        if pd.state == DisplayState.SCREENSAVER:
            pd.state = DisplayState.IDLE
            pd.last_frame_sent = b""

        vol_text = f"Volume: {volume}"
        parts = ScreenParts(center=[None, vol_text])
        await self.show_briefly(player_id, parts, duration=2.0)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_default_manager: DisplayManager | None = None


def get_display_manager() -> DisplayManager:
    """Return the global DisplayManager singleton."""
    global _default_manager
    if _default_manager is None:
        _default_manager = DisplayManager()
    return _default_manager


def set_display_manager(manager: DisplayManager) -> None:
    """Set the global DisplayManager singleton (for DI / testing)."""
    global _default_manager
    _default_manager = manager
