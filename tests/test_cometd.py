"""
Tests for Cometd/Bayeux protocol implementation.

These tests verify the CometdManager and /cometd endpoint work correctly
for LMS app compatibility (iPeng, Squeezer, Material Skin, etc.).
"""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from resonance.core.events import (
    Event,
    PlayerConnectedEvent,
    PlayerDisconnectedEvent,
    PlayerPlaylistEvent,
    PlayerStatusEvent,
    event_bus,
)
from resonance.web.cometd import (
    REEXEC_DEBOUNCE_DEFAULT,
    REEXEC_DEBOUNCE_JUMP,
    REEXEC_DEBOUNCE_STOP,
    CometdClient,
    CometdManager,
)

# =============================================================================
# CometdClient Tests
# =============================================================================


class TestCometdClient:
    """Tests for CometdClient dataclass."""

    def test_creation(self) -> None:
        """Test basic client creation."""
        client = CometdClient(client_id="abc12345")
        assert client.client_id == "abc12345"
        assert client.subscriptions == set()
        assert client.pending_events == []
        assert client.created_at > 0
        assert client.last_seen > 0

    def test_touch_updates_last_seen(self) -> None:
        """Test that touch() updates last_seen timestamp."""
        client = CometdClient(client_id="abc12345")
        original_time = client.last_seen
        client.touch()
        assert client.last_seen >= original_time

    def test_is_expired(self) -> None:
        """Test expiration check."""
        client = CometdClient(client_id="abc12345")
        # Not expired immediately
        assert not client.is_expired(timeout_s=180)
        # Expired with very short timeout
        assert client.is_expired(timeout_s=0)


# =============================================================================
# CometdManager Tests
# =============================================================================


class TestCometdManager:
    """Tests for CometdManager."""

    @pytest.fixture
    def manager(self) -> CometdManager:
        """Create a CometdManager for testing."""
        return CometdManager()

    @pytest.mark.asyncio
    async def test_start_stop_unsubscribes_event_bus_handler(self, manager: CometdManager) -> None:
        """CometdManager.stop() should unsubscribe the player.* event handler."""
        before = len(event_bus._handlers.get("player.*", []))  # noqa: SLF001

        await manager.start()
        after_start = len(event_bus._handlers.get("player.*", []))  # noqa: SLF001
        assert after_start == before + 1

        await manager.stop()
        after_stop = len(event_bus._handlers.get("player.*", []))  # noqa: SLF001
        assert after_stop == before

    @pytest.mark.asyncio
    async def test_handshake(self, manager: CometdManager) -> None:
        """Test /meta/handshake creates a new client session."""
        response = await manager.handshake(msg_id="1")

        assert response["channel"] == "/meta/handshake"
        assert response["successful"] is True
        assert "clientId" in response
        assert len(response["clientId"]) == 8  # 8 hex chars
        assert response["version"] == "1.0"
        assert "long-polling" in response["supportedConnectionTypes"]
        assert "advice" in response

    @pytest.mark.asyncio
    async def test_handshake_creates_client(self, manager: CometdManager) -> None:
        """Test handshake registers the client."""
        response = await manager.handshake()
        client_id = response["clientId"]

        assert await manager.is_valid_client(client_id)
        assert await manager.get_client_count() == 1

    @pytest.mark.asyncio
    async def test_connect_without_events_times_out(self, manager: CometdManager) -> None:
        """Test /meta/connect returns after timeout when no events."""
        hs = await manager.handshake()
        client_id = hs["clientId"]

        # Use very short timeout for test
        responses = await manager.connect(
            client_id=client_id,
            msg_id="2",
            timeout_ms=50,  # 50ms timeout
        )

        assert len(responses) >= 1
        connect_response = responses[0]
        assert connect_response["channel"] == "/meta/connect"
        assert connect_response["successful"] is True
        assert connect_response["clientId"] == client_id

    @pytest.mark.asyncio
    async def test_connect_returns_pending_events(self, manager: CometdManager) -> None:
        """Test /meta/connect returns pending events immediately."""
        hs = await manager.handshake()
        client_id = hs["clientId"]

        # Queue an event
        await manager.deliver_to_client(
            client_id,
            [{"channel": "/test", "data": {"msg": "hello"}}],
        )

        # Connect should return immediately with the event
        responses = await manager.connect(
            client_id=client_id,
            msg_id="2",
            timeout_ms=5000,
        )

        assert len(responses) == 2
        assert responses[0]["channel"] == "/meta/connect"
        assert responses[1]["channel"] == "/test"
        assert responses[1]["data"]["msg"] == "hello"

    @pytest.mark.asyncio
    async def test_connect_invalid_client(self, manager: CometdManager) -> None:
        """Test /meta/connect with invalid clientId returns error."""
        responses = await manager.connect(
            client_id="invalid",
            msg_id="1",
            timeout_ms=50,
        )

        assert len(responses) == 1
        assert responses[0]["successful"] is False
        assert "invalid clientId" in responses[0]["error"]
        assert responses[0]["advice"]["reconnect"] == "handshake"

    @pytest.mark.asyncio
    async def test_disconnect(self, manager: CometdManager) -> None:
        """Test /meta/disconnect removes client session."""
        hs = await manager.handshake()
        client_id = hs["clientId"]

        assert await manager.is_valid_client(client_id)

        response = await manager.disconnect(client_id=client_id, msg_id="3")

        assert response["channel"] == "/meta/disconnect"
        assert response["successful"] is True
        assert not await manager.is_valid_client(client_id)

    @pytest.mark.asyncio
    async def test_subscribe(self, manager: CometdManager) -> None:
        """Test /meta/subscribe adds channel subscriptions."""
        hs = await manager.handshake()
        client_id = hs["clientId"]

        responses = await manager.subscribe(
            client_id=client_id,
            subscriptions=["/foo/bar", "/baz/*"],
            msg_id="4",
        )

        assert len(responses) == 2
        for resp in responses:
            assert resp["channel"] == "/meta/subscribe"
            assert resp["successful"] is True
            assert resp["subscription"] in ["/foo/bar", "/baz/*"]

    @pytest.mark.asyncio
    async def test_unsubscribe(self, manager: CometdManager) -> None:
        """Test /meta/unsubscribe removes channel subscriptions."""
        hs = await manager.handshake()
        client_id = hs["clientId"]

        # Subscribe first
        await manager.subscribe(
            client_id=client_id,
            subscriptions=["/foo/bar"],
        )

        # Then unsubscribe
        responses = await manager.unsubscribe(
            client_id=client_id,
            subscriptions=["/foo/bar"],
            msg_id="5",
        )

        assert len(responses) == 1
        assert responses[0]["channel"] == "/meta/unsubscribe"
        assert responses[0]["successful"] is True

    @pytest.mark.asyncio
    async def test_slim_subscribe(self, manager: CometdManager) -> None:
        """Test /slim/subscribe for LMS-style event subscription."""
        hs = await manager.handshake()
        client_id = hs["clientId"]

        response = await manager.slim_subscribe(
            client_id=client_id,
            request=["-", ["serverstatus", "0", "50", "subscribe:60"]],
            response_channel=f"/slim/{client_id}/serverstatus",
            msg_id="6",
        )

        assert response["channel"] == "/slim/subscribe"
        assert response["successful"] is True

    @pytest.mark.asyncio
    async def test_slim_unsubscribe(self, manager: CometdManager) -> None:
        """Test /slim/unsubscribe for LMS-style event unsubscription."""
        hs = await manager.handshake()
        client_id = hs["clientId"]

        response = await manager.slim_unsubscribe(
            client_id=client_id,
            unsubscribe_channel=f"/slim/{client_id}/serverstatus",
            msg_id="7",
        )

        assert response["channel"] == "/slim/unsubscribe"
        assert response["successful"] is True

    @pytest.mark.asyncio
    async def test_deliver_event_to_subscribers(self, manager: CometdManager) -> None:
        """Test event delivery to subscribed clients."""
        hs = await manager.handshake()
        client_id = hs["clientId"]

        # Subscribe to channel
        await manager.subscribe(
            client_id=client_id,
            subscriptions=["/test/channel"],
        )

        # Deliver event
        count = await manager.deliver_event(
            channel="/test/channel",
            data={"message": "hello"},
        )

        assert count == 1

        # Check event is pending
        responses = await manager.connect(
            client_id=client_id,
            timeout_ms=50,
        )

        # Should have connect response + event
        assert len(responses) == 2
        assert responses[1]["channel"] == "/test/channel"
        assert responses[1]["data"]["message"] == "hello"

    @pytest.mark.asyncio
    async def test_wildcard_subscription(self, manager: CometdManager) -> None:
        """Test wildcard subscription patterns."""
        hs = await manager.handshake()
        client_id = hs["clientId"]

        # Subscribe to wildcard
        await manager.subscribe(
            client_id=client_id,
            subscriptions=["/player/**"],
        )

        # Deliver event on matching channel
        count = await manager.deliver_event(
            channel="/player/aa:bb:cc:dd:ee:ff/status",
            data={"state": "playing"},
        )

        assert count == 1

    @pytest.mark.asyncio
    async def test_single_wildcard_subscription(self, manager: CometdManager) -> None:
        """Test single-level wildcard subscription."""
        hs = await manager.handshake()
        client_id = hs["clientId"]

        # Subscribe to single-level wildcard
        await manager.subscribe(
            client_id=client_id,
            subscriptions=["/player/*"],
        )

        # Should match single level
        count = await manager.deliver_event(
            channel="/player/status",
            data={},
        )
        assert count == 1

        # Should NOT match nested level
        count = await manager.deliver_event(
            channel="/player/aa/status",
            data={},
        )
        assert count == 0

    @pytest.mark.asyncio
    async def test_no_match_no_delivery(self, manager: CometdManager) -> None:
        """Test that unsubscribed channels don't receive events."""
        hs = await manager.handshake()
        client_id = hs["clientId"]

        # Subscribe to one channel
        await manager.subscribe(
            client_id=client_id,
            subscriptions=["/foo"],
        )

        # Deliver to different channel
        count = await manager.deliver_event(
            channel="/bar",
            data={},
        )

        assert count == 0


# =============================================================================
# Event Bus Integration Tests
# =============================================================================


class TestCometdEventIntegration:
    """Tests for CometdManager integration with event bus."""

    @pytest.fixture
    async def manager(self) -> CometdManager:
        """Create and start a CometdManager for testing."""
        mgr = CometdManager()
        await mgr.start()
        yield mgr
        await mgr.stop()

    @pytest.mark.asyncio
    async def test_player_status_event_delivery(self, manager: CometdManager) -> None:
        """Test that player status events are delivered to subscribers."""
        hs = await manager.handshake()
        client_id = hs["clientId"]

        # Subscribe to player status channel
        await manager.subscribe(
            client_id=client_id,
            subscriptions=["/slim/serverstatus"],
        )

        # Publish a player status event
        await event_bus.publish(
            PlayerStatusEvent(
                player_id="aa:bb:cc:dd:ee:ff",
                state="playing",
                volume=80,
            )
        )

        # Small delay for async processing
        await asyncio.sleep(0.1)

        # Connect should receive the event
        responses = await manager.connect(
            client_id=client_id,
            timeout_ms=50,
        )

        # Check if we received the event
        assert len(responses) >= 1

    @pytest.mark.asyncio
    async def test_player_connected_event_delivery(self, manager: CometdManager) -> None:
        """Test that player connected events are delivered."""
        hs = await manager.handshake()
        client_id = hs["clientId"]

        await manager.subscribe(
            client_id=client_id,
            subscriptions=["/slim/serverstatus"],
        )

        await event_bus.publish(
            PlayerConnectedEvent(
                player_id="aa:bb:cc:dd:ee:ff",
                name="Living Room",
                model="squeezelite",
            )
        )

        await asyncio.sleep(0.1)

        responses = await manager.connect(
            client_id=client_id,
            timeout_ms=50,
        )

        assert len(responses) >= 1


# =============================================================================
# HTTP Endpoint Tests
# =============================================================================


class TestCometdEndpoint:
    """Tests for /cometd HTTP endpoint."""

    @pytest.fixture
    def app(self) -> FastAPI:
        """Create a test FastAPI app with Cometd route."""
        from resonance.web.cometd import CometdManager
        from resonance.web.routes.cometd import router

        app = FastAPI()
        app.state.cometd_manager = CometdManager()
        app.include_router(router)
        return app

    @pytest.fixture
    def client(self, app: FastAPI) -> TestClient:
        """Create a test client."""
        return TestClient(app)

    def test_handshake_via_http(self, client: TestClient) -> None:
        """Test /cometd handshake via HTTP POST."""
        response = client.post(
            "/cometd",
            json=[{"channel": "/meta/handshake", "id": "1"}],
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["channel"] == "/meta/handshake"
        assert data[0]["successful"] is True
        assert "clientId" in data[0]

    def test_subscribe_via_http(self, client: TestClient) -> None:
        """Test /cometd subscribe via HTTP POST."""
        # First handshake
        hs_response = client.post(
            "/cometd",
            json=[{"channel": "/meta/handshake", "id": "1"}],
        )
        client_id = hs_response.json()[0]["clientId"]

        # Then subscribe
        response = client.post(
            "/cometd",
            json=[
                {
                    "channel": "/meta/subscribe",
                    "clientId": client_id,
                    "subscription": "/slim/serverstatus",
                    "id": "2",
                }
            ],
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["channel"] == "/meta/subscribe"
        assert data[0]["successful"] is True

    def test_batch_messages(self, client: TestClient) -> None:
        """Test sending multiple messages in a batch."""
        response = client.post(
            "/cometd",
            json=[
                {"channel": "/meta/handshake", "id": "1"},
            ],
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1

        client_id = data[0]["clientId"]

        # Send batch with subscribe
        response = client.post(
            "/cometd",
            json=[
                {
                    "channel": "/meta/subscribe",
                    "clientId": client_id,
                    "subscription": ["/foo", "/bar"],
                    "id": "2",
                },
            ],
        )

        data = response.json()
        # Should have 2 subscription responses
        assert len(data) == 2

    def test_disconnect_via_http(self, client: TestClient) -> None:
        """Test /cometd disconnect via HTTP POST."""
        # Handshake
        hs_response = client.post(
            "/cometd",
            json=[{"channel": "/meta/handshake", "id": "1"}],
        )
        client_id = hs_response.json()[0]["clientId"]

        # Disconnect
        response = client.post(
            "/cometd",
            json=[
                {
                    "channel": "/meta/disconnect",
                    "clientId": client_id,
                    "id": "2",
                }
            ],
        )

        assert response.status_code == 200
        data = response.json()
        assert data[0]["channel"] == "/meta/disconnect"
        assert data[0]["successful"] is True

    def test_invalid_json(self, client: TestClient) -> None:
        """Test handling of invalid JSON."""
        response = client.post(
            "/cometd",
            content="not valid json",
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 400

    def test_empty_body(self, client: TestClient) -> None:
        """Test handling of empty message list."""
        response = client.post(
            "/cometd",
            json=[],
        )

        assert response.status_code == 400

    def test_slim_request_via_http(self, client: TestClient) -> None:
        """Test /slim/request channel."""
        # Handshake first
        hs_response = client.post(
            "/cometd",
            json=[{"channel": "/meta/handshake", "id": "1"}],
        )
        client_id = hs_response.json()[0]["clientId"]

        # Send slim request
        response = client.post(
            "/cometd",
            json=[
                {
                    "channel": "/slim/request",
                    "clientId": client_id,
                    "id": "2",
                    "data": {
                        "request": ["-", ["serverstatus", "0", "10"]],
                        "response": f"/slim/{client_id}/serverstatus",
                    },
                }
            ],
        )

        assert response.status_code == 200
        data = response.json()
        # Should have acknowledgement + result
        assert len(data) >= 1
        assert data[0]["channel"] == "/slim/request"
        assert data[0]["successful"] is True


# =============================================================================
# Debounce Tests
# =============================================================================


class TestReexecDebounce:
    """Tests for LMS-style debounced subscription re-execution.

    LMS uses killOneTimer + setTimer in Request.pm notify() to debounce
    subscription re-execution.  Resonance mirrors this with asyncio tasks.
    """

    @pytest.fixture
    async def manager(self) -> CometdManager:
        """Create and start a CometdManager for testing."""
        mgr = CometdManager()
        await mgr.start()
        yield mgr
        await mgr.stop()

    # ── Delay classification ──

    def test_delay_default_for_playing_status(self) -> None:
        """PlayerStatusEvent with state='playing' should use default delay."""
        mgr = CometdManager()
        event = PlayerStatusEvent(player_id="aa:bb:cc:dd:ee:ff", state="playing")
        assert mgr._get_reexec_delay(event) == REEXEC_DEBOUNCE_DEFAULT

    def test_delay_stop_for_stopped_status(self) -> None:
        """PlayerStatusEvent with state='stopped' should use stop delay (1.0s)."""
        mgr = CometdManager()
        event = PlayerStatusEvent(player_id="aa:bb:cc:dd:ee:ff", state="stopped")
        assert mgr._get_reexec_delay(event) == REEXEC_DEBOUNCE_STOP

    def test_delay_default_for_paused_status(self) -> None:
        """PlayerStatusEvent with state='paused' should use default delay."""
        mgr = CometdManager()
        event = PlayerStatusEvent(player_id="aa:bb:cc:dd:ee:ff", state="paused")
        assert mgr._get_reexec_delay(event) == REEXEC_DEBOUNCE_DEFAULT

    def test_delay_jump_for_playlist_index(self) -> None:
        """PlayerPlaylistEvent with action='index' should use jump delay (1.5s)."""
        mgr = CometdManager()
        event = PlayerPlaylistEvent(player_id="aa:bb:cc:dd:ee:ff", action="index")
        assert mgr._get_reexec_delay(event) == REEXEC_DEBOUNCE_JUMP

    def test_delay_jump_for_playlist_load(self) -> None:
        """PlayerPlaylistEvent with action='load' should use jump delay (1.5s)."""
        mgr = CometdManager()
        event = PlayerPlaylistEvent(player_id="aa:bb:cc:dd:ee:ff", action="load")
        assert mgr._get_reexec_delay(event) == REEXEC_DEBOUNCE_JUMP

    def test_delay_default_for_playlist_add(self) -> None:
        """PlayerPlaylistEvent with action='add' should use default delay."""
        mgr = CometdManager()
        event = PlayerPlaylistEvent(player_id="aa:bb:cc:dd:ee:ff", action="add")
        assert mgr._get_reexec_delay(event) == REEXEC_DEBOUNCE_DEFAULT

    # ── Debounce scheduling ──

    @pytest.mark.asyncio
    async def test_schedule_creates_pending_task(self, manager: CometdManager) -> None:
        """Scheduling a debounced reexec should create a pending task."""
        player_id = "aa:bb:cc:dd:ee:01"
        manager._schedule_debounced_reexec(player_id, 10.0)

        assert player_id in manager._reexec_debounce_tasks
        task = manager._reexec_debounce_tasks[player_id]
        assert not task.done()

        # Cleanup
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_schedule_cancels_previous_task(self, manager: CometdManager) -> None:
        """A new schedule for the same player should cancel the previous one."""
        player_id = "aa:bb:cc:dd:ee:02"

        manager._schedule_debounced_reexec(player_id, 10.0)
        first_task = manager._reexec_debounce_tasks[player_id]

        manager._schedule_debounced_reexec(player_id, 10.0)
        second_task = manager._reexec_debounce_tasks[player_id]

        # Give the event loop a tick to process the cancellation
        await asyncio.sleep(0)

        assert first_task.cancelled() or first_task.done()
        assert not second_task.done()

        # Cleanup
        second_task.cancel()
        try:
            await second_task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_canceled_task_does_not_clear_new_task_mapping(
        self, manager: CometdManager
    ) -> None:
        """A cancelled old timer must not delete the newer timer mapping."""
        player_id = "aa:bb:cc:dd:ee:02a"

        manager._schedule_debounced_reexec(player_id, 10.0)
        await asyncio.sleep(0)

        manager._schedule_debounced_reexec(player_id, 10.0)
        second_task = manager._reexec_debounce_tasks[player_id]

        # Let cancelled first task run its cancellation/finally path.
        await asyncio.sleep(0)

        # Mapping must still point to the newer task.
        assert manager._reexec_debounce_tasks.get(player_id) is second_task

        # Cleanup
        second_task.cancel()
        try:
            await second_task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_followup_event_still_cancels_pending_timer_after_cancellation(
        self, manager: CometdManager
    ) -> None:
        """After rapid reschedules, only one reexec should fire."""
        player_id = "aa:bb:cc:dd:ee:02b"
        reexec_count = 0

        async def _counting_reexec(pid: str) -> None:  # noqa: ARG001
            nonlocal reexec_count
            reexec_count += 1

        manager._reexecute_slim_subscriptions = _counting_reexec  # type: ignore[assignment]

        # 1) schedule long timer, 2) reschedule long timer (cancels old),
        # 3) allow cancellation cleanup, 4) follow-up short timer.
        manager._schedule_debounced_reexec(player_id, 0.10)
        await asyncio.sleep(0)
        manager._schedule_debounced_reexec(player_id, 0.10)
        await asyncio.sleep(0)
        manager._schedule_debounced_reexec(player_id, 0.05)

        await asyncio.sleep(0.20)

        assert reexec_count == 1

    @pytest.mark.asyncio
    async def test_debounce_coalesces_rapid_events(self, manager: CometdManager) -> None:
        """Rapid events for the same player should coalesce into one reexec."""
        player_id = "aa:bb:cc:dd:ee:03"
        reexec_count = 0
        original_reexec = manager._reexecute_slim_subscriptions

        async def _counting_reexec(pid: str) -> None:
            nonlocal reexec_count
            reexec_count += 1
            await original_reexec(pid)

        manager._reexecute_slim_subscriptions = _counting_reexec  # type: ignore[assignment]

        # Fire 5 events in rapid succession with short delay
        for _ in range(5):
            manager._schedule_debounced_reexec(player_id, 0.05)

        # Wait for the debounced task to fire
        await asyncio.sleep(0.15)

        assert reexec_count == 1, f"Expected 1 coalesced reexec, got {reexec_count}"

    @pytest.mark.asyncio
    async def test_different_players_debounce_independently(
        self, manager: CometdManager
    ) -> None:
        """Debounce timers for different players should be independent."""
        player_a = "aa:bb:cc:dd:ee:0a"
        player_b = "aa:bb:cc:dd:ee:0b"
        fired: list[str] = []
        original_reexec = manager._reexecute_slim_subscriptions

        async def _tracking_reexec(pid: str) -> None:
            fired.append(pid)
            await original_reexec(pid)

        manager._reexecute_slim_subscriptions = _tracking_reexec  # type: ignore[assignment]

        manager._schedule_debounced_reexec(player_a, 0.05)
        manager._schedule_debounced_reexec(player_b, 0.05)

        await asyncio.sleep(0.15)

        assert player_a in fired, "Player A reexec should have fired"
        assert player_b in fired, "Player B reexec should have fired"

    @pytest.mark.asyncio
    async def test_shorter_delay_wins_on_followup(self, manager: CometdManager) -> None:
        """A follow-up event with a shorter delay should replace the longer one.

        Example: playlist load (1.5s) followed by STMs playing (0.3s) should
        fire ~0.3s after the second event, not 1.5s after the first.
        """
        player_id = "aa:bb:cc:dd:ee:04"
        reexec_count = 0
        original_reexec = manager._reexecute_slim_subscriptions

        async def _counting_reexec(pid: str) -> None:
            nonlocal reexec_count
            reexec_count += 1

        manager._reexecute_slim_subscriptions = _counting_reexec  # type: ignore[assignment]

        # Long delay (like playlist load/jump)
        manager._schedule_debounced_reexec(player_id, 2.0)
        await asyncio.sleep(0.05)

        # Short delay (like status playing) replaces the long one
        manager._schedule_debounced_reexec(player_id, 0.05)
        await asyncio.sleep(0.15)

        assert reexec_count == 1, "Only the short-delay reexec should have fired"

    @pytest.mark.asyncio
    async def test_stop_cancels_pending_debounce_tasks(self) -> None:
        """CometdManager.stop() should cancel all pending debounce tasks."""
        mgr = CometdManager()
        await mgr.start()

        mgr._schedule_debounced_reexec("aa:bb:cc:dd:ee:05", 10.0)
        mgr._schedule_debounced_reexec("aa:bb:cc:dd:ee:06", 10.0)

        task_a = mgr._reexec_debounce_tasks.get("aa:bb:cc:dd:ee:05")
        task_b = mgr._reexec_debounce_tasks.get("aa:bb:cc:dd:ee:06")

        await mgr.stop()

        # Give the event loop a tick to process the cancellations
        await asyncio.sleep(0)

        assert mgr._reexec_debounce_tasks == {}
        assert task_a is not None and (task_a.cancelled() or task_a.done())
        assert task_b is not None and (task_b.cancelled() or task_b.done())

    # ── Integration with handle_event ──

    @pytest.mark.asyncio
    async def test_handle_event_uses_debounce(self, manager: CometdManager) -> None:
        """handle_event should schedule a debounced reexec, not an immediate one."""
        player_id = "aa:bb:cc:dd:ee:07"
        reexec_count = 0

        async def _counting_reexec(pid: str) -> None:
            nonlocal reexec_count
            reexec_count += 1

        manager._reexecute_slim_subscriptions = _counting_reexec  # type: ignore[assignment]

        event = PlayerStatusEvent(player_id=player_id, state="playing")
        await manager.handle_event(event)

        # Should NOT have fired yet (debounce pending)
        assert reexec_count == 0, "Reexec should be debounced, not immediate"
        assert player_id in manager._reexec_debounce_tasks

        # Wait for debounce to fire
        await asyncio.sleep(REEXEC_DEBOUNCE_DEFAULT + 0.1)
        assert reexec_count == 1, "Reexec should fire after debounce delay"

    @pytest.mark.asyncio
    async def test_legacy_channel_delivered_immediately(
        self, manager: CometdManager
    ) -> None:
        """Legacy channel events for web-ui should be delivered immediately,
        even though slim subscription reexec is debounced."""
        player_id = "aa:bb:cc:dd:ee:08"

        hs = await manager.handshake()
        client_id = hs["clientId"]
        await manager.subscribe(
            client_id=client_id,
            subscriptions=[f"/{player_id}/status"],
        )

        event = PlayerStatusEvent(player_id=player_id, state="playing", volume=75)
        await manager.handle_event(event)

        # Legacy event should be delivered immediately (before debounce fires)
        await asyncio.sleep(0.05)
        responses = await manager.connect(client_id=client_id, timeout_ms=50)

        status_events = [
            r for r in responses
            if r.get("channel") == f"/{player_id}/status"
        ]
        assert len(status_events) >= 1, "Legacy event should arrive immediately"

