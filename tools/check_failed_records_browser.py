"""Real browser acceptance of legacy failed_queue with synthetic SQLite only."""
from __future__ import annotations

import argparse
import csv
from dataclasses import astuple
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.run_browser_checks import child_environment, free_port, port_released, source_hashes, stop_child, wait_for_health
from tools.check_readonly_library_browser import build_fixture
from pixivvault_nicegui.failed_records_fixtures import build_failed_records_fixture


def build_failure_fixture(folder: Path):
    folder.mkdir(parents=True, exist_ok=False)
    db, images = build_fixture(folder)
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE failed_queue (work_id TEXT PRIMARY KEY,user_id TEXT,title TEXT,"
                     "work_type TEXT,error_reason TEXT,failed_at TEXT,retry_count INTEGER)")
        conn.executemany("INSERT INTO failed_queue VALUES (?,?,?,?,?,?,?)",
                         [astuple(row) for row in build_failed_records_fixture()])
    from tools.failed_records_fixture_guard import write_fixture_manifest
    write_fixture_manifest(db, images)
    return db, images


def serve(port: int, db: Path, images: Path):
    from tools.failed_records_fixture_guard import verify_fixture_manifest
    verify_fixture_manifest(db, images)
    from nicegui import app, ui
    from pixivvault_nicegui.bootstrap import register_pages
    register_pages(library_readonly_db=db, library_image_root=images)

    @app.post("/_failure_acceptance/shutdown")
    async def shutdown():
        if os.environ.get("PV_FAILURE_SHUTDOWN_DIAGNOSTIC") == "1":
            import asyncio
            import faulthandler
            faulthandler.dump_traceback_later(8)
            def diagnose():
                tasks = [{"coroutine": getattr(task.get_coro(), "__qualname__", "unknown"),
                          "stack": [f"{Path(frame.f_code.co_filename).name}:{frame.f_lineno}:{frame.f_code.co_name}"
                                    for frame in task.get_stack(limit=8)]}
                         for task in asyncio.all_tasks()]
                print(json.dumps({"synthetic_shutdown_tasks": tasks}), file=sys.stderr, flush=True)
            asyncio.get_running_loop().call_later(8, diagnose)
        app.shutdown()
        return {"stopping": True}

    try:
        ui.run(host="127.0.0.1", port=port, show=False, reload=False, binding_refresh_interval=None)
    finally:
        if os.environ.get("PV_FAILURE_SHUTDOWN_DIAGNOSTIC") == "1":
            import faulthandler
            faulthandler.cancel_dump_traceback_later()


def view(page):
    from playwright.sync_api import expect
    page.get_by_test_id("tab-failed").click()
    root = page.get_by_test_id("failed-records-view")
    expect(root).to_be_visible()
    expect(root.get_by_test_id("failed-status")).to_be_hidden(timeout=10000)
    expect(root.get_by_test_id("failed-error")).to_be_hidden()
    return root


def export(page, root, output: Path, name: str):
    root.get_by_test_id("failed-menu").click()
    from playwright.sync_api import expect
    expect(page.get_by_test_id("failed-clear-all")).to_be_disabled()
    with page.expect_download(timeout=10000) as event:
        page.get_by_test_id("failed-export").click()
    download = event.value
    assert download.suggested_filename == "failed_queue_export.csv"
    destination = output / name
    download.save_as(destination)
    data = destination.read_bytes()
    assert data.startswith(b"\xef\xbb\xbf")
    reader = csv.DictReader(StringIO(data.decode("utf-8-sig")))
    assert reader.fieldnames == ["work_id", "user_id", "title", "work_type",
                                "error_reason", "failed_at", "retry_count"]
    return list(reader)


def inspect(page, url, output, width, height):
    from playwright.sync_api import expect
    page.goto(url, wait_until="domcontentloaded")
    root = view(page)
    expect(root.get_by_test_id("failed-count")).to_have_text("17 / 17 件")
    expect(root.get_by_test_id("failed-record")).to_have_count(8)
    expect(root.get_by_test_id("failed-page")).to_have_text("1 / 3")
    expect(root.get_by_test_id("failed-reason").first).to_contain_text("二行目の診断は省略せず表示します。")
    expect(root.get_by_test_id("failed-retry-all")).to_be_disabled()
    expect(root.get_by_test_id("failed-row-retry").first).to_be_disabled()
    expect(root.get_by_test_id("failed-row-delete").first).to_be_disabled()
    root.get_by_test_id("failed-next").click()
    expect(root.get_by_test_id("failed-page")).to_have_text("2 / 3")
    expect(root.get_by_test_id("failed-record").first).to_have_attribute("data-work-id", "failure-0009")
    root.get_by_test_id("failed-record").first.evaluate("el => window.__failureRow = el")
    root.evaluate("el => {el.scrollTop = 120}")
    scroll = root.evaluate("el => el.scrollTop")
    page.get_by_test_id("tab-ranking").click()
    root = view(page)
    assert root.get_by_test_id("failed-record").first.evaluate("el => el === window.__failureRow"), "tab return replaced row DOM"
    returned_scroll = root.evaluate("el => el.scrollTop")
    assert abs(returned_scroll - scroll) <= 2, f"tab scroll changed {scroll} -> {returned_scroll}"
    rows = export(page, root, output, f"all-{width}.csv")
    assert len(rows) == 17 and rows[-1]["title"] == "'=SUM(1,2)"
    expected = []
    for record in build_failed_records_fixture():
        row = {key: str(value) for key, value in zip(
            ["work_id", "user_id", "title", "work_type", "error_reason", "failed_at", "retry_count"], astuple(record))}
        if record.work_id == "failure-0017":
            row["title"] = "'=SUM(1,2)"
        expected.append(row)
    assert rows == expected, "CSV did not contain every filtered record with all original fields"
    search = root.get_by_placeholder("タイトル・IDで検索")
    search.fill("failure-0001")
    expect(root.get_by_test_id("failed-count")).to_have_text("1 / 17 件")
    expect(root.get_by_test_id("failed-category-all")).to_have_text("すべて 17")
    root.locator(".q-field__focusable-action").click()
    expect(search).to_have_value("")
    expect(root.get_by_test_id("failed-count")).to_have_text("17 / 17 件")
    root.get_by_test_id("failed-category-format").click()
    expect(root.get_by_test_id("failed-count")).to_have_text("3 / 17 件")
    expect(root.get_by_test_id("failed-page")).to_have_text("1 / 1")
    search.fill("absent")
    expect(root.get_by_test_id("failed-empty")).to_have_text("一致する失敗はありません")
    root.get_by_test_id("failed-clear-search").click()
    expect(root.get_by_test_id("failed-count")).to_have_text("3 / 17 件")
    page.get_by_test_id("tab-ranking").click()
    root = view(page)
    expect(root.get_by_test_id("failed-category-format")).to_have_attribute("aria-pressed", "true")
    filtered = export(page, root, output, f"filtered-{width}.csv")
    assert filtered == [row for row in expected if row["work_id"] in
                        {"failure-0005", "failure-0011", "failure-0017"}]
    page.screenshot(path=str(output / f"failed-{width}x{height}.png"))
    geometry = page.evaluate("""() => ({width:innerWidth, doc:document.documentElement.scrollWidth})""")
    assert geometry["doc"] <= geometry["width"] + 1
    return {"viewport": [width, height], "rows_per_page": 8,
            "export_all_filtered_not_only_page": True, "formula_neutralized": True,
            "full_reason": True, "category_search_tab_state": True,
            "same_row_dom_and_scroll_on_tab_return": True, "clear_icon_resets_query": True,
            "unconnected_writes_disabled": True, "no_horizontal_overflow": True}


def inspect_two_clients(browser, url, observe):
    from playwright.sync_api import expect
    a, b = browser.new_page(), browser.new_page()
    try:
        for page in (a, b):
            observe(page)
            page.goto(url, wait_until="domcontentloaded")
            view(page)
        a.get_by_placeholder("タイトル・IDで検索").fill("failure-0001")
        expect(a.get_by_test_id("failed-count")).to_have_text("1 / 17 件")
        expect(b.get_by_test_id("failed-count")).to_have_text("17 / 17 件")
        b.get_by_test_id("failed-next").click()
        expect(b.get_by_test_id("failed-page")).to_have_text("2 / 3")
        expect(a.get_by_test_id("failed-page")).to_have_text("1 / 1")
        return True
    finally:
        a.close(); b.close()


def inspect_refresh(page, url, db):
    from playwright.sync_api import expect
    page.goto(url, wait_until="domcontentloaded")
    root = view(page)
    root.get_by_test_id("failed-next").click()
    expect(root.get_by_test_id("failed-page")).to_have_text("2 / 3")
    with sqlite3.connect(db) as conn:
        conn.execute("ALTER TABLE failed_queue RENAME TO temporarily_unavailable")
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    root.get_by_test_id("failed-reload").click()
    expect(root.get_by_test_id("failed-error")).to_be_visible()
    expect(root.get_by_test_id("failed-count")).to_have_text("17 / 17 件")
    expect(root.get_by_test_id("failed-page")).to_have_text("2 / 3")
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    with sqlite3.connect(db) as conn:
        conn.execute("ALTER TABLE temporarily_unavailable RENAME TO failed_queue")
        conn.execute("DELETE FROM failed_queue WHERE work_id != 'failure-0001'")
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    root.get_by_test_id("failed-reload").click()
    expect(root.get_by_test_id("failed-error")).to_be_hidden()
    expect(root.get_by_test_id("failed-count")).to_have_text("1 / 1 件")
    expect(root.get_by_test_id("failed-page")).to_have_text("1 / 1")
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM failed_queue")
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    root.get_by_test_id("failed-reload").click()
    expect(root.get_by_test_id("failed-count")).to_have_text("0 / 0 件")
    expect(root.get_by_test_id("failed-empty")).to_contain_text("失敗はありません")
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    return {"failure_keeps_previous_rows_and_page": True, "retry_recovers_and_clamps_page": True,
            "empty_is_not_failure": True, "db_unchanged_outside_explicit_test_writer": True}


def acceptance_hashes():
    hashes = source_hashes()
    root = Path(__file__).resolve().parents[1]
    for name in ("check_failed_records_browser.py", "failed_records_fixture_guard.py",
                 "check_readonly_library_browser.py", "run_browser_checks.py"):
        hashes["tools/" + name] = hashlib.sha256((root / "tools" / name).read_bytes()).hexdigest()
    return hashes


def run(channel, output):
    from playwright.sync_api import sync_playwright
    output.mkdir(parents=True, exist_ok=False)
    before_source = acceptance_hashes()
    db, images = build_failure_fixture(output.resolve() / "fixture")
    db_before = hashlib.sha256(db.read_bytes()).hexdigest()
    report = {"success": False, "synthetic_only": True, "viewports": []}
    process = None
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    page_errors, console_errors, foreign = [], [], []
    try:
        with (output / "app-stderr.log").open("wb") as log:
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                "--serve", "--port", str(port), "--db", str(db), "--images", str(images)],
                cwd=Path(__file__).resolve().parents[1], env=child_environment(),
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log)
        wait_for_health(url, process, expected_data="library-readonly")
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge" if channel == "msedge" else None, headless=True)

            def observe(page):
                page.on("pageerror", lambda error: page_errors.append(type(error).__name__))
                page.on("console", lambda message: console_errors.append(message.text[:250])
                        if message.type == "error" else None)
                def guard(route):
                    parsed = urlsplit(route.request.url)
                    if parsed.scheme in {"http", "https"} and parsed.netloc != f"127.0.0.1:{port}":
                        foreign.append(parsed.hostname)
                        route.abort()
                    else:
                        route.continue_()
                page.route("**/*", guard)

            try:
                for width, height in ((1280, 720), (1920, 1080)):
                    page = browser.new_page(viewport={"width": width, "height": height}, accept_downloads=True)
                    observe(page)
                    report["viewports"].append(inspect(page, url, output, width, height))
                    page.close()
                report["two_clients_independent"] = inspect_two_clients(browser, url, observe)
                assert hashlib.sha256(db.read_bytes()).hexdigest() == db_before
                page = browser.new_page()
                observe(page)
                report["refresh"] = inspect_refresh(page, url, db)
                page.close()
                assert not foreign and not page_errors and not console_errors
            finally:
                browser.close()
        request = Request(url + "/_failure_acceptance/shutdown", data=b"", method="POST")
        with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
            assert response.status == 200 and json.loads(response.read(1024)) == {"stopping": True}
        process.wait(timeout=15)
        report["normal_exit"] = process.returncode == 0
        report["success"] = report["normal_exit"]
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {str(error)[:500]}"
    finally:
        report["forced_cleanup"] = process is not None and process.poll() is None
        stop_child(process)
        report["port_released"] = port_released(port)
        report["source_changed"] = before_source != acceptance_hashes()
        report["page_errors"], report["foreign_requests"] = page_errors, foreign
        report["console_errors"] = console_errors
        report["success"] &= (report["port_released"] and not report["source_changed"]
                              and not report["forced_cleanup"])
        (output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["success"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--images", type=Path)
    parser.add_argument("--channel", choices=["msedge", "chromium"], default="msedge")
    parser.add_argument("--output", type=Path, default=Path("artifacts/failed-records-browser"))
    args = parser.parse_args()
    if args.serve:
        serve(args.port, args.db, args.images)
    else:
        raise SystemExit(run(args.channel, args.output))
