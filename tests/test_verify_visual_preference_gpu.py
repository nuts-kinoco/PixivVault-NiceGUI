"""Contracts for the isolated EgaraNet provider comparison."""

from pathlib import Path
import runpy
import sys

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
