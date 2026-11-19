import asyncio
from types import SimpleNamespace as NS
from threading import Lock, Event
import pytest
from pixivvault_nicegui.learning_service import LearningService
from pixivvault_nicegui.fixtures import generate_demo_artworks
from pixivvault_nicegui.learning import Judgment

@pytest.mark.parametrize('verdict',['like','superLike','skip','hate'])
async def test_badge_is_current_join_and_survives_reopen(tmp_path,verdict):
    s=LearningService(tmp_path/'learning',generate_demo_artworks(2))
    try:
        x=await s.open_session()
        for _ in range(3):await s.record_view('demo-0001')
        await s.record(x.session_id,Judgment('demo-0001',verdict,'j'))
        row=(await s.view_history())[0]
        assert row['open_count']==3 and row['discovery_verdict']==verdict
        review=(await s.review_snapshot(x.session_id))['rows'][0]
        await s._wait(s.submit_review_undo(x.session_id,'demo-0001',review['revision'],'undo'))
        assert (await s.view_history())[0]['discovery_verdict'] is None
        await s._wait(s.submit_review_restore(x.session_id,'undo','restore'))
        assert (await s.view_history())[0]['discovery_verdict']==verdict
    finally:s.close()
    s=LearningService(tmp_path/'learning',generate_demo_artworks(2))
    try:assert (await s.view_history())[0]['discovery_verdict']==verdict
    finally:s.close()

async def test_clear_rejects_changed_snapshot_and_unknown_open(tmp_path):
    s=LearningService(tmp_path/'learning',generate_demo_artworks(2))
    try:
        await s.open_session();await s.record_view('demo-0001');old=await s.view_history()
        await s.record_view('demo-0002')
        with pytest.raises(ValueError):await s.clear_view_history(old)
        with pytest.raises(ValueError):await s.record_view('unknown')
        current=await s.view_history();assert len(current)==2
        await s.clear_view_history(current);assert await s.view_history()==[]
    finally:s.close()

@pytest.mark.parametrize('kind',['uncertain','busy','stop','http','io','unknown'])
async def test_scheduler_displays_typed_failure_without_raw_exception(monkeypatch,kind):
    from nicegui import Client,ui
    from pixivvault_nicegui.ui.scheduler_view import SchedulerView
    from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkGateStorageError, ManagedNetworkGateBusy, ManagedNetworkStopped
    from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected
    errors={'uncertain':ManagedNetworkGateStorageError('prior shared GET outcome uncertain'), 'busy':ManagedNetworkGateBusy('busy'), 'stop':ManagedNetworkStopped(429), 'http':HTTPStatusRejected(404), 'io':OSError('private-data'), 'unknown':ValueError('private-data')}
    def fail(*a,**kw):raise errors[kind]
    monkeypatch.setattr('pixivvault_nicegui.managed_scheduler.submit_author_batch',fail)
    client=Client(ui.page('/batch3-'+kind))
    try:
        with client:
            v=SchedulerView(NS(closing=False,managed_scheduler=NS(sync_favourites=lambda authors: None),scheduler_favourite_authors=lambda: ('9',),_scheduler_dispatch_lock=Lock(),_scheduler_cancel_event=Event()))
            await v.run_once();text=v.action_notice.text
            assert text and 'private-data' not in text and 'キュー状態を確認' not in text
            if kind=='uncertain':assert '取得停止の確認・回復' in text
            v.close()
    finally:client.delete()

async def test_history_tab_retirement_and_failed_clear_notice_survives_refresh(tmp_path,monkeypatch):
    from nicegui import Client,ui,core
    monkeypatch.setattr(core, "loop", asyncio.get_running_loop())
    from pixivvault_nicegui.services import ApplicationServices
    from pixivvault_nicegui.ui.learning_view import LearningView
    s=ApplicationServices(2,learning_sandbox=tmp_path/'learning')
    client=Client(ui.page('/batch3-lifetime'))
    try:
        with client:
            v=LearningView(s);v.active=True
            await s.learning.open_session();await s.learning.record_view('demo-0001')
            await v.view_history.refresh();v.view_history.confirm.set_value('1')
            await s.learning.record_view('demo-0002')
            await v.view_history.clear()
            text=v.view_history.notice.text
            assert '変更されたか' in text
            await v.view_history.refresh();assert v.view_history.notice.text==text
            generation=v.view_history.generation
            v.set_active(False);assert v.view_history.generation>generation
            v.close()
    finally:
        s.close();client.delete()
