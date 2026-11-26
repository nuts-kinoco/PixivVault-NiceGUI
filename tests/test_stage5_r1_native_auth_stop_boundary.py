"""Native Cookie verification must not bypass an established Pixiv GET stop.

Generated Cookie/profile only; fake transport can never reach the network.
"""

import asyncio
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


@pytest.mark.parametrize(
    ("allow_auth", "missing_shared_history"),
    [(False, False), (False, True), (True, False)],
    ids=["protected-open-gates", "protected-local-401", "normal"],
)
def test_native_profile_save_schedules_auth_only_when_allowed(
    tmp_path, monkeypatch, allow_auth, missing_shared_history,
):
    from nicegui import Client
    from nicegui.page import page as Page

    from pixivvault_nicegui import managed_network_stop_reader as reader_module
    from test_stage5_r1_native_first_draft_shared import _generated_shared_owner

    profile = tmp_path / "generated-profile"
    cookie_root = tmp_path / "generated-credential"
    cookie_root.mkdir()
    cookie = cookie_root / "synthetic-cookie.txt"
    cookie.write_text(
        "# Netscape HTTP Cookie File\n"
        f".pixiv.net\tTRUE\t/\tTRUE\t{int(time.time())+86400}\tPHPSESSID\t55_generatedonly\n",
        encoding="utf-8",
    )
    parent = tmp_path / "generated-storage"
    parent.mkdir()
    store = NativeProfileStore(profile)
    local = ManagedProfileNetworkStopScope(profile)
    shared = _generated_shared_owner(tmp_path)
    if missing_shared_history:
        local.gate.trip(401)

    gets = []

    def fake_get(request, *_args):
        assert request.get_method() == "GET" and request.data is None
        gets.append(request.get_method())
        raise HTTPStatusRejected(401)

    monkeypatch.setattr(
        reader_module, "create_exact_managed_read_client",
        lambda path, gate: LivePixivReadClient(
            path, transport=ManagedNetworkStopTransport(gate, fake_get),
        ),
    )
    binding = NativeProfileBinding(store)
    binding.auth_stop_required = True
    binding.auth_shared_stop_required = True
    binding.managed_network_stop_scope = local
    # With missing provenance the shared owner is rejected before being bound.
    if not missing_shared_history:
        binding.shared_network_stop_scope = shared

    client = Client(Page("/generated-native-save"))

    async def save():
        with client:
            view = binding.create_view(allow_auth=allow_auth)
            view.build()
            view.draft_cookie = cookie
            view.draft_parent = parent
            assert view.save()
            scheduled = tuple(binding._background_tasks)
            if scheduled:
                await asyncio.gather(*scheduled)
            assert len(gets) == int(allow_auth)
            assert len(scheduled) == int(allow_auth)
            assert (view.verify_auth is not None) is allow_auth
            view.close()

    try:
        asyncio.run(save())
    finally:
        client.delete()
        binding.close()

    reopened = NativeProfileStore(profile)
    try:
        saved = reopened.snapshot()
        assert saved.cookie_file == cookie
        assert saved.save_parent == parent
        assert saved.revision == 1
    finally:
        reopened.close()

    if missing_shared_history:
        local_stop = local.gate.snapshot()
        assert (local_stop.blocked, local_stop.status_code, local_stop.revision) == (True, 401, 1)
        assert not shared.gate.snapshot().blocked
        assert shared.gate.snapshot().revision == 0
