"""Main server — orchestrates all components and manages lifecycle."""

import asyncio
import logging
import os
import signal
import uuid
from pathlib import Path
from typing import Any

from resonance.content_provider import ContentProviderRegistry
from resonance.core.alarm_runtime import AlarmRuntime
from resonance.core.artwork import ArtworkManager
from resonance.core.events import (
    Event,
    LiveStreamDroppedEvent,
    PlayerDecodeReadyEvent,
    PlayerTrackFinishedEvent,
    PlayerTrackStartedEvent,
    ServerStartedEvent,
    ServerStoppingEvent,
    event_bus,
)
from resonance.core.library import MusicLibrary
from resonance.core.library_db import LibraryDb
from resonance.core.playlist import PlaylistManager
from resonance.display.manager import DisplayManager, set_display_manager
from resonance.player.registry import PlayerRegistry
from resonance.plugin_installer import PluginInstaller
from resonance.plugin_manager import PluginManager
from resonance.plugin_repository import PluginRepository
from resonance.protocol.cli import CliServer
from resonance.protocol.discovery import UDPDiscoveryServer
from resonance.protocol.slimproto import SlimprotoServer
from resonance.streaming.seek_coordinator import init_seek_coordinator
from resonance.streaming.server import StreamingServer
from resonance.web.handlers.alarm import configure_persistence as configure_alarm_persistence
from resonance.web.handlers.alarm import load_alarms
from resonance.web.handlers.compat import configure_prefs_persistence, load_all_player_prefs
from resonance.web.jsonrpc import register_command, unregister_command
from resonance.web.server import WebServer

logger = logging.getLogger(__name__)

# Path for persisting server UUID
SERVER_UUID_FILE = Path("cache/server_uuid")

# Feature flag: enable bitmap display rendering for SB2/SB3/Classic/Boom.
# Default OFF until hardware-verified.  Set RESONANCE_DISPLAY=1 to activate.
DISPLAY_RENDERING_ENABLED = os.environ.get("RESONANCE_DISPLAY", "0") == "1"


def get_or_create_server_uuid() -> str:
    """
    Get or create a persistent server UUID.

    The UUID is stored in cache/server_uuid and reused across restarts.
    This matches LMS behavior where each server has a unique identity.

    Format: Full UUID v4 string (36 chars with dashes), e.g. "1a421556-465b-4802-9599-654aa2d6dbd4"
    LMS uses: UUID::Tiny::create_UUID_as_string(UUID_V4())
    """
    SERVER_UUID_FILE.parent.mkdir(parents=True, exist_ok=True)

    if SERVER_UUID_FILE.exists():
        try:
            stored_uuid = SERVER_UUID_FILE.read_text().strip()
            # Accept both old 8-char format and new 36-char UUID v4 format
            # If old format, we'll regenerate a proper UUID
            if stored_uuid and len(stored_uuid) == 36 and stored_uuid.count('-') == 4:
                logger.debug("Using existing server UUID: %s", stored_uuid)
                return stored_uuid
            elif stored_uuid:
                logger.info("Upgrading old 8-char UUID to full UUID v4 format")
        except Exception as e:
            logger.warning("Could not read server UUID: %s", e)

    # Generate new UUID v4 (full 36-char format like LMS)
    # LMS uses: UUID::Tiny::create_UUID_as_string(UUID_V4())
    new_uuid = str(uuid.uuid4())

    try:
        SERVER_UUID_FILE.write_text(new_uuid)
        logger.info("Generated new server UUID: %s", new_uuid)
    except Exception as e:
        logger.warning("Could not save server UUID: %s", e)

    return new_uuid


class ResonanceServer:
    """
    Main Resonance server that coordinates all components.

    The server manages:
    - Slimproto protocol server (port 3483) for player communication
    - Streaming server for audio delivery
    - Player registry for tracking connected players
    - Core music library (SQLite + scanner)
    - Playlist manager for per-player queues
    - Web server for HTTP/JSON-RPC API

    NOTE ON TRACK ADVANCEMENT:
    - We auto-advance the playlist on Slimproto STAT "STMu" (underrun / buffer empty).
    - This matches LMS behavior: only STMu triggers playerStopped(), not STMd.
    - When the user manually starts a new track (e.g. via Web-UI), a late STMu from the
      previous stream can arrive after the manual switch and incorrectly advance to the
      next track. To prevent this, we use stream generation checks and a short
      suppression window immediately after a manual track start.
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 3483,
        *,
        web_port: int = 9000,
        cli_port: int = 9090,
        music_root: Path | None = None,
        library_db_path: Path | None = None,
        cors_origins: str | list[str] = "*",
    ) -> None:
        """
        Initialize the Resonance server.

        Args:
            host: Host address to bind to.
            port: Slimproto port (default 3483).
            web_port: HTTP/JSON-RPC port (default 9000).
            cli_port: Telnet CLI port (default 9090, 0 disables CLI).
            music_root: Optional root directory for the local music library.
            library_db_path: Optional path to the library SQLite DB file.
            cors_origins: Allowed CORS origins for the web server (default ``"*"``).
        """
        self.host = host
        self.port = port
        self.web_port = web_port
        self.cli_port = cli_port
        self.cors_origins = cors_origins

        # Plugin manager + installer + repository client
        self.plugin_manager = PluginManager(
            core_plugins_dir=Path("plugins"),
            community_plugins_dir=Path("data/installed_plugins"),
        )
        self.plugin_installer = PluginInstaller(install_dir=Path("data/installed_plugins"))
        self.plugin_repository = PluginRepository()

        # Content provider registry (Radio, Podcasts, external sources).
        # Plugins register providers during setup; the streaming/handler layer
        # queries the registry when users browse or play remote content.
        self.content_registry = ContentProviderRegistry()

        # Core components
        self.player_registry = PlayerRegistry()

        # Streaming server (handles audio file requests from players)
        self.streaming_server = StreamingServer(
            host=host,
            port=web_port,
            audio_provider=self._resolve_audio_for_player,
        )

        self.slimproto = SlimprotoServer(
            host=host,
            port=port,
            streaming_port=web_port,
            player_registry=self.player_registry,
        )
        # Link back to ResonanceServer for track-finished suppression
        # Note: Use _resonance_server to avoid conflict with SlimprotoServer._server (asyncio server)
        self.slimproto._resonance_server = self

        # Expose StreamingServer on SlimprotoServer so the STAT handler can attach
        # the current stream generation to track-finished events (STMu) and ignore stale events.
        self.slimproto.streaming_server = self.streaming_server

        # Keep runtime DB data under cache/db.
        default_db_path = Path("cache/db/resonance-library.sqlite3")
        if library_db_path is not None:
            resolved_db_path = Path(library_db_path)
        else:
            resolved_db_path = default_db_path

        resolved_db_path.parent.mkdir(parents=True, exist_ok=True)
        self.library_db = LibraryDb(db_path=str(resolved_db_path))

        # Core library (kept independent of any web/UI layer)
        self.music_library = MusicLibrary(db=self.library_db, music_root=music_root)

        # Artwork manager (handles cover art extraction and caching)
        self.artwork_manager = ArtworkManager(cache_dir=Path("cache/artwork"))

        # Playlist manager (one playlist per player, persisted to disk)
        playlist_persistence_dir = Path("data/playlists")
        self.playlist_manager = PlaylistManager(persistence_dir=playlist_persistence_dir)

        # Display manager (bitmap rendering for SB2/SB3/Classic/Boom).
        # Gated behind RESONANCE_DISPLAY=1 env var until hardware-verified.
        self.display_manager: DisplayManager | None = None
        if DISPLAY_RENDERING_ENABLED:
            self.display_manager = DisplayManager(
                self.slimproto,
                playlist_manager=self.playlist_manager,
                streaming_server=self.streaming_server,
            )
            set_display_manager(self.display_manager)
            logger.info("Display rendering enabled (RESONANCE_DISPLAY=1)")

        # Web server (HTTP/JSON-RPC on port 9000)
        self.web_server: WebServer | None = None

        # Telnet CLI server (LMS-compatible, port 9090)
        self.cli_server: CliServer | None = None

        # Server state
        self._running = False
        self._shutdown_event: asyncio.Event | None = None

        # Alarm runtime (fires LMS-compatible alarms)
        self.alarm_runtime: AlarmRuntime | None = None

        # Per-player suppression window for STMu-based auto-advance (race protection).
        # Key: player MAC, Value: event-loop time() until which track-finished should be ignored.
        self._suppress_track_finished_until: dict[str, float] = {}

        # Crossfade/gapless prefetch state.
        # When STMd fires we prefetch the next track and record the NEW stream
        # generation here. On STMu we check: if prefetch already happened for
        # the current generation, we only advance the playlist index (no second strm).
        self._prefetched_generation: dict[str, int] = {}

        # Per-player STMd idempotency marker.
        # Key: player MAC, value: stream generation whose decode-ready was
        # already handled. This avoids duplicate prefetching while still
        # allowing chained prefetch across generations (g1 -> g2 -> g3).
        self._decode_ready_handled_generation: dict[str, int] = {}

        # SeekCoordinator for latest-wins seek semantics (initialized on start)
        self.seek_coordinator = None

        # Server UUID (persistent across restarts, like LMS)
        self.server_uuid = get_or_create_server_uuid()

        # UDP Discovery server for player discovery on local network
        # NOTE: version="7.999.999" is required for firmware compatibility!
        # SqueezePlay firmware 7.7.3 and earlier has a version comparison bug
        # that rejects servers reporting version 8.0.0 or higher.
        # LMS uses "7.999.999" (RADIO_COMPATIBLE_VERSION) to bypass this.
        self.discovery_server = UDPDiscoveryServer(
            host=host,
            port=port,  # Same port as Slimproto (3483)
            server_name="Resonance",
            http_port=web_port,
            server_uuid=self.server_uuid,
            version="7.999.999",
        )

    async def start(self) -> None:
        """Start all server components."""
        logger.info("Starting Resonance server on %s:%d", self.host, self.port)

        self._running = True
        self._shutdown_event = asyncio.Event()

        # Start core library DB (schema/migrations)
        await self.library_db.open()
        await self.library_db.ensure_schema()

        # Mark the facade initialized (DB-backed operations will be wired in next)
        await self.music_library.initialize()

        # Start Slimproto server
        await self.slimproto.start()

        # Start UDP Discovery server (allows players to find us via broadcast)
        try:
            await self.discovery_server.start()
        except Exception as e:
            # Discovery is optional - don't fail startup if it doesn't work
            logger.warning("UDP Discovery failed to start (players can still connect directly): %s", e)

        # Mark streaming server as ready (no longer binds its own port)
        # Streaming is now handled via FastAPI routes at /stream.mp3
        await self.streaming_server.start()

        self.seek_coordinator = init_seek_coordinator(self.streaming_server)

        # Start DisplayManager (bitmap rendering for SB2/SB3/Classic/Boom)
        # Must come after slimproto + streaming_server are ready.
        if self.display_manager is not None:
            await self.display_manager.start()

        # Start Web server (HTTP/JSON-RPC + Streaming)
        self.web_server = WebServer(
            player_registry=self.player_registry,
            music_library=self.music_library,
            playlist_manager=self.playlist_manager,
            streaming_server=self.streaming_server,
            artwork_manager=self.artwork_manager,
            slimproto=self.slimproto,
            server_uuid=self.server_uuid,
            plugin_manager=self.plugin_manager,
            plugin_installer=self.plugin_installer,
            plugin_repository=self.plugin_repository,
            cors_origins=self.cors_origins,
        )
        await self.web_server.start(host=self.host, port=self.web_port)

        # Load persisted playlists and start background auto-save
        self.playlist_manager.load_all()
        await self.playlist_manager.start_autosave()

        # Load persisted alarms
        alarm_persistence_path = Path("data/alarms.json")
        configure_alarm_persistence(alarm_persistence_path)
        load_alarms()

        # Load persisted player preferences
        prefs_persistence_dir = Path("data/player_prefs")
        configure_prefs_persistence(prefs_persistence_dir)
        load_all_player_prefs()

        # Configure saved-playlists directory (M3U persistence)
        from resonance.web.handlers.playlist import configure_saved_playlists_dir
        saved_playlists_dir = Path("data/saved_playlists")
        configure_saved_playlists_dir(saved_playlists_dir)

        # Wire JSON-RPC handler onto SlimprotoServer so IR/BUTN dispatch
        # can execute playback commands (pause, volume, skip, etc.)
        self.slimproto.jsonrpc_handler = self.web_server.jsonrpc_handler

        # Start AlarmRuntime (scheduler that triggers alarms at local time)
        self.alarm_runtime = AlarmRuntime(
            jsonrpc_execute=self.web_server.jsonrpc_handler.execute_command,
        )
        await self.alarm_runtime.start()

        # Start CLI server (LMS telnet CLI, port 9090).
        # Disabled when cli_port <= 0.
        if self.cli_port > 0:
            async def _execute_cli_command(player_id: str, command: list[str]) -> dict[str, object]:
                if self.web_server is None:
                    return {"error": "Web server not initialized"}
                return await self.web_server.jsonrpc_handler.execute_command(player_id, command)

            self.cli_server = CliServer(
                host=self.host,
                port=self.cli_port,
                command_executor=_execute_cli_command,
                event_bus=event_bus,
            )
            await self.cli_server.start()

        # Subscribe to decode-ready events for crossfade/gapless prefetch
        async def _on_decode_ready_event(event: Event) -> None:
            from resonance.core.events import PlayerDecodeReadyEvent
            if isinstance(event, PlayerDecodeReadyEvent):
                await self._on_decode_ready(event)

        await event_bus.subscribe("player.decode_ready", _on_decode_ready_event)

        # Subscribe to track-started events so prefetched handoffs update
        # playlist metadata immediately on STMs.
        async def _on_track_started_event(event: Event) -> None:
            if isinstance(event, PlayerTrackStartedEvent):
                await self._on_track_started(event)

        await event_bus.subscribe("player.track_started", _on_track_started_event)

        # Subscribe to track finished events for automatic playlist advancement
        async def _on_track_finished_event(event: Event) -> None:
            if isinstance(event, PlayerTrackFinishedEvent):
                await self._on_track_finished(event)

        await event_bus.subscribe("player.track_finished", _on_track_finished_event)

        # Subscribe to live stream dropped events for automatic re-stream
        # (LMS _RetryOrNext equivalent — StreamingController.pm L920-927)
        async def _on_live_stream_dropped_event(event: Event) -> None:
            if isinstance(event, LiveStreamDroppedEvent):
                await self._on_live_stream_dropped(event)

        await event_bus.subscribe("player.live_stream_dropped", _on_live_stream_dropped_event)

        # ── Plugin lifecycle: discover → load → start ────────────
        await self.plugin_manager.discover()
        await self.plugin_manager.load_all()
        await self.plugin_manager.start_all(
            event_bus=event_bus,
            music_library=self.music_library,
            player_registry=self.player_registry,
            playlist_manager=self.playlist_manager,
            command_register=register_command,
            command_unregister=unregister_command,
            route_register=lambda r: self.web_server.app.include_router(r) if self.web_server else None,
            content_registry=self.content_registry,
            server_info=self.server_info,
        )

        # Notify listeners that the server is fully operational
        await event_bus.publish(ServerStartedEvent())

        logger.info("Resonance server started successfully")
        if self.plugin_manager.started_plugins:
            logger.info("Plugins: %s", ", ".join(self.plugin_manager.started_plugins))
        if self.cli_server is not None:
            logger.info("Slimproto: port %d | Web/Streaming: port %d | CLI: port %d", self.port, self.web_port, self.cli_server.port)
        else:
            logger.info("Slimproto: port %d | Web/Streaming: port %d", self.port, self.web_port)
    async def stop(self) -> None:
        """Stop all server components gracefully."""
        if not self._running:
            return

        logger.info("Stopping Resonance server...")
        self._running = False

        # Notify listeners that shutdown is beginning
        await event_bus.publish(ServerStoppingEvent())

        # Stop plugins first (they may depend on other components)
        await self.plugin_manager.stop_all()

        # Stop CLI server (line-based control channel).
        if self.cli_server:
            await self.cli_server.stop()
            self.cli_server = None

        # Stop AlarmRuntime first (it uses the JSON-RPC command path)
        if self.alarm_runtime is not None:
            await self.alarm_runtime.stop()
            self.alarm_runtime = None

        # Flush dirty playlists and stop background auto-save
        await self.playlist_manager.stop_autosave()

        # Stop DisplayManager (before streaming/slimproto — reverse of start order)
        if self.display_manager is not None:
            await self.display_manager.stop()

        # Stop Web server (clients get 503)
        if self.web_server:
            await self.web_server.stop()

        # Stop UDP Discovery server
        await self.discovery_server.stop()

        # Stop Streaming server (clears queue)
        await self.streaming_server.stop()

        # Stop Slimproto server
        await self.slimproto.stop()

        # Disconnect all players
        await self.player_registry.disconnect_all()

        # Close library DB last, after all components are stopped.
        await self.library_db.close()

        if self._shutdown_event:
            self._shutdown_event.set()

        logger.info("Resonance server stopped")

    async def run(self) -> None:
        """
        Run the server until shutdown is requested.

        This method starts all components and waits for a shutdown signal
        (SIGINT or SIGTERM).
        """
        await self.start()

        # Set up signal handlers for graceful shutdown
        loop = asyncio.get_running_loop()

        def handle_signal() -> None:
            logger.info("Received shutdown signal")
            if self._shutdown_event:
                self._shutdown_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, handle_signal)
            except NotImplementedError:
                # Signal handlers not supported on Windows
                pass

        # Wait for shutdown
        if self._shutdown_event:
            await self._shutdown_event.wait()

        await self.stop()

    @property
    def is_running(self) -> bool:
        """Check if the server is currently running."""
        return self._running

    @property
    def server_info(self) -> dict[str, Any]:
        """Networking info about this server instance (host + port).

        Passed to ``PluginContext.server_info`` so plugins that spawn
        external processes (e.g. squeeze2raop) know where to connect.
        """
        return {
            "host": self.host,
            "port": self.port,
        }

    @property
    def connected_players(self) -> int:
        """Get the number of currently connected players."""
        return len(self.player_registry)

    def _resolve_audio_for_player(self, player_mac: str) -> Path | None:
        """
        Callback for StreamingServer to resolve which audio file to serve.

        This looks up the player's playlist and returns the path of the
        current track.

        Args:
            player_mac: MAC address of the requesting player.

        Returns:
            Path to the audio file, or None if no track is queued.
        """
        if player_mac not in self.playlist_manager:
            return None

        playlist = self.playlist_manager.get(player_mac)
        current = playlist.current_track
        if current is None:
            return None

        return Path(current.path)

    async def _on_decode_ready(self, event: PlayerDecodeReadyEvent) -> None:
        """Handle decode-ready (STMd) by prefetching the next track.

        This enables crossfade and gapless playback: the player's decoder
        has consumed all input for the current track, so we prepare the next
        track's stream NOW — before the output buffer runs dry (STMu).

        The player firmware will handle the actual crossfade/gapless mixing
        based on the transition parameters we send in the strm frame.
        """
        player_id = event.player_id

        # ── Diagnostic: trace every STMd event ──
        _diag_cur_gen = self.streaming_server.get_stream_generation(player_id)
        _diag_prefetch_gen = self._prefetched_generation.get(player_id)
        _diag_playlist = self.playlist_manager.get(player_id) if player_id in self.playlist_manager else None
        _diag_cur_track = _diag_playlist.current_track if _diag_playlist else None
        _diag_next_track = _diag_playlist.peek_next() if _diag_playlist else None
        logger.info(
            "[DIAG-DECODE] STMd player=%s event_gen=%s current_gen=%s prefetch_gen=%s "
            "cur_track=%s (idx=%s/%s) next_track=%s",
            player_id,
            event.stream_generation,
            _diag_cur_gen,
            _diag_prefetch_gen,
            getattr(_diag_cur_track, "title", None),
            getattr(_diag_playlist, "current_index", "?"),
            len(_diag_playlist) if _diag_playlist else "?",
            getattr(_diag_next_track, "title", None),
        )

        # Generation guard — ignore stale STMd from a previous stream.
        if event.stream_generation is not None:
            current_gen = self.streaming_server.get_stream_generation(player_id)
            if current_gen is not None and current_gen != event.stream_generation:
                logger.debug(
                    "Ignoring stale STMd for player %s (event gen=%s, current gen=%s)",
                    player_id, event.stream_generation, current_gen,
                )
                return

        # Resolve generation for this decode-ready signal.
        current_gen = self.streaming_server.get_stream_generation(player_id)
        decode_ready_gen = event.stream_generation if event.stream_generation is not None else current_gen

        # Idempotency guard: handle STMd only once per generation.
        # This allows chained prefetch across generations while filtering
        # duplicate STMd bursts for the same track.
        decode_ready_handled = getattr(self, "_decode_ready_handled_generation", None)
        if decode_ready_handled is None:
            decode_ready_handled = {}
            self._decode_ready_handled_generation = decode_ready_handled
        if decode_ready_gen is not None and decode_ready_handled.get(player_id) == decode_ready_gen:
            logger.debug(
                "Already handled STMd for player %s gen=%s, ignoring duplicate",
                player_id,
                decode_ready_gen,
            )
            return

        player = await self.player_registry.get_by_mac(player_id)
        if not player:
            return

        if player_id not in self.playlist_manager:
            return

        playlist = self.playlist_manager.get(player_id)

        # If this generation was prefetched earlier, we have now reached that
        # prefetched track. Some players do not emit STMu between gapless
        # transitions, so advance playlist index here before prefetching again.
        prefetched_gen = self._prefetched_generation.get(player_id)
        if decode_ready_gen is not None and prefetched_gen == decode_ready_gen:
            advanced_track = playlist.next()
            if advanced_track is not None:
                logger.info(
                    "STMd chain for player %s gen=%s: advancing playlist to %s (index %d/%d)",
                    player_id,
                    decode_ready_gen,
                    advanced_track.title,
                    playlist.current_index,
                    len(playlist),
                )
                from resonance.core.events import PlayerPlaylistEvent

                await event_bus.publish(
                    PlayerPlaylistEvent(
                        player_id=player_id,
                        action="index",
                        index=playlist.current_index,
                        count=len(playlist),
                    )
                )
            self._prefetched_generation.pop(player_id, None)

        next_track = playlist.peek_next()

        if next_track is None:
            logger.info(
                "No next track to prefetch for player %s (end of playlist)",
                player_id,
            )
            return

        # Resolve track duration for short-track clamping.
        track_duration_s: float | None = None
        duration_ms = getattr(next_track, "duration_ms", None) or getattr(next_track, "duration", None)
        if duration_ms is not None:
            try:
                track_duration_s = float(duration_ms) / 1000.0
            except (TypeError, ValueError):
                pass

        runtime_params = await self.streaming_server.resolve_runtime_stream_params(
            player_id,
            track=next_track,
            playlist=playlist,
            allow_transition=True,
            is_currently_playing=True,
            track_duration_s=track_duration_s,
        )

        # CRITICAL: Set volume before stream start (audg must precede strm).
        current_volume = getattr(player.status, "volume", 100)
        current_muted = getattr(player.status, "muted", False)
        await player.set_volume(current_volume, current_muted)

        current_track = playlist.current_track
        use_server_side_crossfade = False
        stream_format_hint_override: str | None = None
        send_transition_type = runtime_params.transition_type
        send_transition_duration = runtime_params.transition_duration
        send_replay_gain = runtime_params.replay_gain

        # Remote tracks: skip server-side crossfade (requires local files for SoX),
        # use queue_url() instead of queue_file().
        _next_is_remote = getattr(next_track, "is_remote", False)

        # Optional server-side overlap engine for real mixed crossfades.
        # Only available for local files — remote URLs cannot be mixed by SoX.
        if (
            not _next_is_remote
            and current_track is not None
            and not getattr(current_track, "is_remote", False)
            and runtime_params.transition_type in (1, 5)
            and runtime_params.transition_duration > 0
        ):
            from resonance.streaming.crossfade import prepare_crossfade_plan
            from resonance.streaming.policy import strm_expected_format_hint

            try:
                source_format_hint = Path(next_track.path).suffix.lstrip(".").lower() or "mp3"
                output_format_hint = strm_expected_format_hint(source_format_hint, player.info.device_type)
                prepared_plan = await asyncio.to_thread(
                    prepare_crossfade_plan,
                    previous_path=Path(current_track.path),
                    next_path=Path(next_track.path),
                    requested_overlap_seconds=float(runtime_params.transition_duration),
                    output_format_hint=output_format_hint,
                )
            except Exception:
                prepared_plan = None
                logger.debug("Failed to prepare server-side crossfade plan", exc_info=True)

            if prepared_plan is not None:
                self.streaming_server.queue_file_with_crossfade_plan(
                    player_id,
                    Path(next_track.path),
                    prepared_plan,
                )
                use_server_side_crossfade = True
                stream_format_hint_override = prepared_plan.output_format_hint

                # Disable player-side transition when server already renders overlap.
                send_transition_type = 0
                send_transition_duration = 0

                # ReplayGain is track-specific and would also affect the preserved tail.
                # Keep mixed overlap neutral to avoid a gain jump on the old track tail.
                send_replay_gain = 0
            else:
                self.streaming_server.queue_file(player_id, Path(next_track.path))
        elif _next_is_remote:
            self.streaming_server.queue_url(
                player_id,
                getattr(next_track, "effective_stream_url", next_track.path),
                content_type=getattr(next_track, "content_type", None) or "audio/mpeg",
                is_live=getattr(next_track, "is_live", False),
                title=next_track.title or next_track.path,
            )
        else:
            self.streaming_server.queue_file(player_id, Path(next_track.path))

        # Record that we prefetched - the NEW generation (after queue_file).
        new_gen = self.streaming_server.get_stream_generation(player_id)
        if new_gen is not None:
            self._prefetched_generation[player_id] = new_gen

        # Store track duration for server-side track-end detection.
        # Controller-class players with transitionType=0 never send STMd/STMu,
        # so the server must detect track end from stream age vs duration.
        _dur_ms = getattr(next_track, "duration_ms", None) or 0
        if _dur_ms > 0:
            self.streaming_server.set_track_duration(player_id, float(_dur_ms) / 1000.0)

        # Start streaming with transition parameters.
        server_ip = self.slimproto.get_advertise_ip_for_player(player)
        await player.start_track(
            next_track,
            server_port=self.web_port,
            server_ip=server_ip,
            transition_duration=send_transition_duration,
            transition_type=send_transition_type,
            stream_flags=runtime_params.flags,
            replay_gain=send_replay_gain,
            format_hint_override=stream_format_hint_override,
        )
        if use_server_side_crossfade:
            # Mixed stream already includes the next track content. Advance playlist index now,
            # otherwise status metadata stays on the old track until a very late STMu.
            advanced_track = playlist.next()
            if advanced_track is not None:
                # Ignore potential stale STMu from the old stream during transition handover.
                self.suppress_track_finished_for_player(player_id, seconds=2.0)

                from resonance.core.events import PlayerPlaylistEvent

                await event_bus.publish(
                    PlayerPlaylistEvent(
                        player_id=player_id,
                        action="index",
                        index=playlist.current_index,
                        count=len(playlist),
                    )
                )


        if decode_ready_gen is not None:
            decode_ready_handled[player_id] = decode_ready_gen

        logger.info(
            "[DIAG-DECODE] Prefetched next track for player %s: %s (transition=%d/%ds, mixed=%s, gen=%s, "
            "flags=0x%02x, replay_gain=%d, format_override=%s)",
            player_id,
            next_track.title,
            send_transition_type,
            send_transition_duration,
            use_server_side_crossfade,
            new_gen,
            runtime_params.flags,
            send_replay_gain,
            stream_format_hint_override,
        )

    async def _on_track_started(self, event: PlayerTrackStartedEvent) -> None:
        """Advance playlist metadata when a prefetched stream actually starts (STMs)."""
        player_id = event.player_id
        started_gen = event.stream_generation

        # ── Diagnostic: trace every STMs event ──
        _diag_prefetch_gen = self._prefetched_generation.get(player_id)
        _diag_cur_gen = self.streaming_server.get_stream_generation(player_id)
        _diag_playlist = self.playlist_manager.get(player_id) if player_id in self.playlist_manager else None
        _diag_cur_track = _diag_playlist.current_track if _diag_playlist else None
        logger.info(
            "[DIAG-STARTED] STMs player=%s started_gen=%s current_gen=%s prefetch_gen=%s "
            "cur_track=%s (idx=%s/%s)",
            player_id, started_gen, _diag_cur_gen, _diag_prefetch_gen,
            getattr(_diag_cur_track, "title", None),
            getattr(_diag_playlist, "current_index", "?"),
            len(_diag_playlist) if _diag_playlist else "?",
        )

        if started_gen is None:
            return

        prefetched_gen = self._prefetched_generation.get(player_id)
        if prefetched_gen is None or prefetched_gen != started_gen:
            return

        if player_id not in self.playlist_manager:
            self._prefetched_generation.pop(player_id, None)
            return

        playlist = self.playlist_manager.get(player_id)
        started_track = playlist.next()
        self._prefetched_generation.pop(player_id, None)
        if started_track is None:
            return

        logger.info(
            "Track started (prefetched STMs path) for player %s gen=%s: advancing playlist to %s (index %d/%d)",
            player_id,
            started_gen,
            started_track.title,
            playlist.current_index,
            len(playlist),
        )

        # Ignore a late STMu from the old stream right after handoff.
        self._suppress_track_finished_until[player_id] = asyncio.get_running_loop().time() + 2.0

        from resonance.core.events import PlayerPlaylistEvent

        await event_bus.publish(
            PlayerPlaylistEvent(
                player_id=player_id,
                action="index",
                index=playlist.current_index,
                count=len(playlist),
            )
        )
    async def _on_track_finished(self, event: PlayerTrackFinishedEvent) -> None:
        """Handle track finished event by playing the next track in the playlist."""
        player_id = event.player_id

        # ── Diagnostic: trace every STMu event ──
        _diag_cur_gen = self.streaming_server.get_stream_generation(player_id)
        _diag_prefetch_gen = self._prefetched_generation.get(player_id)
        _diag_suppress = self._suppress_track_finished_until.get(player_id)
        _diag_now = asyncio.get_running_loop().time()
        _diag_playlist = self.playlist_manager.get(player_id) if player_id in self.playlist_manager else None
        _diag_cur_track = _diag_playlist.current_track if _diag_playlist else None
        logger.info(
            "[DIAG-FINISHED] STMu player=%s event_gen=%s current_gen=%s prefetch_gen=%s "
            "suppress_remaining=%.3fs cur_track=%s (idx=%s/%s)",
            player_id, event.stream_generation, _diag_cur_gen, _diag_prefetch_gen,
            max(0, (_diag_suppress or 0) - _diag_now),
            getattr(_diag_cur_track, "title", None),
            getattr(_diag_playlist, "current_index", "?"),
            len(_diag_playlist) if _diag_playlist else "?",
        )

        # Suppress late STMu from a previous stream right after a manual track start.
        # This prevents: user clicks track A -> server starts A -> late STMu arrives -> server jumps to next.
        now = asyncio.get_running_loop().time()
        suppress_until = self._suppress_track_finished_until.get(player_id)
        if suppress_until is not None and now < suppress_until:
            logger.info(
                "Ignoring track-finished for player %s (suppressed %.3fs remaining)",
                player_id,
                suppress_until - now,
            )
            return

        # Ignore stale track-finished events by stream generation.
        #
        # We attach a per-player stream generation to STMu events (see slimproto STAT handler).
        # When the user manually switches tracks, the streaming server increments generation.
        # A late STMu from the previous stream must NOT advance the playlist to track +1.
        if event.stream_generation is not None:
            current_gen = self.streaming_server.get_stream_generation(player_id)
            if current_gen is not None and current_gen != event.stream_generation:
                logger.info(
                    "Ignoring stale track-finished for player %s (event gen=%s, current gen=%s)",
                    player_id,
                    event.stream_generation,
                    current_gen,
                )
                return

        player = await self.player_registry.get_by_mac(player_id)
        if not player:
            return

        playlist = self.playlist_manager.get(player_id)

        # Check if prefetch already prepared the next track (STMd → _on_decode_ready).
        # If so, we only need to advance the playlist index — the stream is already running.
        prefetch_gen = self._prefetched_generation.pop(player_id, None)
        current_gen = self.streaming_server.get_stream_generation(player_id)

        if prefetch_gen is not None and current_gen is not None and prefetch_gen == current_gen:
            # Prefetch path: stream already started, just advance index.
            next_track = playlist.next()
            if next_track:
                logger.info(
                    "Track finished (prefetched path) for player %s: advancing to %s (index %d/%d)",
                    player_id, next_track.title,
                    playlist.current_index, len(playlist),
                )
                self.suppress_track_finished_for_player(player_id, seconds=4.0)

                from resonance.core.events import PlayerPlaylistEvent
                await event_bus.publish(
                    PlayerPlaylistEvent(
                        player_id=player_id,
                        action="index",
                        index=playlist.current_index,
                        count=len(playlist),
                    )
                )
            else:
                logger.info("Playlist finished for player %s (all %d tracks played)", player_id, len(playlist))
            return

        # Fallback path: no prefetch happened — full flow (backward compatible).
        next_track = playlist.next()

        if next_track:
            logger.info(
                "Advancing to next track for player %s: %s (index %d/%d)",
                player_id, next_track.title,
                playlist.current_index, len(playlist),
            )

            # Suppress track-finished briefly so the strm s → STMu transition
            # of the OLD stream (if any late packet arrives) doesn't double-advance.
            self.suppress_track_finished_for_player(player_id, seconds=4.0)

            runtime_params = await self.streaming_server.resolve_runtime_stream_params(
                player_id,
                track=next_track,
                playlist=playlist,
                allow_transition=True,
                is_currently_playing=True,
            )

            # CRITICAL: Set volume before stream start (audg must precede strm).
            # Without this, some Squeezebox models play the next track silently.
            current_volume = getattr(player.status, "volume", 100)
            current_muted = getattr(player.status, "muted", False)
            await player.set_volume(current_volume, current_muted)

            # Queue track in streaming server (cancels old stream + increments generation)
            if getattr(next_track, "is_remote", False):
                self.streaming_server.queue_url(
                    player_id,
                    getattr(next_track, "effective_stream_url", next_track.path),
                    content_type=getattr(next_track, "content_type", None) or "audio/mpeg",
                    is_live=getattr(next_track, "is_live", False),
                    title=next_track.title or next_track.path,
                )
            else:
                self.streaming_server.queue_file(player_id, Path(next_track.path))

            # Store track duration for server-side track-end detection.
            # Controller-class players with transitionType=0 never send STMd/STMu,
            # so the server must detect track end from stream age vs duration.
            _dur_ms = getattr(next_track, "duration_ms", None) or 0
            if _dur_ms > 0:
                self.streaming_server.set_track_duration(player_id, float(_dur_ms) / 1000.0)

            # Start streaming
            server_ip = self.slimproto.get_advertise_ip_for_player(player)
            await player.start_track(
                next_track,
                server_port=self.web_port,
                server_ip=server_ip,
                transition_duration=runtime_params.transition_duration,
                transition_type=runtime_params.transition_type,
                stream_flags=runtime_params.flags,
                replay_gain=runtime_params.replay_gain,
            )
            # Publish playlist event so Cometd pushes updated status to Radio/Touch/Boom
            from resonance.core.events import PlayerPlaylistEvent
            await event_bus.publish(
                PlayerPlaylistEvent(
                    player_id=player_id,
                    action="index",
                    index=playlist.current_index,
                    count=len(playlist),
                )
            )
        else:
            logger.info("Playlist finished for player %s (all %d tracks played)", player_id, len(playlist))

    async def _on_live_stream_dropped(self, event: LiveStreamDroppedEvent) -> None:
        """Handle a live radio stream that dropped unexpectedly.

        Mirrors LMS ``_RetryOrNext`` (StreamingController.pm L910-930):
        if the stream was live, remote, played for >10 s, and we haven't
        exhausted retries, re-queue the same URL and send a new ``strm``
        command so the player reconnects seamlessly.
        """
        player_id = event.player_id

        # ── Guard 1: generation must still match (no user action replaced it) ──
        current_gen = self.streaming_server.get_stream_generation(player_id)
        if event.stream_generation is not None and current_gen != event.stream_generation:
            logger.info(
                "[RESTREAM] Ignoring stale live-stream-dropped for player %s "
                "(event_gen=%s, current_gen=%s)",
                player_id, event.stream_generation, current_gen,
            )
            return

        # ── Guard 2: at least 10 s of playback (LMS: $elapsed > 10) ──
        stream_age = self.streaming_server.get_stream_generation_age(player_id)
        min_elapsed = self.streaming_server.MIN_ELAPSED_FOR_RESTREAM
        if stream_age is not None and stream_age < min_elapsed:
            logger.info(
                "[RESTREAM] Stream too short for re-stream player=%s age=%.1fs (min %.0fs)",
                player_id, stream_age, min_elapsed,
            )
            return

        # ── Guard 3: retry budget ──
        if not self.streaming_server.record_restream_attempt(player_id):
            # Budget exhausted — let normal STMu → advance/stop proceed.
            logger.info(
                "[RESTREAM] Retry budget exhausted for player %s — not re-streaming",
                player_id,
            )
            return

        # ── Guard 4: player still exists ──
        player = await self.player_registry.get_by_mac(player_id)
        if not player:
            logger.info("[RESTREAM] Player %s no longer connected — skipping", player_id)
            return

        # ── Suppress any pending/upcoming STMu from the old stream ──
        # The old stream's buffer will drain and fire STMu, which must NOT
        # advance the playlist — we are re-queuing the SAME track.
        self.suppress_track_finished_for_player(player_id, seconds=10.0)

        retry_count = self.streaming_server.get_restream_retry_count(player_id)
        logger.info(
            "[RESTREAM] Re-streaming live radio for player %s "
            "(attempt %d/%d, age=%.1fs, url=%s)",
            player_id,
            retry_count,
            self.streaming_server.MAX_RESTREAM_RETRIES,
            stream_age or 0.0,
            event.title or event.remote_url,
        )

        # ── Re-queue the same URL (is_restream=True preserves retry counter) ──
        self.streaming_server.queue_url(
            player_id,
            event.remote_url,
            content_type=event.content_type,
            is_live=True,
            title=event.title,
            is_restream=True,
        )

        # ── Send strm command so the player connects to the new stream ──
        # Resolve volume first (audg must precede strm on some models).
        current_volume = getattr(player.status, "volume", 100)
        current_muted = getattr(player.status, "muted", False)
        await player.set_volume(current_volume, current_muted)

        # Determine format hint from content type (same as initial play).
        _ct_to_fmt = {
            "audio/mpeg": "mp3",
            "audio/mp3": "mp3",
            "audio/aac": "aac",
            "audio/aacp": "aac",
            "audio/ogg": "ogg",
            "application/ogg": "ogg",
            "audio/flac": "flc",
        }
        format_hint = _ct_to_fmt.get(event.content_type, "mp3")

        # Use the playlist's current track to build the strm command.
        playlist = self.playlist_manager.get(player_id)
        current_track = playlist.current_track if playlist else None

        if current_track is not None:
            server_ip = self.slimproto.get_advertise_ip_for_player(player)
            await player.start_track(
                current_track,
                server_port=self.web_port,
                server_ip=server_ip,
                format_hint_override=format_hint,
            )
        else:
            # Fallback: use start_stream directly with the URL path.
            server_ip = self.slimproto.get_advertise_ip_for_player(player)
            await player.start_stream(
                event.remote_url,
                server_port=self.web_port,
                server_ip=server_ip,
                format_hint=format_hint,
            )

    def suppress_track_finished_for_player(self, player_mac: str, seconds: float = 1.0) -> None:
        """
        Temporarily suppress STMu-based auto-advance for a player.

        Call this right before/after starting a new track explicitly (manual user action),
        so a late STMu from the previous stream can't advance the playlist.

        Args:
            player_mac: Player MAC address.
            seconds: Suppression window duration.
        """
        until = asyncio.get_running_loop().time() + seconds
        self._suppress_track_finished_until[player_mac] = until
        # Invalidate any pending prefetch — manual actions (skip, seek, play)
        # override the prefetch flow so STMu won't try the fast path.
        self._prefetched_generation.pop(player_mac, None)

        # Clear DSCO / playback-confirmed guards on the slimproto
        # layer so stale state from the old stream cannot trigger
        # stale track-finished handling or premature prefetch on the NEW stream.
        decode_ready_handled = getattr(self, "_decode_ready_handled_generation", None)
        if isinstance(decode_ready_handled, dict):
            decode_ready_handled.pop(player_mac, None)

        if hasattr(self, "slimproto") and self.slimproto is not None:
            self.slimproto._dsco_received_generation.pop(player_mac, None)
            self.slimproto._stms_confirmed_generation.pop(player_mac, None)
