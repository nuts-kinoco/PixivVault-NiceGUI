"""One activation-password input, followed by a separate persistence choice."""
import asyncio
from pathlib import Path
from nicegui import ui
from ..external_vault import VaultError, VAULT_INTEGRITY_ERRORS, parse_uid_text

def vault_unlock_error_message(code):
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

class PremiumGateDialog:
    def __init__(self,gate,*,on_change=None, default_uid_path=r"S:\Projects\PixivVault\nana7mi_uids.txt"):
        self.gate=gate;self.on_change=on_change;self.closed=False
        self.default_uid_path=default_uid_path;self._uids=[];self._pick_epoch=0;self._uid_error=False
        with ui.dialog().props('persistent') as self.password_dialog:
            with ui.card().classes('w-full max-w-md gap-4'):
                ui.label('借用プレミアムをONにする').classes('text-h6')
                ui.label('パスワードを確認してから有効にします。入力内容は保存しません。')
                with ui.column().classes('w-full') as self.password_slot:
                    self.password=None
                    self._reset_password()
                self.secret=ui.input('Shaft \u7f72\u540d\u30ad\u30fc\uff08\u4efb\u610f\u30fb\u65e2\u306b\u767b\u9332\u6e08\u307f\u306a\u3089\u7a7a\u6b04\u3067\u3088\u3044\uff09',password=True).props('outlined autocomplete=off data-testid=premium-secret')
                self.uid_upload=ui.upload(on_upload=self.pick_uid_file,on_rejected=self.reject_uid_file,
                    multiple=False,max_file_size=65536,auto_upload=True,label='UID\u4e00\u89a7 txt \u3092\u9078\u629e').props('accept=.txt data-testid=premium-uid-upload')
                self.uid_notice=ui.label('').props('role=status data-testid=premium-uid-count')
                with ui.row().classes('w-full justify-end'):
                    ui.button('キャンセル',on_click=lambda:self.password_dialog.submit(False)).props('flat')
                    ui.button('\u6709\u52b9\u306b\u3059\u308b',on_click=lambda:self.password_dialog.submit(True)).props('data-testid=premium-password-confirm')
        with ui.dialog().props('persistent') as self.save_dialog:
            with ui.card().classes('w-full max-w-md gap-4'):
                ui.label('有効な状態を保存しますか？').classes('text-h6')
                ui.label('保存すると次回もONで始まります。このWindowsユーザーだけが使えるよう、有効化の証跡を保護します。')
                with ui.row().classes('w-full justify-end'):
                    ui.button('保存しない',on_click=lambda:self.save_dialog.submit(False)).props('flat data-testid=premium-save-no')
                    ui.button('保存する',on_click=lambda:self.save_dialog.submit(True)).props('data-testid=premium-save-yes')
        self.notice=ui.label(vault_unlock_error_message(gate.status().get('error')) or '').props('role=status data-testid=premium-permission-status')
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
    def _clear_uids(self):
        self._pick_epoch+=1;self._uids=[];self._uid_error=False
        self.uid_notice.set_text('UID\u4e00\u89a7: \u672a\u8aad\u307f\u8fbc\u307f')
    def _load_uid_bytes(self,payload):
        self._clear_uids()
        try:
            if not isinstance(payload,bytes) or len(payload)>65536:raise ValueError()
            self._uids=parse_uid_text(payload.decode('utf-8-sig'))
        except VaultError as error:
            self._uid_error=True
            code=error.code
            if code.startswith('invalid-uid-line-') and code[17:].isdigit():
                message=f'UID\u4e00\u89a7: {code[17:]}\u884c\u76ee\u304c\u4e0d\u6b63\u3067\u3059\u3002'
            else:message='UID\u4e00\u89a7\u306f1\uff5e10\u4ef6\u3067\u6307\u5b9a\u3057\u3066\u304f\u3060\u3055\u3044\u3002'
            self.uid_notice.set_text(message);return False
        except (ValueError,UnicodeError):
            self._uid_error=True
            self.uid_notice.set_text('UID\u4e00\u89a7\u306e txt \u3092\u8aad\u307f\u8fbc\u3081\u307e\u305b\u3093\u3002');return False
        self.uid_notice.set_text(f'UID\u4e00\u89a7: {len(self._uids)}\u4ef6\u3092\u8aad\u307f\u8fbc\u307f\u307e\u3057\u305f')
        return True
    def reject_uid_file(self,event=None):
        self._clear_uids();self._uid_error=True;self.uid_notice.set_text('UID\u4e00\u89a7\u306e txt \u3092\u8aad\u307f\u8fbc\u3081\u307e\u305b\u3093\u3002')
    async def pick_uid_file(self,event):
        self._clear_uids();epoch=self._pick_epoch
        try:
            file=event.file
            if not str(file.name).lower().endswith('.txt'):raise ValueError()
            payload=await file.read()
        except Exception:
            if not self.closed and epoch==self._pick_epoch:self.reject_uid_file()
            return
        if not self.closed and epoch==self._pick_epoch:self._load_uid_bytes(payload)
    def _load_default_uids(self):
        self._clear_uids()
        if not self.default_uid_path:return
        try:
            with Path(self.default_uid_path).open('rb') as stream:payload=stream.read(65537)
        except FileNotFoundError:return
        except OSError:
            self._uid_error=True
            self.uid_notice.set_text('UID\u4e00\u89a7\u306e txt \u3092\u8aad\u307f\u8fbc\u3081\u307e\u305b\u3093\u3002');return
        self._load_uid_bytes(payload)
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
            self.secret.set_value('');self._load_default_uids()
            accepted=await self.password_dialog
            if not accepted or self.closed:return
            if self._uid_error:
                self.notice.set_text('UID\u4e00\u89a7\u3092\u78ba\u8a8d\u3057\u3066\u304f\u3060\u3055\u3044\u3002');return
            password=str(self.password.value or '');self._reset_password()
            secret=str(self.secret.value or '');self.secret.set_value('')
            uid_text='\n'.join(map(str,self._uids));self._clear_uids()
            await asyncio.to_thread(self.gate.authenticate,password,expected_epoch=epoch)
            password=''
            if not self.gate.vault.status()['initialized'] and (not secret.strip() or not uid_text):
                self.notice.set_text('\u521d\u671f\u8a2d\u5b9a\u306b\u306f Shaft \u7f72\u540d\u30ad\u30fc\u3068 UID \u4e00\u89a7\u306e\u4e21\u65b9\u304c\u5fc5\u8981\u3067\u3059\u3002')
                return
            remember=await self.save_dialog
            if self.closed:return
            await asyncio.to_thread(self.gate.confirm,bool(remember),expected_epoch=epoch)
            try:
                if not self.gate.vault.status()['initialized']:
                    await asyncio.to_thread(self.gate.initialize_vault,uid_text,secret)
                else:
                    ready=await asyncio.to_thread(self.gate.restore_vault)
                    if not ready:raise VaultError('premium-legacy-unlock-required')
                    if secret.strip() or uid_text:
                        await asyncio.to_thread(self.gate.vault.update_config,secret_key=secret,uid_text=uid_text)
            except Exception:
                await asyncio.to_thread(self.gate.disable)
                raise
            secret=uid_text=''
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
            password='';secret=uid_text=''
            if not self.closed:
                self.secret.set_value('');self._clear_uids();self._reset_password()
            self.gate.end_prompt()
            if self.on_change and not self.closed:self.on_change()
    def close(self):
        if self.closed:return
        self.closed=True;self.password.set_value('');self.secret.set_value('');self._clear_uids()
        self.password_dialog.submit(False);self.save_dialog.submit(False)
