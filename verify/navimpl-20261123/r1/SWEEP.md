# S4 ナビ再編の設計・実装・検証対応表

設計正本は [統合設計書](../../../docs/design/UI_NAV_RESTRUCTURE_20261122.md)、[操作モック](../../../docs/design/UI_NAV_RESTRUCTURE_20261122.html)、[監査仕様](../../../docs/UIUX_AUDIT_SPEC_20261122.md)、[NAV レビュー回答](../../../docs/review/NAV_REVIEW_RESPONSE_20261122.md)、[Dots NAV4](../../../docs/review/replies/DOTS_NAV4_UIINTEG_R3_SUMMARY_20261123.md)。

| 設計項目・原受入 | 製品での接続 | 試験・証拠 |
| --- | --- | --- |
| FL-A02/A03 起動既定、別名、利用条件、明示時だけ自動処理 | [startup_tab.py](../../../src/pixivvault_nicegui/startup_tab.py)、[bootstrap.py](../../../src/pixivvault_nicegui/bootstrap.py)、[shell.py](../../../src/pixivvault_nicegui/ui/shell.py) | [明示 CLI の red](STARTUP_EXPLICIT_RED.txt) → [green](STARTUP_EXPLICIT_GREEN.txt)、元の起動キー・履歴・利用不可の回帰 |
| FL-V02/V03 等幅10、表示名「カスタムランキング」、右上7操作 | [shell.py](../../../src/pixivvault_nicegui/ui/shell.py)、[名称定数](../../../src/pixivvault_nicegui/ui/navigation_labels.py)、[theme.py](../../../src/pixivvault_nicegui/ui/theme.py) | [タブ契約 red](01-red.txt) → [green](01-green.txt)、[寸法 red](04-red.txt) → [green](04-green.txt)、[実 DOM の寸法](BROWSER.json) |
| N1、A11/A12 狭幅ナビの順逆、Esc、IME、中心点の遮蔽 | [navigation.js](../../../src/pixivvault_nicegui/ui/navigation.js)、[focus_recovery.js](../../../src/pixivvault_nicegui/ui/focus_recovery.js) | [固定 640 と 200% の操作記録](BROWSER_KEYS.json)、[フォーカス・Tab 循環の回帰](UI_FINAL_REGRESSION.txt) |
| 本番相当 DB 警告、保存先と取得停止入口の同居 | [shell.py](../../../src/pixivvault_nicegui/ui/shell.py)、[theme.py](../../../src/pixivvault_nicegui/ui/theme.py)、[既存取得停止 UI](../../../src/pixivvault_nicegui/ui/shared_get_recovery_view.py) | [通常／長文・取得停止・未解消2件の実 DOM](BROWSER.json)、[DB バナーの回帰](04-green.txt) |
| H2、FL-O01/C20 自動処理の要約、意味の異なるバッジ、確認済みの持続 | [automation_dialog.py](../../../src/pixivvault_nicegui/ui/automation_dialog.py)、[managed_scheduler.py](../../../src/pixivvault_nicegui/managed_scheduler.py)、[services.py](../../../src/pixivvault_nicegui/services.py)、[metadata_auto.py](../../../src/pixivvault_nicegui/metadata_auto.py) | [関連検証](02-green.txt)、[定期停止の red](AUTOMATION_STOP_RED.txt) → [green](AUTOMATION_STOP_GREEN.txt)、[併発表示・確認・解消](BROWSER.json) |
| 詳細の遅延生成、詳細タイマーの活性、設定からの移動、共有ログ | [automation_dialog.py](../../../src/pixivvault_nicegui/ui/automation_dialog.py)、[scheduler_view.py](../../../src/pixivvault_nicegui/ui/scheduler_view.py)、[metadata_view.py](../../../src/pixivvault_nicegui/ui/metadata_view.py)、[preferences_view.py](../../../src/pixivvault_nicegui/ui/preferences_view.py)、[shared_log_view.py](../../../src/pixivvault_nicegui/ui/shared_log_view.py) | [開閉・共有ログの回帰](02-green.txt)、[矢印・Esc と設定への復帰](BROWSER.json) |
| 再編後の一度きりのヒントと設定の2行 | [automation_dialog.py](../../../src/pixivvault_nicegui/ui/automation_dialog.py)、[preferences.py](../../../src/pixivvault_nicegui/preferences.py)、[preferences_view.py](../../../src/pixivvault_nicegui/ui/preferences_view.py) | [表示対象の既存回帰](PREFERENCES_HINT_GREEN.txt)、[初回表示・設定DB再読込](HINT_RESTART.json) |
| 閉鎖中の保存完了通知と所有者、既存のデスクトップ通知との claim | [services.py](../../../src/pixivvault_nicegui/services.py)、[automation_dialog.py](../../../src/pixivvault_nicegui/ui/automation_dialog.py) | [定期処理の回帰](AUTOMATION_STOP_GREEN.txt)。複数クライアントと所有者変更が交差する配送の独立検証が残る |
| FL-Y02 よく効くタグ、1 行、残り件数、元の開閉キー | [personal_ranking_view.py](../../../src/pixivvault_nicegui/ui/personal_ranking_view.py)、[candidate_tags.js](../../../src/pixivvault_nicegui/ui/candidate_tags.js) | [red](03-red.txt) → [green](03-green.txt)、[ブラウザー操作記録](BROWSER.json) |
| A17/B9 手動開閉・画像やカードへの面積配分、学習四択 | [manual_toolbar.py](../../../src/pixivvault_nicegui/ui/manual_toolbar.py)、[theme.py](../../../src/pixivvault_nicegui/ui/theme.py)、各画面の既存コントロール接続 | [画面別実測](BROWSER.json)。達否・倍率条件は [OUTCOME](OUTCOME.md) |
| FL-K01/K02 マイブックマーク独立、DL済み作者詳細、別検索語と保存表示 | [library_view.py](../../../src/pixivvault_nicegui/ui/library_view.py)、[bookmark_view.py](../../../src/pixivvault_nicegui/ui/bookmark_view.py)、[card_grid.js](../../../src/pixivvault_nicegui/ui/card_grid.js)、[media_views.py](../../../src/pixivvault_nicegui/ui/media_views.py) | [説明と独立した入口の red](05-red.txt) → [green](05-green.txt)、[キャッシュ・借用の既存試験](RELATED_FINAL.txt) |
| H1 作品単位の選択／フォーカス復帰、作者詳細の見出し | [navigation.js](../../../src/pixivvault_nicegui/ui/navigation.js)、[library_view.py](../../../src/pixivvault_nicegui/ui/library_view.py)、既存 [bookmark_state.py](../../../src/pixivvault_nicegui/bookmark_state.py) | [選択・キャッシュの回帰](05-green.txt)、[タブ往復](BROWSER.json)。合成ブラウザーでの全入替と全メディア構成は未完 |
| FL-D7 取得停止と429、ページ外部通信0 | 既存停止経路、[棚卸し](../../../tools/p6_network_review.json) の式の照合 | [棚卸し試験](SMOKE_NETWORK.txt)、[ページ／サーバー外部通信](BROWSER.json)、[全体 pytest](PYTEST_FULL.txt) |
| 125% の事前下限 M1、Native と独立監査 | 監査仕様の同条件比較・判定 | 回収待ち。製品の隔離 Chromium 現在値を [OUTCOME](OUTCOME.md) に記録 |

## 原証拠を再実行するとき

作業ツリーの src と tests を PYTHONPATH に追加し、TEMP／TMP と各実行の scratch を担当の S: 一時領域に設定する。既存の隔離 Python から `-B verify/navimpl-20261123/r1/check_product.py --scratch <担当の未使用の一時ディレクトリ> --port <未使用のループバックポート>` を指定する。通常／併発の両テーマ、24条件を既定で測る。`--narrow-only` は固定640×400と200%の8条件。`--keyboard-matrix` は1280、固定640、200%に絞ったフォーカス・四択試験。合成データと偽クライアントで起動し、計測結果のエラー件数、外部通信数、プロセス残存と寸法を照合する。

ビューア初期化は [失敗時の生成箇所の差](VIEWER_SITES_RED.txt) と、ライブラリ／独立マイブックマークの通常HTML・遅延表示を含む [追加後の検証](VIEWER_SITES_GREEN.txt) を残した。

学習の傾向、ダウンロードの対象詳細は既存の開閉操作。共通ツールバーを接続した7画面の範囲は上表の実装を参照。
