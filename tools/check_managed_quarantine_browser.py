"""X07/X08 and S03 through the real product shell, generated data only.

One Edge process and one loopback port are reused across viewports/restarts.
No Cookie, real Pixiv transport, original repository, or native window is used.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from types import SimpleNamespace
from urllib.parse import urlsplit
import urllib.request

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.run_browser_checks import child_environment, free_port, port_released, source_hashes, stop_child, wait_for_health

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / 'artifacts/p3-318-x07-quarantine'
PREFIX = 'https://i.pximg.net/x07-generated/'


def owned_path(path):
    from pixivvault_nicegui.managed_downloads import _safe_path
    path = Path(path).absolute()
    if path == ARTIFACTS or not path.is_relative_to(ARTIFACTS):
        raise ValueError('fresh X07 candidate artifact subdirectory required')
    _safe_path(path)
    return path


def serve(port, root, nonce, *, focus='all'):
    from fastapi import Header, HTTPException
    from nicegui import app, ui
    from PIL import Image
    from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
    from pixivvault_nicegui.managed_downloads import ManagedPixivDownloads, _safe_path, _read_bounded
    from pixivvault_nicegui.media_routes import register_media_routes
    from pixivvault_nicegui.services import get_services
    from pixivvault_nicegui.ui.shell import build_demo_page
    from tools.p3_residuals_fixtures import FixtureReader

    if not 1024 <= port <= 65535 or port == 8089:
        raise ValueError('owned high loopback port required')
    external_attempts = []
    def network_guard(event, args):
        if event == 'socket.connect':
            address = args[1]
            host = address[0] if isinstance(address, tuple) else None
            if host not in ('127.0.0.1', '::1', 'localhost'):
                external_attempts.append('connect')
                raise RuntimeError('external fixture connection prohibited')
        elif event == 'socket.getaddrinfo' and args[0] not in ('127.0.0.1', '::1', 'localhost', None):
            external_attempts.append('dns')
            raise RuntimeError('external fixture DNS prohibited')
    sys.addaudithook(network_guard)
    root = owned_path(root)
    root.mkdir(parents=True, exist_ok=True)
    marker = root / '.x07-fixture.json'
    identity = {'purpose': 'generated-X07-X08-S03-product-acceptance', 'version': 1}
    if marker.exists():
        if json.loads(_read_bounded(marker, 4096)) != identity:
            raise ValueError('fixture ownership rejected')
    else:
        if any(root.iterdir()):
            raise ValueError('nonempty unmarked fixture rejected')
        with marker.open('x', encoding='utf-8') as stream:
            json.dump(identity, stream)

    flags = {'copy_failure': False, 'empty_following': False}
    class Reader(FixtureReader):
        def __init__(self):
            super().__init__()
            self._verified_identity = '99'
            self.original_gets = 0
            self.following_calls = []
        def require_identity(self):
            return '99'
        def verify_identity(self):
            return '99'
        def get_artwork_download_record(self, wid):
            if wid not in ('123', '124', '223', '224'):
                raise ValueError('generated work only')
            return RemoteBookmark(wid, 'illustration', 'show', 'Generated ' + wid,
                '24' if wid in ('123', '124') else '25', 'Generated author', (), '',
                '2026-10-29T00:00:00Z', 3 if wid == '123' else 1, 0, 0)
        def get_artwork_original_pages(self, wid):
            record = self.get_artwork_download_record(wid)
            return tuple(PREFIX + wid + '/' + str(page) + '.png' for page in range(record.page_count))
        def get_image_bytes(self, url, **kwargs):
            if not url.startswith(PREFIX):
                return super().get_image_bytes(url, **kwargs)
            parts = url[len(PREFIX):].split('/')
            if len(parts) != 2 or url not in self.get_artwork_original_pages(parts[0]):
                raise ValueError('generated original only')
            page = int(parts[1].split('.')[0])
            self.original_gets += 1
            out = BytesIO()
            Image.new('RGB', (160 + page * 20, 120), (30 + page * 20, 20, 10)).save(out, 'PNG')
            return out.getvalue()
        def get_following_users_detailed(self, account_id, rest_type='show'):
            if account_id != '99' or rest_type not in ('show', 'hide'):
                raise ValueError('generated following identity only')
            self.following_calls.append(rest_type)
            if flags['empty_following']:
                return []
            uid = '24' if rest_type == 'show' else '25'
            return [dict(user_id=uid, name='Generated ' + rest_type,
                         account='fixture' + uid, profile_img='')]

    reader = Reader()
    seed = ManagedPixivDownloads(root / 'managed', reader,
                                 resolve_record=reader.get_artwork_download_record)
    context = SimpleNamespace(checkpoint=lambda: None, progress=lambda *_: None)
    try:
        for wid in ('123', '124', '223', '224'):
            seed.run(SimpleNamespace(kind='pixiv-illust', target_id=wid), context)
    finally:
        seed.close()
    startup_gets = reader.original_gets
    reader.calls.clear()
    services = get_services(app, live_client=reader, pixiv_download_root=root / 'managed',
                            preferences_root=root / 'preferences', following_sync_root=root / 'following')
    if services._public_images is not None and services._public_images is not reader:
        services._public_images.close()
    services._public_images = reader
    writer = services.managed_downloads
    def proof_map():
        result = {}
        for folder in (writer.outputs, writer.generation_outputs):
            for path in sorted(folder.rglob('*')):
                if path.is_file():
                    _safe_path(path, file=True)
                    result[path.relative_to(writer.root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        return result
    baseline_path = root / 'original-proof.json'
    if baseline_path.exists():
        baseline = json.loads(_read_bounded(baseline_path, 1024 * 1024))
    else:
        baseline = proof_map()
        with baseline_path.open('x', encoding='utf-8') as stream:
            json.dump(baseline, stream, sort_keys=True)
    if baseline != proof_map():
        raise ValueError('generated original proof changed before startup')
    original_copy = writer.quarantine._copy_page
    def copy_page(item, destination):
        if flags['copy_failure'] and item.work_id == '223':
            raise OSError('generated copy failure')
        return original_copy(item, destination)
    writer.quarantine._copy_page = copy_page

    focused = None
    if focus == 'x09-v06':
        from tools.x09_v06_browser_focus import FocusedFixture
        focused = FocusedFixture(services, reader, flags)

    def auth(value):
        if not secrets.compare_digest(value or '', nonce):
            raise HTTPException(403)
    @app.get('/healthz')
    def health():
        return {'status': 'ok', 'mode': 'demo', 'data': 'synthetic'}
    @app.get('/_x07/state')
    def state(x_acceptance: str | None = Header(default=None)):
        auth(x_acceptance)
        # Page mapping and operation counts must describe the same revision;
        # the observer must not combine two individually valid generations.
        for _ in range(3):
            snapshot = writer.catalog_snapshot()
            generation = writer._generations.state_snapshot()
            if snapshot.revision == generation['revision']:
                break
        else:
            raise HTTPException(409, 'generated observation revision changed')
        following = services.following_sync.snapshot('99')
        result = dict(source_pages={wid: list(pages) for wid, pages in snapshot.source_pages.items()},
            complete_receipts=len(snapshot.receipts), original_proofs=proof_map(),
            original_unchanged=proof_map() == baseline,
            operation_count=sum(op['state'] == 'committed' for op in generation['operations'].values()),
            excluded_count=sum(len(pages) for pages in generation['exclusions'].values()),
            original_gets=reader.original_gets, startup_gets=startup_gets,
            library=[{'id': work.work_id, 'pages': work.page_count} for work in services.library_snapshot().works],
            following=asdict(following), following_calls=list(reader.following_calls),
            external_server_attempts=list(external_attempts))
        if focused is not None:
            extra = focused.state()
            if extra['revision'] != generation['revision']:
                raise HTTPException(409, 'generated observation revision changed')
            result['focus'] = extra
        return result
    @app.post('/_x07/inject')
    def inject(values: dict, x_acceptance: str | None = Header(default=None)):
        auth(x_acceptance)
        if not set(values) <= set(flags) or any(type(value) is not bool for value in values.values()):
            raise HTTPException(400)
        flags.update(values)
        if focused is not None:
            focused.on_inject(values)
        return {'ok': True}
    @app.post('/_x07/shutdown')
    def shutdown(x_acceptance: str | None = Header(default=None)):
        auth(x_acceptance)
        app.shutdown()
        return {'stopping': True}
    async def close():
        if focused is not None:
            focused.release()
        await asyncio.to_thread(services.close)
    app.on_shutdown(close)
    register_media_routes(app, services.images)
    ui.page('/')(build_demo_page)
    ui.run(host='127.0.0.1', port=port, show=False, reload=False,
           binding_refresh_interval=None, timeout_graceful_shutdown=10)


def inspect(page, url, headers, output, *, restart=False, previous=None):
    from playwright.sync_api import expect
    expect.set_options(timeout=20000)
    checks = []
    phase = 'open-product'
    observation_conflicts = 0
    def mark(value):
        nonlocal phase
        phase = value
        with (output / 'progress.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'phase': phase, 'time': time.time()}, ensure_ascii=False) + '\n')
    def state():
        nonlocal observation_conflicts
        for _ in range(5):
            response = page.request.get(url + '/_x07/state', headers=headers)
            if response.status == 409:
                observation_conflicts += 1
                page.wait_for_timeout(50)
                continue
            assert response.ok, f'{phase}: state HTTP {response.status}'
            return response.json()
        raise AssertionError(f'{phase}: observation revisions did not settle')
    def inject(**values):
        response = page.request.post(url + '/_x07/inject', headers=headers, data=values)
        assert response.ok
    def eventually(predicate, label):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            value = state()
            if predicate(value):
                return value
            page.wait_for_timeout(100)
        raise AssertionError(label)
    def tab(key):
        page.get_by_test_id('tab-' + key).click()
        expect(page.get_by_test_id('tab-' + key)).to_have_attribute('aria-current', 'page')
    def checked(wid, number):
        return page.get_by_test_id(f'duplicate-select-{wid}-{number}')
    def preferences():
        page.get_by_test_id('preferences-open').click()
        dialog = page.get_by_test_id('preferences-dialog')
        expect(dialog).to_be_visible()
        expect(page.get_by_test_id('following-sync-button')).to_be_enabled()
        return dialog
    def close_preferences(dialog):
        dialog.get_by_role('button', name='閉じる', exact=True).click()
        expect(dialog).not_to_be_visible()

    mark('open-product')
    page.goto(url, wait_until='domcontentloaded')
    expect(page.get_by_test_id('tab-dedup')).to_be_enabled()
    initial = state()
    assert initial['complete_receipts'] == 4 and initial['original_unchanged']
    assert initial['following_calls'] == [] and initial['external_server_attempts'] == []
    mark('scan-dedup')
    tab('dedup')
    expect(page.get_by_test_id('duplicate-mode-near')).to_be_disabled()
    page.get_by_test_id('duplicate-scan').click()
    expect(page.get_by_test_id('duplicate-mode-near')).to_be_enabled()
    page.get_by_test_id('duplicate-mode-near').click()
    if restart:
        mark('verify-restart')
        assert initial['startup_gets'] == initial['original_gets'] == 0
        for key in ('source_pages', 'operation_count', 'excluded_count', 'original_proofs', 'following'):
            assert initial[key] == previous[key], key
        expect(page.get_by_test_id('duplicate-mode-near')).to_contain_text('（1）')
        expect(checked('123', 2)).to_have_count(0)
        expect(checked('223', 1)).to_have_count(0)
        page.screenshot(path=str(output / 'quarantine-restart.png'), animations='disabled')
        mark('following-restart')
        dialog = preferences()
        expect(page.get_by_test_id('following-sync-summary')).to_have_text('同期済みの作者: 0人')
        expect(page.get_by_test_id('following-sync-row')).to_have_count(0)
        assert state()['following_calls'] == []
        page.screenshot(path=str(output / 'following-restart.png'), animations='disabled')
        close_preferences(dialog)
        checks.append('restart retains exclusions and explicit empty sync without original GET or following GET')
    else:
        expect(page.get_by_test_id('duplicate-mode-near')).to_contain_text('（2）')
        expect(page.get_by_test_id('duplicate-thumbnail')).to_have_count(6)
        thumbnails = page.get_by_test_id('duplicate-thumbnail')
        for index in range(6):
            thumbnail = thumbnails.nth(index)
            thumbnail.scroll_into_view_if_needed()
            page.wait_for_function("""node => {
                const image = node.querySelector('img');
                return !!image && image.complete && image.naturalWidth > 0;
            }""", arg=thumbnail.element_handle())
        mark('keeper-protection')
        checked('223', 1).click()
        checked('224', 1).click()
        expect(checked('223', 1)).to_be_checked()
        expect(checked('224', 1)).not_to_be_checked()
        expect(page.get_by_test_id('duplicate-status')).to_contain_text('1枚以上')
        checked('223', 1).click()
        mark('single-confirm-cancel')
        checked('123', 2).click()
        page.get_by_test_id('duplicate-group-move').first.click()
        dialog = page.get_by_test_id('duplicate-confirm-dialog')
        expect(dialog).to_be_visible()
        expect(page.get_by_test_id('duplicate-confirm-title')).to_contain_text('1枚')
        expect(dialog).to_contain_text('元の2ページ')
        page.screenshot(path=str(output / 'single-confirm.png'), animations='disabled')
        page.get_by_test_id('duplicate-confirm-cancel').click()
        assert state()['operation_count'] == 0 and state()['source_pages']['123'] == [0, 1, 2]
        mark('single-commit')
        page.get_by_test_id('duplicate-group-move').first.click()
        page.get_by_test_id('duplicate-confirm-execute').click()
        eventually(lambda s: s['source_pages']['123'] == [0, 2], 'single quarantine did not commit')
        expect(page.get_by_test_id('duplicate-status')).to_contain_text('1枚を隔離しました・失敗0枚')
        checks.append('per-group confirmation/cancel/commit and at least one keeper')

        mark('batch-partial-commit')
        inject(copy_failure=True)
        checked('123', 1).click()
        checked('223', 1).click()
        page.get_by_test_id('duplicate-move').click()
        expect(dialog).to_be_visible()
        expect(page.get_by_test_id('duplicate-confirm-title')).to_contain_text('2枚')
        expect(page.get_by_test_id('duplicate-confirm-title')).to_contain_text('2組')
        page.screenshot(path=str(output / 'batch-confirm.png'), animations='disabled')
        page.get_by_test_id('duplicate-confirm-execute').click()
        partial = eventually(lambda s: s['operation_count'] == 2, 'partial batch did not settle')
        assert partial['source_pages']['123'] == [2] and partial['source_pages']['223'] == [0]
        assert partial['excluded_count'] == 2
        expect(page.get_by_test_id('duplicate-status')).to_contain_text('1枚を隔離しました・失敗1枚')
        page.screenshot(path=str(output / 'batch-partial-result.png'), animations='disabled')
        mark('batch-failed-member-retry')
        inject(copy_failure=False)
        checked('223', 1).click()
        page.get_by_test_id('duplicate-move').click()
        page.get_by_test_id('duplicate-confirm-execute').click()
        eventually(lambda s: s['operation_count'] == 3 and s['source_pages']['223'] == [],
                   'failed member retry did not commit')
        expect(page.get_by_test_id('duplicate-status')).to_contain_text('1枚を隔離しました・失敗0枚')
        final_quarantine = state()
        assert final_quarantine['excluded_count'] == 3 and final_quarantine['original_unchanged']
        assert final_quarantine['original_gets'] == initial['original_gets']
        assert {row['id']: row['pages'] for row in final_quarantine['library']} == {'123': 1, '124': 1, '224': 1}
        mark('product-library-refresh')
        tab('library')
        expect(page.get_by_test_id('library-author-open')).to_have_count(2)
        tab('dedup')
        expect(page.get_by_test_id('duplicate-selection')).to_have_text('0枚選択中')
        checks.append('batch confirms all groups, reports partial success, retries only failed choice, refreshes product library')

        mark('following-public-private')
        dialog = preferences()
        expect(page.get_by_test_id('following-sync-summary')).to_have_text('まだ同期していません。')
        assert state()['following_calls'] == []
        page.get_by_test_id('following-sync-button').click()
        synced = eventually(lambda s: len([a for a in s['following']['authors'] if a['is_following']]) == 2,
                            'explicit public/private following sync did not commit')
        assert synced['following_calls'] == ['show', 'hide']
        expect(page.get_by_test_id('following-sync-summary')).to_have_text('同期済みの作者: 2人')
        expect(page.get_by_test_id('following-sync-row')).to_have_count(2)
        expect(page.get_by_test_id('following-sync-rows')).to_contain_text('公開')
        expect(page.get_by_test_id('following-sync-rows')).to_contain_text('非公開')
        page.screenshot(path=str(output / 'following-public-private.png'), animations='disabled')
        close_preferences(dialog)
        dialog = preferences()
        assert state()['following_calls'] == ['show', 'hide']
        mark('following-empty')
        inject(empty_following=True)
        page.get_by_test_id('following-sync-button').click()
        empty = eventually(lambda s: s['following']['revision'] > synced['following']['revision']
                            and not any(a['is_following'] for a in s['following']['authors']),
                            'explicit empty following result did not commit')
        assert empty['following_calls'] == ['show', 'hide', 'show', 'hide']
        assert len(empty['following']['authors']) == 2
        expect(page.get_by_test_id('following-sync-summary')).to_have_text('同期済みの作者: 0人')
        expect(page.get_by_test_id('following-sync-row')).to_have_count(0)
        page.screenshot(path=str(output / 'following-zero.png'), animations='disabled')
        close_preferences(dialog)
        checks.append('settings explicit following sync covers public/private and genuine zero; reopening does not GET')
    mark('final-proof')
    final = state()
    assert final['original_unchanged'] and final['original_gets'] == initial['original_gets']
    assert final['external_server_attempts'] == []
    return {'checks': checks, 'initial': initial, 'final': final,
            'observation_conflicts': observation_conflicts,
            'viewport': page.evaluate('({width:innerWidth,height:innerHeight,dpr:devicePixelRatio})')}


def run(output, *, channel, headed=False, focus='all'):
    import psutil
    from playwright.sync_api import sync_playwright
    output = owned_path(output)
    if output.exists():
        raise ValueError('do not overwrite an earlier browser attempt')
    output.mkdir(parents=True)
    before = source_hashes()
    tool_paths = (Path(__file__), Path(__file__).with_name('x09_v06_browser_focus.py'))
    tool_before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in tool_paths}
    phases = ('initial',) if focus == 'x09-v06' else ('initial', 'restart')
    port = free_port()
    if port == 8089:
        raise ValueError('reserved port rejected')
    url = f'http://127.0.0.1:{port}'
    nonce = secrets.token_hex(24)
    headers = {'X-Acceptance': nonce}
    summary = {'success': False, 'channel': channel, 'headed': headed, 'focus': focus,
               'source_before': before, 'runs': [],
               'tools_before': tool_before,
               'checker_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
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
            try:
                for width, height in ((1280, 720), (1920, 1080)):
                    previous = None
                    fixture = output / f'fixture-{width}x{height}'
                    page.set_viewport_size({'width': width, 'height': height})
                    for phase in phases:
                        destination = output / f'{width}x{height}' / phase
                        destination.mkdir(parents=True)
                        current = {'success': False, 'phase': phase, 'viewport': [width, height],
                                   'port': port, 'page_errors': [], 'console_errors': [],
                                   'external_requests': [], 'forced_cleanup': 0,
                                   'remaining_children': []}
                        summary['runs'].append(current)
                        process = None
                        observed_children = []
                        with (destination / 'server.log').open('xb') as log:
                            try:
                                env = child_environment()
                                env['PYTHONDONTWRITEBYTECODE'] = '1'
                                process = subprocess.Popen(
                                    [sys.executable, '-B', str(Path(__file__).resolve()), '--serve', str(port),
                                     '--root', str(fixture), '--nonce', nonce, '--focus', focus],
                                    cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                                owner = psutil.Process(process.pid)
                                current['owner'] = {'pid': process.pid, 'created': owner.create_time()}
                                wait_for_health(url, process)
                                if focus == 'x09-v06':
                                    from tools.x09_v06_browser_focus import inspect as inspect_focused
                                    inspected = inspect_focused(page, url, headers, destination)
                                else:
                                    inspected = inspect(page, url, headers, destination,
                                                        restart=phase == 'restart', previous=previous)
                                current['inspection'] = inspected
                                previous = inspected['final']
                                assert not current['page_errors'], current['page_errors']
                                assert not current['console_errors'], current['console_errors']
                                assert not current['external_requests'], current['external_requests']
                                current['success'] = True
                            except Exception as error:
                                current['error'] = f'{type(error).__name__}: {error}'
                                try:
                                    page.screenshot(path=str(destination / 'failure.png'), animations='disabled')
                                except Exception:
                                    pass
                            finally:
                                try:
                                    page.goto('about:blank')
                                except Exception:
                                    pass
                                if process is not None:
                                    if process.poll() is None:
                                        try:
                                            parent = psutil.Process(process.pid)
                                            observed_children = [(child.pid, child.create_time())
                                                                 for child in parent.children(recursive=True)]
                                        except psutil.NoSuchProcess:
                                            pass
                                        try:
                                            request = urllib.request.Request(url + '/_x07/shutdown', data=b'',
                                                                             headers=headers, method='POST')
                                            with opener.open(request, timeout=4) as response:
                                                current['shutdown_requested'] = response.status == 200
                                        except Exception as error:
                                            current['shutdown_error'] = type(error).__name__
                                        try:
                                            process.wait(timeout=25)
                                        except subprocess.TimeoutExpired:
                                            current['forced_cleanup'] += 1
                                            stop_child(process)
                                    current['exit_code'] = process.poll()
                                    # A venv launcher can finish just before its
                                    # interpreter child exits. Wait for each
                                    # observed owned child within one deadline;
                                    # only actual survivors require cleanup.
                                    child_deadline = time.monotonic() + 10
                                    for child_pid, created in observed_children:
                                        try:
                                            child = psutil.Process(child_pid)
                                            if child.create_time() != created:
                                                continue
                                            try:
                                                code = child.wait(timeout=max(0, child_deadline - time.monotonic()))
                                                current.setdefault('child_exit_codes', []).append(
                                                    {'pid': child_pid, 'created': created, 'exit_code': code})
                                                if code not in (None, 0):
                                                    current['child_exit_failed'] = True
                                                continue
                                            except psutil.TimeoutExpired:
                                                pass
                                            if child.create_time() != created or not child.is_running():
                                                continue
                                            current['remaining_children'].append({'pid': child_pid, 'created': created})
                                            current['forced_cleanup'] += 1
                                            child.terminate()
                                            try:
                                                child.wait(timeout=5)
                                            except psutil.TimeoutExpired:
                                                child.kill()
                                                child.wait(timeout=5)
                                        except psutil.NoSuchProcess:
                                            pass
                                current['port_released'] = port_released(port)
                                current['success'] = bool(current['success']
                                    and current.get('exit_code') == 0 and current['port_released']
                                    and not current.get('child_exit_failed', False)
                                    and current['forced_cleanup'] == 0 and not current['remaining_children'])
                                (destination / 'result.json').write_text(
                                    json.dumps(current, ensure_ascii=False, indent=2), encoding='utf-8')
                        if not current['success']:
                            raise AssertionError(current.get('error', 'owned server did not exit cleanly'))
            finally:
                current = None
                context.close()
                browser.close()
        summary['success'] = len(summary['runs']) == 2 * len(phases) and all(run['success'] for run in summary['runs'])
    except Exception as error:
        summary['error'] = f'{type(error).__name__}: {error}'
    finally:
        after = source_hashes()
        tool_after = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in tool_paths}
        summary['source_after'] = after
        summary['tools_after'] = tool_after
        summary['source_changed'] = before != after or tool_before != tool_after
        summary['success'] = summary['success'] and not summary['source_changed']
        (output / 'result.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'success': summary['success'], 'runs': len(summary['runs']),
                      'source_changed': summary['source_changed'], 'output': str(output)}, ensure_ascii=False))
    return 0 if summary['success'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serve', type=int)
    parser.add_argument('--root', type=Path)
    parser.add_argument('--nonce')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--channel', choices=('msedge', 'chromium'), default='msedge')
    parser.add_argument('--headed', action='store_true')
    parser.add_argument('--focus', choices=('all', 'x09-v06'), default='all')
    args = parser.parse_args()
    if args.serve is not None:
        if args.root is None or not args.nonce:
            parser.error('--serve requires --root and --nonce')
        serve(args.serve, args.root, args.nonce, focus=args.focus)
        return 0
    if args.output is None:
        parser.error('--output required')
    return run(args.output, channel=args.channel, headed=args.headed, focus=args.focus)


if __name__ == '__main__':
    raise SystemExit(main())
