import asyncio
import pytest
from nicegui import Client, core, ui
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.library_view import LibraryView
from test_managed_download_services import FakeSource


@pytest.mark.asyncio
@pytest.mark.parametrize('first_write_succeeds,second_write_succeeds,first_still_saved', [
    (False, True, False), (True, False, True), (True, True, False)])
async def test_each_view_uses_its_own_bookmark_persistence(tmp_path, monkeypatch, first_write_succeeds, second_write_succeeds, first_still_saved):
    class Source(FakeSource):
        work_id = '202'
        def get_bookmarked_works(self, identity, rest_type):
            if rest_type == 'show':
                yield {'id': self.work_id, 'title': 'fixture', 'user_id': '23',
                       'user_name': 'fixture', 'tags': [], 'type': 0, 'page_count': 1,
                       'thumb_url': f'https://i.pximg.net/synthetic/{self.work_id}.png'}

    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    source = Source(); services = ApplicationServices(live_client=source, preferences_root=tmp_path/'prefs')
    clients = [Client(ui.page('/_d5_reaudit_' + str(i) + str(first_write_succeeds))) for i in range(2)]
    views = []
    try:
        for client in clients:
            with client:
                view = LibraryView(services); view.subview = 'bookmarks'
                view.bookmarks.set_active(True); views.append(view)
        real_write = services._bookmark_cache_store.write
        outcome = [first_write_succeeds]
        monkeypatch.setattr(services._bookmark_cache_store, 'write',
                            lambda *a, **k: real_write(*a, **k) if outcome[0] else False)
        async def refresh(view):
            epoch = view.bookmark_state.begin_fetch()
            await view._refresh_bookmarks_async(epoch)
        with clients[0]: await refresh(views[0])
        assert {x.work_id for x in views[0].bookmark_state.items} == {'202'}
        source.work_id = '204'; outcome[0] = second_write_succeeds
        with clients[1]: await refresh(views[1])
        source.fail = True
        with clients[0]: await refresh(views[0])
        assert {x.work_id for x in views[0].bookmark_state.items} == {'202'}
        assert ('保存済み' in views[0].bookmarks.message.text) is first_still_saved
        assert ('保存を確認できません' in views[0].bookmarks.persistence_status.text) is (not first_still_saved)
    finally:
        for view in views: view.close()
        for client in clients: client.delete()
        services.close()


def test_queue_log_heading_has_serif_and_individual_expectation():
    from pixivvault_nicegui.ui.job_views import QueueView
    from tools.ui_smoke_helpers import load_expectations
    from types import SimpleNamespace
    client = Client(ui.page('/_d5_reaudit_log'))
    try:
        with client:
            view = QueueView(SimpleNamespace(jobs=SimpleNamespace(snapshot=lambda: (), faulted=False), artworks=()))
            try:
                heading = next(e for e in view.log_heading.descendants() if getattr(e, 'text', None) == 'ダウンロードキューログ')
                assert 'pv-serif-heading' in heading._classes
                assert load_expectations()['screens']['queue']['text_fonts']['[data-testid=queue-log-heading] .pv-library-heading'] == 'Yu Mincho'
            finally: view.close()
    finally: client.delete()


def test_font_audit_requires_serif_for_queue_log_independently_of_class():
    from tools.p5_design_evidence import mechanics, LIGHT_TOKENS
    from tools import shell_measure
    selector = '[data-testid=queue-log-heading] .pv-library-heading'
    port = {'tokens':LIGHT_TOKENS, 'width':[1280,1280], 'display_contract':{'queue_list_height':40, 'queue_empty_alignment':None},
            'clipped':[], 'shell':{'tabs':[]}, 'styles':[], 'text':[], 'background_pixel':shell_measure.ORIG['bg']['light'],
            'rendered_fonts':{selector:[{'familyName':'Noto Sans JP'}]}}
    assert any('font' in flag for flag in mechanics({'port':port}, 'light', 'queue'))
