"""Per-client UI wiring over the real job service; no browser or live account."""

import asyncio
from threading import Event
from types import SimpleNamespace

from nicegui import Client, ui
import pytest
import pytest_asyncio

from pixivvault_nicegui.job_service import JobService
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.job_views import DownloadView, QueueView
from pixivvault_nicegui.ui.shell import DemoShell


async def completed(service, job_id, state="done"):
    def wait():
        with service._condition:
            assert service._condition.wait_for(
                lambda: any(job.job_id == job_id and job.state == state for job in service.snapshot()), timeout=5)
    await asyncio.to_thread(wait)


@pytest_asyncio.fixture
async def environment():
    services = ApplicationServices(12)
    services.jobs = JobService(lambda *_: "verified-test-receipt")
    client = Client(ui.page("/_job_unit"))
    try:
        yield services, client
    finally:
        if client.id in Client.instances:
            client.delete()
        await asyncio.to_thread(services.close)


@pytest.mark.asyncio
async def test_default_download_explains_missing_storage_without_enqueuing():
    services = ApplicationServices(3)
    client = Client(ui.page("/_jobs_disabled"))
    try:
        with client:
            download = DownloadView(services)
            queue = QueueView(services)
            download.select("demo-0001", True)
            assert not download.submit_button.enabled
            assert "保存先未指定" in download.notice.text
            assert not await download.submit()
            assert not queue.rows
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_selected_targets_survive_search_and_create_real_jobs(environment):
    services, client = environment
    with client:
        view = DownloadView(services)
        view.select("demo-0001", True)
        view.select("demo-0003", True)
        view.search("0003")
        assert view.selected == {"demo-0001", "demo-0003"}
        assert await view.submit()
        assert not view.selected
        assert len(services.jobs.snapshot()) == 2
        assert not await view.submit()
    for job in services.jobs.snapshot():
        await completed(services.jobs, job.job_id)


@pytest.mark.asyncio
async def test_queue_eight_rows_pagination_search_and_stable_refresh(environment):
    services, client = environment
    for index in range(11):
        job = services.jobs.enqueue("demo", str(index), f"合成 {index}")
        await completed(services.jobs, job.job_id)
    with client:
        view = QueueView(services)
        assert len(view.rows) == 8
        assert view.page_label.text == "1 / 2"
        existing = {key: row["root"].id for key, row in view.rows.items()}
        elements_before = len(client.elements)
        for _ in range(5):
            view.refresh()
        assert len(client.elements) == elements_before
        assert existing == {key: row["root"].id for key, row in view.rows.items()}
        view.move_page(1)
        assert len(view.rows) == 3
        assert view.page_label.text == "2 / 2"
        view.search("合成 10")
        assert len(view.rows) == 1 and view.page == 0


@pytest.mark.asyncio
async def test_failed_view_retry_uses_new_attempt_and_disappears(environment):
    services, client = environment
    services.jobs.close()
    def runner(job, context):
        if job.attempt == 1:
            raise ValueError("private error payload")
        return "receipt"
    services.jobs = JobService(runner)
    job = services.jobs.enqueue("demo", "one")
    await completed(services.jobs, job.job_id, "error")
    with client:
        view = QueueView(services, failed_only=True)
        assert list(view.rows) == [job.job_id]
        snapshot = services.jobs.snapshot()[0]
        assert await view.command(job.job_id, "retry", snapshot.revision)
    await completed(services.jobs, job.job_id)
    with client:
        view.refresh()
        assert view.rows == {}
    assert services.jobs.snapshot()[0].attempt == 2


@pytest.mark.asyncio
async def test_old_browser_epochs_do_not_mutate_after_tab_return(environment):
    services, client = environment
    job = services.jobs.enqueue("demo", "one")
    await completed(services.jobs, job.job_id)
    with client:
        view = QueueView(services)
        epoch = view.epoch
        view.set_active(False)
        assert not view.timer.active
        view.set_active(True)
        assert view.timer.active and view.epoch != epoch
        assert not await view.command(job.job_id, "delete", services.jobs.snapshot()[0].revision, epoch)
        assert len(services.jobs.snapshot()) == 1
        download = DownloadView(services)
        old = download.epoch
        download.select("demo-0001", True)
        download.set_active(False)
        download.set_active(True)
        assert not await download.submit(old)
        assert len(services.jobs.snapshot()) == 1


@pytest.mark.asyncio
async def test_shell_close_keeps_shared_app_jobs_and_client_filters_independent(environment):
    services, client = environment
    other = Client(ui.page("/_job_other"))
    try:
        with client:
            first = DemoShell(services)
            first.show("queue")
            first.job_views["queue"].search("first")
        with other:
            second = DemoShell(services)
            second.show("queue")
            assert second.job_views["queue"].query == ""
            second.show("download")
            assert second.job_views["download"].root.visible
        client.delete()
        assert first.closed and first.job_views["queue"].closed
        assert not services.jobs.closed and not second.closed
    finally:
        other.delete()


@pytest.mark.asyncio
async def test_duplicate_submit_waits_for_app_accepted_work(environment, monkeypatch):
    services, client = environment
    entered, release = Event(), Event()
    real = services.jobs.enqueue
    calls = []
    def slow(*args, **kwargs):
        calls.append(True)
        entered.set()
        assert release.wait(5)
        return real(*args, **kwargs)
    monkeypatch.setattr(services.jobs, "enqueue", slow)
    with client:
        view = DownloadView(services)
        view.select("demo-0001", True)
        task = asyncio.create_task(view.submit())
        assert await asyncio.to_thread(entered.wait, 5)
        assert not await view.submit()
        release.set()
        assert await task
        assert len(calls) == 1


@pytest.mark.asyncio
async def test_selection_across_pages_filters_and_clients_and_bulk_pause(environment):
    services, client = environment
    entered, release = Event(), Event()
    services.jobs.close()
    def runner(job, context):
        if job.target_id == "blocker":
            entered.set()
            assert release.wait(5)
        context.checkpoint()
        return "receipt"
    services.jobs = JobService(runner)
    blocker = services.jobs.enqueue("demo", "blocker")
    assert await asyncio.to_thread(entered.wait, 5)
    jobs = [services.jobs.enqueue("demo", f"target-{i}", f"item {i}") for i in range(12)]
    other = Client(ui.page("/_queue_isolation"))
    try:
        with client:
            view = QueueView(services)
            view.enter_selection()
            view.search("item")
            view.select_all_filtered()
            assert len(view.selected) == 12
            assert len(view.rows) == 8
            view.set_filter("paused")
            assert len(view.selected) == 12
            view.set_filter("all")
        with other:
            independent = QueueView(services)
            assert independent.selected == set() and not independent.selection_mode
        with client:
            assert await view.bulk("pause")
            assert all(next(item for item in services.jobs.snapshot() if item.job_id == job.job_id).state == "paused"
                       for job in jobs)
            assert not view.selected
            assert "受理12件" in view.notice.text
            assert view.selection_mode
            assert not await view.bulk("pause")
            view.select_job(jobs[0].job_id, True)
            old_epoch = view.epoch
            view.set_active(False)
            view.set_active(True)
            assert not await view.bulk("resume", old_epoch)
            assert view.selected == {jobs[0].job_id}
            assert await view.bulk("resume")
        release.set()
        await completed(services.jobs, blocker.job_id)
        await completed(services.jobs, jobs[0].job_id)
    finally:
        release.set()
        other.delete()


@pytest.mark.asyncio
async def test_download_reactivation_preserves_list_elements(environment):
    services, client = environment
    with client:
        view = DownloadView(services)
        original = view.list.id
        children = tuple(view.list.default_slot.children)
        view.select("demo-0001", True)
        view.set_active(False)
        view.set_active(True)
        assert view.list.id == original
        assert tuple(view.list.default_slot.children) == children
        assert view.selected == {"demo-0001"}


@pytest.mark.asyncio
async def test_stale_checkbox_after_filter_and_bulk_generation_are_rejected(environment):
    services, client = environment
    jobs = [services.jobs.enqueue("demo", f"stale-{i}", f"item {i}") for i in range(3)]
    await asyncio.gather(*(completed(services.jobs, job.job_id) for job in jobs))
    with client:
        view = QueueView(services)
        view.enter_selection()
        revision = view.selection_revision
        view.search("item 0")
        assert not view.select_job(jobs[1].job_id, True, view.epoch, revision)
        assert jobs[1].job_id not in view.selected
        view.select_job(jobs[0].job_id, True)
        captured = view.selection_revision
        view.select_job(jobs[0].job_id, False)
        view.select_job(jobs[1].job_id, True)
        assert not await view.bulk("delete", view.epoch, captured)
        assert any(job.job_id == jobs[1].job_id for job in services.jobs.snapshot())


@pytest.mark.asyncio
async def test_confirmed_delete_never_substitutes_new_selection_or_changed_job(environment):
    services, client = environment
    jobs = [services.jobs.enqueue("demo", f"confirm-{i}") for i in range(2)]
    await asyncio.gather(*(completed(services.jobs, job.job_id) for job in jobs))
    with client:
        view = QueueView(services)
        view.enter_selection()
        view.select_job(jobs[0].job_id, True)
        captured_revision = view.selection_revision
        frozen = ((jobs[0].job_id, next(j.control_revision for j in services.jobs.snapshot()
                                      if j.job_id == jobs[0].job_id)),)
        view.select_job(jobs[0].job_id, False)
        view.select_job(jobs[1].job_id, True)
        assert not await view.bulk("delete", view.epoch, captured_revision, frozen)
        assert {j.job_id for j in services.jobs.snapshot()} == {j.job_id for j in jobs}
        view.select_job(jobs[1].job_id, False)
        view.select_job(jobs[0].job_id, True)
        frozen = ((jobs[0].job_id, next(j.control_revision for j in services.jobs.snapshot()
                                      if j.job_id == jobs[0].job_id)),)
        # A different operation wins while the confirmation dialog is open.
        assert services.jobs.control(jobs[0].job_id, "delete")
        assert not await view.bulk("delete", view.epoch, view.selection_revision, frozen)
        assert any(j.job_id == jobs[1].job_id for j in services.jobs.snapshot())


@pytest.mark.asyncio
async def test_browser_event_missing_or_malformed_generations_fail_closed(environment):
    services, client = environment
    job = services.jobs.enqueue("demo", "bad-payload")
    await completed(services.jobs, job.job_id)
    with client:
        queue = QueueView(services)
        queue.enter_selection()
        download = DownloadView(services)
        for malformed in ({}, {"epoch": None}, {"epoch": True}, {"epoch": "oops"}):
            event = SimpleNamespace(args=malformed)
            queue._search_event(event)
            queue._filter_event(event, "error")
            queue._page_event(event, 1)
            queue._selection_event(event, queue.exit_selection)
            queue._select_job_event(event, job.job_id)
            await queue._bulk_event(event, "delete")
            await queue._bulk_delete_event(event)
            await queue._action_event(event)
            download._search_event(event)
            download._select_event(event, "demo-0001")
            await download._submit_event(event)
        assert queue.selection_mode and not queue.selected and queue.query == ""
        assert not download.selected and download.query == ""
        assert any(item.job_id == job.job_id for item in services.jobs.snapshot())
        for malformed in (
            {"epoch": str(queue.epoch)},
            {"epoch": str(queue.epoch), "selectionRevision": None, "viewGeneration": str(queue.view_generation)},
            {"epoch": str(queue.epoch), "selectionRevision": True, "viewGeneration": str(queue.view_generation)},
            {"epoch": str(queue.epoch), "selectionRevision": str(queue.selection_revision), "viewGeneration": "bad"},
        ):
            queue._select_job_event(SimpleNamespace(args={**malformed, "value": True}), job.job_id)
            await queue._bulk_event(SimpleNamespace(args=malformed), "delete")
        assert not queue.selected
        assert any(item.job_id == job.job_id for item in services.jobs.snapshot())


@pytest.mark.asyncio
async def test_select_all_browser_event_cannot_cross_a_filter_change(environment):
    services, client = environment
    for i in range(2):
        job = services.jobs.enqueue("demo", f"scope-{i}", f"scope {i}")
        await completed(services.jobs, job.job_id)
    with client:
        view = QueueView(services)
        view.enter_selection()
        view.search("scope 0")
        old = {"epoch": str(view.epoch), "selectionRevision": str(view.selection_revision),
               "viewGeneration": str(view.view_generation)}
        view.search("scope 1")
        view._selection_event(SimpleNamespace(args=old), view.select_all_filtered)
        assert not view.selected, "old select-all applied to the newly filtered group"


@pytest.mark.asyncio
async def test_download_more_callback_survives_tab_return_but_not_replaced_list():
    services = ApplicationServices(120)
    client = Client(ui.page("/_download_more_return"))
    try:
        with client:
            view = DownloadView(services)
            button = next(e for e in client.elements.values()
                          if e.props.get("data-testid") == "download-more")
            callback = next(iter(button._event_listeners.values())).handler
            view.set_active(False)
            view.set_active(True)
            callback(SimpleNamespace(args={"epoch": str(view.epoch)}))
            assert view.shown == 100, "retained More button stopped working after tab return"
            view.search("0003")
            callback(SimpleNamespace(args={"epoch": str(view.epoch)}))
            assert view.shown == 50, "discarded More button applied to a new search"
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_old_download_checkbox_cannot_select_an_invisible_new_search_item(environment):
    services, client = environment
    with client:
        view = DownloadView(services)
        checkbox = view._choices["demo-0001"]
        callback = next(listener.handler for listener in checkbox._event_listeners.values()
                        if listener.js_handler is not None)
        view.search("0003")
        callback(SimpleNamespace(args={"epoch": str(view.epoch), "value": True}))
        assert not view.selected


@pytest.mark.asyncio
async def test_old_download_submit_cannot_apply_a_different_selection(environment):
    services, client = environment
    with client:
        view = DownloadView(services)
        view.select("demo-0001", True)
        old = {"epoch": str(view.epoch),
               "selectionRevision": str(view.root.props.get("data-selection-revision", "0"))}
        view.select("demo-0001", False)
        view.select("demo-0003", True)
        await view._submit_event(SimpleNamespace(args=old))
        assert not services.jobs.snapshot(), "old submit enqueued the new selected artwork"
