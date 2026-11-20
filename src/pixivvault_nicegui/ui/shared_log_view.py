"""Per-client view of the application-owned common log."""
from nicegui import ui


LOG_HIDDEN_TABS = frozenset({'download', 'queue', 'failed', 'history', 'learning',
                             'ranking', 'personal', 'library', 'recommend', 'dedup'})


def render_log_entries(panel, entries):
    panel.clear()
    with panel:
        for entry in entries:
            ui.label(f'[{entry.timestamp}] {entry.message}').classes(
                'pv-queue-log-line pv-queue-log-' + entry.level)


class SharedLogView:
    def __init__(self, log):
        self.log = log
        self.client = ui.context.client
        self.closed = False
        self.expanded = True
        self._signature = None
        with ui.column().classes('pv-shared-log-host w-full gap-0').props('data-testid=shared-log-host') as self.root:
            self.button = ui.button('ログを折りたたむ', icon='expand_more', color=None,
                                    on_click=self.toggle).props('flat dense no-caps data-testid=shared-log-toggle aria-expanded=true')
            self.panel = ui.column().classes('pv-queue-log w-full gap-0').props('data-testid=shared-log')
        self.root.set_visibility(False)
        self.timer = ui.timer(2, self.refresh, immediate=False)

    def _scroll(self):
        from nicegui import core
        if core.loop is not None and self.root.visible and self.expanded:
            self.client.run_javascript(f"requestAnimationFrame(() => requestAnimationFrame(() => {{ const e=document.getElementById('{self.panel.html_id}'); if(e) e.scrollTop=e.scrollHeight; }}))")

    def show_tab(self, tab):
        if self.closed:
            return
        self.root.set_visibility(tab not in LOG_HIDDEN_TABS)
        if self.root.visible:
            self.refresh()
            self._scroll()

    def toggle(self, *_):
        if self.closed:
            return
        self.expanded = not self.expanded
        self.panel.set_visibility(self.expanded)
        self.button.set_text('ログを折りたたむ' if self.expanded else 'ログを表示')
        self.button.props(f'aria-expanded={str(self.expanded).lower()}')
        if self.expanded:
            self.refresh()
            self._scroll()

    def refresh(self):
        if self.closed or not self.root.visible or not self.expanded:
            return
        entries = self.log.snapshot()
        if entries == self._signature:
            return
        self._signature = entries
        render_log_entries(self.panel, entries)
        self._scroll()

    def close(self):
        if not self.closed:
            self.closed = True
            self.timer.deactivate()
