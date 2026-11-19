"""独立入口。既定は合成デモ、外部読取と管理保存は明示opt-inのみ。"""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import multiprocessing
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True, slots=True)
class RunConfig:
    host: str = "127.0.0.1"
    port: int = 8089
    native: bool = False
    check_only: bool = False
    learning_sandbox: Path | None = None
    library_readonly_db: Path | None = None
    library_image_root: Path | None = None
    pixiv_cookie_file: Path | None = None
    download_sandbox: Path | None = None
    pixiv_download_root: Path | None = None
    failed_queue_readonly_db: Path | None = None
    legacy_zip_export_root: Path | None = None
    legacy_diff_image_root: Path | None = None
    novel_fixture_root: Path | None = None
    novel_download_root: Path | None = None
    ugoira_root: Path | None = None
    ugoira_fixture_root: Path | None = None
    ugoira_codec_python: Path | None = None
    recommendation_preferences_root: Path | None = None
    preferences_root: Path | None = None
    discovery_root: Path | None = None
    external_vault_root: Path | None = None
    image_cache_root: Path | None = None
    startup_uri: str | None = None
    extension_bridge: bool = False
    save_export_root: Path | None = None
    bookmark_account_id: str | None = None
    profile_root: Path | None = None
    profile_revision: int | None = None
    profile_reference_unavailable: bool = False
    following_sync_root: Path | None = None
    enable_pixiv_actions: bool = False
    similarity_index_root: Path | None = None
    similarity_model_file: Path | None = None
    similarity_provider: str = 'auto'
    similarity_auto: bool = False
    extension_bridge_auto: bool = False
    startup_tab: str | None = None
    legacy_copy_root: Path | None = None


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("ポートは整数で指定してください") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("ポートは1〜65535で指定してください")
    return port


def _sandbox_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise argparse.ArgumentTypeError("学習デモの保存先は専用フォルダーの絶対パスで指定してください")
    return path


def _validate_managed_paths(root: Path, cookie: Path) -> None:
    """Keep managed storage disjoint from the credential/source-app directory."""
    storage, credentials = root.resolve(), cookie.resolve().parent
    if storage == credentials or storage in credentials.parents or credentials in storage.parents:
        raise ValueError("managed storage must be outside the Cookie source directory")


def parse_args(argv: Sequence[str] | None = None) -> RunConfig:
    parser = argparse.ArgumentParser(description="PixivVault-NiceGUI 独立デモ")
    parser.add_argument("--host", choices=["127.0.0.1", "::1"], default="127.0.0.1")
    parser.add_argument("--port", type=_port, default=8089)
    parser.add_argument("--native", action="store_true", help="専用ウィンドウで起動（実機検証待ち）")
    parser.add_argument("--check", action="store_true", help="環境情報だけを表示し、サーバーを起動しない")
    parser.add_argument("--learning-sandbox", type=_sandbox_path,
                        help="合成デモの判定を専用フォルダーへ保存（絶対パス、新規または本デモ作成済み）")
    parser.add_argument("--library-readonly-db", type=_sandbox_path,
                        help="明示SQLiteの読取専用ライブラリ（DB変更は行わない）")
    parser.add_argument("--legacy-copy-root", type=_sandbox_path,
                        help="Existing dedicated S: directory for frozen legacy database snapshots")
    parser.add_argument("--library-image-root", type=_sandbox_path,
                        help="読取専用ライブラリの明示画像ルート")
    parser.add_argument("--pixiv-cookie-file", type=_sandbox_path,
                        help="明示Cookieファイルによるpixiv読取接続。原本は変更せず、更新操作まで通信しない")
    parser.add_argument('--enable-pixiv-actions', action='store_true',
                        help='Pixiv操作（ブクマ・フォロー）を明示的に有効化。既定はGET専用で、送信は画面上の操作時のみ')
    parser.add_argument("--download-sandbox", type=_sandbox_path,
                        help="架空画像の保存・キュー復旧を専用フォルダーで検証（絶対パス）")
    parser.add_argument("--pixiv-download-root", type=_sandbox_path,
                        help="通常イラスト・漫画の原寸画像を新しい専用管理フォルダーへ保存。Cookieと旧DBの場所は指定不可")
    parser.add_argument("--failed-queue-readonly-db", type=_sandbox_path,
                        help="静止した旧失敗キューDBコピーを読取専用参照し、明示操作で通常作品のみ管理キューへ投入")
    parser.add_argument("--legacy-zip-export-root", type=_sandbox_path,
                        help="管理保存済みの画像を旧形式作者ZIPとして新たな専用rootへ非破壊で書き出す（明示opt-in）")
    parser.add_argument("--legacy-diff-image-root", type=_sandbox_path,
                        help="静止した旧DBコピーに対応する旧画像/ZIPの読取専用root（旧DBコピーと管理保存を併用する場合のみ）")
    parser.add_argument("--novel-fixture-root", type=_sandbox_path,
                        help="合成小説の入力・形式選択・保存UIを専用rootで検証（実サービス接続なし）")
    parser.add_argument('--novel-download-root', type=_sandbox_path,
                        help='小説を専用rootへ保存。明示Cookieと作品・作者の指定が必要')
    parser.add_argument('--ugoira-root',type=_sandbox_path,help='うごイラ専用管理保存先（明示Cookieが必要）')
    parser.add_argument('--ugoira-fixture-root',type=_sandbox_path,help='うごイラ合成fixture専用保存先')
    parser.add_argument('--ugoira-codec-python',type=_sandbox_path,help='既存OpenCV CPU環境のPython絶対パス（MP4用）')
    parser.add_argument('--recommendation-preferences-root',type=_sandbox_path,help='静止ライブラリコピー用の別管理設定先')
    parser.add_argument('--preferences-root',type=_sandbox_path,help='専用設定の保存先')
    parser.add_argument('--discovery-root',type=_sandbox_path,help='実作品候補の学習履歴を保存する専用root。取得は明示操作のみ')
    parser.add_argument('--following-sync-root',type=_sandbox_path,
                        help='明示したフォロー同期結果を保存する専用root。同期操作まで通信しない')
    parser.add_argument('--external-vault-root',type=_sandbox_path,help='借用プレミアム用の専用暗号化保存先。自動解錠を保存した場合だけ起動時セッション更新')
    parser.add_argument('--image-cache-root',type=_sandbox_path,help='共有サムネイルキャッシュの専用保存先')
    from .uri_launch import parse_start_uri
    parser.add_argument('startup_uri', nargs='?', type=parse_start_uri)
    parser.add_argument('--extension-bridge', action='store_true',
                        help='Enable the original browser extension loopback receiver')
    parser.add_argument('--tab', default=None,
                        help='起動時に開くタブのキー（省略時は既定タブ）')
    parser.add_argument('--no-extension-bridge', action='store_true',
                        help='管理保存先があっても、拡張機能の受信サーバーを起動しない')
    parser.add_argument('--save-export-root',type=_sandbox_path)
    parser.add_argument('--bookmark-account-id')
    parser.add_argument('--profile-root', type=_sandbox_path,
                        help='通常起動のCookie参照先・保存先を保持する専用設定フォルダー')
    parser.add_argument('--similarity-index-root', type=_sandbox_path,
                        help='GPU/CPU解析結果を保存する別の専用root')
    parser.add_argument('--similarity-model-file', type=_sandbox_path,
                        help='既存EgaraNet ONNXの読取専用参照先')
    parser.add_argument('--similarity-provider', choices=('auto','cuda','cpu'), default=None)
    parser.add_argument('--similarity-auto', action='store_true', default=None,
                        help='保存済みの未解析ページを自動解析（明示opt-in）')
    args = parser.parse_args(argv)
    from .test_owner_runtime import validate_config
    validate_config(args)
    profile_revision = None
    profile_reference_unavailable = False
    if args.profile_root is not None:
        if not args.native:
            parser.error('起動設定は専用ウィンドウで使用してください。')
        source_options = ('learning_sandbox', 'library_readonly_db', 'library_image_root', 'legacy_copy_root',
            'pixiv_cookie_file', 'download_sandbox', 'pixiv_download_root',
            'failed_queue_readonly_db', 'legacy_zip_export_root', 'legacy_diff_image_root',
            'novel_fixture_root', 'novel_download_root', 'ugoira_root', 'ugoira_fixture_root',
            'ugoira_codec_python', 'recommendation_preferences_root', 'preferences_root',
            'discovery_root', 'external_vault_root', 'image_cache_root', 'save_export_root',
            'bookmark_account_id', 'following_sync_root', 'similarity_index_root',
            'similarity_model_file', 'similarity_provider', 'similarity_auto')
        if any(getattr(args, key) is not None for key in source_options):
            parser.error('起動設定と個別の保存先・入力元オプションは併用できません。')
        try:
            from .native_profile import (NativeProfileStore, NativeProfileReferenceUnavailable,
                                         NativeProfileSourceUnavailable, native_kwargs)
            from .native_startup import validate_profile_boundaries
            snapshot = NativeProfileStore.peek(args.profile_root)
            try:
                values = native_kwargs(snapshot, args.profile_root)
            except (NativeProfileReferenceUnavailable, NativeProfileSourceUnavailable):
                values = {}
                profile_reference_unavailable = True
            validate_profile_boundaries(args.profile_root, snapshot, values)
            for key, value in values.items():
                if key not in source_options:
                    raise ValueError('起動設定に未知の項目があります。')
                setattr(args, key, value)
            profile_revision = snapshot.revision
        except Exception:
            parser.error('起動設定を読み込めません。設定フォルダーと保存先を確認してください。')
    args.similarity_provider = args.similarity_provider or 'auto'
    args.similarity_auto = bool(args.similarity_auto)
    from .similarity_configuration import validate_similarity_configuration
    try:
        validate_similarity_configuration(args.similarity_index_root,
            args.similarity_model_file, args.similarity_provider, args.similarity_auto,
            writable=(args.preferences_root,args.discovery_root,args.external_vault_root,
                args.image_cache_root,args.recommendation_preferences_root,args.pixiv_download_root,
                args.novel_download_root or args.novel_fixture_root,args.ugoira_root or args.ugoira_fixture_root,
                args.learning_sandbox,args.download_sandbox,args.legacy_zip_export_root,
                args.save_export_root,args.following_sync_root),
            readonly=(args.library_image_root,args.legacy_diff_image_root,
                args.library_readonly_db.parent if args.library_readonly_db else None,
                args.failed_queue_readonly_db.parent if args.failed_queue_readonly_db else None,
                args.pixiv_cookie_file.parent if args.pixiv_cookie_file else None),
            has_images=args.pixiv_download_root is not None or args.library_readonly_db is not None)
    except ValueError as error:
        parser.error(str(error))
    # A lost reference in an existing native profile must reach its repair
    # screen. That branch constructs no live reader/action owner; ordinary
    # startup still requires the explicit Cookie source before enabling writes.
    if args.enable_pixiv_actions and not profile_reference_unavailable:
        if args.pixiv_cookie_file is None:
            parser.error('Pixiv actions require an explicit Cookie source')
        if (args.novel_download_root is not None or args.ugoira_root is not None) and args.pixiv_download_root is None:
            parser.error('Pixiv actions with live media require a managed Pixiv root')
    if args.save_export_root is not None:
        if args.pixiv_download_root is None or args.preferences_root is None:
            parser.error('save exports require managed storage and preferences')
        try:
            from .media_jobs import validate_media_roots
            validate_media_roots(args.save_export_root,args.pixiv_download_root,args.preferences_root,
                args.novel_download_root,args.ugoira_root,args.legacy_zip_export_root,
                args.discovery_root,args.external_vault_root,args.image_cache_root,args.following_sync_root)
            if args.pixiv_cookie_file is not None:_validate_managed_paths(args.save_export_root,args.pixiv_cookie_file)
        except ValueError as error:parser.error(str(error))
    if args.bookmark_account_id is not None:
        import re
        if args.save_export_root is None or re.fullmatch(r'[1-9][0-9]{0,19}',args.bookmark_account_id) is None:
            parser.error('bookmark account requires export root and positive ID')
    if args.extension_bridge and args.pixiv_download_root is None and not profile_reference_unavailable:
        parser.error('extension bridge requires explicit managed storage and reader')
    if args.discovery_root is not None and (args.pixiv_cookie_file is None or args.learning_sandbox is not None):
        parser.error('discovery requires an explicit Cookie source and separate managed root')
    if args.following_sync_root is not None:
        if args.pixiv_cookie_file is None or args.learning_sandbox is not None:
            parser.error('following synchronization requires an explicit Cookie source and separate root')
        if (args.novel_download_root is not None or args.ugoira_root is not None) and args.pixiv_download_root is None:
            parser.error('following synchronization with live media requires a managed Pixiv root')
    if args.image_cache_root is not None and args.preferences_root is None:
        parser.error('image cache requires explicit preferences')
    from .media_jobs import validate_media_roots
    try:
        from .media_jobs import validate_readonly_boundaries
        validate_readonly_boundaries(
            (args.preferences_root, args.discovery_root, args.external_vault_root,
             args.image_cache_root, args.recommendation_preferences_root, args.pixiv_download_root,
             args.novel_download_root, args.novel_fixture_root, args.ugoira_root,
             args.ugoira_fixture_root, args.learning_sandbox, args.download_sandbox,
             args.legacy_zip_export_root, args.save_export_root, args.following_sync_root),
            (args.library_image_root, args.legacy_diff_image_root,
             args.library_readonly_db.parent if args.library_readonly_db else None,
             args.failed_queue_readonly_db.parent if args.failed_queue_readonly_db else None))
        validate_media_roots(args.preferences_root,args.discovery_root,args.external_vault_root,
            args.image_cache_root,args.recommendation_preferences_root,args.pixiv_download_root,
            args.novel_download_root or args.novel_fixture_root,args.ugoira_root or args.ugoira_fixture_root,
            args.learning_sandbox,args.download_sandbox,args.legacy_zip_export_root,args.following_sync_root)
        for root in (args.preferences_root,args.discovery_root,args.external_vault_root,args.image_cache_root,args.following_sync_root):
            if root is not None:
                if args.pixiv_cookie_file is not None:_validate_managed_paths(root,args.pixiv_cookie_file)
                validate_readonly_boundaries((root,),
                    (args.library_readonly_db.parent if args.library_readonly_db else None,
                     args.library_image_root))
    except ValueError as error:parser.error(str(error))
    if args.recommendation_preferences_root is not None:
        if args.library_readonly_db is None and args.discovery_root is None: parser.error('recommendation preferences require readonly library or managed discovery')
        from .media_jobs import validate_media_roots
        try: validate_readonly_boundaries((args.recommendation_preferences_root,),
            (args.library_readonly_db.parent if args.library_readonly_db else None,args.library_image_root))
        except ValueError as error: parser.error(str(error))
    if args.novel_download_root is not None:
        if args.pixiv_cookie_file is None or any(v is not None for v in (
                args.novel_fixture_root, args.learning_sandbox, args.download_sandbox)):
            parser.error('novel-download-root requires Cookie and isolated storage')
        try:
            _validate_managed_paths(args.novel_download_root, args.pixiv_cookie_file)
        except ValueError as error:
            parser.error(str(error))
    if args.novel_fixture_root is not None and any(value is not None for value in (
            args.learning_sandbox, args.library_readonly_db, args.library_image_root,
            args.pixiv_cookie_file, args.download_sandbox, args.pixiv_download_root,
            args.failed_queue_readonly_db, args.legacy_zip_export_root, args.legacy_diff_image_root)):
        parser.error("novel-fixture-root requires an isolated synthetic-only configuration")
    if args.ugoira_root is not None or args.ugoira_fixture_root is not None:
        if any(v is not None for v in (args.learning_sandbox,args.download_sandbox)):
            parser.error('ugoira requires isolated configuration')
        if args.ugoira_fixture_root is not None and args.library_readonly_db is not None:
            parser.error('ugoira fixture excludes a legacy library')
        if args.ugoira_root is not None:
            if args.pixiv_cookie_file is None or args.ugoira_fixture_root is not None: parser.error('ugoira root needs explicit Cookie')
            try: _validate_managed_paths(args.ugoira_root,args.pixiv_cookie_file)
            except ValueError as error: parser.error(str(error))
        elif args.pixiv_cookie_file is not None: parser.error('fixture excludes Cookie')
    elif args.ugoira_codec_python is not None: parser.error('codec needs ugoira mode')
    if (args.novel_fixture_root is not None and args.ugoira_root is not None) or (args.ugoira_fixture_root is not None and args.novel_download_root is not None):
        parser.error('fixture and live media cannot be mixed')
    from .media_jobs import validate_media_roots
    try: validate_media_roots(args.novel_fixture_root or args.novel_download_root, args.ugoira_fixture_root or args.ugoira_root, args.pixiv_download_root, args.legacy_zip_export_root)
    except ValueError as error: parser.error(str(error))
    if args.legacy_copy_root is not None and args.library_readonly_db is None:
        parser.error('legacy-copy-root requires a legacy library database')
    if (args.library_readonly_db is None) != (args.library_image_root is None):
        parser.error("library-readonly-db and library-image-root must be specified together")
    if args.library_readonly_db is not None:
        if (args.novel_download_root is not None or args.ugoira_root is not None) and args.pixiv_download_root is None:
            parser.error('legacy library with live media requires an explicit managed Pixiv root')
        from .library_repository import require_frozen_library_copy
        try:
            require_frozen_library_copy(args.library_readonly_db)
        except (OSError, ValueError):
            parser.error('legacy library requires a standalone frozen database copy')
    if args.download_sandbox is not None and (args.library_readonly_db is not None or args.pixiv_cookie_file is not None):
        parser.error("download-sandbox is synthetic-only; do not combine with a read-only library or pixiv cookies")
    if args.pixiv_download_root is not None:
        if (args.pixiv_cookie_file is None or args.download_sandbox is not None or
                args.learning_sandbox is not None):
            parser.error("pixiv-download-root requires Cookie and isolated storage; other storage modes cannot be combined")
        try:
            _validate_managed_paths(args.pixiv_download_root, args.pixiv_cookie_file)
        except ValueError as error:
            parser.error(str(error))
    if args.failed_queue_readonly_db is not None:
        if args.pixiv_download_root is None or args.pixiv_cookie_file is None:
            parser.error('legacy retry needs an explicit Pixiv managed root and cookie source')
        source = args.failed_queue_readonly_db
        if (not source.is_file() or source.is_symlink()
                or args.pixiv_download_root.resolve() == source.resolve().parent
                or args.pixiv_download_root.resolve() in source.resolve().parents
                or any(Path(str(source) + suffix).exists()
                       for suffix in ('-wal', '-shm', '-journal'))):
            parser.error('failed queue must be a separate complete immutable SQLite copy')
    if args.legacy_zip_export_root is not None:
        if args.pixiv_download_root is None or args.pixiv_cookie_file is None:
            parser.error('ZIP export needs an isolated managed Pixiv root')
        dest = args.legacy_zip_export_root.resolve()
        managed = args.pixiv_download_root.resolve()
        cookie_dir = args.pixiv_cookie_file.resolve().parent
        if (dest == managed or dest in managed.parents or managed in dest.parents
                or dest == cookie_dir or dest in cookie_dir.parents or cookie_dir in dest.parents
                or (args.failed_queue_readonly_db is not None and
                    (dest == args.failed_queue_readonly_db.resolve().parent or
                     dest in args.failed_queue_readonly_db.resolve().parents))):
            parser.error('ZIP export must be separate from managed, Cookie and old DB roots')
    if args.legacy_diff_image_root is not None:
        if args.pixiv_download_root is None or args.failed_queue_readonly_db is None:
            parser.error('legacy diff needs a frozen failed-queue/works DB copy and a managed Pixiv root')
        image = args.legacy_diff_image_root
        if not image.is_dir() or image.is_symlink():
            parser.error('legacy diff needs an existing read-only image/ZIP root')
        source = args.failed_queue_readonly_db.resolve()
        managed = args.pixiv_download_root.resolve()
        cookie = args.pixiv_cookie_file.resolve().parent
        image = image.resolve()
        if (image == managed or image in managed.parents or managed in image.parents
                or image == cookie or image in cookie.parents or cookie in image.parents
                or image == source.parent or source in image.parents
                or (args.legacy_zip_export_root is not None and
                    (image == args.legacy_zip_export_root.resolve() or
                     image in args.legacy_zip_export_root.resolve().parents or
                     args.legacy_zip_export_root.resolve() in image.parents))):
            parser.error('legacy image root must be separate from managed, Cookie, snapshot and ZIP export paths')
    if args.extension_bridge and args.no_extension_bridge:
        parser.error('--extension-bridge と --no-extension-bridge は併用できません')
    from . import test_owner_runtime
    # 元(server.py)は常時起動。管理保存先があるときだけ既定で起動する。
    # テスト用の隔離領域では起動しない。設定「拡張機能の受信」で止められる。
    extension_bridge_auto = (not args.extension_bridge and not args.no_extension_bridge
                             and (args.pixiv_download_root is not None or args.profile_root is not None)
                             and test_owner_runtime._root is None)
    return RunConfig(args.host, args.port, args.native, args.check, args.learning_sandbox,
                     args.library_readonly_db, args.library_image_root, args.pixiv_cookie_file,
                     args.download_sandbox, args.pixiv_download_root,
                     args.failed_queue_readonly_db, args.legacy_zip_export_root,
                     args.legacy_diff_image_root, args.novel_fixture_root, args.novel_download_root,args.ugoira_root,args.ugoira_fixture_root,args.ugoira_codec_python,args.recommendation_preferences_root,args.preferences_root,args.discovery_root,args.external_vault_root,args.image_cache_root,args.startup_uri,args.extension_bridge,args.save_export_root,args.bookmark_account_id,args.profile_root,profile_revision,profile_reference_unavailable,args.following_sync_root,args.enable_pixiv_actions,
                     args.similarity_index_root,args.similarity_model_file,args.similarity_provider,args.similarity_auto,
                     extension_bridge_auto, args.tab, args.legacy_copy_root)


_registered = False


def _wire_browser_cookie_source(native_profile, store):
    """FL-A08 (explicit native launch only): installed-browser Cookie source.

    Never inside a test-owner workspace, which must not read a real browser.
    The credential store is built lazily on the first adoption, as an owned
    sibling of the profile root, so launching never creates it.
    """
    from . import test_owner_runtime
    if test_owner_runtime.owner_root() is not None:
        return
    from .browser_cookie_source import default_browser_cookie_source
    from .extension_cookie_store import ExtensionCookieStore

    root = Path(store.root)
    credentials = root.with_name(root.name + '-credentials')
    # Browser DB copies (Firefox holds every site's cookies in plaintext) go to
    # an app-owned folder, not the shared system TEMP; each copy is removed.
    scratch = root.with_name(root.name + '-scratch')
    native_profile.browser_cookie_source = default_browser_cookie_source(scratch)
    native_profile.cookie_store_factory = lambda: ExtensionCookieStore(credentials, profile=store)


def _wire_followup(native_profile, services):
    """FL-A09/A10: the following store and request pacing of this application."""
    native_profile.followup.store_provider = lambda: services.following_sync
    native_profile.followup.admission_provider = lambda: getattr(services, '_request_admission', None)
    native_profile._shared_event_services = services
    native_profile.followup._shared_event_services = services


def _build_owned_media_readers(*, pixiv_cookie_file, pixiv_download_root,
                               novel_download_root, novel_fixture_root,
                               novel_source, ugoira_root, ugoira_fixture_root,
                               live_client, managed_network_stop_scope,
                               shared_network_stop_scope=None, cookie_expiry_coordinator=None):
    """Construct all authenticated readers as one fail-closed ownership unit.

    A partial failure before ApplicationServices assumes the readers must not
    leak any default HTTPS pool. The success path transfers their existing
    lifecycle to the application, without closing an injected fixture client.
    """
    from contextlib import ExitStack
    from .pixiv_read_client import LivePixivReadClient

    with ExitStack() as owned:
        def make_reader():
            from .managed_network_stop_gate import ManagedNetworkGateStorageError
            if shared_network_stop_scope is None:
                # This is the product-owned source of *every* Cookie reader.
                # A test or direct register_pages caller must supply a verified
                # shared owner too: silently using the original bare client
                # would retain the profile-less CLI escape hatch.
                raise ManagedNetworkGateStorageError(
                    "sealed shared Pixiv GET stop owner required")
            if managed_network_stop_scope is not None:
                from .managed_network_stop_shared_transport import create_shared_profile_reader
                reader = create_shared_profile_reader(
                    managed_network_stop_scope, pixiv_cookie_file,
                    shared_network_stop_scope.gate,
                    shared_owner_check=shared_network_stop_scope._check_owner,
                )
            else:
                reader = shared_network_stop_scope.create_reader(pixiv_cookie_file)
            owned.callback(reader.close)
            if cookie_expiry_coordinator is not None:
                cookie_expiry_coordinator.register(reader)
            return reader

        if pixiv_cookie_file is not None:
            live_client = make_reader()
        if novel_download_root is not None:
            from .managed_novel_jobs import PinnedNovelSource
            novel_source = PinnedNovelSource(
                make_reader() if pixiv_download_root is not None else live_client, {})
            novel_fixture_root = novel_download_root
            if pixiv_download_root is None:
                live_client = None
        ugoira_source = None
        if ugoira_fixture_root is not None:
            from .ugoira_fixtures import build_ugoira_fixture
            ugoira_source = build_ugoira_fixture()
            ugoira_root = ugoira_fixture_root
        elif ugoira_root is not None:
            from .ugoira_practical import PinnedUgoiraSource
            ugoira_source = PinnedUgoiraSource(
                make_reader() if novel_download_root is not None
                or pixiv_download_root is not None else live_client)
            if pixiv_download_root is None:
                live_client = None
        owned.pop_all()
    return live_client, novel_source, novel_fixture_root, ugoira_source, ugoira_root


def extension_bridge_wanted(explicit, auto, preferences) -> bool:
    """拡張機能の受信サーバーを起動するか。明示指定は従来どおり。既定起動は設定で止められる。"""
    if explicit:
        return True
    if not auto:
        return False
    if preferences is None:
        return True      # 設定の保存先が無い起動では止める設定が無い（--no-extension-bridge で止める）
    try:
        return preferences.get_setting('extension_bridge_enabled', '1') != '0'
    except Exception:
        return False


def _wire_cookie_store(native_profile, store):
    """拡張機能から受けた Cookie の保存先（プロファイルの隣の -credentials）。作成は最初の受信時。"""
    from . import test_owner_runtime
    if test_owner_runtime.owner_root() is not None:
        return
    from .extension_cookie_store import ExtensionCookieStore

    root = Path(store.root)
    credentials = root.with_name(root.name + '-credentials')
    native_profile.cookie_store_factory = lambda: ExtensionCookieStore(credentials, profile=store)


def prepare_extension_receiver(services, explicit, auto, wanted) -> bool:
    """受信用の台帳を用意し、受信サーバーを起動するかを返す。"""
    if services.managed_downloads is None:
        return wanted
    existing = (services.managed_downloads.root / 'extension-commands').exists()
    if not (wanted or existing):
        return wanted
    try:
        services.enable_extension_commands()
    except Exception:
        if explicit or existing or not auto:
            raise            # 明示指定・既存台帳の失敗は従来どおり起動を止める
        # 既定起動で新しく作る台帳の失敗では、通常起動を止めない（ポートの問題とは別に表示する）。
        services.extension_bridge_status = 'commands_unavailable'
        return False
    return wanted


def register_pages(learning_sandbox: Path | None = None,
                   library_readonly_db: Path | None = None,
                   library_image_root: Path | None = None,
                   pixiv_cookie_file: Path | None = None, live_client=None,
                   download_sandbox: Path | None = None,
                   pixiv_download_root: Path | None = None,
                   failed_queue_readonly_db: Path | None = None,
                   legacy_zip_export_root: Path | None = None,
                   legacy_diff_image_root: Path | None = None,
                   novel_fixture_root: Path | None = None,
                   novel_download_root: Path | None = None,
                   ugoira_root=None,ugoira_fixture_root=None,ugoira_codec_python=None,
                   recommendation_preferences_root=None, preferences_root=None, discovery_root=None,
                   external_vault_root=None, image_cache_root=None, desktop_enabled=False, protocol_command=None,
                   extension_bridge=False,save_export_root=None,bookmark_account_id=None,
                   native_profile=None, following_sync_root=None, pixiv_actions_mode='readonly',
                   managed_network_stop_scope=None, shared_network_stop_scope=None,
                   similarity_index_root=None, similarity_model_file=None,
                   similarity_provider='auto', similarity_auto=False, extension_bridge_auto=False,
                   startup_tab=None, legacy_copy_root=None) -> None:
    """NiceGUIのHTTPルートを登録する。呼ぶだけではサーバーを起動しない。"""
    global _registered
    if _registered:
        return
    if (type(pixiv_actions_mode) is not str or
            pixiv_actions_mode not in ('readonly', 'enabled', 'preferences')):
        raise ValueError('invalid Pixiv action policy')
    if pixiv_actions_mode == 'enabled':
        if pixiv_cookie_file is None and live_client is None:
            raise ValueError('Pixiv actions require an explicit live reader')
        if (novel_download_root is not None or ugoira_root is not None) and pixiv_download_root is None:
            raise ValueError('Pixiv actions with live media require a managed Pixiv root')
    from nicegui import app, ui

    from pixivvault_nicegui.ui.shell import build_demo_page
    from pixivvault_nicegui.media_routes import register_media_routes
    from pixivvault_nicegui.services import get_services

    from .similarity_configuration import validate_similarity_configuration
    validate_similarity_configuration(similarity_index_root,similarity_model_file,
        similarity_provider,similarity_auto,
        writable=(preferences_root,discovery_root,external_vault_root,image_cache_root,
            recommendation_preferences_root,novel_fixture_root or novel_download_root,
            ugoira_fixture_root or ugoira_root,pixiv_download_root,legacy_zip_export_root,
            learning_sandbox,download_sandbox,following_sync_root,save_export_root),
        readonly=(library_image_root,legacy_diff_image_root,
            Path(library_readonly_db).parent if library_readonly_db else None,
            Path(failed_queue_readonly_db).parent if failed_queue_readonly_db else None,
            Path(pixiv_cookie_file).parent if pixiv_cookie_file else None),
        has_images=pixiv_download_root is not None or library_readonly_db is not None)

    if following_sync_root is not None:
        if pixiv_cookie_file is None and live_client is None:
            raise ValueError('following synchronization requires an explicit live reader')
        if (novel_download_root is not None or ugoira_root is not None) and pixiv_download_root is None:
            raise ValueError('following synchronization with live media requires a managed Pixiv root')

    if novel_fixture_root is not None and any(value is not None for value in (
            learning_sandbox, library_readonly_db, library_image_root, pixiv_cookie_file,
            live_client, download_sandbox, pixiv_download_root, failed_queue_readonly_db,
            legacy_zip_export_root, legacy_diff_image_root)):
        raise ValueError("novel fixture UI requires an isolated synthetic-only configuration")
    if novel_download_root is not None:
        if pixiv_cookie_file is None or any(v is not None for v in (
                novel_fixture_root, live_client, download_sandbox,
                learning_sandbox)):
            raise ValueError('novel download requires isolated root and explicit Cookie')
        _validate_managed_paths(novel_download_root, pixiv_cookie_file)
    from .media_jobs import validate_media_roots, validate_readonly_boundaries
    validate_readonly_boundaries(
        (novel_fixture_root, novel_download_root, ugoira_fixture_root, ugoira_root,
         pixiv_download_root, legacy_zip_export_root, preferences_root, discovery_root,
         external_vault_root, image_cache_root, recommendation_preferences_root,
         learning_sandbox, download_sandbox, save_export_root, following_sync_root),
        (library_image_root, legacy_diff_image_root,
         Path(library_readonly_db).parent if library_readonly_db else None,
         Path(failed_queue_readonly_db).parent if failed_queue_readonly_db else None))
    validate_media_roots(novel_fixture_root or novel_download_root, ugoira_fixture_root or ugoira_root, pixiv_download_root, legacy_zip_export_root)
    validate_media_roots(preferences_root,discovery_root,external_vault_root,image_cache_root,
        recommendation_preferences_root,novel_fixture_root or novel_download_root,
        ugoira_fixture_root or ugoira_root,pixiv_download_root,legacy_zip_export_root,
        learning_sandbox,download_sandbox,following_sync_root)
    if shared_network_stop_scope is not None:
        from .managed_network_stop_startup import attach_selected_histories
        attach_selected_histories(shared_network_stop_scope, (
            ('images', pixiv_download_root), ('novel', novel_download_root),
            ('ugoira', ugoira_root)))
    for sidecar in (preferences_root,discovery_root,external_vault_root,image_cache_root,following_sync_root):
        if sidecar is not None:
            if pixiv_cookie_file is not None:_validate_managed_paths(sidecar,pixiv_cookie_file)
            validate_readonly_boundaries((sidecar,),
                (Path(library_readonly_db).parent if library_readonly_db else None,
                 library_image_root,Path(failed_queue_readonly_db).parent if failed_queue_readonly_db else None,
                 legacy_diff_image_root))
    if (library_readonly_db is None) != (library_image_root is None):
        raise ValueError('legacy library requires its explicit database and image root')
    if library_readonly_db is not None:
        if (novel_download_root is not None or ugoira_root is not None) and pixiv_download_root is None:
            raise ValueError('legacy library with live media requires an explicit managed Pixiv root')
        from .library_repository import require_frozen_library_copy
        from .legacy_library_copies import LegacyCopyError
        try:
            require_frozen_library_copy(library_readonly_db)
        except (OSError, ValueError):
            raise LegacyCopyError('legacy database source is unavailable or in use') from None
    desktop_roots = ()
    if desktop_enabled:
        from .saved_folders import desktop_saved_roots
        # Compute native authority before constructing any HTTP/media owner.
        # The selected save_parent itself is never an allowed root.
        desktop_roots = desktop_saved_roots(native_profile, pixiv_download_root,
            library_image_root, save_export_root, download_sandbox,
            novel_fixture_root or novel_download_root,
            ugoira_root or ugoira_fixture_root, legacy_zip_export_root)
    novel_source = None
    if novel_fixture_root is not None:
        from pixivvault_nicegui.novel_save_presentation import build_novel_ui_fixture
        novel_source = build_novel_ui_fixture()

    if ugoira_root is not None or ugoira_fixture_root is not None:
        if any(v is not None for v in (learning_sandbox,download_sandbox,live_client)):
            raise ValueError('ugoira requires isolated configuration')
        if ugoira_fixture_root is not None and library_readonly_db is not None:
            raise ValueError('ugoira fixture excludes a legacy library')
        if ugoira_root is not None:
            if pixiv_cookie_file is None or ugoira_fixture_root is not None: raise ValueError('explicit Cookie required')
            _validate_managed_paths(ugoira_root,pixiv_cookie_file)
        elif pixiv_cookie_file is not None: raise ValueError('fixture excludes Cookie')
    if managed_network_stop_scope is not None:
        from .managed_network_stop_profile_scope import ManagedProfileNetworkStopScope
        if (type(managed_network_stop_scope) is not ManagedProfileNetworkStopScope
                or native_profile is None
                or getattr(getattr(native_profile, 'store', None), 'root', None)
                   != managed_network_stop_scope.root):
            raise ValueError('native profile stop scope ownership mismatch')
    desktop = None
    notification_backend = None
    if desktop_enabled:
        from .desktop_integration import DesktopIntegration, WindowsLauncher, WindowsProtocolRegistration
        from .notifications import WindowsToastBackend,windows_notifications_available
        if preferences_root is not None and windows_notifications_available():
            notification_backend=WindowsToastBackend()
        if desktop_roots:
            desktop = DesktopIntegration(allowed_roots=desktop_roots, launcher=WindowsLauncher(),
                legacy_image_root=library_image_root,
                protocol=WindowsProtocolRegistration(protocol_command) if protocol_command else None)
    if pixiv_cookie_file is not None:
        if live_client is not None:
            raise ValueError("choose one pixiv client source")
        if pixiv_download_root is not None:
            _validate_managed_paths(pixiv_download_root, pixiv_cookie_file)
    if library_readonly_db is not None:
        from .legacy_library_copies import LegacyCopyError
        from .managed_downloads import _safe_path
        try:
            provision_copy_root = legacy_copy_root is None
            if provision_copy_root:
                if native_profile is None or native_profile.launch_snapshot.save_parent is None:
                    raise LegacyCopyError('legacy database snapshot destination is required')
                from .native_profile import STORAGE_FOLDER, STORAGE_MARKER, STORAGE_IDENTITY, _owned_marker
                parent = Path(native_profile.launch_snapshot.save_parent)
                base = parent / STORAGE_FOLDER
                _owned_marker(base, STORAGE_MARKER, STORAGE_IDENTITY)
                legacy_copy_root = base / 'legacy-library-copies'
            legacy_copy_root = Path(legacy_copy_root)
            if not legacy_copy_root.is_absolute() or legacy_copy_root.drive.upper() != 'S:':
                raise LegacyCopyError('legacy database snapshot destination must be on S:')
            validate_readonly_boundaries((legacy_copy_root,),
                (Path(library_readonly_db).parent, library_image_root,
                 Path(failed_queue_readonly_db).parent if failed_queue_readonly_db else None,
                 legacy_diff_image_root,
                 Path(pixiv_cookie_file).parent if pixiv_cookie_file else None,
                 Path(similarity_model_file).parent if similarity_model_file else None,
                 getattr(getattr(native_profile, 'store', None), 'root', None)))
            validate_media_roots(legacy_copy_root, pixiv_download_root, novel_fixture_root,
                novel_download_root, ugoira_root, ugoira_fixture_root, preferences_root,
                discovery_root, external_vault_root, image_cache_root,
                recommendation_preferences_root, following_sync_root, legacy_zip_export_root,
                learning_sandbox, download_sandbox, similarity_index_root, save_export_root)
            _safe_path(legacy_copy_root)
            if provision_copy_root:
                legacy_copy_root.mkdir(exist_ok=True)
            _safe_path(legacy_copy_root)
        except LegacyCopyError:
            raise
        except (OSError, ValueError):
            raise LegacyCopyError('legacy database snapshot destination unavailable') from None
    live_client, novel_source, novel_fixture_root, ugoira_source, ugoira_root = _build_owned_media_readers(
        pixiv_cookie_file=pixiv_cookie_file,
        pixiv_download_root=pixiv_download_root,
        novel_download_root=novel_download_root,
        novel_fixture_root=novel_fixture_root,
        novel_source=novel_source,
        ugoira_root=ugoira_root,
        ugoira_fixture_root=ugoira_fixture_root,
        live_client=live_client,
        managed_network_stop_scope=managed_network_stop_scope,
        shared_network_stop_scope=shared_network_stop_scope,
        cookie_expiry_coordinator=(native_profile.cookie_expiry_coordinator()
            if native_profile is not None and pixiv_cookie_file is not None
            and managed_network_stop_scope is not None
            and getattr(native_profile, 'cookie_store_factory', None) is not None else None))
    try:
        services = get_services(app, learning_sandbox=learning_sandbox,
            library_readonly_db=library_readonly_db, library_image_root=library_image_root,
            live_client=live_client, download_sandbox=download_sandbox,
            pixiv_download_root=pixiv_download_root,
            failed_queue_readonly_db=failed_queue_readonly_db,
            legacy_zip_export_root=legacy_zip_export_root,
            legacy_diff_image_root=legacy_diff_image_root,
            novel_fixture_root=novel_fixture_root, novel_fixture_source=novel_source,
            ugoira_root=ugoira_root,ugoira_source=ugoira_source,ugoira_codec_python=ugoira_codec_python,
            recommendation_preferences_root=recommendation_preferences_root,
            preferences_root=preferences_root,discovery_root=discovery_root,
            external_vault_root=external_vault_root,image_cache_root=image_cache_root,desktop=desktop,notification_backend=notification_backend,
            following_sync_root=following_sync_root, pixiv_actions_mode=pixiv_actions_mode,
            managed_network_stop_scope=managed_network_stop_scope,
            shared_network_stop_scope=shared_network_stop_scope, legacy_copy_root=legacy_copy_root)
    except BaseException:
        if novel_download_root is not None and novel_source is not None:
            novel_source._reader.close()
        if ugoira_source is not None: ugoira_source.close()
        if live_client is not None:
            live_client.close()
        raise
    try:
        if save_export_root is not None:
            services.configure_save_exports(save_export_root,bookmark_account_id)
            app.on_startup(services.save_exports.start)
        if similarity_index_root is not None:
            services.configure_similarity_index(similarity_index_root,
                model_file=similarity_model_file, provider=similarity_provider)
            # 元 gui.py:6344-6346: 自動実行が ON（既定）なら起動時に起こす。モデルが無ければ何もしない。
            # 設定の保存先があるときは設定が決める（旧 profile の「自動」印で OFF を打ち消さない）。
            services.similarity_auto_flag = bool(similarity_auto) and services.preferences is None
            app.on_startup(services.start_similarity_auto)
        # 元 scheduler.py:104-123 相当の自動後埋め。設定（既定 ON）に従い、取得停止ゲートと
        # ダウンロード優先を通すループを 1 本だけ立てる。通信は最初の周（60 秒後）まで起きない。
        from .pixiv_read_client import LivePixivReadClient
        if (services.managed_downloads is not None and services.preferences is not None
                and isinstance(services.live_client, LivePixivReadClient)):
            app.on_startup(services.start_metadata_auto)
        if startup_tab is not None:
            services.startup_tab = startup_tab
        bridge_wanted = extension_bridge_wanted(extension_bridge, extension_bridge_auto, services.preferences)
        if extension_bridge_auto and not bridge_wanted:
            services.extension_bridge_status = 'setting_off'
        bridge_wanted = prepare_extension_receiver(services, extension_bridge, extension_bridge_auto, bridge_wanted)
        if bridge_wanted:
            app.on_startup(services.start_extension_bridge)
        if desktop_enabled:
            from .native_lifecycle import bind_native_tray
            services.tray=bind_native_tray(app,services.preferences,services=services)
        if native_profile is not None:
            services.native_profile = native_profile
            _wire_followup(native_profile, services)
        register_media_routes(app, services.images)

        async def close_services() -> None:
            import asyncio
            services._asgi_shutdown_started = True
            was_closing = services.closing
            try:
                await services.drain_startup_discovery()
            finally:
                try:
                    if not was_closing:
                        await asyncio.to_thread(services.close)
                finally:
                    if native_profile is not None and services.closed:
                        native_profile.close()

        app.on_startup(services.start_discovery_initial_load)
        app.on_shutdown(close_services)
        ui.page("/")(build_demo_page)

        @app.get("/healthz", include_in_schema=False)
        def health() -> dict[str, str]:
            return {"status": "ok", "mode": "demo" if services.data_mode == "synthetic" else "readonly",
                    "data": services.data_mode}

        # Browser acceptance only: a fresh private nonce ties the loopback HTTP
        # endpoint to the process spawned by its owning checker. This route is
        # never installed in normal, read-only, or authenticated application runs.
        import os
        acceptance_nonce = os.environ.get("PV_SYNTHETIC_BROWSER_OWNER_NONCE")
        if acceptance_nonce is not None:
            if (len(acceptance_nonce) != 48 or not all(c in "0123456789abcdef" for c in acceptance_nonce)
                    or services.data_mode != "synthetic" or services.live_client is not None):
                raise ValueError("browser acceptance owner route requires isolated synthetic mode")
            import secrets
            from fastapi import Header, HTTPException

            @app.get("/_browser_acceptance/owner", include_in_schema=False)
            def browser_acceptance_owner(x_acceptance: str | None = Header(default=None)):
                if not secrets.compare_digest(x_acceptance or "", acceptance_nonce):
                    raise HTTPException(status_code=403)
                return {"synthetic": True, "pid": os.getpid(), "parent_pid": os.getppid()}

            @app.post("/_browser_acceptance/shutdown", include_in_schema=False)
            def browser_acceptance_shutdown(x_acceptance: str | None = Header(default=None)):
                if not secrets.compare_digest(x_acceptance or "", acceptance_nonce):
                    raise HTTPException(status_code=403)
                app.shutdown()
                return {"stopping": True}

        @app.get("/readyz", include_in_schema=False)
        def ready() -> dict[str, bool]:
            # HTTP listenだけでなく、UIが起動して接続したかをnative試験で区別する。
            from nicegui import Client
            return {"ui_connected": any(c.has_socket_connection for c in Client.instances.values())}

        _registered = True
    except BaseException as startup_error:
        try:
            services.close()
        except BaseException as cleanup_error:
            startup_error.add_note("application service cleanup was not verified")
            raise startup_error from cleanup_error
        raise


def native_window_options(layout):
    return {
        'x': layout.x, 'y': layout.y, 'confirm_close': True,
        'min_size': (min(1280, layout.width), min(720, layout.height)),
        'localization': {
            'global.quitConfirmation': 'PixivVaultを終了しますか？',
            'global.quit': '終了',
            'global.cancel': 'キャンセル',
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    config = parse_args(argv)
    from .test_owner_runtime import validate_config
    validate_config(config)
    if config.check_only:
        print(json.dumps({
            "python": sys.version.split()[0],
            "nicegui": importlib.metadata.version("nicegui"),
            "mode": "readonly" if config.library_readonly_db is not None or config.pixiv_cookie_file is not None else "demo",
            "host": config.host,
            "port": config.port,
            "native_requested": config.native,
            "native_available": importlib.util.find_spec("webview") is not None,
            "configuration_required": config.profile_reference_unavailable,
            "pixiv_actions_policy": ('enabled' if config.enable_pixiv_actions else
                                     'preferences' if config.profile_root is not None else 'readonly'),
            "data": ("pixiv-readonly" if config.pixiv_cookie_file is not None else
                     "library-readonly" if config.library_readonly_db is not None else "synthetic"),
            "learning_storage": "sandbox" if config.learning_sandbox is not None else "memory",
            "download_storage": ("managed-novel" if config.novel_download_root is not None else
                                 "novel-fixture" if config.novel_fixture_root is not None else
                                 "managed-pixiv" if config.pixiv_download_root is not None else
                                 "sandbox" if config.download_sandbox is not None else "disabled"),
        }, ensure_ascii=False))
        return 0
    if config.native and importlib.util.find_spec("webview") is None:
        raise SystemExit("native用のpywebviewは未導入です。Windows配布検証の段階で専用環境へ追加します。")

    multiprocessing.freeze_support()
    from nicegui import ui

    window_size = (1920, 1080) if config.native else None
    if config.native and sys.platform == "win32":
        from nicegui import app
        from pixivvault_nicegui.native_layout import current_startup_layout

        # Establish layout before creating DB/session resources. Only our new
        # native window receives these logical coordinates; no other HWND moves.
        layout = current_startup_layout()
        window_size = (layout.width, layout.height)
        app.native.window_args.update(native_window_options(layout))
        # pywebview otherwise cancels even an explicit CSV download. In this
        # newly created Windows app only, retain its owned SaveFileDialog so
        # the user chooses the destination (and confirms any replacement).
        app.native.settings["ALLOW_DOWNLOADS"] = True

    registration = {}
    if config.enable_pixiv_actions:
        registration['pixiv_actions_mode'] = 'enabled'
    elif config.profile_root is not None:
        registration['pixiv_actions_mode'] = 'preferences'
    native_profile = None
    native_instance = None
    cookie_rejected_for_recovery = False
    stop_recovery_required = False
    stop_gate_busy = False
    shared_network_stop_scope = None
    if config.profile_root is not None:
        from .native_profile import (NativeProfileStore, NativeProfileReferenceUnavailable,
                                     NativeProfileSourceUnavailable, native_kwargs, provision_storage)
        from .native_startup import NativeProfileBinding, validate_profile_boundaries
        store = NativeProfileStore(config.profile_root)
        try:
            snapshot = store.snapshot()
            unavailable = False
            try:
                values = native_kwargs(snapshot, config.profile_root)
            except (NativeProfileReferenceUnavailable, NativeProfileSourceUnavailable):
                values = {}
                unavailable = True
            if (snapshot.revision != config.profile_revision or
                    unavailable != config.profile_reference_unavailable or
                    any(getattr(config, key) != value for key, value in values.items())):
                raise ValueError('起動設定が変更されました。もう一度起動してください。')
            validate_profile_boundaries(config.profile_root, snapshot, values)
            if config.native and sys.platform == 'win32':
                from .native_instance import claim_or_restore
                native_instance = claim_or_restore(store.root)
                if native_instance is None:
                    # The exact existing owner's window was restored. Do not
                    # provision storage, start jobs, register pages, or bind a
                    # second server for a warm URI/plain native invocation.
                    store.close()
                    return 0
            native_profile = NativeProfileBinding(store, launch_snapshot=snapshot)
            _wire_cookie_store(native_profile, store)
            # All GUI auth checks, including draft Cookie validation, require
            # the same stop fence as the managed application readers.
            native_profile.auth_stop_required = True
            native_profile.auth_shared_stop_required = True
            registration['native_profile'] = native_profile
            _wire_browser_cookie_source(native_profile, store)
            # Establish a stable owned profile stop *before* constructing any
            # authenticated media reader. Newly created revision-zero profiles
            # can safely initialize it; previously configured profiles without
            # proven stop history must first undergo a reviewed migration.
            # Offline recovery of an unconfigured/credential-free old profile
            # remains available without inventing network authority.
            from .managed_network_stop_profile_scope import ManagedProfileNetworkStopScope
            from .managed_network_stop_profile_scope import _SCOPE_MARKER
            from .managed_network_stop_gate import ManagedNetworkGateStorageError, ManagedNetworkGateBusy
            if (not unavailable and
                    (snapshot.revision == 0 or snapshot.cookie_file is not None
                     or (store.root / _SCOPE_MARKER).exists())):
                try:
                    registration['managed_network_stop_scope'] = ManagedProfileNetworkStopScope(
                        store.root)
                    native_profile.managed_network_stop_scope = registration[
                        'managed_network_stop_scope']
                except ManagedNetworkGateStorageError as gate_error:
                    # Configured legacy profiles without a safe stop migration
                    # remain blocked. A syntactically invalid saved Cookie may
                    # still open the OFFLINE editable recovery page, never a
                    # live reader/job. This one-time parse performs no GET.
                    if isinstance(gate_error, ManagedNetworkGateBusy):
                        stop_gate_busy = True
                        stop_recovery_required = True
                    elif snapshot.cookie_file is None:
                        stop_recovery_required = True
                    else:
                        from .pixiv_read_client import LivePixivReadClient, CookieSourceRejected
                        try:
                            diagnostic = LivePixivReadClient(snapshot.cookie_file)
                        except CookieSourceRejected:
                            cookie_rejected_for_recovery = True
                        else:
                            diagnostic.close()
                            stop_recovery_required = True
            if not unavailable and not cookie_rejected_for_recovery and not stop_recovery_required:
                try:
                    if (snapshot.save_parent is not None
                            and 'managed_network_stop_scope' in registration):
                        from .managed_network_stop_storage_binding import (
                            prepare_native_storage_binding, bind_new_native_storage,
                        )
                        prepare_native_storage_binding(
                            registration['managed_network_stop_scope'], snapshot.save_parent)
                    provision_storage(snapshot, config.profile_root)
                    if (snapshot.save_parent is not None
                            and 'managed_network_stop_scope' in registration):
                        bind_new_native_storage(
                            registration['managed_network_stop_scope'], snapshot.save_parent)
                except ManagedNetworkGateStorageError as gate_error:
                    # Missing/unknown prior stop ownership must never launch
                    # an authenticated reader. Show only the offline settings
                    # view; an explicit reviewed migration remains necessary.
                    stop_recovery_required = True
                    stop_gate_busy = isinstance(gate_error, ManagedNetworkGateBusy)
        except BaseException:
            try:
                if native_instance is not None:
                    native_instance.close()
            finally:
                store.close()
            raise
    # Normal first use establishes the durable shared owner. Existing owner
    # loss, STOP and uncertain outcomes retain their original repair boundary.
    if (not cookie_rejected_for_recovery and
            (config.pixiv_cookie_file is not None or config.external_vault_root is not None or
             (config.profile_root is not None and
              registration.get('managed_network_stop_scope') is not None))):
        if config.profile_root is None and config.pixiv_cookie_file is not None:
            # Preserve the CLI's existing invalid-Cookie diagnostic before
            # attempting any network-stop ownership migration. This is only
            # the bounded local Cookie-file parser: no GET or POST is issued.
            from .pixiv_read_client import _read_cookies, CookieSourceRejected
            try:
                _read_cookies(config.pixiv_cookie_file)
            except CookieSourceRejected:
                from .native_auth_result import NativeAuthResult
                print(NativeAuthResult('cookie_unreadable').message, file=sys.stderr)
                return 2
        from .managed_network_stop_startup import open_or_initialize_windows_user_stop
        from .managed_network_stop_shared_scope import SharedStopOwnerAbsent
        from .managed_network_stop_gate import ManagedNetworkGateStorageError, ManagedNetworkGateBusy
        try:
            shared_network_stop_scope = open_or_initialize_windows_user_stop(
                selected_roots=(('images', config.pixiv_download_root),
                                ('novel', config.novel_download_root),
                                ('ugoira', config.ugoira_root)),
                profile_scope=registration.get('managed_network_stop_scope'))
            registration['shared_network_stop_scope'] = shared_network_stop_scope
            if registration.get('managed_network_stop_scope') is not None:
                registration['managed_network_stop_scope'].shared_owner = shared_network_stop_scope
            if native_profile is not None:
                native_profile.shared_network_stop_scope = shared_network_stop_scope
        except ManagedNetworkGateStorageError as gate_error:
            # Keep the offline settings page, with the bounded local cause.
            from .native_auth_result import NativeAuthResult
            from .managed_network_stop_shared_scope import SharedGetOutcomeUncertain
            code = ('busy' if isinstance(gate_error, ManagedNetworkGateBusy) else
                    'uncertain' if isinstance(gate_error, SharedGetOutcomeUncertain) else
                    'owner_absent' if isinstance(gate_error, SharedStopOwnerAbsent) else
                    'local_unavailable')
            if config.profile_root is None:
                print(NativeAuthResult(code).message, file=sys.stderr)
                return 2
            native_profile.auth_startup_result = NativeAuthResult(code)
            stop_recovery_required = True
            stop_gate_busy = isinstance(gate_error, ManagedNetworkGateBusy)
    try:
        # The native owner lock is already held. Every exception before the
        # UI's later shutdown-finally must release that exact original lock.
        if config.novel_download_root is not None:
            registration["novel_download_root"] = config.novel_download_root
        if config.novel_fixture_root is not None:
            registration["novel_fixture_root"] = config.novel_fixture_root
        if config.learning_sandbox is not None:
            registration["learning_sandbox"] = config.learning_sandbox
        if config.library_readonly_db is not None:
            registration["library_readonly_db"] = config.library_readonly_db
            registration["library_image_root"] = config.library_image_root
            if config.legacy_copy_root is not None:
                registration["legacy_copy_root"] = config.legacy_copy_root
        if config.pixiv_cookie_file is not None:
            registration["pixiv_cookie_file"] = config.pixiv_cookie_file
        if config.download_sandbox is not None:
            registration["download_sandbox"] = config.download_sandbox
        if config.pixiv_download_root is not None:
            registration["pixiv_download_root"] = config.pixiv_download_root
        if config.failed_queue_readonly_db is not None:
            registration['failed_queue_readonly_db'] = config.failed_queue_readonly_db
        if config.legacy_zip_export_root is not None:
            registration['legacy_zip_export_root'] = config.legacy_zip_export_root
        if config.legacy_diff_image_root is not None:
            registration['legacy_diff_image_root'] = config.legacy_diff_image_root
        for key in ('ugoira_root','ugoira_fixture_root','ugoira_codec_python','recommendation_preferences_root',
                    'preferences_root','discovery_root','external_vault_root','image_cache_root','following_sync_root'):
            if getattr(config,key) is not None: registration[key]=getattr(config,key)
        if config.save_export_root is not None:
            registration['save_export_root']=config.save_export_root
            registration['bookmark_account_id']=config.bookmark_account_id
        if config.similarity_index_root is not None:
            registration.update(similarity_index_root=config.similarity_index_root,
                similarity_model_file=config.similarity_model_file,
                similarity_provider=config.similarity_provider,similarity_auto=config.similarity_auto)
        if config.extension_bridge:
            registration['extension_bridge'] = True
        if config.extension_bridge_auto:
            registration['extension_bridge_auto'] = True
        # 元 main.py:367-372: 不正キーは黙らず印字してから既定タブへ。
        from .startup_tab import resolve_startup_tab, shell_tab_key
        resolved_tab, tab_warning = resolve_startup_tab(config.startup_tab)
        if tab_warning:
            print(tab_warning)
        registration['startup_tab'] = shell_tab_key(resolved_tab)
        if config.native:
            registration["desktop_enabled"] = True
            if config.pixiv_download_root is not None:
                from .uri_launch import build_protocol_command
                # 元の URI 起動で受信が立つかは、既定起動と同じく設定（受信を止める）に従う。
                args_for_uri = ['--native', '--host', config.host, '--port', str(config.port)]
                from dataclasses import fields
                if config.profile_root is not None:
                    args_for_uri.extend(['--profile-root', str(config.profile_root)])
                else:
                    for field in fields(config):
                        value = getattr(config, field.name)
                        if isinstance(value, Path):
                            args_for_uri.extend(['--' + field.name.replace('_', '-'), str(value)])
                    # The original loopback URI must relaunch the exact selected
                    # inference backend, not quietly revert to CUDA-auto or CPU.
                    if config.similarity_index_root is not None:
                        args_for_uri.extend(['--similarity-provider', config.similarity_provider])
                        if config.similarity_auto:
                            args_for_uri.append('--similarity-auto')
                if config.enable_pixiv_actions:
                    args_for_uri.append('--enable-pixiv-actions')
                if config.bookmark_account_id is not None:
                    args_for_uri.extend(['--bookmark-account-id',config.bookmark_account_id])
                launcher = Path(__file__).resolve().parents[2] / 'tools' / 'launch_owned_candidate.py'
                registration['protocol_command'] = build_protocol_command(sys.executable,
                    script=None if getattr(sys, 'frozen', False) else str(launcher),
                    frozen=bool(getattr(sys, 'frozen', False)), startup_args=tuple(args_for_uri))
    except BaseException:
        try:
            if native_profile is not None:
                native_profile.close()
        finally:
            if native_instance is not None:
                native_instance.close()
        raise
    # Capture the ASGI service that THIS invocation creates; a previous
    # owner's app.state resource must never be closed by a second launch.
    from nicegui import app as _owned_app
    from .services import _APP_KEY as _SERVICE_KEY
    from .managed_network_stop_gate import ManagedNetworkGateStorageError
    from .managed_network_stop_shared_scope import SharedGetOutcomeUncertain
    _prior_service = getattr(_owned_app.state, _SERVICE_KEY, None)
    _launched_service = None
    entered_ui_run = False
    try:
        if (config.profile_reference_unavailable or cookie_rejected_for_recovery
                or stop_recovery_required):
            from .native_startup import register_profile_recovery
            if cookie_rejected_for_recovery:
                register_profile_recovery(native_profile, cookie_rejected=True)
            elif stop_gate_busy:
                register_profile_recovery(native_profile, stop_gate_busy=True)
            elif stop_recovery_required:
                register_profile_recovery(native_profile, stop_migration_required=True)
            else:
                register_profile_recovery(native_profile)
        else:
            from .pixiv_read_client import CookieSourceRejected
            from .legacy_library_copies import LegacyCopyError
            try:
                register_pages(**registration)
                _candidate_service = getattr(_owned_app.state, _SERVICE_KEY, None)
                if _candidate_service is not _prior_service:
                    _launched_service = _candidate_service
            except LegacyCopyError as copy_error:
                if native_profile is None:
                    print(str(copy_error), file=sys.stderr)
                    return 2
                from .native_startup import register_profile_recovery
                register_profile_recovery(native_profile, legacy_copy_error=True)
            except CookieSourceRejected:
                if native_profile is None:
                    raise
                from .native_startup import register_profile_recovery
                register_profile_recovery(native_profile, cookie_rejected=True)
            except ManagedNetworkGateStorageError as gate_error:
                if native_profile is None:
                    raise
                # An existing per-root STOP, absent owner, or busy gate may
                # be discovered during actual reader/service registration.
                # Keep the owned Native window in offline settings recovery;
                # never turn this into a live GET or an unhandled silent exit.
                from .native_startup import register_profile_recovery
                if isinstance(gate_error, ManagedNetworkGateBusy):
                    register_profile_recovery(native_profile, stop_gate_busy=True)
                elif (isinstance(gate_error, SharedGetOutcomeUncertain)
                      and native_profile.shared_network_stop_scope is not None):
                    register_profile_recovery(native_profile, stop_get_uncertain=True)
                else:
                    register_profile_recovery(native_profile, stop_migration_required=True)
        entered_ui_run = True
        ui.run(
            host=config.host,
            port=config.port,
            native=config.native,
            show=False,
            reload=False,
            title="PixivVault" if config.profile_root is not None else "PixivVault-NiceGUI / 移植デモ",
            # NiceGUIではwindow_sizeの指定だけでもnativeが有効になる。
            window_size=window_size,
            binding_refresh_interval=None,
        )
    except (ValueError, OSError):
        # Runtime and shutdown failures must never masquerade as owner
        # admission errors. Hide private paths in a packaged --noconsole
        # build, but retain the original exception for source diagnostics.
        if entered_ui_run and config.native and sys.platform == 'win32' and getattr(sys, 'frozen', False):
            from .native_instance import NativeAppRuntimeError
            raise NativeAppRuntimeError() from None
        raise
    finally:
        try:
            try:
                if (_launched_service is not None
                        and not getattr(_launched_service, "_asgi_shutdown_started", False)
                        and not getattr(_launched_service, "closing", False)):
                    _launched_service.close()
            finally:
                try:
                    if (native_profile is not None
                            and (_launched_service is None
                                 or getattr(_launched_service, "closed", False))):
                        native_profile.close()
                finally:
                    if native_instance is not None:
                        native_instance.close()
        except (ValueError, OSError):
            if entered_ui_run and config.native and sys.platform == 'win32' and getattr(sys, 'frozen', False):
                from .native_instance import NativeAppRuntimeError
                raise NativeAppRuntimeError() from None
            raise
    return 0
