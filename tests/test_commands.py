"""
Unit tests for Slimproto commands (Server → Player).

Tests the binary command frame builders for strm, audg, and other
commands that the server sends to players.
"""

import struct

import pytest

from resonance.protocol.commands import (
    STRM_FIXED_HEADER_SIZE,
    AudioFormat,
    AutostartMode,
    PCMChannels,
    PCMEndianness,
    PCMSampleRate,
    PCMSampleSize,
    SpdifMode,
    StreamCommand,
    StreamParams,
    TransitionType,
    build_audg_frame,
    build_display_bitmap,
    build_display_brightness,
    build_display_clear,
    build_display_framebuffer,
    build_display_framebuffer_clear,
    build_stream_flush,
    build_stream_pause,
    build_stream_start,
    build_stream_status,
    build_stream_stop,
    build_stream_unpause,
    build_strm_frame,
    build_volume_frame,
)


class TestStreamParams:
    """Tests for StreamParams dataclass."""

    def test_default_values(self) -> None:
        """Default StreamParams should have sensible values."""
        params = StreamParams()

        assert params.command == StreamCommand.START
        assert params.autostart == AutostartMode.AUTO
        assert params.format == AudioFormat.MP3
        assert params.buffer_threshold_kb == 255
        assert params.server_ip == 0

    def test_custom_values(self) -> None:
        """StreamParams should accept custom values."""
        params = StreamParams(
            command=StreamCommand.PAUSE,
            format=AudioFormat.FLAC,
            server_port=8080,
        )

        assert params.command == StreamCommand.PAUSE
        assert params.format == AudioFormat.FLAC
        assert params.server_port == 8080


class TestBuildStrmFrame:
    """Tests for build_strm_frame function."""

    def test_header_size(self) -> None:
        """Frame without request string should be exactly 24 bytes."""
        params = StreamParams()
        frame = build_strm_frame(params, "")

        assert len(frame) == STRM_FIXED_HEADER_SIZE

    def test_header_with_request_string(self) -> None:
        """Frame with request string should be 24 + len(request)."""
        params = StreamParams()
        request = "GET /stream.mp3 HTTP/1.0\r\n\r\n"
        frame = build_strm_frame(params, request)

        assert len(frame) == STRM_FIXED_HEADER_SIZE + len(request)

    def test_command_byte(self) -> None:
        """First byte should be the command character."""
        params = StreamParams(command=StreamCommand.START)
        frame = build_strm_frame(params, "")

        assert frame[0:1] == b"s"

        params = StreamParams(command=StreamCommand.PAUSE)
        frame = build_strm_frame(params, "")

        assert frame[0:1] == b"p"

        params = StreamParams(command=StreamCommand.STOP)
        frame = build_strm_frame(params, "")

        assert frame[0:1] == b"q"

    def test_autostart_byte(self) -> None:
        """Second byte should be the autostart mode."""
        params = StreamParams(autostart=AutostartMode.AUTO)
        frame = build_strm_frame(params, "")

        assert frame[1:2] == b"1"

        params = StreamParams(autostart=AutostartMode.OFF)
        frame = build_strm_frame(params, "")

        assert frame[1:2] == b"0"

        params = StreamParams(autostart=AutostartMode.DIRECT_AUTO)
        frame = build_strm_frame(params, "")

        assert frame[1:2] == b"3"

    def test_format_byte(self) -> None:
        """Third byte should be the format character."""
        params = StreamParams(format=AudioFormat.MP3)
        frame = build_strm_frame(params, "")

        assert frame[2:3] == b"m"

        params = StreamParams(format=AudioFormat.FLAC)
        frame = build_strm_frame(params, "")

        assert frame[2:3] == b"f"

        params = StreamParams(format=AudioFormat.PCM)
        frame = build_strm_frame(params, "")

        assert frame[2:3] == b"p"

    def test_server_port_position(self) -> None:
        """Server port should be at bytes 18-19 (big-endian)."""
        params = StreamParams(server_port=9000)
        frame = build_strm_frame(params, "")

        port = struct.unpack(">H", frame[18:20])[0]
        assert port == 9000

        params = StreamParams(server_port=8080)
        frame = build_strm_frame(params, "")

        port = struct.unpack(">H", frame[18:20])[0]
        assert port == 8080

    def test_server_ip_position(self) -> None:
        """Server IP should be at bytes 20-23 (big-endian)."""
        params = StreamParams(server_ip=0)
        frame = build_strm_frame(params, "")

        ip = struct.unpack(">I", frame[20:24])[0]
        assert ip == 0

        # 192.168.1.1 = 0xC0A80101
        params = StreamParams(server_ip=0xC0A80101)
        frame = build_strm_frame(params, "")

        ip = struct.unpack(">I", frame[20:24])[0]
        assert ip == 0xC0A80101

    def test_request_string_appended(self) -> None:
        """Request string should be appended after the header."""
        request = "GET /test HTTP/1.0\r\n"
        params = StreamParams()
        frame = build_strm_frame(params, request)

        assert frame[24:] == request.encode("latin-1")

    def test_buffer_threshold(self) -> None:
        """Buffer threshold should be at byte 7."""
        params = StreamParams(buffer_threshold_kb=128)
        frame = build_strm_frame(params, "")

        assert frame[7] == 128

    def test_transition_type(self) -> None:
        """Transition type should be at byte 10."""
        params = StreamParams(transition_type=TransitionType.CROSSFADE)
        frame = build_strm_frame(params, "")

        assert frame[10:11] == b"1"

    def test_flags_byte(self) -> None:
        """Flags should be at byte 11."""
        params = StreamParams(flags=0x80)  # loop infinite
        frame = build_strm_frame(params, "")

        assert frame[11] == 0x80


class TestBuildStreamStart:
    """Tests for build_stream_start convenience function."""

    def test_creates_start_command(self) -> None:
        """Should create a start ('s') command."""
        frame = build_stream_start("aa:bb:cc:dd:ee:ff")

        assert frame[0:1] == b"s"

    def test_includes_player_mac_in_url(self) -> None:
        """Request string should include player MAC."""
        mac = "aa:bb:cc:dd:ee:ff"
        frame = build_stream_start(mac)

        request_string = frame[24:].decode("latin-1")
        assert mac in request_string
        assert "GET /stream.mp3?player=" in request_string

    def test_uses_provided_port(self) -> None:
        """Should use the provided server port."""
        frame = build_stream_start("aa:bb:cc:dd:ee:ff", server_port=8080)

        port = struct.unpack(">H", frame[18:20])[0]
        assert port == 8080

    def test_format_parameter(self) -> None:
        """Should use the provided audio format."""
        frame = build_stream_start("aa:bb:cc:dd:ee:ff", format=AudioFormat.FLAC)

        assert frame[2:3] == b"f"

    def test_runtime_stream_fields(self) -> None:
        """Runtime fields should be encoded in the strm fixed header."""
        frame = build_stream_start(
            "aa:bb:cc:dd:ee:ff",
            transition_duration=7,
            transition_type=TransitionType.CROSSFADE_IMMEDIATE,
            flags=0x4A,
            output_threshold=9,
            sync_streams=3,
            replay_gain=0x00018000,
        )

        assert frame[9] == 7
        assert frame[10:11] == b"5"
        assert frame[11] == 0x4A
        assert frame[12] == 9
        assert frame[13] == 3
        assert struct.unpack(">I", frame[14:18])[0] == 0x00018000


class TestBuildStreamPause:
    """Tests for build_stream_pause function."""

    def test_creates_pause_command(self) -> None:
        """Should create a pause ('p') command."""
        frame = build_stream_pause()

        assert frame[0:1] == b"p"
        assert len(frame) == STRM_FIXED_HEADER_SIZE

    def test_autostart_off(self) -> None:
        """Autostart should be off for pause."""
        frame = build_stream_pause()

        assert frame[1:2] == b"0"


class TestBuildStreamUnpause:
    """Tests for build_stream_unpause function."""

    def test_creates_unpause_command(self) -> None:
        """Should create an unpause ('u') command."""
        frame = build_stream_unpause()

        assert frame[0:1] == b"u"
        assert len(frame) == STRM_FIXED_HEADER_SIZE


class TestBuildStreamStop:
    """Tests for build_stream_stop function."""

    def test_creates_stop_command(self) -> None:
        """Should create a stop ('q') command."""
        frame = build_stream_stop()

        assert frame[0:1] == b"q"
        assert len(frame) == STRM_FIXED_HEADER_SIZE


class TestBuildStreamFlush:
    """Tests for build_stream_flush function."""

    def test_creates_flush_command(self) -> None:
        """Should create a flush ('f') command."""
        frame = build_stream_flush()

        assert frame[0:1] == b"f"


class TestBuildStreamStatus:
    """Tests for build_stream_status function."""

    def test_creates_status_command(self) -> None:
        """Should create a status ('t') command."""
        frame = build_stream_status()

        assert frame[0:1] == b"t"


class TestBuildAudgFrame:
    """Tests for build_audg_frame function."""

    def test_frame_structure(self) -> None:
        """Frame should have correct size and structure."""
        frame = build_audg_frame()

        # Frame should be 18 bytes (2x4 old-style + 1 + 1 + 2x4 new-style)
        assert len(frame) == 18

    def test_old_style_gain_fields(self) -> None:
        """Old-style gain fields should carry the values passed in."""
        frame = build_audg_frame(old_left=46, old_right=46)

        old_left = struct.unpack(">I", frame[0:4])[0]
        old_right = struct.unpack(">I", frame[4:8])[0]

        assert old_left == 46
        assert old_right == 46

    def test_old_style_defaults_zero(self) -> None:
        """Old-style gain fields default to zero."""
        frame = build_audg_frame()

        old_left = struct.unpack(">I", frame[0:4])[0]
        old_right = struct.unpack(">I", frame[4:8])[0]

        assert old_left == 0
        assert old_right == 0

    def test_digital_volume_flag(self) -> None:
        """Digital volume flag should be at byte 8."""
        frame = build_audg_frame(digital_volume=True)
        assert frame[8] == 1

        frame = build_audg_frame(digital_volume=False)
        assert frame[8] == 0

    def test_preamp_byte(self) -> None:
        """Preamp should be at byte 9."""
        frame = build_audg_frame(preamp=200)
        assert frame[9] == 200

    def test_gain_values(self) -> None:
        """New-style gain values should be stored as-is (16.16 fixed point)."""
        frame = build_audg_frame(new_left=0x8000, new_right=0x10000)

        left_fixed = struct.unpack(">I", frame[10:14])[0]
        right_fixed = struct.unpack(">I", frame[14:18])[0]

        assert left_fixed == 0x8000
        # 0x10000 = unity gain in 16.16 fixed point
        assert right_fixed == 0x10000


class TestBuildAudeFrame:
    """Tests for build_aude_frame function."""

    def test_frame_structure(self) -> None:
        """Frame should be 2 bytes."""
        from resonance.protocol.commands import build_aude_frame

        frame = build_aude_frame()
        assert len(frame) == 2

    def test_both_enabled(self) -> None:
        """Both outputs enabled should be (1, 1)."""
        from resonance.protocol.commands import build_aude_frame

        frame = build_aude_frame(spdif_enable=True, dac_enable=True)
        assert frame == b"\x01\x01"

    def test_both_disabled(self) -> None:
        """Both outputs disabled should be (0, 0)."""
        from resonance.protocol.commands import build_aude_frame

        frame = build_aude_frame(spdif_enable=False, dac_enable=False)
        assert frame == b"\x00\x00"

    def test_spdif_only(self) -> None:
        """Only S/PDIF enabled should be (1, 0)."""
        from resonance.protocol.commands import build_aude_frame

        frame = build_aude_frame(spdif_enable=True, dac_enable=False)
        assert frame == b"\x01\x00"

    def test_dac_only(self) -> None:
        """Only DAC enabled should be (0, 1)."""
        from resonance.protocol.commands import build_aude_frame

        frame = build_aude_frame(spdif_enable=False, dac_enable=True)
        assert frame == b"\x00\x01"

    def test_defaults_to_enabled(self) -> None:
        """Default should enable both outputs."""
        from resonance.protocol.commands import build_aude_frame

        frame = build_aude_frame()
        assert frame == b"\x01\x01"



class TestDisplayFrames:
    """Tests for grfb/grfe display frame builders."""

    def test_build_display_brightness_negative_one(self) -> None:
        """grfb should encode -1 as 0xFFFF."""
        frame = build_display_brightness(-1)
        assert frame == b"\xff\xff"

    def test_build_display_brightness_positive(self) -> None:
        """grfb should encode signed 16-bit values in big-endian order."""
        frame = build_display_brightness(5)
        assert frame == struct.pack(">h", 5)

    def test_build_display_brightness_out_of_range_raises(self) -> None:
        """grfb should reject values that do not fit into signed 16-bit."""
        with pytest.raises(ValueError, match="Brightness code out of range"):
            build_display_brightness(40000)

    def test_build_display_bitmap_header(self) -> None:
        """grfe should prepend offset/transition/param header to bitmap bytes."""
        bitmap = b"\x01\x02\x03\x04"
        frame = build_display_bitmap(bitmap, offset=640, transition="R", param=7)

        assert struct.unpack(">H", frame[0:2])[0] == 640
        assert frame[2:3] == b"R"
        assert frame[3] == 7
        assert frame[4:] == bitmap

    def test_build_display_bitmap_invalid_transition_raises(self) -> None:
        """grfe transition must be a single character."""
        with pytest.raises(ValueError, match="Transition must be exactly one character"):
            build_display_bitmap(b"", transition="LR")

    def test_build_display_bitmap_non_ascii_transition_raises(self) -> None:
        """grfe transition must be ASCII-encodable."""
        with pytest.raises(ValueError, match="Transition must be ASCII"):
            build_display_bitmap(b"", transition="ä")

    def test_build_display_clear_default_size(self) -> None:
        """Default clear frame should contain 1280 zero bytes plus 4-byte header."""
        frame = build_display_clear()

        assert len(frame) == 4 + 1280
        assert frame[0:4] == struct.pack(">HcB", 0, b"c", 0)
        assert frame[4:] == (b"\x00" * 1280)

    def test_build_display_framebuffer_default_offset(self) -> None:
        """grfd should prepend the LMS live framebuffer offset by default."""
        frame = build_display_framebuffer(b"\xde\xad")

        assert struct.unpack(">H", frame[0:2])[0] == 560
        assert frame[2:] == b"\xde\xad"

    def test_build_display_framebuffer_custom_offset(self) -> None:
        """grfd should allow caller-provided framebuffer offsets."""
        frame = build_display_framebuffer(b"\x11\x22", offset=128)

        assert struct.unpack(">H", frame[0:2])[0] == 128
        assert frame[2:] == b"\x11\x22"

    def test_build_display_framebuffer_invalid_offset_raises(self) -> None:
        """grfd offset must fit into unsigned 16-bit."""
        with pytest.raises(ValueError, match="Offset out of range for grfd"):
            build_display_framebuffer(b"", offset=70000)

    def test_build_display_framebuffer_clear_default_size(self) -> None:
        """Default grfd clear should contain 560 zero bytes plus 2-byte offset."""
        frame = build_display_framebuffer_clear()

        assert struct.unpack(">H", frame[0:2])[0] == 560
        assert len(frame[2:]) == 560
        assert set(frame[2:]) == {0}

class TestBuildVolumeFrame:
    """Tests for build_volume_frame function."""

    def test_volume_zero(self) -> None:
        """Volume 0 should produce zero gain (old and new)."""
        frame = build_volume_frame(volume=0)

        old_left = struct.unpack(">I", frame[0:4])[0]
        old_right = struct.unpack(">I", frame[4:8])[0]
        new_left = struct.unpack(">I", frame[10:14])[0]
        new_right = struct.unpack(">I", frame[14:18])[0]

        assert old_left == 0
        assert old_right == 0
        assert new_left == 0
        assert new_right == 0

    def test_volume_max(self) -> None:
        """Volume 100 should produce unity gain (0x10000) and old-style 128."""
        frame = build_volume_frame(volume=100)

        old_left = struct.unpack(">I", frame[0:4])[0]
        new_left = struct.unpack(">I", frame[10:14])[0]
        new_right = struct.unpack(">I", frame[14:18])[0]

        # Old-style: VOLUME_MAP[100] = 128
        assert old_left == 128
        # New-style: 0 dB → 16.16 fixed-point unity = 0x10000
        assert new_left == 0x10000
        assert new_right == 0x10000

    def test_muted_produces_zero(self) -> None:
        """Muted should produce zero gain regardless of volume."""
        frame = build_volume_frame(volume=100, muted=True)

        old_left = struct.unpack(">I", frame[0:4])[0]
        new_left = struct.unpack(">I", frame[10:14])[0]
        new_right = struct.unpack(">I", frame[14:18])[0]

        assert old_left == 0
        assert new_left == 0
        assert new_right == 0

    def test_volume_50_uses_db_curve(self) -> None:
        """Volume 50 should use the dB-based logarithmic curve, NOT linear."""
        from resonance.protocol.commands import VOLUME_MAP, _db_to_fixed, _volume_to_db

        frame = build_volume_frame(volume=50)

        old_left = struct.unpack(">I", frame[0:4])[0]
        new_left = struct.unpack(">I", frame[10:14])[0]

        # Old-style: VOLUME_MAP[50] = 46
        assert old_left == VOLUME_MAP[50]
        assert old_left == 46

        # New-style: dB-based, much quieter than the old linear 0x8000
        expected_db = _volume_to_db(50)
        expected_gain = _db_to_fixed(expected_db)
        assert new_left == expected_gain
        # Sanity: dB-based gain at vol 50 is far below linear 50% (0x8000)
        assert new_left < 0x8000

    def test_volume_curve_is_monotonic(self) -> None:
        """Gain should increase monotonically with volume."""
        prev_gain = 0
        for vol in range(1, 101):
            frame = build_volume_frame(volume=vol)
            new_left = struct.unpack(">I", frame[10:14])[0]
            assert new_left >= prev_gain, f"Gain decreased at volume {vol}"
            prev_gain = new_left


class TestDeviceCapabilities:
    """Tests for device capability system and per-device volume curves."""

    def test_get_capabilities_known_device(self) -> None:
        """Known device types should return their specific capabilities."""
        from resonance.player.capabilities import get_device_capabilities
        from resonance.player.client import DeviceType

        caps = get_device_capabilities(DeviceType.BOOM)
        assert caps.volume_params.total_volume_range_db == -74.0
        assert caps.volume_params.step_point == 25.0
        assert caps.has_line_in is True
        assert caps.has_rtc_alarm is True
        assert caps.can_power_off is False

    def test_get_capabilities_unknown_device(self) -> None:
        """Unknown device types should return SB2 defaults."""
        from resonance.player.capabilities import get_device_capabilities
        from resonance.player.client import DeviceType

        caps = get_device_capabilities(DeviceType.UNKNOWN)
        assert caps.volume_params.total_volume_range_db == -50.0
        assert caps.volume_params.step_point == -1.0

    def test_squeezeplay_uses_boom_curve(self) -> None:
        """SqueezePlay (Radio/Touch) should use the Boom volume curve."""
        from resonance.player.capabilities import BOOM_VOLUME, get_device_capabilities
        from resonance.player.client import DeviceType

        caps = get_device_capabilities(DeviceType.SQUEEZEPLAY)
        assert caps.volume_params is BOOM_VOLUME

    def test_boom_volume_curve_two_slopes(self) -> None:
        """Boom curve should have two distinct slopes around step_point=25."""
        from resonance.protocol.commands import _volume_to_db

        # Boom parameters
        params = dict(total_volume_range_db=-74.0, step_point=25.0, step_fraction=0.5)

        # At volume 0: should be -74 dB
        assert _volume_to_db(0, **params) == -74.0
        # At volume 25 (step point): should be -37 dB (half of -74)
        assert abs(_volume_to_db(25, **params) - (-37.0)) < 0.01
        # At volume 100: should be 0 dB
        assert _volume_to_db(100, **params) == 0.0

    def test_boom_curve_is_monotonic(self) -> None:
        """Boom volume curve should increase monotonically."""
        from resonance.protocol.commands import _volume_to_db

        params = dict(total_volume_range_db=-74.0, step_point=25.0, step_fraction=0.5)
        prev_db = -100.0
        for vol in range(0, 101):
            db = _volume_to_db(vol, **params)
            assert db >= prev_db, f"dB decreased at volume {vol}"
            prev_db = db

    def test_boom_volume_frame_differs_from_sb2(self) -> None:
        """Boom curve should produce different gain than SB2 at low volume.

        At volume 10, the two-slope Boom curve (-74 dB range) is much
        quieter than the single-slope SB2 curve (-50 dB range) because
        the steep lower slope covers -74 to -37 dB in just 0-25 UI steps.
        """
        from resonance.player.capabilities import BOOM_VOLUME

        frame_sb2 = build_volume_frame(volume=10)
        frame_boom = build_volume_frame(volume=10, volume_params=BOOM_VOLUME)

        gain_sb2 = struct.unpack(">I", frame_sb2[10:14])[0]
        gain_boom = struct.unpack(">I", frame_boom[10:14])[0]

        # At volume 10, Boom is in the steep lower slope and much quieter
        # SB2: -44.6 dB → gain ~388, Boom: -59.2 dB → gain ~72
        assert gain_sb2 > gain_boom
        assert gain_boom > 0

    def test_boom_volume_frame_max_is_unity(self) -> None:
        """Volume 100 should be unity gain regardless of curve."""
        from resonance.player.capabilities import BOOM_VOLUME

        frame = build_volume_frame(volume=100, volume_params=BOOM_VOLUME)
        new_left = struct.unpack(">I", frame[10:14])[0]
        assert new_left == 0x10000

    def test_capability_properties(self) -> None:
        """Convenience properties should derive from min/max ranges."""
        from resonance.player.capabilities import get_device_capabilities
        from resonance.player.client import DeviceType

        boom = get_device_capabilities(DeviceType.BOOM)
        assert boom.has_bass is True   # 23 != -23
        assert boom.has_treble is True
        assert boom.has_stereo_xl is True  # 3 != 0

        sb2 = get_device_capabilities(DeviceType.SQUEEZEBOX2)
        assert sb2.has_bass is False   # 50 == 50
        assert sb2.has_treble is False
        assert sb2.has_stereo_xl is False


class TestEnumValues:
    """Tests for enum value correctness."""

    def test_stream_command_values(self) -> None:
        """StreamCommand values should match protocol spec."""
        assert StreamCommand.START.value == ord("s")
        assert StreamCommand.PAUSE.value == ord("p")
        assert StreamCommand.UNPAUSE.value == ord("u")
        assert StreamCommand.STOP.value == ord("q")
        assert StreamCommand.FLUSH.value == ord("f")
        assert StreamCommand.STATUS.value == ord("t")

    def test_audio_format_values(self) -> None:
        """AudioFormat values should match protocol spec."""
        assert AudioFormat.MP3.value == ord("m")
        assert AudioFormat.PCM.value == ord("p")
        assert AudioFormat.FLAC.value == ord("f")
        assert AudioFormat.OGG.value == ord("o")
        assert AudioFormat.AAC.value == ord("a")
        assert AudioFormat.WMA.value == ord("w")

    def test_autostart_mode_values(self) -> None:
        """AutostartMode values should match protocol spec."""
        assert AutostartMode.OFF.value == ord("0")
        assert AutostartMode.AUTO.value == ord("1")
        assert AutostartMode.DIRECT.value == ord("2")
        assert AutostartMode.DIRECT_AUTO.value == ord("3")

    def test_transition_type_values(self) -> None:
        """TransitionType values should match protocol spec."""
        assert TransitionType.NONE.value == ord("0")
        assert TransitionType.CROSSFADE.value == ord("1")
        assert TransitionType.FADE_IN.value == ord("2")
        assert TransitionType.FADE_OUT.value == ord("3")
        assert TransitionType.FADE_IN_OUT.value == ord("4")
        assert TransitionType.CROSSFADE_IMMEDIATE.value == ord("5")

