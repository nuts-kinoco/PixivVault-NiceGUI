"""Per-client controls for opt-in managed author checks."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from threading import Event
from uuid import uuid4

from nicegui import ui

STATUS_LABELS = {
    'never': '未実行', 'saved': '完了', 'queued': '受付済み', 'running': '実行中',
    'deferred': '保留', 'failed': '失敗', 'error': 'エラー', 'empty': '対象なし',
    'partial': '一部完了', 'unknown': '不明', 'interrupted': '中断',
}


class SchedulerView:
    def __init__(self, services):
        self.services = services
        self.closed = False
        self.revision = None
        self._busy = False
        self._settings_dirty = False
        self._loading_settings = False
        self._refresh_pending = False
        with ui.column().classes('w-full gap-3') as self.root:
            ui.label('お気に入り作者の定期チェック').classes('text-h6')
            ui.label('新しい専用管理フォルダーにだけ保存します。旧DB、画像、Cookieは更新しません。')
            with ui.row().classes('items-center gap-3 flex-wrap'):
                self.open_button = ui.button('管理スケジューラを開く', on_click=self.open).props('data-testid=scheduler-open')
                self.interval = ui.select({'0': '自動チェックしない', '6': '6時間', '12': '12時間',
                                           '24': '24時間', '48': '48時間'}, value='24',
                                          label='自動チェック間隔').style('min-width:184px').props('data-testid=scheduler-interval')
                self.startup = ui.checkbox('起動時にもチェック').props('data-testid=scheduler-startup')
            self.authors = ui.input(label='お気に入り作者ID（カンマ区切り、最大128件）').classes('w-full').style('max-width:640px').props('data-testid=scheduler-authors')
            self.interval.on_value_change(lambda _: self._mark_settings_dirty())
            self.startup.on_value_change(lambda _: self._mark_settings_dirty())
            self.authors.on_value_change(lambda _: self._mark_settings_dirty())
            with ui.row().classes('items-center gap-2 flex-wrap'):
                self.save_button = ui.button('設定を保存', on_click=self.save).props('data-testid=scheduler-save')
                self.discard_button = ui.button('編集を破棄して再読込', on_click=self.discard_changes).props('data-testid=scheduler-discard')
                self.run_button = ui.button('今すぐチェック', on_click=self.run_once).props('data-testid=scheduler-run')
                self.refresh_button = ui.button(
                    '状態更新', on_click=lambda: self.refresh(ensure_after_current=True)
                ).props('data-testid=scheduler-refresh')
                self.ack_button = ui.button('中断状態を確認して解除', on_click=self.acknowledge).props('data-testid=scheduler-ack')
            self.status = ui.label('定期状態: 未起動').props('data-testid=scheduler-status')
            self.history_label = ui.label('実行履歴はありません').classes('text-caption').props('data-testid=scheduler-history')
            self.action_notice = ui.label('').props('data-testid=scheduler-action-notice role=status')
        self._set_controls(False)
        self._refreshing = False
        self._background_refresh_tasks: set[asyncio.Task] = set()
        self._timer = ui.timer(5.0, self.refresh)

    def _set_controls(self, enabled):
        for widget in (self.interval, self.startup, self.authors, self.save_button, self.discard_button,
                       self.run_button, self.refresh_button, self.ack_button):
            widget.set_enabled(enabled)

    def _mark_settings_dirty(self):
        if not self._loading_settings:
            self._settings_dirty = True

    async def open(self):
        if self.closed:
            return
        if self._busy:
            self.action_notice.set_text('処理中です。完了後に設定を開いてください')
            return
        self._busy = True
        try:
            ledger = await asyncio.to_thread(self.services.enable_managed_scheduler)
            if self.closed:
                return
            self.open_button.set_enabled(False)
            self._set_controls(True)
            self._load(ledger)
        except Exception:
            self.status.set_text('専用管理モードのスケジューラを開始できませんでした')
        finally:
            self._busy = False

    def _load(self, ledger):
        state = ledger.snapshot()
        if not self._settings_dirty:
            # Revision is the CAS token of the editable form, not the last
            # polled ledger version. Pin it while dirty so another client
            # cannot be silently overwritten.
            self.revision = state.revision
            self._loading_settings = True
            try:
                self.interval.set_value(str(state.interval_hours))
                self.startup.set_value(state.startup_check)
                self.authors.set_value(','.join(ledger.favourites()))
            finally:
                self._loading_settings = False
        status_label = STATUS_LABELS.get(state.last_status, '状態不明')
        self.status.set_text(f'定期状態: {status_label} / 次回UTC: {state.next_due_utc or "未設定"}')
        self.ack_button.set_enabled(state.last_status == 'interrupted')

    def discard_changes(self):
        """Explicitly abandon stale edits and adopt the newest CAS revision."""
        if self.closed or self.services.managed_scheduler is None:
            return
        if self._busy:
            self.action_notice.set_text('処理中です。完了後に編集を破棄してください')
            return
        self._settings_dirty = False
        try:
            self._load(self.services.managed_scheduler)
        except Exception:
            self._settings_dirty = True
            self.status.set_text('設定を再読込できませんでした')

    async def acknowledge(self):
        if self.closed or self.services.managed_scheduler is None:
            return
        if self._busy:
            self.action_notice.set_text('処理中です。完了後に中断状態を確認してください')
            return
        self._busy = True
        try:
            ledger = self.services.managed_scheduler
            from ..job_service import UNFINISHED
            def inspect():
                # Never silently requeue an ambiguous accepted job; the
                # existing JobService owns reconciliation and receipt checks.
                with self.services._scheduler_dispatch_lock:
                    if any(job.state in UNFINISHED for job in self.services.jobs.snapshot()):
                        raise RuntimeError('unfinished managed jobs must be inspected first')
                    self.services.managed_schedule_runner.acknowledge_interrupted(
                        expected_revision=self.revision)
            await asyncio.to_thread(inspect)
            self.services.managed_schedule_runner.settings_changed()
            self._load(ledger)
        except Exception:
            self.status.set_text('未完了ジョブまたは設定更新があり中断状態を解除できません。キューを確認してください')
        finally:
            self._busy = False

    async def refresh(self, *, ensure_after_current: bool = False):
        if self.closed or self.services.managed_scheduler is None:
            return
        if self._refreshing:
            # Queue explicit user/manual-run refreshes, but do not let every
            # five-second timer tick extend a long reconciliation forever.
            if ensure_after_current:
                self._refresh_pending = True
            return
        self._refreshing = True
        try:
            await asyncio.to_thread(self.services.reconcile_managed_scheduler)
            if self.closed:
                return
            ledger = self.services.managed_scheduler
            if ledger is None:
                return
            self._load(ledger)
            pending = await asyncio.to_thread(ledger.claim_pending_notifications)
            if self.closed:
                return
            notification_error = False
            if pending:
                # _schedule_refresh creates a fresh asyncio task whose
                # NiceGUI slot stack is empty. Restore this view's client
                # context before using ui.notify; otherwise a claimed durable
                # notification can be lost and the whole refresh misreported.
                try:
                    with self.root:
                        ui.notify(f'保存完了: {len(pending)}件', type='positive')
                except Exception:
                    # Delivery is deliberately at-most-once; if this client is
                    # disappearing, keep durable history readable instead of
                    # aborting the entire refresh with a misleading error.
                    notification_error = True
            history = await asyncio.to_thread(ledger.history)
            if self.closed:
                return
            if history:
                latest = history[0]
                status_label = STATUS_LABELS.get(latest['status'], '状態不明')
                self.history_label.set_text(
                    f'最終実行: {status_label} / 受付 {latest["accepted"]}件 / '
                    f'保留 {latest["deferred"]}件 / {latest["finished_utc"] or "実行中"}')
            if notification_error and not self.closed:
                self.action_notice.set_text('保存完了の通知を表示できませんでした。実行履歴で確認してください')
        except Exception:
            if not self.closed:
                self.status.set_text('状態を取得できませんでした')
        finally:
            self._refreshing = False
            if self._refresh_pending and not self.closed:
                self._refresh_pending = False
                self._schedule_refresh()

    def _schedule_refresh(self):
        if self.closed:
            return
        task = asyncio.create_task(self.refresh(ensure_after_current=True))
        self._background_refresh_tasks.add(task)
        task.add_done_callback(self._background_refresh_tasks.discard)

    async def save(self):
        if self.closed or self.revision is None:
            return
        if self._busy:
            self.action_notice.set_text('処理中です。完了後に設定を保存してください')
            return
        self._busy = True
        self.action_notice.set_text('')
        # An in-flight CAS save must not permit a Run click that the busy
        # guard would silently discard (or run under the previous settings).
        self.save_button.set_enabled(False)
        self.run_button.set_enabled(False)
        try:
            ledger = self.services.managed_scheduler
            raw = tuple(part.strip() for part in (self.authors.value or '').split(',') if part.strip())
            def update():
                return ledger.configure_all(
                    author_ids=raw, interval_hours=int(self.interval.value),
                    startup_check=bool(self.startup.value),
                    expected_revision=self.revision,
                    now=datetime.now(timezone.utc))
            await asyncio.to_thread(update)
            self.services.managed_schedule_runner.settings_changed()
            self._settings_dirty = False
            self._load(ledger)
        except Exception:
            self.status.set_text('設定を保存できませんでした。別画面の更新や入力を確認してください')
        finally:
            self._busy = False
            if not self.closed:
                self.save_button.set_enabled(True)
                self.run_button.set_enabled(True)

    async def run_once(self):
        if self.closed or self.services.closing or self.services.managed_scheduler is None:
            return
        if self._busy:
            self.action_notice.set_text('処理中です。完了後にもう一度操作してください')
            return
        if self._settings_dirty:
            self.action_notice.set_text('未保存の設定があります。保存または破棄してからチェックしてください')
            return
        self._busy = True
        self.action_notice.set_text('作者一覧を確認中です。受付件数を集計しています…')
        self.run_button.set_enabled(False)
        self.save_button.set_enabled(False)
        try:
            from ..managed_scheduler import submit_author_batch
            ledger = self.services.managed_scheduler
            slot = f'manual-{uuid4().hex}'
            def submit():
                with self.services._scheduler_dispatch_lock:
                    return submit_author_batch(self.services, ledger.favourites(),
                                               cancellation=self.services._scheduler_cancel_event,
                                               ledger=ledger, slot=slot)
            result = await asyncio.to_thread(submit)
            if not self.closed:
                self.action_notice.set_text(
                    f'受付 {result["accepted"]}件、保留 {result["deferred"]}件。保存結果はキューで確認してください')
                self.history_label.set_text(
                    f'今回のチェック: 受付 {result["accepted"]}件 / 保留 {result["deferred"]}件 / 保存結果を確認中')
                # Show admission immediately and release the form. Receipt
                # reconciliation can be much slower than enqueue (e.g. a
                # large deferred batch); do not keep Save/Run disabled while
                # the UI only needs a background history refresh.
                self._schedule_refresh()
        except Exception:
            self.action_notice.set_text('チェックに失敗しました。キュー状態を確認してください')
        finally:
            self._busy = False
            if not self.closed:
                self.run_button.set_enabled(True)
                self.save_button.set_enabled(True)

    def close(self):
        self.closed = True
        self._timer.cancel()
