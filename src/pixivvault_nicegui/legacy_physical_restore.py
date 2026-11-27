"""Restore a page moved by the legacy physical quarantine.

The existing physical recovery entry point rolls a durable restore intent
forward. The displaced ZIP remains on disk and public names are never replaced.
"""
from copy import copy
from hashlib import sha256
import json
import os
from pathlib import PurePosixPath
import re
import shutil
import stat
from struct import unpack_from
from uuid import uuid4
from zipfile import ZipFile

from .fs_paths import native_io_path
from .legacy_physical_quarantine import (
    _copy, _digest, _legacy_input, _packet, _same_filesystem, _require_space, _SPACE_MARGIN,
)
from .legacy_quarantine import LIMIT
from .local_media_adapter import _safe_entry
from .managed_downloads import _fsync_dir, _json_unique, _read_bounded, _safe_path, _write


LEGACY_RESTORE_PREFIX = 'legacy:'
_ID = re.compile(r'[a-f0-9]{32}\Z')
_HASH = re.compile(r'[a-f0-9]{64}\Z')
_PAGE_LIMIT = 24 * 1024 * 1024


def _json(path, limit):
    return json.loads(_read_bounded(path, limit), object_pairs_hook=_json_unique)


def _checked_digest(path, *, legacy=False):
    if legacy:
        _legacy_input(path)
    else:
        _safe_path(path, file=True)
    return _digest(path)


def _new_zip_info(original):
    # A Zip64 field describes the source generation's sizes and offsets.
    # zipfile writes new fields for the output, including in local headers.
    extra = bytearray()
    offset = 0
    while offset < len(original.extra):
        if len(original.extra) - offset < 4:
            raise ValueError('legacy restore archive extra field rejected')
        tag, size = unpack_from('<HH', original.extra, offset)
        end = offset + 4 + size
        if end > len(original.extra):
            raise ValueError('legacy restore archive extra field rejected')
        if tag != 0x0001:
            extra.extend(original.extra[offset:end])
        offset = end
    info = copy(original)
    info.extra = bytes(extra)
    return info


def _restore_member(archive, target, info, data):
    """Keep all entries in this generation and add only the selected member."""
    with ZipFile(archive) as current, target.open('xb') as output:
        entries = current.infolist()
        names = {entry.filename for entry in entries}
        if len(entries) > 100000 or len(names) != len(entries) or info.filename in names:
            raise ValueError('legacy restore archive members conflict')
        expected = [(e.filename, e.file_size, e.CRC) for e in (*entries, info)]
        with ZipFile(output, 'w', allowZip64=True) as updated:
            updated.comment = current.comment
            for entry in entries:
                if (not _safe_entry(entry.filename) or entry.flag_bits & 1
                        or stat.S_IFMT(entry.external_attr >> 16) == stat.S_IFLNK):
                    raise ValueError('legacy restore archive member rejected')
                new_entry = _new_zip_info(entry)
                if entry.is_dir():
                    updated.writestr(new_entry, b'')
                else:
                    with current.open(entry) as source, updated.open(
                            new_entry, 'w', force_zip64=entry.file_size >= 2 * 1024**3) as destination:
                        shutil.copyfileobj(source, destination, 1024 * 1024)
            updated.writestr(_new_zip_info(info), data)
        output.flush()
        os.fsync(output.fileno())
    with ZipFile(target) as rebuilt:
        if ([(e.filename, e.file_size, e.CRC) for e in rebuilt.infolist()] != expected
                or rebuilt.testzip() is not None):
            raise ValueError('legacy restore archive readback failed')


class LegacyPhysicalRestore:
    def __init__(self, physical):
        self.physical = physical
        self.store = physical.store
        self.blocked_sources = frozenset()

    def require_settled(self):
        if self.blocked_sources:
            raise ValueError('legacy restore recovery required before another write')

    def _identity(self):
        root = native_io_path(self.physical.root)
        if _json(root / 'IDENTITY.json', 4096) != self.physical.identity:
            raise ValueError('legacy restore owner changed')

    def _move(self, unit, state, *, required):
        journal = native_io_path(self.physical.journals / unit)
        path = journal / 'intent.json'
        if not path.exists():
            if required:
                raise ValueError('legacy move record missing')
            return None
        intent, digest = self.physical._read_intent(path, verify_before=False)
        item = state['exclusions'].get(intent['key'])
        if (item is None or item.get('purged')
                or item.get('copy') != f'operations/{unit}/selected.image'):
            if required:
                raise ValueError('legacy move has no live recovery copy')
            return None
        if (state['operations'].get(unit) != [intent['key']]
                or item['store'] != intent['source'] or item['member'] != intent['member']
                or _json(journal / 'settled.json', 1024) != {'intent': digest, 'status': 'committed'}):
            raise ValueError('legacy move record changed')
        if intent['kind'] == 'zip':
            if (not isinstance(intent['member'], str) or not _safe_entry(intent['member'])
                    or intent['member'].endswith('/') or not _HASH.fullmatch(intent['after_sha256'])):
                raise ValueError('legacy archive move record rejected')
        elif (intent['member'] is not None or intent['after_sha256'] is not None
              or intent['filename'] != PurePosixPath(intent['source']).name):
            raise ValueError('legacy file move record rejected')
        return intent, item, digest

    def candidates(self):
        with self.store._lock:
            state = self.store.state(verify_outputs=False, recover=False)
            result = []
            for unit in state['operations']:
                if not _ID.fullmatch(unit):
                    continue
                try:
                    move = self._move(unit, state, required=False)
                    if move is None:
                        continue
                    intent, item, _ = move
                    if intent['source'] in self.blocked_sources:
                        continue
                    self._selected(item)
                    before = self.physical._unit(unit) / 'before'
                    _safe_path(before, file=True)
                    if not before.is_file():
                        continue
                except (OSError, KeyError, TypeError, ValueError, RuntimeError):
                    continue  # A damaged row must not hide the trash summary.
                result.append((LEGACY_RESTORE_PREFIX + unit, 'moved', 1, state['revision']))
            return tuple(result)

    def _selected(self, item):
        data = _read_bounded(self.store._path(item['copy']), _PAGE_LIMIT)
        if len(data) != item['size'] or sha256(data).hexdigest() != item['sha256']:
            raise ValueError('legacy restore selected image changed')
        return data

    def _locations(self, record, source):
        unit = self.physical._unit(record['revision'])
        workspace = source.parent / ('.pv-restore-' + record['revision']) if record['beside_source'] else unit
        _safe_path(workspace)
        if not workspace.is_dir():
            raise ValueError('legacy restore workspace missing')
        return workspace / 'ready', workspace / 'displaced'

    def _image_donor(self, intent):
        return self.physical._unit(intent['unit']) / intent['filename']

    def _stage_image(self, intent, ready, data):
        donor = self._image_donor(intent)
        if _checked_digest(donor, legacy=True) != intent['before_sha256']:
            raise ValueError('legacy restore original image changed')
        try:
            os.link(donor, ready)
        except OSError:
            # Copying an image with another name would split the bookmark
            # alias into two inodes and make the catalog entry ambiguous.
            _legacy_input(donor)
            if donor.stat().st_nlink != 1:
                raise ValueError('legacy restore cannot preserve image aliases') from None
            _write(ready, data)
            return False
        return True

    def _stage(self, unit, state):
        intent, item, move_digest = self._move(unit, state, required=True)
        before_path = self.physical._unit(unit) / 'before'
        data = self._selected(item)
        if (_checked_digest(before_path) != intent['before_sha256']
                or intent['source'] in self.physical.blocked_sources):
            raise ValueError('legacy restore evidence changed')
        source = self.physical._source(intent['source'])
        if not source.parent.is_dir():
            raise ValueError('legacy restore destination folder missing')
        before = None
        active = None
        if intent['kind'] == 'zip':
            entry = state['archives'].get(intent['source'])
            if entry is None:
                raise ValueError('legacy restore archive record missing')
            active = self.store.active_archive(self.store.catalog.root.joinpath(
                *PurePosixPath(intent['source']).parts), state)
            before = entry['source_sha256']
            if _checked_digest(source, legacy=True) != before:
                raise ValueError('legacy restore source changed')
        elif source.exists() or intent['before_sha256'] != item['sha256']:
            raise ValueError('legacy restore file collision or evidence changed')

        revision = uuid4().hex
        record = {'identity': self.physical.identity, 'move': unit, 'move_sha256': move_digest,
                  'source': intent['source'], 'key': intent['key'],
                  'revision': revision, 'expected_revision': state['revision'], 'before': before,
                  'beside_source': not _same_filesystem(self.physical.root, source.parent)}
        workspace = (source.parent / ('.pv-restore-' + revision) if record['beside_source']
                     else self.physical._unit(revision))
        _require_space(workspace.parent, (source.stat().st_size if before else len(data)) + _SPACE_MARGIN)
        workspace.mkdir()
        ready, _ = self._locations(record, source)
        stage = self.store._path(f'operations/{revision}/state.json').parent
        stage.mkdir()
        updated = json.loads(_packet(state))
        del updated['exclusions'][intent['key']]
        updated['operations'][revision] = [intent['key']]
        updated['revision'] = revision
        if before:
            member = intent['member']
            with ZipFile(before_path) as original:
                info = original.getinfo(member)
                with original.open(info) as selected:
                    if info.file_size != len(data) or selected.read(len(data) + 1) != data:
                        raise ValueError('legacy restore member evidence changed')
            if before == intent['after_sha256']:
                _copy(before_path, ready)
                if _checked_digest(ready) != intent['before_sha256']:
                    raise ValueError('legacy restore backup changed during staging')
            else:
                physical_archive = active if entry['sha256'] == before else source
                _restore_member(physical_archive, ready, info, data)
            # The virtual ZIP may hide additional copy-mode exclusions. Keep
            # those differences while adding the restored page to both views.
            if entry['sha256'] == before:
                _copy(ready, stage / 'active.zip')
            else:
                _restore_member(active, stage / 'active.zip', info, data)
            if (_checked_digest(source, legacy=True) != before
                    or _checked_digest(active) != entry['sha256']):
                raise ValueError('legacy restore ZIP changed during staging')
            record['after'] = _checked_digest(ready)
            updated['archives'][intent['source']] = {
                'path': f'operations/{revision}/active.zip', 'source_sha256': record['after'],
                'sha256': _checked_digest(stage / 'active.zip'),
            }
        else:
            record['ready_linked'] = self._stage_image(intent, ready, data)
            record['after'] = item['sha256']
        info = ready.stat()
        record['ready_identity'] = [info.st_dev, info.st_ino]
        record['ready_size'] = info.st_size
        _fsync_dir(workspace)
        _fsync_dir(workspace.parent)
        raw = _packet(updated)
        if len(raw) > LIMIT:
            raise ValueError('legacy restore state limit')
        _write(stage / 'state.json', raw)
        _fsync_dir(stage)
        _fsync_dir(stage.parent)
        record['state_sha256'] = sha256(raw).hexdigest()
        journal = native_io_path(self.physical.journals / revision)
        journal.mkdir()
        path = journal / 'restore.json'
        _write(journal / 'restore.next', _packet(record))
        os.rename(journal / 'restore.next', path)
        _fsync_dir(journal)
        _fsync_dir(journal.parent)
        return path, record

    def _read(self, path):
        raw = _read_bounded(path, 16384)
        record = json.loads(raw, object_pairs_hook=_json_unique)
        revision = path.parent.name
        if (record['identity'] != self.physical.identity or record['revision'] != revision
                or not _ID.fullmatch(revision) or not _ID.fullmatch(record['move'])
                or not _safe_entry(record['source']) or type(record['beside_source']) is not bool):
            raise ValueError('legacy restore intent rejected')
        if ('ready_size' in record and (type(record['ready_size']) is not int or record['ready_size'] <= 0)
                or type(record.get('ready_linked', False)) is not bool):
            raise ValueError('legacy restore staged file record rejected')
        return record, sha256(raw).hexdigest()

    def _finish(self, path, record, digest, state=None):
        revision = record['revision']
        marker = path.parent / 'restored.json'
        if marker.exists():
            seal = _json(marker, 1024)
            if seal.get('intent') != digest or seal.get('status') not in ('committed', 'cancelled'):
                raise ValueError('legacy restore settlement changed')
            return seal['status']
        if state is None:
            state = self.store.state(verify_outputs=False, recover=False)
        status = 'committed'
        if revision in state['operations']:
            if state['operations'][revision] != [record['key']]:
                raise ValueError('legacy restore publication changed')
        else:
            if state['revision'] != record['expected_revision']:
                raise ValueError('legacy restore revision changed')
            self._identity()
            intent, item, move_digest = self._move(record['move'], state, required=True)
            if (move_digest != record['move_sha256']
                    or intent['source'] != record['source'] or intent['key'] != record['key']
                    or (record['before'] is None) != (intent['kind'] == 'file')
                    or intent['source'] in self.physical.blocked_sources
                    or not _HASH.fullmatch(record['after'])):
                raise ValueError('legacy restore binding changed')
            self._selected(item)
            before_path = self.physical._unit(record['move']) / 'before'
            if _checked_digest(before_path) != intent['before_sha256']:
                raise ValueError('legacy restore backup changed')
            source = self.physical._source(intent['source'])
            ready, displaced = self._locations(record, source)
            state_path = self.store._path(f'operations/{revision}/state.json')
            next_state = _read_bounded(state_path, LIMIT)
            if sha256(next_state).hexdigest() != record['state_sha256']:
                raise ValueError('legacy restore staged state changed')
            donor = None
            if record['before'] is not None:
                relative = intent['source']
                previous_archive = state['archives'][relative]
                archive = json.loads(next_state)['archives'][relative]
                if (previous_archive['source_sha256'] != record['before']
                        or archive['source_sha256'] != record['after']
                        or archive['path'] != f'operations/{revision}/active.zip'
                        or _checked_digest(self.store._path(archive['path'])) != archive['sha256']):
                    raise ValueError('legacy restore staged ZIP changed')
            else:
                if record['after'] != item['sha256']:
                    raise ValueError('legacy restore image proof changed')
                retained = self._image_donor(intent)
                _legacy_input(retained)
                if record.get('ready_linked', False):
                    donor = retained
                elif retained.stat().st_nlink != 1:
                    # Old intents may contain a standalone copy of a linked
                    # image. Keep a placed copy until it is moved out of the way.
                    if ready.exists() or not source.exists():
                        status = 'cancelled'
                    elif not os.path.samefile(source, retained):
                        raise ValueError('legacy restore cannot preserve image aliases')
            if status == 'committed':
                ref = _packet({'path': f'operations/{revision}/state.json', 'sha256': record['state_sha256']})
                next_ref = state_path.parent / ('restore-' + uuid4().hex + '.next')
                _write(next_ref, ref)
                _fsync_dir(next_ref.parent)
                if self._install(source, ready, displaced, record, intent['key']):
                    self.store._check()
                    self._check_ready(source, record, installed=True)
                    if donor is not None and not os.path.samefile(source, donor):
                        raise ValueError('legacy restore installed image lost its hardlink')
                    os.replace(next_ref, native_io_path(self.store.root / 'CURRENT'))
                    _fsync_dir(native_io_path(self.store.root))
                    self.store._checkpoint('restore-published:' + intent['key'])
                else:
                    status = 'cancelled'  # The collision occurred before any source change.
        if status == 'committed':
            self.store.catalog.replace_snapshot(self.store.original_snapshot)
        seal = {'intent': digest, 'status': status}
        temporary = path.parent / ('restored-' + uuid4().hex + '.next')
        _write(temporary, _packet(seal))
        os.rename(temporary, marker)
        _fsync_dir(marker.parent)
        return status

    def _check_ready(self, path, record, *, installed=False):
        actual = _checked_digest(path, legacy=installed or record.get('ready_linked', False))
        info = path.stat()
        device, inode = record['ready_identity']
        # FAT filesystems can change the ID during a move across directories.
        # Compare it while the file is staged; use volume and payload after rename.
        if (actual != record['after'] or info.st_dev != device
                or info.st_size != record.get('ready_size', info.st_size)
                or (not installed and info.st_ino != inode)):
            raise ValueError('legacy restore source or staged data changed')

    def _install(self, source, ready, displaced, record, key):
        if os.name != 'nt':
            raise ValueError('legacy restore requires Windows no-overwrite rename')
        is_zip = record['before'] is not None
        if ready.exists():
            self._check_ready(ready, record)
            if not is_zip and not source.parent.is_dir():
                return False
            if is_zip and not displaced.exists():
                if not source.exists() or _checked_digest(source, legacy=True) != record['before']:
                    return False
                _legacy_input(displaced, missing=True)
                # Preserve the exact file removed from the public name, even
                # if a non-cooperating writer raced the preceding hash check.
                os.rename(source, displaced)
                _fsync_dir(source.parent)
                _fsync_dir(displaced.parent)
                self.store._checkpoint('restore-displaced:' + key)
            if is_zip:
                if _checked_digest(displaced, legacy=True) != record['before']:
                    raise ValueError('legacy restore displaced ZIP changed')
                self._check_ready(ready, record)
            _legacy_input(source, missing=True)
            # Windows rename fails if another file appears at the destination.
            try:
                os.rename(ready, source)
            except FileExistsError:
                if not is_zip:
                    return False
                raise
            except FileNotFoundError:
                if not is_zip and ready.is_file() and not source.parent.is_dir():
                    return False
                raise
            _fsync_dir(ready.parent)
            _fsync_dir(source.parent)
            self.store._checkpoint('restore-installed:' + key)
        elif is_zip:
            if _checked_digest(displaced, legacy=True) != record['before']:
                raise ValueError('legacy restore displaced ZIP changed')
        return True

    def recover(self):
        blocked = set()
        for path in sorted(native_io_path(self.physical.journals).glob('*/restore.json')):
            record, digest = self._read(path)
            try:
                self._finish(path, record, digest)
            except (OSError, ValueError, RuntimeError, KeyError, TypeError):
                blocked.add(record['source'])
        self.blocked_sources = frozenset(blocked)
        return self.blocked_sources

    def restore(self, unit, expected_revision):
        if os.name != 'nt' or not _ID.fullmatch(unit):
            raise ValueError('legacy restore requires a valid Windows operation')
        with self.store._lock:
            state = self.store._mutation_state(verify_outputs=False)
            if expected_revision != state['revision']:
                raise ValueError('legacy restore revision changed')
            self._identity()
            path, staged = self._stage(unit, state)
            try:
                record, digest = self._read(path)
                self.store._checkpoint('restore-intent:' + record['key'])
                status = self._finish(path, record, digest, state)
            except BaseException:
                # The intent is durable. Read-only callers must observe the
                # unresolved source before any later recovery has a chance to run.
                self.blocked_sources |= {staged['source']}
                self.physical.blocked_sources |= self.blocked_sources
                raise
            if status != 'committed':
                raise ValueError('legacy restore destination changed')
            return self.store.state(verify_outputs=False, recover=False)
