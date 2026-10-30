"""Preference UI reflects the fixed startup policy without changing live owners."""
import pytest
from nicegui import Client, ui

from pixivvault_nicegui.preferences import PreferencesStore
from pixivvault_nicegui.ui.preferences_view import PreferencesView


@pytest.mark.asyncio
@pytest.mark.parametrize('mode,saved,effective,notice', [
    ('readonly', '1', '0', 'GET専用'),
    ('enabled', '0', '1', '起動オプション'),
])
async def test_cli_fixed_policy_cannot_edit_or_misreport_saved_native_choice(
        tmp_path, mode, saved, effective, notice):
    store = PreferencesStore(tmp_path / 'settings.sqlite3')
    store.set_setting('pixiv_actions_enabled', saved)
    client = Client(ui.page('/_pixiv_action_policy_' + mode))
    try:
        with client:
            view = PreferencesView(store,
                enabled_keys={'pixiv_actions_enabled', 'novel_save_format'},
                pixiv_actions_mode=mode)
            view.open()
            control = view.controls['pixiv_actions_enabled']
            assert not control.enabled
            assert control.value == effective
            labels = [element for element in client.elements.values()
                      if element.props.get('data-testid') == 'preference-pixiv-actions-mode']
            assert len(labels) == 1 and notice in labels[0].text
            # A stale/forged UI value must not write a disabled capability.
            control.set_value('1' if saved == '0' else '0')
            view.controls['novel_save_format'].set_value('txt')
            assert view.save()
            assert store.get_setting('pixiv_actions_enabled') == saved
            assert store.get_setting('novel_save_format') == 'txt'
            assert '次回起動' not in view.status.text
            view.close()
    finally:
        client.delete()
        store.close()


@pytest.mark.asyncio
async def test_native_preference_saves_with_restart_notice_and_no_implicit_action(tmp_path):
    store = PreferencesStore(tmp_path / 'settings.sqlite3')
    client = Client(ui.page('/_pixiv_action_policy_preferences'))
    applied = []
    try:
        with client:
            view = PreferencesView(store,
                enabled_keys={'pixiv_actions_enabled'},
                pixiv_actions_mode='preferences', on_change=applied.append)
            view.open()
            control = view.controls['pixiv_actions_enabled']
            assert control.enabled and control.value == '0'
            labels = [element for element in client.elements.values()
                      if element.props.get('data-testid') == 'preference-pixiv-actions-mode']
            assert len(labels) == 1 and '次回' in labels[0].text
            control.set_value('1')
            assert view.save()
            assert store.get_setting('pixiv_actions_enabled') == '1'
            assert '次回起動' in view.status.text
            assert len(applied) == 1
            assert applied[0].values['pixiv_actions_enabled'] == '1'
            view.close()
    finally:
        client.delete()
        store.close()
