"""タブ内ビューアの表示契約。ブラウザ外観の受入はprime担当。"""

from nicegui import ui
import pytest

from pixivvault_nicegui.fixtures import generate_demo_artworks
from pixivvault_nicegui.ui.artwork_viewer import ArtworkViewer
from pixivvault_nicegui.ui.verified_image import VerifiedImage


@pytest.fixture
def viewer():
    calls = []
    with ui.column():
        component = ArtworkViewer(
            lambda: calls.append("previous"),
            lambda: calls.append("next"),
            lambda: calls.append("close"),
            lambda: calls.append("details"),
            lambda: calls.append("retry"),
        )
    return component, calls


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
    assert component.description.parent_slot.parent is component.stage
    assert component.image.parent_slot.parent is component.stage
    assert component.image._props["request_id"] == 0
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
