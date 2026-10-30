"""R16/R17 real request construction with *only* synthetic Cookie and fake transports."""
from __future__ import annotations

import json
import threading
import time
from html import escape
from email.message import Message
from pathlib import Path
from urllib.parse import parse_qs
from urllib.request import Request

import pytest

from pixivvault_nicegui.pixiv_read_client import LivePixivReadClient
from pixivvault_nicegui.pixiv_write_client import (
    PixivWriteClient, WriteIntent, WriteHTTPResponse, WriteBusy,
    WriteOperationConflict, WriteValidationError, _csrf_from_home,
    _default_write_transport,
)


def _cookie(tmp_path, uid='55'):
    path=tmp_path/'fake-cookies.txt'
    path.write_text('# Netscape HTTP Cookie File\n'
        +f'.pixiv.net\tTRUE\t/\tTRUE\t{int(time.time())+86400}\tPHPSESSID\t{uid}_fakevalue\n',
        encoding='utf8')
    return path


class FakeRead:
    def __init__(self, uid='55'):
        self.calls=[]
        self.uid=uid
        self.by_work={}

    def __call__(self, request, timeout, limit, deadline):
        assert request.get_method()=='GET'
        self.calls.append(request.full_url)
        if request.full_url=='https://www.pixiv.net/':
            next_data={'props':{'pageProps':{'isLoggedIn':True,'gaUserData':{'id':self.uid},
                'serverSerializedPreloadedState':json.dumps({'api':{'token':'a'*32}})}}}
            return ('<script id="__NEXT_DATA__" type="application/json">'
                    +json.dumps(next_data)+'</script>').encode()
        if request.full_url.endswith('/ajax/user/extra'):
            return json.dumps({'error':False,'body':{'userId':self.uid}}).encode()
        if f'/ajax/user/{self.uid}/following?' in request.full_url:
            return json.dumps({'error':False,'body':{'users':[]}}).encode()
        if '/ajax/illust/' in request.full_url or '/ajax/novel/' in request.full_url:
            wid=request.full_url.split('/')[-1]
            body=self.by_work.get(wid, {'id':wid,'userId':'91','bookmarkData':{'id':'888'},
                'title':'Synthetic novel','content':'Synthetic novel body'})
            return json.dumps({'error':False,'body':body}).encode()
        raise AssertionError('unexpected fake GET')


class FakeWrite:
    def __init__(self, answers=None):
        self.calls=[]
        self.answers=list(answers if answers is not None else [
            WriteHTTPResponse(200,b'{"error":false,"body":{"last_bookmark_id":"888"}}')])

    def __call__(self, request, timeout, limit, deadline):
        self.calls.append(request)
        assert request.get_method()=='POST'
        assert 0<timeout<=10 and limit<=131072 and deadline>=time.monotonic()
        if not self.answers:raise AssertionError('unexpected second mutation')
        answer=self.answers.pop(0)
        if isinstance(answer, BaseException):raise answer
        return answer


@pytest.fixture
def writer(tmp_path):
    path=_cookie(tmp_path)
    before=path.read_bytes()
    read=FakeRead()
    client=LivePixivReadClient(path,transport=read)
    write=FakeWrite()
    executor=PixivWriteClient(client,transport=write)
    try:yield executor,write,read,path,before,client
    finally:
        assert path.read_bytes()==before
        client.close()


def intent(op='add1', *, action='bookmark_add', kind='illust', target='123', author='91',
           account='55', restrict=0, tags=(), comment=''):
    return WriteIntent(operation_id=op,account_id=account,target_id=target,
        action=action,work_kind=kind,author_id=author,restrict=restrict,
        tags=tags,comment=comment)


def test_write_core_is_dormant_until_explicit_execute_and_bookmark_json_receipt(writer):
    client,transport,read,path,before,reader=writer
    assert transport.calls==[] and read.calls==[]
    result=client.execute(intent(restrict=1,tags=('favorite',),comment='test'))
    assert result.status=='confirmed' and result.created_bookmark_id=='888'
    assert result.account_id=='55' and result.action=='bookmark_add'
    assert client.confirmed_state('55','illust','123') is True
    assert len(transport.calls)==1
    request=transport.calls[0]
    assert request.full_url=='https://www.pixiv.net/ajax/illusts/bookmarks/add'
    assert request.get_header('X-csrf-token')=='a'*32
    assert request.get_header('Origin')=='https://www.pixiv.net'
    assert request.get_header('Cookie') and 'fakevalue' in request.get_header('Cookie')
    assert json.loads(request.data)=={'illust_id':'123','restrict':1,'tags':['favorite'],'comment':'test'}
    assert request.get_header('Content-type')=='application/json; charset=utf-8'
    assert reader._verified_identity=='55'  # original GET-only reader unchanged
    assert path.read_bytes()==before


@pytest.mark.parametrize(('action','payload'),[
    ('follow',{'mode':['add'],'type':['user'],'user_id':['91'],'restrict':['1'],'format':['json']}),
    ('unfollow',{'mode':['delete'],'type':['user'],'user_id':['91'],'format':['json']}),
])
def test_follow_and_unfollow_keep_original_form_flags(writer,action,payload):
    client,transport,read,*_=writer
    transport.answers=[WriteHTTPResponse(200,b'{"error":false,"body":{}}')]
    result=client.execute(intent('follow-'+action,action=action,target='91',restrict=1))
    assert result.status=='confirmed'
    assert client.confirmed_state('55','user','91')==(action=='follow')
    request=transport.calls[0]
    assert request.full_url=='https://www.pixiv.net/bookmark_add.php'
    assert parse_qs(request.data.decode())==payload
    assert request.get_header('Referer')=='https://www.pixiv.net/users/91'


def test_bookmark_delete_uses_author_pinned_bookmark_receipt_not_work_id(writer):
    client,transport,read,*_=writer
    transport.answers=[WriteHTTPResponse(200,b'{"error":false,"body":{}}')]
    result=client.execute(intent('del1',action='bookmark_delete'))
    assert result.status=='confirmed'
    request=transport.calls[0]
    assert request.full_url=='https://www.pixiv.net/ajax/illusts/bookmarks/delete'
    assert parse_qs(request.data.decode())=={'bookmark_id':['888']}
    assert any(x.endswith('/ajax/illust/123') for x in read.calls)
    assert client.confirmed_state('55','illust','123') is False


def test_novel_bookmark_add_and_author_verified_delete(writer):
    client,transport,read,*_=writer
    transport.answers=[WriteHTTPResponse(200,b'{"error":false,"body":{"last_bookmark_id":"900"}}'),
                       WriteHTTPResponse(200,b'{"error":false,"body":{}}')]
    assert client.execute(intent('novel-add',kind='novel')).created_bookmark_id=='900'
    assert json.loads(transport.calls[0].data)['novel_id']=='123'
    assert transport.calls[0].full_url.endswith('/ajax/novels/bookmarks/add')
    assert client.execute(intent('novel-delete',kind='novel',action='bookmark_delete')).status=='confirmed'
    assert transport.calls[1].full_url.endswith('/ajax/novels/bookmarks/delete')


@pytest.mark.parametrize('unsafe_account',('56','0','55 ',True,None))
def test_wrong_account_never_transmits_post(writer,unsafe_account):
    client,transport,*_=writer
    if unsafe_account=='56':
        result=client.execute(intent('bad-account',account=unsafe_account))
        assert result.status=='rejected'
    else:
        with pytest.raises(WriteValidationError):
            client.execute(intent('bad-account',account=unsafe_account))
    assert transport.calls==[]


@pytest.mark.parametrize('bad',[
    {'target_id':'0'},{'operation_id':''},{'action':'erase_all'},
    {'restrict':2},{'tags':('x'*200,)},{'comment':'x'*10001},
    {'work_kind':'other'},{'account_id':True},
])
def test_invalid_intent_rejected_before_reader_or_transport(writer,bad):
    client,transport,read,*_=writer
    request=intent()
    from dataclasses import replace
    with pytest.raises(WriteValidationError):
        client.execute(replace(request,**bad))
    assert transport.calls==[] and read.calls==[]


@pytest.mark.parametrize('answer',[
    TimeoutError('secret-cookie=TOKEN'),OSError('secret-cookie=TOKEN'),
    WriteHTTPResponse(503,b'{"error":true,"message":"TOKEN"}'),
    WriteHTTPResponse(302,b'login TOKEN'),
    WriteHTTPResponse(200,b'<html> TOKEN </html>'),
    WriteHTTPResponse(200,b'{"error":false,"body":{}}'),
    WriteHTTPResponse(200,b'{"error":false,"body":{"bookmark_id":"1","id":"2"}}'),
])
def test_ambiguous_mutation_never_marks_success_or_automatically_retries(writer,answer):
    client,transport,read,*_=writer
    transport.answers=[answer]
    result=client.execute(intent())
    assert result.status=='indeterminate'
    assert result.requires_reconciliation
    assert client.confirmed_state('55','illust','123') is None
    assert len(transport.calls)==1
    assert 'TOKEN' not in repr(result)
    with pytest.raises(WriteOperationConflict):client.execute(intent())
    assert len(transport.calls)==1


@pytest.mark.parametrize('answer',[
    WriteHTTPResponse(400,b'{"error":true}'),
    WriteHTTPResponse(200,b'{"error":true,"message":"TOKEN"}'),
])
def test_explicit_rejection_is_sanitized_and_does_not_update_state(writer,answer):
    client,transport,*_=writer
    transport.answers=[answer]
    result=client.execute(intent())
    assert result.status=='rejected'
    assert 'TOKEN' not in repr(result)
    assert client.confirmed_state('55','illust','123') is None


def test_concurrent_duplicate_click_and_same_operation_replay_rejected(writer):
    client,transport,*_=writer
    entered=threading.Event();release=threading.Event()
    def blocked(req,timeout,limit,deadline):
        transport.calls.append(req)
        entered.set(); assert release.wait(2)
        return WriteHTTPResponse(200,b'{"error":false,"body":{"last_bookmark_id":"88"}}')
    client._transport=blocked
    answers=[]
    thread=threading.Thread(target=lambda:answers.append(client.execute(intent())),daemon=True)
    thread.start()
    assert entered.wait(2)
    with pytest.raises(WriteBusy):client.execute(intent('other-op'))
    with pytest.raises(WriteBusy):client.execute(intent())
    release.set();thread.join(3)
    assert len(answers)==1 and answers[0].status=='confirmed'
    with pytest.raises(WriteOperationConflict):client.execute(intent())
    with pytest.raises(WriteOperationConflict):client.execute(intent('another-op'))
    assert len(transport.calls)==1


def test_missing_or_wrong_bookmark_id_and_author_never_delete(writer):
    client,transport,read,*_=writer
    read.by_work['123']={'id':'123','userId':'91','bookmarkData':None}
    assert client.execute(intent('no-id',action='bookmark_delete')).status=='indeterminate'
    # Unknown outcome blocks this target even with a new operation ID.
    with pytest.raises(WriteOperationConflict):
        client.execute(intent('wrong-author',action='bookmark_delete'))
    assert transport.calls==[]
    # A fresh confirmed observation is required before another attempt.
    read.by_work['123']={'id':'123','userId':'91','bookmarkData':{'id':'888'}}
    assert client.reconcile_from_reader('55','illust','123',author_id='91')
    read.by_work['123']={'id':'123','userId':'92','bookmarkData':{'id':'888'}}
    assert client.execute(intent('wrong-author',action='bookmark_delete')).status=='indeterminate'
    assert transport.calls==[]


def test_failed_csrf_preflight_never_posts_and_still_no_automatic_retry(writer):
    client,transport,read,*_=writer
    client._csrf_token=lambda _account: (_ for _ in ()).throw(RuntimeError('raw-token TOKEN'))
    outcome=client.execute(intent())
    assert outcome.status=='rejected'
    assert transport.calls==[]
    assert 'TOKEN' not in repr(outcome)


def test_csrf_page_rejected_when_account_switched_between_identity_and_post(writer):
    client,transport,read,*_=writer
    original=read.__call__
    # The first GET establishes the correct account; a later CSRF GET no longer
    # does. No writable mutation may be dispatched under another account.
    read.__call__=original  # no runtime monkeypatch of shared existing reader
    home_calls=0
    def switched(request,timeout,limit,deadline):
        nonlocal home_calls
        if request.full_url=='https://www.pixiv.net/':
            home_calls+=1
            if home_calls>1:
                data={'props':{'pageProps':{'isLoggedIn':True,
                     'gaUserData':{'id':'56'},'serverSerializedPreloadedState':
                     json.dumps({'api':{'token':'b'*32}})}}}
                return ('<script id="__NEXT_DATA__" type="application/json">'
                         +json.dumps(data)+'</script>').encode()
        return original(request,timeout,limit,deadline)
    client._reader._transport=switched
    result=client.execute(intent())
    assert result.status=='rejected' and home_calls==2
    assert transport.calls==[]


def test_writer_close_during_post_does_not_close_reader_or_confirm_result(writer):
    client,transport,read,path,before,reader=writer
    started=threading.Event();release=threading.Event()
    def delayed(request,timeout,limit,deadline):
        transport.calls.append(request)
        started.set()
        assert release.wait(2)
        return WriteHTTPResponse(200,b'{"error":false,"body":{"last_bookmark_id":"78"}}')
    client._transport=delayed
    received=[]
    thread=threading.Thread(target=lambda:received.append(client.execute(intent())),daemon=True)
    thread.start()
    assert started.wait(2)
    client.close()
    release.set();thread.join(3)
    assert len(received)==1 and received[0].status=='indeterminate'
    assert client.confirmed_state('55','illust','123') is None
    assert len(transport.calls)==1
    assert reader.require_identity()=='55'  # shared GET-only reader still open


def test_ambiguous_result_blocks_new_operation_until_bounded_observation(writer):
    client,transport,read,*_=writer
    transport.answers=[TimeoutError('secret'),
                       WriteHTTPResponse(200,b'{"error":false,"body":{"last_bookmark_id":"888"}}')]
    assert client.execute(intent()).status=='indeterminate'
    with pytest.raises(WriteOperationConflict):
        client.execute(intent('fresh-id'))
    assert len(transport.calls)==1
    assert client.reconcile_from_reader('56','illust','123',author_id='91') is False
    read.by_work['123']={'id':'123','userId':'91'}
    assert client.reconcile_from_reader('55','illust','123',author_id='91') is False
    read.by_work['123']={'id':'123','userId':'91','bookmarkData':{'id':'888'}}
    assert client.reconcile_from_reader('55','illust','123',author_id='91')
    assert client.confirmed_state('55','illust','123') is True
    # Observed bookmark present => redundant add is blocked, not silently sent.
    with pytest.raises(WriteOperationConflict):
        client.execute(intent('fresh-id'))
    assert len(transport.calls)==1
    # A *new* observation that it is now absent permits a genuinely new click.
    # A forced refresh is not the same as reusing the old uncertain operation ID.
    # The writer can now delete it with a new, explicit operation.
    transport.answers=[WriteHTTPResponse(200,b'{"error":false,"body":{}}')]
    assert client.execute(intent('explicit-remove',action='bookmark_delete')).status=='confirmed'
    assert len(transport.calls)==2


def test_duplicate_json_keys_do_not_authorize_write_success(writer):
    client,transport,*_=writer
    transport.answers=[WriteHTTPResponse(200,b'{"error":false,"error":true,"body":{"id":"7"}}')]
    assert client.execute(intent()).status=='indeterminate'
    assert client.confirmed_state('55','illust','123') is None


def test_reader_closed_during_post_is_ambiguous_not_confirmed(writer):
    client,transport,read,path,before,reader=writer
    def closes_reader(req,timeout,limit,deadline):
        transport.calls.append(req)
        reader.close()
        return WriteHTTPResponse(200,b'{"error":false,"body":{"last_bookmark_id":"88"}}')
    client._transport=closes_reader
    result=client.execute(intent())
    assert result.status=='indeterminate' and result.requires_reconciliation
    assert client.confirmed_state('55','illust','123') is None
    assert len(transport.calls)==1


def test_indeterminate_bookmark_requires_author_pinned_bounded_get_for_unblock(writer):
    client,transport,read,*_=writer
    transport.answers=[TimeoutError('unknown')]
    assert client.execute(intent()).requires_reconciliation
    read.by_work['123']={'id':'123','userId':'92','bookmarkData':None}
    assert not client.reconcile_from_reader('55','illust','123',author_id='91')
    assert client.confirmed_state('55','illust','123') is None
    with pytest.raises(WriteOperationConflict):client.execute(intent('fresh'))
    read.by_work['123']={'id':'123','userId':'91','bookmarkData':None}
    assert client.reconcile_from_reader('55','illust','123',author_id='91')
    assert client.confirmed_state('55','illust','123') is False
    transport.answers=[WriteHTTPResponse(200,b'{"error":false,"body":{"last_bookmark_id":"889"}}')]
    assert client.execute(intent('fresh')).confirmed
    assert len(transport.calls)==2


def test_follow_indeterminate_reconciles_only_from_complete_bounded_reader_lists(writer):
    client,transport,read,*_=writer
    transport.answers=[TimeoutError('unknown')]
    request=intent('follow-unknown',action='follow',target='91')
    assert client.execute(request).requires_reconciliation
    with pytest.raises(WriteOperationConflict):
        client.execute(intent('new-follow',action='follow',target='91'))
    assert client.reconcile_from_reader('55','user','91')
    assert client.confirmed_state('55','user','91') is False
    assert len(transport.calls)==1
    assert any('/following?' in route for route in read.calls)


def test_reconcile_rejects_invalid_or_unknown_observation_without_another_post(writer):
    client,transport,read,*_=writer
    transport.answers=[TimeoutError('unknown')]
    assert client.execute(intent()).requires_reconciliation
    read.by_work['123']={'id':'123','userId':'91'}  # bookmarkData omitted
    assert client.reconcile_from_reader('55','illust','123',author_id='91') is False
    assert client.reconcile_from_reader('56','illust','123',author_id='91') is False
    assert client.reconcile_from_reader('55','illust','123',author_id='0') is False
    with pytest.raises(WriteOperationConflict):client.execute(intent('next-id'))
    assert len(transport.calls)==1


def test_two_simultaneous_reconciliation_gets_cannot_overwrite_newer_state(writer):
    client,transport,read,*_=writer
    transport.answers=[TimeoutError('outcome unknown')]
    assert client.execute(intent()).requires_reconciliation
    entered=threading.Event();release=threading.Event();answers=[]
    def delayed_observer(target,author):
        entered.set()
        assert release.wait(2)
        return {'id':target,'userId':author,'bookmarkData':None}
    client._reader.get_artwork_metadata_raw=delayed_observer
    task=threading.Thread(target=lambda:answers.append(client.reconcile_from_reader(
        '55','illust','123',author_id='91')),daemon=True)
    task.start();assert entered.wait(2)
    # A second overlapping read is not allowed to supersede the first one.
    assert not client.reconcile_from_reader('55','illust','123',author_id='91')
    with pytest.raises(WriteOperationConflict):client.execute(intent('new-click'))
    release.set();task.join(3)
    assert answers==[True]
    assert client.confirmed_state('55','illust','123') is False
    assert len(transport.calls)==1


def test_closed_writer_disallows_reconciliation_after_ambiguous_mutation(writer):
    client,transport,read,*_=writer
    transport.answers=[TimeoutError('unknown')]
    assert client.execute(intent()).requires_reconciliation
    client.close()
    assert not client.reconcile_from_reader('55','illust','123',author_id='91')
    with pytest.raises(WriteOperationConflict):client.execute(intent('new'))
    assert read.calls  # Reader remains the owner of its own resources.


def test_reusing_prior_operation_id_for_different_target_never_sends_again(writer):
    client,transport,read,*_=writer
    assert client.execute(intent()).confirmed
    with pytest.raises(WriteOperationConflict):client.execute(intent('add1',target='124'))
    assert len(transport.calls)==1


def test_original_csrf_legacy_meta_fallback_validates_token_without_network():
    from html import escape
    data={'userData':{'id':'55'},'token':'a'*32}
    home=('<meta id="meta-global-data" content="'+escape(json.dumps(data),quote=True)+'">').encode()
    assert _csrf_from_home(home)=='a'*32
    with pytest.raises(WriteValidationError):
        _csrf_from_home(b'<meta name="csrf-token" content="BAD">')


def test_default_one_post_transport_has_no_redirect_or_proxy_and_closes_response(monkeypatch):
    from pixivvault_nicegui import pixiv_write_client as mod
    seen=[]
    class Response:
        headers=Message()
        closed=False
        def getcode(self):return 302
        def read(self,limit):return b'no redirect followed'
        def __enter__(self):return self
        def __exit__(self,*_):self.closed=True
    result=Response()
    class Opener:
        def open(self,request,timeout):
            seen.append((request,timeout))
            return result
    def fake_build(*handlers):
        from urllib.request import ProxyHandler
        assert any(isinstance(h,mod._NoRedirects) for h in handlers)
        assert any(isinstance(h,ProxyHandler) for h in handlers)
        return Opener()
    monkeypatch.setattr(mod,'build_opener',fake_build)
    req=Request('https://www.pixiv.net/bookmark_add.php',data=b'fake',method='POST')
    response=_default_write_transport(req,5,512,time.monotonic()+6)
    assert response.status==302
    assert len(seen)==1 and seen[0][0] is req and result.closed


def test_close_during_csrf_preflight_never_starts_post(writer):
    client,transport,read,path,before,reader=writer
    original=reader._transport
    started=threading.Event(); release=threading.Event(); homes=0
    def delayed_home(request,timeout,limit,deadline):
        nonlocal homes
        if request.full_url=='https://www.pixiv.net/':
            homes+=1
            if homes==2:
                started.set();assert release.wait(2)
        return original(request,timeout,limit,deadline)
    reader._transport=delayed_home
    received=[]
    thread=threading.Thread(target=lambda:received.append(client.execute(intent())),daemon=True)
    thread.start(); assert started.wait(2)
    client.cancel()
    release.set();thread.join(3)
    assert len(received)==1 and received[0].status=='indeterminate'
    assert transport.calls==[]
    assert reader.require_identity()=='55' and path.read_bytes()==before


def test_result_identity_check_rejects_late_wrong_work_or_action(writer):
    from dataclasses import replace
    client,transport,*_=writer
    action=intent('selected-click')
    result=client.execute(action)
    assert result.confirmed and result.matches(action)
    assert not result.matches(replace(action,target_id='124'))
    assert not result.matches(replace(action,action='bookmark_delete'))
    assert not result.matches(replace(action,operation_id='other-click'))
