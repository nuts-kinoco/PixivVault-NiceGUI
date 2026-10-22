"""ランキングとライブラリが共有する、画像要求とビューア操作の結線。"""

from __future__ import annotations

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
        # Keep a request identity across replacement GalleryState instances (reload).
        # A state generation alone can be reused and accept a stale browser load event.
        self._request_serial = 0
        self.detail_open = False
        self.viewer = ArtworkViewer(
            lambda: self.move_page(-1), lambda: self.move_page(1),
            self.close_viewer, self.toggle_details, self.request_image,
            test_id="library-viewer" if library else "gallery-viewer",
            image_test_id="library-viewer-image" if library else "viewer-image",
        )
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

    def open(self, work_id: str, page_no: int = 0) -> None:
        if self._viewer_active and self._viewer_state.open_artwork(work_id, page_no):
            self.detail_open = False
            self.viewer.set_details_open(False)
            self.update_viewer()

    def move_work(self, delta: int) -> None:
        if self._viewer_active and self._viewer_state.move_viewer(delta):
            self.update_viewer()

    def move_page(self, delta: int) -> None:
        if self._viewer_active and self._viewer_state.move_page(delta):
            self.detail_open = False
            self.viewer.set_details_open(False)
            self.update_viewer()

    def update_viewer(self) -> None:
        state = self._viewer_state
        art = state.selected_artwork
        if art is None or state.closed or not self._viewer_active:
            return
        self.viewer.set_metadata(art, state.selected_page)
        self.request_image()

    def request_image(self) -> None:
        state = self._viewer_state
        art = state.selected_artwork
        if art is None or state.closed or not self._viewer_active:
            return
        self._image_token = state.begin_request(art.work_id)
        self._request_serial += 1
        # fragment はHTTPへ送られず、既存cacheを保って要求世代を照合できる。
        self._image_url = (self.services.images.url(art.work_id, state.selected_page, "view")
                           + f"#request-{self._image_token.generation}-{self._request_serial}")
        self.viewer_image.set_source(self._image_url)
        self.viewer_image.props["aria-busy"] = "true"
        self.viewer.set_status("画像読込中")

    def _image_loaded(self, event) -> None:
        if (self._viewer_active and source_matches(event.args, self._image_url)
                and self._image_token is not None):
            if self._viewer_state.finish_image(self._image_token):
                self.viewer_image.style("opacity:1")
                self.viewer_image.props["aria-busy"] = "false"
                self.viewer.set_status("")
                self.dialog.open()

    def _image_failed(self, event) -> None:
        if (self._viewer_active and source_matches(event.args, self._image_url)
                and self._image_token is not None):
            if self._viewer_state.finish_image(self._image_token, success=False):
                self.viewer.set_status("画像を読み込めませんでした", error=True)
                self.viewer_image.props["aria-busy"] = "false"
                self.dialog.open()

    def toggle_details(self) -> None:
        if self._viewer_active and self._viewer_state.viewer_open and not self._viewer_state.closed:
            self.detail_open = not self.detail_open
            self.viewer.set_details_open(self.detail_open)

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
        self._viewer_state.close_viewer()
        self.dialog.close(restore_focus=restore_focus)
        self._image_token = None
        self._image_url = ""
        self.viewer_image.set_source("")

    def _close_viewer_controller(self) -> None:
        self._viewer_state.close()
        self._viewer_active = False
        self._image_token = None
        self._image_url = ""
