# 原寸画像の管理保存・キュー・保存済み閲覧

基準main `a7b021a3001660ba38776ae3509426e0c322985a`（PR #23）、専用branch `phase5/managed-pixiv-downloads`。最終ゴールは全体移植完了であり、本書は通常イラスト/漫画の明示保存単位を扱う。旧DB・元Images・Cookie原本は変更しない。

## 接続した操作と責任境界

- `--pixiv-cookie-file`と別領域の`--pixiv-download-root`を明示。constructorと`--check`は通信せず、保存先の指定だけでアカウント取得やジョブを実行しない。合成download-sandboxや旧DB、学習保存と混在させない。
- 4経路すべてを取得できたブックマークから、通常イラスト/漫画だけを検索・選択して投入。50件追加、検索中の選択保持、タブ再訪、遅いイベントの世代検査を維持する。
- 既存GET専用clientへ`get_artwork_original_pages()`を追加。旧`get_artwork_pages()`のregular表示契約は変更しない。元版`pixiv_client.py`のoriginal取得を照合し、対象ID・0始まりページ順・CDN・URL形式を検査する。
- 元バイトをPNG/JPEG/WebPの静止画としてdecode/verifyし、各頁の上限と総量を検査。全ページfsync・SHA検証・receiptを作った専用partialディレクトリだけを完成領域へrenameする。失敗/停止のpartialは別領域に残し、正常ライブラリへ入れない。
- 明示の「保存先更新」はI/OをUI外で行う。新たな不変LibrarySnapshotと画像catalogを公開し、別clientが保持している旧catalogは参照がある間存続する。全頁検証は公開/再利用時、各表示要求はmanifestと対象頁のhashを再検査し、全頁を毎回decodeする二乗コストを避ける。
- キューの停止・再開・再試行・履歴削除はPR #23のJobServiceを再利用。保存先の失敗と旧DBのfailed_queueは混ぜない。

## 受理した選択情報と再起動

選択したRemoteBookmarkはjob IDに固定する。受理前にメモリpinを変更する初稿は、enqueue失敗や古いerror再試行で別のmetadataを使う危険があったため修正した。

1. URL/Cookieを含めないstrictな選択仕様を`requests/<operation-idのSHA>.json`へfsync・renameする。
2. JobServiceがSQLiteのjobとoperation ledgerをcommitする。
3. workerは受付lockの解放を待ち、commit済job IDに対応する選択だけを使用する。

再起動では、現存するjobのDB操作台帳が参照する要求だけを復元する。未受理のorphan intentを勝手に実行しない。壊れた/欠落した必要intent、target/title不一致、異なる仕様のoperation ID再利用は拒否する。intent書込み中の失敗は`request-incomplete`へ隔離され、他の正常な保存を永久に塞がない。

`error`の明示retryは再起動後も同じjob/受理仕様でattemptを増やす。`stopped`を自動再開しない。done後に別operationで再投入した場合も、成功receiptと全頁の実体を照合し、新しい画像GETを行わず再利用できる。履歴削除は出力ファイルを削除しない。

## 独立レビューと故障注入

- 保存writer、original reader、閲覧catalog、選択UIを別担当へ分離。接続はprimeが行い、別担当がpin/世代・所有寿命・原本保護・native privacyを再レビューした。
- 任意の参考意見としてClaude CodeのSonnet/lowを使用。ツールとネットアクセスなし、限定した公開製品コードだけを渡し、ファイル公開とSQLite最終commitの非原子性を検討した。モデルの意見を試験結果の代用にはしない。
- 決定的なEvent故障注入で、完成ディレクトリ公開直後のstop/delete、最終SQLite commit失敗を再現。UI/DBに偽doneを出さず、正常な保存物を削除しないこと、再起動後の明示再投入でGETせず再利用することを確認した。
- 2つの資源は原子的に同時確定しない。停止・DB障害時に「検証済み保存物があるがジョブはstopped/error/faulted」は起こり得る。成功表示はDBの確定後だけで、回復時に実体を再確認する。Windowsのdirectory fsyncは電源断耐性の証明ではない。
- Windowsの実symlink作成試験1件は権限不足でskip。別のreparse/hardlink拒否の非skip故障試験を実行しているが、skipを実機合格とは扱わない。

## 現時点の受入証拠

- `full-first.xml`: 940 passed / 1 skipped。native検証器の回帰試験を追加した`full-final.xml`は **945 passed / 1 skipped / 既存Starlette警告1件**。pip check・compileall・diff check成功。
- `durable-retry.xml`: 38 passed。JobService/intent/services/確定境界の試験。これはnative実走の証拠ではない。
- `edge-fourth/result.json`: 実Edgeの1920×1080、1280×720で各初回/再起動の計4経路成功。原寸120×84の2頁がregular48×32と異なることをバイト/SHA/画素で照合。検索/選択/queue done、保存先更新、作者/2頁viewer、戻りfocus/タブ、2client独立を確認。再起動直後の注入transport要求0、同じ作品の再投入でoriginal GET0。4回通常終了・port解放、console/page/外部browser通信errorなし。
- 先行Edge失敗3試行は保存。検証器がdebounce後の別検索行を待たずに旧checkboxへ入力した問題と、再訪で保持された作者詳細を作者一覧と取り違えた問題を修正した。製品の世代拒否や状態保持を弱めていない。
- `edge-regression/runner-result.json`: 既存gallery/learning/image-race/library4script全0、source不変・port解放。この既存runnerはchildをterminateするためreturncode1を記録しており、この経路で通常終了したとは主張しない。
- 製品入力56件を固定してonedir/noconsole build。入力差分0。配布1050ファイルを別ディレクトリへcopy、全SHA一致。exe SHA `1e20d9d25a682692616c9ad128403161968dd86f8fb35aba16a4f30dcf560099`。

## 実アカウント・配布exeの境界

実Cookie受入は、独立レビューを通した専用native検証器で通常作品1件（最大3頁）を新しい管理領域へ保存する限定経路。元DB/元画像/Cookieを変更せず、実データの画面・本文・識別値・画像hashを証跡へ保存しない。検証中のstdout/stderrを保存せず、例外もclass/固定phaseだけに制限する。

GDI画素受入は、実作品の画面から合成ランキングへ復帰し、可視画像がすべてdemoであることを証明してからだけ行う。途中例外・timeout・復帰不明時にはcaptureを飛ばし、失敗として通常WM_CLOSE/終了監視へ進む。独立full-main故障注入試験でこの分岐を検査する。

同一コピーexeで実Cookieの初回保存と同じ管理先の再起動を受入した。`native-managed-accepted/native.json`は通常作品1件・原寸3頁の保存、キュー完了、共有viewerのページ送り/戻りfocus、合成画面復帰後のGDI復帰、通常終了・port解放、Cookie原本不変がすべて成功。`native-managed-restart-accepted/native.json`は保存物・job/operation/intentの内部signature不変、保存済み閲覧・戻り・GDI・通常終了・Cookie不変が成功。再起動試行ではページ送りを再実施していないので`multiple_pages_validated=false`であり、初回の成功とは分けている。

先行の1頁保存試行はファイル/DB照合と通常終了が成功したが、合成画面復帰の検証で失敗したため総合FAILを保持。検証器が画面外の未読込サムネイルまで一斉decodeを要求していた点を修正した。最終ゲートは、可視50カードのIDがすべてdemo、privateパネルが非表示、実際のviewport内画像がすべてloopback demoのdecode済み画像であることを確認してからだけcaptureへ進む。修正前は撮影を飛ばしており、実作品の画面は証跡に残していない。

`final-integrity.json`で製品56入力・配布1050ファイルがfreeze/copy manifestと再一致することを照合する。配布scanは既知パターン/名前/限定ZIP/ファイルSHAの検査であり、PyInstaller CArchive/PYZを含む完全な秘密監査ではない。最初のscanはsource_commitラベルの書式不正で検査前に拒否、ラベル省略の`release-scan-second.json`で1050ファイル・違反0を確認した。commitとの一致は別途product manifestとGit blobを照合する。

正確なcommit/head CI/main CIは別ゲートとしてPRへ記録する。この限定実Cookie・配布exe成功を段階5全体の完了へ置き換えない。

## 残る全体移植ゲート

旧DBへの更新同期、旧保存命名/ZIP出力、作者単位取得、定期scheduler、リモート更新検出/強制再取得、小説本文・うごイラ保存、旧失敗の再投入/削除、重複整理、設定/外部連携、全DPI/別PC・長時間運用は未完。PR #7のwarm画像比較は限定結果として維持し、この単位では速度改善率を追加しない。
