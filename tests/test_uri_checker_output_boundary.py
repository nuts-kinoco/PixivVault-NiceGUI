"""Check URI harness output scope without touching any original directory."""
import os
import pytest

from tools import check_uri_native as checker
from pixivvault_nicegui.native_profile import NativeProfileStore

pytestmark=pytest.mark.skipif(os.name!='nt',reason='Windows native checker')


def inputs(tmp_path,monkeypatch):
    root=tmp_path/'logical-worktree'
    artifacts=root/'artifacts';artifacts.mkdir(parents=True)
    outside=root/'outside';outside.mkdir()
    exe=root/'synthetic-never-executed.exe';exe.write_bytes(b'not-executable')
    save=artifacts/'uri-native-save';save.mkdir()
    profile=artifacts/'uri-native-profile'
    store=NativeProfileStore(profile)
    try:store.update(save_parent=save,expected_revision=0)
    finally:store.close()
    monkeypatch.setattr(checker,'ROOT',root)
    return root,exe,profile


def test_parent_reference_is_rejected_before_uri_preparation(tmp_path,monkeypatch):
    root,exe,profile=inputs(tmp_path,monkeypatch)
    out=root/'artifacts'/'..'/'outside'/'result'
    with pytest.raises(checker.AcceptanceError,match='output_parent_reference_rejected'):
        checker.run(['--exe',str(exe),'--profile-root',str(profile),
                     '--output',str(out),'--app-port','59001','--debug-port','59002'])
    assert not (root/'outside'/'result').exists()


def test_ordinary_owned_preflight_remains_read_only(tmp_path,monkeypatch):
    root,exe,profile=inputs(tmp_path,monkeypatch)
    out=root/'artifacts'/'ordinary-run'
    report=checker.run(['--exe',str(exe),'--profile-root',str(profile),
                        '--output',str(out),'--app-port','59001','--debug-port','59002'])
    assert report['status']=='prepared'
    assert not out.exists()
