"""既存のナビゲーションを維持する、ページ単位のデモシェル。"""

from nicegui import app, ui

from ..services import ApplicationServices, get_services
from .gallery import GalleryView
from .grid_layout import MAX_COLUMNS
from ..auth_status import (BLUE as AUTH_BLUE, CHECKING_TEXT as AUTH_CHECKING_TEXT, GREEN as AUTH_GREEN,
                           RED as AUTH_RED, YELLOW as AUTH_YELLOW)
from .theme import CSS, TOKENS
from ..startup_tab import TAB_INITIAL, shell_tab_key
from ..db_status import db_status

NAVIGATION = (
    ("download", "ダウンロード"), ("queue", "キュー"), ("failed", "失敗リスト"),
    ("dedup", "重複整理"), ("learning", "学習"), ("ranking", "ランキング"),
    ("personal", "自分のランキング"), ("library", "ライブラリ"), ("recommend", "おすすめ"),
    ("scheduler", "定期チェック"), ("metadata", "メタデータ補完"),
)
# 元 gui.py:87-118 のアイコン表（DOWNLOAD/QUEUE/REPORT_PROBLEM/CONTENT_COPY/EXPLORE/LEADERBOARD/
# PHOTO_LIBRARY/RECOMMEND）。元に無い 3 タブは近い意味の Material アイコン。
# 「直近のバックアップ」(history) は元どおりタブにせず、ヘッダー右端の history ボタンから開く。
TAB_ICONS = {
    "download": "download", "queue": "queue", "failed": "report_problem", "dedup": "content_copy",
    "learning": "explore", "ranking": "leaderboard", "personal": "military_tech",
    "library": "photo_library", "recommend": "recommend", "scheduler": "schedule", "metadata": "edit_note",
}
AVAILABLE_TABS = {"history", "ranking", "learning", "library", "failed", "download", "queue", "scheduler", "metadata", "dedup", "recommend", "personal"}


def cookie_banner_second_line(status):
    """受信が実際に動いているときだけ、元の『自動的に連携されます』を言う。"""
    if status == 'running':
        return 'ブラウザでPixivを開くだけで自動的に連携されます。'
    reason = {'in_use': '他のアプリがポートを使用中です。',
              'setting_off': '拡張機能の受信が設定で停止中です。'}.get(status, '拡張機能の受信が動いていません。')
    return reason + '自動連携は使えません。設定からcookies.txtを選んでください。'


class DemoShell:
    def __init__(self, services: ApplicationServices) -> None:
        self.services = services
        self.multiple_media = sum(owner is not None for owner in (
            services.managed_downloads, services.novel_jobs, services.ugoira)) > 1
        self.client = ui.context.client
        self.active_tab = "ranking"
        self.backup_view = None
        self.learning = None
        self.library = None
        self.failed_records = None
        self.scheduler_view = None
        self.metadata_view = None
        self.duplicate_view = None
        self.recommend_view = None
        self.personal_view = None
        self.preferences_view = None
        self.profile_view = None
        self.get_recovery_view = None
        self.request_status_view = None
        self.following_view = None
        self.job_views = {}
        store = getattr(services, "preferences", None)
        try:
            self.card_size = store.snapshot().values["grid_card_size_mode"] if store is not None else "large"
        except Exception:
            self.card_size = "large"
        self.closed = False
        self._syncing_borrow=False
        self.borrow_switch=None
        self._borrow_timer=None
        self.borrow_dialog=None
        self.borrow_recovery_button=None
        self.buttons: dict[str, ui.button] = {}
        self.auth_label = None
        self.cookie_banner = None
        self._auth_unsubscribe = None
        self._followup_unsubscribe = None
        self.account_switch_dialog = None
        self._banner_dismissed = False
        self._app_conn_timer = None
        self.view_history_button = None
        self.view_history = None
        self.client.on_delete(self.close)
        ui.add_css(CSS)
        ui.colors(primary=TOKENS["accent"])
        self.dark_mode = ui.dark_mode(True)
        with ui.column().classes("pv-shell") as self.root:
            # 元 gui.py:6126 db_status_banner: ヘッダーより上、最上段に常時表示。
            is_production, db_text = db_status(services)
            self.db_banner = ui.label(db_text).props('role=status data-testid=db-status-banner')
            self.db_banner.style(
                'background-color:#d32f2f;color:#fff;font-weight:bold;padding:8px;width:100%' if is_production
                else 'font-size:11px;color:#9e9e9e;padding:2px 0 0 8px')
            with ui.element("div").classes("pv-header"):
                with ui.column().classes("pv-header-main"):
                    ui.label("PixivVault").classes("pv-title")
                    # 元 gui.py login_status_text: ロゴの直下に常時表示。
                    if getattr(services, 'native_profile', None) is not None:
                        self.auth_label = ui.label(AUTH_CHECKING_TEXT).classes('pv-auth').props(
                            'role=status aria-live=polite data-testid=auth-status data-tone=primary')
                    # 元 gui.py app_connection_row: アプリ接続状態と「API切り替え」。iOS/Shaft 連携は作らない。
                    with ui.row().classes('pv-conn-row items-center'):
                        self.app_conn_dot = ui.element('span').classes('pv-conn-dot').props('data-state=idle')
                        self.app_conn_label = ui.label('アプリ: 未接続').props('role=status data-testid=app-connection-status')
                        from .request_status_view import RequestStatusView
                        from .shared_get_recovery_view import SharedGetRecoveryView, shared_get_scope
                        if getattr(services,'external_vault',None) is not None:
                            gate=getattr(services,'premium_authorization',None)
                            if gate is not None:
                                from .premium_gate_view import PremiumGateDialog,DEFAULT_UID_PATH
                                self.borrow_dialog=PremiumGateDialog(gate,on_change=self.sync_borrow,
                                    default_uid_path=getattr(services,'default_uid_path',DEFAULT_UID_PATH))
                                # Original gui.py:859: an opened vault takes the fixed UID file at start.
                                self._reflect_uid_once(self.borrow_dialog)
                            self.borrow_switch=ui.switch('API切り替え',on_change=self.borrow_changed).props(
                                'dense data-testid=premium-borrow-toggle')
                            # .props の文字列は全角括弧を含む値を捨てる（Props.parse）。完全な値を直接代入する。
                            self.borrow_switch.props['aria-label'] = '借用プレミアム（API切り替え）'
                            with self.borrow_switch:ui.tooltip('借用プレミアム')
                            if self.borrow_dialog is not None:
                                # Shown only while a Pixiv result is unconfirmed (the Shaft tab no longer exists).
                                self.borrow_recovery_button=ui.button('通信結果を確認',on_click=self.borrow_dialog.open_recovery).props(
                                    'flat dense no-caps data-testid=premium-recovery-shell')
                            self._borrow_timer=ui.timer(1,self.sync_borrow)
                            self.sync_borrow()
                        else:
                            self.borrow_switch=ui.switch('API切り替え',value=False).props(
                                'dense data-testid=premium-borrow-toggle')
                            self.borrow_switch.props['aria-label'] = '借用プレミアム（API切り替え）'
                            self.borrow_switch.disable()
                            with self.borrow_switch:ui.tooltip('借用プレミアム: 設定でCookieと保存先を保存し、再起動するとONにできます。')
                        shared = shared_get_scope(services)
                        if shared is not None:
                            self.get_recovery_view = SharedGetRecoveryView(shared)
                        self._app_conn_timer = ui.timer(2, self._refresh_app_connection)
                        self._refresh_app_connection()
                    policy=getattr(self.services,'_request_retry',None)
                    if policy is not None:
                        self.request_status_view=RequestStatusView(policy)
                # 元 gui.py:6125-6136 の右カラム: IconButton 6 個（バックアップ・閲覧履歴・保存フォルダー・テーマ・Pixiv・設定）。
                with ui.element("div").classes("pv-header-actions"):
                    history = ui.button(icon='history', color=None, on_click=lambda _: self.show('history')).classes(
                        'pv-hbtn').props('flat round data-testid=tab-history aria-label=直近のバックアップ')
                    history_available = self._tab_available('history')
                    with history:
                        # 使えないときは理由を出す（タブ側と同じ文言）。
                        ui.tooltip('直近のバックアップ' if history_available else 'この起動設定では利用できません')
                    history.set_enabled(history_available)
                    self.buttons['history'] = history
                    self.view_history_button = ui.button(icon='visibility', color=None,
                                                         on_click=self.open_view_history).classes('pv-hbtn').props(
                        'flat round data-testid=view-history-open aria-label=閲覧履歴')
                    with self.view_history_button:
                        self.view_history_badge = ui.badge('').props('floating color=primary')
                        self.view_history_badge.set_visibility(False)
                        self.view_history_tooltip = ui.tooltip('閲覧履歴（学習タブ）')
                    from .saved_folder_button import SavedFolderButton
                    self.saved_folder_button = SavedFolderButton(services)
                    self.saved_folder_button.button.classes('pv-hbtn').props('round')
                    self.theme_button = ui.button(icon='light_mode', color=None, on_click=self.toggle_theme).classes(
                        'pv-hbtn').props('flat round data-testid=theme-toggle aria-label=ライトモードへ切り替え')
                    with self.theme_button:
                        self.theme_tooltip = ui.tooltip('ライトモードへ切り替え')
                    self.pixiv_home = ui.link('', 'https://www.pixiv.net/', new_tab=True).classes('pv-hbtn').props(
                        'data-testid=pixiv-home-link aria-label=ブラウザでPixivを開く')
                    with self.pixiv_home:
                        ui.icon('public')
                        ui.tooltip('ブラウザで Pixiv を開く (ログイン状態確認・自動連携)')
                    # NiceGUI's .props parser treats whitespace as a separator:
                    # ``rel=noopener noreferrer`` serializes only ``noopener``.
                    # Assign the complete HTML rel value as one property instead.
                    self.pixiv_home.props['rel'] = 'noopener noreferrer'
                    settings = ui.button(icon='settings', color=None, on_click=self.open_preferences).classes(
                        'pv-hbtn').props('flat round data-testid=preferences-open aria-label=設定')
                    with settings:
                        ui.tooltip('設定')
                    settings.set_enabled(getattr(services, 'preferences', None) is not None
                                         or getattr(services, 'native_profile', None) is not None)
            if self.auth_label is not None:
                self._build_cookie_banner()
            with ui.element("nav").classes("pv-nav").props("aria-label=メインナビゲーション"):
                for key, label in NAVIGATION:
                    if key=='personal' and getattr(services,'external_vault',None) is None and services.live_client is None:continue
                    available = self._tab_available(key)
                    button = ui.button(label, icon=TAB_ICONS[key], color=None, on_click=lambda _, tab=key: self.show(tab))
                    button.classes("pv-tab").props(f"flat no-caps data-testid=tab-{key}")
                    button.set_enabled(available)
                    if not available:
                        button.tooltip("この起動設定では利用できません")
                    self.buttons[key] = button
            with ui.column().classes("pv-content w-full") as self.content:
                if services.ranking_source is not None:
                    from .ranking_view import RankingView
                    self.gallery = RankingView(services, card_size=self.card_size, on_size_change=self.change_card_size)
                else:
                    self.gallery = GalleryView(services, card_size=self.card_size, on_size_change=self.change_card_size)
            # 閲覧履歴（元 gui.py の閲覧履歴ボタン）は学習タブを開かずに、起動時から件数を出してダイアログを開く。
            self._build_view_history()
        self._attach_auth_status()
        # 起動直後のタブは元 TAB_INITIAL（ダウンロード）。--tab で変えられる。
        # ランキングは開いたときに初回取得する（元 on_tab_open）ので、先に有効化しない。
        initial = getattr(services, 'startup_tab', None) or shell_tab_key(TAB_INITIAL)
        if not self._tab_available(initial):
            initial = shell_tab_key(TAB_INITIAL)
        if initial != 'ranking':
            self._update_tabs()
            self.show(initial)
        else:
            self.gallery.set_viewer_active(True)
            self._update_tabs()

    def _build_cookie_banner(self):
        # Original gui.py cookie_banner: 青枠の情報帯。ヘッダーとタブバーの間。
        with ui.row().classes('pv-cookie-banner items-center no-wrap').props(
                'role=status data-testid=cookie-banner') as self.cookie_banner:
            ui.icon('info_outline').classes('pv-banner-icon').style('color:var(--pv-auth-blue)')
            with ui.column().classes('gap-0').style('flex:1 1 0;min-width:0'):
                ui.label('【Cookie未連携】').classes('pv-banner-title')
                ui.label('PV拡張機能をご利用中の場合、cookies.txtの手動DLは不要です。\n'
                         + cookie_banner_second_line(getattr(self.services, 'extension_bridge_status', 'disabled'))).classes(
                    'pv-banner-body')
            link = ui.link('', 'https://www.pixiv.net/', new_tab=True).classes('pv-pill').props(
                'data-testid=cookie-banner-open aria-label=ブラウザでPixivを開く')
            with link:
                ui.icon('open_in_browser').style('font-size:18px')
                ui.label('Pixivを開く')
            link.props['rel'] = 'noopener noreferrer'
            ui.button(icon='close', color=None, on_click=self.close_cookie_banner).classes('pv-hbtn').props(
                'flat round dense aria-label=閉じる data-testid=cookie-banner-close')
        self.cookie_banner.set_visibility(False)

    def close_cookie_banner(self):
        self._banner_dismissed = True
        if self.cookie_banner is not None:
            self.cookie_banner.set_visibility(False)

    def _render_auth_status(self, status, notice=None):
        if self.closed or self.auth_label is None:
            return
        self.auth_label.set_text(status.text)
        # 色は CSS 変数（テーマ別。ライトでも 4.5:1 を保つ）から引く。
        tone = {AUTH_RED: 'red', AUTH_GREEN: 'green', AUTH_YELLOW: 'yellow',
                AUTH_BLUE: 'blue'}.get(status.color, 'primary')
        self.auth_label.props(f'data-tone={tone}')
        if not status.banner:
            self._banner_dismissed = False
        self.cookie_banner.set_visibility(status.banner and not self._banner_dismissed)
        if notice:
            with self.client:
                ui.notify(notice, type='positive')

    def _attach_auth_status(self):
        profile = getattr(self.services, 'native_profile', None)
        if self.auth_label is None or profile is None:
            return
        holder = profile.auth_status
        self._auth_unsubscribe = holder.subscribe(self._render_auth_status)
        self._render_auth_status(holder.status)
        self._attach_followup(profile)
        # Once per process, only for a STOP-fenced profile; a no-op otherwise.
        profile.start_startup_auth_check()

    def _attach_followup(self, profile):
        followup = getattr(profile, 'followup', None)
        if followup is None:
            return
        self._followup = followup
        self._followup_unsubscribe = followup.subscribe(self._on_followup)
        if followup.pending is not None:
            self._show_account_switch(followup.pending)

    def _on_followup(self, kind, payload):
        if self.closed:
            return
        with self.client:
            if kind == 'notice':
                ui.notify(payload['text'], type=payload.get('type', 'info'))
            elif kind == 'account_switch':
                self._show_account_switch(self._followup.pending)
            elif kind == 'account_switch_closed':
                self._close_account_switch()

    def _show_account_switch(self, request):
        # Original gui.py confirm_dlg: the irreversible deletion needs an explicit yes.
        self._close_account_switch()
        if request is None:
            return
        with ui.dialog().props('persistent data-testid=account-switch-dialog') as dialog,                 ui.card().style('max-width:480px'):
            ui.label('別アカウントのCookieを検出しました').style('font-size:16px;font-weight:700')
            ui.label(
                f'現在保存されているフォロー中データ（お気に入り・ダウンロード状況などを含む）を'
                f'すべて削除し、アカウント（ID: {request.new_account}）の情報で再取得します。'
                'この操作は元に戻せません。よろしいですか？'
            ).style(f'font-size:13px;color:var(--pv-muted)')
            with ui.row().classes('w-full justify-end'):
                ui.button('キャンセル', on_click=self._followup.cancel_account_switch).props(
                    'flat no-caps data-testid=account-switch-cancel')
                ui.button('削除して再取得する', on_click=self._followup.confirm_account_switch).props(
                    'color=negative no-caps data-testid=account-switch-confirm')
        self.account_switch_dialog = dialog
        dialog.open()

    def _close_account_switch(self):
        dialog, self.account_switch_dialog = self.account_switch_dialog, None
        if dialog is not None:
            dialog.close()
            dialog.delete()

    def open_preferences(self):
        if self.closed:
            return
        profile = getattr(self.services, 'native_profile', None)
        if profile is not None and self.profile_view is None:
            self.profile_view = profile.create_view()
        if getattr(self.services, 'preferences', None) is None:
            if self.profile_view is not None:
                self.profile_view.open()
            return
        if self.preferences_view is None:
            from .preferences_view import PreferencesView
            from .following_sync_view import FollowingSyncView
            self.following_view = FollowingSyncView(self.services)
            self.preferences_view = PreferencesView(self.services.preferences,
                enabled_keys=self.services.preference_consumers(),
                on_change=self.apply_preferences,
                clear_cache=self.services.shared_image_cache.clear if self.services.shared_image_cache else None,
                desktop=self.services.desktop,
                export_legacy=(self.services.export_legacy_metadata
                               if self.services.legacy_metadata_export_available() else None),
                extension_status=lambda:getattr(self.services,"extension_bridge_status","disabled"),
                extension_snapshot=lambda: self.services.extension_bridge.response_snapshot() if self.services.extension_bridge is not None else ({}, {}),
                native_profile=self.profile_view, following_sync=self.following_view,
                vault=getattr(self.services, 'external_vault', None),
                pixiv_actions_mode=getattr(self.services, 'pixiv_actions_mode', 'readonly'))
        self.preferences_view.open()

    def _refresh_app_connection(self):
        # 元は iOS 連携の最終接続時刻から出す。移植版は iOS 連携を除外しており最終接続時刻の源が無いため、
        # ブラウザ拡張の受信口の状態（services.extension_bridge_status）から出す。
        if self.closed:
            return
        status = getattr(self.services, 'extension_bridge_status', 'disabled')
        state, text = {'running': ('waiting', 'アプリ: 受信待機中'),
                       'setting_off': ('off', 'アプリ: 切断中')}.get(status, ('idle', 'アプリ: 未接続'))
        self.app_conn_label.set_text(text)
        self.app_conn_dot.props(f'data-state={state}')

    def _build_view_history(self):
        storage = getattr(self.services, 'discovery', None) or getattr(self.services, 'learning', None)
        if storage is None:
            return
        from .view_history_view import HeaderHistoryHost, ViewHistoryView
        self.view_history = ViewHistoryView(HeaderHistoryHost(self, storage), storage)
        self.view_history.on_count = self.set_view_history_count

    async def open_view_history(self):
        # 元 gui.py: ダイアログを開くだけで、タブは変えない。学習タブは作らず、通信も起こさない。
        if self.closed:
            return
        view = self.view_history
        if view is None or view.storage is None:
            ui.notify('閲覧履歴を利用できません。', type='warning')
            return
        await view.open()

    def set_view_history_count(self, count):
        if self.closed or self.view_history_button is None:
            return
        self.view_history_badge.set_text(str(count))
        self.view_history_badge.set_visibility(count > 0)
        # 元 gui.py:2466 の文言。
        self.view_history_tooltip.set_text(f'閲覧履歴（学習タブ・{count}件）')

    def sync_borrow(self):
        if self.closed or self.borrow_switch is None:return
        if getattr(self.services,'external_vault',None) is None:return
        gate=getattr(self.services,'premium_authorization',None)
        status=gate.status() if gate is not None else self.services.external_vault.status()
        self._syncing_borrow=True
        try:self.borrow_switch.set_value(bool(status.get('enabled') if gate is not None else status.get('open')))
        finally:self._syncing_borrow=False
        if self.borrow_recovery_button is not None and self.borrow_dialog is not None:
            self.borrow_recovery_button.set_visibility(self.borrow_dialog.recovery_visible())
        library = self.library
        if library is not None:
            library = getattr(library, 'views', {}).get('illustration', library)
            update = getattr(library, 'refresh_search_scope', None)
            if callable(update):
                update()
    async def borrow_changed(self,event):
        if self._syncing_borrow or self.closed:return
        vault=self.services.external_vault
        gate=getattr(self.services,'premium_authorization',None)
        if gate is not None:
            if bool(event.value)==gate.status()['enabled']:return
            if event.value:
                await self.borrow_dialog.request_enable()
            else:
                import asyncio
                try:await asyncio.to_thread(gate.disable)
                except Exception:ui.notify('OFFの保存を確認できません。設定の借用プレミアムの状態を確認してください。',type='warning')
            self.sync_borrow();return
        if bool(event.value)==bool(vault.status().get('open')):return
        if event.value:
            # ON always goes through the password/setup flow. No credentials
            # are copied into the browser and the switch itself sends no HTTP.
            with self.root: ui.notify('\u501f\u7528\u30d7\u30ec\u30df\u30a2\u30e0\u306e\u8a8d\u8a3c\u3092\u5229\u7528\u3067\u304d\u307e\u305b\u3093\u3002',type='warning')
        else:
            import asyncio
            try:await asyncio.to_thread(vault.lock_explicitly)
            except Exception:ui.notify('ロックの完了を確認できません。設定の借用プレミアムの状態を確認してください。',type='warning')
        self.sync_borrow()
    def open_premium_search(self,word):
        self.show('personal')
        if self.personal_view is not None and word.strip():self.personal_view.tags.set_value(word.strip())
    def apply_preferences(self,snapshot=None):
        self.services.apply_preferences(snapshot)
        if self.learning is not None:self.learning.refresh_prefetch()

    def _reflect_uid_once(self, dialog) -> None:
        # プロセス起動時に1回だけ（シェルは画面を開くたびに作られる）。
        if getattr(self.services, 'uid_file_reflected', False):
            return
        self.services.uid_file_reflected = True
        dialog.reflect_uid_file_at_startup()

    def _tab_available(self, tab: str) -> bool:
        services = self.services
        return (tab in AVAILABLE_TABS and
                (tab != 'history' or getattr(services, 'save_exports', None) is not None
                 or getattr(services, 'legacy_backup_history_available', False)) and
                (tab != 'metadata' or services.managed_downloads is not None) and
                (tab != 'dedup' or services.duplicate_inspection is not None) and
                (tab != 'recommend' or services.recommendations is not None) and
                (tab != 'personal' or getattr(services, 'external_vault', None) is not None))

    def _update_tabs(self) -> None:
        for key, button in self.buttons.items():
            if key == self.active_tab:
                button.classes(add="pv-tab-active")
                button.props["aria-current"] = "page"
            else:
                button.classes(remove="pv-tab-active")
                button.props.pop("aria-current", None)

    def show(self, tab: str) -> None:
        if (self.closed or tab not in AVAILABLE_TABS or tab == self.active_tab
                or (tab == 'history' and getattr(self.services,'save_exports',None) is None
                    and not getattr(self.services,'legacy_backup_history_available',False))
                or (tab == 'metadata' and self.services.managed_downloads is None)
                or (tab == 'dedup' and self.services.duplicate_inspection is None)
                or (tab == 'recommend' and self.services.recommendations is None)
                or (tab in ('personal',) and getattr(self.services, 'external_vault', None) is None)):
            return
        if self.view_history is not None:
            self.view_history.viewer.close_viewer(restore_focus=False)  # 履歴から開いた作品は別タブへ持ち越さない
        self.gallery.set_viewer_active(False)
        if self.library is not None:
            self.library.set_active(False)
        if self.failed_records is not None:
            self.failed_records.set_active(False)
        for view in self.job_views.values():
            view.set_active(False)
        if tab == 'history' and self.backup_view is None:
            from .viewer_backup_view import ViewerBackupView
            with self.content:
                self.backup_view=ViewerBackupView(self.services,card_size=self.card_size,on_size_change=self.change_card_size)
        if tab == "learning" and self.learning is None:
            from .learning_view import LearningView
            with self.content:
                self.learning = LearningView(self.services)
            history_view = getattr(self.learning, 'view_history', None)
            if history_view is not None:
                history_view.on_count = self.set_view_history_count
        if tab == "library" and self.library is None:
            from .library_view import LibraryView
            with self.content:
                if self.multiple_media:
                    from .media_views import MediaLibraryView
                    self.library = MediaLibraryView(self.services, card_size=self.card_size,
                                                   on_size_change=self.change_card_size,on_premium_search=self.open_premium_search)
                elif self.services.ugoira is not None:
                    from .ugoira_view import UgoiraLibraryView
                    self.library = UgoiraLibraryView(self.services)
                elif self.services.novel_jobs is not None:
                    from .novel_save_view import NovelLibraryView
                    self.library = NovelLibraryView(self.services)
                else:
                    self.library = LibraryView(self.services, card_size=self.card_size,
                                           on_size_change=self.change_card_size,on_premium_search=self.open_premium_search)
        if tab == 'recommend' and self.recommend_view is None:
            from .recommend_view import RecommendView
            with self.content:
                self.recommend_view = RecommendView(self.services, card_size=self.card_size, on_size_change=self.change_card_size, on_open=self.open_recommended,
                    on_navigate_to_learning=lambda: self.show('learning'),
                    on_search_tag=self.open_premium_search,
                    on_enqueue=self.services.save_explicit_work if self.services.managed_downloads else None)
        if tab == 'personal' and self.personal_view is None:
            from .personal_ranking_view import PersonalRankingView
            with self.content:
                self.personal_view = PersonalRankingView(self.services, card_size=self.card_size,
                                                        on_size_change=self.change_card_size)
        if tab == 'dedup' and self.duplicate_view is None:
            from .duplicate_view import DuplicateView
            with self.content:
                self.duplicate_view = DuplicateView(self.services.duplicate_inspection,
                    open_trash=(getattr(self.services,'open_duplicate_quarantine',None)
                        if getattr(self.services,'desktop',None) is not None else None))
        if tab == 'scheduler' and self.scheduler_view is None:
            from .scheduler_view import SchedulerView
            with self.content:
                self.scheduler_view = SchedulerView(self.services)
        if tab == 'metadata' and self.metadata_view is None:
            from .metadata_view import MetadataView
            with self.content:
                self.metadata_view = MetadataView(self.services)
        use_legacy_failed = (self.services.jobs is None or
                             getattr(self.services, 'legacy_failed_retry_available', False))
        if tab == "failed" and use_legacy_failed and self.failed_records is None:
            from .failed_records_view import FailedRecordsView
            with self.content:
                self.failed_records = FailedRecordsView(self.services)
        if (tab in {"download", "queue"} or
                (tab == "failed" and not use_legacy_failed)) and tab not in self.job_views:
            from .job_views import DownloadView, QueueView
            with self.content:
                if tab == "download" and self.multiple_media:
                    from .media_views import MediaDownloadView
                    self.job_views[tab] = MediaDownloadView(self.services, navigate=self.show)
                elif tab == "download" and self.services.ugoira is not None:
                    from .ugoira_view import UgoiraDownloadView
                    self.job_views[tab] = UgoiraDownloadView(self.services, navigate=self.show)
                elif tab == "download" and self.services.novel_jobs is not None:
                    from .novel_save_view import NovelDownloadView
                    self.job_views[tab] = NovelDownloadView(self.services, navigate=self.show)
                elif tab == "download" and self.services.managed_downloads is not None:
                    from .managed_download_view import ManagedDownloadView
                    self.job_views[tab] = ManagedDownloadView(self.services)
                else:
                    self.job_views[tab] = (DownloadView(self.services) if tab == "download" else
                                          QueueView(self.services, failed_only=tab == "failed"))
        for key, view in (('personal', self.personal_view),):
            if view is not None:
                view.root.set_visibility(tab == key)
                view.set_active(tab == key)
        if self.backup_view is not None:
            self.backup_view.root.set_visibility(tab=='history')
            self.backup_view.set_active(tab=='history')
        self.active_tab = tab
        self.gallery.root.set_visibility(tab == "ranking")
        self.gallery.set_viewer_active(tab == "ranking")
        if self.library is not None:
            self.library.root.set_visibility(tab == "library")
            self.library.set_active(tab == "library")
        if self.learning is not None:
            self.learning.root.set_visibility(tab == "learning")
            self.learning.set_active(tab == "learning")
        if self.failed_records is not None:
            self.failed_records.root.set_visibility(tab == "failed")
            self.failed_records.set_active(tab == "failed")
        if self.scheduler_view is not None:
            self.scheduler_view.root.set_visibility(tab == 'scheduler')
        if self.metadata_view is not None:
            self.metadata_view.root.set_visibility(tab == 'metadata')
        if self.duplicate_view is not None:
            self.duplicate_view.root.set_visibility(tab == 'dedup')
            self.duplicate_view.set_active(tab == 'dedup')
        if self.recommend_view is not None:
            self.recommend_view.root.set_visibility(tab == 'recommend')
            self.recommend_view.set_active(tab == 'recommend')
        for key, view in self.job_views.items():
            view.root.set_visibility(tab == key)
            view.set_active(tab == key)
        self._update_tabs()
        if tab in {"ranking", "library"} and self.client.has_socket_connection:
            self.client.run_javascript(f"""requestAnimationFrame(() => {{
                const node = document.getElementById('{self.buttons[tab].html_id}');
                if (node?.isConnected && node.getAttribute('aria-current') === 'page' &&
                    node.getClientRects().length)
                    node.focus({{preventScroll:true}});
            }})""")

    def open_recommended(self, work_id):
        self.show('library')
        if self.library is None or not self.library.open_saved_work(work_id):
            ui.notify('この作品の保存画像はありません。元のおすすめを再取得したとは扱いません。', type='warning')

    def toggle_theme(self) -> None:
        if self.closed:
            return
        self.dark_mode.toggle()
        dark = self.dark_mode.value
        label = 'ライトモードへ切り替え' if dark else 'ダークモードへ切り替え'
        self.theme_button.icon = 'light_mode' if dark else 'dark_mode'
        self.theme_button.props(f'aria-label={label}')
        self.theme_tooltip.set_text(label)

    def change_card_size(self, size: str) -> None:
        if size not in MAX_COLUMNS:
            raise ValueError("invalid card size")
        if self.closed:
            return
        store = getattr(self.services, "preferences", None)
        if store is not None:
            try:
                current = store.snapshot()
                if current.values["grid_card_size_mode"] != size:
                    store.update({"grid_card_size_mode": size}, expected_revision=current.revision)
            except Exception:
                ui.notify("カードサイズを保存できません。再度お試しください。", type="warning")
                return
        self.card_size = size
        if self.backup_view is not None:self.backup_view.apply_card_size(size)
        self.gallery.apply_card_size(size)
        if self.library is not None:
            self.library.apply_card_size(size)
        if self.recommend_view is not None:
            self.recommend_view.apply_card_size(size)
        if getattr(self, 'personal_view', None) is not None:
            self.personal_view.apply_card_size(size)

    def close(self) -> None:
        if self._auth_unsubscribe is not None:
            self._auth_unsubscribe()
            self._auth_unsubscribe = None
        if self._followup_unsubscribe is not None:
            self._followup_unsubscribe()
            self._followup_unsubscribe = None
        if self.closed:
            return
        self.closed = True
        if self._borrow_timer is not None:self._borrow_timer.deactivate()
        if self._app_conn_timer is not None:self._app_conn_timer.deactivate()
        if self.borrow_dialog is not None:self.borrow_dialog.close()
        self.saved_folder_button.close()
        if self.get_recovery_view is not None:self.get_recovery_view.close()
        if self.request_status_view is not None:self.request_status_view.close()
        self.gallery.close()
        if self.view_history is not None:self.view_history.close()
        if self.backup_view is not None:self.backup_view.close()
        if self.library is not None:
            self.library.close()
        if self.learning is not None:
            self.learning.close()
        if self.failed_records is not None:
            self.failed_records.close()
        if self.scheduler_view is not None:
            self.scheduler_view.close()
        if self.metadata_view is not None:
            self.metadata_view.close()
        if self.duplicate_view is not None:
            self.duplicate_view.close()
        if self.recommend_view is not None:
            self.recommend_view.close()
        for view in (self.personal_view, self.preferences_view, self.profile_view, self.following_view):
            if view is not None:
                view.close()
        for view in self.job_views.values():
            view.close()
        # サービスはアプリが所有する。画面が閉じても共有キャッシュを終了しない。


def build_demo_page() -> None:
    ui.context.client.pixivvault_shell = DemoShell(get_services(app))
