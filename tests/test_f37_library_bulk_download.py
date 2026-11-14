"""F37-A: explicit library selections retain their freshly verified DTO."""
import asyncio
from dataclasses import replace

from nicegui import Client, ui
import pytest

from pixivvault_nicegui.bookmark_state import BookmarkState
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.bookmark_view import BookmarkView
from test_managed_download_services import FakeSource, terminal


class DetailSource(FakeSource):
    def get_artwork_download_record(self, work_id):
        assert work_id == '202'
        return self.detail


@pytest.mark.asyncio
@pytest.mark.parametrize('visibility', ['show', 'hide'])
async def test_library_selection_pins_fresh_detail_without_replacing_bookmarks(tmp_path, visibility):
    source = DetailSource()
    services = ApplicationServices(live_client=source, pixiv_download_root=tmp_path / 'managed')
    client = Client(ui.page('/_f37_library_' + visibility))
    try:
        await services.refresh_bookmark_cache()
        original = services.download_candidates()[0]
        services._remote_records = (replace(original, visibility=visibility),)
        bookmark = services.download_candidates()[0]
        source.detail = replace(bookmark, visibility='show', title='Fresh detail title',
                                tags=('fresh',), thumbnail='https://i.pximg.net/detail.png')
        chosen = []
        enqueue = services.enqueue_managed_download

        def checked(record, operation, *, owner):
            # Exercise the real admission: do not weaken equality to ID-only.
            assert record is source.detail
            assert services.download_candidates(owner=owner) == (source.detail,)
            assert services.download_candidates() == (bookmark,)
            assert services.download_candidates(owner='f' * 32) == (bookmark,)
            chosen.append(record)
            return enqueue(record, operation, owner=owner)

        services.enqueue_managed_download = checked
        with client:
            state = BookmarkState(local=(), cached_remote=(bookmark.as_artwork(),))
            view = BookmarkView(state, on_enqueue=services.save_explicit_work)
            view.render()
            view.select('202', True)
            assert await view.download_selected()
            assert '保存キュー受付 1 件' in view.download_status.text
            job = services.jobs.snapshot()[0]
            result = await asyncio.to_thread(terminal, services.jobs, job.job_id)
            assert result.done == result.total == 2
            assert chosen == [source.detail]
            assert services._managed_job_records[job.job_id] == source.detail
            assert services.download_candidates() == (bookmark,)
            assert not services._direct_download_candidates and not services._author_owned_pins
            before = len(source.calls)
            replay = await services.save_explicit_work('202', view._download_operations['202'])
            assert replay.job_id == job.job_id and len(source.calls) == before
            view.close()
    finally:
        client.delete()
        await asyncio.to_thread(services.close)


@pytest.mark.asyncio
async def test_download_tab_selection_still_saves_and_changed_dto_is_rejected(tmp_path):
    source = DetailSource()
    services = ApplicationServices(live_client=source, pixiv_download_root=tmp_path / 'managed')
    try:
        await services.refresh_bookmark_cache()
        record = services.download_candidates()[0]
        with pytest.raises(ValueError, match='download selection changed'):
            services.enqueue_managed_download(replace(record, title='Unselected metadata'), 'tampered')
        assert not services.jobs.snapshot()
        job = await asyncio.to_thread(services.enqueue_managed_download, record, 'download-tab')
        result = await asyncio.to_thread(terminal, services.jobs, job.job_id)
        assert result.done == result.total == 2
        assert services._managed_job_records[job.job_id] == record
    finally:
        await asyncio.to_thread(services.close)
