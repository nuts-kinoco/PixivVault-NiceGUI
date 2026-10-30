"""Non-destructive CAS index for immutable managed Pixiv image generations.

Old v1 `outputs/` directories are never renamed or changed. Indexed v2
`generations/` directories become active only after their receipt was fully
written and verified. Unindexed generation folders are harmless crash orphans.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import re
from threading import RLock
from uuid import uuid4

from .fs_paths import logical_path, native_io_path
from .managed_downloads import (IDENTITY, MARKER, MAX_RECEIPT_BYTES, MAX_PAGE_BYTES,
                                MAX_PAGES, MAX_PIXELS, MAX_TOTAL, _image,
                                ManagedDownloadError, ManagedPageQuarantined,
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
_MAX_INDEX_BYTES = 2 * 1024 * 1024
_MAX_ENTRIES = 10000
_QUARANTINE_KEYS = _INDEX_KEYS | {'exclusions', 'quarantine_operations'}
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


class ManagedGenerations:
    """Index is owned by one managed-root writer; all returned data is copied."""

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
        for folder in (self.output_dir, self.generation_dir, self.stage_dir):
            _safe_path(folder)
            native_io_path(folder).mkdir(exist_ok=True)
            _safe_path(folder)
        self._lock = RLock()
        self._visibility_cache = None
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
            if verify_complete and {item.name for item in native_io_path(folder).iterdir()} != names:
                raise ValueError()
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise ManagedDownloadError('generation receipt rejected') from None
        return {'work_id': wid, 'relative_receipt': relative,
                'sha256': digest, 'update_date': update_date}

    def _legacy(self) -> dict[str, dict[str, str]]:
        _safe_path(self.output_dir)
        result: dict[str, dict[str, str]] = {}
        for item in native_io_path(self.output_dir).iterdir():
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
        if native_io_path(self.index_path).is_symlink():
            raise ManagedDownloadError('generation index link rejected')
        if not native_io_path(self.index_path).exists():
            # Once any immutable generation was sealed, dropping the only
            # active pointer must NEVER silently expose old images or an
            # empty library. An empty pointer is installed before the first
            # generation is sealed, even if the subsequent save is cancelled.
            _safe_path(self.generation_dir)
            if any(native_io_path(self.generation_dir).iterdir()) or self._has_quarantine_records():
                raise ManagedDownloadError('active generation index missing')
            return {}
        try:
            raw = _read_bounded(self.index_path, _MAX_INDEX_BYTES)
            packet = json.loads(raw, object_pairs_hook=_json_unique)
            if (type(packet) is not dict or type(packet.get('version')) is not int
                    or packet['version'] not in (1, 2, 3, 4)):
                raise ValueError()
            expected_keys = _QUARANTINE_KEYS if packet['version'] in (3, 4) else _INDEX_KEYS
            if (packet.keys() != expected_keys or type(packet.get('entries')) is not dict
                    or len(packet['entries']) > _MAX_ENTRIES):
                raise ValueError()
            if packet['version'] not in (3, 4) and self._has_quarantine_records():
                raise ValueError()
            self._quarantine_fields(packet)
            result = {}
            for wid, entry in packet['entries'].items():
                _id(wid)
                if (type(entry) is not dict or
                        (entry.keys() != _ENTRY_KEYS and
                         (packet['version'] not in (2, 3, 4) or entry.keys() != _ENTRY_WITH_JOB))):
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
            for folder in native_io_path(self.generation_dir).iterdir():
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
            if native_io_path(self.index_path).exists():
                self._index()  # Never trust a preexisting malformed index.
                return
            self._index()  # Reject an already-missing index with generations.
            _safe_path(self.stage_dir)
            temporary = self.stage_dir / ('prepare-' + uuid4().hex + '.tmp')
            try:
                _write(temporary, b'{"entries":{},"version":2}')
                _fsync_dir(self.stage_dir)
                _safe_path(self.index_path, file=True)
                native_io_path(temporary).replace(native_io_path(self.index_path))
                _fsync_dir(self.root)
            except Exception:
                raise ManagedDownloadError('generation index preparation failed') from None

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
        with self._lock:
            active = self.entries()
            before = active.get(wid)
            state = self.state_snapshot()
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
            actual_token = self._work_token(state, wid)
            if expected_work_state_token is not None:
                _sha(expected_work_state_token)
                if actual_token != expected_work_state_token:
                    raise ManagedDownloadError('generation compare-and-swap rejected')
            elif state['exclusions'].get(wid):
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
            indexed = self._index()
            indexed[wid] = target
            if len(indexed) > _MAX_ENTRIES:
                raise ManagedDownloadError('generation limit exceeded')
            packet = {'version': 2, 'entries': indexed}
            if state['quarantine_enabled']:
                version = 4 if any('mode' in op for op in state['operations'].values()) else 3
                if version == 4:
                    # A relocation pins its own work's immutable receipt. An
                    # unrelated work may still publish a new generation.
                    if any(proof['item']['work_id'] == wid
                           for proof in self.relocation_proofs().values()):
                        raise ManagedDownloadError('relocation blocks generation publication')
                packet.update(version=version, exclusions=state['exclusions'],
                              quarantine_operations=state['operations'])
            try:
                self._commit_packet(packet)
            except Exception:
                # If the replace already happened, the durable pointer remains
                # authoritative; the next retry verifies/reuses the generation.
                raise ManagedDownloadError('generation index publication failed') from None
            return dict(target)

    def _has_quarantine_records(self) -> bool:
        """Completed quarantine files require their authoritative v3 pointer.

        Incomplete copies do not change visibility. The empty v3 pointer is
        installed before any operation enters the completed namespace.
        """
        folder = native_io_path(self.root / 'quarantine' / 'operations')
        _safe_path(folder)
        return folder.exists() and any(folder.iterdir())

    def _quarantine_fields(self, packet=None):
        """Validate index/manifest agreement without exposing copied images.

        The immutable original receipt remains the source proof. A missing or
        malformed exclusion is never interpreted as permission to show a page.
        """
        try:
            if packet is None:
                if not native_io_path(self.index_path).exists():
                    return {}, {}, False
                packet = json.loads(_read_bounded(self.index_path, _MAX_INDEX_BYTES),
                                    object_pairs_hook=_json_unique)
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
                allowed_states = ('relocating', 'moved', 'restoring', 'restored') if moving else ('committed', 'restored')
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
                raw = _read_bounded(self.root / relative, MAX_RECEIPT_BYTES)
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
                        if operation['state'] == 'moved' and native_io_path(source_path).exists():
                            raise ValueError()
                        if operation['state'] == 'restored' and not native_io_path(source_path).is_file():
                            raise ValueError()
                    if operation['state'] in ('committed', 'relocating', 'moved', 'restoring'):
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
            if exclusions != expected:
                raise ValueError()
            return deepcopy(exclusions), deepcopy(operations), True
        except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            raise ManagedDownloadError('quarantine index rejected') from None

    @staticmethod
    def _state_digest(value) -> str:
        raw = json.dumps(value, sort_keys=True, ensure_ascii=True,
                         separators=(',', ':')).encode('ascii')
        return hashlib.sha256(raw).hexdigest()

    def state_snapshot(self):
        with self._lock:
            active = self.entries()
            exclusions, operations, enabled = self._quarantine_fields()
            if any(wid not in active for wid in exclusions):
                raise ManagedDownloadError('quarantine source receipt missing')
            value = {'entries': active, 'exclusions': exclusions, 'operations': operations}
            return {**value, 'revision': self._state_digest(value),
                    'quarantine_enabled': enabled}

    def relocation_proofs(self):
        """Exact source receipt/page -> verified managed backup for v4 moves.

        Never use this to excuse a missing unselected page. Every returned proof
        comes from a validated manifest and immutable original receipt.
        """
        with self._lock:
            self._quarantine_fields()
            if not native_io_path(self.index_path).exists():
                return {}
            packet = json.loads(_read_bounded(self.index_path, _MAX_INDEX_BYTES),
                                object_pairs_hook=_json_unique)
            if packet['version'] != 4:
                return {}
            result = {}
            for operation_id, operation in packet['quarantine_operations'].items():
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

    def _commit_packet(self, packet) -> None:
        data = json.dumps(packet, sort_keys=True, ensure_ascii=True,
                          separators=(',', ':')).encode('ascii')
        if len(data) > _MAX_INDEX_BYTES:
            raise ManagedDownloadError('generation index size exceeded')
        _safe_path(self.stage_dir)
        temporary = self.stage_dir / ('active-' + uuid4().hex + '.tmp')
        _write(temporary, data)
        _fsync_dir(self.stage_dir)
        _safe_path(self.index_path, file=True)
        native_io_path(temporary).replace(native_io_path(self.index_path))
        self._visibility_cache = None
        _fsync_dir(self.root)

    def prepare_for_quarantine(self) -> None:
        """Install the v3 guard before sealing any quarantine operation."""
        with self._lock:
            state = self.state_snapshot()
            if state['quarantine_enabled']:
                return
            packet = {'version': 3, 'entries': self._index(),
                      'exclusions': {}, 'quarantine_operations': {}}
            self._commit_packet(packet)

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

    def transition_quarantine(self, operation_id: str, *, expected_revision: str, state: str):
        if type(state) is not str or state not in ('moved', 'restoring'):
            raise ManagedDownloadError('quarantine transition rejected')
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

    def restore_quarantine(self, operation_id: str, *, expected_revision: str):
        if type(operation_id) is not str or not _OPERATION_ID.fullmatch(operation_id):
            raise ManagedDownloadError('quarantine operation rejected')
        _sha(expected_revision)
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

    def assert_page_visible(self, work_id: str, page_no: int) -> None:
        """Check exclusions without rescanning all artwork for every image.

        Only a previously validated index can be cached. Relevant quarantine
        manifests and source receipts remain watched, including cache hits.
        Changing any watched file forces full validation, never an empty mask.
        """
        wid = _id(work_id)
        if type(page_no) is not int or not 0 <= page_no < MAX_PAGES:
            raise ManagedDownloadError('managed page identity rejected')
        with self._lock:
            try:
                signature = self._file_identity(self.index_path)
            except FileNotFoundError:
                self._index()
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
            self._index()
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
            if self._file_identity(self.index_path) != signature:
                raise ManagedDownloadError('quarantine index changed')
            self._visibility_cache = (signature, exclusions, watched)
            if str(page_no) in exclusions.get(wid, {}):
                raise ManagedPageQuarantined('managed page is quarantined')
