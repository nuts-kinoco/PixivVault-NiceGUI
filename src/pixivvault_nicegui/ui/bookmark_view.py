"""Synthetic My Bookmarks card grid; network and persistence belong to the caller."""

from __future__ import annotations

from collections.abc import Callable
import inspect
from uuid import uuid4

from nicegui import ui

from ..bookmark_state import BookmarkState, BookmarkCard
from .grid_layout import MAX_COLUMNS


class BookmarkView:
    def __init__(self, state: BookmarkState, *, on_refresh: Callable[[], None] | None = None,
                 on_open: Callable[[str], None] | None = None,
                 on_enqueue: Callable | None = None,
                 on_similar: Callable[[str], object] | None = None,
                 image_url: Callable[[str], str] | None = None,
                 can_open: Callable[[str], bool] | None = None,
                 with_filter_toggle: bool = False) -> None:
        self.state = state
        self.on_refresh = on_refresh
        self.on_open = on_open
        self.on_enqueue = on_enqueue
        self.on_similar = on_similar
        self.image_url = image_url
        self.can_open = can_open
        self.with_filter_toggle = with_filter_toggle
        self.filter_toggle = None
        self.root = None
        self.grid = None
        self.cards: dict[str, ui.element] = {}
        self.checkboxes: dict[str, ui.checkbox] = {}
        self._rendered_ids: tuple[str, ...] = ()
        self._rendered_cards: tuple[BookmarkCard, ...] = ()
        self._session = object()
        self.more_cell = None
        self.card_size = "large"
        self._download_busy = False
        self._download_operations: dict[str,str] = {}

    def render(self, parent=None):
        if self.root is not None:
            return self.root
        container = parent if parent is not None else ui.context.slot.parent
        with container:
            with ui.column().classes("pv-library-panel pv-bookmarks w-full gap-3").props(
                "data-testid=bookmark-view"
            ) as self.root:
                with ui.row().classes("pv-library-summary w-full items-center"):
                    if self.with_filter_toggle:
                        self.filter_toggle = ui.button("絞り込み ›").props(
                            "flat dense no-caps data-testid=bookmark-filter-toggle")
                    ui.label("マイブックマーク").classes("pv-library-heading")
                    ui.space()
                    self.counter = ui.label().classes("pv-counter").props("data-testid=bookmark-count")
                    self.selected_count = ui.label().classes("pv-counter").props(
                        "data-testid=bookmark-selection")
                    self.download_button = ui.button("選択した作品をDL", color=None,
                        on_click=self.download_selected).props(
                        "flat dense data-testid=bookmark-download")
                    self.download_button.disable()
                    if self.on_enqueue is None:
                        self.download_button.tooltip("この起動では作品の保存先が未接続です")
                    self.retry = ui.button("再読込", on_click=self.request_refresh).props(
                        "flat dense data-testid=bookmark-refresh")
                self.search_input = ui.input("ブックマーク内を検索", value=self.state.query,
                    on_change=lambda event: self.search(event.value or "")).props(
                    "outlined dense clearable debounce=200 data-testid=bookmark-search")
                self.message = ui.label().classes("pv-empty").props("data-testid=bookmark-status role=status")
                self.download_status = ui.label("").classes("pv-subtitle").props(
                    "role=status data-testid=bookmark-download-status")
                with ui.element("div").classes("pv-gallery-scroll").props("data-testid=bookmark-scroll") as self.scroll:
                    self.grid = ui.element("div").classes("pv-grid").style(
                        f"--pv-max-cols:{MAX_COLUMNS[self.card_size]}")
                self.scroll.on("scroll", js_handler="""event => {
                    if (event.target.getClientRects().length)
                        event.target.__pvBookmarkScroll = event.target.scrollTop;
                }""")
        self.refresh()
        return self.root

    def _make_card(self, item: BookmarkCard) -> None:
        art = item.artwork
        with self.grid:
            with ui.element("div").classes("pv-card pv-bookmark-card").props(
                f"data-work-id={art.work_id} data-testid=bookmark-card"
            ) as card:
                with ui.element("div").classes("pv-library-thumb-wrap"):
                    with ui.element("div").classes("pv-library-author-open").props(
                        "role=button tabindex=0 aria-label=作品を開く data-testid=bookmark-open"
                    ) as opener:
                        if self.can_open is not None and not self.can_open(art.work_id):
                            opener.props("aria-disabled=true tabindex=-1")
                            ui.tooltip("表示未対応の形式、または利用できる画像接続がありません")
                        opener.on("click", lambda _, wid=art.work_id, token=self._session:
                                  self.open(wid, token))
                        opener.on("keydown.enter", lambda _, wid=art.work_id,
                                  token=self._session: self.open(wid, token))
                        if self.image_url is not None:
                            image = ui.image(self.image_url(art.work_id))
                            image.props("fit=contain loading=lazy no-transition").classes("pv-thumb")
                        else:
                            ui.icon("image").classes("pv-thumb")
                    if item.downloaded:
                        ui.label("DL済み").classes("pv-bookmark-badge").props(
                            "data-testid=bookmark-downloaded")
                ui.label(art.title).classes("pv-card-title")
                ui.label(art.author).classes("pv-card-author")
                checked = art.work_id in self.state.selected_ids
                choice = ui.checkbox("選択", value=checked, on_change=lambda event,
                    wid=art.work_id, token=self._session: self.select(wid, bool(event.value), token))
                choice.props("data-testid=bookmark-select")
                if self.on_similar is not None:
                    with ui.context_menu().props('data-testid=bookmark-similar-context') as menu:
                        ui.button('似た絵を探す',on_click=lambda _,wid=art.work_id,
                                  token=self._session,popup=menu:self.search_similar(wid,token,popup)).props(
                            'flat dense data-testid=bookmark-similar-open')
        self.cards[art.work_id] = card
        self.checkboxes[art.work_id] = choice

    def refresh(self, *, reset_controls: bool = False) -> None:
        if self.root is None or self.state.closed:
            return
        visible = self.state.visible_items
        wanted = tuple(card.work_id for card in visible)
        if (reset_controls or wanted[:len(self._rendered_ids)] != self._rendered_ids
                or tuple(visible[:len(self._rendered_cards)]) != self._rendered_cards):
            self.grid.clear()
            self.cards.clear()
            self.checkboxes.clear()
            self.more_cell = None
            self._rendered_ids = ()
            self._rendered_cards = ()
            self._session = object()
        for item in visible[len(self._rendered_ids):]:
            self._make_card(item)
        self._rendered_ids = wanted
        self._rendered_cards = visible
        for wid, checkbox in self.checkboxes.items():
            if checkbox.value != (wid in self.state.selected_ids):
                checkbox.set_value(wid in self.state.selected_ids)
        if self.more_cell is None:
            with self.grid:
                with ui.element("div").classes("pv-more-cell") as self.more_cell:
                    self.more_button = ui.button(
                        "もっと見る",
                        on_click=lambda _, session=self._session: self.more(session),
                    ).props(
                        "flat no-caps data-testid=bookmark-more")
        else:
            self.more_cell.move(self.grid)
        self.more_cell.set_visibility(self.state.has_more)
        status = self.state.status
        messages = {
            "loading": "ブックマークを読み込み中です",
            "fetch_failed": "ブックマークの取得に失敗しました。再読込できます",
            "empty": "ブックマークはありません",
            "unloaded": "ブックマークを読み込んでいません",
            "no_match": "条件に合うブックマークがありません",
        }
        self.message.set_text(messages.get(status, ""))
        self.message.set_visibility(status != "ready")
        self.retry.set_visibility(status != "loading")
        self.counter.set_text(f"{len(visible)} / {len(self.state.matched_items)} 件表示")
        self.selected_count.set_text(f"選択中: {len(self.state.selected_ids)}件")
        self.download_button.set_enabled(bool(self.state.active and not self.state.closed
            and self.state.selected_ids and self.on_enqueue is not None and not self._download_busy))

    async def download_selected(self):
        """Batch explicit bookmarks through the normal durable job admission.

        The selected IDs are frozen before the first await; a tab change does not
        cancel a user's accepted batch. Reception is never called a saved file.
        """
        if (self._download_busy or self.on_enqueue is None
                or self.state.closed or not self.state.active):
            return False
        chosen=tuple(item.work_id for item in self.state.items
                     if item.work_id in self.state.selected_ids)
        if not chosen:
            self.refresh()
            return False
        self._download_busy=True
        self.refresh()
        accepted=0;failed=0;interrupted=False
        self.download_status.set_text(f"選択した {len(chosen)} 件を保存キューへ受け付けています")
        try:
            for wid in chosen:
                if self.state.closed:
                    interrupted=True
                    break
                operation=self._download_operations.setdefault(wid,uuid4().hex)
                try:
                    result=self.on_enqueue(wid,operation,
                        is_current=lambda:not self.state.closed)
                    job=await result if inspect.isawaitable(result) else result
                except Exception:
                    failed+=1
                else:
                    if job is None:
                        interrupted=True
                        break
                    accepted+=1
        finally:
            self._download_busy=False
            if not self.state.closed:
                message=f"保存キュー受付 {accepted} 件"
                if failed: message+=f"・受付できなかった作品 {failed} 件（選択は保持）"
                if interrupted: message+="・受付が中断されました"
                message+="。保存完了・失敗はキューで確認してください。"
                self.download_status.set_text(message)
                self.refresh()
        return failed==0 and not interrupted

    async def search_similar(self,work_id,session,menu=None):
        if (self.state.closed or not self.state.active or self.state.loading
                or self.on_similar is None or session is not self._session
                or work_id not in self.cards
                or not any(item.work_id==work_id for item in self.state.visible_items)):
            return False
        if menu is not None:menu.close()
        result=self.on_similar(work_id)
        if inspect.isawaitable(result):await result
        return True

    def select(self, work_id: str, checked: bool, session=None) -> None:
        if self.state.closed or not self.state.active or (session is not None and session is not self._session):
            return
        if checked:
            self.state.select(work_id)
        else:
            self.state.deselect(work_id)
        self.refresh()

    def open(self, work_id: str, session=None) -> None:
        if (self.state.closed or not self.state.active or self.on_open is None or
                (session is not None and session is not self._session) or
                work_id not in self.cards or
                (self.can_open is not None and not self.can_open(work_id))):
            return
        self.on_open(work_id)

    def more(self, session=None) -> None:
        if ((session is None or session is self._session) and self.state.load_more()):
            self.refresh()

    def request_refresh(self) -> None:
        if self.state.closed or not self.state.active or self.on_refresh is None:
            return
        result = self.on_refresh()
        if inspect.isawaitable(result):
            from nicegui import background_tasks
            background_tasks.create(result)
        self.refresh()

    def search(self, query: str) -> None:
        if self.state.set_query(query):
            self.refresh()
            if self.root is not None and self.root.client.has_socket_connection:
                self.root.client.run_javascript(
                    f"document.getElementById('{self.scroll.html_id}').scrollTop = 0;")

    def set_active(self, active: bool) -> None:
        if self.state.active != active:
            # Invalidate queued controls from the previous visit. Rebuild
            # handlers with the new identity even when the work IDs are equal.
            self._session = object()
            self._rendered_ids = ()
            self._rendered_cards = ()
            if self.grid is not None:
                self.grid.clear()
            self.cards.clear()
            self.checkboxes.clear()
            self.more_cell = None
        self.state.revisit(active)
        if self.root is not None:
            self.root.props["data-active"] = str(active).lower()
            self.refresh()
            if active and self.root.client.has_socket_connection:
                self.root.client.run_javascript(f"""requestAnimationFrame(() => {{
                    const root = document.getElementById('{self.root.html_id}');
                    const node = document.getElementById('{self.scroll.html_id}');
                    if (root?.dataset.active === 'true' && node?.getClientRects().length)
                        node.scrollTop = node.__pvBookmarkScroll || 0;
                }})""")

    def apply_card_size(self, size: str) -> None:
        if size not in MAX_COLUMNS:
            raise ValueError("invalid card size")
        self.card_size = size
        if self.grid is not None and not self.state.closed:
            self.grid.style(f"--pv-max-cols:{MAX_COLUMNS[size]}")

    def close(self) -> None:
        self.state.close()
        self._session = object()
