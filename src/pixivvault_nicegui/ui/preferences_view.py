"""Per-client explicit settings form; unsupported consumers remain unavailable."""
import asyncio
from contextlib import contextmanager
from nicegui import ui
from .management_dialog import managed_dialog
from .action_controls import (
    danger_button, dialog_actions, dialog_card, notify_action, outlined_input, outlined_select, primary_button, secondary_button,
)
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
        'ダウンロードに合わせて進み、次回は続きから再開します。'),
}
CHOICE_LABELS = {
    'epub':'EPUBのみ', 'txt':'TXTのみ', 'both':'EPUBとTXT両方',
    'gif':'GIF動画', 'mp4':'MP4動画', 'folder':'フォルダ（フレームとJSON）',
    'bytes':'メモリ使用量', 'count':'画像枚数', 'direct':'直下にフラット配置', 'link':'作者別フォルダに配置',
}
# 値だけのラベル表（CHOICE_LABELS）は '1' '6' '12' '24' を ON/OFF・回数・容量の項目と共有するため、
# 項目ごとの表示は別に持つ（元 gui.py:1290-1297 の文言）。
KEY_CHOICE_LABELS = {
    'visual_preference_mode': {'idle': 'アイドル時に少しずつ', 'manual': '手動のみ'},
    'nana7mi_shaft_refresh_interval_hours': {
        '1':'1時間ごと', '6':'6時間ごと', '12':'12時間ごと', '24':'24時間ごと (1日)', '48':'48時間ごと (2日)',
    },
}
PREFERENCE_GROUP_TITLES = {
    '保存': '保存ポリシー・通知',
    'enable_notifications': '保存ポリシー・通知',
    '通信': '詳細設定',
    'Windows': '詳細設定',
}
CACHE_LIMIT_FIELDS = {
    'bytes': 'discovery_cache_size_mb',
    'count': 'discovery_cache_size_count',
}


@contextmanager
def settings_group(title, *, collapsible=False, default_open=True, heading_testid=None, classes=''):
    with ui.column().classes(f'pv-preferences-group {classes}'):
        if collapsible:
            with ui.expansion(title, value=default_open).props('dense header-class=pv-focus-ring').classes('w-full pv-settings-expansion') as expansion:
                if heading_testid is not None:
                    with expansion.add_slot('header'), ui.item_section():
                        heading = ui.label(title).classes('q-item__label')
                        heading.props['data-testid'] = heading_testid
                yield expansion
        else:
            heading = ui.label(title).classes('pv-section-heading pv-serif-heading')
            if heading_testid is not None:
                heading.props['data-testid'] = heading_testid
            yield


TAGGER_ALERT_STATUSES = frozenset({
    '未検出', '読込失敗', 'SHA 不一致', '利用不可（ローカルのフォルダを指定）', 'onnxruntime なし',
})


def visual_status_text(*, phase, error, indexed, pending, generation, worker_available,
                       waiting_for_lease, provider='cuda', analyzing=False, pause_reason=None, **_):
    reasons = {
        'scope_failure': '視覚学習データを読み込めません',
        'lease_failure': '共有 GPU リースを取得できません',
        'lease_release_failure': '共有 GPU リースの解放を確認できません',
        'cuda_failure': 'CUDA 推論に失敗しました',
        'cpu_failure': 'CPU 推論に失敗しました',
        'worker_failure': '視覚学習の解析に失敗しました',
        'thread_start': '解析を開始できません',
    }
    reason = reasons.get(error)
    if waiting_for_lease and phase == 'running':
        reason = '共有 GPU の空き待ち'
    elif phase in ('paused', 'pausing'):
        reason = reason or {
            'disabled': '視覚補正をオフにしました',
            'manual': '実行タイミングを手動に変更しました',
            'cleared': '視覚学習データを削除しました',
            'shared_stop': '共有 GPU の解放が要求されました',
            'user': '停止ボタンで停止しました',
        }.get(pause_reason, '解析を停止しました')
    elif not worker_available:
        reason = reason or '確認済みのアカウントと推論モデルが必要です'
    text = ('一時停止中' if phase in ('paused', 'pausing') else
            '解析中' if phase == 'running' and analyzing and not waiting_for_lease else '待機中')
    device = 'CPU' if provider == 'cpu' else 'GPU（CUDA）'
    parts = [text, f'解析済み {indexed} 件（残り {pending} 件）', f'学習世代 {generation}',
             f'実行先 {device}', '追加 GET 0 件']
    if reason:
        parts.append(reason)
    return ' ／ '.join(parts)


def _set_status_alert(label, active, *, normal_role=None):
    if active:
        label.classes(add='pv-alert-banner')
    else:
        label.classes(remove='pv-alert-banner')
    label.props.set_optional('role', 'alert' if active else normal_role)


class PreferencesView:
    def __init__(self, store, *, enabled_keys=(), unavailable_reasons=None, on_change=None, desktop=None, clear_cache=None, extension_status=None, export_legacy=None, export_metadata=None, native_profile=None, following_sync=None, pixiv_actions_mode='readonly', extension_counts=None, extension_reasons=None, extension_snapshot=None, vault=None, similarity_status=None, resume_similarity=None, tagger=None, on_tagger_change=None, visual_service=None, build_automation=None, build_learning_reset=None):
        if pixiv_actions_mode not in ('readonly', 'enabled', 'preferences'):
            raise ValueError('invalid Pixiv action policy')
        self.pixiv_actions_mode=pixiv_actions_mode
        self.store=store
        self.tagger=tagger
        self.on_tagger_change=on_tagger_change
        self.similarity_status=similarity_status
        self.resume_similarity=resume_similarity
        self._similarity_timer=None
        self.visual_service = visual_service
        self.build_automation = build_automation
        self.build_learning_reset = build_learning_reset
        self.learning_reset = None
        self._visual_timer = None
        self._visual_confirm = None
        self._visual_clear_target = None
        self._visual_busy = False
        self._visual_action_error = None
        self._tagger_timer=None
        self._tagger_verifying=0
        self._tagger_pending=False
        self._tagger_verification_failed = False
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
        self.export_includes_images=False
        self.export_button=None
        self.export_metadata_button=None
        self.export_status=None
        self.export_notices=None
        self.closed=False
        self._epoch=0
        self.opened=False
        self.snapshot=None
        self.controls={}
        self._cache_limit_notes={}
        self._cache_limit_fields={}
        self.dialog=managed_dialog().props('persistent no-shake data-testid=preferences-dialog')
        self.dialog.on('keyup', self._on_dialog_key, js_handler="(event) => { if (event.key === 'Escape') emit({key: event.key}); }")

    def _vault_state_changed(self, label, group):
        if self.closed:
            return
        try:
            state = self.vault.status()
            message = ('ロック解除済み・検索時にトークンの有効期限を確認します。' if state.get('open') else
                       'ロック中' if state.get('initialized') else '保管庫は未設定です。')
            alert = bool(state.get('oauth_review_required'))
            if alert:
                message += ' 更新結果の確認が必要です。'
        except Exception:
            message = '状態を確認できません。'
            alert = True
        _set_status_alert(label, alert)
        label.set_text(message)
        if alert:
            group.open()

    def open(self):
        if self.closed or (self.learning_reset is not None and self.learning_reset.busy):
            return
        if self.learning_reset is not None:
            self.learning_reset.close()
            self.learning_reset = None
        self._close_visual_controls()
        self._visual_action_error = None
        if self._similarity_timer is not None:
            self._similarity_timer.cancel()
            self._similarity_timer=None
        if self._tagger_timer is not None:
            self._tagger_timer.cancel()
            self._tagger_timer=None
        self._tagger_verifying=0
        self._tagger_pending=False
        self._tagger_verification_failed = False
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
        with self.dialog, dialog_card(size='lg', classes='pv-preferences-card pv-settings-modal') as card:

            with ui.row().classes('w-full items-center pv-preferences-top'):
                title = ui.label('設定').classes('pv-dialog-title').props('role=heading aria-level=1 tabindex=-1 data-autofocus')
                self.dialog.props['aria-labelledby'] = title.html_id
            message = ''
            with ui.column().classes('w-full pv-preferences-content'):
                if self.native_profile is not None:
                    with settings_group('アカウント'):
                        self.native_profile.build(embedded=True)
                if self.following_sync is not None:
                    with settings_group('フォロー中リストの同期', collapsible=True, default_open=False, classes='pv-settings-follow'):
                        self.following_sync.build(embedded=True)
                try:
                    self.snapshot = self.store.snapshot()
                except Exception:
                    message = '設定を読み込めません。保存は行いません。'
                if self.snapshot is not None:
                    self._build_settings(epoch)
                self._build_vault_settings()
                if self.build_learning_reset is not None:
                    with settings_group('学習状況', heading_testid='preferences-learning-heading'):
                        self.learning_reset = self.build_learning_reset(
                            lambda: self.opened and self._epoch == epoch)
            with ui.column().classes('w-full pv-preferences-footer'):
                self.status = ui.label(message).classes('pv-subtitle pv-compact-status').props(
                    'data-testid=preferences-status role=status')
                ui.label(f'ver {__version__}').props('data-testid=preferences-version').style(
                    'font-size:11px;color:var(--pv-muted);text-align:center;width:100%')
                with dialog_actions():
                    secondary_button('変更を破棄して再読込', self.open, icon='refresh', testid='preferences-reload')
                    secondary_button('閉じる', self.close_dialog, icon='close', testid='preferences-close-top')
                    save_button = primary_button(
                        '保存', lambda:self.save() if self._epoch == epoch and self.opened else None,
                        icon='save', testid='preferences-save')
                    save_button.set_enabled(self.snapshot is not None and (bool(self.enabled_keys) or self.tagger is not None))
        self.dialog.open()

    def _build_settings(self, epoch):
        application_notice = (
            'Pixiv操作の設定は次回起動から、その他の設定は次の操作から使われます。'
            if 'pixiv_actions_enabled' in self.enabled_keys else
            '変更は専用設定に保存され、次の操作から使われます。')
        ui.label(application_notice + '実行中のジョブは変更しません。').classes('pv-field-hint')
        groups = {}
        for spec in SPECS:
            if spec.group in {'local_tagger_internal', 'ui_internal'} or spec.key == 'grid_card_size_mode':
                continue
            title = PREFERENCE_GROUP_TITLES.get(spec.key, PREFERENCE_GROUP_TITLES.get(spec.group, spec.group))
            groups.setdefault(title, []).append(spec)
        self._build_preference_group('保存ポリシー・通知', groups.pop('保存ポリシー・通知', ()),
                                     hint='静止画保存: 保存元の原画像を維持します。画質を変更する設定はありません。')
        for title in ('作品メタデータ', '絵柄・構図の推論'):
            self._build_preference_group(title, groups.pop(title, ()))
        if self.build_automation is not None:
            with settings_group('自動処理', heading_testid='preferences-automation-heading'):
                self.build_automation()
        with settings_group('エクスポート', heading_testid='preferences-export-heading'):
            ui.label('データベースのエクスポート').classes('pv-field-hint')
            ui.label('データベースと作品メタデータをZIPに保存します（画像は含まれません）。').classes('pv-field-hint')
            self.export_metadata_button=secondary_button('エクスポートを実行', self.run_metadata_export, icon='archive',
                testid='preferences-export-metadata')
            self.export_metadata_button.set_enabled(self.export_metadata is not None and not self.export_busy)
            metadata_result = ui.column().classes('w-full gap-0 pv-hide-empty')
            ui.label('旧データベース、作品メタデータ、指定した画像フォルダーをZIPに保存します。').classes('pv-field-hint')
            ui.label('同じ画像は1回だけ保存します。全ての元のパスに復元するには、ZIP内の RESTORE_IMAGES.txt の手順を使ってください。').classes('pv-field-hint')
            self.export_button=secondary_button('DBと画像をZIPへ保存', self.run_legacy_export, icon='archive',
                testid='preferences-export-db')
            self.export_button.set_enabled(self.export_legacy is not None and not self.export_busy)
            archive_result = ui.column().classes('w-full gap-0 pv-hide-empty')
            if self.export_legacy is None:
                ui.label('この起動では読取専用の旧DBと専用設定保存先の両方が必要です。').classes('pv-field-hint')
            self.export_results = {False: metadata_result, True: archive_result}
            with self.export_results[self.export_includes_images], ui.column().classes('w-full gap-0') as self.export_result:
                self.export_status=ui.label(self.export_message).classes('pv-compact-status').props('data-testid=preferences-export-status role=status')
                self.export_notices=ui.column().classes('w-full gap-0 pv-hide-empty')

        self._build_preference_group('詳細設定', groups.pop('詳細設定', ()))
        with settings_group('ブラウザ拡張機能', collapsible=True, default_open=False):
            if self.extension_status is not None:
                label=EXTENSION_STATUS_LABELS.get(self.extension_status(),'拡張連携: 状態不明')
                ui.label(label).classes('pv-subtitle').props('role=status data-testid=extension-status')
                if self.extension_snapshot is not None or self.extension_counts is not None:
                    counts, reasons = self.extension_snapshot() if self.extension_snapshot is not None else (self.extension_counts(), self.extension_reasons() if self.extension_reasons is not None else {})
                    ui.label(f"拡張応答（設定を開いた時点）: 200 {counts.get(200,0)} / 400 {counts.get(400,0)} / 503 {counts.get(503,0)}").props('data-testid=extension-counts')
                    if self.extension_snapshot is not None or self.extension_reasons is not None:
                        labels={'framing':'形式不正','invalid_cookie':'Cookie形式不正','invalid_command':'コマンド不正','status_unavailable':'状態取得不可','queue_unavailable':'キュー利用不可','cookie_unavailable':'Cookie受付未確認','cookie_received':'Cookie受付','accepted':'正常応答','unknown':'理由不明'}
                        ui.label('内訳: ' + (' / '.join(f'{status} {labels.get(reason, "理由不明")} {count}' for (status,reason),count in sorted(reasons.items())) or '応答なし')).props('data-testid=extension-reasons')
            if self.desktop is not None and getattr(self.desktop,'protocol',None) is not None:
                secondary_button('ブラウザ拡張機能の自動起動を設定', self._uri_prompt, testid='preferences-uri')
            else:
                ui.label('ブラウザ拡張機能の自動起動: URI起動経路が未接続です。').classes('pv-field-hint')

        self._build_preference_group('キャッシュ', groups.pop('キャッシュ', ()))
        for title, specs in groups.items():
            self._build_preference_group(title, specs)
        with settings_group('画像キャッシュ'):
            secondary_button('画像キャッシュを削除', lambda:self._clear_cache(epoch), icon='delete_outline', testid='preferences-clear-cache').set_enabled(self.clear_cache is not None)
            ui.label('アプリ全体で表示した作品・作者のサムネイル画像キャッシュを削除します。保存した作品は削除しません。').classes('pv-field-hint')
        if self.tagger is not None:
            with settings_group('ローカルタグ補完モデル（任意）', collapsible=True, default_open=False) as self.tagger_group:
                self.tagger_path=outlined_input('モデルフォルダ', value=self.snapshot.values['local_tagger_path']).classes('w-full').props('data-testid=tagger-path input-class=pv-focus-ring')
                self.tagger_path.props['title'] = self.tagger_path.value or ''
                self.tagger_path.on_value_change(lambda event: event.sender.props.update(title=event.value or ''))
                self.tagger_enabled=ui.switch('タグ補完を自動で使う', value=self.snapshot.values['local_tagger_enabled']=='1').classes('pv-filter-switch pv-hit28').props('data-testid=tagger-enabled')
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

        self.controls['discovery_cache_limit_mode'].on_value_change(
            lambda _: self._sync_cache_limit_visibility()
            if self.opened and not self.closed and self._epoch == epoch else None)
        self._sync_cache_limit_visibility()

    def _build_vault_settings(self):
        if self.vault is None:
            return
        with settings_group('借用プレミアムの状態', collapsible=True, default_open=False) as group:
            state_label = ui.label('').classes('pv-subtitle').props('data-testid=premium-vault-state')
            self._vault_state_changed(state_label, group)
            try:
                pool = self.vault.premium_pool_status()
            except Exception:
                group.open()
                ui.label('利用回数を確認できません。').classes('pv-subtitle pv-alert-banner').props(
                    'role=alert data-testid=premium-vault-budget-error')
            else:
                for slot in pool:
                    ui.label(f"登録{slot['index']}：払い出し5時間 {slot['payouts']}/{slot['payout_limit']}件・借用1分 {slot['borrowed']}/{slot['borrow_limit']}件・待機 {int(slot['wait'])}秒").props('data-testid=premium-request-budget')
            self.vault_recovery = PremiumRecoveryPanel(
                self.vault, gate=getattr(self.vault, 'premium_authorization', None),
                on_change=lambda: self._vault_state_changed(state_label, group))

    def _build_preference_group(self, title, specs, *, hint=None):
        visual = title == '絵柄・構図の推論' and self.visual_service is not None
        with settings_group(title):
            if hint:
                ui.label(hint).classes('pv-field-hint')
            with ui.element('div').classes('pv-settings-fields'):
                for spec in specs:
                    wide = (spec.key in ('meta_backfill_enabled', 'embed_auto_on_download', 'extension_bridge_enabled')
                            or spec.key.startswith('visual_preference_'))
                    with ui.column().classes('pv-settings-field' + (' pv-settings-wide' if wide else '')) as field:
                        if spec.key in CACHE_LIMIT_FIELDS.values():
                            self._cache_limit_fields[spec.key] = field
                        value = self.snapshot.values[spec.key]
                        if spec.key == 'pixiv_actions_enabled' and self.pixiv_actions_mode != 'preferences':
                            value = '1' if self.pixiv_actions_mode == 'enabled' else '0'
                        if spec.choices == ('0', '1'):
                            # Keep the QToggle model identical to the stored/acknowledged values.
                            control = ui.switch(spec.label, value=value).classes('pv-filter-switch pv-hit28').props(
                                'dense true-value=1 false-value=0')
                        else:
                            labels = KEY_CHOICE_LABELS.get(spec.key, {})
                            options = {v: labels.get(v, CHOICE_LABELS.get(v, v)) for v in spec.choices}
                            control = outlined_select(options, label=spec.label, value=value).classes('w-full')
                        control.props['data-testid'] = f'preference-{spec.key}'
                        control.props['data-value']=str(control.value)
                        def acknowledge(event, control=control):
                            control.props['data-value']=str(event.value)
                            control.update()
                        control.on_value_change(acknowledge)
                        control.set_enabled(spec.key in self.enabled_keys)
                        self.controls[spec.key]=control
                        if spec.key in DESCRIPTIONS:
                            ui.label(DESCRIPTIONS[spec.key]).classes('pv-field-hint').props(
                                f'data-testid=preference-description-{spec.key}')
                        if spec.key == 'embed_auto_on_download' and self.similarity_status is not None:
                            self.similarity_status_label=ui.label('').props('role=status data-testid=preferences-similarity-status')
                            self.similarity_resume_button=secondary_button('いま推論する', self._resume_similarity, testid='preferences-similarity-resume')
                            self._refresh_similarity_status()
                            self._similarity_timer=ui.timer(1.0, self._refresh_similarity_status)
                        if spec.key == 'visual_preference_thumb_prefetch':
                            ui.label('追加取得はまだ利用できません。現在の解析に使うのは取得済みの画像です。').classes('pv-field-hint')
                        if spec.key == 'pixiv_actions_enabled':
                            notice=('変更は次回の通常起動から適用されます。Cookieの参照先が必要です。'
                                    if spec.key in self.enabled_keys else self.reasons[spec.key])
                            ui.label(notice).classes('pv-field-hint').props('data-testid=preference-pixiv-actions-mode')
                        elif spec.key == 'extension_bridge_enabled' and spec.key in self.enabled_keys:
                            ui.label('変更は次回の起動から適用されます。実行中の受信は止めません。127.0.0.1（このPC内）だけで受け付けます。').classes(
                                'pv-field-hint').props('data-testid=preference-extension-bridge-note')
                        elif spec.key == 'bookmark_download_mode' and spec.key in self.enabled_keys:
                            ui.label('設定保存だけでは通信しません。通常起動では、マイブックマークの「再読込」が成功してから、そのアカウントの保存配置に適用します。').classes(
                                'pv-field-hint').props('data-testid=preference-bookmark-account-gate')
                        elif spec.key not in self.enabled_keys:
                            note=ui.label(self.reasons.get(spec.key,'この起動では適用先が接続されていません。')).classes('pv-field-hint').props(
                                f'data-testid=preference-unavailable-{spec.key}')
                            if spec.key in CACHE_LIMIT_FIELDS.values():
                                self._cache_limit_notes[spec.key]=note

            if visual:
                with settings_group('視覚学習の操作', collapsible=True) as group:
                    self._build_visual_controls(group, self._epoch)

    def _build_visual_controls(self, group, epoch):
        self.visual_status_label = ui.label('待機中').classes('pv-subtitle').props(
            'data-testid=preferences-visual-status role=status aria-live=polite')
        self._visual_group = group
        with dialog_actions():
            self.visual_start = primary_button('いま解析する', self._start_visual,
                                               testid='preferences-visual-start')
            self.visual_pause = secondary_button('解析を停止', self._pause_visual,
                                                 testid='preferences-visual-pause')
            self.visual_clear = secondary_button('視覚学習データを削除…', self._confirm_visual_clear,
                                                 testid='preferences-visual-clear').props('aria-haspopup=dialog')
        for button in (self.visual_start, self.visual_pause, self.visual_clear):
            button.set_enabled(False)

        async def refresh():
            await self._refresh_visual_status(epoch)

        self._visual_timer = ui.timer(1.0, refresh)

    async def _refresh_visual_status(self, epoch):
        if self.closed or not self.opened or epoch != self._epoch:
            return
        state = await asyncio.to_thread(self.visual_service.visual_preference_status)
        if self.closed or not self.opened or epoch != self._epoch:
            return
        text = visual_status_text(**state)
        if self._visual_action_error:
            text += ' ／ ' + self._visual_action_error
        if self.visual_status_label.text != text:
            self.visual_status_label.set_text(text)
            alert = bool(state['error'] or self._visual_action_error)
            _set_status_alert(self.visual_status_label, alert, normal_role='status')
            self.visual_status_label.style(replace=(
                'color:var(--pv-text);border-color:var(--pv-error-text)' if alert else ''))
            if alert:
                self._visual_group.open()
        phase = state['phase']
        self._visual_clear_target = state['clear_target']
        can_start = phase not in ('running', 'pausing', 'stopping') or (
            phase == 'running' and state.get('automatic', False))
        self.visual_start.set_enabled(not self._visual_busy and state['worker_available'] and can_start)
        self.visual_pause.set_enabled(not self._visual_busy and state['can_clear'] and phase == 'running')
        self.visual_clear.set_enabled(not self._visual_busy and state['can_clear'])

    async def _visual_action(self, action, error, *args):
        if self.closed or not self.opened or self._visual_busy:
            return
        epoch = self._epoch
        self._visual_busy = True
        self._visual_action_error = None
        try:
            await asyncio.to_thread(action, *args)
        except Exception:
            if self._epoch == epoch:
                self._visual_action_error = error
        finally:
            self._visual_busy = False
            await self._refresh_visual_status(epoch)

    async def _start_visual(self):
        await self._visual_action(self.visual_service.start_visual_preference, '解析を開始できませんでした。')

    async def _pause_visual(self):
        await self._visual_action(self.visual_service.pause_visual_preference, '解析の停止を確認できませんでした。')

    def _confirm_visual_clear(self):
        if self._visual_busy or self._visual_confirm is not None:
            return
        expected = self._visual_clear_target
        if expected is None:
            return
        epoch = self._epoch
        with managed_dialog() as dialog, dialog_card('視覚学習データを削除', size='sm'):
            dialog.props['data-testid'] = 'preferences-visual-clear-dialog'
            dialog.props['aria-label'] = '視覚学習データを削除'
            ui.label('保存した作品・判定履歴・「似た絵を探す」の解析台帳は削除しません。').classes('pv-subtitle')
            with dialog_actions():
                secondary_button('キャンセル', dialog.close, testid='preferences-visual-clear-cancel').props('autofocus')

                async def clear():
                    dialog.close()
                    if self._epoch == epoch:
                        await self._visual_action(self.visual_service.clear_visual_preference,
                                                  '視覚学習データの削除を確認できませんでした。', expected)

                danger_button('削除する', clear, testid='preferences-visual-clear-confirm')

        def on_hide():
            if self._visual_confirm is dialog:
                self._visual_confirm = None
            dialog.delete()

        dialog.on('hide', on_hide)
        self._visual_confirm = dialog
        dialog.open()

    def _close_visual_controls(self):
        self._visual_clear_target = None
        if self._visual_timer is not None:
            self._visual_timer.cancel()
            self._visual_timer = None
        if self._visual_confirm is not None:
            self._visual_confirm.close()
            self._visual_confirm = None

    async def _verify_tagger(self, epoch):
        if self.closed or not self.opened or epoch != self._epoch or self.tagger is None:
            return
        self._tagger_pending=False
        self._tagger_verifying+=1
        self._display_tagger_status('検証中')
        failed = False
        try:
            result = await asyncio.to_thread(self.tagger.verify)
            latest = self.store.snapshot()
            if self.snapshot is not None and all(latest.values[k] == v for k, v in self.snapshot.values.items() if k != 'local_tagger_receipt'):
                self.snapshot = latest
        except Exception:
            result = '読込失敗'
            failed = True
        finally:
            if epoch == self._epoch:
                self._tagger_verifying-=1
        if not self.closed and self.opened and epoch == self._epoch:
            self._tagger_verification_failed = failed
            self._display_tagger_status(result)

    def _display_tagger_status(self, result):
        configured = self.snapshot.values['local_tagger_enabled'] == '1'
        usable = getattr(self.tagger, 'usable', configured and result == '利用可能')
        state = '検証中' if result == '検証中' else ('有効' if usable and result == '利用可能' else '無効')
        self.tagger_effective.set_text(f'状態 {state}')
        if self.tagger_status.text == result:
            return
        alert = result in TAGGER_ALERT_STATUSES
        _set_status_alert(self.tagger_status, alert)
        self.tagger_status.set_text(result)
        if alert:
            self.tagger_group.open()

    async def _watch_tagger_status(self, epoch):
        while not self.closed and self.opened and epoch == self._epoch:
            await asyncio.sleep(0.1)
            self._sync_tagger_status(epoch)

    def _sync_tagger_status(self, epoch):
        if not self.closed and self.opened and epoch == self._epoch and not self._tagger_verifying and not self._tagger_pending:
            # An exception can leave the service's published status unchanged.
            result = '読込失敗' if self._tagger_verification_failed else self.tagger.status
            self._display_tagger_status(result)

    def _sync_cache_limit_visibility(self):
        # Keep both draft values and consumer capabilities. Only the selected
        # unit and its unavailable reason belong in the visible form.
        mode=self.controls['discovery_cache_limit_mode'].value
        for unit,key in CACHE_LIMIT_FIELDS.items():
            visible=mode == unit
            self._cache_limit_fields[key].set_visibility(visible)
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
        if self.export_includes_images != include_images:
            self.export_includes_images=include_images
            self.export_result.move(self.export_results[include_images])
        self.export_notices.clear()
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
                notices = self.export_notices if self.opened and not self.export_notices.is_deleted else None
                return_focus = None
                if notices is not None:
                    self.export_button.set_enabled(self.export_legacy is not None)
                    self.export_metadata_button.set_enabled(self.export_metadata is not None)
                    self.export_status.set_text(self.export_message)
                    return_focus = self.export_button if include_images else self.export_metadata_button
                with self.dialog:
                    if cleanup_warning:
                        notify_action('エクスポートは完了しましたが、一時フォルダーを確認してください。',
                                      type='warning', important=True, within_dialog=notices, return_focus=return_focus)
                    elif succeeded:
                        notify_action('エクスポートが完了しました。', type='positive')
                    else:
                        result_notice = notify_action('エクスポートに失敗しました。', type='negative',
                                                      within_dialog=notices, return_focus=return_focus)
                        if notices is not None:
                            alert, _, _ = result_notice
                            ui.run_javascript(
                                f"document.getElementById('{alert.html_id}')?.scrollIntoView({{block:'nearest'}})")

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
        with managed_dialog().props('data-testid=preferences-uri-dialog aria-label=ブラウザ拡張機能の自動連携') as dialog, dialog_card(
                'ブラウザ拡張機能の自動連携', classes='pv-settings-modal',
                subtitle='Windowsのユーザー設定にpixivvault URLスキームを登録します。解除すると登録前の状態に戻ります。'):

            result=ui.label('').props('role=status')
            def apply(enabled):
                if self.closed or not self.opened or epoch!=self._epoch:return
                try:
                    self.desktop.set_uri_registration(enabled,confirmed=True)
                    result.set_text('有効にしました。' if enabled else '登録前の自動起動設定に戻しました。')
                except Exception:
                    result.set_text('登録状態を変更できません。')
            with dialog_actions():
                secondary_button('キャンセル', dialog.close).props('data-autofocus')
                secondary_button('無効にする', lambda:apply(False))
                primary_button('有効にする', lambda:apply(True))
        dialog.open()

    def _on_dialog_key(self, event):
        if (self.opened and not self.closed and not self.export_busy
                and self._visual_confirm is None and event.args == {'key': 'Escape'}):
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
        if self.learning_reset is not None:
            self.learning_reset.dialog.close()
        self._close_visual_controls()
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
        if self.learning_reset is not None:
            self.learning_reset.close()
        if self.following_sync is not None:
            self.following_sync.close()
