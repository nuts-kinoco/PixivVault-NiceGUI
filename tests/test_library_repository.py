import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
from pixivvault_nicegui.library_repository import ReadonlyLibraryRepository


@pytest.fixture
def library(tmp_path):
    path = tmp_path / "fixture.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE works (work_id TEXT PRIMARY KEY,title TEXT,page_count INTEGER,
                            tags TEXT,user_id TEXT,is_deleted INTEGER DEFAULT 0);
        CREATE TABLE following_users (user_id TEXT PRIMARY KEY,name TEXT);
        INSERT INTO following_users VALUES ('u1','作者甲');
        INSERT INTO works VALUES ('01','100%_完了',2,'["日本語","猫"]','u1',0);
        INSERT INTO works VALUES ('02','second',NULL,'invalid','u2',0);
        INSERT INTO works VALUES ('03','deleted',1,'[]','u1',1);
    """)
    conn.commit()
    conn.close()
    return path


def test_read_mapping_search_and_paging(library):
    before = library.read_bytes()
    with ReadonlyLibraryRepository(library) as repo:
        assert repo.count_artworks() == 2
        assert repo.count_artworks("作者甲") == 1
        assert repo.count_artworks("%_") == 1
        assert repo.count_artworks("' OR 1=1 --") == 0
        a, b = repo.list_artworks()
        assert (a.work_id, a.title, a.author, a.page_count, a.tags) == (
            "01", "100%_完了", "作者甲", 2, ("日本語", "猫"))
        assert (b.author, b.page_count, b.tags) == ("u2", 1, ())
        assert repo.list_artworks(offset=1, limit=1) == (b,)
        assert repo.list_artworks(query="%_") == (a,)
        for kwargs in ({"offset": -1}, {"offset": 1_000_001}, {"limit": 0}, {"limit": 101}):
            with pytest.raises(ValueError):
                repo.list_artworks(**kwargs)
        with pytest.raises(sqlite3.OperationalError):
            repo._conn.execute("DELETE FROM works")
    assert library.read_bytes() == before
    with pytest.raises(RuntimeError, match="closed"):
        repo.count_artworks()


def test_missing_and_unknown_schema(tmp_path):
    absent = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        ReadonlyLibraryRepository(absent)
    assert not absent.exists()
    with pytest.raises(ValueError):
        ReadonlyLibraryRepository(None)
    path = tmp_path / "empty.db"
    conn = sqlite3.connect(path)
    conn.close()
    with pytest.raises(ValueError, match="schema"):
        ReadonlyLibraryRepository(path)


def test_wal_visible_without_immutable(tmp_path):
    path = tmp_path / "wal.db"
    writer = sqlite3.connect(path)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.executescript("""
            CREATE TABLE works (work_id TEXT,title TEXT,page_count INTEGER,tags TEXT,user_id TEXT,is_deleted INTEGER);
            CREATE TABLE following_users (user_id TEXT,name TEXT);
            INSERT INTO works VALUES ('wal','visible',1,'[]','',0);
        """)
        writer.commit()
        with ReadonlyLibraryRepository(path) as reader:
            assert reader.count_artworks() == 1
            assert [work.work_id for work in reader.library_snapshot().works] == ["wal"]
    finally:
        writer.close()


def test_full_snapshot_preserves_author_grouping_and_unknown_pages(library):
    before = library.read_bytes()
    with ReadonlyLibraryRepository(library) as repo:
        snapshot = repo.library_snapshot()
    assert len(snapshot.works) == 2
    first, second = snapshot.works
    assert (first.work_id, first.author_id, first.artwork.author) == ("01", "u1", "作者甲")
    assert first.artwork.page_count == 2 and first.page_count_known
    assert second.work_id == "02" and not second.page_count_known
    assert second.artwork.page_count == 1 and second.author_id == "u2"
    assert library.read_bytes() == before


def test_novel_ui_id_keeps_raw_sqlite_view_history_join(tmp_path):
    path = tmp_path / "novel.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE works (work_id TEXT PRIMARY KEY,title TEXT,page_count INTEGER,
                tags TEXT,user_id TEXT,is_deleted INTEGER DEFAULT 0,content_type TEXT,
                is_bookmarked INTEGER DEFAULT 0);
            CREATE TABLE following_users (user_id TEXT PRIMARY KEY,name TEXT);
            CREATE TABLE discovery_view_history (work_id TEXT PRIMARY KEY,open_count INTEGER);
            INSERT INTO works VALUES ('17','Novel',NULL,'[]','author',0,'novel',1);
            INSERT INTO works VALUES ('18','Illustration',2,'[]','author',0,'illust',1);
            INSERT INTO discovery_view_history VALUES ('17',6);
            INSERT INTO discovery_view_history VALUES ('18',3);
        """)
    with ReadonlyLibraryRepository(path) as repo:
        snap = repo.library_snapshot()
        records, local = repo.bookmark_bundle()
    assert [work.work_id for work in snap.works] == ["novel:17", "18"]
    assert snap.authors[0].view_count == 9
    assert [work.work_type for work in snap.works] == ["novel", "illust"]
    assert snap.works[0].page_count == 1 and not snap.works[0].page_count_known
    assert records is None
    assert [work.work_id for work in local] == ["novel:17", "18"]


def test_snapshot_does_not_mask_reader_failure_as_empty(library):
    with ReadonlyLibraryRepository(library) as repo:
        repo.close()
        with pytest.raises(RuntimeError, match="closed"):
            repo.library_snapshot()


def test_snapshot_uses_legacy_optional_columns_and_preserves_zero_information(tmp_path):
    path = tmp_path / "extended.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE works (work_id TEXT PRIMARY KEY,title TEXT,page_count INTEGER,
                tags TEXT,user_id TEXT,is_deleted INTEGER DEFAULT 0,
                last_backup TEXT,content_type TEXT,view_count INTEGER,work_meta TEXT);
            CREATE TABLE following_users (user_id TEXT PRIMARY KEY,name TEXT);
            CREATE TABLE discovery_view_history (work_id TEXT, open_count INTEGER);
            CREATE TABLE library_capacity_cache (user_id TEXT, total_bytes INTEGER);
            INSERT INTO library_capacity_cache VALUES ('same-id',4096);
            INSERT INTO following_users VALUES ('same-id','最新の表示名');
            INSERT INTO works VALUES ('old','作品1',NULL,'[]','same-id',0,
                NULL,'ugoira',NULL,'{}');
            INSERT INTO works VALUES ('new','作品2',3,'["青"]','same-id',0,
                '2026-10-22','manga',7,'{}');
            INSERT INTO discovery_view_history VALUES ('old',2);
            INSERT INTO discovery_view_history VALUES ('new',5);
        """)
    with ReadonlyLibraryRepository(path) as repo:
        snap = repo.library_snapshot()
    assert snap.authors[0].author_id == "same-id"
    assert snap.authors[0].name == "最新の表示名"
    assert snap.authors[0].work_count == 2
    assert snap.authors[0].representative.work_id == "new"
    assert snap.authors[0].view_count == 7
    assert snap.authors[0].capacity_bytes == 4096
    assert [w.page_count_known for w in snap.works] == [False, True]
    assert [w.work_type for w in snap.works] == ["ugoira", "manga"]


def test_null_legacy_author_id_keeps_original_empty_group(tmp_path):
    from pixivvault_nicegui.library_state import LibraryState

    path = tmp_path / "missing_author.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE works (work_id TEXT PRIMARY KEY,title TEXT,page_count INTEGER,
                tags TEXT,user_id TEXT,is_deleted INTEGER DEFAULT 0);
            CREATE TABLE following_users (user_id TEXT PRIMARY KEY,name TEXT);
            INSERT INTO works VALUES ('orphan','作品',1,'[]',NULL,0);
        """)
    with ReadonlyLibraryRepository(path) as repo:
        state = LibraryState(repo.library_snapshot())
    assert state.open_author("")
    assert [page.key for page in state.visible_pages] == [("orphan", 0)]


def test_optional_broken_history_and_capacity_do_not_erase_required_works(tmp_path):
    path = tmp_path / "optional.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE works (work_id TEXT PRIMARY KEY,title TEXT,page_count INTEGER,
                tags TEXT,user_id TEXT,is_deleted INTEGER DEFAULT 0);
            CREATE TABLE following_users (user_id TEXT PRIMARY KEY,name TEXT);
            INSERT INTO works VALUES ('12','still visible',2,'["ok"]','31',0);
            CREATE TABLE discovery_view_history (work_id TEXT);
            CREATE TABLE library_capacity_cache (user_id TEXT);
        """)
    with ReadonlyLibraryRepository(path) as repo:
        snap = repo.library_snapshot()
    assert [work.work_id for work in snap.works] == ["12"]
    assert snap.authors[0].view_count == 0
    assert snap.authors[0].capacity_bytes is None


def test_required_read_failure_cannot_be_mistaken_for_empty(tmp_path):
    path = tmp_path / "required.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE works (work_id TEXT PRIMARY KEY,title TEXT,page_count INTEGER,
                tags TEXT,user_id TEXT,is_deleted INTEGER DEFAULT 0);
            CREATE TABLE following_users (user_id TEXT PRIMARY KEY,name TEXT);
            INSERT INTO works VALUES ('12','present',2,'[]','31',0);
        """)
    with ReadonlyLibraryRepository(path) as repo:
        # The writer changes schema after the reader is opened. Required SELECT
        # must raise and not look like a legitimate empty library.
        with sqlite3.connect(path) as writer:
            writer.execute("ALTER TABLE works RENAME COLUMN is_deleted TO removed")
        with pytest.raises(sqlite3.OperationalError):
            repo.library_snapshot()


def test_tags_and_sqlite_ids_preserve_legacy_types_without_crashing(tmp_path):
    path = tmp_path / "mixed.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE works (work_id TEXT PRIMARY KEY,title TEXT,page_count INTEGER,
                tags TEXT,user_id TEXT,is_deleted INTEGER DEFAULT 0);
            CREATE TABLE following_users (user_id TEXT PRIMARY KEY,name TEXT);
            INSERT INTO works VALUES (123,'numeric id',NULL,'["one",3,null,true]','0',0);
            INSERT INTO works VALUES ('456','broken tags',1,'{broken}',NULL,0);
        """)
    with ReadonlyLibraryRepository(path) as repo:
        snap = repo.library_snapshot()
        listed = repo.list_artworks()
    assert [(work.work_id, work.artwork.tags) for work in snap.works] == [
        ("123", ("one",)), ("456", ())]
    assert [art.tags for art in listed] == [("one",), ()]
    assert snap.works[1].author_id == ""
    assert not snap.works[0].page_count_known


def test_wal_two_readers_and_writer_reopen_consistent_snapshots(tmp_path):
    path = tmp_path / "concurrent.db"
    writer = sqlite3.connect(path, timeout=2)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.executescript("""
            CREATE TABLE works (work_id TEXT PRIMARY KEY,title TEXT,page_count INTEGER,
                tags TEXT,user_id TEXT,is_deleted INTEGER DEFAULT 0);
            CREATE TABLE following_users (user_id TEXT PRIMARY KEY,name TEXT);
            CREATE TABLE discovery_view_history (work_id TEXT PRIMARY KEY,open_count INTEGER);
            INSERT INTO works VALUES ('100','first',2,'[]','31',0);
            INSERT INTO discovery_view_history VALUES ('100',4);
        """)
        writer.commit()
        with ReadonlyLibraryRepository(path) as first, ReadonlyLibraryRepository(path) as second:
            def snapshot(repo):
                snap = repo.library_snapshot()
                return tuple(w.work_id for w in snap.works), snap.authors[0].view_count
            with ThreadPoolExecutor(max_workers=2) as pool:
                before = list(pool.map(snapshot, (first, second)))
            assert before == [(("100",), 4), (("100",), 4)]
            writer.execute("BEGIN IMMEDIATE")
            writer.execute("INSERT INTO works VALUES ('101','second',1,'[]','31',0)")
            writer.execute("UPDATE discovery_view_history SET open_count=9")
            # Both readers see the old committed view before the writer commits.
            assert snapshot(first) == (("100",), 4)
            writer.commit()
            with ThreadPoolExecutor(max_workers=2) as pool:
                after = list(pool.map(snapshot, (first, second)))
            assert after == [(("100", "101"), 9), (("100", "101"), 9)]
    finally:
        writer.close()


def _bookmark_db(tmp_path):
    path = tmp_path / "bookmarks.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE works (work_id TEXT PRIMARY KEY,title TEXT,page_count INTEGER,
                tags TEXT,user_id TEXT,is_deleted INTEGER DEFAULT 0,
                content_type TEXT,is_bookmarked INTEGER DEFAULT 0);
            CREATE TABLE following_users (user_id TEXT PRIMARY KEY,name TEXT);
            CREATE TABLE bookmark_remote_cache (
                work_id TEXT PRIMARY KEY,is_novel INTEGER NOT NULL DEFAULT 0,
                title TEXT,user_id TEXT,user_name TEXT,tags TEXT,create_date TEXT,
                thumb_url TEXT,rest_type TEXT,x_restrict INTEGER);
            INSERT INTO following_users VALUES ('31','Saved Author');
            INSERT INTO works VALUES ('11','local comic',3,'["local"]','31',0,'manga',1);
            INSERT INTO works VALUES ('12','local novel',NULL,'["fiction"]','31',0,'novel',1);
            INSERT INTO works VALUES ('13','local animation',2,'[]','31',0,'ugoira',0);
            INSERT INTO bookmark_remote_cache VALUES
                ('11',0,'cached comic','31','Remote', '["tag"]','2026-01-01',
                    'https://i.pximg.net/comic.png','show',1);
            INSERT INTO bookmark_remote_cache VALUES
                ('12',1,'cached novel','31','Remote', '["fiction"]','2026-01-02',
                    'https://i.pximg.net/novel.png','hide',NULL);
            INSERT INTO bookmark_remote_cache VALUES
                ('13',0,'cached animation','31','Remote', '[]','2026-01-03',
                    'https://i.pximg.net/anim.png','show',0);
        """)
    return path


def test_bookmark_cache_metadata_namespaces_and_projection(tmp_path):
    path = _bookmark_db(tmp_path)
    original = path.read_bytes()
    with ReadonlyLibraryRepository(path) as repo:
        records = repo.bookmark_records()
        remote, local = repo.bookmark_snapshots()
    assert records is not None and all(isinstance(row, RemoteBookmark) for row in records)
    assert [(r.work_id, r.visibility, r.illustration_type, r.page_count) for r in records] == [
        ("11", "show", 1, 3), ("novel:12", "hide", None, 1), ("13", "show", 2, 2)]
    assert [r.page_count_known for r in records] == [True, True, True]
    assert records[1].legacy_identity == ("12", True)
    assert records[0].x_restrict == 1 and records[1].x_restrict == 0
    assert [art.work_id for art in remote] == ["11", "novel:12", "13"]
    assert [art.work_id for art in local] == ["11", "novel:12"]
    assert remote[0].tags == ("tag",)
    assert path.read_bytes() == original


def test_bookmark_bundle_is_the_source_of_both_compatible_projections(tmp_path):
    path = _bookmark_db(tmp_path)
    with ReadonlyLibraryRepository(path) as repo:
        bundle_records, bundle_local = repo.bookmark_bundle()
        assert bundle_records == repo.bookmark_records()
        projection_remote, projection_local = repo.bookmark_snapshots()
    assert bundle_records is not None
    assert projection_remote == tuple(record.as_artwork() for record in bundle_records)
    assert projection_local == bundle_local
    assert [item.work_id for item in bundle_local] == ["11", "novel:12"]


def test_bookmark_bundle_keeps_cache_and_local_badges_in_one_wal_version(tmp_path):
    path = _bookmark_db(tmp_path)
    writer = sqlite3.connect(path, isolation_level=None)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        with ReadonlyLibraryRepository(path) as repo:
            triggered = False

            def update_between_reads(statement):
                nonlocal triggered
                if triggered or not statement.startswith("SELECT w.work_id,w.title"):
                    return
                triggered = True
                writer.execute("BEGIN IMMEDIATE")
                writer.execute("UPDATE bookmark_remote_cache SET title='new cache' WHERE work_id='11'")
                writer.execute("UPDATE works SET title='new local' WHERE work_id='11'")
                writer.execute("COMMIT")

            repo._conn.set_trace_callback(update_between_reads)
            old_remote, old_local = repo.bookmark_bundle()
            repo._conn.set_trace_callback(None)
            fresh_remote, fresh_local = repo.bookmark_bundle()
        assert triggered and old_remote is not None and fresh_remote is not None
        assert old_remote[0].title == "cached comic"
        assert old_local[0].title == "local comic"
        assert fresh_remote[0].title == "new cache"
        assert fresh_local[0].title == "new local"
    finally:
        writer.close()


def test_remote_cache_does_not_inherit_wrong_paged_type_from_same_raw_id(tmp_path):
    path = _bookmark_db(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE works SET page_count=7 WHERE work_id='12'")
        conn.execute("UPDATE bookmark_remote_cache SET is_novel=0 WHERE work_id='12'")
        conn.execute("UPDATE bookmark_remote_cache SET is_novel=1 WHERE work_id='11'")
    with ReadonlyLibraryRepository(path) as repo:
        records, local = repo.bookmark_bundle()
    assert records is not None
    by_id = {record.work_id: record for record in records}
    # Two independent Pixiv ID namespaces; neither can borrow the other's DB metadata.
    assert by_id["12"].page_count == 1 and by_id["12"].illustration_type == 0
    assert not by_id["12"].page_count_known
    assert by_id["novel:11"].page_count == 1
    assert by_id["novel:11"].page_count_known
    assert [art.work_id for art in local] == ["11", "novel:12"]


def test_unknown_remote_page_count_does_not_claim_a_known_single_page(tmp_path):
    path = _bookmark_db(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute("""INSERT INTO bookmark_remote_cache VALUES
            ('99',0,'remote only','31','Remote','[]','2026-01-04',
             'https://i.pximg.net/remote.png','show',0)""")
    with ReadonlyLibraryRepository(path) as repo:
        records, local = repo.bookmark_bundle()
    assert records is not None
    remote_only = next(item for item in records if item.work_id == "99")
    assert remote_only.page_count == 1 and not remote_only.page_count_known
    assert remote_only.as_artwork().page_count == 1
    assert "99" not in [art.work_id for art in local]


def test_cache_absent_vs_empty_and_bad_cache_refuses_false_empty(tmp_path):
    path = tmp_path / "no-cache.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE works (work_id TEXT,title TEXT,page_count INTEGER,
                tags TEXT,user_id TEXT,is_deleted INTEGER);
            CREATE TABLE following_users (user_id TEXT,name TEXT);
        """)
    with ReadonlyLibraryRepository(path) as repo:
        assert repo.bookmark_bundle() == (None, ())
        assert repo.bookmark_records() is None
        assert repo.bookmark_snapshots() == (None, ())
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE bookmark_remote_cache (
            work_id TEXT,is_novel INTEGER,title TEXT,user_id TEXT,user_name TEXT,
            tags TEXT,create_date TEXT,thumb_url TEXT,rest_type TEXT,x_restrict INTEGER)""")
    with ReadonlyLibraryRepository(path) as repo:
        assert repo.bookmark_bundle() == ((), ())
        assert repo.bookmark_records() == ()
        assert repo.bookmark_snapshots() == ((), ())


def test_remote_records_still_read_when_local_bookmark_marker_column_is_broken(tmp_path):
    path = _bookmark_db(tmp_path)
    with ReadonlyLibraryRepository(path) as repo:
        with sqlite3.connect(path) as writer:
            writer.execute("ALTER TABLE works RENAME COLUMN is_bookmarked TO renamed")
        # The cache is a separate source of truth and needs no local badge query.
        assert [row.work_id for row in repo.bookmark_records()] == [
            "11", "novel:12", "13"]
        assert repo.bookmark_snapshots()[1] == ()


@pytest.mark.parametrize("field,value", [
    ("thumb_url", "http://i.pximg.net/unsafe.png"),
    ("thumb_url", "https://i.pximg.net.evil.example/a.png"),
    ("thumb_url", "https://i.pximg.net/a.png#fragment"),
    ("work_id", "invalid-id"),
    ("is_novel", 4),
    ("rest_type", "unknown"),
    ("x_restrict", 8),
    ("tags", '["okay",7]'),
])
def test_bad_cached_records_fail_closed_without_http_or_writes(tmp_path, field, value):
    path = _bookmark_db(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute(f"UPDATE bookmark_remote_cache SET {field}=? WHERE work_id='11'", (value,))
    before = path.read_bytes()
    with ReadonlyLibraryRepository(path) as repo:
        with pytest.raises(ValueError):
            repo.bookmark_records()
        with pytest.raises(ValueError):
            repo.bookmark_snapshots()
    assert path.read_bytes() == before
