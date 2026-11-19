import sqlite3

import pytest

from pixivvault_nicegui.legacy_library_copies import LegacyLibraryCopies, LegacyCopyError


def test_foreign_generation_shaped_database_is_never_replaced(tmp_path):
    old = tmp_path / 'old'
    old.mkdir()
    source = old / 'source.sqlite'
    with sqlite3.connect(source) as connection:
        connection.execute('CREATE TABLE works (work_id TEXT, title TEXT, page_count INTEGER, user_id TEXT, tags TEXT, is_deleted INTEGER)')
        connection.execute('CREATE TABLE following_users (user_id TEXT, name TEXT)')
    root = tmp_path / 'copies'
    root.mkdir()
    owner = LegacyLibraryCopies(root, source=source, max_generations=1, max_bytes=1048576)
    foreign = root / ('a' * 32 + '.sqlite')
    try:
        with sqlite3.connect(foreign) as connection:
            connection.execute('CREATE TABLE foreign_data (value TEXT)')
            connection.execute("INSERT INTO foreign_data VALUES ('retain')")
        connection.close()
        before = foreign.read_bytes()
        with pytest.raises(LegacyCopyError):
            owner.snapshot()
        assert foreign.read_bytes() == before
    finally:
        owner.close()


def test_unreadable_generation_is_preserved(tmp_path):
    old = tmp_path / 'old'
    old.mkdir()
    source = old / 'source.sqlite'
    with sqlite3.connect(source) as connection:
        connection.execute('CREATE TABLE works (work_id TEXT, title TEXT, page_count INTEGER, user_id TEXT, tags TEXT, is_deleted INTEGER)')
        connection.execute('CREATE TABLE following_users (user_id TEXT, name TEXT)')
    connection.close()
    root = tmp_path / 'copies'
    root.mkdir()
    owner = LegacyLibraryCopies(root, source=source, max_generations=1, max_bytes=1048576)
    foreign = root / ('b' * 32 + '.sqlite')
    foreign.write_bytes(b'not sqlite')
    try:
        with pytest.raises(LegacyCopyError):
            owner.snapshot()
        assert foreign.read_bytes() == b'not sqlite'
    finally:
        owner.close()


def test_marked_generation_rotates_even_when_user_version_differs(tmp_path):
    old = tmp_path / 'old'
    old.mkdir()
    source = old / 'source.sqlite'
    with sqlite3.connect(source) as connection:
        connection.execute('CREATE TABLE works (work_id TEXT, title TEXT, page_count INTEGER, user_id TEXT, tags TEXT, is_deleted INTEGER)')
        connection.execute('CREATE TABLE following_users (user_id TEXT, name TEXT)')
    connection.close()
    root = tmp_path / 'copies'
    root.mkdir()
    owner = LegacyLibraryCopies(root, source=source, max_generations=1, max_bytes=1048576)
    first = owner.snapshot()
    owner.close()
    with sqlite3.connect(first) as connection:
        connection.execute('PRAGMA user_version=7')
    connection.close()
    owner = LegacyLibraryCopies(root, source=source, max_generations=1, max_bytes=1048576)
    try:
        assert owner.snapshot() == first
        with sqlite3.connect(first) as connection:
            assert connection.execute('PRAGMA application_id').fetchone() == (0x5056434C,)
            assert connection.execute('PRAGMA user_version').fetchone() == (1,)
        connection.close()
    finally:
        owner.close()
