"""Generated-only contract for a stable, strictly new native-profile GET gate.

Never uses a real Cookie, browser, network transport, or existing profile.
This tests an additive capability, NOT product startup wiring or old-profile
migration, which are separate blocked acceptance requirements.
"""

import time

import pytest

from pixivvault_nicegui.managed_network_stop_gate import (
    ManagedNetworkGateStorageError, ManagedNetworkStopped,
)
from pixivvault_nicegui.managed_network_stop_profile_scope import (
    ManagedProfileNetworkStopScope,
    _GATE_NAME, _SCOPE_IDENTITY, _SCOPE_MARKER,
)
from pixivvault_nicegui.native_profile import NativeProfileStore
from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected, LivePixivReadClient


IMAGE = "https://i.pximg.net/generated/fictional.png"
TRANSITIONS = (
    ("image_novel_to_novel", ("images", "novel"), ("novel",)),
    ("novel_ugoira_to_ugoira", ("novel", "ugoira"), ("ugoira",)),
    ("ugoira_to_novel_ugoira", ("ugoira",), ("novel", "ugoira")),
    ("all_to_ugoira", ("images", "novel", "ugoira"), ("ugoira",)),
)


def _profile(tmp_path):
    root = tmp_path / "generated-profile"
    store = NativeProfileStore(root)
    store.close()
    return root


def _cookie(tmp_path):
    container = tmp_path / "generated-credential"
    container.mkdir()
    path = container / "generated-cookie.txt"
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        f".pixiv.net\tTRUE\t/\tTRUE\t{int(time.time()) + 86400}"
        "\tPHPSESSID\t55_generatedonly\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("status", (401, 403, 429))
@pytest.mark.parametrize("label,before,after", TRANSITIONS,
                         ids=[t[0] for t in TRANSITIONS])
def test_one_owned_profile_gate_survives_root_mode_switch(
    tmp_path, status, label, before, after
):
    profile = _profile(tmp_path)
    cookie = _cookie(tmp_path)
    calls = []

    def transport(request, *_args):
        assert request.get_method() == "GET" and request.data is None
        calls.append("synthetic-get")
        raise HTTPStatusRejected(status)

    first = ManagedProfileNetworkStopScope(profile)
    assert first.gate.path.parent == profile
    first_parent = tmp_path / "generated-first-save"
    second_parent = tmp_path / "generated-second-save"
    first_parent.mkdir()
    second_parent.mkdir()
    store = NativeProfileStore(profile)
    try:
        # The synthetic profile now actually records a credential and save
        # parent; the later launch changes its selected parent and revision.
        assert store.update(cookie_file=cookie, save_parent=first_parent,
                            expected_revision=0).revision == 1
    finally:
        store.close()
    first_readers = {
        name: first.create_reader(cookie, transport=transport) for name in before
    }
    try:
        assert all(type(value) is LivePixivReadClient
                   for value in first_readers.values())
        with pytest.raises(ManagedNetworkStopped):
            next(iter(first_readers.values())).get_image_bytes(IMAGE)
        assert calls == ["synthetic-get"]
    finally:
        for reader in first_readers.values():
            reader.close()

    store = NativeProfileStore(profile)
    try:
        assert store.update(save_parent=second_parent,
                            expected_revision=1).revision == 2
    finally:
        store.close()
    assert NativeProfileStore.peek(profile).save_parent == second_parent

    second = ManagedProfileNetworkStopScope(profile)
    assert first.gate.path == second.gate.path
    assert second.gate.snapshot().status_code == status
    second_readers = {
        name: second.create_reader(cookie, transport=transport) for name in after
    }
    try:
        for reader in second_readers.values():
            with pytest.raises(ManagedNetworkStopped):
                reader.get_image_bytes(IMAGE)
        assert calls == ["synthetic-get"], label
    finally:
        for reader in second_readers.values():
            reader.close()


def test_unowned_profile_never_creates_stop_files(tmp_path):
    root = tmp_path / "foreign-profile"
    root.mkdir()
    (root / "foreign.txt").write_text("generated", encoding="utf-8")
    before = sorted(path.name for path in root.iterdir())
    with pytest.raises(ManagedNetworkGateStorageError):
        ManagedProfileNetworkStopScope(root)
    assert sorted(path.name for path in root.iterdir()) == before


def test_configured_profile_without_migration_refuses_fresh_gate(tmp_path):
    root = _profile(tmp_path)
    parent = tmp_path / "generated-save-parent"
    parent.mkdir()
    store = NativeProfileStore(root)
    try:
        assert store.update(save_parent=parent, expected_revision=0).revision == 1
    finally:
        store.close()
    with pytest.raises(ManagedNetworkGateStorageError, match="migration"):
        ManagedProfileNetworkStopScope(root)
    assert not (root / _SCOPE_MARKER).exists()
    assert not (root / _GATE_NAME).exists()


def test_profile_configured_during_first_scope_creation_fails_closed(tmp_path, monkeypatch):
    """A revision-zero peek must not authorize a scope after settings change.

    This deterministic interleaving simulates a separate settings writer that
    commits immediately after the creator's initial profile inspection.
    """
    root = _profile(tmp_path)
    parent = tmp_path / "generated-new-save-parent"
    parent.mkdir()
    original_peek = NativeProfileStore.peek
    updated = False

    def peek_with_concurrent_update(folder):
        nonlocal updated
        snapshot = original_peek(folder)
        if not updated:
            updated = True
            writer = NativeProfileStore(root)
            try:
                assert writer.update(save_parent=parent,
                                     expected_revision=0).revision == 1
            finally:
                writer.close()
        return snapshot

    monkeypatch.setattr(NativeProfileStore, "peek",
                        staticmethod(peek_with_concurrent_update))
    with pytest.raises(ManagedNetworkGateStorageError):
        ManagedProfileNetworkStopScope(root)
    assert updated
    assert NativeProfileStore.peek(root).revision == 1
    assert not (root / _GATE_NAME).exists()
    # A half-created marker is intentionally not removed or retried: the
    # next startup must not silently create an unblocked historical scope.
    with pytest.raises(ManagedNetworkGateStorageError):
        ManagedProfileNetworkStopScope(root)


@pytest.mark.parametrize("missing", ("gate", "lock", "both"))
def test_lost_gate_or_lock_never_reinitializes_a_scope(tmp_path, missing):
    root = _profile(tmp_path)
    ManagedProfileNetworkStopScope(root)
    for path in (
        (root / _GATE_NAME,) if missing == "gate"
        else (root / (_GATE_NAME + ".lock"),) if missing == "lock"
        else (root / _GATE_NAME, root / (_GATE_NAME + ".lock"))
    ):
        path.unlink()
    with pytest.raises(ManagedNetworkGateStorageError):
        ManagedProfileNetworkStopScope(root)
    assert (root / _SCOPE_MARKER).read_bytes() == _SCOPE_IDENTITY
    if missing in ("gate", "both"):
        assert not (root / _GATE_NAME).exists()


def test_interrupted_first_creation_fails_closed(tmp_path):
    root = _profile(tmp_path)
    (root / _SCOPE_MARKER).write_bytes(_SCOPE_IDENTITY)
    with pytest.raises(ManagedNetworkGateStorageError):
        ManagedProfileNetworkStopScope(root)
    assert not (root / _GATE_NAME).exists()


def test_deleted_scope_identity_with_remaining_database_fails_closed(tmp_path):
    root = _profile(tmp_path)
    first = ManagedProfileNetworkStopScope(root)
    first.gate.trip(403)
    (root / _SCOPE_MARKER).unlink()
    with pytest.raises(ManagedNetworkGateStorageError):
        ManagedProfileNetworkStopScope(root)
    assert first.gate.snapshot().status_code == 403


def test_profile_marker_tamper_prevents_reopen(tmp_path):
    root = _profile(tmp_path)
    first = ManagedProfileNetworkStopScope(root)
    first.gate.trip(429)
    (root / _SCOPE_MARKER).write_text("forged", encoding="utf-8")
    with pytest.raises(ManagedNetworkGateStorageError):
        ManagedProfileNetworkStopScope(root)
    assert first.gate.snapshot().blocked


def test_revision_zero_profile_cannot_open_a_live_reader(tmp_path):
    profile = _profile(tmp_path)
    cookie = _cookie(tmp_path)
    scope = ManagedProfileNetworkStopScope(profile)
    with pytest.raises(ManagedNetworkGateStorageError):
        scope.create_reader(cookie, transport=lambda *_: pytest.fail("unexpected GET"))


def test_profile_reader_rejects_unselected_cookie_before_construction(tmp_path):
    profile = _profile(tmp_path)
    cookie = _cookie(tmp_path)
    other = cookie.with_name("other-generated-cookie.txt")
    other.write_bytes(cookie.read_bytes())
    scope = ManagedProfileNetworkStopScope(profile)
    store = NativeProfileStore(profile)
    try:
        store.update(cookie_file=cookie, expected_revision=0)
    finally:
        store.close()
    with pytest.raises(ManagedNetworkGateStorageError):
        scope.create_reader(other, transport=lambda *_: pytest.fail("unexpected GET"))


def test_profile_reader_rechecks_cookie_selection_before_every_get(tmp_path):
    profile = _profile(tmp_path)
    cookie = _cookie(tmp_path)
    other = cookie.with_name("other-generated-cookie.txt")
    other.write_bytes(cookie.read_bytes())
    scope = ManagedProfileNetworkStopScope(profile)
    store = NativeProfileStore(profile)
    try:
        store.update(cookie_file=cookie, expected_revision=0)
    finally:
        store.close()
    calls = []

    def transport(*_args):
        calls.append("synthetic-get")
        raise HTTPStatusRejected(404)

    reader = scope.create_reader(cookie, transport=transport)
    try:
        with pytest.raises(HTTPStatusRejected) as original:
            reader.get_image_bytes(IMAGE)
        assert original.value.status_code == 404
        assert calls == ["synthetic-get"]

        store = NativeProfileStore(profile)
        try:
            store.update(cookie_file=other, expected_revision=1)
        finally:
            store.close()

        # An unrelated admission callback cannot remove profile binding.
        reader.set_request_admission(lambda _deadline: None)
        with pytest.raises(ManagedNetworkGateStorageError):
            reader.get_image_bytes(IMAGE)
        assert calls == ["synthetic-get"]
    finally:
        reader.close()


def test_profile_reader_survives_save_parent_only_change(tmp_path):
    profile = _profile(tmp_path)
    cookie = _cookie(tmp_path)
    first_parent = tmp_path / "generated-first-save"
    second_parent = tmp_path / "generated-second-save"
    first_parent.mkdir()
    second_parent.mkdir()
    scope = ManagedProfileNetworkStopScope(profile)
    store = NativeProfileStore(profile)
    try:
        store.update(cookie_file=cookie, save_parent=first_parent,
                     expected_revision=0)
    finally:
        store.close()
    calls = []

    def transport(*_args):
        calls.append("synthetic-get")
        raise HTTPStatusRejected(404)

    reader = scope.create_reader(cookie, transport=transport)
    try:
        store = NativeProfileStore(profile)
        try:
            store.update(save_parent=second_parent, expected_revision=1)
        finally:
            store.close()
        with pytest.raises(HTTPStatusRejected) as result:
            reader.get_image_bytes(IMAGE)
        assert result.value.status_code == 404
        assert calls == ["synthetic-get"]
    finally:
        reader.close()


def test_reader_detects_profile_marker_tamper_before_get(tmp_path):
    profile = _profile(tmp_path)
    cookie = _cookie(tmp_path)
    scope = ManagedProfileNetworkStopScope(profile)
    store = NativeProfileStore(profile)
    try:
        store.update(cookie_file=cookie, expected_revision=0)
    finally:
        store.close()
    calls = []
    reader = scope.create_reader(cookie, transport=lambda *_: calls.append("GET"))
    try:
        (profile / ".pixivvault-native-profile.json").write_text(
            "tampered", encoding="utf-8")
        with pytest.raises(ManagedNetworkGateStorageError):
            reader.get_image_bytes(IMAGE)
        assert calls == []
    finally:
        reader.close()


@pytest.mark.parametrize("mutation", ("remove", "corrupt"))
def test_open_reader_rechecks_stop_scope_identity_before_each_get(tmp_path, mutation):
    """A reader opened earlier must not outlive its network-stop owner."""
    profile = _profile(tmp_path)
    cookie = _cookie(tmp_path)
    scope = ManagedProfileNetworkStopScope(profile)
    store = NativeProfileStore(profile)
    try:
        store.update(cookie_file=cookie, expected_revision=0)
    finally:
        store.close()
    calls = []

    def transport(*_args):
        calls.append("synthetic-get")
        raise HTTPStatusRejected(404)

    reader = scope.create_reader(cookie, transport=transport)
    try:
        identity = profile / _SCOPE_MARKER
        if mutation == "remove":
            identity.unlink()
        else:
            identity.write_text("corrupted", encoding="utf-8")
        with pytest.raises(ManagedNetworkGateStorageError):
            reader.get_image_bytes(IMAGE)
        assert calls == []
    finally:
        reader.close()


@pytest.mark.parametrize("lost", ("gate", "lock"))
def test_open_reader_refuses_lost_durable_gate_files(tmp_path, lost):
    """Losing an established stop latch must not recreate/admit on a live reader."""
    profile = _profile(tmp_path)
    cookie = _cookie(tmp_path)
    scope = ManagedProfileNetworkStopScope(profile)
    store = NativeProfileStore(profile)
    try:
        store.update(cookie_file=cookie, expected_revision=0)
    finally:
        store.close()

    calls = []

    def transport(*_args):
        calls.append("synthetic-get")
        raise HTTPStatusRejected(404)

    reader = scope.create_reader(cookie, transport=transport)
    try:
        path = profile / (_GATE_NAME if lost == "gate" else _GATE_NAME + ".lock")
        path.unlink()
        with pytest.raises(ManagedNetworkGateStorageError):
            reader.get_image_bytes(IMAGE)
        assert calls == []
        assert not path.exists(), "GET must not recreate a missing lock or gate"
    finally:
        reader.close()
