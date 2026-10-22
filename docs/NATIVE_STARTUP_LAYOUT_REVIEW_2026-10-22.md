# Windows初期配置 — 製品側の修正と受入

最終目標はPixivVault全体の移植完了。本単位は、タグ条件の受入で判明した初期ウィンドウの画面外配置を、検証器の補助ではなく製品側で修正するマイルストーン。基準mainはPR #19の `13ec540c2d3364c7cd0bcf4d9ad1d1c73c784bd8`。

## 原因と修正

旧起動は `ui.run(native=True, window_size=(1920,1080))` だけを指定し、位置をバックエンドのCenterScreenへ任せていた。開発機175% DPIではclient矩形 `(496,536,3832,2362)` が仮想画面下端2160を202px超え、検証器のGDI baselineが正しく拒否した。前単位では専用検証窓の明示初期配置で操作受入を分離したが、製品の自動配置は未解決だった。

インストール済みpywebview 6.2.1 `platforms/winforms.py:198-229` は入力された幅/高さ/x/yを論理座標とし、実ウィンドウのDPIに応じて一度だけ物理座標へ変換する。NiceGUI 3.18.0 `native/native_mode.py` は `app.native.window_args` のx/yを新規ウィンドウへ転送する。

新規 `native_layout.py` はプライマリモニターの物理作業領域とDPIから、内側に丸めた論理サイズ・位置を計算する。1920×1080が収まる環境では維持し、小さい作業領域でだけ縮める。タスクバー領域を除き、pywebviewの最低200×100論理pxを下回る環境は黙ってはみ出さず失敗として扱う。

Win32測定はWindows native起動時だけ、DB/セッションの登録より前に行う。現在のスレッドを一時的にPMv2へ切り替え、プライマリのMONITORINFO.rcWorkを取得し、自分で作成した非表示1px STATICウィンドウのGetDpiForWindowでDPIを測る。測定ウィンドウは破棄し、スレッドのDPIコンテキストもfinallyで復元する。既存の別アプリやWebView子は移動・変更しない。ブラウザ起動、import、`--check`ではこの測定を呼ばない。

## 独立レビュー・単体試験

実装者とは別の担当が3ファイルを静的監査し、限定APPROVE。座標の一重変換、内向き丸め、最低サイズ、失敗時の測定HWND破棄とDPI復元、native限定の登録順序を確認した。レビュー担当自身のGUI実行ではなく、実機結果は下記の統合担当実行による。

- 対象70 pytest成功。DPI96/120/144/168/192/240/288/384、負座標/タスクバー/小画面等の純粋計算、Win32境界の故障注入、browser/check非干渉を含む。
- 全 **635 pytest成功**、既存TestClient非推奨警告1件。syntax、依存整合、Git差分検査成功。
- 純粋DPI計算の成功を、それら全DPIの実モニター受入と読み替えない。

## 配置補助なしの実機受入

証拠root: `artifacts/native-startup-20261022`。いずれも合成SQLite・合成画像だけを使用し、検証器へ `--client-origin` は渡していない。復帰判定・owner画素検査・閾値・検証器の実装には変更なし。

- 実測プライマリ作業領域 `(0,0,3840,2076)`、DPI168。製品の初期設定は1920×1080、論理origin `(137,53)`。実clientは `(251,144)`、3336×1826物理px、CSS1907×1044で、従来の画像表示面積を保持。
- `interpreter-first/native.json`、`native-copy-first/native.json`、`native-copy-repeat/native.json` の3走すべて総合PASS。コピーexeの連続2起動も同じclient位置/寸法/DPIで画面内。
- ランキング/学習/旧DB画像/ブックマーク/タグ条件/フォーカスを実操作。最小化/復帰のGDIは基準・復帰とも安定2標本、平均画素差 `0.0000106705435`（上限1.0）。各コピーexeでowned8プロセス通常終了、WM_CLOSE、port解放、強制cleanup・エラーなし。
- onedir/noconsoleコピーexe SHA-256: `b49de6ffd967076aad1b0f9559cb6fa6d2d1eabf63a69b176540a64d29fd59a4`。製品43入力を凍結しbuild後/受入後一致、配布1050ファイルのコピー一致、合成DB/画像3ファイルも無変更。`build-inputs.json`、`native-copy-manifest.json`、`fixture-before.json`、`final-evidence.json`。
- `edge-default`の4操作検査、`edge-readonly`の旧DB更新失敗/復旧、`edge-transport`の合成認証transport/未DL画像/条件保持/2clientも成功。1280×720と1920×1080、外部通信なし、port解放を確認。ブラウザ経路へnative配置処理は入らない。

## 閉じた範囲と残件

開発機プライマリ175% DPIでの自動初期配置は、補助移動なしの配布exe受入まで完了した。任意モニター指定、モニター間移動、全DPIの実機、別PC、長時間/最大化・トレイ/URI連携、実CookieでのNiceGUI全操作は別ゲート。これを段階1全体・段階4全体・移植完了とは呼ばない。レビューと正確なhead/main CIを確認してから統合する。
