"""Summarize complete pairs and export numerical evidence from this run."""

import argparse
from collections import Counter
import json
from math import comb
from pathlib import Path
from statistics import median

from live_gate import ROUND_THREE_PRIOR, RUN_CAPS
from preflight import SCRATCH, WORKTREE, copied_path
from timing import _stats, summarize_pairs, trial_metrics


def paired_estimate(values):
    """Two-sided sign test and a distribution-free interval for the median."""
    ordered = sorted(values)
    n = len(ordered)
    if not n:
        return {}
    lower = sum(v < 0 for v in ordered)
    upper = sum(v > 0 for v in ordered)
    nonzero = lower + upper
    p_value = min(1, 2 * sum(comb(nonzero, k) for k in range(min(lower, upper) + 1))
                  / 2**nonzero)
    candidates = [k for k in range(n // 2)
                  if 2 * sum(comb(n, j) for j in range(k + 1)) / 2**n <= .05]
    limit = [round(ordered[candidates[-1]], 3), round(ordered[~candidates[-1]], 3)] if candidates else None
    return {"n": n, "median_difference": round(median(ordered), 3),
            "nice_faster": lower, "nice_slower": upper, "sign_test_p": round(p_value, 6),
            "median_ci_at_least_95": limit}


def read(root, name, fallback=None):
    path = root / name
    return json.loads(path.read_bytes()) if path.is_file() else fallback


def export(root, out):
    outcome = read(root, "ranking-outcome.json")
    dispatch = read(root, "dispatch.json")
    if outcome is None or dispatch["inflight"]:
        raise ValueError("run has not settled")
    if (root / "fake-wire-only").exists():
        raise ValueError("live export requires live run")
    rows = read(root, "ranking-samples.json", [])
    metadata = read(root, "ranking-metadata.json", [])
    observations = root / "host-observations.jsonl"
    host = [json.loads(line) for line in observations.read_bytes().splitlines()] if observations.is_file() else []
    summary = summarize_pairs(rows)
    count = summary["complete_pairs"]
    paired = {"original": [], "nice": []}
    for sample, stage in zip(rows[:2 * count], metadata[:2 * count], strict=True):
        side = sample["app"]
        if (side, sample["pair"]) != (stage["app"], stage["pair"]):
            raise ValueError("timing points do not align")
        metrics = trial_metrics(sample)
        paired[side].append({
            **metrics, "click_to_list": stage["metadata_ms"],
            "list_to_image": stage["metadata_to_image_ms"], "card": stage["card_call_ms"],
            "render": stage["render_ms"], "pace_adjusted": metrics["total"] - metrics["pace"],
            "preclick_wait": stage.get("preclick_wait_ms"),
        })
    summary["apps"] = {
        side: {key: _stats(row[key] for row in data) for key in data[0]} if data else {}
        for side, data in paired.items()
    }
    summary["paired_nice_minus_flet"] = {
        key: paired_estimate([b[key] - a[key] for a, b in zip(paired["original"], paired["nice"])
                              if b[key] is not None and a[key] is not None])
        for key in ("total", "pace_adjusted", "api", "remaining", "list_to_image")
    }
    by_phase = {item["phase"]: item for item in host}
    by_load = {"quiet": [], "busy": [], "unobserved": []}
    for i, (a, b) in enumerate(zip(paired["original"], paired["nice"]), start=1):
        observation = by_phase.get("process_inventory" if i == 1 else f"before_pair_{i}")
        group = "unobserved" if observation is None else "quiet" if observation["quiet"] else "busy"
        by_load[group].append((a, b))
    summary["by_load_before_pair"] = {
        group: {
            "pairs": len(data), "flet": _stats(a["total"] for a, _ in data),
            "nice": _stats(b["total"] for _, b in data),
            "paired_total_difference": paired_estimate([b["total"] - a["total"] for a, b in data])
        }
        for group, data in by_load.items()
    }
    summary["by_first_app"] = {
        side: {
            key: paired_estimate([b[key] - a[key]
                                  for a, b in zip(paired["original"][offset::2], paired["nice"][offset::2])])
            for key in ("total", "pace_adjusted")
        }
        for offset, side in enumerate(("original", "nice"))
    }
    events = dispatch["events"]
    used = dispatch["count"] - dispatch["prior_attempts"]
    spacing = [(b["start"] - a["end"]) * 1000 for a, b in zip(events, events[1:])]
    counts = Counter(row["kind"] for row in events)
    ledger = {"scope": "live", "status": outcome["status"], "stop": dispatch["stop"],
              "get_used": used, "get_remaining": 180 - dispatch["count"],
              "previous_gets": dispatch["prior_attempts"], "shared_cap": 180,
              "planned_run_cap": RUN_CAPS[dispatch["prior_attempts"]],
              "shortest_end_to_start_ms": round(min(spacing), 3) if spacing else None,
              "destinations": [{"kind": kind, "count": size} for kind, size in sorted(counts.items())],
              "response_status_counts": dict(Counter(row["status"] for row in events))}
    artifacts = {"ranking-outcome.json": outcome, "ranking-samples.json": rows,
                 "ranking-metadata.json": metadata, "ranking-summary.json": summary,
                 "dispatch-summary.json": ledger}
    for name in ("original-serve.json", "nice-serve.json", "prepare-profile-stop.json", "runtime-profile-stop.json",
                 "owned-servers.json", "processes-running.json"):
        value = read(root, name)
        if value is not None:
            artifacts[name] = value
    if host:
        artifacts["host-observations.json"] = host
    for name, value in artifacts.items():
        (out / name).write_bytes((json.dumps(value, ensure_ascii=True, indent=2) + "\n").encode("ascii"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    root = copied_path(SCRATCH, args.root)
    prior = read(root, "dispatch.json")["prior_attempts"]
    evidence_dir = "r2" if prior == ROUND_THREE_PRIOR else "r1"
    export(root, WORKTREE / "verify/d6final-20261122" / evidence_dir)
