"""Synthetic persistence lifecycle, rollback, receipts and client isolation."""
import asyncio
from concurrent.futures import Future
from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
from threading import Event, get_ident

import pytest

from pixivvault_nicegui.fixtures import generate_demo_artworks
from pixivvault_nicegui.learning import Judgment
from pixivvault_nicegui.learning_repository import LearningConflict, LearningRepository
from pixivvault_nicegui.learning_service import DATABASE_NAME, MARKER_NAME, LearningService
from pixivvault_nicegui.legacy.database import Database
import pixivvault_nicegui.learning_service as service_module


@pytest.fixture
async def storage(tmp_path):
    service = LearningService(tmp_path / "sandbox", generate_demo_artworks(4))
    try:
        yield service
    finally:
        await asyncio.to_thread(service.close)


async def on_worker(service, callback):
    return await service._wait(service._submit(callback))


async def execute(service, sql):
    def run():
        service._db.conn.execute(sql)
    await on_worker(service, run)


async def scalar(service, sql):
    return await on_worker(service, lambda: service._db.conn.execute(sql).fetchone()[0])


async def test_constructor_has_no_filesystem_io_and_all_db_work_uses_one_worker(tmp_path, monkeypatch):
    seen = []
    class TrackingDatabase(Database):
        def __init__(self, *args, **kwargs):
            seen.append(("open", get_ident()))
            super().__init__(*args, **kwargs)
        def get_judged_work_ids(self):
            seen.append(("read", get_ident()))
            return super().get_judged_work_ids()
        def close(self):
            seen.append(("close", get_ident()))
            return super().close()
    monkeypatch.setattr(service_module, "Database", TrackingDatabase)
    original_record, original_revert = LearningRepository.record, LearningRepository.revert
    def record(self, *args):
        seen.append(("record", get_ident()))
        return original_record(self, *args)
    def revert(self, *args):
        seen.append(("revert", get_ident()))
        return original_revert(self, *args)
    monkeypatch.setattr(LearningRepository, "record", record)
    monkeypatch.setattr(LearningRepository, "revert", revert)
    root = tmp_path / "sandbox"
    service = LearningService(root, generate_demo_artworks(2))
    assert not root.exists() and seen == []
    try:
        session = await service.open_session()
        assert session.judged_ids == frozenset()
        judgment = Judgment("demo-0001", "like", "first")
        await service.record(session.session_id, judgment)
        await service.revert(session.session_id, judgment, "undo")
    finally:
        await asyncio.to_thread(service.close)
    assert {name for name, _ in seen} == {"open", "read", "record", "revert", "close"}
    assert len({thread for _, thread in seen}) == 1
    assert get_ident() not in {thread for _, thread in seen}


async def test_restart_keeps_all_verdicts_excluded_but_never_restores_undo(storage):
    first = await storage.open_session()
    judgments = [Judgment(f"demo-{index:04d}", choice, f"op-{index}")
                 for index, choice in enumerate(("skip", "hate", "like", "superLike"), 1)]
    for judgment in judgments:
        await storage.record(first.session_id, judgment)
    await asyncio.to_thread(storage.close)
    reopened = LearningService(storage.sandbox_root, generate_demo_artworks(4))
    try:
        snapshot = await reopened.open_session()
        assert snapshot.judged_ids == frozenset(item.work_id for item in judgments)
        assert snapshot.session_id != first.session_id
        with pytest.raises(ValueError, match="not latest"):
            await reopened.revert(snapshot.session_id, judgments[-1], "new-undo")
        assert await scalar(reopened, "SELECT COUNT(*) FROM discovery_swipe_history") == 4
    finally:
        await asyncio.to_thread(reopened.close)


async def test_receipts_repeat_success_without_duplicate_write_and_reject_changed_payload(storage):
    session = await storage.open_session()
    judgment = Judgment("demo-0001", "like", "save")
    await storage.record(session.session_id, judgment)
    await storage.record(session.session_id, judgment)
    assert await scalar(storage, "SELECT revision FROM learning_adapter_revision") == 1
    with pytest.raises(ValueError, match="different operation"):
        await storage.record(session.session_id, Judgment("demo-0001", "hate", "save"))
    with pytest.raises(ValueError, match="different operation"):
        await storage.revert(session.session_id, judgment, "save")
    await storage.revert(session.session_id, judgment, "undo")
    await storage.revert(session.session_id, judgment, "undo")
    assert await scalar(storage, "SELECT revision FROM learning_adapter_revision") == 2
    assert await scalar(storage, "SELECT COUNT(*) FROM discovery_swipe_history") == 0


async def test_failed_transaction_reuses_operation_id_and_has_no_partial_counts(storage):
    session = await storage.open_session()
    judgment = Judgment("demo-0001", "superLike", "retry-same")
    await execute(storage, """CREATE TRIGGER reject_insert BEFORE INSERT ON discovery_swipe_history
        BEGIN SELECT RAISE(ABORT, 'blocked for test'); END""")
    with pytest.raises(sqlite3.DatabaseError, match="blocked for test"):
        await storage.record(session.session_id, judgment)
    assert await scalar(storage, "SELECT COUNT(*) FROM discovery_swipe_history") == 0
    assert await scalar(storage, "SELECT COUNT(*) FROM discovery_preference") == 0
    await execute(storage, "DROP TRIGGER reject_insert")
    await storage.record(session.session_id, judgment)
    assert await scalar(storage, "SELECT COUNT(*) FROM discovery_swipe_history") == 1
    assert await scalar(storage, "SELECT SUM(superlike_count) FROM discovery_preference") == 2


@pytest.mark.parametrize("choices", [("like",), ("superLike", "like")])
async def test_other_client_same_value_or_aba_rejects_stale_undo_and_can_resync(storage, choices):
    a = await storage.open_session()
    other = LearningService(storage.sandbox_root, generate_demo_artworks(4))
    try:
        b = await other.open_session()
        original = Judgment("demo-0001", "like", "a")
        await storage.record(a.session_id, original)
        for index, choice in enumerate(choices):
            await other.record(b.session_id, Judgment("demo-0001", choice, f"b-{index}"))
        before = await scalar(storage, "SELECT revision FROM learning_adapter_revision")
        with pytest.raises(LearningConflict):
            await storage.revert(a.session_id, original, "stale-undo")
        assert await scalar(storage, "SELECT revision FROM learning_adapter_revision") == before
        assert await scalar(storage, "SELECT verdict FROM discovery_swipe_history") == "like"
        storage.release_session(a.session_id)
        fresh = await storage.open_session()
        assert fresh.judged_ids == frozenset({"demo-0001"})
        with pytest.raises(ValueError, match="not latest"):
            await storage.revert(fresh.session_id, original, "fresh-undo")
    finally:
        await asyncio.to_thread(other.close)


async def test_history_read_failure_does_not_return_empty_candidates(storage, monkeypatch):
    real = Database.get_judged_work_ids
    def broken(self):
        raise sqlite3.OperationalError("history unavailable")
    monkeypatch.setattr(Database, "get_judged_work_ids", broken)
    with pytest.raises(sqlite3.OperationalError, match="history unavailable"):
        await storage.open_session()
    assert await on_worker(storage, lambda: len(storage._sessions)) == 0
    monkeypatch.setattr(Database, "get_judged_work_ids", real)
    assert (await storage.open_session()).judged_ids == frozenset()


async def test_sqlite_lock_wait_leaves_event_loop_responsive(storage, monkeypatch):
    session = await storage.open_session()
    entered = Event()
    original = LearningRepository.record
    def traced(self, *args):
        entered.set()
        return original(self, *args)
    monkeypatch.setattr(LearningRepository, "record", traced)
    await execute(storage, "PRAGMA busy_timeout=3000")
    blocker = sqlite3.connect(storage.sandbox_root / DATABASE_NAME, isolation_level=None)
    try:
        blocker.execute("BEGIN IMMEDIATE")
        future = storage.submit_record(session.session_id, Judgment("demo-0001", "like", "blocked"))
        assert await asyncio.to_thread(entered.wait, 1)
        heartbeat = asyncio.Event()
        asyncio.get_running_loop().call_soon(heartbeat.set)
        await asyncio.wait_for(heartbeat.wait(), 0.5)
        assert not future.done()
        blocker.execute("ROLLBACK")
        await storage._wait(future)
        assert await scalar(storage, "SELECT COUNT(*) FROM discovery_swipe_history") == 1
    finally:
        if blocker.in_transaction:
            blocker.execute("ROLLBACK")
        blocker.close()


async def test_cancelled_waiter_and_release_do_not_cancel_queued_write(storage):
    session = await storage.open_session()
    entered, unblock = Event(), Event()
    def hold_worker():
        entered.set()
        assert unblock.wait(3)
    blocked = storage._submit(hold_worker)
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        future = storage.submit_record(session.session_id, Judgment("demo-0001", "like", "accepted"))
        waiter = asyncio.create_task(storage._wait(future))
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        storage.release_session(session.session_id)
        assert not future.cancelled()
    finally:
        unblock.set()
        await asyncio.to_thread(storage.close)
    assert blocked.done() and future.done() and future.result() is None
    with closing(sqlite3.connect(storage.sandbox_root / DATABASE_NAME)) as check:
        assert check.execute("SELECT COUNT(*) FROM discovery_swipe_history").fetchone()[0] == 1


def test_submit_then_immediate_close_drains_without_any_async_wait(tmp_path):
    service = LearningService(tmp_path / "sandbox", generate_demo_artworks(1))
    session = asyncio.run(service.open_session())
    future = service.submit_record(session.session_id, Judgment("demo-0001", "like", "accepted"))
    assert isinstance(future, Future)
    service.close()
    assert future.done() and future.result() is None
    with closing(sqlite3.connect(service.sandbox_root / DATABASE_NAME)) as check:
        assert check.execute("SELECT COUNT(*) FROM discovery_swipe_history").fetchone()[0] == 1
    with pytest.raises(RuntimeError, match="closed"):
        service.submit_record(session.session_id, Judgment("demo-0001", "hate", "late"))
    service.release_session(session.session_id)
    service.close()


@pytest.mark.parametrize("kind", ["unmarked", "wrong-marker", "malformed", "extra-file"])
async def test_unmanaged_existing_data_rejected_before_database_constructor(tmp_path, monkeypatch, kind):
    root = tmp_path / "sandbox"
    root.mkdir()
    (root / DATABASE_NAME).write_bytes(b"unmanaged database must remain untouched")
    service = LearningService(root, generate_demo_artworks(2))
    if kind == "wrong-marker":
        (root / MARKER_NAME).write_text(json.dumps({"format": "other"}), encoding="utf-8")
    elif kind == "malformed":
        (root / MARKER_NAME).write_text("not json", encoding="utf-8")
    elif kind == "extra-file":
        (root / MARKER_NAME).write_text(json.dumps(service._marker), encoding="utf-8")
        (root / "unmanaged.txt").write_text("leave alone", encoding="utf-8")
    before = {path.name: path.read_bytes() for path in root.iterdir()}
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(args)
        raise AssertionError("unmanaged DB must not be opened")
    monkeypatch.setattr(service_module, "Database", forbidden)
    try:
        with pytest.raises(ValueError):
            await service.open_session()
        assert calls == []
        assert {path.name: path.read_bytes() for path in root.iterdir()} == before
    finally:
        await asyncio.to_thread(service.close)


async def test_fixture_change_rejected_before_opening_existing_database(storage, monkeypatch):
    session = await storage.open_session()
    await storage.record(session.session_id, Judgment("demo-0001", "like", "a"))
    await asyncio.to_thread(storage.close)
    before = {path.name: path.read_bytes() for path in storage.sandbox_root.iterdir()}
    def forbidden(*args, **kwargs):
        raise AssertionError("fixture mismatch must fail before DB open")
    monkeypatch.setattr(service_module, "Database", forbidden)
    changed = LearningService(storage.sandbox_root, generate_demo_artworks(3))
    try:
        with pytest.raises(ValueError, match="fixture mismatch"):
            await changed.open_session()
        assert {path.name: path.read_bytes() for path in storage.sandbox_root.iterdir()} == before
    finally:
        await asyncio.to_thread(changed.close)


def test_constructor_rejects_relative_path_and_modified_fixture(tmp_path):
    with pytest.raises(ValueError, match="absolute"):
        LearningService(Path("relative"), generate_demo_artworks(1))
    modified = (replace(generate_demo_artworks(1)[0], title="not the generated fixture"),)
    with pytest.raises(ValueError, match="synthetic fixture"):
        LearningService(tmp_path / "never-created", modified)
    assert not (tmp_path / "never-created").exists()


@pytest.mark.parametrize("replacement", ["foreign", "other-synthetic-instance"])
async def test_existing_marker_cannot_adopt_replaced_database(tmp_path, monkeypatch, replacement):
    import shutil
    root = tmp_path / "owner"
    initial = LearningService(root, generate_demo_artworks(2))
    await initial.open_session()
    await asyncio.to_thread(initial.close)
    path = root / DATABASE_NAME
    if replacement == "foreign":
        path.unlink()
        foreign = sqlite3.connect(path)
        try:
            foreign.execute("CREATE TABLE unrelated_payload (value TEXT)")
            foreign.execute("INSERT INTO unrelated_payload VALUES ('synthetic sentinel')")
            foreign.commit()
        finally:
            foreign.close()
    else:
        other = LearningService(tmp_path / "other", generate_demo_artworks(2))
        session = await other.open_session()
        await other.record(session.session_id, Judgment("demo-0001", "like", "other"))
        await asyncio.to_thread(other.close)
        shutil.copyfile(other.sandbox_root / DATABASE_NAME, path)
    before = path.read_bytes()
    marker_before = (root / MARKER_NAME).read_bytes()
    calls = []
    real_database = service_module.Database
    def trace(*args, **kwargs):
        calls.append(args)
        return real_database(*args, **kwargs)
    monkeypatch.setattr(service_module, "Database", trace)
    reopened = LearningService(root, generate_demo_artworks(2))
    try:
        with pytest.raises(ValueError, match="identity"):
            await reopened.open_session()
        assert calls == []
        assert path.read_bytes() == before
        assert (root / MARKER_NAME).read_bytes() == marker_before
    finally:
        await asyncio.to_thread(reopened.close)


@pytest.mark.parametrize("damage", ["missing-database", "changed-identity"])
async def test_managed_identity_damage_fails_closed_before_legacy_open(tmp_path, monkeypatch, damage):
    root = tmp_path / "sandbox"
    initial = LearningService(root, generate_demo_artworks(2))
    await initial.open_session()
    await asyncio.to_thread(initial.close)
    path = root / DATABASE_NAME
    if damage == "missing-database":
        path.unlink()
    else:
        corrupt = sqlite3.connect(path)
        try:
            corrupt.execute("UPDATE learning_service_identity SET payload='{}'")
            corrupt.commit()
        finally:
            corrupt.close()
    before = {entry.name: entry.read_bytes() for entry in root.iterdir()}
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(args)
        raise AssertionError("identity must be checked before legacy DB open")
    monkeypatch.setattr(service_module, "Database", forbidden)
    reopened = LearningService(root, generate_demo_artworks(2))
    try:
        with pytest.raises(ValueError, match="identity"):
            await reopened.open_session()
        assert calls == []
        assert {entry.name: entry.read_bytes() for entry in root.iterdir()} == before
    finally:
        await asyncio.to_thread(reopened.close)


async def test_identity_creation_and_readonly_verification_run_off_event_loop(tmp_path, monkeypatch):
    connections = []
    original = sqlite3.connect
    def trace(path, *args, **kwargs):
        connections.append((str(path), kwargs.copy(), get_ident()))
        return original(path, *args, **kwargs)
    monkeypatch.setattr(sqlite3, "connect", trace)
    service = LearningService(tmp_path / "sandbox", generate_demo_artworks(2))
    assert connections == []
    try:
        await service.open_session()
    finally:
        await asyncio.to_thread(service.close)
    assert len({thread for _, _, thread in connections}) == 1
    assert get_ident() not in {thread for _, _, thread in connections}
    readonly = [(path, options) for path, options, _ in connections if options.get("uri")]
    assert len(readonly) == 1
    assert readonly[0][0].endswith("?mode=ro&immutable=1")
