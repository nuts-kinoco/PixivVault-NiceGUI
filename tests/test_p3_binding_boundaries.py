"""Fail-closed metadata boundaries plus independent post-save progress."""
import json
from types import SimpleNamespace
import pytest
from pixivvault_nicegui import save_exports as subject
from pixivvault_nicegui.fs_paths import native_io_path
from test_save_exports import fixture,settled
from test_export_completion_history import req,wait_state


@pytest.mark.parametrize('target',['../outside','not-a-digest'])
def test_legacy_invalid_target_is_refused_before_manifest_fallback_io(tmp_path,monkeypatch,target):
    _,managed,ex=fixture(tmp_path,auto='0',mode='link');reads=[]
    outside=native_io_path(tmp_path/'outside.export.json');outside.write_bytes(b'owned fixture outside export root; never read')
    monkeypatch.setattr(ex.jobs,'accepted_operations',lambda:(('legacy-projection','a'*32,'bookmark-projection',target,'synthetic'),))
    ex._operation_count=-1
    def read(path,limit):reads.append(path);raise AssertionError('unexpected fallback read')
    monkeypatch.setattr(subject,'_raw_read',read)
    try:
        ex._refresh_admissions(None)
        assert reads==[] and ex._unresolved['legacy-projection']=='bookmark-projection'
        with pytest.raises(ValueError,match='unresolved'):ex.enqueue('bookmark-projection',req(ex))
        assert reads==[] and ex.jobs.operation_count==0
        assert outside.read_bytes()==b'owned fixture outside export root; never read'
    finally:ex.close();managed.close()


@pytest.mark.parametrize('unknown',[False,True])
def test_author_journal_issues_preserve_unknown_and_allow_verified_outputs(tmp_path,unknown):
    _,managed,ex=fixture(tmp_path,auto='0',mode='link')
    rel,row,sha=next(v for v in managed.saved_receipts() if v[1]['work_id']=='303');proof=dict(receipt=rel,sha256=sha)
    def issues(aid=None):return ({'23'} if not unknown and aid in (None,'23') else set(),unknown)
    ex.s.author_media.completed_projection_sources=lambda:(dict(kind='illustration',work_id='303',author_id='24',format='original',proof=proof),)
    ex.s.author_media.projection_unresolved_authors=issues;ex.s.author_media._proof=lambda *a:proof
    try:
        ex._author_dependencies()
        assert all(j.state=='done' for j in settled(ex))  # observe actual completion
        deps=ex._author_dependencies()
        assert deps is None if unknown else deps=={'23'}
        author=next(j for j in ex.jobs.snapshot() if j.kind=='author-projection')
        assert author.target_id and author.result
        with pytest.raises(ValueError,match='not settled'):ex.enqueue('bookmark-projection',req(ex,'301'))
        if unknown:
            with pytest.raises(ValueError,match='not settled'):ex.enqueue('bookmark-projection',req(ex,'303'))
        else:
            job=ex.enqueue('bookmark-projection',req(ex,'303'));assert wait_state(ex,job.job_id,'done').state=='done'
        sources=[json.loads(native_io_path(ex.root/(j.target_id+'.json')).read_bytes()) for j in ex.jobs.snapshot()]
        assert not any(v.get('work_id')=='301' for v in sources)
    finally:ex.close();managed.close()



@pytest.mark.parametrize('bad',['source-only','bad-path','bad-sha','duplicate-source'])
def test_v1_retired_publication_needs_complete_original_metadata(tmp_path,bad):
    import sqlite3
    _,managed,ex=fixture(tmp_path,auto='0',mode='link')
    try:
        first=ex.enqueue('bookmark-projection',req(ex,'303'));first=wait_state(ex,first.job_id,'done')
        path=native_io_path(ex.root/first.result);original=path.read_bytes();value=json.loads(original)
        assert ex.jobs.control(first.job_id,'delete');service,root=ex.s,ex.root;ex.close()
        if bad=='source-only':value={'source':value['source']}
        elif bad=='bad-path':value['files'][0]['path']='../outside'
        elif bad=='bad-sha':value['files'][0]['sha256']='not-a-sha'
        raw=json.dumps(value).encode()
        if bad=='duplicate-source':raw=b'{"source":'+json.dumps(value['source']).encode()+b','+raw[1:]
        path.write_bytes(raw)
        with sqlite3.connect(native_io_path(root/'jobs.sqlite3')) as db:
            db.execute('DROP TABLE job_history');db.execute('PRAGMA user_version=1')
        ex=subject.SaveExports(service,root,'12')
        assert ex.jobs.retained_job(first.job_id) is None
        assert [v.job_id for v in ex.retained_requests()]==[first.job_id]
        count=ex.jobs.operation_count
        with pytest.raises(ValueError):ex.enqueue('bookmark-projection',req(ex,'303'))
        assert ex.jobs.operation_count==count and path.read_bytes()==raw
    finally:ex.close();managed.close()


@pytest.mark.parametrize('change',['none','edited','deleted'])
def test_v1_original_completion_metadata_survives_mutable_copy_changes(tmp_path,change):
    import sqlite3
    _,managed,ex=fixture(tmp_path,auto='0',mode='link')
    try:
        first=ex.enqueue('bookmark-projection',req(ex,'303'));first=wait_state(ex,first.job_id,'done')
        manifest=json.loads(native_io_path(ex.root/first.result).read_bytes())
        target=native_io_path(ex.projection.root)/manifest['files'][0]['path']
        if change=='edited':target.write_bytes(b'keep user edit')
        elif change=='deleted':target.unlink()  # private synthetic projection only
        assert ex.jobs.control(first.job_id,'delete');service,root=ex.s,ex.root;ex.close()
        with sqlite3.connect(native_io_path(root/'jobs.sqlite3')) as db:
            db.execute('DROP TABLE job_history');db.execute('PRAGMA user_version=1')
        ex=subject.SaveExports(service,root,'12');count=ex.jobs.operation_count
        assert ex.retained_requests()==() and ex.enqueue('bookmark-projection',req(ex,'303')).job_id==first.job_id
        assert ex.jobs.operation_count==count
        if change=='edited':assert target.read_bytes()==b'keep user edit'
        if change=='deleted':assert not target.exists()
    finally:ex.close();managed.close()



@pytest.mark.parametrize('kind,fmt',[('novel','TXT'),('ugoira','frames')])
def test_v1_direct_media_completion_keeps_original_source_reference_fields(tmp_path,kind,fmt):
    import sqlite3
    from fastapi import FastAPI
    from tools.p3_residuals_fixtures import create_services
    from test_bookmark_media_projection import save,finish
    services,_=create_services(FastAPI(),tmp_path/'data')
    try:
        save(services,kind,fmt);services.configure_save_exports(tmp_path/'exports','91');ex=services.save_exports
        services.preferences.update({'bookmark_download_mode':'direct'},expected_revision=services.preferences.snapshot().revision)
        first=finish(ex.jobs,ex.enqueue_media(kind,'123',fmt,'91'))
        value=json.loads(native_io_path(ex.root/first.result).read_bytes())
        assert value['source']['reference_layout']==1
        assert all(set(row)=={'path','sha256','bytes','source_sha256','source_bytes'} for row in value['files'])
        assert ex.jobs.control(first.job_id,'delete');root=ex.root;ex.close()
        with sqlite3.connect(native_io_path(root/'jobs.sqlite3')) as db:
            db.execute('DROP TABLE job_history');db.execute('PRAGMA user_version=1')
        services.save_exports=subject.SaveExports(services,root,'91');ex=services.save_exports
        count=ex.jobs.operation_count
        assert ex.retained_requests()==() and ex.enqueue_media(kind,'123',fmt,'91').job_id==first.job_id
        assert ex.jobs.operation_count==count
    finally:services.close()



@pytest.mark.parametrize('bad_name',[False,True])
def test_v1_auto_zip_completion_uses_original_manifest_without_reexport(tmp_path,bad_name):
    import sqlite3
    _,managed,ex=fixture(tmp_path,auto='0',mode='link')
    try:
        value=dict(author_id='24',proofs=[(row['work_id'],sha) for rel,row,sha in ex.author_snapshot('24')])
        first=ex.enqueue('auto-zip',value);first=wait_state(ex,first.job_id,'done')
        path=native_io_path(ex.root/first.result);manifest=json.loads(path.read_bytes())
        archive=native_io_path(ex.zip.root/manifest['archive']);original=archive.read_bytes()
        assert ex.jobs.control(first.job_id,'delete');service,root=ex.s,ex.root;ex.close()
        if bad_name:manifest['archive']='../outside.zip';path.write_text(json.dumps(manifest))
        with sqlite3.connect(native_io_path(root/'jobs.sqlite3')) as db:
            db.execute('DROP TABLE job_history');db.execute('PRAGMA user_version=1')
        ex=subject.SaveExports(service,root,'12');count=ex.jobs.operation_count
        if bad_name:
            assert [v.job_id for v in ex.retained_requests()]==[first.job_id]
            with pytest.raises(ValueError):ex.enqueue('auto-zip',value)
        else:
            assert ex.retained_requests()==() and ex.enqueue('auto-zip',value).job_id==first.job_id
        assert ex.jobs.operation_count==count and archive.read_bytes()==original
    finally:ex.close();managed.close()
