from hashlib import sha256
from zipfile import ZipFile
import pytest
from test_u11_zip_contract import generated_exports, settled
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
        relative, _, digest = f.managed.saved_receipts()[0]
        original_page = f.managed.read_saved_page(relative, 0, digest)
        with ZipFile(archive, 'w') as z:
            z.writestr('synthetic title_1.png', original_page)
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


def test_ambiguous_old_name_with_two_same_title_works_fails_closed(tmp_path):
    from dataclasses import replace
    from types import SimpleNamespace
    from test_managed_downloads import Context, record
    with generated_exports(tmp_path, True, None) as q:
        exporter = q.exports.zip
        path = exporter.export_author('456')
        with ZipFile(path, 'w') as z:
            z.writestr('synthetic title_1.png', b'old')
        before = path.read_bytes()
        q.managed.resolve_record = lambda wid: replace(record(), remote_id=wid)
        q.managed.run(SimpleNamespace(kind='pixiv-illust', target_id='124'), Context())
        with pytest.raises(LegacyZipExportError):
            exporter.export_author('456', replace_existing_sha256=sha256(before).hexdigest())
        assert path.read_bytes() == before


def test_changed_managed_page_is_updated(tmp_path):
    with generated_exports(tmp_path, True, None) as q:
        exporter = q.exports.zip
        path = exporter.export_author('456')
        with ZipFile(path) as z:
            names = z.namelist()
            second = z.read(names[1])
        with ZipFile(path, 'w') as z:
            z.writestr(names[0], b'different')
            z.writestr(names[1], second)
        before = path.read_bytes()
        exporter.export_author('456', replace_existing_sha256=sha256(before).hexdigest())
        relative, _, digest = q.managed.saved_receipts()[0]
        with ZipFile(path) as z:
            assert z.read(names[0]) == q.managed.read_saved_page(relative, 0, digest)
            assert z.read(names[1]) == second


def test_excluded_page_does_not_remain_in_successful_zip(tmp_path, monkeypatch):
    from dataclasses import replace
    with generated_exports(tmp_path, True, None) as q:
        exporter = q.exports.zip
        path = exporter.export_author('456')
        before = path.read_bytes()
        original = q.managed.catalog_snapshot
        def filtered(*args, **kwargs):
            snapshot = original(*args, **kwargs)
            return replace(snapshot, source_pages={'123': (1,)})
        monkeypatch.setattr(q.managed, 'catalog_snapshot', filtered)
        exporter.export_author('456', replace_existing_sha256=sha256(before).hexdigest())
        with ZipFile(path) as z:
            assert z.namelist() == ['synthetic title(123)_2.png'] and z.testzip() is None


def test_unmatched_old_title_is_preserved(tmp_path):
    with generated_exports(tmp_path, True, None) as q:
        path = q.exports.zip.export_author('456')
        with ZipFile(path, 'a') as z: z.writestr('old title(123)_1.png', b'old')
        before = path.read_bytes()
        q.exports.zip.export_author('456', replace_existing_sha256=sha256(before).hexdigest())
        with ZipFile(path) as z:
            assert z.read('old title(123)_1.png') == b'old'


def test_two_works_cannot_claim_same_legacy_member(tmp_path):
    from dataclasses import replace
    from types import SimpleNamespace
    from test_managed_downloads import Context, record
    with generated_exports(tmp_path, True, None) as q:
        q.exports.scan()
        assert all(j.state == 'done' for j in settled(q.exports))
        path = q.exports.zip.root / 'tester(456).zip'
        before = path.read_bytes()
        q.reader.get_artwork_original_pages = lambda wid: ('https://i.pximg.net/a',)
        q.managed.resolve_record = lambda wid: replace(record(), remote_id=wid,
                                                        title='synthetic title(123)_2', page_count=1)
        q.managed.run(SimpleNamespace(kind='pixiv-illust', target_id='124'), Context())
        q.exports.scan()
        assert any(j.state == 'error' for j in settled(q.exports))
        assert path.read_bytes() == before


def test_canonical_members_take_precedence_over_weak_alias(tmp_path):
    from dataclasses import replace
    from types import SimpleNamespace
    from test_managed_downloads import Context, record
    with generated_exports(tmp_path, True, None) as q:
        q.reader.get_artwork_original_pages = lambda wid: ('https://i.pximg.net/a',)
        q.managed.resolve_record = lambda wid: replace(record(), remote_id=wid,
                                                        title='synthetic title(123)_2', page_count=1)
        q.managed.run(SimpleNamespace(kind='pixiv-illust', target_id='124'), Context())
        q.exports.scan()
        assert all(j.state == 'done' for j in settled(q.exports))
        q.managed.resolve_record = lambda wid: replace(record(), remote_id=wid, title='another', page_count=1)
        q.managed.run(SimpleNamespace(kind='pixiv-illust', target_id='125'), Context())
        q.exports.scan()
        assert all(j.state == 'done' for j in settled(q.exports))
        with ZipFile(q.exports.zip.root / 'tester(456).zip') as z:
            assert len(z.namelist()) == 4
