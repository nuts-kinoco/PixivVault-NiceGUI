# P3 held-warning compatibility — CoS handoff

Fixed input candidate 298: SHA 7b624b0b3a3553ccbc11fbf2a27d70113ae6cc828f127f40a8ad84740e3c5646, HEAD 2cf4a7a7286dd728e8df8125094498a3622babba. CoS fixed295 impact.xml SHA caf7ded8d7b919256b9a33ecca7362af6adb2badc6205597fd15c27e6c677089 records test_status_is_visible_in_queue_and_repeated_warning_is_suppressed as the single failure, with zero matching warnings. Queue hold status itself was observed.

The SaveExports error logger now prefixes its existing opaque token and allowlisted category with the original fixed Japanese wording 保存後処理を保留. Raw keys, IDs, exception text and paths do not enter the logger. No scan state, reason selection, queue display, control, or suppression logic was changed. tests/test_export_log_privacy.py adds repeated same-reason and changed-reason checks alongside assertions that synthetic ID, folder, and exception path do not appear. The original queue visibility and warning-count assert in tests/test_durable_export_acceptance.py is unchanged. The earlier 一番大きい checks remain unchanged.

CoS focused execution using the existing S venv and a fresh artifact root (not run by author):

```powershell
Set-Location S:/Projects/PixivVault-NiceGUI-p3-held-warning-20261028
$env:PYTHONUTF8='1'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH='S:/Projects/PixivVault-NiceGUI-p3-held-warning-20261028/src'
& S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_durable_export_acceptance.py tests/test_export_log_privacy.py tests/test_export_admission_stability.py tests/test_author_projection_layout.py --basetemp=artifacts/p3-held-warning-20261028/cos-owned/tmp --junitxml=artifacts/p3-held-warning-20261028/cos-owned/impact.xml
```

Verify the original single hold-warning assertion, queue reason, repeated-warning suppression, a new notification when the reason changes, both largest-layout warning routes and absence of fixture IDs, folder names and exception paths. Prior 295 results are not tests of this candidate. Candidate 298 socket/TLS fence and 294 ZIP recovery remain unverified here; Windows socket propagation, trusted adapter protocol, old partial-file handling, independent review and full P3 acceptance remain separate. No tests, import, build, GUI or network executed by author.
