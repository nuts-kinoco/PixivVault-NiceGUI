# P2-9: real localhost TCP/TLS fixture handoff

Implementation only. Product src/ and tools/ are retained byte-for-byte from candidate 300. The public CPython v3.13.11 test CA and localhost certificate/key are included under tests/fixtures/cpython_tls with their source, SHA-256 and PSF licence. The key is deliberately public fixture data, never a real secret. No system trust, browser configuration, real account, real service, dependency installation, or external network is needed. Only the test SSLContext trusts the fixture CA; CERT_REQUIRED and hostname validation remain on. A wrong-host case must fail certificate verification.

The test listener binds only 127.0.0.1 on a kernel-selected ephemeral port, with the client DNS and connect constrained to that listener. It uses real TCP, real SSLSocket and the production request/fence/lock implementations. Teardown releases gates, joins owned threads, closes the listener/connections, and fails if owned threads remain. JUnit records case, listener port, TLS version, verification mode, connection/request counts and error code; no request body or synthetic credentials are logged.

## CoS run (not executed by Codex)

Use the existing Windows environment and an isolated **new S:** artifact directory. The following process must be owned by CoS; do not alter other ports/processes. Set pytest temp and JUnit locations inside the candidate-owned artifact area. For example:

```powershell
Set-Location 'S:/Projects/PixivVault-NiceGUI-p3-vault-tls-fixture-20261028'
$env:PYTHONUTF8='1'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
New-Item -ItemType Directory -Force -Path 'artifacts/p3-vault-tls-fixture-20261028/cos-owned-r1' | Out-Null
& 'S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe' -B -m pytest -p no:cacheprovider -o junit_family=xunit1 tests/test_vault_tls_loopback.py tests/test_vault_socket_fence.py tests/test_external_vault.py tests/test_external_vault_ui.py --basetemp='artifacts/p3-vault-tls-fixture-20261028/cos-owned-r1/tmp' --junitxml='artifacts/p3-vault-tls-fixture-20261028/cos-owned-r1/impact.xml'
$LASTEXITCODE
```

Capture exit code, JUnit/console hashes, candidate manifest comparison before/after, and confirmation that owned threads/processes and ephemeral listeners ended. If a test fails, preserve the original output; do not modify the product, weaken an assertion, increase timeouts to pass, use the actual service, disable TLS verification, or add a certificate to Windows trust. Optional AES-GCM remains subject to the existing environment's documented skip; install nothing. Descriptor recycling is platform-dependent: the explicit skip means this case is **unverified**, not passed.

## What is checked and remaining limits

* Verified localhost TLS plus explicit wrong-host rejection, and a single owned request.
* Lock before socket registration, followed by re-unlock, rejects the old epoch. Lock after registration but before connect is checked without assuming Windows shutdown of an unconnected duplicate succeeds. A failure to confirm shutdown must remain a failure.
* The production ordering registers before connecting. A socket guard asserts that ordering for every test connection, so an unregistered connect is rejected rather than artificially creating a production state after connect but before registration.
* Lock after TCP connect before TLS, while the server holds ClientHello, after verified TLS but before HTTP, and during an actually blocked TLS send. Each scenario is locally coordinated by events; deadlines only detect a hung fixture. Bytes accepted before lock can later reach the peer; a peer's receive timestamp is not evidence of post-lock kernel acceptance. The blocked-send scenario checks lock completion and worker outcome; the separate live SSLSocket test attempts a send after lock and expects an OSError, directly exercising the duplicate's Windows shutdown effect.
* Redirect returns 302 without a second connection; a server that drops after handshake checks that no automatic reconnect is attempted. An external-DNS/connect guard fails immediately if either tries another endpoint. A real socket-handle reuse case checks that a later unrelated socket is not shut down; inability to obtain reuse is an explicit skip.
* This does not prove behavior for every proxy/TLS platform, every kernel race, a packaged native build, real remote services, or independent review. The socket fence's existing live implementation may fail a genuine Windows shutdown case; report rather than reinterpret it. Review the older socketpair-only evidence separately. Product and P3 acceptance remain open.
