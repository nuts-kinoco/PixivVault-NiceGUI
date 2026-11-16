"""An export preserves every old path while storing identical bytes only once."""
import hashlib
import json
import sqlite3
import zipfile
import pytest

from pixivvault_nicegui import legacy_metadata_export as exporter


def test_identical_images_share_one_payload_and_restore_both_paths(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    images = source / 'images'
    (images / 'author').mkdir(parents=True)
    original = b'synthetic-image-content'
    (images / 'first.png').write_bytes(original)
    (images / 'author' / 'second.png').write_bytes(original)
    db = source / 'legacy.db'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE works(work_id TEXT, title TEXT)')
    result = exporter.export_frozen_legacy_metadata(db, tmp_path / 'exports', image_root=images)
    with zipfile.ZipFile(result.path) as archive:
        images_in_zip = [name for name in archive.namelist() if name.startswith('images/')]
        assert len(images_in_zip) == 1
        manifest = json.loads(archive.read('image-paths.json'))
        group, = manifest['images']
        assert group['sha256'] == hashlib.sha256(original).hexdigest()
        assert set(group['paths']) == {'first.png', 'author/second.png'}
        assert archive.testzip() is None
    out = tmp_path / 'restored'
    exporter.restore_legacy_export(result.path, out)
    assert (out / 'images' / 'first.png').read_bytes() == original
    assert (out / 'images' / 'author' / 'second.png').read_bytes() == original
    assert (images / 'first.png').read_bytes() == original
    assert (images / 'author' / 'second.png').read_bytes() == original


def test_restore_rejects_existing_destination(tmp_path):
    destination = tmp_path / 'existing'
    destination.mkdir()
    marker = destination / 'keep.txt'
    marker.write_bytes(b'keep')
    with pytest.raises(exporter.LegacyMetadataExportError):
        exporter.restore_legacy_export(tmp_path / 'not-an-export.zip', destination)
    assert marker.read_bytes() == b'keep'


@pytest.mark.parametrize('bad_path', ['../outside.png', '/absolute.png', 'x/../../outside.png', 'C:/outside.png'])
def test_restore_rejects_unsafe_paths_before_publishing(tmp_path, bad_path):
    source = tmp_path / 'bad.zip'
    manifest = {'version': 1, 'images': [{'sha256': hashlib.sha256(b'x').hexdigest(),
        'size': 1, 'entry': 'images/good.png', 'paths': ['good.png', bad_path]}]}
    with zipfile.ZipFile(source, 'w') as archive:
        for name, data in [('pixiv_vault.db', b'db'), ('metadata.json', b'[]'),
                           ('image-paths.json', json.dumps(manifest)),
                           ('RESTORE_IMAGES.txt', b''), ('images/good.png', b'x')]:
            archive.writestr(name, data)
    out = tmp_path / 'restore'
    with pytest.raises(exporter.LegacyMetadataExportError):
        exporter.restore_legacy_export(source, out)
    assert not out.exists() and not (tmp_path / 'outside.png').exists()


def test_restore_rejects_image_hash_mismatch(tmp_path):
    source = tmp_path / 'bad.zip'
    manifest = {'version': 1, 'images': [{'sha256': '0' * 64,
        'size': 1, 'entry': 'images/a.png', 'paths': ['a.png', 'b.png']}]}
    with zipfile.ZipFile(source, 'w') as archive:
        for name, data in [('pixiv_vault.db', b'db'), ('metadata.json', b'[]'),
                           ('image-paths.json', json.dumps(manifest)),
                           ('RESTORE_IMAGES.txt', b''), ('images/a.png', b'x')]:
            archive.writestr(name, data)
    out = tmp_path / 'restore'
    with pytest.raises(exporter.LegacyMetadataExportError):
        exporter.restore_legacy_export(source, out)
    assert not out.exists()
