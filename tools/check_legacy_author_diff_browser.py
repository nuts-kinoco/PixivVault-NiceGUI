"""Edge acceptance for old DB/ZIP author diff, with disposable synthetic data only."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import tempfile
from time import perf_counter
from urllib.parse import urlsplit

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.run_browser_checks import child_environment, free_port, port_released, source_hashes, stop_child, wait_for_health
from tools.check_managed_downloads_browser import ManagedTransport, png
from tools.check_live_bookmark_browser import write_synthetic_cookie


class LegacyTransport(ManagedTransport):
    def __init__(self):
        super().__init__()
        self.original_gets.clear()
        self.author_revision = '2026-10-23T10:00:00+09:00'

    def __call__(self, request, timeout, limit, deadline):
        url = urlsplit(request.full_url)
        self.request_count += 1
        self.all_requests_get &= request.get_method() == 'GET'
        api = (url.scheme == 'https' and url.hostname == self.API_HOST and url.port is None
               and url.username is None and url.password is None)
        image = (url.scheme == 'https' and url.hostname == self.IMAGE_HOST and url.port is None
                 and url.username is None and url.password is None)
        self.allowed_https_hosts_only &= api or image
        if api:
            if not request.has_header('Cookie') or request.get_method() != 'GET':
                raise AssertionError('synthetic API requests must be authenticated GET only')
        elif image:
            self.image_requests_cookie_free &= not request.has_header('Cookie')
            if request.has_header('Cookie') or request.get_method() != 'GET':
                raise AssertionError('synthetic image requests must be cookie-free GET only')
            import re
            match = re.fullmatch(r'/img-original/img/2026/10/01/12/00/00/([0-9]+)_p0\.png', url.path)
            if not match:
                raise AssertionError('unexpected synthetic image URL')
            self.original_gets[match.group(1)] += 1
            return png((64, 32), (101, 102, 103))
        else:
            raise AssertionError('unexpected non-Pixiv transport destination')
        if url.path == '/':
            return self._home()
        if url.path == '/ajax/user/extra':
            return self._packet({'userId': '12'})
        if url.path == '/ajax/user/91/profile/all':
            return self._packet({'illusts': {'101': None, '102': None, '103': None,
                                             '106': None}, 'manga': []})
        if url.path == '/ajax/user/91/profile/illusts':
            from urllib.parse import parse_qs
            ids = parse_qs(url.query).get('ids[]', [])
            rows = {'101': ('Restored synthetic', True), '102': ('Missing synthetic', False),
                    '103': ('Unknown date synthetic', False), '106': ('Updated synthetic', False)}
            return self._packet({'works': {wid: {
                'id': wid, 'userId': '91', 'userName': 'Legacy fixture', 'title': rows[wid][0],
                'illustType': 0, 'pageCount': 1,
                'updateDate': '' if wid == '103' else
                    '2026-10-23T10:00:00+09:00' if wid == '106' else self.author_revision,
                'createDate': '2026-10-01T00:00:00+09:00'} for wid in ids}})
        if url.path.startswith('/ajax/illust/') and url.path.endswith('/pages'):
            wid = url.path.split('/')[3]
            return self._packet([{'urls': {'regular': f'https://i.pximg.net/synthetic/{wid}.png',
                                            'original': f'https://i.pximg.net/img-original/img/2026/10/01/12/00/00/{wid}_p0.png'}}])
        if url.path.startswith('/ajax/illust/'):
            wid = url.path.rsplit('/', 1)[-1]
            return self._packet({'id': wid, 'title': f'Synthetic {wid}', 'userId': '91',
                'userName': 'Legacy fixture', 'tags': {'tags': []}, 'illustType': 0,
                'pageCount': 1, 'xRestrict': 0, 'createDate': '2026-10-01T00:00:00+09:00',
                'updateDate': '2026-10-23T10:00:00+09:00' if wid == '106' else self.author_revision,
                'urls': {'small': f'https://i.pximg.net/synthetic/{wid}.png'}})
        raise AssertionError('unexpected synthetic API route')


def make_fixture(root: Path):
    db = root / 'old-copy.sqlite3'
    media = root / 'old-media'
    media.mkdir()
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE works(work_id TEXT PRIMARY KEY,title TEXT,user_id TEXT,page_count INTEGER,content_type TEXT,update_date TEXT,is_deleted INTEGER,missing_streak INTEGER)')
        conn.executemany('INSERT INTO works VALUES(?,?,?,?,?,?,?,?)', [
            ('101', 'Restored synthetic', '91', 1, 'illust', '2026-10-23T10:00:00+09:00', 1, 0),
            ('102', 'Missing synthetic', '91', 1, 'illust', '2026-10-23T10:00:00+09:00', 0, 0),
            ('103', 'Unknown date synthetic', '91', 1, 'illust', '2026-10-23T10:00:00+09:00', 0, 0),
            ('104', 'Absent synthetic', '91', 1, 'illust', '2026-10-22T10:00:00+09:00', 0, 2),
            ('106', 'Updated synthetic', '91', 1, 'illust', '2026-10-22T10:00:00+09:00', 0, 0),
            ('900', None, None, 0, 'unknown', None, None, None),
        ])
        conn.execute('CREATE TABLE failed_queue(work_id TEXT,user_id TEXT,title TEXT,work_type TEXT,error_reason TEXT,failed_at TEXT,retry_count INTEGER)')
        conn.execute("INSERT INTO failed_queue VALUES('101','91','Restored synthetic','illust','Synthetic old failure','2026-10-22',1)")
    old_folder = media / 'Legacy author(91)'
    old_folder.mkdir()
    (old_folder / 'Unknown date synthetic(103).png').write_bytes(png((64, 32), (42, 71, 109)))
    hashes = {'db': sha256(db.read_bytes()).hexdigest(),
              'media_tree': media_tree_signature(media)}
    return db, media, hashes


def media_tree_signature(root: Path):
    return tuple((path.relative_to(root).as_posix(), sha256(path.read_bytes()).hexdigest())
                 for path in sorted(root.rglob('*')) if path.is_file())


def verify_observation_ledger(root: Path, db: Path, media: Path, old_hashes):
    sidecar = root / '.pixivvault-legacy-author-sync.sqlite3'
    assert sidecar.is_file() and not sidecar.is_symlink()
    assert not any(Path(str(db) + suffix).exists() for suffix in ('-wal', '-shm', '-journal'))
    assert not any(Path(str(sidecar) + suffix).exists() for suffix in ('-wal', '-shm', '-journal'))
    assert sha256(db.read_bytes()).hexdigest() == old_hashes['db']
    assert media_tree_signature(media) == old_hashes['media_tree']
    with sqlite3.connect(sidecar.as_uri() + '?mode=ro', uri=True) as conn:
        streak, deleted = conn.execute(
            "SELECT missing_streak,deleted FROM states WHERE author_id='91' AND work_id='104'").fetchone()
        scans = conn.execute('SELECT COUNT(*) FROM observations WHERE author_id=?', ('91',)).fetchone()[0]
    assert (streak, deleted, scans) == (3, 1, 1)
    return {'sidecar_in_managed_root': True, 'source_sidecars_absent': True,
            'media_tree_stable': True, 'remote_absent_streak': streak,
            'remote_absent_deleted': bool(deleted), 'durable_scan_receipts': scans}


def serve(port, root, nonce, db, media):
    from fastapi import Header, HTTPException
    from nicegui import app, ui
    from pixivvault_nicegui.bootstrap import register_pages
    from pixivvault_nicegui.pixiv_read_client import LivePixivReadClient
    with tempfile.TemporaryDirectory(prefix='pv-legacy-diff-cookie-') as cookies:
        transport = LegacyTransport()
        client = LivePixivReadClient(write_synthetic_cookie(cookies), transport=transport)
        register_pages(live_client=client, pixiv_download_root=root,
                       failed_queue_readonly_db=db, legacy_diff_image_root=media)

        @app.get('/_legacy_diff/status')
        def status(x_acceptance: str | None = Header(default=None)):
            if x_acceptance != nonce:
                raise HTTPException(403)
            return {'synthetic': True, 'requests': transport.request_count,
                    'all_get': transport.all_requests_get,
                    'https_only': transport.allowed_https_hosts_only,
                    'image_requests_cookie_free': transport.image_requests_cookie_free,
                    'original_gets': dict(transport.original_gets)}

        @app.post('/_legacy_diff/shutdown')
        def shutdown(x_acceptance: str | None = Header(default=None)):
            if x_acceptance != nonce:
                raise HTTPException(403)
            app.shutdown()
            return {'stopping': True}

        # Bound Uvicorn's websocket drain before the application's shutdown
        # callbacks. Windows may retain a disconnected browser socket otherwise.
        ui.run(host='127.0.0.1', port=port, show=False, reload=False,
               binding_refresh_interval=None, timeout_graceful_shutdown=5)
        client.close()


def run(channel, output):
    if output.exists():
        raise ValueError('new --output directory required')
    from playwright.sync_api import sync_playwright, expect
    output = output.resolve()
    output.mkdir(parents=True)
    fixture_root = output / 'fixture'
    fixture_root.mkdir()
    db, media, old_hashes = make_fixture(fixture_root)
    before = source_hashes()
    results = {'success': False, 'synthetic_only': True, 'channel': channel, 'runs': [],
               'submit_preflights': [], 'console_errors': [], 'page_errors': [],
               'foreign_requests': [], 'normal_exit': [], 'ports_released': []}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel=None if channel == 'chromium' else channel)
            try:
                for width, height in ((1280, 720), (1920, 1080)):
                    for restart in (False, True):
                        root = (output / f'managed-{width}x{height}').resolve()
                        if not restart:
                            if root.exists():
                                raise AssertionError('managed root must be fresh per viewport')
                            root.mkdir()
                        elif not root.is_dir():
                            raise AssertionError('managed root missing on restart')
                        port, nonce = free_port(), secrets.token_hex(16)
                        base, headers = f'http://127.0.0.1:{port}', {'X-Acceptance': nonce}
                        log_path = output / f'server-{width}-{restart}.log'
                        process = None
                        try:
                            with log_path.open('wb') as log:
                                process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                                    '--serve', str(port), '--root', str(root), '--nonce', nonce,
                                    '--db', str(db), '--media', str(media)], env=child_environment(),
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log)
                            wait_for_health(base, process, expected_data='pixiv-readonly')
                            context = browser.new_context(viewport={'width': width, 'height': height})
                            def route(req):
                                parsed = urlsplit(req.request.url)
                                if (parsed.scheme, parsed.netloc) == ('http', f'127.0.0.1:{port}'):
                                    req.continue_()
                                else:
                                    results['foreign_requests'].append('blocked')
                                    req.abort()
                            context.route('**/*', route)
                            page = context.new_page()
                            page.on('console', lambda msg: results['console_errors'].append(msg.text[:200]) if msg.type == 'error' else None)
                            page.on('pageerror', lambda err: results['page_errors'].append(str(err)[:200]))
                            page.goto(base, wait_until='domcontentloaded')
                            if restart:
                                page.get_by_test_id('tab-queue').click()
                                expect(page.get_by_test_id('queue-view').get_by_test_id('queue-filter-done')).to_contain_text('完了 3', timeout=20000)
                                page.get_by_test_id('tab-download').click()
                            else:
                                page.get_by_test_id('tab-download').click()
                            view = page.get_by_test_id('managed-download-view')
                            expect(view).to_be_visible(timeout=20000)
                            view.get_by_test_id('managed-download-author-id').fill('91')
                            view.get_by_test_id('managed-download-author-add').click()
                            expect(view.get_by_test_id('managed-download-author-selection')).to_contain_text('選択作者 1 件')
                            other = context.new_page()
                            other.goto(base, wait_until='domcontentloaded')
                            other.get_by_test_id('tab-download').click()
                            expect(other.get_by_test_id('managed-download-author-selection')).to_contain_text('選択作者 0 件')
                            expect(other.get_by_test_id('managed-download-selection')).to_contain_text('対象 0 件')
                            other.close()
                            if not restart:
                                view.get_by_test_id('managed-download-author-scan').click()
                                expect(view.get_by_test_id('managed-download-author-status')).to_contain_text('削除確定 1 件', timeout=20000)
                            t0 = perf_counter()
                            view.get_by_test_id('managed-download-author-fetch').click()
                            if not restart:
                                expect(view.get_by_test_id('managed-download-author-status')).to_contain_text('復帰 1 件', timeout=20000)
                                expect(view.get_by_test_id('managed-download-author-status')).to_contain_text('実物欠損 1 件')
                                expect(view.get_by_test_id('managed-download-author-status')).to_contain_text('更新 1 件')
                                expect(view.get_by_test_id('managed-download-author-status')).to_contain_text('旧失敗履歴 1 件')
                                expect(page.get_by_test_id('managed-download-author-differences').locator('[data-difference="restored"]')).to_have_count(1)
                                expect(page.get_by_test_id('managed-download-author-differences').locator('[data-difference="missing"]')).to_have_count(1)
                                expect(page.get_by_test_id('managed-download-author-differences').locator('[data-difference="unknown"]')).to_have_count(1)
                                expect(page.get_by_test_id('managed-download-author-differences').locator('[data-difference="updated"]')).to_have_count(1)
                            else:
                                expect(view.get_by_test_id('managed-download-author-status')).to_contain_text('復帰 0 件', timeout=20000)
                                expect(view.get_by_test_id('managed-download-author-differences').locator('[data-difference="skipped"]')).to_have_count(3)
                            expect(page.get_by_test_id('managed-download-remote-absent-row')).to_have_count(1)
                            expect(page.get_by_test_id('managed-download-selection')).to_contain_text(
                                '対象 0 件' if restart else '対象 3 件')
                            if not restart:
                                submit_button = view.get_by_test_id('managed-download-submit')
                                expect(submit_button).to_be_enabled()
                                submit_button.scroll_into_view_if_needed()
                                preflight = submit_button.evaluate("el => {const body=el.closest('.pv-download-body');const view=el.closest('.pv-jobs-view');const r=el.getBoundingClientRect();const top=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);const hidden=[];let n=el.parentElement;while(n){if(getComputedStyle(n).overflowY==='hidden'&&n.scrollTop!==0)hidden.push({className:String(n.className).slice(0,80),scrollTop:n.scrollTop});n=n.parentElement;}return {viewport:[innerWidth,innerHeight],disabled:el.disabled,rect:{x:r.x,y:r.y,width:r.width,height:r.height},bodyHeight:body?body.clientHeight:0,viewOverflowY:view?getComputedStyle(view).overflowY:'missing',hitTarget:!!top&&(top===el||el.contains(top)),hiddenAncestorScrolls:hidden};}")
                                results['submit_preflights'].append(preflight)
                                assert preflight['bodyHeight'] >= 180, preflight
                                assert preflight['viewOverflowY'] == 'auto', preflight
                                assert preflight['hitTarget'], preflight
                                assert not preflight['hiddenAncestorScrolls'], preflight
                            elapsed_ms = round((perf_counter()-t0)*1000, 2)
                            if not restart:
                                submit_button.click(timeout=10000)
                                expect(view.get_by_test_id('managed-download-notice')).to_contain_text('3件をキュー', timeout=20000)
                                queue = page.get_by_test_id('tab-queue')
                                queue.click()
                                expect(page.get_by_test_id('queue-view').get_by_test_id('queue-filter-done')).to_contain_text('完了 3', timeout=30000)
                            status = page.request.get(base+'/_legacy_diff/status', headers=headers).json()
                            assert status['synthetic'] and status['all_get'] and status['https_only'] and status['image_requests_cookie_free']
                            assert sha256(db.read_bytes()).hexdigest() == old_hashes['db']
                            assert media_tree_signature(media) == old_hashes['media_tree']
                            observed_states = [state for state in ('new','updated','restored','missing','skipped','unknown')
                                if page.get_by_test_id('managed-download-author-differences').locator(
                                    f'[data-difference="{state}"]').count() > 0]
                            expected_states = (['updated', 'restored', 'missing', 'unknown']
                                               if not restart else ['skipped', 'unknown'])
                            assert observed_states == expected_states, (observed_states, expected_states)
                            results['runs'].append({'viewport':[width,height], 'restart':restart,
                                'difference_render_ms':elapsed_ms, 'states':observed_states,
                                'remote_absent_rows':1, 'old_source_sha_stable':True,
                                'original_gets':status['original_gets']})
                            context.close()
                            from urllib.request import Request, ProxyHandler, build_opener
                            req = Request(base+'/_legacy_diff/shutdown', data=b'', headers=headers, method='POST')
                            with build_opener(ProxyHandler({})).open(req, timeout=5): pass
                            process.wait(timeout=25)
                            results['normal_exit'].append(process.returncode == 0)
                            results['runs'][-1]['observation_ledger'] = verify_observation_ledger(
                                root, db, media, old_hashes)
                        finally:
                            stop_child(process)
                            results['ports_released'].append(port_released(port))
            finally:
                browser.close()
        assert len(results['runs']) == 4
        assert not results['console_errors'] and not results['page_errors'] and not results['foreign_requests']
        assert all(results['normal_exit']) and all(results['ports_released'])
        assert before == source_hashes()
        results['old_source_sha_before_after'] = old_hashes
        assert media_tree_signature(media) == old_hashes['media_tree']
        results['success'] = True
    except subprocess.TimeoutExpired:
        # TimeoutExpired.__str__ includes argv, including the temporary nonce.
        results['failure'] = 'TimeoutExpired: synthetic server did not exit after shutdown'
    except Exception as exc:
        # Never serialize arbitrary exception details containing paths, nonce,
        # transport payloads or other sensitive input into CI evidence.
        results['failure'] = f'{type(exc).__name__}: synthetic browser acceptance failed'
    (output/'result.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if results['success'] else 1


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--channel', choices=('msedge','chromium'), default='chromium')
    cli.add_argument('--output', type=Path)
    cli.add_argument('--serve', type=int)
    cli.add_argument('--root', type=Path)
    cli.add_argument('--nonce')
    cli.add_argument('--db', type=Path)
    cli.add_argument('--media', type=Path)
    args = cli.parse_args()
    if args.serve is not None:
        serve(args.serve, args.root, args.nonce, args.db, args.media)
        raise SystemExit(0)
    if args.output is None:
        cli.error('--output required')
    raise SystemExit(run(args.channel, args.output))
