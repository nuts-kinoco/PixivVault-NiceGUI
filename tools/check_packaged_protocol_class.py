"""N06/N07: nonce HKCU registration -> packaged EXE -> cold/warm -> restore.

Only a fresh nonce-owned class is registered. ShellExecuteExW uses its explicit
read-only class handle (SEE_MASK_CLASSKEY); the real pixivvault registration
is never opened, changed, or selected. The registered product command receives
the unchanged pixivvault://start URI. No product callbacks/parsers are patched.
This proves class-key registry dispatch, NOT default-scheme selection or the
product's registration-button UI. All inputs are synthetic, no credentials.

Microsoft contract: SHELLEXECUTEINFOW hkeyClass/SEE_MASK_CLASSKEY.
https://learn.microsoft.com/windows/win32/api/shellapi/ns-shellapi-shellexecuteinfow
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes as W
from hashlib import sha256
import json
import os
from pathlib import Path
import socket
import sys
from types import SimpleNamespace
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))
from tools import check_uri_native as uri
from tools import check_protocol_registration_windows as registry_check
from tools.freeze_windows_candidate import verify_frozen
from pixivvault_nicegui.desktop_integration import WindowsProtocolRegistration
from pixivvault_nicegui.native_profile import NativeProfileStore
from pixivvault_nicegui.uri_launch import build_protocol_command, START_URI


def require(value, label):
    if not value:
        raise registry_check.CheckFailed(label)


def new_json(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=True, indent=2)
        stream.write('\n')


def _class_launch_info(exe: Path, handle: int):
    require(type(handle) is int and handle > 0, 'read_only_class_handle_missing')
    info = uri._StartupInfo()
    info.cbSize = ctypes.sizeof(info)
    # CLASSKEY includes CLASSNAME; the actual key handle is authoritative.
    info.fMask = 0x03 | 0x40 | 0x100 | 0x400
    info.lpVerb = 'open'
    info.lpFile = START_URI
    info.lpDirectory = str(exe.parent)
    info.hkeyClass = handle
    info.nShow = 1
    return info


def fresh_ports():
    sockets = []
    try:
        for _ in range(2):
            item = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sockets.append(item)
            item.bind(('127.0.0.1', 0))
        return tuple(item.getsockname()[1] for item in sockets)
    finally:
        for item in sockets:
            item.close()


def candidate_binding(candidate: Path):
    require(candidate.is_absolute() and '..' not in candidate.parts
            and candidate.is_relative_to(ROOT / 'artifacts'),
            'candidate_outside_owned_artifacts')
    registry_check.no_links(candidate)
    frozen = candidate / 'frozen-01'
    verified = verify_frozen(frozen)
    copy = candidate / 'standalone-copy-r1'
    exe = copy / 'PixivVault-NiceGUI/PixivVault-NiceGUI.exe'
    registry_check.no_links(exe)
    receipt = json.loads((copy / 'result.json').read_text(encoding='utf-8'))
    rows = json.loads((copy / 'sha-manifest.json').read_text(encoding='utf-8'))
    rows_sha = sha256(json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    executable_rows = [item for item in rows if item['path'] == exe.name]
    executable_sha = registry_check.file_hash(exe)
    require(receipt.get('identical') is True
            and receipt['copied_files'] == receipt['source_files'] == len(rows)
            and rows_sha == receipt['full_tree_sha256'], 'copied_manifest_unverified')
    require(len(executable_rows) == 1
            and executable_rows[0]['sha256'] == executable_sha == receipt['exe_sha256'],
            'copied_executable_sha_mismatch')
    manifest = json.loads((frozen / 'source-manifest.json').read_text(encoding='utf-8'))
    # Fixture construction and registered-command builder must match the
    # packaged candidate rather than silently exercise a later worktree.
    for item in manifest['files']:
        current = ROOT / item['path']
        require(current.is_file() and registry_check.file_hash(current) == item['sha256'],
                'worktree_product_differs_from_frozen_candidate')
    return exe, dict(verified, exe_sha256=executable_sha, copy_manifest_sha256=rows_sha,
                    copy_result_sha256=registry_check.file_hash(copy / 'result.json'),
                    build_source_manifest_sha256=registry_check.file_hash(frozen / 'source-manifest.json'))


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    candidate, output = args.candidate.absolute(), args.output.absolute()
    require(os.name == 'nt', 'windows_only')
    require('..' not in output.parts and output.is_relative_to(ROOT / 'artifacts')
            and output != ROOT / 'artifacts'
            and output.parent.is_dir() and not output.exists(), 'fresh_owned_output_required')
    registry_check.no_links(output)
    exe, binding = candidate_binding(candidate)
    if not args.execute:
        return {'status': 'prepared', 'binding': binding, 'registry_changed': False}
    output.mkdir(exist_ok=False)
    report = {'status': 'failed', 'scope': 'nonce-class-key-registry-dispatch-to-packaged-exe',
              'default_pixivvault_association_accessed': False,
              'default_scheme_dispatch_tested': False, 'registration_button_ui_tested': False,
              'binding': binding, 'product_native': None, 'errors': [], 'cleanup': {}}
    guard = owner = held = ole = None
    com_initialized = False
    original_launcher = uri.shell_launch
    nonce = uuid.uuid4().hex
    scheme = 'pv-acceptance-native-' + nonce
    require(scheme != 'pixivvault', 'real_scheme_forbidden')
    seeds = targets = {}
    imports_before = {p: registry_check.file_hash(ROOT / p) for p in (
        'tools/check_packaged_protocol_class.py', 'tools/check_uri_native.py',
        'tools/check_protocol_registration_windows.py', 'tools/check_native_tray_window.py')}
    try:
        import winreg
        ole = ctypes.WinDLL('ole32', use_last_error=True)
        ole.CoInitializeEx.argtypes = [ctypes.c_void_p, W.DWORD]
        ole.CoInitializeEx.restype = ctypes.c_long
        ole.CoUninitialize.argtypes = []
        hr = ole.CoInitializeEx(None, 0x02 | 0x04)
        require(hr in (0, 1, -2147417850), 'class_key_com_init_failed')
        com_initialized = hr in (0, 1)
        guard = registry_check.ScopedRegistry(winreg, (scheme,))
        require(all(not registry_check.exists(guard, p) for p in (*guard.roots, *guard.receipts)),
                'nonce_class_already_exists')
        profile, save = output / 'uri-native-profile', output / 'uri-native-save'
        save.mkdir()
        store = NativeProfileStore(profile)
        try:
            snap = store.update(save_parent=save, expected_revision=0)
            require(snap.cookie_file is None and snap.library_readonly_db is None
                    and snap.library_image_root is None, 'non_synthetic_profile')
        finally:
            store.close()
        app_port, debug_port = fresh_ports()
        startup = ['--native', '--host', '127.0.0.1', '--port', str(app_port),
                   '--profile-root', str(profile)]
        command = build_protocol_command(str(exe), frozen=True, startup_args=tuple(startup))
        owner = WindowsProtocolRegistration(command, registry=guard)
        owner.KEY = guard.roots[0]
        values = {
            '': {'PixivVaultAcceptanceNonce': (guard.REG_SZ, nonce),
                 '': (guard.REG_MULTI_SZ, ['synthetic earlier owner', nonce]),
                 'URL Protocol': (guard.REG_BINARY, b'owned-synthetic-original')},
            'shell': {}, r'shell\open': {},
            r'shell\open\command': {'': (guard.REG_EXPAND_SZ, 'synthetic-never-launched'),
                                    'PreservedFlag': (guard.REG_DWORD, 7)},
        }
        seeds = {rel: {name: registry_check.packed(value, kind) for name, (kind, value) in v.items()}
                 for rel, v in values.items()}
        targets = {('', ''): registry_check.packed('URL:PixivVault Protocol', guard.REG_SZ),
                   ('', 'URL Protocol'): registry_check.packed('', guard.REG_SZ),
                   (r'shell\open\command', ''): registry_check.packed(command, guard.REG_SZ)}
        for rel, data in values.items():
            registry_check.put(guard, owner.KEY + (chr(92) + rel if rel else ''), data)
        original = registry_check.snapshot(guard, owner.KEY)
        require(original == seeds, 'original_nonce_registry_mismatch')
        new_json(output / 'registry-before.json', original)
        owner.register()
        require(owner.is_registered(), 'product_registration_not_confirmed')
        held = guard.OpenKey(guard.HKEY_CURRENT_USER, owner.KEY, 0, guard.KEY_READ)
        launches = []
        def class_launcher(actual_exe, actual_args, *, cwd):
            require(actual_exe == exe and list(actual_args) == startup + ['--', START_URI]
                    and cwd == exe.parent, 'unexpected_native_dispatch_request')
            require(owner.is_registered(), 'registered_command_changed_before_dispatch')
            require(registry_check.file_hash(exe) == binding['exe_sha256'], 'exe_changed_before_dispatch')
            info = _class_launch_info(exe, int(held.handle))
            shell = ctypes.WinDLL('shell32', use_last_error=True)
            shell.ShellExecuteExW.argtypes = [ctypes.POINTER(uri._StartupInfo)]
            shell.ShellExecuteExW.restype = W.BOOL
            require(bool(shell.ShellExecuteExW(ctypes.byref(info))), 'class_key_shell_dispatch_failed')
            require(bool(info.hProcess), 'class_key_process_handle_missing')
            kernel = registry_check.kernel_api()
            try:
                require(registry_check.image_path(kernel, info.hProcess) ==
                        registry_check.normalized_image(exe), 'dispatched_image_not_candidate')
            except BaseException:
                kernel.CloseHandle(info.hProcess)
                # No interaction or termination of an unverified process.
                raise
            launches.append({'via_registry_class_handle': True, 'uri': START_URI,
                             'executable_identity_verified': True})
            return info.hProcess
        # Change only the CHECKER's OS-dispatch adapter. The EXE, its parser,
        # profile loader, single-instance protocol and UI are untouched.
        uri.shell_launch = class_launcher
        report['product_native'] = uri.run([
            '--exe', str(exe), '--profile-root', str(profile),
            '--app-port', str(app_port), '--debug-port', str(debug_port),
            '--output', str(output / 'native'), '--execute'])
        report['class_key_launches'] = launches
        require(report['product_native']['status'] == 'passed' and len(launches) == 2,
                'packaged_cold_warm_not_accepted')
        held.handle.Close(); held.closed = True; held = None
        reopened = WindowsProtocolRegistration(command, registry=guard)
        reopened.KEY = owner.KEY
        require(reopened.is_registered(), 'reopened_registration_not_owned')
        reopened.unregister()
        restored = registry_check.snapshot(guard, owner.KEY)
        require(restored == original and not reopened.is_registered(), 'typed_registry_restore_mismatch')
        require(not registry_check.exists(guard, guard.receipts[0]), 'restore_receipt_remained')
        new_json(output / 'registry-restored.json', restored)
        report['registration_roundtrip_passed'] = True
    except Exception as error:
        report['errors'].append(registry_check.safe_error('class_key_acceptance_failed', error))
    finally:
        uri.shell_launch = original_launcher
        if held is not None:
            held.handle.Close(); held.closed = True
        if owner is not None and guard is not None:
            try:
                registry_check.cleanup_registry(guard, owner, seeds, targets, nonce, report['cleanup'])
            except Exception as error:
                report['errors'].append(registry_check.safe_error('class_key_cleanup_failed', error))
        if com_initialized:
            ole.CoUninitialize()
        report['checker_helpers_unchanged'] = all(registry_check.file_hash(ROOT / p) == h
                                                 for p, h in imports_before.items())
        report['checker_helpers_sha256'] = imports_before
        report['exe_unchanged'] = registry_check.file_hash(exe) == binding['exe_sha256']
        report['passed'] = (not report['errors'] and report.get('registration_roundtrip_passed') is True
                            and report['cleanup'].get('owned_registry_absent') is True
                            and not report['cleanup'].get('cleanup_errors')
                            and report['cleanup'].get('scope_violations') == 0
                            and report['checker_helpers_unchanged'] and report['exe_unchanged'])
        report['status'] = 'passed' if report['passed'] else 'failed'
        new_json(output / 'result.json', report)
    return report


if __name__ == '__main__':
    try:
        result = run()
        print(json.dumps({key: result.get(key) for key in (
            'status', 'scope', 'passed', 'registration_roundtrip_passed', 'errors', 'cleanup')}))
        raise SystemExit(0 if result['status'] in ('prepared', 'passed') else 1)
    except registry_check.CheckFailed as error:
        print(json.dumps({'status': 'failed', 'reason': str(error)}))
        raise SystemExit(2)
