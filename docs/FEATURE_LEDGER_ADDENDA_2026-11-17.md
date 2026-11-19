# 機能対応台帳 追記（FL-Z）2026-11-17

- 位置づけ: `FEATURE_LEDGER_2026-11-17.md`（171 項目）の追記。元リポ `S:\Projects\PixivVault` を機械的に列挙した `inventory.json` のうち、既存台帳の行では説明しきれなかった **core.py・database.py・pixiv_client.py 由来の利用者機能**を、実際に元コードを読んで行にしたもの。
- 元: `S:\Projects\PixivVault`（読取のみ）。移植版: `S:\Projects\PV-DEV-20261117\work\src\pixivvault_nicegui`（読取のみ、work の HEAD `ae443b4`）。`file:N` は読んだ箇所。
- 状態の定義・「無」の書き方は元の台帳と同じ。「無」は grep した語と 0 件であることを根拠欄に書く。
- 対応表: `ledger_map.json`（inventory 項目 → FL-ID または `n/a:理由`）。検査: `tools\check_inventory_coverage.py`。
- 読んだ深さ: 元は下記「元の場所」の範囲を読んだ。移植版は grep で入口を探し、判定に使った箇所だけ読んだ。**「有」は読んだ範囲での判断で、実機での同値確認ではない。**

## 網羅検査の読み方（`tools\check_inventory_coverage.py`）

- 未対応 0 は「全項目が何かの FL または `n/a:理由` に結ばれた」意味で、**挙動の同値の確認ではない**。対応の強さを 3 段に分けて数える。
  - tier A（狭い根拠）: 台帳の引用範囲が 150 行以内、同じ文言が台帳の 1 行にだけ出る、設定キー索引（T 節）、名前の一致、元で呼び出し元なしの関数（`do_OPTIONS` など動的に呼ばれるものは除く）、DB の表、**行範囲が 150 行以内の手規則（名前指定・`n/a` も同じ）**。
  - tier B（単位での対応）: 同じ関数内の近い項目に寄せたもの、1 つの関数全体・1 つのファイル全体を 1 つの機能と見なした手規則（**幅が 400 行以内のものだけ**）。
  - tier C（弱い）: 台帳の引用範囲が 150 行を超える、近傍（120 行以内）、**150 行を超える手規則（名前指定・`n/a` も同じ。関数・ファイル単位の規則でも 400 行を超えれば同じ）**。`--strict-weak` で未対応扱い、`--strict-all` で B も未対応扱い。**tier C が「台帳のどの行の内側かだけは分かるが、その行が項目を説明しているかは未確認」の残件**。
- 台帳の本体と、この追記（FL-Z・FL-Y）の両方を対応づけに使う。複数の FL に結ばれた項目は、全ての FL に数える。
- **未解決の差**（`--strict-gaps` で非 0 終了）: 対応づけ済みでも、対応先の台帳の状態が「無」「弱」の項目と、元の UI 文言が移植版に見つからない項目（`port_text_missing`）。対応づけは「どの機能の一部か」を示すだけで、差が無いことを示さない。逆方向の検査（引用範囲に inventory 項目が 0 件の台帳行）は警告で、`--strict-reverse` でエラーにする。
- inventory には、画面のロジックの大半を持つ関数の内側の関数（クロージャ。`gui.py` の `main_window` など）と、`filter_panel.load_json_setting`／`save_json_setting` 経由の設定キーも入る（2026-11-17 に追加）。
- 付録 A は確認の手がかりであり、判定ではない。FL-Z は core.py・database.py・pixiv_client.py 由来で読んだもの。画面側（UI 文言）の未確認は付録 A と tier C の一覧が入口で、FL-Z の行にはしていない。
- 元の `get_setting` に結ばれないキー（`exclude_hidden_authors`・`exclude_hidden_works`）は、台帳 T 節に設定キーとして載っているが、実際は ranking_tab.py:159-182 の絞り込み辞書のキー。

## 追記行

| ID | 元の機能 | 元の場所 | 移植版の場所 | 状態 | 根拠 |
|---|---|---|---|---|---|
| FL-Z01 | 保存ファイル名の規則（題名の無害化 `sanitize_filename`、`illust_save_filename`（題名・作品 ID・p 番号）、旧名 `illust_legacy_filename` との互換） | core.py:29-31, 467-523 | managed_downloads.py:814（管理領域内は `page-{n}.{ext}`）。元の配置への投影は save_exports.py:482（ジョブ `作者互換配置` = `author-projection`） | 有 | 管理領域の名前は元と違う（設計差 D-00）。元の命名で書き出す投影ジョブはあるが、命名規則が元と同じかは読んでいない（要確認） **2026-11-19 再ステータス（work 2842b6c、候補・実機未確認）**: 保存ファイル名の規則（wt-ah、tests/test_ah_filename_20261119.py・test_ah_z01_edges_20261119.py）。既知の限界は tmp/known_limits_z02.md |
| FL-Z02 | 作者フォルダの ZIP 追記・ZIP64 互換化・エントリ削除・ZIP 内の名称整合（`append_to_zip`、`normalize_zip_for_compatibility`、`remove_entry_from_zip`、`read_zip_entry_sizes`、`reconcile_author_zip_path`、`find_author_library_entry`） | core.py:123-434, 2385-2546 | save_exports.py:160, 359, 542-545（ジョブ `自動ZIP` = `auto-zip`。全体設定 `zip_all_after_download` または作者フラグで起動。542 行のコメントは元の合成規則と同じ） | 有 | ZIP 化の起動規則は元と同じ。ZIP64 互換化・既存 ZIP への追記・名称整合の同等物は `normalize_zip`/`zip64` を save_exports.py で grep 0 件（ヒットは novel_*・ugoira_* の別用途）。作者 ZIP の中身の作り方は未読 **2026-11-19 再ステータス（work 2842b6c、候補・実機未確認）**: ZIP 追記・ZIP64 互換化・エントリ削除・名称整合（wt-ah、tests/test_ah_zip_20261119.py・test_ah_zip64_20261119.py・test_ah_remove_20261119.py・test_ah_z02_update_20261119.py）。既知の限界は tmp/known_limits_z02.md |
| FL-Z03 | ダウンロード前のログイン生存確認（`check_login_alive`。`verify_login` で応答を確かめ、設定の `my_user_id` と違うアカウントなら止める。`is_authenticated` の弱い判定へ落とさない） | core.py:435-466（呼び出し 642, 1232）、pixiv_client.py:1112-1190 | managed_downloads.py:762, author_media.py:237, managed_scheduler.py:607（`verify_identity()`）、auth_status.py | 有 | 保存・作者取得・自動取得の各入口で、口座に結びついた本人確認をしてから進む。名前は違うが同じ役割（別アカウント検出の確認ダイアログ自体は FL-A09 で無） |
| FL-Z04 | 通信完了時に Cookie を `cookies.txt` へ自動で延長保存（`_save_cookies_if_changed`、セッション Cookie を書き戻す） | pixiv_client.py:270-343, 725-735 | 無（`_save_cookies_if_changed`・`save_cookies`・`cookie.*refresh` を非 legacy の src で grep 0 件。`MozillaCookieJar` は auth_status.py:55・pixiv_read_client.py:268 で読むだけ。書くのは拡張からの受信時 extension_cookie_store.py:503 のみ） | 有 | 中。通信のたびに期限を延ばす挙動が無い。拡張機能を使わない人は期限が来たら取り直す。FL-A08（ブラウザからの自動抽出）と同じ領域。実装前に「Cookie を移植版が書き換えてよいか」の裁定が要る 逆方向除外:引用先の `_save_cookies_if_changed` は pixiv_client.py の非公開メソッドで、inventory の列挙対象外（2026-11-18 に確認）。 **本人裁定 2026-11-18: 実装する**（FL-A08 と合わせて設計。自動で書き換えてよい） **2026-11-19 再ステータス（work 2842b6c、候補・実機未確認）**: 通信完了時の Cookie 延長保存（値はログに出さない）（wt-ag、tests/test_ag_z04_20261119.py・test_ag_z04_race_20261119.py・test_ag_z04_wiring_20261119.py）。既知の限界は tmp/known_limits_zag.md |
| FL-Z05 | 通信の再試行・待機・レート制限対応（`_request_with_retry`。待機間隔 ±20% の乱数、総試行数 `api_retry_count`、429 は `api_retry_wait` 待機、それ以外は 2^n+乱数 の指数待機、404 は即失敗。`get_rate_settings`） | pixiv_client.py:165-225, 686-760 | request_policy.py:81-187（`RequestAdmission` 81-97、`RequestRetryPolicy` 98-187）、preferences.py（`api_retry_count`/`api_retry_wait`/`download_interval`） | 有 | 総試行数・429 待機・指数待機＋乱数・404 即失敗は同じ。**差**: (1) 待機間隔は固定の選択肢 0.3/0.4/0.5/0.6/1.0/1.5 秒で、元の ±20% の乱数は無い(request_policy.py:91)。(2) 再試行は GET のみ（元は POST も `_request_with_retry`）。(3) 401/403/リダイレクトも即失敗（元は 404 のみ即失敗）。(4) 408/425/5xx を一時障害として再試行（元は全例外を再試行）。意図した硬化か要確認。小 **2026-11-19 再ステータス（work 2842b6c、候補・実機未確認）**: 再試行・待機（±20% 乱数）・429 対応（wt-ag、tests/test_ag_z05_20261119.py） |
| FL-Z06 | 削除作品の確定規則（今回の一覧に無い作品を連続欠落でカウント、通常 3 回で確定、一度に 40% 以上・母集団 5 以上なら 5 回、確定時に警告） | core.py:1255-1300（定数 66-72）、database.py:1518-1555 | legacy_author_sync.py:429-445（母集団 5 以上かつ欠落 40% 以上なら閾値 5、それ以外は 3。再出現で 0 に戻す）、ui/managed_download_view.py:616（`連続N回` の表示）、services.py:1852-1858（完全な一覧取得のときだけ連続欠落を進める） | 有 | 確定規則（3 回／5 回／40%／母集団 5）は同じ。再出現だけでは復元扱いにせず受領書の検証を要する点は元より厳しい。元の確定時警告「削除されている可能性があります」の文言は未確認（連続表示で代替か） |
| FL-Z07 | 本番 DB へ接続する前の自動バックアップ（`prod_backup_state`/`ensure_prod_backup`、本番 DB パス判定） | database.py:26-151 | 無（`prod_backup`・`ensure_prod_backup`・`before_wipe` を非 legacy の src で grep 0 件） | 有 | 移植版は元の DB を書き換えない設計（D-00）のため、不要の可能性が高い。「不要」と決めるなら本人裁定を記録して `n/a` にする。それまでは無 **本人裁定 2026-11-18: 実装する**（移植版自身の DB・台帳の変更前に自動バックアップ。元の本番 DB は対象外） **2026-11-19 再ステータス（work 2842b6c、候補・実機未確認）**: 管理領域 DB の接続前自動バックアップ（wt-ag、tests/test_ag_z07_20261119.py）。旧 DB パスの M:\ 固定は本人裁定（旧リポ放棄）で対象外 |
| FL-Z08 | 作者ごとの個別設定（`author_settings` の対象タイプ上書き `target_type`。フォロー中作者の ZIP フラグ `is_zipped` は別表で、フォロー時に初期値を入れる） | core.py:1185-1195, 1344, 1783, 2147、database.py:1975-1998、gui.py:3926 | author_media.py:92, 120-153（`author_targets` の `target_type`）、save_exports.py:542（作者フラグ）、following_sync.py:33（`zipped` 列を保持） | 有 | 対象タイプの上書きの読み取りと ZIP フラグの保持はある。元でも `set_author_setting` は呼び出し元なし（`target_type` を画面で設定する入口は元に無い）。`is_zipped` の設定入口（未フォロー確認でのフォロー時）は FL-D14 |
| FL-Z09 | ディスク上の既存作品の判定（再ダウンロード要否。`check_work_exists_on_disk`、`judge_local_copy`、`find_existing_local`、`local_name_candidates`、同名題の衝突判定） | core.py:721-856, 2547-2719（3 つの根拠メッセージ 795-854） | author_media.py:276-323（`saved_update_date`・`files_present` による台帳ベースの判定）、managed_downloads.py:711-760（有効な受領書があれば再取得しない） | 有 | 判定方式は別（ファイル名の突き合わせではなく受領書）。「実物があれば落とし直さない」「実物が無ければ落とし直す」は成立。同名題の衝突を決められない場合の扱い（元 838 行）は受領書方式では発生しない |
| FL-Z10 | 通信のレーン（`interactive` を `background` より優先する共有ゲート `rate_coordinator`。interactive の待ちがあれば次の発行枠を渡し、background は最大 2 秒まで譲る） | rate_coordinator.py:27-82（読んだ）、pixiv_client.py:686 | request_policy.py:81-97（`RequestAdmission`＝最小間隔の共有ゲート）、discovery_learning.py:42（コメント「元のレーン名は助言のみ。実際の入場は有界リーダーが持つ」） | 有 | 間隔を共有ゲートで一本化する点は同じ。**レーンによる優先（画面操作の取得を裏処理より先に通す）は無い**（`RateCoordinator`・`MAX_BACKGROUND_DEFER` を非 legacy の src で grep 0 件）。小〜中 **2026-11-19 再ステータス（work 2842b6c、候補・実機未確認）**: 通信レーン（元 rate_coordinator の予約方式＋実発行間隔の確認）（wt-ai、tests/test_ai_lane_priority_20261119.py、Sol 12 回・Argon 1 回の検証）。既知の限界は tmp/known_limits_z10.md |
| FL-Z11 | 同一作者の処理を直列化するロック（手動実行・定期チェック・拡張機能の 3 経路が同時に ZIP を壊さないため、`get_user_lock`） | core.py:77-88 | managed_downloads.py:256-264（作品単位の `work_lock`）、691（`_writer.acquire(blocking=False)`＝書き込みは全体で 1 本） | 有 | 書き込みが全体で 1 本のため、作者単位のロックが無くても同時書き込みは起きない（別方式）。作者単位ロックは `author_lock` を grep 0 件 |
| FL-Z12 | うごイラの MP4 保存に失敗したら GIF で保存し直す（OpenCV → imageio の 2 段、最後に GIF。失敗理由を残す） | core.py:1030-1085（1080 行のメッセージ） | job_failure.py:14（`ugoira_mp4_failed` として失敗で返す）、ugoira_mp4_worker.py:1・ugoira_gif.py:6（コメントが「形式の切替なし」「黙って MP4 に戻さない」と明記） | 有 | ugoira*.py・managed_ugoira.py・save_exports.py を `fallback` で grep し、実コードの切替は 0 件（コメントが「切替なし」と書くのみ）。移植版が意図して外した可能性があり、本人裁定が要る。元の挙動（失敗時 GIF）を残すなら小 **本人裁定 2026-11-18: 実装する**（MP4 に失敗したら GIF で保存し直す） **2026-11-19 再ステータス（work 2842b6c、候補・実機未確認）**: うごイラ MP4 失敗時の GIF 再保存（wt-ag、tests/test_ag_z12_20261119.py・test_ag_z12_followup_20261119.py） |
| FL-Z13 | 元の SQLite DB のスキーマ（52 の表・列。作品台帳・フォロー中・設定・失敗キュー・学習・ブックマーク/SuperLike キャッシュ・埋め込み・重複台帳・容量キャッシュなど） | database.py:264-790 | 専用管理領域の DB 群（managed_*.py、failed_record_*.py、learning_repository.py、library_repository.py など）。元 DB は読むだけ（legacy_*.py） | 本人裁定 | 設計差 D-00（元の DB・Images を更新しない）。表ごとの対応は `ledger_map.json` の `table_rules`（works→FL-D00、failed_queue→FL-F01、discovery_*→FL-I19・FL-L07・FL-B06、など）。同値確認ではなく、どの機能の表かの対応づけ **2026-11-19 再ステータス（work 2842b6c、候補・実機未確認）**: 元の DB スキーマは更新しない設計（本人裁定 2026-11-18: 旧リポは放棄、新規で作る。D-00）。表ごとの対応は ledger_map.json の table_rules |
| FL-Z14 | 保存し直した作品の推論記録を消して推論ワーカーを起こす（`_after_illust_saved`：`work_page_scan`・`work_embed_failures` の削除、起床通知） | core.py:33-60 | similarity_worker.py:92（`wake()`＝イベント駆動の起床）。記録の削除に当たる処理は無（`page_scan`・`embed_failure`・`clear_embed`・`notify_embed` を非 legacy の src で grep 0 件） | 有 | 起床の仕組みはある。落とし直して画像が変わった作品が、古い推論結果や「諦め」のまま残らないための処理（記録の削除）が別方式で満たされるかは未読（FL-A12・FL-C21 と同じ領域）。小 逆方向除外:引用先の `_after_illust_saved`（core.py:33）は非公開関数で、inventory の列挙対象外（2026-11-18 に確認）。 **2026-11-19 再ステータス（work 2842b6c、候補・実機未確認）**: 再保存で画像が変わった作品の推論を古いまま使わない性質を、画像・受領書・モデルのハッシュ鍵で満たす（wt-aj、tests/test_aj_z14_resave_20261119.py）。永続の「諦め」記録は移植版に無い |
| FL-Z15 | Cookie の期限判定（`check_cookie_status`。PHPSESSID の期限を優先、無ければ他の Cookie の最大。残り日数で 7 日超=有効／4〜7 日=黄／3 日以内=赤、通信エラー・未認証・Cookie 無しを区別） | pixiv_client.py:344-455 | auth_status.py:51-140（`cookie_days_left`、`status_from_result`。名前・ドメイン・期限だけを読む） | 有 | 日数の丸め（切り上げ・最小 1・期限不明は 30）、7 日／3 日の閾値、無視する Cookie（`_cfuvid` など）、PHPSESSID 優先まで元と同じ。通信しない写像で、確認は services 側。FL-A11 の根拠の補強 |
| FL-Z16 | フォロー中一覧の取得（公開 `show`・非公開 `hide`、24 件ずつ、`/ajax/user/{id}/following`） | pixiv_client.py:1192-1232 | pixiv_read_client.py:1011-1040（`get_following_users`、同じ経路・同じ `limit=24`・`rest`）、following_sync.py:320-376（公開・非公開の両方を同期） | 有 | 取得の経路と刻みは同じ。移植版は件数上限（`MAX_FOLLOWED_AUTHORS`）と読み取り期限を持つ。自動同期（24 時間経過で起動時に走る）は FL-A10 で無 |
| FL-Z17 | ブックマーク一覧の取得（イラスト・漫画 48 件ずつ、小説 24 件ずつ、公開・非公開） | pixiv_client.py:1481-1607 | pixiv_read_client.py:903-916, 1005-1008（`get_bookmarked_works`・`get_bookmarked_novels`、`limit=48`・`tag=`・`rest`）、bookmark_remote_adapter.py:157 | 有 | 経路・刻み・重複 ID の除外は同じ（読んだのは取得部分のみ）。一括 DL（`download_bookmarks`）側の保存方式・ハードリンクは FL-D12・FL-D13 |
| FL-Y01 | 世間ランキングのカードに、好みのタグを持つ作品の順位横へ青ハート印を出し、更新ボタンで好みの集合を引き直す（get_discovery_preferences・build_bounds） | ranking_tab.py:522-541, 822-824, 1276 | 無（`FAVORITE`・`heart`・`has_preferred_tag`・`build_bounds` を非 legacy の src で grep。`build_bounds` は discovery_ranking.py:405 の定義だけで、ランキングのカード描画からは呼ばれない。ui/gallery.py:54-75 にも印が無い） | 有 | 規模 小。検証済み（元 ranking_tab.py:519-544 の `_has_preferred_tag`、822-824 の印、1276 の `_on_refresh`）。Argon の指摘は事実 **2026-11-19 再ステータス（work 2842b6c、候補・実機未確認）**: 世間ランキングの好みタグの青ハート（wt-y、tests/test_y_ranking_heart_20261118.py） |
| FL-Y02 | 自分のランキングの「よく効くタグ」副行を隠す／表示するトグルと、開閉の保存（設定キー `ranking_personal_candidates_open`、既定は開き） | ranking_tab.py:130, 1844-1862, 2146-2166 | ui/personal_ranking_view.py:36-37（`ui.expansion('よく効くタグ', value=True)`。開閉は保存しない） | 有 | 規模 小。検証済み。開閉の入口はあるが、閉じた状態が次回に残らない。元は `filter_panel.save_json_setting` で保存（ranking_tab.py:1861-1863）。台帳 T 節にも無かったので追加した。Argon の指摘は事実 **2026-11-19 再ステータス（work 2842b6c、候補・実機未確認）**: 「よく効くタグ」副行の開閉保存（wt-y、tests/test_y_ranking_candidates_toggle_20261118.py） |
| FL-Y03 | 自分のランキングで、取得ページ内を作者別ブクマ実績（`seed_bookmarked`）の降順に並べ替える（ページ内だけ。追記しても先に出たものは動かない） | ranking_tab.py:1512-1517, 1708-1715 | personal_ranking.py:279（作者実績の降順・同点は作者 ID・作品 ID）、services.py:1592（`author_seed_bookmarked` を `author_counts` に渡す） | 有 | 検証済み。Argon の指摘のうち、この並べ替えが無いという部分は誤り |
| FL-Y04 | 自分のランキングで、除外集合の DB 読込に失敗したとき「⚠️一部の除外条件を適用できませんでした」を出す（除外しない側へ倒して警告） | ranking_tab.py:1436-1456, 1640-1642 | ui/personal_ranking_view.py:166-167（文脈に除外集合が無いと該当の除外チェックを無効化する。警告文言は無） | 有 | 規模 小。検証済み。除外が黙って効かない状態は避けているが、警告の文言と、読めた分だけ適用する挙動は違う。Argon の指摘のうち警告文言の欠落は事実 **2026-11-19 再ステータス（work 2842b6c、候補・実機未確認）**: 除外集合の読込失敗の警告（wt-y、tests/test_y_ranking_exclusion_warning_20261118.py） |
| FL-Y05 | ライブラリの作者一覧・作者詳細で、サムネイル URL を持たない小説に、保存済みの表紙 `cover_{work_id}.*` を作者フォルダ配下から探して表示する | library_tab.py:724-744, 753-772, 1246-1250, 1673-1675 | 無（`cover_` は novel_bundle_storage.py:15・novel_practical_export.py:126 の書き出し側だけ。library_media_registry.py:197-201 は「ローカルの静止画カタログは小説を扱わない」とし、読み込み側に表紙探索が無い） | 有 | 規模 中。検証済み。リモートのプレビューが使えれば表示されうるが、保存済み表紙を読む経路は無い。Argon の指摘は事実 **2026-11-19 再ステータス（work 2842b6c、候補・実機未確認）**: 保存済み小説表紙 cover_{work_id}.* の探索（wt-ac、tests/test_ac_library_cover_20261119.py） |
| FL-Y06 | ライブラリ作者詳細のページ（`p.N`）カードから、そのページ起点で似た絵を探す。作者カードの代表作が未解析なら、同作者の解析済み作品へフォールバックする | library_tab.py:1139-1165, 1196-1216, 1695 | ui/library_view.py:926-929（ページカードの「似た絵を探す」ボタン）、958-975（`query_candidates` に同作者の作品を渡す）、ui/similar_view.py:259-265, 322（候補から検索） | 有 | 検証済み。右クリックではなくボタンだが、ページ起点の検索と代表作のフォールバックはある。Argon の指摘は誤り |
| FL-Y07 | 学習タブの判定画面で、探索枠から出た候補に「新しい絵柄を試しています」「知らない題材を試しています」のバッジを添える | discovery_tab.py:2819-2828, 2918-2926 | ui/learning_view.py:993-994（枠の種別を `frame_label` に「確実枠／題材探索枠／絵柄探索枠」と出す） | 有 | 規模 小。検証済み。枠を知らせる入口はあるが、文言とバッジの形が元と違う。Argon の指摘は一部事実 **2026-11-19 再ステータス（work 2842b6c、候補・実機未確認）**: 探索枠のバッジ（wt-z、P3-4a） |
| FL-Y08 | 学習タブの進捗行に「SuperLike使用: N回」を、1 回以上使ったときだけ出す | discovery_tab.py:136-142, 1876-1877, 1996-1998, 2680 | 無（`SuperLike使用`・`superlike_used` を非 legacy の src で grep 0 件。ui/learning_view.py:26-35 に SuperLike ボタンの定義だけ） | 有 | 規模 小。検証済み。Argon の指摘は事実 **2026-11-19 再ステータス（work 2842b6c、候補・実機未確認）**: SuperLike 使用回数（wt-z、P3-4a） |
| FL-Y09 | 学習タブの「概要」板で、説明文の `<a href>` と URL をクリックできるリンク（外部は `↗` つき）にする | discovery_tab.py:3751-3777 | ui/learning_view.py:627（`ui.link(run.text + ' ↗')`）、discovery_description.py:143-160（HTML パーサで行ごとの run に分ける） | 有 | 検証済み。Argon の指摘のうちリンク抽出は誤り（書き直されているが同じ機能） |
| FL-Y10 | 学習タブで、保存・仕入れなどの重い失敗を、自動で消えず手動の「閉じる」まで残す 2 行の通知枠で出す | discovery_tab.py:853-896（`_discovery_gave_up_toast`） | ui/learning_view.py:1447-1449（補充停止の説明を `refill_status` に常設表示。手動で閉じる通知枠は無） | 有 | 規模 小。検証済み。消えない通知という目的は常設テキストで満たすが、「閉じる」操作と 2 行枠は無い。FL-I06（判定直後のトースト帯）と同じ領域。Argon の指摘は一部事実 **2026-11-19 再ステータス（work 2842b6c、候補・実機未確認）**: 重い失敗の通知枠（wt-z、P3-4a） |
| FL-Y11 | 学習タブ「振り返り」で、ローカルに無い作品の題名・サムネイルを get_work_info で遅延取得し、失敗したら「取得失敗（タップで再取得）」枠に変える | discovery_tab.py:4955-5017, 5329-5331 | ui/learning_view.py:1202-1207（保存済みの題名か作品 ID と「画像未取得」の表示のみ。遅延取得も再取得も無い） | 有 | 規模 中。検証済み。元 4955-5005 は失敗枠のタップで再取得。Argon の指摘のうち遅延取得と再取得枠は事実 **2026-11-19 再ステータス（work 2842b6c、候補・実機未確認）**: 振り返りの題名・サムネイル遅延取得と再取得枠（wt-ab、tests/test_ab_learning_y11_20261118.py） |
| FL-Y12 | 学習タブ「振り返り」の期間絞り込みの「この学習セッション」 | discovery_tab.py:4869-4871, 5054-5055, 5485 | ui/learning_view.py:222（期間 `session`＝「この学習セッション」）、1195（`session_started`） | 有 | 検証済み。台帳 FL-I16 の期間フィルターの記述に含まれており、移植版にもある。Argon の指摘は誤り |
| FL-Y13 | おすすめタブで、「もっと見る」で展開した後に初期件数へ戻す「畳む」ボタン | recommend_tab.py:1190-1190, 1845-1849, 2418-2422 | ui/recommend_view.py:455（`ui.button('畳む')`）、`fold` | 有 | 検証済み。Argon の指摘は誤り |
| FL-Y14 | おすすめの SuperLike 節で、以前に表示できた作品が削除・非公開で取得できなくなったら、「⚠ 表示できません」のカードとして残す | recommend_tab.py:1042-1053, 1165-1172 | 無（`deleted`・`表示できません` を recommendation*.py・ui/recommend_view.py で grep。ui/recommend_view.py:412 の「画像を表示できません」はサムネイル取得の例外用で別の用途） | 有 | 規模 小。検証済み。Argon の指摘は事実 **2026-11-19 再ステータス（work 2842b6c、候補・実機未確認）**: 取得不能の既知 SuperLike を「表示できません」で残す（wt-aa、tests/test_aa_recommend_superlike_20261118.py） |
| FL-Y15 | おすすめの節（確実枠・boosted）の結果を DB に持ち越し（`recommend_section_carryover`）、開いた直後に即時表示する。空の結果では上書きしない | recommend_tab.py:2285-2299, 1522-1543 | recommendation_state.py:140（`recommend_section_carryover` を読む）、recommendation_acquisition.py:95, 164（持ち越しの保持と、空・失敗で消さない処理） | 有 | 検証済み。Argon の指摘は誤り |
| FL-Y16 | 借用トークンの失効を先回りで防ぐウォッチャー（`start_token_keeper`。解錠中だけ、失効時刻から逆算して更新） | scheduler.py:137-142 | external_vault.py:1052-1081（`start_keeper`、スレッド `pixivvault-external-token-keeper`）、services.py:788（起動）、ui/external_vault_view.py:379-381、ui/premium_gate_view.py:287 | 有 | 検証済み。Argon の指摘は誤り（名前が違うだけ） 逆方向除外:引用先 scheduler.py:137-142 は `_run_nana7mi_shaft_refresh_if_enabled` の中の 1 行の呼び出しで、項目が無い（2026-11-18 に確認）。 |
| FL-Y17 | 拡張機能連携で、進行中の同一ジョブ（種別と対象 ID が同じ）を新しく作らず再利用する | server.py:1371-1383 | job_service.py:526-532（同じ kind・target_id の未完了ジョブを返す）、extension_application.py:71-85 | 有 | 検証済み。Argon の指摘は誤り |
| FL-Y18 | `POST /download` で type=work かつ new_only=True を、作者の差分 DL へ変換する | server.py の _run_web_download_job の new_only 分岐（iOS／Web 連携の経路） | 該当なし（拡張機能の経路は work を `new_only=False` 固定で積む。server.py:689-701。移植版 extension_bridge.py:55-60 は work に new_only を許さない） | 意図的削除 | 検証済み。変換が使われるのは iOS／Web 連携の経路（`newOnly`、server.py:775-801）で、本人が除外した範囲。拡張機能からは起きない。Argon の指摘は誤り（対象外） |
| FL-V01 | ライトモードのカードの色 | 元の見た目契約（暗色カード。ライトでも暗いカード） | ui/theme.py の `light` ブロックが別名を再宣言して明るいカードにする | 本人裁定 | **2026-11-18 記録**。本人裁定 11-17: ライトモードは明るいカード（元の暗色カードは写さない）。「元に合わせる」作業で戻さない。試験 tests/test_q_shell_20261117.py の test_light_theme_cards_are_bright・test_light_block_redeclares_the_aliases_so_views_do_not_keep_dark_cards |
| FL-V02 | タブの幅と並び | 元のタブバー（gui.py の TAB_ORDER の順・幅は内容なり） | ui/shell.py の NAVIGATION、theme.py の `.pv-tab` | 本人裁定 | **2026-11-18 記録**。本人裁定 11-17: 全タブを同じ幅（アイコン付き）。並びは元の順→自分のランキング→ライブラリ・おすすめ→定期チェック・メタデータ補完で、ランキングと自分のランキングは隣り合わせ、追加タブは後ろ。「元に合わせる」作業で戻さない。試験 test_q_shell_20261117.py の test_navigation_constant_matches_the_decided_order・test_tab_bar_order_icons_and_no_backup_tab・test_all_tabs_have_the_same_width_and_keep_icon_and_label_visible |
| FL-V03 | 1280px でのタブの 2 段折り返し | 元（タブは 1 行） | 1280px で 2 段に折り返し、1920px は 1 行（tests/test_q_shell_20261117.py の 2 段・1 行の表明、コミット 4bc14bb） | 有（本人裁定） | **2026-11-18 記録**。本人裁定 11-18: 1280px では 2 段に折り返して全タブを見えるようにする（等幅は保つ）。**未実装**のため弱（小）。実装時は上の試験の「1 行・overflowX=auto」の表明を、2 段・全タブ可視の表明へ直す |
| FL-V04 | 直近のバックアップの置き場所 | 元はタブ（gui.py の直近のバックアップ画面） | ヘッダー右の個別アイコン（ui/shell.py。画面は ui/viewer_backup_view.py のまま） | 本人裁定 | **2026-11-18 記録**。本人裁定 11-17: 直近のバックアップはヘッダーのアイコン（タブにしない）。FL-G01 の本体（作者サムネイルの画面）は別に追う。試験 test_q_shell_20261117.py の test_backup_is_a_header_icon_button_and_still_opens_the_backup_screen |
| FL-V05 | メタデータ自動補完の既定 | 元は `meta_backfill_enabled` が既定 OFF（gui.py の設定ダイアログ） | preferences.py:38-39（既定 ON。設定で止められる） | 本人裁定 | **2026-11-18 記録**。本人裁定 11-17: 既定はオン（元はオフ）。「元に合わせる」作業で OFF に戻さない。試験 tests/test_i_metadata_auto_20261117.py の test_preference_specs_default_on_with_original_names・test_auto_enabled_defaults_on_and_follows_preference |
| FL-V06 | 旧リポ（元アプリ）と旧 DB の扱い | 元アプリ S:\Projects\PixivVault（Flet 版）と元の SQLite DB | 管理領域の台帳で完結する（FL-D00 も同じ） | 本人裁定 | **2026-11-18 記録**。本人裁定 11-18: 旧リポは放棄・サポート終了で併用しない。旧 DB へは書かない（FL-D00 の設計書 WU-9「旧 DB の反映器」は作らない）。旧ライブラリ（おすすめ・重複整理の条件付き機能）は元 DB を読み取り専用で参照するだけ。FL-Z13（元のスキーマ）の弱は、この裁定で「旧スキーマの再現は不要」側に倒れる可能性があるが、行は本人の確認まで残す |

## 無・弱の規模見積り（FL-Z）

| 規模 | ID | 内容 |
|---|---|---|
| 小（裁定が先） | FL-Z12 | MP4 失敗時に GIF へ切り替える（移植版は意図して外した可能性） |
| 小 | FL-Z14 | 保存し直しで推論記録を消す（類似度処理の読解が先） |
| 小〜中 | FL-Z05 | 待機の乱数、POST 再試行、即失敗の範囲（意図した硬化かの確認） |
| 小〜中 | FL-Z10 | レーンによる優先 |
| 中 | FL-Z01 | 元の命名での書き出しの確認（読解が先） |
| 中 | FL-Z02 | ZIP64 互換化・追記・名称整合の有無の確認 |
| 中 | FL-Z04 | Cookie の延長保存（書き換えてよいかの裁定が先。FL-A08 と合わせて設計） |
| 裁定 | FL-Z07 | 本番 DB バックアップは不要と決めるか |

### FL-Y（Argon 指摘の検証で追記）の無・弱

| 規模 | ID | 内容 |
|---|---|---|
| 小 | FL-Y01 | 世間ランキングの好みタグ青ハート印（無） |
| 小 | FL-Y02 | 「よく効くタグ」副行の開閉の保存（弱） |
| 小 | FL-Y04 | 除外条件の部分失敗の警告文言（弱） |
| 小 | FL-Y07 | 探索枠バッジの文言（弱） |
| 小 | FL-Y08 | 「SuperLike使用: N回」（無） |
| 小 | FL-Y10 | 重い諦めの手動閉鎖通知枠（弱） |
| 小 | FL-Y14 | 取得不能 SuperLike の「⚠ 表示できません」（無） |
| 中 | FL-Y05 | 保存済み小説表紙のローカル探索（無） |
| 中 | FL-Y11 | 振り返りの未保存作品メタ遅延取得と再取得枠（無） |

2026-11-18 の追記: FL-Z04・Z07・Z12 は本人裁定で「実装する」と決まった（状態は実装が済むまで 無・弱のまま。Z04 中・Z07 中・Z12 小）。FL-V03（1280px の 2 段折り返し）は小で未実装。FL-V01〜V06 のうち状態が「本人裁定」の 5 行は、元に合わせる作業で戻さない（`--strict-gaps` の対象外）。

事実でなかった Argon の取りこぼし指摘（FL-Y03・Y06・Y09・Y12・Y13・Y15・Y16・Y17・Y18）は、状態を有（Y18 は意図的削除＝iOS／Web 連携の経路）として行に残した。

## 付録A: 元の文言が移植版に見つからないもの

<!-- APPENDIX-A:BEGIN -->

元の UI 文言(ui_text・menu_item・label_const・snack)のうち、移植版 `src/pixivvault_nicegui` 全体にその文言(f 文字列は最長の固定部分)が見つからないもの。
言い回しが違うだけの可能性があるため、**同値の判定ではなく確認の手がかり**。件数は台帳の FL ごと。

| 台帳 FL | 台帳の状態 | 未検出の文言 件数 | 例(元の場所) |
|---|---|---|---|
| FL-A06 | 条件付きで有 | 2 | 「終了確認」main.py:494; 「いいえ」main.py:498 |
| FL-A08 | 有 | 10 | 「[確認中] Cookie の状態をチェック中」gui.py:1119; 「[更新完了] {}」gui.py:1132; 「✓ Cookie をブラウザ ({}) から」gui.py:1134 |
| FL-A09 | 有 | 1 | 「現在保存されているフォロー中データ（お気に入」gui.py:6304 |
| FL-A11 | 有 | 1 | 「PV拡張機能をご利用中の場合、cookies」gui.py:2844 |
| FL-A12 | 弱 | 3 | 「{} （詳しい中身は gui_error_l」gui.py:2064; 「前回は次の理由で止まりました: {}。ダウン」gui.py:2070; 「前回は次の理由で止まりました: {}。『いま」gui.py:2076 |
| FL-A14 | 条件付きで有 | 6 | 「自動起動を有効にしました！」gui.py:1607; 「登録に失敗しました。」gui.py:1609; 「自動起動を無効にしました。」gui.py:1617 |
| FL-B04 | 有 | 1 | 「対象の保存フォルダが見つかりません。」gui.py:2441 |
| FL-B06 | 有 | 11 | 「閲覧履歴（学習タブ・{}件）」gui.py:2466; 「閲覧履歴: 全件削除しました」gui.py:2486; 「閲覧履歴: 削除に失敗しました: {}」gui.py:2494 |
| FL-B10 | 条件付きで有 | 7 | 「Shaft SECRET_KEY（任意・既に」gui.py:878; 「UID一覧（txt）を読み込む」gui.py:950; 「先に tools/nana7mi_vault」gui.py:963 |
| FL-C01 | 有 | 2 | 「[完了] cookies.txt をインポー」gui.py:1094; 「キャンセルされました。」gui.py:1101 |
| FL-C02 | 有 | 3 | 「[完了] 同期完了 ({}人の作者)」gui.py:1215; 「[失敗] 同期失敗: {}」gui.py:1222; 「リストを同期」gui.py:1664 |
| FL-C03 | 有 | 3 | 「現在の保存先: {}」gui.py:1192; 「フォルダー選択エラー: {}」gui.py:1199; 「Pixivから同期中...」gui.py:1203 |
| FL-C04 | 条件付きで有 | 1 | 「有効 ─ 自動起動は登録済みです」gui.py:1599 |
| FL-C05 | 条件付きで有 | 1 | 「起動時にもお気に入り新着チェックを実行する」gui.py:1309 |
| FL-C06 | 条件付きで有 | 1 | 「☆ お気に入り自動チェック間隔」gui.py:1271 |
| FL-C09 | 条件付きで有 | 2 | 「GIF動画 (デフォルト)」gui.py:1257; 「すべてフォルダ格納する (ZIP解凍+JSO」gui.py:1259 |
| FL-C10 | 条件付きで有 | 3 | 「新着通知のON/OFF」gui.py:1318; 「ON (通知する)」gui.py:1320; 「OFF (通知しない)」gui.py:1321 |
| FL-C11 | 条件付きで有 | 3 | 「ブックマーク保存モード」gui.py:1332; 「ブックマークフォルダ内は作者別フォルダを作ら」gui.py:1334; 「ブックマークフォルダ内も作者別フォルダを作成」gui.py:1335 |
| FL-C12 | 条件付きで有 | 6 | 「ダウンロード待機時間 (秒)」gui.py:1400; 「0.3秒 (最速・自己責任)」gui.py:1402; 「0.4秒 (高速)」gui.py:1403 |
| FL-C13 | 条件付きで有 | 7 | 「3回 (推奨)」gui.py:1420; 「5回」gui.py:1421; 「429 レート制限検知時待機時間」gui.py:1433 |
| FL-C14 | 条件付きで有 | 14 | 「学習: 画像先読みキャッシュの上限指定方法」gui.py:1458; 「メモリ使用量で指定」gui.py:1460; 「画像枚数で指定」gui.py:1461 |
| FL-C15 | 条件付きで有 | 15 | 「画像キャッシュ上限 (全タブ共有・メモリ使用」gui.py:1494; 「512MB」gui.py:1496; 「1024MB」gui.py:1497 |
| FL-C16 | 条件付きで有 | 3 | 「キャッシュを削除しました ({}件)」gui.py:1365; 「キャッシュはありませんでした。」gui.py:1368; 「キャッシュを削除する」gui.py:1374 |
| FL-C17 | 条件付きで有 | 1 | 「すべての作者に対しダウンロード完了後zipに」gui.py:1231 |
| FL-C18 | 条件付きで有 | 1 | 「最小化時にタスクトレイに格納する」gui.py:1344 |
| FL-C21 | 有 | 3 | 「いま推論する」gui.py:1823; 「モデルの読み込みに失敗」style_infer.py:106; 「構図の出口が無い」style_infer.py:108 |
| FL-C22 | 条件付きで有 | 5 | 「エクスポートを実行」gui.py:2320; 「エクスポートが完了しました: {}」gui.py:2330; 「エクスポートに失敗しました: {}」gui.py:2336 |
| FL-C24 | 有 | 1 | 「ver 4.0.0 build260913-」gui.py:2420 |
| FL-C26 | 意図的削除 | 3 | 「接続トークンを再発行する（旧トークンで設定済」gui.py:803; 「API経路を切り替える」gui.py:820; 「API経路を切り替える」gui.py:823 |
| FL-D01 | 有 | 3 | 「作者を選ぶか、IDを入れて実行します」unified_download_tab.py:1378; 「対象の選び方はありません」unified_download_tab.py:1471; 「含めるブックマーク」unified_download_tab.py:1472 |
| FL-D02 | 有 | 3 | 「絞り込み（数字はID指定）」unified_download_tab.py:404; 「前の50人」unified_download_tab.py:413; 「次の50人」unified_download_tab.py:418 |
| FL-D03 | 弱 | 4 | 「作者名順」unified_download.py:53; 「保存済みの多い順」unified_download.py:54; 「最後に取得した日」unified_download.py:55 |
| FL-D04 | 有 | 2 | 「マイブックマーク（全体）」unified_download_tab.py:587; 「左の一覧でチェックすると、ここに積まれます」unified_download_tab.py:628 |
| FL-D05 | 条件付きで有 | 2 | 「0 人」unified_download_tab.py:1513; 「自動監視」unified_download_tab.py:1519 |
| FL-D06 | 有 | 2 | 「対象に積む」unified_download_tab.py:389; 「フォロー中の {} です（ID: {}）」unified_download_tab.py:798 |
| FL-D09 | 弱 | 6 | 「左の一覧で作者を選ぶと押せます」unified_download_tab.py:513; 「差分DL · {} · 保存先 {}」unified_download_tab.py:533; 「再開しました。」unified_download_tab.py:1046 |
| FL-D10 | 弱 | 9 | 「残り時間計算中...」gui.py:381; 「この回の対象」unified_download_tab.py:206; 「0 人」unified_download_tab.py:207 |
| FL-D11 | 弱 | 1 | 「含めるブックマークを1つ選びます」unified_download_tab.py:1437 |
| FL-D12 | 弱 | 1 | 「階層化（作者別フォルダ）」unified_download_tab.py:1440 |
| FL-D13 | 条件付きで有 | 10 | 「cookies.txt が見つかりません。設」gui.py:4884; 「統合DL が完了しました」gui.py:4911; 「統合DL でエラー: {}」gui.py:4916 |
| FL-D14 | 条件付きで有 | 16 | 「ログインチェックを完了してください。」gui.py:3785; 「含めるブックマークを1つ選びます。」gui.py:3788; 「抽出エラー: {}」gui.py:3805 |
| FL-D16 | 弱 | 2 | 「PixivVault - ZIP破損検出」core.py:112; 「{} のアーカイブに問題があります: {}」core.py:112 |
| FL-E01 | 有 | 3 | 「現在のジョブはありません」gui.py:3617; 「iOS版から送られた取得ジョブがここに並びま」gui.py:3618; 「一致するジョブはありません」gui.py:3622 |
| FL-E04 | 有 | 1 | 「作者名で絞り込む」gui.py:3386 |
| FL-E05 | 有 | 5 | 「全ジョブを削除しますか？」gui.py:3341; 「完了・エラーを含む全ジョブをキューから削除し」gui.py:3342; 「{}件を削除しますか？」gui.py:3363 |
| FL-E06 | 無 | 2 | 「ダウンロードキューログ」gui.py:3708; 「拡張機能連携のDLはここにのみ表示」gui.py:3710 |
| FL-E07 | 有 | 1 | 「2秒毎に自動更新／一覧かタイトルのダブルクリ」gui.py:3739 |
| FL-F02 | 有 | 1 | 「再試行エラー ({}): {}」gui.py:4217 |
| FL-F03 | 有 | 6 | 「再試行対象の失敗作品がありません。」gui.py:4291; 「失敗リスト（絞り込み分）の再試行: {}/{」gui.py:4307; 「失敗リストを全件クリアしますか？」gui.py:4319 |
| FL-G01 | 弱 | 5 | 「直近バックアップ履歴取得エラー: {}」gui.py:4512; 「直近バックアップ履歴: PixivClien」gui.py:4528; 「{} ({}) クリックして開く」gui.py:4693 |
| FL-H01 | 有 | 3 | 「🗑 ごみ箱」gui.py:5016; 「スキャン中…」gui.py:5711; 「重複整理スキャン: 」gui.py:5742 |
| FL-H03 | 無 | 1 | 「{}（{}）のものにする」gui.py:5172 |
| FL-H04 | 有 | 3 | 「{}枚を選択中」gui.py:5261; 「全選択を戻しました（1枚は必ず残ります）」gui.py:5269; 「画質の低い方をまとめて選ぶ」gui.py:5351 |
| FL-H05 | 有 | 3 | 「{}組・{}枚を選択中・{}」gui.py:5388; 「0件選択中」gui.py:5390; 「この操作では消えません。あとから戻せます。各」gui.py:5529 |
| FL-H07 | 有 | 4 | 「作者名・user_idで絞り込む」gui.py:5032; 「1組ずつ開かずに、この一覧のまま選んで移せま」gui.py:5044; 「0件選択中」gui.py:5051 |
| FL-I02 | 有 | 1 | 「直前の判定を取り消す　Ctrl+Z」discovery_tab.py:2544 |
| FL-I05 | 無 | 1 | 「何が合いませんでしたか？」discovery_tab.py:1760 |
| FL-I06 | 弱 | 1 | 「判定しました」discovery_tab.py:1680 |
| FL-I07 | 有 | 1 | 「画像を見ないままの判定はできません」discovery_tab.py:2385 |
| FL-I08 | 有 | 2 | 「クリックで画集ビューアを開く」discovery_tab.py:2446; 「クリックでpixivの作品ページを開く」discovery_tab.py:2456 |
| FL-I09 | 弱 | 1 | 「絵が表示されてから押せます」discovery_tab.py:2577 |
| FL-I10 | 条件付きで有 | 1 | 「ダウンロードキューへ積む」recommend_tab.py:847 |
| FL-I11 | 弱 | 6 | 「絵が表示されてから押せます」discovery_tab.py:2885; 「pixivでブックマーク（pixiv側に書き」discovery_tab.py:2885; 「いまはあなたの保存から出しています」discovery_tab.py:2975 |
| FL-I12 | 弱 | 3 | 「探索の設定」discovery_tab.py:3295; 「どのくらい知らないものを見るかを変えます」discovery_tab.py:3302; 「判定は保存され、次に出す候補の並べ替えに反映」discovery_tab.py:3360 |
| FL-I13 | 有 | 2 | 「作品の情報を取れませんでした。押すともう一度」discovery_tab.py:3853; 「作品の情報を取りにいっています」discovery_tab.py:3860 |
| FL-I14 | 有 | 3 | 「作品を「見送る」「合わない」「いいね」「Su」discovery_tab.py:4357; 「自動での再取得は止まっています。」discovery_tab.py:4409; 「通信に失敗しました。30秒ごとにもう一度試し」discovery_tab.py:4409 |
| FL-I15 | 無 | 2 | 「画像からのタグ補完にはローカルモデルの導入（」discovery_tab.py:4366; 「設定を開く」discovery_tab.py:4367 |
| FL-I16 | 弱 | 7 | 「『{}』の判定を取り消しました — {}」discovery_tab.py:5302; 「今日は該当なし」discovery_tab.py:5412; 「今日は該当なし」discovery_tab.py:5418 |
| FL-I18 | 有 | 4 | 「これまでの判定（{}枚）と、そこから学んだ好」gui.py:2195; 「学習状況: リセットに失敗しました: {}」gui.py:2224; 「CSV出力完了: {}」gui.py:2260 |
| FL-J05 | 弱 | 2 | 「R-18を含める」ranking_tab.py:1319; 「AI作品を除外（作品ごとに追加確認するため、」ranking_tab.py:1324 |
| FL-J06 | 有 | 2 | 「タグ検索に失敗しました（{}）」ranking_tab.py:1130; 「タグで絞り込み」ranking_tab.py:1328 |
| FL-J07 | 有 | 3 | 「取得時刻: {}　{}件」ranking_tab.py:955; 「ランキングを取得できませんでした（{}）」ranking_tab.py:1056; 「ランキングを取得できませんでした（{}）」ranking_tab.py:1067 |
| FL-J08 | 有 | 2 | 「クリックで画集ビューアを開く」ranking_tab.py:857; 「クリックで画集ビューアを開く」ranking_tab.py:1577 |
| FL-J09 | 弱 | 7 | 「ブックマークしました。」ranking_tab.py:617; 「iOS連携サーバーが起動していないためキュー」ranking_tab.py:627; 「「{}」をもっと見るに追加しました。」ranking_tab.py:760 |
| FL-J10 | 無 | 6 | 「ダウンロードキューへ積みました。」ranking_tab.py:629; 「出さない一覧（{}件）」ranking_tab.py:678; 「次回から表示しません。」ranking_tab.py:751 |
| FL-J11 | 条件付きで有 | 6 | 「検索中…」ranking_tab.py:1592; 「検索に失敗しました（{}）」ranking_tab.py:1690; 「検索結果の処理に失敗しました。」ranking_tab.py:1737 |
| FL-J12 | 弱 | 14 | 「DL済みを除外」ranking_tab.py:158; 「出さない作者を除外」ranking_tab.py:159; 「「出さない」登録の作者」ranking_tab.py:159 |
| FL-K01 | 有 | 10 | 「{} 人の作者中 {} 人を表示 / 全 {」library_tab.py:2203; 「{} 人の作者 / 全 {} 人」library_tab.py:2205; 「pixiv本体の検索に失敗しました（{}）」library_tab.py:2215 |
| FL-K02 | 弱 | 6 | 「タイトル・作者・タグを検索」library_tab.py:2279; 「ライブラリのみ」library_tab.py:2325; 「pixiv本体も含む」library_tab.py:2326 |
| FL-K04 | 有 | 15 | 「このタブでは使いません」filter_panel.py:88; 「データ取得後に使えるようになります」filter_panel.py:89; 「未実装です」filter_panel.py:90 |
| FL-K05 | 有 | 1 | 「いま表示している作者ぶんの容量キャッシュを消」library_tab.py:2978 |
| FL-K06 | 弱 | 12 | 「選択中: 0件」library_tab.py:1875; 「選択をダウンロード」library_tab.py:1876; 「{} 件中 {} 件を表示 / 全 {} 件」library_tab.py:1950 |
| FL-K07 | 有 | 2 | 「<ContextMenu>」library_tab.py:1168; 「クリックで画集ビューアを開く」library_tab.py:1383 |
| FL-K08 | 有 | 2 | 「クリックでこの作者の作品一覧を開きます」library_tab.py:1241; 「クリックでpixivの作者ページを開く」library_tab.py:1262 |
| FL-K10 | 条件付きで有 | 6 | 「クリックで画集ビューアを開く」library_tab.py:1007; 「解析済みの作品がまだありません。」library_tab.py:1028; 「この作品はまだ解析されていません（解析済み:」library_tab.py:1030 |
| FL-K11 | 有 | 5 | 「実ファイルが見つからないため1ページ目の画像」library_tab.py:1627; 「クリックで p.{} から画集ビューアを開き」library_tab.py:1630; 「クリックで p.{} から画集ビューアを開き」library_tab.py:1664 |
| FL-L01 | 弱 | 13 | 「傾向ピルをクリックして追加したタグの候補」recommend_tab.py:151; 「好みの学習結果に基づく候補」recommend_tab.py:152; 「あなたが好んで見ているタグの作品」recommend_tab.py:153 |
| FL-L02 | 弱 | 9 | 「この節を開く」recommend_tab.py:2830; 「この節を開く」recommend_tab.py:2860; 「この節を閉じる」recommend_tab.py:2860 |
| FL-L03 | 有 | 2 | 「更新（取得済みの内容を取り直します）」recommend_tab.py:2671; 「今日は該当なし」recommend_tab.py:2681 |
| FL-L04 | 弱 | 2 | 「このタグを開く」recommend_tab.py:1894; 「このタグを開く」recommend_tab.py:1987 |
| FL-L05 | 弱 | 16 | 「ブックマークしました。」recommend_tab.py:509; 「次回から表示しません。」recommend_tab.py:622; 「iOS連携サーバーが起動していないためキュー」recommend_tab.py:651 |
| FL-L07 | 有 | 14 | 「出さない一覧（{}件）」recommend_tab.py:1337; 「もっと見る作者（{}人）」recommend_tab.py:1338; 「出さない作者一覧（{}人）」recommend_tab.py:1339 |
| FL-L09 | 有 | 1 | 「登録中の作者に新着はありません」recommend_tab.py:2905 |
| FL-L10 | 条件付きで有 | 8 | 「フォローしました。」recommend_tab.py:3484; 「差分DLを実行しました。」recommend_tab.py:3494; 「差分DLに失敗しました。」recommend_tab.py:3497 |
| FL-M01 | 有 | 3 | 「前へ（←）」artbook_viewer.py:1270; 「次へ（→）」artbook_viewer.py:1272; 「閉じる（Escでも可）」artbook_viewer.py:1480 |
| FL-M02 | 有 | 2 | 「<ContextMenu>」artbook_viewer.py:1303; 「説明文はありません。」artbook_viewer.py:1371 |
| FL-M03 | 条件付きで有 | 5 | 「ページを取得できませんでした。」artbook_viewer.py:510; 「画像を取得できませんでした。」artbook_viewer.py:540; 「ブックマークしました。」artbook_viewer.py:673 |
| FL-M04 | 弱 | 2 | 「次回から表示しません。」artbook_viewer.py:817; 「pixivで作品ページを開く」artbook_viewer.py:860 |
| FL-M05 | 条件付きで有 | 3 | 「アニメーション」artbook_viewer.py:413; 「うごイラを取得できませんでした。」artbook_viewer.py:448; 「まだうごイラを読み込めていないため保存できま」artbook_viewer.py:1045 |
| FL-N02 | 有 | 4 | 「PixivVault ダウンロード完了」server.py:1586; 「{} (id={}) の保存が完了しました！」server.py:1586; 「PixivVault エラー」server.py:1588 |
| FL-N05 | 弱 | 7 | 「PixivVault 転送完了（一部失敗）」server.py:1308; 「{} の転送が終わりました（成功{}件／失敗」server.py:1309; 「PixivVault 転送完了」server.py:1311 |
| FL-O01 | 弱 | 2 | 「PixivVault 定期チェック」scheduler.py:227; 「お気に入りの作者から {} 件の作品を保存し」scheduler.py:228 |
| FL-X01 | PV_DEV_P | 2 | 「PixivVault ダウンロード開始」server.py:1522; 「{} (id={}) の保存を開始します。」server.py:1522 |
| FL-Y02 | 弱 | 2 | 「よく効くタグを隠す」ranking_tab.py:2147; 「よく効くタグ:」ranking_tab.py:2148 |
| FL-Y08 | 無 | 1 | 「SuperLike使用: {}回」discovery_tab.py:143 |
| FL-Y11 | 無 | 1 | 「タップで再取得」discovery_tab.py:5001 |
| FL-Y12 | 有 | 2 | 「直近7日」discovery_tab.py:5482; 「直近30日」discovery_tab.py:5483 |
| FL-Y14 | 無 | 1 | 「⚠ 表示できません」recommend_tab.py:1170 |
| FL-Z06 | 有 | 3 | 「user_id={}: 未認証のため、削除の」core.py:1243; 「user_id={}: 今回の一覧に見当たら」core.py:1275; 「以下の作品がPixivから削除されている可能」core.py:1288 |
| FL-Z07 | 無 | 2 | 「M:\2次\PixivVault」database.py:20; 「M:\2次\PixivVault」database.py:23 |
| FL-Z09 | 有 | 1 | 「user_id={}: この作者の実物が1つ」core.py:1335 |

合計 463 件(複数の FL に結ばれた項目は、各 FL の行に 1 件ずつ数えるため、表の件数の和は合計より多い)。

<!-- APPENDIX-A:END -->

## 2026-11-18 wt-y ランキング実装の検証メモ

下表は隔離データでの実装・自動試験の対応であり、D1 の「有」（通常起動・実プロファイルで到達）や独立した出口判定を意味しない。上の過去の弱／無の記述は調査時点の履歴として残す。

| 項目 | 実装根拠 | 実行した主な試験 |
|---|---|---|
| FL-J12 | `ui/personal_ranking_view.py:79`, `:217`, `:231`, `:242`, `:256`, `:263`; `personal_ranking.py:85`, `:127` | `test_active_chips_row_shows_current_conditions_and_hides_when_empty`, `test_clicking_a_chip_removes_that_one_condition_only`, `test_clear_all_chip_returns_every_condition_to_its_default`, `test_r18_switch_default_on_and_operable_when_search_is_available`, `test_server_side_conditions_search_again_by_themselves`, `test_clear_conditions_and_bookmark_chip_search_again`, `test_personal_all_filter_combinations_and_original_unknown_author_limit` |
| FL-J05 | `ui/ranking_view.py:75`, `:103`; `ranking_source.py:203` | `test_r18_switch_stays_operable_in_premium_tag_search_and_keeps_its_value`, `test_r18_switch_still_disabled_for_modes_that_cannot_use_it`, `test_ai_exclusion_does_not_change_raw_end_condition` |
| FL-Y01 | `ui/ranking_view.py:17`, `:219`, `:289`; `ranking_preferred_tags.py:10` | `test_heart_is_shown_beside_the_rank_only_for_works_with_a_preferred_tag`, `test_preferences_are_rebuilt_on_first_fetch_and_on_refresh_only`, `test_light_ranking_heart_and_filter_chip_contrast` |
| FL-Y02 | `ui/personal_ranking_view.py:48`, `:88`; `external_vault.py:1267` | `test_toggle_hides_only_label_and_chips_and_the_choice_survives_reopen_and_tag_edits` |
| FL-Y04 | `services.py:1648`; `ui/personal_ranking_view.py:181` | `test_warning_when_an_enabled_exclusion_set_could_not_be_read_and_the_rest_still_applies`, `test_one_failing_loader_does_not_discard_the_sets_that_were_read` |

逸脱: 元 `ranking_tab.py:452,1319` の世間 R-18 既定は OFF で、FL-J05 の旧根拠は個人の `:2135` を引用していた。今回の明示条件に従い世間側チェックを既定 ON としたため、借用タグ検索以外の初期ランキング・通常タグ検索でも R-18 選択が初期 ON になる。借用タグ検索の結果自体は元と同じく R-18 指定で変わらない。元 `:1219-1227` は非対応ランキング種別でスイッチを無効化し、タグ入力で復帰しないが、今回の指定に従いタグ検索中は操作可能とした。

P3-4a 候補（2026-11-18、wt-z）: FL-Y07・FL-Y08・FL-Y10 の実装と隔離検証の根拠、残る受入は [検証記録](evidence/p3-4a-20261118/README.md) を参照。上表は元の調査時点の履歴であり、この候補の実装状態ではない。実機受入前のため正式状態は変更しない。
