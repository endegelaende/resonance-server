"""
Core domain package.

This package contains business logic which should be independent of any UI layer
(web, CLI, etc.). The goal is to keep this layer small, testable, and free of
networking concerns.

We intentionally keep exports minimal; consumers should usually import from the
specific module they need (e.g. `resonance.core.library`).
"""

from __future__ import annotations

__all__: list[str] = [
    "CoreError",
    "NotFoundError",
]


class CoreError(Exception):
    """Base class for core-layer exceptions."""


class NotFoundError(CoreError):
    """Raised when an entity (track/album/artist/etc.) cannot be found."""
