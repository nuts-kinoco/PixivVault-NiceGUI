"""Owned, bounded frozen copies of one explicitly selected legacy SQLite DB."""
from __future__ import annotations

from contextlib import closing
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import stat
from threading import RLock
from uuid import uuid4

from .fs_paths import logical_path, native_io_path, sqlite_readonly_uri
from .managed_downloads import _read_bounded, _safe_path, _write
from .media_jobs import validate_readonly_boundaries
from .ugoira_storage import _owner_lock

_MARKER = '.legacy-library-copies'
_IDENTITY = b'pixivvault-legacy-library-copies-v1\n'
_NAME = re.compile(r'[0-9a-f]{32}\.sqlite\Z')
_STAGE = re.compile(r'\.(?:raw-[0-9a-f]{32}|ready-[0-9a-f]{32}\.sqlite(?:-(?:journal|wal|shm))?)\Z')
_AUXILIARY_RESERVE = 65536
_CHUNK = 1024 * 1024
_APPLICATION_ID = 0x5056434C


class LegacyCopyError(ValueError):
    """A safe snapshot could not be established; message contains no path."""


class LegacyCopyCommitUnconfirmed(LegacyCopyError):
    """Publication may have succeeded; inspect the destination before retrying."""


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns)


def _matches_published(path, size, digest):
    try:
        _safe_path(path, file=True)
        before = _identity(path.stat())
        if before[4] != size:
            return False
        calculated = hashlib.sha256()
        with path.open('rb') as stream:
            if _identity(os.fstat(stream.fileno())) != before:
                return False
            while block := stream.read(_CHUNK):
                calculated.update(block)
            if _identity(os.fstat(stream.fileno())) != before:
                return False
        return _identity(path.stat()) == before and calculated.digest() == digest
    except (OSError, ValueError):
        return False


def _sidecars_absent(path):
    for suffix in ('-wal', '-shm', '-journal'):
        try:
            Path(str(path) + suffix).lstat()
        except FileNotFoundError:
            continue
        raise LegacyCopyError('legacy database is in use')


def _require_marked_sqlite(path):
    """Read and verify the application marker without writing the file."""
    try:
        _safe_path(path, file=True)
        before = _identity(path.stat())
        _sidecars_absent(path)
        with closing(sqlite3.connect(sqlite_readonly_uri(path, immutable=True), uri=True)) as connection:
            if connection.execute('PRAGMA application_id').fetchone() != (_APPLICATION_ID,):
                raise LegacyCopyError('legacy snapshot ownership unavailable')
        if _identity(path.stat()) != before:
            raise LegacyCopyError('legacy snapshot ownership changed')
        _sidecars_absent(path)
        return before
    except (OSError, sqlite3.Error, ValueError):
        raise LegacyCopyError('legacy snapshot ownership unavailable') from None


def _require_owned_copy(path):
    """Only marked SQLite generations may be rotated or removed."""
    if _NAME.fullmatch(path.name) is None:
        raise LegacyCopyError('legacy snapshot ownership unavailable')
    return _require_marked_sqlite(path)


def _open_source(path):
    """Use a short read handle that permits ordinary delete/rename sharing."""
    if os.name != 'nt':
        return os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    import msvcrt
    from .frozen_source_pin import _kernel
    create, close, invalid = _kernel()
    handle = create(str(native_io_path(path)), 0x80000000, 0x1 | 0x2 | 0x4,
                    None, 3, 0x00200000, None)
    if handle is None or handle == invalid:
        raise LegacyCopyError('legacy database source unavailable')
    try:
        return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY | os.O_NOINHERIT)
    except BaseException:
        close(handle)
        raise


def _mutate_file(path, expected, destination=None, *, require_marker=True):
    """Move or delete the verified file by the same exclusive Windows handle."""
    if os.name != 'nt':
        raise LegacyCopyError('legacy snapshot ownership unavailable')
    import ctypes
    import msvcrt
    from ctypes import wintypes
    from .frozen_source_pin import _kernel
    create, close, invalid = _kernel()
    handle = create(str(native_io_path(path)), 0x80000000 | 0x00010000, 0x1,
                    None, 3, 0x00200000, None)
    if handle is None or handle == invalid:
        raise LegacyCopyError('legacy snapshot ownership unavailable')
    fd = None
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY | os.O_NOINHERIT)
        handle = None
        info = os.fstat(fd)
        if _identity(info) != expected or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise LegacyCopyError('legacy snapshot ownership changed')
        if require_marker:
            header = os.read(fd, 72)
            if len(header) < 72 or header[:16] != b'SQLite format 3\x00' or int.from_bytes(header[68:72], 'big') != _APPLICATION_ID:
                raise LegacyCopyError('legacy snapshot ownership unavailable')
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        operation = kernel.SetFileInformationByHandle
        operation.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        operation.restype = wintypes.BOOL
        if destination is None:
            class Disposition(ctypes.Structure):
                _fields_ = [('delete_file', ctypes.c_ubyte)]
            data = Disposition(True)
            kind = 4
        else:
            name = str(native_io_path(destination))
            encoded = name.encode('utf-16-le')
            class RenameInfo(ctypes.Structure):
                _fields_ = [('replace', ctypes.c_ubyte), ('root', wintypes.HANDLE),
                            ('length', wintypes.DWORD), ('name', ctypes.c_ubyte * (len(encoded) + 2))]
            data = RenameInfo()
            data.replace, data.root = 0, None
            data.length = len(encoded)
            ctypes.memmove(ctypes.addressof(data) + RenameInfo.name.offset, encoded, len(encoded))
            kind = 3
        if not operation(msvcrt.get_osfhandle(fd), kind, ctypes.byref(data), ctypes.sizeof(data)):
            raise LegacyCopyError('legacy snapshot move or cleanup failed')
    finally:
        if fd is not None:
            os.close(fd)
        if handle is not None:
            close(handle)


def _mutate_owned_copy(path, expected, destination=None):
    return _mutate_file(path, expected, destination)


def _publish_copy(source, target, expected):
    # Move the same verified handle without replacing an existing destination.
    _mutate_owned_copy(source, expected, target)


class LegacyLibraryCopies:
    """Own a dedicated existing root until every issued snapshot is closed.

    Source and protected directories are bound before creating any marker or
    lock file. The caller must keep this owner open until all readers close.
    """
    def __init__(self, root, *, source, protected_roots=(), max_generations=3,
                 max_bytes=4 * 1024**3):
        logical_root = logical_path(Path(root)).absolute()
        logical_source = logical_path(Path(source)).absolute()
        # Keep normal public paths unchanged while ensuring descendant stages
        # and generation names remain usable beyond the Windows path limit.
        self.root = native_io_path(logical_root) if os.name == 'nt' and len(str(logical_root).encode('utf-16-le')) // 2 >= 180 else logical_root
        self.source = native_io_path(logical_source) if os.name == 'nt' and len(str(logical_source).encode('utf-16-le')) // 2 >= 180 else logical_source
        self.max_generations = max_generations
        self.max_bytes = max_bytes
        self._guard = RLock()
        self._active = set()
        self._session_stages = {}
        self._owner = None
        self._root_identity = None
        if (not self.root.is_dir() or logical_root.drive.upper() != 'S:'
                or type(max_generations) is not int or max_generations < 1
                or type(max_bytes) is not int or max_bytes < 1):
            raise LegacyCopyError('legacy snapshot destination unavailable')
        try:
            _safe_path(self.root)
            _safe_path(self.source, file=True)
            protected = (self.source.parent,) + tuple(Path(p).absolute() for p in protected_roots if p is not None)
            validate_readonly_boundaries((self.root,), protected)
            marker = self.root / _MARKER
            # Verify ownership before creating the lock. Initialize an empty
            # root with its marker first, so interruption cannot leave only a
            # lock in an otherwise unowned directory.
            if not marker.exists():
                if any(self.root.iterdir()):
                    raise LegacyCopyError('legacy snapshot destination is not owned')
                try:
                    _write(marker, _IDENTITY)
                except FileExistsError:
                    pass  # Another initializer published the same marker.
            if _read_bounded(marker, len(_IDENTITY)) != _IDENTITY:
                raise LegacyCopyError('legacy snapshot destination is not owned')
            self._root_identity = _identity(self.root.stat())[:4]
            self._owner = _owner_lock(self.root)
            self._owner.__enter__()
            self._usable()
            self._recover_stages()
        except LegacyCopyError:
            self.close()
            raise
        except Exception:
            self.close()
            raise LegacyCopyError('legacy snapshot destination unavailable') from None

    def _usable(self):
        if self._owner is None:
            raise LegacyCopyError('legacy snapshot owner is closed')
        _safe_path(self.root)
        if _identity(self.root.stat())[:4] != self._root_identity:
            raise LegacyCopyError('legacy snapshot owner changed')
        if _read_bounded(self.root / _MARKER, len(_IDENTITY)) != _IDENTITY:
            raise LegacyCopyError('legacy snapshot destination is not owned')

    def _entries(self):
        retained, stages = [], []
        for item in self.root.iterdir():
            if item.name in {_MARKER, '.writer.lock'}:
                continue
            if not (_NAME.fullmatch(item.name) or _STAGE.fullmatch(item.name)):
                raise LegacyCopyError('legacy snapshot destination contains unknown files')
            _safe_path(item, file=True)
            info = item.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise LegacyCopyError('legacy snapshot destination contains unsafe files')
            if _NAME.fullmatch(item.name):
                _require_owned_copy(item)
            (retained if _NAME.fullmatch(item.name) else stages).append(item)
        return retained, stages

    def _recover_stages(self):
        _retained, stages = self._entries()  # Unknown names prevent all cleanup.
        checked = []
        for stage in stages:
            self._usable()
            try:
                checked.append((stage, _require_marked_sqlite(stage)))
            except LegacyCopyError:
                raise LegacyCopyError('legacy snapshot stage is unverified and preserved') from None
        for stage, identity in checked:
            self._usable()
            _mutate_owned_copy(stage, identity)

    def _read_source(self, raw, expected):
        _sidecars_absent(self.source)
        _safe_path(self.source, file=True)
        def read_at(offset, count):
            fd = _open_source(self.source)
            try:
                opened = os.fstat(fd)
                if _identity(opened) != expected or not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                    raise LegacyCopyError('legacy database changed during snapshot')
                os.lseek(fd, offset, os.SEEK_SET)
                data = os.read(fd, count)
                if _identity(os.fstat(fd)) != expected:
                    raise LegacyCopyError('legacy database changed during snapshot')
                return data
            finally:
                os.close(fd)
        digest = hashlib.sha256()
        total = 0
        with raw.open('xb') as target:
            info = os.fstat(target.fileno())
            self._session_stages[raw] = (info.st_dev, info.st_ino)
            while True:
                block = read_at(total, min(_CHUNK, expected[4] + 1 - total))
                if not block:
                    break
                total += len(block)
                if total > expected[4]:
                    raise LegacyCopyError('legacy database changed during snapshot')
                digest.update(block)
                target.write(block)
            target.flush()
            os.fsync(target.fileno())
        if total != expected[4]:
            raise LegacyCopyError('legacy database changed during snapshot')
        second = hashlib.sha256()
        remaining = expected[4]
        while remaining:
            block = read_at(expected[4] - remaining, min(_CHUNK, remaining))
            if not block:
                raise LegacyCopyError('legacy database changed during snapshot')
            second.update(block)
            remaining -= len(block)
        if read_at(expected[4], 1) or second.digest() != digest.digest():
            raise LegacyCopyError('legacy database changed during snapshot')
        _safe_path(self.source, file=True)
        if _identity(self.source.stat()) != expected:
            raise LegacyCopyError('legacy database changed during snapshot')
        _sidecars_absent(self.source)
        return digest.digest()

    def snapshot(self):
        with self._guard:
            self._usable()
            token = uuid4().hex
            raw = self.root / ('.raw-' + token)
            ready = self.root / ('.ready-' + token + '.sqlite')
            published = False
            try:
                _safe_path(self.source, file=True)
                _sidecars_absent(self.source)
                initial = self.source.stat()
                if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1:
                    raise LegacyCopyError('legacy database source unavailable')
                retained, stages = self._entries()
                if stages:
                    raise LegacyCopyError('legacy snapshot stage cleanup required')
                retained.sort(key=lambda p: (p.stat().st_mtime_ns, p.name))
                total = (sum(p.stat().st_size for p in retained)
                         + (self.root / _MARKER).stat().st_size
                         + (self.root / '.writer.lock').stat().st_size)
                if (initial.st_size > self.max_bytes or
                        total + 2 * initial.st_size + _AUXILIARY_RESERVE > self.max_bytes):
                    raise LegacyCopyError('legacy snapshot exceeds capacity')
                victim = None
                if len(retained) >= self.max_generations:
                    victim = next((p for p in retained if p not in self._active), None)
                    if victim is None:
                        raise LegacyCopyError('legacy snapshot generation limit reached')
                source_digest = self._read_source(raw, _identity(initial))
                self._usable()
                with ready.open('xb') as ready_guard:
                    info = os.fstat(ready_guard.fileno())
                    self._session_stages[ready] = (info.st_dev, info.st_ino)
                    with closing(sqlite3.connect(sqlite_readonly_uri(raw, immutable=True), uri=True)) as origin:
                        with closing(sqlite3.connect(ready)) as destination:
                            destination.execute('PRAGMA journal_mode=OFF')
                            destination.execute('PRAGMA temp_store=MEMORY')
                            page_size = origin.execute('PRAGMA page_size').fetchone()[0]
                            def progress(_status, _remaining, pages):
                                if pages * page_size > initial.st_size:
                                    raise LegacyCopyError('legacy snapshot exceeds capacity')
                            origin.backup(destination, pages=128, progress=progress)
                    if (os.fstat(ready_guard.fileno()).st_dev, os.fstat(ready_guard.fileno()).st_ino) != self._session_stages[ready]:
                        raise LegacyCopyError('legacy snapshot stage changed')
                for checked_path in (raw, ready):
                    with closing(sqlite3.connect(sqlite_readonly_uri(checked_path, immutable=True), uri=True)) as checked:
                        if checked.execute('PRAGMA quick_check').fetchone() != ('ok',):
                            raise LegacyCopyError('legacy snapshot integrity check failed')
                        columns = {row[1] for row in checked.execute('PRAGMA table_info(works)')}
                        authors = {row[1] for row in checked.execute('PRAGMA table_info(following_users)')}
                        if not {'work_id', 'title', 'page_count', 'user_id', 'tags', 'is_deleted'} <= columns or not {'user_id', 'name'} <= authors:
                            raise LegacyCopyError('legacy snapshot schema unsupported')
                        checked.execute('SELECT COUNT(*) FROM works').fetchone()
                    _sidecars_absent(checked_path)
                digest = hashlib.sha256()
                raw_before = _identity(raw.stat())
                with raw.open('rb') as stream:
                    while block := stream.read(_CHUNK):
                        digest.update(block)
                if (raw_before != _identity(raw.stat()) or raw_before[4] != initial.st_size
                        or digest.digest() != source_digest):
                    raise LegacyCopyError('legacy snapshot content changed')
                with raw.open('rb') as raw_guard:
                    info = os.fstat(raw_guard.fileno())
                    if (info.st_dev, info.st_ino) != self._session_stages.get(raw):
                        raise LegacyCopyError('legacy snapshot stage changed')
                    with closing(sqlite3.connect(raw)) as marked:
                        marked.execute('PRAGMA journal_mode=OFF')
                        marked.execute(f'PRAGMA application_id={_APPLICATION_ID}')
                        marked.execute('PRAGMA user_version=1')
                        marked.commit()
                _sidecars_absent(raw)
                with closing(sqlite3.connect(sqlite_readonly_uri(raw, immutable=True), uri=True)) as marked:
                    if (marked.execute('PRAGMA application_id').fetchone() != (_APPLICATION_ID,)
                            or marked.execute('PRAGMA user_version').fetchone() != (1,)
                            or marked.execute('PRAGMA quick_check').fetchone() != ('ok',)):
                        raise LegacyCopyError('legacy snapshot ownership unavailable')
                marked_size = raw.stat().st_size
                marked_digest = hashlib.sha256()
                with raw.open('rb') as stream:
                    while block := stream.read(_CHUNK):
                        marked_digest.update(block)
                raw_identity = _identity(raw.stat())
                if ((raw_identity[0], raw_identity[1]) != self._session_stages.get(raw)
                        or not _matches_published(raw, marked_size, marked_digest.digest())):
                    raise LegacyCopyError('legacy snapshot stage changed')
                if total + raw.stat().st_size + ready.stat().st_size > self.max_bytes:
                    raise LegacyCopyError('legacy snapshot exceeds capacity')
                with raw.open('r+b') as stream:
                    stream.flush()
                    os.fsync(stream.fileno())
                self._usable()
                if _identity(self.source.stat()) != _identity(initial):
                    raise LegacyCopyError('legacy database changed during snapshot')
                _sidecars_absent(self.source)
                target = victim if victim is not None else self.root / (uuid4().hex + '.sqlite')
                backup = None
                if victim is not None:
                    victim_identity = _require_owned_copy(victim)
                    backup = self.root / (uuid4().hex + '.sqlite')
                    _mutate_owned_copy(victim, victim_identity, backup)
                try:
                    _publish_copy(raw, target, raw_identity)
                except (OSError, LegacyCopyError):
                    if raw.exists():
                        if backup is not None:
                            try:
                                _mutate_owned_copy(backup, _require_owned_copy(backup), victim)
                            except (OSError, ValueError):
                                raise LegacyCopyCommitUnconfirmed('legacy snapshot publication is unconfirmed') from None
                        raise
                    if not _matches_published(target, marked_size, marked_digest.digest()):
                        raise LegacyCopyCommitUnconfirmed('legacy snapshot publication is unconfirmed') from None
                published = True
                self._active.add(target)
                # Keep room for a later snapshot when older unused copies can
                # be retired; the newly published and active copies stay safe.
                try:
                    published, _stages = self._entries()
                    published.sort(key=lambda path: (path.stat().st_mtime_ns, path.name))
                    used = (sum(path.stat().st_size for path in published)
                            + (self.root / _MARKER).stat().st_size
                            + (self.root / '.writer.lock').stat().st_size)
                    remaining = len(published)
                    for old in published:
                        if (remaining <= self.max_generations and
                                used + 2 * target.stat().st_size + _AUXILIARY_RESERVE <= self.max_bytes):
                            break
                        if old in self._active:
                            continue
                        self._usable()
                        old_identity = _require_owned_copy(old)
                        length = old.stat().st_size
                        _mutate_owned_copy(old, old_identity)
                        remaining -= 1
                        used -= length
                except (OSError, ValueError):
                    # Publication already succeeded; verify the hard count below.
                    pass
                try:
                    current, _stages = self._entries()
                    if len(current) > self.max_generations:
                        raise LegacyCopyCommitUnconfirmed('legacy snapshot generation cleanup is unconfirmed')
                except LegacyCopyCommitUnconfirmed:
                    raise
                except (OSError, ValueError):
                    raise LegacyCopyCommitUnconfirmed('legacy snapshot generation cleanup is unconfirmed') from None
                return target
            except LegacyCopyError:
                raise
            except Exception:
                raise LegacyCopyError('legacy snapshot unavailable') from None
            finally:
                import sys
                cleanup_failed = False
                for stage in (raw, ready):
                    try:
                        recorded = self._session_stages.pop(stage, None)
                        if recorded is not None and stage.exists():
                            _safe_path(stage, file=True)
                            info = stage.stat()
                            if (info.st_dev, info.st_ino) != recorded:
                                raise LegacyCopyError('legacy snapshot stage changed')
                            # Same-user replacement during cleanup is outside the threat model.
                            _mutate_file(stage, _identity(info), require_marker=False)
                    except (OSError, ValueError):
                        cleanup_failed = True
                if cleanup_failed:
                    active = sys.exc_info()[1]
                    if active is not None and not isinstance(active, Exception):
                        active.add_note('legacy snapshot stage is unverified and preserved')
                    elif published:
                        raise LegacyCopyCommitUnconfirmed('legacy snapshot stage is unverified and preserved') from None
                    else:
                        raise LegacyCopyError('legacy snapshot stage is unverified and preserved') from None

    def close(self):
        with self._guard:
            owner, self._owner = self._owner, None
            if owner is not None:
                owner.__exit__(None, None, None)
