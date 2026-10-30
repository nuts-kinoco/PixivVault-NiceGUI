"""CPU-only synthetic UI contract for explicit, generation-bound similarity work."""
import asyncio
from threading import Event, get_ident
from types import SimpleNamespace

import pytest
from nicegui import Client, core, ui

from pixivvault_nicegui.ui.similar_view import SimilarView


def click(view):
    return SimpleNamespace(args={
        'token': view._analysis_token,
        'work': view.origin[0],
        'page': view.origin[2],
        'generation': view.generation,
    })


class EmptySource:
    def __init__(self):
        self.calls = []

    def search(self, work, page):
        self.calls.append(('search', work, page))
        return {'status': 'no_query_embedding', 'results': [], 'pool_size': 0}

    def search_author(self, candidates, representative):
        self.calls.append(('author', candidates, representative))
        return candidates[1], {'status': 'no_query_embedding', 'results': [], 'pool_size': 0}


@pytest.mark.asyncio
async def test_analysis_requires_exact_one_use_dom_token_and_only_clicked_page():
    source=EmptySource();done=[];observations=[];main_thread=get_ident()
    def analyze(wid,page,*,is_current):
        observations.append((wid,page,get_ident()!=main_thread,is_current()))
        done.append((wid,page))
    client=Client(ui.page('/_similar_gpu_token'))
    try:
        with client:
            view=SimilarView(source,lambda *args:None,analyze=analyze)
        await view.show('101','First',2)
        assert source.calls==[('search','101',2)]
        assert view._analysis_token and view.analyze_button.visible
        valid=click(view)
        assert await view.analyze_current(SimpleNamespace(args={**valid.args,'page':3})) is False
        assert await view.analyze_current(SimpleNamespace(args={**valid.args,'token':'foreign'})) is False
        assert done==[]
        assert await view.analyze_current(valid) is True
        assert observations==[('101',2,True,True)]
        assert done==[('101',2)] and len(source.calls)==2
        assert await view.analyze_current(valid) is False
        assert done==[('101',2)]
    finally:client.delete()


@pytest.mark.asyncio
async def test_inflight_click_busy_duplicate_hide_and_other_show_invalidate():
    source=EmptySource();entered=Event();release=Event();checks=[];done=[]
    def analyze(wid,page,*,is_current):
        entered.set()
        assert release.wait(5)
        checks.append(is_current())
        done.append((wid,page))
    a=Client(ui.page('/_similar_gpu_race'))
    task=None
    try:
        with a:view=SimilarView(source,lambda *args:None,analyze=analyze)
        await view.show('101','First',1)
        event=click(view)
        task=asyncio.create_task(view.analyze_current(event))
        assert await asyncio.to_thread(entered.wait,3)
        assert await view.analyze_current(event) is False
        assert not view.analyze_button.enabled
        await view.show('102','Second',2)
        assert await view.analyze_current(event) is False
        view.set_active(False)
        release.set()
        assert await task is False
        assert checks==[False] and done==[('101',1)]
        assert not view.dialog.value
        view.set_active(True)
        await view.show('102','Second',2)
        assert view._analysis_token and view._analysis_token != event.args['token']
    finally:
        release.set()
        if task is not None:await task
        a.delete()


@pytest.mark.asyncio
async def test_error_requires_fresh_explicit_retry_and_client_token_isolation():
    src=EmptySource();attempts=[]
    def analyze(wid,page,*,is_current):
        attempts.append((wid,page,is_current()))
        if len(attempts)==1:raise RuntimeError('synthetic unavailable')
    first=Client(ui.page('/_similar_gpu_client_first'))
    second=Client(ui.page('/_similar_gpu_client_second'))
    try:
        with first:a=SimilarView(src,lambda *args:None,analyze=analyze)
        with second:b=SimilarView(src,lambda *args:None,analyze=analyze)
        await a.show('1',page=1)
        await b.show('2',page=2)
        bad=click(b)
        assert await a.analyze_current(bad) is False
        prior=click(a)
        assert await a.analyze_current(prior) is False
        assert '再試行' in a.notice.text
        assert a.analyze_button.visible and a.analyze_button.enabled
        assert await a.analyze_current(prior) is False
        fresh=click(a)
        assert fresh.args['token']!=prior.args['token']
        assert await a.analyze_current(fresh) is True
        assert attempts==[('1',1,True),('1',1,True)]
    finally:
        first.delete();second.delete()


@pytest.mark.asyncio
async def test_bulk_analysis_is_explicit_optional_and_progress_provider_truthful(monkeypatch):
    monkeypatch.setattr(core,'loop',asyncio.get_running_loop())
    async def connected(self,timeout=None):return None
    monkeypatch.setattr(Client,'connected',connected)
    calls=[]
    state={'phase':'idle','indexed':0,'resumed':0,'errors':0,'examined':0,
           'provider':None,'requested':'auto','fallback_reason':None}
    def start():calls.append('start');state.update(phase='running',examined=10,indexed=5,
                                                     provider='cpu',fallback_reason='EgaraBackendUnavailable')
    def pause():calls.append('pause');state.update(phase='paused')
    def resume():calls.append('resume');state.update(phase='running',resumed=1)
    client=Client(ui.page('/_similar_gpu_bulk'))
    try:
        with client:
            view=SimilarView(EmptySource(),lambda *args:None,
                analysis_status=lambda:dict(state),start_analysis=start,
                pause_analysis=pause,resume_analysis=resume)
        assert calls==[] and view._status_timer is None
        await view.show('101',page=1)
        assert calls==[] and '未実行' in view.analysis_text.text
        await view.refresh_analysis_status()
        assert '自動' in view.analysis_text.text and '未実行' in view.analysis_text.text
        assert '実行先候補' not in view.analysis_text.text
        assert await view.run_analysis_control('start',view.control_event('start'))
        assert calls==['start'] and '実行中' in view.analysis_text.text
        assert '解析済み 5' in view.analysis_text.text
        assert '実行先 CPU' in view.analysis_text.text
        assert 'フォールバック' in view.analysis_text.text
        assert 'EgaraBackendUnavailable' not in view.analysis_text.text
        assert await view.run_analysis_control('pause',view.control_event('pause'))
        assert calls==['start','pause'] and '一時停止中' in view.analysis_text.text
        assert await view.run_analysis_control('resume',view.control_event('resume'))
        assert calls==['start','pause','resume']
        view.set_active(False)
        assert view._status_timer is None or view._status_timer._is_canceled
        assert not view.dialog.value
        view.close()
        assert view._status_timer is None
    finally:client.delete()


@pytest.mark.asyncio
async def test_idle_after_real_cuda_analysis_is_waiting_with_actual_gpu_and_no_noop_start():
    source=EmptySource()
    state={'phase':'idle','started':True,'indexed':4,'resumed':1,'errors':0,'examined':5,
           'provider':'cuda','requested':'auto','fallback_reason':None}
    starts=[]
    client=Client(ui.page('/_similar_gpu_completed_idle'))
    try:
        with client:
            view=SimilarView(source,lambda *args:None,
                analysis_status=lambda:dict(state),
                start_analysis=lambda:starts.append('unexpected start'),
                pause_analysis=lambda:None,resume_analysis=lambda:None)
        await view.show('11',page=1)
        assert '待機中' in view.analysis_text.text
        assert '実行先 GPU (CUDA)' in view.analysis_text.text
        assert '解析済み 4' in view.analysis_text.text
        assert '未実行' not in view.analysis_text.text
        assert view.start_button.enabled and view.pause_button.enabled
        assert view.start_button.text=='新しい保存を確認'
        assert await view.run_analysis_control('start',view.control_event('start'))
        assert starts==['unexpected start']  # service handles this as wake()
    finally:client.delete()


@pytest.mark.asyncio
async def test_idle_after_start_with_no_pages_is_waiting_and_start_noop_not_offered():
    state={'phase':'idle','started':False,'indexed':0,'resumed':0,'errors':0,'examined':0,
           'provider':None,'requested':'cuda','fallback_reason':None}
    calls=[]
    def start():
        calls.append('start')
        state['started']=True  # worker has started, no pages yet
    client=Client(ui.page('/_similar_gpu_empty_started_idle'))
    try:
        with client:
            view=SimilarView(EmptySource(),lambda *args:None,
                analysis_status=lambda:dict(state),start_analysis=start)
        await view.show('11',page=1)
        assert '未実行' in view.analysis_text.text
        assert not '実行先 GPU (CUDA)' in view.analysis_text.text
        assert await view.run_analysis_control('start',view.control_event('start'))
        assert calls==['start']
        assert '待機中' in view.analysis_text.text
        assert '未実行' not in view.analysis_text.text
        assert view.start_button.text=='新しい保存を確認'
        assert view.start_button.enabled
        assert await view.run_analysis_control('start',view.control_event('start'))
        assert calls==['start','start']  # service start() wakes the existing worker
    finally:client.delete()


@pytest.mark.asyncio
async def test_started_empty_idle_from_another_view_allows_pause_without_fake_provider():
    state={'phase':'idle','started':True,'indexed':0,'resumed':0,'errors':0,
           'examined':0,'provider':None,'requested':'auto','fallback_reason':None}
    calls=[]
    def pause():calls.append('pause');state['phase']='paused'
    client=Client(ui.page('/_similar_gpu_other_view_idle'))
    try:
        with client:
            view=SimilarView(EmptySource(),lambda *args:None,
                analysis_status=lambda:dict(state),start_analysis=lambda:None,
                pause_analysis=pause)
        await view.show('11',page=1)
        assert '待機中' in view.analysis_text.text and '未実行' not in view.analysis_text.text
        assert '実行先 未確定（モデル未ロード）' in view.analysis_text.text
        assert view.pause_button.enabled
        assert await view.run_analysis_control('pause',view.control_event('pause'))
        assert calls==['pause'] and '一時停止中' in view.analysis_text.text
    finally:client.delete()


@pytest.mark.asyncio
async def test_paused_and_resumed_idle_preserve_cpu_fallback_and_requested_auto():
    state={'phase':'paused','started':True,'indexed':3,'resumed':2,'errors':1,'examined':6,
           'provider':'cpu','requested':'auto','fallback_reason':'RuntimeError'}
    calls=[]
    def resume():calls.append('resume');state['phase']='idle'
    client=Client(ui.page('/_similar_gpu_pause_idle'))
    try:
        with client:
            view=SimilarView(EmptySource(),lambda *args:None,
                analysis_status=lambda:dict(state),start_analysis=lambda:calls.append('start'),
                pause_analysis=lambda:calls.append('pause'),resume_analysis=resume)
        await view.show('11',page=1)
        assert '一時停止中' in view.analysis_text.text
        assert '実行先 CPU' in view.analysis_text.text
        assert 'フォールバック' in view.analysis_text.text
        assert 'RuntimeError' not in view.analysis_text.text
        assert view.resume_button.enabled and not view.start_button.enabled
        assert await view.run_analysis_control('resume',view.control_event('resume'))
        assert calls==['resume']
        assert '待機中' in view.analysis_text.text and '実行先 CPU' in view.analysis_text.text
        assert view.start_button.enabled and view.pause_button.enabled
    finally:client.delete()


@pytest.mark.asyncio
async def test_unknown_provider_fields_are_never_echoed_and_deleted_client_invalidates_token():
    raw='private fixture token not a fixed public label'
    state={'phase':'running','indexed':0,'resumed':0,'errors':0,'examined':1,
           'provider':raw,'requested':raw,'fallback_reason':raw}
    client=Client(ui.page('/_similar_gpu_private_provider'))
    try:
        with client:
            view=SimilarView(EmptySource(),lambda *args:None,
                analysis_status=lambda:dict(state),analyze=lambda *a,**k:None)
        await view.show('3',page=2)
        assert raw not in view.analysis_text.text
        assert '実行先' in view.analysis_text.text
        old=click(view)
        # A missing Client.instances entry must invalidate an old browser
        # click even before NiceGUI's on_delete callback drains.
        del Client.instances[client.id]
        assert not view._valid()
        assert not await view.analyze_current(old)
    finally:
        Client.instances[client.id]=client
        client.delete()


def test_nicegui_click_js_uses_current_target_and_published_one_use_token():
    client=Client(ui.page('/_similar_gpu_dom_event'))
    try:
        with client:view=SimilarView(EmptySource(),lambda *args:None,analyze=lambda *a,**k:None)
        listeners=lambda widget:[item for item in widget._event_listeners.values()
                                  if item.type=='click' and item.js_handler]
        assert len(listeners(view.analyze_button))==1
        assert listeners(view.analyze_button)[0].js_handler.count('event.currentTarget')==1
        for button in (view.start_button,view.pause_button,view.resume_button):
            assert len(listeners(button))==1
            assert 'event.currentTarget' in listeners(button)[0].js_handler
    finally:client.delete()


@pytest.mark.asyncio
async def test_unconfigured_bulk_never_starts_provider_on_init_or_show():
    client=Client(ui.page('/_similar_gpu_disabled'))
    try:
        with client:view=SimilarView(EmptySource(),lambda *args:None)
        assert not view.start_button.enabled
        await view.show('17',page=1)
        assert '未実行' in view.analysis_text.text
        assert not view.start_button.enabled
        assert not await view.run_analysis_control('start',SimpleNamespace(args={}))
    finally:client.delete()


@pytest.mark.asyncio
async def test_dialog_hide_and_client_delete_revoke_tokens_without_starting_work():
    source=EmptySource();requests=[]
    def analyze(wid,page,*,is_current):requests.append((wid,page))
    client=Client(ui.page('/_similar_gpu_hide'))
    try:
        with client:
            view=SimilarView(source,lambda *args:None,analyze=analyze)
        await view.show('101',page=3)
        stale=click(view)
        view.dialog.close()
        view._hide()  # Actual browser emits this after Escape/outside close.
        assert view._analysis_token is None
        assert not await view.analyze_current(stale)
        await view.show('202',page=1)
        fresh=click(view)
        assert fresh.args['token']!=stale.args['token']
        assert not await view.analyze_current(stale)
        client.delete()
        assert view.closed and view._analysis_token is None
        assert not await view.analyze_current(fresh)
        assert requests==[]
    finally:
        if client.id in Client.instances:client.delete()


@pytest.mark.asyncio
async def test_new_page_waits_for_old_analysis_and_then_gets_a_fresh_token():
    source=EmptySource();entered=Event();release=Event();current_checks=[]
    def analyze(wid,page,*,is_current):
        entered.set()
        assert release.wait(5)
        current_checks.append(is_current())
    client=Client(ui.page('/_similar_gpu_new_show'))
    task=None
    try:
        with client:view=SimilarView(source,lambda *args:None,analyze=analyze)
        await view.show('101',page=1)
        old=click(view)
        task=asyncio.create_task(view.analyze_current(old))
        assert await asyncio.to_thread(entered.wait,3)
        await view.show('102',page=2)
        assert view._analysis_token is None and not view.analyze_button.enabled
        release.set()
        assert await task is False and current_checks==[False]
        assert view.origin==('102','',2)
        assert view._analysis_token and view._analysis_token!=old.args['token']
        assert not await view.analyze_current(old)
    finally:
        release.set()
        if task is not None:await task
        client.delete()


@pytest.mark.asyncio
@pytest.mark.parametrize('reason,expected',[
    ('EgaraBackendUnavailable','GPUを利用できない'),
    ('RuntimeError','GPU実行中の問題'),
    ('Fail','GPUの初期化または実行'),
    ('RuntimeException','GPUの初期化または実行'),
    ('EPFail','GPU実行プロバイダー'),
    ('InvalidArgument','GPU推論の入力'),
    ('ModuleNotFoundError','GPU関連モジュール'),
    ('ImportError','GPU関連モジュール'),
    ('OSError','GPU関連ライブラリ'),
    ('SyntheticPrivateError','GPUからCPUへ切替'),
])
async def test_cuda_cpu_fallback_uses_bounded_fixed_public_label(reason,expected):
    state={'phase':'idle','started':True,'indexed':2,'resumed':4,'errors':1,'examined':7,
           'provider':'cpu','requested':'auto','fallback_reason':reason}
    client=Client(ui.page('/_similar_fallback_'+reason))
    try:
        with client:
            view=SimilarView(EmptySource(),lambda *args:None,
                             analysis_status=lambda:dict(state))
        await view.show('17',page=1)
        shown=view.analysis_text.text
        assert '実行先 CPU' in shown and '希望 自動' in shown
        assert 'フォールバック' in shown and expected in shown
        assert reason not in shown, 'raw exception class and private error names must not escape'
        assert 'ワーカー累計' in shown and '今回の一括解析' not in shown
    finally:client.delete()


@pytest.mark.asyncio
async def test_idle_without_fallback_reports_accumulated_totals_and_exact_cuda():
    state={'phase':'idle','started':True,'indexed':12,'resumed':23,'errors':5,'examined':40,
           'provider':'cuda','requested':'cuda','fallback_reason':None}
    client=Client(ui.page('/_similar_cumulative_cuda'))
    try:
        with client:
            view=SimilarView(EmptySource(),lambda *args:None,
                             analysis_status=lambda:dict(state))
        await view.show('17',page=1)
        shown=view.analysis_text.text
        assert 'ワーカー累計' in shown and '今回の一括解析' not in shown
        assert '解析済み 12' in shown and '再開 23' in shown
        assert '待機中' in shown and '実行先 GPU (CUDA)' in shown
        assert 'フォールバック' not in shown
    finally:client.delete()
