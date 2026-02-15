"""
Security Module for Resonance.

Provides optional security middleware for the FastAPI web server:

1. **HTTP Basic Authentication** — optional, default off.
   Protects all HTTP endpoints except streaming (players need unauthenticated
   access) and health checks.

2. **Rate Limiting** — optional, default off.
   Simple token-bucket per client IP.  Protects against accidental or
   malicious request floods.  Long-poll (Cometd) and streaming endpoints
   are exempt by design.

3. **Password Hashing** — stdlib-only (``hashlib.pbkdf2_hmac``).
   No external dependency (no bcrypt).  Passwords are stored as
   ``pbkdf2:sha256:<iterations>$<hex-salt>$<hex-hash>``.

Usage::

    from resonance.web.security import (
        AuthMiddleware,
        RateLimitMiddleware,
        hash_password,
        verify_password,
    )
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time
from base64 import b64decode
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Password hashing (stdlib only — no bcrypt dependency)
# ---------------------------------------------------------------------------

_PBKDF2_ALGORITHM = "sha256"
_PBKDF2_ITERATIONS = 600_000  # OWASP 2023 recommendation for PBKDF2-SHA256
_SALT_BYTES = 16


def hash_password(password: str) -> str:
    """
    Hash a plaintext password for safe storage.

    Returns a string in the format::

        pbkdf2:sha256:<iterations>$<hex-salt>$<hex-hash>

    This is fully self-contained — salt, algorithm, and iteration count
    are embedded so that :func:`verify_password` can validate without
    external state.
    """
    salt = os.urandom(_SALT_BYTES)
    dk = hashlib.pbkdf2_hmac(
        _PBKDF2_ALGORITHM,
        password.encode("utf-8"),
        salt,
        _PBKDF2_ITERATIONS,
    )
    return (
        f"pbkdf2:{_PBKDF2_ALGORITHM}:{_PBKDF2_ITERATIONS}"
        f"${salt.hex()}${dk.hex()}"
    )


def verify_password(password: str, password_hash: str) -> bool:
    """
    Verify *password* against a previously hashed value.

    Returns ``True`` if the password matches, ``False`` otherwise.
    Accepts the format produced by :func:`hash_password`.

    For convenience, if *password_hash* does **not** start with ``pbkdf2:``
    it is treated as a plaintext comparison (constant-time).  This allows
    a simple ``auth_password`` in the TOML config during development, but
    a warning is logged to encourage switching to a hashed value.
    """
    if not password_hash:
        return False

    # Hashed format: pbkdf2:sha256:<iterations>$<salt-hex>$<hash-hex>
    if password_hash.startswith("pbkdf2:"):
        try:
            header, salt_hex, hash_hex = password_hash.split("$", 2)
            parts = header.split(":")
            # parts = ["pbkdf2", algorithm, iterations]
            if len(parts) != 3:
                return False
            algorithm = parts[1]
            iterations = int(parts[2])
            salt = bytes.fromhex(salt_hex)
            expected = bytes.fromhex(hash_hex)
        except (ValueError, IndexError):
            logger.warning("Malformed password hash — verification failed")
            return False

        dk = hashlib.pbkdf2_hmac(
            algorithm,
            password.encode("utf-8"),
            salt,
            iterations,
        )
        return hmac.compare_digest(dk, expected)

    # Plaintext fallback (development convenience — warn)
    logger.warning(
        "auth_password is stored in plaintext — run "
        "`resonance --hash-password` to generate a secure hash"
    )
    return secrets.compare_digest(password, password_hash)


# ---------------------------------------------------------------------------
# HTTP Basic Authentication Middleware
# ---------------------------------------------------------------------------

# Paths that are NEVER protected by authentication.
# Streaming endpoints must stay open for Squeezebox players.
_AUTH_EXEMPT_PREFIXES: tuple[str, ...] = (
    "/stream",       # /stream.mp3, /stream, /stream/*
    "/health",       # health-check probe
)


def _is_auth_exempt(path: str) -> bool:
    """Return True if *path* should bypass authentication."""
    for prefix in _AUTH_EXEMPT_PREFIXES:
        if path == prefix or path.startswith(prefix + "/") or path.startswith(prefix + "."):
            return True
    return False


def _parse_basic_auth(authorization: str) -> tuple[str, str] | None:
    """
    Parse an ``Authorization: Basic <b64>`` header.

    Returns ``(username, password)`` or ``None`` on failure.
    """
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "basic":
        return None
    try:
        decoded = b64decode(parts[1]).decode("utf-8")
    except Exception:
        return None
    if ":" not in decoded:
        return None
    username, password = decoded.split(":", 1)
    return username, password


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Optional HTTP Basic Auth middleware.

    When *enabled* is ``True``, every non-exempt request must carry a valid
    ``Authorization: Basic …`` header.  Exempt paths (streaming, health)
    are always allowed through.

    Args:
        app: The ASGI application (provided by Starlette).
        enabled: Whether authentication is active.
        username: Expected username.
        password_hash: Hashed password (output of :func:`hash_password`)
            or plaintext for development convenience.
    """

    def __init__(
        self,
        app: Any,
        *,
        enabled: bool = False,
        username: str = "",
        password_hash: str = "",
    ) -> None:
        super().__init__(app)
        self._enabled = enabled
        self._username = username
        self._password_hash = password_hash

        if enabled:
            if not username or not password_hash:
                logger.warning(
                    "Auth enabled but username/password not configured — "
                    "all requests will be rejected (401)"
                )
            else:
                logger.info("HTTP Basic Authentication enabled")

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[..., Any],
    ) -> Response:
        if not self._enabled:
            return await call_next(request)

        path = request.url.path
        if _is_auth_exempt(path):
            return await call_next(request)

        # Check Authorization header
        auth_header = request.headers.get("authorization", "")
        credentials = _parse_basic_auth(auth_header)

        if credentials is None:
            return _unauthorized_response("Authentication required")

        username, password = credentials
        if not secrets.compare_digest(username, self._username):
            return _unauthorized_response("Invalid credentials")

        if not verify_password(password, self._password_hash):
            return _unauthorized_response("Invalid credentials")

        return await call_next(request)


def _unauthorized_response(detail: str = "Unauthorized") -> Response:
    """Build a 401 response with ``WWW-Authenticate`` header."""
    return JSONResponse(
        status_code=401,
        content={"detail": detail},
        headers={"WWW-Authenticate": 'Basic realm="Resonance"'},
    )


# ---------------------------------------------------------------------------
# Rate Limiting Middleware (Token Bucket)
# ---------------------------------------------------------------------------

@dataclass
class _TokenBucket:
    """Per-client token bucket state."""

    tokens: float
    max_tokens: float
    last_refill: float = field(default_factory=time.monotonic)

    def consume(self, rate: float, now: float | None = None) -> bool:
        """
        Try to consume one token.

        *rate* is tokens added per second.  Returns ``True`` if the
        request is allowed, ``False`` if rate-limited.
        """
        if now is None:
            now = time.monotonic()
        # Refill tokens
        elapsed = now - self.last_refill
        self.tokens = min(self.max_tokens, self.tokens + elapsed * rate)
        self.last_refill = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


# Paths exempt from rate limiting (long-poll / streaming)
_RATE_LIMIT_EXEMPT_PREFIXES: tuple[str, ...] = (
    "/cometd",       # long-poll is by design long-lived
    "/stream",       # audio streaming
    "/health",       # monitoring probes
)


def _is_rate_limit_exempt(path: str) -> bool:
    """Return True if *path* should bypass rate limiting."""
    for prefix in _RATE_LIMIT_EXEMPT_PREFIXES:
        if path == prefix or path.startswith(prefix + "/") or path.startswith(prefix + "."):
            return True
    return False


# Maximum number of tracked client IPs to prevent unbounded memory growth
_MAX_TRACKED_IPS = 10_000

# Cleanup interval: remove stale entries after this many seconds
_BUCKET_STALE_SECONDS = 300.0


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Optional per-IP token-bucket rate limiter.

    When *enabled* is ``True``, each client IP is limited to
    *requests_per_second* requests.  A burst of up to
    ``requests_per_second`` requests is allowed (bucket capacity
    equals the per-second rate).

    Cometd long-poll and streaming endpoints are always exempt.

    Returns HTTP 429 when the limit is exceeded.

    Args:
        app: The ASGI application.
        enabled: Whether rate limiting is active.
        requests_per_second: Allowed request rate per client IP.
    """

    def __init__(
        self,
        app: Any,
        *,
        enabled: bool = False,
        requests_per_second: int = 100,
    ) -> None:
        super().__init__(app)
        self._enabled = enabled
        self._rate = float(max(1, requests_per_second))
        self._buckets: dict[str, _TokenBucket] = {}
        self._last_cleanup = time.monotonic()

        if enabled:
            logger.info(
                "Rate limiting enabled: %d requests/s per client",
                requests_per_second,
            )

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[..., Any],
    ) -> Response:
        if not self._enabled:
            return await call_next(request)

        path = request.url.path
        if _is_rate_limit_exempt(path):
            return await call_next(request)

        # Determine client IP
        client = request.client
        client_ip = client.host if client else "unknown"

        now = time.monotonic()

        # Periodic cleanup of stale buckets
        if now - self._last_cleanup > _BUCKET_STALE_SECONDS:
            self._cleanup_stale_buckets(now)

        # Get or create bucket
        bucket = self._buckets.get(client_ip)
        if bucket is None:
            if len(self._buckets) >= _MAX_TRACKED_IPS:
                # Safety valve: don't track more IPs, allow request
                logger.warning(
                    "Rate limiter IP table full (%d entries) — allowing request",
                    _MAX_TRACKED_IPS,
                )
                return await call_next(request)
            bucket = _TokenBucket(tokens=self._rate, max_tokens=self._rate)
            self._buckets[client_ip] = bucket

        if not bucket.consume(self._rate, now):
            logger.debug("Rate limit exceeded for %s on %s", client_ip, path)
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests"},
                headers={
                    "Retry-After": "1",
                },
            )

        return await call_next(request)

    def _cleanup_stale_buckets(self, now: float) -> None:
        """Remove buckets that haven't been used recently."""
        stale_before = now - _BUCKET_STALE_SECONDS
        stale_keys = [
            ip
            for ip, bucket in self._buckets.items()
            if bucket.last_refill < stale_before
        ]
        for key in stale_keys:
            del self._buckets[key]
        self._last_cleanup = now
        if stale_keys:
            logger.debug("Cleaned up %d stale rate-limit buckets", len(stale_keys))


# ---------------------------------------------------------------------------
# Input Validation Helpers
# ---------------------------------------------------------------------------

# Maximum items a single JSON-RPC list query may request
MAX_QUERY_ITEMS = 10_000

# Maximum sane paging start index
MAX_QUERY_START = 1_000_000


def clamp_paging(start: int, items: int) -> tuple[int, int]:
    """
    Sanitise paging parameters from client requests.

    Ensures *start* is non-negative and *items* does not exceed
    :data:`MAX_QUERY_ITEMS`.

    Returns:
        ``(clamped_start, clamped_items)``
    """
    if start < 0:
        start = 0
    elif start > MAX_QUERY_START:
        start = MAX_QUERY_START
    if items < 0:
        items = 0
    elif items > MAX_QUERY_ITEMS:
        items = MAX_QUERY_ITEMS
    return start, items


import re as _re

_MAC_RE = _re.compile(
    r"^(?:[0-9a-fA-F]{2}[:\-]){5}[0-9a-fA-F]{2}$"
)


def is_valid_mac(player_id: str) -> bool:
    """
    Check whether *player_id* looks like a valid MAC address.

    Accepts colon-separated (``aa:bb:cc:dd:ee:ff``) and
    dash-separated (``aa-bb-cc-dd-ee-ff``) formats.

    The special value ``"-"`` (server-level commands) is also accepted.
    """
    if player_id in ("-", ""):
        return True
    return bool(_MAC_RE.match(player_id))


def is_safe_path(path: str) -> bool:
    """
    Check whether *path* is safe from directory-traversal attacks.

    Rejects paths containing ``..``, absolute paths, and
    other suspicious patterns.
    """
    if not path:
        return False

    # Reject absolute paths (Unix and Windows)
    if path.startswith("/") or path.startswith("\\"):
        return False
    if len(path) >= 2 and path[1] == ":":
        # Windows drive letter (e.g. C:\\)
        return False

    # Reject path traversal
    # Check each component for ".."
    for component in path.replace("\\", "/").split("/"):
        if component == "..":
            return False

    return True


def sanitise_player_id(player_id: str) -> str:
    """
    Normalise a player ID (MAC address) to lowercase colon-separated format.

    If the input is not a valid MAC, returns it unchanged (callers should
    validate separately if needed).
    """
    if player_id == "-":
        return player_id
    # Normalise separators
    normalised = player_id.replace("-", ":").lower()
    if _MAC_RE.match(normalised):
        return normalised
    return player_id


def clamp_volume(value: int) -> int:
    """Clamp a volume value to the valid range 0–100."""
    return max(0, min(100, value))


def clamp_seek(target: float, duration: float) -> float:
    """
    Clamp a seek target to the valid range ``[0, duration - 1]``.

    If *duration* is zero or negative, clamps to 0.
    """
    if duration <= 0:
        return max(0.0, target)
    return max(0.0, min(target, max(0.0, duration - 1.0)))


def clamp_playlist_index(index: int, length: int) -> int:
    """
    Clamp a playlist index to the valid range ``[0, length - 1]``.

    If *length* is zero, returns 0.
    """
    if length <= 0:
        return 0
    return max(0, min(index, length - 1))
