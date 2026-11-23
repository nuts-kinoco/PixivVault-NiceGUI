"""Synthetic My Bookmarks card grid; network and persistence belong to the caller."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import inspect
import json
from uuid import uuid4

from .manual_toolbar import manual_toolbar

from nicegui import ui

from ..bookmark_state import BookmarkState, BookmarkCard
from .grid_layout import MAX_COLUMNS
from .card_grid import CardGrid, MoreCell
from .action_controls import refresh_button


def add_library_card_titles(grid, *, caption, action):
    """Attach full text and action names to the library layouts of the shared grid."""
    script = f"""({{el}}) => {{
        for (const card of el.querySelectorAll('.pv-card')) {{
            const title = card.querySelector({json.dumps(caption)});
            const opener = card.matches('[role=button]') ? card : card.querySelector('[role=button]');
            if (!title || !opener) continue;
            title.title = title.textContent;
            opener.classList.add('pv-focus-ring');
            opener.setAttribute('aria-label', {json.dumps(action)} + '：' + title.textContent + '…');
            const author = card.querySelector('.pv-card-author');
            if (author) author.title = author.textContent;
        }}
        for (const button of el.querySelectorAll('.pv-library-profile, [data-testid=library-external-open], [data-testid=library-similar-open], [data-testid=library-more], [data-testid=bookmark-more]'))
            button.classList.add('pv-hit28');
    }}"""
    for hook in (':onVnodeMounted', ':onVnodeUpdated'):
        grid.props[hook] = script


class BookmarkView:
    def __init__(self, state: BookmarkState, *, on_refresh: Callable[[], None] | None = None,
                 on_open: Callable[[str], None] | None = None,
                 on_enqueue: Callable | None = None,
                 on_similar: Callable[[str], object] | None = None,
                 on_author: Callable | None = None,
                 image_url: Callable[[str], str] | None = None,
                 can_open: Callable[[str], bool] | None = None,
                 cache_persistence_unconfirmed: Callable[[], bool] | None = None,
                 with_filter_toggle: bool = False, standalone: bool = False) -> None:
        self.state = state
        self.on_refresh = on_refresh
        self.on_open = on_open
        self.on_enqueue = on_enqueue
        self.on_similar = on_similar
        self.on_author = on_author
        self.image_url = image_url
        self.can_open = can_open
        self.cache_persistence_unconfirmed = cache_persistence_unconfirmed
        self.with_filter_toggle = with_filter_toggle
        self.standalone = standalone
        self.filter_toggle = None
        self.root = None
        self.grid = None
        self.cards: dict[str, dict] = {}
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
                        self.filter_toggle = ui.button("絞り込み ›").classes('pv-hit28').props(
                            "flat dense no-caps data-testid=bookmark-filter-toggle")
                    self.heading = ui.label("マイブックマーク").classes("pv-library-heading").props(
                        'role=heading aria-level=2 tabindex=-1 data-pv-focus-heading data-testid=bookmark-heading')
                    if self.standalone:
                        ui.label('Pixivでブックマークした作品').classes('pv-subtitle')
                    ui.space()
                    self.counter = ui.label().classes("pv-counter").props("data-testid=bookmark-count")
                    self.selected_count = ui.label().classes("pv-counter").props(
                        "data-testid=bookmark-selection")
                    self.download_button = ui.button("選択をダウンロード", color=None,
                        on_click=self.download_selected).classes('pv-hit28').props(
                        "flat dense data-testid=bookmark-download")
                    self.download_button.disable()
                    if self.on_enqueue is None:
                        self.download_button.tooltip("この起動では作品の保存先が未接続です")
                    self.retry = refresh_button(self.request_refresh, label="更新", testid="bookmark-refresh")
                    self.freshness = ui.label().classes("pv-counter").props("data-testid=bookmark-freshness")
                self.search_input = ui.input("ブックマーク内を検索", value=self.state.query,
                    on_change=lambda event: self.search(event.value or "")).props(
                    "outlined dense clearable debounce=200 data-testid=bookmark-search")
                if self.standalone:
                    self.toolbar, _, _ = manual_toolbar(self.root, 'bookmarks', (self.search_input, self.filter_toggle))
                self.message = ui.label().classes("pv-empty").props("data-testid=bookmark-status role=status")
                self.download_status = ui.label("").classes("pv-subtitle").props(
                    "role=status data-testid=bookmark-download-status")
                self.persistence_status = ui.label("").classes("pv-subtitle").props(
                    "role=status data-testid=bookmark-cache-status")
                with ui.element("div").classes("pv-gallery-scroll").props("data-testid=bookmark-scroll") as self.scroll:
                    self.grid = CardGrid(self._on_grid_event).classes("pv-grid").style(
                        f"--pv-max-cols:{MAX_COLUMNS[self.card_size]}")
                    self.grid.props['layout'] = 'bookmark'
                    add_library_card_titles(self.grid, caption='.pv-card-title', action='ブックマークを開く')
                    self.more_cell = self.more_button = MoreCell(self.grid)
                preview = ui.label().classes('pv-bookmark-title-preview').props('popover=manual aria-hidden=true')
                preview_node = f"document.getElementById('{preview.html_id}')"
                self.scroll.on('focusin', js_handler=f"""event => {{
                    const title = event.target.closest('[data-testid=bookmark-card]')?.querySelector('.pv-card-title');
                    const preview = {preview_node};
                    if (preview && event.target.matches(':focus-visible') &&
                        title && title.scrollWidth > title.clientWidth) {{
                        preview.textContent = title.textContent;
                        preview.showPopover();
                    }}
                }}""")
                self.scroll.on('focusout', js_handler=f"() => {preview_node}?.hidePopover()")
                self.scroll.on('keydown.escape', js_handler=f"""event => {{
                    const preview = {preview_node};
                    if (preview?.matches(':popover-open')) {{
                        preview.hidePopover();
                        event.stopPropagation();
                    }}
                }}""")
                self.scroll.on("scroll", js_handler="""event => {
                    if (event.target.getClientRects().length)
                        event.target.__pvBookmarkScroll = event.target.scrollTop;
                }""")
        self.refresh()
        return self.root

    def _make_card(self, item: BookmarkCard) -> dict:
        art = item.artwork
        menu = []
        if self.on_author is not None:
            menu.append(dict(key='author', label='作者を開く…', enabled=True,
                             testid='bookmark-author-open'))
        if self.on_similar is not None:
            menu.append(dict(key='similar', label='似た絵を探す', enabled=True,
                             testid='bookmark-similar-open'))
        return dict(work_id=art.work_id, title=art.title, author=art.author, author_id=art.author_id,
                    src=self.image_url(art.work_id) if self.image_url is not None else None,
                    disabled=self.can_open is not None and not self.can_open(art.work_id),
                    downloaded=item.downloaded, storage_label='保存済み' if item.downloaded else '未保存',
                    checked=art.work_id in self.state.selected_ids,
                    count=f'★ {art.bookmark_count:,}' if art.bookmark_count is not None else None,
                    menu_id='bookmark-similar-context', menu=menu)

    async def _on_grid_event(self, event):
        await self._dispatch_grid_event(event.args)

    async def _dispatch_grid_event(self, args):
        if (self.state.closed or not self.state.active or type(args) is not dict
                or type(args.get('epoch')) is not int or args['epoch'] != self.grid.render_epoch):
            return
        work_id, action = args.get('work_id'), args.get('action')
        session = self._session
        if action == 'more' and work_id is None:
            if self.more_cell.visible and self.more_button.enabled:
                self.more(session)
        elif type(work_id) is str and work_id in self.cards:
            if action == 'open':
                self.open(work_id, session)
            elif action == 'select' and type(args.get('value')) is bool:
                self.select(work_id, args['value'], session)
            elif action == 'author':
                await self.open_author(work_id, session)
            elif action == 'similar':
                await self.search_similar(work_id, session)

    def refresh(self, *, reset_controls: bool = False) -> None:
        if self.root is None or self.state.closed:
            return
        visible = self.state.visible_items
        self.cards = {item.work_id: self._make_card(item) for item in visible}
        retired = self.grid.publish(list(self.cards.values()),
                                    action_fields=('src', 'disabled', 'author_id', 'downloaded', 'menu'))
        active_changed = self.grid.set_active(self.state.active)
        if retired or active_changed:
            self._session = object()
        if reset_controls or (active_changed and self.state.active):
            self.grid.retry_failed_images()
        self.more_cell.set_visibility(self.state.has_more)
        status = self.state.status
        persistence_unconfirmed = (self.cache_persistence_unconfirmed is not None
                                   and self.cache_persistence_unconfirmed())
        if self.cache_persistence_unconfirmed is not None and self.state.has_remote_cache:
            self.persistence_status.set_text(
                'キャッシュの保存を確認できません。' if persistence_unconfirmed else '')
        messages = {
            "loading": "ブックマークを読み込み中です",
            "fetch_failed": "ブックマークの取得に失敗しました。再読込できます",
            "empty": "ブックマークはありません",
            "unloaded": "ブックマークを読み込んでいません",
            "no_match": "条件に合うブックマークがありません",
        }
        if status == "fetch_failed":
            if visible:
                if persistence_unconfirmed:
                    messages["fetch_failed"] = "取得に失敗しました。現在の一覧を表示しています。再読込できます"
                else:
                    messages["fetch_failed"] = "取得に失敗しました。保存済みのブックマークを表示しています。再読込できます"
            elif self.state.items and not self.state.matched_items:
                messages["fetch_failed"] = "取得に失敗しました。条件に合うブックマークがありません。再読込できます"
        self.message.set_text(messages.get(status, ""))
        self.message.set_visibility(status != "ready")
        self.retry.set_visibility(status != "loading")
        matched, total = len(self.state.matched_items), len(self.state.items)
        count = (f"{matched} 件中 {len(visible)} 件を表示 / 全 {total} 件"
                 if self.state.has_more else f"{matched} 件 / 全 {total} 件")
        self.counter.set_text(count if self.state.has_remote_cache else "ブックマークは未読込です")
        try:
            freshness = datetime.fromisoformat(self.state.loaded_at).strftime('%Y-%m-%d %H:%M')
        except (TypeError, ValueError):
            freshness = None
        self.freshness.set_text(f"最終更新: {freshness}" if freshness else "")
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

    async def open_author(self,work_id,session,menu=None):
        if (self.state.closed or not self.state.active or self.state.loading
                or self.on_author is None or session is not self._session or work_id not in self.cards):
            return False
        item=next((item for item in self.state.visible_items if item.work_id==work_id),None)
        if item is None:return False
        if menu is not None:menu.close()
        result=self.on_author(item.artwork)
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
