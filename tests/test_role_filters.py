import pytest

from resonance.core.library_db import LibraryDb, UpsertTrack


@pytest.mark.asyncio
async def test_titles_role_id_filters_tracks(tmp_path) -> None:
    """
    Ensure `titles ... role_id:<id>`-style filtering works end-to-end in the DB layer:
    - roles are created/ensured
    - contributor_tracks are persisted via UpsertTrack.contributors
    - list/count_tracks_by_role_id only returns matching tracks
    """
    db_path = tmp_path / "library.db"
    db = LibraryDb(db_path)
    await db.open()
    try:
        await db.ensure_schema()

        composer_role_id = await db._ensure_role("composer")
        conductor_role_id = await db._ensure_role("conductor")

        # Two albums, two tracks, different roles.
        t1 = UpsertTrack(
            path=str(tmp_path / "a1" / "t1.flac"),
            title="Track One",
            artist="Artist A",
            album="Album A",
            year=2021,
            contributors=(("composer", "Alice Composer"),),
        )
        t2 = UpsertTrack(
            path=str(tmp_path / "a2" / "t2.flac"),
            title="Track Two",
            artist="Artist B",
            album="Album B",
            year=2022,
            contributors=(("conductor", "Bob Conductor"),),
        )

        await db.upsert_tracks([t1, t2])
        await db.commit()

        # Composer should match only Track One
        composer_count = await db.count_tracks_by_role_id(int(composer_role_id))
        assert composer_count == 1

        composer_tracks = await db.list_tracks_by_role_id(
            int(composer_role_id), limit=100, offset=0, order_by="title"
        )
        assert len(composer_tracks) == 1
        assert composer_tracks[0].title == "Track One"
        assert composer_tracks[0].album == "Album A"

        # Conductor should match only Track Two
        conductor_count = await db.count_tracks_by_role_id(int(conductor_role_id))
        assert conductor_count == 1

        conductor_tracks = await db.list_tracks_by_role_id(
            int(conductor_role_id), limit=100, offset=0, order_by="title"
        )
        assert len(conductor_tracks) == 1
        assert conductor_tracks[0].title == "Track Two"
        assert conductor_tracks[0].album == "Album B"

        # Unknown role_id matches nothing
        assert await db.count_tracks_by_role_id(999999) == 0
        assert await db.list_tracks_by_role_id(999999, limit=100, offset=0, order_by="title") == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_titles_role_id_and_genre_id_filters_tracks(tmp_path) -> None:
    """
    Ensure AND filtering works in the DB layer:
      titles ... role_id:<id> genre_id:<id>
    """
    db_path = tmp_path / "library.db"
    db = LibraryDb(db_path)
    await db.open()
    try:
        await db.ensure_schema()

        composer_role_id = await db._ensure_role("composer")
        rock_genre_id = await db._ensure_genre("Rock")
        jazz_genre_id = await db._ensure_genre("Jazz")

        # t1: composer + Rock
        t1 = UpsertTrack(
            path=str(tmp_path / "a1" / "t1.flac"),
            title="Track One",
            artist="Artist A",
            album="Album A",
            year=2021,
            genres=("Rock",),
            contributors=(("composer", "Alice Composer"),),
        )
        # t2: composer + Jazz
        t2 = UpsertTrack(
            path=str(tmp_path / "a2" / "t2.flac"),
            title="Track Two",
            artist="Artist B",
            album="Album B",
            year=2021,
            genres=("Jazz",),
            contributors=(("composer", "Alice Composer"),),
        )
        # t3: conductor + Rock (should not match composer role)
        t3 = UpsertTrack(
            path=str(tmp_path / "a3" / "t3.flac"),
            title="Track Three",
            artist="Artist C",
            album="Album C",
            year=2021,
            genres=("Rock",),
            contributors=(("conductor", "Bob Conductor"),),
        )

        await db.upsert_tracks([t1, t2, t3])
        await db.commit()

        # Composer + Rock => only Track One
        count = await db.count_tracks_by_role_and_genre_id(
            int(composer_role_id), int(rock_genre_id)
        )
        assert count == 1

        rows = await db.list_tracks_by_role_and_genre_id(
            int(composer_role_id),
            int(rock_genre_id),
            limit=100,
            offset=0,
            order_by="title",
        )
        assert len(rows) == 1
        assert rows[0].title == "Track One"

        # Composer + Jazz => only Track Two
        count2 = await db.count_tracks_by_role_and_genre_id(
            int(composer_role_id), int(jazz_genre_id)
        )
        assert count2 == 1

        rows2 = await db.list_tracks_by_role_and_genre_id(
            int(composer_role_id),
            int(jazz_genre_id),
            limit=100,
            offset=0,
            order_by="title",
        )
        assert len(rows2) == 1
        assert rows2[0].title == "Track Two"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_titles_role_id_and_year_filters_tracks(tmp_path) -> None:
    """
    Ensure AND filtering works in the DB layer:
      titles ... role_id:<id> year:<yyyy>
    """
    db_path = tmp_path / "library.db"
    db = LibraryDb(db_path)
    await db.open()
    try:
        await db.ensure_schema()

        composer_role_id = await db._ensure_role("composer")

        t1 = UpsertTrack(
            path=str(tmp_path / "a1" / "t1.flac"),
            title="Y2021",
            artist="Artist A",
            album="Album A",
            year=2021,
            contributors=(("composer", "Alice Composer"),),
        )
        t2 = UpsertTrack(
            path=str(tmp_path / "a2" / "t2.flac"),
            title="Y2022",
            artist="Artist B",
            album="Album B",
            year=2022,
            contributors=(("composer", "Alice Composer"),),
        )
        t3 = UpsertTrack(
            path=str(tmp_path / "a3" / "t3.flac"),
            title="Other Role 2021",
            artist="Artist C",
            album="Album C",
            year=2021,
            contributors=(("conductor", "Bob Conductor"),),
        )

        await db.upsert_tracks([t1, t2, t3])
        await db.commit()

        count = await db.count_tracks_by_role_and_year(int(composer_role_id), 2021)
        assert count == 1

        rows = await db.list_tracks_by_role_and_year(
            int(composer_role_id),
            2021,
            limit=100,
            offset=0,
            order_by="title",
        )
        assert len(rows) == 1
        assert rows[0].title == "Y2021"

        count2 = await db.count_tracks_by_role_and_year(int(composer_role_id), 2022)
        assert count2 == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_titles_role_id_and_compilation_filters_tracks(tmp_path) -> None:
    """
    Ensure AND filtering works in the DB layer:
      titles ... role_id:<id> compilation:0/1
    """
    db_path = tmp_path / "library.db"
    db = LibraryDb(db_path)
    await db.open()
    try:
        await db.ensure_schema()

        composer_role_id = await db._ensure_role("composer")

        # compilation=1 + composer
        t1 = UpsertTrack(
            path=str(tmp_path / "c1" / "t1.flac"),
            title="Comp One",
            artist="Artist A",
            album="Album A",
            year=2021,
            compilation=True,
            contributors=(("composer", "Alice Composer"),),
        )
        # compilation=0 + composer
        t2 = UpsertTrack(
            path=str(tmp_path / "c2" / "t2.flac"),
            title="Non-Comp One",
            artist="Artist B",
            album="Album B",
            year=2021,
            compilation=False,
            contributors=(("composer", "Alice Composer"),),
        )
        # compilation=1 but different role
        t3 = UpsertTrack(
            path=str(tmp_path / "c3" / "t3.flac"),
            title="Comp Other Role",
            artist="Artist C",
            album="Album C",
            year=2021,
            compilation=True,
            contributors=(("conductor", "Bob Conductor"),),
        )

        await db.upsert_tracks([t1, t2, t3])
        await db.commit()

        # role=composer AND compilation=1 => only t1
        count = await db.count_tracks_by_role_and_compilation(int(composer_role_id), 1)
        assert count == 1

        rows = await db.list_tracks_by_role_and_compilation(
            int(composer_role_id),
            1,
            limit=100,
            offset=0,
            order_by="title",
        )
        assert len(rows) == 1
        assert rows[0].title == "Comp One"

        # role=composer AND compilation=0 => only t2
        count2 = await db.count_tracks_by_role_and_compilation(int(composer_role_id), 0)
        assert count2 == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_albums_role_id_filters_albums(tmp_path) -> None:
    """
    Ensure `albums ... role_id:<id>`-style filtering works end-to-end in the DB layer:
    - list/count_albums_by_role_id returns albums that contain at least one matching track
    - list_albums_with_track_counts_by_role_id includes track_count and album metadata
    """
    db_path = tmp_path / "library.db"
    db = LibraryDb(db_path)
    await db.open()
    try:
        await db.ensure_schema()

        composer_role_id = await db._ensure_role("composer")

        # Album A: two tracks, one with composer
        t1 = UpsertTrack(
            path=str(tmp_path / "a1" / "t1.flac"),
            title="A1",
            artist="Artist A",
            album="Album A",
            track_no=1,
            year=2021,
            contributors=(("composer", "Alice Composer"),),
        )
        t2 = UpsertTrack(
            path=str(tmp_path / "a1" / "t2.flac"),
            title="A2",
            artist="Artist A",
            album="Album A",
            track_no=2,
            year=2021,
            contributors=(),
        )

        # Album B: one track, no composer
        t3 = UpsertTrack(
            path=str(tmp_path / "b1" / "t1.flac"),
            title="B1",
            artist="Artist B",
            album="Album B",
            track_no=1,
            year=2022,
            contributors=(("conductor", "Bob Conductor"),),
        )

        await db.upsert_tracks([t1, t2, t3])
        await db.commit()

        total = await db.count_albums_by_role_id(int(composer_role_id))
        assert total == 1

        albums = await db.list_albums_with_track_counts_by_role_id(
            int(composer_role_id), limit=100, offset=0, order_by="album"
        )
        assert len(albums) == 1
        assert albums[0]["name"] == "Album A"
        assert albums[0]["track_count"] >= 1

        # Ensure Album B does not appear
        names = {a["name"] for a in albums}
        assert "Album B" not in names

        # Unknown role_id matches nothing
        assert await db.count_albums_by_role_id(999999) == 0
        assert (
            await db.list_albums_with_track_counts_by_role_id(
                999999, limit=100, offset=0, order_by="album"
            )
            == []
        )
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_artists_role_id_filters_artists(tmp_path) -> None:
    """
    Ensure `artists ... role_id:<id>`-style filtering works end-to-end in the DB layer using
    LibraryDb helper methods:
      - list_artists_with_album_counts_by_role_id
      - count_artists_by_role_id
    """
    db_path = tmp_path / "library.db"
    db = LibraryDb(db_path)
    await db.open()
    try:
        await db.ensure_schema()

        composer_role_id = await db._ensure_role("composer")
        conductor_role_id = await db._ensure_role("conductor")

        # Three tracks, two different contributors/roles
        t1 = UpsertTrack(
            path=str(tmp_path / "a1" / "t1.flac"),
            title="Track One",
            artist="Artist A",
            album="Album A",
            year=2021,
            contributors=(("composer", "Alice Composer"),),
        )
        t2 = UpsertTrack(
            path=str(tmp_path / "a2" / "t2.flac"),
            title="Track Two",
            artist="Artist B",
            album="Album B",
            year=2022,
            contributors=(("conductor", "Bob Conductor"),),
        )
        # Another composer track to ensure album_count/track_count aggregation stays sane
        t3 = UpsertTrack(
            path=str(tmp_path / "a3" / "t3.flac"),
            title="Track Three",
            artist="Artist A",
            album="Album C",
            year=2021,
            contributors=(("composer", "Alice Composer"),),
        )

        await db.upsert_tracks([t1, t2, t3])
        await db.commit()

        # Composer should match only Alice Composer
        composer_total = await db.count_artists_by_role_id(int(composer_role_id))
        assert composer_total == 1

        composer_artists = await db.list_artists_with_album_counts_by_role_id(
            int(composer_role_id),
            limit=100,
            offset=0,
            order_by="artist",
        )
        assert len(composer_artists) == 1
        assert composer_artists[0]["name"] == "Alice Composer"
        assert composer_artists[0]["track_count"] == 2
        assert composer_artists[0]["album_count"] == 2

        # Conductor should match only Bob Conductor
        conductor_total = await db.count_artists_by_role_id(int(conductor_role_id))
        assert conductor_total == 1

        conductor_artists = await db.list_artists_with_album_counts_by_role_id(
            int(conductor_role_id),
            limit=100,
            offset=0,
            order_by="artist",
        )
        assert len(conductor_artists) == 1
        assert conductor_artists[0]["name"] == "Bob Conductor"
        assert conductor_artists[0]["track_count"] == 1
        assert conductor_artists[0]["album_count"] == 1

        # Unknown role_id matches nothing
        assert await db.count_artists_by_role_id(999999) == 0
        assert (
            await db.list_artists_with_album_counts_by_role_id(
                999999, limit=100, offset=0, order_by="artist"
            )
            == []
        )
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_artists_role_id_and_genre_id_filters_artists(tmp_path) -> None:
    """
    Ensure AND filtering works in the DB layer:
      artists ... role_id:<id> genre_id:<id>
    """
    db_path = tmp_path / "library.db"
    db = LibraryDb(db_path)
    await db.open()
    try:
        await db.ensure_schema()

        composer_role_id = await db._ensure_role("composer")
        rock_genre_id = await db._ensure_genre("Rock")
        jazz_genre_id = await db._ensure_genre("Jazz")

        # composer + Rock
        t1 = UpsertTrack(
            path=str(tmp_path / "a1" / "t1.flac"),
            title="Track One",
            artist="Artist A",
            album="Album A",
            year=2021,
            genres=("Rock",),
            contributors=(("composer", "Alice Composer"),),
        )
        # composer + Jazz
        t2 = UpsertTrack(
            path=str(tmp_path / "a2" / "t2.flac"),
            title="Track Two",
            artist="Artist B",
            album="Album B",
            year=2021,
            genres=("Jazz",),
            contributors=(("composer", "Alice Composer"),),
        )
        # conductor + Rock (should not match)
        t3 = UpsertTrack(
            path=str(tmp_path / "a3" / "t3.flac"),
            title="Track Three",
            artist="Artist C",
            album="Album C",
            year=2021,
            genres=("Rock",),
            contributors=(("conductor", "Bob Conductor"),),
        )

        await db.upsert_tracks([t1, t2, t3])
        await db.commit()

        total_rock = await db.count_artists_by_role_and_genre_id(
            int(composer_role_id), int(rock_genre_id)
        )
        assert total_rock == 1
        artists_rock = await db.list_artists_with_album_counts_by_role_and_genre_id(
            int(composer_role_id),
            int(rock_genre_id),
            limit=100,
            offset=0,
            order_by="artist",
        )
        assert len(artists_rock) == 1
        assert artists_rock[0]["name"] == "Alice Composer"

        total_jazz = await db.count_artists_by_role_and_genre_id(
            int(composer_role_id), int(jazz_genre_id)
        )
        assert total_jazz == 1
        artists_jazz = await db.list_artists_with_album_counts_by_role_and_genre_id(
            int(composer_role_id),
            int(jazz_genre_id),
            limit=100,
            offset=0,
            order_by="artist",
        )
        assert len(artists_jazz) == 1
        assert artists_jazz[0]["name"] == "Alice Composer"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_artists_role_id_and_year_filters_artists(tmp_path) -> None:
    """
    Ensure AND filtering works in the DB layer:
      artists ... role_id:<id> year:<yyyy>
    """
    db_path = tmp_path / "library.db"
    db = LibraryDb(db_path)
    await db.open()
    try:
        await db.ensure_schema()

        composer_role_id = await db._ensure_role("composer")

        # composer in 2021 + 2022
        t1 = UpsertTrack(
            path=str(tmp_path / "a1" / "t1.flac"),
            title="Y2021",
            artist="Artist A",
            album="Album A",
            year=2021,
            contributors=(("composer", "Alice Composer"),),
        )
        t2 = UpsertTrack(
            path=str(tmp_path / "a2" / "t2.flac"),
            title="Y2022",
            artist="Artist B",
            album="Album B",
            year=2022,
            contributors=(("composer", "Alice Composer"),),
        )
        # conductor in 2021 (should not match composer role)
        t3 = UpsertTrack(
            path=str(tmp_path / "a3" / "t3.flac"),
            title="Other Role 2021",
            artist="Artist C",
            album="Album C",
            year=2021,
            contributors=(("conductor", "Bob Conductor"),),
        )

        await db.upsert_tracks([t1, t2, t3])
        await db.commit()

        total_2021 = await db.count_artists_by_role_and_year(int(composer_role_id), 2021)
        assert total_2021 == 1
        artists_2021 = await db.list_artists_with_album_counts_by_role_and_year(
            int(composer_role_id),
            2021,
            limit=100,
            offset=0,
            order_by="artist",
        )
        assert len(artists_2021) == 1
        assert artists_2021[0]["name"] == "Alice Composer"

        total_2022 = await db.count_artists_by_role_and_year(int(composer_role_id), 2022)
        assert total_2022 == 1
        artists_2022 = await db.list_artists_with_album_counts_by_role_and_year(
            int(composer_role_id),
            2022,
            limit=100,
            offset=0,
            order_by="artist",
        )
        assert len(artists_2022) == 1
        assert artists_2022[0]["name"] == "Alice Composer"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_artists_role_id_and_compilation_filters_artists(tmp_path) -> None:
    """
    Ensure AND filtering works in the DB layer:
      artists ... role_id:<id> compilation:0/1
    """
    db_path = tmp_path / "library.db"
    db = LibraryDb(db_path)
    await db.open()
    try:
        await db.ensure_schema()

        composer_role_id = await db._ensure_role("composer")

        # compilation=1 + composer
        t1 = UpsertTrack(
            path=str(tmp_path / "c1" / "t1.flac"),
            title="Comp One",
            artist="Artist A",
            album="Album A",
            year=2021,
            compilation=True,
            contributors=(("composer", "Alice Composer"),),
        )
        # compilation=0 + composer
        t2 = UpsertTrack(
            path=str(tmp_path / "c2" / "t2.flac"),
            title="Non-Comp One",
            artist="Artist B",
            album="Album B",
            year=2021,
            compilation=False,
            contributors=(("composer", "Alice Composer"),),
        )
        # compilation=1 but different role
        t3 = UpsertTrack(
            path=str(tmp_path / "c3" / "t3.flac"),
            title="Comp Other Role",
            artist="Artist C",
            album="Album C",
            year=2021,
            compilation=True,
            contributors=(("conductor", "Bob Conductor"),),
        )

        await db.upsert_tracks([t1, t2, t3])
        await db.commit()

        total_comp_1 = await db.count_artists_by_role_and_compilation(int(composer_role_id), 1)
        assert total_comp_1 == 1
        artists_comp_1 = await db.list_artists_with_album_counts_by_role_and_compilation(
            int(composer_role_id),
            1,
            limit=100,
            offset=0,
            order_by="artist",
        )
        assert len(artists_comp_1) == 1
        assert artists_comp_1[0]["name"] == "Alice Composer"

        total_comp_0 = await db.count_artists_by_role_and_compilation(int(composer_role_id), 0)
        assert total_comp_0 == 1
        artists_comp_0 = await db.list_artists_with_album_counts_by_role_and_compilation(
            int(composer_role_id),
            0,
            limit=100,
            offset=0,
            order_by="artist",
        )
        assert len(artists_comp_0) == 1
        assert artists_comp_0[0]["name"] == "Alice Composer"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_albums_role_id_and_genre_id_filters_albums(tmp_path) -> None:
    """
    Ensure AND filtering works in the DB layer:
      albums ... role_id:<id> genre_id:<id>
    """
    db_path = tmp_path / "library.db"
    db = LibraryDb(db_path)
    await db.open()
    try:
        await db.ensure_schema()

        composer_role_id = await db._ensure_role("composer")
        rock_genre_id = await db._ensure_genre("Rock")
        jazz_genre_id = await db._ensure_genre("Jazz")

        # Album A: composer + Rock
        t1 = UpsertTrack(
            path=str(tmp_path / "a1" / "t1.flac"),
            title="A1",
            artist="Artist A",
            album="Album A",
            year=2021,
            genres=("Rock",),
            contributors=(("composer", "Alice Composer"),),
        )

        # Album B: composer + Jazz
        t2 = UpsertTrack(
            path=str(tmp_path / "b1" / "t1.flac"),
            title="B1",
            artist="Artist B",
            album="Album B",
            year=2021,
            genres=("Jazz",),
            contributors=(("composer", "Alice Composer"),),
        )

        # Album C: Rock but not composer
        t3 = UpsertTrack(
            path=str(tmp_path / "c1" / "t1.flac"),
            title="C1",
            artist="Artist C",
            album="Album C",
            year=2021,
            genres=("Rock",),
            contributors=(("conductor", "Bob Conductor"),),
        )

        await db.upsert_tracks([t1, t2, t3])
        await db.commit()

        # Composer + Rock => only Album A
        total = await db.count_albums_by_role_and_genre_id(
            int(composer_role_id), int(rock_genre_id)
        )
        assert total == 1

        albums = await db.list_albums_with_track_counts_by_role_and_genre_id(
            int(composer_role_id),
            int(rock_genre_id),
            limit=100,
            offset=0,
            order_by="album",
        )
        assert len(albums) == 1
        assert albums[0]["name"] == "Album A"

        # Composer + Jazz => only Album B
        total2 = await db.count_albums_by_role_and_genre_id(
            int(composer_role_id), int(jazz_genre_id)
        )
        assert total2 == 1
        albums2 = await db.list_albums_with_track_counts_by_role_and_genre_id(
            int(composer_role_id),
            int(jazz_genre_id),
            limit=100,
            offset=0,
            order_by="album",
        )
        assert len(albums2) == 1
        assert albums2[0]["name"] == "Album B"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_albums_role_id_and_year_filters_albums(tmp_path) -> None:
    """
    Ensure AND filtering works in the DB layer:
      albums ... role_id:<id> year:<yyyy>
    """
    db_path = tmp_path / "library.db"
    db = LibraryDb(db_path)
    await db.open()
    try:
        await db.ensure_schema()

        composer_role_id = await db._ensure_role("composer")

        # Album A: 2021 composer
        t1 = UpsertTrack(
            path=str(tmp_path / "a1" / "t1.flac"),
            title="A1",
            artist="Artist A",
            album="Album A",
            year=2021,
            contributors=(("composer", "Alice Composer"),),
        )

        # Album B: 2022 composer
        t2 = UpsertTrack(
            path=str(tmp_path / "b1" / "t1.flac"),
            title="B1",
            artist="Artist B",
            album="Album B",
            year=2022,
            contributors=(("composer", "Alice Composer"),),
        )

        # Album C: 2021 but not composer
        t3 = UpsertTrack(
            path=str(tmp_path / "c1" / "t1.flac"),
            title="C1",
            artist="Artist C",
            album="Album C",
            year=2021,
            contributors=(("conductor", "Bob Conductor"),),
        )

        await db.upsert_tracks([t1, t2, t3])
        await db.commit()

        total_2021 = await db.count_albums_by_role_and_year(int(composer_role_id), 2021)
        assert total_2021 == 1
        albums_2021 = await db.list_albums_with_track_counts_by_role_and_year(
            int(composer_role_id), 2021, limit=100, offset=0, order_by="album"
        )
        assert len(albums_2021) == 1
        assert albums_2021[0]["name"] == "Album A"

        total_2022 = await db.count_albums_by_role_and_year(int(composer_role_id), 2022)
        assert total_2022 == 1
        albums_2022 = await db.list_albums_with_track_counts_by_role_and_year(
            int(composer_role_id), 2022, limit=100, offset=0, order_by="album"
        )
        assert len(albums_2022) == 1
        assert albums_2022[0]["name"] == "Album B"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_albums_role_id_and_compilation_filters_albums(tmp_path) -> None:
    """
    Ensure AND filtering works in the DB layer:
      albums ... role_id:<id> compilation:0/1
    """
    db_path = tmp_path / "library.db"
    db = LibraryDb(db_path)
    await db.open()
    try:
        await db.ensure_schema()

        composer_role_id = await db._ensure_role("composer")

        # Album A: compilation=1 + composer
        t1 = UpsertTrack(
            path=str(tmp_path / "a1" / "t1.flac"),
            title="A1",
            artist="Artist A",
            album="Album A",
            year=2021,
            compilation=True,
            contributors=(("composer", "Alice Composer"),),
        )

        # Album B: compilation=0 + composer
        t2 = UpsertTrack(
            path=str(tmp_path / "b1" / "t1.flac"),
            title="B1",
            artist="Artist B",
            album="Album B",
            year=2021,
            compilation=False,
            contributors=(("composer", "Alice Composer"),),
        )

        # Album C: compilation=1 but not composer
        t3 = UpsertTrack(
            path=str(tmp_path / "c1" / "t1.flac"),
            title="C1",
            artist="Artist C",
            album="Album C",
            year=2021,
            compilation=True,
            contributors=(("conductor", "Bob Conductor"),),
        )

        await db.upsert_tracks([t1, t2, t3])
        await db.commit()

        total_comp_1 = await db.count_albums_by_role_and_compilation(int(composer_role_id), 1)
        assert total_comp_1 == 1
        albums_comp_1 = await db.list_albums_with_track_counts_by_role_and_compilation(
            int(composer_role_id), 1, limit=100, offset=0, order_by="album"
        )
        assert len(albums_comp_1) == 1
        assert albums_comp_1[0]["name"] == "Album A"

        total_comp_0 = await db.count_albums_by_role_and_compilation(int(composer_role_id), 0)
        assert total_comp_0 == 1
        albums_comp_0 = await db.list_albums_with_track_counts_by_role_and_compilation(
            int(composer_role_id), 0, limit=100, offset=0, order_by="album"
        )
        assert len(albums_comp_0) == 1
        assert albums_comp_0[0]["name"] == "Album B"
    finally:
        await db.close()
