import asyncio
from threading import Event
from types import SimpleNamespace

import pytest
from nicegui import Client, ui

from pixivvault_nicegui.managed_network_stop_shared_scope import ManagedUserNetworkStopGate
from pixivvault_nicegui.managed_scheduler import ScheduleConfirmationUnavailable
from pixivvault_nicegui.pixiv_read_client import ClientClosed
from pixivvault_nicegui.request_policy import request_checkpoint
from pixivvault_nicegui.ui.scheduler_view import SchedulerView
from test_cookie_auth_status_20261117 import cookie_file
from test_cookie_auto_extract_20261117 import FakeReader, make
from test_scheduler_startup_r2_20261121 import native
from test_w_account_separation_20261119 import A, B, Reader


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ('transport', 'expiry-checkpoint'))
async def test_close_revokes_confirmation_then_drains_shared_gate(tmp_path, monkeypatch, stage):
    with native(tmp_path, monkeypatch) as (binding, services, reader):
        services.following_sync.sync(Reader(A))
        await binding.refresh_auth_status(startup=True)
        gate = ManagedUserNetworkStopGate(tmp_path / 'stop' / 'shared.sqlite3')
        services.managed_network_stop = SimpleNamespace(require_allowed=gate.snapshot)
        entered, aborted, finish = Event(), Event(), Event()

        def require_identity():
            with gate.request():
                entered.set()
                assert aborted.wait(5)
                assert finish.wait(5)
                if stage == 'expiry-checkpoint':
                    request_checkpoint()
                raise ClientClosed()

        monkeypatch.setattr(reader, 'require_identity', require_identity)
        monkeypatch.setattr(reader, 'close', aborted.set)
        confirmation = asyncio.create_task(asyncio.to_thread(services.confirm_scheduler_account))
        closing = None
        try:
            assert await asyncio.to_thread(entered.wait, 4)
            assert gate.inflight_path.is_file()
            closing = asyncio.create_task(asyncio.to_thread(services.close))
            assert await asyncio.to_thread(aborted.wait, 2)
            done, _ = await asyncio.wait((closing,), timeout=0.2)
            assert not done and not services.closed and gate.inflight_path.is_file()
            assert binding.store.snapshot().revision > 0
            finish.set()
            await asyncio.wait_for(closing, 2)
            with pytest.raises(ScheduleConfirmationUnavailable):
                await confirmation
            assert services.closed and not gate.inflight_path.exists() and not gate.snapshot().blocked
        finally:
            finish.set()
            await asyncio.gather(confirmation, *([closing] if closing is not None else []), return_exceptions=True)


@pytest.mark.asyncio
async def test_close_with_unresponsive_confirmation_times_out_without_closing_resources(tmp_path, monkeypatch):
    with native(tmp_path, monkeypatch) as (binding, services, reader):
        services.following_sync.sync(Reader(A))
        await binding.refresh_auth_status(startup=True)
        gate = ManagedUserNetworkStopGate(tmp_path / 'stop' / 'shared.sqlite3')
        services.managed_network_stop = SimpleNamespace(require_allowed=gate.snapshot)
        entered, aborted, finish = Event(), Event(), Event()

        def require_identity():
            with gate.request():
                entered.set()
                assert finish.wait(5)
                raise ClientClosed()

        monkeypatch.setattr(reader, 'require_identity', require_identity)
        monkeypatch.setattr(reader, 'close', aborted.set)
        monkeypatch.setattr('pixivvault_nicegui.services._SCHEDULER_CONFIRMATION_DRAIN_SECONDS', 0.1, raising=False)
        confirmation = asyncio.create_task(asyncio.to_thread(services.confirm_scheduler_account))
        try:
            assert await asyncio.to_thread(entered.wait, 4)
            with pytest.raises(RuntimeError, match='scheduler confirmation shutdown unverified'):
                await asyncio.wait_for(asyncio.to_thread(services.close), 2)
            assert aborted.is_set() and services.closing and not services.closed
            assert gate.inflight_path.is_file() and services.following_sync.last_synced_account() == A
        finally:
            finish.set()
            await asyncio.gather(confirmation, return_exceptions=True)
        await asyncio.to_thread(services.close)
        assert services.closed and not gate.inflight_path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ('refresh', 'expiry', 'adoption', 'startup-adopted'))
async def test_startup_and_adoption_use_their_own_authentication_proof(tmp_path, monkeypatch, phase):
    binding, store, old = make(tmp_path, monkeypatch, cookie_days=5 if phase == 'expiry' else 20,
                               expired=phase == 'startup-adopted')
    parent = tmp_path / 'draft'
    parent.mkdir()
    draft = cookie_file(parent, 20)
    FakeReader.uids[str(draft)] = B
    FakeReader.uids[str(old)] = A
    entered, resume = asyncio.Event(), asyncio.Event()
    lock = binding._auth_lock
    pause_on = 2 if phase == 'startup-adopted' else 1

    class InterleavedLock:
        exits = 0

        async def __aenter__(self):
            await lock.acquire()

        async def __aexit__(self, *_):
            lock.release()
            self.exits += 1
            if self.exits == pause_on:
                entered.set()
                await resume.wait()

    calls = []

    async def followup(account):
        calls.append(account)

    monkeypatch.setattr(binding, '_auth_lock', InterleavedLock())
    monkeypatch.setattr(binding.followup, 'after_confirmed', followup)
    if phase == 'adoption':
        operation = binding.adopt_browser_cookies()
    else:
        operation = binding.refresh_auth_status(startup=True)
    task = asyncio.create_task(operation)
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert (await binding.check_authentication(draft)).ok
        resume.set()
        outcome = await task
        if phase == 'refresh':
            assert calls == [A] and binding.browser_cookie_source.calls == 0
            assert store.snapshot().cookie_file == old
        else:
            assert binding.browser_cookie_source.calls == 1 and store.snapshot().cookie_file != old
            assert binding.last_cookie_adoption == 'adopted'
            assert binding.auth_status.status.user_id == '777'
            if phase == 'adoption':
                assert outcome == 'adopted'
            else:
                assert calls == ['777']
    finally:
        resume.set()
        await asyncio.gather(task, return_exceptions=True)
        binding.close()


@pytest.mark.asyncio
async def test_other_view_shows_confirmation_busy_and_clears_it_after_completion(tmp_path, monkeypatch):
    with native(tmp_path, monkeypatch) as (binding, services, reader):
        services.following_sync.sync(Reader(A))
        await binding.refresh_auth_status(startup=True)
        entered, finish = Event(), Event()

        def blocked():
            entered.set()
            assert finish.wait(5)
            raise ClientClosed()

        monkeypatch.setattr(reader, 'require_identity', blocked)
        clients = [Client(ui.page('/_scheduler_r4_first')), Client(ui.page('/_scheduler_r4_second'))]
        views, first = [], None
        try:
            for client in clients:
                with client:
                    view = SchedulerView(services)
                    await view.refresh()
                    assert view.confirm_account_button.visible and view.confirm_account_button.enabled
                    views.append(view)
            first = asyncio.create_task(views[0].confirm_account())
            assert await asyncio.to_thread(entered.wait, 4)
            await views[1].confirm_account()  # click queued on the other page
            busy = '別の画面で定期チェックの認証を確認中'
            assert busy in views[1].status.text and busy == views[1].action_notice.text
            assert not views[1].confirm_account_button.enabled
            finish.set()
            await first
            await views[1].refresh()
            assert busy not in views[1].status.text and busy not in views[1].action_notice.text
            assert views[1].confirm_account_button.visible and views[1].confirm_account_button.enabled
        finally:
            finish.set()
            if first is not None:
                await asyncio.gather(first, return_exceptions=True)
            for view in views:
                view.close()
            for client in clients:
                client.delete()
