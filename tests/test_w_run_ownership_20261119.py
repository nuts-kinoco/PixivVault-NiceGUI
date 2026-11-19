"""Independent tests of normal account switching and the corresponding UI actions."""
import asyncio
from threading import Event

import pytest
from nicegui import Client, ui

from pixivvault_nicegui.batch_runs import BatchRunLedger
from pixivvault_nicegui.following_sync import FollowingSyncStore
from pixivvault_nicegui.ui.scheduler_view import SchedulerView
from pixivvault_nicegui.ui.unified_download_view import UnifiedDownloadView

from test_w_account_history_controls_20261119 import services
from test_w_account_separation_20261119 import A, B, X, Reader, resources
from test_w_review_bookmark_resume_20261118 import GeneratedReader, services_with_runs


def listing_reader(account):
    reader = Reader(account)
    reader.get_author_artworks_complete = lambda _: ()
    reader.get_author_novel_ids = lambda _: ()
    return reader


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ('pause', 'stop'))
async def test_b_cannot_control_active_a_author_run(tmp_path, action):
    root, follow_path, batch_path = (tmp_path / name for name in ('managed', 'following', 'batch'))
    reader = listing_reader(A)
    started, unblocked = Event(), Event()
    def inventory(_):
        started.set()
        assert unblocked.wait(5)
        return ()

    reader.get_author_artworks_complete = inventory
    following, ledger = FollowingSyncStore(follow_path), BatchRunLedger(batch_path)
    svc = services(root, following, reader, ledger)
    first = Client(ui.page(f'/r11-a-{action}'))
    second = Client(ui.page(f'/r11-b-{action}'))
    views = []
    try:
        following.sync(reader)
        with first:
            a_view = UnifiedDownloadView(svc)
            views.append(a_view)
            await a_view.reload()
        a_view.toggle_pick(X)
        assert a_view.target_ids() == [X]
        assert a_view.run()
        assert await asyncio.to_thread(started.wait, 5)
        owner_run = svc.batch_runs.run_id
        before = ledger.run(owner_run)
        assert before.account_id == A and before.state == 'running'

        # Follow synchronization has its own reader and can finish while the
        # author run is still waiting for its current result.
        following.sync(listing_reader(B))
        with second:
            b_view = UnifiedDownloadView(svc)
            views.append(b_view)
            await b_view.reload()
        b_view.sync_engine()
        b_view.render_controls()
        assert {row['account_id'] for row in b_view.authors} == {B}
        button = getattr(b_view, f'{action}_button')
        enabled = button.visible and not button.props.get('disabled')
        changed = b_view.toggle_pause() if action == 'pause' else b_view.stop()
        after = ledger.run(owner_run)
        print(f'{action}: visible-and-enabled={enabled}; returned={changed}; '
              f'A run {before.state}@{before.revision} -> {after.state}@{after.revision}')
        if action == 'stop' and changed:
            unblocked.set()
            assert await asyncio.to_thread(svc.batch_runs.join, 5)
            print(f'A run after worker exits: {ledger.run(owner_run).state}')
        assert not enabled and not changed and after == before
    finally:
        svc.batch_runs._shutdown(0)
        unblocked.set()
        assert await asyncio.to_thread(svc.batch_runs.join, 5)
        for view in views:
            view.close()
        first.delete()
        second.delete()
        ledger.close()
        following.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ('resume', 'discard'))
async def test_id_only_author_run_has_working_offered_action(tmp_path, action):
    root, follow_path, batch_path = (tmp_path / name for name in ('managed', 'following', 'batch'))
    reader = listing_reader(A)
    blocked, clear = Event(), Event()
    def inventory(_):
        blocked.set()
        assert clear.wait(5)
        return ()
    reader.get_author_artworks_complete = inventory
    following, ledger = FollowingSyncStore(follow_path), BatchRunLedger(batch_path)
    svc = services(root, following, reader, ledger)
    client = Client(ui.page(f'/r11-id-only-{action}'))
    view = None
    try:
        following.sync(reader)
        with client:
            view = UnifiedDownloadView(svc)
            await view.reload()
        view.query = '2001'
        assert view.pick_from_id()
        assert view.run()
        assert await asyncio.to_thread(blocked.wait, 5)
        assert view.stop()
        clear.set()
        assert await asyncio.to_thread(svc.batch_runs.join, 5)
        run_id = svc.batch_runs.run_id
        run = ledger.run(run_id)
        assert run.account_id == A and run.state == 'stopped'
        view.sync_engine()
        view.refresh_resumable()
        assert view.resumable.run_id == run_id
        assert getattr(view, f'{action}_button').visible
        result = await view.resume_interrupted() if action == 'resume' else view.discard_interrupted()
        if result:
            assert await asyncio.to_thread(svc.batch_runs.join, 5)
        print(f'ID-only {action}: account={run.account_id}; result={result}; '
              f'runs={len(ledger.runs())}; notice={view.notice_text}')
        assert result
    finally:
        svc.batch_runs._shutdown(0)
        clear.set()
        assert await asyncio.to_thread(svc.batch_runs.join, 5)
        if view is not None:
            view.close()
        client.delete()
        ledger.close()
        following.close()


@pytest.mark.asyncio
async def test_stale_stop_form_cannot_replace_newer_schedule(tmp_path):
    with resources(tmp_path) as (_, store, ledger, reader, svc):
        store.sync(reader)
        stale_client = Client(ui.page('/r11-stale-stop'))
        newer_client = Client(ui.page('/r11-newer-settings'))
        with stale_client:
            stale = SchedulerView(svc)
        with newer_client:
            newer = SchedulerView(svc)
        try:
            stale._load(ledger)
            newer._load(ledger)
            stale.interval.set_value('0')
            newer.interval.set_value('6')
            await newer.save()
            saved = ledger.snapshot()
            assert saved.interval_hours == 6 and saved.revision != stale.revision
            await stale.save()
            after = ledger.snapshot()
            print(f'stale stop: saved={saved.interval_hours}@{saved.revision}; '
                  f'after={after.interval_hours}@{after.revision}; notice={stale.action_notice.text}')
            assert after == saved
        finally:
            stale.close()
            newer.close()
            stale_client.delete()
            newer_client.delete()


class BookmarkBReader(GeneratedReader):
    def verify_identity(self):
        self._verified_identity = B
        return B


@pytest.mark.asyncio
@pytest.mark.parametrize("current", (A, B), ids=("owner", "other"))
async def test_bookmark_resume_requires_current_owner_after_restart(tmp_path, current):
    # Persist a genuinely verified A bookmark run, then reopen using B's reader
    # before B has synced following. The bookmark list itself is empty.
    first_reader = GeneratedReader()
    first_reader.get_bookmarked_works = lambda *_a, **_kw: ()
    first = services_with_runs(tmp_path, first_reader)
    from pixivvault_nicegui.bookmark_run import VISIBILITY_ROWS
    row_id = VISIBILITY_ROWS['show']
    try:
        _, _, checked = await first.refresh_bookmark_cache(return_account=True)
        assert checked == A
        run_id = first.batch_run_ledger.begin_run(
            'bookmarks', 'manual', 'both', (row_id,), account_id=A, verified_bookmark_account=True).run_id
        first.batch_run_ledger.set_run_state(first.batch_run_ledger.run(run_id).handle, 'stopped')
    finally:
        await asyncio.to_thread(first.close)

    reader = GeneratedReader() if current == A else BookmarkBReader()
    reader.get_bookmarked_works = lambda *_a, **_kw: ()
    svc = services_with_runs(tmp_path, reader)
    client = Client(ui.page('/r11-b-bookmark'))
    view = None
    try:
        before = svc.batch_run_ledger.runs()
        with client:
            view = UnifiedDownloadView(svc)
            await view.reload()
        view.refresh_resumable()
        assert view._history_account() is None
        assert reader._verified_identity == current
        if current == B:
            assert view.resumable is None
            assert await view.resume_interrupted() is False
            assert not reader.calls and svc.batch_run_ledger.runs() == before
        else:
            assert view.resume_button.visible and view.resumable.run_id == run_id
            assert await view.resume_interrupted()
            assert await asyncio.to_thread(svc.batch_runs.join, 5)
            resumed, original = svc.batch_run_ledger.runs()
            assert original == before[0]
            assert (resumed.account_id, resumed.state) == (A, 'done')
    finally:
        if view is not None:
            view.close()
        client.delete()
        await asyncio.to_thread(svc.close)


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ('resume', 'discard', 'resume-after-b'))
async def test_a_owns_resume_and_discard(tmp_path, action):
    root, follow_path, batch_path = (tmp_path / name for name in ('managed', 'following', 'batch'))
    reader = listing_reader(A)
    started, unblock = Event(), Event()
    def inventory(_):
        started.set()
        assert unblock.wait(5)
        return ()
    reader.get_author_artworks_complete = inventory
    following, ledger = FollowingSyncStore(follow_path), BatchRunLedger(batch_path)
    a = services(root, following, reader, ledger)
    client = Client(ui.page(f'/r11-a-control-{action}'))
    view = None
    b = None
    try:
        following.sync(reader)
        with client:
            view = UnifiedDownloadView(a)
            await view.reload()
        view.toggle_pick(X)
        assert view.target_ids() == [X] and view.run()
        assert await asyncio.to_thread(started.wait, 5)
        original = a.batch_runs.run_id
        assert view.stop()
        unblock.set()
        assert await asyncio.to_thread(a.batch_runs.join, 5)
        before = ledger.run(original)
        assert before.state == 'stopped' and before.account_id == A

        if action == 'resume-after-b':
            b_reader = listing_reader(B)
            following.sync(b_reader)
            b = services(root, following, b_reader, ledger)
            b_id = b.start_batch_run((X,), target_type='illust', account_id=B).run_id
            assert await asyncio.to_thread(b.batch_runs.join, 5)
            assert ledger.run(b_id).state == 'done'
            following.sync(reader)
            await view.reload()
            assert view._history_account() == A

        view.sync_engine()
        view.refresh_resumable()
        offer = view.resumable
        print(f'A {action}: stored={ledger.run(original).state}; '
              f'offer-is-A={offer is not None and offer.run_id == original}')
        assert offer is not None and offer.run_id == original
        if action == 'discard':
            assert view.discard_button.visible and view.discard_interrupted()
            assert ledger.resumable_run(account_id=A) is None
        else:
            assert view.resume_button.visible and await view.resume_interrupted()
            assert await asyncio.to_thread(a.batch_runs.join, 5)
            continued = ledger.run(a.batch_runs.run_id)
            assert continued.run_id != original and continued.account_id == A
            assert continued.state == 'done'
        assert ledger.run(original) == before
    finally:
        unblock.set()
        for svc in (a, b):
            if svc is not None:
                svc.batch_runs._shutdown(0)
                assert await asyncio.to_thread(svc.batch_runs.join, 5)
        if view is not None:
            view.close()
        client.delete()
        ledger.close()
        following.close()


from concurrent.futures import ThreadPoolExecutor
from threading import Event, RLock

from pixivvault_nicegui.batch_engine import BatchRunEngine
from pixivvault_nicegui.batch_runs import BatchRunLedger
from pixivvault_nicegui.managed_scheduler import run_author_check
from pixivvault_nicegui.services import ApplicationServices

from test_w_account_separation_20261119 import A, B, X, Y, Reader, resources
from test_t_dl_engine_20261117 import FakePort, cand


class ChangingReaderPort(FakePort):
    def __init__(self):
        super().__init__({X: (cand(X, '200'),), Y: ()})
        self.remote_account = A

    def verified_author_account(self):
        self._verified_identity = self.remote_account
        return self._verified_identity


def test_waiting_scheduler_persists_the_scheduled_run_totals(tmp_path):
    with resources(tmp_path) as (_, store, schedule, reader, svc):
        store.sync(reader)
        assert not svc.scheduler_favourite_authors(ledger=schedule)
        assert schedule._bound_account() == A

        port = ChangingReaderPort()
        port.verified_author_account()
        svc.live_client = port
        svc._batch_listener_lock = RLock()
        svc._batch_listeners = []
        a_published = Event()
        a_may_exit = Event()
        scheduled_run_ids = []

        def listener(event):
            if event['event'] == 'run_end' and event['totals']['n_new'] == 1:
                scheduled_run_ids.append(event['run_id'])
                a_published.set()
                a_may_exit.wait(5)

        unsubscribe = ApplicationServices.subscribe_batch_events(svc, listener)
        batch = BatchRunLedger(tmp_path / 'batch')
        port.ledger = batch
        engine = BatchRunEngine(port, batch, following=store, poll_seconds=0.001,
                                on_progress=ApplicationServices._fan_out_batch_event.__get__(svc))
        svc.batch_runs = engine
        svc.start_batch_run = ApplicationServices.start_batch_run.__get__(svc)
        try:
            with ThreadPoolExecutor(max_workers=1) as scheduler:
                pending = scheduler.submit(run_author_check, svc, (X,), cancellation=Event(), ledger=schedule,
                                           slot='r11-periodic', wait=True, account_id=A)
                try:
                    assert a_published.wait(5)
                    a_id = scheduled_run_ids[0]
                    assert batch.run(a_id).state == 'done' and batch.run(a_id).account_id == A
                    assert batch.authors(a_id)[0].n_new == 1
                    assert not engine.active

                    # Account switching and B's ordinary manual start occur
                    # after A has durably finished and published the event.
                    port.remote_account = B
                    assert port.verified_author_account() == B
                    store.sync(Reader(B))
                    b_id = svc.start_batch_run((Y,), account_id=B).run_id
                    assert engine.join(5)
                    assert batch.run(b_id).state == 'done' and batch.run(b_id).account_id == B
                    assert batch.authors(b_id)[0].n_new == 0
                finally:
                    a_may_exit.set()
                result = pending.result(5)
            (row,) = schedule.history()
            print(f"A author n_new=1; B author n_new=0; A dispatch accepted={row['accepted']} "
                  f"status={row['status']}; reported={result['accepted']}")
            assert row['accepted'] == 1 and row['status'] == 'queued'
        finally:
            a_may_exit.set()
            engine._shutdown(0)
            assert engine.join(5)
            unsubscribe()
            batch.close()
