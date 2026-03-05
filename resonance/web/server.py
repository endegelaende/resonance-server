"""
Web Server Module for Resonance.

This module provides the WebServer class that creates and manages the
FastAPI application, registers all routes, and handles HTTP/JSON-RPC requests.

The WebServer integrates:
- JSON-RPC endpoint for LMS-compatible clients
- REST API for web UI
- Streaming endpoint for audio playback
- Cometd endpoint for real-time updates
- Artwork endpoint for album covers
"""

from __future__ import annotations

import asyncio
import logging
import socket
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from resonance.config.settings import get_settings, settings_loaded
from resonance.web.cometd import CometdManager
from resonance.web.jsonrpc import JsonRpcHandler
from resonance.web.routes.api import register_api_routes
from resonance.web.routes.artwork import register_artwork_routes
from resonance.web.routes.cometd import register_cometd_routes
from resonance.web.routes.streaming import register_streaming_routes
from resonance.web.security import AuthMiddleware, RateLimitMiddleware, SecurityHeadersMiddleware

if TYPE_CHECKING:
    from resonance.core.artwork import ArtworkManager
    from resonance.core.library import MusicLibrary
    from resonance.core.playlist import PlaylistManager
    from resonance.player.registry import PlayerRegistry
    from resonance.plugin_installer import PluginInstaller
    from resonance.plugin_manager import PluginManager
    from resonance.plugin_repository import PluginRepository
    from resonance.protocol.slimproto import SlimprotoServer
    from resonance.streaming.server import StreamingServer

logger = logging.getLogger(__name__)


@dataclass
class JsonRpcRequest:
    """JSON-RPC request model."""

    id: int | str | None = None
    method: str = ""
    params: list[Any] | None = None

    def __post_init__(self) -> None:
        if self.params is None:
            self.params = []


@dataclass
class JsonRpcResponse:
    """JSON-RPC response model."""

    id: int | str | None = None
    method: str = ""
    params: list[Any] | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.params is None:
            self.params = []


class WebServer:
    """
    FastAPI-based web server for Resonance.

    Provides HTTP, JSON-RPC, and streaming endpoints for:
    - LMS-compatible apps (iPeng, Squeezer, Material Skin)
    - Web UI
    - Squeezebox players (audio streaming)
    """

    def __init__(
        self,
        player_registry: PlayerRegistry,
        music_library: MusicLibrary,
        playlist_manager: PlaylistManager | None = None,
        streaming_server: StreamingServer | None = None,
        artwork_manager: ArtworkManager | None = None,
        slimproto: SlimprotoServer | None = None,
        plugin_manager: PluginManager | None = None,
        plugin_installer: PluginInstaller | None = None,
        plugin_repository: PluginRepository | None = None,
        server_uuid: str = "resonance",
        cors_origins: str | list[str] = "*",
    ) -> None:
        """
        Initialize the WebServer.

        Args:
            player_registry: Registry of connected players
            music_library: Music library for browsing/search
            playlist_manager: Optional playlist manager
            streaming_server: Optional streaming server for audio
            artwork_manager: Optional artwork extraction/caching
            slimproto: Optional Slimproto server for player control
            server_uuid: Server UUID for identification (full UUID v4, 36 chars with dashes)
            cors_origins: Allowed CORS origins. ``"*"`` permits all origins (default).
                A comma-separated string or list of origin URLs restricts access.
        """
        self.player_registry = player_registry
        self.music_library = music_library
        self.playlist_manager = playlist_manager
        self.streaming_server = streaming_server
        self.artwork_manager = artwork_manager
        self.slimproto = slimproto
        self.plugin_manager = plugin_manager
        self.plugin_installer = plugin_installer
        self.plugin_repository = plugin_repository

        # Create FastAPI app
        self.app = FastAPI(
            title="Resonance",
            description="Modern Python Music Server (LMS-compatible)",
            version="0.1.0",
        )

        # Add CORS middleware
        if isinstance(cors_origins, str):
            if cors_origins.strip() == "*":
                origins: list[str] = ["*"]
            else:
                origins = [o.strip() for o in cors_origins.split(",") if o.strip()]
        else:
            origins = list(cors_origins)

        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # Add security middleware (reads from ServerSettings if available).
        # Middleware execution order is LIFO — last added runs first.
        # We add security-headers first, then rate-limit, then auth, so:
        #   1. Auth checks run first (outermost)
        #   2. Rate-limit accounting next (rejected auth requests don't count)
        #   3. Security headers added last (innermost, runs on every response)
        self.app.add_middleware(SecurityHeadersMiddleware, enabled=True)

        if settings_loaded():
            _settings = get_settings()
            self.app.add_middleware(
                RateLimitMiddleware,
                enabled=_settings.rate_limit_enabled,
                requests_per_second=_settings.rate_limit_per_second,
            )
            self.app.add_middleware(
                AuthMiddleware,
                enabled=_settings.auth_enabled,
                username=_settings.auth_username,
                password_hash=_settings.auth_password_hash,
            )

        # Create Cometd manager
        self.cometd_manager = CometdManager()

        # Create JSON-RPC handler
        self.jsonrpc_handler = JsonRpcHandler(
            music_library=music_library,
            player_registry=player_registry,
            playlist_manager=playlist_manager,
            streaming_server=streaming_server,
            slimproto=slimproto,
            artwork_manager=artwork_manager,
            plugin_manager=plugin_manager,
            plugin_installer=plugin_installer,
            plugin_repository=plugin_repository,
            server_uuid=server_uuid,
        )

        # Server state
        self._server: uvicorn.Server | None = None
        self._serve_task: asyncio.Task[None] | None = None
        self._host = "0.0.0.0"
        self._port = 9000

        # Register routes
        self._register_routes()



    def _register_routes(self) -> None:
        """Register all routes with the FastAPI app."""

        # ── Diagnostic middleware: log artwork-related requests ───────
        # JiveLite fetches artwork via artworkPool (persistent HTTP).
        # This middleware logs any request to /imageproxy/ or /music/
        # so we can verify whether JiveLite actually reaches the server.
        @self.app.middleware("http")
        async def _log_artwork_requests(request, call_next):
            path = request.url.path
            if path.startswith("/imageproxy/") or (
                path.startswith("/music/") and "cover" in path
            ) or path.startswith("/html/images/"):
                logger.info(
                    "[ARTWORK-REQ] %s %s from %s (user-agent: %s)",
                    request.method,
                    path[:200],
                    request.client.host if request.client else "?",
                    request.headers.get("user-agent", "?")[:80],
                )
            resp = await call_next(request)
            if path.startswith("/imageproxy/") or (
                path.startswith("/music/") and "cover" in path
            ) or path.startswith("/html/images/"):
                logger.info(
                    "[ARTWORK-REQ] %s %s -> %d (%s bytes)",
                    request.method,
                    path[:120],
                    resp.status_code,
                    resp.headers.get("content-length", "?"),
                )
            return resp

        # Health check
        @self.app.get("/health")
        async def health_check() -> dict[str, str]:
            """Health check endpoint."""
            return {"status": "ok", "server": "resonance"}

        # JSON-RPC endpoints
        @self.app.post("/jsonrpc.js", tags=["jsonrpc"])
        async def jsonrpc_endpoint(request: dict[str, Any]) -> dict[str, Any]:
            """Main JSON-RPC endpoint.

            This is the primary API endpoint used by LMS-compatible apps.
            """
            return await self.jsonrpc_handler.handle_request(request)

        @self.app.post("/jsonrpc", tags=["jsonrpc"])
        async def jsonrpc_alt_endpoint(request: dict[str, Any]) -> dict[str, Any]:
            """Alternative JSON-RPC endpoint (without .js extension)."""
            return await self.jsonrpc_handler.handle_request(request)

        # Register API routes
        register_api_routes(
            self.app,
            music_library=self.music_library,
            player_registry=self.player_registry,
            playlist_manager=self.playlist_manager,
            streaming_server=self.streaming_server,
            plugin_manager=self.plugin_manager,
            plugin_installer=self.plugin_installer,
            plugin_repository=self.plugin_repository,
        )

        # Register streaming routes
        if self.streaming_server is not None:
            register_streaming_routes(self.app, self.streaming_server, player_registry=self.player_registry)

        # Register artwork routes
        if self.artwork_manager is not None:
            register_artwork_routes(
                self.app,
                artwork_manager=self.artwork_manager,
                music_library=self.music_library,
            )

        # Register Cometd routes
        register_cometd_routes(
            self.app,
            cometd_manager=self.cometd_manager,
            jsonrpc_handler=self.jsonrpc_handler,
        )

        # Serve LMS-compatible static assets (/html/images/radio.png etc.)
        # Must come before the catch-all Svelte mount.
        _static_html = Path(__file__).resolve().parent.parent.parent / "static" / "html"
        if _static_html.is_dir():
            self.app.mount("/html", StaticFiles(directory=str(_static_html)), name="lms_html")
            logger.info("LMS-compatible /html/ static assets mounted from %s", _static_html)

        # Serve Svelte static build at / (must be last — catch-all)
        _ui_build = Path(__file__).resolve().parent.parent.parent / "web-ui" / "build"
        if _ui_build.is_dir() and any(_ui_build.iterdir()):
            self.app.mount("/", StaticFiles(directory=str(_ui_build), html=True), name="webui")
            logger.info("Svelte UI mounted from %s", _ui_build)
        else:
            logger.info("Svelte UI build not found at %s — skipping static mount", _ui_build)

    async def start(self, host: str = "0.0.0.0", port: int = 9000) -> None:
        """
        Start the web server.

        Args:
            host: Host address to bind to
            port: Port to listen on
        """
        self._host = host
        self._port = port

        # Update JSON-RPC handler with server info
        # If binding to all interfaces (0.0.0.0), detect the actual LAN IP
        # so Squeezebox devices can construct valid URLs for artwork/streaming
        if host == "0.0.0.0":
            self.jsonrpc_handler.server_host = self._detect_lan_ip()
        else:
            self.jsonrpc_handler.server_host = host
        self.jsonrpc_handler.server_port = port

        # Start Cometd manager
        await self.cometd_manager.start()

        # Configure uvicorn
        config = uvicorn.Config(
            self.app,
            host=host,
            port=port,
            log_level="warning",
            access_log=False,
        )
        self._server = uvicorn.Server(config)

        # Start server in background and wait until it is actually listening.
        # Without this, bind/startup failures can be missed and discovery still
        # advertises a web port that is not reachable.
        self._serve_task = asyncio.create_task(self._server.serve())
        try:
            await self._wait_for_server_start(timeout_s=5.0)
        except Exception:
            await self._stop_uvicorn_task(force_cancel=True)
            await self.cometd_manager.stop()
            raise

        logger.info("Web server started on http://%s:%d", host, port)



    async def stop(self) -> None:
        """Stop the web server."""
        # Stop Cometd manager
        await self.cometd_manager.stop()

        # Stop uvicorn server
        await self._stop_uvicorn_task()

        logger.info("Web server stopped")

    async def _wait_for_server_start(self, timeout_s: float) -> None:
        """Wait until the web API is actually reachable or startup fails."""
        if self._server is None or self._serve_task is None:
            raise RuntimeError("Web server start requested without uvicorn task")

        loop = asyncio.get_running_loop()
        start_time = loop.time()
        deadline = start_time + timeout_s
        attempt = 0
        last_log_time = start_time

        while True:
            if self._serve_task.done():
                exc = self._serve_task.exception()
                if exc is not None:
                    raise RuntimeError(
                        f"Web server failed to start on {self._host}:{self._port}"
                    ) from exc
                raise RuntimeError(
                    f"Web server exited during startup on {self._host}:{self._port}"
                )

            attempt += 1
            if await self._probe_healthcheck():
                elapsed = loop.time() - start_time
                logger.info(
                    "Web server reachable after %.1fs (%d probes)",
                    elapsed,
                    attempt,
                )
                return

            now = loop.time()
            if now - last_log_time >= 3.0:
                logger.info(
                    "Still waiting for web server startup… (%.0fs / %.0fs)",
                    now - start_time,
                    timeout_s,
                )
                last_log_time = now

            if now >= deadline:
                raise TimeoutError(
                    f"Timed out waiting for reachable web API on {self._host}:{self._port}"
                )

            await asyncio.sleep(0.25)

    async def _probe_healthcheck(self) -> bool:
        """Return True when this process serves /health on the configured host/port."""
        probe_host = self._host
        if probe_host in {"0.0.0.0", "::"}:
            probe_host = "127.0.0.1"

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(probe_host, self._port),
                timeout=0.5,
            )
        except Exception as exc:
            logger.debug("Health probe: connect failed: %s", exc)
            return False

        try:
            request = (
                f"GET /health HTTP/1.0\r\n"
                f"Host: {probe_host}\r\n"
                f"Connection: close\r\n\r\n"
            ).encode("ascii")
            writer.write(request)
            await writer.drain()

            # Read until EOF (connection close).  A single read() may only
            # return HTTP headers on Windows where headers and body arrive
            # in separate TCP segments.
            chunks: list[bytes] = []
            try:
                async with asyncio.timeout(1.0):
                    while True:
                        chunk = await reader.read(4096)
                        if not chunk:
                            break
                        chunks.append(chunk)
                        if sum(len(c) for c in chunks) > 8192:
                            break
            except TimeoutError:
                pass
            response = b"".join(chunks)
        except Exception as exc:
            logger.debug("Health probe: request/read failed: %s", exc)
            return False
        finally:
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()

        ok = (
            b"200 OK" in response
            and b'"status":"ok"' in response
            and b'"server":"resonance"' in response
        )
        if not ok:
            # Log first 300 bytes to diagnose unexpected responses
            logger.debug(
                "Health probe: unexpected response (%d bytes): %s",
                len(response),
                response[:300],
            )
        return ok

    async def _stop_uvicorn_task(self, *, force_cancel: bool = False) -> None:
        """Stop and await the uvicorn serve task if one is running."""
        if self._server is not None:
            self._server.should_exit = True

        task = self._serve_task
        if task is None:
            self._server = None
            return

        if force_cancel and not task.done():
            task.cancel()

        try:
            await asyncio.wait_for(task, timeout=2.0)
        except asyncio.TimeoutError:
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=1.0)
            except asyncio.TimeoutError:
                logger.warning("Timed out cancelling uvicorn serve task")
            except asyncio.CancelledError:
                pass
        except asyncio.CancelledError:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=1.0)
        except Exception as exc:
            logger.warning("Uvicorn task ended with error during shutdown: %s", exc)
        finally:
            self._serve_task = None
            self._server = None

    @property
    def port(self) -> int:
        """Get the server port."""
        return self._port

    @property
    def host(self) -> str:
        """Get the server host."""
        return self._host

    @staticmethod
    def _detect_lan_ip() -> str:
        """
        Detect the primary LAN IP address of this machine.

        Uses the UDP socket trick: connect to a public DNS server
        (no packet is actually sent) to determine which local
        interface would be used for outbound traffic.
        """
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                return s.getsockname()[0]
        except Exception:
            return "127.0.0.1"
