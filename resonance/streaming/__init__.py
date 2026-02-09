"""
Streaming module for Resonance.

This module provides HTTP streaming endpoints for serving audio
to Squeezebox players.

Components:
    StreamingServer: Manages audio file queuing and streaming.
    SeekCoordinator: Coordinates seek operations with latest-wins semantics.
"""

from resonance.streaming.seek_coordinator import (
    SeekCoordinator,
    cleanup_processes,
    get_seek_coordinator,
    init_seek_coordinator,
    set_seek_coordinator,
    terminate_subprocess_safely,
)
from resonance.streaming.server import StreamingServer

__all__ = [
    "SeekCoordinator",
    "StreamingServer",
    "cleanup_processes",
    "get_seek_coordinator",
    "init_seek_coordinator",
    "set_seek_coordinator",
    "terminate_subprocess_safely",
]
