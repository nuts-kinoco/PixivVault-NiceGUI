import asyncio
import pytest
from nicegui import Client, ui, core
from test_external_vault_ui import setup, drain_personal_view
from pixivvault_nicegui.external_vault import VaultError
from pixivvault_nicegui.ui.external_vault_view import ExternalVaultView
from pixivvault_nicegui.ui.personal_ranking_view import PersonalRankingView

def test_busy_observer_does_not_block_event_loop():
    view=object.__new__(ExternalVaultView)
    view.closed=False;view.active=True;view.busy=True
    class BlockingVault:
        def status(self):raise AssertionError('busy observer called blocking status')
    view.vault=BlockingVault()
    view._observe_status()

@pytest.mark.asyncio
async def test_invalid_tokens_disable_controls_and_reunlock_restores(tmp_path,monkeypatch):
    monkeypatch.setattr(core,'loop',asyncio.get_running_loop())
    services,vault,transport,pins=setup(tmp_path);client=Client(ui.page('/_z05_exhausted'))
    view=None;initial=None
    try:
        with client:
            view=PersonalRankingView(services);view.set_active(True);view.add_tag('tag')
            initial=view._context_task
            def reject(*a,**kw):raise VaultError('invalid-grant')
            vault.search=reject
            await view.load()
            assert not view.tags.enabled and not view.fetch_button.enabled
            assert '借用アカウントを利用できません' in view.notice.text
            vault.lock();vault.unlock('fixture-password');view.availability()
            assert view.tags.enabled and view.fetch_button.enabled
    finally:
        try:await drain_personal_view(view,initial)
        finally:client.delete();vault.close();services.close()
