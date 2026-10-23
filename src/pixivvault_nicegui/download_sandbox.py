"""Explicit, isolated PNG-save executor for the migration acceptance UI.

Only synthetic Artwork IDs from the supplied fixture may be saved. The
executor has no account, network, legacy application or production DB access.
It records a receipt only after every page has been flushed and verified.
"""

from __future__ import annotations

from collections.abc import Iterable
import hashlib
import json
import os
from pathlib import Path
import stat
from uuid import uuid4

from .media import DemoImageCatalog
from .models import Artwork


MARKER = ".pixivvault-download-sandbox.json"
MARKER_CONTENT = {"application": "pixivvault-nicegui", "purpose": "synthetic-downloads", "version": 1}
MAX_PAGE_BYTES = 20 * 1024 * 1024


class DownloadSandboxError(ValueError):
    """A fixed-message error that never embeds user file contents."""


def _assert_directory_chain(path: Path) -> None:
    for component in (path, *path.parents):
        if component.is_symlink():
            raise DownloadSandboxError("download sandbox links are not permitted")
        try:
            metadata = component.lstat()
        except FileNotFoundError:
            continue
        if getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise DownloadSandboxError("download sandbox reparse points are not permitted")
        if not stat.S_ISDIR(metadata.st_mode):
            raise DownloadSandboxError("download sandbox parent is not a directory")


def _write_exclusive(path: Path, payload: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


class DownloadSandbox:
    """A managed directory with independent jobs.sqlite3 and receipt folders.

    Unknown nonempty directories and link/junction paths are rejected before
    writing. An existing marker must match exactly; it never authorizes use
    of an arbitrary old application database or library.
    """

    def __init__(self, root: Path, artworks: Iterable[Artwork], *, catalog=None):
        self.root = Path(root)
        if not self.root.is_absolute():
            raise DownloadSandboxError("download sandbox must be an absolute directory")
        _assert_directory_chain(self.root)
        self.root = self.root.resolve()
        if self.root.parent == self.root:
            raise DownloadSandboxError("download sandbox cannot be a filesystem root")
        self.artworks = {art.work_id: art for art in artworks}
        if (not self.artworks or any(not key.startswith("demo-")
                or not key[5:].isascii() or not key[5:].isdecimal()
                for key in self.artworks)):
            raise DownloadSandboxError("download sandbox requires synthetic artwork IDs")
        self._owns_catalog = catalog is None
        self.catalog = catalog or DemoImageCatalog(tuple(self.artworks.values()))
        self.closed = False
        marker = self.root / MARKER
        if self.root.exists():
            entries = tuple(self.root.iterdir())
            if entries and not marker.is_file():
                raise DownloadSandboxError("download sandbox directory is not managed")
        else:
            self.root.mkdir(parents=True, exist_ok=False)
        _assert_directory_chain(self.root)
        if marker.exists() or marker.is_symlink():
            if marker.is_symlink() or marker.stat().st_size > 4096:
                raise DownloadSandboxError("download sandbox marker rejected")
            try:
                content = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, ValueError, UnicodeError):
                raise DownloadSandboxError("download sandbox marker rejected") from None
            if content != MARKER_CONTENT:
                raise DownloadSandboxError("download sandbox marker rejected")
        else:
            _write_exclusive(marker, (json.dumps(MARKER_CONTENT) + "\n").encode("utf-8"))
        self.outputs = self.root / "outputs"
        _assert_directory_chain(self.outputs)
        self.outputs.mkdir(exist_ok=True)

    @property
    def storage_path(self) -> Path:
        return self.root / "jobs.sqlite3"

    def run(self, job, context) -> str:
        if self.closed:
            raise DownloadSandboxError("download sandbox is closed")
        if job.kind != "demo" or job.target_id not in self.artworks:
            raise DownloadSandboxError("download target is not a synthetic artwork")
        context.checkpoint()
        _assert_directory_chain(self.outputs)
        destination = self.outputs / ("run-" + uuid4().hex)
        destination.mkdir(exist_ok=False)
        art = self.artworks[job.target_id]
        entries = []
        context.progress(0, art.page_count)
        for page in range(art.page_count):
            context.checkpoint()
            if self.closed:
                raise DownloadSandboxError("download sandbox is closed")
            blob = self.catalog.get(art.work_id, page, "view")
            if (type(blob.data) is not bytes or not 0 < len(blob.data) <= MAX_PAGE_BYTES
                    or blob.media_type != "image/png" or not blob.data.startswith(b"\x89PNG\r\n\x1a\n")):
                raise DownloadSandboxError("synthetic image payload rejected")
            _assert_directory_chain(destination)
            name = f"{art.work_id}_p{page + 1}.png"
            temporary = destination / (name + ".partial")
            target = destination / name
            _write_exclusive(temporary, blob.data)
            digest = hashlib.sha256(blob.data).hexdigest()
            if hashlib.sha256(temporary.read_bytes()).hexdigest() != digest:
                raise DownloadSandboxError("saved image verification failed")
            # The run directory was created exclusively, so the target must
            # never already exist. No pre-existing library file is replaced.
            if target.exists() or target.is_symlink():
                raise DownloadSandboxError("saved image destination already exists")
            temporary.rename(target)
            entries.append({"name": name, "bytes": len(blob.data), "sha256": digest})
            context.progress(page + 1, art.page_count)
        context.checkpoint()
        if self.closed:
            raise DownloadSandboxError("download sandbox is closed")
        _assert_directory_chain(destination)
        receipt = {"version": 1, "data": "synthetic", "work_id": art.work_id,
                   "page_count": art.page_count, "files": entries}
        _write_exclusive(destination / "receipt.json",
                         (json.dumps(receipt, ensure_ascii=True, indent=2) + "\n").encode("utf-8"))
        relative = (destination / "receipt.json").relative_to(self.root).as_posix()
        self.verify_receipt(relative)
        return relative

    def verify_receipt(self, relative: str) -> None:
        """Re-read the receipt and all pages before the runner reports success."""
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or len(path.parts) != 3 or path.parts[0] != "outputs":
            raise DownloadSandboxError("download receipt path rejected")
        receipt_path = self.root / path
        _assert_directory_chain(receipt_path.parent)
        try:
            receipt_info = receipt_path.lstat()
        except OSError:
            raise DownloadSandboxError("download receipt rejected") from None
        if (receipt_path.name != "receipt.json" or receipt_path.is_symlink()
                or not stat.S_ISREG(receipt_info.st_mode) or receipt_info.st_nlink != 1
                or getattr(receipt_info, "st_file_attributes", 0) & 0x400
                or receipt_info.st_size > 1024 * 1024):
            raise DownloadSandboxError("download receipt rejected")
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            art = self.artworks.get(receipt.get("work_id"))
            entries = receipt["files"]
            if (receipt.get("version") != 1 or receipt.get("data") != "synthetic" or art is None
                    or receipt.get("page_count") != art.page_count or len(entries) != art.page_count):
                raise ValueError()
            for page, entry in enumerate(entries):
                name = f"{art.work_id}_p{page + 1}.png"
                if entry["name"] != name:
                    raise ValueError()
                target = receipt_path.parent / name
                info = target.lstat()
                if (target.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or not 0 < info.st_size <= MAX_PAGE_BYTES
                        or getattr(info, "st_file_attributes", 0) & 0x400):
                    raise ValueError()
                data = target.read_bytes()
                if len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
                    raise ValueError()
        except (AttributeError, TypeError, KeyError, ValueError, OSError):
            raise DownloadSandboxError("download receipt verification failed") from None

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            if self._owns_catalog:
                self.catalog.close()
