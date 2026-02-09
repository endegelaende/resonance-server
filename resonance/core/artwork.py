import asyncio
import hashlib
import io
import logging
from pathlib import Path
from typing import Any, Optional

from mutagen import File as mutagen_file
from mutagen.flac import FLAC
from mutagen.id3 import ID3
from mutagen.mp4 import MP4, MP4Cover

logger = logging.getLogger(__name__)

# Limit concurrent cache writes to prevent task explosion under load
_MAX_CONCURRENT_WRITES = 4

# BlurHash configuration
BLURHASH_X_COMPONENTS = 4
BLURHASH_Y_COMPONENTS = 3
BLURHASH_THUMBNAIL_SIZE = 32  # Resize to this before encoding for speed


class ArtworkManager:
    """
    Manages extraction and caching of cover art from audio files.

    This manager handles:
    1. Extracting binary image data from various audio formats (ID3, MP4, FLAC, Vorbis).
    2. Caching extracted artwork to avoid repeated expensive extraction.
    3. Providing a consistent interface for the web server to serve images.
    4. ETag generation for HTTP caching.
    5. BlurHash generation for instant placeholders.

    Cache invalidation:
    - Cache keys include path + mtime_ns + file_size to detect file changes.
    - When a file is modified, a new cache entry is created automatically.

    Cache files per artwork:
    - {key}.data: Raw image bytes
    - {key}.mime: MIME type string
    - {key}.blurhash: BlurHash string (compact placeholder)
    """

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._write_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_WRITES)
        self._pending_writes: set[asyncio.Task[Any]] = set()
        self._blurhash_available = self._check_blurhash_available()

    def _check_blurhash_available(self) -> bool:
        """Check if blurhash and PIL are available."""
        try:
            import blurhash
            from PIL import Image

            return True
        except ImportError:
            logger.warning(
                "blurhash-python or Pillow not installed. "
                "BlurHash placeholders will not be available. "
                "Install with: pip install blurhash-python pillow"
            )
            return False

    def _compute_cache_key(self, path: Path, mtime_ns: int, size: int) -> str:
        """
        Compute a cache key that invalidates when the file changes.

        Uses path + mtime_ns + size to ensure cache freshness.
        """
        key_data = f"{path.absolute()}|{mtime_ns}|{size}"
        return hashlib.sha256(key_data.encode()).hexdigest()

    def compute_etag(self, path: Path, mtime_ns: int, size: int) -> str:
        """
        Compute an ETag for HTTP caching.

        The ETag is a shorter hash suitable for HTTP headers.
        """
        key_data = f"{path.absolute()}|{mtime_ns}|{size}"
        return hashlib.md5(key_data.encode()).hexdigest()

    def _generate_blurhash(self, image_data: bytes) -> str | None:
        """
        Generate a BlurHash string from image data.

        BlurHash is a compact representation of a placeholder for an image.
        Typically 20-30 characters that can be decoded client-side to a
        blurred preview image.

        Returns:
            BlurHash string or None if generation fails.
        """
        if not self._blurhash_available:
            return None

        try:
            import blurhash
            from PIL import Image

            # Open image and convert to RGB (BlurHash requires RGB)
            img = Image.open(io.BytesIO(image_data))
            img = img.convert("RGB")

            # Resize to small thumbnail for faster encoding
            # BlurHash doesn't need high resolution - it's a placeholder
            img.thumbnail((BLURHASH_THUMBNAIL_SIZE, BLURHASH_THUMBNAIL_SIZE))

            # Encode to BlurHash
            hash_str = blurhash.encode(
                img,
                x_components=BLURHASH_X_COMPONENTS,
                y_components=BLURHASH_Y_COMPONENTS,
            )
            return hash_str

        except Exception as e:
            logger.debug("BlurHash generation failed: %s", e)
            return None

    async def get_artwork(self, track_path: str) -> tuple[bytes, str, str] | None:
        """
        Get artwork for a track, either from cache or by extracting it.

        Returns:
            Tuple of (image_bytes, mime_type, etag) or None if no artwork found.
        """
        path = Path(track_path)
        if not path.exists():
            return None

        try:
            stat = path.stat()
            mtime_ns = stat.st_mtime_ns
            size = stat.st_size
        except OSError as e:
            logger.warning("Failed to stat %s: %s", path, e)
            return None

        cache_key = self._compute_cache_key(path, mtime_ns, size)
        etag = self.compute_etag(path, mtime_ns, size)
        cache_file = self.cache_dir / f"{cache_key}.data"
        mime_file = self.cache_dir / f"{cache_key}.mime"

        # Check cache
        if cache_file.exists() and mime_file.exists():
            try:
                data = await asyncio.to_thread(cache_file.read_bytes)
                mime = await asyncio.to_thread(mime_file.read_text)
                return data, mime.strip(), etag
            except Exception as e:
                logger.warning("Failed to read artwork cache for %s: %s", path, e)

        # Extract from file
        result = await asyncio.to_thread(self._extract_from_file, path)
        if result:
            data, mime = result
            # Update cache with bounded concurrency (also generates BlurHash)
            self._schedule_cache_write(cache_key, data, mime)
            return data, mime, etag

        return None

    async def get_blurhash_if_cached(self, track_path: str) -> str | None:
        """
        Fast path: return BlurHash ONLY if it's already cached.

        This must be cheap and must NOT trigger artwork extraction or BlurHash
        generation. It exists to keep latency-sensitive endpoints (e.g. JSON-RPC
        `status`) responsive under load and during seeks.

        Returns:
            Cached BlurHash string or None if not available.
        """
        if not self._blurhash_available:
            return None

        path = Path(track_path)
        if not path.exists():
            return None

        try:
            stat = path.stat()
            mtime_ns = stat.st_mtime_ns
            size = stat.st_size
        except OSError as e:
            logger.warning("Failed to stat %s: %s", path, e)
            return None

        cache_key = self._compute_cache_key(path, mtime_ns, size)
        blurhash_file = self.cache_dir / f"{cache_key}.blurhash"

        if not blurhash_file.exists():
            return None

        try:
            blurhash_str = await asyncio.to_thread(blurhash_file.read_text)
            return blurhash_str.strip()
        except Exception as e:
            logger.debug("Failed to read BlurHash cache for %s: %s", path, e)
            return None

    async def get_blurhash(self, track_path: str) -> str | None:
        """
        Get BlurHash for a track's artwork.

        Returns cached BlurHash if available, otherwise extracts artwork
        and generates BlurHash on-demand.

        Returns:
            BlurHash string or None if no artwork or generation fails.
        """
        if not self._blurhash_available:
            return None

        path = Path(track_path)
        if not path.exists():
            return None

        try:
            stat = path.stat()
            mtime_ns = stat.st_mtime_ns
            size = stat.st_size
        except OSError as e:
            logger.warning("Failed to stat %s: %s", path, e)
            return None

        cache_key = self._compute_cache_key(path, mtime_ns, size)
        blurhash_file = self.cache_dir / f"{cache_key}.blurhash"

        # Check cache for existing BlurHash
        if blurhash_file.exists():
            try:
                blurhash_str = await asyncio.to_thread(blurhash_file.read_text)
                return blurhash_str.strip()
            except Exception as e:
                logger.debug("Failed to read BlurHash cache for %s: %s", path, e)

        # Need to get artwork first to generate BlurHash
        artwork_result = await self.get_artwork(track_path)
        if not artwork_result:
            return None

        data, _mime, _etag = artwork_result

        # Generate BlurHash
        blurhash_str = await asyncio.to_thread(self._generate_blurhash, data)

        if blurhash_str:
            # Cache the BlurHash
            try:
                await asyncio.to_thread(blurhash_file.write_text, blurhash_str)
            except Exception as e:
                logger.debug("Failed to cache BlurHash for %s: %s", path, e)

        return blurhash_str

    def _schedule_cache_write(self, key: str, data: bytes, mime: str) -> None:
        """Schedule a cache write with bounded concurrency."""
        task = asyncio.create_task(self._bounded_cache_write(key, data, mime))
        self._pending_writes.add(task)
        task.add_done_callback(self._pending_writes.discard)

    async def _bounded_cache_write(self, key: str, data: bytes, mime: str) -> None:
        """Write to cache with semaphore to limit concurrent writes."""
        async with self._write_semaphore:
            await self._update_cache(key, data, mime)

    async def _update_cache(self, key: str, data: bytes, mime: str) -> None:
        """Write extraction result to cache, including BlurHash."""
        try:
            cache_file = self.cache_dir / f"{key}.data"
            mime_file = self.cache_dir / f"{key}.mime"
            blurhash_file = self.cache_dir / f"{key}.blurhash"

            await asyncio.to_thread(cache_file.write_bytes, data)
            await asyncio.to_thread(mime_file.write_text, mime)

            # Generate and cache BlurHash
            if self._blurhash_available:
                blurhash_str = await asyncio.to_thread(self._generate_blurhash, data)
                if blurhash_str:
                    await asyncio.to_thread(blurhash_file.write_text, blurhash_str)

        except Exception as e:
            logger.error("Failed to write artwork cache for key %s: %s", key, e)

    async def shutdown(self) -> None:
        """Wait for pending cache writes to complete during shutdown."""
        if self._pending_writes:
            logger.info("Waiting for %d pending artwork cache writes...", len(self._pending_writes))
            await asyncio.gather(*self._pending_writes, return_exceptions=True)

    def _extract_from_file(self, path: Path) -> tuple[bytes, str] | None:
        """Synchronous extraction using mutagen."""
        try:
            audio = mutagen_file(path)
            if audio is None:
                return None

            # 1. MP4 (m4a, m4b) - use MP4Cover.imageformat for reliable MIME detection
            if isinstance(audio, MP4):
                if audio.tags and "covr" in audio.tags:
                    covers = audio.tags["covr"]
                    if covers:
                        cover = covers[0]
                        data = bytes(cover)
                        # Use MP4Cover's imageformat attribute if available
                        if isinstance(cover, MP4Cover):
                            if cover.imageformat == MP4Cover.FORMAT_PNG:
                                mime = "image/png"
                            elif cover.imageformat == MP4Cover.FORMAT_JPEG:
                                mime = "image/jpeg"
                            else:
                                # Fallback to magic bytes
                                mime = self._detect_mime_from_magic(data)
                        else:
                            mime = self._detect_mime_from_magic(data)
                        return data, mime

            # 2. FLAC - has proper MIME in picture metadata
            elif isinstance(audio, FLAC):
                if audio.pictures:
                    pic = audio.pictures[0]
                    return pic.data, pic.mime

            # 3. ID3 (mp3) - has proper MIME in APIC frame
            elif isinstance(audio, ID3) or (hasattr(audio, "tags") and isinstance(audio.tags, ID3)):
                tags = audio if isinstance(audio, ID3) else audio.tags
                if tags:
                    apic_frames = tags.getall("APIC")
                    if apic_frames:
                        # Prefer front cover (type 3) if available
                        cover = next((f for f in apic_frames if f.type == 3), apic_frames[0])
                        return cover.data, cover.mime

            # 4. Vorbis (ogg, opus) - uses METADATA_BLOCK_PICTURE (base64)
            elif hasattr(audio, "tags") and audio.tags:
                for key in ["metadata_block_picture", "METADATA_BLOCK_PICTURE"]:
                    if key in audio.tags:
                        import base64

                        from mutagen.flac import Picture

                        try:
                            b64_data = audio.tags[key][0]
                            raw_data = base64.b64decode(b64_data)
                            pic = Picture(raw_data)
                            return pic.data, pic.mime
                        except Exception:
                            continue

        except Exception as e:
            logger.debug("Artwork extraction failed for %s: %s", path, e)

        return None

    @staticmethod
    def _detect_mime_from_magic(data: bytes) -> str:
        """Fallback MIME detection via magic bytes."""
        if data.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        elif data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        elif data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
            return "image/gif"
        elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            return "image/webp"
        else:
            # Default to JPEG as it's most common for album art
            return "image/jpeg"
