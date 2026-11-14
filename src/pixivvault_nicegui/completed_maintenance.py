"""Opt-in synthetic-data maintenance; never enabled from existing settings."""
import hashlib,json,os,stat,time,threading
from pathlib import Path
from .fs_paths import native_io_path
from .managed_downloads import _safe_path,_read_bounded,_write,_fsync_dir

class MaintenancePolicy:
    def __init__(self,*,synthetic_roots,clock=time.time,days=7,keep=2):
        self.roots=tuple(Path(p).absolute() for p in synthetic_roots)
        if not self.roots or days<7 or keep<2:raise ValueError('maintenance policy rejected')
        self.clock=clock;self.days=days;self.keep=keep
    def allows(self,root):return Path(root).absolute() in self.roots

def remove_verified(path,digest,*,cancelled=lambda:False):
    """Windows handle binds verification and deletion; no path-unlink fallback."""
    import ctypes,msvcrt
    from ctypes import wintypes
    from .frozen_source_pin import _kernel
    if os.name!='nt':raise OSError('verified collection requires Windows')
    _safe_path(path,file=True)
    create,close,invalid=_kernel()
    h=create(str(native_io_path(path)),0x80000000|0x10000,1,None,3,0x00200000,None)
    if h is None or h==invalid:raise OSError('collection pin unavailable')
    fd=None
    try:
        fd=msvcrt.open_osfhandle(h,os.O_RDONLY|os.O_BINARY|os.O_NOINHERIT);h=None
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or getattr(info,'st_file_attributes',0)&0x400:raise OSError('collection file rejected')
        actual=hashlib.sha256()
        while block:=os.read(fd,1024*1024):
            if cancelled():raise OSError('collection cancelled')
            actual.update(block)
        if actual.hexdigest()!=digest:raise OSError('collection file changed')
        class Disposition(ctypes.Structure):_fields_=[('delete_file',ctypes.c_ubyte)]
        fn=ctypes.WinDLL('kernel32',use_last_error=True).SetFileInformationByHandle
        fn.argtypes=(wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD);fn.restype=wintypes.BOOL
        if cancelled():raise OSError('collection cancelled')
        request=Disposition(True)
        if not fn(msvcrt.get_osfhandle(fd),4,ctypes.byref(request),ctypes.sizeof(request)):raise OSError('collection delete failed')
    finally:
        if fd is not None:os.close(fd)
        if h is not None:close(h)

class BackgroundCollection:
    def __init__(self,callback,*,interval=60):
        if interval<.05:raise ValueError("collection interval rejected")
        self.stop=threading.Event();self.callback=callback;self.last_error=False
        def run():
            while not self.stop.wait(interval):
                try:callback(self.stop);self.last_error=False
                except Exception:self.last_error=True
        self.thread=threading.Thread(target=run,name='pv-approved-synthetic-maintenance',daemon=True);self.thread.start()
    def close(self):self.stop.set();self.thread.join(5)

def collect_zip_duplicates(physical,policy,stop=None):
    store=physical.store
    if not policy.allows(store.owner.root):return ()
    with store._lock:
        state=store.state();physical.recover()
        _safe_path(physical.root)
        if not native_io_path(physical.root).is_dir():return ()
        if json.loads(_read_bounded(physical.root/'IDENTITY.json',4096))!=physical.identity:return ()
        groups={}
        for path in native_io_path(physical.journals).glob('*/intent.json'):
            if stop is not None and stop.is_set():return ()
            intent,digest=physical._read_intent(path)
            settled=path.parent/'settled.json'
            if not settled.exists():continue
            marker=json.loads(_read_bounded(settled,1024))
            if marker!={'intent':digest,'status':'committed'}:continue
            if intent['kind']!='zip' or intent['unit'] not in state['operations']:continue
            if intent['source'] in physical.blocked_sources or intent['source'] in physical.cleanup_warnings:continue
            unit=physical._unit(intent['unit'])
            if tuple(unit.glob('unreceipted-*')):continue
            stamp=path.parent/'collection-observed.json'
            expected={'intent':digest,'observed':policy.clock()}
            if not stamp.exists():
                _write(stamp,json.dumps(expected).encode());continue
            observed=json.loads(_read_bounded(stamp,1024))
            if observed.get('intent')!=digest or type(observed.get('observed')) not in (float,int):continue
            groups.setdefault(intent['source'],[]).append((settled.stat().st_mtime_ns,observed['observed'],intent))
        removed=[]
        for items in groups.values():
            items.sort(key=lambda x:(x[0],x[2]['unit']),reverse=True)
            for order,when,intent in items[policy.keep:]:
                if policy.clock()-when<policy.days*86400:continue
                unit=physical._unit(intent['unit'])
                # before is a live recovery/page-binding source; active.zip is
                # a live library/download proof. Do not collect either.
                for name,key in [('retained','before_sha256'),('after','after_sha256')]:
                    path=unit/name
                    if not path.exists():continue
                    # An independent verified surviving copy is mandatory.
                    survivor=unit/'before' if name=='retained' else store._path('operations/'+intent['unit']+'/active.zip')
                    expected=intent[key]
                    actual=hashlib.sha256()
                    _safe_path(survivor,file=True)
                    with native_io_path(survivor).open('rb') as f:
                        while block:=f.read(1024*1024):actual.update(block)
                    if actual.hexdigest()!=expected:continue
                    with verified_survivor(survivor,expected):
                        remove_verified(path,expected,cancelled=lambda:stop is not None and stop.is_set())
                    removed.append((intent['unit'],name))
        return tuple(removed)

from contextlib import contextmanager

@contextmanager
def verified_survivor(path,digest):
    """Keep the surviving copy pinned against write/rename until deletion ends."""
    import msvcrt
    from .frozen_source_pin import _kernel
    if os.name!='nt':raise OSError('collection pin requires Windows')
    _safe_path(path,file=True);create,close,invalid=_kernel()
    h=create(str(native_io_path(path)),0x80000000,1,None,3,0x00200000,None)
    if h is None or h==invalid:raise OSError('survivor pin unavailable')
    fd=None
    try:
        fd=msvcrt.open_osfhandle(h,os.O_RDONLY|os.O_BINARY|os.O_NOINHERIT);h=None
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or getattr(info,'st_file_attributes',0)&0x400:raise OSError('survivor rejected')
        actual=hashlib.sha256()
        while block:=os.read(fd,1024*1024):actual.update(block)
        if actual.hexdigest()!=digest:raise OSError('survivor changed')
        yield
    finally:
        if fd is not None:os.close(fd)
        if h is not None:close(h)
