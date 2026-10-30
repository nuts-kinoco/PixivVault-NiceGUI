"""Verified single-page saves, separate from complete-work CAS receipts.

Only ManagedPixivDownloads' already owned root is used. Publication is one
atomic directory rename; incomplete bytes are never a saved page. A page does
not enter the complete-work catalog or authorize skipping a whole-work job.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from uuid import uuid4

from .fs_paths import native_io_path
from .managed_downloads import (IDENTITY, MARKER, MAX_PAGE_BYTES, MAX_RECEIPT_BYTES,
    ManagedDownloadError, _safe_path, _read_bounded, _write, _fsync_dir, _image,
    _json_unique, _record)
from .managed_download_requests import _spec, _decode

PAGE_MARKER = '.pixivvault-viewer-pages.json'
PAGE_IDENTITY = {'application': 'pixivvault-nicegui', 'purpose': 'viewer-original-pages', 'version': 1}


@dataclass(frozen=True)
class SavedViewerPage:
    work_id: str
    page_no: int
    source_page_count: int
    relative_receipt: str
    receipt_sha256: str
    relative_image: str
    reused: bool
    export_manifest: str | None = None
    export_error: bool = False


def selection(work_id, page_no, page_count, author_id):
    if (type(work_id) is not str or re.fullmatch(r'[1-9][0-9]{0,19}', work_id) is None
            or type(page_no) is not int or type(page_count) is not int
            or not 0 <= page_no < page_count <= 1000
            or author_id is not None and (type(author_id) is not str or
                re.fullmatch(r'[1-9][0-9]{0,19}', author_id) is None)):
        raise ManagedDownloadError('viewer page selection rejected')


def original_name(value):
    """An original basename is a filename, never a decoded path or URL."""
    from pathlib import PureWindowsPath
    if (type(value) is not str or not value or value in ('.', '..') or
            any(c in value for c in '/\\:*?"<>|') or any(ord(c)<32 or ord(c)==127 for c in value) or
            value.endswith((' ', '.')) or PureWindowsPath(value).is_reserved()):
        raise ManagedDownloadError('original filename rejected')
    return value


def _prepare(owner):
    root = native_io_path(owner.root)
    if json.loads(_read_bounded(root / MARKER, 4096), object_pairs_hook=_json_unique) != IDENTITY:
        raise ManagedDownloadError('managed marker rejected')
    pages = root / 'viewer-pages'
    _safe_path(pages)
    pages.mkdir(exist_ok=True)
    _fsync_dir(root)
    marker = pages / PAGE_MARKER
    _safe_path(marker, file=True)
    if marker.exists():
        if json.loads(_read_bounded(marker, 4096), object_pairs_hook=_json_unique) != PAGE_IDENTITY:
            raise ManagedDownloadError('viewer page root rejected')
    else:
        if any(pages.iterdir()):
            raise ManagedDownloadError('foreign viewer page root rejected')
        _write(marker, json.dumps(PAGE_IDENTITY, sort_keys=True).encode('ascii'))
        _fsync_dir(pages)
    staging = pages / 'incomplete'
    _safe_path(staging)
    staging.mkdir(exist_ok=True)
    return pages, staging


def _read(folder, work_id, page_no, page_count, author_id):
    """Validate all page proof/bytes; unknown extras and links fail closed."""
    _safe_path(folder)
    raw = _read_bounded(folder / 'receipt.json', MAX_RECEIPT_BYTES)
    value = json.loads(raw, object_pairs_hook=_json_unique)
    fields = {'version', 'scope', 'record', 'page_no', 'saved_at', 'file'}
    if (type(value) is not dict or
            (set(value), value.get('version')) not in ((fields, 1), (fields | {'original_name'}, 2))
            or type(value['version']) is not int
            or value['scope'] != 'pixiv-original-page' or type(value['page_no']) is not int
            or value['page_no'] != page_no):
        raise ManagedDownloadError('viewer page receipt rejected')
    if value['version'] == 2:
        original_name(value['original_name'])
    spec = _decode(json.dumps(value['record']).encode('utf-8'))
    if (spec.get('remote_id') != work_id or spec.get('page_count') != page_count
            or author_id is not None and spec.get('author_id') != author_id):
        raise ManagedDownloadError('viewer page identity changed')
    if (type(value['saved_at']) is not str or len(value['saved_at']) > 64
            or datetime.fromisoformat(value['saved_at']).tzinfo is None):
        raise ManagedDownloadError('viewer page timestamp rejected')
    entry = value['file']
    if (type(entry) is not dict or set(entry) != {'name','bytes','sha256','format','width','height'}
            or type(entry['bytes']) is not int or not 0 < entry['bytes'] <= MAX_PAGE_BYTES
            or type(entry['width']) is not int or type(entry['height']) is not int
            or type(entry['sha256']) is not str or re.fullmatch(r'[a-f0-9]{64}', entry['sha256']) is None
            or type(entry['format']) is not str):
        raise ManagedDownloadError('viewer page file rejected')
    ext = {'png':'png', 'jpeg':'jpg', 'webp':'webp'}.get(entry['format'])
    if value['version']==2:
        from pathlib import PurePosixPath
        endings={'png':{'.png'},'jpeg':{'.jpg','.jpeg'},'webp':{'.webp'}}
        if PurePosixPath(value['original_name']).suffix.lower() not in endings.get(entry['format'],set()):
            raise ManagedDownloadError('original filename format rejected')
    if ext is None or entry['name'] != f'page-{page_no+1}.{ext}':
        raise ManagedDownloadError('viewer page name rejected')
    if {item.name for item in folder.iterdir()} != {'receipt.json', entry['name']}:
        raise ManagedDownloadError('viewer page contents rejected')
    data = _read_bounded(folder / entry['name'], MAX_PAGE_BYTES)
    if (len(data) != entry['bytes'] or hashlib.sha256(data).hexdigest() != entry['sha256']
            or _image(data)[1:] != (entry['format'], entry['width'], entry['height'])):
        raise ManagedDownloadError('viewer page bytes changed')
    return value, hashlib.sha256(raw).hexdigest()


def save_page(owner, work_id, page_no, page_count, author_id, load_record):
    selection(work_id, page_no, page_count, author_id)
    with owner._page_writer:
        if owner.closed:
            raise ManagedDownloadError('managed download is closed')
        work_state_token = owner.assert_page_available(work_id, page_no)
        # Full saved originals are immutable and already prove this page. Reuse
        # their actual bytes without a GET, copy, or mutation of the active CAS.
        full = owner.verified_saved_receipt(work_id, verify_pages=False)
        if full is not None:
            relative, receipt, digest, _, _ = full
            if (receipt['page_count'] != page_count or
                    author_id is not None and receipt['author_id'] != author_id):
                raise ManagedDownloadError('viewer page identity changed')
            data = owner.read_saved_page(relative, page_no, digest)
            entry = receipt['files'][page_no]
            if _image(data)[1:] != (entry['format'], entry['width'], entry['height']):
                raise ManagedDownloadError('saved viewer image rejected')
            owner.assert_page_available(work_id, page_no,
                                        expected_work_state_token=work_state_token)
            return SavedViewerPage(work_id, page_no, page_count, relative, digest,
                relative.rsplit('/',1)[0] + '/' + entry['name'], True)
        pages, staging = _prepare(owner)
        name = f'work-{work_id}-page-{page_no+1}'
        final = pages / name
        _safe_path(final)
        relative = f'viewer-pages/{name}/receipt.json'
        if final.exists():
            value, digest = _read(final, work_id, page_no, page_count, author_id)
            owner.assert_page_available(work_id, page_no,
                                        expected_work_state_token=work_state_token)
            return SavedViewerPage(work_id, page_no, page_count, relative, digest,
                f"viewer-pages/{name}/{value['file']['name']}", True)
        record = _record(load_record(), work_id)
        if record.page_count != page_count or author_id is not None and record.author_id != author_id:
            raise ManagedDownloadError('viewer page metadata changed')
        # Existing transport validates original hosts, redirects and byte limits.
        owner.client.verify_identity()
        urls = owner.client.get_artwork_original_pages(work_id)
        if (type(urls) not in (list,tuple) or len(urls) != page_count
                or any(type(url) is not str or not url or len(url)>4096 for url in urls)
                or len(set(urls)) != len(urls)):
            raise ManagedDownloadError('original page list rejected')
        from urllib.parse import urlsplit
        original = original_name(urlsplit(urls[page_no]).path.rsplit('/', 1)[-1])
        owner.assert_page_available(work_id, page_no,
                                    expected_work_state_token=work_state_token)
        data = owner.client.get_image_bytes(urls[page_no])
        ext, fmt, width, height = _image(data)
        if owner.closed:
            raise ManagedDownloadError('managed download is closed')
        owner.assert_page_available(work_id, page_no,
                                    expected_work_state_token=work_state_token)
        folder = staging / ('partial-' + uuid4().hex)
        folder.mkdir()
        filename = f'page-{page_no+1}.{ext}'
        _write(folder / filename, data)
        proof = dict(version=2, scope='pixiv-original-page', record=_spec(record), page_no=page_no, original_name=original,
            saved_at=datetime.now(timezone.utc).isoformat(),
            file=dict(name=filename, bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                      format=fmt, width=width, height=height))
        raw = json.dumps(proof, ensure_ascii=True, sort_keys=True).encode('ascii')
        if len(raw)>MAX_RECEIPT_BYTES:
            raise ManagedDownloadError('viewer page receipt too large')
        _write(folder / 'receipt.json', raw)
        _fsync_dir(folder)
        _read(folder, work_id, page_no, page_count, author_id)
        if owner.closed:
            raise ManagedDownloadError('managed download is closed')
        # No replace/overwrite. Failed staging remains inert for inspection.
        with owner.work_lock(work_id):
            owner.assert_page_available(work_id, page_no,
                                        expected_work_state_token=work_state_token)
            folder.rename(final)
            _fsync_dir(pages)
            _, digest = _read(final, work_id, page_no, page_count, author_id)
        return SavedViewerPage(work_id, page_no, page_count, relative, digest,
            f'viewer-pages/{name}/{filename}', False)
