# 元(Flet)→移植版(NiceGUI) 機能対応台帳 2026-11-17

- 元: `S:\Projects\PixivVault`（正。ルート直下の `.py`。`.claude\worktrees\` 内の旧コピーは見ていない）
- 移植版: `S:\Projects\PV-DEV-20261117\work\src\pixivvault_nicegui`、**HEAD `5ca397a`（Merge fix-g：借用ポップアップの原本一致・回復操作）時点**。作成途中で 4b16f20 → 5ca397a と進んだ（fix-d：auth_status・shell・ranking_view・native_startup・native_profile_view、fix-g：premium_*・preferences_view・shell・failed_records_view）。これらのファイルの引用行は 5ca397a で grep し直して差し替えた（`file:N` は末尾の検査スクリプトで存在・範囲を確認済み）。それ以外のファイルの引用行は 4b16f20 時点
- 未取り込みの別ツリー（wt-a〜wt-g）は「無」と判定し、実装が別ツリーにあれば備考へ書く（work に無いものを有にしない）
- 書き込みはこのファイルのみ。Cookie 実値・実DB・実通信は使っていない（読取のみ）

## 状態の定義

| 記号 | 意味 |
|---|---|
| 有 | 元と同じ操作・同じ結果が work にある（読んだ箇所を根拠に併記） |
| 条件付きで有 | 起動構成・接続状況（`services.*`）・設定が揃ったときだけ有。条件を書く |
| 弱 | 入口はあるが、元より機能が欠ける / 既定や自動性が違う / 別画面・別操作へ変わっている |
| 無 | work に見つからない（別名・別画面も確認済みの場合のみ）。「確認の深さ」を根拠欄へ書く |
| 意図的削除 | 本人決定（iOS連携・Web連携・Shaft連携タブ・旧保管庫取込）。退行に数えない |

## 確認の深さ（正直な申告）

- **全文を読んだ**: gui.py の設定ダイアログ 1561-2430、キュー 2872-3380・3379-3780、タブ切替・キーボード・起動時処理 5870-6348、main.py のライフサイクル 282-545、unified_download_tab.py の主要操作（200-460, 1026-1075, 1343-1570）、ranking_tab.py の定義一覧とコンテキストメニュー・フィルター定数、library_tab.py の定義一覧とソート/フィルター定数、recommend_tab.py の定義一覧とコンテキストメニュー、artbook_viewer.py のキー・メニュー・状態、tray.py、scheduler.py の構成、server.py のルート一覧
- **定義一覧とラベルの照合まで**（関数本体は未読）: discovery_tab.py 5702 行（学習タブ）、recommend_tab.py 節の内部、library_tab.py の内部、gui.py 重複整理 5025-5860・失敗リスト 4092-4440・直近バックアップ 4488-4810・閲覧履歴ダイアログ 2500-2750。移植版側は ui/*.py のラベルと主要ハンドラの照合。**これらの行の「有」は、画面要素の存在確認であり、挙動の同値確認ではない**
- **未走査**: core.py（ダウンロード本体 3176 行）、database.py、pixiv_client.py、nana7mi_vault.py、style_infer.py、discovery_engine.py/ranking.py の中身（移植版が `discovery_*.py` を持つため構造上は対応）
- 既存資料から取り込んだもの: `ORIGINAL_COMPARISON_FOLLOWUP.md`（D07/D11/D13/D14/L14/M01/M04 の結論）、`OLD45_classification_2026-11-17.md`、`PV_OLD45_INDEX.csv`（旧 ID と操作名）、`PV_DEV_PLAN_2026-11-17.md`（P0〜P6）。**FOLLOWUP の「RC#57 閲覧履歴の入口なし」は現在は古い**（`ui/view_history_view.py` が存在する）ため再確認した

## 共通の判定上の注意

1. `preferences.py` の `PreferenceSpec` があっても、実際に効くのは `services.preference_consumers()`（services.py:1013-1043）が返すキーだけ。選択肢が出ていても適用先が無い起動構成では「適用先が接続されていません」と表示される（ui/preferences_view.py:143-153）。設定項目は全て「条件付きで有」とし、条件を書く
2. 移植版は「専用管理フォルダへ保存し、元の DB・Images を更新しない」設計（shell.py の説明文、`managed_*.py`）。元は DB（works/following_users）と `Images\` 構造へ直接書く。この差は個別の行ではなく D-00 に一括して記す

---

## A. 起動・常駐・ライフサイクル

| ID | 元の操作・機能 | 元の場所 | 移植版の場所 | 状態 | 根拠 / 備考 |
|---|---|---|---|---|---|
| FL-A01 | 多重起動防止（ローカルポート 25011 の排他バインド） | main.py:280-330 | native_instance.py:25-46, 441-447（`.pixivvault-native-instance-v1.json` による所有ファイル方式） | 有 | 方式は違うが 2 重起動拒否（メッセージ native_instance.py:31）。旧 N01 |
| FL-A02 | `--tab <key>` 起動オプション（起動時タブ指定） | main.py:333-342, gui.py:128 `resolve_startup_tab`, 6183 | 無（bootstrap.py の引数一覧 82-133 に無い。`startup_tab` を全 .py で grep 0 件） | 無 | 小。移植版の初期タブは shell.py:36 `active_tab="ranking"` 固定 |
| FL-A03 | 起動直後のタブ＝ダウンロード（統合） | gui.py:98 `TAB_INITIAL` | shell.py:27 `self.active_tab = "ranking"` | 弱 | 元は「ダウンロード」が初期。移植版はランキング初期。意図的かは不明（元に合わせるなら 1 行） |
| FL-A04 | 終了確認ダイアログ「ダウンロードが実行中です。アプリを終了しますか？／アプリを終了しますか？」はい/いいえ | main.py:480-520 | native_lifecycle.py（×は pywebview の確認、`create_confirmation_dialog('終了の確認','処理中または状態を確認できません。終了しますか？')` native_tray_exit 付近 83） | 弱 | 確認は有るが、文言と「実行中のときだけ文面を変える」が違う。常に同じ文言（要実機確認） |
| FL-A05 | 終了時の後片付け（DL 停止 → スケジューラ停止 → 推論ワーカー停止 → トレイ停止 → DB close → 3 秒後強制終了） | main.py:438-476 | native_lifecycle.py:24-93（子プロセスへ終了コマンド・トークン、`_native_exit_freeze`）、services.py:2648 `notifications.close()` | 有 | 設計は別（所有子プロセス方式）。旧 N02 は PASS 済み。実行中停止順は旧 N02 の留保あり |
| FL-A06 | 最小化→トレイ格納（設定 `minimize_to_tray` 既定 1）、トレイ「PixivVaultを開く／終了」 | main.py:496-531, tray.py:30-46 | desktop_integration.py:478-547（pystray、同名メニュー） | 条件付きで有 | `services.tray.available`（pystray があるとき）のみ設定が効く（services.py:1042）。旧 S15/N03/N04 は部分確認 |
| FL-A07 | 復元時にウィンドウを 1280×720 へ戻す | main.py:521-525 | native_layout.py（論理サイズの管理） | 弱 | 別実装。寸法を戻す挙動は未確認 |
| FL-A08 | 起動時に Cookie 有効期限を調べ、期限切れ/残り 1 週間以内ならブラウザ（Chrome/Edge/Firefox）から Cookie を自動抽出 | gui.py:6216-6243 `check_login_status` → `PixivClient.auto_extract_browser_cookies` | 無（`auto_extract_browser_cookies`・`browser_cookie` を grep 0 件） | 無 | 中。PV_DEV_PLAN の P2 に計上済み（偽プロファイル試験）。拡張機能の自動同期（FL-N03）で代替されるのは拡張を入れた人のみ |
| FL-A09 | 別アカウント Cookie 検出時の確認ダイアログ（旧フォロー中データ削除→再同期／キャンセルで再表示抑止） | gui.py:6274-6330 | 無（「別アカウント」を grep 0 件。auth_status.py にも無い） | 無 | 中。P2 に計上済み。元は取り返しのつかない削除のため確認が必須 |
| FL-A10 | Cookie 確認後 24 時間以上経っていればフォロー中を自動同期（`last_sync_date`、公開+非公開） | gui.py:6187-6209 `run_auto_sync_thread`/`trigger_auto_sync` | 無（following_sync_view.py は手動ボタンのみ: 134-143。`last_sync_date` 参照なし） | 無 | 小〜中。P2 に計上済み（「Cookie 設定後の自動フォロー同期」） |
| FL-A11 | ヘッダーのログイン状態行（● 有効 / ▲ 残り1週間 / ▲ 残り3日 / ▲ 通信エラー / × 未認証）と Cookie 未連携バナー（Pixivを開く・閉じる） | gui.py:6247-6272, 2823-2870 | auth_status.py（全体）、native_startup.py:88-294、shell.py:159-200 | 有 | fix-d が取り込み済み（HEAD 4b16f20）。文言・色（GREEN/YELLOW/RED）を元から転記。実画面での目視は未実施。旧 S01 |
| FL-A12 | 起動時の推論ワーカー自動起動（`embed_auto_on_download` が ON なら） | gui.py:6344-6346, 1867-2125 | native_profile_view.py:166 `auto_switch`「保存後に未解析ページを自動解析」、similarity_worker.py、`--similarity-auto` | 弱 | 自動解析スイッチはあるが「次回起動から反映」（native_profile_view.py:167）。元は ON 既定・起動時に即起動。PV_DEV_PLAN 確定判断 2 は「既定を自動実行」としており、現状の既定は False（native_profile_view.py:166）→ 差 |
| FL-A13 | `crash_log.txt` への faulthandler / 例外フック | main.py:25-108 | 無（`faulthandler`/`crash_log`/`excepthook` を移植版 .py（legacy 除く）で grep 0 件。診断用で利用者の操作ではない） | 無 | 小。利用者の操作ではない。優先度低 |
| FL-A14 | カスタム URI `pixivvault://start` で自動起動（レジストリ登録・解除、初回確認ダイアログ） | registry_helper.py:25-67, gui.py:1604-1651 | uri_launch.py:1-30、desktop_integration.py:40-430、ui/preferences_view.py:191-193, 288-303 | 条件付きで有 | `desktop` と uri 経路が接続されたときのみ（未接続時は「URI起動経路が未接続です」preferences_view.py:193）。旧 N06/N07 は部分確認 |

## B. ヘッダー・共通シェル

| ID | 元の操作・機能 | 元の場所 | 移植版の場所 | 状態 | 根拠 / 備考 |
|---|---|---|---|---|---|
| FL-B01 | タブ 9 種（ダウンロード・キュー・失敗リスト・重複整理・学習・ランキング・ライブラリ・おすすめ＋ヘッダーの直近バックアップ） | gui.py:63-120, 6080-6123 | shell.py:11 `NAVIGATION`（12 項目） | 有 | 移植版は定期チェック・メタデータ補完・自分のランキングを別タブ化。差は FL-O01/FL-C20/FL-J11 に記載 |
| FL-B02 | テーマ切替（ライト/ダーク） | gui.py:2749-2781 | shell.py:119, 416 | 有 | トグルと tooltip 切替を確認 |
| FL-B03 | 「ブラウザで Pixiv を開く」ボタン（ログイン確認・自動連携の案内ログ付き） | gui.py:2823-2830 | shell.py:122（リンク `Pixivを開く`） | 有 | 案内ログ行は無し（共有ログ欄が無いため。FL-B08） |
| FL-B04 | 保存フォルダを開く（ヘッダー） | gui.py:2429-2447 | ui/saved_folder_button.py:22-106 | 有 | 複数保存先から選ぶダイアログまで拡張（弱ではなく上位） |
| FL-B05 | 直近のバックアップ（ヘッダーの時計アイコンでタブ表示） | gui.py:2449-2452 | shell.py の `history` タブ | 有 | ナビ位置のみ違う |
| FL-B06 | 閲覧履歴ボタン（件数 tooltip、ダイアログ） | gui.py:2454-2750 | ui/view_history_view.py:18-111 | 有 | ボタン `閲覧履歴`、CSV 出力、全件削除（件数入力）、もっと見る、Pixivで開く を確認。件数バッジ（tooltip の件数）は未確認 |
| FL-B07 | 設定ボタン（ダイアログ。閉じるで全項目保存） | gui.py:2782-2822, 2370-2403 | shell.py:128, ui/preferences_view.py（全体） | 弱 | 保存方式が違う（元は閉じる=保存、移植版は「保存」「変更を破棄して再読込」の明示ボタン＋世代競合検出）。意図的な安全設計だが元の操作とは異なる |
| FL-B08 | 共有ログ欄（各タブ下部、折りたたみ、色分け、タイムスタンプ、タブごと表示切替 `LOG_HIDDEN_TABS`） | gui.py:591-640, 108-122 | 無（共通ログ欄は無い。各画面のステータスラベルで代替） | 弱 | 大。ダウンロード系の詳細ログは `download_status_details.py` 等へ分散。元の「ログ欄」自体は無い。元でも主要タブは非表示（`LOG_HIDDEN_TABS` に 9 タブ全て）のため影響は限定的 |
| FL-B09 | スナックバー通知（成功=緑/警告/エラー） | gui.py:642-680 | NiceGUI `ui.notify`（duplicate_view.py:665 他） | 有 | 形は違うが同等 |
| FL-B10 | 借用プレミアム（旧 nana7mi）トグルとラベル、ON でパスワード/署名キー/UID ダイアログ | gui.py:818-1065 | shell.py:105-117、ui/premium_gate_view.py、external_vault*.py | 条件付きで有 | `external_vault` 構成のときのみ（shell.py:96-113、非構成時は無効スイッチ）。fix-g（ポップアップの原本一致・「有効な状態を保存しますか」廃止・回復操作）は **5ca397a で取り込み済み**（ui/premium_gate_view.py、premium_recovery_view.py、premium_uid_file.py。文言・挙動の実機確認は未実施） |
| FL-B11 | DB 状態バナー（`db_status_banner`） | gui.py:6126 | 無 | 無 | 小。元は gui.py:470-483 で「DB: <パス>」を常時表示し、本番DB なら赤帯「⚠️⚠️ 本番DBを使用中」。移植版に「本番DB/DBを使用」を grep 0 件 |
| FL-B12 | 初回起動時の「ようこそ」類 | 元に無し | なし | — | 台帳対象外（本人未指示の機能は入れない方針） |

## C. 設定ダイアログ（gui.py:1561-2430 全文確認）と config キー

元の構成（2406-2427）: アカウント → 保存ポリシー・通知 → 作品メタデータ → 絵柄・構図の推論 → エクスポート → 詳細設定 → 学習状況 → バージョン表記 `ver 4.0.0 build260913-96c4f24` → 閉じる（幅500・高さ480・スクロール）。

| ID | 元の操作・機能（設定キー） | 元の場所 | 移植版の場所 | 状態 | 根拠 / 備考 |
|---|---|---|---|---|---|
| FL-C01 | Cookie インポート（ファイル選択、状態テキスト） | gui.py:1655-1660, 1076-1112 | ui/native_profile_view.py:123-131, 176 | 有 | 選択→認証確認ボタン。ブラウザ自動抽出 `_run_auto_browser_cookie_picker`(1113-1172) は FL-A08 |
| FL-C02 | フォロー中リストの同期ボタン＋状態 | gui.py:1662-1666, 1201-1226 | ui/following_sync_view.py:134-143, 170 | 有 | 公開/非公開、取消、最終同期表示あり（元に無い上位機能） |
| FL-C03 | 保存フォルダー設定（`save_path`） | gui.py:1668-1672, 1178-1200 | native_profile_view.py:133-140 | 有 | 「作品の保存先（親フォルダ）」。反映は次回起動（native_profile_view.py:169）→ 弱側面あり（元は即時） |
| FL-C04 | ブラウザ拡張機能との自動連携トグル（初回 ON で確認ダイアログ、レジストリ書込） | gui.py:1594-1685 | preferences_view.py:191, 288-303 | 条件付きで有 | FL-A14 と同じ |
| FL-C05 | 起動時に自動チェック（`check_on_startup`、既定 0） | gui.py:1308-1313, 1304 | ui/scheduler_view.py:36 `起動時にもチェック` | 条件付きで有 | `managed_scheduler`（`services.scheduler` 構成）のみ。設定画面ではなく定期チェック画面へ移動 |
| FL-C06 | お気に入り自動チェック間隔（`auto_check_interval_hours` 6/12/24/48/0） | gui.py:1270-1285 | scheduler_view.py:33 | 条件付きで有 | 同上。選択肢の一致は未照合（0=しない は有） |
| FL-C07 | 借用アカウント自動更新間隔（`nana7mi_shaft_refresh_interval_hours` 1/6/12/24/48） | gui.py:1286-1303 | preferences.py:28（spec）、services.py:1038-1040 | 条件付きで有 | `external_vault.set_refresh_interval_provider` があるときのみ。fix-e 取り込み済み |
| FL-C08 | 小説の保存形式 `novel_save_format`（epub/txt/both） | gui.py:1236-1250 | preferences.py:29、services.py:1023、novel_save_presentation.py:21、author_media_view.py:34 | 条件付きで有 | `novel_jobs` 構成のみ |
| FL-C09 | うごイラ保存形式 `ugoira_save_format`（gif/mp4/folder） | gui.py:1251-1265 | preferences.py:30、services.py:1024、author_media_view.py:35 | 条件付きで有 | `ugoira` 構成のみ。`folder` の表記は `frames`（author_media_view.py:35）で内部名が違う可能性（未確認） |
| FL-C10 | 通知 ON/OFF `enable_notifications`（既定 1） | gui.py:1314-1327 | preferences.py:43、services.py:1041、notifications.py | 条件付きで有 | win11toast があるときのみ（preferences_view.py:22 の理由表示）。旧 S09 |
| FL-C11 | ブックマーク保存方式 `bookmark_download_mode`（direct/link） | gui.py:1328-1350 | preferences.py:32、services.py:1022、bookmark_projection.py | 条件付きで有 | `save_exports` 構成。アカウント紐付け後に有効（services.py:1019-1022 コメント） |
| FL-C12 | ダウンロード待機 `download_interval`（0.3/0.4/0.6/1.0/1.5） | gui.py:1396-1412 | preferences.py:34（0.5 を追加） | 条件付きで有 | `set_request_admission` を持つ読取クライアントが接続されたときのみ（services.py:1025-1028）。上位（0.5 追加） |
| FL-C13 | 通信リトライ回数/429 待機 `api_retry_count`/`api_retry_wait` | gui.py:1413-1448 | preferences.py:35-36、services.py:1029-1032 | 条件付きで有 | 旧 S13「429 待機を変更しても待機しない」は C 分類（元に合わせると決定済み） |
| FL-C14 | 学習の先読みキャッシュ（`discovery_cache_limit_mode`/`_size_mb`/`_size_count`） | gui.py:1449-1560 | preferences.py:37-39、services.py:1033-1036 | 条件付きで有 | `shared_image_cache` かつ `discovery` 構成のみ |
| FL-C15 | 全タブ共有画像キャッシュ MB / ディスクサムネイルキャッシュ GB | gui.py:1490-1530 | preferences.py:40-41、services.py:1033-1037, 1049-1051 | 条件付きで有 | `shared_image_cache(.disk)` 構成のみ |
| FL-C16 | サムネイルキャッシュを消去（状態テキスト付き） | gui.py:1351-1395 | preferences_view.py:174-175 `画像キャッシュを削除` | 条件付きで有 | `clear_cache` が渡された構成。旧 S20 部分確認 |
| FL-C17 | 作者ダウンロード後 ZIP 化 `zip_all_after_download` | gui.py:1227-1235 | preferences.py:31、services.py:1018、save_exports.py | 条件付きで有 | `save_exports` 構成 |
| FL-C18 | 最小化時にタスクトレイへ `minimize_to_tray`（既定 1） | gui.py:1343-1347 | preferences.py:42、services.py:1042 | 条件付きで有 | FL-A06 |
| FL-C19 | 作品ごとのフォルダー `use_work_folder` | core.py（16 参照箇所） | preferences.py:27、services.py:1022 | 条件付きで有 | 元は設定画面に出ていない内部キー（gui.py に set なし）。移植版は設定画面へ露出（上位） |
| FL-C20 | メタデータ自動後埋め `meta_backfill_enabled`（既定 OFF）＋「いま埋める」「取れなかったものをもう一度」＋状態「N / M 件（残り…）」 | gui.py:1700-1814, scheduler.py:104-123 | ui/metadata_view.py:26-65（`専用メタデータ台帳を開く`、ON 設定、状態更新、1 作品取得、中断解除） | 弱 | **元の挙動と違う**: 移植版は「設定を ON にしても自動通信はしません。明示実行時のみ保存済み 1 作品を取得」(metadata_view.py:28)。元はスケジューラが ON 時にバックグラウンドで一括後埋め。PV_DEV_PLAN 確定判断 2 は自動既定＋設定で切れること。「いま埋める」(全件)・「再試行」相当は無し。大 |
| FL-C21 | 絵柄・構図の推論（自動 ON 既定、状態、いま実行/リセット/再スキャン） | gui.py:1816-2153 | native_profile_view.py:159-167、ui/similar_view.py:48-64（全保存ページ解析 開始/一時停止/再開）、similarity_worker.py | 弱 | 実行・一時停止・再開は有。**リセット/再スキャン**ボタンは無し（`similar_view` に該当ラベルなし）、自動既定が OFF（FL-A12）。中 |
| FL-C22 | データベースのエクスポート（DB のみ ZIP、約 9MB、結果表示） | gui.py:2315-2368, core.export_data | preferences_view.py:197 `DBと画像をZIPへ保存`、legacy_zip_export.py、legacy_metadata_export.py | 条件付きで有 | 読取専用旧 DB と専用設定保存先の両方が必要（preferences_view.py:197 付近）。内容が違う（画像まで含む＋RESTORE_IMAGES.txt）。旧 E01 部分確認 |
| FL-C23 | 学習状況のリセット（件数入力で解錠、CSV 出力、設定内グループ） | gui.py:2169-2313 | ui/learning_reset.py:30-144 | 有 | fix-e 取り込み済み。確認ダイアログの文言を原本から転記。配置が設定ダイアログ内かは未確認（計画では「元の位置へ移す」） |
| FL-C24 | バージョン表記 `ver 4.0.0 build…` | gui.py:2420 | 無（`__version__` は __init__.py:3 のみで UI から未参照） | 無 | 小 |
| FL-C25 | 起動直後の `grid_card_size_mode`（カードサイズ大/中/小・3 タブ共通） | grid_card_size.py:1-72、grid_card_size_ui.py | preferences.py:26、shell.py:426, ui/card_size_control.py | 有 | 3 サイズ。同期通知 `_notify_other_grid_card_size_tabs`(gui.py:4814) 相当は shell の `change_card_size` |
| FL-C26 | ブラウザ拡張 Cookie 同期受信の ON 状態（`web_bridge_enabled`/`web_api_token` は iOS/Web 用） | gui.py:768-833 | — | 意図的削除 | iOS/Web 連携。`/api/cookie/sync`（拡張機能用）は FL-N03 で別途 |

---

## D. ダウンロード（統合）タブ（unified_download_tab.py / unified_download.py）

| ID | 元の操作・機能 | 元の場所 | 移植版の場所 | 状態 | 根拠 / 備考 |
|---|---|---|---|---|---|
| FL-D00 | 全体設計: 作者単位の一括「差分DL」（`update_date` 比較で新規・更新・削除復帰を自動判定し DB・`Images\` へ書く）。完全DL は 08-14 に画面から廃止 | unified_download_tab.py:1026-1036、core.py（run_batch_backup） | managed_download_view.py:115-133, 803-1010（作者の「差分を確認」→ 作品を選んで「キューへ追加」）、author_media_view.py | 弱 | **設計が異なる**。移植版は「差分を確認→選択→キュー」の 2 段で、専用管理フォルダへ保存し旧 DB を更新しない。元の「実行 1 回で全員を差分DL」に近い操作は `author_media_view.py:62`「選択した全媒体をキューへ追加」だが、DB の `last_downloaded`/`is_zipped`/`is_favorite` は更新されない。大（方針判断が要る） |
| FL-D01 | モード切替「フォロー中の作者／マイブックマーク」 | unified_download_tab.py:1386-1420 | managed_download_view.py:83-91, 235 | 有 | `author_mode_button`/`bookmark_mode_button` |
| FL-D02 | 作者一覧の絞り込み（名前/数字=ID）、前の 50 人/次の 50 人 | unified_download_tab.py:395-418 | managed_download_view.py:144-148, 759-766（`作者をもっと見る`） | 有 | 50 人単位のページ送りは「もっと見る」方式に変更 |
| FL-D03 | 並び替え 5 択（フォロー登録順・作者名順・保存済みの多い順・最後に取得した日・☆を先に）＋昇順/降順 | unified_download.py:51-57, unified_download_tab.py:434-446, 982-1010 | managed_download_view.py:149-153（`follow_order`/`name`/`id` の 3 択＋昇降順） | 弱 | 「保存済みの多い順」「最後に取得した日」「☆を先に」が無く、代わりに「作者ID順」。小 |
| FL-D04 | 表示中を全部選ぶ／選択を全部外す／ヘッダーのチェック（三態） | unified_download_tab.py:907-956 | managed_download_view.py:159-166 | 有 | 「絞り込み結果を全部選ぶ」「選択を全部外す」。三態ヘッダーチェックは未確認 |
| FL-D05 | ☆（お気に入り）の付け外し・☆のみフィルター・「☆を対象にする」・自動監視カード（☆件数） | unified_download_tab.py:878-980, 1513-1520 | managed_download_view.py:155-166, 651-692 | 条件付きで有 | `_favorites_known()`（旧 DB の `is_favorite` が読めるとき）。行ごとの ☆ トグル（書込）は読取専用設計のため要確認。「自動監視」件数カードの有無は未確認 |
| FL-D06 | ID 入力「対象に積む」（作者 ID 直入力） | unified_download_tab.py:389, 790-802, 1016-1025 | managed_download_view.py:115-120（作者ID入力＋「作者を選択」） | 有 | |
| FL-D07 | 単一作品 ID を指定して保存キューへ送る | 元の個別 DL | managed_download_view.py:106-112, 1019-1060（「IDから作品を確認」→選択） | 有 | 旧 U16 は C 分類（元の右クリック入口をカード下ボタンで代替、既定: 元に合わせる）。右クリックからのキュー投入は FL-J09 |
| FL-D08 | 対象種別トグル（イラスト・漫画 → 小説 → 両方 を循環、押下で 120ms 沈む） | unified_download_tab.py:1343-1368 | ui/author_media_view.py:33（対象 select）、unfollowed_authors_view.py:35 | 弱 | 循環ボタンではなく select へ変更。作者単位の対象設定（作者個別設定）は元に無い上位機能（旧 U10）。小 |
| FL-D09 | 実行／一時停止／停止（バッチ全体）、停止直後に「停止しています（いまの作者が終わるまで…）」 | unified_download_tab.py:1026-1075 | job_views.py（ジョブ単位の一時停止・再開・停止 24-25） | 弱 | バッチ全体の一時停止/停止ボタンは無くジョブ単位。「停止中」状態は有（job_views.py:17）。中 |
| FL-D10 | 進捗パネル（N / M 人目、作者内 N/M 作品、経過/ETA、「この回の記録」詳細開閉、ダブルクリック） | unified_download_tab.py:206-330, 1075-1220、gui.py:333-420 `_EtaTimer` | ui/download_status_details.py:45（「個別ジョブの進捗と操作は『キュー』タブで確認できます」） | 弱 | 常設の全体進捗行と ETA は無し。ジョブ行の進捗はキュー側。中 |
| FL-D11 | マイブックマーク: 公開/非公開 の選択チェック（`bm_rest_show/hide`） | unified_download_tab.py:1423-1442, 1271-1280 | bookmark_remote_adapter.py:22 `VISIBILITIES=("show","hide")`（常に両方を取得）、novel_save_view.py:47-48（小説のみ公開/非公開ボタン） | 弱 | イラスト側の公開/非公開の選択 UI は無く常に両方。小 |
| FL-D12 | マイブックマーク: 保存方式「階層化（作者別フォルダ）／フラット配置」トグル | unified_download_tab.py:1440-1460 | preferences.py:32 `bookmark_download_mode`（設定画面のみ） | 弱 | 同じ値を設定画面へ移動。タブ内トグルは無し。小 |
| FL-D13 | ブックマーク一括 DL（`download_bookmarks`、軽い作者は Images 直下に作らない、ハードリンク `enable_bookmark_hardlink` 既定 1） | core.py:2791、unified_download_tab.py:1279-1342 | bookmark_projection.py | 条件付きで有 | アカウント紐付け後の投影（services.py:1019-1022）。`enable_bookmark_hardlink` 設定キーは移植版に無い（常時有効か未確認） |
| FL-D14 | 未フォロー作者を確認（代表作サムネイル付きダイアログ、その場でフォロー、フォロー時の `is_zipped` 初期値＝`auto_archive_new_users`） | gui.py:3775-4090 | ui/unfollowed_authors_view.py:22-42、unfollowed_authors.py | 条件付きで有 | `pixiv_actions` 接続時にフォロー可（view の `follow_actions`）。`auto_archive_new_users` 相当は無し |
| FL-D15 | 保存形式の個別反映: 小説（EPUB/TXT、ルビ・改ページ・挿絵）、うごイラ（GIF/MP4/フォルダ）、ZIP 化 | core.py:1518-1592、epub_builder.py | novel_practical_export.py、ugoira_*.py、author_media.py、save_exports.py | 条件付きで有 | M01 は FOLLOWUP で「仕様差（出力正規化）」と決着。うごイラは `--ugoira-root` 構成のみ |
| FL-D16 | 保存品質自動チェック（0 バイト・ZIP 破損 `testzip`・ページ不足・空 ZIP）→ 失敗キューへ記録、ZIP 破損トースト | core.py:89-112、FEATURES.md §10-2 | failed_records_state.py、legacy_zip_export.py（検証）、ui/failed_records_view.py | 弱 | 失敗の記録・再試行は有（FL-F）。ダウンロード直後の自動品質検査と破損トーストが移植版にあるかは未確認（grep のヒットは検証側のみ） |
| FL-D17 | 削除作品の検知（`mark_as_deleted`、削除復帰の再 DL） | core.py（run_backup）、FEATURES.md §1 | managed_download_view.py:605-619「遠隔欠落（連続N回）」、author_download.py | 有 | 別表現で実装 |

## E. キュー（gui.py:2872-3780）

| ID | 元の操作・機能 | 元の場所 | 移植版の場所 | 状態 | 根拠 / 備考 |
|---|---|---|---|---|---|
| FL-E01 | ジョブ一覧（状態アイコン・名前・状態チップ・進捗テキスト・ID・進捗バー、7 状態スタイル、エラーは破線バー＋左帯） | gui.py:2946-3180 | ui/job_views.py:17-24, 304-360 | 有 | 状態ラベル 7 種を確認（job_views.py:17-18）。帯・破線の視覚は目視未確認（旧 D 分類） |
| FL-E02 | 2 秒ごとの自動更新 | gui.py:3647-3659 | job_views.py:354 `ui.timer(REFRESH_INTERVAL_SECONDS…)` | 有 | |
| FL-E03 | 行ボタン: 一時停止・再開・停止・削除 | gui.py:3047-3056, 3229-3258 | job_views.py:24-25 | 有 | 再試行を追加 |
| FL-E04 | 状態チップで絞り込み、作者名検索、ページ送り | gui.py:3379-3482 | job_views.py:304-346 | 有 | チップ（すべて＋各状態）、検索、前へ/次へ |
| FL-E05 | 選択モード: 「選ぶ」→ チェック、「この絞り込みの全件を選ぶ」、一括 一時停止/再開/停止、まとめて削除（確認）、「選択モードを終了」 | gui.py:3418-3540, 3259-3378 | job_views.py:319-332, 491-566 | 有 | `queue-bulk-{action}` と一括削除確認を確認 |
| FL-E06 | キューログ欄（「ダウンロードキューログ」: 拡張機能連携 DL のログ）。一覧かタイトルのダブルクリックでログと一覧を切替（全画面化） | gui.py:3706-3766, 3683-3689 | 無（job_views.py にログ欄・ダブルクリック展開なし） | 無 | 小〜中 |
| FL-E07 | 見出し「現在のジョブ（アプリ連携）／2 秒毎に自動更新…」 | gui.py:3734-3739 | job_views.py:304 `ui.label("失敗リスト" if failed_only else "キュー")` | 弱 | 見出し・補助文が違う。表示のみ。小 |

## F. 失敗リスト（gui.py:4092-4440）

| ID | 元の操作・機能 | 元の場所 | 移植版の場所 | 状態 | 根拠 / 備考 |
|---|---|---|---|---|---|
| FL-F01 | 失敗の分類チップ・タイトル/ID 検索・ページ送り | gui.py:4092-4200 | ui/failed_records_view.py:90-128, 214 | 有 | |
| FL-F02 | 行: 「タイトル（ID）」「分類・リトライ回数」「再試行」「キューから削除」 | gui.py:4199-4247 | failed_records_view.py:264-277 | 有 | 理由行（`error_reason`）は移植版で表示 |
| FL-F03 | 主ボタン（絞り込み中は「N 件を再試行」）と「その他の操作」: 表示中の N 件を CSV 出力／失敗リストを全件クリア（確認ダイアログ） | gui.py:4143-4144, 4288-4343, 4374-4376 | failed_records_view.py:100-119, 393-399 | 有 | Q17 は fix 済み（e43b408）。旧 Q17/Q13 |
| FL-F04 | 空状態・一致なし状態（検索を消す/すべて） | gui.py:4248-4287 | failed_records_view.py:127-128 | 有 | |
| FL-F05 | 「リスト更新」ボタン | gui.py:4406 | 未確認（`更新` ラベルを failed_records_view で見つけていない） | 弱 | 小。自動更新の可能性あり |

## G. 直近のバックアップ（gui.py:4444-4810）

| ID | 元の操作・機能 | 元の場所 | 移植版の場所 | 状態 | 根拠 / 備考 |
|---|---|---|---|---|---|
| FL-G01 | 作者フォルダ単位でサムネイルを並べる（バックアップ日時順）、クリックで保存先を開く、ホバー表示、カードサイズ（大中小）、更新 | gui.py:4444-4810 | ui/viewer_backup_view.py:9-20, 82（「旧ライブラリのバックアップ日時順・最大60作者」「保存先を開く」「更新」）、`card_size` 引数 | 弱 | ホバー演出は未確認（元の関数本体は未読）。**2026-11-17 に Argon 指摘を元と移植版で再読して検証（一部事実・規模 小）**。事実: 元は作者名バーを最初 opacity=0 にしてホバー時だけフェード表示する正方形タイル（gui.py:4648-4697）、移植版は作者名・題名・日付を常時 3 行で出す別カード（ui/viewer_backup_view.py:71-74）。誤り: 探索範囲は同等（元 gui.py:4545-4569 のフォルダ・☆ブックマーク配下・ZIP、移植版 legacy_backup_history.py:73-125 が ZIP 内日時と最大 60 作者を踏襲）、カードサイズ通知は on_size_change で共有（viewer_backup_view.py:30） |

## H. 重複整理（gui.py:5025-5860）

| ID | 元の操作・機能 | 元の場所 | 移植版の場所 | 状態 | 根拠 / 備考 |
|---|---|---|---|---|---|
| FL-H01 | スキャン（近似画像の距離付き重複グループ） | gui.py:5704-5760 | ui/duplicate_view.py:37、duplicate_inspection.py | 有 | 旧 X01 部分確認 |
| FL-H02 | 表示チップ「決められないもの N／近似重複の候補 N」で切替 | gui.py:5439-5480 | 無（両セクションを縦に並べる: duplicate_view.py:64-72） | 弱 | 小 |
| FL-H03 | 決められないもの（A）の解決: 候補作品を選び「…のものにする」で台帳確定、先頭 50 件のみ表示 | gui.py:5138-5195 | duplicate_view.py:64-70（「作品への対応付け確定は、この読取画面からは実行できません」） | 無 | 中。確定操作が移植版に無い（読取専用設計の意図。元の機能なので退行扱い） |
| FL-H04 | 近似グループ行: サムネイル、画質の低い方に「低画質」バッジ、距離・枚数、チェックボックス、「ごみ箱へ移す」、先頭 50 件 | gui.py:5238-5360, 5395-5438 | duplicate_view.py:74-80, 45 | 有 | 「画質の低い方を一括で選ぶ」「選んだものをごみ箱へ移す」。組ごと 1 枚以上残す規則の文言あり（duplicate_view.py:72）。**2026-11-17 に Argon 指摘を元と移植版で再読して検証（Argon 指摘は大半が誤り・有のまま）**。1 行 5 枚と +N 表示は移植版にある（ui/duplicate_view.py:555, 568「非表示の画像は選択しません」）。一括選択は可視範囲だけ（duplicate_inspection.py:192-194 low_quality_keys は MAX_VISIBLE_ITEMS、元 gui.py:5314-5317 も members[:MAX_THUMBS]）。先頭 50 件（duplicate_inspection.py:26 MAX_VISIBLE_GROUPS=50、元 gui.py:5427 MAX_ROWS） |
| FL-H05 | 移動確認ダイアログ（「この操作では消えません。あとから戻せます。各組には必ず 1 枚以上残ります」） | gui.py:5500-5540 | duplicate_view.py:80-93 | 有 | 隔離方式の説明が増えている（上位）。**2026-11-17 に Argon 指摘を元と移植版で再読して検証（Argon 指摘は一部誤り・有のまま）**。旧フォルダ・ZIP は実移動＋元 ZIP 更新の経路がある（ui/duplicate_view.py:67-70 の注記、legacy_quarantine.py:208-212 execute_physical、journal 付き legacy_physical_quarantine.py）。管理領域内の既定は隔離コピーで、物理移動は確認ダイアログのチェックで選ぶ（duplicate_view.py:94-97）。これは設計差 D-00 で、元より安全側。ZIP エントリ削除の同値は FL-Z02 で別に追う |
| FL-H06 | ごみ箱サマリ（件数・容量）とごみ箱ダイアログ、**空にする（件数入力で解錠）** | gui.py:5604-5702 | duplicate_view.py:97-113（件数・隔離コピー容量・保存先を開く・再読込のみ） | 無 | 中。work に「空にする」無し（`empty_trash`/「空にす」grep 0 件）。**wt-f `c9c77c7` が未取り込み**（P1 計画。HEAD 5ca397a 時点でも未取込） |
| FL-H07 | 重複整理の検索、サムネイルサイズ、ごみ箱の保存先を開く | gui.py:5025-5060, 5761-5800, 5083 | duplicate_view.py:38, 46, 113 | 有 | 旧 X09 は fix 済み |

---

## I. 学習タブ（discovery_tab.py 5702 行。定義一覧・ラベル・判定/キー/トースト部を確認。画面構築の内部は未読）

| ID | 元の操作・機能 | 元の場所 | 移植版の場所 | 状態 | 根拠 / 備考 |
|---|---|---|---|---|---|
| FL-I01 | 判定 4 種（見送る←/A/1、合わない↓/S/2、いいね→/D/3、SuperLike↑/W/4） | gui.py:6027-6063、discovery_tab.py:2585-2770 | ui/learning_view.py:26-28, 280-300（JS の `map`）、173 | 有 | キー対応表が一致。`SuperLike` の 4 つ目は JS 側（learning_view.py:287-291）で確認 |
| FL-I02 | Ctrl+Z で直前の判定を取り消す、取り消すボタン（ツールチップ「直前の判定を取り消す Ctrl+Z」） | gui.py:6047-6050、discovery_tab.py:2510-2553 | learning_view.py:177-178, 236-250（`ctrlKey && z`） | 有 | FOLLOWUP D06 で決着済み |
| FL-I03 | Esc で概要の板を閉じる（他は判定に落とさない） | gui.py:6053-6059 | learning_view.py:280-292 | 有 | |
| FL-I04 | 学習タブ以外ではキーを無視、入力欄・IME 中は無視 | gui.py:6042-6043 | learning_view.py:236-262（`isComposing`/`tag` 判定） | 有 | 要素単位の `keydown`（ページ全体フックではない） |
| FL-I05 | 「合わない」後の理由 2 択チップ「何が合いませんでしたか？ 絵柄／ジャンル」（任意・無視すると「タグだけ下げる」） | discovery_tab.py:1701-1765 | 無（`learning_view.py` に `reason` 表示なし。`learning_repository.py:75-79` と `legacy/database.py:34 VALID_HATE_REASONS` はデータ層のみ） | 無 | **小〜中**。データ層は残っているので UI の追加のみ。好みの学習精度に直結。FOLLOWUP/KNOWN_ISSUES に記録なし（見落とされていた） |
| FL-I06 | 判定直後のトースト帯「判定しました ／取り消す」（5 秒、理由帯と共存） | discovery_tab.py:1528-1700 | learning_view.py:734（`status` に「判定を保存しています…」）、177（常設 Undo ボタン） | 弱 | 時限付きの取り消し帯は無く常設ボタンのみ。小 |
| FL-I07 | 画像が表示されるまで判定不可（`画像を読み込んでいます`／`画像を見ないままの判定はできません`／再試行） | discovery_tab.py:399, 2384-2386, 409-448 | learning_view.py:1050-1069, 179（`画像を再読込`） | 有 | |
| FL-I08 | 画像クリックで画集ビューアを開く／pixiv ページを直接開く | discovery_tab.py:2062, 2446-2456 | learning_view.py:165 `_open_learning_picture`、1643 `view_history.open_work(record=True)` | 有 | 閲覧履歴に記録（`record_discovery_open`）。FOLLOWUP 時点の「接続なし」は解消 |
| FL-I09 | カード下の「ブックマーク」（pixiv へ書込・♥） | discovery_tab.py:2132-2216, 2536-2584 | 無（learning_view.py に bookmark ボタンなし。ビューアを開けば `ブクマ` ボタンあり: artwork_viewer.py:42, pixiv_action_buttons.py:108） | 弱 | 判定画面から直接ブクマ不可、ビューア経由のみ。小〜中（`pixiv_actions` 接続時のみ） |
| FL-I10 | 「作品を保存」（保存対象へ追加） | 元は「ダウンロードキューへ積む」系 | learning_view.py:187（`managed_button`） | 条件付きで有 | 管理保存構成のみ |
| FL-I11 | 好みの傾向パネル（判定 20 枚から。「いまはあなたの保存から出しています」「まだ傾向が出ていません」） | discovery_tab.py:2930-3120, 4229-4242 | learning_view.py:191-193, 1317-1336（「傾向表示は20件から」） | 弱 | 判定すると自動で閉じる（tooltip 同文言: 193）。**2026-11-17 に Argon 指摘を元と移植版で再読して検証（一部事実・規模 小）**。事実: 元はビューポート高さと配置 rail/band/fold で表示件数を増減（discovery_tab.py:2930-3158, 2995-2997）、移植版は全行を 1 列固定で描く（ui/learning_view.py:1342-1356）。誤り: バーの長さは元も合成スコア相対（row["score"]/score_max、discovery_tab.py:2768-2817）で、「最大いいね数比」ではない。行の文言（いいね N・表示 N、MAX バッジ）は同じ |
| FL-I12 | 探索の設定（「知らないジャンルを見る割合」「知らない絵柄を見る割合」スライダー、ドロワー） | discovery_tab.py:3176-3330 | learning_view.py:184, 220-229（`発掘設定` ダイアログ: 数値入力＋保存）、discovery_settings.py | 弱 | 即時スライダー→ダイアログ＋保存ボタンへ UI 変更。FOLLOWUP D07 で「入力粒度・保存表記の違い」と記録済み。検索対象（すべて/全年齢/R-18）の選択は移植版で追加 |
| FL-I13 | 作品の概要（説明文）板、ページ送り、取得失敗で再取得 | discovery_tab.py:3593-4119 | learning_view.py:122-160, 386-408 | 有 | 状態 5 種（ready/none/unavailable/failed/retrying） |
| FL-I14 | 候補の先読み・補充・在庫切れ表示「表示できる候補がありません」「もう一度探す」、取得失敗表示 | discovery_tab.py:977-1232, 4349-4488 | learning_view.py:185（`候補を取得`）、1360-1435（自動補充の状態文）、discovery_refill.py | 有 | 学習タブへ来た時に在庫切れなら再取得する（gui.py:5972）相当は `set_active`（要確認） |
| FL-I15 | 画像タグ補完の案内「ローカルモデルの導入（任意）→設定を開く」（空状態） | discovery_tab.py:4366-4367 | 無 | 無 | 小。空状態の案内のみ（「ローカルモデル／タグ補完」を移植版 .py で grep 0 件） |
| FL-I16 | 振り返り（好意側/否定側の 2 列、今日/前/次、タイトル・タグ検索、未保存だけ/SuperLikeだけ/合わないだけ、取り消す/元に戻す、保存済み、めくる） | discovery_tab.py:5258-5600 | learning_view.py:197-215, 1081-1240 | 弱 | 期間フィルターは拡張（全期間/今日/7日/30日/セッション）。D13/D14 は FOLLOWUP で決着。**2026-11-17 に Argon 指摘を元と移植版で再読して検証（一部事実・規模 小）**。事実: 元の取消は差し戻し結果 requeued/current/absent を 3 通りの文言で出し、在庫切れ画面なら判定画面へ自動復帰（discovery_tab.py:5089-5125, 5283-5321, 5394-5406）。移植版は一律「判定を取り消しました。学習候補を読み直しました。」（ui/learning_view.py:1219）。差し戻し自体は reload_storage(review_queue=...) で行う（同 881-886）。未保存作品の題名・サムネイル遅延取得は FL-Y11 |
| FL-I17 | 閲覧履歴の判定バッジ（SuperLike/いいね/見送り/合わない） | gui.py:2598-2612、FOLLOWUP「RC#57」 | ui/view_history_view.py:8（`LABELS`）、legacy/database.py（JOIN） | 有 | FOLLOWUP の「未移植」は古い。`view_history_view.py` で表示を確認（バッジの描画位置は未確認） |
| FL-I18 | 学習状況のリセット | gui.py:2183-2313 | ui/learning_reset.py | 有 | FL-C23 |
| FL-I19 | 作者連続見送り等の学習ロジック（`discovery_ranking.py` 839 行、`discovery_engine.py` 680 行） | discovery_*.py | discovery_ranking.py:1-839、discovery_engine.py、discovery_population.py、discovery_learning.py | 有 | 構造対応（AST/係数の一致は未走査。FOLLOWUP は D02 の関数本体一致を確認済み）。**2026-11-17 に Argon 指摘を元と移植版で再読して検証（Argon 指摘は検証の結果、誤り・有のまま）**。関数本体の AST 照合（コメント・docstring・logger 呼び出しを除く）: discovery_ranking.py は 30 関数中 29 が一致、1 つの差は import 先のみ。定数 27 個すべて一致。discovery_population.py は 6 関数すべて一致。discovery_engine.py は 14 関数中 10 が一致で、差は _get_db/_get_client（明示注入）・_is_not_found・_source_tag_search の import とログのみ。discovery_description.py は HTML パーサへ書き直されている（FL-I06・FL-Y09 側で確認） |

## J. ランキング（ranking_tab.py 2611 行）

| ID | 元の操作・機能 | 元の場所 | 移植版の場所 | 状態 | 根拠 / 備考 |
|---|---|---|---|---|---|
| FL-J01 | モード 4 種（日間/週間/月間/新人）、モード切替、R-18 は日間・週間のみ（理由表示） | ranking_tab.py:39-50, 404-410 | ranking_source.py:12、ui/ranking_view.py:48, 85-92 | 有 | 旧 K01/K02。R-18 非対応モードの理由文言あり（ranking_view.py:227） |
| FL-J02 | 作品種別（イラスト/マンガ/うごイラ） | ranking_tab.py:51-56, 1302 | ranking_source.py:13（＋小説）、ranking_view.py:50 | 有 | 旧 K04。小説を追加（上位） |
| FL-J03 | 日付選択（DatePicker、「今日」） | ranking_tab.py:1311-1340 | ranking_view.py:52-54, 122-155（カレンダー、YYYYMMDD 入力） | 有 | |
| FL-J04 | 並び替え 4 種（公式順/評価数/閲覧数/伸びしろ） | ranking_tab.py:60-65 | ranking_source.py:14、ranking_view.py:60 | 有 | |
| FL-J05 | R-18 を含める / AI 除外（AI 除外は作品ごとの追加確認のため自動継ぎ足しを止め「もっと見る」に切替） | ranking_tab.py:1319-1328, 988-1015 | ranking_view.py:56-57, 85-92 | 有 | R-18 チェックボックスは ranking_view.py:56（非対応モードで無効化 85-88）。「AI除外で自動継ぎ足しを止めて もっと見る に切替」の挙動までは未確認。**2026-11-17 に Argon 指摘を元と移植版で再読して検証（Argon「挙動が逆転」は誤り・状態は有のまま）**。元の借用タグ検索 search_illust_by_bookmark_count は r18 をリクエストに載せない（pixiv_client.py:1361-1460）ため、借用中に R-18 切替が効くという指摘は事実でない。移植版が借用中に R-18 を disabled にするのは表示だけの差（小）。AI 除外は移植版も作品ごとに詳細で ai_type を確かめ（ranking_source.py:252-256 get_work_info）、自動継ぎ足しなしの「もっと見る」（ui/ranking_view.py:253）で元（ranking_tab.py:988-1015、2026-08-19 に全時「もっと見る」化）と一致 |
| FL-J06 | タグで絞り込み（世間ランキングを pixiv のタグ検索へ切替） | ranking_tab.py:1093-1142 | ranking_view.py:58 | 有 | **2026-11-17 に Argon 指摘を元と移植版で再読して検証（Argon 指摘は J05 と同じ理由で誤り・有のまま）**。借用タグ検索時の R-18 は元も API に送らない（pixiv_client.py:1361-1460）。非借用のタグ検索は元 ranking_tab.py:1115-1117 と同じく mode=r18/safe を送り、移植版 ui/ranking_view.py:84-91 も R-18 を有効のまま扱う |
| FL-J07 | 更新ボタン、無限スクロール/「もっと見る」セル（グリッド最終セル） | ranking_tab.py:1181-1216, 1275 | ranking_view.py:59（取得・更新）、gallery.py:100-110 | 有 | 原版の「最終セル」契約を移植（gallery.py:100 のコメント） |
| FL-J08 | カードのクリックで画集ビューア | ranking_tab.py:1577 | gallery.py:58-62 | 有 | |
| FL-J09 | カードの右クリック（ブックマークする／ダウンロードキューへ積む／出さない／この作者をもっと見る／pixivで作品ページを開く） | ranking_tab.py:763-790 | gallery.py:68-72（「作者ページを開く」の 1 項目のみ） | 弱 | ブクマ・キュー・出さない・もっと見る・pixiv は右クリックに無し（ビューア内の `ブクマ`/`フォロー`/`Pixivで作品を開く` は有）。中 |
| FL-J10 | 出さない一覧の管理（ランキング右上、作品を取り消す・元に戻す、バッジ件数） | ranking_tab.py:632-730, 2505-2580 | 無（`ranking_view.py`/`gallery.py` に hide なし。`hide_work` は recommend/personal/services/discovery_* のみ） | 無 | 中。ランキングで作品を非表示にする入口自体が無い。KNOWN_ISSUES の N45-B（取得停止解除手段）とは別 |
| FL-J11 | 「自分のランキング」モード（ランキングタブ内で世間/自分を切替、選択タグ・フィルター・カード・よく効くタグ・タグ AND 検索） | ranking_tab.py:2397-2430, 1436-2400 | ui/personal_ranking_view.py:26-50、shell.py の `personal` タブ（`external_vault` または `live_client` が無いと非表示: shell.py ナビ生成部） | 条件付きで有 | ランキングタブ内の切替から独立タブへ変更。旧 K09。タグ入力が「1 行に 1 件のテキストエリア」でチップ＋Enter 追加ではない（弱の側面）。R-18 は原 API 未対応で無効（personal_ranking_view.py:31）→ 元でも同じかは未確認 |
| FL-J12 | 自分のランキングの絞り込み（ブクマ数下限プリセット、並び順、投稿日、含む/除外タグ、1 作者の上限、保存済み/非表示作者/非表示作品/合わない を除外、条件の保存・クリア、有効条件チップ） | ranking_tab.py:1921-2350, 165-346 | personal_ranking_view.py:27-50 | 弱 | `ranking_personal_filters` に相当（保存: `この条件を保存`）。有効条件チップの表示は未確認。**2026-11-17 に Argon 指摘を元と移植版で再読して検証（事実・規模 中）**。差: (1) 有効条件(NOW)チップ行（1 クリックで個別解除）が移植版に無い。元 ranking_tab.py:2315-2350 _personal_active_chips、移植版 ui/personal_ranking_view.py:27-63 にチップ UI なし。(2) ブクマ数下限・並び順・「条件をクリア」で再検索が走らない。元 ranking_tab.py:1935-1947, 2012-2022 は re_search=True、移植版 168-184 は _show_unfetched() で「未取得」へ戻し「取得・更新」待ち。(3) R-18 スイッチは元が既定 ON（ranking_tab.py:2135）、移植版は常時 disabled・既定 False（personal_ranking_view.py:32）。ただし元も R-18 を API へ送らない（pixiv_client.py:1361-1460 の params に r18 無し）ので検索結果は同じで、差は表示だけ。保存・含む除外タグ・上限・除外 4 種・クリアは有 |
| FL-J13 | タブ初回表示で pixiv ランキングを自動取得（`on_tab_open`） | ranking_tab.py:1387-1435 | ranking_view.py:283-289（`set_active` → `_schedule_first_load`）、fix-d df78ff0 | 有 | 実画面確認は未実施 |
| FL-J14 | 画集ビューアをタブ離脱時に閉じる | gui.py:5987-6002 | gallery.py:`set_viewer_active`（learning_view と同形: viewer_controller.py:95-97） | 有 | |

## K. ライブラリ（library_tab.py 3216 行）

| ID | 元の操作・機能 | 元の場所 | 移植版の場所 | 状態 | 根拠 / 備考 |
|---|---|---|---|---|---|
| FL-K01 | サブビュー切替（作者一覧「DL済み」／マイブックマーク／作者詳細 ← 作者一覧） | library_tab.py:2985-3056, 1758-1797 | ui/library_view.py:100-110, 161-177 | 有 | |
| FL-K02 | 検索（タイトル・作者・タグ）とスコープ切替「ライブラリのみ／pixiv本体も含む」＋再検索 | library_tab.py:2279-2360, 583-650 | library_view.py:110-117（「ライブラリ内」固定ラベル＋「人気順で探す」ボタン） | 弱 | pixiv 全体検索は「人気順で探す」（借用）に置換され、通常 Cookie でのスコープ切替は無し。中 |
| FL-K03 | 並び替え（閲覧数・保存日・容量、各方向トグル） | library_tab.py:92-96, 2370-2400 | library_view.py:300-310 | 有 | |
| FL-K04 | 絞り込みパネル 8 席（並び順・期間・ブクマ数・タグ含む・タグ除く・作者あたり上限・除外・種別/形式） | library_tab.py:2410-2990、filter_panel.py | ui/library_filter_panel.py:127-200 | 弱 | 8 席の表示と「ここには無い →／仕様上不要」を踏襲。作者フィルター・未読のみ・R-18・AI・未取得のみ・タグ＋/−・どれか/すべて・日付プリセット（1年/3年/期間指定/すべて）・ブクマ数プリセットを確認。**2026-11-17 に Argon 指摘を元と移植版で再読して検証（一部事実・規模 小）**。事実: 元は借用検索（ライブラリ内の結果）に AI 除外切替が再検索を走らせる（library_tab.py:2819-2826, 596-615）。移植版は借用検索がライブラリ内でなく「人気順で探す」ボタンから人気順タブを開く別操作（ui/library_view.py:116-117、ui/shell.py:340）。誤り: タグチップの ＋/− 反転は有（ui/library_filter_panel.py:457, 533-539）、席 01 の elsewhere／unavailable 切替も有（同 427-429） |
| FL-K05 | 容量表示と再計測（キャッシュ消去して計測し直す） | library_tab.py:413-495, 1297-1313, 2978 | library_view.py:148-150, 424-559 | 有 | 管理保存と作者保存先（旧）を分けて再計測（上位） |
| FL-K06 | マイブックマーク: 一覧取得・選択チェック・「選択をダウンロード」・更新・もっと見る・最終更新時刻の表示・タブを開いた時の自動更新（120 秒間引き） | library_tab.py:224-395, 1881-2160 | ui/bookmark_view.py:56-130（再読込、選択、`選択した作品をDL`） | 弱 | 「最終更新: YYYY-MM-DD HH:MM」表示（library_tab.py:1881-1888）と、タブを開いた時の自動更新（2115-2155）は無し。旧 L17「ライブラリを明示更新する」は C 分類で「追随しない・手動更新」が既定案（FOLLOWUP/OLD45）。小 |
| FL-K07 | 「DL済み」バッジ、ブクマカードのクリック＝ビューア、右クリック「似た絵を探す」 | library_tab.py:1229-1440, 1805-1836, 1149-1168 | bookmark_view.py:112-130, library_view.py:924 | 有 | |
| FL-K08 | 作者カード: クリック＝作者の作品一覧、右クリック＝似た絵を探す／保存先を開く、pixiv 作者ページ | library_tab.py:1229-1296, 909-956 | library_view.py:823-850 | 有 | 右クリックは明示ボタン化 |
| FL-K09 | 作品カードから「ローカルで開く」（既定アプリ／Honeyview）、作品を開くと閲覧数加算 | library_tab.py:805-889 | library_view.py:918-943（`外部ビューアで開く`）、desktop_integration.py:443-461 | 条件付きで有 | `launcher` 接続時のみ。閲覧数加算の記録は未確認 |
| FL-K10 | 似た絵を探す（全ページ母集団、類似度 0.50 以上、p.N 表示、閉じる/再試行） | library_tab.py:957-1148 | ui/similar_view.py、managed_similarity.py | 条件付きで有 | モデル（EgaraNet）とインデックスが接続された構成のみ（native_profile_view.py:159-169）。旧 W01 部分確認 |
| FL-K11 | 作者詳細（全ページ、ページ数不明は 1 枚、容量、作者あたりの p.N から開く） | library_tab.py:1474-1800 | library_view.py:1028-1041 | 有 | |
| FL-K12 | カードサイズ（大中小） | library_tab.py:2156, 3056-3106 | library_view.py（card_size 引数） | 有 | |
| FL-K13 | 保存済み小説を開く（M04） | library_tab.py:859-887 | ui/novel_save_view.py:366-440 | 有 | FOLLOWUP M04（内部リーダーで置換。仕様差）。元の「既定アプリで開く」とは異なる |
| FL-K14 | `work_actually_saved`（作品が実際に保存済みか判定） | library_tab.py:76-125 | library_repository.py、library_media_registry.py | 有 | 構造対応のみ確認 |

## L. おすすめ（recommend_tab.py 3979 行）

| ID | 元の操作・機能 | 元の場所 | 移植版の場所 | 状態 | 根拠 / 備考 |
|---|---|---|---|---|---|
| FL-L01 | 5 つの節（追加した傾向／好みの傾向／好みの傾向から（確実）／もっと見る登録作者の新着／SuperLike した作品）、節ごとの開く/畳む、「もっと見る」ページ送り | recommend_tab.py:876-2500 | recommendation_state.py:19-21（ORDER/LABELS）、ui/recommend_view.py:321-450 | 有 | |
| FL-L02 | 節の並び替え（上へ/下へ/既定に戻す）と状態保存（`recommend_section_order`/`_collapsed`/`_certain_group_collapsed`/`_trend_added_tags`） | recommend_tab.py:2794-3100 | recommend_view.py:217-235, 351（`move`/`collapse`/`reset_order`）、recommendation_state.py:160-166 | 弱 | **2026-11-17 に Argon 指摘を元と移植版で再読して検証（事実・規模 中）**。節の並べ替えと節ごとの折りたたみ（上へ/下へ/既定に戻す）は有（ui/recommend_view.py:217-237, 362-396）。無いのは「確実枠」内の個別タググループごとの開閉 recommend_certain_group_collapsed（元 recommend_tab.py:1860-1914）。移植版 src に同名キー・同機能は grep 0 件 |
| FL-L03 | 更新（取得済みの内容を取り直す）／初回読込（保存結果）／取得件数の表示 | recommend_tab.py:2656-2700 | recommend_view.py:34-37, 162-172 | 有 | R01a/R01b/R07 は fix 済み（5871aae）。旧 R01b は「更新後の節数・作品数」まで |
| FL-L04 | 傾向ピル（タップで追加/外す、「このタグを開く」） | recommend_tab.py:921-961, 2193-2260 | recommend_view.py:173-176, 359-367 | 弱 | **2026-11-17 に Argon 指摘を元と移植版で再読して検証（事実・規模 小）**。タップで追加/外すは有（ui/recommend_view.py:173-176, 362-396）。無いのは順位別 4 段階のサイズと枠線太さ _TREND_TIERS、2 行クリップ、+N 残数表示（元 recommend_tab.py:238-243, 893-949）。移植版は同じ大きさの outline ボタンを並べるだけ |
| FL-L05 | カードの右クリック 12 項目（ブックマークする／好み（保存する）／取り下げる／出さない／出さない（理由）／DL キューへ積む／この作者をもっと見る／この作者を出さない／このタグで検索／作者の作品一覧へ／pixivでフォロー／pixivで作品ページを開く） | recommend_tab.py:815-876 | recommend_view.py:404-436（インラインボタン: 保存済みを開く／キューに追加／SuperLike取り下げ／出さない／作者を出さない／登録解除・作者をもっと見る／Pixivで作品を開く／作者ページを開く） | 弱 | 「ブックマークする」「このタグで検索」「pixivでフォロー」「好み（保存する）」が無し（ビューアに `ブクマ`/`フォロー` は有）。「このタグで検索」は元で個人ランキングのタグへ遷移（gui.py:4857）。中 |
| FL-L06 | 出さない理由ダイアログ（ジャンル/絵柄/選ばない） | recommend_tab.py:630-645 | recommend_view.py:42-52, 191-200 | 有 | |
| FL-L07 | 出さない/もっと見る/出さない作者の一覧管理（オーバーレイ、元に戻す）、出さないの取り消し | recommend_tab.py:1194-1430, 2780-2800, 3800-3870 | recommend_view.py:41, 211-216, 451-458 | 有 | 「出さない一覧」展開で元に戻す。もっと見る登録解除は 370 |
| FL-L08 | SuperLike の取り下げと取消 | recommend_tab.py:1093-1130, 1230 | recommend_view.py:150-160, 428 | 有 | R01a/R07 |
| FL-L09 | 空状態「おすすめの材料がまだありません→学習タブへ」ボタン | recommend_tab.py:2895-2905 | 無（recommend_view.py:373-376 は文言のみ。遷移ボタン無し） | 弱 | 小 |
| FL-L10 | 作者の作品一覧オーバーレイ（ブックマーク・DL・差分DL・pixivでフォロー） | recommend_tab.py:3591-3736 | recommend_view.py:265-290 `open_author_page`、ui/author_browse_view.py:89-104 | 条件付きで有 | 読取接続の設定が必要（recommend_view.py:272）。「差分DL」は作者ブラウザの展開（author_browse_view.py:99）。旧 R08/R10/R16/R17 は部分確認 |

## M. 画集ビューア（artbook_viewer.py 1494 行）

| ID | 元の操作・機能 | 元の場所 | 移植版の場所 | 状態 | 根拠 / 備考 |
|---|---|---|---|---|---|
| FL-M01 | キー: →次ページ／←前／i・Tab で詳細／Esc（詳細が開いていれば詳細だけ閉じ、閉じていればビューアを閉じる） | artbook_viewer.py:1100-1146 | ui/viewer_controller.py:71-80, 82-83 | 有 | `['arrowleft','arrowright','i','tab','escape']` |
| FL-M02 | 詳細パネル（説明・タグ・ブクマ/閲覧/いいね数・遅延取得） | artbook_viewer.py:1333-1440 | ui/artwork_viewer.py:114-115, 384-386 | 有 | 投稿日・ブクマ・閲覧・解像度を表示（artwork_viewer.py:386）。説明文・タグは別実装（viewer_metadata.py） |
| FL-M03 | ヘッダー 3 ボタン（ブクマ／フォロー／個別 DL）と状態表示 | artbook_viewer.py:587-760, 928-1100 | artwork_viewer.py:41-43、pixiv_action_buttons.py:101-300 | 条件付きで有 | ブクマ・フォローは `pixiv_actions` 接続時（実状態確認付き、上位）。個別 DL は `_unavailable_action`（artwork_viewer.py:41, 127-131）＝保存先未接続では無効 |
| FL-M04 | 右クリックメニュー 4 項目（pixivで作品ページを開く／作者ページを開く／出さない／保存フォルダを開く） | artbook_viewer.py:849-870 | artwork_viewer.py:44-48（`⋮` に「Pixivで作品を開く」）、author_button（109-112）、ヘッダーの保存フォルダ（FL-B04） | 弱 | 「出さない」がビューア内に無し。保存フォルダはヘッダー側。小 |
| FL-M05 | うごイラ再生（GIF 化）・個別 GIF 保存 | artbook_viewer.py:55-95, 371-440, 1028-1100 | ui/ugoira_view.py、viewer_gif.py、ugoira_gif.py | 条件付きで有 | 旧 M10 部分確認 |
| FL-M06 | ズーム・パン | （元の画集ビューアに専用実装は未確認） | artwork_viewer.py:70-80, 276-283 | 有（上位） | 旧 V08 は物理マウス確認が必要（E 分類） |
| FL-M07 | 原寸表示 | 元に無し | artwork_viewer.py:37-39 | — | 台帳対象外（上位） |

## N. 拡張機能連携・ローカルサーバー（server.py 1735 行、ポート 25010）

| ID | 元の操作・機能 | 元の場所 | 移植版の場所 | 状態 | 根拠 / 備考 |
|---|---|---|---|---|---|
| FL-N01 | `GET /ping`、`GET /api/work/{id}`（保存済み状態）、`GET /api/user/{id}/status` | server.py:294-330 | extension_bridge.py:221-250、extension_application.py:284-300 | 弱 | **`--extension-bridge` 指定時のみ起動**（bootstrap.py:122, 603-604。引数なしの通常起動では受信サーバーが立たない）。元は常時起動。**profile 起動でも立たない**: `native_kwargs`（native_profile.py:257-290）は `extension_bridge` を渡さない。**条件が厳しい**ため 弱 寄り（`launch_work.ps1` が付けているかは未確認） 【`--extension-bridge` が無い通常起動・launch_work.ps1/launch_native.ps1 では受信サーバーが起動しない（bootstrap.py:603-604、launch_work.ps1:18）】 |
| FL-N02 | `POST /download`（作品/作者を保存キューへ。重複キュー登録を防止） | server.py:612、1680-1700 | extension_bridge.py:221、extension_application.py:71-258 | 弱 | 同上。旧 N09/N10 は E 分類（専用拡張・URI 登録が必要） 【`--extension-bridge` が無い通常起動・launch_work.ps1/launch_native.ps1 では受信サーバーが起動しない（bootstrap.py:603-604、launch_work.ps1:18）】 |
| FL-N03 | `POST /api/cookie/sync`（拡張が Cookie を自動同期。UI ヘッダー更新） | server.py:631-660 | extension_bridge.py:221, 280、extension_cookie_store.py、native_startup.py:235-240 | 弱 | fix-d で「拡張機能受信時の確認」を取り込み。**Cookie 同期成功時の緑（#00FF66）キューログ通知**（FEATURES.md §10）は移植版で未確認 【`--extension-bridge` が無い通常起動・launch_work.ps1/launch_native.ps1 では受信サーバーが起動しない（bootstrap.py:603-604、launch_work.ps1:18）】 |
| FL-N04 | Origin 検証（`chrome-extension://` と、ループバックからの `https://pixiv.net`・`https://*.pixiv.net`） | server.py:21-26, 222-235, 257-272（`_is_allowed_extension_origin`・`do_OPTIONS` の CORS 応答・`_check_origin`） | extension_bridge.py（`allowed_origin`:74-80、`path` 判定前に検証・204-205、CORS 応答 225-228） | 有 | **2026-11-17 に Argon 指摘を元と移植版で再読して検証（Argon 指摘は事実。台帳の仕様記述を訂正・移植版は元と同値で有のまま）**。元の仕様は chrome-extension:// に加えて、ループバックからの https://pixiv.net と https://*.pixiv.net も許可（server.py:21-26, 222-235。content.js の直接 fetch フォールバック用）。移植版 extension_bridge.py:74-80 allowed_origin も同じ 2 系統（拡張 ID は 32 文字・pixiv.net は :443 のみ許可）で、Origin が 1 件でない要求も拒否（同 204-205）。移植版は実装を読んで確認した |
| FL-N05 | DL 開始/完了/失敗の Windows トースト通知（拡張機能経由・設定 `enable_notifications`） | server.py:1693-1696 | 無（notifications.py は `services.py:2890` の定期チェック新規保存通知のみ利用） | 弱 | 中。拡張機能ジョブの完了通知と ZIP 破損通知（core.py:112）が無い |
| FL-N06 | 拡張機能の「保存済み ✅」表示の元となる `GET /library/*` | server.py:306, 380-480 | — | 意図的削除 | `/library/*`・`/jobs`・`/progress/*`・`/bookmark`・`/jobs/bulk/*` は iOS/Web 専用（server.py:28-55, 301, 617-626 のコメント）。拡張機能は `/ping`・`/api/work`・`/api/user`・`/download`・`/api/cookie/sync` のみ |
| FL-N07 | 設定の「拡張機能の受付状況」表示（応答の内訳） | 元に無し | preferences_view.py:169-176 | — | 台帳対象外（上位） |

## O. 定期処理・スケジューラ（scheduler.py 242 行）

| ID | 元の操作・機能 | 元の場所 | 移植版の場所 | 状態 | 根拠 / 備考 |
|---|---|---|---|---|---|
| FL-O01 | 定期チェック（お気に入り作者を間隔で自動チェック→新規保存を通知）。対象＝DB の `is_favorite=1` 作者全員 | scheduler.py:56-230 | managed_scheduler.py、ui/scheduler_view.py:29-50（対象は「お気に入り作者ID（カンマ区切り、最大128件）」を手入力。台帳 `managed_scheduler.py:116` が独自の `favourite_authors` を持ち、旧 DB の `is_favorite` とは連動しない（services.py の `is_favorite` 参照は作者一覧の ☆ 表示のみ: 1849, 1864））、services.py:2886-2890 | 弱 | **対象の決め方が違う**。元は ☆ を付けた全員（FL-D05）を自動で対象。移植版は画面で ID を手入力（最大 128 件）。☆ との連動が無ければ元の運用はできない。中。旧 T02（履歴・状態）は scheduler_view.py:49-50 で有 |
| FL-O02 | 起動時チェック（`check_on_startup`） | gui.py:1308、scheduler.py | scheduler_view.py:36 | 条件付きで有 | FL-C05 |
| FL-O03 | 実行中ダウンロードがある間はスケジュールをずらす | scheduler.py:91 `_download_is_running` | managed_scheduler.py（`resume`/`pause` 系）、services.py | 有 | 構造対応のみ確認（本体未読） |
| FL-O04 | 借用アカウントの自動更新（間隔設定、起動ごとに叩かない） | scheduler.py:125-175 | external_vault.py:144-175、services.py:1038-1040 | 条件付きで有 | fix-e 取り込み済み |
| FL-O05 | メタデータ後埋めの自動実行（`meta_backfill_enabled` ON 時） | scheduler.py:104-123 | metadata_view.py:28（自動通信しない） | 弱 | FL-C20 と同一原因。重複計上しない |
| FL-O06 | 起動 N 時間ぶんの未実行時の扱い・設定変更で待機を起こす（`on_settings_changed`） | scheduler.py:43-90 | scheduler_view.py:42-44（保存／破棄／今すぐ） | 有 | |

## P. 意図的削除（退行に数えない）

| ID | 元の機能 | 元の場所 | 状態 | 備考 |
|---|---|---|---|---|
| FL-X01 | iOS 連携（接続ラベル、最終接続時刻、トークン再生成） | gui.py:692-834、server.py:28-55, 301-626 | 意図的削除 | PV_DEV_PLAN 確定判断 1 |
| FL-X02 | Web 連携（LAN 公開サーバー、`web_bridge_enabled`、`web_api_token`、`/progress`） | gui.py:768-833、server.py | 意図的削除 | 同上 |
| FL-X03 | Shaft 連携タブ（署名キー・UID・状態の専用タブ） | 元の `nana7mi_vault.py` 関連 UI | 意図的削除 | 署名キー・UID 読込は借用 ON のポップアップへ移設済み（17cb109）。残りは FL-B10 |
| FL-X04 | 旧保管庫取込 | — | 意図的削除 | 本人決定（依頼文）。元のコード位置は未特定 |

---

## Q. 旧 45 ID（PV_OLD45_INDEX.csv）との対応

「旧 ID → 本台帳の行」。旧 ID の判定は変えない（OLD45_classification の A〜E 分類を尊重）。本台帳が追加で見つけた差は新行（FL-*）として別に数える。

| 旧 ID（操作） | 対応行 | 備考 |
|---|---|---|
| D01 学習候補を明示取得 | FL-I14 | 部分確認のまま |
| R01a/R01b/R07 おすすめ再読込・更新・SuperLike 取り下げ | FL-L03, L08 | fix 済み（5871aae）。実機未確認 |
| R08/R10 非表示・元に戻す | FL-L06, L07 | |
| R16/R17 ブクマ・フォロー | FL-M03, L05, I09 | カード直下のブクマ/フォローは FL-L05/I09/J09 で弱 |
| K01/K02/K04 ランキング切替・期間・種別 | FL-J01, J02 | |
| K09 個人ランキングのタグ編集 | FL-J11, J12 | |
| L14/L16/L17 ブクマ一覧・一括 DL・更新 | FL-K06 | L14 は FOLLOWUP で決着 |
| U10/U11 作者別媒体・ZIP 設定 | FL-D08（author_media_view.py:40-54） | 上位機能 |
| U16 単一作品 ID | FL-D07, J09 | C 分類（元に合わせる）→ 右クリック入口は J09 で弱 |
| Q13/Q17 失敗リスト・一括再試行 | FL-F01-F03 | Q17 fix 済み |
| X01/X09 重複・ごみ箱の保存先 | FL-H01, H07 | X09 fix 済み。**ごみ箱を空にする（FL-H06）は旧 ID に無く新規発見** |
| S01/S03/S04 Cookie・フォロー同期・保存先 | FL-C01-C03, A11 | 自動同期系は FL-A08-A10 |
| S09/S13/S15/S20 通知・429 待機・トレイ・キャッシュ消去 | FL-C10, C13, C18, C16 | |
| S22 ブラウザで Pixiv を開く | FL-B03 | |
| E01 DB エクスポート | FL-C22 | 内容は元と違う（画像まで含む） |
| V06/V08 pixiv ページ・パン | FL-M04, M06 | |
| W01 類似画像検索 | FL-K10 | |
| T02 スケジューラ履歴 | FL-O01 | 対象指定の違いは新規発見 |
| M01/M10 小説・うごイラ | FL-D15, M05 | |
| N01/N03/N04 起動・トレイ・復帰 | FL-A01, A06 | |
| N06/N07 URI 登録・解除 | FL-A14 | |
| N09/N10 拡張機能の受付・状態 | FL-N01-N03 | **既定で受信サーバーが起動しない**（新規発見） |
| Z04 Shaft で個人検索 | FL-B10, J11 | fix 済み（c454b12） |

---

## R. 件数

- 総行数 171（うち対象外 3「—」: FL-B12, FL-M07, FL-N07。スクリプトで集計）
- 有 75（上位 1 含む）／条件付きで有 33／**弱 41**／**無 13**／意図的削除 6（FL-C26, FL-N06, FL-X01〜X04）。2026-11-17 の Argon 検証で FL-J12・G01・I11・I16・K04・L02・L04 の 7 行が 有→弱（下の「Argon 検証による更新」）
- 弱 のうち FL-O05 は FL-C20 と同一原因（二重計上）。実質 弱 40
- **無 13 ＋ 弱 40 ＝ 53 ID**。規模の内訳は 小 33／中 17／大 3（大＝FL-D00・FL-C20・FL-B08）。小は表示・文言・既定値・数行の追加。追記台帳（FL-Y・FL-Z）の無・弱は FEATURE_LEDGER_ADDENDA を参照

## S. 無・弱 の一覧と規模見積り（小＝1 ファイル・半日以内／中＝複数ファイルまたは新規 UI／大＝設計判断が要る）

### 無（13）

| ID | 内容 | 規模 | 備考 |
|---|---|---|---|
| FL-A08 | 起動時のブラウザ Cookie 自動抽出 | 中 | P2。偽プロファイル試験 |
| FL-A09 | 別アカウント Cookie 検出ダイアログ | 中 | P2。削除を伴うため確認が必須 |
| FL-A10 | 24 時間ごとのフォロー中自動同期 | 小 | P2 |
| FL-A02 | `--tab` 起動オプション | 小 | |
| FL-A13 | `crash_log.txt`（faulthandler） | 小 | 診断用 |
| FL-B11 | DB 状態バナー | 小 | 元の内容は未読。要確認 |
| FL-C24 | バージョン表記 | 小 | |
| FL-E06 | キューログ欄と一覧の切替（ダブルクリック） | 小 | |
| FL-H03 | 重複「決められないもの」の確定操作 | 中 | 読取専用設計との調整が要る |
| FL-H06 | ごみ箱を空にする（件数入力で解錠） | 小（取込のみ） | wt-f `c9c77c7` に実装あり・未取り込み。P1 |
| FL-I05 | 「合わない」の理由 2 択（絵柄/ジャンル） | 小 | データ層 `VALID_HATE_REASONS` は在る。学習精度に直結 |
| FL-I15 | 空状態の「ローカルモデル導入」案内 | 小 | |
| FL-J10 | ランキングの「出さない」入口と管理 | 中 | 「出さない」データ層は既存（recommend と共有） |

### 弱（33）

| ID | 内容 | 規模 |
|---|---|---|
| FL-D00 | 作者一括「差分DL」の実行モデルと DB 更新（`last_downloaded` 等）の差 | **大**（方針判断。専用管理フォルダ設計との折り合い） |
| FL-C20（＋O05） | メタデータの自動後埋め（元はバックグラウンド、移植版は明示実行のみ） | **大**（PV_DEV_PLAN 確定判断 2「自動既定＋設定で切れる」と食い違い） |
| FL-N01〜N03 | 拡張機能の受信サーバーが `--extension-bridge` なしでは起動しない | 小（既定 ON 化）。ただし影響は大 |
| FL-O01 | 定期チェックの対象が ☆ ではなく手入力 ID（最大 128） | 中 |
| FL-C21 / A12 | 推論の自動既定が OFF、リセット/再スキャン無し | 中 |
| FL-B08 | 共有ログ欄が無い | 大（ただし元でも主要タブは非表示） |
| FL-D09 | バッチ全体の一時停止/停止・停止中表示 | 中 |
| FL-D10 | 常設の進捗行・ETA | 中 |
| FL-D16 | 保存直後の自動品質検査・ZIP 破損通知 | 中 |
| FL-J09 | ランキングカードの右クリック 4 項目が無い | 中 |
| FL-L05 | おすすめの右クリック 12 項目のうち 4 つが無い | 中 |
| FL-I09 | 学習画面から直接ブックマークできない | 中 |
| FL-K02 | ライブラリの「pixiv 本体も含む」スコープ | 中 |
| FL-N05 | 拡張機能ジョブ・ZIP 破損の Windows 通知 | 中 |
| FL-A03 | 初期タブが「ダウンロード」ではなく「ランキング」 | 小 |
| FL-A04 | 終了確認の文言（実行中で変える）が違う可能性 | 小 |
| FL-A07 | 復元時のウィンドウ寸法 | 小 |
| FL-B07 | 設定が「閉じる=保存」ではなく明示保存（意図的な安全設計と思われる） | 小（判断） |
| FL-D03 | 並び替え 5 択のうち 3 つが無い | 小 |
| FL-D08 | 対象種別が循環ボタンではなく select | 小 |
| FL-D11 | ブクマの公開/非公開の選択が無い（常に両方） | 小 |
| FL-D12 | 階層/フラットの切替がタブ内に無い（設定のみ） | 小 |
| FL-E07 | キュー見出し・補助文 | 小 |
| FL-F05 | 失敗リストの「リスト更新」 | 小 |
| FL-H02 | 重複整理の切替チップ | 小 |
| FL-I06 | 判定直後の時限トースト帯 | 小 |
| FL-I12 | 探索設定がスライダーではなくダイアログ＋保存 | 小（FOLLOWUP D07 で記録済み） |
| FL-K06 | ブクマ一覧の最終更新表示と自動更新（120 秒間引き） | 小（L17 は C 分類） |
| FL-L09 | おすすめ空状態の「学習タブへ」 | 小 |
| FL-M04 | ビューアの右クリックに「出さない」が無い | 小 |

### Argon 検証による更新（2026-11-17。有→弱へ直した 7 行）

元と移植版の該当箇所を読み直した結果。根拠は各行の備考欄に file:line つきで書いた。

| ID | 内容 | 規模 |
|---|---|---|
| FL-J12 | 自分のランキング: 有効条件チップ行が無い／ブクマ数・並び順・クリアで自動再検索しない／R-18 は表示上 disabled | 中 |
| FL-L02 | おすすめ: 確実枠の個別タググループの折りたたみ（`recommend_certain_group_collapsed`）が無い | 中 |
| FL-L04 | おすすめ: 傾向ピルの順位別サイズ・枠線・2 行クリップ・`+N` が無い | 小 |
| FL-G01 | 直近のバックアップ: ホバーで出る作者名バーと正方形タイルが無い（常時ラベルの別カード） | 小 |
| FL-I11 | 学習: 好みの傾向パネルの表示件数の増減と 3 列配置（rail/band/fold）が無い | 小 |
| FL-I16 | 学習: 取消の差し戻し結果ごとの文言 3 通りと在庫切れ画面からの自動復帰が無い | 小 |
| FL-K04 | ライブラリ: 借用検索がライブラリ内でなく人気順タブを開く別操作 | 小 |

検証の結果、有のまま残したもの: FL-J05・J06（R-18 は元も API へ送らない）、FL-H04・H05（5 枚＋`+N`・50 件・実移動の経路あり）、FL-I19（AST 一致）、FL-N04（台帳の仕様記述を訂正）。

### 大規模な未確認・今後の走査

- 学習タブ（discovery_tab.py）の画面構築の内部（レイアウト分岐 wide/fold/narrow、画像ゲート、先読みキャッシュ）、ランキング/ライブラリ/おすすめの各関数本体は定義一覧とラベルまで。**挙動の同値は未確認**
- core.py（ダウンロード本体）、database.py、pixiv_client.py の機能一覧（フォルダ命名、ファイル名規則、ハードリンク、`Images\` 構造、差分判定の細部）は未走査
- 目視が要る見た目（帯・破線・ホバー・フォント・余白）は D 分類（OLD45）のまま
- 次回更新時は `git -C work log -1` を再確認し、wt-f（ごみ箱を空にする）の取り込み後に FL-H06 を更新すること（wt-g は 5ca397a で取り込み済み）

## T. 設定キー（`get_setting`/`set_setting` と `*_SETTING_KEY`）→ 台帳行の索引

元で参照されるキー全て（gui.py・core.py・scheduler.py・server.py・各 *_tab.py・discovery_*.py を grep）。

| 設定キー | 台帳行 | 備考 |
|---|---|---|
| save_path | FL-C03 | |
| my_user_id | FL-A09, A11 | 別アカウント判定の基準。移植版は `bookmark_account_id`／認証結果で代替（auth_status.py） |
| check_on_startup / auto_check_interval_hours | FL-C05, C06, O02 | |
| nana7mi_shaft_refresh_interval_hours / nana7mi_shaft_last_refresh_at | FL-C07, O04 | 最終実行時刻の永続化は external_vault 側（読んでいない） |
| nana7mi_pw_salt / nana7mi_pw_hash | FL-B10 | 借用ポップアップのパスワード照合。移植版は専用保管庫（external_vault*.py） |
| novel_save_format / ugoira_save_format / zip_all_after_download / use_work_folder | FL-C08, C09, C17, C19 | |
| enable_notifications | FL-C10, N05 | |
| bookmark_download_mode / enable_bookmark_hardlink | FL-C11, D12, D13 | `enable_bookmark_hardlink` は移植版に設定として無い |
| download_interval / api_retry_count / api_retry_wait | FL-C12, C13 | |
| discovery_cache_limit_mode / _size_mb / _size_count | FL-C14 | |
| pixiv_shared_image_cache_size_mb / pixiv_disk_thumbnail_cache_size_gb | FL-C15 | |
| minimize_to_tray | FL-A06, C18 | |
| meta_backfill_enabled | FL-C20, O05 | |
| embed_auto_on_download | FL-C21, A12 | |
| last_sync_date | FL-A10 | 無 |
| auto_archive_new_users | FL-D14 | 無 |
| ext_autolaunch_confirm_shown | FL-A14, C04 | 初回のみ確認する記憶。移植版は毎回ダイアログ（preferences_view.py:288-303）か未確認 |
| web_bridge_enabled / web_api_token | FL-X02 | 意図的削除 |
| bookmark_cache_fetched_at | FL-K06 | 最終更新表示・自動更新は無し |
| discovery_search_mode / discovery_search_cursor / discovery_related_cursor / discovery_following_cursor | FL-I12, I14, I19 | 移植版は `discovery_*.py` と発掘設定の「検索対象」（learning_view.py:221）。カーソルの永続化は discovery_engine/learning/population に同名あり（grep のみ） |
| discovery_ratio_subject / discovery_ratio_style | FL-I12 | |
| discovery_generic_tags_extra | FL-I19 | discovery_ranking.py:222 に同名キー |
| exclude_hidden_authors / exclude_hidden_works / ranking_personal_tags / ranking_personal_filters | FL-J11, J12 | |
| ranking_personal_candidates_open | FL-Y02 | 自分のランキングの「よく効くタグ」副行の開閉（`filter_panel.load_json_setting`/`save_json_setting` 経由。元 ranking_tab.py:130, 1844-1866, 2146-2166） |
| recommend_section_order / _collapsed / recommend_certain_group_collapsed / recommend_trend_added_tags | FL-L02 | recommendation_state.py:160-166 |
| grid_card_size_mode | FL-C25 | |
