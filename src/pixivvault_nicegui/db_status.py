"""DB状態バナーの内容（元 gui.py:470-483 / main.py:357-364）。

元は「本番DB（M:/2次/PixivVault/pixiv_vault.db）」を開いているとき赤帯で警告する。
移植版には単一のDBファイルがないため、実Cookieで書き込む管理保存先を使う起動を
「本番」に当てる。デモ・読取専用の旧DB・隔離保存先は通常表示にする。
"""
import os


def db_status(services):
    """(本番か, 表示文) を返す。文言は元と同じ。"""
    root = getattr(services, 'pixiv_download_root', None)
    if root is not None:
        return True, f"⚠️⚠️ 本番DBを使用中: {os.path.abspath(root)}"
    for name in ('library_readonly_db', 'download_sandbox', 'learning_sandbox'):
        path = getattr(services, name, None)
        if path is not None:
            return False, f"DB: {os.path.abspath(path)}"
    return False, "DB: なし（サンプル作品・保存しません）"
