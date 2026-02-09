"""
Player management for Resonance.

This package handles connected Squeezebox players, their state,
playlists, and synchronization.
"""

from resonance.player.client import PlayerClient
from resonance.player.registry import PlayerRegistry

__all__ = [
    "PlayerClient",
    "PlayerRegistry",
]
