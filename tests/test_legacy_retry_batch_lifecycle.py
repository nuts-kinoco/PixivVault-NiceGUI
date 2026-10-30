"""Synthetic retry-batch source lifetime: one validation, no cross-client use."""
import asyncio
from contextlib import closing
from hashlib import sha256
import sqlite3
from threading import Event
from types import SimpleNamespace

from nicegui import Client, ui
import pytest

from pixivvault_nicegui import frozen_source_pin, legacy_retry_batch
from pixivvault_nicegui.legacy_retry_batch import (
    CURRENT_RETRY_BATCH, LegacyRetryBatch, legacy_retry_lease, retry_batch_scope,
)
from pixivvault_nicegui.legacy_retry_errors import LegacyRetrySourceChanged
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.failed_records_view import FailedRecordsView
from test_legacy_failed_managed_retry import OfflinePixiv


def fixture(tmp_path, count=53):
    source = tmp_path / 'generated-frozen'
    source.mkdir()
    database = source / 'failed.sqlite3'
    with closing(sqlite3.connect(database)) as connection:
        connection.execute('CREATE TABLE failed_queue(work_id TEXT,user_id TEXT,title TEXT,'
                           'work_type TEXT,error_reason TEXT,failed_at TEXT,retry_count INTEGER)')
        connection.executemany('INSERT INTO failed_queue VALUES(?,?,?,?,?,?,?)', (
            (str(1001 + i), '23', f'Generated {i}', 'illust', 'Timeout', '2026-10-30', 0)
            for i in range(count)))
        connection.commit()
    service = ApplicationServices(live_client=OfflinePixiv(),
        pixiv_download_root=tmp_path/'managed', failed_queue_readonly_db=database)
    return service, database


async def until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(.01)


@pytest.mark.asyncio
async def test_whole_retry_set_hashes_and_reads_frozen_table_once(tmp_path, monkeypatch):
    service, database = fixture(tmp_path)
    client = Client(ui.page('/_retry_batch_one_source_read'))
    hashes, reads, accepted = [], [], []
    before = sha256(database.read_bytes()).hexdigest()
    real_hash = frozen_source_pin._digest
    real_read = legacy_retry_batch.read_failed_records
    def count_hash(*args):
        hashes.append(True)
        return real_hash(*args)
    def count_read(*args, **kwargs):
        reads.append(True)
        return real_read(*args, **kwargs)
    def commit(record, operation_id, *, owner):
        accepted.append(record.work_id)
        return SimpleNamespace(job_id='generated-' + record.work_id)
    monkeypatch.setattr(frozen_source_pin, '_digest', count_hash)
    monkeypatch.setattr(legacy_retry_batch, 'read_failed_records', count_read)
    monkeypatch.setattr(service, 'enqueue_managed_download', commit)
    try:
        with client:
            view = FailedRecordsView(service)
            await view._refresh(view.state.begin_refresh())
            # The list's deletion snapshot now has its own source pin. This
            # assertion measures the explicit retry command only, not that
            # prior independent UI read.
            before_retry_hashes = len(hashes)
            assert await view._retry_records(view.state.records)
            assert len(accepted) == 53 and len(set(accepted)) == 53
            assert len(reads) == 1
            if frozen_source_pin._WINDOWS:
                assert len(hashes)-before_retry_hashes == 1
        assert CURRENT_RETRY_BATCH.get() is None
        assert sha256(database.read_bytes()).hexdigest() == before
        assert not service._author_owned_pins and not service._direct_download_candidates
    finally:
        client.delete()
        service.close()


@pytest.mark.asyncio
async def test_owner_mismatch_and_inflight_lease_release(tmp_path):
    service, _ = fixture(tmp_path, 2)
    rows = await service.read_failed_records()
    token = 'a' * 32
    try:
        batch = LegacyRetryBatch.prepare(service, rows, token)
        with pytest.raises(LegacyRetrySourceChanged):
            with batch.lease(service, 'b' * 32):
                pytest.fail('other client was admitted')
        with batch.lease(service, token):
            batch.close()
            assert batch._pin is not None
            batch.check_row(service, rows[0])
            with pytest.raises(LegacyRetrySourceChanged):
                with batch.lease(service, token):
                    pytest.fail('closing scope accepted a new lease')
        assert batch._pin is None
        batch.close()
        with pytest.raises(LegacyRetrySourceChanged):
            batch.check_row(service, rows[0])
    finally:
        service.close()


@pytest.mark.asyncio
async def test_cancelling_pending_prepare_disposes_late_pin(tmp_path, monkeypatch):
    service, _ = fixture(tmp_path, 2)
    rows = await service.read_failed_records()
    entered, release = Event(), Event()
    prepared = []
    original = service.prepare_legacy_retry_batch
    def delayed(selected, *, owner, is_current=None):
        batch = original(selected, owner=owner, is_current=is_current)
        prepared.append(batch)
        entered.set()
        assert release.wait(5)
        return batch
    monkeypatch.setattr(service, 'prepare_legacy_retry_batch', delayed)
    async def use():
        async with retry_batch_scope(service, rows, 'c'*32, is_current=lambda: True):
            pytest.fail('cancelled scope yielded')
    task = asyncio.create_task(use())
    try:
        await until(entered.is_set)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        await until(lambda: prepared and prepared[0]._pin is None)
        assert CURRENT_RETRY_BATCH.get() is None
    finally:
        release.set()
        if not task.done():
            task.cancel()
        service.close()


@pytest.mark.asyncio
async def test_shielded_thread_keeps_pin_after_ui_scope_exits(tmp_path):
    service, _ = fixture(tmp_path, 2)
    rows = await service.read_failed_records()
    entered, release = Event(), Event()
    thread_task = None
    def accepting():
        with legacy_retry_lease(service, 'd'*32) as batch:
            entered.set()
            assert release.wait(5)
            batch.check_row(service, rows[0])
            return True
    try:
        async with retry_batch_scope(service, rows, 'd'*32, is_current=lambda: True) as batch:
            thread_task = asyncio.create_task(asyncio.to_thread(accepting))
            await until(entered.is_set)
        assert batch._pin is not None
        release.set()
        assert await asyncio.wait_for(thread_task, 5)
        assert batch._pin is None and CURRENT_RETRY_BATCH.get() is None
    finally:
        release.set()
        if thread_task is not None:
            await asyncio.wait_for(asyncio.shield(thread_task), 5)
        service.close()
