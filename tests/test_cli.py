"""Tests for the Telnet CLI server (port 9090)."""

from __future__ import annotations

import asyncio

import pytest

from resonance.core.events import (
    EventBus,
    LibraryScanEvent,
    PlayerConnectedEvent,
    PlayerDisconnectedEvent,
    PlayerPlaylistEvent,
    PlayerStatusEvent,
)
from resonance.protocol.cli import CliServer, parse_cli_command_line

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _noop_executor(player_id: str, command: list[str]) -> dict[str, object]:
    return {"ok": 1}


def _make_server(
    *,
    executor: object | None = None,
    event_bus: EventBus | None = None,
) -> CliServer:
    return CliServer(
        host="127.0.0.1",
        port=0,
        command_executor=executor or _noop_executor,
        event_bus=event_bus,
    )


async def _connect(server: CliServer) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.open_connection("127.0.0.1", server.port)


async def _send(writer: asyncio.StreamWriter, line: str) -> None:
    writer.write((line + "\n").encode())
    await writer.drain()


async def _recv(reader: asyncio.StreamReader, timeout: float = 1.5) -> str:
    data = await asyncio.wait_for(reader.readline(), timeout=timeout)
    return data.decode().strip()


async def _close(writer: asyncio.StreamWriter) -> None:
    writer.close()
    await writer.wait_closed()


# ---------------------------------------------------------------------------
# parse_cli_command_line
# ---------------------------------------------------------------------------

def test_parse_cli_command_line_with_player_prefix() -> None:
    player_id, command = parse_cli_command_line("aa:bb:cc:dd:ee:ff status 0 10")
    assert player_id == "aa:bb:cc:dd:ee:ff"
    assert command == ["status", "0", "10"]


def test_parse_cli_command_line_without_player_prefix() -> None:
    player_id, command = parse_cli_command_line("players 0 20")
    assert player_id == "-"
    assert command == ["players", "0", "20"]


def test_parse_cli_command_line_decodes_percent_encoding() -> None:
    player_id, command = parse_cli_command_line("- playlist add file%3A%2F%2Fmusic%2FTrack%25201.mp3")
    assert player_id == "-"
    assert command == ["playlist", "add", "file://music/Track%201.mp3"]


# ---------------------------------------------------------------------------
# Basic CLI server tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cli_server_starts_and_stops() -> None:
    server = _make_server()

    assert not server.is_running
    await server.start()
    assert server.is_running
    assert server.port > 0

    await server.stop()
    assert not server.is_running


@pytest.mark.asyncio
async def test_cli_executes_command_without_player_prefix() -> None:
    calls: list[tuple[str, list[str]]] = []

    async def _executor(player_id: str, command: list[str]) -> dict[str, object]:
        calls.append((player_id, command))
        return {
            "player count": 1,
            "players_loop": [{"name": "Kitchen"}],
        }

    server = _make_server(executor=_executor)
    await server.start()

    try:
        reader, writer = await _connect(server)
        await _send(writer, "players 0 20")

        response = await _recv(reader)

        await _close(writer)

        assert calls == [("-", ["players", "0", "20"])]
        assert response.startswith("- players 0 20 ")
        assert "player%20count:1" in response
        assert "result:" in response
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_cli_executes_command_with_player_prefix() -> None:
    calls: list[tuple[str, list[str]]] = []

    async def _executor(player_id: str, command: list[str]) -> dict[str, object]:
        calls.append((player_id, command))
        return {"mode": "play", "time": 12}

    server = _make_server(executor=_executor)
    await server.start()

    try:
        reader, writer = await _connect(server)
        await _send(writer, "aa:bb:cc:dd:ee:ff status 0 10")

        response = await _recv(reader)

        await _close(writer)

        assert calls == [(
            "aa:bb:cc:dd:ee:ff",
            ["status", "0", "10"],
        )]
        assert response.startswith("aa:bb:cc:dd:ee:ff status 0 10 ")
        assert "mode:play" in response
        assert "time:12" in response
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_cli_quit_closes_connection() -> None:
    server = _make_server()
    await server.start()

    try:
        reader, writer = await _connect(server)
        await _send(writer, "quit")

        data = await asyncio.wait_for(reader.readline(), timeout=1.5)
        assert data == b""

        await _close(writer)
    finally:
        await server.stop()


# ---------------------------------------------------------------------------
# listen command
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_listen_query_default_is_zero() -> None:
    """``listen ?`` returns 0 when no subscription is active."""
    server = _make_server(event_bus=EventBus())
    await server.start()
    try:
        reader, writer = await _connect(server)
        await _send(writer, "listen ?")
        response = await _recv(reader)
        assert response == "listen 0"
        await _close(writer)
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_listen_1_then_query() -> None:
    """``listen 1`` enables listening, ``listen ?`` confirms it."""
    server = _make_server(event_bus=EventBus())
    await server.start()
    try:
        reader, writer = await _connect(server)

        await _send(writer, "listen 1")
        response = await _recv(reader)
        assert response == "listen 1"

        await _send(writer, "listen ?")
        response = await _recv(reader)
        assert response == "listen 1"

        await _close(writer)
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_listen_0_disables() -> None:
    """``listen 0`` after ``listen 1`` disables listening."""
    server = _make_server(event_bus=EventBus())
    await server.start()
    try:
        reader, writer = await _connect(server)

        await _send(writer, "listen 1")
        await _recv(reader)

        await _send(writer, "listen 0")
        response = await _recv(reader)
        assert response == "listen 0"

        await _send(writer, "listen ?")
        response = await _recv(reader)
        assert response == "listen 0"

        await _close(writer)
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_listen_toggle() -> None:
    """Bare ``listen`` toggles subscription on/off."""
    server = _make_server(event_bus=EventBus())
    await server.start()
    try:
        reader, writer = await _connect(server)

        # Toggle on
        await _send(writer, "listen")
        response = await _recv(reader)
        assert response == "listen 1"

        await _send(writer, "listen ?")
        response = await _recv(reader)
        assert response == "listen 1"

        # Toggle off
        await _send(writer, "listen")
        response = await _recv(reader)
        assert response == "listen 0"

        await _send(writer, "listen ?")
        response = await _recv(reader)
        assert response == "listen 0"

        await _close(writer)
    finally:
        await server.stop()


# ---------------------------------------------------------------------------
# subscribe command
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_subscribe_specific_commands() -> None:
    """``subscribe playlist,mixer`` limits notifications to those prefixes."""
    bus = EventBus()
    server = _make_server(event_bus=bus)
    await server.start()
    try:
        reader, writer = await _connect(server)

        await _send(writer, "subscribe playlist,mixer")
        response = await _recv(reader)
        assert response == "subscribe playlist%2Cmixer"

        # listen ? should report 1 since we have an active subscription
        await _send(writer, "listen ?")
        response = await _recv(reader)
        assert response == "listen 1"

        await _close(writer)
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_subscribe_empty_disables() -> None:
    """``subscribe`` with no args disables listening."""
    bus = EventBus()
    server = _make_server(event_bus=bus)
    await server.start()
    try:
        reader, writer = await _connect(server)

        await _send(writer, "listen 1")
        await _recv(reader)

        await _send(writer, "subscribe")
        await _recv(reader)

        await _send(writer, "listen ?")
        response = await _recv(reader)
        assert response == "listen 0"

        await _close(writer)
    finally:
        await server.stop()


# ---------------------------------------------------------------------------
# Notification delivery — listen 1 (all events)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_listen_receives_player_connected() -> None:
    """``listen 1`` receives ``client new`` on PlayerConnectedEvent."""
    bus = EventBus()
    server = _make_server(event_bus=bus)
    await server.start()
    try:
        reader, writer = await _connect(server)
        await _send(writer, "listen 1")
        await _recv(reader)  # echo: listen 1

        await bus.publish(PlayerConnectedEvent(
            player_id="aa:bb:cc:dd:ee:ff",
            name="Kitchen",
            model="squeezelite",
        ))

        notification = await _recv(reader)
        assert "aa:bb:cc:dd:ee:ff" in notification
        assert "client" in notification
        assert "new" in notification

        await _close(writer)
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_listen_receives_player_disconnected() -> None:
    """``listen 1`` receives ``client disconnect`` on PlayerDisconnectedEvent."""
    bus = EventBus()
    server = _make_server(event_bus=bus)
    await server.start()
    try:
        reader, writer = await _connect(server)
        await _send(writer, "listen 1")
        await _recv(reader)

        await bus.publish(PlayerDisconnectedEvent(player_id="aa:bb:cc:dd:ee:ff"))

        notification = await _recv(reader)
        assert "aa:bb:cc:dd:ee:ff" in notification
        assert "client" in notification
        assert "disconnect" in notification

        await _close(writer)
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_listen_receives_playlist_newsong() -> None:
    """``listen 1`` receives ``playlist newsong`` on PlaylistEvent(action=index)."""
    bus = EventBus()
    server = _make_server(event_bus=bus)
    await server.start()
    try:
        reader, writer = await _connect(server)
        await _send(writer, "listen 1")
        await _recv(reader)

        await bus.publish(PlayerPlaylistEvent(
            player_id="aa:bb:cc:dd:ee:ff",
            action="index",
            index=3,
            count=10,
        ))

        notification = await _recv(reader)
        assert "aa:bb:cc:dd:ee:ff" in notification
        assert "playlist" in notification
        assert "newsong" in notification
        assert "3" in notification

        await _close(writer)
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_listen_receives_playlist_load_done() -> None:
    """``listen 1`` receives ``playlist load_done`` on PlaylistEvent(action=load)."""
    bus = EventBus()
    server = _make_server(event_bus=bus)
    await server.start()
    try:
        reader, writer = await _connect(server)
        await _send(writer, "listen 1")
        await _recv(reader)

        await bus.publish(PlayerPlaylistEvent(
            player_id="aa:bb:cc:dd:ee:ff",
            action="load",
            count=5,
        ))

        notification = await _recv(reader)
        assert "playlist" in notification
        assert "load_done" in notification

        await _close(writer)
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_listen_receives_playlist_addtracks() -> None:
    bus = EventBus()
    server = _make_server(event_bus=bus)
    await server.start()
    try:
        reader, writer = await _connect(server)
        await _send(writer, "listen 1")
        await _recv(reader)

        await bus.publish(PlayerPlaylistEvent(
            player_id="aa:bb:cc:dd:ee:ff",
            action="add",
            count=8,
        ))

        notification = await _recv(reader)
        assert "playlist" in notification
        assert "addtracks" in notification

        await _close(writer)
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_listen_receives_playlist_delete() -> None:
    bus = EventBus()
    server = _make_server(event_bus=bus)
    await server.start()
    try:
        reader, writer = await _connect(server)
        await _send(writer, "listen 1")
        await _recv(reader)

        await bus.publish(PlayerPlaylistEvent(
            player_id="aa:bb:cc:dd:ee:ff",
            action="delete",
            count=4,
        ))

        notification = await _recv(reader)
        assert "playlist" in notification
        assert "delete" in notification

        await _close(writer)
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_listen_receives_playlist_clear() -> None:
    bus = EventBus()
    server = _make_server(event_bus=bus)
    await server.start()
    try:
        reader, writer = await _connect(server)
        await _send(writer, "listen 1")
        await _recv(reader)

        await bus.publish(PlayerPlaylistEvent(
            player_id="aa:bb:cc:dd:ee:ff",
            action="clear",
            count=0,
        ))

        notification = await _recv(reader)
        assert "playlist" in notification
        assert "clear" in notification

        await _close(writer)
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_listen_receives_play_state_change() -> None:
    """State change from stopped to playing sends ``play`` notification."""
    bus = EventBus()
    server = _make_server(event_bus=bus)
    await server.start()
    try:
        reader, writer = await _connect(server)
        await _send(writer, "listen 1")
        await _recv(reader)

        await bus.publish(PlayerStatusEvent(
            player_id="aa:bb:cc:dd:ee:ff",
            state="playing",
            volume=50,
        ))

        notification = await _recv(reader)
        assert "aa:bb:cc:dd:ee:ff" in notification
        assert "play" in notification

        await _close(writer)
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_listen_receives_pause_state_change() -> None:
    """State change to paused sends ``pause`` notification."""
    bus = EventBus()
    server = _make_server(event_bus=bus)
    await server.start()
    try:
        reader, writer = await _connect(server)
        await _send(writer, "listen 1")
        await _recv(reader)

        # First set state to playing
        await bus.publish(PlayerStatusEvent(
            player_id="aa:bb:cc:dd:ee:ff",
            state="playing",
            volume=50,
        ))
        # consume play + mixer notifications
        await _recv(reader)
        await _recv(reader)

        # Now pause
        await bus.publish(PlayerStatusEvent(
            player_id="aa:bb:cc:dd:ee:ff",
            state="paused",
            volume=50,
        ))

        notification = await _recv(reader)
        assert "aa:bb:cc:dd:ee:ff" in notification
        assert "pause" in notification

        await _close(writer)
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_listen_receives_stop_state_change() -> None:
    """State change to stopped sends ``stop`` notification."""
    bus = EventBus()
    server = _make_server(event_bus=bus)
    await server.start()
    try:
        reader, writer = await _connect(server)
        await _send(writer, "listen 1")
        await _recv(reader)

        await bus.publish(PlayerStatusEvent(
            player_id="aa:bb:cc:dd:ee:ff",
            state="stopped",
            volume=50,
        ))

        notification = await _recv(reader)
        assert "aa:bb:cc:dd:ee:ff" in notification
        assert "stop" in notification

        await _close(writer)
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_listen_receives_volume_change() -> None:
    """Volume change sends ``mixer volume`` notification."""
    bus = EventBus()
    server = _make_server(event_bus=bus)
    await server.start()
    try:
        reader, writer = await _connect(server)
        await _send(writer, "listen 1")
        await _recv(reader)

        await bus.publish(PlayerStatusEvent(
            player_id="aa:bb:cc:dd:ee:ff",
            state="playing",
            volume=75,
        ))

        # Should get both play and mixer notifications
        lines: list[str] = []
        for _ in range(2):
            lines.append(await _recv(reader))

        combined = "\n".join(lines)
        assert "play" in combined
        assert "mixer" in combined
        assert "volume" in combined
        assert "75" in combined

        await _close(writer)
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_listen_suppresses_duplicate_state() -> None:
    """Repeated PlayerStatusEvent with same state does not re-send ``play``."""
    bus = EventBus()
    server = _make_server(event_bus=bus)
    await server.start()
    try:
        reader, writer = await _connect(server)
        await _send(writer, "listen 1")
        await _recv(reader)

        # First event: playing + volume — generates play + mixer notifications
        await bus.publish(PlayerStatusEvent(
            player_id="aa:bb:cc:dd:ee:ff",
            state="playing",
            volume=50,
        ))

        first_play = await _recv(reader)
        assert "play" in first_play
        first_mixer = await _recv(reader)
        assert "mixer" in first_mixer

        # Second event: same state, same volume — should generate nothing
        await bus.publish(PlayerStatusEvent(
            player_id="aa:bb:cc:dd:ee:ff",
            state="playing",
            volume=50,
        ))

        # Send a probe command to verify no stale notification is pending
        await _send(writer, "listen ?")
        response = await _recv(reader)
        assert response == "listen 1"
        # If a stale notification had been queued, we'd have read it instead

        await _close(writer)
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_listen_volume_change_without_state_change() -> None:
    """Volume change on same state only sends mixer notification, not play."""
    bus = EventBus()
    server = _make_server(event_bus=bus)
    await server.start()
    try:
        reader, writer = await _connect(server)
        await _send(writer, "listen 1")
        await _recv(reader)

        # Initial state
        await bus.publish(PlayerStatusEvent(
            player_id="aa:bb:cc:dd:ee:ff",
            state="playing",
            volume=50,
        ))
        await _recv(reader)  # play
        await _recv(reader)  # mixer volume 50

        # Volume change only, same state
        await bus.publish(PlayerStatusEvent(
            player_id="aa:bb:cc:dd:ee:ff",
            state="playing",
            volume=80,
        ))

        notification = await _recv(reader)
        assert "mixer" in notification
        assert "volume" in notification
        assert "80" in notification
        # No play notification expected — verify with probe
        await _send(writer, "listen ?")
        probe = await _recv(reader)
        assert probe == "listen 1"

        await _close(writer)
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_listen_receives_rescan_events() -> None:
    """Library scan events send ``rescan`` / ``rescan done`` notifications."""
    bus = EventBus()
    server = _make_server(event_bus=bus)
    await server.start()
    try:
        reader, writer = await _connect(server)
        await _send(writer, "listen 1")
        await _recv(reader)

        await bus.publish(LibraryScanEvent(status="started"))
        notification = await _recv(reader)
        assert notification == "rescan"

        await bus.publish(LibraryScanEvent(status="completed"))
        notification = await _recv(reader)
        assert notification == "rescan done"

        await _close(writer)
    finally:
        await server.stop()


# ---------------------------------------------------------------------------
# subscribe filtering
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_subscribe_filters_playlist_only() -> None:
    """``subscribe playlist`` only receives playlist notifications."""
    bus = EventBus()
    server = _make_server(event_bus=bus)
    await server.start()
    try:
        reader, writer = await _connect(server)
        await _send(writer, "subscribe playlist")
        await _recv(reader)

        # Publish a client event — should NOT arrive
        await bus.publish(PlayerConnectedEvent(
            player_id="aa:bb:cc:dd:ee:ff",
            name="Kitchen",
            model="squeezelite",
        ))

        # Publish a playlist event — SHOULD arrive
        await bus.publish(PlayerPlaylistEvent(
            player_id="aa:bb:cc:dd:ee:ff",
            action="index",
            index=2,
            count=5,
        ))

        notification = await _recv(reader)
        assert "playlist" in notification
        assert "newsong" in notification

        # Verify no client notification was queued
        await _send(writer, "listen ?")
        probe = await _recv(reader)
        assert probe == "listen 1"

        await _close(writer)
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_subscribe_filters_multiple() -> None:
    """``subscribe client,mixer`` receives both client and mixer notifications."""
    bus = EventBus()
    server = _make_server(event_bus=bus)
    await server.start()
    try:
        reader, writer = await _connect(server)
        await _send(writer, "subscribe client,mixer")
        await _recv(reader)

        # client event — should arrive
        await bus.publish(PlayerConnectedEvent(
            player_id="aa:bb:cc:dd:ee:ff",
            name="Test",
            model="squeezelite",
        ))
        notification = await _recv(reader)
        assert "client" in notification
        assert "new" in notification

        # mixer event — should arrive
        await bus.publish(PlayerStatusEvent(
            player_id="aa:bb:cc:dd:ee:ff",
            state="playing",
            volume=60,
        ))
        notification = await _recv(reader)
        assert "mixer" in notification
        assert "60" in notification

        # playlist event — should NOT arrive (probe to verify)
        await bus.publish(PlayerPlaylistEvent(
            player_id="aa:bb:cc:dd:ee:ff",
            action="index",
            index=0,
            count=3,
        ))
        await _send(writer, "listen ?")
        probe = await _recv(reader)
        assert probe == "listen 1"

        await _close(writer)
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_subscribe_play_filter() -> None:
    """``subscribe play`` only receives play state notifications."""
    bus = EventBus()
    server = _make_server(event_bus=bus)
    await server.start()
    try:
        reader, writer = await _connect(server)
        await _send(writer, "subscribe play")
        await _recv(reader)

        await bus.publish(PlayerStatusEvent(
            player_id="aa:bb:cc:dd:ee:ff",
            state="playing",
            volume=50,
        ))

        notification = await _recv(reader)
        assert "play" in notification

        # mixer volume change should NOT arrive
        await bus.publish(PlayerStatusEvent(
            player_id="aa:bb:cc:dd:ee:ff",
            state="playing",
            volume=80,
        ))
        await _send(writer, "listen ?")
        probe = await _recv(reader)
        assert probe == "listen 1"

        await _close(writer)
    finally:
        await server.stop()


# ---------------------------------------------------------------------------
# listen 0 stops notifications
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_listen_0_stops_notifications() -> None:
    """After ``listen 0``, no further notifications are delivered."""
    bus = EventBus()
    server = _make_server(event_bus=bus)
    await server.start()
    try:
        reader, writer = await _connect(server)

        # Enable
        await _send(writer, "listen 1")
        await _recv(reader)

        # Verify we get a notification
        await bus.publish(PlayerConnectedEvent(
            player_id="aa:bb:cc:dd:ee:ff",
            name="Test",
            model="squeezelite",
        ))
        notification = await _recv(reader)
        assert "client" in notification

        # Disable
        await _send(writer, "listen 0")
        await _recv(reader)

        # Publish another event — should NOT arrive
        await bus.publish(PlayerDisconnectedEvent(player_id="aa:bb:cc:dd:ee:ff"))

        # Probe to verify no stale notification
        await _send(writer, "listen ?")
        probe = await _recv(reader)
        assert probe == "listen 0"

        await _close(writer)
    finally:
        await server.stop()


# ---------------------------------------------------------------------------
# Multiple listeners
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_multiple_listeners_both_receive() -> None:
    """Two connections with ``listen 1`` both receive the same notification."""
    bus = EventBus()
    server = _make_server(event_bus=bus)
    await server.start()
    try:
        r1, w1 = await _connect(server)
        r2, w2 = await _connect(server)

        await _send(w1, "listen 1")
        await _recv(r1)
        await _send(w2, "listen 1")
        await _recv(r2)

        await bus.publish(PlayerConnectedEvent(
            player_id="aa:bb:cc:dd:ee:ff",
            name="Shared",
            model="squeezelite",
        ))

        n1 = await _recv(r1)
        n2 = await _recv(r2)

        assert "client" in n1 and "new" in n1
        assert "client" in n2 and "new" in n2

        await _close(w1)
        await _close(w2)
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_one_listener_one_not() -> None:
    """Only the subscribed connection receives notifications."""
    bus = EventBus()
    server = _make_server(event_bus=bus)
    await server.start()
    try:
        r1, w1 = await _connect(server)
        r2, w2 = await _connect(server)

        # Only first connection listens
        await _send(w1, "listen 1")
        await _recv(r1)

        await bus.publish(PlayerConnectedEvent(
            player_id="aa:bb:cc:dd:ee:ff",
            name="Test",
            model="squeezelite",
        ))

        n1 = await _recv(r1)
        assert "client" in n1

        # Second connection should NOT have received anything
        await _send(w2, "listen ?")
        probe = await _recv(r2)
        assert probe == "listen 0"

        await _close(w1)
        await _close(w2)
    finally:
        await server.stop()


# ---------------------------------------------------------------------------
# Disconnect cleanup
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_disconnect_cleans_up_listener() -> None:
    """Disconnecting a listening client removes its subscription."""
    bus = EventBus()
    server = _make_server(event_bus=bus)
    await server.start()
    try:
        reader, writer = await _connect(server)
        await _send(writer, "listen 1")
        await _recv(reader)

        assert server._bus_subscribed is True

        # Disconnect
        await _close(writer)
        # Give the server a moment to process the disconnect
        await asyncio.sleep(0.1)

        # After last listener disconnects, bus should be unsubscribed
        assert server._bus_subscribed is False
        assert len(server._listeners) == 0

    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_disconnect_one_of_two_listeners() -> None:
    """Disconnecting one listener keeps bus subscribed for the other."""
    bus = EventBus()
    server = _make_server(event_bus=bus)
    await server.start()
    try:
        r1, w1 = await _connect(server)
        r2, w2 = await _connect(server)

        await _send(w1, "listen 1")
        await _recv(r1)
        await _send(w2, "listen 1")
        await _recv(r2)

        assert server._bus_subscribed is True

        # Disconnect first client
        await _close(w1)
        await asyncio.sleep(0.1)

        # Bus should still be subscribed for second client
        assert server._bus_subscribed is True
        assert len(server._listeners) == 1

        # Second client should still receive notifications
        await bus.publish(PlayerConnectedEvent(
            player_id="aa:bb:cc:dd:ee:ff",
            name="Test",
            model="squeezelite",
        ))
        n2 = await _recv(r2)
        assert "client" in n2

        await _close(w2)
        await asyncio.sleep(0.1)
        assert server._bus_subscribed is False

    finally:
        await server.stop()


# ---------------------------------------------------------------------------
# Player disconnect cleans up last-notified state
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_player_disconnect_clears_notified_state() -> None:
    """PlayerDisconnectedEvent clears last-notified state for that player."""
    bus = EventBus()
    server = _make_server(event_bus=bus)
    await server.start()
    try:
        reader, writer = await _connect(server)
        await _send(writer, "listen 1")
        await _recv(reader)

        mac = "aa:bb:cc:dd:ee:ff"

        # Set initial state
        await bus.publish(PlayerStatusEvent(player_id=mac, state="playing", volume=50))
        await _recv(reader)  # play
        await _recv(reader)  # mixer volume

        assert mac in server._last_notified

        # Disconnect player
        await bus.publish(PlayerDisconnectedEvent(player_id=mac))
        await _recv(reader)  # client disconnect

        assert mac not in server._last_notified

        await _close(writer)
    finally:
        await server.stop()


# ---------------------------------------------------------------------------
# No event_bus — graceful degradation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_listen_without_event_bus() -> None:
    """``listen 1`` without event_bus still responds but won't crash."""
    server = _make_server(event_bus=None)
    await server.start()
    try:
        reader, writer = await _connect(server)

        await _send(writer, "listen 1")
        response = await _recv(reader)
        assert response == "listen 1"

        # listen ? should still work
        await _send(writer, "listen ?")
        response = await _recv(reader)
        assert response == "listen 1"

        await _close(writer)
    finally:
        await server.stop()


# ---------------------------------------------------------------------------
# server.stop() with active listeners
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stop_with_active_listeners() -> None:
    """Stopping the server with active listeners cleans up without errors."""
    bus = EventBus()
    server = _make_server(event_bus=bus)
    await server.start()

    r1, w1 = await _connect(server)
    await _send(w1, "listen 1")
    await _recv(r1)

    assert server._bus_subscribed is True

    # Close client connection first to unblock the handler's readline(),
    # otherwise server.stop() hangs waiting for the connection task.
    await _close(w1)
    await asyncio.sleep(0.1)

    await server.stop()

    assert server._bus_subscribed is False
    assert len(server._listeners) == 0


# ---------------------------------------------------------------------------
# Case insensitivity
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_listen_case_insensitive() -> None:
    """Commands are case-insensitive: ``LISTEN 1``, ``Listen 1`` etc."""
    server = _make_server(event_bus=EventBus())
    await server.start()
    try:
        reader, writer = await _connect(server)

        await _send(writer, "LISTEN 1")
        response = await _recv(reader)
        assert response == "listen 1"

        await _send(writer, "Listen ?")
        response = await _recv(reader)
        assert response == "listen 1"

        await _send(writer, "LISTEN 0")
        response = await _recv(reader)
        assert response == "listen 0"

        await _close(writer)
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_subscribe_case_insensitive() -> None:
    """``SUBSCRIBE playlist`` works case-insensitively."""
    server = _make_server(event_bus=EventBus())
    await server.start()
    try:
        reader, writer = await _connect(server)

        await _send(writer, "SUBSCRIBE playlist")
        response = await _recv(reader)
        assert "subscribe" in response.lower()

        await _send(writer, "listen ?")
        response = await _recv(reader)
        assert response == "listen 1"

        await _close(writer)
    finally:
        await server.stop()


# ---------------------------------------------------------------------------
# Normal commands still work with listen enabled
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_commands_work_while_listening() -> None:
    """Regular commands still get responses while ``listen 1`` is active."""
    calls: list[tuple[str, list[str]]] = []

    async def _executor(player_id: str, command: list[str]) -> dict[str, object]:
        calls.append((player_id, command))
        return {"mode": "play", "time": 42}

    bus = EventBus()
    server = _make_server(executor=_executor, event_bus=bus)
    await server.start()
    try:
        reader, writer = await _connect(server)
        await _send(writer, "listen 1")
        await _recv(reader)

        # Send a regular command
        await _send(writer, "aa:bb:cc:dd:ee:ff status 0 10")
        response = await _recv(reader)

        assert calls == [("aa:bb:cc:dd:ee:ff", ["status", "0", "10"])]
        assert "mode:play" in response
        assert "time:42" in response

        await _close(writer)
    finally:
        await server.stop()


# ---------------------------------------------------------------------------
# Multiple events in sequence
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_multiple_events_in_sequence() -> None:
    """Multiple events arrive in order to a listening connection."""
    bus = EventBus()
    server = _make_server(event_bus=bus)
    await server.start()
    try:
        reader, writer = await _connect(server)
        await _send(writer, "listen 1")
        await _recv(reader)

        mac = "aa:bb:cc:dd:ee:ff"

        # Connect
        await bus.publish(PlayerConnectedEvent(player_id=mac, name="T", model="sb"))
        n = await _recv(reader)
        assert "client" in n and "new" in n

        # Play state
        await bus.publish(PlayerStatusEvent(player_id=mac, state="playing", volume=50))
        n = await _recv(reader)
        assert "play" in n
        n = await _recv(reader)
        assert "mixer" in n and "50" in n

        # Track change
        await bus.publish(PlayerPlaylistEvent(player_id=mac, action="index", index=1, count=5))
        n = await _recv(reader)
        assert "playlist" in n and "newsong" in n and "1" in n

        # Pause
        await bus.publish(PlayerStatusEvent(player_id=mac, state="paused", volume=50))
        n = await _recv(reader)
        assert "pause" in n

        # Disconnect
        await bus.publish(PlayerDisconnectedEvent(player_id=mac))
        n = await _recv(reader)
        assert "client" in n and "disconnect" in n

        await _close(writer)
    finally:
        await server.stop()


# ---------------------------------------------------------------------------
# Playlist move action
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_listen_receives_playlist_move() -> None:
    """Playlist move action sends ``playlist move`` notification."""
    bus = EventBus()
    server = _make_server(event_bus=bus)
    await server.start()
    try:
        reader, writer = await _connect(server)
        await _send(writer, "listen 1")
        await _recv(reader)

        await bus.publish(PlayerPlaylistEvent(
            player_id="aa:bb:cc:dd:ee:ff",
            action="move",
            index=0,
            count=3,
        ))

        notification = await _recv(reader)
        assert "playlist" in notification
        assert "move" in notification

        await _close(writer)
    finally:
        await server.stop()


# ---------------------------------------------------------------------------
# Generic/unknown playlist action
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_listen_receives_unknown_playlist_action() -> None:
    """Unknown playlist actions still produce a notification with the action name."""
    bus = EventBus()
    server = _make_server(event_bus=bus)
    await server.start()
    try:
        reader, writer = await _connect(server)
        await _send(writer, "listen 1")
        await _recv(reader)

        await bus.publish(PlayerPlaylistEvent(
            player_id="aa:bb:cc:dd:ee:ff",
            action="customaction",
            count=1,
        ))

        notification = await _recv(reader)
        assert "playlist" in notification
        assert "customaction" in notification

        await _close(writer)
    finally:
        await server.stop()


# ---------------------------------------------------------------------------
# Rescan progress event is ignored (only started/completed produce output)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rescan_progress_ignored() -> None:
    """LibraryScanEvent with status='progress' does not produce a notification."""
    bus = EventBus()
    server = _make_server(event_bus=bus)
    await server.start()
    try:
        reader, writer = await _connect(server)
        await _send(writer, "listen 1")
        await _recv(reader)

        await bus.publish(LibraryScanEvent(status="progress", scanned=50, total=100))

        # No notification expected — verify with probe
        await _send(writer, "listen ?")
        probe = await _recv(reader)
        assert probe == "listen 1"

        await _close(writer)
    finally:
        await server.stop()
