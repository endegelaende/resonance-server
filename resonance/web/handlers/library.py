"""
Library Command Handlers.

Handles music library browsing commands:
- artists: List and filter artists
- albums: List and filter albums
- titles: List and filter tracks
- genres: List genres
- roles: List contributor roles (artist, albumartist, composer, conductor, band)
- search: Full-text search across library
"""

from __future__ import annotations

import logging
from typing import Any

from resonance.core.db.query_builder import AlbumFilter, ArtistFilter, TrackFilter
from resonance.web.handlers import CommandContext
from resonance.web.jsonrpc_helpers import (
    build_album_item,
    build_artist_item,
    build_genre_item,
    build_list_response,
    build_role_item,
    build_track_item,
    get_filter_int,
    get_filter_str,
    parse_start_items,
    parse_tagged_params,
    parse_tags_string,
)

logger = logging.getLogger(__name__)


async def cmd_artists(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """
    Handle 'artists' command.

    Lists artists with optional filtering and pagination.

    Filters:
    - genre_id:<id> : Filter by genre
    - role_id:<id> : Filter by contributor role
    - year:<year> : Filter by year
    - compilation:<0|1> : Filter by compilation status
    - search:<term> : Search by name

    Sorting:
    - sort:artist : Sort by name (default)
    - sort:id : Sort by ID
    - sort:albums : Sort by album count (descending)
    """
    start, items = parse_start_items(params)
    tagged_params = parse_tagged_params(params)

    tags_str = tagged_params.get("tags", "")
    tags = parse_tags_string(tags_str) if tags_str else None

    # Parse filters
    genre_id = get_filter_int(tagged_params, "genre_id")
    role_id = get_filter_int(tagged_params, "role_id")
    year = get_filter_int(tagged_params, "year")
    compilation = get_filter_int(tagged_params, "compilation")
    search_term = get_filter_str(tagged_params, "search")

    # Parse sort
    sort_key = tagged_params.get("sort", "artist").lower()

    db = ctx.music_library._db

    # Build composable filter (search is just another filter dimension)
    f = ArtistFilter(
        genre_id=genre_id,
        role_id=role_id,
        year=year,
        compilation=compilation,
        search=search_term,
    )

    total_count = await db.count_artists_filtered(f)
    rows = await db.list_artists_filtered(
        f,
        offset=start,
        limit=items,
        order_by=sort_key,
    )

    # Build response items
    artists_loop = []
    for row in rows:
        item = build_artist_item(row, tags)
        artists_loop.append(item)

    return build_list_response(artists_loop, total_count, "artists_loop")


async def cmd_albums(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """
    Handle 'albums' command.

    Lists albums with optional filtering and pagination.

    Filters:
    - artist_id:<id> : Filter by artist
    - genre_id:<id> : Filter by genre
    - role_id:<id> : Filter by contributor role
    - year:<year> : Filter by year
    - compilation:<0|1> : Filter by compilation status
    - search:<term> : Search by title

    Sorting:
    - sort:album : Sort by title (default)
    - sort:artist : Sort by artist name
    - sort:year : Sort by year (descending)
    - sort:new : Sort by recently added
    """
    start, items = parse_start_items(params)
    tagged_params = parse_tagged_params(params)

    tags_str = tagged_params.get("tags", "")
    tags = parse_tags_string(tags_str) if tags_str else None

    # Parse filters
    artist_id = get_filter_int(tagged_params, "artist_id")
    genre_id = get_filter_int(tagged_params, "genre_id")
    role_id = get_filter_int(tagged_params, "role_id")
    year = get_filter_int(tagged_params, "year")
    compilation = get_filter_int(tagged_params, "compilation")
    search_term = get_filter_str(tagged_params, "search")

    # Parse sort
    sort_key = tagged_params.get("sort", "album").lower()

    db = ctx.music_library._db
    server_url = f"http://{ctx.server_host}:{ctx.server_port}"

    # Build composable filter (search is just another filter dimension)
    f = AlbumFilter(
        artist_id=artist_id,
        genre_id=genre_id,
        year=year,
        compilation=compilation,
        role_id=role_id,
        search=search_term,
    )

    total_count = await db.count_albums_filtered(f)
    rows = await db.list_albums_filtered(
        f,
        offset=start,
        limit=items,
        order_by=sort_key,
    )

    # Build response items
    albums_loop = []
    for row in rows:
        item = build_album_item(row, tags, server_url=server_url)
        albums_loop.append(item)

    return build_list_response(albums_loop, total_count, "albums_loop")


async def cmd_titles(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """
    Handle 'titles' command.

    Lists tracks with optional filtering and pagination.

    Filters:
    - artist_id:<id> : Filter by artist
    - album_id:<id> : Filter by album
    - genre_id:<id> : Filter by genre
    - role_id:<id> : Filter by contributor role
    - year:<year> : Filter by year
    - compilation:<0|1> : Filter by compilation status
    - search:<term> : Search by title

    Sorting:
    - sort:title : Sort by title (default)
    - sort:album : Sort by album
    - sort:artist : Sort by artist
    - sort:tracknum : Sort by track number
    """
    start, items = parse_start_items(params)
    tagged_params = parse_tagged_params(params)

    tags_str = tagged_params.get("tags", "")
    tags = parse_tags_string(tags_str) if tags_str else None

    # Parse filters
    artist_id = get_filter_int(tagged_params, "artist_id")
    album_id = get_filter_int(tagged_params, "album_id")
    genre_id = get_filter_int(tagged_params, "genre_id")
    role_id = get_filter_int(tagged_params, "role_id")
    year = get_filter_int(tagged_params, "year")
    compilation = get_filter_int(tagged_params, "compilation")
    search_term = get_filter_str(tagged_params, "search")

    # Parse sort
    sort_key = tagged_params.get("sort", "title").lower()

    db = ctx.music_library._db
    server_url = f"http://{ctx.server_host}:{ctx.server_port}"

    if search_term:
        # Search — uses dedicated FTS/LIKE search (not filter-based)
        rows = await db.search_tracks(
            query=search_term,
            offset=start,
            limit=items,
        )
        total_count = len(rows)
    else:
        # Build composable filter
        f = TrackFilter(
            genre_id=genre_id,
            artist_id=artist_id,
            album_id=album_id,
            year=year,
            compilation=compilation,
            role_id=role_id,
        )

        total_count = await db.count_tracks_filtered(f)
        rows = await db.list_tracks_filtered(
            f,
            offset=start,
            limit=items,
            order_by=sort_key,
        )

    # Build response items
    titles_loop = []
    for row in rows:
        item = build_track_item(row, tags, server_url=server_url)
        titles_loop.append(item)

    return build_list_response(titles_loop, total_count, "titles_loop")


async def cmd_genres(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """
    Handle 'genres' command.

    Lists all genres with track counts.
    """
    start, items = parse_start_items(params)
    tagged_params = parse_tagged_params(params)

    tags_str = tagged_params.get("tags", "")
    tags = parse_tags_string(tags_str) if tags_str else None

    db = ctx.music_library._db

    total_count = await db.count_genres()
    rows = await db.list_genres(offset=start, limit=items)

    genres_loop = []
    for row in rows:
        item = build_genre_item(row, tags)
        genres_loop.append(item)

    return build_list_response(genres_loop, total_count, "genres_loop")


async def cmd_roles(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """
    Handle 'roles' command.

    Lists all contributor roles (artist, albumartist, composer, conductor, band).
    """
    start, items = parse_start_items(params)
    tagged_params = parse_tagged_params(params)

    tags_str = tagged_params.get("tags", "")
    tags = parse_tags_string(tags_str) if tags_str else None

    db = ctx.music_library._db

    total_count = await db.count_roles()
    rows = await db.list_roles(offset=start, limit=items)

    roles_loop = []
    for row in rows:
        item = build_role_item(row, tags)
        roles_loop.append(item)

    return build_list_response(roles_loop, total_count, "roles_loop")


async def cmd_search(
    ctx: CommandContext,
    params: list[Any],
) -> dict[str, Any]:
    """
    Handle 'search' command.

    Performs a full-text search across artists, albums, and tracks.
    Returns combined results from all three categories.

    Parameters:
    - term:<query> : The search query string
    """
    tagged_params = parse_tagged_params(params)
    start, items = parse_start_items(params)

    tags_str = tagged_params.get("tags", "")
    tags = parse_tags_string(tags_str) if tags_str else None

    search_term = tagged_params.get("term", "")
    if not search_term:
        return {"count": 0}

    db = ctx.music_library._db
    server_url = f"http://{ctx.server_host}:{ctx.server_port}"

    # Search tracks
    tracks = await db.search_tracks(query=search_term, limit=items, offset=start)
    tracks_loop = [build_track_item(t, tags, server_url=server_url) for t in tracks]

    # Search artists (via composable filter)
    artist_filter = ArtistFilter(search=search_term)
    artists = await db.list_artists_filtered(
        artist_filter, offset=start, limit=items, order_by="artist"
    )
    artists_loop = [build_artist_item(a, tags) for a in artists]

    # Search albums (via composable filter)
    album_filter = AlbumFilter(search=search_term)
    albums = await db.list_albums_filtered(
        album_filter, offset=start, limit=items, order_by="album"
    )
    albums_loop = [build_album_item(al, tags, server_url=server_url) for al in albums]

    result: dict[str, Any] = {"count": len(tracks_loop) + len(artists_loop) + len(albums_loop)}
    if tracks_loop:
        result["tracks_loop"] = tracks_loop
        result["tracks_count"] = len(tracks_loop)
    if artists_loop:
        result["artists_loop"] = artists_loop
        result["artists_count"] = len(artists_loop)
    if albums_loop:
        result["albums_loop"] = albums_loop
        result["albums_count"] = len(albums_loop)

    return result
