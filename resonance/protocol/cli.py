"""
Telnet CLI server (LMS-style command surface) for Resonance.

This server listens on TCP port 9090 and accepts line-based commands, e.g.:

    aa:bb:cc:dd:ee:ff status 0 10
    - players 0 20

For convenience, if no player id prefix is provided, "-" is assumed.

Supports LMS-compatible subscription commands:
    listen 1          - subscribe to all server notifications
    listen 0          - unsubscribe from notifications
    listen ?          - query current listen state
    subscribe <funcs> - subscribe to specific comma-separated command types
                        e.g. "subscribe playlist,mixer,client"

Optional authentication (§14.1):
    When ``auth_enabled`` is True in ServerSettings, CLI clients must
    authenticate via ``login <user> <pass>`` before any other command
    is accepted.  Unauthenticated commands receive an error response.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, unquote

from resonance.core.events import (
    Event,
    EventBus,
    LibraryScanEvent,
    PlayerConnectedEvent,
    PlayerDisconnectedEvent,
    PlayerPlaylistEvent,
    PlayerStatusEvent,
)

logger = logging.getLogger(__name__)

MAC_RE = re.compile(r"^[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}$")
MAX_LINE_BYTES = 65536

CommandExecutor = Callable[[str, list[str]], Awaitable[dict[str, Any]]]


def _is_scalar(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool)) or value is None


def _scalar_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def _encode_token(value: str, *, safe: str = "") -> str:
    return quote(value, safe=safe)


def parse_cli_command_line(line: str) -> tuple[str, list[str]]:
    """
    Parse one CLI command line into (player_id, command_tokens).

    Accepted forms:
    - "aa:bb:cc:dd:ee:ff status 0 10"
    - "- players 0 20"
    - "players 0 20"  (implicit player_id "-")
    """
    tokens = [unquote(t) for t in line.strip().split()]
    if not tokens:
        raise ValueError("Empty command")

    first = tokens[0]
    if first == "-" or MAC_RE.match(first):
        player_id = first
        command = tokens[1:]
    else:
        player_id = "-"
        command = tokens

    if not command:
        raise ValueError("Missing command")

    return player_id, command


@dataclass
class _CliListenerState:
    """Per-connection subscription state for CLI notifications."""

    # None = not listening, "*" = all, list = specific command prefixes
    listen_filter: str | list[str] | None = None

    def is_listening(self) -> bool:
        return self.listen_filter is not None

    def matches(self, command_prefix: str) -> bool:
        """Check if a notification command matches this listener's filter."""
        if self.listen_filter is None:
            return False
        if self.listen_filter == "*":
            return True
        if isinstance(self.listen_filter, list):
            return command_prefix in self.listen_filter
        return False


@dataclass
class _PlayerNotifiedState:
    """Tracks last notified state per player to suppress duplicate notifications."""

    state: str = ""
    volume: int = -1


class CliServer:
    """TCP line-based CLI server compatible with LMS-style command input.

    Supports ``listen 1``, ``listen 0``, ``listen ?``, and
    ``subscribe <functions>`` for LMS-compatible event notifications.

    When authentication is enabled (via ServerSettings), clients must
    issue ``login <user> <pass>`` before any other command is accepted.
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 9090,
        *,
        command_executor: CommandExecutor,
        event_bus: EventBus | None = None,
        auth_enabled: bool = False,
        auth_username: str = "",
        auth_password_hash: str = "",
    ) -> None:
        self.host = host
        self.port = port
        self._command_executor = command_executor
        self._event_bus = event_bus
        self._server: asyncio.AbstractServer | None = None
        self._running = False
        self._connections: set[asyncio.StreamWriter] = set()

        # Authentication (§14.1)
        self._auth_enabled = auth_enabled
        self._auth_username = auth_username
        self._auth_password_hash = auth_password_hash
        self._authenticated: set[asyncio.StreamWriter] = set()

        # Subscription management
        self._listeners: dict[asyncio.StreamWriter, _CliListenerState] = {}
        self._bus_subscribed = False
        self._last_notified: dict[str, _PlayerNotifiedState] = {}

    async def start(self) -> None:
        """Start listening for CLI connections."""
        if self._running:
            logger.warning("CLI server already running")
            return

        self._server = await asyncio.start_server(
            self._handle_connection,
            host=self.host,
            port=self.port,
            reuse_address=True,
        )

        sockets = self._server.sockets or []
        if sockets:
            self.port = int(sockets[0].getsockname()[1])

        self._running = True
        logger.info("CLI server listening on %s:%d", self.host, self.port)

    async def stop(self) -> None:
        """Stop server and close active client connections."""
        if not self._running:
            return

        self._running = False

        # Unsubscribe from event bus
        await self._ensure_bus_unsubscribed()

        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        writers = list(self._connections)
        self._connections.clear()
        self._listeners.clear()
        for writer in writers:
            try:
                writer.close()
            except Exception:
                continue

        if writers:
            await asyncio.gather(*(w.wait_closed() for w in writers), return_exceptions=True)

        logger.info("CLI server stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # Connection handling
    # ------------------------------------------------------------------

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        peer = writer.get_extra_info("peername")
        logger.debug("CLI client connected: %s", peer)
        self._connections.add(writer)

        try:
            while True:
                data = await reader.readline()
                if not data:
                    break

                if len(data) > MAX_LINE_BYTES:
                    await self._write_line(writer, "error:line_too_long")
                    continue

                line = data.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                lowered = line.lower()
                if lowered in {"exit", "quit"}:
                    break

                # ---- Intercept login command (§14.1) ----
                if lowered.startswith("login "):
                    await self._handle_login(writer, line)
                    continue

                # ---- Auth gate: reject commands before login ----
                if self._auth_enabled and writer not in self._authenticated:
                    await self._write_line(
                        writer,
                        "error:not_authenticated login required",
                    )
                    continue

                # ---- Intercept listen / subscribe commands ----
                handled = await self._try_handle_listen_subscribe(writer, line, lowered)
                if handled:
                    continue

                # ---- Normal command dispatch ----
                try:
                    player_id, command = parse_cli_command_line(line)
                except ValueError as exc:
                    await self._write_line(writer, f"error:{_encode_token(str(exc))}")
                    continue

                try:
                    result = await self._command_executor(player_id, command)
                except Exception as exc:
                    logger.exception("CLI command execution failed: %s", exc)
                    result = {"error": str(exc)}

                response_line = self._format_response_line(player_id, command, result)
                await self._write_line(writer, response_line)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("CLI connection handler error: %s", exc)
        finally:
            await self._remove_listener(writer)
            self._authenticated.discard(writer)
            self._connections.discard(writer)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            logger.debug("CLI client disconnected: %s", peer)

    # ------------------------------------------------------------------
    # login command handling (§14.1)
    # ------------------------------------------------------------------

    async def _handle_login(
        self,
        writer: asyncio.StreamWriter,
        line: str,
    ) -> None:
        """Handle ``login <user> <pass>`` for optional CLI authentication.

        When auth is disabled, login always succeeds (compatibility).
        When auth is enabled, credentials are validated against the
        configured username and password hash.
        """
        if not self._auth_enabled:
            # Auth disabled — accept any login for LMS compatibility
            self._authenticated.add(writer)
            await self._write_line(writer, "login ok")
            return

        parts = line.split(None, 2)
        if len(parts) < 3:
            await self._write_line(writer, "login error:missing_credentials")
            return

        username = unquote(parts[1])
        password = unquote(parts[2])

        import secrets as _secrets

        from resonance.web.security import verify_password

        if not _secrets.compare_digest(username, self._auth_username):
            logger.warning("CLI login failed for user %r from %s", username,
                           writer.get_extra_info("peername"))
            await self._write_line(writer, "login error:invalid_credentials")
            return

        if not verify_password(password, self._auth_password_hash):
            logger.warning("CLI login failed (bad password) for user %r from %s",
                           username, writer.get_extra_info("peername"))
            await self._write_line(writer, "login error:invalid_credentials")
            return

        self._authenticated.add(writer)
        logger.debug("CLI login succeeded for user %r from %s", username,
                      writer.get_extra_info("peername"))
        await self._write_line(writer, "login ok")

    # ------------------------------------------------------------------
    # listen / subscribe command handling
    # ------------------------------------------------------------------

    async def _try_handle_listen_subscribe(
        self,
        writer: asyncio.StreamWriter,
        line: str,
        lowered: str,
    ) -> bool:
        """Handle ``listen`` and ``subscribe`` commands locally.

        Returns True if the command was handled, False otherwise.
        """
        # ---- listen ? ----
        if lowered == "listen ?":
            state = self._listeners.get(writer)
            val = "1" if (state and state.is_listening()) else "0"
            await self._write_line(writer, f"listen {val}")
            return True

        # ---- listen 1 ----
        if lowered == "listen 1":
            await self._set_listen(writer, "*")
            await self._write_line(writer, "listen 1")
            return True

        # ---- listen 0 ----
        if lowered == "listen 0":
            await self._remove_listener(writer)
            await self._write_line(writer, "listen 0")
            return True

        # ---- listen (toggle) ----
        if lowered == "listen":
            state = self._listeners.get(writer)
            if state and state.is_listening():
                await self._remove_listener(writer)
                await self._write_line(writer, "listen 0")
            else:
                await self._set_listen(writer, "*")
                await self._write_line(writer, "listen 1")
            return True

        # ---- subscribe (bare, no args) ----
        if lowered == "subscribe":
            await self._remove_listener(writer)
            await self._write_line(writer, "subscribe")
            return True

        # ---- subscribe <functions> ----
        if lowered.startswith("subscribe "):
            parts = line.split(None, 1)
            if len(parts) == 2:
                functions = [f.strip() for f in parts[1].split(",") if f.strip()]
                if functions:
                    await self._set_listen(writer, functions)
                    await self._write_line(
                        writer,
                        f"subscribe {_encode_token(','.join(functions))}",
                    )
                else:
                    await self._remove_listener(writer)
                    await self._write_line(writer, "subscribe")
            else:
                await self._remove_listener(writer)
                await self._write_line(writer, "subscribe")
            return True

        return False

    async def _set_listen(
        self,
        writer: asyncio.StreamWriter,
        filter_value: str | list[str],
    ) -> None:
        """Enable listening for a connection."""
        state = self._listeners.get(writer)
        if state is None:
            state = _CliListenerState()
            self._listeners[writer] = state
        state.listen_filter = filter_value
        logger.debug(
            "CLI listener subscribed: %s filter=%s",
            writer.get_extra_info("peername"),
            filter_value,
        )
        await self._ensure_bus_subscribed()

    async def _remove_listener(self, writer: asyncio.StreamWriter) -> None:
        """Remove subscription for a connection."""
        removed = self._listeners.pop(writer, None)
        if removed is not None:
            logger.debug(
                "CLI listener unsubscribed: %s",
                writer.get_extra_info("peername"),
            )
        # If no more listeners, unsubscribe from event bus
        has_listeners = any(s.is_listening() for s in self._listeners.values())
        if not has_listeners:
            await self._ensure_bus_unsubscribed()

    # ------------------------------------------------------------------
    # EventBus integration
    # ------------------------------------------------------------------

    async def _ensure_bus_subscribed(self) -> None:
        """Subscribe to EventBus if not already subscribed."""
        if self._bus_subscribed or self._event_bus is None:
            return
        await self._event_bus.subscribe("player.*", self._on_event)
        await self._event_bus.subscribe("library.scan", self._on_event)
        self._bus_subscribed = True
        logger.debug("CLI server subscribed to EventBus")

    async def _ensure_bus_unsubscribed(self) -> None:
        """Unsubscribe from EventBus if currently subscribed."""
        if not self._bus_subscribed or self._event_bus is None:
            return
        await self._event_bus.unsubscribe("player.*", self._on_event)
        await self._event_bus.unsubscribe("library.scan", self._on_event)
        self._bus_subscribed = False
        self._last_notified.clear()
        logger.debug("CLI server unsubscribed from EventBus")

    async def _on_event(self, event: Event) -> None:
        """Handle an EventBus event and broadcast to listening CLI connections."""
        notifications = self._event_to_notifications(event)
        if not notifications:
            return

        for notification in notifications:
            await self._broadcast_notification(notification)

    def _event_to_notifications(self, event: Event) -> list[tuple[str, str]]:
        """Convert an EventBus event to (command_prefix, notification_line) tuples.

        Returns a list of (command_prefix, line) pairs. The command_prefix is
        used to match against ``subscribe`` filters (e.g. "playlist", "mixer",
        "client").
        """
        results: list[tuple[str, str]] = []

        if isinstance(event, PlayerConnectedEvent):
            mac = event.player_id
            tokens = [
                _encode_token(mac, safe=":-_.~"),
                "client",
                "new",
            ]
            results.append(("client", " ".join(tokens)))

        elif isinstance(event, PlayerDisconnectedEvent):
            mac = event.player_id
            # Clean up last-notified state for disconnected players
            self._last_notified.pop(mac, None)
            tokens = [
                _encode_token(mac, safe=":-_.~"),
                "client",
                "disconnect",
            ]
            results.append(("client", " ".join(tokens)))

        elif isinstance(event, PlayerPlaylistEvent):
            mac = event.player_id
            action = event.action
            encoded_mac = _encode_token(mac, safe=":-_.~")

            if action == "index":
                # Track change — LMS sends "playlist newsong <title> <index>"
                # We don't have the title readily available, so send index only.
                tokens = [encoded_mac, "playlist", "newsong", str(event.index)]
                results.append(("playlist", " ".join(tokens)))
            elif action == "load":
                tokens = [encoded_mac, "playlist", "load_done"]
                results.append(("playlist", " ".join(tokens)))
            elif action == "add":
                tokens = [encoded_mac, "playlist", "addtracks"]
                results.append(("playlist", " ".join(tokens)))
            elif action == "delete":
                tokens = [encoded_mac, "playlist", "delete"]
                results.append(("playlist", " ".join(tokens)))
            elif action == "clear":
                tokens = [encoded_mac, "playlist", "clear"]
                results.append(("playlist", " ".join(tokens)))
            elif action == "move":
                tokens = [encoded_mac, "playlist", "move"]
                results.append(("playlist", " ".join(tokens)))
            elif action == "newmetadata":
                # ICY title change — LMS fires "playlist newsong <title>"
                # when setCurrentTitle() detects a metadata change (Info.pm L535).
                tokens = [encoded_mac, "playlist", "newsong"]
                results.append(("playlist", " ".join(tokens)))
            else:
                # Generic playlist notification for unknown actions
                tokens = [encoded_mac, "playlist", _encode_token(action)]
                results.append(("playlist", " ".join(tokens)))

        elif isinstance(event, PlayerStatusEvent):
            mac = event.player_id
            encoded_mac = _encode_token(mac, safe=":-_.~")

            # Get or create last-notified state for this player
            last = self._last_notified.get(mac)
            if last is None:
                last = _PlayerNotifiedState()
                self._last_notified[mac] = last

            # Only notify on actual state changes to avoid spamming on
            # throttled STMt elapsed-time updates.
            if event.state and event.state != last.state:
                last.state = event.state
                if event.state == "playing":
                    results.append(("play", f"{encoded_mac} play"))
                elif event.state == "paused":
                    results.append(("pause", f"{encoded_mac} pause"))
                elif event.state == "stopped":
                    results.append(("stop", f"{encoded_mac} stop"))

            # Volume change notification
            if event.volume != last.volume:
                last.volume = event.volume
                results.append(
                    ("mixer", f"{encoded_mac} mixer volume {event.volume}")
                )

        elif isinstance(event, LibraryScanEvent):
            if event.status == "started":
                results.append(("rescan", "rescan"))
            elif event.status == "completed":
                results.append(("rescan", "rescan done"))

        return results

    async def _broadcast_notification(
        self,
        notification: tuple[str, str],
    ) -> None:
        """Send a notification line to all matching listening connections."""
        command_prefix, line = notification

        # Snapshot current listeners to avoid mutation during iteration
        listeners = list(self._listeners.items())

        for writer, state in listeners:
            if writer not in self._connections:
                continue
            if not state.matches(command_prefix):
                continue
            try:
                await self._write_line(writer, line)
            except Exception:
                logger.debug(
                    "Failed to send notification to CLI client: %s",
                    writer.get_extra_info("peername"),
                )

    # ------------------------------------------------------------------
    # Response formatting (unchanged from original)
    # ------------------------------------------------------------------

    async def _write_line(self, writer: asyncio.StreamWriter, line: str) -> None:
        writer.write((line + "\n").encode("utf-8"))
        await writer.drain()

    def _format_response_line(
        self,
        player_id: str,
        command: list[str],
        result: dict[str, Any],
    ) -> str:
        request_tokens = [player_id, *command]
        encoded_request = [_encode_token(t, safe=":-_.~") for t in request_tokens]
        encoded_result = self._format_result_tokens(result)
        return " ".join([*encoded_request, *encoded_result])

    def _format_result_tokens(self, result: dict[str, Any]) -> list[str]:
        if not isinstance(result, dict):
            json_blob = json.dumps(result, separators=(",", ":"), ensure_ascii=True)
            return [f"result:{_encode_token(json_blob)}"]

        error = result.get("error")
        if isinstance(error, str) and error:
            return [f"error:{_encode_token(error)}"]

        scalar_tokens: list[str] = []
        has_complex_values = False

        for key, value in result.items():
            if _is_scalar(value):
                encoded_key = _encode_token(str(key))
                encoded_val = _encode_token(_scalar_to_text(value))
                scalar_tokens.append(f"{encoded_key}:{encoded_val}")
            else:
                has_complex_values = True

        if has_complex_values:
            json_blob = json.dumps(result, separators=(",", ":"), ensure_ascii=True)
            scalar_tokens.append(f"result:{_encode_token(json_blob)}")

        if not scalar_tokens:
            scalar_tokens.append("ok:1")

        return scalar_tokens
