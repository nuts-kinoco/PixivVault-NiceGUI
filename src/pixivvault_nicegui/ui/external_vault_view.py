"""Explicit local vault actions; no automatic unlock or fetch on navigation."""
import asyncio
from nicegui import ui
from ..external_vault import VaultError

class ExternalVaultView:
    def __init__(self,services,**_):
        self.vault=getattr(services,'external_vault',None);self.closed=False;self.busy=False;self.active=False
        self.client=ui.context.client
        self._status_signature=None;self._status_timer=None
        with ui.column().classes('w-full gap-3').props('data-testid=external-vault-view') as self.root:
            ui.label('外部連携・借用アカウント').classes('text-h6')
            ui.label('専用の暗号化保管庫を使用します。通常の Pixiv Cookie とは分離されます。')
            self.notice=ui.label('').props('role=status data-testid=external-vault-status')
            self.password=ui.input('保管庫パスワード',password=True,password_toggle_button=True).props('outlined autocomplete=off data-testid=external-vault-password')
            self.uid_text=ui.textarea('登録 UID（1 行に 1 件、最大 10 件）').props('outlined data-testid=external-vault-uids')
            self.secret=ui.input('Shaft 署名キー',password=True).props('outlined autocomplete=off data-testid=external-vault-secret')
            with ui.row():
                self.create_button=ui.button('専用保管庫を作成',on_click=lambda:self.action('initialize')).props('data-testid=external-vault-create')
                self.unlock_button=ui.button('ロック解除',on_click=lambda:self.action('unlock')).props('data-testid=external-vault-unlock')
                self.lock_button=ui.button('ロック',on_click=self.lock).props('data-testid=external-vault-lock')
                self.refresh_button=ui.button('アカウントを更新',on_click=lambda:self.action('refresh')).props('data-testid=external-vault-refresh')
            self.auto_unlock=ui.checkbox('この端末で次回自動解錠（原版と同じ端末結合方式）',value=False,on_change=self.auto_changed).props('data-testid=external-vault-auto-unlock')
            self.slot_status=ui.label('').props('data-testid=external-vault-slots')
        self.render()
        # Keep status observation per client and only while this tab is active.
        # A keeper or another client may poison the shared socket fence without
        # any event being delivered to the current view.
        self._status_timer=ui.timer(0.5,self._observe_status,active=False)
        self.client.on_delete(self.close)
    @staticmethod
    def _signature(status):
        if status is None:return None
        return tuple(status.get(key) for key in (
            'socket_shutdown_unverified','initialized','crypto_available',
            'transport_available','open','auto_unlock','mode'))
    def _observe_status(self):
        if self.closed or not self.active or self.vault is None:return
        status=self.vault.status()
        if self._status_signature!=self._signature(status):
            self.render()
    def render(self):
        if self.closed:return
        status=self.vault.status() if self.vault else None
        self._status_signature=self._signature(status)
        poisoned=bool(status and status.get('socket_shutdown_unverified'))
        if poisoned:
            # All client-local credentials disappear as soon as this active
            # client observes another client's or keeper's shutdown failure.
            self.password.set_value('');self.secret.set_value('')
            if self._status_timer is not None:self._status_timer.active=False
        available=bool(status and status['crypto_available'] and not poisoned)
        opened=bool(status and status['open'] and not poisoned);initialized=bool(status and status['initialized'])
        message=('専用保管庫が未設定です。起動時に専用保存先を指定してください。' if status is None else
            '通信の遮断を確認できません。安全のためアプリを終了し、再起動してください。' if poisoned else
            '暗号化依存 cryptography が製品環境にありません。保管庫操作は利用できません。' if not available else
            'ロック解除済み・借用アカウント利用可能' if opened and status['mode']=='nana7mi' else
            'ロック解除済み・トークン更新が必要です。' if opened else 'ロック中' if initialized else '専用保管庫を作成してください。')
        self.notice.set_text(message)
        self.auto_unlock.set_enabled(opened and not self.busy)
        self.auto_unlock.set_value(bool(status and status.get('auto_unlock')))
        self.password.set_enabled(available and not self.busy);self.create_button.set_enabled(available and not initialized and not self.busy)
        self.unlock_button.set_enabled(available and initialized and not opened and not self.busy);self.lock_button.set_enabled(opened)
        self.refresh_button.set_enabled(opened and status['transport_available'] and not self.busy)
        self.uid_text.set_visibility(not initialized);self.secret.set_visibility(not initialized);self.create_button.set_visibility(not initialized)
        self.uid_text.set_enabled(available and not self.busy);self.secret.set_enabled(available and not self.busy)
        self.slot_status.set_text(' / '.join(f"登録 {s['index']}: 失敗 {s['failures']} 回・更新待ち {int(s['retry_after'])} 秒" for s in status['slots']) if status else '')
    async def action(self,kind):
        if self.closed or self.busy or self.vault is None:return
        if self.vault.status().get('socket_shutdown_unverified'):
            self.password.set_value('');self.secret.set_value('')
            self.render()
            return
        self.busy=True;self.render();self.notice.set_text('処理中…')
        password=str(self.password.value or '');secret=str(self.secret.value or '');uids=str(self.uid_text.value or '')
        self.password.set_value('');self.secret.set_value('');error=None
        try:
            if kind=='initialize':await asyncio.to_thread(self.vault.initialize,password,uids,secret)
            elif kind=='unlock':await asyncio.to_thread(self.vault.unlock,password)
            elif kind=='refresh':await asyncio.to_thread(self.vault.refresh)
            else:raise ValueError('invalid action')
            if kind in ('initialize','unlock'):self.vault.start_keeper()
            error=None
        except Exception as exc:error=exc.code if isinstance(exc,VaultError) else 'operation-failed'
        finally:
            password=secret='';self.busy=False
            if not self.closed:
                self.render()
                if error and not self.vault.status().get('socket_shutdown_unverified'):
                    self.notice.set_text('操作を完了できませんでした: '+error)
    def auto_changed(self,event):
        if self.closed or self.vault is None:return
        status=self.vault.status()
        if status.get('socket_shutdown_unverified'):return
        if not status['open'] or bool(event.value)==status.get('auto_unlock',False):return
        try:self.vault.configure_auto_unlock(bool(event.value))
        except Exception:self.notice.set_text('自動解錠設定を保存できませんでした。')
    def lock(self):
        failed=False
        try:
            if self.vault is not None:self.vault.lock()
        except VaultError:
            failed=True
        finally:
            self.password.set_value('');self.secret.set_value('');self.render()
        if failed:self.notice.set_text('通信の遮断を確認できません。安全のためアプリを終了し、再起動してください。')
    def set_active(self,active):
        self.active=bool(active) and not self.closed
        if self.active:self.render()
        self._status_timer.active=self.active and not bool(
            self._status_signature and self._status_signature[0])
    def close(self):
        if self.closed:return
        self.closed=True;self.active=False
        if self._status_timer is not None:
            self._status_timer.active=False
            self._status_timer.cancel()
        # Borrowed app service stays owned by application shutdown.
