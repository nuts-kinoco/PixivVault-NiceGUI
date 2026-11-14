"""Generated-only sealed shared STOP owner and non-reset regressions.

The marker in this fixture is NOT an approval of any historical user profile.
No real Windows-user stop owner, Cookie, old root or network is touched.
"""

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from pixivvault_nicegui.managed_network_stop_gate import (
    ManagedNetworkGateStorageError, ManagedNetworkStopped,
    ManagedNetworkStopGate, _file_identity,
)
from pixivvault_nicegui.managed_network_stop_shared_scope import (
    ManagedUserNetworkStopScope,
    _OWNER_FILE, _GATE_FILE, _OWNER_SCHEMA, _file_identity_directory,
)
from pixivvault_nicegui.pixiv_read_client import (
    HTTPStatusRejected, ReadDeadlineExceeded, ClientClosed,
    ResponseRejected, ResponseLimitExceeded,
)
from pixivvault_nicegui.bootstrap import _build_owned_media_readers


IMAGE = "https://i.pximg.net/generated/fictional.png"


def generated_sealed_shared_owner(tmp_path):
    root = tmp_path / "generated-only-shared-user-owner"
    root.mkdir()
    database = root / _GATE_FILE
    gate = ManagedNetworkStopGate(database)
    cookie_dir = tmp_path / "generated-cookie"
    cookie_dir.mkdir()
    cookie = cookie_dir / "cookies.txt"
    cookie.write_text(
        "# Netscape HTTP Cookie File\n"
        f".pixiv.net\tTRUE\t/\tTRUE\t{int(time.time()) + 86400}"
        "\tPHPSESSID\t55_generatedonly\n", encoding="utf-8",
    )
    marker = {
        "schema": _OWNER_SCHEMA,
        "state": "sealed",
        "root_identity": list(_file_identity_directory(root)),
        "gate_identity": list(_file_identity(database)),
        "lock_identity": list(_file_identity(gate.lock_path)),
        "history_proof": hashlib.sha256(b"synthetic-no-legacy-profiles").hexdigest(),
    }
    (root / _OWNER_FILE).write_text(json.dumps(marker), encoding="utf-8")
    return root, cookie


@pytest.mark.parametrize("status", (401, 403, 429))
def test_sealed_owner_reopen_blocks_other_cli_reader_after_fatal_status(tmp_path, status):
    root, cookie = generated_sealed_shared_owner(tmp_path)
    shared = ManagedUserNetworkStopScope(root)
    calls = []

    def fatal(request, *_):
        calls.append("generated GET")
        raise HTTPStatusRejected(status)

    first = shared.create_reader(cookie, transport=fatal)
    try:
        with pytest.raises(ManagedNetworkStopped):
            first.get_image_bytes(IMAGE)
    finally:
        first.close()
    assert calls == ["generated GET"]
    other_root_reader = ManagedUserNetworkStopScope(root).create_reader(cookie, transport=fatal)
    try:
        with pytest.raises(ManagedNetworkStopped):
            other_root_reader.get_image_bytes(IMAGE)
        assert calls == ["generated GET"]
        assert not shared.admit_job(None)
    finally:
        other_root_reader.close()


def test_sealed_owner_does_not_initialize_when_both_stop_files_disappear(tmp_path):
    root, _ = generated_sealed_shared_owner(tmp_path)
    gate = root / _GATE_FILE
    lock = root / (_GATE_FILE + ".lock")
    gate.unlink()
    lock.unlink()
    with pytest.raises(ManagedNetworkGateStorageError):
        ManagedUserNetworkStopScope(root)
    assert not gate.exists() and not lock.exists()


def test_missing_or_unsealed_history_proof_refuses_before_gate_created(tmp_path):
    root = tmp_path / "generated-unverified"
    root.mkdir()
    with pytest.raises(ManagedNetworkGateStorageError):
        ManagedUserNetworkStopScope(root)
    assert list(root.iterdir()) == []
    (root / _OWNER_FILE).write_text(
        json.dumps({"schema": _OWNER_SCHEMA, "state": "pending", "history_proof": "0" * 64}),
        encoding="utf-8",
    )
    with pytest.raises(ManagedNetworkGateStorageError):
        ManagedUserNetworkStopScope(root)
    assert not (root / _GATE_FILE).exists()


def test_owner_marker_removed_after_reader_creation_denies_later_get(tmp_path):
    root, cookie = generated_sealed_shared_owner(tmp_path)
    scope = ManagedUserNetworkStopScope(root)
    requests = []
    reader = scope.create_reader(cookie, transport=lambda *_: requests.append("generated GET"))
    try:
        (root / _OWNER_FILE).unlink()
        with pytest.raises(ManagedNetworkGateStorageError):
            reader.get_image_bytes(IMAGE)
        assert requests == []
    finally:
        reader.close()


@pytest.mark.parametrize("status", (401, 403, 429))
def test_shared_fatal_commit_failure_stays_blocked_for_next_cli_before_native_transfer(
    tmp_path, monkeypatch, status,
):
    root, cookie = generated_sealed_shared_owner(tmp_path)
    owner = ManagedUserNetworkStopScope(root)
    calls = []

    def fatal(request, *_):
        calls.append("first generated GET")
        raise HTTPStatusRejected(status)

    first = owner.create_reader(cookie, transport=fatal)
    with monkeypatch.context() as broken:
        broken.setattr(
            owner.gate, "_trip_locked",
            lambda *_: (_ for _ in ()).throw(
                ManagedNetworkGateStorageError("synthetic shared commit failure")
            ),
        )
        try:
            with pytest.raises(ManagedNetworkGateStorageError):
                first.get_image_bytes(IMAGE)
        finally:
            first.close()
    assert calls == ["first generated GET"]
    # No Native reader is allowed to copy its local STOP into shared here.
    # The separate CLI is a newly reopened owner immediately after recovery.
    second = ManagedUserNetworkStopScope(root).create_reader(
        cookie, transport=lambda *_: calls.append("UNSAFE second GET") or b"unsafe",
    )
    try:
        with pytest.raises(ManagedNetworkGateStorageError):
            second.get_image_bytes(IMAGE)
        assert calls == ["first generated GET"]
    finally:
        second.close()


def test_shared_inflight_crash_marker_blocks_reopen_without_reset(tmp_path):
    root, cookie = generated_sealed_shared_owner(tmp_path)
    marker = root / ".pixivvault-user-stop-inflight.v1"
    marker.write_bytes(b"synthetic incomplete GET; do not erase")
    owner = ManagedUserNetworkStopScope(root)
    calls = []
    reader = owner.create_reader(cookie, transport=lambda *_: calls.append("GET") or b"unsafe")
    try:
        with pytest.raises(ManagedNetworkGateStorageError):
            reader.get_image_bytes(IMAGE)
        assert calls == []
        assert marker.is_file()
    finally:
        reader.close()


def test_successful_shared_cli_get_releases_inflight_marker(tmp_path):
    root, cookie = generated_sealed_shared_owner(tmp_path)
    owner = ManagedUserNetworkStopScope(root)
    calls = []
    reader = owner.create_reader(
        cookie, transport=lambda request, *_: calls.append(request.get_method()) or b"generated",
    )
    try:
        assert reader.get_image_bytes(IMAGE) == b"generated"
        assert calls == ["GET"]
        assert not (root / ".pixivvault-user-stop-inflight.v1").exists()
        assert owner.admit_job(None) is True
    finally:
        reader.close()


def test_known_nonfatal_http_failure_releases_inflight_for_bounded_retry(tmp_path):
    root, cookie = generated_sealed_shared_owner(tmp_path)
    scope = ManagedUserNetworkStopScope(root)
    calls = []

    def not_found(request, *_):
        calls.append(request.get_method())
        raise HTTPStatusRejected(404)

    reader = scope.create_reader(cookie, transport=not_found)
    try:
        with pytest.raises(HTTPStatusRejected) as raised:
            reader.get_image_bytes(IMAGE)
        assert raised.value.status_code == 404
        assert calls == ["GET"]
        assert scope.admit_job(None) is True
        assert not (root / ".pixivvault-user-stop-inflight.v1").exists()
    finally:
        reader.close()


@pytest.mark.parametrize("error_type", (
    ReadDeadlineExceeded, ClientClosed, ResponseRejected, ResponseLimitExceeded,
))
def test_nonfatal_owned_read_errors_do_not_permanently_poison_shared_owner(
    tmp_path, error_type,
):
    root, cookie = generated_sealed_shared_owner(tmp_path)
    scope = ManagedUserNetworkStopScope(root)
    calls = []

    def fail_once(request, *_):
        calls.append("generated failed GET")
        raise error_type()

    reader = scope.create_reader(cookie, transport=fail_once)
    try:
        with pytest.raises(error_type):
            reader.get_image_bytes(IMAGE)
        assert not (root / ".pixivvault-user-stop-inflight.v1").exists()
        assert scope.admit_job(None) is True
    finally:
        reader.close()

    restarted = ManagedUserNetworkStopScope(root).create_reader(
        cookie, transport=lambda *_: calls.append("generated later GET") or b"generated",
    )
    try:
        assert restarted.get_image_bytes(IMAGE) == b"generated"
        assert calls == ["generated failed GET", "generated later GET"]
    finally:
        restarted.close()


def test_concurrent_shared_cli_get_defers_without_second_network_dispatch(tmp_path):
    root, cookie = generated_sealed_shared_owner(tmp_path)
    scope = ManagedUserNetworkStopScope(root)
    entered, released = Event(), Event()
    calls = []

    def slow(request, *_):
        calls.append("first synthetic GET")
        entered.set()
        assert released.wait(10)
        return b"generated"

    first = scope.create_reader(cookie, transport=slow)
    second = scope.create_reader(cookie, transport=lambda *_: calls.append("UNSAFE second") or b"unsafe")
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(first.get_image_bytes, IMAGE)
            try:
                assert entered.wait(10)
                assert (root / ".pixivvault-user-stop-inflight.v1").exists()
                with pytest.raises(ManagedNetworkGateStorageError):
                    second.get_image_bytes(IMAGE)
                assert calls == ["first synthetic GET"]
            finally:
                released.set()
            assert future.result(timeout=10) == b"generated"
        assert not (root / ".pixivvault-user-stop-inflight.v1").exists()
        assert scope.admit_job(None) is True
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize("status", (401, 403, 429))
@pytest.mark.parametrize("root_mode", ("cookie-only", "different-media-root"))
def test_real_bootstrap_cli_factory_honors_reviewed_shared_stop_across_roots(
    tmp_path, monkeypatch, status, root_mode,
):
    root, cookie = generated_sealed_shared_owner(tmp_path)
    shared = ManagedUserNetworkStopScope(root)
    # Synthetic receipt of a previously observed profile stop, not a claim
    # that a real user's unknown old profile history has been migrated.
    shared.gate.trip(status)
    calls = []

    class SyntheticOwnedTransport:
        revocation_unverified = False

        def __call__(self, request, *_):
            calls.append("synthetic GET")
            return b"generated image"

        def close(self):
            pass

    import pixivvault_nicegui.managed_network_stop_reader as reader_module
    monkeypatch.setattr(reader_module, "_OwnedReadTransport", SyntheticOwnedTransport)
    client, novel, _, ugoira, _ = _build_owned_media_readers(
        pixiv_cookie_file=cookie,
        pixiv_download_root=(tmp_path / "other-generated-media") if root_mode != "cookie-only" else None,
        novel_download_root=None, novel_fixture_root=None, novel_source=None,
        ugoira_root=None, ugoira_fixture_root=None, live_client=None,
        managed_network_stop_scope=None, shared_network_stop_scope=shared,
    )
    try:
        assert novel is None and ugoira is None
        with pytest.raises(ManagedNetworkStopped):
            client.get_image_bytes(IMAGE)
        assert calls == []
    finally:
        client.close()


def test_reviewed_unblocked_shared_owner_retains_cookie_only_cli_get(tmp_path, monkeypatch):
    root, cookie = generated_sealed_shared_owner(tmp_path)
    shared = ManagedUserNetworkStopScope(root)
    calls = []

    class SyntheticOwnedTransport:
        revocation_unverified = False

        def __call__(self, request, *_):
            calls.append(request.get_method())
            return b"generated image"

        def close(self):
            pass

    import pixivvault_nicegui.managed_network_stop_reader as reader_module
    monkeypatch.setattr(reader_module, "_OwnedReadTransport", SyntheticOwnedTransport)
    client, _, _, _, _ = _build_owned_media_readers(
        pixiv_cookie_file=cookie, pixiv_download_root=None,
        novel_download_root=None, novel_fixture_root=None, novel_source=None,
        ugoira_root=None, ugoira_fixture_root=None, live_client=None,
        managed_network_stop_scope=None, shared_network_stop_scope=shared,
    )
    try:
        assert client.get_image_bytes(IMAGE) == b"generated image"
        assert calls == ["GET"]
    finally:
        client.close()


def test_register_pages_refuses_historical_root_stop_before_any_writer(tmp_path, monkeypatch):
    import pixivvault_nicegui.bootstrap as bootstrap
    root, cookie = generated_sealed_shared_owner(tmp_path)
    shared = ManagedUserNetworkStopScope(root)
    media = tmp_path / "historical-generated-media"
    media.mkdir()
    old_gate = media / ".pixivvault-network-stop.sqlite3"
    old_gate.write_bytes(b"synthetic historical state - must remain unchanged")
    before = old_gate.read_bytes()
    monkeypatch.setattr(bootstrap, "_registered", False)
    with pytest.raises(ManagedNetworkGateStorageError):
        bootstrap.register_pages(
            pixiv_cookie_file=cookie,
            pixiv_download_root=media,
            shared_network_stop_scope=shared,
        )
    assert old_gate.read_bytes() == before
    assert not (media / ".pixivvault-managed-downloads.sqlite3").exists()
