"""
Player client representation for Resonance.

This module defines the PlayerClient class which represents a connected
Squeezebox player (hardware or software like Squeezelite).
"""

import logging
import struct
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from asyncio import StreamReader, StreamWriter

    from resonance.core.playlist import PlaylistTrack

logger = logging.getLogger(__name__)


class PlayerState(Enum):
    """Possible states of a player."""

    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    PLAYING = "playing"
    PAUSED = "paused"
    STOPPED = "stopped"


class DeviceType(Enum):
    """Known Squeezebox device types."""

    UNKNOWN = 0
    SLIMP3 = 1
    SQUEEZEBOX = 2
    SOFTSQUEEZE = 3
    SQUEEZEBOX2 = 4
    TRANSPORTER = 5
    SOFTSQUEEZE3 = 6
    RECEIVER = 7
    SQUEEZESLAVE = 8  # Protocol name, cannot be renamed
    CONTROLLER = 9
    BOOM = 10
    SOFTBOOM = 11
    SQUEEZEPLAY = 12

    @classmethod
    def from_id(cls, device_id: int) -> "DeviceType":
        """Get device type from numeric ID."""
        try:
            return cls(device_id)
        except ValueError:
            return cls.UNKNOWN


@dataclass
class PlayerInfo:
    """Static information about a player, set during HELO handshake."""

    device_type: DeviceType = DeviceType.UNKNOWN
    firmware_version: str = ""
    mac_address: str = ""
    uuid: str = ""
    name: str = ""
    model: str = ""
    capabilities: dict[str, str] = field(default_factory=dict)


@dataclass
class PlayerStatus:
    """Dynamic status of a player, updated via STAT messages.

    Note on "sticky elapsed":
    Some players (notably during stop/flush/stream restart around seeks) will
    transiently report elapsed=0 for a short period even though playback is
    effectively continuing from a non-zero position. Clients that poll status
    (e.g., 1 Hz) can interpret this as a hard jump back to zero.

    To make status more stable across these transient restarts, we track the
    last non-zero elapsed reported by the player, along with its timestamp.
    Web/status handlers can use this to avoid regressing to 0 during a brief
    restart window.
    """

    state: PlayerState = PlayerState.DISCONNECTED
    volume: int = 50
    muted: bool = False
    elapsed_seconds: float = 0.0
    elapsed_milliseconds: int = 0
    # Monotonic timestamp when the latest elapsed sample was received via STAT.
    # Mirrors LMS elapsed extrapolation (sample time + now-delta).
    elapsed_report_monotonic: float = 0.0

    # Monotonic timestamp of the latest stream start command sent to the player.
    # Used to bound raw elapsed samples during stream handovers.
    elapsed_origin_monotonic: float = 0.0

    # Sticky elapsed (last good non-zero progress) to mask transient zeros.
    # NOTE: Currently tracked but not actively consumed. The primary mechanism
    # for correct elapsed reporting is start_offset in StreamingServer.
    # This is kept as a safety net for future use (e.g., if a status handler
    # wants to avoid showing 0 during brief stop/flush windows).
    last_nonzero_elapsed_seconds: float = 0.0
    last_nonzero_elapsed_milliseconds: int = 0
    last_nonzero_elapsed_at: float = 0.0

    buffer_fullness: int = 0
    output_buffer_fullness: int = 0
    signal_strength: int = 0
    last_seen: float = field(default_factory=time.time)


class PlayerClient:
    """
    Represents a connected Squeezebox player.

    Each PlayerClient instance manages the state and communication
    with a single player device. It tracks both static information
    (set during connection) and dynamic status (updated continuously).

    Attributes:
        id: Unique identifier for this player (usually MAC address).
        info: Static player information from HELO.
        status: Dynamic player status from STAT messages.
    """

    def __init__(
        self,
        reader: "StreamReader",
        writer: "StreamWriter",
        client_id: str | None = None,
    ) -> None:
        """
        Initialize a new player client.

        Args:
            reader: Asyncio stream reader for this connection.
            writer: Asyncio stream writer for this connection.
            client_id: Optional identifier (usually set after HELO).
        """
        self._reader = reader
        self._writer = writer
        self._id = client_id or ""

        self.info = PlayerInfo()
        self.status = PlayerStatus()

        # Sequence number for volume sync (LMS/SqueezePlay compatibility)
        self._seq_no: int | None = None

        # Connection metadata
        peername = writer.get_extra_info("peername")
        self._remote_ip = peername[0] if peername else "unknown"
        self._remote_port = peername[1] if peername else 0
        self._connected_at = time.time()

        # Stream metadata (set by RESP/META handlers in slimproto)
        self.last_resp_headers: str | None = None
        self.icy_title: str | None = None

        logger.debug(
            "PlayerClient created for connection from %s:%d",
            self._remote_ip,
            self._remote_port,
        )

    @property
    def id(self) -> str:
        """Get the player's unique identifier (MAC address)."""
        return self._id

    @id.setter
    def id(self, value: str) -> None:
        """Set the player's unique identifier."""
        self._id = value

    @property
    def name(self) -> str:
        """Get the player's display name."""
        return self.info.name or self._id or f"Player-{self._remote_ip}"

    @name.setter
    def name(self, value: str) -> None:
        """Set the player's display name (e.g. via SETD)."""
        self.info.name = value

    @property
    def mac_address(self) -> str:
        """Get the player's MAC address."""
        return self.info.mac_address or self._id

    @property
    def ip_address(self) -> str:
        """Get the player's IP address."""
        return self._remote_ip

    @property
    def is_connected(self) -> bool:
        """Check if the player is still connected."""
        return self.status.state != PlayerState.DISCONNECTED

    @property
    def remote_address(self) -> tuple[str, int]:
        """Get the remote IP address and port."""
        return (self._remote_ip, self._remote_port)

    async def send_message(self, command: bytes, payload: bytes = b"") -> None:
        """
        Send a message to the player.

        Args:
            command: 4-byte command tag (e.g., b'strm', b'audg').
            payload: Message payload bytes.

        Raises:
            ConnectionError: If the connection is closed.
        """
        if len(command) != 4:
            raise ValueError(f"Command must be exactly 4 bytes, got {len(command)}")

        # Debug: attempt to parse outgoing STRM header even when sent directly
        # via PlayerClient (i.e., bypassing SlimprotoServer._send_message()).
        if command == b"strm" and len(payload) >= 24 and logger.isEnabledFor(logging.DEBUG):
            fixed = payload[:24]
            try:
                cmd_ch = fixed[0:1].decode("ascii", errors="replace")
                autostart_ch = fixed[1:2].decode("ascii", errors="replace")
                format_ch = fixed[2:3].decode("ascii", errors="replace")
                pcm_sample_size_ch = fixed[3:4].decode("ascii", errors="replace")
                pcm_sample_rate_ch = fixed[4:5].decode("ascii", errors="replace")
                pcm_channels_ch = fixed[5:6].decode("ascii", errors="replace")
                pcm_endian_ch = fixed[6:7].decode("ascii", errors="replace")
            except Exception:
                cmd_ch = "?"
                autostart_ch = "?"
                format_ch = "?"
                pcm_sample_size_ch = "?"
                pcm_sample_rate_ch = "?"
                pcm_channels_ch = "?"
                pcm_endian_ch = "?"
            server_port = struct.unpack(">H", fixed[18:20])[0]
            server_ip = struct.unpack(">I", fixed[20:24])[0]
            logger.debug(
                "PlayerClient TX strm parsed: command=%s autostart=%s format=%s pcm_sz=%s pcm_rate=%s pcm_ch=%s pcm_endian=%s server_port=%d server_ip=0x%08x",
                cmd_ch,
                autostart_ch,
                format_ch,
                pcm_sample_size_ch,
                pcm_sample_rate_ch,
                pcm_channels_ch,
                pcm_endian_ch,
                server_port,
                server_ip,
            )
            if len(payload) > 24:
                req_preview = payload[24 : 24 + 200]
                logger.debug(
                    "PlayerClient TX strm request_preview=%r",
                    req_preview.decode("latin-1", errors="replace"),
                )

        # Message format: [2 bytes length (len+4)][4 bytes command][payload]
        message = (len(payload) + 4).to_bytes(2, "big") + command + payload

        try:
            self._writer.write(message)
            await self._writer.drain()
            logger.debug("Sent %s to %s (%d bytes)", command, self.id, len(payload))
        except (ConnectionError, OSError) as e:
            logger.warning("Failed to send to %s: %s", self.id, e)
            await self.disconnect()
            raise ConnectionError(f"Player {self.id} disconnected: {e}") from e

    async def disconnect(self) -> None:
        """Close the connection to this player."""
        if self.status.state == PlayerState.DISCONNECTED:
            return

        logger.info("Disconnecting player %s", self.name)
        self.status.state = PlayerState.DISCONNECTED

        try:
            self._writer.close()
            await self._writer.wait_closed()
        except (ConnectionError, OSError):
            pass  # Already disconnected

    def update_last_seen(self) -> None:
        """Update the last seen timestamp."""
        self.status.last_seen = time.time()

    def seconds_since_last_seen(self) -> float:
        """Get seconds elapsed since last communication."""
        return time.time() - self.status.last_seen

    def __repr__(self) -> str:
        """Return a string representation for debugging."""
        return (
            f"PlayerClient(id={self._id!r}, name={self.name!r}, "
            f"type={self.info.device_type.name}, state={self.status.state.name})"
        )

    def __str__(self) -> str:
        """Return a human-readable string representation."""
        return f"{self.name} ({self.info.device_type.name})"

    # =========================================================================
    # Playback Control Methods
    # =========================================================================

    @property
    def device_type(self) -> str:
        """Get the device type as a string."""
        return self.info.device_type.name.lower()

    @property
    def device_capabilities(self) -> "DeviceCapabilities":
        """Get hardware capabilities for this device type."""
        from resonance.player.capabilities import get_device_capabilities

        return get_device_capabilities(self.info.device_type)

    async def play(self) -> None:
        """
        Resume playback (unpause).

        Note: To start a new stream, use start_stream() instead.
        This only resumes a paused stream.
        """
        from resonance.protocol.commands import build_stream_unpause

        logger.info("Resuming playback on %s", self.name)
        # Ensure outputs are enabled before unpausing; power/sleep flows may have disabled them.
        await self.set_audio_enable(True)
        frame = build_stream_unpause()
        await self.send_message(b"strm", frame)
        self.status.state = PlayerState.PLAYING

    async def pause(self) -> None:
        """Pause playback."""
        from resonance.protocol.commands import build_stream_pause

        logger.info("Pausing playback on %s", self.name)
        frame = build_stream_pause()
        await self.send_message(b"strm", frame)
        self.status.state = PlayerState.PAUSED

    async def stop(self) -> None:
        """Stop playback and clear the buffer."""
        from resonance.protocol.commands import build_stream_stop

        logger.info("Stopping playback on %s", self.name)
        frame = build_stream_stop()
        await self.send_message(b"strm", frame)
        self.status.state = PlayerState.STOPPED

    async def flush(self) -> None:
        """Flush the player's buffer.

        This is used during track changes to immediately clear the old audio
        from the buffer, ensuring the new track starts without delay.
        LMS does this via closeStream() + flush in _stopClient().
        """
        from resonance.protocol.commands import build_stream_flush

        logger.info("Flushing buffer on %s", self.name)
        frame = build_stream_flush()
        await self.send_message(b"strm", frame)

    async def toggle_pause(self) -> None:
        """Toggle between playing and paused states."""
        if self.status.state == PlayerState.PAUSED:
            await self.play()
        elif self.status.state == PlayerState.PLAYING:
            await self.pause()
        else:
            # If stopped or other state, try to play
            await self.play()

    async def set_volume(self, volume: int, muted: bool = False, seq_no: int | None = None) -> None:
        """
        Set the player volume.

        Uses the device-specific volume curve (e.g. -74 dB two-slope for
        Boom/SqueezePlay, -50 dB single-slope for Squeezebox2).

        Args:
            volume: Volume level 0-100.
            muted: Whether to mute the player.
            seq_no: Sequence number from client (for LMS/SqueezePlay volume sync).
                   When provided, it's echoed back in the audg frame so the
                   player can discard stale volume updates.
        """
        from resonance.protocol.commands import build_volume_frame

        volume = max(0, min(100, volume))  # Clamp to 0-100
        logger.info("Setting volume to %d%s on %s", volume, " (muted)" if muted else "", self.name)
        frame = build_volume_frame(
            volume, muted, seq_no=seq_no,
            volume_params=self.device_capabilities.volume_params,
        )
        await self.send_message(b"audg", frame)
        self.status.volume = volume
        self.status.muted = muted
        # Store seq_no for status responses (LMS compatibility)
        if seq_no is not None:
            self._seq_no = seq_no

    async def set_audio_enable(self, enabled: bool = True) -> None:
        """
        Enable or disable audio outputs (S/PDIF and DAC).

        This is sent when powering the player on/off. Hardware players
        use this to actually enable/disable their audio output circuits.

        Args:
            enabled: True to enable audio outputs, False to disable.
        """
        from resonance.protocol.commands import build_aude_frame

        logger.info("Setting audio enable to %s on %s", enabled, self.name)
        frame = build_aude_frame(spdif_enable=enabled, dac_enable=enabled)
        await self.send_message(b"aude", frame)

    async def volume_up(self, step: int = 5) -> None:
        """Increase volume by step percent."""
        new_volume = min(100, self.status.volume + step)
        await self.set_volume(new_volume)

    async def volume_down(self, step: int = 5) -> None:
        """Decrease volume by step percent."""
        new_volume = max(0, self.status.volume - step)
        await self.set_volume(new_volume)

    async def mute(self) -> None:
        """Mute the player."""
        await self.set_volume(self.status.volume, muted=True)

    async def unmute(self) -> None:
        """Unmute the player."""
        await self.set_volume(self.status.volume, muted=False)

    # =========================================================================
    # Stream Control Methods
    # =========================================================================

    async def start_stream(
        self,
        track_path: str,
        *,
        server_port: int,
        server_ip: int,
        format_hint: str = "mp3",
        buffer_threshold_kb: int = 255,
        transition_duration: int = 0,
        transition_type: int = 0,
        stream_flags: int = 0,
        replay_gain: int = 0,
    ) -> None:
        """
        Start streaming a track to this player.

        This sends a 'strm' start command with the appropriate HTTP request
        that tells the player where to fetch the audio data.

        Args:
            track_path: Path to the audio file (used in the stream URL).
            server_port: HTTP port the player should connect to.
            server_ip: Server IP (0 = use control server IP).
            format_hint: Audio format hint ('mp3', 'flac', 'ogg', etc.).
            buffer_threshold_kb: Player buffer threshold (KB) required before starting playback.
                Lower values start sooner (snappier track changes) but can increase underruns.
            transition_duration: Transition duration in seconds (strm header byte d).
            transition_type: Transition mode index (0..5).
            stream_flags: Bit flags for the strm header.
            replay_gain: ReplayGain value as 16.16 fixed point (32-bit).
        """
        from resonance.protocol.commands import (
            AudioFormat,
            AutostartMode,
            PCMChannels,
            PCMEndianness,
            PCMSampleRate,
            PCMSampleSize,
            TransitionType,
            build_stream_start,
        )

        # Determine audio format from file extension or hint.
        #
        # IMPORTANT:
        # The HTTP streaming route may transcode certain formats on the server side.
        # The `strm` command MUST signal the format the player will actually receive.
        #
        # Source of truth:
        # - resonance.streaming.policy.strm_expected_format_hint()
        logger.debug("Resolving format for hint: %s", format_hint)

        from resonance.streaming.policy import strm_expected_format_hint

        expected_hint = strm_expected_format_hint(format_hint, self.info.device_type)
        hint_lower = expected_hint.lower()

        # If the server will transcode, expected_hint will be "flac" (current policy).
        if hint_lower == "flac":
            audio_format = AudioFormat.FLAC
            autostart = AutostartMode.AUTO
            pcm_sample_size = PCMSampleSize.SELF_DESCRIBING
            pcm_sample_rate = PCMSampleRate.SELF_DESCRIBING
            pcm_channels = PCMChannels.SELF_DESCRIBING
            pcm_endianness = PCMEndianness.SELF_DESCRIBING
            logger.debug(
                "Streaming policy expects transcoded output for hint=%s -> %s (format='f')",
                format_hint,
                expected_hint,
            )
        elif hint_lower in ("wav", "pcm"):
            audio_format = AudioFormat.PCM
            autostart = AutostartMode.AUTO
            pcm_sample_size = PCMSampleSize.SELF_DESCRIBING
            pcm_sample_rate = PCMSampleRate.SELF_DESCRIBING
            pcm_channels = PCMChannels.SELF_DESCRIBING
            pcm_endianness = PCMEndianness.SELF_DESCRIBING
        else:
            format_map = {
                "mp3": AudioFormat.MP3,
                "flac": AudioFormat.FLAC,
                "ogg": AudioFormat.OGG,
                "aac": AudioFormat.AAC,
                "wma": AudioFormat.WMA,
                "alc": AudioFormat.ALAC,
                "alac": AudioFormat.ALAC,
                "dsd": AudioFormat.DSD,
                "dsf": AudioFormat.DSD,
                "dff": AudioFormat.DSD,
            }
            audio_format = format_map.get(hint_lower, AudioFormat.MP3)
            autostart = AutostartMode.AUTO
            pcm_sample_size = PCMSampleSize.SELF_DESCRIBING
            pcm_sample_rate = PCMSampleRate.SELF_DESCRIBING
            pcm_channels = PCMChannels.SELF_DESCRIBING
            pcm_endianness = PCMEndianness.SELF_DESCRIBING

            # DSD sub-format: pcmsamplesize distinguishes DSF (0) vs DFF (1)
            # on the wire. LMS reference: Slim/Player/Squeezebox.pm stream_s()
            #   $pcmsamplesize = $format eq 'dsf' ? 0 : 1;
            if hint_lower == "dsf":
                pcm_sample_size = PCMSampleSize.DSD_DSF
                pcm_sample_rate = PCMSampleRate.SELF_DESCRIBING
                pcm_endianness = PCMEndianness.SELF_DESCRIBING
                pcm_channels = PCMChannels.SELF_DESCRIBING
            elif hint_lower == "dff":
                pcm_sample_size = PCMSampleSize.DSD_DFF
                pcm_sample_rate = PCMSampleRate.SELF_DESCRIBING
                pcm_endianness = PCMEndianness.SELF_DESCRIBING
                pcm_channels = PCMChannels.SELF_DESCRIBING

        try:
            transition_index = int(transition_type)
        except (TypeError, ValueError):
            transition_index = 0
        transition_index = max(0, min(transition_index, 5))

        try:
            duration_value = int(transition_duration)
        except (TypeError, ValueError):
            duration_value = 0
        duration_value = max(0, min(duration_value, 255))

        transition_mode = TransitionType(ord("0") + transition_index)

        try:
            flags_value = int(stream_flags) & 0xFF
        except (TypeError, ValueError):
            flags_value = 0

        try:
            replay_gain_value = int(replay_gain) & 0xFFFFFFFF
        except (TypeError, ValueError):
            replay_gain_value = 0

        logger.debug(
            "start_stream logic: hint='%s' -> format=%s (val=%r), autostart=%s (val=%r), pcm_sz=%s [port=%d, ip=%d]",
            format_hint,
            audio_format.name,
            chr(audio_format.value),
            autostart.name,
            chr(autostart.value),
            chr(pcm_sample_size.value),
            server_port,
            server_ip,
        )

        logger.info(
            "Starting stream for %s on %s (format=%s, autostart=%s, port=%d)",
            track_path,
            self.name,
            audio_format.name,
            autostart.name,
            server_port,
        )

        # Keep DAC/S/PDIF enabled on every stream start to avoid silent playback
        # after power/sleep transitions.
        await self.set_audio_enable(True)

        # Build and send the strm start frame
        frame = build_stream_start(
            player_mac=self.mac_address,
            server_port=server_port,
            server_ip=server_ip,
            format=audio_format,
            pcm_sample_size=pcm_sample_size,
            pcm_sample_rate=pcm_sample_rate,
            pcm_channels=pcm_channels,
            pcm_endianness=pcm_endianness,
            autostart=autostart,
            buffer_threshold_kb=buffer_threshold_kb,
            transition_duration=duration_value,
            transition_type=transition_mode,
            flags=flags_value,
            replay_gain=replay_gain_value,
        )
        await self.send_message(b"strm", frame)

        # Reset elapsed baseline immediately on stream switch so status does not
        # briefly report stale progress from the previous stream.
        self.status.elapsed_seconds = 0.0
        self.status.elapsed_milliseconds = 0
        self.status.last_nonzero_elapsed_seconds = 0.0
        self.status.last_nonzero_elapsed_milliseconds = 0
        self.status.last_nonzero_elapsed_at = 0.0
        self.status.elapsed_report_monotonic = time.monotonic()
        self.status.elapsed_origin_monotonic = time.monotonic()

    def get_streaming_url(self, server_host: str, server_port: int = 9000) -> str:
        """
        Get the streaming URL for this player.

        Args:
            server_host: Server hostname or IP.
            server_port: HTTP port.

        Returns:
            URL string the player would connect to.
        """
        return f"http://{server_host}:{server_port}/stream.mp3?player={self.mac_address}"

    async def start_track(
        self,
        track: "PlaylistTrack",
        *,
        server_port: int,
        server_ip: int,
        transition_duration: int = 0,
        transition_type: int = 0,
        stream_flags: int = 0,
        replay_gain: int = 0,
        format_hint_override: str | None = None,
    ) -> None:
        """
        Start playing a PlaylistTrack.

        This is a convenience method that extracts format from the track path
        and calls start_stream.

        Args:
            track: The PlaylistTrack to play.
            server_port: HTTP port for streaming.
            server_ip: Server IP (0 = use control server IP).
            transition_duration: Transition duration in seconds (strm header byte d).
            transition_type: Transition mode index (0..5).
            stream_flags: Bit flags for the strm header.
            replay_gain: ReplayGain value as 16.16 fixed point (32-bit).
            format_hint_override: Optional format hint to signal in strm instead
                of deriving it from the file extension.
        """
        from pathlib import Path

        # Extract format from file extension unless caller overrides it.
        path = Path(track.path)
        format_hint = format_hint_override or path.suffix.lstrip(".").lower() or "mp3"

        await self.start_stream(
            track.path,
            server_port=server_port,
            server_ip=server_ip,
            format_hint=format_hint,
            transition_duration=transition_duration,
            transition_type=transition_type,
            stream_flags=stream_flags,
            replay_gain=replay_gain,
        )
