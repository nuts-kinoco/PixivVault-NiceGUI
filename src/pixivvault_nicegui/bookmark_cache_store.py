"""Application-owned durable bookmark snapshot, never the original DB."""
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from threading import RLock
import json
import re
import sqlite3

from .bookmark_remote_adapter import parse_bookmark
from .fs_paths import native_io_path
from .managed_downloads import _safe_path


class BookmarkCacheStore:
    def __init__(self, directory):
        directory = Path(directory)
        if not directory.is_absolute() or not directory.is_dir():
            raise ValueError('bookmark cache directory unavailable')
        _safe_path(directory)
        path = directory / 'bookmark-cache.sqlite3'
        _safe_path(path, file=True)
        existed = path.exists()
        self._lock = RLock()
        self._db = sqlite3.connect(str(native_io_path(path)), check_same_thread=False, isolation_level=None)
        try:
            if existed:
                if self._db.execute('SELECT kind FROM bookmark_cache_identity').fetchone() != ('pixivvault-bookmark-cache-v1',):
                    raise ValueError('unowned bookmark cache')
            else:
                self._db.executescript("BEGIN IMMEDIATE; CREATE TABLE bookmark_cache_identity(kind TEXT NOT NULL); INSERT INTO bookmark_cache_identity VALUES('pixivvault-bookmark-cache-v1'); CREATE TABLE snapshot(id INTEGER PRIMARY KEY CHECK(id=1), account TEXT NOT NULL, fetched_at TEXT NOT NULL, payload TEXT NOT NULL); CREATE TABLE cache_revision(value INTEGER NOT NULL); INSERT INTO cache_revision VALUES(0); COMMIT;")
        except BaseException:
            self._db.close()
            raise

    def has_snapshot(self):
        with self._lock:
            return self._db.execute('SELECT 1 FROM snapshot WHERE id=1').fetchone() is not None

    def revision(self):
        with self._lock:
            row = self._db.execute('SELECT value FROM cache_revision').fetchone()
            if row is None or type(row[0]) is not int or row[0] < 0:
                raise ValueError('invalid bookmark cache revision')
            return row[0]

    def read(self, account):
        with self._lock:
            row = self._db.execute('SELECT account,fetched_at,payload FROM snapshot WHERE id=1').fetchone()
        if row is None or row[0] != account:
            return None
        stamp, payload = row[1:]
        if not isinstance(stamp, str): raise ValueError('invalid bookmark timestamp')
        datetime.fromisoformat(stamp)
        if not isinstance(payload, str) or len(payload) > 128 * 1024 * 1024:
            raise ValueError('invalid bookmark payload')
        envelope = json.loads(payload)
        # Older snapshots did not bind account inside the payload. Keep them
        # intact until a complete verified refresh, but never expose them.
        if type(envelope) is list:
            return None
        if type(envelope) is not dict or set(envelope) != {'account', 'records'} or envelope['account'] != account:
            raise ValueError('bookmark account binding invalid')
        rows = envelope['records']
        if type(rows) is not list or len(rows) > 200000:
            raise ValueError('invalid bookmark rows')
        records, seen = [], set()
        for item in rows:
            if type(item) is not dict: raise ValueError('invalid bookmark row')
            kind, visibility = item.get('kind'), item.get('visibility')
            raw = dict(id=item.get('remote_id'), title=item.get('title'), user_id=item.get('author_id'),
                       user_name=item.get('author_name'), tags=item.get('tags'), thumb_url=item.get('thumbnail'),
                       create_date=item.get('created_at'), page_count=item.get('page_count'),
                       type=item.get('illustration_type'), x_restrict=item.get('x_restrict'),
                       bookmark_count=item.get('bookmark_count'))
            record = parse_bookmark(raw, kind=kind, visibility=visibility)
            known, viewed = item.get('page_count_known'), item.get('viewed')
            if record is None or type(known) is not bool or (viewed is not None and type(viewed) is not bool):
                raise ValueError('invalid bookmark row')
            record = replace(record, page_count_known=known, viewed=viewed)
            if record.work_id in seen: raise ValueError('duplicate bookmark row')
            seen.add(record.work_id)
            records.append(record)
        return tuple(records), stamp

    def write(self, account, stamp, records, *, expected_revision):
        if type(account) is not str or re.fullmatch(r'[1-9][0-9]{0,19}', account) is None:
            raise ValueError('invalid bookmark account')
        datetime.fromisoformat(stamp)
        payload = json.dumps({'account': account, 'records': [asdict(record) for record in records]},
                             ensure_ascii=False, separators=(',', ':'))
        with self._lock:
            self._db.execute('BEGIN IMMEDIATE')
            try:
                row = self._db.execute('SELECT value FROM cache_revision').fetchone()
                if row is None or row[0] != expected_revision:
                    self._db.execute('ROLLBACK')
                    return False
                self._db.execute('INSERT INTO snapshot VALUES (1,?,?,?) ON CONFLICT(id) DO UPDATE SET account=excluded.account,fetched_at=excluded.fetched_at,payload=excluded.payload', (account, stamp, payload))
                self._db.execute('UPDATE cache_revision SET value=value+1')
                self._db.execute('COMMIT')
                return True
            except BaseException:
                if self._db.in_transaction:
                    self._db.execute('ROLLBACK')
                else:
                    # A COMMIT may have reached disk even when its result was lost.
                    # Read back the exact candidate; never repeat the write.
                    try:
                        row = self._db.execute('SELECT account,fetched_at,payload FROM snapshot WHERE id=1').fetchone()
                        revision = self._db.execute('SELECT value FROM cache_revision').fetchone()
                        if row == (account, stamp, payload) and revision == (expected_revision + 1,):
                            return True
                    except Exception:
                        pass
                raise

    def close(self):
        with self._lock:
            self._db.close()
