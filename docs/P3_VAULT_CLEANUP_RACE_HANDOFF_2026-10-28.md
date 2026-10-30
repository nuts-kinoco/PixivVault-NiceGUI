# P2-9 D1: cleanup and lock race

Fixed313 SHA 830a76a0675e98eca7ae4ca19bd8ca6b66de4745100d23d721e871f80f3b70cf; HEAD 2cf4a7a7286dd728e8df8125094498a3622babba. The candidate preserves its L12/D15 work and existing P2-9 assertions. Fixed308's CoS 80 PASS is baseline evidence, not a test result for this candidate.

D1 is an availability race, not a confirmed bypass: after connected duplicate creation fails, the request finally closes its original socket before unregistering the pending fence entry; a concurrent lock can observe the closed owner and report socket-shutdown-unverified. A similar close/unregister window exists in failed connection attempts. Conversely, unregistering before close would leave the live connection without its fence. The change uses the existing reentrant fence gate during closing the connection/owner and registration context exit as one critical section. It covers both successful and failed connection paths. The gate is held for close and deregistration only, not network response waiting or thread joining. The registered duplicate stays visible until its owned request aliases close. Original epoch validation and invalidation remain in place.

The new regression injects failure only on the second socket dup (after successful connect), pauses owner close, starts concurrent invalidate, checks it cannot cross unfinished cleanup, releases close, verifies lock completes, request returns transport-failed, no fixture send occurred, old epoch cannot register, no fence entry remains, and structured duplicate failure was retained. Existing real TLS/socketpair tests and original asserts remain unchanged; this is synthetic error injection, not proof of real socket shutdown behavior. Close failures beyond the described dup/lock race are not newly claimed as verified.

CoS: create a fresh S-only artifact directory and run the candidate's original TLS, fence, vault and UI tests including the new D1 case, with the existing environment and synthetic localhost fixture:

```powershell
Set-Location 'S:/Projects/PixivVault-NiceGUI-p3-vault-cleanup-race-20261028'
$env:PYTHONUTF8='1'; $env:PYTHONDONTWRITEBYTECODE='1'; $env:PYTHONPATH=(Join-Path (Get-Location) 'src')
New-Item -ItemType Directory -Force 'artifacts/p3-vault-cleanup-race-20261028/cos-owned-r1' | Out-Null
& 'S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe' -B -m pytest -p no:cacheprovider -o junit_family=xunit1 tests/test_vault_tls_loopback.py tests/test_vault_socket_fence.py tests/test_external_vault.py tests/test_external_vault_ui.py --basetemp='artifacts/p3-vault-cleanup-race-20261028/cos-owned-r1/tmp' --junitxml='artifacts/p3-vault-cleanup-race-20261028/cos-owned-r1/impact.xml'
$LASTEXITCODE
```

Collect JUnit and console hashes, original real-TLS/handle-reuse checks, lifecycle files, and owned process/port cleanup. All network remains test-owned loopback with verified fixture CA/hostname, no real secrets or trust changes. Test/import/build/server/GUI were not run by Codex. Independent review and broader P3 acceptance remain pending.
