"""All-media author output over durable author admissions and synthetic CAS."""
import json
from pathlib import Path
from uuid import uuid4
from fastapi import FastAPI
import pytest
from tools.p3_residuals_fixtures import create_services
from pixivvault_nicegui.bookmark_projection import legacy_membership
from pixivvault_nicegui.fs_paths import native_io_path
from test_p3_residuals_fixtures import wait_done


def test_all_media_author_output_without_bookmark_account_restarts_without_get(tmp_path):
    data=tmp_path/'data';out=tmp_path/'exports'
    services,reader=create_services(FastAPI(),data)
    try:
        services.preferences.update({'novel_save_format':'both','ugoira_save_format':'folder'},expected_revision=services.preferences.snapshot().revision)
        for c in services.author_media_preview('91'):
            services.enqueue_author_media(c,uuid4().hex,origin='id')
        for owner in (services.static_jobs,services.novel_jobs.jobs,services.ugoira.jobs):
            for job in owner.snapshot():wait_done(owner,job)
        sources=services.author_media.completed_projection_sources()
        assert {v['kind'] for v in sources}=={'illustration','novel','ugoira'}
        calls=list(reader.calls)
        services.configure_save_exports(out,None);ex=services.save_exports
        ex.scan()
        for job in ex.jobs.snapshot():wait_done(ex.jobs,job)
        ex.scan()
        jobs=ex.jobs.snapshot()
        assert len(jobs)==3 and all(j.kind=='author-projection' and j.state=='done' for j in jobs)
        manifests=[json.loads(native_io_path(out/j.result).read_bytes()) for j in jobs]
        assert all(v['source']['output_scope']=='author' for v in manifests)
        for manifest in manifests:
            for row in manifest['files']:
                path=native_io_path(ex.projection.root)/row['path']
                assert path.is_file() and '☆ブックマーク' not in row['path']
                assert path.relative_to(native_io_path(ex.projection.root)).parts[0].endswith('(91)')
        assert reader.calls==calls
        identity=[(j.job_id,j.result,j.attempt) for j in jobs]
    finally:services.close()
    services,reader=create_services(FastAPI(),data)
    try:
        services.configure_save_exports(out,None);ex=services.save_exports
        ex.scan()
        assert [(j.job_id,j.result,j.attempt) for j in ex.jobs.snapshot()]==identity
        assert all(j.state=='done' for j in ex.jobs.snapshot())
        assert reader.calls==[]
    finally:services.close()


def test_legacy_membership_is_readonly_excludes_bookmark_and_rejects_zip_ambiguity(tmp_path):
    root=native_io_path(tmp_path/'legacy');root.mkdir()
    bookmark=root/'☆ブックマーク(12)';bookmark.mkdir()
    (bookmark/'Light(91)').mkdir()
    assert legacy_membership(root,'91') is None
    master=root/'Old name(91)';master.mkdir()
    original=master/'original.bin';original.write_bytes(b'original data')
    assert legacy_membership(root,'91')=='Old name(91)'
    assert original.read_bytes()==b'original data'
    archive=root/'Other(91).ZIP';archive.write_bytes(b'old ZIP never adopted')
    with pytest.raises(ValueError,match='ZIP author'):legacy_membership(root,'91')
    assert archive.read_bytes()==b'old ZIP never adopted'
    (root/'Zipped(92).zip').write_bytes(b'keep')
    with pytest.raises(ValueError,match='ZIP author'):legacy_membership(root,'92')
    assert original.read_bytes()==b'original data'


def test_new_bookmark_inherits_master_name_but_never_links_old_user_files(tmp_path):
    from test_save_exports import fixture,settled
    _,managed,ex=fixture(tmp_path,auto='0',mode='link')
    legacy=native_io_path(tmp_path/'old-master');legacy.mkdir()
    folder=legacy/'Earlier author(23)';folder.mkdir()
    original=folder/'old.png';original.write_bytes(b'old data')
    ex.s.library_image_root=legacy
    try:
        ex.scan();jobs=settled(ex)
        assert jobs and all(j.state=='done' for j in jobs)
        result=[json.loads(native_io_path(ex.root/j.result).read_bytes()) for j in jobs]
        target=next(v for v in result if v['source']['work_id']=='301')
        assert target['source']['author_folder']=='Earlier author(23)'
        assert any(Path(v['path']).parts[0]=='Earlier author(23)' for v in target['files'])
        assert original.read_bytes()==b'old data'
        assert list(folder.iterdir())==[original]
    finally:ex.close();managed.close()


def test_done_without_author_admission_is_not_exported_as_author(tmp_path):
    from test_bookmark_media_projection import save
    services,_=create_services(FastAPI(),tmp_path/'data')
    try:
        save(services,'novel','TXT')
        services.configure_save_exports(tmp_path/'exports',None)
        assert services.author_media.completed_projection_sources()==()
        services.save_exports.scan()
        assert services.save_exports.jobs.snapshot()==()
    finally:services.close()


def test_failed_author_output_retries_same_job_without_mutating_source(tmp_path,monkeypatch):
    from test_save_exports import settled
    import os
    services,reader=create_services(FastAPI(),tmp_path/'data')
    try:
        for c in services.author_media_preview('91'):
            services.enqueue_author_media(c,uuid4().hex,origin='id')
        for owner in (services.static_jobs,services.novel_jobs.jobs,services.ugoira.jobs):
            for job in owner.snapshot():wait_done(owner,job)
        proofs=services.author_media.reconcile()[0];calls=list(reader.calls)
        services.configure_save_exports(tmp_path/'exports',None);ex=services.save_exports
        root=native_io_path(ex.projection.root);real=os.link;seen=[];fired=[]
        def fail(source,target,*args,**kwargs):
            seen.append(target)
            if native_io_path(target).is_relative_to(root) and not fired:
                fired.append(target)
                raise OSError('synthetic projection link failure')
            return real(source,target,*args,**kwargs)
        with monkeypatch.context() as patcher:
            patcher.setattr(os,'link',fail)
            ex.scan();jobs=settled(ex)
        assert seen and len(fired)==1
        failed=next(j for j in jobs if j.state=='error')
        assert ex.jobs.control(failed.job_id,'retry',expected_control_revision=failed.control_revision)
        wait_done(ex.jobs,next(j for j in ex.jobs.snapshot() if j.job_id==failed.job_id))
        current=next(j for j in ex.jobs.snapshot() if j.job_id==failed.job_id)
        assert current.state=='done' and current.attempt==2
        ex.scan();assert len(ex.jobs.snapshot())==3
        assert services.author_media.reconcile()[0]==proofs and reader.calls==calls
    finally:services.close()
