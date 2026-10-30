# P2-9 Windows TCP/TLS revocation correction (unexecuted)

Baseline: fixed307, branch phase11/p3-vault-tls-fixture-20261028, HEAD 2cf4a7a7286dd728e8df8125094498a3622babba; aggregate ee6b7552d001ac643d32ffb41d6e34f27d135bc43012b64ef1a23aa7a138895a. CoS original 67 PASS / 7 FAIL / 0 SKIP is not inherited as this candidate's result. Baseline artifacts/processes are not modified or copied. Original XML and console SHA match the parent report.

## Evidence and uncertainty

Six original tests raised socket-shutdown-unverified before their post-lock assertions. The swallowed OS exception was not recorded. Therefore their actual errno and whether any post-lock bytes were accepted remain unknown. The descriptor reuse case failed because the test wrapper attempted an arbitrary attribute assignment on a built-in socket; it never reached reuse assertions. Corrected bookkeeping uses a set of object identities during registration, discarded on scope exit; no arbitrary socket attributes are used.

Official references:
* https://learn.microsoft.com/en-us/windows/win32/api/winsock2/nf-winsock2-shutdown : WSAENOTCONN for a connection-oriented unconnected socket; successful shutdown disables further sends. No successful shutdown is claimed on an active-socket error.
* https://learn.microsoft.com/en-us/windows/win32/api/winsock2/nf-winsock2-wsaduplicatesocketa : duplicated descriptors share state, and the example establishes the connection before handing off the duplicate. This documentation alone does not prove the specific cause of the five connected-case failures.
* https://github.com/python/cpython/blob/v3.13.11/Modules/socketmodule.c : socket_dup uses WSADuplicateSocketW / WSASocketW on Windows.

## Product changes

SocketFence distinguishes pending original ownership from a connected duplicate. Registration and stale-epoch rejection remain under the same gate. During pending cancellation it shuts down the original and closes that Python socket, so a paused later connect cannot use it. Only exact ENOTCONN / WSAENOTCONN is accepted in this phase, and only together with actual owner close; other OS errors remain failures. A connect already in progress can finish or fail, but activation rejects its old epoch before TLS or HTTP.

After connect, before any TLS bytes, transport calls activate under the fence gate. A new duplicate of the connected resource replaces the pre-connect duplicate before SSL detaches the original. Invalidation uses that connected duplicate and still fails closed on every shutdown error. This addresses the missing lifecycle transition; whether it resolves the connected Windows failures must be demonstrated by CoS. No timeout, assertion, certificate check or lock-completion contract was weakened. No raw fd integers are used for revocation; descriptor reuse cannot redirect the fence to an unrelated object.

Diagnostics are bounded to the last 32 structural events, with phase/operation/errno/winerror only. No socket addresses, IDs, exception messages, credentials or paths are included. The transport's existing public generic errors remain unchanged.

## CoS one-batch entry

Do not run real service traffic. Use existing Python 3.13.11/OpenSSL and public fixture CA/cert only. Keep CERT_REQUIRED and hostname checking on. No dependency install or Windows trust changes. Use a new S-only artifact run directory:

```powershell
Set-Location 'S:/Projects/PixivVault-NiceGUI-p3-vault-tls-revoke-20261028'
$env:PYTHONUTF8='1'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
New-Item -ItemType Directory -Force 'artifacts/p3-vault-tls-revoke-20261028/cos-owned-r1' | Out-Null
& 'S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe' -B -m pytest -p no:cacheprovider -o junit_family=xunit1 tests/test_vault_tls_loopback.py tests/test_vault_socket_fence.py tests/test_external_vault.py tests/test_external_vault_ui.py --basetemp='artifacts/p3-vault-tls-revoke-20261028/cos-owned-r1/tmp' --junitxml='artifacts/p3-vault-tls-revoke-20261028/cos-owned-r1/impact.xml'
$LASTEXITCODE
```

Preserve original seven failed cases and all their following assertions: no late connect/request, worker termination, direct post-lock SSLSocket send rejection, and descriptor reuse. New regressions separately cover pending not-connected close (POSIX and Windows codes), connected duplicate replacement before detach, connected ENOTCONN rejection, unexpected OS failure/privacy, and late-connect activation rejection. Old-epoch/re-unlock, redirect/reconnect guards and verification checks are retained.

Each constructed session records its listener port and owner PID before assertions. Teardown writes lifecycle-PORT.json under that test tmp directory even after failure, containing pre-cleanup client/request state and fence events, plus final client/server/worker liveness and listener closure. This provides failed-case ports missing from original307. If cleanup fails, it is an error, not a passing test. Retain these JSON files plus JUnit/console hashes, before/after source-manifest comparison and owned process/listener/connection status. Do not touch 8089, EMA or any unrelated process. A descriptor-recycling skip remains unverified rather than a pass.

A peer receiving buffered bytes after lock is not evidence of a new application send. Keep pre-kernel acceptance distinct from bytes already accepted before lock. Kernel retransmission / handshake packets from a connect already submitted are not new user TLS or HTTP sends; this candidate does not claim their timing has been measured. A direct send attempted only after successful lock is separately asserted to fail.

## Exit status

Code and regression creation only. No pytest/import/syntax/build/server/GUI/product execution by Codex. Windows shutdown effectiveness, failure errno, all original post-lock assertions, lifecycle output and independent review are pending CoS. P2-9/P3 acceptance and operation ledger are unchanged. No additional design choice has been adopted; if the connected duplicate still cannot be revoked, report its exact structural diagnostic and stop acceptance rather than swallowing it.
