"""
Streaming Routes for Resonance.

Provides the /stream.mp3 endpoint for audio streaming to Squeezebox players.

Decision logic for transcoding vs. direct streaming is centralized in
resonance.streaming.policy to ensure consistency between the HTTP route
and the player's format expectations (strm command).

Remote URL proxy:
    When a content-provider plugin queues a remote URL (via
    ``StreamingServer.queue_url``), the streaming route fetches the URL
    on behalf of the player and relays audio chunks.  This is necessary
    because Squeezebox hardware cannot handle HTTPS and has limited HTTP
    capabilities.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from resonance.core.events import LiveStreamDroppedEvent, event_bus
from resonance.streaming.crossfade import (
    PreparedCrossfadePlan,
    build_crossfade_command,
    media_type_for_output_format,
)
from resonance.streaming.policy import needs_transcoding
from resonance.streaming.server import RemoteStreamInfo
from resonance.streaming.transcoder import get_transcode_config, transcode_stream

if TYPE_CHECKING:
    from resonance.streaming.server import StreamingServer

logger = logging.getLogger(__name__)

router = APIRouter(tags=["streaming"])

# If a transcoded stream ends extremely quickly, that can indicate a broken pipeline/teardown
# race (premature EOF). However, short segments (e.g. seeking near end-of-track) will also
# legitimately end quickly. We therefore only warn for "meaningfully long" requested segments
# (see logic below), and we increase the byte threshold to reduce false positives.
#
# Note: When start/end are unknown (startup stream), we can't infer the requested segment
# length, so we avoid warning in that case.
_SUSPICIOUS_TRANSCODE_EOF_BYTES = 2 * 1024 * 1024  # 2MB
_SUSPICIOUS_TRANSCODE_EOF_SECONDS = 1.0  # 1s

# Timeout / buffer settings for remote URL proxy streaming.
_REMOTE_CONNECT_TIMEOUT = 10.0  # seconds
_REMOTE_READ_TIMEOUT = 30.0  # seconds
_REMOTE_CHUNK_SIZE = 65536  # 64 KB — matches STREAM_BUFFER_SIZE

# Shared httpx.AsyncClient for remote proxy streaming (created lazily).
_httpx_client: httpx.AsyncClient | None = None

# References set during route registration
_streaming_server: StreamingServer | None = None
_player_registry: Any = None


def register_streaming_routes(
    app,
    streaming_server: StreamingServer | None = None,
    player_registry: Any = None,
) -> None:
    """
    Register streaming routes with the FastAPI app.

    Args:
        app: FastAPI application instance
        streaming_server: StreamingServer for file resolution (optional, falls back to app.state)
        player_registry: PlayerRegistry for device-type lookup during transcoding decisions
    """
    global _streaming_server, _player_registry
    # Use provided streaming_server or fall back to app.state
    if streaming_server is not None:
        _streaming_server = streaming_server
    elif hasattr(app, "state") and hasattr(app.state, "streaming_server"):
        _streaming_server = app.state.streaming_server
    _player_registry = player_registry
    app.include_router(router)


@router.get("/stream.mp3")
async def stream_audio(
    request: Request,
    player: str | None = None,
) -> StreamingResponse:
    """
    Stream audio to a Squeezebox player.

    The player MAC address is passed as a query parameter.
    The streaming server resolves which file to serve based on
    the player's current playlist.

    Decision logic (shared policy):
    - Uses `resonance.streaming.policy.needs_transcoding()` as the single source of truth.
    - Remote URLs are proxy-streamed via ``_stream_remote_proxy()``.

    Args:
        request: The FastAPI request.
        player: Player MAC address (query parameter).
        range: Optional Range header for seeking.

    Returns:
        StreamingResponse with audio data.

    Raises:
        HTTPException: 404 if no file is available for the player.
    """
    if _streaming_server is None:
        raise HTTPException(status_code=503, detail="Streaming server not initialized")

    if player is None:
        raise HTTPException(status_code=400, detail="Missing player parameter")

    # ── Diagnostic: log every incoming stream request ──
    _diag_gen = _streaming_server.get_stream_generation(player) if _streaming_server else None
    logger.info(
        "[DIAG-STREAM] >>> stream_audio called player=%s gen=%s user-agent=%s",
        player,
        _diag_gen,
        request.headers.get("user-agent", "?")[:80],
    )

    # Unified resolution — returns either a local Path or a RemoteStreamInfo.
    resolved = _streaming_server.resolve_stream(player)

    # ---- Remote URL proxy path ----
    if resolved.remote is not None:
        logger.info(
            "[DIAG-STREAM] player=%s gen=%s -> REMOTE PROXY: %s",
            player,
            _diag_gen,
            resolved.remote.title or resolved.remote.url,
        )
        return await _stream_remote_proxy(request, player, resolved.remote)

    # ---- Local file path (existing logic) ----
    file_path = resolved.file_path
    if file_path is None:
        logger.warning("[DIAG-STREAM] player=%s gen=%s -> NO FILE QUEUED (404)", player, _diag_gen)
        raise HTTPException(status_code=404, detail="No track queued for player")

    if not file_path.exists():
        logger.warning(
            "[DIAG-STREAM] player=%s gen=%s -> FILE NOT FOUND: %s", player, _diag_gen, file_path
        )
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")

    file_size = file_path.stat().st_size
    suffix = file_path.suffix.lower()

    # Server-side crossfade plan (if pending for this generation).
    crossfade_plan = _streaming_server.pop_crossfade_plan(player, file_path=file_path)
    if crossfade_plan is not None:
        logger.info(
            "[DIAG-STREAM] player=%s gen=%s -> CROSSFADE path: prev=%s next=%s overlap=%.2fs output=%s",
            player,
            _diag_gen,
            crossfade_plan.previous_path.name,
            crossfade_plan.next_path.name,
            crossfade_plan.overlap_seconds,
            crossfade_plan.output_format_hint,
        )
        return await _stream_with_crossfade(request, player, crossfade_plan)

    # Get Range header from request
    range_header = request.headers.get("range")

    # Resolve device type for transcoding decision.
    device_type = None
    if _player_registry is not None:
        player_client = await _player_registry.get_by_mac(player)
        if player_client is not None and getattr(player_client, "info", None) is not None:
            device_type = player_client.info.device_type

    # Check for seek position (diagnostic)
    _diag_seek = _streaming_server.get_seek_position(player) if _streaming_server else None

    # Check if we need to transcode
    if needs_transcoding(suffix, device_type=device_type):
        logger.info(
            "[DIAG-STREAM] player=%s gen=%s -> TRANSCODE path: file=%s suffix=%s device=%s seek=%s",
            player,
            _diag_gen,
            file_path.name,
            suffix,
            device_type,
            _diag_seek,
        )
        return await _stream_with_transcoding(request, player, file_path)
    else:
        logger.info(
            "[DIAG-STREAM] player=%s gen=%s -> DIRECT path: file=%s suffix=%s size=%d range=%s device=%s",
            player,
            _diag_gen,
            file_path.name,
            suffix,
            file_size,
            range_header,
            device_type,
        )
        return await _stream_direct(request, player, file_path, file_size, range_header)


async def _stream_with_crossfade(
    request: Request,
    player_mac: str,
    plan: PreparedCrossfadePlan,
) -> StreamingResponse:
    """
    Stream a server-side mixed crossfade transition.

    Output starts at the overlap window and continues with the next track.
    """
    if _streaming_server is None:
        raise HTTPException(status_code=503, detail="Streaming server not initialized")

    cancel_token = _streaming_server.get_cancellation_token(player_mac)
    token_generation = getattr(cancel_token, "generation", None)

    try:
        command = build_crossfade_command(plan)
    except Exception as e:
        logger.exception("Failed to build crossfade command for %s", player_mac)
        raise HTTPException(status_code=500, detail=f"Crossfade setup failed: {e}") from e

    async def _terminate_process(proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is not None:
            return
        with contextlib.suppress(Exception):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=0.3)
            return
        except Exception:
            pass
        with contextlib.suppress(Exception):
            proc.kill()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), timeout=0.3)

    async def generate() -> AsyncIterator[bytes]:
        started_at = time.time()
        bytes_sent = 0
        chunk_count = 0
        abort_reason: str | None = None

        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stderr_preview = ""
        try:
            if process.stdout is None:
                raise RuntimeError("crossfade process has no stdout")

            while True:
                if chunk_count % 4 == 0 and await request.is_disconnected():
                    abort_reason = "disconnected"
                    await _terminate_process(process)
                    return

                if chunk_count % 4 == 0 and cancel_token and cancel_token.cancelled:
                    abort_reason = "cancelled"
                    await _terminate_process(process)
                    return

                chunk = await process.stdout.read(65536)
                if not chunk:
                    break

                yield chunk
                bytes_sent += len(chunk)
                chunk_count += 1

            return_code = await process.wait()
            if process.stderr is not None:
                with contextlib.suppress(Exception):
                    stderr_data = await process.stderr.read()
                    stderr_preview = stderr_data.decode(errors="ignore")[:500]

            if return_code != 0:
                abort_reason = abort_reason or "error"
                logger.warning(
                    "Crossfade process exited non-zero player=%s gen=%s rc=%s stderr=%s",
                    player_mac,
                    token_generation,
                    return_code,
                    stderr_preview,
                )

        except asyncio.CancelledError:
            abort_reason = abort_reason or "cancelled_error"
            raise
        except Exception as e:
            abort_reason = abort_reason or "error"
            logger.exception("Crossfade streaming error for player %s: %s", player_mac, e)
        finally:
            await _terminate_process(process)
            elapsed = time.time() - started_at
            logger.info(
                "Crossfade stream finished player=%s gen=%s reason=%s chunks=%d bytes=%d elapsed=%.3fs overlap=%.2fs",
                player_mac,
                token_generation,
                abort_reason or "eof",
                chunk_count,
                bytes_sent,
                elapsed,
                plan.overlap_seconds,
            )

    return StreamingResponse(
        generate(),
        media_type=media_type_for_output_format(plan.output_format_hint),
        headers={
            "Accept-Ranges": "none",
            "X-Content-Type-Options": "nosniff",
        },
    )


async def _stream_with_transcoding(
    request: Request,
    player_mac: str,
    file_path: Path,
) -> StreamingResponse:
    """
    Stream a file with transcoding (for M4B/M4A/MP4 etc.).

    Uses the transcoder module to convert to a streamable format.

    NOTE:
    We serialize transcoded streams per player to avoid overlapping transcode pipelines
    during rapid seeks (Windows asyncio subprocess teardown races).
    """
    if _streaming_server is None:
        raise HTTPException(status_code=503, detail="Streaming server not initialized")

    # Get file extension and find transcoding rule
    suffix = file_path.suffix.lower().lstrip(".")
    config = get_transcode_config()
    rule = config.find_rule(suffix)
    if rule is None:
        raise HTTPException(status_code=500, detail=f"No transcoding rule for format: {suffix}")

    # Check for seek position (time-based seeking for transcoded files)
    seek_pos = _streaming_server.get_seek_position(player_mac)
    start_seconds = seek_pos[0] if seek_pos else None
    end_seconds = seek_pos[1] if seek_pos else None

    # Capture generation for logging (this token is replaced on each queue)
    cancel_token = _streaming_server.get_cancellation_token(player_mac)
    token_generation = getattr(cancel_token, "generation", None)

    async def generate() -> AsyncIterator[bytes]:
        """Generate transcoded audio chunks.

        LMS-style approach: No locks! When a new seek/stream is requested,
        the old stream's cancel_token is set, and this generator aborts
        on the next chunk. The new stream starts immediately without waiting.

        This matches LMS's StreamingController._Stream() which simply closes
        the old songStreamController and opens a new one - no serialization.
        """
        started_at = time.time()
        bytes_sent = 0
        chunk_count = 0
        abort_reason: str | None = None

        # LMS-style: No lock! Old stream aborts via cancel_token, new stream starts immediately.
        # This prevents blocking during rapid seeks where the old transcoder might be slow.
        logger.debug(
            "[STREAM] player=%s gen=%s starting transcode (LMS-style, no lock)",
            player_mac,
            token_generation,
        )

        try:
            async for chunk in transcode_stream(
                file_path,
                rule=rule,
                start_seconds=start_seconds,
                end_seconds=end_seconds,
            ):
                # Abort quickly if the client went away.
                #
                # This is important because upstream cancellation might not arrive
                # immediately, and we want to stop transcoding as soon as possible.
                if await request.is_disconnected():
                    abort_reason = "disconnected"
                    logger.info(
                        "Stream client disconnected for player %s (transcoded, gen=%s)",
                        player_mac,
                        token_generation,
                    )
                    return

                # Abort quickly on generation cancellation (seek/track change).
                # This is the LMS-style "close stream" - when a new seek comes in,
                # cancel_stream() sets cancelled=True and we abort immediately.
                if cancel_token and cancel_token.cancelled:
                    abort_reason = "cancelled"
                    logger.info(
                        "[STREAM] Stream cancelled for player %s (transcoded, gen=%s) - new seek/track",
                        player_mac,
                        token_generation,
                    )
                    return

                yield chunk
                bytes_sent += len(chunk)
                chunk_count += 1

                # Clear seek position after first chunk so subsequent requests don't reuse it.
                if chunk_count == 1:
                    _streaming_server.clear_seek_position(player_mac)

        except asyncio.CancelledError:
            # Uvicorn/FastAPI can cancel the generator when the client disconnects.
            abort_reason = abort_reason or "cancelled_error"
            raise
        except Exception as e:
            abort_reason = abort_reason or "error"
            logger.exception("Transcoding error for %s: %s", file_path, e)
        finally:
            elapsed = time.time() - started_at
            final_reason = abort_reason or "eof"

            # A transcoded stream that ends (EOF) after a tiny amount of data and very quickly
            # can indicate a broken pipeline. However, if the request is a near-end seek
            # (e.g. start close to end), a tiny/fast EOF is expected and should not warn.
            #
            # We only warn when the requested segment is "meaningfully long" but still ends
            # very quickly / with very little data.
            requested_segment_seconds: float | None = None
            if start_seconds is not None and end_seconds is not None:
                requested_segment_seconds = max(0.0, float(end_seconds) - float(start_seconds))

            # Only warn when:
            # - we actually know the requested segment length (start/end provided), AND
            # - it was a "meaningfully long" segment (>=10s), AND
            # - we still ended extremely quickly / with very little data.
            warn_suspicious_eof = (
                final_reason == "eof"
                and bytes_sent > 0
                and (
                    bytes_sent < _SUSPICIOUS_TRANSCODE_EOF_BYTES
                    or elapsed < _SUSPICIOUS_TRANSCODE_EOF_SECONDS
                )
                and requested_segment_seconds is not None
                and requested_segment_seconds >= 10.0
            )

            if warn_suspicious_eof:
                logger.warning(
                    "Suspicious transcoded EOF player=%s gen=%s chunks=%d bytes=%d elapsed=%.3fs file=%s start=%s end=%s",
                    player_mac,
                    token_generation,
                    chunk_count,
                    bytes_sent,
                    elapsed,
                    file_path.name,
                    f"{start_seconds:.3f}" if start_seconds is not None else "None",
                    f"{end_seconds:.3f}" if end_seconds is not None else "None",
                )

            logger.info(
                "Transcoded stream finished player=%s gen=%s reason=%s chunks=%d bytes=%d elapsed=%.3fs file=%s",
                player_mac,
                token_generation,
                final_reason,
                chunk_count,
                bytes_sent,
                elapsed,
                file_path.name,
            )

    # Transcoded output is MP3 (as per streaming/policy.py)
    return StreamingResponse(
        generate(),
        media_type="audio/mpeg",
        headers={
            "Accept-Ranges": "none",  # No byte-range seeking for transcoded streams
            "X-Content-Type-Options": "nosniff",
        },
    )


async def _stream_direct(
    request: Request,
    player_mac: str,
    file_path: Path,
    file_size: int,
    range_header: str | None,
) -> StreamingResponse:
    """
    Stream a file directly without transcoding (for MP3/FLAC/OGG etc.).

    Supports byte-range requests for seeking.
    """
    if _streaming_server is None:
        raise HTTPException(status_code=503, detail="Streaming server not initialized")

    content_type = _get_content_type(file_path)

    # Check for forced byte offset (from time-based seeking)
    forced_offset = _streaming_server.get_byte_offset(player_mac)

    # Parse Range header or use forced offset
    start_byte = 0
    end_byte = file_size - 1

    if forced_offset is not None:
        start_byte = min(forced_offset, file_size - 1)
    elif range_header:
        start_byte, end_byte = _parse_range_header(range_header, file_size)

    content_length = end_byte - start_byte + 1

    cancel_token = _streaming_server.get_cancellation_token(player_mac)
    token_generation = getattr(cancel_token, "generation", None)

    async def generate() -> AsyncIterator[bytes]:
        """Generate file chunks from the specified byte range."""
        started_at = time.time()
        bytes_sent = 0
        chunk_size = 65536  # 64KB chunks
        chunk_count = 0
        abort_reason: str | None = None

        try:
            with open(file_path, "rb") as f:
                f.seek(start_byte)
                remaining = content_length

                while remaining > 0:
                    # Abort if client disconnected.
                    if chunk_count % 4 == 0 and await request.is_disconnected():
                        abort_reason = "disconnected"
                        logger.info(
                            "Stream client disconnected for player %s (direct, gen=%s)",
                            player_mac,
                            token_generation,
                        )
                        return

                    # Abort on cancellation (seek/track change).
                    if chunk_count % 4 == 0 and cancel_token and cancel_token.cancelled:
                        abort_reason = "cancelled"
                        logger.info(
                            "Stream cancelled for player %s (direct, gen=%s)",
                            player_mac,
                            token_generation,
                        )
                        return

                    read_size = min(chunk_size, remaining)
                    chunk = f.read(read_size)
                    if not chunk:
                        break

                    yield chunk
                    remaining -= len(chunk)
                    bytes_sent += len(chunk)
                    chunk_count += 1

                    # Clear byte offset after first chunk
                    if chunk_count == 1 and forced_offset is not None:
                        _streaming_server.clear_byte_offset(player_mac)

        except asyncio.CancelledError:
            abort_reason = abort_reason or "cancelled_error"
            raise
        except Exception as e:
            abort_reason = abort_reason or "error"
            logger.exception("Streaming error for %s: %s", file_path, e)
        finally:
            elapsed = time.time() - started_at
            logger.info(
                "Direct stream finished player=%s gen=%s reason=%s chunks=%d bytes=%d elapsed=%.3fs file=%s range=%s-%s",
                player_mac,
                token_generation,
                abort_reason or "eof",
                chunk_count,
                bytes_sent,
                elapsed,
                file_path.name,
                start_byte,
                end_byte,
            )

    # Determine response status and headers
    if start_byte > 0 or end_byte < file_size - 1:
        # Partial content
        return StreamingResponse(
            generate(),
            status_code=206,
            media_type=content_type,
            headers={
                "Content-Range": f"bytes {start_byte}-{end_byte}/{file_size}",
                "Content-Length": str(content_length),
                "Accept-Ranges": "bytes",
            },
        )
    else:
        # Full content
        return StreamingResponse(
            generate(),
            media_type=content_type,
            headers={
                "Content-Length": str(file_size),
                "Accept-Ranges": "bytes",
            },
        )


# =============================================================================
# Remote URL proxy streaming
# =============================================================================


def _get_httpx_client() -> httpx.AsyncClient:
    """Return (and lazily create) the shared httpx client for remote proxying."""
    global _httpx_client
    if _httpx_client is None or _httpx_client.is_closed:
        _httpx_client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=_REMOTE_CONNECT_TIMEOUT,
                read=_REMOTE_READ_TIMEOUT,
                write=None,
                pool=None,
            ),
            follow_redirects=True,
            # Request ICY metadata from Shoutcast/Icecast servers.
            headers={"Icy-MetaData": "1"},
        )
    return _httpx_client


async def _stream_remote_proxy(
    request: Request,
    player_mac: str,
    remote: RemoteStreamInfo,
) -> StreamingResponse:
    """Proxy a remote HTTP(S) audio stream to the player.

    The server fetches the remote URL and relays audio chunks so that
    Squeezebox hardware (which cannot handle HTTPS) can play the stream.

    ICY metadata (title changes) is stripped from the byte stream and
    logged.  In a future iteration the parsed metadata will be forwarded
    via Slimproto META frames.
    """
    if _streaming_server is None:
        raise HTTPException(status_code=503, detail="Streaming server not initialized")

    cancel_token = _streaming_server.get_cancellation_token(player_mac)
    token_generation = getattr(cancel_token, "generation", None)

    client = _get_httpx_client()

    async def generate() -> AsyncIterator[bytes]:
        started_at = time.time()
        bytes_sent = 0
        chunk_count = 0
        abort_reason: str | None = None
        resp: httpx.Response | None = None

        try:
            # Use streaming request so we can iterate chunks without
            # buffering the entire (potentially infinite) response.
            req_headers: dict[str, str] = {}
            if remote.start_byte > 0:
                req_headers["Range"] = f"bytes={remote.start_byte}-"
            resp = await client.send(
                client.build_request("GET", remote.url, headers=req_headers),
                stream=True,
            )

            # Accept both 200 (full content) and 206 (partial / Range honoured).
            if resp.status_code not in (200, 206):
                resp.raise_for_status()
            # Parse ICY metadata interval if server advertises one.
            icy_metaint_str = resp.headers.get("icy-metaint", "")
            icy_metaint: int = int(icy_metaint_str) if icy_metaint_str.isdigit() else 0

            # Determine actual content type from response if available.
            _actual_ct = resp.headers.get("content-type", remote.content_type)

            logger.info(
                "[REMOTE] Proxy stream started player=%s gen=%s url=%s ct=%s icy_metaint=%d",
                player_mac,
                token_generation,
                remote.title or remote.url,
                _actual_ct,
                icy_metaint,
            )

            if icy_metaint > 0:
                # ICY-aware relay: strip inline metadata blocks.
                async for chunk in _icy_strip_relay(
                    resp.aiter_bytes(chunk_size=_REMOTE_CHUNK_SIZE),
                    icy_metaint,
                    player_mac,
                    cancel_token,
                    request,
                ):
                    yield chunk
                    bytes_sent += len(chunk)
                    chunk_count += 1
            else:
                # Simple relay — just forward chunks.
                async for chunk in resp.aiter_bytes(chunk_size=_REMOTE_CHUNK_SIZE):
                    if chunk_count % 4 == 0 and await request.is_disconnected():
                        abort_reason = "disconnected"
                        return
                    if chunk_count % 4 == 0 and cancel_token and cancel_token.cancelled:
                        abort_reason = "cancelled"
                        return

                    yield chunk
                    bytes_sent += len(chunk)
                    chunk_count += 1

        except httpx.HTTPStatusError as exc:
            abort_reason = f"http_{exc.response.status_code}"
            logger.error(
                "[REMOTE] HTTP error proxying for player %s: %s %s",
                player_mac,
                exc.response.status_code,
                remote.url,
            )
        except httpx.RequestError as exc:
            abort_reason = "request_error"
            logger.error(
                "[REMOTE] Request error proxying for player %s: %s",
                player_mac,
                exc,
            )
        except asyncio.CancelledError:
            abort_reason = abort_reason or "cancelled_error"
            raise
        except Exception as exc:
            abort_reason = abort_reason or "error"
            logger.exception(
                "[REMOTE] Unexpected error proxying for player %s: %s",
                player_mac,
                exc,
            )
        finally:
            # Always close the upstream response to release the connection.
            if resp is not None:
                await resp.aclose()
            elapsed = time.time() - started_at
            final_reason = abort_reason or "eof"
            logger.info(
                "[REMOTE] Proxy stream finished player=%s gen=%s reason=%s chunks=%d bytes=%d elapsed=%.3fs url=%s",
                player_mac,
                token_generation,
                final_reason,
                chunk_count,
                bytes_sent,
                elapsed,
                remote.title or remote.url,
            )

            # ── LMS _RetryOrNext equivalent: signal re-stream candidate ──
            #
            # For live streams that ended unexpectedly (not intentionally
            # cancelled by the server, and not because the player
            # disconnected), fire an event so the server can attempt to
            # reconnect — mirroring LMS StreamingController.pm L920-927.
            #
            # Intentional endings that must NOT trigger re-stream:
            #   "cancelled"       — server cancelled (track change / seek)
            #   "cancelled_error" — asyncio.CancelledError (same cause)
            #   "disconnected"    — player closed its HTTP connection
            if remote.is_live and final_reason not in (
                "cancelled",
                "cancelled_error",
                "disconnected",
            ):
                logger.info(
                    "[REMOTE] Live stream dropped for player %s gen=%s reason=%s — firing re-stream event",
                    player_mac,
                    token_generation,
                    final_reason,
                )
                event_bus.publish_sync(
                    LiveStreamDroppedEvent(
                        player_id=player_mac,
                        stream_generation=token_generation,
                        remote_url=remote.url,
                        content_type=remote.content_type,
                        title=remote.title,
                    )
                )

    # Use the content type advertised by the provider; the player will
    # rely on the format hint in the strm command for decoding.
    return StreamingResponse(
        generate(),
        media_type=remote.content_type,
        headers={
            "Accept-Ranges": "none",
            "X-Content-Type-Options": "nosniff",
        },
    )


async def _icy_strip_relay(
    byte_stream: AsyncIterator[bytes],
    metaint: int,
    player_mac: str,
    cancel_token: Any,
    request: Request,
) -> AsyncIterator[bytes]:
    """Relay audio bytes from an ICY stream, stripping inline metadata.

    Shoutcast/Icecast servers interleave metadata blocks every *metaint*
    audio bytes.  The metadata block is preceded by a single length byte
    (actual length = value * 16).  We strip these blocks and yield only
    the audio data.

    Parsed metadata (e.g. ``StreamTitle``) is logged; in a future version
    it will be forwarded to the player via Slimproto META frames.
    """
    buf = bytearray()
    audio_remaining = metaint  # bytes of audio until next metadata block
    chunk_idx = 0

    async for raw_chunk in byte_stream:
        if chunk_idx % 4 == 0 and await request.is_disconnected():
            return
        if chunk_idx % 4 == 0 and cancel_token and cancel_token.cancelled:
            return

        buf.extend(raw_chunk)
        chunk_idx += 1

        while buf:
            if audio_remaining > 0:
                # Yield up to audio_remaining bytes of audio data.
                take = min(len(buf), audio_remaining)
                yield bytes(buf[:take])
                del buf[:take]
                audio_remaining -= take
            else:
                # Next byte is the ICY metadata length indicator.
                if len(buf) < 1:
                    break  # need more data
                meta_len = buf[0] * 16
                total_meta = 1 + meta_len
                if len(buf) < total_meta:
                    break  # need more data
                if meta_len > 0:
                    meta_bytes = bytes(buf[1:total_meta])
                    _log_icy_metadata(meta_bytes, player_mac)
                del buf[:total_meta]
                audio_remaining = metaint


def _log_icy_metadata(meta_bytes: bytes, player_mac: str) -> None:
    """Parse an ICY metadata block, log it, and store the StreamTitle.

    The extracted ``StreamTitle`` is saved on the module-level
    ``_streaming_server`` so that ``cmd_status`` can read it via
    ``streaming_server.get_icy_title(player_mac)`` and expose it as
    ``current_title`` for radio streams.

    When the title actually changes (not a repeat of the same ICY block),
    a ``PlayerPlaylistEvent(action="newmetadata")`` is fired so that
    Cometd subscribers receive a fresh ``status`` response immediately.
    This mirrors LMS ``setCurrentTitle()`` (Info.pm L535-540) which fires
    ``['playlist', 'newsong', $title]`` on title change.
    """
    import re

    try:
        text = meta_bytes.rstrip(b"\x00").decode("utf-8", errors="replace")
        if not text:
            return

        logger.debug("[ICY] player=%s metadata: %s", player_mac, text)

        # Extract StreamTitle='Artist - Song'; from the ICY block.
        match = re.search(r"StreamTitle='([^']*)'", text)
        if match and _streaming_server is not None:
            title = match.group(1).strip()
            if title:
                changed = _streaming_server.set_icy_title(player_mac, title)
                if changed:
                    logger.info(
                        "[ICY] player=%s StreamTitle changed: %s",
                        player_mac,
                        title,
                    )
                else:
                    logger.debug(
                        "[ICY] player=%s StreamTitle unchanged: %s",
                        player_mac,
                        title,
                    )

                # ── Push notification on title change (LMS-equivalent) ──
                # LMS fires `['playlist', 'newsong', $title]` when the
                # ICY title changes, which triggers Cometd subscription
                # re-execution so hardware players update Now Playing
                # immediately instead of waiting for the next poll.
                if changed:
                    try:
                        from resonance.core.events import (
                            PlayerPlaylistEvent,
                            event_bus,
                        )

                        event_bus.publish_sync(
                            PlayerPlaylistEvent(
                                player_id=player_mac,
                                action="newmetadata",
                                count=0,
                            )
                        )
                        logger.debug(
                            "[ICY] player=%s fired newmetadata event for title change",
                            player_mac,
                        )
                    except Exception:
                        # Non-critical — polling still works as fallback
                        pass
    except Exception:
        pass


# =============================================================================
# Local file helpers
# =============================================================================


def _get_content_type(file_path: Path) -> str:
    """Get the MIME type for an audio file."""
    suffix = file_path.suffix.lower()
    content_types = {
        ".mp3": "audio/mpeg",
        ".flac": "audio/flac",
        ".ogg": "audio/ogg",
        ".opus": "audio/opus",
        ".wav": "audio/wav",
        ".aiff": "audio/aiff",
        ".aif": "audio/aiff",
        ".m4a": "audio/mp4",
        ".m4b": "audio/mp4",
        ".aac": "audio/aac",
    }
    return content_types.get(suffix, "application/octet-stream")


def _parse_range_header(
    range_header: str,
    file_size: int,
) -> tuple[int, int]:
    """
    Parse an HTTP Range header.

    Args:
        range_header: The Range header value (e.g., "bytes=0-1023")
        file_size: Total file size for validation

    Returns:
        Tuple of (start_byte, end_byte)
    """
    try:
        # Parse "bytes=start-end" format
        if not range_header.startswith("bytes="):
            return 0, file_size - 1

        range_spec = range_header[6:]  # Remove "bytes="

        if range_spec.startswith("-"):
            # Suffix range: "-500" means last 500 bytes
            suffix_len = int(range_spec[1:])
            start = max(0, file_size - suffix_len)
            return start, file_size - 1

        parts = range_spec.split("-")
        start = int(parts[0]) if parts[0] else 0
        end = int(parts[1]) if len(parts) > 1 and parts[1] else file_size - 1

        # Clamp values
        start = max(0, min(start, file_size - 1))
        end = max(start, min(end, file_size - 1))

        return start, end

    except (ValueError, IndexError):
        return 0, file_size - 1
