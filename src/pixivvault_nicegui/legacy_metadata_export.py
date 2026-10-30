"""Explicit read-only export of a frozen legacy DB and its works metadata.

This does not export images or touch the original database/ZIPs. The archive
is published only after SQLite copy, JSON, ZIP and source-stability checks
succeed. The output directory must be a dedicated, marked, owned folder.
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


class LegacyMetadataExportError(ValueError):
    """No successful export result exists for this request."""


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


def export_frozen_legacy_metadata(db_path: Path, output_directory: Path, *, cancelled=None) -> LegacyMetadataExport:
    """Produce exactly pixiv_vault.db and metadata.json, without source writes.

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
            with zipfile.ZipFile(temporary_zip, 'w', compression=zipfile.ZIP_DEFLATED,
                                 compresslevel=6, allowZip64=True) as archive:
                for source_file, entry in ((copied, 'pixiv_vault.db'),
                                           (metadata, 'metadata.json')):
                    with source_file.open('rb') as incoming, archive.open(entry, 'w', force_zip64=True) as target:
                        while chunk := incoming.read(1024 * 1024):
                            check_cancelled()
                            target.write(chunk)
            with zipfile.ZipFile(temporary_zip, 'r') as archive:
                if set(archive.namelist()) != {'pixiv_vault.db', 'metadata.json'} or archive.testzip() is not None:
                    raise LegacyMetadataExportError('作成したZIPの検証に失敗しました。')
            with temporary_zip.open('r+b') as verified:
                verified.flush()
                os.fsync(verified.fileno())
            saved_zip_hash = _digest(temporary_zip)
            saved_zip_size = temporary_zip.stat().st_size
            saved_db_hash = _digest(copied)
            check_cancelled()
            current = source.stat()
            if (current.st_size != initial.st_size or current.st_mtime_ns != initial.st_mtime_ns
                    or current.st_ino != initial.st_ino or _digest(source) != before
                    or any(Path(str(source) + suffix).exists() for suffix in ('-wal','-shm','-journal'))):
                raise LegacyMetadataExportError('エクスポート中に旧DBが変更されました。')
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
