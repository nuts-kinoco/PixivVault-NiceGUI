
import json,os
from types import SimpleNamespace
import pytest
from nicegui import Client,ui
from required_network_guard import no_network
from pixivvault_nicegui.ui.external_vault_view import ExternalVaultView
from test_rz02_migration import make,legacy_on

@pytest.mark.asyncio
async def test_reopen_ui_then_uid_update_does_not_silently_stop_inherited_on(tmp_path,monkeypatch):
    v=make(tmp_path);old,old_bytes=legacy_on(v);v.lock()
    with monkeypatch.context() as m:
        m.setattr(v,'_write_auto',lambda *a,**k:(_ for _ in ()).throw(OSError('generated transient migration failure')))
        assert not v.try_auto_unlock()
    def observed():
        s=v.status()
        return {k:s[k] for k in ('open','auto_unlock','auto_unlock_state','auto_unlock_inherited','auto_unlock_save_failed')}
    states={'after_migration_failure':observed()}
    c=Client(ui.page('/_review_legacy_on'))
    try:
        with c:
            view=ExternalVaultView(SimpleNamespace(external_vault=v))
            view.password.set_value('generated-password')
            await view.action('reopen_saved')
            states['after_reopen']=observed()
            assert v.status()['open'] and v.status()['auto_unlock']
            assert view.update_button.enabled
            view.update_uid_text.set_value('17\n18')
            await view.action('update')
            states['after_update']=observed()
            states['auto_notice']=view.auto_unlock_notice.text
            states['main_notice']=view.notice.text
            states['old_generation_unchanged']=old.read_bytes()==old_bytes
            assert v._keeper is None
            view.close()
        with open(os.environ.get('PV_REVIEW_UI_OBSERVATION',str(tmp_path/'observation.json')),'w',encoding='utf-8') as f:json.dump(states,f,ensure_ascii=False,indent=2)
        assert states['after_update']['auto_unlock'] is True, 'UI config update disabled inherited ON without explicit OFF'
    finally:c.delete();v.close()
