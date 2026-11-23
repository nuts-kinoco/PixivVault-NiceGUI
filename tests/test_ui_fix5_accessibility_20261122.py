from nicegui import Client, ui

from pixivvault_nicegui.ui.learning_toast import LearningToast
from pixivvault_nicegui.ui.ranking_view import RankingView
from test_ranking_ui import setup
from z_learning_support_20261118 import open_discovery_view


async def test_judgment_shortcuts_and_explore_sliders_have_names(tmp_path, monkeypatch):
    view, _, _, cleanup = await open_discovery_view(tmp_path, monkeypatch, count=3)
    try:
        for choice, label, keys in (
            ('skip', '見送る', 'ArrowLeft A 1'),
            ('hate', '合わない', 'ArrowDown S 2'),
            ('like', 'いいね', 'ArrowRight D 3'),
            ('superLike', 'SuperLike', 'ArrowUp W 4'),
        ):
            button = view.buttons[choice]
            assert button.props.get('aria-label') == label
            assert button.props.get('aria-keyshortcuts') == keys
        assert view.settings_subject.props.get('aria-label') == '知らないジャンルを見る割合'
        assert view.settings_style.props.get('aria-label') == '知らない絵柄を見る割合'
    finally:
        await cleanup()


async def test_calendar_has_named_dialog_and_safe_initial_focus():
    services, _, _ = setup()
    client = Client(ui.page('/_ui_fix5_calendar'))
    with client:
        view = RankingView(services)
        view.set_active(True)
        try:
            view.open_calendar()
            assert view.calendar_dialog.props.get('aria-label') == 'ランキングの日付を選択'
            cancel = next(element for element in view.calendar_dialog.descendants()
                          if element.props.get('data-testid') == 'ranking-date-cancel')
            assert 'data-autofocus' in cancel.props
        finally:
            view.close()
            client.delete()


def test_important_learning_notice_is_announced_and_dismissed_explicitly():
    client = Client(ui.page('/_ui_fix5_toast'))
    try:
        with client:
            toast = LearningToast()
            assert toast.show_gave_up('判定を保存できませんでした', '保存を再試行できます', heavy=True)
            message = next(element for element in toast.band.descendants()
                           if element.props.get('data-testid') == 'learning-toast-message')
            close = next(element for element in toast.band.descendants()
                         if element.props.get('data-testid') == 'learning-toast-close')
            assert message.props.get('role') == 'alert'
            assert close.text == '閉じる'
            assert toast.hide() is False
    finally:
        client.delete()
