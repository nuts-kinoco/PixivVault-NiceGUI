"""Read-only pin of an explicit, authorized frozen SQLite copy.

Signature order is (st_ino, st_size, st_mtime_ns, SHA256 digest bytes), the
SavedSimilaritySource convention. The pin never creates, copies or mutates the
source and never logs local paths or exception payloads.

On Windows the source is opened through CreateFileW with GENERIC_READ,
FILE_SHARE_READ only, OPEN_EXISTING and FILE_FLAG_OPEN_REPARSE_POINT. While
that handle is owned the kernel rejects other data writers, deletion, rename
and replacement, so the content hashed at admission stays the content of the
pinned file and check() only re-verifies cheap path/handle metadata.

Limits of that model, documented rather than hidden:
- Only file-handle mutators are excluded. A writable memory mapping created
  before the pin whose handle was already closed, raw volume or kernel
  writers and backup-privileged tools are outside this guarantee.
- Attribute-only opens (FILE_WRITE_ATTRIBUTES) are not share-checked; a
  timestamp change is still detected by check() and fails closed.
- The parent directory is not locked; new SQLite sidecars or hard links are
  detected by check() and fail closed.

Elsewhere a normal read-only descriptor is held with secure=False. Advisory
locks are not treated as mandatory, so every check() rehashes the full
content through the held descriptor, preserving the old full-SHA behavior.
"""

from __future__ import annotations

import functools
import hashlib
import hmac
import os
from pathlib import Path
import stat
from threading import RLock

from .fs_paths import native_io_path


MAX_SOURCE_BYTES = 16 * 1024 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024
SIDECARS = ("-wal", "-shm", "-journal")

_WINDOWS = os.name == "nt"
_REPARSE = 0x400
_GENERIC_READ = 0x80000000
_FILE_SHARE_READ = 0x00000001
_OPEN_EXISTING = 3
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000


class FrozenSourceError(ValueError):
    """Fixed public error text; never includes paths or OS payloads."""

    def __init__(self, message="frozen source unavailable or changed"):
        super().__init__(message)


class FrozenSourceCancelled(FrozenSourceError):
    def __init__(self):
        super().__init__("frozen source pin cancelled")


@functools.cache
def _kernel():
    import ctypes
    from ctypes import wintypes

    # A private WinDLL instance keeps these prototypes out of ctypes.windll.
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    return create, close, ctypes.c_void_p(-1).value


def _open_held(path: Path) -> int:
    """Return an owned read-only descriptor; the caller closes it exactly once."""
    native = str(native_io_path(path))
    if not _WINDOWS:
        flags = os.O_RDONLY
        for name in ("O_NOFOLLOW", "O_CLOEXEC", "O_NONBLOCK", "O_NOCTTY"):
            flags |= getattr(os, name, 0)
        return os.open(native, flags)
    import msvcrt

    create, close, invalid = _kernel()
    handle = create(native, _GENERIC_READ, _FILE_SHARE_READ, None, _OPEN_EXISTING,
                    _FILE_FLAG_OPEN_REPARSE_POINT, None)
    if handle is None or handle == invalid:
        raise FrozenSourceError()
    try:
        # On success the CRT descriptor owns the handle; os.close releases it.
        return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY | os.O_NOINHERIT)
    except BaseException:
        close(handle)
        raise


def _require_file(info) -> None:
    if (stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & _REPARSE
            or info.st_nlink != 1 or not 0 <= info.st_size <= MAX_SOURCE_BYTES):
        raise FrozenSourceError()


def _inspect(path: Path):
    """Validate the I/O spelling, every ancestor and sidecar absence."""
    native = native_io_path(path)
    for item in native.parents:
        info = os.lstat(item)
        if (stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & _REPARSE):
            raise FrozenSourceError()
    info = os.lstat(native)
    _require_file(info)
    if any(os.path.lexists(str(native) + suffix) for suffix in SIDECARS):
        raise FrozenSourceError()
    return info


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _digest(fd: int, size: int, poll) -> bytes:
    os.lseek(fd, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    total = 0
    while True:
        poll()
        block = os.read(fd, CHUNK_BYTES)
        if not block:
            break
        total += len(block)
        if total > size:
            raise FrozenSourceError()
        digest.update(block)
    if total != size:
        raise FrozenSourceError()
    return digest.digest()


def _expected(signature):
    try:
        ino, size, mtime_ns, digest = signature
    except (TypeError, ValueError):
        raise FrozenSourceError() from None
    if (any(type(value) is not int for value in (ino, size, mtime_ns))
            or ino < 0 or size < 0 or type(digest) is not bytes or len(digest) != 32):
        raise FrozenSourceError()
    return ino, size, mtime_ns, digest


class FrozenSourcePin:
    """Single-use context manager; check()/close() are safe from other threads.

    The constructor performs no filesystem access. __enter__ admits only the
    exact expected signature; any failure closes what it opened and the pin
    cannot be entered again.
    """

    def __init__(self, path, expected_signature, *, cancelled=None):
        try:
            self.path = Path(path)
        except TypeError:
            raise FrozenSourceError() from None
        if not self.path.is_absolute() or ".." in self.path.parts:
            raise FrozenSourceError()
        if self.path.name.endswith(SIDECARS):
            raise FrozenSourceError()
        if cancelled is not None and not callable(cancelled):
            raise FrozenSourceError()
        self._expected = _expected(expected_signature)
        self._cancelled = cancelled
        self._lock = RLock()
        self._fd = None
        self._secure = False
        self._used = False
        self._close_requested = False

    @property
    def secure(self) -> bool:
        with self._lock:
            return self._fd is not None and self._secure

    def _poll(self) -> None:
        if self._close_requested:
            raise FrozenSourceCancelled()
        if self._cancelled is None:
            return
        try:
            stop = bool(self._cancelled())
        except FrozenSourceCancelled:
            raise
        except Exception:
            # Unknown cancellation failures fail closed without their payload.
            raise FrozenSourceError() from None
        if stop:
            raise FrozenSourceCancelled()

    def _matches(self, held, current) -> bool:
        return (_identity(held) == _identity(current)
                and _identity(held)[1:] == self._expected[:3])

    def __enter__(self):
        with self._lock:
            if self._used or self._close_requested:
                raise FrozenSourceError()
            self._used = True
            fd = None
            try:
                before = _inspect(self.path)
                if _identity(before)[1:] != self._expected[:3]:
                    raise FrozenSourceError()
                fd = _open_held(self.path)
                held = os.fstat(fd)
                _require_file(held)
                if not self._matches(held, before):
                    raise FrozenSourceError()
                digest = _digest(fd, held.st_size, self._poll)
                if not hmac.compare_digest(digest, self._expected[3]):
                    raise FrozenSourceError()
                after = _inspect(self.path)
                if not self._matches(os.fstat(fd), after) or _identity(after) != _identity(before):
                    raise FrozenSourceError()
                self._fd, fd = fd, None
                self._secure = _WINDOWS
                return self
            except FrozenSourceError:
                raise
            except Exception:
                raise FrozenSourceError() from None
            finally:
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass

    def check(self) -> None:
        """Re-verify the held source; any mismatch releases the pin."""
        with self._lock:
            if self._fd is None:
                raise FrozenSourceError()
            try:
                held = os.fstat(self._fd)
                _require_file(held)
                if not self._matches(held, _inspect(self.path)):
                    raise FrozenSourceError()
                if not self._secure:
                    digest = _digest(self._fd, held.st_size, self._poll)
                    if not hmac.compare_digest(digest, self._expected[3]):
                        raise FrozenSourceError()
                    if not self._matches(os.fstat(self._fd), _inspect(self.path)):
                        raise FrozenSourceError()
            except FrozenSourceError:
                self._release()
                raise
            except Exception:
                self._release()
                raise FrozenSourceError() from None
            except BaseException:
                self._release()
                raise

    def _release(self) -> None:
        fd, self._fd = self._fd, None
        self._secure = False
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

    def close(self) -> None:
        # Set before waiting so an admission hash in another thread stops at
        # its next chunk instead of holding the lock for the whole file.
        self._close_requested = True
        with self._lock:
            self._release()

    def __exit__(self, exc_type, exc, traceback):
        self.close()
        return False
