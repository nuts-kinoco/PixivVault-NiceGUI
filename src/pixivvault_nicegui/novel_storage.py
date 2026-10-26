"""Candidate-only atomic storage for one validated novel.
Uses the existing NovelDocument and deterministic TXT/EPUB exporters. Receipt,
namespace, and quotas below are proposals, not accepted application policy.
"""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib, json, os, re, stat
from pathlib import Path
from threading import Lock
from uuid import uuid4
from .managed_downloads import _read_bounded
from .media_parity_policy import NovelDocument
from .novel_export import render_novel_epub, render_novel_txt

ROOT_MARKER = ".pixivvault-novel-storage.json"
_MARKER = b'{"application":"pixivvault-nicegui","purpose":"isolated-novel-exports","version":1}\n'
_OWNER = ".writer.lock"
_RECEIPT = "receipt.json"
_COMPLETION = "completion.json"
_ID = re.compile(r"[1-9][0-9]{0,19}\Z")
_PATH = re.compile(r"outputs/(novel-[1-9][0-9]{0,19}-[a-f0-9]{32})/receipt\.json\Z")
_FORMATS = frozenset({"TXT", "EPUB", "BOTH"})
_MAX_OUTPUT = 32 * 1024 * 1024
_MAX_RECEIPT = 64 * 1024
_MAX_COMPLETION = 1024
# Candidate-only proposed limits; require contract review.
_MAX_GENERATIONS, _MAX_FILES, _MAX_BYTES = 256, 1024, 256 * 1024 * 1024


class NovelStorageError(ValueError):
    """Fixed diagnostic without paths, novel text, or author metadata."""
    def __init__(self):
        super().__init__("novel storage operation failed")


class NovelStorageIOError(NovelStorageError):
    """Transient filesystem I/O failed; no success receipt was returned."""

class NovelStorageIntegrityError(NovelStorageError):
    """Published or staged bytes failed integrity validation."""

class NovelStoragePublicationUncertain(NovelStorageIOError):
    """Rename succeeded but the directory durability barrier failed."""
    def __init__(self, relative_receipt: str, receipt_sha256: str):
        super().__init__()
        self.relative_receipt = relative_receipt
        self.receipt_sha256 = receipt_sha256

@dataclass(frozen=True, slots=True)
class SavedNovel:
    relative_receipt: str = field(repr=False)
    receipt_sha256: str = field(repr=False)
    work_id: str = field(repr=False)
    output_format: str
    output_names: tuple[str, ...]


class NovelStorageIOError(NovelStorageError):
    """Transient filesystem I/O failed; no success receipt was returned."""

class NovelStorageIntegrityError(NovelStorageError):
    """Published or staged bytes failed integrity validation."""

class NovelStoragePublicationUncertain(NovelStorageIOError):
    """Rename succeeded but the directory durability barrier failed."""
    def __init__(self, relative_receipt: str, receipt_sha256: str):
        super().__init__()
        self.relative_receipt = relative_receipt
        self.receipt_sha256 = receipt_sha256

@dataclass(frozen=True, slots=True)
class ReconciledNovel:
    state: str  # missing or complete; invalid published data raises.
    saved: SavedNovel | None = field(default=None, repr=False)


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=True, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("ascii")


def _safe(path: Path, *, file=False):
    for item in (path, *path.parents):
        if item.is_symlink():
            raise NovelStorageError()
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if getattr(info, "st_file_attributes", 0) & 0x400:
            raise NovelStorageError()
        if item == path and file:
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise NovelStorageError()
        elif not stat.S_ISDIR(info.st_mode):
            raise NovelStorageError()


def _write_new(path: Path, data: bytes):
    _safe(path.parent)
    _safe(path, file=True)
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _fsync_dir(path: Path):
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _lock(fd: int):
    os.lseek(fd, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX)
    if os.fstat(fd).st_size == 0:
        os.lseek(fd, 0, os.SEEK_SET)
        if os.write(fd, b"0") != 1:
            raise NovelStorageError()
        os.fsync(fd)
    os.lseek(fd, 0, os.SEEK_SET)
    if os.read(fd, 1) != b"0" or os.fstat(fd).st_size != 1:
        raise NovelStorageError()


def _unlock(fd: int):
    os.lseek(fd, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)


class NovelExportStore:
    """Atomic TXT/EPUB/BOTH store under a separate explicitly owned root."""

    def __init__(self, root: Path):
        self.root = Path(root)
        if not self.root.is_absolute() or self.root.parent == self.root:
            raise NovelStorageError()
        _safe(self.root)
        self._thread_lock = Lock()
        try:
            if not self.root.exists():
                self.root.mkdir()
                _fsync_dir(self.root.parent)
                # Publish the ownership marker atomically. A crash before the
                # replace leaves a recognized temp marker; never delete it.
                marker_tmp = self.root / ".init-marker.tmp"
                _write_new(marker_tmp, _MARKER)
                os.replace(marker_tmp, self.root / ROOT_MARKER)
                _fsync_dir(self.root)
            else:
                _safe(self.root)
                marker = self.root / ROOT_MARKER
                marker_tmp = self.root / ".init-marker.tmp"
                if marker.exists():
                    if _read_bounded(marker, 4096) != _MARKER:
                        raise NovelStorageError()
                    # A leftover temp marker is only recoverable if byte-identical.
                    if marker_tmp.exists():
                        if _read_bounded(marker_tmp, 4096) != _MARKER:
                            raise NovelStorageError()
                        marker_tmp.unlink()
                elif marker_tmp.exists():
                    # Even a byte-perfect temp marker is not ownership proof
                    # when unrelated entries coexist in this root.
                    with os.scandir(self.root) as entries:
                        names = {entry.name for entry in entries}
                    if names != {".init-marker.tmp"}:
                        raise NovelStorageError()
                    temp_bytes = _read_bounded(marker_tmp, 4096)
                    if temp_bytes != _MARKER:
                        # Resume only a recognizable non-empty prefix of our
                        # fixed marker, and never adopt arbitrary bytes.
                        if not temp_bytes or not _MARKER.startswith(temp_bytes):
                            raise NovelStorageError()
                        with marker_tmp.open("ab") as stream:
                            stream.write(_MARKER[len(temp_bytes):])
                            stream.flush()
                            os.fsync(stream.fileno())
                        if _read_bounded(marker_tmp, 4096) != _MARKER:
                            raise NovelStorageError()
                    os.replace(marker_tmp, marker)
                    _fsync_dir(self.root)
                else:
                    # An empty caller-designated root is safe to initialize;
                    # any unknown entry without ownership proof is preserved/refused.
                    with os.scandir(self.root) as entries:
                        if next(entries, None) is not None:
                            raise NovelStorageError()
                    _write_new(marker_tmp, _MARKER)
                    os.replace(marker_tmp, marker)
                    _fsync_dir(self.root)
            # Once the marker proves ownership, missing namespace directories
            # are safe to recreate after a crash at either mkdir boundary.
            for name in ("outputs", ".staging"):
                child = self.root / name
                if child.exists():
                    _safe(child)
                    if not child.is_dir():
                        raise NovelStorageError()
                else:
                    child.mkdir()
                    _fsync_dir(self.root)
            with self._locked():
                pass
        except NovelStorageError:
            raise
        except OSError:
            raise NovelStorageIOError() from None
        except Exception:
            raise NovelStorageError() from None
    @contextmanager
    def _locked(self):
        fd, acquired = -1, False
        try:
            with self._thread_lock:
                _safe(self.root)
                if _read_bounded(self.root / ROOT_MARKER, 4096) != _MARKER:
                    raise NovelStorageError()
                lock_path = self.root / _OWNER
                _safe(lock_path, file=True)
                flags = os.O_RDWR | os.O_CREAT
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW
                fd = os.open(lock_path, flags, 0o600)
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise NovelStorageError()
                _lock(fd)
                acquired = True
                yield
        except NovelStorageError:
            raise
        except OSError:
            raise NovelStorageIOError() from None
        except Exception:
            raise NovelStorageError() from None
        finally:
            if fd >= 0:
                try:
                    if acquired:
                        _unlock(fd)
                except Exception:
                    pass
                os.close(fd)

    def _usage(self):
        generations = files = size = 0
        for name in ("outputs", ".staging"):
            namespace = self.root / name
            _safe(namespace)
            with os.scandir(namespace) as entries:
                for entry in entries:
                    folder = Path(entry.path)
                    _safe(folder)
                    if (not entry.is_dir(follow_symlinks=False)
                            or re.fullmatch(r"novel-[1-9][0-9]{0,19}-[a-f0-9]{32}", folder.name) is None):
                        raise NovelStorageError()
                    generations += 1
                    with os.scandir(folder) as children:
                        for child in children:
                            path = Path(child.path)
                            allowed = {"novel.txt", "novel.epub", "receipt.json", "completion.json"}
                            if (child.name not in allowed or child.is_symlink()
                                    or child.is_dir(follow_symlinks=False)):
                                raise NovelStorageError()
                            _safe(path, file=True)
                            info = Path(child.path).lstat()
                            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                                raise NovelStorageError()
                            files += 1
                            size += info.st_size
        return generations, files, size

    @staticmethod
    def _payloads(document, author_name, output_format):
        if type(output_format) is not str or output_format not in _FORMATS:
            raise NovelStorageError()
        try:
            if output_format == "TXT":
                result = {"novel.txt": render_novel_txt(document, author_name=author_name)}
            elif output_format == "EPUB":
                result = {"novel.epub": render_novel_epub(document, author_name=author_name)}
            else:
                result = {"novel.txt": render_novel_txt(document, author_name=author_name),
                          "novel.epub": render_novel_epub(document, author_name=author_name)}
        except Exception:
            raise NovelStorageError() from None
        if any(type(data) is not bytes or not data or len(data) > _MAX_OUTPUT
               for data in result.values()):
            raise NovelStorageError()
        return result

    def save(self, document: NovelDocument, *, author_name: str, output_format="TXT"):
        payloads = self._payloads(document, author_name, output_format)
        work_id, author_id = document.work_id, document.author_id
        if (type(work_id) is not str or _ID.fullmatch(work_id) is None
                or type(author_id) is not str or _ID.fullmatch(author_id) is None):
            raise NovelStorageError()
        generation = f"novel-{work_id}-{uuid4().hex}"
        stage = self.root / ".staging" / generation
        final = self.root / "outputs" / generation
        rows = [{"name": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                for name, data in sorted(payloads.items())]
        receipt = _json_bytes({"schema": 1, "kind": "novel-export", "work_id": work_id,
                               "author_id": author_id, "format": output_format, "outputs": rows})
        completion = _json_bytes({"schema": 1, "generation": generation,
                                  "receipt_sha256": hashlib.sha256(receipt).hexdigest()})
        if len(receipt) > _MAX_RECEIPT or len(completion) > _MAX_COMPLETION:
            raise NovelStorageError()
        need_files = len(payloads) + 2
        need_bytes = sum(len(v) for v in payloads.values()) + len(receipt) + len(completion)
        with self._locked():
            generations, files, size = self._usage()
            if (generations + 1 > _MAX_GENERATIONS or files + need_files > _MAX_FILES
                    or size + need_bytes > _MAX_BYTES or final.exists() or stage.exists()):
                raise NovelStorageError()
            stage.mkdir()
            try:
                for name, data in payloads.items():
                    _write_new(stage / name, data)
                _write_new(stage / _RECEIPT, receipt)
                _write_new(stage / _COMPLETION, completion)
                _fsync_dir(stage)
                self._verify(stage, generation, receipt, completion)
                if final.exists():
                    raise NovelStorageError()
                os.rename(stage, final)
                try:
                    _fsync_dir(final.parent)
                except OSError:
                    # The directory is visible; callers must reconcile by receipt,
                    # not blindly repeat save and create another generation.
                    raise NovelStoragePublicationUncertain(
                        f"outputs/{generation}/{_RECEIPT}",
                        hashlib.sha256(receipt).hexdigest(),
                    ) from None
            except NovelStoragePublicationUncertain:
                raise
            except NovelStorageError:
                raise
            except OSError:
                # Preserve failed stages; they remain counted against quotas.
                raise NovelStorageIOError() from None
            except Exception:
                # Preserve failed stages; they remain counted against quotas.
                raise NovelStorageError() from None
        rel = f"outputs/{generation}/{_RECEIPT}"
        return SavedNovel(rel, hashlib.sha256(receipt).hexdigest(), work_id,
                          output_format, tuple(sorted(payloads)))

    @staticmethod
    def _verify(folder, generation, receipt, completion):
        try:
            _safe(folder)
            obj = json.loads(receipt)
            if (type(obj) is not dict
                    or set(obj) != {"schema", "kind", "work_id", "author_id", "format", "outputs"}
                    or type(obj["schema"]) is not int or obj["schema"] != 1
                    or obj["kind"] != "novel-export"
                    or type(obj["work_id"]) is not str or _ID.fullmatch(obj["work_id"]) is None
                    or type(obj["author_id"]) is not str or _ID.fullmatch(obj["author_id"]) is None
                    or type(obj["format"]) is not str or obj["format"] not in _FORMATS
                    or type(obj["outputs"]) is not list):
                raise NovelStorageError()
            generation_match = re.fullmatch(r"novel-([1-9][0-9]{0,19})-([a-f0-9]{32})", generation)
            if generation_match is None or generation_match.group(1) != obj["work_id"]:
                raise NovelStorageError()
            expected = {"TXT": {"novel.txt"}, "EPUB": {"novel.epub"},
                        "BOTH": {"novel.txt", "novel.epub"}}[obj["format"]]
            rows = obj["outputs"]
            if len(rows) != len(expected) or any(
                    type(row) is not dict or set(row) != {"name", "bytes", "sha256"}
                    or type(row["name"]) is not str or row["name"] not in expected
                    or type(row["bytes"]) is not int or not 0 < row["bytes"] <= _MAX_OUTPUT
                    or type(row["sha256"]) is not str
                    or re.fullmatch(r"[a-f0-9]{64}", row["sha256"]) is None
                    for row in rows):
                raise NovelStorageError()
            if {row["name"] for row in rows} != expected:
                raise NovelStorageError()
            with os.scandir(folder) as children:
                names = {c.name for c in children}
            if names != expected | {_RECEIPT, _COMPLETION}:
                raise NovelStorageError()
            for row in rows:
                data = _read_bounded(folder / row["name"], _MAX_OUTPUT)
                if len(data) != row["bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
                    raise NovelStorageError()
            if _read_bounded(folder / _RECEIPT, _MAX_RECEIPT) != receipt:
                raise NovelStorageError()
            if _read_bounded(folder / _COMPLETION, _MAX_COMPLETION) != completion:
                raise NovelStorageError()
            expected_completion = _json_bytes({"schema": 1, "generation": generation,
                                               "receipt_sha256": hashlib.sha256(receipt).hexdigest()})
            if completion != expected_completion:
                raise NovelStorageError()
            return obj
        except NovelStorageIOError:
            raise
        except OSError:
            raise NovelStorageIOError() from None
        except NovelStorageError:
            raise NovelStorageIntegrityError() from None
        except Exception:
            raise NovelStorageIntegrityError() from None

    def reconcile(self, relative_receipt, *, expected_sha256=None):
        match = _PATH.fullmatch(relative_receipt) if type(relative_receipt) is str else None
        if match is None:
            raise NovelStorageError()
        generation = match.group(1)
        folder = self.root / "outputs" / generation
        with self._locked():
            _safe(folder)
            if not folder.exists():
                return ReconciledNovel("missing")
            try:
                receipt = _read_bounded(folder / _RECEIPT, _MAX_RECEIPT)
                digest = hashlib.sha256(receipt).hexdigest()
                if expected_sha256 is not None and digest != expected_sha256:
                    raise NovelStorageIntegrityError()
                completion = _read_bounded(folder / _COMPLETION, _MAX_COMPLETION)
                obj = self._verify(folder, generation, receipt, completion)
                saved = SavedNovel(relative_receipt, digest, obj["work_id"],
                                   obj["format"], tuple(sorted(r["name"] for r in obj["outputs"])))
                return ReconciledNovel("complete", saved)
            except NovelStorageIOError:
                raise
            except FileNotFoundError:
                raise NovelStorageIntegrityError() from None
            except OSError:
                raise NovelStorageIOError() from None
            except NovelStorageError:
                raise
            except Exception:
                raise NovelStorageIntegrityError() from None
