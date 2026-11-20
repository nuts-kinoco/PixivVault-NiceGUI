"""R2: the real follow-sync publication invalidates pending scheduler actions."""
import asyncio
from datetime import datetime, timezone
from threading import Event, RLock
from types import SimpleNamespace as NS

import pytest
from nicegui import Client, ui

from pixivvault_nicegui.account_followup import AccountFollowup
from pixivvault_nicegui.account_scope import AccountMismatch
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.managed_scheduler import ManagedScheduleRunner
from pixivvault_nicegui.ui.personal_ranking_view import PersonalRankingView
from pixivvault_nicegui.ui.scheduler_view import SchedulerView
from test_ac_library_search_20261119 import until
from test_w_account_separation_20261119 import A, B, X, Reader, resources
from test_y_ranking_personal_20261118 import build, settle


def populate(store, ledger, reader, services):
    store.sync(reader)
    store.set_favorite(A, X, True)
    services.scheduler_favourite_authors(ledger=ledger)
    services.reconcile_managed_scheduler = lambda: None
    services._scheduler_dispatch_lock = RLock()
    services.jobs = NS(snapshot=lambda: ())
    services.managed_schedule_runner = ManagedScheduleRunner(ledger, lambda *_: None)


def seed_history(ledger, *, saved=False):
    slot = 'isolated-check'
    ledger.begin_dispatch(slot, datetime.now(timezone.utc), 1, account_id=A)
    if saved:
        ledger.prepare_dispatch_item(slot=slot, work_id='200', author_id=X,
                                     operation_id='isolated-operation', account_id=A)
        ledger.bind_dispatch_job(slot=slot, work_id='200', operation_id='isolated-operation',
                                 job_id='isolated-job', accepted_fingerprint='verified', account_id=A)
    ledger.finish_dispatch(slot, status='queued', accepted=7, deferred=3, account_id=A)
    if saved:
        ledger.reconcile_jobs((NS(job_id='isolated-job', state='done'),), {'200': 'verified'}, account_id=A)


async def confirm_switch(store, services):
    binding = NS(_closed=False, _configured_cookie=lambda: object(),
                 _reader_lock=RLock(), _requests={})
    followup = AccountFollowup(binding)
    reader = Reader(B)
    followup.reader_factory = lambda _: reader
    followup.store_provider = lambda: store
    await followup.after_confirmed(B)
    assert followup.pending is not None
    services.live_client = reader
    await followup.confirm_account_switch()
    assert store.last_synced_account() == B


@pytest.mark.asyncio
async def test_scheduler_discards_visible_owner_on_confirmed_switch_without_poll(tmp_path):
    with resources(tmp_path) as (_, store, ledger, reader, services):
        populate(store, ledger, reader, services)
        seed_history(ledger, saved=True)
        client = Client(ui.page('/_r2_visible_owner'))
        try:
            with client:
                view = SchedulerView(services)
                view._timer.cancel()
                await view.refresh()
                assert view.authors.visible and view.authors.value == X
                assert f'ID {A}' in view.account_label.text and '受付 7件' in view.history_label.text
                assert view._completion_notification is not None
                view.interval.set_value('6')
                await confirm_switch(store, services)
                assert not view.authors.visible and not view.authors.value
                assert f'ID {A}' not in view.account_label.text
                assert not view.history_label.visible and not view.history_label.text
                assert view._completion_notification is None and not view.action_notice.text
                assert not view.ack_button.enabled and not view.run_button.enabled
                view.close()
        finally:
            client.delete()


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ('acknowledge_interrupted', 'claim_pending_notifications'))
async def test_scheduler_refuses_commit_after_confirmed_switch(tmp_path, monkeypatch, operation):
    with resources(tmp_path) as (_, store, ledger, reader, services):
        populate(store, ledger, reader, services)
        ledger._claim_due(datetime.fromisoformat(ledger.snapshot().next_due_utc))
        before = ledger.snapshot()
        assert before.last_status == 'interrupted'
        client = Client(ui.page('/_r2_owner_commit_' + operation))
        entered, release = Event(), Event()
        task = None
        try:
            with client:
                view = SchedulerView(services)
                view._timer.cancel()
                view._load(ledger)
                if operation == 'claim_pending_notifications':
                    seed_history(ledger, saved=True)
                    assert ledger.pending_notifications() == ('200',)
                original = getattr(ledger, operation)

                def delay(*args, **kwargs):
                    entered.set()
                    assert release.wait(5)
                    return original(*args, **kwargs)

                monkeypatch.setattr(ledger, operation, delay)
                pending = view.acknowledge() if operation == 'acknowledge_interrupted' else view.refresh()
                task = asyncio.create_task(pending)
                await until(entered.is_set)
                await confirm_switch(store, services)
                release.set()
                await task
                assert ledger.snapshot() == before
                if operation == 'claim_pending_notifications':
                    assert ledger.pending_notifications() == ('200',)
                view.close()
        finally:
            release.set()
            if task is not None:
                await task
            client.delete()


@pytest.mark.asyncio
async def test_personal_typing_tags_never_changes_the_active_query(monkeypatch):
    services, vault, client = await build(monkeypatch, 'r2_tag_draft')
    try:
        with client:
            view = PersonalRankingView(services)
            view.set_active(True)
            await view._context_task
            for text in ('c', 'ca', 'cat'):
                view.tags.set_value(text)
                await asyncio.sleep(.18)
                await settle(view)
            assert vault.calls == []
            assert view.selected_tags() == ()
            submit = next(event.handler for event in view.tags._event_listeners.values()
                          if event.type == 'keydown.enter')
            submit(NS(args=view.tags.value))
            await settle(view)
            assert [call['word'] for call in vault.calls] == ['cat']
            view.tags.set_value('caterpillar')
            view.bookmarks.set_value(1000)
            await settle(view)
            assert [(call['word'], call['bookmark_min']) for call in vault.calls] == [('cat', 0), ('cat', 1000)]
            view.close()
    finally:
        client.delete()
        services.close()




@pytest.mark.asyncio
async def test_scheduler_owner_proof_is_not_reused_after_switching_back(tmp_path):
    with resources(tmp_path) as (_, store, ledger, reader, services):
        populate(store, ledger, reader, services)
        seed_history(ledger, saved=True)
        owner = ledger.capture_owner(services, account_id=A)
        await confirm_switch(store, services)
        store.sync(reader)
        services.live_client = reader
        with pytest.raises(AccountMismatch):
            ledger.mark_notified(('200',), owner=owner)
        assert ledger.pending_notifications() == ('200',)
        current = ledger.capture_owner(services, account_id=A)
        assert ledger.claim_pending_notifications(owner=current) == ('200',)


@pytest.mark.asyncio
async def test_desktop_notification_waiting_to_claim_cannot_consume_old_owner(tmp_path, monkeypatch):
    with resources(tmp_path) as (_, store, ledger, reader, services):
        populate(store, ledger, reader, services)
        seed_history(ledger, saved=True)
        services.managed_downloads = NS(verified_saved_receipt=lambda *_args, **_kwargs: None)
        services._scheduler_unfinished_count = None
        notifications = []
        services.notifications = NS(notify=lambda *args: notifications.append(args))
        entered, release = Event(), Event()
        original = ledger.claim_pending_notifications

        def delay(*args, **kwargs):
            entered.set()
            assert release.wait(5)
            return original(*args, **kwargs)

        monkeypatch.setattr(ledger, 'claim_pending_notifications', delay)
        task = asyncio.create_task(asyncio.to_thread(ApplicationServices.reconcile_managed_scheduler, services))
        try:
            await until(entered.is_set)
            await confirm_switch(store, services)
        finally:
            release.set()
            await task
        assert ledger.pending_notifications() == ('200',)
        assert notifications == []


@pytest.mark.asyncio
async def test_unverified_editor_can_still_save_the_global_off_switch(tmp_path):
    with resources(tmp_path) as (_, store, ledger, reader, services):
        populate(store, ledger, reader, services)
        reader._verified_identity = None
        client = Client(ui.page('/_r2_global_scheduler_off'))
        try:
            with client:
                view = SchedulerView(services)
                view._load(ledger)
                assert not view.authors.visible
                view.interval.set_value('0')
                assert view.save_button.enabled
                await view.save()
                assert ledger.snapshot().interval_hours == 0
                assert not view.authors.visible
                view.close()
        finally:
            client.delete()
