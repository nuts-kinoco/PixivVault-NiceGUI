import hashlib
import socket
from types import SimpleNamespace

import pytest
from test_oauth_send_boundary import make_vault, payout, reopened
from pixivvault_nicegui.external_vault import LegacyVaultCrypto, VaultError
from pixivvault_nicegui.external_vault_transport import VaultResponse, OAUTH


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    connect=socket.socket.connect
    def local_socketpair(sock,address):
        if isinstance(address,tuple) and address[0] in ('127.0.0.1','::1'):
            return connect(sock,address)
        raise AssertionError("external network forbidden")
    def reject(*args, **kwargs):
        raise AssertionError("no network permitted")
    monkeypatch.setattr(socket.socket, "connect", local_socketpair)
    monkeypatch.setattr(socket, "create_connection", reject)
    monkeypatch.setattr(socket, "getaddrinfo", reject)


def interrupted_result(tmp_path, monkeypatch, rejected=False, slot_count=2, other_pending_false=False):
    import pixivvault_nicegui.external_vault as module
    response = (VaultResponse(400, {"error": "invalid_grant"}) if rejected else
                VaultResponse(200, {"access_token": "generated-recovered-access",
                                    "refresh_token": "generated-recovered-refresh", "expires_in": 3600}))
    v, wire, scope = make_vault(tmp_path, [payout(), response], crypto=LegacyVaultCrypto())
    if slot_count != 2:
        v.update_config(uid_text='\n'.join(map(str, range(1, slot_count + 1))))
    if other_pending_false:
        epoch, data = v._snapshot()
        for uid in data['shaft_uids'][1:]:data['slots'][str(uid)]['oauth_send_pending'] = False
        v._publish(epoch, data)
    replace, count = module.os.replace, 0
    def fail_before_current(source, target):
        nonlocal count
        if target.name == "CURRENT":
            count += 1
            if count == 2:
                raise OSError("generated pre-CURRENT persistence failure")
        return replace(source, target)
    with monkeypatch.context() as fault:
        fault.setattr(module.os, "replace", fail_before_current)
        with pytest.raises(VaultError):
            v.refresh(1)
    assert v.status()["oauth_review_required"] and wire.calls.count(OAUTH) == 1
    return v, wire, scope


@pytest.mark.parametrize("rejected", [False, True])
def test_known_result_recovered_without_replay_and_shared_stop_preserved(tmp_path, monkeypatch, rejected):
    v, wire, scope = interrupted_result(tmp_path, monkeypatch, rejected)
    try:
        v = reopened(v)
        assert v.status()["oauth_review_required"]
        before = v._revision
        calls = list(wire.calls)
        scope.gate.trip(429)
        stopped = scope.gate.snapshot()
        v.recover_known_result("generated-password")
        assert not v.status()["oauth_review_required"]
        assert v._revision != before and wire.calls == calls
        assert scope.gate.snapshot() == stopped and v._keeper_stop.is_set()
        slot = v._snapshot()[1]["slots"]["1"]
        if rejected:
            assert hashlib.sha256(b"generated-refresh").hexdigest() in slot["rejected_refresh_hashes"]
        else:
            assert slot["access_token"] == "generated-recovered-access"
            assert slot["refresh_token"] == "generated-recovered-refresh"
        for path in (v.root / "known-results").rglob("*.enc"):
            assert b"generated-recovered" not in path.read_bytes()
        pointer = (v.root / "CURRENT").read_bytes()
        with pytest.raises(VaultError):
            v.recover_known_result("generated-password")
        assert (v.root / "CURRENT").read_bytes() == pointer and wire.calls == calls
    finally:
        v.close()


def test_true_unknown_has_no_recovery_evidence(tmp_path):
    v, wire, scope = make_vault(tmp_path, [payout(), VaultError("transport-failed")], crypto=LegacyVaultCrypto())
    try:
        with pytest.raises(VaultError):
            v.refresh(1)
        current = (v.root / "CURRENT").read_bytes()
        calls = list(wire.calls)
        with pytest.raises(VaultError, match="known-result-unavailable"):
            v.recover_known_result("generated-password")
        assert (v.root / "CURRENT").read_bytes() == current and wire.calls == calls
        v.unlock("generated-password")
        assert v.status()["oauth_review_required"]
        with pytest.raises(VaultError):v.keeper_tick()
        assert wire.calls == calls
    finally:
        v.close()


@pytest.mark.parametrize("fault", ["password", "proof", "result"])
def test_bad_auth_or_corrupt_evidence_never_clears_pending(tmp_path, monkeypatch, fault):
    v, wire, _ = interrupted_result(tmp_path, monkeypatch)
    try:
        if fault != "password":
            path = v.root / "known-results" / v._revision / (fault + ".enc")
            payload = bytearray(path.read_bytes())
            payload[-1] ^= 1
            path.write_bytes(payload)
        current = (v.root / "CURRENT").read_bytes()
        calls = list(wire.calls)
        with pytest.raises(VaultError):
            v.recover_known_result("wrong" if fault == "password" else "generated-password")
        assert not v.status()["open"]
        assert (v.root / "CURRENT").read_bytes() == current and wire.calls == calls
        v.unlock("generated-password")
        assert v.status()["oauth_review_required"]
    finally:
        v.close()


@pytest.mark.asyncio
async def test_explicit_ui_recovery_uses_evidence_without_keeper(tmp_path, monkeypatch):
    from nicegui import Client, ui
    from pixivvault_nicegui.ui.external_vault_view import ExternalVaultView
    v, wire, _ = interrupted_result(tmp_path, monkeypatch)
    client = Client(ui.page("/_known_result"))
    starts = []
    monkeypatch.setattr(v, "start_keeper", lambda: starts.append(True))
    try:
        calls = list(wire.calls)
        current = (v.root / 'CURRENT').read_bytes()
        with client:
            view = ExternalVaultView(SimpleNamespace(external_vault=v))
            assert view.recover_button.enabled
            view.render()
            assert v.status()['oauth_review_required']
            assert (v.root / 'CURRENT').read_bytes() == current and not starts and wire.calls == calls
            view.password.set_value("generated-password")
            await view.action("recover_known")
            assert view.password.value == "" and not starts and wire.calls == calls
            assert not v.status()["oauth_review_required"]
            assert "自動更新" in view.recovery.text
            view.close()
    finally:
        client.delete();v.close()


def rewrite_authenticated_result(v, change):
    """Synthetic writer-contract fault, with the test's key; not an attacker bypass."""
    directory = v.root / 'known-results' / v._revision
    result = v.crypto.decrypt((directory / 'result.enc').read_bytes(), v._key)
    change(result)
    encrypted = v.crypto.encrypt(result, v._key)
    proof = v.crypto.decrypt((directory / 'proof.enc').read_bytes(), v._key)
    proof['result_sha256'] = hashlib.sha256(encrypted).hexdigest()
    (directory / 'result.enc').write_bytes(encrypted)
    (directory / 'proof.enc').write_bytes(v.crypto.encrypt(proof, v._key))


@pytest.mark.parametrize('slot_count,other', [(2, 2), (10, 2), (10, 10)])
def test_recovery_rejects_authenticated_result_changing_other_slot_pending(tmp_path, monkeypatch, slot_count, other):
    v, wire, _ = interrupted_result(tmp_path, monkeypatch, slot_count=slot_count)
    try:
        rewrite_authenticated_result(v, lambda data: data['slots'][str(other)].update(oauth_send_pending=True))
        current = (v.root / 'CURRENT').read_bytes()
        calls = list(wire.calls)
        with pytest.raises(VaultError, match='known-result-unavailable'):
            v.recover_known_result('generated-password')
        assert (v.root / 'CURRENT').read_bytes() == current and wire.calls == calls
        v.unlock('generated-password')
        slots = v._snapshot()[1]['slots']
        assert slots['1']['oauth_send_pending'] is True
        assert not slots[str(other)].get('oauth_send_pending', False)
    finally:
        v.close()
