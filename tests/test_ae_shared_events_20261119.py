from threading import Event, Lock, RLock
from types import SimpleNamespace as NS

import pytest
from nicegui import Client, ui

from pixivvault_nicegui.queue_log import QueueLog
from pixivvault_nicegui.services import ApplicationServices


def scheduler_fixture(monkeypatch,tmp_path,authors,dispatch):
    from pixivvault_nicegui import managed_scheduler
    class Ledger:
        def __init__(self,root):pass
        def favourites(self):return authors
        def sync_favourites(self, authors):pass
        def close(self):pass
    class Runner:
        def __init__(self,ledger,dispatch,*,reconcile):self.dispatch=dispatch
        def start(self):pass
    monkeypatch.setattr(managed_scheduler,'ManagedScheduler',Ledger)
    monkeypatch.setattr(managed_scheduler,'ManagedScheduleRunner',Runner)
    monkeypatch.setattr(managed_scheduler,'submit_author_batch',dispatch)
    service=NS(_close_lock=RLock(),_managed_scheduler_lock=Lock(),_scheduler_dispatch_lock=Lock(),
        closing=False,managed_downloads=NS(root=tmp_path),live_client=object(),managed_scheduler=None,
        managed_schedule_runner=None,shared_log=QueueLog(),reconcile_managed_scheduler=lambda:None,
        scheduler_favourite_authors=lambda:authors)
    ApplicationServices.enable_managed_scheduler(service)
    return service


def test_scheduler_empty_start_and_failure_have_fixed_safe_messages(monkeypatch,tmp_path):
    service=scheduler_fixture(monkeypatch,tmp_path,(),lambda *a,**k:{'authors':0,'accepted':0,'deferred':0})
    service.managed_schedule_runner.dispatch('slot',Event())
    assert [e.message for e in service.shared_log.snapshot()] == [
        '☆ お気に入り自動チェック: 対象の作者（☆お気に入り）が登録されていません。']
    service=scheduler_fixture(monkeypatch,tmp_path,('1','2'),lambda *a,**k:(_ for _ in ()).throw(RuntimeError('synthetic-secret')))
    with pytest.raises(RuntimeError):service.managed_schedule_runner.dispatch('slot',Event())
    entries=service.shared_log.snapshot()
    assert entries[0].message=='☆ お気に入り自動チェックを開始します (2人の作者)'
    assert entries[1].message=='☆ お気に入り自動チェック中にエラーが発生しました。'
    assert entries[1].level=='error' and all('synthetic-secret' not in e.message for e in entries)


def test_verified_saves_logged_once_after_durable_reconcile():
    class Ledger:
        def __init__(self):self.results=[('123','456'),()]
        def reconcile_expected_fingerprints(self,_):return {}
        def reconcile_jobs(self,*_):return self.results.pop(0)
        def has_deferred(self):return False
    service=NS(closing=False,managed_scheduler=Ledger(),jobs=NS(snapshot=lambda:()),
        managed_downloads=object(),_managed_job_records={},notifications=None,
        _scheduler_unfinished_count=None,managed_schedule_runner=None,shared_log=QueueLog())
    ApplicationServices.reconcile_managed_scheduler(service)
    ApplicationServices.reconcile_managed_scheduler(service)
    assert [e.message for e in service.shared_log.snapshot()] == [
        '☆ お気に入り自動チェック: 2件の作品の保存を確認しました。']


def test_pixiv_link_click_logs_once_without_network():
    from pixivvault_nicegui.ui.shell import DemoShell
    service=ApplicationServices(2);client=Client(ui.page('/_ae_shared_events'))
    try:
        with client:
            shell=DemoShell(service)
            try:
                handlers=[x.handler for x in shell.pixiv_home._event_listeners.values() if x.type=='click']
                assert len(handlers)==1
                handlers[0](NS(args={}))
                rows=service.shared_log.snapshot()
                assert len(rows)==1 and 'ブラウザで Pixiv を開きました' in rows[0].message
            finally:shell.close()
    finally:client.delete();service.close()


def test_extension_failure_uses_original_failure_grammar_without_exception_text():
    from pixivvault_nicegui.extension_application import ExtensionApplication
    owner=ExtensionApplication.__new__(ExtensionApplication);owner._lock=RLock()
    owner.services=NS(queue_log=QueueLog(),shared_log=QueueLog())
    owner._terminal_log(NS(state='error',target_id='{"type":"work","work_id":"123"}'))
    assert owner.services.shared_log.snapshot()[0].message == '--- [拡張機能連携] ダウンロードに失敗しました: work (id=123) ---'


def test_cancelled_scheduler_is_not_reported_as_failure(monkeypatch,tmp_path):
    cancelled=Event()
    def stop(*args,**kwargs):
        cancelled.set()
        raise RuntimeError('synthetic cancellation')
    service=scheduler_fixture(monkeypatch,tmp_path,('1',),stop)
    with pytest.raises(RuntimeError):service.managed_schedule_runner.dispatch('slot',cancelled)
    entries=service.shared_log.snapshot()
    assert len(entries)==1 and '開始します' in entries[0].message
    assert all(e.level!='error' for e in entries)
