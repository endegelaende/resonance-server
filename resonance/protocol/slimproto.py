"""
Slimproto Protocol Server for Resonance.

This module implements the Slimproto binary protocol used by Squeezebox
players to communicate with the server. The protocol runs on TCP port 3483.

Protocol Format:
    Messages consist of a 4-byte ASCII command tag, followed by a 4-byte
    big-endian length field, followed by the payload data.

    [COMMAND: 4 bytes][LENGTH: 4 bytes][PAYLOAD: LENGTH bytes]

Reference: Slim/Networking/Slimproto.pm from the original LMS
"""

import asyncio
import ipaddress
import logging
import re
import socket
import struct
import time

# Throttle interval for elapsed-time pushes via STMt heartbeats.
# JiveLite interpolates locally (trackTime + rate * (now - trackSeen)),
# so frequent pushes aren't strictly needed — but periodic corrections
# keep the UI accurate after seeks and drift.  5 s is a good balance
# between freshness and CPU cost of subscription re-execution.
ELAPSED_PUSH_INTERVAL_SECONDS: float = 5.0
from collections.abc import Callable, Coroutine
from typing import Any

from resonance.core.events import (
    PlayerConnectedEvent,
    PlayerDisconnectedEvent,
    PlayerStatusEvent,
    PlayerTrackFinishedEvent,
    PlayerTrackStartedEvent,
    event_bus,
)
from resonance.player.client import DeviceType, PlayerClient, PlayerState
from resonance.player.registry import PlayerRegistry
from resonance.protocol.commands import (
    DEFAULT_GRFD_BITMAP_BYTES,
    DEFAULT_GRFD_FRAMEBUFFER_OFFSET,
    AudioFormat,
    AutostartMode,
    StreamParams,
    build_display_bitmap,
    build_display_brightness,
    build_display_clear,
    build_display_framebuffer,
    build_display_framebuffer_clear,
    build_stream_pause,
    build_stream_stop,
    build_stream_unpause,
    build_strm_frame,
    build_volume_frame,
)

logger = logging.getLogger(__name__)

# When enabled, outgoing frames include a compact hexdump for easier debugging.
OUTGOING_FRAME_DEBUG = True
OUTGOING_FRAME_HEXDUMP_BYTES = 64

# NOTE (SlimServer semantics):
# - STMd = DECODE_READY (decoder has no more input data) -> NOT "track finished"
# - STMu = UNDERRUN     (output buffer empty)            -> playerStopped / track finished
#
# Therefore, Resonance must NEVER auto-advance on STMd.
# Track-finished is handled on STMu only.


def _hexdump(data: bytes, limit: int = OUTGOING_FRAME_HEXDUMP_BYTES) -> str:
    """Return a compact hex representation of the first N bytes."""
    view = data[:limit]
    hexpart = " ".join(f"{b:02x}" for b in view)
    if len(data) > limit:
        return f"{hexpart} … (+{len(data) - limit} bytes)"
    return hexpart


def _force_outgoing_frame_debug_log(
    command: str,
    client_id: str,
    payload: bytes,
) -> None:
    """
    Emit outgoing-frame diagnostics even if the logger is not set to DEBUG.

    We print to stderr as a last resort because, during real-player debugging,
    we must capture TX frames to understand protocol expectations.
    """
    if not OUTGOING_FRAME_DEBUG:
        return

    try:
        prefix = "[TX]"
        print(
            f"{prefix} to={client_id} cmd={command} payload_len={len(payload)} payload_hex={_hexdump(payload)}",
            file=__import__("sys").stderr,
        )

        if command == "strm" and len(payload) >= 24:
            fixed = payload[:24]
            try:
                cmd_ch = fixed[0:1].decode("ascii", errors="replace")
                autostart_ch = fixed[1:2].decode("ascii", errors="replace")
                format_ch = fixed[2:3].decode("ascii", errors="replace")
            except Exception:
                cmd_ch = "?"
                autostart_ch = "?"
                format_ch = "?"
            server_port = struct.unpack(">H", fixed[18:20])[0]
            server_ip = struct.unpack(">I", fixed[20:24])[0]
            print(
                f"{prefix} strm parsed: command={cmd_ch} autostart={autostart_ch} format={format_ch} server_port={server_port} server_ip=0x{server_ip:08x}",
                file=__import__("sys").stderr,
            )

            if len(payload) > 24:
                req_preview = payload[24 : 24 + 200]
                print(
                    f"{prefix} strm request_preview={req_preview.decode('latin-1', errors='replace')!r}",
                    file=__import__("sys").stderr,
                )
    except Exception:
        # Never let diagnostics break protocol sending paths.
        return


# Default Slimproto port
SLIMPROTO_PORT = 3483

# Time after which a client is considered dead if no heartbeat received.
CLIENT_TIMEOUT_SECONDS = 60

# Interval for sending server heartbeats (strm t) to players
SERVER_HEARTBEAT_INTERVAL_SECONDS = 10

# How often to check for dead clients
CLIENT_CHECK_INTERVAL_SECONDS = 5

# Device ID to name mapping (from original Slimproto.pm)
DEVICE_IDS: dict[int, str] = {
    2: "squeezebox",
    3: "softsqueeze",
    4: "squeezebox2",
    5: "transporter",
    6: "softsqueeze3",
    7: "receiver",
    8: "squeezeslave",  # protocol name, cannot be renamed
    9: "controller",
    10: "boom",
    11: "softboom",
    12: "squeezeplay",
}

# Message handler type
MessageHandler = Callable[[PlayerClient, bytes], Coroutine[Any, Any, None]]


class SlimprotoError(Exception):
    """Base exception for Slimproto protocol errors."""

    pass


class ProtocolError(SlimprotoError):
    """Invalid protocol data received."""

    pass


class SlimprotoServer:
    """
    Slimproto protocol server for Squeezebox player communication.

    This server listens for incoming connections from Squeezebox hardware
    and software players (like Squeezelite) and handles the binary protocol
    for playback control, status updates, and other operations.

    The server is fully asynchronous using asyncio and can handle multiple
    concurrent player connections.

    Optional integration:
        streaming_server: Optional StreamingServer-like object, set by the main
            server, used to read the current per-player stream generation for
            generation-aware "track finished" (STMd) handling. This attribute is
            optional to keep the protocol layer decoupled from streaming.

    Attributes:
        host: The host address to bind to.
        port: The TCP port to listen on (default 3483).
        player_registry: Registry for tracking connected players.
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = SLIMPROTO_PORT,
        streaming_port: int = 9000,
        player_registry: PlayerRegistry | None = None,
    ) -> None:
        """
        Initialize the Slimproto server.

        Args:
            host: Host address to bind to.
            port: TCP port to listen on.
            streaming_port: HTTP port for audio streaming.
            player_registry: Registry for player management (created if not provided).
        """
        self.host = host
        self.port = port
        self.streaming_port = streaming_port
        self.player_registry = player_registry if player_registry is not None else PlayerRegistry()

        # Optional: wired by the main server. Kept as `Any` to avoid a hard dependency
        # on the streaming package from the protocol layer.
        self.streaming_server: Any | None = None

        self._server: asyncio.Server | None = None
        self._running = False
        self._client_tasks: dict[str, asyncio.Task[None]] = {}
        self._heartbeat_task: asyncio.Task[None] | None = None

        # Deferred track-finished tasks per player.
        #
        # Kept for backward compatibility with existing code paths, but with
        # SlimServer semantics we no longer defer STMd-based track-finished.
        self._deferred_finish_tasks: dict[str, asyncio.Task[None]] = {}

        # DSCO (end-of-stream) tracking per player (diagnostic/compat state).
        self._dsco_received_generation: dict[str, int] = {}

        # Playback-confirmed tracking per player.
        #
        # When the player sends STMs (track started) we record the current
        # stream generation here.  This allows _on_decode_ready and
        # _on_track_finished to distinguish "decoder consumed input and
        # playback is running" from "decoder consumed input but nothing was
        # ever audibly played" (e.g. broken transcode after a seek).
        #
        # Without this guard the server enters an endless STOP+START cycle:
        #   STMd/STMu fires  →  prefetch/advance  →  broken stream  →  repeat
        self._stms_confirmed_generation: dict[str, int] = {}
        # Deferred STMd tracking per player.
        #
        # On some devices STMd (decode ready) and STMs (track started) can
        # arrive almost simultaneously at a track boundary. If STMd is
        # processed first, strict confirmed-playback guards would drop it,
        # which can skip prefetch for the next track. We stash the generation
        # and replay decode-ready as soon as STMs confirms playback.
        self._pending_stmd_generation: dict[str, int] = {}

        # Throttle dict for elapsed-time pushes on STMt heartbeats.
        # Keyed by player MAC, value is the monotonic timestamp of the last
        # PlayerStatusEvent published for elapsed-time purposes.
        self._last_elapsed_push: dict[str, float] = {}

        # Message handlers indexed by 4-byte command
        self._handlers: dict[str, MessageHandler] = {
            "STAT": self._handle_stat,
            "BYE!": self._handle_bye,
            "IR  ": self._handle_ir,
            "RESP": self._handle_resp,
            "META": self._handle_meta,
            "DSCO": self._handle_dsco,
            "BUTN": self._handle_butn,
            "KNOB": self._handle_knob,
            "SETD": self._handle_setd,
            "ANIC": self._handle_anic,
        }

    async def start(self) -> None:
        """Start the Slimproto server and begin accepting connections."""
        if self._running:
            logger.warning("Slimproto server already running")
            return

        self._server = await asyncio.start_server(
            self._handle_connection,
            host=self.host,
            port=self.port,
            reuse_address=True,
        )

        self._running = True

        # Start heartbeat checker
        self._heartbeat_task = asyncio.create_task(self._check_heartbeats())

        logger.info("Slimproto server listening on %s:%d", self.host, self.port)

    def get_advertise_ip_for_player(self, player: PlayerClient) -> int:
        """
        Compute the IPv4 address we should advertise in 'strm' frames.

        Why:
        - Advertising 0.0.0.0 makes players try to connect to "server 0" and fail.
        - Binding to 0.0.0.0 is fine, but the advertised address must be reachable
          from the player.

        Rules:
        1) If bound host is a concrete IPv4 address (not 0.0.0.0), advertise that.
        2) If the player is connected via loopback, advertise 127.0.0.1.
        3) Otherwise, derive the local interface address used to reach the player's
           remote address (peer-facing local IP) and advertise that.

        Returns:
            IPv4 address as big-endian u32 suitable for the slimproto 'strm' header.
        """
        # 1) If host is a concrete IPv4 address, prefer it.
        try:
            host_ip = ipaddress.ip_address(self.host)
            if isinstance(host_ip, ipaddress.IPv4Address) and str(host_ip) != "0.0.0.0":
                return int(host_ip)
        except ValueError:
            # host might be a hostname; ignore and fall back
            pass

        # Determine peer IP.
        peer_ip = player.ip_address

        # 2) Loopback player => advertise loopback.
        if peer_ip in ("127.0.0.1", "::1"):
            return int(ipaddress.IPv4Address("127.0.0.1"))

        # 3) Best-effort: derive local interface IP used for this peer.
        if peer_ip and peer_ip != "unknown":
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    # Port doesn't matter for UDP connect; no packets are sent.
                    s.connect((peer_ip, 9))
                    local_ip = s.getsockname()[0]
                finally:
                    s.close()

                return int(ipaddress.IPv4Address(local_ip))
            except Exception:
                pass

        # Final fallback: loopback (better than 0.0.0.0).
        return int(ipaddress.IPv4Address("127.0.0.1"))

    async def stop(self) -> None:
        """Stop the server and close all connections."""
        if not self._running:
            return

        logger.info("Stopping Slimproto server...")
        self._running = False

        # Stop heartbeat task
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

        # Cancel all client handler tasks
        for task in self._client_tasks.values():
            task.cancel()

        if self._client_tasks:
            await asyncio.gather(*self._client_tasks.values(), return_exceptions=True)
            self._client_tasks.clear()

        # Cancel all deferred track-finished tasks
        for task in self._deferred_finish_tasks.values():
            task.cancel()

        if self._deferred_finish_tasks:
            await asyncio.gather(*self._deferred_finish_tasks.values(), return_exceptions=True)
            self._deferred_finish_tasks.clear()

        # Close server
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        logger.info("Slimproto server stopped")

    def cancel_deferred_track_finished(self, player_mac: str) -> bool:
        """
        Cancel any pending deferred STMd-based track-finished task for a player.

        Why:
        - We may defer STMd-based "track finished" to avoid early auto-advance
          while the output buffer still plays.
        - Manual user actions (seek/manual track start/skip) restart streams.
          A late deferred callback must NOT be allowed to fire after such an
          action, otherwise it can incorrectly auto-advance to the next track.

        Args:
            player_mac: Player MAC address.

        Returns:
            True if there was an active deferred task and we cancelled it, else False.
        """
        task = self._deferred_finish_tasks.pop(player_mac, None)
        if task is None:
            return False

        if not task.done():
            task.cancel()
        return True

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """
        Handle a new incoming connection.

        This is called by asyncio for each new client connection.
        We wait for a HELO message to identify the player, then
        process messages in a loop until disconnection.
        """
        peername = writer.get_extra_info("peername")
        remote_addr = f"{peername[0]}:{peername[1]}" if peername else "unknown"

        logger.info("New connection from %s", remote_addr)

        # Enable TCP keepalive to prevent WinError 121 (semaphore timeout)
        # This is critical for Windows which aggressively closes idle connections
        sock = writer.get_extra_info("socket")
        if sock is not None:
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                # Windows-specific: set keepalive parameters
                # (onoff=1, keepalive_time=10000ms, keepalive_interval=5000ms)
                if hasattr(socket, "SIO_KEEPALIVE_VALS"):
                    sock.ioctl(
                        socket.SIO_KEEPALIVE_VALS,
                        (1, 10000, 5000),
                    )
                logger.debug("TCP keepalive enabled for %s", remote_addr)
            except Exception as e:
                logger.debug("Could not set TCP keepalive for %s: %s", remote_addr, e)

        # Create a temporary client object for the connection
        client = PlayerClient(reader, writer)

        try:
            # First message must be HELO
            await self._wait_for_helo(client, reader)

            if client.id:
                # Register the client and start message loop
                await self.player_registry.register(client)
                if task := asyncio.current_task():
                    self._client_tasks[client.id] = task

                # Publish connect event for Cometd subscribers
                await event_bus.publish(
                    PlayerConnectedEvent(
                        player_id=client.mac_address,
                        name=client.name,
                        model=client.device_type,
                    )
                )

                # Main message processing loop
                await self._message_loop(client, reader)
        except asyncio.CancelledError:
            logger.debug("Connection handler cancelled for %s", remote_addr)
        except ConnectionResetError:
            logger.info("Connection reset by %s", remote_addr)
        except ProtocolError as e:
            logger.warning("Protocol error from %s: %s", remote_addr, e)
        except Exception as e:
            logger.exception("Error handling connection from %s: %s", remote_addr, e)
        finally:
            # Clean up
            if client.id:
                self._client_tasks.pop(client.id, None)
                self._last_elapsed_push.pop(client.mac_address, None)
                self._pending_stmd_generation.pop(client.mac_address, None)
                await self.player_registry.unregister(client.id)

                # Publish disconnect event for Cometd subscribers
                await event_bus.publish(PlayerDisconnectedEvent(player_id=client.mac_address))

            await client.disconnect()
            logger.info("Connection closed: %s", remote_addr)

    async def _wait_for_helo(
        self,
        client: PlayerClient,
        reader: asyncio.StreamReader,
    ) -> None:
        """
        Wait for and process the initial HELO message.

        The first message from a player must be HELO, which contains
        device identification and capabilities.

        Raises:
            ProtocolError: If HELO is not received or is malformed.
            asyncio.TimeoutError: If no HELO received within timeout.
        """
        try:
            # Wait for HELO with timeout
            command, payload = await asyncio.wait_for(
                self._read_message(reader),
                timeout=5.0,
            )
        except TimeoutError:
            raise ProtocolError("Timeout waiting for HELO") from None

        if command != "HELO":
            raise ProtocolError(f"Expected HELO, got {command}")

        self._parse_helo(client, payload)

        logger.info(
            "Player connected: %s (%s, rev %s)",
            client.id,
            client.info.device_type.name,
            client.info.firmware_version,
        )

        # Send initial server greeting/acknowledgment
        await self._send_server_capabilities(client)

    def _parse_helo(self, client: PlayerClient, data: bytes) -> None:
        """
        Parse HELO message payload and populate client info.

        HELO format (minimum 20 bytes, up to 36+ with UUID):
            [1] Device ID
            [1] Firmware revision
            [6] MAC address
            [16] UUID (optional, newer players)
            [2] WLAN channel list / flags
            [4] Bytes received high
            [4] Bytes received low
            [2] Language code
            [*] Capabilities string (optional)
        """
        if len(data) < 10:
            raise ProtocolError(f"HELO too short: {len(data)} bytes")

        # Parse fixed fields
        device_id = data[0]
        revision = data[1]

        # MAC address (bytes 2-7)
        mac_bytes = data[2:8]
        mac_address = ":".join(f"{b:02x}" for b in mac_bytes)

        # Check for UUID (36+ bytes means UUID is present)
        uuid = ""
        capabilities_offset = 20

        if len(data) >= 36:
            # UUID is 16 bytes as hex string (32 chars when parsed)
            uuid_bytes = data[8:24]
            uuid = uuid_bytes.hex()
            capabilities_offset = 36

        # Parse capabilities string if present
        capabilities: dict[str, str] = {}
        if len(data) > capabilities_offset:
            try:
                cap_string = data[capabilities_offset:].decode("utf-8", errors="ignore")
                capabilities = self._parse_capabilities(cap_string)
            except Exception as e:
                logger.debug("Failed to parse capabilities: %s", e)

        # Populate client info
        client.id = mac_address
        client.info.mac_address = mac_address
        client.info.device_type = DeviceType.from_id(device_id)
        client.info.firmware_version = str(revision)
        client.info.uuid = uuid
        client.info.capabilities = capabilities
        client.info.model = DEVICE_IDS.get(device_id, f"unknown-{device_id}")

        # Extract name from capabilities if present
        if "Name" in capabilities:
            client.info.name = capabilities["Name"]

        # Set connected state
        client.status.state = PlayerState.CONNECTED
        client.update_last_seen()

    def _parse_capabilities(self, cap_string: str) -> dict[str, str]:
        """
        Parse the capabilities string from HELO.

        Format: "Key1=Value1,Key2=Value2,..."
        """
        capabilities: dict[str, str] = {}

        for part in cap_string.split(","):
            if "=" in part:
                key, value = part.split("=", 1)
                capabilities[key.strip()] = value.strip()
            elif part.strip():
                # Flag without value
                capabilities[part.strip()] = "1"

        return capabilities

    async def _send_server_capabilities(self, client: PlayerClient) -> None:
        """
        Send server capabilities/acknowledgment to client after HELO.

        This sequence matches what LMS sends to SqueezePlay/Jive devices
        (Radio, Boom, Touch) to properly initialize the connection:
        1. vers - Server version string (required for SqueezePlay/Jive!)
        2. strm q - Query status (not 't'!)
        3. setd 0x00 - Player ID type
        4. setd 0x04 - Firmware check
        5. aude - Enable audio outputs
        6. audg - Set initial volume/gain
        """
        logger.debug("Sending server capabilities to %s", client.id)

        # 1. Send 'vers' - Server version (LMS sends this first!)
        # This is critical for SqueezePlay/Jive devices (Radio, Boom, Touch)
        # Without this, they show "Connection not possible"
        # NOTE: Must be 7.x for firmware compatibility - see Research_gold.md
        # LMS uses "7.999.999" (RADIO_COMPATIBLE_VERSION) to bypass this.
        version = "7.999.999"
        await self._send_message(client, "vers", version.encode("utf-8"))
        logger.debug("Sent vers %s to %s", version, client.id)

        from resonance.protocol.commands import (
            AudioFormat,
            AutostartMode,
            StreamCommand,
            StreamParams,
            build_aude_frame,
            build_audg_frame,
            build_strm_frame,
        )

        # 2. Send 'strm q' (query) - this is what LMS sends, NOT 'strm t'
        # The 'q' command queries player status without the extra server IP/port info
        strm_query = build_strm_frame(StreamParams(
            command=StreamCommand.STOP,  # 'q' = stop/query
            autostart=AutostartMode.OFF,
            format=AudioFormat.MP3,
            server_port=0,  # LMS sends 0 here (verified in Squeezebox.pm)
            server_ip=0,
        ))
        await self._send_message(client, "strm", strm_query)
        logger.debug("Sent strm query to %s", client.id)

        # 3. Send 'setd' with type 0x00 (player ID query)
        await self._send_message(client, "setd", b"\x00")
        logger.debug("Sent setd 0x00 to %s", client.id)

        # 4. Send 'setd' with type 0x04 (firmware ID query)
        await self._send_message(client, "setd", b"\x04")
        logger.debug("Sent setd 0x04 to %s", client.id)

        # 5. Send 'aude' to enable audio outputs (S/PDIF and DAC)
        aude_frame = build_aude_frame(spdif_enable=True, dac_enable=True)
        await self._send_message(client, "aude", aude_frame)
        logger.debug("Sent aude (enable audio) to %s", client.id)

        # 6. Send 'audg' to set initial volume/gain
        # LMS sends: 00000000 00000000 01 ff 00000000 00000000 00000003
        # Which is: old_left=0, old_right=0, dvc=1, preamp=255, left=0, right=0, seq=3
        audg_frame = build_audg_frame(
            old_left=0,
            old_right=0,
            new_left=0,
            new_right=0,
            preamp=255,
            digital_volume=True,
        )
        # Add sequence byte (0x03) that LMS sends
        audg_frame += b"\x00\x00\x00\x03"
        await self._send_message(client, "audg", audg_frame)
        logger.debug("Sent audg (volume) to %s", client.id)

    async def _message_loop(
        self,
        client: PlayerClient,
        reader: asyncio.StreamReader,
    ) -> None:
        """
        Main message processing loop for a connected client.

        Reads and dispatches messages until the connection is closed.
        """
        while self._running and client.is_connected:
            try:
                command, payload = await self._read_message(reader)
            except asyncio.IncompleteReadError:
                logger.debug("Client %s disconnected (incomplete read)", client.id)
                break
            except ConnectionResetError:
                logger.debug("Client %s connection reset", client.id)
                break
            except OSError as e:
                # Catch WinError 121 (semaphore timeout) and other socket errors
                logger.warning("Client %s socket error: %s", client.id, e)
                break

            client.update_last_seen()

            # Dispatch to handler
            handler = self._handlers.get(command)
            if handler:
                try:
                    await handler(client, payload)
                except Exception as e:
                    logger.error(
                        "Error handling %s from %s: %s",
                        command,
                        client.id,
                        e,
                    )
            else:
                logger.debug("Unknown command from %s: %s", client.id, command)

    async def _read_message(
        self,
        reader: asyncio.StreamReader,
    ) -> tuple[str, bytes]:
        """
        Read a single Slimproto message from the stream.

        Returns:
            Tuple of (command, payload) where command is a 4-char string.

        Raises:
            asyncio.IncompleteReadError: If connection closed mid-message.
            ProtocolError: If message format is invalid.
        """
        # Read header: 4 bytes command + 4 bytes length
        header = await reader.readexactly(8)

        command = header[:4].decode("ascii", errors="replace")
        length = struct.unpack(">I", header[4:8])[0]

        # Sanity check on length
        if length > 65536:  # 64KB max payload
            raise ProtocolError(f"Message too large: {length} bytes")

        # Read payload
        if length > 0:
            payload = await reader.readexactly(length)
        else:
            payload = b""

        logger.debug("Received %s from client (%d bytes)", command, length)

        return command, payload

    async def _check_heartbeats(self) -> None:
        """
        Periodically check for clients that haven't sent heartbeats.

        Clients that haven't been heard from in CLIENT_TIMEOUT_SECONDS
        are considered dead and disconnected.
        """
        while self._running:
            await asyncio.sleep(CLIENT_CHECK_INTERVAL_SECONDS)

            players = await self.player_registry.get_all()
            for player in players:
                if player.seconds_since_last_seen() > CLIENT_TIMEOUT_SECONDS:
                    logger.warning(
                        "Player %s timed out (no heartbeat for %.1f seconds)",
                        player.id,
                        player.seconds_since_last_seen(),
                    )
                    await player.disconnect()
                    await self.player_registry.unregister(player.id)
                else:
                    # Send periodic heartbeat (strm t) to keep connection alive
                    try:
                        from resonance.protocol.commands import build_stream_status

                        # LMS sends 0/0 for 'strm t' (heartbeat/status query)
                        # See Slim/Player/Squeezebox.pm stream() method for command 't'
                        strm_status = build_stream_status(
                            server_port=0, server_ip=0
                        )
                        await self._send_message(player, "strm", strm_status)
                        logger.debug("Sent heartbeat to %s", player.id)
                    except Exception as e:
                        logger.warning("Failed to send heartbeat to %s: %s", player.id, e)

    # -------------------------------------------------------------------------
    # Message Handlers
    # -------------------------------------------------------------------------

    async def _handle_stat(self, client: PlayerClient, data: bytes) -> None:
        """
        Handle STAT (status) message from player.

        This is the heartbeat/status message sent periodically by players.
        It contains playback state, buffer fullness, elapsed time, etc.

        Format (36 bytes minimum):
            [4] Event code (e.g., 'STMt', 'STMc', etc.)
            [1] Number of CRLF in buffer
            [1] MAS initialized flags (SB1 only)
            [1] MAS mode (SB1 only)
            [4] Buffer size in bytes
            [4] Data in receive buffer
            [8] Bytes received
            [2] Signal strength
            [4] Jiffies
            [4] Output buffer size
            [4] Output buffer fullness
            [4] Elapsed seconds
            [2] Voltage (Boom only)
            [4] Elapsed milliseconds
            [4] Server timestamp
            [2] Error code
        """
        if len(data) < 36:
            logger.warning("STAT too short from %s: %d bytes", client.id, len(data))
            return

        # Parse event code (first 4 bytes)
        event_code = data[:4].decode("ascii", errors="replace")

        # Parse buffer fullness (bytes 11-15)
        buffer_fullness = struct.unpack(">I", data[11:15])[0] if len(data) >= 15 else 0

        # Parse bytes received (bytes 15-23)
        # bytes_received is currently not used, but keep the computation around for debugging
        # (parity with Slimproto status struct) without tripping "unused variable" diagnostics.
        _bytes_received = struct.unpack(">Q", data[15:23])[0] if len(data) >= 23 else 0

        # Parse signal strength (bytes 23-25)
        signal_strength = struct.unpack(">H", data[23:25])[0] if len(data) >= 25 else 0

        # Parse output buffer fullness (bytes 33-37)
        # This indicates decoded audio queued for playback. Unlike input buffer_fullness,
        # it only becomes >0 when decoding/playback actually progressed.
        output_buffer_fullness = struct.unpack(">I", data[33:37])[0] if len(data) >= 37 else 0

        # Parse elapsed seconds (bytes 37-41)
        # Format: [25-28] Jiffies, [29-32] Output buffer size, [33-36] Output buffer fullness, [37-40] Elapsed seconds
        elapsed_seconds = struct.unpack(">I", data[37:41])[0] if len(data) >= 41 else 0

        # Parse elapsed milliseconds (bytes 43-47, if present)
        # Format: [41-42] Voltage (2 bytes), [43-46] Elapsed milliseconds
        elapsed_ms = 0
        if len(data) >= 47:
            elapsed_ms = struct.unpack(">I", data[43:47])[0]

        # Update client status
        client.status.buffer_fullness = buffer_fullness
        client.status.output_buffer_fullness = output_buffer_fullness
        client.status.signal_strength = signal_strength

        # Always accept the raw elapsed from the player.
        # After a seek, the player reports elapsed relative to the NEW stream start (0, 1, 2...).
        # The seek offset is added in the status handler (cmd_status / get_player_status).
        # We must NOT filter or reject low/regressing values here, or the offset math breaks.
        client.status.elapsed_seconds = elapsed_seconds
        client.status.elapsed_milliseconds = elapsed_ms
        client.status.elapsed_report_monotonic = time.monotonic()

        # Maintain "sticky" last-nonzero elapsed to mask transient 0s during
        # stop/flush/stream restarts (common around seeks).
        #
        # Some clients poll status (e.g. 1 Hz). If the player reports elapsed=0
        # for a short window, the UI can jump backwards to 0 and then snap again.
        # Tracking the last good non-zero value lets status handlers avoid that
        # regression during a brief restart window.
        try:
            has_nonzero_s = (elapsed_seconds is not None) and (elapsed_seconds > 0)
            has_nonzero_ms = (elapsed_ms is not None) and (elapsed_ms > 0)

            if has_nonzero_ms:
                client.status.last_nonzero_elapsed_milliseconds = int(elapsed_ms)
                client.status.last_nonzero_elapsed_seconds = float(elapsed_ms) / 1000.0
                client.status.last_nonzero_elapsed_at = time.time()
            elif has_nonzero_s:
                client.status.last_nonzero_elapsed_seconds = float(elapsed_seconds)
                client.status.last_nonzero_elapsed_milliseconds = int(
                    float(elapsed_seconds) * 1000.0
                )
                client.status.last_nonzero_elapsed_at = time.time()
        except Exception:
            # Defensive: never let sticky-elapsed bookkeeping break STAT handling.
            pass

        # Update player state based on event code
        # LMS event codes:
        #   STMp = pause
        #   STMr = resume/play
        #   STMs = track Started (PLAYING!)
        #   STMt = timer/heartbeat
        #   STMd = decode ready (finished)
        #   STMu = underrun (buffer empty - triggers playerStopped in LMS)
        #   STMf = Flush/close (does NOT trigger playerStopped in LMS!)
        #   STMc = connect
        #   STMn = not supported
        #
        # IMPORTANT: LMS only calls playerStopped() on STMu, NOT on STMf!
        # STMf occurs during normal track transitions when the old stream is flushed.
        # Setting state to STOPPED on STMf causes false "stop" status during track changes.
        if event_code.startswith("STM"):
            state_code = event_code[3] if len(event_code) > 3 else ""
            if state_code == "p":  # Paused
                client.status.state = PlayerState.PAUSED
            elif state_code in ("r", "s"):  # Playing/resumed or track Started
                client.status.state = PlayerState.PLAYING
                # Record that playback was confirmed for the current stream
                # generation.  _on_decode_ready / _on_track_finished use this
                # to avoid advancing the playlist when a broken stream (e.g.
                # failed transcode after seek) never actually produced audio.
                if state_code == "s":
                    try:
                        streaming_server = getattr(self, "streaming_server", None)
                        if streaming_server is not None:
                            _gen = streaming_server.get_stream_generation(client.mac_address)
                            if _gen is not None:
                                self._stms_confirmed_generation[client.mac_address] = _gen
                                logger.debug(
                                    "STMs confirmed playback for player %s gen=%s",
                                    client.mac_address, _gen,
                                )

                                # STMs is the authoritative "track started" signal.
                                # Publish it so server-side playlist metadata can move
                                # to the prefetched track even when no STMu arrives
                                # at the handoff boundary.
                                event_bus.publish_sync(
                                    PlayerTrackStartedEvent(
                                        player_id=client.mac_address,
                                        stream_generation=_gen,
                                    )
                                )

                                pending_stmd_gen = self._pending_stmd_generation.get(client.mac_address)
                                if pending_stmd_gen == _gen:
                                    self._pending_stmd_generation.pop(client.mac_address, None)
                                    from resonance.core.events import PlayerDecodeReadyEvent

                                    logger.info(
                                        "Replaying deferred STMd for player %s gen=%s after STMs confirmation",
                                        client.mac_address,
                                        _gen,
                                    )
                                    event_bus.publish_sync(
                                        PlayerDecodeReadyEvent(
                                            player_id=client.mac_address,
                                            stream_generation=_gen,
                                        )
                                    )
                    except Exception:
                        pass
            elif state_code == "u":  # Underrun - this triggers playerStopped in LMS
                client.status.state = PlayerState.STOPPED
            # NOTE: STMf (flush) does NOT set STOPPED - it's a normal part of track transitions
            elif state_code == "t":  # Timer/heartbeat
                # Fallback state promotion for players that may skip STMs/STMr.
                #
                # STRICT guard: only promote to PLAYING when the output buffer
                # actually contains decoded audio (output_buffer_fullness > 0).
                # Previously this also accepted elapsed_seconds > 0 or
                # elapsed_ms > 0, but those values can be stale/bogus from the
                # player firmware after a STOP+FLUSH (e.g. Squeezebox Radio
                # reports elapsed=9s on a brand-new stream that never produced
                # output).  Promoting to PLAYING on a broken/stalled stream
                # enables server-side track-end detection and can trigger
                # STOP+START cycling through the entire playlist.
                #
                # Additional guard: only promote if playback was confirmed
                # (STMs received) for the current stream generation.  Without
                # this, a transcode that fills the input buffer but produces
                # undecodable output (buf>0, out=0) could still be promoted
                # via a brief output_buffer_fullness blip.
                _mac = client.mac_address
                _stms_gen = self._stms_confirmed_generation.get(_mac)
                _cur_gen_for_promo: int | None = None
                try:
                    _ss = getattr(self, "streaming_server", None)
                    if _ss is not None:
                        _cur_gen_for_promo = _ss.get_stream_generation(_mac)
                except Exception:
                    pass

                _playback_confirmed_for_promo = (
                    _stms_gen is not None
                    and _cur_gen_for_promo is not None
                    and _stms_gen == _cur_gen_for_promo
                )

                has_real_playback_progress = output_buffer_fullness > 0

                if (
                    buffer_fullness > 0
                    and has_real_playback_progress
                    and _playback_confirmed_for_promo
                    and client.status.state not in (
                        PlayerState.PLAYING,
                        PlayerState.PAUSED,
                    )
                ):
                    logger.debug(
                        "STMt with confirmed playback progress (buf=%d out=%d elapsed=%d/%d gen=%s) "
                        "- setting state to PLAYING",
                        buffer_fullness,
                        output_buffer_fullness,
                        elapsed_seconds,
                        elapsed_ms,
                        _cur_gen_for_promo,
                    )
                    client.status.state = PlayerState.PLAYING

        logger.debug(
            "STAT %s from %s: buffer=%d, elapsed=%ds",
            event_code,
            client.id,
            buffer_fullness,
            elapsed_seconds,
        )

        # Helper to get start_offset for LMS-style elapsed correction.
        # After a seek to position X, the player reports elapsed relative to stream start.
        # Real position = start_offset + raw_elapsed (same formula as in status.py/api.py).
        def _get_start_offset() -> float:
            try:
                streaming_server = getattr(self, "streaming_server", None)
                if streaming_server is not None:
                    get_offset = getattr(streaming_server, "get_start_offset", None)
                    if get_offset is not None:
                        return get_offset(client.mac_address)
            except Exception:
                pass
            return 0.0

        # SlimServer semantics:
        # - STMd = DECODE_READY (decoder has no more input data) -> NOT track finished
        # - STMu = UNDERRUN (output buffer empty)                -> track finished / playerStopped
        #
        # STMd does NOT advance the playlist. But it signals that the player's
        # decoder consumed all input for the current track → time to prefetch
        # the next track so crossfade/gapless can work.
        if event_code == "STMd":
            from resonance.core.events import PlayerDecodeReadyEvent

            def _get_stream_generation_for_stmd() -> int | None:
                try:
                    streaming_server = getattr(self, "streaming_server", None)
                    if streaming_server is not None:
                        get_gen = getattr(streaming_server, "get_stream_generation", None)
                        if get_gen is not None:
                            return get_gen(client.mac_address)
                except Exception:
                    pass
                return None

            stmd_generation = _get_stream_generation_for_stmd()

            # Guard: only trigger prefetch if the player actually confirmed
            # playback (STMs) for this stream generation.  Without this,
            # a broken transcode (e.g. failed M4B seek) causes an endless
            # STOP+START cycle: decoder consumes broken input → STMd →
            # prefetch next track → also broken → repeat.
            _confirmed_gen = self._stms_confirmed_generation.get(client.mac_address)
            if stmd_generation is not None and _confirmed_gen != stmd_generation:
                # Race guard: STMd and STMs can arrive almost together. When
                # STMd is first, defer it briefly so STMs can confirm playback.
                if _confirmed_gen is None or _confirmed_gen < stmd_generation:
                    self._pending_stmd_generation[client.mac_address] = stmd_generation
                    logger.info(
                        "STMd from player %s gen=%s deferred: waiting for STMs confirmation "
                        "(confirmed_gen=%s)",
                        client.mac_address,
                        stmd_generation,
                        _confirmed_gen,
                    )
                else:
                    logger.info(
                        "STMd from player %s gen=%s ignored as stale (confirmed_gen=%s)",
                        client.mac_address,
                        stmd_generation,
                        _confirmed_gen,
                    )
                return

            if stmd_generation is not None:
                self._pending_stmd_generation.pop(client.mac_address, None)

            logger.info(
                "Decode ready (STMd) from player %s (generation %s) - triggering prefetch",
                client.mac_address,
                stmd_generation,
            )
            event_bus.publish_sync(
                PlayerDecodeReadyEvent(
                    player_id=client.mac_address,
                    stream_generation=stmd_generation,
                )
            )
            return

        # Fire PlayerTrackFinishedEvent on STMu (UNDERRUN) to match SlimServer's
        # playerStopped() semantics.
        if event_code == "STMu":
            # Helper to get stream generation via public API
            def _get_stream_generation() -> int | None:
                try:
                    streaming_server = getattr(self, "streaming_server", None)
                    if streaming_server is not None:
                        get_gen = getattr(streaming_server, "get_stream_generation", None)
                        if get_gen is not None:
                            return get_gen(client.mac_address)
                except Exception:
                    logger.debug(
                        "Failed to get stream generation for player %s",
                        client.mac_address,
                        exc_info=True,
                    )
                return None

            stream_generation = _get_stream_generation()

            # Guard: only advance the playlist if playback was confirmed (STMs).
            #
            # Important prefetch nuance:
            # while track N is still playing, STMd can prequeue track N+1 and
            # increment stream_generation.  The later STMu belongs to track N,
            # so confirmed_gen can legitimately be (stream_generation - 1).
            # Rejecting that case keeps playlist metadata stuck on the old track.
            _confirmed_gen = self._stms_confirmed_generation.get(client.mac_address)
            _confirmed_current = (
                stream_generation is not None
                and _confirmed_gen is not None
                and _confirmed_gen == stream_generation
            )
            _confirmed_prefetch_handoff = (
                stream_generation is not None
                and _confirmed_gen is not None
                and _confirmed_gen + 1 == stream_generation
            )
            if not (_confirmed_current or _confirmed_prefetch_handoff):
                logger.info(
                    "STMu from player %s gen=%s ignored: playback not confirmed "
                    "for current/prefetch handoff (confirmed_gen=%s) — not advancing playlist",
                    client.mac_address, stream_generation, _confirmed_gen,
                )
                return

            logger.info(
                "Track finished (STMu/UNDERRUN) from player %s - advancing playlist",
                client.mac_address,
            )

            event_bus.publish_sync(
                PlayerTrackFinishedEvent(
                    player_id=client.mac_address,
                    stream_generation=stream_generation,
                )
            )

        # LMS semantics: track-finished is driven by STMu (underrun).
        # Do not synthesize track-finished from STMt heartbeats.
        # Publish status event for Cometd subscribers.
        # State changes (STMp/r/s) always publish immediately.
        # STMt heartbeats publish throttled elapsed-time updates during playback
        # so that JiveLite / Web-UI / Cadence receive periodic time corrections.
        should_publish = False
        if event_code.startswith("STM"):
            state_code = event_code[3:4]
            if state_code in ("p", "r", "s"):
                # Always publish on state transitions — these are rare and important.
                should_publish = True
                # Reset elapsed-push timer so the next STMt doesn't fire too soon
                # after the state-change push.
                self._last_elapsed_push[client.mac_address] = time.monotonic()
            elif state_code == "t" and client.status.state == PlayerState.PLAYING:
                # Throttled elapsed-time push during playback.
                now_mono = time.monotonic()
                last_push = self._last_elapsed_push.get(client.mac_address, 0.0)
                if now_mono - last_push >= ELAPSED_PUSH_INTERVAL_SECONDS:
                    should_publish = True
                    self._last_elapsed_push[client.mac_address] = now_mono

        if should_publish:
            # Apply LMS-style start_offset correction for consistent elapsed time.
            start_offset = _get_start_offset()
            corrected_elapsed_sec = (elapsed_seconds or 0) + start_offset
            corrected_elapsed_ms = (elapsed_ms or 0) + int(start_offset * 1000)

            event_bus.publish_sync(
                PlayerStatusEvent(
                    player_id=client.mac_address,
                    state=client.status.state.value,
                    volume=client.status.volume,
                    muted=client.status.muted,
                    elapsed_seconds=corrected_elapsed_sec,
                    elapsed_milliseconds=corrected_elapsed_ms,
                )
            )

    async def _handle_bye(self, client: PlayerClient, data: bytes) -> None:
        """Handle BYE! message - player is disconnecting."""
        logger.info("Player %s sent BYE!", client.id)
        client.status.state = PlayerState.DISCONNECTED

        # Clear elapsed-push throttle so a reconnecting player gets a fresh
        # push immediately on the first STMt after reconnect.
        self._last_elapsed_push.pop(client.mac_address, None)
        self._pending_stmd_generation.pop(client.mac_address, None)

        # Clear start_offset to prevent stale elapsed after reconnect.
        # Without this, a reconnecting player could show incorrect position
        # if start_offset from a previous seek was still cached.
        streaming_server = getattr(self, "streaming_server", None)
        if streaming_server is not None:
            clear_fn = getattr(streaming_server, "clear_start_offset", None)
            if callable(clear_fn):
                clear_fn(client.mac_address)

        # Publish disconnect event
        await event_bus.publish(PlayerDisconnectedEvent(player_id=client.mac_address))

    async def _handle_ir(self, client: PlayerClient, data: bytes) -> None:
        """
        Handle IR (infrared remote) message.

        Format:
            [4] Time since startup in ticks (1KHz)
            [1] Code format
            [1] Number of bits
            [4] IR code (up to 32 bits)
        """
        if len(data) < 10:
            return

        ir_time = struct.unpack(">I", data[:4])[0]
        ir_code = data[6:10].hex()

        logger.debug("IR from %s: code=%s, time=%d", client.id, ir_code, ir_time)

        # Dispatch IR code to playback command (LMS: IR::enqueue → execute(['ir',...]))
        await self._dispatch_ir(client, ir_code, ir_time)

    async def _handle_resp(self, client: PlayerClient, data: bytes) -> None:
        """Handle RESP message - HTTP response headers from player.

        LMS (Slimproto.pm _http_response_handler): logs headers, clears
        ``connecting`` flag, and forwards to ``directHeaders()`` for
        content-type / bitrate extraction.  We log and store the raw
        headers on the player for debugging; no further action needed
        because Resonance controls the stream source directly.
        """
        headers_text = data.decode("latin-1", errors="replace")
        logger.info("RESP from %s: HTTP response received (%d bytes)", client.id, len(data))
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("RESP headers:\n%s", headers_text)

        # Store last response headers for diagnostics (mirrors LMS directHeaders)
        client.last_resp_headers = headers_text

    async def _handle_meta(self, client: PlayerClient, data: bytes) -> None:
        """Handle META message - stream metadata from player.

        LMS (_http_metadata_handler): forwards to ``directMetadata()``
        which extracts ICY ``StreamTitle`` for display.  We do the same
        extraction here and store it on the player object.
        """
        meta_text = data.decode("latin-1", errors="replace")
        logger.debug("META from %s: %d bytes — %s", client.id, len(data), meta_text[:200])

        # Extract ICY StreamTitle if present (e.g. "StreamTitle='Artist - Song';")
        match = re.search(r"StreamTitle='([^']*)'", meta_text)
        if match:
            icy_title = match.group(1).strip()
            client.icy_title = icy_title
            logger.info("META from %s: ICY title = '%s'", client.id, icy_title)
        else:
            client.icy_title = None

    async def _handle_dsco(self, client: PlayerClient, data: bytes) -> None:
        """Handle DSCO message - player's data stream disconnected.

        DSCO is sent by the player when its HTTP data connection closes.
        This is a core Slimproto protocol event that LMS uses to detect
        end-of-stream conditions.

        Disconnect reason codes (from LMS Slim::Networking::Slimproto):
            0 - Connection closed normally       (TCP_CLOSE_FIN)
            1 - Connection reset by local host   (TCP_CLOSE_LOCAL_RST)
            2 - Connection reset by remote host  (TCP_CLOSE_REMOTE_RST)
            3 - Connection is no longer able to work (TCP_CLOSE_UNREACHABLE)
            4 - Connection timed out             (TCP_CLOSE_LOCAL_TIMEOUT)

        On reason=0 (normal close), LMS calls ``statHandler('EoS')`` which
        maps to ``playerEndOfStream()`` → the ``EndOfStream`` event in the
        StreamingController state machine.  For players that never send
        STMd/STMu (e.g. controller-class devices with transitionType=0),
        this is the **only** signal that the data transfer finished.

        We mirror this by firing a deferred ``PlayerTrackFinishedEvent``
        when the stream age exceeds the track duration (i.e. the output
        buffer has had time to drain).  For error disconnects we log and
        ignore — matching LMS behaviour when the controller is already
        in STREAMOUT state.
        """
        reason = data[0] if len(data) >= 1 else 0

        _DSCO_REASONS = {
            0: "Connection closed normally",
            1: "Connection reset by local host",
            2: "Connection reset by remote host",
            3: "Connection is no longer able to work",
            4: "Connection timed out",
        }

        reason_text = _DSCO_REASONS.get(reason, f"Unknown({reason})")

        if reason == 0:
            logger.info(
                "DSCO from %s: %s — normal disconnect",
                client.id, reason_text,
            )
            # Record normal disconnect generation for diagnostics/compat state.
            streaming_server = getattr(self, "streaming_server", None)
            if streaming_server is not None:
                gen = streaming_server.get_stream_generation(client.mac_address)
                if gen is not None:
                    self._dsco_received_generation[client.mac_address] = gen
                    logger.debug(
                        "DSCO normal: recorded generation %s for player %s",
                        gen, client.mac_address,
                    )
        else:
            logger.warning(
                "DSCO from %s: %s (reason=%d) — ignoring error disconnect",
                client.id, reason_text, reason,
            )

    async def _handle_butn(self, client: PlayerClient, data: bytes) -> None:
        """Handle BUTN message - hardware button press (Transporter/Boom).

        LMS (_button_handler): unpacks (time, button_hex) and feeds the
        button code into ``IR::enqueue`` — identical path to IR codes.
        We mirror this by dispatching through the same IR mapping table.
        """
        if len(data) < 8:
            logger.warning("BUTN from %s: bad length %d (expected >= 8)", client.id, len(data))
            return

        btn_time = struct.unpack(">I", data[:4])[0]
        btn_code = data[4:8].hex()
        logger.info("BUTN from %s: button=%s, time=%d", client.id, btn_code, btn_time)

        # Dispatch through same path as IR (LMS treats buttons identically)
        await self._dispatch_ir(client, btn_code, btn_time)

    async def _handle_knob(self, client: PlayerClient, data: bytes) -> None:
        """Handle KNOB message - rotary encoder input (Transporter/Boom).

        LMS (_knob_handler): unpacks (time, position, sync), checks sync
        counter, updates knobPos, and calls ``IR::executeButton('knob')``.
        We parse and log; volume adjustment can be added when Transporter
        hardware is available for testing.
        """
        if len(data) < 9:
            logger.warning("KNOB from %s: bad length %d (expected >= 9)", client.id, len(data))
            return

        knob_time, raw_position, sync = struct.unpack(">IiB", data[:9])

        logger.info(
            "KNOB from %s: position=%d, time=%d, sync=%d",
            client.id, raw_position, knob_time, sync,
        )

    async def _handle_setd(self, client: PlayerClient, data: bytes) -> None:
        """Handle SETD message - player settings/preferences.

        LMS (_settings_handler → playerSettingsFrame): dispatches on
        setting ID byte:
            0 = Player name (string payload)
            4 = Disabled flag (0/1)
        """
        if len(data) < 1:
            return

        setting_id = data[0]
        payload = data[1:]

        if setting_id == 0 and payload:
            # Player name update
            name = payload.decode("utf-8", errors="replace").rstrip("\x00")
            if name:
                old_name = client.name
                client.name = name
                logger.info(
                    "SETD from %s: player name changed '%s' → '%s'",
                    client.id, old_name, name,
                )
        elif setting_id == 4:
            # Disabled flag
            disabled = payload[0] if payload else 0
            logger.info("SETD from %s: disabled=%d", client.id, disabled)
        else:
            logger.debug(
                "SETD from %s: unknown setting_id=%d, %d payload bytes",
                client.id, setting_id, len(payload),
            )

    async def _handle_anic(self, client: PlayerClient, data: bytes) -> None:
        """Handle ANIC message - animation complete.

        LMS (_animation_complete_handler): forwards to display engine
        ``clientAnimationComplete()``.  Since Resonance does not drive
        VFD/LCD bitmap displays, this is a no-op — we just log it.
        """
        logger.debug("ANIC from %s: animation complete", client.id)

    # -------------------------------------------------------------------------
    # IR / Button Dispatch
    # -------------------------------------------------------------------------

    # IR/button code mapping for LMS-compatible playback controls.
    #
    # Sources:
    # - IR/Slim_Devices_Remote.ir
    # - IR/jvc_dvd.ir
    # - IR/Front_Panel.ir (down events only; up events are intentionally ignored)
    #
    # This remains focused on transport/power/volume/mute controls.
    _IR_CODE_MAP: dict[str, str] = {
        # Slim Devices remote (JVC)
        "768910ef": "play",
        "768920df": "pause",
        "7689a05f": "playlist_next",
        "7689c03f": "playlist_prev",
        "7689807f": "volume_up",
        "768900ff": "volume_down",
        "768940bf": "power_toggle",
        "76898f70": "power_on",
        "76898778": "power_off",
        "7689c43b": "mute_toggle",

        # JVC DVD remote profile used by LMS
        "0000f732": "play",
        "0000f7d6": "play",
        "0000f7b2": "pause",
        "0000f7c2": "stop",
        "0000f76e": "playlist_next",
        "0000f70e": "playlist_prev",
        "0000c078": "volume_up",
        "0000c578": "volume_up",
        "0000f778": "volume_up",
        "0000c0f8": "volume_down",
        "0000c5f8": "volume_down",
        "0000f7f8": "volume_down",
        "0000f702": "power_toggle",
        "0000f701": "power_on",
        "0000f700": "power_off",
        "0000c038": "mute_toggle",
        "0000c538": "mute_toggle",

        # Front panel buttons (".down" events only)
        "00010012": "play",
        "00010017": "pause",
        "00010010": "playlist_prev",
        "00010011": "playlist_next",
        "00010019": "volume_up",
        "0001001a": "volume_down",
        "0001000a": "power_toggle",

        # Boom hardware buttons (observed BUTN codes)
        "0000f501": "volume_up",
        "0000f502": "volume_down",
        "0000f508": "pause",
        "0000f509": "power_toggle",
    }

    _IR_ACTION_COMMANDS: dict[str, tuple[str, ...]] = {
        "play": ("play",),
        "pause": ("pause",),
        "stop": ("stop",),
        "playlist_next": ("playlist", "index", "+1"),
        "playlist_prev": ("playlist", "index", "-1"),
        "volume_up": ("mixer", "volume", "+5"),
        "volume_down": ("mixer", "volume", "-5"),
        "power_toggle": ("power",),
        "power_on": ("power", "1"),
        "power_off": ("power", "0"),
        "mute_toggle": ("mixer", "muting", "toggle"),
    }

    async def _dispatch_ir(
        self,
        client: PlayerClient,
        ir_code: str,
        ir_time: int,
    ) -> None:
        """Map an IR/button code to a playback command and execute it.

        This is a simplified version of LMS ``Slim::Hardware::IR`` which
        maintains a full IR queue with timing-based repeat/hold detection.
        For now we map known codes directly to commands; repeat-suppression
        uses a simple time-gate per player.
        """
        action = self._IR_CODE_MAP.get(ir_code)
        if action is None:
            logger.debug(
                "IR from %s: unmapped code %s (time=%d) — ignoring",
                client.id, ir_code, ir_time,
            )
            return

        # Simple repeat-gate: ignore identical codes within 300 ms
        gate_key = client.mac_address
        now_ms = ir_time  # player ticks @ 1 kHz
        last = getattr(self, "_ir_last", {})
        if gate_key in last:
            prev_code, prev_time = last[gate_key]
            if prev_code == ir_code and abs(now_ms - prev_time) < 300:
                logger.debug("IR from %s: repeat-suppressed %s", client.id, ir_code)
                return
        last[gate_key] = (ir_code, now_ms)
        self._ir_last = last

        logger.info("IR from %s: code=%s → action=%s", client.id, ir_code, action)

        # Execute the mapped command through JSON-RPC handler
        jsonrpc_handler = getattr(self, "jsonrpc_handler", None)
        if jsonrpc_handler is None:
            logger.warning("IR dispatch: no jsonrpc_handler wired — cannot execute '%s'", action)
            return

        command = self._IR_ACTION_COMMANDS.get(action)
        if command is None:
            logger.warning("IR dispatch: no handler for action '%s'", action)
            return

        try:
            await jsonrpc_handler(client.mac_address, list(command))
        except Exception:
            logger.exception("IR dispatch error for action '%s' on %s", action, client.id)

    # -------------------------------------------------------------------------
    # Stream Control Commands
    # -------------------------------------------------------------------------

    async def stream_start(
        self,
        player_id: str,
        stream_url: str | None = None,
        server_port: int = 9000,
        format: AudioFormat = AudioFormat.MP3,
        autostart: AutostartMode = AutostartMode.AUTO,
        buffer_threshold_kb: int = 255,
    ) -> bool:
        """
        Start streaming audio to a player.

        This sends a 'strm' command with 's' (start) to tell the player
        to connect back to the server and start playing audio.

        Args:
            player_id: MAC address of the target player.
            stream_url: Optional custom URL. If not provided, uses default
                        stream endpoint with player MAC.
            server_port: HTTP port the player should connect to for streaming.
            format: Audio format (MP3, FLAC, etc.).
            autostart: When to start playback.
            buffer_threshold_kb: Buffer size in KB before playback starts.

        Returns:
            True if command was sent, False if player not found.
        """
        player = await self.player_registry.get_by_mac(player_id)
        if not player:
            logger.warning("Cannot start stream: player %s not found", player_id)
            return False

        # Build the HTTP request string
        if stream_url:
            request_string = f"GET {stream_url} HTTP/1.0\r\n\r\n"
        else:
            request_string = f"GET /stream.mp3?player={player_id} HTTP/1.0\r\n\r\n"

        # IMPORTANT:
        # Do NOT advertise server_ip=0 (0.0.0.0) here.
        # A player will interpret that as "connect to server 0" and fail.
        #
        # Instead, advertise a reachable IP:
        # - If the player is local/loopback, use 127.0.0.1
        # - Otherwise, use the bound host unless it's 0.0.0.0, in which case
        #   fall back to the peer-facing local interface IP for this connection.
        #
        # Note: binding to 0.0.0.0 is fine; advertising it is not.
        advertise_ip = self.get_advertise_ip_for_player(player)

        params = StreamParams(
            format=format,
            autostart=autostart,
            buffer_threshold_kb=buffer_threshold_kb,
            server_port=server_port,
            server_ip=advertise_ip,
        )

        frame = build_strm_frame(params, request_string)

        logger.info(
            "Starting stream for player %s (port=%d, format=%s)",
            player_id,
            server_port,
            format.name,
        )
        await self._send_message(player, "strm", frame)
        return True

    async def stream_pause(self, player_id: str) -> bool:
        """
        Pause playback on a player.

        Args:
            player_id: MAC address of the target player.

        Returns:
            True if command was sent, False if player not found.
        """
        player = await self.player_registry.get_by_mac(player_id)
        if not player:
            return False

        frame = build_stream_pause()
        logger.info("Pausing stream for player %s", player_id)
        await self._send_message(player, "strm", frame)
        return True

    async def stream_unpause(self, player_id: str) -> bool:
        """
        Resume playback on a player.

        Args:
            player_id: MAC address of the target player.

        Returns:
            True if command was sent, False if player not found.
        """
        player = await self.player_registry.get_by_mac(player_id)
        if not player:
            return False

        frame = build_stream_unpause()
        logger.info("Resuming stream for player %s", player_id)
        await self._send_message(player, "strm", frame)
        return True

    async def stream_stop(self, player_id: str) -> bool:
        """
        Stop playback on a player.

        Args:
            player_id: MAC address of the target player.

        Returns:
            True if command was sent, False if player not found.
        """
        player = await self.player_registry.get_by_mac(player_id)
        if not player:
            return False

        frame = build_stream_stop()
        logger.info("Stopping stream for player %s", player_id)
        await self._send_message(player, "strm", frame)
        return True

    async def set_volume(
        self,
        player_id: str,
        volume: int,
        muted: bool = False,
    ) -> bool:
        """
        Set the volume on a player.

        Args:
            player_id: MAC address of the target player.
            volume: Volume level 0-100.
            muted: Whether to mute the player.

        Returns:
            True if command was sent, False if player not found.
        """
        player = await self.player_registry.get_by_mac(player_id)
        if not player:
            return False

        frame = build_volume_frame(volume, muted)
        logger.info(
            "Setting volume for player %s: %d%s", player_id, volume, " (muted)" if muted else ""
        )
        await self._send_message(player, "audg", frame)

        # Update local state
        player.status.volume = volume
        player.status.muted = muted
        return True

    async def set_display_brightness(self, player_id: str, brightness_code: int) -> bool:
        """
        Send a 'grfb' brightness update to a player.

        Args:
            player_id: MAC address of the target player.
            brightness_code: Signed 16-bit brightness code.

        Returns:
            True if command was sent, False if player not found.
        """
        player = await self.player_registry.get_by_mac(player_id)
        if not player:
            return False

        frame = build_display_brightness(brightness_code)
        logger.debug(
            "Sending grfb to player %s: code=%d",
            player_id,
            brightness_code,
        )
        await self._send_message(player, "grfb", frame)
        return True

    async def send_display_bitmap(
        self,
        player_id: str,
        bitmap: bytes,
        *,
        offset: int = 0,
        transition: str = "c",
        param: int = 0,
    ) -> bool:
        """
        Send a 'grfe' bitmap frame to a player.

        Args:
            player_id: MAC address of the target player.
            bitmap: Raw bitmap bytes.
            offset: Framebuffer offset.
            transition: Transition character.
            param: Transition parameter byte.

        Returns:
            True if command was sent, False if player not found.
        """
        player = await self.player_registry.get_by_mac(player_id)
        if not player:
            return False

        frame = build_display_bitmap(
            bitmap,
            offset=offset,
            transition=transition,
            param=param,
        )
        logger.debug(
            "Sending grfe to player %s: offset=%d transition=%s param=%d bytes=%d",
            player_id,
            offset,
            transition,
            param,
            len(bitmap),
        )
        await self._send_message(player, "grfe", frame)
        return True

    async def clear_display(self, player_id: str, bitmap_size: int = 1280) -> bool:
        """
        Clear a player's graphics display by sending a zeroed grfe frame.

        Args:
            player_id: MAC address of the target player.
            bitmap_size: Size of zero bitmap payload.

        Returns:
            True if command was sent, False if player not found.
        """
        player = await self.player_registry.get_by_mac(player_id)
        if not player:
            return False

        frame = build_display_clear(bitmap_size=bitmap_size)
        logger.debug("Sending grfe clear to player %s: bytes=%d", player_id, bitmap_size)
        await self._send_message(player, "grfe", frame)
        return True

    async def send_display_framebuffer(
        self,
        player_id: str,
        bitmap: bytes,
        *,
        offset: int = DEFAULT_GRFD_FRAMEBUFFER_OFFSET,
    ) -> bool:
        """
        Send a 'grfd' legacy framebuffer frame to a player.

        Args:
            player_id: MAC address of the target player.
            bitmap: Raw framebuffer bytes.
            offset: Framebuffer offset (LMS live default: 560).

        Returns:
            True if command was sent, False if player not found.
        """
        player = await self.player_registry.get_by_mac(player_id)
        if not player:
            return False

        frame = build_display_framebuffer(bitmap, offset=offset)
        logger.debug(
            "Sending grfd to player %s: offset=%d bytes=%d",
            player_id,
            offset,
            len(bitmap),
        )
        await self._send_message(player, "grfd", frame)
        return True

    async def clear_display_framebuffer(
        self,
        player_id: str,
        *,
        bitmap_size: int = DEFAULT_GRFD_BITMAP_BYTES,
        offset: int = DEFAULT_GRFD_FRAMEBUFFER_OFFSET,
    ) -> bool:
        """
        Clear a player's legacy graphics framebuffer using grfd.

        Args:
            player_id: MAC address of the target player.
            bitmap_size: Size of zero framebuffer payload.
            offset: Framebuffer offset (LMS live default: 560).

        Returns:
            True if command was sent, False if player not found.
        """
        player = await self.player_registry.get_by_mac(player_id)
        if not player:
            return False

        frame = build_display_framebuffer_clear(
            bitmap_size=bitmap_size,
            offset=offset,
        )
        logger.debug(
            "Sending grfd clear to player %s: offset=%d bytes=%d",
            player_id,
            offset,
            bitmap_size,
        )
        await self._send_message(player, "grfd", frame)
        return True

    # -------------------------------------------------------------------------
    # Server Commands (sending to players)
    # -------------------------------------------------------------------------

    async def send_to_player(
        self,
        player_id: str,
        command: str,
        payload: bytes = b"",
    ) -> bool:
        """
        Send a command to a specific player.

        Args:
            player_id: MAC address of the target player.
            command: 4-character command code.
            payload: Command payload data.

        Returns:
            True if message was sent, False if player not found.
        """
        player = await self.player_registry.get_by_mac(player_id)
        if not player:
            return False

        await self._send_message(player, command, payload)
        return True

    async def _send_message(
        self,
        client: PlayerClient,
        command: str,
        payload: bytes = b"",
    ) -> None:
        """
        Send a message to a player.

        Args:
            client: Target player client.
            command: 4-character command code.
            payload: Command payload data.
        """
        if len(command) != 4:
            raise ValueError(f"Command must be 4 characters: {command}")

        # Always emit a fallback diagnostic line (stderr) so we can see TX frames
        # even when logging configuration doesn't show DEBUG output.
        _force_outgoing_frame_debug_log(command, client.id, payload)

        if OUTGOING_FRAME_DEBUG and logger.isEnabledFor(logging.DEBUG):
            # We don't know the on-wire framing here (it depends on the player implementation),
            # but we can still log command + payload information deterministically.
            logger.debug(
                "TX to %s cmd=%s payload_len=%d payload_hex=%s",
                client.id,
                command,
                len(payload),
                _hexdump(payload),
            )

            # Special-case: show likely strm header fields when present (first 24 bytes).
            if command == "strm" and len(payload) >= 24:
                fixed = payload[:24]
                # Bytes 0..6 are ASCII-ish fields in many implementations.
                try:
                    cmd_ch = fixed[0:1].decode("ascii", errors="replace")
                    autostart_ch = fixed[1:2].decode("ascii", errors="replace")
                    format_ch = fixed[2:3].decode("ascii", errors="replace")
                except Exception:
                    cmd_ch = "?"
                    autostart_ch = "?"
                    format_ch = "?"
                server_port = struct.unpack(">H", fixed[18:20])[0]
                server_ip = struct.unpack(">I", fixed[20:24])[0]
                logger.debug(
                    "TX strm parsed: command=%s autostart=%s format=%s server_port=%d server_ip=0x%08x",
                    cmd_ch,
                    autostart_ch,
                    format_ch,
                    server_port,
                    server_ip,
                )

                if len(payload) > 24:
                    req_preview = payload[24 : 24 + 200]
                    logger.debug(
                        "TX strm request_preview=%r",
                        req_preview.decode("latin-1", errors="replace"),
                    )

        await client.send_message(command.encode("ascii"), payload)

    @property
    def is_running(self) -> bool:
        """Check if the server is running."""
        return self._running
