"""Read-only author overlay using the existing gallery and image pin owner."""
import asyncio
from nicegui import ui

from .management_dialog import dismiss_focus_js, remember_focus
from ..viewer_metadata import read_author
from ..state import GalleryState
from .gallery import GalleryView
from ..author_download import AuthorTarget
from .pixiv_action_buttons import PixivActionButtons


class _DownloadOwner:
    """Bind the existing author download controls to this overlay's lifetime."""
    target_mode = 'author'
    def __init__(self, owner):
        self.owner=owner;self.services=owner.services;self.root=owner.root
    @property
    def epoch(self):return self.owner.epoch
    @property
    def author_revision(self):return self.owner.epoch
    @property
    def author_targets(self):
        owner=self.owner
        return (AuthorTarget(owner.author_id,owner.author_name[:256],'id'),) if owner.author_id else ()
    def _valid(self):return self.owner.visible and not self.owner.closed


class _Services:
    def __init__(self, parent):
        self.parent = parent
        self.artworks = ()
        self.images = parent.images

    def __getattr__(self, name):
        return getattr(self.parent, name)


class _Gallery(GalleryView):
    def _card_menu(self, art):
        if self.owner.build_card_menu is not None:
            return self.owner.build_card_menu(art, self.owner)
        return None, ()

    def _card_source(self, art):
        if art.work_id in self.owner.missing_previews:
            return None
        return super()._card_source(art)

    def open(self, work_id, page_no=0):
        if self.owner.closed or not self.owner.visible or not self._viewer_active or self.state.closed:
            return
        self.owner.open_request += 1
        request = self.owner.open_request
        provider = self.services.images
        if work_id in self.owner.missing_previews or not provider.can_open(work_id):
            self.owner.status.set_text('この作品の画像を表示できません。')
            return
        if provider.needs_metadata(work_id):
            asyncio.create_task(self.owner.open_unknown(work_id, provider, self.owner.epoch, request))
            return
        super().open(work_id, page_no)

    async def _open_author(self, event=None):
        # This gallery already belongs to the selected author.
        self.close_viewer()


class AuthorBrowseView:
    def __init__(self, services, reader, *, card_size='large', build_card_menu=None):
        self.services, self.reader = services, reader
        self.build_card_menu = build_card_menu
        self.proxy = _Services(services)
        self.epoch = 0
        self.open_request = 0
        self.visible = self.closed = False
        self.pin = self.author_id = None
        self.cache, self.pending = {}, {}
        self._rendered_author_id = self._rendered_records = None
        self.missing_previews = set()
        self.author_download = None
        with ui.column().classes('pv-viewer pv-viewer-overlay hidden').props(
                'data-testid=author-browse-overlay') as self.root:
            with ui.row().classes('w-full items-center'):
                self.title = ui.label().classes('pv-view-title')
                ui.space()
                self.follow_actions = PixivActionButtons(services, prefix='author-browse')
                self.follow_actions.bookmark.set_visibility(False)
                close = ui.button('閉じる').props('flat data-testid=author-browse-close')
                close.on('click', self.hide, js_handler=dismiss_focus_js(self.root))
            self.status = ui.label().props('role=status data-testid=author-browse-status')
            self.retry = ui.button('再取得').props('flat data-testid=author-browse-retry')
            self.retry.on('click', self._retry_clicked, js_handler=f"""() => {{
                const root = document.getElementById('{self.root.html_id}');
                if (root) emit({{author: root.dataset.authorId,
                                 epoch: root.dataset.authorEpoch}});
            }}""")
            if getattr(services,'author_media',None) is not None:
                from .author_media_view import AuthorMediaView
                with ui.expansion('この作者の差分ダウンロード').classes('w-full shrink-0'):
                    with ui.column().classes('w-full max-h-72 overflow-auto'):
                        self.author_download=AuthorMediaView(_DownloadOwner(self))
                self.author_download.set_active(False)
            else:
                ui.label('作者の差分ダウンロードには保存先と読取接続の設定が必要です。').classes('pv-subtitle')
            self.gallery = _Gallery(self.proxy, card_size=card_size)
            self.gallery.owner = self
        self.retry.set_visibility(False)

    async def _records(self, author_id):
        if author_id in self.cache:
            return self.cache[author_id]
        task = self.pending.get(author_id)
        if task is None:
            task = asyncio.create_task(asyncio.to_thread(read_author, self.reader, author_id))
            self.pending[author_id] = task
        try:
            records = await asyncio.shield(task)
            if not self.closed and self.pending.get(author_id) is task:
                self.cache[author_id] = records
            return records
        finally:
            if task.done() and self.pending.get(author_id) is task:
                self.pending.pop(author_id)

    async def show(self, author_id, name):
        if self.closed or getattr(self.services, 'closing', False):
            return
        cached = self.cache.get(author_id)
        reuse = (author_id == self._rendered_author_id
                 and cached is not None and cached is self._rendered_records
                 and self.pin is not None and not self.pin.closed
                 and self.proxy.images is self.pin and not self.gallery.state.closed)
        if not reuse or not self.visible:
            self.epoch += 1
        epoch = self.epoch
        self.author_id, self.author_name, self.visible = author_id, name, True
        self.follow_actions.bind(None, author_id, author_name=name,
            is_current=lambda: self.visible and not self.closed and self.epoch == epoch
                and getattr(self.services, 'live_client', self.reader) is self.reader)
        self.root.props['data-epoch']=str(self.epoch)
        if self.author_download is not None:
            self.author_download._changed()
            self.author_download.set_active(True)
        self.root.props['data-author-id'] = author_id
        self.root.props['data-author-epoch'] = str(epoch)
        remember_focus(self.root)
        self.root.classes(remove='hidden')
        self.title.set_text(f'{name} ({author_id})')
        self.retry.set_visibility(False)
        if reuse:
            # Keep the expanded/search state and the existing scroll/card DOM.
            # Reopening a hidden author still has a new author epoch.
            self.status.set_text('')
            self.gallery.set_viewer_active(True)
            self.gallery.root.set_visibility(True)
            return
        self._rendered_author_id = self._rendered_records = None
        self.status.set_text('作者の作品を取得しています…')
        self.gallery.set_viewer_active(False)
        self.gallery.root.set_visibility(False)
        candidate, previous = None, self.pin
        try:
            records = await self._records(author_id)
            if (self.closed or not self.visible or epoch != self.epoch
                    or getattr(self.services, 'closing', False)):
                return
            candidate = self.services.acquire_ranking_images(tuple(r for r in records if r.thumbnail))
            self.proxy.images = candidate
            self.missing_previews = {r.work_id for r in records if not r.thumbnail}
            state = GalleryState(tuple(r.as_artwork() for r in records), page_size=50)
            state.set_card_size(self.gallery.state.card_size)
            self.gallery.state.close()
            self.gallery.state = self.gallery._viewer_state = state
            self.gallery.search_input.set_value('')
            self.gallery.render_gallery()
            self.gallery.set_viewer_active(True)
            self.gallery.root.set_visibility(True)
            self.pin, candidate = candidate, None
            self.status.set_text('')
            self._rendered_author_id, self._rendered_records = author_id, records
        except Exception:
            if self.visible and not self.closed and epoch == self.epoch:
                self.proxy.images = previous or self.services.images
                self.gallery.set_viewer_active(False)
                self.status.set_text('作者の作品を取得できませんでした。再取得できます。')
                self.retry.set_visibility(True)
        finally:
            if self.visible and not self.closed and self.epoch == epoch:
                self.follow_actions.refresh()
            if candidate is not None:
                candidate.close()
            if self.pin is not previous and previous is not None:
                previous.close()

    async def _retry_clicked(self, event):
        # The packet belongs to the failure view at click time. A delayed
        # button event must never evict a different/newer author's cache.
        args = event.args
        if (self.closed or not self.visible or not self.retry.visible
                or getattr(self.services, 'closing', False)
                or self.author_id is None or type(args) is not dict
                or args.get('author') != self.author_id
                or args.get('epoch') != str(self.epoch)):
            return
        await self.retry_load()

    async def retry_load(self):
        if (self.visible and not self.closed
                and not getattr(self.services, 'closing', False)):
            # An explicit retry requests fresh metadata. A detached older read
            # may finish, but _records will not let it replace the new cache.
            self.cache.pop(self.author_id, None)
            self.pending.pop(self.author_id, None)
            await self.show(self.author_id, self.author_name)

    async def open_unknown(self, work_id, provider, epoch, request):
        try:
            art = await asyncio.to_thread(provider.prepare_artwork, work_id)
        except Exception:
            if epoch == self.epoch and request == self.open_request and self.visible:
                self.status.set_text('作品のページ情報を取得できませんでした。')
            return
        if (self.closed or not self.visible or epoch != self.epoch
                or request != self.open_request or provider is not self.pin):
            return
        self.gallery.open_resolved_artwork(art)

    def hide(self):
        self.epoch += 1
        self.root.props['data-epoch']=str(self.epoch)
        self.root.props['data-author-epoch'] = str(self.epoch)
        self.visible = False
        self.follow_actions.clear()
        if self.author_download is not None:self.author_download.set_active(False)
        self.gallery.set_viewer_active(False)
        self.root.classes(add='hidden')

    def close(self):
        if self.closed:
            return
        self.hide()
        self.closed = True
        self.follow_actions.close()
        if self.author_download is not None:self.author_download.close()
        self.gallery.close()
        if self.pin is not None:
            self.pin.close()
            self.pin = None
