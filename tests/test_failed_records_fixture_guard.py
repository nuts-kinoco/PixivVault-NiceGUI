from pathlib import Path

import pytest

from tools.check_failed_records_browser import build_failure_fixture
from tools.failed_records_fixture_guard import verify_fixture_manifest, MANIFEST


def test_native_failure_fixture_manifest_is_required_and_bound_to_contents(tmp_path):
    db, images = build_failure_fixture(tmp_path / "fixture")
    assert verify_fixture_manifest(db, images)
    with db.open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(ValueError, match="changed"):
        verify_fixture_manifest(db, images)


def test_non_fixture_path_and_missing_marker_are_rejected(tmp_path):
    with pytest.raises(ValueError):
        verify_fixture_manifest(tmp_path / "production.sqlite", tmp_path / "actual-images")
    db, images = build_failure_fixture(tmp_path / "fixture")
    (db.parent / MANIFEST).unlink()
    with pytest.raises(OSError):
        verify_fixture_manifest(db, images)
