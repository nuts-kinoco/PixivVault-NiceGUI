"""Recommendation image/viewer acceptance through the real product shell.

Only generated PNG/GIF/ZIP data and a synthetic Cookie are used. Actual
LivePixivReadClient, managed receipts, image registry, RecommendationView and
ViewerController are exercised. No real Pixiv GET or POST is permitted.
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
import re
import secrets
import subprocess
import sys
from threading import Event, RLock
import time
from types import SimpleNamespace
from urllib.parse import urlsplit
import urllib.request
from zipfile import ZIP_DEFLATED, ZipFile

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.run_browser_checks import (
    child_environment, free_port, port_released, source_hashes, wait_for_health)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
ARTIFACTS = ROOT / 'artifacts/p3-318-recommendation-visual'
WORKS = tuple(str(number) for number in range(11, 29))
MARKER = b'{"purpose":"generated-recommendation-visual-acceptance","version":1}\n'


def owned_path(path):
    from pixivvault_nicegui.managed_downloads import _safe_path
    path = Path(path).absolute()
    if path == ARTIFACTS or not path.is_relative_to(ARTIFACTS):
        raise ValueError('fresh recommendation artifact subdirectory required')
    _safe_path(path)
    return path


def image_url(wid, page=0, *, original=False):
    folder = 'img-original' if original else 'img-master'
    suffix = '' if original else '_master1200'
    extension = 'png' if original else 'jpg'
    return (f'https://i.pximg.net/{folder}/img/2026/01/01/00/00/00/'
            f'{wid}_p{page}{suffix}.{extension}')


def generated_png(wid, page, *, original=False):
    from PIL import Image
    output = BytesIO()
    Image.new('RGB', (160, 120) if original else (80, 60),
              (int(wid) * 7 % 255, page * 50 + 30, 110)).save(output, 'PNG')
    return output.getvalue()


class SyntheticHTTP:
    """An exhaustive, non-network transport; only safe generated observations."""
    def __init__(self):
        from PIL import Image
        self.lock = RLock()
        self.reads = []
        self.posts = []
        self.hold_next_detail = False
        self.detail_pending = False
        self.release_detail = Event()
        self.release_detail.set()
        archive = BytesIO()
        with ZipFile(archive, 'w', compression=ZIP_DEFLATED) as output:
            for index, color in enumerate(('red', 'green', 'blue')):
                raw = BytesIO()
                Image.new('RGB', (96, 64), color).save(raw, 'PNG')
                output.writestr(f'{index:06d}.png', raw.getvalue())
        self.archive = archive.getvalue()

    def get(self, request, timeout, limit, deadline):
        route = urlsplit(request.full_url)
        if request.get_method() != 'GET' or route.scheme != 'https':
            raise AssertionError('generated GET only')
        if route.netloc == 'www.pixiv.net':
            if route.path == '/ajax/user/extra':
                with self.lock:self.reads.append(dict(kind='identity', target='55'))
                body = dict(userId='55')
            elif route.path == '/':
                with self.lock:self.reads.append(dict(kind='csrf-page', target='55'))
                data = dict(props=dict(pageProps=dict(isLoggedIn=True, gaUserData=dict(id='55'),
                    serverSerializedPreloadedState=json.dumps(dict(api=dict(token='a' * 32))))))
                return ('<script id="__NEXT_DATA__" type="application/json">'
                        + json.dumps(data) + '</script>').encode()
            else:
                match = re.fullmatch(r'/ajax/illust/([1-9][0-9]*)(?:/(pages|ugoira_meta))?', route.path)
                if match is None or match[1] not in WORKS:
                    raise AssertionError('unexpected generated API path')
                wid, operation = match.groups()
                with self.lock:
                    self.reads.append(dict(kind=operation or 'detail', target=wid))
                    hold = wid == '14' and operation is None and self.hold_next_detail
                    if hold:
                        self.hold_next_detail = False
                        self.detail_pending = True
                if hold:
                    try:
                        if not self.release_detail.wait(max(0, min(20, deadline-time.monotonic()))):
                            raise TimeoutError('generated detail response held')
                    finally:
                        with self.lock:self.detail_pending = False
                if operation is None:
                    body = dict(id=wid, userId='91', userName='Generated author',
                        title='Generated ' + wid, description='Generated recommendation description',
                        urls=dict(small=image_url(wid)), pageCount=1 if wid == '13' else 3,
                        illustType=2 if wid == '13' else 1, xRestrict=0, aiType=0,
                        tags=['generated'], width=160, height=120,
                        createDate='2026-01-01T00:00:00Z',
                        updateDate='2026-01-01T00:00:00Z',
                        bookmarkCount=0, viewCount=0, bookmarkData=None)
                elif operation == 'pages':
                    body = [dict(urls=dict(regular=image_url(wid, page),
                        original=image_url(wid, page, original=True))) for page in range(3)]
                else:
                    if wid != '13':
                        raise AssertionError('unexpected generated animation')
                    body = dict(originalSrc='https://i.pximg.net/img-zip-ugoira/fixture/13_ugoira600x600.zip',
                        frames=[dict(file=f'{n:06d}.png', delay=delay)
                                for n, delay in enumerate((40, 90, 170))])
            return json.dumps(dict(error=False, body=body)).encode()
        if route.netloc == 'i.pximg.net':
            if route.path == '/img-zip-ugoira/fixture/13_ugoira600x600.zip':
                with self.lock:self.reads.append(dict(kind='archive', target='13'))
                return self.archive
            match = re.fullmatch(
                r'/(img-original|img-master)/img/2026/01/01/00/00/00/([1-9][0-9]*)_p([0-2])(?:_master1200)?\.(png|jpg)',
                route.path)
            if match is None or match[2] not in WORKS:
                raise AssertionError('unexpected generated image path')
            folder, wid, page, _ = match.groups()
            original = folder == 'img-original'
            with self.lock:
                self.reads.append(dict(kind='original-image' if original else 'regular-image',
                                       target=wid, page=int(page)))
            return generated_png(wid, int(page), original=original)
        raise AssertionError('unexpected generated HTTP destination')

    def post(self, *_args, **_kwargs):
        with self.lock:self.posts.append('unexpected')
        raise AssertionError('this image/viewer acceptance performs no Pixiv POST')

    def snapshot(self):
        with self.lock:
            return dict(reads=[dict(row) for row in self.reads],
                        posts=list(self.posts), detail_pending=self.detail_pending)


def serve(port, root, nonce, mode):
    from fastapi import Header, HTTPException
    from nicegui import app, ui
    from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
    from pixivvault_nicegui.duplicate_inspection import ManagedDuplicateInspection
    from pixivvault_nicegui.managed_downloads import ManagedPixivDownloads, _safe_path
    from pixivvault_nicegui.fs_paths import native_io_path
    from pixivvault_nicegui.media_routes import register_media_routes
    from pixivvault_nicegui.pixiv_read_client import LivePixivReadClient
    from pixivvault_nicegui.pixiv_write_client import PixivWriteClient
    from pixivvault_nicegui.recommendation_acquisition import encode
    from pixivvault_nicegui.recommendation_state import (
        RecommendationController, RecommendationGroup, RecommendationSnapshot,
        RecommendationWork, RecommendationPreferences)
    from pixivvault_nicegui.services import get_services
    from pixivvault_nicegui.ui.shell import build_demo_page
    from pixivvault_nicegui import pixiv_actions as actions_module
    if not 1024 <= port <= 65535 or port == 8089 or mode not in ('initial', 'restart'):
        raise ValueError('owned loopback port and explicit run required')
    external_attempts = []
    def guard(event, args):
        if event == 'socket.connect':
            address = args[1]
            host = address[0] if isinstance(address, tuple) else None
            if host not in ('127.0.0.1', '::1', 'localhost'):
                external_attempts.append('connect')
                raise RuntimeError('external fixture connection prohibited')
        elif event == 'socket.getaddrinfo' and args[0] not in ('127.0.0.1', '::1', 'localhost', None):
            external_attempts.append('dns')
            raise RuntimeError('external fixture DNS prohibited')
    sys.addaudithook(guard)
    root = owned_path(root)
    root.mkdir(parents=True, exist_ok=True)
    if mode == 'initial':
        if any(root.iterdir()):raise ValueError('fresh fixture required')
        with (root / '.recommend-fixture.json').open('xb') as file:file.write(MARKER)
        with (root / 'generated-cookie.txt').open('x', encoding='utf-8') as file:
            file.write('# Netscape HTTP Cookie File\n.pixiv.net\tTRUE\t/\tTRUE\t'
                + str(int(time.time())+86400) + '\tPHPSESSID\t55_fixture_' + secrets.token_hex(16) + '\n')

        class SeedReader:
            def verify_identity(self):return '55'
            def get_artwork_download_record(self, wid):
                if wid != '11':raise ValueError('one generated saved work only')
                return RemoteBookmark(wid, 'illustration', 'show', 'Generated saved 11',
                    '91', 'Generated author', ('generated',), '', '2026-01-01T00:00:00Z', 3, 1, 0)
            def get_artwork_original_pages(self, wid):
                self.get_artwork_download_record(wid)
                return tuple(image_url(wid, page, original=True) for page in range(3))
            def get_image_bytes(self, url):
                pages = self.get_artwork_original_pages('11')
                if url not in pages:raise ValueError('generated seed page only')
                return generated_png('11', pages.index(url), original=True)
        seed_reader = SeedReader()
        seed = ManagedPixivDownloads(root / 'managed', seed_reader,
                                     resolve_record=seed_reader.get_artwork_download_record)
        dedup = None
        try:
            seed.run(SimpleNamespace(kind='pixiv-illust', target_id='11'),
                     SimpleNamespace(checkpoint=lambda: None, progress=lambda *_: None))
            dedup = ManagedDuplicateInspection(seed)
            scan = dedup.scan()
            selections = {g.group_id: {p.key for p in g.items if p.key == '11:2'} for g in scan.groups}
            plan = dedup.plan(scan, selections)
            result = dedup.execute(plan, operation_id='8' * 32)
            if tuple(p.key for p in result.committed) != ('11:2',):
                raise AssertionError('generated partial saved-page fixture failed')
        finally:
            if dedup is not None:dedup.close()
            seed.close()
    elif (root / '.recommend-fixture.json').read_bytes() != MARKER:
        raise ValueError('owned restart fixture marker required')
    cookie = root / 'generated-cookie.txt'
    cookie_digest = hashlib.sha256(cookie.read_bytes()).hexdigest()
    http = SyntheticHTTP()
    reader = LivePixivReadClient(cookie, transport=http.get)
    actual_actions = actions_module.PixivActions
    def factory(owner, *, following_store=None):
        if owner is not reader:raise AssertionError('reader ownership changed')
        return actual_actions(owner, writer=PixivWriteClient(owner, transport=http.post),
                              following_store=following_store)
    actions_module.PixivActions = factory
    try:
        services = get_services(app, live_client=reader, pixiv_download_root=root / 'managed',
            preferences_root=root / 'preferences', pixiv_actions_mode='enabled')
    finally:
        actions_module.PixivActions = actual_actions
    works = tuple(RecommendationWork(wid, 'Generated ' + wid, '91', 'Generated author',
        image_url(wid), None if wid == '14' else (1 if wid == '13' else 3),
        None if wid == '14' else (2 if wid == '13' else 1), ('generated',),
        '2026-01-01T00:00:00Z', 0) for wid in WORKS)
    group = RecommendationGroup('superlike', 'Generated recommendation images', works)
    snapshot = RecommendationSnapshot(sections=(('superlike', (group,)),), unavailable=())
    preferences = RecommendationPreferences(root / 'recommendations')
    revision, _, settings = preferences.snapshot()
    if 'recommend_acquisition_state' not in settings:
        if mode != 'initial':raise AssertionError('recommendation cache missing on restart')
        preferences.store_acquisition(dict(cache=dict(superlike=[encode(group)]), boosts={}, added=[]), revision)
    previous = services.recommendations
    services.recommendations = RecommendationController(SimpleNamespace(load=lambda: snapshot), preferences)
    if previous is not None:previous.close()
    if http.reads or http.posts:raise AssertionError('constructor performed HTTP')
    writer = services.managed_downloads
    def proof_map():
        result = {}
        root_io = native_io_path(writer.root)
        for folder in (writer.outputs, writer.generation_outputs, writer.root / 'quarantine'):
            for path in sorted(native_io_path(folder).rglob('*')):
                if path.is_file():
                    _safe_path(path, file=True)
                    result[path.relative_to(root_io).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        return result
    baseline = proof_map()
    clients = {}
    quality_events = []
    open_events = []
    from pixivvault_nicegui.ui.viewer_controller import ViewerController
    from pixivvault_nicegui.ui.recommend_view import RecommendView
    original_quality = ViewerController._quality_clicked
    def trace_quality(viewer, event):
        result = original_quality(viewer, event)
        quality_events.append(dict(accepted=bool(result)))
        return result
    ViewerController._quality_clicked = trace_quality
    original_open = RecommendView.open_work
    async def trace_open(view, work_id, *, generation):
        try:
            return await original_open(view, work_id, generation=generation)
        finally:
            selected = view._recommend_viewer._viewer_state.selected_artwork
            open_events.append(dict(requested=work_id,
                                    selected=selected.work_id if selected else None))
    RecommendView.open_work = trace_open
    def auth(value):
        if not secrets.compare_digest(value or '', nonce):raise HTTPException(403)
    @app.get('/healthz')
    def health():return dict(status='ok', mode='demo', data='synthetic')
    @app.get('/_rv/state')
    async def state(x_acceptance: str | None = Header(default=None)):
        auth(x_acceptance)
        views = {}
        for key, shell in tuple(clients.items()):
            view = shell.recommend_view
            viewer = view._recommend_viewer if view is not None else None
            art = viewer._viewer_state.selected_artwork if viewer is not None else None
            identity = None
            if art is not None and viewer.selection is not None:
                identity = viewer.selection.images.source_page_identity(
                    art.work_id, viewer._viewer_state.selected_page)
            views[key] = dict(tab=shell.active_tab, closed=shell.closed,
                work=art.work_id if art else None,
                page=viewer._viewer_state.selected_page if art else None,
                source_identity=asdict(identity) if identity is not None else None,
                opening=list(view._opening) if view is not None and view._opening is not None else None)
        actions = services.pixiv_actions
        return dict(mode=mode, http=http.snapshot(), views=views,
            original_unchanged=proof_map()==baseline,
            cookie_unchanged=hashlib.sha256(cookie.read_bytes()).hexdigest()==cookie_digest,
            reader_closed=reader._closed, active_count=actions.active_count,
            shutdown_unverified=actions.shutdown_unverified, pins=len(services.images._versions),
            static_jobs=len(services.static_jobs.snapshot()), quality_events=list(quality_events),
            open_events=list(open_events), external_server_attempts=list(external_attempts))
    @app.post('/_rv/inject')
    def inject(values: dict, x_acceptance: str | None = Header(default=None)):
        auth(x_acceptance)
        if values not in ({'hold_detail': True}, {'release_detail': True}):raise HTTPException(400)
        with http.lock:
            if values.get('hold_detail'):
                http.hold_next_detail = True
                http.release_detail.clear()
            else:http.release_detail.set()
        return dict(ok=True)
    @app.post('/_rv/shutdown')
    def shutdown(x_acceptance: str | None = Header(default=None)):
        auth(x_acceptance)
        http.release_detail.set()
        app.shutdown()
        return dict(stopping=True)
    async def close():
        actions = services.pixiv_actions
        await asyncio.to_thread(services.close)
        final = dict(reader_closed=reader._closed, active_count=actions.active_count,
            shutdown_unverified=actions.shutdown_unverified,
            original_unchanged=proof_map()==baseline,
            cookie_unchanged=hashlib.sha256(cookie.read_bytes()).hexdigest()==cookie_digest,
            external_server_attempts=list(external_attempts), http=http.snapshot())
        with (root / f'shutdown-{mode}.json').open('x', encoding='utf-8') as file:
            json.dump(final, file, ensure_ascii=False, indent=2)
    app.on_shutdown(close)
    def product_page():
        client = ui.context.client
        ui.element('span').props('data-testid=fixture-client data-client-id='+client.id).style('display:none')
        build_demo_page()
        clients[client.id] = client.pixivvault_shell
        client.on_delete(lambda: clients.pop(client.id, None))
    register_media_routes(app, services.images)
    ui.page('/')(product_page)
    ui.run(host='127.0.0.1', port=port, show=False, reload=False,
           binding_refresh_interval=None, timeout_graceful_shutdown=10)


def inspect(pages, url, headers, output, *, mode):
    from PIL import Image
    from playwright.sync_api import expect
    expect.set_options(timeout=20000)
    page, observer = pages
    checks = []
    phase = 'constructor'
    def mark(value):
        nonlocal phase
        phase = value
        with (output / 'progress.jsonl').open('a', encoding='utf-8') as file:
            file.write(json.dumps(dict(phase=phase, time=time.time())) + '\n')
    def state():
        response = observer.request.get(url + '/_rv/state', headers=headers)
        assert response.ok, f'{phase}: state HTTP {response.status}'
        return response.json()
    def inject(**values):
        response = observer.request.post(url + '/_rv/inject', headers=headers, data=values)
        assert response.ok, f'{phase}: fixture control HTTP {response.status}'
    def eventually(predicate, label, timeout=20):
        deadline = time.monotonic()+timeout
        while time.monotonic()<deadline:
            current = state()
            if predicate(current):return current
            observer.wait_for_timeout(80)
        raise AssertionError(phase + ': ' + label)
    def tab(name):
        previous = None
        if name == 'recommend':
            prior = page.get_by_test_id('recommend-thumb-superlike-12')
            if prior.count():
                previous = prior.get_attribute('id')
        button = page.get_by_test_id('tab-'+name)
        button.click()
        expect(button).to_have_attribute('aria-current', 'page')
        if name == 'recommend':
            # set_active schedules a cache refresh. The tab's selected marker
            # can arrive before that refresh replaces its previous card tree.
            page.wait_for_function("""old => {
                const root=document.querySelector('[data-testid="recommend-view"]');
                const node=document.querySelector('[data-testid="recommend-thumb-superlike-12"]');
                return root && root.dataset.busy==='false' && node && node.isConnected &&
                    (!old || node.id!==old);
            }""",arg=previous)
    def card(wid):
        return page.get_by_test_id('recommend-open-superlike-'+wid)
    def thumb(wid):
        return page.get_by_test_id('recommend-thumb-superlike-'+wid)
    def decoded_thumbnail(wid):
        node = thumb(wid)
        node.scroll_into_view_if_needed()
        page.wait_for_function("""selector => {
            const e=document.querySelector(selector);
            return e && [...e.querySelectorAll('img')].some(i=>i.complete && i.naturalWidth>0 &&
                getComputedStyle(i).visibility!=='hidden' && Number(getComputedStyle(i).opacity)>0);
        }""", arg='[data-testid="recommend-thumb-superlike-'+wid+'"]')
    def geometry(wid):
        return thumb(wid).evaluate("""e => {
            const i=[...e.querySelectorAll('img')].find(i=>i.complete && i.naturalWidth>0 &&
                Number(getComputedStyle(i).opacity)>0);
            if(!i) return null;
            const r=i.getBoundingClientRect(), fit=getComputedStyle(i).objectFit;
            const scale=Math.min(r.width/i.naturalWidth,r.height/i.naturalHeight);
            return {x:r.x,y:r.y,w:r.width,h:r.height,natural:[i.naturalWidth,i.naturalHeight],
                fit,draw:{x:r.x+(r.width-i.naturalWidth*scale)/2,
                    y:r.y+(r.height-i.naturalHeight*scale)/2,
                    w:i.naturalWidth*scale,h:i.naturalHeight*scale}};
        }""")
    def ready(wid, number=0, quality='view'):
        viewer = page.get_by_test_id('recommend-viewer')
        expect(viewer).to_have_attribute('aria-hidden','false')
        expect(viewer).to_have_attribute('data-quality-work',wid)
        expect(viewer).to_have_attribute('data-quality-page',str(number))
        expect(viewer).to_have_attribute('data-viewer-quality',quality)
        expect(viewer).to_have_attribute('data-zoom-ready','true')
        image = viewer.get_by_test_id('recommend-viewer-image').locator('img[data-pv-displayed="true"]')
        expect(image).to_have_count(1)
        assert image.evaluate('(i)=>i.complete && i.naturalWidth>0 && i.naturalHeight>0')
        return viewer, image
    def close_viewer(wid):
        page.get_by_test_id('recommend-viewer').get_by_role('button',name='閉じる',exact=True).click()
        expect(page.get_by_test_id('recommend-viewer')).to_have_attribute('aria-hidden','true')
        expect(card(wid)).to_be_focused()

    constructed = state()
    assert constructed['http']['reads'] == constructed['http']['posts'] == []
    assert constructed['static_jobs']==0 and constructed['original_unchanged']
    mark('mount-visible-thumbnails')
    page.bring_to_front()  # Only the operation Page created by this checker.
    page.goto(url, wait_until='domcontentloaded')
    tab('recommend')
    client_id=page.get_by_test_id('fixture-client').get_attribute('data-client-id')
    assert client_id
    for wid in WORKS[:6]:decoded_thumbnail(wid)
    expect(page.locator('[data-testid^="recommend-open-superlike-"]')).to_have_count(6)
    mounted = state()
    assert all(row['kind']=='regular-image' for row in mounted['http']['reads'])
    assert {row['target'] for row in mounted['http']['reads']}<=set(WORKS[:6])-{'11'}
    assert mounted['http']['posts']==[]
    expected_size='small' if mode=='restart' else 'large'
    expect(page.get_by_test_id('recommend-size-'+expected_size)).to_have_attribute('aria-pressed','true')
    checks.append('real DemoShell mount restores cached previews with visible-only images and no work/page metadata GET')

    mark('actual-card-image-sizes')
    sizes={}
    before_sizes=state()['http']['reads']
    for size in ('large','medium','small'):
        previous_id=thumb('12').get_attribute('id')
        page.get_by_test_id('recommend-size-'+size).click()
        expect(page.get_by_test_id('recommend-size-'+size)).to_have_attribute('aria-pressed','true')
        # The selected toolbar state can arrive before the old shelf body is
        # replaced. Observe the new actual card; do not scroll a retiring node.
        page.wait_for_function("""old => {
            const node=document.querySelector('[data-testid="recommend-thumb-superlike-12"]');
            return node && node.isConnected && node.id && node.id!==old;
        }""",arg=previous_id)
        decoded_thumbnail('12')
        sizes[size]=geometry('12')
        assert sizes[size] and sizes[size]['draw']['w']>0 and sizes[size]['draw']['h']>0
        page.screenshot(path=str(output / ('cards-'+size+'.png')),animations='disabled')
    assert sizes['large']['draw']['w']>=sizes['medium']['draw']['w']>=sizes['small']['draw']['w']
    if page.viewport_size['width']>=1900:
        assert sizes['large']['draw']['w']>sizes['medium']['draw']['w']>sizes['small']['draw']['w']
    assert state()['http']['reads']==before_sizes
    checks.append('large/medium/small controls change the actual decoded image area; unchanged catalogs add no HTTP')

    mark('shelf-expand-fold')
    for count in (12,18):
        page.get_by_test_id('recommend-expand').click()
        expect(page.locator('[data-testid^="recommend-open-superlike-"]')).to_have_count(count)
        for wid in WORKS[:count]:decoded_thumbnail(wid)
        observed=state()['http']['reads']
        assert all(row['kind']=='regular-image' for row in observed)
        assert {row['target'] for row in observed}<=set(WORKS[:count])-{'11'}
    page.get_by_test_id('recommend-collapse').click()
    expect(page.locator('[data-testid^="recommend-open-superlike-"]')).to_have_count(6)
    for wid in WORKS[:6]:decoded_thumbnail(wid)
    checks.append('6→12→18→6 uses actual image cards; expansion introduces no detail/page request')

    mark('saved-pinned-page-map')
    before_saved=state()['http']['reads']
    card('11').focus()
    card('11').press('Enter')
    saved,_=ready('11')
    expect(saved.locator('.pv-counter')).to_have_text('1/2')
    saved.get_by_role('button',name='次のページ',exact=True).click()
    saved,saved_image=ready('11',1)
    mapping=state()['views'][client_id]['source_identity']
    assert mapping['source_page_no']==2 and mapping['source_page_count']==3
    saved.get_by_test_id('viewer-quality-toggle').click()
    saved,saved_image=ready('11',1,'original')
    rgb=Image.open(BytesIO(saved_image.screenshot(animations='disabled'))).convert('RGB')
    pixel=rgb.getpixel((rgb.width//2,rgb.height//2))
    assert all(abs(a-b)<=4 for a,b in zip(pixel,(77,130,110)))
    page.screenshot(path=str(output / 'saved-visible-page2-original-source-page3.png'),animations='disabled')
    assert state()['http']['reads']==before_saved and state()['original_unchanged']
    close_viewer('11')
    checks.append('saved recommendation retains the verified source page mapping and bytes, with no remote image fallback')

    mark('unsaved-pages-original-zoom-focus')
    card('12').focus()
    card('12').press('Enter')
    viewer,image=ready('12')
    expect(viewer.locator('.pv-counter')).to_have_text('1/3')
    expect(viewer.get_by_test_id('viewer-action-bookmark')).to_be_enabled()
    expect(viewer.get_by_test_id('viewer-action-follow')).to_be_enabled()
    assert viewer.get_by_test_id('viewer-action-bookmark').get_attribute('data-action-token')
    assert image.evaluate('(i)=>i.naturalWidth')==80
    before_zoom=state()['http']['reads']
    viewer.get_by_test_id('viewer-zoom-in').click()
    viewer.get_by_test_id('viewer-zoom-in').click()
    viewport=viewer.get_by_test_id('viewer-zoom-viewport')
    expect(viewport).to_have_attribute('data-zoom-scale','1.5625')
    rect=viewport.bounding_box()
    assert rect
    x,y=rect['x']+rect['width']/2,rect['y']+rect['height']/2
    page.mouse.move(x,y)
    page.mouse.down()
    page.mouse.move(x+20,y+30,steps=5)
    page.mouse.up()
    assert abs(float(viewport.get_attribute('data-zoom-y')))>0
    assert state()['http']['reads']==before_zoom
    viewer.get_by_role('button',name='次のページ',exact=True).click()
    viewer,image=ready('12',1)
    expect(viewport).to_have_attribute('data-zoom-scale','1')
    viewer.get_by_test_id('viewer-quality-toggle').click()
    viewer,image=ready('12',1,'original')
    assert image.evaluate('(i)=>i.naturalWidth')==160
    stale=viewer.evaluate("""e=>({serial:e.dataset.qualitySerial,work:e.dataset.qualityWork,page:e.dataset.qualityPage})""")
    originals=[row for row in state()['http']['reads'] if row['kind']=='original-image']
    assert originals==[dict(kind='original-image',target='12',page=1)]
    page.screenshot(path=str(output / 'unsaved-original-page2.png'),animations='disabled')
    viewer.get_by_role('button',name='次のページ',exact=True).click()
    viewer,image=ready('12',2)
    assert image.evaluate('(i)=>i.naturalWidth')==80
    sequence=len(state()['quality_events'])
    viewer.evaluate("""(e,old)=>{
        const saved={serial:e.dataset.qualitySerial,work:e.dataset.qualityWork,page:e.dataset.qualityPage};
        e.dataset.qualitySerial=old.serial;e.dataset.qualityWork=old.work;e.dataset.qualityPage=old.page;
        e.querySelector('[data-testid="viewer-quality-toggle"]').click();
        e.dataset.qualitySerial=saved.serial;e.dataset.qualityWork=saved.work;e.dataset.qualityPage=saved.page;
    }""", stale)
    rejected=eventually(lambda s:len(s['quality_events'])>sequence,'stale quality click not observed')
    assert rejected['quality_events'][-1]['accepted'] is False
    assert [row for row in rejected['http']['reads'] if row['kind']=='original-image']==originals
    close_viewer('12')
    checks.append('unsaved thumbnail/Enter opens all pages; original is selected-page-only; zoom/pan/focus and stale quality fences are retained')

    mark('real-animated-pixels')
    card('13').focus()
    card('13').press('Enter')
    viewer,image=ready('13')
    expect(viewer.get_by_test_id('viewer-quality-toggle')).to_be_disabled()
    assert image.evaluate('(i)=>[i.naturalWidth,i.naturalHeight]')==[96,64]
    colors={}
    samples=[]
    pixel_record=dict(scope='full recommendation actual GIF pixel gate',
                      colors=colors,samples=samples,screenshot_timeout_ms=5000,
                      sample_window_ms=3000,requested_wait_ms=40)
    def animation_document():
        return page.evaluate("""() => ({
            visibilityState:document.visibilityState,hidden:document.hidden,
            hasFocus:document.hasFocus(),time:performance.now(),
            viewport:[innerWidth,innerHeight],dpr:devicePixelRatio})""")
    def flush_animation_samples():
        # Fresh run output only. Preserve partial observations if a later
        # screenshot, assertion, or unrelated UI operation fails.
        with (output / 'animation-samples.json').open('w',encoding='utf-8') as file:
            json.dump(pixel_record,file,ensure_ascii=False,indent=2)
            file.flush()
    pixel_record['before_front']=animation_document()
    flush_animation_samples()
    animation_handle=None
    started=time.monotonic()
    try:
        # The state observer is a different, newly owned Page. Foreground the
        # operation Page explicitly; document.visibilityState alone was not
        # sufficient to establish stable screenshot sampling in diagnostic r1.
        page.bring_to_front()
        pixel_record['after_front']=animation_document()
        animation_handle=image.element_handle()
        assert animation_handle is not None
        source=animation_handle.evaluate('(i)=>i.currentSrc || i.src')
        pixel_record['source_uri_sha256']=hashlib.sha256(source.encode()).hexdigest()
        pixel_record['node']=animation_handle.evaluate("""i => {
            const r=i.getBoundingClientRect();
            return {id:i.id || null,componentId:i.parentElement?.id || null,
                natural:[i.naturalWidth,i.naturalHeight],
                rect:{x:r.x,y:r.y,width:r.width,height:r.height}};
        }""")
        started=time.monotonic()
        deadline=started+3
        flush_animation_samples()
        while time.monotonic()<deadline and len(colors)<3:
            entry=dict(index=len(samples),started_ms=(time.monotonic()-started)*1000)
            samples.append(entry)
            flush_animation_samples()
            assert image.evaluate('(current,pinned)=>current===pinned',animation_handle)
            assert animation_handle.evaluate(
                '(i)=>i.isConnected && i.getAttribute("data-pv-displayed")==="true"')
            capture_started=time.monotonic()
            raw=animation_handle.screenshot(animations='allow',timeout=5000)
            entry['capture_ms']=(time.monotonic()-capture_started)*1000
            name=f'animation-sample-{entry["index"]:03d}.png'
            with (output / name).open('xb') as file:
                file.write(raw)
            with Image.open(BytesIO(raw)) as screenshot:
                rgb=screenshot.convert('RGB')
                pixel=rgb.getpixel((rgb.width//2,rgb.height//2))
                entry['capture_size']=list(rgb.size)
            channel=max(range(3),key=lambda n:pixel[n])
            label=('red','green','blue')[channel]
            entry.update(time=time.monotonic(),rgb=list(pixel),file=name,
                         sha256=hashlib.sha256(raw).hexdigest(),document=animation_document())
            if pixel[channel]>70 and all(pixel[n]<20 for n in range(3) if n!=channel):
                entry['color']=label
                if label not in colors:
                    name='animation-'+label+'.png'
                    with (output / name).open('xb') as file:
                        file.write(raw)
                    colors[label]=dict(file=name,sha256=entry['sha256'],rgb=list(pixel))
            flush_animation_samples()
            observer.wait_for_timeout(40)
        pixel_record['source_unchanged']=animation_handle.evaluate('(i)=>i.currentSrc || i.src')==source
        pixel_record['same_displayed_node']=image.evaluate(
            '(current,pinned)=>current===pinned',animation_handle)
        pixel_record['three_colors']=set(colors)=={'red','green','blue'}
        assert pixel_record['source_unchanged'] and pixel_record['same_displayed_node']
        assert pixel_record['three_colors'], 'three actual GIF frame colors were not painted'
    except Exception as error:
        pixel_record['failure']=type(error).__name__+': '+str(error)
        raise
    finally:
        pixel_record['elapsed_ms']=(time.monotonic()-started)*1000
        flush_animation_samples()
    page.screenshot(path=str(output / 'recommended-ugoira.png'),animations='allow')
    gif_http=state()['http']['reads']
    assert any(row['kind']=='ugoira_meta' and row['target']=='13' for row in gif_http)
    assert any(row['kind']=='archive' and row['target']=='13' for row in gif_http)
    assert not any(row['target']=='13' and row['kind'] in ('pages','original-image') for row in gif_http)
    close_viewer('13')
    checks.append('recommended ugoira is an actual three-frame animated image, not the cover; original-only controls remain unavailable')

    mark('delayed-other-work')
    inject(hold_detail=True)
    card('14').click()
    eventually(lambda s:s['http']['detail_pending'],'explicit unknown-work metadata GET did not start')
    card('12').focus()
    card('12').press('Enter')
    ready('12')
    sequence=len(state()['open_events'])
    inject(release_detail=True)
    drained=eventually(lambda s:not s['http']['detail_pending'] and len(s['open_events'])>sequence,
                       'old metadata request did not drain')
    assert drained['views'][client_id]['work']=='12'
    assert drained['open_events'][-1]==dict(requested='14',selected='12')
    close_viewer('12')
    mark('delayed-tab-leave')
    inject(hold_detail=True)
    card('14').click()
    eventually(lambda s:s['http']['detail_pending'],'second explicit delayed GET did not start')
    sequence=len(state()['open_events'])
    tab('ranking')
    inject(release_detail=True)
    drained=eventually(lambda s:not s['http']['detail_pending'] and len(s['open_events'])>sequence,
                       'inactive recommendation request did not drain')
    assert drained['views'][client_id]['work'] is None
    assert drained['views'][client_id]['tab']=='ranking'
    tab('recommend')
    for wid in WORKS[:6]:decoded_thumbnail(wid)
    expect(page.get_by_test_id('recommend-size-small')).to_have_attribute('aria-pressed','true')
    assert state()['views'][client_id]['work'] is None
    card('12').scroll_into_view_if_needed()
    page.screenshot(path=str(output / 'final-shelf-small.png'),animations='disabled')
    checks.append('pending work metadata cannot replace another work or reopen a viewer after a real tab change')

    final=state()
    assert final['original_unchanged'] and final['cookie_unchanged']
    assert not final['reader_closed'] and final['static_jobs']==0
    assert final['http']['posts']==[] and final['active_count']==0
    assert not any(row['kind']=='pages' and row['target']=='14' for row in final['http']['reads'])
    assert final['external_server_attempts']==[]
    return dict(checks=checks,constructed=constructed,mounted=mounted,final=final,
        sizes=sizes,animation_frames=colors,animation_samples=samples,
        animation_sample_evidence='animation-samples.json',source_page_identity=mapping,
        viewport=page.evaluate('({width:innerWidth,height:innerHeight,dpr:devicePixelRatio})'),
        mode=mode,page_objects=2)


def inspect_gif_diagnostic(pages, url, headers, output, *, mode):
    """Observe the failing GIF gate before/after foregrounding the same page.

    This is one diagnostic, not recommendation acceptance. No shelf expansion,
    size cycling, saved-page/original/zoom test, or additional GIF open is run.
    """
    import base64
    from PIL import Image
    from playwright.sync_api import expect

    expect.set_options(timeout=20000)
    page, observer = pages
    report = dict(diagnostic_only=True, mode=mode, phase='created',
                  full_acceptance=False, expected_frames=3,
                  expected_delays_ms=[40, 90, 170], expected_loop=0,
                  samples={}, errors=[])
    image = handle = cdp = None
    initial_src = None

    def save():
        # This file belongs to this fresh run. Flush after each actual sample
        # so a later assertion/timeout cannot erase the diagnostic observations.
        with (output / 'gif-diagnostic.json').open('w', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.flush()

    def documents():
        result = {}
        for name, candidate in (('operation', page), ('observer', observer)):
            try:
                result[name] = candidate.evaluate("""() => ({
                    visibilityState: document.visibilityState, hidden: document.hidden,
                    hasFocus: document.hasFocus(), time: performance.now(),
                    viewport: [innerWidth,innerHeight], dpr:devicePixelRatio,
                    activeElement: {tag:document.activeElement?.tagName || null,
                                    id:document.activeElement?.id || null}
                })""")
            except Exception as error:
                result[name] = {'error': type(error).__name__}
        return result

    def displayed():
        if handle is None:
            return None
        value = handle.evaluate("""i => {
            const r=i.getBoundingClientRect(), s=getComputedStyle(i);
            const component=i.closest('[data-testid="recommend-viewer-image"]');
            const root=i.closest('[data-testid="recommend-viewer"]');
            return {id:i.id || null,tag:i.tagName,connected:i.isConnected,
                displayed:i.getAttribute('data-pv-displayed'),ariaHidden:i.getAttribute('aria-hidden'),
                componentId:component?.id || null,viewerId:root?.id || null,
                rect:{x:r.x,y:r.y,width:r.width,height:r.height},
                viewportIntersection:{width:Math.max(0,Math.min(r.right,innerWidth)-Math.max(r.left,0)),
                    height:Math.max(0,Math.min(r.bottom,innerHeight)-Math.max(r.top,0))},
                natural:[i.naturalWidth,i.naturalHeight],complete:i.complete,
                styles:{display:s.display,visibility:s.visibility,opacity:s.opacity,
                    objectFit:s.objectFit,objectPosition:s.objectPosition,transform:s.transform},
                viewer:{work:root?.dataset.qualityWork,page:root?.dataset.qualityPage,
                    quality:root?.dataset.viewerQuality,zoomReady:root?.dataset.zoomReady},
                visibilityState:document.visibilityState,hidden:document.hidden,
                hasFocus:document.hasFocus(),time:performance.now()};
        }""")
        value['still_selected_node'] = image.evaluate('(current,pinned)=>current===pinned', handle)
        return value

    def state():
        response = observer.request.get(url + '/_rv/state', headers=headers, timeout=5000)
        assert response.ok, f'GIF diagnostic state HTTP {response.status}'
        return response.json()

    def sample(phase):
        record = dict(before=documents(), displayed_before=displayed(),
                      samples=[], colors={}, seconds=3, requested_wait_ms=40)
        report['samples'][phase] = record
        report['phase'] = phase
        save()
        started = time.monotonic()
        deadline = started + 3
        while time.monotonic() < deadline and len(record['colors']) < 3:
            entry = dict(index=len(record['samples']), started_s=time.monotonic()-started)
            record['samples'].append(entry)
            try:
                entry['before'] = displayed()
                capture_started = time.monotonic()
                # Same screenshot mode as r2, pinned to the mounted node;
                # no GIF source reset, CSS disabling, or canvas surrogate.
                raw = handle.screenshot(animations='allow', timeout=5000)
                entry['capture_ms'] = (time.monotonic()-capture_started)*1000
                filename = f'gif-{phase}-{entry["index"]:03d}.png'
                with (output / filename).open('xb') as stream:
                    stream.write(raw)
                with Image.open(BytesIO(raw)) as captured:
                    rgb = captured.convert('RGB')
                    pixel = rgb.getpixel((rgb.width//2, rgb.height//2))
                    entry['capture_size'] = list(rgb.size)
                channel = max(range(3), key=lambda number: pixel[number])
                color = ('red','green','blue')[channel]
                accepted = pixel[channel]>70 and all(
                    pixel[number]<20 for number in range(3) if number!=channel)
                entry.update(file=filename,sha256=hashlib.sha256(raw).hexdigest(),
                             rgb=list(pixel),color=color if accepted else None,
                             finished_s=time.monotonic()-started,after=displayed())
                if accepted:
                    record['colors'].setdefault(color, dict(
                        file=filename,sha256=entry['sha256'],rgb=list(pixel)))
            except Exception as error:
                entry['error'] = type(error).__name__ + ': ' + str(error)
                report['errors'].append(phase + ': screenshot_or_node_observation_failed')
                save()
                break
            save()
            observer.wait_for_timeout(40)
        record['elapsed_ms'] = (time.monotonic()-started)*1000
        record['after'] = documents()
        record['three_colors'] = set(record['colors']) == {'red','green','blue'}
        save()
        return record

    try:
        assert mode == 'initial'
        report['documents_created'] = documents()
        report['constructed'] = state()
        assert report['constructed']['http']['reads'] == report['constructed']['http']['posts'] == []
        assert report['constructed']['static_jobs'] == 0
        page.goto(url, wait_until='domcontentloaded')
        button = page.get_by_test_id('tab-recommend')
        button.click()
        expect(button).to_have_attribute('aria-current','page')
        report['phase'] = 'open-generated-animation'
        report['documents_before_open'] = documents()
        save()
        card = page.get_by_test_id('recommend-open-superlike-13')
        expect(card).to_be_visible()
        card.focus()
        card.press('Enter')
        viewer = page.get_by_test_id('recommend-viewer')
        expect(viewer).to_have_attribute('aria-hidden','false')
        expect(viewer).to_have_attribute('data-quality-work','13')
        expect(viewer).to_have_attribute('data-quality-page','0')
        expect(viewer).to_have_attribute('data-viewer-quality','view')
        expect(viewer).to_have_attribute('data-zoom-ready','true')
        expect(viewer.get_by_test_id('viewer-quality-toggle')).to_be_disabled()
        image = viewer.get_by_test_id('recommend-viewer-image').locator('img[data-pv-displayed="true"]')
        expect(image).to_have_count(1)
        handle = image.element_handle()
        assert handle is not None
        report['displayed_at_ready'] = displayed()
        report['documents_at_ready'] = documents()
        initial_src = handle.evaluate('(i)=>i.currentSrc || i.src')
        report['source_kind'] = initial_src.split(',',1)[0]
        try:
            assert initial_src.startswith('data:image/gif;base64,')
            assert len(initial_src) < 2_000_000, 'generated diagnostic GIF unexpectedly large'
            raw_gif = base64.b64decode(initial_src.split(',',1)[1], validate=True)
            with (output / 'displayed-animation.gif').open('xb') as stream:
                stream.write(raw_gif)
            info = dict(file='displayed-animation.gif',bytes=len(raw_gif),
                        sha256=hashlib.sha256(raw_gif).hexdigest(),frames=[])
            report['gif'] = info
            with Image.open(BytesIO(raw_gif), formats=('GIF',)) as decoded:
                info.update(format=decoded.format,size=list(decoded.size),
                            loop=decoded.info.get('loop'),frame_count=decoded.n_frames)
                assert decoded.n_frames <= 16, 'generated GIF frame count exceeded diagnostic bound'
                for number in range(decoded.n_frames):
                    decoded.seek(number)
                    decoded.load()
                    frame = decoded.convert('RGB')
                    info['frames'].append(dict(index=number,
                        delay_ms=decoded.info.get('duration',0),
                        disposal=getattr(decoded,'disposal_method',None),
                        center_rgb=list(frame.getpixel((frame.width//2,frame.height//2)))))
            info['matches_generated_contract'] = (
                info['loop']==0 and info['frame_count']==3 and info['size']==[96,64]
                and [frame['delay_ms'] for frame in info['frames']]==[40,90,170])
        except Exception as error:
            report['gif_read_error'] = type(error).__name__ + ': ' + str(error)
            report['errors'].append('actual_displayed_gif_not_verified')
        try:
            # Read a backend identity without adding/changing any product DOM
            # attribute. The pinned ElementHandle also detects replacement.
            cdp = page.context.new_cdp_session(page)
            root = cdp.send('DOM.getDocument', {'depth':0})['root']['nodeId']
            node_id = cdp.send('DOM.querySelector', {
                'nodeId':root,'selector':'[data-testid="recommend-viewer-image"] img[data-pv-displayed="true"]'})['nodeId']
            node = cdp.send('DOM.describeNode', {'nodeId':node_id})['node']
            report['displayed_node_identity'] = dict(
                node_id=node_id,backend_node_id=node['backendNodeId'],node_name=node['nodeName'])
        except Exception as error:
            report['node_identity_error'] = type(error).__name__
        save()

        before = sample('before-front')
        # This is the only experimental change between the two sample windows.
        # It operates solely on this checker's newly created product Page.
        report['bring_to_front'] = dict(before=documents(),requested_at=time.monotonic())
        page.bring_to_front()
        report['bring_to_front']['returned_at'] = time.monotonic()
        report['bring_to_front']['after'] = documents()
        save()
        after = sample('after-front')
        final_src = handle.evaluate('(i)=>i.currentSrc || i.src')
        report['source_unchanged'] = final_src == initial_src
        report['displayed_at_end'] = displayed()
        report['final'] = state()
        report['comparison'] = (
            'three_colors_only_after_front' if not before['three_colors'] and after['three_colors'] else
            'three_colors_in_both_windows' if before['three_colors'] and after['three_colors'] else
            'three_colors_only_before_front' if before['three_colors'] else
            'three_colors_in_neither_window')
        report['phase'] = 'observed'
        save()
        assert report.get('gif',{}).get('matches_generated_contract') is True
        assert report['source_unchanged'] and report['displayed_at_end']['still_selected_node']
        assert report['displayed_at_end']['connected'] and report['displayed_at_end']['displayed']=='true'
        assert not report['errors'], 'one or more actual observations failed'
        final = report['final']
        assert final['original_unchanged'] and final['cookie_unchanged']
        assert final['http']['posts']==[] and final['static_jobs']==0 and final['active_count']==0
        assert final['external_server_attempts']==[]
        reads = final['http']['reads']
        assert any(row['target']=='13' and row['kind']=='ugoira_meta' for row in reads)
        assert any(row['target']=='13' and row['kind']=='archive' for row in reads)
        assert not any(row['kind'] in ('pages','original-image') for row in reads)
        assert after['three_colors'], 'foreground GIF still did not paint all three colors; see gif-diagnostic.json'
        report['diagnostic_observed'] = True
        return report
    except Exception as error:
        report['failure'] = type(error).__name__ + ': ' + str(error)
        raise
    finally:
        try:
            report['documents_final'] = documents()
            if handle is not None:
                report['displayed_final'] = displayed()
        except Exception as error:
            report['final_observation_error'] = type(error).__name__
        if cdp is not None:
            try:
                cdp.detach()
            except Exception as error:
                report['cdp_detach_error'] = type(error).__name__
        save()


def run(output, *, channel, headed=False, gif_diagnostic=False):
    import psutil
    from playwright.sync_api import sync_playwright
    output = owned_path(output)
    if output.exists():
        raise ValueError('do not overwrite an earlier browser attempt')
    output.mkdir(parents=True)
    before = source_hashes()
    tool_paths = (Path(__file__).resolve(), ROOT / 'tools/run_browser_checks.py')
    tool_before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in tool_paths}
    port = free_port()
    if port == 8089:
        raise ValueError('reserved port rejected')
    url = f'http://127.0.0.1:{port}'
    nonce = secrets.token_hex(24)
    headers = {'X-Acceptance': nonce}
    summary = {'success': False, 'channel': channel, 'headed': headed, 'python': sys.version,
               'source_before': before, 'tools_before': tool_before, 'runs': [],
               'synthetic_only': True, 'real_pixiv_gets': 0, 'real_pixiv_posts': 0,
               'browser_instances': 1, 'page_objects': 2, 'port': port,
               'scope': ('GIF foreground diagnostic only; not full recommendation acceptance' if gif_diagnostic else
                         'product recommendation visual acceptance; synthetic originals/GET only; native packaging and performance remain separate'),
               'diagnostic_only': gif_diagnostic, 'expected_runs': 1 if gif_diagnostic else 4}
    current = None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=channel, headless=not headed)
            summary['browser_version'] = browser.version
            context = browser.new_context(viewport={'width': 1280, 'height': 720}, device_scale_factor=1)
            pages = (context.new_page(), context.new_page())
            def guard(route):
                parsed = urlsplit(route.request.url)
                if (parsed.scheme, parsed.netloc) != ('http', f'127.0.0.1:{port}'):
                    if current is not None:
                        current['external_requests'].append(parsed.scheme + '://' + parsed.netloc)
                    route.abort()
                else:
                    route.continue_()
            context.route('**/*', guard)
            for page in pages:
                page.on('pageerror', lambda error: current['page_errors'].append(str(error))
                        if current is not None else None)
                page.on('console', lambda message: current['console_errors'].append(message.text)
                        if current is not None and message.type == 'error' else None)
                page.on('websocket', lambda socket: current['external_sockets'].append(
                    urlsplit(socket.url).scheme + '://' + urlsplit(socket.url).netloc)
                    if current is not None and (
                        urlsplit(socket.url).scheme, urlsplit(socket.url).netloc)
                        != ('ws', f'127.0.0.1:{port}') else None)
            try:
                viewports = ((1280, 720),) if gif_diagnostic else ((1280, 720), (1920, 1080))
                for width, height in viewports:
                    for page in pages:
                        page.set_viewport_size({'width': width, 'height': height})
                    for mode in (('initial',) if gif_diagnostic else ('initial', 'restart')):
                        destination = output / f'{width}x{height}' / mode
                        destination.mkdir(parents=True)
                        fixture = output / f'fixture-{width}x{height}'
                        current = {'success': False, 'mode': mode, 'viewport': [width, height],
                                   'port': port, 'page_errors': [], 'console_errors': [],
                                   'external_requests': [], 'external_sockets': [],
                                   'forced_cleanup': 0, 'remaining_children': []}
                        summary['runs'].append(current)
                        process = None
                        observed_children = []
                        context.clear_cookies()
                        with (destination / 'server.log').open('xb') as log:
                            try:
                                assert port_released(port), 'previous owned server still uses the port'
                                env = child_environment()
                                env['PYTHONDONTWRITEBYTECODE'] = '1'
                                process = subprocess.Popen(
                                    [sys.executable, '-B', str(Path(__file__).resolve()), '--serve', str(port),
                                     '--root', str(fixture), '--nonce', nonce, '--mode', mode],
                                    cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                                owner = psutil.Process(process.pid)
                                current['owner'] = {'pid': process.pid, 'created': owner.create_time()}
                                wait_for_health(url, process)
                                observed_children = [(child.pid, child.create_time())
                                                     for child in owner.children(recursive=True)]
                                operation = inspect_gif_diagnostic if gif_diagnostic else inspect
                                if gif_diagnostic:
                                    current['diagnostic_file'] = 'gif-diagnostic.json'
                                current['inspection'] = operation(pages, url, headers, destination, mode=mode)
                                assert not current['page_errors'], current['page_errors']
                                assert not current['console_errors'], current['console_errors']
                                assert not current['external_requests'], current['external_requests']
                                assert not current['external_sockets'], current['external_sockets']
                                current['success'] = True
                            except Exception as error:
                                current['error'] = f'{type(error).__name__}: {error}'
                                for index, page in enumerate(pages):
                                    try:
                                        page.screenshot(path=str(destination / f'failure-client-{index + 1}.png'),
                                                        animations='disabled')
                                    except Exception:
                                        pass
                                try:
                                    response = pages[1].request.get(url + '/_rv/state', headers=headers, timeout=3000)
                                    if response.ok:
                                        (destination / 'failure-state.json').write_text(
                                            json.dumps(response.json(), ensure_ascii=False, indent=2), encoding='utf-8')
                                except Exception:
                                    pass
                            finally:
                                for page in pages:
                                    try:
                                        page.goto('about:blank', timeout=5000)
                                    except Exception:
                                        pass
                                if process is not None:
                                    if process.poll() is None:
                                        try:
                                            parent = psutil.Process(process.pid)
                                            if parent.create_time() != current['owner']['created']:
                                                raise RuntimeError('owned server PID was reused')
                                            observed_children = sorted(set(observed_children) | {
                                                (child.pid, child.create_time())
                                                for child in parent.children(recursive=True)})
                                        except psutil.NoSuchProcess:
                                            pass
                                        try:
                                            request = urllib.request.Request(url + '/_rv/shutdown', data=b'',
                                                                             headers=headers, method='POST')
                                            with opener.open(request, timeout=4) as response:
                                                current['shutdown_requested'] = response.status == 200
                                        except Exception as error:
                                            current['shutdown_error'] = type(error).__name__
                                        try:
                                            process.wait(timeout=25)
                                        except subprocess.TimeoutExpired:
                                            current['normal_exit_timeout'] = True
                                    current['exit_code'] = process.poll()
                                    deadline = time.monotonic() + 10
                                    for child_pid, created in observed_children:
                                        try:
                                            child = psutil.Process(child_pid)
                                            if child.create_time() != created:
                                                current.setdefault('reused_child_pids', []).append(child_pid)
                                                continue
                                            try:
                                                code = child.wait(timeout=max(0, deadline - time.monotonic()))
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
                                            current['normal_exit_timeout'] = True
                                        except psutil.NoSuchProcess:
                                            current.setdefault('already_exited_children', []).append(
                                                {'pid': child_pid, 'created': created})
                                current['port_released'] = port_released(port)
                                shutdown_path = fixture / f'shutdown-{mode}.json'
                                if shutdown_path.is_file():
                                    current['shutdown_proof'] = json.loads(shutdown_path.read_text(encoding='utf-8'))
                                proof = current.get('shutdown_proof', {})
                                current['success'] = bool(current['success']
                                    and current.get('exit_code') == 0 and current['port_released']
                                    and not current.get('child_exit_failed', False)
                                    and current['forced_cleanup'] == 0 and not current['remaining_children']
                                    and proof.get('reader_closed') is True
                                    and proof.get('active_count') == 0
                                    and proof.get('shutdown_unverified') is False
                                    and proof.get('original_unchanged') is True
                                    and proof.get('cookie_unchanged') is True
                                    and proof.get('external_server_attempts') == [])
                                (destination / 'result.json').write_text(
                                    json.dumps(current, ensure_ascii=False, indent=2), encoding='utf-8')
                        if not current['success']:
                            raise AssertionError(current.get('error', 'owned server did not exit cleanly'))
            finally:
                current = None
                context.close()
                browser.close()
        summary['success'] = (len(summary['runs']) == summary['expected_runs']
                              and all(item['success'] for item in summary['runs']))
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
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serve',type=int)
    parser.add_argument('--root',type=Path)
    parser.add_argument('--nonce')
    parser.add_argument('--mode',choices=('initial','restart'),default='initial')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--channel',choices=('msedge','chromium'),default='msedge')
    parser.add_argument('--headed',action='store_true')
    parser.add_argument('--gif-diagnostic',action='store_true',
                        help='One 1280x720 initial run: actual GIF bytes/pixels before and after bring_to_front only')
    args=parser.parse_args()
    if args.serve is not None:
        if args.root is None or not args.nonce:parser.error('--serve requires --root and --nonce')
        serve(args.serve,args.root,args.nonce,args.mode)
        return 0
    if args.output is None:parser.error('--output required')
    return run(args.output,channel=args.channel,headed=args.headed,gif_diagnostic=args.gif_diagnostic)


if __name__=='__main__':
    raise SystemExit(main())
