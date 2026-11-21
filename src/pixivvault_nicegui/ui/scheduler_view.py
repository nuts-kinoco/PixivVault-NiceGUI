"""Per-client controls for opt-in managed author checks."""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone
from threading import Event
from uuid import uuid4
from ..account_scope import AccountMismatch, require_same_account
from ..following_sync import subscribe_owner_on_loop
from ..legacy_author_sync import LegacyAuthorSourceError
from ..managed_scheduler import ScheduleAccountError, ScheduleConfirmationUnavailable

from nicegui import ui

STATUS_LABELS = {
    'never': '未実行', 'saved': '完了', 'queued': '受付済み', 'running': '実行中',
    'deferred': '保留', 'failed': '失敗', 'error': 'エラー', 'empty': '対象なし',
    'partial': '一部完了', 'unknown': '不明', 'interrupted': '中断',
}

CONFIRMATION_STATUS = {
    'waiting': '認証・フォロー情報の確認待ち',
    'unsynced': 'フォロー同期の完了待ち',
    'restart': 'Cookieの変更を反映するには再起動してください',
    'closing': '終了処理中',
    'busy': '別の画面で定期チェックの認証を確認中',
}


class SchedulerView:
    _stars_unknown_in_form = False   # the form was filled while the ☆ list could not be read
    def __init__(self, services):
        self.services = services
        self.closed = False
        self.revision = None
        self._settings_account = None
        self._settings_owner = None
        self._stop_owner = None
        self._completion_notification = None
        self._hidden_star_draft = None
        self._busy = False
        self._settings_dirty = False
        self._loading_settings = False
        self._refresh_pending = False
        with ui.column().classes('pv-scheduler-view w-full gap-3') as self.root:
            ui.label('お気に入り作者の定期チェック').classes('text-h6')
            ui.label('新しい専用管理フォルダーにだけ保存します。旧DB、画像、Cookieは更新しません。')
            with ui.row().classes('items-center gap-3 flex-wrap'):
                self.open_button = ui.button('管理スケジューラを開く', on_click=self.open).props('data-testid=scheduler-open')
                self.confirm_account_button = ui.button('定期チェックの認証を確認', on_click=self.confirm_account).props(
                    'data-testid=scheduler-confirm-account')
                self.confirm_account_button.set_visibility(False)
                self.interval = ui.select({'0': '自動チェックしない', '6': '6時間', '12': '12時間',
                                           '24': '24時間', '48': '48時間'}, value='24',
                                          label='自動チェック間隔').style('min-width:184px').props('data-testid=scheduler-interval')
                self.startup = ui.checkbox('起動時にもチェック').props('data-testid=scheduler-startup')
            ui.label('☆お気に入りにした作者全員をチェックします。').classes('pv-subtitle')
            self.account_label = ui.label('紐づけアカウントを確認できません').classes('pv-subtitle').props(
                'data-testid=scheduler-account')
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
            status = ('未起動' if getattr(services, 'managed_scheduler', None) is None else '状態を確認中')
            self.status = ui.label(f'定期状態: {status}').props('data-testid=scheduler-status')
            self.history_label = ui.label('実行履歴はありません').classes('text-caption').props('data-testid=scheduler-history')
            self.star_notice = ui.label('').classes('text-caption').props('data-testid=scheduler-star-import role=status')
            self.action_notice = ui.label('').props('data-testid=scheduler-action-notice role=status')
        self._set_controls(False)
        self.open_button.set_enabled(getattr(services, 'managed_scheduler', None) is None)
        self._refreshing = False
        self._background_refresh_tasks: set[asyncio.Task] = set()
        self._timer = ui.timer(5.0, self.refresh)
        self._unsubscribe_owner = None
        self._subscribe_to_owner()
        ui.context.client.on_delete(self.close)

    def _subscribe_to_owner(self):
        store = getattr(self.services, 'following_sync', None)
        if store is None:
            return
        def invalidate(epoch):
            owner = self._settings_owner
            if not self.closed and owner is not None and owner.epoch < epoch:
                self._hide_account_data()

        self._unsubscribe_owner = subscribe_owner_on_loop(store, invalidate)

    def _set_controls(self, enabled):
        for widget in (self.interval, self.startup, self.authors, self.save_button, self.discard_button,
                       self.run_button, self.refresh_button, self.ack_button):
            widget.set_enabled(enabled)

    def _mark_settings_dirty(self):
        if not self._loading_settings:
            self._settings_dirty = True
            self._enable_save()

    def _enable_save(self):
        ledger = self.services.managed_scheduler
        if ledger is None or self.revision is None:
            return
        usable = (not getattr(ledger, '_account_guard', False)
                  or self._settings_owner is not None or self._can_save_global_off())
        self.save_button.set_enabled(usable and not self._busy)

    def _can_save_global_off(self):
        if str(self.interval.value) != '0' or self.startup.value:
            return False
        if self._settings_account is None:
            return True
        if self._stop_owner is None:
            return False
        try:
            self._stop_owner.verify_stop_allowed()
            return True
        except (OSError, RuntimeError, sqlite3.Error):
            return False

    def _finish_action(self):
        self._busy = False
        if self.closed:
            return
        ledger = self.services.managed_scheduler
        self._enable_save()
        if ledger is None or self.revision is None:
            return
        if not getattr(ledger, '_account_guard', False):
            self.run_button.set_enabled(True)
        elif self._settings_owner is not None:
            self._show_account(ledger)

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
            self._finish_action()

    def _load(self, ledger):
        guarded = getattr(ledger, '_account_guard', False)
        account_id = self._settings_account
        if not self._settings_dirty:
            try:
                account_id = ledger.favourite_account()
            except (OSError, RuntimeError, sqlite3.Error):
                account_id = None
        editor = self._editor(ledger, account_id) if guarded else None
        state = editor.state if editor is not None else ledger.snapshot()
        if guarded and editor is None and not self._settings_dirty:
            # A new unverified editor has only global controls, including OFF.
            account_id = None
        if not self._settings_dirty:
            # The revision belongs to the editable form, even during a poll.
            self.revision = state.revision
            self._stop_owner = editor.owner if editor is not None else None
            self._hidden_star_draft = None
            self._loading_settings = True
            try:
                self.interval.set_value(str(state.interval_hours))
                self.startup.set_value(state.startup_check)
                if self._settings_account != account_id:
                    self.action_notice.set_text('')
                    self._hide_history()
                self._settings_account = account_id
                authors = editor.authors if editor is not None else (() if guarded else ledger.favourites(account_id))
                self.authors.set_value(','.join(authors))
                self._loaded_authors = self.authors.value or ''
                self._stars_unknown_in_form = guarded and editor is None
            finally:
                self._loading_settings = False
        self.star_notice.set_text(
            '☆の一覧を読み込めません。読み込めるまで、☆の設定は保存できません（保留中の取得は消しません）。'
            if self._stars_unknown_in_form else '')
        if guarded and not self._show_editor(editor):
            return
        owns_history = editor is None or editor.owns_history
        self.ack_button.set_enabled(owns_history and state.last_status == 'interrupted')
        if not owns_history and (not editor.unbound or state.last_status != 'never'):
            self.status.set_text('定期状態: 実行履歴の紐づけを確認できません')
            return
        status_label = STATUS_LABELS.get(state.last_status, '状態不明')
        self.status.set_text(f'定期状態: {status_label} / 次回UTC: {state.next_due_utc or "未設定"}')

    def _editor(self, ledger, account_id):
        try:
            if self._cookie_restart_required():
                return None
            owner = ledger.capture_owner(self.services, account_id=account_id)
            return ledger.read_editor(owner=owner)
        except (OSError, RuntimeError, sqlite3.Error):
            return None

    def _cookie_restart_required(self):
        check = getattr(self.services, 'scheduler_cookie_restart_required', None)
        return check is not None and check()

    def _confirmation_blocker(self):
        check = getattr(self.services, 'scheduler_account_confirmation_blocker', None)
        if check is not None:
            return check()
        if self._cookie_restart_required():
            return 'restart'
        if getattr(self.services, '_scheduler_waiting_for_auth', False):
            return 'waiting'
        return None

    async def confirm_account(self):
        confirm = getattr(self.services, 'confirm_scheduler_account', None)
        if self.closed or self._busy or confirm is None:
            return
        if self._confirmation_blocker() is not None:
            if self._hide_account_data() == 'busy':
                self.action_notice.set_text(CONFIRMATION_STATUS['busy'])
            return
        self._busy = True
        self.confirm_account_button.set_enabled(False)
        try:
            await asyncio.to_thread(confirm)
            if not self.closed:
                await self.refresh(ensure_after_current=True)
        except Exception as error:
            if not self.closed:
                blocker = self._hide_account_data()
                busy = isinstance(error, ScheduleConfirmationUnavailable) and error.reason == 'busy'
                if blocker != 'restart' and (not busy or blocker == 'busy'):
                    self.action_notice.set_text(check_failure_message(error))
        finally:
            self._finish_action()
            if not self.closed:
                self.confirm_account_button.set_enabled(self.confirm_account_button.visible)

    def _verify_result(self, owner):
        if owner is None or owner != self._settings_owner or self._cookie_restart_required():
            raise AccountMismatch()
        owner.verify_current()

    def _dismiss_completion(self):
        notice, self._completion_notification = self._completion_notification, None
        if notice is not None and not notice.is_deleted:
            notice.dismiss()

    def _hide_history(self):
        self._dismiss_completion()
        self.history_label.set_text('')
        self.history_label.set_visibility(False)

    def _hide_account_data(self):
        self._settings_owner = None
        if self.authors.visible or self.history_label.visible:
            self.action_notice.set_text('')
        if self._settings_dirty and self.authors.visible and self._settings_account is not None:
            self._hidden_star_draft = (self._settings_account, self.authors.value, self._loaded_authors,
                                       self._stars_unknown_in_form)
        loading = self._loading_settings
        self._loading_settings = True
        try:
            self.authors.set_value('')
        finally:
            self._loading_settings = loading
        self.authors.set_visibility(False)
        self._loaded_authors = ''
        self._stars_unknown_in_form = True
        self.star_notice.set_text('☆一覧を読み込めません。ログイン中のアカウントと保存済みのフォロー情報を照合できるまで、履歴も非表示になります。')
        self.account_label.set_text('紐づけアカウントを確認できません')
        blocker = self._confirmation_blocker()
        if blocker != 'busy' and self.action_notice.text == CONFIRMATION_STATUS['busy']:
            self.action_notice.set_text('')
        show_confirm = blocker is None and callable(getattr(self.services, 'confirm_scheduler_account', None))
        self.confirm_account_button.set_visibility(show_confirm)
        self.confirm_account_button.set_enabled(show_confirm and not self._busy)
        status = CONFIRMATION_STATUS.get(blocker, '紐づけアカウントを確認できません')
        self.status.set_text(f'定期状態: {status}')
        self.ack_button.set_enabled(False)
        self.run_button.set_enabled(False)
        self._hide_history()
        self._enable_save()
        return blocker

    def _show_account(self, ledger):
        if getattr(ledger, '_account_guard', False):
            self._show_editor(self._editor(ledger, self._settings_account))

    def _show_editor(self, editor):
        if editor is None:
            self._hide_account_data()
            return False
        try:
            editor.owner.verify_current()
        except (OSError, RuntimeError, sqlite3.Error):
            self._hide_account_data()
            return False
        self._settings_owner = self._stop_owner = editor.owner
        if self.action_notice.text == CONFIRMATION_STATUS['busy']:
            self.action_notice.set_text('')
        self.confirm_account_button.set_visibility(False)
        account_id = editor.owner.account_id
        draft = self._hidden_star_draft
        if draft is not None and draft[0] == account_id:
            self._loading_settings = True
            try:
                self.authors.set_value(draft[1])
                self._loaded_authors, self._stars_unknown_in_form = draft[2:]
                self._hidden_star_draft = None
                if not self._stars_unknown_in_form:
                    self.star_notice.set_text('')
            finally:
                self._loading_settings = False
        self.authors.set_visibility(True)
        if not editor.owns_history:
            self._hide_history()
            self.ack_button.set_enabled(False)
        self.run_button.set_enabled(not self._busy and (editor.owns_history or editor.unbound))
        self._enable_save()
        if not (editor.owns_history or editor.unbound):
            self.star_notice.set_text('定期チェックの履歴は別のアカウントに紐づいているか、作成元を確認できません。履歴を保持しています。')
            self.account_label.set_text('紐づけアカウントを確認できません')
        elif self._stars_unknown_in_form:
            self.account_label.set_text('紐づけアカウントを確認できません')
        elif editor.unbound:
            self.account_label.set_text(f'対象アカウント ID {account_id}（未紐づけ）')
        else:
            self.account_label.set_text(f'紐づけアカウント ID {account_id}')
        return True

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
            owner, revision = self._settings_owner, self.revision

            def inspect():
                # Never silently requeue an ambiguous accepted job; the
                # existing JobService owns reconciliation and receipt checks.
                with self.services._scheduler_dispatch_lock:
                    if any(job.state in UNFINISHED for job in self.services.jobs.snapshot()):
                        raise RuntimeError('unfinished managed jobs must be inspected first')
                    return self.services.managed_schedule_runner.acknowledge_interrupted(
                        expected_revision=revision, owner=owner)
            saved = await asyncio.to_thread(inspect)
            self.services.managed_schedule_runner.settings_changed()
            if self.closed:
                return
            if getattr(ledger, '_account_guard', False):
                self._verify_result(owner)
            self.revision = saved.revision
            self._load(ledger)
        except (ScheduleAccountError, AccountMismatch):
            if not self.closed:
                self._show_account(ledger)
                self.action_notice.set_text('同期先を確認して、定期チェック画面を再読込してください。')
        except Exception:
            if not self.closed:
                self._show_account(ledger)
                self.status.set_text('未完了ジョブまたは設定更新があり中断状態を解除できません。キューを確認してください')
        finally:
            self._finish_action()

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
            self._show_account(self.services.managed_scheduler)
            await asyncio.to_thread(self.services.reconcile_managed_scheduler)
            if self.closed:
                return
            ledger = self.services.managed_scheduler
            if ledger is None:
                return
            self.open_button.set_enabled(False)
            if not self.interval.enabled:
                self._set_controls(True)
            self._load(ledger)
            owner = self._settings_owner
            guarded = getattr(ledger, '_account_guard', False)

            def read_activity():
                if not guarded:
                    return None, ledger.history(), ledger.claim_pending_notifications(owner=None)
                history = ledger.history_for(owner=owner)
                pending = () if history.rows is None else ledger.claim_pending_notifications(owner=history.owner)
                return history.owner, history.rows, pending

            try:
                result_owner, history, pending = await asyncio.to_thread(read_activity)
                if self.closed:
                    return
                if self.services.managed_scheduler is not ledger:
                    self._hide_account_data()
                    return
                if guarded:
                    self._verify_result(result_owner)
                    if history is None:
                        self._hide_history()
                        return
            except (ScheduleAccountError, AccountMismatch):
                if not self.closed:
                    self._show_account(ledger)
                    self._hide_history()
                return
            self.history_label.set_text('実行履歴はありません')
            self.history_label.set_visibility(True)
            notification_error = False
            if pending:
                # _schedule_refresh creates a fresh asyncio task whose
                # NiceGUI slot stack is empty. Restore this view's client
                # context before using ui.notify; otherwise a claimed durable
                # notification can be lost and the whole refresh misreported.
                try:
                    with self.root:
                        if guarded:
                            self._dismiss_completion()
                            self._completion_notification = ui.notification(f'保存完了: {len(pending)}件', type='positive')
                        else:
                            ui.notify(f'保存完了: {len(pending)}件', type='positive')
                except Exception:
                    # Delivery is deliberately at-most-once; if this client is
                    # disappearing, keep durable history readable instead of
                    # aborting the entire refresh with a misleading error.
                    notification_error = True
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
                self._hide_account_data()
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
            guarded = getattr(ledger, '_account_guard', False)
            owner = self._settings_owner
            proof = owner if guarded else account_id
            if str(form[1]) == '0' and not form[2]:
                if guarded and not self._can_save_global_off():
                    raise ScheduleAccountError('mismatch')
                stop_owner = self._stop_owner if guarded else proof
                authors_edited = form[0] != getattr(self, '_loaded_authors', '')
                saved = await asyncio.to_thread(ledger._disable, expected_revision=revision,
                                                account_id=stop_owner)
                stop_saved = True
                runner = getattr(self.services, 'managed_schedule_runner', None)
                if runner is not None:
                    runner.settings_changed()
                if self.closed:
                    return
                if guarded and stop_owner is not None:
                    stop_owner.verify_stop_allowed()
                self.revision = revision = saved.revision
                if not authors_edited:
                    self._settings_dirty = (self.authors.value or '', self.interval.value, self.startup.value) != form
                    self._load(ledger)
                    return
                if self._stars_unknown_in_form or (guarded and owner is None):
                    self.action_notice.set_text('停止を保存しました。☆を変更するには再読込してください。')
                    return
            if guarded:
                self._verify_result(owner)
            else:
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
                                            account_id=proof), (), ()
                return ledger.configure_all(author_ids=raw, interval_hours=int(form[1]),
                                            startup_check=bool(form[2]), expected_revision=revision,
                                            now=datetime.now(timezone.utc), return_ignored=True,
                                            account_id=proof, report_retained=True)
            saved, ignored, retained = await asyncio.to_thread(update)
            runner = getattr(self.services, 'managed_schedule_runner', None)
            if runner is not None:
                runner.settings_changed()
            # Edits made while the worker saved the captured form still belong
            # to the user. Keep them and base the next CAS on this commit.
            self._settings_dirty = (self.authors.value or '', self.interval.value, self.startup.value) != form
            self.revision = saved.revision
            if self.closed:
                return
            if guarded:
                self._verify_result(owner)
            self._load(ledger)
            if guarded:
                self._verify_result(owner)
            notices = []
            if ignored:
                notices.append('フォローしていないため保存しなかったID: ' + ', '.join(ignored))
            if retained:
                notices.append('フォローが解除された作者の☆と保留を保持しました。ID ' + ', '.join(retained))
            if notices:
                self.action_notice.set_text(' / '.join(notices))
        except (ScheduleAccountError, AccountMismatch):
            if not self.closed:
                self._show_account(ledger)
            notice = '同期先アカウントが変わりました。「編集を破棄して再読込」してから保存してください'
            if account_id is None and form[0].strip():
                notice = '☆の一覧を読めないため入力したIDは保存できません。復旧後に再読込してください'
            self.action_notice.set_text(('停止を保存しました。' if stop_saved else '') + notice)
        except Exception:
            notice = '設定を保存できませんでした。別画面の更新や入力を確認してください'
            self.status.set_text(('停止を保存しました。' if stop_saved else '') + notice)
        finally:
            self._finish_action()

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
            guarded = getattr(ledger, '_account_guard', False)
            owner = self._settings_owner
            if guarded:
                self._verify_result(owner)

            def submit():
                with self.services._scheduler_dispatch_lock:
                    if owner is not None:
                        editor = ledger.read_editor(owner=owner)
                        if not (editor.owns_history or editor.unbound):
                            raise ScheduleAccountError('mismatch')
                    dispatch = getattr(self.services, 'dispatch_author_check', None)
                    displayed = {} if owner is None else {'displayed_owner': owner}
                    if dispatch is not None:
                        return dispatch(ledger, slot, self.services._scheduler_cancel_event, wait=False, **displayed)
                    source = getattr(self.services, 'scheduler_favourite_authors', None)
                    authors = source() if source is not None else ledger.favourites()
                    if source is not None:
                        ledger._sync_favourites(authors)
                    return submit_author_batch(self.services, authors,
                                               cancellation=self.services._scheduler_cancel_event,
                                               ledger=ledger, slot=slot, **displayed)
            result = await asyncio.to_thread(submit)
            if self.closed:
                return
            if guarded:
                self._verify_result(owner)
            if result.get('started'):
                self.action_notice.set_text(f'☆作者 {result["authors"]}人のチェックを始めました。進捗はダウンロード画面で確認できます')
            elif not self.closed:
                self.action_notice.set_text(
                    f'受付 {result["accepted"]}件、保留 {result["deferred"]}件。保存結果はキューで確認してください')
            if not self.closed and not result.get('started'):
                self.history_label.set_visibility(True)
                self.history_label.set_text(
                    f'今回のチェック: 受付 {result["accepted"]}件 / 保留 {result["deferred"]}件 / 保存結果を確認中')
                # Show admission immediately and release the form. Receipt
                # reconciliation can be much slower than enqueue (e.g. a
                # large deferred batch); do not keep Save/Run disabled while
                # the UI only needs a background history refresh.
                self._schedule_refresh()
        except Exception as error:
            if not self.closed:
                self._show_account(ledger)
                self.action_notice.set_text(check_failure_message(error))
        finally:
            self._finish_action()

    def close(self):
        self.closed = True
        self._timer.cancel()
        self._dismiss_completion()
        if self._unsubscribe_owner is not None:
            self._unsubscribe_owner()
            self._unsubscribe_owner = None


def check_failure_message(error):
    # Never display raw exception text: it may contain paths or credentials.
    if isinstance(error, LegacyAuthorSourceError):
        return '旧DBまたは画像の保存元を確認できません。停止した旧DBのコピーと保存元を確認してください'
    from ..managed_network_stop_gate import ManagedNetworkStopped, ManagedNetworkGateBusy, ManagedNetworkGateStorageError
    from ..pixiv_read_client import HTTPStatusRejected, AuthenticationMissing
    if isinstance(error, ScheduleConfirmationUnavailable):
        return CONFIRMATION_STATUS.get(error.reason, '定期チェックの認証を確認できません。')
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
