"""Checker contracts only: no GUI, listener, browser, subprocess or Pixiv API."""
import asyncio
from copy import deepcopy
from pathlib import Path
import sys
from uuid import uuid4

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import check_q03_q12_managed_browser as checker


def test_fixture_has_exact_rows_closed_sqlite_and_detects_image_drift(tmp_path):
    before = checker.build_fixture(tmp_path)
    assert checker.verify_fixture(tmp_path) == before
    assert set(before['files']) == set(checker.PROTECTED)
    assert checker.read_source_rows(tmp_path / 'source' / 'frozen.sqlite3') == checker.ROWS
    with pytest.raises(FileExistsError):
        checker.build_fixture(tmp_path)
    image = tmp_path / 'source' / '301.png'
    image.write_bytes(image.read_bytes() + b'changed')
    with pytest.raises(AssertionError, match='protected fixture changed'):
        checker.verify_fixture(tmp_path)


def test_output_namespace_and_traversal_are_rejected_without_writes():
    root = checker.ROOT
    for path in (root, root / 'artifacts', root / 'artifacts' / 'p3-339-native',
                 root / 'artifacts' / checker.PREFIX,
                 root / 'artifacts' / (checker.PREFIX + 'x') / '..' / 'foreign'):
        with pytest.raises(ValueError):
            checker.admit_output(path)
    candidate = root / 'artifacts' / (checker.PREFIX + 'cpu-contract-unused')
    assert checker.admit_output(candidate) == candidate.resolve()
    assert not candidate.exists()


@pytest.mark.asyncio
async def test_local_asgi_origin_peer_and_nonce_boundaries():
    reached = []
    async def app(scope, receive, send):
        reached.append(scope['type'])
    fence = checker.LoopbackOnly(app, port=12345)
    base = {'type': 'http', 'client': ('127.0.0.1', 40000),
            'headers': [(b'host', b'127.0.0.1:12345')]}
    async def receive():
        return {'type': 'http.disconnect'}
    emitted = []
    async def send(message):
        emitted.append(message)
    await fence(base, receive, send)
    assert reached == ['http'] and emitted == []
    for update in ({'client': ('192.0.2.1', 40000)},
                   {'headers': [(b'host', b'localhost:12345')]},
                   {'headers': [(b'host', b'127.0.0.1:12345'), (b'origin', b'https://example.invalid')]},
                   {'type': 'websocket', 'headers': [(b'host', b'wrong.invalid')]}):
        emitted.clear()
        await fence({**base, **update}, receive, send)
        assert reached == ['http']
        assert emitted[0].get('status') == 403 or emitted[0].get('code') == 1008
    nonce = 'a' * 64
    assert checker.authorized(nonce, nonce)
    for value in (None, '', 'b' * 64, 'あ' * 64, nonce + 'a'):
        assert not checker.authorized(value, nonce)
    with pytest.raises(ValueError, match='redirects'):
        checker.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.invalid')


def test_normal_close_rejects_missing_receipt_live_children_and_source_change():
    protected = {'kind': checker.KIND, 'files': {name: {'sha256': name} for name in checker.PROTECTED}}
    result = {'errors': [], 'page_errors': [], 'console_errors': [], 'foreign_requests': [],
              'shutdown_acknowledged': True, 'exit_code': 0, 'port_released': True,
              'process_tree': {'living': [], 'errors': []},
              'lifecycle': {'pid': 42, 'services_closed': True, 'shutdown_requested': True,
                            'external_denied': 0, 'protected_before': protected,
                            'protected_after': deepcopy(protected)}}
    assert checker.closed_ok(result, {'pid': 42})
    assert not checker.closed_ok(result, None)
    for key, value in (('exit_code', 1), ('shutdown_acknowledged', False),
                       ('port_released', False), ('lifecycle', {}),
                       ('process_tree', {'living': [42], 'errors': []})):
        assert not checker.closed_ok({**result, key: value}, {'pid': 42})
    changed = deepcopy(result)
    changed['lifecycle']['protected_after']['files']['frozen.sqlite3']['sha256'] = 'changed'
    assert not checker.closed_ok(changed, {'pid': 42})
    missing = deepcopy(result)
    del missing['lifecycle']['protected_before']
    del missing['lifecycle']['protected_after']
    assert not checker.closed_ok(missing, {'pid': 42})


async def accept(service, work_id, owner='a' * 32):
    rows = await service.read_failed_records()
    row = next(row for row in rows if row.work_id == work_id)
    record = await service.lookup_legacy_failed_original(row, owner=owner)
    return await asyncio.to_thread(service.enqueue_legacy_failed_original,
                                   row, record, uuid4().hex, owner=owner)


async def settled(service, job_id, state='done'):
    def wait():
        with service.static_jobs._condition:
            assert service.static_jobs._condition.wait_for(lambda: any(
                j.job_id == job_id and j.state == state for j in service.jobs.snapshot()), timeout=8)
    await asyncio.to_thread(wait)


@pytest.mark.asyncio
async def test_real_managed_receipts_active_cancellation_and_restart_metadata(tmp_path):
    """Exercise real SQLite acceptance/writer, NOT the old Q17 fake-accept seam."""
    from pixivvault_nicegui.services import ApplicationServices
    protected = checker.build_fixture(tmp_path)
    remote = checker.OfflineManaged(tmp_path)
    service = ApplicationServices(live_client=remote, pixiv_download_root=tmp_path / 'managed',
                                  failed_queue_readonly_db=tmp_path / 'source' / 'frozen.sqlite3')
    gate = checker.ManagedGate(service.static_jobs._runner, timeout=10)
    service.static_jobs._runner = gate
    try:
        first = await accept(service, '301')
        await settled(service, first.job_id)
        second = await accept(service, '302')
        await settled(service, second.job_id)
        outputs = checker.saved_outputs(tmp_path)
        assert len(outputs) == 4
        assert service.managed_job_author_names(service.jobs.snapshot()) == {
            first.job_id: '合成作者', second.job_id: '合成作者'}
        active = await accept(service, '305')
        await settled(service, active.job_id, 'running')
        queued = await accept(service, '306')
        assert next(j for j in service.jobs.snapshot() if j.job_id == queued.job_id).state == 'queued'
        assert '305' in gate.snapshot()['started']
        # The browser will use the real dialog. Here only validate its fixture's
        # real cancellation/bytes oracle, using the production control boundary.
        for target in (queued.job_id, active.job_id, first.job_id):
            assert service.jobs.control(target, 'delete')
        def wait_cancel():
            with service.static_jobs._condition:
                assert service.static_jobs._condition.wait_for(
                    lambda: service.static_jobs._active is None, timeout=8)
        await asyncio.to_thread(wait_cancel)
        assert gate.snapshot()['cancelled'] == ['305']
        assert '306' not in gate.snapshot()['started']
        assert checker.saved_outputs(tmp_path) == outputs
        assert remote.image_ids == ['301', '302']
    finally:
        await asyncio.to_thread(service.close)
    assert service.closed and checker.verify_fixture(tmp_path) == protected
    reopened_remote = checker.OfflineManaged(tmp_path, no_remote=True)
    reopened = ApplicationServices(live_client=reopened_remote, pixiv_download_root=tmp_path / 'managed',
                                   failed_queue_readonly_db=tmp_path / 'source' / 'frozen.sqlite3')
    try:
        jobs = reopened.jobs.snapshot()
        assert [j.target_id for j in jobs] == ['302']
        assert reopened.managed_job_author_names(jobs) == {second.job_id: '合成作者'}
        assert reopened_remote.attempted == 0 and reopened.static_jobs._thread is None
        assert checker.saved_outputs(tmp_path) == outputs
        assert reopened.jobs.control(second.job_id, 'delete')
        assert reopened.jobs.snapshot() == ()
        assert checker.saved_outputs(tmp_path) == outputs
    finally:
        await asyncio.to_thread(reopened.close)
    assert checker.verify_fixture(tmp_path) == protected
    # A corrupted receipt/page must make the checker fail, even after deletion
    # removed every history entry that could otherwise expose the corruption.
    image_path = next((tmp_path / 'managed' / 'outputs').glob('work-*/page-1.png'))
    image_path.write_bytes(image_path.read_bytes() + b'corrupt')
    with pytest.raises(AssertionError, match='saved page bytes mismatch'):
        checker.saved_outputs(tmp_path)
