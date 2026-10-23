"""Author-ID UI is additive to direct-work lookup and preserves safe queue gates."""

import asyncio
from nicegui import Client, ui
import pytest

from pixivvault_nicegui.author_download import Difference
from pixivvault_nicegui.author_download import AuthorTarget
from pixivvault_nicegui.managed_download_requests import ManagedAuthorIntent
from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
from pixivvault_nicegui.ui.managed_download_view import ManagedDownloadView


def record(wid='123'):
    return RemoteBookmark(wid, 'illustration', 'show', 'Synthetic', '91',
                          'Synthetic author', (), '', '', 1, 0, 0)


class Services:
    def __init__(self):
        self.managed_downloads = object()
        self.jobs = object()
        self.published = []
        self.enqueued = []
        self.hold = None
        self.author_calls = 0

    def download_candidates(self):
        return ()

    async def lookup_managed_author(self, uid, *, owner, publish=False):
        assert uid == '91' and not publish
        self.author_calls += 1
        if self.hold:
            await self.hold.wait()
        return (({'id': '123'}, Difference.NEW),
                ({'id': '124'}, Difference.UNKNOWN))

    async def lookup_managed_work_id(self, wid, *, owner, publish=False):
        return record(wid)

    def remember_managed_work_id(self, owner, rec):
        self.published.append(rec)

    def remember_managed_author_batch(self, owner, records):
        self.published.extend(records)
        return tuple(records)

    async def followed_download_authors(self, *, owner):
        return (AuthorTarget('91', 'Followed one', 'follow'),
                AuthorTarget('92', 'Followed two', 'follow'))

    def release_managed_owner(self, owner):
        pass

    def enqueue_managed_download(self, rec, operation, *, owner):
        self.enqueued.append(rec)


@pytest.mark.asyncio
async def test_author_new_only_requires_second_explicit_queue_action():
    services = Services()
    client = Client(ui.page('/_author_new'))
    try:
        with client:
            view = ManagedDownloadView(services)
            view.author_input.set_value('91')
            view._author_add_event(type('Evt', (), {'args': {'epoch': '0'}})())
            assert len(view.author_targets) == 1
            assert await view.fetch_author_differences()
            assert set(view.selected) == {'123'}
            assert not services.enqueued
            assert await view.submit()
            assert [r.work_id for r in services.enqueued] == ['123']
            assert isinstance(services.enqueued[0], ManagedAuthorIntent)
            assert services.enqueued[0].update_date == ''
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_invalid_author_cannot_be_selected():
    services = Services()
    client = Client(ui.page('/_author_bad'))
    try:
        with client:
            view = ManagedDownloadView(services)
            view.author_input.set_value('00091')
            view._author_add_event(type('Evt', (), {'args': {'epoch': '0'}})())
            assert not view.author_targets
            assert not view.selected
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_author_result_after_tab_switch_never_selects_or_publishes():
    services = Services()
    services.hold = asyncio.Event()
    client = Client(ui.page('/_author_stale'))
    try:
        with client:
            view = ManagedDownloadView(services)
            view.author_input.set_value('91')
            view._author_add_event(type('Evt', (), {'args': {'epoch': '0'}})())
            task = asyncio.create_task(view.fetch_author_differences())
            await asyncio.sleep(0)
            view.set_active(False)
            services.hold.set()
            assert not await task
            assert not view.selected and not services.published and not services.enqueued
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_follow_selection_origin_and_search_independence():
    services = Services()
    client = Client(ui.page('/_author_follow'))
    try:
        with client:
            view = ManagedDownloadView(services)
            assert await view.refresh_follow_authors()
            view._follow_pick('91', view.epoch, view.follow_render_generation)
            assert view.author_targets[0].origin == 'follow'
            view.follow_query = 'Followed two'
            view._render_follow_list()
            assert tuple(a.user_id for a in view.author_targets) == ('91',)
            view.author_input.set_value('91')
            view._author_add_event(type('Evt', (), {'args': {'epoch': '0'}})())
            assert len(view.author_targets) == 1
            assert view.author_targets[0].origin == 'follow'
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_old_author_fetch_event_cannot_use_new_target_selection():
    services = Services()
    client = Client(ui.page('/_author_old_fetch'))
    try:
        with client:
            view = ManagedDownloadView(services)
            stale = type('Evt', (), {'args': {'epoch': '0', 'authorRevision': '0'}})()
            view.author_input.set_value('91')
            view._author_add_event(type('Evt', (), {'args': {'epoch': '0'}})())
            assert view.author_revision == 1
            await view._author_fetch_event(stale)
            assert services.author_calls == 0 and not view.selected
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_author_updated_generation_enqueues_frozen_v2_intent():
    class Updating(Services):
        async def lookup_managed_author(self, uid, *, owner, publish=False):
            return (({'id': '123', 'type': 0, 'page_count': 1,
                      'update_date': '2026-10-24',
                      'expected_receipt_sha256': 'a' * 64,
                      'expected_relative_receipt':
                          'generations/work-123-' + 'b' * 32 + '/receipt.json'},
                     Difference.UPDATED),)

    services = Updating()
    client = Client(ui.page('/_author_v2_update'))
    try:
        with client:
            view = ManagedDownloadView(services)
            view.author_input.set_value('91')
            view._author_add_event(type('Evt', (), {'args': {'epoch': '0'}})())
            assert await view.fetch_author_differences()
            assert view.selected['123'] == record()
            assert await view.submit()
            assert len(services.enqueued) == 1
            intent = services.enqueued[0]
            assert isinstance(intent, ManagedAuthorIntent)
            assert intent.mode == 'updated' and intent.origin == 'id'
            assert intent.expected_receipt_sha256 == 'a' * 64
            assert intent.expected_relative_receipt.endswith('/receipt.json')
            assert intent.update_date == '2026-10-24'
    finally:
        client.delete()
