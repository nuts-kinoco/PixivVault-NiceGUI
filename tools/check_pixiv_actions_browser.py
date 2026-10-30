"""R16/R17 acceptance through DemoShell with generated data and fake HTTP only.

Actual LivePixivReadClient, PixivWriteClient, PixivActions and product UI run in
an isolated server. A synthetic Cookie is generated inside its fresh owned
root; no user's Cookie or real API is used. One browser, two Page objects and
one loopback port are reused for both modes and both viewports.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import hashlib
from io import BytesIO
import json
import os
import re
from pathlib import Path
import secrets
import subprocess
import sys
from threading import Event, RLock
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
import urllib.request

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.run_browser_checks import (
    child_environment, free_port, port_released, source_hashes, stop_child, wait_for_health,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
ARTIFACTS = ROOT / 'artifacts/p3-318-r16-r17-actions'
PREFIX = 'https://i.pximg.net/r16-generated/'
WORKS = ('123', '124')


def owned_path(path):
    from pixivvault_nicegui.managed_downloads import _safe_path
    path = Path(path).absolute()
    if path == ARTIFACTS or not path.is_relative_to(ARTIFACTS):
        raise ValueError('fresh R16 candidate artifact subdirectory required')
    _safe_path(path)
    return path


class SyntheticHTTP:
    """Only generated targets; never retain Cookie, token, request or raw body."""
    def __init__(self):
        self.lock = RLock()
        self.account = '55'
        self.bookmarks = {'123': {'id': '777', 'private': True}, '124': None}
        self.following = {}
        self.reads = []
        self.posts = []
        self.hold_next_follow = False
        self.follow_waiting = False
        self.release_follow = Event()
        self.release_follow.set()
        self.unknown_next_bookmark = False

    def get(self, request, timeout, limit, deadline):
        route = urlsplit(request.full_url)
        if request.get_method() != 'GET' or (route.scheme, route.netloc) != ('https', 'www.pixiv.net'):
            raise AssertionError('unexpected generated GET transport target')
        with self.lock:
            self.reads.append({'path': route.path})
            account = self.account
            if route.path == '/':
                payload = {'props': {'pageProps': {
                    'isLoggedIn': True, 'gaUserData': {'id': account},
                    'serverSerializedPreloadedState': json.dumps({'api': {'token': 'a' * 32}}),
                }}}
                return ('<script id="__NEXT_DATA__" type="application/json">'
                        + json.dumps(payload) + '</script>').encode()
            if route.path == '/ajax/user/extra':
                body = {'userId': account}
            elif route.path in tuple('/ajax/illust/' + wid for wid in WORKS):
                wid = route.path.rsplit('/', 1)[-1]
                body = {'id': wid, 'userId': '91', 'userName': 'Generated author',
                        'bookmarkData': self.bookmarks[wid], 'title': 'Generated ' + wid,
                        'pageCount': 2 if wid == '123' else 1, 'illustType': 0,
                        'xRestrict': 0, 'tags': {'tags': []},
                        'createDate': '2026-10-29T00:00:00Z', 'width': 160, 'height': 120}
            elif route.path == '/ajax/user/' + account + '/following':
                query = parse_qs(route.query)
                if query.get('rest') not in (['show'], ['hide']) or 'offset' not in query:
                    raise AssertionError('unexpected generated following query')
                visibility = 0 if query['rest'] == ['show'] else 1
                users = [] if query['offset'] != ['0'] else [
                    {'userId': uid, 'userName': 'Generated author'}
                    for uid, restrict in self.following.items() if restrict == visibility]
                body = {'users': users}
            else:
                raise AssertionError('unexpected generated GET path')
            return json.dumps({'error': False, 'body': body}).encode()

    def post(self, request, timeout, limit, deadline):
        from pixivvault_nicegui.pixiv_write_client import WriteHTTPResponse
        route = urlsplit(request.full_url)
        if request.get_method() != 'POST' or (route.scheme, route.netloc) != ('https', 'www.pixiv.net'):
            raise AssertionError('unexpected generated POST transport target')
        held = False
        with self.lock:
            if route.path == '/ajax/illusts/bookmarks/add':
                values = json.loads(request.data)
                target = values.get('illust_id')
                if target not in WORKS or values.get('restrict') not in (0, 1):
                    raise AssertionError('unexpected generated bookmark target')
                entry = {'path': route.path, 'action': 'bookmark_add', 'target': target,
                         'restrict': values['restrict'], 'outcome': 'pending'}
            elif route.path == '/bookmark_add.php':
                values = parse_qs(request.data.decode())
                if (values.get('type') != ['user'] or values.get('user_id') != ['91']
                        or values.get('mode') not in (['add'], ['delete'])):
                    raise AssertionError('unexpected generated follow target')
                action = 'follow' if values['mode'] == ['add'] else 'unfollow'
                restrict = int(values['restrict'][0]) if action == 'follow' else None
                if restrict not in (None, 0, 1):
                    raise AssertionError('unexpected generated follow visibility')
                entry = {'path': route.path, 'action': action, 'target': '91',
                         'restrict': restrict, 'outcome': 'pending'}
                held = self.hold_next_follow and action == 'follow'
                if held:
                    self.hold_next_follow = False
                    self.follow_waiting = True
            else:
                self.posts.append({'path': route.path, 'outcome': 'unexpected'})
                raise AssertionError('unexpected generated POST path')
            self.posts.append(entry)

        # Deliberately outside the fixture lock so the second client can observe
        # pending state and can release the already accepted request.
        if held:
            remaining = max(0.0, min(20.0, deadline - time.monotonic()))
            released = self.release_follow.wait(remaining)
            with self.lock:
                self.follow_waiting = False
                if not released:
                    entry['outcome'] = 'indeterminate_not_applied'
            if not released:
                raise TimeoutError('generated response unavailable')

        with self.lock:
            if entry['action'] == 'bookmark_add':
                self.bookmarks[entry['target']] = {'id': '888', 'private': bool(entry['restrict'])}
                if self.unknown_next_bookmark:
                    self.unknown_next_bookmark = False
                    entry['outcome'] = 'indeterminate_applied'
                    raise TimeoutError('generated response unavailable')
                body = b'{"error":false,"body":{"last_bookmark_id":"888"}}'
            else:
                if entry['action'] == 'follow':
                    self.following['91'] = entry['restrict']
                else:
                    self.following.pop('91', None)
                body = b'{"error":false,"body":{}}'
            entry['outcome'] = 'confirmed'
            return WriteHTTPResponse(200, body)

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps({
                'account': self.account, 'bookmarks': self.bookmarks,
                'following': self.following, 'reads': self.reads, 'posts': self.posts,
                'follow_waiting': self.follow_waiting,
            }))


def serve(port, root, nonce, mode):
    from fastapi import Header, HTTPException
    from nicegui import app, ui
    from PIL import Image
    from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
    from pixivvault_nicegui.managed_downloads import ManagedPixivDownloads, _safe_path
    from pixivvault_nicegui.media_routes import register_media_routes
    from pixivvault_nicegui.pixiv_read_client import LivePixivReadClient
    from pixivvault_nicegui.pixiv_write_client import PixivWriteClient
    from pixivvault_nicegui.recommendation_state import (
        RecommendationController, RecommendationGroup, RecommendationSnapshot, RecommendationWork,
    )
    from pixivvault_nicegui.services import get_services
    from pixivvault_nicegui.ui.pixiv_action_buttons import PixivActionButtons
    from pixivvault_nicegui.ui.shell import build_demo_page
    from pixivvault_nicegui import pixiv_actions as actions_module

    if not 1024 <= port <= 65535 or port == 8089 or mode not in ('readonly', 'enabled'):
        raise ValueError('owned high loopback port and explicit policy required')
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
    if any(root.iterdir()):
        raise ValueError('fresh empty fixture root required')
    with (root / '.r16-fixture.json').open('x', encoding='utf-8') as stream:
        json.dump({'purpose': 'generated-R16-R17-product-acceptance', 'version': 1, 'mode': mode}, stream)
    cookie = root / 'generated-cookie.txt'
    with cookie.open('x', encoding='utf-8') as stream:
        stream.write('# Netscape HTTP Cookie File\n.pixiv.net\tTRUE\t/\tTRUE\t'
                     + str(int(time.time()) + 86400) + '\tPHPSESSID\t55_fixture_'
                     + secrets.token_hex(16) + '\n')
    cookie_digest = hashlib.sha256(cookie.read_bytes()).hexdigest()

    class MediaReader:
        def verify_identity(self):
            return '55'
        def get_artwork_download_record(self, wid):
            if wid not in WORKS:
                raise ValueError('generated saved work only')
            return RemoteBookmark(wid, 'illustration', 'show', 'Generated ' + wid,
                '91', 'Generated author', (), '', '2026-10-29T00:00:00Z',
                2 if wid == '123' else 1, 0, 0)
        def get_artwork_original_pages(self, wid):
            record = self.get_artwork_download_record(wid)
            return tuple(PREFIX + wid + '/' + str(page) + '.png' for page in range(record.page_count))
        def get_image_bytes(self, url, **kwargs):
            if not url.startswith(PREFIX):
                raise ValueError('generated saved image only')
            parts = url[len(PREFIX):].split('/')
            if len(parts) != 2 or url not in self.get_artwork_original_pages(parts[0]):
                raise ValueError('generated saved image identity rejected')
            page = int(parts[1].split('.')[0])
            image = BytesIO()
            Image.new('RGB', (160 + page * 20, 120), (30 + page * 40, 60, 100)).save(image, 'PNG')
            return image.getvalue()

    media = MediaReader()
    seed = ManagedPixivDownloads(root / 'managed', media,
                                 resolve_record=media.get_artwork_download_record)
    try:
        context = SimpleNamespace(checkpoint=lambda: None, progress=lambda *_: None)
        for wid in WORKS:
            seed.run(SimpleNamespace(kind='pixiv-illust', target_id=wid), context)
    finally:
        seed.close()

    http = SyntheticHTTP()
    reader = LivePixivReadClient(cookie, transport=http.get)
    actual_actions = actions_module.PixivActions
    factory_calls = []
    def synthetic_factory(owner, *, following_store=None):
        if owner is not reader:
            raise AssertionError('fixture reader ownership mismatch')
        factory_calls.append('created')
        return actual_actions(owner, writer=PixivWriteClient(owner, transport=http.post),
                              following_store=following_store)
    actions_module.PixivActions = synthetic_factory
    try:
        services = get_services(app, live_client=reader, pixiv_download_root=root / 'managed',
            preferences_root=root / 'preferences', following_sync_root=root / 'following',
            pixiv_actions_mode=mode)
    finally:
        actions_module.PixivActions = actual_actions
    if (mode == 'enabled') != (services.pixiv_actions is not None):
        raise AssertionError('product capability gate did not select the explicit mode')
    if len(factory_calls) != (1 if mode == 'enabled' else 0):
        raise AssertionError('product action owner factory count mismatch')
    if http.reads or http.posts:
        raise AssertionError('product construction performed implicit HTTP')
    snapshot = RecommendationSnapshot(sections=(
        ('superlike', (RecommendationGroup('superlike', 'Generated saved works', tuple(
            RecommendationWork(wid, 'Generated ' + wid, '91', 'Generated author') for wid in WORKS)),)),
    ), unavailable=())
    previous_recommendations = services.recommendations
    services.recommendations = RecommendationController(SimpleNamespace(load=lambda: snapshot))
    if previous_recommendations is not None:
        previous_recommendations.close()

    writer = services.managed_downloads
    def proof_map():
        result = {}
        for folder in (writer.outputs, writer.generation_outputs):
            for path in sorted(folder.rglob('*')):
                if path.is_file():
                    _safe_path(path, file=True)
                    result[path.relative_to(writer.root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        return result
    baseline = proof_map()
    client_ids = set()
    trace_lock = RLock()
    click_events = []
    actual_click = PixivActionButtons.click
    async def traced_click(binding, kind, token):
        # Observe the real UI handler; do not substitute authorization/results.
        item = {'client_id': binding.client.id, 'kind': kind,
                'target': list(binding._target) if binding._target is not None else None}
        try:
            result = await actual_click(binding, kind, token)
        except BaseException as error:
            item.update(result=False, error=type(error).__name__)
            raise
        else:
            item['result'] = result
            return result
        finally:
            with trace_lock:
                item['sequence'] = len(click_events) + 1
                click_events.append(item)
    PixivActionButtons.click = traced_click

    def auth(value):
        if not secrets.compare_digest(value or '', nonce):
            raise HTTPException(403)
    @app.get('/healthz')
    def health():
        return {'status': 'ok', 'mode': 'demo', 'data': 'synthetic'}
    @app.get('/_r16/state')
    def state(x_acceptance: str | None = Header(default=None)):
        auth(x_acceptance)
        actions = services.pixiv_actions
        states, outcomes = {}, {}
        if actions is not None:
            for kind, target in (('illust', '123'), ('illust', '124'), ('user', '91')):
                key = kind + ':' + target
                current = actions.state('55', kind, target)
                states[key] = asdict(current)
                original = actions.result(current.operation_id) if current.operation_id else None
                reconciled = actions.reconciliation_result(current.operation_id) if current.operation_id else None
                outcomes[key] = {
                    'original': original.status if original is not None else None,
                    'reconciled': reconciled.status if reconciled is not None else None,
                }
        with trace_lock:
            clients, clicks = sorted(client_ids), list(click_events)
        return {'mode': mode, 'enabled': actions is not None, 'factory_calls': len(factory_calls),
                'http': http.snapshot(), 'states': states, 'outcomes': outcomes,
                'active_count': actions.active_count if actions is not None else 0,
                'cached_account': actions.cached_verified_account_id if actions is not None else None,
                'reader_closed': bool(getattr(reader, '_closed', False)),
                'following': asdict(services.following_sync.snapshot('55')),
                'clients': clients, 'clicks': clicks, 'original_unchanged': proof_map() == baseline,
                'cookie_unchanged': hashlib.sha256(cookie.read_bytes()).hexdigest() == cookie_digest,
                'external_server_attempts': list(external_attempts)}
    @app.post('/_r16/inject')
    def inject(values: dict, x_acceptance: str | None = Header(default=None)):
        auth(x_acceptance)
        if not values or not set(values) <= {'hold_follow', 'release_follow', 'unknown_bookmark', 'account'}:
            raise HTTPException(400)
        if any(type(v) is not bool for k, v in values.items() if k != 'account'):
            raise HTTPException(400)
        if 'account' in values and values['account'] not in ('55', '56'):
            raise HTTPException(400)
        with http.lock:
            if values.get('hold_follow'):
                http.hold_next_follow = True
                http.release_follow.clear()
            if values.get('release_follow'):
                http.release_follow.set()
            if values.get('unknown_bookmark'):
                http.unknown_next_bookmark = True
            if 'account' in values:
                http.account = values['account']
                reader._verified_identity = values['account']
        return {'ok': True}
    @app.post('/_r16/shutdown')
    def shutdown(x_acceptance: str | None = Header(default=None)):
        auth(x_acceptance)
        http.release_follow.set()
        app.shutdown()
        return {'stopping': True}
    async def close():
        actions = services.pixiv_actions
        await asyncio.to_thread(services.close)
        final = {'reader_closed': bool(getattr(reader, '_closed', False)),
                 'active_count': actions.active_count if actions is not None else 0,
                 'shutdown_unverified': actions.shutdown_unverified if actions is not None else False,
                 'original_unchanged': proof_map() == baseline,
                 'cookie_unchanged': hashlib.sha256(cookie.read_bytes()).hexdigest() == cookie_digest,
                 'external_server_attempts': list(external_attempts)}
        with (root / 'shutdown.json').open('x', encoding='utf-8') as stream:
            json.dump(final, stream, ensure_ascii=False, indent=2)
    app.on_shutdown(close)
    def product_page():
        client = ui.context.client
        with trace_lock:
            client_ids.add(client.id)
        def deleted():
            with trace_lock:
                client_ids.discard(client.id)
        client.on_delete(deleted)
        ui.element('span').props('data-testid=fixture-client data-client-id=' + client.id).style('display:none')
        build_demo_page()
    register_media_routes(app, services.images)
    ui.page('/')(product_page)
    ui.run(host='127.0.0.1', port=port, show=False, reload=False,
           binding_refresh_interval=None, timeout_graceful_shutdown=10)


def inspect(pages, url, headers, output, *, mode):
    from playwright.sync_api import expect
    expect.set_options(timeout=20000)
    first, second = pages
    checks = []
    phase = 'constructed'

    def mark(value):
        nonlocal phase
        phase = value
        with (output / 'progress.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'phase': phase, 'time': time.time()}, ensure_ascii=False) + '\n')

    def state():
        response = second.request.get(url + '/_r16/state', headers=headers)
        assert response.ok, f'{phase}: state HTTP {response.status}'
        return response.json()

    def inject(**values):
        response = second.request.post(url + '/_r16/inject', headers=headers, data=values)
        assert response.ok, f'{phase}: injection HTTP {response.status}'

    def counts(value):
        return len(value['http']['reads']), len(value['http']['posts'])

    def eventually(predicate, label, *, timeout=20):
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            last = state()
            if predicate(last):
                return last
            second.wait_for_timeout(100)
        raise AssertionError(f'{phase}: {label}; counts={counts(last) if last else None}')

    def tab(page, name):
        button = page.get_by_test_id('tab-' + name)
        button.click()
        expect(button).to_have_attribute('aria-current', 'page')

    def mount(page):
        page.goto(url, wait_until='domcontentloaded')
        expect(page.get_by_test_id('tab-recommend')).to_be_enabled()
        tab(page, 'recommend')
        for wid in WORKS:
            expect(page.get_by_test_id('recommend-work-superlike-' + wid)).to_be_visible()
        client_id = page.get_by_test_id('fixture-client').get_attribute('data-client-id')
        assert client_id
        return client_id

    def action(page, wid, kind):
        return page.get_by_test_id(f'recommend-action-superlike-{wid}-{kind}')

    def label(button, value):
        # Quasar icons also contribute DOM text; anchor the exact label at the end.
        expect(button).to_have_text(re.compile(re.escape(value) + r'\s*$'))

    def viewer(page):
        page.get_by_test_id('recommend-work-superlike-123').get_by_role(
            'button', name='保存済みを開く', exact=True).click()
        view = page.get_by_test_id('library-viewer')
        expect(view).to_be_visible()
        expect(view).to_have_attribute('data-zoom-ready', 'true')
        expect(view).to_have_attribute('data-quality-work', '123')
        expect(view).to_have_attribute('data-quality-page', '0')
        expect(view.locator('.pv-counter')).to_have_text('1/2')
        return view

    def click_done(button, predicate, label):
        before = state()
        sequence = len(before['clicks'])
        button.click()
        return eventually(lambda value: len(value['clicks']) > sequence and predicate(value), label)

    def stale_click(button, old_token, label):
        before = state()
        current_token = button.get_attribute('data-action-token')
        assert current_token and old_token and current_token != old_token
        sequence = len(before['clicks'])
        # Replay a previously emitted render token through the real browser
        # listener and real server handler; never call a service action directly.
        button.evaluate('(element, token) => element.dataset.actionToken = token', old_token)
        try:
            button.click()
            after = eventually(lambda value: len(value['clicks']) > sequence,
                               'stale browser event did not finish')
            assert after['clicks'][-1]['result'] is False, label
            assert 'error' not in after['clicks'][-1], label
            assert counts(after) == counts(before), label
        finally:
            button.evaluate('(element, token) => element.dataset.actionToken = token', current_token)
        checks.append(label)

    def local_following(value):
        return {row['user_id'] for row in value['following']['authors'] if row['is_following']}

    mark('constructed')
    constructed = state()
    assert counts(constructed) == (0, 0)
    assert constructed['enabled'] is (mode == 'enabled')
    assert constructed['factory_calls'] == (1 if mode == 'enabled' else 0)
    mark('two-client-mount')
    first_id, second_id = mount(first), mount(second)
    assert first_id != second_id
    initial = eventually(lambda value: first_id in value['clients'] and second_id in value['clients'],
                         'both product clients were not registered')
    # Include at least one actual 500-ms client-state poll in the GET0 proof.
    second.wait_for_timeout(650)
    initial = state()
    assert counts(initial) == (0, 0)
    checks.append('product construction, two client mounts and cached UI polling perform zero Pixiv GET/POST')
    for page in pages:
        for wid in WORKS:
            for kind in ('bookmark', 'follow'):
                if mode == 'enabled':
                    expect(action(page, wid, kind)).to_be_enabled()
                else:
                    expect(action(page, wid, kind)).to_be_disabled()

    mark('product-saved-viewer')
    view = viewer(first)
    bookmark = view.get_by_test_id('viewer-action-bookmark')
    follow = view.get_by_test_id('viewer-action-follow')
    assert counts(state()) == (0, 0)
    if mode == 'readonly':
        expect(bookmark).to_be_disabled()
        expect(follow).to_be_disabled()
        first.screenshot(path=str(output / 'readonly-viewer.png'), animations='disabled')
        view.get_by_role('button', name='閉じる', exact=True).click()
        tab(first, 'recommend')
        action(first, '123', 'bookmark').scroll_into_view_if_needed()
        first.screenshot(path=str(output / 'readonly-recommend.png'), animations='disabled')
        assert counts(state()) == (0, 0)
        checks.append('explicit readonly policy disables actual recommendation and saved-viewer action controls')
    else:
        mark('stale-viewer-page-event')
        old_page_token = bookmark.get_attribute('data-action-token')
        view.get_by_role('button', name='次のページ', exact=True).click()
        expect(view).to_have_attribute('data-quality-page', '1')
        expect(view).to_have_attribute('data-zoom-ready', 'true')
        expect(view.locator('.pv-counter')).to_have_text('2/2')
        expect(bookmark).not_to_have_attribute('data-action-token', old_page_token)
        stale_click(bookmark, old_page_token, 'old viewer page event is rejected before GET and POST')

        mark('existing-private-bookmark')
        private = click_done(bookmark,
            lambda value: value['states']['illust:123']['observed'] is True,
            'private existing bookmark was not observed')
        assert len(private['http']['reads']) > 0 and len(private['http']['posts']) == 0
        assert private['http']['bookmarks']['123'] == {'id': '777', 'private': True}
        assert private['outcomes']['illust:123']['original'] == 'already'
        label(bookmark, 'ブクマ済み')
        expect(bookmark).to_be_disabled()
        label(action(second, '123', 'bookmark'), 'ブクマ済み')
        expect(action(second, '123', 'bookmark')).to_be_disabled()
        first.screenshot(path=str(output / 'private-bookmark-viewer.png'), animations='disabled')
        action(second, '123', 'bookmark').scroll_into_view_if_needed()
        second.screenshot(path=str(output / 'private-bookmark-peer.png'), animations='disabled')
        checks.append('existing private bookmark is observed using GET only and shared with the recommendation client')

        view.get_by_role('button', name='閉じる', exact=True).click()
        tab(first, 'recommend')
        mark('accepted-follow-client-delete')
        inject(hold_follow=True)
        action(first, '123', 'follow').click()
        pending = eventually(lambda value: value['http']['follow_waiting']
            and value['states']['user:91']['pending'], 'accepted synthetic follow did not remain pending')
        assert len(pending['http']['posts']) == 1
        expect(action(second, '123', 'follow')).to_be_disabled()
        label(action(second, '123', 'follow'), '処理中…')
        action(second, '123', 'follow').scroll_into_view_if_needed()
        second.screenshot(path=str(output / 'shared-pending-follow.png'), animations='disabled')
        first.goto('about:blank')
        deleted = eventually(lambda value: first_id not in value['clients'],
                             'departed product client was not deleted')
        assert deleted['states']['user:91']['pending']
        assert deleted['active_count'] > 0 and not deleted['reader_closed']
        inject(release_follow=True)
        followed = eventually(lambda value: value['active_count'] == 0
            and value['states']['user:91']['observed'] is True
            and local_following(value) == {'91'}, 'accepted follow did not drain and publish')
        assert len(followed['http']['posts']) == 1 and followed['http']['following'] == {'91': 0}
        assert not followed['reader_closed']
        label(action(second, '123', 'follow'), 'フォロー解除')
        expect(action(second, '123', 'follow')).to_be_enabled()
        checks.append('accepted follow survives initiating-client deletion; peer state and local following commit after drain')

        mark('explicit-unfollow')
        unfollowed = click_done(action(second, '123', 'follow'),
            lambda value: value['active_count'] == 0 and value['states']['user:91']['observed'] is False
                and local_following(value) == set(), 'explicit unfollow did not commit')
        assert len(unfollowed['http']['posts']) == 2 and unfollowed['http']['following'] == {}
        label(action(second, '123', 'follow'), 'フォロー')
        assert [item['action'] for item in unfollowed['http']['posts']] == ['follow', 'unfollow']
        checks.append('explicit unfollow updates the remote fixture, local following row and displayed action')

        mark('client-remount-no-fetch')
        before_mount = counts(state())
        remounted_id = mount(first)
        assert remounted_id != first_id
        assert counts(state()) == before_mount
        label(action(first, '123', 'bookmark'), 'ブクマ済み')
        label(action(first, '123', 'follow'), 'フォロー')
        checks.append('same browser Page remount shares completed action state without extra Pixiv GET/POST')

        mark('unknown-bookmark-outcome')
        inject(unknown_bookmark=True)
        unknown = click_done(action(second, '124', 'bookmark'),
            lambda value: value['states']['illust:124']['unknown']
                and value['active_count'] == 0, 'unknown bookmark result was not fenced')
        assert len(unknown['http']['posts']) == 3
        assert unknown['http']['posts'][-1]['outcome'] == 'indeterminate_applied'
        assert unknown['outcomes']['illust:124']['original'] == 'indeterminate'
        for page in pages:
            label(action(page, '124', 'bookmark'), 'ブクマ結果を確認')
            expect(action(page, '124', 'bookmark')).to_be_enabled()
        second.screenshot(path=str(output / 'unknown-bookmark.png'), animations='disabled')

        mark('peer-explicit-get-reconciliation')
        reads_before = len(unknown['http']['reads'])
        reconciled = click_done(action(first, '124', 'bookmark'),
            lambda value: not value['states']['illust:124']['unknown']
                and value['states']['illust:124']['observed'] is True,
            'peer GET reconciliation did not publish the observed state')
        assert len(reconciled['http']['posts']) == 3 and len(reconciled['http']['reads']) > reads_before
        assert reconciled['outcomes']['illust:124'] == {
            'original': 'indeterminate', 'reconciled': 'observed'}
        for page in pages:
            label(action(page, '124', 'bookmark'), 'ブクマ済み')
            expect(action(page, '124', 'bookmark')).to_be_disabled()
        first.screenshot(path=str(output / 'unknown-reconciled-by-peer.png'), animations='disabled')
        checks.append('peer explicitly reconciles an applied-but-unacknowledged bookmark using GET; POST stays at three and original uncertainty is retained')

        mark('stale-recommendation-render-event')
        old_card_token = action(first, '123', 'follow').get_attribute('data-action-token')
        first.get_by_test_id('recommend-size-small').click()
        expect(first.get_by_test_id('recommend-size-small')).to_have_attribute('aria-pressed', 'true')
        expect(action(first, '123', 'follow')).not_to_have_attribute('data-action-token', old_card_token)
        stale_click(action(first, '123', 'follow'), old_card_token,
                    'old recommendation render event is rejected before GET and POST')

        mark('stale-account-event')
        old_account_token = action(first, '123', 'follow').get_attribute('data-action-token')
        before_account = counts(state())
        inject(account='56')
        expect(action(first, '123', 'follow')).not_to_have_attribute('data-action-token', old_account_token)
        assert state()['cached_account'] == '56' and counts(state()) == before_account
        stale_click(action(first, '123', 'follow'), old_account_token,
                    'old account event is rejected before GET and POST')
        inject(account='55')
        restored = eventually(lambda value: value['cached_account'] == '55', 'cached account did not restore')
        assert counts(restored) == before_account
        label(action(first, '123', 'bookmark'), 'ブクマ済み')
        label(action(second, '123', 'bookmark'), 'ブクマ済み')
        action(first, '123', 'bookmark').scroll_into_view_if_needed()
        first.screenshot(path=str(output / 'final-enabled.png'), animations='disabled')

    mark('final-proof')
    final = eventually(lambda value: value['active_count'] == 0, 'action workers did not drain')
    assert final['original_unchanged'] and final['cookie_unchanged'] and not final['reader_closed']
    assert final['external_server_attempts'] == []
    assert len(final['http']['posts']) == (3 if mode == 'enabled' else 0)
    assert final['http']['bookmarks']['123'] == {'id': '777', 'private': True}
    return {'checks': checks, 'constructed': constructed, 'initial': initial, 'final': final,
            'viewport': second.evaluate('({width:innerWidth,height:innerHeight,dpr:devicePixelRatio})'),
            'mode': mode, 'page_objects': 2}


def run(output, *, channel, headed=False):
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
               'scope': 'product browser acceptance; CPU weakref proof and durable restart state are separate'}
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
                for width, height in ((1280, 720), (1920, 1080)):
                    for page in pages:
                        page.set_viewport_size({'width': width, 'height': height})
                    for mode in ('readonly', 'enabled'):
                        destination = output / f'{width}x{height}' / mode
                        destination.mkdir(parents=True)
                        fixture = output / f'fixture-{width}x{height}-{mode}'
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
                                current['inspection'] = inspect(pages, url, headers, destination, mode=mode)
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
                                    response = pages[1].request.get(url + '/_r16/state', headers=headers, timeout=3000)
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
                                            request = urllib.request.Request(url + '/_r16/shutdown', data=b'',
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
                                            current['forced_cleanup'] += 1
                                            child.terminate()
                                            try:
                                                child.wait(timeout=5)
                                            except psutil.TimeoutExpired:
                                                child.kill()
                                                child.wait(timeout=5)
                                        except psutil.NoSuchProcess:
                                            current.setdefault('already_exited_children', []).append(
                                                {'pid': child_pid, 'created': created})
                                current['port_released'] = port_released(port)
                                shutdown_path = fixture / 'shutdown.json'
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
        summary['success'] = len(summary['runs']) == 4 and all(item['success'] for item in summary['runs'])
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
    parser.add_argument('--mode', choices=('readonly', 'enabled'), default='readonly')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--channel', choices=('msedge', 'chromium'), default='msedge')
    parser.add_argument('--headed', action='store_true')
    args = parser.parse_args()
    if args.serve is not None:
        if args.root is None or not args.nonce:
            parser.error('--serve requires --root and --nonce')
        serve(args.serve, args.root, args.nonce, args.mode)
        return 0
    if args.output is None:
        parser.error('--output required')
    return run(args.output, channel=args.channel, headed=args.headed)


if __name__ == '__main__':
    raise SystemExit(main())
