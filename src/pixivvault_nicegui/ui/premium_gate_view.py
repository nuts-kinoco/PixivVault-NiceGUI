"""One activation-password popup; ON is always kept for the next launch (original)."""
import asyncio
from pathlib import Path
from nicegui import ui
from .action_controls import dialog_actions, dialog_card, outlined_input, primary_button, secondary_button
from ..external_vault import VaultError, VAULT_INTEGRITY_ERRORS
from .premium_recovery_view import PremiumRecoveryPanel
from ..premium_uid_file import DEFAULT_UID_PATH, read_uid_file, uid_text_errors, apply_uid_file

def vault_unlock_error_message(code):
    if code == 'pixiv-outcome-uncertain':
        return ('前回の通信の結果を確認できていない登録があります。保管された状態を確認できるまで、借用プレミアムはOFFのままです。'
                '保管されているデータには変更を加えていません。')
    if code == 'premium-vault-password-rejected':
        return '保管庫のパスワード（以前に設定したもの）が一致しません。入力を確認して、もう一度試してください。'
    if code == 'premium-legacy-unlock-required':
        return '保管庫を開けませんでした。以前に設定した保管庫のパスワードを確認して、もう一度ONにしてください。'
    if code == 'premium-material-stale':
        return '保存済みの鍵が古くなっています。借用プレミアムを ON にしてパスワードを入力し直してください。'
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

def dialog_error_message(code):
    """In-dialog wording; the original says the password is wrong (gui.py:947)."""
    if code in ('premium-password-rejected','unlock-failed'):
        return 'パスワードが違います。入力を確認して、もう一度試してください。'
    return vault_unlock_error_message(code) or ''

class PremiumGateDialog:
    def __init__(self,gate,*,on_change=None, default_uid_path=DEFAULT_UID_PATH):
        self.gate=gate;self.on_change=on_change;self.closed=False
        self.default_uid_path=default_uid_path;self._uids=[];self._pick_epoch=0;self._uid_error=False
        self._uid_explicit=False
        with ui.dialog().props('persistent') as self.password_dialog:
            with dialog_card('借用プレミアムをONにする',
                             subtitle='パスワードを確認して有効にします。入力内容は保存しません。'):
                with ui.column().classes('w-full') as self.password_slot:
                    self.password=None
                    self._reset_password()
                # Wrong password and similar input errors stay here, under the
                # field, so the dialog and the other inputs are kept (original).
                self.dialog_error=ui.label('').classes('pv-alert-banner').props('role=alert data-testid=premium-dialog-error')
                with ui.column().classes('w-full gap-2') as self.legacy_box:
                    ui.label('保管庫は以前に設定した別のパスワードで保護されています。以前のパスワードを入力してください。').classes('pv-subtitle')
                    with ui.column().classes('w-full') as self.legacy_slot:
                        self.legacy=None  # the input exists only while it is needed
                self.legacy_box.set_visibility(False)
                with ui.column().classes('w-full gap-2') as self.main_box:
                    with ui.column().classes('w-full') as self.secret_slot:
                        self.secret=None
                        self._reset_secret()
                    self.uid_upload=ui.upload(on_upload=self.pick_uid_file,on_rejected=self.reject_uid_file,
                        multiple=False,max_file_size=65536,auto_upload=True,label='UID\u4e00\u89a7 txt \u3092\u9078\u629e').props('accept=.txt data-testid=premium-uid-upload').classes('pv-compact-upload')
                    self.uid_notice=ui.label('').classes('pv-field-hint pv-compact-status').props('role=status data-testid=premium-uid-count')
                # Leaving an unconfirmed Pixiv result (the old "Shaft連携" tab is gone).
                self.recovery_open=secondary_button('未確認の通信結果を確認する', self.open_recovery, testid='premium-recovery-open')
                with dialog_actions():
                    secondary_button('キャンセル', lambda:self.password_dialog.submit(False))
                    self.confirm_button=primary_button('有効にする', lambda:self.password_dialog.submit(True), testid='premium-password-confirm')
        with ui.dialog() as self.recovery_dialog:
            with dialog_card('通信結果の確認', size='lg'):
                self.recovery=PremiumRecoveryPanel(gate.vault,gate=gate,on_change=self._recovery_changed) if getattr(gate,'vault',None) is not None else None
                with dialog_actions():
                    secondary_button('閉じる', self.recovery_dialog.close, testid='premium-recovery-close')
        self.recovery_open.set_visibility(self.recovery_visible())
        self.notice=ui.label(vault_unlock_error_message(gate.status().get('error')) or '').props('role=status data-testid=premium-permission-status')
        ui.context.client.on_delete(self.close)
    def recovery_visible(self):
        return bool(self.recovery is not None and self.recovery.unconfirmed())
    def open_recovery(self):
        if self.closed or self.recovery is None:return
        self.recovery.render();self.recovery_dialog.open()
    def _recovery_changed(self):
        if self.closed:return
        visible=self.recovery_visible()
        self.recovery_open.set_visibility(visible)
        if not visible and '停止したまま' in (self.notice.text or ''):self.notice.set_text('')
        if self.on_change:self.on_change()
    def _wait_message(self):
        state=self.gate.status()
        return f"しばらく待ってから、もう一度「有効にする」を押してください（あと{int(state['retry_after'])+1}秒）。"
    def _wait_suffix(self):
        state=self.gate.status()
        return f" あと{int(state['retry_after'])+1}秒待ってください。" if state['retry_after']>0 else ''
    def _set_dialog_error(self,text):
        self.dialog_error.set_text(text)
    async def _wait_password(self):
        """Wait for submit() WITHOUT closing the dialog (awaiting it would close it).

        True/False is the user's confirm/cancel; a closed or deleted dialog is a cancel.
        """
        dialog=self.password_dialog
        if self.closed:return False
        dialog._result=None;dialog.submitted.clear()
        if not dialog.value:dialog.open()
        await dialog.submitted.wait()
        result=dialog._result;dialog._result=None
        return result is True and not self.closed
    def _close_password_dialog(self):
        if not self.closed:self.password_dialog.close()
    def _reset_password(self):
        # QDialog unmount can restore QInput's last inputValue on the client
        # after set_value('') was sent. A new element ID cannot inherit that
        # old client model, even when the server value was already empty.
        if self.password is not None:
            self.password.set_value('')
        self.password_slot.clear()
        with self.password_slot:
            self.password=outlined_input('パスワード',password=True).props(
                'autofocus autocomplete=off data-testid=premium-activation-password')
            self.password.on('keydown.enter',lambda:self.password_dialog.submit(True))
    def _reset_secret(self):
        # Same reason as _reset_password: a fresh element ID per popup so the
        # browser cannot restore the previously typed signing key.
        if self.secret is not None:
            self.secret.set_value('')
        self.secret_slot.clear()
        with self.secret_slot:
            self.secret=outlined_input('Shaft \u7f72\u540d\u30ad\u30fc\uff08\u4efb\u610f\u30fb\u65e2\u306b\u767b\u9332\u6e08\u307f\u306a\u3089\u7a7a\u6b04\u3067\u3088\u3044\uff09',password=True).props(
                'autocomplete=off data-testid=premium-secret')
    def _reset_legacy(self,create=False):
        if self.legacy is not None:
            self.legacy.set_value('')
        self.legacy=None
        self.legacy_slot.clear()
        if not create:return
        with self.legacy_slot:
            self.legacy=outlined_input('保管庫のパスワード（以前に設定したもの）',password=True).props(
                'autofocus autocomplete=off data-testid=premium-vault-password')
            self.legacy.on('keydown.enter',lambda:self.password_dialog.submit(True))
    def _show_legacy(self,visible):
        self._reset_legacy(create=visible)
        self.password_slot.set_visibility(not visible)
        self.main_box.set_visibility(not visible)
        self.legacy_box.set_visibility(visible)
    def _set_uid_notice(self,text,*,error=False):
        self.uid_notice.set_text(text)
        if error:self.uid_notice.classes(add='text-negative')
        else:self.uid_notice.classes(remove='text-negative')
    def _clear_uids(self):
        self._pick_epoch+=1;self._uids=[];self._uid_error=False;self._uid_explicit=False
        self._set_uid_notice('UID一覧: 未読み込み（変更しません）')
    def _load_uid_bytes(self,payload,*,explicit=True):
        """Take a UID list from a picked file.

        A bad list shows EVERY problem line (line numbers only) in the dialog and
        leaves the list loaded before it untouched, as the original does.
        """
        try:
            if not isinstance(payload,bytes) or len(payload)>65536:raise ValueError()
            uids,errors=uid_text_errors(payload.decode('utf-8-sig'))
        except (ValueError,UnicodeError):
            self._uid_error=True
            self._set_uid_notice('ファイルを読めませんでした（UTF-8のテキストにしてください）。',error=True);return False
        if errors:
            self._uid_error=True
            self._set_uid_notice(' '.join(errors),error=True);return False
        self._uids=uids;self._uid_explicit=explicit;self._uid_error=False
        self._set_uid_notice(f'UID一覧: {len(uids)}件を読み込みました（有効にすると保存されます）')
        return True
    def reject_uid_file(self,event=None):
        self._uid_error=True;self._set_uid_notice('UID一覧の txt を読み込めません。',error=True)
    async def pick_uid_file(self,event):
        self._pick_epoch+=1;epoch=self._pick_epoch
        try:
            file=event.file
            if not str(file.name).lower().endswith('.txt'):raise ValueError()
            payload=await file.read()
        except Exception:
            if not self.closed and epoch==self._pick_epoch:self.reject_uid_file()
            return
        if not self.closed and epoch==self._pick_epoch:self._load_uid_bytes(payload)
    def _load_default_uids(self):
        """The fixed UID file is the authority whenever it exists (original gui.py:859).

        Absent: nothing is loaded and the saved UIDs stay. Unreadable or invalid: every
        problem is shown, nothing is loaded and the saved UIDs are never overwritten.
        """
        self._clear_uids()
        state,uids,errors=read_uid_file(self.default_uid_path)
        if state=='ok':
            self._uids=uids;self._uid_explicit=False
            self._set_uid_notice(f'UID一覧: {len(uids)}件を自動読み込みしました（{Path(self.default_uid_path).name}）')
        elif state=='error':
            self._uid_error=True;self._set_uid_notice(' '.join(errors),error=True)
    def reflect_uid_file_at_startup(self):
        """At application start an opened vault takes the file's list (original). Never raises."""
        try:
            if self.closed or not self.default_uid_path or not self.gate.status()['enabled']:return 'skipped'
            return apply_uid_file(self.gate.vault,self.default_uid_path)
        except Exception:return 'failed'
    async def request_enable(self):
        epoch=self.gate.begin_prompt()
        if epoch is None:return
        password='';legacy_password=''
        try:
            state=self.gate.status()
            if state['retry_after']>0:
                self.notice.set_text(f"しばらく待ってから操作してください（あと{int(state['retry_after'])+1}秒）。")
                return
            self._show_legacy(False);self._reset_password();self._reset_secret();self._set_dialog_error('')
            self.recovery_open.set_visibility(self.recovery_visible())
            # The fixed UID file is read every time the popup opens (original).
            self._load_default_uids()
            while True:
                if not await self._wait_password():return
                self._set_dialog_error('')
                password=str(self.password.value or '')
                self.confirm_button.disable()
                try:
                    await asyncio.to_thread(self.gate.authenticate,password,expected_epoch=epoch)
                except VaultError as error:
                    if self.closed:return
                    if error.code=='premium-password-wait':
                        # Typed but not tried: keep it and let the user press again.
                        self._set_dialog_error(self._wait_message());continue
                    if error.code=='premium-password-rejected':
                        self._reset_password()
                        self._set_dialog_error(dialog_error_message(error.code)+self._wait_suffix());continue
                    raise
                finally:
                    password=''
                    if not self.closed:self.confirm_button.enable()
                if not self.gate.vault.status()['initialized'] and (not str(self.secret.value or '').strip() or not self._uids):
                    # The password itself was right; keep the dialog and everything typed.
                    self._set_dialog_error('初期設定には Shaft 署名キーと UID 一覧の両方が必要です。');continue
                break
            # The password was accepted: take the remaining inputs and leave the dialog.
            self._reset_password()
            # The original strips the signing key before saving it (gui.py:989).
            secret=str(self.secret.value or '').strip();self._reset_secret()
            uid_list=list(self._uids);uid_text='\n'.join(map(str,uid_list));explicit_uids=self._uid_explicit;self._clear_uids()
            self._close_password_dialog()
            vault_status=self.gate.vault.status()
            if (self.gate.status().get('error')=='premium-legacy-unlock-required'
                    and vault_status['initialized'] and not vault_status['open']):
                # The vault was protected with a different (earlier) password.
                self._show_legacy(True)
                accepted=await self._wait_password()
                legacy_password=str(self.legacy.value or '');self._reset_legacy()
                self._show_legacy(False)
                if not accepted or self.closed:return
                self._close_password_dialog()
                await asyncio.to_thread(self.gate.unlock_legacy,legacy_password,expected_epoch=epoch)
                legacy_password=''
            if not self.gate.vault.status()['initialized'] and (not secret or not uid_text):
                self.notice.set_text('\u521d\u671f\u8a2d\u5b9a\u306b\u306f Shaft \u7f72\u540d\u30ad\u30fc\u3068 UID \u4e00\u89a7\u306e\u4e21\u65b9\u304c\u5fc5\u8981\u3067\u3059\u3002')
                return
            if self.closed:return
            # The original always keeps ON for the next launch: no separate question.
            await asyncio.to_thread(self.gate.confirm,True,expected_epoch=epoch)
            try:
                if not self.gate.vault.status()['initialized']:
                    await asyncio.to_thread(self.gate.initialize_vault,uid_text,secret)
                else:
                    ready=await asyncio.to_thread(self.gate.restore_vault)
                    if not ready:raise VaultError('premium-legacy-unlock-required')
                    # The UID file (or a file picked here) replaces the saved UIDs when it
                    # differs; a signing key typed here replaces the saved key. An
                    # unconfirmed send blocks every change, so a list that only came from
                    # the fixed file is then left alone instead of blocking ON.
                    change_uids=bool(uid_list) and uid_list!=self.gate.vault.registered_uids()
                    if change_uids and not explicit_uids and self.gate.vault.status().get('oauth_review_required'):
                        change_uids=False
                    if secret or change_uids:
                        kwargs={}
                        if secret:kwargs['secret_key']=secret
                        if change_uids:kwargs['uid_text']=uid_text
                        await asyncio.to_thread(self.gate.vault.update_config,**kwargs)
            except Exception:
                await asyncio.to_thread(self.gate.disable)
                raise
            secret=uid_text=''
            ready=await asyncio.to_thread(self.gate.restore_vault)
            review=bool(ready and self.gate.vault.status().get('oauth_review_required'))
            if ready and not review:
                self.gate.vault.start_keeper()
            self.notice.set_text('借用プレミアムはONです。'+'次回起動後も維持します。'+
                ('' if ready else '保管庫を開けませんでした。借用プレミアムをOFFにしてから再度ONにし、署名キーとUID一覧を確認してください。')+
                ('前回の通信の結果を確認できていない登録があるため、借用の通信は停止したままです。保管されているデータには変更を加えていません。' if review else ''))
        except Exception as error:
            state=self.gate.status()
            message=vault_unlock_error_message(error.code if isinstance(error,VaultError) else None)
            self.notice.set_text((message or 'ONにできませんでした。入力または保管状態を確認してください。')+
                (f" あと{int(state['retry_after'])+1}秒待ってください。" if state['retry_after']>0 else ''))
        finally:
            password=legacy_password='';secret=uid_text=''
            if not self.closed:
                self._close_password_dialog();self._set_dialog_error('')
                self._show_legacy(False);self._clear_uids();self._reset_password();self._reset_legacy();self._reset_secret()
            self.gate.end_prompt()
            if not self.closed:self.recovery_open.set_visibility(self.recovery_visible())
            if not self.closed and self.recovery is not None:self.recovery.render()
            if self.on_change and not self.closed:self.on_change()
    def close(self):
        if self.closed:return
        self.closed=True;self.password.set_value('');self.secret.set_value('')
        if self.legacy is not None:self.legacy.set_value('')
        self._clear_uids()
        self.password_dialog.submit(False)
