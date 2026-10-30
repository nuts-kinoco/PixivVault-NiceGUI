"""H01 original last_backup author tiles using an isolated legacy DB/media copy."""

import asyncio
from datetime import datetime, timedelta
import hashlib
from io import BytesIO
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from zipfile import ZipFile, ZipInfo

from nicegui import Client, ui
from PIL import Image
import pytest

from pixivvault_nicegui.desktop_integration import DesktopIntegration
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.viewer_backup_view import ViewerBackupView
from pixivvault_nicegui.ui.shell import DemoShell


def png(color="blue"):
    buffer = BytesIO()
    Image.new("RGB", (32, 30), color).save(buffer, format="PNG")
    return buffer.getvalue()


def legacy_fixture(tmp_path, works, names, *, with_last_backup=True):
    db = tmp_path / "old.sqlite"
    images = tmp_path / "old-images"
    images.mkdir()
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE following_users(user_id TEXT PRIMARY KEY,name TEXT)")
        column = ",last_backup TEXT" if with_last_backup else ""
        conn.execute("CREATE TABLE works(work_id TEXT PRIMARY KEY,title TEXT,"
                     "page_count INTEGER,tags TEXT,user_id TEXT,is_deleted INTEGER,"
                     "content_type TEXT" + column + ")")
        conn.executemany("INSERT INTO following_users VALUES(?,?)", names)
        fields = "work_id,title,page_count,tags,user_id,is_deleted,content_type"
        if with_last_backup:
            fields += ",last_backup"
        conn.executemany(f"INSERT INTO works({fields}) VALUES("
                         + ",".join("?" * (8 if with_last_backup else 7)) + ")", works)
    return db, images


def fingerprint(paths):
    return {str(path): (hashlib.sha256(path.read_bytes()).hexdigest(),
                        path.stat().st_mtime_ns)
            for path in paths}


@pytest.mark.asyncio
async def test_h01_sorted_legacy_author_tiles_folder_zip_missing_novel_and_safe_open(tmp_path):
    works = [
        ("101", "newest missing", 1, "[]", "12", 0, "illust", "2026-10-28T20:00:00"),
        ("102", "older actual", 2, "[]", "12", 0, "manga", "2026-10-27T20:00:00"),
        ("201", "zip picture", 1, "[]", "22", 0, "illust", "2026-10-26T20:00:00"),
        ("301", "novel only", 1, "[]", "33", 0, "novel", "2026-10-29T20:00:00"),
        ("401", "no image", 1, "[]", "44", 0, "illust", "2026-10-25T20:00:00"),
        ("501", "not backed up", 1, "[]", "55", 0, "illust", None),
    ]
    db, images = legacy_fixture(tmp_path, works,
                                 [(str(i), f"author{i}") for i in (12, 22, 33, 44, 55)])
    nested = images / "☆ブックマーク(999)" / "Alice(12)"
    nested.mkdir(parents=True)
    page = nested / "older actual(102)_2.png"
    page.write_bytes(png("blue"))
    zip_path = images / "Bob(22).zip"
    with ZipFile(zip_path, "w") as archive:
        archive.writestr("zip picture(201)_1.png", png("yellow"))
    before = fingerprint((db, page, zip_path))
    opened = []
    desktop = DesktopIntegration(allowed_roots=(images,), environment={},
                                 launcher=SimpleNamespace(open_default=opened.append))
    services = ApplicationServices(library_readonly_db=db, library_image_root=images, desktop=desktop)
    try:
        assert services.legacy_backup_history_available
        assert services.save_exports is None
        rows, unavailable, pinned = await services.recent_legacy_backup_authors()
        try:
            assert [row.author_id for row in rows] == ["12", "22"]
            assert unavailable == 2  # novel-only and missing media consume top-60 slots
            assert rows[0].last_backup == "2026-10-28T20:00:00"
            assert rows[0].work_id == "102" and rows[0].page_no == 1
            assert rows[0].target == nested
            assert rows[1].work_id == "201" and rows[1].target == images
            assert all("/media/" in row.thumbnail and "?catalog=" in row.thumbnail
                       for row in rows)
            assert services.images.get("102", 1, "thumb").media_type == "image/png"
            assert services.images.get("201", 0, "thumb").media_type == "image/png"
            await services.open_legacy_backup_author(rows[0], pinned)
            await services.open_legacy_backup_author(rows[1], pinned)
            assert opened == [nested, images]
            bad_selection = SimpleNamespace(**{slot: getattr(rows[1], slot)
                                             for slot in rows[1].__dataclass_fields__})
            with pytest.raises(ValueError, match="invalid backup selection"):
                await services.open_legacy_backup_author(bad_selection, pinned)
        finally:
            pinned.close()
    finally:
        services.close()
    assert fingerprint((db, page, zip_path)) == before
    assert not list(tmp_path.glob("old.sqlite-*"))


@pytest.mark.asyncio
async def test_h01_top_60_are_selected_before_missing_media_not_refilled(tmp_path):
    base = datetime(2026, 10, 28)
    works = [
        (str(1000+i), f"saved{i}", 1, "[]", str(i), 0, "illust",
         (base+timedelta(minutes=i)).isoformat(timespec="seconds"))
        for i in range(1, 63)
    ]
    db, images = legacy_fixture(tmp_path, works,
                                 [(str(i), f"Author{i}") for i in range(1, 63)])
    for i in range(1, 62):
        folder = images / f"Author{i}({i})"
        folder.mkdir()
        (folder / f"saved{i}({1000+i})_1.png").write_bytes(png())
    services = ApplicationServices(library_readonly_db=db, library_image_root=images)
    try:
        rows, unavailable, pinned = await services.recent_legacy_backup_authors()
        try:
            assert len(rows) == 59 and unavailable == 1
            assert [r.author_id for r in rows] == [str(i) for i in range(61, 2, -1)]
            assert all(r.work_id == str(1000+int(r.author_id)) for r in rows)
            assert rows[-1].author_id == "3"  # author 2 was outside the original limit
        finally:
            pinned.close()
    finally:
        services.close()


@pytest.mark.asyncio
async def test_h01_empty_and_unbacked_up_are_not_synthetic_history(tmp_path):
    db, images = legacy_fixture(
        tmp_path,
        [("501", "only undated", 1, "[]", "55", 0, "illust")],
        [("55", "No backup")], with_last_backup=False)
    folder = images / "No backup(55)"
    folder.mkdir()
    (folder / "only undated(501)_1.png").write_bytes(png())
    services = ApplicationServices(library_readonly_db=db, library_image_root=images)
    try:
        rows, unavailable, pinned = await services.recent_legacy_backup_authors()
        try:
            assert rows == () and unavailable == 0
            assert services.recent_viewer_backups is not None
        finally:
            pinned.close()
    finally:
        services.close()


@pytest.mark.asyncio
async def test_h01_chooses_newest_verified_folder_image_and_name_fallback(tmp_path):
    db, images = legacy_fixture(tmp_path, [
        ("710", "last backed up", 1, "[]", "90", 0, "illust", "2026-10-28"),
        ("711", "newest physical image", 2, "[]", "90", 0, "manga", "2026-10-27"),
    ], names=[])
    folder = images / "StoredAuthor(90)"
    folder.mkdir()
    old = folder / "last backed up(710)_1.png"
    newest = folder / "newest physical image(711)_2.png"
    old.write_bytes(png("red"))
    newest.write_bytes(png("green"))
    os.utime(old, ns=(1000000000, 1000000000))
    os.utime(newest, ns=(2000000000, 2000000000))
    services = ApplicationServices(library_readonly_db=db, library_image_root=images)
    try:
        rows, unavailable, pinned = await services.recent_legacy_backup_authors()
        try:
            assert len(rows) == 1 and unavailable == 0
            assert rows[0].author_name == "StoredAuthor"
            assert rows[0].last_backup == "2026-10-28"
            assert rows[0].work_id == "711" and rows[0].page_no == 1
            assert rows[0].target == folder
        finally:
            pinned.close()
    finally:
        services.close()


@pytest.mark.asyncio
async def test_h01_zip_entry_date_and_middle_page_choose_actual_newest_image(tmp_path):
    db, images = legacy_fixture(tmp_path, [
        ("901", "three pages", 3, "[]", "99", 0, "illust", "2026-10-28"),
    ], [("99", "Zipped Author")])
    archive = images / "Zipped Author(99).zip"
    with ZipFile(archive, "w") as zipped:
        for page, date in ((1, (2026, 1, 1, 12, 0, 0)),
                           (2, (2026, 10, 28, 12, 0, 0)),
                           (3, (2026, 2, 1, 12, 0, 0))):
            info = ZipInfo(f"three pages(901)_{page}.png", date_time=date)
            zipped.writestr(info, png())
    before = fingerprint((db, archive))
    services = ApplicationServices(library_readonly_db=db, library_image_root=images)
    try:
        rows, unavailable, pinned = await services.recent_legacy_backup_authors()
        try:
            assert len(rows) == 1 and unavailable == 0
            assert rows[0].work_id == "901" and rows[0].page_no == 1
            assert rows[0].target == images
            assert pinned.get("901", 1, "thumb").media_type == "image/png"
        finally:
            pinned.close()
    finally:
        services.close()
    assert fingerprint((db, archive)) == before


@pytest.mark.asyncio
async def test_h01_view_exposes_verified_tiles_and_closes_stale_click(tmp_path):
    db, images = legacy_fixture(
        tmp_path,
        [("101", "actual", 1, "[]", "12", 0, "illust", "2026-10-28")],
        [("12", "Alice")])
    folder = images / "Alice(12)"
    folder.mkdir()
    (folder / "actual(101)_1.png").write_bytes(png())
    opened=[]
    services = ApplicationServices(library_readonly_db=db, library_image_root=images,
        desktop=DesktopIntegration(allowed_roots=(images,),environment={},
                                   launcher=SimpleNamespace(open_default=opened.append)))
    client = Client(ui.page('/_h01_backup_history'))
    try:
        with client:
            view = ViewerBackupView(services)
            assert view.legacy_mode and view._images is None
            view.active = True
            await view.refresh()
            assert [r.author_id for r in view.rows] == ['12']
            assert len(view.openers) == 1
            opener = view.openers['12']
            assert opener.props['data-testid'] == 'backup-open-12'
            handler = next(e.handler for e in opener._event_listeners.values() if e.type == 'click')
            await handler(None)
            assert opened == [folder]
            view.set_active(False)
            assert view._images is None
            await handler(None)
            assert opened == [folder]
            view.close()
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_h01_shell_exposes_original_history_with_only_readonly_legacy(tmp_path, monkeypatch):
    from pixivvault_nicegui.ui import viewer_backup_view
    # ui.run installs NiceGUI's background core loop. The isolated Client
    # test uses the live pytest loop for the very same refresh coroutine.
    tasks = []
    def run_background(coroutine):
        task = asyncio.create_task(coroutine)
        tasks.append(task)
        return task
    monkeypatch.setattr(viewer_backup_view.background_tasks, 'create', run_background)
    db, images = legacy_fixture(tmp_path, [
        ("101", "actual", 1, "[]", "12", 0, "illust", "2026-10-28")
    ], [("12", "Alice")])
    folder = images / "Alice(12)"
    folder.mkdir()
    (folder / "actual(101)_1.png").write_bytes(png())
    services = ApplicationServices(library_readonly_db=db, library_image_root=images)
    client = Client(ui.page('/_h01_shell_readonly'))
    try:
        with client:
            shell = DemoShell(services)
            assert shell.buttons['history'].enabled
            assert shell.pixiv_home.text == 'Pixivを開く'
            shell.show('history')
            assert shell.active_tab == 'history'
            assert shell.backup_view is not None and shell.backup_view.legacy_mode
            for _ in range(100):
                if shell.backup_view.rows:
                    break
                await asyncio.sleep(.01)
            assert [row.author_id for row in shell.backup_view.rows] == ['12']
            shell.close()
    finally:
        if tasks:
            await asyncio.gather(*tasks)
        client.delete()
        services.close()
