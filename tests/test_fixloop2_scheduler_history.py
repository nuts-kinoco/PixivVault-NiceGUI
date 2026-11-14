"""Synthetic scheduler reopen should render durable completed history."""
from types import SimpleNamespace
import pytest
from nicegui import Client,ui
from pixivvault_nicegui.ui.scheduler_view import SchedulerView

@pytest.mark.asyncio
async def test_open_renders_existing_history_without_waiting_for_timer():
    class Ledger:
        def snapshot(self):
            return SimpleNamespace(revision=1,interval_hours=24,startup_check=False,
                last_status='never',next_due_utc=None)
        def favourites(self):return ()
        def claim_pending_notifications(self):return ()
        def history(self):
            return [{'status':'saved','accepted':2,'deferred':0,'finished_utc':'generated'}]
    ledger=Ledger()
    services=SimpleNamespace(managed_scheduler=ledger,enable_managed_scheduler=lambda:ledger,
                             reconcile_managed_scheduler=lambda:None)
    client=Client(ui.page('/generated-fixloop2-history'))
    view=None
    try:
        with client:view=SchedulerView(services)
        await view.open()
        assert '最終実行: 完了' in view.history_label.text
        assert '受付 2件' in view.history_label.text
    finally:
        if view:view.close()
        client.delete()
