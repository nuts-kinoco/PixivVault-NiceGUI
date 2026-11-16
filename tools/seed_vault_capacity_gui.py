"""Prepare a fresh generated R4-sized root for the existing CoS GUI fixture.

No server, browser, network, Cookie, original data, or existing run is opened.
"""
import argparse
import json
import hashlib
from pathlib import Path
import re
import sys
import time

HERE = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-name', required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,48}', args.run_name):
        parser.error('run-name must be a fresh simple generated name')
    sys.path.insert(0, str(HERE/'src'))
    sys.path.append(str(HERE.parents[1]/'artifacts/p3-334-native/built-01/dist/PixivVault-NiceGUI/_internal'))
    from pixivvault_nicegui.external_vault import ExternalVaultService, LegacyVaultCrypto, LIMIT
    from pixivvault_nicegui.managed_downloads import _safe_path, _write
    run = HERE/'evidence/gui-runs'/args.run_name
    _safe_path(run)
    if run.exists():
        raise RuntimeError('run already exists; inspect its outcome and choose a fresh name')
    crypto = LegacyVaultCrypto()
    if not crypto.available:
        raise RuntimeError('existing AES-GCM dependency unavailable')
    run.mkdir(parents=True)
    vault = ExternalVaultService(run/'generated-vault', crypto=crypto)
    try:
        vault.initialize('generated-password', '1\n2', 'generated-secret')
        epoch, data = vault._snapshot()
        data['slots']['1'].update(
            rejected_refresh_hashes=[hashlib.sha256(('generated-old-'+str(i)).encode()).hexdigest() for i in range(128)],
            oauth_send_pending=False, last_refresh_attempt_at=time.time())
        data['secret_key'] = ''
        size = len(json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode())
        data['secret_key'] = 'g'*(LIMIT-32-size)
        original = vault._compact_rejections
        # Fixture construction only: create a valid pre-upgrade R4 generation.
        # Normal R5 compaction is restored before any UI operation.
        vault._compact_rejections = lambda *a, **k: None
        try:
            vault._publish(epoch, data)
        finally:
            vault._compact_rejections = original
        _write(run/'SCENARIO', b'account429')
    finally:
        vault.close()
    print(json.dumps({'generated_run': str(run), 'scenario': 'account429',
                      'seed_plaintext_bytes': LIMIT-32, 'gui_launched': False}))


if __name__ == '__main__':
    main()

