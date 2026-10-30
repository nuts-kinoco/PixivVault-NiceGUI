"""Packaged --noconsole must never leak traceback on expected owner refusal."""
import sys

import pytest

from tools import native_entry
from pixivvault_nicegui.native_instance import NativeInstanceError
from pixivvault_nicegui.native_profile import NativeProfileError


@pytest.mark.parametrize('expected', (NativeInstanceError, NativeProfileError,
                                      ValueError, OSError))
def test_expected_native_rejection_has_one_safe_fixed_failure_path(monkeypatch, expected):
    from pixivvault_nicegui import bootstrap
    calls=[]
    monkeypatch.setattr(sys, 'argv', ['PixivVault-NiceGUI.exe','--native'])
    monkeypatch.setattr(native_entry.multiprocessing, 'freeze_support', lambda:None)
    monkeypatch.setattr(bootstrap, 'main', lambda argv: (_ for _ in ()).throw(expected('sensitive info path/secret')))
    monkeypatch.setattr(native_entry, '_report_startup_rejected', lambda:calls.append('safe fixed UI') or 2)
    assert native_entry.main()==2
    assert calls==['safe fixed UI']


def test_unexpected_programming_bug_still_propagates_for_diagnostics(monkeypatch):
    from pixivvault_nicegui import bootstrap
    monkeypatch.setattr(sys, 'argv', ['PixivVault-NiceGUI.exe','--native'])
    monkeypatch.setattr(native_entry.multiprocessing, 'freeze_support', lambda:None)
    def bug(argv):
        raise AssertionError('synthetic programming bug')
    monkeypatch.setattr(bootstrap, 'main', bug)
    with pytest.raises(AssertionError, match='synthetic programming bug'):
        native_entry.main()


def test_safe_error_path_always_returns_nonzero_without_os_popup(monkeypatch):
    monkeypatch.setattr(native_entry.sys,'platform','linux')
    assert native_entry._report_startup_rejected()==2


def test_packaged_runtime_error_has_distinct_fixed_non_startup_message(monkeypatch):
    from pixivvault_nicegui import bootstrap
    from pixivvault_nicegui.native_instance import NativeAppRuntimeError
    calls=[]
    monkeypatch.setattr(sys,'argv',['PixivVault-NiceGUI.exe','--native'])
    monkeypatch.setattr(native_entry.multiprocessing,'freeze_support',lambda:None)
    monkeypatch.setattr(bootstrap,'main',lambda argv: (_ for _ in ()).throw(
        NativeAppRuntimeError('synthetic sensitive runtime detail')))
    monkeypatch.setattr(native_entry,'_report_startup_rejected',
                        lambda:calls.append('wrong-startup-message') or 2)
    monkeypatch.setattr(native_entry,'_report_runtime_failed',
                        lambda:calls.append('fixed-runtime-message') or 3,raising=False)
    assert native_entry.main()==3
    assert calls==['fixed-runtime-message']
