"""Real Edge synthetic author-ID/follow selection to managed originals and viewer.

Only fake Pixiv data and a disposable managed root are used. The loopback
browser cannot contact any non-local address. No real account IDs are logged.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
import sys
import time
import traceback
from urllib.parse import urlsplit
from urllib.request import Request, ProxyHandler, build_opener

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.run_browser_checks import (child_environment, free_port, port_released,
                                      source_hashes, stop_child, wait_for_health)


def verify_saved(root, *, expected_update_date='2026-10-24T10:00:00+09:00',
                 expected_versions=2, expected_entries=1):
    """Require the active v2 generation, not just an orphan on disk."""
    import hashlib
    from io import BytesIO
    from PIL import Image
    # Distinct jobs can finish in a different order under Linux/Windows.
    # 70 saved active entries do not imply the updated generation of work 321
    # has been committed yet. Wait for the *exact* desired CAS generation,
    # not merely the total entry count or a historical 'saved' UI message.
    deadline = time.monotonic() + (120 if expected_entries > 1 else 30)
    index = {}
    versions = []
    last_read_error = None
    while time.monotonic() < deadline:
        try:
            # An atomic index replacement can transiently make a file open
            # fail on Windows; retry without accepting an incomplete snapshot.
            index = json.loads((root / 'active-generations.json').read_text(encoding='utf-8'))
            versions = sorted((root / 'generations').glob('work-321-*/receipt.json'))
        except (OSError, json.JSONDecodeError) as exc:
            last_read_error = type(exc).__name__
            time.sleep(0.1)
            continue
        entries = index.get('entries', {})
        selected = entries.get('321')
        if (len(entries) == expected_entries and selected is not None
                and selected.get('update_date') == expected_update_date
                and len(versions) == expected_versions):
            break
        time.sleep(0.1)
    assert index, f'active generation index not readable: {last_read_error}'
    assert type(index['version']) is int and index['version'] == 2
    assert '321' in index['entries'] and len(index['entries']) == expected_entries
    selected = index['entries']['321']
    assert selected['update_date'] == expected_update_date, (
        f"work 321 active generation not settled: expected={expected_update_date}, "
        f"actual={selected['update_date']}, versions={len(versions)}")
    assert re.fullmatch('[a-f0-9]{64}', selected['accepted_fingerprint'])
    relative = selected['relative_receipt']
    assert relative.startswith('generations/work-321-') and relative.endswith('/receipt.json')
    receipt_path = root / relative
    data = receipt_path.read_bytes()
    assert hashlib.sha256(data).hexdigest() == selected['sha256']
    receipt = json.loads(data)
    assert receipt['version'] == 1 and receipt['work_id'] == '321'
    assert receipt['page_count'] == 2
    assert len(versions) == expected_versions, 'updated generations must retain prior receipts'
    for row_no, row in enumerate(receipt['files']):
        source = (receipt_path.parent / row['name']).read_bytes()
        assert hashlib.sha256(source).hexdigest() == row['sha256']
        with Image.open(BytesIO(source)) as decoded:
            assert decoded.size == (120, 84)
            if expected_update_date == '2026-10-25T10:00:00+09:00':
                expected_pixel = (13, 37, 211) if row_no == 0 else (9, 191, 53)
            else:
                expected_pixel = (74, 28, 90) if row_no == 0 else (17, 89, 141)
            assert decoded.getpixel((0, 0)) == expected_pixel
    for historical in versions:
        assert json.loads(historical.read_text())['work_id'] == '321'
    return receipt


def inspect(page, base, headers, root, *, restart, scheduler_acceptance=True,
            expected_metadata_revision=None):
    from playwright.sync_api import expect
    page.goto(base, wait_until='domcontentloaded')
    if not restart:
        page.get_by_test_id('tab-download').click()
        view = page.get_by_test_id('managed-download-view')
        expect(view).to_be_visible()
        author_id = view.get_by_test_id('managed-download-author-id')
        author_id.fill('bad')
        view.get_by_test_id('managed-download-author-add').click()
        expect(view.get_by_test_id('managed-download-author-status')).to_contain_text('正しい作者ID')
        author_id.fill('24')
        view.get_by_test_id('managed-download-author-add').click()
        expect(view.get_by_test_id('managed-download-author-selection')).to_have_text('選択作者 1 件')
        view.get_by_test_id('managed-download-follow-refresh').click()
        expect(view.get_by_test_id('managed-download-author-status')).to_contain_text('フォロー作者 1 件', timeout=20000)
        view.get_by_test_id('managed-download-follow-search').fill('合成フォロー')
        follow = view.get_by_test_id('managed-download-follow-select')
        expect(follow).to_have_count(1)
        follow.click()  # Same author does not double-select or replace ID origin.
        expect(view.get_by_test_id('managed-download-author-selection')).to_have_text('選択作者 1 件')
        view.get_by_test_id('managed-download-author-fetch').click()
        expect(view.get_by_test_id('managed-download-author-status')).to_contain_text('新規 1 件', timeout=25000)
        expect(view.get_by_test_id('managed-download-selection')).to_have_text('対象 1 件')
        other = page.context.new_page()
        try:
            other.goto(base, wait_until='domcontentloaded')
            other.get_by_test_id('tab-download').click()
            another = other.get_by_test_id('managed-download-view')
            expect(another.get_by_test_id('managed-download-selection')).to_have_text('対象 0 件')
        finally:
            other.close()
        view.get_by_test_id('managed-download-submit').click()
        expect(view.get_by_test_id('managed-download-notice')).to_contain_text('1件をキュー', timeout=20000)

    page.get_by_test_id('tab-queue').click()
    queue = page.get_by_test_id('queue-view')
    expect(queue.get_by_test_id('queue-filter-done')).to_contain_text(
        ('完了 50' if scheduler_acceptance else '完了 2') if restart else '完了 1',
        timeout=30000)
    if not restart:
        # Change only the synthetic GET fixture: no Pixiv write is involved.
        page.request.post(base + '/_managed_test/control?author_updated=true', headers=headers)
        page.get_by_test_id('tab-download').click()
        view = page.get_by_test_id('managed-download-view')
        view.get_by_test_id('managed-download-author-fetch').click()
        expect(view.get_by_test_id('managed-download-author-status')).to_contain_text(
            '更新 1 件', timeout=25000)
        expect(view.get_by_test_id('managed-download-selection')).to_have_text('対象 1 件')
        view.get_by_test_id('managed-download-submit').click()
        expect(view.get_by_test_id('managed-download-notice')).to_contain_text(
            '1件をキュー', timeout=20000)
        page.get_by_test_id('tab-queue').click()
        expect(queue.get_by_test_id('queue-filter-done')).to_contain_text('完了 2', timeout=30000)
    # Phase10 M4 UI: opening a tab must neither provision a side-ledger nor
    # read the fake remote account. Only the explicit button creates a local
    # metadata ledger. The ON setting persists on restart but starts no GET.
    metadata_db = root / '.pixivvault-managed-metadata.sqlite3'
    metadata_absent_before_tab = not metadata_db.exists()
    requests_before_metadata = page.request.get(
        base + '/_managed_test/status', headers=headers).json()['request_count']
    if not restart:
        assert not metadata_db.exists()
    page.get_by_test_id('tab-metadata').click()
    expect(page.get_by_test_id('metadata-view')).to_be_visible()
    if not restart:
        assert not metadata_db.exists()
    page.get_by_test_id('metadata-open').click()
    expect(page.get_by_test_id('metadata-save')).to_be_enabled(timeout=15000)
    expect(page.get_by_test_id('metadata-status')).to_contain_text('自動取得なし')
    assert metadata_db.is_file()
    if not restart:
        metadata_uri = metadata_db.resolve().as_uri() + '?mode=ro'
        with closing(sqlite3.connect(metadata_uri, uri=True, timeout=1.0)) as metadata_conn:
            metadata_conn.execute('PRAGMA query_only=ON')
            enabled_before_save, revision_before_save = metadata_conn.execute(
                'SELECT enabled,revision FROM settings WHERE id=1').fetchone()
        assert enabled_before_save == 0
        # The current settings CAS increments revision exactly once per save;
        # if that contract changes, this acceptance must be updated explicitly.
        expected_metadata_revision = revision_before_save + 1
        expect(page.get_by_test_id('metadata-enabled')).not_to_be_checked()
        page.get_by_test_id('metadata-enabled').click()
        expect(page.get_by_test_id('metadata-enabled')).to_be_checked()
        page.get_by_test_id('metadata-save').click()
        expect(page.get_by_test_id('metadata-status')).to_contain_text('保存済み設定: ON')
    else:
        expect(page.get_by_test_id('metadata-enabled')).to_be_checked()
    page.get_by_test_id('metadata-work-id').fill('321')
    page.get_by_test_id('metadata-inspect').click()
    expect(page.get_by_test_id('metadata-entry-status')).to_contain_text('記録はありません')
    expect(page.get_by_test_id('metadata-progress')).to_contain_text('台帳登録 0件')
    requests_after_metadata = page.request.get(
        base + '/_managed_test/status', headers=headers).json()['request_count']
    assert requests_after_metadata == requests_before_metadata, (
        'metadata tab/configuration caused an implicit Pixiv request')
    # Verify the displayed empty-state against the actual isolated side-ledger,
    # not just a UI label. Open it in read-only mode so acceptance cannot mutate it.
    if expected_metadata_revision is None:
        raise AssertionError('initial run did not establish metadata settings revision')
    metadata_uri = metadata_db.resolve().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(metadata_uri, uri=True, timeout=1.0)) as metadata_conn:
        metadata_conn.execute('PRAGMA query_only=ON')
        enabled, revision = metadata_conn.execute(
            'SELECT enabled,revision FROM settings WHERE id=1').fetchone()
        metadata_entries = metadata_conn.execute(
            'SELECT COUNT(*) FROM metadata').fetchone()[0]
    assert metadata_entries == 0, 'synthetic metadata ledger must remain empty'
    assert enabled == 1, 'explicitly saved ON setting must persist'
    assert revision == expected_metadata_revision, 'settings CAS revision must be stable across restart'
    page.get_by_test_id('tab-library').click()
    library = page.get_by_test_id('library-view')
    if not restart:
        library.get_by_test_id('library-db-refresh').click()
    author = library.locator('.pv-library-author-card[data-author-id="24"]')
    expect(author).to_be_visible(timeout=20000)
    author.get_by_test_id('library-author-open').click()
    cards = library.locator('.pv-library-page-card[data-work-id="321"]')
    expect(cards).to_have_count(2)
    cards.first.click()
    viewer = page.get_by_test_id('library-viewer')
    image = viewer.locator('img:visible')
    for index in range(2):
        expect(viewer.locator('.pv-counter')).to_have_text(f'{index+1}/2')
        image.evaluate('img => img.decode()')
        expect(image).to_have_attribute('src', re.compile(rf'/media/321/{index}/view\.png\?catalog='))
        if index == 0:
            page.keyboard.press('ArrowRight')
    viewer.get_by_role('button', name='閉じる', exact=True).click()
    expect(cards.first).to_be_focused()
    if restart:
        if scheduler_acceptance:
            assert verify_saved(root, expected_update_date='2026-10-25T10:00:00+09:00',
                                expected_versions=3, expected_entries=70)['page_count'] == 2
        else:
            assert verify_saved(root, expected_update_date='2026-10-24T10:00:00+09:00',
                                expected_versions=2, expected_entries=1)['page_count'] == 2
    else:
        assert verify_saved(root)['page_count'] == 2
    scheduler_saved = False
    if scheduler_acceptance and not restart:
        # End-to-end synthetic Phase9: settings UI -> full author inventory ->
        # authorized managed enqueue -> JobService/CAS receipt -> history/toast.
        page.request.post(base + '/_managed_test/control?scheduler_update=true', headers=headers)
        page.request.post(base + '/_managed_test/control?scheduler_many=true', headers=headers)
        page.get_by_test_id('tab-scheduler').click()
        scheduler = page.get_by_test_id('scheduler-status')
        page.get_by_test_id('scheduler-open').click()
        # A dirty form must be explicitly discardable without retaining a
        # stale CAS revision or accidentally saving a discarded author.
        expect(page.get_by_test_id('scheduler-discard')).to_be_enabled()
        page.get_by_test_id('scheduler-authors').fill('999')
        page.get_by_test_id('scheduler-discard').click()
        expect(page.get_by_test_id('scheduler-authors')).to_have_value('')
        page.get_by_test_id('scheduler-authors').fill('24')
        page.get_by_test_id('scheduler-interval').click()
        page.get_by_text('自動チェックしない', exact=True).click()
        page.get_by_test_id('scheduler-save').click()
        page.get_by_test_id('scheduler-run').click()
        deadline = time.monotonic() + 30
        accepted, deferred, dispatch_status = 0, 0, 'running'
        while time.monotonic() < deadline:
            with sqlite3.connect(root / '.pixivvault-managed-scheduler.sqlite3') as db:
                row = db.execute(
                    "SELECT accepted,deferred,status FROM dispatch_history WHERE slot LIKE 'manual-%' "
                    'ORDER BY started_utc DESC LIMIT 1').fetchone()
            if row is not None:
                accepted, deferred, dispatch_status = row
                if dispatch_status != 'running':
                    break
            time.sleep(0.1)
        assert (accepted, deferred) == (50, 20), 'first batch must persist capacity-limited backlog'
        expect(page.get_by_test_id('scheduler-history')).to_contain_text('saved', timeout=120000)
        assert verify_saved(root, expected_update_date='2026-10-25T10:00:00+09:00',
                            expected_versions=3, expected_entries=70)['page_count'] == 2
        page.get_by_test_id('tab-queue').click()
        expect(page.get_by_test_id('queue-view').get_by_test_id('queue-filter-done')).to_have_text(
            '完了 50', timeout=120000)
        active_index = json.loads((root / 'active-generations.json').read_text(encoding='utf-8'))
        assert len(active_index['entries']) == 70
        assert set(active_index['entries']) == {'321', *(str(wid) for wid in range(400, 469))}
        # Files/index/history alone can hide a failed or unknown dispatch item.
        # Assert the durable scheduler ledger itself is fully reconciled.
        # Publishing the CAS index and reconciling the dispatch ledger are
        # separate durable steps. Never assert immediately after the index
        # flips; wait for the exact 70 saved / zero deferred condition.
        ledger_deadline = time.monotonic() + 120
        item_states, deferred_count = [], -1
        while time.monotonic() < ledger_deadline:
            try:
                with sqlite3.connect(root / '.pixivvault-managed-scheduler.sqlite3') as db:
                    item_states = db.execute(
                        "SELECT state,COUNT(*) FROM dispatch_items GROUP BY state").fetchall()
                    deferred_count = db.execute('SELECT COUNT(*) FROM deferred_work').fetchone()[0]
            except sqlite3.OperationalError:
                time.sleep(0.1)
                continue
            if item_states == [('saved', 70)] and deferred_count == 0:
                break
            time.sleep(0.1)
        assert item_states == [('saved', 70)], f'unexpected dispatch item states: {item_states!r}'
        assert deferred_count == 0, f'deferred work remains after all 70 saves: {deferred_count}'
        scheduler_saved = True
    elif scheduler_acceptance:
        page.get_by_test_id('tab-scheduler').click()
        page.get_by_test_id('scheduler-open').click()
        expect(page.get_by_test_id('scheduler-history')).to_contain_text('saved', timeout=10000)
    status = page.request.get(base + '/_managed_test/status', headers=headers).json()
    assert status['all_requests_get'] and status['allowed_https_hosts_only']
    assert status['image_requests_cookie_free']
    assert status['original_gets'].get('321', 0) == (
        0 if restart else (6 if scheduler_acceptance else 4))
    if not restart and scheduler_acceptance:
        assert all(status['original_gets'].get(str(wid), 0) == 2 for wid in range(400, 469))
        assert sum(status['original_gets'].values()) == 6 + 2 * 69
    if restart:
        assert status['request_count'] == 0
    return {'restart': restart, 'pages': 2, 'viewer_decode_and_focus': True,
            'metadata': {
                'sidecar_absent_before_tab': metadata_absent_before_tab,
                'sidecar_created_by_explicit_open': bool(not restart and metadata_absent_before_tab
                                                         and metadata_db.is_file()),
                'enabled': bool(enabled), 'settings_revision': revision,
                'entry_count': metadata_entries,
                'fake_pixiv_request_count_before': requests_before_metadata,
                'fake_pixiv_request_count_after': requests_after_metadata,
                'fake_pixiv_request_delta': requests_after_metadata - requests_before_metadata,
            },
            'scheduler_settings_dispatch_receipt_history':
                (scheduler_saved or restart) if scheduler_acceptance else False,
            'original_gets': status['original_gets'].get('321', 0)}


def run(channel, output):
    if output.exists():
        raise ValueError('new --output directory required')
    from playwright.sync_api import sync_playwright
    output.mkdir(parents=True)
    before = source_hashes()
    results = {'success': False, 'synthetic_only': True, 'viewports': [],
               'channel': channel,
               'console_errors': [], 'page_errors': [], 'foreign_requests': [],
               'normal_exit': [], 'ports_released': []}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel=None if channel == 'chromium' else channel)
            try:
                for width, height in ((1280, 720), (1920, 1080)):
                    root = (output / f'author-{width}x{height}').resolve()
                    root.mkdir()
                    expected_metadata_revision = None
                    for restart in (False, True):
                        port, nonce = free_port(), secrets.token_hex(16)
                        base, headers = f'http://127.0.0.1:{port}', {'X-Acceptance': nonce}
                        process, context = None, None
                        try:
                            with (output / f'server-{width}-{restart}.log').open('wb') as log:
                                process = subprocess.Popen([
                                    sys.executable,
                                    str(Path(__file__).with_name('check_managed_downloads_browser.py')),
                                    '--serve', str(port), '--root', str(root), '--nonce', nonce,
                                ], env=child_environment(), stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=log)
                            wait_for_health(base, process, expected_data='pixiv-readonly')
                            context = browser.new_context(viewport={'width': width, 'height': height})
                            def block_external(route):
                                parsed = urlsplit(route.request.url)
                                if (parsed.scheme, parsed.netloc) == ('http', f'127.0.0.1:{port}'):
                                    route.continue_()
                                else:
                                    results['foreign_requests'].append('blocked')
                                    route.abort()
                            context.route('**/*', block_external)
                            page = context.new_page()
                            page.on('console', lambda m: results['console_errors'].append(m.text[:250])
                                    if m.type == 'error' else None)
                            page.on('pageerror', lambda e: results['page_errors'].append(str(e)[:250]))
                            results['viewports'].append({'viewport': [width, height],
                                                         **inspect(page, base, headers, root, restart=restart,
                                                                   expected_metadata_revision=expected_metadata_revision)})
                            if not restart:
                                expected_metadata_revision = results['viewports'][-1]['metadata']['settings_revision']
                            context.close(); context = None
                            request = Request(base + '/_managed_test/shutdown', data=b'',
                                              headers=headers, method='POST')
                            with build_opener(ProxyHandler({})).open(request, timeout=5):
                                pass
                            process.wait(timeout=15)
                            results['normal_exit'].append(process.returncode == 0)
                        finally:
                            if context is not None:
                                context.close()
                            stop_child(process)
                            results['ports_released'].append(port_released(port))
            finally:
                browser.close()
        assert len(results['viewports']) == 4
        assert not results['console_errors'] and not results['page_errors']
        assert not results['foreign_requests']
        assert all(results['normal_exit']) and all(results['ports_released'])
        assert before == source_hashes()
        results['success'] = True
    except Exception as exc:
        results['failure'] = f'{type(exc).__name__}: synthetic acceptance failed'
        results['failure_trace'] = traceback.format_exc()[-3000:]
    results['source_hashes'] = before
    (output / 'result.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if results['success'] else 1


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--channel', choices=('msedge', 'chromium'), default='chromium')
    cli.add_argument('--output', type=Path, required=True)
    args = cli.parse_args()
    raise SystemExit(run(args.channel, args.output))
