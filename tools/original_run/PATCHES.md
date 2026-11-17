# original_copy へ当てたパッチ（元リポ S:\Projects\PixivVault は無改変。基準 commit 3ac3aa0d の作業ツリー）
コピー: *.py（トップ）・tools\*.py（gui.py が tools\dedup を import する）・assets\・requirements.txt。
除外: Images / cache / build / dist / models / *.db / cookies.txt / Export zip / ログ。

1. main.py check_single_instance(port=25011) -> 環境変数 PV_LOCK_PORT（実機が 25010 を使用中のため別ポート必須）
2. main.py start_server(25010,...) -> 環境変数 PV_SERVER_PORT
3. main.py run_tray(...) -> PV_NO_TRAY=1 で呼ばない
4. main.py ft.run(...) -> PV_WEB_PORT があれば view=ft.AppView.WEB_BROWSER, port=PV_WEB_PORT
5. pixiv_client.py auto_extract_browser_cookies: PV_NO_BROWSER_COOKIES=1 なら即 expired を返す
   （未認証だと起動時に Chrome/Edge/Brave/Firefox の Cookie 抽出を自動試行する＝実 Cookie に触れるため必須）
データ保存先: DB は PIXIVVAULT_DB_PATH、cookies.txt・Images・cache・ログは cwd 相対（original_copy 直下）。
