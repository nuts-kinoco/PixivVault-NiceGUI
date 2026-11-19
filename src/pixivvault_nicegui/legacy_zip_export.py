"""Non-destructive, explicit-root export of verified managed images as legacy ZIPs.

Never imports or opens the Flet Database. Every archive page originates from
the active, SHA-verified managed receipt, and a staged ZIP is published only
through an atomic no-overwrite hard link. Original PixivVault ZIPs, images,
Cookie and the NiceGUI managed store remain untouched.
"""

from __future__ import annotations

import hashlib
import copy
from contextlib import ExitStack
from collections import defaultdict
import json
import os
from pathlib import Path
import re
import shutil
import stat
from threading import Lock
from uuid import uuid4
from zipfile import BadZipFile, ZipFile, ZIP_DEFLATED, LargeZipFile, ZIP64_LIMIT, ZIP_FILECOUNT_LIMIT
from zlib import error as ZlibError

from .fs_paths import native_io_path, logical_path
from .job_failure import JobFailure
from .managed_downloads import (ManagedPixivDownloads, ManagedDownloadError,
                                _safe_path, _read_bounded, _write, _fsync_dir,
                                _json_unique)


EXPORT_MARKER = '.pixivvault-nicegui-legacy-zip-export.json'
EXPORT_IDENTITY = {'application': 'pixivvault-nicegui', 'purpose': 'legacy-zip-export',
                   'version': 1}
_ID = re.compile(r'[1-9][0-9]{0,19}\Z')
_PAGE_SUFFIX = re.compile(r'^(.*)_(\d+)$')
_INVALID_FILENAME = re.compile(r'[\\/:*?"<>|]+')
_STAGED_ZIP = re.compile(r'zip-[0-9a-f]{32}\.tmp(?:\.publish)?\Z')
MAX_EXPORT_WORKS = 3000
MAX_EXPORT_PAGES = 5000
MAX_EXPORT_INPUT_BYTES = 1700 * 1024 * 1024
MAX_ZIP_NAME_BYTES = 240
ZIP64_THRESHOLD = 4000 * 1024 * 1024
# CPython uses a 2 GiB ZIP32 guard; switch early to avoid partial writes.
# ZIP64 is selected consistently for all members and remains ZIP-readable.
ZIP32_SAFE_LIMIT = int(ZIP64_LIMIT / 1.05) - 1024 * 1024
ZIP32_ENTRY_LIMIT = ZIP_FILECOUNT_LIMIT



def _without_zip64_extra(extra):
    clean = bytearray()
    pos = 0
    while pos < len(extra):
        if pos + 4 > len(extra):
            raise LegacyZipExportError('invalid ZIP extra field')
        kind = int.from_bytes(extra[pos:pos+2], 'little')
        size = int.from_bytes(extra[pos+2:pos+4], 'little')
        end = pos + 4 + size
        if end > len(extra):
            raise LegacyZipExportError('invalid ZIP extra field')
        if kind != 1:
            clean.extend(extra[pos:end])
        pos = end
    return bytes(clean)

def verified_zip_digest(path):
    """Stream an existing regular owned ZIP without buffering the archive."""
    _safe_path(path,file=True)
    expected=path.lstat();hasher=hashlib.sha256()
    with path.open('rb') as stream:
        opened=os.fstat(stream.fileno())
        if not os.path.samestat(expected,opened):raise ValueError('ZIP changed during open')
        for block in iter(lambda:stream.read(1024*1024),b''):hasher.update(block)
        after=os.fstat(stream.fileno())
    current=path.lstat()
    if (not os.path.samestat(expected,current) or after.st_size!=expected.st_size
            or after.st_mtime_ns!=expected.st_mtime_ns or current.st_mtime_ns!=after.st_mtime_ns):
        raise ValueError('ZIP changed during hashing')
    return hasher.hexdigest()


class LegacyZipExportError(ValueError):
    """Fixed public error strings: no original titles, IDs or paths."""


class LegacyZipAmbiguousError(JobFailure, LegacyZipExportError):
    def __init__(self):
        super().__init__('ambiguous legacy ZIP member', reason_code='zip_member_ambiguous')


def legacy_filename(title: str, work_id: str, page_no: int, page_count: int,
                    ext: str) -> str:
    """Pure parity of legacy core.py sanitize_filename + work_id_arcname.

    The original migrator inserts work ID *before* a final `_N` suffix.
    Titles themselves ending in `_N` are intentionally handled identically.
    """
    if (type(title) is not str or type(work_id) is not str or not _ID.fullmatch(work_id)
            or type(page_no) is not int or type(page_count) is not int
            or not 0 <= page_no < page_count <= 1000 or ext not in ('png', 'jpg', 'webp')):
        raise LegacyZipExportError('legacy filename source rejected')
    safe_title = _INVALID_FILENAME.sub('_', title)
    legacy = (f'{safe_title}.{ext}' if page_count == 1
              else f'{safe_title}_{page_no + 1}.{ext}')
    return _work_id_arcname(legacy, work_id)


def _work_id_arcname(arcname, work_id):
    if not work_id or '/' in arcname or f'({work_id})' in arcname:
        return arcname
    stem, ext = os.path.splitext(arcname)
    match = _PAGE_SUFFIX.match(stem)
    return (f'{match.group(1)}({work_id})_{match.group(2)}{ext}'
            if match else f'{stem}({work_id}){ext}')


def zip_arcname_candidates(arcname: str, safe_title: str, work_id: str) -> list[str]:
    """Legacy lookup order, including the title ending in a page-like suffix."""
    if '/' in arcname:
        return [arcname]
    stem, ext = os.path.splitext(arcname)
    candidates = []
    if stem == safe_title:
        candidates.append(f'{safe_title}({work_id}){ext}')
        structural = _work_id_arcname(arcname, work_id)
        if structural not in candidates:
            candidates.append(structural)
    else:
        prefix = f'{safe_title}_'
        page = stem[len(prefix):] if stem.startswith(prefix) else ''
        if page and page.isdigit():
            candidates.append(f'{safe_title}({work_id})_{page}{ext}')
    candidates.append(arcname)
    return candidates

def _safe_page_candidates(canonical, candidates, work_id):
    # A weak old name containing a different work ID cannot identify this work.
    return [candidate for candidate in candidates if candidate == canonical
            or not any(marker != work_id for marker in re.findall(r'\(([1-9][0-9]*)\)', candidate))]


def _page_names(receipt, index):
    work_id = receipt['work_id']
    title = _INVALID_FILENAME.sub('_', receipt['title'])
    ext = {'png': 'png', 'jpeg': 'jpg', 'webp': 'webp'}[receipt['files'][index]['format']]
    legacy = (f'{title}.{ext}' if receipt['page_count'] == 1
              else f'{title}_{index + 1}.{ext}')
    canonical = _work_id_arcname(legacy, work_id)
    return canonical, zip_arcname_candidates(legacy, title, work_id)


def author_zip_candidates(root, author_id):
    """Find an author's flat ZIP paths by their trailing author ID."""
    suffix = f'({author_id}).zip'
    return [path for path in native_io_path(root).iterdir() if path.name.endswith(suffix)]


def _zip_path_size(path):
    try:
        return path.lstat().st_size
    except OSError:
        return 0


def largest_author_zip(paths):
    return max(paths, key=_zip_path_size)

def _export_path(root: Path) -> Path:
    if not isinstance(root, Path) or not root.is_absolute() or root == root.parent:
        raise LegacyZipExportError('dedicated absolute legacy export root required')
    _safe_path(native_io_path(root))
    return logical_path(native_io_path(root).resolve())


class LegacyZipExporter:
    def __init__(self, root: Path, managed: ManagedPixivDownloads, *, preserve_incomplete=False, notify_corrupt=None):
        self.notify_corrupt = notify_corrupt
        self.mutation_guard = None
        if not isinstance(managed, ManagedPixivDownloads) or managed.closed:
            raise LegacyZipExportError('managed originals unavailable')
        path = _export_path(logical_path(root))
        store = logical_path(native_io_path(managed.root).resolve())
        if path == store or path in store.parents or store in path.parents:
            raise LegacyZipExportError('ZIP export root overlaps managed originals')
        self.root = path
        self.managed = managed
        self._writer = Lock()
        self.closed = False
        self._closing = False
        root_io = native_io_path(self.root)
        marker = root_io / EXPORT_MARKER
        if root_io.exists():
            # A preexisting empty directory is NOT proof of ownership. Require
            # an explicit fresh destination on first use, or our exact marker.
            if not marker.is_file():
                raise LegacyZipExportError('ZIP export directory is not owned by this app')
            try:
                packet = json.loads(_read_bounded(marker, 1024),
                                    object_pairs_hook=_json_unique)
                if packet != EXPORT_IDENTITY:
                    raise ValueError()
            except Exception:
                raise LegacyZipExportError('ZIP export marker rejected') from None
        else:
            _safe_path(root_io.parent)
            root_io.mkdir()
            _safe_path(native_io_path(self.root))
            _write(marker, json.dumps(EXPORT_IDENTITY, sort_keys=True).encode('ascii'))
        self.incomplete = self.root / '.incomplete'
        incomplete_io = native_io_path(self.incomplete)
        _safe_path(incomplete_io)
        incomplete_io.mkdir(exist_ok=True)
        _safe_path(incomplete_io)
        # Recover a crash just before/after publishing an exclusive hardlink.
        # The incomplete name is app-owned; never touch an unknown file or a
        # symlink/reparse point. In the post-link crash case st_nlink == 2;
        # unlinking *only* this temporary name leaves the published ZIP intact
        # and restores its normal single-link file identity.
        for leftover in incomplete_io.iterdir():
            info = leftover.lstat()
            if (not _STAGED_ZIP.fullmatch(leftover.name)
                    or not stat.S_ISREG(info.st_mode) or leftover.is_symlink()
                    or getattr(info, 'st_file_attributes', 0) & 0x400
                    or info.st_nlink not in (1, 2)):
                raise LegacyZipExportError('unknown or unsafe incomplete ZIP file')
            if not preserve_incomplete:leftover.unlink()

    def close(self) -> None:
        # Application shutdown must not close managed image readers halfway
        # through ZIP production. The export is owned by the app, not a tab.
        self._closing = True
        with self._writer:
            self.closed = True

    def _verify_archive(self, path, members, label):
        try:
            with ZipFile(path, 'r') as archive:
                if set(archive.namelist()) != members or archive.testzip() is not None:
                    raise LegacyZipExportError('legacy ZIP verification failed')
        except (LegacyZipExportError, BadZipFile, OSError, RuntimeError, ZlibError) as error:
            if self.notify_corrupt is not None:
                try:
                    self.notify_corrupt('PixivVault - ZIP破損検出',
                                        f'{label} のアーカイブに問題があります: ZIPの検証に失敗しました')
                except Exception:
                    pass  # Notification failure does not hide verification failure.
            raise LegacyZipExportError('legacy ZIP verification failed') from error

    def normalize_archive(self, path: Path, expected_sha256: str, *,
                          zip64_threshold: int = 4000 * 1024 * 1024) -> None:
        """Rewrite an owned ZIP with normalized paths and one ZIP64 policy."""
        path = native_io_path(logical_path(path))
        if path.parent != native_io_path(self.root) or not path.name.endswith('.zip'):
            raise LegacyZipExportError('ZIP path rejected')
        if (not isinstance(expected_sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', expected_sha256)
                or type(zip64_threshold) is not int or zip64_threshold < 1):
            raise LegacyZipExportError('ZIP normalization input rejected')
        if not self._writer.acquire(blocking=False):
            raise LegacyZipExportError('ZIP writer already active')
        staging = None
        try:
            if self.closed or self._closing or verified_zip_digest(path) != expected_sha256:
                raise LegacyZipExportError('previous ZIP changed')
            with ZipFile(path, 'r') as inspect:
                infos = inspect.infolist()
                expanded = sum(info.file_size + len(info.filename.encode('utf-8'))
                               + len(info.extra) + len(info.comment) + 256 for info in infos)
            use_zip64 = (path.stat().st_size >= zip64_threshold
                         or expanded >= ZIP32_SAFE_LIMIT or len(infos) >= ZIP32_ENTRY_LIMIT)
            _safe_path(native_io_path(self.incomplete))
            staging = native_io_path(self.incomplete) / ('zip-' + uuid4().hex + '.tmp')
            names = set()
            with ZipFile(path, 'r') as source, staging.open('xb') as output:
                with ZipFile(output, 'w', allowZip64=use_zip64) as target:
                    target.comment = source.comment
                    for original in source.infolist():
                        info = copy.copy(original)
                        info.filename = info.filename.replace('\\', '/')
                        if info.filename in names:
                            raise LegacyZipExportError('duplicate previous ZIP member')
                        names.add(info.filename)
                        info.extra = _without_zip64_extra(info.extra)
                        if not use_zip64 and info.compress_type in (0, 8):
                            info.extract_version = min(info.extract_version, 20)
                            info.create_version = min(info.create_version, 20)
                        with source.open(original) as src, target.open(info, 'w', force_zip64=use_zip64) as dst:
                            shutil.copyfileobj(src, dst, 1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
            self._verify_archive(staging, names, 'ZIP')
            if self._closing or verified_zip_digest(path) != expected_sha256:
                raise LegacyZipExportError('previous ZIP changed')
            finish = self.mutation_guard(staging, path, expected_sha256) if self.mutation_guard else None
            try:
                os.replace(staging, path)
                _fsync_dir(path.parent)
            finally:
                if finish is not None:
                    finish()
        except (OSError, BadZipFile, RuntimeError, ZlibError, LargeZipFile) as error:
            raise LegacyZipExportError('ZIP normalization failed') from error
        finally:
            try:
                if staging is not None:
                    staging.unlink(missing_ok=True)
            finally:
                self._writer.release()

    def remove_entry(self, path: Path, entry_name: str, expected_sha256: str) -> bytes:
        """Remove one entry from an owned archive after verifying every member."""
        path = native_io_path(logical_path(path))
        if path.parent != native_io_path(self.root) or not path.name.endswith('.zip'):
            raise LegacyZipExportError('ZIP path rejected')
        if not isinstance(entry_name, str) or not entry_name:
            raise LegacyZipExportError('ZIP member rejected')
        if not isinstance(expected_sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', expected_sha256):
            raise LegacyZipExportError('invalid previous ZIP proof')
        if not self._writer.acquire(blocking=False):
            raise LegacyZipExportError('ZIP writer already active')
        staging = None
        try:
            if self.closed or self._closing:
                raise LegacyZipExportError('ZIP exporter closed')
            if verified_zip_digest(path) != expected_sha256:
                raise LegacyZipExportError('previous ZIP changed')
            _safe_path(native_io_path(self.incomplete))
            staging = native_io_path(self.incomplete) / ('zip-' + uuid4().hex + '.tmp')
            removed = None
            names = set()
            with ZipFile(path, 'r') as source, staging.open('xb') as output:
                with ZipFile(output, 'w', allowZip64=True) as target:
                    target.comment = source.comment
                    for info in source.infolist():
                        if info.filename in names:
                            raise LegacyZipExportError('duplicate previous ZIP member')
                        names.add(info.filename)
                        if info.filename == entry_name:
                            removed = source.read(info)
                        else:
                            with source.open(info) as src, target.open(info, 'w') as dst:
                                shutil.copyfileobj(src, dst, 1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
            if removed is None:
                raise LegacyZipExportError('ZIP member not found')
            names.remove(entry_name)
            self._verify_archive(staging, names, 'ZIP')
            if self._closing or verified_zip_digest(path) != expected_sha256:
                raise LegacyZipExportError('previous ZIP changed')
            finish = (self.mutation_guard(staging, path, expected_sha256, contents_changed=True)
                      if self.mutation_guard else None)
            try:
                os.replace(staging, path)
                _fsync_dir(path.parent)
            finally:
                if finish is not None:
                    finish()
            return removed
        except (OSError, BadZipFile, RuntimeError, ZlibError, LargeZipFile) as error:
            raise LegacyZipExportError('ZIP removal failed') from error
        finally:
            try:
                if staging is not None:
                    staging.unlink(missing_ok=True)
            finally:
                self._writer.release()

    def _prior_member_proofs(self, author_id, author_rows, previous_source):
        """Resolve immutable receipts from the last published ZIP's source."""
        wanted = dict(previous_source['proofs'])
        rows = {}
        for _, receipt, proof in author_rows:
            wid = receipt['work_id']
            if wanted.get(wid) == proof:
                rows[wid] = receipt
                del wanted[wid]
        if wanted:
            for root in ('outputs', 'generations'):
                folder = native_io_path(self.managed.root / root)
                _safe_path(folder)
                for path in folder.iterdir():
                    match = re.fullmatch(r'work-([1-9][0-9]{0,19})-[0-9a-f]{32}', path.name)
                    if match is None or match[1] not in wanted:
                        continue
                    relative = f'{root}/{path.name}/receipt.json'
                    try:
                        receipt, proof = self.managed._receipt(relative, verify_pages=False)
                    except ManagedDownloadError:
                        continue  # Missing prior proof leaves members untouched.
                    if receipt['author_id'] == author_id and wanted[match[1]] == proof:
                        rows[match[1]] = receipt
                        del wanted[match[1]]
                        if not wanted:
                            break
                if not wanted:
                    break
        owners = defaultdict(set)
        filters = previous_source.get('page_filters', {})
        for wid, receipt in rows.items():
            for index in filters.get(wid, range(receipt['page_count'])):
                name, _ = _page_names(receipt, index)
                owners[name, receipt['files'][index]['sha256']].add((wid, index))
        return owners

    def _zip_member_plan(self, author_id, author_rows, source_pages, old_hashes, previous_source):
        owners = (self._prior_member_proofs(author_id, author_rows, previous_source)
                  if previous_source is not None else {})
        old_owners = {name: owners.get((name, proof), set()) for name, proof in old_hashes.items()}
        verified_work = {name: next(iter(pages))[0] for name, pages in old_owners.items() if len(pages) == 1}
        canonical = {}
        weak = defaultdict(set)
        pages = []
        for relative, receipt, digest in author_rows:
            wid = receipt['work_id']
            for index, file in enumerate(receipt['files']):
                name, candidates = _page_names(receipt, index)
                active = index in source_pages[wid]
                page = wid, index
                if name in canonical:
                    raise LegacyZipAmbiguousError()
                canonical[name] = page
                candidates = _safe_page_candidates(name, candidates, wid)
                for alias in {candidate for candidate in candidates if candidate != name}:
                    weak[alias].add(page)
                pages.append((page, relative, digest, file, name, candidates, active))

        drop = set()
        write = []
        existing = set(old_hashes)
        for page, relative, digest, file, name, candidates, active in pages:
            wid, index = page
            usable_slot = (name in old_hashes
                           and set(re.findall(r'\(([1-9][0-9]*)\)', name)) == {wid}
                           and (old_owners[name] == {page} or previous_source is None))
            if not active:
                if usable_slot:
                    drop.add(name)
                continue
            if name in old_hashes:
                # Flet skips exact names. A managed generation may replace the
                # exact slot only when the prior publication proves its owner.
                if usable_slot and old_hashes[name] != file['sha256']:
                    drop.add(name)
                    write.append((relative, digest, index, name, file['bytes']))
                continue

            # Flet append_to_zip checks both directions with the ID from the
            # incoming filename map. Receipt and payload proof can disambiguate
            # an old name that actually belongs to another work.
            same_work = _work_id_arcname(name, wid)
            legacy = name.replace(f'({wid})', '', 1)
            aliases = {same_work, legacy, *candidates} - {name}
            if previous_source is None:
                aliases = set(_safe_page_candidates(name, aliases, wid))
            matched = {alias for alias in aliases & existing
                       if verified_work.get(alias, wid) == wid
                       and (alias not in canonical or canonical[alias] == page)}
            if matched:
                if previous_source is None and any(len(weak[alias]) > 1 for alias in matched):
                    raise LegacyZipAmbiguousError()
                continue
            write.append((relative, digest, index, name, file['bytes']))
            existing.add(name)
        return drop, write

    def export_author(self, author_id: str, *, expected_proofs=None, before_publish=None,
                      expected_source_pages=None, replace_existing_sha256=None,
                      previous_source=None) -> Path:
        """Export all active still-image works of one author as a new ZIP.

        Does not overwrite, append to, rename, delete or hardlink any original
        managed image. This function is blocking; callers use asyncio.to_thread.
        """
        if type(author_id) is not str or _ID.fullmatch(author_id) is None:
            raise LegacyZipExportError('ZIP author ID rejected')
        if (replace_existing_sha256 is not None and
                (type(replace_existing_sha256) is not str or re.fullmatch(r'[a-f0-9]{64}',replace_existing_sha256) is None)):
            raise LegacyZipExportError('invalid previous ZIP proof')
        if not self._writer.acquire(blocking=False):
            raise LegacyZipExportError('ZIP writer already active')
        staging = None
        retain_stage = False
        try:
            if self.closed or self._closing or self.managed.closed:
                raise LegacyZipExportError('managed originals unavailable')
            snapshot = self.managed.catalog_snapshot()
            author_rows = [(relative, receipt, digest)
                           for relative, receipt, digest in snapshot.receipts
                           if receipt['author_id'] == author_id]
            source_pages = {receipt['work_id']: snapshot.source_pages[receipt['work_id']]
                            for _, receipt, _ in author_rows}
            source_tokens = {wid: snapshot.work_tokens[wid] for wid in source_pages}
            receipts = [(relative, receipt, digest) for relative, receipt, digest in author_rows
                        if source_pages[receipt['work_id']]]
            if (expected_source_pages is not None
                    and (type(expected_source_pages) is not dict
                         or {wid: tuple(pages) for wid, pages in expected_source_pages.items()}
                            != source_pages)):
                raise LegacyZipExportError('managed page selection changed before ZIP export')
            if self._closing:
                raise LegacyZipExportError('ZIP export interrupted by shutdown')
            if not receipts or len(receipts) > MAX_EXPORT_WORKS:
                raise LegacyZipExportError('author ZIP has no supported work or exceeds limit')
            receipts.sort(key=lambda item: item[1]['work_id'])
            if expected_proofs is not None and [[row['work_id'], proof] for _,row,proof in receipts] != expected_proofs:
                raise LegacyZipExportError('managed library changed before ZIP export')
            pages = sum(len(source_pages[receipt['work_id']]) for _, receipt, _ in receipts)
            total_bytes = sum(receipt['files'][page]['bytes'] for _, receipt, _ in receipts
                              for page in source_pages[receipt['work_id']])
            if (pages > MAX_EXPORT_PAGES or not 0 < total_bytes <= MAX_EXPORT_INPUT_BYTES):
                raise LegacyZipExportError('author ZIP exceeds safe size or page limit')
            # Original Flet uses the most recently observed author name. An
            # older receipt can keep the author's pre-rename display name.
            latest = max(receipts, key=lambda item: (item[1]['saved_at'], item[1]['work_id']))
            author_name = _INVALID_FILENAME.sub('_', latest[1]['author_name']) or author_id
            zip_name = f'{author_name}({author_id}).zip'
            if len(zip_name.encode('utf-8')) > MAX_ZIP_NAME_BYTES:
                raise LegacyZipExportError('author ZIP filename too long')
            destination = native_io_path(self.root / zip_name)
            _safe_path(native_io_path(self.root))
            _safe_path(destination, file=True)
            existing_archives = author_zip_candidates(self.root, author_id)
            if replace_existing_sha256 is None:
                if existing_archives or destination.exists():
                    raise LegacyZipExportError('legacy author ZIP already exported')
            else:
                if not existing_archives:
                    raise LegacyZipExportError('previous ZIP missing')
                previous_path = largest_author_zip(existing_archives)
                if verified_zip_digest(previous_path) != replace_existing_sha256:
                    raise LegacyZipExportError('previous ZIP changed')
                # Validate all bytes before changing even the archive name.
                with ZipFile(previous_path, 'r') as previous:
                    if previous.testzip() is not None:
                        raise LegacyZipExportError('legacy ZIP verification failed')
                destination = previous_path

            previous_infos = {}
            old_hashes = {}
            if replace_existing_sha256 is not None:
                with ZipFile(destination, 'r') as previous:
                    for info in previous.infolist():
                        name = info.filename.replace('\\', '/')
                        if name in previous_infos:
                            raise LegacyZipExportError('duplicate previous ZIP member')
                        previous_infos[name] = info
                        hasher = hashlib.sha256()
                        with previous.open(info) as source:
                            for block in iter(lambda: source.read(1024 * 1024), b''):
                                hasher.update(block)
                        old_hashes[name] = hasher.hexdigest()
            drop, entries = self._zip_member_plan(
                author_id, author_rows, source_pages, old_hashes, previous_source)
            old_names = previous_infos.keys() - drop
            members = old_names | {entry[3] for entry in entries}
            estimate = sum(previous_infos[name].file_size + 2 * len(name.encode('utf-8'))
                           + 2 * len(previous_infos[name].extra) + len(previous_infos[name].comment) + 256
                           for name in old_names)
            estimate += sum(size + 2 * len(name.encode('utf-8')) + 256
                            for _, _, _, name, size in entries)
            use_zip64 = (estimate >= min(ZIP64_THRESHOLD, ZIP32_SAFE_LIMIT)
                         or len(members) >= ZIP32_ENTRY_LIMIT)

            _safe_path(native_io_path(self.incomplete))
            staging = native_io_path(self.incomplete) / ('zip-' + uuid4().hex + '.tmp')
            # Never overwrite a retained archive from an earlier attempt.
            with staging.open('xb') as output:
                with ZipFile(output, 'w', compression=ZIP_DEFLATED,
                             compresslevel=6, allowZip64=use_zip64) as archive:
                    if replace_existing_sha256 is not None:
                        with ZipFile(destination, 'r') as previous:
                            archive.comment = previous.comment
                            for name, info in previous_infos.items():
                                if name not in old_names:
                                    continue
                                new_info = copy.copy(info)
                                new_info.filename = name
                                new_info.extra = _without_zip64_extra(info.extra)
                                if not use_zip64 and info.compress_type in (0, 8):
                                    new_info.extract_version = min(info.extract_version, 20)
                                    new_info.create_version = min(info.create_version, 20)
                                with previous.open(info) as source, archive.open(new_info, 'w', force_zip64=use_zip64) as target:
                                    shutil.copyfileobj(source, target, 1024 * 1024)
                    for relative, digest, index, name, _ in entries:
                        if self._closing:
                            raise LegacyZipExportError('ZIP export interrupted by shutdown')
                        data = self.managed.read_saved_page(relative, index, digest)
                        with archive.open(name, 'w', force_zip64=use_zip64) as target:
                            target.write(data)
                output.flush()
                os.fsync(output.fileno())
            self._verify_archive(staging, members, zip_name[:-4])
            if self._closing:
                raise LegacyZipExportError('ZIP export interrupted by shutdown')
            # Prevent exporting an outdated author generation after a
            # concurrent Pixiv managed download has changed active pointers.
            with ExitStack() as locks:
                for wid in sorted(source_pages):
                    locks.enter_context(self.managed.work_lock(wid))
                current_snapshot = self.managed.catalog_snapshot()
                current_rows = [(relative, row, digest)
                                for relative, row, digest in current_snapshot.receipts
                                if row['author_id'] == author_id]
                current_pages = {row['work_id']: current_snapshot.source_pages[row['work_id']]
                                 for _, row, _ in current_rows}
                current_tokens = {wid: current_snapshot.work_tokens[wid] for wid in current_pages}
                if current_pages != source_pages or current_tokens != source_tokens:
                    raise LegacyZipExportError('managed library changed during ZIP export')
                _safe_path(native_io_path(self.root))
                _safe_path(destination, file=True)
                if before_publish is not None:
                    retain_stage = True
                    before_publish(staging,destination)
                if any(self.managed._generations.work_state_token(wid) != token
                       for wid, token in source_tokens.items()):
                    raise LegacyZipExportError('managed library changed during ZIP export')
                if replace_existing_sha256 is None:
                    os.link(staging, destination)
                else:
                    if verified_zip_digest(destination)!=replace_existing_sha256:
                        raise LegacyZipExportError('previous ZIP changed')
                    os.replace(staging,destination)
                    _fsync_dir(staging.parent)
                _fsync_dir(native_io_path(self.root))
            return logical_path(destination)
        except (LegacyZipExportError, ManagedDownloadError):
            raise
        except (OSError, ValueError, BadZipFile, LargeZipFile, KeyError, RuntimeError, ZlibError):
            raise LegacyZipExportError('legacy ZIP export failed') from None
        finally:
            if staging is not None and not retain_stage:
                try:
                    staging.unlink(missing_ok=True)
                except OSError:
                    pass
            self._writer.release()
