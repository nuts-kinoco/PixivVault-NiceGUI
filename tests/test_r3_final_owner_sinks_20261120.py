"""Final R3 owner boundaries, using the real confirmation with synthetic owners."""
import asyncio
import builtins
from datetime import datetime, timezone
from inspect import Parameter, getmembers, isroutine, signature
import sys
from threading import Event
from types import SimpleNamespace as NS

import pytest
from nicegui import Client, ui

from account_test_support import observed_scope
from pixivvault_nicegui.account_scope import AccountMismatch
from pixivvault_nicegui.managed_scheduler import ScheduleAccountError
from pixivvault_nicegui.notifications import NotificationDispatcher, WindowsToastBackend
from pixivvault_nicegui.queue_log import QueueLog
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.shared_log_view import SharedLogView
from test_ac_library_search_20261119 import until
from test_r3_owner_commit_20261120 import confirm_switch, populate
from test_w_account_separation_20261119 import A, B, X, Y, Reader, resources


@pytest.mark.asyncio
async def test_initial_bind_rejects_owner_changed_after_selection(tmp_path, monkeypatch):
    with resources(tmp_path) as (_, store, ledger, reader, services):
        store.sync(reader)
        store.set_favorite(A, X, True)
        original = ledger.bind_account
        entered, release = Event(), Event()

        def delay(*args, **kwargs):
            entered.set()
            assert release.wait(5)
            return original(*args, **kwargs)

        monkeypatch.setattr(ledger, 'bind_account', delay)
        task = asyncio.create_task(asyncio.to_thread(services.scheduler_favourite_authors, ledger=ledger))
        try:
            await until(entered.is_set)
            await confirm_switch(store, services, update_live=False)
            release.set()
            with pytest.raises((AccountMismatch, ScheduleAccountError)):
                await task
            assert ledger._bound_account() is None and ledger.history() == ()
            services.live_client = Reader(B)
            store.set_favorite(B, Y, True)
            assert services.scheduler_favourite_authors(ledger=ledger) == (Y,)
            assert ledger._bound_account() == B
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('switch_before_reconcile', (True, False), ids=('old-receipt', 'queued-log'))
async def test_old_owner_scheduler_receipt_does_not_render_on_new_owner(tmp_path, switch_before_reconcile):
    with resources(tmp_path) as (_, store, ledger, reader, services):
        populate(store, ledger, reader, services)
        fingerprint = 'fixture-fingerprint'
        ledger.begin_dispatch('slot', datetime.now(timezone.utc), 1, account_id=A)
        ledger.prepare_dispatch_item(slot='slot', work_id='200', author_id=X, operation_id='fixture', account_id=A)
        ledger.bind_dispatch_job(slot='slot', work_id='200', operation_id='fixture',
                                 job_id='job', accepted_fingerprint=fingerprint, account_id=A)
        ledger.finish_dispatch('slot', status='queued', accepted=1, deferred=0, account_id=A)
        receipt = (None, {'work_id': '200'}, None, None, fingerprint)
        services.managed_downloads = NS(verified_saved_receipt=lambda *_args, **_kw: receipt)
        services.shared_log = QueueLog()
        services.shared_log.append('synthetic common log')
        services._scheduler_unfinished_count = None
        if switch_before_reconcile:
            await confirm_switch(store, services, update_live=False)
        assert ApplicationServices.reconcile_managed_scheduler(services)
        assert ledger.history()[0]['status'] == 'saved'
        assert ledger.pending_notifications() == ('200',)
        if not switch_before_reconcile:
            assert len(services.shared_log.snapshot()) == 2
            await confirm_switch(store, services, update_live=False)

        client = Client(ui.page('/_r3_old_log_' + str(switch_before_reconcile)))
        try:
            with client:
                view = SharedLogView(services.shared_log)
                view.show_tab('scheduler')
                assert [child.text.split('] ', 1)[1] for child in view.panel.default_slot.children] == [
                    'synthetic common log']
                view.close()
        finally:
            client.delete()


@pytest.mark.asyncio
@pytest.mark.parametrize('switch', (True, False), ids=('switch', 'same-owner'))
async def test_windows_backend_rechecks_after_loading_toast_before_sending(tmp_path, monkeypatch, switch):
    with resources(tmp_path) as (_, store, ledger, reader, services):
        populate(store, ledger, reader, services)
        owner = ledger.capture_owner(services, account_id=A)
        imported, release = Event(), Event()
        calls = []
        original = builtins.__import__

        def delay(name, *args, **kwargs):
            if name == 'win11toast':
                imported.set()
                assert release.wait(5)
            return original(name, *args, **kwargs)

        monkeypatch.setitem(sys.modules, 'win11toast', NS(toast=lambda *args, **_kwargs: calls.append(args)))
        monkeypatch.setattr(builtins, '__import__', delay)
        dispatcher = NotificationDispatcher(NS(get_setting=lambda *_: '1'), backend=WindowsToastBackend())
        try:
            future = dispatcher.notify('fixture', '保存 1件', authorize=owner.verify_current)
            await until(imported.is_set)
            if switch:
                await confirm_switch(store, services, update_live=False)
            release.set()
            expected = 'suppressed' if switch else 'sent'
            assert (await asyncio.wrap_future(future)).status == expected
            assert len(calls) == (0 if switch else 1)
        finally:
            release.set()
            dispatcher.close()




@pytest.mark.asyncio
async def test_owner_switch_removes_already_rendered_log_without_polling(tmp_path):
    with resources(tmp_path) as (_, store, ledger, reader, services):
        populate(store, ledger, reader, services)
        log = QueueLog()
        log.append('common')
        log.append_owned('scoped', owner=ledger.capture_owner(services, account_id=A))
        client = Client(ui.page('/_r3_open_shared_log'))
        try:
            with client:
                view = SharedLogView(log, following=store)
                view.timer.deactivate()
                view.show_tab('scheduler')

                def messages():
                    return [child.text.split('] ', 1)[1] for child in view.panel.default_slot.children]

                assert messages() == ['common', 'scoped']
                await confirm_switch(store, services, update_live=False)
                assert messages() == ['common']
                store.sync(reader)
                view.show_tab('scheduler')
                assert messages() == ['common']
                view.close()
        finally:
            client.delete()


def test_attached_ledger_requires_follow_owner_even_when_reader_matches(tmp_path):
    with resources(tmp_path) as (_, store, ledger, reader, services):
        store.sync(reader)
        with pytest.raises((AccountMismatch, ScheduleAccountError)):
            ledger.bind_account(A, scope=observed_scope(A))
        assert ledger._bound_account() is None
        ledger.bind_account(A, scope=ledger.capture_owner(services, account_id=A))
        assert ledger._bound_account() == A


@pytest.mark.parametrize(('cls', 'methods', 'required'), (
    (QueueLog, {'append', 'append_owned', 'cookie_sync_notice', 'snapshot'}, {'append_owned': 'owner'}),
    (NotificationDispatcher, {'notify', 'close'}, {}),
    (WindowsToastBackend, {'send', 'send_authorized'}, {'send_authorized': 'authorize'}),
))
def test_scheduler_output_sinks_have_an_explicit_classification(cls, methods, required):
    assert {name for name, _ in getmembers(cls, isroutine) if not name.startswith('_')} == methods
    for method, argument in required.items():
        assert signature(getattr(cls, method)).parameters[argument].default is Parameter.empty


def test_unaware_notification_backend_accepts_common_but_suppresses_owned_messages():
    calls = []

    def send(title, body):
        calls.append((title, body))

    dispatcher = NotificationDispatcher(NS(get_setting=lambda *_: '1'), backend=NS(send=send))
    try:
        assert dispatcher.notify('common', 'fixture').result(timeout=5).status == 'sent'
        assert dispatcher.notify('owned', 'fixture', authorize=lambda: None).result(timeout=5).status == 'suppressed'
        assert calls == [('common', 'fixture')]
    finally:
        dispatcher.close()
