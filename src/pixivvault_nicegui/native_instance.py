"""One Windows native owner per explicit owned profile, before job startup.

A deny-write/delete file handle serializes admission. A second invocation may
only restore a unique WinForms window in the recorded PID/birth process tree.
It does not start a second server or send arbitrary commands to a loopback port.
No global hooks, process termination, registry change, or foreign window input.
"""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
from threading import RLock
import time

from .fs_paths import native_io_path
from .managed_downloads import _safe_path
from .native_profile import PROFILE_MARKER, PROFILE_IDENTITY, _owned_marker


OWNER_FILE = '.pixivvault-native-instance-v1.json'
FORMAT = 'pixivvault-native-instance-v1'
MAX_BYTES = 2048


class NativeInstanceError(ValueError):
    def __init__(self, message='起動済みアプリの所有状態を確認できません。既存ウィンドウを確認してください。'):
        super().__init__(message)


class NativeInstanceOwnerClosed(NativeInstanceError):
    """The previously locked owner has ended; retry only exclusive admission."""


class NativeAppRuntimeError(RuntimeError):
    """A packaged native GUI failed after entering its run/exit lifecycle.

    Never carry the original exception's paths or user details into a dialog.
    This is deliberately not a startup/owner-admission error.
    """


def _require(value):
    if not value:
        raise NativeInstanceError()


def _path_hash(path):
    return sha256(os.path.normcase(os.path.normpath(str(path))).encode('utf-8')).hexdigest()


def _file_identity(info):
    return info.st_dev, info.st_ino


def _json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise NativeInstanceError()
        result[key] = value
    return result


def _record(raw, root_hash):
    try:
        result = json.loads(raw.decode('ascii'), object_pairs_hook=_json_pairs)
        _require(type(result) is dict and set(result) ==
                 {'format','root','image','pid','birth','phase'})
        _require(result['format'] == FORMAT and result['root'] == root_hash
                 and result['phase'] in ('running','closed'))
        _require(type(result['pid']) is int and 0 < result['pid'] <= 0xffffffff
                 and type(result['birth']) is int and result['birth'] > 0)
        _require(type(result['image']) is str and len(result['image']) == 64
                 and all(char in '0123456789abcdef' for char in result['image']))
        return result
    except (ValueError, UnicodeError, TypeError, KeyError):
        raise NativeInstanceError() from None


class _Windows:
    def __init__(self):
        _require(os.name == 'nt')
        self.k = C.WinDLL('kernel32', use_last_error=True)
        self.u = C.WinDLL('user32', use_last_error=True)
        self.callback = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
        specs = (
            (self.k,'CreateFileW',W.HANDLE,(W.LPCWSTR,W.DWORD,W.DWORD,C.c_void_p,W.DWORD,W.DWORD,W.HANDLE)),
            (self.k,'CloseHandle',W.BOOL,(W.HANDLE,)),
            (self.k,'OpenProcess',W.HANDLE,(W.DWORD,W.BOOL,W.DWORD)),
            (self.k,'WaitForSingleObject',W.DWORD,(W.HANDLE,W.DWORD)),
            (self.k,'GetProcessTimes',W.BOOL,(W.HANDLE,*([C.POINTER(W.FILETIME)]*4))),
            (self.k,'QueryFullProcessImageNameW',W.BOOL,(W.HANDLE,W.DWORD,W.LPWSTR,C.POINTER(W.DWORD))),
            (self.k,'CreateToolhelp32Snapshot',W.HANDLE,(W.DWORD,W.DWORD)),
            (self.u,'EnumWindows',W.BOOL,(self.callback,W.LPARAM)),
            (self.u,'GetWindowThreadProcessId',W.DWORD,(W.HWND,C.POINTER(W.DWORD))),
            (self.u,'GetClassNameW',C.c_int,(W.HWND,W.LPWSTR,C.c_int)),
            (self.u,'GetWindowLongW',W.LONG,(W.HWND,C.c_int)),
            (self.u,'GetAncestor',W.HWND,(W.HWND,W.UINT)),
            (self.u,'IsWindow',W.BOOL,(W.HWND,)),
            (self.u,'IsWindowVisible',W.BOOL,(W.HWND,)),
            (self.u,'IsIconic',W.BOOL,(W.HWND,)),
            (self.u,'ShowWindow',W.BOOL,(W.HWND,C.c_int)),
            (self.u,'SetForegroundWindow',W.BOOL,(W.HWND,)),
            (self.u,'GetForegroundWindow',W.HWND,()),
        )
        for dll, name, result, arguments in specs:
            function = getattr(dll,name)
            function.restype, function.argtypes = result,list(arguments)

    def open(self, path, *, writable):
        import msvcrt
        # The owner permits reads only. A reader shares the owner's write
        # right but cannot acquire write/delete permission while owner lives.
        handle = self.k.CreateFileW(str(path), 0xC0000000 if writable else 0x80000000,
            1 if writable else 3, None, 4 if writable else 3, 0x00200000|0x80, None)
        if handle == C.c_void_p(-1).value:
            code = C.get_last_error()
            if writable and code == 32:  # ERROR_SHARING_VIOLATION
                return None
            raise NativeInstanceError()
        try:
            return msvcrt.open_osfhandle(handle,
                (os.O_RDWR if writable else os.O_RDONLY)|os.O_BINARY|os.O_NOINHERIT)
        except Exception:
            self.k.CloseHandle(handle)
            raise NativeInstanceError() from None

    def process(self, pid):
        handle = self.k.OpenProcess(0x1000|0x100000, False, pid)
        if not handle:
            if C.get_last_error() == 87:
                return None
            raise NativeInstanceError()
        try:
            status = self.k.WaitForSingleObject(handle,0)
            if status == 0:
                return None
            _require(status == 0x102)
            created, ended, kernel, user = (W.FILETIME() for _ in range(4))
            _require(self.k.GetProcessTimes(handle,C.byref(created),C.byref(ended),C.byref(kernel),C.byref(user)))
            value = C.create_unicode_buffer(32768)
            size = W.DWORD(len(value))
            _require(self.k.QueryFullProcessImageNameW(handle,0,value,C.byref(size)))
            return ((int(created.dwHighDateTime)<<32)|int(created.dwLowDateTime), _path_hash(value.value))
        finally:
            self.k.CloseHandle(handle)

    def pairs(self):
        class ENTRY(C.Structure):
            _fields_ = [('dwSize',W.DWORD),('cntUsage',W.DWORD),('pid',W.DWORD),
                        ('heap',C.c_size_t),('module',W.DWORD),('threads',W.DWORD),
                        ('parent',W.DWORD),('priority',W.LONG),('flags',W.DWORD),('image',W.WCHAR*260)]
        first,last = self.k.Process32FirstW,self.k.Process32NextW
        for function in (first,last):
            function.argtypes,function.restype=[W.HANDLE,C.POINTER(ENTRY)],W.BOOL
        handle=self.k.CreateToolhelp32Snapshot(2,0)
        _require(handle != C.c_void_p(-1).value)
        try:
            item=ENTRY();item.dwSize=C.sizeof(item)
            result=[];present=first(handle,C.byref(item))
            while present:
                _require(len(result)<16384)
                result.append((int(item.pid),int(item.parent)))
                present=last(handle,C.byref(item))
            _require(C.get_last_error()==18)  # ERROR_NO_MORE_FILES
            return result
        finally:
            self.k.CloseHandle(handle)

    def window_owner(self, hwnd):
        _require(self.u.IsWindow(hwnd))
        pid=W.DWORD()
        _require(self.u.GetWindowThreadProcessId(hwnd,C.byref(pid)))
        identity=self.process(int(pid.value))
        return (int(pid.value),identity[0]) if identity else None

    def windows(self, identities):
        found, errors=[],[]
        def inspect(hwnd,_):
            try:
                pid=W.DWORD()
                self.u.GetWindowThreadProcessId(hwnd,C.byref(pid))
                if pid.value not in identities:
                    return True
                identity=self.process(int(pid.value))
                if identity is None or identity[0] != identities[pid.value]:
                    return True
                style=self.u.GetWindowLongW(hwnd,-16)&0xffffffff
                if style&0x40000000 or style&0x00C80000 != 0x00C80000:
                    return True
                name=C.create_unicode_buffer(128)
                _require(self.u.GetClassNameW(hwnd,name,len(name)))
                if name.value.startswith('WindowsForms10.Window'):
                    found.append((int(hwnd),int(pid.value),identity[0]))
            except Exception:
                errors.append(True)
                return False
            return True
        callback=self.callback(inspect)
        _require(self.u.EnumWindows(callback,0) and not errors)
        return found

    def restore(self, hwnd, pid, birth):
        _require(self.window_owner(hwnd)==(pid,birth))
        # A hidden WinForms form can remain iconic when SW_RESTORE is sent
        # directly. Show the exact already-owned HWND first, then de-iconify;
        # the tray restoration requires the same order on this Windows host.
        self.u.ShowWindow(hwnd,5)
        _require(self.window_owner(hwnd)==(pid,birth))
        self.u.ShowWindow(hwnd,9)
        _require(self.window_owner(hwnd)==(pid,birth))
        # Windows may reject foreground activation across process boundaries.
        # That is not a failed visible restore and must not cause the second
        # --noconsole process to show an unhandled exception dialog. Request
        # focus once; never manipulate other windows or bypass focus policy.
        self.u.SetForegroundWindow(hwnd)
        deadline=time.monotonic()+3
        stable_visible=0
        while time.monotonic()<deadline:
            _require(self.window_owner(hwnd)==(pid,birth))
            if self.u.IsWindowVisible(hwnd) and not self.u.IsIconic(hwnd):
                stable_visible+=1
                # Foreground permission is independent of a stable visible
                # restore. Both paths must observe the same two samples.
                if stable_visible>=2:
                    return True
            else:
                stable_visible=0
            time.sleep(.05)
        return False


def _root(path):
    path=Path(path)
    _require(path.is_absolute() and '..' not in path.parts)
    _owned_marker(path,PROFILE_MARKER,PROFILE_IDENTITY)
    return native_io_path(path),_path_hash(path)


class NativeInstance:
    def __init__(self, root, backend=None):
        self.root,self.namespace=_root(root)
        self.path=self.root/OWNER_FILE
        self.backend=backend or _Windows()
        self._lock=RLock()
        self._fd=None
        self._record=None

    def _check_file(self, fd):
        _safe_path(self.path,file=True)
        held,current=os.fstat(fd),self.path.stat()
        _require(stat.S_ISREG(held.st_mode) and held.st_nlink==1 and held.st_size<=MAX_BYTES
                 and _file_identity(held)==_file_identity(current))

    def _read(self, fd):
        self._check_file(fd)
        os.lseek(fd,0,0)
        raw=os.read(fd,MAX_BYTES+1)
        _require(len(raw)<=MAX_BYTES)
        return _record(raw,self.namespace) if raw else None

    def _write(self, value):
        self._check_file(self._fd)
        raw=json.dumps(value,sort_keys=True,separators=(',',':')).encode('ascii')
        _require(len(raw)<=MAX_BYTES)
        # Both running/closed and PID digit changes must retain one file
        # length. Readers may share the owner file handle while it is being
        # updated; padding avoids stale trailing JSON between write/truncate.
        # A partial native write still fails closed, with a bounded reader
        # retry below rather than accepting an incomplete ownership record.
        raw=raw.ljust(MAX_BYTES,b' ')
        os.lseek(self._fd,0,0)
        _require(os.write(self._fd,raw)==len(raw))
        os.fsync(self._fd)
        self._check_file(self._fd)

    def acquire(self):
        """Return True only with exclusive owner handle and durable record."""
        with self._lock:
            _require(self._fd is None)
            _safe_path(self.path,file=True)
            fd=self.backend.open(self.path,writable=True)
            if fd is None:
                return False
            try:
                old=self._read(fd)
                own=self.backend.process(os.getpid())
                _require(own is not None)
                if old is not None and old['phase']=='running':
                    previous=self.backend.process(old['pid'])
                    _require(previous is None or previous[0]!=old['birth'])
                value={'format':FORMAT,'root':self.namespace,'image':own[1],
                       'pid':os.getpid(),'birth':own[0],'phase':'running'}
                self._fd,fd=fd,None
                self._write(value)
                self._record=value
                return True
            except Exception:
                if self._fd is not None:
                    os.close(self._fd);self._fd=None
                raise
            finally:
                if fd is not None:
                    os.close(fd)

    def restore_existing(self, timeout=30):
        _require(type(timeout) in (int,float) and 0<timeout<=60)
        deadline=time.monotonic()+timeout
        identities={}
        pinned=None
        while time.monotonic()<deadline:
            _safe_path(self.path,file=True)
            fd=self.backend.open(self.path,writable=False)
            try:
                try:
                    value=self._read(fd)
                except NativeInstanceError:
                    # The exact owner can be writing its fixed-size record.
                    # Never accept partial data or replace a file; only retry
                    # this read until a complete authenticated record exists.
                    time.sleep(.1)
                    continue
            finally:
                os.close(fd)
            if value is None:
                time.sleep(.1)
                continue
            if value['phase']=='closed':
                # The old owner released its file lock while this warm launch
                # was waiting. A fresh exclusive acquire must arbitrate any
                # new owner; never revive a closed or unowned HWND.
                raise NativeInstanceOwnerClosed()
            if pinned is None:
                pinned=value
                own=self.backend.process(os.getpid())
                _require(own and own[1]==value['image'])
                identities[value['pid']]=value['birth']
            if value!=pinned:
                # A valid new owner may replace this exact record after the
                # old writer released its deny-write handle, while the old
                # process itself is still finishing shutdown. We cannot
                # restore this unpinned owner; only restart admission through
                # CreateFileW and validate its current unique HWND anew.
                if (value['phase']=='running' and value['root']==pinned['root']
                        and value['image']==pinned['image']
                        and self.backend.process(value['pid']) ==
                        (value['birth'],value['image'])):
                    raise NativeInstanceOwnerClosed()
                previous_identity=self.backend.process(pinned['pid'])
                if (pinned is not None
                        and (previous_identity is None or previous_identity[0]!=pinned['birth'])
                        and value['root']==pinned['root']
                        and value['image']==pinned['image']):
                    raise NativeInstanceOwnerClosed()
                _require(False)
            root=self.backend.process(value['pid'])
            if root is None or root[0]!=value['birth']:
                raise NativeInstanceOwnerClosed()
            _require(root==(value['birth'],value['image']))
            pairs=self.backend.pairs()
            changed=True
            while changed:
                changed=False
                for pid,parent in pairs:
                    if pid in identities or parent not in identities:
                        continue
                    parent_identity=self.backend.process(parent)
                    if parent_identity is None or parent_identity[0]!=identities[parent]:
                        continue
                    child=self.backend.process(pid)
                    if child is not None and child[0]>=identities[parent]:
                        identities[pid]=child[0];changed=True
            choices=self.backend.windows(identities)
            _require(len(choices)<=1)
            if choices:
                if self.backend.process(value['pid']) is None:
                    raise NativeInstanceOwnerClosed()
                _require(self.backend.process(value['pid'])==root)
                hwnd,pid,birth=choices[0]
                try:
                    restored=self.backend.restore(hwnd,pid,birth)
                except NativeInstanceError:
                    current=self.backend.process(value['pid'])
                    if current is None or current[0]!=value['birth']:
                        raise NativeInstanceOwnerClosed() from None
                    _require(current==root)
                    # A closing owner can destroy its HWND and release the
                    # exclusive handle before its root PID exits. Re-read the
                    # pinned record: only 'closed'/verified owner turnover
                    # may trigger a fresh exclusive acquire. Never restore a
                    # different, unverified window after this failure.
                    time.sleep(.1)
                    continue
                if restored:
                    # The old HWND can outlive an owner's admission record.
                    # Re-read after the native restore, before publishing a
                    # successful warm launch; a close/turnover must go back
                    # through the ordinary authenticated admission loop.
                    fd=self.backend.open(self.path,writable=False)
                    try:
                        try:
                            after_restore=self._read(fd)
                        except NativeInstanceError:
                            time.sleep(.1)
                            continue
                    finally:
                        os.close(fd)
                    if after_restore!=value:
                        time.sleep(.05)
                        continue
                    current=self.backend.process(value['pid'])
                    if current is None or current[0]!=value['birth']:
                        raise NativeInstanceOwnerClosed()
                    _require(current==root)
                    return True
                # A stale/hidden HWND is not an ownership grant. The next
                # iteration must revalidate PID, birth, record and windows.
                time.sleep(.1)
                continue
            time.sleep(.1)
        raise NativeInstanceError('起動中のウィンドウをまだ確認できません。少し後で再度開いてください。')

    def close(self):
        with self._lock:
            if self._fd is not None:
                try:
                    if self._record is not None:
                        self._write(dict(self._record,phase='closed'))
                finally:
                    os.close(self._fd)
                    self._fd=None


def claim_or_restore(root, *, timeout=30):
    """Windows-only profile entrance; None means existing owner was restored."""
    instance=NativeInstance(root)
    try:
        _require(type(timeout) in (int,float) and 0<timeout<=60)
        deadline=time.monotonic()+timeout
        for _ in range(10):
            if instance.acquire():
                return instance
            try:
                instance.restore_existing(timeout=max(0.1,deadline-time.monotonic()))
                return None
            except NativeInstanceOwnerClosed:
                # Never bypass CreateFileW's exclusive handle or alter the
                # profile on a failed retry; a different owner may now win.
                if time.monotonic()>=deadline:
                    break
                time.sleep(min(.1,max(0,deadline-time.monotonic())))
        raise NativeInstanceError('前の起動が終了中です。起動状態を確認して再試行してください。')
    except Exception:
        instance.close()
        raise
