# P3 independent review: initial fixes and CoS handoff

Baseline: fixed candidate 288, HEAD 2cf4a7a7286dd728e8df8125094498a3622babba, SHA eeaa2b76b5f28a51f55d0b77aed8c4220ca5ab501b096ec26f7e034fed5c41da. Prior 285 review remains BLOCK until all findings are resolved and independently reviewed.

Implemented, untested:

* P1: compare every configured writable media/storage root against each declared read-only image root or read-only database directory before any media owner is initialized, both CLI and register_pages paths, and again in ApplicationServices. Direct configure_save_exports also checks readonly diff and DB roots. Equal, ancestor and descendant paths reject. Readonly roots are not compared to each other by this new check. Test synthetic pairs and verify no directory is created on rejection. Existing root checks remain in place.
* P2-10: validate a candidate token response with the existing vault normalization before modifying or publishing the live slot. Invalid field types preserve current generation and unlockability. Tests cover truthy invalid token values, invalid numerics, locked/reopened state. No transport calls are run by author.
* P2-12: redact export warning logs using per-instance opaque identifiers and fixed warning categories; the existing user-facing warning detail is retained. Synthetic caplog regression checks IDs and folder names.
* P2-13: return work IDs newly transitioned to verified saved state from reconcile_jobs, enabling existing OS notification dispatch. Repeat reconciliation returns no new item and existing UI notification queue remains separate.
* P2-14: propagate common card size changes to PersonalRankingView; extend shared-size regression to personal entry.

Review and verify interaction with existing standalone and combined-media configurations. The additional readonly DB-directory boundaries are intentionally strict, consistent with existing sidecar boundaries; CoS must check legitimate separate sibling roots, all overlapping pairs, and managed/new media startup on isolated fixtures.

Not implemented in this batch: P2-2 ugoira durable receipt index; 3 extension retired child retry; 4 ZIP receipt generation race; 5 static export interruption; 6 ranking late selection; 7 personal ranking UI blocking; 8-9 vault locking and session/network fencing; 11 cache hardlink safety. These remain open; the P1, 10, 12, 13 and 14 changes are unverified until tests and independent review. Existing candidate 288 capacity work has not been reverified by this implementation turn.

CoS: run the original affected suites and new test, using the existing S venv and an isolated S artifact basetemp. Do not modify frozen candidates. Example from this candidate root:

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH='S:/Projects/PixivVault-NiceGUI-p3-review-fixes-20261028/src'
& S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_readonly_root_boundaries.py tests/test_readonly_acceptance_boundaries.py tests/test_three_media_services.py tests/test_managed_download_services.py tests/test_capacity_normal_configuration.py tests/test_external_vault.py tests/test_external_vault_crypto.py tests/test_export_log_privacy.py tests/test_export_admission_stability.py tests/test_author_projection_layout.py tests/test_managed_scheduler.py tests/test_p3_card_size_shared.py tests/test_personal_ranking.py --basetemp=artifacts/p3-review-fixes-20261028/cos-owned/tmp --junitxml=artifacts/p3-review-fixes-20261028/cos-owned/impact.xml
```

Also exercise normal combined managed/static, novel and ugoira fixtures and export init with nonoverlapping roots; inspect notification backend delivery ON/OFF and repeated reconciliation; confirm vault failed refresh leaves CURRENT byte-identical and unlock works; exercise the personal ranking size control and other existing entries. Test and GUI execution, independent audit, native/live/CI, and full P3 acceptance were not performed by the author. No real data, credentials, external API, OS changes or deployment.
