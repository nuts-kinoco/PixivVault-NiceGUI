import asyncio
from threading import Event
import pytest
from pixivvault_nicegui.discovery_refill import DiscoveryRefill
from pixivvault_nicegui.discovery_learning import DiscoveryLearningService
from test_discovery_learning import Reader


def test_no_implicit_arm_and_exact_lookahead_threshold():
    policy=DiscoveryRefill()
    assert policy.delay(0,0) is None and not policy.begin(0,0)
    policy.arm(10)
    assert policy.delay(10,10) is None
    assert policy.delay(9,10)==0
    assert policy.begin(9,10)
    assert not policy.begin(9,10)
    policy.complete(0,False,10)
    assert policy.delay(9,10)==2
    assert not policy.begin(9,11.999)
    assert policy.begin(9,12)


def test_three_empty_topups_then_wait_until_exhaustion():
    policy=DiscoveryRefill();policy.arm(0)
    for index in range(3):
        assert policy.begin(3,index*2)
        policy.complete(0,False,index*2)
    assert policy.empty_topups==3
    assert policy.delay(2,1000) is None
    assert policy.delay(1,1001) is None
    assert policy.delay(0,1002)==0
    assert policy.begin(0,1002) and policy.phase=='restock'
    policy.complete(0,False,1002)
    assert policy.delay(0,1002)==30


def test_twenty_restock_rounds_thirty_seconds_then_visible_stop_and_resume():
    policy=DiscoveryRefill();policy.arm(0)
    for index in range(20):
        assert policy.begin(0,index*30)
        policy.complete(0,index%2==0,index*30)
        assert policy.rounds==index+1 and not policy.gave_up
        assert policy.delay(0,index*30)==30
    assert not policy.begin(0,599.999)
    assert not policy.begin(0,600) and policy.gave_up
    assert policy.failed_rounds==10
    assert policy.delay(0,900) is None
    policy.pause();policy.resume(901)
    assert policy.begin(0,901) and policy.rounds==0 and not policy.gave_up


def test_undo_recovery_stops_restock_and_success_resets_empty_attempts():
    policy=DiscoveryRefill();policy.arm(0)
    assert policy.begin(0,0)
    policy.complete(0,True,0)
    assert policy.delay(11,1) is None and policy.phase=='topup'
    policy.pause()
    assert policy.delay(0,999) is None
    policy.resume(1000)
    assert policy.begin(4,1000)
    policy.complete(6,False,1001)
    assert policy.empty_topups==0 and policy.rounds==0 and not policy.last_failed
    assert policy.delay(10,1001) is None

async def test_concurrent_views_share_one_source_fetch_and_cancelled_waiter_does_not_cancel(tmp_path):
    entered=Event();release=Event()
    class BlockingReader(Reader):
        def get_ranking(self,**kwargs):
            entered.set();assert release.wait(2)
            return super().get_ranking(**kwargs)
    reader=BlockingReader();service=DiscoveryLearningService(tmp_path/'singleflight',reader)
    try:
        one=asyncio.create_task(service.fetch_candidates())
        assert await asyncio.to_thread(entered.wait,1)
        two=asyncio.create_task(service.fetch_candidates())
        await asyncio.sleep(0)
        one.cancel()
        with pytest.raises(asyncio.CancelledError):await one
        release.set();result=await asyncio.wait_for(two,2)
        assert result.artworks and sum(isinstance(call,tuple) and call[0]=='ranking' for call in reader.calls)==1
    finally:release.set();service.close()

async def test_paused_queued_acquisition_makes_no_get(tmp_path):
    entered=Event();release=Event();reader=Reader();service=DiscoveryLearningService(tmp_path/'paused',reader)
    live=[True]
    def block():entered.set();assert release.wait(2)
    try:
        blocker=service._submit(block)
        assert await asyncio.to_thread(entered.wait,1)
        pending=asyncio.create_task(service.fetch_candidates(is_current=lambda:live[0]))
        await asyncio.sleep(0);live[0]=False;release.set()
        assert await asyncio.wait_for(pending,2) is None
        assert reader.calls==[]
        await service._wait(blocker)
    finally:release.set();service.close()

async def test_stop_during_accepted_batch_prevents_next_source_get(tmp_path):
    live=[True]
    class StopReader(Reader):
        def get_user_works(self,user_id,**kwargs):
            result=super().get_user_works(user_id,**kwargs)
            live[0]=False
            return result
    reader=StopReader();service=DiscoveryLearningService(tmp_path/'stop-source',reader,following=[{'user_id':'12','name':'fixture'}])
    try:
        await service.fetch_candidates(is_current=lambda:live[0])
        assert [c[0] for c in reader.calls if isinstance(c,tuple)]==['following']
    finally:service.close()
