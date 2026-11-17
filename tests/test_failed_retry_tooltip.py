"""The retry button explains itself only while it is disabled."""
import pytest
from nicegui import Client, ui
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.failed_records_view import FailedRecordsView
from test_q07_novel_retry import create

OLD = 'ダウンロード再試行の書込み接続は未受入です'


def tips(view, button):
    target = f'#{button.html_id}'
    return [element for element in view.root.descendants()
            if isinstance(element, ui.tooltip)
            and (element.props.get('target') == target or element.parent_slot.parent is button)]


def visible_text(view, button):
    return ' '.join(str(element.text) for element in tips(view, button) if element.visible)


@pytest.mark.asyncio
async def test_enabled_retry_button_shows_no_stale_unaccepted_tooltip(tmp_path):
    service, reader, db = create(tmp_path, 3)
    client = Client(ui.page('/_retry_tooltip_enabled'))
    try:
        with client:
            view = FailedRecordsView(service)
            await view._refresh(view.state.begin_refresh())
            assert view.retry_all.enabled
            assert OLD not in visible_text(view, view.retry_all)
            view.close()
    finally:
        client.delete()
        service.close()


@pytest.mark.asyncio
async def test_disabled_retry_button_keeps_its_explanation(tmp_path, monkeypatch):
    service, reader, db = create(tmp_path, 3)
    # Retry connection not accepted for this startup.
    monkeypatch.setattr(ApplicationServices, 'legacy_failed_retry_available', property(lambda self: False))
    client = Client(ui.page('/_retry_tooltip_disabled'))
    try:
        with client:
            view = FailedRecordsView(service)
            assert not view.retry_all.enabled
            assert OLD in visible_text(view, view.retry_all)
            view.close()
    finally:
        client.delete()
        service.close()


@pytest.mark.asyncio
async def test_busy_retry_button_does_not_claim_connection_is_unaccepted(tmp_path):
    service, reader, db = create(tmp_path, 3)
    client = Client(ui.page('/_retry_tooltip_busy'))
    try:
        with client:
            view = FailedRecordsView(service)
            await view._refresh(view.state.begin_refresh())
            assert view.retry_all.enabled
            view._retry_busy = True
            view.render()
            assert not view.retry_all.enabled and OLD not in visible_text(view, view.retry_all)
            view._retry_busy = False
            view.close()
    finally:
        client.delete()
        service.close()
