# P3 capacity fixture repair — implementation handoff

Baseline fixed308 SHA 93ef40599d76e389fb030e16d4bcda69e020efb489d038319a85e72851e4defc. Candidate288 original CoS summary: artifacts/p3-capacity-auto-20261028/cos-owned-summary-r1/P3_288_CAPACITY_AUTO_CPU_GUI_INDEPENDENT_ACCEPTANCE_AND_LIMITS.md in fixed288. Codex did not execute tests, imports, GUI, servers, or product. Original 152-operation ledger stays unchanged.

## Facts and changes

1. CoS strict startup stderr reports sqlite3.OperationalError invalid uri authority %3F at ReadonlyLibraryRepository path.as_uri() when passed a Windows extended path. Existing fs_paths.sqlite_readonly_uri already encodes native paths for the job store. The helper now has a backwards-compatible immutable=True default; repository calls it with immutable=False. mode=ro and query_only remain, and WAL is intentionally visible, per docs/LIBRARY_ADAPTER.md. Tests add a >260-character Windows extended path with # and &, active WAL, visible data and denied DELETE; existing WAL and other adapter tests remain. The original 285 JobService URI result is not inherited as a result for this path.
2. CoS reports synthetic images were called work_id.png, which local_media_adapter._page_for does not identify as the accepted original flat title(work_id).png or work-folder form. The fixture now writes the flat title(work_id).png; the separate ZIP used for capacity selection has a non-image member, avoiding an ambiguous duplicate. Author1002 still has no store, preserving the known-zero capacity case: /media/9002/0/thumb.png may legitimately be 404. The new local-media regression uses the actual catalog. No product image fallback or old-image replacement policy is changed.
3. CoS cancel scenario reports its functional assertions succeeded; two ERR_CONNECTION_REFUSED were observed at shutdown with the page open. The new CoS checker reuses the exact cancellation, stale-worker, revisit, 50-to-62 and restart-independent operation assertions from the prior checker; it closes the browser after observation and before requesting owned-server shutdown. Browser-close errors are recorded and fail success, while owned-server cleanup still runs. 404 acceptance is narrowed to the deliberate author1002 missing image. No console errors besides that known 404 are accepted. This is a test teardown correction, not proof of a product console defect or of a clean GUI.

Old Flet baseline source: core.py at 3ac3aa0d47a64785251593bcb09bb2d5f6df374b uses title(work_id) and work-folder naming; library reading was adapted using docs/BASELINE.md and docs/LIBRARY_ADAPTER.md. The legacy repository, media adapter, native-path helper, and original CoS checker are reused. New checker/wrapper exist only to make the isolated reproduction and cleanup reproducible; CoS original files stay untouched.

## CoS validation

Use the existing Windows Python environment and a fresh S-only candidate-owned artifact area, never the original data or a prior marker root. Run, preserving exit status, JUnit and console:

```powershell
Set-Location 'S:/Projects/PixivVault-NiceGUI-p3-capacity-fixture-repair-20261028'
$env:PYTHONUTF8='1'; $env:PYTHONDONTWRITEBYTECODE='1'; $env:PYTHONPATH=(Join-Path (Get-Location) 'src')
New-Item -ItemType Directory -Force 'artifacts/p3-capacity-auto-20261028/cos-owned-gui-cancel-r1' | Out-Null
& 'S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe' -B -m pytest -p no:cacheprovider tests/test_library_repository.py tests/test_local_media_adapter.py tests/test_capacity_automatic.py --basetemp='artifacts/p3-capacity-auto-20261028/cos-owned-cpu-r1/tmp' --junitxml='artifacts/p3-capacity-auto-20261028/cos-owned-cpu-r1/results.xml'
$LASTEXITCODE
& 'S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe' -B tools/check_capacity_auto_cancel_browser.py
$LASTEXITCODE
```

Confirm test_capacity_auto.py exists before executing; if its existing filename differs, use the existing candidate288 auto-capacity CPU list. The GUI checker chooses a localhost ephemeral port and rejects 8089; run only once in a new output root. It uses the original fixture with extended path; no logical-path wrapper. Record normal exit, owned process/port release and screenshots/logs, expected missing author1002 404 only, no other 404/console/page/external errors. Validate 50/62, stale-result rejection, revision0/None then retry, and byte/readonly WAL behavior. For two viewport and first/restart restoration, use the original CoS GUI path with original fixture (no logical path substitution), without altering its assertions; the standalone cancellation checker covers the 1280 cancellation case. Preserve fixed308 hashes and separately report all failures, skips and evidence.

P2-9's 308 independent 80 PASS and real TLS assertions belong to fixed308; this candidate does not claim a new TLS rerun. Independent review, original L12 row, full P3/native/live acceptance remain outstanding. Only real CoS results establish whether the changes work on Windows.
