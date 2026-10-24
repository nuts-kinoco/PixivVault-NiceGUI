# Phase10 — 管理root専用メタデータ後埋めの契約と進行

Scope note: `docs/MIGRATION_SCOPE_AMENDMENT_2026-10-24.md` excludes iPhone/iOS app integration. The original `docs/BASELINE.md` and legacy baseline evidence remain unchanged historical records.

> 2026-10-24 後任による最新状態：Phase9 PR #30 head 61a0e5c2b9d95f63c25e57ff6ea20e4175a4e6c4 は独立read-only全差分レビューAPPROVE/P1=P2=0、ローカル全pytest/Chromium/Windows nativeを経てmainへマージ済み。merge SHA 9d62fbad826e2cdc469c06806f44996cc61fb91c に対するGitHub Actions run 40055760877 の Windows/Ubuntu/Chromium **全SUCCESS**確認済み。Phase10専用worktreeは既存M1 commit 99ba92ef、M2 commit f06cba9 を保持したまま、通常mergeにより統合済みmainへ合流。現在のPhase10 HEADは 54e98e20ff88a40c0dc20075aa16c3fb94fe1001。以下の「Phase9 CI実行中」は作業当時の履歴。Phase10 M2はオフライン基盤のみで、Phase10全機能・製品全体の完成ではない。

## 現在位置

- Phase9 PR #30 の新head c4f3427ced0f1c881d0631b057c2898fdc6eb8d8 がCI実行中に、独立した専用worktree phase10/managed-meta-backfill を同headから作成した。Phase9のdirtyコードや原版のファイルを編集しない。
- このフェーズの初期マイルストーン M1 は metadata_policy.py と test_metadata_policy.py の**通信もDB接続もしない**判定契約。現時点ではアプリのUIやschedulerへ接続しない。自動起動OFF、実Cookie不使用。
- main統合済み最新HEADへ基準を揃えるまではPhase10をpush/PR/mergeしない。Phase9のCIが失敗した場合でも、Phase10の独立した純関数試験は保護する。

## 原版で確定した条件（読み取りのみ）

1. 原版 scheduler.py 104–123：メタ後埋めは設定が明示ONの場合のみ。ダウンロードが走っている、対象が空、認証未確認の場合は通信を始めない。ダウンロード中は処理を譲る。
2. 原版 core.py 560–587：HTTP 404のように作品固有の失敗だけ試行回数を増やす。401/403/429/5xxや通信断/timeoutは全体的障害として即中止し、作品試行回数を消費しない。原因不明の失敗も試行回数を消費せず、5件連続した場合にその周を停止。
3. 原版 core.py 608–716：処理対象は開始時に1度だけ固定する。先頭の失敗作品を毎件繰り返し選択しない。処理中止と認証失敗はUIへ明示する。詳細応答にbookmarkCountが無い場合は作品固有失敗として扱う。
4. 原版 database.py 1644–1695：作品種別illust、未削除、bookmark_countがNULL、meta_attemptsが3未満のみ対象。meta_fetched_atは判定条件に使わない。対象順はmeta_attempts、user_id、work_idの昇順。打切り作品の再挑戦は利用者の明示操作のみ。
5. 原版 core.py 496–555とdatabase.py 1606–1642：存在した値が0やFalseでも捨てない。詳細メタ更新は本体のtitle/create_date/update_date/page_count/tags/deletedを変更しない。欠損値で既存メタを潰さない。
6. Claude SonnetのM1独立テスト監査で指摘された原典確認事項をCoS Coreで照合：database.py 267のwork_idはTEXT、278のuser_idもTEXT。ORDER BYは数値順ではなく文字列順（例：10は9より先）。core.py 700–705では正常な作品詳細応答にbookmarkCountが欠損する場合だけ作品固有のmeta_attemptsを加算。別の不完全/認証/通信エラーを作品固有と偽定義しない。両分岐はM1の追加テストで固定。

## NiceGUI側の実装境界

- 元FletアプリのDB、画像、設定、ZIP、Cookieは読取専用。後埋め結果の永続化は新アプリの明示した専用managed rootに置き、旧DB/既存世代receiptを更新しない。
- 既存のmanaged画像保存は内容SHAとactive CAS fingerprintを持つ。後埋め結果は別side-ledgerで基礎となる保存世代digestに紐づける。別世代に書き換わったら古い補完結果を新世代の事実として表示しない。
- PixivReadClientは現在HTTPErrorのステータスをResponseRejectedへまとめている（pixiv_read_client.py 260–297）。将来の実通信アダプタでステータスを安全な区別付きエラーとして保持するまでは、ResponseRejectedを404と推測しない。認証/URL/本文を公開ログへ出さない。
- real PixivへのGETは既承認範囲の確認とユーザー所有の安全なCookieコピーが必要。純関数・合成トランスポート・専用DB試験は実接続なしで進める。外部サービスShaft/vault、認証変更、課金、権限変更、削除は本フェーズから実行許可を広げない。
- UIは既定OFF、ONと明示手動操作を別にする。タブを開くだけではバックグラウンド通信を起こさない。CAS revisionと複数clientのdirty form保護、再起動後の曖昧な未完処理、キャンセルと終了時のresource barrierをPhase9と同様に設計する。

## 段階とゲート

- M1: 対象選定と失敗分類を副作用なしの関数へ切り分け、synthetic pytestで原版契約と例外系を固める。これ単独のGREENは後埋め製品完成ではない。
- M2: isolate managed root内の永続補完台帳、CAS、restart/SQLite例外、試行上限、明示再挑戦と安全なread-onlyカタログoverlay。サイドデータの世代一致を強制。
- M3: synthetic-only GETアダプタ、取得結果の形式/容量制限、失敗分類と停止条件、競合するdownload jobを優先するバリア。元DBと既存receiptのSHA不変をassert。
- M4: 設定UIと利用者進捗（対象/完了/保留/打切り）、stop/resume、複数client、5連続失敗の可視化、明示ON/OFF。
- M5: 独立read-onlyレビューでP1/P2=0、全pytest、実Edge 1280×720/1920×1080 初回/再起動、Windows onedir別rootコピー全SHA、native GUI自然終了、正確headのPR/CI、既承認範囲の条件付きmain統合。

## 未着手と保全

原版metadata backfill以外にも、Shaft/vault、うごイラ、小説、重複/隔離整理、全画面UI、実データ保存と最終Windows製品化が未完。Phase10局所M1をもって完全移植の完成とは扱わない。過去Phase9のwebview-ui共通名上書き事故の原bytesは未復元であり、今後も同じ証跡名を再利用しない。

## M1 ローカル合成受入

- 作品対象の純選択とHTTP失敗分類（外部通信・SQLite・Cookie読取なし）に対するテストは **41 passed**。original TEXT IDの文字列ソート、404/401/403/429/5xx、接続不能/認証/timeout、bookmark_count=0対NULL、未知失敗の5連続打切り、無効型、frozen入力の例外を含む。
- Phase10専用worktreeの全pytest **1258 passed / 1 skipped / 1 warning**。試験XMLは artifacts/phase10-pure-policy-full-20261024.xml。skipはWindows symlink作成権限、warningはFastAPI/Starlette TestClient互換性。
- Claude Sonnet read-onlyテスト監査は **M1純ロジック限定 APPROVE（P1なし）**。指摘の「原版のID並び順」と「正常応答のbookmarkCount欠損」について原典を別途確認し、実装変更なしでテストを補強。M2以降の台帳/UI/実GETを承認したものではない。
- 本段階は独立したローカル開発途中であり、Phase10のPR/CI、Edge/Windows製品受入、main統合は未実施。Phase9 PR #30 の同一SHA CIを阻害せずに並行した成果として保管する。

## M2: isolated side ledger（実GET/画面への接続なし）

- metadata_ledger.py は既にMARKERとIDENTITYを持つ管理専用rootだけにSQLite side-ledgerを作る。クラス明示構築時以外は初期化しない。元PixivVaultのDB/画像/ZIP/Cookieおよび既存世代receiptへ書き込まない。
- 専用lock・SQLite FULL同期・1プロセスwriter・OFF既定設定とrevision CAS・作品ごとの状態とlease epoch・再起動時の明示中断確認を用意。古いin-flightを自動でsaved/再投入扱いしない。
- 注入されたactive managed世代SHA検証関数でoffer/completeを検査。旧世代再登録は永続supersededテーブルで禁止。active SHAと一致しないoverlayは見せない。格納するメタは小さい数値スカラーだけで、URL/HTML/Cookieを受け付けない。
- 作品固有障害だけattemptsを増やし3回で打切り。全体障害/unknownでは増やさずblockedにし、同じ世代を無人高速リトライさせない。明示resume_blockedには設定revision CASを必須にする。OFFへ変更すると全live leaseを失効させる。
- 最初のClaude Sonnet read-only独立QAは、古いlease epochなし・旧世代offer復活・不正なfailure flagで試行回数改変の3P2を発見しBLOCK。その後すべて是正し、独立再監査は **M2限定APPROVE / P1=0 / P2=0**。残P3として注入callbackのロック再入・外部CASとの連携・SQLiteスキーマ不正への対処はM3接続前に再設計する。
- M1/M2 focused 55 passed。初期OFF/marker拒否/CAS/ファイルlock/active世代overlayと0/失敗3回/429とunknown/再起動旧lease/偽装FailureDecision/古い世代差戻し/改ざん/2スレッドclaim/OFFフェンスなどを合成試験した。
- M2はHTTP・Cookie・原DB読取・自動スケジューラ・UIのないオフライン基盤。Phase10のPR/CI、Edge/Windows製品受入、main統合は依然未実施。元の全移植ゴールは未達であり、Phase9 PR #30統合後main CIを先行確認する。

## M3: 明示起動・synthetic GETアダプタ（2026-10-24 継続）

- Phase9本体の統合後main CI run **40055760877** のWindows/Ubuntu/Chromium成功を再照合した。M3は独立worktree `phase10/managed-meta-backfill` / base HEAD `54e98e20ff88a40c0dc20075aa16c3fb94fe1001` 上の未コミット差分として引き継ぎ、別の同一作業を重複起動しなかった。
- `metadata_transport.py` は合成結果の作品/作者ID一致、マスク、bookmarkCount 0・NULL、昇格列、bounded `work_meta`、失敗クラスを検証。`pixiv_read_client.py` はHTTPStatusRejectedをURL/本文を含まない型で保持し、重複JSON key/NaNを拒否。明示的既存Cookieクライアントの読み取りアダプタのみを追加し、アプリの起動経路・自動スケジューラ・実Pixiv通信へ接続していない。
- `metadata_runner.py` は起動OFF既定、候補のimmutable snapshot、本人認証照合、1周各作品最大1 GET、save/download busy待ち、明示キャンセル、全体障害即停止、unknown 5件連続停止、lease/世代CASと再起動時の曖昧性保留を実装。初期の無停止前提テストでM3関連440件成功。base mainを取り込んだ全体試験は **1571 passed / 1 skipped / 1 warning**（`artifacts/phase10-m3-full-before-review-20261024.xml`）。
- Claude Sonnet独立read-only M3テストレビューは最初 **BLOCK P2=3**（事前cancelでも認証通信／世代切替中429の全体停止逸失／`wait_for_idle=False`で途中busy無限待ち）と判定。M2の旧`user_version=0`台帳互換と既存`meta_attempts`継承も追加確認。計5件のREDを合成pytestで再現し、それぞれ修正、局所 **102 passed**（`artifacts/phase10-m3-p2-remediation-focused-20261024.xml`）。修正後にClaude独立read-only再レビュー **APPROVE / P1=0 / P2=0**、残P3を明記した。
- 既存rootの元DB、画像、ZIP、Cookie、世代receiptを変更していない。実GET、ブラウザUIでのON/OFF、実downloadsとの共有実行バリア、Edge/native/コピー受入、Phase10のPR/正確なhead CI/main統合は**未達**。`is_download_busy()`の合成優先確認はあるが、claim直後〜GET間の実downloadsとの原子的排他は未結線。この境界をM4/M5の受入までに解消し、未完成を完成扱いしない。
- その他のP3：既に`superseded`へ登録されたA→B→A差戻しの1作品でバッチが`managed_state_changed`停止する（旧結果表示/書込みはしない）。sqlite異常のrun summary統一・ライブepoch中の非通常終了、及び実ジョブ競合についても統合時に再点検する。
- 修正後全pytestは **1575 passed / 1 skipped / 1 warning、失敗0**。証跡 `artifacts/phase10-m3-after-p2-remediation-full-20261024.xml`。skippedはWindows symlink権限、warningは既存Starlette/httpx TestClient deprecation。これはM3のローカルsource snapshotに対する結果でありPR head CIではない。

## M4-A: 設定・台帳進捗画面（通信接続なし、2026-10-24）

- M3を `7dc11bccbd9fc9991dd089a9fdaf8c33280a8d90` にローカルコミット。その次の作業を、ユーザー承認済みの同一専用worktreeで開始した。M4-AはM4全機能の一部であり、独立マイルストーン受入と製品完成は別。
- `ApplicationServices.enable_managed_metadata()` は**明示呼出のみ**で、既存の管理専用rootを要求する。画面を表示するだけでは新DBを開かず、設定のON・保存・再起動時にも実Pixiv GETや新規自動ワーカーを始めない。開始後の設定/進捗照会はローカルside-ledgerのみ。shared serviceに配置し、close/enableは共通順序のロックで保護する。
- 原本となる世代digestは `verified_saved_receipt()` の **receipt本体SHA256 `[2]`** であり、ジョブ受付fingerprint `[4]` とは別。この違いをテストで固定。元DB/画像/receipt/Cookieは変更せず、既に認識済み管理root内の独立SQLiteのみ変更する。
- NiceGUIの「メタデータ補完」タブはmanaged modeのみ有効。明示「台帳を開く」操作、OFF/ONをrevision CAS保存、複数clientのdirty form固定、状態更新/破棄、close時タイマーキャンセルを提供。台帳登録数・待機・処理中の記録・完了・保留・中断不明・打切りを **side-ledger状態**から集計する（現在のactive世代との照合済み数ではない）。実行ボタンや無人通信は公開しない。
- syntheticとmanagedのNiceGUI Clientナビ試験で、未承認モードではtab disabledかつshow no-op、managed modeでもタブ表示はsidecar未作成・GETなしを確認。M4 focusでは43 passed（初期）、ナビ＋UIの追加後12 passed、M4台帳進捗関連34 passed。全pytestの初回失敗は旧9タブを厳密期待していた `test_page_structure.py` のfixture差分であり新タブの期待値へ更新。初回独立Sonnetレビューはテスト不足を指摘、その回帰試験追加後の再レビューはsettings-onlyのP1/P2なし。ただし新しく追加したdigest・進捗コードの最終read-onlyレビューと全pytest/コミットhead受入は改めて実行する。
- M4の残作業：明示的な停止・中断再開、認証/GET consent boundaryを満たす実サービス統合、scheduler/download優先の**原子的**排他、5連続不明を示す実ラン状態、UI browser/nativeとexact-head CI。完全移植の残り（小説・うごイラ等）も未達。M4-Aをもって自動取得対応・Phase10 CI完了と表現しない。
- M4-A最終local全pytest **1583 passed / 1 skipped / 1 warning、失敗0**（`artifacts/phase10-m4-settings-progress-full-20261024.xml`）。Claude Sonnet最終read-only reviewも **APPROVE / P1=0 / P2=0**。現在ソースの独立監査は `verified_saved_receipt` index `[2]` 本体SHAと `[4]` ジョブfingerprint区別、ローカルのみの進捗、閉鎖・UI CASを確認。残P3はhidden tabでのローカルpoll・軽微なUIラベル/閉鎖競合時の表示。これはM4-Aローカル受入であり、製品のEdge/nativeとPR HEAD CIによるM5受入の代わりではない。

## M4-B: 明示的な作品別保留・中断確認と復帰（通信なし）

- M4-Aは `c38697eaed55f1d97670d203c6f58ad1b04df50f` にローカルコミット。その次の変更は同じPhase10専用作業ブランチ。`services.py`に side-ledger の作品状態照会・以前の中断確認・中断不明/保留の改訂CAS付き復帰を用意。NiceGUIの作品ID入力と明示操作ボタンを追加。成功後も `pending` へ戻すのみで **GET/ジョブ投入/自動再試行は絶対開始しない**。未保存設定を持つ画面での復帰は拒否し、他clientの改訂をCAS検査する。
- 前プロセスの `in_flight` は自動復帰しない。`acknowledge_interrupted`で前世代のGET終了確認を明示し `unknown` へ移行後、別の明示CAS操作 `resume_interrupted` で待機へ戻す。**同一processの生きたleaseへのackは拒否**。元SQLite、実Pixiv GET/Cookieおよび元ファイルの更新はなし。
- M4-B最初のfocus **37 passed**、元のM4-A+M4-B全体で **1586 passed / 1 skipped / 1 warning**（`artifacts/phase10-m4-recovery-full-20261024.xml`）。ただし以降に独立コードレビューがP2を2件報告し、未修正snapshotのGREENは最終受入に使用しない。
- Sonnet独立read-only reviewで検出したP2: `resume_blocked`のSQL CASが `base_sha` を照合せず世代競合をまたいでしまう、同一processでOFFにしたin-flightが `unknown` になり同じprocess内の旧GET終了前に復帰可能、の2件。いずれも合成テストで**RED 2件**を確認。前者は `base_sha`/lease-empty predicate追加、後者はprocess/世代ごとの揮発性 `same_epoch_unknown` ガードで再開拒否（プロセス再起動後の明示CAS復帰のみ許可）し、RED→GREEN 2件。最新focusは **113 passed**（`artifacts/phase10-m4-recovery-after-review-focused-20261024.xml`）。
- M4-Bはまだローカル追加変更であり、本章のGREENはPR exact HEAD / Edge / native受入を意味しない。全体pytestと独立再レビューの最終結果は該当run後に記録する。レビュー後もdownload優先排他、サービス実行ワーカーの停止/joinと無人処理、リアルPixiv GET許可・認証・権限の境界は未結線。これらが整うまで実取得は公開しない。
- **M4-B修正後**: Claude Sonnet独立read-only再レビュー **APPROVE / P1=0 / P2=0**。focused **113 passed**、全pytest **1588 passed / 1 skipped / 1 warning、失敗0**（`artifacts/phase10-m4-recovery-final-full-20261024.xml`）。現行snapshotは最終P2修正後のもの。P3は同一epoch不明記録の揮発guardセットをプロセス中に保持すること、UIエラー文言の改善余地。現在のauthoritative CIは依然Phase9統合後main成功であり、Phase10 PR/head CIではない。

## M4-C: 保存済み作品1件の明示GETとダウンロード優先ゲート（2026-10-24）

- ユーザーは **既存の実Pixiv Cookieを使った限定検証を許可**。既存`LivePixivReadClient`の明示ファイル指定を使用し、本人認証と既存ライブラリ内1作品の詳細GETに成功。検証はread-onlyで、Cookie値・応答本文・アカウント/作品IDを会話・Git・ログへ出力しなかった。`S:\Projects\PixivVault\pixiv_vault.db` はSQLite `mode=ro` + `query_only` で候補照会し、後埋め要件に該当する行は0件だったため不要な再取得を実行していない。元Cookie、元DB、画像、ZIPは変更していない。
- `ApplicationServices.run_managed_metadata_one()` は既存の明示オープン済み・設定ONの管理rootのみで動く。検証済み保存receiptから作品ID・作者ID・**receipt SHA `[2]`** を取得し、`MetadataCandidate` を1件だけ生成。元FletのDBへは書き込まない。実行にはユーザーの **「指定した保存済み1作品を取得」** ボタンが必要であり、設定ON/画面オープン/再起動ではGETしない。バックグラウンド定期チェックへは未接続。revision CASにより別画面の設定変更を検出し、二重クリックは`already_running`で拒否する。
- 全未完了JobService downloadを先に確認し、未完了ジョブがある場合は**認証GETすら行わず**`download_busy`を返す。1作品の処理中は既存の`_managed_enqueue_lock`を共有し、新規受付の割込みによる指紋/世代破綻を防ぐ。**厳密な即時プリエンプトではない**：GET開始後に来た新規ダウンロード受付は進行中のHTTP取得が自然終了するまで待つ場合がある（1要求上限 `MAX_REQUEST_SECONDS`、1操作上限 `MAX_OPERATION_SECONDS`）。次作品へ自動継続せず、取得は1作品で終了。今後のnative受入でこの受入待ち時間と停止応答を測定する。
- `stop_managed_metadata()` は進行中GETに停止イベントを送り、GET中断を無理に実行せず **完了待ち後に終了**。アプリcloseは先にstop eventを立て、専用run lockを待ってから side-ledger・JobService・Pixivクライアントを閉じる。送信前にStopされた場合はGETに進まない。初回独立レビューで「Stopを開始処理中のclearが消すP2」を発見し、イベントresetを **当該run完了後**に変更、狙い撃ち回帰試験を追加。UIには停止要求ボタンと結果件数・固定理由コードのみ表示し、GET実データ・秘密・応答は露出させない。
- M4-C focused初回 **99 passed**、UI追加後 **127 passed**、停止競合修正後 **96 passed**（`artifacts/phase10-m4c-service-focused-20261024.xml`、`...m4c-ui-focused...`、`...m4c-stop-fix-focused...`）。初回全pytest **1594 passed / 1 skipped / 1 warning** は停止競合修正前の記録につき、最終受入の代用にしない。後続の修正後全pytestと再レビューの結果で置き換える。
- **停止修正後の全pytest 1595 passed / 1 skipped / 1 warning、失敗0**（`artifacts/phase10-m4c-stop-fixed-final-full-20261024.xml`）。Claude Sonnetの独立read-only再レビューは **APPROVE / P1=0 / P2=0**。残P3: 既に進行中のGETに後から来たenqueueはGET終了まで有界に待つ仕様、Stopがrun終了瞬間と重なる場合には次回明示実行が1度cancelledになる可能性、停止要求表示の細分化。手動enqueue経路はNiceGUI event-loopで直接ブロックせず、`ui/job_views.py:_accepted_call`→`asyncio.to_thread`で処理されることをコードで確認。
- iPhone/iOS app連携は除外（`docs/MIGRATION_SCOPE_AMENDMENT_2026-10-24.md`）。元Baseline/履歴/証跡は無変更。Phase10のPR/正確なheadのCI、Windows native/Edge初回再起動の実受入と製品全体の完全移植はまだ未達。

## M5 受入状況（2026-10-24、未完）

- M4-C実装・iPhone連携除外を含むローカルcommit `48be3b8693b2c30135efd4782aea861f8838f8a2` で全pytest **1595 passed / 1 skipped / 1 warning**（`artifacts/phase10-m4c-stop-fixed-final-full-20261024.xml`）・M4-Cコード独立read-only APPROVE P1=P2=0。後からの新規DL受付は先行する有界GET終了まで待つ、という残P3は意図的な排他・非即時preempt境界として記録した。
- 既存作者差分/schedulerの合成実Edge受入を拡張してメタデータ設定タブの明示オープン・OFF→ON・再起動後の設定保持・台帳の空表示を検証した。初回の`Locator.check`操作はQuasar checkboxに合わず失敗し、`.click()` + `to_be_checked()` に修正。最初の成功証跡 `artifacts/phase10-m4c-edge-accept-recheck-20261024/result.json` は新しいrequest-count/DB直接照合より前のもの。最新版 `artifacts/phase10-m5-edge-no-implicit-get-20261024/result.json` は4/4 SUCCESS・外部要求0・通常終了/ポート解放4/4、さらにfake Pixiv GET count前後差分0を検査した。今回のテストfixtureは旧DBを使わないため、旧DB不変をこのテストで証明したとは主張しない。
- Claude Sonnetのread-onlyテスト監査は初回Edge差分を **APPROVE / P1=0 / P2=0** とし、P3として実測値を結果JSONへ保存することと台帳の直接照合を勧告。補強後 `artifacts/phase10-m5-edge-db-verified-20261024/result.json`、`artifacts/phase10-m5-edge-dynamic-revision-20261024/result.json`、最終checkerの `artifacts/phase10-m5-edge-final-checker-20261024/result.json` はすべて **4/4 SUCCESS**。最終checkerではSQLiteをmode=ro/query_onlyで読取り、`enabled=true`、revisionを初回OFF snapshotのCAS増分から導出して再起動前後一致、metadata row count=0、fake Pixiv request_count delta=0を直接照合。Console errors=0、page errors=0、foreign requests=0、normal exit/port release=4/4。source_hashes全件は現行srcと照合し **0 mismatch**。初回だけサイドカー未作成を確認し、明示オープン後に作成されることを実測。旧DB fixtureはこの検査に渡していないため、旧DB不変は別受入の責務とし、このEdgeテストの成果には含めない。
- Windows PyInstaller onedir build は専用worktreeの新規 `artifacts/windows-packaging/dist/PixivVault-NiceGUI` に成功。`tools/check_release_tree.py` による既知秘密パターンとパッケージ構造の走査は **1050 files / 87585456 bytes / 0 violations**。そのonedirの別rootコピー `artifacts/phase10-m4c-onedir-copy-20261024` に対する全1050件SHA manifest比較も一致。これは完全な秘密監査ではなく、既知パターン・ZIP・ファイル名の範囲限定。
- 旧native失敗証跡は履歴として保持：`artifacts/phase10-m4c-native-smoke-20261024.json` はCDP UI inspection TimeoutError後にrestore child visibility mismatch、`artifacts/phase10-m4c-native-owned-recheck-20261024.json` はUI inspectionなしの最初のGDI captureがflatで失敗。後者はWebViewのpaint完了を待たずcaptureしたため、成功/失敗を混ぜない。
- 新しいowned synthetic native再受入 `artifacts/phase10-m5-native-phase-diagnostic-20261024.json` と独立2回目 `artifacts/phase10-m5-native-repeat-20261024.json` は **両方SUCCESS**。WebView2 CDPでhandshake/cards=50/画像decode/bridge、gallery open-decode-close、learning/undo、library author/search/98 pages/viewer/focusを完了してからrestore gateへ進むと、両runでbaseline/restoreの安定フレーム各2、同一target visible=true、geometry一致、restore scene一致、topmost復元、通常終了、port解放まで成功。固定ラベルの診断用`ui_inspection_phase`を`check_native_window.py`へ追加したのみで、受入条件は緩めていない。両runとも`ui_inspection_phase=final_screenshot`、`restore_stable_samples=2`、`errors=[]`。例外時は最後に通過した固定phase labelを保存するため、失敗した個別API操作そのものの識別ではなく診断の粗い目安である。これにより前のnative blockerは合成native受入上は解消。既存の2つの失敗証跡も履歴として残す。実Cookie付きmanaged-root nativeの保存/再起動検査はまだ未実施で、Windows製品全体の最終受入やPR exact HEAD CIを代替しない。
- Phase10のGitHub PR headに対するWindows/Ubuntu/Chromium CI、独立最終受入とmain統合、他の未移植機能（うごイラ・小説等）は**未完**。途中証跡を完成判定に使わない。
- **最終M5ローカルゲート（2026-10-24）**: latest full pytest `artifacts/phase10-m5-acceptance-final-full-20261024.xml` = **1595 passed / 1 skipped / 1 warning / failures=0**。warningは既存Starlette/httpx TestClient deprecation、skipはWindows symlink権限。最終checker Edge結果 `artifacts/phase10-m5-edge-final-checker-20261024/result.json` は4/4 SUCCESS・direct SQLite read-only・dynamic CAS revision・empty ledger・fake GET delta0・source hash mismatch0。native合成実機は `phase10-m5-native-phase-diagnostic-20261024.json` と `phase10-m5-native-repeat-20261024.json` の2/2 SUCCESS。Claude Sonnet final read-only reviewは **APPROVE / P1=0 / P2=0**。`docs/BASELINE.md`と旧失敗証跡は変更せず保持。Phase10のPR/head CIはこれからであり、ローカル成功をCI成功や全移植完了と同一視しない。
