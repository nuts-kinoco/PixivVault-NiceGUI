# P3 PersonalRankingView NiceGUI loop fixture — CoS handoff

Fixed input candidate 299: SHA 6ee0547c0b1a13b49d5fc3f1f5c4c5e872919700e2b7dccbda7e18082cc1b515, HEAD 2cf4a7a7286dd728e8df8125094498a3622babba. CoS impact.xml SHA 029fb248ead653b44ab6e9740acd8fd4fce3723041221f9917fd8302deb0162c records 119 PASS / 2 FAIL / 1 SKIP. The failures in test_personal_actual_controls_append_filter_and_persistence and test_personal_stale_and_close_drop_late_results stop at NiceGUI background_tasks.create because core.loop is None. The skip is optional AES-GCM, with no dependency installation.

Both original tests already use pytest.mark.asyncio; a running asyncio loop is insufficient because NiceGUI background_tasks.create explicitly uses nicegui.core.loop, normally populated at NiceGUI startup. The tests now point core.loop at the actual running pytest loop with monkeypatch and assert that the view's real task is registered in NiceGUI's running_tasks and belongs to that loop. They do not replace background_tasks.create, the worker, callbacks, or product functionality. Their original no-fetch-on-initialization, tag/fetch/pagination/filter persistence, delayed-result and close assertions remain. In finally, delayed fixture work is released, the view is closed, real pending load/context tasks are awaited, NiceGUI completion callbacks are given a scheduling turn, and Client/services are closed.

No product source or checker was modified. The reported failure alone is not evidence of a production loop initialization defect; normal NiceGUI app startup owns core.loop. The original product behavior after the formerly failing line remains untested until CoS executes the original assertions. A test-only loop binding is not a GUI or TLS acceptance result.

CoS focused verification (not run by author):

```powershell
Set-Location S:/Projects/PixivVault-NiceGUI-p3-personal-loop-fixture-20261028
$env:PYTHONUTF8='1'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH='S:/Projects/PixivVault-NiceGUI-p3-personal-loop-fixture-20261028/src'
& S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_external_vault_ui.py tests/test_personal_ranking.py tests/test_external_vault.py tests/test_vault_socket_fence.py tests/test_external_vault_crypto.py --basetemp=artifacts/p3-personal-loop-fixture-20261028/cos-owned/tmp --junitxml=artifacts/p3-personal-loop-fixture-20261028/cos-owned/impact.xml
```

Confirm both original tests reach and satisfy their late assertions, no unfinished task/unawaited-coroutine warning, the real loop/task linkage, and no synthetic transport call before the intended fetch. Preserve the optional AES skip if the existing runtime lacks AES-GCM. CoS previously reported socketpair duplicate shutdown and synthetic fence tests passing; physical TCP/TLS/blocked-send behavior, exact P2-9 acceptance, product GUI, independent review, P2-5 old partial-file policy and full P3 acceptance remain separate/unclaimed. Do not install dependencies, access real credentials, or send external traffic.
