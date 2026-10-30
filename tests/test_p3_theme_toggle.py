"""S21 theme toggle uses per-client UI state and preserves stored preferences."""
import pytest
from nicegui import Client, ui
from pixivvault_nicegui.preferences import PreferencesStore
from pixivvault_nicegui.ui.shell import DemoShell
from pixivvault_nicegui.ui.theme import CSS

@pytest.mark.asyncio
async def test_theme_toggle_and_client_isolation_preserve_card_setting(tmp_path):
    store=PreferencesStore(tmp_path/'settings.sqlite3')
    store.update({'grid_card_size_mode':'small'},expected_revision=0)
    first=Client(ui.page('/_s21_first'));second=Client(ui.page('/_s21_second'))
    try:
        def construct(client):
            shell=DemoShell.__new__(DemoShell);shell.closed=False
            with client:
                shell.dark_mode=ui.dark_mode(True)
                shell.theme_button=ui.button(icon='light_mode')
                with shell.theme_button:shell.theme_tooltip=ui.tooltip('ライトモードへ切り替え')
            return shell
        a=construct(first);b=construct(second)
        before=store.snapshot()
        a.toggle_theme()
        assert a.dark_mode.value is False and b.dark_mode.value is True
        assert a.theme_button.icon=='dark_mode'
        assert a.theme_button.props['aria-label']=='ダークモードへ切り替え'
        assert a.theme_tooltip.text=='ダークモードへ切り替え'
        a.toggle_theme()
        assert a.dark_mode.value is True and a.theme_button.icon=='light_mode'
        assert a.theme_tooltip.text=='ライトモードへ切り替え'
        assert store.snapshot().revision==before.revision
        assert store.snapshot().values['grid_card_size_mode']=='small'
    finally:first.delete();second.delete();store.close()
    reopened=PreferencesStore(tmp_path/'settings.sqlite3')
    try:assert reopened.snapshot().values['grid_card_size_mode']=='small'
    finally:reopened.close()

def test_light_and_dark_tokens_and_existing_grid_caps():
    assert 'body.body--light' in CSS
    assert '--pv-bg:#0b0b10' in CSS and '--pv-bg:#fafafa' in CSS
    assert '--pv-text:#e8e8ef' in CSS and '--pv-text:#1d2632' in CSS
    assert 'var(--pv-max-cols, 6)' in CSS
