import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import p6_c_drive


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
