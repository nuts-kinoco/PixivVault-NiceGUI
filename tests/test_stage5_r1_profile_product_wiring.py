"""Offline product-owned media service with one immutable native profile gate.

This exercises a generated NativeProfileStore, synthetic Cookie and transport.
No real profile, network, image or original source is used.
"""

import time

import pytest

from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopped
from pixivvault_nicegui.managed_network_stop_profile_scope import ManagedProfileNetworkStopScope
from pixivvault_nicegui.managed_novel_jobs import PinnedNovelSource
from pixivvault_nicegui.native_profile import NativeProfileStore, provision_storage
from pixivvault_nicegui.managed_network_stop_storage_binding import (
    prepare_native_storage_binding, bind_new_native_storage,
)
from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ugoira_practical import PinnedUgoiraSource
from pixivvault_nicegui.managed_network_stop_shared_transport import create_shared_profile_reader
from stage5_generated_shared_owner import generated_shared_scope


IMAGE = "https://i.pximg.net/generated/fictional.png"


def _generated_cookie(root):
    root.mkdir()
    path = root / "synthetic-cookie.txt"
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        f".pixiv.net\tTRUE\t/\tTRUE\t{int(time.time())+86400}\tPHPSESSID\t55_generatedonly\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("status", (401, 403, 429))
def test_product_all_queues_share_profile_latch_after_storage_mode_switch(tmp_path, status):
    profile_root = tmp_path / "generated-profile"
    store = NativeProfileStore(profile_root)
    store.close()
    credential = _generated_cookie(tmp_path / "synthetic-credential")
    first_parent, second_parent = tmp_path / "generated-first", tmp_path / "generated-second"
    first_parent.mkdir()
    second_parent.mkdir()
    scope = ManagedProfileNetworkStopScope(profile_root)
    shared = generated_shared_scope(tmp_path)
    scope.shared_owner = shared
    store = NativeProfileStore(profile_root)
    try:
        first_snapshot = store.update(cookie_file=credential, save_parent=first_parent, expected_revision=0)
    finally:
        store.close()
    prepare_native_storage_binding(scope, first_parent)
    first_paths = provision_storage(first_snapshot, profile_root)
    bind_new_native_storage(scope, first_parent)

    requests = []

    def synthetic(request, *_args):
        assert request.get_method() == "GET" and request.data is None
        requests.append("synthetic GET")
        raise HTTPStatusRejected(status)

    image_reader = create_shared_profile_reader(scope, credential, shared.gate,
        transport=synthetic, shared_owner_check=shared._check_owner)
    novel_reader = create_shared_profile_reader(scope, credential, shared.gate,
        transport=synthetic, shared_owner_check=shared._check_owner)
    first = ApplicationServices(
        live_client=image_reader,
        pixiv_download_root=first_paths["pixiv_download_root"],
        novel_fixture_root=first_paths["novel_download_root"],
        novel_fixture_source=PinnedNovelSource(novel_reader, {}),
        managed_network_stop_scope=scope,
        shared_network_stop_scope=shared,
    )
    try:
        if status==429:
            from test_display_recovery_batch import assert_product_429
            assert_product_429((image_reader,novel_reader),scope.gate,requests,IMAGE)
            assert not shared.gate.snapshot().blocked and not shared.gate.inflight_path.exists()
            return
        with pytest.raises(ManagedNetworkStopped):
            novel_reader.get_image_bytes(IMAGE)
        assert requests == ["synthetic GET"]
        assert first.static_jobs._dispatch_admission(None) is False
        assert first.novel_jobs.jobs._dispatch_admission(None) is False
    finally:
        first.close()

    store = NativeProfileStore(profile_root)
    try:
        second_snapshot = store.update(save_parent=second_parent, expected_revision=1)
    finally:
        store.close()
    reopened = ManagedProfileNetworkStopScope(profile_root)
    prepare_native_storage_binding(reopened, second_parent)
    second_paths = provision_storage(second_snapshot, profile_root)
    bind_new_native_storage(reopened, second_parent)
    shared = generated_shared_scope(tmp_path)
    reopened.shared_owner = shared
    ugoira_reader = create_shared_profile_reader(reopened, credential, shared.gate,
        transport=synthetic, shared_owner_check=shared._check_owner)
    second = ApplicationServices(
        ugoira_root=second_paths["ugoira_root"],
        ugoira_source=PinnedUgoiraSource(ugoira_reader),
        managed_network_stop_scope=reopened,
        shared_network_stop_scope=shared,
    )
    try:
        assert reopened.gate.snapshot().status_code == status
        with pytest.raises(ManagedNetworkStopped):
            ugoira_reader.get_image_bytes(IMAGE)
        assert requests == ["synthetic GET"]
        assert second.ugoira.jobs._dispatch_admission(None) is False
    finally:
        second.close()
