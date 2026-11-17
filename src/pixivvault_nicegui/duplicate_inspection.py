"""Original near-duplicate review over verified isolated managed originals.

Original: tools/dedup.py and gui.py:5025-5828 at 3ac3aa0. Explicit scans publish
an owned undecidable-ledger generation; confirmation plans do not move files.
Managed operations preserve receipts. Confirmed legacy moves retain complete
before-generations and update the original folder/ZIP through durable intents.
"""
from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass, replace
from io import BytesIO
import base64
import hashlib
import json
from pathlib import Path
import sqlite3
from threading import Event, RLock, Lock
from uuid import uuid4
from PIL import Image
from .fs_paths import sqlite_readonly_uri
from .managed_downloads import ManagedPixivDownloads
from .managed_quarantine import TrashEmptyResult, check_trash_confirmation

THRESHOLD=4
PER_AUTHOR_FILE_LIMIT=2000
MAX_VISIBLE_GROUPS=50
MAX_VISIBLE_ITEMS=5
BLOCKED_REASON='保存済み作品を保全した隔離と再取得防止の手順が未受入のため、移動は利用できません。'

class DuplicateInspectionError(ValueError):
    def __init__(self):super().__init__('duplicate inspection snapshot unavailable or changed')

@dataclass(frozen=True)
class DuplicatePage:
    work_id:str
    page:int
    author_id:str
    author_name:str
    relative_receipt:str
    receipt_sha256:str
    image_sha256:str
    width:int
    height:int
    file_size:int
    thumb_b64:str|None
    source:str='managed'
    filename:str=''
    store_name:str=''
    store_size:int=0
    @property
    def key(self):return f'{self.work_id}:{self.page}'

@dataclass(frozen=True)
class DuplicateGroup:
    group_id:str
    author_id:str
    author_name:str
    items:tuple[DuplicatePage,...]
    edges:tuple[tuple[int,int,int],...]

@dataclass(frozen=True)
class DuplicateScan:
    scan_id:str
    snapshot_sha256:str
    groups:tuple[DuplicateGroup,...]
    scanned_files:int
    unreadable:tuple[str,...]
    skipped_authors:tuple[tuple[str,int],...]

@dataclass(frozen=True)
class QuarantinePlan:
    scan_id:str
    snapshot_sha256:str
    items:tuple[DuplicatePage,...]
    remaining:tuple[tuple[str,int],...]
    mutation_allowed:bool=False
    blocked_reason:str=BLOCKED_REASON
    state_revision:str=''
    groups:tuple[DuplicateGroup,...]=()
    @property
    def bytes_count(self):return sum(p.file_size for p in self.items)
    @property
    def backup_bytes(self):return sum(p.store_size or p.file_size for p in self.items)

def dhash(image,hash_size=8):
    # Same integer comparisons as original numpy int16 array; no model/numpy.
    gray=image.convert('L').resize((hash_size+1,hash_size),Image.Resampling.LANCZOS)
    value=0
    for y in range(hash_size):
        for x in range(hash_size):value=(value<<1)|int(gray.getpixel((x+1,y))>gray.getpixel((x,y)))
    return value

def hamming(a,b):return (a^b).bit_count()

def cluster_edges(n,edges):
    parent=list(range(n))
    def find(x):
        while parent[x]!=x:
            parent[x]=parent[parent[x]];x=parent[x]
        return x
    for a,b,_ in edges:
        aa,bb=find(a),find(b)
        if aa!=bb:parent[aa]=bb
    groups={}
    for index in range(n):groups.setdefault(find(index),[]).append(index)
    return [tuple(items) for items in groups.values() if len(items)>=2]

def best_item_index(items):
    # Original tie rule: first item wins.
    return max(range(len(items)),key=lambda i:(items[i].width*items[i].height,items[i].file_size))

def visible_groups(scan,query='',*,limit=MAX_VISIBLE_GROUPS):
    query=str(query).strip().casefold()
    return tuple(g for g in scan.groups if not query or query in g.author_id.casefold() or query in g.author_name.casefold())[:limit]

def visible_undecidable_groups(groups, query='', *, limit=MAX_VISIBLE_GROUPS):
    """Original gui.py:5122-5136: author name or candidate work ID, max 50."""
    search=str(query or '').strip().casefold()
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError('invalid undecidable display limit')
    if not search:
        return tuple(groups)[:limit]
    result=[]
    for group in groups:
        candidates=group['candidates']
        author=(candidates[0].get('author_name') or '') if candidates else ''
        if search in author.casefold() or any(search in str(c['work_id']).casefold() for c in candidates):
            result.append(group)
            if len(result)==limit:break
    return tuple(result)


def read_undecidable_ledger(db_path):
    """Read the *existing* legacy file_ledger groups, with the original SQL.

    Never create a group by comparing managed receipt titles: file_ledger's
    ambiguous_group and its manual-resolution exclusion are the authority.
    """
    path=Path(db_path)
    if not path.is_absolute() or not path.is_file():
        raise DuplicateInspectionError()
    try:
        from .failed_records_repository import immutable_copy_signature
        before=immutable_copy_signature(path)
        # X02 only accepts the same explicitly frozen failed_queue copy used
        # by managed retries. Opening the old ledger with ordinary mode=ro
        # could create source-side WAL shared-memory files.
        conn=sqlite3.connect(sqlite_readonly_uri(path,immutable=True),uri=True)
        try:
            conn.row_factory=sqlite3.Row
            conn.execute('PRAGMA query_only=ON')
            rows=conn.execute("""
                SELECT fl.work_id AS work_id, fl.page AS page,
                       fl.store_path AS store_path, fl.entry_name AS entry_name,
                       fl.reason AS reason, fl.ambiguous_group AS ambiguous_group,
                       w.title AS title, w.user_id AS user_id,
                       COALESCE(f.name, '') AS author_name
                FROM file_ledger fl
                JOIN works w ON w.work_id = fl.work_id
                LEFT JOIN following_users f ON f.user_id = w.user_id
                WHERE fl.status = 'unsure' AND fl.ambiguous_group IS NOT NULL
                  AND fl.ambiguous_group NOT IN (
                      SELECT DISTINCT ambiguous_group FROM file_ledger
                      WHERE resolved_by = 'manual' AND ambiguous_group IS NOT NULL
                  )
                ORDER BY fl.ambiguous_group, fl.work_id, fl.page
            """).fetchall()
        finally:
            conn.close()
        if immutable_copy_signature(path) != before:
            raise DuplicateInspectionError()
    except (sqlite3.Error, OSError, ValueError) as error:
        raise DuplicateInspectionError() from error

    # Preserve the original database.get_undecidable_groups() grouping and order.
    groups={}
    for row in rows:
        group=groups.setdefault(row['ambiguous_group'],{
            'ambiguous_group':row['ambiguous_group'],
            'reason':row['reason'],
            'candidates':{},
        })
        candidate=group['candidates'].setdefault(row['work_id'],{
            'work_id':row['work_id'],'title':row['title'],
            'author_name':row['author_name'],'user_id':row['user_id'],'pages':[],
        })
        candidate['pages'].append({'page':row['page'],'store_path':row['store_path'],
                                   'entry_name':row['entry_name']})
    return tuple({**group,'candidates':tuple(group['candidates'].values())}
                 for group in groups.values())

def low_quality_keys(group):
    best=best_item_index(group.items)
    return frozenset(item.key for i,item in enumerate(group.items[:MAX_VISIBLE_ITEMS]) if i!=best)

def validate_selection(scan,selections,query=''):
    groups={g.group_id:g for g in visible_groups(scan,query)}
    selected=[];remaining=[]
    for gid,keys in selections.items():
        if not keys:continue
        if gid not in groups:raise DuplicateInspectionError()
        group=groups[gid];keys=frozenset(keys)
        shown={item.key:item for item in group.items[:MAX_VISIBLE_ITEMS]}
        if not keys<=shown.keys() or len(keys)>=len(group.items):raise DuplicateInspectionError()
        selected.extend(shown[k] for k in shown if k in keys)
        remaining.append((gid,len(group.items)-len(keys)))
    return tuple(selected),tuple(remaining)

class ManagedDuplicateInspection:
    def __init__(self,owner,*,undecidable_db=None,legacy=None):
        if not isinstance(owner,ManagedPixivDownloads) and not (owner is None and legacy is not None):raise DuplicateInspectionError()
        self.legacy=legacy
        self.owner=owner;self._lock=RLock();self._closed=False;self._latest=None;self._closing=Event()
        self._planned=None
        self._scan_proofs=()
        self._progress_lock=Lock();self._progress=dict(phase='未開始',done=0,total=0,author_id='')
        self.on_changed=None  # Awaited by the UI after a committed operation.
        # Only an explicitly supplied legacy read-only DB can supply the
        # undecidable mode. The default managed owner has no file_ledger.
        self.undecidable_db=Path(undecidable_db) if undecidable_db is not None else None
        self._rebuilt_undecidable=None
    @property
    def undecidable_available(self):
        return self.undecidable_db is not None
    def undecidable_groups(self):
        with self._lock:
            self._check()
            if self.undecidable_db is None:return None
            if self._rebuilt_undecidable is not None:return self._rebuilt_undecidable
            if self.owner is not None and self.legacy is not None:
                from .legacy_ledger_rebuild import load_rebuilt_ledger
                current=load_rebuilt_ledger(managed_root=self.owner.root,
                    undecidable_db=self.undecidable_db,images_root=self.legacy.catalog.root)
                if current is not None:
                    self._rebuilt_undecidable=current
                    return current
            groups=read_undecidable_ledger(self.undecidable_db)
            self._check()
            return groups
    def _check(self):
        if self._closed or self._closing.is_set() or self.owner is not None and self.owner.closed:raise DuplicateInspectionError()
    def _state(self):
        return self.owner._generations.state_snapshot() if self.owner is not None else {'revision':'','exclusions':{}}
    def progress_snapshot(self):
        with self._progress_lock:return dict(self._progress)
    def _report(self,phase,done=0,total=0,author_id=''):
        self._check()
        with self._progress_lock:self._progress=dict(phase=phase,done=done,total=total,author_id=author_id)
    def _catalog(self,*,progress=False,final=False):
        self._check()
        before=self._state()
        prefix='最終照合: ' if final else ''
        if progress:self._report(prefix+'保存元を確認中（件数集計中）')
        rows=(self.owner.saved_receipts(**({'progress':lambda done,total:self._report(
            prefix+'保存ページを検証中（現在の作品）',done,total)} if progress else {}))
            if self.owner is not None else ())
        if self.legacy is not None:
            rows=tuple(rows)+self.legacy.rows({receipt['work_id'] for _,receipt,_ in rows},
                **({'progress':lambda done,total:self._report(prefix+'旧保存画像を照合中',done,total)} if progress else {}))
        self._check()
        if self._state()['revision']!=before['revision']:
            raise DuplicateInspectionError()
        signature=self._signature(tuple((relative,digest) for relative,_,digest in rows),before['revision'])
        return rows,signature
    @staticmethod
    def _signature(proofs,revision):
        return hashlib.sha256(json.dumps([proofs,revision],separators=(',',':')).encode()).hexdigest()
    def scan(self):
        with self._lock:
            self._report('スキャンを準備中')
            rebuilt=None
            if self.undecidable_db is not None and self.owner is not None and self.legacy is not None:
                from .legacy_ledger_rebuild import rebuild_undecidable_ledger
                rebuilt=rebuild_undecidable_ledger(undecidable_db=self.undecidable_db,
                    images_root=self.legacy.catalog.root,managed_root=self.owner.root,check_cancelled=self._check)
            rows,signature=self._catalog(progress=True);authors=defaultdict(list);unreadable=[];seen=set()
            exclusions=self._state()['exclusions']
            for relative,receipt,digest in rows:
                excluded=exclusions.get(receipt['work_id'],{})
                legacy_excluded=(self.legacy.excluded_pages(receipt['work_id']) if self.legacy is not None else ())
                for page,file in enumerate(receipt['files'],1):
                    if str(page-1) not in excluded and page-1 not in legacy_excluded and not file.get('excluded',False):
                        if file.get('available') is False:
                            unreadable.append(f"{receipt['work_id']}:{page}");continue
                        physical=file.get('physical_id')
                        if physical is not None:
                            if physical in seen:continue
                            seen.add(physical)
                        authors[receipt['author_id']].append((relative,receipt,digest,page,file))
            groups=[];skipped=[];scanned=0
            for aid,targets in authors.items():
                self._check()
                self._report('作者の画像数を確認中',0,len(targets),aid)
                if len(targets)>PER_AUTHOR_FILE_LIMIT:
                    skipped.append((aid,len(targets)));continue
                items=[];hashes=[]
                for ordinal,(relative,receipt,digest,page,file) in enumerate(targets):
                    self._check()
                    self._report('画像の特徴を計算中',ordinal,len(targets),aid)
                    try:
                        reader=self.legacy if receipt.get('legacy') else self.owner
                        raw=reader.read_saved_page(relative,page-1,digest)
                        with Image.open(BytesIO(raw)) as opened:image=opened.convert('RGB')
                        value=dhash(image)
                    except Exception:
                        unreadable.append(f"{receipt['work_id']}:{page}");continue
                    thumb=None
                    try:
                        small=image.copy();small.thumbnail((150,150));stream=BytesIO()
                        small.save(stream,format='JPEG',quality=80);thumb=base64.b64encode(stream.getvalue()).decode('ascii')
                    except Exception:pass  # Original: thumbnail failure retains candidate.
                    items.append(DuplicatePage(receipt['work_id'],page,aid,receipt['author_name'],relative,digest,
                        hashlib.sha256(raw).hexdigest(),image.width,image.height,len(raw),thumb,
                        'legacy' if receipt.get('legacy') else 'managed',
                        file.get('filename') or Path(file.get('name',file.get('path',''))).name,
                        file.get('store_name',''),file.get('store_size',0)));hashes.append(value)
                scanned+=len(items)
                edges=[]
                for i in range(len(items)):
                    self._check()
                    self._report('画像を比較中',i*(2*len(items)-i-1)//2,len(items)*(len(items)-1)//2,aid)
                    for j in range(i+1,len(items)):
                        distance=hamming(hashes[i],hashes[j])
                        if distance<=THRESHOLD:edges.append((i,j,distance))
                for indexes in cluster_edges(len(items),edges):
                    mapping={old:new for new,old in enumerate(indexes)}
                    members=tuple(items[i] for i in indexes)
                    gedges=tuple((mapping[a],mapping[b],d) for a,b,d in edges if a in mapping and b in mapping)
                    key=hashlib.sha256(json.dumps([(p.relative_receipt,p.page,p.receipt_sha256) for p in members]).encode()).hexdigest()
                    groups.append(DuplicateGroup(key,aid,members[0].author_name,members,gedges))
            if self._catalog(progress=True,final=True)[1]!=signature:raise DuplicateInspectionError()
            result=DuplicateScan(uuid4().hex,signature,tuple(groups),scanned,tuple(unreadable),tuple(skipped))
            if rebuilt is not None:
                from .legacy_ledger_rebuild import publish_rebuilt_ledger
                publish_rebuilt_ledger(rebuilt,managed_root=self.owner.root,
                    undecidable_db=self.undecidable_db,images_root=self.legacy.catalog.root)
                self._rebuilt_undecidable=rebuilt.groups
            self._latest=result
            self._planned=None
            self._scan_proofs=tuple((relative,digest) for relative,_,digest in rows)
            self._report('スキャン完了',scanned,scanned)
            return result
    def plan(self,scan,selections,query=''):
        with self._lock:
            if self._closed or scan is not self._latest:raise DuplicateInspectionError()
            items,remaining=validate_selection(scan,selections,query)
            if not items:raise DuplicateInspectionError()
            state=self._state()
            legacy_revision=(self.legacy.state(verify_outputs=False)['revision'] if self.legacy is not None else '')
            if self._catalog()[1]!=scan.snapshot_sha256:raise DuplicateInspectionError()
            if self._state()['revision']!=state['revision']:
                raise DuplicateInspectionError()
            if self.legacy is not None and self.legacy.state(verify_outputs=False)['revision']!=legacy_revision:
                raise DuplicateInspectionError()
            selected_groups={gid for gid,_ in remaining}
            origins={item.source for item in items}
            # Separate transactions have separate durable state pointers.
            # Never pretend a mixed publication is atomic.
            allowed=self.owner is not None and len(origins)==1
            reason=('旧画像と管理画像は分けて隔離してください。' if len(origins)>1 else
                    '隔離には専用の管理保存先が必要です。') if not allowed else ''
            plan=QuarantinePlan(
                scan.scan_id,scan.snapshot_sha256,items,remaining,
                mutation_allowed=allowed,blocked_reason=reason,
                state_revision=(state['revision']+':'+legacy_revision
                                if origins=={'legacy'} else state['revision']),
                groups=tuple(group for group in scan.groups if group.group_id in selected_groups))
            self._planned=plan
            return plan
    def _finish_trash_purges(self):
        """Complete deletions already committed before an interruption (idempotent)."""
        for store in (self.owner.quarantine if self.owner is not None else None,self.legacy):
            if store is None:continue
            try:store.finish_purges()
            except Exception:pass  # never block reading the trash; retried next time

    def empty_trash(self,*,expected_count,confirmed):
        """Original gui.py:5640-5669: permanently delete the trash after a typed count.

        Only copies are destroyed; exclusion records stay (they prevent re-acquisition).
        The managed and legacy stores are separate durable transactions, so each
        re-derives its own eligible set under its writer fence and refuses on mismatch.
        """
        with self._lock:
            self._check()
            check_trash_confirmation(expected_count,confirmed)
            self._finish_trash_purges()
            managed=self.owner.quarantine.snapshot().purgeable_count if self.owner is not None else 0
            old=self.legacy.snapshot().purgeable_count if self.legacy is not None else 0
            if managed+old!=expected_count:raise DuplicateInspectionError()
            removed=failed=0
            try:
                # The two stores are separate durable transactions. A failure in one
                # must not hide what the other already destroyed: report both counts
                # (failed = everything not removed); a rerun converges on the rest.
                if managed:
                    try:
                        part=self.owner.quarantine.purge(expected_count=managed,confirmed=True)
                        removed+=part.removed;failed+=part.failed
                    except Exception:failed+=managed
                if old:
                    try:
                        part=self.legacy.purge(expected_count=old,confirmed=True)
                        removed+=part.removed;failed+=part.failed
                    except Exception:failed+=old
            finally:
                # Candidate bindings include the changed state revisions.
                self._latest=None;self._planned=None
            return TrashEmptyResult(removed,failed)

    def quarantine_snapshot(self):
        with self._lock:
            result=self._quarantine_snapshot()
            # After the summary: purged operations are never counted, so finishing
            # their pending deletions cannot change what was just read.
            self._finish_trash_purges()
            return result

    def _quarantine_snapshot(self):
        with self._lock:
            self._check()
            if self.owner is None:return self.legacy.snapshot()
            current=self.owner.quarantine.snapshot()
            if self.legacy is None:return current
            old=self.legacy.snapshot()
            if self.owner._generations.state_snapshot(verify_quarantine_payloads=False,tolerate_missing=True)['revision']!=current.state_revision:
                raise DuplicateInspectionError('quarantine snapshot changed')
            # Totals span both stores; existence must do the same. A legacy
            # snapshot is also 'ready' before its directory exists, so use its
            # validated location rather than that state or a nonzero count.
            return replace(current,
                           state='ready' if current.state=='ready' or old.location is not None else 'uncreated',
                           copy_count=current.copy_count+old.copy_count,
                           missing_count=current.missing_count+old.missing_count,
                           corrupt_count=current.corrupt_count+old.corrupt_count,
                           unconfirmed_count=current.unconfirmed_count+old.unconfirmed_count,
                           copy_bytes=current.copy_bytes+old.copy_bytes,
                           purgeable_count=current.purgeable_count+old.purgeable_count,
                           purgeable_bytes=current.purgeable_bytes+old.purgeable_bytes,
                           excluded_pages=current.excluded_pages+old.excluded_pages,
                           location=self.owner.root)

    def quarantine_location(self):
        with self._lock:
            self._check()
            if self.owner is None:return None
            return self.owner.root if self.legacy is not None else self.owner.quarantine.location()

    def quarantine_parent_if_uncreated(self):
        with self._lock:
            self._check()
            if self.owner is None or self.quarantine_snapshot().state != 'uncreated':
                return None
            self.owner.quarantine._check()
            return self.owner.root

    def recoverable_moves(self):
        """UI-safe list of owned v4 move operations; no original paths."""
        with self._lock:
            self._check()
            if self.owner is None:return ()
            state = self.owner._generations.state_snapshot(verify_quarantine_payloads=False,tolerate_missing=True)
            results = []
            for operation_id, operation in state['operations'].items():
                if operation_id in state['unconfirmed_operations']:continue  # reported by the summary
                if (operation.get('mode') != 'move'
                        or operation['state'] not in ('relocating', 'moved', 'restoring')):
                    continue
                manifest = self.owner.quarantine._manifest(operation_id, operation)
                results.append((operation_id, operation['state'],
                                len(manifest['items']), state['revision']))
            return tuple(results)

    def restore_move(self, operation_id, *, expected_revision):
        """Restore only a confirmed managed move, maintaining scan lifetime."""
        with self._lock:
            self._check()
            state = self.owner._generations.state_snapshot()
            operation = state['operations'].get(operation_id)
            if operation is None or operation.get('mode') != 'move':
                raise DuplicateInspectionError()
            result = self.owner.quarantine.restore(
                operation_id, expected_revision=expected_revision)
            self._latest = None
            self._planned = None
            return result

    @property
    def latest_scan(self):
        # Immutable snapshots are published by one pointer assignment. Reading
        # that pointer must not block the UI on a copy running under _lock.
        return self._latest
    def execute(self,plan,*,operation_id,mode='copy',confirmed_move=False):
        with self._lock:
            self._check()
            if plan is not self._planned:raise DuplicateInspectionError()
            if not plan.mutation_allowed:raise DuplicateInspectionError()
            if self.legacy is not None:
                physical=all(item.source=='legacy' for item in plan.items) and mode=='move' and confirmed_move
                # Physical admission rechecks every survivor under the image
                # fence and reports missing sources as failed/unprocessed.
                if not physical and self._catalog()[1]!=plan.snapshot_sha256:raise DuplicateInspectionError()
                if all(item.source=='legacy' for item in plan.items):
                    if mode=='move' and confirmed_move:
                        result=self.legacy.execute_physical(plan,operation_id=operation_id,confirmed=True,
                            validate_snapshot=lambda:self._catalog()[1]==plan.snapshot_sha256)
                    elif mode=='copy' and not confirmed_move:
                        result=self.legacy.execute(plan,operation_id=operation_id)
                    else:raise DuplicateInspectionError()
                    # Refresh receipt/store identity bindings; do not decode
                    # images or repeat pairwise similarity comparisons.
                    try:
                        rows,signature=self._catalog()
                        proofs={relative:digest for relative,_,digest in rows}
                        self._scan_proofs=tuple(proofs.items())
                    except Exception:
                        # Keep known per-item results and candidates visible.
                        # A blank binding prevents a new admission until recovery.
                        proofs=dict(self._scan_proofs);signature=''
                        result=replace(result,recovery_required=True)
                    removed={p.key for p in result.committed};groups=[]
                    retryable=(getattr(self,'_legacy_retryable',set()) |
                        {p.key for p in (*result.failed,*result.unprocessed)})-removed
                    self._legacy_retryable=retryable
                    for group in self._latest.groups:
                        edges=tuple((a,b,d) for a,b,d in group.edges
                            if group.items[a].key not in removed and group.items[b].key not in removed)
                        remaining=tuple(i for i,p in enumerate(group.items) if p.key not in removed)
                        # A successful bridge move can isolate a failed item.
                        # Keep the reviewed group's remaining images together
                        # for explicit retry, without inventing similarity edges
                        # or relaxing the requirement to leave one image.
                        clusters=([remaining] if len(remaining)>=2
                            and any(p.key in retryable for p in group.items)
                            else cluster_edges(len(group.items),edges))
                        for indexes in clusters:
                            mapping={old:new for new,old in enumerate(indexes)}
                            members=tuple(replace(group.items[i],receipt_sha256=proofs[group.items[i].relative_receipt]) for i in indexes)
                            kept=tuple((mapping[a],mapping[b],d) for a,b,d in edges if a in mapping and b in mapping)
                            gid=hashlib.sha256(json.dumps([(p.relative_receipt,p.page,p.receipt_sha256) for p in members]).encode()).hexdigest()
                            groups.append(replace(group,group_id=gid,items=members,edges=kept))
                    self._latest=replace(self._latest,scan_id=uuid4().hex,groups=tuple(groups),
                                         scanned_files=max(0,self._latest.scanned_files-len(result.committed)),snapshot_sha256=signature)
                    self._planned=None
                    return result
            result=self.owner.quarantine.execute(
                plan,operation_id=operation_id,mode=mode,confirmed_move=confirmed_move)
            if self._latest is not None and self._latest.scan_id==plan.scan_id and result.committed:
                removed={item.key for item in result.committed}
                groups=[]
                for group in self._latest.groups:
                    remaining={index for index,item in enumerate(group.items)
                               if item.key not in removed}
                    retained_edges=tuple((a,b,distance) for a,b,distance in group.edges
                                         if a in remaining and b in remaining)
                    # Removing a bridge can split a valid near-duplicate group.
                    # Reuse the measured edges; isolated survivors are no longer
                    # duplicate candidates and need no image reload or rehash.
                    for indexes in cluster_edges(len(group.items),retained_edges):
                        mapping={old:new for new,old in enumerate(indexes)}
                        members=tuple(group.items[index] for index in indexes)
                        edges=tuple((mapping[a],mapping[b],distance)
                                    for a,b,distance in retained_edges if a in mapping and b in mapping)
                        gid=hashlib.sha256(json.dumps([
                            (item.relative_receipt,item.page,item.receipt_sha256)
                            for item in members]).encode()).hexdigest()
                        groups.append(replace(group,group_id=gid,items=members,edges=edges))
                self._latest=replace(
                    self._latest,scan_id=uuid4().hex,groups=tuple(groups),
                    snapshot_sha256=self._signature(self._scan_proofs,result.state_revision),
                    scanned_files=max(0,self._latest.scanned_files-len(result.committed)))
            return result
    def recover_legacy(self):
        """Settle an already authorized intent; never start another move."""
        with self._lock:
            self._check()
            if self.legacy is None or self.legacy._physical is None:raise DuplicateInspectionError()
            self.legacy._physical.recover(strict=True)
            self.legacy.catalog.replace_snapshot(self.legacy.original_snapshot)
            rows,signature=self._catalog()
            proofs={relative:digest for relative,_,digest in rows}
            if self._latest is not None:
                groups=tuple(replace(g,items=tuple(replace(p,receipt_sha256=proofs[p.relative_receipt])
                    for p in g.items)) for g in self._latest.groups)
                self._latest=replace(self._latest,scan_id=uuid4().hex,groups=groups,snapshot_sha256=signature)
            self._scan_proofs=tuple(proofs.items());self._planned=None
            return self._latest
    def close(self):
        self._closing.set()
        with self._lock:
            self._closed=True;self._latest=None;self._planned=None
            if self.legacy is not None:self.legacy.close()
