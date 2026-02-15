"""
Tests for resonance.web module (FastAPI + JSON-RPC).

These tests verify:
- FastAPI application setup
- JSON-RPC endpoint functionality
- REST API endpoints
- Proper integration with MusicLibrary and PlayerRegistry

Note on LMS compatibility:
- Some JSON-RPC commands accept `tags:` which controls which fields are returned
  in `*_loop` payloads (field gating). Tests below validate that behavior for a
  minimal, stable subset.
- Phase 2 (small start): year filtering (e.g. "year:2020") should be accepted by
  JSON-RPC commands like `artists`, `albums`, and `titles` and restrict results accordingly.
- Combined filters should behave LMS-like: filters stack (AND), and `count` remains total matches.
- Phase 2 (bigger): genre filtering (e.g. "genre_id:123") should be accepted by
  JSON-RPC commands like `artists`, `albums`, and `titles` and restrict results accordingly.

Seeking notes:
- Direct-stream seeking follows LMS-style time->offset mapping for MP3/FLAC/OGG.
- For MP3, we try to skip ID3v2 tag bytes (so early seeks don't land in metadata).
- If duration is unknown, seek offset stays at stream start (no guessed bitrate fallback).

Playback notes:
- LMS-like behavior: `play` from STOP with a non-empty queue should start
  streaming the current playlist item (not just "resume").
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from resonance.core.library import MusicLibrary
from resonance.core.library_db import LibraryDb, UpsertTrack
from resonance.player.registry import PlayerRegistry
from resonance.web.routes import artwork as artwork_routes
from resonance.web.server import WebServer

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
async def db() -> LibraryDb:
    """Create an in-memory database for testing."""
    db = LibraryDb(":memory:")
    await db.open()
    await db.ensure_schema()
    yield db
    await db.close()


@pytest.fixture
async def library(db: LibraryDb) -> MusicLibrary:
    """Create a MusicLibrary with in-memory DB."""
    lib = MusicLibrary(db=db, music_root=None)
    await lib.initialize()
    return lib


@pytest.fixture
def registry() -> PlayerRegistry:
    """Create a PlayerRegistry for testing."""
    return PlayerRegistry()


@pytest.fixture
async def web_server(registry: PlayerRegistry, library: MusicLibrary) -> WebServer:
    """Create a WebServer instance for testing."""
    server = WebServer(player_registry=registry, music_library=library)
    return server


@pytest.fixture
async def client(web_server: WebServer) -> AsyncClient:
    """Create an async HTTP client for testing."""
    transport = ASGITransport(app=web_server.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# =============================================================================
# Health Check Tests
# =============================================================================


class TestHealthCheck:
    """Tests for the health check endpoint."""

    async def test_health_check(self, client: AsyncClient) -> None:
        """Test that health check returns ok."""
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["server"] == "resonance"


# =============================================================================
# Server Status Tests
# =============================================================================


class TestServerStatus:
    """Tests for the server status endpoint."""

    async def test_server_status(self, client: AsyncClient) -> None:
        """Test that server status returns expected info."""
        response = await client.get("/api/status")
        assert response.status_code == 200
        data = response.json()
        assert data["server"] == "resonance"
        assert data["version"] == "0.1.0"
        assert data["players_connected"] == 0
        assert data["library_initialized"] is True


# =============================================================================
# JSON-RPC Tests
# =============================================================================


@pytest.mark.asyncio
async def test_jsonrpc_play_from_stop_starts_current_playlist_track(
    web_server: WebServer,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Regression: If the player is STOPPED and there is a non-empty playlist,
    issuing slim.request(player_id, ["play"]) must start streaming the
    *current* playlist item (LMS semantics).

    We intentionally set current_index != 0 to catch "always start at 0" bugs.
    """
    player_id = "aa:bb:cc:dd:ee:ff"

    # Sentinel track returned by playlist.play(index)
    class _SentinelTrack:
        id = 424242
        path = "file:///music/sentinel.flac"
        title = "Sentinel"
        artist_name = "Test Artist"
        album_title = "Test Album"
        duration_ms = 123000

    sentinel_track = _SentinelTrack()

    started: dict[str, Any] = {}

    # Patch the stream-start function used by playlist/index and by the fixed `play`.
    from resonance.web.handlers import playlist as playlist_handler

    async def _fake_start_track_stream(ctx: Any, player: Any, track: Any) -> None:
        started["start_track_stream_called"] = True
        started["started_track_id"] = getattr(track, "id", None)
        started["started_track_path"] = getattr(track, "path", None)

    monkeypatch.setattr(
        playlist_handler,
        "_start_track_stream",
        _fake_start_track_stream,
        raising=True,
    )

    # Fake playlist with current_index != 0
    class _FakePlaylist:
        def __init__(self) -> None:
            self.current_index = 1
            self._tracks = [object(), object()]  # len() must be > 0

        def __len__(self) -> int:
            return len(self._tracks)

        def play(self, index: int) -> Any:
            started["playlist_play_index"] = index
            self.current_index = index
            return sentinel_track

    class _FakePlaylistManager:
        def get(self, pid: str) -> _FakePlaylist:
            assert pid == player_id
            return _FakePlaylist()

    # Fake STOPPED player returned by registry.get_by_mac()
    class _FakeState:
        def __init__(self, name: str) -> None:
            self.name = name

    class _FakeStatus:
        def __init__(self, state_name: str) -> None:
            self.state = _FakeState(state_name)

    class _FakePlayer:
        def __init__(self) -> None:
            self.status = _FakeStatus("STOPPED")

        async def play(self) -> None:
            # If server incorrectly does resume-only, this might be called,
            # but stream-start must happen for this regression to pass.
            started["player_play_called"] = True

    class _FakePlayerRegistry:
        async def get_by_mac(self, mac: str):
            assert mac == player_id
            return _FakePlayer()

    # Inject fakes into the JSON-RPC handler dependencies used to build CommandContext.
    #
    # WebServer wires these dependencies into `web_server.jsonrpc_handler` at init time,
    # so patching `web_server.player_registry` / `web_server.playlist_manager` alone
    # does not affect JSON-RPC command execution.
    web_server.jsonrpc_handler.player_registry = _FakePlayerRegistry()
    web_server.jsonrpc_handler.playlist_manager = _FakePlaylistManager()

    # Act: issue "play" via JSON-RPC for this player.
    response = await client.post(
        "/jsonrpc.js",
        json={
            "id": 9999,
            "method": "slim.request",
            "params": [player_id, ["play"]],
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert "result" in payload

    assert started.get("start_track_stream_called") is True, started
    assert started.get("playlist_play_index") == 1, started
    assert started.get("started_track_id") == sentinel_track.id, started
    assert started.get("started_track_path") == sentinel_track.path, started


class TestJsonRpc:
    """Tests for the JSON-RPC endpoint."""

    async def test_jsonrpc_serverstatus(self, client: AsyncClient) -> None:
        """Test serverstatus command via JSON-RPC."""
        response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 1,
                "method": "slim.request",
                "params": ["-", ["serverstatus"]],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == 1
        assert data["method"] == "slim.request"
        assert "result" in data
        assert data["result"]["version"] == "7.999.999"  # Required for firmware compatibility

    async def test_jsonrpc_players_empty(self, client: AsyncClient) -> None:
        """Test players command with no connected players."""
        response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 2,
                "method": "slim.request",
                "params": ["-", ["players", 0, 100]],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["result"]["count"] == 0
        assert data["result"]["players_loop"] == []

    async def test_jsonrpc_alternative_endpoint(self, client: AsyncClient) -> None:
        """Test that /jsonrpc (without .js) also works."""
        response = await client.post(
            "/jsonrpc",
            json={
                "id": 3,
                "method": "slim.request",
                "params": ["-", ["serverstatus"]],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "result" in data

    async def test_jsonrpc_unknown_method(self, client: AsyncClient) -> None:
        """Test that unknown method returns error."""
        response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 4,
                "method": "unknown.method",
                "params": [],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == -32601

    async def test_jsonrpc_invalid_params(self, client: AsyncClient) -> None:
        """Test that invalid params return error."""
        response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 5,
                "method": "slim.request",
                "params": [],  # Missing player_id and command
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == -32602

    async def test_jsonrpc_artists_empty(self, client: AsyncClient) -> None:
        """Test artists command on empty library."""
        response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 6,
                "method": "slim.request",
                "params": ["-", ["artists", 0, 100]],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["result"]["count"] == 0
        assert data["result"]["artists_loop"] == []

    async def test_jsonrpc_albums_empty(self, client: AsyncClient) -> None:
        """Test albums command on empty library."""
        response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 7,
                "method": "slim.request",
                "params": ["-", ["albums", 0, 100]],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["result"]["count"] == 0
        assert data["result"]["albums_loop"] == []

    async def test_jsonrpc_titles_empty(self, client: AsyncClient) -> None:
        """Test titles command on empty library."""
        response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 8,
                "method": "slim.request",
                "params": ["-", ["titles", 0, 100]],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["result"]["count"] == 0
        assert data["result"]["titles_loop"] == []
    async def test_jsonrpc_display_grfb_passthrough(
        self,
        web_server: WebServer,
        client: AsyncClient,
    ) -> None:
        """display grfb should pass the brightness code to Slimproto."""

        class _FakeSlimproto:
            def __init__(self) -> None:
                self.calls: list[tuple[str, int]] = []

            async def set_display_brightness(self, player_id: str, brightness_code: int) -> bool:
                self.calls.append((player_id, brightness_code))
                return True

        fake_slimproto = _FakeSlimproto()
        web_server.jsonrpc_handler.slimproto = fake_slimproto

        player_id = "00:11:22:33:44:60"
        response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 861,
                "method": "slim.request",
                "params": [player_id, ["display", "grfb", "-1"]],
            },
        )

        assert response.status_code == 200
        result = response.json()["result"]
        assert result["_grfb"] == -1
        assert fake_slimproto.calls == [(player_id, -1)]

    async def test_jsonrpc_display_grfe_clear_passthrough(
        self,
        web_server: WebServer,
        client: AsyncClient,
    ) -> None:
        """display grfe clear should call Slimproto clear_display with size."""

        class _FakeSlimproto:
            def __init__(self) -> None:
                self.calls: list[tuple[str, int]] = []

            async def clear_display(self, player_id: str, bitmap_size: int = 1280) -> bool:
                self.calls.append((player_id, bitmap_size))
                return True

        fake_slimproto = _FakeSlimproto()
        web_server.jsonrpc_handler.slimproto = fake_slimproto

        player_id = "00:11:22:33:44:61"
        response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 862,
                "method": "slim.request",
                "params": [player_id, ["display", "grfe", "clear", "2560"]],
            },
        )

        assert response.status_code == 200
        result = response.json()["result"]
        assert result["_grfe"] == "clear"
        assert result["bytes"] == 2560
        assert fake_slimproto.calls == [(player_id, 2560)]

    async def test_jsonrpc_display_grfd_clear_passthrough(
        self,
        web_server: WebServer,
        client: AsyncClient,
    ) -> None:
        """display grfd clear should call Slimproto clear_display_framebuffer."""

        class _FakeSlimproto:
            def __init__(self) -> None:
                self.calls: list[tuple[str, int, int]] = []

            async def clear_display_framebuffer(
                self,
                player_id: str,
                *,
                bitmap_size: int = 560,
                offset: int = 560,
            ) -> bool:
                self.calls.append((player_id, bitmap_size, offset))
                return True

        fake_slimproto = _FakeSlimproto()
        web_server.jsonrpc_handler.slimproto = fake_slimproto

        player_id = "00:11:22:33:44:62"
        response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 863,
                "method": "slim.request",
                "params": [player_id, ["display", "grfd", "clear", "640", "700"]],
            },
        )

        assert response.status_code == 200
        result = response.json()["result"]
        assert result["_grfd"] == "clear"
        assert result["bytes"] == 640
        assert result["offset"] == 700
        assert fake_slimproto.calls == [(player_id, 640, 700)]

    async def test_jsonrpc_display_grfd_bitmap_passthrough(
        self,
        web_server: WebServer,
        client: AsyncClient,
    ) -> None:
        """display grfd bitmap should call Slimproto send_display_framebuffer."""

        class _FakeSlimproto:
            def __init__(self) -> None:
                self.calls: list[tuple[str, bytes, int]] = []

            async def send_display_framebuffer(
                self,
                player_id: str,
                bitmap: bytes,
                *,
                offset: int = 560,
            ) -> bool:
                self.calls.append((player_id, bitmap, offset))
                return True

        fake_slimproto = _FakeSlimproto()
        web_server.jsonrpc_handler.slimproto = fake_slimproto

        player_id = "00:11:22:33:44:63"
        response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 864,
                "method": "slim.request",
                "params": [player_id, ["display", "grfd", "deadbeef", "900"]],
            },
        )

        assert response.status_code == 200
        result = response.json()["result"]
        assert result["_grfd"] == "bitmap"
        assert result["bytes"] == 4
        assert result["offset"] == 900
        assert fake_slimproto.calls == [(player_id, b"\xde\xad\xbe\xef", 900)]

    async def test_jsonrpc_power_off_rejected_when_device_cannot_power_off(
        self,
        registry: PlayerRegistry,
        client: AsyncClient,
    ) -> None:
        """power 0 should be rejected for devices without power-off support (e.g. Boom)."""
        from resonance.player.capabilities import get_device_capabilities
        from resonance.player.client import DeviceType

        class _FakePlayer:
            def __init__(self) -> None:
                self.stop_called = False
                self.audio_enable_calls: list[bool] = []
                self._caps = get_device_capabilities(DeviceType.BOOM)

            @property
            def device_capabilities(self):
                return self._caps

            async def stop(self) -> None:
                self.stop_called = True

            async def set_audio_enable(self, enabled: bool) -> None:
                self.audio_enable_calls.append(enabled)

        player_id = "00:11:22:33:44:97"
        fake_player = _FakePlayer()
        registry._players_by_mac[player_id] = fake_player

        response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 77,
                "method": "slim.request",
                "params": [player_id, ["power", "0"]],
            },
        )
        assert response.status_code == 200

        result = response.json()["result"]
        assert result["_power"] == 1
        assert "error" in result
        assert "does not support power off" in result["error"]
        assert fake_player.stop_called is False
        assert fake_player.audio_enable_calls == []


    async def test_jsonrpc_power_off_supported_device_executes_powerdown(
        self,
        registry: PlayerRegistry,
        client: AsyncClient,
    ) -> None:
        """power 0 should stop playback and disable audio on devices that support power-off."""
        from resonance.player.capabilities import get_device_capabilities
        from resonance.player.client import DeviceType

        class _FakePlayer:
            def __init__(self) -> None:
                self.stop_called = False
                self.audio_enable_calls: list[bool] = []
                self._caps = get_device_capabilities(DeviceType.SQUEEZEBOX2)

            @property
            def device_capabilities(self):
                return self._caps

            async def stop(self) -> None:
                self.stop_called = True

            async def set_audio_enable(self, enabled: bool) -> None:
                self.audio_enable_calls.append(enabled)

        player_id = "00:11:22:33:44:99"
        fake_player = _FakePlayer()
        registry._players_by_mac[player_id] = fake_player

        response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 770,
                "method": "slim.request",
                "params": [player_id, ["power", "0"]],
            },
        )
        assert response.status_code == 200

        result = response.json()["result"]
        assert result["_power"] == 0
        assert "error" not in result
        assert fake_player.stop_called is True
        assert fake_player.audio_enable_calls == [False]

    async def test_jsonrpc_mixer_rejects_unsupported_stereoxl_set(
        self,
        registry: PlayerRegistry,
        client: AsyncClient,
    ) -> None:
        """mixer stereoxl set should fail on devices without StereoXL capability."""
        from resonance.player.capabilities import get_device_capabilities
        from resonance.player.client import DeviceType
        from resonance.web.handlers import playback as playback_handler

        player_id = "00:11:22:33:44:98"
        playback_handler._MIXER_PREFS.pop(player_id, None)

        class _FakeState:
            value = "stopped"

        class _FakeStatus:
            def __init__(self) -> None:
                self.state = _FakeState()
                self.volume = 50
                self.muted = False

        class _FakePlayer:
            def __init__(self) -> None:
                self.status = _FakeStatus()
                self._caps = get_device_capabilities(DeviceType.SQUEEZEBOX2)

            @property
            def device_capabilities(self):
                return self._caps

        registry._players_by_mac[player_id] = _FakePlayer()

        query_response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 78,
                "method": "slim.request",
                "params": [player_id, ["mixer", "stereoxl", "?"]],
            },
        )
        assert query_response.status_code == 200
        assert query_response.json()["result"]["_stereoxl"] == 0

        set_response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 79,
                "method": "slim.request",
                "params": [player_id, ["mixer", "stereoxl", "2"]],
            },
        )
        assert set_response.status_code == 200

        result = set_response.json()["result"]
        assert "error" in result
        assert "not supported" in result["error"]
        assert "stereoxl" not in playback_handler._MIXER_PREFS.get(player_id, {})



    @pytest.mark.parametrize("subcommand", ["bass", "treble"])
    async def test_jsonrpc_mixer_rejects_unsupported_tone_set(
        self,
        subcommand: str,
        registry: PlayerRegistry,
        client: AsyncClient,
    ) -> None:
        """mixer bass/treble writes should fail when the device exposes no tone controls."""
        from resonance.player.capabilities import get_device_capabilities
        from resonance.player.client import DeviceType
        from resonance.web.handlers import playback as playback_handler

        player_id = "00:11:22:33:44:9a"
        playback_handler._MIXER_PREFS.pop(player_id, None)

        class _FakeState:
            value = "stopped"

        class _FakeStatus:
            def __init__(self) -> None:
                self.state = _FakeState()
                self.volume = 50
                self.muted = False

        class _FakePlayer:
            def __init__(self) -> None:
                self.status = _FakeStatus()
                self._caps = get_device_capabilities(DeviceType.SQUEEZEBOX2)

            @property
            def device_capabilities(self):
                return self._caps

        registry._players_by_mac[player_id] = _FakePlayer()

        query_response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 771,
                "method": "slim.request",
                "params": [player_id, ["mixer", subcommand, "?"]],
            },
        )
        assert query_response.status_code == 200
        assert query_response.json()["result"][f"_{subcommand}"] == 50

        set_response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 772,
                "method": "slim.request",
                "params": [player_id, ["mixer", subcommand, "10"]],
            },
        )
        assert set_response.status_code == 200

        result = set_response.json()["result"]
        assert "error" in result
        assert "not supported" in result["error"]
        assert subcommand not in playback_handler._MIXER_PREFS.get(player_id, {})

    async def test_jsonrpc_mixer_allows_supported_tone_set_on_boom(
        self,
        registry: PlayerRegistry,
        client: AsyncClient,
    ) -> None:
        """mixer bass set should succeed and clamp within Boom capability range."""
        from resonance.player.capabilities import get_device_capabilities
        from resonance.player.client import DeviceType
        from resonance.web.handlers import playback as playback_handler

        player_id = "00:11:22:33:44:9b"
        playback_handler._MIXER_PREFS.pop(player_id, None)

        class _FakeState:
            value = "stopped"

        class _FakeStatus:
            def __init__(self) -> None:
                self.state = _FakeState()
                self.volume = 50
                self.muted = False

        class _FakePlayer:
            def __init__(self) -> None:
                self.status = _FakeStatus()
                self._caps = get_device_capabilities(DeviceType.BOOM)

            @property
            def device_capabilities(self):
                return self._caps

        registry._players_by_mac[player_id] = _FakePlayer()

        response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 773,
                "method": "slim.request",
                "params": [player_id, ["mixer", "bass", "999"]],
            },
        )
        assert response.status_code == 200
        result = response.json()["result"]
        assert "error" not in result
        assert result["_bass"] == 23
        assert playback_handler._MIXER_PREFS[player_id]["bass"] == 23

    async def test_jsonrpc_menustatus_passthrough_fields(self, client: AsyncClient) -> None:
        """menustatus with payload should expose menu/action passthrough fields."""
        response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 80,
                "method": "slim.request",
                "params": ["00:11:22:33:44:80", ["menustatus", "myMusic", "add"]],
            },
        )
        assert response.status_code == 200
        result = response.json()["result"]
        assert result["menu"] == "myMusic"
        assert result["action"] == "add"

    async def test_jsonrpc_alarmsettings_builds_items_from_alarms(self, client: AsyncClient) -> None:
        """alarmsettings should expose LMS-style menu items based on stored alarms."""
        from resonance.web.handlers import alarm as alarm_handler

        player_id = "00:11:22:33:44:81"
        alarm_handler._PLAYER_ALARMS.pop(player_id, None)
        alarm_handler._PLAYER_DEFAULT_VOLUME.pop(player_id, None)

        add_response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 81,
                "method": "slim.request",
                "params": [
                    player_id,
                    ["alarm", "add", "time:25200", "enabled:1", "repeat:1", "dow:1,2"],
                ],
            },
        )
        assert add_response.status_code == 200
        alarm_id = add_response.json()["result"]["id"]

        settings_response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 82,
                "method": "slim.request",
                "params": [player_id, ["alarmsettings", 0, 20]],
            },
        )
        assert settings_response.status_code == 200
        result = settings_response.json()["result"]

        assert result["count"] == 3
        assert len(result["item_loop"]) == 3

        all_alarms_item = result["item_loop"][0]
        assert all_alarms_item["text"] == "All alarms"
        assert all_alarms_item["selectedIndex"] == 2

        alarm_item = result["item_loop"][1]
        assert "07:00" in alarm_item["text"]
        assert alarm_item["checkbox"] == 1
        assert f"id:{alarm_id}" in alarm_item["actions"]["on"]["cmd"]

        add_item = result["item_loop"][2]
        assert add_item["text"] == "Add alarm (07:00)"
        assert add_item["nextWindow"] == "refresh"

    async def test_jsonrpc_syncsettings_lists_candidates_and_unsync(self, registry: PlayerRegistry, client: AsyncClient) -> None:
        """syncsettings should list candidate players and expose unsync option when synced."""

        class _FakePlayer:
            def __init__(self, mac: str, name: str) -> None:
                self.mac_address = mac
                self.name = name

        current_id = "00:11:22:33:44:82"
        alpha_id = "00:11:22:33:44:83"
        beta_id = "00:11:22:33:44:84"

        registry._players_by_mac[current_id] = _FakePlayer(current_id, "Current")
        registry._players_by_mac[alpha_id] = _FakePlayer(alpha_id, "Alpha")
        registry._players_by_mac[beta_id] = _FakePlayer(beta_id, "Beta")
        registry._sync_master_by_player[current_id] = current_id
        registry._sync_master_by_player[alpha_id] = current_id

        response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 83,
                "method": "slim.request",
                "params": [current_id, ["syncsettings", 0, 20]],
            },
        )
        assert response.status_code == 200
        result = response.json()["result"]

        assert result["count"] == 3
        assert len(result["item_loop"]) == 3

        alpha_item = next(item for item in result["item_loop"] if item.get("text") == "Alpha")
        beta_item = next(item for item in result["item_loop"] if item.get("text") == "Beta")
        unsync_item = next(item for item in result["item_loop"] if item.get("text") == "Do not sync")

        assert alpha_item["radio"] == 1
        assert beta_item["radio"] == 0
        assert alpha_item["actions"]["do"]["cmd"] == ["sync", alpha_id]
        assert unsync_item["actions"]["do"]["cmd"] == ["sync", "-"]


    async def test_jsonrpc_playlist_loadalbum_alias_loads_album_tracks(
        self,
        web_server: WebServer,
        client: AsyncClient,
        db: LibraryDb,
    ) -> None:
        """playlist loadalbum should map to loadtracks semantics and fill the queue."""
        from resonance.core.playlist import PlaylistManager

        player_id = "00:11:22:33:44:90"
        playlist_manager = PlaylistManager()
        web_server.playlist_manager = playlist_manager
        web_server.jsonrpc_handler.playlist_manager = playlist_manager

        await db.upsert_tracks(
            [
                UpsertTrack(
                    path="/music/a/album_one/track1.mp3",
                    title="Album One Track 1",
                    artist="Artist A",
                    album="Album One",
                    track_no=1,
                    year=2020,
                    duration_ms=180000,
                    genres=("Rock",),
                ),
                UpsertTrack(
                    path="/music/a/album_one/track2.mp3",
                    title="Album One Track 2",
                    artist="Artist A",
                    album="Album One",
                    track_no=2,
                    year=2020,
                    duration_ms=200000,
                    genres=("Rock",),
                ),
                UpsertTrack(
                    path="/music/a/album_two/track1.mp3",
                    title="Album Two Track 1",
                    artist="Artist A",
                    album="Album Two",
                    track_no=1,
                    year=2021,
                    duration_ms=210000,
                    genres=("Rock",),
                ),
            ]
        )
        await db.commit()

        tracks = await db.list_tracks(limit=200, offset=0, order_by="title")
        album_one_id = next(t.album_id for t in tracks if t.album == "Album One")
        assert album_one_id is not None

        response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 9010,
                "method": "slim.request",
                "params": [player_id, ["playlist", "loadalbum", f"album_id:{album_one_id}"]],
            },
        )
        assert response.status_code == 200

        result = response.json()["result"]
        assert result["count"] == 2

        playlist = playlist_manager.get(player_id)
        assert len(playlist) == 2
        assert [track.title for track in playlist.tracks] == [
            "Album One Track 1",
            "Album One Track 2",
        ]

    async def test_jsonrpc_playlist_addalbum_and_insertalbum_aliases(
        self,
        web_server: WebServer,
        client: AsyncClient,
        db: LibraryDb,
    ) -> None:
        """playlist addalbum/insertalbum should route to addtracks/inserttracks."""
        from resonance.core.playlist import PlaylistManager

        player_id = "00:11:22:33:44:91"
        playlist_manager = PlaylistManager()
        web_server.playlist_manager = playlist_manager
        web_server.jsonrpc_handler.playlist_manager = playlist_manager

        await db.upsert_tracks(
            [
                UpsertTrack(
                    path="/music/b/album_a/track1.mp3",
                    title="Album A Track 1",
                    artist="Artist B",
                    album="Album A",
                    track_no=1,
                    year=2020,
                    duration_ms=180000,
                    genres=("Jazz",),
                ),
                UpsertTrack(
                    path="/music/b/album_a/track2.mp3",
                    title="Album A Track 2",
                    artist="Artist B",
                    album="Album A",
                    track_no=2,
                    year=2020,
                    duration_ms=190000,
                    genres=("Jazz",),
                ),
                UpsertTrack(
                    path="/music/b/album_b/track1.mp3",
                    title="Album B Track 1",
                    artist="Artist B",
                    album="Album B",
                    track_no=1,
                    year=2021,
                    duration_ms=200000,
                    genres=("Jazz",),
                ),
            ]
        )
        await db.commit()

        tracks = await db.list_tracks(limit=200, offset=0, order_by="title")
        album_a_id = next(t.album_id for t in tracks if t.album == "Album A")
        album_b_id = next(t.album_id for t in tracks if t.album == "Album B")
        assert album_a_id is not None
        assert album_b_id is not None

        add_response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 9011,
                "method": "slim.request",
                "params": [player_id, ["playlist", "addalbum", f"album_id:{album_a_id}"]],
            },
        )
        assert add_response.status_code == 200
        assert add_response.json()["result"]["count"] == 2

        index_response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 9012,
                "method": "slim.request",
                "params": [player_id, ["playlist", "index", "0"]],
            },
        )
        assert index_response.status_code == 200
        assert index_response.json()["result"]["_index"] == 0

        insert_response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 9013,
                "method": "slim.request",
                "params": [player_id, ["playlist", "insertalbum", f"album_id:{album_b_id}"]],
            },
        )
        assert insert_response.status_code == 200
        assert insert_response.json()["result"]["count"] == 3

        playlist = playlist_manager.get(player_id)
        assert len(playlist) == 3
        assert [track.title for track in playlist.tracks] == [
            "Album A Track 1",
            "Album B Track 1",
            "Album A Track 2",
        ]

    async def test_jsonrpc_playlist_deletetracks_removes_filtered_tracks(
        self,
        web_server: WebServer,
        client: AsyncClient,
        db: LibraryDb,
    ) -> None:
        """playlist deletetracks should remove queued items matched by filter args."""
        from resonance.core.playlist import PlaylistManager

        player_id = "00:11:22:33:44:93"
        playlist_manager = PlaylistManager()
        web_server.playlist_manager = playlist_manager
        web_server.jsonrpc_handler.playlist_manager = playlist_manager

        await db.upsert_tracks(
            [
                UpsertTrack(
                    path="/music/d/album_d1/track1.mp3",
                    title="Album D1 Track 1",
                    artist="Artist D",
                    album="Album D1",
                    track_no=1,
                    year=2023,
                    duration_ms=180000,
                    genres=("Indie",),
                ),
                UpsertTrack(
                    path="/music/d/album_d1/track2.mp3",
                    title="Album D1 Track 2",
                    artist="Artist D",
                    album="Album D1",
                    track_no=2,
                    year=2023,
                    duration_ms=185000,
                    genres=("Indie",),
                ),
                UpsertTrack(
                    path="/music/d/album_d2/track1.mp3",
                    title="Album D2 Track 1",
                    artist="Artist D",
                    album="Album D2",
                    track_no=1,
                    year=2024,
                    duration_ms=190000,
                    genres=("Indie",),
                ),
            ]
        )
        await db.commit()

        tracks = await db.list_tracks(limit=200, offset=0, order_by="title")
        album_d1_id = next(t.album_id for t in tracks if t.album == "Album D1")
        assert album_d1_id is not None

        load_response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 9020,
                "method": "slim.request",
                "params": [player_id, ["playlist", "loadtracks", "artist:Artist D"]],
            },
        )
        assert load_response.status_code == 200
        assert load_response.json()["result"]["count"] == 3

        delete_response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 9021,
                "method": "slim.request",
                "params": [player_id, ["playlist", "deletetracks", f"album_id:{album_d1_id}"]],
            },
        )
        assert delete_response.status_code == 200
        assert delete_response.json()["result"]["count"] == 1

        playlist = playlist_manager.get(player_id)
        assert len(playlist) == 1
        assert [track.title for track in playlist.tracks] == ["Album D2 Track 1"]

    async def test_jsonrpc_playlist_deletealbum_alias_with_numeric_id(
        self,
        web_server: WebServer,
        client: AsyncClient,
        db: LibraryDb,
    ) -> None:
        """playlist deletealbum should map numeric arg to album_id and delete matching tracks."""
        from resonance.core.playlist import PlaylistManager

        player_id = "00:11:22:33:44:94"
        playlist_manager = PlaylistManager()
        web_server.playlist_manager = playlist_manager
        web_server.jsonrpc_handler.playlist_manager = playlist_manager

        await db.upsert_tracks(
            [
                UpsertTrack(
                    path="/music/e/album_e1/track1.mp3",
                    title="Album E1 Track 1",
                    artist="Artist E",
                    album="Album E1",
                    track_no=1,
                    year=2021,
                    duration_ms=180000,
                    genres=("Soul",),
                ),
                UpsertTrack(
                    path="/music/e/album_e1/track2.mp3",
                    title="Album E1 Track 2",
                    artist="Artist E",
                    album="Album E1",
                    track_no=2,
                    year=2021,
                    duration_ms=182000,
                    genres=("Soul",),
                ),
                UpsertTrack(
                    path="/music/e/album_e2/track1.mp3",
                    title="Album E2 Track 1",
                    artist="Artist E",
                    album="Album E2",
                    track_no=1,
                    year=2022,
                    duration_ms=188000,
                    genres=("Soul",),
                ),
            ]
        )
        await db.commit()

        tracks = await db.list_tracks(limit=200, offset=0, order_by="title")
        album_e2_id = next(t.album_id for t in tracks if t.album == "Album E2")
        assert album_e2_id is not None

        load_response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 9022,
                "method": "slim.request",
                "params": [player_id, ["playlist", "loadtracks", "artist:Artist E"]],
            },
        )
        assert load_response.status_code == 200
        assert load_response.json()["result"]["count"] == 3

        delete_response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 9023,
                "method": "slim.request",
                "params": [player_id, ["playlist", "deletealbum", str(album_e2_id)]],
            },
        )
        assert delete_response.status_code == 200
        assert delete_response.json()["result"]["count"] == 2

        playlist = playlist_manager.get(player_id)
        assert len(playlist) == 2
        assert {track.title for track in playlist.tracks} == {
            "Album E1 Track 1",
            "Album E1 Track 2",
        }

    async def test_jsonrpc_playlist_deleteitem_removes_track_by_path(
        self,
        web_server: WebServer,
        client: AsyncClient,
        db: LibraryDb,
    ) -> None:
        """playlist deleteitem should remove matching queued items by path."""
        from resonance.core.playlist import PlaylistManager

        player_id = "00:11:22:33:44:95"
        playlist_manager = PlaylistManager()
        web_server.playlist_manager = playlist_manager
        web_server.jsonrpc_handler.playlist_manager = playlist_manager

        await db.upsert_tracks(
            [
                UpsertTrack(
                    path="/music/f/album_f/track1.mp3",
                    title="Album F Track 1",
                    artist="Artist F",
                    album="Album F",
                    track_no=1,
                    year=2020,
                    duration_ms=180000,
                    genres=("Pop",),
                ),
                UpsertTrack(
                    path="/music/f/album_f/track2.mp3",
                    title="Album F Track 2",
                    artist="Artist F",
                    album="Album F",
                    track_no=2,
                    year=2020,
                    duration_ms=181000,
                    genres=("Pop",),
                ),
            ]
        )
        await db.commit()

        load_response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 9024,
                "method": "slim.request",
                "params": [player_id, ["playlist", "loadtracks", "artist:Artist F"]],
            },
        )
        assert load_response.status_code == 200
        assert load_response.json()["result"]["count"] == 2

        delete_response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 9025,
                "method": "slim.request",
                "params": [player_id, ["playlist", "deleteitem", "/music/f/album_f/track1.mp3"]],
            },
        )
        assert delete_response.status_code == 200
        assert delete_response.json()["result"]["count"] == 1

        playlist = playlist_manager.get(player_id)
        assert len(playlist) == 1
        assert [track.title for track in playlist.tracks] == ["Album F Track 2"]

    async def test_jsonrpc_playlist_pause_alias_uses_pause_semantics(
        self,
        registry: PlayerRegistry,
        client: AsyncClient,
    ) -> None:
        """playlist pause should behave like top-level pause for 1/0."""

        class _FakeState:
            def __init__(self, name: str) -> None:
                self.name = name

        class _FakeStatus:
            def __init__(self, state_name: str) -> None:
                self.state = _FakeState(state_name)

        class _FakePlayer:
            def __init__(self) -> None:
                self.status = _FakeStatus("PLAYING")
                self.pause_calls = 0
                self.play_calls = 0

            async def pause(self) -> None:
                self.pause_calls += 1
                self.status.state.name = "PAUSED"

            async def play(self) -> None:
                self.play_calls += 1
                self.status.state.name = "PLAYING"

        player_id = "00:11:22:33:44:96"
        fake_player = _FakePlayer()
        registry._players_by_mac[player_id] = fake_player

        pause_response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 9026,
                "method": "slim.request",
                "params": [player_id, ["playlist", "pause", "1"]],
            },
        )
        assert pause_response.status_code == 200
        assert fake_player.pause_calls == 1
        assert fake_player.play_calls == 0

        resume_response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 9027,
                "method": "slim.request",
                "params": [player_id, ["playlist", "pause", "0"]],
            },
        )
        assert resume_response.status_code == 200
        assert fake_player.pause_calls == 1
        assert fake_player.play_calls == 1

    async def test_jsonrpc_playlist_stop_alias_stops_player(
        self,
        registry: PlayerRegistry,
        client: AsyncClient,
    ) -> None:
        """playlist stop should behave like top-level stop."""

        class _FakeState:
            def __init__(self, name: str) -> None:
                self.name = name

        class _FakeStatus:
            def __init__(self) -> None:
                self.state = _FakeState("PLAYING")

        class _FakePlayer:
            def __init__(self) -> None:
                self.status = _FakeStatus()
                self.stop_calls = 0

            async def stop(self) -> None:
                self.stop_calls += 1

        player_id = "00:11:22:33:44:97"
        fake_player = _FakePlayer()
        registry._players_by_mac[player_id] = fake_player

        response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 9028,
                "method": "slim.request",
                "params": [player_id, ["playlist", "stop"]],
            },
        )
        assert response.status_code == 200
        assert fake_player.stop_calls == 1

    async def test_jsonrpc_playlist_load_loads_single_track_by_path(
        self,
        web_server: WebServer,
        client: AsyncClient,
        db: LibraryDb,
    ) -> None:
        """playlist load should clear queue and load one track by path."""
        from resonance.core.playlist import PlaylistManager

        player_id = "00:11:22:33:44:98"
        playlist_manager = PlaylistManager()
        web_server.playlist_manager = playlist_manager
        web_server.jsonrpc_handler.playlist_manager = playlist_manager

        track_path = "/music/g/album_g/track1.mp3"
        await db.upsert_tracks(
            [
                UpsertTrack(
                    path=track_path,
                    title="Album G Track 1",
                    artist="Artist G",
                    album="Album G",
                    track_no=1,
                    year=2020,
                    duration_ms=180000,
                    genres=("Pop",),
                ),
                UpsertTrack(
                    path="/music/g/album_g/track2.mp3",
                    title="Album G Track 2",
                    artist="Artist G",
                    album="Album G",
                    track_no=2,
                    year=2020,
                    duration_ms=181000,
                    genres=("Pop",),
                ),
            ]
        )
        await db.commit()

        response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 9029,
                "method": "slim.request",
                "params": [player_id, ["playlist", "load", track_path]],
            },
        )
        assert response.status_code == 200
        assert response.json()["result"]["count"] == 1

        playlist = playlist_manager.get(player_id)
        assert len(playlist) == 1
        assert [track.title for track in playlist.tracks] == ["Album G Track 1"]

    async def test_jsonrpc_playlist_insertlist_inserts_after_current(
        self,
        web_server: WebServer,
        client: AsyncClient,
        db: LibraryDb,
    ) -> None:
        """playlist insertlist should use insert semantics for list/filter inputs."""
        from resonance.core.playlist import PlaylistManager

        player_id = "00:11:22:33:44:99"
        playlist_manager = PlaylistManager()
        web_server.playlist_manager = playlist_manager
        web_server.jsonrpc_handler.playlist_manager = playlist_manager

        await db.upsert_tracks(
            [
                UpsertTrack(
                    path="/music/h/album_h1/track1.mp3",
                    title="Album H1 Track 1",
                    artist="Artist H",
                    album="Album H1",
                    track_no=1,
                    year=2021,
                    duration_ms=180000,
                    genres=("Rock",),
                ),
                UpsertTrack(
                    path="/music/h/album_h1/track2.mp3",
                    title="Album H1 Track 2",
                    artist="Artist H",
                    album="Album H1",
                    track_no=2,
                    year=2021,
                    duration_ms=182000,
                    genres=("Rock",),
                ),
                UpsertTrack(
                    path="/music/h/album_h2/track1.mp3",
                    title="Album H2 Track 1",
                    artist="Artist H",
                    album="Album H2",
                    track_no=1,
                    year=2022,
                    duration_ms=184000,
                    genres=("Rock",),
                ),
            ]
        )
        await db.commit()

        tracks = await db.list_tracks(limit=200, offset=0, order_by="title")
        album_h1_id = next(t.album_id for t in tracks if t.album == "Album H1")
        insert_track_id = next(t.id for t in tracks if t.title == "Album H2 Track 1")
        assert album_h1_id is not None
        assert insert_track_id is not None

        load_response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 9030,
                "method": "slim.request",
                "params": [player_id, ["playlist", "loadtracks", f"album_id:{album_h1_id}"]],
            },
        )
        assert load_response.status_code == 200
        assert load_response.json()["result"]["count"] == 2

        index_response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 9031,
                "method": "slim.request",
                "params": [player_id, ["playlist", "index", "0"]],
            },
        )
        assert index_response.status_code == 200

        insert_response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 9032,
                "method": "slim.request",
                "params": [player_id, ["playlist", "insertlist", f"track_id:{insert_track_id}"]],
            },
        )
        assert insert_response.status_code == 200
        assert insert_response.json()["result"]["count"] == 3

        playlist = playlist_manager.get(player_id)
        assert [track.title for track in playlist.tracks] == [
            "Album H1 Track 1",
            "Album H2 Track 1",
            "Album H1 Track 2",
        ]

    async def test_jsonrpc_playlist_playlistsinfo_returns_current_queue_metadata(
        self,
        web_server: WebServer,
        client: AsyncClient,
        db: LibraryDb,
    ) -> None:
        """playlist playlistsinfo should expose basic current queue metadata."""
        from resonance.core.playlist import PlaylistManager

        player_id = "00:11:22:33:44:a0"
        playlist_manager = PlaylistManager()
        web_server.playlist_manager = playlist_manager
        web_server.jsonrpc_handler.playlist_manager = playlist_manager

        track_path = "/music/i/album_i/track1.mp3"
        await db.upsert_tracks(
            [
                UpsertTrack(
                    path=track_path,
                    title="Album I Track 1",
                    artist="Artist I",
                    album="Album I",
                    track_no=1,
                    year=2020,
                    duration_ms=180000,
                    genres=("Jazz",),
                ),
            ]
        )
        await db.commit()

        load_response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 9033,
                "method": "slim.request",
                "params": [player_id, ["playlist", "load", track_path]],
            },
        )
        assert load_response.status_code == 200

        info_response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 9034,
                "method": "slim.request",
                "params": [player_id, ["playlist", "playlistsinfo"]],
            },
        )
        assert info_response.status_code == 200
        info = info_response.json()["result"]
        assert info["id"] == 0
        assert info["name"] == "Current Playlist"
        assert info["modified"] == 1
        assert info["url"] == track_path

    async def test_jsonrpc_playlist_preview_save_and_restore(
        self,
        web_server: WebServer,
        client: AsyncClient,
        db: LibraryDb,
    ) -> None:
        """playlist preview should save queue, play preview item, and restore on stop."""
        from resonance.core.playlist import PlaylistManager

        player_id = "00:11:22:33:44:a1"
        playlist_manager = PlaylistManager()
        web_server.playlist_manager = playlist_manager
        web_server.jsonrpc_handler.playlist_manager = playlist_manager

        await db.upsert_tracks(
            [
                UpsertTrack(
                    path="/music/j/album_j/track1.mp3",
                    title="Album J Track 1",
                    artist="Artist J",
                    album="Album J",
                    track_no=1,
                    year=2022,
                    duration_ms=180000,
                    genres=("Indie",),
                ),
                UpsertTrack(
                    path="/music/j/album_j/track2.mp3",
                    title="Album J Track 2",
                    artist="Artist J",
                    album="Album J",
                    track_no=2,
                    year=2022,
                    duration_ms=181000,
                    genres=("Indie",),
                ),
                UpsertTrack(
                    path="/music/j/preview/track.mp3",
                    title="Preview Track",
                    artist="Artist J",
                    album="Preview",
                    track_no=1,
                    year=2023,
                    duration_ms=120000,
                    genres=("Indie",),
                ),
            ]
        )
        await db.commit()

        tracks = await db.list_tracks(limit=200, offset=0, order_by="title")
        album_j_id = next(t.album_id for t in tracks if t.album == "Album J")
        assert album_j_id is not None

        load_response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 9035,
                "method": "slim.request",
                "params": [player_id, ["playlist", "loadalbum", f"album_id:{album_j_id}"]],
            },
        )
        assert load_response.status_code == 200
        assert load_response.json()["result"]["count"] == 2

        index_response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 9036,
                "method": "slim.request",
                "params": [player_id, ["playlist", "index", "1"]],
            },
        )
        assert index_response.status_code == 200

        preview_response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 9037,
                "method": "slim.request",
                "params": [
                    player_id,
                    ["playlist", "preview", "url:/music/j/preview/track.mp3", "title:Preview"],
                ],
            },
        )
        assert preview_response.status_code == 200
        assert preview_response.json()["result"]["count"] == 1

        playlist = playlist_manager.get(player_id)
        assert [track.title for track in playlist.tracks] == ["Preview Track"]

        stop_preview_response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 9038,
                "method": "slim.request",
                "params": [player_id, ["playlist", "preview", "cmd:stop"]],
            },
        )
        assert stop_preview_response.status_code == 200
        stop_result = stop_preview_response.json()["result"]
        assert stop_result["count"] == 2
        assert stop_result["_index"] == 1

        restored = playlist_manager.get(player_id)
        assert [track.title for track in restored.tracks] == [
            "Album J Track 1",
            "Album J Track 2",
        ]

    async def test_jsonrpc_playlist_zap_removes_current_track(
        self,
        web_server: WebServer,
        client: AsyncClient,
        db: LibraryDb,
    ) -> None:
        """playlist zap should remove the current queue item."""
        from resonance.core.playlist import PlaylistManager

        player_id = "00:11:22:33:44:a2"
        playlist_manager = PlaylistManager()
        web_server.playlist_manager = playlist_manager
        web_server.jsonrpc_handler.playlist_manager = playlist_manager

        await db.upsert_tracks(
            [
                UpsertTrack(
                    path="/music/k/album_k/track1.mp3",
                    title="Album K Track 1",
                    artist="Artist K",
                    album="Album K",
                    track_no=1,
                    year=2019,
                    duration_ms=180000,
                    genres=("Metal",),
                ),
                UpsertTrack(
                    path="/music/k/album_k/track2.mp3",
                    title="Album K Track 2",
                    artist="Artist K",
                    album="Album K",
                    track_no=2,
                    year=2019,
                    duration_ms=181000,
                    genres=("Metal",),
                ),
                UpsertTrack(
                    path="/music/k/album_k/track3.mp3",
                    title="Album K Track 3",
                    artist="Artist K",
                    album="Album K",
                    track_no=3,
                    year=2019,
                    duration_ms=182000,
                    genres=("Metal",),
                ),
            ]
        )
        await db.commit()

        tracks = await db.list_tracks(limit=200, offset=0, order_by="title")
        album_k_id = next(t.album_id for t in tracks if t.album == "Album K")
        assert album_k_id is not None

        load_response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 9039,
                "method": "slim.request",
                "params": [player_id, ["playlist", "loadalbum", f"album_id:{album_k_id}"]],
            },
        )
        assert load_response.status_code == 200
        assert load_response.json()["result"]["count"] == 3

        index_response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 9040,
                "method": "slim.request",
                "params": [player_id, ["playlist", "index", "1"]],
            },
        )
        assert index_response.status_code == 200

        zap_response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 9041,
                "method": "slim.request",
                "params": [player_id, ["playlist", "zap"]],
            },
        )
        assert zap_response.status_code == 200
        assert zap_response.json()["result"]["count"] == 2

        playlist = playlist_manager.get(player_id)
        assert [track.title for track in playlist.tracks] == [
            "Album K Track 1",
            "Album K Track 3",
        ]

    async def test_jsonrpc_playlist_query_subcommands_return_track_fields(
        self,
        web_server: WebServer,
        client: AsyncClient,
        db: LibraryDb,
    ) -> None:
        """playlist query-style subcommands should expose current queue metadata."""
        from resonance.core.playlist import PlaylistManager

        player_id = "00:11:22:33:44:a3"
        playlist_manager = PlaylistManager()
        web_server.playlist_manager = playlist_manager
        web_server.jsonrpc_handler.playlist_manager = playlist_manager

        track_path = "/music/l/album_l/track1.mp3"
        await db.upsert_tracks(
            [
                UpsertTrack(
                    path=track_path,
                    title="Album L Track 1",
                    artist="Artist L",
                    album="Album L",
                    track_no=1,
                    year=2021,
                    duration_ms=183000,
                    genres=("Electro",),
                ),
            ]
        )
        await db.commit()

        load_response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 9042,
                "method": "slim.request",
                "params": [player_id, ["playlist", "load", track_path]],
            },
        )
        assert load_response.status_code == 200

        async def _query(subcommand: str, *args: str) -> dict[str, Any]:
            response = await client.post(
                "/jsonrpc.js",
                json={
                    "id": 90420,
                    "method": "slim.request",
                    "params": [player_id, ["playlist", subcommand, *args]],
                },
            )
            assert response.status_code == 200
            return response.json()["result"]

        assert (await _query("album", "0", "?"))["_album"] == "Album L"
        assert (await _query("artist", "0", "?"))["_artist"] == "Artist L"
        assert (await _query("title", "0", "?"))["_title"] == "Album L Track 1"
        assert (await _query("path", "0", "?"))["_path"] == track_path
        assert (await _query("url", "?"))["_url"] == track_path
        assert (await _query("name", "?"))["_name"] == "Current Playlist"
        assert (await _query("modified", "?"))["_modified"] == 1
        assert (await _query("remote", "0", "?"))["_remote"] == 0
        assert (await _query("duration", "0", "?"))["_duration"] == 183.0
        assert (await _query("genre", "0", "?"))["_genre"] == "Electro"

    async def test_jsonrpc_playlist_remote_query_detects_stream_url(
        self,
        web_server: WebServer,
        client: AsyncClient,
        db: LibraryDb,
    ) -> None:
        """playlist remote/url should reflect a loaded remote stream URL."""
        from resonance.core.playlist import PlaylistManager

        player_id = "00:11:22:33:44:a4"
        playlist_manager = PlaylistManager()
        web_server.playlist_manager = playlist_manager
        web_server.jsonrpc_handler.playlist_manager = playlist_manager

        remote_url = "http://radio.example.invalid/live.mp3"
        await db.upsert_tracks(
            [
                UpsertTrack(
                    path=remote_url,
                    title="Live Stream",
                    artist="Radio",
                    album="Stream",
                    track_no=1,
                    year=2026,
                    duration_ms=0,
                    genres=("News",),
                ),
            ]
        )
        await db.commit()

        load_response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 9043,
                "method": "slim.request",
                "params": [player_id, ["playlist", "load", remote_url]],
            },
        )
        assert load_response.status_code == 200

        remote_response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 9044,
                "method": "slim.request",
                "params": [player_id, ["playlist", "remote", "0", "?"]],
            },
        )
        assert remote_response.status_code == 200
        assert remote_response.json()["result"]["_remote"] == 1

        url_response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 9045,
                "method": "slim.request",
                "params": [player_id, ["playlist", "url", "?"]],
            },
        )
        assert url_response.status_code == 200
        assert url_response.json()["result"]["_url"] == remote_url

    async def test_jsonrpc_playlist_event_style_subcommands_are_noop_compatible(
        self,
        web_server: WebServer,
        client: AsyncClient,
        db: LibraryDb,
    ) -> None:
        """LMS event-style playlist subcommands should not error in JSON-RPC path."""
        from resonance.core.playlist import PlaylistManager

        player_id = "00:11:22:33:44:a5"
        playlist_manager = PlaylistManager()
        web_server.playlist_manager = playlist_manager
        web_server.jsonrpc_handler.playlist_manager = playlist_manager

        track_path = "/music/m/album_m/track1.mp3"
        await db.upsert_tracks(
            [
                UpsertTrack(
                    path=track_path,
                    title="Album M Track 1",
                    artist="Artist M",
                    album="Album M",
                    track_no=1,
                    year=2020,
                    duration_ms=180000,
                    genres=("Rock",),
                ),
            ]
        )
        await db.commit()

        load_response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 9046,
                "method": "slim.request",
                "params": [player_id, ["playlist", "load", track_path]],
            },
        )
        assert load_response.status_code == 200

        commands = [
            ["playlist", "load_done"],
            ["playlist", "newsong", "Album M Track 1"],
            ["playlist", "open", track_path],
            ["playlist", "sync"],
            ["playlist", "cant_open", track_path, "404"],
        ]

        for offset, command in enumerate(commands):
            response = await client.post(
                "/jsonrpc.js",
                json={
                    "id": 90460 + offset,
                    "method": "slim.request",
                    "params": [player_id, command],
                },
            )
            assert response.status_code == 200
            result = response.json()["result"]
            assert "Unknown playlist subcommand" not in str(result)

        playlist = playlist_manager.get(player_id)
        assert len(playlist) == 1
        assert [track.title for track in playlist.tracks] == ["Album M Track 1"]

    async def test_jsonrpc_playlist_save_resume_snapshot_and_wipe(
        self,
        web_server: WebServer,
        client: AsyncClient,
        db: LibraryDb,
    ) -> None:
        """playlist save/resume should persist and optionally wipe in-memory snapshots."""
        from resonance.core.playlist import PlaylistManager

        player_id = "00:11:22:33:44:92"
        playlist_manager = PlaylistManager()
        web_server.playlist_manager = playlist_manager
        web_server.jsonrpc_handler.playlist_manager = playlist_manager

        await db.upsert_tracks(
            [
                UpsertTrack(
                    path="/music/c/album_c/track1.mp3",
                    title="Album C Track 1",
                    artist="Artist C",
                    album="Album C",
                    track_no=1,
                    year=2022,
                    duration_ms=180000,
                    genres=("Metal",),
                ),
                UpsertTrack(
                    path="/music/c/album_c/track2.mp3",
                    title="Album C Track 2",
                    artist="Artist C",
                    album="Album C",
                    track_no=2,
                    year=2022,
                    duration_ms=190000,
                    genres=("Metal",),
                ),
            ]
        )
        await db.commit()

        tracks = await db.list_tracks(limit=200, offset=0, order_by="title")
        album_c_id = next(t.album_id for t in tracks if t.album == "Album C")
        assert album_c_id is not None

        load_response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 9014,
                "method": "slim.request",
                "params": [player_id, ["playlist", "loadalbum", f"album_id:{album_c_id}"]],
            },
        )
        assert load_response.status_code == 200
        assert load_response.json()["result"]["count"] == 2

        save_response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 9015,
                "method": "slim.request",
                "params": [player_id, ["playlist", "save", "session-save"]],
            },
        )
        assert save_response.status_code == 200
        save_result = save_response.json()["result"]
        assert save_result["__playlist_id"] == "session-save"
        assert save_result["count"] == 2

        clear_response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 9016,
                "method": "slim.request",
                "params": [player_id, ["playlist", "clear"]],
            },
        )
        assert clear_response.status_code == 200
        assert clear_response.json()["result"]["count"] == 0

        resume_response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 9017,
                "method": "slim.request",
                "params": [
                    player_id,
                    ["playlist", "resume", "session-save", "noplay:1", "wipePlaylist:1"],
                ],
            },
        )
        assert resume_response.status_code == 200
        resume_result = resume_response.json()["result"]
        assert resume_result["count"] == 2
        assert resume_result["_index"] == 0

        playlist = playlist_manager.get(player_id)
        assert len(playlist) == 2

        clear_again_response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 9018,
                "method": "slim.request",
                "params": [player_id, ["playlist", "clear"]],
            },
        )
        assert clear_again_response.status_code == 200

        resume_missing_response = await client.post(
            "/jsonrpc.js",
            json={
                "id": 9019,
                "method": "slim.request",
                "params": [player_id, ["playlist", "resume", "session-save", "noplay:1"]],
            },
        )
        assert resume_missing_response.status_code == 200
        assert "Saved playlist not found" in resume_missing_response.json()["result"]["error"]

# =============================================================================
# JSON-RPC with Library Data Tests
# =============================================================================


class TestJsonRpcWithData:
    """Tests for JSON-RPC with actual library data."""

    @pytest.fixture
    async def populated_client(
        self, db: LibraryDb, library: MusicLibrary, registry: PlayerRegistry
    ) -> AsyncClient:
        """Create a client with some tracks in the library."""
        # Add test tracks
        await db.upsert_tracks(
            [
                # Ensure deterministic genre ids in this fixture:
                # first encountered genre becomes id=1, second becomes id=2, etc.
                UpsertTrack(
                    path="/music/artist1/album1/track1.mp3",
                    title="First Song",
                    artist="Artist One",
                    album="Album One",
                    year=2020,
                    track_no=1,
                    duration_ms=180000,
                    genres=("Rock",),
                ),
                UpsertTrack(
                    path="/music/artist1/album1/track2.mp3",
                    title="Second Song",
                    artist="Artist One",
                    album="Album One",
                    year=2020,
                    track_no=2,
                    duration_ms=200000,
                    genres=("Rock",),
                ),
                # Add a second album for Artist One so sort:albums has a real album_count difference.
                UpsertTrack(
                    path="/music/artist1/album3/track1.mp3",
                    title="Bonus Song",
                    artist="Artist One",
                    album="Album Three",
                    year=2022,
                    track_no=1,
                    duration_ms=210000,
                    genres=("Rock",),
                ),
                UpsertTrack(
                    path="/music/artist2/album2/track1.mp3",
                    title="Another Track",
                    artist="Artist Two",
                    album="Album Two",
                    year=2021,
                    track_no=1,
                    duration_ms=240000,
                    genres=("Jazz",),
                    compilation=True,
                ),
                UpsertTrack(
                    path="/music/artist0/album0/track1.mp3",
                    title="Zed Song",
                    artist="Artist Zero",
                    album="Album Zero",
                    year=2019,
                    track_no=1,
                    duration_ms=150000,
                    genres=("Metal",),
                ),
            ]
        )
        await db.commit()

        from resonance.streaming.server import StreamingServer

        streaming_server = StreamingServer()
        server = WebServer(
            player_registry=registry,
            music_library=library,
            streaming_server=streaming_server,
        )
        transport = ASGITransport(app=server.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client

    async def test_jsonrpc_genres_with_data(self, populated_client: AsyncClient) -> None:
        """Test genres command with data (count + loop shape)."""
        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 930,
                "method": "slim.request",
                "params": ["-", ["genres", 0, 100]],
            },
        )
        assert response.status_code == 200
        data = response.json()

        # From fixture: Rock, Jazz, Metal
        assert data["result"]["count"] == 3
        loop = data["result"]["genres_loop"]
        assert len(loop) == 3

        names = [g["genre"] for g in loop]
        assert "Rock" in names
        assert "Jazz" in names
        assert "Metal" in names

        # Track counts from fixture:
        # Rock: 3 tracks, Jazz: 1, Metal: 1
        by_name = {g["genre"]: g for g in loop}
        assert by_name["Rock"]["tracks"] == 3
        assert by_name["Jazz"]["tracks"] == 1
        assert by_name["Metal"]["tracks"] == 1

    async def test_jsonrpc_genres_tags_gating(self, populated_client: AsyncClient) -> None:
        """genres tags: should gate fields LMS-ish (id always present)."""
        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 931,
                "method": "slim.request",
                "params": ["-", ["genres", 0, 100, "tags:i"]],
            },
        )
        assert response.status_code == 200
        data = response.json()

        assert data["result"]["count"] == 3
        loop = data["result"]["genres_loop"]
        assert len(loop) == 3

        for item in loop:
            assert "id" in item
            assert "genre" not in item
            assert "tracks" not in item

    async def test_jsonrpc_genres_paging_count_is_total(
        self, populated_client: AsyncClient
    ) -> None:
        """genres paging: count must be total matches, not page size."""
        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 932,
                "method": "slim.request",
                "params": ["-", ["genres", 0, 1]],
            },
        )
        assert response.status_code == 200
        data = response.json()

        assert data["result"]["count"] == 3
        assert len(data["result"]["genres_loop"]) == 1

    # -------------------------------------------------------------------------
    # roles command tests (Phase 3: Role Discovery)
    # -------------------------------------------------------------------------

    async def test_jsonrpc_roles_discovery(self, populated_client: AsyncClient) -> None:
        """Test roles command returns seeded roles for discovery."""
        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 940,
                "method": "slim.request",
                "params": ["-", ["roles", 0, 100]],
            },
        )
        assert response.status_code == 200
        data = response.json()

        # Seeded roles: artist, albumartist, composer, conductor, band
        assert data["result"]["count"] == 5
        loop = data["result"]["roles_loop"]
        assert len(loop) == 5

        # All items must have role_id (always present)
        for item in loop:
            assert "role_id" in item
            assert isinstance(item["role_id"], int)

    async def test_jsonrpc_roles_tags_gating(self, populated_client: AsyncClient) -> None:
        """roles tags:t should include role_name."""
        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 941,
                "method": "slim.request",
                "params": ["-", ["roles", 0, 100, "tags:t"]],
            },
        )
        assert response.status_code == 200
        data = response.json()

        assert data["result"]["count"] == 5
        loop = data["result"]["roles_loop"]
        assert len(loop) == 5

        # With tags:t, role_name should be present
        names = [item.get("role_name") for item in loop]
        assert "artist" in names
        assert "albumartist" in names
        assert "composer" in names
        assert "conductor" in names
        assert "band" in names

    async def test_jsonrpc_roles_paging_count_is_total(self, populated_client: AsyncClient) -> None:
        """roles paging: count must be total matches, not page size."""
        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 942,
                "method": "slim.request",
                "params": ["-", ["roles", 0, 2]],
            },
        )
        assert response.status_code == 200
        data = response.json()

        # count is total (5 seeded roles), loop is limited to page size (2)
        assert data["result"]["count"] == 5
        assert len(data["result"]["roles_loop"]) == 2

    async def test_jsonrpc_artists_with_data(self, populated_client: AsyncClient) -> None:
        """Test artists command with data."""
        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 1,
                "method": "slim.request",
                "params": ["-", ["artists", 0, 100]],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["result"]["count"] == 3
        artists = [a["artist"] for a in data["result"]["artists_loop"]]
        assert "Artist One" in artists
        assert "Artist Two" in artists
        assert "Artist Zero" in artists

    async def test_jsonrpc_albums_with_data(self, populated_client: AsyncClient) -> None:
        """Test albums command with data."""
        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 2,
                "method": "slim.request",
                "params": ["-", ["albums", 0, 100]],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["result"]["count"] == 4
        albums = [a["album"] for a in data["result"]["albums_loop"]]
        assert "Album One" in albums
        assert "Album Two" in albums
        assert "Album Zero" in albums
        assert "Album Three" in albums

    async def test_jsonrpc_artists_filter_by_genre_id(self, populated_client: AsyncClient) -> None:
        """
        artists genre_id:<id> should restrict artists to those having tracks in that genre.

        Fixture mapping (deterministic in this test DB):
        - genre_id:1 => Rock (Artist One)
        - genre_id:2 => Jazz (Artist Two)
        - genre_id:3 => Metal (Artist Zero)
        """
        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 920,
                "method": "slim.request",
                "params": ["-", ["artists", 0, 100, "genre_id:1"]],
            },
        )
        assert response.status_code == 200
        data = response.json()

        assert data["result"]["count"] == 1
        loop = data["result"]["artists_loop"]
        assert len(loop) == 1
        assert loop[0]["artist"] == "Artist One"

    async def test_jsonrpc_albums_filter_by_genre_id(self, populated_client: AsyncClient) -> None:
        """
        albums genre_id:<id> should restrict albums to those having tracks in that genre.

        genre_id:1 => Rock, which exists on Album One + Album Three in fixture.
        """
        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 921,
                "method": "slim.request",
                "params": ["-", ["albums", 0, 100, "genre_id:1"]],
            },
        )
        assert response.status_code == 200
        data = response.json()

        assert data["result"]["count"] == 2
        albums = [a["album"] for a in data["result"]["albums_loop"]]
        assert "Album One" in albums
        assert "Album Three" in albums

    async def test_jsonrpc_titles_filter_by_genre_id(self, populated_client: AsyncClient) -> None:
        """
        titles genre_id:<id> should restrict titles to those in that genre.

        genre_id:1 => Rock, which exists on 3 tracks in fixture:
        - First Song
        - Second Song
        - Bonus Song
        """
        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 922,
                "method": "slim.request",
                "params": ["-", ["titles", 0, 100, "genre_id:1"]],
            },
        )
        assert response.status_code == 200
        data = response.json()

        assert data["result"]["count"] == 3
        titles = [t["title"] for t in data["result"]["titles_loop"]]
        assert "First Song" in titles
        assert "Second Song" in titles
        assert "Bonus Song" in titles

    async def test_jsonrpc_albums_filter_by_compilation(
        self, populated_client: AsyncClient
    ) -> None:
        """
        albums compilation:1 should restrict albums to compilation albums.

        Fixture:
        - Album Two is marked compilation=True (track-level), aggregated to album-level.
        """
        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 940,
                "method": "slim.request",
                "params": ["-", ["albums", 0, 100, "compilation:1"]],
            },
        )
        assert response.status_code == 200
        data = response.json()

        assert data["result"]["count"] == 1
        loop = data["result"]["albums_loop"]
        assert len(loop) == 1
        assert loop[0]["album"] == "Album Two"

    async def test_jsonrpc_albums_filter_by_compilation_and_genre_id(
        self, populated_client: AsyncClient
    ) -> None:
        """
        albums compilation:1 + genre_id:<id> should AND-filter (intersection).

        We discover the concrete genre_id via the genres command to avoid relying on
        implicit AUTOINCREMENT ordering.
        """
        # Discover Jazz genre_id via genres listing
        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 9410,
                "method": "slim.request",
                "params": ["-", ["genres", 0, 100]],
            },
        )
        assert response.status_code == 200
        genres_data = response.json()
        jazz = next(g for g in genres_data["result"]["genres_loop"] if g["genre"] == "Jazz")
        jazz_id = jazz["id"]

        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 942,
                "method": "slim.request",
                "params": ["-", ["albums", 0, 100, "compilation:1", f"genre_id:{jazz_id}"]],
            },
        )
        assert response.status_code == 200
        data = response.json()

        assert data["result"]["count"] == 1
        loop = data["result"]["albums_loop"]
        assert len(loop) == 1
        assert loop[0]["album"] == "Album Two"

    async def test_jsonrpc_albums_filter_by_compilation_and_year(
        self, populated_client: AsyncClient
    ) -> None:
        """
        albums compilation:1 + year:<yyyy> should AND-filter (intersection).

        Fixture:
        - Album Two is compilation=True and has year=2021.
        """
        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 948,
                "method": "slim.request",
                "params": ["-", ["albums", 0, 100, "compilation:1", "year:2021"]],
            },
        )
        assert response.status_code == 200
        data = response.json()

        assert data["result"]["count"] == 1
        loop = data["result"]["albums_loop"]
        assert len(loop) == 1
        assert loop[0]["album"] == "Album Two"

    async def test_jsonrpc_artists_filter_by_compilation(
        self, populated_client: AsyncClient
    ) -> None:
        """
        artists compilation:1 should restrict artists to those having at least one compilation track.

        Fixture:
        - Only Artist Two has a compilation=True track ("Another Track").
        """
        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 951,
                "method": "slim.request",
                "params": ["-", ["artists", 0, 100, "compilation:1"]],
            },
        )
        assert response.status_code == 200
        data = response.json()

        assert data["result"]["count"] == 1
        loop = data["result"]["artists_loop"]
        assert len(loop) == 1
        assert loop[0]["artist"] == "Artist Two"

    async def test_jsonrpc_albums_filter_by_compilation_and_artist_id(
        self, populated_client: AsyncClient
    ) -> None:
        """
        albums compilation:1 + artist_id:<id> should AND-filter (intersection).

        Fixture:
        - Album Two is compilation=True and belongs to Artist Two.
        """
        # Fetch artists to obtain Artist Two id
        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 949,
                "method": "slim.request",
                "params": ["-", ["artists", 0, 100]],
            },
        )
        assert response.status_code == 200
        data = response.json()

        artist_two = next(a for a in data["result"]["artists_loop"] if a["artist"] == "Artist Two")
        artist_id = artist_two["id"]

        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 950,
                "method": "slim.request",
                "params": ["-", ["albums", 0, 100, "compilation:1", f"artist_id:{artist_id}"]],
            },
        )
        assert response.status_code == 200
        data = response.json()

        assert data["result"]["count"] == 1
        loop = data["result"]["albums_loop"]
        assert len(loop) == 1
        assert loop[0]["album"] == "Album Two"

    async def test_jsonrpc_titles_filter_by_genre_id_and_year(
        self, populated_client: AsyncClient
    ) -> None:
        """
        titles genre_id:<id> + year:<yyyy> should AND-filter (intersection).

        Fixture:
        - Rock (genre_id:1) tracks are in 2020 and 2022.
        - For year:2020, only the two Album One tracks match.
        """
        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 933,
                "method": "slim.request",
                "params": ["-", ["titles", 0, 100, "genre_id:1", "year:2020"]],
            },
        )
        assert response.status_code == 200
        data = response.json()

        assert data["result"]["count"] == 2
        titles = [t["title"] for t in data["result"]["titles_loop"]]
        assert "First Song" in titles
        assert "Second Song" in titles
        assert "Bonus Song" not in titles

    async def test_jsonrpc_titles_filter_by_compilation_and_genre_id(
        self, populated_client: AsyncClient
    ) -> None:
        """
        titles compilation:1 + genre_id:<id> should AND-filter (intersection).

        We discover the concrete genre_id via the genres command to avoid relying on
        implicit AUTOINCREMENT ordering.
        """
        # Discover Jazz genre_id via genres listing
        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 9411,
                "method": "slim.request",
                "params": ["-", ["genres", 0, 100]],
            },
        )
        assert response.status_code == 200
        genres_data = response.json()
        jazz = next(g for g in genres_data["result"]["genres_loop"] if g["genre"] == "Jazz")
        jazz_id = jazz["id"]

        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 941,
                "method": "slim.request",
                "params": ["-", ["titles", 0, 100, "compilation:1", f"genre_id:{jazz_id}"]],
            },
        )
        assert response.status_code == 200
        data = response.json()

        assert data["result"]["count"] == 1
        loop = data["result"]["titles_loop"]
        assert len(loop) == 1
        assert loop[0]["title"] == "Another Track"

    async def test_jsonrpc_titles_filter_by_compilation_and_year(
        self, populated_client: AsyncClient
    ) -> None:
        """
        titles compilation:1 + year:<yyyy> should AND-filter (intersection).

        Fixture:
        - Only "Another Track" is compilation=True and has year=2021.
        """
        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 943,
                "method": "slim.request",
                "params": ["-", ["titles", 0, 100, "compilation:1", "year:2021"]],
            },
        )
        assert response.status_code == 200
        data = response.json()

        assert data["result"]["count"] == 1
        loop = data["result"]["titles_loop"]
        assert len(loop) == 1
        assert loop[0]["title"] == "Another Track"

    async def test_jsonrpc_titles_filter_by_compilation_and_artist_id(
        self, populated_client: AsyncClient
    ) -> None:
        """
        titles compilation:1 + artist_id:<id> should AND-filter (intersection).

        Fixture:
        - Only Artist Two has a compilation track ("Another Track").
        """
        # Fetch artists to obtain Artist Two id
        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 944,
                "method": "slim.request",
                "params": ["-", ["artists", 0, 100]],
            },
        )
        assert response.status_code == 200
        data = response.json()

        artist_two = next(a for a in data["result"]["artists_loop"] if a["artist"] == "Artist Two")
        artist_id = artist_two["id"]

        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 945,
                "method": "slim.request",
                "params": ["-", ["titles", 0, 100, "compilation:1", f"artist_id:{artist_id}"]],
            },
        )
        assert response.status_code == 200
        data = response.json()

        assert data["result"]["count"] == 1
        loop = data["result"]["titles_loop"]
        assert len(loop) == 1
        assert loop[0]["title"] == "Another Track"

    async def test_jsonrpc_titles_filter_by_compilation_and_album_id(
        self, populated_client: AsyncClient
    ) -> None:
        """
        titles compilation:1 + album_id:<id> should AND-filter (intersection).

        Fixture:
        - "Another Track" is on Album Two, which is compilation=True.
        """
        # Fetch albums to obtain Album Two id
        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 946,
                "method": "slim.request",
                "params": ["-", ["albums", 0, 100]],
            },
        )
        assert response.status_code == 200
        data = response.json()

        album_two = next(a for a in data["result"]["albums_loop"] if a["album"] == "Album Two")
        album_id = album_two["id"]

        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 947,
                "method": "slim.request",
                "params": ["-", ["titles", 0, 100, "compilation:1", f"album_id:{album_id}"]],
            },
        )
        assert response.status_code == 200
        data = response.json()

        assert data["result"]["count"] == 1
        loop = data["result"]["titles_loop"]
        assert len(loop) == 1
        assert loop[0]["title"] == "Another Track"

    async def test_jsonrpc_titles_filter_by_genre_id_and_artist_id(
        self, populated_client: AsyncClient
    ) -> None:
        """
        titles genre_id:<id> + artist_id:<id> should AND-filter (intersection).

        Fixture:
        - Rock (genre_id:1) belongs to Artist One only.
        - Therefore filtering by (Rock + Artist One) returns 3 tracks.
        """
        # Fetch artists to obtain Artist One id
        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 934,
                "method": "slim.request",
                "params": ["-", ["artists", 0, 100]],
            },
        )
        assert response.status_code == 200
        data = response.json()

        artist_one = next(a for a in data["result"]["artists_loop"] if a["artist"] == "Artist One")
        artist_id = artist_one["id"]

        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 935,
                "method": "slim.request",
                "params": ["-", ["titles", 0, 100, f"genre_id:1", f"artist_id:{artist_id}"]],
            },
        )
        assert response.status_code == 200
        data = response.json()

        assert data["result"]["count"] == 3
        titles = [t["title"] for t in data["result"]["titles_loop"]]
        assert "First Song" in titles
        assert "Second Song" in titles
        assert "Bonus Song" in titles

    async def test_jsonrpc_albums_filter_by_genre_id_and_year(
        self, populated_client: AsyncClient
    ) -> None:
        """
        albums genre_id:<id> + year:<yyyy> should AND-filter (intersection).

        Fixture:
        - Rock (genre_id:1) albums are Album One (2020) and Album Three (2022).
        - For year:2020, only Album One matches.
        """
        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 936,
                "method": "slim.request",
                "params": ["-", ["albums", 0, 100, "genre_id:1", "year:2020"]],
            },
        )
        assert response.status_code == 200
        data = response.json()

        assert data["result"]["count"] == 1
        loop = data["result"]["albums_loop"]
        assert len(loop) == 1
        assert loop[0]["album"] == "Album One"

    async def test_jsonrpc_artists_filter_by_year(self, populated_client: AsyncClient) -> None:
        """artists year:<yyyy> should restrict artists to those having tracks in that year."""
        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 912,
                "method": "slim.request",
                "params": ["-", ["artists", 0, 100, "year:2020"]],
            },
        )
        assert response.status_code == 200
        data = response.json()

        # In fixture data, only Artist One has tracks in year=2020.
        assert data["result"]["count"] == 1
        loop = data["result"]["artists_loop"]
        assert len(loop) == 1
        assert loop[0]["artist"] == "Artist One"

    async def test_jsonrpc_albums_filter_by_year(self, populated_client: AsyncClient) -> None:
        """albums year:<yyyy> should restrict albums to that year."""
        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 910,
                "method": "slim.request",
                "params": ["-", ["albums", 0, 100, "year:2020"]],
            },
        )
        assert response.status_code == 200
        data = response.json()

        albums = data["result"]["albums_loop"]
        # In fixture data, only Album One is year=2020.
        assert data["result"]["count"] == 1
        assert len(albums) == 1
        assert albums[0]["album"] == "Album One"
        assert albums[0]["year"] == 2020

    async def test_jsonrpc_albums_filter_by_artist_id_and_year(
        self, populated_client: AsyncClient
    ) -> None:
        """albums artist_id:<id> + year:<yyyy> should AND-filter (intersection)."""
        # First fetch artists to obtain Artist One id
        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 913,
                "method": "slim.request",
                "params": ["-", ["artists", 0, 100]],
            },
        )
        assert response.status_code == 200
        data = response.json()
        artist_one = next(a for a in data["result"]["artists_loop"] if a["artist"] == "Artist One")
        artist_id = artist_one["id"]

        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 914,
                "method": "slim.request",
                "params": ["-", ["albums", 0, 100, f"artist_id:{artist_id}", "year:2020"]],
            },
        )
        assert response.status_code == 200
        data = response.json()

        # Artist One has albums in 2020 (Album One) and 2022 (Album Three) -> intersection w/ 2020 is 1 album.
        assert data["result"]["count"] == 1
        albums = data["result"]["albums_loop"]
        assert len(albums) == 1
        assert albums[0]["album"] == "Album One"
        assert albums[0]["year"] == 2020

    async def test_jsonrpc_titles_with_data(self, populated_client: AsyncClient) -> None:
        """Test titles command with data."""
        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 3,
                "method": "slim.request",
                "params": ["-", ["titles", 0, 100]],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["result"]["count"] == 5
        titles = [t["title"] for t in data["result"]["titles_loop"]]
        assert "First Song" in titles
        assert "Second Song" in titles
        assert "Another Track" in titles
        assert "Zed Song" in titles

    async def test_jsonrpc_titles_filter_by_year(self, populated_client: AsyncClient) -> None:
        """titles year:<yyyy> should restrict tracks to that year."""
        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 911,
                "method": "slim.request",
                "params": ["-", ["titles", 0, 100, "year:2020"]],
            },
        )
        assert response.status_code == 200
        data = response.json()

        titles_loop = data["result"]["titles_loop"]
        titles = [t["title"] for t in titles_loop]

        # In fixture data, the two tracks from Album One are year=2020.
        assert data["result"]["count"] == 2
        assert set(titles) == {"First Song", "Second Song"}
        assert all(t.get("year") == 2020 for t in titles_loop)

    async def test_jsonrpc_titles_filter_by_artist_id_and_year(
        self, populated_client: AsyncClient
    ) -> None:
        """titles artist_id:<id> + year:<yyyy> should AND-filter (intersection)."""
        # First fetch artists to obtain Artist One id
        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 915,
                "method": "slim.request",
                "params": ["-", ["artists", 0, 100]],
            },
        )
        assert response.status_code == 200
        data = response.json()
        artist_one = next(a for a in data["result"]["artists_loop"] if a["artist"] == "Artist One")
        artist_id = artist_one["id"]

        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 916,
                "method": "slim.request",
                "params": ["-", ["titles", 0, 100, f"artist_id:{artist_id}", "year:2020"]],
            },
        )
        assert response.status_code == 200
        data = response.json()

        titles_loop = data["result"]["titles_loop"]
        titles = [t["title"] for t in titles_loop]

        # Artist One has 2020 tracks (First/Second) and a 2022 track (Bonus). Intersection w/ 2020 is 2.
        assert data["result"]["count"] == 2
        assert set(titles) == {"First Song", "Second Song"}
        assert all(t.get("year") == 2020 for t in titles_loop)

    async def test_jsonrpc_titles_filter_by_album_id_and_year(
        self, populated_client: AsyncClient
    ) -> None:
        """titles album_id:<id> + year:<yyyy> should AND-filter (intersection)."""
        # Fetch albums to obtain Album One id
        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 917,
                "method": "slim.request",
                "params": ["-", ["albums", 0, 100]],
            },
        )
        assert response.status_code == 200
        data = response.json()
        album_one = next(a for a in data["result"]["albums_loop"] if a.get("album") == "Album One")
        album_id = album_one["id"]

        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 918,
                "method": "slim.request",
                "params": ["-", ["titles", 0, 100, f"album_id:{album_id}", "year:2020"]],
            },
        )
        assert response.status_code == 200
        data = response.json()

        titles_loop = data["result"]["titles_loop"]
        titles = [t["title"] for t in titles_loop]

        # Album One is 2020, so intersection should still be Album One's two tracks.
        assert data["result"]["count"] == 2
        assert set(titles) == {"First Song", "Second Song"}
        assert all(t.get("year") == 2020 for t in titles_loop)

    async def test_jsonrpc_artists_paging_count_is_total(
        self, populated_client: AsyncClient
    ) -> None:
        """LMS-style: count is total matches, not page size (artists)."""
        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 200,
                "method": "slim.request",
                "params": ["-", ["artists", 0, 1]],
            },
        )
        assert response.status_code == 200
        data = response.json()

        # We inserted 3 artists in the fixture, but requested only 1 item.
        assert data["result"]["count"] == 3
        assert len(data["result"]["artists_loop"]) == 1

    async def test_jsonrpc_artists_sort_artist_orders_by_name(
        self, populated_client: AsyncClient
    ) -> None:
        """artists sort:artist should order alphabetically by artist name."""
        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 202,
                "method": "slim.request",
                "params": ["-", ["artists", 0, 100, "sort:artist"]],
            },
        )
        assert response.status_code == 200
        data = response.json()
        artists = [a["artist"] for a in data["result"]["artists_loop"]]
        # Alphabetical: Artist One, Artist Two, Artist Zero
        assert artists == ["Artist One", "Artist Two", "Artist Zero"]

    async def test_jsonrpc_artists_sort_id_orders_by_id(
        self, populated_client: AsyncClient
    ) -> None:
        """artists sort:id should order by stable numeric artist id."""
        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 203,
                "method": "slim.request",
                "params": ["-", ["artists", 0, 100, "sort:id"]],
            },
        )
        assert response.status_code == 200
        data = response.json()
        ids = [a["id"] for a in data["result"]["artists_loop"]]
        assert ids == sorted(ids)

    async def test_jsonrpc_artists_sort_albums_orders_by_album_count_desc(
        self, populated_client: AsyncClient
    ) -> None:
        """artists sort:albums should order by album count desc, then name."""
        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 204,
                "method": "slim.request",
                "params": ["-", ["artists", 0, 100, "sort:albums"]],
            },
        )
        assert response.status_code == 200
        data = response.json()

        # Artist One has 2 albums, the others have 1 => Artist One must be ranked first.
        artists = [a["artist"] for a in data["result"]["artists_loop"]]
        assert artists[0] == "Artist One"

    async def test_jsonrpc_albums_paging_count_is_total(
        self, populated_client: AsyncClient
    ) -> None:
        """LMS-style: count is total matches, not page size (albums)."""
        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 201,
                "method": "slim.request",
                "params": ["-", ["albums", 0, 1]],
            },
        )
        assert response.status_code == 200
        data = response.json()

        # Fixture inserts 4 distinct albums; we requested only 1 item.
        assert data["result"]["count"] == 4
        assert len(data["result"]["albums_loop"]) == 1

    async def test_jsonrpc_titles_paging_count_is_total(
        self, populated_client: AsyncClient
    ) -> None:
        """LMS-style: count is total matches, not page size (titles)."""
        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 202,
                "method": "slim.request",
                "params": ["-", ["titles", 0, 1]],
            },
        )
        assert response.status_code == 200
        data = response.json()

        # Fixture inserts 5 tracks; we requested only 1 item.
        assert data["result"]["count"] == 5
        assert len(data["result"]["titles_loop"]) == 1

    async def test_jsonrpc_artists_tags_field_gating_minimal(
        self, populated_client: AsyncClient
    ) -> None:
        """
        artists tags: should gate returned fields.

        We request only id + artist name:
        - i => id
        - a => artist
        """
        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 900,
                "method": "slim.request",
                "params": ["-", ["artists", 0, 1, "tags:ia"]],
            },
        )
        assert response.status_code == 200
        data = response.json()

        assert data["result"]["count"] == 3
        loop = data["result"]["artists_loop"]
        assert len(loop) == 1

        item = loop[0]
        assert "id" in item
        assert "artist" in item
        assert "albums" not in item

    async def test_jsonrpc_albums_tags_field_gating_minimal(
        self, populated_client: AsyncClient
    ) -> None:
        """
        albums tags: should gate returned fields.

        We request only:
        - i => id
        - l => album title
        """
        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 901,
                "method": "slim.request",
                "params": ["-", ["albums", 0, 1, "tags:il"]],
            },
        )
        assert response.status_code == 200
        data = response.json()

        assert data["result"]["count"] == 4
        loop = data["result"]["albums_loop"]
        assert len(loop) == 1

        item = loop[0]
        assert "id" in item
        assert "album" in item
        assert "artist" not in item
        assert "year" not in item
        assert "tracks" not in item
        assert "artist_id" not in item

    async def test_jsonrpc_titles_tags_field_gating_minimal_includes_url(
        self, populated_client: AsyncClient
    ) -> None:
        """
        titles tags: should gate returned fields.

        We request only id + title. In addition, servers typically must include `url`
        for playback/navigation, so we allow `url` to be present even if not requested.
        """
        response = await populated_client.post(
            "/jsonrpc.js",
            json={
                "id": 902,
                "method": "slim.request",
                "params": ["-", ["titles", 0, 1, "tags:it"]],
            },
        )
        assert response.status_code == 200
        data = response.json()

        assert data["result"]["count"] == 5
        loop = data["result"]["titles_loop"]
        assert len(loop) == 1

        item = loop[0]
        assert "id" in item
        assert "title" in item

        # `url` is required by many clients; allow as always-present.
        assert "url" in item

        # Gated fields should not be present when not requested.
        assert "artist" not in item
        assert "album" not in item
        assert "year" not in item
        assert "duration" not in item
        assert "tracknum" not in item


@pytest.mark.asyncio
async def test_artwork_music_cover_no_ext_falls_back_to_track_lookup(
    library: MusicLibrary,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """
    Regression:
    /music/{id}/cover must accept IDs that resolve as track IDs, not only album IDs.
    """
    cover_path = tmp_path / "cover.jpg"
    cover_bytes = b"\xff\xd8\xff\xd9"
    cover_path.write_bytes(cover_bytes)

    class _FakeDb:
        async def list_tracks_by_album(self, album_id: int, *, offset: int, limit: int, order_by: str = "tracknum"):
            return []

        async def get_track_by_id(self, track_id: int):
            return SimpleNamespace(path=str(cover_path))

    class _FakeArtworkManager:
        async def get_artwork(self, path: str):
            assert path == str(cover_path)
            return cover_bytes, "image/jpeg", "etag"

    server = WebServer(
        player_registry=PlayerRegistry(),
        music_library=library,
        artwork_manager=SimpleNamespace(),
    )
    transport = ASGITransport(app=server.app)
    async with AsyncClient(transport=transport, base_url="http://test") as artwork_client:
        monkeypatch.setattr(artwork_routes, "_music_library", SimpleNamespace(_db=_FakeDb()))
        monkeypatch.setattr(artwork_routes, "_artwork_manager", _FakeArtworkManager())
        response = await artwork_client.get("/music/777/cover")

    assert response.status_code == 200
    assert response.content == cover_bytes
    assert response.headers["content-type"].startswith("image/jpeg")


@pytest.mark.asyncio
async def test_artwork_cover_with_spec_preserves_content_type_without_pillow(
    library: MusicLibrary,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """
    Regression:
    When Pillow is unavailable, cover_{spec} must keep original bytes/content-type.
    """
    cover_path = tmp_path / "cover.png"
    cover_bytes = b"\x89PNG\r\n\x1a\nnot-a-real-png"
    cover_path.write_bytes(cover_bytes)

    class _FakeDb:
        async def list_tracks_by_album(self, album_id: int, *, offset: int, limit: int, order_by: str = "tracknum"):
            return [SimpleNamespace(path=str(cover_path))]

        async def get_track_by_id(self, track_id: int):
            return None

    class _FakeArtworkManager:
        async def get_artwork(self, path: str):
            assert path == str(cover_path)
            return cover_bytes, "image/png", "etag"

    server = WebServer(
        player_registry=PlayerRegistry(),
        music_library=library,
        artwork_manager=SimpleNamespace(),
    )
    transport = ASGITransport(app=server.app)
    async with AsyncClient(transport=transport, base_url="http://test") as artwork_client:
        monkeypatch.setattr(artwork_routes, "PIL_AVAILABLE", False)
        monkeypatch.setattr(artwork_routes, "_music_library", SimpleNamespace(_db=_FakeDb()))
        monkeypatch.setattr(artwork_routes, "_artwork_manager", _FakeArtworkManager())
        response = await artwork_client.get("/music/3/cover_41x41_m")

    assert response.status_code == 200
    assert response.content == cover_bytes
    assert response.headers["content-type"].startswith("image/png")


@pytest.mark.asyncio
async def test_web_server_start_waits_for_uvicorn_started(
    library: MusicLibrary,
    registry: PlayerRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """start() must wait for uvicorn startup and keep a running serve task."""

    class _FakeUvicornServer:
        def __init__(self, config: Any) -> None:
            self.config = config
            self.started = False
            self.should_exit = False

        async def serve(self) -> None:
            await asyncio.sleep(0.01)
            self.started = True
            while not self.should_exit:
                await asyncio.sleep(0.01)

    async def _probe_healthcheck(self: WebServer) -> bool:
        return bool(self._server is not None and self._server.started)

    monkeypatch.setattr("resonance.web.server.uvicorn.Server", _FakeUvicornServer, raising=True)
    monkeypatch.setattr(WebServer, "_probe_healthcheck", _probe_healthcheck, raising=True)

    server = WebServer(player_registry=registry, music_library=library)
    await server.start(host="0.0.0.0", port=9000)

    assert server._server is not None
    assert server._server.started is True
    assert server._serve_task is not None
    assert server._serve_task.done() is False

    await server.stop()

    assert server._server is None
    assert server._serve_task is None


@pytest.mark.asyncio
async def test_web_server_start_propagates_uvicorn_startup_failure(
    library: MusicLibrary,
    registry: PlayerRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """start() must fail fast if uvicorn exits with an exception."""

    class _FakeUvicornServer:
        def __init__(self, config: Any) -> None:
            self.config = config
            self.started = False
            self.should_exit = False

        async def serve(self) -> None:
            raise OSError("bind failed")

    async def _probe_healthcheck(self: WebServer) -> bool:
        return False

    monkeypatch.setattr("resonance.web.server.uvicorn.Server", _FakeUvicornServer, raising=True)
    monkeypatch.setattr(WebServer, "_probe_healthcheck", _probe_healthcheck, raising=True)

    server = WebServer(player_registry=registry, music_library=library)

    with pytest.raises(RuntimeError, match="failed to start"):
        await server.start(host="0.0.0.0", port=9000)

    assert server._server is None
    assert server._serve_task is None
    assert server.cometd_manager._started is False


@pytest.mark.asyncio
async def test_web_server_stop_uvicorn_task_does_not_hang_on_stubborn_task(
    library: MusicLibrary,
    registry: PlayerRegistry,
) -> None:
    """_stop_uvicorn_task() must return even if the task resists cancellation."""

    async def _stubborn_serve_task() -> None:
        while True:
            try:
                await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                # Simulate a misbehaving task that ignores cancellation.
                continue

    server = WebServer(player_registry=registry, music_library=library)
    server._server = SimpleNamespace(should_exit=False)
    server._serve_task = asyncio.create_task(_stubborn_serve_task())

    await asyncio.wait_for(server._stop_uvicorn_task(force_cancel=True), timeout=3.0)

    assert server._server is None
    assert server._serve_task is None
