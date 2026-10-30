"""Original physical heavy/light layout and immutable acceptance regressions."""
import json,os
from pathlib import Path
import pytest
from fastapi import FastAPI
from tools.p3_residuals_fixtures import create_services
from pixivvault_nicegui.bookmark_projection import author_folder,destinations,snapshot
from pixivvault_nicegui.fs_paths import native_io_path
from test_bookmark_media_projection import save,finish
from test_legacy_zip_export import Context


@pytest.mark.parametrize('heavy',[False,True])
@pytest.mark.parametrize('mode',['direct','link'])
@pytest.mark.parametrize('kind,fmt',[('novel','TXT'),('ugoira','frames')])
def test_accepted_layout_pins_physical_membership_and_preserves_cas(tmp_path,heavy,mode,kind,fmt):
    s,reader=create_services(FastAPI(),tmp_path/'data')
    try:
        save(s,kind,fmt)
        s.configure_save_exports(tmp_path/'exports','91');ex=s.save_exports
        s.preferences.update({'bookmark_download_mode':mode},expected_revision=s.preferences.snapshot().revision)
        master=native_io_path(ex.projection.root)/'Existing(91)'
        if heavy:master.mkdir()
        source=snapshot(s,kind,'123',fmt,False)
        original={str(f['path']):native_io_path(f['path']).read_bytes() for f in source['files']}
        calls=list(reader.calls)
        j=finish(ex.jobs,ex.enqueue_media(kind,'123',fmt,'91'))
        manifest=json.loads((ex.root/j.result).read_bytes());identity=manifest['source']
        assert identity['author_folder']==('Existing(91)' if heavy else None)
        assert len(manifest['files'])==len(source['files'])*(2 if heavy else 1)
        assert master.exists()==heavy
        targets=[native_io_path(ex.projection.root)/f['path'] for f in manifest['files']]
        assert any('☆ブックマーク(91)' in str(t) for t in targets)
        if heavy:assert any(t.is_relative_to(master) for t in targets)
        if not heavy:
            # A later unrelated master appearance cannot relocate this job.
            master.mkdir()
        assert ex.run(j,Context())==j.result
        assert {str(f['path']):native_io_path(f['path']).read_bytes() for f in source['files']}==original
        assert reader.calls==calls
        if kind=='ugoira':
            for f in manifest['files']:
                if 'ugoira_meta' not in Path(f['path']).name or not f['path'].endswith('.json'):continue
                meta=native_io_path(ex.projection.root)/f['path'];value=json.loads(meta.read_bytes())
                for frame in value['frames']:assert (meta.parent/frame['file']).is_file()
        root=ex.root;ids=[v.job_id for v in ex.jobs.snapshot()]
        ex.close()
        from pixivvault_nicegui.save_exports import SaveExports
        s.save_exports=SaveExports(s,root,'91')
        assert [v.job_id for v in s.save_exports.jobs.snapshot()]==ids
        assert s.save_exports.run(j,Context())==j.result
    finally:s.close()


def test_old_intent_layout_and_multiple_folder_selection_warns(tmp_path,caplog,monkeypatch):
    base=native_io_path(tmp_path);(base/'First(91)').mkdir()
    assert author_folder(tmp_path,'91')=='First(91)'
    old=dict(author_id='91',work_id='123',account='91',mode='link',use_work_folder=False)
    assert len(destinations(old,'Title','Author','novel.txt'))==2
    (base/'Second(91)').mkdir()
    original=Path.iterdir
    def ordered(path):
        if path==base:return iter((base/'Second(91)',base/'First(91)'))
        return original(path)
    monkeypatch.setattr(Path,'iterdir',ordered)
    # Equal fixture directory sizes retain enumerated first, as original max.
    assert author_folder(tmp_path,'91')=='Second(91)'
    assert any('一番大きい' in record.message for record in caplog.records)
