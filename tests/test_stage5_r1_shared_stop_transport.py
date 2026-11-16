"""Offline two-owner STOP adapter tests, without OS-user bootstrap authority.

These pass only for explicitly supplied and independently owned synthetic
shared/profile gates; unknown historic user/profile migration is not tested.
"""

import time
from urllib.error import HTTPError

import pytest

from pixivvault_nicegui.managed_network_stop_gate import (
    ManagedNetworkGateStorageError, ManagedNetworkStopGate, ManagedNetworkStopped,
)
from pixivvault_nicegui.managed_network_stop_profile_scope import ManagedProfileNetworkStopScope
from pixivvault_nicegui.managed_network_stop_reader import create_exact_managed_read_client
from pixivvault_nicegui.managed_network_stop_transport import ManagedNetworkStopTransport
from pixivvault_nicegui.managed_network_stop_shared_transport import (
    SharedProfileStopTransport,
    create_shared_profile_reader,
)
from pixivvault_nicegui.native_profile import NativeProfileStore
from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected


IMAGE = "https://i.pximg.net/generated/fictional.png"


def generated_owners(tmp_path):
    credentials = tmp_path / "generated-credential"
    credentials.mkdir()
    cookie = credentials / "synthetic-cookie.txt"
    cookie.write_text(
        "# Netscape HTTP Cookie File\n"
        f".pixiv.net\tTRUE\t/\tTRUE\t{int(time.time())+86400}"
        "\tPHPSESSID\t55_generatedonly\n", encoding="utf-8",
    )
    root = tmp_path / "generated-native-profile"
    store = NativeProfileStore(root)
    store.close()
    scope = ManagedProfileNetworkStopScope(root)
    store = NativeProfileStore(root)
    try:
        store.update(cookie_file=cookie, expected_revision=0)
    finally:
        store.close()
    shared_path = tmp_path / "generated-shared-gate" / "stop.sqlite3"
    shared = ManagedNetworkStopGate(shared_path)
    return scope, shared, shared_path, cookie


@pytest.mark.parametrize("status", (401, 403, 429))
def test_native_http_stop_commits_shared_first_then_blocks_new_cli_root(
    tmp_path, monkeypatch, status,
):
    scope, shared, shared_path, cookie = generated_owners(tmp_path)
    calls = []

    def transport(request, *_):
        calls.append("generated GET")
        raise HTTPStatusRejected(status)

    native = create_shared_profile_reader(scope, cookie, shared, transport=transport)
    assert isinstance(native._transport, SharedProfileStopTransport)
    assert native._transport.transport is transport, "must use raw transport, not the nested local wrapper"
    try:
        with pytest.raises(ManagedNetworkStopped):
            native.get_image_bytes(IMAGE)
        assert calls == ["generated GET"]
        assert shared.snapshot().status_code == status
        assert scope.gate.snapshot().status_code == status
    finally:
        native.close()

    # Fresh process/CLI-style reader with a distinct media directory but same
    # explicitly supplied durable shared gate must not dispatch any GET.
    restarted_gate = ManagedNetworkStopGate(shared_path)
    new_cli = create_exact_managed_read_client(cookie, restarted_gate, transport=transport)
    try:
        with pytest.raises(ManagedNetworkStopped):
            new_cli.get_image_bytes(IMAGE)
        assert calls == ["generated GET"]
    finally:
        new_cli.close()


def test_raw_urllib_403_is_latched_in_both_owned_scopes(tmp_path):
    scope, shared, _, cookie = generated_owners(tmp_path)
    calls = []

    def raw_http_error(request, *_):
        calls.append("synthetic GET")
        raise HTTPError(request.full_url, 403, "synthetic", {}, None)

    reader = create_shared_profile_reader(scope, cookie, shared, transport=raw_http_error)
    try:
        with pytest.raises(ManagedNetworkStopped):
            reader.get_image_bytes(IMAGE)
        assert calls == ["synthetic GET"]
        assert shared.snapshot().status_code == 403
        assert scope.gate.snapshot().status_code == 403
    finally:
        reader.close()


def test_shared_stop_survives_local_commit_failure(tmp_path, monkeypatch):
    scope, shared, shared_path, cookie = generated_owners(tmp_path)
    calls = []

    def fatal(request, *_):
        calls.append("generated GET")
        raise HTTPStatusRejected(429)

    reader = create_shared_profile_reader(scope, cookie, shared, transport=fatal)
    monkeypatch.setattr(
        scope.gate, "_trip_locked",
        lambda *_: (_ for _ in ()).throw(
            ManagedNetworkGateStorageError("synthetic local commit interrupted")
        ),
    )
    try:
        with pytest.raises(ManagedNetworkGateStorageError):
            reader.get_image_bytes(IMAGE)
        assert calls == ["generated GET"]
        assert ManagedNetworkStopGate(shared_path).snapshot().status_code == 429
        cli_reader = create_exact_managed_read_client(cookie, shared, transport=fatal)
        try:
            with pytest.raises(ManagedNetworkStopped):
                cli_reader.get_image_bytes(IMAGE)
            assert calls == ["generated GET"]
        finally:
            cli_reader.close()
    finally:
        reader.close()


@pytest.mark.parametrize("status", (401, 403, 429))
def test_shared_commit_failure_must_still_latch_local_before_unlock(
    tmp_path, monkeypatch, status,
):
    scope, shared, shared_path, cookie = generated_owners(tmp_path)
    calls = []

    def fatal(request, *_):
        assert request.get_method() == "GET"
        calls.append("synthetic GET")
        raise HTTPStatusRejected(status)

    reader = create_shared_profile_reader(scope, cookie, shared, transport=fatal)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(
                shared, "_trip_locked",
                lambda *_: (_ for _ in ()).throw(
                    ManagedNetworkGateStorageError("synthetic shared commit unavailable")
                ),
            )
            with pytest.raises(ManagedNetworkGateStorageError):
                reader.get_image_bytes(IMAGE)
        assert calls == ["synthetic GET"]
        assert scope.gate.snapshot().status_code == status
        assert shared.snapshot().status_code is None

        # A previously opened local-only Native reader is denied after the
        # shared disk fault. Once shared storage recovers, the two-owner reader
        # must transfer this known local STOP, still without another GET.
        local_only = scope.create_reader(cookie, transport=fatal)
        try:
            with pytest.raises(ManagedNetworkStopped):
                local_only.get_image_bytes(IMAGE)
        finally:
            local_only.close()
        with pytest.raises(ManagedNetworkStopped):
            reader.get_image_bytes(IMAGE)
        assert ManagedNetworkStopGate(shared_path).snapshot().status_code == status
        assert calls == ["synthetic GET"]
    finally:
        reader.close()

@pytest.mark.parametrize("status", (401, 403, 429))
def test_known_native_history_is_latched_before_shared_transport_is_available(tmp_path, status):
    scope, shared, shared_path, cookie = generated_owners(tmp_path)
    scope.gate.trip(status)
    calls = []
    native = create_shared_profile_reader(
        scope, cookie, shared, transport=lambda *_: calls.append("unexpected")
    )
    try:
        assert shared.snapshot().status_code == status
        with pytest.raises(ManagedNetworkStopped):
            native.get_image_bytes(IMAGE)
        assert calls == []
        assert ManagedNetworkStopGate(shared_path).snapshot().blocked
    finally:
        native.close()


def test_pre_fenced_injected_transport_cannot_skip_the_shared_stop(tmp_path):
    scope, shared, _, cookie = generated_owners(tmp_path)
    calls = []

    def fatal(request, *_):
        calls.append("synthetic GET")
        raise HTTPStatusRejected(429)

    # The local wrapper would translate 429 into ManagedNetworkStopped before
    # the outer shared gate sees HTTPStatusRejected. Reject such a reader
    # before publishing it instead of making a false shared-STOP guarantee.
    pre_fenced = ManagedNetworkStopTransport(scope.gate, fatal)
    with pytest.raises(ManagedNetworkGateStorageError):
        create_shared_profile_reader(scope, cookie, shared, transport=pre_fenced)
    assert calls == []


def test_local_stop_after_reader_creation_must_reach_shared_gate(tmp_path):
    scope, shared, shared_path, cookie = generated_owners(tmp_path)
    calls = []

    def unexpected(request, *_):
        calls.append("unexpected synthetic GET")
        return b"never dispatch"

    native = create_shared_profile_reader(scope, cookie, shared, transport=unexpected)
    try:
        # A separate already-open local-only reader may have completed a
        # fatal GET after the shared reader was created. This reader must
        # propagate the known local stop before a different CLI reader runs.
        scope.gate.trip(429)
        assert shared.snapshot().blocked is False
        with pytest.raises(ManagedNetworkStopped):
            native.get_image_bytes(IMAGE)
        assert shared.snapshot().status_code == 429
        assert ManagedNetworkStopGate(shared_path).snapshot().status_code == 429
        assert calls == []
    finally:
        native.close()


@pytest.mark.parametrize("status", (401, 403, 429))
def test_raw_callable_direct_stop_durably_blocks_both_local_and_shared_next_gets(
    tmp_path, status,
):
    scope, shared, shared_path, cookie = generated_owners(tmp_path)
    calls = []

    def direct_stopped(request, *_):
        assert request.get_method() == "GET" and request.data is None
        calls.append("generated GET")
        raise ManagedNetworkStopped(status)

    reader = create_shared_profile_reader(scope, cookie, shared, transport=direct_stopped)
    try:
        with pytest.raises(ManagedNetworkStopped):
            reader.get_image_bytes(IMAGE)
        assert calls == ["generated GET"]
        assert shared.snapshot().status_code == status
        assert scope.gate.snapshot().status_code == status
    finally:
        reader.close()

    # Independently reconstructed readers still observe the persisted STOP.
    local_reader = scope.create_reader(cookie, transport=direct_stopped)
    cli_reader = create_exact_managed_read_client(
        cookie, ManagedNetworkStopGate(shared_path), transport=direct_stopped,
    )
    try:
        with pytest.raises(ManagedNetworkStopped):
            local_reader.get_image_bytes(IMAGE)
        with pytest.raises(ManagedNetworkStopped):
            cli_reader.get_image_bytes(IMAGE)
        assert calls == ["generated GET"]
    finally:
        local_reader.close()
        cli_reader.close()
