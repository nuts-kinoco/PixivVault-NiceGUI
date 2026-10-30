"""Original K03-K06: explicit selection updates without a second fetch click."""
import asyncio
from datetime import date, timedelta
from threading import Event

from nicegui import Client, ui
import pytest

from pixivvault_nicegui.ui.ranking_view import RankingView
from test_ranking_ui import setup


async def settled(view):
    # No GUI/server: wait only for this component's serialized condition task.
    for _ in range(150):
        task=getattr(view,'_condition_task',None)
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task),3)
            return
        await asyncio.sleep(.01)


@pytest.mark.asyncio
async def test_date_text_change_applies_once_and_relabels_calendar_without_fetch_button():
    services,reader,pins=setup()
    client=Client(ui.page('/_ranking_date_auto'))
    try:
        with client:
            view=RankingView(services);view.set_active(True)
            assert reader.calls==[]
            view.date.set_value('20261001')
            await settled(view)
            assert [call for call in reader.calls if call[0]=='ranking']==[
                ('ranking',{'mode':'daily','content':'illust','date':'20261001','page':1})]
            assert view.page.query.date=='20261001'
            assert view.date_button.text=='2026-10-01'
            assert len(pins)==1
    finally:
        client.delete();services.close()


@pytest.mark.asyncio
async def test_multiple_explicit_selections_are_coalesced_to_latest_condition():
    services,reader,pins=setup()
    client=Client(ui.page('/_ranking_filter_coalesce'))
    try:
        with client:
            view=RankingView(services);view.set_active(True)
            view.mode.set_value('weekly')
            view.content.set_value('manga')
            view.date.set_value('20261001')
            view.r18.set_value(True)
            view.ai.set_value(True)
            await settled(view)
            calls=[call[1] for call in reader.calls if call[0]=='ranking']
            assert calls==[dict(mode='weekly_r18',content='manga',date='20261001',page=1)]
            assert view.page.query.exclude_ai and not view.busy
    finally:
        client.delete();services.close()


@pytest.mark.asyncio
async def test_condition_changed_during_fetch_is_serialized_and_stale_result_is_not_rendered():
    services,reader,pins=setup()
    client=Client(ui.page('/_ranking_filter_running'))
    entered,release=Event(),Event()
    real=reader.get_ranking
    calls=[]
    def delayed(**kw):
        calls.append(kw['mode'])
        if kw['mode']=='daily':
            entered.set();assert release.wait(3)
        return real(**kw)
    reader.get_ranking=delayed
    task=None
    try:
        with client:
            view=RankingView(services);view.set_active(True)
            task=asyncio.create_task(view.load())
            assert await asyncio.to_thread(entered.wait,2)
            view.mode.set_value('weekly')
            await asyncio.sleep(.2)
            assert calls==['daily'], 'never overlap an in-flight GET with a new condition'
            release.set();await task
            await settled(view)
            assert calls==['daily','weekly']
            assert view.page.query.mode=='weekly' and len(pins)==1
    finally:
        release.set()
        if task is not None:await task
        client.delete();services.close()


@pytest.mark.asyncio
async def test_invalid_future_date_and_closed_pending_selection_never_fetch():
    services,reader,pins=setup()
    client=Client(ui.page('/_ranking_invalid_cancel'))
    try:
        with client:
            view=RankingView(services);view.set_active(True)
            view.date.set_value((date.today()+timedelta(days=1)).strftime('%Y%m%d'))
            await settled(view)
            assert not reader.calls and view.page is None
            view.date.set_value('20261001')
            view.set_active(False)
            await settled(view)
            assert not reader.calls
            view.set_active(True)
            assert not reader.calls
            view.mode.set_value('weekly')
            view.close()
            await settled(view)
            assert not reader.calls and not pins
    finally:
        client.delete();services.close()


@pytest.mark.asyncio
async def test_calendar_selection_and_cancel_keep_epoch_guard():
    services,reader,pins=setup()
    client=Client(ui.page('/_ranking_calendar_guard'))
    try:
        with client:
            view=RankingView(services);view.set_active(True)
            view.open_calendar()
            captured=view._calendar_token
            view._calendar_pick(captured,{'epoch':captured[0],'nonce':captured[1],
                                          'value':'2026-10-01'})
            await settled(view)
            assert view.page.query.date=='20261001'
            assert view.date_button.text=='2026-10-01'
            before=list(reader.calls)
            view.open_calendar();old=view._calendar_token
            view._close_calendar()
            view.open_calendar();new=view._calendar_token
            view._calendar_pick(old,{'epoch':old[0],'nonce':old[1],'value':'2026-09-30'})
            await asyncio.sleep(.2)
            assert reader.calls==before and view._calendar_token==new
            view.set_active(False)
            view._calendar_pick(new,{'epoch':new[0],'nonce':new[1],'value':'2026-09-29'})
            assert view.date.value=='20261001'
    finally:
        client.delete();services.close()
