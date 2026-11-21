"""Batch viewer presentation changes for its independent Vue render."""

import asyncio

from nicegui import json, ui

from ..media_routes import ViewerGif
from .verified_image import VerifiedImage


class ArtworkPage(ui.element, component='artwork_page.js'):
    def __init__(self):
        super().__init__()
        self._pending = {}
        self._published = set()
        self._dom_actions = []
        self._scheduled = None

    def bind(self, *controls):
        if len(self.default_slot.children) != 1:
            raise ValueError('ArtworkPage requires exactly one root child')
        for control in controls:
            control._artwork_page = self

    def stage(self, control):
        # NiceGUI 3.18 includes these element values and event listeners in
        # build_response(). Start the ordered stream only after that snapshot.
        if not self.client._response_built or self.is_deleted:
            return False
        self._pending[control.id] = control
        self._schedule()
        return True

    def after_state(self, code):
        """Send a DOM action after NiceGUI's update handler awaits nextTick."""
        if self.is_deleted or self.client.is_deleted:
            return
        self._dom_actions.append(code)
        self._schedule()

    def _schedule(self):
        if self._scheduled is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._apply()
        else:
            self._scheduled = loop.call_soon(self._apply)

    def _apply(self):
        self._scheduled = None
        pending, self._pending = self._pending, {}
        actions, self._dom_actions = self._dom_actions, []
        if self.is_deleted or self.client.is_deleted:
            return
        outbox = self.client.outbox
        # Ordinary outbox.updates are prepended to replayed messages during
        # reconnect. Append every viewer snapshot and DOM action to messages
        # so NiceGUI's rewind preserves their order. Freeze mutable props now.
        patch = {key: control._to_dict() for key, control in pending.items() if not control.is_deleted}
        if patch:
            snapshot = json.loads(json.dumps(patch))
            outbox.enqueue_message('update', snapshot, self.client.id)
            self._published.update(snapshot)
        for code in actions:
            outbox.enqueue_message('run_javascript', {'code': code}, self.client.id)

    def _handle_delete(self):
        if self._scheduled is not None:
            self._scheduled.cancel()
            self._scheduled = None
        self._pending.clear()
        self._dom_actions.clear()
        if self._published and not self.client.is_deleted:
            # NiceGUI also sends standard deletions ahead of replay. Retire any
            # descriptors that an earlier viewer message could reintroduce.
            self.client.outbox.enqueue_message('update', dict.fromkeys(self._published), self.client.id)
        self._published.clear()
        super()._handle_delete()


class _ViewerControl:
    _artwork_page = None

    def update(self):
        if self._artwork_page is None:
            super().update()
        elif self._is_safe_to_interact() and not self._artwork_page.stage(self):
            super().update()


class PageColumn(_ViewerControl, ui.column):
    pass


class PageLabel(_ViewerControl, ui.label):
    pass


class PageLink(_ViewerControl, ui.link):
    pass


class PageButton(_ViewerControl, ui.button):
    pass


class PageRow(_ViewerControl, ui.row):
    pass


class PageTags(_ViewerControl, ui.element, component='artwork_tags.js',
               default_classes='nicegui-row row'):
    pass


class PageMenuItem(_ViewerControl, ui.menu_item):
    pass


class PageImage(_ViewerControl, VerifiedImage):
    _viewer_gif = None

    def set_gif(self, data: bytes, *, event_source: str):
        image = ViewerGif(data, self.client.request.app.state.viewer_gifs)
        self.set_source(image.url, event_source=event_source)
        self._viewer_gif = image

    def _release_gif(self):
        if self._viewer_gif is not None:
            self._viewer_gif.close()
            self._viewer_gif = None

    def set_source(self, source, **kwargs):
        self._release_gif()
        return super().set_source(source, **kwargs)

    def _handle_delete(self):
        self._release_gif()
        super()._handle_delete()
