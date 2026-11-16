"""Generated-only replacement test for an already-open profile-scoped reader.

The native SQLite profile may be replaced at its original pathname while a
reader remains open. Its original credential/stop ownership must not silently
carry over to the replacement. No real profile, Cookie or HTTP is involved.
"""

import os
import shutil
import time

import pytest

from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkGateStorageError
from pixivvault_nicegui.managed_network_stop_profile_scope import (
    ManagedProfileNetworkStopScope, _SCOPE_MARKER,
)
from pixivvault_nicegui.native_profile import NativeProfileStore, PROFILE_DB
from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected


def test_open_profile_reader_rejects_replaced_native_profile_database(tmp_path):
    root = tmp_path / "synthetic-profile"
    store = NativeProfileStore(root)
    store.close()
    scope = ManagedProfileNetworkStopScope(root)

    credential_dir = tmp_path / "synthetic-credential"
    credential_dir.mkdir()
    credential = credential_dir / "synthetic-cookie.txt"
    credential.write_text(
        "# Netscape HTTP Cookie File\n"
        f".pixiv.net\tTRUE\t/\tTRUE\t{int(time.time()) + 86400}"
        "\tPHPSESSID\t55_generatedonly\n",
        encoding="utf-8",
    )
    store = NativeProfileStore(root)
    try:
        store.update(cookie_file=credential, expected_revision=0)
    finally:
        store.close()

    calls = []

    def transport(request, *_args):
        assert request.get_method() == "GET" and request.data is None
        calls.append("synthetic GET")
        raise HTTPStatusRejected(404)

    reader = scope.create_reader(credential, transport=transport)
    try:
        with pytest.raises(HTTPStatusRejected):
            reader.get_image_bytes("https://i.pximg.net/generated/fictional.png")
        assert calls == ["synthetic GET"]

        # Same bytes and same pathname, but a different OS file identity.
        # No profile SQLite connection is open while replacing this test copy.
        profile_db = root / PROFILE_DB
        replacement = root / "generated-profile-replacement.db"
        shutil.copyfile(profile_db, replacement)
        os.replace(replacement, profile_db)
        assert NativeProfileStore.peek(root).cookie_file == credential

        with pytest.raises(ManagedNetworkGateStorageError):
            reader.get_image_bytes("https://i.pximg.net/generated/fictional.png")
        assert calls == ["synthetic GET"]
        with pytest.raises(ManagedNetworkGateStorageError):
            scope.create_reader(credential, transport=transport)
        assert calls == ["synthetic GET"]
    finally:
        reader.close()


def test_scope_creation_refuses_native_database_swap_during_first_peek(
    tmp_path, monkeypatch,
):
    root = tmp_path / "synthetic-profile"
    store = NativeProfileStore(root)
    store.close()
    profile_db = root / PROFILE_DB
    original_peek = NativeProfileStore.peek
    swapped = False

    def peek_and_replace(folder):
        nonlocal swapped
        result = original_peek(folder)
        if not swapped:
            replacement = root / "generated-profile-replacement.db"
            shutil.copyfile(profile_db, replacement)
            os.replace(replacement, profile_db)
            swapped = True
        return result

    monkeypatch.setattr(NativeProfileStore, "peek", staticmethod(peek_and_replace))
    with pytest.raises(ManagedNetworkGateStorageError):
        ManagedProfileNetworkStopScope(root)
    assert swapped
    # Interrupted identity acquisition must not bless a newly unblocked gate.
    assert not (root / _SCOPE_MARKER).exists()
