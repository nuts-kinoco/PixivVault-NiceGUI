"""N06/N07 Windows acceptance on nonce-owned test schemes only.

This checks the current Python adapter and product command builder, not a
packaged product window. It never opens the real pixivvault registry key.
Fault injection remains separate CPU evidence. Run from the candidate tree:
  python -B tools/check_protocol_registration_windows.py --output artifacts/<fresh>/result.json
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes as wt
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'src'))

from pixivvault_nicegui.desktop_integration import WindowsProtocolRegistration
from pixivvault_nicegui.uri_launch import START_URI, build_protocol_command, parse_start_uri

if __package__:
    from .check_native_window import (
        process_creation_time, process_identity, process_pairs, adopt_descendants,
        living_pids,
    )
else:
    from check_native_window import (
        process_creation_time, process_identity, process_pairs, adopt_descendants,
        living_pids,
    )


class CheckFailed(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise CheckFailed(code)


def no_links(path):
    for node in (path, *path.parents):
        if node.exists() or node.is_symlink():
            st = node.lstat()
            require(not node.is_symlink() and not getattr(st, 'st_file_attributes', 0) & 0x400,
                    'artifact_link_rejected')


def write_json(path, value):
    no_links(path)
    require(not path.exists(), 'artifact_already_exists')
    temporary = path.with_name(path.name + '.pending')
    with temporary.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=True, sort_keys=True, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    # Windows rename refuses an existing destination; no previous evidence is replaced.
    os.rename(temporary, path)


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def safe_error(code, error):
    result = {'code': code, 'type': type(error).__name__}
    if isinstance(error, CheckFailed):
        result['reason'] = str(error)
    if type(getattr(error, 'winerror', None)) is int:
        result['winerror'] = error.winerror
    return result


def phase(name):
    print(json.dumps({'phase': name}, ensure_ascii=True), flush=True)


class ScopedRegistry:
    """Reject access outside generated schemes, receipts and read-only parents."""
    class Key:
        def __init__(self, owner, handle, path):
            self.owner, self.handle, self.path = owner, handle, path
            self.closed = False
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            self.handle.Close()
            self.closed = True

    def __init__(self, native, schemes):
        self.native = native
        for name in ('HKEY_CURRENT_USER', 'REG_SZ', 'REG_EXPAND_SZ', 'REG_BINARY',
                     'REG_DWORD', 'REG_MULTI_SZ', 'KEY_READ', 'KEY_WRITE'):
            setattr(self, name, getattr(native, name))
        self.schemes = tuple(schemes)
        self.roots = tuple(r'Software\Classes' + '\\' + item for item in schemes)
        self.receipts = tuple(WindowsProtocolRegistration.RECEIPT_ROOT + '\\' + item for item in schemes)
        self.parents = (r'Software\PixivVault-NiceGUI', WindowsProtocolRegistration.RECEIPT_ROOT)
        self.parent_existed = {}
        self.created = set()
        self.scope_violations = 0
        self.mutations = 0
        for path in self.parents:
            try:
                with native.OpenKey(native.HKEY_CURRENT_USER, path):
                    self.parent_existed[path] = True
            except FileNotFoundError:
                self.parent_existed[path] = False

    def _path(self, parent, path, *, write=False, delete=False):
        if isinstance(parent, self.Key):
            require(parent.owner is self and not parent.closed, 'foreign_registry_handle')
            path = parent.path + ('\\' + path if path else '')
        else:
            require(parent == self.HKEY_CURRENT_USER, 'registry_hive_rejected')
        require(type(path) is str and '\0' not in path, 'registry_path_rejected')
        allowed = any(path.casefold() == root.casefold()
                      or path.casefold().startswith(root.casefold() + '\\')
                      for root in (*self.roots, *self.receipts))
        if path in self.parents:
            allowed = not write or (delete and not self.parent_existed[path])
        if not allowed:
            self.scope_violations += 1
            raise CheckFailed('registry_scope_rejected')
        return path

    def _key(self, key, *, write=False):
        require(isinstance(key, self.Key) and key.owner is self and not key.closed,
                'foreign_registry_handle')
        self._path(self.HKEY_CURRENT_USER, key.path, write=write)
        return key.handle

    def OpenKey(self, parent, path, reserved=0, access=None):
        path = self._path(parent, path)
        handle = self.native.OpenKey(self.HKEY_CURRENT_USER, path, reserved,
                                     self.KEY_READ if access is None else access)
        return self.Key(self, handle, path)

    def CreateKeyEx(self, parent, path, reserved=0, access=None):
        path = self._path(parent, path, write=True)
        existed = exists(self, path)
        handle = self.native.CreateKeyEx(self.HKEY_CURRENT_USER, path, reserved,
            self.KEY_READ | self.KEY_WRITE if access is None else access)
        if not existed:
            self.created.add(path)
        self.mutations += 1
        return self.Key(self, handle, path)

    def CreateKey(self, parent, path):
        return self.CreateKeyEx(parent, path)

    def QueryValueEx(self, key, name):
        return self.native.QueryValueEx(self._key(key), name)

    def QueryInfoKey(self, key):
        return self.native.QueryInfoKey(self._key(key))

    def EnumKey(self, key, index):
        require(key.path not in self.parents, 'parent_enumeration_rejected')
        return self.native.EnumKey(self._key(key), index)

    def EnumValue(self, key, index):
        require(key.path not in self.parents, 'parent_enumeration_rejected')
        return self.native.EnumValue(self._key(key), index)

    def SetValueEx(self, key, name, reserved, kind, value):
        self.native.SetValueEx(self._key(key, write=True), name, reserved, kind, value)
        self.mutations += 1

    def DeleteValue(self, key, name):
        self.native.DeleteValue(self._key(key, write=True), name)
        self.mutations += 1

    def DeleteKey(self, parent, path):
        path = self._path(parent, path, write=True, delete=True)
        self.native.DeleteKey(self.HKEY_CURRENT_USER, path)
        self.mutations += 1


def exists(registry, path):
    try:
        with registry.OpenKey(registry.HKEY_CURRENT_USER, path):
            return True
    except FileNotFoundError:
        return False


def packed(value, kind):
    if isinstance(value, bytes):
        return {'type': kind, 'bytes_hex': value.hex()}
    require(value is None or type(value) in (str, int, list), 'unexpected_registry_value_type')
    if type(value) is list:
        require(all(type(item) is str for item in value), 'unexpected_registry_list')
    return {'type': kind, 'value': value}


def snapshot(registry, root):
    if not exists(registry, root):
        return None
    result = {}
    pending = [('', root)]
    while pending:
        relative, path = pending.pop()
        require(len(result) < 32 and relative.count('\\') < 8, 'unexpected_registry_extent')
        with registry.OpenKey(registry.HKEY_CURRENT_USER, path) as key:
            count_keys, count_values, _stamp = registry.QueryInfoKey(key)
            require(count_keys <= 16 and count_values <= 16, 'unexpected_registry_extent')
            values = {}
            for index in range(count_values):
                name, value, kind = registry.EnumValue(key, index)
                values[name] = packed(value, kind)
            result[relative] = values
            for index in range(count_keys):
                name = registry.EnumKey(key, index)
                require(name and '\\' not in name and '\0' not in name, 'unexpected_registry_child')
                pending.append((relative + ('\\' if relative else '') + name, path + '\\' + name))
    return result


def put(registry, path, values):
    with registry.CreateKeyEx(registry.HKEY_CURRENT_USER, path, 0,
                              registry.KEY_READ | registry.KEY_WRITE) as key:
        for name, (kind, value) in values.items():
            registry.SetValueEx(key, name, 0, kind, value)


def delete_snapshot(registry, root, captured):
    """Only exact observed nodes, after the caller proved nonce ownership."""
    for relative in sorted(captured, key=lambda value: (value.count('\\'), len(value)), reverse=True):
        path = root + ('\\' + relative if relative else '')
        current = snapshot(registry, path)
        require(current == {'': captured[relative]}, 'cleanup_registry_node_changed')
        registry.DeleteKey(registry.HKEY_CURRENT_USER, path)


PROBE = r'''import sys
sys.dont_write_bytecode = True
import ctypes
from ctypes import wintypes as wt
import hashlib
import json
import os
from pathlib import Path
import time

TOKEN = __TOKEN__
SCHEME = __SCHEME__
root = Path(__file__).resolve().parent
expected = ['--probe-token', TOKEN, '--', SCHEME + '://start']
invocation = root / 'probe-invocation.json'
with invocation.open('x', encoding='utf-8') as stream:
    json.dump({'nonce': TOKEN, 'argv': sys.argv[1:]}, stream, ensure_ascii=True)
if sys.argv[1:] not in (expected, expected[:-1] + [expected[-1] + '/']):
    raise SystemExit(81)

def publish(name, value):
    path = root / name
    temporary = root / (name + '.pending')
    with temporary.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=True, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.rename(temporary, path)

kernel = ctypes.WinDLL('kernel32', use_last_error=True)
kernel.GetCurrentProcess.restype = wt.HANDLE
kernel.GetProcessTimes.argtypes = [wt.HANDLE] + [ctypes.POINTER(wt.FILETIME)] * 4
kernel.GetProcessTimes.restype = wt.BOOL
times = [wt.FILETIME() for _ in range(4)]
if not kernel.GetProcessTimes(kernel.GetCurrentProcess(), *(ctypes.byref(v) for v in times)):
    raise SystemExit(82)
birth = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
ready = dict(nonce=TOKEN, pid=os.getpid(), birth=birth, argv=sys.argv[1:],
             executable=str(Path(sys.executable).resolve()),
             script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
publish('probe-ready.json', ready)
deadline = time.monotonic() + 30.0
while time.monotonic() < deadline:
    release = root / 'probe-release.json'
    if release.exists():
        if release.stat().st_size > 4096:
            raise SystemExit(83)
        value = json.loads(release.read_text(encoding='utf-8'))
        if value != {'nonce': TOKEN}:
            raise SystemExit(84)
        publish('probe-result.json', dict(ready, completed=True))
        raise SystemExit(0)
    time.sleep(.05)
raise SystemExit(85)
'''


def kernel_api():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetProcessId.argtypes = [wt.HANDLE]
    kernel.GetProcessId.restype = wt.DWORD
    kernel.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    kernel.OpenProcess.restype = wt.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wt.BOOL
    kernel.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
    kernel.WaitForSingleObject.restype = wt.DWORD
    kernel.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
    kernel.GetExitCodeProcess.restype = wt.BOOL
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    kernel.CloseHandle.restype = wt.BOOL
    kernel.GetSystemTimeAsFileTime.argtypes = [ctypes.POINTER(wt.FILETIME)]
    return kernel


def normalized_image(path):
    value = str(Path(path).resolve())
    if value.startswith('\\\\?\\'):
        value = value[4:]
    return value.casefold()


def image_path(kernel, handle):
    buffer = ctypes.create_unicode_buffer(32768)
    size = wt.DWORD(len(buffer))
    require(bool(kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size))),
            'probe_image_unverified')
    return normalized_image(buffer.value)


def read_probe_json(path):
    no_links(path)
    require(path.is_file() and path.stat().st_size <= 8192, 'probe_record_rejected')
    return json.loads(path.read_text(encoding='utf-8'))


def probe_uri(uri, nonce, directory, script, report):
    class ShellExecuteInfo(ctypes.Structure):
        _fields_ = [('cbSize', wt.DWORD), ('fMask', wt.ULONG), ('hwnd', wt.HWND),
            ('lpVerb', wt.LPCWSTR), ('lpFile', wt.LPCWSTR), ('lpParameters', wt.LPCWSTR),
            ('lpDirectory', wt.LPCWSTR), ('nShow', ctypes.c_int), ('hInstApp', wt.HINSTANCE),
            ('lpIDList', ctypes.c_void_p), ('lpClass', wt.LPCWSTR), ('hkeyClass', wt.HKEY),
            ('dwHotKey', wt.DWORD), ('hIconOrMonitor', wt.HANDLE), ('hProcess', wt.HANDLE)]
    kernel = kernel_api()
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    shell.ShellExecuteExW.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
    shell.ShellExecuteExW.restype = wt.BOOL
    ole = ctypes.WinDLL('ole32', use_last_error=True)
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wt.DWORD]
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CoUninitialize.argtypes = []
    hr = ole.CoInitializeEx(None, 0x2 | 0x4)
    require(hr in (0, 1, -2147417850), 'probe_com_initialization_failed')  # RPC_E_CHANGED_MODE
    initialized = hr in (0, 1)
    handle = None
    identities = {}
    nonce_verified = False
    expected_images = {normalized_image(sys.executable),
                       normalized_image(getattr(sys, '_base_executable', sys.executable))}
    expected_script_hash = file_hash(script)
    ready_path = directory / 'probe-ready.json'
    release_path = directory / 'probe-release.json'
    ready = None
    try:
        stamp = wt.FILETIME()
        kernel.GetSystemTimeAsFileTime(ctypes.byref(stamp))
        launched_after = (stamp.dwHighDateTime << 32) | stamp.dwLowDateTime
        info = ShellExecuteInfo()
        info.cbSize = ctypes.sizeof(info)
        info.fMask = 0x40 | 0x100 | 0x400 | 0x8000  # keep handle, synchronous dispatch, no UI/console
        info.lpVerb, info.lpFile, info.lpDirectory, info.nShow = 'open', uri, str(directory), 0
        dispatched = bool(shell.ShellExecuteExW(ctypes.byref(info)))
        handle = info.hProcess
        require(dispatched, 'probe_shell_execute_failed')
        require(bool(handle), 'probe_shell_process_handle_missing')
        pid = int(kernel.GetProcessId(handle))
        birth = process_creation_time(handle)
        require(pid > 0 and pid != os.getpid() and birth >= launched_after, 'probe_root_identity_rejected')
        require(image_path(kernel, handle) in expected_images, 'probe_root_image_rejected')
        identities[pid] = birth
        report['shell_process'] = {'pid': pid, 'birth': birth}
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            root_alive = kernel.WaitForSingleObject(handle, 0) == 0x102
            adopt_descendants(pid, identities, process_pairs(), root_alive)
            if ready_path.exists():
                ready = read_probe_json(ready_path)
                candidate = ready.get('pid')
                if candidate in identities:
                    break
            if not root_alive:
                break
            time.sleep(.05)
        require(type(ready) is dict and ready.get('nonce') == nonce, 'probe_nonce_unverified')
        probe_pid = ready.get('pid')
        require(type(probe_pid) is int and probe_pid in identities
                and identities[probe_pid] == ready.get('birth')
                and process_identity(probe_pid) == identities[probe_pid], 'probe_child_identity_unverified')
        received = ready.get('argv')
        expected = ['--probe-token', nonce, '--', uri]
        require(received in (expected, expected[:-1] + [uri + '/']), 'probe_uri_arguments_changed')
        # ShellExecute normalizes a host-only custom URI with a trailing slash.
        # Check that exact received suffix with the product's wake-only parser.
        suffix = received[-1][len(uri.partition(':')[0]):]
        require(parse_start_uri('pixivvault' + suffix) == START_URI,
                'normalized_uri_rejected_by_product_parser')
        report['product_uri_parser_accepted'] = True
        require(ready.get('script_sha256') == expected_script_hash
                and normalized_image(ready.get('executable', '')) in expected_images,
                'probe_script_identity_unverified')
        for owned_pid, owned_birth in identities.items():
            current = process_identity(owned_pid)
            if current is None:
                continue
            require(current == owned_birth, 'probe_process_identity_changed')
            opened = kernel.OpenProcess(0x1000 | 0x00100000, False, owned_pid)
            require(bool(opened), 'probe_owned_handle_missing')
            try:
                require(process_creation_time(opened) == owned_birth
                        and image_path(kernel, opened) in expected_images, 'probe_owned_image_rejected')
            finally:
                kernel.CloseHandle(opened)
        nonce_verified = True
        report['nonce_identity_verified'] = True
        report['argv'] = ready['argv']
        report['owned_processes'] = [{'pid': item, 'birth': creation} for item, creation in identities.items()]
        write_json(release_path, {'nonce': nonce})
        require(kernel.WaitForSingleObject(handle, 15000) == 0, 'probe_exit_timeout')
        code = wt.DWORD()
        require(bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))), 'probe_exit_code_missing')
        report['exit_code'] = int(code.value)
        require(code.value == 0, 'probe_exit_nonzero')
        completed = read_probe_json(directory / 'probe-result.json')
        require(completed == dict(ready, completed=True), 'probe_completion_record_changed')
        deadline = time.monotonic() + 5.0
        while living_pids(identities) and time.monotonic() < deadline:
            time.sleep(.05)
        require(not living_pids(identities), 'probe_children_remaining')
        require(file_hash(script) == expected_script_hash, 'probe_script_changed')
        report['passed'] = True
    finally:
        try:
            if nonce_verified:
                if not release_path.exists():
                    write_json(release_path, {'nonce': nonce})
                # The nonce-owned probe exits itself after its bounded deadline.
                # Never force a process to exit, including a failing acceptance.
                deadline = time.monotonic() + 35.0
                while living_pids(identities) and time.monotonic() < deadline:
                    time.sleep(.05)
                report['remaining_processes'] = sorted(living_pids(identities))
                if report['remaining_processes']:
                    report['passed'] = False
            elif handle:
                # Do not kill an unverified process. The dedicated probe has a
                # bounded self-exit even when no nonce handshake is received.
                kernel.WaitForSingleObject(handle, 35000)
                report['unverified_handle_exited'] = kernel.WaitForSingleObject(handle, 0) == 0
                if report['unverified_handle_exited']:
                    code = wt.DWORD()
                    if kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                        report['unverified_exit_code'] = int(code.value)
        finally:
            if handle:
                kernel.CloseHandle(handle)
            if initialized:
                ole.CoUninitialize()


def permitted_seed_cleanup(registry, key, current, seed_values, target_values, nonce):
    if current is None:
        return
    marker = current.get('', {}).get('PixivVaultAcceptanceNonce')
    if marker != packed(nonce, registry.REG_SZ):
        require(current == {'': {}} and key in registry.created, 'cleanup_nonce_unverified')
    for relative, values in current.items():
        require(relative in seed_values, 'cleanup_foreign_registry_child')
        for name, actual in values.items():
            options = [seed_values[relative].get(name)]
            if (relative, name) in target_values:
                options.append(target_values[(relative, name)])
            require(actual in options, 'cleanup_foreign_registry_value')


def cleanup_registry(registry, owner, seed_values, target_values, nonce, report):
    errors = []
    key = owner.KEY
    receipt = registry.receipts[0]
    try:
        if exists(registry, receipt):
            owner.unregister()
    except Exception as error:
        errors.append(safe_error('adapter_cleanup_incomplete', error))
    try:
        current = snapshot(registry, key)
        permitted_seed_cleanup(registry, key, current, seed_values, target_values, nonce)
        if current is not None:
            delete_snapshot(registry, key, current)
    except Exception as error:
        errors.append(safe_error('owned_scheme_cleanup_failed', error))
    try:
        current = snapshot(registry, receipt)
        if current is not None:
            require(set(current) == {''}, 'cleanup_foreign_receipt_child')
            fields = current['']
            if fields:
                require(set(fields) == {'State'} and fields['State']['type'] == registry.REG_SZ,
                        'cleanup_foreign_receipt_value')
                raw = fields['State'].get('value')
                envelope = json.loads(raw)
                payload = envelope['payload']
                encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True,
                                     separators=(',', ':'), allow_nan=False)
                require(envelope['sha256'] == hashlib.sha256(encoded.encode('ascii')).hexdigest()
                        and payload['key'] == key.casefold()
                        and payload['owner'] == owner.command
                        and payload['format'] == 'pixivvault-nicegui-uri-restore-v1',
                        'cleanup_receipt_owner_unverified')
            else:
                require(receipt in registry.created, 'cleanup_empty_receipt_unowned')
            delete_snapshot(registry, receipt, current)
        for parent in reversed(registry.parents):
            if not registry.parent_existed[parent] and exists(registry, parent):
                with registry.OpenKey(registry.HKEY_CURRENT_USER, parent) as handle:
                    children, values, _stamp = registry.QueryInfoKey(handle)
                if children == values == 0:
                    registry.DeleteKey(registry.HKEY_CURRENT_USER, parent)
    except Exception as error:
        errors.append(safe_error('owned_receipt_cleanup_failed', error))
    report['cleanup_errors'] = errors
    report['owned_registry_absent'] = all(not exists(registry, path)
        for path in (*registry.roots, *registry.receipts))
    report['scope_violations'] = registry.scope_violations
    return not errors and report['owned_registry_absent'] and not registry.scope_violations


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    if os.name != 'nt':
        parser.error('Windows is required; no registry operation was performed')
    import winreg
    output = args.output.absolute()
    no_links(output)
    require(output.is_relative_to((PROJECT / 'artifacts').absolute()), 'output_must_be_in_candidate_artifacts')
    require(not output.parent.exists(), 'fresh_output_directory_required')
    require(output.parent.parent.is_dir(), 'output_parent_namespace_required')
    output.parent.mkdir()
    folder = output.parent
    temporary = folder / 'temp'
    temporary.mkdir()
    probe_folder = folder / 'probe with spaces'
    probe_folder.mkdir()
    nonce = uuid.uuid4().hex
    scheme = 'pv-acceptance-318-' + nonce
    empty_scheme = scheme + '-empty'
    require(scheme != 'pixivvault' and len(empty_scheme) <= 63, 'test_scheme_rejected')
    guard = ScopedRegistry(winreg, (scheme, empty_scheme))
    source_paths = [PROJECT / 'src/pixivvault_nicegui' / name
                    for name in ('desktop_integration.py', 'uri_launch.py', 'fs_paths.py')]
    source_paths.append(Path(__file__).resolve())
    before_sources = {str(path.relative_to(PROJECT)): file_hash(path) for path in source_paths}
    result = dict(status='failed', scope='Windows registry and dedicated Python URI probe',
        packaged_product_window=False, nonce=nonce, scheme=scheme, empty_scheme=empty_scheme,
        fault_injection='separate CPU evidence; not performed here',
        source_sha256=before_sources, probe={'passed': False, 'forced_cleanup': []},
        checks={}, errors=[])
    owner = None
    seed_values = {}
    target_values = {}
    environment_before = {name: os.environ.get(name) for name in ('TEMP', 'TMP')}
    try:
        os.environ['TEMP'] = os.environ['TMP'] = str(temporary)
        require(all(not exists(guard, path) for path in (*guard.roots, *guard.receipts)),
                'generated_registry_name_already_exists')
        script = probe_folder / 'uri probe.py'
        script.write_text(PROBE.replace('__TOKEN__', repr(nonce)).replace('__SCHEME__', repr(scheme)),
                          encoding='utf-8', newline='\n')
        previous_script = probe_folder / 'previous synthetic handler.py'
        previous_script.write_text('raise SystemExit(86)\n', encoding='utf-8')
        command = build_protocol_command(str(Path(sys.executable).resolve()), script=str(script),
                                         startup_args=('--probe-token', nonce))
        previous_command = build_protocol_command(str(Path(sys.executable).resolve()),
                                                  script=str(previous_script))
        owner = WindowsProtocolRegistration(command, registry=guard)
        owner.KEY = guard.roots[0]
        key = owner.KEY
        seeds = {
            '': {'PixivVaultAcceptanceNonce': (guard.REG_SZ, nonce),
                 '': (guard.REG_MULTI_SZ, ['synthetic prior owner', nonce]),
                 'URL Protocol': (guard.REG_BINARY, ('original-' + nonce).encode('ascii')),
                 'PreservedSetting': (guard.REG_DWORD, 318)},
            'shell': {}, r'shell\open': {},
            r'shell\open\command': {'': (guard.REG_EXPAND_SZ, previous_command),
                                    'PreservedFlag': (guard.REG_DWORD, 7)},
            'DefaultIcon': {'': (guard.REG_SZ, str(probe_folder / 'unused synthetic icon.ico'))},
            r'Preserved\Nested': {'List': (guard.REG_MULTI_SZ, ['first', nonce])},
            'Preserved': {},
        }
        seed_values = {relative: {name: packed(value, kind) for name, (kind, value) in values.items()}
                       for relative, values in seeds.items()}
        target_values = {
            ('', ''): packed('URL:PixivVault Protocol', guard.REG_SZ),
            ('', 'URL Protocol'): packed('', guard.REG_SZ),
            (r'shell\open\command', ''): packed(command, guard.REG_SZ),
        }
        phase('seed_owned_registry')
        for relative, values in seeds.items():
            put(guard, key + ('\\' + relative if relative else ''), values)
        original = snapshot(guard, key)
        require(original == seed_values, 'seed_snapshot_changed')
        write_json(folder / 'registry-before.json', original)
        require(not owner.is_registered(), 'unowned_seed_claimed')
        phase('register_and_launch_owned_probe')
        owner.register()
        require(owner.is_registered(), 'registration_not_confirmed')
        probe_uri(scheme + '://start', nonce, probe_folder, script, result['probe'])
        require(result['probe']['passed'] and not result['probe']['forced_cleanup'],
                'probe_not_accepted')
        phase('reopen_adapter_and_restore')
        reopened = WindowsProtocolRegistration(command, registry=guard)
        reopened.KEY = key
        require(reopened.is_registered(), 'reopened_owner_not_confirmed')
        reopened.unregister()
        restored = snapshot(guard, key)
        require(restored == original and not reopened.is_registered(), 'original_registry_not_restored')
        write_json(folder / 'registry-restored.json', restored)
        require(not exists(guard, guard.receipts[0]), 'restore_receipt_remaining')
        result['checks']['restored_values_types_subkeys'] = True
        empty_owner = WindowsProtocolRegistration(command, registry=guard)
        empty_owner.KEY = guard.roots[1]
        before_mutations = guard.mutations
        empty_owner.unregister()
        require(not empty_owner.is_registered() and guard.mutations == before_mutations
                and not exists(guard, guard.roots[1]) and not exists(guard, guard.receipts[1]),
                'absent_unregister_had_side_effects')
        result['checks']['absent_unregister_no_write'] = True
        result['checks']['shell_uri_args_and_exit0'] = True
    except Exception as error:
        result['errors'].append(safe_error('acceptance_failed', error))
    finally:
        phase('cleanup_owned_test_registry')
        try:
            if owner is not None:
                result['checks']['owned_cleanup'] = cleanup_registry(
                    guard, owner, seed_values, target_values, nonce, result)
            else:
                result['checks']['owned_cleanup'] = all(not exists(guard, path)
                    for path in (*guard.roots, *guard.receipts))
        except Exception as error:
            result['checks']['owned_cleanup'] = False
            result['errors'].append(safe_error('cleanup_unverified', error))
        after_sources = {str(path.relative_to(PROJECT)): file_hash(path) for path in source_paths}
        result['source_changed'] = before_sources != after_sources
        for name, value in environment_before.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        expected = ('restored_values_types_subkeys', 'absent_unregister_no_write',
                    'shell_uri_args_and_exit0', 'owned_cleanup')
        if (not result['errors'] and not result['source_changed']
                and all(result['checks'].get(key) is True for key in expected)
                and result['probe'].get('passed') is True
                and not result['probe'].get('forced_cleanup')
                and not result['probe'].get('remaining_processes')):
            result['status'] = 'passed'
        write_json(output, result)
        print(json.dumps({'status': result['status'], 'output': str(output),
                          'checks': result['checks']}, ensure_ascii=True), flush=True)
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
