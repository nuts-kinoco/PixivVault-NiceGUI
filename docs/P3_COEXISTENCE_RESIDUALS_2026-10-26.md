# P3 共存候補と原操作の残差 — 正式受入未完

## 基線・所有・保全

専用候補 `S:/Projects/PixivVault-NiceGUI-p3-coexistence-20261026`、branch `phase11/p3-coexistence-20261026`、HEAD `2cf4a7a7286dd728e8df8125094498a3622babba`。
固定P1のHEAD＋dirty28を開始点とし、共通base `31aad331846fc3d4c3aceac4f7a90fb61f05b111` からPR35/P2の差分を三方向照合。Git merge/commit/pushは行わず、新しい独立checkoutのファイルだけを編集した。
P1/P2の試験数は合算・継承しない。`artifacts/p3-20261026/provenance.json` に入力manifest hash・コピー元・初期競合を記録。共有bootstrap/services/job表示/shellはroot担当一人が整合。補助担当はAGENTSの並行規約に沿い、library/filter、ranking、learning-review、saved-similarity/duplicate、recommendationの別所有ファイルを編集した。全て自己点検で独立監査ではない。
`frozen-preservation.json` はP1 28/28、P2 16/16、M3 7/7、M4 18/18の不変を確認。元利用者DB/Cookie/画像/監査ZIP/Base64は変更・抽出していない。

## 今回接続した操作

| 原操作 | P3コードと接続 | 限界を含む結果 |
|---|---|---|
| Phase11 小説・うごイラ保存の共存 | `media_jobs.py`, `ui/media_views.py`, services/bootstrap/shell/job_views | 2つの明示した非重複root、媒体別ledger/receipt/operation_idを保持し、同じdownload/library/queueから選択・制御。TXT/EPUB/BOTHとGIF/MP4/framesを保持。v1/v2の既存保存契約を変更しない |
| 原ランキング mode/date/content/R18/AI/4ソート、非premiumタグ検索 (`ranking_tab.py:1016,1093,1217–1275`) | `ranking_source.py`, `ui/ranking_view.py`, `pixiv_read_client.py` | 明示取得のみ。既存カード/画像viewerを再利用し、非current画像pinでライブラリを置換しない。厳密なGET許可先/認証/上限/no redirect/no retry。個人/premiumは別未接続 |
| 原ライブラリ日付/ブクマ数/作者/R18/AI/既読/未取得/容量順 (`library_tab.py:497–560,2370–2886`) | library_filters/state/repository/models/filter_panel/library_view、bookmark投影 | 実在するoptional metadataと履歴行から判定。不明を推測値で埋めない。following_usersの作品なし作者も原順に名前検索。元DBを書かない |
| 保存済み類似検索 (`similar_works.py:25–154`, `library_tab.py:1066–1149`) | `similar_saved.py`, `ui/similar_view.py` とlibrary入口 | 保存済みfloat32、model egara_net_fp32、top30/閾値0.50、多数派dim、同作品全頁除外、composition起点なし時だけstyle。新規推論/GPUなし。stdlib内積のBLAS丸め完全一致・大規模性能は未保証 |
| 学習履歴review (`discovery_tab.py:4856–5551`) | learning_review/service/repository/ui | 好意/否定、文字/期間/SuperLike/hate/未保存、10件頁。FIFO transactionで取消・同滞在中復元、CAS/ABA/operation冪等、4判定候補へ復帰。旧v2合成marker指紋を維持。実作品候補取得と実保存は別未接続 |
| 保存済みおすすめ、hide/Undo/節順/折畳み (`recommend_tab.py:513–611,962,1515,2273,2967`) | `recommendation_state.py`, `ui/recommend_view.py` | 元静止DBのSuperLike/certain/boosted cacheを読取。変更は明示 `--recommendation-preferences-root` の別sidecarのみ。保存画像を既存libraryで開く。新規trend計算/外部更新/boost登録を実装済みとしない |
| viewer個別DL | `viewer_save.py`, `ui/viewer_controller.py` | 画像確認後の明示操作→既存managed lookup/admission。古い表示世代・遅着・二重操作を拒否。ブクマ/フォローwriteを有効にしない |
| 原重複scanと隔離計画 (`gui.py:5025–5117`) | `duplicate_inspection.py`, `ui/duplicate_view.py` | 検証済みmanaged原画像だけを読取り、原距離4/作者2000/表示50組5枚/各組1枚保持。実隔離・ごみ箱削除は未実装、理由付き無効 |

上表は原操作と実装の対応であり、正式な全操作分母や得点ではない。原版は固定 `3ac3aa0d47a64785251593bcb09bb2d5f6df374b` を読取り参照した。

## 既存採用機能を作り直さなかった範囲

- BASELINE V01–V13の一覧/検索/追加/カード寸法/頁送り/戻りfocus/scroll、画像世代・遅着拒否、4判定/Undo基盤は再利用。
- 単一静止画DL、作者差分、旧failed_queue再投入、旧作者ZIP export、旧保存library/作者/4種bookmarkの読取は既存コードを維持。
- Phase9 schedulerの履歴/通知/再起動とPhase10 metadataのside-ledger/世代CAS/明示1作品GETを再利用。旧PROGRESSの未完文面を根拠に再実装していない。
- 原版の自動metadata backfillは最新採用Phase10の「明示1作品、設定ON/起動でGETなし」と差がある。自動通信を黙って追加していない。

## 具体的な実装阻害：重複隔離の保存契約

原 `gui.py:5093–5117` はfolder moveまたはZIP entry除去の成功後だけ旧DBへ除外を記録する。`5604–5697` のごみ箱空は物理ファイルを消しても再DL抑止行を残す。
現managed保存は全ページのhash/件数をreceiptへ固定し、世代を不変とする。選択ページを直接移すとreceipt、CAS、再起動照合、schedulerの保存済み判定が破損する。

対象操作: managed保存済み画像の実隔離とごみ箱空。状態: **未実装・未実行**。安全拒否の迂回ではなく、採用済み保存契約との設計衝突である。検査と計画の表示までは完成し、移動/削除を成功表示しない。

必要な採用判断は以下のまとまり：

1. 不変の旧世代を残し、新世代receipt＋除外page/tombstoneを採用するか、別の互換表現にするか。
2. active pointerと除外台帳のクラッシュ原子性、隔離失敗時の戻し方。
3. 作者差分/schedulerが除外を尊重する保存済み判定と再DL抑止。
4. 旧作者ZIP export/importと物理ごみ箱の互換、除外行を残す範囲。

原版ZIPを直接書換える案は今回実行していない。設計はdotへ返す。独立監査待ちやlive未承認をコード上の阻害と取り違えない。

## 既知の実装残件（受入待ちとは別）

- 作者差分/schedulerの原 `target_type=both`（core.py:1204/1208, scheduler.py:182–242）はillust＋novel。既存Phase9 dispatcherは型0/1限定。共存UIから個別保存はできても、媒体別dispatch adapterと同一作者バッチ復旧は未接続。
- 原discoveryのfollowing/ranking/related-author/tag候補取得と既存好みへの接続、reviewからのmanaged保存、傾向スコア表示は未接続。現LearningServiceの合成専用境界を実作品対応と偽っていない。
- 新規おすすめtrend/trend追加検索、boost登録変更、候補の外部更新・追加取得、個人/premiumランキングは未接続。保存cache閲覧とは別。
- 原設定群の統合画面（保存既定形式、速度/retry/429/cache/tray等）、Shaft/vaultの暗号・セッション・更新、Windows tray/URI/外部viewerの一部配線が残る。iPhone除外をこれらの除外に読み替えない。
- 元DB書戻し、全量migration、旧ZIP直接追記は既存readonly/sidecar採用境界の外。ブクマ/フォローの実writeも今回境界外。

## 検証・残る受入ゲート

最終全pytest/Edgeの実数、失敗と修正、対象runtime SHAは `artifacts/p3-20261026/final/manifest.json` を正とする。各担当focused件数は合算しない。
全受入ではない：独立コード監査、実API/実Cookie、Windows配布native/codec同梱、exact-head Windows/Ubuntu/Chromium CI、同条件Flet比較と総合UX、正式全操作台帳の回収。これらを自己試験で代用しない。
P1/P2は固定保全し正式受入保留を維持。P3未完なのでP4へ進めない。判断をdotへ返す。

再現は既存S venvで -B、PYTHONDONTWRITEBYTECODE=1、PYTHONPATH=<P3>/src、TEMP/TMPと各--basetemp/--outputを新規S隔離runへ指定。MP4はPV_TEST_CODEC_PYTHONに既存CPU OpenCV runtimeを指定。導入なし。`pytest -q -ra`、`tools/check_media_coexistence_browser.py`、`tools/check_learning_review_browser.py`、`tools/check_library_browser.py` をheadless msedge/所有loopbackで実行する。実Cookieを入れない。


## 最終候補の自己検証結果

- 全 pytest: **2313 PASS / 2 SKIP / 0 FAIL、6 warnings、123.96秒、exit 0** (`artifacts/p3-20261026/full-r3.log`)。SKIPはWindows symlink権限2件。権限変更なし。先行P1/P2の件数は合算していない。
- 共存 Edge: **4/4成功** (`coexist-edge-r5/result.json`)。1280×720 / 1920×1080の初回・再起動。公共ランキングfixture取得→既存viewer、小説BOTHのqueue→保存→reader、うごイラframes/GIF/MP4のqueue→保存→再生、pause/resume、stop、失敗→retry、再起動時の永続台帳・receipt照合を確認。全ケースでconsole/page/外部要求エラーなし、所有アプリ正常終了、port解放。
- 学習履歴 Edge: **4/4成功** (`review-edge-r2/result.json`)。2画面サイズの初回・再起動、取消/復元と永続化。全ケース正常終了・port解放。
- ライブラリ Edge: **成功** (`library-edge-final/runner-result.json`, `library/result.json`)。2画面サイズ・独立2クライアント、原フィルターと既存操作。所有PID698104をrunnerが終了処理しport解放。returncode=1は通常native終了の合格証拠にしない。
- 上記最終browser全てのsource_before/source_afterが同一で、最終runtime集合と照合済み。docs更新はruntime集合外。
- 1280幅の小説reader・MP4再生スクリーンショットを目視確認。推薦/重複/類似はfixture回帰範囲で、Edge実画面の独立受入は未実施。

途中失敗と修正も保持: 共存r1はcheckerの子root親作成不足、r2はranking外枠の高さ不足、r3は選択済みタブの不要なrefresh、r4は再入場時の古い小説行をクリックできる競合。製品修正はコンテナのflex配置、メディア選択の冪等化、再照合中の旧行非表示、うごイラ遅着後の再照合である。既存epoch/generation拒否は緩めていない。追加回帰と関連分は28 PASS (`ui-race.log`)、最終全pytestと共存r5で再確認した。失敗ログを削除していない。

全操作を閉鎖したとは扱わない。原操作別のコード未接続と、実隔離の保存契約衝突は上記の通り。独立監査/live/native/CI/総合UXの未実施とは別項目である。
