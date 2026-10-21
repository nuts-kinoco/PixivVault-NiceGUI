"""既存のナビゲーションを維持する、ページ単位のデモシェル。"""

from nicegui import app, ui

from ..services import ApplicationServices, get_services
from .gallery import GalleryView
from .theme import CSS, TOKENS

NAVIGATION = (
    ("download", "ダウンロード"), ("queue", "キュー"), ("failed", "失敗リスト"),
    ("dedup", "重複整理"), ("learning", "学習"), ("ranking", "ランキング"),
    ("library", "ライブラリ"), ("recommend", "おすすめ"),
)


class DemoShell:
    def __init__(self, services: ApplicationServices) -> None:
        self.services = services
        self.active_tab = "ranking"
        self.learning = None
        self.buttons: dict[str, ui.button] = {}
        ui.context.client.on_delete(self.close)
        ui.add_css(CSS)
        ui.colors(primary=TOKENS["accent"])
        ui.dark_mode(True)
        with ui.column().classes("pv-shell") as self.root:
            with ui.element("div").classes("pv-header"):
                with ui.column().classes("gap-0"):
                    ui.label("PixivVault").classes("pv-title")
                    ui.label("移植検証用 · 架空作品 / 実アカウントへの接続なし").classes("pv-subtitle")
                ui.label("オフライン検証").classes("pv-subtitle")
            with ui.element("nav").classes("pv-nav").props("aria-label=メインナビゲーション"):
                for key, label in NAVIGATION:
                    available = key in {"ranking", "learning"}
                    button = ui.button(label, color=None, on_click=lambda _, tab=key: self.show(tab))
                    button.classes("pv-tab").props(f"flat no-caps data-testid=tab-{key}")
                    button.set_enabled(available)
                    if not available:
                        button.tooltip("未移植")
                    self.buttons[key] = button
            with ui.column().classes("pv-content w-full") as self.content:
                self.gallery = GalleryView(services)
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
        if tab not in {"ranking", "learning"} or tab == self.active_tab:
            return
        self.gallery.close_viewer()
        if tab == "learning" and self.learning is None:
            from .learning_view import LearningView
            with self.content:
                self.learning = LearningView(self.services)
        self.active_tab = tab
        self.gallery.root.set_visibility(tab == "ranking")
        if self.learning is not None:
            self.learning.root.set_visibility(tab == "learning")
            self.learning.set_active(tab == "learning")
        self._update_tabs()

    def close(self) -> None:
        self.gallery.close()
        if self.learning is not None:
            self.learning.close()
        # サービスはアプリが所有する。画面が閉じても共有キャッシュを終了しない。


def build_demo_page() -> None:
    ui.context.client.pixivvault_shell = DemoShell(get_services(app))
