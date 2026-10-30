"""Read-only active managed receipt byte measurement."""
from types import SimpleNamespace
import pytest
from test_duplicate_inspection import create, Context
from pixivvault_nicegui.managed_downloads import ManagedDownloadError


def test_capacity_uses_actual_receipt_and_image_bytes_without_mutation(tmp_path):
    owner,_=create(tmp_path)
    try:
        relative,receipt,digest,_=owner.saved_receipts_with_dates()[0]
        expected=len((owner.root/relative).read_bytes())+sum(len(owner.read_saved_page(relative,i,digest)) for i in range(receipt['page_count']))
        assert owner.measure_active_receipt_bytes(relative,digest)==(receipt['author_id'],expected)
    finally:owner.close()


def test_capacity_refuses_changed_receipt_and_inactive_generation(tmp_path,monkeypatch):
    owner,_=create(tmp_path)
    try:
        relative,receipt,digest,_=owner.saved_receipts_with_dates()[0]
        with pytest.raises(ManagedDownloadError):owner.measure_active_receipt_bytes(relative,'f'*64)
        original=owner._generations.current
        calls=[]
        def changed(work_id):
            calls.append(work_id)
            return original(work_id) if len(calls)==1 else None
        monkeypatch.setattr(owner._generations,'current',changed)
        with pytest.raises(ManagedDownloadError):owner.measure_active_receipt_bytes(relative,digest)
        assert len(calls)>=2
    finally:owner.close()


def test_author_failures_do_not_turn_into_zero_or_discard_other_success():
    from pixivvault_nicegui.managed_capacity import measure_managed_capacities
    class Index:
        def entries(self):return {'1':{'sha256':'x'},'2':{'sha256':'y'}}
    class Owner:
        _generations=Index()
        def saved_receipts_with_dates(self):
            return (('one',{'author_id':'1'},'x',None),('two',{'author_id':'2'},'y',None))
        def measure_active_receipt_bytes(self,relative,digest):
            if relative=='two':raise OSError('synthetic failure')
            return '1',123
    values,failed,_=measure_managed_capacities(Owner(),('1','2','3'))
    assert values=={'1':123} and failed==('2',) and '3' not in values
    with pytest.raises(ValueError):measure_managed_capacities(Owner(),tuple(str(i) for i in range(51)))

@pytest.mark.asyncio
async def test_search_change_discards_pending_capacity_result(monkeypatch):
    import asyncio
    from threading import Event
    from nicegui import Client,ui
    from pixivvault_nicegui.services import ApplicationServices
    from pixivvault_nicegui.ui.library_view import LibraryView
    import pixivvault_nicegui.managed_capacity as capacity
    started,release=Event(),Event()
    class Index:
        def entries(self):return {'generation':'unchanged'}
    class Owner:
        _generations=Index()
    services=ApplicationServices(120);client=Client(ui.page('/_managed_capacity_stale'))
    try:
        with client:view=LibraryView(services)
        services.managed_downloads=Owner()
        def measure(owner,targets):
            started.set();release.wait(5)
            return ({targets[0]:123},(),{'generation':'unchanged'})
        monkeypatch.setattr(capacity,'measure_managed_capacities',measure)
        task=asyncio.create_task(view.measure_capacity())
        assert await asyncio.to_thread(started.wait,5)
        view.search('0003');release.set();await task
        assert 123 not in view.state.snapshot.author_capacities.values()
        assert not view._managed_capacities
        assert view.capacity_status.text=='' 
        assert not view._capacity_busy
    finally:
        release.set();client.delete();services.managed_downloads=None;services.close()


@pytest.mark.asyncio
async def test_real_saved_fixture_recalculates_displayed_managed_author(tmp_path):
    import asyncio
    from fastapi import FastAPI
    from nicegui import Client,ui
    from tools.p3_residuals_fixtures import create_services
    from test_p3_residuals_fixtures import wait_done
    from pixivvault_nicegui.ui.library_view import LibraryView
    services,_=create_services(FastAPI(),tmp_path/'data')
    client=Client(ui.page('/_managed_capacity_real'))
    try:
        job=await services.save_explicit_work('1','capacity-test-fixture')
        await asyncio.to_thread(wait_done,services.static_jobs,job)
        await services.refresh_library_snapshot()
        with client:view=LibraryView(services)
        row=next(author for author in view.state.visible_authors if author.author_id=='91')
        expected=0
        for relative,receipt,digest,_ in services.managed_downloads.saved_receipts_with_dates():
            if receipt['author_id']=='91':
                expected+=services.managed_downloads.measure_active_receipt_bytes(relative,digest)[1]
        assert expected>0
        snapshot=view.state.snapshot
        await view.measure_capacity()
        assert view.state.snapshot is snapshot
        assert view._managed_capacities['91']==expected
        assert any(e.props.get('data-testid')=='library-managed-capacity' for e in view.author_cards['91'].descendants())
        assert '管理保存' in view.capacity_status.text
    finally:client.delete();services.close()
