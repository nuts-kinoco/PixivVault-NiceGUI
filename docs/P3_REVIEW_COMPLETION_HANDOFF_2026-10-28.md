# P3 review follow-up: implementation candidate and CoS handoff

Fixed input: candidate 292, HEAD 2cf4a7a7286dd728e8df8125094498a3622babba, aggregate SHA 60b5312a7257ea41a57216152ce9fb2272eb52433a4a6957f08c2585d81ad7b4. The original 285 review remains BLOCK. No test, product import, GUI, network or build was performed in this implementation turn.

Changes made, pending CoS execution and independent review:

* Review 2: PracticalUgoiraJobs obtains completed accepted jobs via durable operation order and retained history, verifies the existing receipt before library/current/playback use, and no longer requires the row in the 50-job display window. Synthetic tests create 50 real failed ugoira-kind jobs or delete a displayed completed job, then check playback and restart.
* 3: extension commands resolve the bound child in retained JobService history; an explicitly retried parent restores an errored child before applying its existing revision-fenced retry. A synthetic >50 case asserts the same child and operation count. Unknown or ambiguous history fails closed.
* 4: auto-ZIP passes accepted work-ID/receipt proof pairs to the existing exporter and the exporter compares the actual receipt snapshot before creating a ZIP; its existing post-write generation check is retained. The fixture changes the snapshot between the two steps and expects failure with no ZIP or manifest.
* 5 (partial): new static projection intent, body and manifest files use existing staged exclusive publish; synthetic interrupted stage verifies no partial destination and edited destination is preserved. Preexisting partial files at final names remain fail-closed: current ownership metadata cannot distinguish an old interrupted write from a user edit. Auto-ZIP archive/manifest cross-publication recovery also remains unresolved. Do not mark this item closed.
* 6: a separate per-selection generation fences delayed ranking metadata, including viewer close and next selection; test clicks A then B, with and without close.
* 7: personal ranking context fetch now uses a worker thread and a captured, stale-fenced snapshot for availability, trend controls and filtering; the event loop test pauses context and invalidates the view. Initial, concurrent load and reactivation must be checked through UI and tests.
* 8: ONLINE work claims its queue without holding the queue lock over HTTP. A blocked synthetic ONLINE call is used to check lock returns and stale retry is not requeued.
* 9 (partial): session guard checks immediately before each transport request, stale OAuth/invalid reports are propagated, and ONLINE queue admission/retry is generation-fenced. The synchronous request callback still has a scheduling gap between the last guard and actual send. Therefore absolute prohibition of a new POST after lock returns is not established; a two-phase transport handoff or equivalent is needed, without making lock wait for an outstanding HTTP response. Do not mark this item closed.
* 11: regular-file and single-link identity checks apply before owner-lock writes and cache reads; cache hits update timestamps through an open file descriptor when supported and otherwise only the in-memory LRU. Tests use actual hardlinks in the synthetic root and verify external bytes and mtime stay unchanged. Windows filesystem/descriptor behavior needs CoS.

Existing partial/final output, old ZIP update, author rename, user-edit and CAS contracts are not changed by assumption. 288 automatic capacity GUI extended-path launcher and local thumb 404 are independent open CoS findings; 292 changes are not automatically validated by this candidate. L12 ledger/real-data GUI restrictions remain.

## CoS suggested impact run (not run here)

Use the existing S interpreter, isolated artifact basetemp, and the exact original tests, for example:

```powershell
Set-Location S:/Projects/PixivVault-NiceGUI-p3-review-completion-20261028
$env:PYTHONUTF8='1'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH='S:/Projects/PixivVault-NiceGUI-p3-review-completion-20261028/src'
& S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_ugoira_practical.py tests/test_ugoira_diagnostic_recovery.py tests/test_extension_application.py tests/test_legacy_zip_export.py tests/test_save_exports.py tests/test_bookmark_media_projection.py tests/test_direct_projection_references.py tests/test_ranking_ui.py tests/test_personal_ranking.py tests/test_external_vault.py tests/test_external_vault_crypto.py tests/test_shared_image_cache.py tests/test_readonly_root_boundaries.py tests/test_managed_scheduler.py tests/test_p3_card_size_shared.py --basetemp=artifacts/p3-review-completion-20261028/cos-owned/tmp --junitxml=artifacts/p3-review-completion-20261028/cos-owned/impact.xml
```

Also validate real GUI behavior with synthetic sources, page/search/close races, GIF/frames playback after restart and job-history deletion, actual Windows hardlink rejection, scheduler/size propagation and extended-path regressions. Use only owned processes and isolated ports. The unchanged assert/preservation gates and independent review are required. The exact original L12 row, full P3 acceptance, native/live/CI and independent audit remain separate and unclaimed.
