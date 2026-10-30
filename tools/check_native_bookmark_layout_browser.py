"""Focused S10 Edge check using an ordinary generated native profile.

The product bootstrap, services, settings, bookmark UI, jobs and file projection
owners remain real. Only the GET transport returns generated fixture payloads.
Ordinary profile startup includes discovery initial_load (bootstrap -> services
-> DiscoveryLearningService.initialize_once); its requests are retained as a
separate startup phase. Settings add zero GETs, and only the explicit complete
bookmark refresh binds the export account. This is browser acceptance, not a
native WebView/OS picker or real-account acceptance claim.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from hashlib import sha256
from html import escape
from io import BytesIO
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
from threading import Event, Lock, Thread
import time
import traceback
from urllib.parse import parse_qs, urlsplit
import urllib.request

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.check_p3_save_exports_browser import visible_job
from tools.run_browser_checks import (
    child_environment, free_port, port_released, source_hashes, wait_for_health)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

FIXTURE_MARKER = b'{"purpose":"s10-native-profile-generated-v1"}\n'
COOKIE_BYTES = (b'# Netscape HTTP Cookie File\n'
                b'.pixiv.net\tTRUE\t/\tTRUE\t2147483647\tPHPSESSID\t55_generated_s10\n')


def owned_path(path):
    from pixivvault_nicegui.fs_paths import logical_path, native_io_path
    from pixivvault_nicegui.managed_downloads import _safe_path
    path = Path(path).absolute()
    _safe_path(path)
    path = logical_path(native_io_path(path).resolve())
    if path == ROOT / 'artifacts' or not path.is_relative_to(ROOT / 'artifacts'):
        raise ValueError('dedicated candidate artifacts path required')
    return path


def packet(body):
    return json.dumps({'error': False, 'message': '', 'body': body}).encode()


class GeneratedHTTP:
    """No fallback transport and no POST capability; every payload is generated."""
    original_url = 'https://i.pximg.net/img-original/img/2026/10/29/00/00/00/701_p0.png'
    thumbnail_url = 'https://i.pximg.net/c/360x360_70/img-master/701_p0_master1200.jpg'

    def __init__(self):
        from PIL import Image
        stream = BytesIO()
        Image.new('RGB', (48, 32), (23, 81, 140)).save(stream, 'PNG')
        self.png = stream.getvalue()
        self._lock = Lock()
        self._calls = []
        self._unexpected = []

    def observations(self):
        with self._lock:
            return list(self._calls), list(self._unexpected)

    def __call__(self, request, timeout, limit, deadline):
        parsed = urlsplit(request.full_url)
        query = parse_qs(parsed.query)
        method = request.get_method()
        group = ('bookmarks' if parsed.path.endswith('/bookmarks') else
                 'detail' if parsed.path == '/ajax/illust/701' else
                 'pages' if parsed.path == '/ajax/illust/701/pages' else
                 'original' if request.full_url == self.original_url else
                 'thumbnail' if request.full_url == self.thumbnail_url else
                 'identity' if parsed.path in ('/', '/ajax/user/extra') else
                 'discovery')
        event = dict(method=method, origin=parsed.scheme + '://' + parsed.netloc,
                     path=parsed.path, query=query, group=group)
        with self._lock:
            self._calls.append(event)
        if method == 'GET' and parsed.scheme == 'https' and parsed.netloc == 'www.pixiv.net':
            if parsed.path == '/' and not query:
                content = escape(json.dumps({'userData': {'id': '55'}}))
                return ('<html><head><meta id="meta-global-data" content="' +
                        content + '"></head></html>').encode()
            if parsed.path == '/ajax/user/extra':
                return packet({'userId': '55'})
            if parsed.path == '/ajax/user/55/following':
                assert query['rest'][0] in ('show', 'hide')
                return packet({'users': []})
            if parsed.path == '/ranking.php':
                return json.dumps({'contents': []}).encode()
            if parsed.path.startswith('/ajax/search/artworks/'):
                return packet({'illustManga': {'data': [], 'total': 0}, 'relatedTags': []})
            if re.fullmatch(r'/ajax/user/[1-9][0-9]*/recommends', parsed.path):
                return packet({'users': [], 'thumbnails': {'illust': []}})
            if parsed.path in ('/ajax/user/55/illusts/bookmarks',
                               '/ajax/user/55/novels/bookmarks'):
                assert query['rest'][0] in ('show', 'hide')
                works = []
                if ('/illusts/' in parsed.path and query['rest'][0] == 'show'
                        and query['offset'][0] == '0'):
                    works = [dict(id='701', title='Generated 701', userId='91',
                                  userName='Generated author', illustType=0,
                                  pageCount=1, tags=[], xRestrict=0,
                                  createDate='2026-10-29T00:00:00Z', url=self.thumbnail_url)]
                return packet({'works': works})
            if parsed.path == '/ajax/illust/701' and not query:
                # The real bookmark-download click resolves the selected ID
                # again before admitting its managed job. Keep this generated
                # detail identical to the selected complete bookmark snapshot.
                return packet(dict(id='701', title='Generated 701', userId='91',
                                   userName='Generated author', illustType=0,
                                   pageCount=1, xRestrict=0, isMasked=False,
                                   createDate='2026-10-29T00:00:00Z',
                                   tags={'tags': []}, urls={'small': self.thumbnail_url}))
            if parsed.path == '/ajax/illust/701/pages':
                return packet([{'urls': {'original': self.original_url,
                                         'regular': self.thumbnail_url}}])
        if method == 'GET' and request.full_url in (self.original_url, self.thumbnail_url):
            assert not request.has_header('Cookie')
            return self.png
        with self._lock:
            self._unexpected.append(event)
        raise AssertionError('unsupported generated-only request')


def _profile(sandbox):
    from pixivvault_nicegui.native_profile import NativeProfileStore
    marker = sandbox / '.s10-fixture.json'
    if sandbox.exists():
        if marker.read_bytes() != FIXTURE_MARKER:
            raise ValueError('foreign S10 fixture root')
    else:
        sandbox.mkdir(parents=True, exist_ok=False)
        marker.write_bytes(FIXTURE_MARKER)
    source = sandbox / 'generated-input'
    source.mkdir(exist_ok=True)
    cookie = source / 'cookie.txt'
    if cookie.exists():
        if cookie.read_bytes() != COOKIE_BYTES:
            raise ValueError('generated Cookie fixture changed')
    else:
        cookie.write_bytes(COOKIE_BYTES)
    parent = sandbox / 'selected-save-parent'
    parent.mkdir(exist_ok=True)
    store = NativeProfileStore(sandbox / 'profile')
    current = store.snapshot()
    if current.revision == 0:
        current = store.update(cookie_file=cookie, save_parent=parent,
                               expected_revision=0)
    if current.cookie_file != cookie or current.save_parent != parent or current.revision != 1:
        store.close()
        raise ValueError('generated profile references changed')
    return store, current, cookie


def serve(port, sandbox, nonce):
    if not 1024 <= port <= 65535 or port == 8089:
        raise ValueError('owned loopback port required')
    external_attempts = []
    audit_lock = Lock()

    def network_guard(event, args):
        denied = False
        if event == 'socket.connect':
            address = args[1]
            host = address[0] if isinstance(address, tuple) else None
            denied = host not in ('127.0.0.1', '::1', 'localhost')
        elif event in ('socket.getaddrinfo', 'socket.gethostbyname', 'socket.gethostbyaddr'):
            denied = args[0] not in ('127.0.0.1', '::1', 'localhost', None)
        if denied:
            # No remote hostname, Cookie or request body is included in evidence.
            with audit_lock:
                external_attempts.append(event)
            print(json.dumps({'external_server_attempt': event, 'blocked': True}), flush=True)
            raise RuntimeError('external fixture socket or DNS prohibited')

    sys.addaudithook(network_guard)
    from fastapi import Header, HTTPException
    from fastapi.responses import FileResponse
    from nicegui import app, ui
    from pixivvault_nicegui import bootstrap, pixiv_read_client
    from pixivvault_nicegui.fs_paths import native_io_path
    from pixivvault_nicegui.managed_downloads import _read_bounded
    from pixivvault_nicegui.native_profile import native_kwargs, provision_storage
    from pixivvault_nicegui.native_startup import NativeProfileBinding, native_entry_arguments
    from pixivvault_nicegui.services import get_services

    sandbox = owned_path(sandbox)
    store, selected, cookie = _profile(sandbox)
    profile_root = sandbox / 'profile'
    cookie_sha = sha256(cookie.read_bytes()).hexdigest()
    os.environ['PIXIVVAULT_PROFILE_ROOT'] = str(profile_root)
    config = bootstrap.parse_args(['--native', *native_entry_arguments([]), '--check'])
    assert config.bookmark_account_id is None and not config.enable_pixiv_actions
    values = native_kwargs(selected, profile_root)
    assert 'bookmark_account_id' not in values
    provision_storage(selected, profile_root)
    http = GeneratedHTTP()
    readers = []
    original_type = pixiv_read_client.LivePixivReadClient

    class GeneratedReader(original_type):
        def __init__(self, path):
            assert Path(path) == cookie
            super().__init__(path, transport=http)
            readers.append(self)

    binding = NativeProfileBinding(store)
    pixiv_read_client.LivePixivReadClient = GeneratedReader
    try:
        bootstrap.register_pages(**values, native_profile=binding,
                                 pixiv_actions_mode='preferences')
        services = get_services(app)
    except BaseException:
        binding.close()
        pixiv_read_client.LivePixivReadClient = original_type
        raise
    assert services.save_exports.account is None and services.pixiv_actions is None
    assert http.observations() == ([], [])  # Constructor only; startup is measured separately.

    def auth(value):
        if not secrets.compare_digest(value or '', nonce):
            raise HTTPException(403)

    def projection_rows():
        rows = []
        ex = services.save_exports
        original_paths = [native_io_path(services.managed_downloads.root / relative).parent / page['name']
                          for relative, receipt, _ in services.managed_downloads.saved_receipts()
                          for page in receipt['files']]
        for job in ex.jobs.snapshot():
            if job.kind != 'bookmark-projection' or job.state != 'done':
                continue
            raw = _read_bounded(native_io_path(ex.root / job.result), 2_000_000)
            manifest = json.loads(raw)
            files = []
            for number, entry in enumerate(manifest['files']):
                target = native_io_path(ex.projection.root / entry['path'])
                ex._owned_bytes(target, entry['sha256'])
                data = target.read_bytes()
                assert len(data) == entry['bytes'] and sha256(data).hexdigest() == entry['sha256']
                files.append(dict(path=entry['path'].replace('\\', '/'),
                                  sha256=entry['sha256'], bytes=len(data),
                                  cas_samefile=any(os.path.samefile(target, old) for old in original_paths),
                                  url=f'/_p3_test/projection/{job.job_id}/{number}'))
            rows.append(dict(job_id=job.job_id, source=manifest['source'], files=files,
                             manifest_sha256=sha256(raw).hexdigest(),
                             intent_sha256=sha256(_read_bounded(native_io_path(
                                 ex.root / (job.target_id + '.json')), 2_000_000)).hexdigest()))
        return rows

    def original_proof_map():
        original_proofs = {}
        writer = services.managed_downloads
        for folder in (writer.outputs, writer.generation_outputs):
            physical = native_io_path(folder)
            for path in physical.rglob('*'):
                if path.is_file():
                    original_proofs[folder.name + '/' + path.relative_to(physical).as_posix()] = sha256(path.read_bytes()).hexdigest()
        return original_proofs

    def capture():
        original_proofs = original_proof_map()
        prefs = services.preferences.snapshot()
        calls, unexpected = http.observations()
        task = services._startup_discovery_task
        return dict(account=services.save_exports.account,
                    cached_identity=services.live_client._verified_identity,
                    startup_settled=task is not None and task.done(),
                    discovery_status=services.discovery.initial_load_status,
                    calls=calls, unexpected=unexpected,
                    preferences=dict(revision=prefs.revision, values=dict(prefs.values)),
                    static_jobs=[asdict(job) for job in services.static_jobs.snapshot()],
                    export_jobs=[asdict(job) for job in services.save_exports.jobs.snapshot()],
                    original_proofs=original_proofs, projections=projection_rows(),
                    cookie_sha256=sha256(cookie.read_bytes()).hexdigest(),
                    expected_image_sha256=sha256(http.png).hexdigest(),
                    profile_revision=store.snapshot().revision,
                    external_server_attempts=list(external_attempts),
                    pixiv_actions=services.pixiv_actions is not None)

    @app.get('/_p3_test/snapshot')
    async def snapshot(x_acceptance: str | None = Header(default=None)):
        auth(x_acceptance)
        return await asyncio.to_thread(capture)

    @app.get('/_p3_test/projection/{job_id}/{number}')
    def projection(job_id: str, number: int, x_acceptance: str | None = Header(default=None)):
        auth(x_acceptance)
        choices = [row for row in projection_rows() if row['job_id'] == job_id]
        if len(choices) != 1 or not 0 <= number < len(choices[0]['files']):
            raise HTTPException(404)
        entry = choices[0]['files'][number]
        target = native_io_path(services.save_exports.projection.root / entry['path'])
        services.save_exports._owned_bytes(target, entry['sha256'])
        return FileResponse(target, media_type='image/png', headers={'Cache-Control': 'no-store'})

    shutdown_originals = None

    @app.post('/_p3_test/shutdown')
    def shutdown(x_acceptance: str | None = Header(default=None)):
        nonlocal shutdown_originals
        auth(x_acceptance)
        shutdown_originals = original_proof_map()
        app.shutdown()
        return {'stopping': True}

    try:
        ui.run(host='127.0.0.1', port=port, show=False, reload=False,
               binding_refresh_interval=None, timeout_graceful_shutdown=10)
    finally:
        # Keep future explicit profile-auth readers inside the same generated
        # transport for this entire isolated server process, then restore it.
        pixiv_read_client.LivePixivReadClient = original_type
        proof = dict(services_closed=services.closed, readers_closed=all(r._closed for r in readers),
                     reader_count=len(readers),
                     export_stopped=services.save_exports.stop.is_set(),
                     cookie_unchanged=sha256(cookie.read_bytes()).hexdigest() == cookie_sha,
                     original_unchanged=shutdown_originals is not None
                         and original_proof_map() == shutdown_originals,
                     reader_shutdown_unverified=any(r.shutdown_unverified for r in readers),
                     reader_active_count=sum(getattr(r._owned_transport, 'active_count', 0) for r in readers),
                     external_server_attempts=list(external_attempts))
        name = 'close-' + sha256(nonce.encode()).hexdigest()[:16] + '.json'
        with (sandbox / name).open('x', encoding='utf-8') as file:
            json.dump(proof, file, ensure_ascii=False, indent=2)
        assert _closed_proof(proof), 'owned normal lifecycle did not complete'


def inspect_layout(page, url, headers, output, *, restart=False, previous=None):
    from playwright.sync_api import expect
    from pixivvault_nicegui.ui.preferences_view import CHOICE_LABELS
    expect.set_options(timeout=20000)
    page.context.set_extra_http_headers(headers)
    phases = {}

    def phase(name):
        (output / 's10-phase-progress.json').write_text(json.dumps(
            dict(active_phase=name, completed_phases=phases),
            ensure_ascii=False, indent=2), encoding='utf-8')

    def snapshot():
        response = page.request.get(url + '/_p3_test/snapshot', headers=headers)
        assert response.ok
        value = response.json()
        assert not value['unexpected'] and not value['pixiv_actions']
        assert value['external_server_attempts'] == []
        assert value['profile_revision'] == 1
        return value

    def wait(predicate, timeout=45):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = snapshot()
            if predicate(value):
                return value
            page.wait_for_timeout(100)
        raise AssertionError('S10 condition not observed: ' + json.dumps(snapshot(), ensure_ascii=False))

    def tab(name):
        page.get_by_test_id('tab-' + name).click()
        expect(page.get_by_test_id('tab-' + name)).to_have_attribute('aria-current', 'page')

    def choose(key, value):
        control = page.get_by_test_id('preference-' + key)
        expect(control).to_be_enabled()
        if control.get_attribute('data-value') != value:
            control.click()
            options = page.get_by_role('listbox').filter(visible=True)
            expect(options).to_have_count(1)
            label = CHOICE_LABELS.get(value, {'0': 'OFF', '1': 'ON'}.get(value, value))
            options.get_by_role('option', name=label, exact=True).click()
            expect(control).to_have_attribute('data-value', value)

    def settings(mode, folders, *, save=True, screenshot):
        before = snapshot()
        page.get_by_test_id('preferences-open').click()
        dialog = page.get_by_test_id('preferences-dialog')
        expect(dialog).to_be_visible()
        hint = page.get_by_test_id('preference-bookmark-account-gate')
        expect(hint).to_contain_text('再読込')
        expect(hint).to_contain_text('通信')
        if save:
            choose('bookmark_download_mode', mode)
            choose('use_work_folder', folders)
            page.get_by_test_id('preferences-save').click()
            after = wait(lambda value: value['preferences']['values']['bookmark_download_mode'] == mode
                         and value['preferences']['values']['use_work_folder'] == folders)
        else:
            expect(page.get_by_test_id('preference-bookmark_download_mode')).to_have_attribute('data-value', mode)
            expect(page.get_by_test_id('preference-use_work_folder')).to_have_attribute('data-value', folders)
            after = snapshot()
        assert after['calls'] == before['calls'], 'settings added a generated GET'
        page.get_by_test_id('preference-bookmark_download_mode').scroll_into_view_if_needed()
        page.screenshot(path=str(output / screenshot), animations='disabled')
        dialog.get_by_role('button', name='閉じる', exact=True).click()
        expect(dialog).to_be_hidden()
        return before, after

    def exported(value, mode):
        return next((row for row in value['projections'] if row['source']['mode'] == mode), None)

    def image_matches(row, expected):
        assert len(row['files']) == 1
        entry = row['files'][0]
        assert not entry['cas_samefile'] and entry['sha256'] == expected
        response = page.request.get(url + entry['url'], headers=headers)
        assert response.ok and sha256(response.body()).hexdigest() == expected

    def stable_jobs(value):
        return {key: [(job['job_id'], job['kind'], job['target_id'], job['state'], job['attempt'])
                      for job in value[key]] for key in ('static_jobs', 'export_jobs')}

    phase('startup')
    page.goto(url, wait_until='domcontentloaded')
    expect(page.get_by_test_id('preferences-open')).to_be_visible()
    initial = wait(lambda value: value['startup_settled'])
    phases['startup'] = dict(calls=initial['calls'], discovery_status=initial['discovery_status'])
    assert initial['account'] is None and initial['cached_identity'] == '55'
    assert not any(call['group'] in ('bookmarks', 'pages', 'original') for call in initial['calls'])
    cookie_sha = initial['cookie_sha256']

    if not restart:
        assert not initial['static_jobs'] and not initial['export_jobs']
        assert not initial['original_proofs'] and not initial['projections']
        phase('settings_direct')
        before, after = settings('direct', '0', screenshot='01-native-profile-settings.png')
        assert after['account'] is None
        phases['settings_direct'] = dict(additional_gets=len(after['calls']) - len(before['calls']), account=None)
        tab('library')
        # Entering My Bookmarks already starts its first complete read. Capture
        # account=None before that explicit navigation, not while its GETs run.
        before_read = snapshot()
        assert before_read['account'] is None
        phase('explicit_bookmark_refresh')
        page.get_by_test_id('library-subview-bookmarks').click()
        expect(page.get_by_test_id('bookmark-view')).to_be_visible()
        refresh_button = page.get_by_test_id('bookmark-refresh')
        refresh_button.click()
        # The click waits for the initial on-entry load and then starts this
        # explicit refresh. account may already be bound by the first load;
        # use the real UI lifecycle to await this second operation as well.
        refresh_status = page.get_by_test_id('bookmark-status')
        expect(refresh_status).to_have_text('ブックマークを読み込み中です')
        expect(refresh_button).to_be_visible()
        expect(refresh_status).to_be_hidden()
        refreshed = wait(lambda value: value['account'] == '55')
        calls = refreshed['calls'][len(before_read['calls']):]
        routes = {(call['path'].rsplit('/', 2)[-2], call['query']['rest'][0])
                  for call in calls if call['group'] == 'bookmarks'}
        assert routes == {('illusts', 'show'), ('illusts', 'hide'), ('novels', 'show'), ('novels', 'hide')}
        phases['explicit_bookmark_refresh'] = dict(calls=calls, account='55')
        card = page.get_by_test_id('bookmark-card').filter(has=page.locator('[data-testid=bookmark-select]'))
        expect(card).to_have_count(1)
        expect(card).to_have_attribute('data-work-id', '701')
        page.wait_for_function("""() => {
            const card=document.querySelector('[data-testid=bookmark-card][data-work-id="701"]');
            const image=card?.querySelector('img[src]');
            return image && image.complete && image.naturalWidth>0;
        }""")
        page.screenshot(path=str(output / '02-explicit-bookmark.png'), animations='disabled')
        card.get_by_test_id('bookmark-select').click()
        button = page.get_by_test_id('bookmark-download')
        expect(button).to_be_enabled()
        before_save = snapshot()
        phase('static_save_direct')
        button.click()
        # Acceptance and finished output are different states. Wait for the
        # actual handler's finally/status before leaving this active tab.
        expect(page.get_by_test_id('bookmark-download-status')).to_have_text(
            '保存キュー受付 1 件。保存完了・失敗はキューで確認してください。')
        expect(button).to_be_enabled()
        direct_state = wait(lambda value: any(job['target_id'] == '701' and job['state'] == 'done'
                                             for job in value['static_jobs']) and exported(value, 'direct'))
        direct = exported(direct_state, 'direct')
        assert direct['source']['account'] == '55'
        pieces = direct['files'][0]['path'].split('/')
        assert pieces[0] == '☆ブックマーク(55)' and len(pieces) == 2
        image_matches(direct, direct_state['expected_image_sha256'])
        phases['static_save'] = dict(calls=direct_state['calls'][len(before_save['calls']):],
                                    projection=direct)
        assert sum(call['group'] == 'original' for call in direct_state['calls']) == 1
        tab('queue')
        static_job, = direct_state['static_jobs']
        for job_id in (static_job['job_id'], direct['job_id']):
            row = visible_job(page, 'queue', job_id, expect)
            expect(row).to_have_attribute('data-state', 'done')
            expect(row.get_by_test_id('job-state')).to_have_text('完了')
        page.screenshot(path=str(output / '03-direct-completed.png'), animations='disabled')
        original_proofs = direct_state['original_proofs']
        phase('settings_link')
        before, after = settings('link', '1', screenshot='04-link-settings.png')
        phases['settings_link'] = dict(additional_gets=len(after['calls']) - len(before['calls']))
        phase('link_projection')
        linked_state = wait(lambda value: exported(value, 'link') is not None)
        linked = exported(linked_state, 'link')
        pieces = linked['files'][0]['path'].split('/')
        assert pieces[:2] == ['☆ブックマーク(55)', 'Generated author(91)']
        assert len(pieces) == 4 and '(701)' in pieces[2]
        assert linked['source']['account'] == '55' and linked['source']['use_work_folder'] is True
        image_matches(linked, linked_state['expected_image_sha256'])
        assert linked_state['original_proofs'] == original_proofs
        assert exported(linked_state, 'direct') == direct
        assert linked_state['calls'] == before['calls']
        assert linked_state['static_jobs'] == direct_state['static_jobs']
        row = visible_job(page, 'queue', linked['job_id'], expect)
        expect(row).to_have_attribute('data-state', 'done')
        page.screenshot(path=str(output / '05-link-completed.png'), animations='disabled')
        phases['link_projection'] = dict(projection=linked, original_proofs_unchanged=True)
    else:
        phase('restart_retention')
        assert previous is not None
        assert initial['cookie_sha256'] == previous['cookie_sha256']
        assert stable_jobs(initial) == stable_jobs(previous)
        assert initial['original_proofs'] == previous['original_proofs']
        assert initial['projections'] == previous['projections']
        assert initial['preferences'] == previous['preferences']
        before, after = settings('link', '1', save=False, screenshot='01-restart-settings.png')
        assert after['account'] is None
        assert stable_jobs(after) == stable_jobs(previous)
        tab('queue')
        linked = exported(after, 'link')
        row = visible_job(page, 'queue', linked['job_id'], expect)
        expect(row).to_have_attribute('data-state', 'done')
        page.screenshot(path=str(output / '02-restart-completed.png'), animations='disabled')
        phases['restart'] = dict(bookmark_gets=0, automatic_downloads=0,
                                 account=None, accepted_jobs_preserved=True)

    phase('final_projection_file')
    final = snapshot()
    assert final['cookie_sha256'] == cookie_sha
    if restart:
        assert final['account'] is None
        assert not any(call['group'] in ('bookmarks', 'pages', 'original') for call in final['calls'])
        assert stable_jobs(final) == stable_jobs(previous)
    linked = exported(final, 'link')
    image_matches(linked, final['expected_image_sha256'])
    page.goto(url + linked['files'][0]['url'], wait_until='domcontentloaded')
    page.wait_for_function('document.images.length===1 && document.images[0].complete && document.images[0].naturalWidth===48')
    page.screenshot(path=str(output / 'projection-file-in-browser.png'), animations='disabled')
    final['phases'] = phases
    final['scope'] = 'generated normal-profile S10 browser acceptance; discovery startup is recorded separately'
    (output / 's10-phases.json').write_text(json.dumps(phases, ensure_ascii=False, indent=2), encoding='utf-8')
    phase('complete')
    return final


def inspect_cache_units(page, url, headers, output, *, restart=False, previous=None):
    """S16 only: real settings events, local persistence, and no added GET."""
    from playwright.sync_api import expect
    from pixivvault_nicegui.ui.preferences_view import CHOICE_LABELS
    expect.set_options(timeout=10000)
    page.context.set_extra_http_headers(headers)
    phases = {}
    keys = ('discovery_cache_limit_mode', 'discovery_cache_size_mb',
            'discovery_cache_size_count')
    wanted = dict(zip(keys, ('count', '2048', '200')))

    def phase(name):
        (output / 's16-phase-progress.json').write_text(json.dumps(
            dict(active_phase=name, completed_phases=phases),
            ensure_ascii=False, indent=2), encoding='utf-8')

    def snapshot():
        response = page.request.get(url + '/_p3_test/snapshot', headers=headers)
        assert response.ok
        value = response.json()
        assert not value['unexpected'] and not value['pixiv_actions']
        assert value['external_server_attempts'] == [] and value['profile_revision'] == 1
        return value

    def wait(predicate):
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            value = snapshot()
            if predicate(value):
                return value
            page.wait_for_timeout(100)
        raise AssertionError('S16 condition not observed')

    def choose(key, value):
        control = page.get_by_test_id('preference-' + key)
        expect(control).to_be_visible()
        expect(control).to_be_enabled()
        if control.get_attribute('data-value') != value:
            control.click()
            options = page.get_by_role('listbox').filter(visible=True)
            expect(options).to_have_count(1)
            options.get_by_role('option', name=CHOICE_LABELS.get(value, value), exact=True).click()
            expect(control).to_have_attribute('data-value', value)

    mode = page.get_by_test_id('preference-' + keys[0])
    mb = page.get_by_test_id('preference-' + keys[1])
    count = page.get_by_test_id('preference-' + keys[2])
    dialog = page.get_by_test_id('preferences-dialog')

    def visible_unit(unit, mb_value, count_value):
        expect(mode).to_have_attribute('data-value', unit)
        expect(mb).to_have_attribute('data-value', mb_value)
        expect(count).to_have_attribute('data-value', count_value)
        expect(mb if unit == 'bytes' else count).to_be_visible()
        expect(count if unit == 'bytes' else mb).to_be_hidden()
        expect(mode).to_be_enabled()
        return {'mode': unit, 'mb': mb.get_attribute('data-value'),
                'count': count.get_attribute('data-value'),
                'mb_visible': mb.is_visible(), 'count_visible': count.is_visible()}

    phase('startup')
    page.goto(url, wait_until='domcontentloaded')
    expect(page.get_by_test_id('preferences-open')).to_be_visible()
    initial = wait(lambda value: value['startup_settled'])
    assert initial['account'] is None
    assert initial['static_jobs'] == initial['export_jobs'] == initial['projections'] == []
    assert initial['original_proofs'] == {}
    phases['startup'] = {'calls': initial['calls'], 'discovery_status': initial['discovery_status']}
    phase('cache-form')
    page.get_by_test_id('preferences-open').click()
    expect(dialog).to_be_visible()
    if restart:
        assert previous is not None
        assert initial['preferences'] == previous['saved_preferences']
        phases['restart'] = visible_unit('count', '2048', '200')
        choose(keys[0], 'bytes')
        phases['retained_inactive_value'] = visible_unit('bytes', '2048', '200')
        mode.scroll_into_view_if_needed()
        page.screenshot(path=str(output / 's16-restart-bytes.png'), animations='disabled')
        choose(keys[0], 'count')
    else:
        phases['default'] = visible_unit('bytes', '512', '100')
        choose(keys[1], '2048')
        choose(keys[0], 'count')
        choose(keys[2], '200')
        phases['count_draft'] = visible_unit('count', '2048', '200')
        choose(keys[0], 'bytes')
        phases['bytes_draft'] = visible_unit('bytes', '2048', '200')
        mode.scroll_into_view_if_needed()
        page.screenshot(path=str(output / 's16-bytes.png'), animations='disabled')
        choose(keys[0], 'count')
        draft = snapshot()
        assert draft['preferences'] == initial['preferences']
        assert draft['calls'] == initial['calls']
        page.get_by_test_id('preferences-save').click()
        saved = wait(lambda value: all(value['preferences']['values'][key] == val
                                      for key, val in wanted.items()))
        assert saved['preferences']['revision'] > initial['preferences']['revision']
        expect(page.get_by_test_id('preferences-status')).to_have_text('設定を保存しました。')
        # Unsaved edits are discarded by the real reload action.
        choose(keys[0], 'bytes')
        choose(keys[1], '1024')
        dialog.get_by_role('button', name='変更を破棄して再読込', exact=True).click()
        phases['reload'] = visible_unit('count', '2048', '200')
    phases['final_form'] = visible_unit('count', '2048', '200')
    mode.scroll_into_view_if_needed()
    page.screenshot(path=str(output / 's16-count.png'), animations='disabled')
    dialog.get_by_role('button', name='閉じる', exact=True).click()
    expect(dialog).to_be_hidden()
    page.get_by_test_id('preferences-open').click()
    expect(dialog).to_be_visible()
    phases['reopen'] = visible_unit('count', '2048', '200')
    dialog.get_by_role('button', name='閉じる', exact=True).click()
    expect(dialog).to_be_hidden()
    final = snapshot()
    assert final['calls'] == initial['calls']
    assert final['cookie_sha256'] == initial['cookie_sha256']
    assert final['static_jobs'] == final['export_jobs'] == final['projections'] == []
    assert final['original_proofs'] == initial['original_proofs'] == {}
    assert all(final['preferences']['values'][key] == val for key, val in wanted.items())
    phases['complete'] = {'settings_added_gets': 0, 'jobs_created': 0,
                          'originals_unchanged': True, 'cookie_unchanged': True}
    phase('complete')
    result = {'success': True, 'restart': restart, 'phases': phases,
              'saved_preferences': final['preferences'], 'settings_added_gets': 0,
              'scope': 'S16 unit visibility and values in ordinary generated profile; cache eviction is separate'}
    with (output / 's16-inspection.json').open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    return result



def _closed_proof(proof):
    flags = ('services_closed', 'readers_closed', 'export_stopped',
             'cookie_unchanged', 'original_unchanged')
    return (all(proof.get(key) is True for key in flags)
            and type(proof.get('reader_count')) is int and proof['reader_count'] > 0
            and proof.get('reader_shutdown_unverified') is False
            and proof.get('reader_active_count') == 0
            and proof.get('external_server_attempts') == [])


def run(output, *, channel, headed=False, cache_units=False):
    """One owned browser/page/port; a failed phase ends the entire acceptance."""
    import psutil
    from playwright.sync_api import sync_playwright

    output = owned_path(output)
    if output.exists():
        raise ValueError('do not overwrite an earlier browser attempt')
    output.mkdir(parents=True, exist_ok=False)
    temp = output / 'temp'
    temp.mkdir()
    before = source_hashes()
    tool_paths = (Path(__file__).resolve(),
                  Path(__file__).with_name('check_p3_save_exports_browser.py'),
                  Path(__file__).with_name('check_p3_residuals_browser.py'),
                  Path(__file__).with_name('p3_residuals_fixtures.py'),
                  Path(__file__).with_name('run_browser_checks.py'))
    tool_before = {path.name: sha256(path.read_bytes()).hexdigest() for path in tool_paths}
    port = free_port()  # Reserved once, then reused after each verified normal exit.
    if port == 8089:
        raise ValueError('reserved native port rejected')
    url = f'http://127.0.0.1:{port}'
    summary = dict(success=False, channel=channel, headed=headed, python=sys.version,
                   source_before=before, tools_before=tool_before, runs=[],
                   synthetic_only=True, real_pixiv_gets=0, real_pixiv_posts=0,
                   browser_instances=1, page_objects=1, port=port,
                   forced_cleanup=0,
                   scope=('S16 cache limit unit settings' if cache_units else 'S10 normal-profile browser acceptance')
                         + '; ordinary discovery startup recorded separately')
    current = None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=channel, headless=not headed)
            summary['browser_version'] = browser.version
            context = browser.new_context(viewport={'width': 1280, 'height': 720}, device_scale_factor=1)
            page = context.new_page()

            def guard(route):
                parsed = urlsplit(route.request.url)
                if (parsed.scheme, parsed.netloc) != ('http', f'127.0.0.1:{port}'):
                    if current is not None:
                        current['external_requests'].append(parsed.scheme + '://' + parsed.netloc)
                    route.abort()
                else:
                    route.continue_()

            context.route('**/*', guard)
            page.on('pageerror', lambda error: current['page_errors'].append(str(error))
                    if current is not None else None)
            page.on('console', lambda message: current['console_errors'].append(message.text)
                    if current is not None and message.type == 'error' else None)
            page.on('websocket', lambda socket: current['external_sockets'].append(
                urlsplit(socket.url).scheme + '://' + urlsplit(socket.url).netloc)
                if current is not None and (urlsplit(socket.url).scheme, urlsplit(socket.url).netloc)
                != ('ws', f'127.0.0.1:{port}') else None)
            try:
                for width, height in ((1280, 720), (1920, 1080)):
                    page.set_viewport_size({'width': width, 'height': height})
                    fixture = output / f'data-{width}x{height}'
                    previous = None
                    for mode in ('initial', 'restart'):
                        destination = output / f'{width}x{height}' / mode
                        destination.mkdir(parents=True, exist_ok=False)
                        nonce = secrets.token_hex(24)
                        headers = {'X-Acceptance': nonce}
                        current = dict(success=False, mode=mode, viewport=[width, height], port=port,
                                       page_errors=[], console_errors=[], external_requests=[],
                                       external_sockets=[], forced_cleanup=0, remaining_children=[])
                        summary['runs'].append(current)
                        process = None
                        monitor = None
                        monitor_stop = Event()
                        child_lock = Lock()
                        observed_children = set()
                        monitor_errors = set()
                        context.clear_cookies()
                        context.set_extra_http_headers(headers)

                        def observe_children(record=current, seen=observed_children,
                                             lock=child_lock, errors=monitor_errors):
                            # No signal is sent to any process. Every observed
                            # descendant remains paired with its creation time.
                            with lock:
                                identities = {(record['owner']['pid'], record['owner']['created']), *seen}
                            for pid, created in identities:
                                try:
                                    observed = psutil.Process(pid)
                                    if observed.create_time() != created:
                                        continue
                                    rows = {(child.pid, child.create_time())
                                            for child in observed.children(recursive=True)}
                                    with lock:
                                        seen.update(rows)
                                except psutil.NoSuchProcess:
                                    continue
                                except Exception as error:
                                    with lock:
                                        errors.add(type(error).__name__)

                        def monitor_children(observe=observe_children, stop=monitor_stop):
                            observe()
                            while not stop.wait(0.1):
                                observe()

                        with (destination / 'server.log').open('xb') as log:
                            try:
                                assert port_released(port), 'previous owned server still uses the port'
                                assert (mode == 'restart') == fixture.exists(), 'fixture phase is inconsistent'
                                env = child_environment()
                                env['PYTHONDONTWRITEBYTECODE'] = '1'
                                env['TEMP'] = env['TMP'] = str(temp)
                                process = subprocess.Popen(
                                    [sys.executable, '-B', str(Path(__file__).resolve()), '--serve', str(port),
                                     '--sandbox', str(fixture), '--nonce', nonce],
                                    cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                                owner = psutil.Process(process.pid)
                                current['owner'] = {'pid': process.pid, 'created': owner.create_time()}
                                monitor = Thread(target=monitor_children, name='s10-owned-descendants', daemon=True)
                                monitor.start()
                                wait_for_health(url, process, expected_data='pixiv-readonly')
                                inspector = inspect_cache_units if cache_units else inspect_layout
                                current['inspection'] = inspector(
                                    page, url, headers, destination, restart=mode == 'restart', previous=previous)
                                assert not current['page_errors'], current['page_errors']
                                assert not current['console_errors'], current['console_errors']
                                assert not current['external_requests'], current['external_requests']
                                assert not current['external_sockets'], current['external_sockets']
                                current['success'] = True
                            except Exception as error:
                                current['error'] = f'{type(error).__name__}: {error}'
                                current['traceback'] = traceback.format_exc()
                                progress = destination / ('s16-phase-progress.json' if cache_units else 's10-phase-progress.json')
                                if progress.is_file():
                                    try:
                                        current['phase_progress'] = json.loads(progress.read_text(encoding='utf-8'))
                                    except Exception as phase_error:
                                        current['phase_progress_error'] = type(phase_error).__name__
                                try:
                                    page.screenshot(path=str(destination / 'failure.png'), animations='disabled')
                                except Exception:
                                    pass
                                try:
                                    response = page.request.get(url + '/_p3_test/snapshot', headers=headers, timeout=3000)
                                    if response.ok:
                                        (destination / 'failure-state.json').write_text(
                                            json.dumps(response.json(), ensure_ascii=False, indent=2), encoding='utf-8')
                                except Exception:
                                    pass
                            finally:
                                try:
                                    page.goto('about:blank', timeout=5000)
                                except Exception:
                                    pass
                                if process is not None:
                                    if process.poll() is None:
                                        verified = False
                                        try:
                                            owner = psutil.Process(process.pid)
                                            verified = owner.create_time() == current.get('owner', {}).get('created')
                                        except psutil.NoSuchProcess:
                                            pass
                                        except Exception as error:
                                            current['shutdown_owner_error'] = type(error).__name__
                                        current['shutdown_owner_verified'] = verified
                                        if verified:
                                            try:
                                                request = urllib.request.Request(
                                                    url + '/_p3_test/shutdown', data=b'', headers=headers, method='POST')
                                                with opener.open(request, timeout=4) as response:
                                                    current['shutdown_requested'] = response.status == 200
                                            except Exception as error:
                                                current['shutdown_error'] = type(error).__name__
                                        try:
                                            process.wait(timeout=25)
                                        except subprocess.TimeoutExpired:
                                            current['normal_exit_timeout'] = True
                                    current['exit_code'] = process.poll()
                                if monitor is not None:
                                    observe_children()
                                    monitor_stop.set()
                                    monitor.join(timeout=2)
                                    current['descendant_monitor_stopped'] = not monitor.is_alive()
                                else:
                                    current['descendant_monitor_stopped'] = False
                                with child_lock:
                                    children = sorted(observed_children)
                                    current['descendant_monitor_errors'] = sorted(monitor_errors)
                                current['observed_children'] = [dict(pid=pid, created=created)
                                                                for pid, created in children]
                                deadline = time.monotonic() + 10
                                for pid, created in children:
                                    try:
                                        child = psutil.Process(pid)
                                        if child.create_time() != created:
                                            current.setdefault('reused_child_pids', []).append(pid)
                                            continue
                                        try:
                                            code = child.wait(timeout=max(0, deadline - time.monotonic()))
                                            current.setdefault('child_exit_codes', []).append(
                                                dict(pid=pid, created=created, exit_code=code))
                                            if code not in (None, 0):
                                                current['child_exit_failed'] = True
                                            continue
                                        except psutil.TimeoutExpired:
                                            pass
                                        if child.create_time() == created and child.is_running():
                                            current['remaining_children'].append(dict(pid=pid, created=created))
                                    except psutil.NoSuchProcess:
                                        current.setdefault('already_exited_children', []).append(
                                            dict(pid=pid, created=created))
                                    except Exception as error:
                                        current.setdefault('child_observation_errors', []).append(type(error).__name__)
                                current['port_released'] = port_released(port)
                                close_path = fixture / ('close-' + sha256(nonce.encode()).hexdigest()[:16] + '.json')
                                if close_path.is_file():
                                    try:
                                        current['shutdown_proof'] = json.loads(close_path.read_text(encoding='utf-8'))
                                    except Exception as error:
                                        current['shutdown_proof_error'] = type(error).__name__
                                current['success'] = bool(current['success']
                                    and current.get('exit_code') == 0
                                    and current.get('shutdown_owner_verified') is True
                                    and current.get('shutdown_requested') is True
                                    and current['port_released']
                                    and current['descendant_monitor_stopped']
                                    and not current['descendant_monitor_errors']
                                    and not current.get('child_exit_failed', False)
                                    and not current.get('child_observation_errors')
                                    and not current['remaining_children']
                                    and not current['page_errors'] and not current['console_errors']
                                    and not current['external_requests'] and not current['external_sockets']
                                    and current['forced_cleanup'] == 0
                                    and _closed_proof(current.get('shutdown_proof', {})))
                                (destination / 'result.json').write_text(
                                    json.dumps(current, ensure_ascii=False, indent=2), encoding='utf-8')
                        if not current['success']:
                            # Do not advance to restart/another viewport and do
                            # not terminate a server whose normal exit failed.
                            raise AssertionError(current.get('error', 'owned server did not exit cleanly'))
                        previous = current['inspection']
            finally:
                current = None
                context.close()
                browser.close()
        summary['success'] = len(summary['runs']) == 4 and all(row['success'] for row in summary['runs'])
    except Exception as error:
        summary['error'] = f'{type(error).__name__}: {error}'
    finally:
        after = source_hashes()
        tool_after = {path.name: sha256(path.read_bytes()).hexdigest() for path in tool_paths}
        summary['source_after'] = after
        summary['tools_after'] = tool_after
        summary['source_changed'] = before != after or tool_before != tool_after
        summary['success'] = bool(summary['success'] and not summary['source_changed'])
        (output / 'result.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        (output / 's10-checker-manifest.json').write_text(json.dumps(
            dict(success=summary['success'], tool_before=tool_before, tool_after=tool_after,
                 tools_changed=tool_before != tool_after, expected_runs=4,
                 closures=[row.get('shutdown_proof') for row in summary['runs']],
                 acceptance='browser; generated GET only; no forced cleanup'),
            ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(success=summary['success'], runs=len(summary['runs']),
                          source_changed=summary['source_changed'], forced_cleanup=0,
                          output=str(output)), ensure_ascii=False), flush=True)
    return 0 if summary['success'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--channel', choices=('msedge', 'chromium'), default='msedge')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--serve', type=int)
    parser.add_argument('--sandbox', type=Path)
    parser.add_argument('--nonce')
    parser.add_argument('--headed', action='store_true')
    parser.add_argument('--cache-units', action='store_true', help='Run only S16 cache-unit settings acceptance')
    args = parser.parse_args()
    if args.serve:
        if not args.sandbox or not args.sandbox.is_absolute() or not args.nonce:
            parser.error('explicit owned fixture path and nonce required')
        serve(args.serve, args.sandbox, args.nonce)
        return 0
    if args.output is None:
        parser.error('--output required')
    return run(args.output, channel=args.channel, headed=args.headed, cache_units=args.cache_units)


if __name__ == '__main__':
    raise SystemExit(main())
