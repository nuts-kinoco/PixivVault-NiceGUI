"""Local legacy-source failure must be actionable without exposing exception text."""
from threading import Event,Lock
from types import SimpleNamespace
import pytest
from nicegui import Client,ui
import pixivvault_nicegui.legacy_author_sync as legacy
import pixivvault_nicegui.managed_scheduler as scheduler
from pixivvault_nicegui.ui.scheduler_view import SchedulerView

@pytest.mark.asyncio
async def test_legacy_source_failure_gives_local_guidance(monkeypatch):
    error_type=getattr(legacy,'LegacyAuthorSourceError',ValueError)
    def fail(*args,**kwargs):raise error_type('synthetic-private-path')
    monkeypatch.setattr(scheduler,'submit_author_batch',fail)
    ledger=SimpleNamespace(favourites=lambda:('11',))
    service=SimpleNamespace(closing=False,managed_scheduler=ledger,
        _scheduler_dispatch_lock=Lock(),_scheduler_cancel_event=Event())
    client=Client(ui.page('/generated-legacy-scheduler-error'))
    view=None
    try:
        with client:view=SchedulerView(service)
        await view.run_once()
        assert '旧' in view.action_notice.text
        assert '確認' in view.action_notice.text
        assert 'synthetic-private-path' not in view.action_notice.text
    finally:
        if view:view.close()
        client.delete()
