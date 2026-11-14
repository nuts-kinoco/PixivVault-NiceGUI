"""One explicit retry command pins its frozen source; no source writes.

The context follows asyncio.to_thread, not unrelated clients. A shielded
accepted operation holds a lease so client cancellation cannot release its
source pin before the accepting worker has drained.
"""
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from threading import RLock
import asyncio

from .failed_records_repository import read_failed_records
from .failed_records_state import MAX_RECORDS
from .frozen_source_pin import FrozenSourcePin, FrozenSourceError
from .legacy_retry_errors import LegacyRetrySourceChanged


CURRENT_RETRY_BATCH = ContextVar('pixivvault_legacy_retry_batch', default=None)
RETRY_CAPACITY_WAIT_SECONDS = 120.0


class LegacyRetryBatch:
    def __init__(self, service, owner, pin, records):
        self.service = service
        self.owner = owner
        self._pin = pin
        self.records = frozenset(records)
        self._lock = RLock()
        self._leases = 1
        self._closing = False

    @classmethod
    def prepare(cls, service, selected, owner, *, is_current=None):
        signature = service._failed_copy_signature
        if (not service.legacy_failed_retry_available or signature is None
                or not 1 <= len(selected) <= MAX_RECORDS):
            raise LegacyRetrySourceChanged('legacy retry source unavailable')
        # failed_records_repository uses (size,mtime,ino,digest); convert
        # explicitly to SavedSimilaritySource's (ino,size,mtime,digest).
        expected = signature[2], signature[0], signature[1], signature[3]
        admitting = True
        pin = FrozenSourcePin(service.failed_queue_readonly_db, expected,
            cancelled=lambda: admitting and
            (service.closing or (is_current is not None and not is_current())))
        try:
            pin.__enter__()
            records = frozenset(read_failed_records(service.failed_queue_readonly_db, immutable=True))
            pin.check()
            if not all(row in records for row in selected):
                raise LegacyRetrySourceChanged('legacy failure selection changed; refresh the list')
            # New-row admission still checks the UI/application generation.
            # Once a shielded accept owns a lease, an unrelated UI departure
            # must not cancel its portable full-SHA proof halfway through.
            admitting = False
            return cls(service, owner, pin, records)
        except BaseException:
            pin.close()
            raise

    @contextmanager
    def lease(self, service, owner):
        with self._lock:
            if self.service is not service or owner != self.owner or self._closing or self._pin is None:
                raise LegacyRetrySourceChanged('legacy retry scope no longer active')
            self._leases += 1
        try:
            yield self
        finally:
            self._release()

    def check_row(self, service, row):
        with self._lock:
            if self.service is not service or self._pin is None:
                raise LegacyRetrySourceChanged('legacy retry scope no longer active')
            try:
                self._pin.check()
            except FrozenSourceError:
                raise LegacyRetrySourceChanged('legacy failure copy changed; refresh the list') from None
            if row not in self.records:
                raise LegacyRetrySourceChanged('legacy failure selection changed; refresh the list')

    def _release(self):
        with self._lock:
            self._leases -= 1
            if self._leases == 0 and self._pin is not None:
                pin, self._pin = self._pin, None
                pin.close()

    def close(self):
        with self._lock:
            if not self._closing:
                self._closing = True
                self._release()


@contextmanager
def legacy_retry_lease(service, owner):
    batch = CURRENT_RETRY_BATCH.get()
    if batch is None:
        yield None
    else:
        with batch.lease(service, owner):
            yield batch


@asynccontextmanager
async def retry_batch_scope(service, selected, owner, *, is_current):
    prepare = getattr(service, 'prepare_legacy_retry_batch', None)
    if len(selected) <= 1 or not callable(prepare):
        yield None
        return
    task = asyncio.create_task(asyncio.to_thread(
        prepare, selected, owner=owner, is_current=is_current))
    try:
        batch = await asyncio.shield(task)
    except BaseException:
        # A cancelled UI cannot leak the completed worker's file handle.
        def dispose(done):
            if not done.cancelled() and done.exception() is None:
                done.result().close()
        task.add_done_callback(dispose)
        raise
    token = CURRENT_RETRY_BATCH.set(batch)
    try:
        yield batch
    finally:
        CURRENT_RETRY_BATCH.reset(token)
        batch.close()
