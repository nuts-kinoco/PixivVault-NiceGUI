# フェーズ移行前レビュー — 2026-10-22

基準: 公開PR #1のhead `7125009928981a1247c0a5b67363ba0de1855a7e`。
対象: 専用worktreeの全画面ビューア・画像保持、学習DBアダプタの未接続実装、旧Flet比較用の準備。
実装担当とは別の2workerが読取レビューし、統合担当が反例・修正・実ブラウザ結果を確認した。
本番DB・実画像・Cookie・pixiv API・元アプリの作業ツリーは変更していない。

前半の判定・実行件数・次のゲートは07:58時点のレビュー記録。引継ぎ後の実施結果は末尾の追補を優先する。

## 判定

**画像/ビューアの実装単位は、下記の修正と回帰確認まで完了。段階3の性能受入と、DB永続接続の受入は保留。**
公開済み基盤のCI成功を、未コミットの後続実装のCI成功とは扱わない。
閲覧6画面、実保存、全DPI、製品切替が完成したという意味ではない。

## 指摘と対応

| 重要度 | 対象 | 根拠・再現 | 対応・判定 |
|---|---|---|---|
| 高 | `ui/verified_image.js` 修正前50–81行 | 先読み用画像のdecode後に表示DOMを交換し、表示ノードのdecodeは後から検証していた。実ブラウザで表示ノードのdecodeを保留すると、`old_connected=false`、次ページが可視となった。 | 候補を不可視のままmountし、その実ノードのdecode・資源・世代を検証後、同じkeyのまま表示へ昇格。旧表示は検証済みのfrontだけ保持。修正後 `old_connected=true`、`same_node=true`。 |
| 高・上記と同根 | `ui/verified_image.js` の旧画像保持 | Aを表示、Bの表示ノードdecodeを保留、Cを要求してdecodeを失敗させると、未検証Bを「旧画像」として戻す余地があった。 | 同じ実ブラウザ故障注入でAのDOMが継続し、Bの遅着で置き換わらず、再試行で検証済みのCノードそのものが表示されることを確認。 |
| 接続前の阻害要因 | ローカル準備中 `learning_repository.py` のUndo | 一時DBでAがlike、別RepositoryのBが同作品をsuperLikeに更新。その後Aの古いUndoで履歴と嗜好が消え、例外も出ないことを実証。 | UIは`MemoryJudgmentLedger`のまま。単一writerの保証、またはトランザクション内での現行判定/世代照合と競合試験が成立するまで永続接続しない。旧SQLを無断で変更して解決済みとしない。 |
| 性能受入未完 | `benchmark_native_pixels.py` と旧Flet fixture | 校正・画像ハッシュ・キャッシュ条件・計測時計の実装は存在するが、両実装の同条件校正と比較結果ファイルはまだない。 | p50/p95や改善率は未測定。表示だけを確認したFletウィンドウを「性能比較済み」と記録しない。 |
| 回帰ではない | I/Tabによる詳細切替 | 原版`artbook_viewer.py:1135–1137`で明示された操作。 | 「操作そのまま」の範囲で維持。通常のTabフォーカス移動との両立は別の操作仕様検討とする。 |

## 実行した確認

- 再開時の全pytest: **154 passed / 5.37秒**。
- 修正後の全pytest: **154 passed / 5.32秒**。対象単位10件も別途成功。
- 修正前の追加ブラウザ試験は意図した画像保持違反でexit 1。コンソール例外やサーバー起動失敗による赤ではない。
- 修正後の実Edge: 一覧・学習・画像競合の3scriptがexit 0。画像競合は既存7項目に表示ノードdecodeの遅延/失敗、A/B/C競合、同ノード昇格など6項目を追加し、**13項目成功**。
- ブラウザ試験前後の製品ソースハッシュは一致。予期しないconsole/page errorは0。試験用サーバーのポート解放を確認。
- 学習DB/旧Flet fixture/計測器の限定pytest計23件成功。ただし複数Repositoryの実証されたUndo競合と、実機測定不足は別の未解決項目である。
- `git diff --check`成功。画像部品の修正後に独立再レビューを行い、対象のdecode前採用/未検証候補への復帰経路がなくなったことを確認した。

実行証拠はignoredの `artifacts/review-20261022-0750/` に保存:
`pytest.xml`、`pytest-after.xml`、`decode-before/image_races/success.json`、
`decode-after/image_races/success.json`、`browser-after/runner-result.json`、
`browser-after/image_races/success.json`。
生の画像・端末プロセス情報・ログはgitへ同梱しない。

## 次のゲート

1. このビューア単位をコミットし、その正確なコミットでCIを確認する。DB/比較準備の未解決差分は混ぜない。
2. 新旧同条件の校正画像を目視し、冷/温キャッシュとキャプチャ負荷を区別した測定結果を得るまで、速度の合格判定を保留する。
3. DB永続接続前に、上記の古いUndo競合を失敗として検知・防止する方針と試験を確定する。現在のUIからこのアダプタを利用しない。
4. 最新の全画面ビューア版のnative再ビルド、DPI/フォーカス/終了、残り画面の互換受入を続ける。旧配布exeの成功を最新版へ流用しない。

## 引継ぎ後の実機再受入と表示修正

`a5d42aa6d54a81181af3661dc331df89e96bc927` のコミットだけを `git archive` で取り出し、専用srcをPYTHONPATHに固定してonedir/noconsoleを再ビルドした。別フォルダーへコピーしたexeでも、2026-10-22 08:59 JSTにビューアopen/decode/close、学習判定/Undo/復帰画像decode、最小化/復帰、WM_CLOSEによる通常終了、全観測子プロセス終了、ポート解放が成功。強制cleanup、console/page/process-log errorは0。実機条件はDPI175%、WebView2 155.0.4283.62であり全DPI・別PCの受入ではない。

同じ実画面確認で、小さなfixture画像がviewer領域へ拡大されず自然寸法に留まる問題と、ボタンにQuasar既定primary色が残る問題を確認した。今回の修正はviewer専用CSSでimgのwidth/heightを100%とし、containで縦横比を維持する。全画像部品への拡大指定は学習画面の既存寸法契約を変えたため採用せず、`verified_image.js`はa5d42aaと同じ内容を保持した。ボタンのcolorをテーマ側へ集約し、文字・アイコンは旧トークンの白/灰色、操作ボタン背景はsurface色にした。

実Edgeの1280×720/1920×1080で、画像枠とimgの寸法一致、contain、自然寸法より大きい表示倍率、前頁/閉じる/詳細のcomputed colorを検証した。学習の既存画像縦横比試験を維持し、一覧/学習/画像競合の3scriptが全て成功。13画像競合シナリオも成功し、予期しないconsole/page errorは0、製品src前後ハッシュ一致。独立担当が画像世代/decode/同一node昇格への影響を読取確認した。

証拠はignored `artifacts/native-a5d42aa-20261022-01/` の `native-acceptance.json`、`packaging-check.json`、`ui-fit-browser-scoped/`。この表示修正後のexeは別途その正確なコミットで再ビルド・検証する。a5d42aaのexe合格を後続コミットへ流用しない。

### 原版の表示領域についての訂正

原`gui.py`のPageにはアプリ見出し・接続表示・タブナビと各タブが兄弟として配置される。原viewerは`ranking_tab.py`のtab内Stackで本文を覆い、タブ離脱時には`close_viewer_if_open()`が呼ばれる。現在の隔離Flet launcherはランキングtabだけを表示し、元shellを省略している。そのため隔離画面の余白をそのまま原製品のviewport互換基準にはできない。

現在のNiceGUIデモはviewport全体を覆う構成を保持しているが、**原製品のヘッダー/ナビの視認と閲覧中のタブ切替まで同じという受入は未完**。これまでの「原版と同じ全画面」という説明はこの点を含めて限定する。元shellを含めた隔離比較で表示境界とタブ切替時のclose/キー復帰を確認するまで、製品全体の外観・操作一致を合格にしない。

## 公開検証と残件の更新

- UIはPR #2の`3947c9543b1f839ccbc9d702247470f5d655414f`へpush済み。同SHAのarchive131試験、Windows再ビルド、別フォルダーexeの操作と通常終了、CI3ジョブに成功。ブラウザCI初回のChromium取得TLS失敗は、失敗jobの再実行で成功した。生証拠は `artifacts/native-ui-20261022-02/`。
- 学習DBは別Draft PR #3へ分離。古いUndoの反例を回帰化し、更新triggerとBEGIN IMMEDIATEの世代照合で拒否。20件の互換/競合試験と、読取後〜取消までのwriter排他を確認する追加1件を実装と別担当が検証した。UIへの永続接続、セッションをまたぐ操作IDの冪等性は依然未受入。
- 計測器はwindow方式の白い取得失敗を記録してscreen方式を追加し、所有PID/作成時刻・位置/寸法・可視・9点被覆を確認。両backendの全frameを目視し30/30を観測した。実装・計測結果の独立レビュー済み。具体値と大きなcapture overhead、画像面積/元shell/配布方式差は `NATIVE_OBSERVATIONS_2026-10-22.md`。製品の性能合格にはしていない。
- 統合179試験は未分離の作業ツリー全体の数で、UI PRの131件とは別。元Databaseの115メソッドAST一致と原UI7ファイルのhash一致を確認し、専用両プロセス群だけをWM_CLOSEで通常終了した。

次の具体的なゲートは、rankingタブ内viewerへの表示境界合わせ、タブ離脱時のfocus/キー復帰、取得負荷を下げて条件を揃えた性能比較、全DPI/別PCである。残り6タブや本番保存の完了を示すものではない。
