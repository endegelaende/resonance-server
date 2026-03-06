"""
Artwork Routes for Resonance.

Provides endpoints for serving album artwork:
- /api/artwork/track/{track_id}: Get artwork for a specific track
- /api/artwork/album/{album_id}: Get artwork for a specific album
- /api/artwork/track/{track_id}/blurhash: Get BlurHash placeholder for a track
- /api/artwork/album/{album_id}/blurhash: Get BlurHash placeholder for an album
- /music/{id}/cover_{spec}: LMS-compatible resized cover art for Squeezebox devices
- /imageproxy/{url}/image{ext}: LMS-compatible proxy for external artwork URLs
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote

import httpx
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
logger.info("[IMAGEPROXY] PIL/Pillow available: %s", PIL_AVAILABLE)

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


# ---------------------------------------------------------------------------
# Image Proxy — LMS Slim::Web::ImageProxy equivalent
# ---------------------------------------------------------------------------
# LMS proxiedImage() (ImageProxy.pm L437-457) converts external artwork URLs
# to /imageproxy/<uri_escaped_url>/image.png so SqueezePlay/JiveLite devices
# fetch artwork from the server instead of reaching out to the internet
# directly (which many embedded players cannot do reliably).
#
# Usage in status responses:
#   proxied_url(url) → "/imageproxy/<escaped>/image.png"
# ---------------------------------------------------------------------------

# Shared httpx client for image proxy requests (lazy-initialized).
_imageproxy_client: httpx.AsyncClient | None = None

# Reasonable limits for proxied images.
_IMAGEPROXY_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
_IMAGEPROXY_TIMEOUT = 10.0  # seconds

# Content-type → extension mapping for validation.
_IMAGE_CONTENT_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "image/bmp": ".bmp",
}


def proxied_url(url: str | None) -> str | None:
    """Convert an external artwork URL to an /imageproxy/ server-local path.

    Mirrors LMS ``Slim::Web::ImageProxy::proxiedImage()`` (ImageProxy.pm L437-457).
    Returns *None* when *url* is falsy or already server-relative.
    """
    if not url or not url.startswith(("http://", "https://")):
        return None

    # Detect file extension (LMS defaults to .png).
    ext = ".png"
    import re as _re
    m = _re.search(r"\.(jpe?g|png|gif|webp)", url, _re.IGNORECASE)
    if m:
        ext = "." + m.group(1).lower()
        if ext == ".jpeg":
            ext = ".jpg"

    # URI-encode the URL so it's safe to embed in a path segment.
    from urllib.parse import quote
    encoded = quote(url, safe="")
    return f"/imageproxy/{encoded}/image{ext}"


async def _get_imageproxy_client() -> httpx.AsyncClient:
    """Return (and lazily create) the shared httpx client for image proxy."""
    global _imageproxy_client  # noqa: PLW0603
    if _imageproxy_client is None or _imageproxy_client.is_closed:
        _imageproxy_client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(_IMAGEPROXY_TIMEOUT),
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
            headers={"User-Agent": "Resonance/1.0 ImageProxy"},
        )
    return _imageproxy_client


# Resolve the static/html directory once.
from resonance._paths import static_html_dir as _get_static_html_dir

_STATIC_HTML_DIR = _get_static_html_dir()

# Radio placeholder image — loaded lazily on first fallback.
_RADIO_PLACEHOLDER_PATH = _STATIC_HTML_DIR / "images" / "radio.png"


def _radio_placeholder_response() -> Response | None:
    """Return a Response with the radio placeholder PNG, or *None*."""
    if _RADIO_PLACEHOLDER_PATH.is_file():
        return Response(
            content=_RADIO_PLACEHOLDER_PATH.read_bytes(),
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=86400"},
        )
    return None


@router.get("/imageproxy/{url:path}")
async def imageproxy(url: str, request: Request) -> Response:
    """LMS-compatible image proxy for external artwork URLs.

    SqueezePlay/JiveLite devices request artwork via:
        /imageproxy/<uri_escaped_url>/image.png

    This endpoint decodes the URL, fetches the image from the origin,
    and returns it with appropriate caching headers.

    When the upstream fetch fails (404, timeout, network error) we fall
    back to the radio placeholder image (``/html/images/radio.png``) so
    that SqueezePlay always gets *some* artwork instead of a broken icon.
    """
    logger.info("[IMAGEPROXY] >>> request path=%s", url[:120])

    # Strip trailing /image[_WxH_m].ext if present.
    # Standard LMS convention:  /image.png
    # JiveLite resize variant:  /image_300x300_m.png
    # Both need to be removed to recover the original upstream URL.
    clean_url = re.sub(r"/image(?:_\d+x\d+_\w)?(?:\.\w{2,4})?$", "", url)
    # URL-decode (the URL was percent-encoded by proxied_url / LMS proxiedImage).
    decoded_url = unquote(clean_url)

    logger.info("[IMAGEPROXY] decoded_url=%s", decoded_url[:200])

    if not decoded_url.startswith(("http://", "https://")):
        logger.warning("[IMAGEPROXY] rejected non-http URL: %s", decoded_url[:120])
        raise HTTPException(status_code=400, detail="Only http/https URLs supported")

    try:
        client = await _get_imageproxy_client()
        resp = await client.get(decoded_url)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.warning("[IMAGEPROXY] upstream HTTP error for %s: %s", decoded_url[:120], exc)
        fallback = _radio_placeholder_response()
        if fallback is not None:
            return fallback
        raise HTTPException(status_code=502, detail="Upstream image fetch failed") from exc
    except (httpx.RequestError, httpx.TimeoutException) as exc:
        logger.warning("[IMAGEPROXY] request failed for %s: %s", decoded_url[:120], exc)
        fallback = _radio_placeholder_response()
        if fallback is not None:
            return fallback
        raise HTTPException(status_code=502, detail="Upstream image fetch failed") from exc

    content = resp.content
    if len(content) > _IMAGEPROXY_MAX_BYTES:
        raise HTTPException(status_code=502, detail="Image too large")

    # Determine content type from upstream response.
    content_type = resp.headers.get("content-type", "image/png").split(";")[0].strip()
    if not content_type.startswith("image/"):
        content_type = "image/png"

    # ── Convert non-standard formats to PNG for JiveLite/SDL_image ───
    # JiveLite uses SDL_image which cannot load .ico (Windows icon) and
    # some other exotic formats.  RadioBrowser stations frequently have
    # favicon.ico as their only artwork.  Convert anything that isn't
    # a standard web image format (JPEG/PNG/GIF/WebP) to PNG so the
    # Squeezebox hardware can display it.
    _STANDARD_TYPES = {
        "image/jpeg", "image/png", "image/gif", "image/webp",
    }
    if content_type not in _STANDARD_TYPES and PIL_AVAILABLE:
        try:
            img = Image.open(io.BytesIO(content))
            img = img.convert("RGBA")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            content = buf.getvalue()
            content_type = "image/png"
            logger.info(
                "[IMAGEPROXY] converted %s -> PNG (%d bytes) for %s",
                resp.headers.get("content-type", "?"), len(content), decoded_url[:80],
            )
        except Exception as exc:
            logger.warning(
                "[IMAGEPROXY] failed to convert %s to PNG for %s: %s",
                resp.headers.get("content-type", "?"), decoded_url[:80], exc,
            )
            # Fall through and return the original content — better than nothing.

    logger.info(
        "[IMAGEPROXY] <<< serving %s %d bytes as %s for %s",
        "converted" if content_type == "image/png" and resp.headers.get("content-type", "").split(";")[0].strip() != "image/png" else "original",
        len(content), content_type, decoded_url[:100],
    )

    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Cache-Control": "public, max-age=86400",
            "X-Imageproxy-Source": decoded_url[:200],
        },
    )


# ---------------------------------------------------------------------------
# Static image serving with resize-suffix stripping
# ---------------------------------------------------------------------------
# JiveLite's fetchArtwork() (SlimServer.lua L1170-1172) appends a resize
# suffix to path-based icon-ids:
#   /html/images/radio.png  →  /html/images/radio_300x300_m.png
#
# LMS's web server (Slim::Web::HTTP) handles this transparently by resizing
# on the fly.  We don't need actual resizing for placeholder images — just
# strip the suffix and serve the original file.
#
# This route MUST be registered before the StaticFiles mount at /html/ so
# FastAPI's router matches it first.
# ---------------------------------------------------------------------------

# Pattern: {stem}_{WxH}_{mode}.{ext}  e.g. radio_300x300_m.png
_RESIZE_SUFFIX_RE = re.compile(r"^(.+?)_\d+x\d+_\w(?:_[0-9a-fA-F]+)?(\.\w+)$")

@router.get("/html/images/{filename:path}")
async def serve_static_image_with_resize(filename: str) -> Response:
    """Serve static images from /html/images/, stripping JiveLite resize suffixes.

    JiveLite requests e.g. ``/html/images/radio_300x300_m.png`` but we only
    have ``radio.png``.  Strip the ``_WxH_m`` suffix and serve the original.
    """
    images_dir = _STATIC_HTML_DIR / "images"

    # Try the literal filename first (no suffix stripping needed).
    candidate = images_dir / filename
    if candidate.is_file():
        ext = candidate.suffix.lower()
        ct = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
              ".gif": "image/gif", ".webp": "image/webp"}.get(ext, "image/png")
        return Response(
            content=candidate.read_bytes(),
            media_type=ct,
            headers={"Cache-Control": "public, max-age=86400"},
        )

    # Strip resize suffix and retry.
    m = _RESIZE_SUFFIX_RE.match(filename)
    if m:
        original = images_dir / (m.group(1) + m.group(2))
        if original.is_file():
            ext = original.suffix.lower()
            ct = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                  ".gif": "image/gif", ".webp": "image/webp"}.get(ext, "image/png")
            logger.debug("Resize-suffix strip: %s → %s", filename, original.name)
            return Response(
                content=original.read_bytes(),
                media_type=ct,
                headers={"Cache-Control": "public, max-age=86400"},
            )

    raise HTTPException(status_code=404, detail="Image not found")


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
