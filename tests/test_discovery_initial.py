import asyncio
import pytest
from pixivvault_nicegui.discovery_learning import DiscoveryLearningService
from pixivvault_nicegui.learning_service import LearningService
from pixivvault_nicegui.fixtures import generate_demo_artworks
from test_discovery_learning import Reader

async def test_constructor_no_io_then_configured_startup_initializes_without_manual_click(tmp_path):
    reader=Reader();root=tmp_path/'initial';service=DiscoveryLearningService(root,reader)
    events=[]
    async def context():
        assert service._db is not None
        events.append('context')
    assert reader.calls==[] and not root.exists() and service.initial_load_status=='idle'
    try:
        result=await service.initialize_once(context)
        assert len(result.artworks)==25 and events==['context']
        assert service.initial_load_status=='complete'
        assert await service.initialize_once(context) is result
        assert events==['context'] and sum(isinstance(c,tuple) and c[0]=='ranking' for c in reader.calls)==1
    finally:service.close()

async def test_simultaneous_startup_and_two_tabs_share_context_and_source(tmp_path):
    entered=asyncio.Event();release=asyncio.Event();events=[]
    reader=Reader();service=DiscoveryLearningService(tmp_path/'shared',reader)
    async def context():events.append('context');entered.set();await release.wait()
    try:
        startup=asyncio.create_task(service.initialize_once(context))
        await asyncio.wait_for(entered.wait(),1)
        one=asyncio.create_task(service.initialize_once(context))
        two=asyncio.create_task(service.initialize_once(context))
        await asyncio.sleep(0);one.cancel()
        with pytest.raises(asyncio.CancelledError):await one
        release.set();a,b=await asyncio.wait_for(asyncio.gather(startup,two),2)
        assert a is b and events==['context']
        assert sum(isinstance(c,tuple) and c[0]=='ranking' for c in reader.calls)==1
    finally:release.set();service.close()

async def test_all_initial_claims_stop_before_get_then_new_tab_can_resume(tmp_path):
    entered=asyncio.Event();release=asyncio.Event();events=[];active=[True]
    reader=Reader();service=DiscoveryLearningService(tmp_path/'stopped',reader)
    async def context():events.append('context');entered.set();await release.wait()
    try:
        first=asyncio.create_task(service.initialize_once(context,is_current=lambda:active[0]))
        await asyncio.wait_for(entered.wait(),1);active[0]=False;release.set()
        assert await asyncio.wait_for(first,1) is None
        assert reader.calls==[] and service.initial_load_status=='stopped'
        active[0]=True
        assert (await service.initialize_once(context,is_current=lambda:active[0])).artworks
        assert events==['context','context'] and service.initial_load_status=='complete'
    finally:release.set();service.close()

async def test_no_context_or_get_if_source_database_is_invalid(tmp_path):
    root=tmp_path/'invalid';root.mkdir();(root/'foreign.txt').write_text('fixture')
    reader=Reader();service=DiscoveryLearningService(root,reader);events=[]
    async def context():events.append('context')
    try:
        with pytest.raises(ValueError):await service.initialize_once(context)
        assert events==[] and reader.calls==[] and service.initial_load_status=='failed'
        with pytest.raises(ValueError):await service.initialize_once(context)
        assert events==[]
    finally:service.close()

async def test_initial_source_failure_is_distinct_from_empty_and_is_not_reissued_on_new_tab(tmp_path):
    reader=Reader();reader.fail=True;service=DiscoveryLearningService(tmp_path/'failure',reader)
    try:
        result=await service.initialize_once()
        assert result.nothing_and_failed and service.initial_load_status=='failed'
        assert await service.initialize_once() is result
        assert sum(isinstance(c,tuple) and c[0]=='ranking' for c in reader.calls)==1
        reader.fail=False
        # Explicit retry / active-view restock remains the existing acquisition API.
        assert (await service.fetch_candidates()).artworks
    finally:service.close()

async def test_new_application_lifetime_restarts_initial_load_and_keeps_judgments(tmp_path):
    from pixivvault_nicegui.learning import Judgment
    root=tmp_path/'restart';reader=Reader();service=DiscoveryLearningService(root,reader)
    await service.initialize_once();session=await service.open_session()
    await service.record(session.session_id,Judgment('1','like','saved'))
    service.close()
    service=DiscoveryLearningService(root,reader)
    try:
        result=await service.initialize_once()
        assert '1' not in {a.work_id for a in result.artworks}
        assert (await service.open_session()).judged_ids=={'1'}
        assert sum(isinstance(c,tuple) and c[0]=='ranking' for c in reader.calls)==2
    finally:service.close()

async def test_synthetic_learning_does_not_gain_initial_acquisition(tmp_path):
    service=LearningService(tmp_path/'synthetic',generate_demo_artworks(2))
    try:
        assert not hasattr(service,'initialize_once')
        assert not (await service.open_session()).judged_ids
    finally:service.close()


from contextlib import asynccontextmanager
from types import SimpleNamespace
from nicegui import Client, ui
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.learning_view import LearningView


@asynccontextmanager
async def initial_view(storage, monkeypatch, context):
    tasks=[];pins=[]
    def start(coro,**_):
        task=asyncio.create_task(coro);tasks.append(task);return task
    monkeypatch.setattr('pixivvault_nicegui.ui.learning_view.background_tasks.create',start)
    services=ApplicationServices(count=1)
    services.discovery=storage
    services.refresh_discovery_context=context
    class Pin:
        closed=False
        def close(self):self.closed=True
        def url(self,wid,page,variant):return f'/fixture/{wid}/{page}/{variant}'
    def acquire(_):
        pin=Pin();pins.append(pin);return pin
    services.acquire_ranking_images=acquire
    client=Client(ui.page('/_discovery_initial_fixture'))
    with client,ui.card():view=LearningView(services)
    await asyncio.wait_for(tasks[0],2)
    try:yield view, tasks, pins
    finally:
        view.close()
        await asyncio.wait_for(asyncio.gather(*tasks,return_exceptions=True),2)
        if storage._initial_task is not None:
            await asyncio.wait_for(asyncio.shield(storage._initial_task),2)
        services.discovery=None
        storage.close();services.close();client.delete()


async def test_app_startup_result_attaches_to_first_tab_without_new_get(tmp_path,monkeypatch):
    reader=Reader();service=DiscoveryLearningService(tmp_path/'app-first',reader);events=[]
    async def context():events.append('context')
    await service.initialize_once(context)
    async with initial_view(service,monkeypatch,context) as (view,tasks,pins):
        before=tuple(reader.calls)
        assert not view.active and view._initial_task is None
        view.set_active(True)
        await asyncio.wait_for(view._initial_task,2)
        assert view._initial_done and view._refill.armed and view.state.current_artwork
        assert tuple(reader.calls)==before and events==['context']
        assert not view.state.ready
        view._on_loaded(SimpleNamespace(args=view._image_url))
        assert view.state.ready
        view.set_active(False);view.set_active(True)
        assert view._initial_task is None and tuple(reader.calls)==before


async def test_unshown_view_is_local_only_then_active_tab_initializes(tmp_path,monkeypatch):
    reader=Reader();service=DiscoveryLearningService(tmp_path/'tab-first',reader);events=[]
    async def context():events.append('context')
    async with initial_view(service,monkeypatch,context) as (view,tasks,pins):
        assert reader.calls==[] and events==[] and not pins
        view.set_active(True)
        await asyncio.wait_for(view._initial_task,2)
        assert events==['context'] and view._initial_done and view.state.current_artwork


async def test_close_initial_waiter_prevents_late_source_and_image_install(tmp_path,monkeypatch):
    reader=Reader();service=DiscoveryLearningService(tmp_path/'view-close',reader)
    entered=asyncio.Event();release=asyncio.Event()
    async def context():entered.set();await release.wait()
    async with initial_view(service,monkeypatch,context) as (view,tasks,pins):
        view.set_active(True)
        await asyncio.wait_for(entered.wait(),2)
        waiter=view._initial_task
        view.close();release.set()
        await asyncio.gather(waiter,return_exceptions=True)
        assert await asyncio.wait_for(service._initial_task,2) is None
        assert reader.calls==[] and pins==[] and view.state.closed
        assert service.initial_load_status=='stopped'


@pytest.mark.parametrize('failed',[False,True])
async def test_initial_empty_or_failure_arms_original_active_restock(tmp_path,monkeypatch,failed):
    class EmptyReader(Reader):
        def get_ranking(self,**kwargs):
            self.calls.append(('ranking',kwargs))
            if failed:raise RuntimeError('fixture initial failure')
            return []
    reader=EmptyReader();service=DiscoveryLearningService(tmp_path/'empty',reader)
    async def context():pass
    async with initial_view(service,monkeypatch,context) as (view,tasks,pins):
        waiting=asyncio.Event();delays=[]
        view._refill_clock=lambda:0.0
        async def wait(delay):
            delays.append(delay);waiting.set();await asyncio.Event().wait()
        monkeypatch.setattr(view,'_wait_candidate_refill',wait)
        view.set_active(True)
        await asyncio.wait_for(waiting.wait(),2)
        assert view._initial_done and view._refill.armed
        assert view._refill.rounds==1 and delays==[30]
        assert view._refill.last_failed is failed
        assert service.initial_load_status==('failed' if failed else 'complete')
        assert sum(isinstance(c,tuple) and c[0]=='ranking' for c in reader.calls)==2
        assert view.state.current_artwork is None and pins==[]
        view.set_active(False)
        assert not view._refill.active and view._refill_task is None
