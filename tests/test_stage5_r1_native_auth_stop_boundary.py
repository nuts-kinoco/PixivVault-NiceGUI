"""Native Cookie verification must not bypass an established Pixiv GET stop.

Generated Cookie/profile only; fake transport can never reach the network.
"""

from threading import Event
from types import SimpleNamespace
import time

import pytest

from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopped
from pixivvault_nicegui.native_profile import NativeProfileStore
from pixivvault_nicegui.native_startup import NativeProfileBinding
from pixivvault_nicegui.managed_network_stop_profile_scope import ManagedProfileNetworkStopScope
from pixivvault_nicegui.managed_network_stop_transport import ManagedNetworkStopTransport
from pixivvault_nicegui.pixiv_read_client import LivePixivReadClient, HTTPStatusRejected


@pytest.mark.parametrize("status", (401, 403, 429))
def test_auth_check_never_gets_after_durable_stop(tmp_path, monkeypatch, status):
    profile = tmp_path / "generated-profile"
    store = NativeProfileStore(profile)
    scope = ManagedProfileNetworkStopScope(profile)
    store.close()
    scope.gate.trip(status)

    calls = []
    class FakeReader:
        def __init__(self, *_args, **_kwargs):
            self._transport = lambda *_args: calls.append("unauthorized GET")
        def require_identity(self):
            self._transport(None, 1, 1, 1)
            return "synthetic-account"
        def close(self):
            pass

    import pixivvault_nicegui.pixiv_read_client as client_module
    monkeypatch.setattr(client_module, "LivePixivReadClient", FakeReader)
    store = NativeProfileStore(profile)
    binding = NativeProfileBinding(store)
    try:
        # The actual native UI binds this scope before it can dispatch a check.
        binding.managed_network_stop_scope = scope
        binding.auth_stop_required = True
        assert binding._verify_auth(tmp_path / "nonexistent-generated-cookie", Event()) is False
        assert calls == [], "Native auth must not issue any GET under a saved stop"
        assert scope.gate.snapshot().status_code == status
    finally:
        binding.close()


@pytest.mark.parametrize("status", (401, 403, 429))
def test_native_auth_probe_latches_its_own_failure_and_refuses_retry(tmp_path, monkeypatch, status):
    profile = tmp_path / "generated-profile"
    store = NativeProfileStore(profile)
    scope = ManagedProfileNetworkStopScope(profile)
    cookie_root = tmp_path / "generated-credential"
    cookie_root.mkdir()
    cookie = cookie_root / "synthetic-cookie.txt"
    cookie.write_text(
        "# Netscape HTTP Cookie File\n"
        f".pixiv.net\tTRUE\t/\tTRUE\t{int(time.time())+86400}\tPHPSESSID\t55_generatedonly\n",
        encoding="utf-8",
    )
    calls = []

    def synthetic_transport(request, *_):
        assert request.get_method() == "GET" and request.data is None
        calls.append("generated GET")
        raise HTTPStatusRejected(status)

    import pixivvault_nicegui.managed_network_stop_reader as reader_module
    monkeypatch.setattr(
        reader_module, "create_exact_managed_read_client",
        lambda path, gate: LivePixivReadClient(
            path, transport=ManagedNetworkStopTransport(gate, synthetic_transport),
        ),
    )
    binding = NativeProfileBinding(store)
    binding.managed_network_stop_scope = scope
    binding.auth_stop_required = True
    try:
        if status==429:
            from test_display_recovery_batch import Clock
            clock=Clock();binding.auth_retry_policy=clock.policy()
            assert binding._verify_auth(cookie,Event()) is False
            assert len(calls)==3 and not scope.gate.snapshot().blocked
            assert sum(clock.waits)==pytest.approx(10)
            assert binding._verify_auth(cookie,Event()) is False and len(calls)==3
            return
        assert binding._verify_auth(cookie, Event()) is False
        assert calls == ["generated GET"]
        assert scope.gate.snapshot().status_code == status
        assert binding._verify_auth(cookie, Event()) is False
        assert calls == ["generated GET"]
    finally:
        binding.close()


def test_stop_migration_recovery_creates_no_authentication_callback(tmp_path):
    store = NativeProfileStore(tmp_path / "generated-profile")
    binding = NativeProfileBinding(store)
    try:
        view = binding.create_view(allow_auth=False)
        assert view.verify_auth is None
    finally:
        binding.close()
