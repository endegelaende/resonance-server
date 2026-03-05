"""
JSON-RPC Helper Functions.

This module provides utilities for JSON-RPC command processing:
- Query parameter parsing (start, itemsPerResponse, tags, filters)
- Loop item building (converting DB results to LMS-format response items)
- Sort mapping (LMS sort parameters to SQL ORDER BY)
"""

from __future__ import annotations

import logging
from dataclasses import asdict, is_dataclass
from typing import Any

logger = logging.getLogger(__name__)


def to_dict(row: Any) -> dict[str, Any]:
    """
    Convert a row (dict or dataclass) to a dictionary.

    Args:
        row: Either a dict or a dataclass instance

    Returns:
        Dictionary representation
    """
    if isinstance(row, dict):
        return row
    if is_dataclass(row) and not isinstance(row, type):
        return asdict(row)
    # Try to convert via __dict__ as fallback
    if hasattr(row, "__dict__"):
        return dict(row.__dict__)
    # Last resort: try dict()
    try:
        return dict(row)
    except (TypeError, ValueError):
        return {}


# =============================================================================
# Query Parameter Parsing
# =============================================================================


# Maximum items a single list query may return (input-validation hardening)
_MAX_QUERY_ITEMS = 10_000

# Maximum sane paging start index
_MAX_QUERY_START = 1_000_000


def parse_start_items(params: list[Any]) -> tuple[int, int]:
    """
    Parse start index and items per response from command params.

    LMS commands use positional params: [command, start, items, ...]

    Values are clamped to sane ranges to prevent abuse:
    - *start* is clamped to ``[0, 1_000_000]``
    - *items* is clamped to ``[0, 10_000]``

    Args:
        params: Command parameters list

    Returns:
        Tuple of (start_index, items_per_response)
    """
    start = 0
    items = 100  # Default

    if len(params) >= 2:
        try:
            start = int(params[1])
        except (ValueError, TypeError):
            pass

    if len(params) >= 3:
        try:
            items = int(params[2])
        except (ValueError, TypeError):
            pass

    # Clamp to safe ranges (input-validation hardening §14.3)
    if start < 0:
        start = 0
    elif start > _MAX_QUERY_START:
        start = _MAX_QUERY_START
    if items < 0:
        items = 0
    elif items > _MAX_QUERY_ITEMS:
        items = _MAX_QUERY_ITEMS

    return start, items


def parse_tagged_params(params: list[Any]) -> dict[str, str]:
    """
    Parse tagged parameters from command params.

    LMS uses "tag:value" format for filters and options.
    Examples: "tags:als", "year:2020", "genre_id:5", "artist_id:10"

    Some clients (Cometd) send ``dict`` objects instead of colon-separated
    strings.  Both formats are supported transparently.

    Args:
        params: Command parameters list

    Returns:
        Dictionary of tag -> value
    """
    result: dict[str, str] = {}

    for param in params:
        # Handle dict elements sent by Cometd clients
        if isinstance(param, dict):
            for k, v in param.items():
                if v is not None:
                    result[str(k)] = str(v)
            continue

        if not isinstance(param, str):
            continue
        if ":" not in param:
            continue

        # Split on first colon only
        parts = param.split(":", 1)
        if len(parts) == 2:
            tag, value = parts
            result[tag] = value

    return result


def parse_start_count(
    command: list[Any], sub_offset: int = 2
) -> tuple[int, int]:
    """Extract ``(start, count)`` from positional args after a sub-command.

    Plugin commands typically look like ``["plugin", "items", 0, 100, …]``
    where the pagination integers follow the sub-command at *sub_offset*.

    Values are clamped to sane ranges (same limits as
    :func:`parse_start_items`):

    - *start* is clamped to ``[0, 1_000_000]``
    - *count* is clamped to ``[0, 10_000]``

    Args:
        command: Full command array (e.g. ``["favorites", "items", 0, 100]``).
        sub_offset: Index of the first pagination integer (default ``2``).

    Returns:
        Tuple of ``(start, count)``.  Defaults to ``(0, 200)`` when the
        positional args are missing or unparseable.
    """
    start = 0
    count = 200

    if len(command) > sub_offset:
        try:
            start = int(command[sub_offset])
        except (ValueError, TypeError):
            pass

    if len(command) > sub_offset + 1:
        try:
            count = int(command[sub_offset + 1])
        except (ValueError, TypeError):
            pass

    # Clamp to safe ranges (same constants as parse_start_items)
    if start < 0:
        start = 0
    elif start > _MAX_QUERY_START:
        start = _MAX_QUERY_START
    if count < 0:
        count = 0
    elif count > _MAX_QUERY_ITEMS:
        count = _MAX_QUERY_ITEMS

    return start, count


def parse_tags_string(tags_str: str) -> set[str]:
    """
    Parse a tags string into a set of single-character tags.

    LMS uses a string of characters to indicate which fields to include.
    Example: "als" means include 'a' (artist), 'l' (album), 's' (something)

    Args:
        tags_str: String of tag characters

    Returns:
        Set of single-character tags
    """
    return set(tags_str)


def get_filter_int(tagged_params: dict[str, str], key: str) -> int | None:
    """
    Get an integer filter value from tagged params.

    Args:
        tagged_params: Parsed tagged parameters
        key: The key to look up (e.g., "year", "genre_id")

    Returns:
        Integer value or None if not present/invalid
    """
    value = tagged_params.get(key)
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def get_filter_str(tagged_params: dict[str, str], key: str) -> str | None:
    """
    Get a string filter value from tagged params.

    Args:
        tagged_params: Parsed tagged parameters
        key: The key to look up

    Returns:
        String value or None if not present
    """
    return tagged_params.get(key)


# =============================================================================
# Sort Mapping
# =============================================================================

# Maps LMS sort parameter values to (column, ascending) tuples
ARTIST_SORT_MAP: dict[str, tuple[str, bool]] = {
    "artist": ("name", True),
    "artistsort": ("name_sort", True),
    "id": ("id", True),
    "albums": ("album_count", False),  # Most albums first
    "tracks": ("track_count", False),  # Most tracks first
}

ALBUM_SORT_MAP: dict[str, tuple[str, bool]] = {
    "album": ("title", True),
    "albumsort": ("title_sort", True),
    "id": ("id", True),
    "year": ("year", False),  # Newest first
    "artist": ("artist_name", True),
    "artflow": ("artist_name", True),  # LMS alias
    "new": ("id", False),  # Recently added (approximated by ID)
}

TRACK_SORT_MAP: dict[str, tuple[str, bool]] = {
    "title": ("title", True),
    "titlesort": ("title_sort", True),
    "id": ("id", True),
    "album": ("album_title", True),
    "artist": ("artist_name", True),
    "year": ("year", False),
    "tracknum": ("track_no", True),
    "duration": ("duration_ms", True),
    "new": ("id", False),
}


def get_sort_params(
    tagged_params: dict[str, str],
    sort_map: dict[str, tuple[str, bool]],
    default_column: str = "name",
) -> tuple[str, bool]:
    """
    Get sort column and direction from tagged params.

    Args:
        tagged_params: Parsed tagged parameters
        sort_map: Mapping of sort names to (column, ascending)
        default_column: Default column if no sort specified

    Returns:
        Tuple of (column_name, ascending)
    """
    sort_key = tagged_params.get("sort", "").lower()

    if sort_key in sort_map:
        return sort_map[sort_key]

    return default_column, True


# =============================================================================
# Loop Item Builders
# =============================================================================

# Tag character to field name mappings for LMS compatibility
ARTIST_TAG_MAP: dict[str, str] = {
    "s": "id",  # Standard LMS uses 's' for artist
    "a": "artist",  # Artist name
    "textkey": "textkey",  # First letter for indexing
}

ALBUM_TAG_MAP: dict[str, str] = {
    "l": "album",  # Album title
    "a": "artist",  # Artist name
    "y": "year",  # Year
    "S": "artist_id",  # Artist ID
    "j": "artwork_track_id",  # Artwork reference
    "id": "id",  # Album ID
    "e": "album_id",  # LMS uses 'e' for album_id in some contexts
    "X": "album_replay_gain",  # Replay gain
}

TRACK_TAG_MAP: dict[str, str] = {
    "t": "title",  # Track title
    "a": "artist",  # Artist name
    "l": "album",  # Album name
    "y": "year",  # Year
    "d": "duration",  # Duration in seconds
    "n": "tracknum",  # Track number
    "i": "disc",  # Disc number
    "u": "url",  # Track URL for streaming
    "o": "type",  # Content type (format)
    "r": "bitrate",  # Bitrate
    "T": "samplerate",  # Sample rate
    "I": "samplesize",  # Bit depth (sample size)
    "id": "id",  # Track ID
    "s": "artist_id",  # Artist ID
    "e": "album_id",  # Album ID
    "j": "coverart",  # Cover art available
    "J": "artwork_track_id",  # Artwork track ID
    "K": "artwork_url",  # Artwork URL
    "c": "coverid",  # Cover ID
}

GENRE_TAG_MAP: dict[str, str] = {
    "id": "id",
    "genre": "genre",
}

ROLE_TAG_MAP: dict[str, str] = {
    "id": "id",
    "role": "role",
}


def build_artist_item(
    row: Any,
    tags: set[str] | None = None,
    include_all: bool = False,
) -> dict[str, Any]:
    """
    Build an artist loop item from a database row.

    Args:
        row: Database result row (dict or dataclass)
        tags: Set of tag characters to include (None = all)
        include_all: If True, include all fields regardless of tags

    Returns:
        LMS-format artist item
    """
    row_dict = to_dict(row)
    item: dict[str, Any] = {}

    # Always include id
    item["id"] = row_dict.get("id")

    # Name is always included as "artist"
    item["artist"] = row_dict.get("name", row_dict.get("artist", ""))

    if include_all or tags is None or "s" in tags:
        # 's' is the standard tag for artists
        pass  # id already included

    if include_all or tags is None:
        if row_dict.get("album_count") is not None:
            item["albums"] = row_dict["album_count"]
        if row_dict.get("track_count") is not None:
            item["track_count"] = row_dict["track_count"]

    # Generate textkey (first letter for indexing)
    name = row_dict.get("name", row_dict.get("artist", ""))
    if name:
        item["textkey"] = name[0].upper()

    return item


def build_album_item(
    row: Any,
    tags: set[str] | None = None,
    include_all: bool = False,
    server_url: str = "",
) -> dict[str, Any]:
    """
    Build an album loop item from a database row.

    Args:
        row: Database result row (dict or dataclass)
        tags: Set of tag characters to include (None = all)
        include_all: If True, include all fields regardless of tags
        server_url: Base URL for artwork

    Returns:
        LMS-format album item
    """
    row_dict = to_dict(row)
    item: dict[str, Any] = {}

    # Always include id and album title
    # DB queries return 'name' for album title in some cases, 'title' in others
    item["id"] = row_dict.get("id")
    item["album"] = row_dict.get("title", row_dict.get("name", row_dict.get("album", "")))

    if include_all or tags is None or "a" in tags:
        # DB queries return 'artist' in some dict results, 'artist_name' in others
        artist = row_dict.get("artist_name", row_dict.get("artist"))
        if artist:
            item["artist"] = artist

    if include_all or tags is None or "y" in tags:
        year = row_dict.get("year")
        if year:
            item["year"] = year

    if include_all or tags is None or "S" in tags:
        artist_id = row_dict.get("artist_id")
        if artist_id:
            item["artist_id"] = artist_id

    if include_all or tags is None:
        track_count = row_dict.get("track_count")
        if track_count is not None:
            item["tracks"] = track_count

    # Artwork
    if include_all or tags is None or "j" in tags or "J" in tags:
        album_id = row_dict.get("id")
        if album_id and server_url:
            item["artwork_track_id"] = album_id
            item["artwork_url"] = f"{server_url}/artwork/{album_id}"

    # Generate textkey
    title = row_dict.get("title", row_dict.get("album", ""))
    if title:
        item["textkey"] = title[0].upper()

    return item


def build_track_item(
    row: Any,
    tags: set[str] | None = None,
    include_all: bool = False,
    server_url: str = "",
) -> dict[str, Any]:
    """
    Build a track/title loop item from a database row.

    Args:
        row: Database result row (dict or dataclass)
        tags: Set of tag characters to include (None = all)
        include_all: If True, include all fields regardless of tags
        server_url: Base URL for streaming and artwork

    Returns:
        LMS-format track item
    """
    row_dict = to_dict(row)
    item: dict[str, Any] = {}

    # Always include id and title
    item["id"] = row_dict.get("id")
    item["title"] = row_dict.get("title", "")

    if include_all or tags is None or "a" in tags:
        artist = row_dict.get("artist_name", row_dict.get("artist"))
        if artist:
            item["artist"] = artist

    if include_all or tags is None or "l" in tags:
        album = row_dict.get("album_title", row_dict.get("album"))
        if album:
            item["album"] = album

    if include_all or tags is None or "y" in tags:
        year = row_dict.get("year")
        if year:
            item["year"] = year

    if include_all or tags is None or "d" in tags:
        duration_ms = row_dict.get("duration_ms")
        if duration_ms is not None:
            item["duration"] = duration_ms / 1000.0  # Convert to seconds

    if include_all or tags is None or "n" in tags:
        track_no = row_dict.get("track_no")
        if track_no is not None:
            item["tracknum"] = track_no

    if include_all or tags is None or "i" in tags:
        disc_no = row_dict.get("disc_no")
        if disc_no is not None:
            item["disc"] = disc_no

    if include_all or tags is None or "s" in tags:
        artist_id = row_dict.get("artist_id")
        if artist_id is not None:
            item["artist_id"] = artist_id

    if include_all or tags is None or "e" in tags:
        album_id = row_dict.get("album_id")
        if album_id is not None:
            item["album_id"] = album_id

    # URL is ALWAYS included when available (needed for playback)
    # Note: 'u' tag controls inclusion, but url is critical for playback
    path = row_dict.get("path")
    if path:
        item["url"] = path  # Use actual path for LMS compat

    # Audio quality metadata
    if include_all or tags is None or "o" in tags:
        path = row_dict.get("path", "")
        if path:
            # Determine format from file extension
            ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
            format_map = {
                "mp3": "mp3",
                "flac": "flc",
                "ogg": "ogg",
                "m4a": "aac",
                "m4b": "aac",
                "wav": "wav",
                "aiff": "aif",
                "aif": "aif",
                "opus": "ops",
            }
            item["type"] = format_map.get(ext, ext)

    if include_all or tags is None or "r" in tags:
        bitrate = row_dict.get("bitrate")
        if bitrate:
            item["bitrate"] = bitrate

    if include_all or tags is None or "T" in tags:
        sample_rate = row_dict.get("sample_rate")
        if sample_rate:
            item["samplerate"] = sample_rate

    if include_all or tags is None or "I" in tags:
        bit_depth = row_dict.get("bit_depth")
        if bit_depth:
            item["samplesize"] = bit_depth

    # Channels
    channels = row_dict.get("channels")
    if channels:
        item["channels"] = channels

    # Artwork
    if include_all or tags is None or "j" in tags or "J" in tags or "K" in tags:
        album_id = row_dict.get("album_id")
        if album_id and server_url:
            item["coverart"] = 1
            item["artwork_track_id"] = row_dict.get("id")
            item["artwork_url"] = f"{server_url}/artwork/{album_id}"

    # Generate textkey
    title = row_dict.get("title", "")
    if title:
        item["textkey"] = title[0].upper()

    return item


def build_genre_item(
    row: Any,
    tags: set[str] | None = None,
    include_all: bool = False,
) -> dict[str, Any]:
    """
    Build a genre loop item from a database row.

    Args:
        row: Database result row (dict or dataclass)
        tags: Set of tag characters to include (None = all)
        include_all: If True, include all fields regardless of tags

    Returns:
        LMS-format genre item
    """
    row_dict = to_dict(row)
    item: dict[str, Any] = {}

    # Always include id
    item["id"] = row_dict.get("id")

    # Genre name is included unless tags gating excludes it
    # tags:i means only id, so we check if tags is set and doesn't include genre indicators
    if include_all or tags is None or "g" in tags:
        item["genre"] = row_dict.get("name", row_dict.get("genre", ""))

    if include_all or tags is None:
        track_count = row_dict.get("track_count")
        if track_count is not None:
            item["tracks"] = track_count

    return item


def build_role_item(
    row: Any,
    tags: set[str] | None = None,
    include_all: bool = False,
) -> dict[str, Any]:
    """
    Build a role loop item from a database row.

    Args:
        row: Database result row (dict or dataclass)
        tags: Set of tag characters to include (None = all)
        include_all: If True, include all fields regardless of tags

    Returns:
        LMS-format role item
    """
    row_dict = to_dict(row)
    item: dict[str, Any] = {}

    # Always include id as role_id for LMS compatibility
    item["role_id"] = row_dict.get("id")

    # Include role_name if tags include 't' (text/title) or no tags specified
    if include_all or tags is None or "t" in tags:
        item["role_name"] = row_dict.get("name", row_dict.get("role", ""))

    return item


NON_AUDIO_PLAYER_MODELS = frozenset({"controller"})

NIL_LIKE_PLAYER_NAMES = frozenset({"nil", "null", "none", "undefined", "(null)"})

PLAYER_MODEL_LABELS: dict[str, str] = {
    "slimp3": "SliMP3",
    "squeezebox": "Squeezebox",
    "squeezebox2": "Squeezebox 2",
    "softsqueeze": "SoftSqueeze",
    "softsqueeze3": "SoftSqueeze 3",
    "transporter": "Transporter",
    "receiver": "Squeezebox Receiver",
    "squeezeslave": "SqueezeSlave",
    "controller": "Squeezebox Controller",
    "boom": "Squeezebox Boom",
    "softboom": "SoftBoom",
    "squeezeplay": "SqueezePlay",
    "baby": "Squeezebox Radio",
    "fab4": "Squeezebox Touch",
    "squeezelite": "Squeezelite",
}


def _player_capability(player: Any, key: str) -> str:
    """Read a single HELO capability value from a player-like object."""
    player_info = getattr(player, "info", None)
    if player_info is None:
        return ""

    capabilities = getattr(player_info, "capabilities", None)
    if not isinstance(capabilities, dict):
        return ""

    value = capabilities.get(key)
    if not isinstance(value, str):
        return ""

    return value.strip()


def _player_model_label(model_name: str) -> str:
    """Return a human-friendly label for a player model identifier."""
    normalized = str(model_name or "").strip().lower()
    if not normalized:
        return ""

    mapped = PLAYER_MODEL_LABELS.get(normalized)
    if mapped:
        return mapped

    return normalized.replace("_", " ").replace("-", " ").title()


def _resolve_player_model(player: Any) -> str:
    """Resolve the LMS model/displaytype name for a player-like object."""
    capability_model = _player_capability(player, "Model")
    if capability_model:
        return capability_model.lower()

    player_info = getattr(player, "info", None)
    if player_info is not None:
        model_name = getattr(player_info, "model", None)
        if isinstance(model_name, str) and model_name:
            return model_name.lower()

        device_type = getattr(player_info, "device_type", None)
        if device_type is not None:
            model_name = getattr(device_type, "name", None)
            if isinstance(model_name, str) and model_name:
                return model_name.lower()

    raw_device_type = getattr(player, "device_type", None)
    if callable(raw_device_type):
        try:
            raw_device_type = raw_device_type()
        except TypeError:
            raw_device_type = None
    if isinstance(raw_device_type, str) and raw_device_type:
        return raw_device_type.lower()

    model_name = getattr(player, "model", None)
    if isinstance(model_name, str) and model_name:
        return model_name.lower()

    return "squeezebox"


def is_audio_player(player: Any) -> bool:
    """
    Return whether this client should be treated as an audio playback player.

    Controller-class clients (for example Squeezebox Controller) may speak parts
    of Slimproto but do not render audio and should not be auto-selected for
    playback controls in UI flows.
    """
    explicit = getattr(player, "is_player", None)
    if explicit is not None:
        try:
            explicit_value = explicit() if callable(explicit) else explicit
            return bool(explicit_value)
        except TypeError:
            pass

    legacy = getattr(player, "isPlayer", None)
    if legacy is not None:
        try:
            legacy_value = legacy() if callable(legacy) else legacy
            return bool(legacy_value)
        except TypeError:
            pass

    return _resolve_player_model(player) not in NON_AUDIO_PLAYER_MODELS



def _normalize_player_name(player: Any, model_name: str) -> str:
    """Normalize player names for client UIs.

    Some firmware reports placeholder names like "nil"/"null". Treat these as
    missing names and fall back to device-reported model information.
    """
    raw_name = getattr(player, "name", "")
    if not isinstance(raw_name, str):
        raw_name = str(raw_name or "")

    name = raw_name.strip()
    if name and name.lower() not in NIL_LIKE_PLAYER_NAMES:
        return name

    capability_model_name = _player_capability(player, "ModelName")
    if capability_model_name and capability_model_name.lower() not in NIL_LIKE_PLAYER_NAMES:
        return capability_model_name

    model_label = _player_model_label(model_name)
    if model_label:
        return model_label

    mac = getattr(player, "mac_address", "")
    if isinstance(mac, str) and mac.strip():
        return mac.strip()

    return "Player"

def build_player_item(player: Any) -> dict[str, Any]:
    """
    Build a player loop item from a Player object.

    Args:
        player: Player instance with name, mac_address, etc.

    Returns:
        LMS-format player item
    """
    model_name = _resolve_player_model(player)
    return {
        "playerid": player.mac_address,
        "name": _normalize_player_name(player, model_name),
        "displaytype": model_name,
        "isplayer": 1 if is_audio_player(player) else 0,
        "canpoweroff": 1,
        "connected": 1,
        "model": model_name,
        "power": 1,
    }

# =============================================================================
# Response Building
# =============================================================================


def build_list_response(
    items: list[dict[str, Any]],
    total_count: int,
    loop_name: str,
) -> dict[str, Any]:
    """
    Build a standard LMS list response.

    Args:
        items: List of items for the loop
        total_count: Total count of matching items (for pagination)
        loop_name: Name of the loop field (e.g., "artists_loop")

    Returns:
        LMS-format response dict
    """
    return {
        "count": total_count,
        loop_name: items,
    }


def build_error_response(code: int, message: str) -> dict[str, Any]:
    """
    Build a JSON-RPC error response.

    Args:
        code: Error code
        message: Error message

    Returns:
        Error dict for JSON-RPC response
    """
    return {
        "code": code,
        "message": message,
    }


# Standard JSON-RPC error codes
ERROR_PARSE_ERROR = -32700
ERROR_INVALID_REQUEST = -32600
ERROR_METHOD_NOT_FOUND = -32601
ERROR_INVALID_PARAMS = -32602
ERROR_INTERNAL_ERROR = -32603
