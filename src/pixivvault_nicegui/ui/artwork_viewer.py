"""原版のタブ内画集ビューアに合わせた、一度だけ構築する表示部品。"""

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
        *,
        test_id: str = "gallery-viewer",
        image_test_id: str = "viewer-image",
    ) -> None:
        self.dialog = self  # GalleryView's existing dialog.open()/close() call sites.
        self.client = ui.context.client
        self._is_open = False
        self._zoom_generation = 0
        with ui.column().classes("pv-viewer pv-viewer-overlay hidden").props(
            "tabindex=-1 role=dialog aria-label=画集ビューア aria-modal=false aria-hidden=true "
            f"data-testid={test_id} data-zoom-generation=0 data-zoom-ready=false"
        ) as self.root:
                with ui.row().classes("pv-viewer-header w-full items-center"):
                    ui.label("画集ビューア").classes("pv-viewer-heading")
                    ui.space()
                    self.counter = ui.label().classes("pv-counter")
                    self.quality_button = ui.button('原寸を表示', icon='high_quality', color=None).props(
                        'flat dense no-caps data-testid=viewer-quality-toggle '
                        'aria-label=現在のページを原寸で表示')
                    self.quality_button.set_enabled(False)
                    self.download_button = self._unavailable_action("download", "個別DL", "個別ダウンロード")
                    self.bookmark_button = self._unavailable_action("favorite_border", "ブクマ", "ブックマーク")
                    self.follow_button = self._unavailable_action("person_add", "フォロー", "作者フォロー")
                    with ui.button(icon='more_vert', color=None).props('flat round aria-label=作品メニュー'):
                        with ui.menu():
                            self.pixiv_link = ui.link('Pixivで作品を開く', new_tab=True).classes('p-3').props(
                                'data-testid=viewer-pixiv-link')
                            self.pixiv_link.props['rel'] = 'noopener noreferrer'
                    self.close_button = ui.button(
                        icon="close", on_click=on_close, color=None
                    ).props("flat round aria-label=閉じる")
                with ui.element("div").classes("pv-viewer-stage") as self.stage:
                    self.previous_page = ui.button(
                        icon="chevron_left", on_click=on_previous, color=None
                    ).props("flat round aria-label=前のページ").classes("pv-viewer-page-prev")
                    with ui.element("div").classes("pv-viewer-viewport").props(
                        "data-testid=viewer-zoom-viewport role=region aria-label=作品画像の拡大と移動"
                    ) as self.viewport:
                        with ui.element("div").classes("pv-viewer-zoom-plane") as self.zoom_plane:
                            self.image = VerifiedImage(alt="作品画像", retain_previous=True).props(
                                f"data-testid={image_test_id}"
                            ).classes("pv-view-art")
                            self.image.on("click", lambda _: self._close_details_on_image_click(on_details))
                    self.next_page = ui.button(
                        icon="chevron_right", on_click=on_next, color=None
                    ).props("flat round aria-label=次のページ").classes("pv-viewer-page-next")
                    with ui.row().classes("pv-viewer-zoom-tools items-center").props(
                        "role=group aria-label=画像の拡大縮小"
                    ) as self.zoom_tools:
                        self.zoom_out = ui.button("−", color=None).props(
                            "flat dense aria-label=縮小 data-testid=viewer-zoom-out")
                        self.zoom_value = ui.label("100%").classes("pv-viewer-zoom-value").props(
                            "role=status aria-live=polite aria-atomic=true data-testid=viewer-zoom-value")
                        self.zoom_in = ui.button("+", color=None).props(
                            "flat dense aria-label=拡大 data-testid=viewer-zoom-in")
                        self.zoom_reset = ui.button("元に戻す", color=None).props(
                            "flat dense aria-label=倍率と位置をリセット data-testid=viewer-zoom-reset")
                        self.zoom_help = ui.label("ホイールまたは＋／−で拡大縮小、0でリセット。拡大中はドラッグで移動").classes(
                            "pv-viewer-zoom-help")
                        ui.tooltip("ホイール・＋／−で拡大縮小、0でリセット。拡大中はドラッグで移動")
                    self.zoom_tools.props(f"aria-describedby={self.zoom_help.html_id}")
                    self.image_status = ui.label().classes("pv-subtitle pv-viewer-status")
                    self.retry_button = ui.button(
                        "画像を再読込", on_click=on_retry, color=None
                    ).props("flat").classes("pv-viewer-retry")
                    with ui.column().classes("pv-description pv-viewer-details gap-2") as self.details_panel:
                        self.description = ui.label().classes("whitespace-pre-wrap")
                        self.tags = ui.row().classes("gap-1 flex-wrap").props('data-testid=viewer-detail-tags')
                        self.numbers = ui.label().props('data-testid=viewer-detail-numbers')
                        self.detail_status = ui.label().props('role=status data-testid=viewer-detail-status')
                with ui.column().classes("pv-viewer-bottom w-full"):
                    with ui.row().classes("pv-viewer-info w-full items-center"):
                        self.title = ui.label().classes("pv-view-title")
                        ui.element("span").classes("pv-viewer-info-separator").style("width:1px;height:14px")
                        self.author = ui.button(color=None).classes('pv-subtitle').props(
                            'flat dense no-caps data-testid=viewer-author-open')
                        self.author_button = self.author
                        self.author_button.tooltip('作者の作品を表示')
                        ui.space()
                        self.details_button = ui.button(
                            "詳細", on_click=on_details, color=None
                        ).props("flat aria-expanded=false")
                    self.action_status = ui.label().classes('pv-subtitle')
                    self.action_status.set_visibility(False)
        self._details_open = False
        self.set_zoom_ready(False)
        self.set_details_open(False)
        self.set_status("")

    @staticmethod
    def _unavailable_action(icon: str, label: str, purpose: str):
        button = ui.button(label, icon=icon, color=None).props("flat no-caps").classes("pv-viewer-header-action")
        button.props(f"aria-label={purpose}")
        button.set_enabled(False)
        button.tooltip("表示中の1ページを専用保存先へ保存します" if purpose == "個別ダウンロード" else
                       "Cookie接続後、表示中の作品・作者への変更を明示操作で送信します")
        return button

    def set_zoom_ready(self, ready: bool) -> None:
        """Only a decoded, current image may be enlarged or dragged."""
        self.root.props["data-zoom-ready"] = "true" if ready else "false"
        for button in (self.zoom_in, self.zoom_out, self.zoom_reset):
            button.set_enabled(ready)
        self.root.update()

    def set_quality_mode(self, variant: str, *, supported: bool, ready: bool) -> None:
        """Display actual source-quality state; never claim full-res on failure."""
        if variant not in {'view','original'}:
            raise ValueError('unsupported viewer quality')
        self.root.props['data-viewer-quality'] = variant
        self.quality_button.set_enabled(bool(supported and ready))
        self.quality_button.set_text('通常画質に戻す' if variant == 'original' else '原寸を表示')
        self.quality_button.props['aria-label'] = (
            '通常画質に戻す' if variant == 'original' else '現在のページを原寸で表示')
        self.quality_button.update()

    def reset_zoom(self) -> None:
        """Changing a request invalidates the previous page's client-side transform."""
        self._zoom_generation += 1
        self.root.props["data-zoom-generation"] = str(self._zoom_generation)
        self.set_zoom_ready(False)

    def _zoom_script(self) -> str:
        # Installed once on the mounted viewer. Pointer movement stays in the
        # browser; no image GET or Python round-trip is needed for each pixel.
        return r"""
        (() => {
            const viewer = document.getElementById('__ROOT_ID__');
            if (!viewer) return;
            const viewport = viewer.querySelector('[data-testid="viewer-zoom-viewport"]');
            const plane = viewport?.querySelector('.pv-viewer-zoom-plane');
            const value = viewer.querySelector('[data-testid="viewer-zoom-value"]');
            if (!viewport || !plane || !value) return;
            if (viewport.__pvZoomController) {
                viewport.__pvZoomController.reset();
                return;
            }

            const maxScale = 4;
            const step = 1.25;
            let scale = 1, offsetX = 0, offsetY = 0, pointer = null;
            let suppressClick = false;
            let generation = viewer.dataset.zoomGeneration;
            const currentImage = () => {
                if (viewer.getAttribute('aria-hidden') !== 'false' ||
                    viewer.dataset.zoomReady !== 'true') return null;
                const image = plane.querySelector('img[data-pv-displayed="true"]');
                return image?.complete && image.naturalWidth > 0 && image.naturalHeight > 0
                    ? image : null;
            };
            const bounds = () => {
                const rect = viewport.getBoundingClientRect();
                const image = currentImage();
                if (!image || !rect.width || !rect.height) return {x: 0, y: 0};
                const fit = Math.min(rect.width / image.naturalWidth, rect.height / image.naturalHeight);
                return {
                    x: Math.max(0, (image.naturalWidth * fit * scale - rect.width) / 2),
                    y: Math.max(0, (image.naturalHeight * fit * scale - rect.height) / 2),
                };
            };
            const paint = () => {
                const limit = bounds();
                offsetX = Math.max(-limit.x, Math.min(limit.x, offsetX));
                offsetY = Math.max(-limit.y, Math.min(limit.y, offsetY));
                plane.style.transform = `translate3d(${offsetX}px, ${offsetY}px, 0) scale(${scale})`;
                viewport.dataset.zoomScale = String(scale);
                viewport.dataset.zoomX = String(offsetX);
                viewport.dataset.zoomY = String(offsetY);
                viewport.dataset.zoomed = scale > 1 ? 'true' : 'false';
                viewport.dataset.dragging = pointer ? 'true' : 'false';
                value.textContent = Math.round(scale * 100) + '%';
            };
            const reset = () => {
                scale = 1; offsetX = 0; offsetY = 0; pointer = null; suppressClick = false;
                paint();
            };
            const zoom = (factor, clientX, clientY) => {
                if (!currentImage() || pointer) return;
                const next = Math.max(1, Math.min(maxScale, Math.round(scale * factor * 10000) / 10000));
                if (next === scale) return;
                const rect = viewport.getBoundingClientRect();
                const anchorX = clientX - rect.left - rect.width / 2;
                const anchorY = clientY - rect.top - rect.height / 2;
                offsetX = anchorX - (anchorX - offsetX) * next / scale;
                offsetY = anchorY - (anchorY - offsetY) * next / scale;
                scale = next;
                if (scale === 1) { offsetX = 0; offsetY = 0; }
                paint();
            };
            const centerZoom = factor => {
                const rect = viewport.getBoundingClientRect();
                zoom(factor, rect.left + rect.width / 2, rect.top + rect.height / 2);
            };

            viewport.addEventListener('wheel', event => {
                if (!currentImage() || event.target.closest('.pv-viewer-details')) return;
                event.preventDefault();
                const factor = event.deltaY < 0 ? step : 1 / step;
                centerZoomAtPointer(factor, event);
            }, {passive: false});
            const centerZoomAtPointer = (factor, event) => zoom(factor, event.clientX, event.clientY);
            viewport.addEventListener('pointerdown', event => {
                if (event.button !== 0 || scale <= 1 || !currentImage() ||
                    event.target.closest('button, .pv-viewer-details')) return;
                suppressClick = false;
                pointer = {id: event.pointerId, x: event.clientX, y: event.clientY,
                           startX: event.clientX, startY: event.clientY, moved: false};
            });
            viewport.addEventListener('pointermove', event => {
                if (!pointer || pointer.id !== event.pointerId) return;
                if (!pointer.moved &&
                    Math.hypot(event.clientX - pointer.startX, event.clientY - pointer.startY) > 3) {
                    pointer.moved = true;
                    viewport.setPointerCapture(event.pointerId);
                }
                if (!pointer.moved) return;
                offsetX += event.clientX - pointer.x;
                offsetY += event.clientY - pointer.y;
                pointer.x = event.clientX; pointer.y = event.clientY;
                paint();
                event.preventDefault();
            });
            const endDrag = event => {
                if (!pointer || pointer.id !== event.pointerId) return;
                suppressClick = pointer.moved;
                pointer = null;
                if (viewport.hasPointerCapture(event.pointerId))
                    viewport.releasePointerCapture(event.pointerId);
                paint();
            };
            viewport.addEventListener('pointerup', endDrag);
            viewport.addEventListener('pointercancel', endDrag);
            viewport.addEventListener('lostpointercapture', endDrag);
            viewport.addEventListener('click', event => {
                if (!suppressClick) return;
                suppressClick = false;
                event.preventDefault();
                event.stopImmediatePropagation();
            }, true);

            viewer.addEventListener('keydown', event => {
                const tag = event.target?.tagName?.toLowerCase();
                if (event.repeat || event.isComposing || event.ctrlKey || event.altKey ||
                    event.metaKey || ['input', 'textarea', 'select'].includes(tag) ||
                    event.target?.isContentEditable || !currentImage()) return;
                if (event.key === '+' || event.key === '=') centerZoom(step);
                else if (event.key === '-' || event.key === '_') centerZoom(1 / step);
                else if (event.key === '0') reset();
                else return;
                event.preventDefault();
                event.stopPropagation();
            }, true);

            viewer.querySelector('[data-testid="viewer-zoom-in"]').addEventListener(
                'click', () => centerZoom(step));
            viewer.querySelector('[data-testid="viewer-zoom-out"]').addEventListener(
                'click', () => centerZoom(1 / step));
            viewer.querySelector('[data-testid="viewer-zoom-reset"]').addEventListener(
                'click', reset);
            const observer = new MutationObserver(() => {
                const next = viewer.dataset.zoomGeneration;
                if (next !== generation || viewer.getAttribute('aria-hidden') !== 'false') {
                    generation = next;
                    reset();
                }
            });
            observer.observe(viewer, {attributes: true, attributeFilter: ['data-zoom-generation', 'aria-hidden']});
            new ResizeObserver(paint).observe(viewport);
            viewport.__pvZoomController = {reset};
            reset();
        })();
        """.replace("__ROOT_ID__", self.root.html_id)

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
                const generation = (node.__pvFocusGeneration || 0) + 1;
                node.__pvFocusGeneration = generation;
                node.__pvPreviousFocus = document.activeElement;
                requestAnimationFrame(() => {{
                    if (node.__pvFocusGeneration === generation && node.isConnected &&
                        node.getClientRects().length &&
                        !node.closest('[inert], [aria-hidden="true"]'))
                        node.focus({{preventScroll:true}});
                }});
            }})()""")
            self.client.run_javascript(self._zoom_script())

    def close(self, *, restore_focus: bool = True) -> None:
        # A tab departure also invalidates a queued focus restoration from a prior close.
        self._is_open = False
        self.reset_zoom()
        self.root.props("aria-hidden=true")
        self.root.classes(add="hidden")
        if self.client.has_socket_connection:
            self.client.run_javascript(f"""(() => {{
                const node = document.getElementById('{self.root.html_id}');
                if (!node) return;
                const generation = (node.__pvFocusGeneration || 0) + 1;
                node.__pvFocusGeneration = generation;
                const previous = node.__pvPreviousFocus;
                node.__pvPreviousFocus = null;
                if ({str(restore_focus).lower()}) requestAnimationFrame(() => {{
                    if (node.__pvFocusGeneration === generation &&
                        node.getAttribute('aria-hidden') === 'true' &&
                        previous?.isConnected && previous.getClientRects().length &&
                        !previous.closest('[inert], [aria-hidden="true"]') &&
                        typeof previous.focus === 'function')
                        previous.focus({{preventScroll:true}});
                }});
            }})()""")

    def set_metadata(self, artwork: Artwork, page_no: int) -> None:
        if not 0 <= page_no < artwork.page_count:
            raise ValueError("page_no out of range")
        self.title.set_text(artwork.title)
        self.author.set_text(artwork.author)
        self.description.set_text(artwork.description)
        self.set_detail_values(artwork.description,artwork.tags,artwork.create_date,artwork.bookmark_count)
        from ..viewer_metadata import pixiv_url
        self.pixiv_link.set_visibility(pixiv_url(artwork.work_id) is not None)
        self.pixiv_link.props['href'] = pixiv_url(artwork.work_id) or ''
        self.pixiv_link.update()
        self.counter.set_text(f"{page_no + 1}/{artwork.page_count}")
        self.counter._props["aria-label"] = f"ページ {page_no + 1} / {artwork.page_count}"
        self.counter.update()
        self.previous_page.set_enabled(page_no > 0)
        self.next_page.set_enabled(page_no + 1 < artwork.page_count)

    def set_detail_values(self, description, tags, date=None, bookmarks=None, views=None, width=None, height=None):
        self.description.set_text(description)
        self.tags.clear()
        with self.tags:
            for tag in tags: ui.label(tag).classes('pv-subtitle border rounded px-2')
        def known(value): return str(value) if value is not None else '不明'
        resolution=f'{width} × {height}' if width is not None and height is not None else '不明'
        self.numbers.set_text(f'投稿日 {known(date or None)} · ブクマ {known(bookmarks)} · 閲覧 {known(views)} · 解像度 {resolution}')
        self.detail_status.set_text('')

    def set_details_open(self, opened: bool) -> None:
        self._details_open = bool(opened)
        self.details_panel.set_visibility(self._details_open)
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
