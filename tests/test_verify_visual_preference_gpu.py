"""Contracts for the isolated EgaraNet provider comparison."""

from contextlib import contextmanager
from io import BytesIO
import json
from pathlib import Path
import runpy
import subprocess
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def verify():
    return runpy.run_path(str(Path(__file__).resolve().parents[1]
                              / "tools" / "verify_visual_preference_gpu.py"))


def test_profile_counts_unique_compute_nodes_and_separates_transfers(verify):
    def kernel(name, provider, op):
        return {"cat": "Node", "name": name + "_kernel_time",
                "args": {"provider": provider, "op_name": op}}

    gpu, cpu = "CUDAExecutionProvider", "CPUExecutionProvider"
    events = [
        kernel("matmul", gpu, "MatMul"), kernel("matmul", gpu, "MatMul"),
        kernel("shape", cpu, "Shape"), kernel("copy", gpu, "MemcpyFromHost"),
        {"cat": "Session", "name": "model_run"},
    ]
    result = verify["node_placement"](events)
    assert result["compute_by_provider"] == {gpu: 1, cpu: 1}
    assert result["cuda_fraction"] == 0.5
    assert result["cpu_nodes"] == [{"name": "shape", "op": "Shape"}]
    assert result["transfer_nodes"] == [{"name": "copy", "op": "MemcpyFromHost"}]
    assert verify["node_placement"]([])["cuda_fraction"] is None


def test_neighbor_comparison_excludes_self_and_detects_rank_swaps(verify):
    original = [(1., 0.), (0.8, 0.6), (0.6, 0.8), (-1., 0.)]
    shifted = [original[0], original[2], original[1], original[3]]
    same = verify["compare_vectors"](original, original, k=2)
    swapped = verify["compare_vectors"](original, shifted, k=2)
    assert same["paired_cosine"]["min"] == pytest.approx(1)
    assert same["exact_top_k_queries"] == same["query_count"] == 4
    assert same["mean_spearman"] == pytest.approx(1)
    assert swapped["top_k_overlap"] > swapped["exact_top_k_queries"] / 4
    assert swapped["mean_spearman"] < 1
    assert swapped["paired_cosine"]["min"] < 1
    with pytest.raises(ValueError):
        verify["compare_vectors"](original, shifted[:-1], k=2)
    with pytest.raises(ValueError):
        verify["compare_vectors"]([(0., 0.)] * 3, original[:3], k=2)


def test_cuda_failure_produces_no_correction_input(verify):
    error = verify["EgaraInferenceError"]

    class Predictor:
        provider_requested = "cuda"
        provider_active = None
        fallback_reason = None

        def infer(self, data):
            raise error("private diagnostic")

    vectors, state = verify["first_inference"](Predictor(), b"image")
    assert vectors is None
    assert state["usable_embedding"] is False
    assert state["error"] == "EgaraInferenceError"
    assert "private diagnostic" not in str(state)


def test_batch_probe_distinguishes_rejection_and_collapsed_output(verify):
    class Output:
        def __init__(self, shape):
            self.shape = shape

    classify = verify["batch_result"]
    assert classify(None)["status"] == "rejected"
    assert classify([])["status"] == "rejected"
    assert classify([Output((1, 1024)), Output((2, 10, 1024))])["status"] == "collapsed"
    assert classify([Output((2, 1024)), Output((2, 10, 1024))])["status"] == "two_rows"


def test_cuda_worker_requires_live_lease_from_its_process_tree(verify):
    check = verify["validate_cuda_lease"]
    current = {"owner": "PV", "pid": 7, "expires": 1800}
    assert check(current, expected_pid=7, ancestors={7, 12}, now=10) == 1800
    for lease, tree, now in ((current, {12, 13}, 10),
                             (current, {7, 12}, 1799),
                             ({"owner": "EMA", "pid": 7, "expires": 1800}, {7, 12}, 10),
                             (None, {7, 12}, 10)):
        with pytest.raises(RuntimeError):
            check(lease, expected_pid=7, ancestors=tree, now=now)


def test_report_records_gpu_incomplete_even_when_cpu_succeeded(verify):
    status = verify["measurement_exit_code"]
    report = {
        "lease": {"status": "unavailable"},
        "cold_runs_requested": 1,
        "datasets": {"synthetic": {"cpu": {"node_placement": {"cuda_fraction": 0},
                                             "cold": {"first_inference_ms": {"n": 1}}}}},
        "failure_controls": {
            "auto-unavailable": {"outcome": {"provider_active": "cpu", "usable_embedding": True}},
            "cuda-unavailable": {"outcome": {"provider_active": None, "usable_embedding": False}},
        },
    }
    assert status(report, skip_gpu=True) == 0
    assert status(report, skip_gpu=False) != 0
    report["cold_runs_requested"] = 2
    assert status(report, skip_gpu=True) != 0
    report["cold_runs_requested"] = 1
    report["failure_controls"]["auto-unavailable"] = {"error": "missing"}
    assert status(report, skip_gpu=True) != 0


@pytest.mark.skipif(sys.platform != "win32", reason="the shared GPU lease runs on Windows")
def test_gpu_run_lock_rejects_duplicate_and_releases_after_exception(verify, tmp_path):
    lock = verify["gpu_run_lock"]
    path = tmp_path / "cuda.lock"
    with pytest.raises(ValueError):
        with lock(path):
            with pytest.raises(OSError):
                with lock(path):
                    pytest.fail("duplicate GPU run")
            raise ValueError("simulated worker failure")
    with lock(path):
        assert path.is_file()


def test_raw_lease_retries_sharing_and_incomplete_json(verify, monkeypatch):
    expected = {"owner": "PV"}
    reads = iter([PermissionError(), b"{", json.dumps(expected).encode()])

    def read(*args, **kwargs):
        item = next(reads)
        if isinstance(item, Exception):
            raise item
        return BytesIO(item)

    monkeypatch.setattr(Path, "open", read)
    monkeypatch.setattr(verify["time"], "sleep", lambda _: None)
    assert verify["raw_lease"]() == expected


@pytest.mark.parametrize("scenario", ["suite_error", "sample_error", "timeout", "stop_unconfirmed", "replaced",
                                     "stop_unconfirmed_cleanup_error",
                                     "replaced_between_reads", "replaced_token", "other_owner", "expired",
                                     "acquire_failed", "acquire_interrupted", "transient",
                                     "unreadable", "uncertain_release"])
def test_lease_cleanup_with_fake_helper(verify, monkeypatch, scenario):
    namespace = verify["run_leased_cuda"].__globals__
    now = 1000
    pid = namespace["os"].getpid()
    owns = None
    commands = []
    inject_read_error = False
    cleanup_reads = 0

    def raw():
        nonlocal inject_read_error, cleanup_reads, owns
        if inject_read_error:
            inject_read_error = False
            raise PermissionError()
        if scenario == "unreadable" and "work" in commands:
            raise PermissionError()
        if scenario == "replaced_between_reads" and "work" in commands:
            cleanup_reads += 1
            if cleanup_reads == 2:
                owns = owns | {"start": now + 1, "purpose": "different"}
        return owns

    def status():
        return {"lease": owns, "free_gib": 12}

    def helper(command, **kwargs):
        nonlocal owns
        action = next(value for value in command if value in {"acquire", "release"})
        commands.append(action)
        if action == "acquire":
            if scenario == "acquire_failed":
                return subprocess.CompletedProcess(command, 2, stdout="")
            owns = {"owner": "PV", "pid": pid, "start": now, "expires": now + 1800,
                    "purpose": command[command.index("--purpose") + 1], "token": "controlled-token"}
            if scenario == "acquire_interrupted":
                raise OSError("controlled acquire completion uncertainty")
            return subprocess.CompletedProcess(command, 0, stdout="acquired " + json.dumps(owns))
        assert kwargs.get("timeout", 0) > 0
        assert command[command.index("--token") + 1] == "controlled-token"
        assert command[command.index("--pid") + 1] == str(pid)
        if scenario == "uncertain_release":
            owns = None
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        owns = None
        return subprocess.CompletedProcess(command, 0)

    def suite(*args, **kwargs):
        nonlocal owns, now, inject_read_error
        commands.append("work")
        if scenario == "suite_error":
            raise RuntimeError("controlled suite failure")
        if scenario == "timeout":
            raise subprocess.TimeoutExpired("worker", 10)
        if scenario.startswith("stop_unconfirmed"):
            raise verify["CudaWorkerStillRunning"]("controlled termination failure")
        if scenario == "replaced":
            owns = owns | {"start": now + 1, "purpose": "foreign", "expires": now + 1801}
        if scenario == "replaced_token":
            owns = owns | {"token": "replacement"}
        if scenario == "other_owner":
            owns = owns | {"owner": "EMA"}
        if scenario == "expired":
            now += 1800
        if scenario == "transient":
            inject_read_error = True
        return {}

    @contextmanager
    def monitor(_):
        if scenario == "sample_error":
            raise RuntimeError("controlled monitor failure")
        try:
            yield
        finally:
            if scenario == "stop_unconfirmed_cleanup_error":
                raise OSError("controlled monitor teardown failure")

    monkeypatch.setitem(namespace, "raw_lease", raw)
    monkeypatch.setitem(namespace, "lease_status", status)
    monkeypatch.setitem(namespace, "sample_vram", monitor)
    monkeypatch.setitem(namespace, "suite", suite)
    monkeypatch.setattr(verify["time"], "time", lambda: now)
    monkeypatch.setattr(verify["time"], "sleep", lambda _: None)
    monkeypatch.setattr(subprocess, "check_output",
                        lambda cmd, **kwargs: "--token" if "--help" in cmd else "RTX")
    monkeypatch.setattr(subprocess, "run", helper)
    result, _ = verify["run_leased_cuda"](SimpleNamespace(vram_gib=2.5, lease_wait=1800),
                                         [("synthetic", None)])
    should_release = scenario in {"suite_error", "sample_error", "timeout", "transient",
                                  "uncertain_release", "acquire_interrupted"}
    assert commands.count("release") == int(should_release)
    if should_release:
        assert result["owned_lease_remaining"] is False
    if scenario == "unreadable":
        assert result["owned_lease_remaining"] is None
    if scenario.startswith("stop_unconfirmed"):
        assert result["owned_lease_remaining"] is True


@pytest.mark.parametrize("confirmed", [True, False])
def test_parent_bounds_cuda_worker_and_closes_its_job(verify, monkeypatch, tmp_path, confirmed):
    namespace = verify["run_worker"].__globals__
    now = 1000
    pid = namespace["os"].getpid()
    lease = {"owner": "PV", "pid": pid, "start": 900, "expires": 1080, "purpose": "own", "token": "fake"}
    monkeypatch.setitem(namespace, "raw_lease", lambda: lease)
    monkeypatch.setattr(verify["time"], "time", lambda: now)
    steps = []

    @contextmanager
    def own_job(name):
        assert name
        steps.append("open")

        def stop():
            steps.append("kill job")

        try:
            yield lambda child: steps.append("attach child"), stop
        finally:
            steps.append("close job")

    class Child:
        def __init__(self, command, **kwargs):
            assert "--worker-job" in command
            self.returncode = None
            self.stdin = self.stdout = self.stderr = SimpleNamespace(close=self.close_pipe)

        def communicate(self, *, input, timeout):
            assert json.loads(input)[-1] == "fake"
            assert 0 < timeout < 80
            steps.append("timeout")
            raise subprocess.TimeoutExpired("fake worker", timeout)

        def poll(self):
            return None

        def kill(self):
            steps.append("kill child")

        def wait(self, *, timeout):
            assert timeout == 3
            if not confirmed:
                raise subprocess.TimeoutExpired("fake worker", timeout)
            self.returncode = 124

        def close_pipe(self):
            if self.returncode is None:
                pytest.fail("a blocked reader's stream must not be closed synchronously")

    monkeypatch.setitem(namespace, "cuda_worker_job", own_job)
    monkeypatch.setattr(subprocess, "Popen", Child)
    args = SimpleNamespace(model=tmp_path/"model", output=tmp_path, synthetic_count=4,
                           batch_sizes=None)
    if confirmed:
        result = verify["run_worker"](args, sys.executable, "cuda",
                                      expected_lease=verify["lease_identity"](lease))
        assert result["error"] == "worker_timeout"
    else:
        with pytest.raises(verify["CudaWorkerStillRunning"]):
            verify["run_worker"](args, sys.executable, "cuda",
                                 expected_lease=verify["lease_identity"](lease))
    assert steps == ["open", "attach child", "timeout", "kill child", "kill job", "close job"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows job object process lifetime")
def test_parent_timeout_reaps_a_real_venv_child_without_gpu(verify, monkeypatch, tmp_path):
    import ctypes
    import os
    import time
    from ctypes import wintypes

    parent = verify["run_worker"]
    namespace = parent.__globals__
    started = tmp_path / "started"
    worker = tmp_path / "controlled_worker.py"
    worker.write_text(
        "import os, runpy, sys, time\n"
        "assert sys.stdin.readline()\n"
        f"assert sys.prefix.casefold() == {sys.prefix.casefold()!r}\n"
        f"runpy.run_path({str(namespace['__file__'])!r})['check_cuda_worker_job']"
        "(sys.argv[sys.argv.index('--worker-job') + 1])\n"
        f"with open({str(started)!r}, 'w', encoding='ascii') as marker:\n"
        "    marker.write(str(os.getpid()))\n"
        "time.sleep(90)\n",
        encoding="ascii",
    )
    monkeypatch.setitem(namespace, "__file__", str(worker))
    expires = time.time() + 120
    lease = {"owner": "PV", "pid": os.getpid(), "expires": expires, "token": "controlled-token"}
    monkeypatch.setitem(namespace, "raw_lease", lambda: lease)
    observed = []

    class ReadyChild(subprocess.Popen):
        def communicate(self, *, input, timeout):
            self.stdin.write(input)
            self.stdin.close()
            self.stdin = None
            readiness_deadline = time.monotonic() + 15
            while not started.exists():
                if self.poll() is not None or time.monotonic() >= readiness_deadline:
                    pytest.fail("controlled worker did not attach to the Windows job")
                time.sleep(0.02)
            observed.append(self)
            return super().communicate(timeout=min(0.1, timeout))

    monkeypatch.setattr(subprocess, "Popen", ReadyChild)
    args = SimpleNamespace(model=tmp_path/"model", output=tmp_path, synthetic_count=4,
                           batch_sizes=None)
    assert parent(args, sys.executable, "cuda",
                  expected_lease=verify["lease_identity"](lease))["error"] == "worker_timeout"
    assert int(started.read_text(encoding="ascii")) == observed[0].pid
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    assert kernel.WaitForSingleObject(observed[0]._handle, 0) == 0


@pytest.mark.parametrize("wmi_result", ["", "invalid", subprocess.TimeoutExpired("cim", 3)])
def test_missing_grandparent_keeps_verified_direct_parent(verify, monkeypatch, wmi_result):
    monkeypatch.setattr(verify["os"], "getppid", lambda: 27)

    def lookup(*args, **kwargs):
        if isinstance(wmi_result, Exception):
            raise wmi_result
        return wmi_result

    monkeypatch.setattr(subprocess, "check_output", lookup)
    assert verify["worker_ancestors"]() == {27}


def test_batch_requires_distinct_equal_shape_inputs_and_reports_all_requested_sizes(verify):
    choose = verify["batch_groups"]
    first = ("wide", "same-image", "tensor-a")
    samples = [first, first, ("narrow", "image-b", "tensor-b")]
    samples.extend(("wide", f"distinct-{i}", f"tensor-{i}") for i in range(7))
    groups = choose(samples, (2, 4, 8))
    assert [len(groups[n]) for n in (2, 4, 8)] == [2, 4, 8]
    assert list(groups[2]) == list(groups[8])[:2]
    assert choose(samples[:3], (2,)) == {}


def test_report_does_not_write_raw_embeddings(verify, tmp_path):
    report = {"datasets": {"saved": {"cuda": {
        "vectors": ["raw-single-sentinel"],
        "batch": {"2": {"status": "two_rows", "vectors": ["raw-batch-sentinel"]}},
    }}}}
    verify["write_report"](SimpleNamespace(output=tmp_path), report)
    result = (tmp_path / "measurement.json").read_text(encoding="utf-8")
    assert "raw-single-sentinel" not in result
    assert "raw-batch-sentinel" not in result
