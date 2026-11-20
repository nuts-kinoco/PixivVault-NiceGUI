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
            assert view.fetch_button._props.get('icon') == 'refresh'
            view.mode.set_value('weekly')
            assert view.mode.value == 'weekly'
            view.close()
    finally:
        client.delete(); services.close()


def test_empty_queue_has_original_message():
    view, _ = _queue(False)
    try:
        assert '現在のジョブはありません' in _texts(view)
    finally:
        view.close()


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
