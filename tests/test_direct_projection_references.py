"""CoS: resolve references from direct TXT/JSON into the actual copied files."""
from pathlib import Path
from types import SimpleNamespace
from hashlib import sha256
import json
import pytest
from pixivvault_nicegui import bookmark_projection as subject
from pixivvault_nicegui.save_exports import SaveExports
from pixivvault_nicegui.fs_paths import native_io_path
from test_legacy_zip_export import Context


@pytest.mark.parametrize('kind',['novel','ugoira'])
def test_flattened_references_resolve_exact_source_bytes_and_leave_cas_unchanged(tmp_path,monkeypatch,kind):
    cas=tmp_path/'cas';cas.mkdir()
    asset=cas/'123-7.png';asset.write_bytes(b'fixture original asset bytes')
    if kind=='novel':
        content='題名\r\n[挿絵: images/123-7.png]\r\nuntouched images/unknown.png'
        main=cas/'Book(123).txt';main.write_bytes(content.encode('utf-8-sig'))
        relative='Book(123).txt';asset_relative='images/123-7.png';fmt='TXT'
    else:
        main=cas/'ugoira_meta.json';main.write_bytes(b'{"frames":[{"file":"123-7.png","delay":90}]}\n')
        relative='Folder(123)/ugoira_meta.json';asset_relative='Folder(123)/123-7.png';fmt='frames'
    original_main=main.read_bytes();original_asset=asset.read_bytes()
    def file(path,relative):
        return dict(path=path,relative=relative,bytes=len(path.read_bytes()),sha256=sha256(path.read_bytes()).hexdigest())
    source=dict(proof='a'*64,author_id='91',author='Author',title='Book',files=[file(main,relative),file(asset,asset_relative)])
    monkeypatch.setattr(subject,'snapshot',lambda *a,**kw:source)
    ex=SaveExports.__new__(SaveExports);ex.s=object();ex.root=tmp_path
    ex.projection=SimpleNamespace(root=tmp_path/'deep-output')
    identity=dict(media=kind,work_id='123',format=fmt,author_id='91',sha256='a'*64,account='91',mode='direct',use_work_folder=False,reference_layout=1)
    manifest=tmp_path/'copy.export.json'
    subject.export_media(ex,identity,Context(),manifest)
    target=native_io_path(ex.projection.root)/subject.destinations(identity,'Book','Author',relative)[0]
    if kind=='novel':
        txt=target.read_bytes()
        assert txt.startswith(b'\xef\xbb\xbf') and b'\r\n' in txt
        import re
        reference=re.search(r'\[挿絵: ([^\]]+)\]',txt.decode('utf-8')).group(1)
        assert 'untouched images/unknown.png' in txt.decode('utf-8')
    else:
        value=json.loads(target.read_bytes())
        reference=value['frames'][0]['file'];assert len(value['frames'])==1 and value['frames'][0]['delay']==90
    resolved=target.parent/reference
    assert resolved.is_file() and resolved.read_bytes()==original_asset
    assert main.read_bytes()==original_main and asset.read_bytes()==original_asset
    assert not __import__('os').path.samefile(resolved,asset)
    records=json.loads(manifest.read_bytes())['files']
    record=next(r for r in records if Path(r['path']).name==target.name)
    assert record['source_sha256']==sha256(original_main).hexdigest()
    assert record['sha256']==sha256(target.read_bytes()).hexdigest()
    assert record['source_sha256']!=record['sha256']
    # Reuse is validated against the derived bytes, not against the CAS bytes.
    subject.export_media(ex,identity,Context(),manifest)
    target.write_bytes(b'user changed projection')
    with pytest.raises(ValueError,match='changed'):subject.export_media(ex,identity,Context(),manifest)
    assert main.read_bytes()==original_main and asset.read_bytes()==original_asset


def test_link_and_preexisting_identity_do_not_rewrite_content():
    source=dict(title='Book',author='Author',files=[])
    file=dict(relative='Book(123).txt')
    raw=b'unchanged bytes'
    assert subject.projection_data({'mode':'link'},source,file,raw)==raw
    assert subject.projection_data({'mode':'direct'},source,file,raw)==raw


def test_real_saved_both_novel_resolves_direct_image_from_projection(tmp_path):
    from fastapi import FastAPI
    from tools.p3_residuals_fixtures import create_services,PREFIX
    from test_bookmark_media_projection import save,finish
    service,reader=create_services(FastAPI(),tmp_path/'data')
    original=reader.get_novel_payload
    def payload(wid,aid):
        value=original(wid,aid)
        return dict(value,content='本文[uploadedimage:1]',textEmbeddedImages={'1':{'urls':{'original':PREFIX+'asset.png'}}})
    reader.get_novel_payload=payload
    try:
        save(service,'novel','BOTH')
        service.configure_save_exports(tmp_path/'exports','91');ex=service.save_exports
        before=subject.snapshot(service,'novel','123','BOTH')
        calls=list(reader.calls)
        job=finish(ex.jobs,ex.enqueue_media('novel','123','BOTH','91'))
        manifest=json.loads((ex.root/job.result).read_bytes())
        txt=next(file for file in before['files'] if file['relative'].endswith('.txt'))
        text_target=native_io_path(ex.projection.root)/subject.destinations(manifest['source'],before['title'],before['author'],txt['relative'])[0]
        import re
        reference=re.search(r'\[挿絵: ([^\]]+)\]',text_target.read_bytes().decode('utf-8')).group(1)
        image=next(file for file in before['files'] if file['relative'].startswith('images/'))
        assert (text_target.parent/reference).read_bytes()==native_io_path(image['path']).read_bytes()
        assert native_io_path(txt['path']).read_bytes().decode('utf-8').find('images/')>=0
        epub=next(file for file in before['files'] if file['relative'].endswith('.epub'))
        epub_target=native_io_path(ex.projection.root)/subject.destinations(manifest['source'],before['title'],before['author'],epub['relative'])[0]
        assert epub_target.read_bytes()==native_io_path(epub['path']).read_bytes()
        assert reader.calls==calls
    finally:service.close()
