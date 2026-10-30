"""A retained previous snapshot must not pretend to accept the new query."""
import asyncio

from nicegui import Client, ui
import pytest

from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected
from pixivvault_nicegui.ui.ranking_view import RankingView
from test_ranking_ui import setup
from test_ranking_control_autoload import settled


@pytest.mark.asyncio
@pytest.mark.parametrize('status', (404, 500))
async def test_failed_condition_change_keeps_old_snapshot_but_disables_stale_append(status):
    services, reader, pins = setup()
    client = Client(ui.page('/_ranking_failed_condition_' + str(status)))
    try:
        with client:
            view = RankingView(services)
            view.set_active(True)
            await view.load()
            old_page, old_pin = view.page, view.pin
            old_card = view.gallery.cards['123']
            reader.failure = HTTPStatusRejected(status)
            view.mode.set_value('weekly')
            await settled(view)
            assert view.page is old_page and view.pin is old_pin
            assert view.gallery.cards['123'] is old_card and not old_pin.closed
            assert view.page.query.mode == 'daily' and view.mode.value == 'weekly'
            assert not view.busy and view.fetch_button.enabled
            assert not view.gallery.more_button.enabled, 'previous-query append cannot be offered'
            assert not view.sort.enabled, 'old snapshot cannot be resorted under the new condition'
            assert '前の一覧' in view.notice.text, 'failure must explain that retained cards are not the selected query'
            old_state = view.gallery.state
            view.sort.set_value('view_count')
            assert view.gallery.state is old_state and view.gallery.cards['123'] is old_card
            before = list(reader.calls)
            await view.load(append=True)
            assert reader.calls == before
            reader.failure = None
            await view.load()
            assert view.page.query.mode == 'weekly' and view.gallery.more_button.enabled
            assert view.sort.enabled
            assert old_pin.closed
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_unsubmitted_tag_keeps_cards_without_offering_append_for_old_query():
    services, reader, pins = setup()
    client = Client(ui.page('/_ranking_pending_tag_append'))
    try:
        with client:
            view = RankingView(services)
            view.set_active(True)
            await view.load()
            old_page = view.page
            before = list(reader.calls)
            view.tag.set_value('not submitted')
            await asyncio.sleep(.15)
            assert reader.calls == before and view.page is old_page
            assert not view.gallery.more_button.enabled
            assert not view.sort.enabled
            assert '前の条件' in view.notice.text
            view.tag.set_value('')
            assert view.gallery.more_button.enabled
            assert view.sort.enabled
            assert reader.calls == before
    finally:
        client.delete()
        services.close()
