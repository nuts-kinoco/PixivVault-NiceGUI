"""Shared action hierarchy and compact dialog controls."""

from contextlib import contextmanager

from nicegui import ui
from nicegui.elements.dialog import Dialog

from .management_dialog import dismiss_focus_js, remember_focus, toast_dismiss_action


def refresh_button(on_click, *, label: str, testid: str):
    button = ui.button(icon='refresh', color=None, on_click=on_click).props('flat dense').classes('pv-hit28')
    button.props['aria-label'] = label
    button.props['data-testid'] = testid
    button.tooltip(label)
    return button


def retry_button(on_click=None, *, testid: str):
    return ui.button('再試行', icon='refresh', color=None, on_click=on_click).props(
        f'unelevated dense rounded no-caps data-testid={testid}').classes('pv-retry-action pv-hit28')


def primary_button(label, on_click=None, *, icon=None, testid=None):
    button = ui.button(label, icon=icon, color=None, on_click=on_click).props(
        'unelevated dense no-caps').classes('pv-btn-primary pv-hit28')
    if testid is not None:
        button.props['data-testid'] = testid
    return button


def secondary_button(label, on_click=None, *, icon=None, testid=None, outline=False):
    button = ui.button(label, icon=icon, color=None, on_click=on_click).props(
        ('outline' if outline else 'flat') + ' dense no-caps').classes('pv-btn-secondary pv-hit28')
    if testid is not None:
        button.props['data-testid'] = testid
    return button


def danger_button(label, on_click=None, *, icon=None, testid=None):
    """Style an action whose workflow already supplies its own confirmation."""
    button = ui.button(label, icon=icon, color=None, on_click=on_click).props(
        'unelevated dense no-caps').classes('pv-btn-danger pv-hit28')
    if testid is not None:
        button.props['data-testid'] = testid
    return button


def notify_action(message, *, type='info', important=False, within_dialog=None,
                  return_focus=None, on_dismiss=None, replaceable=False):
    """Show an action result, or return the controls of an inline alert."""
    if within_dialog is not None:
        return _dialog_notice(within_dialog, message, type, return_focus, on_dismiss)
    options = {'classes': f'pv-toast pv-toast-{type}'}
    if type in ('negative', 'positive', 'info'):
        # Leave the background to theme.py instead of Quasar's type color utility.
        options['color'] = f'pv-toast-{type}'
    if important or type == 'negative':
        return ui.notification(options={
            **options, 'message': str(message), 'type': type, 'position': 'bottom', 'timeout': 0,
            'actions': [{'label': '閉じる', 'class': 'pv-hit28', ':handler': toast_dismiss_action(return_focus)}],
        }, on_dismiss=on_dismiss)
    if replaceable:
        return ui.notification(message, type=type, **options)
    ui.notify(message, type=type, **options)


def _dialog_notice(content, message, kind, return_focus, on_dismiss):
    owner = content
    while not isinstance(owner, Dialog) and owner.parent_slot is not None:
        owner = owner.parent_slot.parent
    if not isinstance(owner, Dialog):
        owner = content

    if not hasattr(owner, '_pv_notices'):
        owner._pv_notices = {}

    notices = owner._pv_notices
    previous = notices.get(kind)
    if previous is not None and not previous.is_deleted:
        previous.delete()

    def dismiss(_event=None):
        if notices.get(kind) is alert:
            notices.pop(kind)
            alert.delete()
            if on_dismiss is not None:
                on_dismiss()

    with content, ui.row().classes(f'pv-dialog-alert pv-toast pv-toast-{kind}') as alert:
        message_label = ui.label(str(message)).classes('pv-dialog-alert-message').props('role=alert')
        close = secondary_button('閉じる')
    notices[kind] = alert

    close.on('click', dismiss, js_handler=dismiss_focus_js(alert, return_focus=return_focus))
    if return_focus is None:
        remember_focus(alert)
    return alert, message_label, close


def status_label(text='', *, testid=None, classes='pv-empty'):
    """Render a status or empty state and collapse its layout when the text is empty."""
    label = ui.label(text).classes(f'pv-hide-empty {classes}').props('role=status')
    if testid is not None:
        label.props['data-testid'] = testid
    return label


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
