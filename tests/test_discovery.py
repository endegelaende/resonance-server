"""
Tests for UDP Discovery Protocol.

Tests the discovery functionality for Squeezebox players to find the server.
"""

import asyncio
import struct
from unittest.mock import MagicMock, patch

import pytest

from resonance.protocol.discovery import (
    DISCOVERY_PORT,
    MAX_HOSTNAME_LENGTH,
    UDPDiscoveryProtocol,
    UDPDiscoveryServer,
)


class TestUDPDiscoveryProtocol:
    """Tests for UDPDiscoveryProtocol class."""

    def test_creation(self) -> None:
        """Protocol can be created with default values."""
        protocol = UDPDiscoveryProtocol(
            server_name="Test Server",
            http_port=9000,
        )
        assert protocol.server_name == "Test Server"
        assert protocol.http_port == 9000
        assert protocol.version == "7.999.999"  # Required for firmware compatibility
        assert protocol.transport is None

    def test_creation_with_all_params(self) -> None:
        """Protocol can be created with all parameters."""
        protocol = UDPDiscoveryProtocol(
            server_name="My Server",
            http_port=8080,
            server_uuid="test-uuid-123",
            version="1.2.3",
        )
        assert protocol.server_name == "My Server"
        assert protocol.http_port == 8080
        assert protocol.server_uuid == "test-uuid-123"
        assert protocol.version == "1.2.3"

    def test_padded_hostname_short(self) -> None:
        """Short hostnames are padded to 17 bytes."""
        protocol = UDPDiscoveryProtocol(
            server_name="Test",
            http_port=9000,
        )
        hostname = protocol._get_padded_hostname()
        assert len(hostname) == 17
        assert hostname[:4] == b"Test"
        assert hostname[4:] == b"\x00" * 13

    def test_padded_hostname_exact(self) -> None:
        """16-char hostnames get one null byte."""
        protocol = UDPDiscoveryProtocol(
            server_name="1234567890123456",  # 16 chars
            http_port=9000,
        )
        hostname = protocol._get_padded_hostname()
        assert len(hostname) == 17
        assert hostname[:16] == b"1234567890123456"
        assert hostname[16:] == b"\x00"

    def test_padded_hostname_long(self) -> None:
        """Long hostnames are truncated to 16 chars."""
        protocol = UDPDiscoveryProtocol(
            server_name="This is a very long server name that should be truncated",
            http_port=9000,
        )
        hostname = protocol._get_padded_hostname()
        assert len(hostname) == 17
        assert hostname[:16] == b"This is a very l"

    def test_parse_tlvs_empty(self) -> None:
        """Empty data returns empty dict."""
        protocol = UDPDiscoveryProtocol(
            server_name="Test",
            http_port=9000,
        )
        tlvs = protocol._parse_tlvs(b"")
        assert tlvs == {}

    def test_parse_tlvs_single(self) -> None:
        """Single TLV is parsed correctly."""
        protocol = UDPDiscoveryProtocol(
            server_name="Test",
            http_port=9000,
        )
        # NAME with empty value (length 0)
        data = b"NAME\x00"
        tlvs = protocol._parse_tlvs(data)
        assert "NAME" in tlvs
        assert tlvs["NAME"] is None

    def test_parse_tlvs_with_value(self) -> None:
        """TLV with value is parsed correctly."""
        protocol = UDPDiscoveryProtocol(
            server_name="Test",
            http_port=9000,
        )
        # IPAD with value "test"
        data = b"IPAD\x04test"
        tlvs = protocol._parse_tlvs(data)
        assert "IPAD" in tlvs
        assert tlvs["IPAD"] == b"test"

    def test_parse_tlvs_multiple(self) -> None:
        """Multiple TLVs are parsed correctly."""
        protocol = UDPDiscoveryProtocol(
            server_name="Test",
            http_port=9000,
        )
        # NAME (len 0) + JSON (len 4) "9000"
        data = b"NAME\x00JSON\x049000"
        tlvs = protocol._parse_tlvs(data)
        assert "NAME" in tlvs
        assert "JSON" in tlvs
        assert tlvs["JSON"] == b"9000"

    def test_get_tlv_value_name(self) -> None:
        """NAME TLV returns server name."""
        protocol = UDPDiscoveryProtocol(
            server_name="My Server",
            http_port=9000,
        )
        value = protocol._get_tlv_value("NAME", None)
        assert value == b"My Server"

    def test_get_tlv_value_json(self) -> None:
        """JSON TLV returns HTTP port."""
        protocol = UDPDiscoveryProtocol(
            server_name="Test",
            http_port=8080,
        )
        value = protocol._get_tlv_value("JSON", None)
        assert value == b"8080"

    def test_get_tlv_value_vers(self) -> None:
        """VERS TLV returns version."""
        protocol = UDPDiscoveryProtocol(
            server_name="Test",
            http_port=9000,
            version="1.2.3",
        )
        value = protocol._get_tlv_value("VERS", None)
        assert value == b"1.2.3"

    def test_get_tlv_value_uuid(self) -> None:
        """UUID TLV returns server UUID."""
        protocol = UDPDiscoveryProtocol(
            server_name="Test",
            http_port=9000,
            server_uuid="test-uuid",
        )
        value = protocol._get_tlv_value("UUID", None)
        assert value == b"test-uuid"

    def test_get_tlv_value_ipad_no_ip(self) -> None:
        """IPAD TLV returns None if no IP set."""
        protocol = UDPDiscoveryProtocol(
            server_name="Test",
            http_port=9000,
        )
        value = protocol._get_tlv_value("IPAD", None)
        assert value is None

    def test_get_tlv_value_ipad_with_ip(self) -> None:
        """IPAD TLV returns IP if set."""
        protocol = UDPDiscoveryProtocol(
            server_name="Test",
            http_port=9000,
        )
        protocol._local_ip = "192.168.1.100"
        value = protocol._get_tlv_value("IPAD", None)
        assert value == b"192.168.1.100"

    def test_get_tlv_value_jvid(self) -> None:
        """JVID TLV returns None (info only)."""
        protocol = UDPDiscoveryProtocol(
            server_name="Test",
            http_port=9000,
        )
        # JVID is info-only, should return None
        value = protocol._get_tlv_value("JVID", b"\x00\x11\x22\x33\x44\x55")
        assert value is None

    def test_get_tlv_value_unknown(self) -> None:
        """Unknown TLV tag returns None."""
        protocol = UDPDiscoveryProtocol(
            server_name="Test",
            http_port=9000,
        )
        value = protocol._get_tlv_value("XXXX", None)
        assert value is None


class TestUDPDiscoveryProtocolDatagrams:
    """Tests for datagram handling in UDPDiscoveryProtocol."""

    def test_connection_made(self) -> None:
        """connection_made stores the transport."""
        protocol = UDPDiscoveryProtocol(
            server_name="Test",
            http_port=9000,
        )
        transport = MagicMock()
        protocol.connection_made(transport)
        assert protocol.transport == transport

    def test_old_discovery_request(self) -> None:
        """Old-style 'd' discovery request is handled."""
        protocol = UDPDiscoveryProtocol(
            server_name="Test",
            http_port=9000,
        )
        transport = MagicMock()
        protocol.connection_made(transport)

        # Build discovery packet: 'd' + device_id + revision + padding + MAC
        packet = b'd' + b'\x04' + b'\x21'  # device=4 (sb2), revision=2.1
        packet += b'\x00' * 9  # padding
        packet += b'\xaa\xbb\xcc\xdd\xee\xff'  # MAC

        protocol.datagram_received(packet, ("192.168.1.10", 12345))

        # Should send 'D' response
        transport.sendto.assert_called_once()
        response, addr = transport.sendto.call_args[0]
        assert response[0:1] == b'D'
        assert len(response) == 18  # 'D' + 17 bytes hostname
        assert addr == ("192.168.1.10", 12345)

    def test_tlv_discovery_request(self) -> None:
        """TLV-style 'e' discovery request is handled."""
        protocol = UDPDiscoveryProtocol(
            server_name="TestServer",
            http_port=9000,
            version="1.0.0",
        )
        transport = MagicMock()
        protocol.connection_made(transport)

        # Build TLV request: 'e' + NAME(len=0) + VERS(len=0)
        packet = b'eNAME\x00VERS\x00'

        protocol.datagram_received(packet, ("192.168.1.10", 12345))

        # Should send 'E' response with NAME and VERS
        transport.sendto.assert_called_once()
        response, addr = transport.sendto.call_args[0]
        assert response[0:1] == b'E'
        assert b'NAME' in response
        assert b'TestServer' in response
        assert b'VERS' in response
        assert b'1.0.0' in response

    def test_hello_request(self) -> None:
        """Hello 'h' packet (not followed by 0x00 0x00) is handled."""
        protocol = UDPDiscoveryProtocol(
            server_name="Test",
            http_port=9000,
        )
        transport = MagicMock()
        protocol.connection_made(transport)

        # Hello packet that doesn't start with 'h\x00\x00'
        packet = b'hXX'

        protocol.datagram_received(packet, ("192.168.1.10", 12345))

        # Should send 'h' response
        transport.sendto.assert_called_once()
        response, addr = transport.sendto.call_args[0]
        assert response[0:1] == b'h'
        assert len(response) == 18  # 'h' + 17 null bytes

    def test_unknown_packet_ignored(self) -> None:
        """Unknown packet types are ignored."""
        protocol = UDPDiscoveryProtocol(
            server_name="Test",
            http_port=9000,
        )
        transport = MagicMock()
        protocol.connection_made(transport)

        # Unknown packet type
        packet = b'XXXX'

        protocol.datagram_received(packet, ("192.168.1.10", 12345))

        # Should not send any response
        transport.sendto.assert_not_called()

    def test_empty_packet_ignored(self) -> None:
        """Empty packets are ignored."""
        protocol = UDPDiscoveryProtocol(
            server_name="Test",
            http_port=9000,
        )
        transport = MagicMock()
        protocol.connection_made(transport)

        protocol.datagram_received(b"", ("192.168.1.10", 12345))

        transport.sendto.assert_not_called()


class TestUDPDiscoveryServer:
    """Tests for UDPDiscoveryServer class."""

    def test_creation(self) -> None:
        """Server can be created with default values."""
        server = UDPDiscoveryServer()
        assert server.host == "0.0.0.0"
        assert server.port == DISCOVERY_PORT
        assert server.server_name == "Resonance"
        assert server.http_port == 9000
        assert not server.is_running

    def test_creation_with_params(self) -> None:
        """Server can be created with custom parameters."""
        server = UDPDiscoveryServer(
            host="127.0.0.1",
            port=3484,
            server_name="Custom Server",
            http_port=8080,
            server_uuid="custom-uuid",
            version="2.0.0",
        )
        assert server.host == "127.0.0.1"
        assert server.port == 3484
        assert server.server_name == "Custom Server"
        assert server.http_port == 8080
        assert server.server_uuid == "custom-uuid"
        assert server.version == "2.0.0"

    @pytest.mark.asyncio
    async def test_start_stop(self) -> None:
        """Server can start and stop."""
        # Use a different port to avoid conflicts
        server = UDPDiscoveryServer(
            port=13483,  # Non-standard port for testing
            server_name="Test",
        )

        try:
            await server.start()
            assert server.is_running

            await server.stop()
            assert not server.is_running
        except OSError:
            # Port might be in use, skip test
            pytest.skip("Port 13483 not available for testing")

    @pytest.mark.asyncio
    async def test_double_start_safe(self) -> None:
        """Starting twice is safe."""
        server = UDPDiscoveryServer(
            port=13484,
            server_name="Test",
        )

        try:
            await server.start()
            await server.start()  # Should not raise
            assert server.is_running
        except OSError:
            pytest.skip("Port not available")
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_double_stop_safe(self) -> None:
        """Stopping twice is safe."""
        server = UDPDiscoveryServer(
            port=13485,
            server_name="Test",
        )

        try:
            await server.start()
            await server.stop()
            await server.stop()  # Should not raise
            assert not server.is_running
        except OSError:
            pytest.skip("Port not available")

    def test_set_local_ip(self) -> None:
        """Local IP can be set."""
        server = UDPDiscoveryServer(server_name="Test")

        # Before start, protocol is None
        server.set_local_ip("192.168.1.100")  # Should not raise

        # Mock the protocol
        server._protocol = MagicMock()
        server.set_local_ip("192.168.1.200")
        assert server._protocol._local_ip == "192.168.1.200"


class TestDiscoveryConstants:
    """Tests for module constants."""

    def test_discovery_port(self) -> None:
        """Discovery port is the standard Slim protocol port."""
        assert DISCOVERY_PORT == 3483

    def test_max_hostname_length(self) -> None:
        """Max hostname length is 16 characters."""
        assert MAX_HOSTNAME_LENGTH == 16
