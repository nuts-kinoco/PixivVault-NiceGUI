import asyncio
from threading import Event
from types import SimpleNamespace

import pytest
from nicegui import Client, ui

from pixivvault_nicegui.managed_scheduler import ManagedScheduler
from pixivvault_nicegui.ui.scheduler_view import SchedulerView


@pytest.mark.asyncio
async def test_edit_during_save_survives_and_can_be_saved(tmp_path):
    ledger = ManagedScheduler(tmp_path)
    entered, release = Event(), Event()
    original = ledger.configure_all

    def delayed(**kwargs):
        entered.set()
        assert release.wait(5)
        return original(**kwargs)

    ledger.configure_all = delayed
    services = SimpleNamespace(managed_scheduler=ledger,
        managed_schedule_runner=SimpleNamespace(settings_changed=lambda: None))
    client = Client(ui.page('/scheduler-save-race'))
    view = None
    try:
        with client:
            view = SchedulerView(services)
        view._load(ledger)
        view.authors.set_value('11')
        view._mark_settings_dirty()
        task = asyncio.create_task(view.save())
        assert await asyncio.to_thread(entered.wait, 5)
        view.authors.set_value('11,12')
        view._mark_settings_dirty()
        release.set()
        await task
        assert ledger.favourites() == ('11',)
        assert view.authors.value == '11,12'
        assert view._settings_dirty
        ledger.configure_all = original
        await view.save()
        assert ledger.favourites() == ('11', '12')
        assert not view._settings_dirty
    finally:
        release.set()
        if view is not None:
            view.close()
        client.delete()
        ledger.close()
