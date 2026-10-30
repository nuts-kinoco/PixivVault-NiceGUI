import asyncio
from types import SimpleNamespace
import pytest
from nicegui import Client,ui
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.learning_view import LearningView
from pixivvault_nicegui.discovery_learning import DiscoveryLearningService
from test_discovery_learning import Reader

@pytest.fixture
async def discovery_view(tmp_path,monkeypatch):
    tasks=[]
    def start(coro,**_):
        task=asyncio.create_task(coro);tasks.append(task);return task
    monkeypatch.setattr('pixivvault_nicegui.ui.learning_view.background_tasks.create',start)
    services=ApplicationServices(count=1)
    storage=DiscoveryLearningService(tmp_path/'view',Reader())
    services.discovery=storage
    services.managed_downloads=SimpleNamespace()
    pins=[]
    class Pin:
        closed=False
        def close(self):self.closed=True
        def url(self,wid,page,variant):return f'/fixture/{wid}/{page}/{variant}'
    def acquire(_):
        pin=Pin();pins.append(pin);return pin
    services.acquire_ranking_images=acquire
    calls=[]
    async def context():calls.append('context')
    services.refresh_discovery_context=context
    async def save(wid,operation,*,is_current):
        if not is_current():return None
        calls.append(('enqueue',wid,operation));return SimpleNamespace(job_id='fixture-job')
    services.save_explicit_work=save
    client=Client(ui.page('/_discovery_view_fixture'))
    with client,ui.card():view=LearningView(services)
    view.set_active(True)
    await tasks[0]
    if view._initial_task is not None:await asyncio.wait_for(view._initial_task,2)
    try:yield view,storage,tasks,calls,pins
    finally:
        view.close()
        await asyncio.wait_for(asyncio.gather(*tasks,return_exceptions=True),timeout=3)
        services.discovery=None;services.managed_downloads=None
        storage.close();services.close();client.delete()

async def test_configured_discovery_auto_initial_context_source_and_explicit_reload(discovery_view):
    view,storage,tasks,calls,pins=discovery_view
    assert calls==['context'] and storage.initial_load_status=='complete'
    assert view.state.current_artwork is not None and view._initial_done
    initial=sum(isinstance(c,tuple) and c[0]=='ranking' for c in storage.reader.calls)
    assert initial==1
    await view.fetch_discovery();await tasks[-1]
    assert calls==['context','context'] and view.state.current_artwork is not None
    assert sum(isinstance(c,tuple) and c[0]=='ranking' for c in storage.reader.calls)==2
    assert view._image_url.startswith('/fixture/')
    assert not view.state.ready


async def test_summary_uses_only_already_cached_candidate_description(discovery_view):
    view,storage,tasks,calls,pins=discovery_view
    await view.fetch_discovery();await tasks[-1]
    item=view.state.current_artwork
    assert item is not None
    assert view.summary_button.text=='概要 未取得'
    assert not view.summary_button.enabled and not view.toggle_summary()
    before=list(storage.reader.calls)
    # Simulate an already-received GET field in the managed in-memory candidate
    # cache. This does not invoke a Pixiv reader, write original data or
    # manufacture text for an unknown description.
    def publish_cached_description():
        candidate=dict(storage._candidates[item.work_id])
        candidate['description']='既存応答の文章<br>続きです'
        storage._accept_metadata(candidate)
    await storage._wait(storage._submit(publish_cached_description))
    known=await storage.description_snapshot()
    assert known[item.work_id]=='既存応答の文章<br>続きです'
    view._summary_descriptions.update(known)
    view.refresh()
    assert view.summary_button.enabled
    assert view.toggle_summary() and view._summary_open
    assert '既存応答の文章' in view._summary_page_text
    assert storage.reader.calls==before
    view.toggle_trend()
    assert view._trend_open and not view._summary_open
    assert view.toggle_summary()
    assert view._summary_open and not view._trend_open
    view.close_summary()


async def test_summary_retry_status_is_visible_and_stale_completion_discarded(
        discovery_view,monkeypatch):
    view,storage,tasks,calls,pins=discovery_view
    await view.fetch_discovery();await tasks[-1]
    item=view.state.current_artwork
    view.set_summary_failed('fixture local failure')
    assert view.summary_button.text=='概要 取り直す'
    entered=asyncio.Event();release=asyncio.Event()
    async def delayed():
        entered.set()
        await release.wait()
        return {item.work_id:'遅延して返るキャッシュ済み本文'}
    monkeypatch.setattr(storage,'description_snapshot',delayed)
    task=asyncio.create_task(view.retry_summary())
    await asyncio.wait_for(entered.wait(),2)
    assert view.summary_button.text=='概要 取得中'
    assert not view.summary_button.enabled
    view.set_active(False)
    release.set()
    assert await asyncio.wait_for(task,2) is False
    assert not view._summary_retrying
    assert not view._summary_open
    assert item.work_id in view._summary_failed
    view._on_loaded(SimpleNamespace(args=view._image_url))
    assert view.state.ready

async def test_stale_save_rejected_and_acceptance_is_not_saved(discovery_view):
    view,storage,tasks,calls,pins=discovery_view
    await view.fetch_discovery();await tasks[-1]
    wid=view.state.current_artwork.work_id
    view._on_managed_save(SimpleNamespace(args={'work_id':wid,'generation':view.state.generation-1}))
    assert not any(isinstance(c,tuple) and c[0]=='lookup' for c in calls)
    await view.save_managed(wid)
    await view.save_managed(wid)
    enqueues=[c for c in calls if isinstance(c,tuple) and c[0]=='enqueue']
    assert len(enqueues)==2 and enqueues[0][2]==enqueues[1][2]
    assert not (await storage.open_session()).judged_ids
    assert 'キュー' in view.status.text

async def test_close_during_source_read_does_not_install_images(discovery_view,monkeypatch):
    view,storage,tasks,calls,pins=discovery_view
    entered=asyncio.Event();release=asyncio.Event()
    original=storage.fetch_candidates
    async def delayed(**kwargs):
        entered.set();await release.wait();return await original(**kwargs)
    monkeypatch.setattr(storage,'fetch_candidates',delayed)
    pin_count=len(pins)
    pending=asyncio.create_task(view.fetch_discovery());await entered.wait()
    view.close();release.set();await pending
    assert len(pins)==pin_count and view.state.closed

async def test_judgment_reranks_tail_but_retains_last_undo(discovery_view):
    view,storage,tasks,calls,pins=discovery_view
    await view.fetch_discovery();await tasks[-1]
    first=view.state.current_artwork.work_id
    view._on_loaded(SimpleNamespace(args=view._image_url))
    assert not view.trend_content.visible
    view.toggle_trend()
    assert view.trend_content.visible and view.trend_button.props['aria-expanded']=='true'
    assert view.judge('like',{'work_id':first,'generation':view.state.generation,'operation_id':'judge-fixture'})
    await tasks[-1]
    assert not view.trend_content.visible and view.trend_button.props['aria-expanded']=='false'
    assert view.state.can_undo and view.state.index==1
    assert view.state.artworks[0].work_id==first
    row=(await storage.review_snapshot(view._session_id))['rows'][0]
    assert row['thumbnail_url'].startswith('https://i.pximg.net/')
    view._on_undo(SimpleNamespace(args={'work_id':view.state.current_artwork.work_id,'generation':view.state.generation,'operation_id':'undo-fixture'}))
    await tasks[-1]
    assert view.state.current_artwork.work_id==first

async def test_discovery_settings_dialog_persists_and_detects_conflict(discovery_view):
    view,storage,tasks,calls,pins=discovery_view
    await view.open_discovery_settings()
    assert view._settings_snapshot.values['discovery_search_mode']=='all'
    view.settings_mode.set_value('r18')
    view.settings_subject.set_value(10)
    view.settings_style.set_value(15)
    await view.save_discovery_settings();await tasks[-1]
    assert (await storage.settings_snapshot()).values['discovery_search_mode']=='r18'
    assert calls==['context'] and sum(isinstance(c,tuple) and c[0]=='ranking' for c in storage.reader.calls)==1
    other=await storage.settings_snapshot()
    values=dict(other.values,discovery_search_mode='safe')
    await storage._wait(storage.submit_settings_update(other.revision,values,'other-view'))
    view.settings_mode.set_value('all')
    await view.save_discovery_settings()
    assert '別の画面' in view.settings_status.text
    assert (await storage.settings_snapshot()).values['discovery_search_mode']=='safe'

async def test_close_during_lookup_never_repins_view(discovery_view,monkeypatch):
    view,storage,tasks,calls,pins=discovery_view
    await view.fetch_discovery();await tasks[-1]
    entered=asyncio.Event();release=asyncio.Event();remembered=[];released=[]
    async def save(wid,operation,*,is_current):
        entered.set();await release.wait()
        assert not is_current()
        return None
    monkeypatch.setattr(view.services,'save_explicit_work',save)
    monkeypatch.setattr(view.services,'remember_managed_work_id',lambda *args:remembered.append(args))
    monkeypatch.setattr(view.services,'release_managed_owner',lambda owner:released.append(owner))
    pending=asyncio.create_task(view.save_managed(view.state.current_artwork.work_id))
    await entered.wait();view.close()
    assert released==[]
    release.set();await pending
    assert remembered==[] and released==[view._managed_owner]

async def test_prefetch_lifecycle_only_after_current_decode_and_stop_on_hide(discovery_view,monkeypatch):
    view,storage,tasks,calls,pins=discovery_view
    await view.fetch_discovery();await tasks[-1]
    events=[]
    class Prefetch:
        def __init__(self,prefs,pin,*,on_ready):self.running=False;events.append('create')
        def configure(self):events.append('configure')
        def set_queue(self,ids):events.append(('queue',tuple(ids)))
        async def run(self):events.append('run')
        def stop(self):events.append('stop')
        def close(self):events.append('close')
    monkeypatch.setattr('pixivvault_nicegui.learning_prefetch.LearningImagePrefetch',Prefetch)
    view.services.preferences=object()
    view.services.images.shared_cache=object()
    try:
        assert events==[]
        view._on_loaded(SimpleNamespace(args=view._image_url))
        await tasks[-1]
        assert 'run' in events and view.state.ready
        view.set_active(False)
        assert events[-1]=='stop'
        view.close()
        assert 'close' in events
    finally:
        view.services.preferences=None
        view.services.images.shared_cache=None


def refill_packet(storage,work_id='100'):
    from dataclasses import replace
    from pixivvault_nicegui.discovery_learning import DiscoveryBatch
    records=storage.candidate_records()
    record=replace(records[0],remote_id=work_id,title='refill fixture')
    return DiscoveryBatch((record.as_artwork(),),(record,),{work_id:'style'},('ranking',),()),records+(record,)

async def test_refill_append_keeps_visible_image_undo_prefix_and_retires_old_pin(discovery_view):
    view,storage,tasks,calls,pins=discovery_view
    await view.fetch_discovery();await tasks[-1]
    view._on_loaded(SimpleNamespace(args=view._image_url))
    first=view.state.current_artwork.work_id
    assert view.judge('like',{'work_id':first,'generation':view.state.generation,'operation_id':'append-before'})
    await view._save_task
    view._on_loaded(SimpleNamespace(args=view._image_url))
    before=(view.state.current_artwork,view.state.index,view.state.generation,view._image_url,view.state.artworks[:view.state.index])
    old_pin=view._discovery_pin
    packet,records=await asyncio.to_thread(refill_packet,storage)
    assert view._append_refill(packet,records,view._refill_epoch)==1
    assert before==(view.state.current_artwork,view.state.index,view.state.generation,view._image_url,view.state.artworks[:view.state.index])
    assert view.state.ready and view.state.can_undo and not old_pin.closed
    assert view._append_refill(packet,records,view._refill_epoch)==0
    view.request_image()
    assert old_pin.closed
    view._on_loaded(SimpleNamespace(args=before[3]))
    assert not view.state.ready

async def test_late_refill_does_not_append_after_tab_leave(discovery_view):
    view,storage,tasks,calls,pins=discovery_view
    await view.fetch_discovery();await tasks[-1]
    epoch=view._refill_epoch;count=len(view.state.artworks);pin_count=len(pins)
    packet,records=await asyncio.to_thread(refill_packet,storage)
    view.set_active(False)
    assert view._append_refill(packet,records,epoch)==0
    assert len(view.state.artworks)==count and len(pins)==pin_count

async def test_automatic_topup_and_exhausted_restock_use_original_intervals_without_real_sleep(discovery_view,monkeypatch):
    view,storage,tasks,calls,pins=discovery_view
    await view.fetch_discovery();await tasks[-1]
    clock=[0.0];waits=[]
    view._refill_clock=lambda:clock[0]
    async def advance(delay):
        waits.append(delay);clock[0]+=delay;await asyncio.sleep(0)
    monkeypatch.setattr(view,'_wait_candidate_refill',advance)
    view._refill.arm(0)
    view.state.index=len(view.state.artworks)-5
    view.refresh()
    await asyncio.wait_for(view._refill_task,2)
    assert view._refill.empty_topups==3 and not view._refill.gave_up
    assert waits==[2,2]
    assert sum(isinstance(c,tuple) and c[0]=='ranking' for c in storage.reader.calls)==5
    view.state.index=len(view.state.artworks)
    view.refresh()
    await asyncio.wait_for(view._refill_task,3)
    assert waits==[2,2]+[30]*20
    assert view._refill.rounds==20 and view._refill.gave_up
    assert sum(isinstance(c,tuple) and c[0]=='ranking' for c in storage.reader.calls)==25
    assert '停止' in view.refill_status.text and '候補を取得' in view.refill_status.text
    assert view.title.text=='現在の候補がありません'
    assert calls==['context','context']  # Automatic refill does not repeatedly GET following context.

async def test_append_while_post_judgment_ranking_waits_cannot_be_overwritten(discovery_view,monkeypatch):
    view,storage,tasks,calls,pins=discovery_view
    await view.fetch_discovery();await tasks[-1]
    entered=asyncio.Event();release=asyncio.Event();original=storage.rank_pending
    async def delayed(*args):
        result=await original(*args);entered.set();await release.wait();return result
    monkeypatch.setattr(storage,'rank_pending',delayed)
    view._on_loaded(SimpleNamespace(args=view._image_url))
    first=view.state.current_artwork.work_id
    assert view.judge('like',{'work_id':first,'generation':view.state.generation,'operation_id':'rank-race'})
    await asyncio.wait_for(entered.wait(),1)
    packet,records=await asyncio.to_thread(refill_packet,storage)
    assert view._append_refill(packet,records,view._refill_epoch)==1
    release.set();await view._save_task
    assert view.state.artworks[-1].work_id=='100' and view.state.artworks[0].work_id==first
    assert view.state.can_undo

async def test_full_image_cache_blocks_positive_topup_but_not_exhausted_restock(discovery_view,monkeypatch):
    view,storage,tasks,calls,pins=discovery_view
    await view.fetch_discovery();await tasks[-1]
    monkeypatch.setattr(view._discovery_pin,'prefetch_stats',lambda:{'full':True},raising=False)
    view.state.index=len(view.state.artworks)-5
    initial=len(storage.reader.calls)
    view.refresh()
    assert view._refill_task is None and len(storage.reader.calls)==initial
    assert 'キャッシュ' in view.refill_status.text
    clock=[0.0];view._refill_clock=lambda:clock[0]
    async def advance(delay):clock[0]+=delay;await asyncio.sleep(0)
    monkeypatch.setattr(view,'_wait_candidate_refill',advance)
    view.state.index=len(view.state.artworks);view.refresh()
    await asyncio.wait_for(view._refill_task,3)
    assert view._refill.rounds==20 and view._refill.gave_up
    assert len(storage.reader.calls)>initial

async def test_automatic_result_while_inactive_is_discarded_and_resume_reconciles_cache(discovery_view,monkeypatch):
    view,storage,tasks,calls,pins=discovery_view
    await view.fetch_discovery();await tasks[-1]
    entered=asyncio.Event();release=asyncio.Event()
    original=storage.fetch_candidates
    async def delayed(**kwargs):
        entered.set()
        try:await release.wait()
        except asyncio.CancelledError:
            # Simulate an app-owned accepted acquisition completing after its
            # view has stopped; the UI epoch must still reject the old result.
            await release.wait()
        return await original(**kwargs)
    monkeypatch.setattr(storage,'fetch_candidates',delayed)
    view.state.index=len(view.state.artworks)-5
    view.refresh();old=view._refill_task
    await asyncio.wait_for(entered.wait(),1)
    count=len(view.state.artworks);pin_count=len(pins);before=len(storage.reader.calls)
    view.set_active(False);release.set()
    await asyncio.wait_for(old,2)
    assert len(view.state.artworks)==count and len(pins)==pin_count
    assert len(storage.reader.calls)==before  # queued stale claim makes no GET
    assert not view._refill.active
