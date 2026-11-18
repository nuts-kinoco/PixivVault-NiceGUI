import asyncio
from types import SimpleNamespace as NS

import pytest
from nicegui import Client, core, ui
from pixivvault_nicegui.queue_log import QueueLog
from pixivvault_nicegui.services import ApplicationServices


def test_owned_cache_clear_logs_confirmed_and_fixed_failure():
    log=QueueLog(); calls=[]
    cache=NS(clear=lambda:calls.append('cleared'))
    owner=NS(shared_image_cache=cache,shared_log=log)
    ApplicationServices.clear_shared_image_cache(owner)
    assert calls==['cleared'] and log.snapshot()[-1].message=='画像キャッシュを削除しました。'
    def fail():raise OSError('synthetic-private-path')
    cache.clear=fail
    with pytest.raises(OSError):ApplicationServices.clear_shared_image_cache(owner)
    assert log.snapshot()[-1].message=='画像キャッシュを削除できませんでした。'
    assert all('synthetic-private-path' not in e.message for e in log.snapshot())


@pytest.mark.asyncio
async def test_real_learning_reset_and_history_clear_log_once(tmp_path,monkeypatch):
    from pixivvault_nicegui.learning import Judgment
    from pixivvault_nicegui.ui.learning_view import LearningView
    monkeypatch.setattr(core,'loop',asyncio.get_running_loop())
    services=ApplicationServices(2,learning_sandbox=tmp_path/'learning')
    client=Client(ui.page('/_ae_shared_learning_actions'))
    try:
        with client:
            view=LearningView(services);view.active=True
            session=await services.learning.open_session()
            await services.learning.record(session.session_id,Judgment('demo-0001','like','op'))
            await view.reset_view.refresh();await view.reset_view.open()
            view.reset_view.confirm.set_value('1');await view.reset_view.execute()
            assert await services.learning.judged_count()==0
            await services.learning.record_view('demo-0001')
            await view.view_history.refresh();view.view_history.confirm.set_value('1')
            await view.view_history.clear()
            assert await services.learning.view_history()==[]
            messages=[e.message for e in services.shared_log.snapshot()]
            assert messages.count('学習状況: リセットしました')==1
            assert messages.count('閲覧履歴: 全件削除しました')==1
            view.close()
    finally:client.delete();services.close()


@pytest.mark.asyncio
async def test_queue_control_failure_logs_fixed_text_once():
    from pixivvault_nicegui.ui.job_views import QueueView
    def fail(*a,**k):raise OSError('synthetic-private-path')
    log=QueueLog();jobs=NS(snapshot=lambda:(),faulted=False,control=fail)
    client=Client(ui.page('/_ae_shared_queue_action'))
    try:
        with client:
            view=QueueView(NS(jobs=jobs,artworks=(),shared_log=log))
            assert await view.command('job','stop') is False
            assert log.snapshot()[-1].message=='キュー操作に失敗しました。'
            assert 'synthetic-private-path' not in log.snapshot()[-1].message
            view.close()
    finally:client.delete()
