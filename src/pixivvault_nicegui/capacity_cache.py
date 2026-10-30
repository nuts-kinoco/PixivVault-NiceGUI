"""Explicitly injected SQLite connection for per-author capacity results.

The caller owns the connection and its isolated storage lifecycle. This module
never opens a legacy database, chooses a directory, or performs a scan.
"""
from threading import RLock
from pathlib import Path
import hashlib
import os
import sqlite3
from .managed_downloads import _safe_path
from .fs_paths import native_io_path

class CommitOutcomeUnknown(RuntimeError):
    """SQLite did not expose whether a failed COMMIT became durable."""

class CapacityCache:
    def __init__(self, connection):
        self._db=connection
        self._lock=RLock()

    @classmethod
    def open_owned(cls, directory):
        """Open a marked cache inside an already owned preferences directory."""
        directory=Path(directory)
        if not directory.is_absolute() or not directory.is_dir():raise ValueError('capacity cache directory unavailable')
        _safe_path(directory)
        path=directory/'legacy-capacity.sqlite3'
        _safe_path(path,file=True)
        existed=path.exists()
        db=sqlite3.connect(str(native_io_path(path)),check_same_thread=False)
        try:
            if existed:
                marker=db.execute('SELECT kind FROM capacity_identity').fetchone()
                if marker!=('pixivvault-legacy-capacity-v1',):raise ValueError('unowned capacity cache')
            else:
                with db:
                    db.execute('CREATE TABLE capacity_identity(kind TEXT NOT NULL)')
                    db.execute("INSERT INTO capacity_identity VALUES('pixivvault-legacy-capacity-v1')")
            result=cls(db);result.initialize();return result
        except BaseException:
            db.close();raise

    def close(self):
        with self._lock:self._db.close()

    def initialize(self):
        with self._lock, self._db:
            self._db.execute("CREATE TABLE IF NOT EXISTS author_capacity (author_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, bytes INTEGER)")

    def bind_root(self, root):
        """Bind the injected cache to one root without storing the raw path."""
        root=Path(root)
        if not root.is_absolute():raise ValueError('absolute root required')
        identity=hashlib.sha256(os.path.normcase(str(root.resolve())).encode('utf-8')).hexdigest()
        with self._lock, self._db:
            self._db.execute('CREATE TABLE IF NOT EXISTS capacity_root_identity (id INTEGER PRIMARY KEY CHECK(id=1), digest TEXT NOT NULL)')
            row=self._db.execute('SELECT digest FROM capacity_root_identity WHERE id=1').fetchone()
            if row is None:
                if self._db.execute('SELECT count(*) FROM author_capacity').fetchone()[0]:
                    raise ValueError('unbound nonempty capacity cache')
                self._db.execute('INSERT INTO capacity_root_identity VALUES (1,?)',(identity,))
            elif row[0]!=identity:raise ValueError('capacity cache root mismatch')

    def snapshot(self, author_id):
        if type(author_id) is not str or not author_id:raise ValueError('invalid author')
        with self._lock:
            row=self._db.execute('SELECT revision,bytes FROM author_capacity WHERE author_id=?',(author_id,)).fetchone()
            return (0,None) if row is None else (row[0],row[1])

    def commit_batch(self, values, revisions, *, decide):
        """Stage CAS updates; decide cancellation just before atomic COMMIT."""
        values=dict(values);revisions=dict(revisions)
        if not callable(decide) or set(values)!=set(revisions):raise ValueError('invalid capacity batch')
        for author_id,value in values.items():
            revision=revisions[author_id]
            if type(author_id) is not str or not author_id or type(revision) is not int or revision<0:
                raise ValueError('invalid capacity identity')
            if value is not None and (type(value) is not int or value<0):raise ValueError('invalid successful capacity')
        with self._lock:
            if self._db.in_transaction:raise RuntimeError('capacity transaction already active')
            self._db.execute('BEGIN IMMEDIATE')
            committing=False
            try:
                committed={}
                for author_id,value in values.items():
                    if value is None:continue
                    revision=revisions[author_id]
                    if revision==0:self._db.execute('INSERT OR IGNORE INTO author_capacity(author_id,revision,bytes) VALUES (?,0,NULL)',(author_id,))
                    row=self._db.execute('UPDATE author_capacity SET revision=revision+1,bytes=? WHERE author_id=? AND revision=?',(value,author_id,revision))
                    if row.rowcount==1:committed[author_id]=value
                if not committed or not decide():
                    self._db.execute('ROLLBACK');return {}
                committing=True
                self._db.execute('COMMIT')
                return committed
            except BaseException as error:
                if self._db.in_transaction:
                    try:self._db.execute('ROLLBACK')
                    except BaseException as rollback_error:
                        raise CommitOutcomeUnknown('capacity rollback outcome unknown') from rollback_error
                elif committing:
                    raise CommitOutcomeUnknown('capacity commit outcome unknown') from error
                raise

    def commit_success(self, author_id, revision, measured_bytes):
        if type(author_id) is not str or not author_id or type(revision) is not int or revision<0:
            raise ValueError('invalid capacity identity')
        if type(measured_bytes) is not int or measured_bytes<0:
            raise ValueError('invalid successful capacity')
        with self._lock, self._db:
            if revision==0:
                self._db.execute('INSERT OR IGNORE INTO author_capacity(author_id,revision,bytes) VALUES (?,0,NULL)',(author_id,))
            result=self._db.execute('UPDATE author_capacity SET revision=revision+1,bytes=? WHERE author_id=? AND revision=?',(measured_bytes,author_id,revision))
            return result.rowcount==1
