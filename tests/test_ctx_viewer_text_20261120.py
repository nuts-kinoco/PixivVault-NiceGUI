from nicegui import Client, ui

from pixivvault_nicegui.ui.artwork_viewer import ArtworkViewer


def test_viewer_context_menu_uses_text_only():
    client = Client(ui.page('/_ctx_viewer_menu_text_only'))
    try:
        with client:
            viewer = ArtworkViewer(lambda: None, lambda: None, lambda: None, lambda: None, lambda: None)
        assert len(viewer.context_items) == 4
        assert not any(isinstance(node, ui.icon) for node in viewer.context_menu.descendants())
    finally:
        client.delete()
