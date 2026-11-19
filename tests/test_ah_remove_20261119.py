from hashlib import sha256
from zipfile import ZipFile
import pytest
from test_u11_zip_contract import generated_exports
from pixivvault_nicegui.legacy_zip_export import LegacyZipExportError


def test_remove_entry_atomic_and_returns_bytes(tmp_path):
    with generated_exports(tmp_path, True, None) as q:
        exporter = q.exports.zip
        path = exporter.export_author('456')
        with ZipFile(path) as z:
            name = z.namelist()[0]
            expected = z.read(name)
        assert exporter.remove_entry(path, name, sha256(path.read_bytes()).hexdigest()) == expected
        with ZipFile(path) as z:
            assert name not in z.namelist() and len(z.namelist()) == 1 and z.testzip() is None


@pytest.mark.parametrize('fault', ['missing', 'corrupt', 'replace'])
def test_remove_entry_failure_keeps_original(tmp_path, monkeypatch, fault):
    with generated_exports(tmp_path, True, None) as q:
        exporter = q.exports.zip
        path = exporter.export_author('456')
        with ZipFile(path) as z: name = z.namelist()[0]
        if fault == 'corrupt': path.write_bytes(b'broken')
        if fault == 'missing': name = 'absent'
        before = path.read_bytes()
        if fault == 'replace':
            def fail(*args): raise OSError('injected')
            monkeypatch.setattr('pixivvault_nicegui.legacy_zip_export.os.replace', fail)
        with pytest.raises((LegacyZipExportError, ValueError)):
            exporter.remove_entry(path, name, sha256(before).hexdigest())
        assert path.read_bytes() == before
