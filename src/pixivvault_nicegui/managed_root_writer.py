"""Cross-process exclusion for all mutations of one owned managed root.

The lifetime of an object does not establish writer ownership: previewers and
restorers may open additional ManagedPixivDownloads instances. A write holds
this fence from revision admission until all affected paths are settled.
"""
from __future__ import annotations

from contextlib import contextmanager
from functools import wraps
import hashlib
import os
from pathlib import Path
from threading import RLock, local

from .fs_paths import native_io_path

_THREAD = local()
_REGISTRY_LOCK = RLock()
_ROOT_LOCKS = {}


def _after_fork():
    """Inherited thread locks are not valid ownership proofs in a child."""
    global _THREAD, _REGISTRY_LOCK, _ROOT_LOCKS
    _THREAD = local()
    _REGISTRY_LOCK = RLock()
    _ROOT_LOCKS = {}


if hasattr(os, 'register_at_fork'):
    os.register_at_fork(after_in_child=_after_fork)


@contextmanager
def _local_fence(lock, blocking):
    from .managed_downloads import ManagedDownloadError
    if not lock.acquire(blocking=blocking):
        raise ManagedDownloadError('managed root writer fence busy')
    try:
        yield
    finally:
        lock.release()


@contextmanager
def writer_fence(root, *, blocking=True):
    """Acquire an OS-wide, recursive per-root mutex (never writes original data)."""
    from .managed_downloads import ManagedDownloadError, MARKER

    path = native_io_path(Path(root))
    key = os.path.normcase(str(path.resolve())).casefold()
    with _REGISTRY_LOCK:
        local_lock = _ROOT_LOCKS.setdefault(key, RLock())
    with _local_fence(local_lock, blocking):
        depths = getattr(_THREAD, 'depths', None)
        if depths is None:
            depths = {}
            _THREAD.depths = depths
        if depths.get(key, 0):
            depths[key] += 1
            try:
                yield
            finally:
                depths[key] -= 1
            return

        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes

            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            create = kernel.CreateMutexW
            create.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
            create.restype = wintypes.HANDLE
            wait = kernel.WaitForSingleObject
            wait.argtypes = (wintypes.HANDLE, wintypes.DWORD)
            wait.restype = wintypes.DWORD
            release = kernel.ReleaseMutex
            release.argtypes = (wintypes.HANDLE,)
            release.restype = wintypes.BOOL
            close = kernel.CloseHandle
            close.argtypes = (wintypes.HANDLE,)
            close.restype = wintypes.BOOL
            digest = hashlib.sha256(key.encode('utf-16le')).hexdigest()
            handle = create(None, False, 'Global\\PixivVaultManagedRoot_' + digest)
            if not handle:
                raise ManagedDownloadError('managed root writer fence unavailable')
            acquired = False
            try:
                if wait(handle, 30000 if blocking else 0) not in (0, 0x80):
                    raise ManagedDownloadError('managed root writer fence busy')
                acquired = True
                depths[key] = 1
                try:
                    yield
                finally:
                    del depths[key]
            finally:
                if acquired:
                    release(handle)
                close(handle)
        else:
            import fcntl

            fd = None
            try:
                try:
                    fd = os.open(str(path / MARKER), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
                    fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
                    # Fail closed if an external actor replaced the lock file.
                    marker = os.stat(str(path / MARKER), follow_symlinks=False)
                    original = os.fstat(fd)
                    if (marker.st_dev, marker.st_ino) != (original.st_dev, original.st_ino):
                        raise OSError('managed writer marker identity changed')
                except OSError:
                    raise ManagedDownloadError('managed root writer fence unavailable') from None
                depths[key] = 1
                try:
                    yield
                finally:
                    del depths[key]
            finally:
                if fd is not None:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                    os.close(fd)


def fenced_writer(method):
    """Hold the same lock for nested generations and quarantine mutations."""
    @wraps(method)
    def call(self, *args, **kwargs):
        owner = getattr(self, 'owner', None)
        with writer_fence(owner.root if owner is not None else self.root):
            return method(self, *args, **kwargs)
    return call
