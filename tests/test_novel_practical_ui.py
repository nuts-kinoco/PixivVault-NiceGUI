"""P1 UI wiring over a fake authenticated reader; not yet executed."""
import asyncio
from nicegui import Client, ui
import pytest
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.managed_novel_jobs import PinnedNovelSource
from pixivvault_nicegui.ui.novel_save_view import NovelDownloadView, NovelLibraryView


class Reader:
    def require_identity(self): return '91'
    def close(self): pass
    def get_author_novel_ids(self, author): return ('123',)
    def get_bookmarked_novels(self, uid, rest):
        return iter([dict(id='123',user_id='91',title='架空小説')])
    def get_novel_payload(self, wid, author):
        return dict(id=wid,userId=author,userName='架空作者',title='架空小説',content='[[rb:漢字 > かんじ]][newpage]本文',
                    textEmbeddedImages=None,seriesNavData=None,coverUrl='')


@pytest.mark.asyncio
async def test_practical_id_preview_queue_library_and_catalog(tmp_path):
    services = ApplicationServices(1, novel_fixture_root=tmp_path/'novels', novel_fixture_source=PinnedNovelSource(Reader(), {}))
    client = Client(ui.page('/_p1_unit'))
    try:
        with client:
            download = NovelDownloadView(services, navigate=lambda _:None)
            download.author_input.set_value('91')
            await download.load_catalog('author')
            assert '1 件' in download.notice.text
            download.work_input.set_value('123')
            download.change_work_id('123')
            assert await download.lookup()
            assert services.jobs.snapshot() == ()
            assert await download.submit()
            def wait():
                with services.jobs._condition:
                    assert services.jobs._condition.wait_for(lambda: services.jobs.snapshot()[0].state == 'done',15)
            await asyncio.to_thread(wait)
            library = NovelLibraryView(services)
            library.set_active(True)
            await library._loading_task
            assert await library.read(library.entries[0])
            assert '漢字《かんじ》' in library.reader_body.text
            assert library.dialog.value
    finally:
        client.delete()
        await asyncio.to_thread(services.close)