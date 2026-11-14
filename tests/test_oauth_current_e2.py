"""E2: real generated CURRENT publication and stale-memory send boundaries."""
import pytest
from test_oauth_send_boundary import make_vault, payout, reopened
from pixivvault_nicegui.external_vault import LegacyVaultCrypto, VaultError
from pixivvault_nicegui.external_vault_transport import VaultResponse, ACCOUNT, OAUTH


@pytest.mark.parametrize('stage', ('config', 'before_send', 'known_success'))
def test_post_current_failure_never_resends_and_preserves_success(tmp_path, monkeypatch, stage):
    pytest.importorskip('cryptography')
    import pixivvault_nicegui.external_vault as module
    reply = VaultResponse(200, {'access_token': 'generated-success',
                               'refresh_token': 'generated-rotated', 'expires_in': 3600})
    v, wire, _ = make_vault(tmp_path, [payout(), reply], crypto=LegacyVaultCrypto())
    replace = module.os.replace
    count = 0
    target_count = 2 if stage == 'known_success' else 1
    def interrupted(source, target):
        nonlocal count
        replace(source, target)
        if target.name == 'CURRENT':
            count += 1
            if count == target_count:
                raise OSError('generated post-CURRENT failure')
    try:
        revision = v._revision
        with monkeypatch.context() as failure:
            failure.setattr(module.os, 'replace', interrupted)
            with pytest.raises((OSError, VaultError)):
                if stage == 'config':
                    v.update_config(secret_key='generated-new')
                else:
                    v.refresh(1)
        assert (v.root/'CURRENT').read_text() != v._revision
        assert stage == 'known_success' or v._revision == revision
        sent = wire.calls.count(OAUTH)
        assert sent == (1 if stage == 'known_success' else 0)
        # Supply expired accounts for an attempted next refresh in the SAME
        # process. The real before-send callback must reject stale revision.
        wire.responses[:] = [payout(), reply, payout(), reply]
        with pytest.raises(VaultError):
            v.refresh(2)
        assert wire.calls.count(OAUTH) == sent
        v = reopened(v)
        state = v._snapshot()[1]
        if stage == 'known_success':
            assert not v.status()['oauth_review_required']
            assert state['slots']['1']['access_token'] == 'generated-success'
            assert state['slots']['1']['refresh_token'] == 'generated-rotated'
            assert state['slots']['1']['token_expires_at_ms'] == 4600000
        elif stage == 'before_send':
            assert v.status()['oauth_review_required']
            with pytest.raises(VaultError, match='pixiv-outcome-uncertain'):
                v.keeper_tick()
        else:
            assert state['secret_key'] == 'generated-new'
        assert wire.calls.count(OAUTH) == sent
    finally:
        v.close()


def test_known_short_oauth_preserves_previous_usable_token(tmp_path):
    v, wire, _ = make_vault(tmp_path, [
        payout(), VaultResponse(200, {'access_token': 'generated-short', 'expires_in': 60}),
    ])
    try:
        epoch, data = v._snapshot()
        data['slots']['1'].update(access_token='generated-old', refresh_token='generated-old-refresh',
                                 token_expires_at_ms=1200000)
        v._publish(epoch, data)
        with pytest.raises(VaultError, match='short-payout'):
            v.refresh(1)
        v = reopened(v)
        slot = v._snapshot()[1]['slots']['1']
        assert slot['access_token'] == 'generated-old'
        assert slot['refresh_token'] == 'generated-old-refresh'
        assert slot['token_expires_at_ms'] == 1200000
        assert not v.status()['oauth_review_required']
        assert wire.calls == [ACCOUNT, OAUTH]
    finally:
        v.close()
