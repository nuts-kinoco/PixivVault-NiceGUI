"""Non-destructive, explicit-root export of verified managed images as legacy ZIPs.

Never imports or opens the Flet Database. Every archive page originates from
the active, SHA-verified managed receipt, and a staged ZIP is published only
through an atomic no-overwrite hard link. Original PixivVault ZIPs, images,
Cookie and the NiceGUI managed store remain untouched.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
from threading import Lock
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED, LargeZipFile

from .managed_downloads import (ManagedPixivDownloads, ManagedDownloadError,
                                _safe_path, _read_bounded, _write, _fsync_dir,
                                _json_unique)


EXPORT_MARKER = '.pixivvault-nicegui-legacy-zip-export.json'
EXPORT_IDENTITY = {'application': 'pixivvault-nicegui', 'purpose': 'legacy-zip-export',
                   'version': 1}
_ID = re.compile(r'[1-9][0-9]{0,19}\Z')
_PAGE_SUFFIX = re.compile(r'^(.*)_(\d+)$')
_INVALID_FILENAME = re.compile(r'[\\/:*?"<>|]+')
_STAGED_ZIP = re.compile(r'zip-[0-9a-f]{32}\.tmp\Z')
MAX_EXPORT_WORKS = 3000
MAX_EXPORT_PAGES = 5000
MAX_EXPORT_INPUT_BYTES = 1700 * 1024 * 1024
MAX_ZIP_NAME_BYTES = 240


class LegacyZipExportError(ValueError):
    """Fixed public error strings: no original titles, IDs or paths."""


def legacy_filename(title: str, work_id: str, page_no: int, page_count: int,
                    ext: str) -> str:
    """Pure parity of legacy core.py sanitize_filename + work_id_arcname.

    The original migrator inserts work ID *before* a final `_N` suffix.
    Titles themselves ending in `_N` are intentionally handled identically.
    """
    if (type(title) is not str or type(work_id) is not str or not _ID.fullmatch(work_id)
            or type(page_no) is not int or type(page_count) is not int
            or not 0 <= page_no < page_count <= 1000 or ext not in ('png', 'jpg', 'webp')
            or len(title) > 256):
        raise LegacyZipExportError('legacy filename source rejected')
    safe_title = _INVALID_FILENAME.sub('_', title)
    legacy = (f'{safe_title}.{ext}' if page_count == 1
              else f'{safe_title}_{page_no + 1}.{ext}')
    stem, suffix = legacy.rsplit('.', 1)
    if f'({work_id})' in legacy:
        filename = legacy  # Original core.py work_id_arcname is idempotent.
    else:
        match = _PAGE_SUFFIX.match(stem)
        filename = (f'{match.group(1)}({work_id})_{match.group(2)}.{suffix}'
                    if match else f'{stem}({work_id}).{suffix}')
    if len(filename.encode('utf-8')) > MAX_ZIP_NAME_BYTES:
        raise LegacyZipExportError('legacy ZIP filename too long')
    return filename


def _export_path(root: Path) -> Path:
    if not isinstance(root, Path) or not root.is_absolute() or root == root.parent:
        raise LegacyZipExportError('dedicated absolute legacy export root required')
    _safe_path(root)
    return root.resolve()


class LegacyZipExporter:
    def __init__(self, root: Path, managed: ManagedPixivDownloads):
        if not isinstance(managed, ManagedPixivDownloads) or managed.closed:
            raise LegacyZipExportError('managed originals unavailable')
        path = _export_path(Path(root))
        store = managed.root.resolve()
        if path == store or path in store.parents or store in path.parents:
            raise LegacyZipExportError('ZIP export root overlaps managed originals')
        self.root = path
        self.managed = managed
        self._writer = Lock()
        self.closed = False
        self._closing = False
        marker = self.root / EXPORT_MARKER
        if self.root.exists():
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
            _safe_path(self.root.parent)
            self.root.mkdir()
            _safe_path(self.root)
            _write(marker, json.dumps(EXPORT_IDENTITY, sort_keys=True).encode('ascii'))
        self.incomplete = self.root / '.incomplete'
        _safe_path(self.incomplete)
        self.incomplete.mkdir(exist_ok=True)
        _safe_path(self.incomplete)
        # Recover a crash just before/after publishing an exclusive hardlink.
        # The incomplete name is app-owned; never touch an unknown file or a
        # symlink/reparse point. In the post-link crash case st_nlink == 2;
        # unlinking *only* this temporary name leaves the published ZIP intact
        # and restores its normal single-link file identity.
        for leftover in self.incomplete.iterdir():
            info = leftover.lstat()
            if (not _STAGED_ZIP.fullmatch(leftover.name)
                    or not stat.S_ISREG(info.st_mode) or leftover.is_symlink()
                    or getattr(info, 'st_file_attributes', 0) & 0x400
                    or info.st_nlink not in (1, 2)):
                raise LegacyZipExportError('unknown or unsafe incomplete ZIP file')
            leftover.unlink()

    def close(self) -> None:
        # Application shutdown must not close managed image readers halfway
        # through ZIP production. The export is owned by the app, not a tab.
        self._closing = True
        with self._writer:
            self.closed = True

    def export_author(self, author_id: str) -> Path:
        """Export all active still-image works of one author as a new ZIP.

        Does not overwrite, append to, rename, delete or hardlink any original
        managed image. This function is blocking; callers use asyncio.to_thread.
        """
        if type(author_id) is not str or _ID.fullmatch(author_id) is None:
            raise LegacyZipExportError('ZIP author ID rejected')
        if not self._writer.acquire(blocking=False):
            raise LegacyZipExportError('ZIP writer already active')
        staging = None
        try:
            if self.closed or self._closing or self.managed.closed:
                raise LegacyZipExportError('managed originals unavailable')
            receipts = [(relative, receipt, digest)
                        for relative, receipt, digest in self.managed.saved_receipts()
                        if receipt['author_id'] == author_id]
            if self._closing:
                raise LegacyZipExportError('ZIP export interrupted by shutdown')
            if not receipts or len(receipts) > MAX_EXPORT_WORKS:
                raise LegacyZipExportError('author ZIP has no supported work or exceeds limit')
            receipts.sort(key=lambda item: item[1]['work_id'])
            pages = sum(item[1]['page_count'] for item in receipts)
            total_bytes = sum(f['bytes'] for _, receipt, _ in receipts for f in receipt['files'])
            if (pages > MAX_EXPORT_PAGES or not 0 < total_bytes <= MAX_EXPORT_INPUT_BYTES):
                raise LegacyZipExportError('author ZIP exceeds safe size or page limit')
            # Original Flet uses the most recently observed author name. An
            # older receipt can keep the author's pre-rename display name.
            latest = max(receipts, key=lambda item: (item[1]['saved_at'], item[1]['work_id']))
            author_name = _INVALID_FILENAME.sub('_', latest[1]['author_name']) or author_id
            zip_name = f'{author_name}({author_id}).zip'
            if len(zip_name.encode('utf-8')) > MAX_ZIP_NAME_BYTES:
                raise LegacyZipExportError('author ZIP filename too long')
            destination = self.root / zip_name
            _safe_path(self.root)
            _safe_path(destination, file=True)
            # The old app reconciles an author rename to a single ZIP. Our
            # non-destructive export cannot rename an earlier archive, so
            # refuse a second ZIP whose author ID matches an older name.
            for existing in self.root.iterdir():
                if existing.name.endswith(f'({author_id}).zip'):
                    raise LegacyZipExportError('legacy author ZIP already exported')
            if destination.exists():
                raise LegacyZipExportError('legacy ZIP already exists; refusing overwrite')

            members = set()
            entries = []
            for relative, receipt, digest in receipts:
                for page_index, entry in enumerate(receipt['files']):
                    ext = {'png': 'png', 'jpeg': 'jpg', 'webp': 'webp'}[entry['format']]
                    name = legacy_filename(receipt['title'], receipt['work_id'],
                                           page_index, receipt['page_count'], ext)
                    if name in members:
                        raise LegacyZipExportError('duplicate legacy ZIP member rejected')
                    members.add(name)
                    entries.append((relative, digest, page_index, name))

            _safe_path(self.incomplete)
            staging = self.incomplete / ('zip-' + uuid4().hex + '.tmp')
            # Create before ZipFile with xb: an earlier failed staging archive
            # may never be silently overwritten or mistaken for a final ZIP.
            with staging.open('xb') as output:
                with ZipFile(output, 'w', compression=ZIP_DEFLATED,
                             compresslevel=6, allowZip64=False) as archive:
                    for relative, digest, index, name in entries:
                        if self._closing:
                            raise LegacyZipExportError('ZIP export interrupted by shutdown')
                        data = self.managed.read_saved_page(relative, index, digest)
                        archive.writestr(name, data)
                output.flush()
                os.fsync(output.fileno())
            with ZipFile(staging, 'r') as archive:
                if set(archive.namelist()) != members or archive.testzip() is not None:
                    raise LegacyZipExportError('legacy ZIP verification failed')
            if self._closing:
                raise LegacyZipExportError('ZIP export interrupted by shutdown')
            # Prevent exporting an outdated author generation after a
            # concurrent Pixiv managed download has changed active pointers.
            expected = {receipt['work_id']: digest for _, receipt, digest in receipts}
            current = {receipt['work_id']: digest
                       for _, receipt, digest in self.managed.saved_receipts()
                       if receipt['author_id'] == author_id}
            if current != expected:
                raise LegacyZipExportError('managed library changed during ZIP export')
            _safe_path(self.root)
            _safe_path(destination, file=True)
            # A same-volume hard link is an atomic *exclusive create* on both
            # POSIX and NTFS. os.rename/replace could overwrite on POSIX.
            os.link(staging, destination)
            _fsync_dir(self.root)
            return destination
        except (LegacyZipExportError, ManagedDownloadError):
            raise
        except (OSError, ValueError, LargeZipFile, KeyError, RuntimeError):
            raise LegacyZipExportError('legacy ZIP export failed') from None
        finally:
            if staging is not None:
                try:
                    staging.unlink(missing_ok=True)
                except OSError:
                    pass
            self._writer.release()
