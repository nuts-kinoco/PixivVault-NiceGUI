"""Generated-data acceptance of the managed-root stop integration boundary.

No real Cookie, HTTP, Pixiv API, original root or GUI is used. Product startup
still needs explicit wiring across all GET, scheduler and job entry points.
"""

import json
import time
from types import SimpleNamespace

import pytest

from pixivvault_nicegui.job_service import JobService
from pixivvault_nicegui.managed_downloads import IDENTITY, MARKER, ManagedPixivDownloads
from pixivvault_nicegui.managed_network_stop_gate import (
    ManagedNetworkGateStorageError, ManagedNetworkStopped,
)
from pixivvault_nicegui.managed_network_stop_integration import ManagedNetworkStopIntegration
from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected, LivePixivReadClient
from pixivvault_nicegui.services import ApplicationServices


_IMAGE = "https://i.pximg.net/generated/fictional.png"


def _owned_root(tmp_path):
    root = tmp_path / "owned-pixiv"
    writer = ManagedPixivDownloads(root, object(), resolve_record=lambda _work_id: None)
    writer.close()
    return root


def _cookie(tmp_path):
    path = tmp_path / "generated-cookie.txt"
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        f".pixiv.net\tTRUE\t/\tTRUE\t{int(time.time()) + 86400}\tPHPSESSID\t55_generatedonly\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("condition", ("missing", "foreign", "forged", "oversize"))
def test_foreign_or_missing_root_never_provisions_gate(tmp_path, condition):
    root = tmp_path / "not-owned"
    if condition != "missing":
        root.mkdir()
    if condition == "foreign":
        (root / "foreign.txt").write_text("generated", encoding="utf-8")
    if condition == "forged":
        (root / MARKER).write_text(json.dumps({**IDENTITY, "version": 7}), encoding="utf-8")
    if condition == "oversize":
        (root / MARKER).write_text("x" * 5000, encoding="utf-8")
    before = sorted(path.name for path in root.iterdir()) if root.exists() else None
    with pytest.raises(ManagedNetworkGateStorageError, match="owned managed root"):
        ManagedNetworkStopIntegration(root)
    after = sorted(path.name for path in root.iterdir()) if root.exists() else None
    assert after == before


def test_owned_root_exact_reader_public_get_and_persistent_job_scheduler_stop(tmp_path):
    root = _owned_root(tmp_path)
    assert (root / MARKER).is_file()
    bundle = ManagedNetworkStopIntegration(root)
    assert bundle.root == root
    calls = []

    def synthetic_transport(request, *_args):
        assert request.get_method() == "GET" and request.data is None
        calls.append("generated-GET")
        raise HTTPStatusRejected(429)

    reader = bundle.create_reader(_cookie(tmp_path), transport=synthetic_transport)
    assert type(reader) is LivePixivReadClient
    try:
        with pytest.raises(ManagedNetworkStopped) as error:
            reader.get_image_bytes(_IMAGE)
        assert error.value.status_code == 429
        with pytest.raises(ManagedNetworkStopped):
            reader.verify_identity()
        assert calls == ["generated-GET"]
        with pytest.raises(ManagedNetworkStopped):
            bundle.require_allowed()
        assert bundle.admit_job(SimpleNamespace(kind="pixiv-illust")) is False
        assert bundle.admit_job(SimpleNamespace(kind="pixiv-novel")) is False
        assert bundle.admit_job(SimpleNamespace(kind="ugoira-webm")) is False
    finally:
        reader.close()

    reopened = ManagedNetworkStopIntegration(root)
    assert reopened.gate.snapshot().status_code == 429
    assert reopened.admit_job(SimpleNamespace(kind="pixiv-illust")) is False

    executed = []
    jobs = JobService(
        lambda job, _context: executed.append(job.target_id) or "generated",
        tmp_path / "generated-jobs.sqlite3",
        dispatch_admission=reopened.admit_job,
    )
    try:
        job = jobs.enqueue("pixiv-illust", "101", operation_id="generated-only")
        with jobs._condition:
            assert jobs._condition.wait_for(
                lambda: any(j.job_id == job.job_id and j.state == "paused"
                            for j in jobs.snapshot()), timeout=10,
            )
        assert executed == []
    finally:
        jobs.close()


def test_application_services_autowires_exact_guarded_reader_and_admission(tmp_path):
    from stage5_generated_shared_owner import generated_shared_scope
    root = _owned_root(tmp_path)
    bundle = ManagedNetworkStopIntegration(root)
    calls = []
    cookie = _cookie(tmp_path)
    legacy_reader = bundle.create_reader(
        cookie,
        transport=lambda *_args: calls.append("generated-GET") or b"generated",
    )
    try:
        with pytest.raises(ManagedNetworkGateStorageError):
            ApplicationServices(live_client=legacy_reader, pixiv_download_root=root)
    finally:
        legacy_reader.close()
    shared = generated_shared_scope(tmp_path)
    reader = shared.create_reader(
        cookie, transport=lambda *_args: calls.append("generated-GET") or b"generated")
    # Do not adopt root: the legacy integration created its own STOP record.
    # A new isolated managed root has no historical owner to migrate.
    fresh_root = tmp_path / "fresh-shared-pixiv"
    services = ApplicationServices(live_client=reader, pixiv_download_root=fresh_root,
                                   shared_network_stop_scope=shared)
    try:
        assert services.live_client is reader
        assert services.managed_downloads.client is reader
        assert not calls, "constructing services must not issue a Pixiv GET"
        # Shared STOP is the current authority; the old root-only owner above
        # is rejected, while both the reader and queue use the shared gate.
        assert services.jobs._dispatch_admission is not None
        assert services.managed_network_stop.gate.path == shared.gate.path
        shared.gate.trip(429)
        assert services.static_jobs._dispatch_admission(None) is False
    finally:
        services.close()


def test_corrupt_or_missing_gate_after_initialization_denies_admission(tmp_path):
    bundle = ManagedNetworkStopIntegration(_owned_root(tmp_path))
    bundle.gate.path.unlink()
    with pytest.raises(ManagedNetworkGateStorageError):
        bundle.admit_job(SimpleNamespace(kind="pixiv-illust"))
    with pytest.raises(ManagedNetworkGateStorageError):
        bundle.admit_job(SimpleNamespace(kind="pixiv-novel"))
    with pytest.raises(ManagedNetworkGateStorageError):
        bundle.require_allowed()
