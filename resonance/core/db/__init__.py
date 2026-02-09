"""
Internal DB subpackage for Resonance.

This package exists to split the large `library_db.py` module into focused units
(models, schema/migrations, and query groups) while keeping `LibraryDb` as the
single public interface that the rest of the codebase imports.

Re-exports here are primarily for convenience inside the `core` package.
External code should continue to import `LibraryDb` from `resonance.core.library_db`.
"""

from __future__ import annotations

# Models / DTOs
from .models import AlbumRow, ArtistRow, TrackRow, UpsertTrack

# Schema / migrations
from .schema import ensure_schema, migrate

__all__ = [
    "AlbumRow",
    "ArtistRow",
    "TrackRow",
    "UpsertTrack",
    "ensure_schema",
    "migrate",
]
