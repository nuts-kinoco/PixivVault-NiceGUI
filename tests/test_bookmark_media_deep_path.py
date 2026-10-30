"""CoS deep Windows projection without shortening its physical location."""
from pathlib import Path
from hashlib import sha256
from types import SimpleNamespace
import pytest
from pixivvault_nicegui import bookmark_projection as subject
from pixivvault_nicegui.fs_paths import native_io_path,logical_path
from test_legacy_zip_export import Context


def test_deep_projection_copy_link_manifest_and_retry(tmp_path,monkeypatch):
    # Isolate projection IO; real saved-media owner coverage is in the other fixture.
    from pixivvault_nicegui.save_exports import SaveExports
    base=tmp_path
    while len(str(base))<230:base=base/'original-depth'
    source=tmp_path/'saved.epub';source.write_bytes(b'fixture EPUB bytes')
    data=source.read_bytes();sha=sha256(data).hexdigest()
    snap=dict(proof='a'*64,author_id='91',title='Book',author='Author',
        files=[dict(path=source,relative='Book(123).epub',sha256=sha,bytes=len(data))])
    monkeypatch.setattr(subject,'snapshot',lambda *a,**kw:snap)
    exports=SaveExports.__new__(SaveExports);exports.s=object();exports.root=tmp_path
    exports.projection=SimpleNamespace(root=base)
    identity=dict(media='novel',work_id='123',format='EPUB',author_id='91',sha256='a'*64,account='91',mode='link',use_work_folder=True)
    manifest=tmp_path/'projection.export.json'
    assert subject.export_media(exports,identity,Context(),manifest)==manifest.name
    assert subject.export_media(exports,identity,Context(),manifest)==manifest.name
    target=native_io_path(base)/'☆ブックマーク(91)'/'Author(91)'/'Book(123)'/'Book(123).epub'
    assert len(str(logical_path(target)))>260 and target.read_bytes()==data
    target.write_bytes(b'user edit')
    assert source.read_bytes()==data
    with pytest.raises(ValueError,match='changed'):subject.export_media(exports,identity,Context(),manifest)
