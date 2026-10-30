"""D09/D10 explicit description GET; generated reader responses only."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_discovery_view import discovery_view  # noqa: F401 - isolated synthetic fixture


def token(view):
    item=view.state.current_artwork
    return {'work_id':item.work_id,'author_id':item.author_id,
            'generation':view.state.generation,'summary_epoch':view._summary_epoch}


async def ready(view,storage,tasks):
    await view.fetch_discovery()
    await tasks[-1]
    view.refresh()
    assert view.state.current_artwork is not None
    assert view._summary_state()=='unavailable'


async def test_missing_live_summary_explicit_get_once_and_cache_survives_snapshot(discovery_view):
    view,storage,tasks,_,pins=discovery_view
    await ready(view,storage,tasks)
    item=view.state.current_artwork
    original_meta=deepcopy(storage._metadata[item.work_id])
    original_images=view._images
    calls=[]
    def detail(wid,aid):
        calls.append((wid,aid))
        return {'id':wid,'userId':aid,'description':'取得本文<br><b>続きを表示</b>'}
    storage.reader.get_artwork_metadata_raw=detail
    view._summary_buttons()
    assert view.summary_button.enabled and calls==[]
    assert await view.request_summary(token(view))
    assert calls==[(item.work_id,item.author_id)]
    assert view._summary_open and '取得本文' in view._summary_page_text
    view.close_summary()
    assert view.toggle_summary() and len(calls)==1
    view._summary_descriptions.update({item.work_id:None})
    view.refresh()
    assert view._summary_state()=='ready' and len(calls)==1
    assert view.state.current_artwork==item and view._images is original_images
    assert storage._metadata[item.work_id]==original_meta


async def test_live_summary_pending_dedupe_stale_click_and_tab_departure(discovery_view):
    view,storage,tasks,_,_=discovery_view
    await ready(view,storage,tasks)
    item=view.state.current_artwork
    waiting=asyncio.Event();release=asyncio.Event();calls=[]
    def detail(wid,aid):
        calls.append((wid,aid))
        waiting.set()
        # Thread worker: release is a stdlib event, not an asyncio.Event.
        import time
        while not release.is_set():time.sleep(.01)
        return {'id':wid,'userId':aid,'description':'遅着した本文'}
    storage.reader.get_artwork_metadata_raw=detail
    view._summary_buttons()
    old=token(view)
    pending=asyncio.create_task(view.request_summary(old))
    await asyncio.wait_for(waiting.wait(),2)
    assert await view.request_summary(old) is False
    assert len(calls)==1
    view.set_active(False)
    release.set()
    assert await asyncio.wait_for(pending,3) is False
    assert not view._summary_open and (item.work_id,item.author_id) not in view._summary_fetched
    view.set_active(True)
    assert await view.request_summary(old) is False


@pytest.mark.parametrize('raw,should_be_empty',[
    ({'description':None},False),({},False),({'description':26},False),
    ({'description':'x'*131073},False),({'description':''},True),
    ({'description':'<br> '},True),
    ({'description':'本文を復元'},None),
])
async def test_live_summary_unknown_invalid_empty_and_explicit_retry(discovery_view,raw,should_be_empty):
    view,storage,tasks,_,_=discovery_view
    await ready(view,storage,tasks)
    item=view.state.current_artwork
    calls=[]
    def detail(wid,aid):
        calls.append(1)
        return {'id':wid,'userId':aid,**raw}
    storage.reader.get_artwork_metadata_raw=detail
    view._summary_buttons()
    result=await view.request_summary(token(view))
    assert len(calls)==1
    if should_be_empty is True:
        assert result and view._summary_state()=='none' and not view.summary_button.enabled
    elif should_be_empty is False:
        assert not result and view._summary_state()=='failed' and view.summary_button.enabled
        storage.reader.get_artwork_metadata_raw=lambda wid,aid: (
            calls.append(1) or {'id':wid,'userId':aid,'description':'正常に再試行'})
        assert await view.request_summary(token(view))
        assert len(calls)==2 and view._summary_state()=='ready'
    else:
        assert result and view._summary_state()=='ready'


async def test_live_summary_identity_error_no_metadata_mutation_and_old_work_rejected(discovery_view):
    view,storage,tasks,_,_=discovery_view
    await ready(view,storage,tasks)
    old=token(view);before=deepcopy(storage._metadata)
    calls=[]
    def bad_identity(wid,aid):
        calls.append((wid,aid))
        return {'id':wid,'userId':'9999999','description':'異なる作者の本文'}
    storage.reader.get_artwork_metadata_raw=bad_identity
    view._summary_buttons()
    assert not await view.request_summary(old)
    assert view._summary_state()=='failed' and storage._metadata==before
    view.state.index+=1
    view.state.generation+=1
    view.refresh()
    assert not await view.request_summary(old)
    assert len(calls)==1 and storage._metadata==before


async def test_d09_browser_payload_fences_old_click_and_move_during_get(discovery_view):
    from threading import Event
    view,storage,tasks,_,_=discovery_view
    await ready(view,storage,tasks)
    current=token(view)
    calls=[]
    started=Event();release=Event()
    def detail(wid,aid):
        calls.append((wid,aid))
        started.set()
        release.wait(timeout=3)
        return {'id':wid,'userId':aid,'description':'旧作品の非同期本文'}
    storage.reader.get_artwork_metadata_raw=detail
    view._summary_buttons()
    wrong={**current,'author_id':'777'}
    view._on_summary_click(SimpleNamespace(args={**wrong,'width':1280,'height':720}))
    await asyncio.sleep(0)
    assert calls==[] and not view._summary_open
    pending=asyncio.create_task(view.request_summary(current))
    assert await asyncio.to_thread(started.wait,2)
    # Selection changes while the explicit, bounded reader is still in flight.
    view.state.index+=1
    view.state.generation+=1
    view.refresh()
    release.set()
    assert await asyncio.wait_for(pending,3) is False
    assert not view._summary_open and not view._summary_fetched
    assert view.state.current_artwork.work_id != current['work_id']
    assert len(calls)==1


async def test_d09_explicit_http_error_retry_and_cached_text_not_refetched(discovery_view):
    view,storage,tasks,_,_=discovery_view
    await ready(view,storage,tasks)
    item=view.state.current_artwork
    calls=[]
    def detail(wid,aid):
        calls.append(1)
        if len(calls)==1:raise RuntimeError('synthetic HTTP 503 with unsafe details')
        return {'id':wid,'userId':aid,'description':'<b>安全な復旧本文</b>'}
    storage.reader.get_artwork_metadata_raw=detail
    view._summary_buttons()
    first=token(view)
    assert not await view.request_summary(first)
    assert view._summary_state()=='failed' and 'HTTP' not in view.summary_status.text
    assert await view.request_summary(first)
    assert len(calls)==2
    assert view._summary_state()=='ready' and '安全な復旧本文' in view._summary_page_text
    view.close_summary()
    assert view.toggle_summary() and len(calls)==2
    assert view.state.current_artwork.work_id==item.work_id
