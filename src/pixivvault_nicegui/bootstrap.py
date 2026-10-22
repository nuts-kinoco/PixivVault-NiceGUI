"""独立デモの起動入口。実データ・既存アプリ・外部APIには接続しない。"""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import multiprocessing
import sys
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True, slots=True)
class RunConfig:
    host: str = "127.0.0.1"
    port: int = 8089
    native: bool = False
    check_only: bool = False


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("ポートは整数で指定してください") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("ポートは1〜65535で指定してください")
    return port


def parse_args(argv: Sequence[str] | None = None) -> RunConfig:
    parser = argparse.ArgumentParser(description="PixivVault-NiceGUI 独立デモ")
    parser.add_argument("--host", choices=["127.0.0.1", "::1"], default="127.0.0.1")
    parser.add_argument("--port", type=_port, default=8089)
    parser.add_argument("--native", action="store_true", help="専用ウィンドウで起動（実機検証待ち）")
    parser.add_argument("--check", action="store_true", help="環境情報だけを表示し、サーバーを起動しない")
    args = parser.parse_args(argv)
    return RunConfig(args.host, args.port, args.native, args.check)


_registered = False


def register_pages() -> None:
    """NiceGUIのHTTPルートを登録する。呼ぶだけではサーバーを起動しない。"""
    global _registered
    if _registered:
        return
    from nicegui import app, ui

    from pixivvault_nicegui.ui.shell import build_demo_page
    from pixivvault_nicegui.media_routes import register_media_routes
    from pixivvault_nicegui.services import get_services

    services = get_services(app)
    register_media_routes(app, services.images)
    app.on_shutdown(services.close)
    ui.page("/")(build_demo_page)

    @app.get("/healthz", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": "demo", "data": "synthetic"}

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
            "mode": "demo",
            "host": config.host,
            "port": config.port,
            "native_requested": config.native,
            "native_available": importlib.util.find_spec("webview") is not None,
            "data": "synthetic",
        }, ensure_ascii=False))
        return 0
    if config.native and importlib.util.find_spec("webview") is None:
        raise SystemExit("native用のpywebviewは未導入です。Windows配布検証の段階で専用環境へ追加します。")

    multiprocessing.freeze_support()
    from nicegui import ui

    register_pages()
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
