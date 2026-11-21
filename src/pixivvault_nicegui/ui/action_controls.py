"""Shared action hierarchy and compact dialog controls."""

from contextlib import contextmanager

from nicegui import ui


def refresh_button(on_click, *, label: str, testid: str):
    button = ui.button(icon='refresh', color=None, on_click=on_click).props(
        f'flat dense aria-label={label} data-testid={testid}')
    button.tooltip(label)
    return button


def retry_button(on_click=None, *, testid: str):
    return ui.button('再試行', icon='refresh', color=None, on_click=on_click).props(
        f'unelevated dense rounded no-caps data-testid={testid}').classes('pv-retry-action')


def primary_button(label, on_click=None, *, icon=None, testid=None):
    button = ui.button(label, icon=icon, color=None, on_click=on_click).props(
        'unelevated dense no-caps').classes('pv-btn-primary')
    if testid is not None:
        button.props['data-testid'] = testid
    return button


def secondary_button(label, on_click=None, *, icon=None, testid=None, outline=False):
    button = ui.button(label, icon=icon, color=None, on_click=on_click).props(
        ('outline' if outline else 'flat') + ' dense no-caps').classes('pv-btn-secondary')
    if testid is not None:
        button.props['data-testid'] = testid
    return button


def danger_button(label, on_click=None, *, icon=None, testid=None):
    """Style an action whose workflow already supplies its own confirmation."""
    button = ui.button(label, icon=icon, color=None, on_click=on_click).props(
        'unelevated dense no-caps').classes('pv-btn-danger')
    if testid is not None:
        button.props['data-testid'] = testid
    return button


@contextmanager
def dialog_card(title=None, *, subtitle=None, size='md', testid=None, classes=''):
    with ui.card().classes(f'pv-dialog-card pv-dialog-{size} {classes}') as card:
        if testid is not None:
            card.props['data-testid'] = testid
        if title is not None:
            ui.label(title).classes('pv-dialog-title')
        if subtitle:
            ui.label(subtitle).classes('pv-subtitle')
        yield card


def dialog_actions():
    return ui.row().classes('pv-dialog-actions')


def outlined_input(*args, **kwargs):
    return ui.input(*args, **kwargs).props('outlined dense')


def outlined_select(*args, **kwargs):
    return ui.select(*args, **kwargs).props('outlined dense')
