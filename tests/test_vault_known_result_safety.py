"""Real encrypted generated files, bounded fault injection, no external I/O."""
import hashlib
import threading

import pytest
import pixivvault_nicegui.external_vault as module
from pixivvault_nicegui.external_vault import LegacyVaultCrypto, VaultError
from pixivvault_nicegui.external_vault_transport import OAUTH, VaultResponse
from test_oauth_send_boundary import make_vault, payout, reopened
from test_vault_known_result_recovery import (
    interrupted_result, no_network, rewrite_authenticated_result,
)


def record_bytes(v):
    return {p.relative_to(v.root).as_posix(): p.read_bytes()
            for p in (v.root / 'known-results').rglob('*.enc')}


@pytest.mark.parametrize('slot_count,explicit_false', [(1, False), (2, False), (2, True), (10, True)])
def test_pending_boundary_accepts_only_target_resolution(tmp_path, monkeypatch, slot_count, explicit_false):
    v, wire, _ = interrupted_result(tmp_path, monkeypatch, slot_count=slot_count, other_pending_false=explicit_false)
    try:
        if explicit_false:
            def mark_absent_as_false(data):
                for uid in data['shaft_uids'][1:]:
                    data['slots'][str(uid)]['oauth_send_pending'] = False
            rewrite_authenticated_result(v, mark_absent_as_false)
        calls = list(wire.calls)
        v.recover_known_result('generated-password')
        assert not v.status()['oauth_review_required'] and wire.calls == calls
        assert len(v._snapshot()[1]['shaft_uids']) == slot_count
    finally:
        v.close()


@pytest.mark.parametrize('pending_uids', [(), (1, 2), tuple(range(1, 11))])
def test_zero_or_multiple_pending_cannot_use_single_result(tmp_path, monkeypatch, pending_uids):
    v, wire, scope = interrupted_result(tmp_path, monkeypatch, slot_count=10)
    try:
        epoch, data = v._snapshot()
        for uid in data['shaft_uids']:
            data['slots'][str(uid)]['oauth_send_pending'] = uid in pending_uids
        v._publish(epoch, data)
        current = (v.root / 'CURRENT').read_bytes()
        records, calls = record_bytes(v), list(wire.calls)
        scope.gate.trip(403); stopped = scope.gate.snapshot()
        with pytest.raises(VaultError, match='known-result-unavailable'):
            v.recover_known_result('generated-password')
        assert (v.root / 'CURRENT').read_bytes() == current
        v.unlock('generated-password')
        observed = v._snapshot()[1]['slots']
        assert tuple(uid for uid in range(1, 11) if observed[str(uid)]['oauth_send_pending']) == pending_uids
        assert record_bytes(v) == records and wire.calls == calls
        assert scope.gate.snapshot() == stopped
    finally:
        v.close()


@pytest.mark.parametrize('field,value', [('oauth_send_pending', None), ('oauth_send_pending', 0)])
def test_pending_requires_boolean_even_in_authenticated_result(tmp_path, monkeypatch, field, value):
    v, wire, _ = interrupted_result(tmp_path, monkeypatch)
    try:
        rewrite_authenticated_result(v, lambda data: data['slots']['2'].update({field: value}))
        current, calls = (v.root / 'CURRENT').read_bytes(), list(wire.calls)
        with pytest.raises(VaultError, match='known-result-unavailable'):
            v.recover_known_result('generated-password')
        assert (v.root / 'CURRENT').read_bytes() == current and wire.calls == calls
    finally:
        v.close()


def test_unlock_reopen_and_observation_never_restore_implicitly(tmp_path, monkeypatch):
    v, wire, _ = interrupted_result(tmp_path, monkeypatch)
    try:
        current, records, calls = (v.root / 'CURRENT').read_bytes(), record_bytes(v), list(wire.calls)
        persisted=v.crypto.decrypt(v._load()[4],v._key)
        v = reopened(v)
        # Z01 normal password unlock persists device metadata. Its authenticated
        # data is unchanged; only local reopen/observation/recovery are no-write.
        assert v.crypto.decrypt(v._load()[4],v._key)==persisted
        current=(v.root/'CURRENT').read_bytes()
        v.reopen_saved_state('generated-password')
        v.inspect_recovery()
        assert v.status()['oauth_review_required'] and v._keeper_stop.is_set()
        assert (v.root / 'CURRENT').read_bytes() == current and record_bytes(v) == records
        with pytest.raises(VaultError, match='pixiv-outcome-uncertain'):
            v.start_keeper()
        assert wire.calls == calls
        v.recover_known_result('generated-password')
        assert not v.status()['oauth_review_required'] and wire.calls == calls
    finally:
        v.close()


@pytest.mark.parametrize('field,value', [
    ('base', '0' * 32), ('uid', True),
    ('source_sha256', '0' * 64), ('result_sha256', '0' * 64),
])
def test_authenticated_proof_must_bind_exact_generation_slot_and_ciphertexts(tmp_path, monkeypatch, field, value):
    v, wire, _ = interrupted_result(tmp_path, monkeypatch)
    try:
        path = v.root / 'known-results' / v._revision / 'proof.enc'
        proof = v.crypto.decrypt(path.read_bytes(), v._key)
        proof[field] = value
        path.write_bytes(v.crypto.encrypt(proof, v._key))
        current, records, calls = (v.root / 'CURRENT').read_bytes(), record_bytes(v), list(wire.calls)
        with pytest.raises(VaultError, match='known-result-unavailable'):
            v.recover_known_result('generated-password')
        assert (v.root / 'CURRENT').read_bytes() == current and record_bytes(v) == records
        v.unlock('generated-password')
        assert v.status()['oauth_review_required'] and wire.calls == calls
    finally:
        v.close()


@pytest.mark.parametrize('fault,recoverable', [
    ('result-before', False), ('result-partial', False), ('result-after', False),
    ('proof-before', False), ('proof-partial', False), ('proof-after', True),
    ('directory-sync', True), ('parent-sync', True),
])
def test_record_and_publish_interruption_restarts_without_guessing(tmp_path, monkeypatch, fault, recoverable):
    v, wire, _ = make_vault(tmp_path, [payout(), VaultResponse(200, {
        'access_token': 'generated-recovered-access',
        'refresh_token': 'generated-recovered-refresh', 'expires_in': 3600,
    })], crypto=LegacyVaultCrypto())
    write, sync = module._write, module._fsync_dir
    publish = v._publish
    def broken_publish(epoch, data):
        # Under M3, a journal fault alone must not stop a known result publish.
        # Keep the old restart/partial-record assertions under a simultaneous
        # primary publish failure; the audit tests cover journal-only faults.
        if data['slots']['1'].get('oauth_send_pending') is False:
            raise OSError('generated primary publish interruption')
        return publish(epoch, data)
    hit = []
    def broken_write(path, payload):
        name = path.stem
        if path.parent.parent.name == 'known-results' and fault.startswith(name + '-'):
            hit.append(fault)
            if fault.endswith('-partial'):
                write(path, payload[:len(payload)//2])
            elif fault.endswith('-after'):
                write(path, payload)
            raise OSError('generated record interruption')
        return write(path, payload)
    def broken_sync(path):
        if ((fault == 'directory-sync' and path.parent.name == 'known-results') or
                (fault == 'parent-sync' and path.name == 'known-results')):
            hit.append(fault)
            raise OSError('generated record sync interruption')
        return sync(path)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(module, '_write', broken_write)
            patch.setattr(module, '_fsync_dir', broken_sync)
            patch.setattr(v, '_publish', broken_publish)
            with pytest.raises(VaultError):v.refresh(1)
        assert hit == [fault] and wire.calls.count(OAUTH) == 1
        current, records, calls = (v.root / 'CURRENT').read_bytes(), record_bytes(v), list(wire.calls)
        persisted=v.crypto.decrypt(v._load()[4],v._key)
        v = reopened(v)
        assert v.crypto.decrypt(v._load()[4],v._key)==persisted
        current=(v.root/'CURRENT').read_bytes()
        assert v.status()['oauth_review_required']
        if recoverable:
            v.recover_known_result('generated-password')
            assert v._snapshot()[1]['slots']['1']['access_token'] == 'generated-recovered-access'
            assert not v.status()['oauth_review_required']
        else:
            with pytest.raises(VaultError, match='known-result-unavailable'):
                v.recover_known_result('generated-password')
            assert (v.root / 'CURRENT').read_bytes() == current
            v.unlock('generated-password')
            assert v.status()['oauth_review_required']
            with pytest.raises(VaultError, match='pixiv-outcome-uncertain'):v.refresh(1)
        assert record_bytes(v) == records and wire.calls == calls
    finally:
        v.close()


@pytest.mark.parametrize('rejected', [False, True])
@pytest.mark.parametrize('fault,committed', [
    ('vault-write', False), ('settings-write', False), ('generation-sync', False),
    ('pointer-write', False), ('replace-before', False), ('replace-after', True), ('root-sync', True),
])
def test_restore_interruption_is_old_pending_or_complete_result(tmp_path, monkeypatch, rejected, fault, committed):
    v, wire, scope = interrupted_result(tmp_path, monkeypatch, rejected=rejected)
    records, calls = record_bytes(v), list(wire.calls)
    current = (v.root / 'CURRENT').read_bytes()
    scope.gate.trip(401); stopped = scope.gate.snapshot()
    write, sync, replace = module._write, module._fsync_dir, module.os.replace
    hit = []
    def fail():
        hit.append(fault)
        raise OSError('generated restore interruption')
    def broken_write(path, payload):
        if ((fault == 'vault-write' and path.name == 'vault.enc') or
                (fault == 'settings-write' and path.name == 'settings.json') or
                (fault == 'pointer-write' and path.name.startswith('CURRENT.'))):
            write(path, payload[:len(payload)//2])
            fail()
        return write(path, payload)
    def broken_sync(path):
        if ((fault == 'generation-sync' and path.parent.name == 'generations') or
                (fault == 'root-sync' and path == v.root)):
            fail()
        return sync(path)
    def broken_replace(source, target):
        if target.name == 'CURRENT' and fault == 'replace-before':fail()
        result = replace(source, target)
        if target.name == 'CURRENT' and fault == 'replace-after':fail()
        return result
    try:
        with monkeypatch.context() as patch:
            patch.setattr(module, '_write', broken_write)
            patch.setattr(module, '_fsync_dir', broken_sync)
            patch.setattr(module.os, 'replace', broken_replace)
            with pytest.raises(OSError, match='generated restore interruption'):
                v.recover_known_result('generated-password')
        assert hit == [fault] and not v.status()['open']
        assert v._keeper_stop.is_set()
        assert ((v.root / 'CURRENT').read_bytes() != current) is committed
        v = reopened(v)
        assert v.status()['oauth_review_required'] is (not committed)
        if not committed:
            v.recover_known_result('generated-password')
        slot = v._snapshot()[1]['slots']['1']
        assert slot['oauth_send_pending'] is False
        if rejected:
            assert hashlib.sha256(b'generated-refresh').hexdigest() in slot['rejected_refresh_hashes']
        else:
            assert slot['access_token'] == 'generated-recovered-access'
            assert slot['refresh_token'] == 'generated-recovered-refresh'
        assert record_bytes(v) == records and wire.calls == calls
        # A new service starts with no worker and a fresh, unset event. The
        # interrupted service's event was checked before closing it above.
        assert scope.gate.snapshot() == stopped and v._keeper is None
    finally:
        v.close()


def test_two_explicit_recoveries_cannot_commit_concurrently(tmp_path, monkeypatch):
    v, wire, _ = interrupted_result(tmp_path, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    commit = v._commit
    errors = []
    def paused_commit(*args, **kwargs):
        entered.set()
        assert release.wait(5), 'generated recovery barrier timeout'
        return commit(*args, **kwargs)
    def restore():
        try:v.recover_known_result('generated-password')
        except Exception as error:errors.append(error)
    thread = threading.Thread(target=restore, name='generated-recovery-test')
    calls = list(wire.calls)
    monkeypatch.setattr(v, '_commit', paused_commit)
    try:
        thread.start()
        assert entered.wait(3)
        with pytest.raises(VaultError, match='busy'):
            v.recover_known_result('generated-password')
        release.set();thread.join(3)
        assert not thread.is_alive() and not errors
        assert not v.status()['oauth_review_required'] and wire.calls == calls
    finally:
        release.set();thread.join(3);v.close()


def test_journal_does_not_reduce_supported_windows_root_length(tmp_path):
    # Existing generation directory is 247 characters; the new journal is 249.
    # No OS long-path setting changes or test skip are needed.
    v, wire, scope = make_vault(tmp_path, [payout(), VaultResponse(200, {
        'access_token': 'generated-long-root-access',
        'refresh_token': 'generated-long-root-refresh', 'expires_in': 3600,
    }), VaultResponse(200, {'ok': True})], crypto=LegacyVaultCrypto())
    v.close()
    parent = tmp_path.parent
    root = parent / ('v' * (202 - len(str(parent)) - 1))
    assert len(str(root)) == 202
    v = module.ExternalVaultService(root, transport=v.transport, crypto=v.crypto, clock=lambda: 1000)
    try:
        v.initialize('generated-password', '1', 'generated-secret')
        v.refresh(1)
        assert not v.status()['oauth_review_required'] and wire.calls.count(OAUTH) == 1
        assert not scope.gate.snapshot().blocked
        assert len(list((v.root / 'known-results').glob('*/proof.enc'))) == 1
    finally:
        v.close()
