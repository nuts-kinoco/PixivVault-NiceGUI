import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from pixivvault_nicegui.native_profile import NativeProfileStore


@pytest.mark.skipif(os.name != 'nt', reason='Windows PowerShell launcher')
@pytest.mark.parametrize('location', ['outside', 'inside', 'drive-root'])
def test_isolated_launcher_checks_preflight_and_preserves_spaced_arguments(tmp_path, location):
    repo = Path(__file__).resolve().parents[1]
    workspace = tmp_path / 'isolated workspace'
    workspace.mkdir()
    (workspace / '.pixivvault-isolated-test-workspace').write_bytes(
        b'PixivVault isolated source test workspace v1\n')
    storage = (tmp_path if location == 'outside' else workspace) / 'save parent'
    storage.mkdir()
    profile = workspace / 'private profile'
    store = NativeProfileStore(profile)
    try:
        store.update(save_parent=storage, expected_revision=0)
    finally:
        store.close()

    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    script = """
function Start-Process {
    $index = [Array]::IndexOf($args, '-ArgumentList')
    Write-Host $args[$index + 1]
    throw 'fixture application start intercepted'
}
try {
    & $env:PV_LAUNCH_TEST_SCRIPT -CodeRoot $env:PV_LAUNCH_TEST_REPO `
        -PythonRoot $env:PV_LAUNCH_TEST_PYTHON -ProfileRoot $env:PV_LAUNCH_TEST_PROFILE `
        -LogRoot (Join-Path $env:PV_LAUNCH_TEST_WORKSPACE 'logs') `
        -TempRoot (Join-Path $env:PV_LAUNCH_TEST_WORKSPACE 'temp') `
        -IsolatedWorkspace $env:PV_LAUNCH_TEST_BOUNDARY -Port ([int]$env:PV_LAUNCH_TEST_PORT)
} catch {
    Write-Output $_.Exception.Message
    exit 4
}
"""
    env = dict(os.environ, PV_LAUNCH_TEST_SCRIPT=str(repo / 'tools' / 'launch_d10.ps1'),
               PV_LAUNCH_TEST_REPO=str(repo), PV_LAUNCH_TEST_PYTHON=str(Path(sys.executable).parents[1]),
               PV_LAUNCH_TEST_PROFILE=str(profile), PV_LAUNCH_TEST_WORKSPACE=str(workspace),
               PV_LAUNCH_TEST_BOUNDARY=str(workspace.anchor if location == 'drive-root' else workspace),
               PV_LAUNCH_TEST_PORT=str(port))
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 4
    if location == 'inside':
        assert 'fixture application start intercepted' in result.stdout
        command_line, intercepted = result.stdout.splitlines()
        assert intercepted == 'fixture application start intercepted'
        from ctypes import POINTER, byref, c_int, c_wchar_p, windll
        argc = c_int()
        parse = windll.shell32.CommandLineToArgvW
        parse.argtypes = [c_wchar_p, POINTER(c_int)]
        parse.restype = POINTER(c_wchar_p)
        parsed = parse('python.exe ' + command_line, byref(argc))
        assert parsed
        try:
            assert parsed[1:argc.value] == [
                '-B', '-u', str(repo / 'tools' / 'launch_test_owner.py'), '--test-workspace',
                str(workspace), '--', '--native', '--host', '127.0.0.1', '--port', str(port),
                '--profile-root', str(profile),
            ]
        finally:
            from ctypes import c_void_p, cast
            free = windll.kernel32.LocalFree
            free.argtypes = [c_void_p]
            free.restype = c_void_p
            free(cast(parsed, c_void_p))
    else:
        message = ('Profile preflight failed' if location == 'outside' else
                   'IsolatedWorkspace must be a directory below the S: drive root.')
        assert message in result.stdout
        assert not (workspace / 'logs').exists()
