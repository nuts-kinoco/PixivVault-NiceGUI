"""M4 UI wiring over real persistent M3. No browser/live service is required."""
import asyncio
from threading import Event, get_ident
from types import SimpleNamespace

from nicegui import Client, ui
import pytest
import pytest_asyncio

from pixivvault_nicegui.novel_save_presentation import build_novel_ui_fixture
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.job_views import QueueView
from pixivvault_nicegui.ui.novel_save_view import NovelDownloadView, NovelLibraryView
from pixivvault_nicegui.ui.shell import DemoShell


async def completed(services, job_id, state="done"):
    def wait():
        with services.jobs._condition:
            assert services.jobs._condition.wait_for(
                lambda: any(j.job_id == job_id and j.state == state for j in services.jobs.snapshot()), timeout=10)
    await asyncio.to_thread(wait)
    return next(j for j in services.jobs.snapshot() if j.job_id == job_id)


@pytest_asyncio.fixture
async def environment(tmp_path):
    services = await asyncio.to_thread(ApplicationServices, 2, novel_fixture_root=tmp_path / "novels",
                                      novel_fixture_source=build_novel_ui_fixture())
    client = Client(ui.page("/_novel_m4_unit"))
    try:
        yield services, client
    finally:
        if client.id in Client.instances:
            client.delete()
        await asyncio.to_thread(services.close)


def event(view, **values):
    return SimpleNamespace(args={"epoch": str(view.epoch), "selectionRevision": str(view.selection_revision), **values})


@pytest.mark.asyncio
async def test_shell_existing_tabs_reach_preview_queue_saved_library_and_plain_reader(environment):
    services, client = environment
    with client:
        shell = DemoShell(services)
        shell.show("download")
        download = shell.job_views["download"]
        assert isinstance(download, NovelDownloadView) and download.output_format == "EPUB"
        download.change_work_id("123")
        assert await download.lookup()
        assert services.jobs.snapshot() == () and "<script>" in download.preview_body.text
        assert await download.submit()
        assert not await download.submit()
        job = services.jobs.snapshot()[0]
        await completed(services, job.job_id)
        shell.show("queue")
        queue = shell.job_views["queue"]
        queue.refresh()
        assert isinstance(queue, QueueView)
        assert queue.rows[job.job_id]["title"].text == "小説 123"
        assert "保存済み" in queue.rows[job.job_id]["result"].text
        shell.show("library")
        library = shell.library
        assert isinstance(library, NovelLibraryView)
        await library._loading_task
        assert len(library.entries) == 1
        assert await library.read(library.entries[0])
        assert "<script>" in library.reader_body.text
        assert library.dialog.value
        shell.show("download")
        assert not library.dialog.value and library.reader_text == ""
        shell.close()
        assert not services.jobs.closed


@pytest.mark.asyncio
async def test_format_change_invalidates_preview_and_preserves_original_three_choices(environment):
    services, client = environment
    with client:
        view = NovelDownloadView(services, navigate=lambda _: None)
        view.change_work_id("123")
        assert await view.lookup()
        stale = event(view)
        view.change_format("BOTH")
        assert view.selection is None and not view.submit_button.enabled
        await view._submit_event(stale)
        assert services.jobs.snapshot() == ()
        assert await view.lookup()
        assert view.selection.intent.output_format == "BOTH"
        assert await view.submit()
        job = services.jobs.snapshot()[0]
        await completed(services, job.job_id)
        saved = await asyncio.to_thread(services.current_novel_export, "123", "BOTH")
        assert saved.output_names == ("novel.epub", "novel.txt")
        assert await view.lookup()
        assert not view.submit_button.enabled and "保存済み" in view.saved_label.text


@pytest.mark.asyncio
async def test_rapid_input_events_are_not_lost_when_browser_revision_lags(environment):
    services, client = environment
    with client:
        view = NovelDownloadView(services, navigate=lambda _: None)
        original_epoch = str(view.epoch)
        for value in ("1", "12", "123"):
            view._input_event(SimpleNamespace(args={"epoch": original_epoch, "selectionRevision": "0", "value": value}))
        assert view.work_id == "123" and view.selection_revision == 3
        assert await view.lookup()
        assert services.jobs.snapshot() == ()


@pytest.mark.asyncio
async def test_preview_thread_and_stale_response_cannot_restore_changed_selection(environment, monkeypatch):
    services, client = environment
    entered, release = Event(), Event()
    main_thread = get_ident()
    with client:
        view = NovelDownloadView(services, navigate=lambda _: None)
        original = view.presentation.preview
        def slow(*args):
            assert get_ident() != main_thread
            entered.set()
            assert release.wait(10)
            return original(*args)
        monkeypatch.setattr(view.presentation, "preview", slow)
        view.change_work_id("123")
        task = asyncio.create_task(view.lookup())
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            view.change_work_id("124")
            release.set()
            assert not await task
            assert view.work_id == "124" and view.selection is None
            assert not view.submit_button.enabled and services.jobs.snapshot() == ()
        finally:
            release.set()
            await task


@pytest.mark.asyncio
async def test_old_browser_epoch_cannot_enqueue_after_tab_return(environment):
    services, client = environment
    with client:
        view = NovelDownloadView(services, navigate=lambda _: None)
        view.change_work_id("123")
        assert await view.lookup()
        stale = event(view)
        view.set_active(False)
        view.set_active(True)
        assert await view.lookup()
        await view._submit_event(stale)
        assert services.jobs.snapshot() == ()


@pytest.mark.asyncio
async def test_two_clients_stale_preview_does_not_rebase_or_duplicate_saved_generation(environment):
    services, client = environment
    other = Client(ui.page("/_novel_other"))
    try:
        with client:
            first = NovelDownloadView(services, navigate=lambda _: None)
            first.change_work_id("123")
            assert await first.lookup()
        with other:
            second = NovelDownloadView(services, navigate=lambda _: None)
            second.change_work_id("123")
            assert await second.lookup()
        with client:
            assert await first.submit()
        job = services.jobs.snapshot()[0]
        await completed(services, job.job_id)
        with other:
            assert not await second.submit()
            assert "追加できません" in second.notice.text
            assert await second.lookup()
            assert second.selection.saved is not None and not second.submit_button.enabled
        assert len(list((services.novel_jobs.store.root / "outputs").iterdir())) == 1
    finally:
        other.delete()


@pytest.mark.asyncio
async def test_accepted_enqueue_survives_view_close_and_uses_off_loop_thread(environment, monkeypatch):
    services, client = environment
    entered, release = Event(), Event()
    main_thread = get_ident()
    original = services.enqueue_novel_export
    def slow(*args):
        assert get_ident() != main_thread
        entered.set()
        assert release.wait(10)
        return original(*args)
    monkeypatch.setattr(services, "enqueue_novel_export", slow)
    with client:
        view = NovelDownloadView(services, navigate=lambda _: None)
        view.change_work_id("123")
        assert await view.lookup()
        task = asyncio.create_task(view.submit())
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            view.close()
            release.set()
            assert await task
            job = services.jobs.snapshot()[0]
            await completed(services, job.job_id)
            assert not services.jobs.closed
        finally:
            release.set()
            await task


@pytest.mark.asyncio
async def test_existing_failed_queue_retry_uses_same_novel_job_and_new_attempt(environment, monkeypatch):
    services, client = environment
    original = services.novel_jobs.store.save
    count = []
    def fail_once(*args, **kwargs):
        count.append(1)
        if len(count) == 1:
            raise OSError("synthetic private details")
        return original(*args, **kwargs)
    monkeypatch.setattr(services.novel_jobs.store, "save", fail_once)
    with client:
        view = NovelDownloadView(services, navigate=lambda _: None)
        view.change_work_id("123")
        assert await view.lookup() and await view.submit()
        job = services.jobs.snapshot()[0]
        error = await completed(services, job.job_id, "error")
        failed = QueueView(services, failed_only=True)
        assert job.job_id in failed.rows
        assert await failed.command(job.job_id, "retry", error.revision)
        done = await completed(services, job.job_id)
        assert done.attempt == 2
        failed.refresh()
        assert failed.rows == {}


@pytest.mark.asyncio
async def test_saved_reader_is_paged_and_old_rendered_row_cannot_open_after_refresh(environment):
    services, client = environment
    with client:
        view = NovelDownloadView(services, navigate=lambda _: None)
        view.change_work_id("124")
        assert await view.lookup() and await view.submit()
        await completed(services, services.jobs.snapshot()[0].job_id)
        library = NovelLibraryView(services)
        library.set_active(True)
        await library._loading_task
        entry = library.entries[0]
        old_generation = library.generation
        old_event = SimpleNamespace(args={"epoch": str(library.epoch), "generation": str(old_generation)})
        assert await library.refresh()
        await library._read_event(old_event, entry, old_generation)
        assert not library.dialog.value
        assert await library.read(library.entries[0])
        assert len(library.reader_body.text) <= library.PAGE_CHARS
        assert library.reader_next.enabled
        first = library.reader_body.text
        library.move_reader(1)
        assert library.reader_body.text != first and library.reader_previous.enabled
        library.set_active(False)
        assert not library.dialog.value

@pytest.mark.asyncio
@pytest.mark.parametrize("index, expected", [(0, "EPUB"), (1, "TXT"), (2, "BOTH")])
async def test_select_browser_payload_resolves_fixed_format_key(environment, index, expected):
    services, client = environment
    with client:
        view = NovelDownloadView(services, navigate=lambda _: None)
        view.change_work_id("123")
        assert await view.lookup()
        old_submit = event(view)
        view._format_event(event(view, value={"value": index, "label": "untrusted label"}))
        assert view.output_format == expected
        assert view.selection is None and not view.submit_button.enabled
        await view._submit_event(old_submit)
        assert services.jobs.snapshot() == ()
        assert await view.lookup()
        assert view.selection.intent.output_format == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [None, "TXT", 1, {}, {"value": True}, {"value": -1},
                                    {"value": 3}, {"value": "1"}, {"label": "TXTのみ"}])
async def test_invalid_select_browser_payload_does_not_change_selection(environment, payload):
    services, client = environment
    with client:
        view = NovelDownloadView(services, navigate=lambda _: None)
        view.change_work_id("123")
        assert await view.lookup()
        selection = view.selection
        view._format_event(event(view, value=payload))
        assert view.output_format == "EPUB" and view.selection is selection
        assert services.jobs.snapshot() == ()


@pytest.mark.asyncio
async def test_old_epoch_select_browser_payload_is_rejected(environment):
    services, client = environment
    with client:
        view = NovelDownloadView(services, navigate=lambda _: None)
        stale = event(view, value={"value": 1, "label": "TXTのみ"})
        view.set_active(False)
        view.set_active(True)
        view._format_event(stale)
        assert view.output_format == "EPUB"

@pytest.mark.asyncio
async def test_library_reactivation_hides_old_rows_until_revalidated(environment, monkeypatch):
    services, client = environment
    entered, release = Event(), Event()
    with client:
        library = NovelLibraryView(services)
        library.set_active(True)
        await library._loading_task
        assert library.list.visible
        library.set_active(False)
        original = library.presentation.library
        def slow():
            entered.set()
            assert release.wait(10)
            return original()
        monkeypatch.setattr(library.presentation, 'library', slow)
        library.set_active(True)
        try:
            assert not library.list.visible
            assert await asyncio.to_thread(entered.wait, 10)
            assert not library.list.visible
        finally:
            release.set()
            await library._loading_task
        assert library.list.visible and not library.busy
        library.close()
