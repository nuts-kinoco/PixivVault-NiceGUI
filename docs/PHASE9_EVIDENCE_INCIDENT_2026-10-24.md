# Phase9 evidence incident and recovery status — 2026-10-24

## Summary

During native Windows acceptance run `phase9-native-final-stable-acceptance-20261024`, `tools/check_native_window.py --inspect-ui` wrote six screenshots to shared fixed paths under `artifacts/` (`webview-ui*.png`). Those paths were not unique to this run, so pre-existing files at those exact names were overwritten. This was an orchestration mistake; the previous bytes were not preserved before the run.

## Affected paths

- `artifacts/webview-ui.png`
- `artifacts/webview-ui-viewer.png`
- `artifacts/webview-ui-library-authors.png`
- `artifacts/webview-ui-library-viewer.png`
- `artifacts/webview-ui-library-detail.png`
- `artifacts/webview-ui-download-jobs.png`

The six current post-run files were copied, without further overwriting, to `artifacts/phase9-native-final-stable-acceptance-20261024/`. The evidence index is `artifacts/phase9-native-final-stable-acceptance-indexed-20261024.json`, which points to those copies. These are copies of the new post-run screenshots, not recovery of the prior versions.

## Follow-up overwrite during final-history reacceptance

During the 2026-10-24 history-preservation native reacceptance, a first attempt to change the checker output naming did not actually modify the checker; the native run therefore wrote to the same six shared filenames again. This was a second orchestration error, and the user instruction not to overwrite evidence was not met. The six files were immediately copied to `artifacts/phase9-native-history-preserve-20261024/` with SHA-256 verified against each just-written file. Comparing those hashes with `artifacts/phase9-native-final-stable-acceptance-20261024/` shows three byte-identical files (`webview-ui.png`, `webview-ui-viewer.png`, `webview-ui-library-viewer.png`) and three that differ (`webview-ui-library-authors.png`, `webview-ui-library-detail.png`, `webview-ui-download-jobs.png`); the latter's earlier variant remains preserved in the older run-specific directory. This comparison does not recover the original bytes that predated the first incident.

The checker source was then correctly changed in both the native build snapshot and the live worktree: `screenshot = output_path.with_name(output_path.stem + "-webview-ui.png")`. All secondary screenshots derive from that unique stem, and restore screenshots already derive from the run-specific result stem. The first rerun after this fix passed UI/handshake, minimize/restore, download jobs, normal exit, port release and no forced cleanup, but was marked failed solely because its process log contained an `asyncio` `ConnectionResetError` at WebView shutdown; this RED result is preserved as `artifacts/phase9-native-history-preserve-final-acceptance-20261024.json` and was not overwritten. A second run, `artifacts/phase9-native-history-preserve-retry2-acceptance-20261024.json`, **passed** with the same app binary and unique screenshot names, zero process-log failures, 8 owned PIDs exiting naturally, no forced cleanup, and port release. Its screenshot paths are run-specific (e.g. `phase9-native-history-preserve-retry2-acceptance-20261024-webview-ui.png`), and the shared fixed names were not touched by that final pass.

## Recovery status

- **Prior screenshot bytes:** not recovered or verified.
- **Recovery methods attempted:** no destructive recovery was attempted. No assumption is made that Previous Versions, shadow copies, backups, or other duplicates exist.
- A read-only search for the same six basenames elsewhere under `artifacts/` found only the new post-run copies, with identical hashes. `vssadmin list shadows` could not run because this shell is not elevated; no elevation or restore attempt was made. Therefore recovery remains unverified.
- **Current evidence:** the new screenshots are preserved under the unique acceptance directory. Native acceptance JSON reports pass, normal exit, port release, and no forced cleanup; it is not evidence that the prior screenshots were recovered.
- **Next action:** inspect safe recovery sources read-only (backup/Previous Versions/history or independently stored duplicate files), compare candidate hashes and timestamps, and restore only to a new recovery directory unless exact originals can be proven. Never overwrite the current acceptance screenshots.

## Subsequent Phase9 Edge runs

The Edge run `artifacts/phase9-managed-scheduler-edge-final-latest-20261024/result.json` failed in the synthetic acceptance assertion: after the initial batch it observed a later backlog dispatch with `unknown` status. Its durable ledger showed the initial slot as `saved` (50 accepted, 20 deferred) and a backlog slot as `unknown` (21 accepted, 49 deferred), with one work item already represented by the prior saved generation. This exposed a likely stale-inventory race: the dispatch function snapshots active receipts before separately taking the unfinished-job snapshot; a job can finish between those reads, so the stale `saved` map can classify an already-saved work as new.

A live-source fix was added to re-read the current managed CAS receipt immediately before creating a new operation when the earlier inventory had no receipt. `verified_saved_receipt` gained an optional `verify_pages=False` mode for this narrow recheck; the active index and receipt are still validated, while full page verification remains part of normal acceptance/publication. Focused tests passed (45 passed, 1 privilege-dependent skip) after that patch.

The first two Edge reruns after the fix did **not** pass the checker: `phase9-managed-scheduler-edge-after-race-fix-20261024` failed the saved-history assertion after a backlog slot became unknown; `phase9-managed-scheduler-edge-final-racefix2-20261024` failed the checker's exact initial `(accepted, deferred) == (50, 20)` assertion. A later distinct run, `phase9-managed-scheduler-70works-final2-20261024`, saved all 70 expected active generations, reconciled all 70 dispatch items to `saved`, and left zero deferred items; it failed only because the checker expected 72 retained terminal queue rows despite JobService's intentional 50-row history cap. The checker was corrected to expect the bounded count of 50.

The next candidate `phase9-managed-scheduler-edge-final-accepted-candidate-20261024` passed the initial 1280×720 UI/dispatch/receipt/history steps but failed during the restart branch because `verify_saved()` still defaulted to `expected_entries=1`, even though the restart fixture correctly retained all 70 active generations from the initial run. The checker was corrected to require `expected_entries=70` for that restart.

The subsequent `artifacts/phase9-managed-scheduler-edge-final-finalcandidate-20261024/result.json` was **SUCCESS, 4/4** before the history-preservation correction. The latest run after the correction, `artifacts/phase9-managed-scheduler-edge-history-preserve-20261024/result.json`, is also **SUCCESS, 4/4**: 1280×720 and 1920×1080, initial and restart; viewer decode/focus and scheduler settings→dispatch→receipt/history all passed; initial synthetic original GET count 6 per viewport and restart GET count 0; console/page errors 0, foreign requests 0, normal exit 4/4, port release 4/4. All 59 application source hashes in that result match the current live worktree and the separate native build snapshot. This is synthetic acceptance only, not real-cookie production acceptance.

Independent review remains a separate gate. Worker-3's earlier full-diff verdict is INCONCLUSIVE due to source mutation during its review. Worker-4 identified the residual enqueue race, then **APPROVED the current live snapshot for the per-work race/lock-order scope**: no P1/P2 in that scope, and no deadlock/lock inversion found. Worker-4 explicitly did not independently rerun tests and did not claim to have reviewed every unrelated Phase9 behavior. A worker-5 full-diff review attempt failed to start because the CoSCore chat initialization timed out during model/reasoning selection; the prior worker family is not accessible from this continuation. The worker-3 wake attempt also failed. Do not treat the limited-scope approval as full Phase9 approval.

## Shared per-work serialization follow-up

To address the residual P2, a shared per-work reentrant lock was added in `ManagedPixivDownloads` and is now used by manual acceptance, the scheduler's final pending/receipt check through detail GET and enqueue, and the worker's active-receipt check plus active-generation CAS publication. The worker intentionally does **not** hold the lock over network/image I/O; this preserves JobService's existing same-work unfinished-job deduplication for two clients while serializing the final pointer read/commit against acceptance. A focused regression verifies that same-ID callers share the lock. Focused tests passed **52 passed / 1 skipped**. The subsequent full pytest on this source passed **1202 passed / 1 skipped / 1 warning in 72.75s**.

The final history-preservation Edge run `artifacts/phase9-managed-scheduler-edge-partial-history-recovery-20261024/result.json` is **SUCCESS, 4/4** (1280×720 and 1920×1080, initial/restart); original GET count is 6 on initial runs and 0 on restarts; console/page/foreign request errors are zero; normal exit and port release are 4/4. The synthetic 70-work ledger records initial slot `saved` (50 accepted, 20 deferred) and backlog slot `saved` (20 accepted, 50 already/pending or non-dispatchable deferred); all 70 dispatch items are `saved`, active-generation index contains 70 entries, durable deferred inventory is 0. All 59 application source hashes match the current live worktree and the native build snapshot. Windows onedir/noconsole was rebuilt from snapshot `S:/Projects/PixivVault-NiceGUI-wt/phase9-native-build-partial-history-recovery-20261024`, copied to `artifacts/phase9-native-copy-partial-history-recovery-20261024`, and all **1050/1050** file SHA-256 values matched. EXE SHA-256 `65ADBBF0D3421024296155CEDB0363B64D9EC5A916A404BAA1906AF864C34E8D`. Native acceptance `artifacts/phase9-native-partial-history-recovery-acceptance-20261024.json` is passed: UI handshake/image decode/operations, minimize/restore, normal exit, 8 owned PIDs exited naturally, forced cleanup 0, port released, no errors. Its six screenshot paths use the unique `phase9-native-partial-history-recovery-acceptance-20261024-webview-ui*.png` namespace.

Worker-4's earlier BLOCK was for the pre-lock source and identified the residual TOCTOU. Worker-4 subsequently approved the current lock/race scope. Worker-5 failed to start because the CoSCore chat launch timed out during model/reasoning selection; no review result exists from worker-5, and the previous worker family is no longer accessible to this continuation. No replacement worker was spawned because the startup issue has not been resolved. **Full-scope independent APPROVE/P1-P2=0 is still pending**; the limited lock-scope approval is not full approval. Original-root manifest was rechecked after these operations: 37/37 files' SHA, size, and mtime match the baseline. Git remains dirty and `git diff --check` is clean; no Phase9 commit/push/PR/merge has been made.

## Additional self-review correction: aggregate history recovery

A further full-source self-audit found a P2 in durable aggregate history: a dispatch slot that had been reconciled to `partial` could later have its ambiguous failed item proven `saved` after operation-ID recovery, but the history UPDATE allowlist did not include `partial`; the per-item receipt truth would be correct while the slot's aggregate history stayed stale. `managed_scheduler.py` now permits that aggregate transition, and `test_partial_history_recovers_when_ambiguous_enqueue_is_later_verified_saved` reproduces partial→saved recovery and pending notifications. Focused scheduler tests: **22 passed**. Full pytest on this final code: **1203 passed / 1 skipped / 1 warning in 67.95s** (`artifacts/phase9-final-partial-history-recovery-20261024/full.xml`). Edge on this exact source is **SUCCESS 4/4** at `artifacts/phase9-managed-scheduler-edge-partial-history-recovery-20261024/result.json`. The matching Windows build/copy is **1050/1050 SHA match** and native acceptance `artifacts/phase9-native-partial-history-recovery-acceptance-20261024.json` is passed. This was found in the orchestrator's own full-source audit, not by an independent full-diff reviewer; independent full-scope review remains a separate unresolved gate.

## Remediation for independent review P2-1 through P2-6 and checker gap

After the user supplied the six BLOCK findings, the following fixes were applied to the live worktree:

- **P2-1 removed favourites:** both `set_favourites` and `configure_all` now delete `deferred_work` for authors not present in the newly persisted favourite list inside the same SQLite transaction. Regression `test_removing_favourite_prunes_its_deferred_work_atomically` added; empty favourites are covered.
- **P2-2 interrupted barrier:** backlog dispatch now checks `state.last_status != 'interrupted'`, so it cannot bypass explicit recovery acknowledgement even when the regular due time is in the future.
- **P2-3 no-job dispatch history:** `reconcile_jobs` now terminalizes queued/running history slots with `accepted=0`, `deferred>0`, and no dispatch items as `deferred`.
- **P2-4 failure hidden by reconcile:** aggregate history reconciliation no longer rewrites a `failed` dispatch history row to `saved`; an author GET failure stays visible even if an earlier accepted item's receipt later verifies.
- **P2-5 dirty settings overwritten by timer:** scheduler form input/select/checkbox changes set `_settings_dirty`; five-second refresh still updates status/history but `_load` no longer overwrites the three editable settings until save succeeds.
- **P2-6 hot retry:** after a backlog dispatch ends in `failed` or `unknown`, the runner waits 60 seconds before the next attempt rather than immediately looping.
- **Checker coverage:** `check_author_download_browser.py` now directly queries the scheduler SQLite ledger and asserts exactly 70 dispatch items are `saved` and `deferred_work` is zero, in addition to active-generation/index/history checks.

The post-remediation full pytest is **1204 passed / 1 skipped / 1 warning in 58.42s**, `artifacts/phase9-final-p2-remediation-20261024/full.xml`; focused scheduler/page/native-check tests **78 passed**, scheduler-only tests **23 passed**. A fresh Edge run `artifacts/phase9-managed-scheduler-edge-p2-remediation-20261024` is in progress; Windows native build/acceptance for this exact source remains pending. A separate CoSCore worker is invited to review these changes read-only; full-scope independent approval remains pending.

## Git and merge gate

The Phase9 worktree remains dirty on `phase9/scheduler-managed`; all dirty changes are retained. `git diff --check` is clean. No Phase9 commit, push, PR, or merge has been made. Worker-4 has approved the per-work race/lock-order scope only and has been asked to extend its read-only review to the full Phase9 dirty diff after worker-5 failed to start. Worker-2 remains context-limited with no durable result, and worker-3 could not be woken for a second review. Do not treat Phase9 as fully approved until a concrete full-scope current-snapshot verdict is received.

## Constraints

- Continue all Windows, Git, pytest, Edge, packaging, and worker operations through CoSCore only.
- Do not kill unknown processes; the observed 70-work Edge run is owned by its existing runner and should be allowed to finish or reported with exact blocker.
- Do not overwrite existing evidence. Future GUI/browser checker runs must direct every screenshot output path—not just the result JSON—to a unique run-specific directory.
- Do not commit/push/merge until source is stable, P1/P2 are resolved and independently approved, full tests and Edge/native acceptance match the exact reviewed source, and head CI is green.
