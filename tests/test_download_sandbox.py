"""The save executor writes only generated fixture pages to a managed directory."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pixivvault_nicegui.download_sandbox import DownloadSandbox, DownloadSandboxError
from pixivvault_nicegui.fixtures import generate_demo_artworks


class Context:
    def __init__(self, stop_at=None):
        self.updates = []
        self.checks = 0
        self.stop_at = stop_at

    def checkpoint(self):
        self.checks += 1
        if self.stop_at == self.checks:
            raise RuntimeError("cooperative stop")

    def progress(self, done, total):
        self.updates.append((done, total))


def test_multi_page_save_produces_verified_receipt(tmp_path):
    with_cleanup = DownloadSandbox(tmp_path / "managed", generate_demo_artworks(3))
    try:
        context = Context()
        result = with_cleanup.run(SimpleNamespace(kind="demo", target_id="demo-0003"), context)
        receipt_path = with_cleanup.root / result
        receipt = json.loads(receipt_path.read_text())
        assert receipt["page_count"] == 3
        assert context.updates == [(0, 3), (1, 3), (2, 3), (3, 3)]
        assert len(receipt["files"]) == 3
        for entry in receipt["files"]:
            data = (receipt_path.parent / entry["name"]).read_bytes()
            assert hashlib.sha256(data).hexdigest() == entry["sha256"]
            assert len(data) == entry["bytes"]
        assert not list(receipt_path.parent.glob("*.partial"))
    finally:
        with_cleanup.close()


def test_nonempty_unknown_directory_is_not_modified(tmp_path):
    folder = tmp_path / "existing-library"
    folder.mkdir()
    original = folder / "pixiv_vault.db"
    original.write_bytes(b"existing private data")
    with pytest.raises(DownloadSandboxError, match="not managed"):
        DownloadSandbox(folder, generate_demo_artworks(3))
    assert list(folder.iterdir()) == [original]
    assert original.read_bytes() == b"existing private data"


def test_relative_root_and_non_synthetic_ids_are_rejected(tmp_path):
    with pytest.raises(DownloadSandboxError):
        DownloadSandbox(Path("relative"), generate_demo_artworks(3))
    from pixivvault_nicegui.models import Artwork
    with pytest.raises(DownloadSandboxError):
        DownloadSandbox(tmp_path / "new", (Artwork("123", "title", "author"),))
    assert not (tmp_path / "new").exists()


def test_stop_never_publishes_a_completion_receipt(tmp_path):
    sandbox = DownloadSandbox(tmp_path / "managed", generate_demo_artworks(3))
    try:
        with pytest.raises(RuntimeError, match="cooperative stop"):
            sandbox.run(SimpleNamespace(kind="demo", target_id="demo-0003"), Context(stop_at=3))
        assert len(list(sandbox.outputs.rglob("*.png"))) == 1
        assert not list(sandbox.outputs.rglob("receipt.json"))
    finally:
        sandbox.close()


def test_reopen_keeps_receipts_and_each_execution_is_isolated(tmp_path):
    first = DownloadSandbox(tmp_path / "managed", generate_demo_artworks(3))
    result = first.run(SimpleNamespace(kind="demo", target_id="demo-0001"), Context())
    initial = (first.root / result).read_bytes()
    first.close()
    second = DownloadSandbox(first.root, generate_demo_artworks(3))
    try:
        again = second.run(SimpleNamespace(kind="demo", target_id="demo-0001"), Context())
        assert result != again
        assert (second.root / result).read_bytes() == initial
        assert len(list(second.outputs.rglob("receipt.json"))) == 2
    finally:
        second.close()


def test_closed_executor_and_unknown_target_do_not_write(tmp_path):
    sandbox = DownloadSandbox(tmp_path / "managed", generate_demo_artworks(3))
    with pytest.raises(DownloadSandboxError):
        sandbox.run(SimpleNamespace(kind="demo", target_id="../private"), Context())
    sandbox.close()
    with pytest.raises(DownloadSandboxError):
        sandbox.run(SimpleNamespace(kind="demo", target_id="demo-0001"), Context())
    assert not list(sandbox.outputs.iterdir())


def test_receipt_reverification_detects_tampered_saved_page(tmp_path):
    sandbox = DownloadSandbox(tmp_path / "managed", generate_demo_artworks(3))
    try:
        receipt = sandbox.run(SimpleNamespace(kind="demo", target_id="demo-0001"), Context())
        sandbox.verify_receipt(receipt)
        (sandbox.root / receipt).with_name("demo-0001_p1.png").write_bytes(b"altered")
        with pytest.raises(DownloadSandboxError, match="verification failed"):
            sandbox.verify_receipt(receipt)
        with pytest.raises(DownloadSandboxError):
            sandbox.verify_receipt("../outside/receipt.json")
    finally:
        sandbox.close()


def test_close_at_last_checkpoint_does_not_publish_receipt(tmp_path):
    sandbox = DownloadSandbox(tmp_path / "managed", generate_demo_artworks(1))
    class CloseContext(Context):
        def checkpoint(self):
            super().checkpoint()
            if self.checks == 3:
                sandbox.close()
    with pytest.raises(DownloadSandboxError, match="closed"):
        sandbox.run(SimpleNamespace(kind="demo", target_id="demo-0001"), CloseContext())
    assert not list(sandbox.outputs.rglob("receipt.json"))


def test_hardlinked_receipt_is_rejected(tmp_path):
    sandbox = DownloadSandbox(tmp_path / "managed", generate_demo_artworks(1))
    try:
        relative = sandbox.run(SimpleNamespace(kind="demo", target_id="demo-0001"), Context())
        receipt = sandbox.root / relative
        linked = tmp_path / "receipt-copy.json"
        linked.hardlink_to(receipt)
        with pytest.raises(DownloadSandboxError, match="receipt rejected"):
            sandbox.verify_receipt(relative)
    finally:
        sandbox.close()
