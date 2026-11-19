import hashlib
import os

import pytest

from pixivvault_nicegui import legacy_library_copies as copies


def test_legacy_source_handle_is_closed_between_chunks(tmp_path, monkeypatch):
    old = tmp_path / "old"
    old.mkdir()
    source = old / "source.sqlite"
    data = bytes(range(100))
    source.write_bytes(data)
    root = tmp_path / "copies"
    root.mkdir()
    owner = copies.LegacyLibraryCopies(root, source=source, max_bytes=1048576)
    original = copies._open_source
    opened = []
    def tracked_open(path):
        if opened:
            with pytest.raises(OSError):
                os.fstat(opened[-1])
        fd = original(path)
        opened.append(fd)
        return fd
    monkeypatch.setattr(copies, "_open_source", tracked_open)
    monkeypatch.setattr(copies, "_CHUNK", 32)
    raw = root / "synthetic-output"
    try:
        assert owner._read_source(raw, copies._identity(source.stat())) == hashlib.sha256(data).digest()
        assert raw.read_bytes() == data
        assert len(opened) >= 4
        with pytest.raises(OSError):
            os.fstat(opened[-1])
    finally:
        owner.close()
