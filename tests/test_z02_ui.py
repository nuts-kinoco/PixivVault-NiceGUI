"""Headless NiceGUI controls, generated Vault and real CurrentUser DPAPI."""
import asyncio
from threading import Event
from types import SimpleNamespace
import pytest
from nicegui import Client,ui
from required_network_guard import no_network
from pixivvault_nicegui.ui.external_vault_view import ExternalVaultView
from pixivvault_nicegui.external_vault import VaultError
from test_z02_policy import vault,legacy_on

@pytest.mark.asyncio
async def test_opt_in_unchecked_and_disk_status_visible_while_locked(tmp_path,monkeypatch):
    v=vault(tmp_path);starts=[]
    monkeypatch.setattr(v,'start_keeper',lambda **kw:starts.append(kw))
    c=Client(ui.page('/_z02_optin'))
    try:
        with c:
            view=ExternalVaultView(SimpleNamespace(external_vault=v))
            assert view.auto_unlock.value is False
            v.lock();view.render();view.password.set_value('generated-password')
            await view.action('unlock')
            assert v.status()['open'] and v.status()['auto_unlock'] is False
            view.auto_unlock.set_value(True);view.auto_changed()
            assert v.status()['auto_unlock'] is True and view.auto_unlock.value is False
            v.lock();view.render()
            assert 'ON' in view.auto_unlock_notice.text and not v.status()['open']
            view.password.set_value('generated-password')
            await view.action('unlock')
            assert v.status()['auto_unlock'] is True and not view.auto_unlock.value
            view.auto_off()
            assert not v.status()['auto_unlock']
            view.close()
    finally:c.delete();v.close()

@pytest.mark.asyncio
async def test_legacy_choice_is_unselected_and_can_stop_on_manual_unlock(tmp_path,monkeypatch):
    v=vault(tmp_path);legacy_on(v);v.lock()
    monkeypatch.setattr(v,'start_keeper',lambda **kw:None)
    c=Client(ui.page('/_z02_inherited'))
    try:
        with c:
            view=ExternalVaultView(SimpleNamespace(external_vault=v))
            assert view.legacy_auto_choice.visible and view.legacy_auto_choice.value is None
            assert '旧設定' in view.auto_unlock_notice.text
            view.password.set_value('generated-password');await view.action('unlock')
            assert not v.status()['open']
            assert '選んで' in view.notice.text
            view.legacy_auto_choice.set_value('stop');await view.action('unlock')
            assert v.status()['open'] and not v.status()['auto_unlock']
            assert not v.status()['auto_unlock_inherited'] and not view.legacy_auto_choice.visible
            assert view.password.value==''
            view.close()
    finally:c.delete();v.close()

@pytest.mark.asyncio
async def test_ui_click_epoch_precedes_delayed_thread_and_lock_wins(tmp_path,monkeypatch):
    import pixivvault_nicegui.ui.external_vault_view as module
    v=vault(tmp_path);v.lock();entered=Event();release=Event();starts=[]
    monkeypatch.setattr(v,'start_keeper',lambda **kw:starts.append(kw))
    original=v.unlock
    def delayed(*args,**kwargs):
        entered.set();assert release.wait(5)
        return original(*args,**kwargs)
    monkeypatch.setattr(v,'unlock',delayed)
    c=Client(ui.page('/_z02_race'));task=None
    try:
        with c:
            view=ExternalVaultView(SimpleNamespace(external_vault=v))
            view.auto_unlock.set_value(True);view.password.set_value('generated-password')
            task=asyncio.create_task(view.action('unlock'))
            assert await asyncio.to_thread(entered.wait,3)
            view.lock();release.set();await task
            assert not v.status()['open'] and not v.status()['auto_unlock'] and starts==[]
            assert view.password.value=='' and not view.auto_unlock.value
            view.close()
    finally:
        release.set()
        if task is not None:await task
        c.delete();v.close()

def test_unknown_and_failed_off_display_disk_truth_not_memory(tmp_path,monkeypatch):
    v=vault(tmp_path);v.configure_auto_unlock(True)
    c=Client(ui.page('/_z02_disk_truth'))
    try:
        with c:
            view=ExternalVaultView(SimpleNamespace(external_vault=v))
            monkeypatch.setattr(v,'_write_auto',lambda *a,**k:(_ for _ in ()).throw(OSError('generated')))
            view.lock()
            assert not v.status()['open'] and 'ON' in view.auto_unlock_notice.text
            assert '確認できません' in view.auto_unlock_notice.text
            v._auto_record().path.write_bytes(b'broken-generated-packet')
            view.render()
            assert v.status()['auto_unlock'] is None
            assert '状態を確認できません' in view.auto_unlock_notice.text
            view.close()
    finally:c.delete();v.close()

def test_other_client_off_clears_an_older_unsubmitted_selection(tmp_path):
    v=vault(tmp_path);c=Client(ui.page('/_z02_other_client_off'))
    try:
        with c:
            view=ExternalVaultView(SimpleNamespace(external_vault=v))
            view.auto_unlock.set_value(True)
            v.lock_explicitly()
            view.render()
            assert not view.auto_unlock.value
            assert not v.status()['auto_unlock'] and not v.status()['open']
            view.close()
    finally:c.delete();v.close()
