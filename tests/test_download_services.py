"""App lifecycle and explicit sandbox boundaries for download acceptance."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

from pixivvault_nicegui.bootstrap import parse_args
from pixivvault_nicegui.services import ApplicationServices


def test_no_implicit_job_storage():
    service = ApplicationServices(3)
    try:
        assert service.jobs is None
        assert service.download_writer is None
        assert service.download_sandbox is None
    finally:
        service.close()


def test_managed_download_is_executed_and_reopened_without_implicit_repeat(tmp_path):
    root = tmp_path / "downloads"
    service = ApplicationServices(3, download_sandbox=root)
    jobs = service.jobs
    job = jobs.enqueue("demo", "demo-0003", "合成3頁", operation_id="download-once")
    with jobs._condition:
        assert jobs._condition.wait_for(
            lambda: jobs.snapshot()[0].state in {"done", "error"}, timeout=10)
    result = jobs.snapshot()[0]
    assert result.state == "done"
    receipt = json.loads((root / result.result).read_text(encoding="utf-8"))
    assert len(receipt["files"]) == 3
    service.close()
    assert jobs.closed and service.closed
    reopened = ApplicationServices(3, download_sandbox=root)
    try:
        same = reopened.jobs.enqueue("demo", "demo-0003", "合成3頁", operation_id="download-once")
        assert same.job_id == job.job_id and same.state == "done"
        assert len(tuple((root / "outputs").glob("*/receipt.json"))) == 1
        assert reopened.jobs.control(same.job_id, "delete")
        assert (root / result.result).is_file()
    finally:
        reopened.close()


def test_live_or_existing_library_cannot_become_a_download_destination(tmp_path):
    target = tmp_path / "untouched"
    with pytest.raises(ValueError, match="cannot be combined"):
        ApplicationServices(download_sandbox=target, live_client=object())
    with pytest.raises(ValueError, match="cannot be combined"):
        ApplicationServices(download_sandbox=target,
            library_readonly_db=tmp_path / "old.db", library_image_root=tmp_path / "images")
    assert not target.exists()


def test_learning_and_download_storage_must_not_share_directory(tmp_path):
    target = tmp_path / "untouched"
    with pytest.raises(ValueError, match="must be separate"):
        ApplicationServices(download_sandbox=target, learning_sandbox=target)
    assert not target.exists()


def test_cli_download_storage_is_explicit_and_check_has_no_writes(tmp_path):
    target = tmp_path / "downloads"
    args = ["--download-sandbox", str(target)]
    assert parse_args(args).download_sandbox == target
    assert not target.exists()
    with pytest.raises(SystemExit):
        parse_args(["--download-sandbox", "relative"])
    with pytest.raises(SystemExit):
        parse_args(args + ["--pixiv-cookie-file", str(tmp_path / "cookie")])
    result = subprocess.run([sys.executable, "-m", "pixivvault_nicegui", "--check", *args],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["download_storage"] == "sandbox"
    assert not target.exists()


def test_job_shutdown_precedes_closing_shared_images(tmp_path, monkeypatch):
    service = ApplicationServices(3, download_sandbox=tmp_path / "downloads")
    order = []
    close_jobs, close_images = service.jobs.close, service.images.close
    def record_jobs():
        order.append("jobs")
        close_jobs()
    def record_images():
        assert service.jobs.closed
        order.append("images")
        close_images()
    monkeypatch.setattr(service.jobs, "close", record_jobs)
    monkeypatch.setattr(service.images, "close", record_images)
    service.close()
    assert order == ["jobs", "images"]
