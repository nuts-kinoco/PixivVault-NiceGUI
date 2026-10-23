"""Direct-ID UI lookup is explicit, client-scoped and immune to late responses."""

import asyncio
from types import SimpleNamespace

from nicegui import Client, ui
import pytest

from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
from pixivvault_nicegui.ui.managed_download_view import ManagedDownloadView


def record():
    return RemoteBookmark("123", "illustration", "show", "作品123", "23", "架空作者",
                          (), "", "", 2, 0, 0)


class FakeServices:
    def __init__(self):
        self.managed_downloads = object()
        self.jobs = object()
        self.candidates = ()
        self.calls = []
        self.fail = False
        self.hold = None
        self.accepted = []
        self.released = []

    def download_candidates(self):
        return self.candidates

    async def lookup_managed_work_id(self, wid, *, owner=None, publish=True):
        self.calls.append(wid)
        if self.hold is not None:
            await self.hold.wait()
        if self.fail:
            raise RuntimeError("private response must be hidden")
        if wid != "123":
            raise ValueError("unsupported")
        self.candidates = (record(),)
        return record()

    def remember_managed_work_id(self, owner, item):
        assert type(owner) is str and len(owner) == 32 and item == record()

    def release_managed_owner(self, owner):
        assert type(owner) is str and len(owner) == 32
        self.released.append(owner)

    async def refresh_bookmark_cache(self):
        return (), ()

    def enqueue_managed_download(self, rec, operation, *, owner=None):
        assert rec in self.candidates
        self.accepted.append((rec, operation))


@pytest.mark.asyncio
async def test_manual_id_lookup_requires_explicit_save_button_and_keeps_bookmark_view():
    services = FakeServices()
    client = Client(ui.page("/_direct_lookup"))
    try:
        with client:
            view = ManagedDownloadView(services)
            assert not view.records and not services.calls
            view.direct_input.set_value("bad ID")
            assert not await view.lookup_work_id()
            assert not services.calls
            view.direct_input.set_value("123")
            assert await view.lookup_work_id()
            assert services.calls == ["123"] and tuple(view.records) == ("123",)
            assert view.query == "123" and "1 / 1" in view.counter.text
            assert not services.accepted, "metadata GET must not enqueue or bookmark"
            view.select("123", True)
            assert await view.submit()
            assert len(services.accepted) == 1 and services.accepted[0][0] == record()
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_late_lookup_after_tab_change_or_search_never_replaces_ui():
    services = FakeServices()
    services.hold = asyncio.Event()
    client = Client(ui.page("/_direct_late"))
    try:
        with client:
            view = ManagedDownloadView(services)
            view.direct_input.set_value("123")
            task = asyncio.create_task(view.lookup_work_id())
            await asyncio.sleep(0)
            view.set_active(False)
            services.hold.set()
            assert not await task
            assert view.query == "" and not view.records and not services.accepted
            view.set_active(True)
            assert await view.lookup_work_id()
            assert tuple(view.records) == ("123",)
            services.hold = asyncio.Event()
            view.search("")
            next_task = asyncio.create_task(view.lookup_work_id())
            await asyncio.sleep(0)
            view.search("different request")
            services.hold.set()
            assert not await next_task
            assert view.query == "different request"
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_failed_lookup_does_not_erase_last_good_candidates():
    services = FakeServices()
    client = Client(ui.page("/_direct_failure"))
    try:
        with client:
            view = ManagedDownloadView(services)
            view.direct_input.set_value("123")
            assert await view.lookup_work_id()
            services.fail = True
            assert not await view.lookup_work_id()
            assert "private response" not in view.notice.text
            assert tuple(view.records) == ("123",)
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_fast_different_checkboxes_and_target_removal_keep_dom_and_state_in_sync():
    services = FakeServices()
    client = Client(ui.page("/_direct_fast_choices"))
    try:
        with client:
            view = ManagedDownloadView(services)
            services.candidates = (record(), RemoteBookmark(
                "124", "illustration", "show", "作品124", "23", "架空作者",
                (), "", "", 2, 0, 0))
            view._load_cached()
            view.render()
            first_revision = str(view.selection_revision)
            session = view._render_session
            for wid in ("123", "124"):
                view._select_event(SimpleNamespace(args={
                    "value": True, "epoch": str(view.epoch),
                    "eventSerial": "1", "selectionRevision": first_revision,
                    "rowGeneration": "0",
                }), wid, session)
            assert set(view.selected) == {"123", "124"}, "fast independent rows lost the second click"
            assert all(view._choices[wid].value for wid in ("123", "124"))
            view._remove_target_event(SimpleNamespace(args={
                "epoch": str(view.epoch),
                "selectionRevision": str(view.selection_revision),
            }), "123")
            assert set(view.selected) == {"124"}
            assert view._choices["123"].value is False, "removal left a checked DOM value"
            assert view._choices["124"].value is True
            # An old same-row update must not resurrect a removed selection.
            view._select_event(SimpleNamespace(args={
                "epoch": str(view.epoch), "value": True, "eventSerial": "3",
                "rowGeneration": "0",
            }), "123", session)
            assert set(view.selected) == {"124"} and not view._choices["123"].value, (
                "a queued checkbox click before chip removal resurrected the old selection")
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_late_lookup_error_does_not_replace_new_search_notice():
    services = FakeServices()
    services.fail = True
    services.hold = asyncio.Event()
    client = Client(ui.page("/_direct_stale_error"))
    try:
        with client:
            view = ManagedDownloadView(services)
            view.direct_input.set_value("123")
            task = asyncio.create_task(view.lookup_work_id())
            await asyncio.sleep(0)
            view.search("different search")
            view.notice.set_text("新しい検索条件です")
            services.hold.set()
            assert not await task
            assert view.notice.text == "新しい検索条件です"
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_close_during_accepted_enqueue_does_not_revoke_its_owner(monkeypatch):
    import pixivvault_nicegui.ui.managed_download_view as module

    services = FakeServices()
    entered, release = asyncio.Event(), asyncio.Event()
    original_call = module._accepted_call

    async def held_call(function, rec, operation):
        entered.set()
        await release.wait()
        return await original_call(function, rec, operation)

    monkeypatch.setattr(module, "_accepted_call", held_call)
    client = Client(ui.page("/_direct_owner_close"))
    try:
        with client:
            view = ManagedDownloadView(services)
            services.candidates = (record(),)
            view._load_cached()
            view.render()
            view.select("123", True)
            task = asyncio.create_task(view.submit())
            await entered.wait()
            view.close()
            assert not services.released, "close revoked an operation already admitted to the app"
            release.set()
            assert await task and len(services.accepted) == 1
            await asyncio.sleep(0)
            assert services.released == [view._owner]
    finally:
        release.set()
        client.delete()
