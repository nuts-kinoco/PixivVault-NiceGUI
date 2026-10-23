"""Explicit legacy repair and inventory controls on two isolated UI clients."""

import asyncio
from types import SimpleNamespace

from nicegui import Client, ui
import pytest

from pixivvault_nicegui.author_download import AuthorTarget, Difference, AuthorPreview
from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
from pixivvault_nicegui.managed_download_requests import ManagedAuthorIntent
from pixivvault_nicegui.ui.managed_download_view import ManagedDownloadView


def record(wid):
    return RemoteBookmark(wid, 'illustration', 'show', 'Synthetic', '91',
                          'Synthetic author', (), '', '', 1, 0, 0)


class Services:
    def __init__(self):
        self.managed_downloads = object()
        self.jobs = object()
        self.legacy_author_bridge = object()
        self.enqueued = []
        self.observations = []
        self.hold = None
        self.scan_hold = None
        self.replay_failure = False

    def download_candidates(self):
        return ()

    def bookmark_download_candidates(self):
        return ()

    async def lookup_managed_author(self, uid, *, owner, publish=False):
        assert uid == '91' and not publish
        if self.hold is not None:
            await self.hold.wait()
        return AuthorPreview(((dict(id='101', type=0, page_count=1,
                                     update_date='2026', title='Recovered',
                                     legacy_diff_source=True, legacy_failed=True),
                               Difference.RESTORED),
                              (dict(id='102', type=0, page_count=1,
                                    update_date='2026', title='Missing',
                                    legacy_diff_source=True), Difference.MISSING),
                              (dict(id='103', type=0, page_count=1,
                                    update_date='2026', title='Unknown'),
                               Difference.UNKNOWN),
                              (dict(id='104', type=0, page_count=1,
                                    update_date='2026', title='Updated',
                                    legacy_diff_source=True), Difference.UPDATED)),
                             remote_missing_pending=1, remote_missing_confirmed=2,
                             remote_absent=(SimpleNamespace(
                                 work_id='501', title='Absent synthetic',
                                 status='pending', streak=2),
                                 SimpleNamespace(work_id='502', title='Gone synthetic',
                                                 status='confirmed', streak=3)))

    async def lookup_managed_work_id(self, wid, *, owner, publish=False):
        return record(wid)

    def remember_managed_author_batch(self, owner, records):
        return tuple(records)

    def release_managed_owner(self, owner):
        pass

    def enqueue_managed_download(self, intent, operation_id, *, owner):
        self.enqueued.append((owner, intent))

    async def confirm_legacy_author_inventory(self, uid, *, owner, scan_id):
        self.observations.append((uid, owner, scan_id))
        if self.scan_hold is not None:
            await self.scan_hold.wait()
        if self.replay_failure:
            raise ValueError('scan ID replay differs from recorded complete listing')
        return SimpleNamespace(remote_missing_pending=1, remote_missing_confirmed=2)


@pytest.mark.asyncio
async def test_restored_missing_updated_from_old_source_require_explicit_submit():
    services = Services()
    client = Client(ui.page('/_legacy_author_rehydrate'))
    try:
        with client:
            view = ManagedDownloadView(services)
            view.author_targets = (AuthorTarget('91', 'Synthetic author', 'id'),)
            view.author_revision = 1
            assert await view.fetch_author_differences()
            assert set(view.selected) == {'101', '102', '104'}
            assert not services.enqueued
            assert all(view._available_author_intents[wid].mode == 'new'
                       for wid in ('101', '102', '104'))
            assert '復帰 1' in view.author_status_label.text
            assert '実物欠損 1' in view.author_status_label.text
            assert '判定保留・対象外 1' in view.author_status_label.text
            assert view.author_diff_revision == view.author_revision
            assert len(view.author_remote_absent) == 2
            assert view.author_remote_absent_list.default_slot.children
            assert await view.submit()
            assert {intent.work_id for _, intent in services.enqueued} == {'101', '102', '104'}
            assert all(isinstance(intent, ManagedAuthorIntent) for _, intent in services.enqueued)
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_inventory_operation_is_explicit_scoped_to_one_author_and_owner():
    services = Services()
    first = Client(ui.page('/_legacy_inventory_first'))
    second = Client(ui.page('/_legacy_inventory_second'))
    try:
        with first:
            a = ManagedDownloadView(services)
            a.author_targets = (AuthorTarget('91', 'Synthetic author', 'id'),)
            a.author_revision = 1
        with second:
            b = ManagedDownloadView(services)
            b.author_targets = (AuthorTarget('91', 'Synthetic author', 'id'),)
            b.author_revision = 1
        assert await a.confirm_legacy_author_inventory()
        assert services.observations[0][1] == a._owner
        assert services.observations[0][2] and len(services.observations[0][2]) == 32
        assert not services.enqueued and not b.selected
        assert not await b.confirm_legacy_author_inventory(epoch='stale')
        assert len(services.observations) == 1
        assert '削除確定 2' in a.author_status_label.text
    finally:
        first.delete()
        second.delete()


@pytest.mark.asyncio
async def test_stale_author_preview_keeps_other_client_selection_isolated():
    services = Services()
    services.hold = asyncio.Event()
    first = Client(ui.page('/_legacy_diff_stale_one'))
    second = Client(ui.page('/_legacy_diff_stale_two'))
    try:
        with first:
            a = ManagedDownloadView(services)
            a.author_targets = (AuthorTarget('91', 'Synthetic author', 'id'),)
            a.author_revision = 1
        with second:
            b = ManagedDownloadView(services)
            b.author_targets = (AuthorTarget('91', 'Synthetic author', 'id'),)
            b.author_revision = 1
        pending = asyncio.create_task(a.fetch_author_differences())
        await asyncio.sleep(0)
        a.set_active(False)
        services.hold.set()
        assert not await pending
        assert not a.selected and not b.selected and not services.enqueued
        assert await b.fetch_author_differences()
        assert set(b.selected) == {'101', '102', '104'}
    finally:
        first.delete()
        second.delete()


@pytest.mark.asyncio
async def test_completed_scan_from_inactive_tab_rotates_receipt_for_next_inventory():
    services = Services()
    services.scan_hold = asyncio.Event()
    client = Client(ui.page('/_legacy_scan_tab_roundtrip'))
    try:
        with client:
            view = ManagedDownloadView(services)
            view.author_targets = (AuthorTarget('91', 'Synthetic author', 'id'),)
            view.author_revision = 1
            pending = asyncio.create_task(view.confirm_legacy_author_inventory())
            await asyncio.sleep(0)
            view.set_active(False)
            services.scan_hold.set()
            assert not await pending  # A stale UI must not render success.
            assert len(services.observations) == 1
            previous = services.observations[0][2]
            view.set_active(True)
            assert await view.confirm_legacy_author_inventory()
            assert services.observations[1][2] != previous
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_remote_changed_during_replay_rotates_scan_id_after_safe_rejection():
    services = Services()
    services.replay_failure = True
    client = Client(ui.page('/_legacy_scan_changed_remote'))
    try:
        with client:
            view = ManagedDownloadView(services)
            view.author_targets = (AuthorTarget('91', 'Synthetic author', 'id'),)
            view.author_revision = 1
            assert not await view.confirm_legacy_author_inventory()
            rejected = services.observations[0][2]
            services.replay_failure = False
            assert await view.confirm_legacy_author_inventory()
            assert services.observations[1][2] != rejected
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_author_inventory_over_128_can_submit_two_complete_batches():
    class LargeInventory(Services):
        def __init__(self):
            super().__init__()
            self.pins = {}

        async def lookup_managed_author(self, uid, *, owner, publish=False):
            assert uid == '91' and not publish
            return AuthorPreview(tuple(
                ({'id': str(wid), 'type': 0, 'page_count': 1,
                  'title': 'Synthetic', 'update_date': '2026-10-24'},
                 Difference.NEW)
                for wid in range(1001, 1141)))

        def pending_managed_author_work_ids(self):
            return frozenset(intent.work_id for _, intent in self.enqueued)

        def remember_managed_author_batch(self, owner, records):
            candidates = self.pins.setdefault(owner, {})
            for record in records:
                candidates[record.work_id] = record
            assert len(candidates) <= 128
            return tuple(records)

        def forget_accepted_managed_author_batch(self, owner, work_ids):
            for work_id in work_ids:
                self.pins[owner].pop(work_id, None)

        def enqueue_managed_download(self, intent, operation_id, *, owner):
            assert intent.work_id in self.pins[owner]
            super().enqueue_managed_download(intent, operation_id, owner=owner)

    services = LargeInventory()
    client = Client(ui.page('/_legacy_author_140_batches'))
    try:
        with client:
            view = ManagedDownloadView(services)
            view.author_targets = (AuthorTarget('91', 'Synthetic author', 'id'),)
            view.author_revision = 1
            assert await view.fetch_author_differences()
            assert len(view.selected) == 128
            assert not services.enqueued
            assert await view.submit()
            assert len(services.enqueued) == 128
            assert not services.pins[view._owner]
            assert not view.selected
            assert await view.fetch_author_differences()
            assert len(view.selected) == 12
            assert await view.submit()
            assert len(services.enqueued) == 140
            assert {intent.work_id for _, intent in services.enqueued} == {
                str(i) for i in range(1001, 1141)}
            assert not services.pins[view._owner]
    finally:
        client.delete()
