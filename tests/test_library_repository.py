import sqlite3

import pytest

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
        INSERT INTO works VALUES ('02','second',NULL,'invalid','unknown',0);
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
        assert (b.author, b.page_count, b.tags) == ("unknown", 1, ())
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
    finally:
        writer.close()
