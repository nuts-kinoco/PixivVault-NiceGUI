"""CoS regressions for interactive readiness and acknowledged preference drafts."""
import asyncio
from threading import Event
from types import SimpleNamespace
import pytest
from nicegui import Client,ui
from pixivvault_nicegui.ui.recommend_view import RecommendView
from pixivvault_nicegui.ui.preferences_view import PreferencesView
from pixivvault_nicegui.preferences import PreferencesStore


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',[False,True])
async def test_mutation_controls_are_disabled_until_worker_and_render_finish(failure):
    client=Client(ui.page('/_p3_busy_regression'))
    entered,release=Event(),Event()
    try:
        with client:
            view=RecommendView(SimpleNamespace(recommendations=object()))
            with view.body:
                permitted=ui.button('permitted')
                forbidden=ui.button('unavailable').set_enabled(False)
        view.active=True
        view.data={'can_acquire':True}
        def render():
            assert view.busy and not permitted.enabled
        view.render=render
        def operation():
            entered.set();release.wait(5)
            if failure:raise ValueError('synthetic worker failure')
            return {'can_acquire':True}
        task=asyncio.create_task(view._change(operation))
        try:
            assert await asyncio.to_thread(entered.wait,5)
            assert not permitted.enabled and not forbidden.enabled
            assert not view.update_button.enabled and not view.refresh_button.enabled
            assert view.root.props['data-busy']=='true'
        finally:release.set()
        await task
        assert permitted.enabled
        assert not forbidden.enabled
        assert view.update_button.enabled and view.refresh_button.enabled
        assert view.root.props['data-busy']=='false'
    finally:release.set();client.delete()


def rendered_data():
    from pixivvault_nicegui.recommendation_state import RecommendationGroup,RecommendationWork
    group=RecommendationGroup('tag','Tag',tuple(RecommendationWork(str(n),'Work','91','Author') for n in range(1,21)),1,40)
    return dict(revision=1,can_acquire=True,writable=False,order=('certain',),collapsed=(),
        unavailable=(),sections={'certain':(group,)},hidden_works=(),hidden_authors=(),boosted=())


@pytest.mark.asyncio
async def test_real_render_relocks_new_buttons_and_update_then_restores_capabilities():
    client=Client(ui.page('/_p3_real_render'))
    try:
        with client:view=RecommendView(SimpleNamespace(recommendations=object()))
        view.active=True;view.data=rendered_data();view.visible[('certain','tag')]=24
        view.render()
        baseline=[(b.text,b.enabled) for b in view.body.descendants() if isinstance(b,ui.button)]
        assert any(enabled for _,enabled in baseline) and any(not enabled for _,enabled in baseline)
        old_ids={b.id for b in view.body.descendants() if isinstance(b,ui.button)}
        view._set_busy(True)
        # Do not stub render or call its re-lock helper: deleting production's
        # render tail must expose enabled buttons and fail these assertions.
        view.render()
        new=[b for b in view.body.descendants() if isinstance(b,ui.button)]
        assert new and old_ids.isdisjoint({b.id for b in new})
        assert all(not b.enabled for b in new)
        assert not view.update_button.enabled
        view._set_busy(False)
        assert [(b.text,b.enabled) for b in new]==baseline
        assert view.update_button.enabled
    finally:client.delete()


@pytest.mark.asyncio
async def test_preference_ack_is_draft_and_saved_values_survive_restart(tmp_path):
    path=tmp_path/'preferences.sqlite3';store=PreferencesStore(path)
    client=Client(ui.page('/_p3_preference_ack'));applied=[]
    try:
        with client:
            view=PreferencesView(store,enabled_keys={'novel_save_format','ugoira_save_format','pixiv_shared_image_cache_size_mb'},on_change=applied.append)
            view.open()
        desired={'novel_save_format':'both','ugoira_save_format':'folder','pixiv_shared_image_cache_size_mb':'512'}
        for key,value in desired.items():
            view.controls[key].set_value(value)
            assert view.controls[key].props['data-value']==value
        assert store.snapshot().revision==0
        assert store.get_setting('pixiv_shared_image_cache_size_mb')=='1024'
        assert view.save()
        assert store.snapshot().revision==1
        assert len(applied)==1
    finally:client.delete();store.close()
    reopened=PreferencesStore(path)
    try:
        assert reopened.snapshot().revision==1
        for key,value in desired.items():assert reopened.get_setting(key)==value
    finally:reopened.close()


@pytest.mark.asyncio
async def test_busy_undo_keeps_request_and_failed_undo_does_not_discard_it():
    client=Client(ui.page('/_p3_undo'))
    def fail(*args):raise ValueError('synthetic CAS failure')
    try:
        with client:view=RecommendView(SimpleNamespace(recommendations=SimpleNamespace(hide=fail)))
        view.active=True;view.data=rendered_data();view.data['revision']=2;view.undo=('work','1',1)
        view._set_busy(True)
        assert not view.undo_button.enabled
        await view.undo_hide()
        assert view.undo==('work','1',1)
        view._set_busy(False)
        await view.undo_hide()
        assert view.undo==('work','1',1)
    finally:client.delete()


@pytest.mark.asyncio
async def test_reenter_during_worker_schedules_readonly_refresh(monkeypatch):
    from pixivvault_nicegui.ui import recommend_view
    client=Client(ui.page('/_p3_reenter'));entered,release=Event(),Event();scheduled=[]
    def fetch():entered.set();release.wait(5);return rendered_data()
    monkeypatch.setattr(recommend_view.background_tasks,'create',scheduled.append)
    try:
        with client:view=RecommendView(SimpleNamespace(recommendations=SimpleNamespace(refresh=fetch)))
        view.active=True
        task=asyncio.create_task(view.refresh())
        try:
            assert await asyncio.to_thread(entered.wait,5)
            view.set_active(False);view.set_active(True)
        finally:release.set()
        await task
        assert len(scheduled)==1
        await scheduled.pop()
        assert view.data['revision']==1 and not view.busy
    finally:
        release.set()
        for coroutine in scheduled:coroutine.close()
        client.delete()


@pytest.mark.asyncio
async def test_propagated_busy_start_failure_recovers_without_dispatch(monkeypatch):
    client=Client(ui.page('/_p3_ui_propagated_exception'));calls=[];injected=[]
    try:
        with client:view=RecommendView(SimpleNamespace(recommendations=object()))
        view.active=True;original=view._set_busy
        def fail_start(value, **kwargs):
            # Preserve the real _set_busy keyword-only status-text contract;
            # only inject after the busy transition actually occurred.
            original(value, **kwargs)
            if value:
                injected.append('busy-start')
                raise RuntimeError('synthetic propagated busy-start failure')
        # Inject at the operation boundary, not an observable callback whose
        # exception NiceGUI deliberately routes to app.handle_exception.
        monkeypatch.setattr(view,'_set_busy',fail_start)
        accepted=await view._change(lambda:calls.append('must not dispatch'))
        assert injected==['busy-start']
        assert accepted is False and not view.busy and not calls
        assert view.root.props['data-busy']=='false'
        assert view.refresh_button.enabled
    finally:client.delete()


@pytest.mark.asyncio
async def test_observer_update_failure_is_reported_and_operation_can_complete(monkeypatch):
    from nicegui import core
    client=Client(ui.page('/_p3_ui_observer_exception'));calls=[];reported=[]
    try:
        with client:view=RecommendView(SimpleNamespace(recommendations=object()))
        view.active=True;original=view.root.update
        monkeypatch.setattr(core.app,'handle_exception',reported.append)
        def fail_once():
            monkeypatch.setattr(view.root,'update',original)
            raise RuntimeError('synthetic observer update failure')
        monkeypatch.setattr(view.root,'update',fail_once)
        def operation():
            calls.append('accepted operation')
            return rendered_data()
        accepted=await view._change(operation)
        assert len(reported)==1
        assert isinstance(reported[0],RuntimeError)
        assert str(reported[0])=='synthetic observer update failure'
        assert calls==['accepted operation'] and accepted is True
        assert view.data['revision']==1
        assert not view.busy and view.root.props['data-busy']=='false'
        assert view.refresh_button.enabled
    finally:client.delete()


def test_page2_check_rejects_late_duplicate_fetch():
    from tools.check_p3_residuals_browser import assert_page2_result
    before={'calls':[]}
    after={'calls':[['search','landscape',2,'all']], 'recommendation':{'groups':{'certain':[
        {'key':'landscape','page':2,'works':[str(n) for n in range(1020,1060)]}]}}}
    assert_page2_result(before,after)
    after['calls'].append(['search','landscape',2,'all'])
    with pytest.raises(AssertionError):assert_page2_result(before,after)
