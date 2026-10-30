"""Malformed author journal rows stay inert while independent rows reconcile."""
import json
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from tools.p3_residuals_fixtures import create_services
from pixivvault_nicegui.author_media import AuthorMediaCandidate,canonical
from pixivvault_nicegui.author_download import Difference


@pytest.fixture
def journal(tmp_path,monkeypatch):
    services,_=create_services(FastAPI(),tmp_path/'data');owner=services.author_media;records=[];specs={};calls=[]
    with owner._db() as db:
        for index,(wid,aid) in enumerate([('301','23'),('303','24')],1):
            candidate=AuthorMediaCandidate('illustration',wid,aid,'2026-10-27',1,'synthetic',Difference.NEW)
            op=str(index)*32;jid=str(index+2)*32;raw=canonical(candidate.admission());specs[op]=raw
            db.execute('INSERT INTO admissions(operation_id,media_key,spec,job_id) VALUES(?,?,?,?)',(op,wid,raw,jid))
            records.append(SimpleNamespace(job_id=jid,state='done',result='fixture-receipt-'+wid,op=op,wid=wid))
    jobs=SimpleNamespace(snapshot=lambda:tuple(records),operation_ids=lambda jid:tuple(r.op for r in records if r.job_id==jid))
    monkeypatch.setattr(owner,'_owner_jobs',lambda kind:jobs)
    def proof(kind,wid,fmt):calls.append(wid);return dict(receipt='fixture-receipt-'+wid,sha256='a'*64)
    monkeypatch.setattr(owner,'_proof',proof)
    try:yield owner,records,specs,calls
    finally:services.close()


def read_row(owner,op):
    with owner._db() as db:return db.execute('SELECT spec,job_id,fingerprint,proof FROM admissions WHERE operation_id=?',(op,)).fetchone()


def write_spec(owner,op,raw):
    with owner._db() as db:db.execute('UPDATE admissions SET spec=? WHERE operation_id=?',(raw,op))


def test_known_bad_spec_is_local_preserved_and_recovers_without_rebinding(journal):
    owner,records,specs,calls=journal;op=records[0].op;value=json.loads(specs[op]);value.pop('output_format');write_spec(owner,op,canonical(value));bad=read_row(owner,op)
    sources=owner.completed_projection_sources()
    assert [v['author_id'] for v in sources]==['24'] and calls==['303']
    assert owner.projection_unresolved_authors()==({'23'},False)
    assert owner.projection_unresolved_authors('24')==(set(),False)
    assert read_row(owner,op)==bad and op in owner.reconcile_errors
    write_spec(owner,op,specs[op]);calls.clear()
    assert {v['author_id'] for v in owner.completed_projection_sources()}=={'23','24'}
    assert owner.projection_unresolved_authors()==(set(),False) and owner.reconcile_errors=={}
    assert read_row(owner,op)[0]==specs[op]


@pytest.mark.parametrize('bad',['broken-json','duplicate-author'])
def test_unknown_bad_spec_does_not_stop_independent_output_or_invent_author(journal,bad):
    owner,records,specs,calls=journal;op=records[0].op
    raw='{broken' if bad=='broken-json' else '{"author_id":"99",'+specs[op][1:]
    write_spec(owner,op,raw);before=read_row(owner,op)
    assert [v['author_id'] for v in owner.completed_projection_sources()]==['24']
    assert calls==['303'] and owner.projection_unresolved_authors()==(set(),True)
    assert read_row(owner,op)==before


def test_bound_spec_author_change_is_detected_before_scoped_filter(journal):
    owner,records,specs,calls=journal;op=records[0].op
    assert len(owner.completed_projection_sources())==2
    original=read_row(owner,op);changed=json.loads(specs[op]);changed['author_id']='99';write_spec(owner,op,canonical(changed));bad=read_row(owner,op);calls.clear()
    assert [v['author_id'] for v in owner.completed_projection_sources_for_author('24')]==['24']
    assert calls==['303'] and owner.projection_unresolved_authors('24')==(set(),True)
    assert read_row(owner,op)==bad and owner.reconcile_errors[op]['phase']=='binding'
    write_spec(owner,op,specs[op]);owner.completed_projection_sources()
    assert owner.reconcile_errors=={} and read_row(owner,op)[2]==original[2]


def test_proof_io_failure_is_local_and_does_not_mutate_failed_row(journal,monkeypatch):
    owner,records,specs,calls=journal;op=records[0].op;before=read_row(owner,op)
    def proof(kind,wid,fmt):
        calls.append(wid)
        if wid=='301':raise PermissionError('PRIVATE-SYNTHETIC-DO-NOT-DISPLAY')
        return dict(receipt='fixture-receipt-'+wid,sha256='a'*64)
    monkeypatch.setattr(owner,'_proof',proof)
    assert [v['author_id'] for v in owner.completed_projection_sources()]==['24']
    assert calls==['301','303'] and owner.projection_unresolved_authors()==({'23'},False)
    assert read_row(owner,op)==before
    assert 'PRIVATE-SYNTHETIC' not in '\n'.join(owner.reconcile_status_messages())



def test_one_journal_write_failure_never_publishes_uncommitted_evidence(journal,monkeypatch):
    from contextlib import contextmanager
    import sqlite3
    owner,records,specs,calls=journal;op=records[0].op;before=read_row(owner,op);real=owner._db;fired=[]
    @contextmanager
    def guarded():
        with real() as db:
            class Connection:
                def execute(self,sql,parameters=()):
                    if sql.startswith('UPDATE admissions SET job_id=') and parameters[-1]==op:
                        fired.append(op);raise sqlite3.OperationalError('synthetic journal write failure')
                    return db.execute(sql,parameters)
            yield Connection()
    monkeypatch.setattr(owner,'_db',guarded)
    proofs,operations,fingerprints=owner.reconcile()
    assert fired==[op] and '301' not in proofs and op not in operations and op not in fingerprints
    assert records[1].op in operations and '303' in proofs
    assert read_row(owner,op)==before and owner.reconcile_errors[op]['phase']=='journal_write'
    assert [v['author_id'] for v in owner.completed_projection_sources()]==['24']
