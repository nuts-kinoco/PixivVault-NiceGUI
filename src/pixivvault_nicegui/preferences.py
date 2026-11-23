"""Original desktop preferences in a separate, marked, transactional sidecar.

No original settings DB is opened, imported or written. Consumers choose which
preferences they support; the view does not present an inert save as applied.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from threading import RLock
import sqlite3
import json


@dataclass(frozen=True)
class PreferenceSpec:
    key: str
    label: str
    default: str
    choices: tuple[str, ...]
    group: str


SPECS = (
    PreferenceSpec('automation_nav_hint_seen', 'automation navigation hint', '0', ('0', '1'), 'ui_internal'),
    PreferenceSpec('local_tagger_path', 'model path', r'S:\EMAKINOCO-Models\tagger\wd-swinv2-tagger-v3', (), 'local_tagger_internal'),
    PreferenceSpec('local_tagger_enabled', 'enabled', '1', ('0','1'), 'local_tagger_internal'),
    PreferenceSpec('local_tagger_receipt', 'receipt', '', (), 'local_tagger_internal'),
    PreferenceSpec('grid_card_size_mode', 'カードサイズ', 'large', ('large','medium','small'), '表示'),
    PreferenceSpec('use_work_folder','作品ごとのフォルダーを使う','0',('0','1'),'保存'),
    PreferenceSpec('nana7mi_shaft_refresh_interval_hours', 'Nana7mi借用アカウント自動更新間隔', '24', ('1','6','12','24','48'), '保存'),
    PreferenceSpec('novel_save_format', '小説の保存形式', 'epub', ('epub','txt','both'), '保存'),
    PreferenceSpec('ugoira_save_format', 'うごイラの保存形式', 'gif', ('gif','mp4','folder'), '保存'),
    PreferenceSpec('zip_all_after_download', '作者のダウンロード完了後ZIPにする', '0', ('0','1'), '保存'),
    PreferenceSpec('bookmark_download_mode', 'ブックマークの保存方法', 'direct', ('direct','link'), '保存'),
    PreferenceSpec('bookmark_auto_refresh', 'マイブックマークを開いた時に自動更新', '1', ('0','1'), '通信'),
    PreferenceSpec('legacy_novel_thumbnail_auto', '旧ライブラリの小説サムネイルを自動取得', '1', ('0','1'), '通信'),
    PreferenceSpec('pixiv_actions_enabled', 'Pixiv操作（ブクマ・フォロー）', '0', ('0','1'), '通信'),
    PreferenceSpec('extension_bridge_enabled', 'ブラウザ拡張機能の受信（Pixivページからの保存指示・Cookie同期）', '1', ('0','1'), '通信'),
    PreferenceSpec('download_interval', 'ダウンロード待機時間（秒）', '0.6', ('0.3','0.4','0.5','0.6','1.0','1.5'), '通信'),
    PreferenceSpec('api_retry_count', '通信エラー時リトライ上限回数', '3', ('1','3','5','10'), '通信'),
    PreferenceSpec('api_retry_wait', '429レート制限時待機時間（秒）', '5.0', ('3.0','5.0','10.0','30.0'), '通信'),
    # 既定は ON（本人の裁定）。元 gui.py:1789 の meta_backfill_enabled は既定 OFF だった。
    PreferenceSpec('meta_backfill_enabled', 'メタデータ（ブックマーク数など）をバックグラウンドで自動的に埋める', '1', ('0','1'), '作品メタデータ'),
    # 既定は ON（元 gui.py:2032 の embed_auto_on_download も既定 ON）。通信はしない。
    PreferenceSpec('embed_auto_on_download', 'ダウンロードした画像を、バックグラウンドで自動的に推論する', '1', ('0','1'), '絵柄・構図の推論'),
    PreferenceSpec('visual_preference_enabled', '視覚学習で候補の並び順を補正する', '0', ('0','1'), '絵柄・構図の推論'),
    PreferenceSpec('visual_preference_mode', '視覚学習の実行タイミング', 'manual', ('idle','manual'), '絵柄・構図の推論'),
    PreferenceSpec('visual_preference_thumb_prefetch', '未表示候補のサムネイルを先読みして解析する', '0', ('0','1'), '絵柄・構図の推論'),
    PreferenceSpec('discovery_cache_limit_mode', '学習: 先読みキャッシュ上限指定方法', 'bytes', ('bytes','count'), 'キャッシュ'),
    PreferenceSpec('discovery_cache_size_mb', '学習: 先読みキャッシュ上限（MB）', '512', ('512','1024','2048','4096'), 'キャッシュ'),
    PreferenceSpec('discovery_cache_size_count', '学習: 先読みキャッシュ上限（枚）', '100', ('50','100','200','500','1000'), 'キャッシュ'),
    PreferenceSpec('pixiv_shared_image_cache_size_mb', '全タブ共有画像キャッシュ上限（MB）', '1024', ('512','1024','2048','4096','8192','10240','20480'), 'キャッシュ'),
    PreferenceSpec('pixiv_disk_thumbnail_cache_size_gb', '全タブ共有ディスクキャッシュ上限（GB）', '12', ('4','8','12','16','24','32'), 'キャッシュ'),
    PreferenceSpec('minimize_to_tray', '最小化時にタスクトレイに格納', '1', ('0','1'), 'Windows'),
    PreferenceSpec('enable_notifications', '通知', '1', ('0','1'), 'Windows'),
)
SPEC_BY_KEY = MappingProxyType({spec.key: spec for spec in SPECS})


class PreferencesError(RuntimeError):
    pass


class PreferencesConflict(PreferencesError):
    """Another window changed settings; keep the draft and require reload."""


@dataclass(frozen=True)
class PreferencesSnapshot:
    revision: int
    values: Mapping[str,str]

    def get_setting(self, key, default=None):
        return self.values.get(key, default)


class PreferencesStore:
    """Explicit dedicated path only; existing unmarked SQLite files are refused."""

    def __init__(self, path):
        self.path = Path(path).absolute()
        # stat() follows Windows junctions and observes the target directory,
        # losing FILE_ATTRIBUTE_REPARSE_POINT. Reject the path component itself
        # *before* mkdir or SQLite could create WAL/journal files elsewhere.
        def reject_links():
            for node in (self.path, *self.path.parents):
                try:
                    metadata = node.lstat()
                except FileNotFoundError:
                    continue
                except OSError:
                    raise PreferencesError('設定保存先の安全性を確認できません。') from None
                if node.is_symlink() or getattr(metadata, 'st_file_attributes', 0) & 0x400:
                    raise PreferencesError('設定保存先にリンクは使用できません。')
        reject_links()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        reject_links()
        existed = self.path.exists()
        self._lock = RLock()
        self._closed = False
        self._local_tagger_listeners = set()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        try:
            self._conn.execute('PRAGMA busy_timeout=5000')
            if existed:
                marker = self._conn.execute('SELECT kind FROM preferences_identity').fetchone()
                if marker != ('pixivvault-nicegui-preferences-v1',):
                    raise PreferencesError('専用設定ファイルではありません。')
            else:
                self._conn.executescript("BEGIN IMMEDIATE; CREATE TABLE preferences_identity(kind TEXT NOT NULL); INSERT INTO preferences_identity VALUES('pixivvault-nicegui-preferences-v1'); CREATE TABLE preferences_revision(value INTEGER NOT NULL); INSERT INTO preferences_revision VALUES(0); CREATE TABLE preferences_values(key TEXT PRIMARY KEY,value TEXT NOT NULL); COMMIT;")
            self.snapshot()
        except Exception:
            self._conn.close()
            self._closed = True
            raise PreferencesError('設定ファイルを読み込めません。') from None

    def snapshot(self):
        with self._lock:
            if self._closed:
                raise PreferencesError('設定は閉じられています。')
            self._conn.execute('BEGIN')
            try:
                revision = self._conn.execute('SELECT value FROM preferences_revision').fetchall()
                if len(revision) != 1 or type(revision[0][0]) is not int or revision[0][0] < 0:
                    raise PreferencesError('設定世代が不正です。')
                values = {s.key:s.default for s in SPECS}
                for key,value in self._conn.execute('SELECT key,value FROM preferences_values'):
                    if key == 'grid_card_size_mode' and value not in SPEC_BY_KEY[key].choices:
                        continue
                    self._validate(key,value)
                    values[key] = value
                self._conn.execute('COMMIT')
                return PreferencesSnapshot(revision[0][0], MappingProxyType(values))
            except Exception:
                self._conn.execute('ROLLBACK')
                raise PreferencesError('設定ファイルを読み込めません。') from None

    @staticmethod
    def _validate(key,value):
        if key in ('local_tagger_path', 'local_tagger_receipt') and type(value) is str and len(value) <= 32767 and '\x00' not in value:
            return
        if key not in SPEC_BY_KEY or type(value) is not str or value not in SPEC_BY_KEY[key].choices:
            raise PreferencesError('設定値が選択肢と一致しません。')

    def update(self, changes, *, expected_revision):
        changes = dict(changes)
        if type(expected_revision) is not int or expected_revision < 0:
            raise PreferencesError('設定世代が不正です。')
        for key,value in changes.items():
            self._validate(key,value)
        with self._lock:
            if self._closed:
                raise PreferencesError('設定は閉じられています。')
            tagger_keys = changes.keys() & {'local_tagger_enabled', 'local_tagger_path'}
            before = self.snapshot().values if tagger_keys and self._local_tagger_listeners else None
            self._conn.execute('BEGIN IMMEDIATE')
            try:
                revision = self._conn.execute('SELECT value FROM preferences_revision').fetchone()[0]
                if revision != expected_revision:
                    raise PreferencesConflict('別の画面で設定が変わりました。入力を確認して再読込してください。')
                for key,value in changes.items():
                    self._conn.execute('INSERT INTO preferences_values VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,value))
                if changes:
                    self._conn.execute('UPDATE preferences_revision SET value=value+1')
                self._conn.execute('COMMIT')
            except Exception:
                self._conn.execute('ROLLBACK')
                raise
            if before is not None and any(changes[key] != before[key] for key in tagger_keys):
                for invalidate in self._local_tagger_listeners:
                    invalidate()
            return self.snapshot()

    def subscribe_local_tagger_settings(self, invalidate):
        # Listeners only invalidate in-memory state. They run before update
        # releases the preferences lock, including for a direct set_setting.
        with self._lock:
            self._local_tagger_listeners.add(invalidate)
        def unsubscribe():
            with self._lock:
                self._local_tagger_listeners.discard(invalidate)
        return unsubscribe

    def get_setting(self,key,default=None):
        return self.snapshot().get_setting(key,default)

    def read_tagger_verification(self):
        with self._lock:
            if self._closed:
                raise PreferencesError('設定は閉じられています。')
            self._conn.execute('CREATE TABLE IF NOT EXISTS local_tagger_verification (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)')
            row = self._conn.execute('SELECT payload FROM local_tagger_verification WHERE id=1').fetchone()
            if row is None:
                return {}
            try:
                result = json.loads(row[0])
                return result if isinstance(result, dict) else {}
            except (ValueError, TypeError):
                return {}

    def write_tagger_verification(self, state):
        payload = json.dumps(state, ensure_ascii=False, separators=(',', ':'))
        with self._lock:
            if self._closed:
                raise PreferencesError('設定は閉じられています。')
            self._conn.execute('CREATE TABLE IF NOT EXISTS local_tagger_verification (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)')
            self._conn.execute('INSERT INTO local_tagger_verification VALUES (1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload', (payload,))

    def set_setting(self,key,value,*,expected_revision=None):
        if expected_revision is None:
            expected_revision = self.snapshot().revision
        return self.update({key:value},expected_revision=expected_revision)

    def close(self):
        with self._lock:
            if not self._closed:
                self._closed=True
                self._conn.close()


@dataclass(frozen=True)
class RequestPreferences:
    """Original request settings for bounded GET retry and shared admission.

    retry_limit is total HTTP tries, not additional JobService attempts.
    RequestRetryPolicy consumes these values within the current operation;
    Phase10 immediate global-failure blocking, stop and deadlines take priority.
    """
    interval_seconds: float
    retry_limit: int
    rate_limit_wait_seconds: float

    @classmethod
    def from_store(cls, store):
        snapshot=store.snapshot()
        return cls(float(snapshot.values['download_interval']),int(snapshot.values['api_retry_count']),float(snapshot.values['api_retry_wait']))

    def delay_before_request(self, *, last_started, now, deadline):
        wait = 0.0 if last_started is None else max(0.0,last_started+self.interval_seconds-now)
        if now+wait >= deadline:
            raise PreferencesError('通信の制限時間内に次の要求を開始できません。')
        return wait
