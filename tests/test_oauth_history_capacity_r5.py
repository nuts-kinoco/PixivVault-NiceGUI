"""R4 capacity and cross-slot keeper regressions; generated roots only."""
import hashlib
import json
import threading
from contextlib import nullcontext
import pytest
from test_oauth_send_boundary import make_vault, payout, reopened
from pixivvault_nicegui.external_vault import LIMIT, LegacyVaultCrypto, VaultError, keeper_plan, normalize_data
from pixivvault_nicegui.external_vault_transport import VaultResponse, ACCOUNT, OAUTH, INVALID


def json_size(data):
    return len(json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode())


def test_real_limit_history_growth_does_not_leave_known_result_pending(tmp_path, monkeypatch):
    pytest.importorskip('cryptography')
    old = payout()
    old.body['account']['refresh_token'] = 'generated-old-0'
    v, wire, scope = make_vault(tmp_path, [
        payout(), VaultResponse(400, {'error': 'invalid_grant'}), VaultResponse(200, {'ok': True}),
        VaultResponse(429, {'quotas': [{'remaining': 0, 'resetsAt': 4000000}]}),
        old, VaultResponse(200, {'account': {'access_token': 'generated-recovery'}, 'expiresAt': 9000000}),
    ], crypto=LegacyVaultCrypto())
    try:
        v.update_config(uid_text='1')
        epoch, data = v._snapshot()
        history = [hashlib.sha256(('generated-old-'+str(i)).encode()).hexdigest() for i in range(128)]
        data['slots']['1'].update(rejected_refresh_hashes=history, oauth_send_pending=False,
                                  last_refresh_attempt_at=1000)
        data['secret_key'] = ''
        data['secret_key'] = 'g' * (LIMIT-32-json_size(data))
        assert json_size(data) == LIMIT-32
        # Seed an existing valid R4 generation. New compaction is bypassed only
        # for fixture construction, never for the operation under test.
        with monkeypatch.context() as seed:
            if hasattr(v, '_compact_rejections'):
                seed.setattr(v, '_compact_rejections', lambda *a: None)
            v._publish(epoch, data)
        assert len(v._load()[4])-28 == LIMIT-32
        with pytest.raises(VaultError) as error:
            v.refresh(1)
        assert error.value.code == 'rate-limited'
        assert wire.calls == [ACCOUNT, OAUTH, INVALID, ACCOUNT]
        assert not v.status()['oauth_review_required']
        assert not scope.gate.snapshot().blocked
        assert v.status()['slots'][0]['retry_after'] == 3000
        assert (v.root/'EXTERNAL_VAULT_V1').read_bytes() == b'PixivVault external vault sidecar v2\n'
        v = reopened(v)
        assert not v.status()['oauth_review_required']
        v.refresh(1)
        assert wire.calls.count(OAUTH) == 1
        assert v._snapshot()[1]['slots']['1']['access_token'] == 'generated-recovery'
    finally:
        v.close()


def test_waiting_slot_does_not_hide_an_eligible_expiring_slot():
    data = normalize_data({'secret_key': 'generated', 'shaft_uids': [1, 2], 'slots': {
        '1': {'backoff_until': 9000},
        '2': {'access_token': 'generated-old', 'token_expires_at_ms': 1100000},
    }})
    assert keeper_plan(data, 1000) == (2, True, 300)


def test_successful_keeper_fetch_does_not_sleep_past_another_slot_expiry(tmp_path):
    v, wire, _ = make_vault(tmp_path, [
        VaultResponse(200, {'account': {'access_token': 'generated-first'}, 'expiresAt': 4600000}),
        VaultResponse(200, {'account': {'access_token': 'generated-second'}, 'expiresAt': 4601000}),
    ])
    now = [1000]
    v.clock = lambda: now[0]
    try:
        epoch, data = v._snapshot()
        data['slots']['2'].update(access_token='generated-old', token_expires_at_ms=1100000)
        v._publish(epoch, data)
        assert v.keeper_tick() <= 1
        now[0] = 1001
        v.keeper_tick()
        assert wire.calls == [ACCOUNT, ACCOUNT]
        assert v._snapshot()[1]['slots']['2']['token_expires_at_ms'] == 4601000
    finally:
        v.close()


def test_keeper_failure_waits_for_other_ready_slot_without_real_sleep(tmp_path, monkeypatch):
    v, wire, _ = make_vault(tmp_path, [
        VaultResponse(429, {'quotas': [{'remaining': 0, 'resetsAt': 9000000}]}),
    ])
    original_event = threading.Event
    waits = []
    class OneWaitStop:
        def is_set(self): return False
        def wait(self, timeout):
            waits.append(timeout)
            return True
        def set(self): pass
    count = [0]
    def event_factory():
        count[0] += 1
        return OneWaitStop() if count[0] == 1 else original_event()
    try:
        monkeypatch.setattr(threading, 'Event', event_factory)
        v.start_keeper()
        v._keeper.join(3)
        assert not v._keeper.is_alive()
        assert waits == [1]
        assert wire.calls == [ACCOUNT]
        assert not v.status()['oauth_review_required']
    finally:
        v.close()


def test_archive_backed_ciphertext_alone_is_not_a_complete_import(tmp_path):
    from pixivvault_nicegui.external_vault import ExternalVaultService
    v, _, _ = make_vault(tmp_path, [])
    target = None
    try:
        seed_history(v)
        _, _, salt, verify, ciphertext = v._load()
        target = ExternalVaultService(tmp_path/'generated-incomplete-import', crypto=v.crypto)
        with pytest.raises(VaultError, match='rejected-history-unavailable'):
            target.import_legacy('generated-password', salt=salt, verify=verify, ciphertext=ciphertext)
        assert not target.status()['initialized']
        assert not (target.root/'CURRENT').exists()
    finally:
        if target is not None:
            target.close()
        v.close()



def test_keeper_does_not_retry_a_local_capacity_refusal(tmp_path, monkeypatch):
    pytest.importorskip('cryptography')
    v, wire, _ = make_vault(tmp_path, [payout(), VaultResponse(400, {'error': 'invalid_grant'})],
                            crypto=LegacyVaultCrypto())
    try:
        epoch, data = v._snapshot()
        data['secret_key'] = ''
        data['secret_key'] = 'g'*(LIMIT-32-json_size(data))
        with monkeypatch.context() as seed:
            seed.setattr(v, '_compact_rejections', lambda *a: None)
            v._publish(epoch, data)
        v.start_keeper()
        v._keeper.join(3)
        assert not v._keeper.is_alive()
        assert wire.calls == [ACCOUNT]
        assert not v.status()['oauth_review_required']
    finally:
        v.close()



def seed_history(v, count=129, prefix='generated-old-'):
    epoch, data = v._snapshot()
    digests = [hashlib.sha256((prefix+str(i)).encode()).hexdigest() for i in range(count)]
    data['slots']['1']['rejected_refresh_hashes'] = digests
    v._publish(epoch, data)
    return digests


def test_archive_keeps_exact_proofs_across_restart_and_uid_removal(tmp_path):
    old = payout()
    old.body['account']['refresh_token'] = 'generated-old-0'
    v, wire, _ = make_vault(tmp_path, [
        old, VaultResponse(200, {'account': {'access_token': 'generated-recovered'}, 'expiresAt': 9000000}),
    ])
    try:
        # Small caches also need archiving when their owning UID is removed.
        digests = seed_history(v, count=3)
        v.update_config(uid_text='2', secret_key='generated-new-secret')
        from pixivvault_nicegui.external_vault_rejections import ARCHIVE_FIELD
        state = v._snapshot()[1]
        assert ARCHIVE_FIELD in state and '1' not in state['slots']
        assert v._rejections.read(state, v._key, v.crypto, LIMIT) == frozenset(digests)
        v = reopened(v)
        v.refresh(2)
        assert wire.calls == [ACCOUNT, ACCOUNT]
        assert not v.status()['oauth_review_required']
    finally:
        v.close()


def test_archive_chain_append_preserves_previous_immutable_chunk(tmp_path):
    from pixivvault_nicegui.external_vault_rejections import ARCHIVE_FIELD
    v, _, _ = make_vault(tmp_path, [])
    try:
        first = seed_history(v, count=2050)
        first_head = v._snapshot()[1][ARCHIVE_FIELD]
        directory = v._rejections._directory()
        first_bytes = (directory/(first_head['generation']+'.enc')).read_bytes()
        second = seed_history(v, prefix='generated-second-old-')
        state = v._snapshot()[1]
        assert state[ARCHIVE_FIELD] != first_head
        assert (directory/(first_head['generation']+'.enc')).read_bytes() == first_bytes
        assert v._rejections.read(state, v._key, v.crypto, LIMIT) == frozenset(first+second)
        assert v._snapshot()[1]['slots']['1']['rejected_refresh_hashes'] == []
    finally:
        v.close()


def test_unreadable_archive_blocks_before_oauth_and_recovers_when_readable(tmp_path, monkeypatch):
    import pixivvault_nicegui.external_vault_rejections as history_module
    old = payout()
    old.body['account']['refresh_token'] = 'generated-old-0'
    v, wire, scope = make_vault(tmp_path, [
        payout(), old,
        VaultResponse(200, {'account': {'access_token': 'generated-recovered'}, 'expiresAt': 9000000}),
    ])
    original_read = history_module._read_bounded
    def unavailable(path, limit):
        if path.suffix == '.enc':
            raise OSError('generated transient storage failure')
        return original_read(path, limit)
    try:
        seed_history(v)
        with monkeypatch.context() as failure:
            failure.setattr(history_module, '_read_bounded', unavailable)
            with pytest.raises(VaultError, match='rejected-history-unavailable'):
                v.refresh(1)
        assert wire.calls == [ACCOUNT]
        assert not v.status()['oauth_review_required']
        assert not scope.gate.snapshot().blocked
        v = reopened(v)
        v.refresh(1)
        assert wire.calls == [ACCOUNT, ACCOUNT, ACCOUNT]
    finally:
        v.close()


def test_insufficient_nonhistory_space_refuses_before_send_and_config_can_recover(tmp_path, monkeypatch):
    pytest.importorskip('cryptography')
    from pixivvault_nicegui.external_vault_transport import ONLINE
    v, wire, _ = make_vault(tmp_path, [payout(), VaultResponse(400, {'error': 'invalid_grant'})],
                            crypto=LegacyVaultCrypto())
    try:
        epoch, data = v._snapshot()
        data['slots']['1'].update(last_refresh_attempt_at=1000, oauth_send_pending=False)
        data['secret_key'] = ''
        data['secret_key'] = 'g'*(LIMIT-32-json_size(data))
        with monkeypatch.context() as seed:
            seed.setattr(v, '_compact_rejections', lambda *a: None)
            v._publish(epoch, data)
        revision = v._revision
        with pytest.raises(VaultError, match='vault-too-large'):
            v.refresh(1)
        assert wire.calls == [ACCOUNT]
        assert not v.status()['oauth_review_required']
        assert v._revision == revision
        v.update_config(secret_key='generated-smaller-secret')
        wire.responses = [payout(), VaultResponse(200, {'access_token': 'generated-recovered',
                          'refresh_token': 'generated-new-refresh', 'expires_in': 3600}),
                          VaultResponse(200, {'ok': True})]
        v.refresh(1)
        assert wire.calls == [ACCOUNT, ACCOUNT, OAUTH, ONLINE]
        assert not v.status()['oauth_review_required']
    finally:
        v.close()


