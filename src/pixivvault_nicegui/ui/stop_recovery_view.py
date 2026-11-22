"""Offline stop-history guidance and explicit verified migration entry."""
import asyncio
from nicegui import ui

from .action_controls import dialog_actions, dialog_card, primary_button, secondary_button
from ..managed_network_stop_gate import (
    ManagedNetworkGateBusy, ManagedNetworkGateStorageError, ManagedNetworkStopped,
)
from ..managed_network_stop_shared_scope import SharedGetOutcomeUncertain, SharedStopOwnerAbsent
from ..native_auth_result import NativeAuthResult
from ..native_profile import NativeProfileError

STEPS = (
    '1. 保存先の設定を確認し、以前使った保存元も控えてください。元DB・Cookie・停止履歴を削除しないでください。',
    '2. 下の確認ボタンで、現在の設定にある停止履歴を読み取ります。認証不要・通信なしで使えます。確認はSTOP解除や移行の証明ではありません。',
    '3. 保存元の正式移行が必要な場合は、「正式移行を実行する」で以前のすべての保存元を追加し、内容を確認してから明示的に開始してください。アプリがロック・コピー・照合を行います。正式移行は履歴と所有情報を引き継ぐ操作で、STOP解除ではありません。履歴や所有ファイルを自分で作り直さないでください。',
    '4. 正式移行が完了したらアプリを終了し、起動し直してください。起動時の検証が通るまで認証・取得は無効のままです。記録済みのSTOPは移行・再起動後も維持されます。STOPが記録されている場合は、元データを保持して確認を依頼してください。',
)

RATE_LIMIT_GUIDANCE = (
    '過去のHTTP 429による永続STOP履歴と、今回の429の設定待機・再試行は別です。'
    '現在のGET再試行ポリシーが扱う429は、設定した総試行回数（初回を含む）と待機時間に従い、共有STOP履歴には記録しません。'
    '上限に達すると、同じポリシーを共有する取得をこの起動中は停止します。'
    '再起動後に取得を明示的にやり直せますが、過去の永続STOP履歴は残ります。自動解除・自動再送は行いません。'
)


def build_startup_stop_recheck(binding, open_scope, notice, view):
    """Re-open the startup fence locally and restore only the manual auth check."""
    client = ui.context.client

    def profile_changed():
        return binding.store.snapshot().revision != binding.launch_snapshot.revision

    def probe():
        scope = None
        try:
            if profile_changed():
                return None, 'profile_changed'
            scope = open_scope()
            # This preview checks both the STOP row and the in-flight marker
            # under the ordinary GET lock. A running GET reports Busy.
            if scope.preview_uncertain_get_recovery() is not None:
                return scope, 'uncertain'
            if profile_changed():
                return None, 'profile_changed'
        except ManagedNetworkGateBusy:
            return None, 'busy'
        except ManagedNetworkStopped:
            return None, 'stopped'
        except SharedGetOutcomeUncertain:
            return scope, 'uncertain'
        except SharedStopOwnerAbsent:
            return None, 'owner_absent'
        except (ManagedNetworkGateStorageError, NativeProfileError, OSError):
            return None, 'local_unavailable'
        return scope, 'stop_rechecked'

    async def verify_auth(cookie_file):
        result = await binding.check_authentication(cookie_file, _publish=False)
        if result.code == 'profile_changed':
            view.set_auth_verifier(None)
            notice.set_text(result.message)
        return result

    async def check():
        if not button.enabled:
            return
        button.disable()
        notice.set_text('通信記録を確認しています。')
        scope, code = await asyncio.to_thread(probe)
        if button.is_deleted or client.is_deleted:
            return
        notice.set_text(NativeAuthResult(code).message)
        if code == 'busy':
            button.enable()
        elif code == 'stop_rechecked':
            notice.classes(remove='pv-warning-banner', add='pv-subtitle')
            binding.managed_network_stop_scope.shared_owner = scope
            binding.shared_network_stop_scope = scope
            view.set_auth_verifier(verify_auth)
        elif code == 'uncertain' and scope is not None:
            from .shared_get_recovery_view import SharedGetRecoveryView
            with actions:
                recovery = SharedGetRecoveryView(scope)
            client.on_delete(recovery.close)

    with ui.column().classes('w-full gap-2') as actions:
        button = secondary_button('再確認', check, icon='refresh', outline=True,
                                  testid='startup-stop-recheck')


class StopRecoveryPanel:
    def __init__(self, binding):
        self.binding, self.closed, self.busy = binding, False, False
        self.migration_view = None
        self.help_text = '\n'.join((RATE_LIMIT_GUIDANCE, *STEPS))
        with ui.column().classes('w-full gap-2') as self.root:
            self.entry = secondary_button('停止履歴の確認と移行手順を開く', self.open, testid='stop-recovery-open')
            with ui.dialog() as self.dialog, dialog_card('通信を再開する前の確認', size='lg'):
                ui.label(RATE_LIMIT_GUIDANCE).classes('pv-warning-banner').props(
                    'data-testid=stop-recovery-rate-limit-guidance')
                with ui.column().classes('w-full gap-1'):
                    for step in STEPS:
                        ui.label(step).classes('pv-subtitle')
                self.result = ui.label('まだ確認していません。').classes('pv-subtitle whitespace-pre-line').props(
                    'role=status data-testid=stop-recovery-result')
                with dialog_actions():
                    secondary_button('閉じる', self.dialog.close)
                    secondary_button('正式移行を実行する', self.open_migration,
                                     testid='stop-recovery-migration', outline=True)
                    self.check_button = primary_button('選択済み保存元の停止履歴を確認する', self.check,
                                                       testid='stop-recovery-check')
    def open(self):
        if not self.closed:
            self.dialog.open()
    def open_migration(self):
        if self.closed:return
        if self.migration_view is None:self.migration_view=self.binding.create_migration_view()
        self.migration_view.open()
    async def check(self):
        if self.closed or self.busy:
            return
        self.busy = True
        self.check_button.disable()
        try:
            observations = await asyncio.to_thread(self.binding.inspect_stop_history)
            names = {'native':'起動設定', 'images':'画像保存先', 'novel':'小説保存先', 'ugoira':'うごいら保存先', 'configured':'保存先設定'}
            lines = []
            for item in observations:
                owner = names.get(item.get('owner'), '保存元')
                state = item.get('state')
                if state == 'observed_stopped' and item.get('status_code') in (401,403,429):
                    value = f"停止履歴あり（HTTP {item['status_code']}）"
                    if item['status_code'] == 429:
                        value += '。過去の永続STOP履歴です。今回の429の再試行待機とは別で、正式移行・再起動でも解除されません'
                elif state == 'observed_unblocked':
                    value = 'この保存元の読取時点ではSTOPなし'
                else:
                    value = '履歴を確認できません'
                lines.append(owner + ': ' + value)
            message = ' / '.join(lines) + '。以前の全保存元を網羅した確認ではありません。通信停止・認証無効を維持しています。'
        except Exception:
            message = '停止履歴を確認できません。元データを保持し、通信を停止したまま正式な移行の確認を依頼してください。'
        finally:
            self.busy = False
        if not self.closed:
            self.result.set_text(message)
            self.check_button.enable()
    def close(self):
        self.closed = True
        if self.migration_view is not None:self.migration_view.close()

