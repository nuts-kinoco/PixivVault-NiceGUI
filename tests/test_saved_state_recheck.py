import pytest
from nicegui import Client, ui
from types import SimpleNamespace
from test_oauth_send_boundary import make_vault, payout
from pixivvault_nicegui.external_vault import VaultError, LegacyVaultCrypto
from pixivvault_nicegui.external_vault_transport import VaultResponse
from pixivvault_nicegui.ui.external_vault_view import ExternalVaultView


@pytest.mark.parametrize('known_success', (False, True))
def test_local_reopen_uses_saved_generation_without_resend_or_token_inference(tmp_path, monkeypatch, known_success):
    pytest.importorskip('cryptography')
    import pixivvault_nicegui.external_vault as module
    response = (VaultResponse(200, {'access_token':'generated-new','refresh_token':'generated-rotated','expires_in':3600})
                if known_success else VaultError('transport-failed'))
    # Match the original product's payout-first sequence; the expired
    # payout then reaches the same interrupted OAuth result boundary.
    v, wire, scope = make_vault(tmp_path, [payout(), response], crypto=LegacyVaultCrypto())
    replace = module.os.replace
    count = 0
    def interrupted(source, target):
        nonlocal count
        replace(source, target)
        if target.name == 'CURRENT':
            count += 1
            if known_success and count == 2:
                raise OSError('generated post-CURRENT failure')
    try:
        epoch, data = v._snapshot()
        data['slots']['1'].update(access_token='generated-old',refresh_token='generated-old-refresh',token_expires_at_ms=1000000)
        v._publish(epoch, data)
        with monkeypatch.context() as failure:
            failure.setattr(module.os, 'replace', interrupted)
            with pytest.raises(VaultError):
                v.refresh(1)
        assert v.status()['oauth_review_required']
        pointer = (v.root/'CURRENT').read_bytes()
        calls = list(wire.calls)
        stop = scope.gate.snapshot()
        v.reopen_saved_state('generated-password')
        assert (v.root/'CURRENT').read_bytes() == pointer
        assert wire.calls == calls and scope.gate.snapshot() == stop
        assert v._keeper_stop.is_set() and v._keeper is None
        assert v.status()['oauth_review_required'] is not known_success
        slot = v._snapshot()[1]['slots']['1']
        assert slot['access_token'] == ('generated-new' if known_success else 'generated-old')
        assert slot['refresh_token'] == ('generated-rotated' if known_success else 'generated-old-refresh')
        if not known_success:
            with pytest.raises(VaultError, match='pixiv-outcome-uncertain'):v.keeper_tick()
            assert wire.calls == calls
    finally:
        v.close()


def test_reopen_preserves_shared_stop_and_wrong_password_stays_locked(tmp_path):
    v, wire, scope = make_vault(tmp_path, [])
    try:
        scope.gate.trip(429)
        before = scope.gate.snapshot()
        revision = v._revision
        with pytest.raises(VaultError, match='unlock-failed'):v.reopen_saved_state('wrong')
        assert not v.status()['open']
        v.reopen_saved_state('generated-password')
        assert v.status()['open'] and v._revision == revision
        assert scope.gate.snapshot() == before and wire.calls == []
        assert v._keeper_stop.is_set()
    finally:
        v.close()


def test_reopen_rejects_busy_without_discarding_session(tmp_path):
    v, wire, _ = make_vault(tmp_path, [])
    try:
        epoch, data = v._snapshot()
        v._busy.acquire()
        try:
            with pytest.raises(VaultError, match='busy'):v.reopen_saved_state('generated-password')
        finally:v._busy.release()
        assert v._snapshot() == (epoch,data) and wire.calls == []
    finally:
        v.close()


@pytest.mark.asyncio
async def test_pending_guidance_and_explicit_button_do_not_start_keeper(tmp_path, monkeypatch):
    v, wire, _ = make_vault(tmp_path, [])
    client = Client(ui.page('/_saved_state'))
    starts = []
    monkeypatch.setattr(v,'start_keeper',lambda:starts.append('unexpected'))
    try:
        epoch,data=v._snapshot();data['slots']['1']['oauth_send_pending']=True;v._publish(epoch,data)
        revision=v._revision
        with client:
            view=ExternalVaultView(SimpleNamespace(external_vault=v))
            assert 'では解決しません' not in view.notice.text
            assert '保存済み状態' in view.notice.text
            assert view.reopen_button.enabled
            view.password.set_value('generated-password')
            await view.action('reopen_saved')
            assert view.password.value == ''
            assert starts == [] and wire.calls == [] and v._revision == revision
            assert v.status()['oauth_review_required'] and not view.refresh_button.enabled
            assert '自動更新は開始していません' in view.recovery.text
            view.close()
    finally:
        client.delete();v.close()
