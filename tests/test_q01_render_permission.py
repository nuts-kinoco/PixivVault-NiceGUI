"""Real MP4 render boundaries, generated inputs, no real codec or network."""
import io
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace
import zipfile

import pytest
from PIL import Image

from pixivvault_nicegui import ugoira_mp4 as mp4
from pixivvault_nicegui.job_failure import job_failure_code
from pixivvault_nicegui.job_service import JobCancelled
from pixivvault_nicegui.managed_ugoira import ManagedUgoiraJobs
from pixivvault_nicegui.media_parity_policy import parse_ugoira_manifest

PRIVATE = "synthetic-cookie=private token=private S:/private/codec.log"


@pytest.fixture(autouse=True)
def no_external_execution(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("real network and subprocess are forbidden")
    monkeypatch.setattr(socket.socket, "connect", reject)
    monkeypatch.setattr(socket, "create_connection", reject)
    monkeypatch.setattr(socket, "getaddrinfo", reject)
    monkeypatch.setattr(subprocess, "Popen", reject)


def plan_input():
    return SimpleNamespace(duration_ms=100, frames=(
        SimpleNamespace(width=2, height=2, filename="000000.png", delay_ms=100),))


def permission_hook(monkeypatch, boundary, matches_stage):
    original_iterdir, original_open = Path.iterdir, Path.open
    hits = []
    def iterdir(path):
        if boundary == "stage" and matches_stage(path):
            hits.append("stage")
            raise PermissionError(PRIVATE)
        return original_iterdir(path)
    def open_file(path, *args, **kwargs):
        if boundary == "log" and path.name == "codec.log" and matches_stage(path.parent):
            hits.append("log")
            raise PermissionError(PRIVATE)
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "iterdir", iterdir)
    monkeypatch.setattr(Path, "open", open_file)
    return hits


@pytest.mark.parametrize("boundary", ["stage", "log"])
def test_render_permission_keeps_public_exception_type(tmp_path, monkeypatch, boundary):
    stage = tmp_path / "generated-stage"
    stage.mkdir()
    hits = permission_hook(monkeypatch, boundary, lambda p: p == stage)
    with pytest.raises(mp4.UgoiraMp4Error) as captured:
        mp4.render_mp4(b"synthetic", plan_input(), stage, sys.executable, lambda: None)
    assert hits == [boundary]
    assert isinstance(captured.value, ValueError)
    assert str(captured.value) == "mp4_environment_or_conversion_failed"
    assert job_failure_code(captured.value) == "permission_denied"
    assert PRIVATE not in repr(captured.value)


def archive():
    frame, output = io.BytesIO(), io.BytesIO()
    Image.new("RGB", (2, 2), (1, 2, 3)).save(frame, format="PNG")
    with zipfile.ZipFile(output, "w") as z:
        z.writestr("000000.png", frame.getvalue())
    manifest = parse_ugoira_manifest({
        "originalSrc": "https://i.pximg.net/img-zip-ugoira/offline/1_ugoira.zip",
        "frames": [{"file": "000000.png", "delay": 100}],
    }, work_id="1")
    return output.getvalue(), manifest


@pytest.mark.parametrize("boundary", ["stage", "log"])
def test_real_owner_preserves_render_permission_after_restart(tmp_path, monkeypatch, boundary):
    root = tmp_path / "generated-ugoira"
    owner = ManagedUgoiraJobs(root, codec_python=sys.executable)
    codec_root = owner.codec_incomplete
    with monkeypatch.context() as faults:
        hits = permission_hook(faults, boundary, lambda p: p.parent == codec_root)
        try:
            owner.enqueue("synthetic-operation", *archive(), output="mp4")
            with owner.jobs._condition:
                assert owner.jobs._condition.wait_for(
                    lambda: owner.jobs.snapshot() and owner.jobs.snapshot()[0].state == "error",
                    timeout=5)
            failed = owner.jobs.snapshot()[0]
            assert boundary in hits
            assert failed.error_code == "permission_denied" and failed.result is None
            assert not tuple(owner.mp4s.iterdir())
            assert PRIVATE.encode() not in (root / "jobs.sqlite3").read_bytes()
            for path in owner.diagnostics.rglob("*.json"):
                assert PRIVATE not in path.read_text()
        finally:
            owner.close()
    reopened = ManagedUgoiraJobs(root, codec_python=sys.executable)
    try:
        assert reopened.jobs.snapshot()[0] == failed
        from nicegui import ui
        from pixivvault_nicegui.ui.job_views import QueueView
        with ui.column() as holder:
            view = QueueView(SimpleNamespace(jobs=reopened.jobs, artworks=()), failed_only=True)
        try:
            text = view.rows[failed.job_id]["tooltip"].text
            assert "アクセス権限" in text and PRIVATE not in text
        finally:
            view.close()
            holder.delete()
    finally:
        reopened.close()


def test_render_cancellation_preserves_type_and_cleanup(tmp_path, monkeypatch):
    stage = tmp_path / "generated-cancel-stage"
    stage.mkdir()
    class SyntheticProcess:
        active = True
        terminated = 0
        waited = 0
        def poll(self):
            return None if self.active else 0
        def terminate(self):
            self.terminated += 1
            self.active = False
        def wait(self, timeout):
            self.waited += 1
            return 0
    process = SyntheticProcess()
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    def cancel():
        raise JobCancelled()
    with pytest.raises(JobCancelled):
        mp4.render_mp4(b"synthetic", plan_input(), stage, sys.executable, cancel)
    assert process.terminated == process.waited == 1


def test_codec_error_carrier_rejects_private_or_unrelated_reason():
    for code in (PRIVATE, [PRIVATE], "image_size_limit", None):
        error = mp4.UgoiraMp4Error(reason_code=code)
        assert isinstance(error, ValueError)
        assert str(error) == "mp4_environment_or_conversion_failed"
        assert job_failure_code(error) == "ugoira_mp4_failed"
