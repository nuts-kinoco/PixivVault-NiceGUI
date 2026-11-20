import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from z_learning_support_20261118 import Clock, candidate, open_discovery_view, seed_candidates


def by_id(view, testid):
    return next((element for element in view.root.descendants()
                 if element.props.get('data-testid') == testid), None)


def text_in(card):
    return {element.text for element in card.descendants() if hasattr(element, 'text')}


@pytest.mark.parametrize(('failed', 'stopped', 'title', 'body', 'icon', 'retry'), [
    (True, False, '候補を取りにいけませんでした',
     '通信に失敗しました。30秒ごとにもう一度試しています。', None, True),
    (True, True, '候補を取りにいけませんでした',
     '自動での再取得は止まっています。', 'cloud_off', True),
    (False, False, '次の候補を探しています',
     'いま出せる候補はすべて判定しました。新しい候補が見つかると続きから始まります。', None, False),
    (False, True, 'しばらく新しい候補が見つかりませんでした',
     '自動での再取得は止まっています。', 'search_off', True),
])
async def test_learning_candidate_status_card(tmp_path, monkeypatch, failed, stopped, title, body, icon, retry):
    view, _, _, cleanup = await open_discovery_view(tmp_path, monkeypatch, count=0)
    try:
        view._refill.arm(view._refill_clock())
        view._refill.last_failed = failed
        view._refill.gave_up = stopped
        # An earlier failed round followed by a successful empty fetch still
        # gives up on exhausted inventory, with the search icon.
        view._refill.failed_rounds = 1
        view._judged_before_session = 17
        with view.client:
            view._render_refill_status()
        card = by_id(view, 'learning-candidate-status')
        assert card is not None and card.visible and card.parent_slot.parent.visible
        assert {title, body, 'これまでの判定: 17枚'} <= text_in(card)
        assert by_id(view, 'learning-candidate-retry').visible is retry
        assert by_id(view, 'learning-candidate-spinner').visible is (icon is None)
        status_icon = by_id(view, 'learning-candidate-icon')
        assert status_icon.visible is (icon is not None)
        if icon is not None:
            assert status_icon.props['name'] == icon
        assert not view.empty_card.visible
        assert not view.fetch_button.visible
        assert not view.refill_status.visible
    finally:
        await cleanup()


async def test_learning_candidate_retry_uses_restock_and_returns_to_judging(tmp_path, monkeypatch):
    view, _, service, cleanup = await open_discovery_view(tmp_path, monkeypatch, count=0)
    clock = Clock()
    view._refill_clock = clock
    calls = []

    async def fetch_candidates(*, is_current, **kwargs):
        assert is_current()
        calls.append(clock.now)
        if len(calls) == 1:
            return SimpleNamespace(failed=('ranking',))
        seed_candidates(service, [candidate(i) for i in range(10)])
        return SimpleNamespace(failed=())

    monkeypatch.setattr(service, 'fetch_candidates', fetch_candidates)
    try:
        session = view._session_id
        view._refill.arm(clock())
        view._refill.delay(0, clock())
        view._refill.next_at += 30
        view._refill.last_failed = True
        with view.client:
            view._render_refill_status()
            view._retry_candidate_restock()
        async with asyncio.timeout(5):
            while not view._refill.rounds:
                await asyncio.sleep(.01)
        assert calls == [1000]
        assert view._refill.delay(0, 1029) == 1
        assert view._refill.delay(0, 1030) == 0
        clock.now += 30
        with view.client:
            view._kick_candidate_refill()
        async with asyncio.timeout(5):
            while view.state.current_artwork is None:
                await asyncio.sleep(.01)
        assert calls == [1000, 1030]
        assert view._session_id == session
        assert not by_id(view, 'learning-candidate-status').parent_slot.parent.visible
        assert view.picture.visible
    finally:
        await cleanup()


async def test_learning_exhausted_includes_history_outside_the_candidate_queue(tmp_path, monkeypatch):
    view, _, service, cleanup = await open_discovery_view(tmp_path, monkeypatch, count=1)
    open_session = service.open_session

    async def with_history():
        snapshot = await open_session()
        return replace(snapshot, judged_ids=frozenset(str(7000 + index) for index in range(17)))

    async def ranking_failed(*args):
        raise OSError('synthetic ranking failure')

    monkeypatch.setattr(service, 'open_session', with_history)
    monkeypatch.setattr(service, 'rank_pending', ranking_failed)
    monkeypatch.setattr(view, '_kick_candidate_refill', lambda: None)
    try:
        with view.client:
            view.reload_storage()
        async with asyncio.timeout(5):
            while view._storage_busy:
                await asyncio.sleep(.01)
        view._refill.arm(view._refill_clock())
        with view.client:
            view._on_loaded(SimpleNamespace(args=view._image_url))
            assert view.judge('like', dict(work_id='1000', generation=view.state.generation, operation_id='lrn'))
        await view._save_task
        assert 'これまでの判定: 18枚' in text_in(by_id(view, 'learning-candidate-status'))
        assert view.status.text == '判定は保存済みです。候補の並び替えを更新できませんでした。'
    finally:
        await cleanup()


async def test_learning_zero_history_keeps_first_use_guidance(tmp_path, monkeypatch):
    view, _, _, cleanup = await open_discovery_view(tmp_path, monkeypatch, count=0)
    try:
        view._refill.arm(view._refill_clock())
        view._refill.gave_up = True
        with view.client:
            view._render_refill_status()
        assert view.empty_card.visible
        assert not by_id(view, 'learning-candidate-status').parent_slot.parent.visible
    finally:
        await cleanup()
