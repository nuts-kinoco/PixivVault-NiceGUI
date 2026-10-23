"""Synthetic-only native download checker tests; no GUI, exe or browser starts."""

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3
import struct
from types import SimpleNamespace
import zlib

import pytest

from tools import check_download_jobs_native as native
from tools import check_native_window as checker


@pytest.fixture
def completed_sandbox(tmp_path):
    from pixivvault_nicegui.download_sandbox import DownloadSandbox
    from pixivvault_nicegui.fixtures import generate_demo_artworks
    from pixivvault_nicegui.job_service import APPLICATION_ID, JobSnapshot

    root = tmp_path / "isolated-downloads"
    writer = DownloadSandbox(root, generate_demo_artworks(4))
    context = SimpleNamespace(checkpoint=lambda: None, progress=lambda *_: None)
    records = []
    try:
        for ordinal, (work_id, pages) in enumerate(native.WORK_PAGES.items()):
            relative = writer.run(SimpleNamespace(kind="demo", target_id=work_id), context)
            records.append(JobSnapshot(job_id=f"{ordinal + 1:032x}", kind="demo",
                                       target_id=work_id, title=work_id, state="done",
                                       total=pages, done=pages, result=relative))
    finally:
        writer.close()
    with sqlite3.connect(root / "jobs.sqlite3") as connection:
        connection.executescript(f"""
            PRAGMA application_id={APPLICATION_ID};
            PRAGMA user_version=1;
            CREATE TABLE jobs (job_id TEXT PRIMARY KEY, ordinal INTEGER NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE operations (operation_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, fingerprint TEXT NOT NULL);
        """)
        connection.executemany("INSERT INTO jobs VALUES (?,?,?)", [
            (job.job_id, i, json.dumps(asdict(job))) for i, job in enumerate(records)])
    return root


def test_initial_root_must_be_absent(tmp_path):
    target = tmp_path / "new"
    assert native.preflight_download_root(target, restart=False) is None
    target.mkdir()
    with pytest.raises(ValueError, match="must not exist"):
        native.preflight_download_root(target, restart=False)
    with pytest.raises(ValueError, match="absolute"):
        native.preflight_download_root(Path("relative"), restart=False)


def test_restart_requires_exact_marker_and_two_durable_jobs(completed_sandbox):
    baseline = native.preflight_download_root(completed_sandbox, restart=True)
    assert baseline["passed"] and baseline["history"] == 2
    assert baseline["validated_page_count"] == 4
    assert {name: len(value["pages"]) for name, value in baseline["receipts"].items()} == {
        "demo-0003": 3, "demo-0004": 1}
    marker = completed_sandbox / native.MARKER
    marker.write_text('{"application":"unknown"}', encoding="utf-8")
    with pytest.raises(ValueError, match="marker"):
        native.preflight_download_root(completed_sandbox, restart=True)


def test_restart_fails_on_unmarked_directory(tmp_path):
    existing = tmp_path / "foreign"
    existing.mkdir()
    (existing / "jobs.sqlite3").write_bytes(b"not sqlite")
    with pytest.raises(ValueError, match="marker"):
        native.preflight_download_root(existing, restart=True)


@pytest.mark.parametrize("change", ["file_hash", "receipt_path", "bad_state", "deleted_output"])
def test_bad_receipts_and_unfinished_history_are_rejected(completed_sandbox, change):
    root = completed_sandbox
    if change == "file_hash":
        page = next((root / "outputs").rglob("demo-0003_p2.png"))
        page.write_bytes(page.read_bytes() + b"x")
    elif change == "deleted_output":
        (root / "outputs" / next((root / "outputs").iterdir()).name / "receipt.json").unlink()
    else:
        with sqlite3.connect(root / "jobs.sqlite3") as connection:
            row = connection.execute("SELECT job_id,payload FROM jobs ORDER BY ordinal LIMIT 1").fetchone()
            snapshot = json.loads(row[1])
            if change == "receipt_path":
                snapshot["result"] = "../outside/receipt.json"
            else:
                snapshot["state"] = "queued"
            connection.execute("UPDATE jobs SET payload=? WHERE job_id=?",
                               (json.dumps(snapshot), row[0]))
    with pytest.raises((AssertionError, ValueError, OSError)):
        native.verify_native_jobs(root, remaining_ids=frozenset(native.WORK_PAGES))


def _replace_png_pixel(payload: bytes) -> bytes:
    """Forge a valid CRC/receipt SHA; only independent decoded pixels catch it."""
    pos = 8
    output = bytearray(payload[:8])
    while pos < len(payload):
        size = struct.unpack(">I", payload[pos:pos + 4])[0]
        name = payload[pos + 4:pos + 8]
        chunk = payload[pos + 8:pos + 8 + size]
        if name == b"IDAT":
            raw = bytearray(zlib.decompress(chunk))
            raw[1] ^= 1
            chunk = zlib.compress(raw, 6)
        output += struct.pack(">I", len(chunk)) + name + chunk
        output += struct.pack(">I", zlib.crc32(name + chunk) & 0xFFFFFFFF)
        pos += 12 + size
    return bytes(output)


def test_pixel_forgery_rejected_even_when_receipt_sha_is_updated(completed_sandbox):
    root = completed_sandbox
    receipt_file = next(p for p in root.rglob("receipt.json")
                        if json.loads(p.read_text())["work_id"] == "demo-0003")
    receipt = json.loads(receipt_file.read_text())
    png = receipt_file.parent / receipt["files"][0]["name"]
    changed = _replace_png_pixel(png.read_bytes())
    png.write_bytes(changed)
    receipt["files"][0].update(bytes=len(changed), sha256=hashlib.sha256(changed).hexdigest())
    receipt_file.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(AssertionError, match="pixel differs"):
        native.verify_native_jobs(root, remaining_ids=frozenset(native.WORK_PAGES))


def test_history_deletion_keeps_every_receipt(completed_sandbox):
    root = completed_sandbox
    baseline = native.verify_native_jobs(root, remaining_ids=frozenset(native.WORK_PAGES))
    with sqlite3.connect(root / "jobs.sqlite3") as connection:
        connection.execute("DELETE FROM jobs WHERE payload LIKE ?", ('%"target_id": "demo-0003"%',))
    after = native.verify_native_jobs(root, remaining_ids=frozenset({"demo-0004"}))
    assert after["history"] == 1 and after["receipts"] == baseline["receipts"]
    assert after["validated_page_count"] == 4


@pytest.mark.parametrize("case,accepted", [
    ("valid", True), ("missing", False), ("unpassed", False),
    ("wrong_mode", False), ("bad_disk", False), ("bad_history", False),
    ("bad_search", False), ("bad_count", False), ("bad_receipts", False),
])
def test_jobs_acceptance_is_explicit_and_complete(case, accepted):
    payload = {"passed": True, "mode": "initial", "disk_verified": True,
               "history_verified": True, "search_retained": True,
               "validated_page_count": 4, "history_before": 2, "history_after": 2,
               "receipts": {work_id: {} for work_id in native.WORK_PAGES}}
    if case == "wrong_mode":
        payload["mode"] = "restart"
    elif case == "bad_disk":
        payload["disk_verified"] = False
    elif case == "bad_history":
        payload["history_verified"] = False
    elif case == "bad_search":
        payload["search_retained"] = False
    elif case == "bad_count":
        payload["history_before"] = 0
    elif case == "bad_receipts":
        payload["receipts"] = {}
    elif case == "unpassed":
        payload["passed"] = False
    result = {"passed": True} if case == "missing" else {"passed": True, "download_jobs": payload}
    assert native.jobs_ui_accepted(result, restart=False) is accepted
    assert not native.jobs_ui_accepted({"passed": False, "download_jobs": payload}, restart=False)
    assert native.jobs_ui_accepted(None, restart=False) is False


def test_restart_must_explicitly_prove_no_auto_run_and_preserve_files():
    payload = {"passed": True, "mode": "restart", "disk_verified": True,
               "history_verified": True, "search_retained": True,
               "validated_page_count": 4, "history_before": 2, "history_after": 1,
               "receipts": {work_id: {} for work_id in native.WORK_PAGES}}
    assert not native.jobs_ui_accepted({"passed": True, "download_jobs": payload}, restart=True)
    payload["no_auto_execution"] = True
    assert not native.jobs_ui_accepted({"passed": True, "download_jobs": payload}, restart=True)
    payload["delete_kept_files"] = True
    assert native.jobs_ui_accepted({"passed": True, "download_jobs": payload}, restart=True)


@pytest.mark.parametrize("outcome", ["accept", "missing", "failure", "post_close_failure", "wrong_mode"])
@pytest.mark.parametrize("restart", [False, True])
def test_mock_native_main_enforces_jobs_and_shutdown(monkeypatch, tmp_path, outcome, restart):
    """Exercise the real orchestration and final status using a mock owned process."""
    states = {"closed": False}
    events = []
    target = tmp_path / "synthetic.exe"
    target.write_bytes(b"mock-executable")
    output = tmp_path / "checker.json"
    root = tmp_path / "isolated"
    expected = {work_id: {"verified": True} for work_id in native.WORK_PAGES}
    before = {"passed": True, "receipts": expected}
    expected_after = expected if outcome != "post_close_failure" else {"changed": True}
    def preflight(path, *, restart):
        assert path == root
        return before if restart else None
    def verify(path, *, remaining_ids):
        assert path == root
        assert remaining_ids == (
            frozenset({"demo-0004"}) if restart else frozenset(native.WORK_PAGES))
        return {"passed": True, "receipts": expected_after}
    def inspect(*args, **kwargs):
        events.append("inspected")
        assert kwargs["download_sandbox"] == root and kwargs["jobs_restart"] == restart
        kwargs["check_owner"]()
        if outcome == "failure":
            raise RuntimeError("synthetic inspection failure")
        if outcome == "missing":
            return {"passed": True}
        return {"passed": True, "download_jobs": {
            "passed": True, "mode": (
                "initial" if outcome == "wrong_mode" and restart else
                "restart" if outcome == "wrong_mode" else
                "restart" if restart else "initial"),
            "disk_verified": True, "history_verified": True,
            "search_retained": True, "receipts": expected,
            "validated_page_count": 4, "history_before": 2,
            "history_after": 1 if restart else 2,
            "no_auto_execution": restart, "delete_kept_files": restart,
        }}
    # For an initial run, wrong_mode is restart. For restart, wrong_mode is initial.
    class Process:
        pid = 10
        def poll(self):
            return 0 if states["closed"] else None
    monkeypatch.setattr(checker, "os", SimpleNamespace(name="nt", environ={}))
    monkeypatch.setattr(checker, "subprocess", SimpleNamespace(
        Popen=lambda *a, **kw: (events.append("spawn") or Process()),
        DEVNULL=-3, STDOUT=-2))
    ports = iter([32000, 32001])
    monkeypatch.setattr(checker, "available_port", lambda: next(ports))
    monkeypatch.setattr(checker, "process_identity", lambda pid: 1000 if not states["closed"] else None)
    monkeypatch.setattr(checker, "process_pairs", lambda: [(10, 1)])
    monkeypatch.setattr(checker, "owned_windows", lambda pids: [100])
    monkeypatch.setattr(checker, "owned_window_identity", lambda *a: (10, 1000))
    monkeypatch.setattr(checker, "health_ready", lambda *a: True)
    monkeypatch.setattr(checker, "ui_ready", lambda *a: True)
    monkeypatch.setattr(checker, "living_pids",
                        lambda ids: set() if states["closed"] else set(ids))
    monkeypatch.setattr(checker, "port_released", lambda *a: states["closed"])
    monkeypatch.setattr(checker, "run_restore_gate",
                        lambda *a, **kw: {"passed": True, "minimized": True})
    monkeypatch.setattr(checker, "close_owned_window",
                        lambda *a: (events.append("closed") or states.__setitem__("closed", True) or True))
    monkeypatch.setattr(checker, "terminate_owned", lambda *a: pytest.fail("forced cleanup"))
    monkeypatch.setattr(checker, "inspect_ui", inspect)
    monkeypatch.setattr(native, "preflight_download_root", preflight)
    monkeypatch.setattr(native, "verify_native_jobs", verify)
    args = ["--exe", str(target), "--output", str(output), "--inspect-ui",
            "--download-sandbox", str(root)]
    if restart:
        args += ["--jobs-restart"]
    assert checker.main(args) == (0 if outcome == "accept" else 1)
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == ("passed" if outcome == "accept" else "failed")
    assert report["normal_exit"] and report["port_released"] and report["wm_close_sent"]
    assert events == ["spawn", "inspected", "closed"]
    if outcome in {"missing", "failure", "wrong_mode"}:
        assert "native_jobs_ui_acceptance_missing" in report["errors"]
    if outcome == "post_close_failure":
        assert "native_jobs_persistence_failed" in report["errors"]


@pytest.mark.parametrize("extra", [
    [], ["--jobs-restart"], ["--learning-sandbox"], ["--library-readonly-db"],
])
def test_main_rejects_invalid_jobs_arguments_without_spawning(monkeypatch, tmp_path, extra):
    fake_exe = tmp_path / "a.exe"
    fake_exe.write_bytes(b"fake")
    monkeypatch.setattr(checker, "os", SimpleNamespace(name="nt", environ={}))
    monkeypatch.setattr(checker, "subprocess", SimpleNamespace(Popen=lambda *a, **k:
                        pytest.fail("rejected CLI spawned app")))
    args = ["--exe", str(fake_exe), "--download-sandbox", str(tmp_path / "fresh")]
    if extra == ["--jobs-restart"]:
        args += ["--jobs-restart", "--inspect-ui"]
    elif extra == ["--learning-sandbox"]:
        args += ["--inspect-ui", "--learning-sandbox", str(tmp_path / "learning")]
    elif extra == ["--library-readonly-db"]:
        args += ["--inspect-ui", "--library-readonly-db", str(tmp_path / "db"),
                 "--library-image-root", str(tmp_path / "images")]
    with pytest.raises(SystemExit) as exc:
        checker.main(args)
    assert exc.value.code == 2
