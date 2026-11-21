"""既存カードとビューアを保って値を更新する、代表的な閲覧経路。"""

from __future__ import annotations

from dataclasses import dataclass
from inspect import isawaitable

from nicegui import ui

from ..models import Artwork
from ..services import ApplicationServices
from ..state import GalleryState
from .grid_layout import MAX_COLUMNS
from .card_context_menu import MenuEntry
from .card_grid import CardGrid, MoreCell
from .card_size_control import size_buttons, update_size_buttons
from .viewer_controller import ViewerController


@dataclass
class GalleryCard:
    """Server-side presentation and actions for one keyed browser card."""
    data: dict


class GalleryView(ViewerController):
    """サーバー側イベント結線も単体検証できる画面単位の所有者。"""

    SIZE_COLS = MAX_COLUMNS

    def __init__(self, services: ApplicationServices, *, card_size: str = "large",
                 on_size_change=None) -> None:
        self.services = services
        self.state = GalleryState(services.artworks)
        self.state.set_card_size(card_size)
        self._on_size_change = on_size_change
        self.cards: dict[str, GalleryCard] = {}
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
                self.grid = CardGrid(self._grid_event).classes("pv-grid").style(
                    f"--pv-max-cols:{self.SIZE_COLS[card_size]}")
                self.more_cell = self.more_button = MoreCell(self.grid)
            self.empty = ui.label("該当する作品がありません").classes("pv-empty")

        self._init_viewer(services, self.state, self.root)
        self.render_gallery()

    def _card_source(self, art: Artwork):
        return self.services.images.url(art.work_id, 0, 'thumb')

    def _card_heart(self, art: Artwork) -> bool:
        return False

    def _card_menu(self, art: Artwork):
        state = self.state
        return f'gallery-ctx-{art.work_id}', (
            MenuEntry('author', '作者ページを開く', on_click=lambda: self.open_card_author(art, state),
                      enabled=bool(art.author_id and self._viewer_reader() is not None),
                      testid='gallery-author-open'),
        )

    def _card_data(self, art: Artwork, rank: int):
        source = self._card_source(art)
        menu_id, entries = self._card_menu(art) if source else (None, ())
        return dict(work_id=art.work_id, rank=f'#{rank}', src=source, title=art.title,
                    author=art.author, heart=self._card_heart(art), menu_id=menu_id,
                    menu=[dict(key=e.key, label=e.label, icon=e.icon, enabled=e.enabled,
                               href=e.href, testid=e.testid or f'{menu_id}-{e.key}')
                          for e in entries])

    async def _grid_event(self, event):
        await self.dispatch_card_event(event.args)

    async def dispatch_card_event(self, args):
        if (not self._viewer_active or self.state.closed or type(args) is not dict
                or type(args.get('epoch')) is not int or args['epoch'] != self.grid.render_epoch):
            return
        work_id, action = args.get('work_id'), args.get('action')
        if action == 'more' and work_id is None:
            if self.more_cell.visible and self.more_button.enabled:
                self.more()
            return
        card = self.cards.get(work_id) if type(work_id) is str else None
        if card is None or not card.data['src']:
            return
        if action == 'open':
            self.open(work_id)
            return
        # Recheck current availability before entering the existing action handler.
        art = self._card_artworks[work_id]
        _, entries = self._card_menu(art)
        for entry in entries:
            if entry.key == action and entry.enabled and entry.on_click and entry.href is None:
                result = entry.on_click()
                if isawaitable(result):
                    await result
                return

    async def open_card_author(self,art,state,menu=None):
        if (state is not self.state or self.state.closed or not self._viewer_active
                or art not in self.state.visible_items):return
        if menu is not None:menu.close()
        await self._open_author_art(art)

    def render_gallery(self) -> None:
        if self.state.closed:
            return
        artworks = {art.work_id: art for art in self.state.visible_items}
        prepared = [(work_id, self._card_data(art, rank))
                    for rank, (work_id, art) in enumerate(artworks.items(), start=1)]
        cards = {}
        for work_id, data in prepared:
            card = self.cards.get(work_id)
            if card is None or self._card_artworks[work_id] != artworks[work_id]:
                card = GalleryCard(data)
            else:
                card.data = data
            cards[work_id] = card
        self.cards, self._card_artworks = cards, artworks
        self.grid.props['cards'] = [card.data for card in cards.values()]
        self.grid.invalidate_actions()
        self.empty.set_visibility(not cards)
        self.more_cell.set_visibility(self.state.has_more)
        self.counter.set_text(f"{self.state.shown_count} / {self.state.total_count} 件表示")

    def set_viewer_active(self, active: bool) -> None:
        if self._viewer_active != bool(active):
            self.grid.invalidate_actions()
            self.grid.props['grid_active'] = bool(active)
        super().set_viewer_active(active)

    def search(self, value: str | None = None) -> None:
        if self.state.closed or not self._viewer_active:
            return
        self.state.set_query(value or "")
        if not self.state.viewer_open:
            super().close_viewer()
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
