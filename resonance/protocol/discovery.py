"""
UDP Discovery Protocol for Resonance.

This module implements the UDP discovery protocol used by Squeezebox players
to find servers on the local network. Players send broadcast packets to port
3483 and servers respond with their hostname/IP information.

Discovery Protocol:
    - Old format (SLIMP3/Squeezebox): Player sends 'd' packet, server responds with 'D'
    - TLV format (newer players): Player sends 'e' packet with TLV data, server responds with 'E'

TLV Format:
    Request/Response starts with 'e'/'E' followed by TLV entries:
    - T: 4-byte tag (e.g., 'NAME', 'IPAD', 'JSON', 'VERS', 'UUID')
    - L: 1-byte length
    - V: value bytes (0-255 bytes)

Reference: Slim/Networking/UDP.pm and Slim/Networking/Discovery.pm from LMS
"""

import asyncio
import logging
import socket
import struct

logger = logging.getLogger(__name__)

# IANA-assigned port for Slim protocol
DISCOVERY_PORT = 3483

# Maximum hostname length for display on player
MAX_HOSTNAME_LENGTH = 16


class UDPDiscoveryProtocol(asyncio.DatagramProtocol):
    """
    Asyncio UDP protocol handler for Squeezebox discovery.

    Handles both old-style 'd' discovery requests and new TLV-based 'e' requests.
    """

    def __init__(
        self,
        server_name: str,
        http_port: int,
        server_uuid: str | None = None,
        version: str = "7.999.999",
    ) -> None:
        """
        Initialize the discovery protocol.

        Args:
            server_name: Server name to advertise (max 16 chars displayed).
            http_port: HTTP/JSON-RPC port for the server.
            server_uuid: Unique server identifier.
            version: Server version string.
        """
        self.server_name = server_name
        self.http_port = http_port
        self.server_uuid = server_uuid or "resonance-server"
        self.version = version
        self.transport: asyncio.DatagramTransport | None = None

        # Local IP address (filled in when we receive first packet)
        self._local_ip: str | None = None

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:
        """Called when the UDP socket is ready."""
        self.transport = transport
        logger.info("UDP Discovery listening on port %d", DISCOVERY_PORT)

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        """
        Handle incoming UDP discovery packets.

        Args:
            data: Raw UDP packet data.
            addr: (host, port) tuple of the sender.
        """
        if not data:
            return

        client_ip, client_port = addr

        first_byte = data[0:1]

        if first_byte == b'd':
            # Old-style discovery request (SLIMP3/Squeezebox)
            self._handle_old_discovery(data, addr)

        elif first_byte == b'e':
            # TLV-based discovery request (newer players)
            self._handle_tlv_discovery(data, addr)

        elif first_byte == b'h':
            # Hello packet from SLIMP3 - respond with hello
            if len(data) < 3 or data[1:3] != b'\x00\x00':
                self._handle_hello(addr)

        else:
            logger.debug(
                "Ignoring unknown UDP packet from %s:%d: %s",
                client_ip,
                client_port,
                data[:20].hex(),
            )

    def _handle_old_discovery(self, data: bytes, addr: tuple[str, int]) -> None:
        """
        Handle old-style 'd' discovery request.

        Packet format:
            [0] 'd' - discovery marker
            [1] device_id
            [2] revision
            [3-10] padding
            [11-16] MAC address (6 bytes)
        """
        client_ip, client_port = addr

        if len(data) >= 18:
            device_id = data[1]
            revision = data[2]
            mac_bytes = data[12:18]
            mac = ':'.join(f'{b:02x}' for b in mac_bytes)

            revision_str = f"{revision // 16}.{revision % 16}"
            logger.info(
                "Discovery request from %s:%d - device=%d, revision=%s, MAC=%s",
                client_ip,
                client_port,
                device_id,
                revision_str,
                mac,
            )
        else:
            logger.info("Discovery request from %s:%d", client_ip, client_port)

        # Build response: 'D' + 17-byte hostname (null-padded)
        hostname = self._get_padded_hostname()
        response = b'D' + hostname

        self._send_response(response, addr)
        logger.debug("Sent discovery response to %s:%d", client_ip, client_port)

    def _handle_tlv_discovery(self, data: bytes, addr: tuple[str, int]) -> None:
        """
        Handle TLV-based 'e' discovery request.

        Request format: 'e' + TLV entries
        Response format: 'E' + TLV entries

        Common TLV tags:
            NAME - Server name (full, no truncation)
            IPAD - IP address as string
            JSON - JSON-RPC port as string
            VERS - Server version
            UUID - Server UUID
            JVID - Jive device ID (info only, no response)
        """
        client_ip, client_port = addr

        logger.info("TLV Discovery request from %s:%d", client_ip, client_port)

        # Determine our local IP that can reach this client
        local_ip = self._get_local_ip_for_client(client_ip)

        # Parse request TLVs (skip leading 'e')
        request_tlvs = self._parse_tlvs(data[1:])

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("Requested TLVs: %s", list(request_tlvs.keys()))

        # Build response TLVs based on what was requested
        response = b'E'
        response_debug = []

        for tag in request_tlvs:
            value = self._get_tlv_value(tag, request_tlvs.get(tag), local_ip)
            if value is not None:
                # Truncate if too long
                if len(value) > 255:
                    logger.warning("TLV response %s too long, truncating", tag)
                    value = value[:255]

                response += tag.encode('ascii')
                response += struct.pack('B', len(value))
                response += value
                value_str = value.decode('utf-8', errors='replace')
                logger.info("  TLV %s = %s", tag, value_str)
                response_debug.append(tag)

        # Log summary
        if response_debug:
            logger.info("TLV Response to %s: sent %s", client_ip, ", ".join(response_debug))

        # Safety check - don't send oversized packets
        if len(response) > 1450:
            logger.warning("TLV response too long (%d bytes), not sending", len(response))
            return

        self._send_response(response, addr)
        logger.debug("Sent TLV response to %s:%d", client_ip, client_port)

    def _handle_hello(self, addr: tuple[str, int]) -> None:
        """
        Respond to hello packet from SLIMP3.

        Response is 'h' + 17 null bytes.
        """
        response = b'h' + (b'\x00' * 17)
        self._send_response(response, addr)
        logger.debug("Sent hello response to %s:%d", addr[0], addr[1])

    def _parse_tlvs(self, data: bytes) -> dict[str, bytes | None]:
        """
        Parse TLV entries from data.

        Returns dict of tag -> value (or None if no value).
        """
        tlvs: dict[str, bytes | None] = {}
        offset = 0

        while offset + 5 <= len(data):
            tag = data[offset:offset + 4].decode('ascii', errors='replace')
            length = data[offset + 4]

            if length > 0 and offset + 5 + length <= len(data):
                value = data[offset + 5:offset + 5 + length]
            else:
                value = None

            tlvs[tag] = value
            offset += 5 + length

        return tlvs

    def _get_local_ip_for_client(self, client_ip: str) -> str | None:
        """
        Determine which local IP address can reach the given client.

        This creates a temporary UDP socket and "connects" it to the client
        (no actual packets sent), then checks which local IP was chosen.
        """
        # Use cached value if we have one
        if self._local_ip:
            return self._local_ip

        try:
            # Create a temporary UDP socket to determine routing
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as temp_sock:
                # Connect to client (doesn't send anything, just sets up routing)
                temp_sock.connect((client_ip, 80))
                local_ip = temp_sock.getsockname()[0]
                if local_ip and local_ip != '0.0.0.0':
                    logger.debug("Detected local IP %s for client %s", local_ip, client_ip)
                    return local_ip
        except Exception as e:
            logger.debug("Could not determine local IP for %s: %s", client_ip, e)

        return None

    def _get_tlv_value(self, tag: str, request_value: bytes | None, local_ip: str | None = None) -> bytes | None:
        """
        Get the response value for a TLV tag.

        Args:
            tag: 4-character TLV tag.
            request_value: Value from request (if any).
            local_ip: Local IP address to use for IPAD response.

        Returns:
            Response value as bytes, or None if no response for this tag.
        """
        if tag == 'NAME':
            # Full server name (no truncation)
            return self.server_name.encode('utf-8')

        elif tag == 'IPAD':
            # IP address as string
            if local_ip:
                return local_ip.encode('ascii')
            if self._local_ip:
                return self._local_ip.encode('ascii')
            return None

        elif tag == 'JSON':
            # JSON-RPC port as string
            return str(self.http_port).encode('ascii')

        elif tag == 'VERS':
            # Server version
            return self.version.encode('ascii')

        elif tag == 'UUID':
            # Server UUID
            return self.server_uuid.encode('ascii')

        elif tag == 'JVID':
            # Jive device ID - just log it, no response
            if request_value:
                mac = ':'.join(f'{b:02x}' for b in request_value)
                logger.info("Jive device: %s", mac)
            return None

        else:
            logger.debug("Unknown TLV tag: %s", tag)
            return None

    def _get_padded_hostname(self) -> bytes:
        """
        Get hostname padded/truncated to exactly 17 bytes.

        The hostname needs to be in ISO-8859-1 encoding to support
        the ip3k firmware font on older players.
        """
        # Encode to ISO-8859-1 (Latin-1) for player compatibility
        try:
            hostname = self.server_name.encode('iso-8859-1', errors='replace')
        except Exception:
            hostname = self.server_name.encode('ascii', errors='replace')

        # Truncate to 16 characters
        hostname = hostname[:MAX_HOSTNAME_LENGTH]

        # Pad to 17 bytes with nulls
        hostname = hostname + (b'\x00' * (17 - len(hostname)))

        return hostname

    def _send_response(self, data: bytes, addr: tuple[str, int]) -> None:
        """Send a UDP response packet."""
        if self.transport:
            self.transport.sendto(data, addr)

    def error_received(self, exc: Exception) -> None:
        """Handle UDP socket errors."""
        logger.warning("UDP Discovery error: %s", exc)

    def connection_lost(self, exc: Exception | None) -> None:
        """Handle socket close."""
        if exc:
            logger.warning("UDP Discovery connection lost: %s", exc)
        else:
            logger.info("UDP Discovery stopped")


class UDPDiscoveryServer:
    """
    High-level UDP Discovery server for Resonance.

    This class manages the lifecycle of the UDP discovery protocol
    and provides a simple start/stop interface.
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = DISCOVERY_PORT,
        server_name: str = "Resonance",
        http_port: int = 9000,
        server_uuid: str | None = None,
        version: str = "7.999.999",
    ) -> None:
        """
        Initialize the UDP Discovery server.

        Args:
            host: Host address to bind to.
            port: UDP port to listen on (default 3483).
            server_name: Server name to advertise.
            http_port: HTTP/JSON-RPC port.
            server_uuid: Unique server identifier.
            version: Server version string.
        """
        self.host = host
        self.port = port
        self.server_name = server_name
        self.http_port = http_port
        self.server_uuid = server_uuid
        self.version = version

        self._transport: asyncio.DatagramTransport | None = None
        self._protocol: UDPDiscoveryProtocol | None = None
        self._running = False

    async def start(self) -> None:
        """Start the UDP Discovery server."""
        if self._running:
            logger.warning("UDP Discovery server already running")
            return

        loop = asyncio.get_running_loop()

        # Create UDP endpoint with broadcast support
        try:
            self._transport, self._protocol = await loop.create_datagram_endpoint(
                lambda: UDPDiscoveryProtocol(
                    server_name=self.server_name,
                    http_port=self.http_port,
                    server_uuid=self.server_uuid,
                    version=self.version,
                ),
                local_addr=(self.host, self.port),
                allow_broadcast=True,
                reuse_port=hasattr(socket, 'SO_REUSEPORT'),  # Not available on Windows
            )

            # Enable broadcast on the socket
            sock = self._transport.get_extra_info('socket')
            if sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                # Also allow address reuse
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

            self._running = True
            logger.info(
                "UDP Discovery server started on %s:%d (advertising '%s')",
                self.host,
                self.port,
                self.server_name,
            )

        except OSError as e:
            if e.errno == 10048 or 'already in use' in str(e).lower():
                # Port already in use - likely Slimproto TCP is using it
                # On some systems UDP and TCP can share the same port, on others not
                logger.warning(
                    "UDP Discovery port %d already in use - discovery may be handled by Slimproto",
                    self.port,
                )
            else:
                logger.error("Failed to start UDP Discovery: %s", e)
                raise

    async def stop(self) -> None:
        """Stop the UDP Discovery server."""
        if not self._running:
            return

        self._running = False

        if self._transport:
            self._transport.close()
            self._transport = None
            self._protocol = None

        logger.info("UDP Discovery server stopped")

    @property
    def is_running(self) -> bool:
        """Check if the server is running."""
        return self._running

    def set_local_ip(self, ip: str) -> None:
        """
        Set the local IP address to advertise.

        This can be used to override the auto-detected IP.
        """
        if self._protocol:
            self._protocol._local_ip = ip
