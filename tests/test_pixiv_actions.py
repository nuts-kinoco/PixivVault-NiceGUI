"""Explicit app-shared actions; all HTTP and credentials are synthetic."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from threading import Event, Timer
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from pixivvault_nicegui.following_sync import FollowingSyncStore
from pixivvault_nicegui.pixiv_actions import (
    PixivActions, ActionBusy, ActionConflict, ActionNeedsReconciliation,
)
from pixivvault_nicegui.pixiv_read_client import LivePixivReadClient, TransportFailed
from pixivvault_nicegui.pixiv_write_client import PixivWriteClient, WriteIntent, WriteHTTPResponse
from pixivvault_nicegui.preferences import RequestPreferences
from pixivvault_nicegui.request_policy import RequestRetryPolicy


class SyntheticHTTP:
    def __init__(self):
        self.account = '55'
        self.bookmark = None
        self.author = '91'
        self.following = {}
        self.reads = []
        self.posts = []
        self.get_hook = None
        self.post_hook = None
        self.post_answer = None
        self.read_count_at_post = None

    def get(self, request, timeout, limit, deadline):
        assert request.get_method() == 'GET'
        route = urlsplit(request.full_url)
        self.reads.append((route.path, route.query))
        if self.get_hook is not None:
            self.get_hook(route)
        if route.path == '/':
            data = {'props': {'pageProps': {
                'isLoggedIn': True, 'gaUserData': {'id': self.account},
                'serverSerializedPreloadedState': json.dumps({'api': {'token': 'a' * 32}}),
            }}}
            return ('<script id="__NEXT_DATA__" type="application/json">'
                    + json.dumps(data) + '</script>').encode()
        if route.path == '/ajax/user/extra':
            body = {'userId': self.account}
        elif route.path.startswith(('/ajax/illust/', '/ajax/novel/')):
            body = {'id': route.path.rsplit('/', 1)[-1], 'userId': self.author,
                    'bookmarkData': self.bookmark, 'title': 'Synthetic',
                    'content': 'Synthetic novel text'}
        elif route.path == '/ajax/user/55/following':
            query = parse_qs(route.query)
            visibility = 0 if query['rest'] == ['show'] else 1
            users = [] if query['offset'] != ['0'] else [
                {'userId': uid, 'userName': 'Synthetic author'}
                for uid, restrict in self.following.items() if restrict == visibility
            ]
            body = {'users': users}
        else:
            raise AssertionError('unexpected synthetic GET')
        return json.dumps({'error': False, 'body': body}).encode()

    def post(self, request, timeout, limit, deadline):
        assert request.get_method() == 'POST'
        self.posts.append(request)
        self.read_count_at_post = len(self.reads)
        if self.post_hook is not None:
            self.post_hook()
        if isinstance(self.post_answer, BaseException):
            raise self.post_answer
        if self.post_answer is not None:
            return self.post_answer
        route = urlsplit(request.full_url).path
        if route.endswith('/bookmarks/add'):
            payload = json.loads(request.data)
            self.bookmark = {'id': '888', 'private': bool(payload['restrict'])}
            return WriteHTTPResponse(200, b'{"error":false,"body":{"last_bookmark_id":"888"}}')
        if route.endswith('/bookmarks/delete'):
            self.bookmark = None
        elif route == '/bookmark_add.php':
            payload = parse_qs(request.data.decode())
            uid = payload['user_id'][0]
            if payload['mode'] == ['add']:
                self.following[uid] = int(payload['restrict'][0])
            else:
                self.following.pop(uid, None)
        else:
            raise AssertionError('unexpected synthetic POST')
        return WriteHTTPResponse(200, b'{"error":false,"body":{}}')


def intent(op='click-1', *, action='bookmark_add', target='123', kind='illust', restrict=0):
    return WriteIntent(op, '55', target, action, kind, '91', restrict)


@pytest.fixture
def stack(tmp_path):
    cookie = tmp_path / 'synthetic-cookie.txt'
    cookie.write_text('# Netscape HTTP Cookie File\n.pixiv.net\tTRUE\t/\tTRUE\t'
                      + str(int(time.time()) + 86400) + '\tPHPSESSID\t55_synthetic\n')
    original = cookie.read_bytes()
    http = SyntheticHTTP()
    reader = LivePixivReadClient(cookie, transport=http.get)
    writer = PixivWriteClient(reader, transport=http.post)
    following = FollowingSyncStore(tmp_path / 'owned-following')
    actions = PixivActions(reader, writer=writer, following_store=following)
    yield SimpleNamespace(actions=actions, writer=writer, reader=reader,
                          following=following, http=http)
    assert actions.close(timeout=2)
    following.close()
    reader.close()
    assert cookie.read_bytes() == original


@pytest.mark.asyncio
async def test_constructor_and_cached_state_do_not_fetch_then_explicit_verify(stack):
    a, http = stack.actions, stack.http
    assert a.cached_verified_account_id is None
    state = a.state('55', 'illust', '123')
    assert not state.pending and not state.unknown and state.observed is None
    assert state.revision == 0 and http.reads == http.posts == []
    assert await a.verify_account(timeout=2) == '55'
    assert a.cached_verified_account_id == '55'
    before = tuple(http.reads)
    assert a.state('55', 'illust', '123') == state
    assert tuple(http.reads) == before and http.posts == []


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ('illust', 'novel'))
async def test_fresh_process_add_does_not_turn_existing_private_bookmark_public(stack, kind):
    stack.http.bookmark = {'id': '777', 'private': True}
    request = intent(kind=kind, restrict=0)
    result = await stack.actions.execute(request)
    assert result.status == 'already' and result.remote_state is True
    assert result.write_result is None and result.matches(request)
    assert stack.http.posts == [] and stack.http.bookmark['private'] is True
    assert stack.actions.state('55', kind, '123').observed is True
    with pytest.raises(ActionConflict):
        await stack.actions.execute(replace(request, restrict=1))
    assert stack.http.posts == []


@pytest.mark.asyncio
async def test_new_add_fixed_intent_result_and_replay_are_correlated(stack):
    request = intent()
    result = await stack.actions.execute(request)
    assert result.status == 'confirmed' and result.remote_state is True
    assert result.write_result.confirmed and result.matches(request)
    assert not result.matches(replace(request, author_id='92'))
    assert not result.matches(replace(request, restrict=1))
    assert await stack.actions.execute(request) == result
    assert len(stack.http.posts) == 1


@pytest.mark.asyncio
async def test_cached_account_tracks_shared_reader_without_get_or_reader_lock(stack, monkeypatch):
    stack.http.bookmark = {'id': '777', 'private': True}
    assert (await stack.actions.execute(intent('observed-before-auth-change'))).status == 'already'
    assert stack.actions.cached_verified_account_id == '55'
    assert stack.actions.state('55', 'illust', '123').observed is True
    reads = tuple(stack.http.reads)

    class NoIdentityLock:
        def __enter__(self):
            pytest.fail('cached getter must not wait for the shared reader identity lock')
        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(stack.reader, '_identity_lock', NoIdentityLock())
    monkeypatch.setattr(stack.reader, '_verified_identity', None)
    assert stack.actions.cached_verified_account_id is None
    monkeypatch.setattr(stack.reader, '_verified_identity', '56')
    assert stack.actions.cached_verified_account_id == '56'
    assert stack.actions.state('56', 'illust', '123').observed is None
    monkeypatch.setattr(stack.reader, '_verified_identity', 'invalid-account')
    assert stack.actions.cached_verified_account_id is None
    monkeypatch.setattr(stack.reader, '_verified_identity', '56')
    monkeypatch.setattr(stack.reader, '_closed', True)
    assert stack.actions.cached_verified_account_id is None
    assert tuple(stack.http.reads) == reads and stack.http.posts == []


@pytest.mark.asyncio
async def test_stale_confirmed_cache_does_not_block_new_explicit_add_after_external_removal(stack):
    assert (await stack.actions.execute(intent('first'))).status == 'confirmed'
    stack.http.bookmark = None
    assert (await stack.actions.execute(intent('second'))).status == 'confirmed'
    assert len(stack.http.posts) == 2


@pytest.mark.asyncio
async def test_unknown_blocks_new_id_without_get_or_post_until_explicit_reconciliation(stack):
    request = intent('unknown')
    stack.http.post_answer = TimeoutError('synthetic secret must not leak')
    result = await stack.actions.execute(request)
    assert result.status == 'indeterminate' and result.remote_state is None
    assert stack.actions.state('55', 'illust', '123').unknown
    reads = tuple(stack.http.reads)
    with pytest.raises(ActionNeedsReconciliation):
        await stack.actions.execute(intent('new-click'))
    assert tuple(stack.http.reads) == reads and len(stack.http.posts) == 1
    stack.http.bookmark = {'id': '888', 'private': True}
    observed = await stack.actions.reconcile(request)
    assert observed.status == 'observed' and observed.remote_state is True
    assert stack.actions.result('unknown').status == 'indeterminate'
    state = stack.actions.state('55', 'illust', '123')
    assert not state.pending and not state.unknown and state.observed is True
    assert (await stack.actions.execute(intent('after-observation'))).status == 'already'
    assert len(stack.http.posts) == 1 and 'secret' not in repr(observed)


@pytest.mark.asyncio
@pytest.mark.parametrize(('action', 'present', 'expected_posts'), (
    ('follow', True, 0), ('follow', False, 1),
    ('unfollow', False, 0), ('unfollow', True, 1),
))
async def test_follow_is_fixed_desired_state_never_fresh_get_toggle(stack, action, present, expected_posts):
    if present:
        stack.http.following['91'] = 1
    request = intent(action=action, target='91')
    result = await stack.actions.execute(request, author_name='Captured author')
    desired = action == 'follow'
    assert result.remote_state is desired
    assert result.status == ('confirmed' if expected_posts else 'already')
    assert len(stack.http.posts) == expected_posts
    rows = stack.following.snapshot('55').authors
    if desired:
        row = next(row for row in rows if row.user_id == '91')
        assert row.is_following and row.restrict == (1 if present else 0)


@pytest.mark.asyncio
async def test_follow_local_single_row_preserves_flags_and_has_no_post_success_full_sync(stack):
    store = stack.following
    store.record_followed('55', '91', 'Original author', 1)
    store._connection.execute("UPDATE following_authors SET is_favorite=1,is_zipped=1,last_downloaded='old' "
                              "WHERE account_id='55' AND user_id='91'")
    store.record_unfollowed('55', '91')
    result = await stack.actions.execute(intent('follow', action='follow', target='91'))
    assert result.status == 'confirmed' and not result.local_warning
    row = store.snapshot('55').authors[0]
    assert row.is_following and row.is_favorite and row.is_zipped and row.last_downloaded == 'old'
    assert store.snapshot('55').last_synced_at is None
    assert len(stack.http.reads) == stack.http.read_count_at_post
    result = await stack.actions.execute(intent('unfollow', action='unfollow', target='91'))
    assert result.status == 'confirmed' and not result.local_warning
    row = store.snapshot('55').authors[0]
    assert not row.is_following and row.is_favorite and row.is_zipped and row.last_downloaded == 'old'
    assert len(stack.http.reads) == stack.http.read_count_at_post


@pytest.mark.asyncio
async def test_remote_follow_success_survives_local_commit_failure_without_repost(stack, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError('synthetic private exception text')
    monkeypatch.setattr(stack.following, 'record_followed', fail)
    request = intent(action='follow', target='91')
    result = await stack.actions.execute(request)
    assert result.status == 'confirmed' and result.remote_state is True and result.local_warning
    assert result.write_result.confirmed and len(stack.http.posts) == 1
    assert 'private exception' not in repr(result)
    assert await stack.actions.execute(request) == result
    assert len(stack.http.posts) == 1


@pytest.mark.asyncio
async def test_cross_client_target_is_busy_during_preflight(stack):
    started, release = Event(), Event()
    def hold(route):
        if route.path == '/ajax/illust/123':
            started.set()
            assert release.wait(3)
    stack.http.get_hook = hold
    first = asyncio.create_task(stack.actions.execute(intent('client-a')))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        assert stack.actions.state('55', 'illust', '123').pending
        with pytest.raises(ActionBusy):
            await stack.actions.execute(intent('client-b'))
        assert stack.http.posts == []
    finally:
        release.set()
    assert (await first).status == 'confirmed'
    assert len(stack.http.posts) == 1


@pytest.mark.asyncio
async def test_cancel_during_writer_csrf_prevents_actual_post_without_closing_reader(stack):
    cancelled = Event()
    homes = 0
    def cancel_csrf(route):
        nonlocal homes
        if route.path == '/':
            homes += 1
            if homes == 2:
                cancelled.set()
    stack.http.get_hook = cancel_csrf
    result = await stack.actions.execute(intent(), cancelled=cancelled.is_set)
    assert result.status == 'cancelled' and stack.http.posts == []
    assert not stack.actions.state('55', 'illust', '123').unknown
    assert stack.reader.require_identity() == '55'


@pytest.mark.asyncio
async def test_cancel_after_dispatch_drains_result_and_single_row_commit(stack):
    started, release = Event(), Event()
    def hold():
        started.set()
        assert release.wait(3)
    stack.http.post_hook = hold
    request = intent('late-follow', action='follow', target='91')
    task = asyncio.create_task(stack.actions.execute(request))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and stack.actions.state('55', 'user', '91').pending
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    result = stack.actions.result('late-follow')
    assert result.status == 'confirmed' and not result.local_warning
    assert stack.following.snapshot('55').following[0].user_id == '91'
    assert stack.reader.require_identity() == '55' and len(stack.http.posts) == 1


@pytest.mark.asyncio
async def test_sync_close_drains_worker_without_needing_ui_loop_completion(stack):
    started, release = Event(), Event()
    def hold():
        started.set()
        assert release.wait(3)
    stack.http.post_hook = hold
    task = asyncio.create_task(stack.actions.execute(intent('closing')))
    assert await asyncio.to_thread(started.wait, 2)
    timer = Timer(.03, release.set)
    timer.start()
    try:
        assert stack.actions.close(timeout=2)
    finally:
        release.set()
        timer.join(2)
    assert stack.actions.result('closing').status == 'confirmed'
    assert stack.actions.active_count == 0 and not stack.actions.shutdown_unverified
    assert (await task).status == 'confirmed'
    assert stack.reader.require_identity() == '55'


@pytest.mark.asyncio
async def test_preflight_get_has_no_retry_and_never_dispatches_on_failure(stack):
    attempts = []
    def fail(route):
        if route.path == '/ajax/illust/123':
            attempts.append(route.path)
            raise TransportFailed()
    stack.http.get_hook = fail
    stack.reader.set_retry_policy(RequestRetryPolicy(
        lambda: RequestPreferences(.3, 3, 3.0),
        sleep=lambda _: pytest.fail('action GET must not retry'),
    ))
    result = await stack.actions.execute(intent())
    assert result.status == 'rejected' and stack.http.posts == []
    assert attempts == ['/ajax/illust/123']


@pytest.mark.asyncio
async def test_account_switch_between_selection_and_csrf_is_never_posted(stack):
    def switch(route):
        if route.path == '/ajax/illust/123':
            stack.http.account = '56'
    stack.http.get_hook = switch
    result = await stack.actions.execute(intent())
    assert result.status == 'rejected' and stack.http.posts == []


@pytest.mark.asyncio
async def test_post_completion_does_not_overwrite_newer_following_revision(stack):
    newer = []
    def another_client_commits():
        # This later local commit must survive late public POST completion.
        newer.append(stack.following.record_followed('55', '91', 'Newer state', 1))
    stack.http.post_hook = another_client_commits
    result = await stack.actions.execute(intent('public-follow', action='follow', target='91'))
    assert result.status == 'confirmed' and result.remote_state is True and result.local_warning
    assert result.write_result.confirmed and stack.http.following['91'] == 0
    assert stack.following.snapshot('55') == newer[0]
    assert newer[0].authors[0].restrict == 1
    assert len(stack.http.posts) == 1
    assert len(stack.http.reads) == stack.http.read_count_at_post


@pytest.mark.asyncio
async def test_missing_local_revision_never_becomes_unconditional_follow_write(stack, monkeypatch):
    def unavailable(_account):
        raise OSError('synthetic local snapshot failure')
    def unexpected(*args, **kwargs):
        pytest.fail('no unguarded local commit after failed revision read')
    monkeypatch.setattr(stack.following, 'snapshot', unavailable)
    monkeypatch.setattr(stack.following, 'record_followed', unexpected)
    result = await stack.actions.execute(intent('follow-without-local-proof', action='follow', target='91'))
    assert result.status == 'confirmed' and result.local_warning
    assert len(stack.http.posts) == 1


@pytest.mark.asyncio
async def test_deadline_scope_reaches_actual_get_and_expired_response_cannot_post(stack):
    original = stack.reader._transport
    deadlines = []
    expired = []
    def delayed(request, timeout, limit, deadline):
        deadlines.append(deadline)
        if urlsplit(request.full_url).path == '/ajax/illust/123':
            expired.append(True)
            time.sleep(max(0, deadline - time.monotonic()) + .02)
        return original(request, timeout, limit, deadline)
    stack.reader._transport = delayed
    result = await stack.actions.execute(intent('bounded-get'), timeout=1)
    assert expired == [True]
    assert len(deadlines) >= 3 and len(set(deadlines)) == 1
    assert result.status == 'rejected' and result.reason == 'pixiv_action_deadline'
    assert stack.http.posts == []


@pytest.mark.asyncio
async def test_unknown_follow_reconciles_private_state_without_reposting(stack):
    request = intent('unknown-follow', action='follow', target='91', restrict=0)
    stack.http.post_answer = TimeoutError('synthetic unknown response')
    assert (await stack.actions.execute(request)).status == 'indeterminate'
    stack.http.following['91'] = 1
    result = await stack.actions.reconcile(request, author_name='Captured author')
    assert result.status == 'observed' and result.remote_state is True
    assert not result.local_warning and result.write_result.status == 'indeterminate'
    row = stack.following.snapshot('55').following[0]
    assert row.user_id == '91' and row.restrict == 1
    assert stack.following.snapshot('55').last_synced_at is None
    assert stack.actions.result('unknown-follow').status == 'indeterminate'
    assert not stack.actions.state('55', 'user', '91').unknown
    assert len(stack.http.posts) == 1
