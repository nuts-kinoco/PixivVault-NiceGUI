"""Synthetic boundary regressions from the independent integration review."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import sqlite3
from threading import Event

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from pixivvault_nicegui.bookmark_remote_adapter import parse_bookmark
from pixivvault_nicegui.library_media_registry import LibraryImageRegistry
from pixivvault_nicegui.media import DemoImageCatalog
from pixivvault_nicegui.media_routes import register_media_routes
from pixivvault_nicegui.remote_media import RemoteBookmarkImages
from pixivvault_nicegui.services import ApplicationServices


def database(path):
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE works (work_id TEXT PRIMARY KEY,title TEXT,page_count INTEGER,
              tags TEXT,user_id TEXT,is_deleted INTEGER DEFAULT 0,last_backup TEXT,
              content_type TEXT,is_bookmarked INTEGER DEFAULT 0);
            CREATE TABLE following_users (user_id TEXT PRIMARY KEY,name TEXT);
            CREATE TABLE bookmark_remote_cache (work_id TEXT,is_novel INTEGER,title TEXT,
              user_id TEXT,user_name TEXT,tags TEXT,create_date TEXT,thumb_url TEXT,
              rest_type TEXT,x_restrict INTEGER);
            INSERT INTO following_users VALUES ('12','作者');
            INSERT INTO works VALUES ('202','local novel',1,'[]','12',0,NULL,'novel',1);
        """)


class Images:
    def __init__(self):
        self.calls = 0
    def get_image_bytes(self, url):
        self.calls += 1
        out = BytesIO()
        Image.new("RGB", (4, 4), "red").save(out, "PNG")
        return out.getvalue()
    def get_artwork_pages(self, work_id):
        return ("https://i.pximg.net/test.png",)
    def close(self):
        pass


def record():
    return parse_bookmark(dict(id="202", title="remote illustration", user_id="12",
        user_name="author", tags=[], thumb_url="https://i.pximg.net/test.png"),
        kind="illustration", visibility="show")


def test_optional_corrupt_bookmark_cache_does_not_prevent_downloaded_library_start(tmp_path):
    db = tmp_path / "library.db"
    images = tmp_path / "images"
    images.mkdir()
    database(db)
    with sqlite3.connect(db) as writer:
        writer.execute("INSERT INTO bookmark_remote_cache VALUES "
                       "('203',0,'bad cache','12','author','[]','',"
                       "'http://i.pximg.net/not-secure.png','show',0)")
    original = db.read_bytes()
    app = ApplicationServices(library_readonly_db=db, library_image_root=images)
    try:
        assert len(app.library_snapshot().works) == 1
        assert app.bookmark_cache_failed
        assert app.bookmark_remote_snapshot() is None
    finally:
        app.close()
    assert db.read_bytes() == original


def test_local_novel_does_not_shadow_remote_illustration_with_same_numeric_id(tmp_path):
    db = tmp_path / "library.db"
    folder = tmp_path / "images"
    folder.mkdir()
    database(db)
    fake = Images()
    app = ApplicationServices(library_readonly_db=db, library_image_root=folder, live_client=fake)
    try:
        assert app.library_snapshot().works[0].work_id == "novel:202"
        app._publish_images(app.library_snapshot(), (record(),))
        pin = app.acquire_library_images()
        assert pin.can_open("202")
        assert not pin.can_open("novel:202")  # No false action for unsupported local novel text.
        assert app.images.get_versioned(pin.token, "202", 0, "view").data
        assert fake.calls == 1
        pin.close()
    finally:
        app.close()


def test_remote_work_requires_image_snapshot_token_and_head_has_no_network_effect():
    fake = Images()
    registry = LibraryImageRegistry(DemoImageCatalog(()))
    registry.publish(None, RemoteBookmarkImages(fake, (record(),)))
    pin = registry.acquire()
    app = FastAPI()
    register_media_routes(app, registry)
    try:
        with TestClient(app) as client:
            for suffix in ("", "?catalog=", "?catalog=wrong"):
                assert client.get("/media/202/0/view.png" + suffix).status_code == 404
                assert client.head("/media/202/0/view.png" + suffix).status_code == 404
            assert fake.calls == 0
            url = pin.url("202")
            assert client.head(url).status_code == 503
            assert fake.calls == 0
            reply = client.get(url)
            assert reply.status_code == 200 and fake.calls == 1
            assert client.head(url).status_code == 200 and fake.calls == 1
            assert client.get(url, headers={"If-None-Match": reply.headers["etag"]}).status_code == 304
            assert fake.calls == 1
    finally:
        pin.close()
        registry.close()


def test_remote_close_cancels_a_request_still_waiting_for_decode_slot():
    entered, proceed = Event(), Event()
    class Gate:
        def __enter__(self):
            entered.set()
            assert proceed.wait(3)
        def __exit__(self, *_):
            pass
    fake = Images()
    remote = RemoteBookmarkImages(fake, (record(),))
    remote._decode_slots = Gate()
    with ThreadPoolExecutor() as executor:
        pending = executor.submit(remote.get, "202", 0, "thumb")
        assert entered.wait(3)
        remote.close()
        proceed.set()
        with pytest.raises(RuntimeError):
            pending.result(3)
    assert fake.calls == 0


@pytest.mark.asyncio
async def test_live_refresh_recovers_despite_corrupt_optional_disk_bookmark_cache(tmp_path):
    class Live(Images):
        def verify_identity(self):
            return "12"
        def get_bookmarked_works(self, identity, rest_type):
            if rest_type == "show":
                yield dict(id="202", title="remote illustration", user_id="12", user_name="author",
                           tags=[], thumb_url="https://i.pximg.net/test.png")
        def get_bookmarked_novels(self, identity, rest_type):
            return iter(())
    db, folder = tmp_path / "library.db", tmp_path / "images"
    folder.mkdir()
    database(db)
    with sqlite3.connect(db) as writer:
        writer.execute("INSERT INTO bookmark_remote_cache VALUES "
                       "('203',0,'bad cache','12','author','[]','',"
                       "'http://i.pximg.net/not-secure.png','show',0)")
    before = db.read_bytes()
    app = ApplicationServices(library_readonly_db=db, library_image_root=folder, live_client=Live())
    try:
        assert app.bookmark_cache_failed
        remote, local = await app.refresh_bookmark_cache()
        assert [item.work_id for item in remote] == ["202"]
        assert [item.work_id for item in local] == ["novel:202"]
        assert not app.bookmark_cache_failed
        assert app.images.get("202").data
    finally:
        app.close()
    assert db.read_bytes() == before
