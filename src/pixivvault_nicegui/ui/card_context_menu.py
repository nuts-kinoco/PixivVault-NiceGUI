"""カードとビューアで共通の右クリックメニュー部品。

元（Flet 版）の ft.ContextMenu(secondary_items=...) に当たる。右クリック（タッチは長押し）で開き、
キーボードは ContextMenu キーまたは Shift+F10 で、フォーカス中の要素から同じメニューを開く。
項目の文言・並びは呼び出し側が元コードどおりに渡す。
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from nicegui import ui

# ContextMenu キー（Windows の Menu キー）と Shift+F10 だけをサーバーへ通す。
KEY_JS = """event => {
    if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) {
        event.preventDefault(); event.stopPropagation(); emit();
    }
}"""


@dataclass(frozen=True)
class MenuEntry:
    key: str
    label: str
    icon: str | None = None
    on_click: Callable[[], Awaitable | None] | None = None
    enabled: bool = True
    href: str | None = None  # 指定時は新しいタブで開くリンク項目
    testid: str | None = None  # 既存の data-testid を保つとき（既定は <menu>-<key>）


def build_context_menu(entries, *, testid: str) -> tuple[ui.context_menu, dict[str, ui.menu_item]]:
    """親要素（カード等）の右クリックで開くメニューを作る。呼び出し元の `with` 内で呼ぶ。

    各項目は data-testid=<testid>-<key>。メニュー自体は data-testid=<testid>。
    """
    items: dict[str, ui.menu_item] = {}
    with ui.context_menu().props(f'data-testid={testid}') as menu:
        for entry in entries:
            item = ui.menu_item(entry.label, on_click=entry.on_click if entry.href is None else None)
            item.props(f'data-testid={entry.testid or f"{testid}-{entry.key}"}')
            if entry.href is not None:
                item.props(f'href="{entry.href}" target=_blank rel="noopener noreferrer"')
            if entry.icon:
                with item:
                    ui.icon(entry.icon).classes('pv-menu-icon')
            item.set_enabled(bool(entry.enabled))
            items[entry.key] = item
    return menu, items


def open_from_keyboard(element: ui.element, menu: ui.context_menu) -> None:
    """フォーカス中の要素で ContextMenu キー／Shift+F10 を押すと同じメニューを開く。"""
    element.on('keydown', lambda _e: menu.open(), js_handler=KEY_JS)
