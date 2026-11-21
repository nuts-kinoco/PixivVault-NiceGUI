"""Read-only in-memory images for authenticated bookmarks, including non-downloaded works."""
from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import Future
from dataclasses import replace
import hashlib
from io import BytesIO
from threading import BoundedSemaphore, RLock
from typing import NamedTuple

from PIL import Image, UnidentifiedImageError

from .bookmark_remote_adapter import RemoteBookmark, thumbnail_url
from .media import ImageBlob
from .pixiv_read_client import HTTPStatusRejected


class RemoteImageNotFound(RuntimeError):
    """An individual work is missing both its page list and its listing image."""


class RemoteImageWithVariant(NamedTuple):
    blob: ImageBlob
    variant: str

    @property
    def data(self):
        return self.blob.data

    @property
    def media_type(self):
        return self.blob.media_type


class RemoteBookmarkImages:
    def __init__(self, client, records: tuple[RemoteBookmark, ...], *, max_bytes: int = 32 * 1024 * 1024):
        self.client = client
        self.works = {record.work_id: record for record in records}
        self._lock = RLock()
        self._decode_slots = BoundedSemaphore(2)
        self._closed = False
        self._cache = OrderedDict()
        self._shared_cache=None
        self._shared_owner=None
        self._bytes, self._limit = 0, max_bytes
        self._pages: dict[str, tuple[str, ...]] = {}
        self._flights = {}

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

    def configure_prefetch(self,mode,limit):
        with self._lock:
            if self._closed or self._shared_cache is None:raise RuntimeError('learning cache unavailable')
            self._shared_cache.configure_fifo(mode,limit)

    def prefetch_stats(self):
        with self._lock:
            if self._closed or self._shared_cache is None:raise RuntimeError('learning cache unavailable')
            return self._shared_cache.prefetch_stats()

    def _record(self, work_id, page_no, variant):
        record = self.works.get(work_id)
        if (record is None or type(page_no) is not int or
                not 0 <= page_no < record.page_count or variant not in {"thumb", "view", "original"}):
            raise ValueError("image not found")
        if self._closed:
            raise RuntimeError("image catalog closed")
        return record

    def is_animation(self, work_id):
        with self._lock:
            return self._record(work_id,0,'thumb').illustration_type==2

    def supports_original(self, work_id):
        with self._lock:
            record=self.works.get(work_id)
            return (not self._closed and record is not None and
                    record.kind=='illustration' and record.illustration_type in (0,1)
                    and callable(getattr(self.client,'get_artwork_original_pages',None))
                    and callable(getattr(self.client,'get_image_bytes',None)))

    def display_animation(self,work_id):
        from .viewer_gif import DisplayedGif
        with self._lock:
            record=self._record(work_id,0,'view')
            if record.illustration_type!=2:raise ValueError('animation required')
        blob=self.get(work_id,0,'view')
        if blob.media_type!='image/gif':raise ValueError('displayed GIF unavailable')
        return DisplayedGif(record,blob.data,hashlib.sha256(blob.data).hexdigest())

    def needs_metadata(self, work_id: str) -> bool:
        with self._lock:
            record = self._record(work_id, 0, "view")
            return record.kind == "illustration" and record.illustration_type != 2 and not record.page_count_known

    def artwork_for(self, work_id: str):
        with self._lock:
            # Metadata remains readable even when a media type's full viewer
            # is unsupported; one animation must not break all other cards.
            return self._record(work_id, 0, "thumb").as_artwork()

    def prepare_artwork(self, work_id: str, *, allow_thumbnail_fallback=False):
        """Resolve the unknown count, or return None to use the listing image on a 404."""
        with self._decode_slots:
            with self._lock:
                record = self._record(work_id, 0, "view")
                if record.kind == "novel" or record.illustration_type == 2 or record.page_count_known:
                    return record.as_artwork()
            try:
                pages = tuple(self.client.get_artwork_pages(record.remote_id))
                if not 1 <= len(pages) <= 1000:
                    raise RuntimeError("invalid page count")
                for url in pages:
                    thumbnail_url(url)
            except Exception as error:
                if allow_thumbnail_fallback and isinstance(error, HTTPStatusRejected) and error.status_code == 404:
                    return None
                raise RuntimeError("remote page metadata unavailable") from None
            with self._lock:
                self._record(work_id, 0, "view")
                known = replace(record, page_count=len(pages), page_count_known=True)
                self.works[work_id] = known
                self._pages[work_id] = pages
                return known.as_artwork()

    def url(self, work_id: str, page_no: int = 0, variant: str = "view") -> str:
        with self._lock:
            self._record(work_id, page_no, variant)
        return f"/media/{work_id}/{page_no}/{variant}.png"

    def get(self, work_id: str, page_no: int = 0, variant: str = "view") -> ImageBlob:
        return self.get_with_variant(work_id, page_no, variant).blob

    def get_with_variant(self, work_id: str, page_no: int = 0,
                         variant: str = "view") -> RemoteImageWithVariant:
        """Foreground and learning-prefetch requests share one in-flight read."""
        key=(work_id,page_no,variant)
        with self._lock:
            self._record(work_id,page_no,variant)
            future=self._flights.get(key)
            owner=future is None
            if owner:
                future=Future();self._flights[key]=future
        if not owner:
            result=future.result()
            with self._lock:self._record(work_id,page_no,variant)
            return result
        try:
            result=self._get_uncached(work_id,page_no,variant)
            with self._lock:
                self._record(work_id,page_no,variant)
                future.set_result(result)
            return result
        except BaseException as exc:
            future.set_exception(exc)
            raise
        finally:
            with self._lock:
                if self._flights.get(key) is future:self._flights.pop(key)

    @staticmethod
    def _cached_variant(cached, requested):
        # The v1 disk cache only persists PNG thumbnails previously scaled to
        # fit 192px. Disk promotion yields a plain ImageBlob in shared memory.
        if isinstance(cached, RemoteImageWithVariant):
            return cached
        if cached is not None and requested == 'thumb':
            return RemoteImageWithVariant(cached, 'thumb_192')
        return None

    def _remember(self, key, blob, actual, *, disk_key=None):
        entry = RemoteImageWithVariant(blob, actual)
        if self._shared_cache is not None:
            self._shared_cache.put(key, entry, disk_key=disk_key)
        else:
            previous = self._cache.pop(key, None)
            if previous is not None:
                self._bytes -= len(previous.data)
            size = len(blob.data)
            if size <= self._limit:
                while self._cache and self._bytes + size > self._limit:
                    self._bytes -= len(self._cache.popitem(last=False)[1].data)
                self._cache[key] = entry
                self._bytes += size
        return entry

    def _get_uncached(self, work_id: str, page_no: int = 0,
                      variant: str = "view") -> RemoteImageWithVariant:
        key = (work_id, page_no, variant)
        with self._lock:
            record = self._record(work_id, page_no, variant)
            disk_key=("remote-thumbnail-v1:"+work_id+":"+str(page_no)+":"+thumbnail_url(record.thumbnail)) if variant=="thumb" else None
            if self._shared_cache is not None:
                cached = self._cached_variant(self._shared_cache.get(key, disk_key=disk_key), variant)
                if cached is not None:
                    return cached
            elif key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        use_listing_thumbnail = False
        # Discovery starts with a thumbnail and an explicitly unknown count.
        # Resolve that metadata before entering the decode semaphore, otherwise
        # a multi-page work would be rejected against the placeholder count=1.
        if variant in ('view','original') and record.kind == 'illustration' and record.illustration_type != 2 and not record.page_count_known:
            from .request_policy import interactive_lane
            with interactive_lane():
                artwork = self.prepare_artwork(
                    work_id, allow_thumbnail_fallback=variant == 'view' and page_no == 0)
            use_listing_thumbnail = artwork is None
            with self._lock:record=self._record(work_id,page_no,variant)
        if variant == 'original':
            # Fetch one original only when the user requests it. No default
            # prefetch, no written media, and no silent fallback to regular.
            from .image_original import verified_original_blob
            if not self.supports_original(work_id):
                raise RuntimeError('original image unavailable')
            with self._decode_slots:
                try:
                    with self._lock:self._record(work_id,page_no,variant)
                    pages=tuple(self.client.get_artwork_original_pages(record.remote_id))
                    if len(pages)!=record.page_count:
                        raise ValueError('remote original page set changed')
                    for original_url in pages:
                        thumbnail_url(original_url)
                    raw=self.client.get_image_bytes(pages[page_no])
                    blob=verified_original_blob(raw)
                except Exception:
                    raise RuntimeError('remote original image unavailable') from None
            with self._lock:
                self._record(work_id,page_no,variant)
                return self._remember(key, blob, 'original')
        if variant == 'view' and record.illustration_type == 2:
            from .ugoira_gif import render_ugoira_gif
            with self._decode_slots:
                manifest=self.client.get_ugoira_manifest(record.remote_id)
                if manifest.work_id!=record.remote_id:raise ValueError('animation identity changed')
                rendered=render_ugoira_gif(self.client.get_ugoira_archive(manifest),manifest)
            blob=ImageBlob(rendered.data,'image/gif','"'+rendered.sha256+'"')
            with self._lock:
                self._record(work_id,page_no,variant)
                return self._remember(key, blob, 'ugoira_gif')
        with self._decode_slots:
            try:
                with self._lock:
                    self._record(work_id, page_no, variant)
                if use_listing_thumbnail or variant == "thumb" or record.kind == "novel":
                    url = record.thumbnail
                    actual = 'thumb_192' if variant == 'thumb' else (
                        'listing_thumb_fallback' if use_listing_thumbnail else 'novel_cover')
                else:
                    with self._lock:
                        pages = self._pages.get(work_id)
                    if pages is None:
                        with self._lock:
                            self._record(work_id, page_no, variant)
                        pages = tuple(self.client.get_artwork_pages(record.remote_id))
                        if not pages or len(pages) != record.page_count:
                            raise RuntimeError("remote page metadata changed; reload bookmarks")
                        for candidate in pages:
                            thumbnail_url(candidate)
                        with self._lock:
                            if self._closed:
                                raise RuntimeError("image catalog closed")
                            self._pages[work_id] = pages
                    url = pages[page_no]
                    actual = 'regular_view'
                with self._lock:
                    self._record(work_id, page_no, variant)
                self._before_image_fetch()
                data = self.client.get_image_bytes(thumbnail_url(url))
                if not isinstance(data, bytes) or len(data) > 24 * 1024 * 1024:
                    raise RuntimeError("remote image exceeds limit")
                with Image.open(BytesIO(data), formats=["PNG", "JPEG", "WEBP", "GIF"]) as image:
                    if image.width * image.height > 28_000_000:
                        raise RuntimeError("remote image exceeds pixel limit")
                    image.thumbnail((192, 192) if variant == "thumb" else (1920, 1920))
                    out = BytesIO()
                    image.convert("RGBA" if "A" in image.getbands() or "transparency" in image.info
                                  else "RGB").save(out, format="PNG")
                png = out.getvalue()
                blob = ImageBlob(png, "image/png", '"' + hashlib.sha256(png).hexdigest() + '"')
            except Exception as error:
                # HTTP exception strings and Pillow messages may contain external data.
                if use_listing_thumbnail and isinstance(error, HTTPStatusRejected) and error.status_code == 404:
                    raise RemoteImageNotFound("remote image unavailable") from None
                raise RuntimeError("remote image unavailable") from None
        with self._lock:
            if self._closed:
                raise RuntimeError("image catalog closed")
            return self._remember(key, blob, actual, disk_key=disk_key)

    def _before_image_fetch(self):
        pass

    def peek(self, work_id: str, page_no: int, variant: str):
        with self._lock:
            self._record(work_id, page_no, variant)
            key = (work_id, page_no, variant)
            cached = self._shared_cache.get(key) if self._shared_cache is not None else self._cache.get(key)
            return cached.blob if isinstance(cached, RemoteImageWithVariant) else cached

    def peek_with_variant(self, work_id: str, page_no: int,
                          variant: str) -> RemoteImageWithVariant | None:
        """Read an in-memory result without changing eviction order or fetching."""
        with self._lock:
            self._record(work_id, page_no, variant)
            key = (work_id, page_no, variant)
            namespace = self._shared_cache
            if namespace is None:
                return self._cache.get(key)
            # Namespace.get() promotes shared LRU entries. Inspect the same
            # namespace and entry under its owner lock, without promotion.
            with namespace.owner._lock:
                if namespace.closed or namespace.owner._closed:
                    return None
                cached = namespace.owner._entries.get((namespace.identity, key))
                return self._cached_variant(cached, variant)

    def close(self):
        with self._lock:
            self._closed = True
            self._cache.clear()
            if self._shared_cache is not None:self._shared_cache.close()
            self._pages.clear()
            self._bytes = 0


class LegacyNovelPreviewImages(RemoteBookmarkImages):
    """Version-owned legacy metadata URLs, used only for author thumbnails."""

    def __init__(self, client, urls, enabled):
        from types import SimpleNamespace
        self._urls = dict(urls)
        self._enabled = enabled
        records = tuple(SimpleNamespace(work_id=wid, remote_id=wid.removeprefix('novel:'),
                         thumbnail=url, page_count=1, kind='novel', illustration_type=None)
                        for wid, url in self._urls.items())
        super().__init__(client, records)

    def clone(self, client):
        with self._lock:
            if self._closed:
                raise RuntimeError('image catalog closed')
            return LegacyNovelPreviewImages(client, self._urls, self._enabled)

    def get_with_variant(self, work_id, page_no=0, variant='view'):
        if variant != 'thumb' or self.client is None or not self._enabled():
            raise RuntimeError('legacy preview unavailable')
        return super().get_with_variant(work_id, page_no, variant)

    def _before_image_fetch(self):
        if self.client is None or not self._enabled():
            raise RuntimeError('legacy preview unavailable')

    def close(self):
        super().close()
        self._urls.clear()
        self.works.clear()
