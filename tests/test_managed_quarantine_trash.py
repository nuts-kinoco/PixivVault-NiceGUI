"""X09 trash overview uses only newly owned synthetic quarantine data."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from pixivvault_nicegui.desktop_integration import DesktopIntegration
from pixivvault_nicegui.fs_paths import logical_path
from pixivvault_nicegui.managed_downloads import ManagedDownloadError
from pixivvault_nicegui.services import ApplicationServices

from test_managed_quarantine import owned, plan_for, proof_bytes


def tree_bytes(root):
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob("*") if p.is_file()}


def copy_files(writer):
    return tuple((writer.root / "quarantine" / "operations").rglob("*.png"))


def test_uncreated_trash_is_distinct_from_ready_zero_and_never_prepares(owned, monkeypatch):
    writer, source, remote = owned
    before, calls = tree_bytes(writer.root), tuple(remote.images)
    monkeypatch.setattr(writer.quarantine, "_prepare",
                        lambda: pytest.fail("opening X09 must not prepare storage"))

    snapshot = source.quarantine_snapshot()
    assert snapshot.state == "uncreated"
    assert snapshot.copy_count == snapshot.copy_bytes == snapshot.excluded_pages == 0
    assert snapshot.location is None and source.quarantine_location() is None
    assert tree_bytes(writer.root) == before and tuple(remote.images) == calls


def test_publication_copies_and_active_exclusions_are_distinct_read_only_totals(owned):
    writer, source, remote = owned
    committed = source.execute(plan_for(source, "123:2"), operation_id="a" * 32)
    copies = copy_files(writer)
    assert len(copies) == 1
    # Incomplete material is neither a published copy nor a completed exclusion.
    (writer.quarantine.incomplete / "not-published.bin").write_bytes(b"fixture")
    snapshot = source.quarantine_snapshot()
    assert (snapshot.state, snapshot.copy_count, snapshot.copy_bytes,
            snapshot.excluded_pages) == ("ready", 1, copies[0].stat().st_size, 1)
    assert logical_path(snapshot.location) == logical_path(writer.quarantine.root)
    assert logical_path(source.quarantine_location()) == logical_path(snapshot.location)

    # Existing recovery core keeps the physical copy; X09 adds no restore action.
    writer.quarantine.restore(committed.operation_id,
                              expected_revision=committed.state_revision)
    before, calls = tree_bytes(writer.root), tuple(remote.images)
    restored = source.quarantine_snapshot()
    assert restored.copy_count == 1 and restored.copy_bytes == snapshot.copy_bytes
    assert restored.excluded_pages == 0
    assert restored.state_revision != snapshot.state_revision
    assert tree_bytes(writer.root) == before and tuple(remote.images) == calls


def test_absent_copy_is_ready_zero_and_does_not_remove_the_exclusion(owned):
    writer, source, remote = owned
    source.execute(plan_for(source, "123:2"), operation_id="b" * 32)
    original_proofs = proof_bytes(writer)
    for copy in copy_files(writer):
        copy.unlink()  # Only this test's own quarantine copy, never a source image.
    before, calls = tree_bytes(writer.root), tuple(remote.images)
    snapshot = source.quarantine_snapshot()
    assert snapshot.state == "ready" and snapshot.location is not None
    assert snapshot.copy_count == snapshot.copy_bytes == 0
    assert snapshot.excluded_pages == 1
    assert proof_bytes(writer) == original_proofs
    assert tree_bytes(writer.root) == before and tuple(remote.images) == calls


def test_partial_copy_counts_only_the_published_success(owned, monkeypatch):
    writer, source, _ = owned
    original = writer.quarantine._copy_page

    def fail_one(item, destination):
        if item.work_id == "223":
            raise OSError("synthetic copy failure")
        return original(item, destination)

    monkeypatch.setattr(writer.quarantine, "_copy_page", fail_one)
    result = source.execute(plan_for(source, "123:2", "223:1"), operation_id="c" * 32)
    assert len(result.committed) == len(result.failed) == 1
    snapshot = source.quarantine_snapshot()
    assert snapshot.copy_count == snapshot.excluded_pages == 1
    assert snapshot.copy_bytes == sum(p.stat().st_size for p in copy_files(writer))


def test_corrupt_manifest_is_read_failure_not_an_empty_trash(owned):
    writer, source, _ = owned
    source.execute(plan_for(source, "123:2"), operation_id="d" * 32)
    manifest = writer.quarantine.operations / ("d" * 32) / "manifest.json"
    manifest.write_bytes(manifest.read_bytes() + b" ")
    with pytest.raises(ManagedDownloadError):
        source.quarantine_snapshot()


def test_changed_publication_during_read_never_returns_mixed_counts(owned, monkeypatch):
    writer, source, _ = owned
    result = source.execute(plan_for(source, "123:2"), operation_id="e" * 32)
    original = writer.quarantine._manifest
    changed = []

    def read_then_restore(operation_id, operation):
        manifest = original(operation_id, operation)
        if not changed:
            changed.append(True)
            writer._generations.restore_quarantine(
                result.operation_id, expected_revision=result.state_revision)
        return manifest

    monkeypatch.setattr(writer.quarantine, "_manifest", read_then_restore)
    with pytest.raises(ManagedDownloadError, match="changed"):
        source.quarantine_snapshot()
    assert writer.visible_source_pages("123") == (0, 1, 2)


def test_reparse_location_is_never_opened_or_counted(owned, monkeypatch):
    writer, source, _ = owned
    source.execute(plan_for(source, "123:2"), operation_id="f" * 32)
    actual = Path.lstat
    target = logical_path(writer.quarantine.root)

    def reported(path, *args, **kwargs):
        info = actual(path, *args, **kwargs)
        if logical_path(path) == target:
            return SimpleNamespace(st_mode=info.st_mode, st_nlink=info.st_nlink,
                st_size=info.st_size, st_file_attributes=0x400)
        return info

    monkeypatch.setattr(Path, "lstat", reported)
    with pytest.raises(ManagedDownloadError):
        source.quarantine_location()
    with pytest.raises(ManagedDownloadError):
        source.quarantine_snapshot()


@pytest.mark.asyncio
async def test_os_open_revalidates_fixed_owned_location_without_a_ui_path(owned):
    writer, source, _ = owned
    source.execute(plan_for(source, "123:2"), operation_id="1" * 32)
    opened = []
    desktop = DesktopIntegration(allowed_roots=[writer.root],
        launcher=SimpleNamespace(open_default=lambda path: opened.append(logical_path(path))))
    services = SimpleNamespace(closing=False, duplicate_inspection=source, desktop=desktop)
    before = tree_bytes(writer.root)

    await ApplicationServices.open_duplicate_quarantine(services)
    assert opened == [logical_path(writer.quarantine.root)]
    assert tree_bytes(writer.root) == before

    marker = writer.quarantine.root / ".pixivvault-managed-quarantine.json"
    marker.write_bytes(b"{}")
    with pytest.raises(ManagedDownloadError):
        await ApplicationServices.open_duplicate_quarantine(services)
    assert len(opened) == 1
    services.closing = True
    with pytest.raises(RuntimeError):
        await ApplicationServices.open_duplicate_quarantine(services)
    assert len(opened) == 1
