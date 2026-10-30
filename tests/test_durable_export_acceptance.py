"""Window-independent acceptance and author/bookmark dependency regressions."""
from pathlib import Path
from types import SimpleNamespace
import json
import pytest
from test_save_exports import fixture,settled
from pixivvault_nicegui.save_exports import SaveExports,packet,digest,RetainedExport
from pixivvault_nicegui.fs_paths import native_io_path


def request(ex,wid='301',account='12'):
    rel,row,sha=next(v for v in ex.s.managed_downloads.saved_receipts() if v[1]['work_id']==wid)
    return dict(relative=rel,sha256=sha,work_id=wid,account=account,mode='link',use_work_folder=False)


def legacy_accept(ex,value):
    key=digest(value);native_io_path(ex.root/(key+'.json')).write_bytes(packet(value))
    return ex.jobs.enqueue('bookmark-projection',key,operation_id='bookmark-projection:'+key)


@pytest.mark.parametrize('legacy',[False,True])
def test_more_than_fifty_completed_jobs_retain_first_request_after_restart(tmp_path,legacy):
    _,managed,ex=fixture(tmp_path,auto='0',mode='link')
    try:
        value=request(ex)
        first=legacy_accept(ex,value) if legacy else ex.enqueue('bookmark-projection',value)
        assert all(j.state=='done' for j in settled(ex))
        intent=native_io_path(ex.root/(first.target_id+'.json'));before=intent.read_bytes()
        for index in range(51):
            ex.enqueue('bookmark-projection',request(ex,account=str(1000+index)))
            assert all(j.state=='done' for j in settled(ex))
        assert len(ex.jobs.snapshot())==50
        assert all(j.job_id!=first.job_id for j in ex.jobs.snapshot())
        operations=ex.jobs.operation_count;ids=[j.job_id for j in ex.jobs.snapshot()]
        (native_io_path(ex.projection.root)/'Later(23)').mkdir()
        for _ in range(3):
            retained=ex.enqueue('bookmark-projection',value)
            assert isinstance(retained,RetainedExport) and retained.job_id==first.job_id
            assert retained.state=='retained'
        assert ex.jobs.operation_count==operations and [j.job_id for j in ex.jobs.snapshot()]==ids
        service,root=ex.s,ex.root;ex.close();ex=SaveExports(service,root,'12')
        retained=ex.enqueue('bookmark-projection',value)
        assert retained.job_id==first.job_id and retained.target_id==first.target_id
        assert ex.jobs.operation_count==operations and len(ex.jobs.snapshot())==50
        assert intent.read_bytes()==before
    finally:ex.close();managed.close()


@pytest.mark.parametrize('legacy',[False,True])
@pytest.mark.parametrize('restart',[False,True])
def test_unreadable_accepted_intent_never_becomes_a_new_request(tmp_path,monkeypatch,legacy,restart):
    import pixivvault_nicegui.save_exports as module
    _,managed,ex=fixture(tmp_path,auto='0',mode='link')
    try:
        value=request(ex);first=legacy_accept(ex,value) if legacy else ex.enqueue('bookmark-projection',value)
        settled(ex);path=native_io_path(ex.root/(first.target_id+'.json'));before=path.read_bytes()
        if restart:
            service,root=ex.s,ex.root;ex.close();ex=SaveExports(service,root,'12')
        (native_io_path(ex.projection.root)/'Later(23)').mkdir()
        count=ex.jobs.operation_count;fired=[];real=module._raw_read
        def fail(node,limit):
            if native_io_path(node)==path:
                fired.append(node);raise PermissionError('synthetic intent lock')
            return real(node,limit)
        with monkeypatch.context() as patcher:
            patcher.setattr(module,'_raw_read',fail)
            with pytest.raises((PermissionError,ValueError)):
                ex.enqueue('bookmark-projection',value)
        assert fired and ex.jobs.operation_count==count
        assert ex.enqueue('bookmark-projection',value).job_id==first.job_id
        assert path.read_bytes()==before and ex.jobs.operation_count==count
    finally:ex.close();managed.close()


def test_global_author_db_failure_defers_bookmarks_until_recovery(tmp_path):
    _,managed,ex=fixture(tmp_path,auto='0',mode='link')
    def fail():raise PermissionError('synthetic author DB lock')
    ex.s.author_media.completed_projection_sources=fail
    try:
        ex.scan();assert not ex.jobs.snapshot() and 'authors' in ex.scan_errors
        (native_io_path(ex.projection.root)/'Recovered(23)').mkdir()
        ex.s.author_media.completed_projection_sources=lambda:()
        ex.scan();jobs=settled(ex)
        assert jobs and all(j.state=='done' for j in jobs)
        values=[json.loads(native_io_path(ex.root/(j.target_id+'.json')).read_bytes()) for j in jobs]
        assert all(v['author_folder']=='Recovered(23)' for v in values if v['work_id'] in ('301','302'))
    finally:ex.close();managed.close()


def test_one_author_admission_failure_defers_only_dependent_bookmarks(tmp_path,monkeypatch):
    _,managed,ex=fixture(tmp_path,auto='0',mode='link')
    values={row['work_id']:(rel,row,sha) for rel,row,sha in managed.saved_receipts()}
    def proof(kind,wid,fmt):return dict(receipt=values[wid][0],sha256=values[wid][2])
    ex.s.author_media.completed_projection_sources=lambda:tuple(dict(kind='illustration',work_id=wid,author_id=aid,format='original',proof=proof('illustration',wid,'original')) for wid,aid in [('301','23'),('303','24')])
    ex.s.author_media._proof=proof
    real=ex.enqueue;failed=[True];fired=[]
    def enqueue(kind,identity,**kwargs):
        if kind=='author-projection' and identity['author_id']=='23' and failed[0]:
            fired.append(kind);raise PermissionError('synthetic author admission lock')
        return real(kind,identity,**kwargs)
    monkeypatch.setattr(ex,'enqueue',enqueue)
    try:
        ex.scan();settled(ex);ex.scan();settled(ex)
        admitted=[json.loads(native_io_path(ex.root/(j.target_id+'.json')).read_bytes()) for j in ex.jobs.snapshot()]
        assert fired and not any(v.get('output_scope')!='author' and v['work_id'] in ('301','302') for v in admitted)
        assert any(v.get('output_scope')!='author' and v['work_id']=='303' for v in admitted)
        failed[0]=False;ex.scan();settled(ex);ex.scan();settled(ex)
        admitted=[json.loads(native_io_path(ex.root/(j.target_id+'.json')).read_bytes()) for j in ex.jobs.snapshot()]
        assert any(v.get('output_scope')!='author' and v['work_id']=='301' and v['author_folder'] for v in admitted)
    finally:ex.close();managed.close()


def test_existing_projection_folder_does_not_bypass_legacy_zip(tmp_path):
    _,managed,ex=fixture(tmp_path,auto='0',mode='link')
    root=native_io_path(tmp_path/'legacy');root.mkdir();archive=root/'Author(23).zip';archive.write_bytes(b'keep')
    ex.s.library_image_root=root;(native_io_path(ex.projection.root)/'Present(23)').mkdir()
    try:
        with pytest.raises(ValueError,match='ZIP author'):ex.enqueue('bookmark-projection',request(ex))
        assert ex.jobs.operation_count==0 and archive.read_bytes()==b'keep'
    finally:ex.close();managed.close()


def test_corrupt_intent_is_preserved_without_repin_and_recovers_after_restore(tmp_path):
    _,managed,ex=fixture(tmp_path,auto='0',mode='link')
    try:
        value=request(ex);first=ex.enqueue('bookmark-projection',value);settled(ex)
        path=native_io_path(ex.root/(first.target_id+'.json'));before=path.read_bytes()
        path.write_bytes(b'{broken')
        (native_io_path(ex.projection.root)/'Later(23)').mkdir()
        count=ex.jobs.operation_count
        with pytest.raises(ValueError):ex.enqueue('bookmark-projection',value)
        assert path.read_bytes()==b'{broken' and ex.jobs.operation_count==count
        path.write_bytes(before)
        assert ex.enqueue('bookmark-projection',value).job_id==first.job_id
    finally:ex.close();managed.close()


def test_status_is_visible_in_queue_and_repeated_warning_is_suppressed(tmp_path,caplog):
    from fastapi import FastAPI
    from nicegui import Client,ui
    from tools.p3_residuals_fixtures import create_services
    from pixivvault_nicegui.ui.job_views import QueueView
    services,_=create_services(FastAPI(),tmp_path/'data')
    client=Client(ui.page('/_export_hold_status'))
    try:
        services.configure_save_exports(tmp_path/'exports','91');ex=services.save_exports
        for _ in range(3):ex._scan_error('author:91',ValueError('legacy ZIP author layout requires accepted update policy'))
        with client:
            view=QueueView(services);view.refresh()
            assert 'author:91' in view.export_notice.text and '旧ZIP作者' in view.export_notice.text
        assert len([r for r in caplog.records if '保存後処理を保留' in r.message])==1
    finally:client.delete();services.close()
