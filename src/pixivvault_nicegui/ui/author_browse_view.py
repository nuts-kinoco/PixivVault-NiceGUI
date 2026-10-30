"""Read-only author overlay using the existing gallery and image pin owner."""
import asyncio
from nicegui import ui
from ..viewer_metadata import read_author
from ..state import GalleryState
from .gallery import GalleryView


class _Services:
    def __init__(self, parent):
        self.parent = parent
        self.artworks = ()
        self.images = parent.images

    def __getattr__(self, name):
        return getattr(self.parent, name)


class _Gallery(GalleryView):
    def _make_card(self, art):
        if art.work_id not in self.owner.missing_previews:
            return super()._make_card(art)
        # Original author pages retain a missing preview instead of losing a work.
        with self.grid:
            with ui.element('div').classes('pv-card').props(f'data-work-id={art.work_id}') as card:
                self.ranks[art.work_id] = ui.label().classes('pv-card-rank')
                ui.label('画像を取得できません').classes('pv-thumb pv-subtitle')
                with ui.element('div').classes('pv-card-meta'):
                    ui.label(art.title).classes('pv-card-title')
                    ui.label(art.author).classes('pv-card-author')
        self.cards[art.work_id] = card

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
    def __init__(self, services, reader, *, card_size='large'):
        self.services, self.reader = services, reader
        self.proxy = _Services(services)
        self.epoch = 0
        self.open_request = 0
        self.visible = self.closed = False
        self.pin = self.author_id = None
        self.cache, self.pending = {}, {}
        self._rendered_author_id = self._rendered_records = None
        self.missing_previews = set()
        with ui.column().classes('pv-viewer pv-viewer-overlay hidden').props(
                'data-testid=author-browse-overlay') as self.root:
            with ui.row().classes('w-full items-center'):
                self.title = ui.label().classes('pv-view-title')
                ui.space()
                ui.button('閉じる', on_click=self.hide).props('flat data-testid=author-browse-close')
            self.status = ui.label().props('role=status data-testid=author-browse-status')
            self.retry = ui.button('再取得').props('flat data-testid=author-browse-retry')
            self.retry.on('click', self._retry_clicked, js_handler=f"""() => {{
                const root = document.getElementById('{self.root.html_id}');
                if (root) emit({{author: root.dataset.authorId,
                                 epoch: root.dataset.authorEpoch}});
            }}""")
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
        self.epoch += 1
        epoch = self.epoch
        cached = self.cache.get(author_id)
        reuse = (author_id == self._rendered_author_id
                 and cached is not None and cached is self._rendered_records
                 and self.pin is not None and not self.pin.closed
                 and self.proxy.images is self.pin and not self.gallery.state.closed)
        self.author_id, self.author_name, self.visible = author_id, name, True
        self.root.props['data-author-id'] = author_id
        self.root.props['data-author-epoch'] = str(epoch)
        self.root.classes(remove='hidden')
        self.title.set_text(f'{name} ({author_id})')
        self.retry.set_visibility(False)
        if reuse:
            # Keep the expanded/search state and the existing scroll/card DOM.
            # The new epoch still invalidates every older pending viewer action.
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
            self.gallery.grid.clear()
            self.gallery.cards.clear()
            self.gallery.images.clear()
            self.gallery.ranks.clear()
            self.gallery.more_cell = None
            self.gallery._rendered_ids = ()
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
        old = self.gallery.state
        state = GalleryState(tuple(art if a.work_id == work_id else a for a in old.artworks), page_size=old.page_size)
        state.set_card_size(old.card_size)
        state.set_query(old.query)
        while state.shown_count < old.shown_count and state.has_more:
            state.load_more()
        old.close()
        self.gallery.state = self.gallery._viewer_state = state
        self.gallery.open(work_id)

    def hide(self):
        self.epoch += 1
        self.root.props['data-author-epoch'] = str(self.epoch)
        self.visible = False
        self.gallery.set_viewer_active(False)
        self.root.classes(add='hidden')

    def close(self):
        if self.closed:
            return
        self.hide()
        self.closed = True
        self.gallery.close()
        if self.pin is not None:
            self.pin.close()
            self.pin = None
