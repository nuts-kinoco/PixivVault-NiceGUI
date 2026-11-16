"""Generated-only product startup acceptance for durable managed GET stop.

No user Cookie, Pixiv request, real artwork or original storage is accessed.
"""

import time
from threading import Event

import pytest

from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopped
from pixivvault_nicegui.managed_network_stop_transport import ManagedNetworkStopTransport
from pixivvault_nicegui.managed_scheduler import submit_author_batch
from pixivvault_nicegui.managed_novel_jobs import PinnedNovelSource
from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected, LivePixivReadClient
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ugoira_practical import PinnedUgoiraSource
from stage5_generated_shared_owner import generated_shared_scope
from pixivvault_nicegui.managed_network_stop_shared_scope import _SealedOwnerTransport


_GENERATED_IMAGE = "https://i.pximg.net/generated/fictional.png"


def _cookie(tmp_path):
    path = tmp_path / "generated-cookie.txt"
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        f".pixiv.net\tTRUE\t/\tTRUE\t{int(time.time()) + 86400}\tPHPSESSID\t55_generatedonly\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("status", (401, 403, 429))
def test_application_services_wires_single_durable_gate_to_managed_reader_and_job_queue(tmp_path, status):
    calls = []

    def transport(request, *_args):
        assert request.get_method() == "GET" and request.data is None
        calls.append("generated-get")
        raise HTTPStatusRejected(status)

    cookie = _cookie(tmp_path)
    root = tmp_path / "generated-managed"
    shared = generated_shared_scope(tmp_path)
    first_reader = shared.create_reader(cookie, transport=transport)
    first = ApplicationServices(live_client=first_reader, pixiv_download_root=root,
                                shared_network_stop_scope=shared)
    try:
        assert isinstance(first_reader._transport, _SealedOwnerTransport)
        assert callable(first.static_jobs._dispatch_admission)
        if status==429:
            from test_display_recovery_batch import assert_product_429
            assert_product_429((first_reader,),shared.gate,calls,_GENERATED_IMAGE)
            return
        with pytest.raises(ManagedNetworkStopped) as stopped:
            first_reader.get_image_bytes(_GENERATED_IMAGE)
        assert stopped.value.status_code == status
        with pytest.raises(ManagedNetworkStopped):
            first_reader.get_image_bytes(_GENERATED_IMAGE)
        assert calls == ["generated-get"]
        assert first.static_jobs._dispatch_admission(None) is False
    finally:
        first.close()

    shared = generated_shared_scope(tmp_path)
    second_reader = shared.create_reader(cookie, transport=transport)
    second = ApplicationServices(live_client=second_reader, pixiv_download_root=root,
                                 shared_network_stop_scope=shared)
    try:
        assert isinstance(second_reader._transport, _SealedOwnerTransport)
        assert callable(second.static_jobs._dispatch_admission)
        with pytest.raises(ManagedNetworkStopped) as stopped:
            second_reader.get_image_bytes(_GENERATED_IMAGE)
        assert stopped.value.status_code == status
        assert calls == ["generated-get"]
    finally:
        second.close()


def test_scheduler_rejects_latched_stop_before_account_or_inventory_probe(tmp_path):
    cookie = _cookie(tmp_path)
    seen = []
    shared = generated_shared_scope(tmp_path)
    reader = shared.create_reader(cookie, transport=lambda *_args: seen.append("GET") or b"generated")
    services = ApplicationServices(live_client=reader, pixiv_download_root=tmp_path / "generated-managed",
                                   shared_network_stop_scope=shared)
    try:
        services.managed_network_stop.gate.trip(429)

        def probe():
            seen.append("identity-called")
            raise AssertionError("scheduler should stop before identity probe")

        reader.verify_identity = probe
        with pytest.raises(ManagedNetworkStopped):
            submit_author_batch(services, ("24",), cancellation=Event())
        assert seen == []
    finally:
        services.close()


@pytest.mark.parametrize("kind", ("novel", "ugoira"))
def test_standalone_live_media_root_has_persistent_reader_and_job_stop(tmp_path, kind):
    cookie = _cookie(tmp_path)
    calls = []

    def transport(request, *_args):
        assert request.get_method() == "GET" and request.data is None
        calls.append("generated-get")
        raise HTTPStatusRejected(429)

    root = tmp_path / ("generated-" + kind)
    shared = generated_shared_scope(tmp_path)

    def start():
        owner = generated_shared_scope(tmp_path)
        reader = owner.create_reader(cookie, transport=transport)
        if kind == "novel":
            services = ApplicationServices(
                novel_fixture_root=root, novel_fixture_source=PinnedNovelSource(reader, {}),
                shared_network_stop_scope=owner,
            )
            queue = services.novel_jobs.jobs
        else:
            services = ApplicationServices(
                ugoira_root=root, ugoira_source=PinnedUgoiraSource(reader),
                shared_network_stop_scope=owner,
            )
            queue = services.ugoira.jobs
        return reader, services, queue

    reader, services, queue = start()
    try:
        assert isinstance(reader._transport, _SealedOwnerTransport)
        assert callable(queue._dispatch_admission)
        from test_display_recovery_batch import assert_product_429
        assert_product_429((reader,),reader._transport.scope.gate,calls,_GENERATED_IMAGE)
        return
    finally:
        services.close()


@pytest.mark.parametrize("status", (401, 403, 429))
def test_combined_managed_media_uses_one_stop_for_all_three_readers_and_queues(tmp_path, status):
    cookie = _cookie(tmp_path)
    calls = []

    def transport(request, *_args):
        assert request.get_method() == "GET" and request.data is None
        calls.append("synthetic")
        raise HTTPStatusRejected(status)

    shared = generated_shared_scope(tmp_path)
    image_reader = shared.create_reader(cookie, transport=transport)
    novel_reader = shared.create_reader(cookie, transport=transport)
    ugoira_reader = shared.create_reader(cookie, transport=transport)
    services = ApplicationServices(
        live_client=image_reader,
        pixiv_download_root=tmp_path / "generated-image-root",
        novel_fixture_root=tmp_path / "generated-novel-root",
        novel_fixture_source=PinnedNovelSource(novel_reader, {}),
        ugoira_root=tmp_path / "generated-ugoira-root",
        ugoira_source=PinnedUgoiraSource(ugoira_reader),
        shared_network_stop_scope=shared,
    )
    try:
        three_readers = (image_reader, novel_reader, ugoira_reader)
        assert all(isinstance(c._transport, _SealedOwnerTransport) for c in three_readers)
        assert len({str(c._transport.scope.gate.path) for c in three_readers}) == 1
        if status==429:
            from test_display_recovery_batch import assert_product_429
            assert_product_429(three_readers,shared.gate,calls,_GENERATED_IMAGE)
            return
        with pytest.raises(ManagedNetworkStopped):
            novel_reader.get_image_bytes(_GENERATED_IMAGE)
        for candidate in three_readers:
            with pytest.raises(ManagedNetworkStopped):
                candidate.get_image_bytes(_GENERATED_IMAGE)
        assert calls == ["synthetic"]
        for queue in (services.static_jobs, services.novel_jobs.jobs, services.ugoira.jobs):
            assert queue._dispatch_admission(None) is False
    finally:
        services.close()


def test_novel_and_ugoira_without_image_root_share_one_durable_stop(tmp_path):
    cookie = _cookie(tmp_path)
    calls = []

    def transport(request, *_args):
        assert request.get_method() == "GET" and request.data is None
        calls.append("generated")
        raise HTTPStatusRejected(429)

    novel_root = tmp_path / "generated-novel-root"
    ugoira_root = tmp_path / "generated-ugoira-root"

    def start():
        owner = generated_shared_scope(tmp_path)
        novel_reader = owner.create_reader(cookie, transport=transport)
        ugoira_reader = owner.create_reader(cookie, transport=transport)
        services = ApplicationServices(
            novel_fixture_root=novel_root,
            novel_fixture_source=PinnedNovelSource(novel_reader, {}),
            ugoira_root=ugoira_root,
            ugoira_source=PinnedUgoiraSource(ugoira_reader),
            shared_network_stop_scope=owner,
        )
        return novel_reader, ugoira_reader, services

    novel_reader, ugoira_reader, services = start()
    try:
        assert isinstance(novel_reader._transport, _SealedOwnerTransport)
        assert isinstance(ugoira_reader._transport, _SealedOwnerTransport)
        assert novel_reader._transport.scope.gate.path == ugoira_reader._transport.scope.gate.path
        from test_display_recovery_batch import assert_product_429
        assert_product_429((novel_reader,ugoira_reader),novel_reader._transport.scope.gate,calls,_GENERATED_IMAGE)
        return
    finally:
        services.close()
