import os
import sqlite3
import pytest


def test_replacement_between_chunks_is_allowed_and_detected(tmp_path, monkeypatch):
    from pixivvault_nicegui import legacy_library_copies as copies
    old = tmp_path / 'old'
    old.mkdir()
    source = old / 'source.sqlite'
    replacement = old / 'replacement.sqlite'
    for path, value in ((source, 'old'), (replacement, 'new')):
        connection = sqlite3.connect(path)
        connection.execute('CREATE TABLE works (work_id TEXT, title TEXT, page_count INTEGER, user_id TEXT, tags TEXT, is_deleted INTEGER)')
        connection.execute('CREATE TABLE following_users (user_id TEXT, name TEXT)')
        connection.execute('INSERT INTO works (work_id) VALUES (?)', (value,))
        connection.commit()
        connection.close()
    new_bytes = replacement.read_bytes()
    root = tmp_path / 'copies'
    root.mkdir()
    owner = copies.LegacyLibraryCopies(root, source=source, max_bytes=1048576)
    original = copies._open_source
    calls = []
    def racing_open(path):
        calls.append(1)
        if len(calls) == 2:
            os.replace(replacement, source)
        return original(path)
    monkeypatch.setattr(copies, '_open_source', racing_open)
    try:
        with pytest.raises(copies.LegacyCopyError):
            owner.snapshot()
        assert len(calls) >= 2
        assert source.read_bytes() == new_bytes
        assert not tuple(root.glob('*.sqlite'))
    finally:
        owner.close()
