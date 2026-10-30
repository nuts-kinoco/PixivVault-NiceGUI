"""N10 generated GUI status: no real extension/credentials/registry calls."""
from nicegui import Client, ui
import pytest

from pixivvault_nicegui.preferences import PreferencesStore
from pixivvault_nicegui.ui.preferences_view import PreferencesView


@pytest.mark.asyncio
@pytest.mark.parametrize('status,expected', [
    ('disabled', '拡張連携: 無効（明示した起動設定が必要）'),
    ('running', '拡張連携: 受付中'),
    ('unavailable', '拡張連携: ポートを使用できません。既存アプリの終了後に再起動してください。'),
    ('unexpected', '拡張連携: 状態不明'),
])
async def test_extension_status_display_is_connected_to_current_app_state(tmp_path,status,expected):
    store = PreferencesStore(tmp_path / 'owned-preferences.sqlite3')
    client = Client(ui.page('/_n10_generated_status'))
    state = {'status': status}
    try:
        with client:
            view = PreferencesView(store, extension_status=lambda:state['status'])
            view.open()
            matches = [node for node in view.dialog.descendants()
                       if node.props.get('data-testid') == 'extension-status']
            assert len(matches) == 1 and matches[0].text == expected
            assert not view.enabled_keys
            assert store.snapshot().revision == 0
            state['status'] = 'disabled' if status != 'disabled' else 'running'
            view.open()
            matches = [node for node in view.dialog.descendants()
                       if node.props.get('data-testid') == 'extension-status']
            assert len(matches) == 1
            assert matches[0].text == ('拡張連携: 無効（明示した起動設定が必要）'
                                       if status != 'disabled' else '拡張連携: 受付中')
            assert store.snapshot().revision == 0
            view.close()
    finally:
        client.delete()
        store.close()
