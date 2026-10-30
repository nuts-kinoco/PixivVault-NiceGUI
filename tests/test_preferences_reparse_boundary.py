"""Native settings SQLite must reject reparse ancestors before mkdir/connect.

No real user profile, original database or Windows junction is mutated here.
Attribute injection models lstat on an actual Windows junction, unlike stat.
"""
from pathlib import Path
from types import SimpleNamespace
import sqlite3

import pytest

from pixivvault_nicegui.preferences import PreferencesStore, PreferencesError


@pytest.mark.parametrize('kind', ('ancestor', 'database'))
def test_injected_windows_reparse_component_rejected_before_sqlite_or_directory_write(
        tmp_path, monkeypatch, kind):
    settings = tmp_path / 'owned' / 'settings.sqlite3'
    settings.parent.mkdir()
    if kind == 'database':
        settings.write_bytes(b'fixture non-SQLite user file')
    before = {p.relative_to(tmp_path).as_posix():p.read_bytes()
              for p in tmp_path.rglob('*') if p.is_file()}
    target = settings if kind == 'database' else settings.parent
    original_lstat = Path.lstat
    original_stat = Path.stat
    opened = []

    class Marked:
        def __init__(self, value): self.value = value
        def __getattr__(self, name):
            return 0x400 if name == 'st_file_attributes' else getattr(self.value, name)

    def lstat(node, *args, **kwargs):
        value = original_lstat(node, *args, **kwargs)
        return Marked(value) if node == target else value

    def stat(node, *args, **kwargs):
        # stat follows Windows junctions and exposes a regular target. The
        # implementation must not rely on its target-side reparse flag.
        return original_stat(node, *args, **kwargs)

    def blocked(*args, **kwargs):
        opened.append(1)
        raise AssertionError('SQLite was opened before rejecting the source path')

    monkeypatch.setattr(Path, 'lstat', lstat)
    monkeypatch.setattr(Path, 'stat', stat)
    monkeypatch.setattr(sqlite3, 'connect', blocked)
    with pytest.raises(PreferencesError, match='リンク'):
        PreferencesStore(settings)
    assert opened == []
    assert {p.relative_to(tmp_path).as_posix():p.read_bytes()
            for p in tmp_path.rglob('*') if p.is_file()} == before
