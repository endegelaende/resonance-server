from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from resonance.web.handlers import CommandContext
from resonance.web.handlers.status import cmd_displaystatus


@pytest.mark.asyncio
async def test_displaystatus_showbriefly_is_silent_even_with_current_track() -> None:
    player = SimpleNamespace(
        name="Squeezebox Radio",
        status=SimpleNamespace(state=SimpleNamespace(name="PLAYING")),
    )

    current_track = SimpleNamespace(
        title="Track 1",
        artist_name="Artist",
        album_title="Album",
    )

    player_registry = AsyncMock()
    player_registry.get_by_mac = AsyncMock(return_value=player)

    playlist_manager = SimpleNamespace(
        get=MagicMock(return_value=SimpleNamespace(current_track=current_track)),
    )

    ctx = CommandContext(
        player_id="00:04:20:26:84:ae",
        music_library=MagicMock(),
        player_registry=player_registry,
        playlist_manager=playlist_manager,
    )

    result = await cmd_displaystatus(ctx, ["displaystatus", "subscribe:showbriefly"])

    assert result == {}


@pytest.mark.asyncio
async def test_displaystatus_without_subscribe_is_empty() -> None:
    ctx = CommandContext(
        player_id="-",
        music_library=MagicMock(),
        player_registry=AsyncMock(),
        playlist_manager=MagicMock(),
    )

    result = await cmd_displaystatus(ctx, ["displaystatus"])

    assert result == {}
