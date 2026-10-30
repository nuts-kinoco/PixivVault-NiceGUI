"""Original post-save operations over durable synthetic history, no live GET."""
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from test_save_exports import fixture,settled
from test_export_completion_history import bind_author,req,wait_state
from test_legacy_zip_export import Job,Context,SyntheticOriginals
from test_durable_export_acceptance import legacy_accept
from test_bookmark_media_projection import save
from tools.p3_residuals_fixtures import create_services
from pixivvault_nicegui.save_exports import SaveExports
from pixivvault_nicegui.job_service import JobService
from pixivvault_nicegui.managed_downloads import ManagedPixivDownloads
from pixivvault_nicegui.managed_download_requests import ManagedAuthorIntent,ManagedDownloadRequests
from pixivvault_nicegui.fs_paths import native_io_path,logical_path


def _author_generation_fixture(tmp_path):
    """Start with a genuine author generation, not an ordinary output receipt."""
    remote=SyntheticOriginals()
    managed=ManagedPixivDownloads(tmp_path/'managed',remote,resolve_record=lambda wid:remote.records[wid])
    try:
        revision=remote.get_artwork_update_revision('301','23')['update_date']
        first=ManagedAuthorIntent(remote.records['301'],'id',revision,None,'new')
        initial=managed.run(Job('301'),Context(),intent=first)
        assert initial.startswith('generations/')
        for wid in ('302','303'):managed.run(Job(wid),Context())
        settings={'zip_all_after_download':'0','bookmark_download_mode':'link'}
        service=SimpleNamespace(managed_downloads=managed,
            preferences=SimpleNamespace(get_setting=lambda key,default=None:settings.get(key,default)),
            author_media=SimpleNamespace(author_zip_setting=lambda aid:None),
            static_jobs=SimpleNamespace(snapshot=lambda:tuple(SimpleNamespace(target_id=wid,state='done',job_id=wid) for wid in remote.records)),
            _managed_job_records=dict(remote.records),bookmark_download_candidates=lambda:tuple(remote.records.values()))
        return remote,managed,SaveExports(service,tmp_path/'exports','12')
    except BaseException:
        managed.close();raise


@pytest.mark.parametrize('edit',[False,True])
def test_author_new_generation_does_not_overwrite_or_hold_other_bookmarks(tmp_path,edit):
    remote,managed,ex=_author_generation_fixture(tmp_path);bind_author(ex)
    try:
        ex.scan_authors();first=settled(ex)[0];assert first.state=='done'
        manifest=json.loads(native_io_path(ex.root/first.result).read_bytes())
        target=native_io_path(ex.projection.root)/manifest['files'][0]['path']
        if edit:target.write_bytes(b'user edit')
        kept=target.read_bytes();old=managed.verified_saved_receipt('301')
        assert old is not None and len(old)==5 and old[0].startswith('generations/')
        assert old[3]==remote.get_artwork_update_revision('301','23')['update_date']
        assert old[4] is not None and len(old[4])==64
        old_page=managed.read_saved_page(old[0],0,old[2])
        remote.version=2
        class Update(Job):job_id='b'*32
        revision=remote.get_artwork_update_revision('301','23')['update_date']
        intent=ManagedAuthorIntent(remote.records['301'],'id',revision,old[2],'updated',old[0])
        managed.run(Update('301'),Context(),intent=intent)
        current=managed.verified_saved_receipt('301')
        assert current is not None and len(current)==5 and current[0].startswith('generations/')
        assert current[0]!=old[0] and current[2]!=old[2]
        assert current[3]==revision and current[4] is not None and current[4]!=old[4]
        assert managed.read_saved_page(current[0],0,current[2])!=old_page
        bind_author(ex);count=ex.jobs.operation_count
        for _ in range(2):assert ex.scan_authors()==set()
        assert ex.jobs.operation_count==count and 'author:301' in ex.scan_errors
        assert '世代更新' in ex.scan_errors['author:301']
        later=ex.enqueue('bookmark-projection',req(ex,'302'));wait_state(ex,later.job_id,'done')
        ex.scan();assert all(j.state=='done' for j in settled(ex))
        assert 'static:301' in ex.scan_errors  # affected work remains explicit
        sources=[json.loads(native_io_path(ex.root/(j.target_id+'.json')).read_bytes()) for j in ex.jobs.snapshot()]
        assert not any(v.get('work_id')=='301' and v.get('output_scope')!='author' for v in sources)
        assert any(v.get('work_id')=='302' and v.get('output_scope')!='author' for v in sources)
        assert target.read_bytes()==kept and managed.read_saved_page(old[0],0,old[2])==old_page
        assert managed.verified_saved_receipt('301')==current
        service,root=ex.s,ex.root;ex.close();ex=SaveExports(service,root,'12')
        count=ex.jobs.operation_count;assert ex.scan_authors()==set()
        assert ex.jobs.operation_count==count and target.read_bytes()==kept
    finally:ex.close();managed.close()


def test_one_scan_reuses_dependencies_and_direct_enqueue_scopes_the_author(tmp_path):
    _,managed,ex=fixture(tmp_path,auto='0',mode='link');full=[];scoped=[]
    ex.s.author_media.completed_projection_sources=lambda:full.append('all') or ()
    try:
        ex.scan();assert all(j.state=='done' for j in settled(ex))
        assert full==['all']
        def targeted(aid):scoped.append(aid);return ()
        ex.s.author_media.completed_projection_sources_for_author=targeted
        def forbidden():pytest.fail('unrelated author scan')
        ex.s.author_media.completed_projection_sources=forbidden
        job=ex.enqueue('bookmark-projection',req(ex,'301','99'));wait_state(ex,job.job_id,'done')
        assert scoped==['23']
    finally:ex.close();managed.close()


def test_unreadable_unknown_legacy_identity_keeps_known_independent_acceptance(tmp_path,monkeypatch):
    _,managed,ex=fixture(tmp_path,auto='0',mode='link');real=ex._run
    def run(job,context):
        value=ex._read_intent(job)
        if value['work_id']=='301':raise OSError('synthetic failure before manifest')
        return real(job,context)
    monkeypatch.setattr(ex,'_run',run)
    try:
        bad=legacy_accept(ex,req(ex));wait_state(ex,bad.job_id,'error')
        good=ex.enqueue('bookmark-projection',req(ex,'303'));wait_state(ex,good.job_id,'done')
        path=native_io_path(ex.root/(bad.target_id+'.json'));path.write_bytes(b'{unreadable')
        service,root=ex.s,ex.root;ex.close();ex=SaveExports(service,root,'12')
        count=ex.jobs.operation_count
        assert ex.enqueue('bookmark-projection',req(ex,'303')).job_id==good.job_id
        with pytest.raises(ValueError,match='unresolved'):ex.enqueue('bookmark-projection',req(ex,'302'))
        assert ex.jobs.operation_count==count and path.read_bytes()==b'{unreadable'
        assert not native_io_path(ex.root/(bad.target_id+'.export.json')).exists()
    finally:ex.close();managed.close()


def test_static_done_outside_fifty_window_survives_catalog_and_process_restart(tmp_path):
    remote,managed,ex=fixture(tmp_path,auto='0',mode='link')
    store=ManagedDownloadRequests(managed.root)
    def run(job,context):
        saved=managed.verified_saved_receipt(job.target_id)
        assert saved is not None
        rel,row,sha,_update_date,_accepted_fingerprint=saved
        assert isinstance(sha,str) and len(sha)==64
        context.progress(row['page_count'],row['page_count']);return rel
    db=tmp_path/'static.sqlite3';jobs=JobService(run,db)
    def accept(wid,op):
        record=remote.records[wid];store.store(op,record)
        job=jobs.enqueue('pixiv-illust',wid,record.title,operation_id=op)
        with jobs._condition:
            assert jobs._condition.wait_for(lambda:any(j.job_id==job.job_id and j.state=='done' for j in jobs.snapshot()),12)
        return job
    try:
        first=accept('301','static-first')
        for index in range(51):accept('303','static-'+str(index))
        assert len(jobs.snapshot())==50 and all(j.job_id!=first.job_id for j in jobs.snapshot())
        ex.s.static_jobs=jobs;ex.s._managed_requests=store;ex.s._managed_job_records={}
        ex.s.bookmark_download_candidates=lambda:()
        # A v1 static request alone is not bookmark provenance. Restore the
        # retained records, but admit no new projection until a bookmark read
        # actually includes these two works.
        before_calls=remote.remote_requests
        before_receipts=managed.saved_receipts()
        ex.scan();assert not settled(ex)
        ex.s.bookmark_download_candidates=lambda:tuple(remote.records[wid] for wid in ('301','303'))
        ex.scan();output=settled(ex);assert len(output)==2 and all(j.state=='done' for j in output)
        source=[json.loads(native_io_path(ex.root/(j.target_id+'.json')).read_bytes()) for j in output]
        assert {v['work_id'] for v in source}=={'301','303'}
        ids=[j.job_id for j in output]
        manifests={j.result:native_io_path(ex.root/j.result).read_bytes() for j in output}
        projected_root=native_io_path(ex.projection.root)
        projected={p.relative_to(projected_root).as_posix():p.read_bytes()
                   for p in projected_root.rglob('*') if p.is_file()}
        service,root=ex.s,ex.root
        # Losing the read cache after durable export acceptance must not erase
        # its intent, completed output, or original retired static job.
        service.bookmark_download_candidates=lambda:()
        ex.close();jobs.close()
        jobs=JobService(run,db);service.static_jobs=jobs;ex=SaveExports(service,root,'12')
        ex.scan();assert [j.job_id for j in ex.jobs.snapshot()]==ids
        assert jobs.retained_job(first.job_id).state=='done'
        assert managed.saved_receipts()==before_receipts
        assert remote.remote_requests==before_calls
        assert all(native_io_path(ex.root/name).read_bytes()==body for name,body in manifests.items())
        assert {p.relative_to(projected_root).as_posix():p.read_bytes()
                for p in projected_root.rglob('*') if p.is_file()}==projected
    finally:ex.close();jobs.close();managed.close()


def test_bad_author_reparse_does_not_stop_following_author_or_two_media(tmp_path,monkeypatch):
    _,managed,ex=fixture(tmp_path,auto='0',mode='link');media,_=create_services(FastAPI(),tmp_path/'media')
    fired=[];original=Path.lstat
    try:
        save(media,'novel','TXT');save(media,'ugoira','frames')
        media.preferences.update({'novel_save_format':'txt','ugoira_save_format':'folder','zip_all_after_download':'0','bookmark_download_mode':'link'},expected_revision=media.preferences.snapshot().revision)
        ex.s.preferences=media.preferences;ex.s.novel_jobs=media.novel_jobs;ex.s.novel_fixture_source=media.novel_fixture_source;ex.s.ugoira=media.ugoira
        rows={v[1]['work_id']:v for v in managed.saved_receipts()}
        def proof(kind,wid,fmt):return dict(receipt=rows[wid][0],sha256=rows[wid][2])
        ex.s.author_media._proof=proof
        ex.s.author_media.completed_projection_sources=lambda:({'kind':'illustration'},)+tuple(dict(kind='illustration',work_id=w,author_id=a,format='original',proof=proof('illustration',w,'original')) for w,a in [('301','23'),('303','24')])
        unsafe=native_io_path(ex.projection.root)/'Unsafe(23)';unsafe.mkdir()
        class Changed:
            def __init__(self,value):self.value=value
            def __getattr__(self,key):return 0x400 if key=='st_file_attributes' else getattr(self.value,key)
        def lstat(path,*args,**kw):
            value=original(path,*args,**kw)
            if logical_path(path)==logical_path(unsafe):fired.append(path);return Changed(value)
            return value
        monkeypatch.setattr(Path,'lstat',lstat)
        ex.remember_bookmark('novel','123','91');ex.remember_bookmark('ugoira','123','91')
        for _ in range(2):ex.scan();assert all(j.state=='done' for j in settled(ex))
        sources=[json.loads(native_io_path(ex.root/(j.target_id+'.json')).read_bytes()) for j in ex.jobs.snapshot()]
        assert fired and 'author-entry:0' in ex.scan_errors and 'author:301' in ex.scan_errors
        assert any(v.get('work_id')=='303' for v in sources)
        assert {v.get('media') for v in sources}>={'novel','ugoira'}
        assert not any(v.get('work_id') in ('301','302') for v in sources)
        assert unsafe.is_dir()
    finally:ex.close();managed.close();media.close()



def test_real_author_reconciliation_verifies_only_the_requested_author(tmp_path,monkeypatch):
    from pixivvault_nicegui.author_media import AuthorMediaCandidate,canonical
    from pixivvault_nicegui.author_download import Difference
    # Actual author DB and reconciliation; only the local owning-job/proof
    # boundary is injected so no client GET or real user data is involved.
    services,_=create_services(FastAPI(),tmp_path/'data');owner=services.author_media;calls=[]
    try:
        owner._open()
        records=[]
        with owner._db() as db:
            for index,(wid,aid) in enumerate([('301','23'),('303','24')],1):
                candidate=AuthorMediaCandidate('illustration',wid,aid,'2026-10-27',1,'synthetic',Difference.NEW)
                op=str(index)*32;jid=str(index+2)*32;receipt='fixture-receipt-'+wid
                db.execute('INSERT INTO admissions(operation_id,media_key,spec,job_id) VALUES(?,?,?,?)',(op,wid,canonical(candidate.admission()),jid))
                records.append(SimpleNamespace(job_id=jid,state='done',result=receipt,op=op,wid=wid))
        jobs=SimpleNamespace(snapshot=lambda:tuple(records),operation_ids=lambda jid:tuple(r.op for r in records if r.job_id==jid))
        monkeypatch.setattr(owner,'_owner_jobs',lambda kind:jobs)
        def proof(kind,wid,fmt):
            calls.append(wid);return dict(receipt='fixture-receipt-'+wid,sha256='a'*64)
        monkeypatch.setattr(owner,'_proof',proof)
        sources=owner.completed_projection_sources_for_author('23')
        assert calls==['301'] and [r['author_id'] for r in sources]==['23']
    finally:services.close()



def test_external_or_expired_dependency_values_cannot_bypass_direct_admission(tmp_path):
    _,managed,ex=fixture(tmp_path,auto='0',mode='link');calls=[]
    def failed():calls.append(True);raise PermissionError('synthetic author DB unavailable')
    ex.s.author_media.completed_projection_sources=failed
    try:
        with ex._dependency_scope(set()) as expired:pass
        for value in (set(),expired):
            with pytest.raises(ValueError,match='not settled'):
                ex.enqueue('bookmark-projection',req(ex),dependencies=value)
        assert calls==[True,True] and ex.jobs.operation_count==0
    finally:ex.close();managed.close()
