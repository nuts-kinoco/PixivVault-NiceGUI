"""Synthetic archive through the real ugoira owner; no codec subprocess or network."""
import io
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace
import zipfile

import pytest
from PIL import Image

from pixivvault_nicegui.job_service import JobCancelled
from pixivvault_nicegui.managed_ugoira import ManagedUgoiraJobs, ManagedUgoiraError
from pixivvault_nicegui.media_parity_policy import parse_ugoira_manifest


PRIVATE = "synthetic-cookie=private token=private S:/private/input.zip"


@pytest.fixture(autouse=True)
def no_external_execution(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("network and codec process are forbidden")
    monkeypatch.setattr(socket.socket, "connect", reject)
    monkeypatch.setattr(socket, "create_connection", reject)
    monkeypatch.setattr(socket, "getaddrinfo", reject)
    monkeypatch.setattr(subprocess, "Popen", reject)


def archive():
    frame = io.BytesIO()
    # Valid archive image; odd width is explicitly rejected by actual mp4_plan.
    Image.new("RGB", (3, 2), (1, 2, 3)).save(frame, format="PNG")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as packet:
        packet.writestr("000000.png", frame.getvalue())
    manifest = parse_ugoira_manifest({
        "originalSrc": "https://i.pximg.net/img-zip-ugoira/offline/1_ugoira.zip",
        "frames": [{"file": "000000.png", "delay": 100}],
    }, work_id="1")
    return out.getvalue(), manifest


@pytest.mark.parametrize("case,expected", [
    ("mp4", "ugoira_mp4_failed"),
    ("permission", "permission_denied"),
    ("unknown", "runner_failed"),
    ("cancel", ""),
])
def test_actual_ugoira_run_keeps_safe_reason(tmp_path, monkeypatch, case, expected):
    root = tmp_path / "generated-ugoira"
    owner = ManagedUgoiraJobs(root, codec_python=sys.executable)
    original_mkdir = Path.mkdir
    calls = []
    def mkdir(path, *args, **kwargs):
        if path.parent == owner.codec_incomplete:
            calls.append(True)
            if case == "permission":
                raise PermissionError(PRIVATE)
            if case == "unknown":
                raise ValueError(PRIVATE)
            if case == "cancel":
                raise JobCancelled()
        return original_mkdir(path, *args, **kwargs)
    monkeypatch.setattr(Path, "mkdir", mkdir)
    try:
        owner.enqueue("synthetic-operation", *archive(), output="mp4")
        with owner.jobs._condition:
            assert owner.jobs._condition.wait_for(
                lambda: owner.jobs.snapshot() and owner.jobs.snapshot()[0].state in {"error", "stopped"},
                timeout=5)
        failed = owner.jobs.snapshot()[0]
        assert calls == [True]
        assert failed.state == ("stopped" if case == "cancel" else "error")
        assert failed.error_code == expected and failed.result is None
        assert not tuple(owner.mp4s.iterdir())
        for path in owner.diagnostics.rglob("*.json"):
            assert PRIVATE not in path.read_text()
    finally:
        owner.close()
    monkeypatch.setattr(Path, "mkdir", original_mkdir)

    reopened = ManagedUgoiraJobs(root, codec_python=sys.executable)
    try:
        assert reopened.jobs.snapshot()[0] == failed
        assert calls == [True]
        if case in {"mp4", "permission"}:
            from nicegui import ui
            from pixivvault_nicegui.ui.job_views import QueueView
            with ui.column() as holder:
                view = QueueView(SimpleNamespace(jobs=reopened.jobs, artworks=()), failed_only=True)
            try:
                text = view.rows[failed.job_id]["tooltip"].text
                assert ("MP4変換" if case == "mp4" else "アクセス権限") in text
                assert PRIVATE not in text
            finally:
                view.close()
                holder.delete()
    finally:
        reopened.close()


def test_admission_error_keeps_existing_contract():
    error = ManagedUgoiraError("mp4_environment_unverified")
    assert isinstance(error, ValueError)
    assert error.code == "mp4_environment_unverified"
    assert str(error) == "mp4_environment_unverified"


def test_ugoira_carrier_rejects_private_reason():
    from pixivvault_nicegui.job_failure import job_failure_code
    error = ManagedUgoiraError(reason_code=PRIVATE)
    assert job_failure_code(error) == "runner_failed"
    assert str(error) == "managed_ugoira_failed"
    error.reason_code = [PRIVATE]
    assert job_failure_code(error) == "runner_failed"
