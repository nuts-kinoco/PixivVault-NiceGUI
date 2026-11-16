"""Recommendation selection over the shared ArtworkViewer/controller."""
from __future__ import annotations

from ..state import GalleryState
from .viewer_controller import ViewerController


class _ViewerServices:
    def __init__(self, services):
        self.parent = services
        self.images = getattr(services, 'images', None)

    def __getattr__(self, name):
        return getattr(self.parent, name)


class RecommendationViewer(ViewerController):
    def __init__(self, services, host, *, on_close):
        self._on_selection_close = on_close
        self.selection = None
        self.closed = False
        self._init_viewer(_ViewerServices(services), GalleryState(()), host)
        self.viewer.root.props('data-testid=recommend-viewer')
        self.viewer_image.props('data-testid=recommend-viewer-image')
        self.set_viewer_active(False)

    def show(self, selection, *, card_size):
        if self.closed or selection.closed:
            raise RuntimeError('recommendation viewer closed')
        # Do not call the public close callback while adopting this request.
        super().close_viewer(restore_focus=False)
        if self.selection is not None:
            self.selection.close()
        old = self._viewer_state
        state = GalleryState((selection.artwork,))
        state.set_card_size(card_size)
        state.generation = old.generation + 1
        old.close()
        self._viewer_state = state
        self._image_provider = selection.images
        self.selection = selection
        self.set_viewer_active(True)
        super().open(selection.artwork.work_id)

    def close_viewer(self, *, restore_focus=True):
        self._on_selection_close()
        super().close_viewer(restore_focus=restore_focus)
        if self.selection is not None:
            self.selection.close()
            self.selection = None

    def close(self):
        if self.closed:
            return
        self.close_viewer(restore_focus=False)
        self._close_viewer_controller()
        self.closed = True
