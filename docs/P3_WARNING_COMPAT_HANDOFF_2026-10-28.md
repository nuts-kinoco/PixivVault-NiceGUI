# P3 warning compatibility fix — CoS handoff

Fixed input candidate 294: SHA 5c9cf9dac574d8938c5001e2a00419953bd3f474a67b9340cddfd3f78a9b4359, HEAD 2cf4a7a7286dd728e8df8125094498a3622babba. CoS candidate 292 source review-fixes.xml SHA fdafe854f80cdf942e63ce9b9ee6c2c4856a41b9feae70deada78dcfad449a43 records two failures: test_largest_uses_original_stat_size_warns_and_ties_are_deterministic and test_old_intent_layout_and_multiple_folder_selection_warns. Both original assertions require the warning text 一番大きい. This change retains both original assertions and preserves the original selection logic.

The direct bookmark projection logger and SaveExports placement warning now use a fixed Japanese message saying that the largest candidate is used. The direct log contains no candidate name or ID; SaveExports retains its per-instance opaque token and never logs the input message. Existing UI warning detail is unchanged. tests/test_export_log_privacy.py now separately checks both loggers for the original wording and rejects the synthetic ID and folder name; the existing error-path test retains the private-path check. No arbitrary exception, user path, raw ID or folder name is interpolated into the revised log messages.

CoS targeted synthetic execution, using the original S venv and a new isolated artifact directory (not run by implementer):

```powershell
Set-Location S:/Projects/PixivVault-NiceGUI-p3-warning-compat-20261028
$env:PYTHONUTF8='1'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH='S:/Projects/PixivVault-NiceGUI-p3-warning-compat-20261028/src'
& S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_export_log_privacy.py tests/test_author_projection_layout.py tests/test_export_admission_stability.py tests/test_durable_export_acceptance.py --basetemp=artifacts/p3-warning-compat-20261028/cos-owned/tmp --junitxml=artifacts/p3-warning-compat-20261028/cos-owned/impact.xml
```

Verify the two CoS-failed original assertions, maximum/tie selection, logged Japanese warning for each route and omission of synthetic IDs, filenames and exception paths, while keeping UI warnings and existing safety assertions. No product test, import, build or GUI was run by implementer. Prior candidate 292 pass counts are not inherited as test results for this candidate. Items P2-5 old unowned partial files and P2-9 physical send-start race, wider independent review, full P3 acceptance, live and CI remain separate and unaccepted.
