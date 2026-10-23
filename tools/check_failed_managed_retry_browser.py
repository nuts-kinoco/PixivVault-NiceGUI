"""Real Edge/Chromium acceptance: static failed_queue -> managed durable queue.

Uses only a synthetic offline Pixiv double, temporary SQLite failure copy,
and isolated managed outputs. The browser is restricted to one loopback port.
No Cookie or original Flet files are opened or modified.
"""
from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
import json
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
from urllib.parse import urlsplit
from urllib.request import Request, ProxyHandler, build_opener

from PIL import Image

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.run_browser_checks import (child_environment, free_port, port_released,
                                      source_hashes, stop_child, wait_for_health)


def build_source(path):
    if path.exists():
        raise ValueError('new synthetic source path required')
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE failed_queue (work_id TEXT, user_id TEXT, title TEXT, '
                     'work_type TEXT, error_reason TEXT, failed_at TEXT, retry_count INTEGER)')
        conn.executemany('INSERT INTO failed_queue VALUES (?,?,?,?,?,?,?)', (
            ('301', '23', '合成イラスト', 'illust', 'Timeout', '2026-10-23 10:00:00', 1),
            ('302', '23', '合成漫画', 'manga', '404', '2026-10-23 09:00:00', 0),
            ('303', '23', '合成小説', 'novel', '403', '2026-10-23 08:00:00', 2),
            ('304', '23', '合成うごイラ', 'ugoira', 'Timeout', '2026-10-23 07:00:00', 0),
        ))


class FakePixiv:
    def __init__(self, *, no_remote=False):
        self.no_remote = no_remote
        self.requests = 0
        self.original_gets = 0

    def _request(self):
        if self.no_remote:
            raise AssertionError('a persisted local restart must not contact Pixiv')
        self.requests += 1

    def verify_identity(self):
        self._request()
        return '12'

    def get_artwork_download_record(self, work_id):
        self._request()
        from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
        if work_id not in ('301', '302'):
            raise AssertionError('unsupported format reached metadata GET')
        return RemoteBookmark(work_id, 'illustration', 'show', f'合成作品 {work_id}',
                              '23', '合成作者', ('合成',), '', '2026-10-23', 1,
                              1 if work_id == '302' else 0, 0)

    def get_artwork_original_pages(self, work_id):
        self._request()
        return (f'https://i.pximg.net/synthetic/{work_id}_p0.png',)

    def get_image_bytes(self, url):
        self._request()
        self.original_gets += 1
        out = BytesIO()
        Image.new('RGB', (120, 84), (13, 37, 211) if '/301_' in url
                  else (9, 191, 53)).save(out, 'PNG')
        return out.getvalue()

    def close(self):
        pass


def serve(port, root, source, nonce, *, restart):
    from nicegui import app, ui
    from fastapi import Header, HTTPException
    from pixivvault_nicegui.bootstrap import register_pages
    remote = FakePixiv(no_remote=restart)
    register_pages(live_client=remote, pixiv_download_root=root,
                   failed_queue_readonly_db=source)

    @app.get('/_failed_retry_test/status')
    def status(x_acceptance: str | None = Header(default=None)):
        if x_acceptance != nonce:
            raise HTTPException(403)
        return {'synthetic': True, 'remote_requests': remote.requests,
                'original_gets': remote.original_gets}

    @app.post('/_failed_retry_test/shutdown')
    def shutdown(x_acceptance: str | None = Header(default=None)):
        if x_acceptance != nonce:
            raise HTTPException(403)
        app.shutdown()
        return {'stopping': True}

    ui.run(host='127.0.0.1', port=port, show=False, reload=False,
           binding_refresh_interval=None)


def inspect(page, base, headers, root, source, *, restart):
    from playwright.sync_api import expect
    page.goto(base, wait_until='domcontentloaded')
    if not restart:
        page.get_by_test_id('tab-failed').click()
        legacy = page.get_by_test_id('failed-records-view')
        expect(legacy).to_be_visible()
        expect(legacy.get_by_test_id('failed-count')).to_have_text('4 / 4 件', timeout=20000)
        expect(legacy.get_by_test_id('failed-row-retry')).to_have_count(4)
        expect(legacy.get_by_test_id('failed-retry-all')).to_be_enabled()
        expect(legacy.locator('.pv-failure-row[data-work-id="303"]')
               .get_by_test_id('failed-row-retry')).to_be_disabled()
        expect(legacy.locator('.pv-failure-row[data-work-id="304"]')
               .get_by_test_id('failed-row-retry')).to_be_disabled()
        legacy.locator('.pv-failure-row[data-work-id="301"]').get_by_test_id('failed-row-retry').click()
        expect(legacy.get_by_test_id('failed-managed-retry-status')).to_contain_text(
            '1 件を管理キュー', timeout=20000)
        second = page.context.new_page()
        try:
            second.goto(base, wait_until='domcontentloaded')
            second.get_by_test_id('tab-failed').click()
            other = second.get_by_test_id('failed-records-view')
            expect(other.get_by_test_id('failed-count')).to_have_text('4 / 4 件', timeout=20000)
            expect(other.get_by_test_id('failed-managed-retry-status')).to_be_empty()
        finally:
            second.close()
        # A *second* explicit click after the first terminal job must create
        # new durable history, not return the old JobSnapshot as a false save.
        page.get_by_test_id('tab-queue').click()
        queue = page.get_by_test_id('queue-view')
        expect(queue.get_by_test_id('queue-filter-done')).to_contain_text('完了 1', timeout=30000)
        page.get_by_test_id('tab-failed').click()
        legacy = page.get_by_test_id('failed-records-view')
        legacy.locator('.pv-failure-row[data-work-id="301"]').get_by_test_id('failed-row-retry').click()
        page.get_by_test_id('tab-queue').click()
        queue = page.get_by_test_id('queue-view')
        expect(queue.get_by_test_id('queue-filter-done')).to_contain_text('完了 2', timeout=30000)
        page.get_by_test_id('tab-failed').click()
        legacy = page.get_by_test_id('failed-records-view')
        # Original filtered-all action: one matching manga, one accepted job.
        legacy.get_by_test_id('failed-search').fill('合成漫画')
        expect(legacy.get_by_test_id('failed-count')).to_have_text('1 / 4 件')
        legacy.get_by_test_id('failed-retry-all').click()
        expect(legacy.get_by_test_id('failed-managed-retry-status')).to_contain_text(
            '1 件を管理キュー', timeout=20000)
        # Source list intentionally remains intact after accepting managed jobs.
        legacy.get_by_test_id('failed-search').fill('')
        expect(legacy.get_by_test_id('failed-count')).to_have_text('4 / 4 件')

    page.get_by_test_id('tab-queue').click()
    queue = page.get_by_test_id('queue-view')
    expect(queue.get_by_test_id('queue-filter-done')).to_contain_text('完了 3', timeout=30000)
    page.get_by_test_id('tab-library').click()
    library = page.get_by_test_id('library-view')
    if not restart:
        library.get_by_test_id('library-db-refresh').click()
    author = library.locator('.pv-library-author-card[data-author-id="23"]')
    expect(author).to_be_visible(timeout=20000)
    author.get_by_test_id('library-author-open').click()
    for wid, pixel in (('301', [13, 37, 211]), ('302', [9, 191, 53])):
        card = library.locator(f'.pv-library-page-card[data-work-id="{wid}"]')
        expect(card).to_have_count(1)
        card.click()
        viewer = page.get_by_test_id('library-viewer')
        expect(viewer).to_be_visible()
        img = viewer.locator('img:visible')
        img.evaluate('image => image.decode()')
        actual = img.evaluate('''image => {
          const canvas=document.createElement('canvas');canvas.width=canvas.height=1;
          const context=canvas.getContext('2d');context.drawImage(image,0,0,1,1);
          return Array.from(context.getImageData(0,0,1,1).data).slice(0,3);
        }''')
        assert actual == pixel, 'incorrect managed page decoded'
        viewer.get_by_role('button', name='閉じる', exact=True).click()
        expect(card).to_be_focused()
    assert len(list((root / 'outputs').glob('work-*/receipt.json'))) == 2
    status = page.request.get(base + '/_failed_retry_test/status', headers=headers).json()
    assert status['original_gets'] == (0 if restart else 2)
    assert status['remote_requests'] == 0 if restart else status['remote_requests'] >= 8
    return {'restart': restart, 'jobs_done': 3, 'viewer_pages_verified': 2,
            'original_gets': status['original_gets']}


def run(channel, output):
    if output.exists():
        raise ValueError('new --output directory required')
    from playwright.sync_api import sync_playwright
    output.mkdir(parents=True)
    before = source_hashes()
    result = {'success': False, 'synthetic_only': True, 'channel': channel,
              'viewports': [], 'console_errors': [], 'page_errors': [],
              'foreign_requests': [], 'normal_exit': [], 'ports_released': []}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel=None if channel == 'chromium' else channel)
            try:
                for width, height in ((1280, 720), (1920, 1080)):
                    managed_root = (output / f'managed-{width}x{height}').resolve()
                    source = (output / f'failures-{width}x{height}.sqlite3').resolve()
                    build_source(source)
                    source_sha = hashlib.sha256(source.read_bytes()).digest()
                    for restart in (False, True):
                        port, nonce = free_port(), secrets.token_hex(16)
                        base, headers = f'http://127.0.0.1:{port}', {'X-Acceptance': nonce}
                        process, context = None, None
                        try:
                            with (output / f'server-{width}-{restart}.log').open('wb') as log:
                                process = subprocess.Popen([
                                    sys.executable, str(Path(__file__).resolve()), '--serve',
                                    '--port', str(port), '--managed-root', str(managed_root),
                                    '--source', str(source), '--nonce', nonce,
                                    '--restart' if restart else '--initial',
                                ], env=child_environment(), stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=log)
                            wait_for_health(base, process, expected_data='pixiv-readonly')
                            context = browser.new_context(viewport={'width': width, 'height': height})
                            def local_only(route):
                                parsed = urlsplit(route.request.url)
                                if (parsed.scheme, parsed.netloc) == ('http', f'127.0.0.1:{port}'):
                                    route.continue_()
                                else:
                                    result['foreign_requests'].append('blocked')
                                    route.abort()
                            context.route('**/*', local_only)
                            page = context.new_page()
                            page.on('console', lambda msg: result['console_errors'].append(msg.text[:250])
                                    if msg.type == 'error' else None)
                            page.on('pageerror', lambda exc: result['page_errors'].append(str(exc)[:250]))
                            result['viewports'].append({'viewport': [width, height],
                                **inspect(page, base, headers, managed_root, source, restart=restart)})
                            context.close(); context = None
                            request = Request(base + '/_failed_retry_test/shutdown',
                                              data=b'', headers=headers, method='POST')
                            with build_opener(ProxyHandler({})).open(request, timeout=5):
                                pass
                            process.wait(timeout=15)
                            result['normal_exit'].append(process.returncode == 0)
                        finally:
                            if context is not None:
                                context.close()
                            stop_child(process)
                            result['ports_released'].append(port_released(port))
                        assert hashlib.sha256(source.read_bytes()).digest() == source_sha
                        assert not any(Path(str(source) + suffix).exists()
                                       for suffix in ('-wal', '-shm', '-journal'))
            finally:
                browser.close()
        assert len(result['viewports']) == 4
        assert all(result['normal_exit']) and all(result['ports_released'])
        assert not result['foreign_requests'] and not result['console_errors'] and not result['page_errors']
        assert before == source_hashes()
        result['success'] = True
    except Exception as exc:
        result['error'] = f'{type(exc).__name__}: {str(exc)[:350]}'
    (output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if result['success'] else 1


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--serve', action='store_true')
    cli.add_argument('--port', type=int)
    cli.add_argument('--managed-root', type=Path)
    cli.add_argument('--source', type=Path)
    cli.add_argument('--nonce')
    stage = cli.add_mutually_exclusive_group()
    stage.add_argument('--restart', action='store_true')
    stage.add_argument('--initial', action='store_true')
    cli.add_argument('--channel', choices=('msedge', 'chromium'), default='chromium')
    cli.add_argument('--output', type=Path, required=False)
    flags = cli.parse_args()
    if flags.serve:
        serve(flags.port, flags.managed_root, flags.source, flags.nonce, restart=flags.restart)
    else:
        if flags.output is None:
            cli.error('--output is required for browser acceptance')
        raise SystemExit(run(flags.channel, flags.output))
