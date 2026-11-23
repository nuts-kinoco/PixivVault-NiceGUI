"""Per-client view of the application-owned common log."""
from contextlib import nullcontext
from sqlite3 import Error as SQLiteError

from nicegui import ui

from .action_controls import secondary_button, status_label
from ..following_sync import subscribe_owner_on_loop


LOG_HIDDEN_TABS = frozenset({'download', 'queue', 'failed', 'history', 'learning',
                             'ranking', 'personal', 'library', 'bookmarks', 'recommend', 'dedup'})


def _visible(entry):
    owner = getattr(entry, 'owner', None)
    if owner is not None:
        try:
            owner.verify_current()
        except (OSError, RuntimeError, SQLiteError):
            return False
    return True


def render_log_entries(panel, entries):
    panel.clear()
    with panel:
        for entry in entries:
            owner = getattr(entry, 'owner', None)
            try:
                with owner.current() if owner is not None else nullcontext():
                    ui.label(f'[{entry.timestamp}] {entry.message}').classes(
                        'pv-queue-log-line pv-queue-log-' + entry.level)
            except (OSError, RuntimeError, SQLiteError):
                if owner is None:
                    raise


class SharedLogView:
    def __init__(self, log, *, following=None):
        self.log = log
        self.client = ui.context.client
        self.closed = False
        self.expanded = True
        self._signature = None
        with ui.column().classes('pv-shared-log-host w-full gap-0').props('data-testid=shared-log-host') as self.root:
            self.button = secondary_button('ログを折りたたむ', self.toggle, icon='expand_less',
                testid='shared-log-toggle').props('aria-expanded=true')
            self.panel = ui.column().classes('pv-queue-log pv-focus-ring w-full gap-0').props(
                'data-testid=shared-log role=region aria-label=共有ログ')
            self.panel.props['tabindex'] = 0
            self.button.props['aria-controls'] = self.panel.html_id
        self.root.set_visibility(False)
        self.timer = ui.timer(2, self.refresh, active=False, immediate=False)
        self._unsubscribe = (subscribe_owner_on_loop(following, self._owner_changed)
                             if following is not None else None)
        self.client.on_delete(self.close)

    def _owner_changed(self, _epoch):
        if self.closed:
            return
        self._signature = None
        if self.root.visible and self.expanded:
            self.refresh()
        else:
            self.panel.clear()

    def _scroll(self):
        from nicegui import core
        if core.loop is not None and not core.loop.is_closed() and self.root.visible and self.expanded:
            self.client.run_javascript(f"requestAnimationFrame(() => requestAnimationFrame(() => {{ const e=document.getElementById('{self.panel.html_id}'); if(e) e.scrollTop=e.scrollHeight; }}))")

    def show_tab(self, tab):
        if self.closed:
            return
        self.root.set_visibility(tab not in LOG_HIDDEN_TABS)
        if self.root.visible:
            self.timer.activate()
            self.refresh()
            self._scroll()
        else:
            self.timer.deactivate()

    def toggle(self, *_):
        if self.closed:
            return
        self.expanded = not self.expanded
        self.panel.set_visibility(self.expanded)
        self.button.set_text('ログを折りたたむ' if self.expanded else 'ログを表示')
        self.button.set_icon('expand_less' if self.expanded else 'expand_more')
        self.button.props(f'aria-expanded={str(self.expanded).lower()}')
        if self.expanded:
            self.refresh()
            self._scroll()

    def refresh(self):
        if self.closed or not self.root.visible or not self.expanded:
            return
        entries = tuple(entry for entry in self.log.snapshot() if _visible(entry))
        if entries == self._signature:
            return
        self._signature = entries
        render_log_entries(self.panel, entries)
        if not self.panel.default_slot.children:
            with self.panel:
                status_label('ログはまだありません。', classes='pv-log-empty')
        self._scroll()

    def close(self):
        if not self.closed:
            self.closed = True
            self.timer.cancel()
            if self._unsubscribe is not None:
                self._unsubscribe()
                self._unsubscribe = None
