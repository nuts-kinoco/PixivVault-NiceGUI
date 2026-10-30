"""Non-destructive post-save exports using existing jobs, receipts and ZIP exporter."""
from pathlib import Path
from .fs_paths import native_io_path,record_io_failure
from threading import Event,Thread,RLock,local
from hashlib import sha256
import json,os,stat,logging,re
from contextlib import contextmanager,ExitStack
from dataclasses import dataclass
from .legacy_zip_export import LegacyZipExporter,legacy_filename,_INVALID_FILENAME
from .managed_downloads import _safe_path,_write as _raw_write,_read_bounded as _raw_read,_json_unique,_fsync_dir
from .job_service import JobService,UNFINISHED


def _write(path,data):return _raw_write(native_io_path(path),data)
def _read_bounded(path,limit):return _raw_read(native_io_path(path),limit)


def _publish_owned(path,data):
    from .bookmark_projection import publish_bytes
    def verify(existing):
        if _read_bounded(existing,len(data)+1)!=data:raise ValueError('owned export bytes changed')
    publish_bytes(path,data,verify)


def packet(value):return json.dumps(value,sort_keys=True,separators=(',',':')).encode()
def digest(value):return sha256(packet(value)).hexdigest()


@dataclass(frozen=True)
class RetainedExport:
    job_id: str
    kind: str
    target_id: str
    title: str
    state: str = 'retained'  # not a JobSnapshot and not a completion claim


@dataclass(frozen=True)
class _DependencyBatch:
    owner: object
    pending: object


class SaveExports:
    def __init__(self, services, root, account_id=None):
        self.s=services;self.root=Path(root);self.account=account_id
        if account_id is not None and (not isinstance(account_id,str) or not account_id.isdecimal() or int(account_id)<1):
            raise ValueError('invalid bookmark account')
        self._account_lock=RLock()
        # Reuse the existing ownership and no-overwrite export contract.
        self.owner=LegacyZipExporter(self.root,services.managed_downloads)
        self.zip=LegacyZipExporter(self.root/'archives',services.managed_downloads,preserve_incomplete=True)
        self.projection=LegacyZipExporter(self.root/'projection',services.managed_downloads)
        self._dependency_local=local()
        self._log_salt=os.urandom(32)
        self.stop=Event();self.thread=None;self.lock=RLock();self.last_error='';self.scan_errors={};self._reported_errors={};self.scan_warnings={};self._reported_warnings={}
        self._admissions={};self._seen_operations=set();self._unresolved={};self._operation_count=-1
        self._recovered={};self._retired_offers={};self._legacy_publications=set()
        self._accepted_inputs={};self._projection_families={};self._author_publications={};self._work_publications={};self._static_records={}
        self.jobs=JobService(self.run,self.root/'jobs.sqlite3')
        for j in self.jobs.snapshot():
            if j.state=='stopped' and j.error_code=='interrupted':self.jobs.reconcile_interrupted(j.job_id)
        self._refresh_admissions(None)
        self._refresh_retained_offers()

    @contextmanager
    def verified_account_binding(self,account_id):
        """Admit one complete read and bind after its publication succeeds.

        The caller owns identity verification. This scope never performs GET,
        reads Cookie bytes, guesses from a path, or rewrites accepted intents.
        Its entry serializes the one-way admission with owner shutdown. Once
        admitted, a later close waits for this publication; it does not turn an
        already published snapshot into an error with an unbound account.
        """
        with self._account_lock:
            self._check_account_binding(account_id)
            yield
            self.account=account_id

    def _check_account_binding(self,account_id):
        if (type(account_id) is not str or
                re.fullmatch(r'[1-9][0-9]{0,19}',account_id) is None):
            raise ValueError('invalid verified bookmark account')
        if self.stop.is_set() or getattr(self.s,'closing',False):
            raise RuntimeError('bookmark exports are closing')
        if self.account not in (None,account_id):
            raise ValueError('bookmark export account differs')

    def bind_verified_account(self,account_id):
        """Idempotent same-ID binding; a different account requires a new owner."""
        with self.verified_account_binding(account_id):
            pass
        return self.account

    def author_snapshot(self,aid):
        rows,pages,_=self._author_page_snapshot(aid)
        return [row for row in rows if pages[row[1]['work_id']]]

    def _author_page_snapshot(self,aid):
        snapshot=self.s.managed_downloads.catalog_snapshot()
        rows=sorted([(rel,row,sha) for rel,row,sha in snapshot.receipts
                     if row['author_id']==aid],key=lambda r:r[1]['work_id'])
        pages={row['work_id']:snapshot.source_pages[row['work_id']] for _,row,_ in rows}
        tokens={wid:snapshot.work_tokens[wid] for wid in pages}
        return rows,pages,tokens

    @staticmethod
    def _same_page_map(value,actual):
        return (type(value) is dict and set(value)==set(actual)
                and all(type(pages) in (list,tuple) and all(type(page) is int for page in pages)
                        and tuple(pages)==actual[wid] for wid,pages in value.items()))

    def _static_page_state(self,identity,*,validate_filter=True):
        owner=self.s.managed_downloads;wid=identity['work_id']
        current=owner.verified_saved_receipt(wid)
        if (current is None or (current[0],current[2])!=(identity['relative'],identity['sha256'])
                or identity.get('author_id',current[1]['author_id'])!=current[1]['author_id']):
            raise ValueError('saved generation changed')
        state=owner._generations.state_snapshot();entry=state['entries'].get(wid)
        if entry is None or (entry['relative_receipt'],entry['sha256'])!=(current[0],current[2]):
            raise ValueError('saved generation changed')
        pages=tuple(page for page in range(current[1]['page_count'])
                    if str(page) not in state['exclusions'].get(wid,{}))
        token=owner._generations._work_token(state,wid)
        if validate_filter:
            expected=identity.get('source_pages',list(range(current[1]['page_count'])))
            if (type(expected) not in (list,tuple) or any(type(page) is not int for page in expected)
                    or tuple(expected)!=pages
                    or ('source_pages' in identity)!=('source_state' in identity)
                    or 'source_state' in identity and identity['source_state']!=token):
                raise ValueError('saved page selection changed')
        if not pages:raise ValueError('managed work is quarantined')
        return current,pages,token

    def _auto_page_state(self,identity,*,validate_filter=True):
        rows,pages,tokens=self._author_page_snapshot(identity['author_id'])
        visible=[row for row in rows if pages[row[1]['work_id']]]
        if (not visible or json.loads(packet(identity['proofs']))
                !=[[row['work_id'],proof] for _,row,proof in visible]):
            raise ValueError('saved generation changed')
        if validate_filter:
            expected=identity.get('page_filters',{
                row['work_id']:list(range(row['page_count'])) for _,row,_ in rows})
            if (not self._same_page_map(expected,pages)
                    or ('page_filters' in identity)!=('page_filter_tokens' in identity)
                    or 'page_filter_tokens' in identity and identity['page_filter_tokens']!=tokens):
                raise ValueError('saved page selection changed')
        return rows,pages,tokens

    def _pin_page_selection(self,kind,identity):
        identity=dict(identity)
        if identity.get('media') in ('novel','ugoira'):return identity
        if kind in ('author-projection','bookmark-projection'):
            with self.s.managed_downloads.work_lock(identity['work_id']):
                current,pages,token=self._static_page_state(identity,
                    validate_filter='source_pages' in identity or 'source_state' in identity)
                if len(pages)!=current[1]['page_count']:
                    identity.update(source_pages=list(pages),source_state=token)
        elif kind=='auto-zip':
            rows,pages,tokens=self._auto_page_state(identity,
                validate_filter='page_filters' in identity or 'page_filter_tokens' in identity)
            if any(len(pages[row['work_id']])!=row['page_count'] for _,row,_ in rows):
                identity.update(page_filters={wid:list(value) for wid,value in pages.items()},
                                page_filter_tokens=tokens)
        return identity

    @contextmanager
    def _zip_publication_scope(self,identity):
        _,pages,tokens=self._auto_page_state(identity)
        with ExitStack() as locks:
            for wid in sorted(pages):locks.enter_context(self.s.managed_downloads.work_lock(wid))
            _,current_pages,current_tokens=self._auto_page_state(identity)
            if current_pages!=pages or current_tokens!=tokens:
                raise ValueError('saved page selection changed')
            yield

    def _scan_error(self,key,error):
        reasons={
            'legacy ZIP author layout requires accepted update policy':'旧ZIP作者のため派生配置を保留',
            'unsafe legacy author storage':'旧作者配置の安全性を確認できません',
            'author projection source changed':'作者保存世代が変更されました',
            'author output not settled':'作者出力が未確定のためブックマーク配置を保留',
            'legacy export acceptance unresolved':'旧受付intentを確認できないため再受付を保留',
            'retained export completion unavailable':'退避済み受付の完了証跡を確認できないため保留',
            'projection update requires accepted preservation policy':'既存の投影を保全しています。同一作品の世代更新は保存互換方針の確認が必要です',
            'retained export requires explicit retry':'退避済み失敗履歴です。失敗リストから明示的に再試行できます',
        }
        typed={'PermissionError':'読取りアクセスを確認できません（PermissionError）',
               'FileNotFoundError':'必要な保存証跡が見つかりません',
               'JSONDecodeError':'既存受付またはmanifestを解釈できません',
               'ValueError':'保存・出力の整合性を確認できません'}
        reason=reasons.get(str(error),typed.get(type(error).__name__,'処理を確認できません（'+type(error).__name__+'）'))
        self.scan_errors[key]=reason
        if hasattr(self,'_cycle_errors'):self._cycle_errors[key]=reason
        self.last_error='post_save_item_failed'
        if self._reported_errors.get(key)!=reason:
            logging.getLogger(__name__).warning('保存後処理を保留 export item %s: %s',
                sha256(self._log_salt+str(key).encode('utf-8')).hexdigest()[:12],
                'known-condition' if str(error) in reasons else type(error).__name__ if type(error).__name__ in typed else 'other-error')
            self._reported_errors[key]=reason

    def _storage_warning(self,aid,message):
        self.scan_warnings[aid]=message
        if hasattr(self,'_cycle_warnings'):self._cycle_warnings[aid]=message
        if self._reported_warnings.get(aid)!=message:
            logging.getLogger(__name__).warning('author layout %s: 複数の候補から一番大きいものを使います',
                sha256(self._log_salt+str(aid).encode('utf-8')).hexdigest()[:12])
            self._reported_warnings[aid]=message

    def status_messages(self):
        return tuple(key+'：'+reason for key,reason in self.scan_errors.copy().items())+tuple('作者'+key+'：'+reason for key,reason in self.scan_warnings.copy().items())

    @contextmanager
    def _item(self,key):
        try:yield
        except Exception as error:self._scan_error(key,error)

    @staticmethod
    def _logical(identity):
        if not isinstance(identity,dict):raise ValueError('export intent identity rejected')
        value={k:v for k,v in identity.items() if k!='author_folder'}
        if 'media' not in value and value.get('output_scope')!='author':
            value.setdefault('use_work_folder',value.get('mode')=='link')
        return json.loads(packet(value))  # canonical persisted JSON, including tuple/list proofs

    def _refresh_admissions(self,kind):
        count=self.jobs.operation_count
        if count==self._operation_count and not self._unresolved:return
        for op,jid,job_kind,target,title in self.jobs.accepted_operations():
            if job_kind not in ('bookmark-projection','author-projection','auto-zip') or op in self._seen_operations:continue
            if type(target) is not str or re.fullmatch(r'[a-f0-9]{64}',target) is None:
                # Refuse before any fallback path is composed, including the
                # legacy manifest lookup after _read_intent's own rejection.
                self._scan_error('intent:'+jid,ValueError('export intent identity rejected'))
                self._unresolved[op]=job_kind
                continue
            descriptor=RetainedExport(jid,job_kind,target,title)
            saved=None
            if op.startswith('export-recover:'):
                parent=op[len('export-recover:'):]
                if re.fullmatch(r'[a-f0-9]{32}',parent) is None:raise ValueError('export recovery identity rejected')
                self._recovered[parent]=descriptor
            # New operations encode the logical request independently of pin.
            prefix=job_kind+':request:'
            if op.startswith(prefix):
                key=op[len(prefix):]
                if re.fullmatch(r'[a-f0-9]{64}',key) is None:raise ValueError('export logical identity rejected')
            else:
                try:
                    saved=self._read_intent(descriptor)
                    key=digest(self._logical(saved))
                except Exception as error:
                    self._scan_error('intent:'+jid,error)
                    try:
                        published=json.loads(_read_bounded(self.root/(target+'.export.json'),2_000_000),object_pairs_hook=_json_unique)
                        saved=published['source']
                        if type(saved) is not dict or digest(saved)!=target:raise ValueError('unbound legacy completion')
                        key=digest(self._logical(saved))
                    except Exception:
                        self._unresolved[op]=job_kind
                        continue
                    # Identify the affected logical request from independently
                    # bound publication metadata. Its input is still unreadable:
                    # _accepted_projection will fail closed for this key only.
            self._admissions.setdefault((job_kind,key),descriptor)
            if saved is None:
                try:
                    saved=self._read_intent(descriptor)
                    if digest(self._logical(saved))!=key:raise ValueError('export logical identity changed')
                except Exception as error:
                    self._scan_error('intent:'+jid,error);saved=None
            if saved is not None:self._index_input(descriptor,saved)
            self._seen_operations.add(op);self._unresolved.pop(op,None)
        self._operation_count=count

    @staticmethod
    def _projection_family(kind,identity):
        return (kind,digest({k:v for k,v in SaveExports._logical(identity).items()
            if k not in ('relative','sha256','accepted_proof','source_pages','source_state')}))

    def _index_input(self,entry,identity):
        self._accepted_inputs[entry.job_id]=(entry,identity)
        if entry.kind=='author-projection':self._author_publications.setdefault(identity.get('author_id'),{})[entry.job_id]=(entry,identity)
        if entry.kind in ('author-projection','bookmark-projection'):
            work=(identity.get('media','illustration'),identity.get('work_id'),identity.get('format','original'))
            self._work_publications.setdefault(work,{})[entry.job_id]=(entry,identity)
            key=self._projection_family(entry.kind,identity)
            bucket=self._projection_families.setdefault(key,{})
            bucket[entry.job_id]=(entry,identity)

    def _previous_publication(self,kind,identity):
        family=self._projection_family(kind,identity)
        for entry,prior in tuple(self._projection_families.get(family,{}).values()):
            if self._logical(prior)!=self._logical(identity) and self._publication_completed(entry,prior):return True
        if kind=='bookmark-projection':
            work=(identity.get('media','illustration'),identity.get('work_id'),identity.get('format','original'))
            for entry,prior in tuple(self._work_publications.get(work,{}).values()):
                if (entry.kind=='author-projection' and prior.get('sha256')!=identity.get('sha256')
                        and self._publication_completed(entry,prior)):return True
        return False

    def _author_established(self,aid):
        # Historical publication establishes classification, not the current
        # generation's mutable bytes or completion. No payload hashes here.
        for entry,identity in tuple(self._author_publications.get(aid,{}).values()):
            if self._publication_completed(entry,identity):return True
        return False

    def _refresh_retained_offers(self):
        visible={j.job_id for j in self.jobs.snapshot()}
        for original in tuple(self._admissions.values()):
            entry=original;seen=set()
            while entry.job_id in self._recovered:
                if entry.job_id in seen:raise ValueError('cyclic export recovery')
                seen.add(entry.job_id);entry=self._recovered[entry.job_id]
            if entry.job_id in visible:continue
            history=self.jobs.retained_job(entry.job_id)
            if history is not None and history.state=='done':continue
            try:completed=self._publication_completed(entry)
            except Exception as error:
                self._scan_error('intent:'+entry.job_id,error);completed=False
            if not completed:self._retired_offers[entry.job_id]=entry

    def _read_intent(self,entry):
        if re.fullmatch(r'[a-f0-9]{64}',entry.target_id) is None:raise ValueError('export intent identity rejected')
        saved=json.loads(_read_bounded(self.root/(entry.target_id+'.json'),2_000_000),object_pairs_hook=_json_unique)
        if digest(saved)!=entry.target_id:raise ValueError('export intent changed')
        return saved

    def _accepted_projection(self,kind,identity):
        self._refresh_admissions(kind)
        logical=self._logical(identity);key=digest(logical)
        # An unreadable legacy record has no safely known logical key. Do not
        # guess it absent or allow a newer record to supersede its first pin.
        entry=self._admissions.get((kind,key))
        if entry is None:
            if kind in self._unresolved.values():raise ValueError('legacy export acceptance unresolved')
            return None
        visited=set()
        while entry.job_id in self._recovered:
            if entry.job_id in visited:raise ValueError('cyclic export recovery')
            visited.add(entry.job_id);entry=self._recovered[entry.job_id]
        saved=self._read_intent(entry)  # one targeted revalidation, not 50 reads
        if self._logical(saved)!=logical:raise ValueError('export logical identity changed')
        self._index_input(entry,saved)
        live=next((j for j in self.jobs.snapshot() if j.job_id==entry.job_id),None)
        if live is not None:return live
        if not self._publication_completed(entry,saved):
            self._retired_offers[entry.job_id]=entry
            self._scan_error('retained:'+entry.job_id,ValueError('retained export requires explicit retry'))
        else:self._retired_offers.pop(entry.job_id,None)
        return entry

    @staticmethod
    def _publication_metadata(entry,manifest,identity):
        if type(manifest) is not dict or manifest.get('source')!=identity:
            raise ValueError('author manifest changed')
        if entry.kind=='auto-zip':
            name=manifest.get('archive')
            if (set(manifest)!={'source','archive','sha256'} or type(name) is not str
                    or not name.lower().endswith('.zip') or '/' in name or chr(92) in name or ':' in name
                    or type(manifest.get('sha256')) is not str or re.fullmatch(r'[a-f0-9]{64}',manifest['sha256']) is None):
                raise ValueError('invalid completion metadata')
            return
        if entry.kind not in ('author-projection','bookmark-projection') or set(manifest)!={'source','files'}:
            raise ValueError('invalid completion metadata')
        files=manifest['files']
        if type(files) is not list or not files:raise ValueError('invalid completion metadata')
        source_fields=(identity.get('media') in ('novel','ugoira') and identity.get('reference_layout')==1)
        fields={'path','sha256','bytes'}|({'source_sha256','source_bytes'} if source_fields else set())
        for row in files:
            if type(row) is not dict or set(row)!=fields:raise ValueError('invalid completion metadata')
            name=row['path']
            if (type(name) is not str or not name or name.startswith(('/',chr(92))) or ':' in name
                    or any(p in ('','.','..') for p in name.replace(chr(92),'/').split('/'))
                    or type(row['sha256']) is not str or re.fullmatch(r'[a-f0-9]{64}',row['sha256']) is None
                    or type(row['bytes']) is not int or row['bytes']<0):raise ValueError('invalid completion metadata')
            if source_fields and (type(row['source_sha256']) is not str or re.fullmatch(r'[a-f0-9]{64}',row['source_sha256']) is None
                                  or type(row['source_bytes']) is not int or row['source_bytes']<0):
                raise ValueError('invalid completion metadata')

    def _publication_completed(self,entry,identity=None):
        history=self.jobs.retained_job(entry.job_id)
        if history is not None:
            # Durable done proves publication, not current editable bytes.
            return (history.state=='done' and history.kind==entry.kind and history.target_id==entry.target_id
                    and history.result==entry.target_id+'.export.json')
        if entry.job_id in self._legacy_publications:return True
        # A v1 job may already have left the window before the v2 migration.
        # Its immutable manifest proves publication once; do not hash mutable
        # projection data or manufacture an attempt/state for the old job.
        identity=self._read_intent(entry) if identity is None else identity
        try:manifest=json.loads(_read_bounded(self.root/(entry.target_id+'.export.json'),2_000_000),object_pairs_hook=_json_unique)
        except FileNotFoundError:return False
        self._publication_metadata(entry,manifest,identity)
        self._legacy_publications.add(entry.job_id)
        return True

    def retained_requests(self):
        visible={j.job_id for j in self.jobs.snapshot()}
        return tuple(entry for jid,entry in self._retired_offers.copy().items()
                     if jid not in visible and jid not in self._legacy_publications)

    def retry_retained(self,job_id):
        """Explicit UI Retry only. Never called by scan/reconciliation."""
        with self.lock:
            entry=self._retired_offers.get(job_id)
            if entry is None:return False
            self._read_intent(entry)
            restored=self.jobs.restore_retained(job_id)
            if restored is not None:
                if restored.state=='done':return False
                changed=self.jobs.control(job_id,'retry',expected_control_revision=restored.control_revision)
            else:
                # Pre-v2 retirement has no reliable attempt metadata. Keep its
                # tombstone; an explicit new recovery operation names it rather
                # than guessing the old attempt or implicitly replaying it.
                recovered=self.jobs.enqueue(entry.kind,entry.target_id,entry.title,
                    operation_id='export-recover:'+job_id)
                self._recovered[job_id]=RetainedExport(recovered.job_id,entry.kind,entry.target_id,entry.title)
                changed=True
            if changed:self._retired_offers.pop(job_id,None)
            return changed


    @contextmanager
    def _dependency_scope(self,pending):
        previous=getattr(self._dependency_local,'batch',None)
        batch=_DependencyBatch(self,None if pending is None else frozenset(pending))
        self._dependency_local.batch=batch
        try:yield batch
        finally:self._dependency_local.batch=previous

    def _valid_dependencies(self,value):
        return (isinstance(value,_DependencyBatch) and value.owner is self
                and getattr(self._dependency_local,'batch',None) is value)

    def enqueue(self,kind,identity,*,dependencies=...):
        identity=self._pin_page_selection(kind,identity)
        with self.lock:
            if kind in ('bookmark-projection','author-projection','auto-zip'):
                accepted=self._accepted_projection(kind,identity)
                if accepted is not None:return accepted
        if kind=='bookmark-projection':
            aid=identity.get('author_id')
            if aid is None:
                saved=self.s.managed_downloads.verified_saved_receipt(identity['work_id'])
                if saved is None:raise ValueError('saved source unavailable')
                aid=saved[1]['author_id']
            dependencies=dependencies.pending if self._valid_dependencies(dependencies) else self._author_dependencies(aid)
            if dependencies is None or aid in dependencies:
                raise ValueError('author output not settled')
        with self.lock:
            if kind in ('bookmark-projection','author-projection','auto-zip'):
                accepted=self._accepted_projection(kind,identity)
                if accepted is not None:return accepted
            if kind in ('author-projection','bookmark-projection') and self._previous_publication(kind,identity):
                raise ValueError('projection update requires accepted preservation policy')
            # Pin physical membership once, before the durable operation identity.
            if kind in ('bookmark-projection','author-projection') and 'author_folder' not in identity:
                from .bookmark_projection import author_folder,legacy_membership
                aid=identity.get('author_id')
                if aid is None:
                    current=self.s.managed_downloads.verified_saved_receipt(identity['work_id'])
                    if current is None:raise ValueError('saved source unavailable')
                    aid=current[1]['author_id']
                warning=lambda message:self._storage_warning(aid,message)
                legacy=legacy_membership(getattr(self.s,'library_image_root',None),aid,warning)
                folder=author_folder(self.projection.root,aid,warning)
                if folder is None:folder=legacy
                if kind=='author-projection' and folder is None:
                    from .legacy_zip_export import _INVALID_FILENAME
                    folder=_INVALID_FILENAME.sub('_',identity['author_name'])+'('+aid+')'
                identity=dict(identity,author_folder=folder)
            key=digest(identity);path=native_io_path(self.root/(key+'.json'))
            with self.lock:
                if path.exists():
                    if _read_bounded(path,2_000_000)!=packet(identity):raise ValueError('export intent changed')
                else:_publish_owned(path,packet(identity))
                op=(kind+':request:'+digest(self._logical(identity))) if kind in ('bookmark-projection','author-projection','auto-zip') else kind+':'+key
                job=self.jobs.enqueue(kind,key,title='自動ZIP' if kind=='auto-zip' else ('作者互換配置' if kind=='author-projection' else 'ブックマーク互換配置'),operation_id=op)
                if kind in ('bookmark-projection','author-projection','auto-zip'):
                    entry=RetainedExport(job.job_id,kind,key,job.title)
                    self._admissions[(kind,digest(self._logical(identity)))]=entry
                    self._index_input(entry,identity)
                return job

    def scan(self):
        self._cycle_errors={};self._cycle_warnings={}
        try:return self._scan()
        except Exception as error:
            self._scan_error('scan',error)
            raise
        finally:
            self.scan_errors=self._cycle_errors;self.scan_warnings=self._cycle_warnings
            self._reported_warnings={k:v for k,v in self._reported_warnings.items() if k in self.scan_warnings}
            self.last_error='post_save_item_failed' if self.scan_errors else ''
            self._reported_errors={k:v for k,v in self._reported_errors.items() if k in self.scan_errors}

    def _scan(self):
        # One verified synchronous batch, never a caller-supplied bypass flag.
        dependencies=self._author_dependencies()
        with self._dependency_scope(dependencies) as batch:return self._scan_batch(batch)

    def _scan_batch(self,batch):
        account=self.account
        dependencies=batch.pending
        # Batch the author's currently accepted static downloads before ZIP.
        jobs=self.s.static_jobs.snapshot()
        zip_ready=not any(j.state in UNFINISHED for j in jobs)
        source_snapshot=self.s.managed_downloads.catalog_snapshot()
        receipts=source_snapshot.receipts
        completed=self._static_completed(jobs,{row['work_id']:rel for rel,row,sha in receipts})
        done=set(completed)
        for rel,row,sha in receipts:
            with self._item('static:'+row['work_id']):
                if not source_snapshot.source_pages[row['work_id']]:continue
                if row['work_id'] not in done:continue
                matching=[(job,record) for job,record in completed[row['work_id']] if not hasattr(job,'result') or job.result==rel]
                if not matching:continue
                setting=self.s.author_media.author_zip_setting(row['author_id'])
                enabled=(setting if setting is not None else self.s.preferences.get_setting('zip_all_after_download','0')=='1')
                if enabled and zip_ready:
                    proofs=[(r[1]['work_id'],r[2]) for r in self.author_snapshot(row['author_id'])]
                    self.enqueue('auto-zip',dict(author_id=row['author_id'],proofs=proofs))
                accepted=[record for job,record in matching]
                from .bookmark_remote_adapter import RemoteBookmark
                if dependencies is None or row['author_id'] in dependencies:continue
                # A v1 manual-ID request also contains a RemoteBookmark DTO;
                # neither its visibility nor the ledger's existence proves
                # bookmark membership. New projections need that actual source.
                # Previously accepted export jobs keep their durable intents.
                eligible=any(record.remote_id==row['work_id'] for record in self.s.bookmark_download_candidates())
                if account and eligible and any(isinstance(record,RemoteBookmark) and record.visibility in ('show','hide') for record in accepted):
                    mode=self.s.preferences.get_setting('bookmark_download_mode','direct')
                    self.enqueue('bookmark-projection',dict(relative=rel,sha256=sha,work_id=row['work_id'],account=account,mode=mode,use_work_folder=self.s.preferences.get_setting('use_work_folder','0')=='1'),dependencies=batch)

        self.scan_media(dependencies=batch)

    def _static_completed(self,live,current):
        jobs={j.job_id:j for j in live};records=dict(self._static_records)
        records.update(getattr(self.s,'_managed_job_records',{}))
        owner=self.s.static_jobs;operations=getattr(owner,'accepted_operations',lambda:())()
        store=getattr(self.s,'_managed_requests',None)
        for operation,jid,kind,target,title in operations:
            if kind!='pixiv-illust' or target not in current:continue
            if jid not in jobs:
                job=owner.retained_job(jid)
                if job is not None:jobs[jid]=job
            job=jobs.get(jid)
            if job is None or job.state!='done' or job.result!=current[target]:continue
            if jid in records or store is None:continue
            with self._item('static-intent:'+jid):
                record=store.load(operation)
                from .managed_download_requests import ManagedAuthorIntent
                work=record.record if isinstance(record,ManagedAuthorIntent) else record
                if work.remote_id!=target or work.title!=title:raise ValueError('saved static intent changed')
                records[jid]=record;self._static_records[jid]=record
        result={}
        for job in jobs.values():
            if job.state=='done':result.setdefault(job.target_id,[]).append((job,records.get(job.job_id)))
        return result

    def _author_dependencies(self,author_id=None):
        try:return self.scan_authors(author_id=author_id)
        except Exception as error:
            self._scan_error('authors',error)
            return None  # unknown set: all dependent bookmarks must wait

    def _author_settled(self,job):
        return self._publication_completed(job)

    def scan_authors(self,author_id=None):
        source=getattr(self.s.author_media,'completed_projection_sources',None)
        if source is None:return set()
        outcomes={}
        from .bookmark_projection import snapshot
        scoped=getattr(self.s.author_media,'completed_projection_sources_for_author',None)
        entries=scoped(author_id) if author_id is not None and scoped is not None else source()
        issues=getattr(self.s.author_media,'projection_unresolved_authors',None)
        invalid,unknown=issues(author_id) if issues is not None else (set(),False)
        for index,entry in enumerate(entries):
            try:
                if type(entry) is not dict:raise ValueError('invalid author source')
                aid=entry['author_id']
                if type(aid) is not str or re.fullmatch(r'[1-9][0-9]{0,19}',aid) is None:raise ValueError('invalid author source')
            except Exception as error:
                self._scan_error('author-entry:'+str(index),error);continue
            if author_id is not None and aid!=author_id:continue
            outcomes.setdefault(aid,[]).append(False)
            wid=entry.get('work_id')
            valid_wid=type(wid) is str and re.fullmatch(r'[1-9][0-9]{0,19}',wid) is not None
            key='author:'+wid if valid_wid else 'author-entry:'+str(index)
            with self._item(key):
                if not valid_wid or entry.get('kind') not in ('illustration','novel','ugoira'):
                    raise ValueError('invalid author source')
                kind=entry['kind'];aid=entry['author_id']
                identity=dict(output_scope='author',work_id=wid,author_id=aid,
                    mode='link',account=self.account,
                    use_work_folder=self.s.preferences.get_setting('use_work_folder','0')=='1')
                if kind=='illustration':
                    current=self.s.managed_downloads.verified_saved_receipt(wid)
                    if current is None or current[1]['author_id']!=aid:continue
                    if not self.s.managed_downloads.visible_source_pages(wid):
                        outcomes[aid][-1]=True
                        continue
                    identity.update(relative=current[0],sha256=current[2],author_name=current[1]['author_name'])
                else:
                    saved=snapshot(self.s,kind,wid,entry['format'],identity['use_work_folder'])
                    if saved['author_id']!=aid:raise ValueError('author projection source changed')
                    identity.update(media=kind,format=entry['format'],sha256=saved['proof'],author_name=saved['author'])
                # A generation change after admission reconciliation must not be
                # substituted into the author's older accepted job.
                if self.s.author_media._proof(kind,wid,entry['format'])!=entry['proof']:
                    continue
                identity['accepted_proof']=entry['proof']
                accepted=self.enqueue('author-projection',identity)
                outcomes[aid][-1]=self._author_settled(accepted)
        # Before the first master publication, incomplete author work must
        # settle before classification. A historical master is not undone by
        # a later generation conflict; that conflict remains item-visible.
        if unknown:return None  # valid author outputs progressed; new classification is still unknown
        return set(invalid)|{aid for aid,states in outcomes.items() if not all(states) and not self._author_established(aid)}

    def remember_bookmark(self,kind,wid,aid):
        import re
        account=self.account
        if account is None:return
        if kind not in ('novel','ugoira') or any(re.fullmatch(r'[1-9][0-9]{0,19}',value) is None for value in (wid,aid)):
            raise ValueError('invalid bookmark source')
        identity=dict(media=kind,work_id=wid,author_id=aid,account=account)
        path=self.root/(digest(identity)+'.bookmark.json')
        from .bookmark_projection import publish_bytes
        def verify(node):
            if _read_bounded(node,4096)!=packet(identity):raise ValueError('bookmark source changed')
        with self.lock:publish_bytes(path,packet(identity),verify)

    def enqueue_media(self,kind,wid,fmt,expected_author_id=None,*,dependencies=...):
        from .bookmark_projection import snapshot
        account=self.account
        if not account:raise ValueError('bookmark account required')
        folders=self.s.preferences.get_setting('use_work_folder','0')=='1'
        source=snapshot(self.s,kind,wid,fmt,folders)
        if expected_author_id is not None and source["author_id"]!=expected_author_id:raise ValueError("bookmark author changed")
        identity=dict(media=kind,work_id=wid,format=fmt,author_id=source['author_id'],
            sha256=source['proof'],account=account,mode=self.s.preferences.get_setting('bookmark_download_mode','direct'),
            use_work_folder=folders)
        if identity['mode']=='direct':identity['reference_layout']=1
        return self.enqueue('bookmark-projection',identity,dependencies=dependencies)

    def scan_media(self,*,dependencies=...):
        if self.account is None:return
        if self._valid_dependencies(dependencies):return self._scan_media_batch(dependencies)
        pending=self._author_dependencies()
        with self._dependency_scope(pending) as batch:return self._scan_media_batch(batch)

    def _scan_media_batch(self,batch):
        dependencies=batch.pending
        if dependencies is None:return
        for row in getattr(self.s,'_remote_records',()):
            with self._item('bookmark:'+row.remote_id):
                if row.kind=='novel':self.remember_bookmark('novel',row.remote_id,row.author_id)
                elif row.kind=='illustration' and row.illustration_type==2:self.remember_bookmark('ugoira',row.remote_id,row.author_id)
        for path in native_io_path(self.root).glob('*.bookmark.json'):
            with self._item('marker:'+path.name):
                value=json.loads(_read_bounded(path,4096))
                if path.name!=digest(value)+'.bookmark.json':raise ValueError('bookmark source changed')
                if value['account']!=self.account or value['author_id'] in dependencies:continue
                wid=value['work_id'];kind=value['media']
                if kind=='novel' and self.s.novel_jobs is not None:
                    fmt=self.s.preferences.get_setting('novel_save_format','epub').upper()
                    saved=self.s.novel_jobs.current(wid,fmt)
                    if saved is not None:self.enqueue_media(kind,wid,fmt,value['author_id'],dependencies=batch)
                elif kind=='ugoira' and self.s.ugoira is not None:
                    fmt=self.s.preferences.get_setting('ugoira_save_format','gif');fmt='frames' if fmt=='folder' else fmt
                    if self.s.ugoira.current(wid,fmt) is not None:self.enqueue_media(kind,wid,fmt,value['author_id'],dependencies=batch)

    def start(self):
        if self.thread is not None:return
        def loop():
            while not self.stop.is_set():
                try:self.scan()
                except Exception:self.last_error='post_save_scan_failed'
                self._reported_errors={k:v for k,v in self._reported_errors.items() if k in self.scan_errors}
                self.stop.wait(.5)
        self.thread=Thread(target=loop,name='pixivvault-save-exports',daemon=True);self.thread.start()

    def _owned_bytes(self,path,expected):
        path=native_io_path(path)
        _safe_path(path.parent)
        info=path.lstat()
        if not stat.S_ISREG(info.st_mode) or path.is_symlink() or getattr(info,'st_file_attributes',0)&0x400:
            raise ValueError('unsafe projection file')
        if sha256(path.read_bytes()).hexdigest()!=expected:raise ValueError('projection bytes changed')

    def _finish_zip_pending(self,job,identity,manifest,context):
        pending=native_io_path(self.root/(job.target_id+'.zip-pending.json'))
        if not pending.exists():return False
        note=json.loads(_read_bounded(pending,2_000_000),object_pairs_hook=_json_unique)
        if (type(note) is not dict or set(note)!={'version','source','archive','stage','sha256'}
                or note['version']!=1 or note['source']!=identity
                or type(note['stage']) is not str or re.fullmatch(r'zip-[a-f0-9]{32}\.tmp',note['stage']) is None
                or type(note['archive']) is not str or Path(note['archive']).name!=note['archive']
                or any(c in note['archive'] for c in ('/',chr(92),':'))
                or not note['archive'].endswith('('+identity['author_id']+').zip')
                or type(note['sha256']) is not str or re.fullmatch(r'[a-f0-9]{64}',note['sha256']) is None):
            raise ValueError('ZIP recovery evidence rejected')
        stage=native_io_path(self.zip.incomplete)/note['stage']
        archive=native_io_path(self.zip.root)/note['archive']
        _safe_path(stage.parent);_safe_path(archive.parent)
        receipt=dict(archive=note['archive'],sha256=note['sha256'],source=identity)
        completed=native_io_path(manifest).exists()
        if completed and json.loads(_read_bounded(manifest,2_000_000),object_pairs_hook=_json_unique)!=receipt:
            raise ValueError('ZIP recovery manifest changed')
        if stage.is_symlink():raise ValueError('unsafe ZIP recovery stage')
        if not stage.exists():
            if completed:return True
            raise ValueError('ZIP recovery stage unavailable')
        info=stage.lstat()
        if (stage.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_nlink not in (1,2)
                or getattr(info,'st_file_attributes',0)&0x400):raise ValueError('unsafe ZIP recovery stage')
        hasher=sha256()
        with stage.open('rb') as stream:
            opened=os.fstat(stream.fileno())
            if not os.path.samestat(info,opened) or opened.st_nlink not in (1,2):raise ValueError('ZIP recovery stage changed')
            for part in iter(lambda:stream.read(1024*1024),b''):hasher.update(part)
        if hasher.hexdigest()!=note['sha256']:raise ValueError('ZIP recovery bytes changed')
        if archive.exists() or archive.is_symlink():
            existing=archive.lstat()
            if (archive.is_symlink() or not stat.S_ISREG(existing.st_mode)
                    or getattr(existing,'st_file_attributes',0)&0x400
                    or info.st_nlink!=2 or existing.st_nlink!=2 or not os.path.samestat(info,existing)):
                raise ValueError('ZIP recovery destination not owned')
        elif completed:
            raise ValueError('ZIP recovery destination missing')
        else:
            if info.st_nlink!=1:raise ValueError('ZIP recovery stage has external link')
            with self._zip_publication_scope(identity):
                context.checkpoint()
                self._auto_page_state(identity)
                os.link(stage,archive)  # exclusive publication, never overwrite
                _fsync_dir(archive.parent)
        if not completed:
            context.checkpoint()
            _publish_owned(manifest,packet(receipt))
        # Both names are proven to refer to our staged bytes. Remove only the
        # retained stage name after the completion manifest is durable.
        info=stage.lstat();existing=archive.lstat()
        if info.st_nlink!=2 or existing.st_nlink!=2 or not os.path.samestat(info,existing):
            raise ValueError('ZIP recovery links changed')
        stage.unlink();_fsync_dir(stage.parent)
        return True

    def run(self,job,context):
        from .job_service import JobCancelled
        self._io_operation='verify_export_intent'
        try:return self._run(job,context)
        except JobCancelled:raise
        except Exception as error:
            record_io_failure(native_io_path(self.root),job,error,self._io_operation)
            raise

    def _run(self,job,context):
        identity=json.loads(_read_bounded(self.root/(job.target_id+'.json'),2_000_000))
        if digest(identity)!=job.target_id:raise ValueError('export intent changed')
        manifest=self.root/(job.target_id+'.export.json')
        context.checkpoint()
        if job.kind=='auto-zip':
            aid=identity['author_id']
            pending=native_io_path(self.root/(job.target_id+'.zip-pending.json'))
            if not native_io_path(manifest).exists() and not pending.exists():
                rows,source_pages,_=self._auto_page_state(identity)
                self._io_operation='export_author_zip'
                def record_stage(stage,archive):
                    context.checkpoint()
                    h=sha256()
                    with stage.open('rb') as stream:
                        for part in iter(lambda:stream.read(1024*1024),b''):h.update(part)
                    note=dict(version=1,source=identity,archive=archive.name,stage=stage.name,sha256=h.hexdigest())
                    _publish_owned(pending,packet(note))
                self.zip.export_author(aid,expected_proofs=identity['proofs'],before_publish=record_stage,
                                       expected_source_pages=source_pages)
            self._finish_zip_pending(job,identity,manifest,context)
            receipt=json.loads(_read_bounded(manifest,2_000_000),object_pairs_hook=_json_unique)
            if receipt.get('source')!=identity or Path(receipt['archive']).name!=receipt['archive']:
                raise ValueError('ZIP export manifest changed')
            archive=native_io_path(self.zip.root/receipt['archive']);_safe_path(archive,file=True)
            hasher=sha256()
            with archive.open('rb') as stream:
                for part in iter(lambda:stream.read(1024*1024),b''):hasher.update(part)
            if hasher.hexdigest()!=receipt['sha256']:raise ValueError('ZIP bytes changed')
            context.checkpoint();return str(manifest.relative_to(self.root))
        if job.kind not in ('bookmark-projection','author-projection','auto-zip'):raise ValueError('unknown export kind')
        if identity.get('media') in ('novel','ugoira'):
            from .bookmark_projection import export_media
            return export_media(self,identity,context,manifest)
        with self.s.managed_downloads.work_lock(identity['work_id']):
            return self._run_static_projection(identity,manifest,context)

    def _run_static_projection(self,identity,manifest,context):
        current,source_pages,source_token=self._static_page_state(identity)
        pinned=identity.get('author_folder')
        if pinned is not None and (type(pinned) is not str or Path(pinned).name!=pinned or not pinned.endswith('('+current[1]['author_id']+')')):
            raise ValueError('unsafe author folder')
        row=current[1];base=native_io_path(self.projection.root);author=_INVALID_FILENAME.sub('_',row['author_name'])+'('+row['author_id']+')'
        bookmark=(base/('☆ブックマーク('+identity['account']+')')) if identity['account'] else None;records=[]
        for index in source_pages:
            page=row['files'][index]
            context.checkpoint()
            if self.s.managed_downloads._generations.work_state_token(identity['work_id'])!=source_token:
                raise ValueError('saved page selection changed')
            data=self.s.managed_downloads.read_saved_page(current[0],index,current[2]);expected=sha256(data).hexdigest()
            name=legacy_filename(row['title'],row['work_id'],index,row['page_count'],{'jpeg':'jpg','png':'png','webp':'webp'}[page['format']])
            body=base/'.bodies'/current[2]/str(index)
            self._io_operation='projection_body_mkdir'
            _safe_path(body.parent);body.parent.mkdir(parents=True,exist_ok=True)
            self._io_operation='projection_copy_or_verify_body'
            if body.exists():self._owned_bytes(body,expected)
            else:_publish_owned(body,data)
            if identity.get('output_scope')=='author':
                work=_INVALID_FILENAME.sub('_',row['title'])[:30]+'('+row['work_id']+')'
                destinations=[base/identity['author_folder']/(Path(work)/name if identity.get('use_work_folder') else Path(name))]
            elif identity['mode']=='direct':
                destinations=[bookmark/(author+'_'+name)]
                if identity.get('author_folder'):
                    work=_INVALID_FILENAME.sub('_',row['title'])[:30]+'('+row['work_id']+')'
                    destinations.append(base/identity['author_folder']/(Path(work)/name if identity.get('use_work_folder') else Path(name)))
            elif identity['mode']=='link':
                work=_INVALID_FILENAME.sub('_',row['title'])[:30]+'('+row['work_id']+')'
                suffix=Path(work)/name if identity.get('use_work_folder',True) else Path(name)
                pinned=identity.get('author_folder')
                destinations=([bookmark/author/suffix] if 'author_folder' in identity and pinned is None else [base/(pinned or author)/suffix,bookmark/author/suffix])
            else:raise ValueError('unknown projection layout')
            for target in destinations:
                self._io_operation='projection_target_mkdir'
                _safe_path(target.parent);target.parent.mkdir(parents=True,exist_ok=True)
                if target.exists():
                    self._owned_bytes(target,expected)
                    if not os.path.samefile(body,target):raise ValueError('projection collision')
                else:
                    self._io_operation='projection_hardlink'
                    os.link(body,target)
                records.append(dict(path=str(target.relative_to(base)),sha256=expected,bytes=len(data)))
        if self.s.managed_downloads.verified_saved_receipt(identity['work_id'])!=current:raise ValueError('saved generation changed')
        if self.s.managed_downloads._generations.work_state_token(identity['work_id'])!=source_token:
            raise ValueError('saved page selection changed')
        self._io_operation='projection_manifest'
        result=packet(dict(source=identity,files=records))
        if native_io_path(manifest).exists():
            if _read_bounded(manifest,2_000_000)!=result:raise ValueError('projection manifest changed')
        else:_publish_owned(manifest,result)
        context.checkpoint();return str(manifest.relative_to(self.root))

    def close(self):
        with self._account_lock:self.stop.set()
        if self.thread is not None:self.thread.join(150)
        if self.thread is not None and self.thread.is_alive():raise RuntimeError('export scanner did not stop')
        self.jobs.close(timeout=150)
        self.projection.close();self.zip.close();self.owner.close()
