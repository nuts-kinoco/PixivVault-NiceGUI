"""P3-4a 試験用: 発掘(discovery)つきの LearningView を、偽クライアント・隔離フォルダーだけで作る。

実 Pixiv へは通信しない（reader は何も返さない偽物）。候補は managed の候補表へ直接入れる。
"""
import asyncio
import json
from types import SimpleNamespace

from nicegui import Client, background_tasks, core, ui

from pixivvault_nicegui.discovery_learning import DiscoveryLearningService
from pixivvault_nicegui.ui.learning_view import LearningView


class FakeReader:
    def __init__(self):
        self.calls = []

    def require_identity(self):
        self.calls.append('require_identity')
        return '99'

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)

        def call(*args, **kwargs):
            self.calls.append(name)
            return []
        return call


class _Pin:
    def close(self):
        pass


class FakeImages:
    def url(self, work_id, page, kind):
        return f'/fake-image/{work_id}/{page}/{kind}'

    def close(self):
        pass


class FakeServices:
    """LearningView が使う窓口だけを持つ最小の services。"""

    def __init__(self, discovery):
        self.discovery = discovery
        self.learning = None
        self.artworks = ()
        self.images = FakeImages()
        self.closing = False
        self.managed_downloads = None

    def acquire_ranking_images(self, records):
        return FakeImages()

    def release_managed_owner(self, owner):
        pass


def candidate(index: int, *, tags=('tagA', 'tagB'), author=None, source='following') -> dict:
    return dict(work_id=str(1000 + index), title=f'candidate {index}', author_id=str(author or 500 + index),
                author_name=f'author {index}', tags=list(tags), source=source,
                thumbnail_url=f'https://i.pximg.net/c/250x250/img/{index}_p0.jpg')


def seed_candidates(service, candidates, frames=None):
    def run():
        db = service._database()
        with db._transaction() as conn:
            for item in candidates:
                conn.execute('INSERT INTO managed_discovery_candidate VALUES (?,?)',
                             (item['work_id'], json.dumps(item, ensure_ascii=False, sort_keys=True)))
        for item in candidates:
            service._accept_metadata(item)
        service.frames = dict(frames or {})
    service._submit(run).result()


async def settled(view):
    async with asyncio.timeout(10):
        while view._storage_busy or not view._storage_ready or view.state.saving:
            await asyncio.sleep(.01)
        while any(not t.done() for t in getattr(view, '_reason_tasks', ())):
            await asyncio.sleep(.01)


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


async def open_discovery_view(tmp_path, monkeypatch, *, count=4, frames=None, route='/_z_discovery'):
    """(view, services, service, cleanup) を返す。cleanup は await する。"""
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    service = DiscoveryLearningService(tmp_path / 'discovery', FakeReader(), following=(), saved_ids=())
    seed_candidates(service, [candidate(i) for i in range(count)], frames)
    services = FakeServices(service)
    client = Client(ui.page(route))
    before = set(background_tasks.running_tasks)
    with client:
        view = LearningView(services)
    view._initial_done = True          # 初回取得（偽 reader への呼び出し）は走らせない
    view.clock = Clock()
    view.toast._clock = view.clock
    await settled(view)
    view.set_active(True)
    await settled(view)

    async def cleanup():
        view.close()
        client.delete()
        owned = set(background_tasks.running_tasks) - before
        for task in owned:
            if not task.done():
                task.cancel()
        await asyncio.gather(*owned, return_exceptions=True)
        await asyncio.to_thread(service.close)
    return view, services, service, cleanup
