# D-001・D-002 修正および検証結果（2026-11-27）

作業ツリー: `S:\Work\PV\d001-ui-20261127\`（ブランチ `d001-ui-20261127`、基点 `8450991a`）。
外部通信 0・実プロファイル非接触・起動した検証プロセスの残存 0 で隔離検証を実施した。

## 1. 変更ファイル

| ファイル | 変更内容 | 対象 |
| --- | --- | --- |
| `src/pixivvault_nicegui/ui/request_status_view.py` | ヘッダー用（`compact=True`）の 429 停止帯（`data-rate-stop="true"`）に `tabindex="0"` と `pv-focus-ring` を付与し、非停止時は `tabindex` を除去。判定・ゲートロジックは未変更（表示・キーボード到達性のみ）。 | D-001 |
| `src/pixivvault_nicegui/ui/theme.py` | `@media (max-height:500px)` に限り、`.pv-shell` の縦余白を圧縮（`padding:4px 8px; gap:2px`）し、`.pv-header-request-status` を 3 列 1 行グリッド化（`[data-rate-stop=true]` を `max-height:30px; overflow-y:auto; scrollbar-width:thin; line-height:13px` で内部スクロール化、閉じた `.pv-request-stop-details` を同列、`閉じる` を右端に配置。展開時は全幅 2 行目へ）。高さ 800px 以上の通常表示は一切未変更。 | D-001 |
| `src/pixivvault_nicegui/ui/theme.py` | `.pv-grid .pv-card-title` を `11px` → `13px`、`.pv-queue-view .pv-job-head .pv-job-title` を `12.5px` → `13px` に引き上げ。 | D-002 |
| `src/pixivvault_nicegui/ui/unified_download_view.py` | `.pv-ud-c-name`（ダウンロード作者一覧の作者名）を `12.5px` → `13px` に引き上げ（表頭 `.pv-ud-thead .pv-ud-c-name` は既存の `11px` 補助文字を維持）。 | D-002 |
| `src/pixivvault_nicegui/ui/failed_records_view.py` | `.pv-failure-title` を `12.5px` → `13px`、`.pv-failure-reason` を `11px` → `13px` に引き上げ。 | D-002 |
| `tests/test_d001_d002_ui_20261127.py` | D-001（429 帯の低高さ圧縮・キーボード到達・1280×800 不変）と D-002（非凍結主情報 13px・凍結例外不変）の回帰試験を追加。 | D-001 / D-002 |
| `verify/d001-20261127/measure_d001_d002.py` | `tools/uiux_image_area.py`・`tools/uiux_fixture.py`・`tools/p5_design_evidence.py` を用いた隔離ブラウザー計測スクリプト。 | 検証 |

## 2. 判断の根拠

### D-001 の原因と方針

- コミット `1492b6c9` で追加された `.pv-header .pv-header-request-status [data-rate-stop=true]`（折り返し全文表示・高さ約 84px）と `.pv-request-stop-details`（`停止の詳細`・高さ約 24.5px）が、640×400 CSS px（および 1280×800 のページ 200%）で合計約 110px を占有し、一覧の先頭画像を Y=313〜365px へ押し下げて全対象画面で完全表示 0 枚になっていた。
- 修正は `@media (max-height:500px)` に限定したため、1280×800（100%）・1920×1080（100%）・1024×768（100%）・1280×800（125% = 1024×640）の見た目は一切変わらない。
- 安全情報（本番 DB 警告と `詳細`、取得停止の回復入口 `取得停止の確認・回復`、429 警告 2 件、`停止の詳細`、`閉じる`、`自動処理：未解消 2` と内訳）はすべて可視のまま維持し、429 警告枠は `tabindex="0"` + `pv-focus-ring` + `overflow-y:auto` によりキーボード（`Tab` → `PageDown`/`ArrowDown`）で末尾まで到達でき、`停止の詳細` も `Enter` で展開できる。

### D-002 と凍結例外（`docs/UIUX_EXCEPTIONS_20261124.md`）の 1 対 1 照合

`docs/UIUX_EXCEPTIONS_20261124.md`（L197〜210）およびその根拠データ `verify/uiux-final-20261124/DISPOSITION.json`・`baseline/FINDINGS.json`（A8-00001〜A8-00284）と、`D-002.md`（64 セル・227 ノード）を全件照合し、以下のように仕分けた。

#### 凍結例外に該当するため変更しなかった項目（除外）

| D-002 対象画面・要素 | 現在 / 下限 | 凍結例外の根拠（`UIUX_EXCEPTIONS_20261124.md` / `DISPOSITION.json`） |
| --- | --- | --- |
| キュー `[data-testid="queue-heading"] > div:nth-of-type(2),(3)`（`2秒毎に自動更新` 等） | 10.5px / 11px | `A8-00195〜A8-00206`（キュー）、`A8-00261〜A8-00284`（通信制限・再試行待ち）。同一セレクタ一致。 |
| 直近のバックアップ `[data-testid="backup-size-{large,medium,small}"]`（`大`/`中`/`小`） | 9px / 11px | `A8-00031〜A8-00048`（直近のバックアップ `history`）。同一セレクタ一致。 |
| マイブックマーク `[data-testid="bookmark-card"] > div:nth-of-type(3)`（作者名 `.pv-card-author`） | 10px / 11px | `A8-00001〜A8-00010`（マイブックマーク）。同一セレクタ一致。 |
| ライブラリ `[data-testid="library-authors-scroll"] ... .pv-library-card-info`（`1点`・`閲覧`・容量） | 9.5px / 11px | `A8-00063〜A8-00128`（ライブラリ）。同一 `.pv-library-card-info` 要素群（合成作者 3〜6 人目も同一クラス）。 |
| カスタムランキング `[data-testid="toolbar-controls-personal"] ...` カードサイズ（`大`/`中`/`小`） | 9px / 11px | `A8-00129〜A8-00194`（カスタムランキング・同絞り込み）。`1492b6c9` で `gallery.py` が `ui.row()` に包んだため `> button:nth-of-type(4..6)` が `> div:nth-of-type(6) > button:nth-of-type(1..3)` に変わった同一 `.pv-card-size` ボタン群。 |
| カスタムランキング `.pv-card-author`（作品カード作者名） | 10px / 11px | `A8-00129〜A8-00170`（カスタムランキング）。同一セレクタ一致。 |
| おすすめ `[data-testid="recommend-section-certain"] ... .pv-card-author` | 9.5px / 11px | `A8-00231〜A8-00260`（おすすめ）。同一セレクタ一致。 |

#### 凍結例外に該当しないため 13px 以上に修正した項目（すべて本文・主情報）

| D-002 対象画面・要素 | 修正前 | 修正後 | 凍結台帳での扱い |
| --- | ---: | ---: | --- |
| マイブックマーク `[data-testid="bookmark-card"] > div:nth-of-type(2)`（`.pv-grid .pv-card-title`） | 11px | **13px** | 台帳に未記載（主情報タイトル） |
| カスタムランキング 作品タイトル（`.pv-grid .pv-card-title`） | 11px | **13px** | 台帳に未記載（主情報タイトル） |
| おすすめ 作品タイトル（`.pv-grid .pv-card-title`） | 11px | **13px** | 台帳に未記載（主情報タイトル） |
| ダウンロード `[data-testid="unified-author-list"] ... .pv-ud-c-name`（作者名） | 12.5px | **13px** | ダウンロードの A8 例外は `cookie-banner` のみで未記載 |
| 失敗リスト `[data-testid="failed-rows"] ... .pv-failure-title`（タイトル） | 12.5px | **13px** | 失敗リストの A8 例外行自体が存在しない |
| 失敗リスト `[data-testid="failed-rows"] ... .pv-failure-reason`（失敗理由） | 11px | **13px** | 失敗リストの A8 例外行自体が存在しない |
| キュー `[data-testid="queue-list"] ... .pv-job-title`（ジョブ名） | 12.5px | **13px** | キューの A8 例外は `queue-heading` 補助文のみで未記載 |

## 3. 測定値（隔離合成データ・ブラウザー実測）

### 3.1 D-001：狭幅・警告併発・絞り込み畳み・先頭位置での完全表示画像数（修正前 → 修正後）

条件: 長文 DB 警告 + HTTP 429 停止 2 件 + 自動処理未解消 2 件、絞り込み畳み、先頭位置。
原証拠: [before/D001.json](before/D001.json) / [after/D001.json](after/D001.json) / [before/png/](before/png) / [after/png/](after/png)

| 画面 | 640×400 dark | 640×400 light | 1280×800 200% dark | 1280×800 200% light |
| --- | --- | --- | --- | --- |
| ランキング (`ranking`) | 0 → **4**（先頭完） | 0 → **3**（先頭完） | 0 → **3**（先頭完） | 0 → **3**（先頭完） |
| カスタムランキング (`personal`) | 未測定※1 → **3**（先頭完） | 未測定※2 → **3**（先頭完） | 未測定※2 → **1**（先頭完） | 未測定※2 → **2**（先頭完） |
| ライブラリ・作者 (`library`) | 未測定※1 → **2**（先頭完） | 0 → **2**（先頭完） | 0 → **2**（先頭完） | 0 → **2**（先頭完） |
| ライブラリ・作品 (`library-works`) | 未測定※1 → **1**（先頭完） | 0 → **1**（先頭完） | 0 → **1**（先頭完） | 0 → **1**（先頭完） |
| マイブックマーク (`bookmarks`) | 未測定※1 → **1**（先頭完） | 0 → **1**（先頭完） | 0 → **1**（先頭完） | 0 → **1**（先頭完） |
| おすすめ (`recommend`) | 未測定※1 → **2**（先頭完） | 0 → **2**（先頭完） | 0 → **2**（先頭完） | 0 → **2**（先頭完） |
| 重複整理 (`dedup`) | 0 → **1**（先頭完） | 0 → **2**（先頭完） | 0 → **2**（先頭完） | 0 → **2**（先頭完） |

- ※1: 修正前計測の `640×400 dark` は、巨大化した 429 帯（84px + 24.5px）がナビ展開位置を下端へ押し下げ、初回案内トースト（`q-notification`）と重なったため自動クリックがタイムアウトし未測定（原資料 `D-001.md` の v2 実測では全件 0 枚を確認済み）。
- ※2: 修正前計測の `personal` は、429 帯圧迫により `gallery-scroll` の可視高さが失われタイムアウトし未測定（原資料 `D-001-addendum.md` の v2 実測では 4 条件とも完全表示 0 枚を確認済み）。
- **修正後は対象 7 画面 × 4 条件 = 全 28 条件すべてで測定成立、先頭画像完全表示（`first_image_complete=True`）かつ完全表示画像 1 枚以上を達成（失敗 0）。**

#### 狭幅ストレス時の安全情報・キーボード到達確認（640×400 light / dark、修正後）

- 本番 DB 警告 (`db-status-banner`): `y=4, h=28`、`詳細` ボタン可視。`Enter` で全文表示し `Esc` で `詳細` ボタンへフォーカス復帰（`db_warning_focus_return=True`）。
- 回復入口 (`shared-get-recovery-open`): `y=34, h=32` 可視。
- 429 停止帯 (`request-retry-status`): `y=68, h=30`（`max-height:30px, overflow-y:auto, scrollH=67, clientH=28`）。フォーカス後に `PageDown` で末尾（`scrollTop=39 / max=39`）まで到達。
- `停止の詳細` (`request-retry-stop-details-toggle`): `y=67, h=32` 可視。`Enter` で補足文展開（`stop_details_keyboard_open=True`）。
- `閉じる` (`request-retry-dismiss`): `y=69, h=28` 可視。
- 自動処理未解消 (`automation-unresolved`, `automation-breakdown`): `y=106.7, h=16.5` 可視。横スクロールなし（`horizontal_overflow=False`）。

#### 1280×800（100%）通常 A17 面積率および通常高さ不変の確認（修正後）

| 画面 | 1280×800 通常 A17（基準 55% 以上） | `navimpl2` 実績との比較 |
| --- | ---: | ---: |
| ランキング | **66.8%** | 66.8%（差 0.0pt） |
| カスタムランキング | **63.1%** | 63.1%（差 0.0pt） |
| ライブラリ・作者 | **70.8%** | 70.8%（差 0.0pt） |
| ライブラリ・作品 | **67.5%** | 67.5%（差 0.0pt） |
| マイブックマーク | **74.2%** | 74.2%（差 0.0pt） |
| おすすめ | **68.3%** | 68.3%（差 0.0pt） |
| 重複整理 | **69.1%** | 69.1%（差 0.0pt） |

- 1280×800 ストレス時の `request-retry-status` は `h=68, maxHeight='none', overflowY='visible'`（全文表示のまま無変更）。

### 3.2 D-002：合成長文・通常データでの computed font-size 実測（1280×800 dark / light）

原証拠: [before/D002.json](before/D002.json) / [after/D002.json](after/D002.json)

| 画面・要素 | 区分 | 修正前 (dark / light) | 修正後 (dark / light) | 判定 |
| --- | --- | --- | --- | --- |
| ダウンロード 作者名 (`.pv-ud-c-name`) | 非凍結（本文 13px） | 12.5px / 未測定※ | **13px / 13px** | 達成 |
| キュー ジョブ名 (`.pv-job-title`) | 非凍結（本文 13px） | 12.5px / 12.5px | **13px / 13px** | 達成 |
| 失敗リスト タイトル (`.pv-failure-title`) | 非凍結（本文 13px） | 12.5px / 12.5px | **13px / 13px** | 達成 |
| 失敗リスト 理由 (`.pv-failure-reason`) | 非凍結（本文 13px） | 11px / 11px | **13px / 13px** | 達成 |
| マイブックマーク 作品タイトル (`.pv-card-title`) | 非凍結（本文 13px） | 11px / 11px | **13px / 13px** | 達成 |
| カスタムランキング 作品タイトル (`.pv-card-title`) | 非凍結（本文 13px） | 11px / 11px | **13px / 13px** | 達成 |
| おすすめ 作品タイトル (`.pv-card-title`) | 非凍結（本文 13px） | 11px / 11px | **13px / 13px** | 達成 |
| キュー 補助文 (`queue-heading`) | 凍結例外 | 10.5px / 10.5px | 10.5px / 10.5px | 維持 |
| マイブックマーク 作者名 (`.pv-card-author`) | 凍結例外 | 10px / 10px | 10px / 10px | 維持 |
| カスタムランキング 作者名 (`.pv-card-author`) | 凍結例外 | 10px / 10px | 10px / 10px | 維持 |
| おすすめ 作者名 (`.pv-card-author`) | 凍結例外 | 9.5px / 9.5px | 9.5px / 9.5px | 維持 |
| ライブラリ 件数・容量 (`.pv-library-card-info`) | 凍結例外 | 9.5px / 9.5px | 9.5px / 9.5px | 維持 |
| 直近のバックアップ サイズ選択 (`backup-size-large`) | 凍結例外 | 9px / 9px | 9px / 9px | 維持 |

※修正前 `download/light` はリスト描画前の採取で `None`（dark では 12.5px 確認済み）。修正後は描画待ちを追加し dark/light 双方で 13px を確認。

## 4. テスト結果

実行環境: `S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe -B -m pytest -p required_network_guard -p no:cacheprovider -k "not dpapi"`、`PYTHONPATH=S:\Work\PV\d001-ui-20261127\src;S:\Work\PV\d001-ui-20261127\tests`、`TEMP/TMP/--basetemp=S:\Work\_tmp\PV\d001\...`

- **Red（実装前）**: `tests/test_d001_d002_ui_20261127.py` → **3 failed in 4.89s**（D-001 フォーカス属性なし・帯高さ超過、D-002 `bm-title 11 < 13px`）。
- **Green（最終実装後）**: 関連 11 ファイル（新規試験＋キュー幅・シェル所見・共通アクセシビリティ・tone-d・fix3 アクセシビリティ・回復案内・取得停止通知・通信棚卸し `test_p6_network.py`・`test_ui_fix3_browser.py`・`test_ui_fix4_20261122.py`）→ **155 passed in 173.45s (0:02:53)**。
- **隔離・後始末**: 全ブラウザー計測で `owned_remaining_count=0`、`owned_ports_closed=True`、`foreign_requests=0`、`server_network_denied=0`、`browser_errors=0`。

## 5. 未解決・制約・逸脱日誌

- **1280×800（100%）の 429 長文 2 件ストレス時の面積率**: 依頼の制約「通常の高さ（800 以上）の見た目は変えない」に従い、高さ 800px 以上では `1492b6c9` の全文表示 429 帯を維持したため、1280×800 の通常状態は全 7 画面で 63.1〜74.2%（基準 55% 以上）だが、長文 429 を 2 件同時表示したストレス時は 47.2〜58.3% となる（先頭画像は全画面で完全表示・6〜8 枚可視）。通常高さでの 429 帯圧縮は今回の制約により実施していない。
- **D-002 凍結例外の扱い**: 凍結例外の 7 要素群（キュー補助文、サイズ選択 `大/中/小`、作品カード作者名、ライブラリ件数・容量）は `docs/UIUX_EXCEPTIONS_20261124.md` に従い意図的に未変更としている。これらを将来引き上げるか凍結継続とするかは監督裁定に委ねる。
- **独立検証の要否**: 変更範囲は UI の CSS・表示属性（`tabindex` / `pv-focus-ring`）のみであり、データ保全・取得停止ゲート判定・認証・並行処理・ロックのコードは一切変更していない。

監督注記: PNG は公開リポに入れず、`S:\Projects\PV-DEV-20261117erify\d001-20261127-png\` と、ブランチ `d001-ui-20261127`（コミット fbc5d03c、ローカル）に保管。
