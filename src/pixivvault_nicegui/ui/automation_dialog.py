"""Shared summary and lazy details for the two automatic processes."""
from dataclasses import dataclass
import asyncio
import json
import sqlite3

from nicegui import ui

from .action_controls import notify_action, secondary_button
from .management_dialog import managed_dialog
from .metadata_view import MetadataView
from .scheduler_view import SchedulerView
from .shared_log_view import SharedLogView

PROCESSES = {'scheduler': '定期チェック', 'metadata': 'メタデータ補完'}


@dataclass(frozen=True)
class Summary:
    state: str = '状態を確認できません'
    last: str | None = None
    next: str | None = None
    failure: object = None
    stopped: bool = False
    running: bool = False

    def line(self, key):
        return (f'{PROCESSES[key]}：{self.state} ／ 最終実行 {self.last or "—"}'
                f' ／ 次回 {self.next or "—"}' + (' ／ 要確認' if self.failure else ' ／ 失敗なし'))


def summaries(services):
    policy = getattr(services, '_request_retry', None)
    blocked = policy is not None and any(
        record.get('stop_cause') in {'durable_gate', 'shared_limit', 'own_limit', 'gate_unverified'}
        for record in policy.progress_snapshot() if record['phase'] == 'stopped')
    scheduler = services.managed_schedule_runner
    schedule = getattr(scheduler, 'display_state', None)
    ledger = services.managed_scheduler
    if schedule is not None and getattr(ledger, '_account_guard', False):
        owner = getattr(services, '_automation_scheduler_owner', None)
        following = services.following_sync
        if (owner is None or owner.following is not following or owner.epoch != following._owner_epoch
                or owner.scope.reader is not services.live_client
                or owner.account_id != getattr(services.live_client, '_verified_identity', None)):
            schedule = None
    if services.managed_scheduler is None:
        periodic = Summary('未起動' if services.managed_downloads is not None else '利用不可')
    elif schedule is None or getattr(services, '_scheduler_waiting_for_auth', False):
        periodic = Summary('認証・フォロー情報の確認待ち')
    else:
        state, _ = schedule
        enabled = bool(state.interval_hours or state.startup_check)
        phase = scheduler.display_phase
        failure = scheduler.display_failure
        status = ('停止要求中（通信完了待ち）' if phase == 'stopping' else
                  '実行中' if phase == 'running' else '停止（設定 OFF）' if not enabled else
                  '取得停止中' if blocked else '停止しました' if phase == 'stopped' else
                  '要確認' if failure else '予定待ち')
        periodic = Summary(status, state.last_due_utc, state.next_due_utc, failure,
                           blocked and enabled, phase in {'running', 'stopping'})
    if services.managed_downloads is None:
        metadata = Summary('利用不可')
    else:
        enabled = getattr(services, '_metadata_auto_seen', None)
        running = services._metadata_run_lock.locked()
        cancelling = running and services._metadata_cancel_event.is_set()
        auto = getattr(services, '_metadata_auto', None)
        current = getattr(services, '_automation_metadata_result', None)
        reader = services.live_client
        owner = (reader, getattr(reader, '_verified_identity', None))
        _, stopped, failure, last = current if current and current[0] == owner else (None, None, None, None)
        if cancelling:
            state = '停止要求中（通信完了待ち）'
        elif running:
            state = '実行中'
        elif enabled is False:
            state = '停止（設定 OFF）'
        elif enabled is None:
            state = '状態を確認できません'
        elif blocked:
            state = '取得停止中'
        elif stopped == 'cancelled':
            state = '停止しました'
        else:
            state = '要確認' if failure else '予定待ち'
        metadata = Summary(state, last, getattr(auto, 'next_run_display', None), failure,
                           blocked and enabled is True, running)
    return {'scheduler': periodic, 'metadata': metadata}


class AutomationDialog:
    def __init__(self, shell, button, settings_button):
        self.shell = shell
        self.services = shell.services
        self.client = shell.client
        self.button, self.settings_button = button, settings_button
        self.views, self.panels, self.tabs = {}, {}, {}
        self.selected = 'scheduler'
        self.return_button = button
        self.acknowledged = {}
        self.current = {}
        self.preference_labels = {}
        self.dialog = None
        self.log = None
        self._completion = None
        self._notification_generation = 0
        with button:
            self.badge = ui.badge('').props('floating color=negative aria-hidden=true')
            self.badge.set_visibility(False)
            self.tooltip = ui.tooltip('自動処理')
        self.timer = ui.timer(1, self.tick, immediate=False)

    def build_status(self):
        with ui.element('div').classes('pv-automation-status'):
            self.unresolved = ui.label('').props('data-testid=automation-unresolved')
            self.breakdown = ui.label('').props('data-testid=automation-breakdown')
            self.live = ui.label('').props('role=status aria-atomic=true data-testid=automation-status')
        self.refresh_summary()

    def available(self, key):
        return self.shell._tab_available(key)

    def show_hint(self):
        store = self.services.preferences
        if store is None:
            return
        try:
            snapshot = store.snapshot()
            if snapshot.values['automation_nav_hint_seen'] == '1':
                return
            store.update({'automation_nav_hint_seen': '1'}, expected_revision=snapshot.revision)
        except Exception:
            return
        notify_action('定期チェックとメタデータ補完は「自動処理」に移りました',
                      important=True, return_focus=self.button)

    def refresh_summary(self):
        current = summaries(self.services)
        previous = self.current
        if current == previous:
            return
        self.current = current
        events = []
        for key, item in current.items():
            old = previous.get(key)
            if old is None or item.failure != old.failure:
                self.acknowledged.pop(key, None)
                if item.failure:
                    events.append(f'{PROCESSES[key]}：未解消の失敗があります')
                elif old and old.failure:
                    events.append(f'{PROCESSES[key]}：未解消の表示を解除しました')
            if old and item.stopped != old.stopped:
                events.append(f'{PROCESSES[key]}：{item.state}')
        if events:
            self.live.set_text(' ／ '.join(events))
        self.render_summary()

    async def tick(self):
        if self.shell.closed:
            return
        self.refresh_summary()
        signal = getattr(self.services, '_automation_scheduler_completion', None)
        if signal is None or signal[0] == self._notification_generation or not self.client.has_socket_connection:
            return
        serial, ledger, owner = signal
        self._notification_generation = serial
        try:
            pending = await asyncio.to_thread(ledger.claim_pending_notifications, owner=owner)
            if not pending or self.shell.closed or self.services.managed_scheduler is not ledger:
                return
            if owner is not None:
                with owner.current():
                    self._notify_completion(len(pending))
            else:
                self._notify_completion(len(pending))
        except (RuntimeError, OSError, sqlite3.Error):
            return

    def _notify_completion(self, count):
        if self._completion is not None and not self._completion.is_deleted:
            self._completion.dismiss()
        with self.client:
            self._completion = notify_action(f'保存完了: {count}件', type='positive', replaceable=True)

    def render_summary(self):
        failures = sum(bool(item.failure) for item in self.current.values())
        stopped = any(item.stopped for item in self.current.values())
        checked = len(self.acknowledged)
        title = f'自動処理：未解消 {failures}' if failures else ''
        detail = f'未確認 {failures - checked}／確認済み {checked}' if failures else ''
        self.unresolved.set_text(title)
        self.breakdown.set_text(detail)
        self.badge.set_text('!' if failures else '■')
        self.badge.set_visibility(bool(failures or stopped))
        self.badge.props['color'] = 'negative' if failures else 'warning'
        name = '。'.join(filter(None, (title or '自動処理', detail, *(
            f'{PROCESSES[key]}：{item.state}' for key, item in self.current.items()))))
        self.button.props['aria-label'] = name
        self.tooltip.set_text(name)
        for key, item in self.current.items():
            if self.dialog is not None:
                self.labels[key].set_text(item.line(key))
                self.ack_buttons[key].set_visibility(bool(item.failure) and key not in self.acknowledged)
                self.confirmed[key].set_visibility(bool(item.failure) and key in self.acknowledged)
                self.recover_buttons[key].set_visibility(item.stopped)
                if key == 'scheduler' and key in self.views:
                    self.views[key].update_automation_controls(item.state, item.running)
            label = self.preference_labels.get(key)
            if label is not None and not label.is_deleted:
                label.set_text(item.line(key))

    def acknowledge(self, key):
        if self.current[key].failure:
            self.acknowledged[key] = self.current[key].failure
            self.render_summary()

    def open(self, key=None, *, from_settings=False):
        if self.shell.closed:
            return
        self.refresh_summary()
        choices = [candidate for candidate in PROCESSES if self.available(candidate)]
        if key is None:
            key = next((k for k in choices if self.current[k].failure), None) or next(
                (k for k in choices if self.current[k].running), None) or next(iter(choices), 'scheduler')
        elif key not in choices:
            notify_action(f'{PROCESSES[key]}はこの起動では利用できません。', type='warning')
            return
        if self.dialog is None:
            self.build()
        self.return_button = self.settings_button if from_settings else self.button
        self.dialog.props['data-pv-focus-origin'] = self.return_button.html_id
        self.button.props['aria-controls'] = self.dialog.html_id
        self.dialog.open()
        self.select(key)

    def build(self):
        self.labels, self.ack_buttons, self.confirmed, self.recover_buttons = {}, {}, {}, {}
        self.dialog = managed_dialog().props('no-refocus data-testid=automation-dialog')
        with self.dialog, ui.card().classes('pv-automation-dialog'):
            with ui.row().classes('w-full items-center justify-between gap-1'):
                title = ui.label('自動処理').classes('pv-dialog-title').props('role=heading aria-level=2')
                self.dialog.props['aria-labelledby'] = title.html_id
                self.dialog.props['aria-modal'] = 'true'
                secondary_button('閉じる', self.dialog.close, testid='automation-close')
            for key in PROCESSES:
                with ui.row().classes('w-full items-center gap-1'):
                    self.labels[key] = ui.label('').classes('pv-subtitle')
                    self.ack_buttons[key] = secondary_button('確認済みにする', lambda _, key=key: self.acknowledge(key),
                                                              testid=f'automation-ack-{key}')
                    self.confirmed[key] = ui.label('確認済み').classes('pv-subtitle')
                    self.recover_buttons[key] = secondary_button('取得停止の確認・回復', self.open_recovery,
                                                                 testid=f'automation-recover-{key}')
            with ui.row().classes('w-full gap-1').props('role=tablist aria-label=自動処理の詳細') as tablist:
                for key, label in PROCESSES.items():
                    self.tabs[key] = secondary_button(label, lambda _, key=key: self.select(key),
                                                      testid=f'automation-{key}').props('role=tab')
            tablist.on('keydown', js_handler="""event => {
                const keys = ['ArrowLeft', 'ArrowRight', 'Home', 'End'];
                if (!keys.includes(event.key) || event.isComposing) return;
                const tabs = [...event.currentTarget.querySelectorAll('[role=tab]:not(:disabled)')];
                const index = tabs.indexOf(event.target.closest('[role=tab]'));
                if (index < 0 || !tabs.length) return;
                event.preventDefault();
                const target = {Home: 0, End: tabs.length - 1,
                    ArrowLeft: (index + tabs.length - 1) % tabs.length,
                    ArrowRight: (index + 1) % tabs.length}[event.key];
                tabs[target].click();
            }""")
            with ui.column().classes('pv-automation-scroll w-full'):
                for key in PROCESSES:
                    panel = self.panels[key] = ui.column().classes('w-full').props('role=tabpanel')
                    panel.props['aria-labelledby'] = self.tabs[key].html_id
                    self.tabs[key].props['aria-controls'] = panel.html_id
                    self.tabs[key].set_enabled(self.available(key))
                self.log = SharedLogView(self.services.shared_log, following=self.services.following_sync)
                self.log.show_tab('download')
        self.dialog.on('show', self.focus_tab)
        self.dialog.on('keydown', js_handler='''event => {
            if (event.key === 'Escape' && (event.isComposing || event.keyCode === 229))
                event.stopPropagation();
        }''')
        self.dialog.on('hide', self.hide)
        self.render_summary()

    async def open_recovery(self):
        if self.shell.get_recovery_view is not None:
            await self.shell.get_recovery_view.open()
        elif self.shell.request_status_view is not None:
            self.dialog.close()
            view = self.shell.request_status_view
            await view.confirm_gate()

    def select(self, key):
        if not self.available(key) and any(self.available(k) for k in PROCESSES):
            return
        self.selected = key
        if key not in self.views and self.available(key):
            with self.panels[key]:
                factory = SchedulerView if key == 'scheduler' else MetadataView
                self.views[key] = factory(self.services, hosted=True)
                if key == 'scheduler':
                    summary = self.current[key]
                    self.views[key].update_automation_controls(summary.state, summary.running)
        for name, panel in self.panels.items():
            active = key == name
            panel.set_visibility(active)
            self.tabs[name].props.update({'aria-selected': str(active).lower(), 'tabindex': 0 if active else -1})
            if name in self.views:
                self.views[name].set_detail_active(active)
        self.log.show_tab('scheduler')
        self.focus_tab()

    def focus_tab(self):
        if self.client.has_socket_connection and self.dialog.value:
            target = self.tabs[self.selected]
            selector = 'automation-close' if not target.enabled else f'automation-{self.selected}'
            self.client.run_javascript(f"""requestAnimationFrame(() =>
                document.querySelector('[data-testid={selector}]')?.focus({{preventScroll:true}}))""")

    def hide(self):
        for view in self.views.values():
            view.set_detail_active(False)
        self.log.show_tab('download')
        if self.client.has_socket_connection:
            self.client.run_javascript(f"""requestAnimationFrame(() => {{
                const dialog = document.getElementById({json.dumps(self.dialog.html_id)});
                if (!dialog?.querySelector('.q-dialog__inner'))
                    window.pvFocus.restore({{origin: document.getElementById({json.dumps(self.return_button.html_id)}), excluded: dialog}});
            }})""")

    def close(self):
        self.timer.cancel()
        for view in self.views.values():
            view.close()
        if self.log is not None:
            self.log.close()
