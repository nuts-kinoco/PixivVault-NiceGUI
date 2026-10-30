"""Author media admission over existing owners; no receipt is rewritten.

Bare illustration IDs preserve Phase9 ledgers. Novel/ugoira keys are namespaced.
The separate admission journal records revision + operation BEFORE enqueue and
binds success only to the exact accepted job's verified current receipt.
"""
from __future__ import annotations
from dataclasses import dataclass, field, replace
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from threading import RLock
from uuid import uuid4

from .author_download import Difference, decide_difference
from .job_service import UNFINISHED
from .fs_paths import native_io_path


def identifier(value):
    if type(value) is not str or re.fullmatch(r'[1-9][0-9]{0,19}', value) is None:
        raise ValueError('invalid media identifier')
    return value


def media_key(kind, work_id):
    identifier(work_id)
    if kind not in ('illustration', 'novel', 'ugoira'):
        raise ValueError('invalid author media kind')
    return work_id if kind == 'illustration' else kind + ':' + work_id


def split_media_key(key):
    if ':' not in key:
        return 'illustration', identifier(key)
    kind, wid = key.split(':', 1)
    if media_key(kind, wid) != key:
        raise ValueError('invalid media key')
    return kind, wid


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False)


@dataclass(frozen=True)
class AuthorMediaCandidate:
    kind: str
    work_id: str
    author_id: str
    update_date: str
    page_count: int
    title: str
    difference: Difference
    output_format: str = ''
    prior_token: str | None = field(default=None, repr=False)
    payload: object = field(default=None, repr=False)
    work_type: int = 0
    # A per-author UI preview may be invalidated by another client's CAS
    # update. Explicit one-shot target overrides leave this fence unset.
    author_setting_revision: int | None = field(default=None, repr=False, compare=False)
    author_setting_target: str | None = field(default=None, repr=False, compare=False)

    @property
    def key(self):
        return media_key(self.kind, self.work_id)

    @property
    def eligible(self):
        return self.difference in (Difference.NEW, Difference.UPDATED, Difference.RESTORED, Difference.MISSING)

    def deferred_work(self):
        return dict(id=self.key, update_date=self.update_date, page_count=self.page_count,
                    type=self.work_type)

    def admission(self):
        return dict(kind=self.kind, work_id=self.work_id, author_id=self.author_id,
                    update_date=self.update_date, output_format=self.output_format,
                    prior_token=self.prior_token, page_count=self.page_count, work_type=self.work_type,
                    ugoira_spec=sha256(canonical(self.payload.spec).encode()).hexdigest() if self.kind=='ugoira' and self.payload is not None else None,
                    source_pin=self.payload.intent.expected_source_sha256 if self.kind == 'novel'
                    and self.payload is not None else None)


@dataclass(frozen=True)
class AuthorTargetSetting:
    author_id: str
    target_type: str | None
    revision: int


class AuthorMediaService:
    """Synchronous adapter. Call from a worker thread; ownership stays in services.

    Construct only after all media owners. Root is already managed and locked
    by the static owner. Each journal connection is short-lived and FULL sync.
    """
    def __init__(self, services):
        from .managed_downloads import _safe_path
        self.services = services
        self.lock = RLock()
        self.closed = False
        self.reconcile_errors = {}
        self.path = Path(services.managed_downloads.root) / 'author-media.sqlite3'
        _safe_path(self.path, file=True)
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS admissions(operation_id TEXT PRIMARY KEY, media_key TEXT NOT NULL, spec TEXT NOT NULL, job_id TEXT, fingerprint TEXT, proof TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS author_targets(author_id TEXT PRIMARY KEY, target_type TEXT, revision INTEGER NOT NULL)')

    @contextmanager
    def _db(self):
        from .managed_downloads import _safe_path
        _safe_path(self.path, file=True)
        for suffix in ('-journal','-wal','-shm'):
            _safe_path(Path(str(self.path)+suffix), file=True)
        db = sqlite3.connect(native_io_path(self.path), timeout=30)
        db.execute('PRAGMA synchronous=FULL')
        try:
            with db:
                yield db
        finally:
            db.close()

    def _open(self):
        if self.closed or self.services.closing:
            raise RuntimeError('author media service unavailable')

    def author_target(self, author_id):
        identifier(author_id)
        with self.lock:
            self._open()
            with self._db() as db:
                row=db.execute('SELECT target_type,revision FROM author_targets WHERE author_id=?',(author_id,)).fetchone()
            if row is None:
                return AuthorTargetSetting(author_id,None,0)
            if row[0] not in (None,'both','illust','novel') or type(row[1]) is not int or row[1]<1:
                raise ValueError('invalid saved author target')
            return AuthorTargetSetting(author_id,*row)

    def resolve_author_target(self, author_id):
        return self.author_target(author_id).target_type or 'both'

    def configure_author_target(self, author_id, target_type, *, expected_revision):
        identifier(author_id)
        if target_type not in (None,'both','illust','novel') or type(expected_revision) is not int or expected_revision<0:
            raise ValueError('invalid author target setting')
        with self.lock:
            self._open()
            with self._db() as db:
                db.execute('BEGIN IMMEDIATE')
                row=db.execute('SELECT revision FROM author_targets WHERE author_id=?',(author_id,)).fetchone()
                revision=row[0] if row else 0
                if revision!=expected_revision:
                    raise ValueError('author target changed in another client; reload')
                next_revision=revision+1
                # Retain the row on clear, preventing stale revision-zero ABA.
                db.execute('INSERT INTO author_targets(author_id,target_type,revision) VALUES(?,?,?) ON CONFLICT(author_id) DO UPDATE SET target_type=excluded.target_type,revision=excluded.revision',
                           (author_id,target_type,next_revision))
            return AuthorTargetSetting(author_id,target_type,next_revision)

    def enable_zip_settings(self):
        with self.lock,self._db() as db:
            if 'auto_zip' not in {row[1] for row in db.execute('PRAGMA table_info(author_targets)')}:
                db.execute('ALTER TABLE author_targets ADD COLUMN auto_zip INTEGER')

    def author_zip_setting(self,author_id):
        identifier(author_id)
        with self.lock,self._db() as db:
            row=db.execute('SELECT auto_zip FROM author_targets WHERE author_id=?',(author_id,)).fetchone()
            value=None if row is None else row[0]
            if value not in (None,0,1):raise ValueError('invalid ZIP setting')
            return None if value is None else bool(value)

    def configure_author_zip(self,author_id,enabled,*,expected_revision):
        identifier(author_id)
        if enabled is not None and type(enabled) is not bool:raise ValueError('invalid ZIP setting')
        with self.lock,self._db() as db:
            self._open();db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT revision FROM author_targets WHERE author_id=?',(author_id,)).fetchone()
            revision=row[0] if row else 0
            if revision!=expected_revision:raise ValueError('author settings changed; reload')
            db.execute('INSERT INTO author_targets(author_id,target_type,revision,auto_zip) VALUES(?,NULL,?,?) ON CONFLICT(author_id) DO UPDATE SET revision=excluded.revision,auto_zip=excluded.auto_zip',(author_id,revision+1,None if enabled is None else int(enabled)))
            return revision+1

    def _owner_jobs(self, kind):
        owner = self.services.static_jobs if kind == 'illustration' else (
            self.services.novel_jobs.jobs if kind == 'novel' else self.services.ugoira.jobs)
        return owner

    def _proof(self, kind, wid, fmt):
        s = self.services
        if kind == 'illustration':
            value = s.managed_downloads.verified_saved_receipt(wid)
            if value is None:
                return None
            relative, receipt, digest, date, fingerprint = value
            return dict(receipt=relative, sha256=digest, fingerprint=fingerprint)
        if kind == 'novel':
            value = s.novel_jobs.current(wid, fmt)
            return None if value is None else dict(receipt=value.relative_receipt, sha256=value.receipt_sha256)
        value = s.ugoira.current(wid, fmt)
        return None if value is None else dict(value['proof'])

    def _date(self, key, proof):
        if proof is None:
            return None
        with self._db() as db:
            rows = db.execute('SELECT spec,proof FROM admissions WHERE media_key=? AND proof IS NOT NULL', (key,)).fetchall()
        for spec, known in rows:
            if json.loads(known) == proof:
                return json.loads(spec)['update_date']
        return None

    def preview(self, author_id, target_type=None, *, novel_format='EPUB', ugoira_format='gif', new_only=False):
        self._open()
        identifier(author_id)
        if target_type is None:
            target_type=self.resolve_author_target(author_id)
        if target_type not in ('both', 'illust', 'novel') or novel_format not in ('TXT','EPUB','BOTH') or ugoira_format not in ('gif','mp4','frames'):
            raise ValueError('invalid author media selection')
        if type(new_only) is not bool:raise ValueError('invalid new-only selection')
        s = self.services
        s.live_client.verify_identity()
        self.reconcile()
        output = []
        bridge = getattr(s,'legacy_author_bridge',None)
        legacy_known = bridge.observe_author(author_id, ()).saved if new_only and bridge is not None else {}
        def known(row):
            wid = identifier(row['id'])
            date = row.get('update_date')
            if not date:return False
            if row.get('type') == 'novel':
                proof = self._proof('novel',wid,novel_format)
                return proof is not None and self._date(media_key('novel',wid),proof) == date
            if row.get('type') == 2:
                proof = self._proof('ugoira',wid,ugoira_format)
                return proof is not None and self._date(media_key('ugoira',wid),proof) == date
            current = s.managed_downloads.verified_saved_receipt(wid)
            old = legacy_known.get(wid)
            return ((current is not None and current[3] == date) or
                    (current is None and old is not None and not old.deleted and old.files_present is True and old.update_date == date))
        if target_type in ('both','illust'):
            works = (s.live_client.get_user_works(author_id,early_exit_checker=known) if new_only
                     else s.live_client.get_author_artworks_complete(author_id))
            bridge = getattr(s,'legacy_author_bridge',None)
            legacy = {}
            if bridge is not None:
                bridge.assert_source_unchanged()
                legacy = legacy_known if new_only else bridge.observe_author(author_id,works).saved
            for work in works:
                if work['user_id'] != author_id or type(work['type']) is not int or work['type'] not in (0,1,2):
                    raise ValueError('complete author identity changed')
                wid = identifier(work['id'])
                date = work.get('update_date') or ''
                kind = 'ugoira' if work['type'] == 2 else 'illustration'
                payload, prior, fmt = None, None, ''
                if kind == 'illustration':
                    current = s.managed_downloads.verified_saved_receipt(wid)
                    old = legacy.get(wid) if current is None else None
                    prior = None if current is None else current[2]
                    difference = decide_difference(saved=current is not None or old is not None,
                        remote_update_date=date, saved_update_date=current[3] if current else old.update_date if old else None,
                        deleted=old.deleted if old else False, files_present=True if current else old.files_present if old else None)
                    payload = dict(work, expected_relative_receipt=current[0] if current else None,
                                   expected_receipt_sha256=prior)
                elif getattr(s,'ugoira',None) is not None:
                    fmt = ugoira_format
                    payload = s.ugoira.preview(wid, author_id, fmt)
                    proof = self._proof(kind,wid,fmt)
                    prior = canonical(proof) if proof else None
                    difference = decide_difference(saved=proof is not None, remote_update_date=date,
                        saved_update_date=self._date(media_key(kind,wid),proof), files_present=True if proof else None)
                    if not date or payload.spec.get('update_date') != date:
                        difference = Difference.UNKNOWN
                else:
                    difference = Difference.UNKNOWN
                if not date:
                    difference = Difference.UNKNOWN
                output.append(AuthorMediaCandidate(kind,wid,author_id,date,work['page_count'],work.get('title') or wid,
                    difference,fmt,prior,payload,work['type']))
        if target_type in ('both','novel'):
            # Complete profile listing is required even when novel storage is
            # disabled: a missing owner is explicit UNKNOWN, not silent loss.
            ids = (tuple(row['id'] for row in s.live_client.get_user_novels(author_id,early_exit_checker=known))
                   if new_only else s.live_client.get_author_novel_ids(author_id))
            for wid in ids:
                identifier(wid)
                if getattr(s,'novel_jobs',None) is None:
                    output.append(AuthorMediaCandidate('novel',wid,author_id,'',1,wid,Difference.UNKNOWN,novel_format))
                    continue
                from .novel_save_presentation import NovelSavePresentation
                from .managed_novel_jobs import _canonical, _hash
                source = s.novel_fixture_source
                source.select(wid,author_id)
                body = source.fetch(wid)
                date = body.get('updateDate') or ''
                presentation = NovelSavePresentation(s)
                selection = presentation.preview(wid,novel_format)
                from .novel_save_presentation import _fingerprint
                from .media_parity_policy import parse_novel_detail
                if selection.fingerprint != _fingerprint(parse_novel_detail(body,work_id=wid,expected_author_id=author_id),body["userName"]):
                    raise ValueError("novel changed during preview")
                # The second detail fetched by preview must match the accepted
                # source fingerprint; enqueue rechecks it before owner CAS.
                selection = replace(selection, intent=replace(selection.intent,
                    expected_source_sha256=_hash(_canonical(body))))
                proof = self._proof('novel',wid,novel_format)
                difference = decide_difference(saved=proof is not None,remote_update_date=date,
                    saved_update_date=self._date(media_key('novel',wid),proof),files_present=True if proof else None)
                if type(date) is not str or not date:
                    difference = Difference.UNKNOWN
                output.append(AuthorMediaCandidate('novel',wid,author_id,date,1,selection.title,difference,novel_format,
                    canonical(proof) if proof else None,selection))
        return tuple(output)

    def _refresh_candidate(self, candidate):
        """Revalidate one frozen item, never repeat a full author crawl per item."""
        s=self.services
        kind,wid,aid=candidate.kind,candidate.work_id,candidate.author_id
        if kind=='illustration':
            revision=s.live_client.get_artwork_update_revision(wid,aid)
            if (revision['work_id']!=wid or revision['author_id']!=aid
                    or revision['illustration_type']!=candidate.work_type
                    or revision['page_count']!=candidate.page_count):
                raise ValueError('author revision identity changed')
            current=s.managed_downloads.verified_saved_receipt(wid)
            return replace(candidate,update_date=revision['update_date'],
                prior_token=current[2] if current else None,
                payload=dict(candidate.payload,expected_relative_receipt=current[0] if current else None))
        proof=self._proof(kind,wid,candidate.output_format)
        prior=canonical(proof) if proof else None
        if kind=='ugoira':
            selection=s.ugoira.preview(wid,aid,candidate.output_format)
            return replace(candidate,payload=selection,prior_token=prior,
                           update_date=selection.spec.get('update_date') or '')
        from .novel_save_presentation import NovelSavePresentation, _fingerprint
        from .managed_novel_jobs import _canonical, _hash
        from .media_parity_policy import parse_novel_detail
        s.novel_fixture_source.select(wid,aid)
        body=s.novel_fixture_source.fetch(wid)
        selection=NovelSavePresentation(s).preview(wid,candidate.output_format)
        if selection.fingerprint!=_fingerprint(parse_novel_detail(body,work_id=wid,expected_author_id=aid),body['userName']):
            raise ValueError('novel changed during admission')
        selection=replace(selection,intent=replace(selection.intent,expected_source_sha256=_hash(_canonical(body))))
        return replace(candidate,payload=selection,prior_token=prior,update_date=body.get('updateDate') or '')

    def enqueue(self, candidate, operation_id, *, origin='follow'):
        from .managed_download_requests import ManagedAuthorIntent, accepted_author_fingerprint
        if type(candidate) is not AuthorMediaCandidate or not candidate.eligible or origin not in ('id','follow'):
            raise ValueError('unaccepted author media candidate')
        identifier(candidate.author_id)
        if type(operation_id) is not str or re.fullmatch(r'[a-f0-9]{32}',operation_id) is None:
            raise ValueError('invalid author operation')
        spec = canonical(candidate.admission())
        with self.lock:
            self._open()
            with self._db() as db:
                row = db.execute('SELECT spec,job_id,fingerprint FROM admissions WHERE operation_id=?',(operation_id,)).fetchone()
            if row is not None and row[0] != spec:
                raise ValueError('author operation replay differs')
            jobs = self._owner_jobs(candidate.kind)
            existing = next((j for j in jobs.snapshot() if operation_id in jobs.operation_ids(j.job_id)),None)
            if existing is not None:
                fingerprint = self._bind(candidate,operation_id,existing)
                return existing,False,fingerprint
            # This check shares the same RLock as configure_author_target/zip,
            # so no other client can change the author setting between the
            # fence and the new admission. Existing operation replays above
            # remain legal even when the author's settings have since changed.
            if candidate.author_setting_revision is not None:
                if (type(candidate.author_setting_revision) is not int
                        or candidate.author_setting_revision < 0
                        or candidate.author_setting_target not in (None,'both','illust','novel')):
                    raise ValueError('invalid author setting fence')
                current_setting = self.author_target(candidate.author_id)
                if (current_setting.revision != candidate.author_setting_revision
                        or current_setting.target_type != candidate.author_setting_target):
                    raise ValueError('author settings changed; preview again')
            if sum(j.state in UNFINISHED for j in self.services.jobs.snapshot()) >= 50:
                raise RuntimeError('managed aggregate queue capacity reached')
            # Source proof and predecessor are revalidated. Never silently
            # rebase a frozen UI selection onto another client's generation.
            match = self._refresh_candidate(candidate)
            if match is None or not match.eligible or canonical(match.admission()) != spec:
                raise ValueError('author selection revision changed')
            with self._db() as db:
                db.execute('INSERT OR IGNORE INTO admissions(operation_id,media_key,spec) VALUES(?,?,?)',
                           (operation_id,candidate.key,spec))
            s=self.services
            before={j.job_id for j in jobs.snapshot()}
            if candidate.kind=='illustration':
                owner=uuid4().hex
                work=match.payload
                with s.managed_downloads.work_lock(candidate.work_id):
                    current=s.managed_downloads.verified_saved_receipt(candidate.work_id)
                    if (current[2] if current else None)!=candidate.prior_token:
                        raise ValueError('author receipt changed')
                    record=s.live_client.get_artwork_download_record(candidate.work_id)
                    if (record.author_id!=candidate.author_id or record.illustration_type!=candidate.work_type
                            or record.page_count!=candidate.page_count or not record.page_count_known):
                        raise ValueError('author detail changed')
                    try:
                        (record,)=s.remember_managed_author_batch(owner,(record,))
                        intent=ManagedAuthorIntent(record,origin,candidate.update_date,candidate.prior_token,
                            'updated' if candidate.prior_token else 'new',work['expected_relative_receipt'])
                        job,_=s.enqueue_managed_download(intent,operation_id,owner=owner,return_created=True)
                    finally:
                        s.release_managed_owner(owner)
            elif candidate.kind=='novel':
                # Source pin is checked by the worker as well; an update after
                # this point cannot publish under the old admitted revision.
                job=s.enqueue_novel_export(match.payload.intent,operation_id)
            else:
                job=s.ugoira.enqueue_selection(match.payload,operation_id)
            return job,job.job_id not in before,self._bind(candidate,operation_id,job)

    def _bind(self,candidate,operation,job):
        with self._db() as db:
            row=db.execute('SELECT spec FROM admissions WHERE operation_id=?',(operation,)).fetchone()
            if row is None:
                raise ValueError('missing author admission')
            fingerprint=sha256((job.job_id+'\0'+row[0]).encode()).hexdigest()
            db.execute('UPDATE admissions SET job_id=?,fingerprint=? WHERE operation_id=?',
                       (job.job_id,fingerprint,operation))
        return fingerprint

    @staticmethod
    def _spec_pairs(pairs):
        value={}
        for name,item in pairs:
            if name in value:raise ValueError('duplicate author spec key')
            value[name]=item
        return value

    @staticmethod
    def _projection_spec(raw,key):
        spec=json.loads(raw,object_pairs_hook=AuthorMediaService._spec_pairs)
        kind,wid=split_media_key(key)
        if (type(spec) is not dict or spec.get('kind')!=kind or spec.get('work_id')!=wid
                or type(spec.get('output_format')) is not str):raise ValueError('invalid author projection spec')
        identifier(spec.get('author_id'))
        return spec,kind,wid

    def _reconcile_error(self,op,aid,phase,error):
        safe_op=(op if type(op) is str and re.fullmatch(r'[a-f0-9]{32}',op)
                 else 'invalid-operation-'+sha256(str(op).encode('utf-8',errors='replace')).hexdigest()[:16])
        value=dict(author_id=aid,phase=phase,error_type=type(error).__name__)
        if self.reconcile_errors.get(safe_op)!=value:
            import logging
            logging.getLogger(__name__).warning('author reconciliation unavailable %s: %s/%s',safe_op,phase,value['error_type'])
        self.reconcile_errors[safe_op]=value

    def projection_unresolved_authors(self,author_id=None):
        values=tuple(self.reconcile_errors.copy().values())
        known={v['author_id'] for v in values if v['author_id'] is not None
               and (author_id is None or v['author_id']==author_id)}
        return known,any(v['author_id'] is None for v in values)

    def reconcile_status_messages(self):
        reasons={'invalid_spec':'受付情報の形式または対象が一致しません',
                 'binding':'保存された受付結合が一致しません',
                 'source_verification':'受付に対応する保存元を確認できません',
                 'journal_write':'受付結合の保存に失敗しました'}
        return tuple('作者受付 '+op+'：'+reasons.get(v['phase'],reasons['invalid_spec'])
                     for op,v in self.reconcile_errors.copy().items())

    def reconcile(self, expected=None, *, author_id=None):
        """Return (verified fingerprint by media key, op->job, op->fingerprint).

        No GET. Recover enqueue/bind crashes from owner operation history.
        Job done alone never proves an active generation or accepted revision.
        """
        with self.lock:
            self._open()
            proofs,operations,fingerprints={},{},{}
            with self._db() as db:
                rows=db.execute('SELECT operation_id,media_key,spec,job_id,fingerprint,proof FROM admissions').fetchall()
            for op,key,raw,jid,fp,known in rows:
                aid=None;phase='invalid_spec'
                try:
                    # A parseable author hint scopes a malformed row; unknown
                    # ownership is retained as unknown, never inferred absent.
                    try:
                        hint=json.loads(raw,object_pairs_hook=self._spec_pairs)
                        if type(hint) is dict:aid=identifier(hint.get('author_id'))
                    except (ValueError,TypeError,RecursionError):pass
                    if jid is not None and fp is not None:
                        phase='binding'
                        if (type(jid) is not str or re.fullmatch(r'[a-f0-9]{32}',jid) is None
                                or type(raw) is not str or fp!=sha256((jid+'\0'+raw).encode()).hexdigest()):
                            aid=None  # changed raw metadata cannot assert its former author
                            raise ValueError('author admission binding changed')
                    phase='invalid_spec'
                    spec,kind,wid=self._projection_spec(raw,key)
                    aid=spec['author_id']
                    if author_id is not None and aid!=author_id:
                        old=self.reconcile_errors.get(op)
                        if old is not None and old['phase']=='invalid_spec':self.reconcile_errors.pop(op,None)
                        continue
                    phase='binding'
                    jobs=self._owner_jobs(kind)
                    job=next((j for j in jobs.snapshot() if op in jobs.operation_ids(j.job_id)),None)
                    if job is None:
                        self.reconcile_errors.pop(op,None)
                        continue
                    expected_fp=sha256((job.job_id+'\0'+raw).encode()).hexdigest()
                    if (jid is not None and jid!=job.job_id) or (fp is not None and fp!=expected_fp):
                        aid=None
                        raise ValueError('author admission binding changed')
                    phase='source_verification'
                    proof=self._proof(kind,wid,spec['output_format']) if job.state=='done' else None
                    valid=proof is not None and proof['receipt']==job.result
                    if kind=='ugoira':valid=valid and proof['job_id']==job.job_id
                    phase='journal_write'
                    with self._db() as db:
                        db.execute('UPDATE admissions SET job_id=?,fingerprint=?,proof=COALESCE(?,proof) WHERE operation_id=?',
                                   (job.job_id,expected_fp,canonical(proof) if valid else None,op))
                    # Publish reconciliation evidence only after the owned
                    # journal commit, preserving the durable-bind boundary.
                    operations[op]=job.job_id;fingerprints[op]=expected_fp
                    if valid and (expected is None or expected_fp in expected.get(key,())):proofs[key]=expected_fp
                    self.reconcile_errors.pop(op,None)
                except Exception as error:self._reconcile_error(op,aid,phase,error)
            return proofs,operations,fingerprints

    def completed_projection_sources(self):
        """Only durable admissions with current verified receipts."""
        return self._completed_projection_sources()

    def completed_projection_sources_for_author(self, author_id):
        return self._completed_projection_sources(identifier(author_id))

    def _completed_projection_sources(self, author_id=None):
        result=[]
        with self.lock:
            proofs,operations,_=self.reconcile(author_id=author_id)
            with self._db() as db:
                rows=db.execute('SELECT operation_id,media_key,spec,fingerprint,proof FROM admissions').fetchall()
            for op,key,raw,fp,known in rows:
                if op not in operations or not fp or not known or proofs.get(key)!=fp:continue
                aid=None
                try:
                    spec,kind,wid=self._projection_spec(raw,key);aid=spec['author_id']
                    if author_id is not None and aid!=author_id:continue
                    result.append(dict(kind=kind,work_id=wid,author_id=aid,
                                       format=spec['output_format'],proof=json.loads(known)))
                except Exception as error:self._reconcile_error(op,aid,'invalid_spec',error)
        return tuple(result)

    def close(self):
        with self.lock:
            self.closed=True
