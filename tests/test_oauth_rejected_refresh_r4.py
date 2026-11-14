"""R3-1 repetition and R3-2 input preservation, generated data only."""
import copy
import hashlib
import pytest
from test_oauth_send_boundary import make_vault, payout
from pixivvault_nicegui.external_vault import VaultError, normalize_data
from pixivvault_nicegui.external_vault_transport import VaultResponse, ACCOUNT, OAUTH, INVALID


def test_same_rejected_refresh_is_not_sent_twice_in_one_cycle(tmp_path):
    v, wire, scope = make_vault(tmp_path, [
        payout(), VaultResponse(400, {'error': 'invalid_grant'}), VaultResponse(200, {'ok': True}),
        payout(), VaultResponse(400, {'error': 'invalid_grant'}),
    ])
    try:
        with pytest.raises(VaultError, match='expired-payout'):
            v.refresh(1)
        assert wire.calls == [ACCOUNT, OAUTH, INVALID, ACCOUNT]
        assert not v.status()['oauth_review_required']
        assert not scope.gate.snapshot().blocked
    finally:
        v.close()


def test_failed_keeper_attempt_keeps_original_proactive_interval(tmp_path):
    v, wire, _ = make_vault(tmp_path, [
        payout(), VaultResponse(400, {'error': 'invalid_grant'}), VaultResponse(200, {'ok': True}),
        VaultResponse(429, {'quotas': [{'remaining': 0, 'resetsAt': 1100000}]}),
        VaultResponse(200, {'account': {'access_token': 'generated-unused'}, 'expiresAt': 9000000}),
    ])
    now = [1000]
    v.clock = lambda: now[0]
    try:
        # One slot makes the retry eligibility visible without another slot's
        # independent quota affecting selection.
        v.update_config(uid_text='1')
        with pytest.raises(VaultError, match='rate-limited'):
            v.keeper_tick()
        assert wire.calls == [ACCOUNT, OAUTH, INVALID, ACCOUNT]
        now[0] = 1300
        assert v.keeper_tick() == 900
        assert wire.calls == [ACCOUNT, OAUTH, INVALID, ACCOUNT]
        assert v._snapshot()[1]['slots']['1']['last_fetch_at'] == 1000
    finally:
        v.close()


def test_rejected_fingerprint_survives_restart_and_protects_other_uid(tmp_path, monkeypatch):
    v, wire, scope = make_vault(tmp_path, [])
    original = wire.fenced_request
    def routed_reply(method, url, **kwargs):
        if url == ACCOUNT:
            reply = (payout() if wire.calls.count(ACCOUNT) % 2 == 0 else
                     VaultResponse(429, {'quotas': [{'remaining': 0, 'resetsAt': 9000000}]}))
        elif url == OAUTH:
            reply = VaultResponse(400, {'error': 'invalid_grant'})
        else:
            assert url == INVALID
            reply = VaultResponse(200, {'ok': True})
        wire.responses.append(reply)
        return original(method, url, **kwargs)
    monkeypatch.setattr(wire, 'fenced_request', routed_reply)
    try:
        with pytest.raises(VaultError, match='rate-limited'):
            v.refresh(1)
        root, transport, crypto = v.root, v.transport, v.crypto
        v.close()
        from pixivvault_nicegui.external_vault import ExternalVaultService
        v = ExternalVaultService(root, transport=transport, crypto=crypto, clock=lambda: 1300)
        v.unlock('generated-password')
        with pytest.raises(VaultError, match='rate-limited'):
            v.refresh(2)
        assert wire.calls.count(OAUTH) == 1
        assert wire.calls.count(INVALID) == 1
        assert not scope.gate.snapshot().blocked
        assert not v.status()['oauth_review_required']
    finally:
        v.close()


def test_different_refresh_after_rejection_can_still_recover(tmp_path):
    from pixivvault_nicegui.external_vault_transport import ONLINE
    different = payout()
    different.body['account']['refresh_token'] = 'generated-replacement-refresh'
    v, wire, _ = make_vault(tmp_path, [
        payout(), VaultResponse(400, {'error': 'invalid_grant'}), VaultResponse(200, {'ok': True}),
        different, VaultResponse(200, {'access_token': 'generated-recovered',
                                      'refresh_token': 'generated-rotated', 'expires_in': 3600}),
        VaultResponse(200, {'ok': True}),
    ])
    try:
        v.refresh(1)
        assert wire.calls == [ACCOUNT, OAUTH, INVALID, ACCOUNT, OAUTH, ONLINE]
        assert v._snapshot()[1]['slots']['1']['access_token'] == 'generated-recovered'
        assert not v.status()['oauth_review_required']
    finally:
        v.close()


def test_rejection_is_remembered_even_when_borrowed_uid_is_missing(tmp_path):
    missing = payout()
    missing.body['account'].pop('user')  # requester uid is not the borrowed identity
    v, wire, _ = make_vault(tmp_path, [
        missing, VaultResponse(400, {'error': 'invalid_grant'}),
        missing, VaultResponse(400, {'error': 'invalid_grant'}),
    ])
    try:
        with pytest.raises(VaultError, match='expired-payout'):
            v.refresh(1)
        assert wire.calls == [ACCOUNT, OAUTH, ACCOUNT]
        slot = v._snapshot()[1]['slots']['1']
        assert slot['rejected_refresh_hashes'] == [hashlib.sha256(b'generated-refresh').hexdigest()]
        assert 'reported_refresh_hash' not in slot
        assert not v.status()['oauth_review_required']
    finally:
        v.close()


def test_rejected_token_state_does_not_leak_to_another_vault(tmp_path):
    from pixivvault_nicegui.external_vault import ExternalVaultService
    from pixivvault_nicegui.external_vault_transport import ONLINE
    v, wire, _ = make_vault(tmp_path, [
        payout(), VaultResponse(400, {'error': 'invalid_grant'}), VaultResponse(200, {'ok': True}),
        VaultResponse(429, {'quotas': [{'remaining': 0, 'resetsAt': 9000000}]}),
        payout(), VaultResponse(200, {'access_token': 'generated-other-vault',
                                      'refresh_token': 'generated-rotated', 'expires_in': 3600}),
        VaultResponse(200, {'ok': True}),
    ])
    other = None
    try:
        with pytest.raises(VaultError, match='rate-limited'):
            v.refresh(1)
        # Reuse the transport on the same thread to verify its callback/predicate
        # lifetime is bounded by the originating vault's operation.
        other = ExternalVaultService(tmp_path / 'generated-other-vault', transport=v.transport,
                                     crypto=v.crypto, clock=lambda: 1000)
        other.initialize('generated-password', '1', 'generated-other-secret')
        other.refresh(1)
        assert wire.calls == [ACCOUNT, OAUTH, INVALID, ACCOUNT, ACCOUNT, OAUTH, ONLINE]
        assert not other.status()['oauth_review_required']
    finally:
        if other is not None:
            other.close()
        v.close()



def test_normalize_data_does_not_mutate_token_or_slot_management():
    token = {'access_token': 'generated', 'refresh_token': 'generated-refresh',
             'expires_in': 3600, 'user': {'name': ['generated-only']}}
    raw = {'secret_key': 'generated-secret', 'shaft_uids': [1], 'slots': {'1': token}}
    before = copy.deepcopy(raw)
    normalized = normalize_data(raw)
    assert raw == before
    assert 'consecutive_failures' not in token
    assert normalized['slots']['1']['consecutive_failures'] == 0
    normalized['slots']['1']['consecutive_failures'] = 8
    assert raw == before


def test_oauth_validation_does_not_import_remote_management_fields(tmp_path):
    reply = {'access_token': 'generated-new', 'refresh_token': 'generated-new-refresh',
             'expires_in': 3600, 'reported_refresh_hash': 'remote-untrusted',
             'rejected_refresh_hashes': ['remote-untrusted'], 'oauth_send_pending': True}
    before = copy.deepcopy(reply)
    v, wire, _ = make_vault(tmp_path, [
        payout(), VaultResponse(200, reply), VaultResponse(200, {'ok': True}),
    ])
    local_hash = hashlib.sha256(b'generated-unrelated-previous-token').hexdigest()
    try:
        epoch, data = v._snapshot()
        data['slots']['1']['reported_refresh_hash'] = local_hash
        v._publish(epoch, data)
        v.refresh(1)
        assert reply == before
        assert v._snapshot()[1]['slots']['1']['reported_refresh_hash'] == local_hash
        assert 'rejected_refresh_hashes' not in v._snapshot()[1]['slots']['1']
        assert not v.status()['oauth_review_required']
    finally:
        v.close()


def test_historical_report_hash_is_not_reinterpreted_as_rejection(tmp_path):
    from pixivvault_nicegui.external_vault_transport import ONLINE
    v, wire, _ = make_vault(tmp_path, [
        payout(), VaultResponse(200, {'access_token': 'generated-recovered',
                                      'refresh_token': 'generated-rotated', 'expires_in': 3600}),
        VaultResponse(200, {'ok': True}),
    ])
    try:
        epoch, data = v._snapshot()
        # A historical "reported" marker retains its original report-dedup
        # meaning; only a new proven rejected marker blocks OAuth.
        data['slots']['1']['reported_refresh_hash'] = hashlib.sha256(b'generated-refresh').hexdigest()
        v._publish(epoch, data)
        v.refresh(1)
        assert wire.calls == [ACCOUNT, OAUTH, ONLINE]
        assert not v.status()['oauth_review_required']
    finally:
        v.close()


def test_alternating_known_rejected_tokens_are_not_sent_again(tmp_path, monkeypatch):
    second = payout()
    second.body['account']['refresh_token'] = 'generated-second-bad-refresh'
    v, wire, _ = make_vault(tmp_path, [])
    original = wire.fenced_request
    def routed_reply(method, url, **kwargs):
        if url == ACCOUNT:
            reply = payout() if wire.calls.count(ACCOUNT) % 2 == 0 else second
        elif url == OAUTH:
            reply = VaultResponse(400, {'error': 'invalid_grant'})
        else:
            assert url == INVALID
            reply = VaultResponse(200, {'ok': True})
        wire.responses.append(reply)
        return original(method, url, **kwargs)
    monkeypatch.setattr(wire, 'fenced_request', routed_reply)
    try:
        with pytest.raises(VaultError, match='expired-payout'):
            v.refresh(1)
        with pytest.raises(VaultError, match='expired-payout'):
            v.refresh(1)
        assert wire.calls.count(OAUTH) == 2
        assert not v.status()['oauth_review_required']
    finally:
        v.close()



