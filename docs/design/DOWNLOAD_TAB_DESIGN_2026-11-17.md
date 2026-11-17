# ダウンロードタブ復元の設計書（FL-D00）2026-11-17

- 対象: 台帳 FL-D00「ダウンロードの全体設計」。本人の裁定「元に合わせる」を、移植版で実装するための設計。
- 範囲: 設計のみ。製品コード（`src`）は変更しない。
- 略記: 元 = `S:\Projects\PixivVault`（Flet 版、正）。移植版 = `wt-s\src\pixivvault_nicegui`。`UD` = 元の `unified_download_tab.py`。`UDL` = 元の `unified_download.py`。
- 根拠は `ファイル:行` で書く。**台帳や元コードのコメントにある古い行番号（`core.py:2052` の sleep、`core.py:2791` の `download_bookmarks`）は使わない**（実際は `core.py:2269` と `core.py:2720`）。
- 読んだ深さ: 元の実行経路は通読した。実機での再現はしていない。コード読みだけの結論には（コード読み）と付けた。

## 0. 要約

1. **元の実行モデル**は「実行 1 回 → 選択した作者全員を順に、一覧全件取得 → `update_date` 比較 → 新規・更新・復帰を保存 → 削除検知 → 作者ごとに `following_users.last_downloaded` を更新 → 4〜6 秒待って次の作者」。更新する DB は `works`・`failed_queue`・`following_users`。
2. **移植版**は「作者ごとに差分を確認 → 作品を選ぶ → 1 作品 1 ジョブでキューへ」。作者の一括実行、バッチ全体の一時停止・停止・進捗、`last_downloaded` の更新が無い。画面も 1 カラムで元と違う。
3. **推奨は案 C**: 管理領域の台帳を正とし（案 A の全部）、旧 DB への反映は「冪等な反映器」として別部品に切り出す。反映器は、本人の確認（§10 の確認 1）を得てから、安全策つきで有効化する。台帳だけでも、実行 1 回の流れ・画面・`last_downloaded` の表示は完成する。
4. **作業分割**は 10 単位（WU-1〜WU-10）。Sonnet 1 本で終わる大きさに分け、WU-9（旧 DB 反映器）だけは本人の回答待ち。
5. **本人に確認が要る点**は 6 つ（§10）。最重要は「旧 DB への書き込みを、どの DB に、どこまでやるか」。

## 1. 裁定の引用と用語

- 本人の裁定（`docs\PV_DEFINITION_OF_DONE_2026-11-17.md`「事前に確定した判断」表）: 「FL-D00 ダウンロードの全体設計 ＝ 元に合わせる（実行 1 回で全員を差分 DL し旧 DB も更新）」。
- 今回の依頼文の裁定: 「実行 1 回で、選択した作者全員を差分 DL し、DB（last_downloaded など）も更新する」流れと、元の 2 カラム構成を復元する。
- **この設計書の判定**: 推奨案 C は、段階 1（台帳）だけでは「旧 DB も更新」を**文字どおりには満たさない**。文字どおり満たすのは段階 2（反映器、WU-9）。そのため段階 2 は確認 1 の選択肢にする（黙って決めない）。
- 「旧 DB」の意味を分ける。
  - **原本 DB** = 元アプリが使っている生きた `pixiv_vault.db`。WAL で、`-wal`/`-shm` が横にある（`original_copy\data\pv_iso.db` の例: 本体 4 KB に `-wal` 1.2 MB）。
  - **凍結コピー** = 移植版が読む、単独ファイルの複製。`immutable_copy_signature` は `-wal`/`-shm`/`-journal` があると拒否する（`failed_records_repository.py:34-64`）。`services.py:28-69` の `_legacy_managed_capabilities` も同じ前提。
  - 凍結コピーに書いても、元アプリには何も届かない。「旧 DB も更新」が意味を持つのは**原本 DB に書くとき**だけ。
- 移植版は旧 DB が無くても動く（`NativeProfileSnapshot.library_readonly_db` は省略可、`native_profile.py:70-79`）。**管理領域の台帳は、どの案でも必須の土台**。

## 2. 元の実行モデル（正確な記述）

### 2.1 入力

| 入力 | 元の場所 | 内容 |
|---|---|---|
| 対象の作者 | UD:1030 | 「この回の対象」に積まれた作者 ID の並び。左の一覧でチェック、☆を対象にする、ID 入力（数字だけの絞り込みは ID 指定、UDL:51-70 `is_id_query`）で積む。重複は積まない（UDL `add_target`、先に積んだ由来を残す） |
| 実行モード | UD:1033-1036 | 完全 DL は 08-14 に画面から廃止。**常に差分**（`is_full=False`）。`new_only` は渡さない（既定 False） |
| 対象種別 | UD:1343-1368 | イラスト・漫画 / 小説 / 両方の循環ボタン。作者個別設定 `author_settings.target_type` があれば作者ごとに上書き（`core.py:1185-1189`） |
| 保存先・形式 | `core.py:1298-1301` | `save_path`、`novel_save_format`、`ugoira_save_format`、`use_work_folder`、`zip_all_after_download` |
| マイブックマーク | UD:1271-1342 | モードを切り替え、公開/非公開のチェック（`bm_rest_show/hide`）、階層化/フラット（`use_work_folder` へ書く）で実行 |

実行ボタンは対象が 0 人なら押せない（「左の一覧で作者を選ぶと押せます」、`1280x800_light_download.txt:68`）。実行中の再実行は「いまダウンロードが走っています」とログに出して無視（UD:1027-1029）。

### 2.2 手順（フォロー中の作者モード）

1. UD:1026 `_run` → `_start(ids, False)`（UD:1222）。画面を「実行中」にし、別スレッドで `start_download` を呼ぶ。
2. `gui.py:4881` `_unified_start_download`: `cookies.txt` の有無だけを確認（無ければ警告して "rejected"）。`batch_stop_event`/`batch_pause_event` を clear。フロー `unified` を active にする（`gui.py:566-578`、`is_downloading_active`）。
3. `core.py:2182` `run_batch_backup`: 作者ごとに次を繰り返す。
   1. `check_state()`（停止なら例外、一時停止なら待つ）。
   2. `batch_progress_callback(idx, total, user_id, elapsed)` と `batch_stats_callback(idx, total, 累計)` を呼ぶ。
   3. `run_backup`（`core.py:1109`）。作者単位のロック `get_user_lock`（`core.py:80`。手動・定期・拡張機能が同じ作者を同時に処理しない。プロセス内のみ）の下で `_run_backup_impl`（`core.py:1133`）。
   4. 例外が出て停止要求でなければ、ログに出して**次の作者へ**（`core.py:2257-2260`）。停止要求なら再送出。
   5. 成功した作者の後に `db.update_following_last_downloaded`（`core.py:2254`）。
   6. 作者の間は `random.uniform(4.0, 6.0)` 秒待つ（`core.py:2269`、最後の作者の後は待たない）。待ちは `time.sleep` で、**停止しても中断できない**（UD:1055-1070 のコメント）。
4. 最後に累計サマリーを 1 行ログ、`batch_stats_callback` を最終値で 1 回呼ぶ。

`_run_backup_impl` の 1 作者の中身:

| フェーズ | 元の場所 | 内容 |
|---|---|---|
| 前処理 | `core.py:1185-1192` | 作者個別設定の対象種別を適用 |
| A 一覧取得 | `core.py:1194-1220` | `get_user_works`/`get_user_novels` で全作品の一覧を取得（`new_only=False` なので打ち切りなし）。一覧が「見えなかった」（非公開等）かを `list_visible_out` で記録 |
| B 削除検知 | `core.py:1222-1291` | `check_login_alive`（応答で確認、`core.py:435`）。一覧が見えない・未認証なら**削除検知だけ**スキップして続行。それ以外は DB の作品 ID と比較し、連続欠落を数える（通常 3 回、母集団 5 以上かつ 40% 以上なら 5 回、`core.py:66-72`）。確定で `mark_as_deleted` |
| C 保存 | `core.py:1293-1766` | 作品ごとに判定（下表）。必要なら取得、品質チェック、成功時だけ `upsert_work` と `remove_failed_job` |
| D ZIP | `core.py:1768-` | `zip_all_after_download`、作者の `is_zipped`/`auto_archive` で ZIP へ追記 |

C の判定（差分、`is_full=False`、`core.py:1427-1500`）:

| 状況 | 判定 | 動作 |
|---|---|---|
| DB に行が無い | 新規 | 取得 |
| `update_date` が DB と違う | 更新 | 取得 |
| `is_deleted=1` | 復帰 | 取得 |
| 一致し、実物がある | スキップ | 何もしない |
| 一致するが実物が無い | 実物なし再取得 | `redownload_missing` が `0` の作者は飛ばす。作者の実物が 1 つも無ければ 1 件も落とし直さず警告（保存先違いの疑い、`core.py:1457-1470`）。判定できないときは飛ばす。重複整理で隔離したものは飛ばす（`core.py:1493`） |

保存後の品質チェック（`verify_work_integrity`、`core.py:857`）に失敗したら `add_failed_job` と失敗数の加算だけで、**DB の作品行は更新しない**（`core.py:1598-1601, 1660-1665`）。

### 2.3 更新する DB の表と列

| 表 | いつ・何を | 元の場所 |
|---|---|---|
| `works` | 保存成功時に `upsert_work`（`last_backup`＝現在時刻、`is_deleted=0`、`tags`、メタ列）。削除確定で `is_deleted=1`。見つかった作品は `missing_streak=0`、見つからなければ +1 | `database.py:1561`、`:1518-1556`、`core.py:1602, 1639, 1733` |
| `failed_queue` | 失敗で `add_failed_job`（既存なら `retry_count+1`）、成功で `remove_failed_job` | `database.py:1940-1965`、`core.py:1599, 1603` |
| `following_users.last_downloaded` | **作者ごとに `run_backup` が例外なく戻った後**に現在時刻（ISO）を書く。作品の失敗があっても書く。停止・例外では書かない。フォロー行が無い作者（ID 指定）は `UPDATE` が 0 行で何も起きない | `core.py:2254`、`database.py:1823-1831` |
| `following_users`（読み） | `is_zipped`（ZIP 判定、`core.py:1769-1790`）、`is_favorite`（☆）、`follow_order`、`is_following` | `database.py:333-362` |
| `author_settings`（読み） | `target_type`、`redownload_missing`、`auto_archive` | `database.py:375-381, 1975` |
| 推論記録 | イラスト保存後 `_after_illust_saved`（`work_page_scan`・`work_embed_failures` を消し推論ワーカーを起こす） | `core.py:33-60` |
| ファイル | `Images\{作者名}({uid})\...` または `.zip` | `core.py:1368-1372` |

マイブックマークモード（`core.py:2720` `download_bookmarks`）: **`last_downloaded` は更新しない**（`update_following_last_downloaded` の呼び出しは `core.py:2254` の 1 か所だけ）。`works`/`failed_queue` は同様に更新。削除済みアカウントの作品（`userId=0`・`-----`）は除外（`core.py:2763-2784`）。

### 2.4 停止・一時停止・再開

- 一時停止: `pause_event` を set/clear するだけ（UD:1039-1052）。走っている処理が `check_state()` で `pause_event.wait(1.0)` を繰り返す（`core.py:2209-2220`）。**メモリ上のみ**で、アプリが落ちれば失われる。
- 停止: `stop_event.set()`、一時停止中なら `pause_event.clear()`、画面を即座に「停止しています（いまの作者が終わるまで待っています）…」へ（UD:1055-1073）。実際の停止は次の `check_state()`（作品の間、画像の間、作者の間の待ちの後）。
- **停止後の再開機能は無い**。再実行すれば、更新済みの `update_date` が DB にあるので、差分で残りだけが対象になる。これが元の「再開」の実体。
- 停止の見え方（コード読み）: `check_state` が通常の `Exception("一括処理がユーザーによって中止されました。")` を投げる。`run_batch_backup`（停止要求なら再送出、`core.py:2257-2259`）→ `_unified_start_download` の `except`（`gui.py:4906-4911`: ログ・エラー通知・再送出）→ UD の `_work`（UD:1260-1262）の `except` で、**`outcome="failed"`、「失敗しました: …中止されました」と出る**。`elif state["stopping"]: "停止しました。"`（UD:1257-1259）は例外が出ない場合だけ到達する。つまり元は停止を「失敗」と表示している可能性が高い（実機未確認）。移植版は「停止しました。」を出す（§3 の差の表に記録）。
- 停止時に `last_downloaded` が書かれないのは、例外が `core.py:2254` の手前で抜けるため。

### 2.5 進捗

- 作者単位: `on_batch_progress(idx,total,user_id)`（UD:1102）。**終わった人数は `idx-1`**。分母は人数で固定（作品数にすると伸び縮みするため）。
- 作品単位: `on_work_progress`（UD:1139）、作品名 `on_work_info`（UD:1171、`core.py:1396-1405` で `progress_callback` より先に呼ぶ）。
- 件数・失敗数: `on_batch_stats`（UD:1183）。作者ごとの確定件数は持たない（累計のみ）。
- 対象チップの状態（完了/取得中/待機、UDL `target_status`）、経過時間と完了見込み（UDL `estimate_completion`）。バー: 待機中は非表示、人数 0 完了までは不定、以降は完了人数分。
- 進捗帯は閉 84px / 開 140px、ダブルクリックまたは「クリックで詳細を開く」で開閉（UD:1075、`PROGRESS_H_*`）。
- 完了「完了しました。」、失敗「失敗しました: …」、開始拒否「開始できませんでした。設定を確認してください。」（UD:1253-1265）。

### 2.6 エラー処理

| 事象 | 元の動作 |
|---|---|
| 作品単位の失敗 | `failed_queue` へ記録し次の作品へ（`core.py:1753-1757`） |
| 品質チェック異常 | 同上、作品行は未更新 |
| 作者単位の例外（一覧取得失敗等） | ログして次の作者へ。**`last_downloaded` は更新しない** |
| 未認証・一覧が見えない | 削除検知だけスキップ、保存は続行（`core.py:1237-1247`） |
| 実物が 1 つも無い作者 | 警告して落とし直さない（`core.py:1457-1470`） |
| ZIP 破損 | `_verify_zip_and_notify` でトースト（`core.py:99`） |

### 2.7 レート制限・待機

- 1 リクエストごと: `download_interval`（既定 0.6 秒）の ±20%（`pixiv_client.py:203-225, 694-703`）。共有ゲート `rate_coordinator` が複数処理の発行を一本化（`pixiv_client.py:700-704`）。
- 429: `api_retry_wait`（既定 5 秒）待って再試行、`api_retry_count`（既定 3）まで。それ以外は 2^n＋乱数の指数待機（`pixiv_client.py:751-760`）。
- 作者の間: 4.0〜6.0 秒（`core.py:2269`）。
- 元は 429 でも**止まらず**待って続行する。

### 2.8 自動監視（☆）

- ☆は `following_users.is_favorite`（`database.py:1866-1872`）。UD の自動監視カードは「☆の人数」と「☆を対象にする」（UD:969-980, 1513-1520）。
- 定期チェック（`scheduler.py:182` `_run_check`）は `get_favorite_users()` の全員を `run_batch_backup(is_full=False, target_type="both")` へ渡す。**上限なし**で、`last_downloaded` も更新される（同じ関数のため）。

## 3. 移植版の現状と、元との差

| # | 項目 | 元 | 移植版（根拠） | 差 |
|---|---|---|---|---|
| 1 | 実行単位 | 実行 1 回で対象の全作者 | 作者ごとに「差分を確認」→作品を選ぶ→「キューへ追加」の 2 段（`ui/managed_download_view.py:859-1010`）。作者の一括実行ボタンは無い | **大** |
| 2 | ジョブ | 1 実行＝1 バッチ処理 | 1 作品＝1 ジョブ（`kind='pixiv-illust'`、`managed_downloads.py:709-`）。直列 1 本（`job_service.py:588-`）。`max_jobs=50`（`job_service.py:328`）、操作 ID 台帳は 10000 件で満杯（`job_service.py:28, 531`） | 大 |
| 3 | 1 回の件数上限 | なし | 画面の選択は 128 件（`managed_download_view.py:939`、`services.py:1677, 1691`）。定期は作品 50 件・作者 128 件（`managed_scheduler.py:560-`。残りは `deferred_work` に持ち越し） | 大 |
| 4 | 作者単位の完了と `last_downloaded` | 作者が終わるたびに更新 | 書く部品が無い。`following_authors` に列はあるが書き手が無い（`following_sync.py:165-181`、`services.py` に書き込み 0 件） | **大** |
| 5 | 差分判定 | `update_date` 比較、実物確認 | `decide_difference`（`author_download.py:67-106`）。日付が無ければ UNKNOWN（元は更新扱い）。保存済みは管理領域の受領記録と旧 DB の読み取り（`legacy_author_sync.py`、`author_media.py:226-`） | 差（意図した保守側） |
| 6 | 削除検知 | `works.missing_streak`、3 回/5 回 | 側台帳 `.pixivvault-legacy-author-sync.sqlite3` に `scan_id` ごとに記録、閾値は同じ 3/5（`legacy_author_sync.py:429-445`）。ただし「作者全件の確認」を手動で 1 作者ずつ（`managed_download_view.py:810-857`）。旧 DB が無いと動かない | 中 |
| 7 | 保存先 | `Images\作者(uid)` | 管理領域（`managed_downloads.py`）。元の配置への投影は別ジョブ `author-projection`（`save_exports.py:154, 284`）、ZIP は `auto-zip`（`:160, 359`） | 設計差（維持） |
| 8 | 旧 DB | 読み書き | 凍結コピーを読み取り専用（`immutable=1`、`legacy_author_sync.py:232`）。署名が変わると fail closed（`legacy_author_sync.py:211-223`） | 設計差 |
| 9 | バッチ全体の一時停止・停止 | ボタン 2 つ | ジョブ単位のみ（`ui/job_views.py:24-27, 340`） | 中 |
| 10 | 停止後の再開 | なし（差分で再実行） | ジョブは再起動をまたいで残る（`job_service.py` の永続ストア）。バッチの概念が無い | 上位互換の余地 |
| 11 | 進捗パネル | 作者・作品・経過・ETA・件数 | 件数サマリーのみ（`ui/download_status_details.py:45-95`）。「個別ジョブの進捗は『キュー』タブで」 | 中 |
| 12 | 画面構成 | 2 カラム（左 776 / 右 460） | 1 カラムの縦積み（`verify\shell_20261117_final\1280x800_light.png`）。ボタン文言も別（「作者を選択」「選択作者の差分を確認」「旧DBと照合して欠落を確認」） | **大** |
| 13 | ☆の保存先 | `following_users.is_favorite` 1 か所 | 3 か所: 旧 DB の `is_favorite`（凍結コピー、読むだけ）、`following_authors.is_favorite`（書き手なし、`following_sync.py:178`）、`managed_scheduler.favourite_authors`（128 件上限、`managed_scheduler.py:116, 473-531`）。作者一覧の ☆ 表示は管理領域の定期設定が優先で、無ければ不明（`services.py:1969, 1984`） | 大（FL-D05, FL-O01） |
| 14 | 通信の安全 | 429 で待って続行 | 401/403/429 で**持続的に停止**（`managed_network_stop_gate.py:36-60`）、ジョブ受け付け前に確認（`managed_network_stop_integration.py:73-94`）。リトライ・待機は `request_policy.py:81-187`（間隔 0.3〜1.5 秒、試行 1/3/5/10、429 待機 3/5/10/30 秒） | 設計差（D7、維持） |
| 15 | ダウンロード優先 | 後埋めはダウンロード中は走らない（`scheduler.py` `_download_is_running`） | 同等。「未完了ジョブが 1 件でもあれば待つ」（`services.py:2956, 2976-2977, 3069`）。**一覧取得中・作者の待ちはジョブが 0 件になりうる**ので、バッチ用の旗が要る | 中 |
| 16 | ブックマーク | `download_bookmarks` 全件 | ブックマークの一覧を取得し、選んでキューへ（`services.py:1623-1636`、`bookmark_remote_adapter.py:22`） | 中 |
| 17 | 停止の表示 | 例外経由で「失敗しました: …中止」（コード読み） | ジョブ単位の「停止」状態（`job_views.py:17`） | 移植版を正とする |

## 4. 「元に合わせる」の実現案

### 4.1 共通の土台（どの案でも必要）

管理領域に**実行台帳**（`batch-runs.sqlite3`）を新設する。`following_sync.py`・`managed_scheduler.py` と同じ堅さ（マーカーファイル、リンク拒否、排他ロック、`BEGIN IMMEDIATE`、`synchronous=FULL`）で作る。作者ごとの完了・件数・失敗・`last_downloaded` の**正**はここ（または `following_authors.last_downloaded`）。旧 DB が無い構成でも成立する。

### 4.2 案 A: 管理領域だけが正、旧 DB には書かない

- 内容: `following_authors.last_downloaded` と実行台帳を更新。作者一覧の表示は「台帳の値、無ければ凍結コピーの値」。
- 長所: 既存の設計（D-00「旧 DB は変更しない」、凍結コピーの署名ゲート）を壊さない。原本 DB にデータ破壊が及ばない。元アプリとの二重書き込みが起きない。
- 短所: **「旧 DB も更新する」を文字どおりには満たさない**。元アプリを併用すると、移植版で取得済みの作者の `last_downloaded` も `works` 行も元に見えず、元アプリが同じ作品を再取得する。
- リスク: 併用時の重複取得（容量・通信）。データ破壊は無い。

### 4.3 案 B: 旧 DB に直接書く

- 内容: 凍結コピー（または原本）を書き込み可能で開き、元の `update_following_last_downloaded`・`upsert_work`・`add_failed_job` 相当を実行する。
- 長所: 文字どおりの「元に合わせる」。
- 短所とリスク:
  1. **凍結コピーに書いても元アプリには届かない**。意味が出るのは原本 DB に書くときだけ。
  2. 原本は WAL で、元アプリが起動中なら同時書き込みになる。元の定期チェック（`scheduler.py:182`）と同じ作者を同時に処理しうる（元の作者ロックはプロセス内のみ、`core.py:80`）。
  3. 凍結コピーの署名ゲートが壊れる: `immutable_copy_signature`（`failed_records_repository.py:34`）、`LegacyAuthorBridge._check_source`（`legacy_author_sync.py:211`）、`_legacy_managed_capabilities`（`services.py:28`）、`frozen_source_pin.py`（Windows では排他読み取りハンドルで書き込み自体が拒否される）、`discovery_seed.py:28`、`learning_service.py:167`、`recommendation_state.py:121, 184`、`managed_network_stop_history_readonly.py:147`、`duplicate_inspection.py:146`。
  4. `works` 行を書くなら、元が見る場所（`Images\作者(uid)` または ZIP）に実物が必要。無いと元アプリが「実物なし」と判定して再取得するか（`core.py:1471-1486`）、「この作者の実物が1つも見つかりません」警告を出す（`core.py:1457-1470`）。`author-projection`（`save_exports.py:154`）との結合が要る。
  5. 元のスキーマ変更に追随する必要がある。書き込みは取り返しがつかない。
- 結論: 単独では採らない。

### 4.4 案 C（推奨）: 台帳を正にし、旧 DB へは「反映器」が冪等に書く

- **段階 1（必須）**: 案 A の全部。さらに、台帳に「旧 DB へ反映すべき変更」の**意図ログ**（`writeback_journal`: 連番、種別、キー、値、反映済み時刻）を最初から残す。
- **段階 2（本人の確認後）**: 反映器（別部品 `legacy_writeback.py`）が、**原本 DB** を対象に、意図ログを冪等に適用する。反映の範囲は 2 段に分ける。
  - W1: `following_users.last_downloaded` のみ（1 列、リスク小）。
  - W2: `works`・`failed_queue`（高リスク。`author-projection` で実物が元の配置にあると確認できた作品に限る）。**v1 では W2 を作らない**（確認 2）。
- 長所: 移植版の動作は旧 DB の有無・状態に左右されない。旧 DB への書き込みは、失敗しても台帳に残り、後で再適用できる。書き込み範囲を列単位・表単位で絞れる。段階 1 だけで UI・実行モデルは完成する。
- 短所: 部品が 1 つ増える。凍結コピーを読む部分は、反映後に古くなる（本人が凍結コピーを撮り直すまで）。台帳の値を優先して重ねる表示規則が要る。
- リスクと対処:

| リスク | 対処 |
|---|---|
| データ破壊 | 書き込み前に SQLite のオンラインバックアップ（backup API）を管理領域へ。スキーマの許可リスト検査（列が無ければ書かない）。1 反映＝1 トランザクション、反映後に `PRAGMA integrity_check`。失敗なら戻す |
| 二重書き込み | 元アプリの起動を検出（`BEGIN IMMEDIATE`＋短い `busy_timeout`、`-wal` の排他確認）。取れなければ**反映しない**（台帳に残して次回） |
| 元アプリとの併用 | W1 だけなら、元が見る `last_downloaded` が進むだけ（元の差分判定は `works` を見るので影響なし）。W2 は §4.3 の 4 を満たすときだけ |
| 反映器の暴走 | 既定は OFF。確認 1 の回答で有効化。1 回の反映件数に上限、反映ごとに記録 |

### 4.5 案 D（採らない）: 管理領域を元の `Images`・原本 DB の位置へ移す

保存先・DB を元と同一にする案。D-00（専用管理フォルダー、元は読み取り専用）を壊し、凍結コピーの署名ゲートも全面的に壊す。データ破壊の影響範囲が最大。却下。

### 4.6 推奨とその理由

**案 C を推奨。段階 1（WU-1〜WU-8）を先に作り、段階 2（WU-9）は確認 1 の回答後。**

- 元の画面と実行の流れ、`last_downloaded` の表示・並べ替え・更新は、段階 1 だけで元と同じになる。
- 「旧 DB も更新」は、原本 DB への書き込みが意味を持つ唯一の形（案 B の 1）を、案 B のリスク（2〜5）を絞った形で提供できる。
- 本人が「原本 DB に書いてよい」を選ばなければ、案 A として完結する。やり直しが要らない。
- 原本 DB への書き込みは、CLAUDE.md の「聞く」条件（実データへの不可逆操作）に当たるため、確認 1 にする。

## 5. 実行エンジンの設計

### 5.1 構成

```
UnifiedDownloadView(新設) -> services.batch_runs (新設 BatchRunEngine)
   |-- 実行台帳 batch-runs.sqlite3 (新設)
   |-- 作者ごとの全件分類: author_media.preview(new_only=False) (既存)
   |-- 作品ごとの保存: services.enqueue_managed_download / author_media.enqueue (既存) -> JobService (既存)
   |-- 削除観察: legacy_author_bridge.observe_author(scan_id=...) (既存)
   |-- last_downloaded: following_sync に書き手を新設
   `-- 旧 DB 反映器 legacy_writeback (WU-9、任意)
```

- **エンジンは JobService のジョブにしない**。JobService は直列 1 本（`job_service.py:588-`）なので、ジョブの中で作品ジョブの完了を待つと詰まる。エンジンは専用スレッド（`ManagedScheduleRunner` と同じ型、`managed_scheduler.py:889-`）で、作品ジョブを**少しずつ**積み、完了を台帳に記録する。
- 作品の保存は既存経路をそのまま使う（`services.py:2083` `enqueue_managed_download`、`author_media.py:367` `enqueue`）。保存形式・受領記録・品質検査・ZIP 追記（`save_exports.py`）は変えない。
- 基礎にする既存部品は `author_media.preview`（`author_media.py:226`）。理由: イラスト・うごイラ・小説の全媒体、作者別の対象種別、旧 DB の読み取りを既に含む。`fetch_author_differences`（`managed_download_view.py:859`）はイラスト中心でビューに結合しており、再利用しにくい。

### 5.2 実行台帳（新設）

- `runs(run_id, kind['authors'|'bookmarks'], origin['manual'|'auto'], target_type, state, started_utc, finished_utc, authors_total, revision, error_code)`。`state`: `running` / `paused` / `stopping` / `stopped` / `done` / `failed` / `interrupted`。
- `run_authors(run_id, seq, author_id, state, n_new, n_updated, n_restored, n_missing, n_skipped, n_failed, n_absent_pending, n_absent_confirmed, started_utc, finished_utc, error_code)`。`state`: `pending` / `listing` / `downloading` / `done` / `failed` / `stopped` / `skipped`。
- `run_items(run_id, author_id, work_id, media, operation_id, job_id, state)`: 再起動後に「積んだ・積んでいない」を判定する（`dispatch_items` と同じ役割、`managed_scheduler.py:111`）。
- `writeback_journal(seq, run_id, kind, key, payload, applied_utc)`: 案 C の意図ログ（段階 1 から書く）。
- 作者の完了時刻は `following_authors.last_downloaded` へ。`following_sync.py` に `record_downloaded(account_id, user_id, at_iso)` を新設する（`record_followed`（`:417`）と同じ CAS）。

### 5.3 作者ごとの状態遷移と `last_downloaded` の規則

1. `listing`: 取得停止ゲートを確認（`managed_network_stop_integration.py:90`）。実行の最初に 1 回 `verify_identity`（FL-Z03、元 `core.py:435` に相当）。全件一覧を取得（`author_media.preview`、`new_only=False`＝元と同じ全件）。
2. 削除観察: 一覧が完全で、旧 DB の橋があるとき `observe_author(uid, works, scan_id=新規UUID)`（元のフェーズ B。閾値 3/5 は既存）。橋が無いとき・一覧が不完全なときは**削除検知を飛ばして続行**（元 `core.py:1237-1247` と同じ）。画面に「削除検知は未実施」と出す。
3. `downloading`: 新規・更新と、既存規則で許される復帰・実物欠損（`managed_download_view.py:914-921` の `rehydrate`）を、少しずつジョブへ積む。UNKNOWN は積まず「判定保留 N 件」に数える（意図した保守側の差、§3-5）。
4. 完了判定: その作者の積んだジョブが全部終端（done/error/stopped）になったら `done`。作品の失敗は件数に入れるだけで、作者は `done`（元と同じ、`core.py:2254`）。
5. **`last_downloaded` を書く条件**: 作者が `done`（一覧取得に成功し、停止要求が無かった）のとき。一覧取得の失敗・停止・取得停止ゲートでは書かない。フォローしていない作者（ID 指定）は台帳の `run_authors` にだけ残し、`following_authors` へは書かない（元の `UPDATE` 0 行と同じ）。
6. 作者の間の待ち: `random.uniform(4.0, 6.0)` 秒（元 `core.py:2269` と同じ値）。**停止・一時停止で即座に切り上げる**（元は切り上げられない。移植版の改善）。最後の作者の後は待たない。

### 5.4 停止・一時停止・再開

| 操作 | 動作 |
|---|---|
| 一時停止 | 実行状態を `paused`（台帳に永続）。エンジンは次の作者・次のジョブを積まない。積み済みの待機中ジョブは `JobService.control('pause')`。再開で `resume` |
| 停止 | 即座に画面を「停止しています（いまの作者が終わるまで待っています）…」へ（元の文言を維持、UD:1070）。待機中ジョブを `stop`、実行中は次のチェックポイント（`job_service.py:274`）で止まる。実行を `stopped`。`last_downloaded` は書かない。最終表示は「停止しました。」 |
| 停止後 | 元と同じく再実行で差分から続く。加えて**再開**（上位互換）: `interrupted`/`stopped` の実行を、完了済みの作者を飛ばして続ける |
| アプリ再起動 | 実行は `interrupted`。画面に「前回は途中で止まりました。再開／破棄」。自動では再開しない（通信を勝手に始めない） |

### 5.5 取得停止ゲートの途中閉鎖

実行中にゲートが閉じた（401/403/429）場合、実行は `paused`（理由 `network_stopped`）にし、**失敗にしない**。ジョブ側は既存の受け付け前確認で `admission_blocked` の一時停止になる（`job_service.py:620-640`）。復旧は既存の復旧画面（`ui/stop_recovery_view.py`）。復旧後に「再開」。元は 429 で待って続行するが、移植版は D7 を優先する（§3-14）。

### 5.6 規模の上限と対処

| 制約 | 内容 | 対処 |
|---|---|---|
| ジョブ数 | `max_jobs=50`、満杯なら終端ジョブが押し出される（`job_service.py:534-541`） | 同時に積む未完了ジョブは**最大 16**（既定、設定値）。完了のたびに補充 |
| 操作 ID 台帳 | 10000 件で満杯（`job_service.py:28, 531`）。初回の全件取得（数万作品）で超える恐れ | **未解決の技術リスク（R1）**。WU-3 の最初の作業で検証。対策案: 終端かつ実行台帳に結果を記録済みの操作 ID を解放する `JobService` の口を新設。解決できなければ確認 5 |
| 選択 128 件 | `remember_managed_author_batch`（`services.py:1691`） | 小さな束で認可（`submit_author_batch` と同じ、`managed_scheduler.py:560-`） |
| 作者数 | 元は上限なし。定期は 128（`managed_scheduler.py:561`） | エンジンは作者数に上限を持たない。定期もエンジン経由にして 128 の制限を外す（§5.8） |

### 5.7 マイブックマークモード

- 元（`core.py:2720`）: 公開/非公開を `show`→`hide` の順に全件取得し、新規・更新・復帰・実物なしを保存。**`last_downloaded` は更新しない**。進捗は「公開ブックマーク: c / t 件」（UD:1320-1341）。
- 移植版: 実行台帳の `kind='bookmarks'`。取得は既存 `bookmark_remote_adapter`（`VISIBILITIES=("show","hide")`、`:22`）。選択した公開/非公開だけを対象に、未保存を全件キューへ（既存の保存経路）。階層化/フラット（`bookmark_download_mode`、`preferences.py:32`）をタブ内トグルにする。`last_downloaded` は書かない。

### 5.8 自動監視（☆）の統一（FL-D05, FL-O01）

- **正は 1 か所**: `following_authors.is_favorite`（管理領域、`following_sync.py:178`）。書き手（`set_favorite`）を新設する。
- 初回の種まき: 旧 DB の `is_favorite`（凍結コピーが読めるとき）と、`managed_scheduler.favourite_authors` の和集合を 1 回だけ取り込み、台帳に記録する。以後 `managed_scheduler.favourites()` は `following_authors` を読む。既存の「お気に入り作者 ID を手入力（最大 128 件）」UI（`ui/scheduler_view.py:29-50`）は、☆の一覧表示へ置き換える。
- 定期チェックはエンジンの `origin='auto'` で動かす（元 `scheduler.py:182` と同じ: ☆全員・差分・両方）。`last_downloaded` も更新される。
- ☆の変更は旧 DB へ書かない（W1 の対象外。許可するなら確認 1 の (b) で列を追加する）。

## 6. 画面の復元仕様

### 6.1 元の画面との対応表

寸法・文言は `verify\original_20261117_225445\{1280x800,1920x1080}_{light,dark}_download.{png,txt}` と UD から。内容幅は `LEFT_W 776 + 10 + RIGHT_W 460 = 1246px`。固定幅の内側コンテナを上中央寄せ（`gui.py:4984-5003`）。1920 でも左右均等に余る。

| # | 元の部品（1280×800 の座標、UD の場所） | 内容 | 移植版の扱い |
|---|---|---|---|
| 1 | 見出し「ダウンロード」＋小さな説明「作者を選ぶか、IDを入れて実行します」（21,331 / 149,336） | | 新設（見出しは既存 `.pv-library-heading` を流用） |
| 2 | モード切替 2 ボタン「フォロー中の作者」「マイブックマーク」各 369×42（UD:1386-1420） | 選択中は青 | 既存 `author_mode_button`/`bookmark_mode_button`（`managed_download_view.py:83-91`、`_mode_event` `:231`）の意味を移し、見た目を元に |
| 3 | 絞り込み入力 300×42「絞り込み（数字はID指定）」（UD:395-418） | 数字のみ→ID 指定行 | 新設。判定は `author_download.filter_authors`（`:44-48`）を拡張（数字のみは ID 指定、全角は NFKC） |
| 4 | 道具行: 「表示中を全部選ぶ」「選択を全部外す」「☆のみ」＋並び替えメニュー（5 択）＋昇降順（UD:907-1010、UDL:51-57） | 5 択: フォロー登録順・作者名順・保存済みの多い順・最後に取得した日・☆を先に | 既存 `_select_visible_follow`/`_clear_follow_selection`/`_toggle_follow_stars`/`_set_follow_sort`（`managed_download_view.py:634-703`）を流用。並び替えを 3 択→5 択へ（FL-D03）。`sort_authors` を `author_download.py` へ移植 |
| 5 | 件数「0 / 0 人」（34,534） | 表示数/全体 | 新設 |
| 6 | 作者表: チェック列・☆・作者名・ID・保存済み・最後に取得した日。ヘッダーのチェックは三態（UD:918-955）。50 人ずつの「前へ／次へ」（UD:816-826） | 保存済み＝作品数、最後に取得した日＝`last_downloaded`（無ければ「－」、UDL `format_last_downloaded`） | 表は新設（行を自前描画して高さを固定）。`last_downloaded` は `following_authors` から。保存済み件数は管理領域の受領記録の作者別集計（新設 `count_saved_by_author`）。移植版の「もっと見る」（`managed_download_view.py:765`）は 50 人ページ送りへ戻す |
| 7 | 右上カード「この回の対象 N 人」（820,392）＋チップ（✕で外す、状態: 完了/取得中/待機、由来 id/follow）（UD:573-660） | | 既存 `_render_author_targets`（`managed_download_view.py:547`）のチップを流用。状態表示を追加 |
| 8 | ☆自動監視カード「自動監視 N 人」＋「☆を対象にする」（836,601 / 1101,593、UD:969-980, 1513-1531） | | 既存 `_select_favorite_follow`（`:686`）を流用。件数は §5.8 の正から |
| 9 | 実行行: 種別トグル 44×44、「実行」（高さ 44・伸びる）、一時停止 44×44、停止 44×44（803-1263,644、UD:1343-1368） | 対象 0 人なら「左の一覧で作者を選ぶと押せます」 | 新設。種別トグルは循環ボタン（イラスト・漫画→小説→両方、押下で 120ms 沈む）。現状の select（`author_media_view.py:33`）は詳細側へ残す |
| 10 | 進捗帯（全幅、閉 84px / 開 140px）: 見出し（待機中/N / M 人目…）、バー、補助行「差分DL · 両方 · 保存先 Images」「クリックで詳細を開く」、開くと「この回の記録」の数値ボックス（開始・現在・件数・失敗・経過・見込み）（UD:206-330, 1075-1100） | ダブルクリックでも開閉。body が 240px を下回ったら自動で閉じる | 既存 `download_status_details.py` の開閉（`:55-84`）を拡張。保存先表示は「専用管理フォルダー（元のDB・画像は変更しません）」から元の書式「保存先 …」へ。詳細に実行台帳の値を出す |
| 11 | マイブックマーク面: 「含めるブックマーク」公開/非公開のチェック、「保存方式」階層化/フラット、「未フォロー作者を確認」（UD:1423-1480） | | 既存 `unfollowed_authors_view.py`（`open_unfollowed`、`managed_download_view.py:224`）を流用。チェックとトグルは新設（FL-D11, D12） |
| 12 | 配色・形: カード surface/border/radius12、青 `#0096FA`、右カラム内は縦の線を揃える（UD `T`、`:51-65`） | | `ui/theme.py` のトークンは同じ値（`theme.py:3-12`）。ライトは本人裁定で明るいカード（元の暗色カードは写さない） |

### 6.2 既存部品の扱い

- `ManagedDownloadView`（1185 行）とそのテスト群（`tests\test_managed_download_view.py` ほか）は**壊さない**。作品単位の確認・選択（「IDから作品を確認」「選択作者の差分を確認」「旧DBと照合して欠落を確認」、`author_media_view.py`）は、新しい `UnifiedDownloadView` の**進捗帯の詳細を開いた中**へ「作品単位で選ぶ（詳細）」として移す（閉じている間は元と同じ見た目。確認 4）。
- `fetch_author_differences` の分類・認可ロジックは触らない（エンジンは `author_media.preview` 側を使う）。
- 新設: `ui/unified_download_view.py`（左右カラム、表、チップ、実行行）、`unified_download_state.py`（UI から切り離した判断: ID 指定判定、ソート、対象の積み方、チップ状態、ETA。元 UDL と同じ分け方でテスト可能にする）。UDL の `estimate_completion`・`target_status` は挙動を変えずに移植する。
- 固定寸法: 1280×800 と 1920×1080 の両方で、内側幅 1246px を上中央に置く。表の高さは body の余りを使う。

## 7. 取得停止ゲート・ダウンロード優先・予算との整合

| 項目 | 設計 |
|---|---|
| 取得停止ゲート | エンジンは自分では通信しない。一覧・詳細・ダウンロードは既存の読み取りクライアント（ゲート経由）。各作者の開始前と各ジョブを積む前に `require_allowed()`（`managed_network_stop_integration.py:90`）。ジョブは受け付け前確認（`:73`）。ゲートが閉じたら実行は `paused(network_stopped)`、失敗にしない。網羅検査（D7）に「バッチ実行が独自に通信しない」試験を追加 |
| ダウンロード優先 | 後埋め・類似度の自動処理は、未完了ジョブがあれば譲る（`services.py:2956, 2976-2977, 3069`）。**バッチが走っている間は、ジョブが 0 件の瞬間（一覧取得中、作者の待ち）も「ダウンロード中」とする**: `services.batch_run_active()` を新設し、上の 3 か所と `metadata_runner.py:50-61`、`bootstrap.py:691` の自動ループの判定に OR で足す。逆に、後埋めの 1 回の GET が走っているとき（`_managed_enqueue_lock` を保持、`services.py:2950`）は、エンジンの積み込みも同じ鍵で待つ |
| 予算 | 本リポジトリに「予算」という語を使った設計は見つからなかった（`PV_DEV_PLAN`、STATUS、docs を grep、0 件）。次のように解釈した。(1) 通信の間隔と再試行（`request_policy.py:81-187`、既存。エンジンは独自に待たず、作者の間の 4〜6 秒だけを元どおり持つ）。(2) 借用アカウントの要求予算（`premium_request_budget.py`、`PremiumRequestLimit.retry_at`）。上限に達したら実行を `paused` にして `retry_at` まで待つ（失敗にしない）。(3) 1 回の実行の通信量の上限は**置かない**（元に上限が無く、完全 DL の確認ダイアログも 08-11 に撤去済み、`unified_download.py:17-24`）。解釈違いなら確認 6 |
| 自動実行 | 定期チェック（☆）は設定で止められる（既定は自動）。エンジンの `origin='auto'` は同じゲート・同じ優先を通る。手動の実行中は自動を始めない |

## 8. 作業の分割

1 つが Sonnet 1 本で終わる大きさ。作業ツリーは 1 ツリー 1 書き手（同時実装は 2 本まで）。

```
WU-1 台帳・DB層 --+-- WU-2 エンジン本体 -- WU-3 ジョブ積み込みと上限 --+-- WU-5 停止・一時停止・再開・ゲート
                  |                                                   `-- WU-6 優先・自動監視の統一
WU-4 画面の状態部品(独立) ------------------------------- WU-7 画面 -- WU-8 進捗帯・ブックマーク面
WU-9 旧DB反映器 (WU-1後、確認1の回答後)               WU-10 互換・総合試験 (全部の後)
```

依存: WU-2←WU-1、WU-3←WU-2、WU-5←WU-3、WU-6←WU-3、WU-7←WU-4+WU-2、WU-8←WU-7+WU-5、WU-9←WU-1+確認 1、WU-10←全部。

| WU | 内容 | 主な触るファイル | red → green の試験案 |
|---|---|---|---|
| WU-1 | 実行台帳（`batch_runs.py`）と `following_sync` の書き手（`record_downloaded`、`set_favorite`）、保存済み件数の作者別集計、意図ログ | 新 `batch_runs.py`、`following_sync.py` | red: 作者が `done` で `last_downloaded` が入る／例外・停止では入らない／フォロー外は何もしない／再同期（`sync`）で `last_downloaded`・`is_favorite`・`is_zipped` が保たれる／再起動で台帳が残る／二重オープン拒否。green: 上が通る |
| WU-2 | エンジン本体（スレッド、作者の状態遷移、作者の間の待ち 4〜6 秒を乱数・停止即応で、統計）。ジョブ積みは差し替え可能な口（偽）で試す | 新 `batch_engine.py` | red: 偽クライアントで 3 作者実行 → 順に処理、作者ごとに `done`、待ちは 4〜6 秒（時計を差し替え）、最後の後は待たない、一覧失敗の作者は次へ進み `last_downloaded` 無し、作品失敗の作者は `done` |
| WU-3 | ジョブ積み込み（最大 16 同時、補充、`author_media.preview` と既存 enqueue の利用、UNKNOWN は保留）と**操作 ID 台帳 10000 の検証・対策（R1）** | `batch_engine.py`、`job_service.py`（解放口） | red: 12,000 作品の積み込みで「job operation history is full」にならない／同時未完了が 16 を超えない／128 件の認可上限に当たらない。green: 解放口 or 別方式 |
| WU-4 | 画面の状態部品（ID 指定判定、5 択ソート、対象の積み方、チップ状態、ETA、`format_last_downloaded`） | 新 `unified_download_state.py`、`author_download.py` | red: 元 UDL の入出力を移植版で再現（同じ入力で同じ出力）。ソート 5 択×昇降、数字のみ=ID、全角数字、絞り込みが対象を消さない、二重に積まない |
| WU-5 | 停止・一時停止・再開、再起動後の `interrupted`、取得停止ゲートでの `paused(network_stopped)`、「停止しました。」 | `batch_engine.py`、サービス層 | red: 停止で `last_downloaded` が書かれない／停止後に待機中ジョブが止まる／待ちの最中の停止が即応する／ゲートが閉じても失敗にならず `paused`／再開で完了済み作者を飛ばす／再起動で `interrupted` |
| WU-6 | `batch_run_active` と優先の接続、☆の単一化（種まき、`managed_scheduler.favourites()` を台帳から、定期をエンジンへ、128 件上限の撤廃） | `services.py`、`managed_scheduler.py`、`metadata_runner.py`、`ui/scheduler_view.py` | red: バッチ中に後埋めが走らない（作者の待ちの最中も）／☆200 人の定期が全員処理される／旧 DB と定期設定の和集合を 1 回だけ取り込む／☆変更が定期へ即反映 |
| WU-7 | 画面（`UnifiedDownloadView`: 左右カラム、モード切替、絞り込み、道具行、表、チップ、☆カード、実行行、50 人ページ送り）と「ダウンロード」タブの差し替え | 新 `ui/unified_download_view.py`、`ui/shell.py` | red: Playwright 画面スモーク（1280×800、1920×1080、ライト・ダーク）で、§6.1 の座標・幅が許容差内。実行→進捗→完了、押せない状態の文言。既存の `test_managed_download_view.py` が緑のまま |
| WU-8 | 進捗帯（84/140 開閉、ダブルクリック、作者・作品・件数・経過・見込み）、マイブックマーク面（公開/非公開、階層/フラット、未フォロー作者）、作品単位 UI の移設 | `ui/download_status_details.py`、`ui/unified_download_view.py` | red: 対象 1 人で ETA が出る（フォールバック）、body 240px 未満で自動で閉じる、ブックマーク実行が `last_downloaded` を更新しない、階層/フラットが設定に反映 |
| WU-9 | 旧 DB 反映器 W1（`last_downloaded` のみ）。確認 1 が「原本へ書く」のときだけ | 新 `legacy_writeback.py` | red: **複製だけで**: 書き込み前のバックアップ、`integrity_check`、元アプリ起動中（ロック）なら反映せず台帳に残す、列が無い DB では書かない、冪等（2 回適用しても同じ）、失敗でロールバック、他の列・他の表が 1 バイトも変わらない（ハッシュ照合） |
| WU-10 | 互換・総合試験: 元の `run_batch_backup` と移植版エンジンを同じ偽 Pixiv に当てて結果を比べる（オラクル）、台帳 FL-D00〜D17 の状態更新 | `tests`、`tools` | §9 |

規模の見積り: WU-1・WU-4 は小、WU-2・WU-3・WU-5・WU-6・WU-9・WU-10 は中、WU-7・WU-8 は大（画面）。

## 9. 試験の方針（互換と隔離）

### 9.1 オラクル試験（元と移植版で同じ入力 → 同じ判定）

- 同じ偽 Pixiv（作者 3 人、作品の新規・更新・削除・復帰・失敗を含む）を、(a) 元の `core.run_batch_backup`（`original_copy` の複製と `pv_iso.db` の複製に、偽クライアントを差す）、(b) 移植版エンジンに当てる。比べるもの: 作者ごとの件数（新規・更新・スキップ・失敗）、`last_downloaded` が入る作者の集合、削除確定の回、停止時に入らないこと。
- 元の実行は**複製のみ**で行う（実機のプロファイルと `original_copy` 本体には触れない）。

### 9.2 実データ・不可逆操作の隔離計画

| 操作 | 先に行う場所 | 実機へ進む条件 |
|---|---|---|
| 台帳（`batch-runs.sqlite3`、`following.sqlite3`）の書き込み | 隔離データ（新規の管理領域、試験用 `--profile-root`）、`launch_test_owner.py` の試験領域（`docs\TEST_OWNER_LAUNCH.md`） | 前後の整合チェックとハッシュ照合 |
| 取得停止ゲートの実発火 | ローカルの偽サーバー（429 を返す）。**実 Pixiv への通信を伴う試験は、本人の指示があるまで行わない** | 試験所有者の領域のみ |
| W1 反映器（原本 DB への書き込み） | `pv_iso.db` の**複製の複製**。次に、元アプリを起動した状態の複製（ロック競合）。併用の確認は複製どうしで | 本人の確認 1 の回答後、実機のバックアップを取った上で 1 作者だけで実証 → 全体 |
| 大量実行（数万作品） | 偽の管理領域と偽クライアント（ネットワークなし）で 12,000 作品 | R1 の対策確認後 |

- C ドライブには何も作らない（`TEMP`/`TMP` を S: 配下へ、全体実行の前後で C ドライブの差分検査、D7）。
- 画面は UI 自動回帰（Playwright 主要画面スモーク）の対象に「ダウンロード」を追加し、並置比較を `verify\` に残す（D2）。

## 10. 本人に確認が要る点

| # | 内容 | 選択肢（先頭が既定） |
|---|---|---|
| 1 | **「旧 DB も更新」の対象と範囲**（実データへの不可逆な書き込み） | (a) 案 C の段階 1 だけ（旧 DB へは書かない。元アプリには届かない）。(b) 段階 2 W1: 原本 DB の `following_users.last_downloaded` だけを、バックアップ・排他・冪等つきで更新（元アプリ非起動のときだけ）。(c) W1 に加え W2（`works`/`failed_queue`、実物が元の配置にある作品に限る）。**設計者の推奨は (a) で出荷し、(b) を回答後に追加。(c) は元アプリ併用の運用が要るときだけ。** 裁定の文言を文字どおり満たすのは (b) 以上 |
| 2 | 元アプリを今後も併用するか。併用するなら、移植版で保存した作品を元に認識させたいか | (a) 併用しない／しても重複取得は許容。(b) 併用し、元の配置へ書き出して認識させたい（W2 と投影が必要） |
| 3 | 日付が無い作品（UNKNOWN）の扱い。元は「更新」として落とし直す。移植版は保守側で保留 | (a) 保留して件数だけ表示（既定）。(b) 元どおり更新扱い（全件取得になりうる） |
| 4 | 作品単位の確認 UI（現在の「IDから作品を確認」等）の置き場所。元の画面には無い | (a) 進捗帯の詳細を開いた中（閉じている間は元の見た目、既定）。(b) 別ダイアログ。(c) 廃止 |
| 5 | 操作 ID 台帳が満杯になる問題（R1）が構造変更を要するとき、`JobService` の拡張を許してよいか | (a) 許す（既定）。(b) 台帳側で作品ジョブを束ねる別方式 |
| 6 | 「予算」の解釈（§7）。通信の間隔・借用の要求予算として扱い、1 回の実行に通信量の上限は置かない | (a) この解釈で進める（既定）。(b) 別の意味があれば指定 |

## 11. 未確認・注意

- 停止が「失敗しました」と出る件（§2.4）は、コード読みの結論。実機の元アプリでは未確認。
- `following_sync.sync()` が再同期時に `last_downloaded`・`is_favorite`・`is_zipped` を保つかは、`clear_account` の注釈（`following_sync.py:400-415`）からの推定。WU-1 の red 試験で最初に確かめる。
- 元の画面のスクリーンショットは作者 0 人の状態のみ。データありの並置比較（表の行、チップ、進捗中）は WU-7/8 で隔離データを用意して撮る。
- 元の作者フォルダー判定（`core.py:1368-1372`）と、移植版の `author-projection` の配置が同一かは未検証（確認 2 の (b) を選ぶ場合は WU-9 の前に検証が要る）。
