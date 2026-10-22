"""独立デモの起動入口。実データ・既存アプリ・外部APIには接続しない。"""

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
    args = parser.parse_args(argv)
    if (args.library_readonly_db is None) != (args.library_image_root is None):
        parser.error("library-readonly-db and library-image-root must be specified together")
    return RunConfig(args.host, args.port, args.native, args.check, args.learning_sandbox,
                     args.library_readonly_db, args.library_image_root, args.pixiv_cookie_file)


_registered = False


def register_pages(learning_sandbox: Path | None = None,
                   library_readonly_db: Path | None = None,
                   library_image_root: Path | None = None,
                   pixiv_cookie_file: Path | None = None, live_client=None) -> None:
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
        from pixivvault_nicegui.pixiv_read_client import LivePixivReadClient
        live_client = LivePixivReadClient(pixiv_cookie_file)
    try:
        services = get_services(app, learning_sandbox=learning_sandbox,
            library_readonly_db=library_readonly_db, library_image_root=library_image_root,
            live_client=live_client)
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
        }, ensure_ascii=False))
        return 0
    if config.native and importlib.util.find_spec("webview") is None:
        raise SystemExit("native用のpywebviewは未導入です。Windows配布検証の段階で専用環境へ追加します。")

    multiprocessing.freeze_support()
    from nicegui import ui

    registration = {}
    if config.learning_sandbox is not None:
        registration["learning_sandbox"] = config.learning_sandbox
    if config.library_readonly_db is not None:
        registration["library_readonly_db"] = config.library_readonly_db
        registration["library_image_root"] = config.library_image_root
    if config.pixiv_cookie_file is not None:
        registration["pixiv_cookie_file"] = config.pixiv_cookie_file
    register_pages(**registration)
    ui.run(
        host=config.host,
        port=config.port,
        native=config.native,
        show=False,
        reload=False,
        title="PixivVault-NiceGUI / 移植デモ",
        # NiceGUIではwindow_sizeの指定だけでもnativeが有効になる。
        window_size=(1920, 1080) if config.native else None,
        binding_refresh_interval=None,
    )
    return 0
