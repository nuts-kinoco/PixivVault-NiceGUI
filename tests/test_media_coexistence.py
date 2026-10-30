from threading import Event
import pytest
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.novel_save_presentation import build_novel_ui_fixture, NovelSavePresentation
from pixivvault_nicegui.ugoira_fixtures import build_ugoira_fixture
from pixivvault_nicegui.media_jobs import MediaJobs

def wait(owner,jid,state):
    with owner.jobs._condition:
        assert owner.jobs._condition.wait_for(lambda:any(j.job_id==jid and j.state==state for j in owner.jobs.snapshot()),20)
    return next(j for j in owner.jobs.snapshot() if j.job_id==jid)

def create(root):
    return ApplicationServices(1,novel_fixture_root=root/'novel',novel_fixture_source=build_novel_ui_fixture(),
                               ugoira_root=root/'ugoira',ugoira_source=build_ugoira_fixture())

def test_media_share_queue_but_keep_receipts_and_operation_namespaces(tmp_path):
    services=create(tmp_path)
    try:
        assert isinstance(services.jobs,MediaJobs)
        n=NovelSavePresentation(services)
        nj=n.enqueue(n.preview('123','BOTH'),'same-operation')
        uj=services.ugoira.enqueue_selection(services.ugoira.preview('123','91','gif'),'same-operation')
        wait(services.novel_jobs,nj.job_id,'done');wait(services.ugoira,uj.job_id,'done')
        rows=services.jobs.snapshot()
        assert {j.kind for j in rows}=={'novel-export','ugoira-gif'}
        assert services.jobs.operation_ids(nj.job_id)==services.jobs.operation_ids(uj.job_id)==('same-operation',)
        assert len(n.library())==len(services.ugoira.library())==1
        assert '合成データ' in n.read_saved(n.library()[0])
        assert services.ugoira.playback(services.ugoira.library()[0])['duration_ms']==300
    finally:services.close()
    assert services.jobs.closed
    services=create(tmp_path)
    try:
        assert services.jobs.snapshot()==rows
        assert len(NovelSavePresentation(services).library())==len(services.ugoira.library())==1
        assert services.ugoira_source.reader.calls==[]
    finally:services.close()

def test_media_control_revision_routes_only_to_own_ledger(tmp_path):
    s=create(tmp_path);gate=Event()
    original=s.ugoira.jobs._runner
    def blocked(job,ctx):
        while not gate.wait(.01):ctx.checkpoint()
        return original(job,ctx)
    s.ugoira.jobs._runner=blocked
    try:
        n=NovelSavePresentation(s);nj=n.enqueue(n.preview('123','TXT'),'novel')
        uj=s.ugoira.enqueue_selection(s.ugoira.preview('123','91','frames'),'ugoira')
        running=wait(s.ugoira,uj.job_id,'running')
        assert not s.jobs.control(uj.job_id,'pause',expected_control_revision=running.control_revision+1)
        assert s.jobs.control(uj.job_id,'pause',expected_control_revision=running.control_revision)
        paused=wait(s.ugoira,uj.job_id,'paused')
        assert s.jobs.control(uj.job_id,'stop',expected_control_revision=paused.control_revision)
        wait(s.ugoira,uj.job_id,'stopped');wait(s.novel_jobs,nj.job_id,'done')
        assert len(NovelSavePresentation(s).library())==1 and not s.ugoira.library()
    finally:gate.set();s.close()

@pytest.mark.parametrize('suffix',['','nested'])
def test_overlapping_media_roots_rejected_before_writes(tmp_path,suffix):
    a=tmp_path/'data';b=a/suffix
    with pytest.raises(ValueError,match='disjoint'):
        ApplicationServices(1,novel_fixture_root=a,novel_fixture_source=build_novel_ui_fixture(),ugoira_root=b,ugoira_source=build_ugoira_fixture())
    assert not a.exists()

def test_combined_fixture_cli_is_explicit(tmp_path):
    from pixivvault_nicegui.bootstrap import parse_args
    config=parse_args(['--novel-fixture-root',str(tmp_path/'n'),'--ugoira-fixture-root',str(tmp_path/'u')])
    assert config.novel_fixture_root!=config.ugoira_fixture_root
    with pytest.raises(SystemExit):parse_args(['--novel-fixture-root',str(tmp_path/'n'),'--ugoira-fixture-root',str(tmp_path/'n')])
