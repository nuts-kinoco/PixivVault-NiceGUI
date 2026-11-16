"""Seven display/recovery requirements: generated roots, HTTP and images only."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
from threading import Event
import time
from types import SimpleNamespace

import pytest
from nicegui import Client, ui

from pixivvault_nicegui.request_policy import RequestRetryPolicy, request_scope
from pixivvault_nicegui.preferences import RequestPreferences
from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected, TransportFailed
from pixivvault_nicegui.managed_network_stop_shared_scope import ManagedUserNetworkStopScope
from test_stage5_r1_user_shared_scope import generated_sealed_shared_owner
from test_shared_get_recovery import rows
from test_duplicate_inspection import create
from pixivvault_nicegui.duplicate_inspection import ManagedDuplicateInspection
def retry_status_text(records):
    from pixivvault_nicegui.ui.request_status_view import retry_status_text as render
    return render(records)


class Clock:
    def __init__(self):self.now=time.monotonic();self.waits=[];self.on_wait=lambda:None
    def sleep(self,seconds):self.waits.append(seconds);self.on_wait();self.now+=seconds
    def policy(self,count=3,wait=5):
        return RequestRetryPolicy(lambda:RequestPreferences(.6,count,wait),
            clock=lambda:self.now,sleep=self.sleep,jitter=lambda *_:0)


@pytest.mark.parametrize('count,wait',[(3,3),(3,5),(5,10),(3,30)])
def test_shared_429_waits_without_latching_and_restart_releases_memory_stop(tmp_path,count,wait):
    root,cookie=generated_sealed_shared_owner(tmp_path)
    scope=ManagedUserNetworkStopScope(root);calls=[];reject=[True]
    def wire(*_):
        calls.append(429 if reject[0] else 200)
        if reject[0]:raise HTTPStatusRejected(429)
        return json.dumps({'contents':rows()}).encode()
    clock=Clock();policy=clock.policy(count,wait)
    reader=scope.create_reader(cookie,transport=wire);reader._verified_identity='55'
    reader.set_retry_policy(policy)
    snapshots=[];clock.on_wait=lambda:snapshots.append(policy.progress_snapshot())
    before=scope.gate.snapshot()
    try:
        with pytest.raises(HTTPStatusRejected) as error:reader.get_ranking()
        assert error.value.status_code==429 and calls==[429]*count
        assert sum(clock.waits)==pytest.approx((count-1)*wait)
        assert scope.gate.snapshot()==before and not scope.gate.inflight_path.exists()
        assert snapshots and '再試行待機中' in retry_status_text(snapshots[0])
        assert '再起動後' in retry_status_text(policy.progress_snapshot())
        with pytest.raises(HTTPStatusRejected):reader.get_ranking()
        assert len(calls)==count
        reader.close()
        reject[0]=False
        reader=ManagedUserNetworkStopScope(root).create_reader(cookie,transport=wire)
        reader._verified_identity='55';reader.set_retry_policy(clock.policy())
        assert len(reader.get_ranking())==50 and calls[-1]==200
        assert not scope.gate.inflight_path.exists() and not scope.gate.snapshot().blocked
    finally:reader.close()


def test_retry_progress_then_success_and_cancellation():
    clock=Clock();policy=clock.policy();seen=[];calls=[]
    def wire():
        calls.append(1)
        if len(calls)==1:raise TransportFailed()
        return 42
    clock.on_wait=lambda:seen.append(retry_status_text(policy.progress_snapshot()))
    assert policy.execute(wire,deadline=clock.now+20)==42
    assert len(calls)==2 and '試行 1/3' in seen[0] and '通信エラー' in seen[0]
    assert not policy.progress_snapshot()
    stopped=[False]
    def checkpoint():
        if stopped[0]:raise RuntimeError('generated cancellation')
    clock.on_wait=lambda:stopped.__setitem__(0,True)
    with request_scope(checkpoint=checkpoint):
        with pytest.raises(RuntimeError):policy.execute(lambda:(_ for _ in ()).throw(TransportFailed()),deadline=clock.now+20)
    assert policy.progress_snapshot()[0]['phase']=='cancelled'


def test_retry_download_removes_only_its_staging_after_verified_completion(tmp_path):
    from test_duplicate_inspection import Client as ImageClient, Context
    from pixivvault_nicegui.managed_downloads import ManagedPixivDownloads, ManagedDownloadError
    from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
    class Pages(ImageClient):
        def get_artwork_original_pages(self,_):return tuple(f'https://i.pximg.net/generated-{i}' for i in range(24))
    record=RemoteBookmark('1','illustration','show','generated','9','generated author',(),'','2026-10-26T00:00:00Z',24,0,0)
    root=tmp_path/'managed';owner=ManagedPixivDownloads(root,Pages(),resolve_record=lambda _:record)
    job=SimpleNamespace(job_id='a'*32,kind='pixiv-illust',target_id='1')
    class Interrupted(Context):
        def progress(self,done,total):
            if done==20:raise RuntimeError('generated interruption')
    try:
        with pytest.raises(ManagedDownloadError):owner.run(job,Interrupted())
        partials=[p for p in owner.incomplete.iterdir() if p.is_dir()]
        assert len(partials)==1 and len(list(partials[0].glob('page-*')))==20
        foreign=owner.incomplete/'partial-foreign';foreign.mkdir();(foreign/'keep').write_bytes(b'keep')
        result=owner.run(job,Context())
        assert not partials[0].exists() and not list(owner.incomplete.glob('*.owner.json'))
        assert (foreign/'keep').read_bytes()==b'keep'
        receipt=owner._receipt(result)[0]
        assert receipt['page_count']==24 and len(receipt['files'])==24
        before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/result).parent.iterdir()}
        assert owner.run(job,Context())==result
        assert before=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/result).parent.iterdir()}
    finally:owner.close()


def restarted_job(tmp_path):
    from pixivvault_nicegui.job_service import _Store, JobSnapshot, JobService
    db=tmp_path/'jobs.sqlite3';store=_Store(db)
    job=JobSnapshot('b'*32,'pixiv-illust','1','generated interrupted',state='running',done=20,total=24)
    store.write({job.job_id:job},{})
    store.close()
    service=JobService(lambda *_:pytest.fail('restart must not dispatch'),db)
    assert service.reconcile_interrupted(job.job_id)
    return service


def test_restart_retains_committed_progress_and_cause(tmp_path):
    from pixivvault_nicegui.job_failure import job_failure_text
    service=restarted_job(tmp_path)
    try:
        job=service.snapshot()[0]
        assert (job.state,job.error_code,job.done,job.total)==('error','interrupted',20,24)
        assert '中断' in job_failure_text(job.error_code)
    finally:service.close()


def test_scan_progress_is_readable_while_scan_holds_operation_lock(tmp_path,monkeypatch):
    owner,_=create(tmp_path);source=ManagedDuplicateInspection(owner)
    entered,release=Event(),Event();read=owner.read_saved_page
    def slow(*args):
        entered.set();assert release.wait(5);return read(*args)
    monkeypatch.setattr(owner,'read_saved_page',slow)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(source.scan)
            try:
                assert entered.wait(5)
                value=source.progress_snapshot()
                assert value['phase']=='画像の特徴を計算中' and value['author_id']=='9' and value['total']==2
            finally:release.set()
            assert future.result(5).scanned_files==3
        assert source.progress_snapshot()['phase']=='スキャン完了'
    finally:source.close();owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_only",[False,True])
async def test_duplicate_warning_and_queue_failure_are_visible(tmp_path,monkeypatch,failed_only):
    from pixivvault_nicegui.ui.duplicate_view import DuplicateView
    from pixivvault_nicegui.ui.job_views import QueueView
    from test_ranking_ui import setup
    (tmp_path/'images').mkdir()
    owner,_=create(tmp_path/'images');source=ManagedDuplicateInspection(owner)
    row=owner.saved_receipts()[0]
    monkeypatch.setattr(owner,'saved_receipts',lambda **_:tuple(row for _ in range(2001)))
    jobs=restarted_job(tmp_path)
    services,_,_=setup();services.jobs=jobs
    client=Client(ui.page('/_display_batch'))
    try:
        with client:
            view=DuplicateView(source);view.set_active(True);await view.scan()
            assert '作者ID 9' in view.skipped_notice.text and '2001枚' in view.skipped_notice.text and view.skipped_notice.visible
            queue=QueueView(services,failed_only=failed_only)
            queue.refresh()
            row=next(iter(queue.rows.values()))
            assert '24ページ中20ページ保存済み' in row['tooltip'].text and row['tooltip'].visible
            assert '中断' in row['result'].text and row['result'].visible
            view.close();queue.close()
    finally:client.delete();source.close();owner.close();jobs.close()


def test_image_failure_slot_is_visible_error_content():
    from pixivvault_nicegui.ui.image_failure import image_failure_slot
    client=Client(ui.page('/_image_failure'))
    try:
        with client:
            image=ui.image('/generated-failed');image_failure_slot(image)
            assert 'error' in image.slots
            elements=list(image.slots['error'].children)
            assert elements and any('画像を取得できませんでした' in getattr(child,'text','') for child in elements[0].default_slot.children)
    finally:client.delete()


def assert_product_429(readers, gate, calls, url):
    """New product contract across reader types; no legacy permanent-429 expectation."""
    policy=readers[0]._retry_policy
    assert policy is not None and all(r._retry_policy is policy for r in readers)
    clock=Clock();policy.clock=lambda:clock.now;policy.sleep=clock.sleep
    policy.settings=lambda:RequestPreferences(.6,3,5)
    before=len(calls)
    with pytest.raises(HTTPStatusRejected) as error:readers[0].get_image_bytes(url)
    assert error.value.status_code==429 and len(calls)==before+3
    assert sum(clock.waits)==pytest.approx(10) and not gate.snapshot().blocked
    for reader in readers:
        with pytest.raises(HTTPStatusRejected):reader.get_image_bytes(url)
    assert len(calls)==before+3
    # Reconstruct the in-memory policy with the same durable owner: HTTP is
    # attempted again; previous 429 must not have changed persistent state.
    restarted=clock.policy()
    for reader in readers:reader.set_retry_policy(restarted)
    with pytest.raises(HTTPStatusRejected):readers[0].get_image_bytes(url)
    assert len(calls)==before+6 and not gate.snapshot().blocked


def test_raw_429_closes_response_and_clears_both_gate_markers(tmp_path):
    from io import BytesIO
    from urllib.error import HTTPError
    from urllib.request import Request
    from stage5_generated_shared_owner import generated_shared_scope
    from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopGate
    from pixivvault_nicegui.managed_network_stop_shared_transport import _dispatch_dual_stop
    local=ManagedNetworkStopGate(tmp_path/'local.sqlite3')
    shared=generated_shared_scope(tmp_path).gate
    response=BytesIO(b'generated');attempts=[];clock=Clock();policy=clock.policy()
    request=Request('https://i.pximg.net/generated.png')
    def wire(*_):
        attempts.append(1)
        if len(attempts)==1:raise HTTPError(request.full_url,429,'generated',{},response)
        return b'generated-success'
    def operation():return _dispatch_dual_stop(local,shared,wire,request,1,1024,clock.now+120)
    assert policy.execute(operation,deadline=clock.now+120)==b'generated-success'
    assert len(attempts)==2 and response.closed
    assert not local.snapshot().blocked and not shared.snapshot().blocked
    assert not shared.inflight_path.exists()
