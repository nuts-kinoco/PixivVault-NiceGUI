"""既存カードとビューアを保って値を更新する、代表的な閲覧経路。"""

from __future__ import annotations

from nicegui import ui

from ..models import Artwork
from ..services import ApplicationServices
from ..state import GalleryState
from .grid_layout import MAX_COLUMNS
from .card_size_control import size_buttons, update_size_buttons
from .viewer_controller import ViewerController


class GalleryView(ViewerController):
    """サーバー側イベント結線も単体検証できる画面単位の所有者。"""

    SIZE_COLS = MAX_COLUMNS

    def __init__(self, services: ApplicationServices, *, card_size: str = "large",
                 on_size_change=None) -> None:
        self.services = services
        self.state = GalleryState(services.artworks)
        self.state.set_card_size(card_size)
        self._on_size_change = on_size_change
        self.cards: dict[str, ui.element] = {}
        self.images: dict[str, ui.image] = {}
        self.ranks: dict[str, ui.label] = {}
        self._rendered_ids: tuple[str, ...] = ()
        self.more_cell = None
        self.client = ui.context.client
        self.client.on_delete(self.close)

        with ui.column().classes("pv-gallery w-full gap-3") as self.root:
            with ui.element("div").classes("pv-toolbar"):
                self.search_input = ui.input(
                    placeholder="作品名・作者・タグで検索",
                    on_change=lambda event: self.search(event.value),
                ).props("outlined dense clearable debounce=200 data-testid=gallery-search").classes("pv-search")
                self.search_input.on("keydown.enter", lambda _: self.search(self.search_input.value))
                ui.button("検索", on_click=lambda: self.search(self.search_input.value))
                ui.label("カードサイズ").classes("pv-subtitle")
                self.size_buttons = size_buttons(self.change_size, prefix="gallery")
                self._update_size_buttons()
            self.counter = ui.label().classes("pv-counter")
            with ui.element("div").classes("pv-gallery-scroll").props("data-testid=gallery-scroll"):
                self.grid = ui.element("div").classes("pv-grid").style(
                    f"--pv-max-cols:{self.SIZE_COLS[card_size]}")
            self.empty = ui.label("該当する作品がありません").classes("pv-empty")

        self._init_viewer(services, self.state, self.root)
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
        if self.state.closed or not self._viewer_active:
            return
        self.state.set_query(value or "")
        if not self.state.viewer_open:
            self.dialog.close()
        self.render_gallery()

    def more(self) -> None:
        if self.state.closed or not self._viewer_active:
            return
        self.state.load_more()
        self.render_gallery()

    def change_size(self, size: str) -> None:
        if self.state.closed or not self._viewer_active:
            return
        if self._on_size_change is not None:
            self._on_size_change(size)
        else:
            self.apply_card_size(size)

    def apply_card_size(self, size: str) -> None:
        self.state.set_card_size(size)
        if not self.state.closed:
            self.grid.style(f"--pv-max-cols:{self.SIZE_COLS[size]}")
            self._update_size_buttons()

    def _update_size_buttons(self) -> None:
        update_size_buttons(self.size_buttons, self.state.card_size)

    def close(self) -> None:
        self._close_viewer_controller()
