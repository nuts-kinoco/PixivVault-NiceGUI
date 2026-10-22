# ビューア表示境界とタブ移動のレビュー

対象は PR #2 の `3947c9543b1f839ccbc9d702247470f5d655414f` からの追加修正。
2026-10-22 にユーザーがコードレビュー後のマージを承認したため、既知の仕様差分をマージ前に解消する。
共有worktree・元製品・実データは変更しない。

## 原版の契約と修正前の反例

- 原 `gui.py:6125–6152` ではヘッダー・ナビゲーションと各タブは別の要素。
- 原 `ranking_tab.py:2589–2595` では画集ビューアはランキングタブ内のStackに置かれる。
- 原 `gui.py:5987–5990` はランキングタブ離脱時にビューアを閉じる。
- I / Tab による詳細の切替は意図された原仕様であり、維持する。

10:15 JST、修正前コードで新しいブラウザ回帰試験が失敗した。
1280×720 のページで、タブ内領域は `(16, 137.5, 1248, 566.5)` だがビューアは `(0, 0, 1280, 720)`。
学習タブの中心をhit-testすると別要素に覆われ、`tabReachable=false` だった。
証拠: `artifacts/viewer-boundary-20261022/before/gallery/result.json`。

## 変更

- `.pv-content` を包含ブロックとし、ビューアはその中を覆うabsolute overlayとする。ヘッダーとタブは操作できる。
- ダイアログは非modalとして表現する。通常の閉じる操作は表示中の元カードへフォーカスを戻す。
- タブ離脱では元カードへの復元を抑止し、画像要求を無効化する。学習は有効な画面にフォーカスし、ランキング再訪では選択中のタブにフォーカスする。
- open / closeで共通のフォーカス世代を進め、遅れたrAFが新しい操作のフォーカスを奪わないようにする。接続・可視・aria-hidden/inertも確認する。
- 検証済みfrontと不可視decode候補を分離する `verified_image.js` は変更しない。

## 検証と独立レビュー

実装者と別の担当が状態・画像世代・非modal・フォーカス・回帰試験をレビューした。
初回の修正後試験ではランキング再訪時のフォーカスがinactiveとなり、ランキングへの明示復元を追加した。
レビューで1280×720の画像拡大率のassertを限定する必要がないと指摘され、両viewportで従来の `fitted_scale > 1` を維持した。

- `python -X utf8 -m pytest -q --junitxml=artifacts/viewer-boundary-20261022/pytest-after.xml`: 132 passed / 5.43秒。
- `python -X utf8 -m pip check`: broken requirementsなし。
- `python -X utf8 tools/run_browser_checks.py --channel msedge --output artifacts/viewer-boundary-20261022/after2`: 一覧・学習・画像競合の3スクリプト成功。source hashは前後一致。
- 厳密な拡大率assertを復元後、同runnerの `CHECKS=('check_gallery_browser.py',)` で `after3` へ一覧だけ再実行し成功。製品ソースはafter2から不変。
- 1280×720 / 1920×1080でviewerとcontentの矩形一致、タブ非被覆、画像contain、詳細による画像領域縮小なし、通常closeの元カードfocusを確認。
- タブ往復で検索語「架空」・100件・小カード・既存DOM・スクロールを保持。移動先のキーボード判定1回とUndo、再訪後の新しい閲覧も成功。
- 既存13件に、初回読込中の離脱・実DOM decode中の離脱・古いcloseフォーカスcallbackの逆順完了を加え、画像/操作競合16件が成功。
- 予期しないconsole/page errorは0。初回HTTP 404/503故障注入は別記録。
- primeは2寸法のviewer全画面PNGでヘッダー・ナビ・作品0003・2/3表示・画像・常設情報行を目視した。

ブラウザrunnerは所有する検証サーバーを明示停止する。`app.returncode=1` とポート解放を記録しており、これをnativeアプリの正常終了の証拠にはしない。

## コミット後の受入

公開head `43d0c64bd7acb6f96f387f9ff3639478417c278f` のWindows/Ubuntu/Chromium CI3ジョブが成功（run `39705815440`）。同SHAのarchive132試験と、onedir/noconsole再ビルド・別フォルダーexeの実WebView受入が10:29 JSTに成功した。viewerタブ境界、通常close、学習への離脱/キー判定/Undo、ランキング復帰focus、最小化/復帰/通常終了を確認。観測8プロセス終了・ポート解放・強制cleanupなし。詳細は `WINDOWS_PACKAGING.md`。旧3947c95のexeは流用していない。
ユーザーのレビュー後マージ指示に基づき、PR #2は `8df86671bbb0d7c4639e8dbb07687cb792f39566` としてmainへ統合した。
原版全体の外観・性能、全DPI、別PC、残画面は引き続き未受入であり、この変更の成功をフェーズ全体の完了とはしない。
各段階は操作・仕様・試験・実機・性能の必須ゲートが揃ってから完了と判定する。
