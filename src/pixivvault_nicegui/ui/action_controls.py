"""Shared visual controls for explicit refresh actions."""

from nicegui import ui


def refresh_button(on_click, *, label: str, testid: str):
    button = ui.button(icon='refresh', color=None, on_click=on_click).props(
        f'flat dense aria-label={label} data-testid={testid}')
    button.tooltip(label)
    return button


def retry_button(on_click=None, *, testid: str):
    return ui.button('再試行', icon='refresh', color=None, on_click=on_click).props(
        f'unelevated dense rounded no-caps data-testid={testid}').classes('pv-retry-action')
