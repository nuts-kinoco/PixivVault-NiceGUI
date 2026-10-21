import concurrent.futures
import struct
import zlib

import pytest

from pixivvault_nicegui.media import DemoImageCatalog
from pixivvault_nicegui.models import Artwork


def artwork(work_id="one", *, ratio=1.0, pages=1):
    return Artwork(work_id=work_id, title="test", author="test", aspect_ratio=ratio, page_count=pages)


def decode(blob):
    data = blob.data
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    offset, parts, dimensions = 8, [], None
    while offset < len(data):
        size = struct.unpack_from(">I", data, offset)[0]
        name = data[offset + 4:offset + 8]
        payload = data[offset + 8:offset + 8 + size]
        crc = struct.unpack_from(">I", data, offset + 8 + size)[0]
        assert crc == zlib.crc32(name + payload) & 0xFFFFFFFF
        offset += 12 + size
        if name == b"IHDR":
            dimensions = struct.unpack(">IIBBBBB", payload)
        if name == b"IDAT":
            parts.append(payload)
        if name == b"IEND":
            break
    assert offset == len(data)
    width, height, depth, color, *_ = dimensions
    assert (depth, color) == (8, 2)
    raw = zlib.decompress(b"".join(parts))
    assert len(raw) == (width * 3 + 1) * height
    assert all(raw[y * (width * 3 + 1)] == 0 for y in range(height))
    return width, height, raw


def test_dimensions_pages_variants_and_determinism():
    catalog = DemoImageCatalog([artwork("wide", ratio=2, pages=2), artwork("tall", ratio=0.5)])
    assert catalog.url("wide", 1, "thumb") == "/media/wide/1/thumb.png"
    view = catalog.get("wide")
    assert decode(view)[:2] == (640, 320)
    assert decode(catalog.get("tall"))[:2] == (320, 640)
    assert decode(catalog.get("wide", 1, "thumb"))[:2] == (192, 96)
    assert catalog.get("wide", 1).data != view.data
    assert DemoImageCatalog([artwork("wide", ratio=2, pages=2)]).get("wide").data == view.data
    assert view.media_type == "image/png"
    assert view.etag.startswith('"') and view.etag.endswith('"')


def test_invalid_and_bounded_lru():
    catalog = DemoImageCatalog([artwork("a"), artwork("b")], max_bytes=3000)
    for args in [("missing",), ("a", 1), ("a", 0, "raw"), ("../a",), ("a", 0, [])]:
        with pytest.raises(ValueError):
            catalog.get(*args)
    first = catalog.get("a", variant="thumb")
    assert catalog.get("a", variant="thumb") is first
    catalog.get("b", variant="thumb")
    stats = catalog.stats()
    assert stats["hits"] == 1 and stats["misses"] == 2
    assert stats["bytes"] <= 3000
    catalog.close()
    assert catalog.stats()["entries"] == 0
    with pytest.raises(RuntimeError):
        catalog.get("a")
    with pytest.raises(RuntimeError):
        catalog.url("a")


def test_invalid_constructor_bounds():
    for limit in (True, 0, 0.5):
        with pytest.raises(ValueError):
            DemoImageCatalog([artwork()], max_bytes=limit)
    with pytest.raises(ValueError):
        DemoImageCatalog([Artwork("a", "test", "test", page_count=True)])


def test_concurrent_same_request_is_computed_once():
    catalog = DemoImageCatalog([artwork("same")])
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda _: catalog.get("same", variant="thumb"), range(24)))
    assert len({id(result) for result in results}) == 1
    assert catalog.stats()["misses"] == 1
    assert catalog.stats()["hits"] == 23
