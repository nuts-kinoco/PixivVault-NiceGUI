"""A new native profile must not adopt an old root with stop history.

All paths, Cookies and transports are generated inside tmp_path.
"""

import time

import pytest

from pixivvault_nicegui.managed_downloads import ManagedPixivDownloads
from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkGateStorageError
from pixivvault_nicegui.managed_network_stop_integration import ManagedNetworkStopIntegration
from pixivvault_nicegui.managed_network_stop_profile_scope import ManagedProfileNetworkStopScope
from pixivvault_nicegui.managed_network_stop_storage_binding import (
    bind_new_native_storage, prepare_native_storage_binding,
)
from pixivvault_nicegui.native_profile import NativeProfileStore, provision_storage
from pixivvault_nicegui.pixiv_read_client import LivePixivReadClient
from pixivvault_nicegui.services import ApplicationServices


def _cookie(tmp_path):
    tmp_path.mkdir()
    path = tmp_path / "generated-cookie.txt"
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        f".pixiv.net\tTRUE\t/\tTRUE\t{int(time.time())+86400}\tPHPSESSID\t55_generatedonly\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("status", (401, 403, 429))
def test_new_profile_refuses_media_root_with_prior_latched_stop(tmp_path, status):
    old_root = tmp_path / "generated-existing-media"
    old_writer = ManagedPixivDownloads(old_root, object(), resolve_record=lambda _: None)
    old_writer.close()
    old_stop = ManagedNetworkStopIntegration(old_root)
    old_stop.gate.trip(status)

    profile = tmp_path / "generated-new-profile"
    store = NativeProfileStore(profile)
    store.close()
    scope = ManagedProfileNetworkStopScope(profile)
    cookie = _cookie(tmp_path / "generated-credential")
    store = NativeProfileStore(profile)
    try:
        store.update(cookie_file=cookie, expected_revision=0)
    finally:
        store.close()

    calls = []
    reader = scope.create_reader(
        cookie, transport=lambda *_: calls.append("unwanted GET") or b"",
    )
    try:
        with pytest.raises(ManagedNetworkGateStorageError):
            app = ApplicationServices(
                live_client=reader,
                pixiv_download_root=old_root,
                managed_network_stop_scope=scope,
            )
            app.close()
        assert calls == []
        assert old_stop.gate.snapshot().status_code == status
    finally:
        reader.close()


@pytest.mark.parametrize("status", (401, 403, 429))
def test_profileless_cli_cannot_reuse_a_native_profile_owned_save_root(tmp_path, status):
    profile = tmp_path / "generated-native-profile"
    store = NativeProfileStore(profile)
    store.close()
    scope = ManagedProfileNetworkStopScope(profile)
    cookie = _cookie(tmp_path / "generated-credential")
    parent = tmp_path / "generated-save-parent"
    parent.mkdir()
    store = NativeProfileStore(profile)
    try:
        snapshot = store.update(cookie_file=cookie, save_parent=parent, expected_revision=0)
        prepare_native_storage_binding(scope, parent)
        roots = provision_storage(snapshot, profile)
    finally:
        store.close()

    image_root = roots["pixiv_download_root"]
    bind_new_native_storage(scope, parent)
    # The earlier product accepted a Native-only reader, which now correctly
    # fails closed without a reviewed shared owner. Simulate the historical
    # local STOP directly; its ownership markers must still bar profile-less
    # CLI reuse without reading or altering the old contents.
    scope.gate.trip(status)

    requested = []
    cli_reader = LivePixivReadClient(
        cookie, transport=lambda *_: requested.append("unwanted GET") or b"",
    )
    try:
        with pytest.raises(ManagedNetworkGateStorageError):
            cli_app = ApplicationServices(
                live_client=cli_reader, pixiv_download_root=image_root,
            )
            cli_app.close()
        assert requested == []
        assert scope.gate.snapshot().status_code == status
    finally:
        cli_reader.close()
