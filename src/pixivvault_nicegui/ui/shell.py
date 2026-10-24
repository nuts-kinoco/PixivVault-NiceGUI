"""既存のナビゲーションを維持する、ページ単位のデモシェル。"""

from nicegui import app, ui

from ..services import ApplicationServices, get_services
from .gallery import GalleryView
from .grid_layout import MAX_COLUMNS
from .theme import CSS, TOKENS

NAVIGATION = (
    ("download", "ダウンロード"), ("queue", "キュー"), ("failed", "失敗リスト"),
    ("dedup", "重複整理"), ("learning", "学習"), ("ranking", "ランキング"),
    ("library", "ライブラリ"), ("recommend", "おすすめ"),
    ("scheduler", "定期チェック"),
)
AVAILABLE_TABS = {"ranking", "learning", "library", "failed", "download", "queue", "scheduler"}


class DemoShell:
    def __init__(self, services: ApplicationServices) -> None:
        self.services = services
        self.client = ui.context.client
        self.active_tab = "ranking"
        self.learning = None
        self.library = None
        self.failed_records = None
        self.scheduler_view = None
        self.job_views = {}
        self.card_size = "large"
        self.closed = False
        self.buttons: dict[str, ui.button] = {}
        self.client.on_delete(self.close)
        ui.add_css(CSS)
        ui.colors(primary=TOKENS["accent"])
        ui.dark_mode(True)
        with ui.column().classes("pv-shell") as self.root:
            with ui.element("div").classes("pv-header"):
                with ui.column().classes("gap-0"):
                    ui.label("PixivVault").classes("pv-title")
                    descriptions = {
                        "synthetic": "移植検証用 · 架空作品 / 実アカウントへの接続なし",
                        "library-readonly": "旧ライブラリ読取専用 · 元DB・画像の変更なし",
                        "pixiv-readonly": "pixiv読取接続 · 認証確認はブックマーク更新時 / 書込操作なし",
                    }
                    description = ("pixiv GET専用 · 選択した原寸画像を専用管理先へ保存 / 元DB・Cookieは変更しません"
                                   if services.managed_downloads is not None else descriptions[services.data_mode])
                    ui.label(description).classes("pv-subtitle")
                ui.label("オフライン検証" if services.data_mode == "synthetic" else
                         "ランキング・学習は合成データ").classes("pv-subtitle")
            with ui.element("nav").classes("pv-nav").props("aria-label=メインナビゲーション"):
                for key, label in NAVIGATION:
                    available = key in AVAILABLE_TABS
                    button = ui.button(label, color=None, on_click=lambda _, tab=key: self.show(tab))
                    button.classes("pv-tab").props(f"flat no-caps data-testid=tab-{key}")
                    button.set_enabled(available)
                    if not available:
                        button.tooltip("未移植")
                    self.buttons[key] = button
            with ui.column().classes("pv-content w-full") as self.content:
                self.gallery = GalleryView(services, card_size=self.card_size,
                                           on_size_change=self.change_card_size)
        self._update_tabs()

    def _update_tabs(self) -> None:
        for key, button in self.buttons.items():
            if key == self.active_tab:
                button.classes(add="pv-tab-active")
                button.props["aria-current"] = "page"
            else:
                button.classes(remove="pv-tab-active")
                button.props.pop("aria-current", None)

    def show(self, tab: str) -> None:
        if self.closed or tab not in AVAILABLE_TABS or tab == self.active_tab:
            return
        self.gallery.set_viewer_active(False)
        if self.library is not None:
            self.library.set_active(False)
        if self.failed_records is not None:
            self.failed_records.set_active(False)
        for view in self.job_views.values():
            view.set_active(False)
        if tab == "learning" and self.learning is None:
            from .learning_view import LearningView
            with self.content:
                self.learning = LearningView(self.services)
        if tab == "library" and self.library is None:
            from .library_view import LibraryView
            with self.content:
                self.library = LibraryView(self.services, card_size=self.card_size,
                                           on_size_change=self.change_card_size)
        if tab == 'scheduler' and self.scheduler_view is None:
            from .scheduler_view import SchedulerView
            with self.content:
                self.scheduler_view = SchedulerView(self.services)
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
                if tab == "download" and self.services.managed_downloads is not None:
                    from .managed_download_view import ManagedDownloadView
                    self.job_views[tab] = ManagedDownloadView(self.services)
                else:
                    self.job_views[tab] = (DownloadView(self.services) if tab == "download" else
                                          QueueView(self.services, failed_only=tab == "failed"))
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

    def change_card_size(self, size: str) -> None:
        if size not in MAX_COLUMNS:
            raise ValueError("invalid card size")
        if self.closed:
            return
        self.card_size = size
        self.gallery.apply_card_size(size)
        if self.library is not None:
            self.library.apply_card_size(size)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.gallery.close()
        if self.library is not None:
            self.library.close()
        if self.learning is not None:
            self.learning.close()
        if self.failed_records is not None:
            self.failed_records.close()
        if self.scheduler_view is not None:
            self.scheduler_view.close()
        for view in self.job_views.values():
            view.close()
        # サービスはアプリが所有する。画面が閉じても共有キャッシュを終了しない。


def build_demo_page() -> None:
    ui.context.client.pixivvault_shell = DemoShell(get_services(app))
