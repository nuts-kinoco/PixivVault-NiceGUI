"""Completed publication, user-editable copies and explicit retained Retry."""
import json,sqlite3
from pathlib import Path
from threading import Event,Thread
import pytest
from test_save_exports import fixture,settled
from pixivvault_nicegui.save_exports import SaveExports
from pixivvault_nicegui.fs_paths import native_io_path


def req(ex,wid='301',account='12'):
    rel,row,sha=next(v for v in ex.s.managed_downloads.saved_receipts() if v[1]['work_id']==wid)
    return dict(relative=rel,sha256=sha,work_id=wid,account=account,mode='link',use_work_folder=False)


def bind_author(ex):
    rel,row,sha=next(v for v in ex.s.managed_downloads.saved_receipts() if v[1]['work_id']=='301')
    proof=dict(receipt=rel,sha256=sha)
    ex.s.author_media.completed_projection_sources=lambda:(dict(kind='illustration',work_id='301',author_id='23',format='original',proof=proof),)
    ex.s.author_media._proof=lambda *a:proof


def wait_state(ex,jid,state):
    with ex.jobs._condition:
        assert ex.jobs._condition.wait_for(lambda:any(j.job_id==jid and j.state==state for j in ex.jobs.snapshot()),12)
    return next(j for j in ex.jobs.snapshot() if j.job_id==jid)


@pytest.mark.parametrize('edit',['edit','delete'])
def test_user_edit_does_not_undo_completion_or_block_later_bookmark(tmp_path,monkeypatch,edit):
    _,managed,ex=fixture(tmp_path,auto='0',mode='link');bind_author(ex)
    try:
        ex.scan_authors();jobs=settled(ex);author=next(j for j in jobs if j.kind=='author-projection')
        manifest=json.loads(native_io_path(ex.root/author.result).read_bytes())
        target=native_io_path(ex.projection.root)/manifest['files'][0]['path']
        if edit=='edit':target.write_bytes(b'user edit must survive')
        else:target.unlink()  # isolated test-owned projection only
        before=managed.saved_receipts()
        def forbidden(*a):pytest.fail('dependency rehashed editable projection bytes')
        monkeypatch.setattr(ex,'_owned_bytes',forbidden)
        for _ in range(3):assert ex.scan_authors()==set()
        service,root=ex.s,ex.root;ex.close();ex=SaveExports(service,root,'12')
        monkeypatch.setattr(ex,'_owned_bytes',forbidden)
        assert ex.scan_authors()==set()
        # Another work of the same author is an independent new bookmark.
        job=ex.enqueue('bookmark-projection',req(ex,'302'))
        assert wait_state(ex,job.job_id,'done').state=='done'
        assert managed.saved_receipts()==before
        assert target.read_bytes()==b'user edit must survive' if edit=='edit' else not target.exists()
    finally:ex.close();managed.close()


@pytest.mark.parametrize('terminal',['error','stopped'])
def test_retired_author_error_or_stop_is_explicitly_retried_with_same_attempt_history(tmp_path,monkeypatch,terminal):
    _,managed,ex=fixture(tmp_path,auto='0',mode='link');bind_author(ex)
    entered,release=Event(),Event();real=ex._run;blocked=[True]
    def run(job,context):
        if job.kind=='author-projection' and blocked[0]:
            entered.set();assert release.wait(8);context.checkpoint()
            raise OSError('synthetic author output failure')
        return real(job,context)
    monkeypatch.setattr(ex,'_run',run)
    try:
        ex.scan_authors();first=ex.jobs.snapshot()[0];assert entered.wait(5)
        if terminal=='stopped':assert ex.jobs.control(first.job_id,'stop')
        release.set();wait_state(ex,first.job_id,terminal)
        blocked[0]=False
        for index in range(51):
            job=ex.enqueue('bookmark-projection',req(ex,'303',str(1000+index)))
            wait_state(ex,job.job_id,'done')
        assert len(ex.jobs.snapshot())==50 and all(j.job_id!=first.job_id for j in ex.jobs.snapshot())
        assert ex.jobs.retained_job(first.job_id).state==terminal
        service,root=ex.s,ex.root;ex.close();ex=SaveExports(service,root,'12')
        assert ex.jobs.retained_job(first.job_id).state==terminal
        assert any(e.job_id==first.job_id for e in ex.retained_requests())  # before any rescan
        count=ex.jobs.operation_count
        ex.scan_authors();assert any(e.job_id==first.job_id for e in ex.retained_requests())
        assert ex.jobs.operation_count==count  # scan never executes the retry
        assert ex.retry_retained(first.job_id)
        restored=wait_state(ex,first.job_id,'done')
        assert restored.attempt==2 and restored.target_id==first.target_id
        assert ex.jobs.operation_count==count
        job=ex.enqueue('bookmark-projection',req(ex));wait_state(ex,job.job_id,'done')
        service,root=ex.s,ex.root;ex.close();ex=SaveExports(service,root,'12')
        assert ex.jobs.retained_job(first.job_id).state=='done'
        assert ex.scan_authors()==set()
    finally:release.set();ex.close();managed.close()


def test_v1_retirement_without_attempt_metadata_uses_explicit_linked_recovery(tmp_path,monkeypatch):
    _,managed,ex=fixture(tmp_path,auto='0',mode='link');bind_author(ex)
    real=ex._run
    def fail(job,context):
        if job.kind=='author-projection':raise OSError('synthetic')
        return real(job,context)
    monkeypatch.setattr(ex,'_run',fail)
    try:
        ex.scan_authors();first=ex.jobs.snapshot()[0];wait_state(ex,first.job_id,'error')
        for index in range(51):
            j=ex.enqueue('bookmark-projection',req(ex,'303',str(2000+index)));wait_state(ex,j.job_id,'done')
        service,root=ex.s,ex.root;ex.close()
        # Recreate an authentic v1-owned fixture: old builds had no archive.
        with sqlite3.connect(native_io_path(root/'jobs.sqlite3')) as db:
            db.execute('DROP TABLE job_history');db.execute('PRAGMA user_version=1')
        ex=SaveExports(service,root,'12')
        assert ex.jobs.retained_job(first.job_id) is None
        ex.scan_authors();assert ex.retained_requests()
        count=ex.jobs.operation_count
        assert ex.retry_retained(first.job_id)
        recovered=next(j for j in ex.jobs.snapshot() if j.kind=='author-projection')
        assert recovered.job_id!=first.job_id and recovered.target_id==first.target_id
        wait_state(ex,recovered.job_id,'done')
        assert ex.jobs.operation_count==count+1
        ex.close();ex=SaveExports(service,root,'12')
        assert ex.scan_authors()==set() and ex.jobs.operation_count==count+1
    finally:ex.close();managed.close()


def test_tuple_and_persisted_list_proofs_are_one_logical_auto_zip_request(tmp_path):
    _,managed,ex=fixture(tmp_path,auto='1')
    try:
        ex.scan();jobs=settled(ex);assert all(j.state=='done' for j in jobs)
        before=[(j.job_id,j.target_id) for j in jobs];count=ex.jobs.operation_count
        ex.scan();assert [(j.job_id,j.target_id) for j in ex.jobs.snapshot()]==before
        assert ex.jobs.operation_count==count and not ex.scan_errors
        service,root=ex.s,ex.root;ex.close();ex=SaveExports(service,root,'12')
        ex.scan();assert [(j.job_id,j.target_id) for j in ex.jobs.snapshot()]==before
        assert ex.jobs.operation_count==count and not ex.scan_errors
    finally:ex.close();managed.close()


def test_unreadable_legacy_intent_localizes_via_bound_manifest(tmp_path):
    from test_durable_export_acceptance import legacy_accept
    _,managed,ex=fixture(tmp_path,auto='0',mode='link')
    try:
        value=req(ex);first=legacy_accept(ex,value);wait_state(ex,first.job_id,'done')
        path=native_io_path(ex.root/(first.target_id+'.json'));path.write_bytes(b'{unreadable')
        service,root=ex.s,ex.root;ex.close();ex=SaveExports(service,root,'12')
        unrelated=ex.enqueue('bookmark-projection',req(ex,'303','99'));wait_state(ex,unrelated.job_id,'done')
        count=ex.jobs.operation_count
        with pytest.raises(ValueError):ex.enqueue('bookmark-projection',value)
        assert ex.jobs.operation_count==count and path.read_bytes()==b'{unreadable'
    finally:ex.close();managed.close()


def test_author_dependency_runs_outside_bookmark_metadata_lock(tmp_path,monkeypatch):
    _,managed,ex=fixture(tmp_path,auto='0',mode='link')
    entered,release,remembered=Event(),Event(),Event();errors=[]
    def source():entered.set();assert release.wait(8);return ()
    ex.s.author_media.completed_projection_sources=source
    def accept():
        try:ex.enqueue('bookmark-projection',req(ex))
        except Exception as e:errors.append(e)
    thread=Thread(target=accept)
    def remember():
        try:ex.remember_bookmark('novel','123','23');remembered.set()
        except Exception as e:errors.append(e)
    second=Thread(target=remember)
    try:
        thread.start();assert entered.wait(5)
        second.start();assert remembered.wait(3)
    finally:
        release.set();thread.join(10)
        if second.ident is not None:second.join(10)
        ex.close();managed.close()
    assert not thread.is_alive() and not second.is_alive() and errors==[]


def test_v1_history_migration_rolls_back_as_one_transaction(tmp_path,monkeypatch):
    from pixivvault_nicegui import job_service as subject
    path=tmp_path/'owned.sqlite3'
    service=subject.JobService(lambda j,c:None,path);service.close()
    with sqlite3.connect(path) as db:
        db.execute('DROP TABLE job_history');db.execute('PRAGMA user_version=1')
    real=sqlite3.connect;fired=[]
    class Connection:
        def __init__(self,inner):self.inner=inner
        def __getattr__(self,key):return getattr(self.inner,key)
        def __enter__(self):self.inner.__enter__();return self
        def __exit__(self,*args):return self.inner.__exit__(*args)
        def execute(self,sql,*args):
            if sql.upper()=='PRAGMA USER_VERSION=2':
                fired.append(sql);raise OSError('synthetic interrupted migration')
            return self.inner.execute(sql,*args)
    def connect(*args,**kwargs):
        value=real(*args,**kwargs)
        return value if kwargs.get('uri') else Connection(value)
    with monkeypatch.context() as patcher:
        patcher.setattr(subject.sqlite3,'connect',connect)
        with pytest.raises((OSError,subject.JobStorageError)):subject.JobService(lambda j,c:None,path)
    assert len(fired)==1
    with real(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==1
        assert {v[0] for v in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}=={'jobs','operations'}
    service=subject.JobService(lambda j,c:None,path)
    try:
        with real(path) as db:assert db.execute('PRAGMA user_version').fetchone()[0]==2
    finally:service.close()


@pytest.mark.asyncio
async def test_retained_retry_event_is_explicit_and_rejects_stale_epoch():
    from types import SimpleNamespace
    from pixivvault_nicegui.ui.job_views import QueueView
    called=[];messages=[]
    view=QueueView.__new__(QueueView)
    view.active=True;view.closed=False;view.epoch=4;view.busy=set()
    view.notice=SimpleNamespace(set_text=messages.append)
    view.services=SimpleNamespace(save_exports=SimpleNamespace(retry_retained=lambda jid:called.append(jid) or True))
    view.refresh=lambda:None
    await view._retained_event(SimpleNamespace(args={'epoch':'3'}),'a'*32)
    assert called==[]
    await view._retained_event(SimpleNamespace(args={'epoch':'4'}),'a'*32)
    assert called==['a'*32] and view.busy==set() and '再試行を受け付けました' in messages


def test_matching_reparse_folder_is_rejected_while_regular_file_is_ignored(tmp_path,monkeypatch):
    from pixivvault_nicegui.bookmark_projection import author_folder
    (tmp_path/'ordinary(23)').write_bytes(b'preserve regular file')
    assert author_folder(tmp_path,'23') is None
    target=tmp_path/'unsafe(23)';target.mkdir();original=Path.lstat;fired=[]
    class Changed:
        def __init__(self,value):self.value=value
        def __getattr__(self,key):return 0x400 if key=='st_file_attributes' else getattr(self.value,key)
    def lstat(path,*args,**kwargs):
        value=original(path,*args,**kwargs)
        from pixivvault_nicegui.fs_paths import logical_path
        if logical_path(path)==target:fired.append(path);return Changed(value)
        return value
    monkeypatch.setattr(Path,'lstat',lstat)
    with pytest.raises(ValueError):author_folder(tmp_path,'23')
    assert fired and (tmp_path/'ordinary(23)').read_bytes()==b'preserve regular file'


@pytest.mark.asyncio
async def test_real_failed_view_renders_retained_retry_and_dispatches_its_handler(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from nicegui import Client,ui
    from pixivvault_nicegui.ui.job_views import QueueView
    _,managed,ex=fixture(tmp_path,auto='0',mode='link');bind_author(ex)
    real=ex._run;client=None
    def fail(job,context):raise OSError('synthetic author failure')
    monkeypatch.setattr(ex,'_run',fail)
    try:
        ex.scan_authors();first=ex.jobs.snapshot()[0];wait_state(ex,first.job_id,'error')
        assert ex.jobs.control(first.job_id,'delete')
        ex.scan_authors();assert [e.job_id for e in ex.retained_requests()]==[first.job_id]
        monkeypatch.setattr(ex,'_run',real)
        client=Client(ui.page('/_retained_export_unit'))
        with client:
            # QueueView resolves author search labels from the browsing catalog.
            # These export jobs have digest targets and need no catalog entries.
            view=QueueView(SimpleNamespace(artworks=(),jobs=ex.jobs,save_exports=ex),failed_only=True)
            assert view.retained_list.visible
            button=next(e for e in client.elements.values() if e.props.get('data-testid')=='retained-export-retry')
            assert button.props.get('data-job-id')==first.job_id and button.enabled
            assert button.text=='退避履歴を再試行'
            callback=next(iter(button._event_listeners.values())).handler
            await callback(SimpleNamespace(args={'epoch':str(view.epoch)}))
            assert not view.retained_list.visible
        row=wait_state(ex,first.job_id,'done')
        assert row.attempt==2 and row.target_id==first.target_id
    finally:
        if client is not None and client.id in Client.instances:client.delete()
        ex.close();managed.close()
