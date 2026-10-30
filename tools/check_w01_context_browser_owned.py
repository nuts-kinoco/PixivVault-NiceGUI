"""Owned generated W01 right-click acceptance; no original files or external API.

Two viewports use the real shell, LibraryView, BookmarkView, SavedSimilaritySource
and media routes. A generated vector DB is frozen before use. The only fixture
delay holds one real search so an actual close/tab action can invalidate it.
This is synthetic functional evidence, not native or performance acceptance.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import struct
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ''):
    sys.path.insert(0, str(ROOT))

from tools.check_library_calendar_slider_owned import (
    OwnedTree, deny_external_connections, owned_output, write_new,
)
from tools.run_browser_checks import (
    child_environment, free_port, port_released, source_hashes, wait_for_health,
)

HOST = '127.0.0.1'
SCOPE = 'W01 original author/bookmark context entries'
OWNER_FILE = 'w01-run-owner.json'
PREFIX = '/_w01_fixture'
VIEWPORTS = ((1280, 720), (1920, 1080))


def helpers():
    names = (Path(__file__).name, 'check_library_calendar_slider_owned.py',
             'run_browser_checks.py', 'check_native_window.py',
             'native_restore_gate.py', 'native_ui_guard.py', 'native_failed_csv.py')
    return {f'tools/{name}': hashlib.sha256((ROOT / 'tools' / name).read_bytes()).hexdigest()
            for name in names}


def signature(path):
    info = path.stat()
    return {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'size': info.st_size, 'mtime_ns': info.st_mtime_ns,
            'sidecars': [suffix for suffix in ('-wal', '-shm', '-journal')
                         if Path(str(path) + suffix).exists()]}


def api(base, nonce, action, *, post=False):
    request = urllib.request.Request(
        base + PREFIX + '/' + action, method='POST' if post else 'GET',
        headers={'X-Acceptance': nonce}, data=b'' if post else None)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=5) as response:
        if response.status != 200:
            raise RuntimeError('fixture endpoint did not return 200')
        return json.loads(response.read(128 * 1024))


def prepare(services, output):
    from pixivvault_nicegui.library_models import LibrarySnapshot
    from pixivvault_nicegui.similar_saved import MODEL, SavedSimilaritySource

    # Fixture metadata only. Demo IDs and image bytes remain generated; no
    # external profile is opened. One cached bookmark has no saved-library row.
    original = services.library_snapshot()
    old_authors = original.authors
    mapping = {author.author_id: str(9101 + index)
               for index, author in enumerate(old_authors[:3])}
    remote = services.bookmark_remote_snapshot()
    local_ids = {art.work_id for art in services.bookmark_local_snapshot()}
    unsaved = next(art for art in reversed(remote) if art.work_id not in local_ids)
    services.library = LibrarySnapshot(tuple(
        replace(work, author_id=mapping.get(work.author_id, work.author_id))
        for work in original.works if work.work_id != unsaved.work_id))
    snapshot = services.library_snapshot()
    author_a, author_b = snapshot.authors[:2]
    representative = author_a.representative
    fallback = next(work for work in author_b.works
                    if work.work_id != author_b.representative.work_id)
    hit = next(work for work in snapshot.works
               if work.author_id not in {author_a.author_id, author_b.author_id}
               and work.page_count >= 2)
    bookmark = next(work for work in snapshot.works if work.work_id in local_ids)
    invalid_author = next(author for author in snapshot.authors
                          if not author.author_id.isdecimal())
    rows = [
        (representative.work_id, 1, 'composition', [1.0, 0.0]),
        (fallback.work_id, 1, 'style', [1.0, 0.0]),
        (bookmark.work_id, 1, 'composition', [1.0, 0.0]),
        (hit.work_id, 2, 'composition', [0.8, 0.6]),
        (hit.work_id, 2, 'style', [0.8, 0.6]),
    ]
    if representative.page_count >= 2:
        rows.append((representative.work_id, 2, 'composition', [1.0, 0.0]))
    db = output / 'generated-vectors.sqlite3'
    with sqlite3.connect(db) as connection:
        connection.execute('CREATE TABLE work_embeddings '
                           '(work_id TEXT,page INTEGER,kind TEXT,model TEXT,dim INTEGER,vec BLOB)')
        connection.executemany('INSERT INTO work_embeddings VALUES (?,?,?,?,?,?)',
            [(wid, page, kind, MODEL, len(vector), struct.pack('<2f', *vector))
             for wid, page, kind, vector in rows])
    frozen = signature(db)
    assert not frozen['sidecars']
    services.similarity_source = source = SavedSimilaritySource(db)
    manifest = {
        'data': 'synthetic', 'vector_database': db.name, 'vector_signature': frozen,
        'model': MODEL, 'candidate_scope': 'all pages', 'top_k': 30, 'minimum_similarity': 0.50,
        'representative_author': author_a.author_id, 'representative': representative.work_id,
        'representative_title': representative.artwork.title,
        'fallback_author': author_b.author_id,
        'fallback_visual_representative': author_b.representative.work_id,
        'fallback_query': fallback.work_id, 'fallback_title': fallback.artwork.title,
        'bookmark': bookmark.work_id, 'bookmark_title': bookmark.artwork.title,
        'hit': hit.work_id, 'hit_page': 2, 'hit_total_pages': hit.page_count,
        'unsaved': unsaved.work_id, 'unsaved_title': unsaved.title,
        'invalid_profile_author': invalid_author.author_id,
        'no_original_db': True, 'network_reader': False, 'managed_downloads': False,
        'cold_performance_claim': False,
    }
    write_new(output / 'fixture-manifest.json', manifest)
    return source, manifest, db, frozen


def serve(port, output, nonce):
    output = owned_output(output)
    owner = json.loads((output / OWNER_FILE).read_text(encoding='utf-8'))
    if not secrets.compare_digest(owner['nonce_sha256'], hashlib.sha256(nonce.encode()).hexdigest()):
        raise ValueError('fixture ownership token mismatch')
    network = deny_external_connections()
    from fastapi import Header, HTTPException
    from nicegui import app, ui
    from pixivvault_nicegui.services import get_services
    from pixivvault_nicegui.media_routes import register_media_routes
    from pixivvault_nicegui.ui.shell import build_demo_page

    services = get_services(app)
    if (services.data_mode != 'synthetic' or services.live_client is not None
            or services.managed_downloads is not None or services.jobs is not None):
        raise RuntimeError('default generated service contract changed')
    source, manifest, db, frozen = prepare(services, output)
    main_thread = threading.get_ident()
    lock = threading.Lock()
    release = threading.Event()
    trace = []
    forbidden = {}
    state = {'armed': False, 'waiting': False, 'overflow': False, 'stopping': False}

    def reject(name):
        def blocked(*_args, **_kwargs):
            with lock:
                forbidden[name] = forbidden.get(name, 0) + 1
            raise AssertionError('W01 must not fetch metadata, refresh, or enqueue')
        return blocked

    # Observe the actual product boundaries; do not supply fabricated results.
    for name in ('lookup_managed_work_id', 'save_explicit_work', 'refresh_bookmark_cache',
                 'enqueue_managed_download'):
        setattr(services, name, reject(name))
    services.images.prepare_artwork = reject('prepare_artwork')
    real_search, real_author = source.search, source.search_author

    def observed(kind, real, args):
        with lock:
            if len(trace) >= 32:
                state['overflow'] = True
                raise RuntimeError('bounded search observation exhausted')
            entry = {'sequence': len(trace) + 1, 'kind': kind, 'complete': False,
                     'off_ui_thread': threading.get_ident() != main_thread}
            trace.append(entry)
            hold = kind == 'author' and state['armed']
            if hold:
                state['armed'] = False
                state['waiting'] = True
        try:
            if hold and not release.wait(10):
                raise TimeoutError('owned delayed search was not released')
            result = real(*args)
            chosen, payload = result if kind == 'author' else (args[0], result)
            with lock:
                entry.update(chosen=chosen, status=payload['status'],
                             results=[list(key) for key, _score in payload['results']],
                             search_kind=payload['kind'], complete=True)
            return result
        except BaseException as exc:
            with lock:
                entry.update(error=type(exc).__name__, complete=True)
            raise
        finally:
            if hold:
                with lock:
                    state['waiting'] = False

    source.search = lambda work_id, page=1: observed('work', real_search, (work_id, page))
    source.search_author = lambda work_ids, representative_id: observed(
        'author', real_author, (work_ids, representative_id))
    register_media_routes(app, services.images)
    ui.page('/')(build_demo_page)

    def auth(value):
        if not secrets.compare_digest(value or '', nonce):
            raise HTTPException(status_code=403)

    @app.get('/healthz')
    def health():
        return {'status': 'ok', 'mode': 'demo', 'data': 'synthetic'}

    @app.get(PREFIX + '/owner', include_in_schema=False)
    def identity(x_acceptance: str | None = Header(default=None)):
        auth(x_acceptance)
        return {'pid': os.getpid(), 'data': 'synthetic', 'network_reader': False,
                'managed_downloads': False, 'jobs': False}

    @app.get(PREFIX + '/state', include_in_schema=False)
    def observations(x_acceptance: str | None = Header(default=None)):
        auth(x_acceptance)
        with lock:
            return {'trace': [dict(item) for item in trace], 'forbidden': dict(forbidden),
                    **state, 'jobs_present': services.jobs is not None,
                    'network_reader': services.live_client is not None,
                    'denied_external_connections': network['denied']}

    @app.post(PREFIX + '/arm', include_in_schema=False)
    def arm(x_acceptance: str | None = Header(default=None)):
        auth(x_acceptance)
        with lock:
            if state['armed'] or state['waiting'] or state['stopping']:
                raise HTTPException(status_code=409)
            release.clear()
            state['armed'] = True
        return {'armed': True}

    @app.post(PREFIX + '/release', include_in_schema=False)
    def resume(x_acceptance: str | None = Header(default=None)):
        auth(x_acceptance)
        with lock:
            if not state['waiting']:
                raise HTTPException(status_code=409)
        release.set()
        return {'released': True}

    @app.post(PREFIX + '/shutdown', include_in_schema=False)
    async def shutdown(x_acceptance: str | None = Header(default=None)):
        auth(x_acceptance)
        with lock:
            if state['stopping']:
                raise HTTPException(status_code=409)
            state['stopping'] = True
        release.set()
        app.shutdown()
        return {'stopping': True}

    async def close_services():
        close_error = None
        try:
            release.set()
            await asyncio.to_thread(services.close)
        except BaseException as exc:
            close_error = type(exc).__name__
            raise
        finally:
            with lock:
                final_trace = [dict(item) for item in trace]
                forbidden_final = dict(forbidden)
                final_state = dict(state)
            after = signature(db)
            write_new(output / 'server-lifecycle.json', {
                'pid': os.getpid(), 'data': 'synthetic', 'services_closed': services.closed,
                'source_closed': source._closed, 'close_error': close_error,
                'shutdown_requested': final_state['stopping'],
                'vector_before': frozen, 'vector_after': after,
                'vector_unchanged': frozen == after, 'trace': final_trace,
                'forbidden': forbidden_final, 'jobs_present': services.jobs is not None,
                'network_reader': services.live_client is not None,
                'denied_external_connections': network['denied'],
                'trace_complete': bool(final_trace) and all(item['complete'] for item in final_trace),
                'observation_overflow': final_state['overflow'],
            })

    app.on_shutdown(close_services)
    ui.run(host=HOST, port=port, show=False, reload=False,
           binding_refresh_interval=None, timeout_graceful_shutdown=5)
    return 0


def inspect(base, nonce, output, manifest, channel, headed):
    from playwright.sync_api import sync_playwright, expect
    output.mkdir(parents=True, exist_ok=False)
    result = {'success': False, 'scope': SCOPE, 'data': 'synthetic', 'viewports': {},
              'console_errors': [], 'page_errors': [], 'blocked_foreign_requests': [],
              'phase': 'launch', 'external_navigation_tested': False}
    context = page = None

    def phase(value):
        result['phase'] = value

    def wait_state(predicate, seconds=8):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            value = api(base, nonce, 'state')
            if predicate(value):
                return value
            time.sleep(0.05)
        raise AssertionError('fixture observation did not reach the expected state')

    def latest(previous, kind, chosen):
        value = wait_state(lambda state: len(state['trace']) == previous + 1
                           and state['trace'][-1]['complete'])
        entry = value['trace'][-1]
        assert (entry['kind'], entry['chosen']) == (kind, chosen), entry
        assert entry['off_ui_thread'] and not entry.get('error'), entry
        assert not value['forbidden'] and not value['jobs_present']
        assert not value['network_reader'] and value['denied_external_connections'] == 0
        return entry

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                channel=channel if channel == 'msedge' else None, headless=not headed,
                args=['--disable-background-networking', '--disable-component-update'])
            try:
                for width, height in VIEWPORTS:
                    key = f'{width}x{height}'
                    out = output / key
                    out.mkdir()
                    phase(key + ':open')
                    context = browser.new_context(viewport={'width': width, 'height': height})
                    def route(request_route):
                        url = request_route.request.url
                        if url.startswith(base + '/') or url.startswith(('data:', 'blob:')):
                            request_route.continue_()
                        else:
                            result['blocked_foreign_requests'].append(
                                {'method': request_route.request.method,
                                 'type': request_route.request.resource_type})
                            request_route.abort()
                    context.route('**/*', route)
                    page = context.new_page()
                    page.set_default_timeout(10000)
                    page.on('console', lambda event: result['console_errors'].append(event.text[:500])
                            if event.type == 'error' else None)
                    page.on('pageerror', lambda error: result['page_errors'].append(str(error)[:500]))
                    page.goto(base, wait_until='domcontentloaded')
                    page.bring_to_front()
                    page.get_by_test_id('tab-library').click()
                    library = page.get_by_test_id('library-view')
                    expect(library).to_have_attribute('data-active', 'true')
                    detail = page.get_by_test_id('library-detail')
                    viewer = page.get_by_test_id('library-viewer')
                    status = page.get_by_test_id('similar-status')

                    def author_card(aid):
                        return library.locator(f'.pv-library-author-card[data-author-id="{aid}"]')
                    def decoded_image(card):
                        image = card.locator('.q-img img:visible').first
                        image.scroll_into_view_if_needed()
                        handle = image.element_handle()
                        page.wait_for_function('el => el.complete && el.naturalWidth > 0',
                                               arg=handle, timeout=5000)
                        return image
                    def right_click(card, action):
                        decoded_image(card).click(button='right')
                        button = page.locator(f'[data-testid="{action}"]:visible')
                        expect(button).to_have_count(1)
                        expect(detail).to_be_hidden()
                        expect(viewer).to_be_hidden()
                        return button
                    def close_similar():
                        page.get_by_test_id('similar-close').click()
                        expect(status).to_be_hidden()

                    phase(key + ':profile-and-left-click')
                    card = author_card(manifest['representative_author'])
                    profile = card.get_by_test_id('library-author-profile')
                    expect(profile).to_be_enabled()
                    expect(profile).to_have_attribute(
                        'href', 'https://www.pixiv.net/users/' + manifest['representative_author'])
                    expect(profile).to_have_attribute('target', '_blank')
                    assert set(profile.get_attribute('rel').split()) == {'noopener', 'noreferrer'}
                    expect(author_card(manifest['invalid_profile_author']).get_by_test_id(
                        'library-author-profile')).to_be_disabled()
                    decoded_image(card).click()
                    expect(detail).to_be_visible()
                    library.get_by_test_id('library-back').click()
                    expect(detail).to_be_hidden()

                    phase(key + ':author-representative-p1')
                    before = len(api(base, nonce, 'state')['trace'])
                    button = right_click(card, 'library-author-similar-open')
                    page.screenshot(path=str(out / 'author-image-context.png'))
                    button.click()
                    expect(status).to_contain_text('類似度0.50以上')
                    representative = latest(before, 'author', manifest['representative'])
                    assert [manifest['hit'], 2] in representative['results']
                    assert all(item[0] != manifest['representative'] for item in representative['results'])
                    expect(page.get_by_text('「' + manifest['representative_title'] + '」に似た絵',
                                            exact=True)).to_be_visible()
                    close_similar()

                    phase(key + ':author-embedded-p1-fallback')
                    fallback_card = author_card(manifest['fallback_author'])
                    expect(fallback_card).to_have_attribute(
                        'data-representative-id', manifest['fallback_visual_representative'])
                    before = len(api(base, nonce, 'state')['trace'])
                    right_click(fallback_card, 'library-author-similar-open').click()
                    expect(status).to_contain_text('類似度0.50以上')
                    fallback = latest(before, 'author', manifest['fallback_query'])
                    assert fallback['search_kind'] == 'style'
                    expect(page.get_by_text('「' + manifest['fallback_title'] + '」に似た絵',
                                            exact=True)).to_be_visible()
                    page.screenshot(path=str(out / 'fallback-similarity.png'))
                    close_similar()
                    expect(fallback_card).to_have_attribute(
                        'data-representative-id', manifest['fallback_visual_representative'])

                    phase(key + ':bookmark-selected-p1-to-result-p2')
                    library.get_by_test_id('library-subview-bookmarks').click()
                    bookmarks = page.get_by_test_id('bookmark-view')
                    search = bookmarks.get_by_role('textbox', name='ブックマーク内を検索', exact=True)
                    search.fill(manifest['bookmark'])
                    book = bookmarks.locator(
                        f'[data-testid=bookmark-card][data-work-id="{manifest["bookmark"]}"]')
                    expect(bookmarks.get_by_test_id('bookmark-card')).to_have_count(1)
                    expect(book.get_by_test_id('bookmark-downloaded')).to_be_visible()
                    checkbox = book.get_by_test_id('bookmark-select')
                    checkbox.click()
                    expect(checkbox).to_have_attribute('aria-checked', 'true')
                    expect(bookmarks.get_by_test_id('bookmark-selection')).to_have_text('選択中: 1件')
                    before = len(api(base, nonce, 'state')['trace'])
                    right_click(book, 'bookmark-similar-open').click()
                    expect(status).to_contain_text('類似度0.50以上')
                    bookmarked = latest(before, 'work', manifest['bookmark'])
                    result_button = page.get_by_test_id('similar-result').filter(
                        has_text=manifest['hit'] + ' · 2ページ')
                    expect(result_button).to_have_count(1)
                    result_button.click()
                    expect(viewer).to_be_visible()
                    expect(viewer.locator('.pv-counter')).to_have_text(
                        f'2/{manifest["hit_total_pages"]}')
                    image = page.get_by_test_id('library-viewer-image').locator('img:visible')
                    handle = image.element_handle()
                    expected_path = '/media/' + manifest['hit'] + '/1/view.png'
                    page.wait_for_function(
                        '(arg) => arg.el.complete && arg.el.naturalWidth > 0 && '
                        'new URL(arg.el.currentSrc || arg.el.src).pathname === arg.path',
                        arg={'el': handle, 'path': expected_path}, timeout=5000)
                    page.screenshot(path=str(out / 'result-page2.png'))
                    viewer.get_by_role('button', name='閉じる', exact=True).click()
                    expect(viewer).to_be_hidden()
                    library.get_by_test_id('library-subview-bookmarks').click()
                    expect(bookmarks.get_by_role('textbox', name='ブックマーク内を検索', exact=True)).to_have_value(manifest['bookmark'])
                    expect(bookmarks.get_by_test_id('bookmark-selection')).to_have_text('選択中: 1件')
                    expect(bookmarks.locator(
                        f'[data-work-id="{manifest["bookmark"]}"]').get_by_test_id(
                            'bookmark-select')).to_have_attribute('aria-checked', 'true')

                    phase(key + ':unsaved-bookmark-reason')
                    search.fill(manifest['unsaved'])
                    unsaved = bookmarks.locator(
                        f'[data-testid=bookmark-card][data-work-id="{manifest["unsaved"]}"]')
                    expect(bookmarks.get_by_test_id('bookmark-card')).to_have_count(1)
                    expect(unsaved.get_by_test_id('bookmark-downloaded')).to_have_count(0)
                    before = len(api(base, nonce, 'state')['trace'])
                    right_click(unsaved, 'bookmark-similar-open').click()
                    expect(status).to_have_text('このページの保存済み解析データがありません。')
                    missing = latest(before, 'work', manifest['unsaved'])
                    assert missing['status'] == 'no_query_embedding' and not missing['results']
                    expect(page.get_by_test_id('similar-result')).to_have_count(0)
                    page.screenshot(path=str(out / 'unsaved-reason.png'))
                    close_similar()
                    expect(bookmarks.get_by_test_id('bookmark-selection')).to_have_text('選択中: 1件')

                    phase(key + ':pending-close-and-tab-stale')
                    library.get_by_test_id('library-subview-downloaded').click()
                    before = len(api(base, nonce, 'state')['trace'])
                    assert api(base, nonce, 'arm', post=True) == {'armed': True}
                    right_click(author_card(manifest['fallback_author']),
                                'library-author-similar-open').click()
                    expect(status).to_have_text('似た絵を探しています…')
                    wait_state(lambda value: value['waiting'])
                    close_similar()
                    library.get_by_test_id('library-subview-bookmarks').click()
                    assert api(base, nonce, 'release', post=True) == {'released': True}
                    stale = latest(before, 'author', manifest['fallback_query'])
                    until = time.monotonic() + 0.4
                    while time.monotonic() < until:
                        expect(status).to_be_hidden()
                        expect(viewer).to_be_hidden()
                        page.wait_for_timeout(50)
                    expect(bookmarks.get_by_test_id('bookmark-selection')).to_have_text('選択中: 1件')
                    page.screenshot(path=str(out / 'stale-remained-closed.png'))
                    final = api(base, nonce, 'state')
                    assert not final['overflow'] and not final['waiting'] and not final['armed']
                    result['viewports'][key] = {
                        'representative_query': representative, 'fallback_query': fallback,
                        'bookmark_query': bookmarked, 'unsaved_query': missing,
                        'stale_query': stale, 'actual_result_page_path': expected_path,
                        'actual_image_context_menu': True, 'left_click_author_open': True,
                        'profile_canonical_href': True, 'invalid_profile_disabled': True,
                        'selection_preserved': True, 'pending_result_discarded': True,
                        'external_get_attempts': 0, 'jobs_created': 0,
                    }
                    context.close()
                    context = page = None
            except Exception:
                result['operation_error'] = traceback.format_exc()
                if page is not None and not page.is_closed():
                    try:
                        page.screenshot(path=str(output / 'failure.png'), timeout=5000)
                        result['failure_screenshot'] = 'failure.png'
                    except Exception as exc:
                        result['failure_screenshot_error'] = type(exc).__name__
                raise
            finally:
                if context is not None:
                    try:
                        context.close()
                    except Exception as exc:
                        result['context_close_error'] = type(exc).__name__
                browser.close()
        assert set(result['viewports']) == {'1280x720', '1920x1080'}
        assert not result['console_errors'] and not result['page_errors']
        assert not result['blocked_foreign_requests'] and 'context_close_error' not in result
        result['success'] = True
    except Exception:
        result['error'] = traceback.format_exc()
    finally:
        try:
            result['server_observations'] = api(base, nonce, 'state')
        except Exception as exc:
            result['observation_error'] = type(exc).__name__
            result['success'] = False
        write_new(output / 'result.json', result)
    return 0 if result['success'] else 1


def run(output, channel, timeout, headed):
    from tools import check_native_window as native
    output = owned_output(output)
    output.mkdir(parents=True, exist_ok=False)
    (output / 'tmp').mkdir()
    nonce = secrets.token_urlsafe(32)
    write_new(output / OWNER_FILE, {'runner_pid': os.getpid(), 'scope': SCOPE,
              'nonce_sha256': hashlib.sha256(nonce.encode()).hexdigest()})
    before, helper_before = source_hashes(), helpers()
    env = child_environment()
    env.update(PYTHONDONTWRITEBYTECODE='1', TEMP=str(output / 'tmp'), TMP=str(output / 'tmp'))
    port = free_port()
    base = f'http://{HOST}:{port}'
    processes = {}
    trees = {}
    failures = []
    endpoint = lifecycle = ui_result = None
    shutdown = {'attempted': False, 'acknowledged': False}
    reports = {}

    def fail(value):
        if value not in failures:
            failures.append(value)

    with (output / 'server.log').open('xb') as server_log, (output / 'checker.log').open('xb') as checker_log:
        try:
            server = subprocess.Popen(
                [sys.executable, '-B', str(Path(__file__).resolve()), '--serve', str(port),
                 '--nonce', nonce, '--output', str(output)], cwd=str(output), env=env,
                stdin=subprocess.DEVNULL, stdout=server_log, stderr=subprocess.STDOUT)
            processes['server'] = server
            trees['server'] = OwnedTree(server, native)
            wait_for_health(base, server, timeout=35, expected_data='synthetic')
            identity = api(base, nonce, 'owner')
            if identity != {'pid': identity.get('pid'), 'data': 'synthetic',
                            'network_reader': False, 'managed_downloads': False, 'jobs': False}:
                raise RuntimeError('synthetic endpoint identity was not proven')
            endpoint = trees['server'].observed_endpoint(identity['pid'])
            if endpoint is None:
                raise RuntimeError('endpoint PID/birth is not in the observed owned tree')
            command = [sys.executable, '-B', str(Path(__file__).resolve()),
                       '--inspect-url', base, '--nonce', nonce,
                       '--output', str(output), '--channel', channel]
            if headed:
                command.append('--headed')
            checker = subprocess.Popen(command, cwd=str(output), env=env,
                stdin=subprocess.DEVNULL, stdout=checker_log, stderr=subprocess.STDOUT)
            processes['checker'] = checker
            trees['checker'] = OwnedTree(checker, native)
            deadline = time.monotonic() + timeout
            while checker.poll() is None and time.monotonic() < deadline:
                if server.poll() is not None:
                    fail('server_exited_during_ui_operations')
                    break
                time.sleep(0.1)
            if checker.poll() is None:
                fail('checker_not_finished')
            elif checker.returncode != 0:
                fail('checker_failed')
            path = output / 'ui' / 'result.json'
            if path.is_file() and checker.poll() is not None:
                ui_result = json.loads(path.read_text(encoding='utf-8'))
            if not (isinstance(ui_result, dict) and ui_result.get('success') is True
                    and ui_result.get('scope') == SCOPE
                    and set(ui_result.get('viewports', {})) == {'1280x720', '1920x1080'}):
                fail('w01_operations_not_accepted')
        except Exception:
            fail('runner_failed')
            write_new(output / 'runner-error.json', {'traceback': traceback.format_exc()})
        finally:
            if 'server' in trees:
                try:
                    if endpoint is not None and trees['server'].observed_endpoint(endpoint['pid']) == endpoint:
                        shutdown['attempted'] = True
                        shutdown['endpoint_owner_verified'] = True
                        shutdown['acknowledged'] = api(base, nonce, 'shutdown', post=True) == {'stopping': True}
                    else:
                        fail('endpoint_owner_missing_before_shutdown')
                except Exception as exc:
                    fail(type(exc).__name__ + ': normal_shutdown_unconfirmed')
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                for tree in trees.values():
                    tree.sample()
                if (all(process.poll() is not None for process in processes.values())
                        and all(tree.last_living == [] for tree in trees.values())):
                    break
                time.sleep(0.1)
            reports = {name: tree.finish() for name, tree in trees.items()}
            if set(reports) != {'server', 'checker'}:
                fail('both_owned_roots_not_observed')
            for name, report in reports.items():
                if (report['exit_code'] != 0 or report['living'] != []
                        or report['errors'] or not report['observed']):
                    fail(name + '_normal_exit_not_proven')
            if not shutdown['attempted'] or not shutdown['acknowledged']:
                fail('normal_shutdown_not_acknowledged')
    try:
        released = port_released(port)
        if not released:
            fail('app_port_not_released')
        lifecycle = json.loads((output / 'server-lifecycle.json').read_text(encoding='utf-8'))
        if not (endpoint is not None and lifecycle['pid'] == endpoint['pid']
                and lifecycle['shutdown_requested'] and lifecycle['services_closed']
                and lifecycle['source_closed'] and lifecycle['close_error'] is None
                and lifecycle['vector_unchanged'] and not lifecycle['vector_after']['sidecars']
                and not lifecycle['forbidden'] and not lifecycle['jobs_present']
                and not lifecycle['network_reader'] and lifecycle['denied_external_connections'] == 0
                and lifecycle['trace_complete'] and not lifecycle['observation_overflow']
                and all(not row.get('error') and row['off_ui_thread'] for row in lifecycle['trace'])):
            fail('source_preservation_or_resource_close_not_proven')
    except Exception as exc:
        released = False
        fail(type(exc).__name__ + ': final_lifecycle_observation_failed')
    try:
        after, helper_after = source_hashes(), helpers()
    except Exception as exc:
        after, helper_after = {}, {}
        fail(type(exc).__name__ + ': final_hash_observation_failed')
    if before != after or helper_before != helper_after:
        fail('source_or_helper_changed')
    result = {'success': not failures, 'scope': SCOPE, 'data': 'synthetic',
              'ui_result': 'ui/result.json', 'port': port, 'endpoint_owner': endpoint,
              'normal_shutdown': shutdown, 'processes': reports, 'port_released': released,
              'forced_cleanup': 0, 'failures': failures, 'lifecycle': lifecycle,
              'source_before': before, 'source_after': after, 'source_changed': before != after,
              'helpers_before': helper_before, 'helpers_after': helper_after,
              'helpers_changed': helper_before != helper_after}
    write_new(output / 'result.json', result)
    print(json.dumps({'success': result['success'], 'failures': failures,
                      'evidence': str(output / 'result.json')}, ensure_ascii=False))
    return 0 if result['success'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--channel', choices=('msedge', 'chromium'), default='msedge')
    parser.add_argument('--timeout', type=float, default=300)
    parser.add_argument('--headed', action='store_true')
    parser.add_argument('--serve', type=int)
    parser.add_argument('--inspect-url')
    parser.add_argument('--nonce')
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('Windows PID/birth observation is required')
    if not 1 <= args.timeout <= 600:
        parser.error('--timeout must be 1..600 seconds')
    if args.serve is not None:
        if args.inspect_url or not args.nonce or not 1024 <= args.serve <= 65535:
            parser.error('invalid owned fixture arguments')
        return serve(args.serve, args.output, args.nonce)
    if args.inspect_url:
        parsed = urllib.parse.urlsplit(args.inspect_url)
        if (not args.nonce or parsed.scheme != 'http' or parsed.hostname != HOST
                or parsed.username or parsed.password or parsed.path
                or parsed.query or parsed.fragment or not parsed.port):
            parser.error('only the owned loopback fixture URL is accepted')
        output = owned_output(args.output)
        owner = json.loads((output / OWNER_FILE).read_text(encoding='utf-8'))
        if not secrets.compare_digest(owner['nonce_sha256'], hashlib.sha256(args.nonce.encode()).hexdigest()):
            parser.error('owned checker token mismatch')
        manifest = json.loads((output / 'fixture-manifest.json').read_text(encoding='utf-8'))
        return inspect(args.inspect_url, args.nonce, output / 'ui', manifest, args.channel, args.headed)
    if args.nonce:
        parser.error('--nonce is reserved for owned children')
    return run(args.output, args.channel, args.timeout, args.headed)


if __name__ == '__main__':
    raise SystemExit(main())

