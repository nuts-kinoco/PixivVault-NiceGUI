from contextlib import closing
from types import SimpleNamespace
import sqlite3

import pytest

from pixivvault_nicegui.legacy.database import Database
from pixivvault_nicegui.services import ApplicationServices


@pytest.mark.asyncio
async def test_discovery_reopens_with_new_owned_copy_of_same_legacy_db(tmp_path):
    source = tmp_path / 'original'
    source.mkdir()
    original = source / 'legacy.sqlite3'
    with closing(Database(original, sandbox_root=source)) as legacy:
        legacy.set_setting('discovery_search_mode', 'safe')
        legacy.record_discovery_judgment('123', 'like', tags=['cloud'])
    baseline = original.read_bytes()
    images = tmp_path / 'images'
    copies = tmp_path / 'copies'
    images.mkdir()
    copies.mkdir()

    def open_services(db):
        return ApplicationServices(
            1, library_readonly_db=db, library_image_root=images,
            live_client=SimpleNamespace(close=lambda: None),
            discovery_root=tmp_path / 'discovery', legacy_copy_root=copies,
        )

    with closing(open_services(original)) as first:
        old_copy = first.library_readonly_db
        settings = await first.discovery.settings_snapshot()
        assert settings.values['discovery_search_mode'] == 'safe'

    with closing(open_services(original)) as second:
        assert second.library_readonly_db != old_copy
        assert (await second.discovery.settings_snapshot()).values == settings.values

    changed = source / 'changed.sqlite3'
    changed.write_bytes(baseline)
    with closing(sqlite3.connect(changed)) as connection, connection:
        connection.execute("UPDATE settings SET value='r18' WHERE key='discovery_search_mode'")
    with closing(open_services(changed)) as other:
        with pytest.raises(ValueError, match='already seeded from another source'):
            await other.discovery.settings_snapshot()

    assert original.read_bytes() == baseline
    with closing(sqlite3.connect(tmp_path / 'discovery' / 'learning.sqlite3')) as learned:
        assert learned.execute('SELECT count(*) FROM discovery_swipe_history').fetchone() == (1,)
