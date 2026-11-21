"""Visual settings controls keep the existing storage values and UI identifiers."""
import pytest
from nicegui import Client, ui

from pixivvault_nicegui.preferences import PreferencesStore
from pixivvault_nicegui.ui.preferences_view import PreferencesView


@pytest.mark.asyncio
async def test_binary_fields_use_switches_with_string_model_values(tmp_path):
    store = PreferencesStore(tmp_path / 'prefs.sqlite3')
    client = Client(ui.page('/_tone_d_settings'))
    try:
        with client:
            view = PreferencesView(store, enabled_keys={'meta_backfill_enabled'})
            view.open()
            switch = view.controls['meta_backfill_enabled']
            assert isinstance(switch, ui.switch)
            assert switch.props['true-value'] == '1'
            assert switch.props['false-value'] == '0'
            assert switch.props['data-value'] == '1'
            assert not view.controls['pixiv_actions_enabled'].enabled
            switch.set_value('0')
            assert switch.props['data-value'] == '0'
            assert view.save()
            view.open()
            assert view.controls['meta_backfill_enabled'].value == '0'
    finally:
        view.close()
        client.delete()
