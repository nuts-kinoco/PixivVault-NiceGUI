"""既存カードとビューアを保って値を更新する、代表的な閲覧経路。"""

from __future__ import annotations

from nicegui import ui

from ..image_events import source_matches
from ..models import Artwork
from ..services import ApplicationServices
from ..state import GalleryState
from .grid_layout import MAX_COLUMNS
from .artwork_viewer import ArtworkViewer


class GalleryView:
    """サーバー側イベント結線も単体検証できる画面単位の所有者。"""

    SIZE_COLS = MAX_COLUMNS

    def __init__(self, services: ApplicationServices) -> None:
        self.services = services
        self.state = GalleryState(services.artworks)
        self.cards: dict[str, ui.element] = {}
        self.images: dict[str, ui.image] = {}
        self.ranks: dict[str, ui.label] = {}
        self._rendered_ids: tuple[str, ...] = ()
        self._image_token = None
        self._image_url = ""
        self.detail_open = False
        self.more_cell = None
        self.client = ui.context.client
        self.client.on_delete(self.close)

        with ui.column().classes("pv-gallery w-full gap-3") as self.root:
            with ui.element("div").classes("pv-toolbar"):
                self.search_input = ui.input(
                    placeholder="作品名・作者・タグで検索",
                    on_change=lambda event: self.search(event.value),
                ).props("outlined dense clearable dark debounce=200 data-testid=gallery-search").classes("pv-search")
                self.search_input.on("keydown.enter", lambda _: self.search(self.search_input.value))
                ui.button("検索", on_click=lambda: self.search(self.search_input.value))
                ui.label("カードサイズ").classes("pv-subtitle")
                self.size_buttons = {}
                for title, size in (("小", "small"), ("中", "medium"), ("大", "large")):
                    self.size_buttons[size] = ui.button(
                        title, color=None, on_click=lambda _, s=size: self.change_size(s),
                    ).props("flat dense no-caps aria-pressed=false").classes("pv-size-button")
                self._update_size_buttons()
            self.counter = ui.label().classes("pv-counter")
            with ui.element("div").classes("pv-gallery-scroll").props("data-testid=gallery-scroll"):
                self.grid = ui.element("div").classes("pv-grid").style("--pv-max-cols:6")
            self.empty = ui.label("該当する作品がありません").classes("pv-empty")

        # 正式UIは原版と同じ全画面・作品内ページ移動。作品間移動は一覧に戻って行う。
        self.viewer = ArtworkViewer(
            lambda: self.move_page(-1), lambda: self.move_page(1),
            self.close_viewer, self.toggle_details, self.request_image,
        )
        self.dialog = self.viewer.dialog
        self.viewer_image = self.viewer.image
        self.description = self.viewer.description
        self.viewer_counter = self.viewer.counter
        self.image_status = self.viewer.image_status
        self.retry_button = self.viewer.retry_button
        self.previous_page = self.viewer.previous_page
        self.next_page = self.viewer.next_page
        self.viewer.root.on("keydown", self._on_key, js_handler="""(event) => {
            const tag = event.target?.tagName?.toLowerCase();
            if (event.repeat || event.isComposing || event.keyCode === 229 ||
                event.ctrlKey || event.altKey || event.metaKey ||
                ['input','textarea','select'].includes(tag) || event.target?.isContentEditable) return;
            const key = event.key.toLowerCase();
            if (!['arrowleft','arrowright','i','tab','escape'].includes(key)) return;
            event.preventDefault(); event.stopPropagation(); emit(key);
        }""")
        # 初回画像待ちではoverlayをまだ見せない。カードに焦点があるEscapeも取消に使う。
        self.root.on("keydown.escape", lambda _: self.close_viewer()
                     if self.state.viewer_open and not self.viewer.is_open else None)
        self.viewer_image.on("load", self._image_loaded)
        self.viewer_image.on("error", self._image_failed)
        self.render_gallery()

    def _make_card(self, art: Artwork) -> None:
        with self.grid:
            with ui.element("div").classes("pv-card").props(
                f"tabindex=0 role=button data-work-id={art.work_id}"
            ) as card:
                card.on("click", lambda _, work_id=art.work_id: self.open(work_id))
                card.on("keydown.enter", lambda _, work_id=art.work_id: self.open(work_id))
                self.ranks[art.work_id] = ui.label().classes("pv-card-rank")
                image = ui.image(self.services.images.url(art.work_id, 0, "thumb"))
                image.props("fit=contain loading=lazy no-transition").classes("pv-thumb")
                with ui.element("div").classes("pv-card-meta"):
                    ui.label(art.title).classes("pv-card-title")
                    ui.label(art.author).classes("pv-card-author")
        self.cards[art.work_id] = card
        self.images[art.work_id] = image

    def render_gallery(self) -> None:
        if self.state.closed:
            return
        visible = self.state.visible_items
        wanted = tuple(item.work_id for item in visible)
        # 同じ列の追加なら既存要素のIDを保つ。検索で順序が変わるときだけ再構成。
        if wanted[:len(self._rendered_ids)] != self._rendered_ids:
            self.grid.clear()
            self.more_cell = None
            self.cards.clear()
            self.images.clear()
            self.ranks.clear()
            self._rendered_ids = ()
        for art in visible[len(self._rendered_ids):]:
            self._make_card(art)
        for rank, art in enumerate(visible, start=1):
            self.ranks[art.work_id].set_text(f"#{rank}")
        self._rendered_ids = wanted
        self.empty.set_visibility(not wanted)
        # 原版ranking_tab.py:963-974の契約: 常時表示ではなくグリッド最終セル。
        if self.more_cell is None:
            with self.grid:
                with ui.element("div").classes("pv-more-cell") as self.more_cell:
                    self.more_button = ui.button("もっと見る", on_click=self.more).props("outline no-caps")
        else:
            self.more_cell.move(self.grid)
        self.more_cell.set_visibility(self.state.has_more)
        self.counter.set_text(f"{self.state.shown_count} / {self.state.total_count} 件表示")

    def search(self, value: str | None = None) -> None:
        if self.state.closed:
            return
        self.state.set_query(value or "")
        if not self.state.viewer_open:
            self.dialog.close()
        self.render_gallery()

    def more(self) -> None:
        self.state.load_more()
        self.render_gallery()

    def change_size(self, size: str) -> None:
        self.state.set_card_size(size)
        if not self.state.closed:
            self.grid.style(f"--pv-max-cols:{self.SIZE_COLS[size]}")
            self._update_size_buttons()

    def _update_size_buttons(self) -> None:
        for size, button in self.size_buttons.items():
            selected = size == self.state.card_size
            button.classes(add="pv-size-selected" if selected else "pv-size-button",
                           remove="pv-size-button" if selected else "pv-size-selected")
            button.props(f"aria-pressed={'true' if selected else 'false'}")

    def open(self, work_id: str) -> None:
        if self.state.open_artwork(work_id):
            self.detail_open = False
            self.viewer.set_details_open(False)
            self.update_viewer()

    def move_work(self, delta: int) -> None:
        if self.state.move_viewer(delta):
            self.update_viewer()

    def move_page(self, delta: int) -> None:
        if self.state.move_page(delta):
            self.detail_open = False
            self.viewer.set_details_open(False)
            self.update_viewer()

    def update_viewer(self) -> None:
        art = self.state.selected_artwork
        if art is None or self.state.closed:
            return
        self.viewer.set_metadata(art, self.state.selected_page)
        self.request_image()

    def request_image(self) -> None:
        art = self.state.selected_artwork
        if art is None or self.state.closed:
            return
        self._image_token = self.state.begin_request(art.work_id)
        # フラグメントはHTTPへ送られずキャッシュを壊さない。loadイベントの世代照合に使う。
        self._image_url = (self.services.images.url(art.work_id, self.state.selected_page, "view")
                           + f"#request-{self._image_token.generation}")
        self.viewer_image.set_source(self._image_url)
        self.viewer_image.props["aria-busy"] = "true"
        self.viewer.set_status("画像読込中")

    def _image_loaded(self, event) -> None:
        if source_matches(event.args, self._image_url) and self._image_token is not None:
            if self.state.finish_image(self._image_token):
                self.viewer_image.style("opacity:1")
                self.viewer_image.props["aria-busy"] = "false"
                self.viewer.set_status("")
                self.dialog.open()

    def _image_failed(self, event) -> None:
        if source_matches(event.args, self._image_url) and self._image_token is not None:
            if self.state.finish_image(self._image_token, success=False):
                self.viewer.set_status("画像を読み込めませんでした", error=True)
                self.viewer_image.props["aria-busy"] = "false"
                self.dialog.open()

    def toggle_details(self) -> None:
        if self.state.viewer_open and not self.state.closed:
            self.detail_open = not self.detail_open
            self.viewer.set_details_open(self.detail_open)

    def _on_key(self, event) -> None:
        if not self.state.viewer_open or self.state.closed:
            return
        if event.args == "arrowleft":
            self.move_page(-1)
        elif event.args == "arrowright":
            self.move_page(1)
        elif event.args in {"i", "tab"}:
            self.toggle_details()
        elif event.args == "escape":
            if self.detail_open:
                self.toggle_details()
            else:
                self.close_viewer()

    def close_viewer(self) -> None:
        self.state.close_viewer()
        self.dialog.close()
        self._image_token = None
        self._image_url = ""
        self.viewer_image.set_source("")

    def close(self) -> None:
        self.state.close()
        self._image_token = None
