"""R3 independent findings on isolated owners, synthetic artwork and fake search."""
import asyncio
from datetime import datetime, timezone
from threading import Event
from types import SimpleNamespace

import pytest
from nicegui import Client, core, ui

from pixivvault_nicegui.external_vault import VaultError
from pixivvault_nicegui.library_models import LibrarySnapshot, LibraryWork
from pixivvault_nicegui.media import DemoImageCatalog
from pixivvault_nicegui.models import Artwork
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.library_view import LibraryView
from pixivvault_nicegui.ui.personal_ranking_view import PersonalRankingView
from pixivvault_nicegui.ui.scheduler_view import SchedulerView
from test_ac_library_search_20261119 import FakeVault, until
from test_managed_download_services import FakeSource
from test_personal_ranking import raw
from test_w_account_separation_20261119 import A, B, X, Y, resources, switch
from test_y_ranking_personal_20261118 import build, settle


@pytest.mark.asyncio
async def test_scheduler_clears_other_owner_stars_and_history_even_when_dirty(tmp_path):
    with resources(tmp_path) as (_, store, ledger, reader, services):
        store.sync(reader)
        store.set_favorite(A, X, True)
        services.scheduler_favourite_authors(ledger=ledger)
        now = datetime.now(timezone.utc)
        ledger.begin_dispatch('generated', now, 1, account_id=A)
        ledger.finish_dispatch('generated', status='queued', accepted=7, deferred=3, now=now, account_id=A)
        services.reconcile_managed_scheduler = lambda: None
        client = Client(ui.page('/_r3_account_projection'))
        try:
            with client:
                view = SchedulerView(services)
                await view.refresh()
                assert view.authors.visible and view.authors.value == X
                assert view.history_label.visible and '受付 7件' in view.history_label.text
                view.interval.set_value('6')
                switch(store, reader)
                store.set_favorite(B, Y, True)
                await view.refresh()
                assert not view.authors.visible and not view.authors.value
                assert not view.history_label.visible and '7' not in view.history_label.text
                assert '最終チェック' not in view.status.text
                view.discard_changes()
                await view.refresh()
                assert view.authors.visible and view.authors.value == Y
                assert not view.history_label.visible and '保留 3件' not in view.history_label.text
                reopened = SchedulerView(services)
                await reopened.refresh()
                assert reopened.authors.visible and reopened.authors.value == Y
                assert not reopened.history_label.visible
                assert '受付 7件' not in reopened.history_label.text
                assert ledger.history()[0]['accepted'] == 7
                view.close()
                reopened.close()
        finally:
            client.delete()


@pytest.mark.asyncio
async def test_personal_last_tag_alone_reschedules_inflight_search(monkeypatch):
    services, vault, client = await build(monkeypatch, 'r3_last_tag_only')
    entered, release = Event(), Event()

    def search(word, bookmark_min=0, **kwargs):
        vault.calls.append((word, bookmark_min))
        if word == 'old' and bookmark_min == 1000:
            entered.set()
            assert release.wait(5)
        return {'illusts': [raw(123 if word == 'old' else 124)]}

    monkeypatch.setattr(vault, 'search', search)
    view = None
    try:
        with client:
            view = PersonalRankingView(services)
            view.set_active(True)
            await view._context_task
            view.tags.set_value('old')
            await view.load()
            view.bookmarks.set_value(1000)
            await until(entered.is_set)
            view.tags.set_value('new')
            release.set()
            await settle(view)
            assert vault.calls == [('old', 0), ('old', 1000), ('new', 1000)]
            assert set(view.gallery.cards) == {'124'}
    finally:
        release.set()
        if view is not None:
            view.close()
        client.delete()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('uncertain', (True, False), ids=('uncertain-outcome', 'unavailable-after-response'))
async def test_personal_reports_route_loss_with_previous_cards(monkeypatch, uncertain):
    services, vault, client = await build(monkeypatch, 'r3_route_lost_' + str(uncertain))
    view = None
    try:
        with client:
            view = PersonalRankingView(services)
            view.set_active(True)
            await view._context_task
            view.tags.set_value('tag')
            await view.load()
            page, pin = view.page, view.pin

            def lose_route(*_args, **_kwargs):
                vault.open = False
                if uncertain:
                    raise VaultError('pixiv-outcome-uncertain')
                return {'illusts': [raw(125)]}

            monkeypatch.setattr(vault, 'search', lose_route)
            await view.load()
            assert view.page is page and view.pin is pin
            assert set(view.gallery.cards) == {'123', '124'}
            reason = '通信結果を確認できない' if uncertain else '検索中に借用アカウント検索を利用できなくなりました'
            assert reason in view.notice.text and '直前の検索結果' in view.notice.text
            assert view.gallery.counter.text == '前の検索結果'
            assert not view.fetch_button.enabled and not view.gallery.more_cell.visible
            await view._refresh_context()
            assert reason in view.notice.text and view.gallery.counter.text == '前の検索結果'
    finally:
        if view is not None:
            view.close()
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_deferred_reload_restores_focus_to_rebuilt_original_page_card(monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    vault = FakeVault()
    services = ApplicationServices(live_client=FakeSource())
    services.external_vault = vault
    work = Artwork('203', 'alpha local', 'author', author_id='23')
    services.library = LibrarySnapshot((LibraryWork(work, '23'),))
    services.images.demo.close()
    services.images.demo = DemoImageCatalog((work,))
    client = Client(ui.page('/_r3_deferred_focus'))
    view = None
    try:
        with client:
            view = LibraryView(services)
            view.set_search_scope('pixiv')
            vault.block = True
            view.search('alpha')
            await until(vault.entered.is_set)
            view.open_author('23')
            key = ('203', 0)
            opener = view.page_cards[key]
            view.open_page(*key)
            view._image_loaded(SimpleNamespace(args=view._image_url))
            assert view.viewer.is_open
            vault.release.set()
            await until(lambda: view.pixiv_query == 'alpha')
            closed, focused = [], []
            close = view.viewer.close

            def close_recorded(*, restore_focus=True):
                closed.append(restore_focus)
                close(restore_focus=restore_focus)

            monkeypatch.setattr(view.viewer, 'close', close_recorded)
            monkeypatch.setattr(view.viewer, 'focus_after_close', focused.append, raising=False)
            view._on_key(SimpleNamespace(args='escape'))
            assert len(closed) == 1
            assert view.page_cards[key] is not opener
            assert focused == [view.page_cards[key]]
    finally:
        vault.release.set()
        if view is not None:
            view.close()
        client.delete()
        services.external_vault = None
        services.close()


@pytest.mark.asyncio
async def test_scheduler_rejects_stale_form_save_before_following_resync(tmp_path):
    with resources(tmp_path) as (_, store, ledger, reader, services):
        store.sync(reader)
        store.set_favorite(A, X, True)
        services.scheduler_favourite_authors(ledger=ledger)
        before = ledger.snapshot()
        client = Client(ui.page('/_r3_stale_schedule_save'))
        try:
            with client:
                view = SchedulerView(services)
                view._load(ledger)
                view.interval.set_value('6')
                view.authors.set_value(Y)
                # The login changes before the follow-cache and screen poll.
                reader._verified_identity = B
                await view.save()
                assert ledger.snapshot() == before
                assert ledger.favourites(A) == (X,)
                assert not view.authors.visible and not view.authors.value
                view.close()
        finally:
            client.delete()


@pytest.mark.asyncio
@pytest.mark.parametrize('timeout', (False, True), ids=('admission', 'timeout'))
async def test_scheduler_late_manual_result_scrubs_stale_display(tmp_path, timeout):
    from threading import RLock

    with resources(tmp_path) as (_, store, ledger, reader, services):
        store.sync(reader)
        store.set_favorite(A, X, True)
        services.scheduler_favourite_authors(ledger=ledger)
        services.reconcile_managed_scheduler = lambda: None
        services._scheduler_dispatch_lock = RLock()
        services._scheduler_cancel_event = Event()
        entered, release = Event(), Event()

        def dispatch(*_args, **_kwargs):
            entered.set()
            assert release.wait(5)
            if timeout:
                raise TimeoutError()
            return {'accepted': 7, 'deferred': 3}

        services.dispatch_author_check = dispatch
        client = Client(ui.page('/_r3_late_manual_result'))
        task = None
        try:
            with client:
                view = SchedulerView(services)
                await view.refresh()
                assert view.authors.visible and view.authors.value == X
                task = asyncio.create_task(view.run_once())
                await until(entered.is_set)
                reader._verified_identity = B
                release.set()
                await task
                assert not view.authors.visible and not view.authors.value
                assert not view.history_label.visible
                assert '受付 7件' not in view.action_notice.text
                view.close()
        finally:
            release.set()
            if task is not None:
                await task
            client.delete()


@pytest.mark.asyncio
async def test_scheduler_restores_dirty_draft_after_same_account_revalidates(tmp_path):
    with resources(tmp_path) as (_, store, ledger, reader, services):
        store.sync(reader)
        store.set_favorite(A, X, True)
        services.scheduler_favourite_authors(ledger=ledger)
        services.reconcile_managed_scheduler = lambda: None
        client = Client(ui.page('/_r3_schedule_renewal'))
        try:
            with client:
                view = SchedulerView(services)
                await view.refresh()
                view.authors.set_value(Y)
                revision = view.revision
                reader._verified_identity = None
                await view.refresh()
                assert not view.authors.visible and not view.authors.value
                reader._verified_identity = A
                await view.refresh()
                assert view.authors.visible and view.authors.value == Y
                assert view._settings_dirty and view.revision == revision
                assert ledger.favourites(A) == (X,)
                view.close()
        finally:
            client.delete()


@pytest.mark.asyncio
async def test_scheduler_clean_adoption_clears_old_owner_notice(tmp_path):
    with resources(tmp_path) as (_, store, ledger, reader, services):
        store.sync(reader)
        store.set_favorite(A, X, True)
        services.scheduler_favourite_authors(ledger=ledger)
        client = Client(ui.page('/_r3_clean_owner_adoption'))
        try:
            with client:
                view = SchedulerView(services)
                view._load(ledger)
                view.authors.set_value(f'{X},999')
                await view.save()
                assert '999' in view.action_notice.text and not view._settings_dirty
                switch(store, reader)
                store.set_favorite(B, Y, True)
                view.discard_changes()
                assert view.authors.visible and view.authors.value == Y
                assert not view.history_label.visible
                assert '999' not in view.action_notice.text
                view.close()
        finally:
            client.delete()


@pytest.mark.asyncio
async def test_personal_last_tag_waits_for_current_context(monkeypatch):
    services, vault, client = await build(monkeypatch, 'r3_context_superseded')
    entered, release = Event(), Event()
    original = services.personal_ranking_context
    reads = 0

    def read_context():
        nonlocal reads
        reads += 1
        if reads == 1:
            entered.set()
            assert release.wait(5)
        return original()

    services.personal_ranking_context = read_context
    view = None
    try:
        with client:
            view = PersonalRankingView(services)
            view.set_active(True)
            await until(entered.is_set)
            view.tags.set_value('last')
            await until(lambda: view.busy)
            release.set()
            await settle(view)
            assert [call['word'] for call in vault.calls] == ['last']
            assert view.page is not None and view.page.key[0] == ('last',)
    finally:
        release.set()
        if view is not None:
            view.close()
        client.delete()
        services.close()
