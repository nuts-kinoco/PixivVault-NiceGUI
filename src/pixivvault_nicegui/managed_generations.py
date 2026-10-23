"""Non-destructive CAS index for immutable managed Pixiv image generations.

Old v1 `outputs/` directories are never renamed or changed. Indexed v2
`generations/` directories become active only after their receipt was fully
written and verified. Unindexed generation folders are harmless crash orphans.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
import re
from threading import RLock
from uuid import uuid4

from .managed_downloads import (IDENTITY, MARKER, MAX_RECEIPT_BYTES, MAX_PAGE_BYTES,
                                MAX_PAGES, MAX_PIXELS, MAX_TOTAL, _image,
                                ManagedDownloadError, _fsync_dir, _json_unique,
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
_MAX_INDEX_BYTES = 2 * 1024 * 1024
_MAX_ENTRIES = 10000


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


class ManagedGenerations:
    """Index is owned by one managed-root writer; all returned data is copied."""

    def __init__(self, root: Path):
        self.root = Path(root)
        if not self.root.is_absolute() or not self.root.is_dir():
            raise ManagedDownloadError('generation root rejected')
        _safe_path(self.root)
        self.root = self.root.resolve()
        if json.loads(_read_bounded(self.root / MARKER, 4096),
                      object_pairs_hook=_json_unique) != IDENTITY:
            raise ManagedDownloadError('generation root rejected')
        self.output_dir = self.root / 'outputs'
        self.generation_dir = self.root / 'generations'
        self.stage_dir = self.root / 'generation-incomplete'
        self.index_path = self.root / 'active-generations.json'
        for folder in (self.output_dir, self.generation_dir, self.stage_dir):
            _safe_path(folder)
            folder.mkdir(exist_ok=True)
            _safe_path(folder)
        self._lock = RLock()
        # A malformed existing index must block startup, never be reset.
        self.entries()

    def _receipt_entry(self, relative: str, wid: str, *, update_date: str,
                       verify_complete: bool = False) -> dict[str, str]:
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
            raw = _read_bounded(path, MAX_RECEIPT_BYTES)
        except (OSError, ValueError):
            raise ManagedDownloadError('generation receipt rejected') from None
        digest = hashlib.sha256(raw).hexdigest()
        try:
            receipt = json.loads(raw, object_pairs_hook=_json_unique)
            if (type(receipt) is not dict or receipt.keys() != _RECEIPT_KEYS
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
            if verify_complete and {item.name for item in folder.iterdir()} != names:
                raise ValueError()
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise ManagedDownloadError('generation receipt rejected') from None
        return {'work_id': wid, 'relative_receipt': relative,
                'sha256': digest, 'update_date': update_date}

    def _legacy(self) -> dict[str, dict[str, str]]:
        _safe_path(self.output_dir)
        result: dict[str, dict[str, str]] = {}
        for item in self.output_dir.iterdir():
            if len(result) >= _MAX_ENTRIES:
                raise ManagedDownloadError('generation limit exceeded')
            match = _FOLDER.fullmatch(item.name)
            if match is None:
                raise ManagedDownloadError('legacy output structure rejected')
            _safe_path(item)
            wid = match.group(1)
            if wid in result:
                raise ManagedDownloadError('duplicate completed artwork rejected')
            relative = f'outputs/{item.name}/receipt.json'
            result[wid] = self._receipt_entry(relative, wid, update_date='')
        return result

    def _index(self) -> dict[str, dict[str, str]]:
        if self.index_path.is_symlink():
            raise ManagedDownloadError('generation index link rejected')
        if not self.index_path.exists():
            # Once any immutable generation was sealed, dropping the only
            # active pointer must NEVER silently expose old images or an
            # empty library. An empty pointer is installed before the first
            # generation is sealed, even if the subsequent save is cancelled.
            _safe_path(self.generation_dir)
            if any(self.generation_dir.iterdir()):
                raise ManagedDownloadError('active generation index missing')
            return {}
        try:
            raw = _read_bounded(self.index_path, _MAX_INDEX_BYTES)
            packet = json.loads(raw, object_pairs_hook=_json_unique)
            if (type(packet) is not dict or packet.keys() != _INDEX_KEYS
                    or type(packet['version']) is not int or packet['version'] not in (1, 2)
                    or type(packet['entries']) is not dict
                    or len(packet['entries']) > _MAX_ENTRIES):
                raise ValueError()
            result = {}
            for wid, entry in packet['entries'].items():
                _id(wid)
                if (type(entry) is not dict or
                        (entry.keys() != _ENTRY_KEYS and
                         (packet['version'] != 2 or entry.keys() != _ENTRY_WITH_JOB))):
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
                actual = self._receipt_entry(entry['relative_receipt'], wid, update_date=date)
                if actual['sha256'] != digest:
                    raise ValueError()
                result[wid] = dict(entry)
            return result
        except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            raise ManagedDownloadError('active generation index rejected') from None

    def entries(self) -> dict[str, dict[str, str]]:
        with self._lock:
            indexed = self._index()
            active = self._legacy()
            active.update(indexed)
            if len(active) > _MAX_ENTRIES:
                raise ManagedDownloadError('generation limit exceeded')
            # Nonactive directories are retained for recovery and pinned viewers;
            # reject link injection even for orphans rather than traversing it.
            _safe_path(self.generation_dir)
            for folder in self.generation_dir.iterdir():
                if not _FOLDER.fullmatch(folder.name):
                    raise ManagedDownloadError('generation folder structure rejected')
                _safe_path(folder)
            return {wid: dict(entry) for wid, entry in active.items()}

    def current(self, work_id: str) -> dict[str, str] | None:
        wid = _id(work_id)
        current = self.entries().get(wid)
        return dict(current) if current is not None else None

    def prepare_for_publication(self) -> None:
        """Persist a zero-entry pointer before exposing any new generation.

        A crash before the rename leaves no visible generation. A crash after
        the rename still leaves this pointer, so missing-index corruption can
        be distinguished from a fresh v1-only managed root on restart.
        """
        with self._lock:
            if self.index_path.exists():
                self._index()  # Never trust a preexisting malformed index.
                return
            self._index()  # Reject an already-missing index with generations.
            _safe_path(self.stage_dir)
            temporary = self.stage_dir / ('prepare-' + uuid4().hex + '.tmp')
            try:
                _write(temporary, b'{"entries":{},"version":2}')
                _fsync_dir(self.stage_dir)
                _safe_path(self.index_path, file=True)
                temporary.replace(self.index_path)
                _fsync_dir(self.root)
            except Exception:
                raise ManagedDownloadError('generation index preparation failed') from None

    def publish(self, work_id: str, relative_receipt: str, sha256: str,
                update_date: str, expected_previous_sha256: str | None,
                expected_previous_relative_receipt: str | None = None,
                accepted_fingerprint: str | None = None) -> dict[str, str]:
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
        with self._lock:
            active = self.entries()
            before = active.get(wid)
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
            indexed = self._index()
            indexed[wid] = target
            if len(indexed) > _MAX_ENTRIES:
                raise ManagedDownloadError('generation limit exceeded')
            packet = {'version': 2, 'entries': indexed}
            data = json.dumps(packet, sort_keys=True, ensure_ascii=True,
                              separators=(',', ':')).encode('ascii')
            if len(data) > _MAX_INDEX_BYTES:
                raise ManagedDownloadError('generation index size exceeded')
            _safe_path(self.stage_dir)
            temporary = self.stage_dir / ('active-' + uuid4().hex + '.tmp')
            try:
                _write(temporary, data)
                _fsync_dir(self.stage_dir)
                _safe_path(self.index_path, file=True)
                temporary.replace(self.index_path)
                _fsync_dir(self.root)
            except Exception:
                # If the replace already happened, the durable pointer remains
                # authoritative; the next retry verifies/reuses the generation.
                raise ManagedDownloadError('generation index publication failed') from None
            return dict(target)
