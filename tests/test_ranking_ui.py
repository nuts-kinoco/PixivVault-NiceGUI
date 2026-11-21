"""Actual NiceGUI controls, synthetic fetch, no browser/server/API startup."""
import asyncio
from threading import Event
from nicegui import Client,ui
import pytest
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ranking_source import RankingSource
from pixivvault_nicegui.ui.ranking_view import RankingView
from test_ranking_source import Reader,row

class Pin:
    def __init__(self,records):self.records=records;self.closed=False
    def url(self,wid,page=0,variant='view'):return f'/media/{wid}/{page}/{variant}.png'
    def can_open(self,wid):return True
    def needs_metadata(self,wid):return False
    def close(self):self.closed=True

def setup():
    services=ApplicationServices(1);reader=Reader();services.ranking_source=RankingSource(reader);pins=[]
    def acquire(records):
        pin=Pin(records);pins.append(pin);return pin
    services.acquire_ranking_images=acquire
    return services,reader,pins


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['monthly', 'rookie'])
async def test_unsupported_r18_explanation_survives_successful_period_fetch(mode):
    services,reader,pins=setup();client=Client(ui.page('/_ranking_r18_reason_'+mode))
    try:
        with client:
            view=RankingView(services);view.set_active(True)
            # Unit-test the settled, successful response without scheduling a
            # second concurrent synthetic request from the select callback.
            view._syncing_controls=True
            view.mode.set_value(mode)
            view._syncing_controls=False
            view.changed(refresh=False)
            assert not view.r18.enabled
            await view.load()
            assert view.page.query.mode==mode
            assert 'R-18 指定を利用できません' in view.notice.text
            assert len(pins)==1 and not pins[0].closed
            view.close()
    finally:
        client.delete();services.close()

@pytest.mark.asyncio
async def test_empty_append_sort_refresh_keep_control_lifetime():
    services,reader,pins=setup();client=Client(ui.page('/_ranking_unit'))
    try:
        with client:
            view=RankingView(services);view.set_active(True)
            assert reader.calls==[]
            await view.load()
            first=view.gallery.cards['123'];more=view.gallery.more_button
            assert not more.is_deleted and view.gallery.more_cell.visible
            reader.rows=[row(124)]
            await view.load(append=True)
            assert view.gallery.cards['123'] is first and len(view.gallery.cards)==2
            assert pins[0].closed and not pins[-1].closed
            view.sort.set_value('view_count');view.render()
            assert not view.gallery.more_button.is_deleted
            reader.rows=[]
            await view.load(append=True)
            assert view.page.exhausted and not view.gallery.more_cell.visible
            await view.load()
            assert view.gallery.state.total_count==0 and view.gallery.empty.visible
            assert view.gallery.more_button.enabled
            view.close()
            assert pins[-1].closed
    finally:
        client.delete();services.close()

@pytest.mark.asyncio
async def test_stale_and_closed_results_do_not_publish_images():
    services,reader,pins=setup();client=Client(ui.page('/_ranking_stale'))
    entered,release=Event(),Event()
    original=reader.get_ranking
    def delayed(**kwargs):
        entered.set();assert release.wait(5);return original(**kwargs)
    reader.get_ranking=delayed
    try:
        with client:
            view=RankingView(services);view.set_active(True)
            task=asyncio.create_task(view.load())
            assert await asyncio.to_thread(entered.wait,5)
            view.changed();release.set();await task
            assert pins==[] and view.page is None and not view.busy
            entered.clear();release.clear()
            task=asyncio.create_task(view.load())
            assert await asyncio.to_thread(entered.wait,5)
            view.close();release.set();await task
            assert pins==[] and view.closed
    finally:
        release.set();client.delete();services.close()

@pytest.mark.asyncio
async def test_render_failure_releases_unadopted_catalog():
    services,reader,pins=setup();client=Client(ui.page('/_ranking_failure'))
    try:
        with client:
            view=RankingView(services);view.set_active(True)
            def fail():raise RuntimeError('synthetic render fault')
            view.render=fail
            await view.load()
            assert len(pins)==1 and pins[0].closed and view.pin is None
            assert not view.busy and view.fetch_button.enabled
    finally:client.delete();services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('close_after_select', [False, True])
async def test_delayed_metadata_cannot_replace_newer_selection_or_reopen_closed_view(monkeypatch, close_after_select):
    import pixivvault_nicegui.ui.ranking_view as ranking_module
    services,reader,pins=setup();reader.rows=[row(123),row(124)]
    client=Client(ui.page('/_ranking_late_metadata'))
    entered,release=Event(),Event();tasks=[];create=asyncio.create_task
    monkeypatch.setattr(ranking_module.asyncio, 'create_task', lambda coro: tasks.append(create(coro)) or tasks[-1])
    try:
        with client:
            view=RankingView(services);view.set_active(True);await view.load()
            pin=pins[-1]
            pin.needs_metadata=lambda wid:wid=='123'
            def prepare(wid):
                entered.set();assert release.wait(5)
                return next(a for a in view.gallery.state.artworks if a.work_id==wid)
            pin.prepare_artwork=prepare
            view.gallery.open('123')
            assert await asyncio.to_thread(entered.wait,5)
            view.gallery.open('124')
            assert view.gallery.state.selected_artwork.work_id=='124'
            if close_after_select:view.gallery.close_viewer()
            release.set();await tasks[-1]
            if close_after_select:assert not view.gallery.state.viewer_open
            else:assert view.gallery.state.selected_artwork.work_id=='124'
    finally:
        release.set();client.delete();services.close()


@pytest.mark.asyncio
async def test_same_ranking_query_keeps_pending_metadata_open():
    services, reader, pins = setup()
    reader.rows = [row(123)]
    client = Client(ui.page('/_ranking_pending_search'))
    entered, release = Event(), Event()
    tasks = set()
    try:
        with client:
            view = RankingView(services)
            view.set_active(True)
            await view.load()
            pin = pins[-1]
            pin.needs_metadata = lambda _: True

            def prepare(work_id):
                entered.set()
                assert release.wait(5)
                return next(art for art in view.gallery.state.artworks if art.work_id == work_id)

            pin.prepare_artwork = prepare
            before = asyncio.all_tasks()
            view.gallery.open('123')
            tasks = asyncio.all_tasks() - before
            assert await asyncio.to_thread(entered.wait, 5)
            view.gallery.search(view.gallery.state.query)
            pin.needs_metadata = lambda _: False
            release.set()
            await asyncio.gather(*tasks)
            assert view.gallery.state.selected_id == '123'
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        client.delete()
        services.close()
