import pytest
from types import SimpleNamespace
from nicegui import Client, ui
from pixivvault_nicegui.ui.external_vault_view import ExternalVaultView
from test_oauth_send_boundary import make_vault


@pytest.mark.asyncio
async def test_pending_becomes_visible_and_forged_update_does_not_run(tmp_path):
    v, wire, _ = make_vault(tmp_path, [])
    client = Client(ui.page('/_pending_status'))
    try:
        with client:
            view = ExternalVaultView(SimpleNamespace(external_vault=v))
            view.set_active(True)
            epoch, data = v._snapshot()
            data['slots']['1']['oauth_send_pending'] = True
            v._publish(epoch, data)
            revision = v._revision
            view._observe_status()
            assert not view.refresh_button.enabled
            assert not view.update_button.enabled
            assert not view.auto_unlock.enabled
            assert '結果を確認できない' in view.notice.text
            assert '利用可能' not in view.notice.text
            await view.action('refresh')
            await view.action('update')
            assert wire.calls == [] and v._revision == revision
            assert v.status()['oauth_review_required']
            view.close()
    finally:
        client.delete()
        v.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('code', (401, 403, 429))
async def test_explicit_local_inspection_displays_shared_stop_without_clearing(tmp_path, code):
    v, wire, scope = make_vault(tmp_path, [])
    scope.gate.trip(code)
    state = scope.gate.snapshot()
    revision = v._revision
    client = Client(ui.page('/_stop_status'))
    try:
        with client:
            view = ExternalVaultView(SimpleNamespace(external_vault=v))
            await view.inspect_recovery()
            assert str(code) in view.recovery.text
            assert '解除' in view.recovery.text
            assert not view.refresh_button.enabled
            await view.action('refresh')
            assert wire.calls == [] and v._revision == revision
            assert scope.gate.snapshot() == state
            view.close()
    finally:
        client.delete()
        v.close()


def test_unknown_shared_history_is_not_reported_as_clear(tmp_path):
    v, wire, scope = make_vault(tmp_path, [])
    try:
        # A generated durable intent, with no response evidence.
        scope.gate._begin_inflight()
        result = v.inspect_recovery()
        assert result['shared_stop']['state'] == 'unavailable'
        assert wire.calls == []
        assert scope.gate.inflight_path.exists()
    finally:
        v.close()


@pytest.mark.asyncio
async def test_locked_inspection_does_not_claim_oauth_resolution(tmp_path):
    v, wire, _ = make_vault(tmp_path, [])
    epoch, data = v._snapshot()
    data['slots']['1']['oauth_send_pending'] = True
    v._publish(epoch, data)
    revision = v._revision
    v.lock()
    client = Client(ui.page('/_locked_inspection'))
    try:
        with client:
            view = ExternalVaultView(SimpleNamespace(external_vault=v))
            await view.inspect_recovery()
            assert 'ロック中' in view.recovery.text
            assert '認証確認や通信再開ではありません' in view.recovery.text
            assert wire.calls == [] and v._revision == revision
            v.unlock('generated-password')
            view.render()
            assert not view.refresh_button.enabled
            assert v.status()['oauth_review_required']
            view.close()
    finally:
        client.delete()
        v.close()
