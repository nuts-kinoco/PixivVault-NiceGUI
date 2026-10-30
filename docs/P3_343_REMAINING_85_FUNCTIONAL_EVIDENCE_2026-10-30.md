# P3 343: remaining 85 original operations ? per-ID functional evidence (2026-10-30)

**Scope:** the 85 original IDs in U/Q/X/H/S/E/A/T/M/N/Z, derived from the immutable 152-operation v8 ledger. No new operation or acceptance condition is added. This is a source/evidence audit, not a 152-operation formal completion or overall acceptance.

**Count:** A=5, B=71, C=9, D=0; total=85. Prefix totals: U=13, Q=19, X=8, H=1, S=21, E=1, A=2, T=2, M=5, N=8, Z=5.

**Decision rule:** A = original function visibly exercised with saved operation-level result for its identified scope, B = current implementation exists but operation/boundary or current binding incomplete, C = original behavior demonstrably absent or replaced in examined product UI, D = truly blocked by authorized/environment boundary. A is only the individual functionality specified in the note and never certifies the full product, native/performance/real GET/CI/review gates. The formal ledger v8 aggregate 33/43/76 is unchanged.

**Baseline:** full342r2 4156 PASS / 6 SKIP and 576-source consistency are separately reported by prime; the current worktree may be concurrent and was read, without Git commands or GUI runs. At inspection, the post-340 Q03/12 result recorded 178 product files with 174 current matches; deviations were outside queue/failure view paths. The Q18/19/20 result recorded 175 with 166 matches; its changed files did not include job_views.py or failed_records_view.py. Relevant source identity is per-row; an unrelated source change does not erase prior functional success, but none proves an entire new candidate. Later changes must be rechecked before formal binding.

**Original literal convention:** below, `expected_full` is reproduced verbatim from v8 (newline escaped as `\n` for table readability). Where v8 leaves it null, the index operation is the literal available anchor; the v8 reference provenance has `unfilled-code-or-summary-requires-reconciliation`, so no invented quoted expectation is supplied. Source SHA values are current file bytes at this audit only. Old Q J/F/M proofs are historical path+SHA recorded in the mapping, without pretending these runs captured current source manifests.

## ID inventory

| ID | original operation (index) | result | selected proof |
|---|---|:---:|---|
| U01 | フォロー作者/ブクマ対象一覧を切り替える | C | none bound |
| U02 | ダウンロード対象を検索・クリアする | B | none bound |
| U03 | 作者対象の表示順序を変える | C | none bound |
| U04 | ☆作者だけを絞り込む | C | none bound |
| U05 | 対象の全選択・選択解除を行う | C | none bound |
| U06 | ☆作者を一括で対象に追加する | C | none bound |
| U07 | 作者を検索/ID指定して対象へ追加する | B | none bound |
| U10 | 作者別の媒体対象設定を変更する | C | none bound |
| U11 | 作者別ZIP方針を変更する | C | none bound |
| U12 | 選択対象の作者・ブクマDLを開始する | B | none bound |
| U15 | 未フォロー作者を抽出・確認する | B | none bound |
| U16 | 単一作品IDを指定して保存キューへ送る | B | none bound |
| U17 | ダウンロード詳細パネルを開閉する | B | artifacts/p3-315-u17-download-details/edge-r1/result.json |
| Q01 | ジョブ状態・進捗一覧を表示する | B | historic J/F/M |
| Q02 | 状態別にキューを絞る | B | historic J/F/M |
| Q03 | キューを作者名・IDで検索する | A | artifacts/p3-340-q03-q12-worker7-edge-r2/result.json |
| Q04 | 個別ジョブを一時停止する | B | historic J/F/M |
| Q05 | 個別ジョブを再開する | B | historic J/F/M |
| Q06 | 個別ジョブを停止する | B | historic J/F/M |
| Q07 | 失敗したジョブを個別に再試行する | B | historic J/F/M |
| Q08 | ジョブ履歴を削除する | B | historic J/F/M |
| Q09 | ジョブを一括停止する | B | historic J/F/M |
| Q10 | ジョブを一括再開する | B | historic J/F/M |
| Q11 | ジョブを一括一時停止する | B | historic J/F/M |
| Q12 | ジョブを一括削除する | A | artifacts/p3-340-q03-q12-worker7-edge-r2/result.json |
| Q13 | 旧失敗リストを表示する | B | historic J/F/M |
| Q14 | 失敗理由カテゴリで絞る | B | historic J/F/M |
| Q15 | 失敗リストをタイトル・ID検索する | B | historic J/F/M |
| Q17 | 失敗項目を一括再試行する | B | historic J/F/M |
| Q18 | 失敗記録を1件削除する | A | artifacts/p3-331-failure-removal-edge-r1/result.json |
| Q19 | 失敗記録を全件クリアする | A | artifacts/p3-331-failure-removal-edge-r1/result.json |
| Q20 | 失敗リストをCSVへ出力する | A | artifacts/p3-331-failure-removal-edge-r1/result.json |
| X01 | 重複候補をスキャン・比較する | B | none bound |
| X02 | 未判定/近似候補を切り替える | B | artifacts/p3-dedup-mode-20261028/worker2-edge-1280-r1/result.json |
| X03 | 作者名・IDで候補を検索する | B | none bound |
| X04 | 重複候補のサムネイルサイズを変更する | B | artifacts/p3-dedup-mode-20261028/worker2-edge-1920-light-r1/result.json |
| X06 | 隔離対象画像を選択する | B | artifacts/p3-318-x07-quarantine/edge-product-r3/result.json |
| X07 | 1組の選択画像をごみ箱へ移す | C | artifacts/p3-318-x07-quarantine/edge-product-r3/result.json |
| X08 | 複数組を一括でごみ箱へ移す | C | artifacts/p3-318-x07-quarantine/edge-product-r3/result.json |
| X09 | ごみ箱の保存先を開く | B | artifacts/p3-318-x07-quarantine/x09-v06-edge-r1/result.json |
| H01 | 直近バックアップ作者一覧を閲覧する | B | none bound |
| S01 | Cookieファイルをインポートする | B | none bound |
| S03 | フォロー中リストを同期する | B | none bound |
| S04 | 保存フォルダを設定変更する | B | artifacts/p3-339-native/profile-packaged-r2/result.json |
| S05 | 起動時の自動チェックをON/OFFする | B | artifacts/p3-318-scheduler-70-authors/edge-r1/result.json |
| S06 | 自動チェック間隔を変更する | B | artifacts/p3-318-scheduler-70-authors/edge-r1/result.json |
| S07 | 小説の既定保存形式を変更する | B | none bound |
| S08 | うごイラ既定形式を変更する | B | none bound |
| S09 | 新着通知をON/OFFする | B | none bound |
| S10 | ブクマのdirect/link保存配置を変更する | B | artifacts/p3-318-native-save-layout/edge-normal-profile-r3/result.json |
| S11 | ダウンロード待機間隔を変更する | B | none bound |
| S12 | HTTP retry回数を変更する | B | none bound |
| S13 | 429待機時間を変更する | B | none bound |
| S14 | 全作者自動ZIPをON/OFFする | B | none bound |
| S15 | トレイ格納設定をON/OFFする | B | artifacts/p3-339-native/tray-packaged-r3/result.json |
| S16 | 学習cacheのMB/枚数モードを変更する | B | artifacts/p3-318-s16-cache-units/edge-r1/result.json |
| S17 | 学習cacheのMB上限を変更する | B | artifacts/p3-318-s16-cache-units/edge-r1/result.json |
| S18 | 学習cacheの枚数上限を変更する | B | artifacts/p3-318-s16-cache-units/edge-r1/result.json |
| S19 | 全タブ共有cache容量を変更する | B | none bound |
| S20 | ディスクサムネイルcacheを消去する | B | none bound |
| S21 | ダーク/ライトテーマを切り替える | B | artifacts/p3-315-d08-theme-recommend-r2/1280-initial/result.json |
| S22 | ブラウザでPixivを開く | B | none bound |
| E01 | DBとmetadataのみをZIPへエクスポートする | B | artifacts/p3-315-e01-export-edge-r3/result.json |
| A01 | 作者の作品一覧オーバーレイを開く | B | none bound |
| A02 | 作者ページの追加作品を取得する | B | artifacts/p3-318-author-revisit/edge-product-r4/1280x720/ui/result.json |
| T01 | 「今すぐチェック」で対象作者の新着確認・dispatchを開始する | B | artifacts/p3-318-scheduler-70-authors/edge-r1/result.json |
| T02 | schedulerの履歴・状態を確認する | B | artifacts/p3-318-scheduler-70-authors/edge-r1/result.json |
| M01 | 小説を保存する | B | none bound |
| M04 | 保存済み小説本文を閲覧する | B | none bound |
| M06 | うごイラmetadata/フレームを確認する | B | none bound |
| M07 | うごイラを形式選択して保存する | B | artifacts/p3-339-native/ugoira-packaged-r1/result.json |
| M10 | 保存済みうごイラを再生する | B | artifacts/p3-339-native/ugoira-packaged-r1/result.json |
| N01 | Windowsアプリを起動する | B | artifacts/p3-340-protocol-class-native-r1/result.json |
| N02 | アプリを正常終了する | B | artifacts/p3-339-native/tray-packaged-r3/result.json |
| N03 | アプリをトレイに格納する | B | artifacts/p3-339-native/tray-packaged-r3/result.json |
| N04 | 最小化状態から画面を復帰する | B | artifacts/p3-339-native/tray-packaged-r3/result.json |
| N06 | URI自動起動登録を有効化する | B | artifacts/p3-340-protocol-class-native-r1/result.json |
| N07 | URI登録を解除する | B | artifacts/p3-340-protocol-class-native-r1/result.json |
| N09 | PC拡張機能からジョブを受付ける | B | none bound |
| N10 | PC拡張機能の受付・接続状態を確認する | B | none bound |
| Z01 | 暗号化Vaultを解錠する | B | none bound |
| Z02 | Vaultを明示lockする | B | none bound |
| Z03 | Vault情報を登録・更新する | B | none bound |
| Z04 | Shaftで個人検索を実行する | B | none bound |
| Z05 | premium条件付き検索を実行する | B | none bound |

## Per-operation trace

### U01 [C] フォロー作者/ブクマ対象一覧を切り替える

**Original source index:** SW U-3. Original v8 literal reference IDs: v8-U01-01. Reference status: verbatim-v7-reference-set-transcribed.

**Frozen original source references (v8 literal transcription if available):**
- v8-U01-01 / U-3: S:/Projects/PixivVault/docs/UI_SWEEP_CHECKLIST_2026-09-03.md L135-135 ; source SHA256=065d0e2c8351587cb56371faf3afcdb172f07efd83575e13dc891b586d4cef28; extracted-lines SHA256=0f0186163969de817f9e6995ee325c93085e51c3a35a0f2bfadca7fbbb84e3ae.
  - Verbatim expectation: 押した直後に左列の中身とボタンの選択表示が変わる

**Original literal `expected_full`:**

```text
押した直後に左列の中身とボタンの選択表示が変わる
```

**Current source:** `src/pixivvault_nicegui/ui/managed_download_view.py` SHA256=330dda5bacb9d69732960ecbb6f24f109c06008abd6250ed30d0f14d79b89224 ; `src/pixivvault_nicegui/author_download.py` SHA256=86dfeb704845fd8057c68cea5f24f4088fd2c5d880e79331a5cc295cb171adfa ; `src/pixivvault_nicegui/managed_download_requests.py` SHA256=5fac2c9c6caa4d690c911c5f78181e2c7d60d8affc6fd18e018eafafb99c5ea7.

**Saved operation evidence:** No operation-level result.json bound in examined sources.

**Current applicability / concrete missing:** Original author/bookmark-list mode switch is absent from managed download UI; separate concurrent lists do not satisfy selected-mode switch.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### U02 [B] ダウンロード対象を検索・クリアする

**Original source index:** SW U-7/U-10. Original v8 literal reference IDs: v8-U02-01, v8-U02-02. Reference status: verbatim-v7-reference-set-transcribed.

**Frozen original source references (v8 literal transcription if available):**
- v8-U02-01 / U-7: S:/Projects/PixivVault/docs/UI_SWEEP_CHECKLIST_2026-09-03.md L139-139 ; source SHA256=065d0e2c8351587cb56371faf3afcdb172f07efd83575e13dc891b586d4cef28; extracted-lines SHA256=3f277756a0675b1832a0267120628fbaf6f0383f624388015f82686975e54e5f.
  - Verbatim expectation: 入力中に一覧が絞られ、✕で全件へ戻る
- v8-U02-02 / U-10: S:/Projects/PixivVault/docs/UI_SWEEP_CHECKLIST_2026-09-03.md L142-142 ; source SHA256=065d0e2c8351587cb56371faf3afcdb172f07efd83575e13dc891b586d4cef28; extracted-lines SHA256=851bdec63241ae460e14f755af9ed02040b3e18e682dfea3d6b00ddb885d0019.
  - Verbatim expectation: 「0件」と分かる文が出る

**Original literal `expected_full`:**

```text
入力中に一覧が絞られ、✕で全件へ戻る\n「0件」と分かる文が出る
```

**Current source:** `src/pixivvault_nicegui/ui/managed_download_view.py` SHA256=330dda5bacb9d69732960ecbb6f24f109c06008abd6250ed30d0f14d79b89224 ; `src/pixivvault_nicegui/author_download.py` SHA256=86dfeb704845fd8057c68cea5f24f4088fd2c5d880e79331a5cc295cb171adfa ; `src/pixivvault_nicegui/managed_download_requests.py` SHA256=5fac2c9c6caa4d690c911c5f78181e2c7d60d8affc6fd18e018eafafb99c5ea7.

**Saved operation evidence:** No operation-level result.json bound in examined sources.

**Current applicability / concrete missing:** Click/clear and zero-hit behavior for both author/bookmark target lists needs one bounded Edge session.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### U03 [C] 作者対象の表示順序を変える

**Original source index:** SW U-5. Original v8 literal reference IDs: v8-U03-01. Reference status: verbatim-v7-reference-set-transcribed.

**Frozen original source references (v8 literal transcription if available):**
- v8-U03-01 / U-5: S:/Projects/PixivVault/docs/UI_SWEEP_CHECKLIST_2026-09-03.md L137-137 ; source SHA256=065d0e2c8351587cb56371faf3afcdb172f07efd83575e13dc891b586d4cef28; extracted-lines SHA256=9a4d1f29995f5ceeff514e1e173384d3be6e4f7b3e544c717414b0486a8c2adb.
  - Verbatim expectation: 押した直後に一覧の並びが変わり、昇順／降順のどちらが効いているか画面で分かる

**Original literal `expected_full`:**

```text
押した直後に一覧の並びが変わり、昇順／降順のどちらが効いているか画面で分かる
```

**Current source:** `src/pixivvault_nicegui/ui/managed_download_view.py` SHA256=330dda5bacb9d69732960ecbb6f24f109c06008abd6250ed30d0f14d79b89224 ; `src/pixivvault_nicegui/author_download.py` SHA256=86dfeb704845fd8057c68cea5f24f4088fd2c5d880e79331a5cc295cb171adfa ; `src/pixivvault_nicegui/managed_download_requests.py` SHA256=5fac2c9c6caa4d690c911c5f78181e2c7d60d8affc6fd18e018eafafb99c5ea7.

**Saved operation evidence:** No operation-level result.json bound in examined sources.

**Current applicability / concrete missing:** No author-target sort buttons or ascending/descending state in the current managed view.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### U04 [C] ☆作者だけを絞り込む

**Original source index:** SW U-6. Original v8 literal reference IDs: v8-U04-01. Reference status: verbatim-v7-reference-set-transcribed.

**Frozen original source references (v8 literal transcription if available):**
- v8-U04-01 / U-6: S:/Projects/PixivVault/docs/UI_SWEEP_CHECKLIST_2026-09-03.md L138-138 ; source SHA256=065d0e2c8351587cb56371faf3afcdb172f07efd83575e13dc891b586d4cef28; extracted-lines SHA256=82729c7b53ee15c6de79366c6aa0da73640a2f2eb6db6153a222e2c678a02609.
  - Verbatim expectation: 押した直後に一覧または対象リストが変わる

**Original literal `expected_full`:**

```text
押した直後に一覧または対象リストが変わる
```

**Current source:** `src/pixivvault_nicegui/ui/managed_download_view.py` SHA256=330dda5bacb9d69732960ecbb6f24f109c06008abd6250ed30d0f14d79b89224 ; `src/pixivvault_nicegui/author_download.py` SHA256=86dfeb704845fd8057c68cea5f24f4088fd2c5d880e79331a5cc295cb171adfa ; `src/pixivvault_nicegui/managed_download_requests.py` SHA256=5fac2c9c6caa4d690c911c5f78181e2c7d60d8affc6fd18e018eafafb99c5ea7.

**Saved operation evidence:** No operation-level result.json bound in examined sources.

**Current applicability / concrete missing:** No favourite-star-only target filter in the current managed view.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### U05 [C] 対象の全選択・選択解除を行う

**Original source index:** SW U-4. Original v8 literal reference IDs: v8-U05-01. Reference status: verbatim-v7-reference-set-transcribed.

**Frozen original source references (v8 literal transcription if available):**
- v8-U05-01 / U-4: S:/Projects/PixivVault/docs/UI_SWEEP_CHECKLIST_2026-09-03.md L136-136 ; source SHA256=065d0e2c8351587cb56371faf3afcdb172f07efd83575e13dc891b586d4cef28; extracted-lines SHA256=08fe4437fbfcfc10ac4ddd571b66973f0d9aba9caed170b54a9eee07b4177d67.
  - Verbatim expectation: 押した直後にチェックの見た目と選択件数の表示が変わる

**Original literal `expected_full`:**

```text
押した直後にチェックの見た目と選択件数の表示が変わる
```

**Current source:** `src/pixivvault_nicegui/ui/managed_download_view.py` SHA256=330dda5bacb9d69732960ecbb6f24f109c06008abd6250ed30d0f14d79b89224 ; `src/pixivvault_nicegui/author_download.py` SHA256=86dfeb704845fd8057c68cea5f24f4088fd2c5d880e79331a5cc295cb171adfa ; `src/pixivvault_nicegui/managed_download_requests.py` SHA256=5fac2c9c6caa4d690c911c5f78181e2c7d60d8affc6fd18e018eafafb99c5ea7.

**Saved operation evidence:** No operation-level result.json bound in examined sources.

**Current applicability / concrete missing:** No select-all / deselect-all for original author target selection in the current managed view.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### U06 [C] ☆作者を一括で対象に追加する

**Original source index:** SW U-6. Original v8 literal reference IDs: v8-U06-01. Reference status: verbatim-v7-reference-set-transcribed.

**Frozen original source references (v8 literal transcription if available):**
- v8-U06-01 / U-6: S:/Projects/PixivVault/docs/UI_SWEEP_CHECKLIST_2026-09-03.md L138-138 ; source SHA256=065d0e2c8351587cb56371faf3afcdb172f07efd83575e13dc891b586d4cef28; extracted-lines SHA256=82729c7b53ee15c6de79366c6aa0da73640a2f2eb6db6153a222e2c678a02609.
  - Verbatim expectation: 押した直後に一覧または対象リストが変わる

**Original literal `expected_full`:**

```text
押した直後に一覧または対象リストが変わる
```

**Current source:** `src/pixivvault_nicegui/ui/managed_download_view.py` SHA256=330dda5bacb9d69732960ecbb6f24f109c06008abd6250ed30d0f14d79b89224 ; `src/pixivvault_nicegui/author_download.py` SHA256=86dfeb704845fd8057c68cea5f24f4088fd2c5d880e79331a5cc295cb171adfa ; `src/pixivvault_nicegui/managed_download_requests.py` SHA256=5fac2c9c6caa4d690c911c5f78181e2c7d60d8affc6fd18e018eafafb99c5ea7.

**Saved operation evidence:** No operation-level result.json bound in examined sources.

**Current applicability / concrete missing:** No add-all-favourite-authors action in the current managed view.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### U07 [B] 作者を検索/ID指定して対象へ追加する

**Original source index:** SW U-1/U-14/U-15、入力拒否・選択保持. Original v8 literal reference IDs: v8-U07-01, v8-U07-02, v8-U07-03. Reference status: verbatim-v7-reference-set-transcribed.

**Frozen original source references (v8 literal transcription if available):**
- v8-U07-01 / U-1: S:/Projects/PixivVault/docs/UI_SWEEP_CHECKLIST_2026-09-03.md L133-133 ; source SHA256=065d0e2c8351587cb56371faf3afcdb172f07efd83575e13dc891b586d4cef28; extracted-lines SHA256=5ddc532ee72037fb485e1b2fb4dabf53b682cda035393d39f76dee191c2dae67.
  - Verbatim expectation: すべて反応する。押しても何も起きない部品が無い（⚠️ 罠6: 未配線の「停止」でPOの判断が積まれた前例 `#42`）
- v8-U07-02 / U-14: S:/Projects/PixivVault/docs/UI_SWEEP_CHECKLIST_2026-09-03.md L146-146 ; source SHA256=065d0e2c8351587cb56371faf3afcdb172f07efd83575e13dc891b586d4cef28; extracted-lines SHA256=51fd3894fb4d0a35bec2054d4bd81d3a185d2fdea0167fbad773c12bde562a8e.
  - Verbatim expectation: 見つからないことがスナックバーか画面上の文で出る。無言で無視されない
- v8-U07-03 / U-15: S:/Projects/PixivVault/docs/UI_SWEEP_CHECKLIST_2026-09-03.md L147-147 ; source SHA256=065d0e2c8351587cb56371faf3afcdb172f07efd83575e13dc891b586d4cef28; extracted-lines SHA256=79b3029f138c31f4576670cafef48985b788fc9d926fcdde17356f7752e3f986.
  - Verbatim expectation: 受け付けられないことが画面に出る（落ちない・無言でない）

**Original literal `expected_full`:**

```text
すべて反応する。押しても何も起きない部品が無い（⚠️ 罠6: 未配線の「停止」でPOの判断が積まれた前例 `#42`）\n見つからないことがスナックバーか画面上の文で出る。無言で無視されない\n受け付けられないことが画面に出る（落ちない・無言でない）
```

**Current source:** `src/pixivvault_nicegui/ui/managed_download_view.py` SHA256=330dda5bacb9d69732960ecbb6f24f109c06008abd6250ed30d0f14d79b89224 ; `src/pixivvault_nicegui/author_download.py` SHA256=86dfeb704845fd8057c68cea5f24f4088fd2c5d880e79331a5cc295cb171adfa ; `src/pixivvault_nicegui/managed_download_requests.py` SHA256=5fac2c9c6caa4d690c911c5f78181e2c7d60d8affc6fd18e018eafafb99c5ea7.

**Saved operation evidence:** No operation-level result.json bound in examined sources.

**Current applicability / concrete missing:** Verify invalid/nonexistent author-ID feedback, revision and selection retention through UI.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### U10 [C] 作者別の媒体対象設定を変更する

**Original source index:** UG作者個別設定、P3作者target. Original v8 literal reference IDs: v8-U10-01, v8-U10-02, v8-U10-03, v8-U10-04. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-U10-01 / U10-original-01: S:/Projects/PixivVault/docs/UI_GUIDE.md L48-62 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=ffa1d7b43f302ff140dae954c39aebc938552ec83f6dd8bb3ea081a990c15c32.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-U10-02 / U10-original-02: S:/Projects/PixivVault/core.py L1188-1210 ; source SHA256=c3c758965bc186aee0e90c37910baa01e9c7860338915ee89354ddf1161dcc0b; extracted-lines SHA256=b91214a042873a2d224a2f33b9617a86702ad20011e8014bfa1bcb18de0f24b5.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-U10-03 / U10-original-03: S:/Projects/PixivVault/gui.py L4972-4981 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=291795fd4a169e7c0a68fddc550c97bb15eb74064a11dac98df09432cedea980.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-U10-04 / U10-original-04: S:/Projects/PixivVault/unified_download_tab.py L689-769 ; source SHA256=a82b573b214c65a7a526c602110e1b239154abd8233bb7227360bf17253396b1; extracted-lines SHA256=3d38e83363923076e3370149e07325e4e92ee9cf83641102e678395581881cba.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/ui/managed_download_view.py` SHA256=330dda5bacb9d69732960ecbb6f24f109c06008abd6250ed30d0f14d79b89224 ; `src/pixivvault_nicegui/author_download.py` SHA256=86dfeb704845fd8057c68cea5f24f4088fd2c5d880e79331a5cc295cb171adfa ; `src/pixivvault_nicegui/managed_download_requests.py` SHA256=5fac2c9c6caa4d690c911c5f78181e2c7d60d8affc6fd18e018eafafb99c5ea7.

**Saved operation evidence:** No operation-level result.json bound in examined sources.

**Current applicability / concrete missing:** No per-author media-target setting control in current selected-author chips.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### U11 [C] 作者別ZIP方針を変更する

**Original source index:** UG作者設定、原優先関係. Original v8 literal reference IDs: v8-U11-01, v8-U11-02, v8-U11-03, v8-U11-04. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-U11-01 / U11-original-01: S:/Projects/PixivVault/docs/UI_GUIDE.md L56-62 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=3ea46e70644d97548957ae451fc835859a1b8757a6977002d48cbed9f675aa75.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-U11-02 / U11-original-02: S:/Projects/PixivVault/core.py L1768-1808 ; source SHA256=c3c758965bc186aee0e90c37910baa01e9c7860338915ee89354ddf1161dcc0b; extracted-lines SHA256=a10c39883649caaf440b7b8b459fb4053fe4439c5659d901f6bfabde9451c6d0.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-U11-03 / U11-original-03: S:/Projects/PixivVault/core.py L2130-2161 ; source SHA256=c3c758965bc186aee0e90c37910baa01e9c7860338915ee89354ddf1161dcc0b; extracted-lines SHA256=d39fbf97d5d8dab9668653415213a40f7ecac41fb4b5a83de5d1f20014a0a8d9.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-U11-04 / U11-original-04: S:/Projects/PixivVault/unified_download_tab.py L719-769 ; source SHA256=a82b573b214c65a7a526c602110e1b239154abd8233bb7227360bf17253396b1; extracted-lines SHA256=2c5b1aa75b5c1b7491e5196d7e41eaf36d1aa09b754e5644764592e90ade474d.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/ui/managed_download_view.py` SHA256=330dda5bacb9d69732960ecbb6f24f109c06008abd6250ed30d0f14d79b89224 ; `src/pixivvault_nicegui/author_download.py` SHA256=86dfeb704845fd8057c68cea5f24f4088fd2c5d880e79331a5cc295cb171adfa ; `src/pixivvault_nicegui/managed_download_requests.py` SHA256=5fac2c9c6caa4d690c911c5f78181e2c7d60d8affc6fd18e018eafafb99c5ea7.

**Saved operation evidence:** No operation-level result.json bound in examined sources.

**Current applicability / concrete missing:** No per-author ZIP override/priority control in current selected-author chips.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### U12 [B] 選択対象の作者・ブクマDLを開始する

**Original source index:** SW U-8、UG 48–68、A03新世代、M11自動ZIP. Original v8 literal reference IDs: v8-U12-01. Reference status: verbatim-v7-reference-set-transcribed.

**Frozen original source references (v8 literal transcription if available):**
- v8-U12-01 / U-8: S:/Projects/PixivVault/docs/UI_SWEEP_CHECKLIST_2026-09-03.md L140-140 ; source SHA256=065d0e2c8351587cb56371faf3afcdb172f07efd83575e13dc891b586d4cef28; extracted-lines SHA256=87043e9483539b4e526a465e6ac184368adcef97881405b41aa33534c84bacbc.
  - Verbatim expectation: 押した直後に受理の表示が出る（停止に時間がかかるなら「停止要求を受け付けました」が先に出る・罠6）

**Original literal `expected_full`:**

```text
押した直後に受理の表示が出る（停止に時間がかかるなら「停止要求を受け付けました」が先に出る・罠6）
```

**Current source:** `src/pixivvault_nicegui/ui/managed_download_view.py` SHA256=330dda5bacb9d69732960ecbb6f24f109c06008abd6250ed30d0f14d79b89224 ; `src/pixivvault_nicegui/author_download.py` SHA256=86dfeb704845fd8057c68cea5f24f4088fd2c5d880e79331a5cc295cb171adfa ; `src/pixivvault_nicegui/managed_download_requests.py` SHA256=5fac2c9c6caa4d690c911c5f78181e2c7d60d8affc6fd18e018eafafb99c5ea7.

**Saved operation evidence:** No operation-level result.json bound in examined sources.

**Supplementary recorded evidence (bounded; not a substitute for original operation):** artifacts/p3-339-native/jobs-packaged-r1/first.json ; SHA256=85350a79ac865083d4ce5576b73d96681633848afbbfb8eb335de065cc3c7d1d | artifacts/p3-339-native/jobs-packaged-r1/restart.json ; SHA256=e248c8e761dbdb573aa8229a8161fbb99789407ecbfedd22e2cead90b44f3854.

**Current applicability / concrete missing:** Verify accepted author/bookmark batch, media boundaries, actual managed output and queue handoff.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### U15 [B] 未フォロー作者を抽出・確認する

**Original source index:** UGブクマ、代表サムネイル. Original v8 literal reference IDs: v8-U15-01, v8-U15-02, v8-U15-03. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-U15-01 / U15-original-01: S:/Projects/PixivVault/docs/UI_GUIDE.md L64-68 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=5e3058cde5e26efcb4eed3b030843558ac350d002bca6565fac0a0d531889922.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-U15-02 / U15-original-02: S:/Projects/PixivVault/gui.py L3775-3830 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=d5ed420cbce6adf17084710b3a0979893d9129d70e8a2992d54a1497b10538ef.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-U15-03 / U15-original-03: S:/Projects/PixivVault/core.py L3132-3176 ; source SHA256=c3c758965bc186aee0e90c37910baa01e9c7860338915ee89354ddf1161dcc0b; extracted-lines SHA256=45ae47af84a5ff7756414a0594d2c2d4382c60eab88527782c779cfe3424f7a1.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/ui/managed_download_view.py` SHA256=330dda5bacb9d69732960ecbb6f24f109c06008abd6250ed30d0f14d79b89224 ; `src/pixivvault_nicegui/author_download.py` SHA256=86dfeb704845fd8057c68cea5f24f4088fd2c5d880e79331a5cc295cb171adfa ; `src/pixivvault_nicegui/managed_download_requests.py` SHA256=5fac2c9c6caa4d690c911c5f78181e2c7d60d8affc6fd18e018eafafb99c5ea7.

**Saved operation evidence:** No operation-level result.json bound in examined sources.

**Current applicability / concrete missing:** Verify unfollowed-authors extraction against frozen list, preview image and empty/failure transitions.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### U16 [B] 単一作品IDを指定して保存キューへ送る

**Original source index:** UG単一作品、推薦キュー入口R18. Original v8 literal reference IDs: v8-U16-01, v8-U16-02, v8-U16-03, v8-U16-04. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-U16-01 / U16-original-01: S:/Projects/PixivVault/recommend_tab.py L645-654 ; source SHA256=9fd1f6ab78973a01f0fbbf09dc7973ce82e47bafbbf8180d007d3da6d571e17d; extracted-lines SHA256=bbb91ba1028ef7959ed0d4bb29cb4ba9c512709e6072eb91f242b70fa38fd868.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-U16-02 / U16-original-02: S:/Projects/PixivVault/recommend_tab.py L840-848 ; source SHA256=9fd1f6ab78973a01f0fbbf09dc7973ce82e47bafbbf8180d007d3da6d571e17d; extracted-lines SHA256=ccd30c94a57a75209a471670e3d27c9dfcdcb6315ac3cc649f2e1bce3d4f7aac.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-U16-03 / U16-original-03: S:/Projects/PixivVault/server.py L66-78 ; source SHA256=a1dfbb1de81cc58432166eec7beae4dfc98b72f9437c92af1e9743a3f4a66be0; extracted-lines SHA256=cf1f68c17ce2b12f32efaf7a82989f13e563d1bd2d0341b0d5ceba3fcec5fffb.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-U16-04 / U16-original-04: S:/Projects/PixivVault/server.py L1371-1385 ; source SHA256=a1dfbb1de81cc58432166eec7beae4dfc98b72f9437c92af1e9743a3f4a66be0; extracted-lines SHA256=0b8f49bc32fb3f23d83f3fe2296cefac61637a7a808a625df3ae2412562b5293.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/ui/managed_download_view.py` SHA256=330dda5bacb9d69732960ecbb6f24f109c06008abd6250ed30d0f14d79b89224 ; `src/pixivvault_nicegui/author_download.py` SHA256=86dfeb704845fd8057c68cea5f24f4088fd2c5d880e79331a5cc295cb171adfa ; `src/pixivvault_nicegui/managed_download_requests.py` SHA256=5fac2c9c6caa4d690c911c5f78181e2c7d60d8affc6fd18e018eafafb99c5ea7.

**Saved operation evidence:** No operation-level result.json bound in examined sources.

**Supplementary recorded evidence (bounded; not a substitute for original operation):** artifacts/p3-339-native/jobs-packaged-r1/first.json ; SHA256=85350a79ac865083d4ce5576b73d96681633848afbbfb8eb335de065cc3c7d1d | artifacts/p3-339-native/jobs-packaged-r1/restart.json ; SHA256=e248c8e761dbdb573aa8229a8161fbb99789407ecbfedd22e2cead90b44f3854.

**Current applicability / concrete missing:** Verify explicit work-ID lookup to selected queued download, invalid ID and restart receipt.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### U17 [B] ダウンロード詳細パネルを開閉する

**Original source index:** SW U-2/U-13. Original v8 literal reference IDs: v8-U17-01, v8-U17-02. Reference status: verbatim-v7-reference-set-transcribed.

**Frozen original source references (v8 literal transcription if available):**
- v8-U17-01 / U-2: S:/Projects/PixivVault/docs/UI_SWEEP_CHECKLIST_2026-09-03.md L134-134 ; source SHA256=065d0e2c8351587cb56371faf3afcdb172f07efd83575e13dc891b586d4cef28; extracted-lines SHA256=e6147d3905f7ec32deeef2883c2d7fb560c55e112b406ef5730025fced5da787.
  - Verbatim expectation: 開いたら開いたまま維持される。一瞬出てすぐ閉じない。開けない高さなら、開かずに理由が画面に出る
- v8-U17-02 / U-13: S:/Projects/PixivVault/docs/UI_SWEEP_CHECKLIST_2026-09-03.md L145-145 ; source SHA256=065d0e2c8351587cb56371faf3afcdb172f07efd83575e13dc891b586d4cef28; extracted-lines SHA256=6af5e07a626848aeecb616201e4aa5d46a1805e1703557ea69dc2c7184ab8519.
  - Verbatim expectation: 開いたままか、閉じているなら閉じたことが分かる（一瞬開いて閉じる形にならない）

**Original literal `expected_full`:**

```text
開いたら開いたまま維持される。一瞬出てすぐ閉じない。開けない高さなら、開かずに理由が画面に出る\n開いたままか、閉じているなら閉じたことが分かる（一瞬開いて閉じる形にならない）
```

**Current source:** `src/pixivvault_nicegui/ui/download_status_details.py` SHA256=f5ce94863492532ac404b956ae1fb5b246564411f7aca82bb8adb4dd084ffa5a ; `src/pixivvault_nicegui/ui/managed_download_view.py` SHA256=330dda5bacb9d69732960ecbb6f24f109c06008abd6250ed30d0f14d79b89224.

**Saved operation evidence:** artifacts/p3-315-u17-download-details/edge-r1/result.json ; SHA256=42dfde59c2a5700f70a9c44a31330c05c763fd0a38b11218599433590019eafd; recorded_success=True; no per-source before binding in result

**Current applicability / concrete missing:** Old panel open/close Edge success must be rebound to current view and window-height boundary.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### Q01 [B] ジョブ状態・進捗一覧を表示する

**Original source index:** UG 70–75、queue行契約、7状態・更新. Original v8 literal reference IDs: v8-Q01-01, v8-Q01-02, v8-Q01-03, v8-Q01-04, v8-Q01-05, v8-Q01-06. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-Q01-01 / Q01-original-01: S:/Projects/PixivVault/docs/UI_GUIDE.md L70-75 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=cfe8256656b14649da5000167a96150e72ed68f3cd9249cfba1fa9f35b428c35.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Q01-02 / Q01-original-02: S:/Projects/PixivVault/docs/QUEUE_FAIL_ROW_LAYOUT_SPEC_2026-08-30.md L28-44 ; source SHA256=472c0bd2c5d8e0dbd505f7bf59c15df8b1ecd45ed2b9414a2af62dd7a2929498; extracted-lines SHA256=99f479f21440bdd03843a8bc6638a613a60a8323f50df1d63f7e73e7d5b22a8f.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Q01-03 / Q01-original-03: S:/Projects/PixivVault/docs/QUEUE_FAIL_ROW_LAYOUT_SPEC_2026-08-30.md L75-93 ; source SHA256=472c0bd2c5d8e0dbd505f7bf59c15df8b1ecd45ed2b9414a2af62dd7a2929498; extracted-lines SHA256=a6ddd82fd2f9a57511e9f6ca1a44749f92a434890c0bf980cebd006ca793f340.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Q01-04 / Q01-original-04: S:/Projects/PixivVault/gui.py L3030-3044 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=bc8ecd794381368c4fa3bea90eff35a68cbbae57762430d580a59999f531d7d6.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Q01-05 / Q01-original-05: S:/Projects/PixivVault/gui.py L3117-3176 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=e8fd44b1987b95a5e142ec7c0637341709462cc1fa31867a8ffae4ae85c25a1d.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Q01-06 / Q01-original-06: S:/Projects/PixivVault/gui.py L3647-3659 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=e294d8aa235de099423086dbcd220defa0f187409137f4bcea7dc10293d8c519.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/ui/job_views.py` SHA256=093395a2e6d47c18f4042abe9523756def5af218f50df5f61f2662169d6a9da7 ; `src/pixivvault_nicegui/ui/failed_records_view.py` SHA256=1f3d6641855021625fbf2811ccda35c493932bc772f21b7a200547f30db2c3de ; `src/pixivvault_nicegui/job_service.py` SHA256=2adcd974e4ce25c14ab33781f07595a7cdb075bb3b6e7432f582e7a98ecc0335.

**Saved operation evidence:** Historical referenced result: S:/Projects/PixivVault-NiceGUI-wt/jobs-integration/artifacts/jobs-integration-20261023/edge-final-02/result.json ; SHA256=2b64cabae86730ffaf6e24d0e0b103aa7c88ed9aba283ccbe2b4a90fc7157653 (source binding incomplete; see P3_324_Q_ACCEPTANCE_MAPPING)

**Supplementary recorded evidence (bounded; not a substitute for original operation):** artifacts/p3-339-native/jobs-packaged-r1/first.json ; SHA256=85350a79ac865083d4ce5576b73d96681633848afbbfb8eb335de065cc3c7d1d | artifacts/p3-339-native/jobs-packaged-r1/restart.json ; SHA256=e248c8e761dbdb573aa8229a8161fbb99789407ecbfedd22e2cead90b44f3854.

**Current applicability / concrete missing:** Seven original queue states, progress rows, refresh and terminal transitions are not all covered by a bound run.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### Q02 [B] 状態別にキューを絞る

**Original source index:** 元queueチップ契約. Original v8 literal reference IDs: v8-Q02-01, v8-Q02-02, v8-Q02-03. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-Q02-01 / Q02-original-01: S:/Projects/PixivVault/docs/FILTER_UNIFY_INVENTORY_queue_failed_dedup_2026-08-31.md L36-41 ; source SHA256=6e5184ce69098762aee230654cdf6d0251cf68df2ecf0f0caaf3542bb04af4bf; extracted-lines SHA256=e3a2701a10c28c75a24fdddc1e4643a2e5952edab26ae10e11cf0838e3e0953e.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Q02-02 / Q02-original-02: S:/Projects/PixivVault/gui.py L2911-2939 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=0628cf5a0c725c59152be93fb05b72b81fbaf625a73daee81b07dc999dfa3b9e.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Q02-03 / Q02-original-03: S:/Projects/PixivVault/gui.py L3450-3469 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=0212b4ed5ac0253238c85a6481cbeafb30a4663cf364c4b4a81a4b2835142742.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/ui/job_views.py` SHA256=093395a2e6d47c18f4042abe9523756def5af218f50df5f61f2662169d6a9da7 ; `src/pixivvault_nicegui/ui/failed_records_view.py` SHA256=1f3d6641855021625fbf2811ccda35c493932bc772f21b7a200547f30db2c3de ; `src/pixivvault_nicegui/job_service.py` SHA256=2adcd974e4ce25c14ab33781f07595a7cdb075bb3b6e7432f582e7a98ecc0335.

**Saved operation evidence:** Historical referenced result: S:/Projects/PixivVault-NiceGUI-wt/jobs-integration/artifacts/jobs-integration-20261023/edge-final-02/result.json ; SHA256=2b64cabae86730ffaf6e24d0e0b103aa7c88ed9aba283ccbe2b4a90fc7157653 (source binding incomplete; see P3_324_Q_ACCEPTANCE_MAPPING)

**Current applicability / concrete missing:** Status-filter categories/counts, empty and reset with mixed states need current GUI operations.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### Q03 [A] キューを作者名・IDで検索する

**Original source index:** 元queue検索契約. Original v8 literal reference IDs: v8-Q03-01, v8-Q03-02, v8-Q03-03. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-Q03-01 / Q03-original-01: S:/Projects/PixivVault/docs/FILTER_UNIFY_INVENTORY_queue_failed_dedup_2026-08-31.md L36-41 ; source SHA256=6e5184ce69098762aee230654cdf6d0251cf68df2ecf0f0caaf3542bb04af4bf; extracted-lines SHA256=e3a2701a10c28c75a24fdddc1e4643a2e5952edab26ae10e11cf0838e3e0953e.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Q03-02 / Q03-original-02: S:/Projects/PixivVault/gui.py L3379-3394 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=a3fea706373e4a3d025985469d7c8eca54dcb3e352ae1c2e606ccec75e2ab31c.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Q03-03 / Q03-original-03: S:/Projects/PixivVault/gui.py L3450-3469 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=0212b4ed5ac0253238c85a6481cbeafb30a4663cf364c4b4a81a4b2835142742.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/ui/job_views.py` SHA256=093395a2e6d47c18f4042abe9523756def5af218f50df5f61f2662169d6a9da7 ; `src/pixivvault_nicegui/ui/failed_records_view.py` SHA256=1f3d6641855021625fbf2811ccda35c493932bc772f21b7a200547f30db2c3de.

**Saved operation evidence:** artifacts/p3-340-q03-q12-worker7-edge-r2/result.json ; SHA256=1f5b31e148e70b0e59c655f14e1d9f085b70b611a72432701a6009c2b91a2ff8; recorded_success=True; related recorded files same where compared

**Current applicability / concrete missing:** Author-name, author-ID, no-match and clear verified in four current-source-bound GUI rounds; scope A only.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### Q04 [B] 個別ジョブを一時停止する

**Original source index:** SW U-8、BASELINE V11. Original v8 literal reference IDs: v8-Q04-01. Reference status: verbatim-v7-reference-set-transcribed.

**Frozen original source references (v8 literal transcription if available):**
- v8-Q04-01 / U-8: S:/Projects/PixivVault/docs/UI_SWEEP_CHECKLIST_2026-09-03.md L140-140 ; source SHA256=065d0e2c8351587cb56371faf3afcdb172f07efd83575e13dc891b586d4cef28; extracted-lines SHA256=87043e9483539b4e526a465e6ac184368adcef97881405b41aa33534c84bacbc.
  - Verbatim expectation: 押した直後に受理の表示が出る（停止に時間がかかるなら「停止要求を受け付けました」が先に出る・罠6）

**Original literal `expected_full`:**

```text
押した直後に受理の表示が出る（停止に時間がかかるなら「停止要求を受け付けました」が先に出る・罠6）
```

**Current source:** `src/pixivvault_nicegui/ui/job_views.py` SHA256=093395a2e6d47c18f4042abe9523756def5af218f50df5f61f2662169d6a9da7 ; `src/pixivvault_nicegui/ui/failed_records_view.py` SHA256=1f3d6641855021625fbf2811ccda35c493932bc772f21b7a200547f30db2c3de ; `src/pixivvault_nicegui/job_service.py` SHA256=2adcd974e4ce25c14ab33781f07595a7cdb075bb3b6e7432f582e7a98ecc0335.

**Saved operation evidence:** Historical referenced result: S:/Projects/PixivVault-NiceGUI-wt/jobs-integration/artifacts/jobs-integration-20261023/edge-final-02/result.json ; SHA256=2b64cabae86730ffaf6e24d0e0b103aa7c88ed9aba283ccbe2b4a90fc7157653 (source binding incomplete; see P3_324_Q_ACCEPTANCE_MAPPING)

**Current applicability / concrete missing:** Individual pause acknowledgement, queued/running transitions and persisted state need current GUI binding.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### Q05 [B] 個別ジョブを再開する

**Original source index:** SW U-8、BASELINE V11. Original v8 literal reference IDs: v8-Q05-01. Reference status: verbatim-v7-reference-set-transcribed.

**Frozen original source references (v8 literal transcription if available):**
- v8-Q05-01 / U-8: S:/Projects/PixivVault/docs/UI_SWEEP_CHECKLIST_2026-09-03.md L140-140 ; source SHA256=065d0e2c8351587cb56371faf3afcdb172f07efd83575e13dc891b586d4cef28; extracted-lines SHA256=87043e9483539b4e526a465e6ac184368adcef97881405b41aa33534c84bacbc.
  - Verbatim expectation: 押した直後に受理の表示が出る（停止に時間がかかるなら「停止要求を受け付けました」が先に出る・罠6）

**Original literal `expected_full`:**

```text
押した直後に受理の表示が出る（停止に時間がかかるなら「停止要求を受け付けました」が先に出る・罠6）
```

**Current source:** `src/pixivvault_nicegui/ui/job_views.py` SHA256=093395a2e6d47c18f4042abe9523756def5af218f50df5f61f2662169d6a9da7 ; `src/pixivvault_nicegui/ui/failed_records_view.py` SHA256=1f3d6641855021625fbf2811ccda35c493932bc772f21b7a200547f30db2c3de ; `src/pixivvault_nicegui/job_service.py` SHA256=2adcd974e4ce25c14ab33781f07595a7cdb075bb3b6e7432f582e7a98ecc0335.

**Saved operation evidence:** Historical referenced result: S:/Projects/PixivVault-NiceGUI-wt/jobs-integration/artifacts/jobs-integration-20261023/edge-final-02/result.json ; SHA256=2b64cabae86730ffaf6e24d0e0b103aa7c88ed9aba283ccbe2b4a90fc7157653 (source binding incomplete; see P3_324_Q_ACCEPTANCE_MAPPING)

**Current applicability / concrete missing:** Individual resume acknowledgement, transitions and persisted state need current GUI binding.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### Q06 [B] 個別ジョブを停止する

**Original source index:** SW U-8、BASELINE V11. Original v8 literal reference IDs: v8-Q06-01. Reference status: verbatim-v7-reference-set-transcribed.

**Frozen original source references (v8 literal transcription if available):**
- v8-Q06-01 / U-8: S:/Projects/PixivVault/docs/UI_SWEEP_CHECKLIST_2026-09-03.md L140-140 ; source SHA256=065d0e2c8351587cb56371faf3afcdb172f07efd83575e13dc891b586d4cef28; extracted-lines SHA256=87043e9483539b4e526a465e6ac184368adcef97881405b41aa33534c84bacbc.
  - Verbatim expectation: 押した直後に受理の表示が出る（停止に時間がかかるなら「停止要求を受け付けました」が先に出る・罠6）

**Original literal `expected_full`:**

```text
押した直後に受理の表示が出る（停止に時間がかかるなら「停止要求を受け付けました」が先に出る・罠6）
```

**Current source:** `src/pixivvault_nicegui/ui/job_views.py` SHA256=093395a2e6d47c18f4042abe9523756def5af218f50df5f61f2662169d6a9da7 ; `src/pixivvault_nicegui/ui/failed_records_view.py` SHA256=1f3d6641855021625fbf2811ccda35c493932bc772f21b7a200547f30db2c3de ; `src/pixivvault_nicegui/job_service.py` SHA256=2adcd974e4ce25c14ab33781f07595a7cdb075bb3b6e7432f582e7a98ecc0335.

**Saved operation evidence:** Historical referenced result: S:/Projects/PixivVault-NiceGUI-wt/jobs-integration/artifacts/jobs-integration-20261023/edge-final-02/result.json ; SHA256=2b64cabae86730ffaf6e24d0e0b103aa7c88ed9aba283ccbe2b4a90fc7157653 (source binding incomplete; see P3_324_Q_ACCEPTANCE_MAPPING)

**Current applicability / concrete missing:** Individual stop acknowledgement, queued/paused edge, terminal state and restart need current GUI binding.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### Q07 [B] 失敗したジョブを個別に再試行する

**Original source index:** 旧Q16、queue/failed双方の入口、attempt保持. Original v8 literal reference IDs: v8-Q07-01, v8-Q07-02, v8-Q07-03, v8-Q07-04, v8-Q07-05. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-Q07-01 / Q07-original-01: S:/Projects/PixivVault/docs/UI_GUIDE.md L77-81 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=864ddf58a5c524acec4d5f6682908d3e33f09b8ba39fc1f248ace6509bc7e7a1.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Q07-02 / Q07-original-02: S:/Projects/PixivVault/gui.py L4199-4219 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=a3a4aa5d1a21e62cfe4c25992e21a6421c9b0f14ca3e01f53e2a995ae7c9fca0.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Q07-03 / Q07-original-03: S:/Projects/PixivVault/gui.py L4231-4245 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=a15cd335f56aa240bb35cdbe4c2f4d90b3941ff166c60f58b8e9e2edd57ede01.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Q07-04 / Q07-original-04: S:/Projects/PixivVault/gui.py L3047-3055 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=cd12b249ec42d05784dbd3027b707278dfbf78074ebce6f7775c0408bb63f41a.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Q07-05 / Q07-original-05: S:/Projects/PixivVault/server.py L153-183 ; source SHA256=a1dfbb1de81cc58432166eec7beae4dfc98b72f9437c92af1e9743a3f4a66be0; extracted-lines SHA256=17d8981b3b06dcf40e208f37a75960ad89c70efb4f7b4d72d25501e26c264cd7.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/ui/job_views.py` SHA256=093395a2e6d47c18f4042abe9523756def5af218f50df5f61f2662169d6a9da7 ; `src/pixivvault_nicegui/ui/failed_records_view.py` SHA256=1f3d6641855021625fbf2811ccda35c493932bc772f21b7a200547f30db2c3de ; `src/pixivvault_nicegui/job_service.py` SHA256=2adcd974e4ce25c14ab33781f07595a7cdb075bb3b6e7432f582e7a98ecc0335.

**Saved operation evidence:** Historical referenced result: S:/Projects/PixivVault-NiceGUI-wt/failed-queue-optin-retry/artifacts/failed-managed-20261023/edge-final-terminal-retry/result.json ; SHA256=df1f6dad2e6b4415b800bd0e5fc6fe535abf57885cfcfc2db0835dc6f143d967 (source binding incomplete; see P3_324_Q_ACCEPTANCE_MAPPING)

**Current applicability / concrete missing:** Both queue and failed-list retry entrances, attempt count and terminal managed receipt need fresh connected GUI evidence.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### Q08 [B] ジョブ履歴を削除する

**Original source index:** UG queue、保存済み媒体は保持. Original v8 literal reference IDs: v8-Q08-01, v8-Q08-02, v8-Q08-03. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-Q08-01 / Q08-original-01: S:/Projects/PixivVault/docs/UI_GUIDE.md L70-74 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=223ce1682adab64a74c23cd3666e9742a245eaf9a24da673d618aa15c7ff63a8.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Q08-02 / Q08-original-02: S:/Projects/PixivVault/gui.py L3229-3257 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=277da74bd5c3b785e347ce1114599272dc5f8d01dc7d056649bdc8e9db91b1e9.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Q08-03 / Q08-original-03: S:/Projects/PixivVault/server.py L1409-1431 ; source SHA256=a1dfbb1de81cc58432166eec7beae4dfc98b72f9437c92af1e9743a3f4a66be0; extracted-lines SHA256=b68669aaf4c213b40bf92657c1d54075b52e797ae0eb1d6125668196230489db.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/ui/job_views.py` SHA256=093395a2e6d47c18f4042abe9523756def5af218f50df5f61f2662169d6a9da7 ; `src/pixivvault_nicegui/ui/failed_records_view.py` SHA256=1f3d6641855021625fbf2811ccda35c493932bc772f21b7a200547f30db2c3de ; `src/pixivvault_nicegui/job_service.py` SHA256=2adcd974e4ce25c14ab33781f07595a7cdb075bb3b6e7432f582e7a98ecc0335.

**Saved operation evidence:** Historical referenced result: S:/Projects/PixivVault-NiceGUI-wt/jobs-integration/artifacts/jobs-integration-20261023/edge-final-02/result.json ; SHA256=2b64cabae86730ffaf6e24d0e0b103aa7c88ed9aba283ccbe2b4a90fc7157653 (source binding incomplete; see P3_324_Q_ACCEPTANCE_MAPPING)

**Supplementary recorded evidence (bounded; not a substitute for original operation):** artifacts/p3-339-native/jobs-packaged-r1/first.json ; SHA256=85350a79ac865083d4ce5576b73d96681633848afbbfb8eb335de065cc3c7d1d | artifacts/p3-339-native/jobs-packaged-r1/restart.json ; SHA256=e248c8e761dbdb573aa8229a8161fbb99789407ecbfedd22e2cead90b44f3854.

**Current applicability / concrete missing:** Deletion versus stored media preservation and restart persistence need connected GUI evidence.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### Q09 [B] ジョブを一括停止する

**Original source index:** UG queue一括制御. Original v8 literal reference IDs: v8-Q09-01, v8-Q09-02, v8-Q09-03, v8-Q09-04. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-Q09-01 / Q09-original-01: S:/Projects/PixivVault/docs/UI_GUIDE.md L70-74 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=223ce1682adab64a74c23cd3666e9742a245eaf9a24da673d618aa15c7ff63a8.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Q09-02 / Q09-original-02: S:/Projects/PixivVault/gui.py L3259-3293 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=4abd48e34bfda4df2de76d5305ee4da134d92bc313efd86ebd1d635dd89a14dc.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Q09-03 / Q09-original-03: S:/Projects/PixivVault/gui.py L3295-3331 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=05e4ef32791f4810f4e910905bf0f44ee8bbe14737c390ba8c0cee582dc401ca.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Q09-04 / Q09-original-04: S:/Projects/PixivVault/server.py L1463-1473 ; source SHA256=a1dfbb1de81cc58432166eec7beae4dfc98b72f9437c92af1e9743a3f4a66be0; extracted-lines SHA256=41f4e6c79e52f8686977f45489c5a67ef27038b23d8dc730414dfc0874cf5b2c.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/ui/job_views.py` SHA256=093395a2e6d47c18f4042abe9523756def5af218f50df5f61f2662169d6a9da7 ; `src/pixivvault_nicegui/ui/failed_records_view.py` SHA256=1f3d6641855021625fbf2811ccda35c493932bc772f21b7a200547f30db2c3de ; `src/pixivvault_nicegui/job_service.py` SHA256=2adcd974e4ce25c14ab33781f07595a7cdb075bb3b6e7432f582e7a98ecc0335.

**Saved operation evidence:** Historical referenced result: S:/Projects/PixivVault-NiceGUI-wt/jobs-integration/artifacts/jobs-integration-20261023/edge-final-02/result.json ; SHA256=2b64cabae86730ffaf6e24d0e0b103aa7c88ed9aba283ccbe2b4a90fc7157653 (source binding incomplete; see P3_324_Q_ACCEPTANCE_MAPPING)

**Current applicability / concrete missing:** Mixed-state bulk stop, acknowledgement and terminal count need current GUI observations.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### Q10 [B] ジョブを一括再開する

**Original source index:** UG queue一括制御. Original v8 literal reference IDs: v8-Q10-01, v8-Q10-02, v8-Q10-03, v8-Q10-04. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-Q10-01 / Q10-original-01: S:/Projects/PixivVault/docs/UI_GUIDE.md L70-74 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=223ce1682adab64a74c23cd3666e9742a245eaf9a24da673d618aa15c7ff63a8.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Q10-02 / Q10-original-02: S:/Projects/PixivVault/gui.py L2918-2925 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=dba78a392ad2e6cbda18db4b4ed807362465ddfb1ed74f9ad004c30ef5d30893.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Q10-03 / Q10-original-03: S:/Projects/PixivVault/gui.py L3267-3278 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=ef78e58ef41a28aa4a5b142563f73ac22ea40db932f341ee8d5db76b31caae69.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Q10-04 / Q10-original-04: S:/Projects/PixivVault/server.py L1450-1461 ; source SHA256=a1dfbb1de81cc58432166eec7beae4dfc98b72f9437c92af1e9743a3f4a66be0; extracted-lines SHA256=7ca93272f47263e86e6535ebb6c708beecb053bb78b0c2e68276a51d5efe663d.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/ui/job_views.py` SHA256=093395a2e6d47c18f4042abe9523756def5af218f50df5f61f2662169d6a9da7 ; `src/pixivvault_nicegui/ui/failed_records_view.py` SHA256=1f3d6641855021625fbf2811ccda35c493932bc772f21b7a200547f30db2c3de ; `src/pixivvault_nicegui/job_service.py` SHA256=2adcd974e4ce25c14ab33781f07595a7cdb075bb3b6e7432f582e7a98ecc0335.

**Saved operation evidence:** Historical referenced result: S:/Projects/PixivVault-NiceGUI-wt/jobs-integration/artifacts/jobs-integration-20261023/edge-final-02/result.json ; SHA256=2b64cabae86730ffaf6e24d0e0b103aa7c88ed9aba283ccbe2b4a90fc7157653 (source binding incomplete; see P3_324_Q_ACCEPTANCE_MAPPING)

**Current applicability / concrete missing:** Mixed-state bulk resume, acknowledgement and terminal count need current GUI observations.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### Q11 [B] ジョブを一括一時停止する

**Original source index:** UG queue一括制御. Original v8 literal reference IDs: v8-Q11-01, v8-Q11-02, v8-Q11-03, v8-Q11-04. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-Q11-01 / Q11-original-01: S:/Projects/PixivVault/docs/UI_GUIDE.md L70-74 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=223ce1682adab64a74c23cd3666e9742a245eaf9a24da673d618aa15c7ff63a8.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Q11-02 / Q11-original-02: S:/Projects/PixivVault/gui.py L3267-3273 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=2660f08a1100b06bb36fbe394e8ea3fb449bb899480ad50b35f0f67a5a69d44f.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Q11-03 / Q11-original-03: S:/Projects/PixivVault/gui.py L3295-3331 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=05e4ef32791f4810f4e910905bf0f44ee8bbe14737c390ba8c0cee582dc401ca.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Q11-04 / Q11-original-04: S:/Projects/PixivVault/server.py L1435-1448 ; source SHA256=a1dfbb1de81cc58432166eec7beae4dfc98b72f9437c92af1e9743a3f4a66be0; extracted-lines SHA256=c882bbf821ae0f8e89ec1e397ef00cafa68fab57e51099bd185ea02d917d8699.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/ui/job_views.py` SHA256=093395a2e6d47c18f4042abe9523756def5af218f50df5f61f2662169d6a9da7 ; `src/pixivvault_nicegui/ui/failed_records_view.py` SHA256=1f3d6641855021625fbf2811ccda35c493932bc772f21b7a200547f30db2c3de ; `src/pixivvault_nicegui/job_service.py` SHA256=2adcd974e4ce25c14ab33781f07595a7cdb075bb3b6e7432f582e7a98ecc0335.

**Saved operation evidence:** Historical referenced result: S:/Projects/PixivVault-NiceGUI-wt/jobs-integration/artifacts/jobs-integration-20261023/edge-final-02/result.json ; SHA256=2b64cabae86730ffaf6e24d0e0b103aa7c88ed9aba283ccbe2b4a90fc7157653 (source binding incomplete; see P3_324_Q_ACCEPTANCE_MAPPING)

**Current applicability / concrete missing:** Mixed-state bulk pause, acknowledgement and terminal count need current GUI observations.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### Q12 [A] ジョブを一括削除する

**Original source index:** UG queue、全件確認・保全. Original v8 literal reference IDs: v8-Q12-01, v8-Q12-02, v8-Q12-03, v8-Q12-04. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-Q12-01 / Q12-original-01: S:/Projects/PixivVault/docs/UI_GUIDE.md L70-74 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=223ce1682adab64a74c23cd3666e9742a245eaf9a24da673d618aa15c7ff63a8.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Q12-02 / Q12-original-02: S:/Projects/PixivVault/gui.py L3333-3371 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=207a8a7f18490f9a8183c155ed14407f1e10565b785c3105603cc3756cd920ff.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Q12-03 / Q12-original-03: S:/Projects/PixivVault/gui.py L3259-3331 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=10392d4144502891835e8fdb9aadc2a925e40743751c402f9fe79546ac6d4637.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Q12-04 / Q12-original-04: S:/Projects/PixivVault/server.py L1409-1431 ; source SHA256=a1dfbb1de81cc58432166eec7beae4dfc98b72f9437c92af1e9743a3f4a66be0; extracted-lines SHA256=b68669aaf4c213b40bf92657c1d54075b52e797ae0eb1d6125668196230489db.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/ui/job_views.py` SHA256=093395a2e6d47c18f4042abe9523756def5af218f50df5f61f2662169d6a9da7 ; `src/pixivvault_nicegui/job_service.py` SHA256=2adcd974e4ce25c14ab33781f07595a7cdb075bb3b6e7432f582e7a98ecc0335.

**Saved operation evidence:** artifacts/p3-340-q03-q12-worker7-edge-r2/result.json ; SHA256=1f5b31e148e70b0e59c655f14e1d9f085b70b611a72432701a6009c2b91a2ff8; recorded_success=True; related recorded files same where compared

**Current applicability / concrete missing:** Selected count frozen, visible confirmation/cancel/confirm, running stop, persistent history deletion and preserved files verified in four GUI rounds; scope A only.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### Q13 [B] 旧失敗リストを表示する

**Original source index:** 行契約B、全文理由・53px設計値. Original v8 literal reference IDs: v8-Q13-01, v8-Q13-02, v8-Q13-03, v8-Q13-04. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-Q13-01 / Q13-original-01: S:/Projects/PixivVault/docs/UI_GUIDE.md L77-81 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=864ddf58a5c524acec4d5f6682908d3e33f09b8ba39fc1f248ace6509bc7e7a1.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Q13-02 / Q13-original-02: S:/Projects/PixivVault/docs/QUEUE_FAIL_ROW_LAYOUT_SPEC_2026-08-30.md L95-106 ; source SHA256=472c0bd2c5d8e0dbd505f7bf59c15df8b1ecd45ed2b9414a2af62dd7a2929498; extracted-lines SHA256=06f8a81ad724a29f53966c229e99f6dbda3dd2e18f2ea76f7f88f4ecd313a21a.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Q13-03 / Q13-original-03: S:/Projects/PixivVault/gui.py L4199-4246 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=f7aed9d32f31bdcfa37afd5f3aa614787e3317efc48f8ab28e27f8c35c9465a2.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Q13-04 / Q13-original-04: S:/Projects/PixivVault/gui.py L4248-4286 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=ac300a70ea961feabdddf1e79e89599792f0c880abc448670e500835326c827a.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/ui/job_views.py` SHA256=093395a2e6d47c18f4042abe9523756def5af218f50df5f61f2662169d6a9da7 ; `src/pixivvault_nicegui/ui/failed_records_view.py` SHA256=1f3d6641855021625fbf2811ccda35c493932bc772f21b7a200547f30db2c3de ; `src/pixivvault_nicegui/job_service.py` SHA256=2adcd974e4ce25c14ab33781f07595a7cdb075bb3b6e7432f582e7a98ecc0335.

**Saved operation evidence:** Historical referenced result: S:/Projects/PixivVault-NiceGUI-wt/failed-records-integration/artifacts/failed-records-integration-20261023/edge-first/result.json ; SHA256=6b846b9ed96644ef1937e07a8bec210411d68ae883dd8a01d3fdcc2fbd4e48ab (source binding incomplete; see P3_324_Q_ACCEPTANCE_MAPPING)

**Current applicability / concrete missing:** Full original failed-list reason text, row density, 6 categories, pagination and restart need bound view observations.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### Q14 [B] 失敗理由カテゴリで絞る

**Original source index:** 旧失敗リスト6分類. Original v8 literal reference IDs: v8-Q14-01, v8-Q14-02, v8-Q14-03. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-Q14-01 / Q14-original-01: S:/Projects/PixivVault/docs/FILTER_UNIFY_INVENTORY_queue_failed_dedup_2026-08-31.md L54-61 ; source SHA256=6e5184ce69098762aee230654cdf6d0251cf68df2ecf0f0caaf3542bb04af4bf; extracted-lines SHA256=febb4dab8987837442690333a1c7d05a6d57234ab78c85695d48c194ee4d58c4.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Q14-02 / Q14-original-02: S:/Projects/PixivVault/gui.py L4080-4097 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=9349ad850919b7b99c6a6c85573d1878db06eefa9b41341e1423cba155b521f1.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Q14-03 / Q14-original-03: S:/Projects/PixivVault/gui.py L4151-4180 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=96ba7c1478b63cf16ffdaa16624b2dbd800ce05bb74e0cbd639a4bf5728e7fc6.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/ui/job_views.py` SHA256=093395a2e6d47c18f4042abe9523756def5af218f50df5f61f2662169d6a9da7 ; `src/pixivvault_nicegui/ui/failed_records_view.py` SHA256=1f3d6641855021625fbf2811ccda35c493932bc772f21b7a200547f30db2c3de ; `src/pixivvault_nicegui/job_service.py` SHA256=2adcd974e4ce25c14ab33781f07595a7cdb075bb3b6e7432f582e7a98ecc0335.

**Saved operation evidence:** Historical referenced result: S:/Projects/PixivVault-NiceGUI-wt/failed-records-integration/artifacts/failed-records-integration-20261023/edge-first/result.json ; SHA256=6b846b9ed96644ef1937e07a8bec210411d68ae883dd8a01d3fdcc2fbd4e48ab (source binding incomplete; see P3_324_Q_ACCEPTANCE_MAPPING)

**Current applicability / concrete missing:** All original failure classifications and current source->view filters/clears need connected evidence.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### Q15 [B] 失敗リストをタイトル・ID検索する

**Original source index:** 旧失敗リスト検索. Original v8 literal reference IDs: v8-Q15-01, v8-Q15-02, v8-Q15-03, v8-Q15-04. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-Q15-01 / Q15-original-01: S:/Projects/PixivVault/docs/FILTER_UNIFY_INVENTORY_queue_failed_dedup_2026-08-31.md L56-61 ; source SHA256=6e5184ce69098762aee230654cdf6d0251cf68df2ecf0f0caaf3542bb04af4bf; extracted-lines SHA256=f411ed87da43a23f76554c9050e7489190746d43e7169529c0973c87c18590c4.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Q15-02 / Q15-original-02: S:/Projects/PixivVault/gui.py L4111-4125 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=3834acb4e8032ef54183344400852069e1a54f3499fc62ee622bb4e3cf552985.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Q15-03 / Q15-original-03: S:/Projects/PixivVault/gui.py L4151-4186 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=84ee5ce330f6abc9b312dc05dd068f13f9fabae24120c89cd6d29d7a5b0f2ef0.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Q15-04 / Q15-original-04: S:/Projects/PixivVault/gui.py L4263-4286 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=60e76ce12f31f42d2a0b29f60f594e4a8d06774023cc682f83ccea6ad310a676.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/ui/job_views.py` SHA256=093395a2e6d47c18f4042abe9523756def5af218f50df5f61f2662169d6a9da7 ; `src/pixivvault_nicegui/ui/failed_records_view.py` SHA256=1f3d6641855021625fbf2811ccda35c493932bc772f21b7a200547f30db2c3de ; `src/pixivvault_nicegui/job_service.py` SHA256=2adcd974e4ce25c14ab33781f07595a7cdb075bb3b6e7432f582e7a98ecc0335.

**Saved operation evidence:** Historical referenced result: S:/Projects/PixivVault-NiceGUI-wt/failed-records-integration/artifacts/failed-records-integration-20261023/edge-first/result.json ; SHA256=6b846b9ed96644ef1937e07a8bec210411d68ae883dd8a01d3fdcc2fbd4e48ab (source binding incomplete; see P3_324_Q_ACCEPTANCE_MAPPING)

**Current applicability / concrete missing:** Title/ID no-match, clear, multiple pages and current source->view need connected evidence.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### Q17 [B] 失敗項目を一括再試行する

**Original source index:** UG 77–81. Original v8 literal reference IDs: v8-Q17-01, v8-Q17-02, v8-Q17-03. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-Q17-01 / Q17-original-01: S:/Projects/PixivVault/docs/UI_GUIDE.md L77-81 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=864ddf58a5c524acec4d5f6682908d3e33f09b8ba39fc1f248ace6509bc7e7a1.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Q17-02 / Q17-original-02: S:/Projects/PixivVault/gui.py L4151-4173 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=a2216a85caeee7209029d6e80646180f5927d6fc5463208e2f0079aa6a6340b5.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Q17-03 / Q17-original-03: S:/Projects/PixivVault/gui.py L4288-4309 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=443d76812276c398adf053ee19ece353c4a4acc5ccffd4b2e27ca944575003dd.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/ui/job_views.py` SHA256=093395a2e6d47c18f4042abe9523756def5af218f50df5f61f2662169d6a9da7 ; `src/pixivvault_nicegui/ui/failed_records_view.py` SHA256=1f3d6641855021625fbf2811ccda35c493932bc772f21b7a200547f30db2c3de ; `src/pixivvault_nicegui/job_service.py` SHA256=2adcd974e4ce25c14ab33781f07595a7cdb075bb3b6e7432f582e7a98ecc0335.

**Saved operation evidence:** Historical referenced result: S:/Projects/PixivVault-NiceGUI-wt/failed-queue-optin-retry/artifacts/failed-managed-20261023/edge-final-terminal-retry/result.json ; SHA256=df1f6dad2e6b4415b800bd0e5fc6fe535abf57885cfcfc2db0835dc6f143d967 (source binding incomplete; see P3_324_Q_ACCEPTANCE_MAPPING)

**Current applicability / concrete missing:** Bulk retry exact eligible subset, noneligible retention, partial failure and accepted attempts need actual managed GUI run.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### Q18 [A] 失敗記録を1件削除する

**Original source index:** UG失敗リスト. Original v8 literal reference IDs: v8-Q18-01, v8-Q18-02, v8-Q18-03. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-Q18-01 / Q18-original-01: S:/Projects/PixivVault/docs/UI_GUIDE.md L77-81 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=864ddf58a5c524acec4d5f6682908d3e33f09b8ba39fc1f248ace6509bc7e7a1.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Q18-02 / Q18-original-02: S:/Projects/PixivVault/gui.py L4221-4225 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=2c8c19c120cd011858ab666fed31c781303a7d5bea2b28234b565227aeb35850.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Q18-03 / Q18-original-03: S:/Projects/PixivVault/gui.py L4231-4245 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=a15cd335f56aa240bb35cdbe4c2f4d90b3941ff166c60f58b8e9e2edd57ede01.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/ui/failed_records_view.py` SHA256=1f3d6641855021625fbf2811ccda35c493932bc772f21b7a200547f30db2c3de ; `src/pixivvault_nicegui/failed_record_dismissals.py` SHA256=d4e202e5cb5027bd4991b8e625245285c5f8bdaf9c140672fdb6db5a5609768a.

**Saved operation evidence:** artifacts/p3-331-failure-removal-edge-r1/result.json ; SHA256=d290412151dede76a76ae0e9dcae599e0f25dbd7b581aac34aa02c1d629aa2e7; recorded_success=True; related recorded files same where compared

**Current applicability / concrete missing:** Single confirmation/cancel, 17->16, CAS and restart persistence observed in four GUI rounds; scope A only.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### Q19 [A] 失敗記録を全件クリアする

**Original source index:** UG失敗リスト. Original v8 literal reference IDs: v8-Q19-01, v8-Q19-02. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-Q19-01 / Q19-original-01: S:/Projects/PixivVault/docs/UI_GUIDE.md L77-81 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=864ddf58a5c524acec4d5f6682908d3e33f09b8ba39fc1f248ace6509bc7e7a1.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Q19-02 / Q19-original-02: S:/Projects/PixivVault/gui.py L4311-4327 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=2dd2b26ed2bcfd0797346102b1ad2d6fd5d79aa14dd24992b52414eee1425f7b.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/ui/failed_records_view.py` SHA256=1f3d6641855021625fbf2811ccda35c493932bc772f21b7a200547f30db2c3de ; `src/pixivvault_nicegui/failed_record_dismissals.py` SHA256=d4e202e5cb5027bd4991b8e625245285c5f8bdaf9c140672fdb6db5a5609768a.

**Saved operation evidence:** artifacts/p3-331-failure-removal-edge-r1/result.json ; SHA256=d290412151dede76a76ae0e9dcae599e0f25dbd7b581aac34aa02c1d629aa2e7; recorded_success=True; related recorded files same where compared

**Current applicability / concrete missing:** Unfiltered all-remaining count, confirmation/cancel, 0/reload and CAS observed in four GUI rounds; scope A only.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### Q20 [A] 失敗リストをCSVへ出力する

**Original source index:** UG失敗リスト、全列・全件フィルタ. Original v8 literal reference IDs: v8-Q20-01, v8-Q20-02, v8-Q20-03. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-Q20-01 / Q20-original-01: S:/Projects/PixivVault/docs/UI_GUIDE.md L77-81 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=864ddf58a5c524acec4d5f6682908d3e33f09b8ba39fc1f248ace6509bc7e7a1.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Q20-02 / Q20-original-02: S:/Projects/PixivVault/gui.py L4151-4173 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=a2216a85caeee7209029d6e80646180f5927d6fc5463208e2f0079aa6a6340b5.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Q20-03 / Q20-original-03: S:/Projects/PixivVault/gui.py L4329-4342 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=03795822e1d8f2ab0205bc1a0978c5d4ee999a3f7a607fdfd69d0d24ae040a07.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/ui/failed_records_view.py` SHA256=1f3d6641855021625fbf2811ccda35c493932bc772f21b7a200547f30db2c3de ; `src/pixivvault_nicegui/failed_records_state.py` SHA256=652fefe2a6724b950ed73d821796dc337cbe6956c1c2b65d916491f246c7a63b.

**Saved operation evidence:** artifacts/p3-331-failure-removal-edge-r1/result.json ; SHA256=d290412151dede76a76ae0e9dcae599e0f25dbd7b581aac34aa02c1d629aa2e7; recorded_success=True; related recorded files same where compared

**Current applicability / concrete missing:** Full vs filtered CSV bytes, BOM, formula handling, complete columns across pagination observed; scope A only.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### X01 [B] 重複候補をスキャン・比較する

**Original source index:** 重複統合一覧仕様§1/4、画質・組. Original v8 literal reference IDs: v8-X01-01, v8-X01-02, v8-X01-03, v8-X01-04, v8-X01-05, v8-X01-06, v8-X01-07. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-X01-01 / X01-original-01: S:/Projects/PixivVault/docs/design/DUPLICATE_CLEANUP_SPEC_2026-08-15.md L33-49 ; source SHA256=bcd649d86561a4e415d0b8024b3033612e02ccbed0057af813e0b2c6f61857c8; extracted-lines SHA256=899b78a872c7a934d5010fd0a2deb1005c718bbd2b9dea2dcee16d970772e301.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-X01-02 / X01-original-02: S:/Projects/PixivVault/docs/design/DUPLICATE_CLEANUP_SPEC_2026-08-15.md L65-94 ; source SHA256=bcd649d86561a4e415d0b8024b3033612e02ccbed0057af813e0b2c6f61857c8; extracted-lines SHA256=9da50cc674c2c3955b86173acccafae3615e565605ec6f3c66e75c7192294a78.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-X01-03 / X01-original-03: S:/Projects/PixivVault/docs/DEDUP_COMPARE_UNIFIED_LIST_SPEC_2026-08-29.md L7-24 ; source SHA256=837727b42bf337b84a8ee31c77753058cc00951027063ba2636023ad2375c3f5; extracted-lines SHA256=21bc3c5968a41c9c226c2992a4401c52975ab66440ccdac73e3ec02ce33da32a.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-X01-04 / X01-original-04: S:/Projects/PixivVault/docs/DEDUP_COMPARE_UNIFIED_LIST_SPEC_2026-08-29.md L71-93 ; source SHA256=837727b42bf337b84a8ee31c77753058cc00951027063ba2636023ad2375c3f5; extracted-lines SHA256=f1325e018b1c0e122ac33ea04eb143b898fa7bac4c982d5d2f4214e6e4cd4aaa.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-X01-05 / X01-original-05: S:/Projects/PixivVault/docs/design/DUPLICATE_DELETE_SPEC_2026-08-15.md L32-43 ; source SHA256=293f1cda4d03c4deceaee04ef531104b573ff4b20d5f78b4c7034ba715ae333e; extracted-lines SHA256=734af5f30bb10a87ce41b905ab56e912f4721502e47499ba86ec8c48f8c817a5.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-X01-06 / X01-original-06: S:/Projects/PixivVault/gui.py L5395-5436 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=d232626666d2d3bf1409e26b243b55aa7c331cbb2599976731263b611331ce68.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-X01-07 / X01-original-07: S:/Projects/PixivVault/gui.py L5704-5758 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=2f5a10bff9345479178578c5747a71eb1f9993ecd83d40ae179f68e6d8b1be8e.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/ui/duplicate_view.py` SHA256=ec3893561b753201a53d2901091459ff6a9eaf320f337b08124b231ffa8b42de ; `src/pixivvault_nicegui/managed_quarantine.py` SHA256=b1db903a1de41d7a1cd46ba79e126d67a4ff3d4989dbdeb55b1c1f45fbb14567 ; `src/pixivvault_nicegui/duplicate_inspection.py` SHA256=1f5469053bae2f3474703727117ab463fe029abd6f98b7c5060635f27b15d7c8.

**Saved operation evidence:** No operation-level result.json bound in examined sources.

**Current applicability / concrete missing:** Candidate scan comparison, image quality, pair grouping and results after re-scan need evidence.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### X02 [B] 未判定/近似候補を切り替える

**Original source index:** 原2表示モード. Original v8 literal reference IDs: v8-X02-01, v8-X02-02, v8-X02-03, v8-X02-04. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-X02-01 / X02-original-01: S:/Projects/PixivVault/docs/FILTER_UNIFY_INVENTORY_queue_failed_dedup_2026-08-31.md L78-84 ; source SHA256=6e5184ce69098762aee230654cdf6d0251cf68df2ecf0f0caaf3542bb04af4bf; extracted-lines SHA256=120aa9733a7e68f671f54ff5acfeadc3fd7c3985d20a38b216467ccd2181a9ef.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-X02-02 / X02-original-02: S:/Projects/PixivVault/docs/design/DUPLICATE_CLEANUP_SPEC_2026-08-15.md L351-384 ; source SHA256=bcd649d86561a4e415d0b8024b3033612e02ccbed0057af813e0b2c6f61857c8; extracted-lines SHA256=d1613e5e191ad55aee2f12cadbdaee0c590949ca649f11c1fb2f4bb86baf1a54.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-X02-03 / X02-original-03: S:/Projects/PixivVault/docs/DEDUP_COMPARE_UNIFIED_LIST_SPEC_2026-08-29.md L17-24 ; source SHA256=837727b42bf337b84a8ee31c77753058cc00951027063ba2636023ad2375c3f5; extracted-lines SHA256=8f3f81802abf41626d9ccddf6ce2166eda174f2715192bf85c56d7a25150119e.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-X02-04 / X02-original-04: S:/Projects/PixivVault/gui.py L5439-5471 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=65e993d168a78fde591d7c2ed39fe4cfe58b57445c3a594d13e01bd287eb527c.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/ui/duplicate_view.py` SHA256=ec3893561b753201a53d2901091459ff6a9eaf320f337b08124b231ffa8b42de ; `src/pixivvault_nicegui/managed_quarantine.py` SHA256=b1db903a1de41d7a1cd46ba79e126d67a4ff3d4989dbdeb55b1c1f45fbb14567 ; `src/pixivvault_nicegui/duplicate_inspection.py` SHA256=1f5469053bae2f3474703727117ab463fe029abd6f98b7c5060635f27b15d7c8.

**Saved operation evidence:** artifacts/p3-dedup-mode-20261028/worker2-edge-1280-r1/result.json ; SHA256=1157e48d81f058923808af3d60b08db701650d1ef11c0f30814cd91c148496e7; recorded_success=True; no per-source before binding in result

**Current applicability / concrete missing:** Older mode UI passed but current duplicate view and backend changed; rebind and confirm unknown/near inventories.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### X03 [B] 作者名・IDで候補を検索する

**Original source index:** 原重複整理検索. Original v8 literal reference IDs: v8-X03-01, v8-X03-02, v8-X03-03, v8-X03-04, v8-X03-05, v8-X03-06. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-X03-01 / X03-original-01: S:/Projects/PixivVault/docs/FILTER_UNIFY_INVENTORY_queue_failed_dedup_2026-08-31.md L78-84 ; source SHA256=6e5184ce69098762aee230654cdf6d0251cf68df2ecf0f0caaf3542bb04af4bf; extracted-lines SHA256=120aa9733a7e68f671f54ff5acfeadc3fd7c3985d20a38b216467ccd2181a9ef.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-X03-02 / X03-original-02: S:/Projects/PixivVault/gui.py L5068-5081 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=834cb802f70b1334aaf4b9cfe99835edc8d93ad4405d4430462a9c73509224eb.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-X03-03 / X03-original-03: S:/Projects/PixivVault/gui.py L5122-5136 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=3c4ae76c78ba5ace09db862752477016fd1b356d9a4866ee52129d3083e27f88.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-X03-04 / X03-original-04: S:/Projects/PixivVault/gui.py L5359-5374 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=216d7158b691a358db9d6bf90c0774812489f11cb6c230ce0f608ba3dfda9223.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-X03-05 / X03-original-05: S:/Projects/PixivVault/gui.py L5395-5419 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=687a645554e482dbd42a720aad2d4331024d6aa0318358385b3ba5e5e169c7d0.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-X03-06 / X03-original-06: S:/Projects/PixivVault/gui.py L5473-5498 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=86855fdcffadd3798ea8a4c2fdbf4133ea45fc5b2fd8e54dc6668cae177ecad0.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/ui/duplicate_view.py` SHA256=ec3893561b753201a53d2901091459ff6a9eaf320f337b08124b231ffa8b42de ; `src/pixivvault_nicegui/managed_quarantine.py` SHA256=b1db903a1de41d7a1cd46ba79e126d67a4ff3d4989dbdeb55b1c1f45fbb14567 ; `src/pixivvault_nicegui/duplicate_inspection.py` SHA256=1f5469053bae2f3474703727117ab463fe029abd6f98b7c5060635f27b15d7c8.

**Saved operation evidence:** No operation-level result.json bound in examined sources.

**Current applicability / concrete missing:** Author search by name and ID across both modes and empty/reset need current evidence.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### X04 [B] 重複候補のサムネイルサイズを変更する

**Original source index:** 小/中/大、選択保持. Original v8 literal reference IDs: v8-X04-01, v8-X04-02, v8-X04-03, v8-X04-04, v8-X04-05. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-X04-01 / X04-original-01: S:/Projects/PixivVault/docs/FILTER_UNIFY_INVENTORY_queue_failed_dedup_2026-08-31.md L82-84 ; source SHA256=6e5184ce69098762aee230654cdf6d0251cf68df2ecf0f0caaf3542bb04af4bf; extracted-lines SHA256=e63bd6709c79e65e51f0573d950b3d50335173783a3d88a3aebd405be7f195ec.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-X04-02 / X04-original-02: S:/Projects/PixivVault/docs/DEDUP_COMPARE_UNIFIED_LIST_SPEC_2026-08-29.md L63-69 ; source SHA256=837727b42bf337b84a8ee31c77753058cc00951027063ba2636023ad2375c3f5; extracted-lines SHA256=866788a17bd8ffeea37735ab26f5c62d2a174def006986e4fe2943e8e5abec44.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-X04-03 / X04-original-03: S:/Projects/PixivVault/docs/DEDUP_COMPARE_UNIFIED_LIST_SPEC_2026-08-29.md L133-136 ; source SHA256=837727b42bf337b84a8ee31c77753058cc00951027063ba2636023ad2375c3f5; extracted-lines SHA256=cc7ac6dbb85d31fbf5fe60f7140884bb9ccd1c48ffb344e884f25380419f32d7.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-X04-04 / X04-original-04: S:/Projects/PixivVault/gui.py L5206-5219 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=9dca8ef2f39b632658e34c8b2aae01ad2921b94174d53e7fcbdc786e67a150be.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-X04-05 / X04-original-05: S:/Projects/PixivVault/gui.py L5761-5799 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=e1309b1c0da9741d42b21729b3d4bf2b014b5692b220347e177d954929a6e9e1.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/ui/duplicate_view.py` SHA256=ec3893561b753201a53d2901091459ff6a9eaf320f337b08124b231ffa8b42de ; `src/pixivvault_nicegui/managed_quarantine.py` SHA256=b1db903a1de41d7a1cd46ba79e126d67a4ff3d4989dbdeb55b1c1f45fbb14567 ; `src/pixivvault_nicegui/duplicate_inspection.py` SHA256=1f5469053bae2f3474703727117ab463fe029abd6f98b7c5060635f27b15d7c8.

**Saved operation evidence:** artifacts/p3-dedup-mode-20261028/worker2-edge-1920-light-r1/result.json ; SHA256=3dae255c5644174d5818769747aef394e43240c11f4d2c8c1eeae508400b2d89; recorded_success=True; no per-source before binding in result

**Current applicability / concrete missing:** Older size UI passed but duplicate view changed; need current selection retention across 3 sizes.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### X06 [B] 隔離対象画像を選択する

**Original source index:** 元X05低画質一括選択も条件、1組1枚保持. Original v8 literal reference IDs: v8-X06-01, v8-X06-02, v8-X06-03, v8-X06-04, v8-X06-05, v8-X06-06, v8-X06-07. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-X06-01 / X06-original-01: S:/Projects/PixivVault/docs/design/DUPLICATE_DELETE_SPEC_2026-08-15.md L308-342 ; source SHA256=293f1cda4d03c4deceaee04ef531104b573ff4b20d5f78b4c7034ba715ae333e; extracted-lines SHA256=6aff7b9304463901cf7faca4fd8b760597c61bdc04c4bc34b607907045e1747b.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-X06-02 / X06-original-02: S:/Projects/PixivVault/docs/DEDUP_COMPARE_UNIFIED_LIST_SPEC_2026-08-29.md L44-59 ; source SHA256=837727b42bf337b84a8ee31c77753058cc00951027063ba2636023ad2375c3f5; extracted-lines SHA256=f6d6d6fec42daef8bef102fdccf6474709c777bc548cce59245541a488cd7b06.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-X06-03 / X06-original-03: S:/Projects/PixivVault/docs/DEDUP_COMPARE_UNIFIED_LIST_SPEC_2026-08-29.md L95-114 ; source SHA256=837727b42bf337b84a8ee31c77753058cc00951027063ba2636023ad2375c3f5; extracted-lines SHA256=30fe6862415f5266f9bced076eaf3ce816b69cda242677d89e9663be01f1fd7a.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-X06-04 / X06-original-04: S:/Projects/PixivVault/gui.py L5195-5219 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=14a5e562dccbaf3656fc97eb2c5b9154912138c3307e68bbb26023a9ea4178c6.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-X06-05 / X06-original-05: S:/Projects/PixivVault/gui.py L5263-5293 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=bead8939f3cee065bbb707117100768a79326d853866d5e98afeec6df93ca6d2.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-X06-06 / X06-original-06: S:/Projects/PixivVault/gui.py L5312-5334 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=2a98bf63261cce442b8e5a0165da6bdce1507c19f2e2b2c768f8e23dc8502f0c.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-X06-07 / X06-original-07: S:/Projects/PixivVault/gui.py L5801-5824 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=ed9616d727fb9dd35a44707691757969ccb25e47b0b5e32e6da56fd1d7425f75.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/ui/duplicate_view.py` SHA256=ec3893561b753201a53d2901091459ff6a9eaf320f337b08124b231ffa8b42de ; `src/pixivvault_nicegui/managed_quarantine.py` SHA256=b1db903a1de41d7a1cd46ba79e126d67a4ff3d4989dbdeb55b1c1f45fbb14567 ; `src/pixivvault_nicegui/duplicate_inspection.py` SHA256=1f5469053bae2f3474703727117ab463fe029abd6f98b7c5060635f27b15d7c8.

**Saved operation evidence:** artifacts/p3-318-x07-quarantine/edge-product-r3/result.json ; SHA256=a7cc0915387ca2fcd057489982c9cfe37f40540549f5f74e76c208e0283caae1; recorded_success=True; related source file(s) changed: src/pixivvault_nicegui/ui/duplicate_view.py,src/pixivvault_nicegui/managed_quarantine.py,src/pixivvault_nicegui/duplicate_inspection.py

**Current applicability / concrete missing:** Selection and one-survivor invariants need current candidate source after quarantine changes.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### X07 [C] 1組の選択画像をごみ箱へ移す

**Original source index:** 元重複整理契約§4/7. Original v8 literal reference IDs: v8-X07-01, v8-X07-02, v8-X07-03, v8-X07-04, v8-X07-05, v8-X07-06, v8-X07-07. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-X07-01 / X07-original-01: S:/Projects/PixivVault/docs/design/DUPLICATE_DELETE_SPEC_2026-08-15.md L17-43 ; source SHA256=293f1cda4d03c4deceaee04ef531104b573ff4b20d5f78b4c7034ba715ae333e; extracted-lines SHA256=0d44a3e566129f2b14086a7f3a1fb644f5d28aeb508007cead0da1c7775cceb1.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-X07-02 / X07-original-02: S:/Projects/PixivVault/docs/design/DUPLICATE_DELETE_SPEC_2026-08-15.md L49-73 ; source SHA256=293f1cda4d03c4deceaee04ef531104b573ff4b20d5f78b4c7034ba715ae333e; extracted-lines SHA256=6dc7ea6c731cb21e476b086e4c6732b8b54835af904668e43d16a478b2e43a0e.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-X07-03 / X07-original-03: S:/Projects/PixivVault/docs/design/DUPLICATE_DELETE_SPEC_2026-08-15.md L75-115 ; source SHA256=293f1cda4d03c4deceaee04ef531104b573ff4b20d5f78b4c7034ba715ae333e; extracted-lines SHA256=a0ae3995b8a9a7656dbaa7dea1c4f73e0907f5be043eefe45168aabd87e6eea4.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-X07-04 / X07-original-04: S:/Projects/PixivVault/docs/design/DUPLICATE_DELETE_SPEC_2026-08-15.md L308-342 ; source SHA256=293f1cda4d03c4deceaee04ef531104b573ff4b20d5f78b4c7034ba715ae333e; extracted-lines SHA256=6aff7b9304463901cf7faca4fd8b760597c61bdc04c4bc34b607907045e1747b.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-X07-05 / X07-original-05: S:/Projects/PixivVault/gui.py L5093-5118 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=8f48eff7ebe7c4e3526f0479adfa885fcc5523135769d8245dc0ce9d44b069d0.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-X07-06 / X07-original-06: S:/Projects/PixivVault/gui.py L5500-5538 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=e9550c95075646db68ca4e938859495645b59cccdf0cbb50e5a0fbb88f60df23.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-X07-07 / X07-original-07: S:/Projects/PixivVault/gui.py L5540-5593 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=735b6fa3d5e01fdc8181a37614a22d15baa9aa1f1cce8fe6827207a0c16624ae.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/ui/duplicate_view.py` SHA256=ec3893561b753201a53d2901091459ff6a9eaf320f337b08124b231ffa8b42de ; `src/pixivvault_nicegui/managed_quarantine.py` SHA256=b1db903a1de41d7a1cd46ba79e126d67a4ff3d4989dbdeb55b1c1f45fbb14567 ; `src/pixivvault_nicegui/duplicate_inspection.py` SHA256=1f5469053bae2f3474703727117ab463fe029abd6f98b7c5060635f27b15d7c8.

**Saved operation evidence:** artifacts/p3-318-x07-quarantine/edge-product-r3/result.json ; SHA256=a7cc0915387ca2fcd057489982c9cfe37f40540549f5f74e76c208e0283caae1; recorded_success=True; related source file(s) changed: src/pixivvault_nicegui/ui/duplicate_view.py,src/pixivvault_nicegui/managed_quarantine.py,src/pixivvault_nicegui/duplicate_inspection.py

**Current applicability / concrete missing:** Original move-to-trash is not implemented as a move: current UI explicitly copies while retaining source receipt; no capacity release.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### X08 [C] 複数組を一括でごみ箱へ移す

**Original source index:** 複数組1確認・実移動件数一致. Original v8 literal reference IDs: v8-X08-01, v8-X08-02, v8-X08-03, v8-X08-04, v8-X08-05, v8-X08-06. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-X08-01 / X08-original-01: S:/Projects/PixivVault/docs/DEDUP_COMPARE_UNIFIED_LIST_SPEC_2026-08-29.md L104-131 ; source SHA256=837727b42bf337b84a8ee31c77753058cc00951027063ba2636023ad2375c3f5; extracted-lines SHA256=684825442b63359d33523c4c6acf7f505a71482f52ad5c6309cd5d9d6b3e5669.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-X08-02 / X08-original-02: S:/Projects/PixivVault/docs/DEDUP_COMPARE_UNIFIED_LIST_SPEC_2026-08-29.md L152-169 ; source SHA256=837727b42bf337b84a8ee31c77753058cc00951027063ba2636023ad2375c3f5; extracted-lines SHA256=d8ca73d408a354cd1ef7c137df805088533de3c6930b8bdf507b7d9f87b9902e.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-X08-03 / X08-original-03: S:/Projects/PixivVault/gui.py L431-459 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=f6dffe831837b7a5b30b1dd3bb92eddf287f9d6925e456eda9d6e4ea467573fa.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-X08-04 / X08-original-04: S:/Projects/PixivVault/gui.py L5500-5593 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=9d75d9ab07a9c3efe0c3312517ee707747fa697cf345ccd9baeeb618d73ad3ed.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-X08-05 / X08-original-05: S:/Projects/PixivVault/gui.py L5826-5838 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=f3badefbaa0afddfe79440cb6879b356bce647a6b97f342f8c79cd9aa966dae0.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-X08-06 / X08-original-06: S:/Projects/PixivVault/docs/design/DUPLICATE_DELETE_SPEC_2026-08-15.md L49-115 ; source SHA256=293f1cda4d03c4deceaee04ef531104b573ff4b20d5f78b4c7034ba715ae333e; extracted-lines SHA256=bb7bbce4cc80232fba494969679f55c50030d94b29c7769368a101e5cee4edbc.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/ui/duplicate_view.py` SHA256=ec3893561b753201a53d2901091459ff6a9eaf320f337b08124b231ffa8b42de ; `src/pixivvault_nicegui/managed_quarantine.py` SHA256=b1db903a1de41d7a1cd46ba79e126d67a4ff3d4989dbdeb55b1c1f45fbb14567 ; `src/pixivvault_nicegui/duplicate_inspection.py` SHA256=1f5469053bae2f3474703727117ab463fe029abd6f98b7c5060635f27b15d7c8.

**Saved operation evidence:** artifacts/p3-318-x07-quarantine/edge-product-r3/result.json ; SHA256=a7cc0915387ca2fcd057489982c9cfe37f40540549f5f74e76c208e0283caae1; recorded_success=True; related source file(s) changed: src/pixivvault_nicegui/ui/duplicate_view.py,src/pixivvault_nicegui/managed_quarantine.py,src/pixivvault_nicegui/duplicate_inspection.py

**Current applicability / concrete missing:** Original multigroup move-to-trash is replaced by quarantine copies; original move/count semantics still absent.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### X09 [B] ごみ箱の保存先を開く

**Original source index:** 原下部バーのごみ箱導線. Original v8 literal reference IDs: v8-X09-01, v8-X09-02, v8-X09-03, v8-X09-04, v8-X09-05, v8-X09-06. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-X09-01 / X09-original-01: S:/Projects/PixivVault/docs/DEDUP_COMPARE_UNIFIED_LIST_SPEC_2026-08-29.md L104-114 ; source SHA256=837727b42bf337b84a8ee31c77753058cc00951027063ba2636023ad2375c3f5; extracted-lines SHA256=9b98a222307ca09f2a1153413d819c94284a5323a9148890b361fb16ba62e78f.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-X09-02 / X09-original-02: S:/Projects/PixivVault/docs/design/DUPLICATE_DELETE_SPEC_2026-08-15.md L352-378 ; source SHA256=293f1cda4d03c4deceaee04ef531104b573ff4b20d5f78b4c7034ba715ae333e; extracted-lines SHA256=fb0863726b6a5e9d59a5a66d337ceb32e17fd6f6976a41be7d5cd9b55dc87d7d.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-X09-03 / X09-original-03: S:/Projects/PixivVault/gui.py L5604-5625 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=0d6e684e7fd61b80a428836326a9d6b1f6ebeb7fbf95691daa4b956b4c6ea5ad.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-X09-04 / X09-original-04: S:/Projects/PixivVault/gui.py L5627-5670 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=c51b87d8bfc20f12937dfb2fb694f5795f109a5b13e2fc5175d9b405df72830a.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-X09-05 / X09-original-05: S:/Projects/PixivVault/gui.py L5702-5702 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=762ad8237dec08dc47d598c36f89c26849519a8da3b542804a6c26ecdf0426e4.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-X09-06 / X09-original-06: S:/Projects/PixivVault/gui.py L5083-5091 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=9a3f7454e6c65dfe7ee44d5a47fd5e752f0e942549079dfc10558c5154ff0f4b.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/ui/duplicate_view.py` SHA256=ec3893561b753201a53d2901091459ff6a9eaf320f337b08124b231ffa8b42de ; `src/pixivvault_nicegui/managed_quarantine.py` SHA256=b1db903a1de41d7a1cd46ba79e126d67a4ff3d4989dbdeb55b1c1f45fbb14567 ; `src/pixivvault_nicegui/duplicate_inspection.py` SHA256=1f5469053bae2f3474703727117ab463fe029abd6f98b7c5060635f27b15d7c8.

**Saved operation evidence:** artifacts/p3-318-x07-quarantine/x09-v06-edge-r1/result.json ; SHA256=a0575f1be8df0fdb833d9dd478306db6a1429fa8a5d8676043653e8ef57c85ea; recorded_success=True; related source file(s) changed: src/pixivvault_nicegui/managed_quarantine.py

**Current applicability / concrete missing:** Old OS-open evidence predates changed duplicate view/backend; verify actual location opens with current owner.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### H01 [B] 直近バックアップ作者一覧を閲覧する

**Original source index:** UG 90–93、last_backup順・最大60・代表画像. Original v8 literal reference IDs: v8-H01-01, v8-H01-02, v8-H01-03, v8-H01-04. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-H01-01 / H01-original-01: S:/Projects/PixivVault/docs/UI_GUIDE.md L90-93 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=25e0a9fa488f1e39c3042c4b10bd5d303d5d808066dd292685d530f94a5f9fd7.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-H01-02 / H01-original-02: S:/Projects/PixivVault/gui.py L4497-4532 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=7cf9d2052ff86b76672a38260b6d4d63b3d9658d23b0adb920563ec6959aed9d.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-H01-03 / H01-original-03: S:/Projects/PixivVault/gui.py L4578-4588 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=dedb8b43fe8492a3642e0bebcb0cdb456f174ba3d088ce32f5599ad4c1a02f80.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-H01-04 / H01-original-04: S:/Projects/PixivVault/gui.py L4590-4646 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=d8dfea7a312cd2fca2df2a0ba9dff15acbaf38a513c795daf04bff2c45b49b6b.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/ui/viewer_backup_view.py` SHA256=2d4447ea5d41c48aa754ed51f33a6bb378bfd98c98d74627325d5233916266d1 ; `src/pixivvault_nicegui/legacy_backup_history.py` SHA256=b4b5dd69ac9f03cf63764afec5978dd1fdbaa54ce8036498fb28d1c5028787c6.

**Saved operation evidence:** No operation-level result.json bound in examined sources.

**Supplementary recorded evidence (bounded; not a substitute for original operation):** artifacts/p3-h01-legacy-backup-20261028/worker1-r8/impact.xml ; SHA256=f66a5705472338f715fab854d8c23640cfb4295b1554059f3f48a5942372c581.

**Current applicability / concrete missing:** Legacy backup history view lacks current first/restart GUI proof for ordering, max 60 and representative image.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### S01 [B] Cookieファイルをインポートする

**Original source index:** UG 99、認証状態反映（旧S02も条件）. Original v8 literal reference IDs: v8-S01-01, v8-S01-02. Reference status: verbatim-v7-reference-set-transcribed.

**Frozen original source references (v8 literal transcription if available):**
- v8-S01-01 / UG:20-20: S:/Projects/PixivVault/docs/UI_GUIDE.md L20-20 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=d567f716b21f372957a26c0b794806a52334388645655545acc4979177a337bc.
  - Verbatim expectation: - ログイン状態テキスト（有効/期限間近/期限切れ/未認証を色分け表示、Cookie状態検証結果を反映）
- v8-S01-02 / UG:99-99: S:/Projects/PixivVault/docs/UI_GUIDE.md L99-99 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=9488d1d02b63be97ccc52b5f3d78f6496f82611c0cc847f97b4b42f2083e9c2b.
  - Verbatim expectation: 1. **Cookieのインポート**: `cookies.txt`をネイティブファイル選択で読み込み

**Original literal `expected_full`:**

```text
- ログイン状態テキスト（有効/期限間近/期限切れ/未認証を色分け表示、Cookie状態検証結果を反映）\n1. **Cookieのインポート**: `cookies.txt`をネイティブファイル選択で読み込み
```

**Current source:** `src/pixivvault_nicegui/ui/preferences_view.py` SHA256=6e7d17894d6805dbce0476b07b10e7729be32681983b3079809c78f519b6ee6d ; `src/pixivvault_nicegui/preferences.py` SHA256=b8b3b585137b2d44d93db0250dac472dd377ed5893a7953352ccd5bedd11e2cb ; `src/pixivvault_nicegui/services.py` SHA256=93b5bb7708547fc129e49903e91a4af256224aacf69c2138b1cd5ab2384753c0.

**Saved operation evidence:** No operation-level result.json bound in examined sources.

**Current applicability / concrete missing:** Native cookie selector and masked auth state exist; real Cookie is outside this audit, so current synthetic state/validity needs GUI receipt.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### S03 [B] フォロー中リストを同期する

**Original source index:** UG 100、Pixiv→DB保存. Original v8 literal reference IDs: v8-S03-01. Reference status: verbatim-v7-reference-set-transcribed.

**Frozen original source references (v8 literal transcription if available):**
- v8-S03-01 / UG:100-100: S:/Projects/PixivVault/docs/UI_GUIDE.md L100-100 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=e60cc848cfd14135f2eb014a4f7e5e576fea3edb966562229e703a461f8bf8f1.
  - Verbatim expectation: 2. **フォロー中リストの同期**: Pixivからフォロー中作者情報を取得しDBへ保存

**Original literal `expected_full`:**

```text
2. **フォロー中リストの同期**: Pixivからフォロー中作者情報を取得しDBへ保存
```

**Current source:** `src/pixivvault_nicegui/legacy_author_sync.py` SHA256=91529efdac2b60e6a2784e530006d48b238ebe30bd62e7274183ebe1d88cfad5 ; `src/pixivvault_nicegui/ui/following_sync_view.py` SHA256=22e6704a11cb57cf4d2a53bbf69853b860a302a828adebf0acc8e97beb2fe4a4.

**Saved operation evidence:** No operation-level result.json bound in examined sources.

**Supplementary recorded evidence (bounded; not a substitute for original operation):** artifacts/p3-318-s03-following/green-r2/result.xml ; SHA256=14582e60de0f0ef64c2b9c0afb061a35a08d607af0d0c3068bf9b60023faf6c2 | artifacts/p3-318-s03-following/ui-green-r1/result.xml ; SHA256=8486e036e6d990867afec97572d3276ca08faff45fd5c0b305871a0fdee71d4b.

**Current applicability / concrete missing:** Following-sync source exists; Pixiv GET and dedicated managed DB persistence need authorized connected acceptance, no invented GET.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### S04 [B] 保存フォルダを設定変更する

**Original source index:** UG 101、選択・再起動保持. Original v8 literal reference IDs: v8-S04-01. Reference status: verbatim-v7-reference-set-transcribed.

**Frozen original source references (v8 literal transcription if available):**
- v8-S04-01 / UG:101-101: S:/Projects/PixivVault/docs/UI_GUIDE.md L101-101 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=4e0891b1e76996a894bfdf0050fbd14d9f99171cc310c4f0fea717403f412f78.
  - Verbatim expectation: 3. **保存フォルダーの設定**: ダウンロード先フォルダ変更（再起動後も保持）

**Original literal `expected_full`:**

```text
3. **保存フォルダーの設定**: ダウンロード先フォルダ変更（再起動後も保持）
```

**Current source:** `src/pixivvault_nicegui/ui/preferences_view.py` SHA256=6e7d17894d6805dbce0476b07b10e7729be32681983b3079809c78f519b6ee6d ; `src/pixivvault_nicegui/preferences.py` SHA256=b8b3b585137b2d44d93db0250dac472dd377ed5893a7953352ccd5bedd11e2cb ; `src/pixivvault_nicegui/services.py` SHA256=93b5bb7708547fc129e49903e91a4af256224aacf69c2138b1cd5ab2384753c0.

**Saved operation evidence:** artifacts/p3-339-native/profile-packaged-r2/result.json ; SHA256=d37b03441e8d0e8f63b1acc67a2c8809962743ac3c8f5d54051c8a12dd6d6d0e; recorded_success=passed; no per-source before binding in result

**Current applicability / concrete missing:** Packaged profile persist/restart observed; must bind current UI/source and saved-folder selection path.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### S05 [B] 起動時の自動チェックをON/OFFする

**Original source index:** UG 103、scheduler CAS保存・再起動. Original v8 literal reference IDs: v8-S05-01. Reference status: verbatim-v7-reference-set-transcribed.

**Frozen original source references (v8 literal transcription if available):**
- v8-S05-01 / UG:103-103: S:/Projects/PixivVault/docs/UI_GUIDE.md L103-103 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=77c787d806ca5e9fc136e152643332790297085b919e3cc72432739e84db7a73.
  - Verbatim expectation:    - 起動時にもお気に入り新着チェックを実行するか

**Original literal `expected_full`:**

```text
   - 起動時にもお気に入り新着チェックを実行するか
```

**Current source:** `src/pixivvault_nicegui/managed_scheduler.py` SHA256=75ba2af038186a1162e27c9887f323304e0e68917dd9d8130d8b146a1a6bae9a ; `src/pixivvault_nicegui/ui/scheduler_view.py` SHA256=bfb7178eb06d155c91241a3843fa911ea70194b682470e5d66dbe182a5f60e73.

**Saved operation evidence:** artifacts/p3-318-scheduler-70-authors/edge-r1/result.json ; SHA256=e7e19dcab3013122ff3cde024a59f5667c1ef754ba3a47abf4b44bfe78d4b1d4; recorded_success=True; related recorded files same where compared

**Current applicability / concrete missing:** Scheduler saved startup toggle and reloaded effective setting need current settings UI operation.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### S06 [B] 自動チェック間隔を変更する

**Original source index:** UG 104、無効/6/12/24/48h. Original v8 literal reference IDs: v8-S06-01. Reference status: verbatim-v7-reference-set-transcribed.

**Frozen original source references (v8 literal transcription if available):**
- v8-S06-01 / UG:104-104: S:/Projects/PixivVault/docs/UI_GUIDE.md L104-104 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=807f368bc14b6503a8304e0788e0af5c42425d05b00b29ccc7f1b360bddb7a32.
  - Verbatim expectation:    - ★お気に入り自動チェック間隔（6h/12h/24h(既定)/48h/しない）

**Original literal `expected_full`:**

```text
   - ★お気に入り自動チェック間隔（6h/12h/24h(既定)/48h/しない）
```

**Current source:** `src/pixivvault_nicegui/managed_scheduler.py` SHA256=75ba2af038186a1162e27c9887f323304e0e68917dd9d8130d8b146a1a6bae9a ; `src/pixivvault_nicegui/ui/scheduler_view.py` SHA256=bfb7178eb06d155c91241a3843fa911ea70194b682470e5d66dbe182a5f60e73.

**Saved operation evidence:** artifacts/p3-318-scheduler-70-authors/edge-r1/result.json ; SHA256=e7e19dcab3013122ff3cde024a59f5667c1ef754ba3a47abf4b44bfe78d4b1d4; recorded_success=True; related recorded files same where compared

**Current applicability / concrete missing:** Five interval choices, disable state and restart scheduler application need current UI operation.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### S07 [B] 小説の既定保存形式を変更する

**Original source index:** UG 105、TXT/EPUB/BOTH・次回適用. Original v8 literal reference IDs: v8-S07-01. Reference status: verbatim-v7-reference-set-transcribed.

**Frozen original source references (v8 literal transcription if available):**
- v8-S07-01 / UG:105-105: S:/Projects/PixivVault/docs/UI_GUIDE.md L105-105 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=4614411518eab9db469dc2eb8ebf9267e6c355618a474cee7f5b7dcd012bc7d1.
  - Verbatim expectation:    - 小説の保存形式（EPUBのみ(既定)/TXTのみ/両方）

**Original literal `expected_full`:**

```text
   - 小説の保存形式（EPUBのみ(既定)/TXTのみ/両方）
```

**Current source:** `src/pixivvault_nicegui/ui/preferences_view.py` SHA256=6e7d17894d6805dbce0476b07b10e7729be32681983b3079809c78f519b6ee6d ; `src/pixivvault_nicegui/preferences.py` SHA256=b8b3b585137b2d44d93db0250dac472dd377ed5893a7953352ccd5bedd11e2cb ; `src/pixivvault_nicegui/services.py` SHA256=93b5bb7708547fc129e49903e91a4af256224aacf69c2138b1cd5ab2384753c0.

**Saved operation evidence:** No operation-level result.json bound in examined sources.

**Current applicability / concrete missing:** EPUB/TXT/BOTH saved setting must be joined to next novel artifact, restart and override.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### S08 [B] うごイラ既定形式を変更する

**Original source index:** UG 106、frames/GIF/MP4・次回適用. Original v8 literal reference IDs: v8-S08-01. Reference status: verbatim-v7-reference-set-transcribed.

**Frozen original source references (v8 literal transcription if available):**
- v8-S08-01 / UG:106-106: S:/Projects/PixivVault/docs/UI_GUIDE.md L106-106 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=551a8633a6b8615db653ca9d2633bee831a2a213cb4b139b9a42748a3ca423d4.
  - Verbatim expectation:    - うごイラの保存形式（GIF(既定)/MP4/すべてフォルダ格納）

**Original literal `expected_full`:**

```text
   - うごイラの保存形式（GIF(既定)/MP4/すべてフォルダ格納）
```

**Current source:** `src/pixivvault_nicegui/ui/preferences_view.py` SHA256=6e7d17894d6805dbce0476b07b10e7729be32681983b3079809c78f519b6ee6d ; `src/pixivvault_nicegui/preferences.py` SHA256=b8b3b585137b2d44d93db0250dac472dd377ed5893a7953352ccd5bedd11e2cb ; `src/pixivvault_nicegui/services.py` SHA256=93b5bb7708547fc129e49903e91a4af256224aacf69c2138b1cd5ab2384753c0.

**Saved operation evidence:** No operation-level result.json bound in examined sources.

**Current applicability / concrete missing:** Frames/GIF/MP4 saved setting must be joined to next ugoira artifact and restart.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### S09 [B] 新着通知をON/OFFする

**Original source index:** UG 107、Windows実通知. Original v8 literal reference IDs: v8-S09-01. Reference status: verbatim-v7-reference-set-transcribed.

**Frozen original source references (v8 literal transcription if available):**
- v8-S09-01 / UG:107-107: S:/Projects/PixivVault/docs/UI_GUIDE.md L107-107 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=22795a594bea8a8a5f40bdaba2c16d192e780279280b3ac694d9c423a38c9f67.
  - Verbatim expectation:    - 新着通知ON/OFF（既定ON）

**Original literal `expected_full`:**

```text
   - 新着通知ON/OFF（既定ON）
```

**Current source:** `src/pixivvault_nicegui/notifications.py` SHA256=46a0d4ed97fa9957744a3c1176ffa5696f5ee9d777ea146017b76a00ad9c9768 ; `src/pixivvault_nicegui/ui/preferences_view.py` SHA256=6e7d17894d6805dbce0476b07b10e7729be32681983b3079809c78f519b6ee6d.

**Saved operation evidence:** No operation-level result.json bound in examined sources.

**Current applicability / concrete missing:** Windows actual toast on/off cannot be concluded from synthetic settings alone; verify with notification backend.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### S10 [B] ブクマのdirect/link保存配置を変更する

**Original source index:** UG 108、実ファイル配置も確認. Original v8 literal reference IDs: v8-S10-01. Reference status: verbatim-v7-reference-set-transcribed.

**Frozen original source references (v8 literal transcription if available):**
- v8-S10-01 / UG:108-108: S:/Projects/PixivVault/docs/UI_GUIDE.md L108-108 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=51bb4a339f876330b9dfca47c8f43dbacb9aace92f2d4107014628277dabd181.
  - Verbatim expectation:    - ブックマーク保存モード（フラット配置(既定) / 階層配置）※詳細は[FEATURES.md](../FEATURES.md)参照

**Original literal `expected_full`:**

```text
   - ブックマーク保存モード（フラット配置(既定) / 階層配置）※詳細は[FEATURES.md](../FEATURES.md)参照
```

**Current source:** `src/pixivvault_nicegui/save_exports.py` SHA256=b98fe56fbf6681aa079e63b0a1a4470d066866c149e8e2a16635bd9bf8f686bd ; `src/pixivvault_nicegui/ui/preferences_view.py` SHA256=6e7d17894d6805dbce0476b07b10e7729be32681983b3079809c78f519b6ee6d.

**Saved operation evidence:** artifacts/p3-318-native-save-layout/edge-normal-profile-r3/result.json ; SHA256=c9f5c0b2a67568557f339ca0737521ce0326ade639e24274a268b773f6d64e37; recorded_success=True; related source file(s) changed: src/pixivvault_nicegui/ui/preferences_view.py

**Current applicability / concrete missing:** Normal-profile 1280/1920 direct/link layout run succeeded; changed preferences/bootstrap connections require bounded rebind.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### S11 [B] ダウンロード待機間隔を変更する

**Original source index:** UG 110、全readerへの適用. Original v8 literal reference IDs: v8-S11-01. Reference status: verbatim-v7-reference-set-transcribed.

**Frozen original source references (v8 literal transcription if available):**
- v8-S11-01 / UG:110-110: S:/Projects/PixivVault/docs/UI_GUIDE.md L110-110 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=a3020044c830ee2441bcd370b400e73b0daea7d5b336cdf14a60fd420fb99448.
  - Verbatim expectation:    - ダウンロード待機時間（0.3〜1.5秒、既定0.6秒。実測データに基づきiOS版と値を統一）

**Original literal `expected_full`:**

```text
   - ダウンロード待機時間（0.3〜1.5秒、既定0.6秒。実測データに基づきiOS版と値を統一）
```

**Current source:** `src/pixivvault_nicegui/ui/preferences_view.py` SHA256=6e7d17894d6805dbce0476b07b10e7729be32681983b3079809c78f519b6ee6d ; `src/pixivvault_nicegui/preferences.py` SHA256=b8b3b585137b2d44d93db0250dac472dd377ed5893a7953352ccd5bedd11e2cb ; `src/pixivvault_nicegui/services.py` SHA256=93b5bb7708547fc129e49903e91a4af256224aacf69c2138b1cd5ab2384753c0.

**Saved operation evidence:** No operation-level result.json bound in examined sources.

**Current applicability / concrete missing:** Download interval chooser connects conditionally; verify admission timing for each active reader and restart.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### S12 [B] HTTP retry回数を変更する

**Original source index:** UG 111、失敗・停止・attemptとの整合. Original v8 literal reference IDs: v8-S12-01. Reference status: verbatim-v7-reference-set-transcribed.

**Frozen original source references (v8 literal transcription if available):**
- v8-S12-01 / UG:111-111: S:/Projects/PixivVault/docs/UI_GUIDE.md L111-111 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=bbf40aa00afef05dcc53a50626d1e470e559872d28adaa1774cdc6ba6e7086f1.
  - Verbatim expectation:    - 通信エラー時リトライ上限回数（1/3(既定)/5/10回）

**Original literal `expected_full`:**

```text
   - 通信エラー時リトライ上限回数（1/3(既定)/5/10回）
```

**Current source:** `src/pixivvault_nicegui/ui/preferences_view.py` SHA256=6e7d17894d6805dbce0476b07b10e7729be32681983b3079809c78f519b6ee6d ; `src/pixivvault_nicegui/preferences.py` SHA256=b8b3b585137b2d44d93db0250dac472dd377ed5893a7953352ccd5bedd11e2cb ; `src/pixivvault_nicegui/services.py` SHA256=93b5bb7708547fc129e49903e91a4af256224aacf69c2138b1cd5ab2384753c0.

**Saved operation evidence:** No operation-level result.json bound in examined sources.

**Current applicability / concrete missing:** Retry policy chooser connects conditionally; verify attempts, stop behavior and terminal accounting per reader.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### S13 [B] 429待機時間を変更する

**Original source index:** UG 112、rate制御との一致. Original v8 literal reference IDs: v8-S13-01. Reference status: verbatim-v7-reference-set-transcribed.

**Frozen original source references (v8 literal transcription if available):**
- v8-S13-01 / UG:112-112: S:/Projects/PixivVault/docs/UI_GUIDE.md L112-112 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=b1a86e8a388c744eee9b39392868ea4624febabe55148dad7e7b8afa7a7c088a.
  - Verbatim expectation:    - 429レート制限検知時待機時間（3/5(既定)/10/30秒）

**Original literal `expected_full`:**

```text
   - 429レート制限検知時待機時間（3/5(既定)/10/30秒）
```

**Current source:** `src/pixivvault_nicegui/ui/preferences_view.py` SHA256=6e7d17894d6805dbce0476b07b10e7729be32681983b3079809c78f519b6ee6d ; `src/pixivvault_nicegui/preferences.py` SHA256=b8b3b585137b2d44d93db0250dac472dd377ed5893a7953352ccd5bedd11e2cb ; `src/pixivvault_nicegui/services.py` SHA256=93b5bb7708547fc129e49903e91a4af256224aacf69c2138b1cd5ab2384753c0.

**Saved operation evidence:** No operation-level result.json bound in examined sources.

**Current applicability / concrete missing:** 429 wait chooser connects conditionally; verify actual rate-control delay and failure boundary per reader.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### S14 [B] 全作者自動ZIPをON/OFFする

**Original source index:** UG 113、作者override・既存ZIP更新. Original v8 literal reference IDs: v8-S14-01. Reference status: verbatim-v7-reference-set-transcribed.

**Frozen original source references (v8 literal transcription if available):**
- v8-S14-01 / UG:113-113: S:/Projects/PixivVault/docs/UI_GUIDE.md L113-113 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=f51ae427758ac3567ea169ff0b1eed1e8dbbcd9cae9e5ae0177342afc5856975.
  - Verbatim expectation:    - すべての作者に対しダウンロード完了後Zip化するか

**Original literal `expected_full`:**

```text
   - すべての作者に対しダウンロード完了後Zip化するか
```

**Current source:** `src/pixivvault_nicegui/ui/preferences_view.py` SHA256=6e7d17894d6805dbce0476b07b10e7729be32681983b3079809c78f519b6ee6d ; `src/pixivvault_nicegui/preferences.py` SHA256=b8b3b585137b2d44d93db0250dac472dd377ed5893a7953352ccd5bedd11e2cb ; `src/pixivvault_nicegui/services.py` SHA256=93b5bb7708547fc129e49903e91a4af256224aacf69c2138b1cd5ab2384753c0.

**Saved operation evidence:** No operation-level result.json bound in examined sources.

**Current applicability / concrete missing:** ZIP-all consumer exists; demonstrate author override priority and existing ZIP update after a completed download.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### S15 [B] トレイ格納設定をON/OFFする

**Original source index:** UG 114、Windows格納結果. Original v8 literal reference IDs: v8-S15-01, v8-S15-02. Reference status: verbatim-v7-reference-set-transcribed.

**Frozen original source references (v8 literal transcription if available):**
- v8-S15-01 / UG:14-14: S:/Projects/PixivVault/docs/UI_GUIDE.md L14-14 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=c87a4c180a898635aaae6073e4ba8fae269e5917c1e3e4032a33f6ccd9b13baa.
  - Verbatim expectation: - 最小化時は設定「最小化時にタスクトレイに格納する」（既定ON）に従いタスクバーからも隠れ、トレイ常駐化する
- v8-S15-02 / UG:114-114: S:/Projects/PixivVault/docs/UI_GUIDE.md L114-114 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=bbb00f6424c2afdaa4e1cf37d85c92ebb5b97d25ba46373a113751af68eec489.
  - Verbatim expectation:    - 最小化時にタスクトレイへ格納するか（既定ON）

**Original literal `expected_full`:**

```text
- 最小化時は設定「最小化時にタスクトレイに格納する」（既定ON）に従いタスクバーからも隠れ、トレイ常駐化する\n   - 最小化時にタスクトレイへ格納するか（既定ON）
```

**Current source:** `src/pixivvault_nicegui/native_tray_exit.py` SHA256=76619e17c47491c1cdc1dda2d2ee56e1720cb800ce3776630b8d1ca57960c04e ; `src/pixivvault_nicegui/ui/preferences_view.py` SHA256=6e7d17894d6805dbce0476b07b10e7729be32681983b3079809c78f519b6ee6d.

**Saved operation evidence:** artifacts/p3-339-native/tray-packaged-r3/result.json ; SHA256=bc14d1783b54e27139f51ae14ac4dc8dc2b2cf47b0b65208a52e2ad7982d0246; recorded_success=True; no per-source before binding in result

**Current applicability / concrete missing:** Packaged tray setting roundtrip was observed; current native owner/source differs and needs bounded rebinding.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### S16 [B] 学習cacheのMB/枚数モードを変更する

**Original source index:** cache棚卸し#1. Original v8 literal reference IDs: v8-S16-01, v8-S16-02, v8-S16-03. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-S16-01 / S16-original-01: S:/Projects/PixivVault/docs/CACHE_SETTINGS_INVENTORY_2026-08-31.md L19-24 ; source SHA256=76ea4f893a309e68b8748555a6f6c03adb1fe1056c8ece8a997a15b421a29414; extracted-lines SHA256=32ba5826b3ad6006ba1b99c7b53ef5207d50ade931ccfe04378f097a85edcd57.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-S16-02 / S16-original-02: S:/Projects/PixivVault/docs/CACHE_SETTINGS_INVENTORY_2026-08-31.md L27-35 ; source SHA256=76ea4f893a309e68b8748555a6f6c03adb1fe1056c8ece8a997a15b421a29414; extracted-lines SHA256=89489148179a32f2da8a542a2b14f4d371ee7e04e129fa4649ea60c3f931b271.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-S16-03 / S16-original-03: S:/Projects/PixivVault/gui.py L1445-1466 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=cf1779968fe19ae054d9a8ca26f33c1b2a34946da4c3c6c39ed38295c8ebfb49.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/learning_prefetch.py` SHA256=44ca8b313c5bcffa40bce08bffc76538a8f4cc4fc69786e491c492fc8430b8a7 ; `src/pixivvault_nicegui/services.py` SHA256=93b5bb7708547fc129e49903e91a4af256224aacf69c2138b1cd5ab2384753c0.

**Saved operation evidence:** artifacts/p3-318-s16-cache-units/edge-r1/result.json ; SHA256=61dd182803008c219a36904d02c11b05366ef3306a20f5b7f3597a69bf385d89; recorded_success=True; related source file(s) changed: src/pixivvault_nicegui/services.py

**Current applicability / concrete missing:** Unit setting Edge passed in prior source; current discovery cache consumer and cross-mode restart must be joined.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### S17 [B] 学習cacheのMB上限を変更する

**Original source index:** cache棚卸し#2、FIFO. Original v8 literal reference IDs: v8-S17-01, v8-S17-02, v8-S17-03. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-S17-01 / S17-original-01: S:/Projects/PixivVault/docs/CACHE_SETTINGS_INVENTORY_2026-08-31.md L21-24 ; source SHA256=76ea4f893a309e68b8748555a6f6c03adb1fe1056c8ece8a997a15b421a29414; extracted-lines SHA256=8de00df17b5842cebde93f6b09eac3a8376b8966ded447efab34901ac19bf6c1.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-S17-02 / S17-original-02: S:/Projects/PixivVault/docs/CACHE_SETTINGS_INVENTORY_2026-08-31.md L29-32 ; source SHA256=76ea4f893a309e68b8748555a6f6c03adb1fe1056c8ece8a997a15b421a29414; extracted-lines SHA256=3478d1eb225971f26c663dd19b9c9d1534b74988b3517dd75aa3b9112cc0df8e.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-S17-03 / S17-original-03: S:/Projects/PixivVault/gui.py L1468-1484 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=df49da008a906990d5f154296b556d1864cea0b77ca3f6d1bd9937741b98b69f.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/learning_prefetch.py` SHA256=44ca8b313c5bcffa40bce08bffc76538a8f4cc4fc69786e491c492fc8430b8a7 ; `src/pixivvault_nicegui/services.py` SHA256=93b5bb7708547fc129e49903e91a4af256224aacf69c2138b1cd5ab2384753c0.

**Saved operation evidence:** artifacts/p3-318-s16-cache-units/edge-r1/result.json ; SHA256=61dd182803008c219a36904d02c11b05366ef3306a20f5b7f3597a69bf385d89; recorded_success=True; related source file(s) changed: src/pixivvault_nicegui/services.py

**Current applicability / concrete missing:** MB cache ceiling and FIFO eviction at boundary need integrated user-flow evidence.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### S18 [B] 学習cacheの枚数上限を変更する

**Original source index:** cache棚卸し#3、FIFO. Original v8 literal reference IDs: v8-S18-01, v8-S18-02, v8-S18-03, v8-S18-04. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-S18-01 / S18-original-01: S:/Projects/PixivVault/docs/CACHE_SETTINGS_INVENTORY_2026-08-31.md L21-23 ; source SHA256=76ea4f893a309e68b8748555a6f6c03adb1fe1056c8ece8a997a15b421a29414; extracted-lines SHA256=7c22988968df785da7dad1e601ca5b97ef6a47345f6dbf113c18c09746a30508.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-S18-02 / S18-original-02: S:/Projects/PixivVault/docs/CACHE_SETTINGS_INVENTORY_2026-08-31.md L29-32 ; source SHA256=76ea4f893a309e68b8748555a6f6c03adb1fe1056c8ece8a997a15b421a29414; extracted-lines SHA256=3478d1eb225971f26c663dd19b9c9d1534b74988b3517dd75aa3b9112cc0df8e.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-S18-03 / S18-original-03: S:/Projects/PixivVault/gui.py L1531-1548 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=c0e3c4ef116e1d113d773fd8ac2d59ee3945b83e05497eccfb047caf1bd6001e.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-S18-04 / S18-original-04: S:/Projects/PixivVault/gui.py L1449-1455 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=562c6aff36870b01b8774d8c8d2e6fd06335d74489977f52a09f56972c7d3cbf.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/learning_prefetch.py` SHA256=44ca8b313c5bcffa40bce08bffc76538a8f4cc4fc69786e491c492fc8430b8a7 ; `src/pixivvault_nicegui/services.py` SHA256=93b5bb7708547fc129e49903e91a4af256224aacf69c2138b1cd5ab2384753c0.

**Saved operation evidence:** artifacts/p3-318-s16-cache-units/edge-r1/result.json ; SHA256=61dd182803008c219a36904d02c11b05366ef3306a20f5b7f3597a69bf385d89; recorded_success=True; related source file(s) changed: src/pixivvault_nicegui/services.py

**Current applicability / concrete missing:** Count cache ceiling and FIFO eviction at boundary need integrated user-flow evidence.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### S19 [B] 全タブ共有cache容量を変更する

**Original source index:** cache棚卸し#4、LRU. Original v8 literal reference IDs: v8-S19-01, v8-S19-02, v8-S19-03, v8-S19-04. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-S19-01 / S19-original-01: S:/Projects/PixivVault/docs/CACHE_SETTINGS_INVENTORY_2026-08-31.md L19-24 ; source SHA256=76ea4f893a309e68b8748555a6f6c03adb1fe1056c8ece8a997a15b421a29414; extracted-lines SHA256=32ba5826b3ad6006ba1b99c7b53ef5207d50ade931ccfe04378f097a85edcd57.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-S19-02 / S19-original-02: S:/Projects/PixivVault/docs/CACHE_SETTINGS_INVENTORY_2026-08-31.md L33-35 ; source SHA256=76ea4f893a309e68b8748555a6f6c03adb1fe1056c8ece8a997a15b421a29414; extracted-lines SHA256=df92c750e03a4dd8d95be42401d1a27de29ed3e9eb65aa964ce4f660ae7bcebd.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-S19-03 / S19-original-03: S:/Projects/PixivVault/gui.py L1486-1507 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=a5c32746946e11b41f074a683d2949ddbe7ec826e22659d31202ec675d7683da.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-S19-04 / S19-original-04: S:/Projects/PixivVault/gui.py L1509-1529 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=fad08d476047515f1412fe36bc6791081bc5cb23e9331da5fc9fcf1db913c472.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/shared_image_cache.py` SHA256=5116973c14ea73d2de26af104f93bbee40ac28c0d63e39770f5f61e2b1b938d2 ; `src/pixivvault_nicegui/services.py` SHA256=93b5bb7708547fc129e49903e91a4af256224aacf69c2138b1cd5ab2384753c0.

**Saved operation evidence:** No operation-level result.json bound in examined sources.

**Current applicability / concrete missing:** Shared cache MB selector and LRU runtime effect across tabs need integrated evidence.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### S20 [B] ディスクサムネイルcacheを消去する

**Original source index:** cache棚卸し#5、全用途のcache. Original v8 literal reference IDs: v8-S20-01. Reference status: verbatim-v7-reference-set-transcribed.

**Frozen original source references (v8 literal transcription if available):**
- v8-S20-01 / UG:115-115: S:/Projects/PixivVault/docs/UI_GUIDE.md L115-115 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=ffa1e2d37ff56d0b32f8fd2ad403ea1b75d3c6e8bca4f53fb1e0c0b5dcb1c4ea.
  - Verbatim expectation:    - サムネイルキャッシュの削除

**Original literal `expected_full`:**

```text
   - サムネイルキャッシュの削除
```

**Current source:** `src/pixivvault_nicegui/shared_image_cache.py` SHA256=5116973c14ea73d2de26af104f93bbee40ac28c0d63e39770f5f61e2b1b938d2 ; `src/pixivvault_nicegui/ui/preferences_view.py` SHA256=6e7d17894d6805dbce0476b07b10e7729be32681983b3079809c78f519b6ee6d.

**Saved operation evidence:** No operation-level result.json bound in examined sources.

**Current applicability / concrete missing:** Disk thumbnail cache clear for all consumers, recovery and restart need connected file evidence.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### S21 [B] ダーク/ライトテーマを切り替える

**Original source index:** UGヘッダー操作. Original v8 literal reference IDs: v8-S21-01. Reference status: verbatim-v7-reference-set-transcribed.

**Frozen original source references (v8 literal transcription if available):**
- v8-S21-01 / UG:24-24: S:/Projects/PixivVault/docs/UI_GUIDE.md L24-24 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=3284d6f88d0cd2a35cf471cade18940ecce4c1dd0a9541624529c2dfe2cd71e9.
  - Verbatim expectation: - 履歴（直近のバックアップタブへ）／保存フォルダを開く／ダーク・ライト切替／ブラウザでPixivを開く（Cookie自動連携用）／設定（歯車）

**Original literal `expected_full`:**

```text
- 履歴（直近のバックアップタブへ）／保存フォルダを開く／ダーク・ライト切替／ブラウザでPixivを開く（Cookie自動連携用）／設定（歯車）
```

**Current source:** `src/pixivvault_nicegui/ui/theme.py` SHA256=a9bbd5fc3d439b3b8b9ef994f6a2775517a37c4bbbc03bf4300eed131aa2318d ; `src/pixivvault_nicegui/ui/shell.py` SHA256=a5069f1486feea7c1895eec68e44fa6442bdc4c0c9b74f5d0cd2c3540a343137.

**Saved operation evidence:** artifacts/p3-315-d08-theme-recommend-r2/1280-initial/result.json ; SHA256=afa1e651615892a8b1aed9c952e00f8bed72b845f9b849516ed7e5a36eb9f8e2; recorded_success=True; no per-source before binding in result

**Current applicability / concrete missing:** Partial 1280 theme switch succeeded; enclosing run did not fully pass; bind full page/restart/native behavior.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### S22 [B] ブラウザでPixivを開く

**Original source index:** UGヘッダー操作. Original v8 literal reference IDs: v8-S22-01. Reference status: verbatim-v7-reference-set-transcribed.

**Frozen original source references (v8 literal transcription if available):**
- v8-S22-01 / UG:24-24: S:/Projects/PixivVault/docs/UI_GUIDE.md L24-24 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=3284d6f88d0cd2a35cf471cade18940ecce4c1dd0a9541624529c2dfe2cd71e9.
  - Verbatim expectation: - 履歴（直近のバックアップタブへ）／保存フォルダを開く／ダーク・ライト切替／ブラウザでPixivを開く（Cookie自動連携用）／設定（歯車）

**Original literal `expected_full`:**

```text
- 履歴（直近のバックアップタブへ）／保存フォルダを開く／ダーク・ライト切替／ブラウザでPixivを開く（Cookie自動連携用）／設定（歯車）
```

**Current source:** `src/pixivvault_nicegui/ui/shell.py` SHA256=a5069f1486feea7c1895eec68e44fa6442bdc4c0c9b74f5d0cd2c3540a343137 ; `src/pixivvault_nicegui/desktop_integration.py` SHA256=e259ddff12e97e88621dea4f694c553b0e3b81c9bb22e1f7de9cc4510d1a5ebe.

**Saved operation evidence:** No operation-level result.json bound in examined sources.

**Current applicability / concrete missing:** Pixiv header link is wired; browser OS open through current native profile was not observed.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### E01 [B] DBとmetadataのみをZIPへエクスポートする

**Original source index:** EXPORT_DB_ONLY_SETTINGS_ENTRY_SPECの11完了条件. Original v8 literal reference IDs: v8-E01-01, v8-E01-02, v8-E01-03, v8-E01-04, v8-E01-05. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-E01-01 / E01-original-01: S:/Projects/PixivVault/docs/EXPORT_DB_ONLY_SETTINGS_ENTRY_SPEC_2026-09-01.md L172-184 ; source SHA256=ca617ff0f0d60e8942e85456f5864f101395ee4ae6a546960a3de416b43d54a5; extracted-lines SHA256=58410fd019ae2e73f40a1b721f2e4d1c95b1452b71378bbee220c8a07ea6fa02.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-E01-02 / E01-original-02: S:/Projects/PixivVault/docs/EXPORT_DB_ONLY_SETTINGS_ENTRY_SPEC_2026-09-01.md L24-84 ; source SHA256=ca617ff0f0d60e8942e85456f5864f101395ee4ae6a546960a3de416b43d54a5; extracted-lines SHA256=355971aba43c692cc1179ca2ecfa769dde4eff3274d0aabdfcfea0ff17daf00f.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-E01-03 / E01-original-03: S:/Projects/PixivVault/core.py L2286-2308 ; source SHA256=c3c758965bc186aee0e90c37910baa01e9c7860338915ee89354ddf1161dcc0b; extracted-lines SHA256=7fd0dbbd97de496fecb533e1f9600537b624053ea58ffd37ba2e458be284eb74.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-E01-04 / E01-original-04: S:/Projects/PixivVault/core.py L2311-2383 ; source SHA256=c3c758965bc186aee0e90c37910baa01e9c7860338915ee89354ddf1161dcc0b; extracted-lines SHA256=3ff7d47417d108cc8682bf9b4ba46bf4312464a4a2b7192f4a81bd0d89e2d4ab.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-E01-05 / E01-original-05: S:/Projects/PixivVault/docs/EXPORT_DB_ONLY_SETTINGS_ENTRY_SPEC_2026-09-01.md L316-321 ; source SHA256=ca617ff0f0d60e8942e85456f5864f101395ee4ae6a546960a3de416b43d54a5; extracted-lines SHA256=9195ebd2c335d0c9e43fc12d1cf0d050a05d702b91c99755e27369e4e08225d6.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/legacy_metadata_export.py` SHA256=aaaaaa115145b99bd9c115daeea7ec5c4c25e9d8841c4941dc36e7c126e7617a ; `src/pixivvault_nicegui/legacy_zip_export.py` SHA256=d65b4f2343760c874288c13e6cc84f231153a18b96830835a5125c4d3400d9ad ; `src/pixivvault_nicegui/ui/preferences_view.py` SHA256=6e7d17894d6805dbce0476b07b10e7729be32681983b3079809c78f519b6ee6d.

**Saved operation evidence:** artifacts/p3-315-e01-export-edge-r3/result.json ; SHA256=5e17e5f6e7415e25337eb364b1f343f538c325b4f06de7d65b0e580d3d80c5f2; recorded_success=True; related source file(s) changed: src/pixivvault_nicegui/legacy_metadata_export.py,src/pixivvault_nicegui/legacy_zip_export.py,src/pixivvault_nicegui/ui/preferences_view.py

**Current applicability / concrete missing:** Synthetic export Edge passed, but full original 11 conditions and file payload/cleanup need current source rebinding.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### A01 [B] 作者の作品一覧オーバーレイを開く

**Original source index:** RC #60B、AUTHOR_PAGE_SPEC:63–100. Original v8 literal reference IDs: v8-A01-01, v8-A01-02, v8-A01-03. Reference status: verbatim-v7-reference-set-transcribed.

**Frozen original source references (v8 literal transcription if available):**
- v8-A01-01 / RC #60B: S:/Projects/PixivVault/docs/REALCLICK_CHECKLIST_2026-08-17.md L147-147 ; source SHA256=dfa039b98bf2f1ba11a043ea426de8c041b5c5d08cf473ace706632bc1703512; extracted-lines SHA256=65dd8d59121560fd55144c4fc2fdc105791c833028e87c2c16259ffb45d0958f.
  - Verbatim expectation: 右クリックメニュー「作者ページを開く」からオーバーレイが開く
- v8-A01-02 / RC #60B: S:/Projects/PixivVault/docs/REALCLICK_CHECKLIST_2026-08-17.md L148-148 ; source SHA256=dfa039b98bf2f1ba11a043ea426de8c041b5c5d08cf473ace706632bc1703512; extracted-lines SHA256=4610c4ecbe20300178e5e96c1492c3c0cc4a1bc87a47aadce8726cdfab8e3aa1.
  - Verbatim expectation: 作者の作品サムネイル一覧が表示される
- v8-A01-03 / RC #60B: S:/Projects/PixivVault/docs/REALCLICK_CHECKLIST_2026-08-17.md L149-149 ; source SHA256=dfa039b98bf2f1ba11a043ea426de8c041b5c5d08cf473ace706632bc1703512; extracted-lines SHA256=15b83e394f04e06422f082dbf0bd35c08bb4e096f354f0ec8daa14c71d2a88a9.
  - Verbatim expectation: ブクマ・差分DL・フォローが動く

**Original literal `expected_full`:**

```text
右クリックメニュー「作者ページを開く」からオーバーレイが開く\n作者の作品サムネイル一覧が表示される\nブクマ・差分DL・フォローが動く
```

**Current source:** `src/pixivvault_nicegui/ui/author_browse_view.py` SHA256=c544578953a8255e48f7bffdddd037b751029c1800b5793a1ca6fc6cf9a81fe4 ; `src/pixivvault_nicegui/author_media.py` SHA256=0b5096b8605aa7d7e65269236a89dfa3daf512e69711dc079952bed724139746.

**Saved operation evidence:** No operation-level result.json bound in examined sources.

**Current applicability / concrete missing:** Author-overlay opening and required bookmark/diff/follow controls need direct author-page GUI evidence.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### A02 [B] 作者ページの追加作品を取得する

**Original source index:** 原「もっと見る」、少数作者の終了条件. Original v8 literal reference IDs: v8-A02-01, v8-A02-02, v8-A02-03, v8-A02-04, v8-A02-05. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-A02-01 / A02-reference-1: S:/Projects/PixivVault/docs/design/AUTHOR_PAGE_SPEC_2026-08-19.md L78-82 ; source SHA256=b5ade205020016b5ec6bd391733c70907e66b468c3e744e008b5c8251bfb2032; extracted-lines SHA256=934f2214843592b8b38bcd865a3287e56d74ba7b0dd7a9acee667ac307b29961.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-A02-02 / A02-reference-2: S:/Projects/PixivVault/docs/design/AUTHOR_PAGE_SPEC_2026-08-19.md L92-100 ; source SHA256=b5ade205020016b5ec6bd391733c70907e66b468c3e744e008b5c8251bfb2032; extracted-lines SHA256=c6cc9c39c0fd51db4b0b019abeeff3f55de9f56ed95d2350698f09d2c13311a5.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-A02-03 / A02-reference-3: S:/Projects/PixivVault/recommend_tab.py L3431-3435 ; source SHA256=9fd1f6ab78973a01f0fbbf09dc7973ce82e47bafbbf8180d007d3da6d571e17d; extracted-lines SHA256=5ae47ab422b4f782246d3fda72214eaccb9104a04317c670c7e442eda92bf022.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-A02-04 / A02-reference-4: S:/Projects/PixivVault/recommend_tab.py L3622-3637 ; source SHA256=9fd1f6ab78973a01f0fbbf09dc7973ce82e47bafbbf8180d007d3da6d571e17d; extracted-lines SHA256=c1741278bdb1522cd813a609b6a4836e303f680512c8ba959bd0f302a4db05c7.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-A02-05 / A02-reference-5: S:/Projects/PixivVault/recommend_tab.py L3678-3697 ; source SHA256=9fd1f6ab78973a01f0fbbf09dc7973ce82e47bafbbf8180d007d3da6d571e17d; extracted-lines SHA256=32897fb7d2a425a12e3da56081ccc93728a3183d8d321b80a64437a63dd45ee2.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/ui/author_browse_view.py` SHA256=c544578953a8255e48f7bffdddd037b751029c1800b5793a1ca6fc6cf9a81fe4 ; `src/pixivvault_nicegui/author_media.py` SHA256=0b5096b8605aa7d7e65269236a89dfa3daf512e69711dc079952bed724139746.

**Saved operation evidence:** artifacts/p3-318-author-revisit/edge-product-r4/1280x720/ui/result.json ; SHA256=d9cfd3f573987cd44ab71cd9f0c571cd19b23c825f2448402c4a297234b467c3; recorded_success=True; no per-source before binding in result

**Current applicability / concrete missing:** Cached revisit succeeded in bounded run; more/end-condition/live pagination still lacks full operation evidence.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### T01 [B] 「今すぐチェック」で対象作者の新着確認・dispatchを開始する

**Original source index:** AR scheduler、PG Phase9/10、70作者・deferred・受付. Original v8 literal reference IDs: v8-T01-01, v8-T01-02, v8-T01-03, v8-T01-04, v8-T01-05, v8-T01-06, v8-T01-07. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-T01-01 / T01-reference-1: S:/Projects/PixivVault/docs/ARCHITECTURE.md L150-152 ; source SHA256=f222d8a8fcd5b2cc1a1829b64b09b6b40a9cb4bbb92547ed1250da93cb6d373e; extracted-lines SHA256=ba59df9a62a41a14a3f8428140cf3596b33682fb4adfffd5f741de709ecec83b.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-T01-02 / T01-reference-2: S:/Projects/PixivVault/scheduler.py L182-214 ; source SHA256=18c1bb184fe5a7199f8c3e2636ab95c1b59ed1a2847c76c38ab111ac492b006e; extracted-lines SHA256=c8d6038df59cfaf799e4a93f61efa8f483a798d2f4faf624c726a3e357b3305a.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-T01-03 / T01-reference-3: S:/Projects/PixivVault/scheduler.py L216-242 ; source SHA256=18c1bb184fe5a7199f8c3e2636ab95c1b59ed1a2847c76c38ab111ac492b006e; extracted-lines SHA256=55f80c1101864628f882a58ae7aec92b64b1fe2b08428551552004f40e62963d.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-T01-04 / T01-reference-4: S:/Projects/PixivVault-NiceGUI-p3-vault-cleanup-race-20261028/docs/MANAGED_SCHEDULER_PHASE9_2026-10-24.md L15-15 ; source SHA256=19554422b3b63c860eb9853a0e6c583fd72d5c80b1b4db60b7228817f5409185; extracted-lines SHA256=77207746688a1921b62855d1b144729ad2c807b9543549234ca5a519d28e2b11.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-T01-05 / T01-reference-5: S:/Projects/PixivVault-NiceGUI-p3-vault-cleanup-race-20261028/docs/MANAGED_SCHEDULER_PHASE9_2026-10-24.md L27-27 ; source SHA256=19554422b3b63c860eb9853a0e6c583fd72d5c80b1b4db60b7228817f5409185; extracted-lines SHA256=26c64d6bdfc5b16425105a4eb0b453c158b5183e30e2de8a607ef268556f61ba.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-T01-06 / T01-reference-6: S:/Projects/PixivVault-NiceGUI-p3-vault-cleanup-race-20261028/docs/MANAGED_SCHEDULER_PHASE9_2026-10-24.md L37-39 ; source SHA256=19554422b3b63c860eb9853a0e6c583fd72d5c80b1b4db60b7228817f5409185; extracted-lines SHA256=28e3064ac2c857c8183742f239e2c5878340045564282ace78fb0ad95df0c34a.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-T01-07 / T01-reference-7: S:/Projects/PixivVault-NiceGUI-p3-vault-cleanup-race-20261028/docs/MANAGED_SCHEDULER_PHASE9_2026-10-24.md L21-21 ; source SHA256=19554422b3b63c860eb9853a0e6c583fd72d5c80b1b4db60b7228817f5409185; extracted-lines SHA256=2350d16eefeeeb4b75b496a3bf3c2ca2f1c44df8f8f74be5bd90a7b3c582a4bf.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/managed_scheduler.py` SHA256=75ba2af038186a1162e27c9887f323304e0e68917dd9d8130d8b146a1a6bae9a ; `src/pixivvault_nicegui/ui/scheduler_view.py` SHA256=bfb7178eb06d155c91241a3843fa911ea70194b682470e5d66dbe182a5f60e73.

**Saved operation evidence:** artifacts/p3-318-scheduler-70-authors/edge-r1/result.json ; SHA256=e7e19dcab3013122ff3cde024a59f5667c1ef754ba3a47abf4b44bfe78d4b1d4; recorded_success=True; related recorded files same where compared

**Current applicability / concrete missing:** 70-author synthetic dispatch succeeded; actual authorization and current lifecycle binding remain separate gates.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### T02 [B] schedulerの履歴・状態を確認する

**Original source index:** PG Phase9/10、再起動・CAS. Original v8 literal reference IDs: v8-T02-01, v8-T02-02, v8-T02-03, v8-T02-04, v8-T02-05, v8-T02-06. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-T02-01 / T02-reference-1: S:/Projects/PixivVault/scheduler.py L56-89 ; source SHA256=18c1bb184fe5a7199f8c3e2636ab95c1b59ed1a2847c76c38ab111ac492b006e; extracted-lines SHA256=9c430d19155cdfcb574cb781e289009d6eb6460623e7a27b11ab238a5648bd6f.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-T02-02 / T02-reference-2: S:/Projects/PixivVault/scheduler.py L182-242 ; source SHA256=18c1bb184fe5a7199f8c3e2636ab95c1b59ed1a2847c76c38ab111ac492b006e; extracted-lines SHA256=9a859643b2e872d7f27d48e876e5f881a7c375957eacc6c099c43ed9f69f317b.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-T02-03 / T02-reference-3: S:/Projects/PixivVault-NiceGUI-p3-vault-cleanup-race-20261028/docs/MANAGED_SCHEDULER_PHASE9_2026-10-24.md L7-8 ; source SHA256=19554422b3b63c860eb9853a0e6c583fd72d5c80b1b4db60b7228817f5409185; extracted-lines SHA256=3e0fa5846e4360ab62bc724b1fbf7e21b1196fd4fc8d1a37fa46d9e1e8822be8.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-T02-04 / T02-reference-4: S:/Projects/PixivVault-NiceGUI-p3-vault-cleanup-race-20261028/docs/MANAGED_SCHEDULER_PHASE9_2026-10-24.md L15-18 ; source SHA256=19554422b3b63c860eb9853a0e6c583fd72d5c80b1b4db60b7228817f5409185; extracted-lines SHA256=ac9f0f9ee15511e30a087359550d309f23b5d418d3b2867962b5d3dfe5d43806.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-T02-05 / T02-reference-5: S:/Projects/PixivVault-NiceGUI-p3-vault-cleanup-race-20261028/docs/MANAGED_SCHEDULER_PHASE9_2026-10-24.md L27-31 ; source SHA256=19554422b3b63c860eb9853a0e6c583fd72d5c80b1b4db60b7228817f5409185; extracted-lines SHA256=c498ddf69a909c2f49ee21bf856505c003ba7099142169aa8216f10aedb6de44.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-T02-06 / T02-reference-6: S:/Projects/PixivVault-NiceGUI-p3-vault-cleanup-race-20261028/docs/MANAGED_SCHEDULER_PHASE9_2026-10-24.md L39-39 ; source SHA256=19554422b3b63c860eb9853a0e6c583fd72d5c80b1b4db60b7228817f5409185; extracted-lines SHA256=dcc013b5c0dadc47eea4858ddcdf63447e5463c5d7c41c2510820cfe6beba217.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/managed_scheduler.py` SHA256=75ba2af038186a1162e27c9887f323304e0e68917dd9d8130d8b146a1a6bae9a ; `src/pixivvault_nicegui/ui/scheduler_view.py` SHA256=bfb7178eb06d155c91241a3843fa911ea70194b682470e5d66dbe182a5f60e73.

**Saved operation evidence:** artifacts/p3-318-scheduler-70-authors/edge-r1/result.json ; SHA256=e7e19dcab3013122ff3cde024a59f5667c1ef754ba3a47abf4b44bfe78d4b1d4; recorded_success=True; related recorded files same where compared

**Current applicability / concrete missing:** Scheduler history/status initial/restart/CAS and error paths need direct viewing evidence.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### M01 [B] 小説を保存する

**Original source index:** TXT/EPUB/BOTH、シリーズ・挿絵・表紙. Original v8 literal reference IDs: v8-M01-01, v8-M01-02, v8-M01-03, v8-M01-04, v8-M01-05, v8-M01-06. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-M01-01 / M01-reference-1: S:/Projects/PixivVault/FEATURES.md L36-40 ; source SHA256=debf63a162bf4cb0d8cd9f65f408336dfb94c6afb27e7ab8a2013d4ac0e86d9a; extracted-lines SHA256=1fd63ca44cf985c407307de6f9bc877695dd1b71c3d43b5c327e933531582639.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-M01-02 / M01-reference-2: S:/Projects/PixivVault/pixiv_client.py L1015-1028 ; source SHA256=37fffbffe3dc872af3b83df272870b6d7445d90bd519b98a3289890bf3107f8f; extracted-lines SHA256=eb14c5a225f0cc7decdcdfd45110fe7f167b24637428177a33e3fc85e3df26b4.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-M01-03 / M01-reference-3: S:/Projects/PixivVault/core.py L1518-1596 ; source SHA256=c3c758965bc186aee0e90c37910baa01e9c7860338915ee89354ddf1161dcc0b; extracted-lines SHA256=14434c153e980dad44f24c60a386722c3ff7ce8621ef440829089a84e2f6d2de.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-M01-04 / M01-reference-4: S:/Projects/PixivVault-NiceGUI-p3-vault-cleanup-race-20261028/docs/PHASE11_MEDIA_PARITY_CONTRACT_2026-10-24.md L14-15 ; source SHA256=a3f6e1c4a8fa6fb6d263c984df734d96c327d95c3d7d09d9ebdcf5cab42af444; extracted-lines SHA256=f23ac2de946532d4e43f42a7edaa4a1d99cdb196f89552957183393c05dd1121.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-M01-05 / M01-reference-5: S:/Projects/PixivVault-NiceGUI-p3-vault-cleanup-race-20261028/docs/PHASE11_MEDIA_PARITY_CONTRACT_2026-10-24.md L22-24 ; source SHA256=a3f6e1c4a8fa6fb6d263c984df734d96c327d95c3d7d09d9ebdcf5cab42af444; extracted-lines SHA256=93ed79d184edb315237f9f09097ef992693abd501c00fa59daa4bc0f66e2854f.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-M01-06 / M01-reference-6: S:/Projects/PixivVault-NiceGUI-p3-vault-cleanup-race-20261028/docs/PHASE11_MEDIA_PARITY_CONTRACT_2026-10-24.md L33-36 ; source SHA256=a3f6e1c4a8fa6fb6d263c984df734d96c327d95c3d7d09d9ebdcf5cab42af444; extracted-lines SHA256=216a276094457ff4064d552ba609022286514ae14919289d92427adbf06d0aa4.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/novel_practical_export.py` SHA256=fca5ab78ef2e9abb16cbcf4d4bfd5f3cb6b49edd7f15aa90ae83aae4aef9e20d ; `src/pixivvault_nicegui/ui/novel_save_view.py` SHA256=2cf86f289e809fb9a4cd0e7f054c3b1d3f1d011f740ceb51281135c17c3daffc.

**Saved operation evidence:** No operation-level result.json bound in examined sources.

**Current applicability / concrete missing:** Novel TXT/EPUB/BOTH, series, cover, illustration, saved hashes and restart need integrated media-path GUI proof.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### M04 [B] 保存済み小説本文を閲覧する

**Original source index:** 本文・長文ページ・挿絵・再起動. Original v8 literal reference IDs: v8-M04-01, v8-M04-02, v8-M04-03, v8-M04-04, v8-M04-05. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-M04-01 / M04-reference-1: S:/Projects/PixivVault/FEATURES.md L36-40 ; source SHA256=debf63a162bf4cb0d8cd9f65f408336dfb94c6afb27e7ab8a2013d4ac0e86d9a; extracted-lines SHA256=1fd63ca44cf985c407307de6f9bc877695dd1b71c3d43b5c327e933531582639.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-M04-02 / M04-reference-2: S:/Projects/PixivVault/library_tab.py L859-887 ; source SHA256=2276e0f39209d9e0b4b687f881845b96ada610511d52c27de15ec7e9fcbae0bc; extracted-lines SHA256=d5fd19270fc04ea32b9839fee8377345069b7efed4bd5fe9ab5e5dfa42429d10.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-M04-03 / M04-reference-3: S:/Projects/PixivVault-NiceGUI-p3-vault-cleanup-race-20261028/docs/PHASE11_MEDIA_PARITY_CONTRACT_2026-10-24.md L15-15 ; source SHA256=a3f6e1c4a8fa6fb6d263c984df734d96c327d95c3d7d09d9ebdcf5cab42af444; extracted-lines SHA256=ec6972ef49d31d08a6339bbcb996d26d4b26e86dee5e0122706d64d64123f7d2.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-M04-04 / M04-reference-4: S:/Projects/PixivVault-NiceGUI-p3-vault-cleanup-race-20261028/docs/PHASE11_MEDIA_PARITY_CONTRACT_2026-10-24.md L26-26 ; source SHA256=a3f6e1c4a8fa6fb6d263c984df734d96c327d95c3d7d09d9ebdcf5cab42af444; extracted-lines SHA256=0afc409ee409b88c07a782e628086bbe5222a1972fc09efe8ae8375e1b63ffe0.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-M04-05 / M04-reference-5: S:/Projects/PixivVault-NiceGUI-p3-vault-cleanup-race-20261028/docs/PHASE11_MEDIA_PARITY_CONTRACT_2026-10-24.md L35-36 ; source SHA256=a3f6e1c4a8fa6fb6d263c984df734d96c327d95c3d7d09d9ebdcf5cab42af444; extracted-lines SHA256=99bbd0400f45b7908b16abeec340bb8638788bc5caceea801b1fe590e805ebbf.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/novel_saved_reader.py` SHA256=63ec1b99936165fe808988303cabfc73c4235df2292e79c3387a1b070b168552 ; `src/pixivvault_nicegui/ui/media_views.py` SHA256=c05c9a1ff9d581212529baa794f6f79d27e44f27676ab89bacd5536352ee0e37.

**Saved operation evidence:** No operation-level result.json bound in examined sources.

**Current applicability / concrete missing:** Saved long text reader, page layout, illustrations and restart need direct GUI proof.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### M06 [B] うごイラmetadata/フレームを確認する

**Original source index:** frame delay・作者ID・形式選択. Original v8 literal reference IDs: v8-M06-01, v8-M06-02, v8-M06-03, v8-M06-04, v8-M06-05, v8-M06-06. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-M06-01 / M06-reference-1: S:/Projects/PixivVault/pixiv_client.py L1652-1656 ; source SHA256=37fffbffe3dc872af3b83df272870b6d7445d90bd519b98a3289890bf3107f8f; extracted-lines SHA256=b76ac2d2bd98700fe46ab1a1ff45b5f86de7f464b2fa198b41f7050f0fd46931.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-M06-02 / M06-reference-2: S:/Projects/PixivVault/docs/design/ARTBOOK_VIEWER_UGOIRA_SPEC_2026-08-22.md L13-20 ; source SHA256=6a00e3a340b5e0404422a405744de0ecd69d056ef7844f249634efac258a2a5d; extracted-lines SHA256=f56f5cf143942d4b6a68450b676207a5dc13eb0cd1dc692922926c0ea2c9f8d5.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-M06-03 / M06-reference-3: S:/Projects/PixivVault/core.py L937-976 ; source SHA256=c3c758965bc186aee0e90c37910baa01e9c7860338915ee89354ddf1161dcc0b; extracted-lines SHA256=ee8d529269421159ea7ea791abaf198bc25b8166068acbd5926c7be11176cc1a.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-M06-04 / M06-reference-4: S:/Projects/PixivVault-NiceGUI-p3-vault-cleanup-race-20261028/docs/PHASE11_MEDIA_PARITY_CONTRACT_2026-10-24.md L16-17 ; source SHA256=a3f6e1c4a8fa6fb6d263c984df734d96c327d95c3d7d09d9ebdcf5cab42af444; extracted-lines SHA256=7017af5debfb1c4c2301c2df2cff707c25918411421432abc2765f0034df2d35.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-M06-05 / M06-reference-5: S:/Projects/PixivVault-NiceGUI-p3-vault-cleanup-race-20261028/docs/PHASE11_MEDIA_PARITY_CONTRACT_2026-10-24.md L30-32 ; source SHA256=a3f6e1c4a8fa6fb6d263c984df734d96c327d95c3d7d09d9ebdcf5cab42af444; extracted-lines SHA256=b042589b8f11c2a71ff48bedb95dbf4690172c8fe5d1b4b0a5a09cf9666875b8.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-M06-06 / M06-reference-6: S:/Projects/PixivVault-NiceGUI-p3-vault-cleanup-race-20261028/docs/PHASE11_MEDIA_PARITY_CONTRACT_2026-10-24.md L35-35 ; source SHA256=a3f6e1c4a8fa6fb6d263c984df734d96c327d95c3d7d09d9ebdcf5cab42af444; extracted-lines SHA256=b23ecf781185efbe0e313572037745e4acfc6f946e907750894b8ae303f2bd03.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/ugoira_practical.py` SHA256=946a1a33c49e76e46dc90b133a981752622ec1e89c5c4b76567b8454295d56b6 ; `src/pixivvault_nicegui/ui/ugoira_view.py` SHA256=86ec860b9959ddde7d2231c9142fe56e1b13713ab3d29175fbf47f6df94267f4.

**Saved operation evidence:** No operation-level result.json bound in examined sources.

**Current applicability / concrete missing:** Ugoira metadata/frame delay, author identity masking and format UI need integrated observation.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### M07 [B] うごイラを形式選択して保存する

**Original source index:** frames/GIF/MP4、delay・hash・CPU codec. Original v8 literal reference IDs: v8-M07-01, v8-M07-02, v8-M07-03, v8-M07-04, v8-M07-05, v8-M07-06. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-M07-01 / M07-reference-1: S:/Projects/PixivVault/FEATURES.md L42-49 ; source SHA256=debf63a162bf4cb0d8cd9f65f408336dfb94c6afb27e7ab8a2013d4ac0e86d9a; extracted-lines SHA256=2b3711522fecd8484fe74541e6cf83876841ef144acbec986fa82511f6335d8f.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-M07-02 / M07-reference-2: S:/Projects/PixivVault/core.py L982-1003 ; source SHA256=c3c758965bc186aee0e90c37910baa01e9c7860338915ee89354ddf1161dcc0b; extracted-lines SHA256=51505c741d7c1eff3f447a2084268a1f926091c279562bd42292f3ee8d3b2e4a.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-M07-03 / M07-reference-3: S:/Projects/PixivVault/core.py L1016-1085 ; source SHA256=c3c758965bc186aee0e90c37910baa01e9c7860338915ee89354ddf1161dcc0b; extracted-lines SHA256=b32bb174141786a461ffe23d82e3ea3e0ad6f6553658475926f06661a58bee60.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-M07-04 / M07-reference-4: S:/Projects/PixivVault/core.py L1091-1107 ; source SHA256=c3c758965bc186aee0e90c37910baa01e9c7860338915ee89354ddf1161dcc0b; extracted-lines SHA256=91868a03cd251775e86f505e1f949d7fbd03dd2d5ae3a5fc31678afe5e9c2185.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-M07-05 / M07-reference-5: S:/Projects/PixivVault-NiceGUI-p3-vault-cleanup-race-20261028/docs/PHASE11_MEDIA_PARITY_CONTRACT_2026-10-24.md L23-24 ; source SHA256=a3f6e1c4a8fa6fb6d263c984df734d96c327d95c3d7d09d9ebdcf5cab42af444; extracted-lines SHA256=fc69971ccf9ff13be3083461df5a9b9aa1737ea02f416eba851e1ee81287ee75.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-M07-06 / M07-reference-6: S:/Projects/PixivVault-NiceGUI-p3-vault-cleanup-race-20261028/docs/PHASE11_MEDIA_PARITY_CONTRACT_2026-10-24.md L33-36 ; source SHA256=a3f6e1c4a8fa6fb6d263c984df734d96c327d95c3d7d09d9ebdcf5cab42af444; extracted-lines SHA256=216a276094457ff4064d552ba609022286514ae14919289d92427adbf06d0aa4.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/ugoira_gif.py` SHA256=1c78bab09db00345c02ba9ff660c8162dbbfc92df3ba2dd7c3a11fccacb0848e ; `src/pixivvault_nicegui/ugoira_mp4.py` SHA256=4c903d323d1afce58e900ba253778d627af7b5678e3f7f81e15a044f8bea92ef ; `src/pixivvault_nicegui/ugoira_practical.py` SHA256=946a1a33c49e76e46dc90b133a981752622ec1e89c5c4b76567b8454295d56b6.

**Saved operation evidence:** artifacts/p3-339-native/ugoira-packaged-r1/result.json ; SHA256=0003d4af0ea4bab10e110c7fe3cc5676d919a051fd6a39cee27c48da82baee82; recorded_success=True; no per-source before binding in result

**Current applicability / concrete missing:** Packaged codec output passed; current source and selectable format/frames/hash need rebound.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### M10 [B] 保存済みうごイラを再生する

**Original source index:** 実フレーム・再生/停止・focus・再起動. Original v8 literal reference IDs: v8-M10-01, v8-M10-02, v8-M10-03, v8-M10-04, v8-M10-05, v8-M10-06, v8-M10-07. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-M10-01 / M10-reference-1: S:/Projects/PixivVault/docs/design/ARTBOOK_VIEWER_UGOIRA_SPEC_2026-08-22.md L29-35 ; source SHA256=6a00e3a340b5e0404422a405744de0ecd69d056ef7844f249634efac258a2a5d; extracted-lines SHA256=be5f6d9fe5c5f3573f2f7ea4ef56e17daf46d34a4b8d9c60187bd4735390090d.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-M10-02 / M10-reference-2: S:/Projects/PixivVault/docs/design/ARTBOOK_VIEWER_UGOIRA_SPEC_2026-08-22.md L100-116 ; source SHA256=6a00e3a340b5e0404422a405744de0ecd69d056ef7844f249634efac258a2a5d; extracted-lines SHA256=818968fe9877ac6188fb68f0715ecf09fe8af9d52b46c6fafc035ffa4cbcc74f.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-M10-03 / M10-reference-3: S:/Projects/PixivVault/artbook_viewer.py L419-459 ; source SHA256=a83a0705d9c879517d52fa8bf51a54a4bf0ec4ec5aa67b2817da796efa697f6d; extracted-lines SHA256=24450ee1126c9debdf2c8962f70c30cb4f437291839d6b3f5d0e0582a861cf44.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-M10-04 / M10-reference-4: S:/Projects/PixivVault/artbook_viewer.py L305-352 ; source SHA256=a83a0705d9c879517d52fa8bf51a54a4bf0ec4ec5aa67b2817da796efa697f6d; extracted-lines SHA256=40624a265a1944ebb34ded71a692363e599c4860843cf03a9e20a8896304e46c.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-M10-05 / M10-reference-5: S:/Projects/PixivVault/library_tab.py L1655-1664 ; source SHA256=2276e0f39209d9e0b4b687f881845b96ada610511d52c27de15ec7e9fcbae0bc; extracted-lines SHA256=cd0d15e1559031f787978dbba51831f9634143dec9143e3a02d98d29b692f681.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-M10-06 / M10-reference-6: S:/Projects/PixivVault-NiceGUI-p3-vault-cleanup-race-20261028/docs/PHASE11_MEDIA_PARITY_CONTRACT_2026-10-24.md L26-26 ; source SHA256=a3f6e1c4a8fa6fb6d263c984df734d96c327d95c3d7d09d9ebdcf5cab42af444; extracted-lines SHA256=0afc409ee409b88c07a782e628086bbe5222a1972fc09efe8ae8375e1b63ffe0.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-M10-07 / M10-reference-7: S:/Projects/PixivVault-NiceGUI-p3-vault-cleanup-race-20261028/docs/PHASE11_MEDIA_PARITY_CONTRACT_2026-10-24.md L35-36 ; source SHA256=a3f6e1c4a8fa6fb6d263c984df734d96c327d95c3d7d09d9ebdcf5cab42af444; extracted-lines SHA256=99bbd0400f45b7908b16abeec340bb8638788bc5caceea801b1fe590e805ebbf.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/ui/ugoira_view.py` SHA256=86ec860b9959ddde7d2231c9142fe56e1b13713ab3d29175fbf47f6df94267f4 ; `src/pixivvault_nicegui/ugoira_storage.py` SHA256=c18950e464736ad754c954c197402a743aa811cb3112da9c853148af81c28ec0.

**Saved operation evidence:** artifacts/p3-339-native/ugoira-packaged-r1/result.json ; SHA256=0003d4af0ea4bab10e110c7fe3cc5676d919a051fd6a39cee27c48da82baee82; recorded_success=True; no per-source before binding in result

**Current applicability / concrete missing:** Packaged ugoira run passed; playback controls, focus and restart in current view need direct GUI observation.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### N01 [B] Windowsアプリを起動する

**Original source index:** 通常起動＋旧URI入口、単一起動・画面. Original v8 literal reference IDs: v8-N01-01. Reference status: verbatim-v7-reference-set-transcribed.

**Frozen original source references (v8 literal transcription if available):**
- v8-N01-01 / UG:12-12: S:/Projects/PixivVault/docs/UI_GUIDE.md L12-12 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=fa53a51ad4b879b854047ed141b7c1872d99fca4159b092ae8587b9e51d06fac.
  - Verbatim expectation: - 起動時 1920×1080・`maximized=True`で画面いっぱいへ（UIブラッシュアップ憲法の基準サイズ・裁定#124）、最小サイズ1280×720、`resizable=True`（`main.py:315-325`実測。裁定#123）。タイトル「PixivVault」、シードカラー`#0096FA`のダークテーマ既定

**Original literal `expected_full`:**

```text
- 起動時 1920×1080・`maximized=True`で画面いっぱいへ（UIブラッシュアップ憲法の基準サイズ・裁定#124）、最小サイズ1280×720、`resizable=True`（`main.py:315-325`実測。裁定#123）。タイトル「PixivVault」、シードカラー`#0096FA`のダークテーマ既定
```

**Current source:** `src/pixivvault_nicegui/native_startup.py` SHA256=5bd5511814f186ebdb063e728a1040d61c76e04220b50f7eb1b3312f7221892d ; `src/pixivvault_nicegui/native_instance.py` SHA256=3f826836d181058efd5c573c0a2636e0984d68dbd2d79f6088c5431942b25a7c.

**Saved operation evidence:** artifacts/p3-340-protocol-class-native-r1/result.json ; SHA256=47d6bdc6d3967bddda450d6bb78f5253fc336a63a3c5161ffcf1cac96a337376; recorded_success=True; no per-source before binding in result

**Supplementary recorded evidence (bounded; not a substitute for original operation):** artifacts/p3-339-native/jobs-packaged-r1/first.json ; SHA256=85350a79ac865083d4ce5576b73d96681633848afbbfb8eb335de065cc3c7d1d | artifacts/p3-339-native/jobs-packaged-r1/restart.json ; SHA256=e248c8e761dbdb573aa8229a8161fbb99789407ecbfedd22e2cead90b44f3854.

**Current applicability / concrete missing:** Packaged startup passed; current native_instance differs, confirm normal and URI entry, singleton/window.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### N02 [B] アプリを正常終了する

**Original source index:** ×確認＋トレイ終了、DB/子/port解放. Original v8 literal reference IDs: v8-N02-01, v8-N02-02. Reference status: verbatim-v7-reference-set-transcribed.

**Frozen original source references (v8 literal transcription if available):**
- v8-N02-01 / UG:13-13: S:/Projects/PixivVault/docs/UI_GUIDE.md L13-13 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=0e5a275da8bb0c93b982d5c8fa66856db8d3ba185d09d0ac23467371e2385335.
  - Verbatim expectation: - 「×」ボタンでは即終了せず、終了確認ダイアログを挟む（[main.py](../main.py)の`prevent_close=True`）
- v8-N02-02 / UG:130-133: S:/Projects/PixivVault/docs/UI_GUIDE.md L130-133 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=fe952c72d22bc20fd19fed285f94c2d66acf90305c22c0f1a5a9453368608480.
  - Verbatim expectation: ## 5. タスクトレイ（tray.py）\n\n- メニュー: 「PixivVaultを開く」（既定動作、ダブルクリックでも発火）／「終了」\n- 「終了」選択時はダウンロード停止要求→バックグラウンドスレッド終了→スケジューラ停止→トレイ停止→DBクローズ→ウィンドウ破棄、という確認ダイアログなしの即時完全終了（「×」ボタンとの違い）

**Original literal `expected_full`:**

```text
- 「×」ボタンでは即終了せず、終了確認ダイアログを挟む（[main.py](../main.py)の`prevent_close=True`）\n## 5. タスクトレイ（tray.py）\n\n- メニュー: 「PixivVaultを開く」（既定動作、ダブルクリックでも発火）／「終了」\n- 「終了」選択時はダウンロード停止要求→バックグラウンドスレッド終了→スケジューラ停止→トレイ停止→DBクローズ→ウィンドウ破棄、という確認ダイアログなしの即時完全終了（「×」ボタンとの違い）
```

**Current source:** `src/pixivvault_nicegui/native_lifecycle.py` SHA256=d7812da4a48fa42c0c16b5f6eec6ddcc94b7891d6af393aa74354926a51c0aa0 ; `src/pixivvault_nicegui/native_tray_exit.py` SHA256=76619e17c47491c1cdc1dda2d2ee56e1720cb800ce3776630b8d1ca57960c04e.

**Saved operation evidence:** artifacts/p3-339-native/tray-packaged-r3/result.json ; SHA256=bc14d1783b54e27139f51ae14ac4dc8dc2b2cf47b0b65208a52e2ad7982d0246; recorded_success=True; no per-source before binding in result

**Supplementary recorded evidence (bounded; not a substitute for original operation):** artifacts/p3-339-native/jobs-packaged-r1/first.json ; SHA256=85350a79ac865083d4ce5576b73d96681633848afbbfb8eb335de065cc3c7d1d | artifacts/p3-339-native/jobs-packaged-r1/restart.json ; SHA256=e248c8e761dbdb573aa8229a8161fbb99789407ecbfedd22e2cead90b44f3854.

**Current applicability / concrete missing:** Packaged normal exit and tray exit passed; rebind changed native owner/termination paths and exit-confirm.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### N03 [B] アプリをトレイに格納する

**Original source index:** 最小化・通知領域への移動. Original v8 literal reference IDs: v8-N03-01, v8-N03-02. Reference status: verbatim-v7-reference-set-transcribed.

**Frozen original source references (v8 literal transcription if available):**
- v8-N03-01 / UG:14-14: S:/Projects/PixivVault/docs/UI_GUIDE.md L14-14 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=c87a4c180a898635aaae6073e4ba8fae269e5917c1e3e4032a33f6ccd9b13baa.
  - Verbatim expectation: - 最小化時は設定「最小化時にタスクトレイに格納する」（既定ON）に従いタスクバーからも隠れ、トレイ常駐化する
- v8-N03-02 / UG:130-133: S:/Projects/PixivVault/docs/UI_GUIDE.md L130-133 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=fe952c72d22bc20fd19fed285f94c2d66acf90305c22c0f1a5a9453368608480.
  - Verbatim expectation: ## 5. タスクトレイ（tray.py）\n\n- メニュー: 「PixivVaultを開く」（既定動作、ダブルクリックでも発火）／「終了」\n- 「終了」選択時はダウンロード停止要求→バックグラウンドスレッド終了→スケジューラ停止→トレイ停止→DBクローズ→ウィンドウ破棄、という確認ダイアログなしの即時完全終了（「×」ボタンとの違い）

**Original literal `expected_full`:**

```text
- 最小化時は設定「最小化時にタスクトレイに格納する」（既定ON）に従いタスクバーからも隠れ、トレイ常駐化する\n## 5. タスクトレイ（tray.py）\n\n- メニュー: 「PixivVaultを開く」（既定動作、ダブルクリックでも発火）／「終了」\n- 「終了」選択時はダウンロード停止要求→バックグラウンドスレッド終了→スケジューラ停止→トレイ停止→DBクローズ→ウィンドウ破棄、という確認ダイアログなしの即時完全終了（「×」ボタンとの違い）
```

**Current source:** `src/pixivvault_nicegui/native_tray_exit.py` SHA256=76619e17c47491c1cdc1dda2d2ee56e1720cb800ce3776630b8d1ca57960c04e ; `src/pixivvault_nicegui/desktop_integration.py` SHA256=e259ddff12e97e88621dea4f694c553b0e3b81c9bb22e1f7de9cc4510d1a5ebe.

**Saved operation evidence:** artifacts/p3-339-native/tray-packaged-r3/result.json ; SHA256=bc14d1783b54e27139f51ae14ac4dc8dc2b2cf47b0b65208a52e2ad7982d0246; recorded_success=True; no per-source before binding in result

**Current applicability / concrete missing:** Packaged minimize->tray hide passed; rebind current owner and tray settings/notification icon.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### N04 [B] 最小化状態から画面を復帰する

**Original source index:** 位置・focus・GDI・子ウィンドウ. Original v8 literal reference IDs: v8-N04-01. Reference status: verbatim-v7-reference-set-transcribed.

**Frozen original source references (v8 literal transcription if available):**
- v8-N04-01 / UG:130-133: S:/Projects/PixivVault/docs/UI_GUIDE.md L130-133 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=fe952c72d22bc20fd19fed285f94c2d66acf90305c22c0f1a5a9453368608480.
  - Verbatim expectation: ## 5. タスクトレイ（tray.py）\n\n- メニュー: 「PixivVaultを開く」（既定動作、ダブルクリックでも発火）／「終了」\n- 「終了」選択時はダウンロード停止要求→バックグラウンドスレッド終了→スケジューラ停止→トレイ停止→DBクローズ→ウィンドウ破棄、という確認ダイアログなしの即時完全終了（「×」ボタンとの違い）

**Original literal `expected_full`:**

```text
## 5. タスクトレイ（tray.py）\n\n- メニュー: 「PixivVaultを開く」（既定動作、ダブルクリックでも発火）／「終了」\n- 「終了」選択時はダウンロード停止要求→バックグラウンドスレッド終了→スケジューラ停止→トレイ停止→DBクローズ→ウィンドウ破棄、という確認ダイアログなしの即時完全終了（「×」ボタンとの違い）
```

**Current source:** `src/pixivvault_nicegui/native_tray_exit.py` SHA256=76619e17c47491c1cdc1dda2d2ee56e1720cb800ce3776630b8d1ca57960c04e ; `src/pixivvault_nicegui/desktop_integration.py` SHA256=e259ddff12e97e88621dea4f694c553b0e3b81c9bb22e1f7de9cc4510d1a5ebe.

**Saved operation evidence:** artifacts/p3-339-native/tray-packaged-r3/result.json ; SHA256=bc14d1783b54e27139f51ae14ac4dc8dc2b2cf47b0b65208a52e2ad7982d0246; recorded_success=True; no per-source before binding in result

**Current applicability / concrete missing:** Packaged tray restore passed; rebind current owner and restored focus/window state.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### N06 [B] URI自動起動登録を有効化する

**Original source index:** Windows登録・安全なコマンド. Original v8 literal reference IDs: v8-N06-01. Reference status: verbatim-v7-reference-set-transcribed.

**Frozen original source references (v8 literal transcription if available):**
- v8-N06-01 / UG:83-88: S:/Projects/PixivVault/docs/UI_GUIDE.md L83-88 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=2be2b3ebb336b141f0654edab0c4d9f6b9c240b3f505f3d53681b39303323621.
  - Verbatim expectation: ### 拡張機能\n\n- 自動起動（レジストリのカスタムURLスキーム登録）の現在状態表示\n- 有効化（レジストリ登録）／無効化（レジストリ解除）ボタン\n- Cookieは拡張機能が30分ごと自動同期、Pixivを開くと即時同期される旨の説明\n- 注意事項: レジストリ書き込みの内容と、未起動時に拡張機能ボタンでアプリが自動起動する旨

**Original literal `expected_full`:**

```text
### 拡張機能\n\n- 自動起動（レジストリのカスタムURLスキーム登録）の現在状態表示\n- 有効化（レジストリ登録）／無効化（レジストリ解除）ボタン\n- Cookieは拡張機能が30分ごと自動同期、Pixivを開くと即時同期される旨の説明\n- 注意事項: レジストリ書き込みの内容と、未起動時に拡張機能ボタンでアプリが自動起動する旨
```

**Current source:** `src/pixivvault_nicegui/uri_launch.py` SHA256=d7d3b651d257a678470c2e056fbc0b83209c6461ed83894153eba0ce1deb746f ; `src/pixivvault_nicegui/desktop_integration.py` SHA256=e259ddff12e97e88621dea4f694c553b0e3b81c9bb22e1f7de9cc4510d1a5ebe.

**Saved operation evidence:** artifacts/p3-340-protocol-class-native-r1/result.json ; SHA256=47d6bdc6d3967bddda450d6bb78f5253fc336a63a3c5161ffcf1cac96a337376; recorded_success=True; no per-source before binding in result

**Current applicability / concrete missing:** Nonce test scheme registered/dispatch passed; default protocol registration UI and real scheme remain untested.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### N07 [B] URI登録を解除する

**Original source index:** 元設定へ戻す. Original v8 literal reference IDs: v8-N07-01. Reference status: verbatim-v7-reference-set-transcribed.

**Frozen original source references (v8 literal transcription if available):**
- v8-N07-01 / UG:83-88: S:/Projects/PixivVault/docs/UI_GUIDE.md L83-88 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=2be2b3ebb336b141f0654edab0c4d9f6b9c240b3f505f3d53681b39303323621.
  - Verbatim expectation: ### 拡張機能\n\n- 自動起動（レジストリのカスタムURLスキーム登録）の現在状態表示\n- 有効化（レジストリ登録）／無効化（レジストリ解除）ボタン\n- Cookieは拡張機能が30分ごと自動同期、Pixivを開くと即時同期される旨の説明\n- 注意事項: レジストリ書き込みの内容と、未起動時に拡張機能ボタンでアプリが自動起動する旨

**Original literal `expected_full`:**

```text
### 拡張機能\n\n- 自動起動（レジストリのカスタムURLスキーム登録）の現在状態表示\n- 有効化（レジストリ登録）／無効化（レジストリ解除）ボタン\n- Cookieは拡張機能が30分ごと自動同期、Pixivを開くと即時同期される旨の説明\n- 注意事項: レジストリ書き込みの内容と、未起動時に拡張機能ボタンでアプリが自動起動する旨
```

**Current source:** `src/pixivvault_nicegui/uri_launch.py` SHA256=d7d3b651d257a678470c2e056fbc0b83209c6461ed83894153eba0ce1deb746f ; `src/pixivvault_nicegui/desktop_integration.py` SHA256=e259ddff12e97e88621dea4f694c553b0e3b81c9bb22e1f7de9cc4510d1a5ebe.

**Saved operation evidence:** artifacts/p3-340-protocol-class-native-r1/result.json ; SHA256=47d6bdc6d3967bddda450d6bb78f5253fc336a63a3c5161ffcf1cac96a337376; recorded_success=True; no per-source before binding in result

**Current applicability / concrete missing:** Nonce registry rollback passed; actual product registration UI/default scheme restoration remain untested.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### N09 [B] PC拡張機能からジョブを受付ける

**Original source index:** loopback、HTTP受付とJobService結合. Original v8 literal reference IDs: v8-N09-01, v8-N09-02, v8-N09-03, v8-N09-04, v8-N09-05. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-N09-01 / N09-reference-1: S:/Projects/PixivVault/FEATURES.md L51-67 ; source SHA256=debf63a162bf4cb0d8cd9f65f408336dfb94c6afb27e7ab8a2013d4ac0e86d9a; extracted-lines SHA256=3c165c6372c33351f88d0a9eab16a9369c23d047ff121c1066d2bd1aa3784e3d.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-N09-02 / N09-reference-2: S:/Projects/PixivVault/server.py L216-234 ; source SHA256=a1dfbb1de81cc58432166eec7beae4dfc98b72f9437c92af1e9743a3f4a66be0; extracted-lines SHA256=9e26dacd18bf4d76dff1e0b55c22d7c1160d7189b476bb234e0ece2cdaf34795.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-N09-03 / N09-reference-3: S:/Projects/PixivVault/server.py L665-710 ; source SHA256=a1dfbb1de81cc58432166eec7beae4dfc98b72f9437c92af1e9743a3f4a66be0; extracted-lines SHA256=ecb6e2759a1e413237a514a238c3f38d3a0f361078f989912d77f38b7b6ae852.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-N09-04 / N09-reference-4: S:/Projects/PixivVault/server.py L1371-1385 ; source SHA256=a1dfbb1de81cc58432166eec7beae4dfc98b72f9437c92af1e9743a3f4a66be0; extracted-lines SHA256=0b8f49bc32fb3f23d83f3fe2296cefac61637a7a808a625df3ae2412562b5293.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-N09-05 / N09-reference-5: S:/Projects/PixivVault/server.py L1590-1658 ; source SHA256=a1dfbb1de81cc58432166eec7beae4dfc98b72f9437c92af1e9743a3f4a66be0; extracted-lines SHA256=916b03518119db8b5135c9e39d1dbd956497ad6ccd4576cbad0a17e8376e9c4f.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/extension_application.py` SHA256=533b2827c34dd637e7a456fc845d4f344d496290ce484c03ecca30f1b9e722da ; `src/pixivvault_nicegui/extension_bridge.py` SHA256=ddc4df47d1029fceab6e10683c288006c6d54087455dec133842487cadf30f06.

**Saved operation evidence:** No operation-level result.json bound in examined sources.

**Supplementary recorded evidence (bounded; not a substitute for original operation):** artifacts/p3-318-native-extension-checker/independent-r2/review.json ; SHA256=da47c7f2ae5f0cd06a4b31ab9988efa2de654f82b8242b824895733837981bf1 | artifacts/p3-318-extension-http/status-ui-r1/result.xml ; SHA256=5970badee9a4d330830a1d01de2dc45e4fe6d79485fe56343e4558d46cd8f7cc.

**Current applicability / concrete missing:** Extension HTTP/bridge code exists; actual isolated command receipt and queue lifecycle lack current native GUI proof.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### N10 [B] PC拡張機能の受付・接続状態を確認する

**Original source index:** status・不能理由表示. Original v8 literal reference IDs: v8-N10-01. Reference status: verbatim-v7-reference-set-transcribed.

**Frozen original source references (v8 literal transcription if available):**
- v8-N10-01 / UG:83-88: S:/Projects/PixivVault/docs/UI_GUIDE.md L83-88 ; source SHA256=78108472f56b3512c421cea07063bd43ca7553a42ff83f4be45a9e07c46b906b; extracted-lines SHA256=2be2b3ebb336b141f0654edab0c4d9f6b9c240b3f505f3d53681b39303323621.
  - Verbatim expectation: ### 拡張機能\n\n- 自動起動（レジストリのカスタムURLスキーム登録）の現在状態表示\n- 有効化（レジストリ登録）／無効化（レジストリ解除）ボタン\n- Cookieは拡張機能が30分ごと自動同期、Pixivを開くと即時同期される旨の説明\n- 注意事項: レジストリ書き込みの内容と、未起動時に拡張機能ボタンでアプリが自動起動する旨

**Original literal `expected_full`:**

```text
### 拡張機能\n\n- 自動起動（レジストリのカスタムURLスキーム登録）の現在状態表示\n- 有効化（レジストリ登録）／無効化（レジストリ解除）ボタン\n- Cookieは拡張機能が30分ごと自動同期、Pixivを開くと即時同期される旨の説明\n- 注意事項: レジストリ書き込みの内容と、未起動時に拡張機能ボタンでアプリが自動起動する旨
```

**Current source:** `src/pixivvault_nicegui/extension_bridge.py` SHA256=ddc4df47d1029fceab6e10683c288006c6d54087455dec133842487cadf30f06 ; `src/pixivvault_nicegui/ui/preferences_view.py` SHA256=6e7d17894d6805dbce0476b07b10e7729be32681983b3079809c78f519b6ee6d.

**Saved operation evidence:** No operation-level result.json bound in examined sources.

**Supplementary recorded evidence (bounded; not a substitute for original operation):** artifacts/p3-318-native-extension-checker/independent-r2/review.json ; SHA256=da47c7f2ae5f0cd06a4b31ab9988efa2de654f82b8242b824895733837981bf1 | artifacts/p3-318-extension-http/status-ui-r1/result.xml ; SHA256=5970badee9a4d330830a1d01de2dc45e4fe6d79485fe56343e4558d46cd8f7cc.

**Current applicability / concrete missing:** Extension state/status UI exists; restart, disconnected/reconnected state and command result need bound observation.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### Z01 [B] 暗号化Vaultを解錠する

**Original source index:** 旧暗号形式・資格情報・session. Original v8 literal reference IDs: v8-Z01-01, v8-Z01-02, v8-Z01-03, v8-Z01-04, v8-Z01-05, v8-Z01-06. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-Z01-01 / Z01-reference-1: S:/Projects/PixivVault/docs/design/NANA7MI_VAULT_FOUNDATION_SPEC_2026-08-16.md L31-59 ; source SHA256=3a889fa8ac74731d11dad9b92d77491650f03835b5aacd1ff380671d6e081467; extracted-lines SHA256=7938b3b45c01e466b4f0dae405d143ae80f95b6f5b9b2509d316272672ace20f.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Z01-02 / Z01-reference-2: S:/Projects/PixivVault/docs/design/NANA7MI_VAULT_FOUNDATION_SPEC_2026-08-16.md L75-97 ; source SHA256=3a889fa8ac74731d11dad9b92d77491650f03835b5aacd1ff380671d6e081467; extracted-lines SHA256=3fc9bf384fe691c4d39c98fe474713d3029cce4e7e1772ebc9450f0ce2e83c45.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Z01-03 / Z01-reference-3: S:/Projects/PixivVault/docs/design/NANA7MI_VAULT_FOUNDATION_SPEC_2026-08-16.md L227-245 ; source SHA256=3a889fa8ac74731d11dad9b92d77491650f03835b5aacd1ff380671d6e081467; extracted-lines SHA256=03512eca7fd1319030237b315d9cf7eadb7b2b8ac6e5437016a041a2b7055190.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Z01-04 / Z01-reference-4: S:/Projects/PixivVault/nana7mi_vault.py L149-184 ; source SHA256=5558507fe58ee7682e82da1fddcc3a6e50d7154d127c93ec10a78a8612db2acb; extracted-lines SHA256=1f6698dac98900d2f6f6a99e8a87f84a9cbfc7abd108f45fccbfea9512c3fd91.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Z01-05 / Z01-reference-5: S:/Projects/PixivVault/gui.py L960-984 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=8d73a294d9428c0c76b32aaa1c7c9f3a06636284d125d0cefebfd52ad2b5423f.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Z01-06 / Z01-reference-6: S:/Projects/PixivVault/gui.py L1012-1026 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=2e44a7afc79c94a1cfab1ab601367acd891a4608e86f1c6e2f5ea34def7f41dc.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/external_vault.py` SHA256=3212353f38ebb7c9a54de219e503c93f14f63c5c964c9808c4e6bc1fc289915d ; `src/pixivvault_nicegui/external_vault_transport.py` SHA256=6554d5ee8c1ed8f67b3c5020ef374c6a359ec9f82b5a6535a902ef40278bcdbc ; `src/pixivvault_nicegui/ui/external_vault_view.py` SHA256=c68ccc5bcc583be834962e4c6000cbfb7af8d6a10aebff55f785b497a6426f0d.

**Saved operation evidence:** No operation-level result.json bound in examined sources.

**Current applicability / concrete missing:** External vault unlock action exists; session/identity, error and reconnect GUI record not bound.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### Z02 [B] Vaultを明示lockする

**Original source index:** 保持情報破棄・期限処理. Original v8 literal reference IDs: v8-Z02-01, v8-Z02-02, v8-Z02-03, v8-Z02-04. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-Z02-01 / Z02-reference-1: S:/Projects/PixivVault/docs/design/NANA7MI_VAULT_FOUNDATION_SPEC_2026-08-16.md L120-142 ; source SHA256=3a889fa8ac74731d11dad9b92d77491650f03835b5aacd1ff380671d6e081467; extracted-lines SHA256=b3a8f6fd934602ebbab3f37ef465f01d9fb09656713a1f18aa170fdedd86ab8f.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Z02-02 / Z02-reference-2: S:/Projects/PixivVault/nana7mi_vault.py L299-326 ; source SHA256=5558507fe58ee7682e82da1fddcc3a6e50d7154d127c93ec10a78a8612db2acb; extracted-lines SHA256=33f88ddca4e7f9d16db4e779c92f8dc88fb0848cd0fa26a0ac4128e71413adb5.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Z02-03 / Z02-reference-3: S:/Projects/PixivVault/gui.py L1039-1054 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=a111bc3f35a6068c285bf3b6f891b15832da26f5c0fea39e03bed81bb8825a7c.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Z02-04 / Z02-reference-4: S:/Projects/PixivVault/docs/design/NANA7MI_BOOKMARK_SEARCH_SPEC_2026-08-16.md L184-198 ; source SHA256=b83b915cfc4eafc2d3d28504d99a4797e551cc68378898d53be6013013dbbe75; extracted-lines SHA256=bf89beaf76b644bc16e8b0da37e1878187ec3fdeb0ebf71ee90afc5b14ed9870.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/external_vault.py` SHA256=3212353f38ebb7c9a54de219e503c93f14f63c5c964c9808c4e6bc1fc289915d ; `src/pixivvault_nicegui/external_vault_transport.py` SHA256=6554d5ee8c1ed8f67b3c5020ef374c6a359ec9f82b5a6535a902ef40278bcdbc ; `src/pixivvault_nicegui/ui/external_vault_view.py` SHA256=c68ccc5bcc583be834962e4c6000cbfb7af8d6a10aebff55f785b497a6426f0d.

**Saved operation evidence:** No operation-level result.json bound in examined sources.

**Current applicability / concrete missing:** External vault lock action exists; revocation/failstop and cross-view GUI reentry not fully bound.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### Z03 [B] Vault情報を登録・更新する

**Original source index:** secret_key/UID、空欄保全. Original v8 literal reference IDs: v8-Z03-01, v8-Z03-02, v8-Z03-03, v8-Z03-04, v8-Z03-05, v8-Z03-06. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-Z03-01 / Z03-reference-1: S:/Projects/PixivVault/gui.py L872-885 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=ea3f473b8b6fa033d953910afa438fce365fede2501b3a758c09bd4892796f23.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Z03-02 / Z03-reference-2: S:/Projects/PixivVault/gui.py L924-945 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=5378cc2a1b208b15d651588328f7dc67eaa2cfeb550ed6da36238640b39dd20b.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Z03-03 / Z03-reference-3: S:/Projects/PixivVault/gui.py L954-1011 ; source SHA256=731e680452ed89cf35daf4fa6f1dd220d2dba7d592be61fe1cd6acbecd84e939; extracted-lines SHA256=e9db880972dc28e75af68c19a7f3f330dc7fe7d952abce1e9c8ff2fd632c70dd.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Z03-04 / Z03-reference-4: S:/Projects/PixivVault/nana7mi_vault.py L448-459 ; source SHA256=5558507fe58ee7682e82da1fddcc3a6e50d7154d127c93ec10a78a8612db2acb; extracted-lines SHA256=6c95a7938d82a7661e50dbe41b08ccbee8ac0df7eaa7863b402b11a897b3ee89.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Z03-05 / Z03-reference-5: S:/Projects/PixivVault/docs/design/NANA7MI_MULTI_UID_SPEC_2026-08-23.md L255-265 ; source SHA256=32ae20c8e5c19500b24dd1525293d90868258525ca59f49a646782c274db3547; extracted-lines SHA256=eb6f2731c704e33c13b3d96dde6153d7a70436e03422b8d27d6d70ae424f5dad.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Z03-06 / Z03-reference-6: S:/Projects/PixivVault/docs/design/NANA7MI_MULTI_UID_SPEC_2026-08-23.md L269-281 ; source SHA256=32ae20c8e5c19500b24dd1525293d90868258525ca59f49a646782c274db3547; extracted-lines SHA256=05a65aff08bf0c8d0f3fc57552825f94128d7d8022aa47f0180f85f62c26f27f.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/external_vault.py` SHA256=3212353f38ebb7c9a54de219e503c93f14f63c5c964c9808c4e6bc1fc289915d ; `src/pixivvault_nicegui/external_vault_transport.py` SHA256=6554d5ee8c1ed8f67b3c5020ef374c6a359ec9f82b5a6535a902ef40278bcdbc ; `src/pixivvault_nicegui/ui/external_vault_view.py` SHA256=c68ccc5bcc583be834962e4c6000cbfb7af8d6a10aebff55f785b497a6426f0d.

**Saved operation evidence:** No operation-level result.json bound in examined sources.

**Current applicability / concrete missing:** Vault save/update path exists; actual success/failed persistence and no secrets in logs need bound evidence.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### Z04 [B] Shaftで個人検索を実行する

**Original source index:** 認証・失効・障害・結果. Original v8 literal reference IDs: v8-Z04-01, v8-Z04-02, v8-Z04-03, v8-Z04-04, v8-Z04-05, v8-Z04-06. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-Z04-01 / Z04-reference-1: S:/Projects/PixivVault/docs/design/PERSONAL_RANKING_TAB_SPEC_2026-08-18.md L10-19 ; source SHA256=a9040d838ba45be52aeca1b77d299862b7f53230adf0a063bb02977545c7be82; extracted-lines SHA256=5279c06818ecfde7586ec460c07bb95302fb9a4bea2c1fd567f1072d636c634a.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Z04-02 / Z04-reference-2: S:/Projects/PixivVault/docs/design/PERSONAL_RANKING_TAB_SPEC_2026-08-18.md L72-95 ; source SHA256=a9040d838ba45be52aeca1b77d299862b7f53230adf0a063bb02977545c7be82; extracted-lines SHA256=903fad3742d78df4ca93feb46740dd843192f35e37a7e8e81785841791a284f1.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Z04-03 / Z04-reference-3: S:/Projects/PixivVault/docs/design/PERSONAL_RANKING_TAB_SPEC_2026-08-18.md L106-114 ; source SHA256=a9040d838ba45be52aeca1b77d299862b7f53230adf0a063bb02977545c7be82; extracted-lines SHA256=152b2f637beaa4489f287cc2e6063d943cf1dc2b2f4ab33777750c1d533498bc.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Z04-04 / Z04-reference-4: S:/Projects/PixivVault/docs/design/NANA7MI_BOOKMARK_SEARCH_SPEC_2026-08-16.md L182-201 ; source SHA256=b83b915cfc4eafc2d3d28504d99a4797e551cc68378898d53be6013013dbbe75; extracted-lines SHA256=531e2187a411027817d45fe03df40fa3c9353a94841678d0788f609dc1a32606.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Z04-05 / Z04-reference-5: S:/Projects/PixivVault/pixiv_client.py L1380-1437 ; source SHA256=37fffbffe3dc872af3b83df272870b6d7445d90bd519b98a3289890bf3107f8f; extracted-lines SHA256=a10192393d0aadf167d37c8f233aee2b5b49f8ad032f9f7629e291d13ddebd9a.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Z04-06 / Z04-reference-6: S:/Projects/PixivVault/pixiv_client.py L1441-1469 ; source SHA256=37fffbffe3dc872af3b83df272870b6d7445d90bd519b98a3289890bf3107f8f; extracted-lines SHA256=affbb0d2939c501c19a1fa677b288654f52e6ecb41455bbedc4457e02e7e696f.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/ui/personal_ranking_view.py` SHA256=39ae692d9d876fe6e634f03790a00624ed7eb20b9396f46cbb704230747fab90 ; `src/pixivvault_nicegui/external_vault_transport.py` SHA256=6554d5ee8c1ed8f67b3c5020ef374c6a359ec9f82b5a6535a902ef40278bcdbc.

**Saved operation evidence:** No operation-level result.json bound in examined sources.

**Current applicability / concrete missing:** Shaft personal search path exists; unlocked/locked/failed results need observed GUI evidence.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

### Z05 [B] premium条件付き検索を実行する

**Original source index:** R-18等の指定を実際の検索へ反映. Original v8 literal reference IDs: v8-Z05-01, v8-Z05-02, v8-Z05-03, v8-Z05-04. Reference status: unfilled-code-or-summary-requires-reconciliation.

**Frozen original source references (v8 literal transcription if available):**
- v8-Z05-01 / Z05-reference-1: S:/Projects/PixivVault/docs/design/PERSONAL_RANKING_TAB_SPEC_2026-08-18.md L129-137 ; source SHA256=a9040d838ba45be52aeca1b77d299862b7f53230adf0a063bb02977545c7be82; extracted-lines SHA256=e7c88e6de82e0f2b7ec65202c021d2d95622a33c804dbc3dfb373e3f96e5a9a6.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Z05-02 / Z05-reference-2: S:/Projects/PixivVault/docs/design/PERSONAL_RANKING_TAB_SPEC_2026-08-18.md L82-89 ; source SHA256=a9040d838ba45be52aeca1b77d299862b7f53230adf0a063bb02977545c7be82; extracted-lines SHA256=79e47e98c8805143d6456e1fa96be7353ea536c6c13ddb0c18881ca7d25f11f8.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Z05-03 / Z05-reference-3: S:/Projects/PixivVault/docs/design/NANA7MI_BOOKMARK_SEARCH_SPEC_2026-08-16.md L203-213 ; source SHA256=b83b915cfc4eafc2d3d28504d99a4797e551cc68378898d53be6013013dbbe75; extracted-lines SHA256=022255a579da7c6aa6df1d61d15e4a23ef702a1b46929385c948a00db3fb2fa1.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.
- v8-Z05-04 / Z05-reference-4: S:/Projects/PixivVault/pixiv_client.py L1395-1406 ; source SHA256=37fffbffe3dc872af3b83df272870b6d7445d90bd519b98a3289890bf3107f8f; extracted-lines SHA256=fbe29c60bbd1ae4e95e76fcd54f373e3c1007646888d3440df30b4b46822dc2d.
  - No v8 literal for code/summary reference; inspect frozen lines to reconcile original behavior.

**Original literal `expected_full`:**

```text
NULL in v8; use original index/reference provenance, no literal invention
```

**Current source:** `src/pixivvault_nicegui/ui/personal_ranking_view.py` SHA256=39ae692d9d876fe6e634f03790a00624ed7eb20b9396f46cbb704230747fab90 ; `src/pixivvault_nicegui/external_vault_transport.py` SHA256=6554d5ee8c1ed8f67b3c5020ef374c6a359ec9f82b5a6535a902ef40278bcdbc.

**Saved operation evidence:** No operation-level result.json bound in examined sources.

**Current applicability / concrete missing:** Premium-filtered personal search path exists; gating and empty/denied results need GUI evidence.

**Boundary:** other-PC/all-DPI, actual Pixiv network GET, public-account write, native/performance, independent review and CI remain independent gates as applicable. No such gate has been inferred solely from a passing test count.

## Prioritized missing work for prime (max 10)

1. Rebind 340 Q03/Q12 and 331 Q18/Q19/Q20 to current source manifests; keep their 4-round operation successes and do not repeat unchanged scenarios.
2. Close Q01/Q02/Q04-Q11/Q13-Q17 with one managed queue+failed-list UI sequence: 7 states, mixed bulk, retry attempts, real managed receipt, pagination/clear; reuse saved J/F/M functional evidence.
3. Restore original U01/U03-U06/U10/U11 author selection/sort/favourites/override controls, then verify U07/U12/U15/U16 and U17 with bounded synthetic managed jobs.
4. Decide original X07/X08 move-vs-copy semantics; current quarantine retains receipts and cannot claim original storage release. Rebind X02/X04/X06/X09 after duplicate_view and managed_quarantine changes.
5. Link S11-S19 live preference consumers, 429 retry/admission boundaries, cache FIFO/LRU and restart; preserve prior S16 Edge success as partial, not as full consumer proof.
6. Complete S01/S03/S04/S05/S06/S07/S08/S09/S10/S14/S15/S20/S21/S22 settings paths with the smallest relevant GUI/native/owned-fixture observations; do not force real GET for local settings.
7. Close E01 eleven-condition export and A01/A02 author overlay/more with existing synthetic sources and cleanup/provenance checks.
8. Connect T01 70-author proof and T02 scheduler history/CAS across restart, leaving notification/native performance independent.
9. Bind M01/M04/M06/M07/M10 novel/ugoira real media outputs (synthetic-owned) to native playback/reader and restart, preserving packaged codec successes.
10. Rebind N01-N10 packaged startup/exit/tray/URI/extension and Z01-Z05 Vault states after relevant source changes; the nonce scheme is not default product registration and no external write is implied.

## Source references

- `docs/P3_FULL_ACCEPTANCE_152_INDEX_2026-10-28.md`, `docs/P3_FORMAL_152_OPERATIONS_LEDGER_2026-10-29_v8.json`, `docs/P3_324_Q_ACCEPTANCE_MAPPING_2026-10-29.md`.
- Saved results and full SHA256 are written under each applicable operation; absent operation-level results are explicitly marked. None of these per-ID labels edits the formal v8 status.
