# P3 vault socket fence — implementation handoff

Fixed input candidate 295: SHA f965a1617d36a2584a13b023e3d45385ffb23a33a81c4948b50542de68844397, HEAD 2cf4a7a7286dd728e8df8125094498a3622babba. Implementation only: no pytest/import/build/GUI/network by author. The review remains BLOCK until CoS execution and independent review.

## Scope and boundaries

P2-9: replace the default urllib request with a direct http.client HTTPS request. Destination allowlist, no Cookie, no proxy, no automatic redirect/auth/retry and 2 MiB JSON response limit remain. Before each TCP connect, a duplicate socket is registered under the same fence gate that checks the captured epoch. The original socket is then connected and TLS-wrapped. Lock clears session and online queue, advances the socket fence epoch and calls shutdown on every registered duplicate before returning. Handles are retained until the request scope exits; only handle objects, not reusable fd integers, identify them. If shutdown fails, lock raises socket-shutdown-unverified and the UI clears inputs and displays a fixed failure message instead of claiming successful interruption. No response wait or worker join is added to lock.

A top-level session guard captures a socket epoch; nested guards retain it. Search and refresh check the vault epoch, and an old search does not adopt an epoch from a later unlock. A stale publish does not clear a newly unlocked session's pending work. The online queue retains its separate task-generation guard. Injected request adapters used inside a ShaftTransport session must supply fenced_request(fence, epoch, validate, ...); unsupported callbacks fail before invocation. Injected whole-vault transports must supply session_guard and clear_pending. Such injected code is trusted to honor the protocol; the default production adapter is the one implemented here. Test-only adapters do not perform network I/O.

The safety claim depends on Windows socket.dup() and shutdown propagating to the original socket/SSLSocket. This has not been verified in this turn. TCP connect, TLS negotiation, request and response remain bounded by existing socket timeout, but DNS latency and platform shutdown behavior must be measured. Invalidation while a socket is not connected can cause shutdown to fail; that is reported as unverified rather than success. Data accepted into kernel buffers before successful lock may arrive later; no new kernel-accepted send after successful shutdown is the intended boundary. The active TLS implementation and OS behavior still require independent review.

## CoS execution (unrun)

```powershell
Set-Location S:/Projects/PixivVault-NiceGUI-p3-vault-socket-fence-20261028
$env:PYTHONUTF8='1'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH='S:/Projects/PixivVault-NiceGUI-p3-vault-socket-fence-20261028/src'
& S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_vault_socket_fence.py tests/test_external_vault.py tests/test_external_vault_ui.py tests/test_external_vault_crypto.py tests/test_personal_ranking.py tests/test_ranking_source.py tests/test_residual_service_connections.py --basetemp=artifacts/p3-vault-socket-fence-20261028/cos-owned/tmp --junitxml=artifacts/p3-vault-socket-fence-20261028/cos-owned/impact.xml
```

Synthetic checks: lock after socket creation/before registration, registered before connect/TLS, lock during TLS before and after fixture bytes, blocked send, stale job after unlock, refusal of unfenced adapter, shutdown failure and UI notice, fd identity reuse, no redirect or automatic reconnect, existing ONLINE/refresh/search semantics. New Windows-only socketpair test must show that shutdown on the registered duplicate makes the original socket unable to send. Also independently exercise an owned loopback TCP/TLS fixture with blocked send, connect in progress, actual SSLSocket after wrap, shutdown races, fd reuse and two simultaneous sessions, verifying no kernel-accepted bytes after a successfully returned lock; pre-lock accepted bytes may arrive later. Capture failure as unverified rather than waive a failed Windows condition. No actual Pixiv/nana7mi credentials or external URLs, no OS changes.

This candidate does not modify P2-5 historical partial file policy or claim its acceptance. The warning regression, fixed295 results, all 14 review items, broader P3 acceptance, CoS/Claude independent review, live/CI/native are not automatically verified.
