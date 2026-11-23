"""`--tab <key>` 起動オプション（元 gui.py:98 TAB_INITIAL / :128 resolve_startup_tab / main.py:333-342）。

元と同じキー名で指定する。有効キーは STARTUP_TAB_ORDER から導出し、手書きで複製しない。
"""
from __future__ import annotations

# 元 gui.py の TAB_ORDER（キー名と並びを元のまま）。
STARTUP_TAB_ORDER = ('unified', 'queue', 'failed', 'history', 'discovery',
                     'dedup', 'ranking', 'library', 'recommend')
# 起動直後に開くタブ（元 TAB_INITIAL = TAB_UNIFIED = ダウンロード）。
TAB_INITIAL = 'unified'
CLI_TAB_ORDER = (*STARTUP_TAB_ORDER, 'bookmarks', 'personal', 'scheduler', 'metadata')
# 移植版のタブキーは、元と名前が違う2つだけ読み替える。
_SHELL_KEYS = {'unified': 'download', 'discovery': 'learning'}


def resolve_startup_tab(requested_key, tab_order=CLI_TAB_ORDER, default_key=TAB_INITIAL):
    """要求キーから起動タブを決める純関数。不正キーは黙らず警告文言を返す。"""
    if requested_key is None:
        return default_key, None
    if requested_key in tab_order:
        return requested_key, None
    valid = '・'.join(tab_order)
    return default_key, f"不明なタブ '{requested_key}'。有効: {valid}。既定で開きます"


def shell_tab_key(key):
    return _SHELL_KEYS.get(key, key)
