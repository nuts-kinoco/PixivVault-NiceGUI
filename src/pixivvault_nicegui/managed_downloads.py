"""Isolated, authenticated original Pixiv artwork saving with verified receipts."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
from threading import Lock, RLock
from types import MappingProxyType
from uuid import uuid4

from PIL import Image

from .bookmark_remote_adapter import RemoteBookmark
from .fs_paths import logical_path, native_io_path
from .job_service import JobCancelled


MARKER = ".pixivvault-managed-pixiv-downloads.json"
IDENTITY = {"application": "pixivvault-nicegui", "purpose": "managed-pixiv-originals", "version": 1}
MAX_PAGE_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 28_000_000
MAX_TOTAL = 2 * 1024 * 1024 * 1024
MAX_PAGES = 1000
MAX_RECEIPT_BYTES = 512 * 1024
_ID = re.compile(r"[1-9][0-9]{0,19}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_FILE = re.compile(r"page-([1-9][0-9]{0,3})\.(png|jpg|webp)\Z")


class ManagedDownloadError(ValueError):
    """Fixed public error text; never includes URLs or downloaded metadata."""


class ManagedPageSelectionChanged(ManagedDownloadError):
    """The pinned selection no longer belongs to the current visible state."""


class ManagedPageQuarantined(ManagedPageSelectionChanged):
    """A verified current exclusion intentionally hides this original page."""


@dataclass(frozen=True)
class ManagedReceiptSnapshot:
    receipts: tuple
    source_pages: object
    work_tokens: object
    revision: str


@dataclass(frozen=True)
class ManagedPageIdentity:
    work_id: str
    source_page_no: int
    source_page_count: int
    author_id: str
    relative_receipt: str
    receipt_sha256: str
    work_state_token: str


def _safe_path(path: Path, *, file: bool = False) -> None:
    # Validate the same spelling used by filesystem I/O. Long native paths
    # still traverse every parent through lstat and retain the reparse fence.
    path = native_io_path(path)
    for item in (path, *path.parents):
        if item.is_symlink():
            raise ManagedDownloadError("managed storage link rejected")
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if getattr(info, "st_file_attributes", 0) & 0x400:
            raise ManagedDownloadError("managed storage reparse point rejected")
        if item == path and file:
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ManagedDownloadError("managed file rejected")
        elif not stat.S_ISDIR(info.st_mode):
            raise ManagedDownloadError("managed directory rejected")


def _write(path: Path, data: bytes) -> None:
    path = native_io_path(path)
    _safe_path(path.parent)
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _read_bounded(path: Path, limit: int) -> bytes:
    path = native_io_path(path)
    _safe_path(path, file=True)
    with path.open('rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
            raise ManagedDownloadError('managed file rejected')
        data = stream.read(limit + 1)
        if len(data) > limit:
            raise ManagedDownloadError('managed file rejected')
        return data


def _json_unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def _fsync_dir(path: Path) -> None:
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _text(value: object, maximum: int, *, empty: bool = True) -> str:
    if (type(value) is not str or len(value) > maximum or (not empty and not value)
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ManagedDownloadError("artwork metadata rejected")
    return value


def _record(value: object, work_id: str) -> RemoteBookmark:
    if not isinstance(value, RemoteBookmark) or value.remote_id != work_id or value.kind != "illustration":
        raise ManagedDownloadError("artwork record rejected")
    if (not _ID.fullmatch(value.author_id) or type(value.illustration_type) is not int
            or value.illustration_type not in (0, 1)
            or type(value.page_count) is not int or not 1 <= value.page_count <= MAX_PAGES
            or value.page_count_known is not True or type(value.x_restrict) is not int
            or value.x_restrict not in (0, 1, 2)):
        raise ManagedDownloadError("artwork record rejected")
    _text(value.title, 256)
    _text(value.author_name, 256)
    _text(value.created_at, 128)
    if type(value.tags) is not tuple or len(value.tags) > 100:
        raise ManagedDownloadError("artwork metadata rejected")
    for tag in value.tags:
        _text(tag, 128)
    return value


def _image(data: bytes) -> tuple[str, str, int, int]:
    if type(data) is not bytes or not 0 < len(data) <= MAX_PAGE_BYTES:
        raise ManagedDownloadError("image size rejected")
    try:
        with Image.open(io.BytesIO(data)) as image:
            kind = image.format
            width, height = image.size
            if (kind not in {"PNG", "JPEG", "WEBP"} or getattr(image, "n_frames", 1) != 1
                    or not 0 < width * height <= MAX_PIXELS):
                raise ManagedDownloadError("image format rejected")
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            if image.size != (width, height):
                raise ManagedDownloadError("image format rejected")
    except (OSError, ValueError, SyntaxError, OverflowError, Image.DecompressionBombError):
        raise ManagedDownloadError("image format rejected") from None
    return {"PNG": ("png", "png"), "JPEG": ("jpg", "jpeg"), "WEBP": ("webp", "webp")}[kind] + (width, height)


class ManagedPixivDownloads:
    def __init__(self, root: Path, client, *, resolve_record):
        self.root = Path(root)
        if not self.root.is_absolute() or self.root.parent == self.root:
            raise ManagedDownloadError("managed root must be an absolute subdirectory")
        _safe_path(self.root)
        self.root = logical_path(native_io_path(self.root).resolve())
        self.client = client
        if not callable(resolve_record):
            raise ManagedDownloadError("artwork resolver required")
        self.resolve_record = resolve_record
        self.closed = False
        self._writer = Lock()
        self._page_writer = Lock()
        self._work_locks_guard = Lock()
        self._work_locks: dict[str, RLock] = {}
        marker = native_io_path(self.root / MARKER)
        if native_io_path(self.root).exists():
            if tuple(native_io_path(self.root).iterdir()) and not marker.is_file():
                raise ManagedDownloadError("foreign managed root rejected")
        else:
            native_io_path(self.root).mkdir()
        _safe_path(self.root)
        if marker.exists():
            _safe_path(marker, file=True)
            try:
                if json.loads(_read_bounded(marker, 4096), object_pairs_hook=_json_unique) != IDENTITY:
                    raise ManagedDownloadError("managed marker rejected")
            except (OSError, UnicodeError, ValueError, RecursionError):
                raise ManagedDownloadError("managed marker rejected") from None
        else:
            _write(marker, json.dumps(IDENTITY, sort_keys=True).encode("utf-8"))
        self.outputs = self.root / "outputs"
        native_io_path(self.outputs).mkdir(exist_ok=True)
        _safe_path(self.outputs)
        self.incomplete = self.root / 'incomplete'
        native_io_path(self.incomplete).mkdir(exist_ok=True)
        _safe_path(self.incomplete)
        # v1 outputs remain immutable forever. v2 generations have their own
        # namespace, so an interrupted publication can never masquerade as a
        # second active receipt or break old reader start-up.
        self.generation_outputs = self.root / 'generations'
        native_io_path(self.generation_outputs).mkdir(exist_ok=True)
        _safe_path(self.generation_outputs)
        from .managed_generations import ManagedGenerations
        self._generations = ManagedGenerations(self.root)
        from .managed_quarantine import ManagedQuarantine
        self.quarantine = ManagedQuarantine(self)

    def work_lock(self, work_id: str) -> RLock:
        """Shared per-work lock for acceptance and active-generation publication."""
        if type(work_id) is not str or not _ID.fullmatch(work_id):
            raise ManagedDownloadError('managed work lock identity rejected')
        with self._work_locks_guard:
            lock = self._work_locks.get(work_id)
            if lock is None:
                lock = RLock()
                self._work_locks[work_id] = lock
            return lock

    @property
    def storage_path(self) -> Path:
        return self.root / "jobs.sqlite3"

    def close(self) -> None:
        self.closed = True

    def _receipt(self, relative: str, *, verify_pages: bool = True):
        pieces = relative.split("/")
        if (len(pieces) != 3 or pieces[0] not in ("outputs", "generations")
                or pieces[2] != "receipt.json"
                or not re.fullmatch(r"work-[1-9][0-9]{0,19}-[0-9a-f]{32}", pieces[1])):
            raise ManagedDownloadError("receipt location rejected")
        folder = native_io_path(self.root / pieces[0] / pieces[1])
        _safe_path(folder)
        path = folder / "receipt.json"
        _safe_path(path, file=True)
        try:
            raw = _read_bounded(path, MAX_RECEIPT_BYTES)
            if not 0 < len(raw) <= MAX_RECEIPT_BYTES:
                raise ValueError()
            receipt = json.loads(raw, object_pairs_hook=_json_unique)
            if set(receipt) != {'version', 'source', 'work_id', 'author_id', 'author_name',
                                'title', 'tags', 'created_at', 'saved_at', 'illustration_type',
                                'x_restrict', 'page_count', 'files'}:
                raise ValueError()
            entries = receipt["files"]
            if (type(receipt) is not dict or type(receipt.get("version")) is not int
                    or receipt.get("version") != 1
                    or receipt.get("source") != "pixiv-original"
                    or receipt.get("work_id") != pieces[1].split("-")[1]
                    or type(receipt.get("page_count")) is not int
                    or not 1 <= receipt["page_count"] <= MAX_PAGES
                    or type(entries) is not list or len(entries) != receipt["page_count"]
                    or type(receipt['author_id']) is not str or not _ID.fullmatch(receipt["author_id"])
                    or type(receipt["illustration_type"]) is not int
                    or receipt["illustration_type"] not in (0, 1)
                    or type(receipt["x_restrict"]) is not int or receipt["x_restrict"] not in (0, 1, 2)
                    or type(receipt["tags"]) is not list or len(receipt["tags"]) > 100):
                raise ValueError()
            for key, limit in (("title", 256), ("author_name", 256), ("created_at", 128), ("saved_at", 64)):
                _text(receipt[key], limit)
            for tag in receipt["tags"]:
                _text(tag, 128)
            if datetime.fromisoformat(receipt["saved_at"]).tzinfo is None:
                raise ValueError()
            # Only a v4 move's exact receipt/page proof may replace a missing
            # physical source. Other missing pages still fail the receipt.
            relocated = None
            expected_names = {"receipt.json"}
            for index, entry in enumerate(entries, 1):
                if type(entry) is not dict or set(entry) != {"name", "bytes", "sha256", "format", "width", "height"}:
                    raise ValueError()
                name = entry["name"]
                match = _FILE.fullmatch(name)
                if (match is None or int(match[1]) != index
                        or type(entry['format']) is not str or entry["format"] not in ("png", "jpeg", "webp")
                        or match[2] != {"png": "png", "jpeg": "jpg", "webp": "webp"}[entry["format"]]
                        or type(entry["bytes"]) is not int or not 0 < entry["bytes"] <= MAX_PAGE_BYTES
                        or type(entry["width"]) is not int or type(entry["height"]) is not int
                        or not 0 < entry["width"] * entry["height"] <= MAX_PIXELS
                        or not isinstance(entry["sha256"], str) or not _SHA.fullmatch(entry["sha256"])):
                    raise ValueError()
                item = folder / name
                expected_names.add(name)
                _safe_path(item, file=True)
                exists = native_io_path(item).exists()
                if not exists:
                    if relocated is None:
                        relocated = self._generations.relocation_proofs()
                    proof = relocated.get((relative, index - 1))
                    if proof is None or any(proof['item'][key] != entry[key]
                                            for key in ('sha256', 'bytes', 'format', 'width', 'height')):
                        raise ValueError()
                if verify_pages:
                    data = (_read_bounded(item, MAX_PAGE_BYTES) if exists else
                            _read_bounded(proof['path'], MAX_PAGE_BYTES))
                    if (len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]
                            or _image(data)[1:] != (entry["format"], entry["width"], entry["height"])):
                        raise ValueError()
            if sum(entry["bytes"] for entry in entries) > MAX_TOTAL:
                raise ValueError()
            actual_names = {item.name for item in folder.iterdir()}
            if actual_names - expected_names or (expected_names - actual_names and relocated is None):
                raise ValueError()
            if not (expected_names - actual_names).issubset({
                    entries[number]["name"] for path, number in (relocated or {})
                    if path == relative}):
                # This also catches an unexpected vanished page even when
                # another page has a valid relocation proof.
                raise ValueError()
            return receipt, hashlib.sha256(raw).hexdigest()
        except (OSError, KeyError, TypeError, ValueError, OverflowError, RecursionError):
            raise ManagedDownloadError("saved receipt verification failed") from None

    def _legacy_receipts(self) -> tuple[tuple[str, dict, str], ...]:
        if self.closed:
            raise ManagedDownloadError('managed download is closed')
        _safe_path(self.outputs)
        found = []
        seen = set()
        folders = []
        for folder in native_io_path(self.outputs).iterdir():
            if len(folders) >= 10000:
                raise ManagedDownloadError('saved receipt limit exceeded')
            folders.append(folder)
        for folder in sorted(folders):
            if not re.fullmatch(r'work-[1-9][0-9]{0,19}-[0-9a-f]{32}', folder.name):
                raise ManagedDownloadError('completed storage structure rejected')
            _safe_path(folder)
            relative = f"outputs/{folder.name}/receipt.json"
            receipt, digest = self._receipt(relative)
            if receipt['work_id'] in seen:
                raise ManagedDownloadError('duplicate completed artwork rejected')
            seen.add(receipt['work_id'])
            found.append((relative, receipt, digest))
        return tuple(found)

    def saved_receipts_with_dates(self, *, verify_pages=True) -> tuple[tuple[str, dict, str, str | None], ...]:
        """Fully verified active receipts with dates from the same index read.

        The generation index can select a legacy v1 output or a newer
        completed generation. Every selected receipt and all pages are verified
        before entering a published catalog. Unpointed generations are inert.
        """
        if self.closed:
            raise ManagedDownloadError('managed download is closed')
        active = self._generations.entries()
        if len(active) > 10000:
            raise ManagedDownloadError('saved receipt limit exceeded')
        result = []
        for wid, entry in sorted(active.items()):
            if type(entry) is not dict or wid != entry.get('work_id'):
                raise ManagedDownloadError('active receipt index rejected')
            relative = entry.get('relative_receipt')
            receipt, digest = self._receipt(relative, verify_pages=verify_pages)
            if receipt['work_id'] != wid or digest != entry.get('sha256'):
                raise ManagedDownloadError('active receipt verification failed')
            value = entry.get('update_date')
            date = value if type(value) is str and value else None
            result.append((relative, receipt, digest, date))
        return tuple(result)

    def verified_saved_receipt(self, work_id: str, *, verify_pages: bool = True) -> tuple[str, dict, str, str | None, str | None] | None:
        """Verify one active receipt for scheduler reconciliation without a full rescan."""
        if (self.closed or type(work_id) is not str or not _ID.fullmatch(work_id)
                or type(verify_pages) is not bool):
            raise ManagedDownloadError('managed receipt lookup rejected')
        entry = self._generations.current(work_id)
        if entry is None:
            return None
        relative = entry.get('relative_receipt')
        receipt, digest = self._receipt(relative, verify_pages=verify_pages)
        if receipt['work_id'] != work_id or digest != entry.get('sha256'):
            raise ManagedDownloadError('active receipt verification failed')
        value = entry.get('update_date')
        date = value if type(value) is str and value else None
        fingerprint = entry.get('accepted_fingerprint')
        if fingerprint is not None and (type(fingerprint) is not str or not re.fullmatch(r'[a-f0-9]{64}', fingerprint)):
            raise ManagedDownloadError('active receipt fingerprint rejected')
        return relative, receipt, digest, date, fingerprint

    def saved_receipts(self, *, verify_pages=True) -> tuple[tuple[str, dict, str], ...]:
        """Compatibility projection; v1 catalogs keep their original shape."""
        return tuple((relative, receipt, digest)
                     for relative, receipt, digest, _ in self.saved_receipts_with_dates(verify_pages=verify_pages))

    def catalog_snapshot(self, *, verify_pages=True) -> ManagedReceiptSnapshot:
        """Pin verified receipts and exclusions, tolerating a bounded commit race.

        Only a changed index revision repeats this local observation. Receipt,
        image, manifest or index corruption still raises immediately. No GET or
        mutation is retried, and sustained changes remain a failed snapshot.
        """
        for _ in range(3):
            if self.closed:
                raise ManagedDownloadError('managed catalog snapshot changed')
            before = self._generations.state_snapshot()
            rows = self.saved_receipts(verify_pages=verify_pages)
            after = self._generations.state_snapshot()
            if self.closed:
                raise ManagedDownloadError('managed catalog snapshot changed')
            if before['revision'] != after['revision']:
                continue
            pages = {
                receipt['work_id']: tuple(
                    number for number in range(receipt['page_count'])
                    if str(number) not in before['exclusions'].get(receipt['work_id'], {}))
                for _, receipt, _ in rows
            }
            tokens = {wid: self._generations._work_token(before, wid) for wid in pages}
            return ManagedReceiptSnapshot(
                deepcopy(rows), MappingProxyType(pages), MappingProxyType(tokens),
                before['revision'])
        raise ManagedDownloadError('managed catalog snapshot changed')

    def visible_source_pages(self, work_id: str) -> tuple[int, ...]:
        if self.closed or type(work_id) is not str or not _ID.fullmatch(work_id):
            raise ManagedDownloadError('managed page identity rejected')
        state = self._generations.state_snapshot()
        current = state['entries'].get(work_id)
        if current is None:
            return ()
        receipt, digest = self._receipt(current['relative_receipt'], verify_pages=False)
        if digest != current['sha256']:
            raise ManagedDownloadError('managed page snapshot changed')
        excluded = state['exclusions'].get(work_id, {})
        return tuple(page for page in range(receipt['page_count'])
                     if str(page) not in excluded)

    def assert_page_available(self, work_id: str, page_no: int, *,
                              expected_work_state_token: str | None = None) -> str:
        if (self.closed or type(work_id) is not str or not _ID.fullmatch(work_id)
                or type(page_no) is not int or not 0 <= page_no < MAX_PAGES):
            raise ManagedDownloadError('managed page identity rejected')
        state = self._generations.state_snapshot()
        if str(page_no) in state['exclusions'].get(work_id, {}):
            raise ManagedPageQuarantined('managed page is quarantined')
        token = self._generations._work_token(state, work_id)
        if expected_work_state_token is not None and token != expected_work_state_token:
            raise ManagedPageSelectionChanged('managed page snapshot changed')
        return token

    def assert_page_visible(self, work_id: str, page_no: int) -> None:
        if self.closed:
            raise ManagedDownloadError('managed download is closed')
        self._generations.assert_page_visible(work_id, page_no)

    def save_pinned_page(self, identity: ManagedPageIdentity):
        """Save the exact original page represented by a pinned local viewer."""
        from .managed_viewer_page import SavedViewerPage, selection
        if not isinstance(identity, ManagedPageIdentity):
            raise ManagedDownloadError('pinned page identity rejected')
        selection(identity.work_id, identity.source_page_no,
                  identity.source_page_count, identity.author_id)
        with self._page_writer, self.work_lock(identity.work_id):
            self.assert_page_available(
                identity.work_id, identity.source_page_no,
                expected_work_state_token=identity.work_state_token)
            receipt, digest = self._receipt(identity.relative_receipt, verify_pages=False)
            if (digest != identity.receipt_sha256
                    or receipt['work_id'] != identity.work_id
                    or receipt['author_id'] != identity.author_id
                    or receipt['page_count'] != identity.source_page_count):
                raise ManagedDownloadError('pinned page proof changed')
            data = self.read_saved_page(identity.relative_receipt,
                                        identity.source_page_no, digest)
            entry = receipt['files'][identity.source_page_no]
            if _image(data)[1:] != (entry['format'], entry['width'], entry['height']):
                raise ManagedDownloadError('pinned page image changed')
            self.assert_page_available(
                identity.work_id, identity.source_page_no,
                expected_work_state_token=identity.work_state_token)
            return SavedViewerPage(
                identity.work_id, identity.source_page_no, identity.source_page_count,
                identity.relative_receipt, digest,
                identity.relative_receipt.rsplit('/', 1)[0] + '/' + entry['name'], True)

    def saved_update_date(self, work_id: str) -> str | None:
        """Legacy v1 receipts cannot provide a trustworthy remote timestamp."""
        if self.closed or type(work_id) is not str or not _ID.fullmatch(work_id):
            raise ManagedDownloadError('saved date request rejected')
        entry = self._generations.current(work_id)
        if entry is None:
            return None
        value = entry.get('update_date')
        return value if type(value) is str and value else None

    def measure_active_receipt_bytes(self, relative_receipt: str, expected_receipt_sha256: str) -> tuple[str, int]:
        """Measure verified active receipt and image bytes, without accepting a path from UI."""
        if (self.closed or type(relative_receipt) is not str or type(expected_receipt_sha256) is not str
                or not _SHA.fullmatch(expected_receipt_sha256)):
            raise ManagedDownloadError('capacity request rejected')
        receipt, digest = self._receipt(relative_receipt, verify_pages=False)
        if digest != expected_receipt_sha256:
            raise ManagedDownloadError('capacity receipt changed')
        work_id = receipt['work_id']
        before = self._generations.current(work_id)
        if before is None or before.get('relative_receipt') != relative_receipt or before.get('sha256') != digest:
            raise ManagedDownloadError('capacity active generation changed')
        raw = _read_bounded(self.root / relative_receipt, MAX_RECEIPT_BYTES)
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ManagedDownloadError('capacity receipt changed')
        total = len(raw)
        for number, entry in enumerate(receipt['files']):
            # Physical capacity still includes immutable recovery originals.
            data = self._read_saved_page_proof(relative_receipt, number, digest)
            if _image(data)[1:] != (entry['format'], entry['width'], entry['height']):
                raise ManagedDownloadError('capacity image rejected')
            total += len(data)
        if self.closed or self._generations.current(work_id) != before:
            raise ManagedDownloadError('capacity active generation changed')
        return receipt['author_id'], total

    def read_saved_page(self, relative_receipt: str, page_no: int, expected_receipt_sha256: str) -> bytes:
        receipt, digest = self._receipt(relative_receipt, verify_pages=False)
        if digest != expected_receipt_sha256:
            raise ManagedDownloadError('saved page snapshot changed')
        self.assert_page_visible(receipt['work_id'], page_no)
        data = self._read_saved_page_proof(relative_receipt, page_no, expected_receipt_sha256)
        self.assert_page_visible(receipt['work_id'], page_no)
        return data

    def _read_saved_page_proof(self, relative_receipt: str, page_no: int,
                               expected_receipt_sha256: str) -> bytes:
        """Internal immutable-byte verification, including recovery originals."""
        if self.closed:
            raise ManagedDownloadError('managed download is closed')
        if (type(page_no) is not int or type(expected_receipt_sha256) is not str
                or not _SHA.fullmatch(expected_receipt_sha256)):
            raise ManagedDownloadError("saved page request rejected")
        # The full receipt set is checked at publication / library refresh.
        # For a pinned image request, re-check its manifest and requested bytes
        # only, rather than decoding every page for every thumbnail (O(n²)).
        receipt, digest = self._receipt(relative_receipt, verify_pages=False)
        if digest != expected_receipt_sha256 or not 0 <= page_no < receipt["page_count"]:
            raise ManagedDownloadError("saved page snapshot changed")
        folder = self.root / relative_receipt.rsplit("/", 1)[0]
        path = folder / receipt["files"][page_no]["name"]
        _safe_path(path, file=True)
        if native_io_path(path).exists():
            data = _read_bounded(path, MAX_PAGE_BYTES)
        else:
            proof = self._generations.relocation_proofs().get((relative_receipt, page_no))
            entry = receipt["files"][page_no]
            if proof is None or any(proof['item'][field] != entry[field]
                                    for field in ('sha256', 'bytes', 'format', 'width', 'height')):
                raise ManagedDownloadError('saved page changed')
            data = _read_bounded(proof['path'], MAX_PAGE_BYTES)
        if (len(data) != receipt["files"][page_no]["bytes"] or
                hashlib.sha256(data).hexdigest() != receipt["files"][page_no]["sha256"]):
            raise ManagedDownloadError("saved page changed")
        return data

    def save_viewer_page(self, work_id, page_no, page_count, author_id, load_record):
        from .managed_viewer_page import save_page
        try:
            return save_page(self, work_id, page_no, page_count, author_id, load_record)
        except ManagedDownloadError:
            raise
        except Exception:
            raise ManagedDownloadError('viewer page save failed') from None

    def run(self, job, context, *, intent=None) -> str:
        return self._run_locked(job, context, intent=intent)

    def _run_locked(self, job, context, *, intent=None) -> str:
        if not self._writer.acquire(blocking=False):
            raise ManagedDownloadError("managed writer already active")
        try:
            if self.closed or job.kind != "pixiv-illust" or type(job.target_id) is not str or not _ID.fullmatch(job.target_id):
                raise ManagedDownloadError("managed job rejected")
            context.checkpoint()
            record = _record(self.resolve_record(job.target_id), job.target_id)
            _safe_path(self.outputs)
            from .managed_download_requests import ManagedAuthorIntent
            from .managed_download_requests import accepted_author_fingerprint
            if intent is not None and (not isinstance(intent, ManagedAuthorIntent)
                    or intent.record != record or intent.work_id != job.target_id):
                raise ManagedDownloadError('accepted author intent changed')
            fingerprint = (accepted_author_fingerprint(intent, getattr(job, 'job_id', None))
                           if intent is not None else None)
            # Only this work's active receipt determines idempotency/CAS.
            # Scanning+decoding every previously saved image for *each* work
            # in an author batch was quadratic in stored image bytes. Full
            # catalog validation still occurs at startup and library refresh.
            # Read and validate the expected active generation atomically with
            # acceptance's final receipt check, but do not hold the lock over
            # network/image I/O: queued same-ID requests must still deduplicate.
            with self.work_lock(record.remote_id):
                start_state = self._generations.state_snapshot()
                active_entry = start_state['entries'].get(record.remote_id)
                work_state_token = self._generations._work_token(start_state, record.remote_id)
                excluded_pages = frozenset(int(page) for page in
                                           start_state['exclusions'].get(record.remote_id, {}))
                previous = None
                if active_entry is not None:
                    relative = active_entry['relative_receipt']
                    receipt, digest = self._receipt(relative)
                    if digest != active_entry['sha256'] or receipt['work_id'] != record.remote_id:
                        raise ManagedDownloadError('active receipt verification failed')
                    previous = (relative, receipt, digest)
                    if intent is None:
                        if (receipt['page_count'] != record.page_count or
                                receipt['illustration_type'] != record.illustration_type or
                                receipt['author_id'] != record.author_id):
                            raise ManagedDownloadError('saved artwork metadata changed')
                        context.progress(record.page_count, record.page_count)
                        context.checkpoint()
                        return relative
                if intent is not None:
                    actual_digest = previous[2] if previous is not None else None
                    current_date = active_entry['update_date'] if active_entry else None
                    # Crash after the pointer commit but before the job's SQLite
                    # terminal commit: verify and reuse the durable new generation.
                    if (previous is not None and current_date == intent.update_date
                            and active_entry.get('accepted_fingerprint') == fingerprint
                            and previous[1]['author_id'] == record.author_id
                            and previous[1]['page_count'] == record.page_count
                            and previous[1]['illustration_type'] == record.illustration_type):
                        context.progress(record.page_count, record.page_count)
                        context.checkpoint()
                        return previous[0]
                    if (actual_digest != intent.expected_receipt_sha256
                            or (previous[0] if previous is not None else None) != intent.expected_relative_receipt
                            or intent.mode == 'new' and previous is not None
                            or intent.mode == 'updated' and previous is None):
                        raise ManagedDownloadError('author download generation changed')
            self.client.verify_identity()
            context.checkpoint()
            if intent is not None:
                current = self.client.get_artwork_update_revision(record.remote_id, record.author_id)
                if (current['author_id'] != record.author_id
                        or current['page_count'] != record.page_count
                        or current['illustration_type'] != record.illustration_type
                        or current['update_date'] != intent.update_date):
                    raise ManagedDownloadError('author metadata changed since selection')
                context.checkpoint()
            urls = self.client.get_artwork_original_pages(job.target_id)
            context.checkpoint()
            if (type(urls) not in (list, tuple) or len(urls) != record.page_count
                    or any(type(url) is not str or len(url) > 4096 for url in urls)
                    or len(set(urls)) != len(urls)):
                raise ManagedDownloadError("original page list rejected")
            folder = native_io_path(self.incomplete) / ("partial-" + uuid4().hex)
            folder.mkdir()
            files = []
            total_bytes = 0
            context.progress(0, len(urls))
            for index, url in enumerate(urls, 1):
                context.checkpoint()
                if index - 1 in excluded_pages:
                    if previous is None or index > previous[1]['page_count']:
                        raise ManagedDownloadError('quarantine recovery proof unavailable')
                    data = self._read_saved_page_proof(previous[0], index - 1, previous[2])
                else:
                    try:
                        data = self.client.get_image_bytes(url)
                    except Exception:
                        context.checkpoint()
                        raise
                context.checkpoint()
                ext, fmt, width, height = _image(data)
                total_bytes += len(data)
                if total_bytes > MAX_TOTAL:
                    raise ManagedDownloadError("managed download size exceeded")
                name = f"page-{index}.{ext}"
                _write(folder / name, data)
                files.append({"name": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                              "format": fmt, "width": width, "height": height})
                context.progress(index, len(urls))
            receipt = {"version": 1, "source": "pixiv-original", "work_id": record.remote_id,
                       "author_id": record.author_id, "author_name": record.author_name,
                       "title": record.title, "tags": list(record.tags), "created_at": record.created_at,
                       "saved_at": datetime.now(timezone.utc).isoformat(), "illustration_type": record.illustration_type,
                       "x_restrict": record.x_restrict, "page_count": len(files), "files": files}
            _write(folder / "receipt.json", json.dumps(receipt, ensure_ascii=False, sort_keys=True).encode("utf-8"))
            _fsync_dir(folder)
            context.checkpoint()
            destination = native_io_path(self.generation_outputs if intent is not None else self.outputs)
            final = destination / f"work-{record.remote_id}-{uuid4().hex}"
            # Verify the staged bytes before they enter the completed namespace.
            for entry in files:
                staged = _read_bounded(folder / entry['name'], MAX_PAGE_BYTES)
                if hashlib.sha256(staged).hexdigest() != entry['sha256']:
                    raise ManagedDownloadError('staged image verification failed')
            context.checkpoint()
            if intent is not None:
                # Pre-publish revision gate while bytes remain in incomplete/.
                # A normal revision drift must not create an immutable orphan
                # in generations/ and steadily exhaust capacity on retries.
                current = self.client.get_artwork_update_revision(
                    record.remote_id, record.author_id)
                if (current['author_id'] != record.author_id
                        or current['page_count'] != record.page_count
                        or current['illustration_type'] != record.illustration_type
                        or current['update_date'] != intent.update_date):
                    raise ManagedDownloadError('author metadata changed during download')
                context.checkpoint()
            with self.work_lock(record.remote_id):
                if self._generations.work_state_token(record.remote_id) != work_state_token:
                    raise ManagedDownloadError('generation compare-and-swap rejected')
                if intent is not None:
                    self._generations.prepare_for_publication()
                folder.rename(final)
                _fsync_dir(destination)
                relative = f"{'generations' if intent is not None else 'outputs'}/{final.name}/receipt.json"
                _, digest = self._receipt(relative)
                context.checkpoint()
                if intent is not None:
                    self._generations.publish(record.remote_id, relative, digest,
                        intent.update_date, intent.expected_receipt_sha256,
                        intent.expected_relative_receipt, fingerprint,
                        expected_work_state_token=work_state_token)
            return relative
        except ManagedDownloadError:
            raise
        except JobCancelled:
            raise
        except Exception:
            raise ManagedDownloadError("managed download failed") from None
        finally:
            self._writer.release()
