"""Recovery from an unconfirmed Pixiv result, reachable from the borrow popup and Settings.

The unconfirmed state (oauth_send_pending) used to be left only through the removed
"Shaft連携" tab. These are the same explicit, local, password-checked operations:
re-read the saved state, restore a recorded update result, and update the account.
"""
import asyncio
from nicegui import ui
from .action_controls import outlined_input, primary_button, secondary_button
from ..external_vault import VaultError, VAULT_INTEGRITY_ERRORS


class PremiumRecoveryPanel:
    def __init__(self, vault, *, gate=None, on_change=None):
        self.vault = vault
        self.gate = gate
        self.on_change = on_change
        self.busy = False
        self.closed = False
        with ui.column().classes('w-full gap-2').props('data-testid=premium-recovery') as self.root:
            self.guide = ui.label('').classes('pv-subtitle').props('role=status data-testid=premium-recovery-guide')
            self.password = outlined_input('保管庫のパスワード', password=True).classes('w-full').props(
                'autocomplete=off input-class=pv-focus-ring data-testid=premium-recovery-password')
            with ui.row().classes('pv-toolbar pv-compact-toolbar'):
                self.reopen_button = primary_button(
                    '保存済み状態を再確認（通信なし）', lambda: self.action('reopen_saved'), testid='premium-recovery-reopen')
                self.recover_button = secondary_button(
                    '記録済み更新結果を復元（通信なし）', lambda: self.action('recover_known'), testid='premium-recovery-recover')
                self.refresh_button = secondary_button(
                    'アカウントを更新', lambda: self.action('refresh'), testid='premium-recovery-refresh')
            self.result = ui.label('').classes('pv-subtitle pv-compact-status').props('role=status data-testid=premium-recovery-result')
        try:
            ui.context.client.on_delete(self.close)
        except Exception:
            pass
        self.render()

    def close(self):
        self.closed = True

    def unconfirmed(self):
        try:
            return bool(self.vault.status().get('oauth_review_required'))
        except Exception:
            return False

    def render(self):
        """Buttons follow the vault state; the original disabled the update while unconfirmed."""
        if self.closed:
            return
        try:
            state = self.vault.status()
        except Exception:
            state = {}
        review = bool(state.get('oauth_review_required'))
        self.guide.set_text(
            '更新結果が未確認のため、借用の通信を止めています。保管されているデータには変更を加えていません。'
            '保存済みの状態を確認して、続けて使えるか判定します。' if review else
            '未確認の更新結果はありません。必要なときだけアカウントを更新できます。')
        self.reopen_button.set_enabled(not self.busy and bool(state.get('initialized')))
        self.recover_button.set_enabled(not self.busy and review)
        self.refresh_button.set_enabled(not self.busy and bool(state.get('open')) and not review
                                        and not state.get('socket_shutdown_unverified'))

    async def action(self, kind):
        if self.closed or self.busy:
            return
        password = str(self.password.value or '')
        self.password.set_value('')
        if kind in ('reopen_saved', 'recover_known') and not password:
            self.result.set_text('保管庫のパスワードを入力してください。')
            return
        self.busy = True
        self.render()
        self.result.set_text('処理中…')
        error = None
        try:
            if kind == 'reopen_saved':
                await asyncio.to_thread(self.vault.reopen_saved_state, password)
            elif kind == 'recover_known':
                await asyncio.to_thread(self.vault.recover_known_result, password)
            elif kind == 'refresh':
                await asyncio.to_thread(self.vault.refresh)
            else:
                raise ValueError('invalid action')
        except Exception as exc:
            error = (exc.code if isinstance(exc, VaultError) else
                     'vault-permission-denied' if isinstance(exc, PermissionError) else
                     'vault-io-error' if isinstance(exc, OSError) else 'operation-failed')
            if error in VAULT_INTEGRITY_ERRORS and self.gate is not None:
                try:
                    await asyncio.to_thread(self.gate.disable)
                except Exception:
                    pass
        finally:
            password = ''
            self.busy = False
            await asyncio.to_thread(self._settle, kind)
            if not self.closed:
                self.render()
        if self.closed:
            return
        from .premium_gate_view import vault_unlock_error_message  # lazy: avoids an import cycle
        review = self.unconfirmed()
        if error:
            if kind == 'recover_known':
                text = '検証済みの記録がないため復元できません。停止と履歴を維持しています。'
            else:
                text = vault_unlock_error_message(error) or '操作を完了できませんでした。'
            self.result.set_text(text)
        elif kind == 'reopen_saved':
            self.result.set_text('保存済み状態を読み直しました。通信や自動更新は開始していません。' + (
                '保存済みの更新結果は未確認のままです。' if review else
                '未確認の更新はありません。借用を再開するには、借用プレミアムを一度OFFにして、もう一度ONにしてください。'))
        elif kind == 'recover_known':
            self.result.set_text('記録済みの更新結果を復元しました。通信・自動更新は開始していません。共通STOPは解除していません。'
                                 + ('' if review else '借用を再開するには、借用プレミアムを一度OFFにして、もう一度ONにしてください。'))
        else:
            self.result.set_text('アカウントを更新しました。')
        if self.on_change:
            self.on_change()

    def _settle(self, kind):
        """Keep the vault in the state the permission says (blocking; runs in a thread)."""
        if self.gate is None:
            return
        try:
            enabled = self.gate.status()['enabled']
            opened = self.vault.status()['open']
            if enabled and not opened:
                # reopen/recover lock first; a failed password must not leave ON with a locked vault.
                self.gate.restore_vault()
            elif not enabled and opened and kind != 'refresh':
                self.vault.lock()  # OFF means locked
        except Exception:
            pass
