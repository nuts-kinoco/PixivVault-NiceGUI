"""Loading, terminal states and work changes on the real detail controller."""
import asyncio
from threading import Event

import pytest
from nicegui import Client, ui

from pixivvault_nicegui.ui.gallery import GalleryView
from test_p3_viewer_author import Reader, services


@pytest.mark.asyncio
@pytest.mark.parametrize('fail', [False, True])
async def test_detail_pending_reopen_finishes_without_leaving_placeholder(fail):
    reader = Reader()
    reader.entered, reader.release = Event(), Event()
    reader.fail = fail
    client = Client(ui.page('/_detail_loading_terminal'))
    tasks = []
    try:
        with client:
            view = GalleryView(services(reader))
            view.open('101')
            tags_id = view.viewer.tags.id
            numbers_id = view.viewer.numbers.id
            status_id = view.viewer.detail_status.id
            before = asyncio.all_tasks()
            view.toggle_details()
            tasks = list(asyncio.all_tasks() - before)
            assert await asyncio.to_thread(reader.entered.wait, 5)
            panel = view.viewer
            assert panel.description_skeleton.visible and panel.tags_skeleton.visible
            assert not panel.description.visible and not panel.tags.visible
            assert panel.details_panel.props['aria-busy'] == 'true'
            assert len(panel.description_skeleton.default_slot.children) == 3
            assert len(panel.tags_skeleton.default_slot.children) == 3
            # Same-work page changes may happen while the single read is pending.
            view.move_page(1)
            view.toggle_details()
            assert panel.description_skeleton.visible
            assert reader.details == [('101', '91')]
            reader.release.set()
            await asyncio.gather(*tasks)
            assert not panel.description_skeleton.visible and not panel.tags_skeleton.visible
            assert panel.details_panel.props['aria-busy'] == 'false'
            assert panel.description.visible and panel.tags.visible
            assert (tags_id, numbers_id, status_id) == (panel.tags.id, panel.numbers.id, panel.detail_status.id)
            assert panel.tags.props['data-testid'] == 'viewer-detail-tags'
            assert panel.detail_status.props['data-testid'] == 'viewer-detail-status'
            if fail:
                assert '詳細を取得できませんでした' in panel.detail_status.text
                assert not view._detail_pending
            else:
                assert panel.description.text == 'caption'
                assert panel.detail_status.text == ''
                view.toggle_details()
                view.toggle_details()
                assert not panel.description_skeleton.visible
                assert reader.details == [('101', '91')]
    finally:
        reader.release.set()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        client.delete()


@pytest.mark.asyncio
async def test_late_failure_does_not_replace_another_work_or_keep_it_busy():
    reader = Reader()
    reader.entered, reader.release = Event(), Event()
    reader.fail = True
    client = Client(ui.page('/_detail_loading_stale'))
    tasks = []
    try:
        with client:
            view = GalleryView(services(reader))
            view.open('101')
            before = asyncio.all_tasks()
            view.toggle_details()
            tasks = list(asyncio.all_tasks() - before)
            assert await asyncio.to_thread(reader.entered.wait, 5)
            assert view.viewer.description_skeleton.visible
            view.close_viewer()
            view.open('102')
            reader.release.set()
            await asyncio.gather(*tasks)
            assert view.viewer.title.text == 'Second'
            assert view.viewer.details_panel.props['aria-busy'] == 'false'
            assert not view.viewer.description_skeleton.visible
            assert view.viewer.detail_status.text == ''
    finally:
        reader.release.set()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        client.delete()


def test_empty_values_end_loading_and_keep_existing_testid_elements():
    client = Client(ui.page('/_detail_loading_empty'))
    try:
        with client:
            view = GalleryView(services(Reader()))
            panel = view.viewer
            view.open('101')
            panel.set_details_open(True)
            elements = (panel.tags, panel.numbers, panel.detail_status)
            panel.set_detail_values('old description', ('old tag',))
            assert panel.tags.props['tags'] == ['old tag']
            panel.set_detail_loading(True)
            panel.set_detail_values('', ())
            assert panel.description.text == '' and panel.tags.props['tags'] == []
            assert panel.description.visible and panel.tags.visible
            assert not panel.description_skeleton.visible and not panel.tags_skeleton.visible
            assert panel.details_panel.props['aria-busy'] == 'false'
            assert elements == (panel.tags, panel.numbers, panel.detail_status)
    finally:
        client.delete()
