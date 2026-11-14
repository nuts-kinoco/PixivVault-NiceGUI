"""Isolated Q07/Q17 preferences regression, real service paths, generated data only."""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
import sqlite3
from uuid import uuid4

import pytest
from pixivvault_nicegui.managed_novel_jobs import PinnedNovelSource
from pixivvault_nicegui.preferences import PreferencesError
from pixivvault_nicegui.services import ApplicationServices


class GeneratedReader:
    def __init__(self):
        self.calls = []
        self.wrong_author = False
    def require_identity(self): return '12'
    def verify_identity(self): return '12'
    def get_novel_payload(self, wid, aid):
        self.calls.append((wid, aid))
        return dict(id=wid, userId='99' if self.wrong_author else aid,
                    userName='Synthetic', title='Generated '+wid,
                    content='Generated offline body '+wid)
    def close(self): pass


def fixture(tmp_path, count=1, prefs=True):
    frozen = tmp_path / 'frozen'
    frozen.mkdir()
    db = frozen / 'failed.sqlite3'
    with sqlite3.connect(db) as con:
        con.execute('CREATE TABLE failed_queue(work_id TEXT,user_id TEXT,title TEXT,'
                    'work_type TEXT,error_reason TEXT,failed_at TEXT,retry_count INTEGER)')
        con.executemany('INSERT INTO failed_queue VALUES(?,?,?,?,?,?,?)', [
            (str(1001+i),'23','generated '+str(1001+i),'novel','Timeout','2026-11-05',1)
            for i in range(count)])
    reader = GeneratedReader()
    kwargs = dict(live_client=reader,
                  pixiv_download_root=tmp_path/'images',
                  failed_queue_readonly_db=db,
                  novel_fixture_root=tmp_path/'novels',
                  novel_fixture_source=PinnedNovelSource(reader, {}))
    if prefs:
        kwargs['preferences_root'] = tmp_path / 'prefs'
    return ApplicationServices(**kwargs), reader, db


async def terminal(service, jobid):
    for _ in range(1000):
        job = next(j for j in service.jobs.snapshot() if j.job_id == jobid)
        if job.state in ('done', 'error', 'stopped'):
            return job
        await asyncio.sleep(.01)
    raise AssertionError('generated queue failed to settle')


@pytest.mark.asyncio
@pytest.mark.parametrize('selected,expected', [('epub','EPUB'),('txt','TXT'),('both','BOTH')])
async def test_prefs_connected_retry_uses_explicit_saved_format(tmp_path, selected, expected):
    service, reader, frozen = fixture(tmp_path)
    original = hashlib.sha256(frozen.read_bytes()).hexdigest()
    try:
        before = service.preferences.snapshot()
        service.preferences.update({'novel_save_format':selected}, expected_revision=before.revision)
        failure = (await service.read_failed_records())[0]
        assert service.supports_legacy_failed_retry(failure)
        owner = 'a' * 32
        assert reader.calls == [] and service.jobs.snapshot() == ()
        await service.wait_legacy_retry_capacity(failure, is_current=lambda: True)
        intent = await service.lookup_legacy_failed_original(failure, owner=owner)
        assert (intent.work_id,intent.author_id,intent.output_format) == ('1001','23',expected)
        assert reader.calls == []
        job = service.enqueue_legacy_failed_original(failure, intent, uuid4().hex, owner=owner)
        finished = await terminal(service, job.job_id)
        assert finished.state == 'done' and finished.attempt == 1
        saved = service.novel_jobs.current('1001',expected)
        assert saved is not None and saved.relative_receipt == finished.result
        assert saved.output_format == expected
        assert reader.calls == [('1001','23')]
        assert hashlib.sha256(frozen.read_bytes()).hexdigest() == original
    finally:
        service.close()


@pytest.mark.asyncio
async def test_prefs_connected_filtered_batch_respects_format_and_all_rows(tmp_path):
    service, reader, frozen = fixture(tmp_path,count=3)
    original = frozen.read_bytes()
    try:
        snapshot=service.preferences.snapshot()
        service.preferences.update({'novel_save_format':'txt'},expected_revision=snapshot.revision)
        rows=await service.read_failed_records()
        assert len(rows)==3
        accepted=[]
        owner='b'*32
        for row in rows:
            await service.wait_legacy_retry_capacity(row,is_current=lambda:True)
            intent=await service.lookup_legacy_failed_original(row,owner=owner)
            assert intent.output_format == 'TXT'
            accepted.append(service.enqueue_legacy_failed_original(row,intent,uuid4().hex,owner=owner))
        assert len(accepted)==3
        for job in accepted:
            assert (await terminal(service,job.job_id)).state=='done'
        assert {str(w) for w,_ in reader.calls}=={'1001','1002','1003'}
        assert len(reader.calls)==3
        assert frozen.read_bytes()==original
    finally:
        service.close()


@pytest.mark.asyncio
async def test_prefs_connected_wrong_author_does_not_publish(tmp_path):
    service, reader, db=fixture(tmp_path)
    reader.wrong_author=True
    try:
        failure=(await service.read_failed_records())[0]
        owner='c'*32
        await service.wait_legacy_retry_capacity(failure,is_current=lambda:True)
        intent=await service.lookup_legacy_failed_original(failure,owner=owner)
        job=service.enqueue_legacy_failed_original(failure,intent,uuid4().hex,owner=owner)
        assert (await terminal(service,job.job_id)).state=='error'
        assert service.novel_jobs.current('1001','EPUB') is None
    finally:service.close()


def test_invalid_format_rejected_by_real_preferences_store(tmp_path):
    service, reader, db=fixture(tmp_path)
    try:
        old=service.preferences.snapshot()
        with pytest.raises(PreferencesError):
            service.preferences.update({'novel_save_format':'PDF'},expected_revision=old.revision)
        assert service.preferences.snapshot().revision==old.revision
        assert service._legacy_novel_retry_intent(type('F', (), {'work_id':'1001','user_id':'23'})()).output_format=='EPUB'
        assert reader.calls==[] and service.jobs.snapshot()==()
    finally:service.close()


def test_absent_preferences_preserves_original_epub_fallback(tmp_path):
    service,reader,db=fixture(tmp_path,prefs=False)
    try:
        assert service.preferences is None
        assert service._legacy_novel_retry_intent(type('F', (), {'work_id':'1001','user_id':'23'})()).output_format=='EPUB'
        assert reader.calls==[] and service.jobs.snapshot()==()
    finally:service.close()
