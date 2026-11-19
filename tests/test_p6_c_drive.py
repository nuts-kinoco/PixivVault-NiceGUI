import sys
import json
import os
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import p6_c_drive
from p6_c_write_audit import write_destinations


def test_drive_diff_includes_profile_and_does_not_follow_links(tmp_path):
    profile = tmp_path / 'Users' / 'fixture' / 'AppData' / 'Local' / 'Temp'
    profile.mkdir(parents=True)
    (profile / 'old').touch()
    with p6_c_drive.open_snapshot(tmp_path / 'snapshot.db') as db:
        assert p6_c_drive.snapshot(tmp_path / 'Users', db, 'before') == []
        (profile / 'new').touch()
        try:
            (tmp_path / 'Users' / 'loop').symlink_to(tmp_path, target_is_directory=True)
        except OSError:
            pass
        assert p6_c_drive.snapshot(tmp_path / 'Users', db, 'after') == []
        assert [Path(path).name for path in p6_c_drive.new_files(db)] == ['new']


def test_drive_diff_keeps_unpaired_ntfs_filename_code_units(tmp_path):
    root = tmp_path / 'root'
    root.mkdir()
    name = 'old-windows-name-\udc4c'
    with p6_c_drive.open_snapshot(tmp_path / 'snapshot.db') as db:
        assert p6_c_drive.snapshot(root, db, 'before') == []
        (root / name).touch()
        assert p6_c_drive.snapshot(root, db, 'after') == []
        assert [Path(path).name for path in p6_c_drive.new_files(db)] == [name]


def test_drive_exclusion_needs_evidence_and_narrow_directory(tmp_path):
    path = tmp_path / 'rules.json'
    entry = {'prefix': 'C:\\Windows\\Logs\\CBS\\', 'reason': 'Windows servicing', 'evidence': 'fixture observation'}
    path.write_text(json.dumps([entry]), encoding='utf-8')
    assert p6_c_drive.exclusions(path) == [entry]
    for changed in (dict(entry, prefix='C:\\'), dict(entry, evidence='')):
        path.write_text(json.dumps([changed]), encoding='utf-8')
        with pytest.raises(ValueError):
            p6_c_drive.exclusions(path)


def test_python_audit_targets_writes_without_exposing_arguments():
    assert write_destinations('open', ('C:/fixture', 'r', os.O_RDONLY)) == ()
    assert write_destinations('open', ('C:/fixture', 'w', os.O_CREAT)) == ('C:/fixture',)
    assert write_destinations('os.rename', ('source', 'C:/fixture', -1, -1)) == ('C:/fixture',)
    assert write_destinations('sqlite3.connect', ('file:///C:/generated.sqlite?mode=ro',)) == ()
    assert write_destinations('sqlite3.connect', ('file:///C:/generated.sqlite?mode=rwc',)) == ('C:/generated.sqlite',)


def test_isolated_profile_retains_existing_browser_installation_lookup(tmp_path, monkeypatch):
    installed = tmp_path / 'existing' / 'ms-playwright'
    installed.mkdir(parents=True)
    monkeypatch.setenv('LOCALAPPDATA', str(installed.parent))
    monkeypatch.delenv('PLAYWRIGHT_BROWSERS_PATH', raising=False)
    env = p6_c_drive.command_environment(tmp_path / 'run')
    assert env['PLAYWRIGHT_BROWSERS_PATH'] == str(installed)
    assert Path(env['LOCALAPPDATA']) == tmp_path / 'run' / 'test-profile' / 'AppData' / 'Local'
    assert Path(env['TEMP']) == tmp_path / 'run' / 'temp'
