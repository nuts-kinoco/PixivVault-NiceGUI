"""Offline deterministic PNG diagnostics for synthetic artwork."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import math
import re
import struct
import threading
import zlib

from .models import Artwork


_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_VARIANTS = {"thumb": 192, "view": 640, "original": 1280}


@dataclass(frozen=True, slots=True)
class ImageBlob:
    data: bytes
    media_type: str
    etag: str


def _chunk(name: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + name + payload + struct.pack(">I", zlib.crc32(name + payload) & 0xFFFFFFFF)


def _png(work_id: str, page: int, variant: str, ratio: float) -> bytes:
    longest = _VARIANTS[variant]
    if ratio >= 1:
        width, height = longest, max(1, round(longest / ratio))
    else:
        width, height = max(1, round(longest * ratio)), longest
    # Original is the same generated artwork at a larger resolution, never a
    # different random work or an upscaled 640-pixel view.
    seed_variant = "view" if variant == "original" else variant
    seed = hashlib.sha256(f"{work_id}:{page}:{seed_variant}".encode("ascii")).digest()
    # Per-row color bars and a page-dependent marker make decoding and page identity inspectable.
    pixels = bytearray()
    for y in range(height):
        pixels.append(0)  # PNG filter None
        for x in range(width):
            band = (x * 5 // width + y * 3 // height) % 8
            pixels.extend(((seed[band] + x // 8) & 255, (seed[band + 8] + y // 8) & 255, seed[band + 16]))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header) + _chunk(b"IDAT", zlib.compress(pixels, 6)) + _chunk(b"IEND", b"")


class DemoImageCatalog:
    """Bounded, thread-safe, offline image fixture catalog."""

    def __init__(self, artworks, *, max_bytes: int = 8 * 1024 * 1024):
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self._artworks: dict[str, Artwork] = {}
        for item in artworks:
            if not _SAFE_ID.fullmatch(item.work_id) or item.work_id in self._artworks:
                raise ValueError("invalid or duplicate work_id")
            if not math.isfinite(item.aspect_ratio) or not 0.05 <= item.aspect_ratio <= 20:
                raise ValueError("invalid aspect ratio")
            if type(item.page_count) is not int or not 1 <= item.page_count <= 10000:
                raise ValueError("invalid page_count")
            self._artworks[item.work_id] = item
        self._max_bytes = max_bytes
        self._lock = threading.RLock()
        self._cache: OrderedDict[tuple[str, int, str], ImageBlob] = OrderedDict()
        self._bytes = 0
        self._hits = 0
        self._misses = 0
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

    def _validate(self, work_id: str, page_no: int, variant: str) -> Artwork:
        if not isinstance(work_id, str) or not _SAFE_ID.fullmatch(work_id):
            raise ValueError("invalid work id")
        if not isinstance(variant, str) or variant not in _VARIANTS:
            raise ValueError("invalid variant")
        artwork = self._artworks.get(work_id)
        if artwork is None or type(page_no) is not int or not 0 <= page_no < artwork.page_count:
            raise ValueError("image not found")
        return artwork

    def url(self, work_id: str, page_no: int = 0, variant: str = "view") -> str:
        with self._lock:
            if self._closed:
                raise RuntimeError("catalog closed")
            self._validate(work_id, page_no, variant)
            return f"/media/{work_id}/{page_no}/{variant}.png"

    def supports_original(self, work_id: str) -> bool:
        with self._lock:
            if self._closed:
                return False
            return work_id in self._artworks

    def get(self, work_id: str, page_no: int = 0, variant: str = "view") -> ImageBlob:
        with self._lock:
            if self._closed:
                raise RuntimeError("catalog closed")
            artwork = self._validate(work_id, page_no, variant)
            key = (work_id, page_no, variant)
            if self._shared_cache is not None:
                cached=self._shared_cache.get(key)
                if cached is not None:
                    self._hits+=1
                    return cached
            elif key in self._cache:
                self._hits += 1
                self._cache.move_to_end(key)
                return self._cache[key]
            self._misses += 1
            data = _png(work_id, page_no, variant, artwork.aspect_ratio)
            blob = ImageBlob(data, "image/png", '"' + hashlib.sha256(data).hexdigest() + '"')
            if self._shared_cache is not None:
                self._shared_cache.put(key,blob)
                return blob
            if len(data) <= self._max_bytes:
                while self._cache and self._bytes + len(data) > self._max_bytes:
                    _, old = self._cache.popitem(last=False)
                    self._bytes -= len(old.data)
                self._cache[key] = blob
                self._bytes += len(data)
            return blob

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"hits": self._hits, "misses": self._misses, "entries": len(self._cache), "bytes": self._bytes}

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._shared_cache is not None:self._shared_cache.close()
            self._cache.clear()
            self._bytes = 0
