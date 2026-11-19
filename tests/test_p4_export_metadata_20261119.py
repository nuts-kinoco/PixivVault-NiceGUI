import json
import sqlite3
import zipfile
from pathlib import Path

import pytest
from nicegui import Client, ui

from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.preferences_view import PreferencesView


@pytest.mark.asyncio
async def test_settings_metadata_export_skips_image_tree_and_publishes_exactly_two_entries(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    db = source / 'old.db'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE works(work_id TEXT, title TEXT)')
        conn.execute('INSERT INTO works VALUES (?, ?)', ('1', 'Generated title'))
    before = db.read_bytes()
    images = source / 'images'
    images.mkdir()
    (images / 'sample.png').write_bytes(b'generated image')
    services = ApplicationServices(1, preferences_root=tmp_path / 'preferences')
    services.library_readonly_db = db
    services.library_image_root = images
    client = Client(ui.page('/_p4_metadata_export'))
    try:
        with client:
            view = PreferencesView(services.preferences, export_legacy=services.export_legacy_metadata,
                                   export_metadata=services.export_legacy_metadata_only)
            view.open()
            await view.run_metadata_export()
            assert view.export_status.text.startswith('[完了]')
            assert '旧DB・メタデータのみ' in view.export_status.text
            assert view.export_button.enabled and view.export_metadata_button.enabled
            first, = (Path(services.preferences_root) / 'exports').glob('*.zip')
            with zipfile.ZipFile(first) as archive:
                assert archive.namelist() == ['pixiv_vault.db', 'metadata.json']
                assert archive.testzip() is None
                assert json.loads(archive.read('metadata.json')) == [{'work_id': '1', 'title': 'Generated title'}]
                copied_db = archive.read('pixiv_vault.db')
            check = sqlite3.connect(':memory:')
            try:
                check.deserialize(copied_db)
                assert check.execute('PRAGMA integrity_check').fetchone() == ('ok',)
                assert check.execute('SELECT * FROM works').fetchall() == [('1', 'Generated title')]
            finally:
                check.close()
            await view.run_legacy_export()
            archive_paths = set((Path(services.preferences_root) / 'exports').glob('*.zip'))
            full, = archive_paths - {first}
            with zipfile.ZipFile(full) as archive:
                assert archive.read('images/sample.png') == b'generated image'
            assert db.read_bytes() == before
    finally:
        client.delete()
        services.close()
