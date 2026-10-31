"""Source-copy protection before any genuine original Flet GUI launch."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tools import check_stage5_original_failed17_native as flet


def test_real_original_gui_33_source_files_pinned_and_generated17_same_fixture():
    hashes = flet.read_pinned_source()
    assert len(hashes) == 33
    assert hashes["gui.py"] == flet.EXACT_GUI_SHA
    rows = flet.generated_failure_rows()
    assert len(rows) == 17
    assert len(set(row["work_id"] for row in rows)) == 17
    assert rows[0]["work_id"] == "failure-0001"
    assert "\n" in rows[0]["error_reason"]
    assert all("error_reason" in row and "retry_count" in row for row in rows)


@pytest.mark.parametrize("mode,flags", [
    ("w", 0), ("a", 0), ("x", 0), ("r+", 0),
    ("r", os.O_CREAT), ("r", os.O_RDWR), ("r", os.O_TRUNC),
])
def test_original_source_copy_write_is_blocked_without_editing_original(mode, flags):
    source = flet.SOURCE / "gui.py"
    with pytest.raises(PermissionError):
        flet.source_copy_write_denial("open", (str(source), mode, flags))


def test_source_copy_mutations_denied_but_reads_and_owned_outputs_allowed(tmp_path):
    source = flet.SOURCE / "gui.py"
    flet.source_copy_write_denial("open", (str(source), "r", 0))
    flet.source_copy_write_denial("open", (str(tmp_path / "generated.json"), "w", os.O_CREAT))
    for event in ("os.remove", "os.mkdir", "os.rename", "os.utime"):
        with pytest.raises(PermissionError):
            flet.source_copy_write_denial(event, (source, tmp_path / "unused"))
    for event in ("os.link", "os.symlink"):
        with pytest.raises(PermissionError):
            flet.source_copy_write_denial(event, (source, tmp_path / "link"))
        with pytest.raises(PermissionError):
            flet.source_copy_write_denial(event, (tmp_path / "other", source))


@pytest.mark.skipif(os.name != "nt", reason="Win32 FILE_SHARE_READ is required")
def test_win32_pinned_read_handle_denies_write_through_outside_hardlink(tmp_path):
    """P1 red: audit(path) alone cannot protect a generated alias outside root."""
    source = tmp_path / "generated-source.py"
    alias = tmp_path / "outside-alias.py"
    source.write_bytes(b"generated-safe-source")
    os.link(source, alias)
    # Positive vulnerability control: a path-only source-root audit would not
    # see this outside-name mutation of the exact same NTFS file inode.
    alias.write_bytes(b"vulnerable")
    assert source.read_bytes() == b"vulnerable"
    source.write_bytes(b"generated-safe-source")
    with flet.pinned_win32_sources([source]):
        with pytest.raises(OSError):
            alias.write_bytes(b"unsafe-write")
        with pytest.raises(OSError):
            source.write_bytes(b"unsafe-direct-write")
        with pytest.raises(OSError):
            os.unlink(source)
        assert source.read_bytes() == b"generated-safe-source"
    assert source.read_bytes() == b"generated-safe-source"


@pytest.mark.skipif(os.name != "nt", reason="Win32 file link contract")
def test_production_source_pin_rejects_preexisting_hardlink(tmp_path):
    source = tmp_path / "copy-source.py"
    alias = tmp_path / "outside-hardlink.py"
    source.write_bytes(b"generated-data")
    os.link(source, alias)
    with pytest.raises(RuntimeError, match="unexpected hardlink"):
        with flet.pinned_win32_sources([source], require_single_link=True):
            pytest.fail("a linked production source must never be leased")
    assert source.read_bytes() == alias.read_bytes() == b"generated-data"
