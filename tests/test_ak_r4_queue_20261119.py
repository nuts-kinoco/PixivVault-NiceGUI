from types import SimpleNamespace
import asyncio
import pytest
from pixivvault_nicegui.ui.learning_view import LearningView


@pytest.mark.asyncio
async def test_pending_ui_order_revalidated_and_old_click_invalidated():
    class Discovery:
        async def rank_pending(self, ids, authors):
            assert ids == ('1', '2')
            return ('2', '1'), {'2': 'following'}
    class State:
        artworks = (SimpleNamespace(work_id='1', author_id='a'), SimpleNamespace(work_id='2', author_id='b'))
        index = 0
        generation = 7
        saving = False
        closed = False
        def _invalidate(self): self.generation += 1
    calls = []
    view = SimpleNamespace(state=State(), discovery=Discovery(), services=SimpleNamespace(closing=False),
        _refill_merge_revision=0, _tagger_rank_epoch=1, _discovery_frames={}, refresh=lambda: calls.append(True))
    await LearningView._rerank_tagger_order(view, 1)
    assert tuple(a.work_id for a in view.state.artworks) == ('2', '1')
    assert view.state.generation == 8
    assert calls == [True]


@pytest.mark.asyncio
async def test_late_ranking_does_not_overwrite_newer_setting():
    started, release = asyncio.Event(), asyncio.Event()
    class Discovery:
        async def rank_pending(self, ids, authors):
            started.set()
            await release.wait()
            return ('2', '1'), {}
    class State:
        artworks = (SimpleNamespace(work_id='1', author_id='a'), SimpleNamespace(work_id='2', author_id='b'))
        index = 0
        generation = 7
        saving = False
        closed = False
    view = SimpleNamespace(state=State(), discovery=Discovery(), services=SimpleNamespace(closing=False),
        _refill_merge_revision=0, _tagger_rank_epoch=1, _discovery_frames={}, refresh=lambda: None)
    task = asyncio.create_task(LearningView._rerank_tagger_order(view, 1))
    await started.wait()
    view._tagger_rank_epoch = 2
    release.set()
    await task
    assert tuple(a.work_id for a in view.state.artworks) == ('1', '2')


def test_cached_local_trend_is_not_reused_before_revalidation(monkeypatch):
    from pixivvault_nicegui.ui import learning_view
    renders, scheduled = [], []
    async def refresh(): pass
    def create(coroutine, **kwargs):
        scheduled.append(True)
        coroutine.close()
    monkeypatch.setattr(learning_view.background_tasks, 'create', create)
    view = SimpleNamespace(_trend_snapshot={'ready': False, 'local_seed_rows': ({'tag': 'old'},)},
        _render_trend=lambda value: renders.append(value), _refresh_local_trend=refresh)
    LearningView._revalidate_local_trend(view)
    assert renders[0]['local_seed_rows'] == ()
    assert scheduled == [True]


@pytest.mark.asyncio
async def test_rerank_retries_when_refill_merges():
    started, release = asyncio.Event(), asyncio.Event()
    class Discovery:
        calls = 0
        async def rank_pending(self, ids, authors):
            self.calls += 1
            if self.calls == 1:
                started.set()
                await release.wait()
            return tuple(reversed(ids)), {}
    class State:
        artworks = (SimpleNamespace(work_id='1', author_id='a'), SimpleNamespace(work_id='2', author_id='b'))
        index = 0
        generation = 7
        saving = False
        closed = False
        def _invalidate(self): self.generation += 1
    view = SimpleNamespace(state=State(), discovery=Discovery(), services=SimpleNamespace(closing=False),
        _refill_merge_revision=0, _tagger_rank_epoch=1, _discovery_frames={}, refresh=lambda: None)
    task = asyncio.create_task(LearningView._rerank_tagger_order(view, 1))
    await started.wait()
    view.state.artworks += (SimpleNamespace(work_id='3', author_id='c'),)
    view._refill_merge_revision += 1
    release.set()
    await task
    assert tuple(a.work_id for a in view.state.artworks) == ('3', '2', '1')
    assert view.discovery.calls == 2
