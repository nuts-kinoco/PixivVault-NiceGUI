"""原版の作者一覧・作者全ページ・タブ内ビューアを保つ合成ライブラリ。"""

from __future__ import annotations

from nicegui import ui

from ..library_models import AuthorSummary, LibrarySnapshot, PageRef
from ..library_state import LibraryState
from ..services import ApplicationServices
from ..state import GalleryState
from .card_size_control import size_buttons, update_size_buttons
from .grid_layout import MAX_COLUMNS
from .viewer_controller import ViewerController


def format_capacity(value: int | None) -> str:
    if value is None:
        return "容量未計測"
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.0f} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")


class LibraryView(ViewerController):
    def __init__(self, services: ApplicationServices, *, card_size: str = "large",
                 on_size_change=None) -> None:
        self.services = services
        self.client = ui.context.client
        self.active = True
        self.closed = False
        self.card_size = card_size
        self._on_size_change = on_size_change
        self.state = LibraryState(LibrarySnapshot(()))
        self.author_cards = {}
        self.author_openers = {}
        self.page_cards = {}
        self._rendered_authors = ()
        self._rendered_pages = ()
        # Old cards may still have queued client events after a full reload.
        # LibraryState generation starts at zero again, so also compare identity.
        self._page_session = object()
        self._author_session = object()
        self._more_session = object()
        self.more_cell = None
        self.load_error = False

        with ui.column().classes("pv-library w-full").props(
            "data-testid=library-view data-active=true"
        ) as self.root:
            with ui.element("div").classes("pv-library-toolbar"):
                ui.label("ライブラリ").classes("pv-library-heading")
                with ui.row().classes("pv-library-subviews"):
                    ui.button("DL済み", color=None).props(
                        "flat dense no-caps aria-current=page"
                    ).classes("pv-library-subview-selected")
                    self._unavailable("マイブックマーク", "マイブックマークは移植準備中です")
                self.search_input = ui.input(
                    placeholder="作品名・作者・タグで検索",
                    on_change=lambda event: self.search(event.value),
                ).props("outlined dense clearable dark debounce=200 data-testid=library-search").classes(
                    "pv-library-search")
                self.search_input.on("keydown.enter", lambda _: self.search(self.search_input.value))
                ui.label("ライブラリ内").classes("pv-subtitle pv-library-scope")
                self.sort_buttons = {}
                for key in ("view_count", "last_backup"):
                    self.sort_buttons[key] = ui.button(
                        "", color=None, on_click=lambda _, value=key: self.sort(value),
                    ).props(f"flat dense no-caps data-testid=library-sort-{key}")
                self._unavailable("容量", "容量順の並べ替えは移植準備中です")
                with ui.row().classes("pv-library-sizes"):
                    self.size_buttons = size_buttons(self.change_size, prefix="library")

            with ui.row().classes("pv-library-error") as self.error_row:
                ui.label("ライブラリを読み込めませんでした").props("role=alert")
                ui.button("再読込", on_click=self.reload).props("flat data-testid=library-reload")

            with ui.column().classes("pv-library-panel w-full") as self.authors_panel:
                with ui.row().classes("pv-library-summary w-full"):
                    self._unavailable("絞り込み", "詳細な絞り込みは移植準備中です")
                    self._unavailable("容量を再計測", "保存先の容量計測は移植準備中です")
                    ui.space()
                    self.author_counter = ui.label().classes("pv-counter").props(
                        "data-testid=library-author-count")
                self.empty = ui.label().classes("pv-empty").props("data-testid=library-empty")
                with ui.element("div").classes("pv-gallery-scroll").props(
                    "data-testid=library-authors-scroll"
                ) as self.authors_scroll:
                    self.author_grid = ui.element("div").classes("pv-grid")
                    self.author_more_button = ui.button("もっと見る", on_click=self.more_authors).props(
                        "flat no-caps data-testid=library-author-more")
                self._remember_scroll(self.authors_scroll)

            with ui.column().classes("pv-library-panel pv-library-detail w-full").props(
                "data-testid=library-detail"
            ) as self.pages_panel:
                with ui.row().classes("pv-library-detail-header w-full"):
                    ui.button("← 作者一覧", color=None, on_click=self.close_author).props(
                        "flat dense no-caps data-testid=library-back")
                    self.detail_title = ui.label().classes("pv-library-detail-title")
                    ui.button(icon="close", color=None, on_click=self.close_author).props(
                        "flat dense round aria-label=作者一覧へ戻る data-testid=library-detail-close")
                self.unknown_note = ui.label().classes("pv-library-unknown").props(
                    "data-testid=library-unknown-pages")
                self.page_counter = ui.label().classes("pv-counter").props(
                    "data-testid=library-page-count")
                with ui.element("div").classes("pv-gallery-scroll").props(
                    "data-testid=library-pages-scroll"
                ) as self.pages_scroll:
                    self.page_grid = ui.element("div").classes("pv-grid")
                self._remember_scroll(self.pages_scroll)

        self._init_viewer(services, GalleryState(()), self.root, library=True)
        self.client.on_delete(self.close)
        self.apply_card_size(card_size)
        self.reload()

    @staticmethod
    def _unavailable(label: str, reason: str):
        button = ui.button(label, color=None).props("flat dense no-caps")
        button.disable()
        button.tooltip(reason)
        return button

    @staticmethod
    def _remember_scroll(element) -> None:
        element.on("scroll", js_handler="""(event) => {
            const node = event.target;
            if (node.getClientRects().length) node.__pvLibraryScroll = node.scrollTop;
        }""")

    def _restore_scroll(self, element, *, reset: bool = False) -> None:
        if not self.client.has_socket_connection:
            return
        self.client.run_javascript(f"""requestAnimationFrame(() => {{
            const root = document.getElementById('{self.root.html_id}');
            const node = document.getElementById('{element.html_id}');
            if (!root || !node) return;
            if ({str(reset).lower()}) node.__pvLibraryScroll = 0;
            if (root.dataset.active === 'true' && node.getClientRects().length)
                node.scrollTop = node.__pvLibraryScroll || 0;
        }})""")

    def _sync_panels(self) -> None:
        selected = self.state.selected_author_id is not None
        self.root.props["data-library-generation"] = str(self.state.generation)
        self.authors_panel.set_visibility(not selected and not self.load_error)
        self.pages_panel.set_visibility(selected and not self.load_error)
        self.error_row.set_visibility(self.load_error)
        for key, button in self.sort_buttons.items():
            label = "閲覧数" if key == "view_count" else "保存日"
            direction = "↓" if self.state.sort_directions[key] else "↑"
            button.set_text(label + direction)
            button.props(f"aria-pressed={'true' if key == self.state.sort_key else 'false'}")

    def reload(self) -> None:
        if self.closed or not self.active:
            return
        self.close_viewer(restore_focus=False)
        self.state.close()
        self._viewer_state.close()
        self.author_grid.clear()
        self.author_cards.clear()
        self.author_openers.clear()
        self._rendered_authors = ()
        self._clear_pages()
        self._page_session = object()
        self._author_session = object()
        self.state = LibraryState(LibrarySnapshot(()))
        self._viewer_state = GalleryState(())
        try:
            snapshot = self.services.library_snapshot()
            state = LibraryState(snapshot)
            viewer_state = GalleryState(work.artwork for work in snapshot.works)
        except Exception:
            # 読取失敗を「保存済み0件」へ置き換えない。例外内容を画面へ出さない。
            self.load_error = True
            self._sync_panels()
            return
        self.state = state
        self._viewer_state = viewer_state
        self.load_error = False
        self.search_input.set_value("")
        self.render_authors()
        self._sync_panels()

    def _make_author_card(self, author: AuthorSummary) -> None:
        with self.author_grid:
            with ui.element("div").classes("pv-card pv-library-author-card").props(
                f"data-author-id={author.author_id} data-representative-id={author.representative.work_id}"
            ) as card:
                with ui.element("div").classes("pv-library-thumb-wrap"):
                    with ui.element("div").classes("pv-library-author-open").props(
                        "role=button tabindex=0 data-testid=library-author-open"
                    ) as opener:
                        opener.props["aria-label"] = author.name
                        session = self._author_session
                        opener.on("click", lambda _, uid=author.author_id, owner=session: self.open_author(uid, owner))
                        opener.on("keydown.enter", lambda _, uid=author.author_id, owner=session: self.open_author(uid, owner))
                        ui.image(self.services.images.url(author.representative.work_id, 0, "thumb")).props(
                            "fit=cover loading=lazy no-transition"
                        ).classes("pv-thumb")
                    profile = ui.button(icon="open_in_new", color=None).props(
                        "flat dense round aria-label=pixivの作者ページ").classes("pv-library-profile")
                    profile.disable()
                    profile.tooltip("pixivの作者ページを開く操作は移植準備中です")
                ui.label(author.name).classes("pv-library-card-title")
                with ui.row().classes("pv-library-card-info"):
                    ui.label(f"{author.work_count}点")
                    ui.label(f"閲覧 {author.view_count}回")
                    ui.label(format_capacity(author.capacity_bytes))
        self.author_cards[author.author_id] = card
        self.author_openers[author.author_id] = opener

    def render_authors(self) -> None:
        visible = self.state.visible_authors
        wanted = tuple(author.author_id for author in visible)
        if wanted[:len(self._rendered_authors)] != self._rendered_authors:
            self.author_grid.clear()
            self.author_cards.clear()
            self.author_openers.clear()
            self._rendered_authors = ()
            self._author_session = object()
        for author in visible[len(self._rendered_authors):]:
            self._make_author_card(author)
        self._rendered_authors = wanted
        self.author_counter.set_text(
            f"{self.state.shown_author_count} / {self.state.matched_author_count} 作者表示"
            f"（全{self.state.total_author_count}作者）")
        self.empty.set_text("保存済み作品がありません" if not self.state.total_author_count
                            else "条件に合う作者がいません")
        self.empty.set_visibility(not wanted)
        self.author_more_button.set_visibility(self.state.has_more_authors)

    def more_authors(self) -> None:
        if self.active and not self.closed and not self.load_error and self.state.load_more_authors():
            self.render_authors()

    def _clear_pages(self) -> None:
        self._more_session = object()
        self.page_grid.clear()
        self.page_cards.clear()
        self._rendered_pages = ()
        self.more_cell = None

    def _make_page_card(self, page: PageRef) -> None:
        work = page.work
        generation = self.state.generation
        with self.page_grid:
            with ui.element("div").classes("pv-card pv-library-page-card").props(
                f"role=button tabindex=0 data-work-id={work.work_id} data-page-no={page.page_no}"
            ) as card:
                callback = lambda _, wid=work.work_id, number=page.page_no, epoch=generation, session=self._page_session: self.open_page(
                    wid, number, epoch, session)
                card.on("click", callback)
                card.on("keydown.enter", callback)
                ui.image(self.services.images.url(work.work_id, page.page_no, "thumb")).props(
                    "fit=cover loading=lazy no-transition"
                ).classes("pv-thumb")
                ui.label(work.artwork.title).classes("pv-library-card-title")
                saved = work.last_backup[:10] if work.last_backup else "保存日 不明"
                ui.label(f"p.{page.page_no + 1} ／ {saved}").classes("pv-library-card-info")
        self.page_cards[page.key] = card

    def render_pages(self) -> None:
        author = self.state.selected_author
        if author is None:
            return
        wanted = tuple(page.key for page in self.state.visible_pages)
        if wanted[:len(self._rendered_pages)] != self._rendered_pages:
            self._clear_pages()
        for page in self.state.visible_pages[len(self._rendered_pages):]:
            self._make_page_card(page)
        self._rendered_pages = wanted
        if self.more_cell is None:
            with self.page_grid:
                with ui.element("div").classes("pv-more-cell pv-library-more") as self.more_cell:
                    self.more_button = ui.button("もっと見る", icon="add_circle_outline",
                                                 color=None, on_click=lambda _, session=self._more_session: self.more(session)).props(
                        "flat stack no-caps data-testid=library-more")
        else:
            self.more_cell.move(self.page_grid)
        self.more_cell.set_visibility(self.state.has_more_pages)
        self.detail_title.set_text(
            f"{author.name} の作品 {author.work_count} 件・画像 {self.state.total_page_count} 枚"
            "（絞り込みは作者一覧にのみ効きます）")
        self.unknown_note.set_text(
            f"うち{self.state.unknown_page_work_count}件はページ数が分からないため1枚だけ表示しています")
        self.unknown_note.set_visibility(bool(self.state.unknown_page_work_count))
        self.page_counter.set_text(
            f"{self.state.shown_page_count} / {self.state.total_page_count} 枚表示")

    def search(self, value: str | None) -> None:
        if self.closed or not self.active or self.load_error:
            return
        if self.state.set_query(value or ""):
            self.close_viewer(restore_focus=False)
            self._clear_pages()
            self.render_authors()
            self._sync_panels()
            self._restore_scroll(self.authors_scroll, reset=True)

    def sort(self, key: str) -> None:
        if self.closed or not self.active or self.load_error:
            return
        if self.state.select_sort(key):
            self.close_viewer(restore_focus=False)
            self._clear_pages()
            self.render_authors()
            self._sync_panels()
            self._restore_scroll(self.authors_scroll, reset=True)

    def open_author(self, author_id: str, session: object | None = None) -> None:
        if self.closed or not self.active or self.load_error:
            return
        if session is not None and session is not self._author_session:
            return
        if self.state.open_author(author_id):
            self.close_viewer(restore_focus=False)
            self._clear_pages()
            self.render_pages()
            self._sync_panels()
            self._restore_scroll(self.pages_scroll, reset=True)

    def close_author(self) -> None:
        if self.closed or not self.active:
            return
        author_id = self.state.selected_author_id
        if not self.state.close_author():
            return
        self.close_viewer(restore_focus=False)
        self._clear_pages()
        self._sync_panels()
        self._restore_scroll(self.authors_scroll)
        opener = self.author_openers.get(author_id)
        if opener is not None and self.client.has_socket_connection:
            self.client.run_javascript(f"""requestAnimationFrame(() => {{
                const root = document.getElementById('{self.root.html_id}');
                const node = document.getElementById('{opener.html_id}');
                if (root?.dataset.active === 'true' &&
                    root.dataset.libraryGeneration === '{self.state.generation}' &&
                    node?.isConnected && node.getClientRects().length)
                    node.focus({{preventScroll:true}});
            }})""")

    def open_page(self, work_id: str, page_no: int, generation: int | None = None,
                  session: object | None = None) -> None:
        if (self.closed or not self.active or self.load_error or
                (session is not None and session is not self._page_session) or
                (generation is not None and generation != self.state.generation)):
            return
        if (work_id, page_no) not in self.page_cards:
            return
        super().open(work_id, page_no)

    def more(self, session: object | None = None) -> None:
        if (self.active and not self.closed and not self.load_error and
                (session is None or session is self._more_session) and
                self.state.load_more_pages()):
            self.render_pages()

    def change_size(self, size: str) -> None:
        if self.closed or not self.active:
            return
        if self._on_size_change is not None:
            self._on_size_change(size)
        else:
            self.apply_card_size(size)

    def apply_card_size(self, size: str) -> None:
        if size not in MAX_COLUMNS:
            raise ValueError("invalid card size")
        if self.closed:
            return
        self.card_size = size
        for grid in (self.author_grid, self.page_grid):
            grid.style(f"--pv-max-cols:{MAX_COLUMNS[size]}")
        update_size_buttons(self.size_buttons, size)

    def set_active(self, active: bool) -> None:
        self.active = bool(active)
        self.root.props["data-active"] = str(self.active).lower()
        self.set_viewer_active(active)
        if active:
            self._restore_scroll(self.pages_scroll if self.state.selected_author else self.authors_scroll)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.active = False
        self.state.close()
        self._close_viewer_controller()
