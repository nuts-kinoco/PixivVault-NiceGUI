"""E01 DB-only original metadata export, isolated synthetic SQLite and ZIP."""
import hashlib
import json
import sqlite3
import zipfile

import pytest

from pixivvault_nicegui import legacy_metadata_export as subject


def fixture_db(tmp_path):
    source = tmp_path / 'frozen' / 'copy.sqlite3'
    source.parent.mkdir()
    with sqlite3.connect(source) as db:
        db.executescript('''
            CREATE TABLE works(work_id TEXT PRIMARY KEY, title TEXT, user_id TEXT,
                tags TEXT, page_count INTEGER, is_deleted INTEGER);
            INSERT INTO works VALUES
              ('123','作品①','42','["original"]',2,0),
              ('124','hidden','42','[]',1,1),
              ('125','a strange title','43','[]',1,0);
        ''')
    return source


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_db_and_metadata_only_repeated_runs_preserve_source(tmp_path):
    source = fixture_db(tmp_path)
    before = sha(source)
    stat = source.stat()
    root = tmp_path / 'owned' / 'exports'
    root.parent.mkdir()
    first = subject.export_frozen_legacy_metadata(source, root)
    second = subject.export_frozen_legacy_metadata(source, root)
    assert first.path != second.path and first.path.is_file() and second.path.is_file()
    assert all(result.works == 3 and result.bytes_count == result.path.stat().st_size
               and result.zip_sha256 == sha(result.path) for result in (first, second))
    assert sha(source) == before and source.stat().st_mtime_ns == stat.st_mtime_ns
    assert list(source.parent.iterdir()) == [source]
    assert {p.name for p in root.iterdir()} == {
        subject.MARKER, first.path.name, second.path.name}
    with zipfile.ZipFile(first.path) as archive:
        assert archive.namelist() == ['pixiv_vault.db', 'metadata.json']
        assert archive.testzip() is None
        rows = json.loads(archive.read('metadata.json'))
        assert len(rows) == first.works
        assert rows[0]['title'] == '作品①' and rows[1]['is_deleted'] == 1
        extracted = tmp_path/'verified-export.sqlite3'
        extracted.write_bytes(archive.read('pixiv_vault.db'))
    with sqlite3.connect(extracted) as db:
        assert db.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        assert db.execute('SELECT COUNT(*) FROM works').fetchone() == (3,)
    assert first.db_sha256 == sha(extracted)


def test_live_wal_snapshot_is_not_exported_as_if_complete(tmp_path):
    source = fixture_db(tmp_path)
    sidecar = source.with_name(source.name + '-wal')
    sidecar.write_bytes(b'synthetic-wal-in-progress')
    root = tmp_path/'owned'/'exports'
    root.parent.mkdir()
    with pytest.raises(subject.LegacyMetadataExportError, match='停止した独立コピー'):
        subject.export_frozen_legacy_metadata(source, root)
    assert not root.exists() and sidecar.read_bytes() == b'synthetic-wal-in-progress'


def test_foreign_export_folder_refused_without_overwrite(tmp_path):
    source = fixture_db(tmp_path)
    root = tmp_path/'owned'/'exports'
    root.mkdir(parents=True)
    user_file = root/'manual-archive.zip'
    user_file.write_bytes(b'preserve existing foreign data')
    with pytest.raises(subject.LegacyMetadataExportError, match='所有を確認できない'):
        subject.export_frozen_legacy_metadata(source, root)
    assert user_file.read_bytes() == b'preserve existing foreign data'
    assert set(root.iterdir()) == {user_file}


@pytest.mark.parametrize('corruption', ('invalid_db', 'no_works', 'too_many_bytes'))
def test_corrupt_or_unsupported_db_never_publishes_success(tmp_path, monkeypatch, corruption):
    source = fixture_db(tmp_path)
    if corruption == 'invalid_db':
        source.write_bytes(b'not a SQLite database')
    elif corruption == 'no_works':
        with sqlite3.connect(source) as db:
            db.execute('DROP TABLE works')
    else:
        monkeypatch.setattr(subject, 'MAX_JSON_BYTES', 3)
    root = tmp_path/'owned'/'exports'
    root.parent.mkdir()
    with pytest.raises(subject.LegacyMetadataExportError):
        subject.export_frozen_legacy_metadata(source, root)
    assert not list(root.glob('*.zip'))
    assert not list(root.glob('.pv-export-*'))


def test_legacy_file_mutation_during_export_rejects_final_zip(tmp_path, monkeypatch):
    source = fixture_db(tmp_path)
    root = tmp_path/'owned'/'exports'
    root.parent.mkdir()
    digest = subject._digest
    hashes = [0]

    def change_source_after_backup(path):
        if path == source:
            hashes[0] += 1
            if hashes[0] == 2:
                with source.open('ab') as stream:
                    stream.write(b'a concurrent synthetic edit')
        return digest(path)

    monkeypatch.setattr(subject, '_digest', change_source_after_backup)
    with pytest.raises(subject.LegacyMetadataExportError, match='変更されました'):
        subject.export_frozen_legacy_metadata(source, root)
    assert not list(root.glob('*.zip'))


def test_source_and_export_same_parent_rejected(tmp_path):
    source = fixture_db(tmp_path)
    with pytest.raises(subject.LegacyMetadataExportError, match='同じ保存階層'):
        subject.export_frozen_legacy_metadata(source, source.parent)


def test_shutdown_cancelled_export_does_not_publish_or_leave_stage(tmp_path):
    source = fixture_db(tmp_path)
    root = tmp_path/'owned'/'exports'
    root.parent.mkdir()
    counter = [0]

    def cancel_at_metadata():
        counter[0] += 1
        return counter[0] >= 5

    with pytest.raises(subject.LegacyMetadataExportError, match='中止しました'):
        subject.export_frozen_legacy_metadata(source, root, cancelled=cancel_at_metadata)
    assert counter[0] >= 5
    assert not list(root.glob('*.zip')) and not list(root.glob('.pv-export-*'))


def test_cleanup_failure_after_publish_preserves_valid_success_with_warning(tmp_path,monkeypatch):
    """A real published ZIP must not be reported as total export failure."""
    from contextlib import AbstractContextManager
    import shutil
    from pathlib import Path

    source=fixture_db(tmp_path)
    owned=tmp_path/'owned'/'exports'
    owned.parent.mkdir()
    original_td=subject.tempfile.TemporaryDirectory
    retained=[]

    class DeniedCleanup(AbstractContextManager):
        def __init__(self,*args,**kwargs):
            self.delegate=original_td(*args,**kwargs)
            retained.append(self.delegate)
        def __enter__(self):return self.delegate.__enter__()
        def __exit__(self,*args):raise PermissionError('synthetic staging cleanup refused')

    try:
        with monkeypatch.context() as patch:
            patch.setattr(subject.tempfile,'TemporaryDirectory',DeniedCleanup)
            result=subject.export_frozen_legacy_metadata(source,owned)
        assert result.cleanup_warning is True
        assert result.path.exists() and result.zip_sha256==sha(result.path)
        with zipfile.ZipFile(result.path) as archive:
            assert archive.testzip() is None
            assert set(archive.namelist())=={'pixiv_vault.db','metadata.json'}
        assert len(list(owned.glob('*.zip')))==1
    finally:
        for tmp in retained:
            if Path(tmp.name).exists():shutil.rmtree(tmp.name)
