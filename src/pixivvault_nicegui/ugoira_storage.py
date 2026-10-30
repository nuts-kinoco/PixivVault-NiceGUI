"""Offline M2 ugoira frame persistence in an explicitly owned, isolated root.

ZIP validation finishes before any output is created. Fresh generations are
published by a no-replace directory rename, never into an old image library.
Failed stages and publication orphans are retained within finite quotas;
directory presence is not completion: reconcile() verifies the completion proof.
This module does not claim a job, active index, GIF/MP4, or power-loss guarantee.
As with managed originals, the private root must be owned by the application,
not concurrently modified by another filesystem writer.
"""
from __future__ import annotations
from .fs_paths import native_io_path

from contextlib import contextmanager
import ctypes
from dataclasses import dataclass, field
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import stat
import sys
from threading import Lock
from uuid import uuid4
import zipfile

from PIL import Image

from .managed_downloads import _fsync_dir, _json_unique, _read_bounded, _safe_path, _write
from .job_service import JobCancelled
from .media_parity_policy import MAX_UGOIRA_FRAMES, UgoiraManifest, parse_ugoira_manifest
from .ugoira_archive import (
    MAX_SINGLE_FRAME_BYTES, MAX_TOTAL_FRAME_BYTES, MAX_FRAME_PIXELS,
    MAX_TOTAL_DECODED_PIXELS, SUPPORTED_TYPES, InspectedFrame, InspectedUgoira, inspect_ugoira_archive,
)


MARKER = '.pixivvault-ugoira-frames.json'
_MARKER_BYTES = b'{"application":"pixivvault-nicegui","purpose":"isolated-ugoira-frames","version":1}\n'
_TOKEN = re.compile(r'[a-f0-9]{32}\Z')
_SHA = re.compile(r'[a-f0-9]{64}\Z')
_ID = re.compile(r'[1-9][0-9]{0,19}\Z')
_RECEIPT_PATH = re.compile(r'frames/([a-f0-9]{32})/receipt\.json\Z')
_OWNER = '.writer.lock'
COMPLETION = 'completion.json'
MAX_RECEIPT_BYTES = 2 * 1024 * 1024
MAX_COMPLETION_BYTES = 1024
# Retained logical file bytes and file/directory counts are bounded together.
# All generations count, including failed stages and uncommitted publications.
# The root marker (at most 4096 bytes) and one-byte owner lock are fixed overhead.
# Rejection never deletes data; operator-directed management is a separate task.
MAX_RETAINED_GENERATIONS = 128
MAX_RETAINED_FILES = 20_000
MAX_RETAINED_BYTES = 1024 * 1024 * 1024


class UgoiraStorageError(ValueError):
    """Public failure that never exposes a filesystem path or remote metadata."""

    def __init__(self):
        super().__init__('ugoira frame storage failed')

class UgoiraStorageIOError(UgoiraStorageError):
    """Transient filesystem I/O prevented a trustworthy reconciliation."""


@dataclass(frozen=True, slots=True)
class SavedUgoiraFrames:
    relative_receipt: str = field(repr=False)
    receipt_sha256: str = field(repr=False)
    frame_count: int
    total_uncompressed_bytes: int
    duration_ms: int


@dataclass(frozen=True, slots=True)
class ReconciledUgoiraFrames:
    state: str  # 'missing', 'uncommitted', or 'complete'; corruption raises.
    saved: SavedUgoiraFrames | None = field(default=None, repr=False)


def _acquire_owner_lock(descriptor: int) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.name == 'nt':
        import msvcrt
        # Windows byte-range locks may cover bytes beyond EOF. Acquire byte
        # zero even on a fresh empty file, before initializing its contents.
        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release_owner_lock(descriptor: int) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.name == 'nt':
        import msvcrt
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(descriptor, fcntl.LOCK_UN)


@contextmanager
def _owner_lock(root: Path, *, create: bool = True):
    """One exclusive OS lock for writes and reconciliation across instances.

    Creation uses neither append nor truncate. Only an exclusive lock holder
    may initialize byte zero. A reader opens an existing owner file read-only, without
    creating or repairing it; missing/empty/corrupt coordination fails closed.
    Closing this descriptor releases its lock, including all failure paths.
    """
    path = root / _OWNER
    _safe_path(path, file=True)
    flags = (os.O_RDWR if create else os.O_RDONLY) | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    if create:
        flags |= os.O_CREAT
    descriptor = os.open(path, flags, 0o600)
    locked = False
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size not in (0, 1):
            raise UgoiraStorageError()
        _acquire_owner_lock(descriptor)
        locked = True
        # A contender may have initialized the same file before we acquired
        # the lock. Always re-read size under ownership, never trust stale EOF.
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size not in (0, 1):
            raise UgoiraStorageError()
        if info.st_size == 0:
            if not create:
                raise UgoiraStorageError()
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.write(descriptor, b'0') != 1:
                raise UgoiraStorageError()
            os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        if os.fstat(descriptor).st_size != 1 or os.read(descriptor, 2) != b'0':
            raise UgoiraStorageError()
        yield
    finally:
        try:
            if locked:
                _release_owner_lock(descriptor)
        finally:
            os.close(descriptor)


def _completion_bytes(token: str, receipt_sha256: str) -> bytes:
    return (json.dumps({'version': 1, 'kind': 'ugoira-frames-complete',
                        'token': token, 'receipt_sha256': receipt_sha256},
                       separators=(',', ':')) + '\n').encode('utf-8')


def _saved_result(token: str, inspection: InspectedUgoira, receipt: bytes) -> SavedUgoiraFrames:
    return SavedUgoiraFrames(f'frames/{token}/receipt.json', hashlib.sha256(receipt).hexdigest(),
                            len(inspection.frames), inspection.total_uncompressed_bytes, inspection.duration_ms)


def _receipt_inspection(payload: bytes, checkpoint=lambda: None) -> tuple[InspectedUgoira, bytes]:
    """Revalidate persisted v1 metadata without trusting caller-built DTOs."""
    packet = json.loads(payload, object_pairs_hook=_json_unique)
    keys = {'version', 'kind', 'work_id', 'duration_ms', 'total_uncompressed_bytes', 'frames'}
    if (type(packet) is not dict or packet.keys() != keys
            or type(packet['version']) is not int or packet['version'] != 1
            or packet['kind'] != 'ugoira-frames' or type(packet['work_id']) is not str
            or _ID.fullmatch(packet['work_id']) is None
            or type(packet['frames']) is not list or not 1 <= len(packet['frames']) <= MAX_UGOIRA_FRAMES):
        raise UgoiraStorageError()
    rows = packet['frames']
    row_keys = {'file', 'delay', 'width', 'height', 'bytes', 'sha256'}
    if any(type(row) is not dict or row.keys() != row_keys for row in rows):
        raise UgoiraStorageError()
    # This URL is a local parser input only, never fetched, stored, or logged.
    canonical = parse_ugoira_manifest({
        'originalSrc': f"https://i.pximg.net/img-zip-ugoira/offline/{packet['work_id']}_ugoira.zip",
        'frames': [{'file': row['file'], 'delay': row['delay']} for row in rows],
    }, work_id=packet['work_id'])
    details = []
    total_bytes = total_pixels = 0
    for row in rows:
        checkpoint()
        if (any(type(row[key]) is not int for key in ('width', 'height', 'bytes'))
                or row['width'] <= 0 or row['height'] <= 0
                or row['width'] * row['height'] > MAX_FRAME_PIXELS
                or not 0 < row['bytes'] <= MAX_SINGLE_FRAME_BYTES
                or type(row['sha256']) is not str or _SHA.fullmatch(row['sha256']) is None):
            raise UgoiraStorageError()
        total_bytes += row['bytes']
        total_pixels += row['width'] * row['height']
        if total_bytes > MAX_TOTAL_FRAME_BYTES or total_pixels > MAX_TOTAL_DECODED_PIXELS:
            raise UgoiraStorageError()
        details.append(InspectedFrame(row['file'], row['width'], row['height'],
                                      row['bytes'], row['sha256'], row['delay']))
    if (type(packet['duration_ms']) is not int or packet['duration_ms'] != canonical.duration_ms
            or type(packet['total_uncompressed_bytes']) is not int
            or packet['total_uncompressed_bytes'] != total_bytes):
        raise UgoiraStorageError()
    inspection = InspectedUgoira(canonical.work_id, tuple(details), total_bytes, canonical.duration_ms)
    metadata, canonical_receipt = _metadata(inspection)
    if payload != canonical_receipt:
        raise UgoiraStorageError()
    return inspection, metadata


def _directory_identity(path: Path) -> tuple[int, int]:
    _safe_path(path)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise UgoiraStorageError()
    return info.st_dev, info.st_ino


def _publish_directory(source: Path, target: Path) -> None:
    """Same-filesystem publication must not replace even an empty directory."""
    _safe_path(source)
    _safe_path(target.parent)
    if os.name == 'nt':
        # Windows rename fails if target exists, including an empty directory.
        os.rename(source, target)
    elif sys.platform.startswith('linux'):
        # POSIX rename alone can replace an existing empty directory. Do not
        # fall back to that behavior if atomic no-replace is unavailable.
        libc = ctypes.CDLL(None, use_errno=True)
        rename = libc.renameat2
        rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
        rename.restype = ctypes.c_int
        if rename(-100, os.fsencode(source), -100, os.fsencode(target), 1) != 0:
            raise UgoiraStorageError()
    else:
        raise UgoiraStorageError()


def _metadata(inspection: InspectedUgoira) -> tuple[bytes, bytes]:
    frames = [{'file': frame.filename, 'delay': frame.delay_ms} for frame in inspection.frames]
    metadata = (json.dumps({'frames': frames}, ensure_ascii=True, separators=(',', ':')) + '\n').encode('utf-8')
    receipt = {
        'version': 1, 'kind': 'ugoira-frames', 'work_id': inspection.work_id,
        'duration_ms': inspection.duration_ms,
        'total_uncompressed_bytes': inspection.total_uncompressed_bytes,
        'frames': [{'file': frame.filename, 'delay': frame.delay_ms,
                    'width': frame.width, 'height': frame.height,
                    'bytes': frame.bytes_count, 'sha256': frame.sha256}
                   for frame in inspection.frames],
    }
    payload = (json.dumps(receipt, ensure_ascii=True, separators=(',', ':')) + '\n').encode('utf-8')
    return metadata, payload


def _verify_contents(folder: Path, inspection: InspectedUgoira, metadata: bytes, receipt: bytes,
                     completion: bytes | None = None, checkpoint=lambda: None) -> None:
    checkpoint()
    _safe_path(folder)
    expected_names = {frame.filename for frame in inspection.frames} | {'ugoira_meta.json', 'receipt.json'}
    if completion is not None:
        expected_names.add(COMPLETION)
    seen = set()
    with os.scandir(folder) as children:
        for child in children:
            checkpoint()
            if child.name not in expected_names or child.name in seen:
                raise UgoiraStorageError()
            seen.add(child.name)
    if seen != expected_names:
        raise UgoiraStorageError()
    for frame in inspection.frames:
        checkpoint()
        content = _read_bounded(folder / frame.filename, frame.bytes_count)
        if len(content) != frame.bytes_count or hashlib.sha256(content).hexdigest() != frame.sha256:
            raise UgoiraStorageError()
    if (_read_bounded(folder / 'ugoira_meta.json', len(metadata)) != metadata
            or _read_bounded(folder / 'receipt.json', len(receipt)) != receipt):
        raise UgoiraStorageError()
    if completion is not None and _read_bounded(folder / COMPLETION, MAX_COMPLETION_BYTES) != completion:
        raise UgoiraStorageError()


class UgoiraFrameStore:
    """Lazy, separate M2 storage; only fresh roots or our exact marker qualify.

    Existing unmarked directories (even empty ones) are never adopted. Existing
    marked generations are immutable. The caller provides an explicit private
    destination; no legacy application path, database, or client is consulted.
    """

    def __init__(self, root: Path):
        try:
            path = Path(root)
            if not path.is_absolute() or path == path.parent or '..' in path.parts:
                raise UgoiraStorageError()
            _safe_path(path)
            self.root = native_io_path(path.resolve())
            self.incomplete = self.root / 'incomplete'
            self.frames = self.root / 'frames'
            self._identities: dict[Path, tuple[int, int]] = {}
            self._lock = Lock()
        except Exception:
            raise UgoiraStorageError() from None

    def _prepare_root(self) -> None:
        _safe_path(self.root)
        if self.root.exists():
            if _read_bounded(self.root / MARKER, 4096) != _MARKER_BYTES:
                raise UgoiraStorageError()
            allowed = {MARKER, 'frames', 'incomplete', _OWNER}
            with os.scandir(self.root) as children:
                for child in children:
                    if child.name not in allowed:
                        raise UgoiraStorageError()
        else:
            self.root.mkdir(mode=0o700)
            _write(self.root / MARKER, _MARKER_BYTES)
        for folder in (self.incomplete, self.frames):
            _safe_path(folder)
            folder.mkdir(mode=0o700, exist_ok=True)
        for folder in (self.root, self.incomplete, self.frames):
            identity = _directory_identity(folder)
            if folder in self._identities and self._identities[folder] != identity:
                raise UgoiraStorageError()
            self._identities[folder] = identity
        # Bootstrap the coordination file under the same OS lock, including
        # roots created before the first save. Reconciliation never creates it.
        with _owner_lock(self.root):
            self._check_root()
            _fsync_dir(self.root)
            _fsync_dir(self.root.parent)

    def _check_root(self) -> None:
        for folder, expected in self._identities.items():
            if _directory_identity(folder) != expected:
                raise UgoiraStorageError()
        if _read_bounded(self.root / MARKER, 4096) != _MARKER_BYTES:
            raise UgoiraStorageError()

    def _reserve_budget(self, incoming_files: int, incoming_bytes: int) -> None:
        """Under _owner_lock, reserve a whole save before creating its stage."""
        generations = files = size = 0
        for parent in (self.incomplete, self.frames):
            _safe_path(parent)
            with os.scandir(parent) as children:
                for child in children:
                    generations += 1
                    if generations + 1 > MAX_RETAINED_GENERATIONS or _TOKEN.fullmatch(child.name) is None:
                        raise UgoiraStorageError()
                    folder = parent / child.name
                    _directory_identity(folder)
                    with os.scandir(folder) as contents:
                        for item in contents:
                            files += 1
                            if files + incoming_files > MAX_RETAINED_FILES:
                                raise UgoiraStorageError()
                            # Windows DirEntry.stat() may report st_nlink=0
                            # for an ordinary file. Query the path directly so
                            # real files count as one link and hardlinks remain
                            # rejected by the same invariant below.
                            info = os.stat(item.path, follow_symlinks=False)
                            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                                    or getattr(info, 'st_file_attributes', 0) & 0x400 or info.st_size < 0):
                                raise UgoiraStorageError()
                            size += info.st_size
                            if size + incoming_bytes > MAX_RETAINED_BYTES:
                                raise UgoiraStorageError()
        if (generations + 1 > MAX_RETAINED_GENERATIONS or files + incoming_files > MAX_RETAINED_FILES
                or size + incoming_bytes > MAX_RETAINED_BYTES):
            raise UgoiraStorageError()

    @contextmanager
    def _reconcile_lock(self, checkpoint):
        # Busy OS ownership still fails closed. A reader waiting on this
        # instance's lock checks cancellation without an unrelated mutex.
        while not self._lock.acquire(timeout=0.05):
            checkpoint()
        try:
            yield
        finally:
            self._lock.release()

    def reconcile(self, relative_receipt: str, *, expected_sha256: str | None = None,
                  checkpoint=None) -> ReconciledUgoiraFrames:
        """Read only: folder/receipt presence never establishes completion.

        Missing folders are 'missing'; a folder without completion.json is
        'uncommitted', including old v1 output and a post-rename failed save.
        A canonical proof binding the token and receipt hash, exact metadata,
        all file hashes and bounded image decodes are required for 'complete'.
        The existing OS owner lock excludes saves for the entire check; busy,
        missing, empty or corrupt coordination raises rather than reporting a
        snapshot taken during publication. No lock/proof is created or repaired.
        Corruption raises; no marker is rebuilt and no orphan is auto-adopted.
        """
        check = (lambda: None) if checkpoint is None else checkpoint
        if not callable(check):
            raise UgoiraStorageError()
        try:
            check()
            with self._reconcile_lock(check):
                check()
                if type(relative_receipt) is not str or len(relative_receipt) > 64:
                    raise UgoiraStorageError()
                match = _RECEIPT_PATH.fullmatch(relative_receipt)
                if match is None or (expected_sha256 is not None and
                        (type(expected_sha256) is not str or _SHA.fullmatch(expected_sha256) is None)):
                    raise UgoiraStorageError()
                with _owner_lock(self.root, create=False):
                    for folder in (self.root, self.incomplete, self.frames):
                        check()
                        identity = _directory_identity(folder)
                        if folder in self._identities and self._identities[folder] != identity:
                            raise UgoiraStorageError()
                        self._identities[folder] = identity
                    self._check_root()
                    token = match.group(1)
                    folder = self.frames / token
                    _safe_path(folder)
                    if not folder.exists():
                        return ReconciledUgoiraFrames('missing')
                    try:
                        completion = _read_bounded(folder / COMPLETION, MAX_COMPLETION_BYTES)
                    except FileNotFoundError:
                        return ReconciledUgoiraFrames('uncommitted')
                    check()
                    receipt = _read_bounded(folder / 'receipt.json', MAX_RECEIPT_BYTES)
                    digest = hashlib.sha256(receipt).hexdigest()
                    if ((expected_sha256 is not None and digest != expected_sha256)
                            or completion != _completion_bytes(token, digest)):
                        raise UgoiraStorageError()
                    inspection, metadata = _receipt_inspection(receipt, check)
                    _verify_contents(folder, inspection, metadata, receipt, completion, check)
                    for frame in inspection.frames:
                        check()
                        content = _read_bounded(folder / frame.filename, frame.bytes_count)
                        expected_format = SUPPORTED_TYPES['.' + frame.filename.rsplit('.', 1)[-1]]
                        with Image.open(BytesIO(content), formats=(expected_format,)) as image:
                            if (image.format != expected_format or image.size != (frame.width, frame.height)
                                    or getattr(image, 'n_frames', 1) != 1):
                                raise UgoiraStorageError()
                            image.load()
                        check()
                    self._check_root()
                    return ReconciledUgoiraFrames('complete', _saved_result(token, inspection, receipt))
        except JobCancelled:
            raise
        except FileNotFoundError:
            # A missing file after the completion marker is an integrity failure,
            # not a transient read error. Missing completion itself is handled above.
            raise UgoiraStorageError() from None
        except OSError:
            raise UgoiraStorageIOError() from None
        except Exception:
            raise UgoiraStorageError() from None

    def save(self, raw_zip: bytes, manifest: UgoiraManifest, *, generation_token: str | None = None,
             checkpoint=None) -> SavedUgoiraFrames:
        # All raw frames must validate before storage preparation or reservation.
        check = (lambda: None) if checkpoint is None else checkpoint
        if not callable(check):
            raise UgoiraStorageError()
        check()
        inspection = inspect_ugoira_archive(raw_zip, manifest)
        check()
        try:
            with self._lock:
                if self._identities:
                    self._check_root()
                self._prepare_root()
                with _owner_lock(self.root):
                    self._check_root()
                    token = uuid4().hex if generation_token is None else generation_token
                    if type(token) is not str or _TOKEN.fullmatch(token) is None:
                        raise UgoiraStorageError()
                    metadata, receipt = _metadata(inspection)
                    completion = _completion_bytes(token, hashlib.sha256(receipt).hexdigest())
                    if (len(metadata) > MAX_RECEIPT_BYTES or len(receipt) > MAX_RECEIPT_BYTES
                            or len(completion) > MAX_COMPLETION_BYTES):
                        raise UgoiraStorageError()
                    self._reserve_budget(len(inspection.frames) + 3,
                                         inspection.total_uncompressed_bytes + len(metadata) + len(receipt) + len(completion))
                    # Retried jobs use a stable final candidate but retain each
                    # failed stage separately; old stages are never overwritten.
                    stage_token = token if generation_token is None else uuid4().hex
                    if type(stage_token) is not str or _TOKEN.fullmatch(stage_token) is None:
                        raise UgoiraStorageError()
                    stage, target = self.incomplete / stage_token, self.frames / token
                    check()
                    stage.mkdir(mode=0o700)
                    stage_identity = _directory_identity(stage)
                    with zipfile.ZipFile(BytesIO(raw_zip), 'r', allowZip64=True) as archive:
                        for frame in inspection.frames:
                            check()
                            self._check_root()
                            if _directory_identity(stage) != stage_identity:
                                raise UgoiraStorageError()
                            with archive.open(frame.filename, 'r') as stream:
                                content = stream.read(MAX_SINGLE_FRAME_BYTES + 1)
                            if (len(content) != frame.bytes_count
                                    or hashlib.sha256(content).hexdigest() != frame.sha256):
                                raise UgoiraStorageError()
                            check()
                            _write(stage / frame.filename, content)
                    self._check_root()
                    if _directory_identity(stage) != stage_identity:
                        raise UgoiraStorageError()
                    _write(stage / 'ugoira_meta.json', metadata)
                    _write(stage / 'receipt.json', receipt)
                    _verify_contents(stage, inspection, metadata, receipt)
                    _fsync_dir(stage)
                    self._check_root()
                    if _directory_identity(stage) != stage_identity:
                        raise UgoiraStorageError()
                    check()
                    _publish_directory(stage, target)
                    _fsync_dir(self.frames)
                    _fsync_dir(self.incomplete)
                    self._check_root()
                    if _directory_identity(target) != stage_identity:
                        raise UgoiraStorageError()
                    _verify_contents(target, inspection, metadata, receipt)
                    # This proof is created only after the complete published
                    # generation was checked. A failed/partial proof is never
                    # sufficient for reconcile(), even if the folder exists.
                    check()
                    _write(target / COMPLETION, completion)
                    _fsync_dir(target)
                    _fsync_dir(self.frames)
                    if _read_bounded(target / COMPLETION, MAX_COMPLETION_BYTES) != completion:
                        raise UgoiraStorageError()
                    self._check_root()
                    return _saved_result(token, inspection, receipt)
        except JobCancelled:
            raise
        except Exception:
            # No deletion or rollback. A caller-visible failure may be either
            # uncommitted or committed-but-unacknowledged; only reconcile() can
            # establish completion from persisted, fully verified evidence.
            raise UgoiraStorageError() from None


__all__ = ['UgoiraFrameStore', 'SavedUgoiraFrames', 'ReconciledUgoiraFrames', 'UgoiraStorageError']
