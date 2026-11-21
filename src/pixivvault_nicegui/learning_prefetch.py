"""Sequential image look-ahead for an already accepted discovery queue.

Original discovery_tab FIFO bytes/count policy; this module never discovers
new works, polls a source or retries an unsuccessful image in the same run.
"""
from __future__ import annotations
import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
import inspect

from .learning_service import notify_shadow
from .library_media_registry import PinnedLibraryImages
from .remote_media import RemoteBookmarkImages, RemoteImageNotFound


@contextmanager
def _ranking_remote(pin, work_id):
    if isinstance(pin, RemoteBookmarkImages):
        yield pin
        return
    if not isinstance(pin, PinnedLibraryImages):
        yield None
        return
    # Ranking pins are remote-only. Take the same temporary version reference
    # as get_versioned(), and leave all other catalog kinds on their normal path.
    registry, token = pin.registry, pin.token
    with registry._lock:
        version = registry._version(token) if not pin.closed else None
        remote = None
        if (version is not None and token != registry._current
                and version.local is None and version.preview is None
                and type(version.remote) is RemoteBookmarkImages and work_id in version.remote.works):
            remote = version.remote
            version.refs += 1
    try:
        yield remote
    finally:
        if remote is not None:
            registry.release(token)


def peek_learning_image(pin, work_id, page):
    with _ranking_remote(pin, work_id) as remote:
        return remote.peek_with_variant(work_id, page, 'view') if remote is not None else None


def _fetch_learning_image(pin, work_id):
    with _ranking_remote(pin, work_id) as remote:
        if remote is not None:
            return remote.get_with_variant(work_id, 0, 'view')
    pin.get(work_id, 0, 'view')
    return None


@dataclass(frozen=True)
class PrefetchResult:
    status: str
    fetched: int=0
    failed: int=0


class LearningImagePrefetch:
    def __init__(self,preferences,pin,*,on_ready=None,on_image=None,image_observer=None):
        self.preferences=preferences;self.pin=pin;self.on_ready=on_ready
        self.on_image=on_image
        self.image_observer=image_observer
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
                    image = None
                    on_image = (notify_shadow(self.image_observer, route='prefetch_observer')
                                if self.image_observer is not None else self.on_image)
                    if on_image is None:
                        await asyncio.to_thread(self.pin.get,target,0,'view')
                    else:
                        image = await asyncio.to_thread(_fetch_learning_image, self.pin, target)
                except RemoteImageNotFound:
                    failed += 1
                    continue
                except Exception:
                    failed+=1
                    return PrefetchResult('failed',fetched,failed)
                if self.closed or not self.active or epoch!=self._generation:continue
                fetched+=1
                if image is not None:
                    notify_shadow(on_image,target,0,image,route='prefetch_image')
                if self.on_ready is not None:
                    result=self.on_ready(target)
                    if inspect.isawaitable(result):await result
            return PrefetchResult('stopped',fetched,failed)
        finally:self.running=False
