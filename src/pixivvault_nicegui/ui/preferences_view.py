"""Per-client explicit settings form; unsupported consumers remain unavailable."""
import asyncio
from nicegui import ui
from .premium_recovery_view import PremiumRecoveryPanel
from .. import __version__
from ..preferences import SPECS, SPEC_BY_KEY, PreferencesError
from ..legacy_metadata_export import LegacyMetadataExportError


EXTENSION_STATUS_LABELS = {
    'running': '拡張連携: 受付中',
    'unavailable': '拡張連携: ポートを使用できません。既存アプリの終了後に再起動してください。',
    'in_use': '拡張連携: 他のアプリがポートを使用中です（元のPixivVaultなど）。そのアプリを終了して再起動してください。',
    'commands_unavailable': '拡張連携: 受信の準備に失敗しました。保存先の状態を確認して再起動してください。',
    'setting_off': '拡張連携: 設定で停止中です。次回の起動から受け付けます（設定でONに戻せます）。',
    'disabled': '拡張連携: 無効（Cookieと保存先の設定が必要です）',
}

UNAVAILABLE_REASONS = {
    'bookmark_auto_refresh': 'この起動では自動更新の設定を保存できません。',
    'extension_bridge_enabled': 'この起動には保存先がないため、拡張機能の受信は使えません。',
    'pixiv_actions_enabled': 'この起動ではPixiv操作の設定を変更できません。',
    'api_retry_count': 'この保存経路は停止して確認後に再試行します。通信の自動再送は未対応です。',
    'api_retry_wait': '429は全体障害として停止します。自動待機・再送は未接続です。',
    'download_interval': '通信間隔の適用先がこの起動では接続されていません。',
    'zip_all_after_download': '作者保存完了後の自動ZIP処理は未接続です。',
    'bookmark_download_mode': '現在の管理保存方式では、既存画像のリンク・移動による配置変更に対応していません。',
    'nana7mi_shaft_refresh_interval_hours': '借用プレミアムの保管庫がこの起動では接続されていません。',
    'discovery_cache_limit_mode': '学習画像の先読みキャッシュは未接続です。',
    'discovery_cache_size_mb': '学習画像の先読みキャッシュは未接続です。',
    'discovery_cache_size_count': '学習画像の先読みキャッシュは未接続です。',
    'pixiv_shared_image_cache_size_mb': '全タブ共有キャッシュへの適用は未接続です。',
    'pixiv_disk_thumbnail_cache_size_gb': '専用ディスクキャッシュへの適用は未接続です。',
    'minimize_to_tray': 'Windowsトレイの接続がこの起動では利用できません。',
    'enable_notifications': 'Windows通知の実行依存（win11toast）がこの環境にはありません。',
    'meta_backfill_enabled': 'この起動ではPixivの取得元または専用保存先が接続されていないため、自動後埋めは動きません。',
    'embed_auto_on_download': '解析モデルが未選択、または画像の参照元がないため、自動の推論は動きません。モデルは上の「画像解析・GPU」で選びます。',
}
# 元 gui.py の _settings_item_desc 相当（項目の下に出す説明）。
DESCRIPTIONS = {
    'meta_backfill_enabled': (
        'ブックマーク数・いいね数・閲覧数などは、作品ごとに詳細を1回ずつ取りに行かないと分かりません。'
        'ダウンロード中・取得停止中は動かず、終わるまで待ちます。'),
    'embed_auto_on_download': (
        'ダウンロードした画像から、絵柄と構図の特徴を取り出して保存します。'
        'ダウンロードのついでに少しずつ進み、止めても次に続きから再開します。'
        'オフにすると、自動では一切動きません。'),
}
CHOICE_LABELS = {
    'epub':'EPUBのみ', 'txt':'TXTのみ', 'both':'EPUBとTXT両方',
    'gif':'GIF動画', 'mp4':'MP4動画', 'folder':'フォルダ（フレームとJSON）',
    'bytes':'メモリ使用量', 'count':'画像枚数', 'direct':'直下にフラット配置', 'link':'作者別フォルダに配置',
}
# 値だけのラベル表（CHOICE_LABELS）は '1' '6' '12' '24' を ON/OFF・回数・容量の項目と共有するため、
# 項目ごとの表示は別に持つ（元 gui.py:1290-1297 の文言）。
KEY_CHOICE_LABELS = {
    'nana7mi_shaft_refresh_interval_hours': {
        '1':'1時間ごと', '6':'6時間ごと', '12':'12時間ごと', '24':'24時間ごと (1日)', '48':'48時間ごと (2日)',
    },
}
CACHE_LIMIT_FIELDS = {
    'bytes': 'discovery_cache_size_mb',
    'count': 'discovery_cache_size_count',
}


class PreferencesView:
    def __init__(self, store, *, enabled_keys=(), unavailable_reasons=None, on_change=None, desktop=None, clear_cache=None, extension_status=None, export_legacy=None, export_metadata=None, native_profile=None, following_sync=None, pixiv_actions_mode='readonly', extension_counts=None, extension_reasons=None, extension_snapshot=None, vault=None, similarity_status=None, resume_similarity=None, tagger=None, on_tagger_change=None):
        if pixiv_actions_mode not in ('readonly', 'enabled', 'preferences'):
            raise ValueError('invalid Pixiv action policy')
        self.pixiv_actions_mode=pixiv_actions_mode
        self.store=store
        self.tagger=tagger
        self.on_tagger_change=on_tagger_change
        self.similarity_status=similarity_status
        self.resume_similarity=resume_similarity
        self._similarity_timer=None
        self._tagger_timer=None
        self._tagger_verifying=0
        self._tagger_pending=False
        self.vault=vault
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
        self.extension_counts=extension_counts
        self.extension_reasons=extension_reasons
        self.extension_snapshot=extension_snapshot
        self.clear_cache=clear_cache
        self.export_legacy=export_legacy
        self.export_metadata=export_metadata
        self.native_profile=native_profile
        self.following_sync=following_sync
        self.export_busy=False
        self.export_message=''
        self.export_button=None
        self.export_metadata_button=None
        self.export_status=None
        self.closed=False
        self._epoch=0
        self.opened=False
        self.snapshot=None
        self.controls={}
        self._cache_limit_notes={}
        self.dialog=ui.dialog().props('persistent data-testid=preferences-dialog')
        self.dialog.on('keydown', self._on_dialog_key, js_handler="(event) => { if (event.key === 'Escape') emit({key: event.key}); }")

    def _vault_state_changed(self,label):
        if self.closed:return
        try:
            state=self.vault.status()
            message=('ロック解除済み・検索時にトークンの有効期限を確認します。' if state.get('open') else 'ロック中' if state.get('initialized') else '保管庫は未設定です。')
            if state.get('oauth_review_required'):message+=' 更新結果の確認が必要です。'
            label.set_text(message)
        except Exception:label.set_text('状態を確認できません。')

    def open(self):
        if self.closed: return
        if self._similarity_timer is not None:
            self._similarity_timer.cancel()
            self._similarity_timer=None
        if self._tagger_timer is not None:
            self._tagger_timer.cancel()
            self._tagger_timer=None
        self._tagger_verifying=0
        self._tagger_pending=False
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
            with ui.row().classes('w-full items-center justify-between'):
                ui.label('設定').classes('text-h6 pv-dialog-title')
                ui.button('閉じる', on_click=self.close_dialog).props('data-testid=preferences-close-top')
            if self.native_profile is not None:
                self.native_profile.build()
                ui.separator()
            if self.following_sync is not None:
                self.following_sync.build()
                ui.separator()
            if self.vault is not None:
                ui.label('\u501f\u7528\u30d7\u30ec\u30df\u30a2\u30e0\u306e\u72b6\u614b').classes('text-subtitle1')
                try:
                    state=self.vault.status()
                    message=('\u30ed\u30c3\u30af\u89e3\u9664\u6e08\u307f\u30fb\u691c\u7d22\u6642\u306b\u30c8\u30fc\u30af\u30f3\u306e\u6709\u52b9\u671f\u9650\u3092\u78ba\u8a8d\u3057\u307e\u3059\u3002' if state.get('open') else '\u30ed\u30c3\u30af\u4e2d' if state.get('initialized') else '\u4fdd\u7ba1\u5eab\u306f\u672a\u8a2d\u5b9a\u3067\u3059\u3002')
                    if state.get('oauth_review_required'): message+=' \u66f4\u65b0\u7d50\u679c\u306e\u78ba\u8a8d\u304c\u5fc5\u8981\u3067\u3059\u3002'
                    state_label=ui.label(message).props('data-testid=premium-vault-state')
                    pool=self.vault.premium_pool_status()
                    for slot in pool:
                        ui.label(f"\u767b\u9332{slot['index']}\uff1a\u6255\u3044\u51fa\u30575\u6642\u9593 {slot['payouts']}/{slot['payout_limit']}\u4ef6\u30fb\u501f\u75281\u5206 {slot['borrowed']}/{slot['borrow_limit']}\u4ef6\u30fb\u5f85\u6a5f {int(slot['wait'])}\u79d2").props('data-testid=premium-request-budget')
                    # Leaving an unconfirmed Pixiv result (the "Shaft連携" tab is gone).
                    self.vault_recovery=PremiumRecoveryPanel(self.vault,gate=getattr(self.vault,'premium_authorization',None),
                        on_change=lambda:self._vault_state_changed(state_label))
                except Exception:
                    ui.label('\u72b6\u614b\u3092\u78ba\u8a8d\u3067\u304d\u307e\u305b\u3093\u3002').props('data-testid=premium-vault-state')
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
            if self.tagger is not None:
                ui.label('ローカルタグ補完モデル（任意）').classes('text-subtitle1')
                self.tagger_path=ui.input('モデルフォルダ', value=self.snapshot.values['local_tagger_path']).classes('w-full').props('data-testid=tagger-path')
                self.tagger_enabled=ui.switch('タグ補完を自動で使う', value=self.snapshot.values['local_tagger_enabled']=='1').props('data-testid=tagger-enabled')
                self.tagger_effective=ui.label('状態 検証中').props('role=status data-testid=tagger-effective')
                self.tagger_status=ui.label('検証中').props('data-testid=tagger-status')
                self._tagger_pending=True
                try:
                    loop=asyncio.get_running_loop()
                except RuntimeError:
                    self._tagger_timer=ui.timer(0.1, lambda: self._sync_tagger_status(epoch))
                else:
                    self._tagger_timer=loop.create_task(self._watch_tagger_status(epoch))
                ui.timer(0.1, lambda: self._verify_tagger(epoch), once=True)
            for group in dict.fromkeys(spec.group for spec in SPECS if spec.key != 'grid_card_size_mode' and spec.group != 'local_tagger_internal'):
                ui.label(group).classes('text-subtitle1')
                for spec in SPECS:
                    if spec.group!=group or spec.key == 'grid_card_size_mode': continue
                    key_labels=KEY_CHOICE_LABELS.get(spec.key,{})
                    options={v:key_labels.get(v, CHOICE_LABELS.get(v, ('ON' if v=='1' else 'OFF') if spec.choices==('0','1') else v)) for v in spec.choices}
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
                    if spec.key in DESCRIPTIONS:
                        ui.label(DESCRIPTIONS[spec.key]).classes('text-caption text-grey').props(
                            f'data-testid=preference-description-{spec.key}')
                    if spec.key == 'embed_auto_on_download' and self.similarity_status is not None:
                        self.similarity_status_label=ui.label('').props('role=status data-testid=preferences-similarity-status')
                        self.similarity_resume_button=ui.button('いま推論する', on_click=self._resume_similarity).props('flat data-testid=preferences-similarity-resume')
                        self._refresh_similarity_status()
                        self._similarity_timer=ui.timer(1.0, self._refresh_similarity_status)
                    if spec.key == 'pixiv_actions_enabled':
                        notice=('変更は次回の通常起動から適用されます。Cookieの参照先が必要です。'
                                if spec.key in self.enabled_keys else self.reasons[spec.key])
                        ui.label(notice).classes('text-caption text-grey').props('data-testid=preference-pixiv-actions-mode')
                    elif spec.key == 'extension_bridge_enabled' and spec.key in self.enabled_keys:
                        ui.label('変更は次回の起動から適用されます。実行中の受信は止めません。127.0.0.1（このPC内）だけで受け付けます。').classes(
                            'text-caption text-grey').props('data-testid=preference-extension-bridge-note')
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
                label=EXTENSION_STATUS_LABELS.get(self.extension_status(),'拡張連携: 状態不明')
                ui.label(label).props('role=status data-testid=extension-status')
                if self.extension_snapshot is not None or self.extension_counts is not None:
                    counts, reasons = self.extension_snapshot() if self.extension_snapshot is not None else (self.extension_counts(), self.extension_reasons() if self.extension_reasons is not None else {})
                    ui.label(f"拡張応答（設定を開いた時点）: 200 {counts.get(200,0)} / 400 {counts.get(400,0)} / 503 {counts.get(503,0)}").props('data-testid=extension-counts')
                    if self.extension_snapshot is not None or self.extension_reasons is not None:
                        labels={'framing':'形式不正','invalid_cookie':'Cookie形式不正','invalid_command':'コマンド不正','status_unavailable':'状態取得不可','queue_unavailable':'キュー利用不可','cookie_unavailable':'Cookie受付未確認','cookie_received':'Cookie受付','accepted':'正常応答','unknown':'理由不明'}
                        ui.label('内訳: ' + (' / '.join(f'{status} {labels.get(reason, "理由不明")} {count}' for (status,reason),count in sorted(reasons.items())) or '応答なし')).props('data-testid=extension-reasons')
            if self.desktop is not None and getattr(self.desktop,'protocol',None) is not None:
                ui.button('ブラウザ拡張機能の自動起動を設定',on_click=self._uri_prompt).props('data-testid=preferences-uri')
            else:
                ui.label('ブラウザ拡張機能の自動起動: URI起動経路が未接続です。').classes('text-caption')
            ui.label('エクスポート').classes('text-subtitle1')
            ui.label('データベースのエクスポート').classes('text-caption')
            ui.label('データベースと作品メタデータをZIPに保存します（画像は含まれません）。').classes('text-caption')
            self.export_metadata_button=ui.button('エクスポートを実行', icon='archive',
                on_click=self.run_metadata_export).props('data-testid=preferences-export-metadata')
            self.export_metadata_button.set_enabled(self.export_metadata is not None and not self.export_busy)
            ui.label('旧データベース、作品メタデータ、指定した画像フォルダーをZIPに保存します。').classes('text-caption')
            ui.label('同じ画像は1回だけ保存します。全ての元のパスに復元するには、ZIP内の RESTORE_IMAGES.txt の手順を使ってください。').classes('text-caption')
            self.export_button=ui.button('DBと画像をZIPへ保存',on_click=self.run_legacy_export).props(
                'data-testid=preferences-export-db')
            self.export_button.set_enabled(self.export_legacy is not None and not self.export_busy)
            if self.export_legacy is None:
                ui.label('この起動では読取専用の旧DBと専用設定保存先の両方が必要です。').classes('text-caption text-grey')
            self.export_status=ui.label(self.export_message).props('data-testid=preferences-export-status role=status')
            # 元 gui.py:2420: 閉じるボタンの上に、中央寄せの小さいバージョン表記。
            ui.label(f'ver {__version__}').props('data-testid=preferences-version').style(
                'font-size:11px;color:var(--pv-muted);text-align:center;width:100%')
            with ui.row():
                ui.button('保存',on_click=lambda:self.save() if self._epoch==epoch and self.opened else None).props('data-testid=preferences-save').set_enabled(bool(self.enabled_keys) or self.tagger is not None)
                ui.button('変更を破棄して再読込',on_click=self.open).props('data-testid=preferences-reload')
                ui.button('閉じる',on_click=self.close_dialog)
        self.dialog.open()

    async def _verify_tagger(self, epoch):
        if self.closed or not self.opened or epoch != self._epoch or self.tagger is None:
            return
        self._tagger_pending=False
        self._tagger_verifying+=1
        self._display_tagger_status('検証中')
        try:
            result = await asyncio.to_thread(self.tagger.verify)
            latest = self.store.snapshot()
            if self.snapshot is not None and all(latest.values[k] == v for k, v in self.snapshot.values.items() if k != 'local_tagger_receipt'):
                self.snapshot = latest
        except Exception:
            result = '読込失敗'
        finally:
            if epoch == self._epoch:
                self._tagger_verifying-=1
        if not self.closed and self.opened and epoch == self._epoch:
            self._display_tagger_status(result)

    def _display_tagger_status(self, result):
        configured = self.snapshot.values['local_tagger_enabled'] == '1'
        usable = getattr(self.tagger, 'usable', configured and result == '利用可能')
        state = '検証中' if result == '検証中' else ('有効' if usable and result == '利用可能' else '無効')
        self.tagger_effective.set_text(f'状態 {state}')
        self.tagger_status.set_text(result)

    async def _watch_tagger_status(self, epoch):
        while not self.closed and self.opened and epoch == self._epoch:
            await asyncio.sleep(0.1)
            self._sync_tagger_status(epoch)

    def _sync_tagger_status(self, epoch):
        if not self.closed and self.opened and epoch == self._epoch and not self._tagger_verifying and not self._tagger_pending:
            self._display_tagger_status(self.tagger.status)

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

    async def run_metadata_export(self):
        await self.run_legacy_export(include_images=False)

    async def run_legacy_export(self, *, include_images=True):
        """Use one in-flight export per view for both archive choices."""
        export = self.export_legacy if include_images else self.export_metadata
        if self.closed or not self.opened or self.export_busy or export is None:
            return
        self.export_busy=True
        self.export_message='エクスポート中…'
        self.export_button.set_enabled(False)
        self.export_metadata_button.set_enabled(False)
        self.export_status.set_text(self.export_message)
        succeeded=False
        cleanup_warning=False
        try:
            result=await asyncio.to_thread(export)
            cleanup_warning=bool(getattr(result,'cleanup_warning',False))
            prefix='[完了・一時フォルダーの後始末要確認]' if cleanup_warning else '[完了]'
            contents = '旧DB・メタデータ・画像フォルダー' if include_images else '旧DB・メタデータのみ'
            self.export_message=f'{prefix} {result.path}（{contents}、作品 {result.works} 件）'
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
                    self.export_metadata_button.set_enabled(self.export_metadata is not None)
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
        if self.tagger is not None:
            for key, value in (('local_tagger_path', self.tagger_path.value or ''),
                               ('local_tagger_enabled', '1' if self.tagger_enabled.value else '0')):
                if value != self.snapshot.values[key]: changes[key]=value
        try:
            snapshot=self.store.update(changes,expected_revision=self.snapshot.revision)
        except PreferencesError as exc:
            self.status.set_text(str(exc))
            return False
        except Exception:
            self.status.set_text('設定を保存できません。入力を保持しています。')
            return False
        self.snapshot=snapshot
        if self.tagger is not None and any(key.startswith('local_tagger_') for key in changes):
            self._tagger_pending=True
            self._display_tagger_status('検証中')
            ui.timer(0.1, lambda: self._verify_tagger(self._epoch), once=True)
        try:
            if self.on_change and changes: self.on_change(snapshot)
            if self.on_tagger_change and any(key in changes for key in ('local_tagger_path', 'local_tagger_enabled')):
                self.on_tagger_change()
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

    def _on_dialog_key(self, event):
        if self.opened and not self.closed and not self.export_busy and event.args == {'key': 'Escape'}:
            self.close_dialog()

    def _refresh_similarity_status(self):
        from .similar_view import SimilarView
        try:
            status=self.similarity_status()
            reason=SimilarView.STOP_REASONS.get(status.get('last_stopped_reason'))
            if reason and status.get('phase') == 'failed':
                text=reason
            elif reason and status.get('auto') is True:
                text=f'前回は次の理由で止まりました: {reason}。ダウンロードのたびに再挑戦しています'
            elif reason:
                text=f'前回は次の理由で止まりました: {reason}。『いま推論する』で再開できます'
            else:
                text=''
            self.similarity_status_label.set_text(text)
            self.similarity_resume_button.set_enabled(self.resume_similarity is not None)
        except Exception:
            self.similarity_status_label.set_text('推論の状態を確認できません。')
            self.similarity_resume_button.set_enabled(False)

    def _resume_similarity(self):
        if self.opened and not self.closed and self.resume_similarity is not None:
            self.resume_similarity()
            self._refresh_similarity_status()

    def close_dialog(self):
        if (not self.closed and self.opened and self.snapshot is not None
                and any(self.controls[key].value != self.snapshot.values[key]
                        for key in self.enabled_keys)):
            if not self.save():
                return
        self.opened=False
        if self._tagger_timer is not None:
            self._tagger_timer.cancel()
            self._tagger_timer=None
        if self._similarity_timer is not None:
            self._similarity_timer.cancel()
            self._similarity_timer=None
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
