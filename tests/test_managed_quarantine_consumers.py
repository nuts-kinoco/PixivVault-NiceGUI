"""Quarantine must remain effective through real catalog/save/export consumers."""
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from pixivvault_nicegui.library_media_registry import LibraryImageRegistry
from pixivvault_nicegui.local_media_adapter import MediaMissing, MediaUnavailable
from pixivvault_nicegui.managed_download_catalog import build_managed_library
from pixivvault_nicegui.managed_downloads import ManagedPageSelectionChanged
from pixivvault_nicegui.legacy_zip_export import LegacyZipExporter, LegacyZipExportError, legacy_filename
from pixivvault_nicegui.viewer_save import ViewerSave
from test_managed_quarantine import owned, plan_for, proof_bytes


def row_for(writer, work_id='123'):
    return next(row for row in writer.saved_receipts() if row[1]['work_id'] == work_id)


def quarantine(source, *keys, operation='8' * 32):
    return source.execute(plan_for(source, *keys), operation_id=operation)


def test_catalog_pins_visible_to_original_pages_and_revokes_warm_old_cache(owned):
    writer, source, remote = owned
    relative, receipt, proof = row_for(writer)
    expected = writer.read_saved_page(relative, 2, proof)
    _, old, _ = build_managed_library(writer)
    old.get('123', 1, 'original')
    assert old.peek('123', 1, 'original') is not None
    calls = tuple(remote.images)
    current = None
    try:
        quarantine(source, '123:2')
        library, current, _ = build_managed_library(writer)
        assert current.source_pages['123'] == (0, 2)
        with pytest.raises(TypeError):
            current.source_pages['123'] = (0, 1, 2)
        assert next(work for work in library.works if work.work_id == '123').page_count == 2
        assert current.get('123', 1, 'original').data == expected
        assert current.external_target('123', 1).name == receipt['files'][2]['name']
        identity = current.source_page_identity('123', 1)
        assert (identity.source_page_no, identity.source_page_count) == (2, 3)
        assert (identity.relative_receipt, identity.receipt_sha256) == (relative, proof)
        with pytest.raises(MediaUnavailable):
            old.get('123', 1, 'original')
        with pytest.raises(MediaUnavailable):
            old.peek('123', 1, 'original')
        assert old.source_pages['123'] == (0, 1, 2), 'old pins must never silently remap'
        assert tuple(remote.images) == calls
    finally:
        old.close()
        if current is not None:
            current.close()


def test_full_quarantine_tombstone_blocks_remote_and_demo_fallback(owned):
    writer, source, _ = owned
    quarantine(source, '123:1', '123:2', '123:3')
    library, local, _ = build_managed_library(writer)
    assert '123' in local.source_pages and local.source_pages['123'] == ()
    assert '123' not in {work.work_id for work in library.works}
    class ForbiddenProvider:
        works = {'123': object()}
        def __init__(self):
            self.calls = 0
        def url(self, *args):
            self.calls += 1
            raise AssertionError('excluded managed identity fell back to another provider')
        get = url
        def close(self):
            pass
    demo, remote = ForbiddenProvider(), ForbiddenProvider()
    registry = LibraryImageRegistry(demo)
    pin = None
    try:
        registry.publish(local, remote)
        pin = registry.acquire()
        assert not pin.can_open('123')
        with pytest.raises(MediaMissing):
            pin.get('123', 0, 'original')
        assert (demo.calls, remote.calls) == (0, 0)
    finally:
        if pin is not None:
            pin.close()
        registry.close()


@pytest.mark.asyncio
async def test_individual_save_receives_original_page_and_full_original_count(owned):
    writer, source, remote = owned
    quarantine(source, '123:2')
    _, catalog, _ = build_managed_library(writer)
    identity = catalog.source_page_identity('123', 1)
    before, calls = proof_bytes(writer), tuple(remote.images)
    requests = []
    async def save(wid, page, count, aid, *, is_current, source_identity=None):
        assert is_current()
        requests.append((wid, page, count, aid, source_identity))
        return writer.save_pinned_page(source_identity)
    services = SimpleNamespace(managed_downloads=writer, save_viewer_page=save)
    try:
        result = await ViewerSave(services).save_page('123', 1, 2, '24', lambda: True,
                                                     source_identity=identity)
        assert requests == [('123', 2, 3, '24', identity)]
        assert result.page_no == 2 and result.source_page_count == 3 and result.reused
        assert Path(result.relative_image).name == 'page-3.png'
        assert (writer.root / result.relative_image).read_bytes() == catalog.get('123', 1, 'original').data
        assert tuple(remote.images) == calls and proof_bytes(writer) == before
        assert not (writer.root / 'viewer-pages').exists()
    finally:
        catalog.close()


def test_old_pin_requests_reload_instead_of_substituting_a_new_visible_page(owned):
    writer, source, remote = owned
    _, old, _ = build_managed_library(writer)
    identity = old.source_page_identity('123', 2)
    calls = tuple(remote.images)
    current = None
    try:
        quarantine(source, '123:2')
        with pytest.raises(ManagedPageSelectionChanged, match='snapshot changed'):
            writer.save_pinned_page(identity)
        _, current, _ = build_managed_library(writer)
        refreshed = current.source_page_identity('123', 1)
        result = writer.save_pinned_page(refreshed)
        assert result.page_no == 2 and result.source_page_count == 3
        assert tuple(remote.images) == calls
    finally:
        old.close()
        if current is not None:
            current.close()


def test_legacy_zip_exports_only_visible_source_pages_with_original_filenames(owned, tmp_path):
    writer, source, remote = owned
    before, calls = proof_bytes(writer), tuple(remote.images)
    relative, row, proof = row_for(writer)
    expected = {legacy_filename(row['title'], '123', index, 3, 'png'):
                writer.read_saved_page(relative, index, proof) for index in (0, 2)}
    other_relative, other, other_proof = row_for(writer, '124')
    expected[legacy_filename(other['title'], '124', 0, 1, 'png')] = writer.read_saved_page(other_relative, 0, other_proof)
    quarantine(source, '123:2')
    exporter = LegacyZipExporter(tmp_path / 'zip-output', writer)
    try:
        path = exporter.export_author('24')
        with ZipFile(path) as archive:
            assert set(archive.namelist()) == set(expected)
            assert {name: archive.read(name) for name in archive.namelist()} == expected
        assert proof_bytes(writer) == before and tuple(remote.images) == calls
    finally:
        exporter.close()


def test_quarantine_during_zip_publication_prevents_a_stale_new_archive(owned, tmp_path):
    writer, source, _ = owned
    plan = plan_for(source, '123:2')
    exporter = LegacyZipExporter(tmp_path / 'zip-race', writer)
    invoked = []
    def before_publish(stage, destination):
        invoked.append((stage, destination))
        source.execute(plan, operation_id='9' * 32)
    try:
        with pytest.raises(LegacyZipExportError, match='changed during ZIP export'):
            exporter.export_author('24', before_publish=before_publish)
        assert len(invoked) == 1 and not invoked[0][1].exists()
        assert not list(exporter.root.glob('*.zip'))
        assert writer.visible_source_pages('123') == (0, 2)
    finally:
        exporter.close()
