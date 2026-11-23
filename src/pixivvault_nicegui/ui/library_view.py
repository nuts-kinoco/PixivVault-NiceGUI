"""原版の作者一覧・作者全ページ・タブ内ビューアを保つ合成ライブラリ。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from uuid import uuid4
from .manual_toolbar import manual_toolbar

from nicegui import ui

from .action_controls import notify_action
from .management_dialog import focus_region

from ..bookmark_state import BookmarkState
from ..library_filters import LibraryFilters
from ..library_models import AuthorSummary, LibrarySnapshot, LibraryWork, PageRef
from ..library_state import LibraryState
from ..services import ApplicationServices
from ..state import GalleryState
from ..viewer_metadata import pixiv_url
from .bookmark_view import BookmarkView, add_library_card_titles
from .card_grid import CardGrid, MoreCell
from .card_size_control import size_buttons, update_size_buttons
from .grid_layout import MAX_COLUMNS
from .viewer_controller import ViewerController


# Material image outline, matching the original Flet IMAGE_OUTLINED fallback.
_IMAGE_OUTLINE = 'M21 19V5c0-1.1-.9-2-2-2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2zM5 5h14v14H5V5zm8.5 6.5-3 3.51-2-2.5L6 17h12l-4.5-5.5z'


class _SearchImages:
    """Client-owned remote pin, while local IDs keep their local provider."""
    def __init__(self, local, remote, remote_ids):
        self.local, self.remote, self.remote_ids = local, remote, frozenset(remote_ids)
        self.token = local.token

    def __getattr__(self, name):
        local_method = getattr(self.local, name)
        remote_method = getattr(self.remote, name)
        if not callable(local_method):
            return local_method
        def call(work_id, *args, **kwargs):
            method = remote_method if work_id in self.remote_ids else local_method
            return method(work_id, *args, **kwargs)
        return call

    def close(self):
        self.local.close()
        self.remote.close()


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
                 on_size_change=None, on_premium_search=None, navigation_view=None) -> None:
        self.services = services
        self.client = ui.context.client
        self._image_provider = services.acquire_library_images()
        self.active = True
        self.closed = False
        self.navigation_view = navigation_view
        self.subview = navigation_view or "downloaded"
        self.search_scope = 'library'
        self.pixiv_query = None
        self._requested_pixiv_query = None
        self.pixiv_error = False
        self.pixiv_loading = False
        self._pixiv_epoch = 0
        self._reload_after_viewer = False
        self._viewer_focus_page = None
        self._remote_retired = False
        self._pixiv_records = ()
        self._pixiv_works = ()
        self._pixiv_ai_dirty = False
        self.card_size = card_size
        self._on_size_change = on_size_change
        self.filters = LibraryFilters()
        self.bookmark_state = None
        if navigation_view != "downloaded":
            self._bookmark_snapshot_token = services.bookmark_snapshot_token()
            self.bookmark_state = BookmarkState(
                local=services.bookmark_local_snapshot(),
                cached_remote=services.bookmark_remote_snapshot(),
                known_artworks=(work.artwork for work in services.library_snapshot().works),
            )
            self.bookmark_state.fetch_failed = services.bookmark_cache_failed
            self.bookmark_state.loaded_at = services.bookmark_cache_fetched_at
        self.bookmarks = None
        self.state = LibraryState(LibrarySnapshot(()))
        self.author_cards = {}
        self.page_cards = {}
        self._rendered_source = None
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
        self._capacity_busy = False
        self._legacy_capacity_busy = False
        self._legacy_lease = None
        self._capacity_epoch = 0
        self._legacy_restore_needed = True
        self._legacy_restore_request = None
        self._legacy_cached_ids = set()
        self._legacy_auto_attempted = set()
        self._legacy_auto_task = None
        self._search_capacity_delay = None
        self._managed_capacities = {}
        self._bookmark_refreshing = False
        self._bookmark_projection_pending = False
        self._bookmark_open_epoch = 0
        self._resolved_bookmark_artwork = {}
        self._zip_export_busy = False
        self._zip_export_epoch = uuid4().hex
        self.similarity = None

        with ui.column().classes("pv-library w-full").props(
            f"data-testid={'bookmark-host' if navigation_view == 'bookmarks' else 'library-view'} data-active=true"
        ) as self.root:
            with ui.element("div").classes("pv-library-toolbar") as toolbar:
                self.heading = ui.label("ライブラリ").classes("pv-library-heading").props(
                    'role=heading aria-level=2 tabindex=-1 data-pv-focus-heading data-testid=library-heading')
                if navigation_view == 'downloaded':
                    ui.label('このPCに保存した作品').classes('pv-subtitle')

                if navigation_view is None:
                    with ui.row().classes("pv-library-subviews"):
                        self.downloaded_button = ui.button(
                            "DL済み", color=None, on_click=lambda: self.show_subview("downloaded")
                        ).props("flat dense no-caps data-testid=library-subview-downloaded"
                                ).classes("pv-library-subview-selected pv-hit28")
                        self.bookmarks_button = ui.button(
                            "マイブックマーク", color=None, on_click=lambda: self.show_subview("bookmarks")
                        ).classes('pv-hit28').props("flat dense no-caps data-testid=library-subview-bookmarks")
                self.search_input = ui.input(
                    placeholder="タイトル・作者・タグを検索",
                    on_change=lambda event: self.search(event.value),
                ).props("outlined dense clearable debounce=200 data-testid=library-search").classes(
                    "pv-library-search")
                self.search_input.props['aria-label'] = 'タイトル・作者・タグを検索'
                self.search_input.on("keydown.enter", lambda _: self.search(self.search_input.value))
                self.pixiv_submit = ui.button('pixiv本体を検索', color=None, on_click=self.retry_pixiv_search).classes(
                    'pv-hit28').props('flat dense no-caps data-testid=library-search-pixiv')
                self.pixiv_submit.set_visibility(False)
                self.scope_library = ui.button('ライブラリのみ', color=None, on_click=lambda: self.set_search_scope('library')).classes('pv-hit28').props(
                    'flat dense no-caps data-testid=library-scope-local')
                self.scope_pixiv = ui.button('pixiv本体も含む', color=None, on_click=lambda: self.set_search_scope('pixiv')).classes('pv-hit28').props(
                    'flat dense no-caps data-testid=library-scope-pixiv')
                self.scope_pending = ui.label('準備中').classes('pv-subtitle').props('data-testid=library-scope-pending')
                if on_premium_search is not None:
                    ui.button('人気順で探す…',on_click=lambda:on_premium_search(str(self.search_input.value or ''))).classes('pv-hit28').props(
                        'flat dense data-testid=library-premium-search')
                self.db_refresh_button = None
                if services.library_refresh_available:
                    self.db_refresh_button = ui.button(
                        "保存先更新" if services.managed_downloads is not None else "DB更新",
                        color=None, on_click=self.refresh_from_disk).classes('pv-hit28').props(
                        "flat dense no-caps data-testid=library-db-refresh")
                    self.db_refresh_button.tooltip('専用保存先と接続済みの旧ライブラリを再読込します。' if services.managed_downloads is not None else '接続済みの旧ライブラリを読み直します。')
                self.sort_buttons = {}
                for key in ("view_count", "last_backup", "capacity"):
                    self.sort_buttons[key] = ui.button(
                        "", color=None, on_click=lambda _, value=key: self.sort(value),
                    ).classes('pv-hit28').props(f"flat dense no-caps data-testid=library-sort-{key}")
                with ui.row().classes("pv-library-sizes"):
                    self.size_buttons = size_buttons(self.change_size, prefix="library")
                    for size_button in self.size_buttons.values():
                        size_button.classes('pv-hit28')

            self.filter_now_host = ui.row().classes("pv-library-filter-now-host w-full")
            self.filter_now_host.set_visibility(False)

            with ui.row().classes("pv-library-error") as self.error_row:
                ui.label("ライブラリを読み込めませんでした").props("role=alert")
                ui.button("再読込", on_click=(self.refresh_from_disk
                    if services.library_refresh_available else self.reload)).props(
                    "flat data-testid=library-reload")

            self.recovery_notice=ui.label('').props('role=status data-testid=library-recovery-status')
            with ui.row().classes('pv-library-error') as self.pixiv_error_row:
                ui.label('pixiv本体の検索に失敗しました').props('role=alert')
                self.pixiv_retry = ui.button('再検索', on_click=self.retry_pixiv_search).props(
                    'flat dense data-testid=library-pixiv-retry')
            self.pixiv_error_row.set_visibility(False)
            with ui.column().classes("pv-library-panel w-full") as self.authors_panel:
                with ui.row().classes("pv-library-summary w-full"):
                    self.filter_toggle = ui.button("絞り込み ›").classes('pv-hit28').props(
                        "flat dense no-caps data-testid=library-filter-toggle")
                    self.capacity_button = ui.button("管理保存容量を再計測", on_click=self.measure_capacity).classes('pv-hit28').props("flat dense data-testid=library-capacity-measure")
                    self.capacity_status = ui.label("").props("role=status data-testid=library-capacity-status")
                    self.legacy_capacity_button = ui.button('作者保存先の容量を再計測',on_click=self.measure_legacy_capacity).classes('pv-hit28').props('flat dense data-testid=library-legacy-capacity-measure')
                    self.legacy_capacity_status = ui.label('').props('role=status data-testid=library-legacy-capacity-status')
                    self.legacy_auto_capacity_status = ui.label('').props('role=status data-testid=library-legacy-capacity-auto-status')
                    ui.space()
                    self.author_counter = ui.label().classes("pv-counter").props(
                        "data-testid=library-author-count")
                self.empty = ui.label().classes("pv-empty").props("data-testid=library-empty")
                with ui.element("div").classes("pv-gallery-scroll").props(
                    "data-testid=library-authors-scroll"
                ) as self.authors_scroll:
                    self.author_grid = CardGrid(self._on_author_grid_event).classes("pv-grid")
                    self.author_grid.props.update(layout='library-author', library_placeholder=_IMAGE_OUTLINE)
                    add_library_card_titles(self.author_grid, caption='.pv-library-card-title', action='作者を開く')
                    self.author_more_button = ui.button("もっと見る", on_click=self.more_authors).props(
                        "flat no-caps data-testid=library-author-more")
                self._remember_scroll(self.authors_scroll)

            with ui.column().classes("pv-library-panel pv-library-detail w-full").props(
                "data-testid=library-detail"
            ) as self.pages_panel:
                with ui.row().classes("pv-library-detail-header w-full"):
                    self.detail_back = ui.button("← 作者一覧", color=None, on_click=self.close_author).classes('pv-hit28').props(
                        "flat dense no-caps data-testid=library-back")
                    self.detail_title = ui.label().classes("pv-library-detail-title").props(
                        'role=heading aria-level=3 tabindex=-1 data-pv-focus-heading data-testid=library-detail-heading')
                    self.zip_export_button = ui.button('旧形式ZIPを書き出す', color=None).classes('pv-hit28').props(
                        'flat dense no-caps data-testid=library-legacy-zip-export')
                    self.zip_export_button.on('click', self._zip_export_event,
                        js_handler=f"() => {{const d=document.getElementById('{self.root.html_id}').dataset;emit({{epoch:d.zipExportEpoch}})}}")
                    ui.button(icon="close", color=None, on_click=self.close_author).classes('pv-hit28').props(
                        "flat dense round aria-label=作者一覧へ戻る data-testid=library-detail-close")
                self.zip_export_status = ui.label().classes('pv-subtitle').props(
                    'role=status data-testid=library-legacy-zip-status')
                self.unknown_note = ui.label().classes("pv-library-unknown").props(
                    "data-testid=library-unknown-pages")
                self.page_counter = ui.label().classes("pv-counter").props(
                    "data-testid=library-page-count")
                with ui.element("div").classes("pv-gallery-scroll").props(
                    "data-testid=library-pages-scroll"
                ) as self.pages_scroll:
                    self.page_grid = CardGrid(self._on_page_grid_event).classes("pv-grid")
                    self.page_grid.props.update(layout='library-page', library_placeholder=_IMAGE_OUTLINE)
                    add_library_card_titles(self.page_grid, caption='.pv-library-card-title', action='作品を開く')
                    self.more_cell = self.more_button = MoreCell(self.page_grid)
                self._remember_scroll(self.pages_scroll)

            if navigation_view == 'downloaded':
                controls = (self.search_input, self.scope_library, self.scope_pixiv, self.scope_pending, self.pixiv_submit, self.filter_toggle,
                            *self.sort_buttons.values(), *self.size_buttons.values())
                controls_toolbar, _, _ = manual_toolbar(self.root, 'library', controls, index=1)
            if navigation_view == 'downloaded':
                focus_region(self.authors_panel, self.detail_title)
            toolbar.set_visibility(navigation_view != 'bookmarks')
            if self.bookmark_state is not None:
                self.bookmarks = BookmarkView(
                    self.bookmark_state,
                    on_refresh=self.refresh_bookmarks,
                    on_open=self.open_bookmark,
                    on_author=self._open_author_art,
                    on_enqueue=services.save_explicit_work if services.managed_downloads is not None else None,
                    on_similar=(self.search_bookmark_similar
                                if getattr(services,'similarity_source',None) is not None else None),
                    image_url=lambda work_id: self._image_provider.bookmark_url(work_id),
                    can_open=lambda work_id: self._image_provider.can_open(work_id),
                    cache_persistence_unconfirmed=lambda: not services.bookmark_persistence_confirmed(
                        self._bookmark_snapshot_token),
                    with_filter_toggle=True,
                    standalone=navigation_view == "bookmarks",
                )
                self.bookmarks.render()
                if services.bookmark_cache_persist_failed:
                    self.bookmarks.persistence_status.set_text('キャッシュの保存先を確認できません。')
                self.bookmarks.root.set_visibility(False)
                self.bookmarks.set_active(False)

            from .library_filter_panel import LibraryFilterPanel
            with ui.row().classes("pv-library-body w-full no-wrap") as self.body:
                self.filter_panel = LibraryFilterPanel(
                    get_filters=lambda: self.filters, on_change=self.apply_filters,
                    can_edit=self._filters_editable, body=self.body, now_parent=self.filter_now_host,
                    get_authors=self._filter_authors)
                self.filter_content = ui.column().classes("pv-library-filter-content")
            for panel in (self.authors_panel, self.pages_panel):
                panel.move(self.filter_content)
            self.filter_panel.register_toggle(self.filter_toggle)
            if self.bookmarks is not None:
                self.bookmarks.root.move(self.filter_content)
                self.filter_panel.register_toggle(self.bookmarks.filter_toggle)
            if navigation_view is not None:
                details = self.bookmarks.toolbar if navigation_view == 'bookmarks' else controls_toolbar
                details.on('toggle', self.filter_panel.close_from_toolbar,
                           js_handler='event => emit(event.target.open)')

        self._init_viewer(services, GalleryState(()), self.root,
                          test_id='bookmark-viewer' if navigation_view == 'bookmarks' else 'library-viewer',
                          image_test_id='bookmark-viewer-image' if navigation_view == 'bookmarks' else 'library-viewer-image')
        if getattr(services,'similarity_source',None) is not None:
            from .similar_view import SimilarView
            self.similarity=SimilarView(
                services.similarity_source,self._open_similar,
                image_url=self._similar_image_url,
                analyze=(services.analyze_saved_for_similarity
                         if services.similarity_analysis_enabled else None),
                analysis_status=services.similarity_analysis_status if services.similarity_analysis_enabled else None,
                start_analysis=services.start_similarity_analysis if services.similarity_analysis_enabled else None,
                pause_analysis=services.pause_similarity_analysis if services.similarity_analysis_enabled else None,
                resume_analysis=services.resume_similarity_analysis if services.similarity_analysis_enabled else None,
                reset_analysis=services.reset_similarity_analysis if services.similarity_analysis_enabled else None,
                rescan_analysis=services.rescan_similarity_analysis if services.similarity_analysis_enabled else None)
        self.client.on_delete(self.close)
        self.apply_card_size(card_size)
        if navigation_view == 'bookmarks':
            self.active = False
            self._sync_panels()
        else:
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
        self._sync_card_grids_active()
        if self.similarity is not None:self.similarity.set_active(self.active and not self.load_error)
        self._zip_export_epoch = uuid4().hex
        self.root.props['data-zip-export-epoch'] = self._zip_export_epoch
        self.zip_export_status.set_text('')
        selected = self.state.selected_author_id is not None
        self.root.props["data-library-generation"] = str(self.state.generation)
        downloaded = self.subview == "downloaded"
        self.capacity_button.set_enabled(downloaded and not selected and self.active and not self.closed and not self.load_error and not self._capacity_busy and self.services.managed_downloads is not None)
        self.legacy_capacity_button.set_enabled(
            downloaded and not selected and self.active and not self.closed and not self.load_error
            and not self._legacy_capacity_busy and getattr(self.services, 'legacy_capacity', None) is not None)
        self.authors_panel.set_visibility(downloaded and not selected and not self.load_error)
        self.pages_panel.set_visibility(downloaded and selected and not self.load_error)
        enabled = (bool(getattr(self.services, 'legacy_zip_export_available', False))
                   and downloaded and selected and self.active and not self.closed
                   and not self._zip_export_busy and not self.load_error)
        self.zip_export_button.set_visibility(
            bool(getattr(self.services, 'legacy_zip_export_available', False)))
        self.zip_export_button.set_enabled(enabled)
        self.zip_export_status.set_visibility(
            bool(getattr(self.services, 'legacy_zip_export_available', False)))
        self.error_row.set_visibility(downloaded and (self.load_error or self.refresh_error))
        self.filter_panel.set_subview(self.subview)
        self.search_input.set_visibility(downloaded)
        from ..premium_read import borrowed_ready
        try:
            ready = borrowed_ready(self.services.external_vault)
        except Exception:
            ready = False
        self.scope_library.set_visibility(downloaded)
        self.scope_pixiv.set_visibility(downloaded)
        self.scope_pixiv.set_enabled(ready)
        self.pixiv_submit.set_visibility(bool(self.navigation_view == "downloaded" and downloaded and self.search_scope == "pixiv"))
        self.pixiv_submit.set_enabled(ready and not self.pixiv_loading)
        self.scope_pending.set_visibility(downloaded and not ready)
        self.scope_library.props(f"aria-pressed={'true' if self.search_scope == 'library' else 'false'}")
        self.scope_pixiv.props(f"aria-pressed={'true' if self.search_scope == 'pixiv' else 'false'}")
        self.scope_library.classes(add='pv-library-subview-selected' if self.search_scope == 'library' else '',
                                   remove='' if self.search_scope == 'library' else 'pv-library-subview-selected')
        self.scope_pixiv.classes(add='pv-library-subview-selected' if self.search_scope == 'pixiv' else '',
                                 remove='' if self.search_scope == 'pixiv' else 'pv-library-subview-selected')
        self.scope_library.classes(add='pv-library-scope-selected' if self.search_scope == 'library' else '',
                                   remove='' if self.search_scope == 'library' else 'pv-library-scope-selected')
        self.scope_pixiv.classes(add='pv-library-scope-selected' if self.search_scope == 'pixiv' else '',
                                 remove='' if self.search_scope == 'pixiv' else 'pv-library-scope-selected')
        self.pixiv_error_row.set_visibility(downloaded and self.pixiv_error)
        if self.navigation_view is None:
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
            label = {"view_count":"閲覧数", "last_backup":"保存日", "capacity":"容量"}[key]
            direction = "↓" if self.state.sort_directions[key] else "↑"
            button.set_text(label + direction)
            button.props(f"aria-pressed={'true' if key == self.state.sort_key else 'false'}")

    async def _zip_export_event(self, event) -> None:
        args = getattr(event, 'args', None)
        if isinstance(args, dict) and args.get('epoch') == self._zip_export_epoch:
            await self.export_legacy_zip(self._zip_export_epoch)

    async def export_legacy_zip(self, epoch=None) -> bool:
        """Explicit author-local export, blocking disk work stays off UI loop."""
        author_id = self.state.selected_author_id
        if (self.closed or not self.active or self.load_error
                or self.subview != 'downloaded' or not author_id
                or self._zip_export_busy
                or not getattr(self.services, 'legacy_zip_export_available', False)
                or (epoch is not None and epoch != self._zip_export_epoch)):
            return False
        captured_epoch = self._zip_export_epoch
        self._zip_export_busy = True
        self.zip_export_button.set_enabled(False)
        self.zip_export_status.set_text('保存済みの全ページを検証してZIPを書き出しています')
        try:
            # A disconnected viewer cannot cancel an already accepted file
            # publication. The application owns the output until completion.
            await self.services.export_managed_author_zip(author_id)
        except Exception as error:
            if (not self.closed and self.active and
                    captured_epoch == self._zip_export_epoch):
                # Only allow-listed fixed exception text: no path, title or
                # Pixiv response can enter the browser's status message.
                if (type(error).__name__ == 'LegacyZipExportError' and
                        str(error) in ('legacy author ZIP already exported',
                                       'legacy ZIP already exists; refusing overwrite')):
                    message = 'この作者のZIPは出力済みです。既存ZIPへの上書きは行いません'
                else:
                    message = ('ZIPを書き出せませんでした。既存のZIPは変更していません。'
                               '保存先・サイズ上限・空き容量を確認してください')
                self.zip_export_status.set_text(message)
            return False
        else:
            if (not self.closed and self.active and
                    captured_epoch == self._zip_export_epoch):
                self.zip_export_status.set_text(
                    '専用のZIP出力先に旧形式の作者ZIPを作成しました。元の管理画像は保持しています')
            return True
        finally:
            self._zip_export_busy = False
            if not self.closed:
                self.zip_export_button.set_enabled(
                    bool(getattr(self.services, 'legacy_zip_export_available', False))
                    and self.active and self.subview == 'downloaded'
                    and self.state.selected_author_id is not None)

    def _invalidate_capacity(self, *, clear_values=False):
        self._capacity_epoch += 1
        self._legacy_restore_needed = True
        if self._search_capacity_delay is not None:
            self._search_capacity_delay.cancel()
            self._search_capacity_delay = None
        self.capacity_status.set_text('')
        self.legacy_capacity_status.set_text('')
        self.legacy_auto_capacity_status.set_text('')
        if self._legacy_lease is not None:self._legacy_lease.cancel()
        if clear_values:self._managed_capacities.clear()

    def _apply_legacy_capacities(self, state, values):
        capacities = dict(state.snapshot.author_capacities)
        changed = {aid: value for aid, value in values.items()
                   if aid not in capacities or capacities[aid] != value}
        if not changed:
            return
        capacities.update(changed)
        state.snapshot = LibrarySnapshot(state.snapshot.works, capacities, state.snapshot.author_names)
        state._all_authors = state.snapshot.authors
        state._by_author = {author.author_id: author for author in state._all_authors}
        state._refresh_matches()
        state._invalidate()
        self.render_authors()
        self._sync_panels()

    def _legacy_current(self,state,generation,epoch):
        return (not self.closed and not getattr(self.services,'closing',False)
                and self.active and self.subview=='downloaded'
                and self.state is state and state.selected_author_id is None
                and state.generation==generation and self._capacity_epoch==epoch)

    def _schedule_legacy_capacity_restore(self):
        """Restore cached capacities before scheduling missing visible authors.

        Repeated activation shares the pending read. The separate sequential
        measurement worker retains the same lifetime and cancellation guards.
        """
        state=self.state
        if self._search_capacity_delay is not None or getattr(self.services,'closing',False):return
        if not self._legacy_restore_needed:
            self._schedule_unmeasured_capacity()
            return
        if (self.closed or not self.active
                or self.subview!='downloaded' or state.selected_author_id is not None
                or self.load_error or self._capacity_busy or self._legacy_capacity_busy
                or getattr(self.services,'legacy_capacity',None) is None):return
        # Restore the whole known author cache before sorting/filtering. The
        # first-50 limit belongs only to explicit disk measurement below.
        targets=tuple(author.author_id for author in state.snapshot.authors)
        request=(state,state.generation,self._capacity_epoch,targets)
        if request==self._legacy_restore_request:return
        from nicegui import background_tasks
        self._legacy_restore_request=request
        background_tasks.create(self._restore_legacy_capacities(*request))

    async def _restore_legacy_capacities(self,state,generation,epoch,targets):
        request=(state,generation,epoch,targets)
        service=getattr(self.services,'legacy_capacity',None)
        try:
            if (service is None or not self._legacy_current(state,generation,epoch)
                    or self.load_error or self._capacity_busy or self._legacy_capacity_busy):return
            values=await asyncio.to_thread(lambda:{aid:value for aid in targets
                for revision,value in (service.cached(aid),) if value is not None})
            if (self._legacy_current(state,generation,epoch) and not self.load_error
                    and not self._capacity_busy and not self._legacy_capacity_busy):
                self._apply_legacy_capacities(state,values)
                # The readonly legacy snapshot may already contain genuine
                # cache rows. Preserve them too; per-work size hints are not cache.
                self._legacy_cached_ids={aid for aid,value in state.snapshot.author_capacities.items()
                                         if value is not None}
                self._legacy_restore_needed=False
        except Exception:
            if self._legacy_current(state,generation,epoch):
                self.legacy_capacity_status.set_text('保存済みの容量を読み込めません。')
        finally:
            if self._legacy_restore_request==request:self._legacy_restore_request=None
            if not self._legacy_restore_needed:self._schedule_unmeasured_capacity()

    def _schedule_unmeasured_capacity(self):
        """One sequential worker for uncached visible authors, once per visit."""
        state=self.state
        service=getattr(self.services,'legacy_capacity',None)
        if (service is None or self.closed or getattr(self.services,'closing',False)
                or not self.active or self.subview!='downloaded'
                or self.load_error or state.selected_author_id is not None
                or self._search_capacity_delay is not None
                or self._legacy_restore_needed or self._legacy_auto_task is not None
                or self._legacy_capacity_busy or self._capacity_busy):return
        targets=tuple(a.author_id for a in state.visible_authors
                      if a.author_id not in self._legacy_cached_ids
                      and a.author_id not in self._legacy_auto_attempted)
        if not targets:return
        from nicegui import background_tasks
        self._legacy_capacity_busy=True
        self.legacy_capacity_button.disable()
        self._legacy_auto_task=background_tasks.create(self._measure_unmeasured_capacity(
            service,state,state.generation,self._capacity_epoch,targets,self._legacy_auto_attempted))

    async def _measure_unmeasured_capacity(self,service,state,generation,epoch,targets,attempted):
        from ..legacy_capacity import MeasurementLease
        completed=0;failed=0
        def current():
            return (self._legacy_current(state,generation,epoch) and not self.load_error
                    and getattr(self.services,'legacy_capacity',None) is service)

        def show_progress():
            if current():
                self.legacy_auto_capacity_status.set_text('未計測の作者保存先容量を確認しています…')

        progress = asyncio.get_running_loop().call_later(0.09, show_progress)
        try:
            for aid in targets:
                if not current():return
                lease=MeasurementLease();self._legacy_lease=lease
                try:
                    revision,cached=await asyncio.to_thread(service.cached,aid)
                    if not current():return
                    if cached is None:
                        values=await asyncio.to_thread(service.measure,(aid,))
                        if not current():return
                        # A failed author stays unmeasured. Never turn None into zero.
                        if values[aid] is None:
                            attempted.add(aid);failed+=1
                            continue
                        committed=await asyncio.to_thread(lease.publish,service.cache,values,{aid:revision})
                        if not current():return
                        if not committed:
                            # Another accepted measurement may have won the CAS.
                            _,cached=await asyncio.to_thread(service.cached,aid)
                            if not current():return
                            committed={} if cached is None else {aid:cached}
                    else:
                        committed={aid:cached}
                    attempted.add(aid)
                    if committed:
                        self._apply_legacy_capacities(state,committed)
                        # Our own redraw advances state generation; user actions still
                        # invalidate epoch/state and cannot publish late results.
                        generation=state.generation
                        self._legacy_cached_ids.update(committed)
                        completed+=1
                    else:failed+=1
                except Exception:
                    if not current():return
                    attempted.add(aid);failed+=1
                finally:
                    if self._legacy_lease is lease:self._legacy_lease=None
            if current():
                self.legacy_auto_capacity_status.set_text(
                    f'作者保存先 {completed}作者の容量を確認。未計測 {failed}作者。次回表示時に再試行します。'
                    if failed else '')
        finally:
            progress.cancel()
            self._legacy_auto_task=None
            self._legacy_capacity_busy=False
            if not self.closed:
                self._sync_panels()
                # An invalidation or appended page may have arrived while the
                # cancelled worker was draining. Never overlap disk measurements.
                self._schedule_legacy_capacity_restore()

    async def measure_legacy_capacity(self):
        service=getattr(self.services,'legacy_capacity',None)
        if (service is None or self.closed or not self.active or self.subview!='downloaded'
                or self.state.selected_author_id is not None or self.load_error or self._legacy_capacity_busy):return
        targets=tuple(a.author_id for a in self.state.visible_authors[:50])
        if not targets:
            self.legacy_capacity_status.set_text('表示中の作者がいません。');return
        from ..legacy_capacity import MeasurementLease
        state=self.state;generation=state.generation;epoch=self._capacity_epoch
        lease=MeasurementLease();self._legacy_lease=lease;self._legacy_capacity_busy=True
        self.legacy_capacity_button.disable();self.legacy_capacity_status.set_text('作者保存先の容量を再計測しています…')
        try:
            revisions=await asyncio.to_thread(lambda:{aid:service.cached(aid)[0] for aid in targets})
            values=await asyncio.to_thread(service.measure,targets)
            if not self._legacy_current(state,generation,epoch):return
            committed=await asyncio.to_thread(lease.publish,service.cache,values,revisions)
            if not self._legacy_current(state,generation,epoch):return
            self._apply_legacy_capacities(state,committed)
            self.legacy_capacity_status.set_text(f'作者保存先 {len(committed)}作者を再計測。未計測 {len(targets)-len(committed)}作者。')
        except Exception:
            if self._legacy_current(state,generation,epoch):self.legacy_capacity_status.set_text('容量を確認できません。以前の値を保持します。')
        finally:
            if self._legacy_lease is lease:self._legacy_lease=None
            self._legacy_capacity_busy=False
            if not self.closed:
                self._sync_panels()
                self._schedule_legacy_capacity_restore()

    async def measure_capacity(self):
        owner=self.services.managed_downloads
        if (owner is None or self.closed or not self.active or self.subview != 'downloaded'
                or self.state.selected_author_id is not None or self.load_error or self._capacity_busy):
            return
        targets=tuple(author.author_id for author in self.state.visible_authors[:50])
        if not targets:
            self.capacity_status.set_text('表示中の作者がいません。');return
        state=self.state;generation=state.generation;epoch=self._capacity_epoch
        self._capacity_busy=True;self.capacity_button.disable()
        self.capacity_status.set_text('管理保存作品を再計測しています…')
        try:
            from ..managed_capacity import measure_managed_capacities
            values,failed,active=await asyncio.to_thread(measure_managed_capacities,owner,targets)
            current=await asyncio.to_thread(owner._generations.entries)
            if (self.closed or not self.active or self.subview!='downloaded' or self.state is not state
                    or state.generation!=generation or epoch!=self._capacity_epoch or current!=active):
                if not self.closed:self.capacity_status.set_text('')
                return
            self._managed_capacities.update(values)
            self.render_authors()
            self._sync_panels()
            self.capacity_status.set_text(f'管理保存 {len(values)}作者を再計測。未計測 {len(targets)-len(values)}作者。')
        except Exception:
            if (not self.closed and self.active and self.subview=='downloaded' and self.state is state
                    and state.generation==generation and epoch==self._capacity_epoch):
                self.capacity_status.set_text('管理保存の容量を確認できません。以前の表示を保持します。')
            elif not self.closed:self.capacity_status.set_text('')
        finally:
            self._capacity_busy=False
            if not self.closed:
                self._sync_panels()
                self._schedule_legacy_capacity_restore()

    def _filter_authors(self):
        authors=dict(self.state.snapshot.author_names)
        for author in self.state.snapshot.authors:authors.setdefault(author.author_id,author.name)
        for card in self.bookmark_state.items if self.bookmark_state is not None else ():
            if card.artwork.author_id:
                authors.setdefault(card.artwork.author_id,card.artwork.author)
        return authors

    def _filters_editable(self) -> bool:
        viewer_state = getattr(self, "_viewer_state", None)
        return (not self.closed and self.active and
                not (viewer_state is not None and viewer_state.viewer_open))

    def apply_filters(self, filters: LibraryFilters) -> bool:
        if not isinstance(filters, LibraryFilters):
            raise TypeError("filters must be LibraryFilters")
        if not self._filters_editable() or filters == self.filters:
            return False
        self._invalidate_capacity()
        if self.similarity is not None:self.similarity.invalidate()
        old_filters = self.filters
        self.filters = filters
        self._bookmark_open_epoch += 1
        self.state.set_filters(filters)
        if self.search_scope == 'pixiv' and old_filters.exclude_ai != filters.exclude_ai:
            self._pixiv_ai_dirty = True
            if self.subview == 'downloaded' and (
                    self.navigation_view is None or self.state.query.strip() == self._requested_pixiv_query):
                self._schedule_pixiv_search()
        if self.bookmark_state is not None:
            self.bookmark_state.set_filters(filters)
        self.close_viewer(restore_focus=False)
        self._clear_pages()
        self.render_authors()
        if self.bookmarks is not None:
            self.bookmarks.refresh(reset_controls=True)
        self._sync_panels()
        self._restore_scroll(self.authors_scroll, reset=True)
        self._schedule_legacy_capacity_restore()
        if self.bookmarks is not None and self.client.has_socket_connection:
            self.client.run_javascript(
                f"const node=document.getElementById('{self.bookmarks.scroll.html_id}');"
                "if(node){node.__pvBookmarkScroll=0;node.scrollTop=0;}")
        return True

    def show_subview(self, subview: str) -> None:
        if self.navigation_view is not None or self.closed or not self.active or subview not in {"downloaded", "bookmarks"}:
            return
        if subview == self.subview:
            return
        if self.subview == 'downloaded':
            self._pixiv_epoch += 1
        if self.similarity is not None:self.similarity.invalidate()
        self.close_viewer(restore_focus=False)
        self._bookmark_open_epoch += 1
        self._invalidate_capacity()
        self.subview = subview
        if subview == 'downloaded':self._legacy_auto_attempted=set()
        self._sync_panels()
        self._schedule_legacy_capacity_restore()
        if subview == "bookmarks":
            self._maybe_auto_refresh_bookmarks()
        elif self.search_scope == 'pixiv' and self.state.query and (self.pixiv_query is None or self._pixiv_ai_dirty):
            self._schedule_pixiv_search()

    @staticmethod
    def _bookmark_refresh_due(value, now=None):
        from datetime import datetime
        now = now or datetime.now()
        try:
            age = (now - datetime.fromisoformat(value)).total_seconds()
        except (TypeError, ValueError):
            return True
        return age >= 120

    def _maybe_auto_refresh_bookmarks(self):
        self._adopt_service_bookmarks()
        if (self.services.bookmark_auto_refresh_enabled()
                and (not self.bookmark_state.has_remote_cache
                     or self._bookmark_refresh_due(self.bookmark_state.loaded_at))):
            self.refresh_bookmarks(automatic=True)

    def _adopt_service_bookmarks(self):
        remote = self.services.bookmark_remote_snapshot()
        fetched_at = self.services.bookmark_cache_fetched_at
        snapshot_token = self.services.bookmark_snapshot_token()
        if remote is not None and (self.bookmark_state.loaded_at != fetched_at
                                   or self._bookmark_snapshot_token is not snapshot_token
                                   or not self.bookmark_state.has_remote_cache):
            self._bookmark_snapshot_token = snapshot_token
            self.bookmark_state.adopt_cache(remote, self.services.bookmark_local_snapshot(), fetched_at)
            self._refresh_bookmark_projection()
            if self.bookmarks is not None:
                self.bookmarks.refresh()

    def _refresh_bookmark_projection(self):
        if self._viewer_state.viewer_open:
            self._bookmark_projection_pending = True
            return
        self.reload(preserve=True, after_viewer=True)
        if self.bookmarks is not None:
            self.bookmarks.refresh()

    def close_viewer(self, *, restore_focus=True, apply_deferred=True):
        was_open = self._viewer_state.viewer_open
        returns_to_redraw = (was_open and restore_focus and self.active and not self.closed
                             and (self._bookmark_projection_pending
                                  or (apply_deferred and (self._remote_retired or self._reload_after_viewer))))
        page_key, self._viewer_focus_page = self._viewer_focus_page, None
        author = self.state.selected_author_id
        artwork = self._viewer_state.selected_artwork
        bookmark_id = artwork.work_id if artwork is not None and self.subview == 'bookmarks' else None
        super().close_viewer(restore_focus=restore_focus and not returns_to_redraw)
        if self._bookmark_projection_pending and not self.closed and self.active:
            self._bookmark_projection_pending = False
            self._refresh_bookmark_projection()
        if apply_deferred and was_open and not self.closed and self.active:
            if self._remote_retired:
                self.set_search_scope('library')
            elif self._reload_after_viewer:
                self.reload(preserve=True)
        if returns_to_redraw:
            self._focus_rebuilt_opener(page_key, author, bookmark_id)

    def _focus_rebuilt_opener(self, page_key, author, bookmark_id):
        if self.closed or not self.active:
            return
        if self.subview == 'bookmarks':
            target = self.bookmarks.heading
            grid = self.bookmarks.grid
            self.viewer.focus_after_close(target, opener=(
                f"[...(document.getElementById('{grid.html_id}')"
                "?.querySelectorAll('[data-testid=bookmark-card]') || [])]"
                f".find(card => card.dataset.workId === {json.dumps(bookmark_id)})"
                "?.querySelector('[data-testid=bookmark-open][tabindex=\"0\"]')"
            ))
            return
        elif self.state.selected_author_id is not None:
            card = self.page_cards.get(page_key)
            if card is not None and not card['disabled']:
                self.viewer.focus_after_close(self.page_grid, opener=self._page_opener(page_key))
                return
            target = self.detail_title
        else:
            if author in self.author_cards:
                self.viewer.focus_after_close(self.author_grid, opener=self._author_opener(author))
                return
            target = self.heading
        self.viewer.focus_after_close(target)

    def refresh_bookmarks(self, *, automatic=False) -> None:
        """Keep demo refresh immediate; real DB/network work is off the UI loop."""
        if self.closed or not self.active or self.subview != "bookmarks" or self._bookmark_refreshing:
            return
        epoch = self.bookmark_state.begin_fetch(silent=automatic and self.bookmark_state.has_remote_cache)
        if epoch is None:
            return
        if self.similarity is not None:self.similarity.invalidate()
        if self.services.library_repository is not None or self.services.live_client is not None:
            from nicegui import background_tasks
            self._bookmark_refreshing = True
            self.bookmarks.refresh()
            background_tasks.create(self._refresh_bookmarks_async(epoch, automatic=automatic))
            return
        try:
            remote = self.services.bookmark_remote_snapshot()
        except Exception:
            self.bookmark_state.complete_fetch(epoch, (), ok=False)
        else:
            if self.bookmark_state.complete_fetch(epoch, remote, ok=True):
                self._bookmark_snapshot_token = self.services.bookmark_snapshot_token()
        if self.bookmarks is not None:
            self.bookmarks.refresh()

    async def _refresh_bookmarks_async(self, epoch: int, *, automatic=False) -> None:
        try:
            if automatic:
                restored = await self.services.restore_bookmark_cache()
                if restored and not self.closed and self.active and self.subview == 'bookmarks':
                    self._bookmark_snapshot_token = self.services.bookmark_snapshot_token()
                    self.bookmark_state.adopt_cache(self.services.bookmark_remote_snapshot(),
                        self.services.bookmark_local_snapshot(), self.services.bookmark_cache_fetched_at)
                    self.bookmark_state.silent_fetch = True
                    with self.client:
                        self._refresh_bookmark_projection()
                remote, local = await self.services.refresh_bookmark_cache(automatic=True)
            else:
                remote, local = await self.services.refresh_bookmark_cache()
        except Exception:
            accepted = self.bookmark_state.complete_fetch(epoch, (), ok=False)
        else:
            if remote is None:
                accepted = self.bookmark_state.cancel_fetch(epoch)
                if accepted and not self.closed and self.bookmarks is not None:
                    with self.client:
                        self.bookmarks.refresh()
                return
            accepted = self.bookmark_state.complete_fetch(epoch, remote, ok=True)
            if accepted:
                self._bookmark_snapshot_token = self.services.bookmark_snapshot_token()
                self.bookmark_state.loaded_at = self.services.bookmark_cache_fetched_at
                self.bookmark_state.replace_local(local)
                if automatic:
                    self._refresh_bookmark_projection()
                else:
                    self.close_viewer(restore_focus=False)
                    self._bookmark_open_epoch += 1
                    self._resolved_bookmark_artwork.clear()
                    self.reload(preserve=True)
        finally:
            self._bookmark_refreshing = False
        if not accepted and not self.closed and self.active and self.subview == 'bookmarks':
            with self.client:
                self._adopt_service_bookmarks()
        if accepted and not self.closed and self.bookmarks is not None:
            with self.client:
                self.bookmarks.refresh()

    def _build_viewer_state(self) -> None:
        # Local data wins for downloaded works; remote-only entries must also
        # exist in the viewer instead of being silently ignored on click.
        artwork = {work.work_id: work.artwork for work in self.state.snapshot.works}
        for card in self.bookmark_state.items if self.bookmark_state is not None else ():
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
                or epoch != self._bookmark_open_epoch or self._image_provider is not provider
                or not any(card.work_id == work_id for card in self.bookmark_state.items)):
            return
        self._resolved_bookmark_artwork[work_id] = artwork
        with self.client:
            self._build_viewer_state()
            super().open(work_id, 0)

    def _reload_source(self, snapshot, local_token):
        return (snapshot, local_token, self.search_scope, self._pixiv_records, self._excluded_search_ids()
                if self.search_scope == 'pixiv' else ())

    def reload(self, *, preserve: bool = False, after_viewer: bool = False) -> None:
        if self.closed or not self.active:
            return
        if preserve and self._rendered_source is not None and not self.load_error:
            try:
                current = self.services.acquire_library_images()
                try:
                    source = self._reload_source(self.services.library_snapshot(), current.token)
                finally:
                    current.close()
            except Exception:
                source = None  # Let the normal reload path report the read failure.
            if source == self._rendered_source:
                self._reload_after_viewer = False
                self.refresh_error = False
                self.error_row.set_visibility(False)
                if self.search_scope == 'pixiv' and self.state.selected_author_id is None:
                    self.render_authors()
                return
        if after_viewer and self._viewer_state.viewer_open:
            self._reload_after_viewer = True
            return
        self._reload_after_viewer = False
        if self.similarity is not None:self.similarity.invalidate()
        self.filter_panel.invalidate()
        self._invalidate_capacity(clear_values=True)
        previous_state = self.state
        saved_query = previous_state.query
        saved_sort = previous_state.sort_key
        saved_directions = dict(previous_state.sort_directions)
        saved_author = previous_state.selected_author_id
        saved_author_limit = previous_state.shown_author_count
        saved_page_limit = previous_state.shown_page_count
        if self._viewer_state.viewer_open or self.viewer.is_open:
            self.close_viewer(restore_focus=False, apply_deferred=False)
        self.state.close()
        self._viewer_state.close()
        self._clear_pages()
        self._bookmark_open_epoch += 1
        self._resolved_bookmark_artwork.clear()
        self.state = LibraryState(LibrarySnapshot(()))
        self._viewer_state = GalleryState(())
        try:
            base_snapshot = snapshot = self.services.library_snapshot()
            if self.search_scope == 'pixiv' and self._pixiv_works:
                existing = {work.work_id for work in snapshot.works} | self._excluded_search_ids()
                snapshot = LibrarySnapshot(snapshot.works + tuple(
                    work for work in self._pixiv_works if work.work_id not in existing),
                    author_capacities=snapshot.author_capacities, author_names=snapshot.author_names)
            if self.bookmark_state is not None:
                self.bookmark_state.replace_known(work.artwork for work in snapshot.works)
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
            if self.author_grid.publish([]):
                self._author_session = object()
            self.author_cards.clear()
            self.load_error = True
            self._sync_panels()
            return
        legacy=getattr(self.services,'legacy_quarantine',None)
        self.recovery_notice.set_text(legacy.recovery_notice() if legacy is not None else '')
        self.state = state
        self._viewer_state = viewer_state
        previous = self._image_provider
        local = self.services.acquire_library_images()
        if self.search_scope == 'pixiv' and self._pixiv_records:
            local_ids = {w.work_id for w in self.services.library_snapshot().works} | self._excluded_search_ids()
            remote_ids = {r.work_id for r in self._pixiv_records} - local_ids
            try:
                remote = self.services.acquire_recommendation_images(self._pixiv_records)
            except Exception:
                local.close()
                raise
            self._image_provider = _SearchImages(local, remote, remote_ids)
        else:
            self._image_provider = local
        self._rendered_source = self._reload_source(base_snapshot, local.token)
        previous.close()
        if self.bookmarks is not None:
            self.bookmarks.refresh(reset_controls=not after_viewer)
        self._build_viewer_state()
        self.load_error = False
        self.refresh_error = False
        self.search_input.set_value(state.query)
        self.render_authors()
        if state.selected_author is not None:
            self.render_pages()
        self._sync_panels()
        self._schedule_legacy_capacity_restore()
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
                if self.bookmark_state is not None:
                    self.bookmark_state.replace_local(self.services.bookmark_local_snapshot())
                    self.bookmarks.refresh()
                self.reload(preserve=True, after_viewer=True)
        finally:
            if epoch == self._refresh_epoch:
                self._refreshing = False
                if self.db_refresh_button is not None and not self.closed:
                    self.db_refresh_button.enable()

    def _make_author_card(self, author: AuthorSummary) -> dict:
        author_id = author.author_id
        representative = author.representative.work_id
        menu = [
            dict(key='author', label='作者ページを開く', enabled=self._viewer_reader() is not None,
                 testid='library-author-open-remote'),
            dict(key='saved', label='保存先を開く', icon='folder_open',
                 enabled=getattr(self.services.desktop, 'launcher', None) is not None,
                 testid='library-author-open-saved'),
        ]
        if self.similarity is not None:
            menu.insert(1, dict(key='similar', label='似た絵を探す', enabled=True,
                                testid='library-author-similar-open'))
        return dict(work_id=author_id, representative_id=representative, title=author.name,
                    src=self._image_provider.url(representative, 0, 'thumb'),
                    profile=pixiv_url(author_id, author=True),
                    work_count=author.work_count, view_count=author.view_count,
                    capacity=format_capacity(author.capacity_bytes),
                    managed=('管理保存 ' + format_capacity(self._managed_capacities[author_id])
                             if author_id in self._managed_capacities else None),
                    menu_id='library-author-similar-context', menu=menu)

    async def _on_author_grid_event(self, event):
        await self._author_grid_event(event.args)

    async def _author_grid_event(self, args):
        if (self.closed or not self.active or self.subview != 'downloaded' or self.load_error
                or self.state.selected_author_id is not None or type(args) is not dict
                or type(args.get('epoch')) is not int or args['epoch'] != self.author_grid.render_epoch):
            return
        author_id, action = args.get('work_id'), args.get('action')
        card = self.author_cards.get(author_id) if type(author_id) is str else None
        if card is None:
            return
        session = self._author_session
        if action == 'open':
            self.open_author(author_id, session)
        elif any(item['key'] == action and item['enabled'] for item in card['menu']):
            if action == 'author':
                await self.open_remote_author(author_id, session)
            elif action == 'similar':
                await self.search_similar_author(author_id, session)
            elif action == 'saved':
                author = next((item for item in self.state.visible_authors
                               if item.author_id == author_id), None)
                if author is not None:
                    await self.open_author_saved(author, session, None)

    def _sync_card_grids_active(self):
        active = self.active and not self.closed and self.subview == 'downloaded' and not self.load_error
        has_author = self.state.selected_author_id is not None
        for grid, enabled in ((self.author_grid, active and not has_author),
                              (self.page_grid, active and has_author)):
            if grid.set_active(enabled) and enabled and grid.props['cards']:
                grid.retry_failed_images()

    def _author_opener(self, author_id):
        return (f"[...(document.getElementById('{self.author_grid.html_id}')"
                f"?.querySelectorAll('[data-author-id]') || [])]"
                f".find(card => card.dataset.authorId === {json.dumps(author_id)})"
                "?.querySelector('[data-testid=library-author-open]')")

    async def open_author_saved(self,author,session,menu):
        provider=self._image_provider
        def current():
            return (self.active and not self.closed and self.subview=='downloaded'
                    and self._author_session is session and self._image_provider is provider
                    and self.author_cards.get(author.author_id, {}).get('representative_id')
                    == author.representative.work_id)
        if not current():return
        if menu is not None:menu.close()
        try:
            await self.services.open_external_saved(author.representative.work_id,
                catalog_token=provider.token,author_id=author.author_id,is_current=current)
        except Exception:
            if current():notify_action('保存場所を開けませんでした。',type='negative')

    async def open_remote_author(self,author_id,session,menu=None):
        if not self.active or self.closed or session is not self._author_session:return
        author=next((a for a in self.state.visible_authors if a.author_id==author_id),None)
        if author is None:return
        if menu is not None:menu.close()
        await self._open_author_art(author.representative.artwork)

    def render_authors(self) -> None:
        visible = self.state.visible_authors
        self.author_cards = {author.author_id: self._make_author_card(author) for author in visible}
        if self.author_grid.publish(list(self.author_cards.values()),
                                    action_fields=('representative_id', 'src', 'profile', 'menu'),
                                    context=(self.state.query, self.state.filters, self.search_scope)):
            self._author_session = object()
        self.author_counter.set_text(
            f"{self.state.shown_author_count} / {self.state.matched_author_count} 作者表示"
            f"（全{self.state.total_author_count}作者）")
        if self.search_scope == 'pixiv' and self.pixiv_loading:
            self.empty.set_text('pixiv本体を検索中…')
        elif self.search_scope == 'pixiv' and self.pixiv_error:
            self.empty.set_text('pixiv本体の検索に失敗しました。再検索してください。')
        else:
            self.empty.set_text("保存済み作品がありません" if not self.state.total_author_count
                                else "条件に合う作者がいません")
        self.empty.set_visibility(not visible)
        self.author_more_button.set_visibility(self.state.has_more_authors)

    def more_authors(self) -> None:
        if (self.subview == "downloaded" and self.active and not self.closed
                and not self.load_error and self.state.load_more_authors()):
            self.render_authors()
            self._schedule_legacy_capacity_restore()

    def _clear_pages(self) -> None:
        self.page_cards.clear()
        if self.page_grid.publish([]):
            self._page_session = self._more_session = object()
        self.more_cell.set_visibility(False)

    def _make_page_card(self, page: PageRef) -> dict:
        work = page.work
        cover = getattr(self._image_provider, 'supports_novel_cover', None)
        variant = ('cover' if work.work_type == 'novel' and callable(cover)
                   and cover(work.work_id) else 'thumb')
        saved = work.last_backup[:10] if work.last_backup else '保存日 不明'
        count = work.artwork.bookmark_count
        return dict(work_id=f'{work.work_id}/{page.page_no}', artwork_id=work.work_id,
                    page_no=page.page_no, title=work.artwork.title,
                    src=self._image_provider.url(work.work_id, page.page_no, variant),
                    disabled=not self._image_provider.can_open(work.work_id),
                    info=f'p.{page.page_no + 1} ／ {saved}',
                    downloaded=work.work_id not in getattr(self._image_provider, 'remote_ids', ()),
                    count=f'★ {count:,}' if count is not None else None,
                    external=(getattr(self.services, 'desktop', None) is not None
                              and self._image_provider.can_open_external(work.work_id)),
                    similar=self.similarity is not None)

    async def _on_page_grid_event(self, event):
        await self._page_grid_event(event.args)

    async def _page_grid_event(self, args):
        if (self.closed or not self.active or self.subview != 'downloaded' or self.load_error
                or self.state.selected_author_id is None or type(args) is not dict
                or type(args.get('epoch')) is not int or args['epoch'] != self.page_grid.render_epoch):
            return
        key, action = args.get('work_id'), args.get('action')
        if action == 'more' and key is None:
            if self.more_cell.visible and self.more_button.enabled:
                self.more(self._more_session)
            return
        if type(key) is not str:
            return
        card = next((item for item in self.page_cards.values() if item['work_id'] == key), None)
        if card is None:
            return
        position = (card['artwork_id'], card['page_no'], self.state.generation, self._page_session)
        if action == 'open':
            self.open_page(*position)
        elif action == 'external' and card['external']:
            await self.open_external(*position)
        elif action == 'similar' and card['similar']:
            await self.search_similar(*position)

    def _page_opener(self, page_key):
        work_id, page_no = page_key
        return (f"[...(document.getElementById('{self.page_grid.html_id}')"
                f"?.querySelectorAll('[data-work-id][data-page-no]') || [])]"
                f".find(card => card.dataset.workId === {json.dumps(work_id)}"
                f" && card.dataset.pageNo === {json.dumps(str(page_no))})")

    async def open_external(self,work_id,page_no,generation,session):
        if (self.closed or not self.active or self.subview!='downloaded' or self.load_error
                or generation!=self.state.generation or session is not self._page_session
                or (work_id,page_no) not in self.page_cards or getattr(self,'_external_busy',False)
                or getattr(self.services,'desktop',None) is None
                or not self._image_provider.can_open_external(work_id)):return
        token=self._image_provider.token
        self._external_busy=True
        try:
            await self.services.open_external_saved(work_id,page_no+1,catalog_token=token)
        except Exception:
            if not self.closed and self.active and generation==self.state.generation and session is self._page_session:
                notify_action('保存場所を外部ビューアで開けませんでした。',type='negative')
        finally:self._external_busy=False

    def _similar_work(self,work_id,page):
        work=next((w for w in self.state.snapshot.works if w.work_id==work_id),None)
        if (work is None or type(page) is not int or not 1<=page<=work.page_count
                or not self._image_provider.can_open(work_id)):
            return None
        return work

    def _similar_image_url(self,work_id,page):
        return self._image_provider.url(work_id,page-1,'thumb') if self._similar_work(work_id,page) else None

    async def search_similar_author(self,author_id,session,menu=None):
        state=self.state;provider=self._image_provider;epoch=self._bookmark_open_epoch
        def current():
            return (not self.closed and self.active and not self.services.closing
                    and self.similarity is not None and not self.load_error
                    and self.subview=='downloaded' and self.state is state
                    and self.state.selected_author_id is None
                    and self._author_session is session and author_id in self.author_cards
                    and self._image_provider is provider and self._bookmark_open_epoch==epoch)
        if not current():return
        author=next((item for item in state.visible_authors if item.author_id==author_id),None)
        if author is None:return
        if menu is not None:menu.close()
        representative=author.representative
        await self.similarity.show(representative.work_id,representative.artwork.title,1,
            query_candidates=tuple((work.work_id,work.artwork.title) for work in author.works),
            is_current=current)

    async def search_bookmark_similar(self,work_id):
        state=self.bookmark_state;session=self.bookmarks._session
        provider=self._image_provider;epoch=self._bookmark_open_epoch;fetch_epoch=state.epoch
        def current():
            return (not self.closed and self.active and not self.services.closing
                    and self.similarity is not None and not self.load_error
                    and self.subview=='bookmarks' and self.bookmark_state is state
                    and state.active and not state.closed and not state.loading
                    and state.epoch==fetch_epoch and self.bookmarks._session is session
                    and work_id in self.bookmarks.cards
                    and self._image_provider is provider and self._bookmark_open_epoch==epoch)
        if not current():return
        item=next((item for item in state.visible_items if item.work_id==work_id),None)
        if item is None:return
        # An unsaved query is allowed to report its missing embedding. It must
        # never enter the remote bookmark-detail/image resolution path.
        await self.similarity.show(work_id,item.artwork.title,1,is_current=current)

    async def search_similar(self,work_id,page_no,generation,session):
        if (self.similarity is None or self.closed or not self.active or self.subview!='downloaded'
                or self.load_error or generation!=self.state.generation or session is not self._page_session
                or (work_id,page_no) not in self.page_cards):return
        work=self._similar_work(work_id,page_no+1)
        if work is None:return
        await self.similarity.show(work_id,work.artwork.title,page_no+1)

    def open_saved_work(self,work_id,page=1) -> bool:
        """Open an already available saved page; never resolves remote metadata."""
        if self.closed or not self.active or self.load_error:return False
        if self._similar_work(work_id,page) is None:return False
        self.show_subview('downloaded')
        self._build_viewer_state()
        super().open(work_id,page-1)
        return True

    def _open_similar(self,work_id,page):
        if self.closed or not self.active:return
        if not self.open_saved_work(work_id,page) and self.similarity is not None:
            self.similarity.notice.set_text('この保存画像を参照できません。ライブラリを更新してください。')
            self.similarity.dialog.open()

    def render_pages(self) -> None:
        author = self.state.selected_author
        if author is None:
            return
        cards = {}
        for page in self.state.visible_pages:
            previous = self.page_cards.get(page.key)
            data = self._make_page_card(page)
            cards[page.key] = previous if previous == data else data
        self.page_cards = cards
        if self.page_grid.publish(list(self.page_cards.values()),
                                   action_fields=('src', 'disabled', 'external', 'similar')):
            self._page_session = self._more_session = object()
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
            if self.search_scope == 'pixiv':
                if self.navigation_view is None:
                    self._schedule_pixiv_search()
                else:
                    self._pixiv_epoch += 1
                    self._pixiv_records = self._pixiv_works = ()
                    self.pixiv_error = self.pixiv_loading = False
                    self.pixiv_query = None
                    self.reload(preserve=True)
            self._invalidate_capacity()
            # Keep normal retirement after any viewer use, including a pending
            # request or focus callback. A never-used viewer needs no reset.
            if self._request_serial or self._viewer_state.viewer_open or self._bookmark_projection_pending:
                self.close_viewer(restore_focus=False)
            self._clear_pages()
            self.render_authors()
            self._sync_panels()
            self._restore_scroll(self.authors_scroll, reset=True)
            if getattr(self.services, 'legacy_capacity', None) is not None:
                # Coalesce searches without ignoring a concurrent cache write.
                self._search_capacity_delay = asyncio.get_running_loop().call_later(
                    0.8, self._resume_search_capacity)

    def _resume_search_capacity(self) -> None:
        self._search_capacity_delay = None
        self._schedule_legacy_capacity_restore()

    def set_search_scope(self, scope):
        from ..premium_read import borrowed_ready
        if self.closed or not self.active or scope not in ('library', 'pixiv'):
            return
        if scope == 'pixiv' and not borrowed_ready(self.services.external_vault):
            return
        if scope == self.search_scope:
            return
        if self.state.selected_author_id is not None:
            self.state.close_author()
        self.search_scope = scope
        self._remote_retired = False
        self._pixiv_epoch += 1
        self._pixiv_works = ()
        self._pixiv_records = ()
        self.pixiv_query = None
        self.pixiv_error = False
        self.pixiv_loading = False
        self.pixiv_error_row.set_visibility(False)
        self.reload(preserve=True)
        if scope == 'pixiv' and self.navigation_view is None:
            self._schedule_pixiv_search()

    def _excluded_search_ids(self):
        catalog = self.services.images.library
        pages = getattr(catalog, 'source_pages', {}) if catalog is not None else {}
        return {work_id for work_id, visible in pages.items() if not visible}

    def refresh_search_scope(self):
        from ..premium_read import borrowed_ready
        if self.closed:
            return
        try:
            ready = borrowed_ready(self.services.external_vault)
        except Exception:
            ready = False
        if self.scope_pixiv.enabled != ready:
            self.scope_pixiv.set_enabled(ready)
        self.scope_pending.set_visibility(self.subview == 'downloaded' and not ready)
        # A shell poll may change availability while the user is panning.
        # Retire the remote pin at once, keep a local image on screen, and
        # apply the scope change when the viewer closes.
        if not ready and self.search_scope == 'pixiv':
            self._retire_remote_images()
        if self._remote_retired and self.search_scope == 'pixiv' and not self._viewer_state.viewer_open:
            self.set_search_scope('library')

    def _retire_remote_images(self):
        if self._remote_retired:
            return
        self._remote_retired = True
        self._pixiv_epoch += 1
        self._pixiv_works = self._pixiv_records = ()
        provider = self._image_provider
        if not isinstance(provider, _SearchImages):
            return
        selected = self._viewer_state.selected_artwork if self._viewer_state.viewer_open else None
        provider.remote.close()
        if selected is not None and selected.work_id in provider.remote_ids:
            self.close_viewer(restore_focus=False)

    def retry_pixiv_search(self):
        if not self.closed and self.active and self.subview == 'downloaded' and self.search_scope == 'pixiv':
            self._schedule_pixiv_search()

    def _schedule_pixiv_search(self):
        if (self.closed or not self.active or self.subview != 'downloaded'
                or self.search_scope != 'pixiv' or self._remote_retired):
            return
        from nicegui import background_tasks
        self._pixiv_epoch += 1
        self._pixiv_ai_dirty = False
        epoch = self._pixiv_epoch
        self.pixiv_error = False
        self.pixiv_error_row.set_visibility(False)
        self.pixiv_query = None
        self._pixiv_works = ()
        self._pixiv_records = ()
        query = self.state.query.strip()
        self._requested_pixiv_query = query
        self.pixiv_loading = bool(query)
        self.reload(preserve=True)
        if not query:
            return
        filters = self.filters
        background_tasks.create(self._search_pixiv_async(epoch, query, filters))

    async def _search_pixiv_async(self, epoch, query, filters):
        from ..premium_read import borrowed_ready
        from ..personal_ranking import normalize_item
        from ..bookmark_remote_adapter import optional_bookmark_count
        await asyncio.sleep(0)
        if self.closed or not self.active or self.subview != 'downloaded' or self.search_scope != 'pixiv' or epoch != self._pixiv_epoch:
            return
        def read():
            if self.closed or not self.active or self.subview != 'downloaded' or self.search_scope != 'pixiv' or epoch != self._pixiv_epoch:
                raise RuntimeError('stale search')
            vault = self.services.external_vault
            if not borrowed_ready(vault):
                raise RuntimeError('borrow unavailable')
            if self.services.managed_network_stop is not None:
                self.services.managed_network_stop.require_allowed()
            return vault.search(query, filters.bookmark_min, r18=filters.include_r18, exclude_ai=filters.exclude_ai)
        try:
            packet = await asyncio.to_thread(read)
            works, records = {}, {}
            for raw in packet['illusts']:
                row = normalize_item(raw)
                item = row['item']
                record = replace(item.record, created_at=row['create_date'],
                    bookmark_count=optional_bookmark_count(raw.get('total_bookmarks')))
                artwork = replace(record.as_artwork(), ai_type=item.ai_type)
                records.setdefault(record.work_id, record)
                works.setdefault(record.work_id, LibraryWork(artwork, record.author_id,
                    page_count_known=record.page_count_known,
                    work_type='ugoira' if record.illustration_type == 2 else 'illustration'))
        except Exception:
            if not self.closed and self.active and self.subview == 'downloaded' and self.search_scope == 'pixiv' and epoch == self._pixiv_epoch:
                self.pixiv_loading = False
                self.pixiv_error = True
                with self.client:
                    self.pixiv_error_row.set_visibility(True)
                    self.render_authors()
            return
        if self.closed or not self.active or self.subview != 'downloaded' or self.search_scope != 'pixiv' or epoch != self._pixiv_epoch:
            return
        try:
            still_ready = borrowed_ready(self.services.external_vault)
        except Exception:
            still_ready = False
        self.pixiv_loading = False
        if not still_ready:
            self.pixiv_error = True
            with self.client:
                self.pixiv_error_row.set_visibility(True)
                self.refresh_search_scope()
            return
        self.pixiv_query = query
        self._pixiv_works, self._pixiv_records = tuple(works.values()), tuple(records.values())
        with self.client:
            self.reload(preserve=True, after_viewer=True)

    def sort(self, key: str) -> None:
        if self.closed or not self.active or self.load_error or self.subview != "downloaded":
            return
        if self.state.select_sort(key):
            self._invalidate_capacity()
            self.close_viewer(restore_focus=False)
            self._clear_pages()
            self.render_authors()
            self._sync_panels()
            self._restore_scroll(self.authors_scroll, reset=True)
            self._schedule_legacy_capacity_restore()

    def open_author(self, author_id: str, session: object | None = None) -> None:
        if self.closed or not self.active or self.load_error or self.subview != "downloaded":
            return
        if session is not None and session is not self._author_session:
            return
        if self.state.open_author(author_id):
            self._invalidate_capacity()
            self.close_viewer(restore_focus=False)
            self._clear_pages()
            self.render_pages()
            self._sync_panels()
            self._restore_scroll(self.pages_scroll, reset=True)
            if self.navigation_view == 'downloaded' and self.client.has_socket_connection:
                self.client.run_javascript(f'''requestAnimationFrame(() => {{
                    const heading = document.getElementById('{self.detail_title.html_id}');
                    const authors = document.getElementById('{self.authors_panel.html_id}');
                    window.pvFocus.restore({{origin: heading, excluded: authors, retiring:true,
                        replaceFallback: authors}});
                }})''')

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
        self._schedule_legacy_capacity_restore()
        if author_id in self.author_cards and self.client.has_socket_connection:
            self.client.run_javascript(f"""(() => {{
                const atViewport = window.pvFocus.viewportVersion();
                requestAnimationFrame(() => {{
                    const root = document.getElementById('{self.root.html_id}');
                    const node = {self._author_opener(author_id)};
                    if (root?.dataset.active === 'true' &&
                        root.dataset.libraryGeneration === '{self.state.generation}' && node)
                        window.pvFocus.restore({{origin: node, near: root, atViewport,
                            excluded: document.getElementById('{self.pages_panel.html_id}')}});
                }});
            }})()""")

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
        self._viewer_focus_page = (work_id, page_no)
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
            self._pixiv_epoch += 1
            self._invalidate_capacity()
            self.filter_panel.invalidate()
            self._bookmark_open_epoch += 1
            self._refresh_epoch += 1
            self._refreshing = False
            if self.db_refresh_button is not None:
                self.db_refresh_button.enable()
        if bool(active) and not self.active:self._legacy_auto_attempted=set()
        self.active = bool(active)
        self._sync_card_grids_active()
        if active:
            self.refresh_search_scope()
            if self._reload_after_viewer:
                self.reload(preserve=True, after_viewer=True)
        self._zip_export_epoch = uuid4().hex
        self.root.props['data-zip-export-epoch'] = self._zip_export_epoch
        self.zip_export_status.set_text('')
        self.zip_export_button.set_enabled(
            self.active and not self._zip_export_busy
            and self.state.selected_author_id is not None
            and bool(getattr(self.services, 'legacy_zip_export_available', False)))
        self.root.props["data-active"] = str(self.active).lower()
        if self._viewer_active != self.active:
            self.set_viewer_active(active)
        if self.similarity is not None:self.similarity.set_active(self.active and not self.load_error)
        if self.bookmarks is not None:
            self.bookmarks.set_active(self.active and self.subview == "bookmarks")
        if active:
            if self._bookmark_projection_pending and not self._viewer_state.viewer_open:
                self._bookmark_projection_pending = False
                self._refresh_bookmark_projection()
            if self.subview == 'bookmarks':
                if self.navigation_view == 'bookmarks':
                    self.bookmark_state.replace_local(self.services.bookmark_local_snapshot())
                    self.bookmark_state.replace_known(work.artwork for work in self.services.library_snapshot().works)
                    self._refresh_bookmark_projection()
                self._maybe_auto_refresh_bookmarks()
            elif (self.search_scope == 'pixiv' and self.state.query
                  and (self.navigation_view is None or self.state.query.strip() == self._requested_pixiv_query)
                  and (self.pixiv_query is None or self._pixiv_ai_dirty)):
                self._schedule_pixiv_search()
            self._restore_scroll(self.pages_scroll if self.state.selected_author else self.authors_scroll)
            self._schedule_legacy_capacity_restore()

    def close(self) -> None:
        if self.closed:
            return
        self._invalidate_capacity()
        self.closed = True
        self._pixiv_epoch += 1
        self.filter_panel.retire()
        if self.similarity is not None:self.similarity.close()
        self._bookmark_open_epoch += 1
        self._refresh_epoch += 1
        self.active = False
        self.state.close()
        if self.bookmarks is not None:
            self.bookmark_state.close()
            self.bookmarks.close()
        self._close_viewer_controller()
        self._image_provider.close()
