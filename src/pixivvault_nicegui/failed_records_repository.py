"""One-shot, explicit-path, read-only snapshots of the original failed_queue.

No legacy Database instance is constructed: it would create/migrate tables.
This reader neither writes records nor creates a missing database. The caller
must supply a permitted copy of the legacy SQLite schema.
"""

from __future__ import annotations

from pathlib import Path
import hashlib
import sqlite3

from .failed_records_state import CSV_COLUMNS, FailureRecord, MAX_RECORDS


_SELECT = ("SELECT work_id,user_id,title,work_type,error_reason,failed_at,retry_count "
           "FROM failed_queue ORDER BY failed_at DESC LIMIT ?")
_TEXT_DEFAULTS = ("", "", "", "illust", "不明なエラー", "")
_MAX_FIELD_CHARS = 65_536
# Bounded read: even 10k rows containing individually valid long fields can
# otherwise consume several gigabytes in a single fetchall()/SQLite sorter.
MAX_TOTAL_UTF8_BYTES = 32 * 1024 * 1024
MAX_IMMUTABLE_COPY_BYTES = 128 * 1024 * 1024
_MAX_SQLITE_VALUE_BYTES = 2 * 1024 * 1024
_TEXT_COLUMNS = CSV_COLUMNS[:-1]  # Constant names only, never user SQL.
_OVERSIZED = ("SELECT 1 FROM failed_queue WHERE " + " OR ".join(
    f"length({field}) > ?" for field in _TEXT_COLUMNS) + " LIMIT 1")
_TOTAL_BYTES = ("SELECT SUM(" + " + ".join(
    f"COALESCE(length(CAST({field} AS BLOB)),0)" for field in _TEXT_COLUMNS)
    + ") FROM failed_queue")


def immutable_copy_signature(db_path: Path) -> tuple[int, int, int, bytes]:
    """Bounded whole-file fingerprint for an explicitly copied source DB.

    A copied failed_queue can be changed without altering any currently
    selected row. Freeze the *entire* source at opt-in time and reject later
    changes before any new managed acceptance, not just stale target rows.
    This proves snapshot stability, not that the caller created a correct copy
    of an active original DB; callers must use SQLite's offline backup process.
    """
    path = Path(db_path)
    if (not path.is_absolute() or not path.is_file() or path.is_symlink()
            or any(Path(str(path) + suffix).exists()
                   for suffix in ('-wal', '-shm', '-journal'))):
        raise ValueError('immutable failure source is not a standalone file')
    original = path.stat()
    if not 0 < original.st_size <= MAX_IMMUTABLE_COPY_BYTES:
        raise ValueError('immutable failure source size rejected')
    digest = hashlib.sha256()
    total = 0
    with path.open('rb') as stream:
        while block := stream.read(1024 * 1024):
            total += len(block)
            if total > MAX_IMMUTABLE_COPY_BYTES:
                raise ValueError('immutable failure source size rejected')
            digest.update(block)
    current = path.stat()
    if (path.is_symlink() or current.st_size != original.st_size
            or current.st_mtime_ns != original.st_mtime_ns
            or current.st_ino != original.st_ino or total != current.st_size):
        raise ValueError('immutable failure source changed during fingerprint')
    return current.st_size, current.st_mtime_ns, current.st_ino, digest.digest()


def _text(value: object, default: str) -> str:
    if value is None:
        return default
    if type(value) is not str or len(value) > _MAX_FIELD_CHARS:
        raise ValueError("unsupported failed_queue text value")
    return value


def _row_to_record(row: tuple) -> FailureRecord:
    fields = [_text(value, default) for value, default in zip(row[:-1], _TEXT_DEFAULTS)]
    retry = row[-1]
    if retry is None:
        retry = 0
    if type(retry) is not int or retry < 0:
        raise ValueError("unsupported failed_queue retry count")
    return FailureRecord(*fields, retry)


def read_failed_records(db_path: Path, *, immutable: bool = False) -> tuple[FailureRecord, ...]:
    """Read all original failures, newest first, from an explicitly named DB.

    A schema mismatch, corrupt row, locked database or excessive result raises
    an exception; none becomes a misleading empty/partial success. Connections
    are thread-confined, opened with SQLite mode=ro, and always closed here.
    """
    if db_path is None or not str(db_path).strip():
        raise ValueError("explicit failed queue path required")
    path = Path(db_path).absolute()
    if not path.is_file():
        raise FileNotFoundError("failed queue database is missing")
    if type(immutable) is not bool:
        raise ValueError('immutable snapshot flag must be boolean')
    if immutable:
        # The opt-in managed retry uses a static copied SQLite database.
        # Never let WAL sidecars silently disappear from the read snapshot,
        # and do not let SQLite create/write a shared-memory sidecar.
        if (path.is_symlink() or any(Path(str(path) + suffix).exists()
                                     for suffix in ('-wal', '-shm', '-journal'))):
            raise ValueError('immutable failed queue source must be a standalone copy')
    uri = path.as_uri() + ('?mode=ro&immutable=1' if immutable else '?mode=ro')
    conn = sqlite3.connect(uri, uri=True, timeout=1.0)
    try:
        # SQLite's per-value/row limit rejects a truly giant field before it
        # can be decoded and materialized as an unbounded Python string.
        if hasattr(conn, "setlimit"):
            conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, _MAX_SQLITE_VALUE_BYTES)
        conn.execute("PRAGMA query_only=ON")
        schema = {column[1] for column in conn.execute("PRAGMA table_info(failed_queue)")}
        if not set(CSV_COLUMNS) <= schema:
            raise ValueError("unsupported failed_queue schema")

        # Ensure the count, bounds checks and iteration observe one WAL
        # snapshot. Check size before ORDER BY can materialize many row payloads.
        conn.execute("BEGIN")
        if conn.execute("SELECT 1 FROM failed_queue LIMIT 1 OFFSET ?", (MAX_RECORDS,)).fetchone():
            raise ValueError("failed_queue record limit exceeded")
        if conn.execute(_OVERSIZED, (_MAX_FIELD_CHARS,) * len(_TEXT_COLUMNS)).fetchone():
            raise ValueError("failed_queue field size exceeded")
        aggregate = conn.execute(_TOTAL_BYTES).fetchone()[0] or 0
        if aggregate > MAX_TOTAL_UTF8_BYTES:
            raise ValueError("failed_queue total payload limit exceeded")

        # Consume a cursor row at a time; never accumulate raw SQLite tuples.
        # Keep a second running limit as defense against unexpected row types.
        result: list[FailureRecord] = []
        seen: set[str] = set()
        total_bytes = 0
        for row in conn.execute(_SELECT, (MAX_RECORDS + 1,)):
            if len(result) == MAX_RECORDS:
                raise ValueError("failed_queue record limit exceeded")
            record = _row_to_record(row)
            if record.work_id in seen:
                raise ValueError("duplicate failed_queue work IDs")
            seen.add(record.work_id)
            total_bytes += sum(len(field.encode("utf-8")) for field in row[:-1] if type(field) is str)
            if total_bytes > MAX_TOTAL_UTF8_BYTES:
                raise ValueError("failed_queue total payload limit exceeded")
            result.append(record)
        return tuple(result)
    finally:
        # sqlite3 Connection context managers do not close connections.
        conn.close()
