import pytest
from nicegui import Client, ui
from pixivvault_nicegui.preferences import PreferencesStore
from pixivvault_nicegui.ui.preferences_view import PreferencesView


@pytest.mark.asyncio
async def test_close_saves_pending_settings_and_notifies(tmp_path):
    store = PreferencesStore(tmp_path / 'prefs.sqlite3')
    changed = []
    client = Client(ui.page('/_ae_close_save'))
    try:
        with client:
            view = PreferencesView(store, enabled_keys={'meta_backfill_enabled'}, on_change=changed.append)
            view.open()
            view.controls['meta_backfill_enabled'].set_value('0')
            view.close_dialog()
            assert store.snapshot().values['meta_backfill_enabled'] == '0'
            assert changed and not view.opened
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_close_keeps_dialog_open_on_revision_conflict(tmp_path):
    store = PreferencesStore(tmp_path / 'prefs.sqlite3')
    client = Client(ui.page('/_ae_close_conflict'))
    try:
        with client:
            view = PreferencesView(store, enabled_keys={'meta_backfill_enabled'})
            view.open()
            view.controls['meta_backfill_enabled'].set_value('0')
            store.update({'embed_auto_on_download': '0'}, expected_revision=view.snapshot.revision)
            view.close_dialog()
            assert view.opened
            assert view.controls['meta_backfill_enabled'].value == '0'
            assert store.snapshot().values['meta_backfill_enabled'] == '1'
    finally:
        client.delete()
