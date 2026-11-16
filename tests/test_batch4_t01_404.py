import pytest
from threading import Lock, Event
from types import SimpleNamespace as NS
from nicegui import Client, ui
from pixivvault_nicegui.ui.scheduler_view import SchedulerView
from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkGateStorageError, ManagedNetworkGateBusy, ManagedNetworkStopped
from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected, AuthenticationMissing
from pixivvault_nicegui.legacy_author_sync import LegacyAuthorSourceError


@pytest.mark.parametrize('kind,error,cause,action',[
    ('uncertain',ManagedNetworkGateStorageError('prior shared GET outcome uncertain'),'前の送信結果','取得停止の確認・回復'),
    ('busy',ManagedNetworkGateBusy('busy'),'通信記録','待って'),
    ('stop',ManagedNetworkStopped(429),'HTTP 429','取得停止の確認・回復'),
    ('http',HTTPStatusRejected(404),'HTTP 404','作者IDと作品の公開状態を確認して、再実行'),
    ('auth',AuthenticationMissing(),'認証情報','Cookie'),
    ('io',OSError('private-data'),'失敗','確認'),
    ('unknown',ValueError('private-data'),'確認できません','再実行'),
    ('legacy',LegacyAuthorSourceError('private-data'),'確認できません','確認してください'),
])
async def test_scheduler_reports_reason_and_next_step(monkeypatch,kind,error,cause,action):
    def fail(*args,**kwargs):
        raise error
    monkeypatch.setattr('pixivvault_nicegui.managed_scheduler.submit_author_batch',fail)
    client=Client(ui.page('/batch4-'+kind))
    try:
        with client:
            view=SchedulerView(NS(closing=False,managed_scheduler=NS(favourites=lambda:('9',)),_scheduler_dispatch_lock=Lock(),_scheduler_cancel_event=Event()))
            await view.run_once()
            assert cause in view.action_notice.text
            assert action in view.action_notice.text
            assert 'private-data' not in view.action_notice.text
            view.close()
    finally:
        client.delete()
