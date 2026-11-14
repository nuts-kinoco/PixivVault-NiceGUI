"""Validated list-only failure deletion; original DB/media remain untouched."""
from contextlib import contextmanager
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from threading import RLock

from .failed_record_dismissals import (
    FailureDismissals, FailureDismissalError, FailureDismissalConflict,
    record_digest, source_namespace,
)
from .failed_records_repository import read_failed_records
from .failed_records_state import FailureRecord, MAX_RECORDS
from .frozen_source_pin import FrozenSourcePin, FrozenSourceError


@dataclass(frozen=True)
class FailureListSnapshot:
    namespace: str
    revision: int
    records: tuple[FailureRecord, ...]
    source_digest: str
    _owner: object = field(repr=False, compare=False)


def _rowset_digest(records):
    value = sha256(b'PixivVault immutable failure snapshot v1\0')
    for row in records:
        value.update(bytes.fromhex(record_digest(row)))
    return value.hexdigest()


class FailureRecordActions:
    def __init__(self, service, root):
        self._service = service
        self.root = Path(root)
        self._lock = RLock()
        self._owner = object()
        self._ledger = None
        self._closed = False

    def _check(self):
        if self._closed or self._service.closing:
            raise FailureDismissalError('失敗記録の操作は終了しています。')

    def _store(self):
        self._check()
        if self._ledger is None:
            self._ledger = FailureDismissals(self.root)
        return self._ledger

    @contextmanager
    def _source(self):
        """Hold the exact admitted source through read or local decision commit."""
        self._check()
        service = self._service
        path = service.failed_queue_readonly_db or service.library_readonly_db
        if path is None:
            if service.data_mode != 'synthetic':
                raise FailureDismissalError('失敗記録の参照元がありません。')
            from .failed_records_fixtures import build_failed_records_fixture
            namespace = sha256(b'PixivVault generated failure fixture v1').hexdigest()
            yield namespace, tuple(build_failed_records_fixture())
            return
        if service.failed_queue_readonly_db is not None:
            signature = service._failed_copy_signature
            expected = ((signature[2], signature[0], signature[1], signature[3])
                        if signature is not None else None)
        else:
            # The ordinary library's read-only similarity guard pins the same
            # frozen SQLite at startup, independently of whether it has vectors.
            source = getattr(service.similarity_source, 'old', service.similarity_source)
            expected = getattr(source, '_signature', None)
        if expected is None:
            raise FailureDismissalError('失敗記録の凍結コピーを確認できません。')
        try:
            with FrozenSourcePin(path, expected, cancelled=lambda: service.closing) as pin:
                records = tuple(read_failed_records(path, immutable=True))
                pin.check()
                yield source_namespace(path), records
        except (FrozenSourceError, OSError, ValueError) as error:
            if isinstance(error, FailureDismissalError):
                raise
            raise FailureDismissalError('失敗記録の参照元が変わりました。再読み込みしてください。') from None

    def snapshot(self):
        with self._lock:
            with self._source() as (namespace, records):
                revision, visible = self._store().snapshot(namespace, records)
                return FailureListSnapshot(namespace, revision, visible, _rowset_digest(records), self._owner)

    def dismiss(self, snapshot, selected):
        """A confirmed exact subset only; refuse all stale/foreign selections."""
        if (type(snapshot) is not FailureListSnapshot or snapshot._owner is not self._owner
                or type(selected) is not tuple or not 1 <= len(selected) <= MAX_RECORDS
                or any(type(row) is not FailureRecord for row in selected)):
            raise FailureDismissalError('失敗記録の選択が不正です。')
        known = frozenset(snapshot.records)
        if len(set(selected)) != len(selected) or any(row not in known for row in selected):
            raise FailureDismissalError('失敗記録の選択が不正です。')
        with self._lock:
            self._check()
            with self._source() as (namespace, records):
                if namespace != snapshot.namespace or _rowset_digest(records) != snapshot.source_digest:
                    raise FailureDismissalConflict('失敗記録の参照元が変わりました。再読み込みしてください。')
                # Keep the pinned frozen source and lifecycle lock alive while
                # CAS commits the separate owned ledger. No source row is updated.
                return self._store().dismiss(namespace, selected, expected_revision=snapshot.revision)

    def close(self):
        with self._lock:
            if not self._closed:
                self._closed = True
                if self._ledger is not None:
                    self._ledger.close()
                    self._ledger = None
