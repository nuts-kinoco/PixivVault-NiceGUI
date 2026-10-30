"""S03 actual controls/lifecycle; all account and transport data are synthetic."""
import asyncio
from threading import Event, get_ident
from types import SimpleNamespace
from uuid import uuid4

import pytest
from nicegui import Client, core, ui

from pixivvault_nicegui.following_sync import FollowingSyncStore
from pixivvault_nicegui.ui.following_sync_view import FollowingSyncView


def author(uid='41', name='Alpha'):
    return dict(user_id=uid, name=name, account='synthetic_author',
                profile_img='https://i.pximg.net/user-profile/offline/avatar.png')


class Reader:
    def __init__(self, public=(), private=(), *, account_id='9001', verified=True, hook=None):
        self.public, self.private = tuple(public), tuple(private)
        self.account_id, self.hook = account_id, hook
        self._verified_identity = account_id if verified else None
        self._closed = False
        self.calls, self.threads, self.auth_calls = [], set(), 0

    def require_identity(self):
        self.threads.add(get_ident())
        self.auth_calls += 1
        self._verified_identity = self.account_id
        return self.account_id

    def get_following_users_detailed(self, account_id, rest_type='show'):
        assert account_id == self.account_id
        self.threads.add(get_ident())
        self.calls.append(rest_type)
        if self.hook is not None:
            self.hook(rest_type)
        return self.public if rest_type == 'show' else self.private


def runtime(monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    async def connected(self, timeout=None):
        return None
    monkeypatch.setattr(Client, 'connected', connected)
    monkeypatch.setattr(type(core.app), 'is_stopping', property(lambda self: False))
    monkeypatch.setattr(type(core.app), 'is_stopped', property(lambda self: False))


async def mount(services, *, observe=False, **kwargs):
    client = Client(ui.page('/_following_' + uuid4().hex))
    with client:
        view = FollowingSyncView(services, **kwargs)
        host = ui.column()
        with host:
            view.build()
        if not observe:
            view._timer.active = False
        await view._initial_task
    return client, view, host


def names(view):
    return tuple(row.default_slot.children[0].text for row in view.rows.default_slot.children)


async def dispose(client, view):
    view.close()
    tasks = {task for task in (view._initial_task, view._worker) if task is not None}
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    await asyncio.sleep(0)
    if client.id in Client.instances:
        client.delete()


async def eventually(predicate, timeout=4):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, 'observer/worker did not finish'
        await asyncio.sleep(.02)


@pytest.mark.asyncio
async def test_only_explicit_sync_verifies_identity_and_zero_is_not_unsynced(tmp_path, monkeypatch):
    runtime(monkeypatch)
    store = FollowingSyncStore(tmp_path / 'following')
    reader = Reader(verified=False)
    services = SimpleNamespace(following_sync=store, live_client=reader, closing=False)
    client, view, host = await mount(services)
    try:
        assert reader.calls == [] and reader.auth_calls == 0
        assert view.snapshot is None and '本人確認' in view.summary.text
        await view.refresh()
        assert reader.calls == [] and reader.auth_calls == 0
        assert await view.sync_now()
        assert reader.calls == ['show', 'hide']
        assert reader.threads and get_ident() not in reader.threads
        assert view.snapshot.last_synced_at is not None and view.snapshot.following == ()
        assert '0人' in view.summary.text and '未同期' not in view.synced_at.text
        previous = view.snapshot
        calls, auth = tuple(reader.calls), reader.auth_calls
        with client:
            view.deactivate()
            host.clear()
            with host:
                view.build()
            view._timer.active = False
            await view._initial_task
        assert view.snapshot == previous and '0人' in view.summary.text
        assert tuple(reader.calls) == calls and reader.auth_calls == auth
    finally:
        await dispose(client, view)
        store.close()


@pytest.mark.asyncio
async def test_private_failure_keeps_list_and_time_and_busy_until_local_recovery(tmp_path, monkeypatch):
    runtime(monkeypatch)
    store = FollowingSyncStore(tmp_path / 'following')
    reader = Reader(public=(author(),), private=(author('42', 'Private author'),))
    services = SimpleNamespace(following_sync=store, live_client=reader, closing=False)
    client, view, unused = await mount(services)
    operation = None
    failed, entered, release = Event(), Event(), Event()
    original_snapshot = store.snapshot
    try:
        assert 'まだ同期していません' in view.summary.text
        assert await view.sync_now()
        before = view.snapshot
        old_names, old_time = names(view), view.synced_at.text
        reader.public = (author('77', 'Partial replacement'),)
        reader.calls.clear()
        def fail_private(visibility):
            if visibility == 'hide':
                failed.set()
                raise RuntimeError('RAW_PRIVATE_RESPONSE_MUST_NOT_APPEAR')
        reader.hook = fail_private
        def delayed_local_read(account):
            if failed.is_set():
                entered.set()
                assert release.wait(5)
            return original_snapshot(account)
        monkeypatch.setattr(store, 'snapshot', delayed_local_read)
        operation = asyncio.create_task(view.sync_now())
        assert await asyncio.to_thread(entered.wait, 5)
        assert view._busy and not view.sync_button.enabled
        assert not await view.sync_now(), 'old failure must keep its operation gate during recovery'
        assert reader.calls == ['show', 'hide']
        release.set()
        assert not await operation
        assert original_snapshot('9001') == before and view.snapshot == before
        assert names(view) == old_names and view.synced_at.text == old_time
        assert '同期できませんでした' in view.status.text
        assert 'RAW_PRIVATE_RESPONSE' not in view.status.text
        assert view.sync_button.enabled and not view._busy
    finally:
        release.set()
        if operation is not None:
            await asyncio.gather(operation, return_exceptions=True)
        await dispose(client, view)
        store.close()


@pytest.mark.asyncio
async def test_concurrent_local_change_wins_and_conflict_does_not_retry_get(tmp_path, monkeypatch):
    runtime(monkeypatch)
    store = FollowingSyncStore(tmp_path / 'following')
    reader = Reader(public=(author(),))
    services = SimpleNamespace(following_sync=store, live_client=reader, closing=False)
    client, view, unused = await mount(services)
    entered, release = Event(), Event()
    operation = None
    try:
        assert await view.sync_now()
        before = view.snapshot
        reader.calls.clear()
        def block_private(visibility):
            if visibility == 'hide':
                entered.set()
                assert release.wait(5)
        reader.hook = block_private
        operation = asyncio.create_task(view.sync_now())
        assert await asyncio.to_thread(entered.wait, 5)
        winner = await asyncio.to_thread(store.record_followed, '9001', '73', 'Other client',
                                         expected_revision=before.revision)
        release.set()
        assert not await operation
        assert store.snapshot('9001') == winner and view.snapshot == winner
        assert set(names(view)) == {'Alpha', 'Other client'}
        assert '別の画面で一覧が更新' in view.status.text
        assert reader.calls == ['show', 'hide']
    finally:
        release.set()
        if operation is not None:
            await asyncio.gather(operation, return_exceptions=True)
        await dispose(client, view)
        store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['deactivate', 'rebuild', 'delete'])
async def test_pending_sync_cannot_publish_after_view_generation_ends(tmp_path, monkeypatch, action):
    runtime(monkeypatch)
    store = FollowingSyncStore(tmp_path / 'following')
    before = store.sync(Reader(public=(author(),)))
    entered, release = Event(), Event()
    def block_private(visibility):
        if visibility == 'hide':
            entered.set()
            assert release.wait(5)
    reader = Reader(public=(author('77', 'Late result'),), hook=block_private)
    services = SimpleNamespace(following_sync=store, live_client=reader, closing=False)
    client, view, host = await mount(services)
    operation = None
    try:
        operation = asyncio.create_task(view.sync_now())
        assert await asyncio.to_thread(entered.wait, 5)
        with client:
            if action == 'delete':
                client.delete()
                assert view._closed and view._timer._is_canceled
            else:
                view.deactivate()
                assert not view._timer.active
                if action == 'rebuild':
                    host.clear()
                    with host:
                        view.build()
                    view._timer.active = False
                    assert view._busy and not view.sync_button.enabled
                    assert not await view.sync_now()
        release.set()
        assert not await operation
        assert store.snapshot('9001') == before
        assert not store._closed and not reader._closed
        assert reader.calls == ['show', 'hide']
        if action == 'rebuild':
            await view._initial_task
            await view.refresh()
            assert view.snapshot == before and names(view) == ('Alpha',)
            assert not view._busy and '停止を待っています' not in view.status.text
            assert '同期完了' not in view.status.text
    finally:
        release.set()
        if operation is not None:
            await asyncio.gather(operation, return_exceptions=True)
        await dispose(client, view)
        store.close()


@pytest.mark.asyncio
async def test_other_client_receives_timer_update_without_get_and_delete_stops_observer(tmp_path, monkeypatch):
    runtime(monkeypatch)
    store = FollowingSyncStore(tmp_path / 'following')
    reader = Reader(public=(author(),))
    services = SimpleNamespace(following_sync=store, live_client=reader, closing=False)
    reads = []
    original_snapshot = store.snapshot
    def observed(account):
        reads.append(account)
        return original_snapshot(account)
    monkeypatch.setattr(store, 'snapshot', observed)
    client_a, view_a, unused = await mount(services, observe=True)
    client_b, view_b, unused = await mount(services, observe=True)
    try:
        assert reader.calls == [] and reader.auth_calls == 0
        assert await view_a.sync_now()
        expected = view_a.snapshot
        await eventually(lambda: view_b.snapshot == expected and names(view_b) == ('Alpha',))
        assert view_b.synced_at.text == view_a.synced_at.text
        assert reader.calls == ['show', 'hide']
        view_a.deactivate()
        await eventually(lambda: view_a._reading == 0 and view_b._reading == 0)
        client_b.delete()
        assert view_b._closed and view_b._timer._is_canceled
        count = len(reads)
        await asyncio.sleep(1.2)
        assert len(reads) == count, 'inactive/deleted views must stop local observation'
        assert not store._closed and not reader._closed
    finally:
        await dispose(client_a, view_a)
        await dispose(client_b, view_b)
        store.close()


@pytest.mark.asyncio
async def test_get_permission_is_checked_without_implicit_authentication(tmp_path, monkeypatch):
    runtime(monkeypatch)
    store = FollowingSyncStore(tmp_path / 'following')
    reader = Reader(verified=False)
    services = SimpleNamespace(following_sync=store, live_client=reader, closing=False)
    permit = [False]
    client, view, unused = await mount(services, allow_get=lambda: permit[0])
    try:
        assert not view.sync_button.enabled and not await view.sync_now()
        assert reader.calls == [] and reader.auth_calls == 0
        permit[0] = True
        await view.refresh()
        assert view.sync_button.enabled and reader.calls == [] and reader.auth_calls == 0
        assert await view.sync_now()
        calls, auth = tuple(reader.calls), reader.auth_calls
        permit[0] = False
        await view.refresh()
        assert not view.sync_button.enabled and not await view.sync_now()
        assert tuple(reader.calls) == calls and reader.auth_calls == auth
    finally:
        await dispose(client, view)
        store.close()


@pytest.mark.asyncio
async def test_account_replacement_clears_old_rows_and_drops_pending_result(tmp_path, monkeypatch):
    runtime(monkeypatch)
    store = FollowingSyncStore(tmp_path / 'following')
    before_a = store.sync(Reader(public=(author(),)))
    before_b = store.sync(Reader(public=(author('82', 'Second account'),), account_id='9002'))
    entered, release = Event(), Event()
    def block_private(visibility):
        if visibility == 'hide':
            entered.set()
            assert release.wait(5)
    reader_a = Reader(public=(author('77', 'Stale replacement'),), hook=block_private)
    reader_b = Reader(account_id='9002')
    services = SimpleNamespace(following_sync=store, live_client=reader_a, closing=False)
    client, view, unused = await mount(services)
    operation = None
    try:
        assert names(view) == ('Alpha',)
        operation = asyncio.create_task(view.sync_now())
        assert await asyncio.to_thread(entered.wait, 5)
        services.live_client = reader_b
        await view.refresh()
        assert view.snapshot is None and names(view) == ()
        release.set()
        assert not await operation
        await view.refresh()
        assert view.snapshot == before_b and names(view) == ('Second account',)
        assert store.snapshot('9001') == before_a and store.snapshot('9002') == before_b
        assert reader_b.calls == [] and reader_b.auth_calls == 0
    finally:
        release.set()
        if operation is not None:
            await asyncio.gather(operation, return_exceptions=True)
        await dispose(client, view)
        store.close()


@pytest.mark.asyncio
async def test_cancelled_async_handler_keeps_busy_gate_until_thread_drains(tmp_path, monkeypatch):
    runtime(monkeypatch)
    store = FollowingSyncStore(tmp_path / 'following')
    before = store.sync(Reader(public=(author(),)))
    entered, release = Event(), Event()
    def block_private(visibility):
        if visibility == 'hide':
            entered.set()
            assert release.wait(5)
    reader = Reader(public=(author('77', 'Cancelled result'),), hook=block_private)
    services = SimpleNamespace(following_sync=store, live_client=reader, closing=False)
    client, view, unused = await mount(services)
    operation = None
    try:
        operation = asyncio.create_task(view.sync_now())
        assert await asyncio.to_thread(entered.wait, 5)
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await operation
        assert view._busy and not view.sync_button.enabled and not await view.sync_now()
        release.set()
        await eventually(lambda: not view._busy)
        if view._initial_task is not None:
            await view._initial_task
        assert store.snapshot('9001') == before and view.snapshot == before
        assert reader.calls == ['show', 'hide']
    finally:
        release.set()
        if operation is not None:
            await asyncio.gather(operation, return_exceptions=True)
        await dispose(client, view)
        store.close()
