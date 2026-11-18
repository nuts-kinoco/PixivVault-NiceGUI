from collections import namedtuple
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from nicegui import Client, ui

from pixivvault_nicegui.queue_log import QueueLog
from pixivvault_nicegui.ui.duplicate_view import DuplicateView
from test_required_legacy_duplicates import setup, select


@pytest.mark.asyncio
async def test_real_no_space_preflight_reports_unprocessed_without_success(tmp_path,monkeypatch):
    from pixivvault_nicegui import legacy_physical_quarantine as physical
    source,legacy,owner,_,original,archive=setup(tmp_path)
    before={p.relative_to(original):p.read_bytes() for p in original.rglob('*') if p.is_file()}
    log=QueueLog();client=Client(ui.page('/_ae_no_space_log'))
    usage=namedtuple('Usage','total used free')
    try:
        with client:
            view=DuplicateView(source,event_services=NS(shared_log=log))
            plan=select(source,{'1','2'})
            monkeypatch.setattr(physical.shutil,'disk_usage',lambda _:usage(100,100,0))
            result,_=await view._execute_accepted(plan,uuid4().hex,True)
            assert not result.committed and not result.failed and len(result.unprocessed)==2
            assert not legacy.state()['exclusions']
            assert before=={p.relative_to(original):p.read_bytes() for p in original.rglob('*') if p.is_file()}
            entry,=log.snapshot()
            assert '未処理2件' in entry.message and entry.level!='success'
            view.close()
    finally:client.delete();source.close();owner.close()


@pytest.mark.asyncio
async def test_real_recovery_required_is_reported_without_success(tmp_path,monkeypatch):
    source,legacy,owner,_,original,_=setup(tmp_path,extra_zip=True)
    log=QueueLog();client=Client(ui.page('/_ae_recovery_required_log'))
    try:
        with client:
            view=DuplicateView(source,event_services=NS(shared_log=log))
            plan=select(source,{'1','2','5'})
            def fail(stage):
                if stage=='physical-installed:1:1':raise OSError('synthetic failure')
            monkeypatch.setattr(legacy,'_checkpoint',fail)
            monkeypatch.setattr(legacy._physical,'_rollback',lambda _:(_ for _ in ()).throw(PermissionError('synthetic denial')))
            result,_=await view._execute_accepted(plan,uuid4().hex,True)
            assert result.recovery_required and len(result.failed)==1 and len(result.unprocessed)==2
            entry,=log.snapshot()
            assert '未処理2件' in entry.message and '再確認' in entry.message and entry.level!='success'
            assert 'synthetic' not in entry.message
            view.close()
    finally:client.delete();source.close();owner.close()


@pytest.mark.asyncio
async def test_real_committed_with_unknown_refresh_is_not_success_color(tmp_path,monkeypatch):
    source,legacy,owner,_,_,archive=setup(tmp_path)
    log=QueueLog();client=Client(ui.page('/_ae_committed_recovery_log'))
    try:
        with client:
            view=DuplicateView(source,event_services=NS(shared_log=log))
            plan=select(source,{'2'})
            original_catalog=source._catalog
            calls=[]
            def unavailable():
                calls.append(1)
                if len(calls)==1:return original_catalog()
                raise OSError('synthetic private path')
            monkeypatch.setattr(source,'_catalog',unavailable)
            result,_=await view._execute_accepted(plan,uuid4().hex,True)
            assert len(result.committed)==1 and not result.failed and not result.unprocessed
            assert result.recovery_required
            entry,=log.snapshot()
            assert '1件を隔離' in entry.message and '再確認' in entry.message
            assert entry.level!='success' and 'synthetic' not in entry.message
            view.close()
    finally:client.delete();source.close();owner.close()
