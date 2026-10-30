"""Per-client explicit settings form; unsupported consumers remain unavailable."""
import asyncio
from nicegui import ui
from ..preferences import SPECS, SPEC_BY_KEY, PreferencesError
from ..legacy_metadata_export import LegacyMetadataExportError


UNAVAILABLE_REASONS = {
    'pixiv_actions_enabled': 'この起動ではPixiv操作の設定を変更できません。',
    'api_retry_count': 'この保存経路は停止して確認後に再試行します。通信の自動再送は未対応です。',
    'api_retry_wait': '429は全体障害として停止します。自動待機・再送は未接続です。',
    'download_interval': '通信間隔の適用先がこの起動では接続されていません。',
    'zip_all_after_download': '作者保存完了後の自動ZIP処理は未接続です。',
    'bookmark_download_mode': '現在の管理保存方式では、既存画像のリンク・移動による配置変更に対応していません。',
    'discovery_cache_limit_mode': '学習画像の先読みキャッシュは未接続です。',
    'discovery_cache_size_mb': '学習画像の先読みキャッシュは未接続です。',
    'discovery_cache_size_count': '学習画像の先読みキャッシュは未接続です。',
    'pixiv_shared_image_cache_size_mb': '全タブ共有キャッシュへの適用は未接続です。',
    'pixiv_disk_thumbnail_cache_size_gb': '専用ディスクキャッシュへの適用は未接続です。',
    'minimize_to_tray': 'Windowsトレイの接続がこの起動では利用できません。',
    'enable_notifications': 'Windows通知の実行依存（win11toast）がこの環境にはありません。',
}
CHOICE_LABELS = {
    'epub':'EPUBのみ', 'txt':'TXTのみ', 'both':'EPUBとTXT両方',
    'gif':'GIF動画', 'mp4':'MP4動画', 'folder':'フォルダ（フレームとJSON）',
    'bytes':'メモリ使用量', 'count':'画像枚数', 'direct':'直下にフラット配置', 'link':'作者別フォルダに配置',
}
CACHE_LIMIT_FIELDS = {
    'bytes': 'discovery_cache_size_mb',
    'count': 'discovery_cache_size_count',
}


class PreferencesView:
    def __init__(self, store, *, enabled_keys=(), unavailable_reasons=None, on_change=None, desktop=None, clear_cache=None, extension_status=None, export_legacy=None, native_profile=None, following_sync=None, pixiv_actions_mode='readonly'):
        if pixiv_actions_mode not in ('readonly', 'enabled', 'preferences'):
            raise ValueError('invalid Pixiv action policy')
        self.pixiv_actions_mode=pixiv_actions_mode
        self.store=store
        self.enabled_keys=frozenset(enabled_keys)
        if not self.enabled_keys.issubset(SPEC_BY_KEY):
            raise ValueError('unknown preference consumer')
        if pixiv_actions_mode != 'preferences':
            self.enabled_keys=self.enabled_keys-{'pixiv_actions_enabled'}
        self.reasons=dict(UNAVAILABLE_REASONS)
        self.reasons.update(unavailable_reasons or {})
        if pixiv_actions_mode == 'readonly':
            self.reasons['pixiv_actions_enabled']='この起動はGET専用です。この項目は通常起動の設定から変更できます。'
        elif pixiv_actions_mode == 'enabled':
            self.reasons['pixiv_actions_enabled']='起動オプションで有効に固定されています。解除するには起動オプションを変更して再起動してください。'
        self.on_change=on_change
        self.desktop=desktop
        self.extension_status=extension_status
        self.clear_cache=clear_cache
        self.export_legacy=export_legacy
        self.native_profile=native_profile
        self.following_sync=following_sync
        self.export_busy=False
        self.export_message=''
        self.export_button=None
        self.export_status=None
        self.closed=False
        self._epoch=0
        self.opened=False
        self.snapshot=None
        self.controls={}
        self._cache_limit_notes={}
        self.dialog=ui.dialog().props('persistent data-testid=preferences-dialog')

    def open(self):
        if self.closed: return
        self.opened=True
        self._epoch+=1
        epoch=self._epoch
        self.snapshot=None
        self.controls={}
        self._cache_limit_notes={}
        if self.native_profile is not None:
            self.native_profile.deactivate()
        if self.following_sync is not None:
            self.following_sync.deactivate()
        self.dialog.clear()
        with self.dialog, ui.card().classes('w-full max-w-2xl').style('max-height:90vh;overflow:auto'):
            ui.label('設定').classes('text-h6')
            if self.native_profile is not None:
                self.native_profile.build()
                ui.separator()
            if self.following_sync is not None:
                self.following_sync.build()
                ui.separator()
            self.status=ui.label('').props('data-testid=preferences-status role=status')
            try:
                self.snapshot=self.store.snapshot()
            except Exception:
                self.status.set_text('設定を読み込めません。保存は行いません。')
                ui.button('閉じる',on_click=self.close_dialog)
                self.dialog.open()
                return
            self.controls={}
            for group in dict.fromkeys(spec.group for spec in SPECS if spec.key != 'grid_card_size_mode'):
                ui.label(group).classes('text-subtitle1')
                for spec in SPECS:
                    if spec.group!=group or spec.key == 'grid_card_size_mode': continue
                    options={v:CHOICE_LABELS.get(v, ('ON' if v=='1' else 'OFF') if spec.choices==('0','1') else v) for v in spec.choices}
                    value=self.snapshot.values[spec.key]
                    if spec.key == 'pixiv_actions_enabled' and self.pixiv_actions_mode != 'preferences':
                        value='1' if self.pixiv_actions_mode == 'enabled' else '0'
                    control=ui.select(options,label=spec.label,value=value).classes('w-full').props(f'data-testid=preference-{spec.key}')
                    control.props['data-value']=str(control.value)
                    def acknowledge(event, control=control):
                        control.props['data-value']=str(event.value)
                        control.update()
                    control.on_value_change(acknowledge)
                    control.set_enabled(spec.key in self.enabled_keys)
                    self.controls[spec.key]=control
                    if spec.key == 'pixiv_actions_enabled':
                        notice=('変更は次回の通常起動から適用されます。Cookieの参照先が必要です。'
                                if spec.key in self.enabled_keys else self.reasons[spec.key])
                        ui.label(notice).classes('text-caption text-grey').props('data-testid=preference-pixiv-actions-mode')
                    elif spec.key == 'bookmark_download_mode' and spec.key in self.enabled_keys:
                        ui.label('設定保存だけでは通信しません。通常起動では、マイブックマークの「再読込」が成功してから、そのアカウントの保存配置に適用します。').classes(
                            'text-caption text-grey').props('data-testid=preference-bookmark-account-gate')
                    elif spec.key not in self.enabled_keys:
                        note=ui.label(self.reasons.get(spec.key,'この起動では適用先が接続されていません。')).classes('text-caption text-grey').props(
                            f'data-testid=preference-unavailable-{spec.key}')
                        if spec.key in CACHE_LIMIT_FIELDS.values():
                            self._cache_limit_notes[spec.key]=note
            self.controls['discovery_cache_limit_mode'].on_value_change(
                lambda _: self._sync_cache_limit_visibility()
                if self.opened and not self.closed and self._epoch == epoch else None)
            self._sync_cache_limit_visibility()
            ui.button('画像キャッシュを削除',on_click=lambda:self._clear_cache(epoch)).props('data-testid=preferences-clear-cache').set_enabled(self.clear_cache is not None)
            ui.label('アプリ全体で表示した作品・作者のサムネイル画像キャッシュを削除します。保存した作品は削除しません。').classes('text-caption')
            ui.label('静止画保存: 保存元の原画像を維持します。画質を変更する設定はありません。').classes('text-caption')
            application_notice=('Pixiv操作の設定は次回起動から、その他の設定は次の操作から使われます。実行中のジョブは変更しません。'
                                if 'pixiv_actions_enabled' in self.enabled_keys else
                                '変更は専用設定に保存され、次の操作から使われます。実行中のジョブは変更しません。')
            ui.label(application_notice).classes('text-caption')
            if self.extension_status is not None:
                label={'running':'拡張連携: 受付中','unavailable':'拡張連携: ポートを使用できません。既存アプリの終了後に再起動してください。','disabled':'拡張連携: 無効（明示した起動設定が必要）'}.get(self.extension_status(),'拡張連携: 状態不明')
                ui.label(label).props('role=status data-testid=extension-status')
            if self.desktop is not None and getattr(self.desktop,'protocol',None) is not None:
                ui.button('ブラウザ拡張機能の自動起動を設定',on_click=self._uri_prompt).props('data-testid=preferences-uri')
            else:
                ui.label('ブラウザ拡張機能の自動起動: URI起動経路が未接続です。').classes('text-caption')
            ui.label('エクスポート').classes('text-subtitle1')
            ui.label('旧データベースと作品メタデータだけをZIPに保存します。画像は含まれません。').classes('text-caption')
            self.export_button=ui.button('DBとメタデータをZIPへ保存',on_click=self.run_legacy_export).props(
                'data-testid=preferences-export-db')
            self.export_button.set_enabled(self.export_legacy is not None and not self.export_busy)
            if self.export_legacy is None:
                ui.label('この起動では読取専用の旧DBと専用設定保存先の両方が必要です。').classes('text-caption text-grey')
            self.export_status=ui.label(self.export_message).props('data-testid=preferences-export-status role=status')
            with ui.row():
                ui.button('保存',on_click=lambda:self.save() if self._epoch==epoch and self.opened else None).props('data-testid=preferences-save').set_enabled(bool(self.enabled_keys))
                ui.button('変更を破棄して再読込',on_click=self.open).props('data-testid=preferences-reload')
                ui.button('閉じる',on_click=self.close_dialog)
        self.dialog.open()

    def _sync_cache_limit_visibility(self):
        # Keep both draft values and consumer capabilities. Only the selected
        # unit and its unavailable reason belong in the visible form.
        mode=self.controls['discovery_cache_limit_mode'].value
        for unit,key in CACHE_LIMIT_FIELDS.items():
            visible=mode == unit
            self.controls[key].set_visibility(visible)
            note=self._cache_limit_notes.get(key)
            if note is not None:
                note.set_visibility(visible)

    async def run_legacy_export(self):
        """Execute E01 off the UI loop, never call an export twice while busy."""
        if self.closed or not self.opened or self.export_busy or self.export_legacy is None:
            return
        self.export_busy=True
        self.export_message='エクスポート中…'
        self.export_button.set_enabled(False)
        self.export_status.set_text(self.export_message)
        succeeded=False
        cleanup_warning=False
        try:
            result=await asyncio.to_thread(self.export_legacy)
            cleanup_warning=bool(getattr(result,'cleanup_warning',False))
            prefix='[完了・一時フォルダーの後始末要確認]' if cleanup_warning else '[完了]'
            self.export_message=(f'{prefix} {result.path}（旧DB + metadata.json、作品 {result.works} 件。'
                                 '画像は含まれません）')
            succeeded=True
        except LegacyMetadataExportError as error:
            # A typed bounded reason from our own validator, never raw secret
            # SQLite contents or an arbitrary exception stack.
            self.export_message='[失敗] '+str(error)
        except Exception:
            self.export_message='[失敗] エクスポートを完了できませんでした。元DBと保存先を確認してください。'
        finally:
            self.export_busy=False
            if not self.closed:
                if self.opened and self.export_button is not None and self.export_status is not None:
                    self.export_button.set_enabled(self.export_legacy is not None)
                    self.export_status.set_text(self.export_message)
                # The thread round-trip may resume without an implicit UI
                # slot; notify explicitly in the original dialog's client.
                with self.dialog:
                    ui.notify(('エクスポートは完了しましたが、一時フォルダーを確認してください。'
                               if cleanup_warning else 'エクスポートが完了しました。') if succeeded
                              else 'エクスポートに失敗しました。',
                              type='warning' if cleanup_warning else 'positive' if succeeded else 'negative')

    def save(self):
        if self.closed or not self.opened or self.snapshot is None: return False
        changes={key:self.controls[key].value for key in self.enabled_keys if self.controls[key].value!=self.snapshot.values[key]}
        try:
            snapshot=self.store.update(changes,expected_revision=self.snapshot.revision)
        except PreferencesError as exc:
            self.status.set_text(str(exc))
            return False
        except Exception:
            self.status.set_text('設定を保存できません。入力を保持しています。')
            return False
        self.snapshot=snapshot
        try:
            if self.on_change and changes: self.on_change(snapshot)
        except Exception:
            self.status.set_text('設定は保存されましたが、適用を確認できません。再読込してください。')
            return False
        if 'pixiv_actions_enabled' in changes:
            self.status.set_text('設定を保存しました。Pixiv操作の変更は次回起動から適用されます。')
        else:
            self.status.set_text('設定を保存しました。' if changes else '設定の変更はありません。')
        return True

    def _clear_cache(self,epoch):
        if self.closed or not self.opened or epoch!=self._epoch or self.clear_cache is None:return
        try:
            self.clear_cache()
            self.status.set_text('画像キャッシュを削除しました。')
        except Exception:
            self.status.set_text('画像キャッシュを削除できません。')

    def _uri_prompt(self):
        if self.closed or not self.opened or self.desktop is None: return
        epoch=self._epoch
        with ui.dialog() as dialog, ui.card():
            ui.label('ブラウザ拡張機能の自動連携')
            ui.label('有効にするとWindowsのユーザー設定にpixivvault URLスキームを登録します。')
            ui.label('このアプリの登録前の設定を保管し、解除時に元へ戻します。')
            result=ui.label('').props('role=status')
            def apply(enabled):
                if self.closed or not self.opened or epoch!=self._epoch:return
                try:
                    self.desktop.set_uri_registration(enabled,confirmed=True)
                    result.set_text('有効にしました。' if enabled else '登録前の自動起動設定に戻しました。')
                except Exception:
                    result.set_text('登録状態を変更できません。')
            ui.button('有効にする',on_click=lambda:apply(True))
            ui.button('無効にする',on_click=lambda:apply(False))
            ui.button('キャンセル',on_click=dialog.close)
        dialog.open()

    def close_dialog(self):
        self.opened=False
        self._epoch+=1
        if self.native_profile is not None:
            self.native_profile.deactivate()
        if self.following_sync is not None:
            self.following_sync.deactivate()
        self.dialog.close()

    def close(self):
        self.closed=True
        self.close_dialog()
        if self.following_sync is not None:
            self.following_sync.close()
