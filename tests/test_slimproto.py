"""
Tests for the Slimproto protocol server.

These tests verify the core functionality of the Slimproto server including
connection handling, HELO parsing, and message dispatch.
"""

import asyncio
import struct
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from resonance.player.client import DeviceType, PlayerClient, PlayerState
from resonance.player.registry import PlayerRegistry
from resonance.protocol.slimproto import (
    DEVICE_IDS,
    ProtocolError,
    SlimprotoServer,
)

# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------


@pytest.fixture
def player_registry() -> PlayerRegistry:
    """Create a fresh player registry for testing."""
    return PlayerRegistry()


@pytest.fixture
def slimproto_server(player_registry: PlayerRegistry) -> SlimprotoServer:
    """Create a Slimproto server instance for testing."""
    return SlimprotoServer(
        host="127.0.0.1",
        port=0,  # Let OS assign port
        player_registry=player_registry,
    )


@pytest.fixture
def mock_reader() -> AsyncMock:
    """Create a mock StreamReader."""
    reader = AsyncMock(spec=asyncio.StreamReader)
    return reader


@pytest.fixture
def mock_writer() -> MagicMock:
    """Create a mock StreamWriter."""
    writer = MagicMock(spec=asyncio.StreamWriter)
    writer.get_extra_info = MagicMock(return_value=("192.168.1.100", 54321))
    writer.write = MagicMock()
    writer.drain = AsyncMock()
    writer.close = MagicMock()
    writer.wait_closed = AsyncMock()
    return writer


# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------


def build_helo_message(
    device_id: int = 12,  # squeezeplay
    revision: int = 1,
    mac: bytes = b"\x00\x04\x20\x12\x34\x56",
    uuid: bytes = b"",
    capabilities: str = "",
) -> bytes:
    """Build a HELO message payload for testing."""
    payload = bytes([device_id, revision]) + mac

    if uuid:
        payload += uuid

    # Add padding to reach minimum length
    while len(payload) < 20:
        payload += b"\x00"

    if capabilities:
        # Ensure we're at capabilities offset
        while len(payload) < 36:
            payload += b"\x00"
        payload += capabilities.encode("utf-8")

    return payload


def build_stat_message(
    event_code: str = "STMt",
    buffer_fullness: int = 0,
    output_buffer_fullness: int = 0,
    elapsed_seconds: int = 0,
    elapsed_ms: int = 0,
) -> bytes:
    """Build a STAT message payload for testing.

    Format (per slimproto.py):
        [0-3]   Event code (4 bytes)
        [4]     CRLF count (1 byte)
        [5]     MAS initialized (1 byte)
        [6]     MAS mode (1 byte)
        [7-10]  Buffer size (4 bytes)
        [11-14] Data in buffer / buffer_fullness (4 bytes)
        [15-22] Bytes received (8 bytes)
        [23-24] Signal strength (2 bytes)
        [25-28] Jiffies (4 bytes)
        [29-32] Output buffer size (4 bytes)
        [33-36] Output buffer fullness (4 bytes)
        [37-40] Elapsed seconds (4 bytes)
        [41-42] Voltage (2 bytes)
        [43-46] Elapsed milliseconds (4 bytes)
    """
    payload = event_code.encode("ascii")  # bytes 0-3
    payload += b"\x00" * 3  # bytes 4-6: CRLF, MAS init, MAS mode
    payload += struct.pack(">I", 0)  # bytes 7-10: buffer size
    payload += struct.pack(">I", buffer_fullness)  # bytes 11-14
    payload += struct.pack(">Q", 0)  # bytes 15-22: bytes received
    payload += struct.pack(">H", 50)  # bytes 23-24: signal strength
    payload += struct.pack(">I", 0)  # bytes 25-28: jiffies
    payload += struct.pack(">I", 0)  # bytes 29-32: output buffer size
    payload += struct.pack(">I", output_buffer_fullness)  # bytes 33-36: output buffer fullness
    payload += struct.pack(">I", elapsed_seconds)  # bytes 37-40: elapsed seconds
    payload += struct.pack(">H", 0)  # bytes 41-42: voltage
    payload += struct.pack(">I", elapsed_ms)  # bytes 43-46: elapsed ms

    return payload


def build_slimproto_message(command: str, payload: bytes) -> bytes:
    """Build a complete Slimproto message with header."""
    header = command.encode("ascii") + struct.pack(">I", len(payload))
    return header + payload

def build_ir_payload(
    ir_time: int,
    ir_code_hex: str,
    code_format: int = 0,
    bit_count: int = 32,
) -> bytes:
    """Build an IR message payload for testing."""
    return struct.pack(">IBBI", ir_time, code_format, bit_count, int(ir_code_hex, 16))


def build_butn_payload(
    button_time: int,
    button_code_hex: str,
) -> bytes:
    """Build a BUTN message payload for testing."""
    return struct.pack(">II", button_time, int(button_code_hex, 16))



# -----------------------------------------------------------------------------
# Server Lifecycle Tests
# -----------------------------------------------------------------------------


class TestSlimprotoServerLifecycle:
    """Tests for server start/stop lifecycle."""

    async def test_server_starts_and_stops(
        self,
        slimproto_server: SlimprotoServer,
    ) -> None:
        """Server should start and stop cleanly."""
        assert not slimproto_server.is_running

        await slimproto_server.start()
        assert slimproto_server.is_running

        await slimproto_server.stop()
        assert not slimproto_server.is_running

    async def test_server_can_restart(
        self,
        slimproto_server: SlimprotoServer,
    ) -> None:
        """Server should be able to restart after stopping."""
        await slimproto_server.start()
        await slimproto_server.stop()

        await slimproto_server.start()
        assert slimproto_server.is_running

        await slimproto_server.stop()

    async def test_double_start_is_safe(
        self,
        slimproto_server: SlimprotoServer,
    ) -> None:
        """Starting an already running server should be safe."""
        await slimproto_server.start()
        await slimproto_server.start()  # Should not raise
        assert slimproto_server.is_running

        await slimproto_server.stop()

    async def test_double_stop_is_safe(
        self,
        slimproto_server: SlimprotoServer,
    ) -> None:
        """Stopping an already stopped server should be safe."""
        await slimproto_server.start()
        await slimproto_server.stop()
        await slimproto_server.stop()  # Should not raise


# -----------------------------------------------------------------------------
# HELO Parsing Tests
# -----------------------------------------------------------------------------


class TestHeloParsing:
    """Tests for HELO message parsing."""

    def test_parse_basic_helo(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """Should parse basic HELO with MAC address."""
        client = PlayerClient(mock_reader, mock_writer)
        payload = build_helo_message(
            device_id=12,
            revision=42,
            mac=b"\xaa\xbb\xcc\xdd\xee\xff",
        )

        slimproto_server._parse_helo(client, payload)

        assert client.id == "aa:bb:cc:dd:ee:ff"
        assert client.info.mac_address == "aa:bb:cc:dd:ee:ff"
        assert client.info.device_type == DeviceType.SQUEEZEPLAY
        assert client.info.firmware_version == "42"
        assert client.status.state == PlayerState.CONNECTED

    def test_parse_helo_with_uuid(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """Should parse HELO with UUID (36+ bytes)."""
        client = PlayerClient(mock_reader, mock_writer)

        # Build HELO with UUID
        device_id = 12
        revision = 1
        mac = b"\x00\x04\x20\x12\x34\x56"
        uuid = b"\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10"

        payload = bytes([device_id, revision]) + mac + uuid
        # Pad to 36 bytes
        while len(payload) < 36:
            payload += b"\x00"

        slimproto_server._parse_helo(client, payload)

        assert client.id == "00:04:20:12:34:56"
        assert client.info.uuid == "0102030405060708090a0b0c0d0e0f10"

    def test_parse_helo_with_capabilities(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """Should parse HELO with capabilities string."""
        client = PlayerClient(mock_reader, mock_writer)
        payload = build_helo_message(
            device_id=12,
            capabilities="Name=Living Room,Model=squeezelite,MaxSampleRate=192000",
        )

        slimproto_server._parse_helo(client, payload)

        assert client.info.name == "Living Room"
        assert "Model" in client.info.capabilities
        assert client.info.capabilities["Model"] == "squeezelite"
        assert client.info.capabilities["MaxSampleRate"] == "192000"

    def test_parse_helo_all_device_types(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """Should correctly identify all known device types."""
        for device_id, device_name in DEVICE_IDS.items():
            client = PlayerClient(mock_reader, mock_writer)
            payload = build_helo_message(device_id=device_id)

            slimproto_server._parse_helo(client, payload)

            assert client.info.model == device_name

    def test_parse_helo_unknown_device(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """Should handle unknown device IDs gracefully."""
        client = PlayerClient(mock_reader, mock_writer)
        payload = build_helo_message(device_id=99)  # Unknown

        slimproto_server._parse_helo(client, payload)

        assert client.info.device_type == DeviceType.UNKNOWN
        assert "unknown" in client.info.model

    def test_parse_helo_too_short(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """Should raise error for too-short HELO."""
        client = PlayerClient(mock_reader, mock_writer)
        payload = b"\x00\x01\x02"  # Only 3 bytes

        with pytest.raises(ProtocolError, match="too short"):
            slimproto_server._parse_helo(client, payload)


# -----------------------------------------------------------------------------
# STAT Handling Tests
# -----------------------------------------------------------------------------


class TestStatHandling:
    """Tests for STAT message handling."""

    async def test_handle_stat_updates_status(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """STAT should update client status fields."""
        client = PlayerClient(mock_reader, mock_writer)
        client._id = "00:04:20:12:34:56"

        payload = build_stat_message(
            event_code="STMt",
            buffer_fullness=8192,
            output_buffer_fullness=4096,
            elapsed_seconds=120,
            elapsed_ms=120500,
        )

        await slimproto_server._handle_stat(client, payload)

        assert client.status.buffer_fullness == 8192
        assert client.status.output_buffer_fullness == 4096
        assert client.status.elapsed_seconds == 120

    async def test_handle_stat_playing_state(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """STAT with STMr should set playing state."""
        client = PlayerClient(mock_reader, mock_writer)
        client._id = "00:04:20:12:34:56"

        payload = build_stat_message(event_code="STMr")
        await slimproto_server._handle_stat(client, payload)

        assert client.status.state == PlayerState.PLAYING

    async def test_handle_stat_paused_state(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """STAT with STMp should set paused state."""
        client = PlayerClient(mock_reader, mock_writer)
        client._id = "00:04:20:12:34:56"

        payload = build_stat_message(event_code="STMp")
        await slimproto_server._handle_stat(client, payload)

        assert client.status.state == PlayerState.PAUSED

    async def test_handle_stat_playing_state_on_stms(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """STAT with STMs (track Started) should set PLAYING state (LMS-conformant)."""
        client = PlayerClient(mock_reader, mock_writer)
        client._id = "00:04:20:12:34:56"

        payload = build_stat_message(event_code="STMs")
        await slimproto_server._handle_stat(client, payload)

        assert client.status.state == PlayerState.PLAYING

    async def test_handle_stat_stmt_does_not_promote_on_input_buffer_only(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """STMt must not force PLAYING when only input buffer grows (out=0, elapsed=0)."""
        client = PlayerClient(mock_reader, mock_writer)
        client._id = "00:04:20:12:34:56"
        client.status.state = PlayerState.STOPPED

        payload = build_stat_message(
            event_code="STMt",
            buffer_fullness=16384,
            output_buffer_fullness=0,
            elapsed_seconds=0,
            elapsed_ms=0,
        )
        await slimproto_server._handle_stat(client, payload)

        assert client.status.state == PlayerState.STOPPED

    async def test_handle_stat_stmt_promotes_on_output_progress(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """STMt should promote to PLAYING once output decoding/playback has progressed
        AND playback was confirmed (STMs received for current generation)."""
        client = PlayerClient(mock_reader, mock_writer)
        client._id = "00:04:20:12:34:56"
        client.status.state = PlayerState.STOPPED

        # The stricter STMt promotion guard requires:
        # 1. A streaming_server with a known generation
        # 2. _stms_confirmed_generation matching that generation
        fake_ss = _FakeStreamingServer(generation=3)
        slimproto_server.streaming_server = fake_ss
        slimproto_server._stms_confirmed_generation["00:04:20:12:34:56"] = 3

        payload = build_stat_message(
            event_code="STMt",
            buffer_fullness=16384,
            output_buffer_fullness=512,
            elapsed_seconds=0,
            elapsed_ms=0,
        )
        await slimproto_server._handle_stat(client, payload)

        assert client.status.state == PlayerState.PLAYING

    async def test_handle_stat_stmt_no_promote_without_confirmed_playback(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """STMt must NOT promote to PLAYING when playback was never confirmed
        (STMs never received) — prevents false PLAYING on broken streams."""
        client = PlayerClient(mock_reader, mock_writer)
        client._id = "00:04:20:12:34:56"
        client.status.state = PlayerState.STOPPED

        fake_ss = _FakeStreamingServer(generation=3)
        slimproto_server.streaming_server = fake_ss
        # Deliberately NOT setting _stms_confirmed_generation

        payload = build_stat_message(
            event_code="STMt",
            buffer_fullness=16384,
            output_buffer_fullness=512,
            elapsed_seconds=9,  # bogus elapsed from firmware
            elapsed_ms=0,
        )
        await slimproto_server._handle_stat(client, payload)

        # State must remain STOPPED — broken stream should not be promoted
        assert client.status.state == PlayerState.STOPPED

    async def test_handle_stat_short_payload(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """Should handle too-short STAT gracefully."""
        client = PlayerClient(mock_reader, mock_writer)
        client._id = "00:04:20:12:34:56"

        # Only 10 bytes, minimum is 36
        payload = b"STMt" + b"\x00" * 6

        # Should not raise, just log warning
        await slimproto_server._handle_stat(client, payload)


# -----------------------------------------------------------------------------
# BYE Handling Tests
# -----------------------------------------------------------------------------


class TestByeHandling:
    """Tests for BYE! message handling."""

    async def test_handle_bye_disconnects_player(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """BYE! should set player state to disconnected."""
        client = PlayerClient(mock_reader, mock_writer)
        client._id = "00:04:20:12:34:56"
        client.status.state = PlayerState.PLAYING

        await slimproto_server._handle_bye(client, b"")

        assert client.status.state == PlayerState.DISCONNECTED


# -----------------------------------------------------------------------------
# Message Reading Tests
# -----------------------------------------------------------------------------


class TestMessageReading:
    """Tests for reading Slimproto messages."""

    async def test_read_message_success(
        self,
        slimproto_server: SlimprotoServer,
    ) -> None:
        """Should correctly read a valid message."""
        reader = AsyncMock()
        payload = b"test payload data"
        message = build_slimproto_message("TEST", payload)

        # Mock readexactly to return header then payload
        reader.readexactly = AsyncMock(
            side_effect=[
                message[:8],  # Header
                payload,  # Payload
            ]
        )

        command, data = await slimproto_server._read_message(reader)

        assert command == "TEST"
        assert data == payload

    async def test_read_message_empty_payload(
        self,
        slimproto_server: SlimprotoServer,
    ) -> None:
        """Should handle messages with zero-length payload."""
        reader = AsyncMock()
        header = b"BYE!" + struct.pack(">I", 0)
        reader.readexactly = AsyncMock(return_value=header)

        command, data = await slimproto_server._read_message(reader)

        assert command == "BYE!"
        assert data == b""

    async def test_read_message_too_large(
        self,
        slimproto_server: SlimprotoServer,
    ) -> None:
        """Should reject messages larger than max size."""
        reader = AsyncMock()
        # Claim a payload of 1MB (exceeds 64KB limit)
        header = b"TEST" + struct.pack(">I", 1024 * 1024)
        reader.readexactly = AsyncMock(return_value=header)

        with pytest.raises(ProtocolError, match="too large"):
            await slimproto_server._read_message(reader)


# -----------------------------------------------------------------------------
# Capabilities Parsing Tests
# -----------------------------------------------------------------------------


class TestCapabilitiesParsing:
    """Tests for capabilities string parsing."""

    def test_parse_simple_capabilities(
        self,
        slimproto_server: SlimprotoServer,
    ) -> None:
        """Should parse key=value pairs."""
        caps = slimproto_server._parse_capabilities("Name=Test,Model=squeezelite")

        assert caps["Name"] == "Test"
        assert caps["Model"] == "squeezelite"

    def test_parse_capabilities_with_flags(
        self,
        slimproto_server: SlimprotoServer,
    ) -> None:
        """Should handle flags without values."""
        caps = slimproto_server._parse_capabilities("HasDisplay,CanSync,Name=Test")

        assert caps["HasDisplay"] == "1"
        assert caps["CanSync"] == "1"
        assert caps["Name"] == "Test"

    def test_parse_empty_capabilities(
        self,
        slimproto_server: SlimprotoServer,
    ) -> None:
        """Should handle empty string."""
        caps = slimproto_server._parse_capabilities("")

        assert caps == {}

    def test_parse_capabilities_with_equals_in_value(
        self,
        slimproto_server: SlimprotoServer,
    ) -> None:
        """Should handle values containing equals signs."""
        caps = slimproto_server._parse_capabilities("Equation=a=b+c")

        assert caps["Equation"] == "a=b+c"





# -----------------------------------------------------------------------------
# IR / BUTN Dispatch Tests
# -----------------------------------------------------------------------------


class TestIrDispatch:
    """Tests for IR and BUTN -> JSON-RPC dispatch mapping."""

    @pytest.mark.parametrize(
        ("ir_code", "expected_command"),
        [
            # Slim Devices Remote — transport
            ("768910ef", ["play"]),
            ("768920df", ["pause"]),
            ("7689a05f", ["playlist", "index", "+1"]),
            ("7689c03f", ["playlist", "index", "-1"]),
            ("7689807f", ["mixer", "volume", "+5"]),
            ("768900ff", ["mixer", "volume", "-5"]),
            ("768940bf", ["power"]),
            ("7689c43b", ["mixer", "muting", "toggle"]),
            # JVC DVD Remote
            ("0000f7d6", ["play"]),
            ("0000f7b2", ["pause"]),
            ("0000f7c2", ["stop"]),
            ("0000f76e", ["playlist", "index", "+1"]),
            ("0000f70e", ["playlist", "index", "-1"]),
            ("0000c078", ["mixer", "volume", "+5"]),
            ("0000c0f8", ["mixer", "volume", "-5"]),
            ("0000f701", ["power", "1"]),
            ("0000f700", ["power", "0"]),
            ("0000c038", ["mixer", "muting", "toggle"]),
            # Front panel
            ("00010012", ["play"]),
            ("00010019", ["mixer", "volume", "+5"]),
            # Boom BUTN
            ("0000f508", ["pause"]),
            # Number keys (Slim Devices Remote)
            ("76899867", ["playlist", "index", "0"]),
            ("7689f00f", ["playlist", "index", "1"]),
            ("768908f7", ["playlist", "index", "2"]),
            ("7689e817", ["playlist", "index", "9"]),
            # Number keys (JVC DVD Remote)
            ("0000f776", ["playlist", "index", "0"]),
            ("0000f786", ["playlist", "index", "1"]),
            # Number keys (Front Panel)
            ("00010000", ["playlist", "index", "0"]),
            ("00010009", ["playlist", "index", "9"]),
            # Shuffle / Repeat
            ("7689d827", ["playlist", "shuffle"]),
            ("768938c7", ["playlist", "repeat"]),
            ("0000f72b", ["playlist", "shuffle"]),
            ("0000f7ab", ["playlist", "repeat"]),
        ],
    )
    async def test_dispatch_ir_maps_known_codes_to_commands(
        self,
        ir_code: str,
        expected_command: list[str],
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """Known IR/BUTN codes should map to LMS-compatible playback commands."""
        client = PlayerClient(mock_reader, mock_writer)
        client._id = "00:04:20:12:34:56"

        jsonrpc_handler = AsyncMock()
        slimproto_server.jsonrpc_handler = jsonrpc_handler

        await slimproto_server._dispatch_ir(client, ir_code, 1000)

        jsonrpc_handler.assert_awaited_once_with(client.mac_address, expected_command)

    async def test_dispatch_ir_repeat_gate_suppresses_fast_repeats(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """Identical non-volume IR code within <300ms should be suppressed (no repeat action)."""
        client = PlayerClient(mock_reader, mock_writer)
        client._id = "00:04:20:12:34:57"

        jsonrpc_handler = AsyncMock()
        slimproto_server.jsonrpc_handler = jsonrpc_handler

        # play has no repeat action → repeats are suppressed
        await slimproto_server._dispatch_ir(client, "768910ef", 1000)  # first press
        await slimproto_server._dispatch_ir(client, "768910ef", 1100)  # suppressed (repeat, no repeat action)
        await slimproto_server._dispatch_ir(client, "768910ef", 1400)  # accepted (>300ms gap → new press)

        assert jsonrpc_handler.await_count == 2
        assert jsonrpc_handler.await_args_list[0].args == (client.mac_address, ["play"])
        assert jsonrpc_handler.await_args_list[1].args == (client.mac_address, ["play"])

    async def test_dispatch_ir_volume_repeat_passes_through_with_fine_step(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """Volume repeats should pass through with finer +2/-2 step (not suppressed)."""
        client = PlayerClient(mock_reader, mock_writer)
        client._id = "00:04:20:ir:vol:01"

        jsonrpc_handler = AsyncMock()
        slimproto_server.jsonrpc_handler = jsonrpc_handler

        # First press → +5, repeats → +2 (finer)
        await slimproto_server._dispatch_ir(client, "7689807f", 1000)  # vol_up first
        await slimproto_server._dispatch_ir(client, "7689807f", 1120)  # repeat
        await slimproto_server._dispatch_ir(client, "7689807f", 1240)  # repeat

        assert jsonrpc_handler.await_count == 3
        assert jsonrpc_handler.await_args_list[0].args == (client.mac_address, ["mixer", "volume", "+5"])
        assert jsonrpc_handler.await_args_list[1].args == (client.mac_address, ["mixer", "volume", "+2"])
        assert jsonrpc_handler.await_args_list[2].args == (client.mac_address, ["mixer", "volume", "+2"])

    async def test_dispatch_ir_volume_down_repeat(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """Volume down repeats should use -2 step."""
        client = PlayerClient(mock_reader, mock_writer)
        client._id = "00:04:20:ir:vol:02"

        jsonrpc_handler = AsyncMock()
        slimproto_server.jsonrpc_handler = jsonrpc_handler

        await slimproto_server._dispatch_ir(client, "768900ff", 2000)  # vol_down first → -5
        await slimproto_server._dispatch_ir(client, "768900ff", 2100)  # repeat → -2

        assert jsonrpc_handler.await_count == 2
        assert jsonrpc_handler.await_args_list[0].args == (client.mac_address, ["mixer", "volume", "-5"])
        assert jsonrpc_handler.await_args_list[1].args == (client.mac_address, ["mixer", "volume", "-2"])

    async def test_dispatch_ir_hold_detected_after_threshold(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """After IR_HOLD_THRESHOLD_MS (900ms) of repeating, hold action should fire."""
        from resonance.protocol.slimproto import IR_HOLD_THRESHOLD_MS

        client = PlayerClient(mock_reader, mock_writer)
        client._id = "00:04:20:ir:hold:01"

        jsonrpc_handler = AsyncMock()
        slimproto_server.jsonrpc_handler = jsonrpc_handler

        # Simulate fwd (playlist_next) button held for >900ms.
        # Real IR remotes repeat at ~120ms intervals, so we need continuous
        # repeats (each < 300ms apart) until we pass the hold threshold.
        t = 1000
        await slimproto_server._dispatch_ir(client, "7689a05f", t)  # first press → index +1
        # Continuous repeats at 120ms intervals: suppressed (no repeat action for fwd)
        for i in range(1, 8):  # 7 repeats × 120ms = 840ms (still < 900ms threshold)
            await slimproto_server._dispatch_ir(client, "7689a05f", t + i * 120)
        # Next repeat at 960ms from first press → crosses hold threshold → time +10
        await slimproto_server._dispatch_ir(client, "7689a05f", t + 960)
        # Continued hold → another hold action
        await slimproto_server._dispatch_ir(client, "7689a05f", t + 1080)

        # First call: playlist index +1 (first press)
        # Then 7 suppressed repeats (no repeat action for fwd)
        # Then 2 hold actions: time +10
        assert jsonrpc_handler.await_count == 3
        assert jsonrpc_handler.await_args_list[0].args == (client.mac_address, ["playlist", "index", "+1"])
        assert jsonrpc_handler.await_args_list[1].args == (client.mac_address, ["time", "+10"])
        assert jsonrpc_handler.await_args_list[2].args == (client.mac_address, ["time", "+10"])

    async def test_dispatch_ir_hold_prev_seeks_backward(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """Holding prev/rew should seek backward (time -10)."""
        client = PlayerClient(mock_reader, mock_writer)
        client._id = "00:04:20:ir:hold:02"

        jsonrpc_handler = AsyncMock()
        slimproto_server.jsonrpc_handler = jsonrpc_handler

        # Simulate rew (playlist_prev) held past threshold with continuous 120ms repeats
        t = 5000
        await slimproto_server._dispatch_ir(client, "7689c03f", t)  # first press → index -1
        for i in range(1, 8):  # 7 repeats to approach threshold (840ms)
            await slimproto_server._dispatch_ir(client, "7689c03f", t + i * 120)
        # Cross hold threshold at 960ms → hold action: time -10
        await slimproto_server._dispatch_ir(client, "7689c03f", t + 960)

        assert jsonrpc_handler.await_count == 2
        assert jsonrpc_handler.await_args_list[0].args == (client.mac_address, ["playlist", "index", "-1"])
        assert jsonrpc_handler.await_args_list[1].args == (client.mac_address, ["time", "-10"])

    async def test_dispatch_ir_hold_pause_fires_stop(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """Holding pause should fire stop (LMS: pause.hold = stop)."""
        client = PlayerClient(mock_reader, mock_writer)
        client._id = "00:04:20:ir:hold:03"

        jsonrpc_handler = AsyncMock()
        slimproto_server.jsonrpc_handler = jsonrpc_handler

        # Simulate pause held with continuous 120ms repeats past threshold
        t = 3000
        await slimproto_server._dispatch_ir(client, "768920df", t)  # pause first press
        for i in range(1, 8):  # 7 repeats (840ms) — suppressed (no repeat action for pause)
            await slimproto_server._dispatch_ir(client, "768920df", t + i * 120)
        # Cross hold threshold at 960ms → hold action: stop
        await slimproto_server._dispatch_ir(client, "768920df", t + 960)

        assert jsonrpc_handler.await_count == 2
        assert jsonrpc_handler.await_args_list[0].args == (client.mac_address, ["pause"])
        assert jsonrpc_handler.await_args_list[1].args == (client.mac_address, ["stop"])

    async def test_dispatch_ir_volume_hold_continues_fine_steps(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """Volume held past threshold should continue with +2 steps (hold action same as repeat)."""
        client = PlayerClient(mock_reader, mock_writer)
        client._id = "00:04:20:ir:hold:04"

        jsonrpc_handler = AsyncMock()
        slimproto_server.jsonrpc_handler = jsonrpc_handler

        # Simulate volume up held with continuous 120ms repeats past threshold
        t = 1000
        await slimproto_server._dispatch_ir(client, "7689807f", t)  # first → +5
        # Repeats before hold → +2 each (volume has repeat action)
        for i in range(1, 8):  # 7 repeats (840ms)
            await slimproto_server._dispatch_ir(client, "7689807f", t + i * 120)
        # Past hold threshold at 960ms → +2 (hold action)
        await slimproto_server._dispatch_ir(client, "7689807f", t + 960)

        # 1 first press (+5) + 7 repeats (+2 each) + 1 hold (+2) = 9 calls
        assert jsonrpc_handler.await_count == 9
        # First press uses primary action (+5)
        assert jsonrpc_handler.await_args_list[0].args == (client.mac_address, ["mixer", "volume", "+5"])
        # All subsequent repeats and holds use +2
        for i in range(1, 9):
            assert jsonrpc_handler.await_args_list[i].args == (client.mac_address, ["mixer", "volume", "+2"])

    async def test_dispatch_ir_different_code_resets_state(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """A different IR code should reset hold state and fire new primary action."""
        client = PlayerClient(mock_reader, mock_writer)
        client._id = "00:04:20:ir:reset:01"

        jsonrpc_handler = AsyncMock()
        slimproto_server.jsonrpc_handler = jsonrpc_handler

        await slimproto_server._dispatch_ir(client, "7689807f", 1000)  # vol_up → +5
        await slimproto_server._dispatch_ir(client, "7689807f", 1100)  # repeat → +2
        await slimproto_server._dispatch_ir(client, "768900ff", 1200)  # vol_down → NEW press → -5

        assert jsonrpc_handler.await_count == 3
        assert jsonrpc_handler.await_args_list[0].args == (client.mac_address, ["mixer", "volume", "+5"])
        assert jsonrpc_handler.await_args_list[1].args == (client.mac_address, ["mixer", "volume", "+2"])
        assert jsonrpc_handler.await_args_list[2].args == (client.mac_address, ["mixer", "volume", "-5"])

    async def test_dispatch_ir_log_only_actions_not_dispatched(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """Arrow keys, presets, and navigation buttons should be logged but not dispatched."""
        client = PlayerClient(mock_reader, mock_writer)
        client._id = "00:04:20:ir:log:01"

        jsonrpc_handler = AsyncMock()
        slimproto_server.jsonrpc_handler = jsonrpc_handler

        # Arrow keys (Slim Devices Remote)
        await slimproto_server._dispatch_ir(client, "7689e01f", 1000)  # arrow_up
        await slimproto_server._dispatch_ir(client, "7689b04f", 2000)  # arrow_down
        await slimproto_server._dispatch_ir(client, "7689906f", 3000)  # arrow_left
        await slimproto_server._dispatch_ir(client, "7689d02f", 4000)  # arrow_right
        # Presets (Slim Devices Remote)
        await slimproto_server._dispatch_ir(client, "76898a75", 5000)  # preset_1
        await slimproto_server._dispatch_ir(client, "76896a95", 6000)  # preset_6
        # Boom presets (Front Panel)
        await slimproto_server._dispatch_ir(client, "00010023", 7000)  # preset_1
        # Navigation
        await slimproto_server._dispatch_ir(client, "768922dd", 8000)  # home
        await slimproto_server._dispatch_ir(client, "768918e7", 9000)  # favorites
        await slimproto_server._dispatch_ir(client, "7689708f", 10000)  # browse

        # None of these should have been dispatched
        jsonrpc_handler.assert_not_awaited()

    async def test_dispatch_ir_unknown_code_ignored(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """Unknown IR codes should be silently ignored."""
        client = PlayerClient(mock_reader, mock_writer)
        client._id = "00:04:20:ir:unk:01"

        jsonrpc_handler = AsyncMock()
        slimproto_server.jsonrpc_handler = jsonrpc_handler

        await slimproto_server._dispatch_ir(client, "deadbeef", 1000)

        jsonrpc_handler.assert_not_awaited()

    async def test_dispatch_ir_timer_wraparound(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """32-bit timer wrap-around should not break repeat detection."""
        client = PlayerClient(mock_reader, mock_writer)
        client._id = "00:04:20:ir:wrap:01"

        jsonrpc_handler = AsyncMock()
        slimproto_server.jsonrpc_handler = jsonrpc_handler

        # Time near 32-bit max, then wraps to small value.
        # 0xFFFFFF00 + 296 ticks wraps to 0x00000027 (39).
        # Delta = 296ms which is < IR_REPEAT_WINDOW_MS (300ms) → repeat.
        t_near_max = 0xFFFFFF00
        t_after_wrap = 39  # (0xFFFFFF00 + 296) & 0xFFFFFFFF = 39; delta = 296 < 300

        await slimproto_server._dispatch_ir(client, "7689807f", t_near_max)  # vol_up → +5
        await slimproto_server._dispatch_ir(client, "7689807f", t_after_wrap)  # repeat → +2

        assert jsonrpc_handler.await_count == 2
        assert jsonrpc_handler.await_args_list[0].args == (client.mac_address, ["mixer", "volume", "+5"])
        assert jsonrpc_handler.await_args_list[1].args == (client.mac_address, ["mixer", "volume", "+2"])

    async def test_dispatch_ir_multi_player_isolation(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """IR state for different players should be independent."""
        client_a = PlayerClient(mock_reader, mock_writer)
        client_a._id = "00:04:20:ir:iso:01"
        client_b = PlayerClient(mock_reader, mock_writer)
        client_b._id = "00:04:20:ir:iso:02"

        jsonrpc_handler = AsyncMock()
        slimproto_server.jsonrpc_handler = jsonrpc_handler

        # Player A presses play
        await slimproto_server._dispatch_ir(client_a, "768910ef", 1000)
        # Player B presses play at the same time — should NOT be suppressed
        await slimproto_server._dispatch_ir(client_b, "768910ef", 1000)

        assert jsonrpc_handler.await_count == 2
        assert jsonrpc_handler.await_args_list[0].args == (client_a.mac_address, ["play"])
        assert jsonrpc_handler.await_args_list[1].args == (client_b.mac_address, ["play"])

    async def test_dispatch_ir_gap_after_repeat_resets_to_new_press(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """After a gap > IR_REPEAT_WINDOW_MS, same code should be treated as new press."""
        from resonance.protocol.slimproto import IR_REPEAT_WINDOW_MS

        client = PlayerClient(mock_reader, mock_writer)
        client._id = "00:04:20:ir:gap:01"

        jsonrpc_handler = AsyncMock()
        slimproto_server.jsonrpc_handler = jsonrpc_handler

        await slimproto_server._dispatch_ir(client, "7689807f", 1000)  # vol_up → +5
        await slimproto_server._dispatch_ir(client, "7689807f", 1100)  # repeat → +2
        # Gap > repeat window
        await slimproto_server._dispatch_ir(client, "7689807f", 1100 + IR_REPEAT_WINDOW_MS + 50)  # new press → +5

        assert jsonrpc_handler.await_count == 3
        assert jsonrpc_handler.await_args_list[0].args == (client.mac_address, ["mixer", "volume", "+5"])
        assert jsonrpc_handler.await_args_list[1].args == (client.mac_address, ["mixer", "volume", "+2"])
        assert jsonrpc_handler.await_args_list[2].args == (client.mac_address, ["mixer", "volume", "+5"])

    async def test_handle_ir_parses_payload_and_dispatches(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """IR payload parsing should feed the mapped code into dispatch."""
        client = PlayerClient(mock_reader, mock_writer)
        client._id = "00:04:20:12:34:58"

        jsonrpc_handler = AsyncMock()
        slimproto_server.jsonrpc_handler = jsonrpc_handler

        payload = build_ir_payload(1234, "768910ef")
        await slimproto_server._handle_ir(client, payload)

        jsonrpc_handler.assert_awaited_once_with(client.mac_address, ["play"])

    async def test_handle_butn_uses_same_dispatch_and_ignores_up_release(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """Front-panel .down BUTN should dispatch; .up release should not dispatch."""
        client = PlayerClient(mock_reader, mock_writer)
        client._id = "00:04:20:12:34:59"

        jsonrpc_handler = AsyncMock()
        slimproto_server.jsonrpc_handler = jsonrpc_handler

        payload_down = build_butn_payload(2000, "00010019")  # volup_front.down
        payload_up = build_butn_payload(2100, "00020019")    # volup_front.up (ignored)

        await slimproto_server._handle_butn(client, payload_down)
        await slimproto_server._handle_butn(client, payload_up)

        jsonrpc_handler.assert_awaited_once_with(
            client.mac_address,
            ["mixer", "volume", "+5"],
        )


# -----------------------------------------------------------------------------
# Player Registry Integration Tests
# -----------------------------------------------------------------------------


class TestPlayerRegistryIntegration:
    """Tests for player registry integration."""

    async def test_player_registered_after_helo(
        self,
        player_registry: PlayerRegistry,
    ) -> None:
        """Player should be registered after successful HELO."""
        server = SlimprotoServer(
            host="127.0.0.1",
            port=0,
            player_registry=player_registry,
        )

        reader = AsyncMock()
        writer = MagicMock()
        writer.get_extra_info = MagicMock(return_value=("192.168.1.100", 54321))
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()

        client = PlayerClient(reader, writer)
        payload = build_helo_message(mac=b"\xaa\xbb\xcc\xdd\xee\xff")
        server._parse_helo(client, payload)

        await player_registry.register(client)

        assert len(player_registry) == 1
        found = await player_registry.get_by_mac("aa:bb:cc:dd:ee:ff")
        assert found is not None
        assert found.id == "aa:bb:cc:dd:ee:ff"

    async def test_send_to_player(
        self,
        player_registry: PlayerRegistry,
    ) -> None:
        """Should be able to send message to registered player."""
        server = SlimprotoServer(
            host="127.0.0.1",
            port=0,
            player_registry=player_registry,
        )

        reader = AsyncMock()
        writer = MagicMock()
        writer.get_extra_info = MagicMock(return_value=("192.168.1.100", 54321))
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()

        client = PlayerClient(reader, writer)
        client._id = "aa:bb:cc:dd:ee:ff"
        client.info.mac_address = "aa:bb:cc:dd:ee:ff"
        client.status.state = PlayerState.CONNECTED

        await player_registry.register(client)

        result = await server.send_to_player("aa:bb:cc:dd:ee:ff", "test", b"payload")

        assert result is True
        writer.write.assert_called()

    async def test_set_display_brightness_sends_grfb(
        self,
        player_registry: PlayerRegistry,
    ) -> None:
        """grfb helper should send brightness payload to the player."""
        server = SlimprotoServer(
            host="127.0.0.1",
            port=0,
            player_registry=player_registry,
        )

        reader = AsyncMock()
        writer = MagicMock()
        writer.get_extra_info = MagicMock(return_value=("192.168.1.100", 54321))
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()

        client = PlayerClient(reader, writer)
        client._id = "aa:bb:cc:dd:ee:ff"
        client.info.mac_address = "aa:bb:cc:dd:ee:ff"
        client.status.state = PlayerState.CONNECTED
        await player_registry.register(client)

        result = await server.set_display_brightness("aa:bb:cc:dd:ee:ff", -1)

        assert result is True
        sent = writer.write.call_args.args[0]
        assert sent[2:6] == b"grfb"
        assert sent[6:] == b"\xff\xff"

    async def test_send_display_bitmap_sends_grfe(
        self,
        player_registry: PlayerRegistry,
    ) -> None:
        """grfe helper should send header + bitmap payload to the player."""
        server = SlimprotoServer(
            host="127.0.0.1",
            port=0,
            player_registry=player_registry,
        )

        reader = AsyncMock()
        writer = MagicMock()
        writer.get_extra_info = MagicMock(return_value=("192.168.1.100", 54321))
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()

        client = PlayerClient(reader, writer)
        client._id = "aa:bb:cc:dd:ee:ff"
        client.info.mac_address = "aa:bb:cc:dd:ee:ff"
        client.status.state = PlayerState.CONNECTED
        await player_registry.register(client)

        result = await server.send_display_bitmap(
            "aa:bb:cc:dd:ee:ff",
            b"\xde\xad\xbe\xef",
            offset=640,
            transition="R",
            param=9,
        )

        assert result is True
        sent = writer.write.call_args.args[0]
        assert sent[2:6] == b"grfe"
        assert struct.unpack(">H", sent[6:8])[0] == 640
        assert sent[8:9] == b"R"
        assert sent[9] == 9
        assert sent[10:] == b"\xde\xad\xbe\xef"

    async def test_clear_display_default_uses_1280_zero_bitmap(
        self,
        player_registry: PlayerRegistry,
    ) -> None:
        """clear_display should send grfe with a 1280-byte zero bitmap by default."""
        server = SlimprotoServer(
            host="127.0.0.1",
            port=0,
            player_registry=player_registry,
        )

        reader = AsyncMock()
        writer = MagicMock()
        writer.get_extra_info = MagicMock(return_value=("192.168.1.100", 54321))
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()

        client = PlayerClient(reader, writer)
        client._id = "aa:bb:cc:dd:ee:ff"
        client.info.mac_address = "aa:bb:cc:dd:ee:ff"
        client.status.state = PlayerState.CONNECTED
        await player_registry.register(client)

        result = await server.clear_display("aa:bb:cc:dd:ee:ff")

        assert result is True
        sent = writer.write.call_args.args[0]
        assert sent[2:6] == b"grfe"
        assert sent[6:10] == struct.pack(">HcB", 0, b"c", 0)
        assert len(sent[10:]) == 1280
        assert set(sent[10:]) == {0}

    async def test_send_display_framebuffer_sends_grfd(
        self,
        player_registry: PlayerRegistry,
    ) -> None:
        """grfd helper should send framebuffer offset + bitmap bytes."""
        server = SlimprotoServer(
            host="127.0.0.1",
            port=0,
            player_registry=player_registry,
        )

        reader = AsyncMock()
        writer = MagicMock()
        writer.get_extra_info = MagicMock(return_value=("192.168.1.100", 54321))
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()

        client = PlayerClient(reader, writer)
        client._id = "aa:bb:cc:dd:ee:ff"
        client.info.mac_address = "aa:bb:cc:dd:ee:ff"
        client.status.state = PlayerState.CONNECTED
        await player_registry.register(client)

        result = await server.send_display_framebuffer(
            "aa:bb:cc:dd:ee:ff",
            b"\xde\xad\xbe\xef",
            offset=560,
        )

        assert result is True
        sent = writer.write.call_args.args[0]
        assert sent[2:6] == b"grfd"
        assert struct.unpack(">H", sent[6:8])[0] == 560
        assert sent[8:] == b"\xde\xad\xbe\xef"

    async def test_clear_display_framebuffer_default_uses_560_zero_bitmap(
        self,
        player_registry: PlayerRegistry,
    ) -> None:
        """clear_display_framebuffer should send grfd with 560 zero bytes by default."""
        server = SlimprotoServer(
            host="127.0.0.1",
            port=0,
            player_registry=player_registry,
        )

        reader = AsyncMock()
        writer = MagicMock()
        writer.get_extra_info = MagicMock(return_value=("192.168.1.100", 54321))
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()

        client = PlayerClient(reader, writer)
        client._id = "aa:bb:cc:dd:ee:ff"
        client.info.mac_address = "aa:bb:cc:dd:ee:ff"
        client.status.state = PlayerState.CONNECTED
        await player_registry.register(client)

        result = await server.clear_display_framebuffer("aa:bb:cc:dd:ee:ff")

        assert result is True
        sent = writer.write.call_args.args[0]
        assert sent[2:6] == b"grfd"
        assert struct.unpack(">H", sent[6:8])[0] == 560
        assert len(sent[8:]) == 560
        assert set(sent[8:]) == {0}

    async def test_send_to_unknown_player(
        self,
        slimproto_server: SlimprotoServer,
    ) -> None:
        """Should return False when sending to unknown player."""
        result = await slimproto_server.send_to_player(
            "ff:ff:ff:ff:ff:ff",
            "test",
            b"",
        )

        assert result is False


# -----------------------------------------------------------------------------
# DSCO Handling Tests
# -----------------------------------------------------------------------------


def _build_dsco_payload(reason: int = 0) -> bytes:
    """Build a DSCO message payload with the given reason code."""
    return bytes([reason])


class _FakeStreamingServer:
    """Minimal streaming-server stub for DSCO / track-end tests."""

    def __init__(
        self,
        generation: int = 5,
        stream_age: float = 0.0,
        track_duration: float | None = None,
    ) -> None:
        self._generation = generation
        self._stream_age = stream_age
        self._track_duration = track_duration

    def get_stream_generation(self, _mac: str) -> int | None:
        return self._generation

    def get_stream_generation_age(self, _mac: str) -> float | None:
        return self._stream_age

    def get_track_duration(self, _mac: str) -> float | None:
        return self._track_duration

    def set_track_duration(self, _mac: str, dur: float) -> None:
        self._track_duration = dur

    def get_start_offset(self, _mac: str) -> float:
        return 0.0


class TestDscoHandling:
    """Tests for DSCO (data stream disconnect) handling."""

    @pytest.mark.asyncio
    async def test_dsco_normal_records_generation(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """DSCO reason=0 should record the current stream generation."""
        client = PlayerClient(mock_reader, mock_writer)
        client._id = "00:04:20:aa:bb:cc"

        fake_ss = _FakeStreamingServer(generation=7)
        slimproto_server.streaming_server = fake_ss

        await slimproto_server._handle_dsco(client, _build_dsco_payload(0))

        assert slimproto_server._dsco_received_generation.get("00:04:20:aa:bb:cc") == 7

    @pytest.mark.asyncio
    async def test_dsco_error_does_not_record_generation(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """DSCO with error reason (!=0) should NOT record a generation."""
        client = PlayerClient(mock_reader, mock_writer)
        client._id = "00:04:20:aa:bb:cc"

        fake_ss = _FakeStreamingServer(generation=3)
        slimproto_server.streaming_server = fake_ss

        for reason in (1, 2, 3, 4):
            await slimproto_server._handle_dsco(client, _build_dsco_payload(reason))

        assert "00:04:20:aa:bb:cc" not in slimproto_server._dsco_received_generation


# -----------------------------------------------------------------------------
# STMt Track-Finished Semantics (LMS parity)
# -----------------------------------------------------------------------------


class TestStatementTrackFinishedSemantics:
    """STMt heartbeats must not synthesize track-finished events."""

    @pytest.mark.asyncio
    async def test_stmt_does_not_fire_track_finished_even_with_dsco(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """LMS advances on STMu, not STMt; STMt must never publish track-finished."""
        from resonance.core.events import PlayerTrackFinishedEvent, event_bus

        player_mac = "00:04:20:aa:bb:01"
        client = PlayerClient(mock_reader, mock_writer)
        client._id = player_mac
        client.status.state = PlayerState.PLAYING

        # Even with DSCO + confirmed playback + long stream age, STMt must not fire.
        fake_ss = _FakeStreamingServer(generation=10, stream_age=999.0, track_duration=180.0)
        slimproto_server.streaming_server = fake_ss
        slimproto_server._dsco_received_generation[player_mac] = 10
        slimproto_server._stms_confirmed_generation[player_mac] = 10

        fired: list[PlayerTrackFinishedEvent] = []

        async def _capture(event):
            if isinstance(event, PlayerTrackFinishedEvent) and event.player_id == player_mac:
                fired.append(event)

        await event_bus.subscribe("player.track_finished", _capture)
        try:
            payload = build_stat_message(event_code="STMt", elapsed_seconds=0, elapsed_ms=0)
            await slimproto_server._handle_stat(client, payload)
            await asyncio.sleep(0)
            assert len(fired) == 0
        finally:
            await event_bus.unsubscribe("player.track_finished", _capture)
# ---------------------------------------------------------------------------
# Elapsed-Time Push (STMt throttled publishing)
# ---------------------------------------------------------------------------


class TestElapsedTimePush:
    """Tests for throttled PlayerStatusEvent publishing on STMt heartbeats.

    During playback, STMt heartbeats arrive every ~1 second.  Resonance
    publishes a PlayerStatusEvent every ELAPSED_PUSH_INTERVAL_SECONDS so
    that Cometd subscription re-execution pushes fresh elapsed time to
    JiveLite / Web-UI / Cadence.
    """

    @pytest.mark.asyncio
    async def test_stmt_publishes_status_event_when_playing(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """First STMt while PLAYING should publish a PlayerStatusEvent."""
        from resonance.core.events import PlayerStatusEvent, event_bus

        player_mac = "00:04:20:e1:e1:01"
        client = PlayerClient(mock_reader, mock_writer)
        client._id = player_mac
        client.status.state = PlayerState.PLAYING

        fired: list[PlayerStatusEvent] = []

        async def _capture(event):
            if isinstance(event, PlayerStatusEvent) and event.player_id == player_mac:
                fired.append(event)

        await event_bus.subscribe("player.status", _capture)
        try:
            # Ensure no prior push timestamp exists
            slimproto_server._last_elapsed_push.pop(player_mac, None)

            payload = build_stat_message(
                event_code="STMt",
                elapsed_seconds=42,
                elapsed_ms=42500,
            )
            await slimproto_server._handle_stat(client, payload)
            await asyncio.sleep(0)

            assert len(fired) == 1, "First STMt while PLAYING should fire"
            assert fired[0].elapsed_seconds == 42.0
        finally:
            await event_bus.unsubscribe("player.status", _capture)

    @pytest.mark.asyncio
    async def test_stmt_throttled_within_interval(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """Rapid STMt heartbeats should NOT all publish — throttle must gate."""
        from resonance.core.events import PlayerStatusEvent, event_bus

        player_mac = "00:04:20:e1:e1:02"
        client = PlayerClient(mock_reader, mock_writer)
        client._id = player_mac
        client.status.state = PlayerState.PLAYING

        fired: list[PlayerStatusEvent] = []

        async def _capture(event):
            if isinstance(event, PlayerStatusEvent) and event.player_id == player_mac:
                fired.append(event)

        await event_bus.subscribe("player.status", _capture)
        try:
            # Ensure no prior push timestamp
            slimproto_server._last_elapsed_push.pop(player_mac, None)

            payload = build_stat_message(event_code="STMt", elapsed_seconds=10, elapsed_ms=10000)

            # First call: should fire (no prior push)
            await slimproto_server._handle_stat(client, payload)
            await asyncio.sleep(0)
            assert len(fired) == 1, "First STMt should fire"

            # Second call immediately after: should be throttled
            await slimproto_server._handle_stat(client, payload)
            await asyncio.sleep(0)
            assert len(fired) == 1, "Second STMt within interval should be throttled"

            # Third call also immediately: still throttled
            await slimproto_server._handle_stat(client, payload)
            await asyncio.sleep(0)
            assert len(fired) == 1, "Third STMt within interval should be throttled"
        finally:
            await event_bus.unsubscribe("player.status", _capture)

    @pytest.mark.asyncio
    async def test_stmt_fires_after_interval_expires(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """STMt should fire again once the throttle interval has elapsed."""
        import time as _time

        from resonance.core.events import PlayerStatusEvent, event_bus
        from resonance.protocol.slimproto import ELAPSED_PUSH_INTERVAL_SECONDS

        player_mac = "00:04:20:e1:e1:03"
        client = PlayerClient(mock_reader, mock_writer)
        client._id = player_mac
        client.status.state = PlayerState.PLAYING

        fired: list[PlayerStatusEvent] = []

        async def _capture(event):
            if isinstance(event, PlayerStatusEvent) and event.player_id == player_mac:
                fired.append(event)

        await event_bus.subscribe("player.status", _capture)
        try:
            # Simulate that the last push happened long ago
            slimproto_server._last_elapsed_push[player_mac] = (
                _time.monotonic() - ELAPSED_PUSH_INTERVAL_SECONDS - 1.0
            )

            payload = build_stat_message(event_code="STMt", elapsed_seconds=99, elapsed_ms=99000)
            await slimproto_server._handle_stat(client, payload)
            await asyncio.sleep(0)

            assert len(fired) == 1, "STMt after interval expiry should fire"
        finally:
            await event_bus.unsubscribe("player.status", _capture)

    @pytest.mark.asyncio
    async def test_stmt_no_publish_when_not_playing(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """STMt should NOT publish elapsed-time events when PAUSED or STOPPED."""
        from resonance.core.events import PlayerStatusEvent, event_bus

        player_mac = "00:04:20:e1:e1:04"
        client = PlayerClient(mock_reader, mock_writer)
        client._id = player_mac

        fired: list[PlayerStatusEvent] = []

        async def _capture(event):
            if isinstance(event, PlayerStatusEvent) and event.player_id == player_mac:
                fired.append(event)

        await event_bus.subscribe("player.status", _capture)
        try:
            slimproto_server._last_elapsed_push.pop(player_mac, None)
            payload = build_stat_message(event_code="STMt", elapsed_seconds=5, elapsed_ms=5000)

            for state in (PlayerState.PAUSED, PlayerState.STOPPED, PlayerState.CONNECTED):
                client.status.state = state
                await slimproto_server._handle_stat(client, payload)
                await asyncio.sleep(0)

            assert len(fired) == 0, "STMt must not publish when not PLAYING"
        finally:
            await event_bus.unsubscribe("player.status", _capture)

    @pytest.mark.asyncio
    async def test_state_change_resets_throttle_timer(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """STMr/STMs/STMp publish immediately AND reset the throttle timer,
        so the next STMt doesn't fire too soon after the state-change push."""
        import time as _time

        from resonance.core.events import PlayerStatusEvent, event_bus
        from resonance.protocol.slimproto import ELAPSED_PUSH_INTERVAL_SECONDS

        player_mac = "00:04:20:e1:e1:05"
        client = PlayerClient(mock_reader, mock_writer)
        client._id = player_mac
        client.status.state = PlayerState.PLAYING

        fired: list[PlayerStatusEvent] = []

        async def _capture(event):
            if isinstance(event, PlayerStatusEvent) and event.player_id == player_mac:
                fired.append(event)

        await event_bus.subscribe("player.status", _capture)
        try:
            # Expire the throttle so STMt would fire if it were next
            slimproto_server._last_elapsed_push[player_mac] = (
                _time.monotonic() - ELAPSED_PUSH_INTERVAL_SECONDS - 1.0
            )

            # STMr (resume) should publish AND reset the timer
            payload_r = build_stat_message(event_code="STMr", elapsed_seconds=20, elapsed_ms=20000)
            await slimproto_server._handle_stat(client, payload_r)
            await asyncio.sleep(0)
            assert len(fired) == 1, "STMr should publish"

            # Immediately following STMt should be throttled (timer was just reset)
            payload_t = build_stat_message(event_code="STMt", elapsed_seconds=21, elapsed_ms=21000)
            await slimproto_server._handle_stat(client, payload_t)
            await asyncio.sleep(0)
            assert len(fired) == 1, "STMt right after STMr should be throttled"
        finally:
            await event_bus.unsubscribe("player.status", _capture)

    @pytest.mark.asyncio
    async def test_disconnect_clears_throttle(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """BYE! should clear the throttle entry so a reconnecting player
        gets a fresh push immediately."""
        player_mac = "00:04:20:e1:e1:06"
        client = PlayerClient(mock_reader, mock_writer)
        client._id = player_mac

        slimproto_server._last_elapsed_push[player_mac] = 12345.0

        await slimproto_server._handle_bye(client, b"")

        assert player_mac not in slimproto_server._last_elapsed_push


# -----------------------------------------------------------------------------
# Playback-Confirmed Generation Guard Tests
# -----------------------------------------------------------------------------


class TestPlaybackConfirmedGuard:
    """Tests for _stms_confirmed_generation guard that prevents endless
    STOP+START cycling when a broken stream (e.g. failed M4B seek transcode)
    never produces audio."""

    @pytest.mark.asyncio
    async def test_stms_records_confirmed_generation(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """STMs should record the current stream generation as confirmed."""
        player_mac = "00:04:20:cf:cf:01"
        client = PlayerClient(mock_reader, mock_writer)
        client._id = player_mac
        client.status.state = PlayerState.STOPPED

        fake_ss = _FakeStreamingServer(generation=7)
        slimproto_server.streaming_server = fake_ss

        payload = build_stat_message(event_code="STMs", elapsed_seconds=0, elapsed_ms=0)
        await slimproto_server._handle_stat(client, payload)

        assert slimproto_server._stms_confirmed_generation.get(player_mac) == 7
        assert client.status.state == PlayerState.PLAYING

    @pytest.mark.asyncio
    async def test_stmr_does_not_record_confirmed_generation(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """STMr (resume) should NOT record confirmed generation — only STMs does."""
        player_mac = "00:04:20:cf:cf:02"
        client = PlayerClient(mock_reader, mock_writer)
        client._id = player_mac
        client.status.state = PlayerState.PAUSED

        fake_ss = _FakeStreamingServer(generation=5)
        slimproto_server.streaming_server = fake_ss

        payload = build_stat_message(event_code="STMr", elapsed_seconds=10, elapsed_ms=10000)
        await slimproto_server._handle_stat(client, payload)

        assert client.status.state == PlayerState.PLAYING
        assert player_mac not in slimproto_server._stms_confirmed_generation

    @pytest.mark.asyncio
    async def test_stmd_blocked_without_playback_confirmation(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """STMd must NOT trigger prefetch if playback was never confirmed
        (STMs never received for this generation)."""
        from resonance.core.events import PlayerDecodeReadyEvent, event_bus

        player_mac = "00:04:20:cf:cf:03"
        client = PlayerClient(mock_reader, mock_writer)
        client._id = player_mac
        client.status.state = PlayerState.STOPPED

        fake_ss = _FakeStreamingServer(generation=8)
        slimproto_server.streaming_server = fake_ss

        # No _stms_confirmed_generation entry — simulates broken stream

        fired: list[PlayerDecodeReadyEvent] = []

        async def _capture(event):
            if isinstance(event, PlayerDecodeReadyEvent) and event.player_id == player_mac:
                fired.append(event)

        await event_bus.subscribe("player.decode_ready", _capture)
        try:
            payload = build_stat_message(event_code="STMd", elapsed_seconds=0, elapsed_ms=0)
            await slimproto_server._handle_stat(client, payload)
            await asyncio.sleep(0)

            # STMd should have been suppressed — no event published
            assert len(fired) == 0
        finally:
            await event_bus.unsubscribe("player.decode_ready", _capture)

    @pytest.mark.asyncio
    async def test_stmd_deferred_until_stms_confirmation_then_replayed(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """If STMd arrives before STMs for the same generation, prefetch should
        be deferred and replayed once STMs confirms playback."""
        from resonance.core.events import PlayerDecodeReadyEvent, event_bus

        player_mac = "00:04:20:cf:cf:03"
        client = PlayerClient(mock_reader, mock_writer)
        client._id = player_mac
        client.status.state = PlayerState.STOPPED

        fake_ss = _FakeStreamingServer(generation=8)
        slimproto_server.streaming_server = fake_ss

        fired: list[PlayerDecodeReadyEvent] = []

        async def _capture(event):
            if isinstance(event, PlayerDecodeReadyEvent) and event.player_id == player_mac:
                fired.append(event)

        await event_bus.subscribe("player.decode_ready", _capture)
        try:
            # 1) STMd first: should be deferred (not published yet).
            payload_stmd = build_stat_message(event_code="STMd", elapsed_seconds=0, elapsed_ms=0)
            await slimproto_server._handle_stat(client, payload_stmd)
            await asyncio.sleep(0)

            assert len(fired) == 0
            assert slimproto_server._pending_stmd_generation.get(player_mac) == 8

            # 2) STMs for same generation: should confirm and replay deferred STMd.
            payload_stms = build_stat_message(event_code="STMs", elapsed_seconds=0, elapsed_ms=0)
            await slimproto_server._handle_stat(client, payload_stms)
            await asyncio.sleep(0)

            assert len(fired) == 1
            assert fired[0].stream_generation == 8
            assert player_mac not in slimproto_server._pending_stmd_generation
        finally:
            await event_bus.unsubscribe("player.decode_ready", _capture)
    @pytest.mark.asyncio
    async def test_stmd_allowed_with_playback_confirmation(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """STMd should trigger prefetch when playback was confirmed (STMs received)."""
        from resonance.core.events import PlayerDecodeReadyEvent, event_bus

        player_mac = "00:04:20:cf:cf:04"
        client = PlayerClient(mock_reader, mock_writer)
        client._id = player_mac
        client.status.state = PlayerState.PLAYING

        fake_ss = _FakeStreamingServer(generation=8)
        slimproto_server.streaming_server = fake_ss

        # Simulate confirmed playback
        slimproto_server._stms_confirmed_generation[player_mac] = 8

        fired: list[PlayerDecodeReadyEvent] = []

        async def _capture(event):
            if isinstance(event, PlayerDecodeReadyEvent) and event.player_id == player_mac:
                fired.append(event)

        await event_bus.subscribe("player.decode_ready", _capture)
        try:
            payload = build_stat_message(event_code="STMd", elapsed_seconds=30, elapsed_ms=30000)
            await slimproto_server._handle_stat(client, payload)
            await asyncio.sleep(0)

            assert len(fired) == 1
            assert fired[0].stream_generation == 8
        finally:
            await event_bus.unsubscribe("player.decode_ready", _capture)

    @pytest.mark.asyncio
    async def test_stmu_blocked_without_playback_confirmation(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """STMu must NOT fire PlayerTrackFinishedEvent if playback was
        never confirmed — prevents cycling through playlist on broken streams."""
        from resonance.core.events import PlayerTrackFinishedEvent, event_bus

        player_mac = "00:04:20:cf:cf:05"
        client = PlayerClient(mock_reader, mock_writer)
        client._id = player_mac
        client.status.state = PlayerState.PLAYING

        fake_ss = _FakeStreamingServer(generation=12)
        slimproto_server.streaming_server = fake_ss

        # No _stms_confirmed_generation entry — broken stream scenario

        fired: list[PlayerTrackFinishedEvent] = []

        async def _capture(event):
            if isinstance(event, PlayerTrackFinishedEvent) and event.player_id == player_mac:
                fired.append(event)

        await event_bus.subscribe("player.track_finished", _capture)
        try:
            payload = build_stat_message(event_code="STMu", elapsed_seconds=0, elapsed_ms=0)
            await slimproto_server._handle_stat(client, payload)
            await asyncio.sleep(0)

            assert len(fired) == 0
        finally:
            await event_bus.unsubscribe("player.track_finished", _capture)

    @pytest.mark.asyncio
    async def test_stmu_allowed_with_playback_confirmation(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """STMu should fire PlayerTrackFinishedEvent when playback was confirmed."""
        from resonance.core.events import PlayerTrackFinishedEvent, event_bus

        player_mac = "00:04:20:cf:cf:06"
        client = PlayerClient(mock_reader, mock_writer)
        client._id = player_mac
        client.status.state = PlayerState.PLAYING

        fake_ss = _FakeStreamingServer(generation=12)
        slimproto_server.streaming_server = fake_ss

        # Simulate confirmed playback
        slimproto_server._stms_confirmed_generation[player_mac] = 12

        fired: list[PlayerTrackFinishedEvent] = []

        async def _capture(event):
            if isinstance(event, PlayerTrackFinishedEvent) and event.player_id == player_mac:
                fired.append(event)

        await event_bus.subscribe("player.track_finished", _capture)
        try:
            payload = build_stat_message(event_code="STMu", elapsed_seconds=180, elapsed_ms=180000)
            await slimproto_server._handle_stat(client, payload)
            await asyncio.sleep(0)

            assert len(fired) == 1
            assert fired[0].stream_generation == 12
        finally:
            await event_bus.unsubscribe("player.track_finished", _capture)

    @pytest.mark.asyncio
    async def test_stmu_allowed_during_prefetch_handoff(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """STMu should still fire in prefetch handoff: confirmed_gen == current_gen - 1.

        This mirrors LMS flow where STMd prequeues the next stream before STMu
        arrives for the currently playing one.
        """
        from resonance.core.events import PlayerTrackFinishedEvent, event_bus

        player_mac = "00:04:20:cf:cf:06"
        client = PlayerClient(mock_reader, mock_writer)
        client._id = player_mac
        client.status.state = PlayerState.PLAYING

        # Current generation already points to prefetched next stream.
        fake_ss = _FakeStreamingServer(generation=13)
        slimproto_server.streaming_server = fake_ss

        # Playback confirmation belongs to the just-finishing stream (gen=12).
        slimproto_server._stms_confirmed_generation[player_mac] = 12

        fired: list[PlayerTrackFinishedEvent] = []

        async def _capture(event):
            if isinstance(event, PlayerTrackFinishedEvent) and event.player_id == player_mac:
                fired.append(event)

        await event_bus.subscribe("player.track_finished", _capture)
        try:
            payload = build_stat_message(event_code="STMu", elapsed_seconds=10, elapsed_ms=10000)
            await slimproto_server._handle_stat(client, payload)
            await asyncio.sleep(0)

            assert len(fired) == 1
            assert fired[0].stream_generation == 13
        finally:
            await event_bus.unsubscribe("player.track_finished", _capture)


    @pytest.mark.asyncio
    async def test_server_side_track_end_blocked_without_confirmation(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """STMt must not fire track-finished even if DSCO bookkeeping exists and playback is in progress."""
        from resonance.core.events import PlayerTrackFinishedEvent, event_bus

        player_mac = "00:04:20:cf:cf:07"
        client = PlayerClient(mock_reader, mock_writer)
        client._id = player_mac
        client.status.state = PlayerState.PLAYING

        fake_ss = _FakeStreamingServer(generation=10, stream_age=185.0, track_duration=180.0)
        slimproto_server.streaming_server = fake_ss

        # DSCO recorded but NO playback confirmation
        slimproto_server._dsco_received_generation[player_mac] = 10
        # Deliberately NOT setting _stms_confirmed_generation

        fired: list[PlayerTrackFinishedEvent] = []

        async def _capture(event):
            if isinstance(event, PlayerTrackFinishedEvent) and event.player_id == player_mac:
                fired.append(event)

        await event_bus.subscribe("player.track_finished", _capture)
        try:
            payload = build_stat_message(event_code="STMt", elapsed_seconds=0, elapsed_ms=0)
            await slimproto_server._handle_stat(client, payload)
            await asyncio.sleep(0)

            assert len(fired) == 0
        finally:
            await event_bus.unsubscribe("player.track_finished", _capture)

    @pytest.mark.asyncio
    async def test_stale_confirmation_does_not_match_new_generation(
        self,
        slimproto_server: SlimprotoServer,
        mock_reader: AsyncMock,
        mock_writer: MagicMock,
    ) -> None:
        """A stale _stms_confirmed_generation from a previous stream must NOT
        satisfy the guard for the current stream generation."""
        from resonance.core.events import PlayerTrackFinishedEvent, event_bus

        player_mac = "00:04:20:cf:cf:08"
        client = PlayerClient(mock_reader, mock_writer)
        client._id = player_mac
        client.status.state = PlayerState.PLAYING

        # Current generation is 15, but confirmation is from old gen 10
        fake_ss = _FakeStreamingServer(generation=15)
        slimproto_server.streaming_server = fake_ss
        slimproto_server._stms_confirmed_generation[player_mac] = 10

        fired: list[PlayerTrackFinishedEvent] = []

        async def _capture(event):
            if isinstance(event, PlayerTrackFinishedEvent) and event.player_id == player_mac:
                fired.append(event)

        await event_bus.subscribe("player.track_finished", _capture)
        try:
            payload = build_stat_message(event_code="STMu", elapsed_seconds=0, elapsed_ms=0)
            await slimproto_server._handle_stat(client, payload)
            await asyncio.sleep(0)

            # Stale confirmation gen=10 != current gen=15 → blocked
            assert len(fired) == 0
        finally:
            await event_bus.unsubscribe("player.track_finished", _capture)
