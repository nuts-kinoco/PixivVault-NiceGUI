"""Contract checks for the generated P3 browser fixture; no browser or live I/O."""
import asyncio
from dataclasses import asdict
from pathlib import Path
import socket
import urllib.request
from uuid import uuid4
from fastapi import FastAPI
import pytest
from tools.p3_residuals_fixtures import FixtureReader,create_services


@pytest.fixture(autouse=True)
def prohibit_network(monkeypatch):
    def fail(*args,**kwargs):raise AssertionError('fixture test attempted network I/O')
    monkeypatch.setattr(socket,'create_connection',fail)
    monkeypatch.setattr(urllib.request.OpenerDirector,'open',fail)


def wait_done(owner,job):
    with owner._condition:
        assert owner._condition.wait_for(
            lambda:any(j.job_id==job.job_id and j.state in ('done','error','stopped') for j in owner.snapshot()),15)
    current=next(j for j in owner.snapshot() if j.job_id==job.job_id)
    assert current.state=='done',asdict(current)
    return current


def test_fixture_normalized_discovery_type_and_no_static_animation_fallback():
    reader=FixtureReader()
    static=reader.get_discovery_work('1')
    animated=reader.get_discovery_work('123')
    assert static['illust_type']==static['type']==0
    assert animated['illust_type']==animated['type']==2
    assert static['is_masked'] is False and static['user_id']=='91'
    assert static['page_count']==1
    with pytest.raises(ValueError,match='ugoira owner'):
        reader.get_artwork_download_record('123')
    assert {r['illust_type'] for r in reader.search_recommendation_illusts('landscape')['works']}=={0}


@pytest.mark.asyncio
async def test_fixture_review_save_uses_real_common_dispatch_and_receipt(tmp_path):
    services,reader=create_services(FastAPI(),tmp_path/'data')
    try:
        assert reader.calls==[]
        job=await services.save_explicit_work('1','fixture-review-save')
        saved=await asyncio.to_thread(wait_done,services.static_jobs,job)
        assert saved.result
        assert len(services.managed_downloads.saved_receipts())==1
        assert ('detail','1') in reader.calls and ('download-record','1') in reader.calls
        assert ('original-pages','1') in reader.calls
        before=len(reader.calls)
        replay=await services.save_explicit_work('1','fixture-review-save')
        assert replay.job_id==job.job_id and len(reader.calls)==before
        assert not services._direct_download_candidates
    finally:services.close()


@pytest.mark.asyncio
async def test_fixture_animation_dispatch_uses_original_frames_owner(tmp_path):
    services,reader=create_services(FastAPI(),tmp_path/'data')
    try:
        preferences=services.preferences.snapshot()
        services.preferences.update({'ugoira_save_format':'folder'},expected_revision=preferences.revision)
        job=await services.save_explicit_work('123','fixture-animation-save')
        await asyncio.to_thread(wait_done,services.ugoira.jobs,job)
        assert len(services.ugoira.library())==1
        assert not services.static_jobs.snapshot()
        assert ('download-record','123') not in reader.calls
        assert ('archive','123') in reader.calls
    finally:services.close()


def test_fixture_author_three_media_preview_save_restart(tmp_path):
    root=tmp_path/'data'
    services,reader=create_services(FastAPI(),root)
    try:
        prefs=services.preferences.snapshot()
        services.preferences.update({'novel_save_format':'both','ugoira_save_format':'folder'},
            expected_revision=prefs.revision)
        candidates=services.author_media_preview('91')
        assert {c.key for c in candidates}=={'701','novel:501','ugoira:123'}
        assert not services.jobs.snapshot()
        assert not any(c[0] in ('original-pages','archive') for c in reader.calls)
        jobs=[]
        for candidate in candidates:
            accepted,created,_=services.enqueue_author_media(candidate,uuid4().hex,origin='id')
            assert created
            jobs.append(accepted)
        for owner in (services.static_jobs,services.novel_jobs.jobs,services.ugoira.jobs):
            for job in owner.snapshot():wait_done(owner,job)
        proofs=services.author_media.reconcile()[0]
        assert set(proofs)=={'701','novel:501','ugoira:123'}
        assert [(r.work_id,r.output_format) for r in services.novel_jobs.saved_entries()]==[('501','BOTH')]
        assert len(services.ugoira.library())==1
        saved_jobs=[(j.job_id,j.state,j.result) for j in services.jobs.snapshot()]
    finally:services.close()
    services,reader=create_services(FastAPI(),root)
    try:
        assert reader.calls==[]
        assert [(j.job_id,j.state,j.result) for j in services.jobs.snapshot()]==saved_jobs
        assert services.author_media.reconcile()[0]==proofs
        assert len(services.managed_downloads.saved_receipts())==1
        assert len(services.novel_jobs.saved_entries())==1
        assert len(services.ugoira.library())==1
        assert reader.calls==[]
    finally:services.close()
