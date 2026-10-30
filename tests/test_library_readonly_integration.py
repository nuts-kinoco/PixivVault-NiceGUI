"""Isolated old-schema SQLite + synthetic PNG end-to-end service boundary."""

from io import BytesIO
from pathlib import Path
import sqlite3

from PIL import Image
import pytest

from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.bootstrap import main, parse_args


def test_explicit_readonly_library_services_and_demo_isolation(tmp_path):
    db = tmp_path / "test-old-schema.sqlite"
    with sqlite3.connect(db) as con:
        con.executescript("""
          CREATE TABLE works (work_id TEXT PRIMARY KEY,title TEXT,page_count INTEGER,
            tags TEXT,user_id TEXT,is_deleted INTEGER DEFAULT 0,last_backup TEXT);
          CREATE TABLE following_users (user_id TEXT PRIMARY KEY,name TEXT);
          INSERT INTO following_users VALUES ('12','架空保存作者');
          INSERT INTO works VALUES ('101','架空作品',2,'[]','12',0,'2026-10-22');
        """)
    images = tmp_path / "fake-images"
    folder = images / "架空保存作者(12)"
    folder.mkdir(parents=True)
    for page, color in ((1, "red"), (2, "blue")):
        Image.new("RGB", (28, 24), color).save(folder / f"架空作品(101)_{page}.png")
    before = db.read_bytes()
    services = ApplicationServices(library_readonly_db=db, library_image_root=images)
    try:
        assert len(services.library_snapshot().authors) == 1
        assert services.library_snapshot().authors[0].author_id == "12"
        assert services.images.get("101", 1, "view").media_type == "image/png"
        # The separate ranking/learning fixture catalog is unchanged.
        assert services.artworks[0].work_id == "demo-0001"
        assert services.images.get("demo-0001", 0, "thumb").media_type == "image/png"
    finally:
        services.close()
    assert db.read_bytes() == before
    with pytest.raises(RuntimeError):
        services.library_repository.library_snapshot()


def test_explicit_pair_required_and_check_does_not_open_files(tmp_path, monkeypatch):
    db = tmp_path / "does-not-exist.sqlite"
    with pytest.raises(SystemExit):
        parse_args(["--library-readonly-db", str(db)])
    arguments = ["--check", "--library-readonly-db", str(db),
                 "--library-image-root", str(tmp_path)]
    # Preflight now requires an actual standalone frozen source; it must not
    # create a missing file or relax the source guard merely for --check.
    with pytest.raises(SystemExit):
        parse_args(arguments)
    assert not db.exists()
    with pytest.raises(ValueError, match="both explicit"):
        ApplicationServices(library_readonly_db=db)
    connection = sqlite3.connect(db)
    try:
        connection.execute('CREATE TABLE synthetic_fixture(value TEXT)')
        connection.commit()
    finally:
        connection.close()
    before = db.read_bytes(), db.stat().st_mtime_ns
    def reject_sqlite_open(*_args, **_kwargs):
        pytest.fail('--check must not open a SQLite connection')
    monkeypatch.setattr(sqlite3, 'connect', reject_sqlite_open)
    config = parse_args(arguments)
    assert config.check_only and config.library_readonly_db == db
    assert main(arguments) == 0
    assert (db.read_bytes(), db.stat().st_mtime_ns) == before
    assert not list(tmp_path.glob(db.name + '-*'))
