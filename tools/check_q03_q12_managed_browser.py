"""Q03/Q12: real Edge input over synthetic, genuinely persisted managed jobs.

Run only after the GUI owner authorizes it. Import and CPU tests do not launch
a server/browser. No Cookie, original database, Pixiv request, or forced kill.
The fixture gates a real runner before writing; it never fakes job acceptance
or a receipt. Two viewport sizes each use an initial app and a restarted app.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import subprocess
import sys
from threading import Event, Lock
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ''):
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / 'src'))

from tools.check_failed_managed_retry_browser import FakePixiv
from tools.check_library_calendar_slider_owned import (
    OwnedTree, deny_external_connections, owned_output, write_new,
)
from tools.run_browser_checks import (
    child_environment, free_port, port_released, source_hashes, wait_for_health,
)

PREFIX = 'p3-340-q03-q12-worker7-'
KIND = 'pixivvault.synthetic.q03-q12.v1'
VIEWPORTS = ((1280, 720), (1920, 1080))
PHASES = ('initial', 'restart')
ROWS = (
    ('301', '23', '合成イラスト', 'illust', 'Timeout', '2026-10-23 10:00:00', 1),
    ('302', '23', '合成漫画', 'manga', '404', '2026-10-23 09:00:00', 0),
    ('305', '23', '合成停止対象', 'illust', 'Timeout', '2026-10-23 08:00:00', 0),
    ('306', '23', '合成待機対象', 'illust', 'Timeout', '2026-10-23 07:00:00', 0),
)
PROTECTED = ('original.sqlite3', 'frozen.sqlite3', '301.png', '302.png')


def require(value, message):
    if not value:
        raise AssertionError(message)


def now_jst():
    return datetime.now(timezone(timedelta(hours=9))).isoformat()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def regular_path(path):
    """Reject links/reparse points before resolving an explicit owned path."""
    path = Path(path).absolute()
    if '..' in path.parts:
        raise ValueError('parent traversal is not an owned fixture path')
    for item in (path, *path.parents):
        try:
            metadata = item.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(metadata.st_mode)
                or getattr(metadata, 'st_file_attributes', 0) & 0x400
                or (stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1)):
            raise ValueError('fixture links/reparse points/hardlinks are forbidden')
    return path


def admit_output(path):
    path = regular_path(path)
    path = owned_output(path)
    if (path.parent != (ROOT / 'artifacts').resolve()
            or not path.name.startswith(PREFIX) or path.name == PREFIX):
        raise ValueError('use a new p3-340-q03-q12-worker7-* artifact directory')
    return path


def file_fingerprint(path):
    path = regular_path(path)
    meta = path.stat()
    require(stat.S_ISREG(meta.st_mode), 'expected regular fixture file')
    return {'bytes': meta.st_size, 'mtime_ns': meta.st_mtime_ns, 'sha256': digest(path)}


def protected_files(folder):
    for name in ('original.sqlite3', 'frozen.sqlite3'):
        for suffix in ('-wal', '-shm', '-journal'):
            require(not (folder / (name + suffix)).exists(), 'source SQLite sidecar appeared')
    return {name: file_fingerprint(folder / name) for name in PROTECTED}


def read_source_rows(path):
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)) as conn:
        return tuple(conn.execute('SELECT work_id,user_id,title,work_type,error_reason,'
                                  'failed_at,retry_count FROM failed_queue ORDER BY work_id'))


def build_fixture(case):
    """Generate and close both SQLite handles before immutable-source admission."""
    folder = regular_path(case) / 'source'
    folder.mkdir(exist_ok=False)
    original, frozen = folder / 'original.sqlite3', folder / 'frozen.sqlite3'
    with closing(sqlite3.connect(original)) as source:
        with source:
            source.execute('CREATE TABLE failed_queue (work_id TEXT PRIMARY KEY, user_id TEXT,'
                           'title TEXT, work_type TEXT, error_reason TEXT, failed_at TEXT,'
                           'retry_count INTEGER)')
            source.executemany('INSERT INTO failed_queue VALUES (?,?,?,?,?,?,?)', ROWS)
        with closing(sqlite3.connect(frozen)) as target:
            source.backup(target)
    offline = FakePixiv()
    for wid in ('301', '302'):
        with (folder / (wid + '.png')).open('xb') as stream:
            stream.write(offline.get_image_bytes(f'https://i.pximg.net/synthetic/{wid}_p0.png'))
    manifest = {'kind': KIND, 'files': protected_files(folder)}
    write_new(folder / 'fixture.json', manifest)
    verify_fixture(case)
    return manifest


def verify_fixture(case):
    folder = regular_path(case) / 'source'
    marker = regular_path(folder / 'fixture.json')
    require(marker.stat().st_size < 16384, 'oversized fixture marker')
    manifest = json.loads(marker.read_text(encoding='utf-8'))
    require(manifest == {'kind': KIND, 'files': protected_files(folder)}, 'protected fixture changed')
    for name in PROTECTED[:2]:
        require(read_source_rows(folder / name) == ROWS, 'not the generated four-row source')
    return manifest


class OfflineManaged(FakePixiv):
    """Reuse the existing double, restricting its image URLs to this fixture."""
    def __init__(self, case, *, no_remote=False):
        super().__init__(no_remote=no_remote)
        self.attempted = 0
        self.metadata_ids = []
        self.image_ids = []
        self.payloads = {wid: (case / 'source' / (wid + '.png')).read_bytes()
                         for wid in ('301', '302')}

    def _request(self):
        self.attempted += 1
        super()._request()

    def get_artwork_download_record(self, work_id):
        require(work_id in {'301', '302', '305', '306'}, 'unexpected synthetic artwork')
        self.metadata_ids.append(work_id)
        record = super().get_artwork_download_record(work_id if work_id in ('301', '302') else '301')
        return replace(record, remote_id=work_id, title=f'合成作品 {work_id}')

    def get_image_bytes(self, url):
        match = re.fullmatch(r'https://i\.pximg\.net/synthetic/(301|302|305|306)_p0\.png', url)
        require(match is not None, 'unexpected synthetic image URL')
        self._request()
        wid = match.group(1)
        self.original_gets += 1
        self.image_ids.append(wid)
        return self.payloads['301' if wid == '301' else '302']


class ManagedGate:
    """Hold only 305, poll the real cancellation checkpoint, preserve the writer."""
    def __init__(self, writer, *, timeout=90.0):
        self.writer = writer
        self.timeout = timeout
        self.release = Event()
        self.lock = Lock()
        self.started = []
        self.cancelled = []
        self.finished = []

    def __call__(self, job, context):
        from pixivvault_nicegui.job_service import JobCancelled
        with self.lock:
            self.started.append(job.target_id)
        try:
            deadline = time.monotonic() + self.timeout
            if job.target_id == '305':
                while not self.release.wait(0.02):
                    context.checkpoint()
                    require(time.monotonic() < deadline, 'synthetic active-job gate timed out')
            context.checkpoint()
            return self.writer(job, context)
        except JobCancelled:
            with self.lock:
                self.cancelled.append(job.target_id)
            raise
        finally:
            with self.lock:
                self.finished.append(job.target_id)

    def snapshot(self):
        with self.lock:
            return {key: list(getattr(self, key)) for key in ('started', 'cancelled', 'finished')}


def saved_outputs(case):
    """Verify actual product receipts, hashes, decoded pixels and exact saved bytes."""
    from PIL import Image
    root = case / 'managed'
    receipts = sorted((root / 'outputs').glob('work-*/receipt.json'))
    require(len(receipts) == 2, 'expected exactly two real managed receipts')
    seen, inventory = set(), {}
    for path in receipts:
        regular_path(path)
        receipt = json.loads(path.read_text(encoding='utf-8'))
        wid = receipt['work_id']
        require(wid in {'301', '302'} and wid not in seen, 'unexpected/duplicate saved artwork')
        seen.add(wid)
        require(receipt['source'] == 'pixiv-original' and receipt['author_id'] == '23'
                and receipt['author_name'] == '合成作者' and receipt['page_count'] == 1
                and len(receipt['files']) == 1, 'invalid managed receipt identity')
        expected = (case / 'source' / (wid + '.png')).read_bytes()
        names = {'receipt.json'}
        for entry in receipt['files']:
            name = entry['name']
            require(name == Path(name).name and '/' not in name and '\\' not in name
                    and name not in ('.', '..'), 'receipt page escaped output folder')
            page = regular_path(path.parent / name)
            data = page.read_bytes()
            require(data == expected and len(data) == entry['bytes']
                    and hashlib.sha256(data).hexdigest() == entry['sha256'], 'saved page bytes mismatch')
            with Image.open(BytesIO(data)) as image:
                image.load()
                require(image.size == (120, 84) and image.size == (entry['width'], entry['height']),
                        'saved page dimensions mismatch')
                require(image.convert('RGB').getpixel((0, 0)) ==
                        ((13, 37, 211) if wid == '301' else (9, 191, 53)), 'saved page pixels mismatch')
            names.add(name)
        require({p.name for p in path.parent.iterdir()} == names, 'unexpected receipt folder content')
    require(seen == {'301', '302'}, 'missing managed artwork')
    for path in sorted((root / 'outputs').rglob('*')):
        regular_path(path)
        if path.is_file():
            inventory[path.relative_to(root).as_posix()] = {'bytes': path.stat().st_size,
                                                          'sha256': digest(path)}
    require(len(inventory) == 4, 'unexpected output files or unfinished-job publication')
    return inventory


def acceptance_hashes():
    result = source_hashes()
    for name in ('tools/check_q03_q12_managed_browser.py', 'tests/test_q03_q12_managed_checker.py',
                 'tools/check_failed_managed_retry_browser.py', 'tools/check_download_jobs_browser.py',
                 'tools/check_library_calendar_slider_owned.py', 'tools/run_browser_checks.py',
                 'tools/check_native_window.py', 'pyproject.toml'):
        path = ROOT / name
        if path.is_file():
            result[name] = digest(path)
    return result


def authorized(value, nonce):
    return (isinstance(value, str) and value.isascii() and len(value) == len(nonce)
            and secrets.compare_digest(value, nonce))


class LoopbackOnly:
    """ASGI Host/Origin/peer fence; control endpoints additionally require nonce."""
    def __init__(self, app, *, port):
        self.app, self.port = app, port

    async def __call__(self, scope, receive, send):
        if scope['type'] in ('http', 'websocket'):
            headers = dict(scope.get('headers', ()))
            host = f'127.0.0.1:{self.port}'.encode()
            peer = (scope.get('client') or ('',))[0]
            allowed = (peer in ('127.0.0.1', '::1') and headers.get(b'host') == host
                       and headers.get(b'origin', b'http://' + host) == b'http://' + host)
            if not allowed:
                if scope['type'] == 'websocket':
                    await send({'type': 'websocket.close', 'code': 1008})
                else:
                    await send({'type': 'http.response.start', 'status': 403, 'headers': []})
                    await send({'type': 'http.response.body', 'body': b'local fixture only'})
                return
        await self.app(scope, receive, send)


def serve(output, width, phase, port, nonce):
    output = admit_output(output)
    require(width in (1280, 1920) and phase in PHASES and 0 < port < 65536, 'invalid fixture phase')
    case, folder = output / str(width), output / str(width) / phase
    marker = json.loads(regular_path(folder / 'owner.json').read_text(encoding='utf-8'))
    require(re.fullmatch(r'[a-f0-9]{64}', nonce or '') is not None, 'invalid fixture nonce')
    require(marker == {'kind': KIND, 'phase': phase, 'width': width, 'port': port,
                       'nonce_sha256': hashlib.sha256(nonce.encode()).hexdigest()}, 'fixture owner mismatch')
    before = verify_fixture(case)
    network = deny_external_connections()
    from fastapi import Header, HTTPException
    from nicegui import app, ui
    from pixivvault_nicegui.bootstrap import register_pages
    from pixivvault_nicegui.services import get_services
    remote = OfflineManaged(case, no_remote=phase == 'restart')
    register_pages(live_client=remote, pixiv_download_root=case / 'managed',
                   failed_queue_readonly_db=case / 'source' / 'frozen.sqlite3')
    services = get_services(app)
    gate = ManagedGate(services.static_jobs._runner)
    services.static_jobs._runner = gate
    stopping = False
    app.add_middleware(LoopbackOnly, port=port)

    def auth(value):
        if not authorized(value, nonce):
            raise HTTPException(403)

    @app.get('/_q03_q12/snapshot', include_in_schema=False)
    def snapshot(x_acceptance: str | None = Header(default=None)):
        auth(x_acceptance)
        return {'pid': os.getpid(), 'kind': KIND, 'jobs': [asdict(j) for j in services.jobs.snapshot()],
                'gate': gate.snapshot(), 'active': services.static_jobs._active,
                'fake_requests': remote.requests, 'fake_attempted': remote.attempted,
                'original_gets': remote.original_gets, 'image_ids': list(remote.image_ids),
                'metadata_ids': list(remote.metadata_ids)}

    @app.post('/_q03_q12/shutdown', include_in_schema=False)
    async def shutdown(x_acceptance: str | None = Header(default=None)):
        nonlocal stopping
        auth(x_acceptance)
        if stopping:
            raise HTTPException(409)
        stopping = True
        app.shutdown()
        return {'stopping': True}

    try:
        ui.run(host='127.0.0.1', port=port, show=False, reload=False,
               binding_refresh_interval=None)
    finally:
        write_new(folder / 'server-lifecycle.json', {
            'pid': os.getpid(), 'services_closed': services.closed, 'shutdown_requested': stopping,
            'external_denied': network['denied'], 'protected_before': before,
            'protected_after': verify_fixture(case), 'gate': gate.snapshot(),
            'fake_attempted': remote.attempted, 'original_gets': remote.original_gets,
        })


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('fixture control redirects are forbidden')


class Control:
    def __init__(self, port, nonce):
        self.base = f'http://127.0.0.1:{port}'
        self.nonce = nonce
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def __call__(self, action='snapshot'):
        require(action in ('snapshot', 'shutdown'), 'unknown fixture control')
        request = Request(self.base + '/_q03_q12/' + action,
                          headers={'X-Acceptance': self.nonce},
                          method='POST' if action == 'shutdown' else 'GET',
                          data=b'' if action == 'shutdown' else None)
        with self.opener.open(request, timeout=5) as response:
            require(response.status == 200, 'fixture control rejected')
            payload = response.read(131073)
            require(len(payload) <= 131072, 'oversized fixture control response')
            return json.loads(payload)


def wait_snapshot(control, predicate, *, timeout=15):
    deadline = time.monotonic() + timeout
    while True:
        state = control()
        if predicate(state):
            return state
        require(time.monotonic() < deadline, 'fixture state did not settle')
        time.sleep(0.05)


def job_by_work(state):
    result = {job['target_id']: job for job in state['jobs']}
    require(len(result) == len(state['jobs']), 'duplicate job for synthetic target')
    return result


def mark(trace, action, **values):
    trace.append({'jst': now_jst(), 'action': action, **values})


def queue_view(page):
    from playwright.sync_api import expect
    page.get_by_test_id('tab-queue').click()
    queue = page.get_by_test_id('queue-view')
    expect(queue).to_be_visible()
    return queue


def verify_search(page, queue, jobs, trace):
    """Keyboard events and the UI clear icon; never set product state directly."""
    from playwright.sync_api import expect
    field = queue.get_by_placeholder('作者名・作品IDで絞り込む')
    target = '301' if '301' in jobs else '302'
    for query, expected in (('合成作者', set(jobs)), (target, {target}), ('別の作者', set())):
        field.click()
        field.press('Control+A')
        field.press('Backspace')
        field.press_sequentially(query, delay=10)
        expect(field).to_have_value(query)
        expect(queue.get_by_test_id('queue-count')).to_have_text(f'{len(expected)}件')
        expect(queue.get_by_test_id('job-row')).to_have_count(len(expected))
        expect(queue.get_by_test_id('queue-page')).to_have_text('1 / 1')
        actual = set(queue.get_by_test_id('job-row').evaluate_all(
            'rows => rows.map(row => row.dataset.jobId)'))
        require(actual == {jobs[wid]['job_id'] for wid in expected}, 'search returned incorrect job IDs')
        mark(trace, 'Q03 keyboard search', query=query, target_ids=sorted(expected), job_ids=sorted(actual))
    queue.locator('.q-field__focusable-action').click()
    expect(field).to_have_value('')
    expect(queue.get_by_test_id('queue-count')).to_have_text(f'{len(jobs)}件')
    expect(queue.get_by_test_id('job-row')).to_have_count(len(jobs))
    actual = set(queue.get_by_test_id('job-row').evaluate_all('rows=>rows.map(row=>row.dataset.jobId)'))
    require(actual == {j['job_id'] for j in jobs.values()}, 'clear did not restore the complete set')
    mark(trace, 'Q03 clear icon', target_ids=sorted(jobs), job_ids=sorted(actual))


def retry_from_ui(page, control, wid, expected_state, trace):
    from playwright.sync_api import expect
    page.get_by_test_id('tab-failed').click()
    failures = page.get_by_test_id('failed-records-view')
    expect(failures.get_by_test_id('failed-count')).to_have_text('4 / 4 件', timeout=20000)
    failures.locator(f'[data-testid="failed-record"][data-work-id="{wid}"]').get_by_test_id(
        'failed-row-retry').click()
    expect(failures.get_by_test_id('failed-managed-retry-status')).to_contain_text(
        '1 件を管理キューへ追加', timeout=20000)
    state = wait_snapshot(control, lambda s: job_by_work(s).get(wid, {}).get('state') == expected_state)
    queue = queue_view(page)
    row = queue.locator(f'[data-testid="job-row"][data-job-id="{job_by_work(state)[wid]["job_id"]}"]')
    expect(row).to_have_attribute('data-state', expected_state, timeout=20000)
    mark(trace, 'managed retry click', target_id=wid, state=expected_state,
         job_id=job_by_work(state)[wid]['job_id'])
    return state


def wait_dialog_painted(page, dialog):
    """Visibility alone also matches a fade-in with effective opacity zero."""
    element=dialog.element_handle()
    require(element is not None, 'confirmation element disappeared before capture')
    dialog.evaluate('el => {delete el.__pvAcceptancePaint}')
    page.wait_for_function("""el => {
        if (!el || !el.isConnected) return false;
        for (let node=el; node; node=node.parentElement) {
            const style=getComputedStyle(node);
            if (style.display==='none' || style.visibility!=='visible' ||
                Number(style.opacity)<0.995) {
                delete el.__pvAcceptancePaint; return false;
            }
            if (node.getAnimations().some(a => a.playState==='running')) {
                delete el.__pvAcceptancePaint; return false;
            }
        }
        const r=el.getBoundingClientRect();
        if (!(r.width>0 && r.height>0 && r.left>=0 && r.top>=0 &&
              r.right<=innerWidth && r.bottom<=innerHeight)) return false;
        const key=[r.x,r.y,r.width,r.height].map(n=>n.toFixed(2)).join(',');
        const previous=el.__pvAcceptancePaint;
        const count=previous && previous.key===key ? previous.count+1 : 1;
        el.__pvAcceptancePaint={key,count};
        return count>=3;
    }""", arg=element, polling='raf', timeout=10000)
    return dialog.evaluate("""el => {
        const r=el.getBoundingClientRect();
        return {x:r.x,y:r.y,width:r.width,height:r.height,
                opacity:getComputedStyle(el).opacity,
                stable_frames:el.__pvAcceptancePaint.count};
    }""")


def bulk_delete(page, queue, control, selected, case, expected_outputs, trace, folder):
    from playwright.sync_api import expect
    before = control()
    jobs = job_by_work(before)
    require(set(selected) <= jobs.keys(), 'selected job missing before UI operation')
    queue.get_by_test_id('queue-select-mode').click()
    expect(queue.get_by_test_id('queue-selection-count')).to_have_text('0件を選択中')
    for index, wid in enumerate(selected, 1):
        queue.locator(f'[data-testid="job-row"][data-job-id="{jobs[wid]["job_id"]}"]').get_by_test_id(
            'job-select').click()
        expect(queue.get_by_test_id('queue-selection-count')).to_have_text(f'{index}件を選択中')
    frozen = {jobs[wid]['job_id']: jobs[wid]['control_revision'] for wid in selected}
    for confirm in (False, True):
        queue.get_by_test_id('queue-bulk-delete').click()
        dialog = page.locator('.q-dialog:visible .q-card')
        expect(dialog).to_have_count(1)
        expect(dialog).to_contain_text(f'選択した {len(selected)} 件のジョブ履歴を削除しますか？')
        expect(dialog).to_contain_text('この履歴削除は取り消せません')
        expect(dialog).to_contain_text('実行中のジョブは停止します')
        expect(dialog).to_contain_text('保存済みファイルは削除しません')
        require(job_by_work(control()) == jobs, 'jobs changed while the fixed confirmation was visible')
        mark(trace, 'Q12 fixed confirmation', count=len(selected), frozen=frozen,
             text=dialog.inner_text(), confirm=confirm)
        paint=wait_dialog_painted(page,dialog)
        mark(trace,'Q12 confirmation painted',confirm=confirm,**paint)
        page.screenshot(path=str(folder / ('confirm.png' if confirm else 'cancel-before.png')))
        dialog.screenshot(path=str(folder / ('confirm-dialog.png' if confirm else 'cancel-dialog.png')))
        if not confirm:
            dialog.get_by_role('button', name='キャンセル', exact=True).click()
            expect(dialog).to_have_count(0)
            expect(queue.get_by_test_id('queue-count')).to_have_text(f'{len(jobs)}件')
            expect(queue.get_by_test_id('queue-selection-count')).to_have_text(f'{len(selected)}件を選択中')
            require(job_by_work(control()) == jobs, 'cancel changed a job or revision')
            require(saved_outputs(case) == expected_outputs, 'cancel changed saved bytes')
            mark(trace, 'Q12 cancel unchanged', job_ids=sorted(frozen))
        else:
            dialog.get_by_test_id('queue-bulk-delete-confirm').click()
    expected = {wid: job for wid, job in jobs.items() if wid not in selected}
    expect(queue.get_by_test_id('queue-count')).to_have_text(f'{len(expected)}件')
    expect(queue.get_by_test_id('queue-selection-count')).to_have_text('0件を選択中')
    expect(queue.get_by_test_id('queue-notice')).to_contain_text(f'受理{len(selected)}件')
    after = wait_snapshot(control, lambda s: job_by_work(s) == expected and s['active'] is None)
    if '305' in selected:
        require('305' in after['gate']['cancelled'] and '305' in after['gate']['finished'],
                'active target did not finish through its real cancellation checkpoint')
        require('306' not in after['gate']['started'], 'deleted queued target was executed')
    require(saved_outputs(case) == expected_outputs, 'history deletion changed receipt/page bytes')
    mark(trace, 'Q12 confirmed', remaining_targets=sorted(expected), cancelled=after['gate']['cancelled'],
         receipt_bytes_unchanged=True, queued_never_started='306' not in after['gate']['started'])
    page.screenshot(path=str(folder / 'after-delete.png'))
    return after


def inspect(page, control, case, phase, folder, expected_outputs, trace):
    from playwright.sync_api import expect
    page.goto(control.base, wait_until='domcontentloaded')
    if phase == 'initial':
        require(control()['jobs'] == [], 'fresh managed queue is not empty')
        retry_from_ui(page, control, '301', 'done', trace)
        retry_from_ui(page, control, '302', 'done', trace)
    queue = queue_view(page)
    jobs = job_by_work(control())
    expected_targets = {'301', '302'} if phase == 'initial' else {'302'}
    require(set(jobs) == expected_targets and all(j['state'] == 'done' for j in jobs.values()),
            'initial/restarted history differs from accepted jobs')
    expect(queue.get_by_test_id('queue-count')).to_have_text(f'{len(jobs)}件')
    verify_search(page, queue, jobs, trace)
    current_outputs = saved_outputs(case)
    if expected_outputs is not None:
        require(current_outputs == expected_outputs, 'restart changed the saved outputs')
    expected_outputs = current_outputs
    write_new(folder / 'saved-before-delete.json', expected_outputs)
    page.screenshot(path=str(folder / 'q03-cleared.png'))
    if phase == 'initial':
        retry_from_ui(page, control, '305', 'running', trace)
        state = retry_from_ui(page, control, '306', 'queued', trace)
        require('305' in state['gate']['started'], 'running fixture gate was not reached')
        selected = ('301', '305', '306')
    else:
        require(control()['fake_attempted'] == 0, 'restart attempted a synthetic remote call')
        mark(trace, 'Q03 persisted accepted metadata after process restart', fake_attempted=0)
        selected = ('302',)
    queue = queue_view(page)
    after = bulk_delete(page, queue, control, selected, case, expected_outputs, trace, folder)
    require(after['original_gets'] == (2 if phase == 'initial' else 0), 'unexpected original image read count')
    require(after['image_ids'] == (['301', '302'] if phase == 'initial' else []), 'unfinished work saved an image')
    return {'q03': True, 'q12': True, 'phase': phase, 'after': after,
            'saved_outputs': expected_outputs, 'input_method': 'Playwright click/keyboard; real Edge DOM'}


def closed_ok(result, owner):
    tree, lifecycle = result.get('process_tree') or {}, result.get('lifecycle') or {}
    protected = lifecycle.get('protected_before') or {}
    return bool(owner is not None and not result['errors'] and not result['page_errors']
                and not result['console_errors'] and not result['foreign_requests']
                and result.get('shutdown_acknowledged') is True and result.get('exit_code') == 0
                and tree.get('living') == [] and not tree.get('errors')
                and result.get('port_released') is True and lifecycle.get('pid') == owner['pid']
                and lifecycle.get('services_closed') is True and lifecycle.get('shutdown_requested') is True
                and lifecycle.get('external_denied') == 0
                and protected.get('kind') == KIND and set(protected.get('files', {})) == set(PROTECTED)
                and protected == lifecycle.get('protected_after'))


def run_round(browser, output, viewport, phase, expected_outputs):
    from tools import check_native_window as native
    width, height = viewport
    case, folder = output / str(width), output / str(width) / phase
    folder.mkdir(exist_ok=False)
    port, nonce = free_port(), secrets.token_hex(32)
    control = Control(port, nonce)
    write_new(folder / 'owner.json', {'kind': KIND, 'phase': phase, 'width': width, 'port': port,
                                    'nonce_sha256': hashlib.sha256(nonce.encode()).hexdigest()})
    result = {'started_jst': now_jst(), 'viewport': list(viewport), 'phase': phase,
              'errors': [], 'page_errors': [], 'console_errors': [], 'foreign_requests': [],
              'forced_cleanup': False, 'trace': [], 'passed': False, 'ui': None}
    process = tree = owner = context = None
    try:
        before = verify_fixture(case)
        command = [sys.executable, '-B', str(Path(__file__).resolve()), '--serve', '--output', str(output),
                   '--width', str(width), '--phase', phase, '--port', str(port), '--nonce', nonce]
        env = child_environment()
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        with (folder / 'server.log').open('xb') as log:
            process = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        tree = OwnedTree(process, native)
        wait_for_health(control.base, process, expected_data='pixiv-readonly')
        identity = control()
        require(identity.get('kind') == KIND, 'wrong fixture endpoint')
        owner = tree.observed_endpoint(identity['pid'])
        require(owner is not None, 'fixture PID is not in the owned process tree')
        context = browser.new_context(viewport={'width': width, 'height': height}, service_workers='block')
        def local_only(route):
            parsed = urlsplit(route.request.url)
            if (parsed.scheme, parsed.netloc) == ('http', f'127.0.0.1:{port}'):
                route.continue_()
            else:
                result['foreign_requests'].append('blocked')
                route.abort()
        context.route('**/*', local_only)
        def observe(page):
            page.on('pageerror', lambda error: result['page_errors'].append(type(error).__name__))
            page.on('console', lambda message: result['console_errors'].append('console-error')
                    if message.type == 'error' else None)
        context.on('page', observe)
        result['ui'] = inspect(context.new_page(), control, case, phase, folder,
                               expected_outputs, result['trace'])
        require(verify_fixture(case) == before, 'protected DB/image changed during UI actions')
    except Exception as exc:
        result['errors'].append(type(exc).__name__ + ': ' + str(exc)[:500])
    finally:
        if context is not None:
            try:
                context.close()
            except Exception as exc:
                result['errors'].append(type(exc).__name__ + ': browser_context_close')
        if tree is not None:
            try:
                if owner is None:
                    identity = control()
                    require(identity.get('kind') == KIND, 'wrong shutdown endpoint')
                    owner = tree.observed_endpoint(identity['pid'])
                require(owner is not None and tree.observed_endpoint(owner['pid']) == owner,
                        'shutdown ownership no longer proven')
                result['shutdown_acknowledged'] = control('shutdown') == {'stopping': True}
            except Exception as exc:
                result['errors'].append(type(exc).__name__ + ': shutdown_unconfirmed')
        if process is not None:
            try:
                result['exit_code'] = process.wait(timeout=35)
            except subprocess.TimeoutExpired:
                result['errors'].append('normal_exit_timeout_no_force')
        if tree is not None:
            result['process_tree'] = tree.finish()
        result['port_released'] = port_released(port)
        lifecycle = folder / 'server-lifecycle.json'
        if lifecycle.is_file():
            result['lifecycle'] = json.loads(lifecycle.read_text(encoding='utf-8'))
        result['passed'] = bool(result['ui']) and closed_ok(result, owner)
        result['finished_jst'] = now_jst()
        write_new(folder / 'result.json', result)
    return result


def run(output, *, headed=False):
    require(os.name == 'nt', 'this owned Edge checker requires Windows')
    output = admit_output(output)
    output.mkdir(exist_ok=False)
    before = acceptance_hashes()
    report = {'scope': ['Q03', 'Q12'], 'synthetic_only': True, 'real_pixiv_gets': 0,
              'channel': 'msedge', 'headed': headed, 'started_jst': now_jst(),
              'success': False, 'rounds': [], 'errors': [], 'source_before': before}
    try:
        from playwright.sync_api import sync_playwright
        network = deny_external_connections()
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=not headed,
                                         args=['--disable-background-networking'])
            try:
                for viewport in VIEWPORTS:
                    case = output / str(viewport[0])
                    case.mkdir()
                    build_fixture(case)
                    saved = None
                    for phase in PHASES:
                        result = run_round(browser, output, viewport, phase, saved)
                        report['rounds'].append(result)
                        require(result['passed'], 'Q03/Q12 round failed; no automatic repetition')
                        saved = result['ui']['saved_outputs']
                    require(saved_outputs(case) == saved, 'normal close changed saved bytes')
                    verify_fixture(case)
            finally:
                browser.close()
        require(network['denied'] == 0, 'external Python connection attempted')
        report['success'] = len(report['rounds']) == 4 and all(r['passed'] for r in report['rounds'])
    except Exception as exc:
        report['errors'].append(type(exc).__name__ + ': ' + str(exc)[:500])
    finally:
        report['source_after'] = acceptance_hashes()
        report['source_changed'] = before != report['source_after']
        report['success'] = report['success'] and not report['source_changed'] and not report['errors']
        report['finished_jst'] = now_jst()
        write_new(output / 'result.json', report)
    print(json.dumps({'success': report['success'], 'rounds': len(report['rounds']),
                      'errors': report['errors']}, ensure_ascii=True))
    return 0 if report['success'] else 1


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--output', type=Path, required=True)
    cli.add_argument('--headed', action='store_true')
    cli.add_argument('--serve', action='store_true')
    cli.add_argument('--width', type=int, choices=(1280, 1920))
    cli.add_argument('--phase', choices=PHASES)
    cli.add_argument('--port', type=int)
    cli.add_argument('--nonce')
    args = cli.parse_args(argv)
    if args.serve:
        if None in (args.width, args.phase, args.port, args.nonce):
            cli.error('internal serve requires the complete owned phase and nonce')
        return serve(args.output, args.width, args.phase, args.port, args.nonce)
    if any(value is not None for value in (args.width, args.phase, args.port, args.nonce)):
        cli.error('internal phase arguments require --serve')
    return run(args.output, headed=args.headed)


if __name__ == '__main__':
    raise SystemExit(main())
