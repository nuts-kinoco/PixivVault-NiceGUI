"""NiceGUIの独立デモ画面。"""

def build_demo_page() -> None:
    """NiceGUIの依存は画面構築時にだけ読み込む。"""
    from .shell import build_demo_page as build

    build()

__all__ = ["build_demo_page"]
