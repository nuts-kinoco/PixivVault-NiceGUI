from hashlib import sha256
import struct
from zipfile import ZipFile
import pytest
from test_u11_zip_contract import generated_exports
from pixivvault_nicegui.legacy_zip_export import LegacyZipExportError


def local_version(path):
    data = path.read_bytes()
    return struct.unpack_from('<H', data, 4)[0]


@pytest.mark.parametrize('threshold,expected64', [(1, True), (10**9, False)])
def test_normalize_rewrites_in_consistent_mode(tmp_path, threshold, expected64):
    with generated_exports(tmp_path, True, None) as q:
        path = q.exports.zip.export_author('456')
        with ZipFile(path, 'a') as z: z.writestr('dir\\test.txt', b'hello')
        q.exports.zip.normalize_archive(path, sha256(path.read_bytes()).hexdigest(), zip64_threshold=threshold)
        with ZipFile(path) as z:
            assert z.read('dir/test.txt') == b'hello'
            assert z.testzip() is None
        assert (local_version(path) >= 45) is expected64


def test_normalize_corrupt_archive_unchanged(tmp_path):
    with generated_exports(tmp_path, True, None) as q:
        path = q.exports.zip.export_author('456')
        path.write_bytes(b'broken')
        with pytest.raises(LegacyZipExportError):
            q.exports.zip.normalize_archive(path, sha256(b'broken').hexdigest())
        assert path.read_bytes() == b'broken'


def test_auto_zip_uses_zip64_policy_for_existing_large_archive(tmp_path, monkeypatch):
    import pixivvault_nicegui.legacy_zip_export as module
    with generated_exports(tmp_path, True, None) as q:
        path = q.exports.zip.export_author('456')
        monkeypatch.setattr(module, 'ZIP64_THRESHOLD', 1)
        q.exports.zip.export_author('456', replace_existing_sha256=sha256(path.read_bytes()).hexdigest())
        assert local_version(path) >= 45
        with ZipFile(path) as z: assert z.testzip() is None


def test_normalize_failed_replace_preserves_archive(tmp_path, monkeypatch):
    with generated_exports(tmp_path, True, None) as q:
        path = q.exports.zip.export_author('456')
        before = path.read_bytes()
        def fail(*args): raise OSError('injected')
        monkeypatch.setattr('pixivvault_nicegui.legacy_zip_export.os.replace', fail)
        with pytest.raises(LegacyZipExportError):
            q.exports.zip.normalize_archive(path, sha256(before).hexdigest(), zip64_threshold=1)
        assert path.read_bytes() == before
