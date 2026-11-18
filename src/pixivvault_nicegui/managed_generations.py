"""Non-destructive CAS index for immutable managed Pixiv image generations.

Old v1 `outputs/` directories are never renamed or changed. Indexed v2
`generations/` directories become active only after their receipt was fully
written and verified. Unindexed generation folders are harmless crash orphans.
"""
from __future__ import annotations

from contextlib import contextmanager

import hashlib
import json
import os
import stat
import time
import zlib
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import re
from threading import RLock
from uuid import uuid4

from .fs_paths import logical_path, native_io_path
from .managed_root_writer import bootstrap_fenced_init, fenced_writer, writer_fence
from .managed_downloads import (IDENTITY, MARKER, MAX_RECEIPT_BYTES, MAX_PAGE_BYTES,
                                MAX_PAGES, MAX_PIXELS, MAX_TOTAL, _image,
                                ManagedDownloadError, ManagedPageQuarantined, LegacyV5Uncertain,
                                _fsync_dir, _json_unique,
                                _read_bounded, _safe_path, _write)


_ID = re.compile(r'[1-9][0-9]{0,19}\Z')
_SHA = re.compile(r'[a-f0-9]{64}\Z')
_FOLDER = re.compile(r'work-([1-9][0-9]{0,19})-[a-f0-9]{32}\Z')
_GEN_REL = re.compile(r'generations/work-([1-9][0-9]{0,19})-[a-f0-9]{32}/receipt\.json\Z')
_INDEX_KEYS = frozenset({'version', 'entries'})
_ENTRY_KEYS = frozenset({'work_id', 'relative_receipt', 'sha256', 'update_date'})
_ENTRY_WITH_JOB = _ENTRY_KEYS | {'accepted_fingerprint'}
_RECEIPT_KEYS = frozenset({'version', 'source', 'work_id', 'author_id',
                           'author_name', 'title', 'tags', 'created_at', 'saved_at',
                           'illustration_type', 'x_restrict', 'page_count', 'files'})
_FILE_KEYS = frozenset({'name', 'bytes', 'sha256', 'format', 'width', 'height'})
# Capacity. The old limits (10,000 entries, a 2 MiB index file) stopped a first full
# download of a large library, and the file limit bound first: ~340 bytes per entry meant
# the store failed at about 6,000 works while `_MAX_ENTRIES` still read 10,000.
_MAX_ENTRIES = 1_000_000
_MAX_INDEX_BYTES = 1024 * 1024 * 1024
_ENTRY_BYTES_ESTIMATE = 400            # JSON bytes per indexed entry, rounded up
# Appends go to a journal instead of rewriting the whole index for every saved work. The
# journal is folded into the index (one atomic rewrite) when either bound is reached.
# Version 5 index: names its journal by id (`journal.id`), so a build that only knows versions 1-4
# refuses the root instead of silently ignoring the journal and reverting updated works. The id
# changes at every fold; the journal and its `.mark` (records, chain) are created and fsynced
# BEFORE the index that points at them, so a missing, emptied or shortened journal is detectable.
_JOURNAL = 'active-generations.journal'  # obsolete name, rejected before opening
_JOURNAL_FILE = 'active-generations-%s.journal'
_MARK_FILE = 'active-generations-%s.mark'
_V5_COMMITTED = 'active-generations.v5-committed'
_V5_DONE = b'pixivvault generation v5 committed\n'
_V5_HEAD = 'active-generations.head'
_GENERATION_FLOOR = 'active-generations.floor'
_V5_SEQUENCE_GUARD = 'active-generations.sequence-required'
_V5_SEQUENCE_DONE = b'pixivvault generation sequence required\n'
_JOURNAL_NAME = re.compile(r'active-generations-([a-f0-9]{32})\.(journal|mark)\Z')
_JOURNAL_RETAIN_SECONDS = 120.0       # how long a folded-away journal stays for readers of the old index
_JOURNAL_RECORD_ESTIMATE = 600           # bytes one journal record takes, rounded up
_JOURNAL_SLACK = 1024 * 1024               # headroom kept below the journal ceiling for the fold to run
_JOURNAL_KIND = 'pixivvault-generation-journal'
_COMPACT_RECORDS = 2048
_COMPACT_BYTES = 2 * 1024 * 1024
_MAX_JOURNAL_BYTES = 64 * 1024 * 1024
_PARALLEL_FROM = 2000                  # entries from which the open-time verification is threaded
_VERIFY_THREADS = 8
_QUARANTINE_KEYS = _INDEX_KEYS | {'exclusions', 'quarantine_operations'}
_V5_KEYS = _QUARANTINE_KEYS | {'journal'}
_OPERATION_KEYS = frozenset({'manifest', 'manifest_sha256', 'selection_sha256', 'state'})
_MOVE_OPERATION_KEYS = _OPERATION_KEYS | {'mode'}
_OPERATION_ID = re.compile(r'[a-f0-9]{32}\Z')
_QUARANTINE_FILE_KEYS = frozenset({
    'work_id', 'page_no', 'relative_receipt', 'receipt_sha256',
    'sha256', 'bytes', 'format', 'width', 'height', 'file',
})
_MAX_QUARANTINE_ITEMS = 250


def _id(value: object) -> str:
    if type(value) is not str or not _ID.fullmatch(value):
        raise ManagedDownloadError('generation work ID rejected')
    return value


def _sha(value: object) -> str:
    if type(value) is not str or not _SHA.fullmatch(value):
        raise ManagedDownloadError('generation checksum rejected')
    return value


def _date(value: object, *, allow_empty: bool = False) -> str:
    if (type(value) is not str or len(value) > 128 or (not allow_empty and not value)
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ManagedDownloadError('generation update date rejected')
    return value


def _identity(path):
    """Cheap file identity; None when absent. A different value forces a full re-read."""
    try:
        info = os.lstat(native_io_path(path))
    except FileNotFoundError:
        return None
    return (info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, stat.S_IFMT(info.st_mode))


def _posix_replace(source, target):
    """Atomic replace that also works while readers hold the target open with FILE_SHARE_DELETE.

    `os.replace` (MoveFileEx) refuses any target that is open, even for a shared-delete reader.
    Windows 10 (1709+) renames with POSIX semantics instead: the name switches at once and open
    readers keep the file they opened. Returns False where that is not available."""
    if os.name != 'nt':
        native_io_path(source).replace(native_io_path(target))
        return True
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    handle = create(str(native_io_path(source)), 0x00010000 | 0x00100000, 0x7, None, 3, 0x80, None)
    if handle in (None, wintypes.HANDLE(-1).value):
        return False
    try:
        name = str(native_io_path(target))
        raw = name.encode('utf-16-le')
        pointer = ctypes.sizeof(ctypes.c_void_p)
        length_at = pointer * 2                       # Flags (+pad), RootDirectory, then FileNameLength
        buffer = ctypes.create_string_buffer(length_at + 4 + len(raw) + 2)
        ctypes.c_uint32.from_buffer(buffer, 0).value = 0x1 | 0x2         # REPLACE_IF_EXISTS | POSIX_SEMANTICS
        ctypes.c_uint32.from_buffer(buffer, length_at).value = len(raw)
        ctypes.memmove(ctypes.addressof(buffer) + length_at + 4, raw, len(raw))
        set_info = kernel.SetFileInformationByHandle
        set_info.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
        set_info.restype = wintypes.BOOL
        return bool(set_info(handle, 22, buffer, len(buffer)))           # FileRenameInfoEx
    finally:
        kernel.CloseHandle(handle)


def _replace_retrying(source, target, patience=5.0):
    """Replace `target` with `source`, waiting at most `patience` seconds out another process that
    holds it open without sharing delete (Windows). Callers hold the writer fence, so the budget is
    kept small for work that can be done later."""
    try:
        if _posix_replace(source, target):
            return
    except OSError:
        pass
    deadline = time.monotonic() + patience
    delay = 0.005
    while True:
        try:
            native_io_path(source).replace(native_io_path(target))
            return
        except PermissionError:
            if time.monotonic() + delay > deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 1.5, 0.1)


def _open_shared(path):
    """Open for reading WITHOUT blocking a replace or delete of the file by a writer.

    The index, journal and mark are replaced by whoever saves next. On Windows a plain open() makes
    that replace fail while any reader is inside, and a reader that keeps re-opening can starve the
    writer for seconds. With FILE_SHARE_DELETE the writer wins at once and the reader goes on reading
    the file it opened (a consistent snapshot); on other systems open() already behaves that way."""
    io_path = native_io_path(path)
    if os.name != 'nt':
        return io_path.open('rb')
    import ctypes
    import msvcrt
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    handle = create(str(io_path), 0x80000000, 0x7, None, 3, 0x80, None)   # READ; share R|W|DELETE
    if handle in (None, wintypes.HANDLE(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY)
    except OSError:
        kernel.CloseHandle(handle)
        raise
    return os.fdopen(descriptor, 'rb')


def _read_shared_identity(path, limit):
    """`_read_bounded` through `_open_shared` (same checks: a regular, unlinked file within `limit`),
    also returning the identity of the very file that was read (not of whatever the name points to
    a moment later)."""
    _safe_path(path, file=True)
    with _open_shared(path) as stream:
        info = os.fstat(stream.fileno())
        # `_safe_path(file=True)` above already required a single link on the NAME. A handle opened a
        # moment before a writer replaced the file reports 0 links (delete-pending): that is a normal
        # replace in progress, not a hard link, so 0 and 1 are both fine here.
        if not stat.S_ISREG(info.st_mode) or info.st_nlink not in (0, 1) or info.st_size > limit:
            raise ManagedDownloadError('managed file rejected')
        # Reading the entire ceiling allocates up to 64 MiB even for an empty
        # journal. Read the observed size plus one byte and reject a concurrent
        # resize; callers retry against a coherent snapshot.
        data = stream.read(min(limit + 1, info.st_size + 1))
        if len(data) != info.st_size or os.fstat(stream.fileno()).st_size != info.st_size:
            raise OSError('managed file changed during read')
    if len(data) > limit:
        raise ManagedDownloadError('managed file rejected')
    mine = (info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, stat.S_IFMT(info.st_mode))
    named = _identity(path)
    # The name still pointing at the file just read: use the identity every later freshness check will
    # compute for it. Otherwise (replaced meanwhile) keep the handle's own identity, which can never
    # match the new file, so the next call reads the newer one.
    if named is not None and named[0] == mine[0] and named[1] == mine[1]:
        return data, named
    return data, mine


def _read_shared(path, limit):
    return _read_shared_identity(path, limit)[0]


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode('ascii')


def _entry_hash(entry) -> int:
    return int.from_bytes(hashlib.sha256(_canonical(entry)).digest(), 'big')


_MOD = 1 << 256


class _EntriesView(Mapping):
    """Read-only snapshot of the active entries. Every value read is a fresh copy.

    The underlying dict is replaced (copy on write) before any later change, so a view
    taken earlier never moves under its holder.
    """
    __slots__ = ('_data',)

    def __init__(self, data):
        self._data = data

    def __getitem__(self, key):
        return dict(self._data[key])

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)

    def __contains__(self, key):
        return key in self._data

    def get(self, key, default=None):
        value = self._data.get(key)
        return default if value is None else dict(value)


class _Loaded:
    """Verified in-memory copy of the index (+ journal) and the file identities it came from."""
    __slots__ = ('version', 'base_sig', 'base_digest', 'base_bytes', 'indexed', 'legacy', 'legacy_names', 'active',
                 'exclusions', 'operations', 'out_sig', 'journal_sig', 'journal_offset',
                 'journal_chain', 'journal_records', 'acc', 'shared',
                 'jid', 'jpath', 'gen_sig', 'mark_records', 'fold_failed', 'header_bytes',
                 'journal_hasher', 'journal_chains', 'generation', 'parent_digest', 'head_records', 'head_chain',
                 'legacy_signature', 'witness_directory', 'witness_entries')

    def __init__(self):
        self.version = None
        self.base_sig = self.base_digest = self.journal_sig = self.journal_chain = self.out_sig = None
        self.base_bytes = self.journal_offset = self.journal_records = 0
        self.indexed, self.legacy, self.active = {}, {}, {}
        self.legacy_names = {}
        self.exclusions, self.operations = {}, {}
        self.shared = False
        self.jid = self.jpath = self.gen_sig = None
        self.header_bytes = 0
        self.mark_records = 0       # `records` the mark file holds (the journal may be ahead of it)
        self.journal_hasher = None
        self.journal_chains = []
        self.generation = self.parent_digest = None
        self.head_records = self.head_chain = None
        self.legacy_signature = None
        self.witness_directory, self.witness_entries = None, ()
        self.fold_failed = False    # the last attempt to fold the journal into the index failed
        self.acc = 0

    @property
    def qversion(self):
        """The quarantine-field dialect (3 or 4) of this state; v5 carries either."""
        return 4 if any(type(op) is dict and 'mode' in op for op in self.operations.values()) else 3

    def qpacket(self):
        if self.version is None:
            return None
        return {'version': self.qversion, 'entries': {}, 'exclusions': self.exclusions,
                'quarantine_operations': self.operations}


def _reject_legacy_files(root):
    """Reject obsolete journal names and interrupted legacy upgrades."""
    if any(root.glob('active-generations.journal*')):
        raise LegacyV5Uncertain()
    marker = root / _V5_COMMITTED
    if _identity(marker) is not None:
        try:
            if _read_shared(marker, 256) != _V5_DONE:
                raise LegacyV5Uncertain()
        except OSError:
            raise LegacyV5Uncertain() from None


def reject_uncertain_old_v5(root):
    """Refuse unsupported indexes before creating or recovering anything."""
    _reject_legacy_files(root)
    index = root / 'active-generations.json'
    if _identity(index) is None:
        return
    try:
        packet = json.loads(_read_shared(index, _MAX_INDEX_BYTES), object_pairs_hook=_json_unique)
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
        raise ManagedDownloadError('active generation index rejected') from None
    if (type(packet) is not dict or type(packet.get('version')) is not int or packet['version'] != 5
            or type(packet.get('journal')) is not dict or 'generation' not in packet['journal']):
        raise LegacyV5Uncertain()


@contextmanager
def _index_creation_guard(root):
    """Pin an existing index or reserve its absent name while creating folders."""
    reject_uncertain_old_v5(root)
    path = native_io_path(root / 'active-generations.json')
    handle = None
    reserved = False
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                           wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        create.restype = wintypes.HANDLE
        invalid = (None, wintypes.HANDLE(-1).value)
        for _ in range(8):
            handle = create(str(path), 0x80000000, 0x1, None, 3, 0x80, None)
            if handle not in invalid:
                break
            error = ctypes.get_last_error()
            if error not in (2, 3):
                raise ctypes.WinError(error)
            # CREATE_NEW atomically reserves an absent name. DELETE_ON_CLOSE
            # removes the empty reservation even after an interrupted process.
            handle = create(str(path), 0x80000000 | 0x00010000, 0x1, None,
                            1, 0x04000100, None)
            if handle not in invalid:
                reserved = True
                break
            error = ctypes.get_last_error()
            if error not in (80, 183):
                raise ctypes.WinError(error)
        else:
            raise ManagedDownloadError('active generation index rejected')
    try:
        if not reserved:
            reject_uncertain_old_v5(root)
        yield reserved
    finally:
        if handle is not None:
            kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel.CloseHandle(handle)


class ManagedGenerations:
    """Index is owned by one managed-root writer; all returned data is copied.

    The index file is the verified base; `active-generations.journal` carries the entries
    published since (one fsynced line each, hash-chained to the base). The whole thing is
    verified once at open and kept in memory; later calls only compare file identities, so
    a call costs O(quarantine operations), not O(works). Receipts are immutable and are
    re-verified wherever their bytes are actually used.

    Partial rollback of the index, head, journal or mark and loss of a later
    same-generation append are detected. Prepared folds and interrupted appends
    are distinct from committed evidence and can be recovered. Restoring an
    entire root together to an earlier consistent snapshot cannot be detected.
    """

    @classmethod
    def _verify_before_directory_creation(cls, root, *, reserved=False):
        """Check existing evidence without creating missing managed folders."""
        absent = reserved or _identity(root / 'active-generations.json') is None
        probe = cls.__new__(cls)
        probe.root = root
        probe.output_dir = root / 'outputs'
        probe.generation_dir = root / 'generations'
        probe.stage_dir = root / 'generation-incomplete'
        probe.index_path = root / 'active-generations.json'
        probe._allow_missing_dirs = True
        probe._unconfirmed_operations = set()
        probe._loaded = None
        if absent:
            allowed = {MARKER, 'outputs', 'generations', 'generation-incomplete', 'incomplete'}
            for child in native_io_path(root).iterdir():
                if child.name == 'active-generations.json' and reserved:
                    continue
                if child.name == MARKER:
                    continue
                if child.name not in allowed or not child.is_dir():
                    raise ManagedDownloadError('active generation index missing')
                _safe_path(child)
                for item in child.iterdir():
                    if (child.name != 'generation-incomplete'
                            or re.fullmatch(r'(?:prepared-[a-f0-9]{32}\.(?:journal|mark)|'
                                            r'(?:active|floor|head)-[a-f0-9]{32}\.tmp)', item.name) is None):
                        raise ManagedDownloadError('active generation index missing')
                    _safe_path(item, file=True)
            probe._legacy()
            if (probe._scan_dir(probe.generation_dir, 'generation folder structure rejected')
                    or probe._has_quarantine_records()
                    or any(_identity(root / name) is not None for name in
                           (_V5_HEAD, _V5_COMMITTED, _V5_SEQUENCE_GUARD, _JOURNAL))
                    or any(root.glob('active-generations-*.journal'))
                    or any(root.glob('active-generations-*.mark'))):
                raise ManagedDownloadError('active generation index missing')
        else:
            probe._load_full()
            raise ManagedDownloadError('managed storage folder missing')

    @bootstrap_fenced_init
    def __init__(self, root: Path):
        self.root = Path(root)
        if not self.root.is_absolute() or not native_io_path(self.root).is_dir():
            raise ManagedDownloadError('generation root rejected')
        _safe_path(self.root)
        self.root = logical_path(native_io_path(self.root).resolve())
        if json.loads(_read_bounded(self.root / MARKER, 4096),
                      object_pairs_hook=_json_unique) != IDENTITY:
            raise ManagedDownloadError('generation root rejected')
        self.output_dir = self.root / 'outputs'
        self.generation_dir = self.root / 'generations'
        self.stage_dir = self.root / 'generation-incomplete'
        self.index_path = self.root / 'active-generations.json'
        folders = (self.output_dir, self.generation_dir, self.stage_dir)
        if _identity(self.index_path) is None:
            folders += (self.root / 'incomplete',)
        reject_uncertain_old_v5(self.root)
        if (_identity(self.index_path) is None
                and _identity(self.root / _V5_HEAD) is None
                and _identity(self.root / _GENERATION_FLOOR) is None):
            self._verify_before_directory_creation(self.root)
        if any(not native_io_path(folder).is_dir() for folder in folders):
            self._verify_before_directory_creation(self.root)
            with _index_creation_guard(self.root) as reserved:
                self._verify_before_directory_creation(self.root, reserved=reserved)
                for folder in folders:
                    _safe_path(folder)
                    native_io_path(folder).mkdir(exist_ok=True)
                    _safe_path(folder)
        else:
            for folder in folders:
                _safe_path(folder)
        self._lock = RLock()
        self._visibility_cache = None
        self._loaded = None
        self._unconfirmed_operations = set()
        self._recover_if_pending()
        # A malformed existing index must block startup, never be reset.
        self.entries()
        if _identity(self.index_path) is None:
            self.prepare_for_publication()

    def _read_head(self):
        guard = self.root / _V5_SEQUENCE_GUARD
        path = self.root / _V5_HEAD
        guarded = _identity(guard) is not None
        if guarded:
            try:
                if _read_shared(guard, 256) != _V5_SEQUENCE_DONE:
                    raise ValueError()
            except (OSError, ValueError):
                raise ManagedDownloadError('active generation index rejected') from None
        if _identity(path) is None:
            if guarded:
                raise ManagedDownloadError('active generation index rejected')
            return None
        try:
            head = json.loads(_read_shared(path, 4096), object_pairs_hook=_json_unique)
            if (type(head) is dict and head.keys() == {'generation', 'id', 'digest', 'candidate'}):
                raise LegacyV5Uncertain()
            if (type(head) is not dict
                    or head.keys() != {'generation', 'id', 'digest', 'candidate', 'records', 'chain'}
                    or type(head['generation']) is not int or head['generation'] < 1
                    or type(head['id']) is not str or re.fullmatch(r'[a-f0-9]{32}', head['id']) is None
                    or type(head['digest']) is not str or not _SHA.fullmatch(head['digest'])
                    or type(head['candidate']) is not str
                    or re.fullmatch(r'active-[a-f0-9]{32}\.tmp', head['candidate']) is None):
                raise ValueError()
            if (type(head['records']) is not int or head['records'] < 0
                    or type(head['chain']) is not str or not _SHA.fullmatch(head['chain'])):
                raise ValueError()
            return head
        except (OSError, ValueError, TypeError, KeyError, UnicodeError) as error:
            if isinstance(error, LegacyV5Uncertain):
                raise
            raise ManagedDownloadError('active generation index rejected') from None

    def _recover_head(self):
        """Finish a committed index replacement interrupted after its head was persisted."""
        # Readers may already hold another application lock. Never wait for a
        # writer which could be waiting for that lock in the opposite order.
        with writer_fence(self.root, blocking=False):
            self._recover_head_locked()

    def _recover_head_locked(self):
        head = self._read_head()
        floor = self._read_floor()
        if floor is not None and (head is None or floor['generation'] > head['generation']):
            candidate = self.stage_dir / floor['candidate']
            if _identity(candidate) is None:
                raise ManagedDownloadError('active generation index rejected')
            self._install_prepared(floor['id'], floor['generation'])
            self._write_head(floor)
            head = floor
        if head is None:
            return
        try:
            if (_identity(self.index_path) is not None and
                    hashlib.sha256(_read_shared(self.index_path, _MAX_INDEX_BYTES)).hexdigest() == head['digest']):
                return
            candidate = self.stage_dir / head['candidate']
            data = _read_shared(candidate, _MAX_INDEX_BYTES)
            if hashlib.sha256(data).hexdigest() != head['digest']:
                raise ValueError()
            packet = json.loads(data, object_pairs_hook=_json_unique)
            pointer = packet['journal']
            if (packet['version'] != 5 or pointer['id'] != head['id']
                    or pointer['generation'] != head['generation']):
                raise ValueError()
            self._install_prepared(head['id'], head['generation'])
            _replace_retrying(candidate, self.index_path)
            _fsync_dir(self.root)
        except (OSError, ValueError, TypeError, KeyError, UnicodeError):
            raise ManagedDownloadError('active generation index rejected') from None

    def _recover_if_pending(self):
        head = self._read_head()
        floor = self._read_floor()
        if floor is not None and (head is None or floor['generation'] > head['generation']):
            try:
                self._recover_head()
            except ManagedDownloadError as error:
                if str(error) != 'managed root writer fence busy':
                    raise
                deadline = time.monotonic() + 0.25
                while time.monotonic() < deadline:
                    current = self._read_head()
                    if (current is not None and current['generation'] >= floor['generation']
                            and _identity(self.stage_dir / current['candidate']) is None):
                        return
                    time.sleep(0.01)
                raise
            return
        if head is None:
            return
        candidate = self.stage_dir / head['candidate']
        if _identity(candidate) is None:
            return
        if (_identity(self.index_path) is not None and
                hashlib.sha256(_read_shared(self.index_path, _MAX_INDEX_BYTES)).hexdigest()
                == head['digest']):
            return
        try:
            self._recover_head()
        except ManagedDownloadError as error:
            if str(error) != 'managed root writer fence busy':
                raise
            deadline = time.monotonic() + 0.25
            while time.monotonic() < deadline:
                if _identity(candidate) is None:
                    return  # The caller still verifies the complete state.
                time.sleep(0.01)
            raise

    @staticmethod
    def _read_receipt(path, pieces, folders):
        if folders is not None and pieces[1] in folders.get(pieces[0], ()):
            # The shared ancestor chain and the folder itself were checked once for the pass.
            io_path = native_io_path(path)
            info = io_path.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_RECEIPT_BYTES
                    or getattr(info, 'st_file_attributes', 0) & 0x400):
                raise ManagedDownloadError('managed file rejected')
            with io_path.open('rb') as stream:
                data = stream.read(MAX_RECEIPT_BYTES + 1)
            if len(data) > MAX_RECEIPT_BYTES:
                raise ManagedDownloadError('managed file rejected')
            return data
        return _read_bounded(path, MAX_RECEIPT_BYTES)

    def _receipt_entry(self, relative: str, wid: str, *, update_date: str,
                       verify_complete: bool = False, folders=None) -> dict[str, str]:
        if (type(relative) is not str or len(relative) > 160
                or not relative.endswith('/receipt.json')):
            raise ManagedDownloadError('generation path rejected')
        pieces = relative.split('/')
        if (len(pieces) != 3 or pieces[0] not in ('outputs', 'generations')
                or not _FOLDER.fullmatch(pieces[1])
                or _FOLDER.fullmatch(pieces[1]).group(1) != wid
                or pieces[2] != 'receipt.json'):
            raise ManagedDownloadError('generation path rejected')
        _date(update_date, allow_empty=True)
        path = self.root / pieces[0] / pieces[1] / pieces[2]
        try:
            raw = self._read_receipt(path, pieces, folders)
        except (OSError, ValueError):
            raise ManagedDownloadError('generation receipt rejected') from None
        digest = hashlib.sha256(raw).hexdigest()
        try:
            receipt = json.loads(raw, object_pairs_hook=_json_unique)
            if (type(receipt) is not dict
                    or not _RECEIPT_KEYS <= receipt.keys() <= _RECEIPT_KEYS | {'bookmark_count', 'generation'}
                    or ('generation' in receipt and (type(receipt['generation']) is not int
                                                      or receipt['generation'] < 1))
                    or (receipt.get('bookmark_count') is not None
                        and (type(receipt['bookmark_count']) is not int or receipt['bookmark_count'] < 0))
                    or type(receipt.get('version')) is not int
                    or receipt['version'] != 1 or receipt.get('source') != 'pixiv-original'
                    or receipt.get('work_id') != wid or type(receipt.get('author_id')) is not str
                    or not _ID.fullmatch(receipt['author_id'])
                    or type(receipt.get('page_count')) is not int
                    or not 1 <= receipt['page_count'] <= MAX_PAGES
                    or type(receipt.get('files')) is not list
                    or len(receipt['files']) != receipt['page_count']
                    or type(receipt.get('illustration_type')) is not int
                    or receipt['illustration_type'] not in (0, 1)
                    or type(receipt.get('x_restrict')) is not int
                    or receipt['x_restrict'] not in (0, 1, 2)
                    or type(receipt.get('tags')) is not list
                    or len(receipt['tags']) > 100):
                raise ValueError()
            for field, limit in (('author_name', 256), ('title', 256),
                                 ('created_at', 128), ('saved_at', 64)):
                value = receipt[field]
                if (type(value) is not str or len(value) > limit
                        or any(ord(char) < 32 or ord(char) == 127 for char in value)):
                    raise ValueError()
            if datetime.fromisoformat(receipt['saved_at']).tzinfo is None:
                raise ValueError()
            for tag in receipt['tags']:
                if (type(tag) is not str or len(tag) > 128
                        or any(ord(char) < 32 or ord(char) == 127 for char in tag)):
                    raise ValueError()
            total = 0
            names = {'receipt.json'}
            folder = path.parent
            for index, item in enumerate(receipt['files'], 1):
                if type(item) is not dict or item.keys() != _FILE_KEYS:
                    raise ValueError()
                ext = {'png': 'png', 'jpeg': 'jpg', 'webp': 'webp'}.get(item.get('format'))
                name = f'page-{index}.{ext}'
                if (ext is None or type(item.get('name')) is not str or item['name'] != name
                        or type(item.get('bytes')) is not int
                        or not 0 < item['bytes'] <= MAX_PAGE_BYTES
                        or type(item.get('width')) is not int or type(item.get('height')) is not int
                        or not 0 < item['width'] * item['height'] <= MAX_PIXELS
                        or type(item.get('sha256')) is not str
                        or not _SHA.fullmatch(item['sha256'])):
                    raise ValueError()
                names.add(name)
                total += item['bytes']
                if verify_complete:
                    data = _read_bounded(folder / name, MAX_PAGE_BYTES)
                    if (len(data) != item['bytes'] or
                            hashlib.sha256(data).hexdigest() != item['sha256'] or
                            _image(data)[1:] != (item['format'], item['width'], item['height'])):
                        raise ValueError()
            if total > MAX_TOTAL:
                raise ValueError()
            if verify_complete and {item.name for item in native_io_path(folder).iterdir()} != names:
                raise ValueError()
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise ManagedDownloadError('generation receipt rejected') from None
        return {'work_id': wid, 'relative_receipt': relative,
                'sha256': digest, 'update_date': update_date}

    def _scan_dir(self, directory, message, limit=None):
        """One pass over a folder of work folders: names only, no link, no stray entry.

        `generations/` is not limited: superseded and orphaned generations stay for recovery and
        pinned viewers, so it can legitimately hold more folders than there are active works.
        """
        _safe_path(directory)
        names = set()
        if getattr(self, '_allow_missing_dirs', False) and _identity(directory) is None:
            return names
        with os.scandir(native_io_path(directory)) as listing:
            for item in listing:
                if limit is not None and len(names) >= limit:
                    raise ManagedDownloadError('generation limit exceeded', reason_code='storage_capacity')
                if _FOLDER.fullmatch(item.name) is None:
                    raise ManagedDownloadError(message)
                info = item.stat(follow_symlinks=False)
                if (item.is_symlink() or not stat.S_ISDIR(info.st_mode)
                        or getattr(info, 'st_file_attributes', 0) & 0x400):
                    raise ManagedDownloadError('managed storage link rejected')
                names.add(item.name)
        return names

    def _legacy(self, loaded=None) -> dict[str, dict[str, str]]:
        names = self._scan_dir(self.output_dir, 'legacy output structure rejected', _MAX_ENTRIES)
        known = {'outputs': names}
        result: dict[str, dict[str, str]] = {}
        by_name: dict[str, str] = {}
        for name in names:
            wid = _FOLDER.fullmatch(name).group(1)
            if wid in result:
                raise ManagedDownloadError('duplicate completed artwork rejected')
            result[wid] = self._receipt_entry(f'outputs/{name}/receipt.json', wid, update_date='',
                                              folders=known)
            by_name[name] = wid
        if loaded is not None:
            loaded.legacy_names = by_name
        return result

    def _set_active(self, loaded, wid):
        """Recompute one work's active entry (an indexed generation beats a legacy output)."""
        if loaded.shared:
            loaded.active = dict(loaded.active)
            loaded.shared = False
        old = loaded.active.get(wid)
        new = loaded.indexed.get(wid) or loaded.legacy.get(wid)
        if old is not None:
            loaded.acc = (loaded.acc - _entry_hash(old)) % _MOD
        if new is None:
            loaded.active.pop(wid, None)
        else:
            loaded.acc = (loaded.acc + _entry_hash(new)) % _MOD
            loaded.active[wid] = new

    def _refresh_legacy(self, loaded):
        """`outputs/` changed (an ID / bookmark save has no intent and lands there): verify only
        the folders that appeared and drop the ones that vanished, not the whole index."""
        signature = _identity(self.output_dir)
        names = self._scan_dir(self.output_dir, 'legacy output structure rejected', _MAX_ENTRIES)
        known = loaded.legacy_names
        gone = [name for name in known if name not in names]
        kept = {wid for name, wid in known.items() if name in names}
        fresh = {}
        for name in sorted(name for name in names if name not in known):
            wid = _FOLDER.fullmatch(name).group(1)
            if wid in kept or wid in fresh:
                raise ManagedDownloadError('duplicate completed artwork rejected')
            fresh[wid] = (name, self._receipt_entry(f'outputs/{name}/receipt.json', wid, update_date='',
                                                    folders={'outputs': names}))
        touched = []
        for name in gone:
            wid = known.pop(name)
            loaded.legacy.pop(wid, None)
            touched.append(wid)
        for wid, (name, entry) in fresh.items():
            known[name] = wid
            loaded.legacy[wid] = entry
            touched.append(wid)
        for wid in touched:
            self._set_active(loaded, wid)
        if len(loaded.active) > _MAX_ENTRIES:
            raise ManagedDownloadError('generation limit exceeded', reason_code='storage_capacity')
        loaded.out_sig = signature

    # -- verified state ---------------------------------------------------------------
    def _check_entry(self, wid, entry, version, folders):
        _id(wid)
        if (type(entry) is not dict or
                (entry.keys() != _ENTRY_KEYS and
                 (version not in (2, 3, 4, 5) or entry.keys() != _ENTRY_WITH_JOB))):
            raise ValueError()
        if 'accepted_fingerprint' in entry:
            _sha(entry['accepted_fingerprint'])
        if entry['work_id'] != wid or type(entry['work_id']) is not str:
            raise ValueError()
        if (type(entry['relative_receipt']) is not str
                or not _GEN_REL.fullmatch(entry['relative_receipt'])
                or _GEN_REL.fullmatch(entry['relative_receipt']).group(1) != wid):
            raise ValueError()
        digest = _sha(entry['sha256'])
        date = _date(entry['update_date'], allow_empty=True)
        actual = self._receipt_entry(entry['relative_receipt'], wid, update_date=date, folders=folders)
        if actual['sha256'] != digest:
            raise ValueError()
        return dict(entry)

    def _check_entries(self, entries, version, folders):
        """Verify every indexed receipt. The time is file-open latency (antivirus, directory
        lookups in a folder of 100,000 works), so a large index is verified on several threads."""
        items = list(entries.items())
        if len(items) < _PARALLEL_FROM:
            return {wid: self._check_entry(wid, entry, version, folders) for wid, entry in items}
        from concurrent.futures import ThreadPoolExecutor
        chunk = 512
        parts = [items[i:i + chunk] for i in range(0, len(items), chunk)]

        def verify(part):
            return [(wid, self._check_entry(wid, entry, version, folders)) for wid, entry in part]
        result = {}
        with ThreadPoolExecutor(max_workers=_VERIFY_THREADS) as pool:
            for done in pool.map(verify, parts):
                result.update(done)
        return result

    def _load_full(self) -> _Loaded:
        """Verify everything once: index, journal, every receipt, every work folder."""
        if native_io_path(self.index_path).is_symlink():
            raise ManagedDownloadError('generation index link rejected')
        loaded = _Loaded()
        loaded.out_sig = _identity(self.output_dir)
        legacy = self._legacy(loaded)
        loaded.legacy = legacy
        if _identity(self.index_path) is None:
            # Once any immutable generation was sealed, dropping the only
            # active pointer must NEVER silently expose old images or an
            # empty library. An empty pointer is installed before the first
            # generation is sealed, even if the subsequent save is cancelled.
            _safe_path(self.generation_dir)
            with os.scandir(native_io_path(self.generation_dir)) as listing:
                occupied = next(listing, None) is not None
            if (occupied or legacy or self._has_quarantine_records()
                    or any(_identity(self.root / name) is not None for name in
                           (_V5_HEAD, _GENERATION_FLOOR, _V5_COMMITTED, _V5_SEQUENCE_GUARD))
                    or any(self.root.glob('active-generations-*.journal'))
                    or any(self.root.glob('active-generations-*.mark'))):
                raise ManagedDownloadError('active generation index missing')
            loaded.active = dict(legacy)
            loaded.acc = sum(_entry_hash(e) for e in legacy.values()) % _MOD
            loaded.gen_sig = _identity(self.generation_dir)  # nothing to re-read until it changes
            return loaded
        generations = self._scan_dir(self.generation_dir, 'generation folder structure rejected')
        folders = {'outputs': set(), 'generations': generations}
        try:
            # The index is read through a handle, and everything else is judged against THAT file: a
            # save or fold that replaces the name meanwhile leaves this read consistent (the journal
            # it names is kept for a while after a fold) and the next call simply sees the newer one.
            raw, loaded.base_sig = _read_shared_identity(self.index_path, _MAX_INDEX_BYTES)
            loaded.base_digest = hashlib.sha256(raw).hexdigest()
            loaded.base_bytes = len(raw)
            packet = json.loads(raw, object_pairs_hook=_json_unique)
            if (type(packet) is not dict or type(packet.get('version')) is not int
                    or packet['version'] != 5):
                raise LegacyV5Uncertain()
            if (packet.keys() != _V5_KEYS or type(packet.get('entries')) is not dict
                    or len(packet['entries']) > _MAX_ENTRIES):
                raise ValueError()
            loaded.version = 5
            loaded.exclusions = packet['exclusions']
            loaded.operations = packet['quarantine_operations']
            journal = packet['journal']
            if (type(journal) is not dict or journal.keys() != {'id', 'generation', 'parent'}
                    or type(journal['id']) is not str
                    or re.fullmatch(r'[a-f0-9]{32}', journal['id']) is None
                    or type(journal['generation']) is not int or journal['generation'] < 1
                    or type(journal['parent']) is not str or not _SHA.fullmatch(journal['parent'])):
                raise LegacyV5Uncertain()
            loaded.jid = journal['id']
            loaded.generation, loaded.parent_digest = journal['generation'], journal['parent']
            self._quarantine_fields(loaded.qpacket(), verify_payloads=True, tolerate_missing=False)
            loaded.indexed = self._check_entries(packet['entries'], loaded.version, folders)
            self.assert_consistent(loaded, folders=folders)
            active = dict(legacy)
            active.update(loaded.indexed)
            if len(active) > _MAX_ENTRIES:
                raise ManagedDownloadError('generation limit exceeded', reason_code='storage_capacity')
            loaded.active = active
            loaded.acc = sum(_entry_hash(e) for e in active.values()) % _MOD
            return loaded
        except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError) as error:
            if isinstance(error, LegacyV5Uncertain):
                raise
            if isinstance(error, ManagedDownloadError) and str(error) in (
                    'generation limit exceeded', 'active generation index missing'):
                raise
            raise ManagedDownloadError('active generation index rejected') from None

    # -- journal ----------------------------------------------------------------------
    def _parse_journal(self, data, start_chain, first_seq, folders, version):
        """Records in `data` (which begins at a record boundary) -> (entries, chain, seq, end)."""
        entries, chain, seq, end, chains = [], start_chain, first_seq, 0, []
        position = 0
        while position < len(data):
            newline = data.find(b'\n', position)
            if newline < 0:
                break  # torn final append: never committed, ignored
            line = data[position:newline]
            record = json.loads(line, object_pairs_hook=_json_unique)
            if (type(record) is not dict or record.keys() != {'chain', 'entry', 'seq'}
                    or type(record['seq']) is not int or record['seq'] != seq + 1
                    or type(record['entry']) is not dict):
                raise ValueError()
            chain = hashlib.sha256((chain + _canonical(record['entry']).decode('ascii')).encode('ascii')).hexdigest()
            if record['chain'] != chain:
                raise ValueError()
            wid = record['entry'].get('work_id')
            entries.append(self._check_entry(wid, record['entry'], version, folders))
            chains.append(chain)
            seq += 1
            position = end = newline + 1
        return entries, chain, seq, end, chains

    def _journal_file(self, jid):
        return self.root / (_JOURNAL_FILE % jid)

    def _mark_file(self, jid):
        return self.root / (_MARK_FILE % jid)

    def assert_consistent(self, loaded, *, folders=None):
        """Validate committed generations and their continuous journal chain.

        Partial rollback of an index, head, journal or mark, including loss of
        later same-generation records, is rejected. Prepared pairs are ignored.
        Restoring the whole root together cannot be detected by this scheme.
        """
        legacy_signature = (_identity(self.root), _identity(self.root / _V5_COMMITTED))
        if legacy_signature != loaded.legacy_signature:
            _reject_legacy_files(self.root)
            loaded.legacy_signature = legacy_signature
        if _identity(self.index_path) != loaded.base_sig:
            raise ValueError()
        head = self._read_head()
        floor = self._read_floor()
        if (head is None or head['id'] != loaded.jid or head['generation'] != loaded.generation
                or head['digest'] != loaded.base_digest):
            raise ValueError()
        if floor is not None and (floor['generation'] > loaded.generation or
                (floor['generation'] == loaded.generation and
                 (floor['id'] != loaded.jid or floor['digest'] != loaded.base_digest))):
            raise ValueError()
        directory = _identity(self.root)
        cached = (loaded.witness_directory == directory and
                  all(_identity(path) == signature for path, signature, _ in loaded.witness_entries))
        if not cached:
            found = []
            for path in self.root.glob('active-generations-*.*'):
                match = _JOURNAL_NAME.fullmatch(path.name)
                if match is None:
                    continue
                signature = _identity(path)
                if match.group(2) == 'journal':
                    _safe_path(path, file=True)
                    with _open_shared(path) as stream:
                        raw_header = stream.readline(4097)
                    if len(raw_header) > 4096 or not raw_header.endswith(b'\n'):
                        raise ValueError()
                    record = json.loads(raw_header, object_pairs_hook=_json_unique)
                    if (type(record) is not dict
                            or record.keys() != {'id', 'kind', 'version', 'generation', 'parent'}
                            or record['id'] != match.group(1) or record['kind'] != _JOURNAL_KIND
                            or record['version'] != 3 or type(record['parent']) is not str
                            or not _SHA.fullmatch(record['parent'])):
                        raise ValueError()
                else:
                    record = json.loads(_read_shared(path, 4096), object_pairs_hook=_json_unique)
                    if (type(record) is not dict or record.keys() != {'id', 'records', 'chain', 'generation'}
                            or record['id'] != match.group(1)):
                        raise ValueError()
                generation = record.get('generation')
                if type(generation) is not int or generation < 1 or _identity(path) != signature:
                    raise ValueError()
                found.append((path, signature, generation))
            if _identity(self.root) != directory:
                raise ValueError()
            loaded.witness_directory, loaded.witness_entries = directory, tuple(found)
        if any(generation > loaded.generation for _, _, generation in loaded.witness_entries):
            raise ValueError()

        jpath, mpath = self._journal_file(loaded.jid), self._mark_file(loaded.jid)
        mark = json.loads(_read_shared(mpath, 4096), object_pairs_hook=_json_unique)
        raw, sig = _read_shared_identity(jpath, _MAX_JOURNAL_BYTES)
        newline = raw.find(b'\n')
        if newline < 0:
            raise ValueError()
        header = raw[:newline + 1]
        meta = json.loads(header, object_pairs_hook=_json_unique)
        if meta != {'id': loaded.jid, 'kind': _JOURNAL_KIND, 'version': 3,
                    'generation': loaded.generation, 'parent': loaded.parent_digest}:
            raise ValueError()
        cached = loaded.journal_hasher is not None
        if cached:
            offset = loaded.journal_offset
            if len(raw) < offset or hashlib.sha256(raw[:offset]).digest() != loaded.journal_hasher.digest():
                raise ValueError()
            start_chain, first_seq = loaded.journal_chain, loaded.journal_records
            chains_before = loaded.journal_chains
        else:
            offset = len(header)
            start_chain = hashlib.sha256(header).hexdigest()
            first_seq, chains_before = 0, [start_chain]
        if folders is None and raw[offset:]:
            folders = {'outputs': set(), 'generations': self._scan_dir(
                self.generation_dir, 'generation folder structure rejected')}
        entries, chain, seq, end, chains = self._parse_journal(
            raw[offset:], start_chain, first_seq, folders, 5)
        all_chains = chains_before + chains
        pending_path = self.stage_dir / ('append-' + loaded.jid + '.prepared')
        pending = None
        try:
            if _identity(pending_path) is not None:
                pending = json.loads(_read_shared(pending_path, 4096), object_pairs_hook=_json_unique)
        except FileNotFoundError:
            pass  # A concurrent writer completed; stable files are checked below.
        if pending is not None:
            if (type(pending) is not dict
                    or pending.keys() != {'id', 'generation', 'seq', 'before', 'chain', 'phase'}
                    or pending['id'] != loaded.jid or type(pending['generation']) is not int
                    or pending['generation'] != loaded.generation
                    or type(pending['seq']) is not int or pending['seq'] < 1
                    or type(pending['before']) is not str or not _SHA.fullmatch(pending['before'])
                    or type(pending['chain']) is not str or not _SHA.fullmatch(pending['chain'])
                    or pending['phase'] not in ('prepared', 'committed')
                    or seq not in (pending['seq'] - 1, pending['seq'])
                    or all_chains[pending['seq'] - 1] != pending['before']
                    or (seq == pending['seq'] and all_chains[seq] != pending['chain'])):
                raise ValueError()
            if pending['phase'] == 'committed' and seq != pending['seq']:
                raise ValueError()
            if pending['phase'] == 'prepared' and seq == pending['seq']:
                # A complete line is still unpublished until the commit record
                # is durable. It may be retried, but it must not become visible.
                if not entries or first_seq >= seq:
                    raise ValueError()
                entries.pop()
                all_chains.pop()
                seq -= 1
                chain = all_chains[-1]
                last = raw.rfind(b'\n', offset, offset + end - 1)
                end = (last + 1 if last >= offset else offset) - offset
        if (type(mark) is not dict or mark.keys() != {'id', 'records', 'chain', 'generation'}
                or mark['id'] != loaded.jid or type(mark['generation']) is not int
                or mark['generation'] != loaded.generation or type(mark['records']) is not int
                or not (loaded.mark_records if cached else 0) <= mark['records'] <= seq
                or mark['chain'] != all_chains[mark['records']]):
            raise ValueError()
        records = head.get('records')
        if records is not None and mark['records'] > records:
            head = self._read_head()
            if (head is None or head['id'] != loaded.jid or head['generation'] != loaded.generation
                    or head['digest'] != loaded.base_digest or head.get('records') is None
                    or mark['records'] > head['records']):
                raise ValueError()
            records = head['records']
        if records is not None and (records > seq or all_chains[records] != head['chain']):
            raise ValueError()
        if pending is not None and pending['phase'] == 'committed':
            if (mark['records'] < seq - 1 or (records is not None and records < seq - 1)):
                raise ValueError()
        elif records is not None and (records != seq or mark['records'] != seq):
            # A concurrent writer may have advanced the mark since it was read.
            latest = json.loads(_read_shared(mpath, 4096), object_pairs_hook=_json_unique)
            if latest != mark:
                raise ValueError()  # retry against a fresh coherent snapshot
            raise ValueError()
        for entry in entries:
            if cached:
                self._apply_entry(loaded, entry)
            else:
                loaded.indexed[entry['work_id']] = entry
        loaded.jpath = jpath
        loaded.journal_sig = sig
        loaded.journal_offset = offset + end
        loaded.header_bytes = len(header)
        loaded.journal_chain, loaded.journal_records = chain, seq
        loaded.journal_hasher = hashlib.sha256(raw[:loaded.journal_offset])
        loaded.journal_chains = all_chains
        loaded.mark_records = mark['records']

    def _apply_entry(self, loaded, entry):
        wid = entry['work_id']
        if loaded.shared:
            loaded.active = dict(loaded.active)
            loaded.shared = False
        old = loaded.active.get(wid)
        if old is not None:
            loaded.acc = (loaded.acc - _entry_hash(old)) % _MOD
        loaded.acc = (loaded.acc + _entry_hash(entry)) % _MOD
        loaded.active[wid] = dict(entry)
        loaded.indexed[wid] = dict(entry)

    def _sync_mark(self, loaded):
        """Learn what the mark on disk says: after taking in another instance's saves it is usually
        already current, and then it must not be written again (or the save fails when it cannot be)."""
        if loaded.jid is None or loaded.mark_records == loaded.journal_records:
            return
        try:
            mark = json.loads(_read_shared(self._mark_file(loaded.jid), 4096), object_pairs_hook=_json_unique)
            mark_keys = {'id', 'records', 'chain'} if loaded.generation is None else {'id', 'records', 'chain', 'generation'}
            if (mark.keys() == mark_keys and mark['id'] == loaded.jid
                    and (loaded.generation is None or mark['generation'] == loaded.generation)
                    and type(mark['records']) is int and loaded.mark_records <= mark['records'] <= loaded.journal_records):
                loaded.mark_records = mark['records']
        except Exception:
            pass

    def _refresh(self) -> _Loaded:
        with self._lock:
            loaded = self._loaded
            if loaded is not None:
                if loaded.version == 5 and _identity(self.index_path) == loaded.base_sig:
                    for attempt in range(8):
                        try:
                            self.assert_consistent(loaded)
                        except Exception:
                            if _identity(self.index_path) != loaded.base_sig:
                                # A legitimate fold moved the base to a new journal.
                                self._loaded = None
                                return self._refresh()
                            if attempt == 7:
                                # Do not discard our witness and accept a valid-looking
                                # older journal/mark pair against the same base.
                                raise ManagedDownloadError('active generation index rejected') from None
                            time.sleep(0.01 * (attempt + 1))
                        else:
                            if _identity(self.output_dir) != loaded.out_sig:
                                self._refresh_legacy(loaded)
                            return loaded
                if (loaded.version is None and _identity(self.index_path) is None
                        and _identity(self.generation_dir) == loaded.gen_sig
                        and _identity(self.output_dir) == loaded.out_sig):
                    return loaded
            self._loaded = None
            # The index and its journal are read as separate files. A writer folding in between would
            # show a half-old pair, so the pair must still be the same files when the read is done.
            for attempt in range(8):
                start = _identity(self.index_path)
                try:
                    fresh = self._load_full()
                except ManagedDownloadError as error:
                    # A file opened while another process replaces it, or a pair read across a fold,
                    # looks like damage for a moment. Real damage is still damage after the retries.
                    if attempt < 7 and str(error).endswith('rejected'):
                        time.sleep(0.01 * (attempt + 1))
                        continue
                    raise
                self._loaded = fresh
                return fresh
            raise ManagedDownloadError('active generation index rejected')

    def _checked(self, *, verify_quarantine_payloads=True, tolerate_missing=False):
        """Fresh state plus the quarantine check the old per-call `_index()` did."""
        with self._lock:
            loaded = self._refresh()
            packet = loaded.qpacket()
            if packet is None:
                self._unconfirmed_operations = set()
                return loaded, ({}, {}, False)
            return loaded, self._quarantine_fields(packet, verify_payloads=verify_quarantine_payloads,
                                                   tolerate_missing=tolerate_missing)

    def _index(self, *, verify_quarantine_payloads=True, tolerate_missing=False) -> dict[str, dict[str, str]]:
        loaded, _ = self._checked(verify_quarantine_payloads=verify_quarantine_payloads,
                                  tolerate_missing=tolerate_missing)
        return {wid: dict(entry) for wid, entry in loaded.indexed.items()}

    def entries(self, *, verify_quarantine_payloads=True, tolerate_missing=False) -> dict[str, dict[str, str]]:
        self._recover_if_pending()
        with self._lock:
            loaded, _ = self._checked(verify_quarantine_payloads=verify_quarantine_payloads,
                                      tolerate_missing=tolerate_missing)
            return {wid: dict(entry) for wid, entry in loaded.active.items()}

    def entry_count(self) -> int:
        self._recover_if_pending()
        with self._lock:
            return len(self._checked()[0].active)

    def committed_generation(self) -> int:
        """Return the currently validated generation under the writer fence."""
        self._recover_if_pending()
        with self._lock:
            generation = self._checked()[0].generation
            if type(generation) is not int or generation < 1:
                raise ManagedDownloadError('active generation index rejected')
            return generation

    def capacity(self) -> dict:
        """What is stored and how much more fits (entry count and index bytes, whichever binds)."""
        self._recover_if_pending()
        with self._lock:
            loaded = self._checked()[0]
            used = loaded.base_bytes + loaded.journal_offset
            by_count = max(0, _MAX_ENTRIES - len(loaded.active))
            by_bytes = max(0, (_MAX_INDEX_BYTES - used) // _ENTRY_BYTES_ESTIMATE)
            room = min(by_count, by_bytes)
            if loaded.version == 5 and loaded.fold_failed:
                # Folds keep failing, so the journal cannot be emptied: what still fits in it is the limit.
                remaining = _MAX_JOURNAL_BYTES - _JOURNAL_SLACK - loaded.journal_offset
                average = ((loaded.journal_offset - loaded.header_bytes) // loaded.journal_records
                           if loaded.journal_records else _JOURNAL_RECORD_ESTIMATE)
                # publish refuses once the offset reaches the limit, so a save fits while it is below it
                room = min(room, 0 if remaining <= 0 else -(-remaining // max(1, average)))
            return {'entries': len(loaded.active), 'max_entries': _MAX_ENTRIES, 'index_bytes': used,
                    'max_index_bytes': _MAX_INDEX_BYTES, 'headroom': room}

    def assert_room(self, work_id: str, *, indexed: bool = True) -> None:
        """Refuse a new work before it is downloaded when the store could not record it afterwards."""
        wid = _id(work_id)
        self._recover_if_pending()
        with self._lock:
            loaded = self._checked()[0]
            if wid in loaded.active:
                return  # an update replaces its entry
            if len(loaded.active) + 1 > _MAX_ENTRIES:
                raise ManagedDownloadError('generation limit exceeded', reason_code='storage_capacity')
            if indexed and loaded.base_bytes + loaded.journal_offset + _ENTRY_BYTES_ESTIMATE > _MAX_INDEX_BYTES:
                raise ManagedDownloadError('generation index size exceeded', reason_code='storage_capacity')
            journal_full = (indexed and loaded.version == 5
                            and loaded.journal_offset >= _MAX_JOURNAL_BYTES - _JOURNAL_SLACK)
        if journal_full:
            # The next save would be refused after the download: try to make room now, or say no now.
            self._fold_for_room()
            with self._lock:
                loaded = self._checked()[0]
                if loaded.version == 5 and loaded.journal_offset >= _MAX_JOURNAL_BYTES - _JOURNAL_SLACK:
                    raise ManagedDownloadError('generation journal full', reason_code='storage_capacity')

    @fenced_writer
    def _fold_for_room(self) -> None:
        self._recover_if_pending()
        with self._lock:
            loaded = self._checked()[0]
            if loaded.version == 5 and loaded.journal_offset >= _MAX_JOURNAL_BYTES - _JOURNAL_SLACK:
                try:
                    self._compact(loaded, 2.0)
                except Exception:
                    loaded.fold_failed = True

    def current(self, work_id: str) -> dict[str, str] | None:
        wid = _id(work_id)
        self._recover_if_pending()
        with self._lock:
            entry = self._checked()[0].active.get(wid)
            return dict(entry) if entry is not None else None

    @fenced_writer
    def prepare_for_publication(self) -> None:
        """Persist a zero-entry pointer before exposing any new generation.

        A crash before the rename leaves no visible generation. A crash after
        the rename still leaves this pointer, so missing-index corruption can
        be distinguished from a fresh v1-only managed root on restart.
        """
        self._recover_if_pending()
        with self._lock:
            if _identity(self.index_path) is not None:
                self._checked()  # Never trust a preexisting malformed index.
                return
            self._checked()  # Reject an already-missing index with generations.
            try:
                # A v5 pointer from the start: an older build refuses it, and a journal can never be
                # lost from a root that was only ever written by this one.
                self._commit_packet({'version': 5, 'entries': {}})
                self._loaded = None
            except Exception:
                raise ManagedDownloadError('generation index preparation failed') from None

    @fenced_writer
    def publish(self, work_id: str, relative_receipt: str, sha256: str,
                update_date: str, expected_previous_sha256: str | None,
                expected_previous_relative_receipt: str | None = None,
                accepted_fingerprint: str | None = None, *,
                expected_work_state_token: str | None = None) -> dict[str, str]:
        wid = _id(work_id)
        digest = _sha(sha256)
        date = _date(update_date, allow_empty=True)
        if accepted_fingerprint is not None:
            _sha(accepted_fingerprint)
        if (type(relative_receipt) is not str
                or not _GEN_REL.fullmatch(relative_receipt)
                or _GEN_REL.fullmatch(relative_receipt).group(1) != wid):
            raise ManagedDownloadError('generation path rejected')
        if expected_previous_sha256 is not None:
            _sha(expected_previous_sha256)
            if (type(expected_previous_relative_receipt) is not str
                    or not (expected_previous_relative_receipt.startswith('outputs/')
                            or expected_previous_relative_receipt.startswith('generations/'))):
                raise ManagedDownloadError('generation compare-and-swap rejected')
        elif expected_previous_relative_receipt is not None:
            raise ManagedDownloadError('generation compare-and-swap rejected')
        self._recover_if_pending()
        with self._lock:
            loaded, (exclusions, operations, enabled) = self._checked()
            if loaded.version == 5 and loaded.generation is not None:
                self._advance_head_sequence(loaded)
                self._ensure_sequence_guard()
            before = loaded.active.get(wid)
            before = dict(before) if before is not None else None
            # Safe idempotent replay after index commit but before job DB commit.
            if (before is not None and before['relative_receipt'] == relative_receipt
                    and before['sha256'] == digest and before['update_date'] == date
                    and before.get('accepted_fingerprint') == accepted_fingerprint):
                # An active pointer matching the same operation is not proof
                # that page bytes remain intact. Revalidate all pages before
                # reporting an idempotent completion after a crash/retry.
                verified = self._receipt_entry(relative_receipt, wid,
                                               update_date=date, verify_complete=True)
                if verified['sha256'] != digest:
                    raise ManagedDownloadError('active generation checksum mismatch')
                return dict(before)
            actual_token = self._work_token({'entries': loaded.active, 'exclusions': exclusions}, wid)
            if expected_work_state_token is not None:
                _sha(expected_work_state_token)
                if actual_token != expected_work_state_token:
                    raise ManagedDownloadError('generation compare-and-swap rejected')
            elif exclusions.get(wid):
                # A matching completed operation above only verifies its old
                # result; it cannot clear exclusions. A new publication must
                # also compare the page-exclusion state.
                raise ManagedDownloadError('generation compare-and-swap rejected')
            if ((before['sha256'] if before is not None else None) != expected_previous_sha256
                    or (before['relative_receipt'] if before is not None else None)
                    != expected_previous_relative_receipt):
                raise ManagedDownloadError('generation compare-and-swap rejected')
            target = self._receipt_entry(relative_receipt, wid,
                                         update_date=date, verify_complete=True)
            if target['sha256'] != digest:
                raise ManagedDownloadError('new generation checksum mismatch')
            if accepted_fingerprint is not None:
                target['accepted_fingerprint'] = accepted_fingerprint
            if len(loaded.active) + (0 if wid in loaded.active else 1) > _MAX_ENTRIES:
                raise ManagedDownloadError('generation limit exceeded', reason_code='storage_capacity')
            if (loaded.base_bytes + loaded.journal_offset + _ENTRY_BYTES_ESTIMATE > _MAX_INDEX_BYTES):
                raise ManagedDownloadError('generation index size exceeded', reason_code='storage_capacity')
            if enabled:
                if loaded.qversion == 4:
                    # A relocation pins its own work's immutable receipt. An
                    # unrelated work may still publish a new generation.
                    # A purged backup can never be restored, so a newer
                    # generation cannot invalidate it any more.
                    if any(proof['item']['work_id'] == wid and proof['state'] != 'purged'
                           for proof in self.relocation_proofs().values()):
                        raise ManagedDownloadError('relocation blocks generation publication')
            if loaded.version == 5 and loaded.journal_offset >= _MAX_JOURNAL_BYTES - _JOURNAL_SLACK:
                # The journal is at its ceiling because folds keep failing: fold now or stop saving,
                # never grow past the bound the next open has to read.
                try:
                    self._compact(loaded, 2.0)
                except Exception:
                    loaded.fold_failed = True
                    raise ManagedDownloadError('generation journal full', reason_code='storage_capacity') from None
            try:
                if loaded.version is None:
                    self._compact(loaded, 5.0)
                self._append_journal(loaded, target)
            except Exception:
                # If the append/replace already happened, the durable pointer remains
                # authoritative; the next retry verifies/reuses the generation.
                self._loaded = None
                raise ManagedDownloadError('generation index publication failed') from None
            if (loaded.journal_records >= _COMPACT_RECORDS or loaded.journal_offset >= _COMPACT_BYTES):
                try:
                    self._compact(loaded, 0.25)  # optional: do not hold the writer fence for long
                except Exception:
                    loaded.fold_failed = True    # the journal stays valid; retried on the next save
            return dict(target)

    def _append_journal(self, loaded, target):
        self._ensure_v5_committed()
        self._advance_head_sequence(loaded)
        self._ensure_sequence_guard()
        entry = dict(target)
        if loaded.mark_records != loaded.journal_records:
            self._sync_mark(loaded)
        if loaded.mark_records != loaded.journal_records:
            # The journal is ahead of its mark (a crash, or an earlier mark write that failed). Catch
            # the mark up BEFORE adding anything, so the journal is never more than one record ahead
            # of what the mark can vouch for. If that fails nothing is appended and the save fails.
            self._write_mark(loaded.jid, loaded.journal_records, loaded.journal_chain,
                             generation=loaded.generation)
            loaded.mark_records = loaded.journal_records
        pending_path = self.stage_dir / ('append-' + loaded.jid + '.prepared')
        if _identity(pending_path) is not None:
            # Discard a torn or fully written but still prepared tail before
            # removing its evidence. A committed tail is included in offset.
            with self._open_for_append(loaded.jpath) as stream:
                stream.truncate(loaded.journal_offset)
                stream.flush()
                os.fsync(stream.fileno())
            _safe_path(pending_path, file=True)
            native_io_path(pending_path).unlink()
            _fsync_dir(self.stage_dir)
        seq = loaded.journal_records + 1
        chain = hashlib.sha256((loaded.journal_chain + _canonical(entry).decode('ascii')).encode('ascii')).hexdigest()
        line = _canonical({'chain': chain, 'entry': entry, 'seq': seq}) + b'\n'
        self.assert_consistent(loaded)
        pending = {'id': loaded.jid, 'generation': loaded.generation, 'seq': seq,
                   'before': loaded.journal_chain, 'chain': chain, 'phase': 'prepared'}
        self._write_append_intent(pending_path, pending)
        _safe_path(loaded.jpath, file=True)
        with self._open_for_append(loaded.jpath) as stream:
            size = os.fstat(stream.fileno()).st_size
            if size < loaded.journal_offset:
                raise ManagedDownloadError('generation journal rejected')
            stream.truncate(loaded.journal_offset)  # a torn, never-committed tail
            stream.seek(loaded.journal_offset)
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())
        self._write_append_intent(pending_path, {**pending, 'phase': 'committed'})
        loaded.journal_offset += len(line)
        loaded.journal_hasher.update(line)
        loaded.journal_chains.append(chain)
        loaded.journal_chain, loaded.journal_records = chain, seq
        loaded.journal_sig = _identity(loaded.jpath)
        self._apply_entry(loaded, entry)
        self._advance_head_sequence(loaded)
        # The save is durable now. The mark only lets the next open notice a lost journal; if it
        # cannot be written this time the save still stands, and the next save writes it first.
        try:
            self._write_mark(loaded.jid, seq, chain, generation=loaded.generation)
            loaded.mark_records = seq
            native_io_path(pending_path).unlink()
            _fsync_dir(self.stage_dir)
        except Exception:
            pass

    def _write_append_intent(self, path, value):
        temporary = self.stage_dir / ('append-' + uuid4().hex + '.tmp')
        _write(temporary, _canonical(value))
        _fsync_dir(self.stage_dir)
        _replace_retrying(temporary, path)
        _fsync_dir(self.stage_dir)

    @staticmethod
    def _open_for_append(path, patience=0.5):
        """Open the journal for writing, waiting briefly out a reader that holds it without sharing
        write access (an unusual tool; our own readers share)."""
        deadline = time.monotonic() + patience
        delay = 0.005
        while True:
            try:
                return native_io_path(path).open('r+b')
            except PermissionError:
                if time.monotonic() + delay > deadline:
                    raise
                time.sleep(delay)
                delay = min(delay * 1.5, 0.05)

    def _write_mark(self, jid, records, chain, patience=1.0, *, generation=None):
        _safe_path(self.stage_dir)
        temporary = self.stage_dir / ('mark-' + uuid4().hex + '.tmp')
        mark = {'id': jid, 'records': records, 'chain': chain}
        if generation is not None:
            mark['generation'] = generation
        _write(temporary, _canonical(mark))
        _fsync_dir(self.stage_dir)
        _replace_retrying(temporary, self._mark_file(jid), patience)

    def _new_journal(self, patience=5.0, *, generation, parent):
        """Prepare a journal pair without exposing it as committed evidence."""
        pointers = tuple(item for item in (self._read_head(), self._read_floor()) if item is not None)
        referenced = {item['id'] for item in pointers}
        for path in self.stage_dir.glob('prepared-*'):
            match = re.fullmatch(r'prepared-([a-f0-9]{32})\.(journal|mark)', path.name)
            if match is not None and match.group(1) not in referenced:
                _safe_path(path, file=True)
                native_io_path(path).unlink()
        referenced_candidates = {item['candidate'] for item in pointers}
        for path in self.stage_dir.glob('active-*.tmp'):
            if (re.fullmatch(r'active-[a-f0-9]{32}\.tmp', path.name)
                    and path.name not in referenced_candidates):
                _safe_path(path, file=True)
                native_io_path(path).unlink()
        for pattern in ('floor-*.tmp', 'head-*.tmp'):
            for path in self.stage_dir.glob(pattern):
                if re.fullmatch(r'(?:floor|head)-[a-f0-9]{32}\.tmp', path.name):
                    _safe_path(path, file=True)
                    native_io_path(path).unlink()
        jid = uuid4().hex
        header = _canonical({'id': jid, 'kind': _JOURNAL_KIND, 'version': 3,
                             'generation': generation, 'parent': parent}) + b'\n'
        _safe_path(self.stage_dir)
        journal = self.stage_dir / ('prepared-' + jid + '.journal')
        mark = self.stage_dir / ('prepared-' + jid + '.mark')
        _write(journal, header)
        _write(mark, _canonical({'id': jid, 'generation': generation, 'records': 0,
                                 'chain': hashlib.sha256(header).hexdigest()}))
        _fsync_dir(self.stage_dir)
        return jid, header

    def _install_prepared(self, jid, generation):
        """Finish installing a pair named by a committed head."""
        journal, mark = self._journal_file(jid), self._mark_file(jid)
        staged_journal = self.stage_dir / ('prepared-' + jid + '.journal')
        staged_mark = self.stage_dir / ('prepared-' + jid + '.mark')
        if _identity(journal) is not None and _identity(mark) is not None:
            return
        journal_bytes = _read_shared(journal if _identity(journal) is not None else staged_journal, 4096)
        header = json.loads(journal_bytes, object_pairs_hook=_json_unique)
        mark_bytes = _read_shared(mark if _identity(mark) is not None else staged_mark, 4096)
        witness = json.loads(mark_bytes, object_pairs_hook=_json_unique)
        if (type(header) is not dict or header.get('id') != jid or header.get('generation') != generation
                or witness != {'id': jid, 'generation': generation, 'records': 0,
                               'chain': hashlib.sha256(journal_bytes).hexdigest()}):
            raise ManagedDownloadError('active generation index rejected')
        if _identity(journal) is None:
            _replace_retrying(staged_journal, journal)
        if _identity(mark) is None:
            _replace_retrying(staged_mark, mark)
        _fsync_dir(self.root)

    def _prune_journals(self, keep):
        """Drop journals and marks no index can refer to any more. A retired one is kept for
        `_JOURNAL_RETAIN_SECONDS` after its fold: a reader that opened the previous index still needs
        it, and it never changes again."""
        now = time.time()
        try:
            with os.scandir(native_io_path(self.root)) as listing:
                found = [(item.name, item.stat(follow_symlinks=False).st_mtime) for item in listing]
        except OSError:
            return
        for name, modified in found:
            match = _JOURNAL_NAME.fullmatch(name)
            if (match is not None and match.group(1) not in keep
                    and now - modified > _JOURNAL_RETAIN_SECONDS):
                try:
                    native_io_path(self.root / name).unlink()
                except OSError:
                    pass

    @fenced_writer
    def _compact(self, loaded, patience=5.0):
        if loaded.version == 5:
            self.assert_consistent(loaded)
        self._commit_packet({'version': 5,
                             'entries': {wid: dict(value) for wid, value in loaded.indexed.items()},
                             'exclusions': deepcopy(loaded.exclusions),
                             'quarantine_operations': deepcopy(loaded.operations)}, patience)

    def _has_quarantine_records(self) -> bool:
        """Completed quarantine files require their authoritative v3 pointer.

        Incomplete copies do not change visibility. The empty v3 pointer is
        installed before any operation enters the completed namespace.
        """
        folder = native_io_path(self.root / 'quarantine' / 'operations')
        _safe_path(folder)
        return folder.exists() and any(folder.iterdir())

    def _quarantine_fields(self, packet=None, *, verify_payloads=True, tolerate_missing=False):
        """Validate index/manifest agreement without exposing copied images.

        The immutable original receipt remains the source proof. A missing or
        malformed exclusion is never interpreted as permission to show a page.
        """
        self._unconfirmed_operations = set()
        try:
            if packet is None:
                packet = self._refresh().qpacket()
                if packet is None:
                    return {}, {}, False
            if type(packet) is not dict or type(packet.get('version')) is not int:
                raise ValueError()
            if packet['version'] in (1, 2):
                if packet.keys() != _INDEX_KEYS:
                    raise ValueError()
                return {}, {}, False
            if packet['version'] not in (3, 4) or packet.keys() != _QUARANTINE_KEYS:
                raise ValueError()
            exclusions = packet['exclusions']
            operations = packet['quarantine_operations']
            if (type(exclusions) is not dict or type(operations) is not dict
                    or len(exclusions) > _MAX_ENTRIES or len(operations) > _MAX_ENTRIES):
                raise ValueError()
            expected = {}
            for operation_id, operation in operations.items():
                moving = packet['version'] == 4 and type(operation) is dict and operation.get('mode') == 'move'
                allowed_states = (('relocating', 'moved', 'restoring', 'restored', 'purged') if moving
                                  else ('committed', 'restored', 'purged'))
                if (type(operation_id) is not str or not _OPERATION_ID.fullmatch(operation_id)
                        or type(operation) is not dict
                        or operation.keys() != (_MOVE_OPERATION_KEYS if packet['version'] == 4 else _OPERATION_KEYS)
                        or (packet['version'] == 4 and operation['mode'] not in ('copy', 'move'))
                        or operation['state'] not in allowed_states):
                    raise ValueError()
                relative = f'quarantine/operations/{operation_id}/manifest.json'
                if operation['manifest'] != relative:
                    raise ValueError()
                _sha(operation['manifest_sha256'])
                _sha(operation['selection_sha256'])
                try:
                    raw = _read_bounded(self.root / relative, MAX_RECEIPT_BYTES)
                except FileNotFoundError:
                    # Display-only summary: one vanished operation is reported
                    # as unconfirmable; every mutation path stays strict.
                    if not tolerate_missing:
                        raise
                    self._unconfirmed_operations.add(operation_id)
                    continue
                if hashlib.sha256(raw).hexdigest() != operation['manifest_sha256']:
                    raise ValueError()
                manifest = json.loads(raw, object_pairs_hook=_json_unique)
                if (type(manifest) is not dict
                        or manifest.keys() != {'version', 'operation_id', 'selection_sha256',
                                               'items', 'failed'}
                        or type(manifest['version']) is not int or manifest['version'] != 1
                        or manifest['operation_id'] != operation_id
                        or manifest['selection_sha256'] != operation['selection_sha256']
                        or type(manifest['items']) is not list
                        or not 1 <= len(manifest['items']) <= _MAX_QUARANTINE_ITEMS
                        or type(manifest['failed']) is not list
                        or len(manifest['failed']) > _MAX_QUARANTINE_ITEMS):
                    raise ValueError()
                seen = set()
                for item in manifest['items']:
                    if type(item) is not dict or item.keys() != _QUARANTINE_FILE_KEYS:
                        raise ValueError()
                    wid = _id(item['work_id'])
                    page = item['page_no']
                    if type(page) is not int or not 0 <= page < MAX_PAGES:
                        raise ValueError()
                    key = (wid, page)
                    if key in seen:
                        raise ValueError()
                    seen.add(key)
                    _sha(item['receipt_sha256'])
                    _sha(item['sha256'])
                    ext = {'png': 'png', 'jpeg': 'jpg', 'webp': 'webp'}.get(item['format'])
                    if (ext is None or item['file'] != f'{wid}-p{page + 1}.{ext}'
                            or type(item['bytes']) is not int
                            or not 0 < item['bytes'] <= MAX_PAGE_BYTES
                            or type(item['width']) is not int or type(item['height']) is not int
                            or item['width'] <= 0 or item['height'] <= 0
                            or item['width'] * item['height'] > MAX_PIXELS):
                        raise ValueError()
                    proof = self._receipt_entry(item['relative_receipt'], wid, update_date='')
                    if proof['sha256'] != item['receipt_sha256']:
                        raise ValueError()
                    if moving:
                        # A foreign folder under a valid managed root cannot
                        # masquerade as our quarantine destination.
                        qmarker = self.root / 'quarantine' / '.pixivvault-managed-quarantine.json'
                        expected_marker = {'application': 'pixivvault-nicegui',
                                           'purpose': 'managed-original-quarantine',
                                           'version': 1}
                        if json.loads(_read_bounded(qmarker, 4096),
                                      object_pairs_hook=_json_unique) != expected_marker:
                            raise ValueError()
                        # The immutable original receipt is still the contract;
                        # relocation never invents a receipt or changes its hash.
                        original = json.loads(_read_bounded(
                            self.root / item['relative_receipt'], MAX_RECEIPT_BYTES),
                            object_pairs_hook=_json_unique)
                        if (page >= original['page_count'] or
                                any(original['files'][page][k] != item[k] for k in
                                    ('sha256', 'bytes', 'format', 'width', 'height'))):
                            raise ValueError()
                        folder = self.root / 'quarantine' / 'operations' / operation_id
                        _safe_path(folder)
                        copy = folder / item['file']
                        _safe_path(copy, file=True)
                        # A restored copy is no longer a proof (the source is back);
                        # a purged one was deliberately destroyed (empty trash).
                        # Either may be present, partially deleted or absent.
                        if verify_payloads and operation['state'] not in ('restored', 'purged'):
                            if native_io_path(copy).lstat().st_nlink != 1:
                                raise ValueError()
                            data = _read_bounded(copy, MAX_PAGE_BYTES)
                            if (len(data) != item['bytes'] or
                                    hashlib.sha256(data).hexdigest() != item['sha256'] or
                                    _image(data)[1:] != (item['format'], item['width'], item['height'])):
                                raise ValueError()
                        # After final commit every selected physical source must
                        # have left the owned output folder. Restored must exist.
                        source_path = (self.root / item['relative_receipt']).parent / original['files'][page]['name']
                        _safe_path(source_path, file=True)
                        if (native_io_path(source_path).exists()
                                and native_io_path(source_path).lstat().st_nlink != 1):
                            raise ValueError()
                        if operation['state'] in ('moved', 'purged') and native_io_path(source_path).exists():
                            raise ValueError()
                        if operation['state'] == 'restored' and not native_io_path(source_path).is_file():
                            raise ValueError()
                    if operation['state'] in ('committed', 'relocating', 'moved', 'restoring', 'purged'):
                        pages = expected.setdefault(wid, {})
                        if str(page) in pages:
                            raise ValueError()
                        pages[str(page)] = operation_id
                failed = set()
                for key in manifest['failed']:
                    if (type(key) is not str
                            or not re.fullmatch(r'[1-9][0-9]{0,19}:[1-9][0-9]{0,3}', key)
                            or int(key.rsplit(':', 1)[1]) > MAX_PAGES or key in failed):
                        raise ValueError()
                    wid, number = key.rsplit(':', 1)
                    if (wid, int(number) - 1) in seen:
                        raise ValueError()
                    failed.add(key)
            for wid, pages in exclusions.items():
                _id(wid)
                if type(pages) is not dict or not 1 <= len(pages) <= MAX_PAGES:
                    raise ValueError()
                for page, operation_id in pages.items():
                    if (type(page) is not str or not page.isascii() or not page.isdecimal()
                            or str(int(page)) != page or not 0 <= int(page) < MAX_PAGES
                            or type(operation_id) is not str):
                        raise ValueError()
            checked = exclusions
            if self._unconfirmed_operations:
                checked = {}
                for wid, pages in exclusions.items():
                    rest = {p: o for p, o in pages.items() if o not in self._unconfirmed_operations}
                    if rest:
                        checked[wid] = rest
            if checked != expected:
                raise ValueError()
            return deepcopy(exclusions), deepcopy(operations), True
        except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            raise ManagedDownloadError('quarantine index rejected') from None

    @staticmethod
    def _state_digest(value) -> str:
        raw = json.dumps(value, sort_keys=True, ensure_ascii=True,
                         separators=(',', ':')).encode('ascii')
        return hashlib.sha256(raw).hexdigest()

    def state_snapshot(self, *, verify_quarantine_payloads=True, tolerate_missing=False):
        """Entries as a read-only view; the revision is an order-free digest kept incrementally."""
        self._recover_if_pending()
        with self._lock:
            loaded, (exclusions, operations, enabled) = self._checked(
                verify_quarantine_payloads=verify_quarantine_payloads, tolerate_missing=tolerate_missing)
            unconfirmed = frozenset(self._unconfirmed_operations)
            active = loaded.active
            if any(wid not in active for wid in exclusions):
                raise ManagedDownloadError('quarantine source receipt missing')
            loaded.shared = True
            revision = self._state_digest({'n': len(active), 'acc': '%064x' % loaded.acc,
                                           'exclusions': exclusions, 'operations': operations})
            return {'entries': _EntriesView(active), 'exclusions': exclusions, 'operations': operations,
                    'revision': revision, 'quarantine_enabled': enabled,
                    'unconfirmed_operations': unconfirmed}

    def relocation_proofs(self):
        """Exact source receipt/page -> verified managed backup for v4 moves.

        Never use this to excuse a missing unselected page. Every returned proof
        comes from a validated manifest and immutable original receipt.
        """
        self._recover_if_pending()
        with self._lock:
            loaded, _ = self._checked()
            if loaded.qversion != 4:
                return {}
            result = {}
            for operation_id, operation in loaded.operations.items():
                if operation['mode'] != 'move' or operation['state'] == 'restored':
                    continue
                manifest = json.loads(_read_bounded(
                    self.root / operation['manifest'], MAX_RECEIPT_BYTES),
                    object_pairs_hook=_json_unique)
                for item in manifest['items']:
                    key = (item['relative_receipt'], item['page_no'])
                    if key in result:
                        raise ManagedDownloadError('duplicate relocation proof')
                    result[key] = {'item': dict(item), 'operation_id': operation_id,
                                   'state': operation['state'],
                                   'path': self.root / 'quarantine' / 'operations' / operation_id / item['file']}
            return result

    @classmethod
    def _work_token(cls, state, work_id) -> str:
        return cls._state_digest({
            'entry': state['entries'].get(work_id),
            'exclusions': state['exclusions'].get(work_id, {}),
        })

    def work_state_token(self, work_id: str) -> str:
        wid = _id(work_id)
        return self._work_token(self.state_snapshot(), wid)

    def _write_v5_marker(self, data):
        marker = self.root / _V5_COMMITTED
        _safe_path(self.stage_dir)
        temporary = self.stage_dir / ('v5-commit-' + uuid4().hex + '.tmp')
        _write(temporary, data)
        _fsync_dir(self.stage_dir)
        _replace_retrying(temporary, marker)
        _fsync_dir(self.root)

    def _read_floor(self):
        path = self.root / _GENERATION_FLOOR
        if _identity(path) is None:
            return None
        try:
            floor = json.loads(_read_shared(path, 4096), object_pairs_hook=_json_unique)
            if (type(floor) is not dict
                    or floor.keys() != {'generation', 'id', 'digest', 'candidate', 'records', 'chain'}
                    or type(floor['generation']) is not int or floor['generation'] < 1
                    or type(floor['id']) is not str or re.fullmatch(r'[a-f0-9]{32}', floor['id']) is None
                    or type(floor['digest']) is not str or not _SHA.fullmatch(floor['digest'])
                    or type(floor['candidate']) is not str
                    or re.fullmatch(r'active-[a-f0-9]{32}\.tmp', floor['candidate']) is None
                    or type(floor['records']) is not int or floor['records'] != 0 or type(floor['chain']) is not str
                    or not _SHA.fullmatch(floor['chain'])):
                raise ValueError()
            return floor
        except (OSError, ValueError, TypeError, KeyError, UnicodeError):
            raise ManagedDownloadError('active generation index rejected') from None

    def _write_head(self, head):
        floor = self._read_floor()
        if (floor is None or head['generation'] > floor['generation']) and head['records'] == 0:
            temporary = self.stage_dir / ('floor-' + uuid4().hex + '.tmp')
            _write(temporary, _canonical(head))
            _fsync_dir(self.stage_dir)
            _replace_retrying(temporary, self.root / _GENERATION_FLOOR)
            _fsync_dir(self.root)
        temporary = self.stage_dir / ('head-' + uuid4().hex + '.tmp')
        _write(temporary, _canonical(head))
        _fsync_dir(self.stage_dir)
        _replace_retrying(temporary, self.root / _V5_HEAD)
        _fsync_dir(self.root)

    def _ensure_sequence_guard(self):
        guard = self.root / _V5_SEQUENCE_GUARD
        if _identity(guard) is not None:
            if _read_shared(guard, 256) != _V5_SEQUENCE_DONE:
                raise ManagedDownloadError('active generation index rejected')
            return
        temporary = self.stage_dir / ('sequence-' + uuid4().hex + '.tmp')
        _write(temporary, _V5_SEQUENCE_DONE)
        _fsync_dir(self.stage_dir)
        _replace_retrying(temporary, guard)
        _fsync_dir(self.root)

    def _advance_head_sequence(self, loaded):
        """Anchor every durable append independently of its journal and mark."""
        self.assert_consistent(loaded)
        head = self._read_head()
        records = head.get('records')
        if records != loaded.journal_records:
            self._write_head({**head, 'records': loaded.journal_records, 'chain': loaded.journal_chain})

    def _ensure_v5_committed(self):
        """Keep durable evidence before a v5 publication or a journal can be folded away."""
        marker = self.root / _V5_COMMITTED
        if _identity(marker) is not None:
            existing = _read_shared(marker, 256)
            if existing == _V5_DONE:
                return
            raise ValueError()
        self._write_v5_marker(_V5_DONE)

    @fenced_writer
    def _commit_packet(self, packet, patience=5.0) -> None:
        loaded = self._loaded
        if loaded is not None and loaded.version == 5:
            previous_sequence = (loaded.journal_records, loaded.journal_chain)
            self.assert_consistent(loaded)
            if previous_sequence != (loaded.journal_records, loaded.journal_chain):
                raise ManagedDownloadError('active generation changed')
        journal = None
        if packet['version'] in (3, 4, 5):
            if loaded is not None and loaded.version == 5:
                self._ensure_v5_committed()
            # Every full rewrite of a v5 root starts a new, empty journal (the entries it held are in
            # this index), and quarantine commits keep the v5 guard instead of downgrading it.
            generation = (loaded.generation or 0) + 1 if loaded is not None else 1
            parent = loaded.base_digest if loaded is not None and loaded.base_digest is not None else '0' * 64
            journal = self._new_journal(patience, generation=generation, parent=parent)
            packet = {'version': 5, 'entries': packet['entries'],
                      'exclusions': packet.get('exclusions', {}),
                      'quarantine_operations': packet.get('quarantine_operations', {}),
                      'journal': {'id': journal[0], 'generation': generation, 'parent': parent}}
        data = _canonical(packet)
        if len(data) > _MAX_INDEX_BYTES:
            raise ManagedDownloadError('generation index size exceeded', reason_code='storage_capacity')
        _safe_path(self.stage_dir)
        temporary = self.stage_dir / ('active-' + uuid4().hex + '.tmp')
        _write(temporary, data)
        _fsync_dir(self.stage_dir)
        if journal is not None:
            # The durable head is the commit point. If index replacement is interrupted,
            # the named, hash-checked staged index is rolled forward at the next open.
            if loaded is not None and loaded.version == 5:
                self.assert_consistent(loaded)
                if previous_sequence != (loaded.journal_records, loaded.journal_chain):
                    raise ManagedDownloadError('active generation changed')
            self._write_head({'generation': generation, 'id': journal[0],
                              'digest': hashlib.sha256(data).hexdigest(), 'candidate': temporary.name,
                              'records': 0, 'chain': hashlib.sha256(journal[1]).hexdigest()})
            self._install_prepared(journal[0], generation)
        _safe_path(self.index_path, file=True)
        _replace_retrying(temporary, self.index_path, patience)
        self._visibility_cache = None
        _fsync_dir(self.root)
        previous = loaded.jid if loaded is not None else None
        if journal is not None:
            self._ensure_v5_committed()
            if previous is not None:
                # Retention counts from THIS fold (the journal's last write may be long ago).
                for retired in (self._journal_file(previous), self._mark_file(previous)):
                    try:
                        os.utime(native_io_path(retired))
                    except OSError:
                        pass
            self._prune_journals({journal[0], previous})
        else:
            self._prune_journals(set())
        if loaded is not None:
            loaded.version = packet['version']
            loaded.indexed = {wid: dict(value) for wid, value in packet['entries'].items()}
            loaded.exclusions = deepcopy(packet.get('exclusions', {}))
            loaded.operations = deepcopy(packet.get('quarantine_operations', {}))
            loaded.base_sig = _identity(self.index_path)
            loaded.base_digest = hashlib.sha256(data).hexdigest()
            loaded.base_bytes = len(data)
            loaded.fold_failed = False
            loaded.mark_records = 0
            if journal is not None:
                loaded.jid = journal[0]
                loaded.generation, loaded.parent_digest = generation, parent
                loaded.jpath = self._journal_file(journal[0])
                loaded.journal_chain = hashlib.sha256(journal[1]).hexdigest()
                loaded.journal_hasher = hashlib.sha256(journal[1])
                loaded.journal_chains = [loaded.journal_chain]
                loaded.journal_records = 0
                loaded.journal_offset = len(journal[1])
                loaded.header_bytes = len(journal[1])
                loaded.journal_sig = _identity(loaded.jpath)
            else:
                loaded.jid = loaded.jpath = loaded.journal_chain = loaded.journal_sig = None
                loaded.generation = loaded.parent_digest = None
                loaded.journal_records = loaded.journal_offset = 0

    @fenced_writer
    def prepare_for_quarantine(self) -> None:
        """Install the v3 guard before sealing any quarantine operation."""
        self._recover_if_pending()
        with self._lock:
            state = self.state_snapshot()
            if state['quarantine_enabled']:
                return
            packet = {'version': 3, 'entries': self._index(),
                      'exclusions': {}, 'quarantine_operations': {}}
            self._commit_packet(packet)

    @fenced_writer
    def commit_quarantine(self, *, operation_id: str, expected_revision: str,
                          manifest_sha256: str, selection_sha256: str,
                          selected_pages, mode='copy'):
        if mode not in ('copy', 'move') or type(mode) is not str:
            raise ManagedDownloadError('quarantine mode rejected')
        if type(operation_id) is not str or not _OPERATION_ID.fullmatch(operation_id):
            raise ManagedDownloadError('quarantine operation rejected')
        _sha(expected_revision)
        _sha(manifest_sha256)
        _sha(selection_sha256)
        self._recover_if_pending()
        with self._lock:
            state = self.state_snapshot()
            if (not state['quarantine_enabled'] or state['revision'] != expected_revision
                    or operation_id in state['operations']):
                raise ManagedDownloadError('quarantine compare-and-swap rejected')
            exclusions = deepcopy(state['exclusions'])
            seen = set()
            for wid, page in selected_pages:
                _id(wid)
                if (type(page) is not int or not 0 <= page < MAX_PAGES
                        or wid not in state['entries'] or (wid, page) in seen):
                    raise ManagedDownloadError('quarantine selection rejected')
                seen.add((wid, page))
                pages = exclusions.setdefault(wid, {})
                if str(page) in pages:
                    raise ManagedDownloadError('quarantine compare-and-swap rejected')
                pages[str(page)] = operation_id
            if not 1 <= len(seen) <= _MAX_QUARANTINE_ITEMS:
                raise ManagedDownloadError('quarantine selection rejected')
            operations = deepcopy(state['operations'])
            if mode == 'move':
                operations = {oid: ({**op, 'mode': 'copy'} if 'mode' not in op else op)
                              for oid, op in operations.items()}
            operations[operation_id] = {
                'manifest': f'quarantine/operations/{operation_id}/manifest.json',
                'manifest_sha256': manifest_sha256,
                'selection_sha256': selection_sha256,
                'state': 'relocating' if mode == 'move' else 'committed',
            }
            version = 4 if mode == 'move' or any('mode' in op for op in operations.values()) else 3
            if version == 4:
                operations[operation_id]['mode'] = mode
            packet = {'version': version, 'entries': self._index(),
                      'exclusions': exclusions, 'quarantine_operations': operations}
            self._quarantine_fields(packet)
            self._commit_packet(packet)
            return self.state_snapshot()

    @fenced_writer
    def transition_quarantine(self, operation_id: str, *, expected_revision: str, state: str):
        if type(state) is not str or state not in ('moved', 'restoring'):
            raise ManagedDownloadError('quarantine transition rejected')
        self._recover_if_pending()
        with self._lock:
            current = self.state_snapshot()
            operation = current['operations'].get(operation_id)
            if (current['revision'] != expected_revision or operation is None
                    or operation.get('mode') != 'move'
                    or (state == 'moved' and operation['state'] != 'relocating')
                    or (state == 'restoring' and operation['state'] not in ('relocating', 'moved'))):
                raise ManagedDownloadError('quarantine compare-and-swap rejected')
            operations = deepcopy(current['operations'])
            operations[operation_id]['state'] = state
            packet = {'version': 4, 'entries': self._index(),
                      'exclusions': current['exclusions'],
                      'quarantine_operations': operations}
            self._quarantine_fields(packet)
            self._commit_packet(packet)
            return self.state_snapshot()

    @fenced_writer
    def purge_quarantine(self, operation_id: str, *, expected_revision: str):
        """Terminal commit before the bytes are destroyed (empty-trash intent).

        Exclusions stay (re-acquisition prevention); only copy mode 'committed'
        and move mode 'moved' can be purged. Working states are never touched.
        The copy payload is deliberately not verified: a damaged copy may be
        purged, and the new state never reads it again.
        """
        if type(operation_id) is not str or not _OPERATION_ID.fullmatch(operation_id):
            raise ManagedDownloadError('quarantine operation rejected')
        _sha(expected_revision)
        self._recover_if_pending()
        with self._lock:
            state = self.state_snapshot(verify_quarantine_payloads=False)
            operation = state['operations'].get(operation_id)
            moving = operation is not None and operation.get('mode') == 'move'
            if (state['revision'] != expected_revision or operation is None
                    or operation['state'] != ('moved' if moving else 'committed')):
                raise ManagedDownloadError('quarantine compare-and-swap rejected')
            operations = deepcopy(state['operations'])
            operations[operation_id]['state'] = 'purged'
            packet = {'version': 4 if any('mode' in op for op in operations.values()) else 3,
                      'entries': self._index(verify_quarantine_payloads=False),
                      'exclusions': state['exclusions'],
                      'quarantine_operations': operations}
            self._quarantine_fields(packet, verify_payloads=False)
            self._commit_packet(packet)
            return self.state_snapshot(verify_quarantine_payloads=False)

    @fenced_writer
    def restore_quarantine(self, operation_id: str, *, expected_revision: str):
        if type(operation_id) is not str or not _OPERATION_ID.fullmatch(operation_id):
            raise ManagedDownloadError('quarantine operation rejected')
        _sha(expected_revision)
        self._recover_if_pending()
        with self._lock:
            state = self.state_snapshot()
            operation = state['operations'].get(operation_id)
            if (state['revision'] != expected_revision or operation is None
                    or operation['state'] not in ('committed', 'restoring')):
                raise ManagedDownloadError('quarantine compare-and-swap rejected')
            if operation['state'] == 'restoring' and operation.get('mode') != 'move':
                raise ManagedDownloadError('quarantine compare-and-swap rejected')
            exclusions = {
                wid: {page: oid for page, oid in pages.items() if oid != operation_id}
                for wid, pages in state['exclusions'].items()
            }
            exclusions = {wid: pages for wid, pages in exclusions.items() if pages}
            operations = deepcopy(state['operations'])
            operations[operation_id]['state'] = 'restored'
            packet = {'version': 4 if operation.get('mode') else 3, 'entries': self._index(),
                      'exclusions': exclusions, 'quarantine_operations': operations}
            self._quarantine_fields(packet)
            self._commit_packet(packet)
            return self.state_snapshot()

    @staticmethod
    def _file_identity(path):
        _safe_path(path, file=True)
        value = native_io_path(path).lstat()
        return (value.st_dev, value.st_ino, value.st_size,
                value.st_mtime_ns, value.st_ctime_ns)

    def _visibility_signature(self):
        self._checked()
        head = self._read_head()
        anchor = None if head is None else (head['generation'], head['id'], head['digest'])
        return self._file_identity(self.index_path), anchor

    def assert_page_visible(self, work_id: str, page_no: int) -> None:
        """Check exclusions without rescanning all artwork for every image.

        Only a previously validated index can be cached. Relevant quarantine
        manifests and source receipts remain watched, including cache hits.
        Changing any watched file forces full validation, never an empty mask.
        """
        wid = _id(work_id)
        if type(page_no) is not int or not 0 <= page_no < MAX_PAGES:
            raise ManagedDownloadError('managed page identity rejected')
        self._recover_if_pending()
        with self._lock:
            try:
                signature = self._visibility_signature()
            except FileNotFoundError:
                self._checked()
                self._visibility_cache = None
                return
            cached = self._visibility_cache
            if cached is not None and cached[0] == signature:
                try:
                    unchanged = all(self._file_identity(self.root / relative) == identity
                                    for relative, identity in cached[2].get(wid, {}).items())
                except OSError:
                    unchanged = False
                if unchanged:
                    if str(page_no) in cached[1].get(wid, {}):
                        raise ManagedPageQuarantined('managed page is quarantined')
                    return
            self._visibility_cache = None
            self._checked()
            exclusions, operations, _ = self._quarantine_fields()
            watched = {}
            verified_proofs = {}
            for operation_id, operation in operations.items():
                relative = operation['manifest']
                manifest_path = self.root / relative
                manifest_identity = self._file_identity(manifest_path)
                raw = _read_bounded(manifest_path, MAX_RECEIPT_BYTES)
                if (hashlib.sha256(raw).hexdigest() != operation['manifest_sha256']
                        or self._file_identity(manifest_path) != manifest_identity):
                    raise ManagedDownloadError('quarantine manifest changed')
                manifest = json.loads(raw, object_pairs_hook=_json_unique)
                for item in manifest['items']:
                    item_wid = item['work_id']
                    paths = watched.setdefault(item_wid, {})
                    paths[relative] = manifest_identity
                    proof_relative = item['relative_receipt']
                    expected = item['receipt_sha256']
                    key = (proof_relative, expected)
                    if key not in verified_proofs:
                        proof_path = self.root / proof_relative
                        proof_identity = self._file_identity(proof_path)
                        proof = self._receipt_entry(proof_relative, item_wid, update_date='')
                        if (proof['sha256'] != expected
                                or self._file_identity(proof_path) != proof_identity):
                            raise ManagedDownloadError('quarantine source proof changed')
                        verified_proofs[key] = proof_identity
                    paths[proof_relative] = verified_proofs[key]
            if self._visibility_signature() != signature:
                raise ManagedDownloadError('quarantine index changed')
            self._visibility_cache = (signature, exclusions, watched)
            if str(page_no) in exclusions.get(wid, {}):
                raise ManagedPageQuarantined('managed page is quarantined')
