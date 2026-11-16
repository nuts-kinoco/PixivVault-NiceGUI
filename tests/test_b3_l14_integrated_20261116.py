"""Both adopted paths through the same imports; generated I/O only, no cookies."""
import copy
from dataclasses import replace
import hashlib
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
from threading import RLock
import time
from types import SimpleNamespace

from nicegui import Client as UIClient, ui
import pytest
from stage5_generated_shared_owner import generated_shared_scope

from pixivvault_nicegui.bookmark_state import BookmarkState
from pixivvault_nicegui.job_service import JobService
from pixivvault_nicegui.library_filters import LibraryFilters
from pixivvault_nicegui.managed_download_catalog import build_managed_library
from pixivvault_nicegui.managed_download_requests import ManagedDownloadRequests
from pixivvault_nicegui.managed_downloads import ManagedPixivDownloads
from pixivvault_nicegui.managed_network_stop_shared_scope import _SealedOwnerTransport
from pixivvault_nicegui.managed_network_stop_transport import ManagedNetworkStopTransport
from pixivvault_nicegui.models import Artwork
from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected, LivePixivReadClient
from pixivvault_nicegui.request_policy import RequestRetryPolicy
from pixivvault_nicegui.ui.bookmark_view import BookmarkView
from pixivvault_nicegui.ui.request_status_view import RequestStatusView
from test_managed_downloads import Client as ImageSource, Context, Job, record
from test_managed_download_services import terminal
from test_l14_bookmark_count import labels
from tools.check_managed_download_native import verify_storage


class Clock:
    def __init__(self):
        self.now=time.monotonic()
        self.on_wait=lambda:None
    def sleep(self,seconds):
        self.on_wait()
        self.now+=seconds
    def policy(self,count):
        return RequestRetryPolicy(lambda:SimpleNamespace(retry_limit=count,rate_limit_wait_seconds=3),
                                  clock=lambda:self.now,sleep=self.sleep,jitter=lambda *_:0)


def reader(scope,policy,wire):
    # Only the constructor's file/account input is omitted. Exercise actual
    # detail -> _json -> _get -> policy -> _get_once -> guarded transport.
    result=object.__new__(LivePixivReadClient)
    result._closed=False
    result._verified_identity='99'
    result._identity_lock=RLock()
    result._cookie_user_id=None
    result._cookies=CookieJar()  # Empty in memory: no file, values or Cookie header.
    result._owned_transport=None
    result._transport=_SealedOwnerTransport(scope,ManagedNetworkStopTransport(scope.gate,wire))
    result.set_retry_policy(policy)
    return result


def policy_state(policy):
    return copy.deepcopy((policy._rate_stopped,policy._serial,policy._records))


def protected_files(root):
    return {p.name:(p.stat().st_ino,hashlib.sha256(p.read_bytes()).hexdigest())
            for p in root.iterdir() if p.is_file()}


def capture(name,data):
    destination=os.environ.get('PV_INTEGRATED_EVIDENCE')
    if destination:
        path=Path(destination);path.mkdir(parents=True,exist_ok=True)
        (path/(name+'.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


@pytest.mark.parametrize('count',[None,0,1200])
def test_retry_detail_receipt_catalog_and_cards_share_one_candidate(tmp_path,count):
    scope=generated_shared_scope(tmp_path);gate=scope.gate
    protected_before=protected_files(scope.root)
    gate_before=gate.snapshot()
    gate_bytes=gate.path.read_bytes()
    clock=Clock();policy=clock.policy(3)
    calls=[];waiting=[]
    def wire(request,*_):
        assert request.get_method()=='GET' and request.data is None and not request.has_header('Cookie')
        assert request.full_url=='https://www.pixiv.net/ajax/illust/123'
        calls.append('detail')
        if len(calls)==1:raise HTTPStatusRejected(429)
        body=dict(id='123',userId='456',illustType=0,pageCount=2,title='Integrated generated work',
                  userName='Generated author',tags={'tags':[]},urls={})
        if count is not None:body['bookmarkCount']=count
        return json.dumps(dict(error=False,body=body)).encode()
    live=reader(scope,policy,wire)
    client=UIClient(ui.page('/_b3_l14_joint'))
    status=view=owner=jobs=catalog=None
    try:
        with client:
            status=RequestStatusView(policy)
            def observe():
                saved=policy_state(policy);status.refresh()
                assert policy_state(policy)==saved
                waiting.append(status.label.text)
                assert '今回の429' in status.label.text and '再試行待機中' in status.label.text
                assert '共有STOP履歴には記録しません' in status.label.text
            clock.on_wait=observe
            dto=live.get_artwork_download_record('123')
            assert dto.bookmark_count==count and calls==['detail','detail']
            assert waiting and not policy.progress_snapshot()
            status.refresh();assert not status.label.visible
            assert gate.snapshot()==gate_before and gate.path.read_bytes()==gate_bytes
            assert not gate.inflight_path.exists()
            assert protected_files(scope.root)==protected_before
            images=ImageSource()
            root=tmp_path/'managed'
            owner=ManagedPixivDownloads(root,images,resolve_record=lambda _:dto)
            requests=ManagedDownloadRequests(root);requests.store('joint',dto)
            assert requests.load('joint')==dto
            jobs=JobService(owner.run,owner.storage_path)
            job=jobs.enqueue('pixiv-illust','123',dto.title,operation_id='joint')
            relative=terminal(jobs,job.job_id).result
            jobs.close()
            receipt=(root/relative).read_bytes()
            assert json.loads(receipt).get('bookmark_count')==count
            assert ('bookmark_count' in json.loads(receipt))==(count is not None)
            snapshot,catalog,_=build_managed_library(owner)
            saved_work,=snapshot.works
            assert saved_work.artwork.bookmark_count==count
            assert catalog.get('123',0,'original').data==images.payload
            assert verify_storage(root)['receipts']==1
            remote=(Artwork('123','remote','author'),Artwork('novel:123','novel','author'))
            state=BookmarkState(local=(replace(saved_work.artwork,bookmark_count=None),),cached_remote=remote)
            membership=[(v.work_id,v.downloaded) for v in state.items]
            state.replace_known((saved_work.artwork,Artwork('999','nonmember','author',bookmark_count=777)))
            assert [(v.work_id,v.downloaded) for v in state.items]==membership==[('123',True),('novel:123',False)]
            assert [v.artwork.bookmark_count for v in state.items]==[count,None]
            view=BookmarkView(state);view.render()
            assert labels(view.cards['123'],'bookmark-card-count')==([] if count is None else [f'★ {count:,}'])
            assert labels(view.cards['novel:123'],'bookmark-card-count')==[]
            state.set_filters(LibraryFilters(bookmark_min=500));view.refresh()
            assert [v.work_id for v in state.matched_items]==(['123'] if count==1200 else [])
            state.set_filters(LibraryFilters());view.refresh()
            assert len(state.matched_items)==2
            assert owner.run(Job(),Context())==relative and (root/relative).read_bytes()==receipt
            assert images.count==2
            catalog.close();catalog=None
            owner.close();owner=None
            owner=ManagedPixivDownloads(root,images,resolve_record=lambda _:dto)
            restarted,catalog,_=build_managed_library(owner)
            assert restarted.works[0].artwork.bookmark_count==count
            assert (root/relative).read_bytes()==receipt
            capture('success-'+str(count),dict(count=count,detail_callbacks=len(calls),waiting=waiting,
                dto_count=dto.bookmark_count,catalog_count=saved_work.artwork.bookmark_count,
                membership=membership,receipt_sha256=hashlib.sha256(receipt).hexdigest(),
                receipt_unchanged=True,gate_unchanged=True,real_cookie_used=False))
    finally:
        if catalog:catalog.close()
        if jobs:jobs.close()
        if owner:owner.close()
        if view:view.close()
        if status:status.close()
        client.delete();live.close()


def test_shared_stop_prevents_detail_publication_and_keeps_l14_values(tmp_path):
    scope=generated_shared_scope(tmp_path);gate=scope.gate
    protected_before=protected_files(scope.root)
    gate_bytes=gate.path.read_bytes();gate_before=gate.snapshot()
    clock=Clock();policy=clock.policy(5)
    calls=[]
    def wire(actor):
        def fail(request,*_):
            assert not request.has_header('Cookie') and request.get_method()=='GET'
            calls.append(actor);raise HTTPStatusRejected(429)
        return fail
    target=reader(scope,policy,wire('target'));other=reader(scope,policy,wire('other'))
    dto=replace(record(),bookmark_count=1200)
    owner=ManagedPixivDownloads(tmp_path/'managed',ImageSource(),resolve_record=lambda _:dto)
    relative=owner.run(Job(),Context());receipt=(owner.root/relative).read_bytes()
    state=BookmarkState(local=(dto.as_artwork(),),cached_remote=(dto.as_artwork(),))
    before=tuple(state.items)
    client=UIClient(ui.page('/_b3_l14_shared'))
    status=bookmarks=None
    injected=[False]
    try:
        with client:
            status=RequestStatusView(policy)
            bookmarks=BookmarkView(state);bookmarks.render()
            def interleave():
                if not injected[0]:
                    injected[0]=True
                    with pytest.raises(HTTPStatusRejected):other.get_artwork_download_record('124')
            clock.on_wait=interleave
            with pytest.raises(HTTPStatusRejected):target.get_artwork_download_record('123')
            assert calls==['target']+['other']*5
            saved=policy_state(policy)
            for _ in range(2):status.refresh();bookmarks.refresh()
            assert policy_state(policy)==saved and tuple(state.items)==before
            target_line,other_line=status.label.text.splitlines()
            assert 'この操作の試行 2/5' in target_line and '別の取得処理が' in target_line
            assert 'この操作自身は試行上限に達していません' in target_line
            assert 'この操作自身が429の試行上限に達した' in other_line
            assert labels(bookmarks.cards['123'],'bookmark-card-count')==['★ 1,200']
            with pytest.raises(HTTPStatusRejected):target.get_artwork_download_record('123')
            assert calls==['target']+['other']*5 and policy_state(policy)==saved
            assert (owner.root/relative).read_bytes()==receipt
            assert owner.verified_saved_receipt('123')[1]['bookmark_count']==1200
            assert gate.snapshot()==gate_before and gate.path.read_bytes()==gate_bytes
            assert not gate.inflight_path.exists()
            assert protected_files(scope.root)==protected_before
            capture('shared-stop',dict(sends=calls,text=status.label.text,state=saved,
                l14_count=state.items[0].artwork.bookmark_count,receipt_unchanged=True,
                no_detail_publication=True,no_extra_dispatch=True,display_state_unchanged=True))
    finally:
        if bookmarks:bookmarks.close()
        if status:status.close()
        client.delete();target.close();other.close();owner.close()
