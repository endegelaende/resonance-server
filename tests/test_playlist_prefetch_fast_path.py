"""Regression tests for prefetch-aware playlist +1 navigation."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from resonance.core.playlist import Playlist
from resonance.streaming.server import StreamingServer
from resonance.web.handlers import CommandContext
from resonance.web.handlers.playlist import cmd_playlist


def _build_context(*, with_prefetch: bool) -> tuple[CommandContext, Playlist, SimpleNamespace, int]:
    player_id = "aa:bb:cc:dd:ee:44"

    playlist = Playlist(player_id=player_id)
    playlist.add_path("/music/track0.mp3")
    playlist.add_path("/music/track1.mp3")

    player = AsyncMock()
    player.status = SimpleNamespace(volume=46, muted=False)

    player_registry = AsyncMock()
    player_registry.get_by_mac = AsyncMock(return_value=player)

    playlist_manager = MagicMock()
    playlist_manager.get = MagicMock(return_value=playlist)

    streaming_server = StreamingServer()
    streaming_server.queue_file(player_id, Path("/music/track0.mp3"))
    current_gen = streaming_server.get_stream_generation(player_id)
    assert current_gen is not None

    prefetched_generation: dict[str, int] = {}
    if with_prefetch:
        prefetched_generation[player_id] = current_gen

    resonance_server = SimpleNamespace(
        _prefetched_generation=prefetched_generation,
        suppress_track_finished_for_player=MagicMock(),
    )
    slimproto = SimpleNamespace(_resonance_server=resonance_server)

    ctx = CommandContext(
        player_id=player_id,
        music_library=MagicMock(),
        player_registry=player_registry,
        playlist_manager=playlist_manager,
        streaming_server=streaming_server,
        slimproto=slimproto,
    )
    return ctx, playlist, resonance_server, current_gen


@pytest.mark.asyncio
async def test_playlist_jump_plus_one_uses_prefetch_fast_path() -> None:
    ctx, playlist, resonance_server, current_gen = _build_context(with_prefetch=True)

    with (
        patch(
            "resonance.web.handlers.playlist_playback._start_track_stream",
            new_callable=AsyncMock,
        ) as mock_start_stream,
        patch(
            "resonance.web.handlers.playlist_playback.event_bus.publish",
            new_callable=AsyncMock,
        ) as mock_publish,
    ):
        result = await cmd_playlist(ctx, ["playlist", "jump", "+1"])

    assert result == {"_index": 1}
    assert playlist.current_index == 1
    mock_start_stream.assert_not_awaited()
    mock_publish.assert_awaited_once()
    resonance_server.suppress_track_finished_for_player.assert_called_once_with(
        ctx.player_id,
        seconds=4.0,
    )
    assert resonance_server._prefetched_generation[ctx.player_id] == current_gen


@pytest.mark.asyncio
async def test_playlist_index_plus_one_uses_prefetch_fast_path() -> None:
    ctx, playlist, resonance_server, current_gen = _build_context(with_prefetch=True)

    with (
        patch(
            "resonance.web.handlers.playlist_playback._start_track_stream",
            new_callable=AsyncMock,
        ) as mock_start_stream,
        patch(
            "resonance.web.handlers.playlist_playback.event_bus.publish",
            new_callable=AsyncMock,
        ) as mock_publish,
    ):
        result = await cmd_playlist(ctx, ["playlist", "index", "+1"])

    assert result == {"_index": 1}
    assert playlist.current_index == 1
    mock_start_stream.assert_not_awaited()
    mock_publish.assert_awaited_once()
    resonance_server.suppress_track_finished_for_player.assert_called_once_with(
        ctx.player_id,
        seconds=4.0,
    )
    assert resonance_server._prefetched_generation[ctx.player_id] == current_gen


@pytest.mark.asyncio
async def test_playlist_jump_plus_one_without_prefetch_uses_restart_path() -> None:
    ctx, playlist, resonance_server, _current_gen = _build_context(with_prefetch=False)

    with (
        patch(
            "resonance.web.handlers.playlist_playback._start_track_stream",
            new_callable=AsyncMock,
        ) as mock_start_stream,
        patch(
            "resonance.web.handlers.playlist_playback.event_bus.publish",
            new_callable=AsyncMock,
        ) as mock_publish,
    ):
        result = await cmd_playlist(ctx, ["playlist", "jump", "+1"])

    assert result == {"_index": 1}
    assert playlist.current_index == 1
    mock_start_stream.assert_awaited_once()
    mock_publish.assert_not_awaited()
    resonance_server.suppress_track_finished_for_player.assert_not_called()
