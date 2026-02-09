"""
Web Routes Package.

This package contains FastAPI route modules:
- api: REST API endpoints (/api/*)
- streaming: Audio streaming (/stream.mp3)
- cometd: Bayeux long-polling (/cometd)
- artwork: Album artwork (/artwork/*)
"""

from resonance.web.routes.api import register_api_routes
from resonance.web.routes.artwork import register_artwork_routes
from resonance.web.routes.cometd import register_cometd_routes
from resonance.web.routes.streaming import register_streaming_routes

__all__ = [
    "register_api_routes",
    "register_artwork_routes",
    "register_cometd_routes",
    "register_streaming_routes",
]
