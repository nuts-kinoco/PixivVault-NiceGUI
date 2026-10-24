"""Offline managed-only schedule persistence and concurrent-revision acceptance."""
from datetime import datetime, timedelta, timezone
import sqlite3
import time
from types import SimpleNamespace

import pytest

from pixivvault_nicegui.managed_scheduler import ManagedScheduler, ManagedScheduleRunner, ScheduleError
from pixivvault_nicegui.job_service import JobSnapshot


START = datetime(2026, 10, 24, 0, 0, tzinfo=timezone.utc)


def scheduler(tmp_path):
    root = tmp_path / 'managed'
    root.mkdir()
    return ManagedScheduler(root), root


def test_disabled_interval_and_startup_are_independent(tmp_path):
    service, _ = scheduler(tmp_path)
    try:
        initial = service.snapshot()
        assert initial.interval_hours == 24 and not initial.startup_check
        disabled = service.configure(interval_hours=0, startup_check=True,
                                     expected_revision=initial.revision, now=START)
        assert disabled.next_due_utc is None
        assert service.claim_due(START + timedelta(days=4)) is None
        slot = service.claim_due(START, startup=True)
        assert slot is not None
        service.complete(slot, succeeded=True)
        assert service.snapshot().last_status == 'queued'
    finally:
        service.close()


def test_due_slot_persists_without_replay_and_reboot(tmp_path):
    service, root = scheduler(tmp_path)
    initial = service.snapshot()
    service.configure(interval_hours=6, startup_check=False,
                      expected_revision=initial.revision, now=START)
    assert service.claim_due(START + timedelta(hours=5)) is None
    slot = service.claim_due(START + timedelta(hours=6))
    assert slot is not None
    assert service.claim_due(START + timedelta(hours=6)) is None
    service.close()  # An interrupted dispatch cannot silently become done.
    reopened = ManagedScheduler(root)
    try:
        assert reopened.snapshot().last_status == 'interrupted'
        assert reopened.claim_due(START + timedelta(hours=6)) is None
        assert reopened.claim_due(START + timedelta(hours=24)) is None
        reopened.complete(slot, succeeded=False)
        assert reopened.snapshot().last_status == 'error'
        assert reopened.claim_due(START + timedelta(hours=12)) is not None
    finally:
        reopened.close()


def test_interrupted_requires_explicit_ack_before_new_dispatch(tmp_path):
    service, _ = scheduler(tmp_path)
    try:
        service.configure(interval_hours=6, startup_check=False,
                          expected_revision=service.snapshot().revision, now=START)
        assert service.claim_due(START + timedelta(hours=6)) is not None
        assert service.claim_due(START + timedelta(hours=18)) is None
        state = service.snapshot()
        assert service.acknowledge_interrupted(expected_revision=state.revision).last_status == 'unknown'
        assert service.claim_due(START + timedelta(hours=18)) is not None
    finally:
        service.close()


def test_scheduler_refuses_a_second_writer_process(tmp_path):
    service, root = scheduler(tmp_path)
    try:
        with pytest.raises((OSError, ScheduleError)):
            ManagedScheduler(root)
    finally:
        service.close()
    recovered = ManagedScheduler(root)
    recovered.close()


def test_concurrent_client_revision_and_stale_completion(tmp_path):
    service, _ = scheduler(tmp_path)
    try:
        revision = service.snapshot().revision
        service.configure(interval_hours=12, startup_check=False,
                          expected_revision=revision, now=START)
        with pytest.raises(ScheduleError, match='another client'):
            service.configure(interval_hours=48, startup_check=True,
                              expected_revision=revision, now=START)
        slot = service.claim_due(START + timedelta(hours=12))
        assert slot is not None
        service.complete(slot, succeeded=True)
        with pytest.raises(ScheduleError, match='stale'):
            service.complete(slot, succeeded=True)
    finally:
        service.close()


def test_removing_favourite_prunes_its_deferred_work_atomically(tmp_path):
    service, _ = scheduler(tmp_path)
    work = {'id': '321', 'user_id': '91', 'type': 0,
            'update_date': '2026-10-24T00:00:00+00:00', 'page_count': 1}
    try:
        revision = service.snapshot().revision
        service.configure_all(author_ids=('91',), interval_hours=24,
                              startup_check=False, expected_revision=revision, now=START)
        service.defer_work(author_id='91', work=work, now=START)
        assert service.has_deferred()
        state = service.snapshot()
        service.configure_all(author_ids=(), interval_hours=24,
                              startup_check=False, expected_revision=state.revision, now=START)
        assert not service.has_deferred()
        assert service.deferred_ids('91') == frozenset()
        # An already-running dispatch may attempt this *after* the pruning
        # transaction. It must not repopulate the deleted author's backlog.
        service.defer_work(author_id='91', work=work, now=START)
        assert service.deferred_count() == 0
    finally:
        service.close()


def test_older_ledger_keeps_selected_backlog_and_prunes_removed_authors_on_open(tmp_path):
    root = tmp_path / 'managed'
    root.mkdir()
    path = root / '.pixivvault-managed-scheduler.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE favourite_authors(author_id TEXT PRIMARY KEY)')
        db.execute('INSERT INTO favourite_authors VALUES (?)', ('91',))
        db.execute('CREATE TABLE deferred_work (author_id TEXT NOT NULL, work_id TEXT NOT NULL, update_date TEXT NOT NULL, page_count INTEGER NOT NULL, work_type INTEGER NOT NULL, created_utc TEXT NOT NULL, PRIMARY KEY(author_id,work_id))')
        db.executemany('INSERT INTO deferred_work VALUES (?, ?, ?, ?, ?, ?)', (
            ('91', '123', '2026-10-24', 1, 0, START.isoformat()),
            ('92', '456', '2026-10-24', 1, 0, START.isoformat())))
    service = ManagedScheduler(root)
    try:
        assert service.favourites() == ('91',)
        assert service.deferred_ids('91') == frozenset({'123'})
        assert service.deferred_ids('92') == frozenset()
        assert service.deferred_count() == 1
    finally:
        service.close()


def test_unknown_legacy_saved_deferred_is_retired_without_enqueue(tmp_path):
    from threading import Event
    from types import SimpleNamespace
    from pixivvault_nicegui.managed_scheduler import submit_author_batch

    service, _ = scheduler(tmp_path)
    service.set_favourites(('91',), expected_revision=service.snapshot().revision)
    work = {'id': '123', 'user_id': '91', 'type': 0,
            'update_date': '2026-10-24T00:00:00+00:00', 'page_count': 1}
    service.defer_work(author_id='91', work=work, now=START)

    class FakeManagedDownloads:
        def saved_receipts_with_dates(self):
            return ()
        def verified_saved_receipt(self, work_id, *, verify_pages=False):
            return None

    class FakeLiveClient:
        def verify_identity(self):
            return 'synthetic-identity'
        def get_author_artworks_complete(self, author_id):
            assert author_id == '91'
            return [work]

    class FakeLegacyBridge:
        def assert_source_unchanged(self):
            pass
        def observe_author(self, author_id, works):
            assert author_id == '91' and works == [work]
            return SimpleNamespace(saved={
                '123': SimpleNamespace(update_date=None, deleted=False, files_present=True)})

    class FakeJobs:
        def snapshot(self):
            return ()

    fake = SimpleNamespace(
        managed_downloads=FakeManagedDownloads(), live_client=FakeLiveClient(),
        legacy_author_bridge=FakeLegacyBridge(), jobs=FakeJobs(), closing=False,
        release_managed_owner=lambda owner: None)
    try:
        result = submit_author_batch(fake, ('91',), cancellation=Event(),
                                     ledger=service, slot='test-unknown')
        assert result == {'accepted': 0, 'deferred': 1, 'authors': 1}
        assert service.deferred_count() == 0
        service.reconcile_jobs((), {})
        assert service.history()[0]['status'] == 'deferred'
    finally:
        service.close()


@pytest.mark.parametrize('value', [-1, 1, 2, 3, 25, 49, True, '24'])
def test_invalid_cadence_never_mutates_storage(tmp_path, value):
    service, _ = scheduler(tmp_path)
    try:
        before = service.snapshot()
        with pytest.raises(ValueError):
            service.configure(interval_hours=value, startup_check=False,
                              expected_revision=before.revision, now=START)
        assert service.snapshot() == before
    finally:
        service.close()


def test_runner_wake_does_not_set_cancellation_and_stop_exits(tmp_path):
    from threading import Event
    service, _ = scheduler(tmp_path)
    trigger = Event()
    observed = []

    def dispatch(slot, cancellation):
        observed.append((slot, cancellation.is_set()))
        trigger.set()

    clock = [START]
    runner = ManagedScheduleRunner(service, dispatch, clock=lambda: clock[0])
    try:
        assert runner.start()
        assert not runner.start()
        runner.settings_changed()
        assert not runner.stop_event.is_set()
        service.configure(interval_hours=6, startup_check=False,
                          expected_revision=service.snapshot().revision, now=START)
        clock[0] = START + timedelta(hours=6)
        runner.settings_changed()
        assert trigger.wait(3)
        assert observed == [(START.replace(hour=6).isoformat(timespec='microseconds'), False)]
    finally:
        runner.stop()
        service.close()


def test_runner_resumes_durable_backlog_after_restart_without_wake(tmp_path):
    from threading import Event
    service, _ = scheduler(tmp_path)
    service.set_favourites(('91',), expected_revision=service.snapshot().revision)
    service.defer_work(author_id='91', work={
        'id': '123', 'update_date': '2026-10-24',
        'page_count': 1, 'type': 0}, now=START)
    trigger = Event()
    dispatched = []

    def dispatch(slot, cancellation):
        dispatched.append(slot)
        service.resolve_deferred('91', '123')
        trigger.set()

    runner = ManagedScheduleRunner(service, dispatch, reconcile=lambda: True,
                                   clock=lambda: START)
    try:
        assert runner.start()
        # No settings_changed() or job-count transition: restart itself must
        # notice durable backlog once reconciliation confirms queue capacity.
        assert trigger.wait(8)
        assert len(dispatched) == 1 and dispatched[0].startswith('backlog-')
        assert not service.has_deferred()
    finally:
        runner.stop()
        service.close()


def test_runner_backs_off_when_successful_dispatch_does_not_reduce_backlog(tmp_path):
    from threading import Event
    service, _ = scheduler(tmp_path)
    service.set_favourites(('91',), expected_revision=service.snapshot().revision)
    service.defer_work(author_id='91', work={
        'id': '123', 'update_date': '2026-10-24', 'page_count': 1, 'type': 0}, now=START)
    observed = []
    first = Event()

    def dispatch(slot, cancellation):
        observed.append(time.monotonic())
        first.set()
        # No progress, but the callback returns normally.

    runner = ManagedScheduleRunner(service, dispatch, reconcile=lambda: True, clock=lambda: START)
    try:
        assert runner.start()
        runner.settings_changed()
        assert first.wait(4)
        time.sleep(0.2)
        assert len(observed) == 1
        assert service.deferred_count() == 1
    finally:
        runner.stop()
        service.close()


def test_runner_interrupted_barrier_blocks_deferred_backlog_until_ack(tmp_path):
    from threading import Event
    service, _ = scheduler(tmp_path)
    service.set_favourites(('91',), expected_revision=service.snapshot().revision)
    service.configure(interval_hours=6, startup_check=False,
                      expected_revision=service.snapshot().revision, now=START)
    slot = service.claim_due(START + timedelta(hours=6))
    assert slot is not None
    service.defer_work(author_id='91', work={
        'id': '123', 'update_date': '2026-10-24', 'page_count': 1, 'type': 0}, now=START)
    dispatched = Event()
    runner = ManagedScheduleRunner(
        service, lambda slot, cancel: dispatched.set(), reconcile=lambda: True,
        clock=lambda: START + timedelta(hours=6))
    try:
        assert runner.start()
        runner.settings_changed()
        assert not dispatched.wait(0.3)
        runner.acknowledge_interrupted(expected_revision=service.snapshot().revision)
        runner.settings_changed()
        assert dispatched.wait(4)
    finally:
        runner.stop()
        service.close()


def test_explicit_favourites_are_durable_and_revision_protected(tmp_path):
    service, root = scheduler(tmp_path)
    rev = service.snapshot().revision
    try:
        assert service.favourites() == ()
        assert service.set_favourites(('91', '92', '91'), expected_revision=rev) == ('91', '92')
        with pytest.raises(ScheduleError, match='another client'):
            service.set_favourites(('93',), expected_revision=rev)
        assert service.favourites() == ('91', '92')
    finally:
        service.close()
    reopened = ManagedScheduler(root)
    try:
        assert reopened.favourites() == ('91', '92')
    finally:
        reopened.close()


def test_atomic_settings_prevent_stale_client_overwrite(tmp_path):
    service, _ = scheduler(tmp_path)
    try:
        revision = service.snapshot().revision
        changed = service.configure_all(
            author_ids=('91',), interval_hours=12, startup_check=True,
            expected_revision=revision, now=START)
        assert changed.interval_hours == 12
        with pytest.raises(ScheduleError, match='another client'):
            service.configure_all(
                author_ids=('92',), interval_hours=6, startup_check=False,
                expected_revision=revision, now=START)
        assert service.snapshot() == changed
        assert service.favourites() == ('91',)
    finally:
        service.close()


def test_claim_and_ack_share_one_reservation_lock(tmp_path):
    service, _ = scheduler(tmp_path)
    try:
        service.configure(interval_hours=6, startup_check=False,
                          expected_revision=service.snapshot().revision, now=START)
        runner = ManagedScheduleRunner(service, lambda slot, cancel: None,
                                       clock=lambda: START + timedelta(hours=6))
        slot = runner._claim_reserved(START + timedelta(hours=6))
        assert slot is not None
        with pytest.raises(ScheduleError, match='active'):
            runner.acknowledge_interrupted(expected_revision=service.snapshot().revision)
        runner._dispatch_slot(slot)
        assert service.snapshot().last_status == 'queued'
    finally:
        service.close()


def test_deferred_over_50_persists_across_restart(tmp_path):
    service, root = scheduler(tmp_path)
    try:
        service.set_favourites(('91',), expected_revision=service.snapshot().revision)
        for index in range(1, 76):
            service.defer_work(author_id='91', work={
                'id': str(index), 'update_date': '2026-10-24',
                'page_count': 1, 'type': 0}, now=START)
        assert len(service.deferred_ids('91')) == 75
    finally:
        service.close()
    reopened = ManagedScheduler(root)
    try:
        assert len(reopened.deferred_ids('91')) == 75
        assert reopened.has_deferred()
        reopened.resolve_deferred('91', '1')
        assert len(reopened.deferred_ids('91')) == 74
        reopened.prune_deferred_inventory('91', {str(index) for index in range(3, 76)})
        assert len(reopened.deferred_ids('91')) == 73
    finally:
        reopened.close()


def test_no_item_deferred_history_does_not_remain_queued(tmp_path):
    service, _ = scheduler(tmp_path)
    try:
        service.begin_dispatch('unsupported-only', START, 1)
        service.finish_dispatch('unsupported-only', status='queued', accepted=0,
                                deferred=3, now=START)
        service.reconcile_jobs((), {})
        assert service.history()[0]['status'] == 'deferred'
    finally:
        service.close()


def test_failed_author_get_history_cannot_be_rewritten_as_saved(tmp_path):
    service, _ = scheduler(tmp_path)
    try:
        slot = 'later-author-get-failed'
        service.begin_dispatch(slot, START, 2)
        service.prepare_dispatch_item(slot=slot, work_id='123',
                                      author_id='91', operation_id='op-123')
        service.bind_dispatch_job(slot=slot, work_id='123',
                                  operation_id='op-123', job_id='job-123',
                                  accepted_fingerprint='a' * 64)
        service.finish_dispatch(slot, status='failed', accepted=1,
                                deferred=0, error_code='ReadError', now=START)
        job = JobSnapshot('job-123', 'pixiv-illust', '123', 'First', state='done')
        service.reconcile_jobs((job,), {'123': 'a' * 64})
        assert service.history()[0]['status'] == 'failed'
        assert service.pending_notifications() == ('123',)
    finally:
        service.close()


def test_scheduler_ui_dirty_form_pins_cas_revision_and_suppresses_programmatic_events(tmp_path):
    from pixivvault_nicegui.ui.scheduler_view import SchedulerView
    service, _ = scheduler(tmp_path)
    try:
        view = object.__new__(SchedulerView)
        view.revision = None
        view._settings_dirty = False
        view._loading_settings = False

        class Widget:
            def __init__(self):
                self.value = None
            def set_value(self, value):
                self.value = value
                view._mark_settings_dirty()
            def set_text(self, value):
                self.value = value
            def set_enabled(self, value):
                self.value = value

        view.interval, view.startup, view.authors = Widget(), Widget(), Widget()
        view.status, view.ack_button = Widget(), Widget()
        view._load(service)
        initial_revision = view.revision
        assert initial_revision == service.snapshot().revision
        assert not view._settings_dirty  # load's set_value is not an edit

        view.authors.set_value('91')  # Actual user edit.
        assert view._settings_dirty
        service.configure_all(author_ids=('92',), interval_hours=6, startup_check=True,
                              expected_revision=initial_revision, now=START)
        view._load(service)  # Five-second refresh during the dirty edit.
        assert view.revision == initial_revision
        assert view.authors.value == '91'
        with pytest.raises(ScheduleError, match='another client'):
            service.configure_all(author_ids=('91',), interval_hours=24, startup_check=False,
                                  expected_revision=view.revision, now=START)
        view.closed = False
        view._busy = False
        view.services = SimpleNamespace(managed_scheduler=service)
        view.discard_changes()
        assert not view._settings_dirty
        assert view.revision == service.snapshot().revision
        assert view.authors.value == '92'
        assert view.interval.value == '6'
    finally:
        service.close()


@pytest.mark.asyncio
async def test_scheduler_ui_reports_progress_and_admission_before_slow_reconciliation(monkeypatch):
    """The user must see acknowledgement before receipt reconciliation finishes."""
    import asyncio
    from threading import Event, RLock
    from types import SimpleNamespace
    import pixivvault_nicegui.managed_scheduler as scheduler_module
    from pixivvault_nicegui.ui.scheduler_view import SchedulerView

    progress_seen = Event()
    allow_dispatch_to_finish = Event()
    refresh_started = asyncio.Event()
    allow_refresh_to_finish = asyncio.Event()

    class Widget:
        def __init__(self):
            self.value = None
        def set_text(self, value):
            self.value = value
        def set_enabled(self, value):
            self.value = value

    view = object.__new__(SchedulerView)
    view.closed = False
    view._busy = False
    view._settings_dirty = False
    view.run_button = Widget()
    view.save_button = Widget()
    view.action_notice = Widget()
    view.history_label = Widget()
    view._background_refresh_tasks = set()
    view._refreshing = False
    view._refresh_pending = False
    view.services = SimpleNamespace(
        closing=False,
        managed_scheduler=SimpleNamespace(favourites=lambda: ('91',)),
        _scheduler_dispatch_lock=RLock(),
        _scheduler_cancel_event=Event(),
    )

    def fake_submit(services, author_ids, **kwargs):
        assert view.action_notice.value == '作者一覧を確認中です。受付件数を集計しています…'
        progress_seen.set()
        assert allow_dispatch_to_finish.wait(3)
        return {'accepted': 50, 'deferred': 20}

    async def slow_refresh(*, ensure_after_current=False):
        refresh_started.set()
        await allow_refresh_to_finish.wait()

    view.refresh = slow_refresh
    monkeypatch.setattr(scheduler_module, 'submit_author_batch', fake_submit)
    task = asyncio.create_task(view.run_once())
    try:
        assert await asyncio.to_thread(progress_seen.wait, 1)
        assert view.action_notice.value == '作者一覧を確認中です。受付件数を集計しています…'
        allow_dispatch_to_finish.set()
        await asyncio.wait_for(refresh_started.wait(), timeout=2)
        await asyncio.wait_for(task, timeout=2)
        assert view.action_notice.value == (
            '受付 50件、保留 20件。保存結果はキューで確認してください')
        assert view.history_label.value == '今回のチェック: 受付 50件 / 保留 20件 / 保存結果を確認中'
        assert view.run_button.value is True
        assert view.save_button.value is True
        allow_refresh_to_finish.set()
    finally:
        allow_dispatch_to_finish.set()
        allow_refresh_to_finish.set()
        if view._background_refresh_tasks:
            await asyncio.gather(*tuple(view._background_refresh_tasks), return_exceptions=True)
        if not task.done():
            await task


@pytest.mark.asyncio
async def test_scheduler_background_refresh_emits_claimed_notification_in_view_context(tmp_path, monkeypatch):
    """A fresh task must restore its NiceGUI slot before emitting notifications."""
    import asyncio
    from nicegui import Client, ui, context
    from pixivvault_nicegui.ui.scheduler_view import SchedulerView

    class Ledger:
        def __init__(self):
            self.claims = 0
        def snapshot(self):
            return SimpleNamespace(revision=1, interval_hours=0, startup_check=False,
                                   last_status='saved', next_due_utc=None)
        def favourites(self):
            return ()
        def claim_pending_notifications(self):
            self.claims += 1
            return ('123',) if self.claims == 1 else ()
        def history(self):
            return ({'status':'saved','accepted':1,'deferred':0,
                     'finished_utc':'2026-10-24T20:00:00+00:00'},)

    ledger = Ledger()
    services = SimpleNamespace(managed_scheduler=ledger,
                               reconcile_managed_scheduler=lambda: None)
    client = Client(ui.page('/_scheduler_background_notification'))
    original_notify = ui.notify
    emitted = []

    def assert_client_notice(message, **kwargs):
        assert context.client is client, 'background notification lost the view client context'
        emitted.append((message, kwargs.get('type')))
        original_notify(message, **kwargs)

    monkeypatch.setattr(ui, 'notify', assert_client_notice)
    try:
        with client:
            view = SchedulerView(services)
            view._schedule_refresh()
            await asyncio.wait_for(asyncio.gather(*tuple(view._background_refresh_tasks)), timeout=3)
            assert ledger.claims == 1
            assert emitted == [('保存完了: 1件', 'positive')]
            assert view.action_notice.text == ''  # No swallowed background toast exception.
            assert '完了' in view.history_label.text
            assert '定期状態: 完了' in view.status.text
            assert '状態を取得できませんでした' not in view.status.text
            view.close()
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_scheduler_refresh_requested_during_active_refresh_is_not_dropped():
    import asyncio
    from threading import Event, Lock
    from nicegui import Client, ui
    from pixivvault_nicegui.ui.scheduler_view import SchedulerView

    first_started, release_first, second_started = Event(), Event(), Event()
    guard = Lock()
    calls = 0

    class Ledger:
        def snapshot(self):
            return SimpleNamespace(revision=1, interval_hours=0, startup_check=False,
                                   last_status='never', next_due_utc=None)
        def favourites(self):
            return ()
        def claim_pending_notifications(self):
            return ()
        def history(self):
            return ()

    def reconcile():
        nonlocal calls
        with guard:
            calls += 1
            current = calls
        if current == 1:
            first_started.set()
            assert release_first.wait(3)
        else:
            second_started.set()

    client = Client(ui.page('/_scheduler_coalesced_refresh'))
    try:
        with client:
            view = SchedulerView(SimpleNamespace(managed_scheduler=Ledger(),
                                                 reconcile_managed_scheduler=reconcile))
            first = asyncio.create_task(view.refresh())
            assert await asyncio.to_thread(first_started.wait, 1)
            view._schedule_refresh()
            await asyncio.sleep(0)
            assert view._refresh_pending
            release_first.set()
            await asyncio.wait_for(first, timeout=3)
            assert await asyncio.to_thread(second_started.wait, 2)
            if view._background_refresh_tasks:
                await asyncio.wait_for(asyncio.gather(*tuple(view._background_refresh_tasks)), timeout=3)
            assert calls == 2
            view.close()
    finally:
        release_first.set()
        client.delete()


@pytest.mark.asyncio
async def test_scheduler_timer_ticks_do_not_queue_unbounded_refreshes():
    import asyncio
    from threading import Event, Lock
    from nicegui import Client, ui
    from pixivvault_nicegui.ui.scheduler_view import SchedulerView

    started, release = Event(), Event()
    lock = Lock()
    calls = 0

    class Ledger:
        def snapshot(self):
            return SimpleNamespace(revision=1, interval_hours=0, startup_check=False,
                                   last_status='never', next_due_utc=None)
        def favourites(self): return ()
        def claim_pending_notifications(self): return ()
        def history(self): return ()

    def reconcile():
        nonlocal calls
        with lock:
            calls += 1
            current = calls
        if current == 1:
            started.set()
            assert release.wait(3)

    client = Client(ui.page('/_scheduler_timer_refresh_coalescing'))
    try:
        with client:
            view = SchedulerView(SimpleNamespace(managed_scheduler=Ledger(),
                                                 reconcile_managed_scheduler=reconcile))
            first = asyncio.create_task(view.refresh())  # periodic timer semantics
            assert await asyncio.to_thread(started.wait, 1)
            await view.refresh()  # another timer tick must not queue a follow-up
            assert not view._refresh_pending
            release.set()
            await asyncio.wait_for(first, timeout=3)
            await asyncio.sleep(.05)
            assert calls == 1
            view.close()
    finally:
        release.set()
        client.delete()


@pytest.mark.asyncio
async def test_scheduler_notification_failure_does_not_hide_durable_history(tmp_path, monkeypatch):
    import asyncio
    from nicegui import Client, ui
    from pixivvault_nicegui.ui.scheduler_view import SchedulerView

    class Ledger:
        def snapshot(self):
            return SimpleNamespace(revision=1, interval_hours=0, startup_check=False,
                                   last_status='saved', next_due_utc=None)
        def favourites(self): return ()
        def claim_pending_notifications(self): return ('123',)
        def history(self):
            return ({'status':'saved','accepted':1,'deferred':0,
                     'finished_utc':'2026-10-24T20:00:00+00:00'},)

    monkeypatch.setattr(ui, 'notify', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('disconnected')))
    client = Client(ui.page('/_scheduler_notification_delivery_failure'))
    try:
        with client:
            view = SchedulerView(SimpleNamespace(managed_scheduler=Ledger(),
                                                 reconcile_managed_scheduler=lambda: None))
            await view.refresh(ensure_after_current=True)
            assert '最終実行: 完了' in view.history_label.text
            assert '通知を表示できませんでした' in view.action_notice.text
            assert '状態を取得できませんでした' not in view.status.text
            view.close()
    finally:
        client.delete()


def test_job_receipt_reconciliation_and_notification_are_idempotent(tmp_path):
    service, _ = scheduler(tmp_path)
    try:
        slot = 'manual-test-slot'
        accepted_fingerprint = 'a' * 64
        service.begin_dispatch(slot, START, 1)
        service.prepare_dispatch_item(slot=slot, work_id='123', author_id='91', operation_id='op-123')
        service.finish_dispatch(slot, status='queued', accepted=1, deferred=0, now=START)
        assert service.reconcile_candidates() == ('123',)
        job = JobSnapshot('job-1', 'pixiv-illust', '123', 'Synthetic', state='done')
        operation_to_job = {'op-123': 'job-1'}
        operation_to_fingerprint = {'op-123': accepted_fingerprint}
        # An older valid active generation must not satisfy a newer update job.
        service.reconcile_jobs((job,), {'123': 'b' * 64},
                               operation_to_job, operation_to_fingerprint)
        assert service.pending_notifications() == ()  # done without receipt is not success
        assert service.history()[0]['status'] == 'unknown'
        service.reconcile_jobs((job,), {'123': accepted_fingerprint},
                               operation_to_job, operation_to_fingerprint)
        assert service.pending_notifications() == ('123',)
        assert service.history()[0]['status'] == 'saved'
        # Without a browser tab claiming the toast, unattended reconciliation
        # must not repeatedly hash/decode the same saved image.
        assert service.reconcile_candidates() == ()
        # A later active generation may replace this receipt before the toast
        # is claimed. The earlier exact-fingerprint success remains historical
        # truth and must retain its pending one-time notification.
        service.reconcile_jobs((job,), {'123': 'c' * 64},
                               operation_to_job, operation_to_fingerprint)
        assert service.pending_notifications() == ('123',)
        assert service.history()[0]['status'] == 'saved'
        assert service.claim_pending_notifications() == ('123',)
        assert service.claim_pending_notifications() == ()
        service.mark_notified(('123',))
        assert service.pending_notifications() == ()
        assert service.reconcile_candidates() == ()
        service.reconcile_jobs((job,), {}, operation_to_job, operation_to_fingerprint)
        assert service.pending_notifications() == ()
        assert service.history()[0]['status'] == 'saved'
    finally:
        service.close()


def test_unattended_unknown_old_active_receipt_never_rehashes_pages(tmp_path):
    from pixivvault_nicegui.services import ApplicationServices
    service, _ = scheduler(tmp_path)
    slot = 'ambiguous-update'
    latest_fingerprint = 'a' * 64
    old_fingerprint = 'b' * 64
    service.begin_dispatch(slot, START, 1)
    service.prepare_dispatch_item(slot=slot, work_id='123', author_id='91',
                                  operation_id='op-update')
    service.bind_dispatch_job(slot=slot, work_id='123', operation_id='op-update',
                              job_id='job-update', accepted_fingerprint=latest_fingerprint)
    service.finish_dispatch(slot, status='queued', accepted=1, deferred=0, now=START)
    job = JobSnapshot('job-update', 'pixiv-illust', '123', 'Update', state='done')
    service.reconcile_jobs((job,), {'123': old_fingerprint})
    assert service.history()[0]['status'] == 'unknown'

    class SavedSpy:
        active_fingerprint = old_fingerprint
        cheap_reads = 0
        full_reads = 0
        def verified_saved_receipt(self, work_id, *, verify_pages=True):
            assert work_id == '123'
            if verify_pages:
                self.full_reads += 1
            else:
                self.cheap_reads += 1
            return ('outputs/demo/receipt.json', {'work_id': work_id},
                    'receipt-sha', '2026-10-24', self.active_fingerprint)

    class Jobs:
        def snapshot(self):
            return (job,)
        def operation_ids(self, job_id):
            return ('op-update',)

    saved = SavedSpy()
    app = SimpleNamespace(closing=False, managed_scheduler=service,
                          jobs=Jobs(), managed_downloads=saved,
                          _managed_job_records={}, _scheduler_unfinished_count=None,
                          managed_schedule_runner=None)
    try:
        for _ in range(5):
            ApplicationServices.reconcile_managed_scheduler(app)
        assert saved.cheap_reads == 5
        assert saved.full_reads == 0  # No old-image hash/decode every 5 seconds.
        assert service.history()[0]['status'] == 'unknown'

        saved.active_fingerprint = latest_fingerprint
        ApplicationServices.reconcile_managed_scheduler(app)
        assert saved.full_reads == 1  # Late exact CAS generation verified once.
        assert service.history()[0]['status'] == 'saved'
        assert service.pending_notifications() == ('123',)
        for _ in range(3):
            ApplicationServices.reconcile_managed_scheduler(app)
        assert saved.full_reads == 1  # Saved-but-unnotified does not rehash.
    finally:
        service.close()


def test_failed_ambiguous_enqueue_recovers_via_production_reconcile(tmp_path):
    from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
    from pixivvault_nicegui.managed_download_requests import (
        ManagedAuthorIntent, accepted_author_fingerprint,
    )
    from pixivvault_nicegui.services import ApplicationServices

    service, _ = scheduler(tmp_path)
    job_id = 'd' * 32
    record = RemoteBookmark('123', 'illustration', 'show', 'Synthetic',
                            '91', 'Author', ('tag',), 'https://i.pximg.net/fake',
                            '2026', 1, 0, 0)
    intent = ManagedAuthorIntent(record, 'follow', '2026-10-24', None, 'new')
    expected = accepted_author_fingerprint(intent, job_id)
    slot, operation = 'ambiguous-enqueue', 'op-crash-window'
    service.begin_dispatch(slot, START, 1)
    service.prepare_dispatch_item(slot=slot, work_id='123', author_id='91',
                                  operation_id=operation)
    service.fail_dispatch_item(slot=slot, work_id='123', operation_id=operation)
    service.finish_dispatch(slot, status='failed', accepted=0, deferred=0,
                            error_code='CrashWindow', now=START)
    assert service.reconcile_candidates() == ('123',)

    job = JobSnapshot(job_id, 'pixiv-illust', '123', 'Synthetic', state='done')

    class RecoveredJobs:
        def snapshot(self):
            return (job,)
        def operation_ids(self, actual_job_id):
            assert actual_job_id == job_id
            return (operation,)

    class Receipt:
        full = 0
        def verified_saved_receipt(self, work_id, *, verify_pages=True):
            assert work_id == '123'
            if verify_pages:
                self.full += 1
            return ('generations/test/receipt.json', {'work_id': work_id},
                    'sha', '2026-10-24', expected)

    receipt = Receipt()
    app = SimpleNamespace(closing=False, managed_scheduler=service,
                          jobs=RecoveredJobs(), managed_downloads=receipt,
                          _managed_job_records={job_id: intent},
                          _scheduler_unfinished_count=None,
                          managed_schedule_runner=None)
    try:
        ApplicationServices.reconcile_managed_scheduler(app)
        assert receipt.full == 1
        assert service.pending_notifications() == ('123',)
        assert service.reconcile_candidates() == ()
        assert service.history()[0]['status'] == 'failed'  # Preserve failed dispatch facts.
        assert service._db.execute(
            'SELECT job_id,state,accepted_fingerprint FROM dispatch_items WHERE work_id=?',
            ('123',)).fetchone() == (job_id, 'saved', expected)
    finally:
        service.close()


def test_partial_history_recovers_when_ambiguous_enqueue_is_later_verified_saved(tmp_path):
    service, _ = scheduler(tmp_path)
    try:
        slot = 'recover-partial-slot'
        first_fp, second_fp = 'a' * 64, 'b' * 64
        service.begin_dispatch(slot, START, 1)
        service.prepare_dispatch_item(slot=slot, work_id='123', author_id='91', operation_id='op-123')
        service.bind_dispatch_job(slot=slot, work_id='123', operation_id='op-123',
                                  job_id='job-123', accepted_fingerprint=first_fp)
        service.prepare_dispatch_item(slot=slot, work_id='124', author_id='91', operation_id='op-124')
        service.fail_dispatch_item(slot=slot, work_id='124', operation_id='op-124')
        service.finish_dispatch(slot, status='queued', accepted=2, deferred=0, now=START)

        first = JobSnapshot('job-123', 'pixiv-illust', '123', 'First', state='done')
        service.reconcile_jobs((first,), {'123': first_fp},
                               {'op-123': 'job-123'}, {'op-123': first_fp})
        assert service.history()[0]['status'] == 'partial'

        # A crash can occur after JobService accepts an operation but before
        # the dispatch ledger binds its job ID/fingerprint. Recovery later
        # proves the receipt, so the durable slot must be allowed to advance
        # from partial to saved rather than preserving a stale aggregate state.
        second = JobSnapshot('job-124', 'pixiv-illust', '124', 'Second', state='done')
        service.reconcile_jobs((first, second), {'123': first_fp, '124': second_fp},
                               {'op-123': 'job-123', 'op-124': 'job-124'},
                               {'op-123': first_fp, 'op-124': second_fp})
        assert service.history()[0]['status'] == 'saved'
        assert service.pending_notifications() == ('123', '124')
    finally:
        service.close()


def test_existing_dispatch_ledger_adds_fingerprint_column_safely(tmp_path):
    root = tmp_path / 'managed'
    root.mkdir()
    path = root / '.pixivvault-managed-scheduler.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE dispatch_items (slot TEXT NOT NULL, work_id TEXT NOT NULL, author_id TEXT NOT NULL, operation_id TEXT NOT NULL UNIQUE, job_id TEXT, state TEXT NOT NULL, notified INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(slot,work_id))')
    service = ManagedScheduler(root)
    try:
        columns = {row[1] for row in service._db.execute('PRAGMA table_info(dispatch_items)')}
        assert 'accepted_fingerprint' in columns
        assert service.snapshot().revision == 1
    finally:
        service.close()
