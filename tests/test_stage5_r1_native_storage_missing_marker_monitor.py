"""Synthetic regression: a missing Native storage owner cannot mean success.

No user profile, credential, media, or remote network is accessed.  Only the
pytest-created directories under tmp_path are used.
"""

import time

import pytest

from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkGateStorageError
from pixivvault_nicegui.managed_network_stop_profile_scope import ManagedProfileNetworkStopScope
from pixivvault_nicegui.managed_network_stop_storage_binding import (
    bind_new_native_storage,
    prepare_native_storage_binding,
    require_native_storage_binding,
)
from pixivvault_nicegui.native_profile import NativeProfileStore, provision_storage
from pixivvault_nicegui.managed_network_stop_shared_transport import create_shared_profile_reader
from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopped
from pixivvault_nicegui.services import ApplicationServices
from stage5_generated_shared_owner import generated_shared_scope


@pytest.mark.parametrize("nested", (False, True))
def test_unmarked_storage_cannot_pass_native_binding_check(tmp_path, nested):
    profile = tmp_path / "generated-native-profile"
    store = NativeProfileStore(profile)
    store.close()
    scope = ManagedProfileNetworkStopScope(profile)

    unmarked = tmp_path / "generated-unowned-media"
    unmarked.mkdir()
    target = unmarked
    if nested:
        target = unmarked / "images" / "items"
        target.mkdir(parents=True)

    with pytest.raises(ManagedNetworkGateStorageError, match="association"):
        require_native_storage_binding(scope, target)

    assert not list(target.iterdir())


def test_real_service_cannot_adopt_unmarked_media_root(tmp_path):
    """Product construction must refuse before any possible synthetic GET."""
    credentials = tmp_path / "generated-credential"
    credentials.mkdir()
    cookie = credentials / "synthetic-cookie.txt"
    cookie.write_text(
        "# Netscape HTTP Cookie File\n"
        f".pixiv.net\tTRUE\t/\tTRUE\t{int(time.time())+86400}"
        "\tPHPSESSID\t55_generatedonly\n",
        encoding="utf-8",
    )
    profile = tmp_path / "generated-profile"
    store = NativeProfileStore(profile)
    store.close()
    scope = ManagedProfileNetworkStopScope(profile)
    store = NativeProfileStore(profile)
    try:
        store.update(cookie_file=cookie, expected_revision=0)
    finally:
        store.close()

    shared = generated_shared_scope(tmp_path)
    scope.shared_owner = shared
    calls = []
    reader = create_shared_profile_reader(
        scope, cookie, shared.gate,
        transport=lambda *_: calls.append("unexpected GET") or b"unsafe",
        shared_owner_check=shared._check_owner,
    )
    unmarked = tmp_path / "generated-media-without-owner"
    unmarked.mkdir()
    try:
        with pytest.raises(ManagedNetworkGateStorageError, match="association"):
            ApplicationServices(
                live_client=reader,
                pixiv_download_root=unmarked,
                managed_network_stop_scope=scope,
                shared_network_stop_scope=shared,
            )
    finally:
        reader.close()
    assert calls == []
    assert list(unmarked.iterdir()) == []


@pytest.mark.parametrize("status", (401, 403, 429))
def test_verified_native_storage_switch_preserves_stop(tmp_path, status):
    """A legitimate two-parent transition needs two explicit bindings."""
    credentials = tmp_path / "generated-credential"
    credentials.mkdir()
    cookie = credentials / "synthetic-cookie.txt"
    cookie.write_text(
        "# Netscape HTTP Cookie File\n"
        f".pixiv.net\tTRUE\t/\tTRUE\t{int(time.time())+86400}"
        "\tPHPSESSID\t55_generatedonly\n",
        encoding="utf-8",
    )
    profile = tmp_path / "generated-profile"
    store = NativeProfileStore(profile)
    store.close()
    scope = ManagedProfileNetworkStopScope(profile)
    shared = generated_shared_scope(tmp_path)
    scope.shared_owner = shared
    first = tmp_path / "generated-first-save"
    second = tmp_path / "generated-second-save"
    first.mkdir()
    second.mkdir()

    for revision, parent in enumerate((first, second)):
        store = NativeProfileStore(profile)
        try:
            if revision == 0:
                snapshot = store.update(
                    cookie_file=cookie, save_parent=parent, expected_revision=0,
                )
            else:
                snapshot = store.update(save_parent=parent, expected_revision=1)
        finally:
            store.close()
        prepare_native_storage_binding(scope, parent)
        roots = provision_storage(snapshot, profile)
        bind_new_native_storage(scope, parent)
        require_native_storage_binding(scope, roots["pixiv_download_root"])

        calls = []
        reader = create_shared_profile_reader(
            scope, cookie, shared.gate,
            transport=lambda *_: calls.append("unexpected GET") or b"unsafe",
            shared_owner_check=shared._check_owner,
        )
        services = ApplicationServices(
            live_client=reader, pixiv_download_root=roots["pixiv_download_root"],
            managed_network_stop_scope=scope, shared_network_stop_scope=shared,
        )
        try:
            if revision == 0:
                scope.gate.trip(status)
            assert services.static_jobs._dispatch_admission(None) is False
            with pytest.raises(ManagedNetworkStopped):
                reader.get_image_bytes("https://i.pximg.net/generated/fictional.png")
            assert calls == []
        finally:
            services.close()
