"""Explicit legacy undecidable rescan into a new owned, immutable generation.

The original tools/dedup.py:373-552, scan_style_embeddings.py:152-248 and
core.py:820-838 supply the filename/page-set rules. No original module is
imported or original Database instantiated. Source DB and image stores are
read-only; generations and their pointer stay below an identified managed root.
The caller publishes ``groups`` only after the entire explicit scan succeeds.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
from typing import Callable
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

from .failed_records_repository import immutable_copy_signature
from .fs_paths import native_io_path, sqlite_readonly_uri
from .local_media_adapter import _safe_entry, _saved_title
from .managed_downloads import (
    IDENTITY, MARKER, _fsync_dir, _json_unique, _read_bounded, _safe_path, _write,
)
from .managed_root_writer import writer_fence

_AUTHOR = re.compile(r'\((\d+)\)$')
_PAGE = re.compile(r'^(.*)_(\d+)$')
_IMAGES = {'.jpg', '.jpeg', '.png', '.webp', '.bmp'}
_REASON = '同じ作者に同じ題名の作品が複数あり、どれのファイルか決められない'
_MAX_ENTRIES = 250_000
_MAX_JSON = 32 * 1024 * 1024
_GENERATION = re.compile(r'[a-f0-9]{32}\Z')
_DIGEST = re.compile(r'[a-f0-9]{64}\Z')


class LedgerRebuildError(ValueError):
    def __init__(self):
        super().__init__('legacy ledger rebuild unavailable or changed')


@dataclass(frozen=True)
class LedgerRebuildResult:
    groups: tuple[dict, ...]
    generation_path: Path
    scanned_groups: int
    skipped_multi: int
    unreadable: tuple[str, ...]
    generation_sha256: str = ''


def _identity(path):
    info = native_io_path(path).stat()
    return info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns


def _read_source(db):
    conn = sqlite3.connect(sqlite_readonly_uri(db, immutable=True), uri=True)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 2 * 1024 * 1024)
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        name = "COALESCE(f.name, '')" if 'following_users' in tables else "''"
        join = (' LEFT JOIN following_users f ON f.user_id=w.user_id'
                if 'following_users' in tables else '')
        rows = conn.execute(
            'SELECT w.work_id,w.user_id,w.title,w.page_count,w.content_type,'
            + name + ' AS author_name FROM works w' + join
            + ' WHERE COALESCE(w.is_deleted,0)=0 ORDER BY w.user_id,w.work_id'
        ).fetchmany(_MAX_ENTRIES + 1)
        if len(rows) > _MAX_ENTRIES:
            raise LedgerRebuildError()
        works = defaultdict(list)
        for row in rows:
            item = dict(row)
            for key in ('work_id', 'user_id', 'title', 'author_name', 'content_type'):
                value = item[key]
                if value is not None and not isinstance(value, (str, int)):
                    raise LedgerRebuildError()
                item[key] = str(value) if value is not None else ''
            item['page_count'] = max(1, int(item['page_count'] or 1))
            if item['page_count'] > 100_000:
                raise LedgerRebuildError()
            works[item['user_id']].append(item)
        # Do not infer or replace a manual decision, even if its images moved.
        manual = ([dict(row) for row in conn.execute(
            "SELECT * FROM file_ledger WHERE resolved_by='manual' ORDER BY work_id,page"
        ).fetchmany(_MAX_ENTRIES + 1)] if 'file_ledger' in tables else [])
        if len(manual) > _MAX_ENTRIES:
            raise LedgerRebuildError()
        return works, manual
    finally:
        conn.close()


class _SourceInventory:
    def __init__(self, root, checkpoint):
        self.root = root
        self.checkpoint = checkpoint
        self.proofs = {}
        self.examined = 0

    def remember(self, path):
        self.checkpoint()
        self.examined += 1
        if self.examined > _MAX_ENTRIES:
            raise LedgerRebuildError()
        if not path.is_relative_to(self.root):
            raise LedgerRebuildError()
        _safe_path(path, file=not native_io_path(path).is_dir())
        identity = _identity(path)
        if path in self.proofs and self.proofs[path] != identity:
            raise LedgerRebuildError()
        self.proofs[path] = identity

    def children(self, path):
        self.remember(path)
        entries = sorted(native_io_path(path).iterdir(), key=lambda p: p.name)
        # Return the input spelling (including normal Windows drive paths).
        result = [path / entry.name for entry in entries]
        for entry in result:
            self.remember(entry)
        return result

    def stores(self):
        stores = defaultdict(list)

        def walk(path, depth):
            for entry in self.children(path):
                if entry.suffix.lower() == '.zip' and native_io_path(entry).is_file():
                    match = _AUTHOR.search(entry.stem)
                    if match:
                        stores[match[1]].append(entry)
                elif native_io_path(entry).is_dir():
                    match = _AUTHOR.search(entry.name)
                    has_author_children = any(
                        _AUTHOR.search(child.name) and native_io_path(child).is_dir()
                        for child in self.children(entry))
                    if match and not has_author_children:
                        stores[match[1]].append(entry)
                    elif depth < 2:
                        walk(entry, depth + 1)
        walk(self.root, 0)
        return stores

    def index(self, paths):
        index = defaultdict(list)
        errors = []
        for path in paths:
            self.remember(path)
            if native_io_path(path).is_dir():
                names = []
                pending = [path]
                while pending:
                    for entry in self.children(pending.pop()):
                        if native_io_path(entry).is_dir():
                            pending.append(entry)
                        else:
                            names.append(entry.relative_to(path).as_posix())
            else:
                try:
                    with native_io_path(path).open('rb') as stream, ZipFile(stream) as archive:
                        infos = archive.infolist()
                        if len(infos) > _MAX_ENTRIES:
                            raise LedgerRebuildError()
                        names = []
                        seen = set()
                        for info in infos:
                            if (not _safe_entry(info.filename) or info.filename in seen
                                    or stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK):
                                raise LedgerRebuildError()
                            seen.add(info.filename)
                            if not info.is_dir():
                                names.append(info.filename)
                except (BadZipFile, OSError) as error:
                    errors.append(type(error).__name__)
                    continue
            for entry in names:
                self.checkpoint()
                self.examined += 1
                if self.examined > _MAX_ENTRIES:
                    raise LedgerRebuildError()
                base = PurePosixPath(entry)
                match = _PAGE.fullmatch(base.stem)
                head = match[1] if match else base.stem
                page = int(match[2]) if match else 1
                index[head].append((str(path), entry, page, base.suffix.lower()))
        return index, errors

    def verify(self):
        for path, identity in self.proofs.items():
            self.checkpoint()
            _safe_path(path, file=not stat.S_ISDIR(identity[2]))
            if _identity(path) != identity:
                raise LedgerRebuildError()


def _author_candidates(works, index, checkpoint):
    illusts = [w for w in works if (w['content_type'] or 'illust') == 'illust']
    titles = Counter(_saved_title(w['title']) for w in illusts)
    flat_stems = {PurePosixPath(entry).stem for values in index.values()
                  for _, entry, _, _ in values if '/' not in entry}
    first_pass = defaultdict(list)
    for work in illusts:
        checkpoint()
        title = _saved_title(work['title'])
        if titles[title] < 2:
            continue
        count = work['page_count']
        stem = f"{title}({work['work_id']})"
        id_stems = {stem} if count == 1 else {f'{stem}_{page}' for page in range(1, count + 1)}
        # core.judge_local_copy checks complete ID names before duplicate title.
        if id_stems <= flat_stems:
            continue
        first_pass[title].append(work)
    candidates = []
    skipped = 0
    for title, members in first_pass.items():
        checkpoint()
        physical = [row for row in index.get(title, ()) if row[3] in _IMAGES]
        actual = {row[2] for row in physical}
        # Preserve the original complete-set rule, including equal-count skip.
        matching = [work for work in members
                    if set(range(1, work['page_count'] + 1)) == actual]
        if len(matching) >= 2:
            skipped += 1
        elif len(matching) == 1:
            # Original ledger PK is (work_id,page); the last path for a page wins.
            pages = {row[2]: {'page': row[2], 'store_path': row[0], 'entry_name': row[1]}
                     for row in physical}
            for work in members:
                candidates.append((f"{work['user_id']}:{title}", work,
                                   [pages[page] for page in sorted(pages)]))
    return candidates, skipped


def rebuild_undecidable_ledger(*, undecidable_db: Path, images_root: Path,
                               managed_root: Path,
                               check_cancelled: Callable[[], None] | None = None
                               ) -> LedgerRebuildResult:
    """Return fresh original-shaped groups and their new owned JSON generation.

    Supply an explicit frozen DB copy and an existing ManagedPixivDownloads
    root. ``check_cancelled`` may raise at checkpoints; no current-generation
    pointer is changed here. A failed/interrupted attempt must not replace the
    caller's last published groups. Unreferenced partial generations are left
    untouched for recovery; this helper never deletes or modifies a file.
    """
    checkpoint = check_cancelled or (lambda: None)
    try:
        db, images, managed = map(Path, (undecidable_db, images_root, managed_root))
        if not all(path.is_absolute() for path in (db, images, managed)):
            raise LedgerRebuildError()
        _safe_path(db, file=True)
        _safe_path(images)
        _safe_path(managed)
        if not native_io_path(images).is_dir() or not native_io_path(managed).is_dir():
            raise LedgerRebuildError()
        # Compare canonical paths after rejecting all symlink/reparse ancestors;
        # caller spelling containing '..' must not evade the overlap boundary.
        db, images, managed = (path.resolve(strict=True) for path in (db, images, managed))
        if (managed.is_relative_to(images) or images.is_relative_to(managed)
                or db.is_relative_to(managed)):
            raise LedgerRebuildError()

        def check_owner():
            _safe_path(managed)
            if json.loads(_read_bounded(managed / MARKER, 4096),
                          object_pairs_hook=_json_unique) != IDENTITY:
                raise LedgerRebuildError()

        checkpoint()
        check_owner()
        before = immutable_copy_signature(db)
        works, manual = _read_source(db)
        inventory = _SourceInventory(images, checkpoint)
        stores = inventory.stores()
        manual_groups = {row['ambiguous_group'] for row in manual if row['ambiguous_group'] is not None}
        manual_keys = {(str(row['work_id']), row['page']) for row in manual}
        grouped = {}
        unreadable = []
        scanned = skipped = 0
        for author, author_works in works.items():
            checkpoint()
            if author not in stores:
                continue
            index, errors = inventory.index(stores[author])
            if errors:
                unreadable.extend(f'{author}:{error}' for error in errors)
                continue
            candidates, multi = _author_candidates(author_works, index, checkpoint)
            skipped += multi
            scanned += len(candidates)
            for group_id, work, pages in candidates:
                if group_id in manual_groups:
                    continue
                pages = [page for page in pages if (work['work_id'], page['page']) not in manual_keys]
                if not pages:
                    continue
                group = grouped.setdefault(group_id, {'ambiguous_group': group_id,
                                                       'reason': _REASON, 'candidates': []})
                group['candidates'].append({key: work[key] for key in
                                            ('work_id', 'title', 'author_name', 'user_id')} | {'pages': pages})
        groups = tuple({**grouped[key], 'candidates': tuple(sorted(
            grouped[key]['candidates'], key=lambda candidate: candidate['work_id']))}
                       for key in sorted(grouped))
        inventory.verify()
        if immutable_copy_signature(db) != before:
            raise LedgerRebuildError()
        payload = json.dumps({
            'version': 1, 'purpose': 'legacy-undecidable-ledger',
            'source_db_sha256': before[3].hex(),
            'source_db_path_sha256': _path_digest(db),
            'images_root_sha256': hashlib.sha256(str(images).casefold().encode()).hexdigest(),
            'groups': groups, 'manual_rows': manual, 'scanned_groups': scanned,
            'skipped_multi': skipped, 'unreadable': unreadable,
        }, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode()
        if len(payload) > _MAX_JSON:
            raise LedgerRebuildError()
        with writer_fence(managed):
            checkpoint()
            check_owner()
            generation = managed / 'legacy-ledger' / uuid4().hex
            _safe_path(generation)
            native_io_path(generation.parent).mkdir(exist_ok=True)
            native_io_path(generation).mkdir()
            target = generation / 'ledger.json'
            _write(target, payload)
            _fsync_dir(native_io_path(generation))
            _fsync_dir(native_io_path(generation.parent))
            checkpoint()
            check_owner()
            inventory.verify()
            if immutable_copy_signature(db) != before:
                raise LedgerRebuildError()
        return LedgerRebuildResult(groups, target, scanned, skipped, tuple(unreadable),
                                   hashlib.sha256(payload).hexdigest())
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError) as error:
        if isinstance(error, LedgerRebuildError):
            raise
        raise LedgerRebuildError() from error


def _path_digest(path):
    return hashlib.sha256(str(path).casefold().encode()).hexdigest()


def _publication_inputs(managed_root, undecidable_db, images_root, *, allow_missing=False):
    db, images, managed = map(Path, (undecidable_db, images_root, managed_root))
    if not all(path.is_absolute() for path in (db, images, managed)):
        raise LedgerRebuildError()
    _safe_path(managed)
    if allow_missing and not native_io_path(managed).exists():
        return None
    _safe_path(db, file=True)
    _safe_path(images)
    db, images, managed = (path.resolve(strict=True) for path in (db, images, managed))
    if (managed.is_relative_to(images) or images.is_relative_to(managed)
            or db.is_relative_to(managed)):
        raise LedgerRebuildError()
    if json.loads(_read_bounded(managed / MARKER, 4096), object_pairs_hook=_json_unique) != IDENTITY:
        raise LedgerRebuildError()
    signature = immutable_copy_signature(db)
    expected = {'source_db_sha256': signature[3].hex(),
                'source_db_path_sha256': _path_digest(db),
                'images_root_sha256': _path_digest(images)}
    return db, images, managed, signature, expected


def _restored_groups(value, images):
    """Validate saved UI-shaped groups; never follow a persisted source path."""
    if type(value) is not list or len(value) > _MAX_ENTRIES:
        raise LedgerRebuildError()
    groups = []
    for group in value:
        if (type(group) is not dict or set(group) != {'ambiguous_group', 'reason', 'candidates'}
                or not all(type(group[key]) is str for key in ('ambiguous_group', 'reason'))
                or type(group['candidates']) is not list or not group['candidates']):
            raise LedgerRebuildError()
        for candidate in group['candidates']:
            if (type(candidate) is not dict
                    or set(candidate) != {'work_id', 'title', 'author_name', 'user_id', 'pages'}
                    or not all(type(candidate[key]) is str for key in
                               ('work_id', 'title', 'author_name', 'user_id'))
                    or type(candidate['pages']) is not list or not candidate['pages']):
                raise LedgerRebuildError()
            for page in candidate['pages']:
                if (type(page) is not dict or set(page) != {'page', 'store_path', 'entry_name'}
                        or type(page['page']) is not int or page['page'] < 1
                        or type(page['store_path']) is not str or type(page['entry_name']) is not str
                        or not _safe_entry(page['entry_name'])):
                    raise LedgerRebuildError()
                store = Path(page['store_path'])
                if not store.is_relative_to(images) or '..' in store.parts:
                    raise LedgerRebuildError()
        groups.append({**group, 'candidates': tuple(group['candidates'])})
    return tuple(groups)


def _read_generation(target, digest, images, expected):
    if type(digest) is not str or not _DIGEST.fullmatch(digest):
        raise LedgerRebuildError()
    raw = _read_bounded(target, _MAX_JSON)
    if hashlib.sha256(raw).hexdigest() != digest:
        raise LedgerRebuildError()
    payload = json.loads(raw, object_pairs_hook=_json_unique)
    if (type(payload) is not dict or payload.get('version') != 1
            or payload.get('purpose') != 'legacy-undecidable-ledger'
            or any(payload.get(key) != value for key, value in expected.items())):
        raise LedgerRebuildError()
    return payload, _restored_groups(payload['groups'], images)


def _read_current(managed, images, expected):
    current = managed / 'legacy-ledger' / 'CURRENT.json'
    _safe_path(current, file=True)
    if not native_io_path(current).exists():
        return None
    raw = _read_bounded(current, 4096)
    ref = json.loads(raw, object_pairs_hook=_json_unique)
    if (type(ref) is not dict or set(ref) != {'version', 'generation', 'sha256'}
            or ref['version'] != 1 or type(ref['generation']) is not str
            or not _GENERATION.fullmatch(ref['generation'])):
        raise LedgerRebuildError()
    target = current.parent / ref['generation'] / 'ledger.json'
    _, groups = _read_generation(target, ref['sha256'], images, expected)
    if _read_bounded(current, 4096) != raw:
        raise LedgerRebuildError()
    return groups


def load_rebuilt_ledger(*, managed_root: Path, undecidable_db: Path,
                       images_root: Path) -> tuple[dict, ...] | None:
    """Read the last explicitly published generation; None means never published.

    A corrupt pointer/generation, changed DB, different input path or unowned
    existing root raises. The caller must not hide such errors by loading the
    source's old ledger. A missing managed root is a read-only initial state.
    """
    try:
        inputs = _publication_inputs(managed_root, undecidable_db, images_root,
                                     allow_missing=True)
        if inputs is None:
            return None
        db, images, managed, signature, expected = inputs
        with writer_fence(managed):
            groups = _read_current(managed, images, expected)
            after = _publication_inputs(managed, db, images)
            if after[3] != signature:
                raise LedgerRebuildError()
            return groups
    except (OSError, ValueError, TypeError, KeyError) as error:
        if isinstance(error, LedgerRebuildError):
            raise
        raise LedgerRebuildError() from error


def publish_rebuilt_ledger(result: LedgerRebuildResult, *, managed_root: Path,
                           undecidable_db: Path, images_root: Path) -> None:
    """Atomically publish only after the caller's complete explicit scan succeeds.

    Every previous generation remains intact. A failure before replacement
    retains the old pointer; an exception after replacement is reconciled from
    the verified new pointer and returned as committed success.
    """
    try:
        db, images, managed, signature, expected = _publication_inputs(
            managed_root, undecidable_db, images_root)
        if not isinstance(result, LedgerRebuildResult):
            raise LedgerRebuildError()
        generation = Path(result.generation_path)
        parent = managed / 'legacy-ledger'
        if (generation.name != 'ledger.json' or generation.parent.parent != parent
                or not _GENERATION.fullmatch(generation.parent.name)):
            raise LedgerRebuildError()
        with writer_fence(managed):
            # Refuse to overwrite tampered or foreign-source existing state.
            _read_current(managed, images, expected)
            payload, groups = _read_generation(generation, result.generation_sha256,
                                                images, expected)
            if (groups != result.groups or payload['scanned_groups'] != result.scanned_groups
                    or payload['skipped_multi'] != result.skipped_multi
                    or tuple(payload['unreadable']) != result.unreadable):
                raise LedgerRebuildError()
            ref = json.dumps({'version': 1, 'generation': generation.parent.name,
                              'sha256': result.generation_sha256}, sort_keys=True,
                             separators=(',', ':')).encode()
            current = parent / 'CURRENT.json'
            pending = parent / ('CURRENT.' + uuid4().hex + '.next')
            _write(pending, ref)
            _fsync_dir(native_io_path(parent))
            _safe_path(current, file=True)
            after = _publication_inputs(managed, db, images)
            if after[3] != signature:
                raise LedgerRebuildError()
            try:
                os.replace(native_io_path(pending), native_io_path(current))
                _fsync_dir(native_io_path(parent))
            except OSError:
                # A post-replace error must not leave memory claiming old state
                # while a verified new generation is already authoritative.
                if not native_io_path(current).exists() or _read_bounded(current, 4096) != ref:
                    raise
            if _read_current(managed, images, expected) != groups:
                raise LedgerRebuildError()
    except (OSError, ValueError, TypeError, KeyError) as error:
        if isinstance(error, LedgerRebuildError):
            raise
        raise LedgerRebuildError() from error
