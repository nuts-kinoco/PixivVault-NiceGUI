"""Generated media only: cross-owner admission, durable reconciliation and capacity."""
from dataclasses import replace
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from uuid import uuid4
import pytest
from pixivvault_nicegui.author_media import AuthorMediaService, AuthorMediaCandidate, media_key, split_media_key
from pixivvault_nicegui.author_download import Difference
from pixivvault_nicegui.managed_novel_jobs import ManagedNovelJobs, PinnedNovelSource, NovelJobIntent
from pixivvault_nicegui.ugoira_practical import PracticalUgoiraJobs, PinnedUgoiraSource
from pixivvault_nicegui.ugoira_fixtures import FixtureUgoiraReader
from pixivvault_nicegui.managed_scheduler import ManagedScheduler, submit_author_batch


class Reader(FixtureUgoiraReader):
    date='2026-10-26T00:00:00+00:00'
    def verify_identity(self): return '91'
    def get_author_artworks_complete(self,aid):
        assert aid=='91'
        return (dict(id='123',user_id=aid,type=2,page_count=1,title='Synthetic',update_date=self.date),)
    def get_author_novel_ids(self,aid): return ('123',)
    def get_artwork_metadata_raw(self,wid,aid):
        return dict(super().get_artwork_metadata_raw(wid,aid),updateDate=self.date)
    def get_novel_payload(self,wid,aid):
        return dict(id=wid,userId=aid,userName='Author',title='Novel',content='Synthetic body '+self.date,
                    seriesNavData=None,textEmbeddedImages={},updateDate=self.date)


class Combined:
    def __init__(self,*owners): self.owners=owners
    def snapshot(self): return tuple(j for o in self.owners for j in o.snapshot())


def wait(owner,job,state='done'):
    with owner.jobs._condition:
        assert owner.jobs._condition.wait_for(lambda:any(j.job_id==job.job_id and j.state==state for j in owner.jobs.snapshot()),15),owner.jobs.snapshot()
    return next(j for j in owner.jobs.snapshot() if j.job_id==job.job_id)


@pytest.fixture
def services(tmp_path):
    reader=Reader()
    static=tmp_path/'static'; static.mkdir()
    source=PinnedNovelSource(reader,{})
    novel=ManagedNovelJobs(tmp_path/'novel',source)
    ugoira=PracticalUgoiraJobs(tmp_path/'ugoira',PinnedUgoiraSource(reader))
    s=SimpleNamespace(closing=False,managed_downloads=SimpleNamespace(root=static),live_client=reader,
        novel_jobs=novel,novel_fixture_source=source,ugoira=ugoira,legacy_author_bridge=None)
    s.jobs=Combined(novel.jobs,ugoira.jobs)
    s.enqueue_novel_export=novel.enqueue
    s.author_media=AuthorMediaService(s)
    s.author_media_preview=s.author_media.preview
    s.enqueue_author_media=s.author_media.enqueue
    yield s
    s.author_media.close(); novel.close(); ugoira.close()


def test_media_namespace_preserves_static_and_prevents_novel_collision():
    assert media_key('illustration','123')=='123'
    assert {media_key(k,'123') for k in ('illustration','novel','ugoira')}=={'123','novel:123','ugoira:123'}
    assert split_media_key('123')==('illustration','123')
    with pytest.raises(ValueError): split_media_key('novel:001')


@pytest.mark.parametrize('fmt',['TXT','EPUB','BOTH'])
def test_both_queue_receipt_update_and_journal_restart(services,fmt):
    s=services
    candidates=s.author_media.preview('91',novel_format=fmt,ugoira_format='frames')
    assert {c.key for c in candidates}=={'novel:123','ugoira:123'}
    assert all(c.difference is Difference.NEW for c in candidates)
    for c in candidates:
        op=uuid4().hex
        job,created,fp=s.author_media.enqueue(c,op)
        assert created
        wait(s.novel_jobs if c.kind=='novel' else s.ugoira,job)
        assert s.author_media.enqueue(c,op)==(job,False,fp) or s.author_media.enqueue(c,op)[0].job_id==job.job_id
    proofs,ops,fps=s.author_media.reconcile()
    assert set(proofs)=={'novel:123','ugoira:123'} and len(ops)==2
    s.author_media.close(); s.author_media=AuthorMediaService(s)
    assert s.author_media.reconcile()[0]==proofs
    current=s.author_media.preview('91',novel_format=fmt,ugoira_format='frames')
    assert all(c.difference is Difference.SKIPPED for c in current)
    s.live_client.date='2026-10-27T00:00:00+00:00'
    newer=s.author_media.preview('91',novel_format=fmt,ugoira_format='frames')
    assert all(c.difference is Difference.UPDATED for c in newer)
    old_receipts={c.key:s.author_media._proof(c.kind,c.work_id,c.output_format) for c in newer}
    for c in newer:
        job,created,_=s.author_media.enqueue(c,uuid4().hex)
        assert created
        wait(s.novel_jobs if c.kind=='novel' else s.ugoira,job)
    assert all(s.author_media._proof(c.kind,c.work_id,c.output_format)!=old_receipts[c.key] for c in newer)


def test_old_saved_without_admission_date_is_unknown(services):
    s=services
    job=s.ugoira.enqueue_selection(s.ugoira.preview('123','91','frames'),'old')
    wait(s.ugoira,job)
    source=s.novel_fixture_source; source.select('123','91')
    job=s.novel_jobs.enqueue(NovelJobIntent('123','91','TXT'),'old')
    wait(s.novel_jobs,job)
    assert all(c.difference is Difference.UNKNOWN for c in s.author_media.preview('91',novel_format='TXT',ugoira_format='frames'))


def test_stale_preview_and_replay_different_revision_rejected(services):
    s=services
    candidate=s.author_media.preview('91','novel')[0]
    s.live_client.date='changed'
    with pytest.raises(ValueError,match='revision changed'): s.author_media.enqueue(candidate,uuid4().hex)
    assert s.jobs.snapshot()==()


def test_author_setting_revision_fences_new_admission_but_not_existing_replay(services):
    s=services
    before=s.author_media.author_target('91')
    candidate=s.author_media.preview('91','novel')[0]
    fenced=replace(candidate,author_setting_revision=before.revision,
                   author_setting_target=before.target_type)
    operation=uuid4().hex
    job,created,_=s.author_media.enqueue(fenced,operation)
    assert created
    wait(s.novel_jobs,job)

    s.author_media.configure_author_target('91','illust',expected_revision=before.revision)
    assert s.author_media.enqueue(fenced,operation)[1] is False  # durable replay
    with pytest.raises(ValueError,match='author settings changed'):
        s.author_media.enqueue(fenced,uuid4().hex)
    assert len(s.novel_jobs.jobs.snapshot())==1

    # A deliberate, new per-run override is not silently constrained by the
    # author-wide setting. It still must pass the normal source/receipt checks.
    s.live_client.date='2026-10-27T00:00:00+00:00'
    override=s.author_media.preview('91','novel')[0]
    assert override.author_setting_revision is None
    new_job,created,_=s.author_media.enqueue(override,uuid4().hex)
    assert created
    wait(s.novel_jobs,new_job)


def test_enqueue_bind_crash_recovers_exact_job(services):
    s=services; c=s.author_media.preview('91','novel')[0]
    original=s.author_media._bind
    s.author_media._bind=lambda *args: (_ for _ in ()).throw(OSError('synthetic lost bind'))
    op=uuid4().hex
    with pytest.raises(OSError): s.author_media.enqueue(c,op)
    s.author_media._bind=original
    job=s.novel_jobs.jobs.snapshot()[0]; wait(s.novel_jobs,job)
    proofs,ops,fps=s.author_media.reconcile()
    assert proofs[c.key]==fps[op] and ops[op]==job.job_id
    assert s.author_media.enqueue(c,op)[1] is False
    with pytest.raises(ValueError,match='replay differs'):
        s.author_media.enqueue(replace(c,update_date='different'),op)


def test_wrong_or_missing_receipt_is_not_saved(services):
    s=services; c=s.author_media.preview('91','novel')[0]
    job,_,_=s.author_media.enqueue(c,uuid4().hex); wait(s.novel_jobs,job)
    actual=s.author_media._proof
    s.author_media._proof=lambda *args:dict(receipt='other/receipt.json',sha256='0'*64)
    assert s.author_media.reconcile()[0]=={}
    s.author_media._proof=actual


def test_aggregate_capacity_blocks_other_media(services):
    s=services; c=s.author_media.preview('91','novel')[0]
    s.jobs=SimpleNamespace(snapshot=lambda:tuple(SimpleNamespace(state='queued') for _ in range(50)))
    with pytest.raises(RuntimeError,match='capacity'): s.author_media.enqueue(c,uuid4().hex)
    assert s.novel_jobs.jobs.snapshot()==()


def test_scheduler_both_namespaced_backlog_and_restart(tmp_path):
    root=tmp_path/'scheduler'; root.mkdir()
    ledger=ManagedScheduler(root)
    ledger.set_favourites(('91',),expected_revision=ledger.snapshot().revision)
    rows=tuple(AuthorMediaCandidate(kind,str(i),'91','date',1,'Synthetic',Difference.NEW)
               for i in range(1,28) for kind in ('illustration','novel'))
    pending=[];calls=[]
    def enqueue(c,operation,origin):
        calls.append((c.key,operation))
        job=SimpleNamespace(job_id=uuid4().hex,state='queued')
        pending.append(job)
        return job,True,'a'*64
    service=SimpleNamespace(closing=False,author_media=object(),jobs=SimpleNamespace(snapshot=lambda:tuple(pending)),
        author_media_preview=lambda *a,**kw:rows,enqueue_author_media=enqueue)
    result=submit_author_batch(service,['91'],cancellation=Event(),ledger=ledger,slot='first')
    assert result['accepted']==50 and result['deferred']==4
    assert ledger.deferred_count()==4
    assert len({key for key,_ in calls})==50
    ledger.close(); ledger=ManagedScheduler(root)
    try:
        pending.clear()
        result=submit_author_batch(service,['91'],cancellation=Event(),ledger=ledger,slot='first')
        assert result['accepted']==4 and len(calls)==54 and ledger.deferred_count()==0
    finally: ledger.close()


def test_cancelled_dispatch_preserves_media_backlog(tmp_path):
    root=tmp_path/'scheduler';root.mkdir();ledger=ManagedScheduler(root)
    ledger.set_favourites(('91',),expected_revision=ledger.snapshot().revision)
    cancel=Event()
    c=AuthorMediaCandidate('novel','123','91','date',1,'Synthetic',Difference.NEW)
    def preview(*a,**kw): cancel.set(); return (c,)
    s=SimpleNamespace(closing=False,author_media=object(),jobs=SimpleNamespace(snapshot=lambda:()),author_media_preview=preview)
    try:
        assert submit_author_batch(s,['91'],cancellation=cancel,ledger=ledger)['accepted']==0
        assert ledger.deferred_ids('91')==frozenset({'novel:123'})
    finally:ledger.close()

def test_real_three_owner_services_scheduler_and_restart(tmp_path):
    from io import BytesIO
    from PIL import Image
    from pixivvault_nicegui.services import ApplicationServices
    from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
    class ThreeReader(Reader):
        def get_author_artworks_complete(self,aid):
            return (*super().get_author_artworks_complete(aid),
                dict(id='124',user_id=aid,type=0,page_count=1,title='Static',update_date=self.date))
        def get_artwork_download_record(self,wid):
            assert wid=='124'
            return RemoteBookmark(wid,'illustration','show','Static','91','Author',(),'',self.date,1,0,0)
        def get_artwork_update_revision(self,wid,aid):
            return dict(work_id=wid,author_id=aid,illustration_type=0,page_count=1,update_date=self.date)
        def get_artwork_original_pages(self,wid): return (f'https://i.pximg.net/synthetic/{wid}_p0.png',)
        def get_image_bytes(self,url):
            out=BytesIO(); Image.new('RGB',(16,16),'blue').save(out,'PNG'); return out.getvalue()
    def open_service():
        reader=ThreeReader()
        return ApplicationServices(1,live_client=reader,pixiv_download_root=tmp_path/'static',
            novel_fixture_root=tmp_path/'novels',novel_fixture_source=PinnedNovelSource(reader,{}),
            ugoira_root=tmp_path/'ugoira',ugoira_source=PinnedUgoiraSource(reader))
    s=open_service()
    try:
        s.enable_managed_scheduler()
        s.managed_scheduler.set_favourites(('91',),expected_revision=s.managed_scheduler.snapshot().revision)
        result=submit_author_batch(s,['91'],cancellation=Event(),ledger=s.managed_scheduler,slot='all-three')
        assert result['accepted']==3
        for owner in (s.static_jobs,s.novel_jobs.jobs,s.ugoira.jobs):
            with owner._condition:
                assert owner._condition.wait_for(lambda:all(j.state=='done' for j in owner.snapshot()),15),owner.snapshot()
        s.reconcile_managed_scheduler()
        assert s.managed_scheduler.history()[0]['status']=='saved'
        proofs=s.author_media.reconcile()[0]
        assert set(proofs)=={'124','novel:123','ugoira:123'}
    finally:s.close()
    s=open_service()
    try:
        assert len(s.jobs.snapshot())==3
        assert all(j.state=='done' for j in s.jobs.snapshot())
        assert s.author_media.reconcile()[0]==proofs
        assert all(c.difference is Difference.SKIPPED for c in s.author_media.preview('91'))
    finally:s.close()

def test_capacity_changed_while_waiting_for_shared_admission_is_deferred(tmp_path):
    root=tmp_path/'scheduler';root.mkdir(); ledger=ManagedScheduler(root)
    ledger.set_favourites(('91',),expected_revision=ledger.snapshot().revision)
    pending=[]
    class Barrier:
        def __enter__(self): pending.extend(SimpleNamespace(state='queued') for _ in range(50))
        def __exit__(self,*args): pass
    c=AuthorMediaCandidate('novel','123','91','date',1,'Synthetic',Difference.NEW)
    s=SimpleNamespace(closing=False,author_media=object(),_media_admission_lock=Barrier(),
        jobs=SimpleNamespace(snapshot=lambda:tuple(pending)),author_media_preview=lambda *a,**kw:(c,))
    try:
        assert submit_author_batch(s,['91'],cancellation=Event(),ledger=ledger)['accepted']==0
        assert ledger.deferred_ids('91')==frozenset({'novel:123'})
    finally:ledger.close()


def test_reconcile_filters_expected_receipt_when_novel_formats_coexist(services):
    s=services; expected={}
    for fmt in ('TXT','EPUB'):
        c=s.author_media.preview('91','novel',novel_format=fmt)[0]
        job,_,fp=s.author_media.enqueue(c,uuid4().hex)
        wait(s.novel_jobs,job);expected[fmt]=fp
    for fp in expected.values():
        assert s.author_media.reconcile({'novel:123':frozenset({fp})})[0]=={'novel:123':fp}

def test_author_target_override_cas_clear_and_restart(services):
    s=services; source=s.author_media
    assert source.resolve_author_target('91')=='both'
    initial=source.author_target('91')
    saved=source.configure_author_target('91','novel',expected_revision=initial.revision)
    assert source.resolve_author_target('91')=='novel'
    assert {c.kind for c in source.preview('91')}=={'novel'}
    assert {c.kind for c in source.preview('91','illust')}=={'ugoira'}
    with pytest.raises(ValueError,match='another client'):
        source.configure_author_target('91','illust',expected_revision=initial.revision)
    source.close(); source=AuthorMediaService(s);s.author_media=source
    assert source.author_target('91')==saved
    cleared=source.configure_author_target('91',None,expected_revision=saved.revision)
    assert cleared.revision==saved.revision+1 and source.resolve_author_target('91')=='both'
    with pytest.raises(ValueError,match='another client'):
        source.configure_author_target('91','illust',expected_revision=0)
    assert source.resolve_author_target('92')=='both'


def test_scheduler_uses_author_override_without_changing_manual_selection(services,tmp_path):
    s=services
    s.author_media.configure_author_target('91','novel',expected_revision=0)
    root=tmp_path/'scheduler';root.mkdir();ledger=ManagedScheduler(root)
    try:
        result=submit_author_batch(s,['91'],cancellation=Event(),ledger=ledger)
        assert result['accepted']==1 and s.ugoira.jobs.snapshot()==()
        job=s.novel_jobs.jobs.snapshot()[0];wait(s.novel_jobs,job)
        assert s.author_media.preview('91','illust')[0].kind=='ugoira'
    finally:ledger.close()
