"""Offline runner checks that do not start a browser or a Pixiv connection."""
from io import BytesIO
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from tools.check_managed_downloads_browser import (
    ManagedTransport, ORIGINAL, png, verify_receipts, run)
from tools.check_live_bookmark_browser import write_synthetic_cookie


def request(url, cookie=False):
    from urllib.request import Request
    return Request(url, headers={"Cookie": "PHPSESSID=synthetic"} if cookie else {})


def test_synthetic_original_is_distinct_and_cookie_free():
    transport = ManagedTransport()
    regular = transport(request("https://i.pximg.net/synthetic/202-page0.png"), 1, 1, 1)
    first = transport(request(ORIGINAL.format(wid="202", page=0)), 1, 1, 1)
    second = transport(request(ORIGINAL.format(wid="202", page=1)), 1, 1, 1)
    with Image.open(BytesIO(regular)) as image:
        assert image.size == (48, 32) and image.getpixel((0, 0)) == (230, 29, 17)
    for payload, color in ((first, (13, 37, 211)), (second, (9, 191, 53))):
        with Image.open(BytesIO(payload)) as image:
            assert image.size == (120, 84) and image.getpixel((0, 0)) == color
    assert transport.original_gets == {"202": 2}
    assert transport.image_requests_cookie_free and transport.all_requests_get
    with pytest.raises(AssertionError):
        transport(request(ORIGINAL.format(wid="202", page=0), cookie=True), 1, 1, 1)


def test_synthetic_page_wire_has_original_and_regular():
    transport = ManagedTransport()
    url = "https://www.pixiv.net/ajax/illust/202/pages"
    payload = json.loads(transport(request(url, cookie=True), 1, 1, 1))
    assert len(payload["body"]) == 2
    assert payload["body"][0]["urls"]["original"] == ORIGINAL.format(wid="202", page=0)
    assert payload["body"][1]["urls"]["original"] == ORIGINAL.format(wid="202", page=1)
    assert payload["body"][0]["urls"]["regular"] != payload["body"][0]["urls"]["original"]


def test_real_client_uses_wire_transport_for_originals(tmp_path):
    from pixivvault_nicegui.pixiv_read_client import LivePixivReadClient
    transport = ManagedTransport()
    client = LivePixivReadClient(write_synthetic_cookie(tmp_path), transport=transport)
    try:
        assert client.verify_identity() == "12"
        assert client.get_artwork_original_pages("202") == (
            ORIGINAL.format(wid="202", page=0), ORIGINAL.format(wid="202", page=1))
        blob = client.get_image_bytes(ORIGINAL.format(wid="202", page=0))
        with Image.open(BytesIO(blob)) as image:
            assert image.size == (120, 84)
        assert transport.original_gets == {"202": 1}
    finally:
        client.close()


def test_receipt_verification_reads_real_bytes(tmp_path):
    folder = tmp_path / "outputs" / "work-202-test"
    folder.mkdir(parents=True)
    files = []
    for index, color in enumerate(((13, 37, 211), (9, 191, 53)), start=1):
        content = png((120, 84), color)
        filename = f"page-{index}.png"
        (folder / filename).write_bytes(content)
        files.append({"name": filename, "bytes": len(content),
                      "sha256": hashlib.sha256(content).hexdigest()})
    (folder / "receipt.json").write_text(json.dumps({
        "version": 1, "source": "pixiv-original", "work_id": "202",
        "page_count": 2, "files": files}), encoding="utf-8")
    assert verify_receipts(tmp_path) == folder / "receipt.json"
    (folder / "page-2.png").write_bytes(b"tampered")
    with pytest.raises(AssertionError):
        verify_receipts(tmp_path)


def test_requires_new_output_and_synthetic_cookie_is_disposable(tmp_path):
    with pytest.raises(ValueError, match="new --output"):
        run("chromium", tmp_path)
    path = write_synthetic_cookie(tmp_path)
    assert path.parent == tmp_path and "synthetic_only" in path.read_text()


def test_rejected_output_does_not_import_optional_browser_dependency(tmp_path, monkeypatch):
    import builtins
    original_import = builtins.__import__
    def guarded_import(name, *args, **kwargs):
        if name == "playwright" or name.startswith("playwright."):
            raise AssertionError("invalid input tried to import optional browser dependency")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    with pytest.raises(ValueError, match="new --output"):
        run("chromium", tmp_path)
