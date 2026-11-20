"""Batch viewer presentation changes for its independent Vue render."""

import asyncio

from nicegui import ui

from .verified_image import VerifiedImage


class ArtworkPage(ui.element, component='artwork_page.js'):
    def __init__(self):
        super().__init__()
        self._pending = {}
        self._scheduled = None
        self._revision = 0

    def bind(self, root, *controls):
        self.props['root_id'] = root.id
        for control in (root, *controls):
            control._artwork_page = self

    def stage(self, control):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        self._pending[control.id] = control
        if self._scheduled is None:
            self._scheduled = loop.call_soon(self._apply)
        return True

    def after_state(self, code):
        """Queue focus and zoom setup behind the pending visibility change."""
        if self._scheduled is None:
            self.client.run_javascript(code)
        else:
            asyncio.get_running_loop().call_soon(self.client.run_javascript, code)

    def _apply(self):
        self._scheduled = None
        pending, self._pending = self._pending, {}
        if not self._is_safe_to_interact():
            return
        self._revision += 1
        self.run_method('applyViewerState', self._revision,
                        {key: control._to_dict() for key, control in pending.items()})


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


class PageButton(_ViewerControl, ui.button):
    pass


class PageRow(_ViewerControl, ui.row):
    pass


class PageMenuItem(_ViewerControl, ui.menu_item):
    pass


class PageImage(_ViewerControl, VerifiedImage):
    pass
