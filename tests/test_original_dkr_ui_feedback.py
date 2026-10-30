"""Original D06/R01b/K05/K14 interactions, not merely handler existence."""
import asyncio
from threading import Event
from types import SimpleNamespace

from nicegui import Client, ui
import pytest

from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.ranking_view import RankingView
from pixivvault_nicegui.ui.recommend_view import RecommendView
from test_learning_view import view, payload
from test_ranking_ui import setup


async def test_ctrl_z_undo_payload_uses_same_generation_guard(view):
    view._on_loaded(SimpleNamespace(args=view._image_url))
    old = payload(view)
    assert view.judge('like', old)
    view._on_key(SimpleNamespace(args=dict(old, action='undo')))
    assert view.state.index == 1
    view._review_open = True
    view._on_key(SimpleNamespace(args=dict(payload(view, 'review-ignored'), action='undo')))
    assert view.state.index == 1
    view._review_open = False
    view._on_key(SimpleNamespace(args=dict(payload(view, 'keyboard-undo'), action='undo')))
    assert view.state.index == 0 and not view.ledger.entries
    assert not view.state.ready


@pytest.mark.asyncio
async def test_recommend_update_has_visible_fetching_feedback():
    service = ApplicationServices(1)
    client = Client(ui.page('/_recommend_visible_busy'))
    entered, release = Event(), Event()
    task = None
    try:
        with client:
            component = RecommendView(service)
            component.active = True
            component.data = {'revision':0, 'can_acquire':True, 'writable':False, 'order':()}
            def delayed(revision):
                entered.set()
                assert release.wait(5)
                raise RuntimeError('generated fetch fault')
            component.controller = SimpleNamespace(update=delayed)
            task = asyncio.create_task(component.update())
            assert await asyncio.to_thread(entered.wait, 5)
            assert component.busy_notice.visible
            assert '取得中' in component.busy_notice.text
            assert not component.update_button.enabled
            release.set()
            await task
            assert not component.busy_notice.visible and not component.busy
            assert component.update_button.enabled
            assert '失敗' in component.notice.text
    finally:
        release.set()
        if task is not None:
            await asyncio.wait_for(task, 5)
        client.delete()
        service.close()


@pytest.mark.asyncio
async def test_ranking_monthly_displays_why_r18_is_disabled():
    service, _, _ = setup()
    client = Client(ui.page('/_ranking_disabled_reason'))
    try:
        with client:
            component = RankingView(service)
            component.mode.set_value('monthly')
            component.changed()
            assert not component.r18.enabled
            assert 'R-18' in component.notice.text and '利用できません' in component.notice.text
    finally:
        client.delete()
        service.close()


@pytest.mark.asyncio
async def test_more_button_has_loading_then_returns_after_failure():
    service, reader, _ = setup()
    client = Client(ui.page('/_ranking_more_busy'))
    entered, release = Event(), Event()
    task = None
    try:
        with client:
            component = RankingView(service)
            component.set_active(True)
            await component.load()
            def delayed(**kwargs):
                entered.set()
                assert release.wait(5)
                raise RuntimeError('generated page fault')
            reader.get_ranking = delayed
            task = asyncio.create_task(component.load(append=True))
            assert await asyncio.to_thread(entered.wait, 5)
            assert component.gallery.more_button.text == '読み込み中…'
            assert not component.gallery.more_button.enabled
            release.set()
            await task
            assert component.gallery.more_button.text == 'もっと見る'
            assert component.gallery.more_button.enabled
            assert '取得できませんでした' in component.notice.text
    finally:
        release.set()
        if task is not None:
            await asyncio.wait_for(task, 5)
        client.delete()
        service.close()
