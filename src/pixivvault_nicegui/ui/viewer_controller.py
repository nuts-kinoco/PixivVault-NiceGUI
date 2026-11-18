"""ランキングとライブラリが共有する、画像要求とビューア操作の結線。"""

from __future__ import annotations

import asyncio
import json

from ..image_events import source_matches
from ..services import ApplicationServices
from ..state import GalleryState
from .artwork_viewer import ArtworkViewer


class ViewerController:
    def _init_viewer(self, services: ApplicationServices, state: GalleryState, host,
                     *, library: bool = False) -> None:
        self.services = services
        self._viewer_state = state
        self._viewer_active = True
        self._image_token = None
        self._image_url = ""
        self._displayed_gif = None
        self._decoded_generation = None
        self._decoded_identity = None
        self._decoded_quality = None
        self._quality = 'view'  # Original pixels require an explicit user click.
        self._quality_available = False
        self._original_failed = False
        # Keep a request identity across replacement GalleryState instances (reload).
        # A state generation alone can be reused and accept a stale browser load event.
        self._request_serial = 0
        self.detail_open = False
        self._viewer_host = host
        self._author_browser = None
        self._detail_cache = {}
        self._detail_pending = set()
        self.viewer = ArtworkViewer(
            lambda: self.move_page(-1), lambda: self.move_page(1),
            self.close_viewer, self.toggle_details, self.request_image,
            test_id="library-viewer" if library else "gallery-viewer",
            image_test_id="library-viewer-image" if library else "viewer-image",
            context_hide=not library,
        )
        self.viewer.on_hide = lambda: self._viewer_hide()
        self.viewer.on_folder = lambda: self._viewer_open_folder()
        from ..viewer_save import ViewerSave
        self._viewer_save = ViewerSave(services)
        from .pixiv_action_buttons import PixivActionButtons
        with self.viewer.root:
            self._pixiv_actions = PixivActionButtons(services,
                bookmark_button=self.viewer.bookmark_button, follow_button=self.viewer.follow_button,
                notice=self.viewer.action_status, prefix='viewer-action')
        self.viewer.author_button.on('click', self._open_author)
        self.viewer.download_button.props('data-testid=viewer-download')
        self.viewer.download_button.on('click', self._save_visible_work,
            js_handler=f"event => emit(document.getElementById('{self.viewer.root.html_id}').dataset.saveGeneration)")
        # Capture the browser's request identity at click time. A queued click
        # from a previous page must not request the newly selected original.
        self.viewer.quality_button.on('click', self._quality_clicked,
            js_handler=f"""event => {{
                const root = document.getElementById('{self.viewer.root.html_id}');
                if (!root) return;
                emit({{serial: root.dataset.qualitySerial,
                       work: root.dataset.qualityWork, page: root.dataset.qualityPage}});
            }}""")
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
        host.on("keydown.escape", lambda _: self.close_viewer()
                if self._viewer_state.viewer_open and not self.viewer.is_open else None)
        self.viewer_image.on("load", self._image_loaded)
        self.viewer_image.on("error", self._image_failed)

    def set_viewer_active(self, active: bool) -> None:
        self._viewer_active = bool(active)
        if not active:
            self.close_viewer(restore_focus=False)
            if self._author_browser is not None:self._author_browser.hide()

    def open(self, work_id: str, page_no: int = 0) -> None:
        if self._viewer_active and self._viewer_state.open_artwork(work_id, page_no):
            self._quality = 'view'
            self.detail_open = False
            self.viewer.set_details_open(False)
            self.update_viewer()

    def move_work(self, delta: int) -> None:
        if self._viewer_active and self._viewer_state.move_viewer(delta):
            self._quality = 'view'
            self.update_viewer()

    def move_page(self, delta: int) -> None:
        if self._viewer_active and self._viewer_state.move_page(delta):
            self._quality = 'view'
            self.detail_open = False
            self.viewer.set_details_open(False)
            self.update_viewer()

    def update_viewer(self) -> None:
        state = self._viewer_state
        art = state.selected_artwork
        if art is None or state.closed or not self._viewer_active:
            return
        self.viewer.set_metadata(art, state.selected_page)
        from ..viewer_metadata import pixiv_url
        self.viewer.author_button.set_enabled(self._viewer_reader() is not None and pixiv_url(art.author_id,author=True) is not None)
        self.viewer.set_context_target(art, hide_ok=getattr(self.services,'recommendations',None) is not None,
                                       folder_ok=self._folder_choices() != ())
        page_no = state.selected_page
        self._pixiv_actions.bind(art.work_id, art.author_id, author_name=art.author,
            is_current=lambda: (self._viewer_active and self._viewer_state is state
                and not state.closed and state.viewer_open
                and state.selected_artwork == art and state.selected_page == page_no))
        self.request_image()

    def request_image(self) -> None:
        state = self._viewer_state
        art = state.selected_artwork
        if art is None or state.closed or not self._viewer_active:
            return
        self._displayed_gif = None
        self._decoded_generation = None
        self._image_token = state.begin_request(art.work_id)
        # The new state token is not bound to the previous image URL. Invalidate
        # old browser events even if provider preflight fails before set_source.
        self._image_url = ''
        self._request_serial += 1
        self._original_failed = False
        # Zoom/pan belong to the decoded page, never to a retained previous frame
        # or a pending GIF/HTTP request.
        self.viewer.reset_zoom()
        self.viewer.root.props['data-save-generation'] = str(self._request_serial)
        self.viewer.root.props['data-quality-serial'] = str(self._request_serial)
        self.viewer.root.props['data-quality-work'] = art.work_id
        self.viewer.root.props['data-quality-page'] = str(state.selected_page)
        self.viewer.root.update()
        self.viewer.download_button.set_enabled(False)
        self.viewer.download_button.set_text("個別DL")
        # fragment はHTTPへ送られず、既存cacheを保って要求世代を照合できる。
        provider = getattr(self, "_image_provider", self.services.images)
        supports = getattr(provider, 'supports_original', None)
        try:
            self._quality_available = bool(callable(supports) and supports(art.work_id))
        except Exception:
            self._quality_available = False
        if not self._quality_available:
            self._quality = 'view'
        self.viewer.set_quality_mode(self._quality, supported=self._quality_available, ready=False)
        try:
            animation = getattr(provider,'is_animation',lambda wid:False)(art.work_id)
            url = None if animation else provider.url(art.work_id, state.selected_page, self._quality)
        except Exception:
            # A closed/missing provider can fail before a new source is set.
            # Fail this request and discard the previous work's visible frame.
            state.finish_image(self._image_token, success=False)
            self._image_token = None
            self._image_url = ''
            self._decoded_identity = None
            self._decoded_quality = None
            self._quality = 'view'
            self._quality_available = False
            self.viewer_image.set_source('', retain_previous=False)
            self.viewer_image.props['aria-busy'] = 'false'
            self.viewer.set_quality_mode('view', supported=False, ready=False)
            self.viewer.set_status('画像を準備できませんでした。画像を再読込してください', error=True)
            self.dialog.open()
            return
        if animation:
            self._quality='view'
            self._quality_available=False
            self.viewer.set_quality_mode('view',supported=False,ready=False)
            # GIF rendering finishes asynchronously; invalidate the previous
            # still image immediately, including when GIF decoding fails.
            self.viewer_image.set_source('', retain_previous=False)
            self._image_url = 'viewer-gif-'+str(self._request_serial)
            self.viewer_image.props['aria-busy'] = 'true'
            self.viewer.set_status('うごイラ読込中')
            self._animation_task=asyncio.create_task(self._load_viewer_gif(provider,art,self._request_serial))
            return
        self._image_url = url + f"#request-{self._image_token.generation}-{self._request_serial}"
        self.viewer_image.set_source(
            self._image_url,
            retain_previous=self._decoded_identity == (art.work_id, state.selected_page),
        )
        self.viewer_image.props["aria-busy"] = "true"
        self.viewer.set_status("画像読込中")

    async def _load_viewer_gif(self,provider,art,generation):
        import base64
        def current():
            return (self._viewer_active and not self._viewer_state.closed and self._viewer_state.viewer_open
                    and self._request_serial==generation and self._viewer_state.selected_artwork==art)
        try:
            display=await asyncio.to_thread(provider.display_animation,art.work_id)
            if not current():return
            if display.record.remote_id!=art.work_id or (art.author_id and display.record.author_id!=art.author_id):
                raise ValueError('displayed animation identity changed')
            self._displayed_gif=display
            # The exact server-owned bytes displayed here are also the save input.
            # Emit a short request identity, not the potentially large data URI.
            self.viewer_image.set_source('data:image/gif;base64,'+base64.b64encode(display.data).decode('ascii'),
                                         event_source=self._image_url)
        except Exception:
            if current():
                self._displayed_gif=None
                self._viewer_state.finish_image(self._image_token,success=False)
                self.viewer_image.props['aria-busy']='false'
                self.viewer.set_status('うごイラを読み込めませんでした',error=True)
                self.viewer.download_button.disable()
                self.dialog.open()

    def _image_loaded(self, event) -> None:
        if (self._viewer_active and source_matches(event.args, self._image_url)
                and self._image_token is not None):
            if self._viewer_state.finish_image(self._image_token):
                self._decoded_generation = self._request_serial
                self._decoded_identity = (self._viewer_state.selected_artwork.work_id,
                                          self._viewer_state.selected_page)
                self._decoded_quality = self._quality
                self.viewer_image.style("opacity:1")
                self.viewer_image.props["aria-busy"] = "false"
                self.viewer.set_zoom_ready(True)
                self.viewer.set_quality_mode(self._quality, supported=self._quality_available, ready=True)
                self.viewer.set_status("")
                self.viewer.download_button.set_enabled(getattr(self.services, 'managed_downloads', None) is not None and not self._viewer_save.busy)
                self.dialog.open()
                self._prefetch_next_view_page()

    def _prefetch_next_view_page(self) -> None:
        """Warm one regular *next* page, without adding it to the visible DOM.

        Only a successfully decoded current page may start this request.  A
        prefetched image does not change the displayed work/page, request
        serial, zoom state, or original-quality consent.  The browser keeps at
        most one preloader reference per viewer.
        """
        state = self._viewer_state
        artwork = state.selected_artwork
        if (not self._viewer_active or state.closed or not state.viewer_open
                or artwork is None or self._quality != 'view'
                or self._decoded_generation != self._request_serial
                or state.selected_page + 1 >= artwork.page_count):
            return
        client = getattr(self.viewer, 'client', None)
        if client is None or not client.has_socket_connection:
            return
        provider = getattr(self, '_image_provider', self.services.images)
        try:
            if getattr(provider, 'is_animation', lambda _: False)(artwork.work_id):
                return
            next_url = provider.url(artwork.work_id, state.selected_page + 1, 'view')
            # The app's existing loopback media route is the only permissible
            # speculative transport; never add an external browser request.
            if not isinstance(next_url, str) or not next_url.startswith('/media/'):
                return
        except Exception:
            return  # A speculative read must never break the visible page.
        root_id = json.dumps(self.viewer.root.html_id)
        payload = json.dumps({
            'serial': str(self._request_serial),
            'work': artwork.work_id,
            'page': str(state.selected_page),
            'url': next_url,
        }, ensure_ascii=True)
        try:
            client.run_javascript(f"""(() => {{
                const root = document.getElementById({root_id});
                const next = {payload};
                if (!root || root.dataset.qualitySerial !== next.serial ||
                    root.dataset.qualityWork !== next.work ||
                    root.dataset.qualityPage !== next.page ||
                    root.getAttribute('aria-hidden') !== 'false') return;
                const image = new Image();
                image.decoding = 'async';
                root.__pvNextViewPreload = image;
                image.src = next.url;
            }})()""")
        except Exception:
            pass  # Disconnected browsers do not affect foreground decoding.

    def _image_failed(self, event) -> None:
        if (self._viewer_active and source_matches(event.args, self._image_url)
                and self._image_token is not None):
            if self._viewer_state.finish_image(self._image_token, success=False):
                art = self._viewer_state.selected_artwork
                retained = (art is not None and self._decoded_identity ==
                            (art.work_id, self._viewer_state.selected_page))
                if self._quality == 'original':
                    # The last successfully decoded normal image is the only
                    # safe fallback. Do not label it as original resolution.
                    restored_original = retained and self._decoded_quality == 'original'
                    self._quality = 'original' if restored_original else 'view'
                    self._original_failed = not restored_original
                    self.viewer.set_quality_mode(self._quality, supported=self._quality_available,
                                                 ready=retained)
                    if retained and not restored_original:
                        self.viewer.quality_button.set_text('通常画質で再表示')
                        self.viewer.quality_button.props['aria-label'] = '通常画質で再表示'
                        self.viewer.quality_button.update()
                    self.viewer.set_status('原寸画像の再読込に失敗しました。直前の原寸画像を表示しています'
                                           if restored_original else
                                           '原寸画像を読み込めませんでした。通常画質で再表示できます'
                                           if retained else '原寸画像を読み込めませんでした。画像を再読込してください',
                                           error=True)
                else:
                    if retained and self._decoded_quality == 'original':
                        self._quality = 'original'
                    self.viewer.set_quality_mode(self._quality,
                                                 supported=self._quality_available, ready=retained)
                    self.viewer.set_status('通常画質を読み込めませんでした。直前の原寸画像を表示しています'
                                           if retained and self._quality == 'original'
                                           else '画像を読み込めませんでした', error=True)
                self.viewer.set_zoom_ready(retained)
                self.viewer_image.props["aria-busy"] = "false"
                self.dialog.open()

    def _quality_clicked(self, event) -> bool:
        """Accept only the page/request on which this browser click occurred."""
        state = self._viewer_state
        art = state.selected_artwork
        payload = getattr(event, 'args', None)
        if (art is None or not isinstance(payload, dict) or
                payload.get('serial') != str(self._request_serial) or
                payload.get('work') != art.work_id or
                payload.get('page') != str(state.selected_page)):
            return False
        return self.toggle_quality()

    def toggle_quality(self) -> bool:
        """Switch only the current decoded page, never prefetch every original."""
        state=self._viewer_state
        if (not self._viewer_active or state.closed or not state.viewer_open
                or state.selected_artwork is None or not self._quality_available
                or (self._quality == 'view' and not self._original_failed
                    and self._decoded_generation != self._request_serial)
                or self.viewer_image.props.get('aria-busy') != 'false'):
            return False
        if self._original_failed:
            self._original_failed = False
            self._quality = 'view'
            self.request_image()
            return True
        self._quality = 'view' if self._quality == 'original' else 'original'
        self.request_image()
        return True

    async def _save_visible_work(self, event):
        generation=self._request_serial
        art=self._viewer_state.selected_artwork
        page=self._viewer_state.selected_page
        if (art is None or event.args != str(generation) or self._viewer_save.busy
                or self.viewer_image.props.get('aria-busy')!='false'
                or self._decoded_generation!=generation):return
        def current():
            return (self._viewer_active and not self._viewer_state.closed and self._viewer_state.viewer_open
                    and self._request_serial==generation and self._viewer_state.selected_artwork==art
                    and self._viewer_state.selected_page==page)
        if not current():return
        self.viewer.download_button.set_enabled(False)
        self.viewer.download_button.set_text("DL中")
        try:
            if self._displayed_gif is not None:
                saved=await self._viewer_save.save_gif(self._displayed_gif,current)
            else:
                provider=getattr(self,"_image_provider",self.services.images)
                resolve=getattr(provider,'source_page_identity',None)
                identity=resolve(art.work_id,page) if callable(resolve) else None
                saved=await self._viewer_save.save_page(
                    art.work_id,page,art.page_count,art.author_id or None,current,
                    source_identity=identity)
            if current():
                self.viewer.download_button.set_text("保存済" if saved is not None else "個別DL")
                self.viewer.set_status(('このうごイラをGIFで保存しました' if self._displayed_gif is not None else 'このページを保存しました')
                                       if saved is not None else '表示が変わりました。作品を確認してください')
                if saved is not None and getattr(saved,'export_error',False):
                    self.viewer.download_button.set_text('個別DL')
                    self.viewer.set_status('画像は保存済みですが、保存先への出力・履歴の記録に失敗しました。個別DLで再試行してください',error=True)
                    self.viewer.retry_button.set_visibility(False)
        except Exception as error:
            if current():
                from ..managed_downloads import ManagedPageSelectionChanged
                changed = (isinstance(error, ManagedPageSelectionChanged)
                           or isinstance(error.__cause__, ManagedPageSelectionChanged))
                self.viewer.download_button.set_text("個別DL")
                self.viewer.set_status(
                    "保存元が変更されたか隔離されています。ライブラリを再読込して、このページを開き直してください。"
                    if changed else "保存に失敗しました。作品・保存先を確認して再試行してください",error=True)
                self.viewer.retry_button.set_visibility(False)  # Retry this save with 個別DL.
        finally:
            # Busy belongs to the in-flight save, not the old page. Re-enable a
            # new *decoded* page after it drains, without leaking old status.
            state=self._viewer_state
            if (self._viewer_active and not state.closed and state.viewer_open
                    and self.viewer_image.props.get('aria-busy')=='false'
                    and self._decoded_generation==self._request_serial):
                self.viewer.download_button.set_enabled(getattr(self.services,'managed_downloads',None) is not None)

    def toggle_details(self) -> None:
        if self._viewer_active and self._viewer_state.viewer_open and not self._viewer_state.closed:
            self.detail_open = not self.detail_open
            self.viewer.set_details_open(self.detail_open)
            if self.detail_open:
                art=self._viewer_state.selected_artwork
                key=(art.work_id,art.author_id)
                if key in self._detail_cache:self._show_detail(self._detail_cache[key])
                elif self._viewer_reader() is not None and art.author_id and not (art.description and art.tags):
                    self.viewer.set_detail_loading(True)
                    self.viewer.detail_status.set_text('詳細を取得しています…')
                    if key not in self._detail_pending:
                        self._detail_pending.add(key)
                        asyncio.create_task(self._load_detail(art,self._request_serial))

    def _viewer_reader(self):
        return getattr(self.services,'live_client',None) or getattr(getattr(self.services,'ranking_source',None),'reader',None)

    def _show_detail(self, data):
        self.viewer.set_detail_values(data['description'],data['tags'],data['create_date'],
            data['bookmarkCount'],data['viewCount'],data['width'],data['height'])

    async def _load_detail(self, art, generation):
        from ..viewer_metadata import read_details
        key=(art.work_id,art.author_id)
        def selected():
            return (self._viewer_active and not self._viewer_state.closed and self._viewer_state.viewer_open
                    and self.detail_open and self._viewer_state.selected_artwork==art)
        def current():
            return selected() and self._request_serial==generation
        try:
            data=await asyncio.to_thread(read_details,self._viewer_reader(),art.work_id,art.author_id)
            self._detail_cache[key]=data
            if current():self._show_detail(data)
        except Exception:
            # Details belong to a work, not an image page. A same-work page
            # change/reopen must also finish the pending loading indication.
            if selected():
                self.viewer.set_detail_loading(False)
                self.viewer.detail_status.set_text('詳細を取得できませんでした。詳細を開き直すと再試行します。')
        finally:
            self._detail_pending.discard(key)
            # A same-work reopen/page change may have occurred while this read was
            # pending. Re-render from the identity-checked cache only for the new
            # current selection, never into a different work or a closed viewer.
            if (generation!=self._request_serial and self._viewer_active and not self._viewer_state.closed
                    and self._viewer_state.viewer_open and self.detail_open
                    and self._viewer_state.selected_artwork==art and key in self._detail_cache):
                self._show_detail(self._detail_cache[key])

    def _folder_choices(self):
        try:return tuple(self.services.saved_folder_choices())
        except Exception:return ()

    def _viewer_say(self, message: str) -> None:
        self.viewer.action_status.set_text(message)
        self.viewer.action_status.set_visibility(bool(message))

    async def _viewer_hide(self):
        """右クリックの「出さない」。おすすめ・ランキングと同じ非表示データへ 1 件書く。"""
        state = self._viewer_state
        art = state.selected_artwork
        if art is None or state.closed or not self._viewer_active or not state.viewer_open:return
        from ..hidden_works import HideUnavailable, set_hidden
        try:hidden = await asyncio.to_thread(set_hidden, self.services, art.work_id, True)
        except HideUnavailable:
            self._viewer_say('出さない設定の保存先が未指定のため、変更できません。');return
        except Exception:
            self._viewer_say('出さない設定を保存できませんでした。再読込してから再試行してください。');return
        if state.closed or not self._viewer_active:return
        self._viewer_say('次回から表示しません。')
        self._after_viewer_hide(art, hidden)

    def _after_viewer_hide(self, art, hidden) -> None:
        """出さない確定後。既定ではビューアを閉じる（一覧側の再描画は持ち主が上書きする）。"""
        self.close_viewer()

    async def _viewer_open_folder(self):
        """右クリックの「保存フォルダを開く」。保存先が 1 つのときだけ直接開く（複数はヘッダーの選択）。"""
        from ..desktop_integration import DesktopActionRejected, DesktopUnavailable
        state = self._viewer_state
        art = state.selected_artwork
        if art is None or state.closed or not self._viewer_active:return
        choices = self._folder_choices()
        if not choices:
            self._viewer_say('保存先が未設定です。設定を確認してください。');return
        if len(choices) > 1:
            self._viewer_say('保存先が複数あります。ヘッダーの「保存フォルダを開く」から選んでください。');return
        try:
            await self.services.open_saved_folder(
                choices[0], is_current=lambda: self._viewer_active and not state.closed and state.selected_artwork == art)
        except (DesktopActionRejected, DesktopUnavailable) as error:
            self._viewer_say(str(error))
        except Exception:
            self._viewer_say('保存フォルダを開けませんでした。')

    async def _open_author(self, event=None):
        art=self._viewer_state.selected_artwork
        await self._open_author_art(art)

    async def _open_author_art(self,art):
        reader=self._viewer_reader()
        if art is None or not art.author_id or reader is None or not self._viewer_active or self._viewer_state.closed:return
        if self._author_browser is not None and self._author_browser.reader is not reader:
            self._author_browser.close();self._author_browser=None
        if self._author_browser is None:
            from .author_browse_view import AuthorBrowseView
            with self._viewer_host:
                self._author_browser=AuthorBrowseView(self.services,reader,card_size=self._viewer_state.card_size)
        self.close_viewer(restore_focus=False)
        await self._author_browser.show(art.author_id,art.author)

    def _on_key(self, event) -> None:
        if (not self._viewer_active or not self._viewer_state.viewer_open
                or self._viewer_state.closed):
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

    def close_viewer(self, *, restore_focus: bool = True) -> None:
        self._pixiv_actions.clear()
        self._displayed_gif = None
        self._quality='view'
        self._quality_available=False
        self._original_failed=False
        self._decoded_identity=None
        self._decoded_quality=None
        self.viewer.set_quality_mode('view',supported=False,ready=False)
        self.viewer.download_button.set_enabled(False)
        self._viewer_state.close_viewer()
        self.dialog.close(restore_focus=restore_focus)
        self._image_token = None
        self._image_url = ""
        self.viewer_image.set_source("")

    def _close_viewer_controller(self) -> None:
        self._pixiv_actions.close()
        self._displayed_gif = None
        self._quality='view'
        self._quality_available=False
        self._original_failed=False
        self._decoded_identity=None
        self._decoded_quality=None
        self.viewer.reset_zoom()
        self.viewer_image.set_source('')
        if self._author_browser is not None:self._author_browser.close()
        self._viewer_state.close()
        self._viewer_active = False
        self._image_token = None
        self._image_url = ""
