"""Explicit offline migration: user inventory + locked, verified copies.

No Cookie/Vault reader or network client is used. Existing STOPs are monotonic.
The caller's declaration alone never creates an owner: execute requires the
service's current preview and independently locks/copies/verifies every source.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager, nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from threading import Event, Lock
import uuid

from .managed_network_stop_gate import (
    ManagedNetworkGateStorageError, ManagedNetworkStopGate,
    _byte_lock_contended, _file_identity, _lock_open_contended,
    _gate_cleanup, _open_existing_stop_lock, _reject_links, _run_gate_cleanup,
    _set_stop_byte_lock, _verify_open_stop_lock,
)
from .managed_network_stop_history_readonly import _check_marker, _read_gate_immutable
from .managed_network_stop_shared_scope import (
    ManagedUserNetworkStopScope, _windows_local_app_data, _file_identity_directory,
    _OWNER_FILE, _OWNER_SCHEMA, _GATE_FILE,
)
from .managed_network_stop_profile_scope import _SCOPE_MARKER, _SCOPE_IDENTITY
from .native_profile import PROFILE_MARKER, PROFILE_IDENTITY, PROFILE_DB, NativeProfileStore, plan_storage

_GATE = '.pixivvault-network-stop.sqlite3'
_SERIAL = Lock()
_KINDS = frozenset(('native', 'images', 'novel', 'ugoira'))


def _now(): return datetime.now(timezone.utc).isoformat()
def _bytes(value): return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode()
def _hash(value): return hashlib.sha256(value).hexdigest()


class MigrationRejected(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class MigrationSource:
    kind: str
    root: Path
    identity: tuple
    files: tuple
    state: tuple
    install_native: bool = False
    absent: bool = False


@dataclass(frozen=True)
class MigrationPlan:
    sources: tuple[MigrationSource, ...]
    token: str
    created_utc: str


@dataclass
class MigrationItemResult:
    kind: str
    root: str
    status: str = 'unprocessed'
    reason: str = ''


@dataclass
class MigrationResult:
    status: str
    reason: str = ''
    items: list[MigrationItemResult] = field(default_factory=list)
    operation_id: str = ''
    stopped: bool = False


class _FileLockBusy(OSError):
    """The OS refused a nonblocking byte lock held by another owner."""


class _FileLock:
    def __init__(self, path, *, create=False):
        self.path, self.stream = Path(path), None
        self.created = False
        _reject_links(self.path)
        try:
            if create:
                try:
                    self.stream = self.path.open('x+b')
                    self.stream.write(b'0'); self.stream.flush(); os.fsync(self.stream.fileno())
                    self.created = True
                except FileExistsError:
                    pass
            if self.stream is None:
                try:
                    self.stream = _open_existing_stop_lock(self.path)
                except OSError as error:
                    if _lock_open_contended(error):
                        raise _FileLockBusy('STOP file lock shared by another process') from None
                    raise
            # A concurrent creator may still be writing the initial byte.
            # Never lock an empty existing file ahead of that write.
            if os.fstat(self.stream.fileno()).st_size == 0:
                raise MigrationRejected('source_unverified')
            try:
                _set_stop_byte_lock(self.stream, lock=True)
            except OSError as error:
                if _byte_lock_contended(error):
                    raise _FileLockBusy('STOP file lock is busy') from None
                raise
            # Windows denies reads of the sentinel to a different handle.
            _verify_open_stop_lock(self.path, self.stream, None)
        except BaseException as error:
            if self.stream is not None:
                stream, self.stream = self.stream, None
                _run_gate_cleanup(stream.close, 'STOP lock close failed', primary=error)
            raise

    def close(self):
        if self.stream is not None:
            stream, self.stream = self.stream, None
            with _gate_cleanup(stream.close, 'STOP lock close failed'):
                try:
                    _set_stop_byte_lock(stream, lock=False)
                except (OSError, ValueError):
                    # The handle close releases the byte even if the explicit
                    # unlock failed. A failed close is still a storage error.
                    pass


def _write_new(path, data):
    _reject_links(path.parent)
    with path.open('xb') as stream:
        stream.write(data); stream.flush(); os.fsync(stream.fileno())


def _journal(path, data):
    temp = path.with_name(path.name + '-' + uuid.uuid4().hex)
    _write_new(temp, _bytes(data))
    os.replace(temp, path)  # Only this operation's own journal generation.


def _rename_new(source, destination, *, directory=False):
    """No replacement of even an empty foreign destination."""
    if directory or os.name == 'nt':
        _rename_atomic_new(source, destination)
    else:
        os.link(source, destination)
        source.unlink()  # Move of a newly generated staging file, never old data.


def _rename_atomic_new(source, destination):
    """Publish without replacing a destination or exposing a second link."""
    if os.name == 'nt':
        os.rename(source, destination)  # Windows rename is no-replace.
    else:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        rename = getattr(libc, 'renameat2', None)
        if rename is None: raise MigrationRejected('atomic_publish_unavailable')
        if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1):
            raise OSError(ctypes.get_errno(), 'migration publish unavailable')


def _fingerprint(path, ticket=None):
    identity = _file_identity(path)
    stat = path.stat()
    digest = hashlib.sha256()
    with nullcontext(ticket.stream) if ticket is not None else path.open('rb') as stream:
        stream.seek(0)
        for block in iter(lambda: stream.read(1024 * 1024), b''): digest.update(block)
    if identity != _file_identity(path) or (stat.st_size, stat.st_mtime_ns) != (path.stat().st_size, path.stat().st_mtime_ns):
        raise MigrationRejected('source_changed')
    return (path.name, identity, stat.st_size, digest.hexdigest())


def _collect(kind, root, locked=None, *, absent=False):
    if kind not in _KINDS: raise MigrationRejected('source_unverified')
    root = Path(root)
    if not root.is_absolute() or root == root.parent: raise MigrationRejected('source_unverified')
    _reject_links(root)
    if absent:
        from .native_profile import STORAGE_MARKER,STORAGE_IDENTITY,_owned_marker
        if root.exists():raise MigrationRejected('source_changed')
        _owned_marker(root.parent,STORAGE_MARKER,STORAGE_IDENTITY)
        return MigrationSource(kind,root.absolute(),_file_identity_directory(root.parent),(),(False,None,0),False,True)
    root = root.resolve(strict=True)
    identity = _file_identity_directory(root)
    install = False
    if kind == 'native':
        if (root / PROFILE_MARKER).read_bytes() != PROFILE_IDENTITY:
            raise MigrationRejected('source_unverified')
        NativeProfileStore.peek(root)
        paths = [root / PROFILE_MARKER, root / PROFILE_DB]
        scope_files = [root / _SCOPE_MARKER, root / _GATE, root / (_GATE + '.lock')]
        if not any(p.exists() for p in scope_files):
            # This means no established *native scope*, not no old media STOP.
            # It is publishable only with the full confirmed multi-root plan.
            install = True
        else:
            _check_marker(root, kind)
            paths += scope_files
    else:
        _check_marker(root, kind)
        if kind == 'images':
            from .managed_downloads import MARKER
        elif kind == 'novel': MARKER = '.pixivvault-novel-jobs.json'
        else:
            from .managed_ugoira import MARKER
        paths = [root / MARKER, root / _GATE, root / (_GATE + '.lock')]
    if any((root / (_GATE + suffix)).exists() for suffix in ('-wal', '-shm', '-journal')):
        raise MigrationRejected('source_busy')
    if (root / '.pixivvault-user-stop-inflight.v1').exists():
        raise MigrationRejected('source_unverified')
    files = tuple(_fingerprint(p, (locked or {}).get(p)) for p in paths)
    state = (False, None, 0) if install else _read_gate_immutable(root / _GATE)
    return MigrationSource(kind, root, identity, files, state, install)


class StopMigrationService:
    def __init__(self, store, *, target_root=None):
        self.store = store
        # Product UI never accepts a destination path. Injection is for tests.
        self._target_root = Path(target_root) if target_root is not None else None
        self._plan = None
    @property
    def target_root(self):
        from .managed_network_stop_shared_scope import shared_owner_root
        from .test_owner_runtime import require_inside
        target = self._target_root or shared_owner_root()
        require_inside(target)
        return target
    def configured_sources(self):
        roots = [('native', self.store.root)]
        paths = plan_storage(self.store.snapshot(), self.store.root)
        for key, kind in (('pixiv_download_root','images'),('novel_download_root','novel'),('ugoira_root','ugoira')):
            if paths.get(key) is not None: roots.append((kind, paths[key]))
        return roots
    def preview(self, additional=()):
        self._plan = None
        try:
            self._require_no_pending()
            configured = self.configured_sources()
            inputs = configured + list(additional)
            for _, old in self._records():
                for value in old.get('inventory',[]):
                    if (value['kind'],Path(value['root'])) not in inputs:
                        inputs.append((value['kind'],Path(value['root'])))
            unique = {}
            for kind, root in inputs:
                from .test_owner_runtime import require_inside
                require_inside(root)
                source = _collect(kind, root, absent=(kind != "native" and (kind,root) in configured and not Path(root).exists()))
                key = os.path.normcase(str(source.root))
                if key in unique and unique[key].kind != kind: raise MigrationRejected('source_unverified')
                unique[key] = source
            sources = tuple(unique.values())
            plan = MigrationPlan(sources, uuid.uuid4().hex, _now())
            self._plan = plan
            return plan
        except MigrationRejected: raise
        except Exception: raise MigrationRejected('source_unverified') from None
    def _checkpoint(self, phase): pass
    @staticmethod
    def _check_cancel(cancel):
        if cancel.is_set(): raise MigrationRejected('cancelled')
    @contextmanager
    def _exclusive(self):
        if not _SERIAL.acquire(blocking=False): raise MigrationRejected('source_busy')
        try:
            from .managed_network_stop_gate import ManagedNetworkGateBusy
            from .managed_network_stop_startup import _ANCHOR, shared_startup_ticket
            parent = self.target_root.parent
            _reject_links(parent); parent.mkdir(parents=True, exist_ok=True)
            try:
                publication_ticket = shared_startup_ticket(parent)
            except ManagedNetworkGateBusy:
                raise MigrationRejected('source_busy') from None
            try:
                anchor = parent / _ANCHOR
                _reject_links(anchor)
                if anchor.exists() and not self.target_root.exists():
                    raise MigrationRejected('history_unverified')
                lock = _FileLock(parent / '.pixivvault-stop-migration.lock', create=True)
                try: yield lock.created
                finally: lock.close()
            finally:
                publication_ticket.close()
        finally: _SERIAL.release()
    def _records(self):
        base=self.target_root.parent/'.pixivvault-stop-migrations'
        if not base.exists(): return []
        _reject_links(base)
        result=[]
        for folder in sorted(base.iterdir()):
            _reject_links(folder)
            if len(folder.name)!=32 or any(c not in '0123456789abcdef' for c in folder.name):
                raise MigrationRejected('recovery_required')
            path=folder/'journal.json';_reject_links(path)
            try:record=json.loads(path.read_text())
            except Exception:raise MigrationRejected('recovery_required') from None
            if record.get('schema')!=1 or record.get('operation_id')!=folder.name:
                raise MigrationRejected('recovery_required')
            result.append((folder,record))
        return result
    def _require_no_pending(self):
        for folder,record in self._records():
            if record.get('state')=='completed':
                if not self.target_root.exists():raise MigrationRejected('history_unverified')
                continue
            if record.get('state') not in ('failed','cancelled','recovered'):
                raise MigrationRejected('recovery_required')
            for move in record.get('native_moves',[]):
                staging=Path(move['staging']);destination=Path(move['destination'])
                if destination.exists() or not staging.is_file():raise MigrationRejected('recovery_required')
                if _file_identity(staging)!=tuple(move['identity']) or _hash(staging.read_bytes())!=move['sha256']:
                    raise MigrationRejected('recovery_required')
    @staticmethod
    def _verify_archive(root, proof_hash):
        record=(root/'migration-record.json').read_bytes()
        if _hash(record)!=proof_hash:raise MigrationRejected('copy_mismatch')
        proof=json.loads(record)
        if proof.get('schema')!='pixivvault-stop-migration-v1' or proof.get('user_confirmed_complete') is not True:
            raise MigrationRejected('copy_mismatch')
        for source in proof['sources']:
            for value in source['files']:
                relative=Path(value['copy'])
                if relative.is_absolute() or '..' in relative.parts or len(relative.parts)!=3 or relative.parts[0]!='sources':
                    raise MigrationRejected('copy_mismatch')
                path=root/relative;_reject_links(path)
                if _hash(path.read_bytes())!=value['sha256']:raise MigrationRejected('copy_mismatch')
        return proof
    def _rollback(self, journal, stage, new_locks=()):
        moves=journal.get('native_moves',[])
        if not moves:return
        for ticket in new_locks:ticket.close()
        proof=self._verify_archive(stage,journal['proof_sha256'])
        if _bytes(proof.get('native_moves'))!=_bytes(moves) or proof['operation_id']!=journal['operation_id']:
            raise MigrationRejected('recovery_conflict')
        sources={x['root']:x for x in proof['sources'] if x['kind']=='native' and x['native_scope_was_absent'] is True}
        with ExitStack() as held:
            tickets={}
            roots={value['root'] for value in moves}
            for name in sorted(roots):
                root=Path(name)
                if name not in sources or _file_identity_directory(root)!=tuple(sources[name]['root_identity']):
                    raise MigrationRejected('recovery_conflict')
                con=sqlite3.connect((root/PROFILE_DB).as_uri()+'?mode=rw',uri=True,timeout=0.1,isolation_level=None)
                held.callback(con.close);con.execute('BEGIN IMMEDIATE');held.callback(lambda c=con:c.execute('ROLLBACK'))
                lock=root/(_GATE+'.lock')
                if lock.exists():
                    ticket=_FileLock(lock);held.callback(ticket.close);tickets[lock]=ticket
                for f in sources[name]['files']:
                    if _fingerprint(root/f['name'])[3]!=f['sha256']:raise MigrationRejected('recovery_conflict')
            # Validate ALL reservations before moving any file.
            for move in moves:
                root=Path(move['root']);dest=Path(move['destination']);staging=Path(move['staging'])
                if (dest.parent!=root or dest.name not in (_GATE,_GATE+'.lock',_SCOPE_MARKER)
                    or staging.name!=dest.name or staging.parent.parent!=root.parent
                    or not staging.parent.name.startswith('.pv-stop-migrate-'+journal['operation_id']+'-')
                    or tuple(move['root_identity'])!=tuple(sources[str(root)]['root_identity'])):
                    raise MigrationRejected('recovery_conflict')
                _reject_links(staging.parent)
                if json.loads((staging.parent/'operation.json').read_text())!={'operation_id':journal['operation_id'],'profile':str(root)}:
                    raise MigrationRejected('recovery_conflict')
                if dest.exists()==staging.exists():raise MigrationRejected('recovery_conflict')
                existing=dest if dest.exists() else staging
                value=_fingerprint(existing,tickets.get(existing))
                if value[1]!=tuple(move['identity']) or value[3]!=move['sha256']:raise MigrationRejected('recovery_conflict')
            ordered=list(reversed(moves))
            for move in ordered:
                dest=Path(move['destination'])
                if dest.name==_SCOPE_MARKER and dest.exists():_rename_new(dest,Path(move['staging']))
            for ticket in tickets.values():ticket.close()
            for move in ordered:
                dest=Path(move['destination'])
                if dest.name!=_SCOPE_MARKER and dest.exists():_rename_new(dest,Path(move['staging']))
    def execute(self, plan, *, confirmed=False, cancel=None, progress=None):
        if confirmed is not True: return MigrationResult('failed','confirmation_required')
        if plan is not self._plan or plan is None: return MigrationResult('failed','preview_required')
        self._plan = None  # A confirmation cannot be replayed.
        cancel = cancel or Event()
        items = [MigrationItemResult(x.kind,str(x.root)) for x in plan.sources]
        result = MigrationResult('failed', items=items, operation_id=uuid.uuid4().hex)
        journal = {'schema':1,'operation_id':result.operation_id,'profile_root':str(self.store.root.resolve()),
                   'profile_identity':list(_file_identity_directory(self.store.root)), 'native_moves':[], 'state':'copying',
                   'inventory':[{'kind':x.kind,'root':str(x.root)} for x in plan.sources]}
        stage = None; journal_path = None; published = False; new_locks = []
        active = 0
        def report(phase, number=0):
            if progress:
                try:progress(phase, number, len(items))
                except Exception:pass  # Losing a view cannot undo a durable commit.
        guard=ExitStack()
        try:
            if cancel.is_set():raise MigrationRejected('cancelled')
            fresh=guard.enter_context(self._exclusive())
            with ExitStack() as locks:
                _reject_links(self.target_root)
                if self.target_root.exists(): raise MigrationRejected('target_exists')
                records_before=self._records()
                if not fresh and not records_before:raise MigrationRejected('history_unverified')
                self._require_no_pending()
                covered={(x.kind,os.path.normcase(str(x.root))) for x in plan.sources}
                if any((x['kind'],os.path.normcase(x['root'])) not in covered
                       for _,r in records_before for x in r.get('inventory',[])):
                    raise MigrationRejected('source_changed')
                base=self.target_root.parent/'.pixivvault-stop-migrations'
                _reject_links(base);base.mkdir(exist_ok=True)
                operation=base/result.operation_id;operation.mkdir()
                stage=operation/'owner';stage.mkdir()
                journal_path=operation/'journal.json';_journal(journal_path,journal)
                self._check_cancel(cancel)
                report('locking')
                # SQLite reserved locks prevent settings writes, but not reads.
                source_locks = {}
                for active, source in enumerate(plan.sources):
                    if source.kind == 'native':
                        con = sqlite3.connect((source.root/PROFILE_DB).as_uri()+'?mode=rw', uri=True, timeout=0.1, isolation_level=None)
                        locks.callback(con.close)
                        con.execute('BEGIN IMMEDIATE')
                        locks.callback(lambda c=con:c.execute('ROLLBACK'))
                    if not source.install_native and not source.absent:
                        try:ticket = _FileLock(source.root/(_GATE+'.lock'))
                        except OSError:raise MigrationRejected('source_busy') from None
                        locks.callback(ticket.close)
                        source_locks[source.root/(_GATE+'.lock')] = ticket
                for active, source in enumerate(plan.sources):
                    if _collect(source.kind, source.root,source_locks,absent=source.absent) != source: raise MigrationRejected('source_changed')
                records = []
                for active, source in enumerate(plan.sources):
                    self._check_cancel(cancel); report('copying',active)
                    record = {'kind':source.kind,'root':str(source.root),'root_identity':source.identity,
                              'state':source.state,'native_scope_was_absent':source.install_native,'absent':source.absent,
                              'locked_utc':_now(),'files':[]}
                    folder = stage/'sources'/str(active); folder.mkdir(parents=True)
                    for name, identity, size, digest in source.files:
                        target = folder/name
                        ticket = source_locks.get(source.root/name)
                        with (nullcontext(ticket.stream) if ticket is not None else (source.root/name).open('rb')) as src, target.open('xb') as dest:
                            src.seek(0)
                            for chunk in iter(lambda:src.read(1024*1024),b''):
                                self._check_cancel(cancel); dest.write(chunk)
                            dest.flush();os.fsync(dest.fileno())
                        if _hash(target.read_bytes()) != digest: raise MigrationRejected('copy_mismatch')
                        record['files'].append({'name':name,'identity':identity,'size':size,'sha256':digest,
                                                'copy':target.relative_to(stage).as_posix()})
                    if _collect(source.kind,source.root,source_locks,absent=source.absent) != source: raise MigrationRejected('source_changed')
                    record['verified_utc']=_now(); records.append(record)
                    self._checkpoint('copied:'+str(active)); self._check_cancel(cancel)
                report('verifying')
                for active, source in enumerate(plan.sources):
                    if _collect(source.kind,source.root,source_locks,absent=source.absent) != source: raise MigrationRejected('source_changed')
                statuses = [s.state[1] for s in plan.sources if s.state[0]]
                gate = ManagedNetworkStopGate(stage/_GATE_FILE)
                if statuses: gate.trip(statuses[0])
                result.stopped = bool(statuses)
                # Prepare native additions on the SAME volume as their profile.
                for active, source in enumerate(plan.sources):
                    if not source.install_native: continue
                    local = source.root.parent/('.pv-stop-migrate-'+result.operation_id+'-'+str(active))
                    _reject_links(local); local.mkdir()
                    _write_new(local/'operation.json',_bytes({'operation_id':result.operation_id,'profile':str(source.root)}))
                    for name in (_GATE,_GATE+'.lock',_SCOPE_MARKER):
                        data = (_SCOPE_IDENTITY if name==_SCOPE_MARKER else
                                (stage/(_GATE_FILE+('.lock' if name.endswith('.lock') else ''))).read_bytes())
                        _write_new(local/name,data)
                        move={'root':str(source.root),'root_identity':source.identity,'destination':str(source.root/name),
                              'staging':str(local/name),'identity':_file_identity(local/name),'sha256':_hash(data),'source_index':active}
                        journal['native_moves'].append(move)
                storage=[]
                snapshot=self.store.snapshot()
                if snapshot.save_parent is not None:
                    from .native_profile import STORAGE_FOLDER,STORAGE_MARKER,STORAGE_IDENTITY,_owned_marker
                    base=snapshot.save_parent/STORAGE_FOLDER
                    _owned_marker(base,STORAGE_MARKER,STORAGE_IDENTITY)
                    gate_id=next((m['identity'] for m in journal['native_moves'] if m['root']==str(self.store.root.resolve()) and Path(m['destination']).name==_GATE),None)
                    if gate_id is None:gate_id=_file_identity(self.store.root/_GATE)
                    storage=[{'profile':str(self.store.root.resolve()),'profile_identity':_file_identity_directory(self.store.root),
                              'gate_identity':gate_id,'base':str(base.resolve()),'base_identity':_file_identity_directory(base),
                              'roots':[{ 'kind':x.kind,'root':str(x.root),'identity':x.identity,'absent':x.absent} for x in plan.sources if x.kind!='native' and x.root.parent==base]}]
                proof = {'schema':'pixivvault-stop-migration-v1','operation_id':result.operation_id,
                         'preview_token':plan.token,'preview_utc':plan.created_utc,
                         'confirmed_utc':_now(),'user_confirmed_complete':True,'sources':records,
                         'storage_bindings':storage,'native_moves':journal['native_moves'],'preserved_stop_statuses':statuses,'verified_utc':_now()}
                _write_new(stage/'migration-record.json',_bytes(proof))
                journal['proof_sha256']=_hash(_bytes(proof)); journal['state']='verified'
                _journal(journal_path,journal)
                self._checkpoint('verified'); self._check_cancel(cancel)
                for move in journal['native_moves']:
                    self._check_cancel(cancel)
                    dest=Path(move['destination']);_rename_new(Path(move['staging']),dest)
                    if dest.name.endswith('.lock'):
                        ticket=_FileLock(dest);new_locks.append(ticket);locks.callback(ticket.close)
                    if dest.name==_SCOPE_MARKER:
                        self._checkpoint('native:'+str(move['source_index']));self._check_cancel(cancel)
                seal={'schema':_OWNER_SCHEMA,'state':'sealed','root_identity':list(_file_identity_directory(stage)),
                      'gate_identity':list(_file_identity(stage/_GATE_FILE)),
                      'lock_identity':list(_file_identity(stage/(_GATE_FILE+'.lock'))),
                      'history_proof':journal['proof_sha256']}
                _write_new(stage/_OWNER_FILE,_bytes(seal))
                # Independently open the staged seal and compare the real gate.
                check=ManagedUserNetworkStopScope(stage).gate.snapshot()
                if check.blocked!=bool(statuses) or check.status_code!=(statuses[0] if statuses else None):
                    raise MigrationRejected('copy_mismatch')
                self._checkpoint('before-publish'); self._check_cancel(cancel)
                self._verify_archive(stage,journal['proof_sha256'])
                for source in plan.sources:
                    if source.absent:
                        _collect(source.kind,source.root,absent=True)
                        continue
                    if _file_identity_directory(source.root)!=source.identity:
                        raise MigrationRejected('source_changed')
                    for value in source.files:
                        if _fingerprint(source.root/value[0],source_locks.get(source.root/value[0]))!=value:
                            raise MigrationRejected('source_changed')
                report('publishing')
                _rename_new(stage,self.target_root,directory=True); published=True
                self._checkpoint('published')
                self._verify_published(journal['proof_sha256'])
                journal['state']='completed';_journal(journal_path,journal)
                if _bytes(json.loads(journal_path.read_text()))!=_bytes(journal):raise MigrationRejected('copy_mismatch')
                result.status='completed'
                for item in items:item.status='success'
                report('completed',len(items))
                return result
        except Exception as error:
            reason = error.reason if isinstance(error,MigrationRejected) else 'io_failed'
            result.reason = reason
            if published:
                result.status='uncertain'; result.reason='publication_unverified'
            else:
                try:
                    if journal.get('native_moves') and 'proof_sha256' not in journal:
                        if any(Path(x['destination']).exists() for x in journal['native_moves']):raise MigrationRejected('recovery_conflict')
                    else:self._rollback(journal,stage,new_locks)
                    result.status='cancelled' if reason=='cancelled' else 'failed'
                except Exception: result.status='failed';result.reason='recovery_conflict'
            for i,item in enumerate(items):
                item.status='failed' if i==active and reason!='cancelled' else 'unprocessed';item.reason=result.reason
            if journal_path is not None:
                journal['state']=result.status; journal['reason']=result.reason
                try:_journal(journal_path,journal)
                except Exception:pass  # Keep earlier durable reservation and all data.
            return result
        finally:guard.close()
    def _verify_published(self, proof_hash):
        proof=self._verify_archive(self.target_root,proof_hash)
        owner=ManagedUserNetworkStopScope(self.target_root)
        seal=json.loads((self.target_root/_OWNER_FILE).read_text())
        if seal['history_proof']!=proof_hash:raise MigrationRejected('copy_mismatch')
        state=owner.gate.snapshot()  # Never issue or authorize a GET here.
        statuses=proof['preserved_stop_statuses']
        if statuses and (not state.blocked or state.status_code!=statuses[0]):raise MigrationRejected('copy_mismatch')
    def recover_pending(self):
        try:
            with self._exclusive():
                for folder,journal in self._records():
                    path=folder/'journal.json';stage=folder/'owner'
                    if journal.get('profile_root')!=str(self.store.root.resolve()):continue
                    if journal.get('profile_identity')!=list(_file_identity_directory(self.store.root)):
                        raise MigrationRejected('recovery_conflict')
                    if self.target_root.exists():
                        proof=json.loads((self.target_root/'migration-record.json').read_text())
                        if proof['operation_id']!=journal['operation_id']:continue
                        self._verify_published(journal['proof_sha256'])
                        journal['state']='completed';_journal(path,journal)
                        return MigrationResult('completed',stopped=ManagedUserNetworkStopScope(self.target_root).gate.snapshot().blocked)
                    if not stage.exists() or journal.get('state')=='completed':raise MigrationRejected('history_unverified')
                    if journal.get('native_moves') and 'proof_sha256' not in journal:
                        if any(Path(x['destination']).exists() for x in journal['native_moves']):raise MigrationRejected('recovery_conflict')
                    else:self._rollback(journal,stage)
                    journal['state']='recovered';_journal(path,journal)
                if self.target_root.exists():
                    raise MigrationRejected('history_unverified')
                return MigrationResult('recovered')
        except Exception:return MigrationResult('failed','recovery_conflict')
