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
    parser.add_argument("--library-image-root", type=_sandbox_path,
                        help="読取専用ライブラリの明示画像ルート")
    parser.add_argument("--pixiv-cookie-file", type=_sandbox_path,
                        help="明示Cookieファイルによるpixiv読取接続。原本は変更せず、更新操作まで通信しない")
    parser.add_argument("--download-sandbox", type=_sandbox_path,
                        help="架空画像の保存・キュー復旧を専用フォルダーで検証（絶対パス）")
    parser.add_argument("--pixiv-download-root", type=_sandbox_path,
                        help="通常イラスト・漫画の原寸画像を新しい専用管理フォルダーへ保存。Cookieと旧DBの場所は指定不可")
    args = parser.parse_args(argv)
    if (args.library_readonly_db is None) != (args.library_image_root is None):
        parser.error("library-readonly-db and library-image-root must be specified together")
    if args.download_sandbox is not None and (args.library_readonly_db is not None or args.pixiv_cookie_file is not None):
        parser.error("download-sandbox is synthetic-only; do not combine with a read-only library or pixiv cookies")
    if args.pixiv_download_root is not None:
        if (args.pixiv_cookie_file is None or args.download_sandbox is not None or
                args.library_readonly_db is not None or args.learning_sandbox is not None):
            parser.error("pixiv-download-root requires Cookie and isolated storage; other storage modes cannot be combined")
        try:
            _validate_managed_paths(args.pixiv_download_root, args.pixiv_cookie_file)
        except ValueError as error:
            parser.error(str(error))
    return RunConfig(args.host, args.port, args.native, args.check, args.learning_sandbox,
                     args.library_readonly_db, args.library_image_root, args.pixiv_cookie_file,
                     args.download_sandbox, args.pixiv_download_root)


_registered = False


def register_pages(learning_sandbox: Path | None = None,
                   library_readonly_db: Path | None = None,
                   library_image_root: Path | None = None,
                   pixiv_cookie_file: Path | None = None, live_client=None,
                   download_sandbox: Path | None = None,
                   pixiv_download_root: Path | None = None) -> None:
    """NiceGUIのHTTPルートを登録する。呼ぶだけではサーバーを起動しない。"""
    global _registered
    if _registered:
        return
    from nicegui import app, ui

    from pixivvault_nicegui.ui.shell import build_demo_page
    from pixivvault_nicegui.media_routes import register_media_routes
    from pixivvault_nicegui.services import get_services

    if pixiv_cookie_file is not None:
        if live_client is not None:
            raise ValueError("choose one pixiv client source")
        if pixiv_download_root is not None:
            _validate_managed_paths(pixiv_download_root, pixiv_cookie_file)
        from pixivvault_nicegui.pixiv_read_client import LivePixivReadClient
        live_client = LivePixivReadClient(pixiv_cookie_file)
    try:
        services = get_services(app, learning_sandbox=learning_sandbox,
            library_readonly_db=library_readonly_db, library_image_root=library_image_root,
            live_client=live_client, download_sandbox=download_sandbox,
            pixiv_download_root=pixiv_download_root)
    except BaseException:
        if live_client is not None:
            live_client.close()
        raise
    register_media_routes(app, services.images)

    async def close_services() -> None:
        import asyncio
        await asyncio.to_thread(services.close)

    app.on_shutdown(close_services)
    ui.page("/")(build_demo_page)

    @app.get("/healthz", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": "demo" if services.data_mode == "synthetic" else "readonly",
                "data": services.data_mode}

    @app.get("/readyz", include_in_schema=False)
    def ready() -> dict[str, bool]:
        # HTTP listenだけでなく、UIが起動して接続したかをnative試験で区別する。
        from nicegui import Client
        return {"ui_connected": any(c.has_socket_connection for c in Client.instances.values())}

    _registered = True


def main(argv: Sequence[str] | None = None) -> int:
    config = parse_args(argv)
    if config.check_only:
        print(json.dumps({
            "python": sys.version.split()[0],
            "nicegui": importlib.metadata.version("nicegui"),
            "mode": "readonly" if config.library_readonly_db is not None or config.pixiv_cookie_file is not None else "demo",
            "host": config.host,
            "port": config.port,
            "native_requested": config.native,
            "native_available": importlib.util.find_spec("webview") is not None,
            "data": ("pixiv-readonly" if config.pixiv_cookie_file is not None else
                     "library-readonly" if config.library_readonly_db is not None else "synthetic"),
            "learning_storage": "sandbox" if config.learning_sandbox is not None else "memory",
            "download_storage": ("managed-pixiv" if config.pixiv_download_root is not None else
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
        app.native.window_args.update(x=layout.x, y=layout.y)
        # pywebview otherwise cancels even an explicit CSV download. In this
        # newly created Windows app only, retain its owned SaveFileDialog so
        # the user chooses the destination (and confirms any replacement).
        app.native.settings["ALLOW_DOWNLOADS"] = True

    registration = {}
    if config.learning_sandbox is not None:
        registration["learning_sandbox"] = config.learning_sandbox
    if config.library_readonly_db is not None:
        registration["library_readonly_db"] = config.library_readonly_db
        registration["library_image_root"] = config.library_image_root
    if config.pixiv_cookie_file is not None:
        registration["pixiv_cookie_file"] = config.pixiv_cookie_file
    if config.download_sandbox is not None:
        registration["download_sandbox"] = config.download_sandbox
    if config.pixiv_download_root is not None:
        registration["pixiv_download_root"] = config.pixiv_download_root
    register_pages(**registration)
    ui.run(
        host=config.host,
        port=config.port,
        native=config.native,
        show=False,
        reload=False,
        title="PixivVault-NiceGUI / 移植デモ",
        # NiceGUIではwindow_sizeの指定だけでもnativeが有効になる。
        window_size=window_size,
        binding_refresh_interval=None,
    )
    return 0
