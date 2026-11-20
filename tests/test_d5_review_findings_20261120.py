import asyncio
import pytest
from nicegui import Client, ui
from pixivvault_nicegui.bookmark_state import BookmarkState
from pixivvault_nicegui.models import Artwork
from pixivvault_nicegui.ui.bookmark_view import BookmarkView


@pytest.mark.asyncio
@pytest.mark.parametrize('search_first', [True, False])
async def test_failed_fetch_with_no_matches_does_not_claim_cards_are_shown(search_first):
    client = Client(ui.page('/_d5_review_search_' + str(search_first)))
    state = BookmarkState(cached_remote=[Artwork('1', 'title', 'author')])
    try:
        with client:
            view = BookmarkView(state); view.render()
            if search_first: view.search('unmatched')
            epoch = state.begin_fetch(); state.complete_fetch(epoch, (), ok=False)
            view.refresh()
            if not search_first: view.search('unmatched')
            assert not view.cards
            assert '取得に失敗' in view.message.text
            assert '条件に合う' in view.message.text
            assert '表示しています' not in view.message.text
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_real_refresh_failure_after_cache_write_failure(tmp_path, monkeypatch):
    from nicegui import core
    from pixivvault_nicegui.services import ApplicationServices
    from pixivvault_nicegui.ui.library_view import LibraryView
    from test_managed_download_services import FakeSource
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    source = FakeSource()
    services = ApplicationServices(live_client=source, preferences_root=tmp_path / 'prefs')
    client = Client(ui.page('/_d5_review_persistence'))
    view = None
    try:
        original_write = services._bookmark_cache_store.write
        monkeypatch.setattr(services._bookmark_cache_store, 'write', lambda *a, **k: False)
        await services.refresh_bookmark_cache()
        assert services.bookmark_cache_persist_failed
        with client:
            view = LibraryView(services)
            view.subview = 'bookmarks'
            view.bookmarks.set_active(True)
            source.fail = True
            epoch = view.bookmark_state.begin_fetch()
            await view._refresh_bookmarks_async(epoch)
            assert view.bookmarks.cards
            assert '取得に失敗' in view.bookmarks.message.text
            assert '保存済み' not in view.bookmarks.message.text
            assert '保存を確認できません' in view.bookmarks.persistence_status.text
            monkeypatch.setattr(services._bookmark_cache_store, 'write', original_write)
            source.fail = False
            epoch = view.bookmark_state.begin_fetch()
            await view._refresh_bookmarks_async(epoch)
            assert not services.bookmark_cache_persist_failed
            source.fail = True
            epoch = view.bookmark_state.begin_fetch()
            await view._refresh_bookmarks_async(epoch)
            assert '保存済み' in view.bookmarks.message.text
            assert not view.bookmarks.persistence_status.text
    finally:
        if view: view.close()
        client.delete(); services.close()


@pytest.mark.asyncio
async def test_unconfigured_cache_is_not_called_saved(monkeypatch):
    from nicegui import core
    from pixivvault_nicegui.services import ApplicationServices
    from pixivvault_nicegui.ui.library_view import LibraryView
    from test_managed_download_services import FakeSource
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    source = FakeSource()
    services = ApplicationServices(live_client=source)
    client = Client(ui.page('/_d5_review_no_cache'))
    view = None
    try:
        await services.refresh_bookmark_cache()
        assert services._bookmark_cache_store is None
        with client:
            view = LibraryView(services); view.subview = 'bookmarks'
            view.bookmarks.set_active(True); source.fail = True
            epoch = view.bookmark_state.begin_fetch()
            await view._refresh_bookmarks_async(epoch)
            assert view.bookmarks.cards and '取得に失敗' in view.bookmarks.message.text
            assert '保存済み' not in view.bookmarks.message.text
    finally:
        if view: view.close()
        client.delete(); services.close()


def test_heading_contract():
    from pixivvault_nicegui.ui.theme import CSS
    from tools.ui_smoke_helpers import load_expectations
    specs = load_expectations()['screens']
    assert 'Yu Mincho' in CSS[CSS.index('.pv-serif-heading'):CSS.index('.pv-serif-heading') + 150]
    assert specs['queue']['text_fonts']['[data-testid=queue-heading] .pv-library-heading'] == 'Yu Mincho'
    assert specs['dedup']['text_fonts']['[data-testid=duplicate-view] .pv-library-heading'] == 'Yu Mincho'
    assert specs['settings']['text_fonts']['[data-testid=preferences-export-heading]'] == 'Yu Mincho'


def test_duplicate_size_accessible_names():
    from pixivvault_nicegui.ui.card_size_control import size_buttons
    client = Client(ui.page('/_d5_review_size'))
    try:
        with client:
            buttons = size_buttons(lambda _: None, prefix='duplicate', small_first=True)
            assert [buttons[k].props['aria-label'] for k in ('small','medium','large')] == [
                'サムネイル小', 'サムネイル中', 'サムネイル大']
    finally:
        client.delete()


def test_mechanics_distinguishes_heading_and_body_fonts():
    from tools.p5_design_evidence import mechanics, LIGHT_TOKENS
    port = {'tokens': LIGHT_TOKENS, 'width': [1280, 1280],
            'display_contract': {}, 'clipped': [], 'shell': {'tabs': []}, 'styles': [],
            'text': [], 'rendered_fonts': {'.pv-dialog-title': [{'familyName':'Yu Mincho'}],
                                           '[data-testid=failed-reason]': [{'familyName':'Noto Sans JP'}]}}
    flags = mechanics({'port': port}, 'light', 'settings')
    assert not any('rendered serif' in flag or 'font' in flag for flag in flags)
    port['rendered_fonts']['[data-testid=failed-reason]'] = [{'familyName':'Yu Mincho'}]
    assert any('font' in flag for flag in mechanics({'port': port}, 'light', 'settings'))
