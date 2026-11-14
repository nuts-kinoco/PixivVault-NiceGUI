import hashlib
import json
import sqlite3
import pytest
from pixivvault_nicegui import legacy_metadata_export as product


def test_large_review_manifest_rejected_by_export_encoder():
    paths=['author/'+('a'*200)+f'-{i:06}.png' for i in range(80000)]
    groups=[{'sha256':hashlib.sha256(b'x').hexdigest(),'size':1,
             'entry':'images/'+paths[0],'paths':paths}]
    with pytest.raises(product.LegacyMetadataExportError, match='分割'):
        product._encode_image_paths(groups)


def test_export_over_limit_never_publishes_success_zip(tmp_path, monkeypatch):
    source=tmp_path/'source';source.mkdir()
    images=source/'images';images.mkdir()
    (images/'first.png').write_bytes(b'x')
    (images/'second.png').write_bytes(b'x')
    db=source/'legacy.db'
    connection=sqlite3.connect(db)
    try:
        connection.execute('CREATE TABLE works(work_id TEXT PRIMARY KEY,title TEXT)')
        connection.commit()
    finally:connection.close()
    before=db.read_bytes()
    saved=product.export_frozen_legacy_metadata(db,tmp_path/'exports',image_root=images)
    saved_bytes=saved.path.read_bytes()
    monkeypatch.setattr(product,'MAX_IMAGE_PATHS_BYTES',128,raising=False)
    with pytest.raises(product.LegacyMetadataExportError, match='分割'):
        product.export_frozen_legacy_metadata(db,tmp_path/'exports',image_root=images)
    assert list((tmp_path/'exports').glob('*.zip'))==[saved.path]
    assert saved.path.read_bytes()==saved_bytes
    assert not list((tmp_path/'exports').glob('.pv-export-*'))
    assert db.read_bytes()==before
    assert all(p.read_bytes()==b'x' for p in images.iterdir())


def test_manifest_utf8_limit_is_inclusive(monkeypatch):
    groups=[{'paths':['日本語.png']}]
    expected=json.dumps({'version':1,'images':groups},ensure_ascii=False,
                        sort_keys=True,separators=(',',':')).encode('utf8')
    monkeypatch.setattr(product,'MAX_IMAGE_PATHS_BYTES',len(expected))
    assert product._encode_image_paths(groups)==expected
    monkeypatch.setattr(product,'MAX_IMAGE_PATHS_BYTES',len(expected)-1)
    with pytest.raises(product.LegacyMetadataExportError,match='分割'):
        product._encode_image_paths(groups)
