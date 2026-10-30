# P3 closure implementation handoff — 2026-10-26

Candidate: S:/Projects/PixivVault-NiceGUI-p3-closure-20261026
Branch: phase11/p3-closure-20261026
HEAD: 2cf4a7a7286dd728e8df8125094498a3622babba
Input: frozen residuals 888cfcb80136e2d2643e22fbbd920ff01d6d65b789ac63f3c2bb3021885faab3 (140 files).

## Product changes and original operations

- Original API retry settings now bind all configured static/novel/ugoira/public-image readers. Total HTTP tries retain original meaning; typed auth/identity/payload/deadline failures escape. Job checkpoints cover pacing/retry waits. Phase10 immediate global-failure stop remains a no-retry scope.
- Original discovery initial_load (fixed original discovery_tab.py around 4722–4788): configured application startup and first learning tab share once/singleflight, local trend first, then following context and sources. Shutdown marks claims inactive and drains before closing readers.
- Original extension content.js wake URI pixivvault://start is parsed as an inert launch argument; payload/query/command URIs reject. Native registration command preserves explicit managed-root configuration and uses this checkout's absolute source launcher. No registry call was performed.
- Original server.py /download work and user commands: explicit --extension-bridge binds only owned 127.0.0.1:25010 after startup. A busy port reports unavailable in preferences; no existing listener/process is touched. Existing origin/Host/schema constraints reject Cookie synchronization and arbitrary routes.
- HTTP 200/jobId now means an existing JobService command ledger accepted the immutable request. The worker dispatches existing media jobs, waits for their done state and verifies current receipts; failures remain visible in the existing queue. Parent pause/resume/stop use CAS and control only a child originally accepted by this command. Active duplicate request shares the command job. Interrupted commands reopen as retryable errors; explicit Retry keeps the command identity/attempt contract. Binding journals recover accepted child job identities; no receipt format is replaced.
- Work requests preserve novel/illustration namespaces via original is_novel flag. A bounded same-route novel author resolver pins the subsequent strict author-checked detail read. Original formats come from existing preferences.
- Original core.py new_only and pixiv_client.get_user_novels: newest-first 48-item metadata batches stop at the first verified saved matching generation; partial scans are never submitted as complete deletion observations. Full author behavior remains separate.

## Validation status

This final candidate was NOT executed, syntax-checked, linted, or opened in a GUI in the resumed 22:11 JST turn. CoS owns that validation. Earlier closure-snapshot results are retained as historical evidence only: root CLI/startup24 PASS, media/service102 PASS, learning174 PASS, retry+resolver491 PASS, URI/standalone bridge71 PASS. These overlap and predate the final extension/new-only integration; do not sum or inherit them as final acceptance. Old residuals2502 PASS/3 SKIP belongs only to 888cfcb.

The one matched rejudgment of old combined-browser-explore-3 was approved and executed: exit1, both initial viewport runs failed before job acceptance, no external/page/console errors, owned processes exited and ports released. The fixture lacked normalized illust_type; fixture/checker code was corrected in this candidate. Its revised browser result is still unverified.

A distinct new invocation of tests/test_p3_residuals_fixtures.py was denied BEFORE CreateProcess by automatic approval review: “The command runs pytest and writes test artifacts, but the trusted user instruction explicitly reserves test execution for CoS rather than the implementation agent.” No output directory, retry, rejudgment, alternative execution, or new GUI run followed that denial. See artifacts/p3-closure-20261026/new-fixture-blocker.json and browser-fixture-write-end/WRITE_END.md. Latest parent explicitly did not request rejudgment.

## CoS steps (not run here)

Use existing S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe with -B. Set PYTHONDONTWRITEBYTECODE=1, PYTHONUTF8=1, PYTHONPATH=<candidate>/src, TEMP/TMP=<candidate>/artifacts/p3-closure-20261026/cos-owned (create only that dedicated directory). No dependencies or OS changes. CPU codec, if needed, is the existing C:/Users/nat/miniconda3/envs/stenv/python.exe; no GPU.

1. Verify final/manifest.json, final/frozen-preservation.json and final/write-end.json before taking writer ownership. Check port25010 before any explicit bridge fixture; do not use or stop an existing owner. Never use8089.
2. Run pytest with -p no:cacheprovider --basetemp <cos-owned>/pytest-base --junitxml=<cos-owned>/focused.xml against tests/test_extension_application.py tests/test_novel_new_only_reader.py tests/test_author_new_only.py tests/test_p3_closure_wiring.py tests/test_extension_bridge.py tests/test_uri_launch.py tests/test_request_retry.py tests/test_novel_author_resolver.py tests/test_discovery_initial.py. Expected: durable acceptance before completion, exact active dedup, child failure shown, explicit retry increments attempts, parent/child CAS pause/resume/stop, restart requires explicit retry and no constructor GET, current receipt required, novel metadata batches stop before second batch, no partial deletion inference, hostile URI/Host/origin rejected. Filenames must be checked against actual manifest; missing tests are a blocker, not a skip.
3. Run existing media/service/metadata/author/learning regressions relevant to changed files, then final full pytest only under CoS's applicable authority. Record exact snapshot hash, commands, skips and failures. The separately denied fixture/checker must be handled under CoS's own authority, not silently replayed by Codex.
4. Fixture loopback browser checker tools/check_p3_residuals_browser.py uses synthetic data and free owned ports. Inspect the tool first; run only under CoS authority. Expect both viewport sizes and restart to complete review save, author3-media save, queue/receipt/library checks, zero external requests and normal owned cleanup. Keep any failure artifacts; no blind resend after uncertain outcome.
5. Add a dedicated fixture launch for ExtensionApplication + ExtensionBridge(port=0); POST original schemas, expect durable jobId then inspect queue/receipts. Never start production bridge25010 or read a real Cookie merely to validate a fixture. Native registry registration, real extension/native acceptance and live GET remain separate unperformed gates.

## Remaining gates and decisions

- Final fixture/GUI acceptance is pending CoS, including the new command journal and startup/extension coexistence. Code creation is not product acceptance.
- Independent code audit, packaged native Windows first/restart, live GET, exact-head CI and total UX/Flet comparison remain unperformed and separate. P4 decision returns to dot.
- Duplicate isolation / ZIP partial deletion, immutable receipt/CAS meaning, auto-ZIP append generations and bookmark placement remain policy decisions. This candidate does not adopt tombstones/new generation removal/legacy ZIP mutation. Recommendation: decide a versioned non-destructive export or exclusion contract before any mutation; retain original saved bytes and immutable receipts until then. Original append-in-place ZIP behavior conflicts with immutable generation proofs; do not hide that difference with a success claim.
- All source changes are confined to this candidate. No commit/push/main/publication, real API/Cookie/model/GPU, install, OS/auth changes, or original-data changes.
