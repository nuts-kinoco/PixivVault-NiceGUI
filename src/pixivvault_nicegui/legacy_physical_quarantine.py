"""Explicit original-folder relocation and recoverable in-place ZIP replacement.

Every old byte remains in a verified before-generation. Only authenticated
scratch copies are unlinked. A durable intent precedes the source change; CURRENT decides whether
recovery retains the new result or restores the exact previous source.
"""
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
from uuid import uuid4
from zipfile import ZipFile, ZIP_FILECOUNT_LIMIT

from .fs_paths import native_io_path
from .local_media_adapter import _safe_entry
from .managed_downloads import _safe_path, _write, _read_bounded, _json_unique, _fsync_dir
from .managed_quarantine import QuarantineResult
from .managed_root_writer import writer_fence


@dataclass(frozen=True)
class ItemResult:
    key: str
    filename: str
    status: str
    reason: str = ''


def _packet(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()


def _digest(path):
    h=sha256()
    with native_io_path(path).open('rb') as stream:
        while block:=stream.read(1024*1024): h.update(block)
    return h.hexdigest()


def _legacy_input(path,*,missing=False):
    """Legacy originals may have bookmark aliases; owned outputs may not."""
    path=native_io_path(path)
    _safe_path(path.parent)
    try:info=path.lstat()
    except FileNotFoundError:
        if missing:return
        raise
    if (not stat.S_ISREG(info.st_mode) or path.is_symlink()
            or getattr(info,'st_file_attributes',0)&0x400 or info.st_nlink<1):
        raise ValueError('legacy input file rejected')


def _copy(source,target,*,on_open=None,legacy_input=False):
    if legacy_input:_legacy_input(source)
    else:_safe_path(source,file=True)
    _safe_path(target,file=True)
    with native_io_path(source).open('rb') as incoming, native_io_path(target).open('xb') as outgoing:
        if on_open is not None:on_open(outgoing)
        shutil.copyfileobj(incoming,outgoing,1024*1024)
        outgoing.flush();os.fsync(outgoing.fileno())
    if _digest(source)!=_digest(target):raise ValueError('physical copy verification failed')


_SPACE_MARGIN = 1024 * 1024

class QuarantineCapacityError(OSError):
    """Before-change rejection; required bytes are a conservative estimate."""
    def __init__(self, required, available=None):
        self.required=required;self.available=available
        super().__init__('quarantine capacity unavailable' if available is None else 'quarantine capacity insufficient')

def _existing_directory(path):
    path=native_io_path(path);_safe_path(path)
    while not path.exists():
        if path==path.parent:raise OSError('capacity volume unavailable')
        path=path.parent
    if not path.is_dir():raise OSError('capacity directory unavailable')
    return path

def _same_filesystem(first,second):
    return _existing_directory(first).stat().st_dev==_existing_directory(second).stat().st_dev

def _require_space(directory,required):
    try:available=shutil.disk_usage(_existing_directory(directory)).free
    except OSError:raise QuarantineCapacityError(required) from None
    if available<required:raise QuarantineCapacityError(required,available)


class ScratchCleanupError(OSError):
    """An owned scratch could not be removed, or ownership is unknown."""


class PhysicalLegacyQuarantine:
    def __init__(self,store):
        self.store=store
        self.journals=store.root/'physical-intents'
        owner=sha256(str(store.owner.root).casefold().encode()).hexdigest()[:8]
        self.root=store.catalog.root.parent/'quarantine'/('pv-'+store.identity['source'][:12]+'-'+owner)
        self.identity={**store.identity,'purpose':'original-relocation','owner':owner}
        self.recovering=False
        self.blocked_sources=set()
        self.cleanup_warnings=set()
        self.capacity_blocked_sources=set()

    def _ensure(self):
        self.store._ensure()
        _safe_path(self.root)
        root=native_io_path(self.root)
        if root.exists():
            if json.loads(_read_bounded(root/'IDENTITY.json',4096),object_pairs_hook=_json_unique)!=self.identity:
                raise ValueError('physical quarantine identity changed')
        else:
            root.parent.mkdir(parents=True,exist_ok=True)
            staging=root.parent/('.pv-init-'+uuid4().hex)
            staging.mkdir();_write(staging/'IDENTITY.json',_packet(self.identity));_fsync_dir(staging)
            os.rename(staging,root);_fsync_dir(root.parent)
        native_io_path(self.journals).mkdir(exist_ok=True);_safe_path(self.journals)

    def _source(self,relative):
        if not _safe_entry(relative):raise ValueError('physical source rejected')
        path=self.store.catalog.root.joinpath(*PurePosixPath(relative).parts)
        _legacy_input(path,missing=True)
        return native_io_path(path)

    def _unit(self,identity):
        if len(identity)!=32 or any(c not in '0123456789abcdef' for c in identity):
            raise ValueError('physical unit rejected')
        path=self.root/identity;_safe_path(path)
        return native_io_path(path)

    def _read_intent(self,path,*,verify_before=True):
        raw=_read_bounded(path,16384)
        intent=json.loads(raw,object_pairs_hook=_json_unique)
        if (intent['identity']!=self.identity or intent['unit']!=path.parent.name
                or intent['kind'] not in ('file','zip')):
            raise ValueError('physical intent rejected')
        unit=self._unit(intent['unit'])
        if verify_before and _digest(unit/'before')!=intent['before_sha256']:
            raise ValueError('physical before-generation changed')
        if not _safe_entry(intent['source']):raise ValueError('physical source rejected')
        return intent,sha256(raw).hexdigest()

    def _seal(self,path,intent,status):
        self._cleanup_scratch(intent)
        marker=path.parent/'settled.json'
        payload={'intent':sha256(_packet(intent)).hexdigest(),'status':status}
        if marker.exists():
            if json.loads(_read_bounded(marker,1024))!=payload:raise ValueError('physical result changed')
        else:
            temporary=path.parent/('settled-'+uuid4().hex+'.next')
            _write(temporary,_packet(payload));os.replace(temporary,marker);_fsync_dir(path.parent)

    def _scratch(self,intent,kind):
        source=self._source(intent['source'])
        return source.parent/('.pv-'+kind+'-'+intent['unit'])

    def _scratch_owner(self,intent,kind):
        return native_io_path(self.journals/intent['unit']/(kind+'-owner.json'))

    def _copy_scratch(self,intent,kind,source):
        target=self._scratch(intent,kind)
        reservation=self._scratch_owner(intent,kind).with_name(kind+'-reservation.json')
        value={'identity':self.identity,'unit':intent['unit'],'name':target.name}
        if reservation.exists():
            if json.loads(_read_bounded(reservation,4096),object_pairs_hook=_json_unique)!=value:
                raise ScratchCleanupError('scratch reservation changed')
        else:
            temporary=reservation.parent/(kind+'-reservation-'+uuid4().hex+'.next')
            _write(temporary,_packet(value));os.rename(temporary,reservation);_fsync_dir(reservation.parent)
        self.store._checkpoint('physical-scratch-reserved:'+kind+':'+intent['key'])
        def own(stream):
            # Exclusive creation is durable before recording inode ownership.
            # A crash before this receipt leaves an unknown file: never unlink it.
            stream.flush();os.fsync(stream.fileno())
            info=os.fstat(stream.fileno())
            receipt={'identity':self.identity,'unit':intent['unit'],'name':target.name,
                     'device':info.st_dev,'inode':info.st_ino}
            owner=self._scratch_owner(intent,kind)
            temporary=owner.parent/(kind+'-owner-'+uuid4().hex+'.next')
            _write(temporary,_packet(receipt));os.replace(temporary,owner);_fsync_dir(owner.parent)
            self.store._checkpoint('physical-scratch-owned:'+kind+':'+intent['key'])
        _copy(source,target,on_open=own)
        return target

    def _before_scratch_delete(self,path):
        """Fault/race seam: ownership is verified and the handle is held."""

    def _delete_scratch_windows(self,target,intent,kind):
        import ctypes
        import msvcrt
        from ctypes import wintypes
        from .frozen_source_pin import _kernel

        create,close,invalid=_kernel()
        # The same handle supplies identity, optional legacy digest and delete.
        # FILE_SHARE_READ denies ordinary rename, replacement and data writers.
        handle=create(str(target),0x80000000|0x00010000,0x00000001,None,3,0x00200000,None)
        if handle is None or handle==invalid:raise ScratchCleanupError('scratch pin unavailable')
        fd=None
        try:
            fd=msvcrt.open_osfhandle(handle,os.O_RDONLY|os.O_BINARY|os.O_NOINHERIT)
            handle=None
            info=os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1
                    or getattr(info,'st_file_attributes',0)&0x400):
                raise ScratchCleanupError('scratch file rejected')
            owner=self._scratch_owner(intent,kind)
            if owner.exists():
                saved=json.loads(_read_bounded(owner,4096),object_pairs_hook=_json_unique)
                expected={'identity':self.identity,'unit':intent['unit'],'name':target.name,
                          'device':info.st_dev,'inode':info.st_ino}
                if saved!=expected:raise ScratchCleanupError('scratch ownership changed')
            else:
                digest=sha256()
                while block:=os.read(fd,1024*1024):digest.update(block)
                if kind!='ready' or digest.hexdigest()!=intent['after_sha256']:
                    raise ScratchCleanupError('scratch ownership unavailable')
            self._before_scratch_delete(target)
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            dispose=kernel.SetFileInformationByHandle
            dispose.argtypes=(wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD)
            dispose.restype=wintypes.BOOL
            class Disposition(ctypes.Structure):
                _fields_=[('delete_file',ctypes.c_ubyte)]
            request=Disposition(True)
            if not dispose(msvcrt.get_osfhandle(fd),4,ctypes.byref(request),ctypes.sizeof(request)):
                raise ScratchCleanupError('scratch handle delete rejected')
        finally:
            if fd is not None:os.close(fd)
            if handle is not None:close(handle)

    def _preserve_unreceipted(self,target,intent,kind):
        # A reservation authorizes preservation, never deletion or use as data.
        reservation=self._scratch_owner(intent,kind).with_name(kind+'-reservation.json')
        expected={'identity':self.identity,'unit':intent['unit'],'name':target.name}
        if reservation.exists() and json.loads(_read_bounded(reservation,4096),object_pairs_hook=_json_unique)!=expected:
            raise ScratchCleanupError('scratch reservation unavailable')
        # Pre-upgrade intents have no reservation. Their exact scratch name may
        # be preserved by handle, but never trusted as image data or deleted.
        if os.name!='nt':raise ScratchCleanupError('scratch preservation handle unavailable')
        import ctypes
        import msvcrt
        from ctypes import wintypes
        from .frozen_source_pin import _kernel
        create,close,invalid=_kernel()
        handle=create(str(target),0x80000000|0x00010000,0x00000001,None,3,0x00200000,None)
        if handle is None or handle==invalid:raise ScratchCleanupError('scratch preservation pin unavailable')
        fd=None
        try:
            fd=msvcrt.open_osfhandle(handle,os.O_RDONLY|os.O_BINARY|os.O_NOINHERIT);handle=None
            info=os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1
                    or getattr(info,'st_file_attributes',0)&0x400):
                raise ScratchCleanupError('scratch preservation file rejected')
            destination=self._unit(intent['unit'])/('unreceipted-'+kind+'-'+uuid4().hex)
            name=str(destination)
            class RenameInfo(ctypes.Structure):
                _fields_=[('replace',ctypes.c_ubyte),('root',wintypes.HANDLE),
                          ('length',wintypes.DWORD),('name',ctypes.c_wchar*(len(name)+1))]
            request=RenameInfo();request.replace=0;request.root=None
            request.length=len(name)*ctypes.sizeof(ctypes.c_wchar);request.name=name
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            rename=kernel.SetFileInformationByHandle
            rename.argtypes=(wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD)
            rename.restype=wintypes.BOOL
            if not rename(msvcrt.get_osfhandle(fd),3,ctypes.byref(request),ctypes.sizeof(request)):
                raise ScratchCleanupError('scratch preservation rename rejected')
        finally:
            if fd is not None:os.close(fd)
            if handle is not None:close(handle)
        _fsync_dir(destination.parent);_fsync_dir(target.parent)
        self.cleanup_warnings.add(intent['source'])

    def _cleanup_one(self,intent,kind):
        target=self._scratch(intent,kind)
        try:
            _safe_path(target,file=True)
            if not target.exists():return
            if not self._scratch_owner(intent,kind).exists():
                self._preserve_unreceipted(target,intent,kind)
                return
            if os.name!='nt':
                # A path-based fallback would recreate the race. Preserve the
                # file when this Windows product has no mandatory handle pin.
                raise ScratchCleanupError('scratch handle deletion unavailable')
            self._delete_scratch_windows(target,intent,kind)
            _fsync_dir(target.parent)
        except Exception:
            raise ScratchCleanupError('physical scratch cleanup required') from None

    def _cleanup_scratch(self,intent):
        for kind in ('ready','recover'):self._cleanup_one(intent,kind)

    def _check_residue(self,source):
        # Recover() has settled all known intents under both writer fences.
        # Never glob-delete old random names or another owner's files.
        known={}
        for journal in native_io_path(self.journals).glob('*/intent.json'):
            intent,_=self._read_intent(journal,verify_before=False)
            for kind in ('ready','recover'):
                known[self._scratch(intent,kind)]=self._source(intent['source'])
        for target in (*source.parent.glob('.pv-ready-*'),*source.parent.glob('.pv-recover-*')):
            if target not in known or known[target]==source:
                raise ScratchCleanupError('unattributed physical scratch requires inspection')

    def _displace_rollback_source(self,intent,source,unit):
        if not source.exists():return
        if _digest(source)!=intent['after_sha256']:raise ValueError('physical recovery source conflict')
        # Keep the changed generation too. Never replace/delete it.
        destination=(unit/('rolled-back-'+uuid4().hex) if _same_filesystem(source.parent,unit)
                     else source.parent/('.pv-rollback-after-'+intent['unit']))
        _safe_path(destination,file=True)
        if destination.exists():raise ValueError('physical rollback preservation occupied')
        os.rename(source,destination);_fsync_dir(source.parent);_fsync_dir(destination.parent)
        self.store._checkpoint('physical-rollback-displaced:'+intent['key'])

    def _rollback(self,intent):
        source=self._source(intent['source']);unit=self._unit(intent['unit'])
        self._cleanup_one(intent,'recover')
        if source.exists():
            current=_digest(source)
            if current==intent['before_sha256']:
                self._cleanup_scratch(intent);return
            if intent['kind']!='zip' or current!=intent['after_sha256']:
                raise ValueError('physical recovery source conflict')
        if intent['kind']=='zip':
            donor=unit/'retained'
            moved=unit/'retained-next'
            if donor.exists() and moved.exists():raise ValueError('physical recovery source conflict')
            if not donor.exists() and moved.exists():donor=moved
            if donor.exists() and _same_filesystem(unit,source.parent):
                _legacy_input(donor)
                if _digest(donor)!=intent['before_sha256']:raise ValueError('physical recovery source conflict')
                if source.exists():
                    if _digest(source)!=intent['after_sha256']:raise ValueError('physical recovery source conflict')
                    self.store._checkpoint('physical-rollback-displaced:'+intent['key'])
                if donor!=moved:os.rename(donor,moved)
                os.replace(moved,source);_fsync_dir(source.parent);_fsync_dir(unit)
                self.store._checkpoint('physical-rollback-restored:'+intent['key'])
                if _digest(source)!=intent['before_sha256']:raise ValueError('physical recovery verification failed')
                self._cleanup_scratch(intent);return
            before=unit/'before'
            if _digest(before)!=intent['before_sha256']:raise ValueError('physical before-generation changed')
            _require_space(source.parent,before.stat().st_size+_SPACE_MARGIN)
            ready=self._copy_scratch(intent,'recover',before)
            if source.exists():
                if _digest(source)!=intent['after_sha256']:raise ValueError('physical recovery source conflict')
                self.store._checkpoint('physical-rollback-displaced:'+intent['key'])
            os.replace(ready,source);_fsync_dir(source.parent)
            self.store._checkpoint('physical-rollback-restored:'+intent['key'])
            if _digest(source)!=intent['before_sha256']:raise ValueError('physical recovery verification failed')
            self._cleanup_scratch(intent);return
        donor=unit/(intent['filename'] if intent['kind']=='file' else 'retained')
        if donor.exists():
            _legacy_input(donor)
            if _digest(donor)!=intent['before_sha256']:raise ValueError('physical recovery source conflict')
            if _same_filesystem(unit,source.parent):
                if source.exists():self._displace_rollback_source(intent,source,unit)
                os.rename(donor,source);_fsync_dir(source.parent);_fsync_dir(unit)
                self.store._checkpoint('physical-rollback-restored:'+intent['key'])
                if _digest(source)!=intent['before_sha256']:raise ValueError('physical recovery verification failed')
                self._cleanup_scratch(intent);return
        # Old intents may lack the retained inode; before remains the immutable
        # recovery proof and must not be consumed by a rename. Cross-volume
        # donors likewise need a copy. Detect shortage before source changes.
        before=unit/'before'
        if _digest(before)!=intent['before_sha256']:raise ValueError('physical before-generation changed')
        _require_space(source.parent,before.stat().st_size+_SPACE_MARGIN)
        ready=self._copy_scratch(intent,'recover',before)
        if source.exists():self._displace_rollback_source(intent,source,unit)
        os.rename(ready,source);_fsync_dir(source.parent)
        self.store._checkpoint('physical-rollback-restored:'+intent['key'])
        if _digest(source)!=intent['before_sha256']:raise ValueError('physical recovery verification failed')
        self._cleanup_scratch(intent)

    def _preflight_space(self,plan):
        # Aggregate the whole batch by actual filesystem, including roots on
        # different volumes. No unit/intent/source mutation precedes this check.
        volumes={}
        def add(directory,amount):
            directory=_existing_directory(directory);device=directory.stat().st_dev
            old=volumes.get(device,(directory,0))
            volumes[device]=(old[0],old[1]+amount)
        for item in plan.items:
            identity=self.store.catalog.analysis_source_identity(item.work_id,item.page-1)
            source=Path(identity[0])/identity[1];size=native_io_path(source).stat().st_size
            member=identity[-1][0] if identity[-1] is not None else None
            add(self.root,size+_SPACE_MARGIN)
            add(self.store.root,item.file_size+_SPACE_MARGIN)
            if member is not None:
                with self.store.catalog._checked_open(source) as stream,ZipFile(stream) as archive:
                    infos=archive.infolist()
                    if len(infos)>100000:raise ValueError('physical archive members rejected')
                    # Conservative compression/header bound, not an estimate
                    # based on a favourable compression ratio.
                    rebuilt=max(size,sum(info.file_size+info.file_size//8+4096+
                        2*len(info.filename.encode('utf-8'))+2*len(info.extra)+len(info.comment)
                        for info in infos)+65536)
                add(self.root,rebuilt+item.file_size)
                add(self.store.root,rebuilt)
                add(source.parent,rebuilt)
            if not _same_filesystem(self.root,source.parent):
                add(source.parent,size+_SPACE_MARGIN)
        for directory,required in volumes.values():_require_space(directory,required)

    def recover(self,*,strict=False):
        if self.recovering or not native_io_path(self.journals).exists():return
        with writer_fence(self.store.owner.root),writer_fence(self.store.catalog.root):
            self.recovering=True
            blocked=set()
            self.cleanup_warnings=set()
            self.capacity_blocked_sources=set()
            try:
                _safe_path(self.journals)
                for path in sorted(native_io_path(self.journals).glob('*/intent.json')):
                    _safe_path(path,file=True)
                    # Invalid identity/path is not a localized, trusted intent.
                    intent,digest=self._read_intent(path,verify_before=False)
                    # Keep previous failures and this in-progress source blocked
                    # until a complete recovery pass publishes its final result.
                    self.blocked_sources=frozenset((*self.blocked_sources,intent['source']))
                    known=False
                    if any(self._unit(intent['unit']).glob('unreceipted-*')):
                        self.cleanup_warnings.add(intent['source'])
                    try:
                        marker=path.parent/'settled.json'
                        if marker.exists():
                            saved=json.loads(_read_bounded(marker,1024),object_pairs_hook=_json_unique)
                            if saved.get('intent')!=digest or saved.get('status') not in ('committed','rolled-back'):
                                raise ValueError('physical settlement rejected')
                            known=True
                            self._cleanup_scratch(intent)
                            continue
                        state=self.store.state(verify_outputs=False)
                        known=intent['unit'] in state['operations']
                        if known:
                            self._seal(path,intent,'committed')
                        else:
                            if _digest(self._unit(intent['unit'])/'before')!=intent['before_sha256']:
                                raise ValueError('physical before-generation changed')
                            self._rollback(intent)
                            self._seal(path,intent,'rolled-back')
                        self.store.catalog.replace_snapshot(self.store.original_snapshot)
                    except (OSError,ValueError,RuntimeError) as error:
                        if isinstance(error,QuarantineCapacityError):self.capacity_blocked_sources.add(intent['source'])
                        if known:self.cleanup_warnings.add(intent['source'])
                        else:blocked.add(intent['source'])
                self.blocked_sources=frozenset(blocked)
                if strict and self.blocked_sources:
                    raise ScratchCleanupError('physical source recovery required')
            finally:self.recovering=False

    def _prepare(self,item,unit_id):
        store=self.store
        identity=store.catalog.analysis_source_identity(item.work_id,item.page-1)
        source=Path(identity[0])/identity[1]
        self._check_residue(native_io_path(source))
        member=identity[-1][0] if identity[-1] is not None else None
        raw=store.catalog.get(item.work_id,item.page-1,'original').data
        if sha256(raw).hexdigest()!=item.image_sha256 or len(raw)!=item.file_size:
            raise ValueError('physical selected image changed')
        unit=self._unit(unit_id);unit.mkdir()
        stage=native_io_path(store.root/'operations'/unit_id);stage.mkdir()
        with store.catalog._checked_open(source):_copy(source,unit/'before',legacy_input=True)
        store._checkpoint('physical-backup:'+item.key)
        # Independent download-proof copy remains verifiable after relocation.
        _write(stage/'selected.image',raw)
        filename=PurePosixPath(member).name if member is not None else source.name
        if member is not None:
            _write(unit/filename,raw)
            with store.catalog._checked_open(source) as incoming,ZipFile(incoming) as original:
                infos=original.infolist()
                if len(infos)>100000 or len({i.filename for i in infos})!=len(infos):
                    raise ValueError('physical archive members rejected')
                use_zip64=source.stat().st_size >= 4000 * 1024 * 1024
                allow_zip64=use_zip64 or len(infos)-1 > ZIP_FILECOUNT_LIMIT
                with (unit/'after').open('xb') as output:
                    with ZipFile(output,'w',allowZip64=allow_zip64) as rebuilt:
                        rebuilt.comment=original.comment
                        for info in infos:
                            if (not _safe_entry(info.filename) or info.flag_bits&1
                                    or stat.S_IFMT(info.external_attr>>16)==stat.S_IFLNK):
                                raise ValueError('physical archive member rejected')
                            if info.is_dir():rebuilt.writestr(info,b'');continue
                            # Streaming preserves large unrelated members and Zip64.
                            with original.open(info) as part:
                                if info.filename==member:
                                    while part.read(1024*1024):pass  # CRC verification, bounded memory.
                                else:
                                    with rebuilt.open(info,'w',force_zip64=use_zip64) as target:
                                        shutil.copyfileobj(part,target,1024*1024)
                    output.flush();os.fsync(output.fileno())
            _copy(unit/'after',stage/'active.zip')
        intent={'identity':self.identity,'unit':unit_id,'key':item.key,
                'source':source.relative_to(store.catalog.root).as_posix(),
                'filename':filename,'member':member,'kind':'zip' if member is not None else 'file',
                'before_sha256':_digest(unit/'before'),
                'after_sha256':_digest(unit/'after') if member is not None else None}
        if _digest(source)!=intent['before_sha256']:raise ValueError('physical source changed')
        _fsync_dir(unit);_fsync_dir(stage)
        journal=native_io_path(self.journals/unit_id);journal.mkdir()
        _write(journal/'intent.next',_packet(intent));os.rename(journal/'intent.next',journal/'intent.json')
        _fsync_dir(journal);_fsync_dir(native_io_path(self.journals))
        store._checkpoint('physical-intent:'+item.key)
        return intent,journal/'intent.json',stage

    def _install(self,intent):
        source=self._source(intent['source']);unit=self._unit(intent['unit'])
        if _digest(source)!=intent['before_sha256']:raise ValueError('physical source changed')
        if intent['kind']=='file':
            os.rename(source,unit/intent['filename'])
        else:
            ready=self._copy_scratch(intent,'ready',unit/'after')
            if _digest(source)!=intent['before_sha256']:raise ValueError('physical source changed')
            # A same-volume hard link preserves the original inode without
            # removing the public source name before the atomic replacement.
            retained=unit/'retained'
            if _same_filesystem(unit,source.parent):
                try:os.link(source,retained)
                except OSError:pass  # The durable before-copy remains the fallback.
                if retained.exists() and (_digest(retained)!=intent['before_sha256']
                                          or not os.path.samefile(source,retained)):
                    raise ValueError('physical source changed')
            self.store._checkpoint('physical-source-retained:'+intent['key'])
            if _digest(source)!=intent['before_sha256']:
                raise ValueError('physical source changed')
            os.replace(ready,source)
        _fsync_dir(source.parent);_fsync_dir(unit)

    def execute(self,plan,operation_id,*,validate_snapshot=None):
        store=self.store
        with writer_fence(store.owner.root),writer_fence(store.catalog.root),store._lock:
            self.recover();state=store.state()
            revision=store.owner._generations.state_snapshot()['revision']+':'+state['revision']
            if plan.state_revision!=revision:raise ValueError('physical plan changed')
            # A different managed root can share this image source. Its writes
            # change no revision here: recheck all selected-group survivors
            # under the source fence, not just the images being moved.
            store.catalog.replace_snapshot(store.original_snapshot)
            unavailable=set()
            for group in plan.groups:
                for page in group.items:
                    try:
                        store.require_available(page.work_id,page.page-1)
                        raw=store.catalog.get(page.work_id,page.page-1,'original').data
                    except (OSError,ValueError,RuntimeError):
                        unavailable.update(p.key for p in group.items);continue
                    if sha256(raw).hexdigest()!=page.image_sha256:
                        raise ValueError('physical remaining group changed')
            if unavailable:
                failed=tuple(p for p in plan.items if p.key in unavailable)
                unprocessed=tuple(p for p in plan.items if p.key not in unavailable)
                return QuarantineResult(operation_id,(),failed,state['revision'],unprocessed=unprocessed,
                    item_results=tuple(ItemResult(p.key,p.filename,'failed' if p.key in unavailable else 'unprocessed',
                        '元の画像または残す画像を確認できません。候補を保持しています' if p.key in unavailable
                        else '他の項目の確認待ち') for p in plan.items))
            # Preserve the full catalog binding for actual mutations. Missing
            # sources above may return diagnostics, never permission to write.
            if validate_snapshot is not None and validate_snapshot() is not True:
                raise ValueError('physical snapshot changed')
            try:self._preflight_space(plan)
            except (OSError,ValueError,RuntimeError) as error:
                reason=('空き容量が不足しています。元データは変更していません'
                        if isinstance(error,QuarantineCapacityError) and error.available is not None
                        else '空き容量または保存先を確認できません。元データは変更していません')
                return QuarantineResult(operation_id,(),(),state['revision'],unprocessed=plan.items,
                    item_results=tuple(ItemResult(p.key,getattr(p,'filename',''),'unprocessed',reason) for p in plan.items))
            try:self._ensure()
            except (OSError,ValueError):
                return QuarantineResult(operation_id,(),(),state['revision'],unprocessed=plan.items,
                    item_results=tuple(ItemResult(p.key,getattr(p,'filename',''),'unprocessed','保存先の準備ができません。元データは変更していません') for p in plan.items))
            committed=[];failed=[];unprocessed=[];results=[];recovery_required=False;cleanup_warning=False
            self.recovering=True  # This writer owns the only unsettled intent.
            try:
                for index,item in enumerate(plan.items):
                    unit_id=sha256(f'{operation_id}:{index}'.encode()).hexdigest()[:32]
                    if native_io_path(self.journals/unit_id).exists():raise ValueError('physical operation already attempted')
                    intent=path=None
                    try:
                        intent,path,stage=self._prepare(item,unit_id)
                        self._install(intent);store._checkpoint('physical-installed:'+item.key)
                        updated=json.loads(_packet(state))
                        updated['exclusions'][item.key]={'work_id':item.work_id,'page':item.page,
                            'sha256':item.image_sha256,'size':item.file_size,
                            'copy':f'operations/{unit_id}/selected.image',
                            'store':intent['source'],'member':intent['member']}
                        if intent['kind']=='zip':
                            updated['archives'][intent['source']]={'source_sha256':intent['after_sha256'],
                                'path':f'operations/{unit_id}/active.zip','sha256':intent['after_sha256']}
                        updated['revision']=unit_id;updated['operations'][unit_id]=[item.key]
                        store._publish_state(updated,stage)
                        state=updated
                        store._checkpoint('physical-published:'+item.key)
                        self._seal(path,intent,'committed')
                        committed.append(item);results.append(ItemResult(item.key,intent['filename'],'success'))
                    except Exception as error:
                        # CURRENT, not an exception location, establishes success.
                        saved=store.state(verify_outputs=False)
                        if unit_id in saved['operations']:
                            state=saved;committed.append(item)
                            results.append(ItemResult(item.key,intent['filename'],'success','保存済み結果を確認しました'))
                            try:
                                if path is not None:self._seal(path,intent,'committed')
                            except Exception:
                                recovery_required=True
                                cleanup_warning=True
                                unprocessed.extend(plan.items[index+1:])
                                results.extend(ItemResult(p.key,getattr(p,'filename',''),'unprocessed','保存済み結果の確認待ち') for p in unprocessed)
                                break
                        else:
                            try:
                                # prepare may have written intent before its checkpoint failed.
                                pending=native_io_path(self.journals/unit_id/'intent.json')
                                if path is None and pending.exists():intent,_=self._read_intent(pending);path=pending
                                if intent is not None:self._rollback(intent);self._seal(path,intent,'rolled-back')
                            except Exception as recovery_error:
                                recovery_required=True
                                cleanup_warning=True
                                reason=('復旧用の空き容量が不足または確認できません。退避データを保全しています'
                                        if isinstance(recovery_error,QuarantineCapacityError) else '退避済み・一時ファイルと復旧の確認が必要です')
                                failed.append(item);results.append(ItemResult(item.key,getattr(item,'filename',''),'failed',reason))
                                unprocessed.extend(plan.items[index+1:])
                                results.extend(ItemResult(p.key,getattr(p,'filename',''),'unprocessed','前項目の復旧待ち') for p in unprocessed)
                                break
                            failed.append(item)
                            if isinstance(error,ScratchCleanupError):
                                cleanup_warning=True;recovery_required=True
                                results.append(ItemResult(item.key,item.filename,'failed','所有を確認できない一時ファイルがあります。移動を停止しました。保存先を確認してください'))
                                unprocessed.extend(plan.items[index+1:])
                                results.extend(ItemResult(p.key,p.filename,'unprocessed','一時ファイルの確認待ち') for p in unprocessed)
                                break
                            reason='保存先の権限を確認してください' if isinstance(error,PermissionError) else '元データを保全しました。状態を確認して再実行してください'
                            results.append(ItemResult(item.key,getattr(item,'filename',''),'failed',reason))
                    finally:store.catalog.replace_snapshot(store.original_snapshot)
                return QuarantineResult(operation_id,tuple(committed),tuple(failed),state['revision'],
                    cleanup_warning=cleanup_warning,unprocessed=tuple(unprocessed),item_results=tuple(results),recovery_required=recovery_required)
            finally:self.recovering=False
