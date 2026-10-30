"""The formal release gate must reject incomplete or stale original operations."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import tools.check_formal_152_coverage as formal_gate
from tools.check_formal_152_coverage import audit, digest, source_fingerprint


def setup_candidate(root):
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("VERSION = 1\n", encoding="utf-8")
    (root / "artifacts").mkdir()
    (root / "tools").mkdir()
    (root / "tools" / "checker.py").write_text("# synthetic checker\n", encoding="utf-8")
    checker_sha = digest(root / "tools" / "checker.py")
    source_sha = source_fingerprint(root)
    review = {"reviewer": "separate-reviewer", "verdict": "APPROVE"}
    originals = []
    candidate = []
    bindings = []
    assertions = []
    for i in range(152):
        rid = f"T{i:03d}"
        references = [{"reference_id": "SRC-1", "source_line": i + 1,
                       "expected": "The requested action reaches its intended state"}]
        if i == 0:
            references.append({"reference_id": "SRC-2", "source_line": 321,
                               "expected": "The failed action is recoverable"})
        binding_ids = []
        for index, ref in enumerate(references):
            bid = f"v8-{rid}-{index + 1:02d}"
            binding_ids.append(bid)
            bindings.append({"id": bid, "original_id": rid,
                             "reference_id": ref["reference_id"], "first_line": ref["source_line"],
                             "literal_expectation": ref["expected"],
                             "selected_lines_utf8_lf_sha256": "f" * 64})
        originals.append({"original_id": rid, "original_expected_references_318": references,
                          "expected_full_provenance_v8": {"reference_ids": binding_ids}})
        assertions.extend({"original_id": rid, "binding_id": binding_id,
                           "observation": "Expected transition observed", "verdict": "PASS"}
                          for binding_id in binding_ids)
        candidate.append({"original_id": rid, "status": "PASS", "start_condition": "Fresh synthetic app",
                          "run_id": "synthetic-owned-run-001",
                          "checker_path": "tools/checker.py", "checker_sha256": checker_sha,
                          "steps": [{"action": "Click test action", "observed": "Expected state visible"}],
                          "full_scope_reconciled": True,
                          "references": [{"index": index, "binding_id": binding_ids[index],
                                          "source_excerpt_sha256": "f" * 64,
                                          "expected": ref["expected"], "full_scope_reconciled": True,
                                          "observation": "Expected transition observed", "verdict": "PASS"}
                                         for index, ref in enumerate(references)],
                          "independent_review": review.copy()})
    (root / "artifacts" / "proof.json").write_text(json.dumps({
        "source_before_sha256": source_sha, "source_after_sha256": source_sha,
        "checker_sha256": checker_sha, "run_id": "synthetic-owned-run-001",
        "events": [{"original_id": row["original_id"], "step_index": n,
                    "action": step["action"], "observed": step["observed"]}
                   for row in candidate for n, step in enumerate(row["steps"])],
        "assertions": assertions}), encoding="utf-8")
    proof_pointer = {"proof_path": "artifacts/proof.json", "proof_sha256": digest(root / "artifacts" / "proof.json")}
    (root / "artifacts" / "review.json").write_text(json.dumps({
        "reviewer": "separate-reviewer", "verdict": "APPROVE", "source_sha256": source_sha,
        "run_id": "synthetic-owned-run-001",
        "independent_of_implementation": True,
        "approved_ids": [r["original_id"] for r in candidate],
        "approved_proofs": {ref["binding_id"]: proof_pointer["proof_sha256"]
                            for row in candidate for ref in row["references"]},
        "approved_clauses": {ref["binding_id"]: {
            "source_excerpt_sha256": ref["source_excerpt_sha256"],
            "expected_sha256": hashlib.sha256(ref["expected"].encode("utf-8")).hexdigest(),
            "full_scope_reconciled": True}
            for row in candidate for ref in row["references"]}}), encoding="utf-8")
    review_pointer = {"proof_path": "artifacts/review.json", "proof_sha256": digest(root / "artifacts" / "review.json")}
    for row in candidate:
        for item in row["references"]:
            item.update(proof_pointer)
        row["independent_review"].update(review_pointer)
    return {"records": originals, "original_reference_bindings_v8": bindings}, {"version": 1, "ledger_sha256": "immutable-ledger",
                                   "source_sha256": source_sha, "source_before_sha256": source_sha,
                                   "source_after_sha256": source_sha, "records": candidate}


def check(root, ledger, candidate):
    return audit(root, ledger, candidate, ledger_sha="immutable-ledger")


def test_no_candidate_does_not_promote_historical_coverage(tmp_path):
    original, _ = setup_candidate(tmp_path)
    result = check(tmp_path, original, None)
    assert result["success"] is False
    assert result["counts"] == {"PASS": 0, "FAIL": 0, "INCOMPLETE": 152, "STALE": 0}


def test_all_original_references_need_observation_proof_and_review(tmp_path):
    ledger, candidate = setup_candidate(tmp_path)
    result = check(tmp_path, ledger, candidate)
    assert result["success"] is True
    assert result["original_references"] == 153
    assert result["counts"]["PASS"] == 152

    missing = deepcopy(candidate)
    missing["records"][0]["references"].pop()
    failed = check(tmp_path, ledger, missing)
    assert failed["success"] is False
    assert failed["counts"]["INCOMPLETE"] == 1
    assert "reference count mismatch" in failed["operations"][0]["reasons"]


def test_stale_source_hash_disqualifies_every_operation(tmp_path):
    ledger, candidate = setup_candidate(tmp_path)
    (tmp_path / "src" / "app.py").write_text("VERSION = 2\n", encoding="utf-8")
    result = check(tmp_path, ledger, candidate)
    assert result["success"] is False
    assert result["counts"]["STALE"] == 152


def test_tampered_proof_and_failed_assertion_are_never_pass(tmp_path):
    ledger, candidate = setup_candidate(tmp_path)
    (tmp_path / "artifacts" / "proof.json").write_text('{"assertion":false}', encoding="utf-8")
    result = check(tmp_path, ledger, candidate)
    assert result["success"] is False
    assert result["counts"]["INCOMPLETE"] == 152
    assert "reference 0: proof SHA256 mismatch" in result["operations"][0]["reasons"]

    failure = deepcopy(candidate)
    failure["records"][0]["references"][0]["verdict"] = "FAIL"
    failed = check(tmp_path, ledger, failure)
    assert failed["counts"]["FAIL"] == 1


def test_rehashed_but_false_proof_cannot_be_accepted(tmp_path):
    ledger, candidate = setup_candidate(tmp_path)
    path = tmp_path / "artifacts" / "proof.json"
    proof = json.loads(path.read_text(encoding="utf-8"))
    proof["assertions"][0]["verdict"] = "FAIL"
    path.write_text(json.dumps(proof), encoding="utf-8")
    for row in candidate["records"]:
        for ref in row["references"]:
            ref["proof_sha256"] = digest(path)
    result = check(tmp_path, ledger, candidate)
    assert result["success"] is False
    assert "reference 0 assertion absent or mismatched in proof" in result["operations"][0]["reasons"]
    assert result["operations"][0]["status"] == "FAIL"


def test_rehashed_review_with_different_scope_fails(tmp_path):
    ledger, candidate = setup_candidate(tmp_path)
    path = tmp_path / "artifacts" / "review.json"
    review = json.loads(path.read_text(encoding="utf-8"))
    review["approved_ids"].remove("T000")
    path.write_text(json.dumps(review), encoding="utf-8")
    for row in candidate["records"]:
        row["independent_review"]["proof_sha256"] = digest(path)
    result = check(tmp_path, ledger, candidate)
    assert result["success"] is False
    assert "independent review payload does not approve current source and ID" in result["operations"][0]["reasons"]


def test_review_must_approve_the_exact_assertion_proof_digest(tmp_path):
    ledger, candidate = setup_candidate(tmp_path)
    path = tmp_path / "artifacts" / "review.json"
    reviewed = json.loads(path.read_text(encoding="utf-8"))
    reviewed["approved_proofs"]["v8-T000-01"] = "0" * 64
    path.write_text(json.dumps(reviewed), encoding="utf-8")
    for row in candidate["records"]:
        row["independent_review"]["proof_sha256"] = digest(path)
    result = check(tmp_path, ledger, candidate)
    assert result["success"] is False
    assert any("independent review not bound to proof and original clause" in reason
               for reason in result["operations"][0]["reasons"])


def test_duplicate_and_unknown_candidate_ids_fail_closed(tmp_path):
    ledger, candidate = setup_candidate(tmp_path)
    candidate["records"].append(deepcopy(candidate["records"][0]))
    candidate["records"].append({"original_id": "NOT-IN-LEDGER"})
    result = check(tmp_path, ledger, candidate)
    assert result["success"] is False
    assert any("duplicate candidate ID" in error for error in result["global_errors"])
    assert any("unknown candidate IDs" in error for error in result["global_errors"])


def test_proof_outside_project_cannot_be_used(tmp_path):
    ledger, candidate = setup_candidate(tmp_path)
    candidate["records"][0]["references"][0]["proof_path"] = "../private-file"
    result = check(tmp_path, ledger, candidate)
    assert result["success"] is False
    assert "reference 0: proof path outside repository" in result["operations"][0]["reasons"]


def test_proof_inside_source_or_private_root_is_not_an_evidence_file(tmp_path):
    ledger, candidate = setup_candidate(tmp_path)
    candidate["records"][0]["references"][0]["proof_path"] = "src/app.py"
    result = check(tmp_path, ledger, candidate)
    assert result["success"] is False
    assert "reference 0: proof path outside repository" in result["operations"][0]["reasons"]


def test_repeated_reference_label_cannot_substitute_a_different_binding(tmp_path):
    ledger, candidate = setup_candidate(tmp_path)
    candidate["records"][0]["references"][1]["binding_id"] = "v8-T000-01"
    result = check(tmp_path, ledger, candidate)
    assert result["success"] is False
    assert "reference 1 immutable binding ID mismatch" in result["operations"][0]["reasons"]


def test_unreconciled_original_expectation_cannot_pass(tmp_path):
    ledger, candidate = setup_candidate(tmp_path)
    ledger["original_reference_bindings_v8"][0]["literal_expectation"] = None
    result = check(tmp_path, ledger, candidate)
    assert result["success"] is False
    assert "reference 0 source-only expectation not reconciled" in result["operations"][0]["reasons"]


def test_original_summary_with_null_line_uses_immutable_binding_line(tmp_path):
    ledger, candidate = setup_candidate(tmp_path)
    ledger["records"][0]["original_expected_references_318"][0]["source_line"] = None
    result = check(tmp_path, ledger, candidate)
    assert result["success"] is True


def test_bool_reference_index_and_duplicate_proof_json_keys_rejected(tmp_path):
    ledger, candidate = setup_candidate(tmp_path)
    candidate["records"][0]["references"][0]["index"] = True
    assert "invalid reference index" in check(tmp_path, ledger, candidate)["operations"][0]["reasons"]

    candidate["records"][0]["references"][0]["index"] = 0
    proof_file = tmp_path / "artifacts" / "proof.json"
    original = proof_file.read_text(encoding="utf-8")
    proof_file.write_text(original[:-1] + ',"assertions":[]}', encoding="utf-8")
    for row in candidate["records"]:
        for reference in row["references"]:
            reference["proof_sha256"] = digest(proof_file)
    result = check(tmp_path, ledger, candidate)
    assert result["success"] is False
    assert "reference 0: proof is not machine-readable JSON" in result["operations"][0]["reasons"]


def test_actual_immutable_v8_stays_unaccepted_without_current_candidate():
    root = Path(__file__).resolve().parents[1]
    file = root / "docs/P3_FORMAL_152_OPERATIONS_LEDGER_2026-10-29_v8.json"
    ledger = json.loads(file.read_text(encoding="utf-8"))
    result = audit(root, ledger, None, ledger_sha=digest(file))
    assert result["success"] is False
    assert result["total"] == 152
    assert result["original_references"] == 479
    assert result["counts"]["INCOMPLETE"] == 152


def test_formal_ci_accepts_reviewed_tracked_evidence_not_ignored_runtime_artifacts():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    formal_section = workflow.split("  formal-152-acceptance:\n", 1)[1].split("\n  browser:\n", 1)[0]
    assert "--manifest docs/formal-152/current-candidate.json" in formal_section
    assert "--output artifacts/formal-152/ci-result.json" in formal_section
    assert "artifacts/" in (root / ".gitignore").read_text(encoding="utf-8")


def test_ci_docs_manifest_cli_loads_reconciled_proofs_and_rejects_missing_one(
        tmp_path, monkeypatch):
    """Exercise the real CLI with an isolated, synthetic docs/ evidence bundle."""
    ledger, candidate = setup_candidate(tmp_path)
    docs = tmp_path / "docs" / "formal-152"
    docs.mkdir(parents=True)
    for name in ("proof.json", "review.json"):
        (docs / name).write_bytes((tmp_path / "artifacts" / name).read_bytes())
    for row in candidate["records"]:
        for ref in row["references"]:
            ref["proof_path"] = "docs/formal-152/proof.json"
        row["independent_review"]["proof_path"] = "docs/formal-152/review.json"

    ledger_path = tmp_path / formal_gate.LEDGER
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    pinned = digest(ledger_path)
    candidate["ledger_sha256"] = pinned
    manifest_path = docs / "current-candidate.json"
    manifest_path.write_text(json.dumps(candidate), encoding="utf-8")
    monkeypatch.setattr(formal_gate, "FROZEN_V8_SHA256", pinned)

    def invoke(stem):
        monkeypatch.setattr(sys, "argv", ["check_formal_152_coverage.py", "--root", str(tmp_path),
                            "--manifest", "docs/formal-152/current-candidate.json",
                            "--output", f"artifacts/formal-152/{stem}.json"])
        result = formal_gate.main()
        payload = json.loads((tmp_path / "artifacts" / "formal-152" / f"{stem}.json")
                             .read_text(encoding="utf-8"))
        return result, payload

    exit_code, observed = invoke("from-tracked-docs")
    assert exit_code == 0
    assert observed["counts"]["PASS"] == 152
    assert observed["original_references"] == 153

    candidate["records"][0]["references"][0]["proof_path"] = "docs/formal-152/missing.json"
    manifest_path.write_text(json.dumps(candidate), encoding="utf-8")
    exit_code, observed = invoke("missing-docs-proof")
    assert exit_code == 1
    assert observed["counts"]["PASS"] == 151
    assert observed["counts"]["INCOMPLETE"] == 1
    assert "reference 0: proof file missing or unreadable" in observed["operations"][0]["reasons"]
