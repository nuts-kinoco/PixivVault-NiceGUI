"""Recoverable managed-page quarantine, including explicit owned source relocation."""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from threading import RLock
from uuid import uuid4

from .fs_paths import native_io_path
from .managed_downloads import (
    IDENTITY, MARKER, MAX_PAGE_BYTES, MAX_RECEIPT_BYTES, ManagedDownloadError,
    ManagedPixivDownloads, _fsync_dir, _image, _json_unique, _read_bounded,
    _safe_path, _write,
)

_OPERATION = re.compile(r'[a-f0-9]{32}\Z')
_QMARKER = '.pixivvault-managed-quarantine.json'
_QIDENTITY = {'application': 'pixivvault-nicegui',
              'purpose': 'managed-original-quarantine', 'version': 1}


def _packet(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True,
                      separators=(',', ':')).encode('ascii')


@dataclass(frozen=True)
class QuarantineResult:
    operation_id: str
    committed: tuple
    failed: tuple
    state_revision: str
    cleanup_warning: bool = False


@dataclass(frozen=True)
class QuarantineSnapshot:
    state: str
    copy_count: int
    copy_bytes: int
    excluded_pages: int
    location: Path | None
    state_revision: str

class ManagedQuarantine:
    def __init__(self, owner):
        if not isinstance(owner, ManagedPixivDownloads):
            raise ManagedDownloadError('quarantine owner rejected')
        self.owner = owner
        self.root = owner.root / 'quarantine'
        self.incomplete = self.root / 'incomplete'
        self.operations = self.root / 'operations'
        self._lock = RLock()

    def _check(self):
        if self.owner.closed:
            raise ManagedDownloadError('managed download is closed')
        if json.loads(_read_bounded(self.owner.root / MARKER, 4096),
                      object_pairs_hook=_json_unique) != IDENTITY:
            raise ManagedDownloadError('quarantine owner rejected')

    def location(self):
        """Resolve an existing owned trash directory without creating anything."""
        with self._lock:
            self._check()
            _safe_path(self.root)
            try:
                native_io_path(self.root).lstat()
            except FileNotFoundError:
                return None
            marker = self.root / _QMARKER
            if json.loads(_read_bounded(marker, 4096),
                          object_pairs_hook=_json_unique) != _QIDENTITY:
                raise ManagedDownloadError('quarantine root rejected')
            return self.root

    def snapshot(self):
        """Count present published copies separately from active exclusions.

        Validated manifests identify published files; incomplete/orphan outputs
        are not counted. A removed copy does not remove its durable exclusion.
        No image decode, GET, directory preparation or metadata write occurs.
        """
        with self._lock:
            try:
                location = self.location()
                state = self.owner._generations.state_snapshot()
                excluded = sum(len(pages) for pages in state['exclusions'].values())
                if location is None:
                    if state['operations'] or excluded:
                        raise ManagedDownloadError('quarantine root missing')
                    return QuarantineSnapshot('uncreated', 0, 0, 0, None,
                                              state['revision'])
                count = total = 0
                for operation_id, operation in state['operations'].items():
                    # state_snapshot already validates schema, original proof,
                    # exact filename, operation identity and manifest SHA.
                    manifest = self._manifest(operation_id, operation)
                    folder = self.operations / operation_id
                    _safe_path(folder)
                    for item in manifest['items']:
                        path = folder / item['file']
                        _safe_path(path, file=True)
                        try:
                            info = native_io_path(path).lstat()
                        except FileNotFoundError:
                            continue
                        if info.st_size != item['bytes']:
                            raise ManagedDownloadError('quarantine copy changed')
                        count += 1
                        total += info.st_size
                if self.owner._generations.state_snapshot()['revision'] != state['revision']:
                    raise ManagedDownloadError('quarantine snapshot changed')
                if self.location() != location:
                    raise ManagedDownloadError('quarantine root changed')
                return QuarantineSnapshot('ready', count, total, excluded,
                                          location, state['revision'])
            except ManagedDownloadError:
                raise
            except (OSError, ValueError, TypeError, KeyError):
                raise ManagedDownloadError('quarantine summary rejected') from None

    def _prepare(self):
        self._check()
        _safe_path(self.root)
        root_io = native_io_path(self.root)
        if not root_io.exists():
            root_io.mkdir()
        marker = root_io / _QMARKER
        _safe_path(marker, file=True)
        if marker.exists():
            if json.loads(_read_bounded(marker, 4096),
                          object_pairs_hook=_json_unique) != _QIDENTITY:
                raise ManagedDownloadError('quarantine root rejected')
        else:
            if any(root_io.iterdir()):
                raise ManagedDownloadError('foreign quarantine root rejected')
            _write(marker, _packet(_QIDENTITY))
        for folder in (self.incomplete, self.operations):
            _safe_path(folder)
            native_io_path(folder).mkdir(exist_ok=True)
            _safe_path(folder)
        _fsync_dir(self.root)

    @staticmethod
    def selection_digest(plan):
        value = {
            'scan_id': plan.scan_id,
            'snapshot_sha256': plan.snapshot_sha256,
            'items': [(item.work_id, item.page, item.relative_receipt,
                       item.receipt_sha256, item.image_sha256,
                       item.file_size, item.width, item.height)
                      for item in plan.items],
            'remaining': list(plan.remaining),
        }
        return hashlib.sha256(_packet(value)).hexdigest()

    @staticmethod
    def _validate_plan(plan):
        from .duplicate_inspection import QuarantinePlan
        if (not isinstance(plan, QuarantinePlan) or not plan.mutation_allowed
                or not 1 <= len(plan.items) <= 250
                or not re.fullmatch(r'[a-f0-9]{64}', plan.state_revision)):
            raise ManagedDownloadError('quarantine plan rejected')
        chosen = {item.key: item for item in plan.items}
        if len(chosen) != len(plan.items):
            raise ManagedDownloadError('quarantine selection rejected')
        expected_remaining = dict(plan.remaining)
        if (len(expected_remaining) != len(plan.remaining)
                or {group.group_id for group in plan.groups} != set(expected_remaining)):
            raise ManagedDownloadError('quarantine group changed')
        matched = set()
        keepers = []
        for group in plan.groups:
            members = {item.key: item for item in group.items}
            selected = set(members) & set(chosen)
            shown = {item.key for item in group.items[:5]}
            if (not selected or not selected <= shown
                    or len(selected) >= len(group.items)
                    or len(group.items) - len(selected) != expected_remaining[group.group_id]
                    or any(members[key] != chosen[key] for key in selected)):
                raise ManagedDownloadError('quarantine group changed')
            matched.update(selected)
            keepers.append(next(item for item in group.items if item.key not in selected))
        if matched != set(chosen):
            raise ManagedDownloadError('quarantine selection rejected')
        return tuple(keepers)

    def _verified_item(self, item):
        self._check()
        current = self.owner._generations.current(item.work_id)
        if (current is None or current['relative_receipt'] != item.relative_receipt
                or current['sha256'] != item.receipt_sha256):
            raise ManagedDownloadError('quarantine snapshot changed')
        receipt, digest = self.owner._receipt(item.relative_receipt, verify_pages=False)
        page = item.page - 1
        if (digest != item.receipt_sha256 or receipt['work_id'] != item.work_id
                or receipt['author_id'] != item.author_id
                or type(item.page) is not int or not 0 <= page < receipt['page_count']):
            raise ManagedDownloadError('quarantine image identity changed')
        entry = receipt['files'][page]
        if ((entry['sha256'], entry['bytes'], entry['width'], entry['height'])
                != (item.image_sha256, item.file_size, item.width, item.height)):
            raise ManagedDownloadError('quarantine image changed')
        data = self.owner.read_saved_page(item.relative_receipt, page, digest)
        if _image(data)[1:] != (entry['format'], entry['width'], entry['height']):
            raise ManagedDownloadError('quarantine image changed')
        return data, entry

    def _copy_page(self, item, destination):
        """A failed attempt stays in incomplete/, never in the success set."""
        data, entry = self._verified_item(item)
        ext = {'png': 'png', 'jpeg': 'jpg', 'webp': 'webp'}[entry['format']]
        filename = f'{item.work_id}-p{item.page}.{ext}'
        path = destination / filename
        _write(path, data)
        copied = _read_bounded(path, MAX_PAGE_BYTES)
        if copied != data:
            raise ManagedDownloadError('quarantine copy verification failed')
        return {
            'work_id': item.work_id, 'page_no': item.page - 1,
            'relative_receipt': item.relative_receipt,
            'receipt_sha256': item.receipt_sha256,
            'sha256': entry['sha256'], 'bytes': entry['bytes'],
            'format': entry['format'], 'width': entry['width'],
            'height': entry['height'], 'file': filename,
        }

    def _relocate_verified_page(self, item, operation_id):
        """Atomically replace a published copy with its proven source inode.

        Requires an already committed v4 relocating intent: the original is
        excluded before its path may disappear. Both paths are in the same
        explicitly owned managed root and each is regular/unlinked elsewhere.
        """
        self._check()
        state = self.owner._generations.state_snapshot()
        operation = state['operations'].get(operation_id)
        if (operation is None or operation.get('mode') != 'move'
                or operation['state'] != 'relocating'
                or state['exclusions'].get(item.work_id, {}).get(str(item.page - 1)) != operation_id):
            raise ManagedDownloadError('relocation intent rejected')
        manifest = self._manifest(operation_id, operation)
        proof = next((row for row in manifest['items']
                      if row['work_id'] == item.work_id and row['page_no'] == item.page - 1), None)
        if (proof is None or proof['relative_receipt'] != item.relative_receipt
                or proof['receipt_sha256'] != item.receipt_sha256
                or proof['sha256'] != item.image_sha256):
            raise ManagedDownloadError('relocation proof rejected')
        receipt, digest = self.owner._receipt(item.relative_receipt, verify_pages=False)
        if digest != item.receipt_sha256 or receipt['work_id'] != item.work_id:
            raise ManagedDownloadError('relocation receipt changed')
        entry = receipt['files'][item.page - 1]
        source = self.owner.root / item.relative_receipt.rsplit('/', 1)[0] / entry['name']
        destination = self.operations / operation_id / proof['file']
        _safe_path(source, file=True)
        _safe_path(destination, file=True)
        source_io, destination_io = native_io_path(source), native_io_path(destination)
        if not source_io.exists():
            # A prior OS rename completed but the process crashed before the
            # v4 final state commit. Never rename an absent source again.
            self._verify_copies(manifest)
            return
        source_stat = source_io.lstat()
        destination_stat = destination_io.lstat()
        if (not source_io.is_file() or not destination_io.is_file()
                or source_stat.st_nlink != 1 or destination_stat.st_nlink != 1
                or source_stat.st_dev != destination_stat.st_dev):
            raise ManagedDownloadError('relocation link or volume rejected')
        data = _read_bounded(source, MAX_PAGE_BYTES)
        copied = _read_bounded(destination, MAX_PAGE_BYTES)
        if (data != copied or len(data) != proof['bytes']
                or hashlib.sha256(data).hexdigest() != proof['sha256']
                or _image(data)[1:] != (proof['format'], proof['width'], proof['height'])):
            raise ManagedDownloadError('relocation bytes changed')
        self._check()
        _safe_path(source, file=True)
        _safe_path(destination, file=True)
        os.replace(source_io, destination_io)
        _fsync_dir(source.parent)
        _fsync_dir(destination.parent)
        self._verify_copies(manifest)

    def _manifest(self, operation_id, operation):
        expected = f'quarantine/operations/{operation_id}/manifest.json'
        if operation['manifest'] != expected:
            raise ManagedDownloadError('quarantine manifest rejected')
        raw = _read_bounded(self.owner.root / expected, MAX_RECEIPT_BYTES)
        if hashlib.sha256(raw).hexdigest() != operation['manifest_sha256']:
            raise ManagedDownloadError('quarantine manifest changed')
        return json.loads(raw, object_pairs_hook=_json_unique)

    def _verify_copies(self, manifest):
        folder = native_io_path(self.operations / manifest['operation_id'])
        _safe_path(folder)
        names = {'manifest.json'}
        for item in manifest['items']:
            names.add(item['file'])
            data = _read_bounded(folder / item['file'], MAX_PAGE_BYTES)
            if (len(data) != item['bytes']
                    or hashlib.sha256(data).hexdigest() != item['sha256']
                    or _image(data)[1:] != (item['format'], item['width'], item['height'])):
                raise ManagedDownloadError('quarantine copy changed')
        if {path.name for path in folder.iterdir()} != names:
            raise ManagedDownloadError('quarantine contents changed')

    @staticmethod
    def _result(plan, operation_id, manifest, state, *, warning=False):
        success = {f"{item['work_id']}:{item['page_no'] + 1}"
                   for item in manifest['items']}
        failed = set(manifest['failed'])
        if (success & failed or success | failed != {item.key for item in plan.items}):
            raise ManagedDownloadError('quarantine operation selection changed')
        return QuarantineResult(
            operation_id,
            tuple(item for item in plan.items if item.key in success),
            tuple(item for item in plan.items if item.key in failed),
            state['revision'], warning,
        )

    @staticmethod
    def _cleanup_empty(stage):
        warning = False
        for folder in (stage / 'copies', stage):
            try:
                _safe_path(folder)
                if folder.exists() and not any(folder.iterdir()):
                    folder.rmdir()
            except OSError:
                warning = True
        return warning

    def execute(self, plan, *, operation_id, mode='copy', confirmed_move=False):
        if (type(mode) is not str or mode not in ('copy', 'move')
                or type(confirmed_move) is not bool
                or (mode == 'move' and not confirmed_move)):
            raise ManagedDownloadError('explicit relocation confirmation required')
        if type(operation_id) is not str or not _OPERATION.fullmatch(operation_id):
            raise ManagedDownloadError('quarantine operation rejected')
        keepers = self._validate_plan(plan)
        selection_sha = self.selection_digest(plan)
        with self._lock:
            self._check()
            state = self.owner._generations.state_snapshot()
            existing = state['operations'].get(operation_id)
            if existing is not None:
                if (existing['state'] != 'committed'
                        or existing['selection_sha256'] != selection_sha
                        or existing.get('mode', 'copy') != mode):
                    raise ManagedDownloadError('quarantine operation selection changed')
                manifest = self._manifest(operation_id, existing)
                self._verify_copies(manifest)
                return self._result(plan, operation_id, manifest, state)
            if state['revision'] != plan.state_revision:
                raise ManagedDownloadError('quarantine snapshot changed')
            self._prepare()
            final = native_io_path(self.operations / operation_id)
            _safe_path(final)
            if final.exists():
                raise ManagedDownloadError('quarantine operation requires recovery')
            stage = native_io_path(self.incomplete) / ('attempt-' + uuid4().hex)
            stage.mkdir()
            copies = stage / 'copies'
            ready = stage / 'ready'
            copies.mkdir()
            ready.mkdir()
            successful = []
            failed = []
            for item in plan.items:
                self._check()
                try:
                    successful.append((item, self._copy_page(item, copies)))
                except Exception:
                    failed.append(item)
            if not successful:
                return QuarantineResult(operation_id, (), tuple(failed),
                                        self.owner._generations.state_snapshot()['revision'])
            manifest = {
                'version': 1, 'operation_id': operation_id,
                'selection_sha256': selection_sha,
                'items': [proof for _, proof in successful],
                'failed': [item.key for item in failed],
            }
            raw = _packet(manifest)
            if len(raw) > MAX_RECEIPT_BYTES:
                raise ManagedDownloadError('quarantine manifest size exceeded')
            for _, proof in successful:
                source = copies / proof['file']
                target = ready / proof['file']
                _safe_path(source, file=True)
                if target.exists() or target.is_symlink():
                    raise ManagedDownloadError('quarantine output collision')
                source.rename(target)
            _write(ready / 'manifest.json', raw)
            _fsync_dir(ready)
            self.owner._generations.prepare_for_quarantine()
            work_ids = {item.work_id for group in plan.groups for item in group.items}
            warning = False
            with ExitStack() as locks:
                for wid in sorted(work_ids):
                    locks.enter_context(self.owner.work_lock(wid))
                self._check()
                if self.owner._generations.state_snapshot()['revision'] != plan.state_revision:
                    raise ManagedDownloadError('quarantine snapshot changed')
                for item in (*keepers, *(item for item, _ in successful)):
                    self._verified_item(item)
                if final.exists() or final.is_symlink():
                    raise ManagedDownloadError('quarantine output collision')
                ready.rename(final)
                _fsync_dir(self.operations)
                self._verify_copies(manifest)
                manifest_sha = hashlib.sha256(raw).hexdigest()
                try:
                    state = self.owner._generations.commit_quarantine(
                        operation_id=operation_id, expected_revision=plan.state_revision,
                        manifest_sha256=manifest_sha, selection_sha256=selection_sha,
                        selected_pages=[(item.work_id, item.page - 1)
                                        for item, _ in successful],
                        mode=mode,
                    )
                except Exception:
                    observed = self.owner._generations.state_snapshot()
                    committed = observed['operations'].get(operation_id)
                    if (committed is None or committed['state'] != 'committed'
                            or committed['manifest_sha256'] != manifest_sha
                            or committed['selection_sha256'] != selection_sha):
                        raise
                    state = observed
                    warning = True
            if mode == 'move':
                # The index is durable and excludes every selected page before
                # the first physical rename. An interrupted relocation remains
                # 'relocating' and can only be recovered or completed explicitly.
                for item, _ in successful:
                    self._relocate_verified_page(item, operation_id)
                state = self.owner._generations.transition_quarantine(
                    operation_id, expected_revision=state['revision'], state='moved')
            warning = self._cleanup_empty(stage) or warning
            return self._result(plan, operation_id, manifest, state, warning=warning)

    def restore(self, operation_id, *, expected_revision):
        """Recovery mechanism; callers must explicitly confirm restoration."""
        if type(operation_id) is not str or not _OPERATION.fullmatch(operation_id):
            raise ManagedDownloadError('quarantine operation rejected')
        with self._lock:
            self._check()
            state = self.owner._generations.state_snapshot()
            operation = state['operations'].get(operation_id)
            if (state['revision'] != expected_revision or operation is None
                    or operation['state'] not in ('committed', 'relocating', 'moved', 'restoring')):
                raise ManagedDownloadError('quarantine compare-and-swap rejected')
            manifest = self._manifest(operation_id, operation)
            moving = operation.get('mode') == 'move'
            with ExitStack() as locks:
                for wid in sorted({item['work_id'] for item in manifest['items']}):
                    locks.enter_context(self.owner.work_lock(wid))
                self._verify_copies(manifest)
                if moving and operation['state'] != 'restoring':
                    state = self.owner._generations.transition_quarantine(
                        operation_id, expected_revision=expected_revision, state='restoring')
                for item in manifest['items']:
                    data = (_read_bounded(self.operations / operation_id / item['file'],
                                          MAX_PAGE_BYTES) if moving else
                            self.owner._read_saved_page_proof(
                                item['relative_receipt'], item['page_no'], item['receipt_sha256']))
                    if (len(data) != item['bytes']
                            or hashlib.sha256(data).hexdigest() != item['sha256']):
                        raise ManagedDownloadError('quarantine recovery proof changed')
                    if moving:
                        receipt, digest = self.owner._receipt(item['relative_receipt'], verify_pages=False)
                        if (digest != item['receipt_sha256'] or receipt['work_id'] != item['work_id']
                                or any(receipt['files'][item['page_no']][k] != item[k]
                                       for k in ('sha256', 'bytes', 'format', 'width', 'height'))):
                            raise ManagedDownloadError('quarantine recovery receipt changed')
                        source = (self.owner.root / item['relative_receipt']).parent / receipt['files'][item['page_no']]['name']
                        _safe_path(source, file=True)
                        source_io = native_io_path(source)
                        if source_io.exists():
                            if _read_bounded(source, MAX_PAGE_BYTES) != data:
                                raise ManagedDownloadError('quarantine restoration collision')
                            continue
                        # Stage in the same managed volume and use Windows'
                        # no-overwrite rename. Never replace an unknown file.
                        stage = self.incomplete / ('restore-' + uuid4().hex)
                        _safe_path(stage, file=True)
                        _write(stage, data)
                        if _read_bounded(stage, MAX_PAGE_BYTES) != data or source_io.exists():
                            raise ManagedDownloadError('quarantine restoration collision')
                        _safe_path(source, file=True)
                        os.rename(native_io_path(stage), source_io)
                        _fsync_dir(source.parent)
                if moving:
                    # Restored files must match original receipt structure and
                    # content before the exclusion mask may be released.
                    for item in manifest['items']:
                        self.owner._receipt(item['relative_receipt'])
                self._check()
                return self.owner._generations.restore_quarantine(
                    operation_id, expected_revision=state['revision'] if moving else expected_revision)
