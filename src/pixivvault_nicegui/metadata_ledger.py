"""Opt-in, isolated metadata side ledger; never opens the original PixivVault.

M2 is an offline persistence primitive, not a background job or HTTP client.
The caller must separately verify the active managed receipt immediately
before a result is committed. Recorded metadata is *not* a replacement for
the immutable managed artwork receipt.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from threading import RLock
from typing import Callable
from uuid import uuid4
from datetime import datetime

from .managed_downloads import IDENTITY, MARKER, _json_unique, _read_bounded, _safe_path
from .metadata_policy import (
    FailureDecision, MetadataCandidate, MetadataFailureClass, MAX_META_ATTEMPTS,
    sanitize_work_meta,
)


_SHA = re.compile(r'[a-f0-9]{64}\Z')
_ID = re.compile(r'[1-9][0-9]{0,19}\Z')
_NUMERIC_FIELDS = frozenset({
    'bookmark_count', 'like_count', 'view_count', 'comment_count',
    'x_restrict', 'ai_type', 'illust_width', 'illust_height',
})
_STRING_FIELDS = {'upload_date': 128, 'user_account': 64}
_FIELDS = frozenset(_NUMERIC_FIELDS | set(_STRING_FIELDS) |
                    {'meta_source', 'meta_fetched_at', 'work_meta'})
_PROMOTED_RAW_KEYS = frozenset({
    'bookmarkCount', 'likeCount', 'viewCount', 'commentCount', 'xRestrict',
    'aiType', 'width', 'height', 'uploadDate', 'userAccount',
})
_EXCLUDED_RAW_KEYS = frozenset({'userIllusts', 'zoneConfig', 'extraData'})
MAX_METADATA_BYTES = 65536


class MetadataLedgerError(ValueError):
    """Sanitized metadata-state rejection; never embeds paths or private data."""


@dataclass(frozen=True, slots=True)
class MetadataSettings:
    enabled: bool
    revision: int


@dataclass(frozen=True, slots=True)
class MetadataEntry:
    work_id: str
    base_receipt_sha256: str
    state: str
    attempts: int


@dataclass(frozen=True, slots=True)
class MetadataProgress:
    """Side-ledger state counts, not a verified count of currently active works."""
    pending: int = 0
    in_flight: int = 0
    done: int = 0
    gave_up: int = 0
    unknown: int = 0
    blocked: int = 0

    @property
    def total(self) -> int:
        return (self.pending + self.in_flight + self.done + self.gave_up
                + self.unknown + self.blocked)


def _fingerprint(value: str) -> str:
    if type(value) is not str or _SHA.fullmatch(value) is None:
        raise MetadataLedgerError('invalid managed generation fingerprint')
    return value


def _work_id(value: str) -> str:
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise MetadataLedgerError('invalid metadata work id')
    return value


def _safe_metadata(meta: dict) -> bytes:
    """Accept bounded extracted metadata, never response envelopes or excluded blobs."""
    if (type(meta) is not dict or not meta or set(meta) - _FIELDS
            or 'bookmark_count' not in meta):
        raise MetadataLedgerError('unsupported metadata payload')
    clean = {}
    for key, value in meta.items():
        if key in _NUMERIC_FIELDS:
            if type(value) is not int or value < 0 or value > 2_147_483_647:
                raise MetadataLedgerError('metadata field rejected')
            if key == 'x_restrict' and value not in (0, 1, 2):
                raise MetadataLedgerError('metadata field rejected')
            if key in ('illust_width', 'illust_height') and value == 0:
                raise MetadataLedgerError('metadata field rejected')
            clean[key] = value
        elif key in _STRING_FIELDS:
            if (type(value) is not str or len(value) > _STRING_FIELDS[key]
                    or any(ord(char) < 32 or ord(char) == 127 for char in value)):
                raise MetadataLedgerError('metadata field rejected')
            clean[key] = value
        elif key == 'meta_source':
            if type(value) is not str or value != 'illust_detail':
                raise MetadataLedgerError('metadata source rejected')
            clean[key] = value
        elif key == 'meta_fetched_at':
            if type(value) is not str or len(value) > 64:
                raise MetadataLedgerError('metadata timestamp rejected')
            try:
                if datetime.fromisoformat(value).tzinfo is None:
                    raise ValueError()
            except ValueError:
                raise MetadataLedgerError('metadata timestamp rejected') from None
            clean[key] = value
        elif key == 'work_meta':
            if type(value) is not dict or set(value) & _EXCLUDED_RAW_KEYS:
                raise MetadataLedgerError('metadata object rejected')
            try:
                clean[key] = sanitize_work_meta(value)
            except ValueError:
                raise MetadataLedgerError('metadata object rejected') from None
    payload = json.dumps(clean, sort_keys=True, separators=(',', ':'),
                         ensure_ascii=True, allow_nan=False).encode('ascii')
    if len(payload) > MAX_METADATA_BYTES:
        raise MetadataLedgerError('metadata payload too large')
    return payload


class ManagedMetadataLedger:
    """Process-owned SQLite writer scoped to a *previously marked* managed root.

    No new storage is initialized merely because an app starts. Opening this
    class is itself an explicit opt-in provisioning action and defaults OFF.
    """

    def __init__(self, managed_root: Path, *, active_receipt_sha256: Callable[[str], str | None]):
        if not callable(active_receipt_sha256):
            raise MetadataLedgerError('active managed receipt verifier required')
        self._active_receipt_sha256 = active_receipt_sha256
        self._epoch = uuid4().hex
        root = Path(managed_root)
        if (not root.is_absolute() or root.parent == root or not root.is_dir()):
            raise MetadataLedgerError('existing managed root required')
        try:
            _safe_path(root)
        except Exception:
            raise MetadataLedgerError('managed root path rejected') from None
        marker = root / MARKER
        try:
            if json.loads(_read_bounded(marker, 4096).decode('utf-8'),
                          object_pairs_hook=_json_unique) != IDENTITY:
                raise MetadataLedgerError('foreign managed root rejected')
        except (OSError, ValueError, UnicodeError, TypeError):
            raise MetadataLedgerError('managed root marker rejected') from None
        self.path = root / '.pixivvault-managed-metadata.sqlite3'
        lock_path = root / '.pixivvault-managed-metadata.lock'
        try:
            _safe_path(self.path, file=True)
            _safe_path(lock_path, file=True)
        except Exception:
            raise MetadataLedgerError('managed metadata path rejected') from None
        self._lock = RLock()
        self._closed = False
        self._owner = None
        try:
            self._owner = lock_path.open('a+b')
            self._owner.seek(0)
            if os.name == 'nt':
                import msvcrt
                if not self._owner.read(1):
                    self._owner.write(b'0')
                    self._owner.flush()
                self._owner.seek(0)
                msvcrt.locking(self._owner.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._owner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._db = sqlite3.connect(
                self.path, timeout=2, check_same_thread=False, isolation_level=None)
            self._db.execute('PRAGMA synchronous=FULL')
            self._db.execute('PRAGMA busy_timeout=2000')
            version = self._db.execute('PRAGMA user_version').fetchone()[0]
            tables = {row[0] for row in self._db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            expected_tables = {'settings', 'metadata', 'superseded'}
            if type(version) is not int or version not in (0, 1):
                raise MetadataLedgerError('unsupported metadata ledger version')
            # M2 shipped the identical table schema with user_version=0.
            # Migrate that exact schema without wiping existing states/leases.
            if ((version == 0 and tables and tables != expected_tables)
                    or (version == 1 and tables != expected_tables)):
                raise MetadataLedgerError('metadata ledger schema rejected')
            self._db.execute('BEGIN IMMEDIATE')
            self._db.execute('CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), revision INTEGER NOT NULL CHECK(revision>=1))')
            self._db.execute('INSERT OR IGNORE INTO settings VALUES (1,0,1)')
            self._db.execute("CREATE TABLE IF NOT EXISTS metadata (work_id TEXT PRIMARY KEY, base_sha TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('pending','in_flight','done','gave_up','unknown','blocked')), attempts INTEGER NOT NULL CHECK(attempts>=0), lease_id TEXT, lease_epoch TEXT, json_body BLOB, json_sha TEXT)")
            self._db.execute('CREATE TABLE IF NOT EXISTS superseded (work_id TEXT NOT NULL, base_sha TEXT NOT NULL, PRIMARY KEY(work_id,base_sha))')
            self._validate_schema_and_rows()
            self._db.execute('PRAGMA user_version=1')
            self._db.execute('COMMIT')
            self._validate_schema_and_rows()
            row = self._db.execute('SELECT enabled,revision FROM settings WHERE id=1').fetchone()
            if row is None or row[0] not in (0, 1) or row[1] < 1:
                raise MetadataLedgerError('invalid metadata settings')
        except BaseException as exc:
            if hasattr(self, '_db'):
                try:
                    self._db.execute('ROLLBACK')
                except sqlite3.Error:
                    pass
                try:
                    self._db.close()
                except sqlite3.Error:
                    pass
            if self._owner is not None:
                self._owner.close()
            if isinstance(exc, MetadataLedgerError):
                raise
            if isinstance(exc, (sqlite3.Error, OSError)):
                raise MetadataLedgerError('metadata ledger unavailable') from None
            raise

    def _validate_schema_and_rows(self) -> None:
        expected = {
            'settings': (('id', 'INTEGER', 1), ('enabled', 'INTEGER', 0),
                         ('revision', 'INTEGER', 0)),
            'metadata': (('work_id', 'TEXT', 1), ('base_sha', 'TEXT', 0),
                         ('state', 'TEXT', 0), ('attempts', 'INTEGER', 0),
                         ('lease_id', 'TEXT', 0), ('lease_epoch', 'TEXT', 0),
                         ('json_body', 'BLOB', 0), ('json_sha', 'TEXT', 0)),
            'superseded': (('work_id', 'TEXT', 1), ('base_sha', 'TEXT', 2)),
        }
        for table, columns in expected.items():
            actual = tuple((row[1], row[2].upper(), row[5])
                           for row in self._db.execute(f'PRAGMA table_info({table})'))
            if actual != columns:
                raise MetadataLedgerError('metadata ledger schema rejected')
        if self._db.execute('PRAGMA integrity_check').fetchone() != ('ok',):
            raise MetadataLedgerError('metadata ledger integrity rejected')
        settings = self._db.execute(
            'SELECT id,enabled,revision FROM settings ORDER BY id').fetchall()
        if (len(settings) != 1 or settings[0][0] != 1
                or type(settings[0][1]) is not int or settings[0][1] not in (0, 1)
                or type(settings[0][2]) is not int or settings[0][2] < 1):
            raise MetadataLedgerError('metadata settings rejected')
        allowed_states = {'pending', 'in_flight', 'done', 'gave_up', 'unknown', 'blocked'}
        for row in self._db.execute(
                'SELECT work_id,base_sha,state,attempts,lease_id,lease_epoch,json_body,json_sha '
                'FROM metadata'):
            wid, base, state, attempts, lease, epoch, body, body_sha = row
            _work_id(wid)
            _fingerprint(base)
            if type(state) is not str or state not in allowed_states:
                raise MetadataLedgerError('metadata row rejected')
            if type(attempts) is not int or not 0 <= attempts <= MAX_META_ATTEMPTS:
                raise MetadataLedgerError('metadata row rejected')
            if state == 'in_flight':
                if (type(lease) is not str or not re.fullmatch(r'[a-f0-9]{32}\Z', lease)
                        or type(epoch) is not str or not re.fullmatch(r'[a-f0-9]{32}\Z', epoch)):
                    raise MetadataLedgerError('metadata lease rejected')
            elif lease is not None or epoch is not None:
                raise MetadataLedgerError('metadata lease rejected')
            if state == 'done':
                if (type(body) is not bytes or len(body) > MAX_METADATA_BYTES
                        or type(body_sha) is not str
                        or hashlib.sha256(body).hexdigest() != body_sha):
                    raise MetadataLedgerError('metadata payload integrity rejected')
                try:
                    decoded = json.loads(body, object_pairs_hook=_json_unique)
                    if _safe_metadata(decoded) != body:
                        raise ValueError()
                except (ValueError, TypeError, UnicodeError):
                    raise MetadataLedgerError('metadata payload integrity rejected') from None
            elif body is not None or body_sha is not None:
                raise MetadataLedgerError('metadata payload state rejected')
        for wid, digest in self._db.execute('SELECT work_id,base_sha FROM superseded'):
            _work_id(wid)
            _fingerprint(digest)

    def _ensure_open(self) -> None:
        if self._closed:
            raise MetadataLedgerError('metadata ledger closed')

    def _active_digest(self, work_id: str) -> str | None:
        """Call the external pointer reader without holding the ledger lock."""
        try:
            value = self._active_receipt_sha256(work_id)
            return None if value is None else _fingerprint(value)
        except Exception:
            raise MetadataLedgerError('active managed receipt unavailable') from None

    def settings(self) -> MetadataSettings:
        with self._lock:
            self._ensure_open()
            row = self._db.execute('SELECT enabled,revision FROM settings WHERE id=1').fetchone()
            return MetadataSettings(bool(row[0]), row[1])

    def progress(self) -> MetadataProgress:
        """Aggregate only local side-ledger states; no Pixiv/receipt scans."""
        with self._lock:
            self._ensure_open()
            counts = {state: count for state, count in self._db.execute(
                'SELECT state,COUNT(*) FROM metadata GROUP BY state')}
            keys = {'pending', 'in_flight', 'done', 'gave_up', 'unknown', 'blocked'}
            if set(counts) - keys or any(type(n) is not int or n < 0 for n in counts.values()):
                raise MetadataLedgerError('metadata progress unavailable')
            return MetadataProgress(**counts)

    def configure(self, *, enabled: bool, expected_revision: int) -> MetadataSettings:
        if type(enabled) is not bool or type(expected_revision) is not int or expected_revision < 1:
            raise MetadataLedgerError('invalid metadata configuration')
        with self._lock:
            self._ensure_open()
            self._db.execute('BEGIN IMMEDIATE')
            try:
                prior = self.settings()
                if expected_revision != prior.revision:
                    raise MetadataLedgerError('metadata settings changed')
                self._db.execute('UPDATE settings SET enabled=?,revision=? WHERE id=1',
                                 (int(enabled), prior.revision + 1))
                if not enabled:
                    # Disable fences all accepted in-flight capabilities. A
                    # later enable never silently resumes the old operation.
                    self._db.execute(
                        "UPDATE metadata SET state='unknown',lease_id=NULL,lease_epoch=NULL "
                        "WHERE state='in_flight'")
                self._db.execute('COMMIT')
            except BaseException:
                self._db.execute('ROLLBACK')
                raise
            return self.settings()

    def entry(self, work_id: str) -> MetadataEntry | None:
        wid = _work_id(work_id)
        with self._lock:
            self._ensure_open()
            row = self._db.execute(
                'SELECT base_sha,state,attempts FROM metadata WHERE work_id=?', (wid,)
            ).fetchone()
            return MetadataEntry(wid, *row) if row is not None else None

    def offer(self, candidate: MetadataCandidate, base_receipt_sha256: str) -> bool:
        """Register a candidate only from a verified immutable snapshot.

        Does not implicitly enable background fetches or consume attempts.
        A changed generation cannot silently replace an in-flight operation.
        """
        if not isinstance(candidate, MetadataCandidate) or not candidate.needs_backfill:
            raise MetadataLedgerError('metadata candidate ineligible')
        digest = _fingerprint(base_receipt_sha256)
        # Never invoke a storage-provider callback while holding the ledger
        # lock: managed downloads and metadata must not form a lock-order cycle.
        if self._active_digest(candidate.work_id) != digest:
            raise MetadataLedgerError('offered generation is not active')
        with self._lock:
            self._ensure_open()
            # An offer is evidence of the *current* active managed pointer.
            # A stale author scan may not overwrite a newer completed overlay.
            self._db.execute('BEGIN IMMEDIATE')
            try:
                prior = self.entry(candidate.work_id)
                if prior is not None and prior.base_receipt_sha256 == digest:
                    self._db.execute('COMMIT')
                    return False
                if prior is not None and prior.state == 'in_flight':
                    raise MetadataLedgerError('metadata operation still in flight')
                if self._db.execute(
                        'SELECT 1 FROM superseded WHERE work_id=? AND base_sha=?',
                        (candidate.work_id, digest)).fetchone():
                    raise MetadataLedgerError('superseded managed generation rejected')
                if prior is not None:
                    self._db.execute(
                        'INSERT OR IGNORE INTO superseded(work_id,base_sha) VALUES(?,?)',
                        (candidate.work_id, prior.base_receipt_sha256))
                self._db.execute(
                    'INSERT INTO metadata(work_id,base_sha,state,attempts,lease_id,lease_epoch,json_body,json_sha) VALUES(?,?,?,?,NULL,NULL,NULL,NULL) '
                    "ON CONFLICT(work_id) DO UPDATE SET base_sha=excluded.base_sha,state='pending',attempts=excluded.attempts,lease_id=NULL,lease_epoch=NULL,json_body=NULL,json_sha=NULL",
                    (candidate.work_id, digest, 'pending', candidate.meta_attempts))
                self._db.execute('COMMIT')
                return True
            except BaseException:
                self._db.execute('ROLLBACK')
                raise

    def claim(self, work_id: str, base_receipt_sha256: str) -> str | None:
        wid, digest = _work_id(work_id), _fingerprint(base_receipt_sha256)
        if self._active_digest(wid) != digest:
            return None
        with self._lock:
            self._ensure_open()
            if not self.settings().enabled:
                return None
            lease = uuid4().hex
            cur = self._db.execute(
                "UPDATE metadata SET state='in_flight',lease_id=?,lease_epoch=? "
                "WHERE work_id=? AND base_sha=? AND state='pending' AND attempts<?",
                (lease, self._epoch, wid, digest, MAX_META_ATTEMPTS))
            return lease if cur.rowcount == 1 else None

    def _locked_lease(self, work_id: str, digest: str, lease: str):
        row = self.entry(work_id)
        proof = self._db.execute(
            'SELECT lease_id,lease_epoch FROM metadata WHERE work_id=?', (work_id,)
        ).fetchone()
        if (row is None or row.base_receipt_sha256 != digest
                or row.state != 'in_flight' or proof != (lease, self._epoch)
                or not self.settings().enabled):
            raise MetadataLedgerError('metadata operation no longer current')
        return row

    def complete_success(self, work_id: str, base_receipt_sha256: str,
                         lease_id: str, metadata: dict,
                         *, verified_active_receipt_sha256: str) -> None:
        wid, base = _work_id(work_id), _fingerprint(base_receipt_sha256)
        if type(lease_id) is not str or not re.fullmatch(r'[a-f0-9]{32}\Z', lease_id):
            raise MetadataLedgerError('invalid metadata operation lease')
        payload = _safe_metadata(metadata)
        verified = _fingerprint(verified_active_receipt_sha256)
        if verified != base:
            raise MetadataLedgerError('active managed generation changed')
        if self._active_digest(wid) != base:
            raise MetadataLedgerError('active managed generation changed')
        with self._lock:
            self._ensure_open()
            self._locked_lease(wid, base, lease_id)
            cursor = self._db.execute(
                "UPDATE metadata SET state='done',lease_id=NULL,lease_epoch=NULL,json_body=?,json_sha=? "
                "WHERE work_id=? AND base_sha=? AND lease_id=? AND lease_epoch=?",
                (payload, hashlib.sha256(payload).hexdigest(), wid, base, lease_id, self._epoch))
            if cursor.rowcount != 1:
                raise MetadataLedgerError('metadata operation no longer current')

    def complete_failure(self, work_id: str, base_receipt_sha256: str,
                         lease_id: str, decision: FailureDecision) -> str:
        wid, base = _work_id(work_id), _fingerprint(base_receipt_sha256)
        if type(lease_id) is not str or not re.fullmatch(r'[a-f0-9]{32}\Z', lease_id):
            raise MetadataLedgerError('invalid metadata operation lease')
        if not isinstance(decision, FailureDecision):
            raise MetadataLedgerError('invalid metadata failure decision')
        if self._active_digest(wid) != base:
            raise MetadataLedgerError('active managed generation changed')
        with self._lock:
            self._ensure_open()
            prior = self._locked_lease(wid, base, lease_id)
            # Recompute the trusted attempt policy from the class. An external
            # caller cannot override it by fabricating a boolean flag.
            if not isinstance(decision.classification, MetadataFailureClass):
                raise MetadataLedgerError('invalid metadata failure classification')
            attempts = prior.attempts + int(decision.classification == MetadataFailureClass.WORK)
            if decision.classification == MetadataFailureClass.WORK:
                next_state = 'gave_up' if attempts >= MAX_META_ATTEMPTS else 'pending'
            else:
                next_state = 'blocked'
            cursor = self._db.execute(
                'UPDATE metadata SET state=?,attempts=?,lease_id=NULL,lease_epoch=NULL WHERE work_id=? AND lease_id=? AND lease_epoch=?',
                (next_state, attempts, wid, lease_id, self._epoch))
            if cursor.rowcount != 1:
                raise MetadataLedgerError('metadata operation no longer current')
            return next_state

    def resume_blocked(self, work_id: str, *, expected_revision: int) -> None:
        """Explicit recovery after an outage; never an automatic hot retry."""
        wid = _work_id(work_id)
        if type(expected_revision) is not int or expected_revision < 1:
            raise MetadataLedgerError('invalid metadata settings revision')
        prior = self.entry(wid)
        if prior is None or self._active_digest(wid) != prior.base_receipt_sha256:
            raise MetadataLedgerError('active managed generation changed')
        with self._lock:
            self._ensure_open()
            self._db.execute('BEGIN IMMEDIATE')
            try:
                settings = self.settings()
                if not settings.enabled or expected_revision != settings.revision:
                    raise MetadataLedgerError('metadata settings changed')
                cur = self._db.execute(
                    "UPDATE metadata SET state='pending' WHERE work_id=? AND state='blocked'",
                    (wid,))
                if cur.rowcount != 1:
                    raise MetadataLedgerError('metadata operation not blocked')
                self._db.execute('UPDATE settings SET revision=? WHERE id=1',
                                 (settings.revision + 1,))
                self._db.execute('COMMIT')
            except BaseException:
                self._db.execute('ROLLBACK')
                raise

    def resume_interrupted(self, work_id: str, *, expected_revision: int) -> None:
        """Explicitly authorize a new GET after a prior process ended mid-read."""
        wid = _work_id(work_id)
        if type(expected_revision) is not int or expected_revision < 1:
            raise MetadataLedgerError('invalid metadata settings revision')
        prior = self.entry(wid)
        if prior is None or prior.state != 'unknown':
            raise MetadataLedgerError('metadata operation not interrupted')
        if self._active_digest(wid) != prior.base_receipt_sha256:
            raise MetadataLedgerError('active managed generation changed')
        with self._lock:
            self._ensure_open()
            self._db.execute('BEGIN IMMEDIATE')
            try:
                settings = self.settings()
                if not settings.enabled or expected_revision != settings.revision:
                    raise MetadataLedgerError('metadata settings changed')
                cur = self._db.execute(
                    "UPDATE metadata SET state='pending' WHERE work_id=? AND base_sha=? "
                    "AND state='unknown' AND lease_id IS NULL AND lease_epoch IS NULL",
                    (wid, prior.base_receipt_sha256))
                if cur.rowcount != 1:
                    raise MetadataLedgerError('metadata operation no longer current')
                self._db.execute('UPDATE settings SET revision=? WHERE id=1',
                                 (settings.revision + 1,))
                self._db.execute('COMMIT')
            except BaseException:
                self._db.execute('ROLLBACK')
                raise

    def acknowledge_interrupted(self, work_id: str) -> None:
        """Explicitly retire a potentially accepted operation; no auto replay."""
        wid = _work_id(work_id)
        with self._lock:
            self._ensure_open()
            proof = self._db.execute(
                'SELECT lease_epoch FROM metadata WHERE work_id=?', (wid,)).fetchone()
            if proof is not None and proof[0] == self._epoch:
                raise MetadataLedgerError('cannot acknowledge live metadata operation')
            cur = self._db.execute(
                "UPDATE metadata SET state='unknown',lease_id=NULL,lease_epoch=NULL WHERE work_id=? AND state='in_flight'",
                (wid,))
            if cur.rowcount != 1:
                raise MetadataLedgerError('metadata operation not interrupted')

    def discard_stale_result(self, work_id: str, base_receipt_sha256: str,
                             lease_id: str) -> None:
        """Retire a current lease after the active generation changes mid-fetch."""
        wid, base = _work_id(work_id), _fingerprint(base_receipt_sha256)
        if type(lease_id) is not str or not re.fullmatch(r'[a-f0-9]{32}\Z', lease_id):
            raise MetadataLedgerError('invalid metadata operation lease')
        with self._lock:
            self._ensure_open()
            cursor = self._db.execute(
                "UPDATE metadata SET state='unknown',lease_id=NULL,lease_epoch=NULL "
                "WHERE work_id=? AND base_sha=? AND state='in_flight' "
                "AND lease_id=? AND lease_epoch=?",
                (wid, base, lease_id, self._epoch))
            if cursor.rowcount != 1:
                raise MetadataLedgerError('metadata operation no longer current')

    def verified_overlay(self, work_id: str, active_receipt_sha256: str) -> dict | None:
        """Only a complete record bound to the current managed generation."""
        wid, digest = _work_id(work_id), _fingerprint(active_receipt_sha256)
        with self._lock:
            self._ensure_open()
            row = self._db.execute(
                "SELECT json_body,json_sha FROM metadata "
                "WHERE work_id=? AND base_sha=? AND state='done'", (wid, digest)).fetchone()
            if row is None:
                return None
            body, expected = row
            if type(body) is not bytes or hashlib.sha256(body).hexdigest() != expected:
                raise MetadataLedgerError('metadata integrity check failed')
            try:
                result = json.loads(body)
                if _safe_metadata(result) != body:
                    raise ValueError()
            except (TypeError, ValueError, UnicodeError):
                raise MetadataLedgerError('metadata integrity check failed') from None
            return result

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                try:
                    self._db.close()
                finally:
                    if self._owner is not None:
                        self._owner.close()
