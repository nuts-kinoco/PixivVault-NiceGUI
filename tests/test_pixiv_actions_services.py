"""Application ownership and shutdown order for the shared Pixiv action service."""
from __future__ import annotations

import asyncio
from threading import Event, Timer
from types import SimpleNamespace

import pytest

from pixivvault_nicegui import pixiv_actions as action_module
from pixivvault_nicegui.following_sync import FollowingSyncStore, FollowingSyncError
from pixivvault_nicegui.pixiv_read_client import LivePixivReadClient
from pixivvault_nicegui.pixiv_write_client import PixivWriteClient
from pixivvault_nicegui.services import ApplicationServices, get_services
from test_pixiv_actions import SyntheticHTTP, intent


@pytest.fixture
def environment(tmp_path, monkeypatch):
    import time
    cookie = tmp_path / 'synthetic-service-cookie.txt'
    cookie.write_text('# Netscape HTTP Cookie File\n.pixiv.net\tTRUE\t/\tTRUE\t'
                      + str(int(time.time()) + 86400) + '\tPHPSESSID\t55_synthetic\n')
    before = cookie.read_bytes()
    http = SyntheticHTTP()
    readers = []
    def new_reader():
        owner = LivePixivReadClient(cookie, transport=http.get)
        readers.append(owner)
        return owner
    reader = new_reader()
    created_actions, created_services = [], []
    actual_type = action_module.PixivActions
    root = tmp_path / 'following'
    def factory(borrowed, *, following_store=None):
        assert borrowed in readers
        owner = actual_type(borrowed,
            writer=PixivWriteClient(borrowed, transport=http.post),
            following_store=following_store)
        created_actions.append(owner)
        return owner
    monkeypatch.setattr(action_module, 'PixivActions', factory)
    def build(*, with_following=True, **kwargs):
        kwargs.setdefault('live_client', reader)
        kwargs.setdefault('pixiv_actions_mode', 'enabled')
        if with_following:
            kwargs.setdefault('following_sync_root', root)
        service = ApplicationServices(**kwargs)
        created_services.append(service)
        return service
    yield SimpleNamespace(build=build, reader=reader, http=http, root=root,
        created_actions=created_actions, created_services=created_services,
        actions_type=actual_type, path=tmp_path, new_reader=new_reader)
    for service in created_services:
        if not service.closed:
            service.close()
    for owner in created_actions:
        assert owner.close(timeout=2)
    for reader in readers:
        reader.close()
    assert cookie.read_bytes() == before


def test_one_application_owner_is_shared_by_two_client_handles_without_get(environment):
    env = environment
    app = SimpleNamespace(state=SimpleNamespace())
    first = get_services(app=app, live_client=env.reader, following_sync_root=env.root,
                         pixiv_actions_mode='enabled')
    env.created_services.append(first)
    second = get_services(app=app, live_client=env.reader, following_sync_root=env.root,
                          pixiv_actions_mode='enabled')
    client_a, client_b = SimpleNamespace(services=first), SimpleNamespace(services=second)
    assert client_a.services is client_b.services
    owner = first.pixiv_actions
    assert owner is client_b.services.pixiv_actions
    assert isinstance(owner, env.actions_type) and len(env.created_actions) == 1
    assert owner._following is first.following_sync
    assert owner.cached_verified_account_id is None
    assert env.http.reads == env.http.posts == []


@pytest.mark.parametrize('mode', ('synthetic', 'duck_reader'))
def test_only_actual_live_reader_constructs_writer_service(mode):
    closed = []
    fake = SimpleNamespace(close=lambda: closed.append(True))
    service = ApplicationServices(live_client=fake if mode == 'duck_reader' else None,
                                  pixiv_actions_mode='enabled')
    try:
        assert service.pixiv_actions is None
    finally:
        service.close()
    assert closed == ([True] if mode == 'duck_reader' else [])


@pytest.mark.asyncio
async def test_sync_service_close_drains_post_and_local_commit_before_borrowed_owners(environment, monkeypatch):
    env = environment
    service = env.build()
    actions, store = service.pixiv_actions, service.following_sync
    started, release = Event(), Event()
    order, captured = [], []
    def delayed_post():
        started.set()
        assert release.wait(3)
    env.http.post_hook = delayed_post
    original_store_close, original_reader_close = store.close, env.reader.close
    def store_close():
        result = actions.result('service-follow-close')
        assert result is not None and result.status == 'confirmed'
        assert actions.active_count == 0 and not env.reader._closed
        captured.append(store.snapshot('55'))
        order.append('store')
        original_store_close()
    def reader_close():
        if not env.reader._closed:
            assert actions.active_count == 0 and order == ['store']
            order.append('reader')
        original_reader_close()
    monkeypatch.setattr(store, 'close', store_close)
    monkeypatch.setattr(env.reader, 'close', reader_close)
    task = asyncio.create_task(actions.execute(
        intent('service-follow-close', action='follow', target='91'),
        author_name='Synthetic service author'))
    assert await asyncio.to_thread(started.wait, 2)
    timer = Timer(.03, release.set)
    timer.start()
    try:
        service.close()  # UI loop is blocked here; worker must finish independently.
    finally:
        release.set()
        timer.join(2)
    assert service.closed and service.closing
    assert order == ['store', 'reader']
    assert len(captured) == 1 and captured[0].following[0].user_id == '91'
    assert captured[0].last_synced_at is None
    assert (await task).status == 'confirmed'
    assert len(env.http.posts) == 1
    assert len(env.http.reads) == env.http.read_count_at_post
    reopened = FollowingSyncStore(env.root)
    try:
        assert reopened.snapshot('55') == captured[0]
    finally:
        reopened.close()


@pytest.mark.parametrize('failure', ('false', 'unverified', 'exception'))
def test_failed_action_shutdown_keeps_borrowed_owners_and_allows_close_retry(environment, monkeypatch, failure):
    env = environment
    service = env.build()
    actions, store = service.pixiv_actions, service.following_sync
    def simulated_close(**_kwargs):
        if failure == 'exception':
            raise OSError('synthetic private close error')
        return failure != 'false'
    with monkeypatch.context() as scoped:
        scoped.setattr(actions, 'close', simulated_close)
        scoped.setattr(actions, '_shutdown_unverified', failure == 'unverified')
        with pytest.raises(RuntimeError, match='^Pixiv action shutdown unverified$') as captured:
            service.close()
        assert 'private' not in str(captured.value)
        assert service.closing and not service.closed
        assert service.pixiv_actions is actions
        assert service.following_sync is store and service.live_client is env.reader
        assert store.snapshot('55').revision == 0
        assert env.reader.require_identity() == '55'
        assert env.http.posts == []
    service.close()
    assert service.closed and env.reader._closed
    with pytest.raises(FollowingSyncError):
        store.snapshot('55')


def test_later_constructor_failure_closes_actions_then_borrowed_store_and_reader(environment, monkeypatch):
    env = environment
    from pixivvault_nicegui import discovery_learning
    def fail_discovery(*_args, **_kwargs):
        assert len(env.created_actions) == 1
        raise ValueError('synthetic discovery construction failure')
    monkeypatch.setattr(discovery_learning, 'DiscoveryLearningService', fail_discovery)
    with pytest.raises(ValueError, match='synthetic discovery construction failure'):
        env.build(preferences_root=env.path / 'preferences',
                  discovery_root=env.path / 'discovery')
    assert len(env.created_actions) == 1
    owner = env.created_actions[0]
    assert owner.active_count == 0 and not owner.shutdown_unverified
    assert owner.cached_verified_account_id is None and env.reader._closed
    with pytest.raises(FollowingSyncError):
        owner._following.snapshot('55')
    reopened = FollowingSyncStore(env.root)
    try:
        assert reopened.snapshot('55').revision == 0
    finally:
        reopened.close()
    assert env.http.reads == env.http.posts == []


@pytest.mark.asyncio
async def test_follow_without_local_store_reports_warning_for_all_proved_remote_outcomes(environment):
    env = environment
    service = env.build(with_following=False)
    actions = service.pixiv_actions
    assert service.following_sync is None and env.http.reads == env.http.posts == []
    confirmed = await actions.execute(intent('no-store-follow', action='follow', target='91'))
    assert confirmed.status == 'confirmed' and confirmed.remote_state is True
    assert confirmed.local_warning
    already = await actions.execute(intent('no-store-already', action='follow', target='91'))
    assert already.status == 'already' and already.remote_state is True and already.local_warning
    env.http.post_answer = TimeoutError('synthetic unknown')
    request = intent('no-store-unfollow', action='unfollow', target='91')
    assert (await actions.execute(request)).status == 'indeterminate'
    observed = await actions.reconcile(request)
    assert observed.status == 'observed' and observed.remote_state is True
    assert observed.local_warning
    assert len(env.http.posts) == 2
