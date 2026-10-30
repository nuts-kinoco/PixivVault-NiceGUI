"""Aggregate LRU for all media snapshots and opt-in owned thumbnail disk storage."""
from __future__ import annotations
from collections import OrderedDict
from pathlib import Path
from threading import RLock
from uuid import uuid4
import hashlib
import os
import re
import stat
import time
from .media import ImageBlob


class ImageCacheError(RuntimeError):
    pass


def _budget(value):
    if type(value) is not int or value <= 0:
        raise ValueError('cache budget must be positive bytes')
    return value


def _safe_node(path):
    try: info=path.lstat()
    except FileNotFoundError:return True
    return (not stat.S_ISLNK(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 0x400
            and (not stat.S_ISREG(info.st_mode) or info.st_nlink == 1))


def _safe_open_file(stream,path):
    info=os.fstat(stream.fileno()); node=path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or getattr(info,'st_file_attributes',0) & 0x400
            or not _safe_node(path) or not os.path.samestat(info,node)):
        raise ImageCacheError('cache file identity rejected')


class DiskThumbnailCache:
    """Image-only .cache files in a marked root; no URLs, cookies or paths stored.

    Exclusive owner lock prevents two app instances from racing eviction.
    Budgets count each stored file's full bytes, including its integrity header.
    Only this marked root's known cache files can be evicted.
    """
    MARKER='pixivvault-nicegui-thumbnail-cache-v1'
    def __init__(self,root,*,max_bytes):
        self.root=Path(root).absolute()
        if not all(_safe_node(p) for p in (self.root,*self.root.parents)):
            raise ImageCacheError('cache root cannot be a link')
        self._lock=RLock();self._closed=False;self.max_bytes=_budget(max_bytes)
        if self.root.exists() and not self.root.is_dir():raise ImageCacheError('cache root must be a directory')
        self.root.mkdir(parents=True,exist_ok=True)
        marker=self.root/'.pixivvault-thumbnail-cache'
        if marker.exists():
            if not _safe_node(marker) or marker.read_text(encoding='ascii')!=self.MARKER:
                raise ImageCacheError('cache root marker rejected')
        else:
            if any(self.root.iterdir()):raise ImageCacheError('unmarked nonempty cache root rejected')
            with marker.open('x',encoding='ascii') as stream:stream.write(self.MARKER)
        owner=self.root/'.owner.lock'
        if not _safe_node(owner):raise ImageCacheError('cache owner lock rejected')
        self._owner=owner.open('a+b')
        try:
            _safe_open_file(self._owner,owner)
            if self._owner.seek(0,2)==0:self._owner.write(b'0');self._owner.flush()
            self._owner.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self._owner.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self._owner.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            self._entries={}
            for path in self.root.iterdir():
                if path.name in {'.pixivvault-thumbnail-cache','.owner.lock'}:continue
                if re.fullmatch(r'[a-f0-9]{32}\.tmp',path.name) and _safe_node(path):
                    path.unlink();continue
                if not re.fullmatch(r'[a-f0-9]{64}\.cache',path.name) or not _safe_node(path) or not path.is_file():
                    raise ImageCacheError('unknown cache root contents')
                if re.fullmatch(r'[a-f0-9]{64}\.cache',path.name) and _safe_node(path) and path.is_file():
                    info=path.stat();self._entries[path.name]=(info.st_size,info.st_mtime_ns)
            self._evict(0)
        except Exception:
            self._owner.close()
            raise ImageCacheError('thumbnail cache already owned or unavailable') from None

    def _guard(self):
        if self._closed:raise ImageCacheError('thumbnail cache closed')
        if not all(_safe_node(p) for p in (self.root,*self.root.parents)):
            raise ImageCacheError('cache root changed')
        marker=self.root/'.pixivvault-thumbnail-cache'
        if not _safe_node(marker) or marker.read_text(encoding='ascii')!=self.MARKER:
            raise ImageCacheError('cache marker changed')

    def _evict(self,extra):
        while self._entries and sum(x[0] for x in self._entries.values())+extra>self.max_bytes:
            name=min(self._entries,key=lambda n:self._entries[n][1])
            path=self.root/name
            if not _safe_node(path):raise ImageCacheError('cache entry changed')
            path.unlink(missing_ok=True)
            self._entries.pop(name)

    def configure(self,max_bytes):
        with self._lock:
            self._guard();self.max_bytes=_budget(max_bytes);self._evict(0)

    def get(self,identity):
        name=hashlib.sha256(identity.encode('utf-8')).hexdigest()+'.cache'
        with self._lock:
            self._guard()
            path=self.root/name
            if name not in self._entries or not path.is_file() or not _safe_node(path):return None
            with path.open('rb') as stream:
                _safe_open_file(stream,path)
                data=stream.read(min(self.max_bytes,32*1024*1024)+1)
                if len(data)>self.max_bytes or len(data)<40 or data[32:40]!=b'\x89PNG\r\n\x1a\n' or hashlib.sha256(data[32:]).digest()!=data[:32]:return None
                # Never update metadata via a path that may be replaced with a hardlink.
                if os.utime in os.supports_fd:os.utime(stream.fileno(),None)
            self._entries[name]=(len(data),time.time_ns())
            png=data[32:]
            return ImageBlob(png,'image/png','"'+hashlib.sha256(png).hexdigest()+'"')

    def put(self,identity,blob):
        if blob.media_type!='image/png' or not blob.data.startswith(b'\x89PNG\r\n\x1a\n'):return
        payload=hashlib.sha256(blob.data).digest()+blob.data
        if len(payload)>min(self.max_bytes,32*1024*1024):return
        name=hashlib.sha256(identity.encode('utf-8')).hexdigest()+'.cache'
        with self._lock:
            self._guard()
            path=self.root/name
            if not _safe_node(path):raise ImageCacheError('cache entry changed')
            if path.exists():path.unlink()
            self._entries.pop(name,None)
            self._evict(len(payload))
            temporary=self.root/(uuid4().hex+'.tmp')
            try:
                with temporary.open('xb') as stream:
                    stream.write(payload);stream.flush();os.fsync(stream.fileno())
                os.replace(temporary,path)
                self._entries[name]=(len(payload),path.stat().st_mtime_ns)
            finally:
                if temporary.exists():temporary.unlink()

    def clear(self):
        with self._lock:
            self._guard()
            for name in tuple(self._entries):
                path=self.root/name
                if not _safe_node(path):raise ImageCacheError('cache entry changed')
                path.unlink(missing_ok=True)
                self._entries.pop(name)

    @property
    def bytes(self):
        with self._lock:return sum(x[0] for x in self._entries.values())

    def close(self):
        with self._lock:
            if not self._closed:
                self._closed=True
                self._owner.close()


class SharedImageCache:
    def __init__(self,*,max_bytes,disk=None):
        self.max_bytes=_budget(max_bytes);self.disk=disk
        self._lock=RLock();self._entries=OrderedDict();self._bytes=0;self._closed=False
        self._namespaces=set()

    def namespace(self):
        with self._lock:
            if self._closed:raise ImageCacheError('image cache closed')
            identity=uuid4().hex;self._namespaces.add(identity)
            return ImageCacheNamespace(self,identity)

    def _evict(self,extra=0):
        while self._entries and self._bytes+extra>self.max_bytes:
            _,blob=self._entries.popitem(last=False);self._bytes-=len(blob.data)

    def configure(self,max_bytes):
        with self._lock:
            if self._closed:raise ImageCacheError('image cache closed')
            self.max_bytes=_budget(max_bytes);self._evict()

    def clear(self):
        with self._lock:
            if self._closed:raise ImageCacheError('image cache closed')
            self._entries.clear();self._bytes=0
            if self.disk is not None:self.disk.clear()

    def stats(self):
        with self._lock:return {'bytes':self._bytes,'limit':self.max_bytes,'entries':len(self._entries),'namespaces':len(self._namespaces)}

    def close(self):
        with self._lock:
            self._closed=True;self._entries.clear();self._namespaces.clear();self._bytes=0
        if self.disk is not None:self.disk.close()


class ImageCacheNamespace:
    def __init__(self,owner,identity):
        self.owner=owner;self.identity=identity;self.closed=False
        self._fifo=OrderedDict();self._fifo_mode=None;self._fifo_limit=None

    def _trim_fifo(self):
        for key in tuple(self._fifo):
            if (self.identity,key) not in self.owner._entries:self._fifo.pop(key,None)
        if self._fifo_mode is None:return
        def over():
            if self._fifo_mode=='count':return len(self._fifo)>self._fifo_limit
            return sum(len(self.owner._entries[(self.identity,key)].data) for key in self._fifo)>self._fifo_limit
        while self._fifo and over():
            key,_=self._fifo.popitem(last=False)
            self.owner._bytes-=len(self.owner._entries.pop((self.identity,key)).data)

    def configure_fifo(self,mode,limit):
        if mode not in {'bytes','count'}:raise ValueError('invalid prefetch mode')
        _budget(limit)
        with self.owner._lock:
            if self.closed or self.owner._closed:raise ImageCacheError('image cache closed')
            self._fifo_mode=mode;self._fifo_limit=limit
            for namespace,key in self.owner._entries:
                if namespace==self.identity and isinstance(key,tuple) and len(key)==3 and key[2]=='view':
                    self._fifo.setdefault(key,None)
            self._trim_fifo()

    def prefetch_stats(self):
        with self.owner._lock:
            self._trim_fifo()
            size=sum(len(self.owner._entries[(self.identity,key)].data) for key in self._fifo)
            return {'mode':self._fifo_mode,'limit':self._fifo_limit,'entries':len(self._fifo),'bytes':size,
                    'full':self._fifo_mode is not None and (len(self._fifo) if self._fifo_mode=='count' else size)>=self._fifo_limit}


    def get(self,key,*,disk_key=None):
        with self.owner._lock:
            if self.closed or self.owner._closed:return None
            full=(self.identity,key)
            blob=self.owner._entries.get(full)
            if blob is not None:self.owner._entries.move_to_end(full);return blob
        if disk_key is not None and self.owner.disk is not None:
            try:blob=self.owner.disk.get(disk_key)
            except (OSError,ImageCacheError):blob=None
            if blob is not None:
                with self.owner._lock:
                    if self.closed or self.owner._closed:return None
                    self.put(key,blob)
                    return blob
        return None

    def put(self,key,blob,*,disk_key=None):
        with self.owner._lock:
            if self.closed or self.owner._closed:return
            full=(self.identity,key)
            old=self.owner._entries.pop(full,None)
            if old is not None:self.owner._bytes-=len(old.data)
            if len(blob.data)<=self.owner.max_bytes:
                self.owner._evict(len(blob.data));self.owner._entries[full]=blob;self.owner._bytes+=len(blob.data)
            if self._fifo_mode is not None and isinstance(key,tuple) and len(key)==3 and key[2]=='view':
                self._fifo.setdefault(key,None)
                self._trim_fifo()
            # Hold owner lock through persistence: a closed namespace must never
            # let an already-completed image publish late to disk.
            if disk_key is not None and self.owner.disk is not None:
                try:self.owner.disk.put(disk_key,blob)
                except (OSError,ImageCacheError):pass

    def clear(self):
        with self.owner._lock:
            keys=[key for key in self.owner._entries if key[0]==self.identity]
            for key in keys:self.owner._bytes-=len(self.owner._entries.pop(key).data)
            self._fifo.clear()

    def close(self):
        with self.owner._lock:
            self.clear();self.closed=True;self.owner._namespaces.discard(self.identity)
