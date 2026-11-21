import asyncio
from threading import Event

import pytest
from nicegui import Client, ui

from pixivvault_nicegui.managed_scheduler import ScheduleError
from pixivvault_nicegui.native_auth_result import NativeAuthResult
from pixivvault_nicegui.ui.scheduler_view import SchedulerView
from test_ac_library_search_20261119 import until
from test_cookie_account_followup_20261117 import FakeFollowReader, age
from test_r3_owner_commit_20261120 import seed_history
from test_scheduler_startup_r2_20261121 import native
from test_w_account_separation_20261119 import A, B, Reader


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ('authentication', 'following', 'first-sync', 'switch-pending'))
async def test_confirm_button_cannot_release_pending_timer(tmp_path, monkeypatch, stage):
    with native(tmp_path, monkeypatch) as (binding, services, reader):
        following = services.following_sync
        entered, release = Event(), Event()
        task = None
        if stage != 'first-sync':
            following.sync(Reader(B if stage == 'switch-pending' else A))
        if stage == 'authentication':
            verify = binding._verify_auth_result

            def delayed(*args):
                entered.set()
                assert release.wait(8)
                return verify(*args)

            monkeypatch.setattr(binding, '_verify_auth_result', delayed)
        elif stage == 'following':
            age(following, A, days=2)

            class DelayedFollowing(FakeFollowReader):
                def get_following_users_detailed(self, *args, **kwargs):
                    entered.set()
                    assert release.wait(8)
                    return super().get_following_users_detailed(*args, **kwargs)

            binding.followup.reader_factory = lambda _: DelayedFollowing(A)

        client = Client(ui.page('/_scheduler_r3_pending'))
        try:
            if stage == 'switch-pending':
                await binding.refresh_auth_status(startup=True)
                assert binding.followup.pending is not None
            if stage in ('authentication', 'following'):
                task = asyncio.create_task(binding.refresh_auth_status(startup=True))
                assert await asyncio.to_thread(entered.wait, 5)
            with client:
                view = SchedulerView(services)
                await view.refresh()
                with pytest.raises(ScheduleError):
                    await asyncio.to_thread(services.confirm_scheduler_account)
                await view.confirm_account()  # an event queued before the view refreshed
                assert 'フォロー' in view.status.text
                assert '出所' not in view.action_notice.text
                assert not (view.confirm_account_button.visible and view.confirm_account_button.enabled)
                assert reader.attempts == 0 and services.managed_schedule_runner._thread is None
                if task is not None:
                    release.set()
                    await task
                    await view.refresh()
                    assert not services._scheduler_waiting_for_auth and view.confirm_account_button.visible
                view.close()
        finally:
            release.set()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
            client.delete()


@pytest.mark.asyncio
async def test_selected_auth_keeps_its_proof_if_another_check_runs_after_unlock(tmp_path, monkeypatch):
    with native(tmp_path, monkeypatch) as (binding, services, _):
        following = services.following_sync
        following.sync(Reader(A))
        entered, overwritten = asyncio.Event(), asyncio.Event()
        lock = binding._auth_lock

        class InterleavedLock:
            async def __aenter__(self):
                await lock.acquire()

            async def __aexit__(self, *_):
                lock.release()
                if not entered.is_set():
                    entered.set()
                    await overwritten.wait()

        calls = 0

        def verify(_file, _stop):
            nonlocal calls
            calls += 1
            binding._last_identity = ((A if calls == 1 else B), 20)
            return NativeAuthResult('verified')

        monkeypatch.setattr(binding, '_auth_lock', InterleavedLock())
        monkeypatch.setattr(binding, '_verify_auth_result', verify)
        cookie = binding.launch_snapshot.cookie_file
        selected = asyncio.create_task(binding.check_selected_authentication(cookie))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            assert (await binding.check_authentication(cookie)).ok
        finally:
            overwritten.set()
            assert (await selected).ok
        await until(lambda: not services._scheduler_waiting_for_auth)
        assert binding.followup.pending is None


@pytest.mark.asyncio
async def test_two_views_and_repeated_click_use_one_confirmation(tmp_path, monkeypatch):
    with native(tmp_path, monkeypatch) as (binding, services, reader):
        services.following_sync.sync(Reader(A))
        await binding.refresh_auth_status(startup=True)
        entered, release = Event(), Event()
        require = reader.require_identity

        def blocked():
            entered.set()
            assert release.wait(8)
            return require()

        monkeypatch.setattr(reader, 'require_identity', blocked)
        clients = [Client(ui.page('/_scheduler_r3_multi_a')), Client(ui.page('/_scheduler_r3_multi_b'))]
        views = []
        first = second = None
        try:
            for client in clients:
                with client:
                    view = SchedulerView(services)
                    await view.refresh()
                    assert view.confirm_account_button.visible and view.confirm_account_button.enabled
                    views.append(view)
            first = asyncio.create_task(views[0].confirm_account())
            assert await asyncio.to_thread(entered.wait, 5)
            await views[0].confirm_account()
            second = asyncio.create_task(views[1].confirm_account())
            done, _ = await asyncio.wait((second,), timeout=1)
            assert second in done and not first.done()
            assert not views[0].confirm_account_button.enabled
            release.set()
            await first
            await views[1].refresh()
            assert reader.attempts == 1
            assert all(view.run_button.enabled for view in views)
        finally:
            release.set()
            tasks = [task for task in (first, second) if task is not None]
            await asyncio.gather(*tasks, return_exceptions=True)
            for view in views:
                view.close()
            for client in clients:
                client.delete()


@pytest.mark.asyncio
async def test_close_does_not_wait_for_editor_confirmation_transport(tmp_path, monkeypatch):
    with native(tmp_path, monkeypatch) as (binding, services, reader):
        services.following_sync.sync(Reader(A))
        await binding.refresh_auth_status(startup=True)
        entered, release = Event(), Event()
        require = reader.require_identity

        def blocked():
            entered.set()
            assert release.wait(8)
            return require()

        monkeypatch.setattr(reader, 'require_identity', blocked)
        confirmation = asyncio.create_task(asyncio.to_thread(services.confirm_scheduler_account))
        closing = None
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            closing = asyncio.create_task(asyncio.to_thread(services.close))
            done, _ = await asyncio.wait((closing,), timeout=2)
            assert closing in done and services.closed and not confirmation.done()
            with pytest.raises(ScheduleError):
                await asyncio.to_thread(services.confirm_scheduler_account)
            release.set()
            with pytest.raises(ScheduleError):
                await confirmation
            assert not services.managed_schedule_runner._thread.is_alive()
        finally:
            release.set()
            await asyncio.gather(confirmation, *([closing] if closing is not None else []), return_exceptions=True)


@pytest.mark.asyncio
async def test_confirmed_live_account_cannot_see_other_accounts_history(tmp_path, monkeypatch):
    with native(tmp_path, monkeypatch, account=B) as (binding, services, reader):
        following, ledger = services.following_sync, services.managed_scheduler
        following.sync(Reader(A))
        reader.account = A
        services.scheduler_favourite_authors(ledger=ledger)
        seed_history(ledger)
        original = ledger.history()
        reader.account, reader._verified_identity = B, None
        following.sync(Reader(B))
        await binding.refresh_auth_status(startup=True)
        client = Client(ui.page('/_scheduler_r3_history'))
        try:
            with client:
                view = SchedulerView(services)
                await view.refresh()
                await view.confirm_account()
                assert not view.confirm_account_button.visible and not view.run_button.enabled
                assert not view.history_label.visible
                assert ledger._bound_account() == A and ledger.history() == original
                view.close()
        finally:
            client.delete()
