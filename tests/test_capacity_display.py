"""L12 managed-only measurements must not replace whole-author capacity."""
import asyncio
from threading import Event
from types import SimpleNamespace
import pytest
from nicegui import Client,ui
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.library_view import LibraryView
from pixivvault_nicegui.library_models import LibrarySnapshot
from pixivvault_nicegui.library_state import LibraryState
import pixivvault_nicegui.managed_capacity as capacity

@pytest.mark.asyncio
async def test_measured_subset_preserves_full_author_capacity_sort_and_zero(monkeypatch):
    services=ApplicationServices(120);client=Client(ui.page('/_capacity_display'))
    try:
        with client:view=LibraryView(services)
        services.managed_downloads=SimpleNamespace(_generations=SimpleNamespace(entries=lambda:{}))
        old=view.state.snapshot
        ids=[a.author_id for a in old.authors]
        whole={aid:9_000_000-i*1000 for i,aid in enumerate(ids)}
        view.state=LibraryState(LibrarySnapshot(old.works,whole,old.author_names));view.sort('capacity')
        snapshot=view.state.snapshot;order=tuple(a.author_id for a in view.state.visible_authors)
        def measure(owner,targets):return {targets[0]:0,targets[1]:1784},(),{}
        monkeypatch.setattr(capacity,'measure_managed_capacities',measure)
        await view.measure_capacity()
        assert view.state.snapshot is snapshot
        assert dict(view.state.snapshot.author_capacities)==whole
        assert tuple(a.author_id for a in view.state.visible_authors)==order
        assert view._managed_capacities=={order[0]:0,order[1]:1784}
        texts=[getattr(e,'text','') for e in view.author_cards[order[0]].descendants()]
        assert '管理保存 0 B' in texts
        view.reload(preserve=True)
        assert view._managed_capacities=={} and view.capacity_status.text==''
    finally:client.delete();services.managed_downloads=None;services.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('action',['subview','inactive','reload','close'])
@pytest.mark.parametrize('worker_fails',[False,True])
async def test_invalidated_worker_cannot_leave_progress_or_late_status(monkeypatch,action,worker_fails):
    services=ApplicationServices(120);client=Client(ui.page('/_capacity_invalidated'))
    entered,release=Event(),Event();task=None
    try:
        with client:view=LibraryView(services)
        services.managed_downloads=SimpleNamespace(_generations=SimpleNamespace(entries=lambda:{}))
        def measure(owner,targets):
            entered.set();release.wait(5)
            if worker_fails:raise OSError('fixture failure')
            return {targets[0]:123},(),{}
        monkeypatch.setattr(capacity,'measure_managed_capacities',measure)
        task=asyncio.create_task(view.measure_capacity())
        assert await asyncio.to_thread(entered.wait,5)
        assert '再計測しています' in view.capacity_status.text
        if action=='subview':view.show_subview('bookmarks');view.show_subview('downloaded')
        elif action=='inactive':view.set_active(False);view.set_active(True)
        elif action=='reload':view.reload(preserve=True)
        else:view.close()
        assert view.capacity_status.text==''
        release.set();await task
        assert view.capacity_status.text=='' and view._managed_capacities=={}
        assert not view._capacity_busy
    finally:
        release.set()
        if task is not None:await task
        client.delete();services.managed_downloads=None;services.close()
