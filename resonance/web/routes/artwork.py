"""
Artwork Routes for Resonance.

Provides endpoints for serving album artwork:
- /api/artwork/track/{track_id}: Get artwork for a specific track
- /api/artwork/album/{album_id}: Get artwork for a specific album
- /api/artwork/track/{track_id}/blurhash: Get BlurHash placeholder for a track
- /api/artwork/album/{album_id}/blurhash: Get BlurHash placeholder for an album
- /music/{id}/cover_{spec}: LMS-compatible resized cover art for Squeezebox devices
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request, Response

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

from resonance.web.jsonrpc_helpers import to_dict

if TYPE_CHECKING:
    from resonance.core.artwork import ArtworkManager
    from resonance.core.library import MusicLibrary

logger = logging.getLogger(__name__)

router = APIRouter(tags=["artwork"])

# References set during route registration
_artwork_manager: ArtworkManager | None = None
_music_library: MusicLibrary | None = None


def register_artwork_routes(
    app,
    artwork_manager: ArtworkManager,
    music_library: MusicLibrary,
) -> None:
    """
    Register artwork routes with the FastAPI app.

    Args:
        app: FastAPI application instance
        artwork_manager: ArtworkManager for extracting/caching artwork
        music_library: MusicLibrary for track lookups
    """
    global _artwork_manager, _music_library
    _artwork_manager = artwork_manager
    _music_library = music_library
    app.include_router(router)


@router.get("/api/artwork/track/{track_id}")
async def get_track_artwork(
    track_id: int,
    request: Request,
) -> Response:
    """
    Serve artwork for a specific track ID.

    This endpoint fetches the track metadata to get the file path,
    then uses the ArtworkManager to extract and return the image.

    Supports HTTP caching via ETag/If-None-Match headers.
    """
    if _artwork_manager is None or _music_library is None:
        raise HTTPException(status_code=503, detail="Artwork service not initialized")

    # Get track path from database
    db = _music_library._db
    row = await db.get_track_by_id(track_id)

    if row is None:
        raise HTTPException(status_code=404, detail="Track not found")

    track_path = (
        getattr(row, "path", None)
        if hasattr(row, "path")
        else row.get("path")
        if isinstance(row, dict)
        else None
    )

    if not track_path:
        raise HTTPException(status_code=404, detail="Track has no path")

    file_path = Path(track_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Track file not found")

    # Get artwork from manager (extracts or returns cached)
    try:
        result = await _artwork_manager.get_artwork(str(file_path))
        if result is None:
            raise HTTPException(status_code=404, detail="No artwork available")
        artwork_data, content_type, _etag = result
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("Failed to get artwork for track %d: %s", track_id, e)
        raise HTTPException(status_code=404, detail="No artwork available")

    # Generate ETag for caching
    etag = hashlib.md5(artwork_data).hexdigest()

    # Check If-None-Match header
    if_none_match = request.headers.get("if-none-match")
    if if_none_match and if_none_match.strip('"') == etag:
        return Response(status_code=304)

    return Response(
        content=artwork_data,
        media_type=content_type or "image/jpeg",
        headers={
            "ETag": f'"{etag}"',
            "Cache-Control": "public, max-age=86400",  # Cache for 1 day
        },
    )


@router.get("/api/artwork/album/{album_id}")
async def get_album_artwork(
    album_id: int,
    request: Request,
) -> Response:
    """
    Serve artwork for a specific album ID.

    Gets the first track from the album and extracts its artwork.
    """
    if _artwork_manager is None or _music_library is None:
        raise HTTPException(status_code=503, detail="Artwork service not initialized")

    # Get first track from album
    db = _music_library._db
    rows = await db.list_tracks_by_album(album_id=album_id, offset=0, limit=1)

    if not rows:
        raise HTTPException(status_code=404, detail="Album not found or empty")

    row = rows[0]
    track_path = (
        getattr(row, "path", None)
        if hasattr(row, "path")
        else row.get("path")
        if isinstance(row, dict)
        else None
    )

    if not track_path:
        raise HTTPException(status_code=404, detail="Album track has no path")

    file_path = Path(track_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Track file not found")

    # Get artwork from manager
    try:
        result = await _artwork_manager.get_artwork(str(file_path))
        if result is None:
            raise HTTPException(status_code=404, detail="No artwork available")
        artwork_data, content_type, _etag = result
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("Failed to get artwork for album %d: %s", album_id, e)
        raise HTTPException(status_code=404, detail="No artwork available")

    # Generate ETag for caching
    etag = hashlib.md5(artwork_data).hexdigest()

    # Check If-None-Match header
    if_none_match = request.headers.get("if-none-match")
    if if_none_match and if_none_match.strip('"') == etag:
        return Response(status_code=304)

    return Response(
        content=artwork_data,
        media_type=content_type or "image/jpeg",
        headers={
            "ETag": f'"{etag}"',
            "Cache-Control": "public, max-age=86400",
        },
    )


@router.get("/artwork/{album_id}")
async def get_artwork_legacy(
    album_id: int,
    request: Request,
) -> Response:
    """
    Legacy artwork endpoint for LMS compatibility.

    Redirects to /api/artwork/album/{album_id}.
    """
    return await get_album_artwork(album_id, request)


@router.get("/music/{track_id}/cover.jpg")
async def get_music_cover_legacy(
    track_id: int,
    request: Request,
) -> Response:
    """
    Legacy cover endpoint for LMS compatibility.

    The path /music/{id}/cover.jpg is used by some LMS clients.
    """
    return await get_track_artwork(track_id, request)


@router.get("/music/{artwork_id}/cover")
async def get_music_cover_no_ext(
    artwork_id: int,
    request: Request,
) -> Response:
    """
    Cover endpoint without extension for JiveLite/SqueezePlay compatibility.

    The path /music/{id}/cover is used by Squeezebox Radio, Touch, etc.
    The ID is treated as album_id first, then track_id as fallback.
    """
    try:
        return await get_album_artwork(artwork_id, request)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
    return await get_track_artwork(artwork_id, request)


def _parse_cover_spec(spec: str) -> tuple[int | None, int | None, str | None, str | None, str | None]:
    """
    Parse LMS cover art specification.

    Format: {WxH}_{mode}_{bgcolor}.{ext}
    Examples:
        - 41x41_m -> (41, 41, 'm', None, None)
        - 100x100_o.jpg -> (100, 100, 'o', None, 'jpg')
        - 50x50_p_ffffff.png -> (50, 50, 'p', 'ffffff', 'png')
        - _m -> (None, None, 'm', None, None)

    Mode letters:
        - m: max (fit within bounds, preserve aspect ratio)
        - o: original (resize to exact dimensions)
        - p: pad (fit within bounds, pad to fill)
        - F: force (?)

    Returns: (width, height, mode, bgcolor, extension)
    """
    # Pattern: optional WxH, optional _mode, optional _bgcolor, optional .ext
    pattern = r'^(?:([0-9X]+)x([0-9X]+))?(?:_(\w))?(?:_([\da-fA-F]+))?(?:\.(\w+))?$'
    match = re.match(pattern, spec)
    if not match:
        return (None, None, None, None, None)

    width_str, height_str, mode, bgcolor, ext = match.groups()

    width = int(width_str) if width_str and width_str != 'X' else None
    height = int(height_str) if height_str and height_str != 'X' else None

    return (width, height, mode, bgcolor, ext)


def _resize_image(
    image_data: bytes,
    width: int | None,
    height: int | None,
    mode: str | None,
    original_content_type: str | None = None,
) -> tuple[bytes, str]:
    """
    Resize image data using PIL.

    Args:
        image_data: Original image bytes
        width: Target width (or None for auto)
        height: Target height (or None for auto)
        mode: Resize mode ('m' = fit, 'o' = exact, 'p' = pad)
        original_content_type: Content type of original image bytes

    Returns: (resized_bytes, content_type)
    """
    fallback_content_type = original_content_type or "image/jpeg"

    if not PIL_AVAILABLE:
        # Keep original bytes and media type when Pillow is unavailable.
        return image_data, fallback_content_type

    try:
        img = Image.open(io.BytesIO(image_data))

        # Convert to RGB if necessary (for JPEG output)
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')

        # Determine target size
        orig_width, orig_height = img.size

        if width is None and height is None:
            # No resize needed
            pass
        elif width is None:
            # Scale by height
            ratio = height / orig_height
            width = int(orig_width * ratio)
            img = img.resize((width, height), Image.Resampling.LANCZOS)
        elif height is None:
            # Scale by width
            ratio = width / orig_width
            height = int(orig_height * ratio)
            img = img.resize((width, height), Image.Resampling.LANCZOS)
        else:
            # Both dimensions specified
            if mode == 'm':
                # Fit within bounds, preserve aspect ratio
                img.thumbnail((width, height), Image.Resampling.LANCZOS)
            elif mode == 'p':
                # Fit and pad to fill
                img.thumbnail((width, height), Image.Resampling.LANCZOS)
                # Create padded image (black background)
                padded = Image.new('RGB', (width, height), (0, 0, 0))
                offset = ((width - img.size[0]) // 2, (height - img.size[1]) // 2)
                padded.paste(img, offset)
                img = padded
            else:
                # mode 'o' or default: resize to exact dimensions
                img = img.resize((width, height), Image.Resampling.LANCZOS)

        # Save to bytes
        output = io.BytesIO()
        img.save(output, format='JPEG', quality=85)
        return output.getvalue(), "image/jpeg"

    except Exception as e:
        logger.warning("Failed to resize image: %s", e)
        return image_data, fallback_content_type


@router.get("/music/{artwork_id}/cover_{spec}")
async def get_music_cover_with_spec(
    artwork_id: int,
    spec: str,
    request: Request,
) -> Response:
    """
    LMS-compatible cover endpoint with resize specification.

    Used by Squeezebox Radio, Touch, Controller, and JiveLite.

    URL format: /music/{id}/cover_{WxH}_{mode}_{bgcolor}.{ext}

    The ID can be:
    - album_id (primary - this is what we set in icon-id fields)
    - track_id (fallback for compatibility)

    LMS uses 'coverid' (8 hex chars hash) but we use numeric IDs.
    When we send icon-id="/music/{album_id}/cover", the client
    requests that URL, so we need to look up by album_id first.

    Examples:
        - /music/3/cover_41x41_m (Jive album list)
        - /music/3/cover_64x64_m (Fab4 album list)
        - /music/3/cover_100x100_o.jpg (Web UI)
    """
    if _artwork_manager is None or _music_library is None:
        raise HTTPException(status_code=503, detail="Artwork service not initialized")

    # Parse the spec
    width, height, mode, bgcolor, ext = _parse_cover_spec(spec)

    logger.debug(
        "Cover request: artwork_id=%d, spec=%s -> %dx%d mode=%s",
        artwork_id, spec, width or 0, height or 0, mode
    )

    db = _music_library._db
    track_path: str | None = None

    # Strategy 1: Try as album_id first (this is what we set in icon-id)
    # Get first track from this album to extract artwork
    try:
        rows = await db.list_tracks_by_album(album_id=artwork_id, offset=0, limit=1, order_by="album")
        if rows:
            row = rows[0]
            track_path = (
                getattr(row, "path", None)
                if hasattr(row, "path")
                else row.get("path")
                if isinstance(row, dict)
                else None
            )
            if track_path:
                logger.debug("Cover: found track via album_id=%d: %s", artwork_id, track_path)
    except Exception as e:
        logger.debug("Cover: album lookup failed for id=%d: %s", artwork_id, e)

    # Strategy 2: Fallback to track_id lookup
    if not track_path:
        try:
            row = await db.get_track_by_id(artwork_id)
            if row:
                track_path = (
                    getattr(row, "path", None)
                    if hasattr(row, "path")
                    else row.get("path")
                    if isinstance(row, dict)
                    else None
                )
                if track_path:
                    logger.debug("Cover: found track via track_id=%d: %s", artwork_id, track_path)
        except Exception as e:
            logger.debug("Cover: track lookup failed for id=%d: %s", artwork_id, e)

    if not track_path:
        raise HTTPException(status_code=404, detail="No track found for artwork")

    file_path = Path(track_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Track file not found")

    # Get artwork from manager (extracts or returns cached)
    try:
        result = await _artwork_manager.get_artwork(str(file_path))
        if result is None:
            raise HTTPException(status_code=404, detail="No artwork available")
        artwork_data, content_type, _etag = result
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("Failed to get artwork for id %d: %s", artwork_id, e)
        raise HTTPException(status_code=404, detail="No artwork available")

    # Resize if dimensions specified
    if width is not None or height is not None:
        artwork_data, content_type = _resize_image(
            artwork_data,
            width,
            height,
            mode,
            original_content_type=content_type,
        )

    # Generate ETag for caching (include spec in hash)
    etag = hashlib.md5(artwork_data + spec.encode()).hexdigest()

    # Check If-None-Match header
    if_none_match = request.headers.get("if-none-match")
    if if_none_match and if_none_match.strip('"') == etag:
        return Response(status_code=304)

    return Response(
        content=artwork_data,
        media_type=content_type,
        headers={
            "ETag": f'"{etag}"',
            "Cache-Control": "public, max-age=86400",  # Cache for 1 day
        },
    )


@router.get("/api/artwork/track/{track_id}/blurhash")
async def get_track_blurhash(track_id: int) -> dict[str, Any]:
    """
    Get BlurHash placeholder for a track's artwork.

    BlurHash is a compact string (20-30 characters) that can be decoded
    client-side to display a blurred placeholder while the full image loads.

    Returns:
        JSON with blurhash string or null if not available.
    """
    if _artwork_manager is None or _music_library is None:
        raise HTTPException(status_code=503, detail="Artwork service not initialized")

    # Get track path from database
    db = _music_library._db
    row = await db.get_track_by_id(track_id)

    if row is None:
        raise HTTPException(status_code=404, detail="Track not found")

    row_dict = to_dict(row)
    track_path = row_dict.get("path")

    if not track_path:
        return {"blurhash": None, "track_id": track_id}

    file_path = Path(track_path)
    if not file_path.exists():
        return {"blurhash": None, "track_id": track_id}

    # Get BlurHash from manager
    try:
        blurhash_str = await _artwork_manager.get_blurhash(str(file_path))
    except Exception as e:
        logger.debug("Failed to get BlurHash for track %d: %s", track_id, e)
        blurhash_str = None

    return {
        "blurhash": blurhash_str,
        "track_id": track_id,
    }


@router.get("/api/artwork/album/{album_id}/blurhash")
async def get_album_blurhash(album_id: int) -> dict[str, Any]:
    """
    Get BlurHash placeholder for an album's artwork.

    Uses the first track from the album to generate the BlurHash.

    Returns:
        JSON with blurhash string or null if not available.
    """
    if _artwork_manager is None or _music_library is None:
        raise HTTPException(status_code=503, detail="Artwork service not initialized")

    # Get first track from album
    db = _music_library._db
    rows = await db.list_tracks_by_album(album_id=album_id, offset=0, limit=1)

    if not rows:
        return {"blurhash": None, "album_id": album_id}

    row = rows[0]
    row_dict = to_dict(row)
    track_path = row_dict.get("path")

    if not track_path:
        return {"blurhash": None, "album_id": album_id}

    file_path = Path(track_path)
    if not file_path.exists():
        return {"blurhash": None, "album_id": album_id}

    # Get BlurHash from manager
    try:
        blurhash_str = await _artwork_manager.get_blurhash(str(file_path))
    except Exception as e:
        logger.debug("Failed to get BlurHash for album %d: %s", album_id, e)
        blurhash_str = None

    return {
        "blurhash": blurhash_str,
        "album_id": album_id,
    }


@router.get("/api/artwork/test")
async def test_artwork_status() -> dict[str, Any]:
    """Debug endpoint to check if ArtworkManager is alive."""
    if _artwork_manager is None:
        return {"status": "not_initialized", "available": False}

    cache_dir = _artwork_manager.cache_dir if hasattr(_artwork_manager, "cache_dir") else None
    blurhash_available = (
        _artwork_manager._blurhash_available
        if hasattr(_artwork_manager, "_blurhash_available")
        else False
    )

    return {
        "status": "ok",
        "available": True,
        "cache_dir": str(cache_dir) if cache_dir else None,
        "blurhash_available": blurhash_available,
    }
