from io import BytesIO
from uuid import uuid4
import asyncio
import pytest
from PIL import Image
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
from pixivvault_nicegui.novel_save_presentation import build_novel_ui_fixture, NovelSavePresentation
from pixivvault_nicegui.ugoira_fixtures import build_ugoira_fixture
from pixivvault_nicegui.media_jobs import MediaJobs
from pixivvault_nicegui.bootstrap import parse_args

class Source:
    def __init__(self):self.calls=[];self.closed=False
    def verify_identity(self):self.calls.append('identity');return '91'
    def get_artwork_original_pages(self,wid):self.calls.append('pages');return ('https://i.pximg.net/fixture.png',)
    def get_image_bytes(self,url):
        self.calls.append('image');out=BytesIO();Image.new('RGB',(4,4),'blue').save(out,'PNG');return out.getvalue()
    def close(self):self.closed=True

def create(path):
    return ApplicationServices(1,live_client=Source(),pixiv_download_root=path/'static',
        novel_fixture_root=path/'novel',novel_fixture_source=build_novel_ui_fixture(),
        ugoira_root=path/'ugoira',ugoira_source=build_ugoira_fixture())

def wait(owner,jid):
    with owner._condition:
        assert owner._condition.wait_for(lambda:any(j.job_id==jid and j.state in ('done','error') for j in owner.snapshot()),15)
    assert next(j for j in owner.snapshot() if j.job_id==jid).state=='done'

@pytest.mark.asyncio
async def test_three_media_same_work_id_independent_receipts_and_restart(tmp_path):
    s=create(tmp_path)
    try:
        assert isinstance(s.jobs,MediaJobs) and s.live_client.calls==[]
        token=uuid4().hex
        record=RemoteBookmark('123','illustration','show','fixture','91','author',(),'','',1,0,0)
        s.remember_managed_work_id(token,record)
        a=s.enqueue_managed_download(record,'same-operation',owner=token)
        n=NovelSavePresentation(s);b=n.enqueue(n.preview('123','BOTH'),'same-operation')
        c=s.ugoira.enqueue_selection(s.ugoira.preview('123','91','gif'),'same-operation')
        for owner,job in ((s.static_jobs,a),(s.novel_jobs.jobs,b),(s.ugoira.jobs,c)):
            await asyncio.to_thread(wait,owner,job.job_id)
        snapshot=await s.refresh_library_snapshot()
        assert [w.work_id for w in snapshot.works]==['123']
        assert len(n.library())==len(s.ugoira.library())==1
        rows=s.jobs.snapshot()
        assert len(rows)==3
    finally:s.close()
    assert s.static_jobs.closed and s.jobs.closed and s.live_client.closed
    s=create(tmp_path)
    try:
        assert s.jobs.snapshot()==rows and s.live_client.calls==[]
        assert len(s.library_snapshot().works)==1
        assert len(NovelSavePresentation(s).library())==len(s.ugoira.library())==1
    finally:s.close()


def test_cli_three_disjoint_managed_roots(tmp_path):
    args=['--pixiv-cookie-file',str(tmp_path/'credentials/cookies.txt'),'--pixiv-download-root',str(tmp_path/'static'),
          '--novel-download-root',str(tmp_path/'novel'),'--ugoira-root',str(tmp_path/'ugoira')]
    result=parse_args(args)
    assert result.pixiv_download_root!=result.novel_download_root
    with pytest.raises(SystemExit):parse_args(args[:-1]+[str(tmp_path/'static/nested')])


@pytest.mark.asyncio
async def test_explicit_discovery_save_dispatches_ugoira_and_replays_after_restart(tmp_path):
    s=create(tmp_path)
    details=[]
    s.live_client.get_discovery_work=lambda wid:(details.append(wid) or {'id':wid,'user_id':'91','illust_type':2})
    try:
        job=await s.save_explicit_work('123','discovery-ugoira')
        await asyncio.to_thread(wait,s.ugoira.jobs,job.job_id)
        assert not s.static_jobs.snapshot()
        assert s.saved_discovery_ids()==frozenset({'123'})
        assert (await s.save_explicit_work('123','discovery-ugoira')).job_id==job.job_id
        assert details==['123']
        assert await s.save_explicit_work('123','closed-view',is_current=lambda:False) is None
    finally:s.close()
    s=create(tmp_path)
    try:
        s.live_client.get_discovery_work=lambda wid:pytest.fail('replay must not perform GET')
        assert (await s.save_explicit_work('123','discovery-ugoira')).job_id==job.job_id
        with pytest.raises(ValueError):await s.save_explicit_work('124','discovery-ugoira')
    finally:s.close()


@pytest.mark.asyncio
async def test_explicit_static_dispatch_keeps_owner_and_stable_operation(tmp_path):
    s=create(tmp_path)
    record=RemoteBookmark('123','illustration','show','fixture','91','author',(),'','',1,0,0)
    s.live_client.get_discovery_work=lambda wid:{'id':wid,'user_id':'91','illust_type':0}
    s.live_client.get_artwork_download_record=lambda wid:record
    try:
        job=await s.save_explicit_work('123','discovery-static')
        await asyncio.to_thread(wait,s.static_jobs,job.job_id)
        assert (await s.save_explicit_work('123','discovery-static')).job_id==job.job_id
        assert len(s.static_jobs.snapshot())==1 and not s.ugoira.jobs.snapshot()
    finally:s.close()
