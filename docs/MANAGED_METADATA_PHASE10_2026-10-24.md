# Phase10 — 管理root専用メタデータ後埋めの契約と進行

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
