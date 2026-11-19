# wt-w Sol 指摘の修正証拠

- 検証した製品候補: `11250478128534116de0cff735c4d41b08b1bc32`。
- 指定の隔離 pytest 全体: `full-bulk-fixed.log`、2624 PASS / 2 SKIP / 3 DPAPI 除外 / 失敗 0。
- `ui-smoke-bulk-fixed.log` と `ui-smoke/summary.json`: 全14画面×2サイズ×2テーマ PASS。スクリーンショットは `ui-smoke/screens/`。元の summary 中の出力先は実行時の一時ディレクトリであり、このディレクトリへ保存した。
- Sol 第1周: `independent/`。第2周候補 `b614693` の再現: `independent2-final/`、94 PASS、原ケース26 PASS、追加4 PASS/1 FAIL。追加の巻き戻し修正後 `19499692ceff54ceb3ddae61e3ab75a5eb1d8c52` は `independent2-recheck/` で独立再確認23 PASS。
- `1949969` から `1125047` の製品差分は作者選択チップの表示軽量化のみ。保存領域・設定・再開処理は同一。
- `full-final.log`: 一つ前の候補の全体実行は2621 PASS / 1 FAIL / 2 SKIP / 3除外。20,000人選択1.396秒で閾値超過。`red-bulk.log` の部品数テストで失敗を確認後、性能修正し、`bulk-fixed.log` で8 PASS、20,000人選択0.547秒。
- `crash-second.log`: 巻き戻し修正の途中候補でクラッシュ復旧4 FAIL。修正後 `crash-fixed.log` 15 PASS と `fold-fixed.log` 6 PASS。
- シンボリックリンク権限の2ケースは SKIP。実機プロファイル、実 Cookie、実 Pixiv、実機ポートは使用していない。実機 UI と元画面の同条件比較は未実施。
- 作品だけが失敗した作者の `last_downloaded` は既存仕様のまま。
## 旧版の畳み込み済み保管庫への追加修正

- 製品候補 `d1c063631d3d9cae012541d36e7183fb7b2e184a`。それ以前の合格候補 `1125047` から、保存領域と専用回帰試験に追加差分あり。
- `preexisting_v5_repro.py` は旧候補 `099203e` を隔離領域に読み込む。`preexisting_v5_repro.log` は旧候補で保存済み1件が現候補で0件に見える失敗、`preexisting_v5_fixed.log` は修正後に拒否した実測。
- `red-preexisting.log` / `red-stage.log` は修正前の失敗。`green-stage.log` は追加ケースと稼働中異常・未公開クラッシュ8 PASS。`pending-crash-final.log` はクラッシュ・2書き手15 PASS。`pending-storage-suite.log` は追加前半の既存保存領域74 PASS。
- `full-preexisting.log`: 修正候補の全体 2627 PASS / 2 SKIP / 3 DPAPI 除外 / 失敗 0。`ui-smoke-preexisting.log` と `ui-smoke-preexisting/summary.json`: 14画面×2サイズ×2テーマ PASS。画面は同ディレクトリ `screens/` に保存。
- Sol の指定2周は既に終了しており、最後の `d1c0636` 保存領域差分は独立検証未実施。先行候補への Sol の結果をこの差分の合格に流用しない。
- 古い移行途中の保管庫で、旧索引が指さない作品とジャーナルの痕跡が残り公開済みか判断できない場合は、作品を推測で取り込まずオープンを拒否する。