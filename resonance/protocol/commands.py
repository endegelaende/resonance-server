"""
Slimproto commands for Server → Player communication.

This module implements the binary command frames that the server sends
to Squeezebox players. The most important command is 'strm' which controls
audio streaming.

Reference: Slim/Player/Squeezebox.pm from the original LMS
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from resonance.player.capabilities import VolumeParameters


class StreamCommand(Enum):
    """Stream command types for the 'strm' frame."""

    START = ord("s")  # Start streaming
    PAUSE = ord("p")  # Pause playback
    UNPAUSE = ord("u")  # Resume playback
    STOP = ord("q")  # Stop playback (quit)
    FLUSH = ord("f")  # Flush buffer
    STATUS = ord("t")  # Request status
    SKIP = ord("a")  # Skip ahead (autostart at position)


class AutostartMode(Enum):
    """Autostart modes for stream command."""

    OFF = ord("0")  # Don't auto-start
    AUTO = ord("1")  # Auto-start when buffer ready
    DIRECT = ord("2")  # Direct streaming, no auto-start
    DIRECT_AUTO = ord("3")  # Direct streaming with auto-start


class AudioFormat(Enum):
    """Audio format byte for stream command."""

    MP3 = ord("m")  # MP3 bitstream
    PCM = ord("p")  # PCM audio
    FLAC = ord("f")  # FLAC
    OGG = ord("o")  # Ogg Vorbis
    AAC = ord("a")  # AAC
    WMA = ord("w")  # WMA
    ALAC = ord("l")  # Apple Lossless
    DSD = ord("d")  # DSD
    UNKNOWN = ord("?")  # Unknown/don't care


class PCMSampleSize(Enum):
    """PCM sample size options.

    This field is overloaded depending on the audio format:
    - PCM: bits per sample ('0'=8, '1'=16, '2'=24, '3'=32)
    - AAC: container type ('1'=adif, '2'=adts, ... '5'=mp4ff, '6'=latm)
    - DSD: container sub-format (0=DSF, 1=DFF) — raw bytes, NOT ASCII!
    - Other: '?' = self-describing / don't care

    LMS reference: Slim/Player/Squeezebox.pm stream_s() sets pcmsamplesize
    per format. For DSD, it uses raw numeric 0/1 (not ord('0')/ord('1')).
    """

    BITS_8 = ord("0")
    BITS_16 = ord("1")
    BITS_24 = ord("2")
    BITS_32 = ord("3")

    # AAC container types (reused field when format is AAC)
    # '1' (adif), '2' (adts), '3' (latm within loas),
    # '4' (rawpkts), '5' (mp4ff), '6' (latm within rawpkts)
    AAC_ADIF = ord("1")
    AAC_ADTS = ord("2")
    AAC_LATM_LOAS = ord("3")
    AAC_RAWPKTS = ord("4")
    AAC_MP4FF = ord("5")
    AAC_LATM_RAWPKTS = ord("6")

    # DSD container sub-format (raw byte values, NOT ASCII characters).
    # LMS: $pcmsamplesize = $format eq 'dsf' ? 0 : 1;
    # These distinguish DSF vs DFF when format byte is 'd' (AudioFormat.DSD).
    DSD_DSF = 0  # DSF container (DSD Stream File)
    DSD_DFF = 1  # DFF container (DSDIFF)

    SELF_DESCRIBING = ord("?")  # Let decoder figure it out


class PCMSampleRate(Enum):
    """PCM sample rate options."""

    RATE_11000 = ord("0")
    RATE_22000 = ord("1")
    RATE_32000 = ord("2")
    RATE_44100 = ord("3")
    RATE_48000 = ord("4")
    RATE_8000 = ord("5")
    RATE_12000 = ord("6")
    RATE_16000 = ord("7")
    RATE_24000 = ord("8")
    RATE_96000 = ord("9")
    SELF_DESCRIBING = ord("?")  # Let decoder figure it out


class PCMChannels(Enum):
    """PCM channel configuration."""

    MONO = ord("1")
    STEREO = ord("2")
    SELF_DESCRIBING = ord("?")


class PCMEndianness(Enum):
    """PCM byte order."""

    BIG = ord("0")
    LITTLE = ord("1")
    SELF_DESCRIBING = ord("?")


class TransitionType(Enum):
    """Audio transition types."""

    NONE = ord("0")
    CROSSFADE = ord("1")
    FADE_IN = ord("2")
    FADE_OUT = ord("3")
    FADE_IN_OUT = ord("4")
    CROSSFADE_IMMEDIATE = ord("5")


class SpdifMode(Enum):
    """S/PDIF output mode."""

    AUTO = 0
    ON = 1
    OFF = 2


# Flag bit constants
FLAG_LOOP_INFINITE = 0x80
FLAG_NO_RESTART_DECODER = 0x40
FLAG_USE_SSL = 0x20
FLAG_DIRECT_PROTOCOL = 0x10
FLAG_MONO_RIGHT = 0x08
FLAG_MONO_LEFT = 0x04
FLAG_INVERT_RIGHT = 0x02
FLAG_INVERT_LEFT = 0x01


@dataclass
class StreamParams:
    """Parameters for building a stream command frame."""

    command: StreamCommand = StreamCommand.START
    autostart: AutostartMode = AutostartMode.AUTO
    format: AudioFormat = AudioFormat.MP3
    pcm_sample_size: PCMSampleSize = PCMSampleSize.SELF_DESCRIBING
    pcm_sample_rate: PCMSampleRate = PCMSampleRate.SELF_DESCRIBING
    pcm_channels: PCMChannels = PCMChannels.SELF_DESCRIBING
    pcm_endianness: PCMEndianness = PCMEndianness.SELF_DESCRIBING
    buffer_threshold_kb: int = 255  # KB of buffer before autostart
    spdif_mode: SpdifMode = SpdifMode.AUTO
    transition_duration: int = 0  # Seconds
    transition_type: TransitionType = TransitionType.NONE
    flags: int = 0
    output_threshold: int = 0  # Tenths of second
    sync_streams: int = 0  # Number of synchronized streams (protocol field "S")
    replay_gain: int = 0  # 16.16 fixed point, 0 = none
    server_port: int = 9000  # HTTP port for streaming
    server_ip: int = 0  # 0 = use control server IP


STRM_FIXED_HEADER_SIZE = 24


def build_strm_frame(params: StreamParams, request_string: str = "") -> bytes:
    """
    Build a 'strm' command frame to send to a player.

    The strm frame controls audio streaming. It tells the player what to
    stream, from where, and in what format.

    Frame layout (24 bytes fixed header + variable request string):
        offset  size  field
        0       1     command ('s', 'p', 'u', 'q', 'f', 't', 'a')
        1       1     autostart ('0', '1', '2', '3')
        2       1     format ('m', 'p', 'f', 'o', 'a', 'w', etc.)
        3       1     pcm_sample_size ('0'-'3', '?')
        4       1     pcm_sample_rate ('0'-'9', '?')
        5       1     pcm_channels ('1', '2', '?')
        6       1     pcm_endianness ('0', '1', '?')
        7       1     buffer_threshold (KB)
        8       1     spdif_enable (0, 1, 2)
        9       1     transition_duration (seconds)
        10      1     transition_type ('0'-'5')
        11      1     flags (bit field)
        12      1     output_threshold (tenths of second)
        13      1     sync_streams
        14-17   4     replay_gain (32-bit, 16.16 fixed point)
        18-19   2     server_port
        20-23   4     server_ip (0 = use control server IP)
        24+     var   request_string (HTTP request)

    Args:
        params: Stream parameters.
        request_string: HTTP request string to send to streaming server.

    Returns:
        Complete strm frame bytes.
    """
    # Pack the 24-byte fixed header
    # Format string breakdown:
    #   c c c c c c c = 7 single bytes (command through pcm_endianness)
    #   B             = unsigned byte (buffer_threshold)
    #   B             = unsigned byte (spdif)
    #   B             = unsigned byte (transition_duration)
    #   c             = single byte (transition_type)
    #   B             = unsigned byte (flags)
    #   B             = unsigned byte (output_threshold)
    #   B             = unsigned byte (sync_streams)
    #   I             = 4-byte unsigned int (replay_gain, big-endian)
    #   H             = 2-byte unsigned short (server_port, big-endian)
    #   I             = 4-byte unsigned int (server_ip, big-endian)

    frame = struct.pack(
        ">cccccccBBBcBBBIHI",
        bytes([params.command.value]),
        bytes([params.autostart.value]),
        bytes([params.format.value]),
        bytes([params.pcm_sample_size.value]),
        bytes([params.pcm_sample_rate.value]),
        bytes([params.pcm_channels.value]),
        bytes([params.pcm_endianness.value]),
        params.buffer_threshold_kb & 0xFF,
        params.spdif_mode.value,
        params.transition_duration & 0xFF,
        bytes([params.transition_type.value]),
        params.flags & 0xFF,
        params.output_threshold & 0xFF,
        params.sync_streams & 0xFF,
        params.replay_gain,
        params.server_port,
        params.server_ip,
    )

    assert len(frame) == STRM_FIXED_HEADER_SIZE, f"Header size mismatch: {len(frame)}"

    # Append the request string
    return frame + request_string.encode("latin-1")


def build_stream_start(
    player_mac: str,
    server_port: int = 9000,
    server_ip: int = 0,
    format: AudioFormat = AudioFormat.MP3,
    pcm_sample_size: PCMSampleSize = PCMSampleSize.SELF_DESCRIBING,
    pcm_sample_rate: PCMSampleRate = PCMSampleRate.SELF_DESCRIBING,
    pcm_channels: PCMChannels = PCMChannels.SELF_DESCRIBING,
    pcm_endianness: PCMEndianness = PCMEndianness.SELF_DESCRIBING,
    autostart: AutostartMode = AutostartMode.AUTO,
    buffer_threshold_kb: int = 255,
    transition_duration: int = 0,
    transition_type: TransitionType = TransitionType.NONE,
    flags: int = 0,
    output_threshold: int = 0,
    sync_streams: int = 0,
    replay_gain: int = 0,
) -> bytes:
    """
    Build a strm frame to start streaming audio to a player.

    This is a convenience function for the common case of starting
    an MP3 or FLAC stream from the server.

    Args:
        player_mac: MAC address of the player (used in request URL).
        server_port: HTTP port the player should connect to.
        server_ip: Server IP (0 = use control server IP).
        format: Audio format.
        pcm_sample_size: PCM sample size (for PCM format).
        pcm_sample_rate: PCM sample rate (for PCM format).
        pcm_channels: PCM channel count (for PCM format).
        pcm_endianness: PCM byte order (for PCM format).
        autostart: Autostart mode.
        buffer_threshold_kb: Buffer threshold in KB.
        transition_duration: Transition duration in seconds.
        transition_type: Transition type mode.
        flags: Additional strm bit flags.
        output_threshold: Output threshold in 0.1s units.
        sync_streams: Number of synced streams (protocol field S).
        replay_gain: Replay gain as 16.16 fixed-point value.

    Returns:
        Complete strm frame bytes.
    """
    # HTTP request the player will make back to us for the audio stream
    request_string = f"GET /stream.mp3?player={player_mac} HTTP/1.0\r\n\r\n"

    params = StreamParams(
        command=StreamCommand.START,
        autostart=autostart,
        format=format,
        pcm_sample_size=pcm_sample_size,
        pcm_sample_rate=pcm_sample_rate,
        pcm_channels=pcm_channels,
        pcm_endianness=pcm_endianness,
        buffer_threshold_kb=buffer_threshold_kb,
        transition_duration=transition_duration,
        transition_type=transition_type,
        flags=flags,
        output_threshold=output_threshold,
        sync_streams=sync_streams,
        replay_gain=replay_gain,
        server_port=server_port,
        server_ip=server_ip,
    )

    return build_strm_frame(params, request_string)

def build_stream_pause(interval_ms: int = 0) -> bytes:
    """
    Build a strm frame to pause playback.

    Args:
        interval_ms: Optional pause-at timestamp in milliseconds.

    Returns:
        Complete strm frame bytes.
    """
    params = StreamParams(
        command=StreamCommand.PAUSE,
        autostart=AutostartMode.OFF,
        format=AudioFormat.MP3,
        replay_gain=interval_ms,  # replay_gain field used for timestamp
    )
    return build_strm_frame(params)


def build_stream_unpause(interval: int = 0) -> bytes:
    """
    Build a strm frame to resume playback.

    Args:
        interval: Optional unpause-at timestamp.

    Returns:
        Complete strm frame bytes.
    """
    params = StreamParams(
        command=StreamCommand.UNPAUSE,
        autostart=AutostartMode.OFF,
        format=AudioFormat.MP3,
        replay_gain=interval,
    )
    return build_strm_frame(params)


def build_stream_stop() -> bytes:
    """
    Build a strm frame to stop playback.

    Returns:
        Complete strm frame bytes.
    """
    params = StreamParams(
        command=StreamCommand.STOP,
        autostart=AutostartMode.OFF,
        format=AudioFormat.MP3,
    )
    return build_strm_frame(params)


def build_stream_flush() -> bytes:
    """
    Build a strm frame to flush the player's buffer.

    Returns:
        Complete strm frame bytes.
    """
    params = StreamParams(
        command=StreamCommand.FLUSH,
        autostart=AutostartMode.OFF,
        format=AudioFormat.MP3,
    )
    return build_strm_frame(params)


def build_stream_status(server_port: int = 9000, server_ip: int = 0) -> bytes:
    """
    Build a strm frame to request player status.

    This sends a 't' command which prompts the player to send
    a STAT response.

    Note:
        Some players treat server_ip=0 (0.0.0.0) as "server 0" and will log
        "unable to connect to server 0". To avoid this, callers should pass a
        reachable server_ip (e.g. 127.0.0.1 for local testing).

    Args:
        server_port: HTTP port to advertise (reserved/ignored by some clients for status).
        server_ip: IPv4 address to advertise as a 32-bit big-endian integer.

    Returns:
        Complete strm frame bytes.
    """
    params = StreamParams(
        command=StreamCommand.STATUS,
        autostart=AutostartMode.OFF,
        format=AudioFormat.MP3,
        server_port=server_port,
        server_ip=server_ip,
    )
    return build_strm_frame(params)


# ============================================================================
# Audio Gain Command (audg)
# ============================================================================

# --------------------------------------------------------------------------
# LMS-compatible logarithmic volume curve
# --------------------------------------------------------------------------
# Ported from Slim/Player/Squeezebox2.pm (volume_map, getVolume,
# getVolumeParameters, dBToFixed).
#
# Human hearing is logarithmic.  A linear gain curve (the old Resonance
# approach: gain = volume/100 * 256) makes the lower half of the knob do
# almost nothing while the upper half is far too aggressive.
#
# LMS converts the 0-100 UI volume to a dB value on a straight-line ramp,
# then to a 16.16 fixed-point multiplier via 10^(dB/20).  We replicate
# that exactly so that a Squeezebox Radio "feels" the same as on stock LMS.
# --------------------------------------------------------------------------

# Old-style 1.7 fixed-point volume map (101 entries, index 0-100).
# Sent in the first 8 bytes of the audg frame for backward compatibility
# with pre-FW22 Squeezebox2 players.  Newer firmware ignores these bytes
# and uses the 16.16 values instead.
# Source: Slim/Player/Squeezebox2.pm @volume_map
VOLUME_MAP: list[int] = [
    0, 1, 1, 1, 2, 2, 2, 3, 3, 4,
    5, 5, 6, 6, 7, 8, 9, 9, 10, 11,
    12, 13, 14, 15, 16, 16, 17, 18, 19, 20,
    22, 23, 24, 25, 26, 27, 28, 29, 30, 32,
    33, 34, 35, 37, 38, 39, 40, 42, 43, 44,
    46, 47, 48, 50, 51, 53, 54, 56, 57, 59,
    60, 61, 63, 65, 66, 68, 69, 71, 72, 74,
    75, 77, 79, 80, 82, 84, 85, 87, 89, 90,
    92, 94, 96, 97, 99, 101, 103, 104, 106, 108, 110,
    112, 113, 115, 117, 119, 121, 123, 125, 127, 128,
]

# Squeezebox2 volume parameters — kept as module-level constants so that
# call sites without a VolumeParameters instance still work (backward compat).
_MAX_VOLUME_DB: float = 0.0  # unity gain at volume 100


def _volume_to_db(
    volume: int,
    total_volume_range_db: float = -50.0,
    step_point: float = -1.0,
    step_fraction: float = 1.0,
) -> float:
    """Convert a 0-100 UI volume to a dB attenuation value.

    Port of LMS ``Slim::Player::Squeezebox2::getVolume``.

    The function implements a two-slope dB ramp.  ``step_point`` divides
    the 0-100 range: below it a steeper slope gives finer control at low
    volume, above it a gentler slope covers the normal listening range.

    With the default Squeezebox2 parameters (step_point=-1) only the
    upper slope is used → simple linear ramp from -50 dB to 0 dB.

    For Boom/SqueezePlay (step_point=25, step_fraction=0.5):
      - UI  0-25  → -74 dB to -37 dB  (steep, fine control)
      - UI 25-100 → -37 dB to   0 dB  (gentler slope)

    Returns:
        dB value (negative = attenuation, 0 = unity gain).
    """
    step_db = total_volume_range_db * step_fraction

    slope_high = (_MAX_VOLUME_DB - step_db) / (100.0 - step_point)
    slope_low = (step_db - total_volume_range_db) / (step_point - 0.0) if step_point != 0.0 else 0.0

    if volume > step_point:
        return slope_high * (volume - 100.0) + _MAX_VOLUME_DB
    else:
        return slope_low * (volume - 0.0) + total_volume_range_db


def _db_to_fixed(db: float) -> int:
    """Convert a dB value to a 16.16 unsigned fixed-point gain multiplier.

    Port of LMS ``Slim::Player::Squeezebox2::dBToFixed``.

    For values in the range -30 dB … 0 dB the conversion uses 8 bits of
    precision then shifts up, which avoids rounding errors for the most
    common listening range.

    Returns:
        16.16 fixed-point gain (e.g. 0x10000 = unity = 0 dB).
    """
    floatmult = 10.0 ** (db / 20.0)

    if -30.0 <= db <= 0.0:
        return int(floatmult * (1 << 8) + 0.5) * (1 << 8)
    else:
        return int(floatmult * (1 << 16) + 0.5)


def build_audg_frame(
    old_left: int = 0,
    old_right: int = 0,
    new_left: int = 0,
    new_right: int = 0,
    preamp: int = 255,
    digital_volume: bool = True,
    seq_no: int | None = None,
) -> bytes:
    """
    Build an 'audg' frame to set player volume/gain.

    Frame layout:
        offset  size  field
        0-3     4     old_left  (old-style 1.7 FP gain, for pre-FW22 players)
        4-7     4     old_right (old-style 1.7 FP gain, for pre-FW22 players)
        8       1     digital_volume_control (0 or 1)
        9       1     preamp (0-255)
        10-13   4     new_left  (16.16 fixed-point gain)
        14-17   4     new_right (16.16 fixed-point gain)
        18-21   4     sequence_number (optional, for volume sync)

    The old-style fields carry the ``VOLUME_MAP`` value for the current
    volume (0-128 range).  The new-style fields carry the dB-derived
    16.16 fixed-point multiplier.  The player uses whichever format its
    firmware understands.

    The sequence number is used by SqueezePlay/Jive devices to track
    volume changes. When the player sends a volume change with seq_no,
    we echo it back so the player can discard stale updates.

    Args:
        old_left: Old-style left gain (0-128, from VOLUME_MAP).
        old_right: Old-style right gain (0-128, from VOLUME_MAP).
        new_left: New-style left gain (16.16 fixed-point).
        new_right: New-style right gain (16.16 fixed-point).
        preamp: Preamp gain (0-255).
        digital_volume: Whether to use digital volume control.
        seq_no: Sequence number from client (for volume sync).

    Returns:
        Complete audg frame bytes.
    """
    frame = struct.pack(
        ">IIBBII",
        old_left & 0xFFFFFFFF,
        old_right & 0xFFFFFFFF,
        1 if digital_volume else 0,
        preamp,
        new_left & 0xFFFFFFFF,
        new_right & 0xFFFFFFFF,
    )

    # Append sequence number if provided (LMS compatibility)
    # This allows the player to track which volume updates are current
    if seq_no is not None:
        frame += struct.pack(">I", seq_no)

    return frame


def build_volume_frame(
    volume: int,
    muted: bool = False,
    seq_no: int | None = None,
    volume_params: VolumeParameters | None = None,
) -> bytes:
    """
    Build an audg frame to set volume using the LMS dB-based gain curve.

    This is a convenience wrapper around build_audg_frame that converts a
    0-100 UI volume into the correct old-style (1.7 FP) and new-style
    (16.16 FP, dB-derived) gain values — exactly matching LMS behaviour.

    Args:
        volume: Volume level 0-100.
        muted: Whether volume should be muted.
        seq_no: Sequence number from client (for volume sync with SqueezePlay).
        volume_params: Device-specific volume curve parameters.
            None defaults to the Squeezebox2 curve (-50 dB).

    Returns:
        Complete audg frame bytes.
    """
    if muted or volume <= 0:
        return build_audg_frame(
            old_left=0, old_right=0,
            new_left=0, new_right=0,
            seq_no=seq_no,
        )

    volume = max(0, min(100, volume))

    # Old-style gain from the LMS volume map (0-128 range)
    old_gain = VOLUME_MAP[volume]

    # New-style: dB-based logarithmic gain → 16.16 fixed-point
    if volume_params is not None:
        db = _volume_to_db(
            volume,
            total_volume_range_db=volume_params.total_volume_range_db,
            step_point=volume_params.step_point,
            step_fraction=volume_params.step_fraction,
        )
    else:
        db = _volume_to_db(volume)
    new_gain = _db_to_fixed(db)

    return build_audg_frame(
        old_left=old_gain, old_right=old_gain,
        new_left=new_gain, new_right=new_gain,
        seq_no=seq_no,
    )


def build_aude_frame(spdif_enable: bool = True, dac_enable: bool = True) -> bytes:
    """
    Build an 'aude' frame to enable/disable audio outputs.

    This command controls the audio output hardware on Squeezebox players.
    Used when powering the player on/off.

    Frame layout:
        offset  size  field
        0       1     S/PDIF (digital) output enable (0=off, 1=on)
        1       1     DAC (analog) output enable (0=off, 1=on)

    Args:
        spdif_enable: Enable S/PDIF digital output.
        dac_enable: Enable DAC analog output.

    Returns:
        Complete aude frame bytes (2 bytes).
    """
    return struct.pack("BB", 1 if spdif_enable else 0, 1 if dac_enable else 0)


# ============================================================================
# Display Command (grfe/grfb/grfd)
# ============================================================================


DEFAULT_GRFE_BITMAP_BYTES = 1280
DEFAULT_GRFD_BITMAP_BYTES = 560
DEFAULT_GRFD_FRAMEBUFFER_OFFSET = 560


def build_display_brightness(brightness_code: int) -> bytes:
    """
    Build a 'grfb' frame payload for display brightness.

    Args:
        brightness_code: Signed 16-bit brightness value (e.g. -1..5).

    Returns:
        2-byte big-endian payload for the grfb command.
    """
    if not -32768 <= brightness_code <= 32767:
        raise ValueError(f"Brightness code out of range for grfb: {brightness_code}")

    return struct.pack(">h", brightness_code)


def build_display_bitmap(
    bitmap: bytes,
    *,
    offset: int = 0,
    transition: str = "c",
    param: int = 0,
) -> bytes:
    """
    Build a 'grfe' frame payload for bitmap graphics.

    Args:
        bitmap: Raw bitmap bytes.
        offset: Display offset (unsigned 16-bit).
        transition: Single-byte transition character.
        param: Transition parameter byte.

    Returns:
        grfe payload bytes: offset + transition + param + bitmap.
    """
    if not 0 <= offset <= 0xFFFF:
        raise ValueError(f"Offset out of range for grfe: {offset}")
    if len(transition) != 1:
        raise ValueError("Transition must be exactly one character")
    if not 0 <= param <= 0xFF:
        raise ValueError(f"Param out of range for grfe: {param}")

    try:
        transition_byte = transition.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("Transition must be ASCII") from exc

    return struct.pack(">HcB", offset, transition_byte, param) + bitmap


def build_display_clear(bitmap_size: int = DEFAULT_GRFE_BITMAP_BYTES) -> bytes:
    """
    Build a 'grfe' frame to clear the player display.

    Args:
        bitmap_size: Number of zero bytes to append after the 4-byte grfe header.
            1280 matches classic 320x32 displays (4 bytes per column).

    Returns:
        Complete grfe frame bytes with blank display.
    """
    if bitmap_size < 0:
        raise ValueError(f"Bitmap size must be non-negative: {bitmap_size}")

    return build_display_bitmap(
        b"\x00" * bitmap_size,
        offset=0,
        transition="c",
        param=0,
    )


def build_display_framebuffer(
    bitmap: bytes,
    *,
    offset: int = DEFAULT_GRFD_FRAMEBUFFER_OFFSET,
) -> bytes:
    """
    Build a 'grfd' frame payload for legacy graphics framebuffers.

    Payload layout:
      - 2-byte big-endian framebuffer offset
      - raw bitmap payload

    LMS SqueezeboxG uses offset 560 for GRAPHICS_FRAMEBUF_LIVE.
    """
    if not 0 <= offset <= 0xFFFF:
        raise ValueError(f"Offset out of range for grfd: {offset}")

    return struct.pack(">H", offset) + bitmap


def build_display_framebuffer_clear(
    bitmap_size: int = DEFAULT_GRFD_BITMAP_BYTES,
    *,
    offset: int = DEFAULT_GRFD_FRAMEBUFFER_OFFSET,
) -> bytes:
    """
    Build a 'grfd' frame to clear the legacy graphics framebuffer.

    Args:
        bitmap_size: Number of zero bytes to append after the 2-byte offset.
        offset: Framebuffer offset (defaults to LMS live framebuffer 560).
    """
    if bitmap_size < 0:
        raise ValueError(f"Bitmap size must be non-negative: {bitmap_size}")

    return build_display_framebuffer(b"\x00" * bitmap_size, offset=offset)
