# P3 export recovery and vault session fencing: handoff

Input is fixed candidate 293, HEAD 2cf4a7a7286dd728e8df8125094498a3622babba and dirty aggregate SHA 64db1fb47b67a7eafa6f8d3bbfe052077409e1772914dbc29d709e2d134e73f6. No product/test/GUI/network execution by implementer.

## P2-5: auto-ZIP recovery candidate

The auto-ZIP exporter records an immutable owned journal before exclusive publication. The journal binds the accepted source proofs, destination basename, fresh stage basename and stage SHA. The archive stage stays in the owned incomplete directory until the export manifest is durably staged/published. On Retry/restart, recovery checks journal shape and source, verifies the stage as a regular non-reparse file and its SHA, and accepts an existing final ZIP only when it is the same file as the stage with exactly two links. If the final path is missing it is created exclusively from the verified stage. After manifest publication only the confirmed stage link is removed. The existing regular-file, no-overwrite, source-generation and final archive checks remain. Direct/manual LegacyZipExporter retains its existing startup cleanup semantics; only the auto-ZIP archives owner preserves recovery stages.

Regression code injects interruption before final linking, before manifest publication and immediately after manifest publication, reopens the owner and checks recovery and ZIP hash. Separate tests preserve a foreign destination and reject an edited stage. The new static intent/body/manifest writes already use staged exclusive publication from candidate 293. Unknown preexisting partial files at final names still fail closed: there is no trustworthy record identifying an old partial as an interrupted app write rather than a user edit. No existing file is silently overwritten or removed. This historical ambiguity remains a separate compatibility boundary, not claimed solved. Orphan stages are preserved by the auto-ZIP owner; capacity and cleanup implications must be reviewed, and no unknown stage should be deleted automatically.

## P2-9: selected ONLINE generation check, exact boundary unresolved

An ONLINE task now carries its queue generation into the transport session guard, so a lock/clear after queue selection but before the transport request check rejects the selected task. A synthetic gated regression verifies no callback call after invalidation at that point. Candidate 293 already adds request checks before ACCOUNT/OAuth/INVALID/ONLINE and generation-fenced pending queues.

The current urllib request callable synchronously combines actual request initiation and response waiting. Between its final session check and the call that starts network I/O, the executing thread can be descheduled and lock can complete. Strictly guaranteeing no physical send starts after lock completes while never waiting for the pending response requires a transport with a separately synchronized send-start boundary (or a different lock completion contract). Holding the existing lock for the whole urllib call would restore UI blocking and would wait for the response, contrary to review item 8. Do not claim item 9 closed; agree and independently review the exact send-start/lock semantics and implement a suitable transport without installing dependencies or sending live requests. No refusal was retried and no additional product contract was adopted here.

## CoS isolated checks (not run)

From this candidate, use existing S interpreter and a new artifact root:

```powershell
Set-Location S:/Projects/PixivVault-NiceGUI-p3-export-vault-fix-20261028
$env:PYTHONUTF8='1'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH='S:/Projects/PixivVault-NiceGUI-p3-export-vault-fix-20261028/src'
& S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_save_exports.py tests/test_legacy_zip_export.py tests/test_export_admission_stability.py tests/test_durable_export_acceptance.py tests/test_bookmark_media_projection.py tests/test_direct_projection_references.py tests/test_external_vault.py tests/test_external_vault_crypto.py tests/test_shared_image_cache.py tests/test_ugoira_practical.py tests/test_extension_application.py --basetemp=artifacts/p3-export-vault-fix-20261028/cos-owned/tmp --junitxml=artifacts/p3-export-vault-fix-20261028/cos-owned/impact.xml
```

Test all three interruption windows on Windows, unchanged source receipts and bytes, destination collision and stage tampering, explicit Retry/restart, preserved old unknown files, user edit and legacy ZIP rejection, and crashes around journal publication and stage unlink. Verify the P2-8 nonblocking lock and session tests; separately test the check/send gap and do not call it closed. Long path, full impact, 293 changes, CoS GUI, independent audit, native/live/CI and P3 acceptance remain unverified.
