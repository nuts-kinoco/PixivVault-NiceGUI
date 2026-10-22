"""UI completion, retries and lifetime while storage is awaiting I/O."""

import asyncio
from concurrent.futures import Future
from types import SimpleNamespace

import pytest
from nicegui import Client, ui

from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.learning_view import LearningView


class DelayedStorage:
    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.attempts = []
        self.released = []
        self.fail = False
        self.fail_open = False
        self.revert_error = None
        self.submit_error = None
        self.open_entered = asyncio.Event()
        self.open_release = asyncio.Event()
        self.open_release.set()
        self.worker_tasks = []
        self.last_future = None
        self.judged = set()
        self.session_count = 0

    async def open_session(self):
        self.open_entered.set()
        await self.open_release.wait()
        if self.fail_open:
            raise OSError("storage unavailable")
        self.session_count += 1
        return SimpleNamespace(session_id=str(self.session_count), judged_ids=frozenset(self.judged))

    def submit_record(self, session_id, judgment):
        if self.submit_error:
            raise self.submit_error
        self.attempts.append((session_id, judgment))
        return self._submit(judgment, undo=False)

    def submit_revert(self, session_id, judgment, operation_id):
        if self.submit_error:
            raise self.submit_error
        return self._submit(judgment, undo=True)

    def _submit(self, judgment, *, undo):
        pending = Future()
        self.last_future = pending
        self.worker_tasks.append(asyncio.create_task(self._write(pending, judgment, undo=undo)))
        return pending

    async def _write(self, pending, judgment, *, undo):
        self.entered.set()
        await self.release.wait()
        if undo and self.revert_error:
            pending.set_exception(self.revert_error)
        elif self.fail:
            pending.set_exception(OSError("storage unavailable"))
        else:
            if undo:
                self.judged.discard(judgment.work_id)
            else:
                self.judged.add(judgment.work_id)
            pending.set_result(None)

    def release_session(self, session_id):
        # The real service puts release after accepted writes in the same queue.
        if self.last_future is not None and not self.last_future.done():
            self.last_future.add_done_callback(lambda _: self.released.append(session_id))
        else:
            self.released.append(session_id)

    def close(self):
        pass


@pytest.fixture
async def persistent_view(monkeypatch):
    tasks = []
    def start(awaitable, **_):
        task = asyncio.create_task(awaitable)
        tasks.append(task)
        return task
    monkeypatch.setattr("pixivvault_nicegui.ui.learning_view.background_tasks.create", start)
    services = ApplicationServices(count=3)
    storage = DelayedStorage()
    services.learning = storage
    client = Client(ui.page("/_learning_persistence_test"))
    with client, ui.card():
        view = LearningView(services)
    view.set_active(True)
    await tasks[0]
    view._on_loaded(SimpleNamespace(args=view._image_url))
    try:
        yield view, storage, tasks
    finally:
        storage.release.set()
        storage.open_release.set()
        await asyncio.gather(*tasks, *storage.worker_tasks, return_exceptions=True)
        view.close()
        services.close()
        client.delete()


def captured(view, operation_id="save-1"):
    return dict(work_id=view.state.current_artwork.work_id, generation=view.state.generation,
                operation_id=operation_id)


async def test_awaiting_save_keeps_current_image_and_allows_tab_leave(persistent_view):
    view, storage, tasks = persistent_view
    payload, image = captured(view), view._image_url
    assert view.judge("like", payload)
    await asyncio.wait_for(storage.entered.wait(), 1)
    assert view.state.saving and view.state.index == 0
    assert view._image_url == image
    assert not view.judge("hate", captured(view, "save-2"))
    view.request_image()
    view._on_undo(SimpleNamespace(args=captured(view, "undo-1")))
    assert view._image_url == image and len(storage.attempts) == 1
    view.set_active(False)
    assert not view.active and view.state.index == 0
    storage.release.set()
    await tasks[-1]
    assert view.state.index == 1 and not view.active and not view.state.saving
    assert storage.judged == {"demo-0001"}


async def test_failed_save_retries_identical_operation_and_advances_after_success(persistent_view):
    view, storage, tasks = persistent_view
    image = view._image_url
    storage.fail = True
    storage.release.set()
    assert view.judge("like", captured(view, "same-id"))
    await tasks[-1]
    assert view.state.index == 0 and view.state.ready and view._image_url == image
    assert not storage.judged and "保存できませんでした" in view.status.text
    assert view.save_retry_button.visible
    storage.fail = False
    view.retry_save()
    await tasks[-1]
    assert view.state.index == 1 and storage.judged == {"demo-0001"}
    assert [judgment.operation_id for _, judgment in storage.attempts] == ["same-id", "same-id"]
    assert not view.save_retry_button.visible


async def test_deleted_client_releases_session_only_after_accepted_write(persistent_view):
    view, storage, tasks = persistent_view
    assert view.judge("like", captured(view))
    # No await between acceptance and client deletion: even an unstarted waiter
    # cannot be responsible for submitting the write.
    assert len(storage.attempts) == 1
    view.close()
    assert view.state.closed and storage.released == []
    storage.release.set()
    await tasks[-1]
    assert storage.judged == {"demo-0001"} and storage.released == ["1"]
    assert view.state.closed and view.state.index == 0
    view.close()
    assert storage.released == ["1"]


@pytest.mark.parametrize("started", [False, True])
async def test_cancelled_waiter_does_not_cancel_accepted_save(persistent_view, started):
    view, storage, tasks = persistent_view
    assert view.judge("like", captured(view))
    if started:
        await asyncio.sleep(0)
    tasks[-1].cancel()
    view.close()
    storage.release.set()
    await asyncio.gather(tasks[-1], return_exceptions=True)
    await asyncio.gather(*storage.worker_tasks)
    assert storage.judged == {"demo-0001"}
    assert not storage.last_future.cancelled()
    assert storage.released == ["1"]


async def test_rejected_submission_is_not_acknowledged(persistent_view):
    view, storage, tasks = persistent_view
    storage.submit_error = RuntimeError("service closed")
    assert not view.judge("like", captured(view))
    assert not view.state.saving and view.state.index == 0
    assert "保存を再試行" in view.status.text
    assert storage.attempts == []


async def test_service_shutdown_does_not_refresh_a_closed_image_catalog(persistent_view):
    view, storage, tasks = persistent_view
    assert view.judge("like", captured(view))
    view.services.close()
    storage.release.set()
    await tasks[-1]
    assert view.state.closed and storage.judged == {"demo-0001"}
    assert storage.released == ["1"]


async def test_reload_response_after_close_releases_new_session(persistent_view):
    view, storage, tasks = persistent_view
    storage.open_release.clear()
    storage.open_entered.clear()
    view.reload_storage()
    await storage.open_entered.wait()
    view.close()
    assert storage.released == ["1"]
    storage.open_release.set()
    await tasks[-1]
    assert view.state.closed and storage.released == ["1", "2"]


async def test_events_during_reload_are_rejected(persistent_view):
    view, storage, tasks = persistent_view
    old = captured(view)
    view.request_image()
    old_image = view._image_url
    storage.open_release.clear()
    view.reload_storage()
    message = view.status.text
    assert not view.judge("like", old)
    view._on_undo(SimpleNamespace(args=old))
    view._on_loaded(SimpleNamespace(args=old_image))
    view._on_failed(SimpleNamespace(args=old_image))
    assert not storage.attempts and view.status.text == message
    storage.open_release.set()
    await tasks[-1]


async def test_conflict_then_failed_reload_blocks_writes_until_resynchronized(persistent_view):
    from pixivvault_nicegui.learning_repository import LearningConflict

    view, storage, tasks = persistent_view
    storage.release.set()
    assert view.judge("like", captured(view))
    await tasks[-1]
    view._on_loaded(SimpleNamespace(args=view._image_url))
    storage.revert_error = LearningConflict("newer judgment")
    storage.fail_open = True
    view._on_undo(SimpleNamespace(args=captured(view, "undo-1")))
    await tasks[-1]
    await tasks[-1]  # conflict creates a separate reload task
    assert not view._storage_ready and not view._storage_busy
    assert "保存状態を再読込" in view.status.text
    assert not view.judge("hate", captured(view, "forbidden"))
    view.retry_save()
    view._on_undo(SimpleNamespace(args=captured(view, "forbidden-undo")))
    assert len(storage.attempts) == 1 and storage.judged == {"demo-0001"}
    storage.fail_open = False
    view.reload_storage()
    await tasks[-1]
    assert view._storage_ready and not view.state.can_undo
    assert view.state.current_artwork.work_id == "demo-0002"
    assert storage.released == ["1"]


async def test_only_last_of_two_persisted_judgments_can_be_undone(persistent_view):
    view, storage, tasks = persistent_view
    storage.release.set()
    for operation in ("first", "second"):
        assert view.judge("like", captured(view, operation))
        await tasks[-1]
        view._on_loaded(SimpleNamespace(args=view._image_url))
    view._on_undo(SimpleNamespace(args=captured(view, "undo")))
    await tasks[-1]
    assert not view.state.can_undo and view.state.index == 1
    assert storage.judged == {"demo-0001"}
    view._on_undo(SimpleNamespace(args=captured(view, "undo-again")))
    assert view.state.index == 1 and storage.judged == {"demo-0001"}


@pytest.mark.parametrize("event", ["load", "error"])
async def test_late_image_keeps_storage_recovery_message(persistent_view, event):
    view, storage, tasks = persistent_view
    view.request_image()
    old_image = view._image_url
    storage.fail_open = True
    view.reload_storage()
    await tasks[-1]
    assert not view._storage_ready
    message = view.status.text
    assert "保存状態を再読込" in message
    handler = view._on_loaded if event == "load" else view._on_failed
    handler(SimpleNamespace(args=old_image))
    assert view.status.text == message
    assert not view.judge("like", captured(view))


async def test_reload_uses_db_snapshot_and_rejects_previous_generation(persistent_view):
    view, storage, tasks = persistent_view
    old = captured(view)
    storage.judged.add("demo-0002")
    view.reload_storage()
    await tasks[-1]
    view._on_loaded(SimpleNamespace(args=view._image_url))
    assert view.state.current_artwork.work_id == "demo-0001"
    assert view.state.generation > old["generation"]
    assert not view.judge("like", old)
    assert [item.work_id for item in view.state.artworks] == ["demo-0001", "demo-0003"]
    assert view._baseline_count == 1 and not view.state.can_undo
    assert storage.released == ["1"]
