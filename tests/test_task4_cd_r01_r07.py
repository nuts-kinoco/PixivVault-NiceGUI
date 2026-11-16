"""Task4 C/D: SuperLike withdrawal (undo, idempotence) and refresh counts. Synthetic only.

Representation chosen for a withdrawn SuperLike: the learning verdict
(discovery_swipe_history.verdict == 'superLike') is left byte-identical, exactly
as the original (recommend_tab._on_dismiss_superlike -> database.dismiss_recommend
touches only recommend_dismissed).  The withdrawal is the sidecar override
('superlike', work_id, hidden=1); Undo writes hidden=0 for the same key.
"""
import asyncio
import sqlite3
from types import SimpleNamespace as NS
import pytest
from nicegui import Client, ui, core, background_tasks
from pixivvault_nicegui.recommendation_state import (
    RecommendationController, RecommendationPreferences, RecommendationSnapshot,
    RecommendationGroup, RecommendationWork, RecommendationError)
from pixivvault_nicegui.ui.recommend_view import RecommendView
from test_fail7_batch2 import SavedSuperLikes, ids


async def settle(before):
    await asyncio.gather(*(set(background_tasks.running_tasks)-before))


def click(button):
    callback=next(e.handler for e in button._event_listeners.values() if e.type=='click')
    pending=set(background_tasks.running_tasks);callback(NS());return pending


def find(view,text):return [b for b in view.body.descendants() if isinstance(b,ui.button) and b.text==text]


@pytest.mark.asyncio
async def test_c_withdraw_superlike_keeps_verdict_is_idempotent_and_undoable(tmp_path,monkeypatch):
    monkeypatch.setattr(core,'loop',asyncio.get_running_loop())
    source=SavedSuperLikes(tmp_path/'history.sqlite3');original=source.path.read_bytes()
    controller=RecommendationController(source,RecommendationPreferences(tmp_path/'prefs'))
    client=Client(ui.page('/_task4_c'))
    try:
        with client:
            view=RecommendView(NS(recommendations=controller));view.active=True;await view.refresh()
            buttons=find(view,'SuperLikeを取り下げる')
            assert len(buttons)==1
            first=click(buttons[0]);click(buttons[0])  # rapid double click
            await settle(first)
            assert ids(view.data,'superlike')==[]
            assert ids(view.data,'certain')==['123']          # verdict-derived shelf kept
            assert source.path.read_bytes()==original           # verdict untouched
            with sqlite3.connect(source.path) as db:
                assert db.execute('SELECT verdict FROM history').fetchall()==[('superLike',)]
            assert 'おすすめから取り下げました' in view.notice.text
            assert 'エラー' not in view.notice.text and '失敗' not in view.notice.text
            revision=view.data['revision']
            # withdrawing again (stale card) is a no-op, not an error or revision bump
            await view.dismiss_superlike('123',generation=view._render_generation)
            assert view.data['revision']==revision
            # Undo restores the shelf entry
            assert view.undo_button.visible and view.undo_button.enabled
            pending=set(background_tasks.running_tasks)
            click(view.undo_button);await settle(pending)
            assert ids(view.data,'superlike')==['123']
            assert not view.undo_button.visible
            assert source.path.read_bytes()==original
            view.close()
    finally:client.delete();controller.close()


def test_c_controller_withdraw_is_idempotent(tmp_path):
    source=SavedSuperLikes(tmp_path/'h.sqlite3')
    c=RecommendationController(source,RecommendationPreferences(tmp_path/'p'))
    try:
        c.refresh();s1=c.dismiss_superlike('123',c.snapshot()['revision'])
        s2=c.dismiss_superlike('123',s1['revision'])
        assert s2['revision']==s1['revision'] and ids(s2,'superlike')==[]
        with pytest.raises(RecommendationError):c.dismiss_superlike('999',s2['revision'])
    finally:c.close()


class Counting:
    """Controller double whose update() yields a known result."""
    def __init__(self,inner,works,errors=None):
        self.inner=inner;self.works=works;self.errors=errors or {}
        self.seen=None;self.view=None
    def refresh(self):return self.inner.refresh()
    def snapshot(self):return self.inner.snapshot()
    def update(self,revision):
        self.seen=self.view.busy_notice.text
        state=self.inner.snapshot()
        works=tuple(RecommendationWork(str(i+1),'t','9','a') for i in range(self.works))
        state['sections']={'certain':(RecommendationGroup('c','c',works),)} if works else {}
        state['errors']=self.errors
        return state


@pytest.mark.asyncio
@pytest.mark.parametrize('count,expected',[(66,'おすすめを更新しました（5節・66作品）。専用設定に保存しました。'),
    (0,'おすすめを更新しました（5節・0作品）。表示できる作品はありません。専用設定に保存しました。')])
async def test_d_refresh_states_numbers(tmp_path,monkeypatch,count,expected):
    monkeypatch.setattr(core,'loop',asyncio.get_running_loop())
    inner=RecommendationController(SavedSuperLikes(tmp_path/'h.sqlite3'),RecommendationPreferences(tmp_path/'p'))
    controller=Counting(inner,count);controller.acquisition=inner.acquisition
    client=Client(ui.page(f'/_task4_d{count}'))
    try:
        with client:
            view=RecommendView(NS(recommendations=controller));controller.view=view
            view.active=True;await view.refresh()
            view.data['can_acquire']=True
            await view.update()
            assert controller.seen=='おすすめを読み込んでいます…'
            assert view.notice.text==expected
            view.close()
    finally:client.delete();inner.close()
