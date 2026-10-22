"""原版の全画面画集ビューアに合わせた、一度だけ構築する表示部品。"""

from __future__ import annotations

from collections.abc import Callable

from nicegui import ui

from ..models import Artwork
from .verified_image import VerifiedImage


class ArtworkViewer:
    def __init__(
        self,
        on_previous: Callable[[], None],
        on_next: Callable[[], None],
        on_close: Callable[[], None],
        on_details: Callable[[], None],
        on_retry: Callable[[], None],
    ) -> None:
        self.dialog = self  # GalleryView's existing dialog.open()/close() call sites.
        self.client = ui.context.client
        self._is_open = False
        with ui.column().classes("pv-viewer pv-viewer-overlay hidden").props(
            "tabindex=-1 role=dialog aria-modal=true aria-hidden=true data-testid=gallery-viewer"
        ) as self.root:
                with ui.row().classes("pv-viewer-header w-full items-center"):
                    ui.label("画集ビューア").classes("pv-viewer-heading")
                    ui.space()
                    self.counter = ui.label().classes("pv-counter")
                    self.download_button = self._unavailable_action("download", "個別DL", "個別ダウンロード")
                    self.bookmark_button = self._unavailable_action("favorite_border", "ブクマ", "ブックマーク")
                    self.follow_button = self._unavailable_action("person_add", "フォロー", "作者フォロー")
                    self.close_button = ui.button(
                        icon="close", on_click=on_close, color=None
                    ).props("flat round aria-label=閉じる")
                with ui.element("div").classes("pv-viewer-stage") as self.stage:
                    self.previous_page = ui.button(
                        icon="chevron_left", on_click=on_previous, color=None
                    ).props("flat round aria-label=前のページ").classes("pv-viewer-page-prev")
                    self.image = VerifiedImage(alt="作品画像", retain_previous=True).props(
                        "data-testid=viewer-image"
                    ).classes("pv-view-art")
                    self.image.on("click", lambda _: self._close_details_on_image_click(on_details))
                    self.next_page = ui.button(
                        icon="chevron_right", on_click=on_next, color=None
                    ).props("flat round aria-label=次のページ").classes("pv-viewer-page-next")
                    self.image_status = ui.label().classes("pv-subtitle pv-viewer-status")
                    self.retry_button = ui.button(
                        "画像を再読込", on_click=on_retry, color=None
                    ).props("flat").classes("pv-viewer-retry")
                    self.description = ui.label().classes("pv-description pv-viewer-details")
                with ui.column().classes("pv-viewer-bottom w-full"):
                    with ui.row().classes("pv-viewer-info w-full items-center"):
                        self.title = ui.label().classes("pv-view-title")
                        ui.element("span").classes("pv-viewer-info-separator").style("width:1px;height:14px")
                        self.author = ui.label().classes("pv-subtitle")
                        ui.space()
                        self.details_button = ui.button(
                            "詳細", on_click=on_details, color=None
                        ).props("flat aria-expanded=false")
        self._details_open = False
        self.set_details_open(False)
        self.set_status("")

    @staticmethod
    def _unavailable_action(icon: str, label: str, purpose: str):
        button = ui.button(label, icon=icon, color=None).props("flat no-caps").classes("pv-viewer-header-action")
        button.props(f"aria-label={purpose}")
        button.set_enabled(False)
        button.tooltip(f"{purpose}は移植準備中です")
        return button

    @property
    def is_open(self) -> bool:
        return self._is_open

    def open(self) -> None:
        if self._is_open:
            return
        self._is_open = True
        self.root.classes(remove="hidden")
        self.root.props("aria-hidden=false")
        if self.client.has_socket_connection:
            self.client.run_javascript(f"""(() => {{
                const node = document.getElementById('{self.root.html_id}');
                if (!node) return;
                node.__pvPreviousFocus = document.activeElement;
                requestAnimationFrame(() => node.focus({{preventScroll:true}}));
            }})()""")

    def close(self) -> None:
        if not self._is_open:
            return
        self._is_open = False
        self.root.props("aria-hidden=true")
        self.root.classes(add="hidden")
        if self.client.has_socket_connection:
            self.client.run_javascript(f"""(() => {{
                const node = document.getElementById('{self.root.html_id}');
                const previous = node?.__pvPreviousFocus;
                if (node) node.__pvPreviousFocus = null;
                if (previous?.isConnected && typeof previous.focus === 'function')
                    requestAnimationFrame(() => previous.focus({{preventScroll:true}}));
            }})()""")

    def set_metadata(self, artwork: Artwork, page_no: int) -> None:
        if not 0 <= page_no < artwork.page_count:
            raise ValueError("page_no out of range")
        self.title.set_text(artwork.title)
        self.author.set_text(artwork.author)
        self.description.set_text(artwork.description)
        self.counter.set_text(f"{page_no + 1}/{artwork.page_count}")
        self.counter._props["aria-label"] = f"ページ {page_no + 1} / {artwork.page_count}"
        self.counter.update()
        self.previous_page.set_enabled(page_no > 0)
        self.next_page.set_enabled(page_no + 1 < artwork.page_count)

    def set_details_open(self, opened: bool) -> None:
        self._details_open = bool(opened)
        self.description.set_visibility(self._details_open)
        self.details_button.props(f"aria-expanded={'true' if self._details_open else 'false'}")

    def _close_details_on_image_click(self, on_details: Callable[[], None]) -> None:
        if self._details_open:
            on_details()

    def set_status(self, status: str, error: bool = False) -> None:
        self.image_status.set_text(status)
        self.image_status.set_visibility(bool(status))
        self.image_status.classes(add="pv-viewer-error" if error else "pv-subtitle",
                                  remove="pv-subtitle" if error else "pv-viewer-error")
        self.retry_button.set_visibility(error)
