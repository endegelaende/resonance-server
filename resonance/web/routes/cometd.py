"""
Cometd Routes for Resonance.

Provides the /cometd endpoint for Bayeux protocol, supporting both:
- Long-polling: Client makes request, waits for response, makes new request
- Streaming: Client makes request, server keeps connection open and sends chunked events

Streaming mode is required for Squeezebox Boom/Touch/Radio devices which use
the embedded SqueezePlay/Jive UI.

Reference: Slim/Web/Cometd.pm in LMS codebase
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

if TYPE_CHECKING:
    from resonance.web.cometd import CometdManager
    from resonance.web.jsonrpc import JsonRpcHandler

logger = logging.getLogger(__name__)

router = APIRouter()

# References set during route registration
_cometd_manager: CometdManager | None = None
_jsonrpc_handler: JsonRpcHandler | None = None

# Streaming connection timeout (seconds)
STREAMING_TIMEOUT = 3600  # 1 hour — Squeezebox Radio stays connected for long sessions
STREAMING_HEARTBEAT_INTERVAL = 30  # Send heartbeat every 30 seconds


def register_cometd_routes(
    app,
    cometd_manager: CometdManager,
    jsonrpc_handler: JsonRpcHandler | None = None,
) -> None:
    """
    Register Cometd routes with the FastAPI app.

    Args:
        app: FastAPI application instance
        cometd_manager: CometdManager for session management
        jsonrpc_handler: Optional JsonRpcHandler for /slim/request
    """
    global _cometd_manager, _jsonrpc_handler
    _cometd_manager = cometd_manager
    _jsonrpc_handler = jsonrpc_handler

    # Set the JSON-RPC handler on the manager for /slim/request
    if jsonrpc_handler is not None:
        cometd_manager.set_jsonrpc_handler(jsonrpc_handler)

    app.include_router(router)


async def _process_message(
    manager: CometdManager,
    msg: dict[str, Any],
) -> tuple[list[dict[str, Any]], bool, str | None]:
    """
    Process a single Cometd message.

    Returns:
        Tuple of (responses, is_connect, connection_type)
    """
    responses: list[dict[str, Any]] = []
    is_connect = False
    connection_type = None

    if not isinstance(msg, dict):
        return [{"successful": False, "error": "Invalid message format"}], False, None

    channel = msg.get("channel", "")
    msg_id = msg.get("id")
    client_id = msg.get("clientId", "")

    try:
        if channel == "/meta/handshake":
            response = await manager.handshake(msg_id=msg_id)
            responses.append(response)

        elif channel in ("/meta/connect", "/meta/reconnect"):
            is_connect = True
            connection_type = msg.get("connectionType", "long-polling")

            # For reconnect with an unknown clientId, tell the client to
            # re-handshake instead of silently auto-creating the session.
            #
            # ROOT CAUSE of the highlighting bug: after a server restart all
            # slim subscriptions are lost.  The Radio sends /meta/reconnect
            # with its old clientId.  If we auto-create the session the Radio
            # thinks the old /slim/subscribe is still active and never
            # re-subscribes → no subscription to re-execute → no
            # playlist_timestamp → no highlighting.
            #
            # By returning successful:false + advice:{reconnect:"handshake"}
            # JiveLite starts a fresh session: handshake → subscribe →
            # /slim/subscribe (with the status command) → everything works.
            if channel == "/meta/reconnect" and client_id:
                if not await manager.is_valid_client(client_id):
                    logger.info(
                        "Unknown clientId %s on /meta/reconnect — "
                        "advising handshake so client re-subscribes",
                        client_id,
                    )
                    response: dict[str, Any] = {
                        "channel": "/meta/reconnect",
                        "successful": False,
                        "clientId": client_id,
                        "error": "Unknown client",
                        "advice": {"reconnect": "handshake", "interval": 0},
                    }
                    if msg_id is not None:
                        response["id"] = msg_id
                    responses.append(response)
                    # This is NOT a valid connect — don't start streaming
                    is_connect = False
                    return responses, is_connect, connection_type

            # For streaming, we'll handle the response differently
            # Just return the connect response immediately
            response = {
                "channel": channel,  # Reply on same channel (connect or reconnect)
                "successful": True,
                "clientId": client_id,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "advice": {
                    "interval": 0 if connection_type == "long-polling" else 5000,
                },
            }
            if msg_id is not None:
                response["id"] = msg_id
            responses.append(response)

        elif channel == "/meta/disconnect":
            response = await manager.disconnect(
                client_id=client_id,
                msg_id=msg_id,
            )
            responses.append(response)

        elif channel == "/meta/subscribe":
            subscription = msg.get("subscription", [])
            result = await manager.subscribe(
                client_id=client_id,
                subscription=subscription,
                msg_id=msg_id,
            )
            responses.extend(result)

        elif channel == "/meta/unsubscribe":
            subscription = msg.get("subscription", [])
            result = await manager.unsubscribe(
                client_id=client_id,
                subscription=subscription,
                msg_id=msg_id,
            )
            responses.extend(result)

        elif channel == "/slim/subscribe":
            # Extract clientId from response channel if not provided directly
            # LMS/Boom sends: {"data": {"response": "/25e894ff/slim/..."}, "channel": "/slim/subscribe"}
            # The clientId is embedded in the response path: /<clientId>/slim/...
            effective_client_id = client_id
            if not effective_client_id:
                data = msg.get("data", {})
                resp_channel = data.get("response", "") if isinstance(data, dict) else ""
                if resp_channel and resp_channel.startswith("/"):
                    # Extract clientId from path like "/25e894ff/slim/serverstatus"
                    parts = resp_channel.split("/")
                    if len(parts) >= 2 and parts[1]:
                        effective_client_id = parts[1]
                        logger.debug(
                            "Extracted clientId %s from response channel %s",
                            effective_client_id,
                            resp_channel,
                        )

            if not effective_client_id:
                logger.warning(
                    "/slim/subscribe with empty clientId — ignoring (msg=%s)",
                    {k: v for k, v in msg.items() if k != "data"},
                )
                response_dict = {
                    "channel": "/slim/subscribe",
                    "successful": False,
                    "error": "No clientId",
                }
                if msg_id is not None:
                    response_dict["id"] = msg_id
                responses.append(response_dict)
            else:
                response = await manager.slim_subscribe(
                    client_id=effective_client_id,
                    request=msg,
                    msg_id=msg_id,
                )
                responses.append(response)

        elif channel == "/slim/unsubscribe":
            # Extract clientId from response channel if not provided directly (same as subscribe)
            effective_client_id = client_id
            if not effective_client_id:
                data = msg.get("data", {})
                resp_channel = data.get("response", "") if isinstance(data, dict) else ""
                if resp_channel and resp_channel.startswith("/"):
                    parts = resp_channel.split("/")
                    if len(parts) >= 2 and parts[1]:
                        effective_client_id = parts[1]

            if not effective_client_id:
                # No clientId found — ignore instead of creating ghost client with key ""
                logger.warning(
                    "/slim/unsubscribe with empty clientId — ignoring (msg=%s)",
                    {k: v for k, v in msg.items() if k != "data"},
                )
                response_dict: dict[str, Any] = {
                    "channel": "/slim/unsubscribe",
                    "successful": False,
                    "error": "No clientId",
                }
                if msg_id is not None:
                    response_dict["id"] = msg_id
                responses.append(response_dict)
            else:
                response = await manager.slim_unsubscribe(
                    client_id=effective_client_id,
                    request=msg,
                    msg_id=msg_id,
                )
                responses.append(response)

        elif channel == "/slim/request":
            # Extract clientId from response channel if not provided directly (same as subscribe)
            effective_client_id = client_id
            if not effective_client_id:
                data = msg.get("data", {})
                resp_channel = data.get("response", "") if isinstance(data, dict) else ""
                if resp_channel and resp_channel.startswith("/"):
                    parts = resp_channel.split("/")
                    if len(parts) >= 2 and parts[1]:
                        effective_client_id = parts[1]

            if not effective_client_id:
                logger.warning(
                    "/slim/request with empty clientId — ignoring (msg=%s)",
                    {k: v for k, v in msg.items() if k != "data"},
                )
                response_dict = {
                    "channel": "/slim/request",
                    "successful": False,
                    "error": "No clientId",
                }
                if msg_id is not None:
                    response_dict["id"] = msg_id
                responses.append(response_dict)
            else:
                response = await manager.slim_request(
                    client_id=effective_client_id,
                    request=msg,
                    msg_id=msg_id,
                )
                responses.append(response)

        else:
            logger.warning("Unknown cometd channel: %s", channel)
            responses.append(
                {
                    "channel": channel,
                    "successful": False,
                    "error": f"Unknown channel: {channel}",
                    **({"id": msg_id} if msg_id else {}),
                }
            )

    except Exception as e:
        logger.exception("Error handling cometd message on %s: %s", channel, e)
        responses.append(
            {
                "channel": channel,
                "successful": False,
                "error": str(e),
                **({"id": msg_id} if msg_id else {}),
            }
        )

    return responses, is_connect, connection_type


async def _streaming_event_generator(
    manager: CometdManager,
    client_id: str,
    initial_responses: list[dict[str, Any]],
) -> AsyncGenerator[bytes, None]:
    """
    Generate streaming Cometd events as chunked HTTP response.

    This keeps the connection open and sends events as they arrive,
    using chunked transfer encoding.
    """
    logger.info("Starting streaming connection for client %s", client_id)

    # Send initial responses first
    if initial_responses:
        chunk = json.dumps(initial_responses) + "\r\n"
        logger.debug("Streaming initial chunk to %s: %s", client_id, chunk[:200])
        yield chunk.encode("utf-8")

    # Keep connection open and stream events
    start_time = time.time()
    last_heartbeat = time.time()

    try:
        while time.time() - start_time < STREAMING_TIMEOUT:
            # Check if client is still valid
            if not await manager.is_valid_client(client_id):
                logger.info("Client %s no longer valid, closing stream", client_id)
                break

            # Get waiter for this client
            async with manager._lock:
                waiter = manager._connect_waiters.get(client_id)
                client = manager._clients.get(client_id)

            if client is None:
                break

            # Wait for events with short timeout
            if waiter:
                try:
                    await asyncio.wait_for(waiter.wait(), timeout=1.0)
                    waiter.clear()
                except TimeoutError:
                    pass

            # Check for pending events
            events = client.get_and_clear_events()
            if events:
                chunk = json.dumps(events) + "\r\n"
                logger.debug("Streaming events to %s: %s", client_id, chunk[:200])
                yield chunk.encode("utf-8")
                last_heartbeat = time.time()

            # Send heartbeat to keep connection alive
            elif time.time() - last_heartbeat > STREAMING_HEARTBEAT_INTERVAL:
                heartbeat = [{"channel": "/meta/ping", "successful": True}]
                chunk = json.dumps(heartbeat) + "\r\n"
                yield chunk.encode("utf-8")
                last_heartbeat = time.time()

            # Touch client to prevent timeout
            client.touch()

    except asyncio.CancelledError:
        logger.info("Streaming connection cancelled for client %s", client_id)
    except Exception as e:
        logger.exception("Error in streaming connection for %s: %s", client_id, e)
    finally:
        # Send reconnect advice so Squeezebox Radio reconnects automatically
        # instead of silently losing the push connection.
        try:
            reconnect_advice = [{
                "channel": "/meta/connect",
                "successful": True,
                "advice": {"reconnect": "retry", "interval": 0, "timeout": 0},
            }]
            chunk = json.dumps(reconnect_advice) + "\r\n"
            yield chunk.encode("utf-8")
        except Exception:
            pass  # Best-effort — connection may already be closed
        logger.info("Streaming connection ended for client %s", client_id)


@router.post("/cometd/")
@router.post("/cometd")
async def cometd_handler(request: Request):
    """
    Handle Cometd/Bayeux protocol requests.

    Accepts an array of Bayeux messages and returns responses.
    Each message has a 'channel' field indicating the operation.

    For streaming connections (Squeezebox Boom/Touch/Radio), this returns
    a StreamingResponse with chunked transfer encoding.

    Meta channels:
    - /meta/handshake: Start a new session, get clientId
    - /meta/connect: Long-poll or streaming for events
    - /meta/disconnect: End session
    - /meta/subscribe: Subscribe to channels
    - /meta/unsubscribe: Unsubscribe from channels

    Slim channels:
    - /slim/subscribe: Subscribe to LMS events with a request
    - /slim/unsubscribe: Unsubscribe from LMS events
    - /slim/request: Execute a one-time LMS command
    """
    # Check both global and app.state for cometd_manager
    manager = _cometd_manager
    if manager is None and hasattr(request.app, "state"):
        manager = getattr(request.app.state, "cometd_manager", None)

    if manager is None:
        raise HTTPException(status_code=503, detail="Cometd service not initialized")

    try:
        body = await request.json()
    except Exception as e:
        logger.warning("Invalid JSON in cometd request: %s", e)
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Normalize to list
    messages = body if isinstance(body, list) else [body]

    if not messages:
        raise HTTPException(status_code=400, detail="Empty message list")

    all_responses: list[dict[str, Any]] = []
    has_connect = False
    connect_client_id = None
    is_streaming = False

    # Process all messages
    for msg in messages:
        responses, is_connect, connection_type = await _process_message(manager, msg)
        all_responses.extend(responses)

        if is_connect:
            has_connect = True
            connect_client_id = msg.get("clientId", "")
            is_streaming = connection_type == "streaming"

    # If this is a streaming connect, return a StreamingResponse
    if has_connect and is_streaming and connect_client_id:
        logger.info(
            "Client %s requested streaming connection, starting chunked response",
            connect_client_id,
        )
        return StreamingResponse(
            _streaming_event_generator(manager, connect_client_id, all_responses),
            media_type="application/json",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                # Note: FastAPI/Starlette handles Transfer-Encoding: chunked automatically
                # for StreamingResponse
            },
        )

    # For long-polling connect, wait for events
    if has_connect and not is_streaming and connect_client_id:
        # Wait for events or timeout
        result = await manager.connect(
            client_id=connect_client_id,
            msg_id=None,
            timeout_s=60.0,
        )
        # Replace the simple connect response with the full one that may include events
        # Find and replace the connect response
        for i, resp in enumerate(all_responses):
            if resp.get("channel") == "/meta/connect":
                all_responses = all_responses[:i] + all_responses[i + 1 :]
                break
        if isinstance(result, list):
            all_responses.extend(result)
        else:
            all_responses.append(result)

    return all_responses
