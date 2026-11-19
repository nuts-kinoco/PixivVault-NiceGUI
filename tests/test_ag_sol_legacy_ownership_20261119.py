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


def test_swap_after_ownership_check_preserves_foreign_file(tmp_path, monkeypatch):
    import os
    from pixivvault_nicegui import legacy_library_copies as module
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
    victim = owner.snapshot()
    owner.close()
    foreign = tmp_path / 'foreign.sqlite'
    with sqlite3.connect(foreign) as connection:
        connection.execute('CREATE TABLE external_data (value TEXT)')
        connection.execute("INSERT INTO external_data VALUES ('retain')")
    connection.close()
    foreign_bytes = foreign.read_bytes()
    owner = LegacyLibraryCopies(root, source=source, max_generations=1, max_bytes=1048576)
    original = module._require_owned_copy
    calls = []
    def swap_after_check(path):
        result = original(path)
        if path == victim:
            calls.append(1)
            if len(calls) == 2:
                os.replace(foreign, victim)
        return result
    monkeypatch.setattr(module, '_require_owned_copy', swap_after_check)
    try:
        with pytest.raises(LegacyCopyError):
            owner.snapshot()
        assert len(calls) == 2
        assert victim.read_bytes() == foreign_bytes
    finally:
        owner.close()


def test_interrupted_publication_keeps_marked_previous_copy(tmp_path, monkeypatch):
    from pixivvault_nicegui import legacy_library_copies as module
    old = tmp_path / 'old'
    old.mkdir()
    source = old / 'source.sqlite'
    with sqlite3.connect(source) as connection:
        connection.execute('CREATE TABLE works (work_id TEXT, title TEXT, page_count INTEGER, user_id TEXT, tags TEXT, is_deleted INTEGER)')
        connection.execute('CREATE TABLE following_users (user_id TEXT, name TEXT)')
        connection.execute("INSERT INTO works (work_id) VALUES ('original')")
    connection.close()
    root = tmp_path / 'copies'
    root.mkdir()
    owner = LegacyLibraryCopies(root, source=source, max_generations=1, max_bytes=1048576)
    first = owner.snapshot()
    before = first.read_bytes()
    owner.close()
    owner = LegacyLibraryCopies(root, source=source, max_generations=1, max_bytes=1048576)
    with monkeypatch.context() as patch:
        def interrupt(*_args):
            raise SystemExit('synthetic interruption')
        patch.setattr(module, '_publish_copy', interrupt)
        try:
            with pytest.raises(SystemExit):
                owner.snapshot()
        finally:
            owner.close()
    preserved = list(root.glob('*.sqlite'))
    assert len(preserved) == 1
    assert preserved[0].read_bytes() == before
    owner = LegacyLibraryCopies(root, source=source, max_generations=1, max_bytes=1048576)
    try:
        assert owner.snapshot().is_file()
    finally:
        owner.close()


@pytest.mark.parametrize('long_path', [False, True])
def test_rotation_handles_supplementary_unicode_in_destination(tmp_path, long_path):
    from pixivvault_nicegui.fs_paths import native_io_path
    old = tmp_path / 'old'
    old.mkdir()
    source = old / 'source.sqlite'
    with sqlite3.connect(source) as connection:
        connection.execute('CREATE TABLE works (work_id TEXT, title TEXT, page_count INTEGER, user_id TEXT, tags TEXT, is_deleted INTEGER)')
        connection.execute('CREATE TABLE following_users (user_id TEXT, name TEXT)')
    connection.close()
    before = source.read_bytes()
    root = tmp_path / '保存先🌱📚' / 'copies'
    if long_path:
        root = tmp_path / ('a' * 90) / ('b' * 90) / (('c' * 60) + '🌱📚') / 'copies'
    native_io_path(root).mkdir(parents=True)
    for _ in range(4):
        owner = LegacyLibraryCopies(root, source=source, max_generations=3, max_bytes=1048576)
        try:
            assert owner.snapshot().is_file()
        finally:
            owner.close()
    assert len(list(native_io_path(root).glob('*.sqlite'))) == 3
    assert source.read_bytes() == before


def test_unmarked_ready_database_is_preserved_and_reported_on_reopen(tmp_path):
    old = tmp_path / 'old'
    old.mkdir()
    source = old / 'source.sqlite'
    with sqlite3.connect(source) as connection:
        connection.execute('CREATE TABLE works (work_id TEXT, title TEXT, page_count INTEGER, user_id TEXT, tags TEXT, is_deleted INTEGER)')
        connection.execute('CREATE TABLE following_users (user_id TEXT, name TEXT)')
    connection.close()
    root = tmp_path / 'copies'
    root.mkdir()
    owner = LegacyLibraryCopies(root, source=source)
    owner.close()
    foreign = root / ('.ready-' + 'd' * 32 + '.sqlite')
    with sqlite3.connect(foreign) as connection:
        connection.execute('CREATE TABLE foreign_data (value TEXT)')
        connection.execute("INSERT INTO foreign_data VALUES ('retain')")
    connection.close()
    before = foreign.read_bytes()
    source_before = source.read_bytes()
    with pytest.raises(LegacyCopyError, match='stage.*preserved'):
        LegacyLibraryCopies(root, source=source)
    assert foreign.read_bytes() == before
    assert source.read_bytes() == source_before


def test_foreign_ready_appearing_mid_snapshot_is_not_opened_or_removed(tmp_path, monkeypatch):
    old = tmp_path / 'old'
    old.mkdir()
    source = old / 'source.sqlite'
    with sqlite3.connect(source) as connection:
        connection.execute('CREATE TABLE works (work_id TEXT, title TEXT, page_count INTEGER, user_id TEXT, tags TEXT, is_deleted INTEGER)')
        connection.execute('CREATE TABLE following_users (user_id TEXT, name TEXT)')
    connection.close()
    root = tmp_path / 'copies'
    root.mkdir()
    owner = LegacyLibraryCopies(root, source=source, max_bytes=1048576)
    original = owner._read_source
    foreign = []
    def appear(raw, expected):
        result = original(raw, expected)
        path = root / ('.ready-' + raw.name[len('.raw-'):] + '.sqlite')
        with sqlite3.connect(path) as connection:
            connection.execute('CREATE TABLE foreign_data (value TEXT)')
        connection.close()
        foreign.append((path, path.read_bytes()))
        return result
    monkeypatch.setattr(owner, '_read_source', appear)
    try:
        with pytest.raises(LegacyCopyError):
            owner.snapshot()
        assert len(foreign) == 1
        assert foreign[0][0].read_bytes() == foreign[0][1]
    finally:
        owner.close()


def test_foreign_raw_swap_before_publish_is_preserved(tmp_path, monkeypatch):
    import os
    from pixivvault_nicegui import legacy_library_copies as module
    old = tmp_path / 'old'
    old.mkdir()
    source = old / 'source.sqlite'
    with sqlite3.connect(source) as connection:
        connection.execute('CREATE TABLE works (work_id TEXT, title TEXT, page_count INTEGER, user_id TEXT, tags TEXT, is_deleted INTEGER)')
        connection.execute('CREATE TABLE following_users (user_id TEXT, name TEXT)')
    connection.close()
    root = tmp_path / 'copies'
    root.mkdir()
    foreign = tmp_path / 'foreign.sqlite'
    with sqlite3.connect(foreign) as connection:
        connection.execute('CREATE TABLE foreign_data (value TEXT)')
    connection.close()
    before = foreign.read_bytes()
    owner = LegacyLibraryCopies(root, source=source, max_bytes=1048576)
    original = module._publish_copy
    placed = []
    def swap(raw, target, expected):
        os.replace(foreign, raw)
        placed.append(raw)
        return original(raw, target, expected)
    monkeypatch.setattr(module, '_publish_copy', swap)
    try:
        with pytest.raises(LegacyCopyError):
            owner.snapshot()
        assert len(placed) == 1 and placed[0].read_bytes() == before
        assert not tuple(root.glob('*.sqlite'))
    finally:
        owner.close()


def test_stage_cleanup_failure_is_reported_even_when_snapshot_fails(tmp_path, monkeypatch):
    from pixivvault_nicegui import legacy_library_copies as module
    old = tmp_path / 'old'
    old.mkdir()
    source = old / 'source.sqlite'
    with sqlite3.connect(source) as connection:
        connection.execute('CREATE TABLE works (work_id TEXT, title TEXT, page_count INTEGER, user_id TEXT, tags TEXT, is_deleted INTEGER)')
        connection.execute('CREATE TABLE following_users (user_id TEXT, name TEXT)')
    connection.close()
    root = tmp_path / 'copies'
    root.mkdir()
    owner = LegacyLibraryCopies(root, source=source, max_bytes=1048576)
    original_read = owner._read_source
    original_mutate = module._mutate_file
    def fail_read(raw, expected):
        original_read(raw, expected)
        raise LegacyCopyError('synthetic read failure')
    def fail_cleanup(path, expected, destination=None, *, require_marker=True):
        if not require_marker and path.name.startswith('.raw-'):
            raise OSError('synthetic cleanup failure')
        return original_mutate(path, expected, destination, require_marker=require_marker)
    monkeypatch.setattr(owner, '_read_source', fail_read)
    monkeypatch.setattr(module, '_mutate_file', fail_cleanup)
    try:
        with pytest.raises(LegacyCopyError, match='stage.*preserved'):
            owner.snapshot()
        assert len(list(root.glob('.raw-*'))) == 1
    finally:
        owner.close()
