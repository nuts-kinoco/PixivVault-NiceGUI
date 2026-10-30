"""Cancellation while a synthetic capacity-cache writer is stalled."""
import asyncio
from threading import Event
import pytest
from pixivvault_nicegui.legacy_capacity import MeasurementLease

@pytest.mark.asyncio
async def test_cancel_does_not_block_ui_when_cache_writer_is_stalled():
    entered,release=Event(),Event()
    class SlowCache:
        def __init__(self):self.saved=[]
        def commit_batch(self,values,revisions,*,decide):
            entered.set();release.wait(5)
            if not decide():return {}
            self.saved.extend(values.items())
            return values
    cache=SlowCache();lease=MeasurementLease()
    writing=asyncio.create_task(asyncio.to_thread(lease.publish,cache,{'12':42},{'12':0}))
    cancellation=None
    try:
        assert await asyncio.to_thread(entered.wait,5)
        cancellation=asyncio.create_task(asyncio.to_thread(lease.cancel))
        # Bounded wait is a deadlock detector for the test, not a product latency target.
        outcome=await asyncio.wait_for(asyncio.shield(cancellation),timeout=1)
        assert outcome==MeasurementLease.CANCELLED
    finally:
        release.set()
        await writing
        if cancellation is not None:await cancellation
    assert cache.saved==[]
