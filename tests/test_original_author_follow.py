"""A01/U15: real components with generated payloads and a mandatory socket fence."""
import asyncio
import json
import time
from copy import deepcopy
from threading import Event, RLock
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from nicegui import Client, core, ui
from required_network_guard import no_network

from pixivvault_nicegui.pixiv_actions import PixivActions
from pixivvault_nicegui.pixiv_read_client import LivePixivReadClient, InvalidPayload, DestinationRejected
from pixivvault_nicegui.pixiv_write_client import PixivWriteClient, WriteHTTPResponse
from pixivvault_nicegui.ui.author_browse_view import AuthorBrowseView
from pixivvault_nicegui.ui.unfollowed_authors_view import UnfollowedAuthorsView
from pixivvault_nicegui.viewer_metadata import read_author
from test_p3_viewer_author import services
from test_required_unfollowed import Reader as BookmarkReader, row as bookmark_row


def work(wid='103', **changes):
    return dict(id=wid, userId='91', title='Generated', userName='Author',
                illustType=0, pageCount=1, url='https://i.pximg.net/generated.jpg', **changes)


def raw_reader(rows):
    """Exercise the production author normalizer without cookies or any transport."""
    reader = LivePixivReadClient.__new__(LivePixivReadClient)
    reader._closed = False
    reader._verified_identity = '7'
    reader._identity_lock = RLock()
    def response(kind, _url, *, deadline):
        if kind == 'author_all':
            return {'illusts': {r['id']: None for r in rows}, 'manga': {}}
        assert kind == 'author_details'
        return {'works': {r['id']: deepcopy(r) for r in rows}}
    reader._json = response
    return reader


@pytest.mark.parametrize('marker', ['isMasked', 'isDeleted'])
def test_author_mixed_accessible_and_identifiable_unavailable_keep_both(marker):
    reader = raw_reader([work(), dict(id='102', userId='91', **{marker: True})])
    records = read_author(reader, '91')
    assert [r.work_id for r in records] == ['103', '102']
    assert records[0].thumbnail.endswith('generated.jpg')
    assert records[1].thumbnail == '' and not records[1].page_count_known
    assert records[1].title and records[1].author_id == '91'


@pytest.mark.parametrize('patch', [
    {'userId': '92'}, {'isMasked': 1}, {'title': 7}, {'userName': []},
    {'tags': [7]}, {'url': 'file:///private'}, {'pageCount': 0},
    {'illustType': 9}, {'xRestrict': True}, {'createDate': 'bad\n'},
    {'description': []},
])
def test_unavailable_does_not_hide_malformed_metadata_or_wrong_author(patch):
    row = dict(id='102', userId='91', isMasked=True)
    row.update(patch)
    with pytest.raises((InvalidPayload, DestinationRejected)):
        read_author(raw_reader([work(), row]), '91')


def test_unavailable_does_not_make_download_inventory_complete():
    reader = raw_reader([work(), dict(id='102', userId='91', isMasked=True)])
    with pytest.raises(InvalidPayload):
        reader.get_author_artworks_complete('91')


@pytest.mark.parametrize('rows', [
    [dict(id='102', isMasked=True)],
    [dict(id='102', userId='91')],
    [dict(work(), title=[])],
    [dict(work(), pageCount=0)],
])
def test_unidentified_or_unmarked_incomplete_work_and_normal_bad_metadata_are_rejected(rows):
    with pytest.raises(InvalidPayload):
        read_author(raw_reader(rows), '91')


def test_author_account_change_during_rows_is_rejected():
    reader = raw_reader([work()])
    original = reader._json
    def change(*args, **kwargs):
        result = original(*args, **kwargs)
        if args[0] == 'author_details':
            reader._verified_identity = '8'
        return result
    reader._json = change
    with pytest.raises(ValueError, match='identity'):
        read_author(reader, '91')


class SyntheticHTTP:
    def __init__(self):
        self.reads, self.posts, self.following = [], [], {}
        self.unknown = False
        self.get_hook = None
    def get(self, request, timeout, limit, deadline):
        assert request.get_method() == 'GET'
        route = urlsplit(request.full_url)
        self.reads.append(route.path)
        if self.get_hook is not None:
            self.get_hook(route.path)
        if route.path == '/':
            data = {'props': {'pageProps': {'isLoggedIn': True,
                'gaUserData': {'id': '7'}, 'serverSerializedPreloadedState':
                json.dumps({'api': {'token': 'a' * 32}})}}}
            return ('<script id="__NEXT_DATA__" type="application/json">' + json.dumps(data) + '</script>').encode()
        if route.path == '/ajax/user/extra':
            body = {'userId': '7'}
        elif route.path == '/ajax/user/7/following':
            query = parse_qs(route.query)
            restrict = 0 if query['rest'] == ['show'] else 1
            body = {'users': [{'userId': aid, 'userName': 'Author'}
                for aid, visibility in self.following.items() if visibility == restrict]
                if query['offset'] == ['0'] else []}
        else:
            raise AssertionError('unexpected synthetic GET route')
        return json.dumps({'error': False, 'body': body}).encode()
    def post(self, request, timeout, limit, deadline):
        assert request.get_method() == 'POST' and urlsplit(request.full_url).path == '/bookmark_add.php'
        payload = parse_qs(request.data.decode())
        self.posts.append(payload)
        if self.unknown:
            raise TimeoutError('generated uncertain result')
        aid = payload['user_id'][0]
        if payload['mode'] == ['add']:
            self.following[aid] = int(payload['restrict'][0])
        else:
            self.following.pop(aid, None)
        return WriteHTTPResponse(200, b'{"error":false,"body":{}}')


@pytest.fixture
def action_stack(tmp_path, monkeypatch):
    # Standard component-only NiceGUI runtime: no server/browser is started.
    async def connected(self, timeout=None):
        return None
    monkeypatch.setattr(Client, 'connected', connected)
    monkeypatch.setattr(type(core.app), 'is_stopping', property(lambda self: False))
    monkeypatch.setattr(type(core.app), 'is_stopped', property(lambda self: False))
    cookie = tmp_path / 'generated-cookie.txt'
    cookie.write_text('# Netscape HTTP Cookie File\n.pixiv.net\tTRUE\t/\tTRUE\t'
        + str(int(time.time()) + 86400) + '\tPHPSESSID\t7_generated\n')
    original = cookie.read_bytes()
    http = SyntheticHTTP()
    reader = LivePixivReadClient(cookie, transport=http.get)
    writer = PixivWriteClient(reader, transport=http.post)
    actions = PixivActions(reader, writer=writer)
    yield SimpleNamespace(http=http, reader=reader, actions=actions)
    assert actions.close(timeout=2)
    reader.close()
    assert cookie.read_bytes() == original


def token(binding):
    return binding.follow.props['data-action-token']


@pytest.mark.asyncio
@pytest.mark.parametrize('unavailable', [False, True])
async def test_author_level_follow_available_with_zero_or_all_missing_works(action_stack, monkeypatch, unavailable):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    stack = action_stack
    reader = stack.reader
    reader.get_user_works = lambda *a, **kw: ([dict(id='102', user_id='91', is_masked=True)] if unavailable else [])
    svc = services(reader)
    svc.pixiv_actions = stack.actions
    client = Client(ui.page('/_original_author_follow_' + str(unavailable)))
    try:
        with client:
            view = AuthorBrowseView(svc, reader)
            await view.show('91', 'Author')
            buttons = [node for node in view.root.descendants()
                       if node.props.get('data-testid') == 'author-browse-follow']
            assert len(buttons) == 1 and buttons[0].enabled
            assert not stack.http.posts
            assert not view.retry.visible
            if unavailable:
                assert view.missing_previews == {'102'}
                view.gallery.open('102')
                assert not view.gallery.state.viewer_open
            binding = view.follow_actions
            assert not binding.bookmark.visible and not binding.bookmark.enabled
            assert await binding.click('follow', token(binding))
            assert stack.http.following == {'91': 0} and len(stack.http.posts) == 1
            stale = token(binding)
            await view.show('92', 'Other')
            assert not await binding.click('follow', stale)
            fresh = token(binding)
            view.hide()
            assert not await binding.click('follow', fresh)
            assert len(stack.http.posts) == 1
            view.close()
    finally:
        client.delete()


async def unfollowed_view(stack):
    # Production collection logic with generated bookmark rows; the same
    # authenticated reader remains the action owner and the view owner.
    reader = stack.reader
    reader.get_bookmarked_works = lambda *a, **kw: [bookmark_row('91', thumb=False)]
    reader.get_bookmarked_novels = lambda *a, **kw: [bookmark_row('91', '23', thumb=False)]
    svc = SimpleNamespace(live_client=reader, pixiv_actions=stack.actions, closing=False)
    view = UnfollowedAuthorsView(svc)
    view.show()
    assert await view.refresh()
    return view, svc


@pytest.mark.asyncio
async def test_unfollowed_inline_follow_explicit_and_unknown_reconciles_without_resend(action_stack, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    stack = action_stack
    client = Client(ui.page('/_original_unfollowed_action'))
    try:
        with client:
            view, _ = await unfollowed_view(stack)
            buttons = [node for node in view.rows.descendants()
                       if node.props.get('data-testid') == 'unfollowed-91-follow']
            assert len(buttons) == 1 and buttons[0].enabled
            assert not stack.http.posts
            binding = view.follow_actions[0]
            assert not binding.bookmark.visible and not binding.bookmark.enabled
            stack.http.unknown = True
            assert not await binding.click('follow', token(binding))
            assert '結果を確認' in binding.follow.text
            assert len(stack.http.posts) == 1
            stack.http.following['91'] = 1
            assert await binding.click('follow', token(binding))
            assert len(stack.http.posts) == 1 and stack.http.following['91'] == 1
            assert '現在の状態' in binding.notice.text
            view.close()
            assert binding._closed
    finally:
        client.delete()


@pytest.mark.asyncio
@pytest.mark.parametrize('invalidate', ['refresh', 'hide', 'account', 'reader', 'close'])
async def test_unfollowed_stale_row_cannot_send(action_stack, monkeypatch, invalidate):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    stack = action_stack
    client = Client(ui.page('/_original_unfollowed_stale_' + invalidate))
    try:
        with client:
            view, svc = await unfollowed_view(stack)
            binding = view.follow_actions[0]
            stale = token(binding)
            if invalidate == 'refresh':
                assert await view.refresh()
            elif invalidate == 'hide':
                view.hide()
            elif invalidate == 'account':
                stack.reader._verified_identity = '8'
            elif invalidate == 'reader':
                svc.live_client = BookmarkReader()
            else:
                view.close()
            assert not await binding.click('follow', stale)
            assert stack.http.posts == []
            view.close()
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_normal_author_work_viewer_follow_still_uses_the_shared_action_owner(action_stack, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    stack = action_stack
    stack.reader.get_user_works = lambda *a, **kw: [dict(id='103', user_id='91',
        title='Generated', user_name='Author', page_count=1,
        thumbnail_url='https://i.pximg.net/generated.jpg')]
    svc = services(stack.reader)
    svc.pixiv_actions = stack.actions
    client = Client(ui.page('/_original_author_normal_viewer'))
    try:
        with client:
            view = AuthorBrowseView(svc, stack.reader)
            await view.show('91', 'Author')
            view.gallery.open('103')
            binding = view.gallery._pixiv_actions
            assert binding.follow.enabled and binding.bookmark.enabled
            assert await binding.click('follow', token(binding))
            assert stack.http.following == {'91': 0} and len(stack.http.posts) == 1
            assert view.follow_actions.follow.text == 'フォロー解除'
            view.close()
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_unfollowed_click_preserves_an_externally_added_private_follow(action_stack, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    stack = action_stack
    client = Client(ui.page('/_original_unfollowed_private'))
    try:
        with client:
            view, _ = await unfollowed_view(stack)
            binding = view.follow_actions[0]
            stack.http.following['91'] = 1
            assert await binding.click('follow', token(binding))
            assert stack.http.posts == [] and stack.http.following['91'] == 1
            assert '既に' in binding.notice.text
            view.close()
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_unfollowed_action_poll_uses_cached_identity_without_reader_lock(action_stack, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    stack = action_stack
    client = Client(ui.page('/_original_unfollowed_cached_identity'))
    try:
        with client:
            view, _ = await unfollowed_view(stack)
            binding = view.follow_actions[0]
            before = list(stack.http.reads)
            def forbidden_read():
                raise AssertionError('cached UI poll must not take the identity lock')
            monkeypatch.setattr(stack.reader, 'require_identity', forbidden_read)
            binding.hub.refresh()
            assert binding.follow.enabled
            assert stack.http.reads == before and stack.http.posts == []
            view.close()
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_unfollowed_close_during_follow_preflight_does_not_send(action_stack, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    stack = action_stack
    entered, release = Event(), Event()
    client = Client(ui.page('/_original_unfollowed_late'))
    task = None
    try:
        with client:
            view, _ = await unfollowed_view(stack)
            binding = view.follow_actions[0]
            def hold(path):
                if path.endswith('/following'):
                    entered.set()
                    assert release.wait(3)
            stack.http.get_hook = hold
            task = asyncio.create_task(binding.click('follow', token(binding)))
            assert await asyncio.to_thread(entered.wait, 2)
            view.close()
            release.set()
            assert not await task
            assert stack.http.posts == [] and binding._closed
    finally:
        release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        client.delete()
