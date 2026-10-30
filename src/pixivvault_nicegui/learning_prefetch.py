"""Sequential image look-ahead for an already accepted discovery queue.

Original discovery_tab FIFO bytes/count policy; this module never discovers
new works, polls a source or retries an unsuccessful image in the same run.
"""
from __future__ import annotations
import asyncio
from dataclasses import dataclass
import inspect


@dataclass(frozen=True)
class PrefetchResult:
    status: str
    fetched: int=0
    failed: int=0


class LearningImagePrefetch:
    def __init__(self,preferences,pin,*,on_ready=None):
        self.preferences=preferences;self.pin=pin;self.on_ready=on_ready
        self.queue=();self.active=False;self.closed=False;self.running=False
        self._generation=0

    def configure(self):
        values=self.preferences.snapshot().values
        mode=values['discovery_cache_limit_mode']
        limit=int(values['discovery_cache_size_count']) if mode=='count' else int(values['discovery_cache_size_mb'])*1024*1024
        self.pin.configure_prefetch(mode,limit)

    def set_queue(self,work_ids):
        if self.closed:return
        self.queue=tuple(dict.fromkeys(wid for wid in work_ids if isinstance(wid,str) and wid and not wid.startswith('mock-')))
        self.active=True

    def stop(self):
        self.active=False;self._generation+=1

    def close(self):
        self.stop();self.closed=True;self.queue=()

    async def run(self):
        if self.running:return PrefetchResult('busy')
        if self.closed or not self.active:return PrefetchResult('stopped')
        self.running=True;fetched=failed=0;attempted=set()
        try:
            while not self.closed and self.active:
                self.configure()
                if self.pin.prefetch_stats()['full']:return PrefetchResult('full',fetched,failed)
                target=None
                for wid in self.queue:
                    if wid not in attempted and self.pin.peek(wid,0,'view') is None:
                        target=wid;break
                if target is None:return PrefetchResult('done',fetched,failed)
                attempted.add(target)
                epoch=self._generation
                try:
                    await asyncio.to_thread(self.pin.get,target,0,'view')
                except Exception:
                    failed+=1
                    return PrefetchResult('failed',fetched,failed)
                if self.closed or not self.active or epoch!=self._generation:continue
                fetched+=1
                if self.on_ready is not None:
                    result=self.on_ready(target)
                    if inspect.isawaitable(result):await result
            return PrefetchResult('stopped',fetched,failed)
        finally:self.running=False
