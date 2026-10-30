"""Owned failure dismissal ledger: exact-record hiding, CAS and ownership.

All data is synthetic under tmp_path; original sources are never modified.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import replace
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

import pixivvault_nicegui.failed_record_dismissals as dismissals_module
from pixivvault_nicegui.failed_record_dismissals import (
    CLOSED_MESSAGE, CONFLICT_MESSAGE, DATABASE, IDENTITY, LIMIT_MESSAGE, MARKER,
    MAX_DECISIONS, MAX_FIELD_CHARS, FailureDismissalConflict, FailureDismissalError,
    FailureDismissals, record_digest, source_namespace,
)
from pixivvault_nicegui.failed_records_state import (
    CSV_COLUMNS, FailedRecordsState, FailureRecord, MAX_RECORDS, PAGE_SIZE,
)
from pixivvault_nicegui.fs_paths import native_io_path, sqlite_readonly_uri

NS_A = 'a' * 64
NS_B = 'b' * 64


def rec(work_id='101', **changes):
    fields = dict(work_id=work_id, user_id='9001', title='合成タイトル' + work_id,
                  work_type='illust', error_reason='Timeout 合成理由',
                  failed_at='2026-10-01 00:00:00', retry_count=1)
    fields.update(changes)
    return FailureRecord(**fields)


def rows(count, start=100):
    return tuple(rec(str(start + index)) for index in range(count))


def stored(root):
    """Exact persisted rows through an independent read-only connection."""
    uri = sqlite_readonly_uri(Path(root) / DATABASE, immutable=False)
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        return (conn.execute('SELECT rowid,namespace,digest,revision FROM dismissals '
                             'ORDER BY rowid').fetchall(),
                conn.execute('SELECT namespace,revision FROM namespaces '
                             'ORDER BY namespace').fetchall())


def tamper(root, statement):
    with closing(sqlite3.connect(str(native_io_path(Path(root) / DATABASE)),
                                 isolation_level=None)) as conn:
        conn.execute(statement)


def tree(root):
    root = Path(root)
    return sorted((str(path.relative_to(root)), path.read_bytes() if path.is_file() else None)
                  for path in root.rglob('*'))


@pytest.fixture
def owned(tmp_path):
    parent = tmp_path / 'managed'
    parent.mkdir()
    return parent / 'failure-dismissals'


@pytest.fixture
def ledger(owned):
    handle = FailureDismissals(owned)
    try:
        yield handle
    finally:
        handle.close()


def test_single_dismissal_hides_exact_row_and_stores_exact_rowid_hash(ledger, owned):
    records = rows(3)
    assert ledger.snapshot(NS_A, records) == (0, records)
    assert ledger.dismiss(NS_A, (records[1],), expected_revision=0) == 1
    revision, visible = ledger.snapshot(NS_A, records)
    assert revision == 1 and visible == (records[0], records[2])
    assert visible[0] is records[0] and visible[1] is records[2]
    assert stored(owned) == ([(1, NS_A, record_digest(records[1]), 1)], [(NS_A, 1)])
    assert (owned / MARKER).read_bytes() == IDENTITY
    assert not Path(str(owned / DATABASE) + '-journal').exists()


def test_clear_all_captures_whole_snapshot_not_filter_or_page(ledger, owned):
    records = rows(PAGE_SIZE * 2 + 3)
    revision, visible = ledger.snapshot(NS_A, records)
    state = FailedRecordsState(visible)
    state.set_query('合成タイトル101')
    assert len(state.visible_records) < len(state.records) == len(records)
    assert ledger.dismiss(NS_A, state.records, expected_revision=revision) == 1
    assert ledger.snapshot(NS_A, records) == (1, ())
    persisted, namespaces = stored(owned)
    assert [row[0] for row in persisted] == list(range(1, len(records) + 1))
    assert [row[2] for row in persisted] == [record_digest(r) for r in records]
    assert namespaces == [(NS_A, 1)]
    newer = rec('999')
    assert ledger.snapshot(NS_A, records + (newer,)) == (1, (newer,))


def test_digest_covers_all_seven_fields_and_is_unambiguous():
    base = rec()
    assert record_digest(base) == record_digest(rec())
    changed = {name: (getattr(base, name) + 'x' if name != 'retry_count' else 2)
               for name in CSV_COLUMNS}
    digests = {record_digest(replace(base, **{name: value}))
               for name, value in changed.items()}
    assert len(digests) == len(CSV_COLUMNS) and record_digest(base) not in digests
    assert (record_digest(rec(title='ab', work_type='c'))
            != record_digest(rec(title='a', work_type='bc')))


@pytest.mark.parametrize('field', CSV_COLUMNS)
def test_changed_row_reappears_under_older_dismissal(ledger, field):
    original = rec()
    ledger.dismiss(NS_A, (original,), expected_revision=0)
    value = 7 if field == 'retry_count' else getattr(original, field) + '-再'
    changed = replace(original, **{field: value})
    assert ledger.snapshot(NS_A, (changed,)) == (1, (changed,))


def test_restart_preserves_decisions_and_revision(owned):
    records = rows(2)
    first = FailureDismissals(owned)
    try:
        assert first.dismiss(NS_A, (records[0],), expected_revision=0) == 1
    finally:
        first.close()
    second = FailureDismissals(owned)
    try:
        assert second.snapshot(NS_A, records) == (1, (records[1],))
        assert second.dismiss(NS_A, (records[1],), expected_revision=1) == 2
    finally:
        second.close()
    assert stored(owned)[1] == [(NS_A, 2)]


def test_namespaces_are_independent(ledger, owned):
    records = rows(2)
    ledger.dismiss(NS_A, records, expected_revision=0)
    assert ledger.snapshot(NS_B, records) == (0, records)
    assert ledger.dismiss(NS_B, (records[0],), expected_revision=0) == 1
    assert ledger.snapshot(NS_A, records) == (1, ())
    assert stored(owned)[1] == [(NS_A, 1), (NS_B, 1)]


def test_source_namespace_normalizes_spelling_without_touching_source(tmp_path, ledger):
    source_dir = tmp_path / 'readonly-source'
    source_dir.mkdir()
    source = source_dir / 'legacy.db'
    with closing(sqlite3.connect(source)) as conn:
        conn.execute('CREATE TABLE failed_queue(work_id TEXT,title TEXT)')
        conn.execute("INSERT INTO failed_queue VALUES('101','合成タイトル101')")
        conn.commit()
    before = tree(source_dir), source.stat().st_mtime_ns
    namespace = source_namespace(source)
    assert len(namespace) == 64 and set(namespace) <= set('0123456789abcdef')
    assert source_namespace(source_dir / 'sub' / '..' / 'legacy.db') == namespace
    assert source_namespace(native_io_path(source)) == namespace
    if os.name == 'nt':
        assert source_namespace(str(source).upper()) == namespace
    assert source_namespace(source_dir / 'other.db') != namespace
    assert source_namespace(source_dir / 'missing.db') != namespace
    for bad in ('relative.db', '', 'C:\0x', b'/bytes.db', 101, None):
        with pytest.raises(FailureDismissalError):
            source_namespace(bad)
    with pytest.raises(FailureDismissalError):
        source_namespace(str(tmp_path) + '/' + 'x' * 40_000)
    records = rows(2)
    ledger.dismiss(namespace, records, expected_revision=0)
    assert ledger.snapshot(namespace, records) == (1, ())
    assert (tree(source_dir), source.stat().st_mtime_ns) == before


def test_concurrent_handles_refuse_stale_confirmation(ledger, owned):
    records = rows(3)
    other = FailureDismissals(owned)
    try:
        assert ledger.snapshot(NS_A, records)[0] == 0
        assert other.snapshot(NS_A, records)[0] == 0
        assert other.dismiss(NS_A, (records[0],), expected_revision=0) == 1
        with pytest.raises(FailureDismissalConflict) as error:
            ledger.dismiss(NS_A, (records[1],), expected_revision=0)
        assert str(error.value) == CONFLICT_MESSAGE
        assert stored(owned)[0] == [(1, NS_A, record_digest(records[0]), 1)]
        # A record hidden by revision 1 cannot be visible in that snapshot.
        with pytest.raises(FailureDismissalConflict):
            ledger.dismiss(NS_A, (records[0], records[1]), expected_revision=1)
        assert ledger.snapshot(NS_A, records) == (1, records[1:])
    finally:
        other.close()


_CHILD = (
    "import sys\n"
    "from pixivvault_nicegui.failed_records_state import FailureRecord\n"
    "from pixivvault_nicegui.failed_record_dismissals import "
    "FailureDismissalConflict, FailureDismissals\n"
    "ledger = FailureDismissals(sys.argv[1])\n"
    "record = FailureRecord(sys.argv[2], '9001', 'child', 'illust', 'Timeout', "
    "'2026-10-01', 0)\n"
    "try:\n"
    "    print('OK', ledger.dismiss('a' * 64, (record,), expected_revision=0))\n"
    "except FailureDismissalConflict:\n"
    "    print('CONFLICT')\n"
    "finally:\n"
    "    ledger.close()\n"
)


def test_separate_processes_compare_and_swap_exactly_once(ledger, owned, tmp_path):
    env = os.environ.copy()
    env.setdefault("TEMP", str(tmp_path))
    env.setdefault("TMP", env["TEMP"])
    env.setdefault("PYTHONPATH", str(Path(__file__).resolve().parents[1] / "src"))
    children = []
    try:
        for work_id in ('201', '202'):
            children.append(subprocess.Popen(
                [sys.executable, "-X", "utf8", "-c", _CHILD, str(owned), work_id],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env))
        outputs = []
        for child in children:
            out, err = child.communicate(timeout=30)
            assert child.returncode == 0, err
            outputs.append(out.strip())
    finally:
        for child in children:
            if child.poll() is None:
                # The generated child has bounded SQLite waits and closes
                # normally. Never turn an exit timeout into forced success.
                child.wait(timeout=30)
    assert sorted(outputs) == ['CONFLICT', 'OK 1']
    persisted, namespaces = stored(owned)
    assert len(persisted) == 1 and namespaces == [(NS_A, 1)]
    with pytest.raises(FailureDismissalConflict):
        ledger.dismiss(NS_A, (rec('203'),), expected_revision=0)


# Built inside the test so large inputs never become pytest parameter payloads.
_INVALID_BATCHES = {
    'empty': lambda: (),
    'list': lambda: [rec()],
    'duplicate-id': lambda: (rec(), rec(title='other')),
    'foreign-item': lambda: (rec(), 'not a record'),
    'too-many': lambda: (rec(),) * (MAX_RECORDS + 1),
    'long-field': lambda: (rec(title='x' * (MAX_FIELD_CHARS + 1)),),
    'huge-retry': lambda: (rec(retry_count=2**63),),
}


@pytest.mark.parametrize('case', sorted(_INVALID_BATCHES))
def test_invalid_batch_is_refused_before_any_write(ledger, owned, case):
    with pytest.raises(FailureDismissalError):
        ledger.dismiss(NS_A, _INVALID_BATCHES[case](), expected_revision=0)
    assert stored(owned) == ([], [])


@pytest.mark.parametrize('namespace, revision', [
    ('A' * 64, 0), ('a' * 63, 0), (None, 0), (NS_A, -1), (NS_A, True), (NS_A, '0'),
])
def test_invalid_namespace_or_revision_is_refused(ledger, owned, namespace, revision):
    with pytest.raises(FailureDismissalError):
        ledger.dismiss(namespace, (rec(),), expected_revision=revision)
    assert stored(owned) == ([], [])


def test_field_bound_is_exact_and_total_bytes_are_bounded(ledger, monkeypatch):
    longest = rec(title='x' * MAX_FIELD_CHARS)
    assert ledger.snapshot(NS_A, (longest,)) == (0, (longest,))
    with pytest.raises(FailureDismissalError):
        ledger.snapshot(NS_A, (rec(error_reason='x' * (MAX_FIELD_CHARS + 1)),))
    monkeypatch.setattr(dismissals_module, 'MAX_TOTAL_UTF8_BYTES', 200)
    with pytest.raises(FailureDismissalError):
        ledger.snapshot(NS_A, (rec('1', title='y' * 90), rec('2', title='y' * 90)))


def test_sqlite_failure_mid_batch_rolls_back_without_raw_error(ledger, owned):
    records = rows(3)
    ledger._conn.execute(
        'CREATE TRIGGER synthetic_failure BEFORE INSERT ON dismissals '
        f"WHEN NEW.digest='{record_digest(records[2])}' "
        "BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
    with pytest.raises(FailureDismissalError) as error:
        ledger.dismiss(NS_A, records, expected_revision=0)
    assert type(error.value) is FailureDismissalError
    assert 'synthetic' not in str(error.value)
    assert stored(owned) == ([], [])
    ledger.close()
    with pytest.raises(FailureDismissalError):
        FailureDismissals(owned)  # The injected trigger is foreign schema.


def test_decision_cap_refuses_atomically_and_never_evicts(ledger, owned, monkeypatch):
    assert MAX_DECISIONS == 100_000
    monkeypatch.setattr(dismissals_module, 'MAX_DECISIONS', 3)
    records = rows(5)
    assert ledger.dismiss(NS_A, records[:2], expected_revision=0) == 1
    with pytest.raises(FailureDismissalError) as error:
        ledger.dismiss(NS_B, records[2:4], expected_revision=0)
    assert str(error.value) == LIMIT_MESSAGE
    assert stored(owned) == ([(1, NS_A, record_digest(records[0]), 1),
                              (2, NS_A, record_digest(records[1]), 1)], [(NS_A, 1)])
    assert ledger.snapshot(NS_B, records) == (0, records)
    assert ledger.dismiss(NS_B, (records[2],), expected_revision=0) == 1
    assert ledger.snapshot(NS_A, records) == (1, records[2:])


def test_no_source_content_or_path_is_stored(ledger, owned, tmp_path):
    source = tmp_path / 'secret-source-folder' / 'legacy.db'
    namespace = source_namespace(source)
    secret = rec('101', user_id='user-synth-zz', title='TITLE-SYNTH-QX',
                 error_reason='REASON-SYNTH-QX', failed_at='2026-10-01T23:59:58Z')
    ledger.dismiss(namespace, (secret,), expected_revision=0)
    ledger.close()
    blobs = b''.join(data for _, data in tree(owned) if data is not None)
    for text in ('user-synth-zz', 'TITLE-SYNTH-QX', 'REASON-SYNTH-QX',
                 '2026-10-01T23:59:58Z', 'secret-source-folder'):
        for encoding in ('utf-8', 'utf-16-le'):
            assert text.encode(encoding) not in blobs
    assert sorted(name for name, _ in tree(owned)) == sorted([DATABASE, MARKER])


def test_close_is_idempotent_and_refuses_later_use(owned):
    handle = FailureDismissals(owned)
    handle.close()
    handle.close()
    for call in (lambda: handle.snapshot(NS_A, ()),
                 lambda: handle.dismiss(NS_A, (rec(),), expected_revision=0)):
        with pytest.raises(FailureDismissalError) as error:
            call()
        assert str(error.value) == CLOSED_MESSAGE
    reopened = FailureDismissals(owned)
    reopened.close()


def test_relative_missing_parent_and_non_path_roots_are_refused(tmp_path):
    for bad in ('relative-ledger', '', 101, None, b'/bytes'):
        with pytest.raises(FailureDismissalError):
            FailureDismissals(bad)
    missing = tmp_path / 'not-created' / 'ledger'
    with pytest.raises(FailureDismissalError) as error:
        FailureDismissals(missing)
    assert str(tmp_path) not in str(error.value)
    assert not missing.parent.exists()


def test_unmarked_existing_data_and_foreign_markers_are_refused(tmp_path):
    foreign = tmp_path / 'foreign'
    foreign.mkdir()
    (foreign / DATABASE).write_bytes(b'not owned synthetic data')
    (tmp_path / 'empty').mkdir()
    wrong = tmp_path / 'wrong-marker'
    wrong.mkdir()
    (wrong / MARKER).write_bytes(IDENTITY + b'extra')
    folder_marker = tmp_path / 'folder-marker'
    (folder_marker / MARKER).mkdir(parents=True)
    plain_file = tmp_path / 'plain-file'
    plain_file.write_bytes(b'synthetic')
    before = tree(tmp_path)
    for root in (foreign, tmp_path / 'empty', wrong, folder_marker, plain_file):
        with pytest.raises(FailureDismissalError):
            FailureDismissals(root)
    assert tree(tmp_path) == before


def test_marked_root_with_foreign_database_is_not_written(tmp_path):
    root = tmp_path / 'marked-foreign'
    root.mkdir()
    (root / MARKER).write_bytes(IDENTITY)
    with closing(sqlite3.connect(root / DATABASE)) as conn:
        conn.execute('CREATE TABLE dismissals(namespace TEXT,digest TEXT,revision INTEGER)')
        conn.commit()
    before = tree(root)
    with pytest.raises(FailureDismissalError):
        FailureDismissals(root)
    assert tree(root) == before


def test_existing_owner_marker_without_database_is_not_reinitialized(tmp_path):
    root = tmp_path / 'interrupted-generated-ledger'
    root.mkdir()
    (root / MARKER).write_bytes(IDENTITY)
    before = tree(root)
    with pytest.raises(FailureDismissalError):
        FailureDismissals(root)
    assert tree(root) == before


def test_existing_empty_database_is_not_adopted_as_new_history(tmp_path):
    root = tmp_path / 'generated-empty-foreign'
    root.mkdir()
    (root / MARKER).write_bytes(IDENTITY)
    (root / DATABASE).write_bytes(b'')
    before = tree(root)
    with pytest.raises(FailureDismissalError):
        FailureDismissals(root)
    assert tree(root) == before


@pytest.mark.parametrize('statement', [
    'PRAGMA application_id=1',
    'PRAGMA user_version=2',
    'CREATE TABLE leaked(text TEXT)',
    'CREATE INDEX extra ON dismissals(revision)',
    'CREATE TRIGGER extra AFTER INSERT ON dismissals BEGIN SELECT 1; END',
    "UPDATE owner SET purpose='other'",
    "INSERT INTO dismissals VALUES('" + NS_A + "','" + 'z' * 64 + "',1)",
])
def test_tampered_schema_or_rows_are_refused(owned, statement):
    FailureDismissals(owned).close()
    tamper(owned, statement)
    try:
        handle = FailureDismissals(owned)
    except FailureDismissalError:
        return
    # Row-level corruption passes schema checks but must fail on read.
    try:
        with pytest.raises(FailureDismissalError):
            handle.snapshot(NS_A, (rec(),))
    finally:
        handle.close()


def test_stray_wal_and_marker_change_while_open_are_refused(ledger, owned):
    wal = Path(str(owned / DATABASE) + '-wal')
    wal.write_bytes(b'synthetic')
    with pytest.raises(FailureDismissalError):
        ledger.snapshot(NS_A, ())
    wal.unlink()  # Synthetic file created by this test only.
    assert ledger.snapshot(NS_A, ()) == (0, ())
    (owned / MARKER).write_bytes(b'changed')
    with pytest.raises(FailureDismissalError):
        ledger.dismiss(NS_A, (rec(),), expected_revision=0)
    (owned / MARKER).write_bytes(IDENTITY)
    assert stored(owned) == ([], [])


def test_linked_root_is_refused(tmp_path, owned):
    FailureDismissals(owned).close()
    alias = tmp_path / 'alias'
    try:
        alias.symlink_to(owned, target_is_directory=True)
    except OSError:
        pytest.skip('symlink requires privileges')
    with pytest.raises(FailureDismissalError):
        FailureDismissals(alias)


def test_long_windows_path_round_trip(tmp_path):
    parent = tmp_path
    for index in range(6):
        parent = parent / (f'long-owned-{index}-' + 'p' * 48)
    native_io_path(parent).mkdir(parents=True)
    root = parent / 'failure-dismissals'
    assert len(str(root)) > 300
    records = rows(2)
    first = FailureDismissals(root)
    try:
        assert first.dismiss(NS_A, (records[0],), expected_revision=0) == 1
    finally:
        first.close()
    second = FailureDismissals(root)
    try:
        assert second.snapshot(NS_A, records) == (1, (records[1],))
    finally:
        second.close()
