"""Real ApplicationServices media-owner integration with a generated sealed stop.

No real credentials, existing roots, remote network, or OS-user owner is used.
"""

import hashlib
import json
import time

import pytest

from pixivvault_nicegui.managed_network_stop_gate import (
    ManagedNetworkStopGate, ManagedNetworkStopped, _file_identity,
)
from pixivvault_nicegui.managed_network_stop_shared_scope import (
    ManagedUserNetworkStopScope, _OWNER_FILE, _GATE_FILE,
    _OWNER_SCHEMA, _file_identity_directory,
)
from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected
from pixivvault_nicegui.managed_network_stop_profile_scope import ManagedProfileNetworkStopScope
from pixivvault_nicegui.native_profile import NativeProfileStore
from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkGateStorageError
from pixivvault_nicegui.managed_network_stop_shared_transport import create_shared_profile_reader
from pixivvault_nicegui.services import ApplicationServices


IMAGE = "https://i.pximg.net/generated/fictional.png"


def synthetic_owner(tmp_path):
    root = tmp_path / "synthetic-reviewed-user-stop"
    root.mkdir()
    gate = ManagedNetworkStopGate(root / _GATE_FILE)
    marker = {
        "schema": _OWNER_SCHEMA, "state": "sealed",
        "root_identity": list(_file_identity_directory(root)),
        "gate_identity": list(_file_identity(gate.path)),
        "lock_identity": list(_file_identity(gate.lock_path)),
        "history_proof": hashlib.sha256(b"generated-fixture-only-history").hexdigest(),
    }
    (root / _OWNER_FILE).write_text(json.dumps(marker), encoding="utf-8")
    credentials = tmp_path / "synthetic-credential"
    credentials.mkdir()
    cookie = credentials / "cookies.txt"
    cookie.write_text(
        "# Netscape HTTP Cookie File\n"
        f".pixiv.net\tTRUE\t/\tTRUE\t{int(time.time()) + 86400}"
        "\tPHPSESSID\t55_generatedonly\n", encoding="utf-8",
    )
    return root, cookie


@pytest.mark.parametrize("status", (401, 403, 429))
def test_direct_application_services_must_reject_native_only_reader_while_shared_stopped(
    tmp_path, status,
):
    shared_root, cookie = synthetic_owner(tmp_path)
    shared = ManagedUserNetworkStopScope(shared_root)
    shared.gate.trip(status)
    profile = tmp_path / "generated-native-profile"
    store = NativeProfileStore(profile)
    store.close()
    scope = ManagedProfileNetworkStopScope(profile)
    store = NativeProfileStore(profile)
    try:
        store.update(cookie_file=cookie, expected_revision=0)
    finally:
        store.close()

    calls = []

    def synthetic_transport(request, *_):
        calls.append(request.get_method())
        return b"UNSAFE generated GET"

    # The local profile STOP is explicitly clear, while a different Native or
    # Cookie-only process already made the Windows-user shared gate fatal.
    reader = scope.create_reader(cookie, transport=synthetic_transport)
    try:
        assert scope.gate.snapshot().blocked is False
        assert shared.gate.snapshot().status_code == status
        with pytest.raises(ManagedNetworkGateStorageError):
            ApplicationServices(
                live_client=reader,
                managed_network_stop_scope=scope,
                # Passing no shared owner MUST NOT authorize this reader.
                shared_network_stop_scope=None,
            )
        assert calls == [], "direct product service must not dispatch synthetic GET"
    finally:
        reader.close()


def test_direct_native_cannot_substitute_plain_gate_with_same_shared_path(tmp_path):
    shared_root, cookie = synthetic_owner(tmp_path)
    shared = ManagedUserNetworkStopScope(shared_root)
    profile = tmp_path / "generated-native"
    store = NativeProfileStore(profile)
    store.close()
    scope = ManagedProfileNetworkStopScope(profile)
    store = NativeProfileStore(profile)
    try:
        store.update(cookie_file=cookie, expected_revision=0)
    finally:
        store.close()
    scope.shared_owner = shared
    plain_gate = ManagedNetworkStopGate(shared.gate.path)
    calls = []
    reader = create_shared_profile_reader(
        scope, cookie, plain_gate,
        transport=lambda *_: calls.append("unfenced") or b"unsafe",
        shared_owner_check=shared._check_owner,
    )
    try:
        with pytest.raises(ValueError, match="shared STOP"):
            ApplicationServices(
                live_client=reader, managed_network_stop_scope=scope,
                shared_network_stop_scope=shared,
            )
        assert calls == []
    finally:
        reader.close()


def test_direct_cli_cannot_substitute_bare_callable_inside_sealed_wrapper(tmp_path):
    shared_root, cookie = synthetic_owner(tmp_path)
    shared = ManagedUserNetworkStopScope(shared_root)
    calls = []
    reader = shared.create_reader(cookie, transport=lambda *_: b"generated")
    try:
        # An arbitrary direct caller cannot claim the validated CLI wrapper
        # merely by retaining its scope metadata while replacing the gate.
        reader._transport.transport = lambda *_: calls.append("unfenced") or b"unsafe"
        with pytest.raises(ValueError, match="shared STOP"):
            ApplicationServices(live_client=reader, shared_network_stop_scope=shared)
        assert calls == []
    finally:
        reader.close()


@pytest.mark.parametrize("status", (401, 403, 429))
def test_actual_cli_media_services_queue_and_second_root_share_durable_stop(tmp_path, status):
    root, cookie = synthetic_owner(tmp_path)
    requests = []

    def synthetic_remote(request, *_):
        requests.append("generated GET")
        raise HTTPStatusRejected(status)

    first_scope = ManagedUserNetworkStopScope(root)
    first = first_scope.create_reader(cookie, transport=synthetic_remote)
    services = ApplicationServices(
        live_client=first,
        pixiv_download_root=tmp_path / "generated-image-root-a",
        shared_network_stop_scope=first_scope,
    )
    try:
        assert services.managed_network_stop is first_scope
        assert services.static_jobs._dispatch_admission(None) is True
        if status==429:
            from test_display_recovery_batch import assert_product_429
            assert_product_429((first,),first_scope.gate,requests,IMAGE)
            return
        with pytest.raises(ManagedNetworkStopped):
            first.get_image_bytes(IMAGE)
        assert requests == ["generated GET"]
        assert services.static_jobs._dispatch_admission(None) is False
        assert not first_scope.admit_job(None)
    finally:
        services.close()

    # The second application has a different media root and a newly reopened
    # sealed owner. Neither media-root selection nor restart clears the STOP.
    second_scope = ManagedUserNetworkStopScope(root)
    second = second_scope.create_reader(cookie, transport=synthetic_remote)
    second_services = ApplicationServices(
        live_client=second,
        pixiv_download_root=tmp_path / "generated-image-root-b",
        shared_network_stop_scope=second_scope,
    )
    try:
        assert not second_services.static_jobs._dispatch_admission(None)
        with pytest.raises(ManagedNetworkStopped):
            second.get_image_bytes(IMAGE)
        assert requests == ["generated GET"]
    finally:
        second_services.close()
