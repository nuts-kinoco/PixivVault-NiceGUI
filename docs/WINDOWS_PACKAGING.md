# Windows onedir 配布（合成デモの開発機検証済み）

Windows / Python 3.13 の専用環境で、NiceGUI 3.18.0、PyInstaller 6.22.3、pywebview 6.2.1、pythonnet / clr_loader と本プロジェクトをインストールして実行する。検証に使用した間接依存の一覧は `requirements-lock.windows.txt`。

```powershell
.\tools\build_windows.ps1
```

入口は `tools/native_entry.py`。`freeze_support()` を呼んでから `bootstrap.main` に `--native` を渡す。`--check` 指定ならウィンドウやサーバーを起動せず、ランタイム情報を出力する。`--native` がすでにある場合は重複させない。`--check-output <absolute path>` は入口で除去し、`--check` の結果だけを指定 JSON ファイルに書き込む。出力先の親ディレクトリは事前作成が必要。

PyInstallerを直接使用する。`--onedir --noconsole`、NiceGUIの静的資産、pywebviewのWindows backend、CLR / pythonnetと配布メタデータを収集する。`--collect-data pixivvault_nicegui` で独自画像部品の `ui/verified_image.js` も同梱する。既存アプリの `main.spec` は使用しない。
生成先は `artifacts/windows-packaging/{build,dist,spec}` 専用領域。`--noconfirm` による既存成果物の更新はこの出力だけに限定する。既存アプリのコード/DB/Cookie/実画像は同梱対象ではない。

ビルド後のチェック：

1. `artifacts\windows-packaging\dist\PixivVault-NiceGUI\PixivVault-NiceGUI.exe --check --check-output "<repo絶対パス>\artifacts\windows-packaging\packaging-check.json"` を実行し、出力 JSON の `native_requested=true`、`native_available=true`、`mode=demo` と正常終了を確認する。`--check-output` は絶対パスのみ受理する。GUI subsystem で `stdout=None` のとき、`--check` 単独では JSON を保存せず、診断の終了コードのみ返す。ユーザー領域への暗黙のファイル作成はない。
2. exe を起動しウィンドウ表示、ページ/画像表示、クリック、入力、Undo 操作を検証する。WebView2 Runtime の有無と、Windows Forms / CLR assembly のロードエラーも確認する。
3. `tools/check_native_window.py --exe <配布exe> --inspect-ui --output <検証JSON>` で専用の一時ポートに起動し、表示・操作・最小化/復元・通常終了を検査する。プロセスIDと作成時刻が一致する自分のプロセスだけを観測する。`normal_exit=true`、`port_released=true`、`forced_cleanup=[]` と、画像/操作検査の成功を確認する。プロセス照会に失敗した場合は合格にしない。
4. 配布フォルダーを別の空ディレクトリへコピーして同じ確認を繰り返す。WebView2 が未導入の Windows では別途ランタイム導入が必要になりうる。

## 2026-10-22の実行結果

Python 3.13.11 / WebView2 155.0.4283.62を使う開発PCで、実ビルドと、配布フォルダーを別の空フォルダーへコピーしての検査を通過した。検証出力先は競合回避のため `artifacts/restart-windows-build` と `artifacts/restart-standalone` を使用した。

- exeの `--check` がexit 0。独自JSが同梱され、検証時のソースとバイト列が一致。
- WebSocket handshake、50カード、実画像decode、pywebviewブリッジを確認。
- 実WebViewでビューアの開閉と画像表示、学習判定→Undoを操作。Undo後の画像URLと実画素のdecodeも確認。
- 最小化→復元→WM_CLOSEによる通常終了、観測した子プロセスの終了、専用ポートの解放に成功。強制cleanupと予期しないconsole/page/process-logエラーなし。
- 実測クライアント領域1907×1044、`devicePixelRatio=1.75`。全DPI条件の検証ではない。

証拠: `artifacts/restart-standalone/native-acceptance.json`。画像とプロセス診断を含む生の成果物はgitへ追加しない。
`tests/test_packaging.py` と `tests/test_native_check.py` は入口や監視器の契約試験であり、この実機試験の代わりではない。

未確認: まっさらな別PC、WebView2未導入環境、トレイ、URI、全DPI、長時間運用、製品データ/実ダウンロードとの終了整合、完全な再現ビルド。実行形式の公開リリースはまだ行っていない。
