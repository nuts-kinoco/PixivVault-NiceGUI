"""Actual NiceGUI component regressions, synthetic service only."""
import asyncio
from threading import Event
from nicegui import Client,ui,core,background_tasks
import pytest
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.personal_ranking_view import PersonalRankingView
from pixivvault_nicegui.ui.external_vault_view import ExternalVaultView
from test_external_vault import FixtureCrypto,FixtureTransport
from pixivvault_nicegui.external_vault import ExternalVaultService
from test_personal_ranking import raw
from test_ranking_ui import Pin

def setup(tmp_path):
    services=ApplicationServices(1);transport=FixtureTransport();transport.response={'illusts':[raw()],'next_url':'next'}
    vault=ExternalVaultService(tmp_path/'vault',crypto=FixtureCrypto(),transport=transport,clock=lambda:1000)
    vault.initialize('fixture-password','1','fixture-secret');services.external_vault=vault;pins=[]
    services.personal_ranking_context=lambda:{'hidden_work_ids':set(),'hidden_author_ids':set(),'downloaded_ids':set(),'hated_ids':set()}
    def acquire(records):pin=Pin(records);pins.append(pin);return pin
    services.acquire_ranking_images=acquire
    return services,vault,transport,pins

async def drain_personal_view(view, initial_task):
    # Run the real NiceGUI task, close the view first, then let callbacks drain
    # before the Client and its backing services are destroyed.
    if view is not None:view.close()
    tasks={task for task in (initial_task, getattr(view, '_context_task', None)) if task is not None}
    if tasks:await asyncio.gather(*tasks)
    await asyncio.sleep(0)  # allow NiceGUI's done callbacks to unregister tasks
    assert all(task.done() and task not in background_tasks.running_tasks for task in tasks)


@pytest.mark.asyncio
async def test_personal_actual_controls_append_filter_and_persistence(tmp_path,monkeypatch):
    monkeypatch.setattr(core,'loop',asyncio.get_running_loop())
    services,vault,transport,pins=setup(tmp_path);client=Client(ui.page('/_personal_test'))
    view=None;initial_task=None
    try:
        with client:
            view=PersonalRankingView(services);view.set_active(True);assert transport.calls==[]
            initial_task=view._context_task
            assert initial_task in background_tasks.running_tasks and initial_task.get_loop() is asyncio.get_running_loop()
            assert not view.r18.enabled
            view.add_tag('tag');assert view.selected_tags()==('tag',)
            view.tags.set_value('tag');await view.load();first=view.gallery.cards['123'];assert view.page.offset==1
            transport.response={'illusts':[raw(124)],'next_url':'next'};await view.load(append=True)
            assert view.gallery.cards['123'] is first and len(view.gallery.cards)==2 and pins[0].closed
            view.exclude.set_value('tag');view.local_changed();assert view.gallery.state.total_count==0
            assert view.page.offset==2 and view.gallery.more_cell.visible
            view.save();assert vault.ranking_preferences()['filters']['tags_exclude']==['tag']
            transport.response={'illusts':[]};await view.load(append=True)
            assert view.page.exhausted and not view.gallery.more_cell.visible and view.gallery.more_button.enabled
            view.close();assert pins[-1].closed
    finally:
        try:await drain_personal_view(view,initial_task)
        finally:client.delete();vault.close();services.close()

@pytest.mark.asyncio
async def test_personal_stale_and_close_drop_late_results(tmp_path,monkeypatch):
    monkeypatch.setattr(core,'loop',asyncio.get_running_loop())
    services,vault,transport,pins=setup(tmp_path);client=Client(ui.page('/_personal_stale'))
    view=None;initial_task=None;task=None
    entered,release=Event(),Event();original=transport.search
    def delayed(*args):entered.set();assert release.wait(3);return original(*args)
    transport.search=delayed
    try:
        with client:
            view=PersonalRankingView(services);view.set_active(True);view.tags.set_value('tag')
            initial_task=view._context_task
            assert initial_task in background_tasks.running_tasks and initial_task.get_loop() is asyncio.get_running_loop()
            task=asyncio.create_task(view.load());assert await asyncio.to_thread(entered.wait,3)
            view.changed();release.set();await task;assert view.page is None and pins==[] and not view.busy
            entered.clear();release.clear();task=asyncio.create_task(view.load());assert await asyncio.to_thread(entered.wait,3)
            view.close();release.set();await task;assert pins==[]
    finally:
        release.set()
        try:
            if view is not None:view.close()
            if task is not None:await task
            await drain_personal_view(view,initial_task)
        finally:client.delete();vault.close();services.close()

@pytest.mark.asyncio
async def test_external_status_lock_and_failed_password_are_safe(tmp_path):
    services,vault,transport,pins=setup(tmp_path);client=Client(ui.page('/_external_test'))
    try:
        with client:
            view=ExternalVaultView(services);assert transport.calls==[]
            view.lock();view.password.set_value('wrong');await view.action('unlock')
            assert view.password.value=='' and 'unlock-failed' in view.notice.text and not vault.status()['open']
            assert 'fixture-secret' not in view.notice.text and 'synthetic' not in view.slot_status.text
            view.close();assert not vault.status()['closed']
    finally:client.delete();vault.close();services.close()

def test_external_missing_crypto_disables_actions(tmp_path):
    class Missing(FixtureCrypto):available=False
    service=ApplicationServices(1);vault=ExternalVaultService(tmp_path/'missing',crypto=Missing());service.external_vault=vault
    client=Client(ui.page('/_external_missing'))
    try:
        with client:
            view=ExternalVaultView(service)
            assert not view.create_button.enabled and not view.unlock_button.enabled and not view.password.enabled
            assert 'cryptography' in view.notice.text
    finally:client.delete();vault.close();service.close()


def test_public_premium_r18_control_is_explicitly_unavailable(tmp_path):
    from pixivvault_nicegui.ranking_source import RankingSource
    from pixivvault_nicegui.ui.ranking_view import RankingView
    services,vault,transport,pins=setup(tmp_path);services.ranking_source=RankingSource(object(),vault)
    client=Client(ui.page('/_public_premium_controls'))
    try:
        with client:
            view=RankingView(services);view.tag.set_value('tag');view.changed()
            assert not view.r18.enabled and 'R-18 絞り込みを適用しません' in view.notice.text
            view.tag.set_value('');view.changed();assert view.r18.enabled
    finally:client.delete();vault.close();services.close()


def test_lock_shutdown_failure_is_visible_and_clears_input(tmp_path):
    from pixivvault_nicegui.external_vault import VaultError
    services,vault,transport,pins=setup(tmp_path);client=Client(ui.page('/_vault_lock_failure'))
    original=vault.lock
    def failed():raise VaultError('socket-shutdown-unverified')
    try:
        with client:
            view=ExternalVaultView(services)
            view.password.set_value('synthetic-password');view.secret.set_value('synthetic-secret')
            vault.lock=failed;view.lock()
            assert view.password.value=='' and view.secret.value==''
            assert '通信の遮断を確認できません' in view.notice.text
            assert 'synthetic' not in view.notice.text
    finally:
        vault.lock=original;client.delete();vault.close();services.close()


@pytest.mark.asyncio
async def test_poisoned_transport_offers_restart_only_and_disables_credentials(tmp_path):
    import errno
    from pixivvault_nicegui.external_vault_transport import ShaftTransport
    from test_external_vault import Request,profile

    class RefusingSocket:
        def dup(self):return self
        def shutdown(self,how):raise OSError(errno.EIO,'synthetic shutdown failure')
        def close(self):pass

    services,vault,unused,pins=setup(tmp_path)
    transport=ShaftTransport(Request([]),oauth_profiles=[profile()])
    vault.transport=transport
    client=Client(ui.page('/_vault_restart_required'))
    try:
        with client:
            view=ExternalVaultView(services)
            view.password.set_value('fixture-password')
            view.secret.set_value('private-fixture-secret')
            fence=transport._socket_fence
            with fence.register(RefusingSocket(),fence.snapshot()):
                view.lock()
            assert view.password.value=='' and view.secret.value==''
            assert '再起動' in view.notice.text
            assert '通信の遮断を確認できません' in view.notice.text
            assert 'private-fixture' not in view.notice.text
            for control in (view.password,view.unlock_button,view.lock_button,
                            view.create_button,view.refresh_button,view.auto_unlock):
                assert not control.enabled
            await view.action('unlock')  # forged/stale UI event must stay blocked
            assert '再起動' in view.notice.text and not vault.status()['open']
            assert not transport.request.calls
    finally:
        client.delete()
        services.external_vault=None  # prime owns app-level poison teardown
        services.close()
        if vault._owner is not None:
            vault._owner.__exit__(None,None,None)
            vault._owner=None


@pytest.mark.asyncio
async def test_shutdown_failure_during_async_action_retains_restart_guidance(tmp_path):
    import errno
    from pixivvault_nicegui.external_vault import VaultError
    from pixivvault_nicegui.external_vault_transport import ShaftTransport
    from test_external_vault import Request,profile

    services,vault,unused,pins=setup(tmp_path)
    transport=ShaftTransport(Request([]),oauth_profiles=[profile()])
    vault.transport=transport
    def interrupted_refresh():
        transport._socket_fence.mark_close_unverified(
            OSError(errno.EIO,'synthetic failure with no secret values'))
        raise VaultError('socket-shutdown-unverified')
    vault.refresh=interrupted_refresh
    client=Client(ui.page('/_vault_async_poison_notice'))
    try:
        with client:view=ExternalVaultView(services)
        await view.action('refresh')
        assert '通信の遮断を確認できません' in view.notice.text
        assert '再起動' in view.notice.text
        assert not view.refresh_button.enabled and not view.unlock_button.enabled
        assert 'synthetic failure' not in view.notice.text
        assert not transport.request.calls
    finally:
        client.delete()
        services.external_vault=None
        services.close()
        if vault._owner is not None:
            vault._owner.__exit__(None,None,None)
            vault._owner=None


@pytest.mark.asyncio
@pytest.mark.parametrize('source',['other-client','keeper'])
async def test_active_second_client_observes_remote_poison_without_manual_render(tmp_path,monkeypatch,source):
    """Both another UI client and a keeper can poison the same shared vault."""
    import errno
    from pixivvault_nicegui.external_vault_transport import ShaftTransport
    from test_external_vault import Request,profile

    monkeypatch.setattr(core,'loop',asyncio.get_running_loop())
    # This is an in-process Client fixture: its WebSocket handshake is absent.
    # Permit the actual NiceGUI timer loop to start as on a connected browser.
    async def connected_for_fixture(self,timeout=None):return None
    monkeypatch.setattr(Client,'connected',connected_for_fixture)
    monkeypatch.setattr(type(core.app),'is_stopping',property(lambda self:False))
    monkeypatch.setattr(type(core.app),'is_stopped',property(lambda self:False))
    services,vault,_,_=setup(tmp_path)
    transport=ShaftTransport(Request([]),oauth_profiles=[profile()])
    vault.transport=transport
    client_a=Client(ui.page('/_vault_client_a_'+source))
    client_b=Client(ui.page('/_vault_client_b_'+source))
    view_a=view_b=None
    try:
        with client_a:
            view_a=ExternalVaultView(services)
            view_a.set_active(True)
        with client_b:
            view_b=ExternalVaultView(services)
            view_b.set_active(True)
            view_b.password.set_value('synthetic-secret-password')
            view_b.secret.set_value('synthetic-secret-key')
        assert view_b.password.enabled and view_b.refresh_button.enabled
        if source=='other-client':
            class RefusingSocket:
                def dup(self):return self
                def shutdown(self,how):raise OSError(errno.EIO,'synthetic socket refusal')
                def close(self):pass
            fence=transport._socket_fence
            with fence.register(RefusingSocket(),fence.snapshot()):
                with client_a:view_a.lock()
        else:
            transport._socket_fence.mark_close_unverified(OSError(errno.EIO,'keeper failed to close'))

        # The B view is not rendered or tab-switched by the test. Only its timer
        # can observe the shared service transition on the NiceGUI event loop.
        for _ in range(20):
            if view_b.password.value=='' and '再起動' in view_b.notice.text:break
            await asyncio.sleep(.1)
        assert view_b.password.value=='' and view_b.secret.value==''
        assert '再起動' in view_b.notice.text
        assert 'synthetic-secret' not in view_b.notice.text
        for control in (view_b.password,view_b.unlock_button,view_b.lock_button,
                        view_b.create_button,view_b.refresh_button,view_b.auto_unlock):
            assert not control.enabled
        assert transport.request.calls==[]
        view_b.set_active(False)
        assert not view_b._status_timer.active
        view_b.set_active(True)
        assert not view_b._status_timer.active  # poisoned view need not keep polling
    finally:
        if view_a is not None:view_a.close()
        if view_b is not None:view_b.close()
        client_a.delete();client_b.delete()
        services.external_vault=None
        services.close()
        if vault._owner is not None:
            vault._owner.__exit__(None,None,None)
            vault._owner=None


@pytest.mark.asyncio
async def test_vault_observer_stops_on_inactive_tab_and_client_delete(tmp_path,monkeypatch):
    monkeypatch.setattr(core,'loop',asyncio.get_running_loop())
    async def connected_for_fixture(self,timeout=None):return None
    monkeypatch.setattr(Client,'connected',connected_for_fixture)
    monkeypatch.setattr(type(core.app),'is_stopping',property(lambda self:False))
    monkeypatch.setattr(type(core.app),'is_stopped',property(lambda self:False))
    services,vault,_,_=setup(tmp_path)
    reads=[]
    status=vault.status
    def observed():reads.append('read');return status()
    vault.status=observed
    client=Client(ui.page('/_vault_timer_delete'))
    try:
        with client:
            view=ExternalVaultView(services)
            view.set_active(True)
        await asyncio.sleep(.65)
        assert len(reads)>=3, 'active timer must actually read shared status'
        view.set_active(False)
        count=len(reads)
        await asyncio.sleep(.65)
        assert len(reads)==count, 'inactive view must not poll shared status'
        view.set_active(True)
        await asyncio.sleep(.65)
        assert len(reads)>count
        client.delete()  # invokes the registered on_delete callback
        assert view.closed and view._status_timer._is_canceled
        count=len(reads)
        await asyncio.sleep(.65)
        assert len(reads)==count, 'deleted client must not poll the vault'
    finally:
        if client.id in Client.instances:client.delete()
        vault.close();services.close()
