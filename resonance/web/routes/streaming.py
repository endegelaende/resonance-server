"""
Streaming Routes for Resonance.

Provides the /stream.mp3 endpoint for audio streaming to Squeezebox players.

Decision logic for transcoding vs. direct streaming is centralized in
resonance.streaming.policy to ensure consistency between the HTTP route
and the player's format expectations (strm command).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from resonance.streaming.crossfade import (
    PreparedCrossfadePlan,
    build_crossfade_command,
    media_type_for_output_format,
)
from resonance.streaming.policy import needs_transcoding
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
_SUSPICIOUS_TRANSCODE_EOF_SECONDS = 1.0            # 1s

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

    # Resolve the file to stream
    file_path = _streaming_server.resolve_file(player)
    if file_path is None:
        raise HTTPException(status_code=404, detail="No track queued for player")

    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")

    file_size = file_path.stat().st_size
    suffix = file_path.suffix.lower()

    # Server-side crossfade plan (if pending for this generation).
    crossfade_plan = _streaming_server.pop_crossfade_plan(player, file_path=file_path)
    if crossfade_plan is not None:
        return await _stream_with_crossfade(request, player, crossfade_plan)

    # Get Range header from request
    range_header = request.headers.get("range")

    # Resolve device type for transcoding decision.
    device_type = None
    if _player_registry is not None:
        player_client = await _player_registry.get_by_mac(player)
        if player_client is not None and getattr(player_client, "info", None) is not None:
            device_type = player_client.info.device_type

    # Check if we need to transcode
    if needs_transcoding(suffix, device_type=device_type):
        return await _stream_with_transcoding(request, player, file_path)
    else:
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
