# Phase9 continuation handoff — 2026-10-24

This document is the current continuation record for the PixivVault Flet → NiceGUI migration. It supplements (does not erase) the longer historical `docs/ORCHESTRATOR_HANDOFF.md` and the start prompt `docs/ORCHESTRATOR_START_PROMPT.md`. At every new start, verify Git/CI/workers from the live environment; timestamps and hashes below are a snapshot, not authority over the live repository.

## Immediate resume prompt

Paste `docs/ORCHESTRATOR_START_PROMPT.md` into the new orchestrator, then read this file and `docs/ORCHESTRATOR_HANDOFF.md` in full. Continue using **Chat On Steroids Core (CoSCore) only** for repository files, PowerShell, pytest, Edge/native GUI, Git, GitHub CLI and worker operations. Do not start Codex tasks; the user explicitly said Codex tasks remain paused. Do not start an unrelated service or change permissions to work around worker launch problems.

First run `update_plan`; then inspect `git status`, branch/HEAD/upstream, `git worktree list`, current worker status, active processes and existing acceptance artifacts. Never replay an operation with an unknown outcome. Do not edit code until ownership and any active reviewer status are understood. Keep the source tree stable while an independent reviewer is hashing/reviewing it.

## Goal and approval boundary

- Final product goal: complete the Windows PixivVault Flet → NiceGUI migration, retaining legacy UX/data semantics and image performance. A Phase or PR is a milestone, not full migration completion.
- User has approved safe PR integration/merge **only after** independent review has no unresolved P1/P2, all required acceptance is green on the exact source, and PR-head CI is green. Main direct push is prohibited.
- Current request explicitly authorizes preparation of handoff documents and a start prompt, and a **non-force commit/push of those documentation files on the appropriate work branch**. It does not expand approval to merge, public release, deletion, billing, permission changes or activating Codex tasks.
- Original application root `S:/Projects/PixivVault` (DB, images, ZIPs, settings, Cookie) remains read-only. Do not publish secrets, real IDs, account details, fetched content or real images. Only the new managed root may be mutated by the authorized limited managed download path. No Pixiv write API.
- Do not use `git reset --hard`, `git clean`, `git stash`; do not discard another worker's dirty changes; do not replace the default product with an unaccepted build. Add only explicitly owned paths.

## Repository/worktree snapshot at handoff preparation

- Repository: `nuts-kinoco/PixivVault-NiceGUI`, remote `origin`.
- Worktree: `S:/Projects/PixivVault-NiceGUI-wt/scheduler-managed`.
- Branch: `phase9/scheduler-managed`.
- HEAD/base: `8d55756f8cbec9c9eaa2e9539b71d6af8af8b283`, same as `origin/main` when last checked. Phase8 PR #29 is merged; post-merge CI run `40001273903` had Windows/Ubuntu/Chromium all SUCCESS. Do not rerun/recreate Phase8.
- Phase9 source remains **uncommitted**. Latest `git status --short` listed 11 tracked modified files and 5 untracked source/test/docs files:
  - Modified: `docs/ORCHESTRATOR_HANDOFF.md`, `docs/PROGRESS.md`, `src/pixivvault_nicegui/managed_downloads.py`, `src/pixivvault_nicegui/services.py`, `src/pixivvault_nicegui/ui/shell.py`, `tests/test_download_services.py`, `tests/test_managed_downloads.py`, `tests/test_page_structure.py`, `tools/check_author_download_browser.py`, `tools/check_managed_downloads_browser.py`, `tools/check_native_window.py`.
  - Untracked: `docs/MANAGED_SCHEDULER_PHASE9_2026-10-24.md`, `docs/PHASE9_EVIDENCE_INCIDENT_2026-10-24.md`, `src/pixivvault_nicegui/managed_scheduler.py`, `src/pixivvault_nicegui/ui/scheduler_view.py`, `tests/test_managed_scheduler.py`.
  - Note: the new `docs/PHASE9_RESTART_HANDOFF_2026-10-24.md` and updated start prompt are documentation work for this handoff and may not yet appear in that earlier status output.
- `git diff --check` was clean. No open PR. Do not stage or commit the source/test/artifact changes as part of the handoff-doc push.
- Several Python processes created on 2026-10-23 at 15:14/15:20 local were present with unclear ownership. Do not terminate them; identify their parent/command/worktree before any action.

## Latest independent review findings (user-provided)

The last completed full-diff review returned **BLOCK, P1=0, P2=6, SHA-256 16/16 stable, no reviewer edits**. Findings:

1. **P2-1 stale deferred work for removed favourite authors.** `managed_scheduler.py` favourite configuration replacement failed to prune `deferred_work`, causing empty backlog runs after removing authors.
2. **P2-2 interrupted barrier bypass.** Regular scheduled dispatch respected `interrupted`, but backlog dispatch could run before explicit acknowledgement when the next due time was in the future.
3. **P2-3 queued aggregate history with no accepted jobs.** Unsupported/deferred-only or already-saved-only runs could create no `dispatch_items`, so history aggregation never advanced them out of `queued`.
4. **P2-4 author GET failure hidden by successful old jobs.** A later reconcile of an earlier accepted job could overwrite an aggregate failed history row as saved, hiding a later author's GET failure.
5. **P2-5 unsaved settings overwritten by timer refresh.** The 5-second UI refresh reset author IDs, interval and startup checkbox to persisted values while the user was editing.
6. **P2-6 no retry backoff for backlog failure.** Runner could immediately retry after failure and loop GET/ledger writes.
7. **Acceptance checker coverage gap (additional):** `tools/check_author_download_browser.py` checked saved generations/history but did not directly assert all 70 `dispatch_items` were `saved` and `deferred_work` was empty.

The reviewer classified excluding unsupported artwork types as a valid product boundary, but said such no-enqueue runs must not stay `queued`. It also confirmed removed-favourite deferred data as a real bug, particularly when the favourites list becomes empty.

## Remediation currently applied (must independently re-review)

- P2-1: `set_favourites` and `configure_all` delete deferred rows for authors not in the persisted favourites list in the same SQLite transaction. Regression test added for removing the only favourite and empty-list pruning.
- P2-2: backlog dispatch requires `state.last_status != 'interrupted'`.
- P2-3: `reconcile_jobs` finalizes no-item slots with `accepted=0`, `deferred>0` as `deferred` instead of leaving them queued.
- P2-4: aggregate reconciliation does not rewrite a failed history row to saved.
- P2-5: UI inputs mark settings dirty; polling refresh does not overwrite editable values until save succeeds.
- P2-6: runner waits 60 seconds after a failed/unknown backlog dispatch. `_dispatch_slot` returns an explicit success boolean so the backoff does not depend on whichever row happens to be newest in history.
- Checker: directly asserts `dispatch_items` grouped state is exactly `[('saved', 70)]` and `deferred_work` count is zero.
- Earlier self-audit fixes retained: accepted operation fingerprint must match active CAS generation before saved; shared per-work lock closes same-work acceptance/publication TOCTOU; historical saved state and pending notification are preserved; aggregate `partial → saved` recovery is allowed when an ambiguous job is later proven saved.

## Current latest acceptance snapshot

These results are from the latest P2-remediation source, not from older snapshots:

- Full pytest: **1204 passed / 1 skipped / 1 warning / 61.09s**, `artifacts/phase9-final-p2-remediation-final-20261024/full.xml`. Skip is privilege-dependent Windows symlink behavior; warning is Starlette/httpx TestClient deprecation.
- Edge: `artifacts/phase9-managed-scheduler-edge-p2-remediation-final-20261024/result.json`, success true, 4/4 (1280×720 and 1920×1080 × first run/restart), 59 source hashes match current source/build snapshot, original GETs 6 on first run and 0 on restart, no console/page/foreign-request errors, normal exit and port release 4/4.
- Windows packaging: snapshot `S:/Projects/PixivVault-NiceGUI-wt/phase9-native-build-p2-remediation-20261024`; copy `artifacts/phase9-native-copy-p2-remediation-final-20261024`; 1050/1050 file SHA-256 match. EXE SHA-256 `D630B570AC490EB19FF7C097F767512DBCF3D3961BF58B4092CCF89954897A0B`.
- Native acceptance: `artifacts/phase9-native-p2-remediation-final-acceptance-20261024.json` passed. UI handshake, image decode, viewer/library/download interactions, minimize/restore, normal exit; eight owned PIDs naturally exited; forced cleanup 0; port released; process log failures 0. Screenshot names are run-specific.
- Original-root invariant: prior manifest checked 37 database/major-source files for SHA/size/mtime match. This is not a recursive hash of every image/ZIP. No real Cookie production acceptance has been performed in this phase.
- These tests are **not a substitute for independent review**. The latest P2 source still needs a stable full-scope independent APPROVE and zero remaining P1/P2 before PR.

## Reviewer/worker status and recovery notes

- Earlier worker-4 approved only the shared per-work lock/race/lock-order scope; it did not approve the full Phase9 diff.
- Earlier worker-3's full review was INCONCLUSIVE because the source changed during review.
- A prior worker-5 launch failed with `chat did not report back in time (no browser opened it)`; prior worker families were inaccessible from a later conversation scope. Do not claim that these workers approved the latest P2 fixes.
- At the time of this handoff request, `agents action=status` in the new caller returned no retained workers for that caller. A fresh CoSCore worker was invited to review the latest P2-remediation source read-only, but its status/result is not yet available. Check status in the owning run before spawning anything else; do not duplicate a live reviewer.

## Immediate next steps

1. Finish and push this handoff/start-prompt documentation commit only, non-force, to `phase9/scheduler-managed`; verify remote commit SHA and read back the pushed files. This is documentation checkpointing only, not source integration or a PR.
2. Recheck the live reviewer state. If active, do not duplicate; collect final verdict and start/end hashes. If it failed with the known browser-start timeout, record exact result and decide whether a normal user-visible CoSCore session needs to be reopened. Do not alter permissions or launch another service.
3. On stable latest P2-remediation source, resolve any review findings. After any code fix, rerun full pytest and the relevant Edge/native acceptance on that exact source; do not reuse old green artifacts.
4. Once independent full-diff review is APPROVE/P1=P2=0 and all acceptance is green, stage only owned code/tests/docs, commit and push normally, open PR, wait for Windows/Ubuntu/Chromium CI on the exact PR head, then follow the already approved safe merge policy and verify post-merge CI/main SHA.
5. After Phase9 integration, continue the migration from fresh main in a dedicated worktree to the next substantial unported feature family. Do not declare the whole Flet→NiceGUI migration complete after Phase9.

## Exact status vocabulary

Always distinguish: implemented; unit-tested; full-pytest-green; Edge-accepted; Windows-native-accepted; independently reviewed; PR-head CI-green; merged; post-merge CI-green; real-Cookie/production accepted. If any evidence is missing, mark that gate pending. Handoff documents and a documentation-only push do not mean the successor has received the work or resumed development.
