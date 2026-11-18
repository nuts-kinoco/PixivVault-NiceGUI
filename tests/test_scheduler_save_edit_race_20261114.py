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
    original = ledger.configure

    def delayed(**kwargs):
        entered.set()
        assert release.wait(5)
        return original(**kwargs)

    ledger.configure = delayed
    services = SimpleNamespace(managed_scheduler=ledger,
        managed_schedule_runner=SimpleNamespace(settings_changed=lambda: None))
    client = Client(ui.page('/scheduler-save-race'))
    view = None
    try:
        with client:
            view = SchedulerView(services)
        view._load(ledger)
        view.interval.set_value('12')
        view._mark_settings_dirty()
        task = asyncio.create_task(view.save())
        assert await asyncio.to_thread(entered.wait, 5)
        view.interval.set_value('6')
        view._mark_settings_dirty()
        release.set()
        await task
        assert ledger.snapshot().interval_hours == 12
        assert view.interval.value == '6'
        assert view._settings_dirty
        ledger.configure = original
        await view.save()
        assert ledger.snapshot().interval_hours == 6
        assert not view._settings_dirty
    finally:
        release.set()
        if view is not None:
            view.close()
        client.delete()
        ledger.close()
