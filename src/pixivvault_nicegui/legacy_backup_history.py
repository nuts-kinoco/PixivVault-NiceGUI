"""Read-only author tiles for the original last_backup history (H01).

Only already-attributed local images may represent an author. The same pinned
catalog supplies both the thumbnail URL and the verified on-disk location;
the history never guesses a missing image or fetches anything from Pixiv.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from zipfile import BadZipFile, ZipFile

from .library_models import LibrarySnapshot
from .local_media_adapter import ReadonlyLocalMediaCatalog, _safe_file


_AUTHOR_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_STILL_TYPES = frozenset({"illust", "manga", "illustration"})


@dataclass(frozen=True, slots=True)
class LegacyBackupAuthor:
    author_id: str
    author_name: str
    last_backup: str
    title: str
    work_id: str
    page_no: int
    thumbnail: str
    target: Path


def author_storage_target(image_root: Path, author_id: str, source: Path) -> Path | None:
    """Only a matching author folder or the containing folder of its ZIP.

    The source is supplied by PinnedLibraryImages.external_target, which
    validates the work/page identity and guards links and ZIP entries. The
    desktop launcher independently rechecks the final path before opening it.
    """
    root = Path(image_root).absolute()
    source = Path(source).absolute()
    if not _AUTHOR_ID.fullmatch(author_id) or not source.is_relative_to(root):
        return None
    if source.is_file() and source.suffix.lower() == ".zip":
        return source.parent
    if not source.is_file():
        return None
    for parent in source.parents:
        if parent == root:
            break
        if (parent.name.endswith(f"({author_id})")
                and not parent.name.startswith("☆ブックマーク(")):
            return parent
    return None


def _display_name(snapshot: LibrarySnapshot, author_id: str, source: Path,
                  target: Path) -> str:
    registered = snapshot.author_names.get(author_id)
    if registered and registered.strip():
        return registered
    # Original gui.py falls back to the saved author directory/ZIP basename
    # when following_users has no name for this old download.
    store = source.stem if source.suffix.lower() == ".zip" else target.name
    match = re.fullmatch(r"(.*)\(" + re.escape(author_id) + r"\)", store)
    return match.group(1) if match and match.group(1) else f"ユーザー({author_id})"


def legacy_backup_authors(snapshot: LibrarySnapshot, images, image_root: Path,
                          catalog: ReadonlyLocalMediaCatalog):
    """Original H01: latest non-null backup per author, first 60 candidates.

    A missing/novel-only author consumes its original top-60 slot, but never
    becomes a fabricated tile. Files and archives are only inspected; the
    image catalog performs the expensive safe attribution once off the UI loop.
    """
    if not isinstance(snapshot, LibrarySnapshot):
        raise TypeError("expected library snapshot")
    if not isinstance(catalog, ReadonlyLocalMediaCatalog):
        raise TypeError("expected read-only local media catalog")
    authors = sorted(
        (author for author in snapshot.authors if author.last_backup is not None),
        key=lambda author: (author.last_backup or "", author.author_id),
        reverse=True,
    )[:60]
    # The media catalog has already attributed each filename/ZIP member to an
    # exact work and page (or rejected it as ambiguous). Reuse that single
    # bounded read-only index instead of crawling all author directories twice.
    works_by_id = {work.work_id: work for author in authors for work in author.works
                   if work.work_type in _STILL_TYPES}
    catalog._ensure_index()
    with catalog._lock:
        locations = tuple((key, paths) for key, paths in catalog._locations.items()
                          if key[0] in works_by_id)

    zip_dates = {}
    by_author = {author.author_id: [] for author in authors}
    for (work_id, page_no), sources in locations:
        work = works_by_id[work_id]
        for path, member in sources:
            try:
                if member is None:
                    if not _safe_file(path, catalog.root):
                        continue
                    score = (1, path.stat().st_mtime_ns)
                else:
                    if path not in zip_dates:
                        with catalog._checked_open(path) as file, ZipFile(file) as archive:
                            infos = archive.infolist()
                            if len(infos) > 100_000:
                                raise ValueError("archive member limit exceeded")
                            zip_dates[path] = {
                                info.filename: info.date_time for info in infos
                                if not info.is_dir()}
                    score = (0, zip_dates[path][member])
            except (OSError, ValueError, RuntimeError, BadZipFile, KeyError):
                continue
            by_author[work.author_id].append((score, work.last_backup or "",
                                              work_id, page_no))

    rows = []
    missing = 0
    for author in authors:
        if not _AUTHOR_ID.fullmatch(author.author_id):
            missing += 1
            continue
        # Original Flet H01 uses newest filesystem mtime for folders, or newest
        # ZIP entry date when no folder image exists. Keep its precedence, but
        # require a verified image identity before constructing each tile.
        selected = None
        tried = set()
        for _, _, work_id, page_no in sorted(by_author[author.author_id], reverse=True):
            if (work_id, page_no) in tried:
                continue
            tried.add((work_id, page_no))
            try:
                source = images.external_target(work_id, page_no)
                target = author_storage_target(image_root, author.author_id, source)
                if target is None:
                    continue
                thumbnail = images.url(work_id, page_no, "thumb")
            except (OSError, ValueError, RuntimeError):
                continue
            selected = LegacyBackupAuthor(
                author_id=author.author_id,
                author_name=_display_name(snapshot, author.author_id, Path(source), target),
                last_backup=author.last_backup or "",
                title=works_by_id[work_id].artwork.title,
                work_id=work_id,
                page_no=page_no,
                thumbnail=thumbnail,
                target=target,
            )
            break
        if selected is None:
            missing += 1
        else:
            rows.append(selected)
    return tuple(rows), missing
