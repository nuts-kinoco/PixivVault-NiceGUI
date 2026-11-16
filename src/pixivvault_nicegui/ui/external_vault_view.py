"""Explicit local vault actions; no automatic unlock or fetch on navigation."""
import asyncio
import base64
from nicegui import ui
from ..external_vault import VaultError, parse_uid_text, LIMIT, VAULT_INTEGRITY_ERRORS
from .premium_gate_view import vault_unlock_error_message

class ExternalVaultView:
    def __init__(self,services,**_):
        self.vault=getattr(services,'external_vault',None);self.closed=False;self.busy=False;self.active=False
        self.client=ui.context.client
        self._status_signature=None;self._status_timer=None
        self._recovery_blocked=False;self._auto_epoch=None
        self._unlock_error=None
        self.gate=getattr(services,'premium_authorization',None)
        self.gate_dialog=None
        self._permission_signature=None
        self._pool_signature=None
        self._picked_uids=[];self._pick_epoch=0
        self._legacy_ciphertext=b'';self._legacy_epoch=0
        with ui.column().classes('w-full gap-3').props('data-testid=external-vault-view') as self.root:
            ui.label('外部連携・借用アカウント').classes('text-h6')
            ui.label('人気順検索・タグ別ランキング・過去のランキングで使います。パスワードで有効にし、OFFでロックします。')
            ui.label('専用の暗号化保管庫を使用し、通常の Pixiv Cookie と分離します。借用が使えないときは本人の経路へ戻り、使えない検索条件を表示します。')
            if self.gate is not None:
                from .premium_gate_view import PremiumGateDialog
                self.gate_dialog=PremiumGateDialog(self.gate,on_change=self.render)
                self.enable_borrow_button=ui.button('借用プレミアムをONにする',on_click=self.gate_dialog.request_enable).props('data-testid=premium-enable')
                self.permission_status=ui.label('').props('role=status data-testid=premium-enabled-state')
            self.notice=ui.label('').props('role=status data-testid=external-vault-status')
            self.recovery=ui.label('').props('role=status data-testid=external-vault-recovery-status')
            self.inspect_button=ui.button('停止状態を確認（通信なし）',on_click=self.inspect_recovery).props('data-testid=external-vault-inspect')
            self.password=ui.input('保管庫パスワード',password=True,password_toggle_button=True).props('outlined autocomplete=off data-testid=external-vault-password')
            self.uid_text=ui.textarea('登録 UID（1 行に 1 件、最大 10 件）').props('outlined data-testid=external-vault-uids')
            self.secret=ui.input('Shaft 署名キー（必須）',password=True).props('outlined autocomplete=off data-testid=external-vault-secret')
            ui.label('署名キーは保管庫に暗号化して保存します。借用ON状態の保存選択とは別です。').classes('text-caption')
            ui.label('登録UIDは払い出しを要求する自分のUIDです。借用先のUIDではありません。トークンは暗号化して保存し、再利用・期限更新します。').classes('text-caption')
            with ui.expansion('旧形式の保管庫を取り込む（通信なし）').classes('w-full') as self.legacy_panel:
                ui.label('旧保管庫の暗号ファイルと、旧設定の salt・verify（Base64）を指定してください。元ファイルは変更せず、未作成の専用保管庫へ取り込みます。')
                self.legacy_password=ui.input('旧保管庫のパスワード',password=True).props(
                    'outlined autocomplete=off data-testid=external-vault-legacy-password')
                ui.label('借用プレミアムをONにするパスワードとは別に、旧保管庫を暗号化したパスワードを入力してください。')
                self.legacy_salt=ui.input('旧設定の salt（Base64）',password=True).props('outlined autocomplete=off')
                self.legacy_verify=ui.input('旧設定の verify（Base64）',password=True).props('outlined autocomplete=off')
                self.legacy_upload=ui.upload(on_upload=self.pick_legacy_file,on_rejected=self.reject_legacy_file,
                    multiple=False,max_file_size=LIMIT+28,auto_upload=True,label='旧保管庫の暗号ファイルを選択')
                self.legacy_import_button=ui.button('選択した旧保管庫を取り込む',on_click=self.import_legacy).props(
                    'data-testid=external-vault-import-legacy')
                self.legacy_notice=ui.label('選択だけでは取り込みません。旧保管庫のパスワードを入力して実行してください。').props(
                    'role=status data-testid=external-vault-legacy-status')
            with ui.row():
                self.create_button=ui.button('専用保管庫を作成',on_click=lambda:self.action('initialize')).props('data-testid=external-vault-create')
                self.unlock_button=ui.button('ロック解除',on_click=lambda:self.action('unlock')).props('data-testid=external-vault-unlock')
                self.reopen_button=ui.button('保存済み状態を再確認（通信なし）',on_click=lambda:self.action('reopen_saved')).props('data-testid=external-vault-reopen-saved')
                self.recover_button=ui.button('記録済み更新結果を復元（通信なし）',on_click=lambda:self.action('recover_known')).props('data-testid=external-vault-recover-known')
                self.lock_button=ui.button('今すぐロック（自動解錠もOFF）',on_click=self.lock).props('data-testid=external-vault-lock')
                self.refresh_button=ui.button('アカウントを更新',on_click=lambda:self.action('refresh')).props('data-testid=external-vault-refresh')
            self.update_secret=ui.input('新しい Shaft 署名キー（空欄なら維持）',password=True).props('outlined autocomplete=off data-testid=external-vault-update-secret')
            self.update_uid_text=ui.textarea('新しい UID 一覧（空欄なら維持）').props('outlined data-testid=external-vault-update-uids')
            self.uid_upload=ui.upload(
                on_upload=self.pick_uid_file,
                multiple=False,max_file_size=65536,
                auto_upload=True,label='UID 一覧の txt ファイルを選択',
            ).props('accept=.txt data-testid=external-vault-upload').on(
                'rejected',self.pick_uid_rejected,
                js_handler='rejections => emit(rejections.map(item => item.failedProp))')
            self.picked_count=ui.label('選択済み: 0 件').props('data-testid=external-vault-picked-count')
            self.update_button=ui.button('保管庫の設定を更新',on_click=lambda:self.action('update')).props('data-testid=external-vault-update')
            self.auto_unlock=ui.checkbox('この端末で自動解錠を有効にする（明示的にOFFにするまで継続）',value=False).props('data-testid=external-vault-auto-unlock')
            ui.label('未選択のままでは保存済みの設定を変えません。新規はOFFです。鍵はこのWindowsユーザーのDPAPIで保護します。')
            self.legacy_auto_choice=ui.radio({'continue':'引き継いだ自動解錠を継続する','stop':'自動解錠を停止する'},value=None).props('data-testid=external-vault-auto-legacy-choice')
            self.enable_auto_button=ui.button('選択した自動解錠を保存',on_click=self.auto_changed).props('data-testid=external-vault-auto-enable')
            self.disable_auto_button=ui.button('自動解錠をOFFにする',on_click=self.auto_off).props('data-testid=external-vault-auto-off')
            self.auto_unlock_notice=ui.label('').props('role=status data-testid=external-vault-auto-unlock-status')
            self.retention_notice=ui.label(
                '新規の正常更新記録は累積16件まで保持します。上限後も本体の保存を続け、'
                '失敗・未確認の記録は保全します。記録前に中断した結果は推定して復元しません。'
            ).props('data-testid=external-vault-retention')
            self.slot_status=ui.label('').props('data-testid=external-vault-slots')
            self.request_budget_status=ui.label('').props('role=status data-testid=premium-request-budget')
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
            'transport_available','open','auto_unlock','auto_unlock_state','auto_unlock_inherited','auto_unlock_epoch','mode','busy','oauth_review_required','auto_unlock_save_failed')) + tuple(
                (s['failures'],int(s['retry_after'])) for s in status.get('slots',[]))
    def _observe_status(self):
        if self.closed or not self.active or self.vault is None or self.busy:return
        status=self.vault.status()
        permission=self.gate.status() if self.gate is not None else {}
        signature=(permission.get('enabled'),permission.get('remembered'),permission.get('error'))
        try:pool_signature=tuple((s['payouts'],s['borrowed'],int(s['wait'])) for s in self.vault.premium_pool_status())
        except Exception:pool_signature=('unavailable',)
        if self._status_signature!=self._signature(status) or signature!=self._permission_signature or pool_signature!=self._pool_signature:
            self.render()
    def _clear_picked(self):
        self._pick_epoch+=1
        self._picked_uids=[]
        self.picked_count.set_text('選択済み: 0 件')
    def _clear_legacy(self,*,clear_inputs=True):
        self._legacy_epoch+=1;self._legacy_ciphertext=b''
        if clear_inputs:
            self.legacy_password.set_value('')
            self.legacy_salt.set_value('');self.legacy_verify.set_value('')
        self.legacy_upload.reset()
    def reject_legacy_file(self,event=None):
        if self.closed:return
        self._clear_legacy()
        self.legacy_notice.set_text('暗号ファイルを読み込めません。サイズとファイルを確認してください。')
    def _can_import(self):
        if self.closed or self.busy or self.vault is None:return False
        status=self.vault.status()
        return bool(status['crypto_available'] and not status.get('has_artifact',status['initialized'])
                    and not status.get('socket_shutdown_unverified') and not status['busy'])
    async def pick_legacy_file(self,event):
        if not self._can_import():return
        # Selecting a file must not discard the password/salt/verify already entered.
        self._clear_legacy(clear_inputs=False);epoch=self._legacy_epoch
        try:payload=await event.file.read()
        except Exception:payload=None
        if self.closed or epoch!=self._legacy_epoch or not self._can_import():return
        if type(payload) is not bytes or not 28<=len(payload)<=LIMIT+28:
            self.reject_legacy_file();return
        self._legacy_ciphertext=payload
        self.legacy_notice.set_text('暗号ファイルを選択しました。まだ取り込んでいません。')
    async def import_legacy(self):
        if not self._can_import():
            if not self.closed:self.legacy_notice.set_text('旧形式の取込は、暗号化機能を利用できる未作成・処理待ちでない保管庫で実行してください。')
            return
        password=str(self.legacy_password.value or '')
        if not password:
            self.legacy_notice.set_text('旧保管庫のパスワードを入力してください。まだ取り込んでいません。')
            return
        if not self._legacy_ciphertext:
            self.legacy_notice.set_text('旧保管庫の暗号ファイルを選択してください。まだ取り込んでいません。')
            return
        salt_text=str(self.legacy_salt.value or '');verify_text=str(self.legacy_verify.value or '')
        ciphertext=self._legacy_ciphertext
        self._clear_legacy()
        self.busy=True;self.render()
        error=None
        try:
            salt=base64.b64decode(salt_text.strip(),validate=True)
            verify=base64.b64decode(verify_text.strip(),validate=True)
            if len(salt)!=16 or len(verify)!=32 or not ciphertext:raise VaultError('invalid-legacy-input')
            await asyncio.to_thread(self.vault.import_legacy,password,
                salt=salt,verify=verify,ciphertext=ciphertext,activate=False)
        except Exception:error=True
        finally:
            password=salt_text=verify_text='';ciphertext=b'';self.busy=False
            if not self.closed:
                self.render()
                self.legacy_notice.set_text('取り込みを完了できませんでした。暗号ファイル・旧設定・パスワードを確認してください。'
                    if error else '取り込みました。保管庫はロック中です。通信・自動更新は開始していません。')
    def pick_uid_rejected(self,event):
        if self.closed:return
        self._clear_picked()
        self.uid_upload.reset()
        if self.vault is None:
            self.render()
            return
        status=self.vault.status()
        if not status['open'] or status.get('socket_shutdown_unverified'):
            self.render()
            return
        # NiceGUI's on_rejected discards the cause. Receive only QUploader's
        # failure categories, without transmitting file names or contents.
        reasons=getattr(event,'args',None)
        if isinstance(reasons,list) and 'max-file-size' in reasons:
            message='txt ファイルは 65536 バイト以下にしてください。'
        elif isinstance(reasons,list) and 'accept' in reasons:
            message='txt ファイルを選択してください。'
        else:
            message='txt ファイルを選択できませんでした。ファイルを確認して、選び直してください。'
        self.notice.set_text(message)
    async def pick_uid_file(self,event):
        if self.closed or self.vault is None:return
        # Each new selection supersedes the previous one, including failures.
        self._clear_picked()
        status=self.vault.status()
        if (self.busy or not status['open'] or not status['crypto_available']
                or status['busy'] or status.get('socket_shutdown_unverified')):
            self.render()
            return
        epoch=self._pick_epoch
        uploaded=getattr(event,'file',None)
        name=getattr(uploaded,'name',None)
        if (type(name) is not str or not name.lower().endswith('.txt')
                or not name[:-4].strip(' .')
                or any(char in name for char in ('/','\\','\x00'))):
            self.notice.set_text('txt ファイルを選択してください。')
            return
        payload=None;read_error=None
        try:payload=await uploaded.read()
        except FileNotFoundError:
            read_error='txt ファイルが見つかりません。ファイルを確認して、選び直してください。'
        except PermissionError:
            read_error='txt ファイルを読み取る権限がありません。アクセス権を確認して、選び直してください。'
        except Exception:
            # Raw exceptions may contain private paths or file contents.
            read_error='txt ファイルの読み取りに失敗しました。ファイルや使用中のアプリを確認して、再選択してください。'
        if self.closed or epoch!=self._pick_epoch:return
        status=self.vault.status()
        if (self.busy or not status['open'] or not status['crypto_available']
                or status['busy'] or status.get('socket_shutdown_unverified')):
            self._clear_picked()
            self.render()
            return
        if read_error is not None:
            self.notice.set_text(read_error)
            return
        if type(payload) is not bytes:
            self.notice.set_text('txt ファイルの読取結果を確認できません。ファイルを確認して、再選択してください。')
            return
        if len(payload)>65536:
            self.notice.set_text('txt ファイルは 65536 バイト以下にしてください。')
            return
        try:uids=parse_uid_text(payload.decode('utf-8-sig','strict'))
        except UnicodeError:
            self.notice.set_text('txt ファイルを UTF-8 として読み込めませんでした。')
            return
        except VaultError as exc:
            self.notice.set_text('UID 一覧を読み込めませんでした: '+exc.code)
            return
        self._picked_uids=uids
        self.picked_count.set_text(f'選択済み: {len(uids)} 件')
        self.notice.set_text('UID 一覧を選択しました。設定の更新で保存されます。')
    def render(self):
        if self.closed:return
        status=self.vault.status() if self.vault else None
        self._status_signature=self._signature(status)
        epoch=status.get('auto_unlock_epoch') if status else None
        if epoch!=self._auto_epoch:
            self.auto_unlock.set_value(False);self.legacy_auto_choice.set_value(None)
            self._auto_epoch=epoch
        poisoned=bool(status and status.get('socket_shutdown_unverified'))
        unresolved=bool(status and status.get('oauth_review_required'))
        if poisoned:
            # All client-local credentials disappear as soon as this active
            # client observes another client's or keeper's shutdown failure.
            self.password.set_value('');self.secret.set_value('')
            self.uid_text.set_value('');self.update_secret.set_value('');self.update_uid_text.set_value('')
            self._clear_picked()
            self._clear_legacy()
            if self._status_timer is not None:self._status_timer.active=False
        available=bool(status and status['crypto_available'] and not poisoned and not status.get('inspection_error'))
        opened=bool(status and status['open'] and not poisoned);initialized=bool(status and status['initialized'])
        if opened:self._unlock_error=None
        permission_error=self.gate.status().get('error') if self.gate is not None else None
        unlock_message=None if opened else vault_unlock_error_message(
            permission_error if permission_error in VAULT_INTEGRITY_ERRORS or permission_error=='premium-material-stale' else self._unlock_error)
        # Keep the result visible after import, but never offer overwrite.
        for control in (self.legacy_password,self.legacy_salt,self.legacy_verify,self.legacy_upload,self.legacy_import_button):
            control.set_enabled(self._can_import())
        if not opened:
            self.update_secret.set_value('');self.update_uid_text.set_value('')
            self._clear_picked()
        message=('専用保管庫が未設定です。起動時に専用保存先を指定してください。' if status is None else
            '通信の遮断を確認できません。安全のためアプリを終了し、再起動してください。' if poisoned else
            ('保管庫を確認できません（'+status['inspection_error']+'）。ファイルを保持してください。') if status.get('inspection_error') else
            '暗号化依存 cryptography が製品環境にありません。保管庫操作は利用できません。' if not available else
            unlock_message if unlock_message else
            'アカウント更新の結果を確認できないため、更新と設定変更を停止しています。保存済み状態を再確認できます。保存済みも未確認なら停止を維持します。' if unresolved else
            '通信の停止状態を確認しました。下の確認結果に従ってください。' if self._recovery_blocked else
            '借用ON・検索時にトークンの有効期限を確認します。' if opened and status['mode']=='nana7mi' else
            'ロック解除済み・トークン更新が必要です。' if opened else 'ロック中' if initialized else '専用保管庫を作成してください。')
        self.notice.set_text(message)
        inherited=bool(status and status.get('auto_unlock_inherited'))
        self.auto_unlock.set_enabled(available and initialized and not self.busy and not unresolved and not inherited)
        self.legacy_auto_choice.set_visibility(inherited)
        self.legacy_auto_choice.set_enabled(available and initialized and not opened and not self.busy)
        self.enable_auto_button.set_enabled(opened and not self.busy and not unresolved)
        self.disable_auto_button.set_enabled(initialized and not self.busy and not poisoned)
        self.inspect_button.set_enabled(self.vault is not None and not self.busy)
        auto_state=status.get('auto_unlock_state','off') if status else 'off'
        auto_message=('次回自動解錠: 状態を確認できません。ON/OFFの完了とは扱いません。' if auto_state=='unknown' else
            '次回自動解錠: ON（旧設定から引継ぎ）。次の手動解錠で継続か停止かを選んでください。' if inherited else
            '次回自動解錠: ON（明示的なOFFまで継続）' if status and status.get('auto_unlock') else
            '次回自動解錠: OFF（毎回パスワードを入力）')
        if status and status.get('auto_unlock_save_failed'):
            auto_message+=' 自動解錠設定の保存・除去を確認できません。表示はディスクの確認結果です。パスワード解錠は引き続き使えます。'
        self.auto_unlock_notice.set_text(auto_message)
        self.password.set_enabled(available and not self.busy);self.create_button.set_enabled(available and not initialized and not self.busy)
        self.unlock_button.set_enabled(available and initialized and not opened and not self.busy);self.lock_button.set_enabled(initialized and not poisoned)
        self.reopen_button.set_enabled(available and initialized and not self.busy and not status['busy'] and not status.get('closed'))
        self.recover_button.set_enabled(available and initialized and not self.busy and not status['busy'] and not status.get('closed'))
        self.refresh_button.set_enabled(opened and status['transport_available'] and not self.busy and not unresolved and not self._recovery_blocked)
        self.uid_text.set_visibility(not initialized);self.secret.set_visibility(not initialized);self.create_button.set_visibility(not initialized)
        self.uid_text.set_enabled(available and not self.busy);self.secret.set_enabled(available and not self.busy)
        for control in (self.update_secret,self.update_uid_text,self.uid_upload,self.update_button):
            control.set_visibility(opened)
            control.set_enabled(opened and available and not self.busy and not status['busy'] and not unresolved)
        self.picked_count.set_visibility(opened)
        self.slot_status.set_text(' / '.join(f"登録 {s['index']}: 失敗 {s['failures']} 回・更新待ち {int(s['retry_after'])} 秒" for s in status['slots']) if status else '')
        try:
            pool=self.vault.premium_pool_status() if self.vault is not None else ()
            self._pool_signature=tuple((s['payouts'],s['borrowed'],int(s['wait'])) for s in pool)
            self.request_budget_status.set_text(' / '.join(f"登録{s['index']}：払い出し5時間 {s['payouts']}/{s['payout_limit']}件・借用1分 {s['borrowed']}/{s['borrow_limit']}件・待機 {int(s['wait'])}秒" for s in pool))
        except Exception:self.request_budget_status.set_text('利用回数の保管状態を確認できません。通信を止め、保管庫を確認してください。')
        if self.gate is not None:
            permission=self.gate.status();enabled=permission['enabled']
            self._permission_signature=(enabled,permission['remembered'],permission['error'])
            self.permission_status.set_text('借用プレミアム ON'+('（次回も維持）' if permission['remembered'] else '（このセッション限り）') if enabled else '借用プレミアム OFF・借用通信は行いません。')
            self.enable_borrow_button.set_visibility(not enabled)
            # Normal activation has one password field in its ON dialog. This
            # retained field is only for a separately encrypted legacy vault.
            legacy=enabled and initialized and not opened
            self.password.set_visibility(legacy);self.unlock_button.set_visibility(legacy)
            self.create_button.set_enabled(available and not initialized and enabled and not self.busy)
            self.refresh_button.set_enabled(enabled and opened and status['transport_available'] and not self.busy and not unresolved and not self._recovery_blocked)
            for control in (self.auto_unlock,self.legacy_auto_choice,self.enable_auto_button,self.disable_auto_button,self.auto_unlock_notice):control.set_visibility(False)
    async def action(self,kind):
        if self.closed or self.vault is None:return
        if self.busy:
            if kind=='update':
                self.update_secret.set_value('');self.update_uid_text.set_value('')
                self._clear_picked()
            return
        status=self.vault.status()
        if (status.get('oauth_review_required') and kind in ('refresh','update')
                or self._recovery_blocked and kind=='refresh'):
            self.update_secret.set_value('');self.update_uid_text.set_value('')
            self._clear_picked();self.render()
            return
        if status.get('socket_shutdown_unverified'):
            self.password.set_value('');self.secret.set_value('')
            self.uid_text.set_value('');self.update_secret.set_value('');self.update_uid_text.set_value('')
            self._clear_picked()
            self.render()
            return
        if kind=='update' and (not status['open'] or not status['crypto_available'] or status['busy']):
            self.update_secret.set_value('');self.update_uid_text.set_value('')
            self._clear_picked()
            self.render()
            return
        request_epoch=self.vault.unlock_request_epoch() if kind=='unlock' else None
        auto_choice=None
        if kind=='unlock' and request_epoch!=self._auto_epoch:self.render()
        if kind=='unlock':
            if status.get('auto_unlock_inherited'):
                choice=self.legacy_auto_choice.value
                if choice not in ('continue','stop'):
                    self.notice.set_text('旧設定の自動解錠を継続するか停止するか選んでください。')
                    return
                auto_choice=choice=='continue'
            elif self.auto_unlock.value:auto_choice=True
        self.auto_unlock.set_value(False);self.legacy_auto_choice.set_value(None)
        self.busy=True;self.render();self.notice.set_text('処理中…')
        password=str(self.password.value or '');secret=str(self.secret.value or '');uids=str(self.uid_text.value or '')
        update_secret=str(self.update_secret.value or '');update_uids=str(self.update_uid_text.value or '')
        picked_uids=list(self._picked_uids) if kind=='update' else []
        if kind=='update':self._clear_picked()
        self.password.set_value('');self.secret.set_value('');self.uid_text.set_value('')
        self.update_secret.set_value('');self.update_uid_text.set_value('');error=None
        try:
            if kind=='initialize':
                if not secret.strip():raise VaultError('signing-key-required')
                if self.gate is not None:await asyncio.to_thread(self.gate.initialize_vault,uids,secret)
                else:await asyncio.to_thread(self.vault.initialize,password,uids,secret)
            elif kind=='unlock':
                await asyncio.to_thread(self.vault.unlock,password,auto_unlock=auto_choice,expected_epoch=request_epoch)
                if self.gate is not None and self.gate.status()['enabled']:self.gate.remember_local_unlock()
            elif kind=='reopen_saved':await asyncio.to_thread(self.vault.reopen_saved_state,password)
            elif kind=='recover_known':await asyncio.to_thread(self.vault.recover_known_result,password)
            elif kind=='refresh':await asyncio.to_thread(self.vault.refresh)
            elif kind=='update':
                if picked_uids and update_uids.lstrip('\ufeff').strip():
                    raise VaultError('uid-source-ambiguous')
                if picked_uids:update_uids='\n'.join(map(str,picked_uids))
                await asyncio.to_thread(self.vault.update_config,secret_key=update_secret,uid_text=update_uids)
            else:raise ValueError('invalid action')
            if kind=='initialize' and (self.gate is None or self.gate.status()['enabled']):self.vault.start_keeper()
            elif kind=='unlock' and not self.vault.status().get('oauth_review_required') and (self.gate is None or self.gate.status()['enabled']):
                self.vault.start_keeper(expected_epoch=request_epoch)
            error=None
        except Exception as exc:
            error=(exc.code if isinstance(exc,VaultError) else 'vault-permission-denied' if isinstance(exc,PermissionError) else 'vault-io-error' if isinstance(exc,OSError) else 'operation-failed')
            if error in VAULT_INTEGRITY_ERRORS and self.gate is not None:
                try:await asyncio.to_thread(self.gate.disable)
                except Exception:pass  # disable clears in-memory permission before persistence
        finally:
            password=secret=uids=update_secret=update_uids='';picked_uids.clear();self.busy=False
            if not self.closed:
                if kind in ('unlock','reopen_saved','recover_known'):
                    self._unlock_error=error if vault_unlock_error_message(error) else None
                self.render()
                if error=='signing-key-required':self.notice.set_text('Shaft 署名キーを入力してください。借用通信は行いません。')
                elif error and not self.vault.status().get('socket_shutdown_unverified') and not self.vault.status().get('oauth_review_required'):
                    self.notice.set_text(vault_unlock_error_message(error) or '操作を完了できませんでした: '+error)
                elif kind=='update' and not error and not self.vault.status().get('socket_shutdown_unverified'):
                    self.notice.set_text(f"設定を更新しました（登録 {len(self.vault.status()['slots'])} 件）。")
                if kind=='reopen_saved' and not error:
                    self.recovery.set_text('保存済み状態を読み直しました。通信や自動更新は開始していません。'+(
                        '保存済みの更新結果は未確認のままです。' if self.vault.status().get('oauth_review_required') else
                        '保管庫の保存状態に未確認の更新はありません。共通STOPの解除ではありません。'))
                if kind=='recover_known':
                    self.recovery.set_text('検証済みの記録がないため復元できません。停止と履歴を維持しています。' if error else
                        '記録済みの更新結果を復元しました。通信・自動更新は開始していません。共通STOPは解除していません。')
    async def inspect_recovery(self):
        if self.closed or self.busy or self.vault is None:return
        self.busy=True;self.render()
        try:
            result=await asyncio.to_thread(self.vault.inspect_recovery)
        except Exception:
            result={'shared_stop':{'state':'unavailable','status_code':None}}
        finally:
            self.busy=False
        if self.closed:return
        shared=result['shared_stop'];state=shared['state']
        self._recovery_blocked=state!='clear'
        if state=='stopped':
            message=f"ユーザー共通の通信停止（HTTP {shared['status_code']}）。再起動やCookie変更では解除されません。停止履歴を保持した復旧手順の確認が必要です。"
        elif state=='clear':
            message='共通停止履歴にSTOPはありません。この確認は認証確認や通信再開ではありません。'
        else:
            message='共通の停止履歴または通信結果を確認できません。通信を再開せず、履歴を保持して復旧手順を確認してください。'
        if result.get('oauth_review_required'):
            message+=' 保管庫の更新結果も未確認です。更新要求を再送しないでください。'
        elif result.get('vault_open') is False:
            message+=' 保管庫がロック中のため、その更新結果は確認していません。'
        self.recovery.set_text(message);self.render()
    def auto_changed(self,event=None):
        if self.closed or self.busy or self.vault is None or not self.auto_unlock.value:return
        epoch=self.vault.unlock_request_epoch()
        self.auto_unlock.set_value(False)
        try:self.vault.configure_auto_unlock(True,expected_epoch=epoch)
        except Exception:
            self.render();self.notice.set_text('自動解錠設定の保存を確認できませんでした。')
        else:self.render()
    def auto_off(self,event=None):
        if self.closed or self.busy or self.vault is None:return
        self.auto_unlock.set_value(False);self.legacy_auto_choice.set_value(None)
        try:self.vault.configure_auto_unlock(False)
        except Exception:
            self.render();self.notice.set_text('自動解錠設定のOFFを確認できませんでした。')
        else:self.render()
    def lock(self):
        failed=None
        self.auto_unlock.set_value(False);self.legacy_auto_choice.set_value(None)
        try:
            if self.gate is not None:self.gate.disable()
            elif self.vault is not None:self.vault.lock_explicitly()
        except Exception as exc:
            failed=exc.code if isinstance(exc,VaultError) else 'operation-failed'
        finally:
            self.password.set_value('');self.secret.set_value('');self.uid_text.set_value('')
            self.update_secret.set_value('');self.update_uid_text.set_value('')
            self._clear_picked();self._clear_legacy();self.render()
        if failed:self.notice.set_text(
            '通信の遮断を確認できません。安全のためアプリを終了し、再起動してください。次回自動解錠の無効化は未確認です。'
            if failed=='socket-shutdown-unverified' else
            'ロックまたは次回自動解錠の無効化を確認できません。自動解錠が無効になったとは扱わず、状態を確認してください。')
    def set_active(self,active):
        self.active=bool(active) and not self.closed
        if self.active:self.render()
        self._status_timer.active=self.active and not bool(
            self._status_signature and self._status_signature[0])
    def close(self):
        if self.closed:return
        self.password.set_value('');self.secret.set_value('');self.uid_text.set_value('')
        self.update_secret.set_value('');self.update_uid_text.set_value('')
        self._clear_picked()
        self._clear_legacy()
        self.closed=True;self.active=False
        if self.gate_dialog is not None:self.gate_dialog.close()
        if self._status_timer is not None:
            self._status_timer.active=False
            self._status_timer.cancel()
        # Borrowed app service stays owned by application shutdown.
