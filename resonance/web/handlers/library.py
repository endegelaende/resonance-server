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

    # Build query based on filters
    if role_id is not None:
        # Filter by role
        total_count = await db.count_artists_by_role_id(role_id)
        rows = await db.list_artists_with_album_counts_by_role_id(
            role_id=role_id,
            offset=start,
            limit=items,
        )
    elif genre_id is not None:
        # Filter by genre
        total_count = await db.count_artists_by_genre_id(genre_id)
        rows = await db.list_artists_by_genre_id(
            genre_id=genre_id,
            offset=start,
            limit=items,
        )
    elif year is not None:
        # Filter by year
        total_count = await db.count_artists_by_year(year)
        rows = await db.list_artists_with_album_counts_by_year(
            year=year,
            offset=start,
            limit=items,
        )
    elif compilation is not None:
        # Filter by compilation
        total_count = await db.count_artists_by_compilation(compilation)
        rows = await db.list_artists_with_album_counts_by_compilation(
            compilation=compilation,
            offset=start,
            limit=items,
        )
    elif search_term:
        # Search - use list_artists with search
        rows = await db.list_artists(
            search=search_term,
            offset=start,
            limit=items,
        )
        total_count = len(rows)  # Approximate for search
    else:
        # All artists with album counts
        total_count = await db.count_artists()
        rows = await db.list_artists_with_album_counts(
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

    db = ctx.music_library._db
    server_url = f"http://{ctx.server_host}:{ctx.server_port}"

    # Handle combined filters
    if compilation is not None and genre_id is not None:
        total_count = await db.count_albums_by_compilation_and_genre_id(compilation, genre_id)
        rows = await db.list_albums_with_track_counts_by_compilation_and_genre_id(
            compilation=compilation,
            genre_id=genre_id,
            offset=start,
            limit=items,
        )
    elif compilation is not None and year is not None:
        total_count = await db.count_albums_by_compilation_and_year(compilation, year)
        rows = await db.list_albums_with_track_counts_by_compilation_and_year(
            compilation=compilation,
            year=year,
            offset=start,
            limit=items,
        )
    elif compilation is not None and artist_id is not None:
        total_count = await db.count_albums_by_compilation_and_artist(compilation, artist_id)
        rows = await db.list_albums_with_track_counts_by_compilation_and_artist(
            compilation=compilation,
            artist_id=artist_id,
            offset=start,
            limit=items,
        )
    elif artist_id is not None and year is not None:
        total_count = await db.count_albums_by_artist_and_year(artist_id, year)
        rows = await db.list_albums_with_track_counts_by_artist_and_year(
            artist_id=artist_id,
            year=year,
            offset=start,
            limit=items,
        )
    elif genre_id is not None and year is not None:
        total_count = await db.count_albums_by_genre_and_year(genre_id, year)
        rows = await db.list_albums_by_genre_and_year(
            genre_id=genre_id,
            year=year,
            offset=start,
            limit=items,
        )
    elif role_id is not None:
        total_count = await db.count_albums_by_role_id(role_id)
        rows = await db.list_albums_with_track_counts_by_role_id(
            role_id=role_id,
            offset=start,
            limit=items,
        )
    elif genre_id is not None:
        total_count = await db.count_albums_by_genre_id(genre_id)
        rows = await db.list_albums_by_genre_id(
            genre_id=genre_id,
            offset=start,
            limit=items,
        )
    elif artist_id is not None:
        total_count = await db.count_albums_by_artist(artist_id)
        rows = await db.list_albums_with_track_counts_by_artist(
            artist_id=artist_id,
            offset=start,
            limit=items,
        )
    elif year is not None:
        total_count = await db.count_albums_by_year(year)
        rows = await db.list_albums_with_track_counts_by_year(
            year=year,
            offset=start,
            limit=items,
        )
    elif compilation is not None:
        total_count = await db.count_albums_by_compilation(compilation)
        rows = await db.list_albums_with_track_counts_by_compilation(
            compilation=compilation,
            offset=start,
            limit=items,
        )
    elif search_term:
        rows = await db.list_albums(
            search=search_term,
            offset=start,
            limit=items,
        )
        total_count = len(rows)
    else:
        total_count = await db.count_albums()
        rows = await db.list_albums_with_track_counts(
            offset=start,
            limit=items,
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

    db = ctx.music_library._db
    server_url = f"http://{ctx.server_host}:{ctx.server_port}"

    # Handle combined filters
    if genre_id is not None and year is not None:
        total_count = await db.count_tracks_by_genre_and_year(genre_id, year)
        rows = await db.list_tracks_by_genre_and_year(
            genre_id=genre_id,
            year=year,
            offset=start,
            limit=items,
        )
    elif genre_id is not None and artist_id is not None:
        total_count = await db.count_tracks_by_genre_and_artist(genre_id, artist_id)
        rows = await db.list_tracks_by_genre_and_artist(
            genre_id=genre_id,
            artist_id=artist_id,
            offset=start,
            limit=items,
        )
    elif compilation is not None and genre_id is not None:
        total_count = await db.count_tracks_by_compilation_and_genre_id(compilation, genre_id)
        rows = await db.list_tracks_by_compilation_and_genre_id(
            compilation=compilation,
            genre_id=genre_id,
            offset=start,
            limit=items,
        )
    elif compilation is not None and year is not None:
        total_count = await db.count_tracks_by_compilation_and_year(compilation, year)
        rows = await db.list_tracks_by_compilation_and_year(
            compilation=compilation,
            year=year,
            offset=start,
            limit=items,
        )
    elif compilation is not None and artist_id is not None:
        total_count = await db.count_tracks_by_compilation_and_artist(compilation, artist_id)
        rows = await db.list_tracks_by_compilation_and_artist(
            compilation=compilation,
            artist_id=artist_id,
            offset=start,
            limit=items,
        )
    elif compilation is not None and album_id is not None:
        total_count = await db.count_tracks_by_compilation_and_album(compilation, album_id)
        rows = await db.list_tracks_by_compilation_and_album(
            compilation=compilation,
            album_id=album_id,
            offset=start,
            limit=items,
        )
    elif artist_id is not None and year is not None:
        total_count = await db.count_tracks_by_artist_and_year(artist_id, year)
        rows = await db.list_tracks_by_artist_and_year(
            artist_id=artist_id,
            year=year,
            offset=start,
            limit=items,
        )
    elif album_id is not None and year is not None:
        total_count = await db.count_tracks_by_album_and_year(album_id, year)
        rows = await db.list_tracks_by_album_and_year(
            album_id=album_id,
            year=year,
            offset=start,
            limit=items,
        )
    elif role_id is not None:
        total_count = await db.count_tracks_by_role_id(role_id)
        rows = await db.list_tracks_by_role_id(
            role_id=role_id,
            offset=start,
            limit=items,
        )
    elif genre_id is not None:
        total_count = await db.count_tracks_by_genre_id(genre_id)
        rows = await db.list_tracks_by_genre_id(
            genre_id=genre_id,
            offset=start,
            limit=items,
        )
    elif artist_id is not None:
        total_count = await db.count_tracks_by_artist(artist_id)
        rows = await db.list_tracks_by_artist(
            artist_id=artist_id,
            offset=start,
            limit=items,
        )
    elif album_id is not None:
        total_count = await db.count_tracks_by_album(album_id)
        rows = await db.list_tracks_by_album(
            album_id=album_id,
            offset=start,
            limit=items,
        )
    elif year is not None:
        total_count = await db.count_tracks_by_year(year)
        rows = await db.list_tracks_by_year(
            year=year,
            offset=start,
            limit=items,
        )
    elif compilation is not None:
        total_count = await db.count_tracks_by_compilation(compilation)
        rows = await db.list_tracks_by_compilation(
            compilation=compilation,
            offset=start,
            limit=items,
        )
    elif search_term:
        rows = await db.search_tracks(
            term=search_term,
            offset=start,
            limit=items,
        )
        total_count = len(rows)
    else:
        total_count = await db.count_tracks()
        rows = await db.list_tracks(
            offset=start,
            limit=items,
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

    Lists all genres with optional pagination.
    """
    start, items = parse_start_items(params)
    tagged_params = parse_tagged_params(params)

    tags_str = tagged_params.get("tags", "")
    tags = parse_tags_string(tags_str) if tags_str else None

    db = ctx.music_library._db

    total_count = await db.count_genres()
    rows = await db.list_genres(offset=start, limit=items)

    # Build response items
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

    Lists all contributor roles (for role_id: filter discovery).
    Standard roles: artist, albumartist, composer, conductor, band
    """
    start, items = parse_start_items(params)
    tagged_params = parse_tagged_params(params)

    tags_str = tagged_params.get("tags", "")
    tags = parse_tags_string(tags_str) if tags_str else None

    db = ctx.music_library._db

    total_count = await db.count_roles()
    rows = await db.list_roles(offset=start, limit=items)

    # Build response items
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
    Returns combined results.
    """
    tagged_params = parse_tagged_params(params)
    search_term = get_filter_str(tagged_params, "term")

    if not search_term:
        # Try to get term from positional params
        for param in params[1:]:
            if isinstance(param, str) and ":" not in param:
                search_term = param
                break

    if not search_term:
        return {
            "artists_count": 0,
            "albums_count": 0,
            "tracks_count": 0,
            "artists_loop": [],
            "albums_loop": [],
            "titles_loop": [],
        }

    db = ctx.music_library._db
    server_url = f"http://{ctx.server_host}:{ctx.server_port}"

    # Search all entity types
    artists = await db.list_artists(search=search_term, offset=0, limit=10)
    albums = await db.list_albums(search=search_term, offset=0, limit=10)
    tracks = await db.search_tracks(term=search_term, offset=0, limit=10)

    # Build response
    artists_loop = []
    for row in artists:
        artists_loop.append(build_artist_item(row))

    albums_loop = []
    for row in albums:
        albums_loop.append(build_album_item(row, server_url=server_url))

    titles_loop = []
    for row in tracks:
        titles_loop.append(build_track_item(row, server_url=server_url))

    return {
        "artists_count": len(artists),
        "albums_count": len(albums),
        "tracks_count": len(tracks),
        "artists_loop": artists_loop,
        "albums_loop": albums_loop,
        "titles_loop": titles_loop,
    }
