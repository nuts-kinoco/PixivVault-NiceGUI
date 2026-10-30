import asyncio
from types import SimpleNamespace
import pytest
from pixivvault_nicegui.viewer_save import ViewerSave

class Services:
    managed_downloads=object()
    def __init__(self):self.calls=[];self.wait=None
    async def save_explicit_work(self,wid,operation,*,is_current):
        self.calls.append(('lookup',wid))
        if self.wait:await self.wait.wait()
        if not is_current():return None
        self.calls.append(('enqueue',wid,operation))
        return SimpleNamespace(job_id='fixture')

def test_explicit_save_reuses_operation_and_existing_admission():
    async def run():
        s=Services();v=ViewerSave(s)
        assert await v.save('123',lambda:True)
        assert await v.save('123',lambda:True)
        enqueues=[c for c in s.calls if c[0]=='enqueue']
        assert len(enqueues)==2 and enqueues[0][2]==enqueues[1][2]
        assert not v.busy
    asyncio.run(run())

def test_late_lookup_cannot_save_different_view():
    async def run():
        s=Services();s.wait=asyncio.Event();v=ViewerSave(s);current=True
        pending=asyncio.create_task(v.save('123',lambda:current))
        await asyncio.sleep(0)
        assert not await v.save('124',lambda:True)
        current=False;s.wait.set()
        assert not await pending
        assert not any(c[0] in ('remember','enqueue') for c in s.calls)
        assert s.calls[-1][0]=='lookup'
    asyncio.run(run())
