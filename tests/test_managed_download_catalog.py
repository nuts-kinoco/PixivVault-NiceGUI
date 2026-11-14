"""Only synthetic receipts and images; no real downloaded Pixiv files."""
from __future__ import annotations

from io import BytesIO
import hashlib

import pytest
from PIL import Image

from pixivvault_nicegui.managed_download_catalog import ManagedDownloadCatalog, build_managed_library
from pixivvault_nicegui.local_media_adapter import MediaUnavailable


def _image(color):
    stream = BytesIO()
    Image.new("RGB", (16, 12), color).save(stream, "PNG")
    return stream.getvalue()


class FakeWriter:
    def __init__(self):
        self.pages = {"1": [_image("red")], "2": [_image("blue")]}
        self.rows = [self.make_row(work_id) for work_id in self.pages]
        self.closed = False

    def make_row(self, work_id):
        raw = self.pages[work_id][0]
        receipt = {
            "version": 1, "source": "pixiv-original", "work_id": work_id,
            "author_id": "9", "author_name": "作者", "title": "作品" + work_id,
            "tags": ["art"], "created_at": "2026-01-01",
            "saved_at": "2026-10-0" + work_id + "T00:00:00+00:00", "illustration_type": 0, "x_restrict": 0,
            "page_count": 1, "files": [{
                "name": "page-1.png", "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(), "format": "png",
                "width": 16, "height": 12,
            }],
        }
        return ("outputs/work-" + work_id + "-" + work_id.zfill(32) + "/receipt.json", receipt, hashlib.sha256(str(receipt).encode()).hexdigest())

    def saved_receipts(self):
        return tuple(self.rows)

    def read_saved_page(self, relative, page_no, expected_receipt_sha256):
        row = next(row for row in self.rows if row[0] == relative)
        if expected_receipt_sha256 != row[2]:
            raise ValueError("receipt changed")
        return self.pages[row[1]["work_id"]][page_no]


def test_snapshot_grouping_order_and_image_derivative():
    writer = FakeWriter()
    snapshot, catalog, artworks = build_managed_library(writer)
    assert [work.work_id for work in snapshot.works] == ["2", "1"]
    assert len(snapshot.authors) == 1
    assert snapshot.authors[0].capacity_bytes == sum(len(image[0]) for image in writer.pages.values())
    assert tuple(art.work_id for art in artworks) == ("2", "1")
    assert isinstance(catalog.receipts, tuple)
    mutated = catalog.receipts
    mutated[0][1]["files"][0]["sha256"] = "0" * 64
    assert catalog.receipts[0][1]["files"][0]["sha256"] != "0" * 64
    direct = ManagedDownloadCatalog(writer, catalog.receipts)
    assert direct.get("1", 0).media_type == "image/png"
    direct.close()
    assert catalog.url("1", 0, "view") == "/media/1/0/view.png"
    assert catalog.peek("1", 0, "view") is None
    original = writer.pages["1"][0]
    blob = catalog.get("1", 0, "view")
    assert blob.data.startswith(b"\x89PNG")
    assert catalog.peek("1", 0, "view") is blob
    assert writer.pages["1"][0] == original
    assert catalog.artwork_for("1").work_id == "1"
    catalog.close()
    assert not writer.closed
    with pytest.raises(RuntimeError):
        catalog.get("1")


def test_receipt_validation_and_duplicate_failure():
    writer = FakeWriter()
    writer.rows.append(writer.rows[0])
    with pytest.raises(ValueError, match="duplicate"):
        build_managed_library(writer)
    writer = FakeWriter()
    writer.rows[0][1]["files"][0]["name"] = "../bad.png"
    with pytest.raises(ValueError, match="filename"):
        build_managed_library(writer)
    writer = FakeWriter()
    writer.rows[0][1]["source"] = "unknown"
    with pytest.raises(ValueError, match="unsupported"):
        build_managed_library(writer)


def test_pinned_receipts_and_distinct_client_instances():
    writer = FakeWriter()
    _, old, _ = build_managed_library(writer)
    writer.rows[0] = writer.make_row("1")
    writer.rows[0] = (writer.rows[0][0], writer.rows[0][1], "0" * 64)
    _, newer, _ = build_managed_library(writer)
    with pytest.raises(MediaUnavailable):
        old.get("1")
    assert newer.get("1").media_type == "image/png"
    newer.close()
    old.close()


def test_corrupt_original_is_not_cached():
    writer = FakeWriter()
    _, catalog, _ = build_managed_library(writer)
    writer.pages["1"][0] = _image("green")
    with pytest.raises(MediaUnavailable, match="changed"):
        catalog.get("1")
    assert catalog.peek("1", 0, "view") is None
    catalog.close()


@pytest.mark.parametrize("change", [
    lambda r: r.update(work_id="-1"),
    lambda r: r.update(author_id="not-numeric"),
    lambda r: r["files"][0].update(format="gif"),
    lambda r: r["files"][0].update(bytes=20 * 1024 * 1024 + 1),
    lambda r: r["files"][0].update(width=28_000_001),
    lambda r: r.update(saved_at="2026-10-01"),
    lambda r: r.update(title="a" * 257),
    lambda r: r.update(tags=["a" * 129]),
    lambda r: r["files"][0].update(name="wrong.png"),
])
def test_writer_contract_fail_closed(change):
    writer = FakeWriter()
    change(writer.rows[0][1])
    with pytest.raises(ValueError):
        build_managed_library(writer)
