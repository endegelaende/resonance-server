"""
Resonance Web Layer.

This package provides the HTTP/JSON-RPC/REST API layer for Resonance,
enabling communication with web browsers, mobile apps (iPeng, Squeezer),
and other LMS-compatible clients.

Components:
- WebServer: FastAPI application with all routes
- CometdManager: Long-polling for real-time updates
- JSON-RPC handlers: LMS-compatible command interface
"""

from resonance.web.cometd import CometdClient, CometdManager
from resonance.web.server import WebServer

__all__ = [
    "CometdClient",
    "CometdManager",
    "WebServer",
]
