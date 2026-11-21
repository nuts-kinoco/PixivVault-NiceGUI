"""Offline G0 benchmark for the existing EgaraNet model.

Run with the isolated GPU interpreter and pass a CPU-only interpreter with
--cpu-python for the unavailable-provider controls. --images accepts a JSON
array of paths relative to the manifest for saved originals. An array entry
may instead map "original", "view", and "thumb" to local paths. Missing view
and thumb variants use the product's local preview resize and encoding.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from importlib.metadata import distributions
import io
from itertools import chain
import json
import math
import os
from pathlib import Path
from statistics import fmean
import struct
import subprocess
import sys
from threading import Event, Thread, Timer
import time

from pixivvault_nicegui.egara_inference import (
    CUDA_OPTIONS, MAX_INFERENCE_IMAGE_BYTES, PROVIDER_NAMES, EgaraInference,
    EgaraInferenceError, preprocess,
)
from pixivvault_nicegui.managed_downloads import _safe_path


LEASE = Path(r"S:\Work\_tmp\_gpu_lease\lease.py")
GPU_RUN_LOCK = LEASE.parent.parent / "PV" / "g0-20261122" / "cuda-verifier.lock"
VARIANTS = ("original", "view", "thumb")
CPU = PROVIDER_NAMES["cpu"]
CUDA = PROVIDER_NAMES["cuda"]


def distribution(values):
    values = sorted(values)
    def quantile(fraction):
        at = (len(values) - 1) * fraction
        lower, upper = math.floor(at), math.ceil(at)
        return values[lower] + (values[upper] - values[lower]) * (at - lower)

    return {"n": len(values), "min": values[0], "p50": quantile(0.5),
            "p95": quantile(0.95), "max": values[-1], "mean": fmean(values)}


def node_placement(events):
    assignments = set()
    for event in events:
        args = event.get("args", {})
        name = event.get("name", "")
        if event.get("cat") == "Node" and name.endswith("_kernel_time") and args.get("provider"):
            assignments.add((name.removesuffix("_kernel_time"),
                             args.get("op_name", ""), args["provider"]))
    compute, cpu_nodes, transfers = Counter(), [], []
    for name, op, provider in sorted(assignments):
        node = {"name": name, "op": op}
        if op.startswith("Memcpy"):
            transfers.append(node)
            continue
        compute[provider] += 1
        if provider == CPU:
            cpu_nodes.append(node)
    total = sum(compute.values())
    return {"compute_by_provider": dict(compute),
            "cuda_fraction": compute[CUDA] / total if total else None,
            "cpu_nodes": cpu_nodes, "transfer_nodes": transfers}


def compare_vectors(reference, candidate, k):
    n = len(reference)
    if n != len(candidate) or n < 3 or not 1 <= k < n:
        raise ValueError("invalid aligned vector corpus or k")
    dimension = len(reference[0])

    def normalized(rows):
        result = []
        for row in rows:
            norm = math.hypot(*row)
            if len(row) != dimension or not math.isfinite(norm) or norm <= 0:
                raise ValueError("invalid embedding")
            result.append(tuple(value / norm for value in row))
        return result

    reference, candidate = normalized(reference), normalized(candidate)

    def rank(rows, query):
        return sorted((index for index in range(n) if index != query),
                      key=lambda index: (-math.sumprod(rows[query], rows[index]), index))

    overlaps, correlations = [], []
    exact_k = exact_ranks = 0
    for query in range(n):
        baseline, other = rank(reference, query), rank(candidate, query)
        exact_k += baseline[:k] == other[:k]
        exact_ranks += baseline == other
        overlaps.append(len(set(baseline[:k]).intersection(other[:k])) / k)
        other_positions = {index: position for position, index in enumerate(other)}
        squared = sum((position - other_positions[index]) ** 2
                      for position, index in enumerate(baseline))
        size = n - 1
        correlations.append(1 - 6 * squared / (size * (size ** 2 - 1)))
    paired = [max(-1., min(1., math.sumprod(left, right)))
              for left, right in zip(reference, candidate, strict=True)]
    return {"query_count": n, "k": k, "paired_cosine": distribution(paired),
            "top_k_overlap": fmean(overlaps), "exact_top_k_queries": exact_k,
            "exact_full_rank_queries": exact_ranks, "mean_spearman": fmean(correlations)}


def first_inference(predictor, data):
    started = time.perf_counter()
    error = None
    try:
        vectors = predictor.infer(data)
    except EgaraInferenceError as exc:
        vectors = None
        error = type(exc).__name__
    return vectors, {"provider_requested": predictor.provider_requested,
                     "provider_active": predictor.provider_active,
                     "fallback_reason": predictor.fallback_reason,
                     "first_inference_ms": (time.perf_counter() - started) * 1000,
                     "usable_embedding": vectors is not None, "error": error}


def batch_result(outputs):
    if not outputs:
        return {"status": "rejected"}
    shapes = [list(output.shape) for output in outputs]
    return {"status": "two_rows" if all(shape and shape[0] == 2 for shape in shapes)
            else "collapsed", "output_shapes": shapes}


def probe_batch(predictor, data, other_data=None):
    import numpy as np

    tensor = preprocess(data)
    # Probe raw ORT output. EgaraInference.infer intentionally accepts one image.
    other = (preprocess(other_data) if other_data is not None
             else np.ascontiguousarray(tensor[:, :, :, ::-1]))
    input_name = predictor._input_name
    batch = np.concatenate((tensor, other), axis=0)
    try:
        outputs = predictor._session.run(None, {input_name: batch})
        result = batch_result(outputs)
        if result["status"] == "two_rows":
            singles = [predictor._session.run(None, {input_name: one}) for one in (tensor, other)]
            pairs = [(output[row:row + 1], singles[row][column])
                     for column, output in enumerate(outputs) for row in range(2)]
            matches = [actual.shape == expected.shape
                       and bool(np.allclose(actual, expected, rtol=1e-5, atol=1e-6))
                       for actual, expected in pairs]
            result["distinct_single_style_vectors"] = not np.allclose(
                singles[0][0], singles[1][0], rtol=1e-5, atol=1e-6,
            )
            result["independent_rows_match"] = all(matches) and result["distinct_single_style_vectors"]
            result["max_abs_error_by_output_and_row"] = [
                float(np.max(np.abs(actual - expected))) if actual.shape == expected.shape else None
                for actual, expected in pairs
            ]
    except Exception as exc:
        if not isinstance(exc, (ValueError, RuntimeError)) and not type(exc).__module__.startswith("onnxruntime."):
            raise
        result = batch_result(None) | {"error": type(exc).__name__}
        if "reshape" in str(exc).lower():
            result["error_operation"] = "Reshape"
    return result | {"input_shape": list(batch.shape)}


def block_network(event, _args):
    if event in {"socket.connect", "socket.sendto", "socket.getaddrinfo",
                 "socket.gethostbyname", "socket.gethostbyaddr", "socket.getnameinfo"}:
        raise RuntimeError("verification requires offline inputs")


def s_path(path):
    path = Path(path).absolute()
    if path.drive.upper() != "S:" or path.resolve().drive.upper() != "S:":
        raise ValueError("verification paths must stay on S:")
    return path


def read_image(path):
    _safe_path(path, file=True)
    with path.open("rb") as stream:
        return stream.read(MAX_INFERENCE_IMAGE_BYTES + 1)


def synthetic_original(index):
    from PIL import Image, ImageDraw

    width, height = ((2304, 1504), (1504, 2304), (2080, 2080))[index % 3]
    image = Image.new("RGB", (width, height), (30 + index * 7 % 196, 47, 86))
    draw = ImageDraw.Draw(image)
    for step in range(28):
        x, y = (step * (137 + index * 11)) % width, (step * (181 + index * 17)) % height
        color = ((step * 17 + index * 9) % 256, (step * 23) % 256, (index * 29) % 256)
        draw.ellipse((x, y, x + 72 + (step % 5) * 140, y + 240), fill=color)
        draw.line((x, y, height - y, width - x), fill=color, width=3 + index % 5)
    with io.BytesIO() as stream:
        image.save(stream, format="PNG")
        return stream.getvalue()


def resized_variant(data, side):
    from PIL import Image

    with Image.open(io.BytesIO(data), formats=["PNG", "JPEG", "WEBP", "GIF"]) as image, io.BytesIO() as stream:
        image.thumbnail((side, side))
        mode = "RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB"
        image.convert(mode).save(stream, format="PNG")
        return stream.getvalue()


def corpus(args):
    if args.images is None:
        entries = range(args.synthetic_count)
    else:
        _safe_path(args.images, file=True)
        entries = json.loads(args.images.read_text(encoding="utf-8-sig"))
        if not isinstance(entries, list) or not 3 <= len(entries) <= 64:
            raise ValueError("manifest needs 3 to 64 paired images")
    for entry in entries:
        if args.images is None:
            supplied = {"original": synthetic_original(entry)}
        else:
            paths = {"original": entry} if isinstance(entry, str) else entry
            if not isinstance(paths, dict) or "original" not in paths or set(paths) - set(VARIANTS):
                raise ValueError("invalid variant manifest entry")
            supplied = {}
            root = args.images.parent.resolve()
            for variant, name in paths.items():
                path = args.images.parent / name
                if not path.resolve().is_relative_to(root):
                    raise ValueError("image paths must be inside the isolated manifest directory")
                supplied[variant] = read_image(path)
        original = supplied["original"]
        yield [(variant, supplied[variant] if variant in supplied else resized_variant(original, side),
                "supplied" if variant in supplied else "derived")
               for variant, side in zip(VARIANTS, (0, 1920, 192), strict=True)]


def runtime(ort):
    packages = {p.metadata["Name"]: p.version for p in distributions()
                if p.metadata["Name"].lower().startswith(("onnxruntime", "nvidia-", "numpy", "pillow"))}
    return {"python": sys.version.split()[0], "ort": ort.__version__, "packages": packages,
            "available_providers": ort.get_available_providers(),
            "ort_build_info": ort.get_build_info()}


def raw_lease():
    try:
        return json.loads(LEASE.with_suffix(".json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def worker_ancestors():
    # CPython's Windows venv redirector can be between parent and worker.
    parent = os.getppid()
    query = f"(Get-CimInstance Win32_Process -Filter 'ProcessId={parent}').ParentProcessId"
    output = subprocess.check_output(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", query], text=True,
    )
    return {parent, int(output.strip())}


def validate_cuda_lease(current, *, expected_pid, ancestors, now):
    if (not current or current["owner"] != "PV" or current["pid"] != expected_pid
            or expected_pid not in ancestors or current["expires"] - now < 60):
        raise RuntimeError("CUDA worker requires its own live PV lease with time remaining")
    return current["expires"]


def benchmark(args):
    provider = args.worker.removesuffix("-unavailable")
    if args.worker != "cuda":
        return benchmark_samples(args, provider)

    ancestors = worker_ancestors()

    def check_cuda():
        return validate_cuda_lease(raw_lease(), expected_pid=args.lease_pid,
                                   ancestors=ancestors, now=time.time())

    expires = check_cuda()
    cache = s_path(Path(os.environ["TEMP"]) / "cuda-kernel-cache")
    cache.mkdir(exist_ok=True)
    os.environ["CUDA_CACHE_PATH"] = str(cache)
    os.environ["CUDA_VISIBLE_DEVICES"] = "0"
    # End this owned worker before the lease expires if a native call stalls.
    # The supervising process observes the exit and performs lease release.
    watchdog = Timer(expires - time.time() - 5, os._exit, args=(124,))
    watchdog.daemon = True
    watchdog.start()
    try:
        return benchmark_samples(args, provider, check_cuda)
    finally:
        watchdog.cancel()
        watchdog.join()


def benchmark_samples(args, provider, check_cuda=None):
    import onnxruntime as ort

    # CPU-only ORT is required for failure controls, avoiding any CUDA attempt.
    if args.worker.endswith("-unavailable") and CUDA in ort.get_available_providers():
        raise ValueError("failure controls require a CPU-only ORT wheel")
    samples = iter(corpus(args))
    first = next(samples)
    start = time.perf_counter()
    predictor = EgaraInference(args.model, provider=provider)
    verified_ms = (time.perf_counter() - start) * 1000
    try:
        if check_cuda:
            check_cuda()
        vectors, outcome = first_inference(predictor, first[0][1])
        result = {"model_sha256": predictor.model_sha256, "model_check_ms": verified_ms,
                  "outcome": outcome, "runtime": runtime(ort),
                  "pid": os.getpid(), "cuda_options": CUDA_OPTIONS if provider == "cuda" else None}
        if vectors is None or args.cold_only or args.worker.endswith("-unavailable"):
            return result
        session = predictor._session
        result["inputs"] = [{"name": item.name, "shape": item.shape, "type": item.type}
                            for item in session.get_inputs()]
        result["outputs"] = [{"name": item.name, "shape": item.shape, "type": item.type}
                             for item in session.get_outputs()]
        del session
        timings = {variant: [] for variant in VARIANTS}
        vectors = {variant: [] for variant in VARIANTS}
        fingerprints = []
        for sample in chain((first,), samples):
            row = {}
            for variant, data, source in sample:
                if check_cuda:
                    check_cuda()
                start = time.perf_counter()
                output = predictor.infer(data)
                timings[variant].append((time.perf_counter() - start) * 1000)
                vectors[variant].append({kind: bits.hex() for kind, bits in output.items()})
                row[variant] = {"sha256": hashlib.sha256(data).hexdigest(), "source": source}
            fingerprints.append(row)
        result.update(steady_ms={key: distribution(values) for key, values in timings.items()},
                      fingerprints=fingerprints, vectors=vectors)
        # This experimental two-image call is intentionally kept off CUDA, so
        # the GPU peak measures only the supported one-image workload.
        if provider == "cpu":
            other = synthetic_original(3) if args.images is None and args.synthetic_count > 3 else None
            result["batch"] = probe_batch(predictor, first[0][1], other)
    finally:
        predictor.close()

    def profiled_session(path, options, selected):
        options.enable_profiling = True
        options.profile_file_prefix = str(args.output / f"{args.label}-{provider}-ort")
        name = PROVIDER_NAMES[selected]
        providers = [(name, dict(CUDA_OPTIONS)), CPU] if selected == "cuda" else [name]
        return ort.InferenceSession(str(path), options, providers=providers)

    predictor = EgaraInference(args.model, provider=provider, session_factory=profiled_session)
    try:
        if check_cuda:
            check_cuda()
        predictor.infer(first[0][1])
        profile = Path(predictor._session.end_profiling())
        result["profile"] = profile.name
        result["node_placement"] = node_placement(json.loads(profile.read_text(encoding="utf-8")))
    finally:
        predictor.close()
    return result


def run_worker(args, python, mode, *, cold_only=False, manifest=None, label="synthetic"):
    command = [str(python), "-B", str(Path(__file__).resolve()), "--model", str(args.model),
               "--output", str(args.output), "--worker", mode, "--label", label,
               "--synthetic-count", str(args.synthetic_count), "--lease-pid", str(os.getpid())]
    if cold_only:
        command.append("--cold-only")
    if manifest is not None:
        command.extend(("--images", str(manifest)))
    start = time.perf_counter()
    process = subprocess.run(command, capture_output=True, encoding="utf-8", errors="replace")
    elapsed = (time.perf_counter() - start) * 1000
    # Native runtime output may precede the final JSON. Refrain from forwarding
    # diagnostics containing filenames or model runtime state.
    for line in reversed(process.stdout.splitlines()):
        if line.startswith('{"worker_result":'):
            return json.loads(line)["worker_result"] | {"process_wall_ms": elapsed,
                                                        "exit_code": process.returncode}
    return {"error": "no_worker_result", "exit_code": process.returncode}


def suite(args, mode, manifest, label):
    run = run_worker(args, sys.executable, mode, manifest=manifest, label=label)
    if "vectors" not in run:
        return run
    cold = [run]
    for _ in range(args.cold_runs - 1):
        cold.append(run_worker(args, sys.executable, mode, cold_only=True,
                               manifest=manifest, label=label))
    measured = [trial for trial in cold if trial.get("outcome", {}).get("usable_embedding")]
    run["cold"] = {
        "first_inference_ms": distribution([trial["outcome"]["first_inference_ms"] for trial in measured]),
        "model_check_through_first_ms": distribution([
            trial["model_check_ms"] + trial["outcome"]["first_inference_ms"] for trial in measured]),
        "trials": [{"outcome": trial.get("outcome"), "model_check_ms": trial.get("model_check_ms"),
                    "error": trial.get("error"), "pid": trial.get("pid")} for trial in cold],
    }
    return run


def lease_status():
    result = subprocess.check_output([sys.executable, "-B", str(LEASE), "status"],
                                     encoding="utf-8", errors="replace")
    return json.loads(result)


@contextmanager
def gpu_run_lock(path):
    import msvcrt

    with path.open("a+b") as stream:
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            yield
        finally:
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


@contextmanager
def sample_vram(samples):
    command = ["nvidia-smi", "-i", "0", "--query-gpu=memory.used",
               "--format=csv,noheader,nounits", "--loop-ms=100"]
    initial = Event()
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          text=True, encoding="utf-8", errors="replace") as monitor:
        def collect():
            for line in monitor.stdout:
                if line.strip().isdigit():
                    samples.append(int(line))
                    initial.set()

        reader = Thread(target=collect)
        reader.start()
        try:
            if not initial.wait(5):
                raise RuntimeError("GPU memory baseline unavailable")
            yield
        finally:
            monitor.terminate()
            monitor.wait()
            reader.join()


def verify_cuda(args, datasets):
    GPU_RUN_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with gpu_run_lock(GPU_RUN_LOCK):
        return run_leased_cuda(args, datasets)


def run_leased_cuda(args, datasets):
    status = lease_status()
    if status["lease"] and status["lease"]["owner"] == "PV":
        return {"status": "existing_pv_lease"}, {}
    gpu = subprocess.check_output(["nvidia-smi", "-i", "0",
                                   "--query-gpu=name,driver_version,compute_cap,memory.total",
                                   "--format=csv,noheader,nounits"], text=True).strip()
    args_id = ["--owner", "PV"]
    command = [sys.executable, "-B", str(LEASE)]
    evidence = {"status": "unavailable", "gpu_name_driver_cc_total_mib": gpu,
                "initial_free_gib": status["free_gib"], "requested_gib": args.vram_gib}
    if status["free_gib"] is None:
        return evidence | {"error": "free_vram_unavailable"}, {}
    samples, runs = [], {}
    try:
        acquired = subprocess.run([*command, "acquire", *args_id, "--purpose",
                                   "EgaraNet CUDA 検証", "--vram-gib", str(args.vram_gib),
                                   "--minutes", "30", "--wait", str(args.lease_wait),
                                   "--pid", str(os.getpid())], capture_output=True)
        evidence["acquire_exit_code"] = acquired.returncode
        if acquired.returncode == 0:
            validate_cuda_lease(raw_lease(), expected_pid=os.getpid(),
                                ancestors={os.getpid()}, now=time.time())
            evidence["status"] = "acquired"
            free = lease_status()["free_gib"]
            evidence["acquired_free_gib"] = free
            if free is None or free < args.vram_gib + 1.5:
                raise RuntimeError("GPU memory margin unavailable after acquisition")
            with sample_vram(samples):
                for label, manifest in datasets:
                    runs[label] = suite(args, "cuda", manifest, label)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        evidence["error"] = type(exc).__name__
    finally:
        raw = raw_lease()
        if raw and raw["owner"] == "PV" and raw["pid"] == os.getpid():
            if samples:
                evidence["last_sample_before_release_mib"] = samples[-1]
            released = subprocess.run([*command, "release", *args_id], capture_output=True)
            evidence["release_exit_code"] = released.returncode
        raw = raw_lease()
        evidence["owned_lease_remaining"] = bool(raw and raw["owner"] == "PV" and raw["pid"] == os.getpid())
    if samples:
        evidence["adapter_vram_mib"] = {"scope": "whole adapter, 100 ms sampling",
                                        "initial": samples[0], "sampled_peak": max(samples),
                                        "peak_above_initial": max(samples) - samples[0],
                                        "samples": samples}
    return evidence, runs


def metrics(reference, candidate, k):
    return {kind: compare_vectors(
        [struct.unpack("<1024f", bytes.fromhex(row[kind])) for row in reference],
        [struct.unpack("<1024f", bytes.fromhex(row[kind])) for row in candidate],
        min(k, len(reference) - 1),
    ) for kind in reference[0]}


def write_report(args, report):
    datasets = {}
    for label, data in report["datasets"].items():
        datasets[label] = {}
        for name, run in data.items():
            public = {key: value for key, value in run.items() if key != "fingerprints"}
            if "fingerprints" in run:
                images = run["fingerprints"]
                public["corpus_sha256"] = hashlib.sha256(
                    json.dumps(images, sort_keys=True).encode("utf-8"),
                ).hexdigest()
                public["variant_sources"] = {
                    variant: dict(Counter(row[variant]["source"] for row in images)) for variant in VARIANTS
                }
            datasets[label][name] = public
    output = args.output / "measurement.json"
    staged = output.with_suffix(".pending")
    staged.write_text(json.dumps(report | {"datasets": datasets}, indent=2), encoding="utf-8")
    staged.replace(output)


def measurement_exit_code(report, *, skip_gpu):
    controls = report["failure_controls"]
    auto = controls.get("auto-unavailable", {}).get("outcome", {})
    explicit = controls.get("cuda-unavailable", {}).get("outcome", {})
    if (auto.get("provider_active") != "cpu" or auto.get("usable_embedding") is not True
            or explicit.get("provider_active") is not None or explicit.get("usable_embedding") is not False):
        return 2
    if not skip_gpu and (report["lease"]["status"] != "acquired" or "error" in report["lease"]
                         or report["lease"].get("owned_lease_remaining") is not False
                         or report["lease"].get("release_exit_code") != 0):
        return 2
    for data in report["datasets"].values():
        cpu = data["cpu"]
        if cpu.get("node_placement", {}).get("cuda_fraction") != 0:
            return 2
        checked = [cpu]
        if not skip_gpu:
            gpu = data.get("cuda", {})
            fraction = gpu.get("node_placement", {}).get("cuda_fraction")
            if not fraction or "provider_comparison" not in data:
                return 2
            checked.append(gpu)
        for run in checked:
            samples = run.get("cold", {}).get("first_inference_ms", {}).get("n")
            if samples != report["cold_runs_requested"]:
                return 2
    return 0


def orchestrate(args):
    args.output.mkdir(parents=True, exist_ok=True)
    datasets = [("synthetic", None)]
    if args.images:
        datasets.append(("saved", args.images))
    report = {"started_utc": datetime.now(timezone.utc).isoformat(),
              "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "cold_runs_requested": args.cold_runs, "synthetic_count": args.synthetic_count,
              "k_requested": args.k, "datasets": {}, "failure_controls": {},
              "lease": {"status": "cpu_only" if args.skip_gpu else "pending"}}
    cpu_vectors = {}
    for label, manifest in datasets:
        print(f"CPU: {label}", flush=True)
        run = suite(args, "cpu", manifest, label)
        vectors = run.pop("vectors", None)
        if vectors is not None:
            cpu_vectors[label] = vectors
            run["variant_comparison"] = {
                f"{variant}_vs_{reference}": metrics(vectors[reference], vectors[variant], args.k)
                for variant, reference in (("view", "original"), ("thumb", "original"), ("thumb", "view"))
            }
        report["datasets"][label] = {"cpu": run}
    for control in ("auto-unavailable", "cuda-unavailable"):
        report["failure_controls"][control] = run_worker(args, args.cpu_python, control)
    write_report(args, report)
    if not args.skip_gpu and cpu_vectors:
        print("Waiting for the GPU lease", flush=True)
        try:
            report["lease"], runs = verify_cuda(args, datasets)
        except (OSError, subprocess.SubprocessError) as exc:
            report["lease"], runs = {"status": "gpu_setup_error", "error": type(exc).__name__}, {}
        for label, run in runs.items():
            gpu_vectors = run.pop("vectors", None)
            data = report["datasets"][label]
            data["cuda"] = run
            if gpu_vectors is not None and label in cpu_vectors:
                if (data["cpu"]["fingerprints"] != run["fingerprints"]
                        or data["cpu"]["model_sha256"] != run["model_sha256"]):
                    raise ValueError("providers used different images or model weights")
                data["provider_comparison"] = {variant: metrics(cpu_vectors[label][variant],
                                                              gpu_vectors[variant], args.k)
                                               for variant in VARIANTS}
        write_report(args, report)
    return measurement_exit_code(report, skip_gpu=args.skip_gpu)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, type=s_path)
    parser.add_argument("--output", required=True, type=s_path)
    parser.add_argument("--images", type=s_path)
    parser.add_argument("--cpu-python", type=s_path)
    parser.add_argument("--synthetic-count", type=int, default=12)
    parser.add_argument("--cold-runs", type=int, default=3)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--skip-gpu", action="store_true")
    parser.add_argument("--vram-gib", type=float, default=4)
    parser.add_argument("--lease-wait", type=int, default=1800)
    parser.add_argument("--worker", choices=("cpu", "cuda", "auto-unavailable", "cuda-unavailable"),
                        help=argparse.SUPPRESS)
    parser.add_argument("--lease-pid", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--label", choices=("synthetic", "saved"), default="synthetic", help=argparse.SUPPRESS)
    parser.add_argument("--cold-only", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 3 <= args.synthetic_count <= 64 or min(args.k, args.cold_runs) < 1:
        parser.error("expected 3 to 64 synthetic images and positive k and cold run count")
    if not math.isfinite(args.vram_gib) or args.vram_gib <= 0 or args.lease_wait < 0:
        parser.error("VRAM must be positive, and lease wait must be nonnegative")
    sys.addaudithook(block_network)
    if args.worker:
        try:
            result = benchmark(args)
        except Exception as exc:
            result = {"error": type(exc).__name__, "pid": os.getpid()}
        print(json.dumps({"worker_result": result}))
        return 2 if "error" in result else 0
    if args.cpu_python is None:
        parser.error("--cpu-python is required")
    return orchestrate(args)


if __name__ == "__main__":
    sys.exit(main())
