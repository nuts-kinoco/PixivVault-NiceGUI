"""Legacy scan, retained copy generations and explicit original relocation.

Scanning and copy-mode operations retain their read-only input contract.
Only a confirmed physical operation delegates source changes to the journaled
relocation helper, preserving complete before-generations and exclusions.
"""
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from threading import RLock
from uuid import uuid4
from zipfile import ZipFile
from PIL import Image
from .fs_paths import native_io_path
from .local_media_adapter import ReadonlyLocalMediaCatalog, MediaUnavailable, MediaMissing, _safe_entry
from .managed_downloads import _safe_path, _write, _read_bounded, _json_unique, _fsync_dir, MARKER, IDENTITY
from .managed_quarantine import QuarantineResult, QuarantineSnapshot
from .library_models import LibrarySnapshot
from .media import ImageBlob
from .managed_root_writer import fenced_writer, writer_fence

LIMIT=8*1024*1024
STORE_LIMIT=2*1024*1024*1024
_OP=re.compile(r'[a-f0-9]{32}\Z')
_SHA=re.compile(r'[a-f0-9]{64}\Z')


def packet(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()


def digest_file(path):
    value=sha256()
    with native_io_path(path).open('rb') as stream:
        while block:=stream.read(1024*1024):value.update(block)
    return value.hexdigest()


class LegacyQuarantine:
    def __init__(self,image_root,snapshot,*,owner=None,source_guard=lambda:None,maintenance=None):
        self.catalog=ReadonlyLocalMediaCatalog(image_root,snapshot)
        self.catalog._cache_limit=0
        self.original_snapshot=snapshot;self.owner=owner;self.source_guard=source_guard
        self.root=owner.root/'legacy-quarantine' if owner is not None else None
        if self.root is not None and (self.root.is_relative_to(self.catalog.root)
                                      or self.catalog.root.is_relative_to(self.root)):
            raise ValueError('legacy and managed storage overlap')
        self.identity={'version':1,'purpose':'legacy-quarantine',
                       'source':sha256(str(self.catalog.root).casefold().encode()).hexdigest()}
        self._mutex=RLock();self._closed=False;self._rows={}
        self._physical=None
        self._recovery_page_bindings={}
        if owner is not None:
            from .legacy_physical_quarantine import PhysicalLegacyQuarantine
            self._physical=PhysicalLegacyQuarantine(self)
        if owner is not None:owner.legacy_quarantine=self
        self._maintenance_worker=None
        if maintenance is not None and owner is not None and maintenance.allows(owner.root):
            from .completed_maintenance import BackgroundCollection,collect_zip_duplicates
            self._maintenance_worker=BackgroundCollection(lambda stop:collect_zip_duplicates(self._physical,maintenance,stop))

    @property
    def _lock(self):return self._guard()

    @contextmanager
    def _guard(self):
        # Readers may recover an interrupted write. All paths acquire the same
        # OS fences before the local mutex; never wait on a writer while holding it.
        if self.owner is None:
            with self._mutex:yield
        else:
            with writer_fence(self.owner.root),writer_fence(self.catalog.root),self._mutex:yield

    def _check(self):
        if self._closed or self.owner is not None and self.owner.closed:raise ValueError('legacy inspection closed')
        self.source_guard()
        if self.owner is not None:
            if json.loads(_read_bounded(self.owner.root/MARKER,4096),object_pairs_hook=_json_unique)!=IDENTITY:
                raise ValueError('legacy quarantine owner rejected')

    def _path(self,value):
        if (type(value) is not str or not _safe_entry(value)
                or PurePosixPath(value).parts[0]!='operations'):
            raise ValueError('legacy quarantine path rejected')
        path=self.root.joinpath(*PurePosixPath(value).parts)
        _safe_path(path,file=True)
        return native_io_path(path)

    def state(self,*,verify_outputs=True):
        with self._lock:
            self._check()
            if self._physical is not None:self._physical.recover()
            empty={'version':1,'revision':'','exclusions':{},'archives':{},'operations':{}}
            if self.root is None:return empty
            _safe_path(self.root)
            root=native_io_path(self.root)
            if not root.exists():return empty
            marker=root/'IDENTITY.json'
            if not marker.exists() and not tuple(root.iterdir()):return empty
            if json.loads(_read_bounded(marker,4096),object_pairs_hook=_json_unique)!=self.identity:
                raise ValueError('legacy quarantine marker rejected')
            current=root/'CURRENT'
            if not current.exists():return empty
            ref=json.loads(_read_bounded(current,4096),object_pairs_hook=_json_unique)
            raw=_read_bounded(self._path(ref['path']),LIMIT)
            if sha256(raw).hexdigest()!=ref['sha256']:raise ValueError('legacy state changed')
            result=json.loads(raw,object_pairs_hook=_json_unique)
            if (result['version']!=1 or not _OP.fullmatch(result['revision'])
                    or any(type(result[k]) is not dict for k in ('exclusions','archives','operations'))):
                raise ValueError('legacy state rejected')
            for key,item in result['exclusions'].items():
                if (key!=f"{item['work_id']}:{item['page']}" or type(item['page']) is not int
                        or not 1<=item['page']<=1000 or not _SHA.fullmatch(item['sha256'])
                        or type(item['size']) is not int or not 0<item['size']<=24*1024*1024):
                    raise ValueError('legacy exclusion rejected')
                if verify_outputs and sha256(_read_bounded(self._path(item['copy']),24*1024*1024)).hexdigest()!=item['sha256']:
                    raise ValueError('legacy quarantine evidence changed')
            for relative,item in result['archives'].items():
                if not _safe_entry(relative) or not _SHA.fullmatch(item['source_sha256']):
                    raise ValueError('legacy archive identity rejected')
                if verify_outputs and digest_file(self._path(item['path']))!=item['sha256']:
                    raise ValueError('legacy active archive changed')
            return result

    def _ensure(self):
        self._check()
        if self.root is None:raise ValueError('managed quarantine root required')
        self.state()
        root=native_io_path(self.root);root.mkdir(exist_ok=True);_safe_path(self.root)
        if not (root/'IDENTITY.json').exists():
            # A partial marker must not poison an otherwise empty new root.
            temporary=native_io_path(self.owner.root/('legacy-identity-'+uuid4().hex+'.next'))
            _write(temporary,packet(self.identity));os.rename(temporary,root/'IDENTITY.json');_fsync_dir(root)
        (root/'operations').mkdir(exist_ok=True);_safe_path(root/'operations')

    def rows(self,exclude=(),*,progress=None):
        """Receipt-shaped private scan inputs; no source file is ever modified."""
        with self._lock:
            state=self.state();self.catalog._ensure_index()
            rows=[];hashes={};self._rows={}
            total=sum(work.page_count for work in self.original_snapshot.works
                      if work.work_id not in exclude and work.work_type not in ('novel','ugoira') and work.page_count_known)
            done=0
            if progress is not None:progress(done,total)
            for work in self.original_snapshot.works:
                if work.work_id in exclude or work.work_type in ('novel','ugoira') or not work.page_count_known:continue
                identities=[];files=[]
                for page in range(work.page_count):
                    done+=1
                    if progress is not None:progress(done,total)
                    key=f'{work.work_id}:{page+1}'
                    if key in state['exclusions']:
                        identities.append(None);files.append({'sha256':'','excluded':True});continue
                    try:
                        self.require_available(work.work_id,page)
                        identity=self.catalog.analysis_source_identity(work.work_id,page)
                        path=Path(identity[0])/identity[1]
                        if path not in hashes:
                            with self.catalog._checked_open(path):
                                hashes[path]=digest_file(path)
                        identities.append((identity,hashes[path]))
                        entry=identity[-1][0] if identity[-1] is not None else None
                        files.append({'sha256':'','excluded':False,'available':True,
                            'filename':PurePosixPath(entry).name if entry else path.name,
                            'store_name':path.name,'store_size':identity[4],
                            'physical_id':sha256(packet([str(path),entry])).hexdigest()})
                    except (OSError,ValueError,RuntimeError):
                        identities.append(None)
                        files.append({'sha256':'','excluded':False,'available':False})
                token=sha256(packet([work.work_id,identities,state['revision']])).hexdigest()
                receipt={'work_id':work.work_id,'author_id':work.author_id,
                         'author_name':work.display_author_name,'files':files,'legacy':True}
                relative='legacy:'+work.work_id
                rows.append((relative,receipt,token));self._rows[relative]=(token,identities)
            self._check()
            return tuple(rows)

    def read_saved_page(self,relative,page,digest):
        token,identities=self._rows[relative]
        if token!=digest or identities[page] is None:raise MediaUnavailable('legacy scan source changed')
        self.require_available(relative.removeprefix('legacy:'),page)
        return self.catalog.get(relative.removeprefix('legacy:'),page,'original').data

    def _checkpoint(self,stage):
        """Finite publication boundaries used by deterministic interruption tests."""

    def _publish_state(self,updated,stage):
        raw=packet(updated)
        if len(raw)>LIMIT:raise ValueError('legacy quarantine state limit')
        _write(stage/'state.json',raw)
        ref=packet({'path':f"operations/{updated['revision']}/state.json",'sha256':sha256(raw).hexdigest()})
        _write(stage/'CURRENT.next',ref);_fsync_dir(stage)
        os.replace(stage/'CURRENT.next',native_io_path(self.root/'CURRENT'))
        _fsync_dir(native_io_path(self.root))

    def execute_physical(self,plan,*,operation_id,confirmed=False,validate_snapshot=None):
        if (confirmed is not True or self._physical is None or not _OP.fullmatch(operation_id)
                or not plan.mutation_allowed or not plan.items or any(p.source!='legacy' for p in plan.items)):
            raise ValueError('explicit physical confirmation required')
        return self._physical.execute(plan,operation_id,validate_snapshot=validate_snapshot)

    def recovery_status(self):
        physical=self._physical
        return {'blocked_sources':tuple(sorted(physical.blocked_sources)) if physical else (),
                'warnings':tuple(sorted(physical.cleanup_warnings)) if physical else ()}

    def recovery_notice(self):
        status=self.recovery_status()
        # File names are text only; no absolute paths or exception contents.
        def names(values):return '、'.join(sorted({PurePosixPath(v).name for v in values}))
        lines=[]
        if status['blocked_sources']:
            lines.append('復旧が必要: '+names(status['blocked_sources'])+'（この保存元のみ利用を停止しています）')
        capacity=getattr(self._physical,'capacity_blocked_sources',())
        if capacity:
            lines.append('復旧用の空き容量が不足または確認できません: '+names(capacity)+'（退避データは保全しています）')
        if status['warnings']:
            lines.append('確定済みの退避操作に後始末の警告: '+names(status['warnings'])+'（読み取りは継続できます）')
        return '。'.join(lines)

    def _recovery_pages(self,intent):
        key=(intent['unit'],intent['before_sha256'])
        if key in self._recovery_page_bindings:return self._recovery_page_bindings[key]
        if intent['kind']=='file':return frozenset((intent['key'],))
        before=self._physical._unit(intent['unit'])/'before'
        if digest_file(before)!=intent['before_sha256']:
            raise MediaUnavailable('legacy recovery binding changed')
        selected=self.catalog.works.get(intent['key'].rsplit(':',1)[0])
        if selected is None:raise MediaUnavailable('legacy recovery work unavailable')
        source=self.catalog.root.joinpath(*PurePosixPath(intent['source']).parts)
        # Reuse the original filename/author/page resolver against the verified
        # before-generation. No current index or original ZIP is required.
        class BeforeCatalog(ReadonlyLocalMediaCatalog):
            def _discover_stores(inner):
                return {selected.author_id:(source,)},()
            def _checked_open(inner,path):
                if path!=source:raise MediaUnavailable('legacy recovery source rejected')
                _safe_path(before,file=True)
                return native_io_path(before).open('rb')
        catalog=BeforeCatalog(self.catalog.root,self.original_snapshot)
        try:
            catalog._ensure_index()
            if catalog._failed_authors:raise MediaUnavailable('legacy recovery binding unavailable')
            pages=frozenset(f'{wid}:{page+1}' for wid,page in catalog._locations)
            if intent['key'] not in pages:raise MediaUnavailable('legacy recovery selection changed')
            if digest_file(before)!=intent['before_sha256']:
                raise MediaUnavailable('legacy recovery binding changed')
        finally:catalog.close()
        self._recovery_page_bindings[key]=pages
        return pages

    def require_available(self,work_id,page=None):
        if self._physical is None:return
        blocked=self._physical.blocked_sources
        if not blocked:return
        requested=self.catalog.works.get(work_id)
        if requested is not None:
            # A fresh checked handle/member identity can establish that this
            # request belongs to another store without reading any backup.
            resolved=True
            for number in (range(requested.page_count) if page is None else (page,)):
                try:
                    identity=self.catalog.analysis_source_identity(work_id,number)
                    relative=(Path(identity[0])/identity[1]).relative_to(self.catalog.root).as_posix()
                except (OSError,ValueError,RuntimeError):
                    resolved=False
                    continue
                if relative in blocked:
                    raise MediaUnavailable('legacy source recovery required: '+PurePosixPath(relative).name)
            if resolved:return
        for journal in native_io_path(self._physical.journals).glob('*/intent.json'):
            intent,_=self._physical._read_intent(journal,verify_before=False)
            if intent['source'] not in blocked:continue
            selected=self.catalog.works.get(intent['key'].rsplit(':',1)[0])
            requested=self.catalog.works.get(work_id)
            if selected is not None and requested is not None and selected.author_id!=requested.author_id:
                continue
            try:pages=self._recovery_pages(intent)
            except (OSError,ValueError,RuntimeError):
                raise MediaUnavailable('legacy recovery binding unavailable: '+PurePosixPath(intent['source']).name) from None
            if (f'{work_id}:{page+1}' in pages if page is not None
                    else any(key.rsplit(':',1)[0]==work_id for key in pages)):
                raise MediaUnavailable('legacy source recovery required: '+PurePosixPath(intent['source']).name)

    def excluded_pages(self,work_id):
        state=self.state(verify_outputs=False)
        self.require_available(work_id)
        return frozenset(item['page']-1 for item in state['exclusions'].values()
                         if item['work_id']==work_id)

    def download_state(self,record):
        state=self.state(verify_outputs=False)
        self.require_available(record.work_id)
        items={key:item for key,item in state['exclusions'].items() if item['work_id']==record.work_id}
        if items:
            work=self.catalog.works.get(record.work_id)
            if work is None or work.author_id!=record.author_id or work.page_count!=record.page_count:
                raise ValueError('legacy quarantine identity changed')
        return (frozenset(item['page']-1 for item in items.values()),sha256(packet(items)).hexdigest())

    def download_proof(self,record,page):
        self.download_state(record)
        item=self.state(verify_outputs=False)['exclusions'].get(f'{record.work_id}:{page+1}')
        if item is None:raise ValueError('legacy quarantine proof unavailable')
        raw=_read_bounded(self._path(item['copy']),24*1024*1024)
        if len(raw)!=item['size'] or sha256(raw).hexdigest()!=item['sha256']:
            raise ValueError('legacy quarantine proof changed')
        return raw

    @fenced_writer
    def execute(self,plan,*,operation_id):
        with self._lock:
            self._check()
            if self.root is None or not _OP.fullmatch(operation_id):raise ValueError('legacy operation rejected')
            state=self.state()
            revision=self.owner._generations.state_snapshot()['revision']+':'+state['revision']
            if (not plan.mutation_allowed or not plan.items or plan.state_revision!=revision
                    or any(item.source!='legacy' for item in plan.items)):
                raise ValueError('legacy quarantine selection changed')
            if operation_id in state['operations']:raise ValueError('legacy operation already published; rescan')
            self._ensure()
            stage=native_io_path(self.root/'operations'/operation_id)
            # An interrupted unpublished attempt is preserved and never replayed.
            stage.mkdir();_safe_path(stage)
            updated=json.loads(packet(state))
            touched=defaultdict(set);source_hashes={}
            for item in plan.items:
                identity=self.catalog.analysis_source_identity(item.work_id,item.page-1)
                path=Path(identity[0])/identity[1];member=identity[-1][0] if identity[-1] is not None else None
                relative=path.relative_to(self.catalog.root).as_posix()
                with self.catalog._checked_open(path):
                    current=digest_file(path)
                source_hashes[relative]=current
                previous=state['archives'].get(relative)
                if previous is not None and previous['source_sha256']!=current:
                    raise ValueError('legacy archive source changed')
                raw=self.catalog.get(item.work_id,item.page-1,'original').data
                if sha256(raw).hexdigest()!=item.image_sha256 or len(raw)!=item.file_size:
                    raise ValueError('legacy image changed')
                name=sha256(item.key.encode()).hexdigest()+'.image'
                _write(stage/name,raw)
                updated['exclusions'][item.key]={
                    'work_id':item.work_id,'page':item.page,'sha256':item.image_sha256,'size':len(raw),
                    'copy':f'operations/{operation_id}/{name}','store':relative,'member':member}
                if member is not None:touched[relative].add(member)
            self._checkpoint('quarantine')
            for relative in touched:
                path=self.catalog.root.joinpath(*PurePosixPath(relative).parts)
                removed={v['member'] for v in updated['exclusions'].values() if v['store']==relative}
                name=sha256(relative.encode()).hexdigest()+'.zip';output=stage/name
                with self.catalog._checked_open(path) as stream, ZipFile(stream) as source:
                    infos=source.infolist()
                    if len(infos)>100000 or len({i.filename for i in infos})!=len(infos):
                        raise ValueError('legacy archive members rejected')
                    total=0
                    with output.open('xb') as target:
                        with ZipFile(target,'w',allowZip64=True) as rebuilt:
                            rebuilt.comment=source.comment
                            for info in infos:
                                total+=info.file_size
                                if (not _safe_entry(info.filename) or info.flag_bits&1
                                        or stat.S_IFMT(info.external_attr>>16)==stat.S_IFLNK
                                        or total>STORE_LIMIT or info.file_size>24*1024*1024
                                        or info.file_size and not info.compress_size
                                        or info.compress_size and info.file_size/info.compress_size>200):
                                    raise ValueError('unsafe legacy archive')
                                # Read removed members as well, verifying their CRC.
                                data=source.read(info)
                                if info.filename not in removed:rebuilt.writestr(info,data)
                        target.flush();os.fsync(target.fileno())
                updated['archives'][relative]={'source_sha256':source_hashes[relative],
                    'path':f'operations/{operation_id}/{name}','sha256':digest_file(output)}
            self._checkpoint('archive')
            # Full source hashes, not timestamps, guard the scan/commit boundary.
            for relative,expected in source_hashes.items():
                path=self.catalog.root.joinpath(*PurePosixPath(relative).parts)
                with self.catalog._checked_open(path):
                    if digest_file(path)!=expected:raise ValueError('legacy source changed')
            self._check()
            updated['revision']=operation_id
            updated['operations'][operation_id]=[item.key for item in plan.items]
            raw=packet(updated)
            if len(raw)>LIMIT:raise ValueError('legacy quarantine state limit')
            _write(stage/'state.json',raw);self._checkpoint('state')
            ref=packet({'path':f'operations/{operation_id}/state.json','sha256':sha256(raw).hexdigest()})
            _write(stage/'CURRENT.next',ref);self._checkpoint('before_current')
            _fsync_dir(stage)
            os.replace(stage/'CURRENT.next',native_io_path(self.root/'CURRENT'))
            _fsync_dir(native_io_path(self.root))
            self._checkpoint('after_current')
            return QuarantineResult(operation_id,plan.items,(),operation_id)

    def active_archive(self,path,state=None):
        state=self.state() if state is None else state
        relative=Path(path).relative_to(self.catalog.root).as_posix()
        if self._physical is not None and relative in self._physical.blocked_sources:
            raise MediaUnavailable('復旧が必要: '+PurePosixPath(relative).name)
        entry=state['archives'].get(relative)
        if entry is None:return Path(path)
        with self.catalog._checked_open(Path(path)):
            if digest_file(path)!=entry['source_sha256']:raise MediaUnavailable('legacy archive source changed')
        active=self._path(entry['path'])
        if digest_file(active)!=entry['sha256']:raise MediaUnavailable('legacy active archive changed')
        return active

    def snapshot(self):
        # Summary reads validate the durable index, then inspect each copy.
        # Missing payloads do not erase exclusions or hide healthy copies.
        state=self.state(verify_outputs=False);items=state['exclusions'].values()
        count=total=missing=0
        for item in items:
            try:
                raw=_read_bounded(self._path(item['copy']),24*1024*1024)
            except FileNotFoundError:
                missing+=1
                continue
            if len(raw)!=item['size'] or sha256(raw).hexdigest()!=item['sha256']:
                raise ValueError('legacy quarantine evidence changed')
            count+=1;total+=len(raw)
        return QuarantineSnapshot('ready',count,total,len(items),
                                  self.root if self.root and native_io_path(self.root).exists() else None,
                                  state['revision'],missing)

    def project(self,snapshot):
        state=self.state();works=[];mapping={}
        for work in snapshot.works:
            pages=tuple(p for p in range(work.page_count) if f'{work.work_id}:{p+1}' not in state['exclusions'])
            mapping[work.work_id]=pages
            if pages:
                works.append(replace(work,artwork=replace(work.artwork,page_count=len(pages)),
                                     capacity_bytes=None) if len(pages)!=work.page_count else work)
        visible=LibrarySnapshot(tuple(works),author_names=snapshot.author_names)
        return visible,LegacyQuarantineCatalog(self,snapshot,visible,mapping,state)

    def close(self):
        if self._maintenance_worker is not None:self._maintenance_worker.close()
        with self._lock:
            self._closed=True;self.catalog.close()


class LegacyQuarantineCatalog:
    """A pinned visible-to-original map; no fallback to a quarantined page."""
    def __init__(self,store,original,visible,mapping,state):
        self.store=store;self.leaf=ReadonlyLocalMediaCatalog(store.catalog.root,original)
        self.works={w.work_id:w for w in visible.works};self.source_pages=mapping;self.state=state
    def _page(self,wid,page):
        if wid not in self.works or type(page) is not int or not 0<=page<len(self.source_pages[wid]):
            raise MediaMissing('legacy page excluded')
        original=self.source_pages[wid][page]
        # Old viewer pins may retain the old visible map after publication.
        if f'{wid}:{original+1}' in self.store.state(verify_outputs=False)['exclusions']:
            raise MediaMissing('legacy page excluded')
        self.store.require_available(wid,original)
        return original
    def url(self,wid,page_no=0,variant='view'):
        self._page(wid,page_no)
        if variant not in ('thumb','view','original'):raise MediaMissing('invalid variant')
        return f'/api/images/{wid}/{page_no}?variant={variant}'
    def supports_original(self,wid):return wid in self.works and self.leaf.supports_original(wid)
    def get(self,wid,page_no=0,variant='view'):
        page=self._page(wid,page_no);self.leaf._ensure_index()
        path,member=self.leaf._source(wid,page,variant)
        active=self.store.active_archive(path,self.state) if member is not None else path
        if active==path:return self.leaf.get(wid,page,variant)
        _safe_path(active,file=True)
        with ZipFile(active) as z:raw=z.read(member)
        from .image_original import verified_original_blob
        blob=verified_original_blob(raw)
        if variant=='original':return blob
        with Image.open(BytesIO(raw)) as image:
            image.thumbnail((192,192) if variant=='thumb' else (640,640))
            out=BytesIO();image.convert('RGB').save(out,format='PNG')
        data=out.getvalue()
        return ImageBlob(data,'image/png','"'+sha256(data).hexdigest()+'"')
    def external_target(self,wid,page_no=0):
        page=self._page(wid,page_no);path=self.leaf.external_target(wid,page)
        return self.store.active_archive(path,self.state) if path.suffix.lower()=='.zip' else path
    def configure_cache(self,cache):self.leaf.configure_cache(cache)
    def peek(self,*args):return None
    def close(self):self.leaf.close()
