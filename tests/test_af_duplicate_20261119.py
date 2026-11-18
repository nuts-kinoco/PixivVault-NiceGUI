import pytest
from nicegui import Client, ui

from pixivvault_nicegui.ui.duplicate_view import DuplicateView
from test_duplicate_view import StubSource, loaded


@pytest.mark.asyncio
async def test_legacy_candidates_explain_read_only_resolution():
    client = Client(ui.page('/_af_legacy_resolution'))
    try:
        with client:
            view = DuplicateView(StubSource())
            view.set_active(True)
        await loaded(view)
        assert '旧データベースは読み取り専用' in view.undecidable_notice.text
        assert not any(isinstance(node, ui.button) for node in view.undecidable_rows.descendants())
        view.close()
    finally:
        client.delete()
