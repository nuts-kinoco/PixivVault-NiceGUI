import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
import pytest
from pixivvault_nicegui.learning_prefetch import LearningImagePrefetch
from pixivvault_nicegui.shared_image_cache import SharedImageCache
from pixivvault_nicegui.library_media_registry import LibraryImageRegistry
from pixivvault_nicegui.media import DemoImageCatalog
from pixivvault_nicegui.remote_media import RemoteBookmarkImages
from test_shared_image_cache import png,record,blob


def prefs(mode='count',limit='2'):
    return SimpleNamespace(snapshot=lambda:SimpleNamespace(values={'discovery_cache_limit_mode':mode,'discovery_cache_size_count':limit,'discovery_cache_size_mb':limit}))


def fixture_pin(client):
    cache=SharedImageCache(max_bytes=10000)
    registry=LibraryImageRegistry(DemoImageCatalog([]),shared_cache=cache)
    provider=RemoteBookmarkImages(client,tuple(record(str(i)) for i in range(1,6)))
    pin=registry.acquire_catalog(provider)
    return cache,registry,provider,pin


@pytest.mark.asyncio
async def test_prefetch_consumes_original_count_limit_without_candidate_get():
    calls=[]
    client=SimpleNamespace(get_image_bytes=lambda url:(calls.append(url),png('red'))[1],get_artwork_pages=lambda wid:(record(wid).thumbnail,))
    cache,registry,provider,pin=fixture_pin(client)
    ready=[];worker=LearningImagePrefetch(prefs(),pin,on_ready=ready.append)
    try:
        worker.set_queue(['mock-1','1','2','3','4'])
        result=await worker.run()
        assert result.status=='full' and result.fetched==2
        assert len(calls)==2 and ready==['1','2']
        assert pin.prefetch_stats()['entries']==2
        assert cache.stats()['bytes']==pin.prefetch_stats()['bytes']
        # Foreground and prefetch share exactly the same bytes, not a shadow buffer.
        assert pin.get('1').data==png('red') and len(calls)==2
        pin.get('3')
        assert pin.peek('1') is None and pin.peek('2') is not None
        assert pin.prefetch_stats()['entries']==2
    finally:worker.close();pin.close();registry.close();cache.close()


def test_fifo_order_is_not_lru_and_bytes_budget_is_real():
    image=blob('red');cache=SharedImageCache(max_bytes=10000);space=cache.namespace()
    space.configure_fifo('bytes',len(image.data)*2)
    space.put(('1',0,'view'),image);space.put(('2',0,'view'),image)
    space.get(('1',0,'view')) # touching first does not change the adopted FIFO order
    space.put(('3',0,'view'),image)
    assert space.get(('1',0,'view')) is None
    assert space.prefetch_stats()['bytes']==2*len(image.data)
    space.configure_fifo('count',1)
    assert space.prefetch_stats()['entries']==1
    assert cache.stats()['bytes']==len(image.data)
    cache.close()


@pytest.mark.asyncio
async def test_stop_during_pending_read_rejects_ui_delivery_and_no_next_read():
    started=Event();release=Event();calls=[]
    def read(url):calls.append(url);started.set();assert release.wait(3);return png('red')
    client=SimpleNamespace(get_image_bytes=read,get_artwork_pages=lambda wid:(record(wid).thumbnail,))
    cache,registry,provider,pin=fixture_pin(client);ready=[]
    worker=LearningImagePrefetch(prefs(),pin,on_ready=ready.append);worker.set_queue(['1','2'])
    try:
        task=asyncio.create_task(worker.run())
        assert await asyncio.to_thread(started.wait,3)
        worker.stop();release.set();result=await task
        assert result.status=='stopped' and len(calls)==1 and ready==[]
    finally:release.set();worker.close();pin.close();registry.close();cache.close()


@pytest.mark.asyncio
async def test_first_image_failure_stops_run_without_retry_or_next_api():
    calls=[]
    def fail(url):calls.append(url);raise RuntimeError('fixture')
    client=SimpleNamespace(get_image_bytes=fail,get_artwork_pages=lambda wid:(record(wid).thumbnail,))
    cache,registry,provider,pin=fixture_pin(client);worker=LearningImagePrefetch(prefs(),pin)
    try:
        worker.set_queue(['1','2'])
        result=await worker.run()
        assert result.status=='failed' and result.failed==1 and len(calls)==1
        assert pin.prefetch_stats()['entries']==0
    finally:worker.close();pin.close();registry.close();cache.close()


def test_foreground_and_prefetch_singleflight_share_one_transport():
    entered=Event();release=Event();calls=[]
    def read(url):calls.append(url);entered.set();assert release.wait(3);return png('red')
    client=SimpleNamespace(get_image_bytes=read,get_artwork_pages=lambda wid:(record(wid).thumbnail,))
    cache,registry,provider,pin=fixture_pin(client)
    try:
        with ThreadPoolExecutor() as pool:
            first=pool.submit(pin.get,'1');assert entered.wait(3)
            second=pool.submit(pin.get,'1');release.set()
            assert first.result(3)==second.result(3)
        assert len(calls)==1
    finally:release.set();pin.close();registry.close();cache.close()
