"""Fail-closed, read-only gate for original PixivVault operation evidence.

The immutable v8 ledger describes original requirements; it is not evidence
that the current application has passed them. A separate candidate manifest
must bind each original reference to an observed result, exact file proof and
independent approval. This checker never writes to the source data or ledger.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat


ROOT = Path(__file__).resolve().parents[1]
LEDGER = Path("docs/P3_FORMAL_152_OPERATIONS_LEDGER_2026-10-29_v8.json")
MANIFEST = Path("artifacts/formal-152/current-candidate.json")
# Immutable original v8 snapshot: changing the original specification requires
# a separately reviewed new ledger/version, not a silent proof substitution.
FROZEN_V8_SHA256 = "487065a9a52228266d540e36d3e3b83ea34a38bdde4d1fdb54e275d725e55bb4"
SHA256_HEX = set("0123456789abcdef")
MAX_PROOF_BYTES = 8 * 1024 * 1024
EVIDENCE_DIRS = {"artifacts", "docs"}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_fingerprint(root: Path) -> str:
    """Hash paths AND bytes: modifying, adding or removing a source invalidates proof."""
    paths = sorted(path for path in (root / "src").rglob("*")
                   if path.is_file() and path.suffix in {".py", ".js", ".css"})
    state = hashlib.sha256()
    for path in paths:
        if path.is_symlink():
            raise ValueError("linked runtime sources cannot be certified")
        state.update(path.relative_to(root).as_posix().encode("utf-8"))
        state.update(b"\0")
        state.update(bytes.fromhex(digest(path)))
        state.update(b"\n")
    return state.hexdigest()


def nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def valid_sha(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= SHA256_HEX


def evidence_path(root: Path, relative_name: str) -> Path:
    relative = Path(relative_name)
    if (relative.is_absolute() or ".." in relative.parts or
            not relative.parts or relative.parts[0] not in EVIDENCE_DIRS):
        raise ValueError("evidence must be a repository artifacts/ or docs/ file")
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError("evidence path resolves outside the repository")
    return resolved


def verified_proof_bytes(root: Path, reference: dict, prefix: str) -> tuple[bytes | None, list[str]]:
    """Read one resolved file once, then hash and parse the SAME bytes."""
    path = reference.get("proof_path")
    sha = reference.get("proof_sha256")
    if not nonempty(path) or not valid_sha(sha):
        return None, [f"{prefix}: missing proof path or valid SHA256"]
    try:
        resolved = evidence_path(root, path)
        before = resolved.lstat()
        if not stat.S_ISREG(before.st_mode):
            return None, [f"{prefix}: proof is not a file"]
        if before.st_size > MAX_PROOF_BYTES:
            return None, [f"{prefix}: proof exceeds bounded size"]
        with resolved.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                return None, [f"{prefix}: proof changed before reading"]
            payload = handle.read(MAX_PROOF_BYTES + 1)
        if len(payload) > MAX_PROOF_BYTES:
            return None, [f"{prefix}: proof exceeds bounded size"]
        if hashlib.sha256(payload).hexdigest() != sha:
            return None, [f"{prefix}: proof SHA256 mismatch"]
    except ValueError:
        return None, [f"{prefix}: proof path outside repository"]
    except OSError:
        return None, [f"{prefix}: proof file missing or unreadable"]
    return payload, []


def reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key}")
        result[key] = value
    return result


def read_bounded_json_file(path: Path, limit: int = MAX_PROOF_BYTES) -> object:
    """Bound input and verify the file object did not change before opening."""
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
        raise ValueError("JSON input is not a bounded regular file")
    with path.open("rb") as handle:
        opened = os.fstat(handle.fileno())
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError("JSON input changed before reading")
        content = handle.read(limit + 1)
    if len(content) > limit:
        raise ValueError("JSON input exceeds bounded size")
    return json.loads(content.decode("utf-8"), object_pairs_hook=reject_duplicate_json_keys)


def proof_json(root: Path, evidence: dict, prefix: str) -> tuple[dict | None, list[str]]:
    content, errors = verified_proof_bytes(root, evidence, prefix)
    if errors:
        return None, errors
    try:
        payload = json.loads(content.decode("utf-8"), object_pairs_hook=reject_duplicate_json_keys)
    except (OSError, ValueError, UnicodeError):
        return None, [f"{prefix}: proof is not machine-readable JSON"]
    if not isinstance(payload, dict):
        return None, [f"{prefix}: proof payload must be an object"]
    return payload, []


def check_checker(root: Path, row: dict, rid: str) -> list[str]:
    name = row.get("checker_path")
    sha = row.get("checker_sha256")
    if not nonempty(name) or not valid_sha(sha):
        return ["checker identity missing"]
    relative = Path(name)
    if (relative.is_absolute() or ".." in relative.parts or
            not relative.parts or relative.parts[0] != "tools"):
        return ["checker must be a repository tools/ path"]
    path = root / relative
    try:
        if (not path.resolve(strict=True).is_relative_to(root.resolve()) or
                digest(path) != sha):
            return [f"{rid}: checker identity stale"]
    except (OSError, ValueError):
        return ["checker file missing or unreadable"]
    return []


def audit(root: Path, ledger: dict, manifest: dict | None,
          *, ledger_sha: str | None = None) -> dict:
    """Return deterministic per-ID statuses and machine-readable blocking reasons."""
    originals = ledger.get("records", [])
    if not isinstance(originals, list):
        raise ValueError("ledger records must be a list")
    ids = [record.get("original_id") for record in originals]
    if len(ids) != 152 or len(set(ids)) != 152 or not all(nonempty(x) for x in ids):
        raise ValueError("original ledger must contain 152 distinct operation IDs")
    bindings = ledger.get("original_reference_bindings_v8")
    if not isinstance(bindings, list) or not bindings:
        raise ValueError("original ledger reference bindings are missing")
    binding_by_id = {x.get("id"): x for x in bindings if isinstance(x, dict)}
    if (len(binding_by_id) != len(bindings) or
            any(not nonempty(x) for x in binding_by_id)):
        raise ValueError("original reference bindings must have unique immutable IDs")
    if len(originals) == 152 and len(bindings) < 152:
        raise ValueError("original reference binding inventory is incomplete")
    snapshot_sha = source_fingerprint(root)

    observed = {}
    global_errors = []
    if not isinstance(manifest, dict):
        global_errors.append("candidate manifest missing")
    else:
        rows = manifest.get("records")
        if not isinstance(rows, list):
            global_errors.append("candidate records missing")
        else:
            for row in rows:
                if not isinstance(row, dict) or not nonempty(row.get("original_id")):
                    global_errors.append("malformed candidate record")
                    continue
                rid = row["original_id"]
                if rid in observed:
                    global_errors.append(f"duplicate candidate ID {rid}")
                observed[rid] = row
            unknown = sorted(set(observed) - set(ids))
            if unknown:
                global_errors.append(f"unknown candidate IDs: {', '.join(unknown)}")
        if type(manifest.get("version")) is not int or manifest.get("version") != 1:
            global_errors.append("candidate manifest version must be 1")
        if ledger_sha is not None and manifest.get("ledger_sha256") != ledger_sha:
            global_errors.append("candidate ledger SHA256 is stale")
        if (manifest.get("source_sha256") != snapshot_sha or
                manifest.get("source_before_sha256") != snapshot_sha or
                manifest.get("source_after_sha256") != snapshot_sha):
            global_errors.append("candidate source SHA256 is stale")

    results = []
    for original in originals:
        rid = original["original_id"]
        row = observed.get(rid)
        reasons = []
        refs = original.get("original_expected_references_318")
        if not isinstance(refs, list) or not refs:
            reasons.append("original reference list missing")
            refs = []
        provenance = original.get("expected_full_provenance_v8")
        bound_ids = provenance.get("reference_ids") if isinstance(provenance, dict) else None
        if not isinstance(bound_ids, list) or len(bound_ids) != len(refs):
            reasons.append("original immutable reference mapping incomplete")
            bound_ids = []
        if any(binding_by_id.get(bid, {}).get("original_id") != rid for bid in bound_ids):
            reasons.append("original immutable reference mapping mismatches operation")
        if row is None:
            reasons.append("candidate observation missing")
        else:
            receipt_failed = False
            reasons.extend(check_checker(root, row, rid))
            if not nonempty(row.get("start_condition")):
                reasons.append("starting state missing")
            steps = row.get("steps")
            if (not isinstance(steps, list) or not steps
                    or any(not isinstance(x, dict) or not nonempty(x.get("action"))
                           or not nonempty(x.get("observed")) for x in steps)):
                reasons.append("actual actions and results missing")
            actual_refs = row.get("references")
            if not isinstance(actual_refs, list):
                actual_refs = []
                reasons.append("per-reference observations missing")
            if len(actual_refs) != len(refs):
                reasons.append("reference count mismatch")
            indices = set()
            for item in actual_refs:
                if not isinstance(item, dict) or type(item.get("index")) is not int:
                    reasons.append("invalid reference index")
                    continue
                index = item["index"]
                if index in indices or index < 0 or index >= len(refs):
                    reasons.append(f"reference index {index} unexpected or duplicated")
                    continue
                indices.add(index)
                expected = refs[index]
                if (index >= len(bound_ids) or
                        item.get("binding_id") != bound_ids[index]):
                    reasons.append(f"reference {index} immutable binding ID mismatch")
                    continue
                binding = binding_by_id.get(bound_ids[index])
                if binding is None:
                    reasons.append(f"reference {index} immutable binding missing")
                    continue
                if (binding.get("reference_id") != expected.get("reference_id") or
                        (expected.get("source_line") is not None and
                         binding.get("first_line") != expected.get("source_line")) or
                        item.get("source_excerpt_sha256") != binding.get("selected_lines_utf8_lf_sha256")):
                    reasons.append(f"reference {index} original source mismatch")
                literal = binding.get("literal_expectation")
                if (nonempty(literal) and item.get("expected") != literal):
                    reasons.append(f"reference {index} original expectation altered")
                if not nonempty(literal) and (not nonempty(item.get("expected")) or
                        item.get("expectation_origin") != "independently-reconciled-original"):
                    reasons.append(f"reference {index} source-only expectation not reconciled")
                if item.get("full_scope_reconciled") is not True:
                    reasons.append(f"reference {index} full original scope unreviewed")
                if not nonempty(item.get("observation")):
                    reasons.append(f"reference {index} observed assertion missing")
                if item.get("verdict") not in {"PASS", "FAIL"}:
                    reasons.append(f"reference {index} has no explicit verdict")
                proof, proof_errors = proof_json(root, item, f"reference {index}")
                reasons.extend(proof_errors)
                if proof is not None:
                    assertions = proof.get("assertions")
                    matched = ([a for a in assertions if isinstance(a, dict) and
                                a.get("original_id") == rid and a.get("binding_id") == bound_ids[index]]
                               if isinstance(assertions, list) else [])
                    if any(match.get("verdict") == "FAIL" for match in matched):
                        receipt_failed = True
                    if (len(matched) != 1 or
                            matched[0].get("observation") != item.get("observation") or
                            matched[0].get("verdict") != item.get("verdict")):
                        reasons.append(f"reference {index} assertion absent or mismatched in proof")
                    if (proof.get("source_before_sha256") != snapshot_sha or
                            proof.get("source_after_sha256") != snapshot_sha or
                            proof.get("checker_sha256") != row.get("checker_sha256")):
                        reasons.append(f"reference {index} run source/checker not bound")
                    if (not nonempty(row.get("run_id")) or
                            proof.get("run_id") != row.get("run_id")):
                        reasons.append(f"reference {index} run identity missing or mismatched")
                    events = proof.get("events")
                    if (not isinstance(events, list) or
                            any(not isinstance(event, dict) or
                                not nonempty(event.get("action")) or
                                not nonempty(event.get("observed")) for event in events) or
                            not isinstance(steps, list) or
                            any({"original_id": rid, "step_index": position,
                                 "action": step.get("action"), "observed": step.get("observed")}
                                not in events for position, step in enumerate(steps)
                                if isinstance(step, dict))):
                        reasons.append(f"reference {index} actual action receipt missing")

            review = row.get("independent_review")
            if not isinstance(review, dict) or not nonempty(review.get("reviewer")):
                reasons.append("independent reviewer missing")
            else:
                if review.get("verdict") != "APPROVE":
                    reasons.append("independent review not approved")
                review_proof, review_errors = proof_json(root, review, "independent review")
                reasons.extend(review_errors)
                if review_proof is not None and (
                    review_proof.get("reviewer") != review.get("reviewer") or
                    review_proof.get("verdict") != "APPROVE" or
                    review_proof.get("source_sha256") != snapshot_sha or
                    review_proof.get("independent_of_implementation") is not True or
                    not isinstance(review_proof.get("approved_ids"), list) or
                    rid not in review_proof["approved_ids"] or
                    review_proof.get("run_id") != row.get("run_id")
                ):
                    reasons.append("independent review payload does not approve current source and ID")
                if review_proof is not None:
                    approved_proofs = review_proof.get("approved_proofs")
                    approved_clauses = review_proof.get("approved_clauses")
                    for reference in actual_refs:
                        if not isinstance(reference, dict):
                            continue
                        bid = reference.get("binding_id")
                        approval = (approved_clauses.get(bid)
                                    if isinstance(approved_clauses, dict) and isinstance(bid, str) else None)
                        if (not isinstance(approved_proofs, dict) or
                                approved_proofs.get(bid) != reference.get("proof_sha256") or
                                not isinstance(approval, dict) or
                                approval.get("source_excerpt_sha256") != reference.get("source_excerpt_sha256") or
                                approval.get("expected_sha256") != hashlib.sha256(
                                    str(reference.get("expected", "")).encode("utf-8")).hexdigest() or
                                approval.get("full_scope_reconciled") is not True):
                            reasons.append(f"independent review not bound to proof and original clause {bid}")
            if row.get("full_scope_reconciled") is not True:
                reasons.append("entire operation scope not independently reconciled")
            if row.get("status") != "PASS":
                reasons.append("candidate ID not explicitly PASS")
        candidate_refs = row.get("references") if row is not None else None
        fail = (row is not None and (row.get("status") == "FAIL" or
                receipt_failed or
                any(isinstance(x, dict) and x.get("verdict") == "FAIL"
                    for x in (candidate_refs if isinstance(candidate_refs, list) else []))))
        results.append({"original_id": rid,
                        "status": "FAIL" if fail else "INCOMPLETE" if reasons else "PASS",
                        "reasons": sorted(set(reasons))})

    # A bad or stale manifest cannot be accepted even if its individual rows look valid.
    if global_errors:
        stale_candidate = any("stale" in error for error in global_errors)
        for item in results:
            if item["status"] != "FAIL" and (item["status"] == "PASS" or stale_candidate):
                item["status"] = "STALE" if stale_candidate else "INCOMPLETE"
    if source_fingerprint(root) != snapshot_sha:
        global_errors.append("runtime source changed during formal coverage audit")
        for item in results:
            if item["status"] == "PASS":
                item["status"] = "STALE"
    counts = {key: sum(item["status"] == key for item in results)
              for key in ("PASS", "FAIL", "INCOMPLETE", "STALE")}
    return {"success": counts["PASS"] == 152 and not global_errors,
            "total": len(results), "original_references": len(bindings),
            "counts": counts, "global_errors": global_errors, "operations": results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--ledger", type=Path, default=LEDGER)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    ledger_path = root / args.ledger
    if (args.ledger != LEDGER or digest(ledger_path) != FROZEN_V8_SHA256):
        raise SystemExit("immutable formal v8 ledger path/SHA256 mismatch")
    original = read_bounded_json_file(ledger_path)
    try:
        manifest_path = evidence_path(root, str(args.manifest))
    except ValueError as exc:
        raise SystemExit(f"invalid candidate manifest path: {exc}") from exc
    try:
        candidate = read_bounded_json_file(manifest_path) if manifest_path.is_file() else None
    except ValueError as exc:
        raise SystemExit(f"candidate manifest rejected: {exc}") from exc
    result = audit(root, original, candidate, ledger_sha=digest(ledger_path))
    if args.output:
        if (args.output.is_absolute() or ".." in args.output.parts or
                not args.output.parts or args.output.parts[0] != "artifacts" or
                not (root / "artifacts").resolve().is_relative_to(root) or
                not (root / args.output).resolve().is_relative_to((root / "artifacts").resolve())):
            raise SystemExit("output must remain under repository artifacts/")
        output = root / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        if not output.parent.resolve().is_relative_to((root / "artifacts").resolve()):
            raise SystemExit("output parent left repository artifacts/")
        try:
            with output.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        except FileExistsError:
            raise SystemExit("evidence output already exists; choose a new path") from None
    print(json.dumps({key: result[key] for key in ("success", "total", "original_references", "counts", "global_errors")}, ensure_ascii=False))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
