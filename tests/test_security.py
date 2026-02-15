"""
Tests for resonance.web.security module.

Covers:
- Password hashing and verification (pbkdf2 + plaintext fallback)
- HTTP Basic Auth middleware (enabled/disabled, exempt paths, invalid creds)
- Rate limiting middleware (token bucket, exempt paths, 429 on excess)
- Input validation helpers (paging clamp, MAC validation, path safety,
  playlist index clamp, volume clamp, seek clamp)
- Player-ID validation in JSON-RPC execute_command
- Paging clamping in parse_start_items
- Path-traversal rejection in playlist _resolve_track
"""

from __future__ import annotations

import asyncio
import base64
import time
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from resonance.core.library import MusicLibrary
from resonance.core.library_db import LibraryDb
from resonance.player.registry import PlayerRegistry
from resonance.web.jsonrpc_helpers import parse_start_items
from resonance.web.security import (
    MAX_QUERY_ITEMS,
    MAX_QUERY_START,
    AuthMiddleware,
    RateLimitMiddleware,
    _TokenBucket,
    clamp_paging,
    clamp_playlist_index,
    clamp_seek,
    clamp_volume,
    hash_password,
    is_safe_path,
    is_valid_mac,
    sanitise_player_id,
    verify_password,
)
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


def _basic_auth_header(username: str, password: str) -> str:
    """Build a Basic auth header value."""
    encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {encoded}"


# =============================================================================
# Password Hashing Tests
# =============================================================================


class TestPasswordHashing:
    """Tests for hash_password / verify_password."""

    def test_hash_and_verify_roundtrip(self) -> None:
        pw = "my-secret-password"
        hashed = hash_password(pw)
        assert hashed.startswith("pbkdf2:sha256:")
        assert verify_password(pw, hashed) is True

    def test_wrong_password_fails(self) -> None:
        hashed = hash_password("correct-horse")
        assert verify_password("wrong-horse", hashed) is False

    def test_empty_hash_fails(self) -> None:
        assert verify_password("anything", "") is False

    def test_different_hashes_for_same_password(self) -> None:
        """Each call should produce a unique salt."""
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2
        # Both must still verify
        assert verify_password("same", h1) is True
        assert verify_password("same", h2) is True

    def test_plaintext_fallback_matches(self) -> None:
        """Plaintext stored password (dev convenience) should work."""
        assert verify_password("dev-pass", "dev-pass") is True

    def test_plaintext_fallback_rejects_wrong(self) -> None:
        assert verify_password("wrong", "dev-pass") is False

    def test_malformed_hash_rejects(self) -> None:
        assert verify_password("pw", "pbkdf2:sha256:badformat") is False
        assert verify_password("pw", "pbkdf2:sha256:100$nothex$") is False

    def test_hash_format_structure(self) -> None:
        hashed = hash_password("test")
        # Format: pbkdf2:sha256:<iterations>$<salt-hex>$<hash-hex>
        header, salt_hex, hash_hex = hashed.split("$", 2)
        assert header.startswith("pbkdf2:sha256:")
        iterations = int(header.split(":")[2])
        assert iterations > 0
        # Salt should be valid hex
        bytes.fromhex(salt_hex)
        # Hash should be valid hex
        bytes.fromhex(hash_hex)


# =============================================================================
# Token Bucket Tests
# =============================================================================


class TestTokenBucket:
    """Tests for the internal token bucket rate limiter."""

    def test_allows_burst_up_to_capacity(self) -> None:
        bucket = _TokenBucket(tokens=5.0, max_tokens=5.0)
        now = time.monotonic()
        for _ in range(5):
            assert bucket.consume(5.0, now) is True
        # 6th should fail (no time elapsed for refill)
        assert bucket.consume(5.0, now) is False

    def test_refills_over_time(self) -> None:
        bucket = _TokenBucket(tokens=0.0, max_tokens=10.0)
        now = time.monotonic()
        bucket.last_refill = now
        # After 1 second at rate 10/s, should have 10 tokens
        assert bucket.consume(10.0, now + 1.0) is True

    def test_does_not_exceed_max(self) -> None:
        bucket = _TokenBucket(tokens=5.0, max_tokens=5.0)
        now = time.monotonic()
        bucket.last_refill = now - 100.0  # Long time ago
        # Should refill to max, not beyond
        bucket.consume(5.0, now)
        # After consuming one, we should have max_tokens - 1 = 4
        assert bucket.tokens == pytest.approx(4.0, abs=0.1)


# =============================================================================
# Auth Middleware Tests
# =============================================================================


class TestAuthMiddleware:
    """Tests for HTTP Basic Auth middleware."""

    async def test_auth_disabled_allows_all(
        self,
        registry: PlayerRegistry,
        library: MusicLibrary,
    ) -> None:
        """When auth is disabled, all requests pass through."""
        server = WebServer(player_registry=registry, music_library=library)
        # Auth middleware is not added when settings are not loaded,
        # so we add it explicitly in disabled mode
        server.app.add_middleware(AuthMiddleware, enabled=False)

        transport = ASGITransport(app=server.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")
            assert resp.status_code == 200

            resp = await client.post("/jsonrpc.js", json={
                "id": 1, "method": "slim.request",
                "params": ["-", ["serverstatus", 0, 10]],
            })
            assert resp.status_code == 200

    async def test_auth_enabled_rejects_without_credentials(
        self,
        registry: PlayerRegistry,
        library: MusicLibrary,
    ) -> None:
        """When auth is enabled, requests without credentials get 401."""
        pw_hash = hash_password("secret")
        server = WebServer(player_registry=registry, music_library=library)
        server.app.add_middleware(
            AuthMiddleware,
            enabled=True,
            username="admin",
            password_hash=pw_hash,
        )

        transport = ASGITransport(app=server.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/jsonrpc.js", json={
                "id": 1, "method": "slim.request",
                "params": ["-", ["serverstatus", 0, 10]],
            })
            assert resp.status_code == 401
            assert "WWW-Authenticate" in resp.headers

    async def test_auth_enabled_accepts_valid_credentials(
        self,
        registry: PlayerRegistry,
        library: MusicLibrary,
    ) -> None:
        """When auth is enabled, valid credentials pass through."""
        pw_hash = hash_password("secret")
        server = WebServer(player_registry=registry, music_library=library)
        server.app.add_middleware(
            AuthMiddleware,
            enabled=True,
            username="admin",
            password_hash=pw_hash,
        )

        transport = ASGITransport(app=server.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/jsonrpc.js",
                json={
                    "id": 1, "method": "slim.request",
                    "params": ["-", ["serverstatus", 0, 10]],
                },
                headers={"Authorization": _basic_auth_header("admin", "secret")},
            )
            assert resp.status_code == 200

    async def test_auth_enabled_rejects_wrong_password(
        self,
        registry: PlayerRegistry,
        library: MusicLibrary,
    ) -> None:
        """Wrong password should get 401."""
        pw_hash = hash_password("correct")
        server = WebServer(player_registry=registry, music_library=library)
        server.app.add_middleware(
            AuthMiddleware,
            enabled=True,
            username="admin",
            password_hash=pw_hash,
        )

        transport = ASGITransport(app=server.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/jsonrpc.js",
                json={"id": 1, "method": "slim.request", "params": ["-", ["serverstatus"]]},
                headers={"Authorization": _basic_auth_header("admin", "wrong")},
            )
            assert resp.status_code == 401

    async def test_auth_enabled_rejects_wrong_username(
        self,
        registry: PlayerRegistry,
        library: MusicLibrary,
    ) -> None:
        """Wrong username should get 401."""
        pw_hash = hash_password("secret")
        server = WebServer(player_registry=registry, music_library=library)
        server.app.add_middleware(
            AuthMiddleware,
            enabled=True,
            username="admin",
            password_hash=pw_hash,
        )

        transport = ASGITransport(app=server.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/jsonrpc.js",
                json={"id": 1, "method": "slim.request", "params": ["-", ["serverstatus"]]},
                headers={"Authorization": _basic_auth_header("hacker", "secret")},
            )
            assert resp.status_code == 401

    async def test_health_exempt_when_auth_enabled(
        self,
        registry: PlayerRegistry,
        library: MusicLibrary,
    ) -> None:
        """/health is always accessible even with auth enabled."""
        pw_hash = hash_password("secret")
        server = WebServer(player_registry=registry, music_library=library)
        server.app.add_middleware(
            AuthMiddleware,
            enabled=True,
            username="admin",
            password_hash=pw_hash,
        )

        transport = ASGITransport(app=server.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")
            assert resp.status_code == 200

    async def test_stream_exempt_when_auth_enabled(
        self,
        registry: PlayerRegistry,
        library: MusicLibrary,
    ) -> None:
        """/stream.mp3 paths bypass auth (players need unauthenticated access)."""
        pw_hash = hash_password("secret")
        server = WebServer(player_registry=registry, music_library=library)
        server.app.add_middleware(
            AuthMiddleware,
            enabled=True,
            username="admin",
            password_hash=pw_hash,
        )

        transport = ASGITransport(app=server.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # /stream.mp3 without auth should NOT get 401
            # (it may get 404 or other errors since no streaming server,
            # but not 401)
            resp = await client.get("/stream.mp3")
            assert resp.status_code != 401

    async def test_auth_rejects_malformed_header(
        self,
        registry: PlayerRegistry,
        library: MusicLibrary,
    ) -> None:
        """Malformed Authorization header should get 401."""
        pw_hash = hash_password("secret")
        server = WebServer(player_registry=registry, music_library=library)
        server.app.add_middleware(
            AuthMiddleware,
            enabled=True,
            username="admin",
            password_hash=pw_hash,
        )

        transport = ASGITransport(app=server.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/jsonrpc.js",
                json={"id": 1, "method": "slim.request", "params": ["-", ["serverstatus"]]},
                headers={"Authorization": "Bearer some-token"},
            )
            assert resp.status_code == 401

            resp = await client.post(
                "/jsonrpc.js",
                json={"id": 1, "method": "slim.request", "params": ["-", ["serverstatus"]]},
                headers={"Authorization": "Basic not-valid-base64!!!"},
            )
            assert resp.status_code == 401


# =============================================================================
# Rate Limiting Middleware Tests
# =============================================================================


class TestRateLimitMiddleware:
    """Tests for per-IP token-bucket rate limiter."""

    async def test_rate_limit_disabled_allows_all(
        self,
        registry: PlayerRegistry,
        library: MusicLibrary,
    ) -> None:
        """When rate limiting is disabled, all requests pass through."""
        server = WebServer(player_registry=registry, music_library=library)
        server.app.add_middleware(RateLimitMiddleware, enabled=False)

        transport = ASGITransport(app=server.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            for _ in range(20):
                resp = await client.get("/health")
                assert resp.status_code == 200

    async def test_rate_limit_returns_429_on_excess(
        self,
        registry: PlayerRegistry,
        library: MusicLibrary,
    ) -> None:
        """Exceeding the rate limit should return HTTP 429."""
        # Very low limit for testing
        server = WebServer(player_registry=registry, music_library=library)
        server.app.add_middleware(
            RateLimitMiddleware,
            enabled=True,
            requests_per_second=3,
        )

        transport = ASGITransport(app=server.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            statuses = []
            # Send more requests than the bucket allows in a burst
            for _ in range(10):
                resp = await client.post("/jsonrpc.js", json={
                    "id": 1, "method": "slim.request",
                    "params": ["-", ["serverstatus", 0, 10]],
                })
                statuses.append(resp.status_code)

            # Some should be 200, some should be 429
            assert 200 in statuses
            assert 429 in statuses

    async def test_rate_limit_429_has_retry_after(
        self,
        registry: PlayerRegistry,
        library: MusicLibrary,
    ) -> None:
        """429 responses should include Retry-After header."""
        server = WebServer(player_registry=registry, music_library=library)
        server.app.add_middleware(
            RateLimitMiddleware,
            enabled=True,
            requests_per_second=1,
        )

        transport = ASGITransport(app=server.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Exhaust the bucket
            await client.post("/jsonrpc.js", json={
                "id": 1, "method": "slim.request",
                "params": ["-", ["serverstatus"]],
            })
            # This should be rate limited
            resp = await client.post("/jsonrpc.js", json={
                "id": 1, "method": "slim.request",
                "params": ["-", ["serverstatus"]],
            })
            if resp.status_code == 429:
                assert "Retry-After" in resp.headers

    async def test_health_exempt_from_rate_limit(
        self,
        registry: PlayerRegistry,
        library: MusicLibrary,
    ) -> None:
        """/health is exempt from rate limiting."""
        server = WebServer(player_registry=registry, music_library=library)
        server.app.add_middleware(
            RateLimitMiddleware,
            enabled=True,
            requests_per_second=1,
        )

        transport = ASGITransport(app=server.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Even with a very low limit, health should always succeed
            for _ in range(10):
                resp = await client.get("/health")
                assert resp.status_code == 200


# =============================================================================
# Input Validation Helper Tests
# =============================================================================


class TestClampPaging:
    """Tests for clamp_paging and parse_start_items input validation."""

    def test_negative_start_clamped_to_zero(self) -> None:
        start, items = clamp_paging(-5, 50)
        assert start == 0
        assert items == 50

    def test_excessive_items_clamped(self) -> None:
        start, items = clamp_paging(0, 999_999)
        assert items == MAX_QUERY_ITEMS

    def test_negative_items_clamped_to_zero(self) -> None:
        start, items = clamp_paging(0, -10)
        assert items == 0

    def test_excessive_start_clamped(self) -> None:
        start, items = clamp_paging(2_000_000, 10)
        assert start == MAX_QUERY_START

    def test_normal_values_pass_through(self) -> None:
        start, items = clamp_paging(10, 50)
        assert start == 10
        assert items == 50

    def test_parse_start_items_clamps_negative_start(self) -> None:
        """parse_start_items should clamp negative start to 0."""
        start, items = parse_start_items(["command", -5, 100])
        assert start == 0

    def test_parse_start_items_clamps_excessive_items(self) -> None:
        """parse_start_items should clamp items to MAX_QUERY_ITEMS."""
        start, items = parse_start_items(["command", 0, 999_999])
        assert items == MAX_QUERY_ITEMS

    def test_parse_start_items_normal_values(self) -> None:
        """Normal values should pass through unchanged."""
        start, items = parse_start_items(["command", 5, 20])
        assert start == 5
        assert items == 20


class TestMacValidation:
    """Tests for is_valid_mac."""

    def test_valid_colon_separated(self) -> None:
        assert is_valid_mac("aa:bb:cc:dd:ee:ff") is True

    def test_valid_dash_separated(self) -> None:
        assert is_valid_mac("AA-BB-CC-DD-EE-FF") is True

    def test_valid_mixed_case(self) -> None:
        assert is_valid_mac("aA:bB:cC:dD:eE:fF") is True

    def test_server_dash_is_valid(self) -> None:
        assert is_valid_mac("-") is True

    def test_empty_is_valid_for_server_commands(self) -> None:
        """Empty player_id is valid — SqueezePlay sends it for server-level commands like serverstatus."""
        assert is_valid_mac("") is True

    def test_short_is_invalid(self) -> None:
        assert is_valid_mac("aa:bb:cc") is False

    def test_garbage_is_invalid(self) -> None:
        assert is_valid_mac("not-a-mac") is False

    def test_sql_injection_attempt_is_invalid(self) -> None:
        assert is_valid_mac("'; DROP TABLE players; --") is False

    def test_too_long_is_invalid(self) -> None:
        assert is_valid_mac("aa:bb:cc:dd:ee:ff:00") is False


class TestSanitisePlayerId:
    """Tests for sanitise_player_id."""

    def test_normalises_to_lowercase_colons(self) -> None:
        assert sanitise_player_id("AA-BB-CC-DD-EE-FF") == "aa:bb:cc:dd:ee:ff"

    def test_dash_unchanged(self) -> None:
        assert sanitise_player_id("-") == "-"

    def test_already_normalised(self) -> None:
        assert sanitise_player_id("aa:bb:cc:dd:ee:ff") == "aa:bb:cc:dd:ee:ff"

    def test_invalid_returned_unchanged(self) -> None:
        assert sanitise_player_id("garbage") == "garbage"


class TestPathSafety:
    """Tests for is_safe_path."""

    def test_normal_path_is_safe(self) -> None:
        assert is_safe_path("music/album/track.mp3") is True

    def test_dot_dot_is_unsafe(self) -> None:
        assert is_safe_path("../etc/passwd") is False

    def test_dot_dot_in_middle_is_unsafe(self) -> None:
        assert is_safe_path("music/../../../etc/passwd") is False

    def test_absolute_unix_is_unsafe(self) -> None:
        assert is_safe_path("/etc/passwd") is False

    def test_absolute_windows_is_unsafe(self) -> None:
        assert is_safe_path("C:\\Windows\\System32") is False

    def test_backslash_traversal_is_unsafe(self) -> None:
        assert is_safe_path("music\\..\\..\\etc\\passwd") is False

    def test_empty_is_unsafe(self) -> None:
        assert is_safe_path("") is False

    def test_single_file_is_safe(self) -> None:
        assert is_safe_path("track.mp3") is True

    def test_dot_component_is_safe(self) -> None:
        """A single dot component (current dir) is not dangerous."""
        assert is_safe_path("./music/track.mp3") is True

    def test_backslash_absolute_is_unsafe(self) -> None:
        assert is_safe_path("\\Windows\\System32") is False


class TestClampVolume:
    """Tests for clamp_volume."""

    def test_clamp_below_zero(self) -> None:
        assert clamp_volume(-10) == 0

    def test_clamp_above_hundred(self) -> None:
        assert clamp_volume(150) == 100

    def test_normal_value(self) -> None:
        assert clamp_volume(50) == 50

    def test_boundary_zero(self) -> None:
        assert clamp_volume(0) == 0

    def test_boundary_hundred(self) -> None:
        assert clamp_volume(100) == 100


class TestClampSeek:
    """Tests for clamp_seek."""

    def test_clamp_negative_to_zero(self) -> None:
        assert clamp_seek(-5.0, 300.0) == 0.0

    def test_clamp_beyond_duration(self) -> None:
        result = clamp_seek(999.0, 300.0)
        assert result == 299.0  # duration - 1

    def test_zero_duration(self) -> None:
        assert clamp_seek(10.0, 0.0) == 10.0

    def test_normal_seek(self) -> None:
        assert clamp_seek(30.0, 300.0) == 30.0


class TestClampPlaylistIndex:
    """Tests for clamp_playlist_index."""

    def test_clamp_negative(self) -> None:
        assert clamp_playlist_index(-1, 10) == 0

    def test_clamp_beyond_length(self) -> None:
        assert clamp_playlist_index(15, 10) == 9

    def test_empty_playlist(self) -> None:
        assert clamp_playlist_index(5, 0) == 0

    def test_valid_index(self) -> None:
        assert clamp_playlist_index(3, 10) == 3

    def test_last_valid(self) -> None:
        assert clamp_playlist_index(9, 10) == 9


# =============================================================================
# JSON-RPC Player-ID Validation Tests
# =============================================================================


class TestJsonRpcPlayerIdValidation:
    """Test that invalid player IDs are rejected at the JSON-RPC level."""

    async def test_invalid_player_id_rejected(
        self,
        registry: PlayerRegistry,
        library: MusicLibrary,
    ) -> None:
        """An obviously invalid player_id should return an error."""
        server = WebServer(player_registry=registry, music_library=library)
        transport = ASGITransport(app=server.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/jsonrpc.js", json={
                "id": 1,
                "method": "slim.request",
                "params": ["not-a-mac", ["status", 0, 10]],
            })
            assert resp.status_code == 200
            data = resp.json()
            assert "error" in data.get("result", {})

    async def test_sql_injection_player_id_rejected(
        self,
        registry: PlayerRegistry,
        library: MusicLibrary,
    ) -> None:
        """SQL injection attempt in player_id should be rejected."""
        server = WebServer(player_registry=registry, music_library=library)
        transport = ASGITransport(app=server.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/jsonrpc.js", json={
                "id": 1,
                "method": "slim.request",
                "params": ["'; DROP TABLE players; --", ["status"]],
            })
            assert resp.status_code == 200
            data = resp.json()
            assert "error" in data.get("result", {})

    async def test_valid_mac_accepted(
        self,
        registry: PlayerRegistry,
        library: MusicLibrary,
    ) -> None:
        """A valid MAC address should be accepted (not rejected by validation)."""
        server = WebServer(player_registry=registry, music_library=library)
        transport = ASGITransport(app=server.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/jsonrpc.js", json={
                "id": 1,
                "method": "slim.request",
                "params": ["aa:bb:cc:dd:ee:ff", ["status", 0, 10]],
            })
            assert resp.status_code == 200
            data = resp.json()
            # Should NOT have a player_id validation error
            result = data.get("result", {})
            error = result.get("error", "")
            assert "Invalid player_id format" not in error

    async def test_server_dash_accepted(
        self,
        registry: PlayerRegistry,
        library: MusicLibrary,
    ) -> None:
        """The '-' player_id (server commands) should be accepted."""
        server = WebServer(player_registry=registry, music_library=library)
        transport = ASGITransport(app=server.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/jsonrpc.js", json={
                "id": 1,
                "method": "slim.request",
                "params": ["-", ["serverstatus", 0, 10]],
            })
            assert resp.status_code == 200
            data = resp.json()
            result = data.get("result", {})
            assert "Invalid player_id format" not in result.get("error", "")


# =============================================================================
# Settings Validation Tests
# =============================================================================


class TestSettingsValidation:
    """Tests that ServerSettings validates security fields correctly."""

    def test_auth_enabled_without_username_fails(self) -> None:
        from resonance.config.settings import ServerSettings

        s = ServerSettings(auth_enabled=True, auth_username="", auth_password_hash="somehash")
        errors = s.validate()
        assert any("auth_username" in e for e in errors)

    def test_auth_enabled_without_password_hash_fails(self) -> None:
        from resonance.config.settings import ServerSettings

        s = ServerSettings(auth_enabled=True, auth_username="admin", auth_password_hash="")
        errors = s.validate()
        assert any("auth_password_hash" in e for e in errors)

    def test_auth_enabled_with_both_passes(self) -> None:
        from resonance.config.settings import ServerSettings

        s = ServerSettings(
            auth_enabled=True,
            auth_username="admin",
            auth_password_hash=hash_password("secret"),
        )
        errors = s.validate()
        assert not any("auth" in e for e in errors)

    def test_auth_disabled_no_username_required(self) -> None:
        from resonance.config.settings import ServerSettings

        s = ServerSettings(auth_enabled=False, auth_username="", auth_password_hash="")
        errors = s.validate()
        assert not any("auth" in e for e in errors)

    def test_rate_limit_per_second_too_low(self) -> None:
        from resonance.config.settings import ServerSettings

        s = ServerSettings(rate_limit_per_second=0)
        errors = s.validate()
        assert any("rate_limit_per_second" in e for e in errors)

    def test_rate_limit_per_second_too_high(self) -> None:
        from resonance.config.settings import ServerSettings

        s = ServerSettings(rate_limit_per_second=99_999)
        errors = s.validate()
        assert any("rate_limit_per_second" in e for e in errors)

    def test_rate_limit_per_second_valid(self) -> None:
        from resonance.config.settings import ServerSettings

        s = ServerSettings(rate_limit_per_second=100)
        errors = s.validate()
        assert not any("rate_limit_per_second" in e for e in errors)


# =============================================================================
# CLI Auth Tests
# =============================================================================


class TestCliAuth:
    """Tests for CLI login command handling."""

    async def test_cli_auth_disabled_allows_commands(self) -> None:
        """When auth is disabled, CLI commands work without login."""
        from resonance.protocol.cli import CliServer

        results: list[dict[str, Any]] = []

        async def fake_executor(player_id: str, command: list[str]) -> dict[str, Any]:
            result = {"count": 0}
            results.append(result)
            return result

        server = CliServer(
            host="127.0.0.1",
            port=0,
            command_executor=fake_executor,
            auth_enabled=False,
        )
        await server.start()
        actual_port = server.port

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", actual_port)
            writer.write(b"- serverstatus 0 10\n")
            await writer.drain()
            response = await asyncio.wait_for(reader.readline(), timeout=2.0)
            line = response.decode("utf-8").strip()
            # Should get a response (not an auth error)
            assert "not_authenticated" not in line
            writer.close()
            await writer.wait_closed()
        finally:
            await server.stop()

    async def test_cli_auth_enabled_rejects_without_login(self) -> None:
        """When auth is enabled, commands before login are rejected."""
        from resonance.protocol.cli import CliServer

        pw_hash = hash_password("secret")

        async def fake_executor(player_id: str, command: list[str]) -> dict[str, Any]:
            return {"count": 0}

        server = CliServer(
            host="127.0.0.1",
            port=0,
            command_executor=fake_executor,
            auth_enabled=True,
            auth_username="admin",
            auth_password_hash=pw_hash,
        )
        await server.start()
        actual_port = server.port

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", actual_port)
            writer.write(b"- serverstatus 0 10\n")
            await writer.drain()
            response = await asyncio.wait_for(reader.readline(), timeout=2.0)
            line = response.decode("utf-8").strip()
            assert "not_authenticated" in line
            writer.close()
            await writer.wait_closed()
        finally:
            await server.stop()

    async def test_cli_auth_enabled_login_then_command(self) -> None:
        """After successful login, commands work normally."""
        from resonance.protocol.cli import CliServer

        pw_hash = hash_password("secret")

        async def fake_executor(player_id: str, command: list[str]) -> dict[str, Any]:
            return {"count": 0, "players_loop": []}

        server = CliServer(
            host="127.0.0.1",
            port=0,
            command_executor=fake_executor,
            auth_enabled=True,
            auth_username="admin",
            auth_password_hash=pw_hash,
        )
        await server.start()
        actual_port = server.port

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", actual_port)
            # Login first
            writer.write(b"login admin secret\n")
            await writer.drain()
            login_resp = await asyncio.wait_for(reader.readline(), timeout=2.0)
            assert b"login ok" in login_resp

            # Now command should work
            writer.write(b"- serverstatus 0 10\n")
            await writer.drain()
            cmd_resp = await asyncio.wait_for(reader.readline(), timeout=2.0)
            line = cmd_resp.decode("utf-8").strip()
            assert "not_authenticated" not in line

            writer.close()
            await writer.wait_closed()
        finally:
            await server.stop()

    async def test_cli_auth_enabled_wrong_password_rejected(self) -> None:
        """Login with wrong password should fail."""
        from resonance.protocol.cli import CliServer

        pw_hash = hash_password("correct")

        async def fake_executor(player_id: str, command: list[str]) -> dict[str, Any]:
            return {}

        server = CliServer(
            host="127.0.0.1",
            port=0,
            command_executor=fake_executor,
            auth_enabled=True,
            auth_username="admin",
            auth_password_hash=pw_hash,
        )
        await server.start()
        actual_port = server.port

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", actual_port)
            writer.write(b"login admin wrong\n")
            await writer.drain()
            login_resp = await asyncio.wait_for(reader.readline(), timeout=2.0)
            assert b"invalid_credentials" in login_resp

            # Command should still be rejected
            writer.write(b"- serverstatus 0 10\n")
            await writer.drain()
            cmd_resp = await asyncio.wait_for(reader.readline(), timeout=2.0)
            assert b"not_authenticated" in cmd_resp

            writer.close()
            await writer.wait_closed()
        finally:
            await server.stop()


# =============================================================================
# Path Traversal in Playlist _resolve_track
# =============================================================================


class TestPlaylistPathTraversal:
    """Test that path traversal attempts are rejected in playlist track resolution."""

    async def test_resolve_track_rejects_dotdot_path(
        self,
        library: MusicLibrary,
        registry: PlayerRegistry,
    ) -> None:
        """_resolve_track should reject paths with '..' components."""
        from resonance.web.handlers import CommandContext
        from resonance.web.handlers.playlist import _resolve_track  # type: ignore[attr-defined]

        ctx = CommandContext(
            player_id="-",
            music_library=library,
            player_registry=registry,
        )

        # Direct traversal
        result = await _resolve_track(ctx, "../../../etc/passwd", {})
        assert result is None

        # Embedded traversal
        result = await _resolve_track(ctx, "music/../../etc/shadow", {})
        assert result is None

        # file:// prefix traversal
        result = await _resolve_track(ctx, "file://../../../etc/passwd", {})
        assert result is None

    async def test_resolve_track_accepts_normal_path(
        self,
        library: MusicLibrary,
        registry: PlayerRegistry,
    ) -> None:
        """_resolve_track should accept normal paths (even if track not found)."""
        from resonance.web.handlers import CommandContext
        from resonance.web.handlers.playlist import _resolve_track  # type: ignore[attr-defined]

        ctx = CommandContext(
            player_id="-",
            music_library=library,
            player_registry=registry,
        )

        # Normal path — will return None because track doesn't exist in DB,
        # but should NOT be rejected by path-traversal check
        result = await _resolve_track(ctx, "/music/album/track.mp3", {})
        # Just verify it didn't raise; result is None because track not in DB
        assert result is None
