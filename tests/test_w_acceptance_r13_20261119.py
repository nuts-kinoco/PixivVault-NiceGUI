"""R12 independent findings at the real service and view entry points."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event, RLock
from types import SimpleNamespace as NS

import pytest
from nicegui import Client, ui

from account_test_support import schedule_owner
from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
from pixivvault_nicegui.bookmark_run import VISIBILITY_ROWS
from pixivvault_nicegui.batch_engine import JobRef
from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopped
from pixivvault_nicegui.managed_scheduler import ManagedScheduleRunner
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.queue_log import QueueLog
from pixivvault_nicegui.ui.scheduler_view import SchedulerView
from pixivvault_nicegui.ui.unified_download_view import UnifiedDownloadView

from test_w_account_separation_20261119 import A, B, X, Y, Reader, resources, switch
from test_w_review_bookmark_resume_20261118 import GeneratedReader, services_with_runs


class RealFlowReader(GeneratedReader):
    def __init__(self, account=A):
        super().__init__()
        self.owner = account
        self.identity_calls = 0

    def verify_identity(self):
        self.identity_calls += 1
        self._verified_identity = self.owner
        return self.owner

    require_identity = verify_identity

    def get_following_users_detailed(self, account, visibility):
        assert account == self.owner
        return Reader(account).get_following_users_detailed(account, visibility)


@pytest.mark.asyncio
@pytest.mark.parametrize('with_follow_sync', (False, True), ids=('without-follow-sync', 'with-follow-sync'))
@pytest.mark.parametrize('session', ('owner', 'other', 'stopped'))
async def test_owner_bookmark_resume_offer_after_normal_restart(tmp_path, with_follow_sync, session, monkeypatch):
    initial = RealFlowReader()
    opts = {'following_sync_root': tmp_path / 'following'} if with_follow_sync else {}
    first = services_with_runs(tmp_path, initial, **opts)
    try:
        if with_follow_sync:
            first.following_sync.sync(initial)
            schedule = first.managed_scheduler
            schedule.configure(interval_hours=0, startup_check=False,
                               expected_revision=schedule.snapshot().revision,
                               now=datetime.now(timezone.utc), account_id=schedule_owner(schedule, A))
        await first.refresh_bookmark_cache()
        handle = first.batch_run_ledger.begin_run(
            'bookmarks', 'manual', 'illust', (VISIBILITY_ROWS['show'],),
            account_id=A, verified_bookmark_account=True)
        first.batch_run_ledger.set_run_state(handle, 'stopped')
    finally:
        await asyncio.to_thread(first.close)
    reader = RealFlowReader(B if session == 'other' else A)
    monkeypatch.setattr(reader, 'get_bookmarked_works', lambda *_a, **_kw: ())
    second = services_with_runs(tmp_path, reader, **opts)
    if second.managed_schedule_runner is not None:
        second.managed_schedule_runner.stop()
    if session == 'stopped':
        def stopped():
            raise ManagedNetworkStopped(429)

        monkeypatch.setattr(second.batch_runs.port, 'require_allowed', stopped)
    client = Client(ui.page('/r13-bookmark-offer'))
    before = second.batch_run_ledger.runs()
    try:
        with client:
            view = UnifiedDownloadView(second)
            await view.reload()
            assert view.set_mode('bookmark')
        if session != 'owner':
            assert view.resumable is None and not view.resume_button.visible
            assert not await view.resume_interrupted()
            assert reader.identity_calls == int(session == 'other') and not reader.calls
            assert second.batch_run_ledger.runs() == before
        else:
            assert reader.identity_calls == 1
            assert view.resume_button.visible and view.resumable.handle == handle
            assert await view.resume_interrupted()
            assert await asyncio.to_thread(second.batch_runs.join, 10)
            resumed, original = second.batch_run_ledger.runs()
            assert resumed.account_id == A and resumed.state == 'done'
            assert original == before[0]
    finally:
        view.close()
        client.delete()
        await asyncio.to_thread(second.close)


@pytest.mark.asyncio
@pytest.mark.parametrize('valid_author_edit', (True, False), ids=('valid-edit', 'invalid-edit'))
async def test_schedule_stop_and_star_save_retains_the_committed_revision(tmp_path, valid_author_edit):
    with resources(tmp_path) as (_, store, schedule, reader, svc):
        store.sync(reader)
        store.set_favorite(A, X, True)
        schedule.configure(interval_hours=6, startup_check=False, expected_revision=schedule.snapshot().revision,
                           now=datetime.now(timezone.utc), account_id=schedule_owner(schedule, A))
        client = Client(ui.page('/r12-stop-and-star'))
        try:
            with client:
                view = SchedulerView(svc)
            view._load(schedule)
            view.interval.set_value('0')
            view.authors.set_value(Y if valid_author_edit else 'invalid-id')
            before = schedule.snapshot()
            await view.save()
            after = schedule.snapshot()
            assert after.interval_hours == 0 and after.revision > before.revision
            assert view.revision == after.revision
            if valid_author_edit:
                assert schedule.favourites(A) == (Y,)
            else:
                assert schedule.favourites(A) == (X,) and view.authors.value == 'invalid-id'
                view.authors.set_value(Y)
                await view.save()
                assert schedule.favourites(A) == (Y,)
                assert view.revision == schedule.snapshot().revision
        finally:
            view.close()
            client.delete()


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ('stop', 'acknowledge'))
async def test_settings_form_never_adopts_another_accounts_commit(tmp_path, action):
    with resources(tmp_path) as (_, store, schedule, reader, svc):
        store.sync(reader)
        store.set_favorite(A, X, True)
        if action == 'acknowledge':
            assert svc.scheduler_favourite_authors() == (X,)
            due = datetime.fromisoformat(schedule.snapshot().next_due_utc)
            assert schedule._claim_due(due)
            svc._scheduler_dispatch_lock = RLock()
            svc.jobs = NS(snapshot=lambda: ())
            svc.managed_schedule_runner = ManagedScheduleRunner(schedule, lambda *_: None)
        client = Client(ui.page('/r13-stale-settings'))
        with client:
            view = SchedulerView(svc)
        try:
            view._load(schedule)
            view.authors.set_value(Y)
            if action == 'stop':
                view.interval.set_value('0')
            switch(store, reader)
            store.set_favorite(B, X, True)
            current = (schedule.snapshot(), store.snapshot(A), store.snapshot(B))
            revision = view.revision
            await (view.save() if action == 'stop' else view.acknowledge())
            assert '\u518d\u8aad\u8fbc' in view.action_notice.text
            assert not view.authors.visible and not view.authors.value
            assert not view.history_label.visible and view.revision == revision
            assert (schedule.snapshot(), store.snapshot(A), store.snapshot(B)) == current
            store.sync(Reader(A))
            svc.live_client = Reader(A)
            view._load(schedule)
            assert view.authors.visible and view.authors.value == Y
            assert view.revision == revision
            if action == 'acknowledge':
                await view.acknowledge()
                assert view.authors.value == Y
                assert view.revision == schedule.snapshot().revision > revision
                await view.save()
                assert schedule.favourites(A) == (Y,)
                assert store.snapshot(B) == current[2]
        finally:
            view.close()
            client.delete()


class Notifier:
    supports_authorized_delivery = True

    def __init__(self):
        self.events = []

    def notify(self, title, message, *, authorize=None):
        if authorize is not None:
            authorize()
        self.events.append((title, message))

    def close(self):
        pass


def test_retirement_preserves_scheduler_evidence_until_owner_verifies_receipt(tmp_path):
    with resources(tmp_path) as (_, store, schedule, reader, svc):
        store.sync(reader)
        svc.scheduler_favourite_authors()
        now = datetime.now(timezone.utc)
        schedule.begin_dispatch('slot', now, 1, account_id=A, batch=True)
        schedule.prepare_dispatch_item(slot='slot', work_id='99', author_id=X, operation_id='op', account_id=A)
        schedule.bind_dispatch_job(slot='slot', work_id='99', operation_id='op', job_id='job',
                                   accepted_fingerprint='proof', account_id=A)
        job = NS(job_id='job', state='done')
        freed, retired = [], []
        svc.jobs = NS(snapshot=lambda: (job,), operation_ids=lambda _: ('op',))
        svc.static_jobs = NS(retained_job=lambda _: job, accepted_operations=lambda: (('op', 'job', '', '', ''),),
                             retire_jobs=lambda ids: retired.extend(ids) or tuple(ids))
        svc.managed_downloads = NS(verified_saved_receipt=lambda *_a, **_kw: None)
        svc._managed_requests = NS(release=freed.extend)
        svc._managed_job_records = {}
        svc._scheduler_unfinished_count = None
        svc._media_admission_lock = RLock()
        svc.shared_log, svc.notifications = QueueLog(), Notifier()
        svc.save_exports = svc.author_media = None
        svc.reconcile_managed_scheduler = ApplicationServices.reconcile_managed_scheduler.__get__(svc)
        refs = (JobRef('illustration', 'job', 'op'),)
        retire = ApplicationServices.retire_settled_author_jobs

        assert retire(svc, refs) is None and not retired and not freed
        assert schedule.history()[0]['status'] == 'running'
        svc.managed_downloads.verified_saved_receipt = lambda *_a, **_kw: (
            'receipt', {'work_id': '99'}, 'digest', None, 'proof')
        svc.live_client = Reader(B)
        before = tuple(schedule._db.iterdump())
        assert retire(svc, refs) is None and tuple(schedule._db.iterdump()) == before
        assert not retired and not freed and not svc.notifications.events

        svc.live_client = reader
        assert retire(svc, refs) == 1 and retired == ['job'] and freed == ['op']
        assert schedule.history()[0]['status'] == 'running'
        schedule.finish_dispatch('slot', status='queued', accepted=1, deferred=0, account_id=A)
        assert schedule.history()[0]['status'] == 'saved'
        assert len(svc.notifications.events) == 1


class AuthorReader(RealFlowReader):
    def get_author_artworks_complete(self, author):
        assert author == X
        return (dict(id='99', user_id=X, title='fixture', type=0, page_count=1,
                     update_date='2026-01-01T00:00:00Z'),)

    def get_author_novel_ids(self, author):
        assert author == X
        return ()

    def get_artwork_update_revision(self, work, author):
        assert work == '99' and author == X
        return dict(work_id=work, author_id=X, page_count=1, illustration_type=0,
                    update_date='2026-01-01T00:00:00Z')

    def get_artwork_download_record(self, work):
        return RemoteBookmark(work, 'illustration', 'show', 'fixture', X, 'author', (), '', '2026-01-01T00:00:00Z', 1, 0, 0)


@pytest.mark.parametrize('wait', (True, False), ids=('wait', 'background'))
def test_periodic_engine_sends_saved_notification(tmp_path, wait, monkeypatch):
    reader = AuthorReader()
    svc = services_with_runs(tmp_path, reader, following_sync_root=tmp_path / 'following')
    svc.managed_schedule_runner.stop()
    monkeypatch.setattr(svc.batch_runs.port, 'retire', lambda _: 'unsupported')
    notifier = Notifier()
    svc.notifications = notifier
    try:
        svc.following_sync.sync(Reader(B))
        svc.following_sync.set_favorite(B, Y, True)
        svc.following_sync.sync(reader)
        svc.following_sync.set_favorite(A, X, True)
        schedule = svc.managed_scheduler
        with svc._scheduler_dispatch_lock:
            assert svc.scheduler_favourite_authors(ledger=schedule) == (X,)
            stats = svc.dispatch_author_check(schedule, 'review-due', Event(), wait=wait)
        assert svc.batch_runs.join(15)
        run = svc.batch_run_ledger.runs()[0]
        item, = svc.batch_run_ledger.items(run.run_id)
        assert run.origin == 'auto' and (stats['accepted'] == 1 if wait else stats['started'])
        assert run.state == 'done' and item.state == 'done'
        receipt = svc.managed_downloads.verified_saved_receipt('99')
        assert receipt is not None and schedule.dispatch_operation('review-due', '99')[0] == item.operation_id

        proofs = svc.author_media._proof
        monkeypatch.setattr(svc.author_media, '_proof', lambda *_: None)
        svc.reconcile_managed_scheduler()
        (unproved,) = schedule.history()
        assert unproved['status'] != 'saved'
        assert not schedule.pending_notifications() and not notifier.events
        monkeypatch.setattr(svc.author_media, '_proof', proofs)

        # Recover a completed operation after it leaves the displayed job window.
        monkeypatch.setattr(svc.static_jobs, 'snapshot', lambda: ())
        svc.live_client = RealFlowReader(B)
        svc.live_client.verify_identity()
        before = (tuple(schedule._db.iterdump()), svc.following_sync.snapshot(A), svc.following_sync.snapshot(B))
        assert not svc.reconcile_managed_scheduler()
        assert (tuple(schedule._db.iterdump()), svc.following_sync.snapshot(A), svc.following_sync.snapshot(B)) == before
        assert not notifier.events

        svc.live_client = reader
        with ThreadPoolExecutor(max_workers=2) as workers:
            list(workers.map(lambda _: svc.reconcile_managed_scheduler(), range(2)))
        (saved,) = schedule.history()
        assert saved['status'] == 'saved' and saved['accepted'] == 1 and saved['finished_utc']
        assert schedule.claim_pending_notifications(owner=schedule.capture_owner(svc, account_id=A)) == ()
        assert len(notifier.events) == 1
        assert svc.following_sync.snapshot(B) == before[2]
    finally:
        svc.close()
