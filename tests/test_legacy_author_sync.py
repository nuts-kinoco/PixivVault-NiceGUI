"""Synthetic immutable legacy copies and managed-only author observation."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
import shutil
import sqlite3

import pytest

from pixivvault_nicegui.failed_records_repository import immutable_copy_signature
from pixivvault_nicegui.local_media_adapter import ReadonlyLocalMediaCatalog
from pixivvault_nicegui.legacy_author_sync import LegacyAuthorBridge


def fixture(tmp_path, *, count=5, streak=0, deleted=0):
    original = tmp_path / 'original.sqlite3'
    frozen = tmp_path / 'immutable.sqlite3'
    media = tmp_path / 'legacy-images'
    managed = tmp_path / 'managed'
    media.mkdir()
    managed.mkdir()
    with sqlite3.connect(original) as conn:
        conn.execute('CREATE TABLE works (work_id TEXT PRIMARY KEY,title TEXT,user_id TEXT,'
                     'page_count INTEGER,content_type TEXT,update_date TEXT,'
                     'is_deleted INTEGER,missing_streak INTEGER)')
        conn.execute('CREATE TABLE failed_queue (work_id TEXT,user_id TEXT,title TEXT,'
                     'work_type TEXT,error_reason TEXT,failed_at TEXT,retry_count INTEGER)')
        conn.executemany('INSERT INTO works VALUES (?,?,?,?,?,?,?,?)', [
            (str(100 + i), f'作品{i}', '23', 2, 'illust', '2026-09-01', deleted, streak)
            for i in range(count)
        ])
        conn.execute('INSERT INTO works VALUES (?,?,?,?,?,?,?,?)',
                     ('999', '別作者', '99', 1, 'illust', '2026', 0, 0))
        conn.execute('INSERT INTO failed_queue VALUES (?,?,?,?,?,?,?)',
                     ('101', '23', '作品1', 'illust', 'Timeout', '2026-10-21', 1))
    shutil.copyfile(original, frozen)
    return original, frozen, media, managed


def remote(*ids, author='23'):
    return tuple({'id': str(wid), 'user_id': author, 'type': 0,
                  'page_count': 2, 'update_date': '2026-09-01'} for wid in ids)


def bridge(frozen, media, managed, **options):
    return LegacyAuthorBridge(frozen, media, managed, **options)


def test_immutable_scan_preserves_legacy_and_recovers_across_clients(tmp_path, monkeypatch):
    original, frozen, media, managed = fixture(tmp_path)
    before = (sha256(original.read_bytes()).digest(), sha256(frozen.read_bytes()).digest())
    calls = []

    def presence(self, work_id):
        calls.append(work_id)
        return {'100': True, '101': False, '102': None}.get(work_id)

    monkeypatch.setattr(ReadonlyLocalMediaCatalog, 'inspect_presence', presence, raising=False)
    signature = immutable_copy_signature(frozen)
    first = bridge(frozen, media, managed, source_signature=signature)
    result = first.observe_author('23', remote(100, 101, 102, 103, 104), scan_id='a' * 32)
    assert set(result.saved) == {'100', '101', '102', '103', '104'}
    assert result.saved['100'].files_present is True
    assert result.saved['101'].files_present is False
    assert result.saved['102'].files_present is None
    assert result.saved['101'].failed is True
    assert result.saved['101'].update_date == '2026-09-01'
    assert result.failed_history == 1
    assert result.remote_missing_pending == result.remote_missing_confirmed == 0
    assert sorted(calls) == ['100', '101', '102', '103', '104']
    second = bridge(frozen, media, managed)
    repeat = second.observe_author('23', remote(100, 101, 102, 103, 104), scan_id='a' * 32)
    assert repeat == result
    assert (sha256(original.read_bytes()).digest(), sha256(frozen.read_bytes()).digest()) == before
    assert not any(Path(str(frozen) + suffix).exists() for suffix in ('-wal', '-shm', '-journal'))
    assert len(tuple(managed.glob('*.sqlite3'))) == 1


@pytest.mark.parametrize('count,streak,expected_pending,expected_confirmed', [
    (1, 1, 1, 0), (1, 2, 0, 1), (5, 2, 5, 0), (5, 4, 0, 5),
])
def test_legacy_streak_threshold_and_empty_complete_list(
        tmp_path, monkeypatch, count, streak, expected_pending, expected_confirmed):
    _, frozen, media, managed = fixture(tmp_path, count=count, streak=streak)
    monkeypatch.setattr(ReadonlyLocalMediaCatalog, 'inspect_presence',
                        lambda self, wid: None, raising=False)
    scan = bridge(frozen, media, managed).observe_author('23', (), scan_id='a' * 32)
    assert (scan.remote_missing_pending, scan.remote_missing_confirmed) == (
        expected_pending, expected_confirmed)
    assert sum(int(item.deleted) for item in scan.saved.values()) == expected_confirmed
    assert len(scan.remote_absent) == count
    assert sum(item.status == 'pending' for item in scan.remote_absent) == expected_pending
    assert sum(item.status == 'confirmed' for item in scan.remote_absent) == expected_confirmed
    assert {item.work_id for item in scan.remote_absent} == set(scan.saved)
    assert all(item.title and item.streak == streak + 1 for item in scan.remote_absent)
    same = bridge(frozen, media, managed).observe_author('23', (), scan_id='a' * 32)
    assert same == scan  # Re-render/restart is not a new deletion observation.


def test_changed_remote_is_atomic_and_two_clients_cannot_double_count(tmp_path, monkeypatch):
    _, frozen, media, managed = fixture(tmp_path, count=2, streak=0)
    monkeypatch.setattr(ReadonlyLocalMediaCatalog, 'inspect_presence',
                        lambda self, wid: None, raising=False)
    a = bridge(frozen, media, managed)
    b = bridge(frozen, media, managed)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda runner: runner.observe_author('23', remote(100),
                                                                    scan_id='a' * 32), (a, b)))
    assert all(r.remote_missing_pending == 1 and r.remote_missing_confirmed == 0 for r in results)
    # A fresh, different complete observation may advance the pending streak.
    later = b.observe_author('23', remote(101), scan_id='b' * 32)
    assert later.remote_missing_pending == 1
    assert later.saved['100'].deleted is False
    assert b.observe_author('23', remote(100, 101), scan_id='c' * 32).remote_missing_pending == 0


def test_original_deleted_requires_managed_receipt_even_after_remote_reappearance(tmp_path, monkeypatch):
    _, frozen, media, managed = fixture(tmp_path, count=1, deleted=1, streak=5)
    monkeypatch.setattr(ReadonlyLocalMediaCatalog, 'inspect_presence',
                        lambda self, wid: None, raising=False)
    first = bridge(frozen, media, managed).observe_author('23', (), scan_id='a' * 32)
    assert first.remote_missing_confirmed == 1 and first.saved['100'].deleted is True
    found = bridge(frozen, media, managed).observe_author('23', remote(100), scan_id='b' * 32)
    assert found.saved['100'].deleted is True and found.remote_missing_confirmed == 0
    assert found.remote_absent == ()
    # The bridge preserves legacy deletion evidence. Services may overlay a
    # verified managed download receipt; a remote reappearance is insufficient.
    reopened = bridge(frozen, media, managed)
    assert reopened.observe_author('23', remote(100)).saved['100'].deleted is True
    reopened.assert_source_unchanged()
    reopened.close()
    reopened.close()
    with pytest.raises(RuntimeError):
        reopened.assert_source_unchanged()
    with pytest.raises(RuntimeError):
        reopened.observe_author('23', remote(100))


@pytest.mark.parametrize('bad', [
    [{'id': '100', 'user_id': '99', 'type': 0, 'page_count': 1, 'update_date': ''}],
    [{'id': '100', 'user_id': '23', 'type': 2, 'page_count': 0, 'update_date': ''}],
    [{'id': '100', 'user_id': '23', 'type': 0, 'page_count': 2}],
    [{'id': '../100', 'user_id': '23', 'type': 0, 'page_count': 2, 'update_date': ''}],
    [{'id': '100', 'user_id': '23', 'type': 0, 'page_count': 2, 'update_date': ''}] * 2,
])
def test_untrusted_remote_rows_fail_without_sidecar(tmp_path, bad):
    _, frozen, media, managed = fixture(tmp_path)
    with pytest.raises((ValueError, TypeError)):
        bridge(frozen, media, managed).observe_author('23', tuple(bad))
    assert not list(managed.iterdir())


def test_rejects_source_signature_change_and_copy_swap(tmp_path):
    _, frozen, media, managed = fixture(tmp_path)
    good = immutable_copy_signature(frozen)
    with pytest.raises(ValueError):
        bridge(frozen, media, managed, source_signature=(*good[:3], b'\0' * 32))
    runner = bridge(frozen, media, managed, source_signature=good)
    with sqlite3.connect(frozen) as conn:
        conn.execute('UPDATE works SET update_date=? WHERE work_id=?', ('changed', '100'))
    with pytest.raises(ValueError):
        runner.observe_author('23', remote(100), scan_id='a' * 32)
    assert not list(managed.iterdir())


def test_unattributable_old_rows_do_not_block_another_authors_diff(tmp_path):
    """Historic ALTER TABLE left user_id NULL on unrelated old records."""
    _, frozen, media, managed = fixture(tmp_path)
    with sqlite3.connect(frozen) as conn:
        conn.executemany('INSERT INTO works VALUES (?,?,?,?,?,?,?,?)', (
            ('8100', None, None, 0, 'unknown-type', None, None, None),
            ('8101', 'Other', '99', -2, 'nonstandard-type', '', 0, 0),
        ))
    result = bridge(frozen, media, managed).observe_author('23', remote(100, 101))
    assert set(result.saved) == {'100', '101', '102', '103', '104'}
    assert not list(managed.iterdir())


def test_malformed_row_of_target_author_is_not_ignored(tmp_path):
    _, frozen, media, managed = fixture(tmp_path)
    with sqlite3.connect(frozen) as conn:
        conn.execute('UPDATE works SET page_count=? WHERE work_id=?', (-2, '101'))
    with pytest.raises(ValueError, match='invalid legacy deletion or page metadata'):
        bridge(frozen, media, managed).observe_author('23', remote(100, 101))
    assert not list(managed.iterdir())


@pytest.mark.parametrize('corruption', ['missing_works', 'missing_column', 'missing_failed',
                                        'bad_streak', 'bad_page', 'corrupt_file'])
def test_partial_corrupt_legacy_copy_fails_closed(tmp_path, corruption):
    _, frozen, media, managed = fixture(tmp_path)
    if corruption == 'corrupt_file':
        frozen.write_bytes(b'not sqlite')
    else:
        with sqlite3.connect(frozen) as conn:
            if corruption == 'missing_works':
                conn.execute('DROP TABLE works')
            elif corruption == 'missing_column':
                conn.execute('ALTER TABLE works DROP COLUMN missing_streak')
            elif corruption == 'missing_failed':
                conn.execute('DROP TABLE failed_queue')
            else:
                column, value = {'bad_streak': ('missing_streak', -1),
                                 'bad_page': ('page_count', -3)}[corruption]
                conn.execute(f'UPDATE works SET {column}=? WHERE work_id=?', (value, '100'))
    with pytest.raises((ValueError, sqlite3.DatabaseError)):
        bridge(frozen, media, managed).observe_author('23', (), scan_id='a' * 32)
    assert not list(managed.iterdir())


def test_rejects_symlinks_sidecars_and_overlapping_roots(tmp_path):
    _, frozen, media, managed = fixture(tmp_path)
    Path(str(frozen) + '-wal').write_bytes(b'')
    with pytest.raises(ValueError):
        bridge(frozen, media, managed)
    Path(str(frozen) + '-wal').unlink()
    with pytest.raises(ValueError):
        bridge(frozen, media, media)
    with pytest.raises(ValueError):
        bridge(frozen, media, frozen.parent)


def test_mid_read_checksum_failure_rolls_back_and_reopens_clean(tmp_path, monkeypatch):
    from pixivvault_nicegui import legacy_author_sync as module
    _, frozen, media, managed = fixture(tmp_path)
    monkeypatch.setattr(ReadonlyLocalMediaCatalog, 'inspect_presence',
                        lambda self, wid: True, raising=False)
    runner = bridge(frozen, media, managed)
    original = module.immutable_copy_signature
    calls = []

    def disrupted(path):
        result = original(path)
        calls.append(result)
        if len(calls) == 2:
            return (result[0], result[1], result[2], b'\xff' * 32)
        return result

    monkeypatch.setattr(module, 'immutable_copy_signature', disrupted)
    with pytest.raises(ValueError):
        runner.observe_author('23', remote(100), scan_id='a' * 32)
    assert not list(managed.iterdir())
    monkeypatch.setattr(module, 'immutable_copy_signature', original)
    assert bridge(frozen, media, managed).observe_author('23', remote(100),
                                                         scan_id='a' * 32).saved['100'].files_present


def test_display_refresh_never_advances_missing_streak_without_scan_id(tmp_path, monkeypatch):
    _, frozen, media, managed = fixture(tmp_path, count=1, streak=1)
    monkeypatch.setattr(ReadonlyLocalMediaCatalog, 'inspect_presence',
                        lambda self, wid: None, raising=False)
    runner = bridge(frozen, media, managed)
    assert runner.observe_author('23', ()).remote_missing_pending == 1
    assert not list(managed.iterdir())
    assert runner.observe_author('23', (), scan_id='a' * 32).remote_missing_pending == 1
    assert runner.observe_author('23', ()).remote_missing_pending == 1
    assert runner.observe_author('23', (), scan_id='b' * 32).remote_missing_confirmed == 1
    assert runner.observe_author('23', (), scan_id='b' * 32).remote_missing_confirmed == 1


def test_duplicate_scan_id_with_different_payload_is_rejected(tmp_path, monkeypatch):
    _, frozen, media, managed = fixture(tmp_path, count=2)
    monkeypatch.setattr(ReadonlyLocalMediaCatalog, 'inspect_presence',
                        lambda self, wid: None, raising=False)
    runner = bridge(frozen, media, managed)
    runner.observe_author('23', remote(100), scan_id='a' * 32)
    with pytest.raises(ValueError):
        runner.observe_author('23', remote(101), scan_id='a' * 32)
    assert runner.observe_author('23', remote(100), scan_id='a' * 32).remote_missing_pending == 1


@pytest.mark.parametrize('column', ('content_type', 'missing_streak', 'is_deleted'))
def test_historical_null_legacy_values_are_interpreted_conservatively(tmp_path, column, monkeypatch):
    _, frozen, media, managed = fixture(tmp_path, count=1)
    with sqlite3.connect(frozen) as conn:
        conn.execute(f'UPDATE works SET {column}=NULL WHERE work_id=?', ('100',))
    monkeypatch.setattr(ReadonlyLocalMediaCatalog, 'inspect_presence',
                        lambda self, wid: None, raising=False)
    scan = bridge(frozen, media, managed).observe_author('23', (), scan_id='a' * 32)
    assert scan.remote_missing_pending == 1
    assert scan.saved['100'].deleted is False


def test_bounded_source_rejects_oversize_without_touching_managed(tmp_path):
    _, frozen, media, managed = fixture(tmp_path)
    with frozen.open('r+b') as stream:
        stream.truncate(128 * 1024 * 1024 + 1)
    with pytest.raises(ValueError):
        bridge(frozen, media, managed)
    assert not list(managed.iterdir())


def test_media_indexing_race_changes_source_without_managed_write(tmp_path, monkeypatch):
    _, frozen, media, managed = fixture(tmp_path)
    original = sha256(frozen.read_bytes()).digest()
    runner = bridge(frozen, media, managed)
    changed = False

    def inspect(self, wid):
        nonlocal changed
        if not changed:
            with sqlite3.connect(frozen) as conn:
                conn.execute('UPDATE works SET title=? WHERE work_id=?', ('変更', '100'))
            changed = True
        return True

    monkeypatch.setattr(ReadonlyLocalMediaCatalog, 'inspect_presence', inspect, raising=False)
    with pytest.raises(ValueError, match='source changed'):
        runner.observe_author('23', remote(100), scan_id='a' * 32)
    assert sha256(frozen.read_bytes()).digest() != original
    assert not list(managed.iterdir())


def test_sidecar_sql_failure_rolls_back_all_streaks_and_scan_receipt(tmp_path, monkeypatch):
    from pixivvault_nicegui import legacy_author_sync as module
    _, frozen, media, managed = fixture(tmp_path, count=2)
    monkeypatch.setattr(ReadonlyLocalMediaCatalog, 'inspect_presence',
                        lambda self, wid: None, raising=False)
    first = bridge(frozen, media, managed)
    assert first.observe_author('23', remote(100), scan_id='a' * 32).remote_missing_pending == 1
    actual_connect = sqlite3.connect

    class FaultConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql.startswith('INSERT INTO observations'):
                raise sqlite3.OperationalError('synthetic storage fault')
            return super().execute(sql, parameters)

    def faulty_connect(path, *args, **kwargs):
        if module.SIDECAR in str(path) and 'mode=rwc' in str(path):
            kwargs['factory'] = FaultConnection
        return actual_connect(path, *args, **kwargs)

    monkeypatch.setattr(module.sqlite3, 'connect', faulty_connect)
    with pytest.raises(sqlite3.OperationalError, match='synthetic storage fault'):
        first.observe_author('23', remote(101), scan_id='b' * 32)
    monkeypatch.setattr(module.sqlite3, 'connect', actual_connect)
    # The failed scan did not persist a half-updated state or mark its ID used.
    assert bridge(frozen, media, managed).observe_author('23', remote(100)).remote_missing_pending == 1
    assert bridge(frozen, media, managed).observe_author('23', remote(101),
                                                       scan_id='b' * 32).remote_missing_pending == 1


@pytest.mark.parametrize('scan_id', ('', 'A' * 32, '0' * 31, 'not-a-uuid'))
def test_invalid_scan_identifier_is_rejected_without_sidecar(tmp_path, scan_id):
    _, frozen, media, managed = fixture(tmp_path)
    with pytest.raises(ValueError):
        bridge(frozen, media, managed).observe_author('23', (), scan_id=scan_id)
    assert not list(managed.iterdir())


def test_simultaneous_first_use_from_many_bridges_is_atomic(tmp_path, monkeypatch):
    _, frozen, media, managed = fixture(tmp_path, count=5, streak=1)
    monkeypatch.setattr(ReadonlyLocalMediaCatalog, 'inspect_presence',
                        lambda self, wid: None, raising=False)
    clients = [bridge(frozen, media, managed) for _ in range(16)]
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda client: client.observe_author('23', (),
                                                                    scan_id='a' * 32), clients))
    assert all(scan.remote_missing_pending == 5 and scan.remote_missing_confirmed == 0
               for scan in results)
    assert len(tuple(managed.glob('*.sqlite3'))) == 1


def test_persistent_empty_sidecar_rejected_even_with_initialization_retry(tmp_path):
    from pixivvault_nicegui.legacy_author_sync import SIDECAR
    _, frozen, media, managed = fixture(tmp_path)
    (managed / SIDECAR).touch()
    with pytest.raises(ValueError, match='store rejected'):
        bridge(frozen, media, managed).observe_author('23', (), scan_id='a' * 32)
    assert (managed / SIDECAR).stat().st_size == 0
