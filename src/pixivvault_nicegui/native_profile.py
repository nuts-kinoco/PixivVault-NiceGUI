"""Explicit native launch profile; never reads Cookie bytes or launches a client.

The settings file lives only under a newly created/marked profile directory.
A selected save parent is a *plan* until the next native startup calls
``provision_storage``. Existing CLI and synthetic fixtures do not use this
module unless their launcher explicitly opts in.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
import os
from pathlib import Path
import sqlite3
import stat
from threading import RLock
from typing import Any

from .fs_paths import sqlite_readonly_uri
from .media_jobs import validate_media_roots, validate_readonly_boundaries


PROFILE_MARKER = '.pixivvault-native-profile.json'
STORAGE_MARKER = '.pixivvault-native-storage.json'
PROFILE_IDENTITY = b'{"application":"pixivvault-nicegui","purpose":"native-profile","version":1}\n'
STORAGE_IDENTITY = b'{"application":"pixivvault-nicegui","purpose":"native-storage-parent","version":1}\n'
PROFILE_DB = 'profile.sqlite3'
STORAGE_FOLDER = 'PixivVault-NiceGUI'
ROOT_NAMES = {
    'preferences_root': 'preferences',
    'recommendation_preferences_root': 'recommendation-preferences',
    'pixiv_download_root': 'artwork-originals',
    'novel_download_root': 'novels',
    'ugoira_root': 'ugoira',
    'legacy_zip_export_root': 'legacy-zip-exports',
    'discovery_root': 'discovery',
    'following_sync_root': 'following-sync',
    'image_cache_root': 'image-cache',
    'external_vault_root': 'external-vault',
    'save_export_root': 'export-staging',
    'similarity_index_root': 'similarity-index',
}
_UNSET = object()


class NativeProfileError(ValueError):
    """User-safe fixed diagnostic; never includes a filesystem path or secret."""


class NativeProfileConflict(NativeProfileError):
    """The same profile was changed by another window/process."""


class NativeProfileReferenceUnavailable(NativeProfileError):
    """A previously selected *reference* went missing; settings remain intact.

    The native launcher may offer an offline repair page for this category.
    Unknown ownership, reparse, invalid profile schema or overlap are different
    errors and must never be silently treated as a missing selection.
    """


class NativeProfileSourceUnavailable(NativeProfileError):
    """An existing legacy reference is not a standalone frozen copy."""


@dataclass(frozen=True, slots=True)
class NativeProfileSnapshot:
    revision: int
    cookie_file: Path | None = None
    save_parent: Path | None = None
    library_readonly_db: Path | None = None
    library_image_root: Path | None = None
    similarity_model_file: Path | None = None
    similarity_provider: str = 'auto'
    similarity_auto: bool = False


def _absolute(path: Any, *, kind: str) -> Path | None:
    if path is None:
        return None
    if not isinstance(path, (str, Path)) or not str(path) or '\x00' in str(path):
        raise NativeProfileError('選択したパスが不正です。')
    value = Path(path)
    if not value.is_absolute():
        raise NativeProfileError('絶対パスを選択してください。')
    if '..' in value.parts:
        raise NativeProfileError('親ディレクトリ参照は使用できません。')
    if value == value.parent:
        raise NativeProfileError('ドライブの直下は設定できません。')
    _assert_chain(value, kind=kind)
    return value


def _assert_chain(path: Path, *, kind: str = 'directory') -> None:
    """Reject every known symlink/junction, not just the final component."""
    for component in (path, *path.parents):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            raise NativeProfileError('選択先を確認できません。') from None
        if (stat.S_ISLNK(info.st_mode) or
                getattr(info, 'st_file_attributes', 0) & 0x400):
            raise NativeProfileError('リンクやreparse pointは使用できません。')
        if component == path and kind == 'file':
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise NativeProfileError('通常のファイルを選択してください。')
        elif not stat.S_ISDIR(info.st_mode):
            raise NativeProfileError('保存先はフォルダを選択してください。')


def _existing(path: Path | None, *, kind: str) -> None:
    if path is None:
        return
    _assert_chain(path, kind=kind)
    if not path.exists():
        raise NativeProfileReferenceUnavailable(
            '選択したファイルが見つかりません。' if kind == 'file'
            else '選択した保存先が見つかりません。')
    if kind == 'file' and not path.is_file():
        raise NativeProfileError('選択したファイルがありません。')
    if kind == 'directory' and not path.is_dir():
        raise NativeProfileError('選択した保存先がありません。')


def _owned_marker(root: Path, marker: str, identity: bytes) -> None:
    _assert_chain(root)
    path = root / marker
    _assert_chain(path, kind='file')
    try:
        if (not path.is_file() or path.stat().st_size != len(identity) or
                path.read_bytes() != identity):
            raise NativeProfileError('このフォルダは新アプリ専用領域ではありません。')
    except OSError:
        raise NativeProfileError('専用領域の所有情報を確認できません。') from None


def _create_marker(root: Path, marker: str, identity: bytes) -> None:
    """Never mark an existing unowned directory as if this app created it."""
    _assert_chain(root)
    if root.exists():
        _owned_marker(root, marker, identity)
        return
    if not root.parent.is_dir():
        raise NativeProfileError('専用領域の親フォルダがありません。')
    try:
        root.mkdir(exist_ok=False)
        _assert_chain(root)
        with (root / marker).open('xb') as stream:
            stream.write(identity)
            stream.flush()
            os.fsync(stream.fileno())
    except (OSError, ValueError):
        raise NativeProfileError('専用領域を作成できませんでした。') from None


def _check_config(snapshot: NativeProfileSnapshot, profile_root: Path,
                  *, live_paths: bool) -> None:
    if not isinstance(snapshot, NativeProfileSnapshot) or type(snapshot.revision) is not int or snapshot.revision < 0:
        raise NativeProfileError('保存された設定の形式が不正です。')
    if (snapshot.similarity_provider not in ('auto', 'cuda', 'cpu')
            or type(snapshot.similarity_auto) is not bool):
        raise NativeProfileError('解析の実行方式が不正です。')
    paths = {
        'cookie_file': 'file', 'save_parent': 'directory',
        'library_readonly_db': 'file', 'library_image_root': 'directory',
        'similarity_model_file': 'file',
    }
    for name, kind in paths.items():
        value = getattr(snapshot, name)
        if value is not None:
            _absolute(value, kind=kind)
            if live_paths:
                _existing(value, kind=kind)
    if (snapshot.library_readonly_db is None) != (snapshot.library_image_root is None):
        raise NativeProfileError('旧DBと旧画像は両方を明示してください。')
    if live_paths and snapshot.library_readonly_db is not None:
        from .library_repository import require_frozen_library_copy
        try:
            require_frozen_library_copy(snapshot.library_readonly_db)
        except (OSError, ValueError):
            raise NativeProfileSourceUnavailable('旧DBは使用中でない凍結コピーを選択してください。') from None
    if snapshot.cookie_file is not None:
        validate_media_roots(profile_root, snapshot.cookie_file.parent)
    if snapshot.similarity_model_file is not None:
        if snapshot.save_parent is None:
            raise NativeProfileError('解析には専用の保存先が必要です。')
        validate_media_roots(profile_root, snapshot.similarity_model_file.parent)
        # A saved parent alone has no image to analyze. Do not silently
        # provision a writeable inference ledger for an unbound source.
        # Existing externally-inconsistent profiles still need to be readable
        # by peek() so the native offline repair screen can be reached.
        if (live_paths and snapshot.cookie_file is None
                and snapshot.library_readonly_db is None):
            raise NativeProfileReferenceUnavailable(
                '解析する画像の参照元がありません。Cookieまたは旧ライブラリを選択してください。')
    elif live_paths and snapshot.similarity_auto:
        # A stale stored profile uses the same repair path; a new update is
        # refused transactionally without changing its prior revision.
        raise NativeProfileReferenceUnavailable('自動解析にはモデルの選択が必要です。')
    if live_paths and snapshot.save_parent is not None:
        plan_storage(snapshot, profile_root)


def plan_storage(snapshot: NativeProfileSnapshot, profile_root: Path) -> dict[str, Path]:
    """Compute non-overlapping launch roots without creating anything there."""
    if snapshot.save_parent is None:
        return {}
    parent = _absolute(snapshot.save_parent, kind='directory')
    _existing(parent, kind='directory')
    profile = _absolute(profile_root, kind='directory')
    base = parent / STORAGE_FOLDER
    _assert_chain(base)
    if base.exists():
        _owned_marker(base, STORAGE_MARKER, STORAGE_IDENTITY)
    paths = {name: base / suffix for name, suffix in ROOT_NAMES.items()}
    for path in paths.values():
        _assert_chain(path)
    readonly = tuple(value for value in (
        snapshot.library_image_root,
        snapshot.library_readonly_db.parent if snapshot.library_readonly_db else None,
        snapshot.cookie_file.parent if snapshot.cookie_file else None,
        snapshot.similarity_model_file.parent if snapshot.similarity_model_file else None,
    ) if value is not None)
    try:
        validate_media_roots(*paths.values())
        validate_readonly_boundaries((base, profile), readonly)
        validate_media_roots(base, profile)
    except ValueError:
        raise NativeProfileError('設定領域、元データ、保存先が重なっています。') from None
    return paths


def provision_storage(snapshot: NativeProfileSnapshot, profile_root: Path) -> dict[str, Path]:
    """Explicit next-start operation; own only the empty managed parent marker.

    App service owners later create each separate output root with their own
    existing markers. A settings save never calls this function.
    """
    _check_config(snapshot, _absolute(profile_root, kind='directory'), live_paths=True)
    paths = plan_storage(snapshot, profile_root)
    if paths:
        base = next(iter(paths.values())).parent
        _create_marker(base, STORAGE_MARKER, STORAGE_IDENTITY)
        plan_storage(snapshot, profile_root)
    return paths


def native_kwargs(snapshot: NativeProfileSnapshot, profile_root: Path) -> dict:
    """Pure, opt-in flat RunConfig kwargs for the native launcher/--check.

    Does not create roots, read Cookie bytes or override explicit CLI inputs.
    The caller performs next-start ``provision_storage`` only after approval
    and a fresh revision comparison.
    """
    if not isinstance(snapshot, NativeProfileSnapshot):
        raise NativeProfileError('profile設定値が不正です。')
    _check_config(snapshot, _absolute(profile_root, kind='directory'), live_paths=True)
    plan = plan_storage(snapshot, profile_root)
    values = {}
    if snapshot.cookie_file is not None:
        values['pixiv_cookie_file'] = snapshot.cookie_file
    if plan:
        values['preferences_root'] = plan['preferences_root']
        if snapshot.cookie_file is not None:
            for name in ('pixiv_download_root','novel_download_root','ugoira_root',
                         'legacy_zip_export_root','discovery_root','image_cache_root',
                         'external_vault_root','save_export_root',
                         'recommendation_preferences_root','following_sync_root'):
                values[name] = plan[name]
        elif snapshot.library_readonly_db is not None:
            values['recommendation_preferences_root'] = plan['recommendation_preferences_root']
    if snapshot.library_readonly_db is not None:
        values['library_readonly_db'] = snapshot.library_readonly_db
        values['library_image_root'] = snapshot.library_image_root
    if plan and snapshot.similarity_model_file is not None:
        values.update(similarity_index_root=plan['similarity_index_root'],
                      similarity_model_file=snapshot.similarity_model_file,
                      similarity_provider=snapshot.similarity_provider,
                      similarity_auto=snapshot.similarity_auto)
    return values


class NativeProfileStore:
    """SQLite CAS transactions inside an explicitly owned profile directory."""

    def __init__(self, root: Path):
        self.root = _absolute(root, kind='directory')
        _create_marker(self.root, PROFILE_MARKER, PROFILE_IDENTITY)
        self.path = self.root / PROFILE_DB
        _assert_chain(self.path, kind='file')
        existed = self.path.exists()
        self._lock = RLock()
        self._closed = False
        self._connection = None
        try:
            self._connection = sqlite3.connect(str(self.path), timeout=4,
                                               isolation_level=None, check_same_thread=False)
            if not existed:
                self._connection.execute('BEGIN IMMEDIATE')
                try:
                    self._connection.execute('CREATE TABLE native_profile_identity (purpose TEXT NOT NULL)')
                    self._connection.execute('INSERT INTO native_profile_identity VALUES (?)', ('native-profile-v1',))
                    self._connection.execute('''CREATE TABLE native_profile_state
                        (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL,
                         cookie_file TEXT, save_parent TEXT,
                         library_readonly_db TEXT, library_image_root TEXT)''')
                    self._connection.execute('INSERT INTO native_profile_state VALUES (1,0,NULL,NULL,NULL,NULL)')
                    self._connection.execute('COMMIT')
                except Exception:
                    self._connection.execute('ROLLBACK')
                    raise
            self.snapshot()
        except Exception:
            if self._connection is not None:
                self._connection.close()
            self._closed = True
            raise NativeProfileError('専用profileを読み込めませんでした。旧設定は変更していません。') from None

    @staticmethod
    def _read_row(connection, root: Path) -> NativeProfileSnapshot:
        identity = connection.execute('SELECT purpose FROM native_profile_identity').fetchall()
        rows = connection.execute('''SELECT revision,cookie_file,save_parent,
            library_readonly_db,library_image_root FROM native_profile_state WHERE id=1''').fetchall()
        if identity != [('native-profile-v1',)] or len(rows) != 1:
            raise NativeProfileError('profile所有情報が不正です。')
        revision, cookie, parent, old_db, old_image = rows[0]
        if type(revision) is not int or revision < 0 or any(
                value is not None and (type(value) is not str or not value)
                for value in (cookie, parent, old_db, old_image)):
            raise NativeProfileError('profile設定値が不正です。')
        model, provider, automatic = None, 'auto', 0
        exists = connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='native_inference_state'").fetchone()
        if exists:
            settings = connection.execute('SELECT model_file,provider,automatic FROM native_inference_state WHERE id=1').fetchall()
            if len(settings) != 1:
                raise NativeProfileError('解析設定が不正です。')
            model, provider, automatic = settings[0]
            if (model is not None and (type(model) is not str or not model)
                    or type(provider) is not str or automatic not in (0, 1)):
                raise NativeProfileError('解析設定が不正です。')
        snapshot = NativeProfileSnapshot(revision, *(Path(p) if p is not None else None
                                                       for p in (cookie,parent,old_db,old_image)),
                                         Path(model) if model is not None else None,
                                         provider, bool(automatic))
        _check_config(snapshot, root, live_paths=False)
        return snapshot

    def _row(self) -> NativeProfileSnapshot:
        return self._read_row(self._connection, self.root)

    @classmethod
    def peek(cls, root: Path) -> NativeProfileSnapshot:
        """Read-only native startup inspection; never creates a profile or DB.

        Returns an empty unconfigured snapshot if the explicit directory does
        not exist. Rejects unknown existing folders/files instead of adopting.
        """
        folder = _absolute(root, kind='directory')
        if not folder.exists():
            return NativeProfileSnapshot(0)
        _owned_marker(folder, PROFILE_MARKER, PROFILE_IDENTITY)
        path = folder / PROFILE_DB
        _assert_chain(path, kind='file')
        if not path.is_file():
            raise NativeProfileError('専用profileを読み込めませんでした。旧設定は変更していません。')
        # A copied/in-flight SQLite file with WAL/journal sidecars cannot be
        # treated as a complete immutable snapshot. --check never recovers it.
        if any(Path(str(path) + suffix).exists() or
               Path(str(path) + suffix).is_symlink()
               for suffix in ('-wal', '-shm', '-journal')):
            raise NativeProfileError('profileの読取専用状態を確認できませんでした。')
        try:
            with closing(sqlite3.connect(sqlite_readonly_uri(path, immutable=True),
                                         uri=True, timeout=4)) as connection:
                return cls._read_row(connection, folder)
        except Exception:
            raise NativeProfileError('専用profileを読み込めませんでした。旧設定は変更していません。') from None

    def snapshot(self) -> NativeProfileSnapshot:
        with self._lock:
            if self._closed:
                raise NativeProfileError('profileは閉じられています。')
            try:
                self._connection.execute('BEGIN')
                result = self._row()
                self._connection.execute('COMMIT')
                return result
            except Exception:
                self._connection.execute('ROLLBACK')
                raise NativeProfileError('profileを読み込めませんでした。旧設定は保持しています。') from None

    def update(self, *, cookie_file=_UNSET, save_parent=_UNSET, expected_revision: int,
               library_readonly_db=_UNSET, library_image_root=_UNSET,
               similarity_model_file=_UNSET, similarity_provider=_UNSET,
               similarity_auto=_UNSET) -> NativeProfileSnapshot:
        if type(expected_revision) is not int or expected_revision < 0:
            raise NativeProfileError('profileの世代が不正です。')
        with self._lock:
            if self._closed:
                raise NativeProfileError('profileは閉じられています。')
            try:
                self._connection.execute('BEGIN IMMEDIATE')
                previous = self._row()
                if previous.revision != expected_revision:
                    raise NativeProfileConflict('別の画面で変更されています。再読込してください。')
                candidate = NativeProfileSnapshot(
                    expected_revision,
                    previous.cookie_file if cookie_file is _UNSET else _absolute(cookie_file, kind='file'),
                    previous.save_parent if save_parent is _UNSET else _absolute(save_parent, kind='directory'),
                    previous.library_readonly_db if library_readonly_db is _UNSET else _absolute(library_readonly_db, kind='file'),
                    previous.library_image_root if library_image_root is _UNSET else _absolute(library_image_root, kind='directory'),
                    previous.similarity_model_file if similarity_model_file is _UNSET else _absolute(similarity_model_file, kind='file'),
                    previous.similarity_provider if similarity_provider is _UNSET else similarity_provider,
                    previous.similarity_auto if similarity_auto is _UNSET else similarity_auto)
                _check_config(candidate, self.root, live_paths=True)
                if candidate == previous:
                    self._connection.execute('COMMIT')
                    return previous
                self._connection.execute('''UPDATE native_profile_state SET revision=?,
                    cookie_file=?,save_parent=?,library_readonly_db=?,library_image_root=? WHERE id=1''',
                    (expected_revision+1, *(str(value) if value is not None else None
                                            for value in (candidate.cookie_file,candidate.save_parent,
                                                          candidate.library_readonly_db,candidate.library_image_root))))
                # Separate optional table keeps old profile rows readable. Peek
                # never creates/migrates it; both writes share this transaction.
                self._connection.execute('''CREATE TABLE IF NOT EXISTS native_inference_state
                    (id INTEGER PRIMARY KEY CHECK(id=1), model_file TEXT,
                     provider TEXT NOT NULL, automatic INTEGER NOT NULL)''')
                self._connection.execute('INSERT OR REPLACE INTO native_inference_state VALUES(1,?,?,?)',
                    (str(candidate.similarity_model_file) if candidate.similarity_model_file else None,
                     candidate.similarity_provider, int(candidate.similarity_auto)))
                self._connection.execute('COMMIT')
                return NativeProfileSnapshot(expected_revision+1, candidate.cookie_file,
                    candidate.save_parent, candidate.library_readonly_db,candidate.library_image_root,
                    candidate.similarity_model_file,candidate.similarity_provider,candidate.similarity_auto)
            except NativeProfileConflict:
                self._connection.execute('ROLLBACK')
                raise
            except NativeProfileError:
                self._connection.execute('ROLLBACK')
                raise  # This module's bounded diagnostics never include secrets.
            except Exception:
                self._connection.execute('ROLLBACK')
                raise NativeProfileError('保存できませんでした。旧設定は変更していません。') from None

    def native_kwargs(self, snapshot: NativeProfileSnapshot | None = None) -> dict:
        """Opt-in launcher input; no implicit config/CLI overrides or I/O."""
        return native_kwargs(snapshot if snapshot is not None else self.snapshot(), self.root)

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                self._connection.close()
