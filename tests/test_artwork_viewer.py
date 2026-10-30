"""タブ内ビューアの表示契約。ブラウザ外観の受入はprime担当。"""

from nicegui import Client, ui
import pytest
from types import SimpleNamespace

from pixivvault_nicegui.fixtures import generate_demo_artworks
from pixivvault_nicegui.models import Artwork
from pixivvault_nicegui.ui.artwork_viewer import ArtworkViewer
from pixivvault_nicegui.ui.gallery import GalleryView
from pixivvault_nicegui.ui.verified_image import VerifiedImage


@pytest.fixture
def viewer():
    calls = []
    client=Client(ui.page('/_artwork_viewer_contract'))
    with client, ui.column():
        component = ArtworkViewer(
            lambda: calls.append("previous"),
            lambda: calls.append("next"),
            lambda: calls.append("close"),
            lambda: calls.append("details"),
            lambda: calls.append("retry"),
        )
    try:yield component, calls
    finally:client.delete()


def test_structure_is_built_once_with_mounted_dialog_role(viewer):
    component, _ = viewer
    assert isinstance(component.image, VerifiedImage)
    assert component.dialog is component
    assert component.root._props["role"] == "dialog"
    assert component.root._props["aria-modal"] == "false"
    assert component.root._props["aria-label"] == "画集ビューア"
    assert component.root._props["aria-hidden"] == "true"
    assert not component._is_open
    assert component.root.visible
    assert "hidden" in component.root._classes
    assert component.root._props["data-testid"] == "gallery-viewer"
    assert component.image._props["data-testid"] == "viewer-image"
    assert component.description.parent_slot.parent is component.details_panel
    assert component.details_panel.parent_slot.parent is component.stage
    assert component.tags.parent_slot.parent is component.details_panel
    assert component.numbers.parent_slot.parent is component.details_panel
    assert component.image.parent_slot.parent is component.zoom_plane
    assert component.zoom_plane.parent_slot.parent is component.viewport
    assert component.viewport.parent_slot.parent is component.stage
    assert component.image._props["request_id"] == 0
    assert component.viewport._props["data-testid"] == "viewer-zoom-viewport"
    assert component.viewport._props["role"] == "region"
    assert component.root._props["data-zoom-generation"] == "0"
    assert component.root._props["data-zoom-ready"] == "false"
    assert component.zoom_value.text == "100%"
    assert component.zoom_value._props["aria-live"] == "polite"
    assert component.zoom_tools._props["role"] == "group"
    assert component.zoom_tools._props["aria-describedby"] == component.zoom_help.html_id
    for action, button in (("viewer-zoom-in", component.zoom_in),
                           ("viewer-zoom-out", component.zoom_out),
                           ("viewer-zoom-reset", component.zoom_reset)):
        assert button._props["data-testid"] == action
        assert button._props["aria-label"]
        assert not button.enabled
    for button in (component.download_button, component.bookmark_button,
                   component.follow_button):
        assert not button.enabled
        assert button.text in {"個別DL", "ブクマ", "フォロー"}
    for button in (component.download_button, component.bookmark_button,
                   component.follow_button, component.close_button,
                   component.previous_page, component.next_page,
                   component.details_button, component.retry_button):
        assert button._props.get("color") is None
    assert not hasattr(component, "previous_work")
    assert not hasattr(component, "next_work")


def test_visibility_idempotence_preserves_mounted_image(viewer):
    component, _ = viewer
    identifiers = (component.root.id, component.image.id)
    component.close()
    component.open()
    component.open()
    assert component._is_open
    assert component.root._props["aria-hidden"] == "false"
    component.image.set_source("/media/demo-0001/0/view.png")
    component.close()
    component.close()
    assert not component._is_open
    assert component.root.visible
    assert component.root._props["aria-hidden"] == "true"
    assert component.image._props["src"] == "/media/demo-0001/0/view.png"
    assert identifiers == (component.root.id, component.image.id)


def test_metadata_changes_without_rebuilding_elements(viewer):
    component, _ = viewer
    artwork = generate_demo_artworks(3)[2]
    ids = (component.image.id, component.root.id, component.title.id)
    component.set_metadata(artwork, 0)
    assert component.counter.text == f"1/{artwork.page_count}"
    assert "ページ" in component.counter._props["aria-label"]
    assert not component.previous_page.enabled
    component.set_metadata(artwork, artwork.page_count - 1)
    assert not component.next_page.enabled
    assert ids == (component.image.id, component.root.id, component.title.id)
    with pytest.raises(ValueError):
        component.set_metadata(artwork, artwork.page_count)


def test_details_status_and_callback_connections(viewer):
    component, calls = viewer
    component.set_details_open(True)
    assert component.description.visible
    component.set_details_open(False)
    assert not component.description.visible
    component._close_details_on_image_click(lambda: calls.append("unwanted"))
    assert calls == []
    component.set_details_open(True)
    component._close_details_on_image_click(lambda: calls.append("dismiss"))
    assert calls == ["dismiss"]
    calls.clear()
    component.set_status("画像を読み込めませんでした", error=True)
    assert component.retry_button.visible
    component.set_status("")
    assert not component.retry_button.visible
    # Verify callback wiring without sending any network event.
    for name, button in (
        ("previous", component.previous_page), ("next", component.next_page),
        ("close", component.close_button), ("details", component.details_button),
        ("retry", component.retry_button),
    ):
        handlers = [listener.handler for listener in button._event_listeners.values()
                    if listener.type == "click"]
        assert len(handlers) == 1
        handlers[0](None)
    assert calls == ["previous", "next", "close", "details", "retry"]


def test_zoom_client_is_scoped_bounded_and_resets_on_request_or_close(viewer):
    component, _ = viewer
    script = component._zoom_script()
    assert component.root.html_id in script
    assert all(fragment in script for fragment in (
        "viewer-zoom-viewport", "viewer-zoom-in", "viewer-zoom-out",
        "viewer-zoom-reset", "data-pv-displayed", "naturalWidth",
        "maxScale = 4", "addEventListener('wheel'", "addEventListener('pointerdown'",
        "setPointerCapture", "stopImmediatePropagation", "MutationObserver",
        "data-zoom-generation", "ResizeObserver", "addEventListener('keydown'",
        "event.key === '0'"))
    assert 'fetch(' not in script and 'emit(' not in script
    component.set_zoom_ready(True)
    assert all(b.enabled for b in (component.zoom_in, component.zoom_out, component.zoom_reset))
    component.reset_zoom()
    assert component.root._props["data-zoom-generation"] == "1"
    assert component.root._props["data-zoom-ready"] == "false"
    assert all(not b.enabled for b in (component.zoom_in, component.zoom_out, component.zoom_reset))
    component.open()
    component.set_zoom_ready(True)
    component.close()
    assert component.root._props["data-zoom-generation"] == "2"
    assert component.root._props["data-zoom-ready"] == "false"


def test_page_work_and_stale_decode_cannot_retain_or_reenable_zoom():
    class Images:
        @staticmethod
        def url(work_id, page=0, variant="view"):
            return f"/synthetic/{work_id}/{page}/{variant}.png"

    service = SimpleNamespace(
        artworks=(Artwork("101", "First", "Author", author_id="91", page_count=2),
                  Artwork("102", "Second", "Author", author_id="91", page_count=1)),
        images=Images(), live_client=None, managed_downloads=None,
    )
    client = Client(ui.page("/_zoom_navigation"))
    try:
        with client:
            gallery = GalleryView(service)
        gallery.open("101")
        first = gallery._image_url
        assert gallery.viewer.root._props["data-zoom-generation"] == "1"
        assert not gallery.viewer.zoom_in.enabled
        gallery._image_loaded(SimpleNamespace(args=first))
        assert gallery.viewer.zoom_in.enabled
        assert gallery.viewer.is_open
        gallery.move_page(1)
        second = gallery._image_url
        assert first != second and gallery.viewer.root._props["data-zoom-generation"] == "2"
        assert not gallery.viewer.zoom_in.enabled
        gallery._image_loaded(SimpleNamespace(args=first))
        assert not gallery.viewer.zoom_in.enabled
        gallery._image_loaded(SimpleNamespace(args=second))
        assert gallery.viewer.zoom_in.enabled
        gallery.open("102")
        assert gallery.viewer.root._props["data-zoom-generation"] == "3"
        assert not gallery.viewer.zoom_in.enabled
        gallery._image_failed(SimpleNamespace(args=gallery._image_url))
        assert not gallery.viewer.zoom_in.enabled
        gallery.close_viewer()
        assert gallery.viewer.root._props["data-zoom-ready"] == "false"
        assert gallery.viewer.root._props["data-zoom-generation"] == "4"
    finally:
        client.delete()
