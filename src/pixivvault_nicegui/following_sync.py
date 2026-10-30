"""Explicit follow-list GET synchronization into one owned, account-scoped DB.

No Cookie file, original DB, live client construction or remote mutation is used.
The caller supplies its existing read-only reader and explicitly requests sync.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import re
import sqlite3
from threading import RLock
import time

from .bookmark_remote_adapter import thumbnail_url
from .fs_paths import native_io_path, sqlite_readonly_uri
from .managed_downloads import _safe_path, _read_bounded, _write, _fsync_dir
from .pixiv_read_client import MAX_FOLLOWED_AUTHORS, ReadDeadlineExceeded, read_deadline_scope
from .request_policy import request_scope
from .ugoira_storage import _owner_lock, _OWNER

MARKER = '.pixivvault-following-sync.json'
IDENTITY = b'{"application":"pixivvault-nicegui","purpose":"following-sync","version":1}\n'
DATABASE = 'following.sqlite3'
APPLICATION_ID = 0x50564631
MAX_AUTHORS = MAX_FOLLOWED_AUTHORS * 2
MAX_DB_BYTES = 256 * 1024 * 1024
_ID = re.compile(r'[1-9][0-9]{0,19}\Z')
_COLUMNS = ('user_id', 'name', 'account', 'profile_img', 'restrict_value',
            'follow_order', 'is_following', 'is_favorite', 'is_zipped', 'last_downloaded')


class FollowingSyncError(RuntimeError):
    code = 'following_sync_failed'
    def __init__(self):
        super().__init__(self.code)


class FollowingSyncConflict(FollowingSyncError):
    code = 'following_sync_conflict'


class FollowingSyncCancelled(FollowingSyncError):
    code = 'following_sync_cancelled'


class FollowingSyncTimedOut(FollowingSyncError):
    code = 'following_sync_timeout'


@dataclass(frozen=True, slots=True)
class FollowingAuthor:
    user_id: str
    name: str
    account: str
    profile_img: str
    restrict: int
    follow_order: int
    is_following: bool = True
    is_favorite: bool = False
    is_zipped: bool = False
    last_downloaded: str | None = None


@dataclass(frozen=True, slots=True)
class FollowingSnapshot:
    account_id: str
    revision: int
    last_synced_at: str | None
    authors: tuple[FollowingAuthor, ...]
    @property
    def following(self) -> tuple[FollowingAuthor, ...]:
        return tuple(row for row in self.authors if row.is_following)


def _identifier(value):
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise FollowingSyncError()
    return value


def _text(value, maximum=256):
    if (type(value) is not str or len(value) > maximum
            or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
        raise FollowingSyncError()
    return value


def _profile(value):
    value = _text(value, 4096)
    if value:
        try:
            thumbnail_url(value)
        except (ValueError, TypeError):
            raise FollowingSyncError() from None
    return value


def _revision(value):
    if value is not None and (type(value) is not int or value < 0):
        raise FollowingSyncError()
    return value


def _stamp(value):
    if value is None:
        return None
    _text(value, 64)
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
            raise ValueError()
    except (ValueError, TypeError):
        raise FollowingSyncError() from None
    return value


class FollowingSyncStore:
    """One process owner; views share this instance and synchronize with CAS."""
    def __init__(self, root):
        self._lock = RLock()
        self._closed = False
        self._connection = self._owner = None
        try:
            raw = Path(root)
            if not raw.is_absolute() or '..' in raw.parts or raw == raw.parent:
                raise FollowingSyncError()
            self.root = native_io_path(raw)
            self.path = self.root / DATABASE
            _safe_path(self.root)
            if not self.root.exists():
                if not self.root.parent.is_dir():
                    raise FollowingSyncError()
                self.root.mkdir(mode=0o700)
                _write(self.root / MARKER, IDENTITY)
                _fsync_dir(self.root)
            self._check_root()
            owner = _owner_lock(self.root)
            owner.__enter__()
            self._owner = owner
            _safe_path(self.path, file=True)
            existed = self.path.exists()
            if existed:
                if self.path.stat().st_size > MAX_DB_BYTES:
                    raise FollowingSyncError()
                with closing(sqlite3.connect(sqlite_readonly_uri(self.path), uri=True)) as check:
                    self._verify_schema(check)
            else:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY
                                     | getattr(os, 'O_NOFOLLOW', 0), 0o600)
                os.close(descriptor)
            self._connection = sqlite3.connect(str(self.path), timeout=0.1,
                isolation_level=None, check_same_thread=False)
            self._connection.execute('PRAGMA foreign_keys=ON')
            self._connection.execute('PRAGMA synchronous=FULL')
            if not existed:
                self._connection.executescript(f'''
                    BEGIN IMMEDIATE;
                    PRAGMA application_id={APPLICATION_ID};
                    PRAGMA user_version=1;
                    CREATE TABLE following_accounts (
                        account_id TEXT PRIMARY KEY,
                        revision INTEGER NOT NULL CHECK(revision>=0),
                        last_synced_at TEXT);
                    CREATE TABLE following_authors (
                        account_id TEXT NOT NULL,
                        user_id TEXT NOT NULL,
                        name TEXT NOT NULL DEFAULT '',
                        account TEXT NOT NULL DEFAULT '',
                        profile_img TEXT NOT NULL DEFAULT '',
                        restrict_value INTEGER NOT NULL CHECK(restrict_value IN (0,1)),
                        follow_order INTEGER NOT NULL,
                        is_following INTEGER NOT NULL DEFAULT 1 CHECK(is_following IN (0,1)),
                        is_favorite INTEGER NOT NULL DEFAULT 0 CHECK(is_favorite IN (0,1)),
                        is_zipped INTEGER NOT NULL DEFAULT 0 CHECK(is_zipped IN (0,1)),
                        last_downloaded TEXT,
                        PRIMARY KEY(account_id,user_id),
                        FOREIGN KEY(account_id) REFERENCES following_accounts(account_id));
                    COMMIT;
                ''')
            self._verify_schema(self._connection)
            metadata = self.path.lstat()
            self._identity = metadata.st_dev, metadata.st_ino
            metadata = self.root.lstat()
            self._root_identity = metadata.st_dev, metadata.st_ino
        except Exception:
            try:
                self.close()
            except Exception:
                pass
            raise FollowingSyncError() from None

    def _check_root(self):
        _safe_path(self.root)
        if _read_bounded(self.root / MARKER, len(IDENTITY)) != IDENTITY:
            raise FollowingSyncError()
        allowed = {MARKER, _OWNER, DATABASE, DATABASE + '-journal'}
        for child in self.root.iterdir():
            if child.name not in allowed:
                raise FollowingSyncError()
            _safe_path(child, file=True)

    @staticmethod
    def _verify_schema(connection):
        if (connection.execute('PRAGMA application_id').fetchone() != (APPLICATION_ID,)
                or connection.execute('PRAGMA user_version').fetchone() != (1,)):
            raise FollowingSyncError()
        tables = {r[0] for r in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if tables != {'following_accounts', 'following_authors'}:
            raise FollowingSyncError()
        account_columns = tuple(r[1] for r in connection.execute('PRAGMA table_info(following_accounts)'))
        author_columns = tuple(r[1] for r in connection.execute('PRAGMA table_info(following_authors)'))
        if (account_columns != ('account_id', 'revision', 'last_synced_at')
                or author_columns != ('account_id', *_COLUMNS)):
            raise FollowingSyncError()

    def _ensure_open(self):
        if self._closed or self._connection is None:
            raise FollowingSyncError()
        self._check_root()
        metadata = self.root.lstat()
        if (metadata.st_dev, metadata.st_ino) != self._root_identity:
            raise FollowingSyncError()
        _safe_path(self.path, file=True)
        metadata = self.path.lstat()
        if ((metadata.st_dev, metadata.st_ino) != self._identity
                or metadata.st_size > MAX_DB_BYTES):
            raise FollowingSyncError()

    def _read_snapshot(self, account_id):
        state = self._connection.execute(
            'SELECT revision,last_synced_at FROM following_accounts WHERE account_id=?',
            (account_id,)).fetchone()
        rows = self._connection.execute(
            'SELECT ' + ','.join(_COLUMNS) +
            ' FROM following_authors WHERE account_id=? ORDER BY follow_order,user_id',
            (account_id,)).fetchmany(MAX_AUTHORS + 1)
        if len(rows) > MAX_AUTHORS or (state is None and rows):
            raise FollowingSyncError()
        if state is None:
            return FollowingSnapshot(account_id, 0, None, ())
        revision, stamp = state
        if type(revision) is not int or revision < 0:
            raise FollowingSyncError()
        authors = []
        for uid, name, account, profile, restrict, order, active, favorite, zipped, downloaded in rows:
            if (type(restrict) is not int or restrict not in (0, 1)
                    or type(order) is not int
                    or any(type(v) is not int or v not in (0, 1) for v in (active, favorite, zipped))):
                raise FollowingSyncError()
            authors.append(FollowingAuthor(
                _identifier(uid), _text(name), _text(account), _profile(profile), restrict, order,
                bool(active), bool(favorite), bool(zipped),
                None if downloaded is None else _text(downloaded, 128)))
        return FollowingSnapshot(account_id, revision, _stamp(stamp), tuple(authors))

    def _abort(self, error):
        try:
            if self._connection is not None and self._connection.in_transaction:
                self._connection.execute('ROLLBACK')
        except Exception:
            try:
                self.close()
            except Exception:
                pass
            raise FollowingSyncError() from None
        if isinstance(error, FollowingSyncError):
            raise error from None
        if isinstance(error, Exception):
            raise FollowingSyncError() from None
        raise error

    def snapshot(self, account_id):
        account_id = _identifier(account_id)
        with self._lock:
            try:
                self._ensure_open()
                self._connection.execute('BEGIN')
                result = self._read_snapshot(account_id)
                self._connection.execute('COMMIT')
                return result
            except BaseException as error:
                self._abort(error)

    def _commit(self, account_id, expected_revision, mutate, *, synced_at=None, checkpoint=lambda: None):
        with self._lock:
            try:
                self._ensure_open()
                checkpoint()
                self._connection.execute('BEGIN IMMEDIATE')
                before = self._read_snapshot(account_id)
                if expected_revision is not None and before.revision != expected_revision:
                    raise FollowingSyncConflict()
                checkpoint()
                self._connection.execute(
                    'INSERT INTO following_accounts VALUES(?,0,NULL) ON CONFLICT(account_id) DO NOTHING',
                    (account_id,))
                if not mutate(self._connection, before):
                    self._connection.execute('ROLLBACK')
                    return before
                if synced_at is None:
                    self._connection.execute(
                        'UPDATE following_accounts SET revision=revision+1 WHERE account_id=?', (account_id,))
                else:
                    self._connection.execute(
                        'UPDATE following_accounts SET revision=revision+1,last_synced_at=? WHERE account_id=?',
                        (synced_at, account_id))
                result = self._read_snapshot(account_id)
                checkpoint()
                self._connection.execute('COMMIT')
                return result
            except BaseException as error:
                self._abort(error)

    def sync(self, reader, *, expected_revision=None, cancelled=None, timeout=120):
        expected_revision = _revision(expected_revision)
        if (type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0
                or (cancelled is not None and not callable(cancelled))):
            raise FollowingSyncError()
        deadline = time.monotonic() + timeout

        def checkpoint():
            if self._closed:
                raise FollowingSyncError()
            if cancelled is not None and cancelled():
                raise FollowingSyncCancelled()
            if time.monotonic() >= deadline:
                raise FollowingSyncTimedOut()

        try:
            with request_scope(checkpoint=checkpoint, allow_retry=False), read_deadline_scope(deadline):
                checkpoint()
                account_id = _identifier(reader.require_identity())
                before = self.snapshot(account_id)
                if expected_revision is not None and before.revision != expected_revision:
                    raise FollowingSyncConflict()
                candidate = {}
                for restrict, visibility in enumerate(('show', 'hide')):
                    checkpoint()
                    rows = reader.get_following_users_detailed(account_id, visibility)
                    checkpoint()
                    for index, row in enumerate(rows):
                        checkpoint()
                        if index >= MAX_FOLLOWED_AUTHORS or type(row) is not dict:
                            raise FollowingSyncError()
                        if set(row) != {'user_id', 'name', 'account', 'profile_img'}:
                            raise FollowingSyncError()
                        uid = _identifier(row['user_id'])
                        order = candidate[uid].follow_order if uid in candidate else len(candidate)
                        candidate[uid] = FollowingAuthor(uid, _text(row['name']), _text(row['account']),
                            _profile(row['profile_img']), restrict, order)
                        if len(candidate) > MAX_AUTHORS:
                            raise FollowingSyncError()
                checkpoint()
                if _identifier(reader.require_identity()) != account_id:
                    raise FollowingSyncError()
                checkpoint()

                def publish(connection, previous):
                    connection.execute('UPDATE following_authors SET is_following=0 WHERE account_id=?',
                                       (account_id,))
                    for row in candidate.values():
                        checkpoint()
                        connection.execute('''INSERT INTO following_authors
                            (account_id,user_id,name,account,profile_img,restrict_value,follow_order,is_following)
                            VALUES(?,?,?,?,?,?,?,1)
                            ON CONFLICT(account_id,user_id) DO UPDATE SET
                            name=excluded.name,account=excluded.account,profile_img=excluded.profile_img,
                            restrict_value=excluded.restrict_value,follow_order=excluded.follow_order,is_following=1''',
                            (account_id,row.user_id,row.name,row.account,row.profile_img,row.restrict,row.follow_order))
                    return True
                return self._commit(account_id, before.revision, publish,
                    synced_at=datetime.now(timezone.utc).isoformat(), checkpoint=checkpoint)
        except FollowingSyncError:
            raise
        except ReadDeadlineExceeded:
            raise FollowingSyncTimedOut() from None
        except Exception:
            raise FollowingSyncError() from None

    def record_followed(self, account_id, user_id, name='', restrict=0, *, expected_revision=None):
        account_id, user_id = _identifier(account_id), _identifier(user_id)
        name = _text(name)
        expected_revision = _revision(expected_revision)
        if type(restrict) is not int or restrict not in (0, 1):
            raise FollowingSyncError()
        def record(connection, before):
            existing = next((row for row in before.authors if row.user_id == user_id), None)
            if existing is not None:
                if existing.is_following and existing.restrict == restrict:
                    return False
                connection.execute('''UPDATE following_authors SET is_following=1,restrict_value=?
                    WHERE account_id=? AND user_id=?''', (restrict, account_id, user_id))
            else:
                order = min((row.follow_order for row in before.authors), default=1) - 1
                connection.execute('''INSERT INTO following_authors
                    (account_id,user_id,name,restrict_value,follow_order,is_following)
                    VALUES(?,?,?,?,?,1)''', (account_id, user_id, name, restrict, order))
            return True
        return self._commit(account_id, expected_revision, record)

    def record_unfollowed(self, account_id, user_id, *, expected_revision=None):
        account_id, user_id = _identifier(account_id), _identifier(user_id)
        expected_revision = _revision(expected_revision)
        def record(connection, before):
            if not any(row.user_id == user_id and row.is_following for row in before.authors):
                return False
            connection.execute('UPDATE following_authors SET is_following=0 WHERE account_id=? AND user_id=?',
                               (account_id, user_id))
            return True
        return self._commit(account_id, expected_revision, record)

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                if self._connection is not None:
                    self._connection.close()
            finally:
                self._connection = None
                owner, self._owner = self._owner, None
                if owner is not None:
                    owner.__exit__(None, None, None)
