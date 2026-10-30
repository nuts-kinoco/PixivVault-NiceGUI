"""Synthetic Win32 facade only: a failed identity check cannot leak hProcess."""
from types import SimpleNamespace
from pathlib import Path
import os
import pytest

from tools import check_uri_native as checker


def fake_kernel():
    closed=[]
    kernel=SimpleNamespace(
        GetProcessId=lambda handle:101,
        WaitForSingleObject=lambda *args:0x102,
        GetExitCodeProcess=lambda *args:True,
        CloseHandle=lambda handle:closed.append(handle) or True)
    return kernel,closed


def test_constructor_releases_returned_shell_handle_on_identity_failure(monkeypatch):
    kernel,closed=fake_kernel()
    monkeypatch.setattr(checker.ctypes,'WinDLL',lambda *a,**kw:kernel,raising=False)
    def unavailable(pid):
        raise RuntimeError('synthetic identity unavailable')
    monkeypatch.setattr(checker.native,'process_identity',unavailable)
    monkeypatch.setattr(checker.native,'process_creation_time',unavailable)
    with pytest.raises(RuntimeError,match='synthetic identity unavailable'):
        checker.HandleProcess(123456)
    assert closed==[123456]


def test_retained_handle_birth_is_used_and_cleanup_is_idempotent(monkeypatch):
    kernel,closed=fake_kernel()
    monkeypatch.setattr(checker.ctypes,'WinDLL',lambda *a,**kw:kernel,raising=False)
    monkeypatch.setattr(checker.native,'process_identity',
                        lambda pid:pytest.fail('cannot reopen a short-lived root by PID'))
    monkeypatch.setattr(checker.native,'process_creation_time',lambda handle:202)
    audit={}
    process=checker.HandleProcess(123456,audit=audit)
    assert audit['identity_verified'] and (audit['pid'],audit['birth'])==(101,202)
    process.close();process.close()
    assert closed==[123456] and audit['handle_released']


def test_shell_owned_keeps_unverified_launch_audit_when_constructor_fails(monkeypatch):
    kernel,closed=fake_kernel()
    monkeypatch.setattr(checker.ctypes,'WinDLL',lambda *a,**kw:kernel,raising=False)
    monkeypatch.setattr(checker,'shell_launch',lambda *a,**kw:123456)
    monkeypatch.setattr(checker.native,'process_creation_time',
                        lambda handle:(_ for _ in ()).throw(RuntimeError('synthetic identity fail')))
    monkeypatch.setenv('PIXIVVAULT_PROFILE_ROOT','synthetic-original-env')
    audit=[]
    with pytest.raises(RuntimeError,match='synthetic identity fail'):
        checker.shell_owned(Path('C:/owned/test.exe'),(),{},launch_audit=audit)
    assert len(audit)==1 and audit[0]['handle_returned']
    assert not audit[0]['identity_verified'] and audit[0]['handle_released']
    assert audit[0]['identity_error_type']=='RuntimeError'
    assert closed==[123456]
    assert os.environ['PIXIVVAULT_PROFILE_ROOT']=='synthetic-original-env'
