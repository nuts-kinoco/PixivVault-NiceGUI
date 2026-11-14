"""CONT review regressions; synthetic credentials and no real network."""
from dataclasses import replace
from urllib.parse import parse_qs
from uuid import uuid4
import pytest
from required_network_guard import no_network
from test_original_author_follow import action_stack
from pixivvault_nicegui.external_vault_transport import ShaftTransport, OAuthProfile, VaultResponse
from pixivvault_nicegui.pixiv_write_client import WriteIntent, WriteHTTPResponse
from pixivvault_nicegui.native_profile import NativeProfileStore, NativeProfileError, native_kwargs, plan_storage
from pixivvault_nicegui.preferences import PreferencesStore


def test_918_profile_is_deprioritized_in_process_only(no_network):
    calls=[]
    profiles=tuple(OAuthProfile('test-agent','test-os','1','1','test-salt',p,'test-secret') for p in ('ios','android'))
    def request(*args,body=None,**kwargs):
        client=parse_qs(body.decode())['client_id'][0];calls.append(client)
        return (VaultResponse(403,{'errors':{'system':{'code':918}}}) if client=='ios'
                else VaultResponse(200,{'access_token':'synthetic-access','expires_in':3600}))
    transport=ShaftTransport(request,oauth_profiles=profiles)
    transport._refresh_oauth('synthetic-refresh');transport._refresh_oauth('synthetic-refresh')
    assert calls==['ios','android','android']
    calls.clear()
    ShaftTransport(request,oauth_profiles=profiles)._refresh_oauth('synthetic-refresh')
    assert calls==['ios','android']


def test_other_oauth_rejections_do_not_change_profile_order(no_network):
    calls=[]
    profiles=tuple(OAuthProfile('test-agent','test-os','1','1','test-salt',p,'test-secret') for p in ('ios','android'))
    def request(*args,body=None,**kwargs):
        client=parse_qs(body.decode())['client_id'][0];calls.append(client)
        return (VaultResponse(403,{'error':{'code':999}}) if client=='ios'
                else VaultResponse(200,{'access_token':'synthetic-access','expires_in':3600}))
    transport=ShaftTransport(request,oauth_profiles=profiles)
    transport._refresh_oauth('synthetic-refresh');transport._refresh_oauth('synthetic-refresh')
    assert calls==['ios','android','ios','android']


@pytest.mark.asyncio
async def test_legacy_follow_roundtrip_is_automatically_observed(action_stack,no_network):
    stack=action_stack;posts=[]
    def post(request,*_):
        action='unfollow' if b'del' in request.data else 'follow'
        posts.append(action)
        if action=='follow':stack.http.following['91']=0
        else:stack.http.following.pop('91',None)
        return WriteHTTPResponse(200,b'[]' if action=='follow' else b'{}')
    stack.actions._writer._transport=post
    # A second full snapshot is unnecessary and can exceed the live deadline.
    stack.actions._writer.reconcile_from_reader=lambda *a,**k:pytest.fail('duplicate following fetch')
    for action in ('follow','unfollow'):
        intent=WriteIntent(uuid4().hex,'7','91',action)
        result=await stack.actions.execute(intent,author_name='Synthetic Author')
        assert result.status=='observed'
        assert result.remote_state is (action=='follow')
        assert not stack.actions.state('7','user','91').unknown
        assert ('7','user','91') not in stack.actions._writer._blocked
        assert stack.actions._writer.result(intent.operation_id).status=='indeterminate'
    assert posts==['follow','unfollow']


@pytest.mark.asyncio
async def test_failed_automatic_reconciliation_keeps_follow_fenced(action_stack,no_network):
    stack=action_stack;posts=[]
    def post(request,*_):
        posts.append('post');stack.http.following['91']=0
        return WriteHTTPResponse(200,b'[]')
    stack.actions._writer._transport=post
    stack.actions._writer._reconcile_observed=lambda *args,**kwargs:False
    outcome=await stack.actions.execute(WriteIntent(uuid4().hex,'7','91','follow'))
    assert outcome.status=='indeterminate'
    assert stack.actions.state('7','user','91').unknown
    assert ('7','user','91') in stack.actions._writer._blocked
    assert posts==['post']


def test_first_native_launch_has_preferences_and_keeps_them_after_setup(tmp_path,no_network):
    store=NativeProfileStore(tmp_path/'profile')
    try:
        snapshot=store.snapshot();initial=native_kwargs(snapshot,store.root).get('preferences_root')
        assert initial is not None
        prefs=PreferencesStore(initial/'preferences.sqlite3')
        try:prefs.set_setting('minimize_to_tray','0')
        finally:prefs.close()
        target=tmp_path/'new-save';target.mkdir()
        configured=replace(snapshot,save_parent=target)
        assert native_kwargs(configured,store.root)['preferences_root']==initial
        # A configured existing install must keep its original preference root.
        other=tmp_path/'existing-profile';other.mkdir()
        assert native_kwargs(configured,other)['preferences_root']==plan_storage(configured,other)['preferences_root']
    finally:store.close()


def test_startup_preferences_cannot_overlap_cookie_source(tmp_path,no_network):
    store=NativeProfileStore(tmp_path/'profile')
    try:
        source=tmp_path/'profile-startup-preferences';source.mkdir()
        cookie=source/'cookies.txt';cookie.write_text('synthetic fixture',encoding='utf8')
        with pytest.raises(NativeProfileError):
            native_kwargs(replace(store.snapshot(),cookie_file=cookie),store.root)
        assert not (source/'preferences.sqlite3').exists()
        assert cookie.read_text(encoding='utf8')=='synthetic fixture'
    finally:store.close()
