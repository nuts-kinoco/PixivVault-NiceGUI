"""CoS: adopted media formats, original layouts, editable copy and recovery."""
import json,os
from pathlib import Path
from uuid import uuid4
import pytest
from fastapi import FastAPI
from tools.p3_residuals_fixtures import create_services
from pixivvault_nicegui.bookmark_projection import snapshot,export_media,destinations
from pixivvault_nicegui.novel_save_presentation import NovelSavePresentation
from pixivvault_nicegui.fs_paths import native_io_path
from test_legacy_zip_export import Context


def finish(owner,job):
    with owner._condition:
        assert owner._condition.wait_for(lambda:any(j.job_id==job.job_id and j.state in ('done','error','stopped') for j in owner.snapshot()),20)
    result=next(j for j in owner.snapshot() if j.job_id==job.job_id)
    assert result.state=='done',result
    return result


def save(s,kind,fmt):
    if kind=='novel':
        s.novel_fixture_source.select('123','91')
        choice=NovelSavePresentation(s).preview('123',fmt)
        return finish(s.novel_jobs.jobs,s.enqueue_novel_export(choice.intent,uuid4().hex))
    if fmt=='mp4':
        codec=os.environ.get('PV_TEST_CODEC_PYTHON')
        if not codec:pytest.skip('existing CPU codec interpreter required')
        s.ugoira.codec_python=Path(codec)
    choice=s.ugoira.preview('123','91',fmt)
    return finish(s.ugoira.jobs,s.enqueue_ugoira_selection(choice,uuid4().hex))


@pytest.mark.parametrize('kind,fmt',[('novel','TXT'),('novel','EPUB'),('novel','BOTH'),('ugoira','gif'),('ugoira','frames'),('ugoira','mp4')])
@pytest.mark.parametrize('mode',['direct','link'])
@pytest.mark.parametrize('folders',['0','1'])
def test_adopted_format_layout_bytes_and_restart(tmp_path,kind,fmt,mode,folders):
    data=tmp_path/'data';out=tmp_path/'exports'
    s,reader=create_services(FastAPI(),data)
    try:
        save(s,kind,fmt)
        s.preferences.update({'bookmark_download_mode':mode,'use_work_folder':folders},expected_revision=s.preferences.snapshot().revision)
        s.configure_save_exports(out,'91');ex=s.save_exports
        ex.remember_bookmark(kind,'123','91')
        calls=list(reader.calls)
        before=snapshot(s,kind,'123',fmt,folders=='1')
        if mode=='link':
            from pixivvault_nicegui.legacy_zip_export import _INVALID_FILENAME
            (native_io_path(ex.projection.root)/(_INVALID_FILENAME.sub('_',before['author'])+'(91)')).mkdir()
        job=finish(ex.jobs,ex.enqueue_media(kind,'123',fmt,'91'))
        manifest=json.loads(native_io_path(ex.root/job.result).read_bytes())
        assert manifest['source']['media']==kind and manifest['source']['format']==fmt
        assert all('(123)' in row['path'] or folders=='1' for row in manifest['files'])
        assert all('☆ブックマーク(91)' in row['path'] for row in manifest['files']) if mode=='direct' else len(manifest['files'])==len(before['files'])*2
        for file in before['files']:
            for rel in destinations(manifest['source'],before['title'],before['author'],file['relative']):
                target=native_io_path(ex.projection.root)/rel
                from hashlib import sha256
                body=native_io_path(ex.projection.root)/'.bodies'/(kind+'-123')/sha256(target.read_bytes()).hexdigest()
                assert os.path.samefile(target,body)
                assert not os.path.samefile(target,native_io_path(file['path']))
                source_bytes=native_io_path(file['path']).read_bytes()
                if mode=='direct' and kind=='ugoira' and fmt=='frames' and Path(file['relative']).name=='ugoira_meta.json':
                    projected=json.loads(target.read_bytes());original=json.loads(source_bytes)
                    assert len(projected['frames'])==len(original['frames'])
                    assert [row['file'] for row in projected['frames']]==[destinations(manifest['source'],before['title'],before['author'],row['file'])[0].name for row in original['frames']]
                    assert [row['delay'] for row in projected['frames']]==[row['delay'] for row in original['frames']]
                    for frame,stored in zip(projected['frames'],original['frames']):
                        assert (target.parent/frame['file']).read_bytes()==native_io_path(file['path']).parent.joinpath(stored['file']).read_bytes()
                else:assert target.read_bytes()==source_bytes
        assert reader.calls==calls
        ids=[j.job_id for j in ex.jobs.snapshot()]
    finally:s.close()
    s,reader=create_services(FastAPI(),data)
    try:
        s.configure_save_exports(out,'91')
        assert [j.job_id for j in s.save_exports.jobs.snapshot()]==ids
        old=next(j for j in s.save_exports.jobs.snapshot() if j.job_id==job.job_id)
        assert s.save_exports.run(old,Context())==old.result
        assert not reader.calls
    finally:s.close()


def test_same_numeric_id_and_txt_both_formats_do_not_alias_cas(tmp_path):
    s,reader=create_services(FastAPI(),tmp_path/'data')
    try:
        s.configure_save_exports(tmp_path/'exports','91');ex=s.save_exports
        for kind,fmt in [('novel','TXT'),('novel','BOTH'),('ugoira','gif'),('ugoira','frames')]:
            save(s,kind,fmt)
            finish(ex.jobs,ex.enqueue_media(kind,'123',fmt,'91'))
        assert len(ex.jobs.snapshot())==4
        assert all(j.state=='done' for j in ex.jobs.snapshot())
        assert native_io_path(ex.projection.root/'.bodies'/'novel-123').is_dir()
        assert native_io_path(ex.projection.root/'.bodies'/'ugoira-123').is_dir()
    finally:s.close()


def test_external_projection_edit_and_changed_generation_are_not_overwritten(tmp_path,monkeypatch):
    from pixivvault_nicegui import bookmark_projection as bp
    s,reader=create_services(FastAPI(),tmp_path/'data')
    try:
        save(s,'novel','TXT');s.configure_save_exports(tmp_path/'exports','91');ex=s.save_exports
        job=finish(ex.jobs,ex.enqueue_media('novel','123','TXT'))
        manifest=json.loads(native_io_path(ex.root/job.result).read_bytes());before=snapshot(s,'novel','123','TXT')
        source=before['files'][0]['path'];original=native_io_path(source).read_bytes()
        target=native_io_path(ex.projection.root)/manifest['files'][0]['path'];target.write_bytes(b'user edit')
        with pytest.raises(ValueError,match='changed'):ex.run(job,Context())
        assert native_io_path(source).read_bytes()==original and target.read_bytes()==b'user edit'
        changed=dict(before,proof='a'*64)
        monkeypatch.setattr(bp,'snapshot',lambda *a,**kw:changed)
        with pytest.raises(ValueError,match='generation changed'):ex.run(job,Context())
        assert target.read_bytes()==b'user edit'
    finally:s.close()


def test_partial_projection_failure_can_retry_existing_owned_files(tmp_path,monkeypatch):
    from pixivvault_nicegui import bookmark_projection as bp
    s,reader=create_services(FastAPI(),tmp_path/'data')
    try:
        save(s,'novel','BOTH');s.configure_save_exports(tmp_path/'exports','91');ex=s.save_exports
        original=bp.os.link;count=[0]
        def fail_second(source,target,*a,**kw):
            count[0]+=1
            if count[0]==2:raise OSError('synthetic interruption')
            return original(source,target,*a,**kw)
        monkeypatch.setattr(bp.os,'link',fail_second)
        accepted=ex.enqueue_media('novel','123','BOTH')
        with ex.jobs._condition:
            assert ex.jobs._condition.wait_for(lambda:any(j.job_id==accepted.job_id and j.state=='error' for j in ex.jobs.snapshot()),20)
        monkeypatch.setattr(bp.os,'link',original)
        failed=next(j for j in ex.jobs.snapshot() if j.job_id==accepted.job_id)
        assert ex.jobs.control(failed.job_id,'retry',expected_control_revision=failed.control_revision)
        completed=finish(ex.jobs,failed)
        assert completed.attempt==2 and completed.job_id==accepted.job_id
    finally:s.close()


def test_bookmark_catalog_provenance_survives_restart_without_new_get(tmp_path):
    data=tmp_path/'data';out=tmp_path/'exports'
    s,reader=create_services(FastAPI(),data)
    try:
        s.configure_save_exports(out,'91')
        reader.get_bookmarked_novels=lambda uid,rest:[{'id':'123','user_id':'91','title':'Generated novel'}]
        assert s.novel_catalog(None,'show')==(('123','91','Generated novel'),)
        save(s,'novel','EPUB')
        before=list(reader.calls);s.save_exports.scan_media()
        with s.save_exports.jobs._condition:
            assert s.save_exports.jobs._condition.wait_for(lambda:len(s.save_exports.jobs.snapshot())==1 and s.save_exports.jobs.snapshot()[0].state=='done',20)
        assert reader.calls==before
    finally:s.close()
    s,reader=create_services(FastAPI(),data)
    try:
        s.configure_save_exports(out,'91');s.save_exports.scan_media()
        assert len(s.save_exports.jobs.snapshot())==1 and not reader.calls
        with pytest.raises(ValueError,match='author changed'):s.save_exports.enqueue_media('novel','123','EPUB','92')
    finally:s.close()
