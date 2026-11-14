"""F1: resolved OAuth must not poison the vault after later account/report errors."""
import pytest
from test_oauth_send_boundary import make_vault, payout, reopened
from pixivvault_nicegui.external_vault import VaultError, LegacyVaultCrypto
from pixivvault_nicegui.external_vault_transport import VaultResponse, ACCOUNT, OAUTH, INVALID


@pytest.mark.parametrize('real_crypto', (False, True))
def test_invalid_grant_then_account429_preserves_wait_and_allows_restart(tmp_path, real_crypto):
    crypto = None
    if real_crypto:
        pytest.importorskip('cryptography')
        crypto = LegacyVaultCrypto()
    v, wire, scope = make_vault(tmp_path, [
        payout(), VaultResponse(400, {'error': 'invalid_grant'}), VaultResponse(200, {'ok': True}),
        VaultResponse(429, {'quotas': [{'remaining': 0, 'resetsAt': 4000000}]}),
        VaultResponse(200, {'account': {'access_token': 'generated-next'}, 'expiresAt': 9000000}),
        VaultResponse(200, {'account': {'access_token': 'generated-keeper'}, 'expiresAt': 10000000}),
    ], crypto=crypto)
    try:
        epoch, data = v._snapshot()
        data['slots']['2'].update(access_token='generated-retained', token_expires_at_ms=6000000)
        v._publish(epoch, data)
        retained = v._snapshot()[1]['slots']['2']
        with pytest.raises(VaultError, match='rate-limited'):
            v.refresh(1)
        assert wire.calls == [ACCOUNT, OAUTH, INVALID, ACCOUNT]
        assert not v.status()['oauth_review_required']
        assert v.status()['slots'][0]['retry_after'] == 3000
        assert v._snapshot()[1]['slots']['2'] == retained
        assert not scope.gate.snapshot().blocked
        with pytest.raises(VaultError, match='backoff'):
            v.refresh(1)
        assert wire.calls == [ACCOUNT, OAUTH, INVALID, ACCOUNT]
        v = reopened(v)
        assert not v.status()['oauth_review_required']
        v.refresh(2)
        v.keeper_tick()
        assert wire.calls == [ACCOUNT, OAUTH, INVALID, ACCOUNT, ACCOUNT, ACCOUNT]
        assert wire.calls.count(OAUTH) == 1
        assert not v.status()['oauth_review_required']
    finally:
        v.close()


@pytest.mark.parametrize('report_stage', ('queue_online', 'online_tick'))
def test_valid_oauth_followed_by_reachable_report_error_has_known_result(tmp_path, report_stage):
    reply = {'access_token': 'generated-new-access', 'refresh_token': 'generated-new-refresh', 'expires_in': 3600}
    # The bounded JSON decoder accepts these non-finite ancillary fields.
    # Existing queue_online _uid(inf) and online_tick canonical(scope=nan)
    # raise OverflowError / ValueError without any mocked report method.
    if report_stage == 'queue_online':
        reply['user'] = {'id': float('inf')}
    else:
        reply['scope'] = float('nan')
    v, wire, scope = make_vault(tmp_path, [
        payout(), VaultResponse(200, reply),
        VaultResponse(200, {'account': {'access_token': 'generated-next'}, 'expiresAt': 9000000}),
    ])
    try:
        with pytest.raises(VaultError, match='refresh-failed'):
            v.refresh(1)
        assert wire.calls == [ACCOUNT, OAUTH]
        assert not v.status()['oauth_review_required']
        assert not scope.gate.snapshot().blocked
        v = reopened(v)
        assert not v.status()['oauth_review_required']
        v.refresh(2)
        assert wire.calls == [ACCOUNT, OAUTH, ACCOUNT]
    finally:
        v.close()


def test_known_result_commit_failure_keeps_prior_fence_and_stops_followup(tmp_path, monkeypatch):
    v, wire, scope = make_vault(tmp_path, [payout(), VaultResponse(400, {'error': 'invalid_grant'})])
    original = v._publish
    transitions = []
    def publish(epoch, data):
        pending = data['slots']['1'].get('oauth_send_pending')
        transitions.append(pending)
        if pending is False:
            raise VaultError('generated-result-persistence-failed')
        return original(epoch, data)
    monkeypatch.setattr(v, '_publish', publish)
    try:
        with pytest.raises(VaultError, match='generated-result-persistence-failed'):
            v.refresh(1)
        assert transitions == [True, False]
        assert wire.calls == [ACCOUNT, OAUTH]
        assert v.status()['oauth_review_required']
        assert not scope.gate.snapshot().blocked
        v = reopened(v)
        with pytest.raises(VaultError, match='pixiv-outcome-uncertain'):
            v.refresh(2)
        assert wire.calls == [ACCOUNT, OAUTH]
    finally:
        v.close()


def test_second_oauth_unknown_after_known_rejection_rearms_durable_pending(tmp_path):
    different = payout()
    different.body['account']['refresh_token'] = 'generated-different-refresh'
    v, wire, _ = make_vault(tmp_path, [
        payout(), VaultResponse(400, {'error': 'invalid_grant'}), VaultResponse(200, {'ok': True}),
        different, VaultError('transport-failed'),
    ])
    try:
        with pytest.raises(VaultError, match='pixiv-outcome-uncertain'):
            v.refresh(1)
        assert wire.calls == [ACCOUNT, OAUTH, INVALID, ACCOUNT, OAUTH]
        assert v.status()['oauth_review_required']
        v = reopened(v)
        with pytest.raises(VaultError, match='pixiv-outcome-uncertain'):
            v.keeper_tick()
        assert wire.calls.count(OAUTH) == 2
    finally:
        v.close()


@pytest.mark.parametrize('reply', (
    VaultResponse(200, {'refresh_token': 'generated-only-no-access'}),
    VaultResponse(200, {'access_token': 'generated-access', 'expires_in': float('nan')}),
))
def test_unusable_or_unknown_oauth_reply_does_not_resolve_pending(tmp_path, reply):
    v, wire, _ = make_vault(tmp_path, [payout(), reply])
    try:
        with pytest.raises(VaultError):
            v.refresh(1)
        assert wire.calls == [ACCOUNT, OAUTH]
        assert v.status()['oauth_review_required']
        v = reopened(v)
        with pytest.raises(VaultError, match='pixiv-outcome-uncertain'):
            v.refresh(2)
        assert wire.calls == [ACCOUNT, OAUTH]
    finally:
        v.close()


