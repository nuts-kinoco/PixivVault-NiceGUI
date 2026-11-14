"""Native first-use draft authentication must own its shared STOP boundary.

These are synthetic profiles, a generated reviewed-user gate, and monkeypatched
UI registration; no real Cookie, remote GET, or Windows settings are accessed.
"""

import hashlib
import json
from types import SimpleNamespace
from threading import Event
import time

import pytest

from pixivvault_nicegui import bootstrap
from pixivvault_nicegui.managed_network_stop_gate import (
    ManagedNetworkGateStorageError, ManagedNetworkStopGate, _file_identity,
)
from pixivvault_nicegui.managed_network_stop_shared_scope import (
    ManagedUserNetworkStopScope, _OWNER_FILE, _GATE_FILE, _OWNER_SCHEMA,
    _file_identity_directory,
)
from pixivvault_nicegui.native_profile import NativeProfileStore


def _generated_shared_owner(tmp_path):
    root = tmp_path / "generated-reviewed-user-stop"
    root.mkdir()
    gate = ManagedNetworkStopGate(root / _GATE_FILE)
    (root / _OWNER_FILE).write_text(json.dumps({
        "schema": _OWNER_SCHEMA,
        "state": "sealed",
        "root_identity": list(_file_identity_directory(root)),
        "gate_identity": list(_file_identity(gate.path)),
        "lock_identity": list(_file_identity(gate.lock_path)),
        "history_proof": hashlib.sha256(b"generated-review-only").hexdigest(),
    }), encoding="utf-8")
    return ManagedUserNetworkStopScope(root)


def _stub_native(monkeypatch):
    from nicegui import app, ui
    from pixivvault_nicegui import native_layout

    actual_find_spec = bootstrap.importlib.util.find_spec
    monkeypatch.setattr(
        bootstrap.importlib.util, "find_spec",
        lambda name, *a, **kw: object() if name == "webview" else actual_find_spec(name, *a, **kw),
    )
    monkeypatch.setattr(native_layout, "current_startup_layout",
                        lambda: SimpleNamespace(width=960, height=720, x=0, y=0))
    monkeypatch.setattr(app.native, "window_args", {})
    monkeypatch.setattr(app.native, "settings", {})
    calls, recovered, registered = [], [], []
    monkeypatch.setattr(ui, "run", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(bootstrap, "register_pages", lambda **kwargs: registered.append(kwargs))
    from pixivvault_nicegui import native_startup
    monkeypatch.setattr(native_startup, "register_profile_recovery",
                        lambda binding, **kwargs: recovered.append((binding, kwargs)))
    return calls, recovered, registered


def test_first_native_no_saved_cookie_provisions_shared_auth_owner(tmp_path, monkeypatch):
    from pixivvault_nicegui import managed_network_stop_shared_scope as shared_module

    scope = _generated_shared_owner(tmp_path)
    monkeypatch.setattr(shared_module, "open_existing_windows_user_stop", lambda: scope)
    calls, recovered, registered = _stub_native(monkeypatch)
    profile_root = tmp_path / "generated-first-native-profile"
    store = NativeProfileStore(profile_root)
    store.close()
    assert bootstrap.main(["--native", "--profile-root", str(profile_root)]) == 0
    assert len(calls) == len(registered) == 1 and recovered == []
    binding = registered[0]["native_profile"]
    assert binding.auth_stop_required is True
    assert binding.auth_shared_stop_required is True
    assert binding.managed_network_stop_scope is registered[0]["managed_network_stop_scope"]
    assert binding.shared_network_stop_scope is scope
    assert registered[0]["shared_network_stop_scope"] is scope


def test_first_native_missing_shared_history_opens_offline_disabled_auth(tmp_path, monkeypatch):
    from pixivvault_nicegui import managed_network_stop_shared_scope as shared_module

    def unavailable():
        raise ManagedNetworkGateStorageError("generated history unverified")

    monkeypatch.setattr(shared_module, "open_existing_windows_user_stop", unavailable)
    calls, recovered, registered = _stub_native(monkeypatch)
    profile_root = tmp_path / "generated-no-shared-history"
    store = NativeProfileStore(profile_root)
    store.close()
    assert bootstrap.main(["--native", "--profile-root", str(profile_root)]) == 0
    assert len(calls) == len(recovered) == 1 and registered == []
    assert recovered[0][1] == {"stop_migration_required": True}


def test_first_profile_draft_cookie_auth_get_uses_both_stop_gates(tmp_path, monkeypatch):
    from pixivvault_nicegui.managed_network_stop_profile_scope import ManagedProfileNetworkStopScope
    from pixivvault_nicegui.native_startup import NativeProfileBinding
    from pixivvault_nicegui.pixiv_read_client import LivePixivReadClient, HTTPStatusRejected
    import pixivvault_nicegui.managed_network_stop_reader as reader_module

    shared = _generated_shared_owner(tmp_path)
    profile_root = tmp_path / "unconfigured-native"
    store = NativeProfileStore(profile_root)
    scope = ManagedProfileNetworkStopScope(profile_root)
    draft_folder = tmp_path / "draft-credential-only"
    draft_folder.mkdir()
    cookie = draft_folder / "synthetic-draft-cookie.txt"
    cookie.write_text(
        "# Netscape HTTP Cookie File\n"
        f".pixiv.net\tTRUE\t/\tTRUE\t{int(time.time()) + 86400}"
        "\tPHPSESSID\t55_generatedonly\n", encoding="utf-8",
    )
    calls = []

    class SyntheticOwnedTransport:
        revocation_unverified = False

        def __call__(self, request, *_):
            calls.append(request.get_method())
            if len(calls) == 2:
                raise HTTPStatusRejected(403)
            return b"generated image bytes"

        def close(self):
            pass

    monkeypatch.setattr(reader_module, "_OwnedReadTransport", SyntheticOwnedTransport)

    def synthetic_identity(self):
        assert self.get_image_bytes("https://i.pximg.net/generated/fictional.png") == b"generated image bytes"
        return "synthetic-only-account"

    monkeypatch.setattr(LivePixivReadClient, "require_identity", synthetic_identity)
    binding = NativeProfileBinding(store)
    binding.auth_stop_required = binding.auth_shared_stop_required = True
    binding.managed_network_stop_scope = scope
    binding.shared_network_stop_scope = shared
    scope.shared_owner = shared
    try:
        assert binding._verify_auth(cookie, Event()) is True
        assert calls == ["GET"]
        assert binding._verify_auth(cookie, Event()) is False
        assert calls == ["GET", "GET"]
        # The Native profile STOP was transferred to the shared user gate
        # before a second credential or a different CLI root can use it.
        assert shared.gate.snapshot().status_code == 403
        assert scope.gate.snapshot().status_code == 403
        assert binding._verify_auth(cookie, Event()) is False
        assert calls == ["GET", "GET"]
    finally:
        binding.close()


def test_native_historical_stop_rejection_enters_offline_recovery(tmp_path, monkeypatch):
    from pixivvault_nicegui import managed_network_stop_shared_scope as shared_module

    owner = _generated_shared_owner(tmp_path)
    monkeypatch.setattr(shared_module, "open_existing_windows_user_stop", lambda: owner)
    calls, recovery, registered = _stub_native(monkeypatch)

    def historical_root_rejected(**_kwargs):
        raise ManagedNetworkGateStorageError("generated historical stop needs review")

    monkeypatch.setattr(bootstrap, "register_pages", historical_root_rejected)
    profile = tmp_path / "generated-native-historical-stop"
    store = NativeProfileStore(profile)
    store.close()
    assert bootstrap.main(["--native", "--profile-root", str(profile)]) == 0
    assert len(calls) == len(recovery) == 1 and registered == []
    assert recovery[0][1] == {"stop_migration_required": True}
