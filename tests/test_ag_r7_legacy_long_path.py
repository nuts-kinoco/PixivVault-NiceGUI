from contextlib import closing
import sqlite3
from types import SimpleNamespace

import pytest


def make_db(path, *, foreign=False):
    with closing(sqlite3.connect(path)) as con:
        if foreign:
            con.execute('CREATE TABLE foreign_data (id)')
            con.execute('INSERT INTO foreign_data VALUES (1)')
        else:
            con.execute('CREATE TABLE works (work_id TEXT, title TEXT, page_count INTEGER, user_id TEXT, tags TEXT, is_deleted INTEGER)')
            con.execute('CREATE TABLE following_users (user_id TEXT, name TEXT)')
        con.commit()




def test_independent_long_root_get_services(tmp_path):
    from pixivvault_nicegui.services import get_services, _APP_KEY
    (tmp_path / 'legacy').mkdir()
    source = tmp_path / 'legacy' / 'source.sqlite'
    root = tmp_path / ('l' * 110)
    images = tmp_path / 'images'
    root.mkdir()
    images.mkdir()
    make_db(source)
    original = source.read_bytes()
    assert len(str(root)) >= 180
    app = SimpleNamespace(state=SimpleNamespace())
    try:
        outcome = get_services(app, library_readonly_db=source, library_image_root=images, legacy_copy_root=root)
        assert get_services(app, library_readonly_db=source, library_image_root=images, legacy_copy_root=root) is outcome
        from pixivvault_nicegui.fs_paths import native_io_path
        assert get_services(app, library_readonly_db=source, library_image_root=images, legacy_copy_root=native_io_path(root)) is outcome
        with pytest.raises(RuntimeError, match='different legacy snapshot root'):
            get_services(app, library_readonly_db=source, library_image_root=images, legacy_copy_root=root.parent / 'other')
    finally:
        service = getattr(app.state, _APP_KEY, None)
        if service is not None:
            service.close()
        print(f'long_root_units={len(str(root))} source_unchanged={source.read_bytes() == original}')
        assert source.read_bytes() == original
