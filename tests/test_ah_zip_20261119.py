from hashlib import sha256
from zipfile import ZipFile
import pytest
from test_u11_zip_contract import generated_exports
from pixivvault_nicegui.legacy_zip_export import LegacyZipExportError


def test_update_preserves_existing_members_and_is_idempotent(tmp_path):
    with generated_exports(tmp_path, True, None) as f:
        exporter = f.exports.zip
        archive = exporter.export_author('456')
        with ZipFile(archive, 'a') as z:
            z.writestr('old.txt', b'old')
        for _ in range(2):
            proof = sha256(archive.read_bytes()).hexdigest()
            exporter.export_author('456', replace_existing_sha256=proof)
        with ZipFile(archive) as z:
            assert z.read('old.txt') == b'old'
            assert len(z.namelist()) == len(set(z.namelist()))


def test_corrupt_previous_archive_is_not_replaced(tmp_path):
    with generated_exports(tmp_path, True, None) as f:
        exporter = f.exports.zip
        archive = exporter.export_author('456')
        original = b'not a zip'
        archive.write_bytes(original)
        with pytest.raises(LegacyZipExportError):
            exporter.export_author('456', replace_existing_sha256=sha256(original).hexdigest())
        assert archive.read_bytes() == original


def test_legacy_names_and_backslash_are_preserved_without_duplicates(tmp_path):
    with generated_exports(tmp_path, True, None) as f:
        exporter = f.exports.zip
        archive = exporter.export_author('456')
        with ZipFile(archive, 'w') as z:
            z.writestr('synthetic title_1.png', b'old page')
            z.writestr('folder\\old.txt', b'old')
        exporter.export_author('456', replace_existing_sha256=sha256(archive.read_bytes()).hexdigest())
        with ZipFile(archive) as z:
            assert z.read('folder/old.txt') == b'old'
            assert 'synthetic title_1.png' in z.namelist()
            assert 'synthetic title(123)_1.png' not in z.namelist()


def test_replace_failure_keeps_old_bytes(tmp_path, monkeypatch):
    with generated_exports(tmp_path, True, None) as f:
        exporter = f.exports.zip
        archive = exporter.export_author('456')
        original = archive.read_bytes()
        def fail(*args):
            raise OSError('injected')
        monkeypatch.setattr('pixivvault_nicegui.legacy_zip_export.os.replace', fail)
        with pytest.raises(LegacyZipExportError):
            exporter.export_author('456', replace_existing_sha256=sha256(original).hexdigest())
        assert archive.read_bytes() == original
