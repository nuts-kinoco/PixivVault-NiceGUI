"""Opted-in synthetic legacy SQLite + saved PNG end-to-end Edge/Chromium check."""

from __future__ import annotations

import argparse
from io import BytesIO
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

from PIL import Image
from playwright.sync_api import expect, sync_playwright

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.run_browser_checks import free_port, wait_for_health, stop_child, port_released, child_environment


def build_fixture(path: Path) -> tuple[Path, Path]:
    db = path / "synthetic-legacy.sqlite"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
            CREATE TABLE works (work_id TEXT PRIMARY KEY,title TEXT,page_count INTEGER,
              tags TEXT,user_id TEXT,is_deleted INTEGER DEFAULT 0,last_backup TEXT);
            CREATE TABLE following_users (user_id TEXT PRIMARY KEY,name TEXT);
            INSERT INTO following_users VALUES ('12','架空保存作者');
            INSERT INTO works VALUES ('101','架空作品',2,'["診断用","保存済み"]','12',0,'2026-10-22');
            ALTER TABLE works ADD COLUMN content_type TEXT DEFAULT 'illust';
            ALTER TABLE works ADD COLUMN is_bookmarked INTEGER DEFAULT 1;
            CREATE TABLE bookmark_remote_cache (work_id TEXT PRIMARY KEY,is_novel INTEGER,
                title TEXT,user_id TEXT,user_name TEXT,tags TEXT,create_date TEXT,
                thumb_url TEXT,rest_type TEXT,x_restrict INTEGER);
            INSERT INTO bookmark_remote_cache VALUES ('101',0,'架空作品','12','架空保存作者',
                '["診断用","保存済み"]','2026-10-22','https://i.pximg.net/synthetic/101.png','show',0);
        """)
    root = path / "fake-images"
    folder = root / "架空保存作者(12)"
    folder.mkdir(parents=True)
    for page_no, color in ((1, "red"), (2, "blue")):
        Image.new("RGB", (640, 427), color).save(folder / f"架空作品(101)_{page_no}.png")
    return db, root


def check(page, url: str, output: Path, width: int, height: int) -> dict:
    page.goto(url, wait_until="domcontentloaded")
    page.get_by_test_id("tab-library").click()
    library = page.get_by_test_id("library-view")
    expect(library).to_have_attribute("data-active", "true")
    cards = library.locator(".pv-library-author-card")
    expect(cards).to_have_count(1)
    cards.get_by_test_id("library-author-open").click()
    pages = library.locator(".pv-library-page-card")
    expect(pages).to_have_count(2)
    expect(library.get_by_test_id("library-page-count")).to_have_text("2 / 2 枚表示")
    first = pages.locator('[data-work-id="101"][data-page-no="0"]')
    if first.count() == 0:
        first = library.locator('.pv-library-page-card[data-work-id="101"][data-page-no="0"]')
    first.click()
    viewer = page.get_by_test_id("library-viewer")
    expect(viewer).to_be_visible()
    expect(viewer.locator(".pv-counter")).to_have_text("1/2")
    img = page.get_by_test_id("library-viewer-image").locator("img:visible")
    expect(img).to_have_attribute("src", __import__("re").compile(r"/media/101/0/view\.png\?catalog=[a-f0-9]+#request-"))
    img.evaluate("el => el.decode()")
    first_path = img.evaluate("el => new URL(el.currentSrc || el.src).pathname")
    assert first_path == "/media/101/0/view.png", first_path
    page.keyboard.press("ArrowRight")
    expect(viewer.locator(".pv-counter")).to_have_text("2/2")
    expect(img).to_have_attribute("src", __import__("re").compile(r"/media/101/1/view\.png\?catalog=[a-f0-9]+#request-"))
    img.evaluate("el => el.decode()")
    last_path = img.evaluate("el => new URL(el.currentSrc || el.src).pathname")
    assert last_path == "/media/101/1/view.png", last_path
    page.screenshot(path=str(output / f"readonly-viewer-{width}x{height}.png"))
    viewer.get_by_role("button", name="閉じる", exact=True).click()
    expect(first).to_be_focused()
    library.get_by_test_id("library-back").click()
    expect(cards.get_by_test_id("library-author-open")).to_be_focused()
    library.get_by_test_id("library-subview-bookmarks").click()
    bookmarks = library.get_by_test_id("bookmark-view")
    expect(bookmarks.get_by_test_id("bookmark-count")).to_have_text("1 / 1 件表示")
    expect(bookmarks.get_by_text("DL済み", exact=True)).to_be_visible()
    bookmarks.get_by_test_id("bookmark-open").click()
    expect(viewer).to_be_visible()
    expect(viewer.locator(".pv-counter")).to_have_text("1/2")
    viewer.get_by_role("button", name="閉じる", exact=True).click()
    expect(bookmarks.get_by_test_id("bookmark-open")).to_be_focused()
    return {"viewport": [width, height], "page0": first_path, "page1": last_path,
            "author_count": 1, "page_count": 2, "focus_restored": True,
            "persisted_bookmark_viewer": True}


def check_refresh(page, url: str, db: Path, images: Path) -> dict:
    page.goto(url, wait_until="domcontentloaded")
    page.get_by_test_id("tab-library").click()
    library = page.get_by_test_id("library-view")
    expect(library.locator(".pv-library-author-card")).to_have_count(1)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO works(work_id,title,page_count,tags,user_id,is_deleted,last_backup) "
                     "VALUES ('102','新作',1,'[]','12',0,'2026-10-23')")
    Image.new("RGB", (40, 30), "green").save(images / "架空保存作者(12)" / "新作(102).png")
    button = library.get_by_test_id("library-db-refresh")
    button.click()
    expect(button).to_be_enabled()
    library.get_by_test_id("library-author-open").click()
    expect(library.get_by_test_id("library-page-count")).to_have_text("3 / 3 枚表示")
    library.get_by_test_id("library-back").click()
    # A failed background read must not turn the last good snapshot into zero rows.
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE following_users")
    button.click()
    expect(library.get_by_role("alert")).to_be_visible()
    expect(library.locator(".pv-library-author-card")).to_have_count(1)
    with sqlite3.connect(db) as conn:
        conn.executescript("CREATE TABLE following_users (user_id TEXT PRIMARY KEY,name TEXT);"
                           "INSERT INTO following_users VALUES ('12','架空保存作者');")
    library.get_by_test_id("library-reload").click()
    expect(library.get_by_role("alert")).to_be_hidden()
    expect(library.locator(".pv-library-author-card")).to_have_count(1)
    return {"refresh_pages": 3, "failure_kept_cached_list": True,
            "retry_restored": True}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", choices=("msedge", "chromium"), default="msedge")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    report = {"success": False, "results": [], "port_released": False}
    with tempfile.TemporaryDirectory(prefix="pv-readonly-", dir=str(args.output.resolve()),
                                     ignore_cleanup_errors=True) as folder:
        db, images = build_fixture(Path(folder))
        before = db.read_bytes()
        port = free_port()
        process = None
        try:
            with (args.output / "app.stderr.log").open("wb") as stderr:
                process = subprocess.Popen([sys.executable, "-m", "pixivvault_nicegui",
                    "--host", "127.0.0.1", "--port", str(port),
                    "--library-readonly-db", str(db), "--library-image-root", str(images)],
                    stdout=subprocess.DEVNULL, stderr=stderr,
                    stdin=subprocess.DEVNULL, cwd=str(Path(__file__).resolve().parents[1]),
                    env=child_environment())
            url = f"http://127.0.0.1:{port}"
            wait_for_health(url, process, expected_data="library-readonly")
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    channel=args.channel if args.channel == "msedge" else None, headless=True)
                try:
                    for width, height in ((1280, 720), (1920, 1080)):
                        page = browser.new_page(viewport={"width": width, "height": height})
                        report["results"].append(check(page, url, args.output, width, height))
                        page.close()
                    if db.read_bytes() != before:
                        raise AssertionError("read-only SQLite changed before synthetic writer test")
                    refresh_page = browser.new_page(viewport={"width": 1280, "height": 720})
                    report["refresh"] = check_refresh(refresh_page, url, db, images)
                    refresh_page.close()
                finally:
                    browser.close()
            if not before:
                raise AssertionError("synthetic SQLite was empty before read")
            report["success"] = True
        except Exception as exc:
            report["error"] = f"{type(exc).__name__}: {str(exc)[:450]}"
        finally:
            stop_child(process)
            report["port_released"] = port_released(port)
            report["success"] = report["success"] and report["port_released"]
    (args.output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
