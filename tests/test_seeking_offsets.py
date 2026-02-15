from __future__ import annotations

from pathlib import Path

from resonance.web.handlers.seeking import calculate_byte_offset


def _synchsafe(value: int) -> bytes:
    """Encode an integer as a 4-byte ID3v2 synchsafe value."""
    return bytes(
        [
            (value >> 21) & 0x7F,
            (value >> 14) & 0x7F,
            (value >> 7) & 0x7F,
            value & 0x7F,
        ]
    )


def _write_mp3_with_id3(path: Path, tag_size: int, audio_size: int) -> None:
    header = b"ID3" + bytes([4, 0, 0]) + _synchsafe(tag_size)
    assert len(header) == 10
    path.write_bytes(header + (b"X" * tag_size) + (b"A" * audio_size))


def test_calculate_byte_offset_uses_duration_based_mapping_for_mp3(tmp_path: Path) -> None:
    file_path = tmp_path / "sample.mp3"
    _write_mp3_with_id3(file_path, tag_size=32, audio_size=1000)

    # duration=10s, target=5s -> midpoint of audio payload plus ID3 start offset.
    offset = calculate_byte_offset(file_path, target_seconds=5.0, duration_ms=10_000)
    assert offset == (10 + 32 + 500)


def test_calculate_byte_offset_without_duration_does_not_guess_bitrate(tmp_path: Path) -> None:
    file_path = tmp_path / "sample.mp3"
    _write_mp3_with_id3(file_path, tag_size=24, audio_size=2048)

    # LMS-style behavior: without known duration, do not guess bytes/sec.
    offset = calculate_byte_offset(file_path, target_seconds=30.0, duration_ms=None)
    assert offset == (10 + 24)


def test_calculate_byte_offset_clamps_to_file_end_on_overseek(tmp_path: Path) -> None:
    file_path = tmp_path / "sample.flac"
    file_path.write_bytes(b"F" * 4096)

    offset = calculate_byte_offset(file_path, target_seconds=120.0, duration_ms=60_000)
    assert offset == 4096


def test_calculate_byte_offset_applies_block_alignment_for_wav(tmp_path: Path) -> None:
    file_path = tmp_path / "sample.wav"
    file_path.write_bytes(b"W" * 1001)

    # Raw offset = int((1001 / 10) * 1.234) = 123; WAV alignment should floor to 120.
    offset = calculate_byte_offset(file_path, target_seconds=1.234, duration_ms=10_000)
    assert offset == 120
