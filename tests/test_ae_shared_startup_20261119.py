from types import SimpleNamespace as NS

import pytest

from pixivvault_nicegui.bootstrap import _wire_followup
from pixivvault_nicegui.queue_log import QueueLog


@pytest.mark.asyncio
async def test_startup_following_logs_one_start_and_verified_count(tmp_path,monkeypatch):
    from test_cookie_account_followup_20261117 import make
    binding,store,_=make(tmp_path,monkeypatch)
    log=QueueLog()
    _wire_followup(binding,NS(following_sync=store,shared_log=log))
    try:
        await binding.refresh_auth_status(startup=True)
        messages=[item.message for item in log.snapshot()]
        assert messages==['起動時フォローリスト自動同期を開始します...', '起動時自動同期完了 (3人の作者)']
        await binding.refresh_auth_status(startup=True)
        assert [item.message for item in log.snapshot()]==messages
    finally:binding.close();store.close()


@pytest.mark.asyncio
async def test_cookie_auto_extract_logs_fixed_success_without_secret(tmp_path,monkeypatch):
    from test_cookie_auto_extract_20261117 import make
    binding,_,_=make(tmp_path,monkeypatch,expired=True)
    log=QueueLog();_wire_followup(binding,NS(following_sync=None,shared_log=log))
    try:
        await binding.refresh_auth_status(startup=True)
        rows=log.snapshot()
        assert [e.message for e in rows]==['Cookie 有効期限チェック: ブラウザから自動抽出を試行します。',
                                          'ブラウザからの Cookie 自動抽出・更新に成功しました。']
        assert all('dummy-browser-session' not in e.message for e in rows)
    finally:binding.close()


def test_metadata_auto_summary_distinguishes_stop_and_failure():
    from pixivvault_nicegui.metadata_runner import MetadataRunSummary
    from pixivvault_nicegui.services import ApplicationServices
    log=QueueLog();owner=NS(shared_log=log,run_managed_metadata_all=lambda **_:MetadataRunSummary(stopped='network_stopped'))
    summary=ApplicationServices._run_metadata_auto_logged(owner,transport=None)
    assert summary.stopped=='network_stopped' and log.snapshot()[-1].level=='normal'
    owner.run_managed_metadata_all=lambda **_:MetadataRunSummary(stopped='global_failure')
    ApplicationServices._run_metadata_auto_logged(owner,transport=None)
    assert log.snapshot()[-1].level=='error'


@pytest.mark.asyncio
async def test_startup_following_gate_stop_is_not_logged_as_failure(tmp_path,monkeypatch):
    from test_cookie_account_followup_20261117 import make
    from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopped
    def stop(_):raise ManagedNetworkStopped(429)
    binding,store,_=make(tmp_path,monkeypatch,reader_factory=stop)
    log=QueueLog();_wire_followup(binding,NS(following_sync=store,shared_log=log))
    try:
        await binding.refresh_auth_status(startup=True)
        rows=log.snapshot()
        assert rows[-1].message=='フォローリスト自動同期は停止しました。'
        assert rows[-1].level=='normal'
    finally:binding.close();store.close()


def test_metadata_shutdown_failure_does_not_emit_error():
    from threading import Event
    from pixivvault_nicegui.services import ApplicationServices
    stop=Event();stop.set();log=QueueLog()
    def fail(**_):raise RuntimeError('synthetic-private')
    owner=NS(shared_log=log,_metadata_cancel_event=stop,closing=False,run_managed_metadata_all=fail)
    with pytest.raises(RuntimeError):ApplicationServices._run_metadata_auto_logged(owner,transport=None)
    assert not log.snapshot()
