"""Q07/Q17: generated immutable failures -> real novel queue, no network."""
import asyncio
from hashlib import sha256
import sqlite3
from threading import Event
from types import SimpleNamespace
import pytest
from nicegui import Client, ui
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.managed_novel_jobs import PinnedNovelSource, ManagedNovelError
from pixivvault_nicegui.ui.failed_records_view import FailedRecordsView
from pixivvault_nicegui import novel_storage

class Reader:
    def __init__(self):
        self.calls=[]
        self.entered=Event()
        self.release=Event()
        self.release.set()
        self.fail=False
        self.wrong=False
    def require_identity(self): self._verified_identity = '12'; return self._verified_identity
    def verify_identity(self): self._verified_identity = '12'; return self._verified_identity
    def get_novel_payload(self,wid,aid):
        self.calls.append(wid)
        self.entered.set()
        assert self.release.wait(10)
        if self.fail: raise OSError('synthetic-private-token-path')
        return dict(id=wid,userId='99' if self.wrong else aid,userName='Synthetic',
                    title='Synthetic novel '+wid,content='Synthetic text '+wid)
    def close(self): pass

def create(tmp_path,count=1,novels=True):
    frozen=tmp_path/'frozen'
    frozen.mkdir()
    db=frozen/'failed.sqlite3'
    with sqlite3.connect(db) as con:
        con.execute('CREATE TABLE failed_queue(work_id TEXT,user_id TEXT,title TEXT,work_type TEXT,error_reason TEXT,failed_at TEXT,retry_count INTEGER)')
        con.executemany('INSERT INTO failed_queue VALUES(?,?,?,?,?,?,?)',[
            (str(1001+i),'23','selected' if i<count-1 or count==1 else 'excluded','novel','Timeout','2026-11-04',1)
            for i in range(count)])
    con.close()
    reader=Reader()
    options=dict(live_client=reader,pixiv_download_root=tmp_path/'images',failed_queue_readonly_db=db)
    if novels:
        options.update(novel_fixture_root=tmp_path/'novels',novel_fixture_source=PinnedNovelSource(reader,{}))
    service=ApplicationServices(**options)
    return service,reader,db

async def wait(service,jobid):
    for _ in range(1000):
        job=next(j for j in service.jobs.snapshot() if j.job_id==jobid)
        if job.state in ('done','error','stopped'): return job
        await asyncio.sleep(.01)
    raise AssertionError('novel worker did not finish')

def label_text(view):
    return [element.text for element in view.rows.descendants()
            if element.props.get('data-testid')=='failed-row-retry-state']

@pytest.mark.asyncio
async def test_single_novel_retry_running_done_and_restart(tmp_path):
    service,reader,db=create(tmp_path)
    original=sha256(db.read_bytes()).hexdigest()
    reader.release.clear()
    client=Client(ui.page('/_q07_novel'))
    try:
        with client:
            view=FailedRecordsView(service)
            await view._refresh(view.state.begin_refresh())
            row=view.state.records[0]
            assert view._retry_supported(row), 'Q07 novel retry is disabled'
            assert reader.calls==[] and service.jobs.snapshot()==()
            assert await view._retry_records((row,))
            assert await asyncio.to_thread(reader.entered.wait,5)
            view._refresh_retry_states()
            assert '再試行: 実行中' in label_text(view)
            reader.release.set()
            job=await wait(service,service.jobs.snapshot()[0].job_id)
            assert job.state=='done'
            view._refresh_retry_states()
            assert '再試行: 完了' in label_text(view)
            assert service.novel_jobs.current('1001','EPUB').relative_receipt==job.result
            before=len(reader.calls)
            assert await view._retry_records((row,))
            assert len(service.jobs.snapshot())==1 and len(reader.calls)==before
            view.close()
        assert sha256(db.read_bytes()).hexdigest()==original
    finally:
        reader.release.set()
        client.delete()
        service.close()
    reopened=ApplicationServices(live_client=reader,pixiv_download_root=tmp_path/'images',
        failed_queue_readonly_db=db,novel_fixture_root=tmp_path/'novels',
        novel_fixture_source=PinnedNovelSource(reader,{}))
    try:
        assert reopened.novel_jobs.current('1001','EPUB') is not None
        assert len(reader.calls)==before
    finally: reopened.close()

@pytest.mark.asyncio
async def test_bulk_filtered_novels_all_pages_and_no_implicit_retry(tmp_path,monkeypatch):
    service,reader,db=create(tmp_path,11)
    before=db.read_bytes()
    client=Client(ui.page('/_q17_novel'))
    captured=[]
    from pixivvault_nicegui.ui import failed_records_view as module
    monkeypatch.setattr(module.background_tasks,'create',lambda coro:captured.append(coro))
    try:
        with client:
            view=FailedRecordsView(service)
            await view._refresh(view.state.begin_refresh())
            view.search('selected')
            view.change_page(1)
            assert service.jobs.snapshot()==() and reader.calls==[]
            assert view.retry_all.enabled, 'Q17 novel bulk retry is disabled'
            view.request_retry()
            assert len(captured)==1
            assert await captured.pop()
            jobs=service.jobs.snapshot()
            assert len(jobs)==10
            for job in jobs: assert (await wait(service,job.job_id)).state=='done'
            assert set(reader.calls)=={str(i) for i in range(1001,1011)}
            assert len(view.state.records)==11 and db.read_bytes()==before
            view.close()
    finally:
        for coro in captured:coro.close()
        client.delete()
        service.close()

@pytest.mark.asyncio
async def test_failed_novel_result_and_explicit_retry(tmp_path):
    service,reader,db=create(tmp_path)
    reader.fail=True
    client=Client(ui.page('/_q07_retry_failure'))
    try:
        with client:
            view=FailedRecordsView(service)
            await view._refresh(view.state.begin_refresh())
            row=view.state.records[0]
            assert await view._retry_records((row,))
            assert (await wait(service,service.jobs.snapshot()[0].job_id)).state=='error'
            view._refresh_retry_states()
            assert '再試行: 失敗（管理キューで詳細を確認）' in label_text(view)
            reader.fail=False
            assert await view._retry_records((row,))
            assert len(service.jobs.snapshot())==2
            for job in service.jobs.snapshot():
                if job.state!='error': assert (await wait(service,job.job_id)).state=='done'
            assert 'synthetic-private' not in ' '.join(label_text(view))
            view.close()
    finally: client.delete();service.close()

@pytest.mark.asyncio
async def test_unavailable_owner_and_changed_snapshot_fail_closed(tmp_path):
    service,reader,db=create(tmp_path,novels=False)
    client=Client(ui.page('/_q07_no_novel_owner'))
    try:
        with client:
            view=FailedRecordsView(service)
            await view._refresh(view.state.begin_refresh())
            assert not view._retry_supported(view.state.records[0])
            view.close()
    finally:client.delete();service.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('retired', [False, True])
async def test_pending_publication_never_new_job_or_fetch(tmp_path,monkeypatch,retired):
    service,reader,db=create(tmp_path)
    outputs=service.novel_jobs.store.root/'outputs'
    original_sync=novel_storage._fsync_dir
    original_read=novel_storage._read_bounded
    flag={'published':False}
    def sync(path):
        if path==outputs:
            flag['published']=True
            raise OSError('synthetic only')
        return original_sync(path)
    def read(path,maximum):
        if flag['published'] and path.parent.parent==outputs: raise PermissionError('synthetic only')
        return original_read(path,maximum)
    monkeypatch.setattr(novel_storage,'_fsync_dir',sync)
    monkeypatch.setattr(novel_storage,'_read_bounded',read)
    client=Client(ui.page('/_q07_pending'))
    try:
        with client:
            view=FailedRecordsView(service)
            await view._refresh(view.state.begin_refresh())
            row=view.state.records[0]
            assert await view._retry_records((row,))
            job=await wait(service,service.jobs.snapshot()[0].job_id)
            assert job.error_code=='novel_publication_unconfirmed'
            proof=service.novel_jobs._journal['jobs'][job.job_id]['pending'].copy()
            if retired:
                from pixivvault_nicegui.managed_novel_jobs import NovelJobIntent
                service.novel_jobs.jobs._max_jobs=1
                other=service.novel_jobs.enqueue(NovelJobIntent('2001','23','EPUB'),'other-work')
                await wait(service,other.job_id)
                assert job.job_id not in {j.job_id for j in service.jobs.snapshot()}
            calls=list(reader.calls)
            count=service.novel_jobs.jobs.operation_count
            assert await view._retry_records((row,))
            assert len(service.jobs.snapshot())==1 and reader.calls==calls
            assert service.novel_jobs.jobs.operation_count==count
            assert service.novel_jobs._journal['jobs'][job.job_id]['pending']==proof
            view.close()
    finally:client.delete();service.close()

def test_bulk_novel_fetch_does_not_exhaust_selection_pins():
    reader=Reader()
    source=PinnedNovelSource(reader,{'900':'23'})
    for wid in range(1001,1262):
        assert source.fetch_selected(str(wid),'23')['id']==str(wid)
    assert source.authors=={'900':'23'}
    with pytest.raises(ManagedNovelError):source.fetch_selected('900','24')
    assert len(reader.calls)==261

@pytest.mark.asyncio
async def test_novel_identity_and_changed_legacy_row_are_rejected(tmp_path):
    service,reader,db=create(tmp_path)
    reader.wrong=True
    try:
        row=(await service.read_failed_records())[0]
        intent=await service.lookup_legacy_failed_original(row,owner='a'*32)
        assert reader.calls==[]
        job=service.enqueue_legacy_failed_original(row,intent,'wrong-author',owner='a'*32)
        assert (await wait(service,job.job_id)).state=='error'
        assert service.novel_jobs.saved_entries()==()
        with sqlite3.connect(db) as con:
            con.execute("UPDATE failed_queue SET retry_count=9")
        con.close()
        calls=list(reader.calls)
        with pytest.raises(ValueError):
            await service.lookup_legacy_failed_original(row,owner='a'*32)
        assert reader.calls==calls and len(service.jobs.snapshot())==1
    finally:service.close()

@pytest.mark.asyncio
async def test_bulk_reports_each_completed_and_failed_job(tmp_path):
    service,reader,db=create(tmp_path,3)
    original=reader.get_novel_payload
    def payload(wid,aid):
        if wid=='1002':raise OSError('synthetic-private')
        return original(wid,aid)
    reader.get_novel_payload=payload
    client=Client(ui.page('/_q17_mixed_result'))
    try:
        with client:
            view=FailedRecordsView(service)
            await view._refresh(view.state.begin_refresh())
            assert await view._retry_records(view.state.records)
            for job in service.jobs.snapshot():await wait(service,job.job_id)
            view._refresh_retry_states()
            assert label_text(view).count('再試行: 完了')==2
            assert label_text(view).count('再試行: 失敗（管理キューで詳細を確認）')==1
            assert len(view.state.records)==3
            view.close()
            assert view._retry_timer._is_canceled
    finally:client.delete();service.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('status',[401,403,429])
async def test_novel_legacy_retry_keeps_shared_stop_before_any_fetch(tmp_path,status):
    from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopGate
    service,reader,db=create(tmp_path)
    gate=ManagedNetworkStopGate(tmp_path/'synthetic-stop'/'gate.sqlite3')
    gate.trip(status)
    before=gate.snapshot()
    service.novel_jobs.jobs._dispatch_admission=lambda job:not gate.snapshot().blocked
    client=Client(ui.page('/_q07_stopped'))
    try:
        with client:
            view=FailedRecordsView(service)
            await view._refresh(view.state.begin_refresh())
            assert await view._retry_records(view.state.records)
            for _ in range(500):
                jobs=service.jobs.snapshot()
                if jobs and jobs[0].state=='paused':break
                await asyncio.sleep(.01)
            assert jobs[0].state=='paused' and jobs[0].error_code=='admission_blocked'
            assert reader.calls==[] and gate.snapshot()==before
            view._refresh_retry_states()
            assert '再試行: 一時停止' in label_text(view)
            view.close()
    finally:client.delete();service.close()



