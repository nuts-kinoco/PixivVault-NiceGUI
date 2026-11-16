"""Generated-only product regressions; no network or original account state."""
import json
import hashlib
import pytest
from test_external_vault import FixtureCrypto, profile
from pixivvault_nicegui.external_vault import ExternalVaultService, VaultError
from pixivvault_nicegui.external_vault_transport import (
    ShaftTransport, UrlopenVaultRequest, VaultResponse, ACCOUNT, OAUTH, ONLINE,
)
from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopGate, _file_identity
from pixivvault_nicegui.managed_network_stop_shared_scope import (
    ManagedUserNetworkStopScope, _OWNER_FILE, _GATE_FILE, _OWNER_SCHEMA, _file_identity_directory,
)


class OfflineWire(UrlopenVaultRequest):
    """Exercise the actual Shaft policy, replacing all network emission."""
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.before_send = None

    def fenced_request(self, method, url, *, observe_send=None, observe_status=None, **kwargs):
        kwargs['validate']()
        action = self.responses.pop(0)
        if isinstance(action, tuple) and action[0] == 'presend':
            raise action[1]
        if observe_send is not None:
            observe_send()
        if self.before_send:
            self.before_send(url)
        self.calls.append(url)
        if isinstance(action, Exception):
            raise action
        if observe_status is not None:
            observe_status(action.status)
        return action


def owned_scope(tmp_path):
    root = tmp_path / 'generated-stop'
    root.mkdir()
    gate = ManagedNetworkStopGate(root / _GATE_FILE)
    (root / _OWNER_FILE).write_text(json.dumps({
        'schema': _OWNER_SCHEMA, 'state': 'sealed',
        'root_identity': list(_file_identity_directory(root)),
        'gate_identity': list(_file_identity(gate.path)),
        'lock_identity': list(_file_identity(gate.lock_path)),
        'history_proof': hashlib.sha256(b'generated-empty-history').hexdigest(),
    }), encoding='utf8')
    return ManagedUserNetworkStopScope(root)


def payout():
    return VaultResponse(200, {'account': {'refresh_token': 'generated-refresh','user':{'id':42}}, 'expired': True, 'uid': 1})


def make_vault(tmp_path, responses, *, crypto=None):
    wire = OfflineWire(responses)
    transport = ShaftTransport(wire, oauth_profiles=[profile(), profile()])
    scope = owned_scope(tmp_path)
    transport.bind_shared_pixiv_stop(scope)
    vault = ExternalVaultService(tmp_path / 'generated-vault', transport=transport, crypto=crypto or FixtureCrypto(), clock=lambda: 1000)
    vault.initialize('generated-password', '1\n2', 'generated-secret')
    return vault, wire, scope


def reopened(vault):
    root, transport, crypto = vault.root, vault.transport, vault.crypto
    vault.close()
    again = ExternalVaultService(root, transport=transport, crypto=crypto, clock=lambda: 5000)
    again.unlock('generated-password')
    return again


def test_account_429_preserves_backoff_without_oauth_restart_stop(tmp_path):
    v, wire, scope = make_vault(tmp_path, [
        VaultResponse(429, {'quotas': [{'remaining': 0, 'resetsAt': 4000000}]}),
        VaultResponse(200, {'account': {'access_token': 'generated-fresh'}, 'expiresAt': 9000000}),
    ])
    try:
        with pytest.raises(VaultError, match='rate-limited'):
            v.refresh(1)
        assert v.status()['slots'][0]['retry_after'] == 3000
        assert not v.status()['oauth_review_required']
        assert not scope.gate.snapshot().blocked
        v = reopened(v)
        v.refresh(2)
        assert not v.status()['oauth_review_required']
        assert wire.calls == [ACCOUNT, ACCOUNT]
    finally:
        v.close()


@pytest.mark.parametrize('where', ('account', 'oauth'))
def test_failure_before_send_does_not_persist_oauth_pending(tmp_path, where):
    responses = [] if where == 'account' else [payout()]
    responses += [('presend', VaultError('transport-failed'))] * (1 if where == 'account' else 2)
    v, wire, scope = make_vault(tmp_path, responses)
    try:
        with pytest.raises(VaultError):
            v.refresh(1)
        assert not v.status()['oauth_review_required']
        assert not scope.gate.snapshot().blocked
        v = reopened(v)
        assert not v.status()['oauth_review_required']
        assert OAUTH not in wire.calls
    finally:
        v.close()


def test_unknown_oauth_send_is_durable_before_send_and_blocks_replay(tmp_path):
    v, wire, scope = make_vault(tmp_path, [payout(), VaultError('transport-failed')])
    observed = []
    def check_durable(url):
        if url != OAUTH:
            return
        _, _, _, _, encrypted = v._load()
        data = v.crypto.decrypt(encrypted, v._key)
        observed.append(data['slots']['1'].get('oauth_send_pending'))
    wire.before_send = check_durable
    try:
        with pytest.raises(VaultError, match='pixiv-outcome-uncertain'):
            v.refresh(1)
        assert observed == [True]
        assert wire.calls == [ACCOUNT, OAUTH]
        assert not scope.gate.snapshot().blocked
        v = reopened(v)
        for operation in (lambda: v.refresh(2), v.keeper_tick, v.start_keeper):
            with pytest.raises(VaultError, match='pixiv-outcome-uncertain'):
                operation()
        assert wire.calls == [ACCOUNT, OAUTH]
    finally:
        v.close()

@pytest.mark.parametrize('supports_boundary', (True, False))
def test_fixture_sent_unknown_and_opaque_fallback_keep_restart_fence(tmp_path, supports_boundary):
    from test_external_vault import FixtureTransport
    class SendingFixture(FixtureTransport):
        def fetch_account(self, uid, secret, slot, now):
            if supports_boundary:
                self._oauth_session.before_send()
            _, _, _, _, encrypted = v._load()
            assert v.crypto.decrypt(encrypted, v._key)['slots']['1']['oauth_send_pending'] is True
            self.calls.append(('generated-oauth-send', uid))
            raise VaultError('pixiv-outcome-uncertain')
    transport = SendingFixture()
    if not supports_boundary:
        transport.oauth_send_guard = None
    v = ExternalVaultService(tmp_path / 'generated-sent-vault', transport=transport,
                             crypto=FixtureCrypto(), clock=lambda: 1000)
    v.initialize('generated-password', '1\n2', 'generated-secret')
    try:
        with pytest.raises(VaultError, match='pixiv-outcome-uncertain'):
            v.refresh(1)
        assert v.status()['oauth_review_required']
        v = reopened(v)
        for operation in (lambda: v.refresh(2), v.keeper_tick, v.start_keeper):
            with pytest.raises(VaultError, match='pixiv-outcome-uncertain'):
                operation()
        assert transport.calls == [('generated-oauth-send', 1)]
    finally:
        v.close()



def test_failed_pending_commit_prevents_send_and_profile_retry(tmp_path, monkeypatch):
    v, wire, scope = make_vault(tmp_path, [payout(), VaultResponse(200, {})])
    revision = v._revision
    attempts = []
    def reject(*args):
        attempts.append('commit')
        raise VaultError('generated-persistence-failed')
    monkeypatch.setattr(v, '_publish', reject)
    try:
        with pytest.raises(VaultError, match='generated-persistence-failed'):
            v.refresh(1)
        assert attempts == ['commit']
        assert wire.calls == [ACCOUNT]
        assert v._revision == revision
        assert not v.status()['oauth_review_required']
        assert not scope.gate.snapshot().blocked
    finally:
        v.close()

@pytest.mark.parametrize('scenario', ('account429', 'unknown-send'))
def test_real_aesgcm_restart_boundary(tmp_path, scenario):
    pytest.importorskip('cryptography')
    from pixivvault_nicegui.external_vault import LegacyVaultCrypto
    responses = ([VaultResponse(429, {'quotas': [{'remaining': 0, 'resetsAt': 4000000}]}),
                  VaultResponse(200, {'account': {'access_token': 'generated-fresh'}, 'expiresAt': 9000000})]
                 if scenario == 'account429' else [payout(), VaultError('transport-failed')])
    v, wire, _ = make_vault(tmp_path, responses, crypto=LegacyVaultCrypto())
    root = v.root
    try:
        with pytest.raises(VaultError):
            v.refresh(1)
        assert v.status()['oauth_review_required'] is (scenario == 'unknown-send')
        v = reopened(v)
        if scenario == 'unknown-send':
            with pytest.raises(VaultError, match='pixiv-outcome-uncertain'):
                v.refresh(2)
            assert wire.calls == [ACCOUNT, OAUTH]
        else:
            v.refresh(2)
            assert wire.calls == [ACCOUNT, ACCOUNT]
            assert not v.status()['oauth_review_required']
    finally:
        v.close()
    for path in root.rglob('*'):
        if path.is_file():
            raw = path.read_bytes()
            assert b'generated-secret' not in raw
            assert b'generated-refresh' not in raw
            assert b'oauth_send_pending' not in raw



def test_known_valid_oauth_result_clears_pending_for_restart(tmp_path):
    v, wire, _ = make_vault(tmp_path, [
        payout(), VaultResponse(200, {'access_token': 'generated-token', 'refresh_token': 'generated-new-refresh', 'expires_in': 3600}),
        VaultResponse(200, {'ok': True}),
    ])
    try:
        v.refresh(1)
        assert wire.calls == [ACCOUNT, OAUTH, ONLINE]
        assert not v.status()['oauth_review_required']
        v = reopened(v)
        assert not v.status()['oauth_review_required']
    finally:
        v.close()


@pytest.mark.parametrize('phase', ('dns', 'tls', 'sent'))
def test_actual_http_path_places_durable_marker_after_tls_before_send(tmp_path, monkeypatch, phase):
    import http.client
    import socket
    import ssl
    from test_vault_socket_fence import State, FakeSocket
    v, wire, scope = make_vault(tmp_path, [payout()])
    original = wire.fenced_request
    def dispatch(method, url, **kwargs):
        if url == OAUTH:
            return UrlopenVaultRequest.fenced_request(wire, method, url, **kwargs)
        return original(method, url, **kwargs)
    monkeypatch.setattr(wire, 'fenced_request', dispatch)
    state = State()
    phases = []
    def dns(*args, **kwargs):
        phases.append('dns')
        assert not v.status()['oauth_review_required']
        if phase == 'dns':
            raise OSError('generated DNS refusal')
        return [(2, 1, 6, '', ('generated.invalid', 443))]
    class TLS:
        def wrap_socket(self, sock, **kwargs):
            phases.append('tls')
            assert not v.status()['oauth_review_required']
            if phase == 'tls':
                raise OSError('generated TLS refusal')
            return sock
    class Connection:
        def __init__(self, *args, **kwargs):
            self.sock = None
        def request(self, *args, **kwargs):
            _, _, _, _, encrypted = v._load()
            durable = v.crypto.decrypt(encrypted, v._key)
            assert durable['slots']['1']['oauth_send_pending'] is True
            phases.append('send')
            self.sock.sendall(b'generated-only')
        def getresponse(self):
            raise OSError('generated response loss')
        def close(self):
            if self.sock is not None:
                self.sock.close()
    monkeypatch.setattr(socket, 'getaddrinfo', dns)
    monkeypatch.setattr(socket, 'socket', lambda *a, **k: FakeSocket(state))
    monkeypatch.setattr(ssl, 'create_default_context', lambda: TLS())
    monkeypatch.setattr(http.client, 'HTTPSConnection', Connection)
    try:
        with pytest.raises(VaultError):
            v.refresh(1)
        assert v.status()['oauth_review_required'] is (phase == 'sent')
        assert state.sends == ([b'generated-only'] if phase == 'sent' else [])
        assert phases.count('send') == (1 if phase == 'sent' else 0)
        assert not scope.gate.snapshot().blocked
    finally:
        v.close()



def test_old_pending_history_remains_blocking_and_profile_isolated(tmp_path):
    v, wire, _ = make_vault(tmp_path, [])
    try:
        epoch, data = v._snapshot()
        data['slots']['1']['oauth_send_pending'] = True
        v._publish(epoch, data)
        v = reopened(v)
        with pytest.raises(VaultError, match='pixiv-outcome-uncertain'):
            v.refresh(2)
        assert wire.calls == []
    finally:
        v.close()


@pytest.mark.parametrize('status', (401, 429))
def test_direct_oauth_fatal_stop_still_latches_shared_history(tmp_path, status):
    v, wire, scope = make_vault(tmp_path, [payout(), VaultResponse(status, {})])
    try:
        with pytest.raises(VaultError, match='pixiv-network-stopped'):
            v.refresh(1)
        assert scope.gate.snapshot().status_code == status
        assert not v.status()['oauth_review_required']
        assert wire.calls == [ACCOUNT, OAUTH]
    finally:
        v.close()

