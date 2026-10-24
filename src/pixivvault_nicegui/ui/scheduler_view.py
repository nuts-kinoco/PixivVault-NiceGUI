"""Per-client controls for opt-in managed author checks."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from threading import Event
from uuid import uuid4

from nicegui import ui


class SchedulerView:
    def __init__(self, services):
        self.services = services
        self.closed = False
        self.revision = None
        self._busy = False
        self._settings_dirty = False
        self._loading_settings = False
        with ui.column().classes('w-full gap-3') as self.root:
            ui.label('お気に入り作者の定期チェック').classes('text-h6')
            ui.label('新しい専用管理フォルダーにだけ保存します。旧DB、画像、Cookieは更新しません。')
            with ui.row().classes('items-center'):
                self.open_button = ui.button('管理スケジューラを開く', on_click=self.open).props('data-testid=scheduler-open')
                self.interval = ui.select({'0': '自動チェックしない', '6': '6時間', '12': '12時間',
                                           '24': '24時間', '48': '48時間'}, value='24',
                                          label='自動チェック間隔').props('data-testid=scheduler-interval')
                self.startup = ui.checkbox('起動時にもチェック').props('data-testid=scheduler-startup')
            self.authors = ui.input(label='お気に入り作者ID（カンマ区切り、最大128件）').props('data-testid=scheduler-authors')
            self.interval.on_value_change(lambda _: self._mark_settings_dirty())
            self.startup.on_value_change(lambda _: self._mark_settings_dirty())
            self.authors.on_value_change(lambda _: self._mark_settings_dirty())
            with ui.row().classes('items-center'):
                self.save_button = ui.button('設定を保存', on_click=self.save).props('data-testid=scheduler-save')
                self.discard_button = ui.button('編集を破棄して再読込', on_click=self.discard_changes).props('data-testid=scheduler-discard')
                self.run_button = ui.button('今すぐチェック', on_click=self.run_once).props('data-testid=scheduler-run')
                self.refresh_button = ui.button('状態更新', on_click=self.refresh).props('data-testid=scheduler-refresh')
                self.ack_button = ui.button('中断状態を確認して解除', on_click=self.acknowledge).props('data-testid=scheduler-ack')
            self.status = ui.label('スケジューラは未起動です').props('data-testid=scheduler-status')
            self.history_label = ui.label('実行履歴はありません').classes('text-caption').props('data-testid=scheduler-history')
        self._set_controls(False)
        self._timer = ui.timer(5.0, self.refresh)

    def _set_controls(self, enabled):
        for widget in (self.interval, self.startup, self.authors, self.save_button, self.discard_button,
                       self.run_button, self.refresh_button, self.ack_button):
            widget.set_enabled(enabled)

    def _mark_settings_dirty(self):
        if not self._loading_settings:
            self._settings_dirty = True

    async def open(self):
        if self.closed or self._busy:
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
        self.status.set_text(f'状態: {state.last_status} / 次回UTC: {state.next_due_utc or "未設定"}')
        self.ack_button.set_enabled(state.last_status == 'interrupted')

    def discard_changes(self):
        """Explicitly abandon stale edits and adopt the newest CAS revision."""
        if self.closed or self._busy or self.services.managed_scheduler is None:
            return
        self._settings_dirty = False
        try:
            self._load(self.services.managed_scheduler)
        except Exception:
            self._settings_dirty = True
            self.status.set_text('設定を再読込できませんでした')

    async def acknowledge(self):
        if self.closed or self._busy or self.services.managed_scheduler is None:
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

    async def refresh(self):
        if self.closed or self.services.managed_scheduler is None:
            return
        try:
            await asyncio.to_thread(self.services.reconcile_managed_scheduler)
            ledger = self.services.managed_scheduler
            self._load(ledger)
            pending = await asyncio.to_thread(ledger.claim_pending_notifications)
            if pending:
                ui.notify(f'保存完了: {len(pending)}件', type='positive')
            history = await asyncio.to_thread(ledger.history)
            if history:
                latest = history[0]
                self.history_label.set_text(
                    f'最終実行: {latest["status"]} / 受付 {latest["accepted"]}件 / '
                    f'保留 {latest["deferred"]}件 / {latest["finished_utc"] or "実行中"}')
        except Exception:
            self.status.set_text('状態を取得できませんでした')

    async def save(self):
        if self.closed or self._busy or self.revision is None:
            return
        self._busy = True
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

    async def run_once(self):
        if self.closed or self._busy or self.services.closing or self.services.managed_scheduler is None:
            return
        self._busy = True
        self.run_button.set_enabled(False)
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
            self.status.set_text(f'受付 {result["accepted"]}件、保留 {result["deferred"]}件。保存結果はキューで確認してください')
            await self.refresh()
        except Exception:
            self.status.set_text('チェックに失敗しました。キュー状態を確認してください')
        finally:
            self._busy = False
            if not self.closed:
                self.run_button.set_enabled(True)

    def close(self):
        self.closed = True
        self._timer.cancel()
