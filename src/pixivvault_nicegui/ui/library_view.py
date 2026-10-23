"""原版の作者一覧・作者全ページ・タブ内ビューアを保つ合成ライブラリ。"""

from __future__ import annotations

import asyncio
from nicegui import ui

from ..bookmark_state import BookmarkState
from ..library_filters import LibraryFilters
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
        self._image_provider = services.acquire_library_images()
        self.active = True
        self.closed = False
        self.subview = "downloaded"
        self.card_size = card_size
        self._on_size_change = on_size_change
        self.filters = LibraryFilters()
        self.bookmark_state = BookmarkState(
            local=services.bookmark_local_snapshot(),
            cached_remote=services.bookmark_remote_snapshot(),
        )
        self.bookmark_state.fetch_failed = services.bookmark_cache_failed
        self.bookmarks = None
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
        self.refresh_error = False
        self._refresh_epoch = 0
        self._refreshing = False
        self._bookmark_refreshing = False
        self._bookmark_open_epoch = 0
        self._resolved_bookmark_artwork = {}

        with ui.column().classes("pv-library w-full").props(
            "data-testid=library-view data-active=true"
        ) as self.root:
            with ui.element("div").classes("pv-library-toolbar"):
                ui.label("ライブラリ").classes("pv-library-heading")
                with ui.row().classes("pv-library-subviews"):
                    self.downloaded_button = ui.button(
                        "DL済み", color=None, on_click=lambda: self.show_subview("downloaded")
                    ).props("flat dense no-caps data-testid=library-subview-downloaded"
                            ).classes("pv-library-subview-selected")
                    self.bookmarks_button = ui.button(
                        "マイブックマーク", color=None, on_click=lambda: self.show_subview("bookmarks")
                    ).props("flat dense no-caps data-testid=library-subview-bookmarks")
                self.search_input = ui.input(
                    placeholder="作品名・作者・タグで検索",
                    on_change=lambda event: self.search(event.value),
                ).props("outlined dense clearable dark debounce=200 data-testid=library-search").classes(
                    "pv-library-search")
                self.search_input.on("keydown.enter", lambda _: self.search(self.search_input.value))
                ui.label("ライブラリ内").classes("pv-subtitle pv-library-scope")
                self.db_refresh_button = None
                if services.library_refresh_available:
                    self.db_refresh_button = ui.button(
                        "保存先更新" if services.managed_downloads is not None else "DB更新",
                        color=None, on_click=self.refresh_from_disk).props(
                        "flat dense no-caps data-testid=library-db-refresh")
                self.sort_buttons = {}
                for key in ("view_count", "last_backup"):
                    self.sort_buttons[key] = ui.button(
                        "", color=None, on_click=lambda _, value=key: self.sort(value),
                    ).props(f"flat dense no-caps data-testid=library-sort-{key}")
                self._unavailable("容量", "容量順の並べ替えは移植準備中です")
                with ui.row().classes("pv-library-sizes"):
                    self.size_buttons = size_buttons(self.change_size, prefix="library")

            self.filter_now_host = ui.row().classes("pv-library-filter-now-host w-full")
            self.filter_now_host.set_visibility(False)

            with ui.row().classes("pv-library-error") as self.error_row:
                ui.label("ライブラリを読み込めませんでした").props("role=alert")
                ui.button("再読込", on_click=(self.refresh_from_disk
                    if services.library_refresh_available else self.reload)).props(
                    "flat data-testid=library-reload")

            with ui.column().classes("pv-library-panel w-full") as self.authors_panel:
                with ui.row().classes("pv-library-summary w-full"):
                    self.filter_toggle = ui.button("絞り込み ›").props(
                        "flat dense no-caps data-testid=library-filter-toggle")
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

            from .bookmark_view import BookmarkView
            self.bookmarks = BookmarkView(
                self.bookmark_state,
                on_refresh=self.refresh_bookmarks,
                on_open=self.open_bookmark,
                image_url=lambda work_id: self._image_provider.url(work_id, 0, "thumb"),
                can_open=lambda work_id: self._image_provider.can_open(work_id),
                with_filter_toggle=True,
            )
            self.bookmarks.render()
            self.bookmarks.root.set_visibility(False)
            self.bookmarks.set_active(False)

            from .library_filter_panel import LibraryFilterPanel
            with ui.row().classes("pv-library-body w-full no-wrap") as self.body:
                self.filter_panel = LibraryFilterPanel(
                    get_filters=lambda: self.filters, on_change=self.apply_filters,
                    can_edit=self._filters_editable, body=self.body, now_parent=self.filter_now_host)
                self.filter_content = ui.column().classes("pv-library-filter-content")
            for panel in (self.authors_panel, self.pages_panel, self.bookmarks.root):
                panel.move(self.filter_content)
            self.filter_panel.register_toggle(self.filter_toggle)
            self.filter_panel.register_toggle(self.bookmarks.filter_toggle)

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
        downloaded = self.subview == "downloaded"
        self.authors_panel.set_visibility(downloaded and not selected and not self.load_error)
        self.pages_panel.set_visibility(downloaded and selected and not self.load_error)
        self.error_row.set_visibility(downloaded and (self.load_error or self.refresh_error))
        self.filter_panel.set_subview(self.subview)
        self.search_input.set_visibility(downloaded)
        self.downloaded_button.classes(
            add="pv-library-subview-selected" if downloaded else "",
            remove="" if downloaded else "pv-library-subview-selected",
        )
        self.bookmarks_button.classes(
            add="pv-library-subview-selected" if not downloaded else "",
            remove="" if not downloaded else "pv-library-subview-selected",
        )
        self.downloaded_button.props(
            "aria-current=page" if downloaded else "aria-current=false")
        self.bookmarks_button.props(
            "aria-current=page" if not downloaded else "aria-current=false")
        for button in self.sort_buttons.values():
            button.set_visibility(downloaded)
        if self.bookmarks is not None:
            self.bookmarks.root.set_visibility(not downloaded)
            self.bookmarks.set_active(self.active and not downloaded)
        for key, button in self.sort_buttons.items():
            label = "閲覧数" if key == "view_count" else "保存日"
            direction = "↓" if self.state.sort_directions[key] else "↑"
            button.set_text(label + direction)
            button.props(f"aria-pressed={'true' if key == self.state.sort_key else 'false'}")

    def _filters_editable(self) -> bool:
        viewer_state = getattr(self, "_viewer_state", None)
        return (not self.closed and self.active and
                not (viewer_state is not None and viewer_state.viewer_open))

    def apply_filters(self, filters: LibraryFilters) -> bool:
        if not isinstance(filters, LibraryFilters):
            raise TypeError("filters must be LibraryFilters")
        if not self._filters_editable() or filters == self.filters:
            return False
        self.filters = filters
        self._bookmark_open_epoch += 1
        self.state.set_filters(filters)
        self.bookmark_state.set_filters(filters)
        self.close_viewer(restore_focus=False)
        self._clear_pages()
        # A changed condition retires callbacks even when the resulting IDs
        # happen to be identical. Appending pages still keeps existing nodes.
        self._author_session = object()
        self.author_grid.clear()
        self.author_cards.clear()
        self.author_openers.clear()
        self._rendered_authors = ()
        self.render_authors()
        self.bookmarks.refresh(reset_controls=True)
        self._sync_panels()
        self._restore_scroll(self.authors_scroll, reset=True)
        if self.client.has_socket_connection:
            self.client.run_javascript(
                f"const node=document.getElementById('{self.bookmarks.scroll.html_id}');"
                "if(node){node.__pvBookmarkScroll=0;node.scrollTop=0;}")
        return True

    def show_subview(self, subview: str) -> None:
        if self.closed or not self.active or subview not in {"downloaded", "bookmarks"}:
            return
        if subview == self.subview:
            return
        self.close_viewer(restore_focus=False)
        self._bookmark_open_epoch += 1
        self.subview = subview
        self._sync_panels()
        if subview == "bookmarks" and not self.bookmark_state.has_remote_cache:
            self.refresh_bookmarks()

    def refresh_bookmarks(self) -> None:
        """Keep demo refresh immediate; real DB/network work is off the UI loop."""
        if self.closed or not self.active or self.subview != "bookmarks" or self._bookmark_refreshing:
            return
        epoch = self.bookmark_state.begin_fetch()
        if epoch is None:
            return
        if self.services.library_repository is not None or self.services.live_client is not None:
            from nicegui import background_tasks
            self._bookmark_refreshing = True
            self.bookmarks.refresh()
            background_tasks.create(self._refresh_bookmarks_async(epoch))
            return
        try:
            remote = self.services.bookmark_remote_snapshot()
        except Exception:
            self.bookmark_state.complete_fetch(epoch, (), ok=False)
        else:
            self.bookmark_state.complete_fetch(epoch, remote, ok=True)
        if self.bookmarks is not None:
            self.bookmarks.refresh()

    async def _refresh_bookmarks_async(self, epoch: int) -> None:
        try:
            remote, local = await self.services.refresh_bookmark_cache()
        except Exception:
            accepted = self.bookmark_state.complete_fetch(epoch, (), ok=False)
        else:
            accepted = self.bookmark_state.complete_fetch(epoch, remote, ok=True)
            if accepted:
                self.bookmark_state.replace_local(local)
                self.close_viewer(restore_focus=False)
                self._bookmark_open_epoch += 1
                self._resolved_bookmark_artwork.clear()
                previous = self._image_provider
                self._image_provider = self.services.acquire_library_images()
                previous.close()
                self.reload(preserve=True)
        finally:
            self._bookmark_refreshing = False
        if accepted and not self.closed and self.bookmarks is not None:
            with self.client:
                self.bookmarks.refresh()

    def _build_viewer_state(self) -> None:
        # Local data wins for downloaded works; remote-only entries must also
        # exist in the viewer instead of being silently ignored on click.
        artwork = {work.work_id: work.artwork for work in self.state.snapshot.works}
        for card in self.bookmark_state.items:
            artwork.setdefault(card.work_id,
                self._image_provider.artwork_for(card.work_id) or
                self._resolved_bookmark_artwork.get(card.work_id, card.artwork))
        self._viewer_state.close()
        self._viewer_state = GalleryState(artwork.values())

    def open_bookmark(self, work_id: str) -> None:
        if (self.closed or not self.active or self.subview != "bookmarks"
                or work_id not in {card.work_id for card in self.bookmark_state.items}):
            return
        if not self._image_provider.can_open(work_id):
            return
        if self._image_provider.needs_metadata(work_id):
            from nicegui import background_tasks
            self._bookmark_open_epoch += 1
            epoch, provider = self._bookmark_open_epoch, self._image_provider
            background_tasks.create(self._open_unknown_bookmark(work_id, epoch, provider))
            return
        self._bookmark_open_epoch += 1
        self._build_viewer_state()
        super().open(work_id, 0)

    async def _open_unknown_bookmark(self, work_id, epoch, provider):
        try:
            artwork = await asyncio.to_thread(provider.prepare_artwork, work_id)
        except Exception:
            if (not self.closed and self.active and self.subview == "bookmarks"
                    and epoch == self._bookmark_open_epoch and self._image_provider is provider):
                self.bookmarks.message.set_text("作品情報を取得できませんでした。もう一度作品を選択してください")
                self.bookmarks.message.set_visibility(True)
            return
        if (self.closed or not self.active or self.subview != "bookmarks"
                or epoch != self._bookmark_open_epoch or self._image_provider is not provider):
            return
        self._resolved_bookmark_artwork[work_id] = artwork
        with self.client:
            self._build_viewer_state()
            super().open(work_id, 0)

    def reload(self, *, preserve: bool = False) -> None:
        if self.closed or not self.active:
            return
        self.filter_panel.invalidate()
        previous_state = self.state
        saved_query = previous_state.query
        saved_sort = previous_state.sort_key
        saved_directions = dict(previous_state.sort_directions)
        saved_author = previous_state.selected_author_id
        saved_author_limit = previous_state.shown_author_count
        saved_page_limit = previous_state.shown_page_count
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
            state.set_filters(self.filters)
            if preserve:
                state.query = saved_query
                state.sort_key = saved_sort
                state.sort_directions.update(saved_directions)
                state._refresh_matches()
                while state.shown_author_count < saved_author_limit and state.has_more_authors:
                    state.load_more_authors()
                if saved_author is not None and state.open_author(saved_author):
                    while state.shown_page_count < saved_page_limit and state.has_more_pages:
                        state.load_more_pages()
            viewer_state = GalleryState(work.artwork for work in snapshot.works)
        except Exception:
            # 読取失敗を「保存済み0件」へ置き換えない。例外内容を画面へ出さない。
            self.load_error = True
            self._sync_panels()
            return
        self.state = state
        self._viewer_state = viewer_state
        previous = self._image_provider
        self._image_provider = self.services.acquire_library_images()
        previous.close()
        self._build_viewer_state()
        self.load_error = False
        self.refresh_error = False
        self.search_input.set_value(state.query)
        self.render_authors()
        if state.selected_author is not None:
            self.render_pages()
        self._sync_panels()
        if preserve:
            self._restore_scroll(self.pages_scroll if state.selected_author is not None else self.authors_scroll)

    async def refresh_from_disk(self) -> None:
        if (self.closed or not self.active or self._refreshing or
                not self.services.library_refresh_available):
            return
        self._refresh_epoch += 1
        epoch = self._refresh_epoch
        state = self.state
        self._refreshing = True
        if self.db_refresh_button is not None:
            self.db_refresh_button.disable()
        try:
            await self.services.refresh_library_snapshot()
        except Exception:
            if not self.closed and self.active and self.state is state and epoch == self._refresh_epoch:
                self.refresh_error = True
                self._sync_panels()
        else:
            if (not self.closed and self.active and self.state is state and
                    epoch == self._refresh_epoch):
                # Keep searches/selections made while disk I/O was in progress.
                # A full reload or tab leave still invalidates state/epoch ownership.
                self.bookmark_state.replace_local(self.services.bookmark_local_snapshot())
                self.reload(preserve=True)
                self.bookmarks.refresh()
        finally:
            if epoch == self._refresh_epoch:
                self._refreshing = False
                if self.db_refresh_button is not None and not self.closed:
                    self.db_refresh_button.enable()

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
                        ui.image(self._image_provider.url(author.representative.work_id, 0, "thumb")).props(
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
        if (self.subview == "downloaded" and self.active and not self.closed
                and not self.load_error and self.state.load_more_authors()):
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
                if not self._image_provider.can_open(work.work_id):
                    card.props("aria-disabled=true tabindex=-1")
                    ui.tooltip("この形式の表示は未接続です")
                callback = lambda _, wid=work.work_id, number=page.page_no, epoch=generation, session=self._page_session: self.open_page(
                    wid, number, epoch, session)
                card.on("click", callback)
                card.on("keydown.enter", callback)
                ui.image(self._image_provider.url(work.work_id, page.page_no, "thumb")).props(
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
        if self.closed or not self.active or self.load_error or self.subview != "downloaded":
            return
        if self.state.set_query(value or ""):
            self.close_viewer(restore_focus=False)
            self._clear_pages()
            self.render_authors()
            self._sync_panels()
            self._restore_scroll(self.authors_scroll, reset=True)

    def sort(self, key: str) -> None:
        if self.closed or not self.active or self.load_error or self.subview != "downloaded":
            return
        if self.state.select_sort(key):
            self.close_viewer(restore_focus=False)
            self._clear_pages()
            self.render_authors()
            self._sync_panels()
            self._restore_scroll(self.authors_scroll, reset=True)

    def open_author(self, author_id: str, session: object | None = None) -> None:
        if self.closed or not self.active or self.load_error or self.subview != "downloaded":
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
        if self.closed or not self.active or self.subview != "downloaded":
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
        if (self.closed or not self.active or self.load_error or self.subview != "downloaded" or
                (session is not None and session is not self._page_session) or
                (generation is not None and generation != self.state.generation)):
            return
        if (work_id, page_no) not in self.page_cards:
            return
        if not self._image_provider.can_open(work_id):
            return
        super().open(work_id, page_no)

    def more(self, session: object | None = None) -> None:
        if (self.subview == "downloaded" and self.active and not self.closed and not self.load_error and
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
        if self.bookmarks is not None:
            self.bookmarks.apply_card_size(size)
        update_size_buttons(self.size_buttons, size)

    def set_active(self, active: bool) -> None:
        if self.active != bool(active):
            self.filter_panel.invalidate()
            self._bookmark_open_epoch += 1
            self._refresh_epoch += 1
            self._refreshing = False
            if self.db_refresh_button is not None:
                self.db_refresh_button.enable()
        self.active = bool(active)
        self.root.props["data-active"] = str(self.active).lower()
        self.set_viewer_active(active)
        if self.bookmarks is not None:
            self.bookmarks.set_active(self.active and self.subview == "bookmarks")
        if active:
            self._restore_scroll(self.pages_scroll if self.state.selected_author else self.authors_scroll)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.filter_panel.retire()
        self._bookmark_open_epoch += 1
        self._refresh_epoch += 1
        self.active = False
        self.state.close()
        self.bookmark_state.close()
        if self.bookmarks is not None:
            self.bookmarks.close()
        self._close_viewer_controller()
        self._image_provider.close()
