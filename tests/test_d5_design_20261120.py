from nicegui import Client, ui
import pytest

from pixivvault_nicegui.bookmark_state import BookmarkState
from pixivvault_nicegui.models import Artwork
from pixivvault_nicegui.ui.bookmark_view import BookmarkView
from pixivvault_nicegui.ui.ranking_view import RankingView
from test_ranking_ui import setup
from test_h05_small_parity_20261117 import _queue, _texts


@pytest.mark.asyncio
async def test_failed_refresh_explains_cached_cards():
    client = Client(ui.page('/_d5_bookmark'))
    state = BookmarkState(cached_remote=[Artwork('1', 'title', 'author')])
    try:
        with client:
            view = BookmarkView(state); view.render()
            epoch = state.begin_fetch(); state.complete_fetch(epoch, (), ok=False)
            view.refresh()
            assert view.cards
            assert '保存済み' in view.message.text
            assert '失敗' in view.message.text
            assert view.retry._props.get('icon') == 'refresh'
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_ranking_period_and_filters_are_toggle_controls():
    services, _, _ = setup(); client = Client(ui.page('/_d5_ranking'))
    try:
        with client:
            view = RankingView(services)
            assert isinstance(view.mode, ui.toggle)
            assert isinstance(view.r18, ui.switch)
            assert isinstance(view.ai, ui.switch)
            assert 'pv-filter-switch' in view.r18._classes
            assert 'pv-filter-switch' in view.ai._classes
            assert view.fetch_button._props.get('icon') == 'refresh'
            view.mode.set_value('weekly')
            assert view.mode.value == 'weekly'
            view.close()
    finally:
        client.delete(); services.close()


def test_empty_queue_has_original_message():
    from nicegui import core
    script_mode_before = core.script_mode
    client = Client(ui.page('/_d5_empty_queue'))
    try:
        with client:
            view, _ = _queue(False)
            try:
                assert '現在のジョブはありません' in _texts(view)
            finally:
                view.close()
    finally:
        client.delete()
    assert core.script_mode == script_mode_before


@pytest.mark.asyncio
async def test_retry_action_uses_pill_with_refresh_icon():
    from pixivvault_nicegui.ui.similar_view import SimilarView
    client = Client(ui.page('/_d5_retry'))
    try:
        with client:
            view = SimilarView(None, lambda *_: None)
            assert view.retry._props.get('icon') == 'refresh'
            assert 'pv-retry-action' in view.retry._classes
            view.close()
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_duplicate_size_controls_match_original_icons_and_order():
    from pixivvault_nicegui.ui.duplicate_view import DuplicateView
    from test_duplicate_view import StubSource
    client = Client(ui.page('/_d5_duplicate_sizes'))
    try:
        with client:
            view = DuplicateView(StubSource())
            assert list(view.size_buttons) == ['small', 'medium', 'large']
            assert [view.size_buttons[size]._props.get('icon') for size in view.size_buttons] == [
                'grid_3x3', 'grid_view', 'crop_square']
            view.close()
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_settings_common_actions_have_pale_icon_buttons(tmp_path):
    from pixivvault_nicegui.preferences import PreferencesStore
    from pixivvault_nicegui.ui.preferences_view import PreferencesView
    client = Client(ui.page('/_d5_settings_actions'))
    try:
        with client:
            view = PreferencesView(PreferencesStore(tmp_path / 'prefs.sqlite'))
            view.open()
            controls = {e.props.get('data-testid'): e for e in view.dialog.descendants()
                        if isinstance(e, ui.button)}
            for key, icon in [('preferences-clear-cache', 'delete_outline'),
                              ('preferences-export-metadata', 'archive'),
                              ('preferences-export-db', 'archive')]:
                assert controls[key].props.get('flat') is True
                assert controls[key].props.get('icon') == icon
            view.close()
    finally:
        client.delete()
