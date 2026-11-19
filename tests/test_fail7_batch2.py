"""R07 and Z04 original acceptance regressions, synthetic services only."""
import asyncio
import hashlib
import json
import logging
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from nicegui import Client, ui, core
from pixivvault_nicegui.recommendation_state import (
    RecommendationController, RecommendationPreferences, RecommendationSnapshot,
    RecommendationGroup, RecommendationWork, RecommendationError)
from pixivvault_nicegui.ui.recommend_view import RecommendView
from pixivvault_nicegui.ui.personal_ranking_view import PersonalRankingView
from pixivvault_nicegui.external_vault import VaultError
from test_external_vault_ui import setup, drain_personal_view


def observe(key, value):
    target=os.environ.get('PV_OBSERVATIONS')
    if target:
        path=Path(target)
        rows=json.loads(path.read_text('utf-8')) if path.exists() else {}
        rows[key]=value;path.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')


class SavedSuperLikes:
    def __init__(self,path):
        self.path=path
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE history(id TEXT,verdict TEXT)')
            db.execute("INSERT INTO history VALUES('123','superLike')")
    def load(self):
        with sqlite3.connect(self.path) as db:rows=db.execute('SELECT id FROM history WHERE verdict=?',('superLike',)).fetchall()
        works=tuple(RecommendationWork(wid,'合成作品','9','合成作者') for wid, in rows)
        return RecommendationSnapshot(sections=(('superlike',(RecommendationGroup('s','SuperLike',works),)),
            ('certain',(RecommendationGroup('c','好みの傾向',works),))),unavailable=())


def ids(data,kind):return [w.work_id for g in data['sections'].get(kind,()) for w in g.works]


@pytest.mark.asyncio
async def test_r07_actual_button_withdraws_only_shelf_and_survives_restart(tmp_path,monkeypatch):
    monkeypatch.setattr(core,'loop',asyncio.get_running_loop())
    source=SavedSuperLikes(tmp_path/'history.sqlite3');original=source.path.read_bytes()
    root=tmp_path/'preferences';controller=RecommendationController(source,RecommendationPreferences(root))
    client=Client(ui.page('/_fail7_r07'))
    try:
        with client:
            view=RecommendView(NS(recommendations=controller));view.active=True;await view.refresh()
            buttons=[b for b in view.body.descendants() if isinstance(b,ui.button) and b.text=='SuperLikeを取り下げる']
            observe('R07-before-click',dict(buttons=len(buttons),superlike=ids(view.data,'superlike'),certain=ids(view.data,'certain')))
            assert len(buttons)==1,'SuperLike withdrawal has no UI entry'
            callback=next(e.handler for e in buttons[0]._event_listeners.values() if e.type=='click')
            from nicegui import background_tasks
            pending=set(background_tasks.running_tasks)
            callback(NS())
            await asyncio.gather(*(set(background_tasks.running_tasks)-pending))
            result=dict(superlike=ids(view.data,'superlike'),certain=ids(view.data,'certain'),notice=view.notice.text,
                history_sha_unchanged=source.path.read_bytes()==original)
            observe('R07-after-click',result)
            assert result['superlike']==[] and result['certain']==['123']
            assert result['history_sha_unchanged']
            await view.refresh();assert ids(view.data,'superlike')==[]
            view.close()
    finally:client.delete();controller.close()
    reopened=RecommendationController(source,RecommendationPreferences(root))
    try:
        assert ids(reopened.refresh(),'superlike')==[]
        assert ids(reopened.snapshot(),'certain')==['123']
        assert source.path.read_bytes()==original
    finally:reopened.close()


def test_r07_stale_revision_does_not_withdraw_other_work(tmp_path):
    source=SavedSuperLikes(tmp_path/'history.sqlite3');c=RecommendationController(source,RecommendationPreferences(tmp_path/'preferences'))
    try:
        before=c.refresh();c.hide('work','987',True,before['revision'])
        with pytest.raises(RecommendationError):c.dismiss_superlike('123',before['revision'])
        assert ids(c.snapshot(),'superlike')==['123']
        with pytest.raises(RecommendationError):c.dismiss_superlike('987',c.snapshot()['revision'])
    finally:c.close()


@pytest.mark.asyncio
async def test_r07_readonly_has_disabled_withdrawal():
    class Source:
        def load(self):return RecommendationSnapshot(sections=(('superlike',(RecommendationGroup('s','s',(RecommendationWork('123'),)),)),))
    c=RecommendationController(Source());client=Client(ui.page('/_fail7_r07_readonly'))
    try:
        with client:
            view=RecommendView(NS(recommendations=c));view.active=True;await view.refresh()
            buttons=[b for b in view.body.descendants() if isinstance(b,ui.button) and b.text=='SuperLikeを取り下げる']
            assert len(buttons)==1 and not buttons[0].enabled
            view.close()
    finally:client.delete();c.close()


@pytest.mark.asyncio
async def test_z04_expired_payout_shows_reason_without_persisting_tokens(tmp_path,monkeypatch,caplog):
    monkeypatch.setattr(core,'loop',asyncio.get_running_loop())
    services,vault,transport,pins=setup(tmp_path);client=Client(ui.page('/_fail7_expired'));view=None;initial=None
    tokens_before={k:{f:v.get(f) for f in ('access_token','refresh_token','token_expires_at_ms')} for k,v in vault._snapshot()[1]['slots'].items()}
    original=transport.fetch_account
    def expired(*a,**kw):raise VaultError('expired-payout')
    transport.fetch_account=expired
    try:
        with client:
            view=PersonalRankingView(services);view.set_active(True);view.tags.set_value('tag');initial=view._context_task
            with caplog.at_level(logging.WARNING):await view.load()
            tokens_after={k:{f:v.get(f) for f in ('access_token','refresh_token','token_expires_at_ms')} for k,v in vault._snapshot()[1]['slots'].items()}
            assert tokens_after==tokens_before
            observe('Z04-expired',dict(tokens_unchanged=tokens_after==tokens_before,notice=view.notice.text,page=view.page is not None,logs=caplog.text,
                fetch_enabled=view.fetch_button.enabled))
            assert '失効' in view.notice.text and 'Shaft' in view.notice.text
            assert 'expired-payout' in caplog.text
            assert view.page is None
            # Failed payout must not replace the saved account/token state.
            transport.fetch_account=original
            await view.load();assert len(view.gallery.cards)==1
    finally:
        await drain_personal_view(view,initial);client.delete();vault.close();services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('append',[False,True],ids=['replace','append'])
@pytest.mark.parametrize('error',[TimeoutError('private-error-must-not-log'),ConnectionError('private-error-must-not-log')],ids=['timeout','connection'])
async def test_z04_network_zero_results_logs_and_local_library_continues(tmp_path,monkeypatch,caplog,error,append):
    from pixivvault_nicegui.ui.library_view import LibraryView
    monkeypatch.setattr(core,'loop',asyncio.get_running_loop())
    services,vault,transport,pins=setup(tmp_path);client=Client(ui.page('/_fail7_network'));view=None;initial=None;library=None
    try:
        with client:
            library=LibraryView(services)
            view=PersonalRankingView(services);view.set_active(True);view.tags.set_value('tag');initial=view._context_task
            await view.load();assert len(view.gallery.cards)==1
            def fail(*a,**kw):raise error
            transport.search=fail
            with caplog.at_level(logging.WARNING):await view.load(append=append)
            library.search('');library.reload()
            result=dict(notice=view.notice.text,remote_rows=len(view.page.rows) if view.page else None,
                remote_cards=len(view.gallery.cards),logs=caplog.text,local_error=library.load_error,
                local_search_enabled=library.search_input.enabled,fetch_enabled=view.fetch_button.enabled)
            observe('Z04-'+type(error).__name__+('-append' if append else '-replace'),result)
            assert result['remote_rows']==0 and result['remote_cards']==0
            assert 'transport-failed' in caplog.text and 'private-error-must-not-log' not in caplog.text
            assert not library.load_error and library.search_input.enabled
            assert view.fetch_button.enabled
            view.include.set_value('local');view.local_changed();assert view.page.rows==()
    finally:
        if library:library.close()
        await drain_personal_view(view,initial);client.delete();vault.close();services.close()


def test_z04_other_callers_keep_expiry_fallback():
    from pixivvault_nicegui.premium_read import PremiumSearch
    class V:
        def status(self):return dict(open=True,mode='nana7mi',transport_available=True)
        def search(self,*a,**kw):raise VaultError('expired-payout')
    class Reader:
        def require_identity(self):self._verified_identity = '1'; return self._verified_identity
        def search_illusts(self,*a,**kw):return {'works':[]}
    assert PremiumSearch(V(),Reader()).search('tag')['illusts']==[]


@pytest.mark.parametrize('code',['pixiv-network-stopped','premium-budget-unavailable','stale-session'])
def test_z04_stop_and_storage_errors_are_never_empty_success(code):
    from pixivvault_nicegui.personal_ranking import PersonalRankingSource,default_personal_filters
    class Search:
        def search(self,*a,**kw):raise VaultError(code)
    with pytest.raises(VaultError,match=code):PersonalRankingSource(Search()).fetch(('tag',),default_personal_filters())


@pytest.mark.asyncio
@pytest.mark.parametrize('following',['success','rate-limited','network-failed','network-stopped'])
async def test_z04_multi_uid_does_not_lose_expiry_reason(tmp_path,monkeypatch,caplog,following):
    from pixivvault_nicegui.external_vault import ExternalVaultService
    from test_external_vault import FixtureCrypto
    monkeypatch.setattr(core,'loop',asyncio.get_running_loop())
    services,old,transport,pins=setup(tmp_path);old.close()
    vault=ExternalVaultService(tmp_path/'multi-vault',crypto=FixtureCrypto(),transport=transport,clock=lambda:1000)
    vault.initialize('fixture-password','1\n2','fixture-secret');services.external_vault=vault
    original=transport.fetch_account
    def payout(uid,*a,**kw):
        if uid==1:raise VaultError('expired-payout')
        if following=='rate-limited':raise VaultError(following)
        return original(uid,*a,**kw)
    transport.fetch_account=payout
    if following=='network-failed':
        def fail(*a,**kw):raise TimeoutError('private-failure')
        transport.search=fail
    if following=='network-stopped':
        def stop(*a,**kw):raise VaultError('pixiv-network-stopped')
        transport.search=stop
    client=Client(ui.page('/_fail7_multi'));view=None;initial=None
    try:
        with client:
            view=PersonalRankingView(services);view.set_active(True);view.tags.set_value('tag');initial=view._context_task
            with caplog.at_level(logging.WARNING):await view.load()
            observe('Z04-multi-'+following,dict(notice=view.notice.text,logs=caplog.text,
                remote_rows=len(view.page.rows) if view.page else None))
            assert '失効' in view.notice.text and 'expired-payout' in caplog.text
            assert not vault._snapshot()[1]['slots']['1'].get('access_token')
            if following=='success':assert len(view.page.rows)==1
            elif following=='network-failed':assert view.page.rows==() and view.page.remote_error=='transport-failed'
            else:assert view.page is None
            if following=='network-stopped':assert '停止状態' in view.notice.text
    finally:
        await drain_personal_view(view,initial);client.delete();vault.close();services.close()
