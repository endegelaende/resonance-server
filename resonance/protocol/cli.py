"""
Telnet CLI server (LMS-style command surface) for Resonance.

This server listens on TCP port 9090 and accepts line-based commands, e.g.:

    aa:bb:cc:dd:ee:ff status 0 10
    - players 0 20

For convenience, if no player id prefix is provided, "-" is assumed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote, unquote

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


class CliServer:
    """TCP line-based CLI server compatible with LMS-style command input."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 9090,
        *,
        command_executor: CommandExecutor,
    ) -> None:
        self.host = host
        self.port = port
        self._command_executor = command_executor
        self._server: asyncio.AbstractServer | None = None
        self._running = False
        self._connections: set[asyncio.StreamWriter] = set()

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

        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        writers = list(self._connections)
        self._connections.clear()
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
            self._connections.discard(writer)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            logger.debug("CLI client disconnected: %s", peer)

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

