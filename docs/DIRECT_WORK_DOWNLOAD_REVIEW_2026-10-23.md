# ブックマーク外の単一作品ID保存 — 限定受入

基準main `640b72ccd892c6ef1a8a1252ad4e33d913e18265`（PR #24）。作業branch `phase5/direct-work-download`。最終ゴールは全体移植完了。本単位は、**既存の原寸保存・永続ジョブ・管理ライブラリ**へ、ブックマーク未登録の通常イラスト/漫画を明示IDで投入する機能を追加する。

## 操作と旧版契約

- 原版`server.py`の単作品`kind=work`と`pixiv_client.py`の`GET /ajax/illust/{id}`を静的照合。旧`unified_download_tab`のnumeric欄は作者IDなので転用しない。新版は「単一作品ID（数字）」を明示し、URL貼付/作者一括/小説/うごイラを対応済みと見せない。
- 実Cookie使用のlive managedモードのみ。作品IDの先頭0なし最大20桁を先に検証し、本人認証済みのGETで詳細の同一ID・作者ID・種別0/1・ページ数1..1000・タグ/タイトル・CDN allowlistを検証。任意サイトや書込API、旧DB、旧Imagesへは接続しない。
- 「IDから作品を確認」はmetadata GETのみで、保存/ブックマーク登録/キュー操作を起こさない。対応作品だけをカード化し、別の「キューへ追加」を選択したとき既存の`ManagedPixivDownloads`で全originalを取得・管理rootへ保存する。
- 候補はアプリの共有bookmark一覧を上書きしない。手動ID候補と送信許可は**server発行のview owner**に紐付ける。別clientの未照会IDは表示も送信も不可。非同期結果は同じepoch/検索/入力の画面へ戻った場合のみ候補登録し、タブ離脱/画面破棄後の遅延結果は無効。
- ブックマークにもある同じ作品はbookmark由来の表示・visibility/選択metadataを優先。手動照会が正常なbookmarkレコードへ一方的に上書きしない。Cookie/取得本文/画像をログ・Git・検証証跡へ残さない。
- UIでチェック後の確認済み選択はIDと固定したmetadataのまま永続request意図へ渡す。連打・復帰・古い通知の世代照合、別行連続checkbox、解除時のDOM再同期、古い同一行イベント拒否、遅い失敗noticeから新状態を保護する。
- クライアントが送信受付中に閉じても、既に起動した受付taskのownerを早く無効化しない。保存失敗は正常保存へ読み替えず、既存JobService retry・receipt検証・起動時の自動再実行禁止を維持。

## 発見・修正した競合

1. **認証同時性**：共有`LivePixivReadClient.verify_identity()`が更新開始時に認証状態を一時Noneへ戻し、別スレッドの詳細GETを誤って`AuthenticationMissing`で拒否。Eventの決定的試験をREDで再現し、認証遷移/詳細のロックで修正。再認証なしで既存の検証済みidentityを再利用する経路も追加した。
2. **close待ち**：HTTPが応答を待つ間に同じ認証ロックを持った`close()`が止まる危険。closeは先にclosed状態を公開し、応答後に再確認して古いpayloadを拒否。held transport中でもcloseが待たない回帰を追加。
3. **client間の誤許可**：初稿はapp-global direct候補を全clientへ公開し、未照会の別clientが保存できる問題。owner別候補と`enqueue`時のserver-side同一owner照合へ変更。未照会別clientのGET/ジョブ0、別clientが独自照会した場合のみ操作可とする。
4. **遅いクリックと表示食い違い**：二行を高速にチェックするとglobal selection revisionで二件目を落とす問題、選択欄の解除後も元checkboxがcheckedのまま残る問題、旧checkbox高Serialが解除後の選択を復活させる問題を検出。render session・epoch・行ごとのserial/generationとサーバー状態へのDOM同期へ修正。
5. **受付中のclient終了**：表示を閉じるとownerを先に解放し、受理済みexecutor処理が後でowner不足になり拒否。await済みと実行中タスクの寿命を分離し、受付処理が完了するまでowner解放を遅らせる回帰を追加。

## 受入と残る境界

- 合成読取・サービス・UIを含む全pytest：**975 passed / 1 skipped**（Windows symlink作成権限）、既存Starlette warning1。`artifacts/direct-work-20261023/full-final.xml`。`pip check`・`compileall`・`git diff --check`成功。
- 専用実Edge `tools/check_direct_work_browser.py` は1920×1080、1280×720の初回/再起動、計4経路で成功。無効IDはGET0、手動ID作品の全原寸2頁/PNG色・receipt SHA、作者詳細/共有viewer/戻り・focus、再起動のremote GET0、未照会別clientへの候補非表示、通常4プロセス終了/port解放、browser外部通信・page/console errors0。`artifacts/direct-work-20261023/edge-final/result.json`。
- PR #24既存の`check_managed_downloads_browser.py`も独立再実走して成功。元のブックマーク経路をデモで置き換えない。
- **新ID作品の実Cookie操作と、新機能を含むコピーWindows onedirの受入は別ゲート**。既存PR #24の実Cookie3頁保存・コピーexe受入を今回の新UI合格へ読み替えない。
- 最新製品src105件をSHA256固定してonedir/noconsoleビルド、入力変更0。別専用ディレクトリに1050ファイルをコピーし全件SHA一致、exe SHA256 `cdd07d72f6bbab7be9559b1224ec0581f6bf436724eedb2c2c1486d7f19afa6c`。`native-copy-smoke.json`はWindows実WebViewのランキング・学習・ライブラリ操作、GDI最小化復帰(安定2標本、平均画素差0.01500163505559189)、8 owned processes通常終了、port解放、強制cleanup0で**合格**。これは合成既定経路の配布スモークであり、ID直接入力の配布native UIはまだ未受入。
- 独立reviewで残る「内部受付wrapper Taskそのものを強制cancelした場合、基礎to_threadだけ動きownerが早く解放され得る」という限定P2を記録。通常client削除・submit coroutine取消では実サービスの完了とowner後解放を確認したが、event loop全体shutdownの最中に受付が完全成立するとまでは主張しない。アプリ終了時の実ジョブは既存サービス終了のdrainと停止/再起動回復を別契約として維持する。
- 配布のsecret scannerは既知パターン/拡張子/限定ZIP/ファイルSHAのみで、PyInstaller CArchive/PYZの完全監査ではない。元Cookieや旧DBを配布へ含めない。
- 作品IDを使う手動保存は旧作品/作者DBへの書込み互換、旧ZIP出力、作者一括、旧failed_queue再実行、小説本文、うごイラ、強制再取得、旧スケジューラを完成させない。フェーズ5全体は未完。
