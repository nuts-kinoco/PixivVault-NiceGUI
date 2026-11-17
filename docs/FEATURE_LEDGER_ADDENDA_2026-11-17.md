# 機能対応台帳 追記（FL-Z）2026-11-17

- 位置づけ: `FEATURE_LEDGER_2026-11-17.md`（171 項目）の追記。元リポ `S:\Projects\PixivVault` を機械的に列挙した `inventory.json` のうち、既存台帳の行では説明しきれなかった **core.py・database.py・pixiv_client.py 由来の利用者機能**を、実際に元コードを読んで行にしたもの。
- 元: `S:\Projects\PixivVault`（読取のみ）。移植版: `S:\Projects\PV-DEV-20261117\work\src\pixivvault_nicegui`（読取のみ、work の HEAD `ae443b4`）。`file:N` は読んだ箇所。
- 状態の定義・「無」の書き方は元の台帳と同じ。「無」は grep した語と 0 件であることを根拠欄に書く。
- 対応表: `ledger_map.json`（inventory 項目 → FL-ID または `n/a:理由`）。検査: `tools\check_inventory_coverage.py`。
- 読んだ深さ: 元は下記「元の場所」の範囲を読んだ。移植版は grep で入口を探し、判定に使った箇所だけ読んだ。**「有」は読んだ範囲での判断で、実機での同値確認ではない。**

## 追記行

| ID | 元の機能 | 元の場所 | 移植版の場所 | 状態 | 根拠 |
|---|---|---|---|---|---|
| FL-Z01 | 保存ファイル名の規則（題名の無害化 `sanitize_filename`、`illust_save_filename`（題名・作品 ID・p 番号）、旧名 `illust_legacy_filename` との互換） | core.py:29-31, 467-523 | managed_downloads.py:814（管理領域内は `page-{n}.{ext}`）。元の配置への投影は save_exports.py:482（ジョブ `作者互換配置` = `author-projection`） | 弱 | 管理領域の名前は元と違う（設計差 D-00）。元の命名で書き出す投影ジョブはあるが、命名規則が元と同じかは読んでいない（要確認） |
| FL-Z02 | 作者フォルダの ZIP 追記・ZIP64 互換化・エントリ削除・ZIP 内の名称整合（`append_to_zip`、`normalize_zip_for_compatibility`、`remove_entry_from_zip`、`read_zip_entry_sizes`、`reconcile_author_zip_path`、`find_author_library_entry`） | core.py:123-434, 2385-2546 | save_exports.py:160, 359, 542-545（ジョブ `自動ZIP` = `auto-zip`。全体設定 `zip_all_after_download` または作者フラグで起動。542 行のコメントは元の合成規則と同じ） | 弱 | ZIP 化の起動規則は元と同じ。ZIP64 互換化・既存 ZIP への追記・名称整合の同等物は `normalize_zip`/`zip64` を save_exports.py で grep 0 件（ヒットは novel_*・ugoira_* の別用途）。作者 ZIP の中身の作り方は未読 |
| FL-Z03 | ダウンロード前のログイン生存確認（`check_login_alive`。未ログインなら実行を止める） | core.py:435-466（呼び出し 642, 1232） | managed_downloads.py:762, author_media.py:237, managed_scheduler.py:607（`verify_identity()`） | 有 | 保存・作者取得・自動取得の各入口で本人確認してから進む。名前は違うが同じ役割 |
| FL-Z04 | 通信完了時に Cookie を `cookies.txt` へ自動で延長保存（`_save_cookies_if_changed`、セッション Cookie を書き戻す） | pixiv_client.py:270-343, 725-735 | 無（`_save_cookies_if_changed`・`save_cookies`・`cookie.*refresh` を非 legacy の src で grep 0 件。`MozillaCookieJar` は auth_status.py:55・pixiv_read_client.py:268 で読むだけ。書くのは拡張からの受信時 extension_cookie_store.py:502 のみ） | 無 | 中。通信のたびに期限を延ばす挙動が無い。拡張機能を使わない人は期限が来たら取り直す。FL-A08（ブラウザからの自動抽出）と同じ領域。実装前に「Cookie を移植版が書き換えてよいか」の裁定が要る |
| FL-Z05 | 通信の再試行・待機・レート制限対応（`_request_with_retry`。待機間隔 ±20% の乱数、総試行数 `api_retry_count`、429 は `api_retry_wait` 待機、それ以外は 2^n+乱数 の指数待機、404 は即失敗。`get_rate_settings`） | pixiv_client.py:165-225, 686-760 | request_policy.py:81-187（`RequestAdmission` 81-97、`RequestRetryPolicy` 98-187）、preferences.py（`api_retry_count`/`api_retry_wait`/`download_interval`） | 弱 | 総試行数・429 待機・指数待機＋乱数・404 即失敗は同じ。**差**: (1) 待機間隔は固定の選択肢 0.3/0.4/0.5/0.6/1.0/1.5 秒で、元の ±20% の乱数は無い(request_policy.py:91)。(2) 再試行は GET のみ（元は POST も `_request_with_retry`）。(3) 401/403/リダイレクトも即失敗（元は 404 のみ即失敗）。(4) 408/425/5xx を一時障害として再試行（元は全例外を再試行）。意図した硬化か要確認。小 |
| FL-Z06 | 削除作品の確定規則（今回の一覧に無い作品を連続欠落でカウント、通常 3 回で確定、一度に 40% 以上・母集団 5 以上なら 5 回、確定時に警告） | core.py:1255-1300（定数 66-72）、database.py:1518-1555 | ui/managed_download_view.py:616（`連続N回` の表示）、services.py:1852-1858（完全な一覧取得のときだけ連続欠落を進める） | 弱 | 連続欠落の記録と表示、誤削除防止の方針（部分取得では進めない）はある。**3 回／5 回／40%／母集団 5 の確定規則と「削除されている可能性」警告**は `DELETION`・`HIGH_RATIO`・`required_streak` を非 legacy の src で grep 0 件。小 |
| FL-Z07 | 本番 DB へ接続する前の自動バックアップ（`prod_backup_state`/`ensure_prod_backup`、本番 DB パス判定） | database.py:26-151 | 無（`prod_backup`・`ensure_prod_backup`・`before_wipe` を非 legacy の src で grep 0 件） | 無 | 移植版は元の DB を書き換えない設計（D-00）のため、不要の可能性が高い。「不要」と決めるなら本人裁定を記録して `n/a` にする。それまでは無 |
| FL-Z08 | 作者ごとの個別設定（`author_settings`。対象タイプの上書き `target_type`、ZIP フラグ `is_zipped`） | core.py:1185-1195, 1344, 1783, 2147、database.py:1975-1998, 1861 | author_media.py:92, 120-153（`author_targets` の `target_type`）、save_exports.py:542（作者フラグ） | 有 | 対象タイプの上書きと ZIP フラグの読み取りは両方ある。元でも `set_author_setting` は呼び出し元なし（利用者が画面で設定する入口は元に無い）ため、書き込み入口が無いのは同じ |
| FL-Z09 | ディスク上の既存作品の判定（再ダウンロード要否。`check_work_exists_on_disk`、`judge_local_copy`、`find_existing_local`、`local_name_candidates`、同名題の衝突判定） | core.py:721-856, 2547-2719（3 つの根拠メッセージ 795-854） | author_media.py:276-323（`saved_update_date`・`files_present` による台帳ベースの判定）、managed_downloads.py:711-760（有効な受領書があれば再取得しない） | 有 | 判定方式は別（ファイル名の突き合わせではなく受領書）。「実物があれば落とし直さない」「実物が無ければ落とし直す」は成立。同名題の衝突を決められない場合の扱い（元 838 行）は受領書方式では発生しない |
| FL-Z10 | 通信のレーン（`interactive` を `background` より優先する共有ゲート `rate_coordinator`） | rate_coordinator.py:27-82、pixiv_client.py:686 | request_policy.py:81-97（`RequestAdmission`＝最小間隔の共有ゲート）、discovery_learning.py:42（コメント「元のレーン名は助言のみ。実際の入場は有界リーダーが持つ」） | 弱 | 間隔を共有ゲートで一本化する点は同じ。**レーンによる優先（画面操作を裏処理より先に通す）は無い**（lane 名は受け取るが助言のみ）。小〜中 |
| FL-Z11 | 同一作者の処理を直列化するロック（手動実行・定期チェック・拡張機能の 3 経路が同時に ZIP を壊さないため、`get_user_lock`） | core.py:77-88 | managed_downloads.py:256-264（作品単位の `work_lock`）、691（`_writer.acquire(blocking=False)`＝書き込みは全体で 1 本） | 有 | 書き込みが全体で 1 本のため、作者単位のロックが無くても同時書き込みは起きない（別方式）。作者単位ロックは `author_lock` を grep 0 件 |
| FL-Z12 | うごイラの MP4 保存に失敗したら GIF で保存し直す（OpenCV・imageio の 2 段、失敗理由を残す） | core.py:1030-1085（1080 行のメッセージ） | job_failure.py:19（`ugoira_mp4_failed` として失敗で返す）、ugoira_mp4.py:93-102 | 弱 | 失敗は理由コード付きで返すが、GIF への自動切替は ugoira_*.py で `fallback` を grep 0 件。小 |
| FL-Z13 | 元の SQLite DB のスキーマ（52 の表・列。作品台帳・フォロー中・設定・失敗キュー・学習・ブックマーク/SuperLike キャッシュ・埋め込み・重複台帳・容量キャッシュなど） | database.py:264-790 | 専用管理領域の DB 群（managed_*.py、failed_record_*.py、learning_repository.py、library_repository.py など）。元 DB は読むだけ（legacy_*.py） | 弱 | 設計差 D-00（元の DB・Images を更新しない）。表ごとの対応は `ledger_map.json` の `table_rules`（works→FL-D00、failed_queue→FL-F01、discovery_*→FL-I19・FL-L07・FL-B06、など）。同値確認ではなく、どの機能の表かの対応づけ |
| FL-Z14 | 保存し直した作品の推論記録を消して推論ワーカーを起こす（`_after_illust_saved`：`work_page_scan`・`work_embed_failures` の削除、起床通知） | core.py:33-78 | 無（`page_scan`・`embed_failure`・`clear_embed` を非 legacy の src で grep 0 件。別方式の候補: similarity_worker.py・managed_similarity.py） | 弱 | 落とし直して画像が変わった作品が、古い推論結果や「諦め」のまま残らないための処理。移植版の類似度処理が別方式で同じ効果を持つかは未読（FL-A12・FL-C21 と同じ領域）。小 |

## 無・弱の規模見積り（FL-Z）

| 規模 | ID | 内容 |
|---|---|---|
| 小 | FL-Z06 | 削除確定の定数と判定を足す |
| 小 | FL-Z12 | MP4 失敗時に GIF へ切り替える |
| 小 | FL-Z14 | 保存し直しで推論記録を消す／起こす（類似度処理の読解が先） |
| 小〜中 | FL-Z05 | 待機の乱数、POST 再試行、即失敗の範囲（意図した硬化かの確認） |
| 小〜中 | FL-Z10 | レーンによる優先 |
| 中 | FL-Z01 | 元の命名での書き出しの確認（読解が先） |
| 中 | FL-Z02 | ZIP64 互換化・追記・名称整合の有無の確認 |
| 中 | FL-Z04 | Cookie の延長保存（書き換えてよいかの裁定が先。FL-A08 と合わせて設計） |
| 裁定 | FL-Z07 | 本番 DB バックアップは不要と決めるか |

## 付録A: 元の文言が移植版に見つからないもの

<!-- APPENDIX-A:BEGIN -->

元の UI 文言(ui_text・menu_item・label_const・snack)のうち、移植版 `src/pixivvault_nicegui` 全体にその文言(f 文字列は最長の固定部分)が見つからないもの。
言い回しが違うだけの可能性があるため、**同値の判定ではなく確認の手がかり**。件数は台帳の FL ごと。

| 台帳 FL | 台帳の状態 | 未検出の文言 件数 | 例(元の場所) |
|---|---|---|---|
| FL-A04 | 弱 | 1 | 「終了確認」main.py:494 |
| FL-A06 | 条件付きで有 | 1 | 「いいえ」main.py:498 |
| FL-A08 | 無 | 10 | 「[確認中] Cookie の状態をチェック中」gui.py:1119; 「[更新完了] {}」gui.py:1132; 「✓ Cookie をブラウザ ({}) から」gui.py:1134 |
| FL-A09 | 無 | 1 | 「現在保存されているフォロー中データ（お気に入」gui.py:6304 |
| FL-A11 | 有 | 1 | 「PV拡張機能をご利用中の場合、cookies」gui.py:2844 |
| FL-A12 | 弱 | 3 | 「{} （詳しい中身は gui_error_l」gui.py:2064; 「前回は次の理由で止まりました: {}。ダウン」gui.py:2070; 「前回は次の理由で止まりました: {}。『いま」gui.py:2076 |
| FL-A14 | 条件付きで有 | 6 | 「自動起動を有効にしました！」gui.py:1607; 「登録に失敗しました。」gui.py:1609; 「自動起動を無効にしました。」gui.py:1617 |
| FL-B03 | 有 | 1 | 「ブラウザで Pixiv を開く (ログイン状」gui.py:2830 |
| FL-B04 | 有 | 1 | 「対象の保存フォルダが見つかりません。」gui.py:2441 |
| FL-B06 | 有 | 11 | 「閲覧履歴（学習タブ・{}件）」gui.py:2466; 「閲覧履歴: 全件削除しました」gui.py:2486; 「閲覧履歴: 削除に失敗しました: {}」gui.py:2494 |
| FL-B10 | 条件付きで有 | 7 | 「Shaft SECRET_KEY（任意・既に」gui.py:878; 「UID一覧（txt）を読み込む」gui.py:950; 「先に tools/nana7mi_vault」gui.py:963 |
| FL-C01 | 有 | 2 | 「[完了] cookies.txt をインポー」gui.py:1094; 「キャンセルされました。」gui.py:1101 |
| FL-C02 | 有 | 3 | 「[完了] 同期完了 ({}人の作者)」gui.py:1215; 「[失敗] 同期失敗: {}」gui.py:1222; 「リストを同期」gui.py:1664 |
| FL-C03 | 有 | 3 | 「現在の保存先: {}」gui.py:1192; 「フォルダー選択エラー: {}」gui.py:1199; 「Pixivから同期中...」gui.py:1203 |
| FL-C04 | 条件付きで有 | 1 | 「有効 ─ 自動起動は登録済みです」gui.py:1599 |
| FL-C06 | 条件付きで有 | 1 | 「☆ お気に入り自動チェック間隔」gui.py:1271 |
| FL-C09 | 条件付きで有 | 2 | 「GIF動画 (デフォルト)」gui.py:1257; 「すべてフォルダ格納する (ZIP解凍+JSO」gui.py:1259 |
| FL-C10 | 条件付きで有 | 3 | 「新着通知のON/OFF」gui.py:1318; 「ON (通知する)」gui.py:1320; 「OFF (通知しない)」gui.py:1321 |
| FL-C11 | 条件付きで有 | 3 | 「ブックマーク保存モード」gui.py:1332; 「ブックマークフォルダ内は作者別フォルダを作ら」gui.py:1334; 「ブックマークフォルダ内も作者別フォルダを作成」gui.py:1335 |
| FL-C12 | 条件付きで有 | 6 | 「ダウンロード待機時間 (秒)」gui.py:1400; 「0.3秒 (最速・自己責任)」gui.py:1402; 「0.4秒 (高速)」gui.py:1403 |
| FL-C13 | 条件付きで有 | 7 | 「3回 (推奨)」gui.py:1420; 「5回」gui.py:1421; 「429 レート制限検知時待機時間」gui.py:1433 |
| FL-C14 | 条件付きで有 | 14 | 「学習: 画像先読みキャッシュの上限指定方法」gui.py:1458; 「メモリ使用量で指定」gui.py:1460; 「画像枚数で指定」gui.py:1461 |
| FL-C15 | 条件付きで有 | 16 | 「画像キャッシュ上限 (全タブ共有・メモリ使用」gui.py:1494; 「512MB」gui.py:1496; 「1024MB」gui.py:1497 |
| FL-C16 | 条件付きで有 | 3 | 「キャッシュを削除しました ({}件)」gui.py:1365; 「キャッシュはありませんでした。」gui.py:1368; 「キャッシュを削除する」gui.py:1374 |
| FL-C17 | 条件付きで有 | 1 | 「すべての作者に対しダウンロード完了後zipに」gui.py:1231 |
| FL-C18 | 条件付きで有 | 1 | 「最小化時にタスクトレイに格納する」gui.py:1344 |
| FL-C21 | 弱 | 3 | 「いま推論する」gui.py:1823; 「モデルの読み込みに失敗」style_infer.py:106; 「構図の出口が無い」style_infer.py:108 |
| FL-C22 | 条件付きで有 | 5 | 「エクスポートを実行」gui.py:2320; 「エクスポートが完了しました: {}」gui.py:2330; 「エクスポートに失敗しました: {}」gui.py:2336 |
| FL-C24 | 無 | 1 | 「ver 4.0.0 build260913-」gui.py:2420 |
| FL-C26 | 意図的削除 | 4 | 「接続トークンを再発行する（旧トークンで設定済」gui.py:803; 「API経路を切り替える」gui.py:820; 「API切り替え」gui.py:822 |
| FL-D02 | 有 | 3 | 「絞り込み（数字はID指定）」unified_download_tab.py:404; 「前の50人」unified_download_tab.py:413; 「次の50人」unified_download_tab.py:418 |
| FL-D03 | 弱 | 4 | 「作者名順」unified_download.py:53; 「保存済みの多い順」unified_download.py:54; 「最後に取得した日」unified_download.py:55 |
| FL-D04 | 有 | 2 | 「マイブックマーク（全体）」unified_download_tab.py:587; 「左の一覧でチェックすると、ここに積まれます」unified_download_tab.py:628 |
| FL-D05 | 条件付きで有 | 2 | 「0 人」unified_download_tab.py:1513; 「自動監視」unified_download_tab.py:1519 |
| FL-D06 | 有 | 2 | 「対象に積む」unified_download_tab.py:389; 「フォロー中の {} です（ID: {}）」unified_download_tab.py:798 |
| FL-D09 | 弱 | 5 | 「左の一覧で作者を選ぶと押せます」unified_download_tab.py:513; 「差分DL · {} · 保存先 {}」unified_download_tab.py:533; 「再開しました。」unified_download_tab.py:1046 |
| FL-D10 | 弱 | 12 | 「残り時間計算中...」gui.py:381; 「この回の対象」unified_download_tab.py:206; 「0 人」unified_download_tab.py:207 |
| FL-D11 | 弱 | 2 | 「{} 人を処理しています…」unified_download_tab.py:1241; 「含めるブックマークを1つ選びます」unified_download_tab.py:1437 |
| FL-D12 | 弱 | 1 | 「階層化（作者別フォルダ）」unified_download_tab.py:1440 |
| FL-D13 | 条件付きで有 | 10 | 「cookies.txt が見つかりません。設」gui.py:4884; 「統合DL が完了しました」gui.py:4911; 「統合DL でエラー: {}」gui.py:4916 |
| FL-D14 | 条件付きで有 | 16 | 「ログインチェックを完了してください。」gui.py:3785; 「含めるブックマークを1つ選びます。」gui.py:3788; 「抽出エラー: {}」gui.py:3805 |
| FL-D15 | 条件付きで有 | 3 | 「user_id={}: 未認証のため、削除の」core.py:1243; 「user_id={}: 今回の一覧に見当たら」core.py:1275; 「以下の作品がPixivから削除されている可能」core.py:1288 |
| FL-D16 | 弱 | 2 | 「PixivVault - ZIP破損検出」core.py:112; 「{} のアーカイブに問題があります: {}」core.py:112 |
| FL-D17 | 有 | 2 | 「M:\2次\PixivVault」database.py:20; 「M:\2次\PixivVault」database.py:23 |
| FL-E01 | 有 | 3 | 「現在のジョブはありません」gui.py:3617; 「iOS版から送られた取得ジョブがここに並びま」gui.py:3618; 「一致するジョブはありません」gui.py:3622 |
| FL-E04 | 有 | 1 | 「作者名で絞り込む」gui.py:3386 |
| FL-E05 | 有 | 5 | 「全ジョブを削除しますか？」gui.py:3341; 「完了・エラーを含む全ジョブをキューから削除し」gui.py:3342; 「{}件を削除しますか？」gui.py:3363 |
| FL-E06 | 無 | 2 | 「ダウンロードキューログ」gui.py:3708; 「拡張機能連携のDLはここにのみ表示」gui.py:3710 |
| FL-E07 | 弱 | 1 | 「2秒毎に自動更新／一覧かタイトルのダブルクリ」gui.py:3739 |
| FL-F02 | 有 | 1 | 「再試行エラー ({}): {}」gui.py:4217 |
| FL-F03 | 有 | 6 | 「再試行対象の失敗作品がありません。」gui.py:4291; 「失敗リスト（絞り込み分）の再試行: {}/{」gui.py:4307; 「失敗リストを全件クリアしますか？」gui.py:4319 |
| FL-G01 | 有 | 5 | 「直近バックアップ履歴取得エラー: {}」gui.py:4512; 「直近バックアップ履歴: PixivClien」gui.py:4528; 「{} ({}) クリックして開く」gui.py:4693 |
| FL-H01 | 有 | 2 | 「スキャン中…」gui.py:5711; 「重複整理スキャン: 」gui.py:5742 |
| FL-H03 | 無 | 1 | 「{}（{}）のものにする」gui.py:5172 |
| FL-H04 | 有 | 5 | 「{}枚を選択中」gui.py:5261; 「全選択を戻しました（1枚は必ず残ります）」gui.py:5269; 「画質の低い方をまとめて選ぶ」gui.py:5351 |
| FL-H05 | 有 | 1 | 「この操作では消えません。あとから戻せます。各」gui.py:5529 |
| FL-H07 | 有 | 4 | 「作者名・user_idで絞り込む」gui.py:5032; 「1組ずつ開かずに、この一覧のまま選んで移せま」gui.py:5044; 「0件選択中」gui.py:5051 |
| FL-I02 | 有 | 1 | 「直前の判定を取り消す　Ctrl+Z」discovery_tab.py:2544 |
| FL-I05 | 無 | 1 | 「何が合いませんでしたか？」discovery_tab.py:1760 |
| FL-I06 | 弱 | 1 | 「判定しました」discovery_tab.py:1680 |
| FL-I07 | 有 | 1 | 「画像を見ないままの判定はできません」discovery_tab.py:2385 |
| FL-I08 | 有 | 4 | 「クリックで画集ビューアを開く」discovery_tab.py:2446; 「クリックでpixivの作品ページを開く」discovery_tab.py:2456; 「絵が表示されてから押せます」discovery_tab.py:2885 |
| FL-I09 | 弱 | 1 | 「絵が表示されてから押せます」discovery_tab.py:2577 |
| FL-I10 | 条件付きで有 | 2 | 「ダウンロードキューへ積む」ranking_tab.py:780; 「ダウンロードキューへ積む」recommend_tab.py:847 |
| FL-I11 | 有 | 5 | 「SuperLike使用: {}回」discovery_tab.py:143; 「いまはあなたの保存から出しています」discovery_tab.py:2975; 「これまでの判定: {}枚」discovery_tab.py:2976 |
| FL-I12 | 弱 | 3 | 「探索の設定」discovery_tab.py:3295; 「どのくらい知らないものを見るかを変えます」discovery_tab.py:3302; 「判定は保存され、次に出す候補の並べ替えに反映」discovery_tab.py:3360 |
| FL-I13 | 有 | 3 | 「作品の情報を取れませんでした。押すともう一度」discovery_tab.py:3853; 「作品の情報を取りにいっています」discovery_tab.py:3860; 「タップで再取得」discovery_tab.py:5001 |
| FL-I14 | 有 | 3 | 「作品を「見送る」「合わない」「いいね」「Su」discovery_tab.py:4357; 「自動での再取得は止まっています。」discovery_tab.py:4409; 「通信に失敗しました。30秒ごとにもう一度試し」discovery_tab.py:4409 |
| FL-I15 | 無 | 2 | 「画像からのタグ補完にはローカルモデルの導入（」discovery_tab.py:4366; 「設定を開く」discovery_tab.py:4367 |
| FL-I16 | 有 | 9 | 「『{}』の判定を取り消しました — {}」discovery_tab.py:5302; 「今日は該当なし」discovery_tab.py:5412; 「今日は該当なし」discovery_tab.py:5418 |
| FL-I18 | 有 | 4 | 「これまでの判定（{}枚）と、そこから学んだ好」gui.py:2195; 「学習状況: リセットに失敗しました: {}」gui.py:2224; 「CSV出力完了: {}」gui.py:2260 |
| FL-J01 | 有 | 7 | 「DL済みを除外」ranking_tab.py:158; 「出さない作者を除外」ranking_tab.py:159; 「「出さない」登録の作者」ranking_tab.py:159 |
| FL-J05 | 有 | 2 | 「R-18を含める」ranking_tab.py:1319; 「AI作品を除外（作品ごとに追加確認するため、」ranking_tab.py:1324 |
| FL-J06 | 有 | 2 | 「タグ検索に失敗しました（{}）」ranking_tab.py:1130; 「タグで絞り込み」ranking_tab.py:1328 |
| FL-J07 | 有 | 3 | 「取得時刻: {}　{}件」ranking_tab.py:955; 「ランキングを取得できませんでした（{}）」ranking_tab.py:1056; 「ランキングを取得できませんでした（{}）」ranking_tab.py:1067 |
| FL-J08 | 有 | 2 | 「クリックで画集ビューアを開く」ranking_tab.py:857; 「クリックで画集ビューアを開く」ranking_tab.py:1577 |
| FL-J09 | 弱 | 5 | 「ブックマークしました。」ranking_tab.py:617; 「「{}」をもっと見るに追加しました。」ranking_tab.py:760; 「ブックマークする」ranking_tab.py:778 |
| FL-J10 | 無 | 7 | 「iOS連携サーバーが起動していないためキュー」ranking_tab.py:627; 「ダウンロードキューへ積みました。」ranking_tab.py:629; 「出さない一覧（{}件）」ranking_tab.py:678 |
| FL-J11 | 条件付きで有 | 6 | 「検索中…」ranking_tab.py:1592; 「検索に失敗しました（{}）」ranking_tab.py:1690; 「検索結果の処理に失敗しました。」ranking_tab.py:1737 |
| FL-J12 | 有 | 9 | 「いまの絞り込み条件を保存しました。」ranking_tab.py:2033; 「タグを追加してEnter（複数タグの組み合わ」ranking_tab.py:2120; 「R-18を含める」ranking_tab.py:2136 |
| FL-K01 | 有 | 10 | 「{} 人の作者中 {} 人を表示 / 全 {」library_tab.py:2203; 「{} 人の作者 / 全 {} 人」library_tab.py:2205; 「pixiv本体の検索に失敗しました（{}）」library_tab.py:2215 |
| FL-K02 | 弱 | 7 | 「選択中: 0件」library_tab.py:1875; 「タイトル・作者・タグを検索」library_tab.py:2279; 「ライブラリのみ」library_tab.py:2325 |
| FL-K04 | 有 | 15 | 「このタブでは使いません」filter_panel.py:88; 「データ取得後に使えるようになります」filter_panel.py:89; 「未実装です」filter_panel.py:90 |
| FL-K05 | 有 | 1 | 「いま表示している作者ぶんの容量キャッシュを消」library_tab.py:2978 |
| FL-K06 | 弱 | 11 | 「選択をダウンロード」library_tab.py:1876; 「{} 件中 {} 件を表示 / 全 {} 件」library_tab.py:1950; 「{} 件 / 全 {} 件」library_tab.py:1952 |
| FL-K07 | 有 | 2 | 「<ContextMenu>」library_tab.py:1168; 「クリックで画集ビューアを開く」library_tab.py:1383 |
| FL-K08 | 有 | 2 | 「クリックでこの作者の作品一覧を開きます」library_tab.py:1241; 「クリックでpixivの作者ページを開く」library_tab.py:1262 |
| FL-K10 | 条件付きで有 | 6 | 「クリックで画集ビューアを開く」library_tab.py:1007; 「解析済みの作品がまだありません。」library_tab.py:1028; 「この作品はまだ解析されていません（解析済み:」library_tab.py:1030 |
| FL-K11 | 有 | 5 | 「実ファイルが見つからないため1ページ目の画像」library_tab.py:1627; 「クリックで p.{} から画集ビューアを開き」library_tab.py:1630; 「クリックで p.{} から画集ビューアを開き」library_tab.py:1664 |
| FL-L01 | 有 | 14 | 「傾向ピルをクリックして追加したタグの候補」recommend_tab.py:151; 「好みの学習結果に基づく候補」recommend_tab.py:152; 「あなたが好んで見ているタグの作品」recommend_tab.py:153 |
| FL-L02 | 有 | 9 | 「この節を開く」recommend_tab.py:2830; 「この節を開く」recommend_tab.py:2860; 「この節を閉じる」recommend_tab.py:2860 |
| FL-L03 | 有 | 2 | 「更新（取得済みの内容を取り直します）」recommend_tab.py:2671; 「今日は該当なし」recommend_tab.py:2681 |
| FL-L04 | 有 | 2 | 「このタグを開く」recommend_tab.py:1894; 「このタグを開く」recommend_tab.py:1987 |
| FL-L05 | 弱 | 15 | 「ブックマークしました。」recommend_tab.py:509; 「iOS連携サーバーが起動していないためキュー」recommend_tab.py:651; 「ダウンロードキューへ積みました。」recommend_tab.py:653 |
| FL-L07 | 有 | 9 | 「次回から表示しません。」recommend_tab.py:622; 「「{}」を次回から表示しません。」recommend_tab.py:722; 「出さない一覧（{}件）」recommend_tab.py:1337 |
| FL-L09 | 弱 | 8 | 「今日は該当なし」recommend_tab.py:2707; 「もっと見る に登録中の作者」recommend_tab.py:2731; 「もっと見る登録中の作者はいません。」recommend_tab.py:2733 |
| FL-L10 | 条件付きで有 | 7 | 「フォローしました。」recommend_tab.py:3484; 「差分DLを実行しました。」recommend_tab.py:3494; 「差分DLに失敗しました。」recommend_tab.py:3497 |
| FL-M02 | 有 | 1 | 「説明文はありません。」artbook_viewer.py:1371 |
| FL-M03 | 条件付きで有 | 5 | 「ページを取得できませんでした。」artbook_viewer.py:510; 「画像を取得できませんでした。」artbook_viewer.py:540; 「ブックマークしました。」artbook_viewer.py:673 |
| FL-M04 | 弱 | 6 | 「次回から表示しません。」artbook_viewer.py:817; 「pixivで作品ページを開く」artbook_viewer.py:860; 「前へ（←）」artbook_viewer.py:1270 |
| FL-M05 | 条件付きで有 | 3 | 「アニメーション」artbook_viewer.py:413; 「うごイラを取得できませんでした。」artbook_viewer.py:448; 「まだうごイラを読み込めていないため保存できま」artbook_viewer.py:1045 |
| FL-N02 | 弱 | 4 | 「PixivVault ダウンロード完了」server.py:1586; 「{} (id={}) の保存が完了しました！」server.py:1586; 「PixivVault エラー」server.py:1588 |
| FL-N05 | 弱 | 7 | 「PixivVault 転送完了（一部失敗）」server.py:1308; 「{} の転送が終わりました（成功{}件／失敗」server.py:1309; 「PixivVault 転送完了」server.py:1311 |
| FL-O01 | 弱 | 2 | 「PixivVault 定期チェック」scheduler.py:227; 「お気に入りの作者から {} 件の作品を保存し」scheduler.py:228 |
| FL-O02 | 条件付きで有 | 1 | 「起動時にもお気に入り新着チェックを実行する」gui.py:1309 |
| FL-X01 | PV_DEV_P | 3 | 「アプリ: 未接続」gui.py:690; 「PixivVault ダウンロード開始」server.py:1522; 「{} (id={}) の保存を開始します。」server.py:1522 |
| FL-Z09 |  | 1 | 「user_id={}: この作者の実物が1つ」core.py:1335 |

合計 466 件。

<!-- APPENDIX-A:END -->
