import pytest
import os
import sqlite3
import hashlib
import json
import struct
import zlib
from zipfile import ZipFile, ZIP_DEFLATED
from io import BytesIO
from PIL import Image
from test_original_ledger_rebuild import fixture, store, run, hashes, png
from pixivvault_nicegui.legacy_ledger_rebuild import (
    LedgerRebuildError, publish_rebuilt_ledger, load_rebuilt_ledger, resolve_rebuilt_group as _resolve,
)
from test_original_scan_publication import opened
from nicegui import Client, ui
from pixivvault_nicegui.ui.duplicate_view import DuplicateView
from pixivvault_nicegui.duplicate_inspection import ManagedDuplicateInspection, DuplicateInspectionError
from pixivvault_nicegui.legacy_quarantine import LegacyQuarantine


def resolve_rebuilt_group(**kwargs):
    current = (kwargs['managed_root'] / 'legacy-ledger' / 'CURRENT.json').read_bytes()
    return _resolve(**kwargs, expected_current=current)


def publish(paths, result):
    db, images, managed = paths
    publish_rebuilt_ledger(result, managed_root=managed, undecidable_db=db, images_root=images)


def test_manual_resolution_is_durable_and_survives_rescan(tmp_path):
    paths = fixture(tmp_path); store(paths[1])
    before_db = paths[0].read_bytes(); before_images = hashes(paths[1])
    result = run(paths); publish(paths, result)
    group = result.groups[0]
    resolve_rebuilt_group(managed_root=paths[2], undecidable_db=paths[0], images_root=paths[1],
                          group=group, work_id='1')
    assert load_rebuilt_ledger(managed_root=paths[2], undecidable_db=paths[0], images_root=paths[1]) == ()
    again = run(paths); publish(paths, again)
    assert again.groups == ()
    assert paths[0].read_bytes() == before_db and hashes(paths[1]) == before_images


def test_stale_scan_cannot_overwrite_manual_resolution(tmp_path):
    paths = fixture(tmp_path); store(paths[1])
    result = run(paths); publish(paths, result)
    stale = run(paths)
    resolve_rebuilt_group(managed_root=paths[2], undecidable_db=paths[0], images_root=paths[1],
                          group=result.groups[0], work_id='1')
    with pytest.raises(LedgerRebuildError): publish(paths, stale)
    assert load_rebuilt_ledger(managed_root=paths[2], undecidable_db=paths[0], images_root=paths[1]) == ()


def test_stale_candidate_cannot_resolve_twice(tmp_path):
    paths = fixture(tmp_path); store(paths[1])
    result = run(paths); publish(paths, result)
    kwargs = dict(managed_root=paths[2], undecidable_db=paths[0], images_root=paths[1],
                  group=result.groups[0], work_id='1')
    resolve_rebuilt_group(**kwargs)
    with pytest.raises(LedgerRebuildError): resolve_rebuilt_group(**kwargs)


@pytest.mark.asyncio
async def test_real_view_resolution_persists_on_reopen(tmp_path):
    source, owner, legacy, db, snapshot = opened(tmp_path)
    client = Client(ui.page('/_af_resolve'))
    try:
        source.scan()
        with client:
            view = DuplicateView(source)
            view.set_active(True)
        await view._undecidable_task
        assert any(isinstance(node, ui.button) and 'ものにする' in node.text
                   for node in view.undecidable_rows.descendants())
        group = view.undecidable_groups[0]
        await view.resolve_undecidable(group, '1')
        assert source.undecidable_groups() == ()
        view.close()
        root = legacy.catalog.root
        source.close()
        reopened = ManagedDuplicateInspection(owner, legacy=LegacyQuarantine(root, snapshot, owner=owner),
                                             undecidable_db=db)
        try:
            assert reopened.undecidable_groups() == ()
            reopened.scan()
            assert reopened.undecidable_groups() == ()
        finally:
            reopened.close()
    finally:
        client.delete(); source.close(); owner.close()


def test_missing_selected_source_does_not_publish(tmp_path):
    paths = fixture(tmp_path); folder = store(paths[1])
    result = run(paths); publish(paths, result)
    (folder / '題名.png').unlink()
    with pytest.raises(LedgerRebuildError):
        resolve_rebuilt_group(managed_root=paths[2], undecidable_db=paths[0], images_root=paths[1],
                              group=result.groups[0], work_id='1')
    assert load_rebuilt_ledger(managed_root=paths[2], undecidable_db=paths[0],
                               images_root=paths[1]) == result.groups


def test_replaced_file_after_scan_does_not_publish(tmp_path):
    paths = fixture(tmp_path); folder = store(paths[1])
    result = run(paths); publish(paths, result)
    (folder / '題名.png').write_bytes(b'changed synthetic file')
    with pytest.raises(LedgerRebuildError):
        resolve_rebuilt_group(managed_root=paths[2], undecidable_db=paths[0], images_root=paths[1],
                              group=result.groups[0], work_id='1')
    assert load_rebuilt_ledger(managed_root=paths[2], undecidable_db=paths[0],
                               images_root=paths[1]) == result.groups


def test_same_size_restored_mtime_change_does_not_resolve(tmp_path):
    paths = fixture(tmp_path); folder = store(paths[1])
    result = run(paths); publish(paths, result)
    image = folder / '題名.png'
    info = image.stat()
    data = bytearray(image.read_bytes()); data[-1] ^= 1
    image.write_bytes(data)
    os.utime(image, ns=(info.st_atime_ns, info.st_mtime_ns))
    with pytest.raises(LedgerRebuildError):
        resolve_rebuilt_group(managed_root=paths[2], undecidable_db=paths[0], images_root=paths[1],
                              group=result.groups[0], work_id='1')


def test_unrelated_corrupt_archive_does_not_block_resolution(tmp_path):
    paths = fixture(tmp_path); store(paths[1])
    with sqlite3.connect(paths[0]) as db:
        db.execute("INSERT INTO works VALUES ('10','10','other',1,'illust',0)")
    (paths[1] / 'broken(10).zip').write_bytes(b'broken synthetic archive')
    result = run(paths); publish(paths, result)
    assert result.unreadable
    assert resolve_rebuilt_group(managed_root=paths[2], undecidable_db=paths[0], images_root=paths[1],
                                 group=result.groups[0], work_id='1') == ()


def test_zip_member_changed_with_restored_archive_mtime_does_not_resolve(tmp_path):
    paths = fixture(tmp_path); archive = store(paths[1], zipped=True)
    result = run(paths); publish(paths, result)
    info = archive.stat()
    data = bytearray(archive.read_bytes()); start = data.find(png())
    assert start >= 0
    data[start + len(png()) - 1] ^= 1
    crc = zlib.crc32(data[start:start + len(png())])
    local = data.find(b'PK\x03\x04'); central = data.find(b'PK\x01\x02')
    assert local >= 0 and central >= 0
    struct.pack_into('<I', data, local + 14, crc)
    struct.pack_into('<I', data, central + 16, crc)
    archive.write_bytes(data)
    os.utime(archive, ns=(info.st_atime_ns, info.st_mtime_ns))
    with ZipFile(archive) as check:
        assert check.testzip() is None
    with pytest.raises(LedgerRebuildError):
        resolve_rebuilt_group(managed_root=paths[2], undecidable_db=paths[0], images_root=paths[1],
                              group=result.groups[0], work_id='1')


def test_old_generation_remains_readable_but_requires_rescan(tmp_path):
    source, owner, legacy, db, snapshot = opened(tmp_path)
    try:
        source.scan()
        root = legacy.catalog.root
        parent = owner.root / 'legacy-ledger'
        ref_path = parent / 'CURRENT.json'
        ref = json.loads(ref_path.read_text(encoding='utf-8'))
        generation = parent / ref['generation'] / 'ledger.json'
        payload = json.loads(generation.read_text(encoding='utf-8'))
        payload.pop('page_proofs')
        data = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode()
        generation.write_bytes(data)
        ref['sha256'] = hashlib.sha256(data).hexdigest()
        ref_path.write_text(json.dumps(ref, sort_keys=True, separators=(',', ':')), encoding='utf-8')
        source.close()
        reopened = ManagedDuplicateInspection(owner, legacy=LegacyQuarantine(root, snapshot, owner=owner),
                                             undecidable_db=db)
        try:
            assert reopened.undecidable_groups()
            assert not reopened.can_resolve_undecidable
            reopened.scan()
            assert reopened.can_resolve_undecidable
        finally:
            reopened.close()
    finally:
        source.close(); owner.close()


def test_corrupt_deflate_of_another_author_does_not_block_scan(tmp_path):
    paths = fixture(tmp_path); store(paths[1])
    with sqlite3.connect(paths[0]) as db:
        db.executemany('INSERT INTO works VALUES (?,?,?,?,?,?)', [
            ('10','10','other',1,'illust',0), ('11','10','other',3,'illust',0)])
    archive = paths[1] / 'other(10).zip'
    with ZipFile(archive, 'w', ZIP_DEFLATED) as z:
        z.writestr('other.png', png())
    data = bytearray(archive.read_bytes())
    local = data.find(b'PK\x03\x04')
    name_len, extra_len = struct.unpack_from('<HH', data, local + 26)
    start = local + 30 + name_len + extra_len
    data[start] = (data[start] & 0xf8) | 7  # reserved DEFLATE block type
    archive.write_bytes(data)
    result = run(paths)
    assert [g['ambiguous_group'] for g in result.groups] == ['9:題名']
    assert result.unreadable
    publish(paths, result)


def test_corrupt_deflate_does_not_block_real_inspection_scan(tmp_path):
    source, owner, legacy, db, _ = opened(tmp_path)
    try:
        with sqlite3.connect(db) as conn:
            conn.executemany('INSERT INTO works VALUES (?,?,?,?,?,?)', [
                ('10','10','other',1,'illust',0), ('11','10','other',3,'illust',0)])
        archive = legacy.catalog.root / 'other(10).zip'
        with ZipFile(archive, 'w', ZIP_DEFLATED) as z:
            z.writestr('other.png', png())
        data = bytearray(archive.read_bytes())
        local = data.find(b'PK\x03\x04')
        name_len, extra_len = struct.unpack_from('<HH', data, local + 26)
        start = local + 30 + name_len + extra_len
        data[start] = (data[start] & 0xf8) | 7
        archive.write_bytes(data)
        source.scan()
        assert [group['ambiguous_group'] for group in source.undecidable_groups()] == ['9:題名']
    finally:
        source.close(); owner.close()


def test_stale_view_cannot_resolve_equal_group_after_rescan(tmp_path):
    source, owner, legacy, db, _ = opened(tmp_path)
    try:
        source.scan()
        stale = source.undecidable_groups()[0]
        image = next(legacy.catalog.root.rglob('題名.png'))
        out = BytesIO(); Image.new('RGB', (2,2), 'red').save(out, format='PNG')
        image.write_bytes(out.getvalue())
        source.scan()
        assert source.undecidable_groups()[0] == stale
        with pytest.raises(DuplicateInspectionError):
            source.resolve_undecidable(stale, '1')
        assert source.undecidable_groups()
    finally:
        source.close(); owner.close()


def test_other_source_rescan_invalidates_displayed_equal_group(tmp_path):
    source, owner, legacy, db, snapshot = opened(tmp_path)
    other = None
    try:
        source.scan()
        stale = source.undecidable_groups()[0]
        image = next(legacy.catalog.root.rglob('題名.png'))
        out = BytesIO(); Image.new('RGB', (2,2), 'red').save(out, format='PNG')
        image.write_bytes(out.getvalue())
        other = ManagedDuplicateInspection(owner, legacy=LegacyQuarantine(legacy.catalog.root, snapshot, owner=owner),
                                           undecidable_db=db)
        other.scan()
        assert other.undecidable_groups()[0] == stale
        with pytest.raises((DuplicateInspectionError, LedgerRebuildError)):
            source.resolve_undecidable(stale, '1')
    finally:
        if other: other.close()
        source.close(); owner.close()
