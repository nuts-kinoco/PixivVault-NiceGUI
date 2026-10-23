"""Synthetic checks for managed bookmark download UI boundaries."""

import asyncio
from types import SimpleNamespace

from nicegui import Client, ui
import pytest

from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
from pixivvault_nicegui.ui.managed_download_view import ManagedDownloadView


def record(index, illustration_type=0, kind="illustration"):
    return RemoteBookmark(str(index), kind, "show", f"作品 {index}", "11",
                          "架空作者", (), "https://i.pximg.net/a.jpg", "",
                          2, illustration_type, 0)


class FakeServices:
    def __init__(self, candidates):
        self.candidates = tuple(candidates)
        self.managed_downloads = object()
        self.jobs = object()
        self.accepted = []
        self.fail_refresh = False
        self.hold = None

    def download_candidates(self):
        return self.candidates

    async def refresh_bookmark_cache(self):
        if self.hold is not None:
            await self.hold.wait()
        if self.fail_refresh:
            raise RuntimeError("sensitive response")
        return (), ()

    def enqueue_managed_download(self, item, operation_id, *, owner=None):
        if item not in self.candidates:
            raise ValueError("candidate changed")
        self.accepted.append((item, operation_id))


@pytest.mark.asyncio
async def test_cached_candidates_filter_search_selection_and_submit():
    services = FakeServices([record(i) for i in range(1, 57)]
                            + [record(60, 2), record(61, None), record(62, kind="novel")])
    client = Client(ui.page("/_managed_unit"))
    try:
        with client:
            view = ManagedDownloadView(services)
            assert len(view.records) == 56
            assert len(view._choices) == 50
            assert "50 / 56" in view.counter.text
            view.select("1", True)
            pinned = view.selected["1"]
            view.search("作品 2")
            assert view.selected["1"] is pinned
            assert len(view._choices) == 11
            view.search("")
            assert await view.submit()
            assert services.accepted[0][0] is pinned
            assert not view.selected
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_refresh_failure_keeps_cache_and_late_result_does_not_replace_view():
    services = FakeServices([record(1)])
    client = Client(ui.page("/_managed_refresh"))
    try:
        with client:
            view = ManagedDownloadView(services)
            services.fail_refresh = True
            assert not await view.refresh_bookmarks()
            assert tuple(view.records) == ("1",)
            assert "以前の一覧" in view.notice.text
            services.fail_refresh = False
            services.hold = asyncio.Event()
            task = asyncio.create_task(view.refresh_bookmarks())
            await asyncio.sleep(0)
            view.set_active(False)
            services.candidates = (record(2),)
            services.hold.set()
            assert not await task
            assert tuple(view.records) == ("1",)
            view.set_active(True)
            assert await view.refresh_bookmarks()
            assert tuple(view.records) == ("2",)
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_two_clients_isolated_and_stale_selection_rejected():
    services = FakeServices([record(1)])
    one = Client(ui.page("/_managed_one"))
    two = Client(ui.page("/_managed_two"))
    try:
        with one:
            first = ManagedDownloadView(services)
            first.select("1", True)
            old_epoch = first.epoch
            old_revision = first.selection_revision
        with two:
            second = ManagedDownloadView(services)
            assert not second.selected
            second.select("1", True)
        with one:
            first.set_active(False)
            first.set_active(True)
            assert not await first.submit(old_epoch)
            assert first.selection_revision == old_revision
            assert first.selected
            assert not first._valid(old_epoch)
            first.close()
            assert not await first.submit()
        with two:
            assert await second.submit()
            assert len(services.accepted) == 1
    finally:
        one.delete()
        two.delete()


@pytest.mark.asyncio
async def test_pinned_record_change_rejected_without_silent_replacement():
    services = FakeServices([record(1)])
    client = Client(ui.page("/_managed_pin"))
    try:
        with client:
            view = ManagedDownloadView(services)
            view.select("1", True)
            services.candidates = (record(1, 1),)
            assert not await view.submit()
            assert view.selected["1"].illustration_type == 0
            assert not services.accepted
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_stale_browser_handlers_and_selection_revision_are_rejected():
    services = FakeServices([record(1), record(2)])
    client = Client(ui.page("/_managed_events"))
    try:
        with client:
            view = ManagedDownloadView(services)
            old_session = view._render_session
            event = SimpleNamespace(args={
                "value": True, "epoch": "0", "selectionRevision": "0"})
            view.select("2", True)
            view._select_event(event, "1", old_session)
            assert "1" not in view.selected
            view.render()
            assert old_session is not view._render_session
            view._select_event(SimpleNamespace(args={
                "value": True, "epoch": "0", "selectionRevision": "1"}),
                "1", old_session)
            assert "1" not in view.selected
            view.set_active(False)
            view._more_event(SimpleNamespace(args={"epoch": "0"}), view._render_session)
            assert view.shown == 50
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_concurrent_submit_is_single_accepted_operation():
    services = FakeServices([record(1)])
    client = Client(ui.page("/_managed_double"))
    gate = asyncio.Event()
    async def slow_accepted(fn, item, operation):
        await gate.wait()
        fn(item, operation)
    try:
        with client:
            view = ManagedDownloadView(services)
            view.select("1", True)
            from pixivvault_nicegui.ui import managed_download_view as module
            original = module._accepted_call
            module._accepted_call = slow_accepted
            try:
                task = asyncio.create_task(view.submit())
                await asyncio.sleep(0)
                assert not await view.submit()
                gate.set()
                assert await task
            finally:
                module._accepted_call = original
            assert len(services.accepted) == 1
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_accepted_submit_does_not_replace_a_new_visit_notice(monkeypatch):
    services = FakeServices([record(1)])
    client = Client(ui.page("/_managed_late_submit"))
    from pixivvault_nicegui.ui import managed_download_view as module
    gate = asyncio.Event()
    async def accepted(fn, item, operation):
        await gate.wait()
        fn(item, operation)
    monkeypatch.setattr(module, "_accepted_call", accepted)
    try:
        with client:
            view = ManagedDownloadView(services)
            view.select("1", True)
            task = asyncio.create_task(view.submit())
            await asyncio.sleep(0)
            view.set_active(False)
            view.notice.set_text("new visit status")
            gate.set()
            assert await task
            assert len(services.accepted) == 1 and not view.selected
            assert view.notice.text == "new visit status"
            view.set_active(True)
            assert view.selection_count.text == "対象 0 件"
            assert not view.submit_button.enabled
    finally:
        gate.set()
        client.delete()
