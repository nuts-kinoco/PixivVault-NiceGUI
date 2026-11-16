"""Explicit read-only export of a frozen legacy DB and optional image root.

The archive is published only after SQLite copy, JSON, ZIP and source-stability
checks succeed. The output directory must be a dedicated, marked, owned folder.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from uuid import uuid4
import zipfile

from .fs_paths import sqlite_readonly_uri


MARKER = 'PIXIVVAULT_METADATA_EXPORT_V1'
MARKER_BYTES = b'PixivVault NiceGUI metadata exports v1\n'
MAX_DB_BYTES = 2 * 1024 * 1024 * 1024
MAX_JSON_BYTES = 512 * 1024 * 1024
MAX_ROWS = 2_000_000
MAX_IMAGE_PATHS_BYTES = 16 * 1024 * 1024


class LegacyMetadataExportError(ValueError):
    """No successful export result exists for this request."""


def _encode_image_paths(groups):
    """Bound the UTF-8 manifest with the same limit used during restoration."""
    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    encoded = bytearray()
    for chunk in encoder.iterencode({'version': 1, 'images': groups}):
        data = chunk.encode('utf-8')
        if len(encoded) + len(data) > MAX_IMAGE_PATHS_BYTES:
            raise LegacyMetadataExportError(
                '画像の対応一覧が復元可能な容量を超えています。ZIPは作成していません。'
                '元データを保持し、相対パスを保った複数のコピーに画像を分割してから、'
                '別々にエクスポートしてください。')
        encoded.extend(data)
    return bytes(encoded)


@dataclass(frozen=True)
class LegacyMetadataExport:
    path: Path
    works: int
    db_sha256: str
    zip_sha256: str
    bytes_count: int
    cleanup_warning: bool = False


def _reject_links(path):
    for item in (path, *path.parents):
        try:
            # stat() follows junctions and loses the FILE_ATTRIBUTE_REPARSE_POINT
            # that protects the owned export boundary. lstat() inspects the
            # path component itself, including a dangling Windows junction.
            try:
                observed=item.lstat()
            except FileNotFoundError:
                continue
            if item.is_symlink() or (getattr(observed, 'st_file_attributes', 0) & 0x400):
                raise LegacyMetadataExportError('リンクを含むパスは使用できません。')
        except LegacyMetadataExportError:
            raise
        except (OSError, ValueError) as exc:
            raise LegacyMetadataExportError('パスの安全性を確認できません。') from exc


def _digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def _same_archive_payload(first, second, check_cancelled):
    """Compare verified entry contents, not ZIP timestamps or filenames."""
    try:
        _reject_links(first)
        with zipfile.ZipFile(first) as left, zipfile.ZipFile(second) as right:
            li, ri = left.infolist(), right.infolist()
            if (len(li) != len(ri) or len({x.filename for x in li}) != len(li)
                    or {x.filename for x in li} != {x.filename for x in ri}):
                return False
            for entry in ri:
                check_cancelled()
                old = left.getinfo(entry.filename)
                if old.file_size != entry.file_size:
                    return False
                with left.open(old) as a, right.open(entry) as b:
                    while True:
                        check_cancelled()
                        x, y = a.read(1024 * 1024), b.read(1024 * 1024)
                        if x != y:
                            return False
                        if not x:
                            break
            return True
    except (OSError, KeyError, ValueError, zipfile.BadZipFile, RuntimeError):
        return False


def _ensure_output_directory(root):
    """Never reuse an unknown output directory or overwrite a foreign file."""
    root = Path(root)
    if not root.is_absolute() or not root.parent.is_dir():
        raise LegacyMetadataExportError('専用の保存先フォルダーが必要です。')
    _reject_links(root)
    marker = root / MARKER
    if root.exists():
        if not root.is_dir():
            raise LegacyMetadataExportError('保存先がフォルダーではありません。')
        if marker.is_file() and not marker.is_symlink() and marker.read_bytes() == MARKER_BYTES:
            return
        if any(root.iterdir()):
            raise LegacyMetadataExportError('所有を確認できないフォルダーには保存しません。')
    else:
        root.mkdir()
    _reject_links(marker)
    try:
        with marker.open('xb') as stream:
            stream.write(MARKER_BYTES)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        if marker.is_symlink() or marker.read_bytes() != MARKER_BYTES:
            raise LegacyMetadataExportError('保存先の所有標識が不正です。') from None


def export_frozen_legacy_metadata(db_path: Path, output_directory: Path, *, image_root=None, cancelled=None) -> LegacyMetadataExport:
    """Export the DB, metadata and optional image tree without source writes.

    A frozen standalone copy is mandatory. WAL/SHM-bearing live databases are
    rejected rather than opened with immutable=1 and silently losing WAL data.
    """
    if cancelled is not None and not callable(cancelled):
        raise LegacyMetadataExportError('エクスポートの取消条件が不正です。')

    def check_cancelled():
        if cancelled is not None and cancelled():
            raise LegacyMetadataExportError('終了要求によりエクスポートを中止しました。')

    check_cancelled()
    source = Path(db_path)
    output = Path(output_directory)
    if not source.is_absolute() or not source.is_file() or not output.is_absolute():
        raise LegacyMetadataExportError('明示された旧DBコピーと専用保存先が必要です。')
    _reject_links(source)
    images = None if image_root is None else Path(image_root)
    if images is not None:
        if not images.is_absolute() or not images.is_dir():
            raise LegacyMetadataExportError('画像の保存元フォルダーを確認できません。')
        _reject_links(images)
        if output == images or images in output.parents or output in images.parents:
            raise LegacyMetadataExportError('画像の保存元と保存先を分離してください。')
    if any(Path(str(source) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
        raise LegacyMetadataExportError('使用中の旧DBは不可です。停止した独立コピーを指定してください。')
    if output == source.parent or source.parent in output.parents:
        raise LegacyMetadataExportError('元DBと同じ保存階層には出力できません。')
    initial = source.stat()
    if not 0 < initial.st_size <= MAX_DB_BYTES:
        raise LegacyMetadataExportError('旧DBサイズが安全上限を超えています。')
    before = _digest(source)
    check_cancelled()
    _ensure_output_directory(output)
    published_result = None
    try:
        with tempfile.TemporaryDirectory(prefix='.pv-export-', dir=output) as temp:
            stage = Path(temp)
            copied = stage / 'pixiv_vault.db'
            metadata = stage / 'metadata.json'
            src = sqlite3.connect(sqlite_readonly_uri(source, immutable=True), uri=True, timeout=5)
            try:
                src.execute('PRAGMA query_only=ON')
                dest = sqlite3.connect(str(copied))
                try:
                    src.backup(dest, pages=128, progress=lambda *args: check_cancelled())
                    check_cancelled()
                    status = dest.execute('PRAGMA integrity_check').fetchone()
                    if status != ('ok',):
                        raise LegacyMetadataExportError('DBコピーの整合性を確認できません。')
                    dest.row_factory = sqlite3.Row
                    if 'works' not in {r[0] for r in dest.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
                        raise LegacyMetadataExportError('元の作品テーブルがありません。')
                    expected = dest.execute('SELECT COUNT(*) FROM works').fetchone()[0]
                    if type(expected) is not int or not 0 <= expected <= MAX_ROWS:
                        raise LegacyMetadataExportError('作品数が安全上限を超えています。')
                    count, encoded_bytes = 0, 0
                    with metadata.open('w', encoding='utf-8', newline='\n') as out:
                        out.write('[\n')
                        for row in dest.execute('SELECT * FROM works'):
                            if count % 1024 == 0:
                                check_cancelled()
                            encoded = json.dumps(dict(row), ensure_ascii=False, allow_nan=False,
                                                 separators=(',', ':'))
                            encoded_bytes += len(encoded.encode('utf-8'))
                            if encoded_bytes > MAX_JSON_BYTES:
                                raise LegacyMetadataExportError('作品メタデータが安全上限を超えています。')
                            out.write(('  ' if count == 0 else ',\n  ') + encoded)
                            count += 1
                        out.write('\n]\n')
                        out.flush()
                        os.fsync(out.fileno())
                    if count != expected:
                        raise LegacyMetadataExportError('DBとメタデータの件数が一致しません。')
                finally:
                    # sqlite3.Connection's context manager commits but does
                    # not close; an open handle prevents stage cleanup on Win.
                    dest.close()
            finally:
                src.close()

            name = 'PixivVault_Export_' + datetime.now().strftime('%Y%m%d_%H%M%S')
            name += '_' + uuid4().hex[:12] + '.zip'
            temporary_zip = stage / name
            image_proofs = []
            image_groups = {}
            image_entries = set()
            with zipfile.ZipFile(temporary_zip, 'w', compression=zipfile.ZIP_DEFLATED,
                                 compresslevel=6, allowZip64=True) as archive:
                for source_file, entry in ((copied, 'pixiv_vault.db'),
                                           (metadata, 'metadata.json')):
                    with source_file.open('rb') as incoming, archive.open(entry, 'w', force_zip64=True) as target:
                        while chunk := incoming.read(1024 * 1024):
                            check_cancelled()
                            target.write(chunk)
                if images is not None:
                    for image_file in sorted(images.rglob('*')):
                        check_cancelled()
                        _reject_links(image_file)
                        if image_file.is_dir():
                            continue
                        if not image_file.is_file():
                            raise LegacyMetadataExportError('画像の保存元に読み取れない項目があります。')
                        before_stat = image_file.stat()
                        digest = _digest(image_file)
                        relative = image_file.relative_to(images).as_posix()
                        key = (digest, before_stat.st_size)
                        if key not in image_groups:
                            entry = 'images/' + relative
                            verified = hashlib.sha256()
                            with image_file.open('rb') as incoming, archive.open(
                                    entry, 'w', force_zip64=True) as target:
                                while chunk := incoming.read(1024 * 1024):
                                    check_cancelled()
                                    verified.update(chunk)
                                    target.write(chunk)
                            if verified.hexdigest() != digest:
                                raise LegacyMetadataExportError('エクスポート中に画像が変更されました。')
                            image_groups[key] = {'sha256': digest, 'size': before_stat.st_size,
                                                 'entry': entry, 'paths': []}
                            image_entries.add(entry)
                        image_groups[key]['paths'].append(relative)
                        image_proofs.append((image_file, before_stat, digest))
                    archive.writestr('image-paths.json', _encode_image_paths(list(image_groups.values())))
                    archive.writestr('RESTORE_IMAGES.txt',
                        '画像を全パスに復元するには PixivVault NiceGUI の復元コマンドを使ってください。\n'
                        'python -m pixivvault_nicegui.legacy_metadata_export restore <ZIP> <新規フォルダー>\n')
            with zipfile.ZipFile(temporary_zip, 'r') as archive:
                expected = {'pixiv_vault.db', 'metadata.json'}
                if images is not None:
                    expected.update(image_entries)
                    expected.update({'image-paths.json', 'RESTORE_IMAGES.txt'})
                if (len(archive.namelist()) != len(expected) or
                        set(archive.namelist()) != expected or archive.testzip() is not None):
                    raise LegacyMetadataExportError('作成したZIPの検証に失敗しました。')
            with temporary_zip.open('r+b') as verified:
                verified.flush()
                os.fsync(verified.fileno())
            saved_zip_hash = _digest(temporary_zip)
            saved_zip_size = temporary_zip.stat().st_size
            saved_db_hash = _digest(copied)
            check_cancelled()
            current = source.stat()
            for image_file, previous, digest in image_proofs:
                check_cancelled()
                _reject_links(image_file)
                current_image = image_file.stat()
                if (current_image.st_size != previous.st_size or
                        current_image.st_mtime_ns != previous.st_mtime_ns or
                        current_image.st_ino != previous.st_ino or _digest(image_file) != digest):
                    raise LegacyMetadataExportError('エクスポート中に画像が変更されました。')
            if images is not None:
                current_files = set()
                for image_file in images.rglob('*'):
                    _reject_links(image_file)
                    if image_file.is_file():
                        current_files.add(image_file)
                if current_files != {p for p, _, _ in image_proofs}:
                    raise LegacyMetadataExportError('エクスポート中に画像の一覧が変更されました。')
            if (current.st_size != initial.st_size or current.st_mtime_ns != initial.st_mtime_ns
                    or current.st_ino != initial.st_ino or _digest(source) != before
                    or any(Path(str(source) + suffix).exists() for suffix in ('-wal','-shm','-journal'))):
                raise LegacyMetadataExportError('エクスポート中に旧DBが変更されました。')
            for existing in output.glob('PixivVault_Export_*.zip'):
                check_cancelled()
                if _same_archive_payload(existing, temporary_zip, check_cancelled):
                    return LegacyMetadataExport(existing, count, saved_db_hash,
                                                _digest(existing), existing.stat().st_size)
            published = output / name
            check_cancelled()
            # On Windows rename is atomic and refuses existing destinations,
            # including on volumes without hardlinks. On POSIX an exclusive
            # hardlink gives the same never-overwrite contract.
            if os.name == 'nt':
                os.rename(temporary_zip, published)
            else:
                os.link(temporary_zip, published)
            published_result = LegacyMetadataExport(published, count, saved_db_hash,
                                                   saved_zip_hash, saved_zip_size)
    except LegacyMetadataExportError:
        raise
    except (OSError, sqlite3.Error, UnicodeError, ValueError, TypeError, zipfile.BadZipFile) as exc:
        if published_result is not None:
            # Cleanup of the *owned staging directory* can fail after ZIP
            # publication. The committed ZIP still exists. Do not tell the UI
            # that a valid completed archive was never created; preserve a
            # separate warning so users can inspect residual staging files.
            try:
                if (published_result.path.is_file() and
                        published_result.path.stat().st_size == published_result.bytes_count and
                        _digest(published_result.path) == published_result.zip_sha256):
                    return replace(published_result,cleanup_warning=True)
            except OSError:
                pass
        raise LegacyMetadataExportError('DBとメタデータのエクスポートに失敗しました。') from exc
    if published_result is None:
        raise LegacyMetadataExportError('エクスポート完了を確認できませんでした。')
    return published_result


def restore_legacy_export(archive_path: Path, destination: Path) -> Path:
    """Restore a deduplicated export to a new directory; never replace data.

    Only archives produced by this exporter are accepted. Extraction is staged
    alongside the destination and the published directory is renamed last.
    """
    import re
    import shutil
    from pathlib import PurePosixPath

    archive_path, destination = Path(archive_path), Path(destination)
    if (not archive_path.is_absolute() or not destination.is_absolute()
            or destination.exists() or not destination.parent.is_dir()):
        raise LegacyMetadataExportError('新しい復元先フォルダーを指定してください。')
    _reject_links(archive_path)
    _reject_links(destination.parent)

    def safe_path(value):
        if type(value) is not str or not value or '\\' in value or ':' in value:
            raise LegacyMetadataExportError('ZIP内のパスが不正です。')
        parts = value.split('/')
        if (any(not part or part in ('.', '..') or part.endswith((' ', '.'))
                or any(ord(ch) < 32 for ch in part) for part in parts)
                or PurePosixPath(value).is_absolute()):
            raise LegacyMetadataExportError('ZIP内のパスが不正です。')
        if any(re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', part)
               for part in parts):
            raise LegacyMetadataExportError('ZIP内のパスが不正です。')
        return parts

    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            names = [entry.filename for entry in members]
            if len(names) != len(set(names)) or 'image-paths.json' not in names:
                raise LegacyMetadataExportError('画像の対応一覧を確認できません。')
            for info in members:
                safe_path(info.filename)
                if info.is_dir() or (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise LegacyMetadataExportError('ZIP内のリンクは復元できません。')
            manifest_info = archive.getinfo('image-paths.json')
            if manifest_info.file_size > MAX_IMAGE_PATHS_BYTES:
                raise LegacyMetadataExportError('画像の対応一覧が大きすぎます。')
            manifest = json.loads(archive.read(manifest_info))
            if (type(manifest) is not dict or set(manifest) != {'version', 'images'}
                    or type(manifest['version']) is not int or manifest['version'] != 1
                    or type(manifest['images']) is not list):
                raise LegacyMetadataExportError('画像の対応一覧が不正です。')
            groups, seen_paths, payloads = [], set(), set()
            for group in manifest['images']:
                if (type(group) is not dict or set(group) != {'sha256', 'size', 'entry', 'paths'}
                        or type(group['sha256']) is not str
                        or re.fullmatch(r'[a-f0-9]{64}', group['sha256']) is None
                        or type(group['size']) is not int or group['size'] < 0
                        or type(group['paths']) is not list or not group['paths']):
                    raise LegacyMetadataExportError('画像の対応一覧が不正です。')
                entry = group['entry']
                if (type(entry) is not str or not entry.startswith('images/')
                        or entry in payloads or entry not in names):
                    raise LegacyMetadataExportError('画像の対応一覧が不正です。')
                payloads.add(entry)
                paths = []
                for rel in group['paths']:
                    parts = safe_path(rel)
                    folded = '/'.join(parts).casefold()
                    if folded in seen_paths:
                        raise LegacyMetadataExportError('復元先の画像パスが重複しています。')
                    seen_paths.add(folded)
                    paths.append(parts)
                if entry != 'images/' + '/'.join(paths[0]):
                    raise LegacyMetadataExportError('画像の対応一覧が不正です。')
                if archive.getinfo(entry).file_size != group['size']:
                    raise LegacyMetadataExportError('画像のサイズが一致しません。')
                groups.append((group, paths))
            expected = {'pixiv_vault.db', 'metadata.json', 'image-paths.json', 'RESTORE_IMAGES.txt'} | payloads
            if set(names) != expected:
                raise LegacyMetadataExportError('ZIP内の項目が不正です。')
            with tempfile.TemporaryDirectory(prefix='.pv-restore-', dir=destination.parent) as temp:
                staged = Path(temp) / 'result'
                staged.mkdir()
                for name in ('pixiv_vault.db', 'metadata.json', 'image-paths.json', 'RESTORE_IMAGES.txt'):
                    with archive.open(name) as source, (staged / name).open('xb') as target:
                        shutil.copyfileobj(source, target, 1024 * 1024)
                for group, paths in groups:
                    first = staged / 'images' / Path(*paths[0])
                    first.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(group['entry']) as source, first.open('xb') as target:
                        shutil.copyfileobj(source, target, 1024 * 1024)
                    if first.stat().st_size != group['size'] or _digest(first) != group['sha256']:
                        raise LegacyMetadataExportError('画像の内容照合に失敗しました。')
                    for parts in paths[1:]:
                        target_path = staged / 'images' / Path(*parts)
                        target_path.parent.mkdir(parents=True, exist_ok=True)
                        with first.open('rb') as source, target_path.open('xb') as target:
                            shutil.copyfileobj(source, target, 1024 * 1024)
                        if _digest(target_path) != group['sha256']:
                            raise LegacyMetadataExportError('復元した画像の内容照合に失敗しました。')
                if destination.exists():
                    raise LegacyMetadataExportError('復元先が作成されたため停止しました。')
                os.rename(staged, destination)
    except LegacyMetadataExportError:
        raise
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
        raise LegacyMetadataExportError('ZIPを復元できませんでした。') from exc
    return destination


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='PixivVault export restoration')
    parser.add_argument('command', choices=('restore',))
    parser.add_argument('archive', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    restore_legacy_export(args.archive, args.destination)
