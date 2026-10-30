"""Immutable snapshot adapter for receipts written by ManagedPixivDownloads."""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from datetime import datetime
from io import BytesIO
from pathlib import Path
from threading import BoundedSemaphore, RLock
from types import MappingProxyType
import hashlib
import re

from PIL import Image, UnidentifiedImageError

from .library_models import LibrarySnapshot, LibraryWork
from .local_media_adapter import MediaMissing, MediaUnavailable
from .media import ImageBlob
from .models import Artwork

_ID = re.compile(r"[1-9][0-9]{0,19}\Z")
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_PATH = re.compile(r"(?:outputs|generations)/work-([1-9][0-9]{0,19})-[a-f0-9]{32}/receipt\.json\Z")
_DECODE = BoundedSemaphore(2)
_CACHE_LIMIT = 32 * 1024 * 1024
_MAX_SOURCE = 20 * 1024 * 1024
_MAX_PIXELS = 28_000_000


def _receipt(relative, receipt, digest):
    path_match = _PATH.fullmatch(relative) if type(relative) is str else None
    if path_match is None:
        raise ValueError("unsafe receipt path")
    if not isinstance(receipt, dict) or not isinstance(digest, str) or not _SHA.fullmatch(digest):
        raise ValueError("invalid receipt")
    required = {"version", "source", "work_id", "author_id", "author_name", "title",
                "tags", "created_at", "saved_at", "illustration_type", "x_restrict",
                "page_count", "files"}
    if set(receipt) != required or type(receipt["version"]) is not int or receipt["version"] != 1 or receipt["source"] != "pixiv-original":
        raise ValueError("unsupported receipt")
    for key in ("work_id", "author_id"):
        if type(receipt[key]) is not str or not _ID.fullmatch(receipt[key]):
            raise ValueError("invalid receipt identity")
    if path_match.group(1) != receipt["work_id"]:
        raise ValueError("receipt path identity mismatch")
    for key, limit in (("title", 256), ("author_name", 256), ("created_at", 128), ("saved_at", 64)):
        value = receipt[key]
        if type(value) is not str or len(value) > limit or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("invalid receipt string")
    if type(receipt["tags"]) is not list or len(receipt["tags"]) > 100 or any(
        type(tag) is not str or len(tag) > 128 or any(ord(c) < 32 or ord(c) == 127 for c in tag)
        for tag in receipt["tags"]
    ):
        raise ValueError("invalid tags")
    try:
        if datetime.fromisoformat(receipt["saved_at"]).tzinfo is None:
            raise ValueError("naive timestamp")
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid saved_at") from exc
    if type(receipt["illustration_type"]) is not int or receipt["illustration_type"] not in (0, 1):
        raise ValueError("unsupported illustration type")
    if type(receipt["x_restrict"]) is not int or receipt["x_restrict"] not in (0, 1, 2):
        raise ValueError("invalid x_restrict")
    count = receipt["page_count"]
    if type(count) is not int or not 1 <= count <= 1000 or type(receipt["files"]) is not list or len(receipt["files"]) != count:
        raise ValueError("invalid page count")
    total = 0
    for index, entry in enumerate(receipt["files"], 1):
        if not isinstance(entry, dict) or set(entry) != {"name", "bytes", "sha256", "format", "width", "height"}:
            raise ValueError("invalid file entry")
        name = entry["name"]
        fmt = entry["format"]
        ext = {"png": "png", "jpeg": "jpg", "webp": "webp"}.get(fmt) if type(fmt) is str else None
        if ext is None or name != f"page-{index}.{ext}":
            raise ValueError("invalid page filename or format")
        if type(entry["bytes"]) is not int or not 0 < entry["bytes"] <= _MAX_SOURCE:
            raise ValueError("invalid file length")
        total += entry["bytes"]
        if type(entry["sha256"]) is not str or not _SHA.fullmatch(entry["sha256"]):
            raise ValueError("invalid file checksum")
        if any(type(entry[k]) is not int for k in ("width", "height")) or not 0 < entry["width"] * entry["height"] <= _MAX_PIXELS:
            raise ValueError("invalid image size")
    if total > 2 * 1024 * 1024 * 1024:
        raise ValueError("saved artwork too large")
    return receipt


class ManagedDownloadCatalog:
    """Per-client pinned receipt snapshot; owner retains writer lifetime."""

    def __init__(self, writer, receipts, *, snapshot=None):
        self._writer = writer
        receipts = tuple(receipts)
        if snapshot is None and callable(getattr(writer, 'catalog_snapshot', None)):
            snapshot = writer.catalog_snapshot()
        if snapshot is not None and snapshot.receipts != receipts:
            raise ValueError('managed catalog snapshot changed')
        self.snapshot = snapshot
        entries = {}
        works = {}
        pinned = []
        source_pages = {}
        for relative, source_receipt, digest in receipts:
            receipt = _receipt(relative, deepcopy(source_receipt), digest)
            work_id = receipt["work_id"]
            if work_id in entries:
                raise ValueError("duplicate saved work")
            entries[work_id] = (relative, receipt, digest)
            pinned.append((relative, receipt, digest))
            visible = (snapshot.source_pages[work_id] if snapshot is not None
                       else tuple(range(receipt['page_count'])))
            if (type(visible) is not tuple
                    or any(type(page) is not int or not 0 <= page < receipt['page_count']
                           for page in visible)
                    or tuple(sorted(set(visible))) != visible):
                raise ValueError('managed source-page mapping rejected')
            source_pages[work_id] = visible
            if not visible:
                continue
            first = receipt['files'][visible[0]]
            art = Artwork(work_id, receipt["title"], receipt["author_name"],
                          page_count=len(visible), tags=tuple(receipt["tags"]),
                          aspect_ratio=first["width"] / first["height"],
                          author_id=receipt["author_id"])
            works[work_id] = LibraryWork(
                art, receipt["author_id"], last_backup=receipt["saved_at"],
                author_name=receipt["author_name"],
                capacity_bytes=sum(receipt['files'][page]['bytes'] for page in visible),
                work_type="manga" if receipt["illustration_type"] == 1 else "illust")
        if snapshot is not None and set(snapshot.source_pages) != set(source_pages):
            raise ValueError('managed source-page identities changed')
        self.source_pages = MappingProxyType(source_pages)
        # An independent catalog owns its copy of receipt metadata.
        self._receipts = tuple((relative, deepcopy(receipt), digest) for relative, receipt, digest in pinned)
        self._entries = entries
        self.works = works
        self._lock = RLock()
        self._cache = OrderedDict()
        self._cache_bytes = 0
        self._closed = False
        self._shared_cache=None
        self._shared_owner=None

    def configure_cache(self, shared_cache):
        """Bind one application-owned aggregate cache before publication."""
        with self._lock:
            if self._closed: raise RuntimeError("image catalog closed")
            if self._shared_owner is shared_cache: return
            if self._shared_cache is not None: self._shared_cache.close()
            self._cache.clear()
            if hasattr(self,"_bytes"):self._bytes=0
            if hasattr(self,"_cache_bytes"):self._cache_bytes=0
            self._shared_owner=shared_cache
            self._shared_cache=shared_cache.namespace()

    @property
    def receipts(self):
        return deepcopy(self._receipts)

    def _check(self, work_id, page_no, variant):
        if self._closed:
            raise MediaUnavailable("catalog closed")
        work = self.works.get(work_id) if isinstance(work_id, str) else None
        if work is None or type(page_no) is not int or not 0 <= page_no < work.page_count or variant not in ("thumb", "view", "original"):
            raise MediaMissing("image not found")
        guard = getattr(self._writer, 'assert_page_visible', None)
        if callable(guard):
            try:
                guard(work_id, self.source_pages[work_id][page_no])
            except (OSError, ValueError) as error:
                raise MediaUnavailable('saved page access changed') from error
        return self._entries[work_id]

    def source_page_identity(self, work_id, page_no=0):
        from .managed_downloads import ManagedPageIdentity
        with self._lock:
            relative, receipt, digest = self._check(work_id, page_no, 'view')
            if self.snapshot is None:
                return None
            return ManagedPageIdentity(
                work_id, self.source_pages[work_id][page_no], receipt['page_count'],
                receipt['author_id'], relative, digest,
                self.snapshot.work_tokens[work_id])

    def supports_original(self, work_id):
        with self._lock:
            return not self._closed and work_id in self.works

    def external_target(self,work_id,page_no=0):
        with self._lock:
            relative,receipt,digest=self._check(work_id,page_no,'view')
            source_page=self.source_pages[work_id][page_no]
        raw=self._writer.read_saved_page(relative,source_page,digest)
        entry=receipt['files'][source_page]
        if len(raw)!=entry['bytes'] or hashlib.sha256(raw).hexdigest()!=entry['sha256']:
            raise MediaUnavailable('saved image changed')
        target=Path(self._writer.root)/relative.rsplit('/',1)[0]/entry['name']
        with self._lock:
            if self._closed:raise MediaUnavailable('catalog closed')
        return target

    def url(self, work_id: str, page_no: int = 0, variant: str = "view") -> str:
        with self._lock:
            self._check(work_id, page_no, variant)
            return f"/media/{work_id}/{page_no}/{variant}.png"

    def artwork_for(self, work_id: str):
        with self._lock:
            return self.works[work_id].artwork if work_id in self.works and not self._closed else None

    def peek(self, work_id: str, page_no: int, variant: str):
        with self._lock:
            self._check(work_id, page_no, variant)
            return self._shared_cache.get((work_id,page_no,variant)) if self._shared_cache is not None else self._cache.get((work_id, page_no, variant))

    def _decode(self, relative, receipt, digest, page_no, variant):
        with _DECODE:
            source_page = self.source_pages[receipt['work_id']][page_no]
            raw = self._writer.read_saved_page(relative, source_page, digest)
            entry = receipt["files"][source_page]
            if not isinstance(raw, bytes) or len(raw) != entry["bytes"] or hashlib.sha256(raw).hexdigest() != entry["sha256"]:
                raise MediaUnavailable("saved image has changed")
            if variant == 'original':
                # The managed receipt already pins the exact original-size
                # bytes. Do not decode/resize, change the receipt or re-fetch.
                from .image_original import verified_original_blob
                try:
                    blob=verified_original_blob(raw, expected_size=(entry['width'],entry['height']))
                    suffix=str(entry['format']).lower()
                    expected_mime={'jpg':'image/jpeg','jpeg':'image/jpeg',
                                   'png':'image/png','webp':'image/webp','gif':'image/gif'}.get(suffix)
                    if blob.media_type != expected_mime:
                        raise ValueError('original format changed')
                    return blob
                except ValueError:
                    raise MediaUnavailable('saved original image could not be verified') from None
            disk_key=("managed-thumbnail-v1:"+receipt["work_id"]+":"+str(source_page)+":"+entry["sha256"]+":"+digest) if variant=="thumb" else None
            key=(receipt["work_id"],page_no,variant)
            if self._shared_cache is not None and disk_key is not None:
                cached=self._shared_cache.get(key,disk_key=disk_key)
                if cached is not None:return cached
            try:
                with Image.open(BytesIO(raw)) as img:
                    if img.width != entry["width"] or img.height != entry["height"] or img.width * img.height > _MAX_PIXELS:
                        raise MediaUnavailable("saved image dimensions changed")
                    expected_format = "JPEG" if entry["format"].lower() in ("jpg", "jpeg") else entry["format"].upper()
                    if img.format != expected_format:
                        raise MediaUnavailable("saved image format changed")
                    size = (192, 192) if variant == "thumb" else (1920, 1920)
                    img.draft("RGB", size)
                    img.thumbnail(size)
                    out = BytesIO()
                    img.convert("RGBA" if "A" in img.getbands() or "transparency" in img.info else "RGB").save(out, format="PNG")
            except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
                raise MediaUnavailable("saved image decode failed") from exc
            data = out.getvalue()
            blob=ImageBlob(data, "image/png", '"' + hashlib.sha256(data).hexdigest() + '"')
            if self._shared_cache is not None and disk_key is not None:self._shared_cache.put(key,blob,disk_key=disk_key)
            return blob

    def get(self, work_id: str, page_no: int = 0, variant: str = "view") -> ImageBlob:
        with self._lock:
            relative, receipt, digest = self._check(work_id, page_no, variant)
            key = (work_id, page_no, variant)
            cached = self._shared_cache.get(key) if self._shared_cache is not None else self._cache.get(key)
            if cached is not None:
                if self._shared_cache is None:self._cache.move_to_end(key)
                return cached
        try:
            blob = self._decode(relative, receipt, digest, page_no, variant)
        except (OSError, ValueError, KeyError) as exc:
            raise MediaUnavailable("saved image read failed") from exc
        with self._lock:
            if self._closed:
                raise MediaUnavailable("catalog closed")
            existing = self._shared_cache.get(key) if self._shared_cache is not None else self._cache.get(key)
            if existing is not None:
                return existing
            if self._shared_cache is not None:
                self._shared_cache.put(key,blob)
                return blob
            if len(blob.data) <= _CACHE_LIMIT:
                while self._cache and self._cache_bytes + len(blob.data) > _CACHE_LIMIT:
                    _, old = self._cache.popitem(last=False)
                    self._cache_bytes -= len(old.data)
                self._cache[key] = blob
                self._cache_bytes += len(blob.data)
            return blob

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._shared_cache is not None:self._shared_cache.close()
            self._cache.clear()
            self._cache_bytes = 0


def build_managed_library(writer):
    """Validate every receipt before exposing any works; errors propagate."""
    reader = getattr(writer, 'catalog_snapshot', None)
    snapshot = reader() if callable(reader) else None
    receipts = snapshot.receipts if snapshot is not None else writer.saved_receipts()
    catalog = ManagedDownloadCatalog(writer, receipts, snapshot=snapshot)
    ordered = tuple(sorted(catalog.works.values(), key=lambda item: item.last_backup or "", reverse=True))
    return LibrarySnapshot(ordered), catalog, tuple(item.artwork for item in ordered)
