import asyncio
from types import SimpleNamespace as NS

import pytest
from nicegui import Client, core, ui
from pixivvault_nicegui.queue_log import QueueLog
from pixivvault_nicegui.services import ApplicationServices


def test_owned_cache_clear_logs_confirmed_and_fixed_failure():
    log=QueueLog(); calls=[]
    cache=NS(clear=lambda:calls.append('cleared'))
    owner=NS(shared_image_cache=cache,shared_log=log)
    ApplicationServices.clear_shared_image_cache(owner)
    assert calls==['cleared'] and log.snapshot()[-1].message=='画像キャッシュを削除しました。'
    def fail():raise OSError('synthetic-private-path')
    cache.clear=fail
    with pytest.raises(OSError):ApplicationServices.clear_shared_image_cache(owner)
    assert log.snapshot()[-1].message=='画像キャッシュを削除できませんでした。'
    assert all('synthetic-private-path' not in e.message for e in log.snapshot())


@pytest.mark.asyncio
async def test_real_learning_reset_and_history_clear_log_once(tmp_path,monkeypatch):
    from pixivvault_nicegui.learning import Judgment
    from pixivvault_nicegui.ui.learning_view import LearningView
    monkeypatch.setattr(core,'loop',asyncio.get_running_loop())
    services=ApplicationServices(2,learning_sandbox=tmp_path/'learning')
    client=Client(ui.page('/_ae_shared_learning_actions'))
    try:
        with client:
            view=LearningView(services);view.active=True
            session=await services.learning.open_session()
            await services.learning.record(session.session_id,Judgment('demo-0001','like','op'))
            await view.reset_view.refresh();await view.reset_view.open()
            view.reset_view.confirm.set_value('1');await view.reset_view.execute()
            assert await services.learning.judged_count()==0
            await services.learning.record_view('demo-0001')
            await view.view_history.refresh();view.view_history.confirm.set_value('1')
            await view.view_history.clear()
            assert await services.learning.view_history()==[]
            messages=[e.message for e in services.shared_log.snapshot()]
            assert messages.count('学習状況: リセットしました')==1
            assert messages.count('閲覧履歴: 全件削除しました')==1
            view.close()
    finally:client.delete();services.close()


@pytest.mark.asyncio
async def test_queue_control_failure_logs_fixed_text_once():
    from pixivvault_nicegui.ui.job_views import QueueView
    def fail(*a,**k):raise OSError('synthetic-private-path')
    log=QueueLog();jobs=NS(snapshot=lambda:(),faulted=False,control=fail)
    client=Client(ui.page('/_ae_shared_queue_action'))
    try:
        with client:
            view=QueueView(NS(jobs=jobs,artworks=(),shared_log=log))
            assert await view.command('job','stop') is False
            assert log.snapshot()[-1].message=='キュー操作に失敗しました。'
            assert 'synthetic-private-path' not in log.snapshot()[-1].message
            view.close()
    finally:client.delete()


@pytest.mark.asyncio
async def test_learning_reset_log_survives_client_wait_cancellation(tmp_path):
    from threading import Event
    started=Event();release=Event();services=ApplicationServices(2,learning_sandbox=tmp_path/'learning')
    original=services.learning._reset
    def blocked():
        started.set();assert release.wait(5);return original()
    services.learning._reset=blocked
    task=asyncio.create_task(services.learning.reset_learning())
    try:
        assert await asyncio.to_thread(started.wait,3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        release.set()
        for _ in range(100):
            if services.shared_log.snapshot():break
            await asyncio.sleep(.02)
        assert [e.message for e in services.shared_log.snapshot()] == ['学習状況: リセットしました']
    finally:
        release.set();services.close()


@pytest.mark.asyncio
async def test_duplicate_actual_committed_count_and_failure_are_logged_once():
    from pixivvault_nicegui.ui.duplicate_view import DuplicateView
    log=QueueLog()
    source=NS(execute=lambda *a,**k:NS(committed=('one','two'),failed=('x',)))
    client=Client(ui.page('/_ae_shared_duplicate_action'))
    try:
        with client:
            view=DuplicateView(source,event_services=NS(shared_log=log))
            result,warning=await view._execute_accepted(object(),'operation')
            assert len(result.committed)==2 and not warning
            assert [e.message for e in log.snapshot()]==['重複整理: 2件を隔離しました。失敗1件。']
            view.close()
    finally:client.delete()


@pytest.mark.asyncio
async def test_failed_retry_logs_accepted_not_completed(tmp_path):
    from test_q07_novel_retry import create
    from pixivvault_nicegui.ui.failed_records_view import FailedRecordsView
    service,reader,_=create(tmp_path)
    client=Client(ui.page('/_ae_shared_failed_retry'))
    try:
        with client:
            view=FailedRecordsView(service)
            await view._refresh(view.state.begin_refresh())
            assert await view._retry_records((view.state.records[0],))
            rows=service.shared_log.snapshot()
            assert len(rows)==1
            assert rows[0].message=='失敗リストの再投入: 受理1件・失敗0件。'
            assert '完了' not in rows[0].message
            view.close()
    finally:
        reader.release.set();client.delete();service.close()


@pytest.mark.asyncio
async def test_recent_backup_refresh_logs_confirmed_empty_without_paths():
    from pixivvault_nicegui.ui.viewer_backup_view import ViewerBackupView
    async def read():return (),()
    log=QueueLog();service=NS(shared_log=log,legacy_backup_history_available=False,recent_viewer_backups=read)
    client=Client(ui.page('/_ae_shared_recent_backup'))
    try:
        with client:
            view=ViewerBackupView(service);view.active=True
            await view.refresh()
            assert [e.message for e in log.snapshot()]==['直近のバックアップ: 0件の履歴を表示しました。']
            view.close()
    finally:client.delete()


@pytest.mark.asyncio
async def test_duplicate_scan_reports_fixed_counts_without_paths():
    from pixivvault_nicegui.ui.duplicate_view import DuplicateView
    log=QueueLog()
    source=NS(scan=lambda:NS(groups=(),scanned_files=2,unreadable=('synthetic-private-path',),skipped_authors=()))
    client=Client(ui.page('/_ae_shared_duplicate_scan'))
    try:
        with client:
            view=DuplicateView(source,event_services=NS(shared_log=log));view.active=True
            await view.scan()
            assert [e.message for e in log.snapshot()]==['重複整理スキャン: 読取失敗1件・上限超過0作者。']
            assert 'synthetic-private-path' not in log.snapshot()[0].message
            view.close()
    finally:client.delete()


@pytest.mark.asyncio
async def test_cancelled_retry_inflight_does_not_claim_zero_accepted(tmp_path):
    from threading import Event
    from test_q07_novel_retry import create
    from pixivvault_nicegui.ui.failed_records_view import FailedRecordsView
    service,reader,_=create(tmp_path)
    entered=Event();release=Event();original=service.enqueue_legacy_failed_original
    def delayed(*args,**kwargs):
        entered.set();assert release.wait(5)
        return original(*args,**kwargs)
    service.enqueue_legacy_failed_original=delayed
    client=Client(ui.page('/_ae_shared_retry_cancel_pending'))
    task=None
    try:
        with client:
            view=FailedRecordsView(service)
            await view._refresh(view.state.begin_refresh())
            task=asyncio.create_task(view._retry_records((view.state.records[0],)))
            assert await asyncio.to_thread(entered.wait,3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
            messages=[e.message for e in service.shared_log.snapshot()]
            assert messages==['失敗リストの再投入を中断しました（受理済み0件・受付結果未確認1件）。']
            release.set()
            await asyncio.gather(*tuple(view._pending_accepts),return_exceptions=True)
            assert len(service.shared_log.snapshot())==1
            view.close()
    finally:
        release.set();reader.release.set();client.delete();service.close()
