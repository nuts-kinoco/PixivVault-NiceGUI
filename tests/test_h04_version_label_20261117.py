"""FL-C24: 設定ダイアログ末尾のバージョン表記（元 gui.py:2420）。"""
from types import SimpleNamespace

from pixivvault_nicegui import __version__
from pixivvault_nicegui.preferences import SPECS
from pixivvault_nicegui.ui.preferences_view import PreferencesView


def test_version_label_sits_above_the_buttons_row():
    store = SimpleNamespace(snapshot=lambda: SimpleNamespace(values={s.key: s.default for s in SPECS}))
    view = PreferencesView(store)
    view.open()
    try:
        labels = [e for e in view.dialog.descendants() if getattr(e, 'text', None) == f'ver {__version__}']
        assert len(labels) == 1
        label = labels[0]
        assert label.props['data-testid'] == 'preferences-version'
        assert label._style.get('font-size') == '11px'
        assert label._style.get('text-align') == 'center'
        everything = list(view.dialog.descendants())
        close_buttons = [e for e in everything if getattr(e, 'text', None) == '閉じる']
        assert everything.index(label) < everything.index(close_buttons[-1])
    finally:
        view.close()
