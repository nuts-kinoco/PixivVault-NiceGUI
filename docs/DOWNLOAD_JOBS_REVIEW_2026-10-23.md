# 合成保存・永続キューの移植単位

段階5のマイルストーン。最終ゴールはPixivVault全体の移植完了であり、実pixivダウンロードや段階5全体の合格を意味しない。基準mainはPR #22の `1bcfdbb60b869a145bbc3c300ee837b485d80079`。branch `phase5/jobs-integration` で既存候補の確認済み10ファイルをSHA-256一致で再利用し、元のjobs-pipeline/queue-state/共有側の作業差分を保護した。

## 実装した操作と境界

- 明示 `--download-sandbox` の専用保存先だけを使い、架空作品を選択して全ページPNGとreceiptを保存する。既存の非管理フォルダー、実Cookie/旧ライブラリ併用、学習と同じ保存先を拒否する。`--check` は保存先を作成しない。
- アプリ所有の直列ジョブ、専用SQLiteの状態保存、操作IDによる再送抑制、状態revisionと進捗revisionの分離。保存成功後のみ新しい不変snapshotを公開し、UI読取はSQLiteのfsyncやワーカーロックを待たない。
- 未完了優先の安定表示、8件ページング、作者/作品ID/タイトル検索と状態フィルター。選択モード、絞込全件のページ越え選択、非表示の既選択保持、個別/一括pause・resume・stop、確認付き履歴delete。旧 `gui.py:2937-2939,3060-3066,3174-3176,3259-3329,3351-3360,3418-3426,3483-3511,3564-3580,3604-3638` を静的照合した。
- ジョブの失敗は新attemptとして再試行し、保存済みファイルは履歴削除で消さない。再起動は履歴を復旧するが、未完了の自動実行はしない。
- 検索・ページ・選択はclient別。受理済みの操作は画面寿命と分離し、アプリ終了はworker join後に保存器・画像providerを閉じる。旧DB指定時の失敗リストはPR #22の読取画面を維持する。

## 独立レビューと再現修正

DB/状態担当とUI/配布担当を分け、実装者以外の再レビューを行った。指摘を件数だけで閉じず、故障を再現してから修正した。

1. 未実行のpausedジョブが先頭にあると後続queuedまで止まった。EventによるREDを保存し、実行対象をqueuedだけから選ぶよう修正。実行中ジョブのpauseは直列runner内で維持し、勝手な並列化はしない。
2. `snapshot()` がDBcommitのConditionロックを待ち、画面更新を止めた。書込みをEventで保留するREDを作成し、最後にcommit済みのimmutable tupleを無待機で読むよう変更した。失敗/未commitデータを公開しない。
3. 一括削除の確認待ち中に選択が変わり、確認していない対象へ作用できた。確認開始時に対象IDとcontrol_revisionを固定し、選択/画面世代が変わったとき拒否する。処理開始後も最新状態に合う対象だけを操作し、結果を件数で示す。
4. 欠落したepoch/revisionが内部APIの省略引数として扱われた。ブラウザ入口を別にし、必須の数値文字列・現在世代を厳格照合する。mode/all/clear、checkbox、bulk、行操作を同じ原則に揃えた。
5. DOMを保ったタブ再訪で「もっと見る」が古い生成epochに固定されていた。現在のブラウザepochとrender object identityを分離し、再訪操作を受理しつつ破棄済みリストの旧イベントを拒否する。Downloadの選択revisionも固定して旧submitによる別作品投入を拒否した。`ui-final-red.xml` の4失敗を修正後、関連55pytestと独立レビューがAPPROVE。

## ローカル検証と証拠

証拠はignoredの `artifacts/jobs-integration-20261023/` に保存する。

- `full-final.xml`: 855pytest成功、既存依存警告1件。pip check/compileall成功。
- 最終 `edge-final-02/result.json` は1280×720/1920×1080で個別pause/resume/stop/retry/delete、12件のページ越え選択と一括制御、13件の履歴削除、11receiptの保持とhash、再起動履歴を確認した。イベント厳格化後のコードで、Download再訪の同一DOM/scrollと「もっと見る」の100件追加も成功。console/page/外部requestなし、両起動とも通常終了/port解放。
- `edge-regression/runner-result.json` の既存4経路と `edge-legacy-failures/result.json` の旧DB失敗リストも成功。sandbox用ジョブ画面によって旧失敗リストを壊していない。
- `native-interpreter-first/native.json` は初回の2作品・4頁PNG保存をGUIから実行し、receipt全SHAと独立復号した全画素を確認。GDI復帰安定2/2、通常9process終了、port解放、forced cleanupなし。ただし後続のUI修正版のコピーexe受入を代替しない。

最終製品51入力を凍結したonedir/noconsoleを新規フォルダーへコピーし、1050ファイルが一致。exe SHA-256は `f4644965253e04bf14266940db1a1359b73c9695802a589c699911ec81469f22`。限定配布scanは違反0で、下記の未走査範囲は残す。

同じコピーexeで `native-copy-first/native.json` と `native-copy-restart/native.json` を受入した。初回はGUIから2作品・4頁を保存し、履歴2件とPNG/receiptを終了後にも確認。再起動は2件doneを自動再実行せず復旧し、GUIで1件の履歴を削除後も4頁とreceiptを保持した。両回とも8 owned processの通常終了、port解放、forced cleanup/エラーなし。DPI168、GDI安定2/2、平均画素差 `0.000010670543521371293`（上限1）。製品51入力とコピー1050ファイルの最終hash不変も照合した。

`edge-final` の最初の再走は、一括pause後のフィルター変更と次のselect-allを、ブラウザが新世代を受け取る前に連続送信して拒否された。保存された状態は12件paused、選択0の安全な状態だった。ガードを緩和せず、検証器をaria-pressedと新表示件数が確定してから次の操作をする手順へ変更した。元の失敗結果を保持する。

## 配布検査の範囲

`check_release_tree.py` はファイル名、既知の秘密形式、制限付きZIP内部とSHA-256 inventoryを検査する。prefix付きZIPの見逃しと入れ子の未検査を修正した。ただしPyInstaller CArchive/PYZ等の全内部デコードは行わず、全レポートに `complete_secret_audit=false` と未走査形式を明示する。包括的な秘密不在証明ではない。`source_commit` はラベルであり、ビルド由来は別途凍結入力・コピーmanifest・実操作で照合する。

## 残る製品互換

実APIからのダウンロード、旧scheduler/failed_queueへの結果反映、作者一括・ZIP・小説/うごイラ等の保存形式、実アカウント書込み、全DPI/別PC/長時間は今回の合格範囲外。実保存は合成画像だけで、本番DB・画像・Cookieを使っていない。これらを未接続のまま有効な実操作として見せない。
