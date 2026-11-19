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


def test_cleanup_failure_releases_writer(tmp_path, monkeypatch):
    from pathlib import Path
    with generated_exports(tmp_path, True, None) as q:
        exporter = q.exports.zip
        path = exporter.export_author('456')
        with ZipFile(path) as z: name = z.namelist()[0]
        unlink = Path.unlink
        def fail_unlink(self, *args, **kwargs):
            if self.name.endswith('.tmp'): raise OSError('injected cleanup error')
            return unlink(self, *args, **kwargs)
        def fail_replace(*args): raise OSError('injected replace error')
        monkeypatch.setattr(Path, 'unlink', fail_unlink)
        monkeypatch.setattr('pixivvault_nicegui.legacy_zip_export.os.replace', fail_replace)
        with pytest.raises((LegacyZipExportError, OSError)):
            exporter.remove_entry(path, name, sha256(path.read_bytes()).hexdigest())
        assert exporter._writer.acquire(blocking=False)
        exporter._writer.release()


def test_removal_preserves_archive_comment(tmp_path):
    with generated_exports(tmp_path, True, None) as q:
        path = q.exports.zip.export_author('456')
        with ZipFile(path, 'a') as z:
            z.comment = b'archive comment'
            name = z.namelist()[0]
        q.exports.zip.remove_entry(path, name, sha256(path.read_bytes()).hexdigest())
        with ZipFile(path) as z: assert z.comment == b'archive comment'
