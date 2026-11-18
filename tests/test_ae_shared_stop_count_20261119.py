from datetime import datetime, timezone
from threading import Event, Lock, RLock
from types import SimpleNamespace as NS

import pytest

from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopped
from pixivvault_nicegui.managed_scheduler import ManagedScheduleRunner
from pixivvault_nicegui.queue_log import QueueLog
from pixivvault_nicegui.services import ApplicationServices


def build(monkeypatch,tmp_path):
    owner=NS(_close_lock=RLock(),_managed_scheduler_lock=Lock(),_scheduler_dispatch_lock=Lock(),
        closing=False,managed_downloads=NS(root=tmp_path),live_client=object(),
        jobs=NS(snapshot=lambda:(),operation_ids=lambda _:()),managed_scheduler=None,
        managed_schedule_runner=None,shared_log=QueueLog(),_managed_job_records={},
        _scheduler_unfinished_count=None,notifications=None,reconcile_managed_scheduler=lambda:None)
    monkeypatch.setattr(ManagedScheduleRunner,'start',lambda self:True)
    ledger=ApplicationServices.enable_managed_scheduler(owner)
    return owner,ledger


def test_prefenced_scheduler_reports_stop_not_error_and_propagates(monkeypatch,tmp_path):
    owner,ledger=build(monkeypatch,tmp_path)
    try:
        ledger.set_favourites(('1',),expected_revision=ledger.snapshot().revision)
        def stopped():raise ManagedNetworkStopped(429)
        owner.managed_network_stop=NS(require_allowed=stopped)
        with pytest.raises(ManagedNetworkStopped):
            owner.managed_schedule_runner.dispatch('stopped-slot',Event())
        entries=owner.shared_log.snapshot()
        assert len(entries)==2 and entries[0].level=='normal'
        assert entries[1].level=='normal' and '取得停止' in entries[1].message
        assert all('エラー' not in e.message for e in entries)
    finally:ledger.close()


def test_two_slots_same_verified_work_count_as_one(monkeypatch,tmp_path):
    owner,ledger=build(monkeypatch,tmp_path)
    notifications=[]
    owner.notifications=NS(notify=lambda title,message:notifications.append((title,message)))
    try:
        fingerprint='a'*64
        for slot,operation in (('slot-one','op-one'),('slot-two','op-two')):
            ledger.begin_dispatch(slot,datetime.now(timezone.utc),1)
            ledger.prepare_dispatch_item(slot=slot,work_id='123',author_id='1',operation_id=operation)
            ledger.bind_dispatch_job(slot=slot,work_id='123',operation_id=operation,
                                     job_id='fixture-job',accepted_fingerprint=fingerprint)
            ledger.finish_dispatch(slot,status='queued',accepted=1,deferred=0)
        receipt=(None,{'work_id':'123'},None,None,fingerprint)
        owner.managed_downloads.verified_saved_receipt=lambda *a,**kw:receipt
        assert ApplicationServices.reconcile_managed_scheduler(owner)
        assert ApplicationServices.reconcile_managed_scheduler(owner)
        messages=[e.message for e in owner.shared_log.snapshot()]
        assert messages==['☆ お気に入り自動チェック: 1件の作品の保存を確認しました。']
        assert notifications==[('PixivVault','作者更新チェック: 1件の保存が完了しました。')]
    finally:ledger.close()
