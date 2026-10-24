# PixivVault Flet → NiceGUI 完全移植：オーケストレータ引き継ぎ正本

> **2026-10-24 latest superseding checkpoint:** The six P2 findings from the completed independent BLOCK review have now been addressed in the current dirty Phase9 source, along with direct checker assertions for 70/70 saved dispatch items and zero deferred work. Latest full pytest: **1204 passed / 1 skipped / 1 warning / 61.09s**; Edge `artifacts/phase9-managed-scheduler-edge-p2-remediation-final-20261024/result.json` **4/4 GREEN**; Windows copy `artifacts/phase9-native-copy-p2-remediation-final-20261024` **1050/1050 SHA match**, EXE SHA `D630B570AC490EB19FF7C097F767512DBCF3D3961BF58B4092CCF89954897A0B`; native acceptance `artifacts/phase9-native-p2-remediation-final-acceptance-20261024.json` passed. **Full independent review of the remediated snapshot is not yet complete.** The latest reviewer invitation failed with `chat did not report back in time (no browser opened it)` in an earlier run; do not call this approval. User now explicitly asks for a restartable handoff/start prompt and a documentation-only non-force commit/push. Keep all Phase9 source/test files dirty and excluded from that docs commit. See `docs/PHASE9_RESTART_HANDOFF_2026-10-24.md` for complete current details and resume steps.

> **2026-10-24 latest superseding status:** Since the prior summary below, a full-source self-audit found a durable aggregate-history P2: a `partial` dispatch-history row could stay stale after an ambiguous operation later reconciled to `saved`. The allowlist now permits `partial` to transition, with regression `test_partial_history_recovers_when_ambiguous_enqueue_is_later_verified_saved`. Exact current source: full pytest **1203 passed / 1 skipped / 1 warning / 67.95s** (`artifacts/phase9-final-partial-history-recovery-20261024/full.xml`); Edge `artifacts/phase9-managed-scheduler-edge-partial-history-recovery-20261024/result.json` **4/4 GREEN**, all 59 product source hashes match; Windows onedir copy `artifacts/phase9-native-copy-partial-history-recovery-20261024` **1050/1050 SHA match**, EXE `65ADBBF0D3421024296155CEDB0363B64D9EC5A916A404BAA1906AF864C34E8D`, native acceptance `artifacts/phase9-native-partial-history-recovery-acceptance-20261024.json` **passed** with run-specific screenshots. Evidence incident was updated to record the second screenshot overwrite and recovery state. Full-scope independent review remains **NOT APPROVED / pending**: only the per-work race/lock scope had worker-4 approval; worker-3 was INCONCLUSIVE and worker-5 failed to start due to CoSCore initialization timeout. No Phase9 commit/push/PR/merge. Do not promote the limited approval to full approval.

> **2026-10-24 Phase9最新候補・未統合:** Phase8 PR#29はMERGED、main `8d55756f8cbec9c9eaa2e9539b71d6af8af8b283`、postmerge CI `40001273903`のWin/Ubuntu/Chromium全SUCCESS（既存実績、再実行なし）。Phase9 worktree `S:/Projects/PixivVault-NiceGUI-wt/scheduler-managed` / branch `phase9/scheduler-managed` はmainからの未commit差分を保持。履歴保持修正後の現ソース full pytest **1202 passed / 1 skipped / 1 warning / 71.59s**。専用Edge `artifacts/phase9-managed-scheduler-edge-history-preserve-20261024/result.json` 4/4 GREEN（1280×720/1920×1080×初回/再起動、scheduler設定→dispatch→CAS receipt/history、再起動時原寸GET0、normal exit/port解放4/4）、全59製品ソースhash一致。Windows onedir/noconsole build→別root copy `artifacts/phase9-native-copy-history-preserve-20261024` 1050/1050 SHA一致、EXE SHA `0CE5E292C37AAF6AD4620A21D120302D337E9B190C5BAE9D43BA486970339313`。Native acceptance `artifacts/phase9-native-history-preserve-retry2-acceptance-20261024.json` passed（GUI/handshake/image decode、minimize/restore、8 PID自然終了、forced cleanup0、port解放、errors0）。checkerのUI screenshotsはrun固有stemを使用し、最新runで共通名は上書きしていない。原版root直下のDB/主要コード37ファイルはSHA/size/mtime一致。**独立最終reviewは未完**：worker-4はshared per-work lock/race scopeのみAPPROVE（P1/P2なし）、worker-3はsource mutationを理由にINCONCLUSIVE、worker-5はCoSCoreチャット初期化タイムアウトで起動失敗し、全dirty diffの包括レビューは未回収。実Cookie・本番作者全件は未受入。原版meta backfill/Shaft/vaultは独立ゲートとして未移植。full diff APPROVE・P1/P2解消・CI GREEN前にPR/mergeしない。証跡画像上書き事故と復元未確認は`docs/PHASE9_EVIDENCE_INCIDENT_2026-10-24.md`。

> **2026-10-24 review-driven corrections:** An older active generation receipt cannot satisfy a newer update operation: dispatch items persist job-specific `accepted_fingerprint`; recovery resolves it through durable operation ID + accepted `ManagedAuthorIntent`; only exact active CAS fingerprint equality permits `saved`/notification. Existing schema migrates safely. Notification claims are atomic before toast, and previously verified `saved` history remains saved if a later active generation replaces the pointer before its pending toast is claimed. A later independent read-only review found a same-work TOCTOU; shared per-work `RLock` serializes manual acceptance, scheduler final pending/receipt recheck→detail GET→enqueue, and worker active-pointer read/publish sections, without holding across network/image I/O. Focused 52 passed/1 skip; final full pytest 1202 passed/1 skip/1 warning/71.59s. Final Edge after history fix `artifacts/phase9-managed-scheduler-edge-history-preserve-20261024/result.json` 4/4 green; Windows onedir copy 1050/1050 SHA and final native acceptance `artifacts/phase9-native-history-preserve-retry2-acceptance-20261024.json` passed. Native checker screenshots now use run-specific stems. Worker-4 approved only the lock/race scope; worker-3's earlier full-diff review is INCONCLUSIVE; worker-5 failed to start due to a CoSCore chat startup timeout, and its worker family is no longer accessible in the current continuation. Do not treat the entire Phase9 diff as approved until a stable full-scope verdict arrives.

> **2026-10-24 Phase9次回再開用最新:** CoSCoreにてmain `8d55756f8cbec9c9eaa2e9539b71d6af8af8b283`・Phase8 postmerge CI `40001273903` Win/Ubuntu/Chromium全SUCCESS・Phase9 PRなしを再確認。Phase9 `S:/Projects/PixivVault-NiceGUI-wt/scheduler-managed` の既存未コミット差分を保護。今回変更: 明示opt-in済みmanaged ledgerだけをアプリ次回起動時に再開、interrupted復旧確認UI（未完了JobServiceが残る場合fail-closed）、停止待機を長時間GETに整合する150秒に拡大、受付履歴の`done`誤称を`queued`へ、focused36件GREEN。**全Phase9受入ではない。** 未解決：50件超永続backlog/再開、JobService/CAS receipt基準の実保存完了/失敗履歴・通知、初期化×close race、停止timeout超過、Phase9専用Edge/Windows最終受入、独立最終レビュー、PR/head3CI/merge/mainCI。最終記録は`docs/PROGRESS.md`と`docs/MANAGED_SCHEDULER_PHASE9_2026-10-24.md`を参照。未受入のままpush/mergeしない。CoSCoreのみでこの同一worktreeから続ける。

> **2026-10-24 Phase9続行・最新:** Phase8 PR#29 `MERGED` main `8d55756f8cbec9c9eaa2e9539b71d6af8af8b283` / main CI `40001273903` Windows・Ubuntu・Chromium全SUCCESSは再利用（再実行なし）。Phase9 `S:/Projects/PixivVault-NiceGUI-wt/scheduler-managed` branch `phase9/scheduler-managed` はHEADが上記main、**未commitのPhase9差分9パス**。管理scheduler SQLite/runnerに加えNiceGUI定期チェックtab・作者設定・手動実行・完全GET→managed JobService投入を追加したが**未完成**。独立read-only監査の設定CAS上書き・旧DB既保存大量再取得・startup wakeの不具合を修正。途中段階全pytest1195 pass/1skip、focused20 pass、既存Phase8/作者/ZIP Edge各4/4正常終了は最終Phase9受入に流用しない。**残る阻害:** タブ未訪問で起動時runner開始しない、停止12秒timeoutと120秒GETの資源寿命衝突、interrupted slot解放UIなし、複数バッチdurable継続・保存完了receipt通知/履歴なし、Phase9本体Edge/Windows未受入。所有パスを破棄せずCoSCoreのみで継続し、P1/P2解消・final full pytest/Edge/native後でなければPR/mergeをしない。詳細`docs/MANAGED_SCHEDULER_PHASE9_2026-10-24.md`/`docs/PROGRESS.md`。

> **2026-10-24 最新確定:** Phase8 **PR #29 MERGED**。PR head `13d897264c373705cfaf3ac56c2861934561e7d7` CI run `40000094399` のWindows/Ubuntu/Chromium全3 SUCCESS。main merge commit `8d55756f8cbec9c9eaa2e9539b71d6af8af8b283`、post-merge CI run `40001273903` の全3 SUCCESS、`git ls-remote origin refs/heads/main`一致。最終ローカル1180 pytest pass/1 skipped、Edge 4/4、Windows onedir copy 1050 SHA一致、native GUI終了passed、独立P1/P2=0。下記「Phase8進行中・PR未実行」等の旧記述は履歴であり現在の未完ではない。Phase9 `phase9/scheduler-managed` 専用worktree `S:/Projects/PixivVault-NiceGUI-wt/scheduler-managed` を上記mainから新規作成、原版`scheduler.py` read-only照合を開始。実Cookie本番・scheduler/うごイラ/小説等は未受入。

> **生きた引き継ぎ文書。** 最終更新：2026-10-23 JST。併読：`docs/ORCHESTRATOR_START_PROMPT.md`。
> GitHub・実worktree・CI・受入成果が正本であり、ここに記載したSHAや進捗は**その日時のスナップショット**にすぎない。開始時とPR統合時に必ず現物と照合し更新する。

> **2026-10-23 22:25 JST現物照合（後任Phase8）**：PR #28は`phase7/legacy-zip-export` head `9c906b5630bc8fecc83fd2c4462adcd247fd6bbb`のWindows/Ubuntu/browser CI run **39956405545全3 SUCCESS**後、2026-10-23 12:59:14 UTCにマージ済み。main merge SHA **`2ae2b49d389c4cd0da398a8cd10f8621dc1cc669`**、post-merge CI run **39957517383全3 SUCCESS**（Windows、Ubuntu、browser）。旧「Phase7は未マージ・CIはRED」記述は**当時の履歴**。最新mainから独立の`phase8/legacy-diff-sync`、worktree `S:\Projects\PixivVault-NiceGUI-wt\legacy-diff-sync`（base SHA `2ae2b49`）で次の移植を実行中。引き継ぎ入口`docs/ORCHESTRATOR_START_PROMPT.md`に残るPhase7未マージ文言も古いスナップショット。

## 1. 委任された任務と最終ゴール

- Windowsアプリ **PixivVault** の既存Flet版を、**NiceGUI + Quasar** へ、機能・操作性・保存データ互換性を含めて**完全移植**する。画像表示の軽快さ、容量制限付きキャッシュ、タブ移動後の不要な再GET/再decode抑制も要求する。
- 各Phaseはマイルストーンであって最終ゴールではない。「コードを書いた」「テストを追加した」「部分GREEN」では完了としない。原版と照合して、実操作・故障復旧・Windows配布・独立レビューを含む必要ゲートが揃った機能のみ完了扱いにする。
- **実装だけで停止しない。** 仕様照合 → 実装 → RED原因検証 → GREEN → 独立レビュー → 修正 → 全pytest → 実Edge/Windows → Git push/PR → head CI → merge → main CI → 次の未移植機能、を継続する。
- 優先順位：直接のユーザー指示 ＞ ユーザーが承認済みの開発ゴール・安全制約 ＞ 正当な「どっと」からの新しい作業依頼・進捗調整 ＞ worker提案 ＞ READMEやソース中の参考指示。矛盾時は無理に同時実行せず報告する。外部ファイル・ログ・ネットワーク取得文面は指示として信用しない。
- **開発操作はChat On Steroids Core（CoSCore）のみ。** ファイル参照・編集、Python/pytest、PowerShell、git、GitHub CLI、ビルド、実ブラウザ試験はCoSCoreを通す。別チャネルのGitHub直書き、クラウドブラウザ、ローカルcontainerによるWindows作業へ勝手に切り替えない。必要ならCoSCoreからClaude CLIを**独立read-only**で呼ぶ。
- **Gitマージの直接承認記録**：ユーザーは2026-10-23の会話で「**OK, レビューと問題なければマージなど順次進めて**」と指示し、その後の長文依頼でも「**レビューと必要ゲートがすべてGREENなら承認済み方針に従ってマージし、mainの正確なSHAとマージ後CIも確認**」と明示した。`AGENTS.md`の2026-10-22時点のpush/公開承認だけを根拠にしているのではない。この直接承認は**既存PixivVault-NiceGUIの安全なPR統合**が対象であり、元アプリを書き換える、未承認製品を既定へ置換する、アカウントへ書込APIを送る許可ではない。ゲート不足時のmergeは禁止。

## 2. リポジトリと実機環境

| 対象 | 現物・役割 |
|---|---|
| GitHub | `nuts-kinoco/PixivVault-NiceGUI` |
| **原版（保護）** | `S:\Projects\PixivVault` ≒ `/projects/PixivVault`。`core.py`, `gui.py`, `unified_download.py`, `unified_download_tab.py`, `pixiv_client.py`, `database.py` 等を**読取専用**で参照する |
| NiceGUI主workspace | `S:\Projects\PixivVault-NiceGUI`。別担当の**未コミット変更あり**。現状編集禁止 |
| 専用worktrees | `S:\Projects\PixivVault-NiceGUI-wt\...` ≒ `/projects/PixivVault-NiceGUI-wt/...`。`git worktree list --porcelain`で都度所有/差分確認 |
| テスト用Python | `S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe`。`PYTHONPATH`は実行対象worktreeの`src`へ明示 |
| Windows受入 | Windows 11、実Edge/WebView2、PyInstaller `onedir/noconsole`、実コピーexe。1280×720と1920×1080 |
| 機密 | 実Cookie、アカウントID・作者/作品ID、実画像、取得本文、元DB・設定をgit/公開ログ/画面証拠へ載せない |

`S:\Projects\PixivVault-NiceGUI-wt`には多数のworktreeが存在する。`failed-records-review`, `jobs-pipeline`, `legacy-baseline`, `library-read-*`, `library-view`, `legacy-failed-retry`などには他者の未コミット差分がある。**git reset --hard / git clean / git stash 禁止。** 未受入新版を既定アプリに置換しない。担当権が不明な作業ツリーは触らず、新しい専用worktreeをmainから切る。

## 3. 統合済みマイルストーン／2026-10-23スナップショット

| 統合／成果 | 正確な識別子 | 限定受入内容 |
|---|---|---|
| PR #24以前 | main `640b72c`までの既存成果 | Flet原版調査、NiceGUI shell、管理ジョブ、読み取りライブラリ、認証境界、ギャラリー等。**全機能完了とは見なさない** |
| PR #25 | main `bacb629` / single work ID | ブックマーク外の単一**作品ID**からGET検証→管理保存。原版の作者ID入力とは別UI |
| **PR #26** | head `033ef981096a425170f742abd53175fd524cf9e6` → main `05115e7f34bbe7e050bc0b563d383fd3117c2854` | **作者ID**直接入力・フォロー一覧/検索/選択・48件作品詳細、NEW/UPDATED、v2 accepted-intent、世代CAS、旧v1互換、停止/再起動復旧、共有ビューア。独立レビューとWin/Ubuntu/Chromium CI全GREEN |
| **PR #27** | head `86bddb2357ca1158eb3c7c1c2fc0d6fb8ecf546b` → main `50ea5ee9ff745226cef6e7ac3fbc2a311c527d4f` | 静的な旧`failed_queue` SQLite**コピー**から通常illust/mangaのみ明示再投入。旧DB無変更、ソースSHA固定、永続ジョブ、再起動、失敗注入、実Edge。headとmerge後のWin/Ubuntu/Chromium CI全GREEN |

### Phase7：**PR #28統合済み（下記のCI REDは修正前の履歴）**

- 所有worktree：`S:\Projects\PixivVault-NiceGUI-wt\legacy-zip-export`。
- branch：`phase7/legacy-zip-export`。**引き継ぎ文書を作る前の製品コードHEAD**：`a667a1e3b473c63b4d377b4e1acc793b8299282e`。文書専用commit **`ebcfc12c004ca42ab78354b59d8a5540db9ca651`** をpushし、**PR #28**（`https://github.com/nuts-kinoco/PixivVault-NiceGUI/pull/28`）を作成した。**当時の初回CI run `39955280876` はWindowsでRED**（後述）・未マージだったが、後のhead `9c906b5`で解消・全3ジョブ成功後、上記のmainへマージ済み。
- 同確認時のGitHub main：`50ea5ee9ff745226cef6e7ac3fbc2a311c527d4f`。**当時Phase7のopen PRは見つからなかった**。開始後に変更されていないか `gh pr list --state all --head ...` を実行。
- 実装：管理保存済みの検証済みアクティブ画像を原版互換`作者名(作者ID).zip`へ**専用新rootのみ**書き出し。ZIPの作品ID入り命名/CRC/非上書き/同作者改名重複拒否/クラッシュ一時リンク復旧/閉じる途中の中断/古いUI通知隔離。別root opt-in：`--legacy-zip-export-root`。
- 追加実装ファイル：`src/pixivvault_nicegui/legacy_zip_export.py`、`tests/test_legacy_zip_export.py`、`tools/check_legacy_zip_export_browser.py`。対応変更は`bootstrap.py`, `services.py`, `ui/library_view.py`, `.github/workflows/ci.yml`, `tools/check_managed_downloads_browser.py`, `docs/LEGACY_ZIP_EXPORT_2026-10-23.md`。
- **直近の受入**：全pytest **1099 passed / 1 skipped**（既存Windows symlink特権skip）、独立read-only Claudeレビュー **APPROVE / P1/P2なし**、実Edge 1280×720 + 1920×1080 初回/再起動計4経路PASS、Win PyInstaller onedir/noconsole **1050ファイルSHA一致**、コピーexeのUI/最小化/復帰/正常終了/所有ポート解放PASS。証拠はworktree内`artifacts/legacy-zip-20261023/`（基本git追跡外）。**これらは当該SHA/その当時のソースに対する記録であり、改変後は再試験する**。
- **CIの最新RED → 修正**：PR #28 head `ebcfc12...` のWindows CIで`tests/test_native_managed_downloads.py::test_actual_synthetic_writer_jobservice_verifier_detects_mismatch`が `running` のまま**5秒のspin-poll期限**に達し、`done` を即断定したため失敗（Ubuntu成功、Chromiumは実行されず）。Windows hosted runnerのI/O遅延は可能性であり**原因として断定していない**。製品の書込/保存検証を弱めず、既存`JobService._condition`の`notify_all`に連動した**有限20秒のterminal待機**、`done/total==2`・receipt path・faultedを追加し、失敗でもwriterを解放するようにした。**この修正を新headのCIで未受入なら、Phase7全体は未完のまま**。独立read-only再レビューはAPPROVE。レビューとテストを統合しRED→GREENを記録すること。
- **当時の未完ゲート（解消済みの履歴）**：修正commit/push → 正確な新HEADのWindows/Ubuntu/Chromium CI全SUCCESS → 独立レビュー・Windows受入のソース一致確認 → merge → 新main SHAのpost-merge CI確認。この手順は下記の再照合結果で完了を確認した。
- **2026-10-23再照合結果**：Windows CI REDの修正はhead `9c906b5630bc8fecc83fd2c4462adcd247fd6bbb`に含まれる。PR head CI run `39956405545` の`tests (windows-latest)`・`tests (ubuntu-latest)`・`browser`すべてSUCCESS。PR #28はMERGED、main `2ae2b49d389c4cd0da398a8cd10f8621dc1cc669`、post-merge run `39957517383`の3ジョブSUCCESS。前段のREDを黙って取り消さず履歴として保持する。

### Phase8：旧作者差分・静止SQLite/物理ZIP/失敗履歴統合（進行中）

- 専用worktree：`S:\Projects\PixivVault-NiceGUI-wt\legacy-diff-sync`、branch `phase8/legacy-diff-sync`。origin/main `2ae2b49d389c4cd0da398a8cd10f8621dc1cc669`から新規作成。他worktreeの未コミット差分は保護。
- 原版読取専用コード調査：`core.py:1415-1507`のNEW→UPDATED→RESTORED→ローカル実物PRESENT/UNSURE/MISSING、`core.py:1200-1290`の認証・全件判定/3回・高比率5回、`database.py:1518-1535`の削除フラグ/欠落回数。原版部分不可視の削除誤検知経路は新製品で再現しない。
- 新オプション`--legacy-diff-image-root`を既存静止旧DBコピーと新managed rootへ明示接続。旧作品DB/物理ZIP索引/`failed_queue`を同一immutableコピーから読み、表示プレビューで6状態を分類。確認済みRESTORED/MISSING/旧更新は専用新rootへのmanaged NEWとし、元画像/DB/ZIPを不変に保つ。既存v2 CAS、管理ジョブ、既存ZIP書出しを再利用。元DBに無いPixiv作品はNEW、v1日付欠損・曖昧な旧名はUNKNOWN。
- 削除検知は表示と別の明示完全GET操作でのみ行い、新管理rootのsidecarに欠落streak・確認scan_idをSQLite原子的記録する。復帰はリモートで発見しただけでは旧削除フラグを消さず、新managedの完全receiptで証明する。多client・再起動・破損・GET失敗を受入対象とする。
- **Phase8ローカル受入（2026-10-24直前のソース）**：最終Edge `tools/check_legacy_author_diff_browser.py --channel msedge --output artifacts/phase8-legacy-author-diff-edge-final19-20261023` SUCCESS。1280×720/1920×1080 ×初回/再起動4/4、初回のUPDATED/RESTORED/MISSING/UNKNOWNと3件managed保存、再起動後のSKIPPED/UNKNOWN・完了3件復元、2client状態分離、全Pixiv transport経路GET-only/allowlisted HTTPS/Cookie境界、旧DB/旧メディアルート全ファイルSHA before/after一致、再起動時原寸GET0、console/page error0、外部通信0、正常終了/port解放4/4。新sidecarはmanaged root内だけ、source/sidecar WAL/SHM/journalなし、remote absence streak3/deleted=true/scan receipt1件が再起動後不変。差分表示143.34/126.95/161.12/119.55msは合成fixtureのみ。
- **1280×720 UI RED→GREEN**：複数差分行でキュー追加ボタンが`pv-download-body`のoverflow領域に埋まり、Edge click timeoutとなった。差分/遠隔欠落一覧に`pv-author-diff-list` max-height64px、`pv-author-remote-absent-list` max-height48pxを設けて再受入GREEN。最終ソースの全pytestとnative buildはこのCSS変更後に再実行する必要がある。
- **直前のWindows受入（CSS変更前）**：`tools/build_windows.ps1 -Python 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe'` SUCCESS。PyInstaller onedir/noconsoleを別root`artifacts/phase8-native-final-copy/`へコピー、1050ファイルSHA完全一致。コピーexe SHA-256 `E17AA6D19F7F6D1A758C8F035DB072CFB0ACD7BA262D88B8D823C00E51A97ED4`。`artifacts/phase8-native-final-result.json`は`status=passed`：UI handshake/cards50/image decode、gallery/learning/library操作、最小化・復帰GDI stable mean diff 0.003369、通常終了、8 owned PIDs終了、port解放、forced cleanup0、console/page errors0。CSS変更後の最終ビルドを再実行すること。
- `.github/workflows/ci.yml` browser jobへPhase8 checkerのChromium実行を追加済み。**GitHub PR/head CIはまだ未実行**（このbranchは未commit/未push）。CIの新head Windows/Ubuntu/Chromium全SUCCESS、最新checker/CIに対する独立read-onlyレビュー、マージ後main CIが残る。元Pixivアカウントの限定Cookie実GET受入は別ゲートであり、未実行。
- 詳細設計は`docs/LEGACY_AUTHOR_DIFF_SYNC_2026-10-23.md`。受入履歴、過去のREDと原因、性能測定、artifact pathは`docs/PROGRESS.md`を参照。**ローカル受入済みとGitHub CI未受入を混同しない**。
- **本書をGitへ定着**：`git add -- AGENTS.md docs/ORCHESTRATOR_HANDOFF.md docs/ORCHESTRATOR_START_PROMPT.md docs/PROGRESS.md`のように規約/進捗docsだけを追加し、**文書のみのcommit・push**をしてPhase7 PRへ含める。文書だけなら製品コード自体を変更しないので**ローカル製品動作の再試験は原則不要**だが、PRの**新head SHAに対するCIは再確認**する。引き継ぎ書がまだ`??`ならGitHubでは取得できない。

## 4. 互換性・保存境界（壊してはいけない契約）

1. 元のFlet版・DB・画像・ZIP・設定・Cookie原本へ**書き込まない**。**承認の変遷**：`AGENTS.md`の2026-10-22追加承認は「専用計測プロセスによるPixiv実APIの読取・実測（GET先限定、API/画像取得時間とUI切替時間を分離）」である。その後の**2026-10-23ユーザー直接依頼**は「**実Cookieによる限定的な作者差分DLも、既に承認された範囲で受入してください**」「**原版のDB・画像・設定・Cookie原本を変更せず、新アプリ専用管理rootだけに保存**」であり、**作者差分DLの限定保存だけ追加で許可**されている。更新済みCookieが有効な場合のみ読取専用コピーを利用し、許可されたPixiv GET以外や実アカウントへのPOST/更新・書込APIへ拡大しない。実Cookie・実画像・実ID・HTML取得本文はgit、公開ログ、スクリーンショットに載せない。現在の実Cookie受入は未完・一部環境安全制約で拒否済みであり、本番GREENとは呼ばない。
2. 新NiceGUI専用のmanaged保存rootのみ書込可。v1 receipt/accepted-intent、JobService永続SQLiteは旧データのまま読めること。v1には更新日が無いので`UNKNOWN`と分類し、`SKIPPED`の偽判定や一括上書きは禁止。
3. v2作者差分は`ManagedAuthorIntent`（origin・更新日・前世代receipt path+SHA）を厳格に凍結して永続化し、active pointerのCAS確定前に全頁SHA・画像寸法/形式・最終GETで更新日を確認する。前世代は残す。クラッシュ後はaccepted-job固有fingerprintと完全検証済みactive receiptでジョブ復元する。
4. 旧失敗リストは**静止したSQLiteコピー**だけをimmutable=1で読み込む。元のfailed_queue行を成功後に削除する原版の動作は、元DB不変の上位制約により意図的に未移植。小説・うごイラは対応していないものを通常イラストに偽装投入しない。
5. Phase7 ZIP exporterは**専用rootへの新規ZIPのみ**。原版のZIP追記/改名、元画像削除、旧DB更新、巨大作者ZIP64、別プロセス同時使用は未対応。旧ファイル/ZIPの**読取互換**は既存`ReadonlyLocalMediaCatalog`を利用し再実装しない。
6. UIは`asyncio.to_thread`等で重い処理を外し、clientごとのowner/session/epochを分離。reload・タブ往復・遅延GET・複数clientで古いクリック通知が混入しないこと。HTTP画像を何度もGET/decodeしない。表示面積/画質を犠牲にせず容量を制限。
7. PR #7由来の性能値は**限定条件の実測**。未計測条件へ改善率を外挿しない。高解像度/大量作者/最大ZIPでのp95、CPU/メモリ/VRAMは未測定なら未測定と記載。

## 5. 完全移植まで残る主な作業

**別々の受入基準が必要。実装済みと混同しないこと。** 推奨順は依存関係・危険度に応じて再検討する。

- Phase7 PR #28のmerge/main CIは確認済み。旧ZIP形式への安全な**出力**は限定であり、原版フォルダを直接更新する互換とは別。
- 原版の完全な差分判定：`NEW / UPDATED / RESTORED / MISSING / SKIPPED / UNKNOWN`。v1日付欠損・物理実体不明の安全判定、全作者ページング、管理ジョブとの一貫した状態同期。
- 旧DBの保存命名、既存ZIP追記・改名を必要とする設定、物理画像とDBの一致・安全な新rootへの移行/検証（原本無変更）。
- 旧failed_queueと新永続jobsの詳細同期、元の一括削除・一括再試行・履歴表示の操作差分（原本不変と矛盾する操作は隔離した新DBで表現）。
- 定期scheduler（停止/再開・スリープ復帰・時刻精度・再起動・競合）。
- うごイラ、漫画の特殊ケース、小説（EPUB等）と固有の失敗/リトライ処理。
- 重複・ハードリンク・ZIP重複整理、隔離ゴミ箱、ロールバック・整合性監査。
- おすすめ、フィルター、設定、外部サービス連携、原版の周辺UI/キーボード操作を全画面比較。
- Windows製品化：onedir/noconsoleコピー、ウィンドウ/最小化/復帰、終了、子プロセスとloopbackポート解放、再インストール/アップグレード、既定製品の安全切替。
- 実Cookieでの限定GET→専用新root保存、原本前後不変性、実データ表示・再起動の受入。認証不能なら範囲を明記して合成環境の独立可能部分だけGREENにする。

## 6. CoSCore専用の再開・開発SOP

1. **最初に短い日本語の進捗報告と`update_plan`**を行い、以下をCoSCoreで照合する。`git ls-remote origin refs/heads/main`; `gh pr list --state open`; `gh run list --branch main --limit 5`; `git worktree list --porcelain`; 全worktree `git status --porcelain`（差分の所有者とパス）。`agents action=status`で既存worker確認。古い`main.py`が起動中なら**コマンドライン、実行元cwd、PID/作成時刻から「このセッションで起動したNiceGUI製品・合成試験」と確認できるものだけ**正常終了する。`S:\Projects\PixivVault`の原版アプリPIDや所有不明のプロセスは終了せず報告する。
2. 作業が別担当のdirty worktreeに当たるなら、**新しい専用worktree/branch**を最新mainから作る。既存担当workerと編集パスを分け、独立レビューは編集・Git禁止で実施。同時worker最大8・実際のCoS空枠を超えず、通常はmedium起点、highは根拠ある難所のみ。**workerはgit操作・依存変更・push禁止**でprimeだけが最終統合する。`git reset --hard`・`git clean`・`git stash`禁止。既存差分を勝手にcommitしない。
3. 原版`core.py/gui.py/unified_download_*.py`等を**read-only静的**で照合し、原版のGET先/DB/例外/操作/命名と新実装の差を受入条件として列挙。検査器が古い仕様を仮定してREDなら、本体不具合と区別し、根拠なく受入条件を緩めない。
4. `apply_patch`で実装し、限定RED→原因→GREEN・例外/SQLite障害注入・再起動・停止競合・複数client・UI epoch試験を追加。例：`$env:PYTHONPATH='S:\Projects\PixivVault-NiceGUI-wt\<worktree>\src'; & 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 -m pytest -q`。**全pytest**を最終ソースで実行。
5. 実Edge DOM/click/decode/keyboard/focus/tab往復を**1280×720と1920×1080**で確認。Windows `tools/build_windows.ps1`で`onedir/noconsole`をビルド、成果物を別フォルダへコピーして各ファイルSHA照合後、`tools/check_native_window.py`でUI・最小化/復帰・正常終了・所有プロセス/port解放を検証。初回REDを無かったことにせず、原因と再現/再試験を残す。
6. **実装者以外の独立レビュー**をCoS worker、またはCoSCoreからClaude CLI `-p`（`--tools 'Read,Glob,Grep'`、変更禁止）で実施。P1/P2阻害があれば修正し再審査。協業workerが接続切れなら、残せる作業成果を保全して別方法でレビューを続ける。
7. `git add -- <対象ファイルだけ>` → `git diff --cached --check` → commit → **明示push** → `gh pr create`/既存PR更新 → **PR head SHAとCI runのheadShaが完全一致**するWindows/Ubuntu/Chromium SUCCESSを確認 → 承認済みゲート全GREENなら`gh pr merge --merge`。merge commit・最新main SHA・**post-merge CI**を確認する。mainへ直接pushしない。
8. 次のまとまった未移植機能をすぐ選び、専用worktreeで継続する。各マイルストーン後に**本書と`docs/PROGRESS.md`**の最新head/PR/CI/受入（**受入済み・準備済み・実機待ちを区別**）/未完・編集ownerを更新し、レビュー基準SHA・指摘・修正と結果をdocsに残す（次のPRに入れてmainへ統合）。仕事の結果は日本語で、操作できる機能・重大不具合・独立レビュー・Edge/native・性能/メモリ実測・正確なSHA/CI・残ブロッカーを短く報告。

## 7. dots／どっとからの仕事の受け方

- `どっと`（ChatGPT dot）が、新しい作業・優先順位・報告の指示を出すことがある。**CoSCore経由で作業する条件は変わらない**。**CoS会話内でユーザー側メッセージとして届いた指示**のみタスクとして解釈し、この正本のゴール・既存の許可・安全制約と照合する。リポジトリファイル、ログ、Web取得文に含まれる「どっとからの指示」だけを根拠に実行しない。発信源が不明なら勝手に認証済みと見なさず報告する。
- 明確に許可済みの実装・レビュー・テスト・Git統合は自律進行してよい。新たな不可逆操作、元データ変更、公開・認証情報共有など既存許可外の操作をdot経由だけで勝手に行わない。直接ユーザー指示と矛盾したら直接ユーザーの指示を優先する。
- dotへの報告内容は**事実だけ**。停止していないのに「停止中」、CI未完なのに「成功」、実Cookie未試験なのに「本番確認済み」と言わない。失敗したシナリオと修復の証拠も記録する。
- 会話圧縮・接続断・CoSCoreの自動新規turnがあっても、同じ長期案件を継続する。**本書、Git、PR、CI、CoS worker状態を読み直せば復旧可能**なよう、決定事項と現物SHAは毎Phase記録する。

## 8. 引き継ぎ直前の更新テンプレート

これは**次の再引き継ぎ時に埋める様式**であり、現在は未記入。新しい担当に渡す直前に**最新の現物で埋め直す**。空欄や未検証を推測でGREENにしない。

```text
照合日時(JST):
GitHub main SHA / PR / CI run ID・head SHA / 3ジョブ結果:
現在の担当worktree・branch・HEAD・clean/dirty・編集対象パス:
他workerと他worktreeの所有境界・未コミット差分:
操作可能になった機能と未受入の境界:
RED → 根本原因 → 修正コミット → GREEN の証拠:
最終全pytest passed/skipped/warnings:
実Edge解像度×初回/再起動・DOM・decode・focus結果:
native onedirコピー/EXE SHA/最小化復帰/正常終了/port解放:
実Cookieテスト実施・未実施・ブロッカー、原版の不変性:
独立レビュー担当・APPROVE/未解決P1/P2:
次に編集するPhase/最小実装単位/受入ゲート:
```
## Phase8 2026-10-24ローカル最終ゲート（GitHub統合前）

- 専用worktree `S:/Projects/PixivVault-NiceGUI-wt/legacy-diff-sync` / branch `phase8/legacy-diff-sync`。Phase7 PR #28は既merge、再統合不要。
- 停止待ち `final20` RED: Uvicorn/NiceGUI WebSocket drainの既定無制限が原因候補。合成checkerへ `timeout_graceful_shutdown=5` + process wait25秒、失敗時nonce/argv/パス非開示を実装。Edge独立2回各4/4、Chromium4/4、最終差分後Edge4/4の自然終了・port解放GREEN。ログのWinError10054は間欠事象でありUvicorn経路が唯一の原因と断定しない。
- 128件超: 受理済みauthor owner pinのみ解放、未完了JobService対象IDを新規プレビューの自動選択から除外。140件全件NEW・pending128+残12の2バッチ受理試験GREEN。保存済みとpendingは厳密に区別。
- 最新全pytest 1180 passed/1 skipped/1 warning (67.53s)。最終Edge `artifacts/phase8-legacy-author-diff-edge-final-after-pending-20261024/result.json` 4/4 GREEN。独立CoS read-onlyレビュー APPROVE（P1/P2ゼロ）。
- Windows PyInstaller onedir/noconsole最終buildと別rootコピー 1050/1050 SHA一致、コピーexe `498296CA10C107B8CC039BB3558FFE605B9130ED2E0B6C9E187B32ACCAD06386`。実native GUI/画像decode/最小化復帰/8所有PID正常終了/port解放 `artifacts/phase8-native-copy-final-acceptance-20261024.json` passed。
- この時点でPhase8は**未commit/未PR/未merge**。`docs/PROGRESS.md`の同日記録参照。次はPhase8所有パスに限ってadd/commit/push、PR作成、正確なhead SHAの3 CI全成功後のみ既承認merge、main SHA/CI確認。原版DB/画像/ZIP/Cookie原本変更禁止、shared主repo dirty変更破棄禁止、実Pixivの許可外通信禁止。
- 未受入境界: 実Cookie本番作者全件・巨大データ性能、scheduler/うごイラ/小説/重複隔離/設定・おすすめ等は別ゲート。Phase8統合を全移植完了とはしない。
