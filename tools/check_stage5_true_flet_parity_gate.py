"""Fail-closed real original gui.py versus NiceGUI eight-tab comparison gate.

The old P3-369/370 images are genuine GUI captures, but are NOT same-data,
same-capture or full behavioral parity. This checker inspects immutable
historical receipts without restarting either app. Later runs may attach
strictly verified per-tab operation/state/failure proofs, never inferred from
successful navigation screenshots.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FLET = ROOT / "artifacts/p3-369-real-original-full-flet-gui-r2"
NICE = ROOT / "artifacts/p3-370-nicegui-configured-fullshape-r1"
TABS = (
    ("unified", "download"), ("queue", "queue"), ("failed", "failed"),
    ("dedup", "dedup"), ("learning", "learning"), ("ranking", "ranking"),
    ("library", "library"), ("recommend", "recommend"),
)
EXPECTED_ORIGINAL_SHA = "731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939"
REQUIRED_PROOFS = ("screen", "operation", "preferences", "persistence", "failure_recovery")


def sha(path):
    return sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def verified_proof(item, tab, label):
    """Check actual on-disk bounded, SHA-pinned structured evidence."""
    if not isinstance(item, dict) or item.get("verdict") != "PASS":
        return False
    relative = item.get("proof_path")
    hexdigest = item.get("proof_sha256")
    if not isinstance(relative, str) or not isinstance(hexdigest, str):
        return False
    loc = Path(relative)
    if (loc.is_absolute() or ".." in loc.parts or len(hexdigest) != 64
            or not loc.parts or loc.parts[0] != "artifacts"):
        return False
    file = ROOT / loc
    if not file.resolve().is_relative_to(ROOT.resolve()) or file.is_symlink():
        return False
    try:
        if not file.is_file() or file.stat().st_size > 8 * 1024 * 1024:
            return False
        with file.open("rb") as handle:
            payload = handle.read(8 * 1024 * 1024 + 1)
            if len(payload) > 8 * 1024 * 1024 or os.fstat(handle.fileno()).st_size != len(payload):
                return False
        if sha256(payload).hexdigest() != hexdigest:
            return False
        doc = json.loads(payload.decode("utf-8"))
    except (OSError, ValueError, UnicodeError):
        return False
    return (isinstance(doc, dict) and doc.get("tab") == tab
            and doc.get("proof_kind") == label and doc.get("verdict") == "PASS"
            and doc.get("flet_original_real_gui") is True
            and doc.get("nicegui_actual_gui") is True
            and doc.get("same_initial_state") is True
            and doc.get("same_ordered_actual_actions") is True
            and doc.get("observed_identical_effect") is True
            and isinstance(doc.get("source_before_sha256"), str)
            and len(doc["source_before_sha256"]) == 64
            and doc.get("source_before_sha256") == doc.get("source_after_sha256")
            and isinstance(doc.get("events"), list) and len(doc["events"]) > 0
            and all(isinstance(event, dict)
                    and isinstance(event.get("action"), str) and event["action"]
                    and isinstance(event.get("observed"), str) and event["observed"]
                    for event in doc["events"]))


def assess(flet_dir=FLET, nice_dir=NICE, *, full_proofs=None):
    """Returns diagnostic results, never conflating displayed tabs with parity."""
    result = {"schema": 1, "scope": "full original gui.py 8 tabs vs current NiceGUI",
              "formal_pass_added": 0, "release_ready": False, "rows": [],
              "status": "UNVERIFIED",
              "trust_boundary": "diagnostic only: no independent cross-app source/owner/review attestation is fixed"}
    original_ready = read(flet_dir / "actual-original-full-gui-ready.json")
    original_run_sha = sha(flet_dir / "actual-original-full-gui-ready.json")
    next_result = read(nice_dir / "result.json")
    original_ok = (
        original_ready.get("actual_original_gui_py_main_window_executed") is True
        and original_ready.get("actual_original_full_tab_ui_constructed") is True
        and original_ready.get("original_gui_py_sha256") == EXPECTED_ORIGINAL_SHA
        and original_ready.get("original_db_cookie_used") is False
        and original_ready.get("source_workers_started") == 0
    )
    next_ok = (
        next_result.get("success") is True
        and next_result.get("all_eight_visible") is True
        and next_result.get("browser_external_requests") == 0
        and next_result.get("page_errors") == 0
        and next_result.get("source_changed") is False
        and next_result.get("harness_changed") is False
        and next_result.get("app", {}).get("normal_shutdown") is True
        and next_result.get("app", {}).get("owned_tree", {}).get("remaining_count") == 0
        and next_result.get("app", {}).get("port_released") is True
    )
    next_tabs = next_result.get("tabs", [])
    tab_map = {r.get("tab"): r for r in next_tabs if isinstance(r, dict)}
    if len(tab_map) != 8 or len(next_tabs) != 8:
        next_ok = False
    for flet_tab, nice_tab in TABS:
        reasons = []
        flet_meta = read(flet_dir / f"original-full-{flet_tab}-native.json")
        flet_img = flet_dir / f"original-full-{flet_tab}-native.png"
        nice_meta = tab_map.get(nice_tab, {})
        nice_img = nice_dir / f"nicegui-configured-{nice_tab}.png"
        if not original_ok or not next_ok:
            reasons.append("source or application lifetime identity unverified")
        if original_ready.get("normal_exit_pending") is True:
            reasons.append("original full GUI normal exit not independently bound to this comparison")
        if (flet_meta.get("tab") != flet_tab or
                flet_meta.get("real_original_gui_py_main_window") is not True or
                flet_meta.get("ready_sha256") != original_run_sha or
                flet_meta.get("original_source_writes") is not False):
            reasons.append("real original tab owner or immutable source not verified")
        if (not flet_img.is_file() or sha(flet_img) != flet_meta.get("image_sha256") or
                not nice_img.is_file() or sha(nice_img) != nice_meta.get("png_sha256") or
                nice_meta.get("screen_success") is not True or
                nice_meta.get("available") is not True):
            reasons.append("actual GUI capture bytes / visible navigation unverified")
        same_pixels = flet_meta.get("pixel_dimensions") == nice_meta.get("pixel_dimensions")
        if not same_pixels or flet_meta.get("dpi") != 96 or next_result.get("nicegui_DPR") != 1:
            reasons.append("physical pixels or DPI/DPR mismatch")
        if flet_meta.get("capture_backend") != next_result.get("capture_backend"):
            # The historical NiceGUI record never claims a physical GDI capture.
            reasons.append("physical capture backend differs or is unverified")
        if next_result.get("flet_and_nicegui_start_datasets_equal") is not True:
            reasons.append("original and NiceGUI starting dataset not identical")
        proof = full_proofs.get(nice_tab) if isinstance(full_proofs, dict) else None
        if not isinstance(proof, dict):
            reasons.append("per-tab genuine operation/preference/state/failure evidence absent")
        else:
            for label in REQUIRED_PROOFS:
                if not verified_proof(proof.get(label), nice_tab, label):
                    reasons.append(f"{label} exact SHA-bound real-GUI proof missing")
            if (proof.get("flet_fixture_sha256") is None or
                    proof.get("flet_fixture_sha256") != proof.get("nicegui_fixture_sha256") or
                    proof.get("capture_backend") != flet_meta.get("capture_backend") or
                    proof.get("identical_operation_sequence") is not True or
                    not verified_proof(proof.get("independent_review"), nice_tab, "independent_review")):
                reasons.append("common fixture, same capture, actions or independent review absent")
        # P1: an arbitrary candidate can self-issue all five PASS files and an
        # "independent review" JSON with matching SHA. There is no pinned
        # separately owned independent adjudication/identity trust root here.
        # Explicitly disallow SCOPED_PASS until a new version of this checker
        # binds real owned process termination, fixture bytes and review to
        # non-self-issued immutable evidence. See P3-384 RED reproduction.
        reasons.append("independent full-product attestation trust root absent: no PASS eligible")
        result["rows"].append({
            "original_tab": flet_tab, "nicegui_tab": nice_tab,
            "real_original_gui_seen": original_ok and flet_meta.get("real_original_gui_py_main_window") is True,
            "nicegui_actual_tab_seen": next_ok and nice_meta.get("screen_success") is True,
            "same_pixels": same_pixels,
            "status": "INCOMPLETE" if reasons else "SCOPED_PASS",
            "remaining": reasons,
        })
    result["display_only_pairs"] = sum(
        x["real_original_gui_seen"] and x["nicegui_actual_tab_seen"] for x in result["rows"]
    )
    result["complete_pairs"] = sum(x["status"] == "SCOPED_PASS" for x in result["rows"])
    result["status"] = "SCOPED_PASS" if result["complete_pairs"] == 8 else "UNVERIFIED"
    # Even eight scoped passes do not independently close the 152 original IDs.
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    dest = args.output.resolve()
    if dest.exists() or not dest.is_relative_to((ROOT / "artifacts").resolve()):
        raise ValueError("fresh dedicated artifacts output only")
    result = assess()
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, ensure_ascii=False)
    print(json.dumps({"status": result["status"], "display_only_pairs": result["display_only_pairs"],
                      "complete_pairs": result["complete_pairs"], "formal_pass_added": 0}))
    # CI must not mistake successful diagnostic execution for a parity PASS.
    return 0 if result["status"] == "SCOPED_PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
