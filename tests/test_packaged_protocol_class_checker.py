"""Class-key acceptance harness contracts; no registry, process or GUI writes."""
import ctypes
from pathlib import Path
import pytest

from tools.check_packaged_protocol_class import _class_launch_info, candidate_binding
from tools.check_protocol_registration_windows import CheckFailed
from tools.check_uri_native import _StartupInfo
from pixivvault_nicegui.uri_launch import START_URI


def test_class_dispatch_uses_exact_uri_and_read_only_handle_not_exe_directly():
    exe=Path('C:/owned/PixivVault-NiceGUI.exe')
    info=_class_launch_info(exe, 12345)
    assert info.cbSize==ctypes.sizeof(_StartupInfo)
    assert info.fMask & 0x03 == 0x03
    assert info.hkeyClass==12345
    assert info.lpFile==START_URI
    assert info.lpVerb=='open'
    assert info.lpParameters is None
    assert info.lpClass is None
    assert info.fMask & 0x40 and info.fMask & 0x100 and info.fMask & 0x400


@pytest.mark.parametrize('handle',(0,-1,None,True,'12345'))
def test_no_implicit_default_association_fallback_when_class_handle_absent(handle):
    with pytest.raises(CheckFailed,match='read_only_class_handle_missing'):
        _class_launch_info(Path('C:/owned/PixivVault-NiceGUI.exe'),handle)


def test_unowned_candidate_rejected_before_reading_files(tmp_path, monkeypatch):
    from tools import check_packaged_protocol_class as checker
    # The project's --basetemp itself lies under artifacts; give this unit
    # a separate logical owned root so "unowned" is truly outside it.
    monkeypatch.setattr(checker, 'ROOT', tmp_path / 'logical-owned-root')
    with pytest.raises(CheckFailed,match='candidate_outside_owned_artifacts'):
        candidate_binding(tmp_path/'unowned')
