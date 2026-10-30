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
            expected_metadata_revision=None, evidence_dir=None, viewport_label='unknown',
            result_record=None):
    from playwright.sync_api import expect
    ux_timings_ms = {}
    ux_status = {}
    ux_screenshots = {}
    ux_repeat_samples_ms = {}
    download_scroll = None
    download_queue_button_rect = None
    if result_record is not None:
        result_record['ux_timings_ms'] = ux_timings_ms
        result_record['ux_status'] = ux_status
        result_record['ux_screenshots'] = ux_screenshots
        result_record['ux_repeat_samples_ms'] = ux_repeat_samples_ms

    def timed(name, action, complete):
        started = time.perf_counter()
        try:
            action()
            complete()
        except Exception as exc:
            # Playwright expect() timeout uses AssertionError, unlike locator
            # action timeouts. Classify both without persisting exception text.
            timeout = 'Timeout' in type(exc).__name__ or 'Timeout' in str(exc)
            ux_status[name] = {'status': 'timeout' if timeout else 'error',
                               'error_type': type(exc).__name__}
            raise
        else:
            ux_status[name] = {'status': 'ok'}
        finally:
            ux_timings_ms[name] = round((time.perf_counter() - started) * 1000, 2)

    def capture(label):
        if evidence_dir is not None:
            path = evidence_dir / f'ux-{viewport_label}-{"restart" if restart else "initial"}-{label}.png'
            page.screenshot(path=str(path), full_page=False)
            ux_screenshots[label] = path.name

    timed('shell_ready_after_server_health', lambda: page.goto(base, wait_until='domcontentloaded'),
          lambda: expect(page.get_by_test_id('tab-download')).to_be_visible())
    capture('initial_shell')
    first_enqueue_started = None
    def open_download_mode(mode):
        # The product now owns two selectable modes in one download tab.
        # Exercise the actual browser button, including from its default
        # bookmark mode; DOM existence alone would not verify the transition.
        view = page.get_by_test_id('managed-download-view')
        assert mode in ('bookmark', 'author')
        view.get_by_test_id('managed-download-mode-' + mode).click()
        expect(view).to_have_attribute('data-target-mode', mode)
        if mode == 'bookmark':
            expect(view.get_by_test_id('managed-download-bookmark-panel')).to_be_visible()
            expect(view.get_by_test_id('managed-download-author-panel')).to_be_hidden()
        else:
            expect(view.get_by_test_id('managed-download-bookmark-panel')).to_be_hidden()
            expect(view.get_by_test_id('managed-download-author-panel')).to_be_visible()
        return view
    if not restart:
        view = page.get_by_test_id('managed-download-view')
        timed('open_download_tab', lambda: page.get_by_test_id('tab-download').click(),
              lambda: expect(view).to_be_visible())
        expect(view).to_have_attribute('data-target-mode', 'bookmark')
        timed('explicit_bookmark_mode', lambda: open_download_mode('bookmark'),
              lambda: (expect(view.get_by_test_id('managed-download-bookmark-select-all')).to_be_visible(),
                       expect(view.get_by_test_id('managed-download-bookmark-clear')).to_be_visible()))
        # An empty first-run cache is still a real selection/clear action and
        # must not silently enqueue any job or hide the bookmark list.
        bookmark_selection = view.get_by_test_id('managed-download-selection').inner_text()
        view.get_by_test_id('managed-download-bookmark-select-all').click()
        view.get_by_test_id('managed-download-bookmark-clear').click()
        expect(view.get_by_test_id('managed-download-selection')).to_have_text('対象 0 件')
        assert bookmark_selection == '対象 0 件'
        timed('switch_to_follow_author_mode', lambda: open_download_mode('author'),
              lambda: expect(view.get_by_test_id('managed-download-author-id')).to_be_visible())
        capture('download')
        author_id = view.get_by_test_id('managed-download-author-id')
        author_id.fill('bad')
        timed('invalid_author_feedback', lambda: view.get_by_test_id('managed-download-author-add').click(),
              lambda: expect(view.get_by_test_id('managed-download-author-status')).to_contain_text('正しい作者ID'))
        capture('download_invalid_author')
        author_id.fill('24')
        view.get_by_test_id('managed-download-author-add').click()
        expect(view.get_by_test_id('managed-download-author-selection')).to_have_text('選択作者 1 件')
        timed('refresh_follow_list', lambda: view.get_by_test_id('managed-download-follow-refresh').click(),
              lambda: expect(view.get_by_test_id('managed-download-author-status')).to_contain_text('フォロー作者 1 件', timeout=20000))
        view.get_by_test_id('managed-download-follow-search').fill('合成フォロー')
        follow = view.get_by_test_id('managed-download-follow-select')
        expect(follow).to_have_count(1)
        follow.click()  # Same author does not double-select or replace ID origin.
        expect(view.get_by_test_id('managed-download-author-selection')).to_have_text('選択作者 1 件')
        timed('author_diff_fetch', lambda: view.get_by_test_id('managed-download-author-fetch').click(),
              lambda: (expect(view.get_by_test_id('managed-download-author-status')).to_contain_text('新規 1 件', timeout=25000),
                       expect(view.get_by_test_id('managed-download-selection')).to_have_text('対象 1 件')))
        capture('download_selected_author')
        other = page.context.new_page()
        try:
            other.goto(base, wait_until='domcontentloaded')
            other.get_by_test_id('tab-download').click()
            another = other.get_by_test_id('managed-download-view')
            expect(another).to_have_attribute('data-target-mode', 'bookmark')
            another.get_by_test_id('managed-download-mode-author').click()
            expect(another).to_have_attribute('data-target-mode', 'author')
            expect(another.get_by_test_id('managed-download-author-panel')).to_be_visible()
            expect(another.get_by_test_id('managed-download-author-selection')).to_have_text('選択作者 0 件')
            expect(another.get_by_test_id('managed-download-selection')).to_have_text('対象 0 件')
        finally:
            other.close()
        # Switching modes must not lose the accepted candidate, its author
        # provenance, or the visible right-hand queue target.
        timed('preserve_author_selection_across_modes',
              lambda: (open_download_mode('bookmark'), open_download_mode('author')),
              lambda: (expect(view.get_by_test_id('managed-download-author-selection')).to_have_text('選択作者 1 件'),
                       expect(view.get_by_test_id('managed-download-selection')).to_have_text('対象 1 件')))
        # Verify the bottom queue action is reachable on short viewports.
        download_root = page.get_by_test_id('managed-download-view')
        download_scroll = download_root.evaluate("""el => ({clientHeight: el.clientHeight,
            scrollHeight: el.scrollHeight, clientWidth: el.clientWidth,
            scrollWidth: el.scrollWidth, overflowY: getComputedStyle(el).overflowY})""")
        assert download_scroll['scrollWidth'] <= download_scroll['clientWidth'] + 1
        if download_scroll['scrollHeight'] > download_scroll['clientHeight'] + 1:
            assert download_scroll['overflowY'] in ('auto', 'scroll'), download_scroll
        download_root.evaluate('el => { el.scrollTop = el.scrollHeight; }')
        download_scroll['scrollTop_after'] = download_root.evaluate('el => el.scrollTop')
        submit_rect = view.get_by_test_id('managed-download-submit').bounding_box()
        assert view.get_by_test_id('managed-download-submit').is_enabled()
        assert submit_rect is not None and 0 <= submit_rect['y'] and (
            submit_rect['y'] + submit_rect['height'] <= page.viewport_size['height']), submit_rect
        download_queue_button_rect = {key: round(value, 2) for key, value in submit_rect.items()}
        capture('download_bottom')
        download_root.evaluate('el => { el.scrollTop = 0; }')
        first_enqueue_started = time.perf_counter()
        timed('enqueue_download', lambda: view.get_by_test_id('managed-download-submit').click(),
              lambda: expect(view.get_by_test_id('managed-download-notice')).to_contain_text('1件をキュー', timeout=20000))

    queue = page.get_by_test_id('queue-view')
    timed('open_queue_tab', lambda: page.get_by_test_id('tab-queue').click(),
          lambda: expect(queue).to_be_visible())
    expected_done = ('完了 50' if scheduler_acceptance else '完了 2') if restart else '完了 1'
    if first_enqueue_started is None:
        timed('reopen_persisted_queue_state', lambda: None,
              lambda: expect(queue.get_by_test_id('queue-filter-done')).to_contain_text(expected_done, timeout=30000))
    else:
        try:
            timed('queue_completion_after_enqueue', lambda: None,
                  lambda: expect(queue.get_by_test_id('queue-filter-done')).to_contain_text(expected_done, timeout=30000))
        finally:
            ux_timings_ms['enqueue_to_queue_completion_user_flow'] = round((time.perf_counter() - first_enqueue_started) * 1000, 2)
            ux_status['enqueue_to_queue_completion_user_flow'] = dict(ux_status.get('queue_completion_after_enqueue',
                                                                           {'status': 'error', 'error_type': 'unknown'}))
    capture('queue')
    if not restart:
        # Change only the synthetic GET fixture: no Pixiv write is involved.
        page.request.post(base + '/_managed_test/control?author_updated=true', headers=headers)
        page.get_by_test_id('tab-download').click()
        view = page.get_by_test_id('managed-download-view')
        timed('revisit_follow_author_mode', lambda: open_download_mode('author'),
              lambda: expect(view.get_by_test_id('managed-download-author-selection')).to_have_text('選択作者 1 件'))
        view.get_by_test_id('managed-download-author-fetch').click()
        expect(view.get_by_test_id('managed-download-author-status')).to_contain_text(
            '更新 1 件', timeout=25000)
        expect(view.get_by_test_id('managed-download-selection')).to_have_text('対象 1 件')
        view.get_by_test_id('managed-download-submit').click()
        expect(view.get_by_test_id('managed-download-notice')).to_contain_text(
            '1件をキュー', timeout=20000)
        page.get_by_test_id('tab-queue').click()
        expect(queue.get_by_test_id('queue-filter-done')).to_contain_text('完了 2', timeout=30000)
    else:
        # Restart creates a new client selection scope. The durable queue and
        # receipt are checked below; neither should be mistaken for a checkbox.
        timed('restart_open_download_tab', lambda: page.get_by_test_id('tab-download').click(),
              lambda: expect(page.get_by_test_id('managed-download-view')).to_be_visible())
        view = page.get_by_test_id('managed-download-view')
        expect(view).to_have_attribute('data-target-mode', 'bookmark')
        timed('restart_mode_switch_roundtrip',
              lambda: (open_download_mode('author'), open_download_mode('bookmark')),
              lambda: (expect(view.get_by_test_id('managed-download-author-selection')).to_have_text('選択作者 0 件'),
                       expect(view.get_by_test_id('managed-download-selection')).to_have_text('対象 0 件')))
    # Phase10 M4 UI: opening a tab must neither provision a side-ledger nor
    # read the fake remote account. Only the explicit button creates a local
    # metadata ledger. The ON setting persists on restart but starts no GET.
    metadata_db = root / '.pixivvault-managed-metadata.sqlite3'
    metadata_absent_before_tab = not metadata_db.exists()
    requests_before_metadata = page.request.get(
        base + '/_managed_test/status', headers=headers).json()['request_count']
    if not restart:
        assert not metadata_db.exists()
    timed('open_metadata_tab', lambda: page.get_by_test_id('tab-metadata').click(),
          lambda: expect(page.get_by_test_id('metadata-view')).to_be_visible())
    capture('metadata_before_open')
    if not restart:
        assert not metadata_db.exists()
    timed('open_metadata_ledger', lambda: page.get_by_test_id('metadata-open').click(),
          lambda: (expect(page.get_by_test_id('metadata-save')).to_be_enabled(timeout=15000),
                   expect(page.get_by_test_id('metadata-status')).to_contain_text('自動取得なし')))
    capture('metadata_open')
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
        timed('save_metadata_setting', lambda: page.get_by_test_id('metadata-save').click(),
              lambda: expect(page.get_by_test_id('metadata-status')).to_contain_text('保存済み設定: ON'))
    else:
        expect(page.get_by_test_id('metadata-enabled')).to_be_checked()
    page.get_by_test_id('metadata-work-id').fill('321')
    timed('inspect_metadata_state', lambda: page.get_by_test_id('metadata-inspect').click(),
          lambda: (expect(page.get_by_test_id('metadata-entry-status')).to_contain_text('記録はありません'),
                   expect(page.get_by_test_id('metadata-progress')).to_contain_text('台帳登録 0件')))
    capture('metadata_state')
    metadata_view = page.get_by_test_id('metadata-view')
    metadata_scroll = metadata_view.evaluate("""el => ({clientHeight: el.clientHeight,
        scrollHeight: el.scrollHeight, clientWidth: el.clientWidth,
        scrollWidth: el.scrollWidth, overflowY: getComputedStyle(el).overflowY})""")
    assert metadata_scroll['overflowY'] in ('auto', 'scroll'), (
        f'metadata controls need an accessible scroll container: {metadata_scroll!r}')
    assert metadata_scroll['scrollWidth'] <= metadata_scroll['clientWidth'] + 1, (
        f'metadata panel overflows horizontally: {metadata_scroll!r}')
    page.evaluate("""() => { const el = document.querySelector('[data-testid="metadata-view"]');
        el.scrollTop = el.scrollHeight; }""")
    metadata_scroll['scrollTop'] = metadata_view.evaluate('el => el.scrollTop')
    assert metadata_scroll['scrollTop'] + metadata_scroll['clientHeight'] >= metadata_scroll['scrollHeight'] - 1
    run_status = page.get_by_test_id('metadata-run-status')
    expect(run_status).to_be_visible()
    root_rect, status_rect = metadata_view.bounding_box(), run_status.bounding_box()
    assert root_rect is not None and status_rect is not None
    assert status_rect['y'] >= root_rect['y'] - 1 and status_rect['y'] + status_rect['height'] <= root_rect['y'] + root_rect['height'] + 1, (
        f'metadata bottom action/status is clipped: root={root_rect!r}, status={status_rect!r}')
    viewport_height = page.viewport_size['height']
    assert 0 <= status_rect['y'] and status_rect['y'] + status_rect['height'] <= viewport_height
    for test_id in ('metadata-run-one', 'metadata-stop'):
        action_rect = page.get_by_test_id(test_id).bounding_box()
        assert action_rect is not None and 0 <= action_rect['y']
        assert action_rect['y'] + action_rect['height'] <= viewport_height
    capture('metadata_bottom')
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
    if evidence_dir is not None:
        # A separate warm micro-benchmark, not part of the user's first-run
        # latency. Each phase/viewport retains its own 20 observations.
        samples = ux_repeat_samples_ms.setdefault('warm_library_to_metadata', [])
        for _ in range(20):
            page.get_by_test_id('tab-library').click()
            expect(page.get_by_test_id('library-view')).to_be_visible()
            started = time.perf_counter()
            try:
                page.get_by_test_id('tab-metadata').click()
                expect(page.get_by_test_id('metadata-view')).to_be_visible()
            except Exception:
                samples.append({'ms': round((time.perf_counter()-started)*1000, 2), 'status': 'error'})
                raise
            samples.append({'ms': round((time.perf_counter()-started)*1000, 2), 'status': 'ok'})
    timed('return_to_library', lambda: page.get_by_test_id('tab-library').click(),
          lambda: expect(page.get_by_test_id('library-view')).to_be_visible())
    library = page.get_by_test_id('library-view')
    if not restart:
        timed('refresh_library', lambda: library.get_by_test_id('library-db-refresh').click(),
              lambda: expect(library.locator('.pv-library-author-card[data-author-id="24"]')).to_be_visible(timeout=20000))
    author = library.locator('.pv-library-author-card[data-author-id="24"]')
    expect(author).to_be_visible(timeout=20000)
    timed('open_library_author', lambda: author.get_by_test_id('library-author-open').click(),
          lambda: expect(library.locator('.pv-library-page-card[data-work-id="321"]')).to_have_count(2))
    cards = library.locator('.pv-library-page-card[data-work-id="321"]')
    expect(cards).to_have_count(2)
    viewer = page.get_by_test_id('library-viewer')
    image = viewer.locator('img:visible')
    timed('open_library_viewer_and_decode', lambda: cards.first.click(),
          lambda: (expect(viewer).to_be_visible(),
                   expect(viewer.locator('.pv-counter')).to_have_text('1/2'),
                   expect(image).to_have_attribute('src', re.compile(r'/media/321/0/view\.png\?catalog=')),
                   image.evaluate('img => img.decode().then(() => { if (!img.naturalWidth) throw Error("not decoded") })')))
    viewer = page.get_by_test_id('library-viewer')
    image = viewer.locator('img:visible')
    for index in range(2):
        expect(viewer.locator('.pv-counter')).to_have_text(f'{index+1}/2')
        expect(image).to_have_attribute('src', re.compile(rf'/media/321/{index}/view\.png\?catalog='))
        image.evaluate('img => img.decode().then(() => { if (!img.naturalWidth) throw Error("not decoded") })')
        if index == 0:
            timed('viewer_page_turn_and_decode', lambda: page.keyboard.press('ArrowRight'),
                  lambda: (expect(viewer.locator('.pv-counter')).to_have_text('2/2'),
                           expect(image).to_have_attribute('src', re.compile(r'/media/321/1/view\.png\?catalog=')),
                           image.evaluate('img => img.decode().then(() => { if (!img.naturalWidth) throw Error("not decoded") })')))
    if evidence_dir is not None:
        samples = ux_repeat_samples_ms.setdefault('warm_viewer_page_turn', [])
        for i in range(20):
            target = 0 if i % 2 == 0 else 1
            key = 'ArrowLeft' if target == 0 else 'ArrowRight'
            started = time.perf_counter()
            try:
                page.keyboard.press(key)
                expect(viewer.locator('.pv-counter')).to_have_text(f'{target+1}/2')
                expect(image).to_have_attribute('src', re.compile(rf'/media/321/{target}/view\.png\?catalog='))
                image.evaluate('img => img.decode().then(() => { if (!img.naturalWidth) throw Error("not decoded") })')
            except Exception:
                samples.append({'ms': round((time.perf_counter()-started)*1000, 2), 'status': 'error'})
                raise
            samples.append({'ms': round((time.perf_counter()-started)*1000, 2), 'status': 'ok'})
    capture('library_viewer')
    timed('close_viewer_restore_focus', lambda: viewer.get_by_role('button', name='閉じる', exact=True).click(),
          lambda: expect(cards.first).to_be_focused())
    capture('library')
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
        timed('open_scheduler_tab', lambda: page.get_by_test_id('tab-scheduler').click(),
              lambda: expect(page.get_by_test_id('scheduler-status')).to_be_visible())
        scheduler = page.get_by_test_id('scheduler-status')
        timed('open_scheduler_settings', lambda: page.get_by_test_id('scheduler-open').click(),
              lambda: expect(page.get_by_test_id('scheduler-discard')).to_be_enabled())
        expect(page.get_by_test_id('scheduler-status')).to_contain_text('定期状態: 未実行')
        expect(page.get_by_test_id('scheduler-status')).not_to_contain_text('never')
        capture('scheduler_settings')
        # A dirty form must be explicitly discardable without retaining a
        # stale CAS revision or accidentally saving a discarded author.
        expect(page.get_by_test_id('scheduler-discard')).to_be_enabled()
        page.get_by_test_id('scheduler-authors').fill('999')
        page.get_by_test_id('scheduler-discard').click()
        expect(page.get_by_test_id('scheduler-authors')).to_have_value('')
        page.get_by_test_id('scheduler-authors').fill('24')
        page.get_by_test_id('scheduler-interval').click()
        page.get_by_text('自動チェックしない', exact=True).click()
        scheduler_db_uri = (root / '.pixivvault-managed-scheduler.sqlite3').resolve().as_uri() + '?mode=ro'
        with closing(sqlite3.connect(scheduler_db_uri, uri=True, timeout=1.0)) as db:
            db.execute('PRAGMA query_only=ON')
            revision_before = db.execute('SELECT revision FROM schedule WHERE id=1').fetchone()[0]

        def wait_for_saved_settings():
            expect(page.get_by_test_id('scheduler-run')).to_be_enabled(timeout=15000)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    with closing(sqlite3.connect(scheduler_db_uri, uri=True, timeout=1.0)) as db:
                        db.execute('PRAGMA query_only=ON')
                        interval, revision = db.execute(
                            'SELECT interval_hours,revision FROM schedule WHERE id=1').fetchone()
                        authors = tuple(row[0] for row in db.execute('SELECT author_id FROM favourite_authors'))
                    if interval == 0 and revision == revision_before + 1 and authors == ('24',):
                        return
                except sqlite3.OperationalError:
                    pass
                time.sleep(.1)
            raise AssertionError('scheduler settings save did not commit expected CAS revision')

        timed('save_scheduler_settings', lambda: page.get_by_test_id('scheduler-save').click(),
              wait_for_saved_settings)

        def wait_for_scheduler_admission():
            deadline = time.monotonic() + 30
            accepted, deferred, dispatch_status = 0, 0, 'running'
            while time.monotonic() < deadline:
                try:
                    with closing(sqlite3.connect(scheduler_db_uri, uri=True, timeout=1.0)) as db:
                        db.execute('PRAGMA query_only=ON')
                        row = db.execute(
                            "SELECT accepted,deferred,status FROM dispatch_history WHERE slot LIKE 'manual-%' "
                            'ORDER BY started_utc DESC LIMIT 1').fetchone()
                except sqlite3.OperationalError:
                    time.sleep(.1)
                    continue
                if row is not None:
                    accepted, deferred, dispatch_status = row
                    if dispatch_status != 'running':
                        break
                time.sleep(0.1)
            assert (accepted, deferred) == (50, 20), 'first batch must persist capacity-limited backlog'

        def wait_for_all_saved():
            nonlocal scheduler_saved
            expect(page.get_by_test_id('scheduler-history')).to_contain_text('完了', timeout=120000)
            assert verify_saved(root, expected_update_date='2026-10-25T10:00:00+09:00',
                                expected_versions=3, expected_entries=70)['page_count'] == 2
            timed('open_queue_after_scheduler', lambda: page.get_by_test_id('tab-queue').click(),
                  lambda: expect(page.get_by_test_id('queue-view')).to_be_visible())
            expect(page.get_by_test_id('queue-view').get_by_test_id('queue-filter-done')).to_contain_text(
                '完了 50', timeout=120000)
            active_index = json.loads((root / 'active-generations.json').read_text(encoding='utf-8'))
            assert len(active_index['entries']) == 70
            assert set(active_index['entries']) == {'321', *(str(wid) for wid in range(400, 469))}
            # Files/index/history alone can hide a failed or unknown dispatch item.
            # Assert the durable scheduler ledger itself is fully reconciled.
            ledger_deadline = time.monotonic() + 120
            item_states, deferred_count = [], -1
            while time.monotonic() < ledger_deadline:
                try:
                    scheduler_db_uri = (root / '.pixivvault-managed-scheduler.sqlite3').resolve().as_uri() + '?mode=ro'
                    with closing(sqlite3.connect(scheduler_db_uri, uri=True, timeout=1.0)) as db:
                        db.execute('PRAGMA query_only=ON')
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

        scheduler_clicked_at = time.perf_counter()
        timed('scheduler_click_to_progress_feedback',
              lambda: page.get_by_test_id('scheduler-run').click(),
              lambda: expect(page.get_by_test_id('scheduler-action-notice')).to_contain_text(
                  '作者一覧を確認中', timeout=5000))
        timed('scheduler_dispatch_admission', lambda: None, wait_for_scheduler_admission)
        timed('scheduler_visible_admission', lambda: None,
              lambda: expect(page.get_by_test_id('scheduler-action-notice')).to_contain_text(
                  '受付 50件', timeout=120000))
        ux_timings_ms['scheduler_click_to_visible_admission'] = round(
            (time.perf_counter() - scheduler_clicked_at) * 1000, 2)
        ux_status['scheduler_click_to_visible_admission'] = {'status': 'ok'}
        capture('scheduler_dispatch_accepted')
        try:
            timed('scheduler_workflow_all_saved_and_reconciled', lambda: None, wait_for_all_saved)
        finally:
            ux_timings_ms['scheduler_workflow_total_including_checks'] = round(
                (time.perf_counter() - scheduler_clicked_at) * 1000, 2)
            ux_status['scheduler_workflow_total_including_checks'] = dict(
                ux_status.get('scheduler_workflow_all_saved_and_reconciled',
                              {'status': 'error', 'error_type': 'unknown'}))
        capture('scheduler_queue_complete')
    elif scheduler_acceptance:
        timed('reopen_scheduler_history', lambda: page.get_by_test_id('tab-scheduler').click(),
              lambda: (page.get_by_test_id('scheduler-open').click(),
                       expect(page.get_by_test_id('scheduler-history')).to_contain_text('完了', timeout=10000)))
        capture('scheduler_history')
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
            'ux_timings_ms': ux_timings_ms, 'ux_status': ux_status,
            'ux_screenshots': ux_screenshots, 'ux_repeat_samples_ms': ux_repeat_samples_ms,
            'download_scroll': download_scroll,
            'download_queue_button_rect': download_queue_button_rect,
            'metadata': {
                'sidecar_absent_before_tab': metadata_absent_before_tab,
                'sidecar_created_by_explicit_open': bool(not restart and metadata_absent_before_tab
                                                         and metadata_db.is_file()),
                'enabled': bool(enabled), 'settings_revision': revision,
                'entry_count': metadata_entries,
                'fake_pixiv_request_count_before': requests_before_metadata,
                'fake_pixiv_request_count_after': requests_after_metadata,
                'fake_pixiv_request_delta': requests_after_metadata - requests_before_metadata,
                'scroll': metadata_scroll,
                'bottom_status_visible_inside_scroll_region': True,
            },
            'scheduler_settings_dispatch_receipt_history':
                (scheduler_saved or restart) if scheduler_acceptance else False,
            'original_gets': status['original_gets'].get('321', 0)}


def _scheduler_70_ledger(root):
    """Read one consistent snapshot of the owned live scheduler ledger."""
    from pixivvault_nicegui.fs_paths import sqlite_readonly_uri
    from pixivvault_nicegui.managed_downloads import _safe_path

    path = Path(root) / '.pixivvault-managed-scheduler.sqlite3'
    _safe_path(path, file=True)
    with closing(sqlite3.connect(sqlite_readonly_uri(path, immutable=False),
                                 uri=True, timeout=1.0)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        schedule = dict(db.execute(
            'SELECT interval_hours,startup_check,revision,last_status FROM schedule WHERE id=1'
        ).fetchone())
        authors = [row[0] for row in db.execute(
            'SELECT author_id FROM favourite_authors ORDER BY length(author_id),author_id')]
        history = [dict(row) for row in db.execute(
            'SELECT slot,status,authors,accepted,deferred,error_code FROM dispatch_history '
            'ORDER BY started_utc,slot')]
        items = [dict(row) for row in db.execute(
            'SELECT slot,work_id,author_id,operation_id,job_id,accepted_fingerprint,state '
            'FROM dispatch_items ORDER BY length(author_id),author_id,work_id')]
        deferred = [dict(row) for row in db.execute(
            'SELECT author_id,work_id FROM deferred_work ORDER BY author_id,work_id')]
        return dict(schedule=schedule, authors=authors, history=history,
                    items=items, deferred=deferred)


def _scheduler_70_storage_proof(root, pairs, *, observation=None):
    """Hash/decode each distinct identity; counts alone are not a saved result."""
    import hashlib
    from io import BytesIO
    from PIL import Image
    from pixivvault_nicegui.fs_paths import native_io_path
    from pixivvault_nicegui.managed_downloads import (
        IDENTITY, MARKER, MAX_PAGE_BYTES, MAX_RECEIPT_BYTES,
        _json_unique, _read_bounded, _safe_path,
    )

    root = Path(root).absolute()
    assert len(pairs) == len({aid for aid, _ in pairs}) == len({wid for _, wid in pairs}) == 70
    assert json.loads(_read_bounded(root / MARKER, 4096)) == IDENTITY
    index_path = root / 'active-generations.json'
    index_bytes = _read_bounded(index_path, 2 * 1024 * 1024)
    index = json.loads(index_bytes, object_pairs_hook=_json_unique)
    assert type(index['version']) is int and index['version'] in (2, 3)
    assert index.get('exclusions', {}) == {} and index.get('quarantine_operations', {}) == {}
    assert set(index['entries']) == {wid for _, wid in pairs}
    rows, folders = [], set()
    observation = {} if observation is None else observation
    observation['rows'] = rows
    for aid, wid in pairs:
        observation['current_identity'] = dict(author_id=aid, work_id=wid)
        entry = index['entries'][wid]
        assert entry['work_id'] == wid
        assert entry['update_date'] == '2026-10-23T10:00:00+09:00'
        assert re.fullmatch(r'[a-f0-9]{64}', entry['accepted_fingerprint'])
        relative = entry['relative_receipt']
        assert re.fullmatch(r'generations/work-' + wid + r'-[a-f0-9]{32}/receipt\.json', relative)
        receipt_path = root / relative
        receipt_bytes = _read_bounded(receipt_path, MAX_RECEIPT_BYTES)
        receipt_sha = hashlib.sha256(receipt_bytes).hexdigest()
        assert receipt_sha == entry['sha256']
        receipt = json.loads(receipt_bytes, object_pairs_hook=_json_unique)
        assert receipt['version'] == 1 and receipt['source'] == 'pixiv-original'
        assert (receipt['author_id'], receipt['work_id'], receipt['page_count']) == (aid, wid, 2)
        assert receipt['illustration_type'] == 0 and len(receipt['files']) == 2
        pages = []
        for page_no, item in enumerate(receipt['files']):
            filename = 'page-' + str(page_no + 1) + '.png'
            assert item['name'] == filename and item['format'] == 'png'
            assert (item['width'], item['height']) == (120, 84)
            payload = _read_bounded(receipt_path.parent / filename, MAX_PAGE_BYTES)
            actual_sha = hashlib.sha256(payload).hexdigest()
            assert actual_sha == item['sha256'] and len(payload) == item['bytes']
            expected_rgb = (int(wid) % 256, int(aid) % 256, 40 + page_no * 100)
            source = BytesIO()
            Image.new('RGB', (120, 84), expected_rgb).save(source, format='PNG')
            assert payload == source.getvalue(), 'original bytes belong to another synthetic identity'
            with Image.open(BytesIO(payload)) as decoded:
                decoded.load()
                assert decoded.format == 'PNG' and decoded.size == (120, 84)
                pixel = decoded.convert('RGB').getpixel((60, 42))
                assert pixel == expected_rgb
            pages.append(dict(source_page_no=page_no, file=filename, bytes=len(payload),
                              sha256=actual_sha, width=120, height=84, rgb=list(pixel)))
        _safe_path(receipt_path.parent)
        assert {path.name for path in native_io_path(receipt_path.parent).iterdir()} == {
            'receipt.json', 'page-1.png', 'page-2.png'}
        folders.add(receipt_path.parent.name)
        rows.append(dict(author_id=aid, work_id=wid, relative_receipt=relative,
                         receipt_sha256=receipt_sha, receipt_bytes=len(receipt_bytes),
                         index_accepted_fingerprint=entry['accepted_fingerprint'], pages=pages))
    _safe_path(root / 'generations')
    assert {path.name for path in native_io_path(root / 'generations').iterdir()} == folders
    assert _read_bounded(index_path, 2 * 1024 * 1024) == index_bytes, 'active index changed during proof'
    assert len({page['sha256'] for row in rows for page in row['pages']}) == 140
    observation.clear()
    observation.update(index_version=index['version'], index_sha256=hashlib.sha256(index_bytes).hexdigest(),
                       authors=70, works=70, pages=140, generations=70, rows=rows)
    return observation


def inspect_scheduler_70_authors(page, base, headers, root, *, restart,
                                 evidence_dir, previous=None, result_record=None):
    """T01 only. Caller owns browser/server/PID/port shutdown and source hashes."""
    from playwright.sync_api import expect
    from tools.check_managed_downloads_browser import SCHEDULER_70_PAIRS
    from pixivvault_nicegui.fs_paths import native_io_path

    root, evidence_dir = Path(root).absolute(), Path(evidence_dir).absolute()
    assert evidence_dir.is_dir()
    evidence_file = evidence_dir / 'scheduler-70-inspection.json'
    assert not evidence_file.exists(), 'fresh evidence directory required'
    pairs = tuple(SCHEDULER_70_PAIRS)
    expected_map = dict(pairs)
    expected_items = {(aid, wid) for aid, wid in pairs}
    record = dict(success=False, synthetic_only=True, restart=bool(restart),
                  scope='T01: 70 authors, one work each; functional evidence only',
                  lifecycle_scope='normal owned process/child/port exit is the caller gate',
                  phase='created', expected_pairs=[list(pair) for pair in pairs])
    if result_record is not None:
        result_record['scheduler_70'] = record

    def save():
        evidence_file.write_text(json.dumps(record, ensure_ascii=False, indent=2),
                                 encoding='utf-8')

    def phase(name):
        record['phase'] = name
        save()

    def status():
        response = page.request.get(base + '/_managed_test/status', headers=headers,
                                    timeout=10000)
        assert response.ok
        value = response.json()
        assert value['synthetic'] is True and value['scheduler_70_authors'] is True
        assert value['scheduler_author_works'] == expected_map
        assert value['all_requests_get'] and value['allowed_https_hosts_only']
        assert value['image_requests_cookie_free']
        return value

    def wait_ledger(predicate, label, timeout=180):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                latest = _scheduler_70_ledger(root)
            except sqlite3.OperationalError:
                page.wait_for_timeout(200)
                continue
            record['last_ledger'] = latest
            if predicate(latest):
                return latest
            page.wait_for_timeout(200)
        raise AssertionError(label)

    def complete(ledger):
        rows = ledger['items']
        return (len(rows) == 70 and not ledger['deferred']
                and {(row['author_id'], row['work_id']) for row in rows} == expected_items
                and all(row['state'] == 'saved' for row in rows))

    save()
    try:
        record['before'] = status()
        assert record['before']['request_count'] == 0, 'startup made implicit Pixiv GET'
        if restart:
            assert previous is not None and previous.get('success') is True
            assert previous['expected_pairs'] == record['expected_pairs']
        else:
            assert not native_io_path(root / '.pixivvault-managed-scheduler.sqlite3').exists()
            index = root / 'active-generations.json'
            if native_io_path(index).exists():
                assert json.loads(native_io_path(index).read_bytes())['entries'] == {}

        page.goto(base, wait_until='domcontentloaded')
        page.get_by_test_id('tab-scheduler').click()
        expect(page.get_by_test_id('scheduler-status')).to_be_visible()
        page.get_by_test_id('scheduler-open').click()
        expect(page.get_by_test_id('scheduler-run')).to_be_enabled(timeout=20000)
        opened = wait_ledger(lambda value: True, 'scheduler ledger did not open', timeout=20)
        record['opened'] = opened
        assert status()['request_count'] == 0, 'opening scheduler made implicit Pixiv GET'

        if not restart:
            phase('save-70-author-settings')
            assert opened['authors'] == [] and opened['history'] == [] and opened['items'] == []
            expect(page.get_by_test_id('scheduler-startup')).not_to_be_checked()
            page.get_by_test_id('scheduler-authors').fill(','.join(expected_map))
            page.get_by_test_id('scheduler-interval').click()
            page.get_by_text('自動チェックしない', exact=True).click()
            revision = opened['schedule']['revision']
            page.get_by_test_id('scheduler-save').click()
            configured = wait_ledger(
                lambda value: (set(value['authors']) == set(expected_map)
                               and value['schedule']['interval_hours'] == 0
                               and value['schedule']['startup_check'] == 0
                               and value['schedule']['revision'] == revision + 1),
                '70-author settings did not commit', timeout=20)
            record['configured'] = configured
            expect(page.get_by_test_id('scheduler-run')).to_be_enabled()
            assert status()['request_count'] == 0, 'settings save made implicit Pixiv GET'
            phase('manual-70-author-dispatch')
            page.get_by_test_id('scheduler-run').click()
            admitted = wait_ledger(
                lambda value: any(row['slot'].startswith('manual-') and row['status'] != 'running'
                                  for row in value['history']),
                'manual author dispatch did not finish')
            manual = [row for row in admitted['history'] if row['slot'].startswith('manual-')]
            assert len(manual) == 1
            assert (manual[0]['authors'], manual[0]['accepted'], manual[0]['deferred']) == (70, 50, 20)
            assert manual[0]['error_code'] is None
            record['admission_50_20'] = manual[0]
            record['ledger_at_admission_observation'] = admitted
            expect(page.get_by_test_id('scheduler-action-notice')).to_contain_text('受付 50件、保留 20件')
            page.screenshot(path=str(evidence_dir / 'scheduler-70-admission.png'))
            phase('wait-70-verified-saves')
        else:
            phase('restart-observe-only')
            assert set(opened['authors']) == set(expected_map)
            assert opened['schedule']['interval_hours'] == 0 and opened['schedule']['startup_check'] == 0
            expect(page.get_by_test_id('scheduler-authors')).to_have_value(','.join(sorted(expected_map)))
            assert complete(opened), 'restart lost a previously saved scheduler identity'

        final_ledger = wait_ledger(complete, '70 distinct author/work items did not reach saved')
        assert len({row['job_id'] for row in final_ledger['items']}) == 70
        assert len({row['operation_id'] for row in final_ledger['items']}) == 70
        assert all(re.fullmatch(r'[a-f0-9]{32}', row['job_id'] or '')
                   and re.fullmatch(r'[a-f0-9]{32}', row['operation_id'])
                   and re.fullmatch(r'[a-f0-9]{64}', row['accepted_fingerprint'] or '')
                   for row in final_ledger['items'])
        record['final_ledger'] = final_ledger
        phase('verify-every-receipt-and-image')
        _scheduler_70_storage_proof(root, pairs, observation=record.setdefault('saved_manifest', {}))
        # This dedicated fixture has only the static managed owner. Its
        # scheduler acceptance fingerprint is the active CAS generation's
        # fingerprint, unlike the separate combined-media admission journal.
        active = {row['work_id']: row for row in record['saved_manifest']['rows']}
        assert all(row['accepted_fingerprint'] == active[row['work_id']]['index_accepted_fingerprint']
                   for row in final_ledger['items'])
        if restart:
            assert record['saved_manifest'] == previous['saved_manifest'], 'restart changed saved bytes/identities'
            assert final_ledger['items'] == previous['final_ledger']['items'], 'restart duplicated/rebound accepted jobs'
            assert final_ledger['history'] == previous['final_ledger']['history'], 'restart started another dispatch'
        page.get_by_test_id('scheduler-refresh').click()
        expect(page.get_by_test_id('scheduler-history')).to_contain_text('完了', timeout=20000)
        record['visible_history'] = page.get_by_test_id('scheduler-history').inner_text()
        record['visible_status'] = page.get_by_test_id('scheduler-status').inner_text()
        record['after'] = status()
        if restart:
            assert record['after']['request_count'] == 0
            assert record['after']['original_gets'] == {}
            assert record['after']['scheduler_profile_gets'] == {}
            assert record['after']['scheduler_metadata_gets'] == {}
        else:
            assert record['after']['original_gets'] == {wid: 2 for _, wid in pairs}
            assert set(record['after']['scheduler_metadata_gets']) == {wid for _, wid in pairs}
            assert all(record['after']['scheduler_metadata_gets'][wid] > 0 for _, wid in pairs)
            assert all(record['after']['scheduler_profile_gets'].get(aid + ':all', 0) >= 1
                       and record['after']['scheduler_profile_gets'].get(aid + ':illusts', 0) >= 1
                       for aid, _ in pairs)
        page.screenshot(path=str(evidence_dir / 'scheduler-70-complete.png'))
        record['phase'] = 'complete'
        record['success'] = True
        return record
    except BaseException as error:
        record['error_type'] = type(error).__name__
        record['failure_phase'] = record['phase']
        import traceback
        record['failure_location'] = [
            {'file': Path(frame.filename).name, 'line': frame.lineno, 'function': frame.name}
            for frame in traceback.extract_tb(error.__traceback__)[-8:]]
        raise
    finally:
        save()



def run(channel, output):
    if output.exists():
        raise ValueError('new --output directory required')
    from playwright.sync_api import sync_playwright
    output.mkdir(parents=True)
    before = source_hashes()
    git_head_before = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=Path(__file__).resolve().parents[1],
                                     capture_output=True, text=True, check=True).stdout.strip()
    results = {'success': False, 'synthetic_only': True, 'viewports': [],
               'channel': channel,
               'git_head_before': git_head_before,
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
                            viewport_record = {'viewport': [width, height], 'restart': restart}
                            results['viewports'].append(viewport_record)
                            viewport_record.update(inspect(
                                page, base, headers, root, restart=restart,
                                expected_metadata_revision=expected_metadata_revision,
                                evidence_dir=output, viewport_label=f'{width}x{height}',
                                result_record=viewport_record))
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
        git_head_after = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=Path(__file__).resolve().parents[1],
                                        capture_output=True, text=True, check=True).stdout.strip()
        results['git_head_after'] = git_head_after
        assert git_head_before == git_head_after, 'Git HEAD changed during UX acceptance run'
        assert not results['console_errors'] and not results['page_errors']
        assert not results['foreign_requests']
        assert all(results['normal_exit']) and all(results['ports_released'])
        assert before == source_hashes()
        results['success'] = True
    except Exception as exc:
        results['failure'] = f'{type(exc).__name__}: synthetic acceptance failed'
        results['failure_trace'] = traceback.format_exc()[-3000:]
    # Produce a partial summary even when an action fails. A single viewport
    # pass is an observation, not a statistically meaningful p95 estimate.
    action_samples = {}
    for viewport in results['viewports']:
        phase = 'restart' if viewport.get('restart') else 'initial'
        size = 'x'.join(str(value) for value in viewport['viewport'])
        for action, duration in viewport.get('ux_timings_ms', {}).items():
            status = viewport.get('ux_status', {}).get(action, {}).get('status', 'unknown')
            action_samples.setdefault(f'{size}:{phase}:{action}', []).append((duration, status))
        for action, samples in viewport.get('ux_repeat_samples_ms', {}).items():
            for sample in samples:
                action_samples.setdefault(f'{size}:{phase}:{action}', []).append(
                    (sample['ms'], sample['status']))

    def percentile(values, quantile):
        ordered = sorted(values)
        if not ordered:
            return None
        position = quantile * (len(ordered) - 1)
        lower = int(position)
        fraction = position - lower
        upper = min(lower + 1, len(ordered) - 1)
        return round(ordered[lower] * (1 - fraction) + ordered[upper] * fraction, 2)

    def summary(samples):
        good = [value for value, state in samples if state == 'ok']
        return {
            'samples': len(samples), 'successful_samples': len(good),
            'failed_samples': len(samples) - len(good),
            'observed_ms': good,
            'p50_success_ms': percentile(good, .5),
            'p95_success_ms': percentile(good, .95) if len(good) >= 20 else None,
            'max_observed_ms': max(value for value, _ in samples),
            'status_counts': {state: sum(1 for _, actual in samples if actual == state)
                              for state in sorted({actual for _, actual in samples})},
        }
    results['ux_timing_summary_ms'] = {
        key: summary(samples)
        for key, samples in sorted(action_samples.items())
    }
    results['ux_timing_caveat'] = (
        'Synthetic app; one-shot actions n=1 per viewport/phase. Repeated warm actions n=20; '
        'p95 intentionally null for n<20. '
        'Playwright auto-wait and screenshots/capture overhead are not physical-paint timing; '
        'shell_ready_after_server_health starts after server health and browser launch (not a cold process launch). '
        'enqueue_to_queue_completion_user_flow includes manual tab navigation, not screenshots. '
        'Scheduler workload metrics include ledger verification and acceptance checks; '
        'queue/dispatch durations reflect synthetic work, not actual Pixiv network performance.')
    results['source_hashes'] = before
    (output / 'result.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if results['success'] else 1


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--channel', choices=('msedge', 'chromium'), default='chromium')
    cli.add_argument('--output', type=Path, required=True)
    args = cli.parse_args()
    raise SystemExit(run(args.channel, args.output))
