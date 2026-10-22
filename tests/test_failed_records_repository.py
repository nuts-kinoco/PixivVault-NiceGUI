"""Read-only snapshots against newly constructed, synthetic SQLite copies."""

from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from pixivvault_nicegui import failed_records_repository as repository
from pixivvault_nicegui.failed_records_repository import read_failed_records
from pixivvault_nicegui.failed_records_state import MAX_RECORDS


DDL = """CREATE TABLE failed_queue (
    work_id TEXT PRIMARY KEY, user_id TEXT, title TEXT, work_type TEXT,
    error_reason TEXT, failed_at TEXT, retry_count INTEGER DEFAULT 0
);"""


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "synthetic.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(DDL)
        connection.executemany("INSERT INTO failed_queue VALUES(?,?,?,?,?,?,?)", [
            ("1", "author1", "old", "illust", "Timeout", "2026-01-01T00:00:00", 1),
            ("2", "author2", "recent", "novel", "403", "2026-10-22T23:00:00", 5),
            ("3", None, None, None, None, None, None),
        ])
    return path


def test_original_seven_fields_sort_order_and_null_fallback_without_writes(source):
    before = source.read_bytes()
    items = read_failed_records(source)
    assert tuple(record.work_id for record in items) == ("2", "1", "3")
    assert items[0].title == "recent" and items[0].work_type == "novel"
    assert items[0].retry_count == 5
    assert (items[2].user_id, items[2].title, items[2].work_type,
            items[2].error_reason, items[2].failed_at, items[2].retry_count) == (
            "", "", "illust", "不明なエラー", "", 0)
    assert source.read_bytes() == before
    assert not list(source.parent.glob("*-journal"))


def test_missing_or_unsupported_table_is_error_not_empty(tmp_path):
    missing = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        read_failed_records(missing)
    assert not missing.exists()
    empty = tmp_path / "empty.db"
    with sqlite3.connect(empty):
        pass
    with pytest.raises(ValueError, match="schema"):
        read_failed_records(empty)
    with sqlite3.connect(empty) as connection:
        connection.execute("CREATE TABLE failed_queue(work_id TEXT, title TEXT)")
    with pytest.raises(ValueError, match="schema"):
        read_failed_records(empty)


def test_bad_rows_are_rejected_without_partial_success(source):
    with sqlite3.connect(source) as connection:
        connection.execute("UPDATE failed_queue SET retry_count=? WHERE work_id='1'", ("bad",))
    with pytest.raises(ValueError, match="retry"):
        read_failed_records(source)
    with sqlite3.connect(source) as connection:
        connection.execute("UPDATE failed_queue SET retry_count=0 WHERE work_id='1'")
        connection.execute("UPDATE failed_queue SET title=? WHERE work_id='1'", (sqlite3.Binary(b"bad-bytes"),))
    with pytest.raises(ValueError, match="text"):
        read_failed_records(source)


def test_bounded_results_reject_overflow_not_silent_truncation(tmp_path):
    path = tmp_path / "large.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(DDL)
        connection.executemany("INSERT INTO failed_queue(work_id) VALUES(?)",
                               ((str(i),) for i in range(MAX_RECORDS + 1)))
    with pytest.raises(ValueError, match="limit"):
        read_failed_records(path)


def test_cumulative_payload_rejected_before_any_partial_snapshot(source, monkeypatch):
    # Small synthetic ceiling exercises the same path as a real 32-MiB cap.
    monkeypatch.setattr(repository, "MAX_TOTAL_UTF8_BYTES", 180)
    with sqlite3.connect(source) as connection:
        connection.execute("UPDATE failed_queue SET error_reason=? WHERE work_id='1'", ("確認" * 70,))
    with pytest.raises(ValueError, match="total payload limit"):
        read_failed_records(source)


def test_single_oversized_sqlite_text_field_is_rejected_before_row_decoding(source):
    with sqlite3.connect(source) as connection:
        connection.execute("UPDATE failed_queue SET title=? WHERE work_id='1'", ("z" * 65_537,))
    with pytest.raises(ValueError, match="field size"):
        read_failed_records(source)


def test_sqlite_giant_record_hits_native_length_limit(source):
    # The native SQLite limit runs before materialization of an unbounded cell.
    with sqlite3.connect(source) as connection:
        connection.execute("UPDATE failed_queue SET error_reason=? WHERE work_id='1'", ("x" * (3 * 1024 * 1024),))
    with pytest.raises(sqlite3.DataError, match="too big"):
        read_failed_records(source)


def test_wal_latest_commit_and_thread_confined_reads(tmp_path):
    path = tmp_path / "wal-fixture.db"
    writer = sqlite3.connect(path)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.executescript(DDL)
        writer.execute("INSERT INTO failed_queue(work_id,title,failed_at) VALUES('wal','visible','2026-10-23')")
        writer.commit()
        with ThreadPoolExecutor(max_workers=2) as pool:
            snapshots = list(pool.map(read_failed_records, [path, path]))
        assert all(len(records) == 1 and records[0].title == "visible" for records in snapshots)
    finally:
        writer.close()


def test_empty_valid_schema_is_success_and_does_not_create_rows(tmp_path):
    path = tmp_path / "empty-valid.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(DDL)
    before = path.read_bytes()
    assert read_failed_records(path) == ()
    assert path.read_bytes() == before
