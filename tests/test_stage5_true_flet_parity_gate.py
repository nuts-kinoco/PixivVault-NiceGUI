"""Eight original real-gui tab screenshots must not become a feature PASS."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json

from tools import check_stage5_true_flet_parity_gate as parity


def test_historical_full_original_flet_eight_tab_images_are_display_only():
    receipt = parity.assess()
    assert receipt["display_only_pairs"] == 8
    assert receipt["complete_pairs"] == 0
    assert receipt["status"] == "UNVERIFIED"
    assert receipt["formal_pass_added"] == 0
    assert [r["original_tab"] for r in receipt["rows"]] == [x[0] for x in parity.TABS]
    assert all(r["real_original_gui_seen"] and r["nicegui_actual_tab_seen"]
               for r in receipt["rows"])
    assert all(r["status"] == "INCOMPLETE" and
               "original and NiceGUI starting dataset not identical" in r["remaining"]
               and "physical capture backend differs or is unverified" in r["remaining"]
               for r in receipt["rows"])


def test_merely_claiming_same_input_does_not_certify_real_actions(tmp_path):
    # Do not mutate the frozen evidence: only alter a generated receipt copy.
    sandbox = tmp_path / "mock-nice"
    sandbox.mkdir()
    original = parity.read(parity.NICE / "result.json")
    candidate = deepcopy(original)
    candidate["flet_and_nicegui_start_datasets_equal"] = True
    candidate["capture_backend"] = "Flet_owned_native_PrintWindow"
    (sandbox / "result.json").write_text(json.dumps(candidate), encoding="utf-8")
    for _, nice_tab in parity.TABS:
        original_png = parity.NICE / f"nicegui-configured-{nice_tab}.png"
        (sandbox / original_png.name).write_bytes(original_png.read_bytes())
    observed = parity.assess(nice_dir=sandbox)
    assert observed["display_only_pairs"] == 8
    assert observed["complete_pairs"] == 0
    assert all("per-tab genuine operation/preference/state/failure evidence absent"
               in row["remaining"] for row in observed["rows"])


def test_unverified_or_fabricated_independent_proofs_cannot_pass():
    proofs = {}
    for _, tab in parity.TABS:
        proofs[tab] = {
            name: {"verdict": "PASS", "proof_path": "artifacts/missing.json",
                   "proof_sha256": "0" * 64}
            for name in (*parity.REQUIRED_PROOFS, "independent_review")
        }
        proofs[tab].update({
            "flet_fixture_sha256": "0" * 64,
            "nicegui_fixture_sha256": "0" * 64,
            "capture_backend": "Flet_owned_native_PrintWindow",
            "identical_operation_sequence": True,
        })
    observed = parity.assess(full_proofs=proofs)
    assert observed["status"] == "UNVERIFIED"
    assert observed["complete_pairs"] == 0
    assert all(any("SHA-bound real-GUI proof missing" in reason
                   for reason in row["remaining"]) for row in observed["rows"])


def test_sha_valid_self_certified_evidence_must_never_make_full_parity_green(tmp_path, monkeypatch):
    """P1 RED: a self-issued JSON with valid SHA is not independent evidence."""
    monkeypatch.setattr(parity, "ROOT", tmp_path)
    store = tmp_path / "artifacts"
    store.mkdir()
    pretend_flet = tmp_path / "candidate-flet"
    pretend_flet.mkdir()
    ready = deepcopy(parity.read(parity.FLET / "actual-original-full-gui-ready.json"))
    ready["normal_exit_pending"] = False
    ready_raw = json.dumps(ready).encode("utf8")
    (pretend_flet / "actual-original-full-gui-ready.json").write_bytes(ready_raw)
    ready_sha = sha256(ready_raw).hexdigest()
    for source_tab, _ in parity.TABS:
        original_meta = parity.FLET / f"original-full-{source_tab}-native.json"
        meta = deepcopy(parity.read(original_meta))
        meta["ready_sha256"] = ready_sha
        (pretend_flet / original_meta.name).write_text(json.dumps(meta), encoding="utf8")
        original_png = parity.FLET / f"original-full-{source_tab}-native.png"
        (pretend_flet / original_png.name).write_bytes(original_png.read_bytes())
    sandbox = tmp_path / "candidate-nice"
    sandbox.mkdir()
    candidate = deepcopy(parity.read(parity.NICE / "result.json"))
    candidate["flet_and_nicegui_start_datasets_equal"] = True
    candidate["capture_backend"] = "Flet_owned_native_PrintWindow"
    (sandbox / "result.json").write_text(json.dumps(candidate), encoding="utf8")
    for _, tab in parity.TABS:
        png = parity.NICE / f"nicegui-configured-{tab}.png"
        (sandbox / png.name).write_bytes(png.read_bytes())
    all_proofs = {}
    for _, tab in parity.TABS:
        proof = {}
        for label in (*parity.REQUIRED_PROOFS, "independent_review"):
            name = f"{tab}-{label}.json"
            forged = {"tab": tab, "proof_kind": label, "verdict": "PASS",
                      "flet_original_real_gui": True, "nicegui_actual_gui": True,
                      "same_initial_state": True, "same_ordered_actual_actions": True,
                      "observed_identical_effect": True,
                      "source_before_sha256": "1" * 64,
                      "source_after_sha256": "1" * 64,
                      "events": [{"action": "forged-button", "observed": "forged-success"}]}
            raw = json.dumps(forged).encode("utf8")
            (store / name).write_bytes(raw)
            proof[label] = {"verdict": "PASS", "proof_path": f"artifacts/{name}",
                            "proof_sha256": sha256(raw).hexdigest()}
        proof["flet_fixture_sha256"] = "0" * 64
        proof["nicegui_fixture_sha256"] = "0" * 64
        proof["capture_backend"] = "Flet_owned_native_PrintWindow"
        proof["identical_operation_sequence"] = True
        all_proofs[tab] = proof
    result = parity.assess(flet_dir=pretend_flet, nice_dir=sandbox, full_proofs=all_proofs)
    assert result["status"] == "UNVERIFIED"
    assert result["complete_pairs"] == 0
    assert all(row["remaining"] ==
               ["independent full-product attestation trust root absent: no PASS eligible"]
               for row in result["rows"])
