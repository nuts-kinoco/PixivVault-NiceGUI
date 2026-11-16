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
import secrets
import sqlite3
import subprocess
import sys
import time
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
    conn.close()
    from tools.failed_records_fixture_guard import write_fixture_manifest
    write_fixture_manifest(db, images)
    return db, images


def serve(port: int, db: Path, images: Path, *, preferences=None, nonce=None, output=None):
    from tools.failed_records_fixture_guard import verify_fixture_manifest
    verify_fixture_manifest(db, images)
    from nicegui import app, ui
    from fastapi import Header, HTTPException
    from pixivvault_nicegui.bootstrap import register_pages
    if preferences is not None:
        from tools.check_library_calendar_slider_owned import owned_output, deny_external_connections
        preferences, output = owned_output(preferences), owned_output(output)
        if not nonce or not output.is_dir():
            raise ValueError('owned failure-list run required')
        network = deny_external_connections()
    else:
        network = {'denied': 0}
    register_pages(library_readonly_db=db, library_image_root=images, preferences_root=preferences)
    from pixivvault_nicegui.services import get_services
    services = get_services(app)
    stopping = False

    def authorized(value):
        if nonce is not None and not secrets.compare_digest(value or '', nonce):
            raise HTTPException(403)

    @app.get('/_failure_acceptance/owner', include_in_schema=False)
    def owner(x_acceptance: str | None = Header(default=None)):
        authorized(x_acceptance)
        return {'pid': os.getpid(), 'synthetic': True, 'local_removal': preferences is not None}

    @app.post("/_failure_acceptance/shutdown")
    async def shutdown(x_acceptance: str | None = Header(default=None)):
        nonlocal stopping
        authorized(x_acceptance)
        if stopping:
            raise HTTPException(409)
        stopping = True
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
        if output is not None:
            from tools.check_library_calendar_slider_owned import write_new
            write_new(output/'server-lifecycle.json', {
                'pid': os.getpid(), 'services_closed': services.closed,
                'shutdown_requested': stopping, 'external_denied': network['denied'],
                'original_db_sha256': hashlib.sha256(db.read_bytes()).hexdigest()})
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


def export(page, root, output: Path, name: str, *, local_removal=False):
    root.get_by_test_id("failed-menu").click()
    from playwright.sync_api import expect
    if local_removal:
        expect(page.get_by_test_id("failed-clear-all")).to_be_enabled()
    else:
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


def inspect_local_removal(context, url, output):
    """Actual click/download/confirmation/CAS actions, no fixture source writer."""
    from playwright.sync_api import expect
    page, other = context.new_page(), context.new_page()
    try:
        page.goto(url, wait_until='domcontentloaded')
        root = view(page)
        expect(root.get_by_test_id('failed-count')).to_have_text('17 / 17 件')
        root.get_by_test_id('failed-next').click()
        expect(root.get_by_test_id('failed-page')).to_have_text('2 / 3')
        rows = export(page, root, output, 'all-17.csv', local_removal=True)
        expected = []
        for record in build_failed_records_fixture():
            values = {key: str(value) for key, value in zip(
                ['work_id','user_id','title','work_type','error_reason','failed_at','retry_count'],
                astuple(record))}
            if record.work_id == 'failure-0017':
                values['title'] = "'=SUM(1,2)"
            expected.append(values)
        assert rows == expected
        expect(root.get_by_test_id('failed-status')).to_contain_text('17 件のCSVダウンロードを開始')
        root.get_by_test_id('failed-category-format').click()
        expect(root.get_by_test_id('failed-count')).to_have_text('3 / 17 件')
        filtered = export(page, root, output, 'filtered-3.csv', local_removal=True)
        assert filtered == [row for row in expected if row['work_id'] in
                            {'failure-0005','failure-0011','failure-0017'}]
        expect(root.get_by_test_id('failed-status')).to_contain_text('3 件のCSVダウンロードを開始')
        root.get_by_test_id('failed-category-all').click()
        expect(root.get_by_test_id('failed-count')).to_have_text('17 / 17 件')
        other.goto(url, wait_until='domcontentloaded')
        stale = view(other)
        # Capture a stale all17 confirmation in an independent client before
        # the first client's deletion advances the owned CAS revision.
        stale.get_by_test_id('failed-menu').click()
        other.get_by_test_id('failed-clear-all').click()
        expect(other.get_by_test_id('failed-remove-confirm-count')).to_have_text(
            '17 件の失敗記録を一覧から削除しますか？')
        root.get_by_test_id('failed-row-delete').first.click()
        expect(page.get_by_test_id('failed-remove-confirm-count')).to_have_text(
            '1 件の失敗記録を一覧から削除しますか？')
        page.get_by_test_id('failed-remove-cancel').click()
        expect(page.get_by_test_id('failed-remove-dialog')).to_be_hidden()
        expect(root.get_by_test_id('failed-count')).to_have_text('17 / 17 件')
        root.get_by_test_id('failed-row-delete').first.click()
        page.get_by_test_id('failed-remove-confirm').click()
        expect(root.get_by_test_id('failed-count')).to_have_text('16 / 16 件')
        expect(root.get_by_test_id('failed-remove-status')).to_contain_text('1 件を一覧から削除')
        other.get_by_test_id('failed-remove-confirm').click()
        expect(stale.get_by_test_id('failed-remove-status')).to_contain_text('削除できませんでした')
        stale.get_by_test_id('failed-reload').click()
        expect(stale.get_by_test_id('failed-count')).to_have_text('16 / 16 件')
        page.screenshot(path=str(output/'single-delete-and-source-safe.png'))
        root.get_by_test_id('failed-category-format').click()
        expect(root.get_by_test_id('failed-count')).to_have_text('3 / 16 件')
        root.get_by_test_id('failed-menu').click()
        page.get_by_test_id('failed-clear-all').click()
        expect(page.get_by_test_id('failed-remove-confirm-count')).to_have_text(
            '16 件の失敗記録を一覧から削除しますか？')
        expect(page.get_by_test_id('failed-remove-dialog')).to_contain_text('元のDB・画像・ZIP')
        page.screenshot(path=str(output/'all16-confirm-while-filter3.png'))
        page.get_by_test_id('failed-remove-cancel').click()
        expect(root.get_by_test_id('failed-count')).to_have_text('3 / 16 件')
        root.get_by_test_id('failed-menu').click()
        page.get_by_test_id('failed-clear-all').click()
        page.get_by_test_id('failed-remove-confirm').click()
        expect(root.get_by_test_id('failed-count')).to_have_text('0 / 0 件')
        root.get_by_test_id('failed-reload').click()
        expect(root.get_by_test_id('failed-count')).to_have_text('0 / 0 件')
        expect(root.get_by_test_id('failed-row-delete')).to_have_count(0)
        page.screenshot(path=str(output/'all-records-removed-from-owned-view.png'))
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1')
        return {'single_cancel': True, 'single_confirm_17_to_16': True,
                'cross_client_stale_cas_refused': True, 'clear_counts_all16_not_filtered3': True,
                'clear_cancel': True, 'clear_confirm_0': True, 'reloaded_0': True,
                'csv_all17_from_page2': True, 'csv_filtered3': True,
                'csv_all_fields_bom_formula_verified': True, 'no_horizontal_overflow': True}
    finally:
        page.close()
        other.close()


def run_local_removal(channel, output):
    from playwright.sync_api import sync_playwright, expect
    from tools import check_native_window as native
    from tools.check_library_calendar_slider_owned import OwnedTree, owned_output, write_new
    from tools.failed_records_fixture_guard import verify_fixture_manifest
    output = owned_output(output)
    output.mkdir(parents=True, exist_ok=False)
    before = acceptance_hashes()
    db, images = build_failure_fixture(output/'fixture')
    original_sha = hashlib.sha256(db.read_bytes()).hexdigest()
    original_mtime = db.stat().st_mtime_ns
    report = {'success': False, 'synthetic_only': True, 'scope': ['Q18','Q19','Q20'],
              'rounds': [], 'errors': [], 'forced_cleanup': 0}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='msedge' if channel == 'msedge' else None,
                                     headless=True, args=['--disable-background-networking'])
        try:
            for width, height in ((1280,720),(1920,1080)):
                preferences = output/f'preferences-{width}'
                for round_number in (1,2):
                    folder = output/f'{width}-round-{round_number}'
                    folder.mkdir()
                    port, nonce = free_port(), secrets.token_urlsafe(32)
                    url = f'http://127.0.0.1:{port}'
                    process, tree, owner, context = None, None, None, None
                    result = {'viewport': [width,height], 'round': round_number, 'errors': [],
                              'page_errors': [], 'console_errors': [], 'foreign_requests': []}
                    report['rounds'].append(result)
                    def request(action, post=False):
                        value = Request(url+'/_failure_acceptance/'+action,
                            data=b'' if post else None, method='POST' if post else 'GET',
                            headers={'X-Acceptance':nonce})
                        with build_opener(ProxyHandler({})).open(value, timeout=5) as response:
                            return json.loads(response.read(4096))
                    try:
                        command = [sys.executable, '-B', str(Path(__file__).resolve()), '--serve',
                            '--port', str(port), '--db', str(db), '--images', str(images),
                            '--preferences', str(preferences), '--nonce', nonce, '--output', str(folder)]
                        with (folder/'server.log').open('xb') as log:
                            process = subprocess.Popen(command, cwd=Path(__file__).resolve().parents[1],
                                env=child_environment(), stdin=subprocess.DEVNULL,
                                stdout=log, stderr=subprocess.STDOUT)
                        tree = OwnedTree(process, native)
                        wait_for_health(url, process, expected_data='library-readonly')
                        identity = request('owner')
                        assert identity.get('synthetic') is True and identity.get('local_removal') is True
                        owner = tree.observed_endpoint(identity['pid'])
                        assert owner is not None
                        context = browser.new_context(viewport={'width':width,'height':height},accept_downloads=True)
                        def route(item):
                            parsed = urlsplit(item.request.url)
                            if parsed.scheme in ('http','https') and parsed.netloc != f'127.0.0.1:{port}':
                                result['foreign_requests'].append(item.request.resource_type)
                                item.abort()
                            else:
                                item.continue_()
                        context.route('**/*', route)
                        def observe(page):
                            page.on('pageerror', lambda error: result['page_errors'].append(type(error).__name__))
                            page.on('console', lambda message: result['console_errors'].append('console-error')
                                    if message.type == 'error' else None)
                        context.on('page', observe)
                        if round_number == 1:
                            result['ui'] = inspect_local_removal(context, url, folder)
                        else:
                            page = context.new_page()
                            page.goto(url, wait_until='domcontentloaded')
                            root = view(page)
                            expect(root.get_by_test_id('failed-count')).to_have_text('0 / 0 件')
                            expect(root.get_by_test_id('failed-empty')).to_contain_text('失敗はありません')
                            page.screenshot(path=str(folder/'restart-owned-list-empty.png'))
                            result['ui'] = {'restart_preserves_dismissals':True}
                        assert not result['page_errors'] and not result['console_errors'] and not result['foreign_requests']
                    except Exception as error:
                        result['errors'].append(type(error).__name__+': '+str(error)[:500])
                    finally:
                        if context is not None:
                            context.close()
                        if tree is not None and owner is not None:
                            try:
                                assert tree.observed_endpoint(owner['pid']) == owner
                                result['shutdown_acknowledged'] = request('shutdown', post=True) == {'stopping':True}
                            except Exception as error:
                                result['errors'].append(type(error).__name__+': shutdown_unconfirmed')
                        if process is not None:
                            try:
                                result['exit_code'] = process.wait(timeout=35)
                            except subprocess.TimeoutExpired:
                                result['errors'].append('normal_exit_timeout_no_force')
                        if tree is not None:
                            result['process_tree'] = tree.finish()
                        result['port_released'] = port_released(port)
                        lifecycle = folder/'server-lifecycle.json'
                        result['lifecycle'] = (json.loads(lifecycle.read_text(encoding='utf-8'))
                                               if lifecycle.is_file() else None)
                        closed = result['lifecycle'] or {}
                        result['passed'] = (not result['errors'] and result.get('exit_code') == 0
                            and result.get('shutdown_acknowledged') is True and result['port_released']
                            and (result.get('process_tree') or {}).get('living') == []
                            and not (result.get('process_tree') or {}).get('errors')
                            and owner is not None and closed.get('pid') == owner['pid']
                            and closed.get('services_closed') is True
                            and closed.get('shutdown_requested') is True
                            and closed.get('external_denied') == 0
                            and closed.get('original_db_sha256') == original_sha)
                        write_new(folder/'result.json', result)
                    if not result['passed']:
                        raise RuntimeError('owned local-removal acceptance failed; no repeat')
            verify_fixture_manifest(db, images)
            report['original_source_unchanged'] = (hashlib.sha256(db.read_bytes()).hexdigest() == original_sha
                                                   and db.stat().st_mtime_ns == original_mtime)
            with sqlite3.connect(db.as_uri()+'?mode=ro&immutable=1', uri=True) as connection:
                report['raw_source_count'] = connection.execute('SELECT COUNT(*) FROM failed_queue').fetchone()[0]
            connection.close()
            report['source_before'] = before
            report['source_after'] = acceptance_hashes()
            report['source_changed'] = before != report['source_after']
            report['success'] = (len(report['rounds']) == 4 and report['original_source_unchanged']
                                 and report['raw_source_count'] == 17 and not report['source_changed'])
        except Exception as error:
            report['errors'].append(type(error).__name__+': '+str(error)[:240])
        finally:
            browser.close()
            report['original_db_sha256'] = original_sha
            report['original_db_unchanged'] = (hashlib.sha256(db.read_bytes()).hexdigest() == original_sha
                                                and db.stat().st_mtime_ns == original_mtime)
            report['source_changed'] = before != acceptance_hashes()
            write_new(output/'result.json', report)
    print(json.dumps({'success':report['success'],'rounds':len(report['rounds']),'errors':report['errors']}))
    return 0 if report['success'] else 1


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
                 "check_readonly_library_browser.py", "run_browser_checks.py",
                 "check_library_calendar_slider_owned.py", "check_native_window.py"):
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
    parser.add_argument('--local-removal', action='store_true')
    parser.add_argument('--preferences', type=Path)
    parser.add_argument('--nonce')
    args = parser.parse_args()
    if args.serve:
        serve(args.port, args.db, args.images, preferences=args.preferences, nonce=args.nonce,
              output=args.output if args.preferences is not None else None)
    elif args.local_removal:
        raise SystemExit(run_local_removal(args.channel, args.output))
    else:
        raise SystemExit(run(args.channel, args.output))
