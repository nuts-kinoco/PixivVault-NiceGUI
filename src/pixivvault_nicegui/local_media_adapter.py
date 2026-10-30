"""Opt-in, read-only local media from ID-identified legacy folders and ZIPs.

Unidentified legacy filenames are intentionally unsupported; guessing could
display another artwork or a different page as if it were the requested one.
"""

from __future__ import annotations

from collections import defaultdict, OrderedDict
from contextlib import contextmanager
from io import BytesIO
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat
import zlib
from threading import BoundedSemaphore, Lock, RLock, local
from zipfile import ZipFile, BadZipFile

from PIL import Image, UnidentifiedImageError

from .library_models import LibrarySnapshot
from .media import ImageBlob


_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_IMAGE = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
_MAX_FILE = 24 * 1024 * 1024
_MAX_PIXELS = 28_000_000
_MAX_MEMBERS = 100_000
_MAX_RATIO = 200
_MAX_FILES = 250_000
_MAX_STORES = 20_000
_DECODE_SLOTS = BoundedSemaphore(2)
_INVALID_FILENAME = re.compile(r'[\\/:*?"<>|]+')
_PAGE_SUFFIX = re.compile(r'^(.*)_(\d{1,5})$')


class MediaMissing(ValueError):
    pass


class MediaAmbiguous(ValueError):
    pass


class MediaUnavailable(RuntimeError):
    pass


def _safe_file(path: Path, root: Path) -> bool:
    if not path.is_relative_to(root):
        return False
    for node in (path, *path.parents):
        if node == root.parent:
            break
        if node.is_symlink() or (node.exists() and getattr(node.stat(), "st_file_attributes", 0) & 0x400):
            return False
    return True


def _safe_entry(name: str) -> bool:
    # PurePosixPath normalizes away '.', so inspect the original components.
    components = name.rstrip("/").split("/")
    return (bool(name) and not name.startswith("/") and "\\" not in name
            and ":" not in name and not name.endswith("//")
            and all(part not in ("", ".", "..") for part in components))


def _saved_title(title: str) -> str:
    """Match the original application's filename sanitization exactly."""
    return _INVALID_FILENAME.sub("_", title)


class ReadonlyLocalMediaCatalog:
    """Identifies exact ID/page still images only; never writes or extracts ZIPs."""

    def __init__(self, image_root: Path, snapshot: LibrarySnapshot):
        root = Path(image_root).absolute()
        if not root.is_dir() or root.is_symlink():
            raise ValueError("explicit existing non-symlink image root required")
        self.root = root.resolve()
        self.works = {work.work_id: work for work in snapshot.works}
        self._lock = RLock()
        self._index_lock = Lock()
        self._closed = False
        self._locations: dict[tuple[str, int], tuple[tuple[Path, str | None], ...]] = {}
        self._failed_authors: set[str] = set()
        self._ambiguous: set[tuple[str, int]] = set()
        self._presence_uncertain_authors: set[str] = set()
        self._indexed = False
        self._generation = 0
        self._cache: OrderedDict[tuple[str, int, str], ImageBlob] = OrderedDict()
        self._cache_bytes = 0
        self._cache_limit = 32 * 1024 * 1024
        self._shared_cache=None
        self._shared_owner=None
        self._analysis_state = local()

    @contextmanager
    def analysis_checkpoint(self, checkpoint):
        """Optional cancellation for off-UI inference index construction only."""
        previous = getattr(self._analysis_state, 'checkpoint', None)
        def composed():
            if previous is not None:
                previous()
            checkpoint()
        self._analysis_state.checkpoint = composed
        try:
            composed()
            yield
        finally:
            self._analysis_state.checkpoint = previous

    def _analysis_checkpoint(self):
        checkpoint = getattr(self._analysis_state, 'checkpoint', None)
        if checkpoint is not None:
            checkpoint()

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

    @contextmanager
    def _checked_open(self, path: Path):
        """Check the opened file's identity, including after the read.

        Path checks alone leave a window where an entry can be replaced with a
        symlink or junction. Verification against the open handle fails closed.
        """
        if not _safe_file(path, self.root):
            raise MediaUnavailable("media path changed")
        with path.open("rb") as source:
            before = os.fstat(source.fileno())

            def unchanged() -> bool:
                if not _safe_file(path, self.root):
                    return False
                resolved = path.resolve(strict=True)
                if not resolved.is_relative_to(self.root):
                    return False
                current = path.stat()
                opened = os.fstat(source.fileno())
                return ((current.st_dev, current.st_ino, current.st_size,
                         current.st_mtime_ns) ==
                        (opened.st_dev, opened.st_ino, opened.st_size,
                         opened.st_mtime_ns) ==
                        (before.st_dev, before.st_ino, before.st_size,
                         before.st_mtime_ns))

            if not unchanged():
                raise MediaUnavailable("media path changed")
            yield source
            if not unchanged():
                raise MediaUnavailable("media changed during read")

    def _discover_stores(self) -> tuple[dict[str, list[Path]], list[Path]]:
        """Scan each storage parent only once, regardless of author count."""
        stores: dict[str, list[Path]] = defaultdict(list)
        wrappers: list[Path] = []
        count = 0

        def inspect(candidate: Path, *, nested: bool = False) -> None:
            nonlocal count
            self._analysis_checkpoint()
            count += 1
            if count > _MAX_STORES:
                raise MediaUnavailable("too many storage entries")
            if not _safe_file(candidate, self.root):
                return
            if candidate.is_dir() and candidate.name.startswith("☆ブックマーク(") and not nested:
                wrappers.append(candidate)
                return
            if not (candidate.is_dir() or
                    (candidate.is_file() and candidate.suffix.lower() == ".zip")):
                return
            match = re.search(r"\(([A-Za-z0-9_-]{1,128})\)(?:\.zip)?\Z", candidate.name)
            if match:
                stores[match.group(1)].append(candidate)

        for candidate in self.root.iterdir():
            inspect(candidate)
        for wrapper in wrappers:
            for candidate in wrapper.iterdir():
                inspect(candidate, nested=True)
        return stores, wrappers

    @staticmethod
    def _page_for(name: str, work) -> int | None:
        path = PurePosixPath(name)
        if path.suffix.lower() not in _IMAGE:
            return None
        work_id = work.work_id
        displayed_pages = work.page_count
        # Folder form: title(work_id)/<work_id>_p0.jpg (zero-based).
        if len(path.parts) >= 2 and path.parts[-2].endswith(f"({work_id})"):
            match = re.fullmatch(re.escape(work_id) + r"_p(\d+)(?:_.*)?", path.stem)
            if match:
                if len(match.group(1)) > 5:
                    return None
                number = int(match.group(1))
                return number if number < displayed_pages else None
        # Flat current form: title(work_id)_1.jpg (one-based).
        match = re.fullmatch(r".+\(" + re.escape(work_id) + r"\)(?:_(\d+))?", path.stem)
        if match:
            if match.group(1) is not None and len(match.group(1)) > 5:
                return None
            suffix = match.group(1)
            if suffix is None:
                return 0
            if displayed_pages == 1 and work.page_count_known:
                # Only a title literally ending in _N can create a suffixed
                # single-page filename. Never guess from page-count alone.
                title = _saved_title(work.artwork.title)
                number_title = _PAGE_SUFFIX.fullmatch(title)
                if (number_title is not None and
                        path.stem == f"{number_title.group(1)}({work_id})_{number_title.group(2)}"):
                    return 0
            number = int(suffix) - 1
            return number if 0 <= number < displayed_pages else None
        return None

    @staticmethod
    def _legacy_pages(name: str, author_works: dict, unique_titles: dict) -> tuple[tuple[str, int], ...]:
        """Resolve ID-less old names only when no alternative attribution exists."""
        path = PurePosixPath(name)
        if len(path.parts) != 1 or path.suffix.lower() not in _IMAGE:
            return ()
        stem = path.stem
        if re.search(r"\([A-Za-z0-9_-]{1,128}\)(?:_\d+)?$", stem):
            return ()
        matches = set()
        exact = unique_titles.get(stem)
        if exact is not None and exact.page_count == 1 and (
                exact.page_count_known or _PAGE_SUFFIX.fullmatch(stem) is None):
            matches.add((exact.work_id, 0))
        suffix = _PAGE_SUFFIX.fullmatch(stem)
        if suffix is not None:
            work = unique_titles.get(suffix.group(1))
            if work is not None:
                number = int(suffix.group(2)) - 1
                if 0 <= number < work.page_count and (
                        work.page_count_known and work.page_count > 1 or
                        not work.page_count_known and number == 0):
                    matches.add((work.work_id, number))
        return tuple(sorted(matches))

    @staticmethod
    def _id_from_name(name: str) -> str | None:
        path = PurePosixPath(name)
        # An ID on the image itself takes precedence over an outer folder ID.
        for stem in (path.stem, (path.parts[-2] if len(path.parts) > 1 else "")):
            match = re.search(r"\(([A-Za-z0-9_-]{1,128})\)(?:_\d+)?$", stem)
            if match:
                return match.group(1)
        return None

    def _index(self, works) -> tuple[dict, set, set, set]:
        locations = defaultdict(list)
        failed_authors: set[str] = set()
        ambiguous: set[tuple[str, int]] = set()
        uncertain_authors: set[str] = set()
        grouped = defaultdict(dict)
        unique_titles = {}
        examined = 0
        stores_by_author, wrappers = self._discover_stores()
        for work in works.values():
            if work.work_type not in {"ugoira", "novel"}:
                grouped[work.author_id][work.work_id] = work
        for author_id, author_works in grouped.items():
            by_title = defaultdict(list)
            self._analysis_checkpoint()
            for work in author_works.values():
                by_title[_saved_title(work.artwork.title)].append(work)
            unique_titles[author_id] = {title: items[0] for title, items in by_title.items()
                                        if title and len(items) == 1}

        def append(location: tuple[Path, str | None], matches) -> None:
            matches = tuple(matches)
            if len(matches) > 1:
                ambiguous.update(matches)
            for key in matches:
                if location not in locations[key]:
                    locations[key].append(location)

        def resolve(name: str, author_id: str, author_works: dict, titles: dict):
            embedded_id = self._id_from_name(name)
            if embedded_id is not None:
                work = author_works.get(embedded_id)
                if work is None:
                    return ()
                page = self._page_for(name, work)
                if page is None and PurePosixPath(name).suffix.lower() in _IMAGE:
                    # An identified but unsupported legacy page name is not
                    # proof the work is absent: its page index is uncertain.
                    uncertain_authors.add(author_id)
                return () if page is None else ((work.work_id, page),)
            matches = self._legacy_pages(name, author_works, titles)
            if PurePosixPath(name).suffix.lower() in _IMAGE and len(matches) != 1:
                # A legacy name may belong to a same-title work even when
                # the viewer intentionally has no safe attribution for it.
                uncertain_authors.add(author_id)
            return matches

        for author_id, author_works in grouped.items():
            for store in stores_by_author.get(author_id, ()):
                self._analysis_checkpoint()
                if store.suffix.lower() == ".zip":
                    try:
                        with self._checked_open(store) as source, ZipFile(source) as archive:
                            infos = archive.infolist()
                            if len(infos) > _MAX_MEMBERS:
                                raise MediaUnavailable("archive too large")
                            names = set()
                            for info in infos:
                                self._analysis_checkpoint()
                                examined += 1
                                if examined > _MAX_FILES:
                                    raise MediaUnavailable("too many media entries")
                                if (len(info.filename) > 4096 or
                                    not _safe_entry(info.filename) or
                                    info.filename in names or
                                    stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK):
                                    raise MediaUnavailable("unsafe archive entry")
                                names.add(info.filename)
                                if (info.file_size > _MAX_FILE or
                                    (info.file_size and info.compress_size == 0) or
                                    (info.compress_size and info.file_size / info.compress_size > _MAX_RATIO)):
                                    raise MediaUnavailable("archive entry exceeds limits")
                                if not info.is_dir():
                                    append((store, info.filename), resolve(
                                        info.filename, author_id, author_works, unique_titles[author_id]))
                    except (BadZipFile, MediaUnavailable, OSError, ValueError, RuntimeError,
                            NotImplementedError, zlib.error):
                        failed_authors.add(author_id)
                        continue
                else:
                    for path in store.rglob("*"):
                        self._analysis_checkpoint()
                        examined += 1
                        if examined > _MAX_FILES:
                            raise MediaUnavailable("too many media entries")
                        if not path.is_file() or not _safe_file(path, self.root):
                            continue
                        name = path.relative_to(store).as_posix()
                        append((path, None), resolve(name, author_id, author_works,
                                                     unique_titles[author_id]))
        # Legacy direct bookmark placement: ☆ブックマーク(owner)/作者(uid)_題名(workid)_N.png
        for wrapper in wrappers:
            for path in wrapper.iterdir():
                self._analysis_checkpoint()
                examined += 1
                if examined > _MAX_FILES:
                    raise MediaUnavailable("too many media entries")
                if not path.is_file() or not _safe_file(path, self.root):
                    continue
                # Parse the embedded author ID once per possible marker rather
                # than searching every author for every direct-mode image.
                for match in re.finditer(r"\(([A-Za-z0-9_-]{1,128})\)_", path.stem):
                    author_id = match.group(1)
                    author_works = grouped.get(author_id)
                    if not author_works or match.start() == 0:
                        continue
                    suffix_name = path.stem[match.end():] + path.suffix
                    append((path, None), resolve(suffix_name, author_id, author_works,
                                                 unique_titles[author_id]))
        return ({key: tuple(items) for key, items in locations.items()},
                failed_authors, ambiguous, uncertain_authors)

    def _ensure_index(self) -> None:
        self._analysis_checkpoint()
        with self._lock:
            if self._closed:
                raise MediaUnavailable("catalog closed")
            if self._indexed:
                return
        # Exclusive construction, but never hold the UI-facing catalog lock
        # while scanning the filesystem or ZIP central directories.
        while not self._index_lock.acquire(timeout=0.1):
            self._analysis_checkpoint()
        try:
            with self._lock:
                if self._closed:
                    raise MediaUnavailable("catalog closed")
                if self._indexed:
                    return
                generation = self._generation
                works = dict(self.works)
            locations, failures, ambiguous, uncertain_authors = self._index(works)
            self._analysis_checkpoint()
            with self._lock:
                if self._closed or self._generation != generation:
                    raise MediaUnavailable("obsolete library index")
                self._locations = locations
                self._failed_authors = failures
                self._ambiguous = ambiguous
                self._presence_uncertain_authors = uncertain_authors
                self._indexed = True
        finally:
            self._index_lock.release()

    def inspect_presence(self, work_id: str) -> bool | None:
        """Read-only, conservative proof of all local pages for one known work.

        None means neither presence nor absence is proved. The author's other
        unambiguous media must establish that the selected storage location is
        populated before missing pages can be reported as absent.
        """
        with self._lock:
            if self._closed:
                raise MediaUnavailable("catalog closed")
            if type(work_id) is not str or not _ID.fullmatch(work_id):
                raise MediaMissing("invalid work ID")
            work = self.works.get(work_id)
            if work is None or work.work_type in {"ugoira", "novel"}:
                return None
            if not work.page_count_known or not work.author_id:
                return None
        self._ensure_index()
        with self._lock:
            if self._closed:
                raise MediaUnavailable("catalog closed")
            work = self.works.get(work_id)
            if (work is None or work.work_type in {"ugoira", "novel"}
                    or not work.page_count_known or not work.author_id
                    or work.author_id in self._failed_authors):
                return None

            def confirmed(key: tuple[str, int]) -> bool | None:
                if key in self._ambiguous:
                    return None
                if not self._locations.get(key):
                    return False
                try:
                    path, member = self._source(*key, "view")
                    if not _safe_file(path, self.root) or not path.is_file():
                        return None
                    if member is not None:
                        # Check only the ZIP directory; never read/decode pixels.
                        with self._checked_open(path) as opened, ZipFile(opened) as archive:
                            if archive.getinfo(member).is_dir():
                                return None
                    return True
                except (MediaMissing, MediaAmbiguous, MediaUnavailable,
                        OSError, ValueError, BadZipFile, KeyError, RuntimeError):
                    return None

            keys = tuple((work_id, page) for page in range(work.page_count))
            statuses = tuple(confirmed(key) for key in keys)
            if any(status is None for status in statuses):
                return None
            if all(status is True for status in statuses):
                return True
            # Unattributed old names could be missing pages; only a fully
            # identified author inventory can establish definite absence.
            if work.author_id in self._presence_uncertain_authors:
                return None
            for key in self._locations:
                other = self.works.get(key[0])
                if (other is not None and other.author_id == work.author_id
                        and confirmed(key) is True):
                    return False
            return None

    def _source(self, work_id: str, page_no: int, variant: str):
        if self._closed:
            raise MediaUnavailable("catalog closed")
        work = self.works.get(work_id)
        if (work is None or not _ID.fullmatch(work_id) or
            type(page_no) is not int or not 0 <= page_no < work.page_count or
            variant not in {"thumb", "view", "original"}):
            raise MediaMissing("image not found")
        if work.work_type in {"ugoira", "novel"}:
            raise MediaMissing("unsupported media type")
        if not self._indexed:
            raise MediaUnavailable("library index not yet prepared")
        if work.author_id in self._failed_authors:
            raise MediaUnavailable("author storage unreadable")
        if (work_id, page_no) in self._ambiguous:
            raise MediaAmbiguous("legacy filename belongs to multiple works")
        choices = self._locations.get((work_id, page_no), ())
        if not choices:
            raise MediaMissing("image not found")
        if len(choices) != 1:
            # The original app hardlinks master files into its bookmark tree.
            # Two paths to the *same inode* are one image, not ambiguity.
            try:
                identities = [(p.stat().st_dev, p.stat().st_ino, p.stat().st_size)
                              for p, member in choices if member is None and _safe_file(p, self.root)]
            except OSError as exc:
                raise MediaUnavailable("media changed during lookup") from exc
            if len(identities) != len(choices) or len(set(identities)) != 1:
                raise MediaAmbiguous("multiple images for same work/page")
        return choices[0]

    def analysis_source_identity(self, work_id, page_no=0):
        """Fresh identity for an inference input; never uses the display cache.

        A ZIP/member is identified by the checked archive handle and its central
        directory entry. A changed file, archive or mapping invalidates a saved
        inference proof, including after a source is removed or quarantined.
        The caller stores a digest, never the private locator itself.
        """
        self.url(work_id, page_no, 'original')
        self._ensure_index()
        with self._lock:
            path, member = self._source(work_id, page_no, 'original')
            generation = self._generation
        with self._checked_open(path) as source:
            info = os.fstat(source.fileno())
            entry = None
            if member is not None:
                with ZipFile(source) as archive:
                    item = archive.getinfo(member)
                    if (not _safe_entry(item.filename) or item.is_dir()
                            or item.file_size > _MAX_FILE
                            or (item.file_size and item.compress_size == 0)
                            or (item.compress_size and item.file_size / item.compress_size > _MAX_RATIO)
                            or stat.S_IFMT(item.external_attr >> 16) == stat.S_IFLNK):
                        raise MediaUnavailable('unsafe analysis archive member')
                    entry = (member, item.CRC, item.file_size, item.compress_size, item.header_offset)
            elif info.st_size > _MAX_FILE:
                raise MediaUnavailable('analysis image exceeds limits')
        with self._lock:
            if self._closed or generation != self._generation:
                raise MediaUnavailable('analysis source changed')
        return (str(self.root), str(path.relative_to(self.root)), info.st_dev,
                info.st_ino, info.st_size, info.st_mtime_ns, entry)

    def external_target(self, work_id, page_no=0):
        """Resolve only indexed saved content; ZIP stays an archive, never extracted."""
        self.url(work_id,page_no,'view')
        self._ensure_index()
        with self._lock:
            path,member=self._source(work_id,page_no,'view')
            generation=self._generation
        with self._checked_open(path) as source:
            if member is not None:
                with ZipFile(source) as archive:
                    info=archive.getinfo(member)
                    if not _safe_entry(info.filename) or info.is_dir():raise MediaUnavailable('saved archive changed')
        with self._lock:
            if self._closed or generation!=self._generation:raise MediaUnavailable('saved snapshot changed')
        return path

    def url(self, work_id: str, page_no: int = 0, variant: str = "view") -> str:
        with self._lock:
            if self._closed:
                raise MediaUnavailable("catalog closed")
            work = self.works.get(work_id)
            if (work is None or not _ID.fullmatch(work_id) or type(page_no) is not int
                    or not 0 <= page_no < work.page_count or variant not in {"thumb", "view", "original"}):
                raise MediaMissing("image not found")
            return f"/media/{work_id}/{page_no}/{variant}.png"

    def supports_original(self, work_id: str) -> bool:
        with self._lock:
            work=self.works.get(work_id)
            return (not self._closed and work is not None and
                    work.work_type not in {'novel','ugoira'})

    def get(self, work_id: str, page_no: int = 0, variant: str = "view") -> ImageBlob:
        # Reject invalid requests before any expensive filesystem scan.
        with self._lock:
            if self._closed:
                raise MediaUnavailable("catalog closed")
            work = self.works.get(work_id)
            if (work is None or not isinstance(work_id, str) or not _ID.fullmatch(work_id)
                    or type(page_no) is not int or not 0 <= page_no < work.page_count
                    or variant not in {"thumb", "view", "original"}
                    or work.work_type in {"ugoira", "novel"}):
                raise MediaMissing("image not found")
        self._ensure_index()
        with self._lock:
            key = (work_id, page_no, variant)
            if self._closed:
                raise MediaUnavailable("catalog closed")
            if self._shared_cache is not None:
                cached=self._shared_cache.get(key)
                if cached is not None:return cached
            elif key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            path, member = self._source(work_id, page_no, variant)
            generation = self._generation
            request_cache=self._shared_cache
        # I/O and decoding are deliberately outside the catalog lock: concurrent
        # requests for different pages must not serialize behind a large image.
        try:
            # A few large originals may consume hundreds of MiB when decoded.
            # Bound the number of concurrent image decoders across clients.
            with _DECODE_SLOTS:
                with self._lock:
                    if self._closed or generation != self._generation:
                        raise MediaUnavailable("obsolete image request")
                with self._checked_open(path) as source:
                    if member is None:
                        if source.seek(0, 2) > _MAX_FILE:
                            raise MediaUnavailable("image exceeds limits")
                        source.seek(0)
                        data = source.read(_MAX_FILE + 1)
                    else:
                        with ZipFile(source) as archive:
                            info = archive.getinfo(member)
                            if (not _safe_entry(info.filename) or info.is_dir() or
                                    info.file_size > _MAX_FILE or
                                    (info.file_size and info.compress_size == 0) or
                                    (info.compress_size and info.file_size / info.compress_size > _MAX_RATIO) or
                                    stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK):
                                raise MediaUnavailable("unsafe archive member")
                            with archive.open(info) as stream:
                                data = stream.read(_MAX_FILE + 1)
                if len(data) > _MAX_FILE:
                    raise MediaUnavailable("image exceeds limits")
                disk_key=("local-thumbnail-v1:"+work_id+":"+str(page_no)+":"+hashlib.sha256(data).hexdigest()) if variant=="thumb" else None
                cached=request_cache.get(key,disk_key=disk_key) if request_cache is not None and disk_key is not None else None
                if cached is not None:
                    blob=cached
                    png=cached.data
                elif variant == 'original':
                    # Exact legacy folder/ZIP bytes. Reuse the same verified
                    # path and ZIP-member checks as the ordinary thumbnail.
                    from .image_original import verified_original_blob
                    blob=verified_original_blob(data)
                    png=blob.data
                else:
                    with Image.open(BytesIO(data)) as img:
                        if img.width * img.height > _MAX_PIXELS:
                            raise MediaUnavailable("image dimension limit")
                        img.draft("RGB", (192, 192) if variant == "thumb" else (640, 640))
                        img.thumbnail((192, 192) if variant == "thumb" else (640, 640))
                        output = BytesIO()
                        img.convert("RGBA" if "A" in img.getbands() or "transparency" in img.info
                                    else "RGB").save(output, format="PNG")
                    png = output.getvalue()
                    blob = ImageBlob(png, "image/png", '"' + hashlib.sha256(png).hexdigest() + '"')
        except (OSError, BadZipFile, UnidentifiedImageError, Image.DecompressionBombError,
                ValueError, KeyError, NotImplementedError, zlib.error) as exc:
            raise MediaUnavailable("media read failed") from exc
        with self._lock:
            if self._closed or generation != self._generation:
                raise MediaUnavailable("obsolete image request")
            # Two requests may have decoded the same key while the lock was
            # released. Reuse the winner and account for its bytes only once.
            if self._shared_cache is not None:
                cached=self._shared_cache.get(key)
                if cached is not None:return cached
            elif key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            if self._shared_cache is not None:
                self._shared_cache.put(key,blob,disk_key=disk_key)
                return blob
            if len(png) <= self._cache_limit:
                while self._cache and self._cache_bytes + len(png) > self._cache_limit:
                    _, oldest = self._cache.popitem(last=False)
                    self._cache_bytes -= len(oldest.data)
                self._cache[key] = blob
                self._cache_bytes += len(png)
            return blob

    def peek(self, work_id: str, page_no: int, variant: str):
        """Cached HTTP metadata only; never scan or open files for HEAD."""
        with self._lock:
            if self._closed:
                raise MediaUnavailable("catalog closed")
            return self._shared_cache.get((work_id,page_no,variant)) if self._shared_cache is not None else self._cache.get((work_id, page_no, variant))

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._shared_cache is not None:self._shared_cache.close()
            self._locations.clear()
            self._failed_authors.clear()
            self._ambiguous.clear()
            self._presence_uncertain_authors.clear()
            self._cache.clear()
            self._cache_bytes = 0
            if self._shared_cache is not None:self._shared_cache.clear()
            self._generation += 1

    def replace_snapshot(self, snapshot: LibrarySnapshot) -> None:
        """Invalidate completed index; future requests use the new DB identity set."""
        with self._lock:
            if self._closed:
                raise MediaUnavailable("catalog closed")
            self.works = {work.work_id: work for work in snapshot.works}
            self._locations.clear()
            self._failed_authors.clear()
            self._ambiguous.clear()
            self._presence_uncertain_authors.clear()
            self._indexed = False
            self._cache.clear()
            self._cache_bytes = 0
            if self._shared_cache is not None:
                self._shared_cache.close()
                self._shared_cache=self._shared_owner.namespace()
            self._generation += 1


class CombinedImageCatalog:
    """Leave ranking/learning fixtures separate from the opted-in library."""

    def __init__(self, library: ReadonlyLocalMediaCatalog, demo):
        self.library = library
        self.demo = demo

    def _catalog(self, work_id: str):
        return self.library if work_id in self.library.works else self.demo

    def supports_original(self, work_id: str) -> bool:
        provider=self._catalog(work_id)
        supports=getattr(provider,'supports_original',None)
        return bool(callable(supports) and supports(work_id))

    def external_target(self,work_id,page_no=0):
        provider=self._catalog(work_id)
        method=getattr(provider,'external_target',None)
        if method is None:raise MediaMissing('saved target unavailable')
        return method(work_id,page_no)

    def url(self, work_id: str, page_no: int = 0, variant: str = "view") -> str:
        return self._catalog(work_id).url(work_id, page_no, variant)

    def get(self, work_id: str, page_no: int = 0, variant: str = "view") -> ImageBlob:
        return self._catalog(work_id).get(work_id, page_no, variant)

    def cache_control(self, work_id: str) -> str:
        return "no-store" if work_id in self.library.works else "private, max-age=3600"

    def close(self):
        self.library.close()
        self.demo.close()
