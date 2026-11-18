from types import SimpleNamespace

import pytest
from nicegui import Client, ui

from pixivvault_nicegui.favourites import FavouriteBook
from pixivvault_nicegui.ui.scheduler_view import SchedulerView
from test_v_dl_priority_20261117 import world, sync  # noqa: F401


@pytest.mark.asyncio
async def test_unreadable_stars_input_is_not_silently_discarded(world):
    _, following, ledger = world
    sync(following, 5)
    book = FavouriteBook(following, scheduler=ledger)
    ledger.attach_favourites(book)
    book.set('1001', True)
    ledger.attach_favourites(FavouriteBook(None, scheduler=ledger))
    services = SimpleNamespace(managed_scheduler=ledger,
                               managed_schedule_runner=SimpleNamespace(settings_changed=lambda: None))
    client = Client(ui.page('/w-unknown-stars'))
    try:
        with client:
            view = SchedulerView(services)
        view._load(ledger)
        view.authors.set_value('1003')
        view._mark_settings_dirty()
        await view.save()
        assert '☆' in view.action_notice.text and '保存' in view.action_notice.text
        ledger.attach_favourites(FavouriteBook(following, scheduler=ledger))
        assert ledger.favourites() == ('1001',)
    finally:
        client.delete()
