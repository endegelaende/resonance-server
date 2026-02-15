from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from resonance.protocol.commands import FLAG_NO_RESTART_DECODER
from resonance.web.handlers import CommandContext
from resonance.web.handlers.playlist import _start_track_stream
from resonance.web.handlers.seeking import _execute_seek_internal


@pytest.mark.asyncio
async def test_playlist_restart_clears_no_restart_decoder_flag() -> None:
    player = AsyncMock()
    player.status = SimpleNamespace(
        state=SimpleNamespace(name="PLAYING"),
        volume=42,
        muted=False,
    )

    runtime_params = SimpleNamespace(
        transition_duration=0,
        transition_type=0,
        flags=FLAG_NO_RESTART_DECODER | 0x05,
        replay_gain=0,
    )

    streaming_server = SimpleNamespace(
        resolve_runtime_stream_params=AsyncMock(return_value=runtime_params),
        cancel_stream=MagicMock(),
        queue_file=MagicMock(),
        set_track_duration=MagicMock(),
    )

    ctx = CommandContext(
        player_id="aa:bb:cc:dd:ee:01",
        music_library=MagicMock(),
        player_registry=AsyncMock(),
        playlist_manager=MagicMock(get=MagicMock(return_value=None)),
        streaming_server=streaming_server,
        slimproto=SimpleNamespace(
            _resonance_server=SimpleNamespace(
                suppress_track_finished_for_player=MagicMock(),
            ),
            get_advertise_ip_for_player=lambda _player: "127.0.0.1",
        ),
    )

    track = SimpleNamespace(path="/music/test.mp3", duration_ms=123000)

    await _start_track_stream(ctx, player, track)

    kwargs = player.start_track.await_args.kwargs
    assert kwargs["stream_flags"] == 0x05
    assert kwargs["stream_flags"] & FLAG_NO_RESTART_DECODER == 0


@pytest.mark.asyncio
async def test_seek_restart_clears_no_restart_decoder_flag() -> None:
    player = AsyncMock()
    player.status = SimpleNamespace(
        state=SimpleNamespace(name="PLAYING"),
        volume=35,
        muted=False,
    )

    track = SimpleNamespace(path="/music/chapter1.m4b", duration_ms=300000)
    playlist = SimpleNamespace(current_track=track)

    runtime_params = SimpleNamespace(
        transition_duration=0,
        transition_type=0,
        flags=FLAG_NO_RESTART_DECODER | 0x03,
        replay_gain=0,
    )

    streaming_server = SimpleNamespace(
        cancel_stream=MagicMock(),
        resolve_runtime_stream_params=AsyncMock(return_value=runtime_params),
        queue_file_with_seek=MagicMock(),
        queue_file_with_byte_offset=MagicMock(),
    )

    player_id = "aa:bb:cc:dd:ee:02"
    ctx = CommandContext(
        player_id=player_id,
        music_library=MagicMock(),
        player_registry=AsyncMock(),
        playlist_manager=SimpleNamespace(get=MagicMock(return_value=playlist)),
        streaming_server=streaming_server,
        slimproto=SimpleNamespace(
            cancel_deferred_track_finished=MagicMock(),
            _resonance_server=SimpleNamespace(
                suppress_track_finished_for_player=MagicMock(),
            ),
            get_advertise_ip_for_player=lambda _player: "127.0.0.1",
        ),
    )

    await _execute_seek_internal(ctx, player, 12.5)

    kwargs = player.start_track.await_args.kwargs
    assert kwargs["stream_flags"] == 0x03
    assert kwargs["stream_flags"] & FLAG_NO_RESTART_DECODER == 0
