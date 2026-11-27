"""Explicit, local-only acknowledgement of an interrupted shared GET."""
import asyncio
from nicegui import ui

from .management_dialog import managed_dialog

from .action_controls import danger_button, dialog_actions, dialog_card, secondary_button
from ..managed_network_stop_gate import (
    ManagedNetworkGateBusy, ManagedNetworkStopped,
)
from ..managed_network_stop_shared_scope import ManagedUserNetworkStopScope

UNCERTAIN_MESSAGE = ('前の取得の結果が不確かなため、新しい取得を停止しています。'
                     '画面上部の「取得停止の確認・回復」を開き、回復手順を確認してください。')


def shared_get_scope(services):
    scope = getattr(services, 'managed_network_stop', None)
    scope = getattr(scope, 'shared_owner', scope)
    return scope if type(scope) is ManagedUserNetworkStopScope else None


class SharedGetRecoveryView:
    def __init__(self, scope):
        self.scope = scope
        self.closed = self.busy = False
        self.plan = None
        self.entry = secondary_button('取得停止の確認・回復', self.open, testid='shared-get-recovery-open')
        self.summary = ui.label('').classes('pv-alert-banner').props('role=alert data-testid=shared-get-recovery-summary')
        self.summary.set_visibility(False)
        with managed_dialog(return_focus=self.entry).props('persistent no-shake data-testid=shared-get-recovery-dialog') as self.dialog:

            self.dialog.on('keydown.esc.stop', self.cancel)
            with dialog_card(size='lg', classes='pv-settings-modal'):
                title = ui.label('前の取得が中断された場合の回復').classes('pv-dialog-title').props(
                    'role=heading aria-level=1 tabindex=-1 data-autofocus')
                self.dialog.props['aria-labelledby'] = title.html_id
                self.notice = ui.label('まだ確認していません。').classes('pv-subtitle').props(
                    'role=status data-testid=shared-get-recovery-notice')
                ui.label('前の送信結果は回復後も不明です。他のPixivVaultで取得が動いていないことを確認してください。'
                         '回復は送信中の記録を退避するだけで、記録済みのHTTP 401・403・429によるSTOPは解除しません。'
                         '今回の429の設定待機・再試行や、試行上限による起動中の停止も、この操作では解除しません。'
                         '自動再送は行いません。回復後に元の画面で取得をやり直してください。').classes('pv-subtitle')
                self.acknowledge = ui.checkbox('他の取得処理が動いていないことと、前の結果が不明なままであることを確認しました',
                    on_change=lambda _: self.sync()).props('dense data-testid=shared-get-recovery-ack').classes('pv-hit28')
                with dialog_actions():
                    self.cancel_button = secondary_button('閉じる', self.cancel, testid='shared-get-recovery-cancel')
                    self.confirm_button = danger_button('確認して取得待ちの記録を退避する', self.recover,
                                                        testid='shared-get-recovery-confirm')
        self.sync()
        self._initial_check = ui.timer(.1, self.inspect_summary, once=True)

    async def inspect_summary(self):
        if self.closed:
            return
        try:
            plan = await asyncio.to_thread(self.scope.preview_uncertain_get_recovery)
            message = UNCERTAIN_MESSAGE if plan is not None else ''
        except ManagedNetworkGateBusy:
            return  # A running GET is not evidence of an abandoned intent.
        except Exception as error:
            message = self.failure_message(error)
        if not self.closed:
            self.summary.set_text(message)
            self.summary.set_visibility(bool(message))

    def sync(self):
        self.entry.set_enabled(not self.closed and not self.busy)
        self.acknowledge.set_enabled(not self.closed and not self.busy and self.plan is not None)
        self.confirm_button.set_enabled(not self.closed and not self.busy
                                        and self.plan is not None and bool(self.acknowledge.value))
        self.cancel_button.set_enabled(not self.busy)

    @staticmethod
    def failure_message(error):
        if isinstance(error, ManagedNetworkStopped):
            reason = {429: 'アクセス制限', 401: '認証エラー', 403: 'アクセス拒否'}.get(error.status_code, '通信エラー')
            message = f'過去のHTTP {error.status_code}（{reason}）によるSTOPが記録されています。この回復では解除できません。'
            if error.status_code == 429:
                message += ('今回の429の設定待機・再試行とは別の永続停止履歴で、再起動しても残ります。'
                            '停止履歴・所有情報を削除せず、確認を依頼してください。'
                            '保存元の正式移行が必要な場合は、起動時の「停止履歴の確認と移行手順を開く」で確認してください。'
                            '正式移行でもSTOPは維持されます。')
            return message
        if isinstance(error, ManagedNetworkGateBusy):
            return '取得処理または別の画面が通信記録を使用中です。処理が終わってから再度確認してください。'
        return '通信記録または所有者を確認できません。回復は実行せず停止を維持します。保存先を変更せず確認を依頼してください。'

    async def open(self):
        if self.closed or self.busy:
            return
        self.plan = None
        self.acknowledge.set_value(False)
        self.notice.set_text('送信中の記録を確認しています（通信なし）。')
        self.dialog.open()
        self.busy = True
        self.sync()
        try:
            plan = await asyncio.to_thread(self.scope.preview_uncertain_get_recovery)
            message = UNCERTAIN_MESSAGE if plan is not None else '中断された取得の記録はありません。回復操作は不要です。'
        except Exception as error:
            plan, message = None, self.failure_message(error)
        self.busy = False
        if not self.closed:
            self.plan = plan
            self.notice.set_text(message)
            self.sync()

    async def recover(self):
        if self.closed or self.busy or self.plan is None or not self.acknowledge.value:
            return
        plan = self.plan
        self.busy = True
        self.sync()
        try:
            await asyncio.to_thread(self.scope.recover_uncertain_get, plan, confirmed=True)
            message = ('送信中の記録を退避しました。認証エラーによる停止は続いています。'
                       '設定で「選択したCookieの認証を確認」を押してください。'
                       '起動設定の確認画面を開いている場合は、アプリを起動し直してください。'
                       if getattr(plan, 'stop_status', None) == 401 else
                       '送信中の記録を退避しました。元の画面で取得をやり直してください（自動再送はしていません）。')
        except Exception as error:
            message = self.failure_message(error)
        self.busy = False
        if not self.closed:
            self.plan = None
            self.acknowledge.set_value(False)
            self.notice.set_text(message)
            await self.inspect_summary()
            self.sync()

    def cancel(self):
        if self.busy:
            return
        self.plan = None
        self.acknowledge.set_value(False)
        self.dialog.close()
        self.sync()

    def close(self):
        self.closed = True
        self.plan = None
        self._initial_check.deactivate()
        self.dialog.close()
