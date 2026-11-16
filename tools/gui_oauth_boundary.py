"""CoS-only generated acceptance launcher; never sends network requests."""
from pathlib import Path
import argparse
import hashlib
import json
import re
import sys
import time
from types import SimpleNamespace

HERE = Path(__file__).resolve().parents[1]
REPO = HERE.parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE / 'src'))
# Read existing cryptography only; never install or modify dependencies.
sys.path.append(str(REPO / 'artifacts/p3-334-native/built-01/dist/PixivVault-NiceGUI/_internal'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scenario', choices=('account429', 'unknown-send', 'fatal429'), required=True)
    parser.add_argument('--run-name', required=True)
    parser.add_argument('--port', type=int, default=58131)
    args = parser.parse_args()
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,48}', args.run_name):
        parser.error('run-name must be a fresh simple generated name')
    if not 1024 <= args.port <= 65535:
        parser.error('invalid port')
    from nicegui import ui, app
    from pixivvault_nicegui.external_vault import ExternalVaultService, LegacyVaultCrypto, VaultError
    from pixivvault_nicegui.external_vault_transport import (
        ShaftTransport, UrlopenVaultRequest, VaultResponse, OAuthProfile, ACCOUNT, OAUTH, ONLINE,
    )
    from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopGate, _file_identity
    from pixivvault_nicegui.managed_network_stop_shared_scope import (
        ManagedUserNetworkStopScope, _OWNER_FILE, _GATE_FILE, _OWNER_SCHEMA, _file_identity_directory,
    )
    from pixivvault_nicegui.ui.external_vault_view import ExternalVaultView
    crypto = LegacyVaultCrypto()
    assert crypto.available, 'existing AES-GCM dependency is required'
    root = HERE / 'evidence/gui-runs' / args.run_name
    if root.exists():
        assert (root / 'SCENARIO').read_text() == args.scenario, 'scenario mismatch; use a fresh run-name'
    else:
        root.mkdir(parents=True)
        (root / 'SCENARIO').write_text(args.scenario)
    stop_root = root / 'generated-stop'
    if not stop_root.exists():
        stop_root.mkdir()
        gate = ManagedNetworkStopGate(stop_root / _GATE_FILE)
        (stop_root / _OWNER_FILE).write_text(json.dumps({
            'schema': _OWNER_SCHEMA, 'state': 'sealed',
            'root_identity': list(_file_identity_directory(stop_root)),
            'gate_identity': list(_file_identity(gate.path)),
            'lock_identity': list(_file_identity(gate.lock_path)),
            'history_proof': hashlib.sha256(b'generated-empty-history-only').hexdigest(),
        }))
    scope = ManagedUserNetworkStopScope(stop_root)
    calls = []
    class GeneratedWire(UrlopenVaultRequest):
        def fenced_request(self, method, url, *, observe_send=None, observe_status=None, **kwargs):
            kwargs['validate']()
            if url not in (ACCOUNT, OAUTH, ONLINE):
                raise VaultError('generated-route-refused')
            if observe_send:
                observe_send()
            route = {ACCOUNT: 'account', OAUTH: 'oauth', ONLINE: 'online'}[url]
            calls.append(route)
            with (root / 'calls.jsonl').open('a', encoding='utf8') as output:
                output.write(json.dumps({'route': route}) + '\n')
            if url == ACCOUNT:
                if args.scenario == 'account429':
                    mark = root / 'rate-observed'
                    if not mark.exists():
                        mark.write_text('generated429')
                        response = VaultResponse(429, {'quotas': [{'remaining': 0, 'resetsAt': (time.time()+5)*1000}]})
                    else:
                        response = VaultResponse(200, {'account': {'access_token': 'generated-access'},
                                                       'expiresAt': (time.time()+3600)*1000})
                else:
                    response = VaultResponse(200, {'account': {'refresh_token': 'generated-refresh'}, 'expired': True, 'uid': 42})
            elif url == OAUTH:
                if args.scenario == 'unknown-send':
                    raise VaultError('transport-failed')
                response = VaultResponse(429, {})
            else:
                response = VaultResponse(200, {'ok': True})
            if observe_status:
                observe_status(response.status)
            return response
    profile = OAuthProfile('generated-UA', 'ios', '1', '1', 'generated-salt', 'generated-client', 'generated-client-secret')
    transport = ShaftTransport(GeneratedWire(), oauth_profiles=[profile, profile])
    transport.bind_shared_pixiv_stop(scope)
    vault = ExternalVaultService(root / 'generated-vault', transport=transport, crypto=crypto)
    if not vault.status()['initialized']:
        vault.initialize('generated-password', '1\n2', 'generated-secret')
    app.on_shutdown(vault.close)
    @ui.page('/')
    def page():
        ui.label('合成受入専用: ' + args.scenario)
        ui.label('再起動時の合成パスワード: generated-password')
        view = ExternalVaultView(SimpleNamespace(external_vault=vault))
        view.set_active(True)
        diagnostic = ui.label().props('data-testid=generated-boundary-diagnostic')
        def render():
            diagnostic.set_text(json.dumps({
                'calls': calls, 'oauth_review_required': vault.status()['oauth_review_required'],
                'shared_stop': scope.gate.snapshot().blocked,
            }))
        ui.timer(0.5, render)
        render()
    ui.run(host='127.0.0.1', port=args.port, show=False, reload=False,
           title='PixivVault OAuth 合成受入候補')


if __name__ == '__main__':
    main()

