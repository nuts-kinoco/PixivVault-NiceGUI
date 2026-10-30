"""Deep ZIP lifecycle with real synthetic managed receipts; CoS executes these."""
from pathlib import Path
import errno
import os
from zipfile import ZipFile

import pytest
from pixivvault_nicegui.fs_paths import native_io_path, logical_path
from pixivvault_nicegui import legacy_zip_export as module
from test_legacy_zip_export import writer


def deep_root(tmp_path):
    root = tmp_path / 'dedicated'
    while len(str(root)) < 270:
        root /= 'owned-export-segment'
    native_io_path(root.parent).mkdir(parents=True, exist_ok=True)
    return root


def native(path):
    if os.name == 'nt':
        assert str(path).startswith(chr(92) * 2 + '?' + chr(92))


def test_deep_marker_export_reopen_and_existing_author_alias_refusal(tmp_path, monkeypatch):
    remote, managed = writer(native_io_path(tmp_path))
    root = deep_root(tmp_path)
    before = managed.saved_receipts()
    calls = remote.remote_requests
    writes, links = [], []
    real_write, real_link = module._write, module.os.link
    def write(path, data):
        native(path);writes.append(path)
        return real_write(path, data)
    def link(source, target):
        native(source);native(target);links.append((source, target))
        return real_link(source, target)
    monkeypatch.setattr(module, '_write', write)
    monkeypatch.setattr(module.os, 'link', link)
    exporter = module.LegacyZipExporter(root, managed)
    try:
        assert exporter.root == root
        assert writes and len(str(logical_path(writes[0]))) > 260
        archive = exporter.export_author('23')
        assert archive.parent == root and logical_path(archive) == archive
        payload = native_io_path(archive).read_bytes()
        with ZipFile(native_io_path(archive)) as zipfile:
            assert zipfile.testzip() is None
            assert len(zipfile.namelist()) == 3
        assert len(links) == 1 and not tuple(native_io_path(exporter.incomplete).iterdir())
        exporter.close()
        exporter = module.LegacyZipExporter(root, managed)
        with pytest.raises(module.LegacyZipExportError, match='already exported'):
            exporter.export_author('23')
        earlier = native_io_path(root / 'Earlier name(24).zip')
        earlier.write_bytes(b'foreign existing archive')
        with pytest.raises(module.LegacyZipExportError, match='already exported'):
            exporter.export_author('24')
        assert earlier.read_bytes() == b'foreign existing archive'
        assert native_io_path(archive).read_bytes() == payload
        assert managed.saved_receipts() == before and remote.remote_requests == calls
    finally:
        exporter.close();managed.close()


@pytest.mark.parametrize('fault', ['publish', 'post_publish_fsync', 'cleanup'])
def test_deep_publication_faults_recover_only_owned_stage(tmp_path, monkeypatch, fault):
    _, managed = writer(native_io_path(tmp_path))
    root = deep_root(tmp_path)
    exporter = module.LegacyZipExporter(root, managed)
    receipts = managed.saved_receipts()
    calls, fired = [], []
    real_link, real_fsync, real_unlink = module.os.link, module._fsync_dir, Path.unlink
    def link(source, target):
        calls.append('publish');native(source);native(target)
        if fault == 'publish':
            fired.append('publish');raise OSError(errno.EIO, 'synthetic')
        return real_link(source, target)
    def fsync(path):
        calls.append('fsync');native(path)
        if fault == 'post_publish_fsync':
            fired.append('fsync');raise OSError(errno.EIO, 'synthetic')
        return real_fsync(path)
    def unlink(path, *args, **kwargs):
        if logical_path(path.parent) == exporter.incomplete:
            calls.append('cleanup');native(path)
            if fault == 'cleanup':
                fired.append('cleanup');raise OSError(errno.EIO, 'synthetic')
        return real_unlink(path, *args, **kwargs)
    try:
        with monkeypatch.context() as patcher:
            patcher.setattr(module.os, 'link', link)
            patcher.setattr(module, '_fsync_dir', fsync)
            patcher.setattr(Path, 'unlink', unlink)
            if fault == 'cleanup':
                exporter.export_author('23')
            else:
                with pytest.raises(module.LegacyZipExportError, match='export failed'):
                    exporter.export_author('23')
        assert calls and len(fired) == 1, 'the intended IO fault must actually fire'
        archives = tuple(native_io_path(root).glob('*.zip'))
        assert len(archives) == (0 if fault == 'publish' else 1)
        original = archives[0].read_bytes() if archives else None
        stages = tuple(native_io_path(exporter.incomplete).iterdir())
        assert len(stages) == (1 if fault == 'cleanup' else 0)
        exporter.close()
        exporter = module.LegacyZipExporter(root, managed)
        assert not tuple(native_io_path(exporter.incomplete).iterdir())
        if archives:
            assert archives[0].read_bytes() == original and archives[0].stat().st_nlink == 1
            with pytest.raises(module.LegacyZipExportError, match='already exported'):
                exporter.export_author('23')
        else:
            assert native_io_path(exporter.export_author('23')).is_file()
        assert managed.saved_receipts() == receipts
    finally:
        exporter.close();managed.close()


@pytest.mark.parametrize('partial', [False, True])
def test_deep_marker_interruption_does_not_claim_partial_root(tmp_path, monkeypatch, partial):
    _, managed = writer(native_io_path(tmp_path))
    root = deep_root(tmp_path)
    real_write = module._write
    calls, fired = [], []
    def write(path, data):
        calls.append(path);native(path)
        if logical_path(path) == root / module.EXPORT_MARKER:
            fired.append(path)
            if partial: real_write(path, data[:5])
            raise OSError(errno.EIO, 'synthetic marker interruption')
        return real_write(path, data)
    try:
        with monkeypatch.context() as patcher:
            patcher.setattr(module, '_write', write)
            with pytest.raises(OSError): module.LegacyZipExporter(root, managed)
        assert calls and len(fired) == 1
        unknown = native_io_path(root / 'preserve.bin');unknown.write_bytes(b'unknown data')
        with pytest.raises(module.LegacyZipExportError): module.LegacyZipExporter(root, managed)
        assert unknown.read_bytes() == b'unknown data'
        marker = native_io_path(root / module.EXPORT_MARKER)
        if partial:
            assert len(marker.read_bytes()) == 5
        else:
            assert not marker.exists()
    finally: managed.close()


def test_deep_unknown_incomplete_file_is_not_removed(tmp_path):
    _, managed = writer(native_io_path(tmp_path))
    exporter = module.LegacyZipExporter(deep_root(tmp_path), managed)
    unknown = native_io_path(exporter.incomplete / 'unknown.bin')
    unknown.write_bytes(b'keep')
    exporter.close()
    try:
        with pytest.raises(module.LegacyZipExportError, match='unsafe incomplete'):
            module.LegacyZipExporter(exporter.root, managed)
        assert unknown.read_bytes() == b'keep'
        with pytest.raises(module.LegacyZipExportError, match='overlaps'):
            module.LegacyZipExporter(logical_path(managed.root) / 'forbidden', managed)
    finally: managed.close()
