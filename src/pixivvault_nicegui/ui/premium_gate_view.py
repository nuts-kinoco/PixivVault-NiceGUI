"""One activation-password input, followed by a separate persistence choice."""
import asyncio
from nicegui import ui
from ..external_vault import VaultError, VAULT_INTEGRITY_ERRORS

def vault_unlock_error_message(code):
    if code in ('invalid-generation', 'invalid-vault'):
        return '保管庫の構造を確認できません。ファイルを保持し、保管状態を確認してください。'
    if code == 'invalid-ciphertext':
        return '鍵ファイルの復号を確認できません。保管庫のファイルを保持してください。'
    if code == 'vault-permission-denied':
        return '保管庫へのアクセス権限を確認できません。ファイルを保持してください。'
    if code == 'vault-io-error':
        return '保管庫の読み取りでI/Oエラーが発生しました。ファイルを保持してください。'
    if code in ('unlock-failed','premium-password-rejected'):
        return 'パスワードが一致しません。入力を確認して、もう一度試してください。'
    return None

class PremiumGateDialog:
    def __init__(self,gate,*,on_change=None):
        self.gate=gate;self.on_change=on_change;self.closed=False
        with ui.dialog().props('persistent') as self.password_dialog:
            with ui.card().classes('w-full max-w-md gap-4'):
                ui.label('借用プレミアムをONにする').classes('text-h6')
                ui.label('パスワードを確認してから有効にします。入力内容は保存しません。')
                with ui.column().classes('w-full') as self.password_slot:
                    self.password=None
                    self._reset_password()
                with ui.row().classes('w-full justify-end'):
                    ui.button('キャンセル',on_click=lambda:self.password_dialog.submit(False)).props('flat')
                    ui.button('確認する',on_click=lambda:self.password_dialog.submit(True)).props('data-testid=premium-password-confirm')
        with ui.dialog().props('persistent') as self.save_dialog:
            with ui.card().classes('w-full max-w-md gap-4'):
                ui.label('有効な状態を保存しますか？').classes('text-h6')
                ui.label('保存すると次回もONで始まります。このWindowsユーザーだけが使えるよう、有効化の証跡を保護します。')
                with ui.row().classes('w-full justify-end'):
                    ui.button('保存しない',on_click=lambda:self.save_dialog.submit(False)).props('flat data-testid=premium-save-no')
                    ui.button('保存する',on_click=lambda:self.save_dialog.submit(True)).props('data-testid=premium-save-yes')
        self.notice=ui.label('').props('role=status data-testid=premium-permission-status')
        ui.context.client.on_delete(self.close)
    def _reset_password(self):
        # QDialog unmount can restore QInput's last inputValue on the client
        # after set_value('') was sent. A new element ID cannot inherit that
        # old client model, even when the server value was already empty.
        if self.password is not None:
            self.password.set_value('')
        self.password_slot.clear()
        with self.password_slot:
            self.password=ui.input('パスワード',password=True).props(
                'outlined autofocus autocomplete=off data-testid=premium-activation-password')
            self.password.on('keydown.enter',lambda:self.password_dialog.submit(True))
    async def request_enable(self):
        epoch=self.gate.begin_prompt()
        if epoch is None:return
        password=''
        try:
            state=self.gate.status()
            if state['retry_after']>0:
                self.notice.set_text(f"しばらく待ってから操作してください（あと{int(state['retry_after'])+1}秒）。")
                return
            self._reset_password()
            accepted=await self.password_dialog
            if not accepted or self.closed:return
            password=str(self.password.value or '');self._reset_password()
            await asyncio.to_thread(self.gate.authenticate,password,expected_epoch=epoch)
            password=''
            remember=await self.save_dialog
            if self.closed:return
            await asyncio.to_thread(self.gate.confirm,bool(remember),expected_epoch=epoch)
            ready=await asyncio.to_thread(self.gate.restore_vault)
            if ready and not self.gate.vault.status().get('oauth_review_required'):
                self.gate.vault.start_keeper()
            self.notice.set_text('借用プレミアムはONです。'+('次回起動後も維持します。' if remember else 'このセッション限りです。')+
                ('' if ready else 'Shaft連携で登録UIDなどを設定してください。'))
        except Exception as error:
            state=self.gate.status()
            message=vault_unlock_error_message(error.code if isinstance(error,VaultError) else None)
            self.notice.set_text((message or 'ONにできませんでした。入力または保管状態を確認してください。')+
                (f" あと{int(state['retry_after'])+1}秒待ってください。" if state['retry_after']>0 else ''))
        finally:
            password=''
            if not self.closed:self._reset_password()
            self.gate.end_prompt()
            if self.on_change and not self.closed:self.on_change()
    def close(self):
        if self.closed:return
        self.closed=True;self.password.set_value('')
        self.password_dialog.submit(False);self.save_dialog.submit(False)
