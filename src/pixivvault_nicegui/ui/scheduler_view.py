"""Per-client controls for opt-in managed author checks."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from threading import Event
from uuid import uuid4
from ..account_scope import AccountMismatch, AccountScope, require_same_account
from ..legacy_author_sync import LegacyAuthorSourceError
from ..managed_scheduler import ScheduleAccountError

from nicegui import ui

STATUS_LABELS = {
    'never': '未実行', 'saved': '完了', 'queued': '受付済み', 'running': '実行中',
    'deferred': '保留', 'failed': '失敗', 'error': 'エラー', 'empty': '対象なし',
    'partial': '一部完了', 'unknown': '不明', 'interrupted': '中断',
}


class SchedulerView:
    _stars_unknown_in_form = False   # the form was filled while the ☆ list could not be read
    def __init__(self, services):
        self.services = services
        self.closed = False
        self.revision = None
        self._settings_account = None
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
            ui.label('☆お気に入りにした作者全員をチェックします。').classes('pv-subtitle')
            self.authors = ui.input(label='お気に入り（☆）作者ID（カンマ区切り。☆はダウンロード画面でも付け外しできます）').classes('w-full').style('max-width:640px').props('data-testid=scheduler-authors')
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
            self.star_notice = ui.label('').classes('text-caption').props('data-testid=scheduler-star-import role=status')
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
            await self.refresh(ensure_after_current=True)
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
                self._settings_account = None
                try:
                    self._settings_account = ledger.favourite_account()
                    self.authors.set_value(','.join(ledger.favourites(self._settings_account)))
                    self._stars_unknown_in_form = False
                    self._loaded_authors = self.authors.value or ''
                except Exception:
                    self.authors.set_value('')
                    self._stars_unknown_in_form = True
            finally:
                self._loading_settings = False
        self.star_notice.set_text(
            '☆の一覧を読み込めません。読み込めるまで、☆の設定は保存できません（保留中の取得は消しません）。'
            if self._stars_unknown_in_form else '')
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

    def _displayed_history_account(self, ledger, account_id):
        if getattr(ledger, '_account_guard', False):
            mismatch = ScheduleAccountError('mismatch')
            require_same_account(account_id, ledger.favourite_account(), error=mismatch)
            if account_id is None:
                raise mismatch
            AccountScope(account_id, getattr(self.services, 'live_client', None)).verify_current(
                self.services, error=mismatch)
        return account_id

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
            account_id, revision = self._settings_account, self.revision

            def inspect():
                # Never silently requeue an ambiguous accepted job; the
                # existing JobService owns reconciliation and receipt checks.
                with self.services._scheduler_dispatch_lock:
                    if any(job.state in UNFINISHED for job in self.services.jobs.snapshot()):
                        raise RuntimeError('unfinished managed jobs must be inspected first')
                    self.services.managed_schedule_runner.acknowledge_interrupted(
                        expected_revision=revision,
                        account_id=self._displayed_history_account(ledger, account_id))
            await asyncio.to_thread(inspect)
            self.services.managed_schedule_runner.settings_changed()
            self._load(ledger)
        except (ScheduleAccountError, AccountMismatch):
            self.action_notice.set_text('同期先を確認して、定期チェック画面を再読込してください。')
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
            account_id = self._settings_account

            def claim_notifications():
                owner = self._displayed_history_account(ledger, account_id)
                return ledger.claim_pending_notifications(account_id=owner)

            try:
                pending = await asyncio.to_thread(claim_notifications)
            except (ScheduleAccountError, AccountMismatch):
                pending = ()
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
                # The periodic schedule and a manually dispatched check have
                # separate durable state. Show both without replacing the
                # schedule's interrupted/running status with an old history row.
                self.status.set_text(f'{self.status.text} / 最終チェック: {status_label}')
                finished = latest['finished_utc'] or ('実行中' if latest['status'] == 'running' else '完了日時なし')
                self.history_label.set_text(
                    f'最終実行: {status_label} / 受付 {latest["accepted"]}件 / '
                    f'保留 {latest["deferred"]}件 / {finished}')
                if latest.get('error_code') == AccountMismatch.code:
                    self.action_notice.set_text(check_failure_message(AccountMismatch()))
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
        stop_saved = False
        try:
            ledger = self.services.managed_scheduler
            form = (self.authors.value or '', self.interval.value, self.startup.value)
            revision = self.revision
            account_id = self._settings_account
            if str(form[1]) == '0' and not form[2]:
                authors_edited = form[0] != getattr(self, '_loaded_authors', '')
                saved = await asyncio.to_thread(ledger._disable, expected_revision=revision)
                stop_saved = True
                runner = getattr(self.services, 'managed_schedule_runner', None)
                if runner is not None:
                    runner.settings_changed()
                if not authors_edited:
                    self._settings_dirty = (self.authors.value or '', self.interval.value, self.startup.value) != form
                    if self._settings_dirty:
                        self.revision = saved.revision
                    self._load(ledger)
                    return
                if self._stars_unknown_in_form:
                    self.action_notice.set_text('停止を保存しました。☆を変更するには再読込してください。')
                    return
                revision = saved.revision
            require_same_account(account_id, ledger.favourite_account(),
                                 error=ScheduleAccountError('mismatch'))
            raw = tuple(part.strip() for part in form[0].split(',') if part.strip())
            if self._stars_unknown_in_form:
                try:
                    ledger.favourites(account_id)
                    readable = True
                except Exception:
                    readable = False
                if readable:
                    # The list can be read now, but this form was filled while it could not: saving it
                    # would replace every star with an empty box. Make the user reload first.
                    self.action_notice.set_text(
                        '☆の一覧を読めるようになりました。「編集を破棄して再読込」してから保存してください')
                    return
                if form[0].strip():
                    self.action_notice.set_text(
                        '☆の一覧を読めないため入力したIDは保存できません。復旧後に再読込してください')
                    return
                raw = None   # still unknown: save only the interval / startup, never the stars
            loaded_authors = self._loaded_authors if raw is not None else None

            def update():
                if raw is None or form[0] == loaded_authors:
                    return ledger.configure(interval_hours=int(form[1]), startup_check=bool(form[2]),
                                            expected_revision=revision, now=datetime.now(timezone.utc),
                                            account_id=account_id), (), ()
                return ledger.configure_all(author_ids=raw, interval_hours=int(form[1]),
                                            startup_check=bool(form[2]), expected_revision=revision,
                                            now=datetime.now(timezone.utc), return_ignored=True,
                                            account_id=account_id, report_retained=True)
            saved, ignored, retained = await asyncio.to_thread(update)
            runner = getattr(self.services, 'managed_schedule_runner', None)
            if runner is not None:
                runner.settings_changed()
            # Edits made while the worker saved the captured form still belong
            # to the user. Keep them and base the next CAS on this commit.
            self._settings_dirty = (self.authors.value or '', self.interval.value, self.startup.value) != form
            if self._settings_dirty:
                self.revision = saved.revision
            self._load(ledger)
            notices = []
            if ignored:
                notices.append('フォローしていないため保存しなかったID: ' + ', '.join(ignored))
            if retained:
                notices.append('フォローが解除された作者の☆と保留を保持しました。ID ' + ', '.join(retained))
            if notices:
                self.action_notice.set_text(' / '.join(notices))
        except ScheduleAccountError:
            notice = '同期先アカウントが変わりました。「編集を破棄して再読込」してから保存してください'
            self.action_notice.set_text(('停止を保存しました。' if stop_saved else '') + notice)
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
            expected = self._settings_account

            def submit():
                with self.services._scheduler_dispatch_lock:
                    dispatch = getattr(self.services, 'dispatch_author_check', None)
                    if dispatch is not None:
                        displayed = {}
                        if getattr(ledger, '_account_guard', False):
                            displayed['displayed_account'] = AccountScope(expected).account_id
                        return dispatch(ledger, slot, self.services._scheduler_cancel_event, wait=False, **displayed)
                    source = getattr(self.services, 'scheduler_favourite_authors', None)
                    authors = source() if source is not None else ledger.favourites()
                    if source is not None:
                        ledger._sync_favourites(authors)
                    return submit_author_batch(self.services, authors,
                                               cancellation=self.services._scheduler_cancel_event,
                                               ledger=ledger, slot=slot)
            result = await asyncio.to_thread(submit)
            if not self.closed and result.get('started'):
                self.action_notice.set_text(f'☆作者 {result["authors"]}人のチェックを始めました。進捗はダウンロード画面で確認できます')
            elif not self.closed:
                self.action_notice.set_text(
                    f'受付 {result["accepted"]}件、保留 {result["deferred"]}件。保存結果はキューで確認してください')
            if not self.closed and not result.get('started'):
                self.history_label.set_text(
                    f'今回のチェック: 受付 {result["accepted"]}件 / 保留 {result["deferred"]}件 / 保存結果を確認中')
                # Show admission immediately and release the form. Receipt
                # reconciliation can be much slower than enqueue (e.g. a
                # large deferred batch); do not keep Save/Run disabled while
                # the UI only needs a background history refresh.
                self._schedule_refresh()
        except LegacyAuthorSourceError:
            self.action_notice.set_text('旧DBまたは画像の保存元を確認できません。停止した旧DBのコピーと保存元を確認してください')
        except Exception as error:
            self.action_notice.set_text(check_failure_message(error))
        finally:
            self._busy = False
            if not self.closed:
                self.run_button.set_enabled(True)
                self.save_button.set_enabled(True)

    def close(self):
        self.closed = True
        self._timer.cancel()


def check_failure_message(error):
    # Never display raw exception text: it may contain paths or credentials.
    from ..managed_network_stop_gate import ManagedNetworkStopped, ManagedNetworkGateBusy, ManagedNetworkGateStorageError
    from ..pixiv_read_client import HTTPStatusRejected, AuthenticationMissing
    if isinstance(error, (ScheduleAccountError, AccountMismatch)):
        if getattr(error, 'reason', None) == 'unknown':
            return '定期チェックの既存データの出所を確認できません。データを保持して停止しました。'
        return '定期チェックの同期先または台帳が別のアカウントです。既存データを保持して停止しました。画面を再読込してください。'
    if isinstance(error, ManagedNetworkStopped):
        return f'HTTP {error.status_code}による取得停止が記録されています。取得停止の確認・回復で状態を確認してください。'
    if isinstance(error, ManagedNetworkGateBusy):
        return '別の取得処理が通信記録を使用中です。完了を待って再確認してください。'
    if isinstance(error, ManagedNetworkGateStorageError):
        return '前の送信結果が不確か、または通信記録を確認できないため停止しました。「取得停止の確認・回復」を開いて確認してください。'
    if isinstance(error, AuthenticationMissing):
        return '認証情報を確認できません。Cookieの読込・認証状態を確認してください。'
    if isinstance(error, HTTPStatusRejected):
        if error.status_code in (401,403,429):
            reason={401:'認証エラー',403:'アクセス拒否',429:'アクセス制限'}[error.status_code]
            return f'HTTP {error.status_code}（{reason}）を確認しました。発生時点は未確認です。停止状態と回復手順を確認してください。'
        if error.status_code == 404:
            return '通信先がHTTP 404を返したためチェックを完了できませんでした。作者IDと作品の公開状態を確認して、再実行してください。'
        return f'通信先がHTTP {error.status_code}を返したためチェックを完了できませんでした。'
    if isinstance(error, (TimeoutError, ConnectionError, OSError)):
        return '通信または保存先へのアクセスに失敗しました。接続状態と試験保存先を確認してください。'
    return '作者情報またはチェック結果を確認できませんでした。作者ID・接続状態・保存先を確認して再実行してください。'
